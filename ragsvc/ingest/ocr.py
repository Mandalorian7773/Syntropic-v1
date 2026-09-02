"""Optical character recognition for scanned pages. Owner: person 2.

CPU only. This is not a fallback, it is the architecture: all 6 GB of VRAM
belongs to the resident LLM, and an OCR model that evicts it costs ~8 seconds
per page to swap back. See docs/decisions/0005-cpu-embeddings.md.

Two interchangeable backends, both running PP-OCRv4 weights:

* **rapidocr** (default) is PP-OCRv4 exported to ONNX, and the weights ship
  *inside the wheel*. It is therefore incapable of reaching for the network on
  first use, which is the property that matters most here.
* **paddle** is the reference PaddleOCR stack. It is only selected when its
  model directories already exist under `models/paddleocr/`, because a bare
  `PaddleOCR()` downloads to `~/.paddleocr` on first call, and a first call
  that happens on stage is a first call that fails.

Both return the same `Line` objects with per-line confidence, and both convert
pixel boxes to PDF points at this boundary so nothing downstream needs to know
what DPI produced them.
"""

from __future__ import annotations

import threading

import numpy as np

import ragconfig as cfg

from .model import Line

_engine = None
_engine_name = "none"
_lock = threading.Lock()

# One engine per worker thread. onnxruntime sessions are not reentrant, so a
# shared engine would serialise the pool it exists to parallelise. The models
# are memory-mapped by onnxruntime, so N engines do not cost N times the RAM.
_thread_local = threading.local()


class OcrUnavailable(RuntimeError):
    """Raised when a scanned page needs OCR and no backend is installed."""


def _quad_to_bbox(quad, scale: float) -> tuple[float, float, float, float]:
    """Axis-aligned bounding box in points from a 4-point pixel polygon.

    OCR detectors return rotated quadrilaterals. Layout analysis works in rows
    and columns, so the quad is squared off here once rather than in three
    places later.
    """
    points = np.asarray(quad, dtype=float).reshape(-1, 2)
    x0, y0 = points.min(axis=0)
    x1, y1 = points.max(axis=0)
    return (x0 / scale, y0 / scale, x1 / scale, y1 / scale)


def _paddle_available() -> bool:
    if not (cfg.OCR_DET_DIR.exists() and cfg.OCR_REC_DIR.exists()):
        return False
    try:
        import paddleocr  # noqa: F401,PLC0415
    except Exception:  # noqa: BLE001
        return False
    return True


def _build_paddle():
    from paddleocr import PaddleOCR  # noqa: PLC0415

    kwargs = dict(
        use_angle_cls=cfg.OCR_CLS_DIR.exists(),
        lang="en",
        use_gpu=False,
        show_log=False,
        det_model_dir=str(cfg.OCR_DET_DIR),
        rec_model_dir=str(cfg.OCR_REC_DIR),
        cpu_threads=cfg.CPU_THREADS,
    )
    if cfg.OCR_CLS_DIR.exists():
        kwargs["cls_model_dir"] = str(cfg.OCR_CLS_DIR)
    return PaddleOCR(**kwargs)


def _build_rapid(threads: int | None = None):
    """Build a RapidOCR engine pinned to `threads` cores.

    The thread count has to be set per model -- `det_`, `cls_` and `rec_` --
    and not once globally. RapidOCR's config resolves the Global value into
    each model section through a YAML anchor when the file is read, so a bare
    `intra_op_num_threads=` lands in Global *after* that has happened and is
    silently ignored. Every session then defaults to -1, meaning all cores,
    and a pool of six engines oversubscribes an 8-core box twelvefold. Measured
    cost of getting this wrong: 227 seconds against 63 for the same document.
    """
    from rapidocr_onnxruntime import RapidOCR  # noqa: PLC0415

    threads = threads or cfg.OCR_THREADS_PER_WORKER
    pinned = {
        f"{model}_{setting}": value
        for model in ("det", "cls", "rec")
        for setting, value in (
            ("intra_op_num_threads", threads),
            ("inter_op_num_threads", 1),
        )
    }
    pinned["use_cls"] = cfg.OCR_USE_CLS
    pinned["rec_batch_num"] = cfg.OCR_REC_BATCH
    if cfg.OCR_REC_MODEL.exists():
        # English recogniser in place of the bundled Chinese one. Its character
        # dictionary travels inside the ONNX metadata, so no separate keys file
        # is needed. See the note in ragconfig.
        pinned["rec_model_path"] = str(cfg.OCR_REC_MODEL)
    try:
        return RapidOCR(**pinned)
    except TypeError:
        # A release that does not accept the per-model keys still honours the
        # OMP pins ragconfig sets before onnxruntime is imported.
        return RapidOCR()


