#!/usr/bin/env python3
"""Outcome-focused tests for the credits-tracker / complaints-bank storage bootstrap.

Asserts observable behavior (exit codes, on-disk store shape, stdout) rather than
internals. Each test runs the real CLI in a subprocess against a throwaway HOME, so the
store always resolves to ~/.claude/<store> exactly as it does in production. Deterministic:
all inputs are fixed and built programmatically; no randomness, no network, no shared state.

Run directly:  python3 test_trackers.py   (exit 0 = all passed, 1 = a failure)
Also discoverable by pytest (test_* functions).
"""

import json
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


def test_mixed_issuer_credit_surfaces_on_each_dimension():
    # Regression (#15): a credit carrying BOTH --airline and --brand must match each issuer
    # dimension independently — it surfaces for an airline scenario on its airline AND for a
    # hotel scenario on its brand. The earlier brand gate made the two mutually exclusive, so
    # a both-tagged credit vanished from airline scenarios.
    home = fresh_home()
    assert run(CREDITS, ["init", "--default"], home).returncode == 0
    assert run(CREDITS, ["add", "--type", "VOUCHER", "--desc", "Co-branded stay credit",
                         "--value", "250", "--passenger", "Baruch",
                         "--airline", "DL", "--brand", "Hilton"], home).returncode == 0
    airline = run(CREDITS, ["check", "--scenario", "Delta business JFK-CDG"], home)
    assert airline.returncode == 0 and "Co-branded stay credit" in airline.stdout, \
        f"a DL+Hilton credit must surface for an airline scenario on its airline dimension:\n{airline.stdout}"
    hotel = run(CREDITS, ["check", "--scenario", "Hilton London, 2 nights"], home)
    assert hotel.returncode == 0 and "Co-branded stay credit" in hotel.stdout, \
        f"the same credit must surface for a hotel scenario on its brand dimension:\n{hotel.stdout}"


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


# ── complaints-bank hotel store (--store hotel) ───────────────────────────────

_HOTEL_FILE_ARGS = [
    "--store", "hotel", "file",
    "--brand", "Hilton", "--property", "Hilton London Angel Islington",
    "--reservation", "3434402137", "--stay-dates", "2026-05-05/2026-05-08",
    "--loyalty-status", "Hilton Honors Gold", "--passenger", "Baruch Sadogursky",
    "--category", "HABITABILITY", "--severity", "MAJOR",
    "--summary", "No hot water for 2 of 3 nights", "--outcome", "Full stay refund + points",
]

_AIRLINE_FILE_ARGS = [
    "file", "--airline", "DL", "--flight", "DL1234", "--flight-date", "2026-01-15",
    "--route", "BNA-JFK", "--passenger", "Baruch Sadogursky", "--category", "CANCELLATION",
    "--severity", "MAJOR", "--summary", "Cancelled 2hrs before", "--outcome", "Full refund",
]


def test_hotel_store_file_list_check_resolve_roundtrip():
    # The whole point of #3: a hotel complaint can be filed, listed, pattern-checked, and
    # resolved through the same script — the schema the airline-only `file` used to reject.
    home = fresh_home()
    assert run(BANK, ["init", "--default"], home).returncode == 0
    f = run(BANK, _HOTEL_FILE_ARGS, home)
    assert f.returncode == 0 and "#1" in f.stdout and "HABITABILITY" in f.stdout, \
        f"hotel file failed:\n{f.stdout}{f.stderr}"
    lst = run(BANK, ["--store", "hotel", "list"], home)
    assert lst.returncode == 0 and "Hilton London Angel" in lst.stdout and "HABITABILITY" in lst.stdout, \
        f"hotel list missing the entry:\n{lst.stdout}"
    chk = run(BANK, ["--store", "hotel", "check", "--brand", "Hilton", "--passenger", "Baruch"], home)
    assert chk.returncode == 0 and "HABITABILITY" in chk.stdout, f"hotel check failed:\n{chk.stdout}"
    rv = run(BANK, ["--store", "hotel", "resolve", "--id", "1", "--resolution", "RESOLVED",
                    "--note", "2-night refund + 30K Honors points"], home)
    assert rv.returncode == 0, f"hotel resolve failed:\n{rv.stderr}"
    chk2 = run(BANK, ["--store", "hotel", "check", "--brand", "Hilton"], home)
    assert "RESOLVED" in chk2.stdout, f"resolution not reflected:\n{chk2.stdout}"


