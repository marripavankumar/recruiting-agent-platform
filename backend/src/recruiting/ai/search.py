"""Embeddings, vector index and semantic search (plan section 5.4; Q4 vector DB adapters).

`SemanticSearch` indexes JDs and resumes and finds the closest ones. The embedder and the vector
index are adapters:

- `HashingEmbedder`: lexical (words and canonical skills), no model; tests and quick local runs.
- `LocalModelEmbedder`: an open-source sentence-embedding model run on the CPU inside the
  container (fastembed, ONNX). Text never leaves the machine, so no new data processor (G5).
- `HybridEmbedder`: the model for meaning plus the skill dictionary, so "Allen-Bradley" and
  "Rockwell" still count as the same skill. The default for the Podman stack.

`QdrantIndex` is the default store (open source, self-hosted); `InMemoryIndex` serves tests.
Each embedder writes to its own collections (`tag`), so switching models never mixes vectors of
different sizes; `recruiting.reindex` fills the new collections.
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
    # Collection suffix: vectors of different embedders never share a collection. "" = none.
    tag: str
    # Cosine of two unrelated texts and of two close matches (calibration for `relatedness`).
    related: tuple[float, float]

    def embed(self, texts: Sequence[str]) -> list[Vector]: ...


def _normalise(vector: Sequence[float]) -> Vector:
    norm = math.sqrt(sum(v * v for v in vector)) or 1.0
    return [float(v) / norm for v in vector]


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))  # vectors are unit length


def relatedness(embedder: Embedder, a: Sequence[float], b: Sequence[float]) -> float:
    """0..1: how related two embedded texts are, on the same scale whatever the embedder
    (0 = no more alike than unrelated texts, 1 = a close match)."""
    low, high = getattr(embedder, "related", (0.0, 0.5))
    return max(0.0, min((cosine(a, b) - low) / (high - low), 1.0))


class HashingEmbedder:
    """Feature hashing over words and canonical skills, L2-normalised.

    Lexical, not semantic: good enough to exercise search end to end without a model. Canonical
    skills get extra weight so "Allen-Bradley" and "Rockwell" land on the same feature.
    """

    tag = ""  # the original collections
    related = (0.0, 0.5)  # lexical cosine rarely exceeds ~0.5 even for close matches

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
            vectors.append(_normalise(vector))
        return vectors


# Sizes of the models we have checked, so collections can be created before the model loads,
# and their calibration (`related`), measured on engineering JD and resume pairs.
KNOWN_MODELS: dict[str, tuple[int, tuple[float, float]]] = {
    "BAAI/bge-small-en-v1.5": (384, (0.55, 0.85)),
}
DEFAULT_MODEL = "BAAI/bge-small-en-v1.5"  # MIT licence, about 130 MB, 512-token input


class LocalModelEmbedder:
    """A sentence-embedding model run locally on the CPU (fastembed, ONNX runtime; no PyTorch).

    The default, BAAI/bge-small-en-v1.5, places texts by meaning: "PLC programming for
    packaging lines" lands near "Allen-Bradley ControlLogix automation" with few words in
    common. The model files come from `cache_dir` (the container image has them, so nothing is
    downloaded at run time). It loads on first use; long texts are cut at 512 tokens.
    """

    def __init__(
        self, model: str = DEFAULT_MODEL, cache_dir: str | None = None, threads: int | None = None
    ) -> None:
        self.model, self._cache_dir, self._threads = model, cache_dir, threads
        self._model: Any = None
        known = KNOWN_MODELS.get(model)
        self._dimensions = known[0] if known else None
        self.related = known[1] if known else (0.5, 0.85)
        self.tag = re.sub(r"[^a-z0-9]+", "_", model.split("/")[-1].lower()).strip("_")

    def _load(self) -> Any:
        if self._model is None:
            try:
                from fastembed import TextEmbedding
            except ImportError as exc:  # the "embeddings" extra is not installed
                raise RuntimeError(
                    "RAP_EMBEDDER=local needs the embeddings extra: uv sync --extra embeddings"
                ) from exc
            self._model = TextEmbedding(
                self.model, cache_dir=self._cache_dir, threads=self._threads
            )
        return self._model

    @property
    def dimensions(self) -> int:
        if self._dimensions is None:
            self._dimensions = len(self.embed(["dimensions"])[0])
        return self._dimensions

    def embed(self, texts: Sequence[str]) -> list[Vector]:
        if not texts:
            return []
        return [_normalise(v.tolist()) for v in self._load().embed(list(texts))]


class HybridEmbedder:
    """Model vector and skill-hash vector side by side, weighted so that
    cosine = w * model cosine + (1 - w) * lexical cosine. The model finds related experience
    worded differently; the lexical part keeps exact skill and tool matches (SolidWorks, PPAP,
    ControlLogix) counting, which small models tend to blur."""

    def __init__(
        self, semantic: Embedder, lexical: Embedder | None = None, semantic_weight: float = 0.7
    ) -> None:
        if not 0 < semantic_weight <= 1:
            raise ValueError("semantic_weight must be in (0, 1]")
        self._semantic, self._lexical = semantic, lexical or HashingEmbedder()
        self._w = semantic_weight
        self.tag = f"{semantic.tag}_h{round(semantic_weight * 100)}"
        (s_low, s_high), (l_low, l_high) = semantic.related, self._lexical.related
        w = semantic_weight
        self.related = (w * s_low + (1 - w) * l_low, w * s_high + (1 - w) * l_high)

    @property
    def dimensions(self) -> int:
        return self._semantic.dimensions + self._lexical.dimensions

    def embed(self, texts: Sequence[str]) -> list[Vector]:
        a, b = math.sqrt(self._w), math.sqrt(1 - self._w)
        semantic, lexical = self._semantic.embed(texts), self._lexical.embed(texts)
        return [
            [a * x for x in s] + [b * y for y in lex]
            for s, lex in zip(semantic, lexical, strict=True)
        ]


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

    def count(self, collection: str) -> int: ...


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

    def count(self, collection: str) -> int:
        return len(self._items.get(collection, {}))

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

    def count(self, collection: str) -> int:
        response = self._http.get(f"/collections/{collection}")
        if response.status_code == 404:
            return 0
        response.raise_for_status()
        return int(response.json()["result"].get("points_count") or 0)

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

    @property
    def embedder(self) -> Embedder:
        return self._embedder

    def _collection(self, kind: str) -> str:
        tag = getattr(self._embedder, "tag", "")
        name = f"{self._prefix}_{kind}" + (f"__{tag}" if tag else "")
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

    def count(self, kind: str) -> int:
        return self._index.count(self._collection(kind))
