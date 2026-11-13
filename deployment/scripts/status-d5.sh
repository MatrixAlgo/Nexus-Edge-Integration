#!/usr/bin/env bash
. "$(dirname "$0")/common.sh"
printf "  %-22s %s\n" "D5QM" "$(/opt/mqm/bin/dspmq -m D5QM 2>/dev/null | grep -oE 'STATUS\([^)]+\)')"
printf "  %-22s %s\n" "IS_D5_EDGE" "$("$D5/ace/deployment/is-d5-edge.sh" status)"
for s in "${SERVICES[@]}"; do IFS='|' read -r name cmd health props <<<"$s"
  printf "  %-22s %s\n" "$name" "$(systemctl is-active "$name" 2>/dev/null) $(curl -sf -m 2 "$health" >/dev/null && echo healthy || echo '')"; done
