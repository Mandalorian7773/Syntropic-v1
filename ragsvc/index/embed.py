"""Embedding model. Owner: person 2. Empty by design.

Will contain: the ONNX embedding model running on CPU via onnxruntime.
CPU is not a fallback here, it is the design -- the 8 GB of VRAM belongs to
the LLM. See docs/decisions/0005-cpu-embeddings.md.
"""
