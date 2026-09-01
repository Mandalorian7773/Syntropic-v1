#!/usr/bin/env bash
# Install the host-level nftables air-gap rules with named counters.
# Owner: person 3. Run as root on the DEMO HOST only, before `make demo`.
#
# What it does:
#   - DROPs any outbound packet not destined for loopback, RFC1918 space or
#     the docker bridge ranges
#   - counts every dropped packet in counter `external`, and every attempted
#     DNS query to a non-local resolver in counter `dns`
#
# backend/audit/network.py reads these two counters and serves them at
# GET /api/network/status; the frontend renders them as the large green zero.
# The counters counting DROPPED packets is the point: a nonzero number means
# something TRIED to leave and was stopped -- and the audit trail shows it.
#
# Remove with: nft delete table inet airgap
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "airgap-nftables: must run as root" >&2
  exit 1
fi

nft -f - <<'NFT'
table inet airgap {
    counter external { }
    counter dns { }

    chain output {
        type filter hook output priority 0; policy accept;

        # Loopback and private space are the workbench itself.
        oif "lo" accept
        ip daddr 127.0.0.0/8 accept
        ip daddr 10.0.0.0/8 accept
        ip daddr 172.16.0.0/12 accept
        ip daddr 192.168.0.0/16 accept
        ip6 daddr ::1 accept

        # DNS attempts to anywhere else: count, then fall through to the drop.
        meta l4proto { tcp, udp } th dport 53 counter name "dns"

        # Everything else outbound: count and drop, with a kernel log line so
        # the audit story has two independent witnesses.
        counter name "external" log prefix "airgap-drop " drop
    }
}
NFT

echo "airgap-nftables: table 'inet airgap' installed"
nft list table inet airgap
