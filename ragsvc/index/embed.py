"""BGE-M3 dense embeddings, ONNX int8, CPU. Owner: person 2.

CPU is the design, not a fallback. All 6 GB of VRAM belongs to the resident
LLM; an embedding model that shares it evicts the LLM, and an eviction costs
about 8 seconds per query. See docs/decisions/0005-cpu-embeddings.md.

onnxruntime rather than torch, for three reasons that all matter to this
project: the wheel is 15 MB against 2.5 GB, so the offline bundle stays
copyable; it starts in about a second rather than ten; and int8 dynamic
quantisation roughly quarters both the file and the arithmetic for a
retrieval-quality loss that the eval harness measures at under a point of
recall@5.

Output is 1024-dimensional, L2-normalised, so a Qdrant cosine distance and a
dot product are the same number.

Nothing here reaches the network. `HF_HUB_OFFLINE` is pinned in ragconfig
before this module is importable, and the tokenizer and graph both load from
explicit local paths.
"""

from __future__ import annotations

import threading

import numpy as np

import ragconfig as cfg

_embedder = None
_lock = threading.Lock()

EXPORT_HINT = (
    "Run scripts/export-onnx-models.py on a connected machine, then copy "
    "models/ to this host. Nothing is ever downloaded at runtime."
)


class WeightsMissing(FileNotFoundError):
    """Raised when the embedding graph or its tokenizer is not on disk."""


class Embedder:
    """Loads once, encodes many. Thread-safe for concurrent search requests."""

    def __init__(
        self,
        onnx_path=None,
        tokenizer_path=None,
        max_len: int | None = None,
        threads: int | None = None,
    ) -> None:
        import onnxruntime as ort  # noqa: PLC0415
        from tokenizers import Tokenizer  # noqa: PLC0415

        onnx_path = onnx_path or cfg.EMBED_ONNX
        tokenizer_path = tokenizer_path or cfg.EMBED_TOKENIZER
        self.max_len = max_len or cfg.EMBED_MAX_LEN

        if not onnx_path.exists():
            raise WeightsMissing(f"embedding model not found at {onnx_path}. {EXPORT_HINT}")
        if not tokenizer_path.exists():
            raise WeightsMissing(f"tokenizer not found at {tokenizer_path}. {EXPORT_HINT}")

        options = ort.SessionOptions()
        options.intra_op_num_threads = threads or cfg.CPU_THREADS
        options.inter_op_num_threads = 1
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        # CPUExecutionProvider, explicitly and only. If a CUDA build of
        # onnxruntime is ever installed by accident, this keeps it off the GPU
        # that llama-server is using.
        self.session = ort.InferenceSession(
            str(onnx_path), options, providers=["CPUExecutionProvider"]
        )
        self.input_names = {i.name for i in self.session.get_inputs()}

        self.tokenizer = Tokenizer.from_file(str(tokenizer_path))
        self.tokenizer.enable_truncation(max_length=self.max_len)
        pad_id = self.tokenizer.token_to_id("<pad>")
        if pad_id is None:
            pad_id = self.tokenizer.token_to_id("[PAD]") or 0
        self.tokenizer.enable_padding(pad_id=pad_id, pad_token="<pad>")
        self._lock = threading.Lock()

    @property
    def dim(self) -> int:
        return cfg.VECTOR_SIZE

    def _feed(self, batch: list[str]) -> dict[str, np.ndarray]:
        encodings = self.tokenizer.encode_batch(batch)
        ids = np.asarray([e.ids for e in encodings], dtype=np.int64)
        mask = np.asarray([e.attention_mask for e in encodings], dtype=np.int64)
        feed = {"input_ids": ids, "attention_mask": mask}
        if "token_type_ids" in self.input_names:
            feed["token_type_ids"] = np.zeros_like(ids)
        return {k: v for k, v in feed.items() if k in self.input_names}

    def encode(self, texts: list[str], batch_size: int | None = None) -> np.ndarray:
        """Encode texts to (n, 1024) float32, L2-normalised."""
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        batch_size = batch_size or cfg.EMBED_BATCH

        vectors: list[np.ndarray] = []
        for start in range(0, len(texts), batch_size):
            batch = texts[start : start + batch_size]
            feed = self._feed(batch)
            with self._lock:  # onnxruntime sessions are not reentrant
                outputs = self.session.run(None, feed)
            vectors.append(_pool(outputs, feed["attention_mask"]))

        stacked = np.vstack(vectors).astype(np.float32)
        norms = np.linalg.norm(stacked, axis=1, keepdims=True)
        return stacked / np.clip(norms, 1e-12, None)

    def encode_one(self, text: str) -> np.ndarray:
        return self.encode([text])[0]


def _pool(outputs: list[np.ndarray], mask: np.ndarray) -> np.ndarray:
    """CLS-pool the token states, or pass through an already-pooled output.

    BGE-M3's dense vector is the first token's hidden state. Exports vary in
    what they emit -- some add a pooled output, some only the sequence -- so
    the shape decides rather than the export's naming.
    """
    primary = outputs[0]
    if primary.ndim == 3:
        return primary[:, 0, :]
    if primary.ndim == 2:
        return primary
    # Some exports put the sequence second when a pooler is present.
    for candidate in outputs[1:]:
        if candidate.ndim == 3:
            return candidate[:, 0, :]
    raise ValueError(f"unexpected embedding output shape {primary.shape}")


class HashEmbedder:
    """Deterministic stand-in for the real model. **Tests only.**

    It exists so the chunking, fusion and tool-truncation tests can run on a
    machine that has not copied 600 MB of weights yet. It is never selected
    automatically -- `RAG_EMBED_BACKEND=hash` has to be set on purpose -- and
    it is not a fallback: retrieval quality with it is meaningless, and a
    silent degradation to it would make the eval harness lie.
    """

    def __init__(self, dim: int = cfg.VECTOR_SIZE) -> None:
        self._dim = dim

    @property
    def dim(self) -> int:
        return self._dim

    def encode(self, texts: list[str], batch_size: int | None = None) -> np.ndarray:
        _ = batch_size
        out = np.zeros((len(texts), self._dim), dtype=np.float32)
        for row, text in enumerate(texts):
            for token in text.lower().split():
                out[row, hash(token) % self._dim] += 1.0
        norms = np.linalg.norm(out, axis=1, keepdims=True)
        return out / np.clip(norms, 1e-12, None)

    def encode_one(self, text: str) -> np.ndarray:
        return self.encode([text])[0]


def get_embedder():
    """Process-wide embedder. First caller pays the load cost."""
    global _embedder
    if _embedder is not None:
        return _embedder
    with _lock:
        if _embedder is None:
            import os  # noqa: PLC0415

            if os.getenv("RAG_EMBED_BACKEND", "").strip().lower() == "hash":
                _embedder = HashEmbedder()
            else:
                _embedder = Embedder()
    return _embedder


def reset() -> None:
    """Drop the loaded model. Used by tests that switch backends."""
    global _embedder
    _embedder = None