def test_hotel_and_airline_stores_are_independent():
    # Separate files, independent ID spaces (both start at #1), neither leaks into the other's
    # list — the back-compat guarantee for the default airline store.
    home = fresh_home()
    assert run(BANK, ["init", "--default"], home).returncode == 0
    a = run(BANK, _AIRLINE_FILE_ARGS, home)
    h = run(BANK, _HOTEL_FILE_ARGS, home)
    assert "#1" in a.stdout and "#1" in h.stdout, \
        f"each store should have its own ID space starting at 1:\n{a.stdout}\n{h.stdout}"
    air = run(BANK, ["list"], home)  # default store = airline
    assert "DL1234" in air.stdout, f"airline list should show the airline complaint:\n{air.stdout}"
    assert "Hilton" not in air.stdout and "HABITABILITY" not in air.stdout, \
        f"the hotel complaint must not leak into the airline list:\n{air.stdout}"
    hot = run(BANK, ["--store", "hotel", "list"], home)
    assert "Hilton" in hot.stdout and "DL1234" not in hot.stdout, \
        f"the airline complaint must not leak into the hotel list:\n{hot.stdout}"


def test_category_vocab_is_store_specific():
    # Hotel rejects an airline-only category and vice-versa; each store enforces its own vocab.
    home = fresh_home()
    assert run(BANK, ["init", "--default"], home).returncode == 0
    # airline category in the hotel store → rejected
    bad_hotel = ["--store", "hotel", "file", "--brand", "Hilton", "--property", "P",
                 "--reservation", "1", "--stay-dates", "2026-05-05/2026-05-06",
                 "--loyalty-status", "Gold", "--passenger", "B", "--category", "CANCELLATION",
                 "--severity", "MINOR", "--summary", "s", "--outcome", "o"]
    r1 = run(BANK, bad_hotel, home)
    assert r1.returncode == 1 and "invalid category" in r1.stderr.lower(), \
        f"airline category must be rejected in the hotel store:\n{r1.stderr}"
    # hotel category in the airline store → rejected
    bad_airline = ["file", "--airline", "DL", "--flight", "DL1", "--flight-date", "2026-01-15",
                   "--route", "BNA-JFK", "--passenger", "B", "--category", "HABITABILITY",
                   "--severity", "MINOR", "--summary", "s", "--outcome", "o"]
    r2 = run(BANK, bad_airline, home)
    assert r2.returncode == 1 and "invalid category" in r2.stderr.lower(), \
        f"hotel category must be rejected in the airline store:\n{r2.stderr}"


def test_hotel_file_missing_store_specific_args_is_rejected():
    # `--store hotel file` with only the shared args must fail with an actionable message
    # naming the missing hotel-specific flags (argparse can't enforce them — cmd_file does).
    home = fresh_home()
    assert run(BANK, ["init", "--default"], home).returncode == 0
    r = run(BANK, ["--store", "hotel", "file", "--passenger", "B", "--category", "SERVICE",
                   "--severity", "MINOR", "--summary", "s", "--outcome", "o"], home)
    assert r.returncode == 1, f"missing hotel args should exit 1, got {r.returncode}\n{r.stdout}{r.stderr}"
    assert "requires" in r.stderr.lower() and "--brand" in r.stderr, \
        f"error should name the missing hotel flags:\n{r.stderr}"
    assert "traceback" not in r.stderr.lower(), f"crashed instead of clean error:\n{r.stderr}"


def _bank_file(home, name):
    return os.path.join(store_path(home, "complaint-bank"), name)


