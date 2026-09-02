"""Ingest benchmark. Owner: person 2.

    python ragsvc/eval/ingest_bench.py --file demo/documents/SOP-INSP-014-relief-valve-testing.pdf

Times the acceptance criterion directly: a 20-page scanned PDF must ingest end
to end in under 90 seconds on laptop CPU. Breaks the time down by stage, so
when it misses you know which stage to attack rather than guessing.

`--stage-profile` times render, preprocess, OCR and layout separately for a
handful of pages. That breakdown is the whole reason this file exists: the
first instinct on a slow ingest is to lower the DPI, and the profile is what
tells you whether DPI is actually where the time is going.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

RAGSVC = Path(__file__).resolve().parent.parent
ROOT = RAGSVC.parent
if str(RAGSVC) not in sys.path:
    sys.path.insert(0, str(RAGSVC))

import ragconfig as cfg  # noqa: E402


def stage_profile(path: Path, pages: int, dpi: int) -> dict:
    """Time each stage separately on the first `pages` scanned pages."""
    from ingest import layout, ocr, pdf, preprocess  # noqa: PLC0415

    timings: dict[str, list[float]] = {
        "render": [], "preprocess": [], "ocr": [], "layout": []
    }
    boxes: list[int] = []
    document = pdf.open_document(path)
    try:
        done = 0
        for index in range(document.page_count):
            if done >= pages:
                break
            page = document[index]
            if pdf.is_native_text(page):
                continue

            started = time.perf_counter()
            image = pdf.render_page(page, dpi)
            timings["render"].append(time.perf_counter() - started)

            started = time.perf_counter()
            ocr_input, report = preprocess.prepare(image)
            timings["preprocess"].append(time.perf_counter() - started)

            scale = pdf.scale_for(page, dpi)
            started = time.perf_counter()
            lines = ocr.read_page(ocr_input, scale)
            timings["ocr"].append(time.perf_counter() - started)
            boxes.append(len(lines))

            started = time.perf_counter()
            ruled = layout.detect_ruled_tables(report.get("binary"), scale)
            layout.build_blocks(index + 1, lines, page.rect.width, ruled_regions=ruled)
            timings["layout"].append(time.perf_counter() - started)
            done += 1
    finally:
        document.close()

    return {
        "dpi": dpi,
        "pages_profiled": len(timings["ocr"]),
        "text_boxes_per_page": round(statistics.mean(boxes), 1) if boxes else 0,
        "mean_ms": {
            stage: round(statistics.mean(values) * 1000, 1)
            for stage, values in timings.items()
            if values
        },
        "total_ms_per_page": round(
            sum(statistics.mean(v) for v in timings.values() if v) * 1000, 1
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--file",
        default=str(ROOT / "demo" / "documents" / "SOP-INSP-014-relief-valve-testing.pdf"),
    )
    parser.add_argument("--budget", type=float, default=90.0, help="seconds")
    parser.add_argument("--out", default=str(ROOT / "bench" / "results" / "ingest-bench.json"))
    parser.add_argument(
        "--stage-profile", type=int, default=0, help="profile stages on N pages and exit"
    )
    parser.add_argument("--dpi", type=int, default=None)
    args = parser.parse_args()

    path = Path(args.file)
    if not path.exists():
        print(f"ingest_bench: {path} not found. Run eval/make_corpus.py first.")
        return 1

    dpi = args.dpi or cfg.RENDER_DPI
    if args.stage_profile:
        print(f"ingest_bench: profiling {args.stage_profile} page(s) at {dpi} DPI")
        profile = stage_profile(path, args.stage_profile, dpi)
        print(json.dumps(profile, indent=2))
        return 0

    from ingest.pipeline import ingest_document  # noqa: PLC0415

    print(f"ingest_bench: {path.name} at {dpi} DPI, budget {args.budget:.0f}s")
    started = time.perf_counter()
    result = ingest_document(path, dpi=dpi)
    elapsed = time.perf_counter() - started

    per_page = elapsed / max(result.page_count, 1)
    payload = {
        "generated_at": int(time.time()),
        "file": path.name,
        "pages": result.page_count,
        "scanned_pages": result.scanned_pages,
        "native_pages": result.native_pages,
        "chunks": len(result.chunks),
        "dpi_requested": dpi,
        "dpi_used": sorted(set(result.dpi_used)),
        "downshifted": result.downshifted,
        "ocr_backend": result.ocr_backend,
        "mean_ocr_confidence": round(result.mean_conf, 3),
        "workers": cfg.OCR_WORKERS,
        "cpu_threads": cfg.CPU_THREADS,
        "elapsed_s": round(elapsed, 2),
        "seconds_per_page": round(per_page, 2),
        "budget_s": args.budget,
        "within_budget": elapsed < args.budget,
    }

    print(
        f"ingest_bench: {result.page_count} pages "
        f"({result.scanned_pages} scanned) in {elapsed:.1f}s "
        f"= {per_page:.2f}s/page  ->  "
        f"{'WITHIN' if payload['within_budget'] else 'OVER'} the {args.budget:.0f}s budget"
    )
    print(
        f"ingest_bench: backend={result.ocr_backend} dpi={payload['dpi_used']} "
        f"workers={cfg.OCR_WORKERS} conf={result.mean_conf:.3f} "
        f"chunks={len(result.chunks)}"
    )

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"ingest_bench: written to {out_path}")
    return 0 if payload["within_budget"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
