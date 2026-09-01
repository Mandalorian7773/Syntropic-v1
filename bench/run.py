"""Benchmark harness. Owner: person 3. Empty by design.

Will contain: replay of bench/tasks.jsonl against a running backend, recording
per-task time-to-first-token, total latency, steps used, tokens in/out, peak
VRAM and whether the routing decision matched the task_type label.

Writes one timestamped JSON per run into bench/results/. Those results are
COMMITTED -- see .gitignore. They are the evidence that the thing got faster,
and "it felt faster" is not evidence.

    python bench/run.py --endpoint http://localhost:8000
"""

if __name__ == "__main__":
    raise SystemExit("bench/run.py is a stub -- owner: person 3")