def test_link_accepts_hotel_only_bank():
    # A bank holding only hotel-complaints.md (no airline complaints yet) must still be
    # linkable — hotel-complaints.md is a valid bank-existence marker too. Before the fix,
    # `link` errored because it only looked for complaints.md.
    home = fresh_home()
    cloud = _mktemp(prefix="ffa-test-cloud-")
    assert run(BANK, ["init", "--path", cloud], home).returncode == 0
    assert run(BANK, _HOTEL_FILE_ARGS, home).returncode == 0
    os.remove(os.path.join(cloud, "complaints.md"))      # make it hotel-only
    os.unlink(store_path(home, "complaint-bank"))         # simulate a fresh machine: data in cloud
    r = run(BANK, ["link", "--path", cloud], home)
    assert r.returncode == 0, f"link to a hotel-only bank should succeed:\n{r.stderr}"
    lst = run(BANK, ["--store", "hotel", "list"], home)
    assert "Hilton" in lst.stdout, f"hotel data must survive the link:\n{lst.stdout}"


def test_interactive_init_does_not_wipe_hotel_only_bank():
    # Data-loss guard: a bank with hotel complaints but no airline complaints must read as
    # POPULATED, so interactive `init` refuses to reinitialize (which would rmtree the dir and
    # destroy the hotel data). Before the fix, emptiness was judged from complaints.md alone.
    home = fresh_home()
    assert run(BANK, ["init", "--default"], home).returncode == 0
    assert run(BANK, _HOTEL_FILE_ARGS, home).returncode == 0
    os.remove(_bank_file(home, "complaints.md"))          # hotel-only bank
    r = run(BANK, ["init"], home, stdin_text="y\n")        # 'y' would confirm a wipe, if offered
    assert r.returncode == 0, f"{r.stdout}{r.stderr}"
    assert "has filed complaints" in r.stdout.lower(), \
        f"a hotel-only bank must read as populated, not offer to reinitialize:\n{r.stdout}"
    assert os.path.isfile(_bank_file(home, "hotel-complaints.md")), \
        "hotel data must not be wiped"
    lst = run(BANK, ["--store", "hotel", "list"], home)
    assert "Hilton" in lst.stdout, f"hotel data must survive:\n{lst.stdout}"


# ── JSON output contract (credits-tracker only) ───────────────────────────────

def _json_out(result):
    """Parse a command's stdout as one JSON object, failing loudly if it is not."""
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise AssertionError(f"stdout was not JSON ({exc}):\n{result.stdout}")


def test_every_command_emits_one_json_object():
    """--json produces a single parseable object on stdout for every subcommand."""
    home = _mktemp("json-all-")
    init = run(CREDITS, ["init", "--json", "--default"], home)
    assert _json_out(init)["state"] == "ready"

    run(CREDITS, ["add", "--type", "ECREDIT", "--desc", "DL credit", "--value", "200.00",
                  "--airline", "DL", "--passenger", "Baruch Sadogursky"], home)

    for argv in (["status", "--json"], ["list", "--json"], ["expiring", "--json"],
                 ["summary", "--json"],
                 ["check", "--json", "--scenario", "Delta business JFK-CDG"]):
        out = _json_out(run(CREDITS, argv, home))
        assert isinstance(out, dict), f"{argv[0]} did not emit an object: {out}"


def test_status_json_states_match_exit_codes():
    """The JSON state and the exit code report the same readiness in every branch."""
    ready_home = _mktemp("json-ready-")
    run(CREDITS, ["init", "--default"], ready_home)
    res = run(CREDITS, ["status", "--json"], ready_home)
    assert res.returncode == 0 and _json_out(res)["state"] == "ready"

    missing_home = _mktemp("json-missing-")
    res = run(CREDITS, ["status", "--json"], missing_home)
    assert res.returncode == 3 and _json_out(res)["state"] == "missing"

    invalid_home = _mktemp("json-invalid-")
    os.makedirs(os.path.join(invalid_home, ".claude"), exist_ok=True)
    with open(os.path.join(invalid_home, ".claude", "travel-credits"), "w") as fh:
        fh.write("not a directory")
    res = run(CREDITS, ["status", "--json"], invalid_home)
    assert res.returncode == 4 and _json_out(res)["state"] == "invalid"
    assert _json_out(res)["reason"], "an invalid store must say why"


