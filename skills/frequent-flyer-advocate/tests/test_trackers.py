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
import atexit
import shutil
import subprocess
import sys
import tempfile

# Track every temp dir we create and remove them on exit, so repeated local/CI runs
# don't leak directories under the system temp dir.
_TMPDIRS = []


@atexit.register
def _cleanup_tmpdirs():
    for d in _TMPDIRS:
        shutil.rmtree(d, ignore_errors=True)


def _mktemp(prefix):
    d = tempfile.mkdtemp(prefix=prefix)
    _TMPDIRS.append(d)
    return d


HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.normpath(os.path.join(HERE, "..", "scripts"))
CREDITS = os.path.join(SCRIPTS, "credits-tracker.py")
BANK = os.path.join(SCRIPTS, "complaints-bank.py")

# (script, store dir under ~/.claude, a read-only command that triggers require_initialized)
STORES = [
    (CREDITS, "travel-credits", ["summary"]),
    (BANK, "complaint-bank", ["list"]),
]


def run(script, args, home, cwd=None, stdin_text=None):
    env = dict(os.environ, HOME=home)
    return subprocess.run(
        [sys.executable, script, *args],
        env=env, cwd=cwd, capture_output=True, text=True, input=stdin_text,
    )


def store_path(home, sub):
    return os.path.join(home, ".claude", sub)


def fresh_home():
    return _mktemp(prefix="ffa-test-home-")


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
        workdir = _mktemp(prefix="ffa-test-cwd-")
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
    cloud = _mktemp(prefix="ffa-test-cloud-")
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
    # Empty AND whitespace-only --path must be refused: abspath('') / abspath('  ') would
    # otherwise resolve against the cwd and link the store somewhere unintended.
    for script, sub, read_cmd in STORES:
        for bad in ["", "   "]:
            home = fresh_home()
            r = run(script, ["link", "--path", bad], home)
            assert r.returncode == 1, f"{script}: link path {bad!r} should exit 1, got {r.returncode}"
            assert "no path" in r.stderr.lower(), f"{script}: {bad!r}: {r.stderr}"
            assert not os.path.lexists(store_path(home, sub)), \
                f"{script}: link path {bad!r} must not create a store"


def test_link_to_dir_without_inventory_is_rejected():
    # `link` attaches to an EXISTING store; pointing it at a dir with no inventory.md /
    # complaints.md must be refused, not silently bootstrapped into a second diverging store.
    for script, sub, read_cmd in STORES:
        home = fresh_home()
        empty = _mktemp(prefix="ffa-test-empty-")
        r = run(script, ["link", "--path", empty], home)
        assert r.returncode == 1, f"{script}: link to dir without marker should exit 1, got {r.returncode}\n{r.stderr}"
        assert "does not create one" in r.stderr.lower() or "attaches to an existing" in r.stderr.lower(), \
            f"{script}: expected refuse-to-bootstrap guidance, got: {r.stderr}"
        assert not os.path.lexists(store_path(home, sub)), \
            f"{script}: link must not create a store when refusing"


# ── status subcommand + regular-file init guard ───────────────────────────────

def test_status_reports_missing_ready_invalid():
    for script, sub, read_cmd in STORES:
        home = fresh_home()
        # missing
        r = run(script, ["status"], home)
        assert r.returncode == 3 and "missing" in r.stdout.lower(), \
            f"{script}: expected missing/exit3, got {r.returncode}: {r.stdout}{r.stderr}"
        # ready after init — stdout is the exact bare token, resolved path goes to stderr
        assert run(script, ["init", "--default"], home).returncode == 0
        r = run(script, ["status"], home)
        assert r.returncode == 0 and r.stdout.strip() == "ready", \
            f"{script}: expected bare 'ready' token/exit0, got {r.returncode}: {r.stdout!r}"
        # invalid: a plain file where the store should be
        home2 = fresh_home()
        os.makedirs(os.path.join(home2, ".claude"))
        open(store_path(home2, sub), "w").close()
        r = run(script, ["status"], home2)
        assert r.returncode == 4 and "invalid" in r.stdout.lower(), \
            f"{script}: expected invalid/exit4, got {r.returncode}: {r.stdout}"


def test_init_default_refuses_regular_file_without_crashing():
    # A plain file at the store path must produce an actionable error, not an uncaught
    # FileExistsError from os.makedirs.
    for script, sub, read_cmd in STORES:
        home = fresh_home()
        os.makedirs(os.path.join(home, ".claude"))
        open(store_path(home, sub), "w").close()
        r = run(script, ["init", "--default"], home)
        assert r.returncode == 2, f"{script}: expected exit 2, got {r.returncode}\n{r.stdout}{r.stderr}"
        assert "not a directory" in r.stderr.lower(), f"{script}: {r.stderr}"
        assert "traceback" not in r.stderr.lower(), f"{script}: crashed instead of clean error:\n{r.stderr}"


