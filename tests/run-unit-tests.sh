#!/usr/bin/env bash
# Unit tests (no running services needed). Output: logs/unit-tests.log
cd "$(dirname "$0")/.." || exit 1
export PYTHONDONTWRITEBYTECODE=1
python3 -m unittest -v central-office/tests/test_processing.py edge-runtime/tests/test_edge.py tests/unit/test_edgeconfig.py 2>&1 | tee logs/unit-tests.log
exit "${PIPESTATUS[0]}"
