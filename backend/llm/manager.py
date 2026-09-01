"""Model lifecycle manager. Owner: person 3. Empty by design.

Will contain: exactly one GGUF resident on the GPU at a time -- load, evict,
and the llama-server process supervision that goes with it. Emits model.loading
(with `evicting`) and model.ready (with load_ms and vram_mb).

See docs/decisions/0003-one-gpu-model-resident.md.
"""
