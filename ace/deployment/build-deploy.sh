#!/usr/bin/env bash
# Generate the D5 flows, build the BAR headless (ibmint package) and deploy it to IS_D5_EDGE (admin 7614).
#   ace/deployment/build-deploy.sh [build|deploy|all]     (default all)
# Runs the IBM commands as aceadmin. Touches only ace/bar/D5_EdgeSync.bar and IS_D5_EDGE.
set -euo pipefail
ACE=/root/IBM/ACE/ace-13.0.9.0
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
BAR="$ROOT/ace/bar/D5_EdgeSync.bar"
as_ace() { if [ "$(id -un)" = aceadmin ]; then bash -c "$1"; else runuser -u aceadmin -- bash -c "$1"; fi; }
what=${1:-all}
if [[ $what == build || $what == all ]]; then
  as_ace "python3 '$ROOT/ace/flows/gen_flows.py' >/dev/null"
  as_ace ". $ACE/server/bin/mqsiprofile >/dev/null 2>&1; ibmint package --input-path '$ROOT/ace/projects' --project D5_EdgeSync \
          --output-bar-file '$BAR'" > "$ROOT/logs/ace-package.log" 2>&1 || { grep -E 'BIP|rror' "$ROOT/logs/ace-package.log" | head -20; exit 1; }
  echo "built $BAR"
fi
if [[ $what == deploy || $what == all ]]; then
  as_ace ". $ACE/server/bin/mqsiprofile >/dev/null 2>&1; ibmint deploy --input-bar-file '$BAR' --output-host 127.0.0.1 \
          --output-port 7614 --https --insecure" > "$ROOT/logs/ace-deploy.log" 2>&1 || { tail -20 "$ROOT/logs/ace-deploy.log"; exit 1; }
  grep -E 'BIP[0-9]+I' "$ROOT/logs/ace-deploy.log" | tail -3
  echo "deployed D5_EdgeSync to IS_D5_EDGE"
fi