def test_init_empty_or_whitespace_path_is_rejected():
    # `init --path ""` (and whitespace-only) must reach the self-error-handled diagnostic, not
    # fall through to the interactive branch: cmd_init dispatches on presence, not truthiness.
    for script, sub, read_cmd in STORES:
        for bad in ["", "   "]:
            home = fresh_home()
            r = run(script, ["init", "--path", bad], home, stdin_text="")
            assert r.returncode == 1, \
                f"{script}: init --path {bad!r} should exit 1, got {r.returncode}\n{r.stdout}{r.stderr}"
            assert "no path" in r.stderr.lower(), f"{script}: {bad!r}: {r.stderr}"
            assert not os.path.lexists(store_path(home, sub)), \
                f"{script}: init --path {bad!r} must not create a store"
            assert "traceback" not in r.stderr.lower(), f"{script}: crashed:\n{r.stderr}"


def test_init_path_refuses_existing_file():
    # A plain file at the --path target would make os.makedirs(exist_ok=True) raise an
    # opaque FileExistsError — init must refuse with an actionable message, not crash.
    for script, sub, read_cmd in STORES:
        home = fresh_home()
        target_parent = _mktemp(prefix="ffa-test-target-")
        filepath = os.path.join(target_parent, "afile")
        open(filepath, "w").close()
        r = run(script, ["init", "--path", filepath], home)
        assert r.returncode == 1, f"{script}: init --path <file> should exit 1, got {r.returncode}\n{r.stderr}"
        assert "not a usable directory" in r.stderr.lower(), f"{script}: {r.stderr}"
        assert not os.path.lexists(store_path(home, sub)), f"{script}: no store should be created"
        assert "traceback" not in r.stderr.lower(), f"{script}: crashed instead of clean error:\n{r.stderr}"


def test_init_path_refuses_dangling_symlink_target():
    # A dangling symlink at the --path target: exists() is False but islink() is True, so
    # os.makedirs would raise FileExistsError. init must refuse, not crash.
    for script, sub, read_cmd in STORES:
        home = fresh_home()
        target_parent = _mktemp(prefix="ffa-test-target-")
        dangling = os.path.join(target_parent, "dangling")
        os.symlink(os.path.join(target_parent, "missing"), dangling)
        r = run(script, ["init", "--path", dangling], home)
        assert r.returncode == 1, f"{script}: init --path <dangling> should exit 1, got {r.returncode}\n{r.stderr}"
        assert "not a usable directory" in r.stderr.lower(), f"{script}: {r.stderr}"
        assert not os.path.lexists(store_path(home, sub)), f"{script}: no store should be created"
        assert "traceback" not in r.stderr.lower(), f"{script}: crashed:\n{r.stderr}"


def test_interactive_init_over_regular_file_refuses():
    # Interactive `init` (no --default/--path) must NOT clobber a plain file at the store
    # path — it routes through the refuse-unusable contract, not os.unlink/shutil.rmtree.
    for script, sub, read_cmd in STORES:
        home = fresh_home()
        os.makedirs(os.path.join(home, ".claude"))
        open(store_path(home, sub), "w").close()  # plain file where the store should be
        r = run(script, ["init"], home, stdin_text="y\n")
        assert r.returncode == 2, \
            f"{script}: interactive init over a file should exit 2, got {r.returncode}\n{r.stdout}{r.stderr}"
        assert "not a directory" in r.stderr.lower(), f"{script}: {r.stderr}"
        assert os.path.isfile(store_path(home, sub)), f"{script}: the plain file must be preserved, not clobbered"
        assert "traceback" not in r.stderr.lower(), f"{script}: crashed:\n{r.stderr}"


def test_status_distinguishes_symlink_to_file_from_dangling():
    # A symlink to an existing non-directory is NOT dangling — status must say so, and only
    # call a symlink "dangling" when its target is actually missing.
    for script, sub, read_cmd in STORES:
        home = fresh_home()
        os.makedirs(os.path.join(home, ".claude"))
        afile = os.path.join(home, "afile")
        open(afile, "w").close()
        os.symlink(afile, store_path(home, sub))  # symlink → existing file
        r = run(script, ["status"], home)
        assert r.returncode == 4, f"{script}: symlink-to-file status should exit 4, got {r.returncode}\n{r.stdout}"
        assert "invalid" in r.stdout.lower() and "not a directory" in r.stdout.lower(), \
            f"{script}: expected 'not a directory', got: {r.stdout}"
        assert "dangling" not in r.stdout.lower(), \
            f"{script}: a symlink to an existing file must NOT be reported as dangling: {r.stdout}"
        # contrast: a genuinely dangling symlink IS reported as dangling
        home2 = fresh_home()
        os.makedirs(os.path.join(home2, ".claude"))
        os.symlink(os.path.join(home2, "gone"), store_path(home2, sub))
        r2 = run(script, ["status"], home2)
        assert r2.returncode == 4 and "dangling" in r2.stdout.lower(), \
            f"{script}: expected dangling, got: {r2.stdout}"


