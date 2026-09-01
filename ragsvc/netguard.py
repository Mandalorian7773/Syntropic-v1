"""In-process egress guard. Owner: person 2.

`scripts/airgap-check.sh` proves the *container* has no route out. This module
proves the *process* never even tries, which is a different and stronger claim:
it survives someone running ragsvc outside Docker on a laptop with Wi-Fi on,
which is exactly how it will be run during the build week.

The rule is address-based, not name-based. Loopback, RFC1918 and link-local are
allowed, because that is where qdrant, llama-server and the backend live. Every
other destination raises `EgressBlocked` before a packet leaves.

Counters are exposed so /health can report them, which is the same number the
frontend's network indicator shows for the backend.
"""

from __future__ import annotations

import ipaddress
import socket
import threading

_lock = threading.Lock()
_installed = False

blocked_attempts: list[str] = []
allowed_count = 0
# Every hostname the process asks to resolve. Resolution itself is not blocked
# -- on the compose network "qdrant" and "llama-server" are names that must
# resolve -- but a name being looked up at all is worth seeing, because it is
# the first observable step of anything trying to leave. Enforcement stays at
# connect(), where a real IP can be judged.
lookups: list[str] = []


class EgressBlocked(OSError):
    """Raised instead of connecting to an address outside the local network."""


def _is_local(host: str) -> bool:
    """True for addresses the workbench is allowed to talk to."""
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        # A hostname, not an IP. Compose service names and localhost reach here
        # only when a library skips getaddrinfo; resolution itself is harmless,
        # and the connect that follows carries a real IP we will check then.
        return host in {"localhost", "localhost.localdomain"} or host.endswith(
            (".local", ".internal")
        )
    return (
        addr.is_loopback
        or addr.is_private
        or addr.is_link_local
        or addr.is_unspecified
    )


def install() -> None:
    """Patch socket.connect. Idempotent, so importing twice is harmless."""
    global _installed
    with _lock:
        if _installed:
            return

        real_connect = socket.socket.connect
        real_connect_ex = socket.socket.connect_ex

        def _check(sock: socket.socket, address) -> None:
            global allowed_count
            # AF_UNIX addresses are plain paths, never remote.
            if getattr(sock, "family", None) == getattr(socket, "AF_UNIX", None):
                return
            if not isinstance(address, tuple) or not address:
                return
            host = str(address[0])
            if _is_local(host):
                allowed_count += 1
                return
            target = f"{host}:{address[1] if len(address) > 1 else '?'}"
            blocked_attempts.append(target)
            raise EgressBlocked(
                f"ragsvc egress blocked: {target}. This service is air-gapped by "
                f"design; nothing may leave the host. Set RAG_NETGUARD=0 only on "
                f"a connected machine during setup."
            )

        def guarded_connect(self, address):  # noqa: ANN001
            _check(self, address)
            return real_connect(self, address)

        def guarded_connect_ex(self, address):  # noqa: ANN001
            _check(self, address)
            return real_connect_ex(self, address)

        real_getaddrinfo = socket.getaddrinfo

        def watched_getaddrinfo(host, *args, **kwargs):  # noqa: ANN001
            if host and not _is_local(str(host)):
                lookups.append(str(host))
            return real_getaddrinfo(host, *args, **kwargs)

        socket.socket.connect = guarded_connect  # type: ignore[method-assign]
        socket.socket.connect_ex = guarded_connect_ex  # type: ignore[method-assign]
        socket.getaddrinfo = watched_getaddrinfo  # type: ignore[assignment]
        _installed = True


def status() -> dict:
    """Egress counters, for /health."""
    return {
        "active": _installed,
        "blocked": len(blocked_attempts),
        "last_blocked": blocked_attempts[-1] if blocked_attempts else None,
        "local_connections": allowed_count,
        "name_lookups": len(lookups),
        "names": sorted(set(lookups))[:10],
    }
