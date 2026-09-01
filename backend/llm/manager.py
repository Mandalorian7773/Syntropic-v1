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


def _vram_used_mb() -> int | None:
    """Actual VRAM in use, via nvidia-smi. None when there is no NVIDIA GPU."""
    smi = shutil.which("nvidia-smi")
    if not smi:
        return None
    try:
        out = subprocess.run(
            [smi, "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        )
        return int(out.stdout.strip().splitlines()[0])
    except Exception:
        return None


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
        used = _vram_used_mb()
        return max(0, 8192 - used) if used is not None else 0

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
        if spec.flash_attn:
            args.append("--flash-attn")
        if spec.mmproj:
            args += ["--mmproj", str(self._model_path(spec.mmproj))]
        self._proc = subprocess.Popen(
            args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
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