def test_check_json_separates_other_passenger_matches():
    """A family member off the trip lands in other_passenger_matches, not matches."""
    home = _mktemp("json-check-")
    run(CREDITS, ["init", "--default"], home)
    run(CREDITS, ["add", "--type", "ECREDIT", "--desc", "AA kid credit", "--value", "189.50",
                  "--airline", "AA", "--passenger", "Kid Sadogursky"], home)

    out = _json_out(run(CREDITS, ["check", "--json", "--scenario",
                                  "American Airlines BNA-ORD economy repo",
                                  "--passengers", "Baruch"], home))
    assert out["matches"] == []
    assert len(out["other_passenger_matches"]) == 1
    other = out["other_passenger_matches"][0]
    assert other["passenger_on_trip"] is False
    assert other["reasons"], "a match must carry its reasons"
    assert out["airlines_detected"] == ["AA"]


def test_add_error_emits_structured_payload():
    """An agent reads a failure from JSON rather than scraping stderr prose."""
    home = _mktemp("json-err-")
    run(CREDITS, ["init", "--default"], home)
    res = run(CREDITS, ["add", "--json", "--type", "NOPE", "--desc", "x", "--value", "1"], home)
    assert res.returncode == 1
    assert _json_out(res)["error"] == "invalid_type"


def test_interactive_init_refuses_json_mode():
    """Bare `init --json` cannot answer prompts on the user's behalf."""
    home = _mktemp("json-interactive-")
    res = run(CREDITS, ["init", "--json"], home)
    assert res.returncode == 2
    assert _json_out(res)["error"] == "interactive_required"


def test_prose_remains_the_default():
    """Existing callers that read tables are unaffected by the JSON path."""
    home = _mktemp("json-default-")
    run(CREDITS, ["init", "--default"], home)
    run(CREDITS, ["add", "--type", "ECREDIT", "--desc", "DL credit", "--value", "200.00",
                  "--airline", "DL"], home)
    out = run(CREDITS, ["list"], home).stdout
    assert "DL credit" in out
    try:
        json.loads(out)
    except json.JSONDecodeError:
        return
    raise AssertionError("default output should be prose, not JSON")


def test_invalid_expiry_rejected_before_any_write():
    """A malformed --expiry fails cleanly and leaves the inventory untouched."""
    home = _mktemp("json-expiry-")
    run(CREDITS, ["init", "--default"], home)
    inventory = os.path.join(home, ".claude", "travel-credits", "inventory.md")
    with open(inventory) as fh:
        before = fh.read()

    res = run(CREDITS, ["add", "--json", "--type", "ECREDIT", "--desc", "Bad date",
                        "--value", "10.00", "--expiry", "not-a-date"], home)
    assert res.returncode == 1, f"expected a clean failure, got {res.returncode}"
    assert _json_out(res)["error"] == "invalid_expiry"
    assert "Traceback" not in res.stderr, f"died in a traceback:\n{res.stderr}"

    with open(inventory) as fh:
        after = fh.read()
    assert after == before, "a rejected credit must not reach the store"
    assert "Bad date" not in after


def test_invalid_expiry_rejected_in_prose_mode_too():
    """The same guard holds without --json; the store is not mutated either way."""
    home = _mktemp("prose-expiry-")
    run(CREDITS, ["init", "--default"], home)
    inventory = os.path.join(home, ".claude", "travel-credits", "inventory.md")
    with open(inventory) as fh:
        before = fh.read()

    res = run(CREDITS, ["add", "--type", "ECREDIT", "--desc", "Bad date",
                        "--value", "10.00", "--expiry", "2026-13-45"], home)
    assert res.returncode == 1
    assert "Traceback" not in res.stderr
    with open(inventory) as fh:
        assert fh.read() == before


def test_uninitialized_store_still_emits_json():
    """A bootstrap failure honours the contract instead of leaving stdout empty."""
    home = _mktemp("json-uninit-")
    res = run(CREDITS, ["list", "--json"], home)
    assert res.returncode != 0
    assert _json_out(res)["error"] == "store_not_initialized"


def test_bad_argument_still_emits_json():
    """An argparse failure exits before args exist and still emits an object."""
    home = _mktemp("json-badarg-")
    res = run(CREDITS, ["list", "--json", "--nonsense"], home)
    assert res.returncode != 0
    assert _json_out(res)["error"] == "command_failed"


