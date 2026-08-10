#!/usr/bin/env bash
# Pre-publish gate for jbaruch/frequent-flyer-advocate.
#
# Invoked by .github/workflows/publish.yml via the reusable publish workflow's
# `pre-publish-script` input, after checkout and before the tessl publish steps.
# A non-zero exit here fails the publish, so no untested tracker or letter-fit
# change reaches the registry (testing-standards: tests run in CI).
#
# Each suite runs standalone under python3 and is also pytest-discoverable; the
# standalone runner is used so CI needs no test-framework dependency. Both print
# a per-test PASS/FAIL line and an "N/M passed" tail, and exit non-zero on any
# failure.
set -euo pipefail

TESTS_DIR="skills/frequent-flyer-advocate/tests"

echo "::group::tracker storage-bootstrap suite"
python3 "${TESTS_DIR}/test_trackers.py"
echo "::endgroup::"

echo "::group::letter-fit suite"
python3 "${TESTS_DIR}/test_letter_fit.py"
echo "::endgroup::"

echo "All suites passed."
