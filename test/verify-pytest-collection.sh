#!/bin/bash
# Verify that pytest collects the same tests as run-tests.
# Run from the anaconda-webui root directory.

set -euo pipefail

RUNTESTS=$(TEST_OS=fedora-rawhide-boot test/common/run-tests --test-dir test -l 2>&1 | sort)
PYTEST=$(python3 -m pytest --collect-only -q 2>&1 | grep "::" | sed 's|test/check_[^.]*\.py::\([^:]*\)::\(.*\)|\1.\2|' | sort)

RUNTESTS_COUNT=$(echo "$RUNTESTS" | wc -l)
PYTEST_COUNT=$(echo "$PYTEST" | wc -l)

if [ "$RUNTESTS_COUNT" != "$PYTEST_COUNT" ]; then
    echo "MISMATCH: run-tests=$RUNTESTS_COUNT pytest=$PYTEST_COUNT"
    diff <(echo "$RUNTESTS") <(echo "$PYTEST") || true
    exit 1
fi

echo "OK: both find $RUNTESTS_COUNT tests"
