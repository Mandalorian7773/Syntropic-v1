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


WIN_RULE_GROUP = "SIH-airgap"          # created by scripts/airgap-windows.ps1
_WIN_CACHE_TTL_S = 10.0                # firewall + event-log probes cost ~1 s


def _powershell(script: str, timeout: int = 8) -> tuple[int, str]:
    exe = shutil.which("powershell") or shutil.which("pwsh")
    if not exe:
        return 1, ""
    return _run([exe, "-NoProfile", "-NonInteractive", "-Command", script], timeout=timeout)


class NetworkMonitor:
    """Counters behind /api/network/status.

    Linux: the nftables counters installed by scripts/airgap-nftables.sh.
    Windows: the Defender Firewall rule group installed by
    scripts/airgap-windows.ps1, with dropped-connection audit events (5157)
    counted since startup. Both are read, never assumed -- when neither is in
    place the endpoint says rules_active=false and the panel shows INACTIVE,
    which is the truth and is better than a green zero nobody enforced.

    The Windows probes shell out to PowerShell, which is slow (~1 s), and the
    frontend polls this endpoint continuously; the result is cached for
    _WIN_CACHE_TTL_S so the poll never stalls the event loop the SSE stream
    runs on.
    """

    def __init__(self) -> None:
        self.since = int(time.time())
        self._win_cache: tuple[float, bool, int, int] | None = None

    # --- Windows ---------------------------------------------------------------

    def _windows_probe(self) -> tuple[bool, int, int]:
        """(rules_active, blocked_total, blocked_dns) from the firewall + Security log."""
        if self._win_cache is not None:
            stamp, active, total, dns = self._win_cache
            if time.monotonic() - stamp < _WIN_CACHE_TTL_S:
                return active, total, dns
        since_iso = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(self.since))
        script = (
            f"$g = Get-NetFirewallRule -Group '{WIN_RULE_GROUP}' -ErrorAction SilentlyContinue "
            f"| Where-Object {{ $_.Enabled -eq 'True' -and $_.Direction -eq 'Outbound' }}; "
            f"$active = if (@($g).Count -gt 0) {{ 1 }} else {{ 0 }}; "
            f"$ev = @(Get-WinEvent -FilterHashtable @{{LogName='Security'; Id=5157; "
            f"StartTime=[datetime]'{since_iso}'}} -ErrorAction SilentlyContinue); "
            f"$dns = @($ev | Where-Object {{ $_.Message -match 'Destination Port:\\s+53\\b' }}).Count; "
            f"Write-Output \"$active $($ev.Count) $dns\""
        )
        code, out = _powershell(script, timeout=8)
        active, total, dns = False, 0, 0
        if code == 0:
            parts = out.strip().split()
            if len(parts) == 3 and all(p.isdigit() for p in parts):
                active, total, dns = parts[0] == "1", int(parts[1]), int(parts[2])
        self._win_cache = (time.monotonic(), active, total, dns)
        return active, total, dns

    # --- Linux -----------------------------------------------------------------

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
        if shutil.which("nft") is None and shutil.which("powershell"):
            # Windows host: Defender Firewall rule group + WFP audit events.
            active, total, dns = self._windows_probe()
            return NetworkStatus(
                external_packets=total if active else 0,
                dns_queries=dns if active else 0,
                since=self.since,
                rules_active=active,
            )
        external = self._read_counter("external")
        dns = self._read_counter("dns")
        return NetworkStatus(
            external_packets=external or 0,
            dns_queries=dns or 0,
            since=self.since,
            rules_active=external is not None,
        )
