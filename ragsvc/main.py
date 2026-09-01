"""RAG service. Owner: person 2.

Scaffold with two live endpoints so the backend can wire against it today:

    GET  /health    liveness
    POST /search    returns an empty hit list

Person 2 builds out ingest, hybrid retrieval and artifact generation behind
these. Note the paths here are unprefixed (/health, /search): the backend
proxies them under /api/*, so the SPA only ever talks to one origin.
"""

from __future__ import annotations

from fastapi import FastAPI

from contracts import SearchRequest, SearchResponse

app = FastAPI(title="SIH26117 ragsvc", version="0.1.0")


@app.get("/health")
async def health() -> dict:
    return {"ok": True, "qdrant": False, "documents": 0}


@app.post("/search", response_model=SearchResponse)
async def search(req: SearchRequest) -> SearchResponse:
    # Empty by design. Person 2 replaces this with bm25 + dense fusion + rerank.
    _ = req
    return SearchResponse(hits=[])
