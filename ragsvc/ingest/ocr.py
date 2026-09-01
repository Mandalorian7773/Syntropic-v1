"""OCR for scanned pages. Owner: person 2. Empty by design.

Will contain: the OCR pass over image-only pages. Runs on CPU -- the GPU is
fully committed to the resident LLM, see
docs/decisions/0005-cpu-embeddings.md. Models are vendored, never downloaded
at runtime.
"""
