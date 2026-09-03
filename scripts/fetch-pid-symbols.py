#!/usr/bin/env python3
"""Fetch the P&ID symbols dataset for the vision demo. Owner: person 3.

    python scripts/fetch-pid-symbols.py            # download, report contents
    python scripts/fetch-pid-symbols.py --sample 12  # also copy N images into demo/datasets/pid-symbols

**Setup-time only. This touches the network** (kaggle.com) and must never run
on the demo host; scripts/airgap-check.sh exempts it by name like the other
fetchers. Run it once on a connected machine and copy demo/datasets/ across.

Source: https://www.kaggle.com/datasets/hristohristov21/pid-symbols
Some Kaggle datasets need credentials (~/.kaggle/kaggle.json or the
KAGGLE_USERNAME / KAGGLE_KEY environment variables); kagglehub will say so.

The download lands in the kagglehub cache (KAGGLEHUB_CACHE, default
~/.cache/kagglehub). The sample copied into demo/datasets/ is what the demo
and bench actually use, so the demo never depends on a cache directory.
"""
from __future__ import annotations

import argparse
import collections
import os
import random
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATASET = "hristohristov21/pid-symbols"
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--sample", type=int, default=0,
                        help="copy N random images into demo/datasets/pid-symbols")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    os.environ.setdefault("KAGGLEHUB_CACHE", str(Path("D:/sih/kagglehub"))
                          if Path("D:/").exists() else str(Path.home() / ".cache" / "kagglehub"))
    try:
        import kagglehub  # noqa: PLC0415
    except ImportError:
        print("kagglehub is not installed: pip install kagglehub", file=sys.stderr)
        return 2

    path = Path(kagglehub.dataset_download(DATASET))
    print("Path to dataset files:", path)

    files = [p for p in path.rglob("*") if p.is_file()]
    by_suffix = collections.Counter(p.suffix.lower() for p in files)
    print(f"{len(files)} files: {dict(by_suffix.most_common(8))}")
    top = sorted({p.relative_to(path).parts[0] for p in files})
    print("top-level:", ", ".join(top[:12]) + (" ..." if len(top) > 12 else ""))

    if args.sample:
        images = [p for p in files if p.suffix.lower() in IMAGE_SUFFIXES]
        random.Random(args.seed).shuffle(images)
        out = ROOT / "demo" / "datasets" / "pid-symbols"
        out.mkdir(parents=True, exist_ok=True)
        for p in images[: args.sample]:
            # Keep the class folder name in the filename: it is the label.
            label = p.parent.name.replace(" ", "_")
            shutil.copy2(p, out / f"{label}--{p.name}")
        print(f"copied {min(args.sample, len(images))} images to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
