#!/usr/bin/env python3
"""Outcome-focused tests for lock-requirements.py.

The generator reaches pip and PyPI, so both are injected: `resolver` and `fetch` are
parameters, and every test supplies a fixed stand-in. Deterministic — no network, no clock,
no randomness, no shared state. The one filesystem test writes into a throwaway temp dir.

Run directly:  python3 test_lock_requirements.py   (exit 0 = all passed, 1 = a failure)
Also discoverable by pytest (test_* functions).
"""

import atexit
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
_TMPDIRS = []


@atexit.register
def _cleanup_tmpdirs():
    for d in _TMPDIRS:
        shutil.rmtree(d, ignore_errors=True)


def _mktemp(prefix):
    d = tempfile.mkdtemp(prefix=prefix)
    _TMPDIRS.append(d)
    return d


def _load():
    """Import the generator by path — its filename has a hyphen, so `import` cannot."""
    path = os.path.join(HERE, "lock-requirements.py")
    spec = importlib.util.spec_from_file_location("lock_requirements", path)
    assert spec is not None and spec.loader is not None, f"cannot load {path}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


lr = _load()

# A minimal pip --report document: two packages, the shape parse_report reads.
REPORT = {"install": [
    {"metadata": {"name": "pyright", "version": "1.1.408"}},
    {"metadata": {"name": "typing_extensions", "version": "4.16.0"}},
]}

# A minimal PyPI release document: one wheel, one sdist, one unrelated artifact.
RELEASE = {"urls": [
    {"packagetype": "bdist_wheel", "digests": {"sha256": "bbb"}},
    {"packagetype": "sdist", "digests": {"sha256": "aaa"}},
    {"packagetype": "bdist_egg", "digests": {"sha256": "zzz"}},
]}


def fake_fetch(_name, _version):
    return RELEASE


def expect_lock_error(fn, needle):
    """Call fn, require a LockError, and require its message to be actionable."""
    try:
        fn()
    except lr.LockError as e:
        assert needle.lower() in str(e).lower(), f"message lacks {needle!r}: {e}"
        return str(e)
    raise AssertionError(f"expected LockError mentioning {needle!r}, none raised")


# ── report parsing ────────────────────────────────────────────────────────────

def test_report_parsing_normalizes_names():
    pins = lr.parse_report(REPORT)
    assert pins == {"pyright": "1.1.408", "typing-extensions": "4.16.0"}, pins


def test_report_without_install_list_is_actionable():
    expect_lock_error(lambda: lr.parse_report({"nope": []}), "install")


def test_report_entry_missing_version_is_actionable():
    expect_lock_error(lambda: lr.parse_report({"install": [{"metadata": {"name": "x"}}]}),
                      "name/version")


def test_report_resolving_nothing_is_actionable():
    expect_lock_error(lambda: lr.parse_report({"install": []}), "no packages")


# ── digest lookup ─────────────────────────────────────────────────────────────

def test_digests_cover_wheels_and_sdists_sorted():
    # Sorted so a relock of unchanged input produces a byte-identical file.
    assert lr.digests_for("pyright", "1.1.408", fetch=fake_fetch) == ["aaa", "bbb"]


def test_release_with_no_wheel_or_sdist_is_actionable():
    empty = {"urls": [{"packagetype": "bdist_egg", "digests": {"sha256": "zzz"}}]}
    expect_lock_error(lambda: lr.digests_for("x", "1", fetch=lambda *_: empty),
                      "no wheel or sdist")


def test_release_with_no_file_list_is_actionable():
    expect_lock_error(lambda: lr.digests_for("x", "1", fetch=lambda *_: {}), "file list")


def test_curl_failure_is_actionable_not_a_traceback():
    def boom(cmd, **kwargs):
        raise subprocess.CalledProcessError(6, cmd)
    saved, lr.subprocess.run = lr.subprocess.run, boom
    try:
        msg = expect_lock_error(lambda: lr.fetch_pypi("pyright", "1.1.408"), "curl exit 6")
        assert "network" in msg.lower(), msg
    finally:
        lr.subprocess.run = saved


def test_missing_curl_is_actionable():
    def absent(cmd, **kwargs):
        raise FileNotFoundError("curl")
    saved, lr.subprocess.run = lr.subprocess.run, absent
    try:
        expect_lock_error(lambda: lr.fetch_pypi("pyright", "1.1.408"), "curl is not installed")
    finally:
        lr.subprocess.run = saved


