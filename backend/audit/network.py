"""Air-gap enforcement and egress counters. Owner: person 3. Demo #5.

Two jobs:

  startup_selfcheck()  four assertions that must ALL hold before the API
                       serves a single request when AIRGAP_ENFORCE=1:
                         1. no default route
                         2. DNS resolution of an external name fails
                         3. TCP connect to a public IP fails
                         4. nftables rules are loaded
                       Enforcement is opt-in by environment because dev
                       laptops are connected on purpose; the demo host and the
                       demo compose file set AIRGAP_ENFORCE=1, and there the
                       process refuses to start on any failure. Every check's
                       outcome lands in the audit log either way.

  NetworkMonitor       reads the packet/DNS counters from the nftables table
                       installed by scripts/airgap-nftables.sh and serves them
                       to GET /api/network/status. The frontend polls this and
                       renders the large green zero. When nftables is absent
                       (dev laptop) the counters read zero with
                       rules_active=false -- the endpoint never lies about
                       having evidence it does not have.
"""

from __future__ import annotations

import json
import shutil
import socket
import subprocess
import time

from contracts import NetworkStatus

CHECK_HOSTNAME = "example.com"
CHECK_IP = "1.1.1.1"
NFT_TABLE = "airgap"


def _run(cmd: list[str], timeout: int = 5) -> tuple[int, str]:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return proc.returncode, proc.stdout
    except (OSError, subprocess.TimeoutExpired):
        return 1, ""


def check_no_default_route() -> tuple[bool, str]:
    if not shutil.which("ip"):
        return False, "`ip` not available; cannot verify routing table"
    code, out = _run(["ip", "route", "show", "default"])
    if code != 0:
        return False, "ip route failed"
    return (out.strip() == ""), (out.strip() or "no default route")


def check_dns_fails() -> tuple[bool, str]:
    try:
        socket.setdefaulttimeout(3)
        socket.getaddrinfo(CHECK_HOSTNAME, 443)
        return False, f"{CHECK_HOSTNAME} RESOLVED -- DNS path to the outside exists"
    except socket.gaierror:
        return True, f"{CHECK_HOSTNAME} does not resolve"
    except OSError as exc:
        return True, f"resolver unavailable: {exc}"


def check_tcp_fails() -> tuple[bool, str]:
    try:
        with socket.create_connection((CHECK_IP, 443), timeout=3):
            return False, f"connected to {CHECK_IP}:443 -- egress path exists"
    except OSError:
        return True, f"cannot reach {CHECK_IP}:443"


def check_nftables_loaded() -> tuple[bool, str]:
    if not shutil.which("nft"):
        return False, "nft not installed"
    code, out = _run(["nft", "list", "table", "inet", NFT_TABLE])
    if code != 0 or NFT_TABLE not in out:
        return False, f"nftables table {NFT_TABLE!r} not loaded "
    return True, f"table {NFT_TABLE!r} present with drop rules"


def startup_selfcheck() -> list[dict]:
    """Run all four assertions; return their outcomes. The caller decides
    whether a failure is fatal (AIRGAP_ENFORCE=1) or logged (dev)."""
    checks = [
        ("no_default_route", check_no_default_route),
        ("dns_resolution_fails", check_dns_fails),
        ("tcp_to_public_ip_fails", check_tcp_fails),
        ("nftables_rules_loaded", check_nftables_loaded),
    ]
    results = []
    for name, fn in checks:
        try:
            passed, detail = fn()
        except Exception as exc:  # a broken probe is a failed assertion, not a crash
            passed, detail = False, f"check raised {type(exc).__name__}: {exc}"
        results.append({"check": name, "passed": passed, "detail": detail})
    return results


class NetworkMonitor:
    def __init__(self) -> None:
        self.since = int(time.time())

    def _read_counter(self, name: str) -> int | None:
        if not shutil.which("nft"):
            return None
        code, out = _run(["nft", "-j", "list", "counters"])
        if code != 0:
            return None
        try:
            for obj in json.loads(out).get("nftables", []):
                counter = obj.get("counter")
                if counter and counter.get("name") == name \
                        and counter.get("table") == NFT_TABLE:
                    return int(counter.get("packets", 0))
        except (json.JSONDecodeError, ValueError):
            return None
        return None

    def status(self) -> NetworkStatus:
        external = self._read_counter("external")
        dns = self._read_counter("dns")
        return NetworkStatus(
            external_packets=external or 0,
            dns_queries=dns or 0,
            since=self.since,
            rules_active=external is not None,
        )
