"""Qdrant vector store. Owner: person 2.

The collection is exactly the one in the build brief:

    {"name": "kb", "vectors": {"size": 1024, "distance": "Cosine"},
     "payload": {doc_id, filename, page, chunk_index, text, section}}

`filename` and `page` are in the payload rather than fetched from SQLite after
the fact for one reason: a hit must be citable from the search result alone. A
result without provenance is a bug, and the cheapest way to make that
impossible is to make provenance part of the thing that comes back.

Two deployments, one class. `QDRANT_URL` is a container on the internal
network and is what the demo runs. `RAG_QDRANT_LOCAL=1` uses the embedded store
qdrant-client ships -- same client, same query semantics, no server process --
which is what tests and a Docker-less laptop use.
"""

from __future__ import annotations

import threading
from typing import Any

import numpy as np

import ragconfig as cfg

_store = None
_lock = threading.Lock()


class VectorStore:
    def __init__(
        self,
        url: str | None = None,
        local_path=None,
        collection: str | None = None,
        prefer_local: bool | None = None,
    ) -> None:
        from qdrant_client import QdrantClient  # noqa: PLC0415

        self.collection = collection or cfg.COLLECTION
        use_local = cfg.QDRANT_LOCAL if prefer_local is None else prefer_local

        if use_local:
            path = local_path or cfg.QDRANT_LOCAL_PATH
            path.mkdir(parents=True, exist_ok=True)
            self.client = QdrantClient(path=str(path))
            self.mode = "embedded"
        else:
            self.client = QdrantClient(url=url or cfg.QDRANT_URL, timeout=30.0)
            self.mode = "server"

    # --- schema -------------------------------------------------------------

    def ensure_collection(self) -> None:
        """Create the collection if absent. Never drops an existing one."""
        from qdrant_client import models  # noqa: PLC0415

        if self.client.collection_exists(self.collection):
            return
        self.client.create_collection(
            collection_name=self.collection,
            vectors_config=models.VectorParams(
                size=cfg.VECTOR_SIZE, distance=models.Distance.COSINE
            ),
        )
        # Deleting or filtering by document is the one non-vector access
        # pattern this service has, and an unindexed payload filter is a full
        # scan of the collection. The embedded store has no payload indexes at
        # all and warns if asked for one, so this is server-only.
        if self.mode == "server":
            self.client.create_payload_index(
                collection_name=self.collection,
                field_name="doc_id",
                field_schema=models.PayloadSchemaType.KEYWORD,
            )

    def recreate(self) -> None:
        """Drop and rebuild. Used by the eval harness between runs."""
        if self.client.collection_exists(self.collection):
            self.client.delete_collection(self.collection)
        self.ensure_collection()

    # --- writes -------------------------------------------------------------

    def upsert(self, chunks: list[Any], vectors: np.ndarray) -> int:
        """Write chunk vectors and payloads. `chunks` are ingest.model.Chunk."""
        from qdrant_client import models  # noqa: PLC0415

        if not chunks:
            return 0
        self.ensure_collection()
        points = [
            models.PointStruct(
                id=chunk.id,
                vector=vectors[i].tolist(),
                payload=chunk.to_payload(),
            )
            for i, chunk in enumerate(chunks)
        ]
        # Batched so a large document does not build one enormous request.
        for start in range(0, len(points), 256):
            self.client.upsert(
                collection_name=self.collection,
                points=points[start : start + 256],
                wait=True,
            )
        return len(points)

    def delete_document(self, doc_id: str) -> None:
        from qdrant_client import models  # noqa: PLC0415

        if not self.client.collection_exists(self.collection):
            return
        self.client.delete(
            collection_name=self.collection,
            points_selector=models.FilterSelector(
                filter=models.Filter(
                    must=[
                        models.FieldCondition(
                            key="doc_id", match=models.MatchValue(value=doc_id)
                        )
                    ]
                )
            ),
            wait=True,
        )

    # --- reads --------------------------------------------------------------

    def search(self, vector: np.ndarray, limit: int | None = None) -> list[dict]:
        """Dense nearest neighbours as [{id, score, payload}], best first."""
        limit = limit or cfg.DENSE_TOP
        if not self.client.collection_exists(self.collection):
            return []
        response = self.client.query_points(
            collection_name=self.collection,
            query=vector.tolist(),
            limit=limit,
            with_payload=True,
        )
        return [
            {"id": str(point.id), "score": float(point.score), "payload": point.payload or {}}
            for point in response.points
        ]

    def count(self) -> int:
        if not self.client.collection_exists(self.collection):
            return 0
        return int(self.client.count(self.collection, exact=True).count)

    def healthy(self) -> bool:
        try:
            self.client.get_collections()
            return True
        except Exception:  # noqa: BLE001 - health must never raise
            return False


def get_store() -> VectorStore:
    """Process-wide store, created on first use."""
    global _store
    if _store is not None:
        return _store
    with _lock:
        if _store is None:
            _store = VectorStore()
    return _store


def reset() -> None:
    """Close and drop the store. Tests use this between fixtures."""
    global _store
    if _store is not None:
        try:
            _store.client.close()
        except Exception:  # noqa: BLE001
            pass
    _store = None
