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
# The pyright version is pinned in .github/requirements.txt, not here, so
# Dependabot's pip ecosystem renews it (dependency-management Freshness). The
# install runs unconditionally and the engine is invoked as `python3 -m pyright`,
# so the pinned build is what executes even on a machine carrying some other
# pyright earlier on PATH. It ships from PyPI, so CI needs no Node toolchain.
set -euo pipefail

REQUIREMENTS=".github/requirements.txt"
TESTS_DIR="skills/frequent-flyer-advocate/tests"

if [ ! -f "${REQUIREMENTS}" ]; then
  echo "error: ${REQUIREMENTS} is missing — it carries the pyright pin this gate runs." >&2
  echo "       Restore it from git (git checkout ${REQUIREMENTS}) and rerun." >&2
  exit 1
fi

echo "::group::pyright"
python3 -m pip install --quiet --disable-pip-version-check -r "${REQUIREMENTS}"
python3 -m pyright --version   # the version that actually ran, for the CI log
# Scope and interpreter version come from pyrightconfig.json, not from flags here.
python3 -m pyright
echo "::endgroup::"

echo "::group::tracker storage-bootstrap suite"
python3 "${TESTS_DIR}/test_trackers.py"
echo "::endgroup::"

echo "::group::letter-fit suite"
python3 "${TESTS_DIR}/test_letter_fit.py"
echo "::endgroup::"

echo "All gates passed."
