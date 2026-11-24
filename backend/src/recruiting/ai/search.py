"""Embeddings, vector index and semantic search (plan section 5.4; Q4 vector DB adapters).

`SemanticSearch` indexes JDs and resumes and finds the closest ones. The embedder and the vector
index are adapters: `HashingEmbedder` is a local, lexical stand-in for development and tests; a
provider embedding model replaces it in production. `QdrantIndex` is the default store (open
source, self-hosted); `InMemoryIndex` serves tests.
"""

from __future__ import annotations

import hashlib
import math
import re
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from recruiting.ai.skills import extract_skills

Vector = list[float]


class Embedder(Protocol):
    dimensions: int

    def embed(self, texts: Sequence[str]) -> list[Vector]: ...


class HashingEmbedder:
    """Feature hashing over words and canonical skills, L2-normalised.

    Lexical, not semantic: good enough to exercise search end to end without a model. Canonical
    skills get extra weight so "Allen-Bradley" and "Rockwell" land on the same feature.
    """

    def __init__(self, dimensions: int = 512) -> None:
        self.dimensions = dimensions

    def _bucket(self, token: str) -> int:
        return (
            int.from_bytes(hashlib.blake2b(token.encode(), digest_size=8).digest(), "big")
            % self.dimensions
        )

    def embed(self, texts: Sequence[str]) -> list[Vector]:
        vectors = []
        for text in texts:
            vector = [0.0] * self.dimensions
            for word in re.findall(r"[a-z0-9+#&/-]{2,}", text.lower()):
                vector[self._bucket(f"w:{word}")] += 1.0
            for match in extract_skills(text):
                vector[self._bucket(f"s:{match.skill}")] += 3.0
            norm = math.sqrt(sum(v * v for v in vector)) or 1.0
            vectors.append([v / norm for v in vector])
        return vectors


@dataclass(frozen=True)
class Hit:
    id: str
    score: float
    payload: dict[str, Any]


class VectorIndex(Protocol):
    def ensure_collection(self, collection: str, dimensions: int) -> None: ...

    def upsert(
        self, collection: str, item_id: str, vector: Vector, payload: Mapping[str, Any]
    ) -> None: ...

    def search(
        self, collection: str, vector: Vector, limit: int, where: Mapping[str, Any] | None = None
    ) -> list[Hit]: ...

    def delete(self, collection: str, item_id: str) -> None: ...


class InMemoryIndex:
    def __init__(self) -> None:
        self._items: dict[str, dict[str, tuple[Vector, dict[str, Any]]]] = {}

    def ensure_collection(self, collection: str, dimensions: int) -> None:
        self._items.setdefault(collection, {})

    def upsert(
        self, collection: str, item_id: str, vector: Vector, payload: Mapping[str, Any]
    ) -> None:
        self._items.setdefault(collection, {})[item_id] = (vector, dict(payload))

    def delete(self, collection: str, item_id: str) -> None:
        self._items.get(collection, {}).pop(item_id, None)

    def search(
        self, collection: str, vector: Vector, limit: int, where: Mapping[str, Any] | None = None
    ) -> list[Hit]:
        hits = [
            Hit(item_id, sum(a * b for a, b in zip(vector, stored, strict=True)), payload)
            for item_id, (stored, payload) in self._items.get(collection, {}).items()
            if all(payload.get(k) == v for k, v in (where or {}).items())
        ]
        return sorted(hits, key=lambda h: h.score, reverse=True)[:limit]


class QdrantIndex:
    """Qdrant over its REST API. Point ids must be UUIDs, so ours are mapped with uuid5 and the
    original id is kept in the payload."""

    _NAMESPACE = uuid.UUID("6f1c3c3e-2a43-4b8e-9a51-6a0f3b2d9c11")

    def __init__(
        self, base_url: str, api_key: str | None = None, client: httpx.Client | None = None
    ) -> None:
        headers = {"api-key": api_key} if api_key else {}
        self._http = client or httpx.Client(base_url=base_url, headers=headers, timeout=30)

    def _point_id(self, item_id: str) -> str:
        return str(uuid.uuid5(self._NAMESPACE, item_id))

    def ensure_collection(self, collection: str, dimensions: int) -> None:
        if self._http.get(f"/collections/{collection}").status_code == 200:
            return
        self._http.put(
            f"/collections/{collection}",
            json={"vectors": {"size": dimensions, "distance": "Cosine"}},
        ).raise_for_status()

    def upsert(
        self, collection: str, item_id: str, vector: Vector, payload: Mapping[str, Any]
    ) -> None:
        point = {
            "id": self._point_id(item_id),
            "vector": vector,
            "payload": {**payload, "_id": item_id},
        }
        self._http.put(
            f"/collections/{collection}/points", params={"wait": "true"}, json={"points": [point]}
        ).raise_for_status()

    def search(
        self, collection: str, vector: Vector, limit: int, where: Mapping[str, Any] | None = None
    ) -> list[Hit]:
        body: dict[str, Any] = {"vector": vector, "limit": limit, "with_payload": True}
        if where:
            body["filter"] = {"must": [{"key": k, "match": {"value": v}} for k, v in where.items()]}
        response = self._http.post(f"/collections/{collection}/points/search", json=body)
        response.raise_for_status()
        hits = []
        for point in response.json()["result"]:
            payload = dict(point.get("payload") or {})
            hits.append(Hit(payload.pop("_id", str(point["id"])), point["score"], payload))
        return hits

    def delete(self, collection: str, item_id: str) -> None:
        self._http.post(
            f"/collections/{collection}/points/delete",
            params={"wait": "true"},
            json={"points": [self._point_id(item_id)]},
        ).raise_for_status()


class SemanticSearch:
    """Index and search texts by kind ("job", "candidate", ...), one collection per kind."""

    def __init__(self, embedder: Embedder, index: VectorIndex, prefix: str = "rap") -> None:
        self._embedder = embedder
        self._index = index
        self._prefix = prefix
        self._ready: set[str] = set()

    def _collection(self, kind: str) -> str:
        name = f"{self._prefix}_{kind}"
        if name not in self._ready:
            self._index.ensure_collection(name, self._embedder.dimensions)
            self._ready.add(name)
        return name

    def index(
        self, kind: str, item_id: str, text: str, payload: Mapping[str, Any] | None = None
    ) -> None:
        [vector] = self._embedder.embed([text])
        self._index.upsert(self._collection(kind), item_id, vector, payload or {})

    def search(
        self, kind: str, query: str, limit: int = 20, where: Mapping[str, Any] | None = None
    ) -> list[Hit]:
        [vector] = self._embedder.embed([query])
        return self._index.search(self._collection(kind), vector, limit, where)

    def remove(self, kind: str, item_id: str) -> None:
        self._index.delete(self._collection(kind), item_id)
