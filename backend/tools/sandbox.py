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


class ExecutePythonTool(Tool):
    name = "execute_python"
    description = "Run Python code in an offline sandbox; returns stdout, stderr, exit code."
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
        (run_dir / "script.py").write_text(args.code, encoding="utf-8")

        host_workspace = os.getenv("SANDBOX_HOST_WORKSPACE", "")
        if host_workspace:
            host_run_dir = str(Path(host_workspace) / ".runs" / run_id)
        else:
            host_run_dir = str(run_dir.resolve())

        try:
            client = docker.from_env()
        except DockerException as exc:
            return done(False, "", f"docker unavailable: {exc}")

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
