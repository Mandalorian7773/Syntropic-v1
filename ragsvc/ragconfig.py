"""Runtime configuration for ragsvc. Owner: person 2.

Two things happen when this module is imported, and both matter:

1.  **Every path resolves against the repository root, never the process
    working directory.** `make dev-rag` starts uvicorn from inside `ragsvc/`,
    Docker starts it from `/app/ragsvc`, and pytest starts it from the repo
    root. A relative `ARTIFACTS_DIR=./artifacts` has to mean the same
    directory in all three, or the demo writes its deliverables somewhere
    nobody is looking.

2.  **The offline environment variables are pinned before any model library
    loads.** huggingface_hub, tokenizers and paddle all decide whether they are
    allowed to reach the network at import time, from the environment they find
    then. Setting these afterwards is setting them too late, which is why
    `main.py` imports this module on its first line.

Nothing in this file may contain a URL that is not a loopback or a compose
service name. scripts/airgap-check.sh greps for exactly that.
"""

from __future__ import annotations

import os
from pathlib import Path

# --- Offline pins. Set before onnxruntime / tokenizers / paddle import. ------
# setdefault, not assignment: an operator who deliberately exports one of these
# for a setup-time export run should not be silently overridden.
for _key, _value in {
    "HF_HUB_OFFLINE": "1",
    "TRANSFORMERS_OFFLINE": "1",
    "HF_DATASETS_OFFLINE": "1",
    "HF_HUB_DISABLE_TELEMETRY": "1",
    "HF_HUB_DISABLE_IMPLICIT_TOKEN": "1",
    "DO_NOT_TRACK": "1",
    "SCARF_NO_ANALYTICS": "true",
    "TOKENIZERS_PARALLELISM": "false",
    "PADDLE_DISABLE_TELEMETRY": "1",
}.items():
    os.environ.setdefault(_key, _value)

REPO_ROOT = Path(__file__).resolve().parent.parent
RAGSVC_DIR = Path(__file__).resolve().parent


def _dir(env_name: str, default: str) -> Path:
    """Resolve a directory from the environment, anchored at the repo root."""
    raw = os.getenv(env_name, default)
    path = Path(raw)
    if not path.is_absolute():
        path = REPO_ROOT / path
    return path.resolve()


def _int(env_name: str, default: int) -> int:
    try:
        return int(os.getenv(env_name, str(default)))
    except ValueError:
        return default


def _flag(env_name: str, default: bool) -> bool:
    return os.getenv(env_name, "1" if default else "0").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


# --- Storage ----------------------------------------------------------------
WORKSPACE_DIR = _dir("WORKSPACE_DIR", "./workspace")
ARTIFACTS_DIR = _dir("ARTIFACTS_DIR", "./artifacts")
MODELS_DIR = _dir("MODELS_DIR", "./models")
DOCUMENTS_DIR = _dir("RAG_DOCUMENTS_DIR", "./workspace/documents")

_db = os.getenv("DB_PATH", "./data/workbench.db")
DB_PATH = Path(_db) if Path(_db).is_absolute() else (REPO_ROOT / _db).resolve()

# --- Qdrant -----------------------------------------------------------------
# QDRANT_URL is the production path: a container on the internal network.
# QDRANT_LOCAL_PATH is the embedded store qdrant-client ships, used by tests
# and by a laptop with no Docker. Same client, same query semantics, no server.
# 127.0.0.1, not localhost, and the difference is 2 seconds a query.
#
# On Windows `localhost` resolves to ::1 before 127.0.0.1, and Qdrant binds
# 0.0.0.0 -- IPv4 only. Every request therefore opens an IPv6 connection that
# nothing is listening on, waits out the connect timeout, and only then retries
# on IPv4. Measured: 2048 ms per search against localhost, 8 ms against
# 127.0.0.1, with recall identical. It looks exactly like a slow vector store
# and is nothing of the kind.
#
# docker-compose sets QDRANT_URL to http://qdrant:6333 explicitly, so this
# default only ever applies to local development -- which is where it bites.
QDRANT_URL = os.getenv("QDRANT_URL", "http://127.0.0.1:6333")
QDRANT_LOCAL = _flag("RAG_QDRANT_LOCAL", False)
QDRANT_LOCAL_PATH = _dir("RAG_QDRANT_LOCAL_PATH", "./qdrant_data/embedded")
COLLECTION = os.getenv("RAG_COLLECTION", "kb")
VECTOR_SIZE = 1024  # BGE-M3 dense output. Not configurable; the collection is.

