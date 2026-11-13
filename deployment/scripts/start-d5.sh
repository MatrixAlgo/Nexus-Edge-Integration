#!/usr/bin/env bash
# Start the complete D5 demo: D5QM -> IS_D5_EDGE (+ BAR if not deployed) -> central -> satellite sim -> 2 edge agents.
# Touches only D5 resources (D5QM, IS_D5_EDGE, d5-* transient systemd units). Existing QMs / ACE servers are untouched.
set -uo pipefail
. "$(dirname "$0")/common.sh"
need_root
echo "[1/4] IBM MQ queue manager D5QM"
"$D5/mq/scripts/setup-mq.sh" | tail -1 || exit 1
echo "[2/4] IBM ACE integration server IS_D5_EDGE"
"$D5/ace/deployment/is-d5-edge.sh" start || exit 1
if ! curl -sk -m 5 https://127.0.0.1:7614/apiv2/applications | grep -q D5_EdgeSync; then
  [ -f "$D5/ace/bar/D5_EdgeSync.bar" ] && "$D5/ace/deployment/build-deploy.sh" deploy || "$D5/ace/deployment/build-deploy.sh" all
fi
echo "[3/4] D5 services"
install -d -o $SVC_USER -g $SVC_USER "$D5/logs"
for s in "${SERVICES[@]}"; do IFS='|' read -r name cmd health props <<<"$s"; start_unit "$name" "$cmd" "$health" "$props" || exit 1; done
echo "[4/4] status"
"$(dirname "$0")/status-d5.sh"
echo
echo "Dashboard:  http://127.0.0.1:${DASHBOARD_PORT}/   (bound to localhost; remote: ssh -L ${DASHBOARD_PORT}:127.0.0.1:${DASHBOARD_PORT} <user>@<server>)"