# ── hotel brand dimension (credits-tracker only) ──────────────────────────────

def _seeded_home_with_hotel_and_airline_credits():
    """Init a store and seed one hotel-brand voucher + one airline eCredit. Returns home."""
    home = fresh_home()
    assert run(CREDITS, ["init", "--default"], home).returncode == 0
    assert run(CREDITS, ["add", "--type", "VOUCHER", "--desc", "Comp 2-night stay",
                         "--value", "2 nights", "--expiry", "2027-03-31",
                         "--passenger", "Baruch", "--brand", "Hilton"], home).returncode == 0
    assert run(CREDITS, ["add", "--type", "ECREDIT", "--desc", "Canceled BNA-JFK",
                         "--value", "347.20", "--expiry", "2027-12-15",
                         "--passenger", "Baruch", "--airline", "DL"], home).returncode == 0
    return home


def test_add_brand_is_stored_and_shown_in_list():
    home = _seeded_home_with_hotel_and_airline_credits()
    r = run(CREDITS, ["list"], home)
    assert r.returncode == 0, r.stderr
    assert "Brand" in r.stdout, f"list should have a Brand column:\n{r.stdout}"
    assert "HILTON" in r.stdout, f"the Hilton voucher's normalized brand should show:\n{r.stdout}"


def test_list_brand_filter_collapses_subbrands_to_chain():
    # A credit tagged with a sub-brand (Conrad) must be found by filtering on the chain (Hilton).
    home = fresh_home()
    assert run(CREDITS, ["init", "--default"], home).returncode == 0
    assert run(CREDITS, ["add", "--type", "VOUCHER", "--desc", "Conrad stay", "--value",
                         "1 night", "--passenger", "Baruch", "--brand", "Conrad"], home).returncode == 0
    hit = run(CREDITS, ["list", "--brand", "Hilton"], home)
    assert hit.returncode == 0 and "Conrad stay" in hit.stdout, \
        f"--brand Hilton should match a Conrad-tagged credit:\n{hit.stdout}"
    miss = run(CREDITS, ["list", "--brand", "Marriott"], home)
    assert miss.returncode == 0, f"list --brand Marriott should succeed, got {miss.returncode}\n{miss.stderr}"
    assert "Conrad stay" not in miss.stdout, f"--brand Marriott must not match a Hilton credit:\n{miss.stdout}"


def test_check_surfaces_hotel_credit_for_hotel_scenario():
    # The core bug: a hotel scenario must surface a brand-tagged credit (airline-only matching
    # never could). The use-it-or-lose-it prompt has to fire for hotel stays.
    home = _seeded_home_with_hotel_and_airline_credits()
    r = run(CREDITS, ["check", "--scenario", "Hilton London, 3 nights"], home)
    assert r.returncode == 0, r.stderr
    assert "Comp 2-night stay" in r.stdout, f"hotel voucher should surface:\n{r.stdout}"
    assert "HILTON" in r.stdout, f"detected brand should be reported:\n{r.stdout}"
    assert "Canceled BNA-JFK" not in r.stdout, f"airline credit must NOT surface for a hotel scenario:\n{r.stdout}"


def test_check_brand_alias_matches_parent_chain():
    # A sub-brand named in the scenario (Conrad) must surface a credit tagged with the chain.
    home = _seeded_home_with_hotel_and_airline_credits()
    r = run(CREDITS, ["check", "--scenario", "Conrad Tokyo, 2 nights"], home)
    assert r.returncode == 0 and "Comp 2-night stay" in r.stdout, \
        f"a Conrad scenario should surface the HILTON-tagged voucher:\n{r.stdout}"


