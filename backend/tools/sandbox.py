"""Sandboxed Python execution. Owner: person 3.

One ephemeral container per execution, built from sandbox/Dockerfile
(sih26117-sandbox). The constraints are the security boundary and they are
non-negotiable: no network, read-only root, 512 MB, 64 pids, 1 CPU, all
capabilities dropped, no privilege escalation, /tmp is a small noexec tmpfs.
The run directory under the workspace is the only writable mount, so anything
the code produces lands where read_file can see it.

Wall clock cap is 30 s; overrun kills the container and reports TOOL_TIMEOUT.
The self-correction cycle (run, fail, feed stderr back, retry) lives in the
agent loop -- this tool only ever runs once and tells the truth about it.

When the backend itself runs in a container, Docker interprets bind-mount
sources against the HOST filesystem, so SANDBOX_HOST_WORKSPACE must carry the
host path of the workspace dir (compose sets it; bare-metal dev needs nothing).
"""

from __future__ import annotations

import os
import re
import time
import uuid
from pathlib import Path

from pydantic import BaseModel

from contracts import RunContext, Tool, ToolResult

SANDBOX_IMAGE = os.getenv("SANDBOX_IMAGE", "sih26117-sandbox")
TIMEOUT_S = 30
MAX_CONTENT_CHARS = 4000


class ExecArgs(BaseModel):
    code: str


def _docker_client(docker, DockerException):
    """A Docker client that follows Docker Desktop's context, not just the env.

    `docker.from_env()` does NOT read `docker context`. With DOCKER_HOST unset
    it goes to the *default* context's endpoint, npipe:////./pipe/docker_engine
    -- but Docker Desktop's active context is `desktop-linux`, serving
    npipe:////./pipe/dockerDesktopLinuxEngine. So `docker ps` works on the
    command line while the SDK reports "CreateFile: the system cannot find the
    file specified", which reads exactly like a stopped daemon and sent us
    chasing four phantom engine crashes.

    Explicit DOCKER_HOST still wins; the fallbacks only run if the default
    endpoint is unreachable.
    """
    try:
        return docker.from_env()
    except DockerException:
        if os.getenv("DOCKER_HOST"):
            raise            # user asked for a specific endpoint; do not guess
    last: Exception | None = None
    for url in ("npipe:////./pipe/dockerDesktopLinuxEngine",
                "npipe:////./pipe/docker_engine",
                "unix:///var/run/docker.sock"):
        try:
            client = docker.DockerClient(base_url=url)
            client.ping()
            return client
        except Exception as exc:      # noqa: BLE001 - any failure means try next
            last = exc
    raise DockerException(last)


_FENCE = re.compile(r"^\s*```[a-zA-Z0-9_+-]*[ \t]*\r?\n(.*?)\r?\n?\s*```\s*$", re.S)


def _strip_fence(code: str) -> str:
    """Remove a markdown code fence wrapped around the whole script.

    The answer-style rule tells the model to present code in a ```python
    block, and it generalised that to the tool argument: script.py began with
    a literal ```python, Python raised SyntaxError on line 1, and the model
    resent the identical code three times because the error message did not
    tell it what was wrong -- TOOL_RETRIES_EXCEEDED for a program that was
    correct. Only a fence around the ENTIRE script is stripped; a fence inside
    a string literal is left alone.
    """
    m = _FENCE.match(code)
    return m.group(1) if m else code


def _denature_escapes(code: str) -> str:
    r"""Turn a literal two-character \n back into a newline.

    Under grammar-constrained decoding the model double-escapes: the JSON
    string arrives holding backslash-n rather than a newline, so the script on
    disk is one line reading `def f(a, b):\n    return a + b` and Python stops
    at `SyntaxError: unexpected character after line continuation character`.
    The model cannot see why -- it re-sends the same code and burns its retries.

    Only rewritten when the code has NO real newlines but does contain the
    literal sequence, which is exactly the broken case. Multi-line code that
    legitimately contains "\\n" inside a string is left alone.
    """
    if "\n" in code or "\\n" not in code:
        return code
    return code.replace("\\n", "\n").replace("\\t", "\t")


