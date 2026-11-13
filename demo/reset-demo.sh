#!/usr/bin/env bash
# Convenience wrapper - see deployment/scripts/reset-d5.sh
exec "$(dirname "$0")/../deployment/scripts/reset-d5.sh" "$@"
