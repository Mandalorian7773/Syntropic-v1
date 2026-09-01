#!/usr/bin/env bash
# Build the offline bundle: everything the demo host needs, in one directory.
#
# Run on a CONNECTED machine. The output is what gets copied to the air-gapped
# host -- after this, nothing is fetched, ever.
#
# Owner: person 3.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="${OUT:-$ROOT/offline-bundle}"
PY="${PYTHON:-$ROOT/.venv/bin/python}"
[ -x "$PY" ] || PY=python3

mkdir -p "$OUT"/{wheels,npm,images,models}
echo "package-offline: bundling into $OUT"

echo "package-offline: [1/4] python wheels"
"$PY" -m pip download -d "$OUT/wheels" -e "$ROOT/contracts" 2>/dev/null || true
"$PY" -m pip download -d "$OUT/wheels" -r <("$PY" - <<'PYEOF'
import tomllib, pathlib
for name in ("backend", "ragsvc"):
    data = tomllib.loads((pathlib.Path(name) / "pyproject.toml").read_text())
    for dep in data["project"]["dependencies"]:
        if dep != "contracts":
            print(dep)
PYEOF
)

echo "package-offline: [2/4] npm tarballs"
( cd "$ROOT/frontend" && npm ci --no-audit --no-fund >/dev/null && \
  tar czf "$OUT/npm/node_modules.tar.gz" node_modules )

echo "package-offline: [3/4] docker images"
for img in qdrant/qdrant:latest python:3.11-slim nginx:alpine busybox:latest; do
  docker pull "$img"
done
docker save -o "$OUT/images/base-images.tar" \
  qdrant/qdrant:latest python:3.11-slim nginx:alpine busybox:latest

echo "package-offline: [4/4] model weights"
"$ROOT/scripts/download-models.sh" || echo "package-offline: models incomplete, see above"
cp -R "$ROOT/models/." "$OUT/models/" 2>/dev/null || true

cat > "$OUT/INSTALL.md" <<'MD'
# Offline install

On the air-gapped host, from the repo root:

    docker load -i offline-bundle/images/base-images.tar
    tar xzf offline-bundle/npm/node_modules.tar.gz -C frontend/
    cp -R offline-bundle/models/. models/
    python3 -m venv .venv
    .venv/bin/pip install --no-index --find-links offline-bundle/wheels \
        -e ./contracts -e ./backend -e ./ragsvc
    make demo
    make airgap

`--no-index` is the point: pip must not be able to fall back to PyPI.
MD

echo "package-offline: done -> $OUT (see $OUT/INSTALL.md)"
