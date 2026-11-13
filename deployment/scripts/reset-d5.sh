#!/usr/bin/env bash
# Reset the D5 demo to a clean state: stops D5 services, deletes ONLY D5 demo data (central + vessel SQLite files,
# link state, D5 logs), clears ONLY the D5.* queues on D5QM, then restarts and seeds the baseline records.
#   reset-d5.sh [-y] [--no-seed]
set -uo pipefail
. "$(dirname "$0")/common.sh"
need_root
if [ "${1:-}" != "-y" ]; then read -r -p "Delete all D5 demo data (D5 databases, D5.* queue contents)? [y/N] " a; [ "$a" = y ] || exit 1; else shift; fi
for ((i=${#SERVICES[@]}-1; i>=0; i--)); do IFS='|' read -r name _ <<<"${SERVICES[$i]}"; stop_unit "$name"; done
"$D5/ace/deployment/is-d5-edge.sh" stop   # releases the input handles so the queues can be cleared
rm -f "$D5"/central-office/database/central.db* "$D5"/network-simulator/link-state.json
rm -rf "$D5"/vessel-simulator/vessel-0*/data
find "$D5/logs" -maxdepth 1 -name '*.log' ! -name 'ace-*' -delete
"$D5/mq/scripts/setup-mq.sh" | tail -1
for q in D5.IN.CRITICAL D5.IN.NORMAL D5.IN.LOW D5.OUT.ACK D5.ERROR D5.DLQ; do echo "CLEAR QLOCAL('$q')"; done \
  | su mqm -s /bin/bash -c '/opt/mqm/bin/runmqsc D5QM' | grep -cE 'AMQ8022I' | xargs -I{} echo "  cleared {} D5 queues"
"$(dirname "$0")/start-d5.sh" || exit 1
if [ "${1:-}" != "--no-seed" ]; then runuser -u $SVC_USER -- $PY "$D5/demo/seed_demo.py"; fi