def test_every_json_failure_path_emits_an_object():
    """No --json invocation may exit with unparseable stdout."""
    uninit = _mktemp("json-fail-uninit-")
    ready = _mktemp("json-fail-ready-")
    run(CREDITS, ["init", "--default"], ready)

    cases = [
        (uninit, ["list", "--json"]),
        (uninit, ["summary", "--json"]),
        (uninit, ["expiring", "--json"]),
        (ready, ["use", "--json", "--id", "999"]),
        (ready, ["add", "--json", "--type", "NOPE", "--desc", "x", "--value", "1"]),
        (ready, ["add", "--json", "--type", "ECREDIT", "--desc", "x", "--value", "1",
                 "--expiry", "nope"]),
        (ready, ["init", "--json"]),
    ]
    for home, argv in cases:
        res = run(CREDITS, argv, home)
        assert res.returncode != 0, f"{argv} unexpectedly succeeded"
        out = _json_out(res)
        assert "error" in out, f"{argv} emitted no error key: {out}"


def test_check_reports_detections_on_an_empty_store():
    """Scenario detection does not depend on whether the store holds credits."""
    home = _mktemp("json-empty-check-")
    run(CREDITS, ["init", "--default"], home)
    out = _json_out(run(CREDITS, ["check", "--json", "--scenario",
                                  "Delta business JFK-CDG"], home))
    assert out["airlines_detected"] == ["DL"], out
    assert out["match_count"] == 0

    hotel = _json_out(run(CREDITS, ["check", "--json", "--scenario",
                                    "Hilton London, 3 nights"], home))
    assert hotel["brands_detected"] == ["HILTON"], hotel


# ── complaints-bank --json contract ───────────────────────────────────────────

def _bank_json(args, home, stdin_text=None):
    """Run complaints-bank with --json and return (payload, exit code)."""
    r = run(BANK, args, home, stdin_text=stdin_text)
    assert r.stdout.strip(), f"{args}: stdout empty under --json\n{r.stderr}"
    return json.loads(r.stdout), r.returncode


_AIRLINE_FILE = ["file", "--airline", "DL", "--flight", "DL1234",
                 "--flight-date", "2026-01-15", "--route", "ATL-SFO",
                 "--passenger", "J Baruch", "--category", "DELAY",
                 "--severity", "MAJOR", "--summary", "6h delay", "--outcome", "refund"]


def test_bank_json_every_subcommand_emits_one_object():
    # script-delegation: a skill-invoked deterministic script emits structured data. Every
    # subcommand, success and failure alike, has to parse — an empty stdout reads as a
    # crash rather than a result.
    home = fresh_home()
    payload, code = _bank_json(["status", "--json"], home)
    assert payload["state"] == "missing" and code == 3, payload

    payload, code = _bank_json(["list", "--json"], home)
    assert payload["error"] == "bank_not_initialized" and code == 2, payload

    assert _bank_json(["init", "--default", "--json"], home)[1] == 0
    assert _bank_json(["status", "--json"], home)[0]["state"] == "ready"

    payload, code = _bank_json([*_AIRLINE_FILE, "--json"], home)
    assert code == 0 and payload["filed"]["id"] == 1, payload
    assert payload["filed"]["airline"] == "DL", payload

    assert _bank_json(["list", "--json"], home)[0]["count"] == 1
    assert _bank_json(["pending", "--json"], home)[0]["count"] == 1
    assert _bank_json(["check", "--airline", "DL", "--json"], home)[0]["count"] == 1

    payload, code = _bank_json(
        ["resolve", "--id", "1", "--resolution", "RESOLVED", "--note", "75K", "--json"], home)
    assert code == 0 and payload["updated"]["resolution"] == "RESOLVED", payload
    assert _bank_json(["pending", "--json"], home)[0]["count"] == 0


def test_bank_json_failures_are_structured():
    home = fresh_home()
    assert run(BANK, ["init", "--default"], home).returncode == 0
    cases = [
        ("invalid_category",
         [*_AIRLINE_FILE[:-6], "--category", "NOPE", "--severity", "MAJOR",
          "--summary", "s", "--outcome", "o", "--json"]),
        ("not_found", ["resolve", "--id", "99", "--resolution", "RESOLVED", "--json"]),
        ("missing_required_args",
         ["file", "--passenger", "P", "--category", "DELAY", "--severity", "MAJOR",
          "--summary", "s", "--outcome", "o", "--json"]),
        ("missing_required_args", ["check", "--json"]),
    ]
    for expected, args in cases:
        payload, code = _bank_json(args, home)
        assert code != 0, f"{args} should exit non-zero"
        assert payload["error"] == expected, f"{args}: wanted {expected}, got {payload}"


