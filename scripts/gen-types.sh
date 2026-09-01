#!/usr/bin/env bash
# Regenerate frontend/src/types/events.ts from the Pydantic contracts.
#
# Offline by design: json-schema-to-typescript is a devDependency in
# frontend/package.json and is invoked from node_modules/.bin directly.
# Never `npx` here -- npx reaches the registry on a cache miss, and the demo
# machine has no route to it.
#
# Owner: shared. Run this after every contracts/ change (see CHANGE-PROTOCOL.md).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="${PYTHON:-$ROOT/.venv/bin/python}"
[ -x "$PY" ] || PY=python3
J2T="$ROOT/frontend/node_modules/.bin/json2ts"
SCHEMA="$ROOT/frontend/src/types/.schema.json"
OUT="$ROOT/frontend/src/types/events.ts"

if [ ! -x "$J2T" ]; then
  echo "gen-types: json-schema-to-typescript not installed."
  echo "           run 'make setup' (or 'npm ci' in frontend/) first."
  exit 1
fi

echo "gen-types: exporting JSON Schema from contracts/ ..."
"$PY" - "$SCHEMA" <<'PYEOF'
import json, sys
from pydantic import TypeAdapter
from contracts import events, api

# One schema document with every contract model hung off it, so json2ts emits
# a single events.ts containing the SSE union AND the REST shapes.
def strip_titles(node):
    """Pydantic titles every field; json2ts turns each one into a junk type
    alias (`export type Ts = number`). Drop them below the top level."""
    if isinstance(node, dict):
        for key in ("properties", "$defs", "definitions"):
            for sub in node.get(key, {}).values():
                sub.pop("title", None)
                strip_titles(sub)
        for key in ("items", "additionalProperties"):
            if isinstance(node.get(key), dict):
                node[key].pop("title", None)
                strip_titles(node[key])
        for key in ("anyOf", "oneOf", "allOf"):
            for sub in node.get(key, []):
                if isinstance(sub, dict):
                    sub.pop("title", None)
                    strip_titles(sub)
    return node


defs: dict = {}
props: dict = {}
for mod in (events, api):
    for name in mod.__all__:
        obj = getattr(mod, name)
        if not (isinstance(obj, type) or name == "Event"):
            continue  # skip Literal aliases and to_sse
        try:
            schema = TypeAdapter(obj).json_schema(ref_template="#/definitions/{model}")
        except Exception:
            continue
        strip_titles(schema)
        nested = schema.pop("$defs", {})
        for k, v in nested.items():
            v["title"] = k
        defs.update(nested)
        schema["title"] = name
        defs[name] = schema
        props[name] = {"$ref": f"#/definitions/{name}"}

doc = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "title": "Contracts",
    "type": "object",
    "properties": props,
    "required": sorted(props),
    "additionalProperties": False,
    "definitions": defs,
}
with open(sys.argv[1], "w") as fh:
    json.dump(doc, fh, indent=2)
print(f"gen-types: {len(defs)} definitions")
PYEOF

echo "gen-types: converting to TypeScript ..."
"$J2T" --input "$SCHEMA" --output "$OUT.tmp" \
  --additionalProperties false \
  --bannerComment "" \
  --style.singleQuote

{
  echo "/* ============================================================"
  echo " * GENERATED FILE -- DO NOT EDIT BY HAND."
  echo " *"
  echo " * Source:    contracts/contracts/{events,api}.py"
  echo " * Regenerate: make types"
  echo " *"
  echo " * Hand edits are overwritten on the next 'make types' and, worse,"
  echo " * they hide contract drift that the build is supposed to catch."
  echo " * If a type here is wrong, fix the Pydantic model and regenerate."
  echo " * See contracts/CHANGE-PROTOCOL.md."
  echo " * ============================================================ */"
  echo
  cat "$OUT.tmp"
} > "$OUT"
rm -f "$OUT.tmp" "$SCHEMA"

echo "gen-types: wrote $OUT"