class ExecutePythonTool(Tool):
    name = "execute_python"
    # A bare expression returns nothing observable, and a 7B model that gets
    # back "exit code: 0" with no stdout will invent an answer. Say print().
    description = "Run Python in an offline sandbox; you must print() any value you want returned."
    args_model = ExecArgs

    def run(self, args: ExecArgs, ctx: RunContext) -> ToolResult:
        started = time.monotonic()

        def done(ok: bool, content: str, error: str | None = None,
                 artifacts: list[str] | None = None) -> ToolResult:
            return ToolResult(
                ok=ok, content=content[:MAX_CONTENT_CHARS],
                artifacts=artifacts or [],
                duration_ms=int((time.monotonic() - started) * 1000),
                error=error,
            )

        try:
            import docker
            from docker.errors import DockerException, ImageNotFound
        except ImportError:
            return done(False, "", "docker SDK not installed on the backend host")

        run_id = uuid.uuid4().hex[:12]
        run_dir = Path(ctx.workspace_dir) / ".runs" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "script.py").write_text(_strip_fence(_denature_escapes(args.code)),
                                           encoding="utf-8")

        host_workspace = os.getenv("SANDBOX_HOST_WORKSPACE", "")
        if host_workspace:
            host_run_dir = str(Path(host_workspace) / ".runs" / run_id)
        else:
            host_run_dir = str(run_dir.resolve())

        try:
            client = _docker_client(docker, DockerException)
        except DockerException as exc:
            # Actionable, not just the raw errno. This message goes into the
            # model's context, and "CreateFile: the system cannot find the file
            # specified" tells it nothing it can act on -- it just re-runs the
            # same code until MAX_TOOL_RETRIES and the run dies reporting a
            # wedged agent instead of a stopped daemon.
            return done(
                False, "",
                f"sandbox unavailable: the Docker engine is not reachable, so no "
                f"code can run right now. This is a host problem, not a problem "
                f"with your code -- do not retry execute_python; answer without "
                f"it or say you cannot. ({exc})",
            )

        container = None
        try:
            container = client.containers.run(
                SANDBOX_IMAGE,
                command=["python3", "/work/script.py"],
                detach=True,
                network_disabled=True,
                read_only=True,
                mem_limit="512m",
                memswap_limit="512m",
                pids_limit=64,
                nano_cpus=1_000_000_000,
                cap_drop=["ALL"],
                security_opt=["no-new-privileges"],
                tmpfs={"/tmp": "rw,size=67108864,noexec"},
                volumes={host_run_dir: {"bind": "/work", "mode": "rw"}},
                working_dir="/work",
                user="1000",
                environment={"MPLBACKEND": "Agg"},
            )
            try:
                exit_code = container.wait(timeout=TIMEOUT_S)["StatusCode"]
            except Exception:
                container.kill()
                return done(
                    False,
                    f"execution killed after {TIMEOUT_S}s",
                    f"TOOL_TIMEOUT: execute_python exceeded {TIMEOUT_S}s",
                )
            stdout = container.logs(stdout=True, stderr=False).decode("utf-8", "replace")
            stderr = container.logs(stdout=False, stderr=True).decode("utf-8", "replace")
        except ImageNotFound:
            return done(False, "",
                        f"sandbox image {SANDBOX_IMAGE!r} missing -- build it: "
                        "docker build -t sih26117-sandbox ./sandbox")
        except DockerException as exc:
            return done(False, "", f"sandbox launch failed: {exc}")
        finally:
            if container is not None:
                try:
                    container.remove(force=True)
                except Exception:
                    pass

        produced = sorted(
            str(p.relative_to(Path(ctx.workspace_dir)))
            for p in run_dir.iterdir() if p.name != "script.py"
        )
        content = f"exit code: {exit_code}\n"
        if stdout:
            content += f"--- stdout ---\n{stdout}\n"
        elif exit_code == 0:
            # Silence on success is the trap: without this line the model reads
            # "exit code: 0" as "my answer is confirmed" and fabricates one.
            content += ("--- stdout ---\n(empty: the script printed nothing. "
                        "Re-run with print() around the value you need.)\n")
        if stderr:
            content += f"--- stderr ---\n{stderr}\n"
        if produced:
            content += f"--- files written ---\n" + "\n".join(produced)
        return done(
            ok=exit_code == 0,
            content=content,
            error=None if exit_code == 0 else f"exit code {exit_code}",
            artifacts=produced,
        )
