"""User-selectable model: the override path, its refusals, and what it leaves alone.

The router is the real one over the real trainset, as in test_router.py --
ragsvc is not running here so the TF-IDF fallback trains, which is the degraded
mode this has to survive anyway.

Model ids are derived from config/models.yaml throughout. Adding a model there
is meant to be a YAML edit; a test that hardcodes "qwen2.5-coder-7b" turns that
edit into a code change, which is exactly what the registry exists to prevent.
"""

import pytest

from contracts import Attachment, ChatRequest
from llm.manager import ModelRegistry
from llm.router import ModelChoiceError, Router

from conftest import REPO_ROOT

CODE_PROMPT = "write a python function that parses a csv of thickness readings"
VAGUE_PROMPT = "hello"


@pytest.fixture(scope="module")
def registry():
    return ModelRegistry(str(REPO_ROOT / "config" / "models.yaml"))


@pytest.fixture(scope="module")
def router(registry, tmp_path_factory):
    r = Router(registry, "http://localhost:1",  # nothing listens: forces TF-IDF
               str(REPO_ROOT / "config" / "router_trainset.jsonl"),
               str(tmp_path_factory.mktemp("router-override")))
    r.prepare()
    return r


def only_with(registry, capability: str) -> str:
    capable = registry.with_capability(capability)
    assert capable, f"no model in config/models.yaml advertises {capability!r}"
    return min(capable, key=lambda m: m.vram_mb).id


def without(registry, capability: str) -> str:
    lacking = [m for m in registry.models if capability not in m.capabilities]
    assert lacking, f"every model advertises {capability!r}; cannot test refusal"
    return lacking[0].id


def image() -> Attachment:
    return Attachment(filename="gauge.jpg", mime="image/jpeg", size_bytes=1, path="x")


# --- honoured -----------------------------------------------------------------


def test_user_choice_wins_over_the_router(router, registry):
    """A code model, asked a code question, is chosen because the user said so."""
    chosen = only_with(registry, "code")
    decision = router.decide_override(chosen, CODE_PROMPT, [])

    assert decision.model_id == chosen
    assert decision.confidence == 1.0
    assert f"user selected {chosen}" in decision.reason
    assert decision.type == "router.decision"


def test_user_choice_arbitrates_between_two_capable_models(router, registry):
    """Where two models can both serve a task, the user's pick decides.

    Skips on the current config, which has two models with disjoint
    capabilities, so "capable but not the router's choice" cannot be
    constructed from it. Written against the registry rather than around it:
    the day a third model lands this starts testing instead of skipping, and
    that is more useful than a test pinned to today's YAML.

    The override-beats-the-router property is not untested meanwhile --
    test_an_unsure_classifier_does_not_veto_the_user covers it on the path
    where the router would have chosen the default.
    """
    for capability in ("general", "document", "data", "code", "vision"):
        capable = sorted(registry.with_capability(capability),
                         key=lambda m: m.vram_mb)
        if len(capable) >= 2:
            break
    else:
        pytest.skip("no capability is served by two models; nothing to arbitrate")

    router_would_pick = capable[0].id      # the rule is lowest vram_mb
    user_picks = capable[1].id
    prompt = f"a prompt classified as {capability}"
    decision = router.decide_override(user_picks, prompt, [])

    assert decision.model_id == user_picks != router_would_pick
    assert decision.confidence == 1.0


def test_a_capable_model_is_accepted_for_a_vision_turn(router, registry):
    chosen = only_with(registry, "vision")
    decision = router.decide_override(chosen, "what does this gauge read?", [image()])

    assert decision.model_id == chosen
    assert decision.task_type == "vision"
    assert decision.confidence == 1.0


def test_alternatives_list_the_other_capable_models(router, registry):
    chosen = only_with(registry, "code")
    decision = router.decide_override(chosen, CODE_PROMPT, [])
    assert chosen not in decision.alternatives


# --- refused ------------------------------------------------------------------


def test_unknown_model_id_is_rejected(router):
    with pytest.raises(KeyError) as excinfo:
        router.decide_override("gpt-9-turbo", CODE_PROMPT, [])
    # The registry names what it does know, so the error is actionable.
    assert "gpt-9-turbo" in str(excinfo.value)


def test_a_blind_model_is_refused_a_vision_turn(router, registry):
    """The hard rule. An image is attached; a text-only model cannot see it."""
    blind = without(registry, "vision")
    with pytest.raises(ModelChoiceError) as excinfo:
        router.decide_override(blind, "what does this gauge read?", [image()])

    error = excinfo.value
    assert error.reason == "incapable"
    assert error.model_id == blind
    message = str(error)
    assert "vision" in message
    # A refusal that does not say what to pick instead is a dead end.
    assert "Try " in message or "No configured model" in message


def test_a_confident_task_mismatch_is_refused(router, registry):
    """Refused only when the classifier is actually sure -- see the next test."""
    no_code = without(registry, "code")
    task, confidence = router.required_task(CODE_PROMPT, [])
    if not (task == "code" and confidence >= registry.router.min_confidence):
        pytest.skip(f"classifier is not confident enough on this prompt "
                    f"({task} @ {confidence:.2f}) to exercise a binding refusal")

    with pytest.raises(ModelChoiceError):
        router.decide_override(no_code, CODE_PROMPT, [])


