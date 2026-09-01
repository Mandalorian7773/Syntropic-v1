# Architecture

> **Placeholder.** The full architecture document will be pasted here.

Until then, the one-paragraph version so nobody is blocked:

The SPA (5173) talks to the backend gateway (8000) and nothing else. The
gateway owns routing, the model manager, the agent loop, the tool registry and
the sandbox; it proxies document and retrieval calls to ragsvc (8001), which
owns ingest, the Qdrant + BM25 hybrid index and artifact generation. Exactly
one GGUF is resident on the GPU at a time, served by llama-server (8080).
Embeddings and OCR run on CPU. All state is on local disk: SQLite for sessions
and audit, Qdrant for vectors, plain directories for workspace and artifacts.
Nothing reaches the internet at runtime.

```
browser :5173 ──> backend :8000 ──> llama-server :8080  (one model resident)
                       │
                       └────────> ragsvc :8001 ──> qdrant :6333
```

See `docs/decisions/` for why each of those choices is what it is.
