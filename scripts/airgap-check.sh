#!/usr/bin/env bash
# Prove the stack has no route to the internet. Exits nonzero on any failure.
#
# Run this before the demo, every time. "We think it is air-gapped" is not a
# claim you want to be making for the first time in front of judges.
#
# Owner: person 3.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
fails=0
pass() { echo "  PASS  $1"; }
fail() { echo "  FAIL  $1"; fails=$((fails + 1)); }

echo "airgap-check: static checks"

# 1. No external URLs baked into source that could be dereferenced at runtime.
#    Excluded, with reasons:
#      - lockfiles: registry URLs there are resolved at SETUP time by npm ci on a
#        connected machine, never on the demo host
#      - local hostnames and compose service names: those are the whole point
#      - schema/xmlns URNs (json-schema.org, w3.org, openxmlformats.org): they
#        are identifiers, never fetched
#      - the three setup-time scripts, which are allowed to touch the network
#      - comment lines
hits="$(grep -rInE 'https?://' \
  --include='*.py' --include='*.ts' --include='*.tsx' --include='*.js' \
  --include='*.json' --include='*.yaml' --include='*.yml' --include='*.html' \
  --include='*.sh' --include='*.conf' --include='Dockerfile' --include='Makefile' \
  --exclude='package-lock.json' --exclude='*.lock' --exclude='yarn.lock' \
  --exclude-dir=node_modules --exclude-dir=.venv --exclude-dir=dist \
  --exclude-dir=.git --exclude-dir=__pycache__ --exclude-dir=offline-bundle \
  "$ROOT" 2>/dev/null \
  | grep -vE '://(localhost|127\.0\.0\.1|0\.0\.0\.0|\[::1\]|host\.docker\.internal|backend|ragsvc|qdrant|llama-server|frontend)([:/]|$)' \
  | grep -vE '://(10\.|192\.168\.|172\.(1[6-9]|2[0-9]|3[01])\.)' \
  | grep -vE 'json-schema\.org|openxmlformats\.org|w3\.org' \
  | grep -vE '^[^:]+:[0-9]+: *(#|//|\*|--)' \
  | grep -vE '/scripts/(download-models|airgap-check|package-offline)\.sh:' \
  || true)"
if [ -n "$hits" ]; then
  fail "external URLs found in source:"
  echo "$hits" | sed 's/^/        /'
else
  pass "no external URLs in source"
fi

# 2. No npx in build scripts -- npx hits the registry on a cache miss.
#    Skip comments and this script itself, or the check flags its own docs.
npx_hits="$(grep -rn 'npx ' "$ROOT/Makefile" "$ROOT/scripts" "$ROOT/frontend/package.json" 2>/dev/null \
  | grep -v 'airgap-check.sh' \
  | grep -vE '^[^:]+:[0-9]+: *(#|//)' \
  | grep -v 'npx --no-install' || true)"
if [ -n "$npx_hits" ]; then
  fail "npx used without --no-install (reaches the registry on cache miss):"
  echo "$npx_hits" | sed 's/^/        /'
else
  pass "no network-reaching npx in build scripts"
fi

# 3. Compose demo network must be internal. This is the load-bearing line of
#    docker-compose.yml -- without it every container has a route out.
if grep -qE '^[[:space:]]*internal:[[:space:]]*true' "$ROOT/docker-compose.yml"; then
  pass "docker-compose.yml declares an internal network"
else
  fail "docker-compose.yml network is not internal: true"
fi

# 3b. And the dev compose file must NOT be internal, or three-laptop dev breaks
#     in a way that looks like a code bug.
if grep -qE '^[[:space:]]*internal:[[:space:]]*true' "$ROOT/docker-compose.dev.yml"; then
  fail "docker-compose.dev.yml is internal: true -- LAN dev cannot work"
else
  pass "docker-compose.dev.yml is not internal (correct for LAN dev)"
fi

echo "airgap-check: runtime checks"

# 4. Containers on the workbench network must not resolve or reach anything.
if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
  net="$(docker network ls --format '{{.Name}}' | grep -E 'workbench' | head -1 || true)"
  if [ -n "$net" ]; then
    if docker run --rm --network "$net" busybox:latest \
         sh -c 'ping -c1 -W2 1.1.1.1 >/dev/null 2>&1' 2>/dev/null; then
      fail "container on $net reached 1.1.1.1"
    else
      pass "container on $net cannot reach 1.1.1.1"
    fi
    if docker run --rm --network "$net" busybox:latest \
         sh -c 'nslookup example.com >/dev/null 2>&1' 2>/dev/null; then
      fail "container on $net resolved DNS"
    else
      pass "container on $net cannot resolve DNS"
    fi
  else
    echo "  SKIP  workbench network not up (run 'make demo' first)"
  fi
else
  echo "  SKIP  docker unavailable, runtime checks not run"
fi

# 5. The backend's own egress counters.
if command -v curl >/dev/null 2>&1; then
  status="$(curl -fsS --max-time 2 http://localhost:8000/api/network/status 2>/dev/null || true)"
  if [ -n "$status" ]; then
    echo "  INFO  /api/network/status -> $status"
  else
    echo "  SKIP  backend not running or /api/network/status not implemented yet"
  fi
fi

echo
if [ "$fails" -ne 0 ]; then
  echo "airgap-check: $fails FAILURE(S)"
  exit 1
fi
echo "airgap-check: all checks passed"