def test_bank_json_empty_results_are_valid_answers():
    # A count of 0 is an answer, not a failure — the prose path prints "No complaints found."
    home = fresh_home()
    assert run(BANK, ["init", "--default"], home).returncode == 0
    for args, key in ((["list", "--json"], "count"), (["pending", "--json"], "count")):
        payload, code = _bank_json(args, home)
        assert code == 0 and payload[key] == 0, f"{args}: {payload}"
    payload, code = _bank_json(["check", "--airline", "DL", "--json"], home)
    assert code == 0 and payload["count"] == 0 and payload["matches"] == [], payload


def test_bank_json_hotel_store_is_covered_too():
    home = fresh_home()
    assert run(BANK, ["init", "--default"], home).returncode == 0
    payload, code = _bank_json(["--store", "hotel", *_HOTEL_FILE_ARGS, "--json"], home)
    assert code == 0, payload
    assert payload["store"] == "hotel" and payload["filed"]["brand"] == "Hilton", payload
    assert _bank_json(["--store", "hotel", "list", "--json"], home)[0]["count"] == 1
    # Airline store stays empty — the two stores never leak into each other.
    assert _bank_json(["list", "--json"], home)[0]["count"] == 0


def test_bank_prose_mode_is_unchanged_by_default():
    # Back-compat: every existing call site omits --json and must still get the tables.
    home = fresh_home()
    assert run(BANK, ["init", "--default"], home).returncode == 0
    assert run(BANK, _AIRLINE_FILE, home).returncode == 0
    r = run(BANK, ["list"], home)
    assert "Resolution" in r.stdout and "DL1234" in r.stdout, r.stdout
    assert not r.stdout.lstrip().startswith("{"), "prose mode must not emit JSON"
# ── schema_version stamping (credits-tracker only) ────────────────────────────

def test_added_credit_carries_schema_version():
    """Every record written must carry the schema version (stateful-artifacts)."""
    home = _mktemp("schemaver-")
    run(CREDITS, ["init", "--default"], home)
    assert run(CREDITS, ["add", "--type", "ECREDIT", "--desc", "Test credit",
                         "--value", "100.00", "--airline", "DL"], home).returncode == 0

    inventory = os.path.join(home, ".claude", "travel-credits", "inventory.md")
    with open(inventory) as fh:
        text = fh.read()
    assert "- **Schema version**: 1" in text, f"no schema version stamped:\n{text}"


def _strip_versions(inventory):
    """Simulate records written before versioning existed."""
    with open(inventory) as fh:
        text = fh.read()
    stripped = "\n".join(l for l in text.split("\n") if "**Schema version**" not in l)
    with open(inventory, "w") as fh:
        fh.write(stripped)
    assert "**Schema version**" not in stripped
    return stripped


def test_a_non_owner_write_does_not_migrate_other_records():
    """stateful-artifacts reserves migration to the owner skill.

    Every skill that logs compensation calls this script directly, so `add` runs
    under a non-owner writer. It stamps the record it is itself writing and
    leaves everyone else's alone — the store is upgraded by `migrate`, not as a
    side effect of somebody logging a voucher.
    """
    home = _mktemp("schemaver-nonowner-")
    run(CREDITS, ["init", "--default"], home)
    run(CREDITS, ["add", "--type", "ECREDIT", "--desc", "Legacy credit",
                  "--value", "50.00", "--airline", "AA"], home)

    inventory = os.path.join(home, ".claude", "travel-credits", "inventory.md")
    _strip_versions(inventory)

    assert run(CREDITS, ["add", "--type", "VOUCHER", "--desc", "Second credit",
                         "--value", "25.00", "--airline", "AA"], home).returncode == 0
    with open(inventory) as fh:
        after = fh.read()
    assert after.count("- **Schema version**: 1") == 1, (
        f"a non-owner write must stamp only its own record:\n{after}")
    assert "Legacy credit" in after, "the untouched record must survive verbatim"


