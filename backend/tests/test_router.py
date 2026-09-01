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
    lines = (REPO_ROOT / "config" / "router_trainset.jsonl").read_text().splitlines()
    assert len([l for l in lines if l.strip()]) == 200


def test_heldout_accuracy_reported_and_sane(router):
    m = router.metrics
    assert m["train_size"] == 160 and m["test_size"] == 40
    # Two numbers, both honest: fine-grained task label, and whether the
    # confusion would have changed which MODEL served the prompt (the only
    # confusion that costs anything). Current TF-IDF baseline: 0.775 / 0.975.
    assert m["tfidf_accuracy"] >= 0.7
    assert m["model_choice_accuracy"] >= 0.9


def test_image_attachment_is_hard_rule(router):
    att = Attachment(filename="gauge.jpg", mime="image/jpeg", size_bytes=1, path="x")
    d = router.decide("write a python script to sort numbers", [att], None)
    assert d.task_type == "vision"
    assert d.confidence == 1.0
    assert "hard rule" in d.reason
    assert d.model_id == "qwen2.5-vl-7b"  # only model with the capability


def test_code_routes_to_coder(router):
    d = router.decide("write a python function that parses a csv and plots the trend",
                      loaded_id=None, attachments=[])
    assert d.task_type == "code"
    assert d.model_id == "qwen3-coder-8b"


def test_document_routes_to_vl(router):
    d = router.decide("summarize the attached standard operating procedure section",
                      loaded_id=None, attachments=[])
    assert d.task_type == "document"
    assert d.model_id == "qwen2.5-vl-7b"


def test_low_confidence_falls_back_to_default(router, registry):
    d = router.decide("xyzzy plugh frobnicate the wumpus", [], None)
    if d.confidence < registry.router.min_confidence:
        assert d.model_id == registry.default.id
        assert "below" in d.reason


def test_switch_penalty_keeps_incumbent(router, registry):
    """A capable resident model is kept unless the challenger clears
    min_confidence + switch_penalty -- an 8 s reload has to be earned."""
    d = router.decide("xyzzy general question about things", [],
                      loaded_id="qwen2.5-vl-7b")
    threshold = registry.router.min_confidence + registry.router.switch_penalty
    if d.task_type != "vision" and d.confidence < threshold:
        assert d.model_id == "qwen2.5-vl-7b"


def test_decision_is_explainable(router):
    d = router.decide("compute the average corrosion rate from these readings", [], None)
    assert d.reason and d.task_type in ("data", "code")
    assert 0.0 <= d.confidence <= 1.0
