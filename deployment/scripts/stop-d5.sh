#!/usr/bin/env bash
# Stop the D5 demo. By default D5QM keeps running (cheap, holds no demo state that must be stopped).
#   stop-d5.sh [--with-qm] [-h|--help]
#   --with-qm   also end queue manager D5QM (endmqm -w D5QM), waiting at most QM_STOP_TIMEOUT seconds (default 60).
#               On timeout nothing is forced: the script reports it and exits non-zero.
# Stop order: edge agents -> IS_D5_EDGE -> satellite simulator -> central office.
#   ACE stops BEFORE the central office so no message in flight is rolled back and parked on D5.ERROR.
# Exit code: 0 = everything requested is stopped, 1 = a component failed to stop, 2 = usage error.
# No other queue manager or ACE server is touched.
set -uo pipefail
. "$(dirname "$0")/common.sh"
QM=D5QM
QM_STOP_TIMEOUT=${QM_STOP_TIMEOUT:-60}
usage() { echo "usage: $(basename "$0") [--with-qm] [-h|--help]   (env QM_STOP_TIMEOUT=<seconds>, default 60)"; }

WITH_QM=0
for arg in "$@"; do
  case $arg in
    --with-qm) WITH_QM=1 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown option: $arg" >&2; usage >&2; exit 2 ;;
  esac
done
case $QM_STOP_TIMEOUT in ''|*[!0-9]*|0) echo "QM_STOP_TIMEOUT must be a positive whole number of seconds" >&2; exit 2 ;; esac
need_root

rc=0
stop_checked() {  # unit name: stop it and verify it is really down
  local unit=$1 state
  state=$(systemctl is-active "$unit" 2>/dev/null)
  case $state in
    inactive|failed|unknown|'') ;;   # not running - nothing to stop
    *)
      systemctl stop "$unit"
      state=$(systemctl is-active "$unit" 2>/dev/null)
      case $state in
        inactive|failed|unknown|'') echo "  $unit stopped" ;;
        *) echo "  $unit FAILED to stop (state: $state) - journalctl -u $unit" >&2; rc=1 ;;
      esac ;;
  esac
  systemctl reset-failed "$unit" >/dev/null 2>&1 || true
}

# 1. edge agents first: no new events are sent to ACE
for ((i=${#SERVICES[@]}-1; i>=0; i--)); do
  IFS='|' read -r name _ <<<"${SERVICES[$i]}"
  [[ $name == d5-edge-* ]] && stop_checked "$name"
done

# 2. IS_D5_EDGE while the central office is still up, so in-flight messages complete instead of backing out
"$D5/ace/deployment/is-d5-edge.sh" stop
if systemctl is-active --quiet d5-ace.service || ss -ltn 2>/dev/null | grep -qE ':7614 '; then
  echo "  IS_D5_EDGE FAILED to stop (unit d5-ace still active or admin port 7614 still listening)" >&2; rc=1
fi

# 3. remaining D5 services (satellite simulator, then central office)
for ((i=${#SERVICES[@]}-1; i>=0; i--)); do
  IFS='|' read -r name _ <<<"${SERVICES[$i]}"
  [[ $name == d5-edge-* ]] || stop_checked "$name"
done

# 4. optionally the queue manager, with a bounded wait
if [ "$WITH_QM" = 1 ]; then
  if /opt/mqm/bin/dspmq -m $QM 2>/dev/null | grep -q 'STATUS(Running)'; then
    timeout -k 5 "$QM_STOP_TIMEOUT" su mqm -s /bin/bash -c "/opt/mqm/bin/endmqm -w $QM"
    ec=$?
    if [ $ec = 0 ]; then
      echo "  $QM ended"
    elif [ $ec = 124 ] || [ $ec = 137 ]; then
      echo "  $QM did NOT end within ${QM_STOP_TIMEOUT}s (an application is probably still connected). Nothing was forced." >&2
      echo "    current: $(/opt/mqm/bin/dspmq -m $QM 2>/dev/null)" >&2
      echo "    it may remain in a quiescing state and end once the connection closes; to end it immediately (your decision):" >&2
      echo "    su mqm -s /bin/bash -c '/opt/mqm/bin/endmqm -i $QM'" >&2
      rc=1
    else
      echo "  $QM FAILED to end (endmqm exit code $ec)" >&2; rc=1
    fi
  else
    echo "  $QM not running: $(/opt/mqm/bin/dspmq -m $QM 2>/dev/null | grep -oE 'STATUS\([^)]+\)' || echo 'not found')"
  fi
fi

[ $rc = 0 ] && echo "D5 stopped" || echo "D5 stop finished WITH ERRORS (exit code $rc)" >&2
exit $rc
