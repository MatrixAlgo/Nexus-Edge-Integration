#!/usr/bin/env bash
# Create (if absent), start and configure the isolated D5 queue manager D5QM. Touches nothing else.
set -euo pipefail
QM=D5QM; HERE="$(cd "$(dirname "$0")/.." && pwd)"
run_mqm() { if [ "$(id -un)" = mqm ] || id -nG | grep -qw mqm; then bash -c "$1"; else su mqm -s /bin/bash -c "$1"; fi; }
if ! /opt/mqm/bin/dspmq -m $QM >/dev/null 2>&1; then
  [ "$(id -u)" = 0 ] || { echo "D5QM does not exist; run as root to create it"; exit 1; }
  su mqm -s /bin/bash -c "/opt/mqm/bin/crtmqm -u D5.DLQ $QM"
fi
/opt/mqm/bin/dspmq -m $QM | grep -q 'Running' || run_mqm "/opt/mqm/bin/strmqm $QM"
run_mqm "/opt/mqm/bin/runmqsc $QM" < "$HERE/queues.mqsc" | grep -E 'AMQ[0-9]+[EW]|commands? (read|have)' || true
echo "D5QM ready"
