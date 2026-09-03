#!/bin/bash
# Fetch the public-domain refinery document set used by the QA/red-team runs.
# Owner: person 3.
#
# Setup-time only: this touches the network and must never run on the demo
# host. The files (~46 MB) are gitignored; run this once on a connected machine,
# then ingest with:
#     for f in demo/datasets/refinery/*; do curl -X POST http://127.0.0.1:8001/documents/upload -F "file=@$f"; done
#
# Sources are US government publications (public domain):
#   CSB   -- Chemical Safety Board investigation reports (csb.gov)
#   OSHA  -- Process Safety Management guidance (osha.gov; needs browser headers)
#   EIA   -- Refinery Capacity Report 2026, individual-refinery XLSX + tables
set -u
D="$(cd "$(dirname "$0")/.." && pwd)/demo/datasets/refinery"
mkdir -p "$D"; cd "$D"
UA="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
get() {  # name url
  [ -s "$1" ] && { printf "%-50s present\n" "$1"; return; }
  code=$(curl -sL -m 600 --retry 3 --retry-all-errors --compressed -A "$UA" -H "Accept: */*" -o "$1" -w "%{http_code}" "$2")
  ct=$(file -b "$1" | cut -c1-24)
  printf "%-50s %s %s\n" "$1" "$code" "$ct"
  case "$ct" in PDF*|Microsoft*|Zip*) ;; *) rm -f "$1";; esac
}
get CSB-BP-Texas-City-2005-final-report.pdf      "https://www.csb.gov/assets/1/20/csbfinalreportbp.pdf"
get CSB-Chevron-Richmond-2012-final-report.pdf   "https://www.csb.gov/assets/1/20/chevron_final_investigation_report_2015-01-28.pdf"
get CSB-Chevron-Richmond-2012-interim-report.pdf "https://www.csb.gov/assets/1/20/chevron_interim_report_final_2013-04-17.pdf"
get CSB-Chevron-Richmond-regulatory-report.pdf   "https://www.csb.gov/assets/1/20/chevron_regulatory_report_06272014.pdf"
get CSB-PES-Philadelphia-2019-final-report.pdf   "https://www.csb.gov/assets/1/6/pes_final_report_published_october_2022.pdf"
get CSB-PES-Philadelphia-2019-factual-update.pdf "https://www.csb.gov/assets/1/6/pes_factual_update_-_final.pdf"
get CSB-Husky-Superior-2018-FCC-explosion.pdf    "https://www.csb.gov/assets/1/6/husky_superior_refinery_report_2022-12-23_(1).pdf"
get CSB-Remote-Isolation-safety-study-2024.pdf   "https://www.csb.gov/assets/1/6/csb_ripe_study_finalv.pdf"
get OSHA-3132-Process-Safety-Management.pdf      "https://www.osha.gov/sites/default/files/publications/osha3132.pdf"
get OSHA-3133-PSM-Guidelines.pdf                 "https://www.osha.gov/sites/default/files/publications/osha3133.pdf"
get EIA-refinery-capacity-2026-by-refinery.xlsx  "https://www.eia.gov/petroleum/refinerycapacity/refcap26.xlsx"
get EIA-refinery-capacity-2026-report.pdf        "https://www.eia.gov/petroleum/refinerycapacity/refcap26.pdf"
get EIA-2026-table3-capacity-by-refinery.pdf     "https://www.eia.gov/petroleum/refinerycapacity/table3.pdf"
get EIA-2026-table11-new-shutdown-refineries.pdf "https://www.eia.gov/petroleum/refinerycapacity/table11.pdf"
get EIA-2026-table13-shutdown-refineries.pdf     "https://www.eia.gov/petroleum/refinerycapacity/table13.pdf"
get EIA-820-refinery-capacity-notes.pdf          "https://www.eia.gov/petroleum/refinerycapacity/820notes.pdf"
echo; ls -l "$D" | awk 'NR>1{printf "  %-50s %9d\n", $9, $5}'
