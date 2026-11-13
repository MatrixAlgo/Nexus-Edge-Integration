#!/usr/bin/env bash
# Start / stop / status of the standalone integration server IS_D5_EDGE (runs as aceadmin, transient systemd unit d5-ace).
#   IS_D5_EDGE   http 7840   admin REST 7614   queue manager D5QM (local bindings)
set -uo pipefail
ACE=/root/IBM/ACE/ace-13.0.9.0
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
S=IS_D5_EDGE; W="$ROOT/runtime/$S"; HTTP=7840; ADMIN=7614; QM=D5QM; UNIT=d5-ace
log="$ROOT/logs/ace-IS_D5_EDGE.console.log"
listening() { ss -ltn 2>/dev/null | grep -qE ":${ADMIN} "; }
start() {
  listening && { echo "$S already running (admin $ADMIN)"; return 0; }
  if [ ! -d "$W" ]; then
    runuser -u aceadmin -- bash -c ". $ACE/server/bin/mqsiprofile >/dev/null 2>&1; mqsicreateworkdir '$W'" >/dev/null
  fi
  install -o aceadmin -g aceadmin -m 0644 /dev/null "$log"
  systemctl reset-failed $UNIT.service >/dev/null 2>&1 || true
  systemd-run --quiet --unit=$UNIT --uid=aceadmin --gid=aceadmin --property=KillMode=mixed \
    --setenv=HOME=/home/aceadmin --working-directory="$W" \
    bash -c ". $ACE/server/bin/mqsiprofile >/dev/null 2>&1; exec IntegrationServer --name $S --work-dir '$W' \
       --admin-rest-api $ADMIN --http-port-number $HTTP --mq-queue-manager-name $QM >> '$log' 2>&1"
  for _ in $(seq 1 90); do grep -qE 'BIP1991I|BIP2228E' "$log" && break; sleep 2; done
  if grep -q BIP1991I "$log"; then echo "$S started (http $HTTP, admin $ADMIN, qmgr $QM)"; else echo "$S FAILED - see $log"; tail -5 "$log"; return 1; fi
}
stop() { systemctl stop $UNIT.service 2>/dev/null && echo "$S stopped" || echo "$S not running"; }
status() { listening && echo "$S running (http $HTTP, admin $ADMIN, qmgr $QM)" || echo "$S stopped"; }
case ${1:-status} in start) start;; stop) stop;; restart) stop; sleep 2; start;; status) status;; *) echo "usage: $0 start|stop|restart|status"; exit 2;; esac
