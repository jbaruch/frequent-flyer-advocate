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

Run this after a Dependabot bump, then commit the result. Exits non-zero if resolution or a
PyPI lookup fails, so a partial lock is never written.
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
TOP_LEVEL = ["pyright==1.1.408"]

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


def resolve(specs):
    """Ask pip what it would install, without installing it."""
    with tempfile.TemporaryDirectory() as tmp:
        report_path = os.path.join(tmp, "report.json")
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "--dry-run", "--quiet",
             "--disable-pip-version-check", "--ignore-installed",
             "--report", report_path, *specs],
            check=True,
        )
        with open(report_path, encoding="utf-8") as f:
            report = json.load(f)
    return {item["metadata"]["name"].lower().replace("_", "-"): item["metadata"]["version"]
            for item in report["install"]}


def digests_for(name, version):
    """Every sha256 PyPI publishes for this release, wheels and sdists alike.

    urllib cannot verify PyPI's chain on some macOS Pythons, so this shells out to curl,
    which is present wherever this script is run.
    """
    url = f"https://pypi.org/pypi/{name}/{version}/json"
    proc = subprocess.run(["curl", "-fsSL", url], capture_output=True, text=True, check=True)
    data = json.loads(proc.stdout)
    digests = sorted({f["digests"]["sha256"] for f in data["urls"]
                      if f["packagetype"] in ("bdist_wheel", "sdist")})
    if not digests:
        print(f"ERROR: PyPI lists no wheel or sdist for {name}=={version}.", file=sys.stderr)
        sys.exit(1)
    return digests


def main():
    specs = sys.argv[1:] or TOP_LEVEL
    pins = resolve(specs)

    lines = [HEADER]
    for name in sorted(pins):
        version = pins[name]
        digests = digests_for(name, version)
        lines.append(f"{name}=={version} \\")
        for i, digest in enumerate(digests):
            trailer = "" if i == len(digests) - 1 else " \\"
            lines.append(f"    --hash=sha256:{digest}{trailer}")
        lines.append("")

    with open(LOCK_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"locked {len(pins)} package(s) into {LOCK_PATH}: "
          + ", ".join(f"{n}=={v}" for n, v in sorted(pins.items())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