def test_migrate_stamps_records_written_before_versioning():
    """The owner's migrate run is what brings a pre-versioning store up to date."""
    home = _mktemp("schemaver-migrate-")
    run(CREDITS, ["init", "--default"], home)
    for desc in ("Legacy one", "Legacy two"):
        run(CREDITS, ["add", "--type", "ECREDIT", "--desc", desc,
                      "--value", "50.00", "--airline", "AA"], home)

    inventory = os.path.join(home, ".claude", "travel-credits", "inventory.md")
    _strip_versions(inventory)

    r = run(CREDITS, ["migrate", "--json"], home)
    assert r.returncode == 0, f"{r.stdout}{r.stderr}"
    payload = _json_out(r)
    assert payload["changed"] is True
    assert payload["stamped"] == 2, payload

    with open(inventory) as fh:
        after = fh.read()
    assert after.count("- **Schema version**: 1") == 2, f"not stamped:\n{after}"
    assert "Legacy one" in after and "Legacy two" in after


def test_migrate_is_idempotent():
    """A store already current is left byte-identical and reports no change."""
    home = _mktemp("schemaver-idem-")
    run(CREDITS, ["init", "--default"], home)
    run(CREDITS, ["add", "--type", "ECREDIT", "--desc", "Current credit",
                  "--value", "50.00", "--airline", "AA"], home)

    inventory = os.path.join(home, ".claude", "travel-credits", "inventory.md")
    with open(inventory) as fh:
        before = fh.read()

    payload = _json_out(run(CREDITS, ["migrate", "--json"], home))
    assert payload["changed"] is False, payload
    assert payload["stamped"] == 0 and payload["upgraded"] == 0, payload

    with open(inventory) as fh:
        assert fh.read() == before, "an idempotent migrate must not rewrite the store"


def test_newer_schema_version_is_skipped_and_its_id_reserved():
    """A record newer than this script reads as unusable, and its id is not reused."""
    home = _mktemp("schemaver-newer-")
    run(CREDITS, ["init", "--default"], home)
    run(CREDITS, ["add", "--type", "ECREDIT", "--desc", "Readable credit",
                  "--value", "10.00", "--airline", "DL"], home)

    inventory = os.path.join(home, ".claude", "travel-credits", "inventory.md")
    with open(inventory) as fh:
        text = fh.read()
    # Simulate a record written by a future owner.
    text = text.replace("- **Schema version**: 1", "- **Schema version**: 99")
    with open(inventory, "w") as fh:
        fh.write(text)

    listed = run(CREDITS, ["list"], home)
    assert "Readable credit" not in listed.stdout, (
        f"a newer-versioned record must not be consumed:\n{listed.stdout}")

    added = run(CREDITS, ["add", "--type", "VOUCHER", "--desc", "New credit",
                          "--value", "20.00", "--airline", "AA"], home)
    assert added.returncode == 0
    assert "#2" in added.stdout, (
        f"id must not be reused over an unreadable record:\n{added.stdout}")


def test_migrate_upgrades_an_explicitly_older_version():
    """An explicitly older record is stepped up to SCHEMA_VERSION, not left stale."""
    home = _mktemp("schemaver-older-")
    run(CREDITS, ["init", "--default"], home)
    run(CREDITS, ["add", "--type", "ECREDIT", "--desc", "Old-shape credit",
                  "--value", "15.00", "--airline", "DL"], home)

    inventory = os.path.join(home, ".claude", "travel-credits", "inventory.md")
    with open(inventory) as fh:
        text = fh.read()
    with open(inventory, "w") as fh:
        fh.write(text.replace("- **Schema version**: 1", "- **Schema version**: 0"))

    payload = _json_out(run(CREDITS, ["migrate", "--json"], home))
    assert payload["upgraded"] == 1, payload

    with open(inventory) as fh:
        after = fh.read()
    assert "- **Schema version**: 0" not in after, f"stale version survived:\n{after}"
    assert "Old-shape credit" in run(CREDITS, ["list"], home).stdout


