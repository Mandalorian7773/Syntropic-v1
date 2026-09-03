"""Router: real classifier over the real trainset, hard rules, thresholds.
ragsvc is not running here, so the TF-IDF fallback path is what trains --
which is exactly the degraded mode the router must survive."""

import pytest

from contracts import Attachment
from llm.manager import ModelRegistry
from llm.router import Router

from conftest import REPO_ROOT


@pytest.fixture(scope="module")
def registry():
    return ModelRegistry(str(REPO_ROOT / "config" / "models.yaml"))


@pytest.fixture(scope="module")
def router(registry, tmp_path_factory):
    r = Router(registry, "http://localhost:1",  # nothing listens: forces TF-IDF
               str(REPO_ROOT / "config" / "router_trainset.jsonl"),
               str(tmp_path_factory.mktemp("router")))
    r.prepare()
    return r


def test_trainset_shape():
    """B4: at least 200 labelled prompts and at least 40 per class.

    A minimum, not an exact count -- the set grows when a class turns out to be
    under-represented (equipment-tag questions were absent from `document`
    until every one of them routed as `general` at 0.33 confidence).
    """
    import collections
    import json
    lines = (REPO_ROOT / "config" / "router_trainset.jsonl").read_text(
        encoding="utf-8").splitlines()
    rows = [json.loads(l) for l in lines if l.strip()]
    assert len(rows) >= 200
    per_class = collections.Counter(r["task_type"] for r in rows)
    assert all(n >= 40 for n in per_class.values()), per_class


def test_heldout_accuracy_reported_and_sane(router):
    m = router.metrics
    # A fixed 40-row held-out set; everything else trains.
    assert m["test_size"] == 40 and m["train_size"] >= 160
    # Two numbers, both honest: fine-grained task label, and whether the
    # confusion would have changed which MODEL served the prompt (the only
    # confusion that costs anything). Current TF-IDF baseline: 0.775 / 0.975.
    assert m["tfidf_accuracy"] >= 0.7
    assert m["model_choice_accuracy"] >= 0.9


def cheapest_with(registry, capability: str) -> str:
    """The model the routing rule must pick: lowest vram_mb among the capable.
    Derived from the registry, never hardcoded -- adding a model is a YAML edit
    (acceptance criterion 1), and a test that pins model ids turns that edit
    into a code change."""
    capable = registry.with_capability(capability)
    assert capable, f"no model in config/models.yaml advertises {capability!r}"
    return min(capable, key=lambda m: m.vram_mb).id


def test_image_attachment_is_hard_rule(router, registry):
    att = Attachment(filename="gauge.jpg", mime="image/jpeg", size_bytes=1, path="x")
    d = router.decide("write a python script to sort numbers", [att], None)
    assert d.task_type == "vision"
    assert d.confidence == 1.0
    assert "hard rule" in d.reason
    # An unambiguously code-shaped prompt still routes to vision: the rule wins
    # over the classifier, which is the point of it being a hard rule.
    assert d.model_id == cheapest_with(registry, "vision")


def test_code_routes_to_coder(router, registry):
    d = router.decide("write a python function that parses a csv and plots the trend",
                      loaded_id=None, attachments=[])
    assert d.task_type == "code"
    assert d.model_id == cheapest_with(registry, "code")


def test_document_routes_to_document_model(router, registry):
    d = router.decide("summarize the attached standard operating procedure section",
                      loaded_id=None, attachments=[])
    assert d.task_type == "document"
    assert d.model_id == cheapest_with(registry, "document")


def test_low_confidence_falls_back_to_default(router, registry):
    d = router.decide("xyzzy plugh frobnicate the wumpus", [], None)
    if d.confidence < registry.router.min_confidence:
        assert d.model_id == registry.default.id
        assert "below" in d.reason


def test_switch_penalty_keeps_incumbent(router, registry):
    """A capable resident model is kept unless the challenger clears
    min_confidence + switch_penalty -- an 8 s reload has to be earned."""
    incumbent = registry.default.id
    d = router.decide("xyzzy general question about things", [], loaded_id=incumbent)
    threshold = registry.router.min_confidence + registry.router.switch_penalty
    if d.task_type != "vision" and d.confidence < threshold:
        assert d.model_id == incumbent


def test_decision_is_explainable(router):
    d = router.decide("compute the average corrosion rate from these readings", [], None)
    assert d.reason and d.task_type in ("data", "code")
    assert 0.0 <= d.confidence <= 1.0
