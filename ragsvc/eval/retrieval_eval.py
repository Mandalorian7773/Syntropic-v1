"""Retrieval evaluation harness. Owner: person 2.

    python ragsvc/eval/retrieval_eval.py --ingest     # build the index, then score
    python ragsvc/eval/retrieval_eval.py              # score the existing index
    python ragsvc/eval/retrieval_eval.py --ablate     # score every configuration

Reports recall@k and mean reciprocal rank over eval/questions.jsonl, so that
"we added a reranker" becomes a number instead of a feeling.

Run it after every change to chunking, fusion or reranking. Do not tune the
prompt, and do not blame the language model, until recall@5 is above 0.8: most
RAG failures are retrieval failures wearing a costume, and a generator cannot
cite a page that was never put in front of it.

**What counts as a hit.** A chunk matches when it comes from the expected file
*and its page span contains the expected page*. Span, not start page: a chunk
that runs from page 3 to page 4 and contains a fact printed on page 4 has
retrieved that fact, and scoring it wrong would penalise the chunker for
working. The stricter start-page number is reported alongside so the difference
is visible rather than hidden in the definition.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import time
from pathlib import Path

RAGSVC = Path(__file__).resolve().parent.parent
ROOT = RAGSVC.parent
if str(RAGSVC) not in sys.path:
    sys.path.insert(0, str(RAGSVC))

import ragconfig as cfg  # noqa: E402
import ragdb  # noqa: E402
from index.search import search  # noqa: E402

DEFAULT_QUESTIONS = RAGSVC / "eval" / "questions.jsonl"
DEFAULT_OUT = ROOT / "bench" / "results" / "retrieval-eval.json"
RECALL_AT = (1, 3, 5, 10)

CONFIGURATIONS = {
    "hybrid+rerank": {"mode": "hybrid", "use_rerank": True},
    "hybrid": {"mode": "hybrid", "use_rerank": False},
    "dense": {"mode": "dense", "use_rerank": False},
    "sparse": {"mode": "sparse", "use_rerank": False},
    "dense+rerank": {"mode": "dense", "use_rerank": True},
}


def load_questions(path: Path) -> list[dict]:
    questions = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        record = json.loads(line)
        if "PLACEHOLDER" in record.get("question", ""):
            continue
        if not record.get("expected_doc"):
            continue
        questions.append(record)
    return questions


def chunk_spans(chunk_ids: list[str]) -> dict[str, tuple[int, int]]:
    """Page span for each chunk, so a fact on the second page of a chunk counts."""
    rows = ragdb.get_chunks(chunk_ids)
    return {cid: (row["page"], row["page_end"]) for cid, row in rows.items()}


def rank_of_hit(hits, expected_doc: str, expected_page: int, spans: dict) -> tuple[int | None, int | None]:
    """1-based rank of the first matching hit, by span and by exact start page."""
    span_rank = exact_rank = None
    for position, hit in enumerate(hits, start=1):
        if hit.filename != expected_doc:
            continue
        start, end = spans.get(hit.chunk_id, (hit.page, hit.page))
        if span_rank is None and start <= expected_page <= end:
            span_rank = position
        if exact_rank is None and hit.page == expected_page:
            exact_rank = position
        if span_rank is not None and exact_rank is not None:
            break
    return span_rank, exact_rank


def evaluate(questions: list[dict], mode: str, use_rerank: bool, top_k: int) -> dict:
    ranks: list[int | None] = []
    exact_ranks: list[int | None] = []
    latencies: list[float] = []
    per_question: list[dict] = []

    for question in questions:
        started = time.perf_counter()
        result = search(question["question"], top_k=top_k, mode=mode, use_rerank=use_rerank)
        elapsed = (time.perf_counter() - started) * 1000
        latencies.append(elapsed)

        spans = chunk_spans([hit.chunk_id for hit in result.hits])
        span_rank, exact_rank = rank_of_hit(
            result.hits, question["expected_doc"], int(question["expected_page"]), spans
        )
        ranks.append(span_rank)
        exact_ranks.append(exact_rank)

        missing_provenance = [
            hit.chunk_id for hit in result.hits if not hit.filename or hit.page is None
        ]
        per_question.append(
            {
                "id": question["id"],
                "kind": question.get("kind", "unknown"),
                "question": question["question"],
                "expected": f"{question['expected_doc']} p.{question['expected_page']}",
                "rank": span_rank,
                "exact_rank": exact_rank,
                "latency_ms": round(elapsed, 1),
                "top_hit": (
                    f"{result.hits[0].filename} p.{result.hits[0].page}"
                    if result.hits
                    else None
                ),
                "missing_provenance": missing_provenance,
            }
        )

    total = len(questions) or 1
    recall = {
        f"recall@{k}": sum(1 for r in ranks if r is not None and r <= k) / total
        for k in RECALL_AT
        if k <= top_k
    }
    mrr = sum(1.0 / r for r in ranks if r is not None) / total
    exact_recall5 = sum(1 for r in exact_ranks if r is not None and r <= 5) / total

    by_kind: dict[str, dict] = {}
    for question, rank in zip(questions, ranks):
        kind = question.get("kind", "unknown")
        bucket = by_kind.setdefault(kind, {"n": 0, "hit@5": 0})
        bucket["n"] += 1
        if rank is not None and rank <= 5:
            bucket["hit@5"] += 1
    for bucket in by_kind.values():
        bucket["recall@5"] = round(bucket["hit@5"] / bucket["n"], 3)

    latencies_sorted = sorted(latencies)
    return {
        "mode": mode,
        "rerank": use_rerank,
        "questions": len(questions),
        **{k: round(v, 4) for k, v in recall.items()},
        "mrr": round(mrr, 4),
        "recall@5_exact_page": round(exact_recall5, 4),
        "latency_ms": {
            "mean": round(statistics.mean(latencies), 1) if latencies else 0,
            "p50": round(statistics.median(latencies), 1) if latencies else 0,
            "p95": round(
                latencies_sorted[max(0, int(len(latencies_sorted) * 0.95) - 1)], 1
            )
            if latencies
            else 0,
            "max": round(max(latencies), 1) if latencies else 0,
        },
        "by_kind": by_kind,
        "misses": [q["id"] for q, r in zip(questions, ranks) if r is None or r > 5],
        "provenance_failures": sum(
            len(entry["missing_provenance"]) for entry in per_question
        ),
        "per_question": per_question,
    }


ANSWER_TOKEN_RE = re.compile(r"[A-Za-z0-9]+(?:[-./][A-Za-z0-9]+)*")


def _answer_keys(answer: str) -> list[str]:
    """The distinctive parts of an expected answer: numbers and long words."""
    return [
        token
        for token in ANSWER_TOKEN_RE.findall(answer)
        if any(c.isdigit() for c in token) or len(token) > 4
    ]


def answer_presence(questions: list[dict], top_k: int = 5) -> dict:
    """Does the expected answer actually reach the model?

    recall@5 says the right chunk was retrieved. It does not say the answer
    survived into the snippet the agent sees, and those came apart badly here:
    with recall@5 at a flat 1.000, the answer was reaching the model in only
    some cases because snippets were cut from the front of the chunk, and a
    valve register row 464 characters in never made it. The agent then had five
    passages and no answer, reissued the same query, and its loop detector
    killed the turn -- a retrieval success that reads as a total failure.

    So this measures the tool output the agent is actually handed.
    """
    import tools as ragtools  # noqa: PLC0415
    from contracts import RunContext  # noqa: PLC0415

    context = RunContext(
        session_id="eval",
        workspace_dir=str(cfg.WORKSPACE_DIR),
        artifacts_dir=str(cfg.ARTIFACTS_DIR),
    )
    tool = ragtools.BY_NAME["search_documents"]

    hits, misses = 0, []
    for question in questions:
        answer = question.get("answer", "")
        keys = _answer_keys(answer)
        if not keys:
            continue
        content = tool.run(
            tool.args_model(query=question["question"], top_k=top_k), context
        ).content.lower()
        found = [k for k in keys if k.lower() in content]
        if len(found) >= max(1, len(keys) // 2):
            hits += 1
        else:
            misses.append(question["id"])

    scored = len([q for q in questions if _answer_keys(q.get("answer", ""))])
    return {
        "scored": scored,
        "present": hits,
        f"answer_present@{top_k}": round(hits / scored, 4) if scored else 0.0,
        "misses": misses,
    }


def print_summary(name: str, result: dict) -> None:
    recalls = "  ".join(
        f"r@{k}={result[f'recall@{k}']:.3f}" for k in RECALL_AT if f"recall@{k}" in result
    )
    print(
        f"  {name:<14} {recalls}  mrr={result['mrr']:.3f}  "
        f"p50={result['latency_ms']['p50']:>6.1f}ms  p95={result['latency_ms']['p95']:>6.1f}ms"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--questions", default=str(DEFAULT_QUESTIONS))
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument(
        "--ingest",
        action="store_true",
        help="wipe the index and re-ingest demo/documents before scoring",
    )
    parser.add_argument(
        "--corpus", default=str(ROOT / "demo" / "documents"), help="corpus to ingest"
    )
    parser.add_argument(
        "--ablate", action="store_true", help="score every retrieval configuration"
    )
    parser.add_argument(
        "--configs",
        default=None,
        help="comma-separated subset to score, e.g. hybrid,dense,sparse. "
        "Reranked configurations are slow on CPU; this is how you skip them.",
    )
    parser.add_argument(
        "--gate",
        type=float,
        default=None,
        help="exit nonzero if recall@5 falls below this value",
    )
    args = parser.parse_args()

    import corpus  # noqa: PLC0415 - after sys.path is set up

    corpus.startup()

    if args.ingest:
        print(f"retrieval_eval: ingesting {args.corpus}")
        from index import qdrant_store  # noqa: PLC0415

        qdrant_store.get_store().recreate()
        for row in ragdb.list_documents():
            ragdb.delete_document(row["id"])
        started = time.perf_counter()
        outcomes = corpus.ingest_directory(args.corpus)
        elapsed = time.perf_counter() - started
        pages = sum(o.pages for o in outcomes)
        chunks = sum(o.chunks for o in outcomes)
        print(
            f"retrieval_eval: ingested {len(outcomes)} documents, {pages} pages, "
            f"{chunks} chunks in {elapsed:.1f}s"
        )
        for outcome in outcomes:
            flag = "scanned" if outcome.scanned_pages else "native "
            print(
                f"  {outcome.filename:<44} {outcome.pages:>3}p {flag} "
                f"{outcome.chunks:>4} chunks  {outcome.duration_ms / 1000:>6.1f}s"
                + (f"  conf={outcome.mean_conf:.2f}" if outcome.scanned_pages else "")
            )

    questions = load_questions(Path(args.questions))
    if not questions:
        print("retrieval_eval: no usable questions found; nothing to score")
        return 1
    print(f"retrieval_eval: {len(questions)} questions, top_k={args.top_k}")

    if args.configs:
        wanted = [name.strip() for name in args.configs.split(",") if name.strip()]
        unknown = [name for name in wanted if name not in CONFIGURATIONS]
        if unknown:
            print(f"retrieval_eval: unknown configuration(s): {', '.join(unknown)}")
            print(f"retrieval_eval: available: {', '.join(CONFIGURATIONS)}")
            return 1
        configurations = {name: CONFIGURATIONS[name] for name in wanted}
    elif args.ablate:
        configurations = dict(CONFIGURATIONS)
    else:
        default = "hybrid+rerank" if cfg.RERANK_ENABLED else "hybrid"
        configurations = {default: CONFIGURATIONS[default]}

    results: dict[str, dict] = {}
    for name, settings in configurations.items():
        results[name] = evaluate(questions, top_k=args.top_k, **settings)
        print_summary(name, results[name])

    # The headline is whatever the service is actually configured to do, not a
    # configuration that only exists in the ablation.
    headline_name = next(
        (n for n in ("hybrid+rerank", "hybrid", "dense", "sparse") if n in results),
        next(iter(results)),
    )
    headline = results[headline_name]
    print()
    print(f"retrieval_eval: headline configuration is {headline_name}")
    print()
    print(f"retrieval_eval: recall@5 = {headline['recall@5']:.3f}   MRR = {headline['mrr']:.3f}")
    if headline["misses"]:
        print(f"retrieval_eval: missed at 5 -> {', '.join(headline['misses'])}")
    if headline["provenance_failures"]:
        print(
            f"retrieval_eval: {headline['provenance_failures']} hit(s) lacked filename "
            f"or page. That is a bug, not a score."
        )
    print("retrieval_eval: by question kind ->", json.dumps(headline["by_kind"]))

    presence = answer_presence(questions, top_k=5)
    print(
        f"retrieval_eval: answer present in tool output = "
        f"{presence['answer_present@5']:.3f} "
        f"({presence['present']}/{presence['scored']})"
        + (f"   missing: {', '.join(presence['misses'])}" if presence["misses"] else "")
    )

    payload = {
        "generated_at": int(time.time()),
        "questions_file": str(args.questions),
        "corpus": str(args.corpus),
        "top_k": args.top_k,
        "config": {
            "chunk_tokens": cfg.CHUNK_TOKENS,
            "chunk_overlap": cfg.CHUNK_OVERLAP,
            "dense_top": cfg.DENSE_TOP,
            "sparse_top": cfg.SPARSE_TOP,
            "rrf_k": cfg.RRF_K,
            "embed_max_len": cfg.EMBED_MAX_LEN,
            "rerank_max_len": cfg.RERANK_MAX_LEN,
        },
        "corpus_stats": corpus.stats(),
        "answer_presence": presence,
        "results": results,
    }
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"retrieval_eval: results written to {out_path}")

    if args.gate is not None and headline["recall@5"] < args.gate:
        print(
            f"retrieval_eval: FAIL recall@5 {headline['recall@5']:.3f} < gate {args.gate}"
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
