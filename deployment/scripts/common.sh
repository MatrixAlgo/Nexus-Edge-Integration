# shared definitions for the D5 lifecycle scripts (sourced)
D5="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PY=/usr/bin/python3
SVC_USER=aceadmin
DASHBOARD_PORT=8500
# unit name | command (relative to $D5) | health URL | extra systemd properties
SERVICES=(
  "d5-central|central-office/app/server.py central-office/config/central.json|http://127.0.0.1:8500/health|"
  "d5-netsim|network-simulator/netsim.py network-simulator/netsim.json|http://127.0.0.1:8510/health|"
  "d5-edge-vessel-01|edge-runtime/agent/main.py vessel-simulator/vessel-01/edge.json|http://127.0.0.1:8501/health|-p CPUQuota=100% -p MemoryMax=512M -p MemorySwapMax=0"
  "d5-edge-vessel-02|edge-runtime/agent/main.py vessel-simulator/vessel-02/edge.json|http://127.0.0.1:8502/health|-p CPUQuota=100% -p MemoryMax=512M -p MemorySwapMax=0"
)
need_root() { [ "$(id -u)" = 0 ] || { echo "run as root (uses systemd-run / runuser; services run as $SVC_USER)"; exit 1; }; }
wait_http() { for _ in $(seq 1 ${2:-40}); do curl -sf -m 2 "$1" >/dev/null 2>&1 && return 0; sleep 0.5; done; return 1; }
start_unit() {  # name cmd health props
  local name=$1 cmd=$2 health=$3 props=$4
  if systemctl is-active --quiet "$name"; then echo "  $name already running"; return 0; fi
  systemctl reset-failed "$name" >/dev/null 2>&1 || true
  # shellcheck disable=SC2086
  systemd-run --quiet --unit="$name" --uid=$SVC_USER --gid=$SVC_USER --working-directory="$D5" \
    --setenv=PYTHONDONTWRITEBYTECODE=1 --setenv=PYTHONUNBUFFERED=1 -p Restart=on-failure -p RestartSec=2 $props \
    $PY $D5/$cmd
  if wait_http "$health"; then echo "  $name started"; else echo "  $name FAILED to become healthy - journalctl -u $name"; return 1; fi
}
stop_unit() { systemctl is-active --quiet "$1" && { systemctl stop "$1"; echo "  $1 stopped"; } || true; systemctl reset-failed "$1" >/dev/null 2>&1 || true; }