def get_engine():
    """Load the OCR backend once. Thread-safe; the first caller pays the cost."""
    global _engine, _engine_name
    if _engine is not None:
        return _engine
    with _lock:
        if _engine is not None:
            return _engine

        wanted = cfg.OCR_BACKEND
        order: list[str]
        if wanted == "none":
            order = []
        elif wanted == "auto":
            order = ["paddle", "rapidocr"] if _paddle_available() else ["rapidocr"]
        else:
            order = [wanted]

        errors = []
        for name in order:
            try:
                _engine = _build_paddle() if name == "paddle" else _build_rapid()
                _engine_name = name
                return _engine
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{name}: {exc}")

        raise OcrUnavailable(
            "no OCR backend available ("
            + "; ".join(errors or ["backend disabled"])
            + "). Install the ocr extra: pip install -e './ragsvc[ocr]'"
        )


def get_worker_engine():
    """An engine private to the calling thread, built on first use.

    The first call also primes the shared engine so `backend_name()` is
    populated and a missing backend fails once, up front, rather than
    simultaneously in every worker.
    """
    engine = getattr(_thread_local, "engine", None)
    if engine is not None:
        return engine
    shared = get_engine()
    if threading.current_thread() is threading.main_thread():
        _thread_local.engine = shared
        return shared
    _thread_local.engine = (
        _build_paddle() if _engine_name == "paddle" else _build_rapid()
    )
    return _thread_local.engine


def backend_name() -> str:
    """Which backend is loaded, for /health and the ingest report."""
    return _engine_name


def read_page(image: np.ndarray, scale: float) -> list[Line]:
    """OCR one preprocessed page image into positioned lines.

    `scale` is pixels per point, so a 200 DPI render passes 200/72. Lines come
    back sorted in rough reading order; layout.py does the real ordering.

    Safe to call from several threads at once: each gets its own engine.
    """
    engine = get_worker_engine()
    lines: list[Line] = []

    if _engine_name == "paddle":
        raw = engine.ocr(image, cls=cfg.OCR_CLS_DIR.exists())
        # PaddleOCR wraps per-image results in an outer list, and returns
        # [None] for a page it found nothing on.
        page_result = (raw or [None])[0] or []
        for entry in page_result:
            quad, (text, conf) = entry[0], entry[1]
            text = (text or "").strip()
            if not text:
                continue
            lines.append(
                Line(text=text, bbox=_quad_to_bbox(quad, scale), conf=float(conf))
            )
    else:
        raw, _elapsed = engine(image)
        for entry in raw or []:
            quad, text, conf = entry[0], entry[1], entry[2]
            text = (text or "").strip()
            if not text:
                continue
            lines.append(
                Line(text=text, bbox=_quad_to_bbox(quad, scale), conf=float(conf))
            )

    lines.sort(key=lambda ln: (round(ln.y0, 1), ln.x0))
    return lines


_pool = None
_pool_failed = False


def get_pool():
    """The shared OCR worker pool, or None when pages must be done inline.

    **Processes, not threads.** Recognition is GIL-bound, so a thread pool
    measured *slower* than sequential (0.49x). See ingest/ocr_worker.py.

    Long-lived on purpose: each worker pays a model load on its first page, and
    a pool created per document would pay it again for every upload. Returns
    None if a pool cannot be created, so the caller falls back to inline work
    rather than failing the ingest.
    """
    global _pool, _pool_failed
    if _pool is not None or _pool_failed:
        return _pool
    if cfg.OCR_WORKERS <= 1:
        _pool_failed = True
        return None
    try:
        from concurrent.futures import ProcessPoolExecutor  # noqa: PLC0415

        from .ocr_worker import init_worker  # noqa: PLC0415

        _pool = ProcessPoolExecutor(
            max_workers=max(1, cfg.OCR_WORKERS), initializer=init_worker
        )
    except Exception:  # noqa: BLE001 - a restricted environment may forbid this
        _pool_failed = True
        _pool = None
    return _pool


def shutdown_pool() -> None:
    """Tear the pool down. Used by tests; the service keeps it for its life."""
    global _pool, _pool_failed
    if _pool is not None:
        _pool.shutdown(wait=False, cancel_futures=True)
    _pool = None
    _pool_failed = False


def mean_confidence(lines: list[Line]) -> float:
    """Length-weighted mean confidence.

    Weighting by character count stops a page of solid text from being marked
    doubtful because one three-character stamp in the corner scored 0.3.
    """
    total_chars = sum(len(ln.text) for ln in lines)
    if not total_chars:
        return 0.0
    return sum(ln.conf * len(ln.text) for ln in lines) / total_chars
