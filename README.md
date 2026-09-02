# SIH26117 — Sovereign On-Premise Agentic AI Workbench

Smart India Hackathon 2026, problem statement **SIH26117**, sponsored by
Mangalore Refinery and Petrochemicals Limited. An agentic AI workbench built on
open-weight multimodal models that runs entirely on one laptop with an 8 GB
GPU: documents are ingested and searched locally, an agent plans and calls
tools, code executes in a sandbox, and reports come out as real .docx and .xlsx
files. Nothing is sent anywhere, because there is nowhere to send it.

> ### Two rules that shape every decision in this repo
>
> **1. Nothing reaches the internet at runtime.** No CDN links, no remote
> fonts, no packages fetched at container start, no `npx` in a build script.
> Everything is vendored or installed at setup time from a local cache.
> `make airgap` proves it and exits nonzero if it cannot.
>
> **2. The demo runs on one machine.** Development happens across three
> laptops; the demo does not. The only difference between the two is `.env`.
> If you are changing code to switch environments, that is a bug.

---

## Ownership

| Person | Owns | Service | Port |
|---|---|---|---|
| 1 | `frontend/` | React SPA | 5173 |
| 2 | `ragsvc/` | Documents, retrieval, artifacts | 8001 |
| 3 | `backend/` | Gateway, models, agent, tools, sandbox | 8000, 8080 |
| all | `contracts/` | Shared types | — |

`contracts/` has no single owner and is the reason three people can work in
this repo at once. Changing it is a separate PR that all three approve — read
[`contracts/CHANGE-PROTOCOL.md`](contracts/CHANGE-PROTOCOL.md) before you touch it.

## Quickstart

Needs **Python 3.11 or 3.12**, Node 20 and Docker. Nothing else.

> **Not Python 3.13.** `ragsvc` pins `python <3.13` because
> `rapidocr-onnxruntime` 1.x will not install on it, and the drop-in successor
> fetches its weights on first use — which would break the air-gap to fix a
> build error. `make setup` checks this and stops with one sentence rather than
> a pip resolver dump. If your default `python3` is newer:
>
> ```bash
> make setup PYTHON3=/path/to/python3.11
> ```

```bash
cp .env.example .env     # 1. configure (localhost defaults are correct for solo dev)
make setup               # 2. venv, contracts, both services, frontend deps, types
make dev-back            # 3. backend on :8000   -> curl localhost:8000/api/health
make dev-front           # 4. SPA on :5173       -> click "open stream"
make test                # 5. pytest + frontend typecheck
```

`make help` lists every target. `make mock` runs the standalone SSE server if
you want to build UI without a backend.

## Driving the demo

The mock server picks a scenario from the message text, so the whole frontend
can be demonstrated with no backend at all:

| Type this | You get |
|---|---|
| …document, SOP, wall loss, report… | vision model, OCR + retrieval with citations, a .docx artifact |
| …code, python, script, downtime… | a 9.4 s model swap, then a tool that fails once and succeeds on retry |
| …fail, timeout, error… | a recoverable `TOOL_TIMEOUT` mid-run, then recovery |
| anything else | plain streamed tokens, no tools |

`python3 frontend/mock/server.py --fast` collapses every delay for automated
checks. Never judge the UI against it: the real timings are the point.

## Pointing the frontend at a real backend

The frontend talks to same-origin `/api`; the vite dev server proxies it. One
env var moves it to another machine — no code change:

```bash
# P3's laptop hosts the backend
VITE_API_TARGET=http://192.168.1.10:8000 npm run dev     # from frontend/
```

Before wiring the UI to it, check the backend actually speaks the contract:

```bash
.venv/bin/python scripts/check-backend.py http://192.168.1.10:8000
```

It validates every endpoint's response against the Pydantic models in
`contracts/`, and streams `/api/chat` to confirm the frames are contract
events starting with `session.start` and ending with `done`. Exit 0 means the
frontend can be pointed at it; anything else names the mismatched field.

## How the pieces fit

```
browser :5173 ──> backend :8000 ──> llama-server :8080  (one model resident)
                       │
                       └────────> ragsvc :8001 ──> qdrant :6333
```

The SPA talks to the backend and nothing else. See
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md), and
[`docs/decisions/`](docs/decisions/) for why each choice is what it is.

## The contract, and why it is strict

`contracts/` is an installable Python package that both services import and
that the frontend generates its TypeScript from:

```bash
make types    # contracts/contracts/*.py -> frontend/src/types/events.ts
```

`frontend/src/types/events.ts` is generated and committed. That is deliberate:
change a field in `contracts/contracts/events.py`, run `make types`, and the
frontend build breaks until someone updates it. **That break is the feature.**
Three people building against an implicit contract on three laptops discover
the drift on stage; a failing `tsc` discovers it in ten seconds.

## State of the repo

This is the **scaffold**. Every service starts, answers a health check and does
nothing else. Each stub file says what it will contain and who owns it. Person
1, 2 and 3 fill them in from their own build prompts — the structure exists so
that none of them blocks the others.
