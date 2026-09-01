"""Cross-encoder reranking with bge-reranker-v2-m3. Owner: person 2.

The retrievers score a query and a chunk independently and compare vectors.
This model reads them together, which is why it can tell that a chunk
mentioning "relief valve set pressure" is about a *different* valve than the
one asked about. That distinction is invisible to a bi-encoder and is most of
what reranking buys.

It is also the most expensive thing in the query path: 30 pairs at 512 tokens
on CPU. int8 and a batch of 8 keep it inside the 2-second budget. If the eval
harness ever shows it moving recall by less than a point, delete it -- 400 ms
that changes nothing is 400 ms off the demo clock. Measure, then decide;
`retrieval_eval.py --ablate` prints exactly that comparison.
"""

from __future__ import annotations

import threading

import numpy as np

import ragconfig as cfg

from .embed import EXPORT_HINT, WeightsMissing

_reranker = None
_lock = threading.Lock()


class Reranker:
    def __init__(
        self,
        onnx_path=None,
        tokenizer_path=None,
        max_len: int | None = None,
        threads: int | None = None,
    ) -> None:
        import onnxruntime as ort  # noqa: PLC0415
        from tokenizers import Tokenizer  # noqa: PLC0415

        onnx_path = onnx_path or cfg.RERANK_ONNX
        tokenizer_path = tokenizer_path or cfg.RERANK_TOKENIZER
        self.max_len = max_len or cfg.RERANK_MAX_LEN

        if not onnx_path.exists():
            raise WeightsMissing(f"reranker not found at {onnx_path}. {EXPORT_HINT}")
        if not tokenizer_path.exists():
            raise WeightsMissing(
                f"reranker tokenizer not found at {tokenizer_path}. {EXPORT_HINT}"
            )

        options = ort.SessionOptions()
        options.intra_op_num_threads = threads or cfg.CPU_THREADS
        options.inter_op_num_threads = 1
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
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

    def score(self, query: str, passages: list[str], batch_size: int | None = None) -> np.ndarray:
        """Relevance logits for each (query, passage) pair, higher is better."""
        if not passages:
            return np.zeros((0,), dtype=np.float32)
        batch_size = batch_size or cfg.RERANK_BATCH

        results: list[np.ndarray] = []
        for start in range(0, len(passages), batch_size):
            batch = passages[start : start + batch_size]
            encodings = self.tokenizer.encode_batch([(query, p) for p in batch])
            ids = np.asarray([e.ids for e in encodings], dtype=np.int64)
            mask = np.asarray([e.attention_mask for e in encodings], dtype=np.int64)
            feed = {"input_ids": ids, "attention_mask": mask}
            if "token_type_ids" in self.input_names:
                feed["token_type_ids"] = np.asarray(
                    [e.type_ids for e in encodings], dtype=np.int64
                )
            feed = {k: v for k, v in feed.items() if k in self.input_names}

            with self._lock:
                outputs = self.session.run(None, feed)
            logits = outputs[0]
            # Sequence-classification exports emit (n, 1) for a scoring head and
            # (n, 2) when the head was built as binary classification.
            if logits.ndim == 2 and logits.shape[1] == 1:
                results.append(logits[:, 0])
            elif logits.ndim == 2 and logits.shape[1] >= 2:
                results.append(logits[:, 1] - logits[:, 0])
            else:
                results.append(logits.reshape(len(batch)))

        return np.concatenate(results).astype(np.float32)


def get_reranker():
    global _reranker
    if _reranker is not None:
        return _reranker
    with _lock:
        if _reranker is None:
            _reranker = Reranker()
    return _reranker


def reset() -> None:
    global _reranker
    _reranker = None