def test_an_unsure_classifier_does_not_veto_the_user(router, registry):
    """Below min_confidence the router does not trust the label either.

    `decide` falls back to the default model rather than acting on a weak
    classification. Refusing a user's explicit pick on the strength of that
    same weak label would be stricter than the router is with itself.
    """
    task, confidence = router.required_task(VAGUE_PROMPT, [])
    if confidence >= registry.router.min_confidence:
        pytest.skip(f"'{VAGUE_PROMPT}' classifies confidently ({confidence:.2f})")

    for spec in registry.models:  # every model, capable or not, is allowed
        decision = router.decide_override(spec.id, VAGUE_PROMPT, [])
        assert decision.model_id == spec.id
        assert decision.confidence == 1.0


# --- unchanged when absent ----------------------------------------------------


def test_the_router_is_untouched_when_no_model_is_chosen(router, registry):
    """Regression guard: no override in play means the old path, exactly."""
    for prompt in (CODE_PROMPT, VAGUE_PROMPT, "summarise the inspection report"):
        decision = router.decide(prompt, [], None)
        assert "user selected" not in decision.reason
        assert decision.model_id in {m.id for m in registry.models}
        # The router's own confidence is a classifier score. Only an override
        # is allowed to assert certainty.
        assert decision.confidence != 1.0 or decision.task_type == "vision"


def test_chat_request_without_model_id_still_validates():
    """The contract field is optional; an old client sends nothing and works."""
    assert ChatRequest(message="hello").model_id is None


# --- the HTTP surface the SPA actually talks to -------------------------------
#
# main.py builds its globals in the startup event, which starts a model manager
# and probes llama-server. These tests want neither, so they import the app and
# inject a real router over the real registry plus a stub manager. TestClient is
# used without `with`, so no lifespan runs.


class _StubManager:
    """Just enough manager for the endpoints under test."""

    def __init__(self, registry, loaded_id=None):
        self.registry = registry
        self.loaded_id = loaded_id


@pytest.fixture()
def client(router, registry, store, monkeypatch):
    from fastapi.testclient import TestClient

    import main
    from audit.logger import AuditLog

    monkeypatch.setattr(main, "router", router, raising=False)
    monkeypatch.setattr(main, "manager", _StubManager(registry, loaded_id=None),
                        raising=False)
    monkeypatch.setattr(main, "store", store, raising=False)
    monkeypatch.setattr(main, "audit", AuditLog(store), raising=False)
    monkeypatch.setattr(main, "_session_model", {}, raising=False)
    return TestClient(main.app)


def test_http_rejects_an_unknown_model_id(client, registry):
    response = client.post("/api/chat", json={"message": CODE_PROMPT,
                                              "model_id": "gpt-9-turbo"})
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "gpt-9-turbo" in detail
    # It lists what you could have asked for instead.
    for spec in registry.models:
        assert spec.id in detail


def test_http_rejects_a_capability_mismatch(client, registry):
    blind = without(registry, "vision")
    response = client.post("/api/chat", json={
        "message": "what does this gauge read?",
        "model_id": blind,
        "attachments": [{"filename": "g.jpg", "mime": "image/jpeg",
                         "size_bytes": 1, "path": "x"}],
    })
    assert response.status_code == 400
    assert "vision" in response.json()["detail"]


def test_a_rejected_turn_leaves_nothing_behind(client, registry, store):
    """A refused request must not create a session or record a user message."""
    before = len(store.list_sessions())
    client.post("/api/chat", json={"message": CODE_PROMPT, "model_id": "nope",
                                   "session_id": "ghost-session"})
    assert len(store.list_sessions()) == before
    assert store.get_session("ghost-session") is None


def test_models_endpoint_carries_what_a_picker_needs(client, registry):
    response = client.get("/api/models")
    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == len(registry.models)

    for entry in payload:
        assert {"id", "capabilities", "context", "vram_mb", "loaded",
                "display_name", "description"} <= set(entry)
        assert entry["display_name"], "a picker cannot show an empty name"
        assert entry["description"].endswith("."), entry["description"]
        # The derived name is for humans; it must not just echo the slug.
        assert entry["display_name"] != entry["id"]
        assert entry["vram_mb"] > 0 and entry["context"] > 0


def test_session_detail_reports_the_pinned_model(client, registry, store):
    import main

    chosen = only_with(registry, "code")
    store.ensure_session("s-pinned", title="t")
    main._pin_session_model("s-pinned", chosen)

    response = client.get("/api/sessions/s-pinned")
    assert response.status_code == 200
    assert response.json()["model_id"] == chosen


def test_a_pin_survives_losing_the_in_memory_cache(client, registry, store):
    """The pin is mirrored to the audit log, so a restart does not forget it."""
    import main

    chosen = only_with(registry, "code")
    store.ensure_session("s-restart", title="t")
    main._pin_session_model("s-restart", chosen)

    main._session_model.clear()  # what a process restart looks like
    assert main._session_model_id("s-restart") == chosen


def test_an_unpinned_session_reports_no_model(client, store):
    store.ensure_session("s-free", title="t")
    response = client.get("/api/sessions/s-free")
    assert response.status_code == 200
    assert response.json()["model_id"] is None
