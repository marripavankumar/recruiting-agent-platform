"""Semantic search with a real embedding model (gap G5): the hybrid embedder's maths, separate
collections per embedder, the reindex after a model change, and, where the model is installed
(the container image), that it ranks matching JD and resume pairs above the rest."""

import importlib.util
import math
import os

import pytest

from recruiting.ai.search import (
    HashingEmbedder,
    HybridEmbedder,
    InMemoryIndex,
    LocalModelEmbedder,
    SemanticSearch,
    cosine,
    relatedness,
)
from recruiting.records.memory import InMemoryCandidates
from recruiting.records.models import Candidate


class FakeModel:
    """Stands in for the model: 3 dimensions, meaning given by keywords."""

    dimensions, tag, related = 3, "fake", (0.2, 0.9)

    def embed(self, texts):
        out = []
        for t in texts:
            v = [float("plc" in t or "automation" in t), float("quality" in t), 0.1]
            n = math.sqrt(sum(x * x for x in v))
            out.append([x / n for x in v])
        return out


def test_hybrid_cosine_is_the_weighted_mix_and_calibration_follows():
    model, lexical = FakeModel(), HashingEmbedder(64)
    hybrid = HybridEmbedder(model, lexical, semantic_weight=0.7)
    a, b = "plc automation lines", "automation and plc start-up"
    [ha, hb] = hybrid.embed([a, b])
    [ma, mb], [la, lb] = model.embed([a, b]), lexical.embed([a, b])
    assert len(ha) == hybrid.dimensions == 67
    assert math.isclose(cosine(ha, ha), 1.0, rel_tol=1e-9)  # still unit length
    assert math.isclose(cosine(ha, hb), 0.7 * cosine(ma, mb) + 0.3 * cosine(la, lb), rel_tol=1e-9)
    assert hybrid.related == pytest.approx((0.7 * 0.2, 0.7 * 0.9 + 0.3 * 0.5))
    assert hybrid.tag == "fake_h70"
    assert relatedness(hybrid, ha, ha) == 1.0
    assert relatedness(HashingEmbedder(), [1.0, 0.0], [0.0, 1.0]) == 0.0
    with pytest.raises(ValueError):
        HybridEmbedder(model, semantic_weight=0)


def test_each_embedder_has_its_own_collections_and_reindex_fills_a_new_one():
    from types import SimpleNamespace

    from recruiting.agents.resume_intake import index_candidate
    from recruiting.reindex import reindex

    index, candidates = InMemoryIndex(), InMemoryCandidates()
    old = SemanticSearch(HashingEmbedder(), index, prefix="rap_test")
    for i, skills in enumerate([["PLC", "HMI"], ["PPAP", "PFMEA"]]):
        c = candidates.save(Candidate(f"c{i}", f"Person {i}", skills=skills, source="manual"))
        index_candidate(old, c)
    assert old.count("candidate") == 2 and "rap_test_candidate" in index._items  # unchanged name

    new = SemanticSearch(HybridEmbedder(FakeModel()), index, prefix="rap_test")
    assert new.count("candidate") == 0  # a new model starts with its own, empty collection
    jobs = SimpleNamespace(list=lambda: [])
    platform = SimpleNamespace(search=new, candidates=candidates, jobs=jobs)
    assert reindex(platform, missing_only=True) == {"candidates": 2, "jobs": 0}
    assert "rap_test_candidate__fake_h70" in index._items and new.count("candidate") == 2
    assert reindex(platform, missing_only=True) == {"candidates": 0, "jobs": 0}  # nothing missing
    assert old.count("candidate") == 2  # the old vectors are left alone


def test_the_platform_uses_one_embedder_for_search_ranking_and_matching():
    from recruiting.api.platform import build_embedder
    from recruiting.config import Settings

    assert isinstance(build_embedder(Settings(_env_file=None)), HashingEmbedder)
    hybrid = build_embedder(Settings(embedder="local", embedding_semantic_weight=0.6,
                                     _env_file=None))  # fmt: skip
    assert isinstance(hybrid, HybridEmbedder) and hybrid.tag == "bge_small_en_v1_5_h60"
    assert hybrid.dimensions == 384 + 512  # known model: no load needed to create collections
    assert LocalModelEmbedder().related == (0.55, 0.85)


HAS_MODEL = importlib.util.find_spec("fastembed") is not None and (
    os.environ.get("RAP_EMBEDDING_CACHE_DIR") or os.environ.get("RAP_TEST_EMBEDDINGS")
)

JD = {
    "controls": "Controls Engineer: PLC programming for packaging lines, Allen-Bradley ControlLogix, FactoryTalk HMI, commissioning automated equipment.",
    "quality": "Supplier Quality Engineer: PPAP, APQP, PFMEA, supplier audits, ISO 13485 medical devices, corrective actions 8D.",
    "cae": "CAE Engineer crash simulation: LS-DYNA, ANSA meshing, vehicle crashworthiness, FMVSS load cases.",
    "design": "Mechanical Design Engineer: SolidWorks 3D modelling, GD&T drawings, sheet metal and plastic part design, DFM.",
}
CV = {
    "controls": "Automation engineer, 6 years Rockwell PLC and HMI programming, start-up of conveyor and filling machines.",
    "quality": "Quality engineer at a medical device supplier: production part approval, process FMEA, supplier corrective action, ISO 13485.",
    "cae": "Simulation engineer: finite element crash analysis of car bodies in LS-DYNA, pre-processing in ANSA, occupant safety.",
    "design": "Product design engineer creating CAD models in SOLIDWORKS, tolerance stack-ups, injection moulded parts, design for manufacture.",
}


@pytest.mark.skipif(not HAS_MODEL, reason="embedding model not installed (runs in the image)")
def test_the_real_model_finds_the_matching_resume_for_each_job():
    hybrid = HybridEmbedder(LocalModelEmbedder(cache_dir=os.environ.get("RAP_EMBEDDING_CACHE_DIR")))
    search = SemanticSearch(hybrid, InMemoryIndex(), prefix="t")
    for name, text in CV.items():
        search.index("candidate", name, text)
    for name, text in JD.items():
        [best, *_] = search.search("candidate", text, limit=4)
        assert best.id == name, (name, best)
    # Same meaning, different words: matching pairs score as close matches, others well below.
    vectors = {n: hybrid.embed([JD[n], CV[n]]) for n in JD}
    assert all(relatedness(hybrid, a, b) >= 0.8 for a, b in vectors.values())
    cross = [relatedness(hybrid, *hybrid.embed([JD[a], CV[b]])) for a in JD for b in CV if a != b]
    assert max(cross) < 0.5
