# Air-gap enforcement for a Windows demo host. Owner: person 3.
#
# The Linux path is scripts/airgap-nftables.sh: an nftables table whose
# counters the backend reads for /api/network/status. Windows has no nft, so
# this is the equivalent built from what Windows does have:
#
#   1. Windows Defender Firewall OUTBOUND BLOCK rules for every destination
#      outside RFC1918 / loopback, in a rule group the backend can look for.
#   2. Audit Filtering Platform Connection *failures* switched on, so every
#      packet those rules drop lands in the Security log as event 5157.
#
# backend/audit/network.py then reports rules_active=true when the group
# exists and is enabled, and counts 5157 events since backend startup as
# external_packets (all of them) and dns_queries (those to port 53). The panel
# therefore shows a measured zero, not an assumed one, and turns red the
# moment anything tries to leave -- which is required demonstration #5.
#
# Run ONCE, from an elevated PowerShell:
#     Set-ExecutionPolicy -Scope Process Bypass; .\scripts\airgap-windows.ps1
# Undo with:
#     .\scripts\airgap-windows.ps1 -Remove
#
# It blocks outbound traffic for the WHOLE machine, not just the workbench.
# That is the point on demo day, and it is why it is a separate, explicit,
# elevated step rather than something the backend does for you at startup.

[CmdletBinding()]
param([switch]$Remove)

$ErrorActionPreference = "Stop"
$group = "SIH-airgap"

$principal = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Error "Run this from an elevated (Administrator) PowerShell."
}

if ($Remove) {
    Get-NetFirewallRule -Group $group -ErrorAction SilentlyContinue | Remove-NetFirewallRule
    auditpol /set /subcategory:"Filtering Platform Connection" /failure:disable | Out-Null
    Write-Host "removed $group rules and disabled Filtering Platform Connection failure auditing"
    exit 0
}

# Everything that is NOT private address space. Windows firewall rules take an
# allow-list of remote addresses per rule, so "block everything except private"
# is expressed as a block rule on the public ranges between the private ones.
$public = @(
    "1.0.0.0-9.255.255.255",
    "11.0.0.0-100.63.255.255",       # 10/8 is private; 100.64/10 is CGNAT, treat as inside
    "100.128.0.0-126.255.255.255",   # 127/8 is loopback
    "128.0.0.0-169.253.255.255",     # 169.254/16 link-local
    "169.255.0.0-172.15.255.255",    # 172.16/12 private
    "172.32.0.0-192.167.255.255",    # 192.168/16 private
    "192.169.0.0-223.255.255.255"
)

Get-NetFirewallRule -Group $group -ErrorAction SilentlyContinue | Remove-NetFirewallRule

New-NetFirewallRule -DisplayName "SIH airgap: block outbound to public IPv4" -Group $group `
    -Direction Outbound -Action Block -Profile Any -Enabled True `
    -RemoteAddress $public | Out-Null
New-NetFirewallRule -DisplayName "SIH airgap: block outbound IPv6" -Group $group `
    -Direction Outbound -Action Block -Profile Any -Enabled True `
    -RemoteAddress "2000::/3" | Out-Null
New-NetFirewallRule -DisplayName "SIH airgap: block outbound DNS" -Group $group `
    -Direction Outbound -Action Block -Profile Any -Enabled True `
    -Protocol UDP -RemotePort 53 -RemoteAddress $public | Out-Null

auditpol /set /subcategory:"Filtering Platform Connection" /failure:enable | Out-Null

Write-Host "installed $group rules:"
Get-NetFirewallRule -Group $group | Select-Object DisplayName, Enabled, Action | Format-Table -AutoSize
Write-Host "Filtering Platform Connection failure auditing: enabled (events 5157 in the Security log)"
Write-Host ""
Write-Host "Verify from another shell:  curl http://127.0.0.1:8000/api/network/status  -> rules_active should be true"
Write-Host "Prove it works:             curl -m 5 https://example.com        -> should fail, and external_packets should rise"
