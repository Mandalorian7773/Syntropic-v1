"""Backend gateway. Owner: person 3.

Wires the pipeline the scaffold promised: router -> model manager -> agent
loop -> tools, with every event mirrored into the audit log and streamed to
the SPA as SSE. Request/response shapes come from contracts and only from
contracts.

Startup order matters and is deliberate:
  1. air-gap self-check   (AIRGAP_ENFORCE=1 refuses to serve on any failure)
  2. store + audit        (evidence trail first)
  3. model manager        (establish what is resident)
  4. tool registry        (local four, then Person 2's over HTTP)
  5. router               (train/refresh classifiers, write metrics)
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import sys
import time
import uuid
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse

from contracts import (
    Attachment,
    CancelRequest,
    CancelResponse,
    ChatRequest,
    HealthResponse,
    ModelInfo,
    RouterDecision,
    SessionDetail,
    SessionStart,
    SessionStep,
    SessionSummary,
    Message,
    NetworkStatus,
    to_sse,
)

from agent.loop import AgentLoop
from audit.logger import AuditLog
from audit.network import NetworkMonitor, startup_selfcheck
from db.store import Store
from llm.client import LLMClient
from llm.manager import ModelManager, ModelRegistry
from llm.router import ModelChoiceError, Router
from sse import Cancels, stream_events
from tools.files import ListFilesTool, ReadFileTool, WriteFileTool, safe_path
from tools.registry import Registry
from tools.sandbox import ExecutePythonTool


def _repo_root() -> Path:
    """Walk up until config/models.yaml appears. Works from a checkout
    (backend/ beside config/) and from the image (/app/backend beside /app/config)."""
    here = Path(__file__).resolve().parent
    for candidate in (here, *here.parents):
        if (candidate / "config" / "models.yaml").is_file():
            return candidate
    raise FileNotFoundError("config/models.yaml not found above " + str(here))


ROOT = _repo_root()
MODEL_ENDPOINT = os.getenv("MODEL_ENDPOINT", "http://localhost:8080")
RAG_ENDPOINT = os.getenv("RAG_ENDPOINT", "http://localhost:8001")
QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333")
WORKSPACE_DIR = os.getenv("WORKSPACE_DIR", str(ROOT / "workspace"))
ARTIFACTS_DIR = os.getenv("ARTIFACTS_DIR", str(ROOT / "artifacts"))
MODELS_DIR = os.getenv("MODELS_DIR", str(ROOT / "models"))
DB_PATH = os.getenv("DB_PATH", str(ROOT / "data" / "workbench.db"))
DATA_DIR = str(Path(DB_PATH).parent)
AIRGAP_ENFORCE = os.getenv("AIRGAP_ENFORCE", "0") == "1"

app = FastAPI(title="SIH26117 backend", version="0.1.0")

store: Store
audit: AuditLog
manager: ModelManager
llm: LLMClient
registry: Registry
router: Router
loop: AgentLoop
monitor: NetworkMonitor
cancels = Cancels()


@app.on_event("startup")
async def startup() -> None:
    global store, audit, manager, llm, registry, router, loop, monitor

    checks = startup_selfcheck()
    # Inside the compose container the isolation comes from the internal:true
    # network and nft is not installed there; the nftables assertion is only
    # fatal where the host layer is expected (AIRGAP_REQUIRE_NFT=1, bare metal).
    require_nft = os.getenv("AIRGAP_REQUIRE_NFT", "0") == "1"
    failed = [c for c in checks if not c["passed"]
              and (c["check"] != "nftables_rules_loaded" or require_nft)]
    if AIRGAP_ENFORCE and failed:
        for c in failed:
            print(f"AIRGAP CHECK FAILED: {c['check']}: {c['detail']}", file=sys.stderr)
        # Refusing to start is the feature: an un-air-gapped sovereign demo
        # is a false claim with a UI.
        raise SystemExit("air-gap self-check failed and AIRGAP_ENFORCE=1")

    Path(WORKSPACE_DIR).mkdir(parents=True, exist_ok=True)
    Path(ARTIFACTS_DIR).mkdir(parents=True, exist_ok=True)
    store = Store(DB_PATH)
    audit = AuditLog(store)
    audit.record("startup.airgap_selfcheck",
                 {"enforced": AIRGAP_ENFORCE, "checks": checks})
    monitor = NetworkMonitor()

    model_registry = ModelRegistry(str(ROOT / "config" / "models.yaml"))
    manager = ModelManager(
        model_registry, MODEL_ENDPOINT, MODELS_DIR,
        estimate_load_s=store.estimate_load_s, record_load=store.record_load,
    )
    await manager.startup()
    llm = LLMClient(manager)

    registry = Registry()
    for tool in (ReadFileTool(), WriteFileTool(), ListFilesTool(), ExecutePythonTool()):
        registry.register(tool)
    remote = registry.register_remote(RAG_ENDPOINT)
    audit.record("startup.tools", {"registered": registry.names(), "remote": remote})

    router = Router(model_registry, RAG_ENDPOINT,
                    str(ROOT / "config" / "router_trainset.jsonl"), DATA_DIR)
    try:
        metrics = router.prepare()
        audit.record("startup.router", metrics)
    except Exception as exc:
        audit.record("startup.router", {"error": str(exc)})

    loop = AgentLoop(llm, registry, store, audit, WORKSPACE_DIR, ARTIFACTS_DIR)


@app.on_event("shutdown")
async def shutdown() -> None:
    # The client keeps one pooled connection to llama-server across agent
    # steps; close it before the server it points at goes away.
    await llm.aclose()
    manager.shutdown()
    store.close()


# --- chat ---------------------------------------------------------------------


def _decide(message: str, attachments: list[Attachment]) -> RouterDecision:
    try:
        return router.decide(message, attachments, manager.loaded_id)
    except Exception as exc:
        default = manager.registry.default
        return RouterDecision(
            model_id=default.id, task_type="general", confidence=0.0,
            reason=f"router unavailable ({type(exc).__name__}); using default model",
            alternatives=[],
        )


# --- user-chosen models -------------------------------------------------------
#
# A session stays on the model the user picked. This is held in memory and
# mirrored into the audit log, which is the only durable store reachable from
# this file: db/store.py belongs to another slice and has no set_model_id, so
# adding a `model_id` column is a request in the PR rather than an edit here.
# The audit log is append-only and already records every routing decision, so
# "the user pinned this session to model X" is a fact that belongs in it
# regardless; reading the latest one back is what survives a restart.
SESSION_MODEL_KIND = "session.model"
_session_model: dict[str, str] = {}


def _pin_session_model(session_id: str, model_id: str) -> None:
    if _session_model.get(session_id) == model_id:
        return
    _session_model[session_id] = model_id
    audit.record(SESSION_MODEL_KIND, {"model_id": model_id}, session_id)


def _session_model_id(session_id: str) -> str | None:
    """The model a session is pinned to, memory first, audit log second."""
    if session_id in _session_model:
        return _session_model[session_id]
    for row in reversed(audit.trail(session_id)):
        if row.get("kind") != SESSION_MODEL_KIND:
            continue
        try:
            model_id = json.loads(row["payload_json"]).get("model_id")
        except (ValueError, KeyError, TypeError):
            continue
        if model_id:
            _session_model[session_id] = model_id
            return model_id
    return None


def _resolve_model(req: ChatRequest) -> str | None:
    """The model this turn should run on, or None to let the router decide.

    An explicit `model_id` on the request wins and re-pins the session. With
    none given, a session already pinned stays pinned -- that is what "a
    conversation stays on one model" means, and it is the difference between a
    picker and a per-message toggle.
    """
    if req.model_id:
        return req.model_id
    if req.session_id:
        return _session_model_id(req.session_id)
    return None


def _user_content(message: str, attachments: list[Attachment]) -> str | list[dict]:
    """Plain string, unless an image attachment turns it into multimodal parts."""
    images = [a for a in attachments if a.mime.startswith("image/") and a.path]
    if not images:
        return message
    parts: list[dict] = [{"type": "text", "text": message}]
    for att in images:
        try:
            data = safe_path(WORKSPACE_DIR, att.path).read_bytes()
        except Exception:
            continue
        b64 = base64.b64encode(data).decode()
        parts.append({"type": "image_url",
                      "image_url": {"url": f"data:{att.mime};base64,{b64}"}})
    return parts


@app.post("/api/chat")
async def chat(req: ChatRequest) -> StreamingResponse:
    # Validate a chosen model before anything is written down. A rejected turn
    # should leave no session, no user message and no audit entry behind: the
    # request never happened as far as the conversation is concerned.
    chosen = _resolve_model(req)
    override: RouterDecision | None = None
    if chosen is not None:
        pinned = chosen != req.model_id
        try:
            override = router.decide_override(chosen, req.message, req.attachments)
        except KeyError:
            known = ", ".join(sorted(m.id for m in manager.registry.models))
            raise HTTPException(
                400, f"unknown model_id {chosen!r}. Available models: {known}."
            ) from None
        except ModelChoiceError as exc:
            detail = str(exc)
            if pinned:
                detail += (
                    f" This session is pinned to {chosen!r}; send model_id with "
                    f"a capable model to move it, or null to let the router choose."
                )
            raise HTTPException(400, detail) from None

    session_id = req.session_id or str(uuid.uuid4())
    store.ensure_session(session_id, title=req.message)
    if req.model_id:
        _pin_session_model(session_id, req.model_id)
    store.add_message(session_id, "user", req.message)
    audit.record("prompt", {
        "message": req.message,
        "attachments": [a.model_dump() for a in req.attachments],
    }, session_id)
    cancel = cancels.register(session_id)

    async def events():
        start = SessionStart(session_id=session_id, ts=int(time.time()))
        audit.event(start, session_id)
        yield start

        # The router still speaks even when it did not choose, so the trace
        # panel always shows why this turn is on this model.
        decision = override if override is not None else _decide(req.message, req.attachments)
        audit.event(decision, session_id)
        store.set_task_type(session_id, decision.task_type)
        yield decision

        spec = manager.registry.get(decision.model_id)
        async for event in loop.run(
            session_id,
            _user_content(req.message, req.attachments),
            decision.model_id,
            spec.context,
            cancel,
        ):
            yield event
        cancels.clear(session_id)

    return StreamingResponse(
        stream_events(events()),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/chat/cancel", response_model=CancelResponse)
async def chat_cancel(req: CancelRequest) -> CancelResponse:
    hit = cancels.cancel(req.session_id)
    audit.record("chat.cancel", {"found": hit}, req.session_id)
    return CancelResponse(ok=hit)


@app.post("/api/upload", response_model=Attachment)
async def upload(file: UploadFile) -> Attachment:
    """Stage a chat attachment into the workspace; the returned Attachment
    (with its server-side path) goes back in ChatRequest.attachments."""
    name = Path(file.filename or "upload.bin").name
    rel = f"uploads/{uuid.uuid4().hex[:8]}-{name}"
    target = safe_path(WORKSPACE_DIR, rel)
    target.parent.mkdir(parents=True, exist_ok=True)
    data = await file.read()
    target.write_bytes(data)
    return Attachment(
        filename=name,
        mime=file.content_type or "application/octet-stream",
        size_bytes=len(data),
        path=rel,
    )


# --- models / sessions / status -----------------------------------------------


# These three return BARE ARRAYS, not envelopes -- the contract is explicit
# about it and frontend/src/api/rest.ts consumes them as ModelInfo[] etc.
# Presentation for the model picker, derived from the registry.
#
# Derived rather than configured because config/models.yaml belongs to another
# slice and has no display_name or description keys. `getattr` below picks them
# up the moment it does, so adding them is a YAML edit and not a code change --
# and that addition is the request in this PR. Until then a name like
# "qwen2.5-coder-7b" is turned into something a person can compare on a stage.
_NAME_TOKENS = {"vl": "VL", "moe": "MoE", "llava": "LLaVA", "vlm": "VLM"}
_SIZE = re.compile(r"^\d+(?:\.\d+)?b$")

# Ordered by how much the capability distinguishes a model. "general" says
# almost nothing, so it comes last and is only used when nothing else applies.
_CAPABILITY_BLURB = [
    ("vision", "reads images and scanned pages"),
    ("code", "writes and reviews code"),
    ("document", "answers questions from your documents"),
    ("data", "works through tables and numbers"),
    # `fast` is a picker-only capability: the router never selects it, a user
    # can. Measured against the 7B it reaches for tools twice as often, so the
    # blurb says what it is good for rather than pretending it is a default.
    ("fast", "answers short general questions quickly on the least VRAM"),
    ("general", "general questions and drafting"),
]


def _display_name(spec) -> str:
    configured = getattr(spec, "display_name", "") or ""
    if configured:
        return configured
    words = []
    for token in spec.id.split("-"):
        if _SIZE.match(token.lower()):
            words.append(token.upper())
        elif token.lower() in _NAME_TOKENS:
            words.append(_NAME_TOKENS[token.lower()])
        else:
            words.append(token[:1].upper() + token[1:])
    return " ".join(words)


def _description(spec) -> str:
    configured = getattr(spec, "description", "") or ""
    if configured:
        return configured
    blurbs = [text for cap, text in _CAPABILITY_BLURB if cap in spec.capabilities]
    if not blurbs:
        return "No capabilities declared."
    # Two clauses at most: this is one line under a name in a dropdown, not a
    # datasheet. The capability chips next to it carry the complete list.
    picked = blurbs[:2]
    sentence = " and ".join(picked) if len(picked) == 2 else picked[0]
    return sentence[:1].upper() + sentence[1:] + "."


@app.get("/api/models", response_model=list[ModelInfo])
async def models() -> list[ModelInfo]:
    return [
        ModelInfo(
            id=m.id, capabilities=m.capabilities, context=m.context,
            vram_mb=m.vram_mb, loaded=(m.id == manager.loaded_id),
            display_name=_display_name(m), description=_description(m),
        )
        for m in manager.registry.models
    ]


@app.get("/api/sessions", response_model=list[SessionSummary])
async def sessions() -> list[SessionSummary]:
    return [
        SessionSummary(
            id=s["session_id"], title=s["title"],
            created_at=s["created_ts"], message_count=s["message_count"],
        )
        for s in store.list_sessions()
    ]


@app.get("/api/sessions/{session_id}", response_model=SessionDetail)
async def session_detail(session_id: str) -> SessionDetail:
    s = store.get_session(session_id)
    if s is None:
        raise HTTPException(404, "unknown session")
    return SessionDetail(
        id=s["session_id"],
        messages=[Message(**m) for m in s["messages"]],
        # Replayed tool calls so reopening a session rehydrates the trace panel
        # instead of showing an empty instrument column.
        steps=[
            SessionStep(
                step=row["step"], tool=row["name"],
                args=json.loads(row["args_json"]),
                ok=bool(row["ok"]), summary=row["summary"] or "",
                duration_ms=row["duration_ms"] or 0,
            )
            for row in store.get_steps(session_id)
            if row["ok"] is not None  # skip calls that never returned
        ],
        # None when the router is choosing per turn, which is the default.
        model_id=_session_model_id(session_id),
    )


@app.get("/api/network/status", response_model=NetworkStatus)
async def network_status() -> NetworkStatus:
    # to_thread: on Windows the monitor shells out to PowerShell (cached, but
    # the first probe after the TTL still takes ~1 s), and this endpoint is
    # polled continuously by the panel. Same lesson as /api/health and
    # nvidia-smi -- a blocking probe here stalls somebody's token stream.
    return await asyncio.to_thread(monitor.status)


@app.get("/api/audit")
async def audit_trail(session_id: str | None = None, limit: int = 500) -> JSONResponse:
    """The evidence trail, raw. This is what goes on screen when a judge asks
    for proof; acceptance criterion 7 is a query over this."""
    return JSONResponse({"trail": audit.trail(session_id, limit)})


@app.get("/api/router/metrics")
async def router_metrics() -> JSONResponse:
    return JSONResponse(router.metrics or {"error": "router not trained"})


@app.get("/api/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    qdrant_ok = False
    try:
        async with httpx.AsyncClient(timeout=2) as client:
            qdrant_ok = (await client.get(f"{QDRANT_URL}/readyz")).status_code == 200
    except httpx.HTTPError:
        pass
    # to_thread, not a direct call: vram_free_mb() shells out to nvidia-smi.
    # Even cached, the first read after the TTL expires would block the event
    # loop -- and this handler is polled by every open frontend tab, so that
    # block lands in the middle of somebody's SSE token stream.
    vram_free = await asyncio.to_thread(manager.vram_free_mb)
    return HealthResponse(
        ok=True, model_loaded=manager.loaded_id,
        qdrant=qdrant_ok, vram_free_mb=vram_free,
    )


# --- artifacts + ragsvc proxy ---------------------------------------------------


@app.get("/api/artifacts/{artifact_id}")
async def artifact(artifact_id: str):
    row = store.get_artifact(artifact_id)
    if row and Path(row["path"]).is_file():
        return FileResponse(row["path"], media_type=row["mime"],
                            filename=row["filename"])
    # Not one of ours: Person 2's docx/xlsx generators register theirs in ragsvc.
    return await _proxy("GET", f"/artifacts/{artifact_id}")


async def _proxy(method: str, path: str, request: Request | None = None) -> Response:
    url = f"{RAG_ENDPOINT}{path}"
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            if request is not None:
                body = await request.body()
                upstream = await client.request(
                    method, url, content=body,
                    headers={k: v for k, v in request.headers.items()
                             if k.lower() in ("content-type", "content-length")},
                    params=dict(request.query_params),
                )
            else:
                upstream = await client.request(method, url)
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"ragsvc unreachable: {exc}")
    return Response(
        content=upstream.content, status_code=upstream.status_code,
        media_type=upstream.headers.get("content-type"),
    )


@app.api_route("/api/documents", methods=["GET", "POST"])
async def documents(request: Request) -> Response:
    upstream = await _proxy(request.method, "/documents", request)
    if request.method == "GET" and upstream.status_code == 404:
        # ragsvc has not built /documents yet. The contract obliges this
        # endpoint to return list[DocumentInfo], and "nothing is indexed" is
        # the truthful answer -- so serve that rather than 404 the frontend.
        # An unreachable ragsvc still raises 502 from _proxy; only a missing
        # endpoint degrades to empty.
        return JSONResponse([])
    return upstream


@app.post("/api/documents/upload")
async def documents_upload(request: Request) -> Response:
    # Multipart passes straight through to ragsvc; the SPA only ever talks to
    # this origin (frontend/src/api/rest.ts::uploadDocument).
    return await _proxy("POST", "/documents/upload", request)


@app.api_route("/api/documents/{doc_id}/reindex", methods=["POST"])
async def reindex(doc_id: str, request: Request) -> Response:
    return await _proxy("POST", f"/documents/{doc_id}/reindex", request)


@app.post("/api/search")
async def search(request: Request) -> Response:
    return await _proxy("POST", "/search", request)
