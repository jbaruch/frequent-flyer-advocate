#!/usr/bin/env python3
"""Outcome-focused tests for the credits-tracker / complaints-bank storage bootstrap.

Asserts observable behavior (exit codes, on-disk store shape, stdout) rather than
internals. Each test runs the real CLI in a subprocess against a throwaway HOME, so the
store always resolves to ~/.claude/<store> exactly as it does in production. Deterministic:
all inputs are fixed and built programmatically; no randomness, no network, no shared state.

Run directly:  python3 test_trackers.py   (exit 0 = all passed, 1 = a failure)
Also discoverable by pytest (test_* functions).
"""

import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.normpath(os.path.join(HERE, "..", "scripts"))
CREDITS = os.path.join(SCRIPTS, "credits-tracker.py")
BANK = os.path.join(SCRIPTS, "complaints-bank.py")

# (script, store dir under ~/.claude, a read-only command that triggers require_initialized)
STORES = [
    (CREDITS, "travel-credits", ["summary"]),
    (BANK, "complaint-bank", ["list"]),
]


def run(script, args, home, cwd=None):
    env = dict(os.environ, HOME=home)
    return subprocess.run(
        [sys.executable, script, *args],
        env=env, cwd=cwd, capture_output=True, text=True,
    )


def store_path(home, sub):
    return os.path.join(home, ".claude", sub)


def fresh_home():
    return tempfile.mkdtemp(prefix="ffa-test-home-")


# ── require_initialized ───────────────────────────────────────────────────────

def test_read_fails_when_uninitialized():
    for script, sub, read_cmd in STORES:
        home = fresh_home()
        r = run(script, read_cmd, home)
        assert r.returncode == 2, f"{script}: expected exit 2, got {r.returncode}\n{r.stderr}"
        assert "not initialized" in r.stderr.lower(), f"{script}: {r.stderr}"


def test_regular_file_at_store_path_is_rejected():
    for script, sub, read_cmd in STORES:
        home = fresh_home()
        os.makedirs(os.path.join(home, ".claude"))
        open(store_path(home, sub), "w").close()  # a plain file where the store should be
        r = run(script, read_cmd, home)
        assert r.returncode == 2, f"{script}: expected exit 2, got {r.returncode}"
        assert "not a directory" in r.stderr.lower(), f"{script}: {r.stderr}"


# ── init --default ────────────────────────────────────────────────────────────

def test_init_default_creates_usable_store():
    for script, sub, read_cmd in STORES:
        home = fresh_home()
        r = run(script, ["init", "--default"], home)
        assert r.returncode == 0, f"{script}: init failed\n{r.stderr}"
        assert os.path.isdir(store_path(home, sub)), f"{script}: store dir missing"
        # read command now works
        r2 = run(script, read_cmd, home)
        assert r2.returncode == 0, f"{script}: read after init failed\n{r2.stderr}"


def test_init_default_refuses_dangling_symlink():
    # A dangling symlink usually means the real (cloud) store is unmounted — init must
    # NOT clobber it into a fresh empty store; it must fail with recovery guidance.
    for script, sub, read_cmd in STORES:
        home = fresh_home()
        os.makedirs(os.path.join(home, ".claude"))
        os.symlink(os.path.join(home, "gone"), store_path(home, sub))  # dangling
        r = run(script, ["init", "--default"], home)
        assert r.returncode == 2, f"{script}: init should refuse a dangling symlink\n{r.stderr}"
        assert "symlink" in r.stderr.lower() and ("re-link" in r.stderr.lower() or "remount" in r.stderr.lower()), \
            f"{script}: expected recovery guidance, got: {r.stderr}"
        assert os.path.islink(store_path(home, sub)), f"{script}: dangling symlink must be preserved, not clobbered"


# ── mutually exclusive flags ──────────────────────────────────────────────────

def test_init_default_and_path_are_mutually_exclusive():
    for script, sub, read_cmd in STORES:
        home = fresh_home()
        r = run(script, ["init", "--default", "--path", os.path.join(home, "x")], home)
        assert r.returncode != 0, f"{script}: --default --path together should be rejected"
        assert "not allowed with" in r.stderr.lower() or "mutually exclusive" in r.stderr.lower(), \
            f"{script}: expected argparse mutual-exclusion error, got: {r.stderr}"


# ── relative custom path → absolute symlink ───────────────────────────────────

def test_init_path_relative_becomes_absolute_symlink():
    for script, sub, read_cmd in STORES:
        home = fresh_home()
        workdir = tempfile.mkdtemp(prefix="ffa-test-cwd-")
        r = run(script, ["init", "--path", "relsub"], home, cwd=workdir)
        assert r.returncode == 0, f"{script}: init --path relsub failed\n{r.stderr}"
        link = store_path(home, sub)
        assert os.path.islink(link), f"{script}: store should be a symlink"
        target = os.readlink(link)
        assert os.path.isabs(target), f"{script}: symlink target must be absolute, got {target!r}"
        assert os.path.realpath(target) == os.path.realpath(os.path.join(workdir, "relsub")), \
            f"{script}: symlink should resolve to the cwd-relative dir, got {target!r}"


# ── link ──────────────────────────────────────────────────────────────────────

def test_link_preserves_existing_store_and_is_idempotent():
    script, sub, read_cmd = STORES[0]  # credits-tracker
    home = fresh_home()
    cloud = tempfile.mkdtemp(prefix="ffa-test-cloud-")
    # seed a populated store at the cloud location, then unlink the default
    assert run(script, ["init", "--path", cloud], home).returncode == 0
    assert run(script, ["add", "--type", "ECREDIT", "--desc", "Seed", "--value", "200",
                        "--passenger", "Baruch", "--airline", "DL"], home).returncode == 0
    os.unlink(store_path(home, sub))  # simulate a fresh machine: data in cloud, not linked
    r = run(script, ["link", "--path", cloud], home)
    assert r.returncode == 0, f"link failed\n{r.stderr}"
    listing = run(script, ["list"], home)
    assert "Seed" in listing.stdout, f"linked store lost its data:\n{listing.stdout}"
    # re-link is a no-op, not an error
    again = run(script, ["link", "--path", cloud], home)
    assert again.returncode == 0 and "already linked" in again.stdout.lower(), \
        f"re-link should be idempotent: rc={again.returncode}\n{again.stdout}{again.stderr}"


def test_link_empty_path_is_rejected():
    for script, sub, read_cmd in STORES:
        home = fresh_home()
        r = run(script, ["link", "--path", ""], home)
        assert r.returncode == 1, f"{script}: empty link path should exit 1, got {r.returncode}"
        assert "no path" in r.stderr.lower(), f"{script}: {r.stderr}"


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL {t.__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
