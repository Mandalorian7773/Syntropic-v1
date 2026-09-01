#!/usr/bin/env bash
# Fetch weights listed in models/MANIFEST.yaml and verify their checksums.
#
# This is the ONLY script in the repo that is allowed to touch the network, and
# it is a setup-time script -- it never runs during a demo. Run it once on a
# connected machine, then copy ./models to the demo host.
#
# Owner: person 3.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MANIFEST="$ROOT/models/MANIFEST.yaml"
DEST="${MODELS_DIR:-$ROOT/models}"
PY="${PYTHON:-$ROOT/.venv/bin/python}"
[ -x "$PY" ] || PY=python3

mkdir -p "$DEST"
echo "download-models: manifest $MANIFEST -> $DEST"

# Parse the manifest into tab-separated rows the shell can loop over.
ROWS="$("$PY" - "$MANIFEST" <<'PYEOF'
import sys, yaml
doc = yaml.safe_load(open(sys.argv[1]))
for m in doc.get("models", []):
    print("\t".join(str(m.get(k, "")) for k in ("id", "repo", "filename", "sha256", "size_bytes")))
PYEOF
)"

sha256_of() {
  if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | cut -d' ' -f1
  else shasum -a 256 "$1" | cut -d' ' -f1; fi
}

fail=0
while IFS=$'\t' read -r id repo filename sha size; do
  [ -n "$id" ] || continue
  target="$DEST/$filename"

  case "$repo$filename$sha" in
    *PLACEHOLDER*|*0000000000000000*)
      echo "download-models: SKIP $id -- manifest entry is still a placeholder."
      echo "                 Verify the repo and filename on Hugging Face, record"
      echo "                 the real sha256, then re-run. See models/MANIFEST.yaml."
      fail=1
      continue
      ;;
  esac

  if [ -f "$target" ]; then
    echo "download-models: $id already present, verifying ..."
  else
    if ! command -v huggingface-cli >/dev/null 2>&1; then
      echo "download-models: huggingface-cli not found. Install it on the CONNECTED"
      echo "                 machine only: pip install 'huggingface_hub[cli]'"
      exit 1
    fi
    echo "download-models: fetching $id from $repo ..."
    huggingface-cli download "$repo" "$filename" --local-dir "$DEST" --local-dir-use-symlinks False
  fi

  actual="$(sha256_of "$target")"
  if [ "$actual" != "$sha" ]; then
    echo "download-models: CHECKSUM MISMATCH for $id"
    echo "                 expected $sha"
    echo "                 actual   $actual"
    echo "                 Refusing to continue. Delete $target and retry."
    exit 1
  fi
  [ "$size" = "0" ] || [ "$(wc -c <"$target" | tr -d ' ')" = "$size" ] || {
    echo "download-models: SIZE MISMATCH for $id (expected $size bytes)"; exit 1; }
  echo "download-models: $id OK"
done <<< "$ROWS"

if [ "$fail" -ne 0 ]; then
  echo "download-models: finished with placeholder entries skipped."
  exit 1
fi
echo "download-models: all weights present and verified."