# --- Model weights. Local paths only; nothing is ever fetched at runtime. ----
EMBED_DIR = MODELS_DIR / os.getenv("RAG_EMBED_DIR", "bge-m3-onnx")
EMBED_ONNX = EMBED_DIR / os.getenv("RAG_EMBED_ONNX", "model_int8.onnx")
EMBED_TOKENIZER = EMBED_DIR / "tokenizer.json"

RERANK_DIR = MODELS_DIR / os.getenv("RAG_RERANK_DIR", "bge-reranker-v2-m3-onnx")
RERANK_ONNX = RERANK_DIR / os.getenv("RAG_RERANK_ONNX", "model_int8.onnx")
RERANK_TOKENIZER = RERANK_DIR / "tokenizer.json"

OCR_DIR = MODELS_DIR / os.getenv("RAG_OCR_DIR", "paddleocr")
OCR_DET_DIR = OCR_DIR / "det"
OCR_REC_DIR = OCR_DIR / "rec"
OCR_CLS_DIR = OCR_DIR / "cls"

# The English PP-OCR recogniser, used in place of the Chinese one that
# rapidocr bundles. This is a correctness fix before it is a speed one: the
# Chinese model is trained on a script with no word spacing and emits none, so
# a letterhead comes back as "MANGALOREREFINERYANDPETROCHEMICALSLIMITED",
# which BM25 cannot match a single word of. The English model also drops the
# output projection from 6625 classes to 97, which is why it is 24% faster.
# Fetched by scripts/fetch-rag-models.py; the Chinese model is the fallback if
# it is absent.
OCR_REC_MODEL = MODELS_DIR / os.getenv(
    "RAG_OCR_REC_MODEL", "rapidocr/en_PP-OCRv3_rec_infer.onnx"
)
# Angle classification detects 180-degree flips. Deskew in preprocess.py
# already handles the small rotations a scanner introduces, and a flipped page
# is not a case this corpus has, so it is off: 0.08 s a page for nothing.
OCR_USE_CLS = _flag("RAG_OCR_USE_CLS", False)

# Recognise one text box at a time. This looks wrong and is the single largest
# speed-up in the pipeline: measured 1.46 s a page against 4.12 s at the
# default batch of 6, for byte-identical text. Every crop in a batch is padded
# to the width of the widest one, and a page mixing a two-word table cell with
# a full-width sentence spends most of a batch multiplying padding. Sorting by
# width, which rapidocr already does, narrows the spread but does not close it.
OCR_REC_BATCH = _int("RAG_OCR_REC_BATCH", 1)

# "paddle" | "rapidocr" | "none" | "auto". Both real backends run PP-OCRv4
# weights on CPU; rapidocr is the same model family exported to ONNX.
OCR_BACKEND = os.getenv("RAG_OCR_BACKEND", "auto").strip().lower()

# --- CPU budget -------------------------------------------------------------
# The GPU belongs to llama-server. See docs/decisions/0005-cpu-embeddings.md.
# Leaving a core free keeps the box responsive while a 20-page ingest runs.
_cpus = os.cpu_count() or 4
CPU_THREADS = _int("RAG_CPU_THREADS", max(1, min(8, _cpus - 1)))
os.environ.setdefault("OMP_NUM_THREADS", str(CPU_THREADS))
os.environ.setdefault("MKL_NUM_THREADS", str(CPU_THREADS))

# Pages can be OCR'd in a worker pool, and by default they are not.
#
# This is measured, not assumed. On an 8-core laptop, ingesting the same
# 20-page scan took 153 s with one worker, 143 s with two and 220 s with three.
# Recognition is a memory-bandwidth-bound LSTM, so several copies of it fight
# over L3 cache and get slower together rather than faster in parallel; a
# thread pool was worse still, at 0.49x of sequential, because the recogniser
# holds the GIL. The knob stays because the demo host is a different machine
# and this is one environment variable to re-measure, not a code change.
OCR_WORKERS = _int("RAG_OCR_WORKERS", 1)
# One core each. The pool is already as wide as the physical core count, so
# anything above 1 here oversubscribes rather than accelerates.
OCR_THREADS_PER_WORKER = _int(
    "RAG_OCR_THREADS_PER_WORKER", 1 if OCR_WORKERS > 1 else CPU_THREADS
)