def test_non_json_from_pypi_is_actionable():
    class Proc:
        stdout = "<html>503 Service Unavailable</html>"
    saved, lr.subprocess.run = lr.subprocess.run, lambda cmd, **kw: Proc()
    try:
        expect_lock_error(lambda: lr.fetch_pypi("pyright", "1.1.408"), "other than JSON")
    finally:
        lr.subprocess.run = saved


# ── resolution ────────────────────────────────────────────────────────────────

def test_pip_failure_is_actionable():
    def boom(cmd, **kwargs):
        raise subprocess.CalledProcessError(1, cmd)
    expect_lock_error(lambda: lr.resolve(["nope==9.9.9"], runner=boom), "could not resolve")


def test_missing_report_file_is_actionable():
    # A pip too old for --report exits 0 and writes nothing.
    expect_lock_error(lambda: lr.resolve(["x"], runner=lambda cmd, **kw: None), "no resolution report")


# ── rendering ─────────────────────────────────────────────────────────────────

def test_rendered_lock_is_installable_shape():
    text = lr.render_lock({"pyright": ("1.1.408", ["aaa", "bbb"])})
    assert "pyright==1.1.408 \\" in text, text
    assert "    --hash=sha256:aaa \\" in text, text
    assert text.rstrip().endswith("--hash=sha256:bbb"), "last hash must not carry a continuation"
    assert "do not hand-edit" in text.lower(), "generated files say so"


def test_rendered_lock_is_stable_across_runs():
    entries = {"b": ("2", ["y", "x"]), "a": ("1", ["q"])}
    assert lr.render_lock(entries) == lr.render_lock(entries)
    assert lr.render_lock(entries).index("a==1") < lr.render_lock(entries).index("b==2"), \
        "packages sort, so an unchanged relock produces an unchanged file"


# ── no partial writes ─────────────────────────────────────────────────────────

def test_a_failed_lookup_leaves_the_lock_untouched():
    # The failure this ordering exists to prevent: a network blip mid-run truncating the
    # committed lock, which would then fail --require-hashes on every later CI run.
    # main() writes only after build_lock returns, so a mid-resolution failure writes nothing.
    lock = os.path.join(_mktemp("ffa-lock-"), "requirements.txt")
    original = "pyright==0.0.0 \\\n    --hash=sha256:previous\n"
    with open(lock, "w", encoding="utf-8") as f:
        f.write(original)

    def build_boom(_specs):
        raise lr.LockError("simulated PyPI outage")

    saved_path = getattr(lr, "LOCK_PATH")
    saved_build = getattr(lr, "build_lock")
    setattr(lr, "LOCK_PATH", lock)
    setattr(lr, "build_lock", build_boom)
    try:
        code = lr.main(["pyright==1.1.408"])
    finally:
        setattr(lr, "LOCK_PATH", saved_path)
        setattr(lr, "build_lock", saved_build)

    assert code == 1, "a failed lock must exit non-zero"
    with open(lock, encoding="utf-8") as f:
        assert f.read() == original, "a failed run must not touch the committed lock"


def test_a_partial_fetch_failure_aborts_before_rendering():
    def fetch_then_fail(name, _version):
        if name == "pyright":
            return RELEASE
        raise lr.LockError("simulated PyPI outage")

    expect_lock_error(
        lambda: lr.build_lock(["x"],
                              resolver=lambda _s: {"pyright": "1.1.408", "other": "1"},
                              fetch=fetch_then_fail),
        "simulated PyPI outage")


def test_build_lock_pairs_versions_with_digests():
    entries = lr.build_lock(["pyright"], resolver=lambda _s: {"pyright": "1.1.408"},
                            fetch=fake_fetch)
    assert entries == {"pyright": ("1.1.408", ["aaa", "bbb"])}, entries


# ── the committed lock ────────────────────────────────────────────────────────

def test_committed_lock_is_hash_pinned_and_complete():
    lock_path = os.path.normpath(os.path.join(HERE, "..", "requirements.txt"))
    with open(lock_path, encoding="utf-8") as f:
        text = f.read()
    pins = [ln for ln in text.split("\n") if "==" in ln and not ln.startswith("#")]
    assert pins, "the committed lock must pin at least the top-level package"
    for pin in pins:
        assert pin.rstrip().endswith("\\"), f"{pin!r} carries no hash continuation"
    assert text.count("--hash=sha256:") >= len(pins), "every pin needs at least one hash"
    assert any("pyright==" in p for p in pins), "pyright is the gate's direct dependency"


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
