#!/usr/bin/env bash
# Deterministic gate for jbaruch/frequent-flyer-advocate: diagnostics, then tests.
#
# Two callers run this exact script, so pre-merge and pre-publish cannot drift:
#   - .github/workflows/tests.yml       on every pull request (gates the merge)
#   - .github/workflows/publish.yml     on push to main, via the reusable publish
#                                       workflow's `pre-publish-script` input
#                                       (defense in depth, gates the registry)
#
# Order is diagnostics before tests per language-diagnostics CI Integration —
# a type error is cheaper to surface than a test failure that hides one.
#
# pyright is pinned; renew it as its own focused change (dependency-management
# Freshness). It ships from PyPI so CI needs no Node toolchain of its own.
set -euo pipefail

PYRIGHT_VERSION="1.1.408"
TESTS_DIR="skills/frequent-flyer-advocate/tests"

echo "::group::pyright ${PYRIGHT_VERSION}"
if ! command -v pyright >/dev/null 2>&1; then
  python3 -m pip install --quiet --disable-pip-version-check "pyright==${PYRIGHT_VERSION}"
fi
# Scope and interpreter version come from pyrightconfig.json, not from flags here.
pyright
echo "::endgroup::"

echo "::group::tracker storage-bootstrap suite"
python3 "${TESTS_DIR}/test_trackers.py"
echo "::endgroup::"

echo "::group::letter-fit suite"
python3 "${TESTS_DIR}/test_letter_fit.py"
echo "::endgroup::"

echo "All gates passed."
