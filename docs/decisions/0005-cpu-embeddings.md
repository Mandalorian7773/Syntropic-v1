# 0005 — Embeddings and OCR run on CPU

**Status:** accepted
**Date:** 2026-09-02
**Owner:** person 2

## Context

The workbench has one **6 GB** GPU -- an RTX 4050, 6141 MiB usable -- and
decision [0003](0003-one-gpu-model-resident.md) has already spent all of it:
exactly one GGUF is resident. Measured on a clean card by Person 3, the vision
model plus its multimodal projector at 16k context occupies 5903 MiB, leaving
**238 MiB free**.

(The scaffold and an earlier draft of this record both said 8 GB, which came
from `models/MANIFEST.yaml` rather than from the hardware. The correction makes
the argument below stronger, not weaker: there was never 2 GB of slack to
argue over.)

Eviction is the whole argument. Reloading a 7B GGUF takes about 8 seconds. An
embedding model sharing the GPU would force that reload on every query that
follows an ingest, and an OCR model would force it on every page. A retrieval
step that costs 8 seconds of visible dead air is not a retrieval step anyone
will use on stage.

So the question was never "GPU or CPU". It was whether CPU inference is fast
enough to meet the two numbers in the brief: a 20-page scanned PDF ingested in
under 90 seconds, and search under 2 seconds for `top_k=5`.

## Decision

**Embeddings, reranking and OCR all run on CPU, through onnxruntime, with the
`CPUExecutionProvider` named explicitly rather than left to default.** No torch
anywhere in `ragsvc/`.

onnxruntime rather than torch for three reasons that all matter here: the wheel
is 15 MB against 2.5 GB, so the offline bundle stays copyable to the demo host;
it starts in about two seconds rather than ten; and int8 dynamic quantisation
quarters both the file and the arithmetic.

Weights are int8 ONNX exports loaded from explicit local paths, listed in
`models/MANIFEST.yaml` under `rag_models:` and hashed into
`models/rag-models.lock.json`. Nothing is fetched at runtime.

### Measured on the target class of machine

AMD Ryzen 7 7435HS, 8 cores, 16 GB, no GPU used by this service.

| Stage | Model | Measurement |
|---|---|---|
| Query embedding | BGE-M3, int8 ONNX, 1024-dim | **23 ms** per query |
| Batch embedding | same, batch 16 | ~50 ms per chunk |
| Model load | same | 1.9 s, once per process |
| Reranking | bge-reranker-v2-m3, int8 ONNX | ~35 ms per passage |
| OCR detection | PP-OCRv4 det | 0.79 s per page |
| OCR recognition | PP-OCRv3 English rec | 1.46 s per page |
| **Ingest, end to end** | 20-page scanned PDF | **81.6 s** (4.08 s/page) |

Peak resident memory for the service is under 3 GB, against a 5 GB budget.
GPU memory used: zero, which is the point.

## Consequences

**Ingest is a one-off cost paid before the demo, and query time is not.** The
expensive direction is embedding a corpus; embedding a single short query is 23
milliseconds. The demo run sheet already says the corpus is ingested in
advance, and this is why.

**Three findings cost more time than the decision itself, and are recorded here
so nobody repeats them.**

1. *The bundled OCR recogniser was the Chinese PP-OCRv4 model.* It is trained
   on a script that does not use word spacing and emits none, so an English
   letterhead came back as `MANGALOREREFINERYANDPETROCHEMICALSLIMITED`. BM25
   cannot match a single word of that. Switching to the English PP-OCRv3
   recogniser fixed the text and was 24% faster as a side effect. This was a
   correctness bug wearing a performance costume.

2. *Recognising one text box at a time is 2.8x faster than the default batch of
   six* — 1.46 s a page against 4.12 s, for byte-identical output. Every crop
   in a batch is padded to the width of the widest, and a page mixing a
   two-word table cell with a full-width sentence spends most of a batch
   multiplying padding.

3. *Parallelising pages does not work on this hardware, in either direction.* A
   six-thread pool ran at 0.49x of sequential, because the recogniser holds the
   GIL. A process pool removed the GIL and was still slower: 153 s sequential,
   143 s with two workers, 220 s with three. Recognition is a
   memory-bandwidth-bound LSTM, and several copies of it contend for L3 rather
   than scaling. `RAG_OCR_WORKERS` defaults to 1 and stays a knob, because the
   demo host is a different machine and re-measuring it is an environment
   variable rather than a code change.

**Resolution is the last thing traded away, not the first.** The pipeline
watches its own clock and drops from 200 to 150 DPI for the pages still to
come only when it is running over budget, recording that it did in the ingest
report. Everything else — deskew, table detection, the reranker — is a step
change in output quality; resolution degrades smoothly.

**If a future machine has spare VRAM**, this decision should be revisited only
if decision 0003 changes first. The constraint is the resident language model,
not a belief that CPU inference is preferable.