def test_migrate_reports_an_unparseable_version_line():
    """A version line that is not an integer is counted, not silently swallowed.

    The router branches on this field to stop rather than present a partial
    inventory, so the count has to be real.
    """
    home = _mktemp("schemaver-garbage-")
    run(CREDITS, ["init", "--default"], home)
    run(CREDITS, ["add", "--type", "ECREDIT", "--desc", "Hand-edited credit",
                  "--value", "15.00", "--airline", "DL"], home)

    inventory = os.path.join(home, ".claude", "travel-credits", "inventory.md")
    with open(inventory) as fh:
        text = fh.read()
    with open(inventory, "w") as fh:
        fh.write(text.replace("- **Schema version**: 1", "- **Schema version**: v1-ish"))

    payload = _json_out(run(CREDITS, ["migrate", "--json"], home))
    assert payload["unreadable"] == 1, payload

    with open(inventory) as fh:
        assert "- **Schema version**: v1-ish" in fh.read(), "must not be guessed at"

    listed = _json_out(run(CREDITS, ["list", "--json"], home))
    assert listed["count"] == 0, f"an unreadable record must not be consumed: {listed}"


def test_migrate_does_not_rewrite_a_newer_record_down():
    """An owner that cannot read a record must not rewrite its version either."""
    home = _mktemp("schemaver-preserve-")
    run(CREDITS, ["init", "--default"], home)
    run(CREDITS, ["add", "--type", "ECREDIT", "--desc", "Future credit",
                  "--value", "15.00", "--airline", "DL"], home)

    inventory = os.path.join(home, ".claude", "travel-credits", "inventory.md")
    with open(inventory) as fh:
        text = fh.read()
    with open(inventory, "w") as fh:
        fh.write(text.replace("- **Schema version**: 1", "- **Schema version**: 99"))

    payload = _json_out(run(CREDITS, ["migrate", "--json"], home))
    assert payload["skipped_newer"] == 1, payload

    with open(inventory) as fh:
        after = fh.read()
    assert "- **Schema version**: 99" in after, f"newer record was downgraded:\n{after}"

    # And a plain non-owner write leaves it alone too.
    run(CREDITS, ["add", "--type", "VOUCHER", "--desc", "Current credit",
                  "--value", "5.00", "--airline", "AA"], home)
    with open(inventory) as fh:
        assert "- **Schema version**: 99" in fh.read()


# ── using-travel-credits router contract ──────────────────────────────────────

ROUTER_SKILL = os.path.normpath(
    os.path.join(HERE, "..", "..", "using-travel-credits", "SKILL.md"))

# Types the router must not spell out — the accepted set is the script's.
_SCRIPT_OWNED_TYPES = ["GUC", "RUC", "ECREDIT", "PARTNER", "AMEX"]


def _router_invocations():
    """Every line in the router's SKILL.md that runs the tracker."""
    with open(ROUTER_SKILL, encoding="utf-8") as fh:
        lines = fh.read().split("\n")
    return [ln for ln in lines if "credits-tracker.py" in ln and "python3 " in ln]


def test_router_invocations_use_the_plugin_mount_path():
    # skill-authoring Script References: one path convention per SKILL.md, and it must be
    # the one that resolves where the skill is invoked. A consumer copies these verbatim.
    mount = (".tessl/plugins/jbaruch/frequent-flyer-advocate"
             "/skills/frequent-flyer-advocate/scripts/credits-tracker.py")
    invocations = _router_invocations()
    assert invocations, f"no tracker invocations found in {ROUTER_SKILL}"
    for line in invocations:
        assert mount in line, f"invocation does not use the mount path:\n  {line.strip()}"


def test_router_always_passes_json():
    # script-delegation Script Requirements: a skill-invoked deterministic script is
    # JSON-producing. The prose rendering is the interactive human path; an agent that
    # scrapes it re-introduces the table-parsing the --json contract exists to end.
    for line in _router_invocations():
        assert "--json" in line, f"router invocation omits --json:\n  {line.strip()}"


def test_router_does_not_restate_the_script_type_vocabulary():
    # script-as-black-box: the accepted --type set belongs to the script. Copied into the
    # skill it drifts, and a stale list is how an agent picks a type the script rejects.
    with open(ROUTER_SKILL, encoding="utf-8") as fh:
        text = fh.read()
    leaked = [t for t in _SCRIPT_OWNED_TYPES if t in text]
    assert not leaked, f"router restates the script's type vocabulary: {leaked}"
    assert "--help" in text, "router must point at --help for the accepted type set"


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
