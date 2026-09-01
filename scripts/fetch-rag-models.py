#!/usr/bin/env python3
"""Fetch the ragsvc CPU models listed under `rag_models:` in models/MANIFEST.yaml.

Owner: person 2. Companion to scripts/download-models.sh, which handles the
single-file GGUF weights; these are directories of ONNX graphs plus their
tokenizers, so they need their own fetcher.

**This script touches the network and is setup-time only.** It never runs on
the demo host. Run it once on a connected machine, commit the lock file, copy
`models/` across. scripts/airgap-check.sh exempts this file by name for exactly
the same reason it exempts download-models.sh.

    python scripts/fetch-rag-models.py --record   # first run, records hashes
    python scripts/fetch-rag-models.py            # later runs, verifies them

Hashes are recorded in models/rag-models.lock.json rather than written back
into MANIFEST.yaml, because rewriting that file would discard its comments.
A mismatch is a hard failure: a silently truncated download that onnxruntime
half-loads is the kind of bug that eats a demo day.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.request
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "models" / "MANIFEST.yaml"
LOCK = ROOT / "models" / "rag-models.lock.json"
HOST = "https://huggingface.co"
CHUNK = 1 << 20


def digest(path: Path) -> tuple[str, int]:
    sha = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(CHUNK), b""):
            sha.update(block)
            size += len(block)
    return sha.hexdigest(), size


def download(url: str, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(target.suffix + ".part")
    request = urllib.request.Request(url, headers={"User-Agent": "sih26117-setup"})
    with urllib.request.urlopen(request, timeout=120) as response:
        total = int(response.headers.get("Content-Length") or 0)
        done = 0
        with partial.open("wb") as handle:
            while True:
                block = response.read(CHUNK)
                if not block:
                    break
                handle.write(block)
                done += len(block)
                if total:
                    percent = 100 * done / total
                    print(
                        f"\r    {target.name}: {done >> 20} / {total >> 20} MiB "
                        f"({percent:5.1f}%)",
                        end="",
                        flush=True,
                    )
        print()
    partial.replace(target)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--record",
        action="store_true",
        help="record the hash of each downloaded file into the lock file",
    )
    parser.add_argument(
        "--dest",
        default=str(ROOT / "models"),
        help="destination directory (default: ./models)",
    )
    parser.add_argument("--only", help="fetch a single manifest entry by id")
    args = parser.parse_args()

    manifest = yaml.safe_load(MANIFEST.read_text(encoding="utf-8")) or {}
    entries = manifest.get("rag_models") or []
    if args.only:
        entries = [e for e in entries if e.get("id") == args.only]
    if not entries:
        print("fetch-rag-models: nothing to do (no rag_models entries matched)")
        return 1

    lock = json.loads(LOCK.read_text(encoding="utf-8")) if LOCK.exists() else {}
    dest_root = Path(args.dest)
    failures = 0

    for entry in entries:
        model_id = entry["id"]
        repo = entry["repo"]
        revision = entry.get("revision", "main")
        model_dir = dest_root / entry.get("dest", model_id)
        print(f"fetch-rag-models: {model_id} <- {repo}@{revision}")
        recorded = lock.setdefault(model_id, {"repo": repo, "revision": revision, "files": {}})

        for spec in entry.get("files", []):
            target = model_dir / spec["dest"]
            url = f"{HOST}/{repo}/resolve/{revision}/{spec['src']}"

            if not target.exists():
                print(f"  downloading {spec['src']}")
                try:
                    download(url, target)
                except Exception as exc:  # noqa: BLE001
                    print(f"  FAILED {spec['src']}: {exc}")
                    failures += 1
                    continue
            else:
                print(f"  present    {spec['dest']}")

            actual, size = digest(target)
            expected = spec.get("sha256") or recorded["files"].get(spec["dest"], {}).get("sha256")

            if expected and expected != actual:
                print(f"  CHECKSUM MISMATCH for {spec['dest']}")
                print(f"    expected {expected}")
                print(f"    actual   {actual}")
                print(f"    Refusing to continue. Delete {target} and retry.")
                return 1

            if not expected:
                if args.record:
                    recorded["files"][spec["dest"]] = {"sha256": actual, "size_bytes": size}
                    print(f"  recorded   {spec['dest']} {actual[:16]}... ({size >> 20} MiB)")
                else:
                    print(f"  UNPINNED   {spec['dest']} -- re-run with --record to pin it")
                    failures += 1
            else:
                print(f"  verified   {spec['dest']} ({size >> 20} MiB)")

    if args.record:
        LOCK.write_text(json.dumps(lock, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"fetch-rag-models: lock written to {LOCK}")

    if failures:
        print(f"fetch-rag-models: {failures} file(s) unresolved")
        return 1
    print("fetch-rag-models: all models present and verified.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
