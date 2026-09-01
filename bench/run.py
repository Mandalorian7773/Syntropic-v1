"""Benchmark harness. Owner: person 3.

Replays bench/tasks.jsonl against a running backend over the same SSE channel
the frontend uses, and records per task: routing decision (and whether it
matched the label), time-to-first-token, total latency, steps, tokens in/out,
stop reason, success (expect regex against the final answer), malformed
tool-call JSON count (from the audit trail), and vram_free after the task.

Two runs make acceptance criterion 3:

    AGENT_GRAMMAR=on  uvicorn main:app ...   ->  python bench/run.py
    AGENT_GRAMMAR=off uvicorn main:app ...   ->  python bench/run.py

Each run writes one timestamped JSON into bench/results/ (committed -- the
chart of success-vs-model is drawn from these files, and "it felt faster" is
not evidence).

    python bench/run.py --endpoint http://localhost:8000 [--only code-01,...]
"""

from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent
RESULTS_DIR = ROOT / "results"


def parse_sse(chunk_iter):
    """Yield decoded event dicts from an SSE byte stream."""
    buffer = ""
    for chunk in chunk_iter:
        buffer += chunk
        while "\n\n" in buffer:
            frame, buffer = buffer.split("\n\n", 1)
            for line in frame.splitlines():
                if line.startswith("data: "):
                    try:
                        yield json.loads(line[len("data: "):])
                    except json.JSONDecodeError:
                        pass


def run_task(client: httpx.Client, endpoint: str, task: dict) -> dict:
    payload = {"message": task["prompt"], "attachments": []}
    attachment = task.get("attachment")
    if attachment and Path(attachment).is_file():
        # Stage the image the way the frontend would.
        with open(attachment, "rb") as fh:
            up = client.post(f"{endpoint}/api/upload",
                             files={"file": (Path(attachment).name, fh)})
        up.raise_for_status()
        payload["attachments"] = [up.json()]
    elif attachment:
        return {"id": task["id"], "skipped": f"attachment missing: {attachment}"}

    record: dict = {"id": task["id"], "task_type": task["task_type"],
                    "prompt": task["prompt"]}
    started = time.monotonic()
    first_token_ms = None
    final_text = ""
    session_id = None

    with client.stream("POST", f"{endpoint}/api/chat", json=payload,
                       timeout=600) as resp:
        resp.raise_for_status()
        for event in parse_sse(resp.iter_text()):
            etype = event.get("type")
            if etype == "session.start":
                session_id = event["session_id"]
            elif etype == "router.decision":
                record["routed_model"] = event["model_id"]
                record["routed_task"] = event["task_type"]
                record["route_confidence"] = event["confidence"]
                record["route_correct"] = event["task_type"] == task["task_type"]
            elif etype == "model.ready":
                record["swap_load_ms"] = event["load_ms"]
            elif etype == "token":
                if first_token_ms is None:
                    first_token_ms = int((time.monotonic() - started) * 1000)
                final_text += event["text"]
            elif etype == "done":
                record.update(
                    stop_reason=event["stop_reason"], steps=event["steps_used"],
                    tokens_in=event["tokens_in"], tokens_out=event["tokens_out"],
                    latency_ms=event["latency_ms"],
                )
            elif etype == "error":
                record.setdefault("errors", []).append(event["code"])

    record["ttft_ms"] = first_token_ms
    record["final_chars"] = len(final_text)
    expect = task.get("expect", "")
    if expect:
        record["success"] = bool(re.search(expect, final_text, re.IGNORECASE))
    else:
        record["success"] = record.get("stop_reason") == "final_answer"

    # Malformed protocol JSON per session, from the audit trail.
    if session_id:
        try:
            trail = client.get(f"{endpoint}/api/audit",
                               params={"session_id": session_id}).json()["trail"]
            record["malformed"] = sum(1 for r in trail if r["kind"] == "llm.malformed")
        except (httpx.HTTPError, KeyError):
            record["malformed"] = None
    try:
        record["vram_free_mb"] = client.get(f"{endpoint}/api/health").json()["vram_free_mb"]
    except (httpx.HTTPError, KeyError):
        pass
    return record


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--endpoint", default="http://localhost:8000")
    parser.add_argument("--tasks", default=str(ROOT / "tasks.jsonl"))
    parser.add_argument("--only", default="", help="comma-separated task ids")
    args = parser.parse_args()

    tasks = [json.loads(line) for line in
             Path(args.tasks).read_text(encoding="utf-8").splitlines() if line.strip()]
    if args.only:
        wanted = set(args.only.split(","))
        tasks = [t for t in tasks if t["id"] in wanted]

    results = []
    with httpx.Client() as client:
        health = client.get(f"{args.endpoint}/api/health").json()
        print(f"bench: backend up, model={health.get('model_loaded')}")
        for task in tasks:
            print(f"bench: {task['id']} ...", end=" ", flush=True)
            try:
                record = run_task(client, args.endpoint, task)
            except Exception as exc:
                record = {"id": task["id"], "error": f"{type(exc).__name__}: {exc}"}
            results.append(record)
            print(record.get("skipped") or record.get("error")
                  or f"{'OK' if record.get('success') else 'FAIL'} "
                     f"{record.get('latency_ms', '?')}ms "
                     f"route={record.get('routed_model', '?')}")

    ran = [r for r in results if "success" in r]
    summary = {
        "ts": int(time.time()),
        "endpoint": args.endpoint,
        "tasks_run": len(ran),
        "success_rate": round(sum(r["success"] for r in ran) / len(ran), 3) if ran else None,
        "route_accuracy": round(
            sum(bool(r.get("route_correct")) for r in ran) / len(ran), 3) if ran else None,
        "malformed_total": sum(r.get("malformed") or 0 for r in ran),
        "avg_latency_ms": round(
            sum(r.get("latency_ms", 0) for r in ran) / len(ran)) if ran else None,
        "results": results,
    }
    RESULTS_DIR.mkdir(exist_ok=True)
    out = RESULTS_DIR / f"bench-{time.strftime('%Y%m%d-%H%M%S')}.json"
    out.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"\nbench: success={summary['success_rate']} "
          f"route_acc={summary['route_accuracy']} "
          f"malformed={summary['malformed_total']} -> {out}")


if __name__ == "__main__":
    main()