def test_check_hotel_credit_does_not_bleed_into_airline_scenario():
    # An airline scenario must surface only the airline credit — the hotel voucher must not
    # appear, and must not trigger the legacy "airline not specified" note.
    home = _seeded_home_with_hotel_and_airline_credits()
    r = run(CREDITS, ["check", "--scenario", "Delta business JFK-CDG"], home)
    assert r.returncode == 0, r.stderr
    assert "Canceled BNA-JFK" in r.stdout, f"airline eCredit should surface:\n{r.stdout}"
    assert "Comp 2-night stay" not in r.stdout, f"hotel voucher must not bleed into airline scenario:\n{r.stdout}"
    assert "airline not specified" not in r.stdout.lower(), \
        f"a brand-tagged credit must not get the 'airline not specified' note:\n{r.stdout}"


def test_ambiguous_words_do_not_false_match_hotel_brands():
    # The whole point of dropping bare aliases (honors, choice, courtyard, …): ordinary
    # airline/travel prose containing those words must NOT surface a hotel credit. If any of
    # these regress to bare aliases, a Hilton/Choice/Marriott credit bleeds into the wrong
    # scenario.
    home = _seeded_home_with_hotel_and_airline_credits()  # has a HILTON voucher
    bleed_scenarios = [
        "Delta honors the upgrade request",   # 'honors' must not mean Hilton
        "Economy was our only choice",         # 'choice' must not mean Choice Hotels
        "United courtyard-view lounge",        # 'courtyard' must not mean Marriott
        "Renaissance-era art tour, AA flight", # 'renaissance' must not mean Marriott
    ]
    for sc in bleed_scenarios:
        r = run(CREDITS, ["check", "--scenario", sc], home)
        assert r.returncode == 0, f"{sc!r}: {r.stderr}"
        assert "Comp 2-night stay" not in r.stdout, \
            f"{sc!r} must NOT surface the Hilton voucher (ambiguous bare-word match):\n{r.stdout}"
        assert "Hotel brands detected" not in r.stdout, \
            f"{sc!r} must not detect any hotel brand:\n{r.stdout}"


def test_brand_tagged_non_voucher_credit_no_cross_bleed():
    # A brand-tagged COMP (e.g. Hilton Honors points) must NOT surface in an airline scenario,
    # even one whose words ("domestic", "companion") trip the airline-era COMP heuristic. Brand
    # is the single gate across the whole --brand surface, not just VOUCHER.
    home = fresh_home()
    assert run(CREDITS, ["init", "--default"], home).returncode == 0
    assert run(CREDITS, ["add", "--type", "COMP", "--desc", "Honors points", "--value",
                         "30000 points", "--passenger", "Baruch", "--brand", "Hilton Honors"],
               home).returncode == 0
    airline = run(CREDITS, ["check", "--scenario", "Delta round-trip domestic companion fare"], home)
    assert airline.returncode == 0, airline.stderr
    assert "Honors points" not in airline.stdout, \
        f"a brand-tagged COMP must not bleed into an airline scenario:\n{airline.stdout}"
    assert "Companion certificate may apply" not in airline.stdout, \
        f"the airline-era COMP heuristic must not fire for a hotel credit:\n{airline.stdout}"
    # ...but it DOES surface for the matching hotel scenario.
    hotel = run(CREDITS, ["check", "--scenario", "Hilton London, 2 nights"], home)
    assert hotel.returncode == 0 and "Honors points" in hotel.stdout, \
        f"the brand-tagged COMP should surface for a Hilton scenario:\n{hotel.stdout}"


def test_unambiguous_brand_phrase_still_matches():
    # The flip side: the disambiguated multi-word phrase must still match its chain.
    home = fresh_home()
    assert run(CREDITS, ["init", "--default"], home).returncode == 0
    assert run(CREDITS, ["add", "--type", "VOUCHER", "--desc", "Free night", "--value",
                         "1 night", "--passenger", "Baruch", "--brand", "Marriott"], home).returncode == 0
    r = run(CREDITS, ["check", "--scenario", "Courtyard by Marriott, 2 nights"], home)
    assert r.returncode == 0 and "Free night" in r.stdout and "MARRIOTT" in r.stdout, \
        f"the 'Courtyard by Marriott' phrase should surface the MARRIOTT credit:\n{r.stdout}"


def test_airline_only_check_unchanged_back_compat():
    # Back-compat: a store with only airline credits behaves exactly as before brand existed.
    home = fresh_home()
    assert run(CREDITS, ["init", "--default"], home).returncode == 0
    assert run(CREDITS, ["add", "--type", "ECREDIT", "--desc", "AA repo credit", "--value",
                         "189.50", "--passenger", "Kid", "--airline", "AA"], home).returncode == 0
    r = run(CREDITS, ["check", "--scenario", "American Airlines BNA-ORD economy"], home)
    assert r.returncode == 0 and "AA repo credit" in r.stdout, \
        f"airline matching must still work unchanged:\n{r.stdout}"


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