# --- Ingest -----------------------------------------------------------------
RENDER_DPI = _int("RAG_RENDER_DPI", 200)
RENDER_DPI_FALLBACK = _int("RAG_RENDER_DPI_FALLBACK", 150)
# Wall-clock budget per page before the pipeline drops to the fallback DPI.
# 90 s for 20 pages, with headroom for embedding and indexing at the end.
PAGE_TIME_BUDGET_S = float(os.getenv("RAG_PAGE_BUDGET_S", "3.2"))
NATIVE_TEXT_MIN_CHARS = _int("RAG_NATIVE_MIN_CHARS", 120)
OCR_LOW_CONF = float(os.getenv("RAG_OCR_LOW_CONF", "0.60"))

CHUNK_TOKENS = _int("RAG_CHUNK_TOKENS", 600)
CHUNK_OVERLAP = _int("RAG_CHUNK_OVERLAP", 100)
EMBED_BATCH = _int("RAG_EMBED_BATCH", 16)
EMBED_MAX_LEN = _int("RAG_EMBED_MAX_LEN", 768)
RERANK_BATCH = _int("RAG_RERANK_BATCH", 8)
# 320 rather than 512: reranking cost is linear in tokens, and a cross-encoder
# decides on the opening of a passage far more than its tail. Measured 10.1 s
# for 30 candidates against 19.8 s at 512.
RERANK_MAX_LEN = _int("RAG_RERANK_MAX_LEN", 320)

# --- Retrieval --------------------------------------------------------------
DENSE_TOP = _int("RAG_DENSE_TOP", 30)
SPARSE_TOP = _int("RAG_SPARSE_TOP", 30)
FUSE_KEEP = _int("RAG_FUSE_KEEP", 30)
RRF_K = _int("RAG_RRF_K", 60)

# Reranking is implemented, loaded from local weights, and OFF by default.
#
# That is a measurement, not an opinion, and eval/retrieval_eval.py is where it
# came from. On the demo corpus, hybrid retrieval alone scores recall@5 = 1.000
# with MRR 0.911 in 28 ms. Reranking the fused 30 with bge-reranker-v2-m3 costs
# 19.8 s per query on this CPU -- ten times the 2 s budget for the whole search
# -- to improve a recall figure that is already at its ceiling.
#
# The repo's own note on rerank.py says it best: a rerank that costs 400 ms and
# moves nothing is 400 ms off the demo clock. This one costs fifty times that.
#
# Turn it on with RAG_RERANK=1 and re-run the harness. The case for doing so is
# a corpus large enough that recall@5 falls below 0.9, at which point the right
# move is probably a smaller cross-encoder rather than this one: 568M
# parameters is the reason for the 19.8 s.
RERANK_ENABLED = _flag("RAG_RERANK", False)

# --- Tool output budget -----------------------------------------------------
# Consumed by a 7B model with a 16K window. One unbounded tool result poisons
# the context and the agent fails three steps later for no visible reason.
TOOL_TOKEN_BUDGET = _int("RAG_TOOL_TOKEN_BUDGET", 1000)

# --- Deliverables -----------------------------------------------------------
ORG_NAME = os.getenv("RAG_ORG_NAME", "Mangalore Refinery and Petrochemicals Limited")
ORG_UNIT = os.getenv("RAG_ORG_UNIT", "Inspection and Reliability Department")
ORG_LOCATION = os.getenv("RAG_ORG_LOCATION", "Kuthethur, Mangaluru 575030, Karnataka")
# Stamped into the footer of every generated document. A file this service
# produces must never be mistakable for one a human has already signed.
GENERATED_NOTICE = os.getenv(
    "RAG_GENERATED_NOTICE",
    "SYSTEM GENERATED DRAFT - NOT VALID WITHOUT AUTHORISED SIGNATURE",
)

# --- Safety -----------------------------------------------------------------
NETGUARD = _flag("RAG_NETGUARD", True)


def ensure_dirs() -> None:
    """Create the writable directories. Called once at startup."""
    for path in (WORKSPACE_DIR, ARTIFACTS_DIR, DOCUMENTS_DIR, DB_PATH.parent):
        path.mkdir(parents=True, exist_ok=True)


def missing_weights() -> list[str]:
    """Return a human-readable list of weights that are not on disk.

    Called by /health so a missing model is visible in the UI before the demo
    rather than as a 500 during it.
    """
    missing: list[str] = []
    if not EMBED_ONNX.exists():
        missing.append(f"embeddings: {EMBED_ONNX}")
    if not EMBED_TOKENIZER.exists():
        missing.append(f"embedding tokenizer: {EMBED_TOKENIZER}")
    if RERANK_ENABLED and not RERANK_ONNX.exists():
        missing.append(f"reranker: {RERANK_ONNX}")
    return missing
