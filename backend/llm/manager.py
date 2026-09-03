"""Model lifecycle manager. Owner: person 3.

Exactly one GGUF resident on the GPU at a time (docs/decisions/0003). This
module is our ~100-line llama-swap equivalent: it owns the llama-server
process, kills it to evict, relaunches it to load, and refuses to let two
models race for the same 6 GB.

Two modes, chosen by environment:

  managed   LLAMA_SERVER_BIN points at a llama-server executable. Swapping
            works: evict = SIGTERM, load = spawn with the new model's args,
            ready = /health answers 200. This is the demo configuration.

  external  no LLAMA_SERVER_BIN. Something else (docker compose) runs one
            fixed llama-server at MODEL_ENDPOINT. ensure() of any other model
            raises; the router is told which single model exists and routes
            everything there. This is the degraded three-laptop-dev mode.

Events: model.loading is emitted BEFORE anything blocks, with the eviction
target and an eta learned from previous loads of the same model. model.ready
carries the measured load_ms. The UI turns 8 seconds of dead air into a
countdown; that is the whole reason this file emits anything.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import signal
import subprocess
import time
from pathlib import Path
from typing import Awaitable, Callable

import httpx
import yaml
from pydantic import BaseModel, Field

from contracts import ModelLoading, ModelReady

EmitFn = Callable[[BaseModel], Awaitable[None]]


class ModelSpec(BaseModel):
    id: str
    path: str
    mmproj: str | None = None
    capabilities: list[str] = Field(default_factory=list)
    context: int = 16384
    vram_mb: int = 0
    default: bool = False
    n_gpu_layers: int = 99
    flash_attn: bool = True
    cache_type_k: str = "q8_0"
    cache_type_v: str = "q8_0"


class RouterConfig(BaseModel):
    min_confidence: float = 0.60
    switch_penalty: float = 0.15


class ModelRegistry:
    """Parsed view of config/models.yaml. Adding a model is a YAML edit; if
    you are editing this class to add one, the registry has failed at its job."""

    def __init__(self, config_path: str) -> None:
        doc = yaml.safe_load(Path(config_path).read_text())
        defaults = doc.get("defaults", {})
        self.models: list[ModelSpec] = [
            ModelSpec(**{**defaults, **entry}) for entry in doc.get("models", [])
        ]
        if not self.models:
            raise ValueError(f"{config_path}: no models defined")
        self.router = RouterConfig(**doc.get("router", {}))
        self._by_id = {m.id: m for m in self.models}

    def get(self, model_id: str) -> ModelSpec:
        if model_id not in self._by_id:
            raise KeyError(f"unknown model {model_id!r}; known: {sorted(self._by_id)}")
        return self._by_id[model_id]

    @property
    def default(self) -> ModelSpec:
        for m in self.models:
            if m.default:
                return m
        return self.models[0]

    def with_capability(self, capability: str) -> list[ModelSpec]:
        return [m for m in self.models if capability in m.capabilities]


# nvidia-smi costs ~1-2 s per invocation on this machine. /api/health calls it,
# /api/health is polled by every open frontend tab, and the handler is `async` --
# so each poll blocked the event loop for a second or more and the SSE token
# stream visibly stalled. Two tabs open reads on stage as "the model is stuck
# thinking". A short cache makes the reading cheap; VRAM does not move between
# polls anyway, and every path that actually cares about a fresh number (a load
# or an evict) calls _vram_mb(fresh=True).
_VRAM_TTL_S = 5.0
_vram_cache: tuple[float, tuple[int, int] | None] | None = None


def _vram_mb(fresh: bool = False) -> tuple[int, int] | None:
    """(used, total) VRAM in MB via nvidia-smi. None when there is no NVIDIA GPU.

    Total is read, never assumed: the build prompt says 8 GB, but the machine
    this actually runs on is a 6 GB RTX 4050 Laptop, and a hardcoded budget
    that lies by 2 GB is how you discover an OOM on stage.

    Cached for _VRAM_TTL_S; pass fresh=True around load/evict decisions.
    """
    global _vram_cache
    if not fresh and _vram_cache is not None:
        stamp, cached = _vram_cache
        if time.monotonic() - stamp < _VRAM_TTL_S:
            return cached

    smi = shutil.which("nvidia-smi")
    if not smi:
        _vram_cache = (time.monotonic(), None)
        return None
    try:
        out = subprocess.run(
            [smi, "--query-gpu=memory.used,memory.total",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        )
        used, total = out.stdout.strip().splitlines()[0].split(",")
        reading = (int(used), int(total))
    except Exception:
        reading = None
    _vram_cache = (time.monotonic(), reading)
    return reading


def _vram_used_mb() -> int | None:
    # fresh: this is the number reported in model.ready right after a load, so
    # a five-second-old reading would show the PREVIOUS model's footprint.
    reading = _vram_mb(fresh=True)
    return reading[0] if reading else None


class ModelManager:
    def __init__(
        self,
        registry: ModelRegistry,
        endpoint: str,
        models_dir: str,
        estimate_load_s: Callable[[str], int],
        record_load: Callable[[str, int], None],
    ) -> None:
        self.registry = registry
        self.endpoint = endpoint.rstrip("/")
        self.models_dir = Path(models_dir)
        self._estimate_load_s = estimate_load_s
        self._record_load = record_load
        self._bin = os.getenv("LLAMA_SERVER_BIN", "")
        # Path to the ggml GPU backend shared library (e.g. ggml-cuda.dll).
        # ggml only scans the executable's own directory, so a build that keeps
        # its CUDA backend in a subfolder loads the CPU backend instead and
        # silently runs at ~1 tok/s. Pointing at the library explicitly is the
        # difference between 1 and 30 tokens/sec, with no error either way.
        self._backend_lib = os.getenv("LLAMA_GGML_BACKEND", "")
        self._proc: subprocess.Popen | None = None
        self._loaded_id: str | None = None
        self._lock = asyncio.Lock()
        self._port = int(self.endpoint.rsplit(":", 1)[-1]) if ":" in self.endpoint else 8080

    @property
    def managed(self) -> bool:
        return bool(self._bin)

    @property
    def loaded_id(self) -> str | None:
        return self._loaded_id

    def vram_free_mb(self) -> int:
        reading = _vram_mb()
        if reading is None:
            return 0
        used, total = reading
        return max(0, total - used)

    async def probe(self) -> bool:
        """Is a llama-server answering at the endpoint right now?"""
        try:
            async with httpx.AsyncClient(timeout=3) as client:
                r = await client.get(f"{self.endpoint}/health")
                return r.status_code == 200
        except httpx.HTTPError:
            return False

    async def startup(self) -> None:
        """Establish what is resident. External mode: whatever answers is the
        default model by definition, since compose starts exactly one."""
        if self.managed:
            # A server answering before we have started anything is an orphan
            # from a killed run, and it owns the GPU we are about to need.
            if await self.probe():
                self._reap_orphan_server()
                await self._wait_port_free()
            await self.ensure(self.registry.default.id, emit=None)
        elif await self.probe():
            self._loaded_id = self.registry.default.id

    async def ensure(self, model_id: str, emit: EmitFn | None) -> None:
        """Make model_id the resident model. Emits model.loading before any
        blocking work and model.ready after; no-op when already resident."""
        spec = self.registry.get(model_id)
        async with self._lock:
            if self._loaded_id == model_id and await self.probe():
                return
            if not self.managed:
                if self._loaded_id == model_id:
                    return
                raise RuntimeError(
                    f"external llama-server holds {self._loaded_id!r}; cannot swap to "
                    f"{model_id!r} without LLAMA_SERVER_BIN (managed mode)"
                )
            evicting = self._loaded_id
            if emit:
                await emit(ModelLoading(
                    model_id=model_id,
                    evicting=evicting,
                    eta_s=self._estimate_load_s(model_id),
                ))
            started = time.monotonic()
            self._stop_server()
            self._loaded_id = None
            # The dying server keeps answering /health for a moment after
            # terminate(). Starting the replacement while the old one still
            # holds :8080 makes the very next probe() succeed against a corpse:
            # we declare ready in ~1 s, then the first completion 404s because
            # nothing is actually serving the new weights. Wait for the port to
            # go quiet before spawning.
            await self._wait_port_free()
            self._start_server(spec)
            await self._wait_healthy(spec)
            load_ms = int((time.monotonic() - started) * 1000)
            self._loaded_id = model_id
            self._record_load(model_id, load_ms)
            if emit:
                await emit(ModelReady(
                    model_id=model_id,
                    load_ms=load_ms,
                    vram_mb=_vram_used_mb() or spec.vram_mb,
                ))

    def _model_path(self, rel: str) -> Path:
        p = Path(rel)
        return p if p.is_absolute() else (self.models_dir.parent / p)

    def _start_server(self, spec: ModelSpec) -> None:
        args = [
            self._bin,
            "--host", "127.0.0.1",
            "--port", str(self._port),
            "--model", str(self._model_path(spec.path)),
            "--ctx-size", str(spec.context),
            "--n-gpu-layers", str(spec.n_gpu_layers),
            "--cache-type-k", spec.cache_type_k,
            "--cache-type-v", spec.cache_type_v,
        ]
        # Newer llama.cpp takes --flash-attn on|off|auto. A bare flag makes the
        # parser swallow whatever follows, which is --mmproj for the vision
        # model -- so always pass the value explicitly.
        args += ["--flash-attn", "on" if spec.flash_attn else "off"]
        # One slot, not the auto-chosen four. The agent re-sends a growing
        # conversation on every step (measured: 1725 prompt tokens at step 1,
        # 3191 by step 3), so what matters is that the next step lands on the
        # slot still holding the previous step's KV cache. Four slots split the
        # budget and let a request land on a cold one. Single-user demo box.
        args += ["--parallel", "1"]
        # Reuse cached KV across a prefix that shifted rather than reprocessing
        # it -- exactly the compaction / observation-append case.
        args += ["--cache-reuse", "256"]
        if spec.mmproj:
            args += ["--mmproj", str(self._model_path(spec.mmproj))]
        env = os.environ.copy()
        if self._backend_lib:
            env["GGML_BACKEND_PATH"] = self._backend_lib
            # The backend DLL's own dependencies (cublas, cudart) sit beside it.
            env["PATH"] = str(Path(self._backend_lib).parent) + os.pathsep + env.get("PATH", "")
        self._proc = subprocess.Popen(
            args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env
        )

    def _stop_server(self) -> None:
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                self._proc.wait(timeout=5)
        self._proc = None

    def _reap_orphan_server(self) -> int | None:
        """Kill a llama-server we did not start that is squatting our port.

        If uvicorn is killed rather than shut down, the FastAPI shutdown hook
        never runs and the child llama-server survives holding the entire VRAM
        budget -- the next start then cannot fit a model and fails for a reason
        that looks nothing like the cause. Reclaim it instead of starving.
        """
        pid = self._pid_on_port()
        if pid is None:
            return None
        try:
            if os.name == "nt":
                subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                               capture_output=True, timeout=10)
            else:
                os.kill(pid, signal.SIGKILL)
        except Exception:
            return None
        return pid

    def _pid_on_port(self) -> int | None:
        """PID listening on the model endpoint's port, or None."""
        try:
            if os.name == "nt":
                out = subprocess.run(["netstat", "-ano"], capture_output=True,
                                     text=True, timeout=10).stdout
                for line in out.splitlines():
                    parts = line.split()
                    if (len(parts) >= 5 and "LISTENING" in parts
                            and parts[1].endswith(f":{self._port}")):
                        return int(parts[-1])
            else:
                out = subprocess.run(["lsof", "-ti", f"tcp:{self._port}"],
                                     capture_output=True, text=True, timeout=10).stdout
                if out.strip():
                    return int(out.strip().splitlines()[0])
        except Exception:
            return None
        return None

    async def _wait_port_free(self, timeout_s: int = 30) -> None:
        """Block until nothing answers on the endpoint, so the replacement
        server cannot be confused with the one it is replacing."""
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if not await self.probe():
                return
            await asyncio.sleep(0.25)

    async def _wait_healthy(self, spec: ModelSpec, timeout_s: int = 120) -> None:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if self._proc and self._proc.poll() is not None:
                raise RuntimeError(
                    f"llama-server exited with {self._proc.returncode} loading {spec.id}"
                )
            if await self.probe():
                return
            await asyncio.sleep(0.5)
        raise TimeoutError(f"llama-server did not become healthy loading {spec.id}")

    def shutdown(self) -> None:
        self._stop_server()
