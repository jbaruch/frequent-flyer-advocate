#!/usr/bin/env python3
"""Regenerate .github/requirements.txt as a fully resolved, hash-pinned lock.

dependency-management Pinning wants pinned versions or a committed lock. Pinning the top
package alone leaves its transitive tree floating, so the CI gate was not reproducible.

Resolution comes from pip itself (`--dry-run --report`), so the lock matches what pip would
actually install rather than a hand-maintained guess. Hashes come from the PyPI JSON API and
cover every distribution published for each pinned version, so the lock is valid on any
platform the gate runs on — not only the one that generated it.

Usage:
  python3 .github/scripts/lock-requirements.py            # relock the versions in TOP_LEVEL
  python3 .github/scripts/lock-requirements.py pyright==1.1.409   # relock at a new version

Run this after a Dependabot bump, then commit the result.

Nothing is written until every resolution and lookup has succeeded, so a network failure
leaves the committed lock untouched rather than truncated. Exit 0 on success, 1 on any
failure, each with an actionable diagnostic on stderr.
"""

import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
# Dependabot's pip ecosystem is configured for /.github, so the lock lives there,
# one level above this script.
LOCK_PATH = os.path.normpath(os.path.join(HERE, "..", "requirements.txt"))

# The gate's direct dependency. Everything else in the lock is pip's resolution of this.
# Must match the version pinned in the committed lock — test_lock_requirements.py fails
# if they drift, since a no-argument regeneration would otherwise silently revert a bump
# Dependabot applied to the lock alone.
TOP_LEVEL = ["pyright==1.1.411"]

HEADER = """\
# Fully resolved, hash-pinned dependency set for .github/scripts/pre-publish-gate.sh.
#
# Generated — do not hand-edit. Regenerate with:
#   python3 .github/scripts/lock-requirements.py
#
# The gate installs with --require-hashes, so an artifact whose digest is not listed here
# aborts the run rather than executing. Dependabot bumps the top-level pin; rerun the
# generator to relock the transitive tree behind it.
"""

# Distribution kinds pip may select. Both are hashed so the lock holds whichever it picks.
DIST_TYPES = ("bdist_wheel", "sdist")


class LockError(Exception):
    """A failure the operator can act on. Carries the message printed to stderr."""


def parse_report(report):
    """Pull {normalized name: version} out of a `pip install --report` document."""
    try:
        installs = report["install"]
    except (TypeError, KeyError) as e:
        raise LockError(
            f"pip's resolution report has no 'install' list ({e}). "
            f"Check that pip is recent enough to support --report (pip >= 22.2)."
        ) from e
    pins = {}
    for item in installs:
        try:
            meta = item["metadata"]
            pins[meta["name"].lower().replace("_", "-")] = meta["version"]
        except (TypeError, KeyError) as e:
            raise LockError(
                f"a package in pip's resolution report is missing name/version ({e}). "
                f"Rerun, and report the pip version if it persists."
            ) from e
    if not pins:
        raise LockError("pip resolved no packages. Check the specifier passed to this script.")
    return pins


def resolve(specs, runner=subprocess.run):
    """Ask pip what it would install, without installing it."""
    with tempfile.TemporaryDirectory() as tmp:
        report_path = os.path.join(tmp, "report.json")
        try:
            runner(
                [sys.executable, "-m", "pip", "install", "--dry-run", "--quiet",
                 "--disable-pip-version-check", "--ignore-installed",
                 "--report", report_path, *specs],
                check=True,
            )
        except subprocess.CalledProcessError as e:
            raise LockError(
                f"pip could not resolve {' '.join(specs)} (exit {e.returncode}). "
                f"Check the version exists on PyPI and that this machine has network access."
            ) from e
        try:
            with open(report_path, encoding="utf-8") as f:
                report = json.load(f)
        except FileNotFoundError as e:
            raise LockError(
                "pip wrote no resolution report. Upgrade pip (--report needs pip >= 22.2)."
            ) from e
        except json.JSONDecodeError as e:
            raise LockError(f"pip's resolution report is not valid JSON ({e}).") from e
    return parse_report(report)


def fetch_pypi(name, version):
    """Fetch a release's PyPI metadata.

    Shells out to curl: urllib cannot verify PyPI's certificate chain on some macOS
    Pythons, and curl is present wherever this script runs.
    """
    url = f"https://pypi.org/pypi/{name}/{version}/json"
    try:
        proc = subprocess.run(["curl", "-fsSL", url],
                              capture_output=True, text=True, check=True)
    except FileNotFoundError as e:
        raise LockError("curl is not installed, and this script needs it to reach PyPI. "
                        "Install curl and rerun.") from e
    except subprocess.CalledProcessError as e:
        raise LockError(
            f"could not fetch {url} (curl exit {e.returncode}). "
            f"Check network access, and that {name}=={version} is published on PyPI."
        ) from e
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        raise LockError(
            f"PyPI returned something other than JSON for {name}=={version} ({e}). "
            f"Retry; if it persists, check https://status.python.org/."
        ) from e


def digests_for(name, version, fetch=fetch_pypi):
    """Every sha256 PyPI publishes for this release, wheels and sdists alike."""
    data = fetch(name, version)
    try:
        files = data["urls"]
    except (TypeError, KeyError) as e:
        raise LockError(f"PyPI's response for {name}=={version} has no file list ({e}).") from e
    digests = sorted({f["digests"]["sha256"] for f in files
                      if f.get("packagetype") in DIST_TYPES})
    if not digests:
        raise LockError(
            f"PyPI lists no wheel or sdist for {name}=={version}. "
            f"Pick a version that publishes one."
        )
    return digests


def render_lock(entries):
    """Render {name: [digest, ...]} pins as a pip requirements file with hashes."""
    lines = [HEADER]
    for name, (version, digests) in sorted(entries.items()):
        lines.append(f"{name}=={version} \\")
        for i, digest in enumerate(digests):
            trailer = "" if i == len(digests) - 1 else " \\"
            lines.append(f"    --hash=sha256:{digest}{trailer}")
        lines.append("")
    return "\n".join(lines)


def build_lock(specs, resolver=resolve, fetch=fetch_pypi):
    """Resolve and look up everything before rendering, so a failure writes nothing."""
    pins = resolver(specs)
    return {name: (version, digests_for(name, version, fetch=fetch))
            for name, version in pins.items()}


def main(argv=None):
    specs = list(argv if argv is not None else sys.argv[1:]) or TOP_LEVEL
    try:
        entries = build_lock(specs)
    except LockError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        print(f"       {LOCK_PATH} was left unchanged.", file=sys.stderr)
        return 1

    with open(LOCK_PATH, "w", encoding="utf-8") as f:
        f.write(render_lock(entries))
    print(f"locked {len(entries)} package(s) into {LOCK_PATH}: "
          + ", ".join(f"{n}=={v}" for n, (v, _) in sorted(entries.items())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
