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


def _load_tracker():
    """Import credits-tracker.py by path — its filename has a hyphen."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("credits_tracker", CREDITS)
    assert spec is not None and spec.loader is not None, f"cannot load {CREDITS}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# Read the version the script actually ships rather than hardcoding it. A suite that
# pins a literal has to be rewritten on every bump, which is how a migration lands
# with its own tests asserting the version it replaced.
CURRENT_SCHEMA = _load_tracker().SCHEMA_VERSION
PRIOR_SCHEMA = CURRENT_SCHEMA - 1


def vline(version):
    return f"- **Schema version**: {version}"


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
    # A brand-tagged COMPANION must NOT surface in an airline scenario, even one whose
    # words ("domestic", "companion") trip the airline-era companion heuristic. Brand is
    # the single gate across the whole --brand surface, not just VOUCHER.
    home = fresh_home()
    assert run(CREDITS, ["init", "--default"], home).returncode == 0
    assert run(CREDITS, ["add", "--type", "COMPANION", "--desc", "Hotel stay certificate",
                         "--value", "1 certificate", "--passenger", "Baruch",
                         "--brand", "Hilton Honors"], home).returncode == 0
    airline = run(CREDITS, ["check", "--scenario", "Delta round-trip domestic companion fare"], home)
    assert airline.returncode == 0, airline.stderr
    assert "Hotel stay certificate" not in airline.stdout, \
        f"a brand-tagged COMPANION must not bleed into an airline scenario:\n{airline.stdout}"
    assert "Companion certificate may apply" not in airline.stdout, \
        f"the airline-era companion heuristic must not fire for a hotel credit:\n{airline.stdout}"
    # ...but it DOES surface for the matching hotel scenario.
    hotel = run(CREDITS, ["check", "--scenario", "Hilton London, 2 nights"], home)
    assert hotel.returncode == 0 and "Hotel stay certificate" in hotel.stdout, \
        f"the brand-tagged COMPANION should surface for a Hilton scenario:\n{hotel.stdout}"


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
    assert vline(CURRENT_SCHEMA) in text, f"no schema version stamped:\n{text}"


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
    assert after.count(vline(CURRENT_SCHEMA)) == 1, (
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
    assert after.count(vline(CURRENT_SCHEMA)) == 2, f"not stamped:\n{after}"
    assert "Legacy one" in after and "Legacy two" in after


def test_an_unversioned_record_is_not_consumed_until_migrated():
    """stateful-artifacts puts a schema_version on every record.

    Without one a reader cannot know the record's shape, so it declines it rather
    than guessing — and the owner's migrate is what makes it readable. The router
    runs migrate ahead of every read, so a pre-versioning store heals on first use.
    """
    home = _mktemp("schemaver-absent-")
    run(CREDITS, ["init", "--default"], home)
    run(CREDITS, ["add", "--type", "ECREDIT", "--desc", "Pre-versioning credit",
                  "--value", "20.00", "--airline", "DL"], home)

    inventory = os.path.join(home, ".claude", "travel-credits", "inventory.md")
    _strip_versions(inventory)

    listed = run(CREDITS, ["list", "--json"], home)
    assert _json_out(listed)["count"] == 0, "an unversioned record must not be consumed"
    assert "no schema version" in listed.stderr, listed.stderr
    assert "migrate" in listed.stderr, "the warning must name the recovery"

    payload = _json_out(run(CREDITS, ["migrate", "--json"], home))
    assert payload["stamped"] == 1, payload
    assert payload["unconsumable"] == 0, payload
    assert _json_out(run(CREDITS, ["list", "--json"], home))["count"] == 1


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
    text = text.replace(vline(CURRENT_SCHEMA), vline(99))
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


def test_a_non_owner_read_declines_an_older_record():
    """An older record is 'no usable prior state' to a reader, not stale data to consume.

    Migration Policy reserves upgrading to the owner. Every caller other than the
    owner skill is a non-owner reader, so it must decline an off-version record
    rather than read it under a shape the owner has since moved past.
    """
    home = _mktemp("schemaver-nonowner-read-")
    run(CREDITS, ["init", "--default"], home)
    run(CREDITS, ["add", "--type", "ECREDIT", "--desc", "Old-shape credit",
                  "--value", "15.00", "--airline", "DL"], home)

    inventory = os.path.join(home, ".claude", "travel-credits", "inventory.md")
    with open(inventory) as fh:
        text = fh.read()
    with open(inventory, "w") as fh:
        fh.write(text.replace(vline(CURRENT_SCHEMA), vline(PRIOR_SCHEMA)))

    listed = run(CREDITS, ["list", "--json"], home)
    assert _json_out(listed)["count"] == 0, "an older record must not be consumed"
    assert "older than" in listed.stderr, f"the skip must be reported:\n{listed.stderr}"
    assert "migrate" in listed.stderr, "the warning must name the recovery"

    # The owner's migrate is what makes it readable again.
    payload = _json_out(run(CREDITS, ["migrate", "--json"], home))
    assert payload["upgraded"] == 1, payload

    with open(inventory) as fh:
        after = fh.read()
    assert vline(PRIOR_SCHEMA) not in after, f"stale version survived:\n{after}"
    assert _json_out(run(CREDITS, ["list", "--json"], home))["count"] == 1, \
        "the record must be readable once the owner has upgraded it"


def test_migrate_does_not_rewrite_a_version_line_over_spacing():
    """A current record whose version line spaces differently is still a no-op.

    Canonicalizing the line would report changed: true for a whitespace difference
    alone, and the owner runs migrate ahead of every read — so a cosmetic diff
    would rewrite the store on every one of them.
    """
    home = _mktemp("schemaver-spacing-")
    run(CREDITS, ["init", "--default"], home)
    run(CREDITS, ["add", "--type", "ECREDIT", "--desc", "Spaced credit",
                  "--value", "15.00", "--airline", "DL"], home)

    inventory = os.path.join(home, ".claude", "travel-credits", "inventory.md")
    with open(inventory) as fh:
        text = fh.read()
    spaced = text.replace(vline(CURRENT_SCHEMA), f"- **Schema version**:  {CURRENT_SCHEMA}")
    with open(inventory, "w") as fh:
        fh.write(spaced)

    payload = _json_out(run(CREDITS, ["migrate", "--json"], home))
    assert payload["already_current"] == 1, payload
    assert payload["changed"] is False, f"a spacing difference is not a migration: {payload}"

    with open(inventory) as fh:
        assert fh.read() == spaced, "the store must be left byte-identical"


def test_migrate_sees_an_indented_version_line_the_way_the_parser_does():
    """Migration and parsing must recognize a field line by the same rule.

    parse_credits() strips before matching. Anchoring migration on column zero
    made an indented version line invisible to it and visible to the parser:
    migrate re-stamped the record and reported the store wholly readable, then
    every later command dropped it. The router's Step 3 gate reads that report,
    so the divergence turned into a partial inventory presented as the whole one.
    """
    home = _mktemp("schemaver-indent-")
    run(CREDITS, ["init", "--default"], home)
    run(CREDITS, ["add", "--type", "ECREDIT", "--desc", "Indented newer",
                  "--value", "10.00", "--airline", "DL"], home)

    inventory = os.path.join(home, ".claude", "travel-credits", "inventory.md")
    with open(inventory) as fh:
        text = fh.read()
    with open(inventory, "w") as fh:
        fh.write(text.replace(vline(CURRENT_SCHEMA), "  - **Schema version**: 99"))

    payload = _json_out(run(CREDITS, ["migrate", "--json"], home))
    assert payload["skipped_newer"] == 1, f"indented newer record not recognized: {payload}"
    assert payload["stamped"] == 0, f"a second version line was spliced in: {payload}"
    assert payload["changed"] is False, f"an unmigratable record must not be rewritten: {payload}"

    # The report must agree with what the reader actually consumes.
    assert payload["unconsumable"] == 1, payload
    assert _json_out(run(CREDITS, ["list", "--json"], home))["count"] == 0


def test_migrate_reports_unconsumable_from_the_parser_not_the_buckets():
    """`unconsumable` is measured by asking the parser, so it holds whatever the cause."""
    home = _mktemp("schemaver-unconsumable-")
    run(CREDITS, ["init", "--default"], home)
    for desc in ("Readable one", "Readable two"):
        run(CREDITS, ["add", "--type", "ECREDIT", "--desc", desc,
                      "--value", "10.00", "--airline", "DL"], home)

    payload = _json_out(run(CREDITS, ["migrate", "--json"], home))
    assert payload["unconsumable"] == 0, payload
    assert _json_out(run(CREDITS, ["list", "--json"], home))["count"] == 2

    inventory = os.path.join(home, ".claude", "travel-credits", "inventory.md")
    with open(inventory) as fh:
        text = fh.read()
    with open(inventory, "w") as fh:
        fh.write(text.replace(vline(CURRENT_SCHEMA), vline(99), 1))

    payload = _json_out(run(CREDITS, ["migrate", "--json"], home))
    assert payload["unconsumable"] == 1, payload
    assert _json_out(run(CREDITS, ["list", "--json"], home))["count"] == 1


def test_next_id_counts_an_indented_record():
    """An id is never reissued over a record the heading scan failed to see."""
    home = _mktemp("schemaver-indentid-")
    run(CREDITS, ["init", "--default"], home)
    run(CREDITS, ["add", "--type", "ECREDIT", "--desc", "Indented record",
                  "--value", "10.00", "--airline", "DL"], home)

    inventory = os.path.join(home, ".claude", "travel-credits", "inventory.md")
    with open(inventory) as fh:
        text = fh.read()
    with open(inventory, "w") as fh:
        fh.write(text.replace("### #1 ", "  ### #1 "))

    added = _json_out(run(CREDITS, ["add", "--json", "--type", "VOUCHER",
                                    "--desc", "Next record", "--value", "5.00"], home))
    assert added["added"]["id"] == 2, f"id reissued over an indented record: {added}"


def test_migrate_finds_a_version_field_anywhere_in_the_record():
    """The parser accepts the field anywhere in a record; migration must agree.

    Deciding "unversioned" from the line after the heading alone spliced a second
    version field into a record that already carried one further down, leaving the
    record's version order-dependent.
    """
    home = _mktemp("schemaver-late-")
    run(CREDITS, ["init", "--default"], home)
    run(CREDITS, ["add", "--type", "ECREDIT", "--desc", "Late version field",
                  "--value", "10.00"], home)

    inventory = os.path.join(home, ".claude", "travel-credits", "inventory.md")
    with open(inventory) as fh:
        lines = fh.read().split("\n")
    vi = next(k for k, l in enumerate(lines) if l.startswith("- **Schema version**"))
    lines.insert(vi + 1, lines.pop(vi))  # push it below the next field
    with open(inventory, "w") as fh:
        fh.write("\n".join(lines))

    payload = _json_out(run(CREDITS, ["migrate", "--json"], home))
    assert payload["stamped"] == 0, f"a second version field was spliced in: {payload}"
    assert payload["already_current"] == 1, payload
    assert payload["changed"] is False, payload

    with open(inventory) as fh:
        after = fh.read()
    assert after.count("**Schema version**") == 1, f"duplicate version field:\n{after}"
    assert _json_out(run(CREDITS, ["list", "--json"], home))["count"] == 1


def test_migrate_collapses_duplicate_version_fields():
    """Two version fields make a record's version depend on read order — repair it."""
    home = _mktemp("schemaver-dup-")
    run(CREDITS, ["init", "--default"], home)
    run(CREDITS, ["add", "--type", "ECREDIT", "--desc", "Duplicated", "--value", "10.00"], home)

    inventory = os.path.join(home, ".claude", "travel-credits", "inventory.md")
    with open(inventory) as fh:
        text = fh.read()
    with open(inventory, "w") as fh:
        fh.write(text.replace(vline(CURRENT_SCHEMA),
                              vline(CURRENT_SCHEMA) + "\n" + vline(CURRENT_SCHEMA)))

    payload = _json_out(run(CREDITS, ["migrate", "--json"], home))
    assert payload["changed"] is True, payload

    with open(inventory) as fh:
        after = fh.read()
    assert after.count("**Schema version**") == 1, f"duplicates survived:\n{after}"
    assert _json_out(run(CREDITS, ["list", "--json"], home))["count"] == 1


def test_upgrade_record_body_return_value_is_applied():
    """A future non-identity upgrade must actually transform the body.

    The upgrade step's return was discarded, so a SCHEMA_VERSION > 1 rollout would
    have bumped every record's version line over an untransformed body — the exact
    silent-corruption the version exists to make auditable.
    """
    tracker = _load_tracker()
    # setattr, not attribute assignment: the module is loaded by path, so a static
    # checker has no declaration to bind these names to.
    setattr(tracker, "SCHEMA_VERSION", 2)
    setattr(tracker, "upgrade_record_body", lambda body, _v: body + ["- **Added by v2**: yes"])

    store = ("<!-- CREDITS_START -->\n"
             "### #1 — [ECREDIT] Old shape\n"
             "- **Schema version**: 1\n"
             "- **Value**: 10.00\n"
             "<!-- CREDITS_END -->\n")
    migrated, stats = tracker.stamp_schema_version(store)

    assert stats["upgraded"] == 1, stats
    assert "- **Added by v2**: yes" in migrated, f"upgrade output discarded:\n{migrated}"
    assert "- **Schema version**: 2" in migrated, migrated
    assert migrated.count("**Schema version**") == 1, migrated


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
        fh.write(text.replace(vline(CURRENT_SCHEMA), "- **Schema version**: v1-ish"))

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
        fh.write(text.replace(vline(CURRENT_SCHEMA), vline(99)))

    payload = _json_out(run(CREDITS, ["migrate", "--json"], home))
    assert payload["skipped_newer"] == 1, payload

    with open(inventory) as fh:
        after = fh.read()
    assert vline(99) in after, f"newer record was downgraded:\n{after}"

    # And a plain non-owner write leaves it alone too.
    run(CREDITS, ["add", "--type", "VOUCHER", "--desc", "Current credit",
                  "--value", "5.00", "--airline", "AA"], home)
    with open(inventory) as fh:
        assert vline(99) in fh.read()


# ── compensation deposits: history, not inventory ─────────────────────────────

_V1_STORE_WITH_DEPOSITS = """# Flight Credits, Vouchers & Upgrade Certificates Inventory

## Active Credits

<!-- CREDITS_START — do not edit this marker -->

### #1 — [COMP] 25,000 SkyMiles goodwill (Case 18758214)
- **Schema version**: 1
- **Value**: 25000 miles
- **Airline**: DL
- **Confirmation**: Case 18758214
- **Added**: 2026-03-01
- **Unknown field**: preserve me

### #2 — [COMP] Delta Reserve companion cert 2026
- **Schema version**: 1
- **Value**: 1 certificate
- **Expiry**: 2024-01-31
- **Airline**: DL
- **Added**: 2026-01-15

### #3 — [COMP] 30,000 Hilton Honors points goodwill
- **Schema version**: 1
- **Value**: 30,000 Hilton Honors points
- **Brand**: HILTON
- **Added**: 2026-05-02

### #4 — [ECREDIT] Canceled BNA-JFK
- **Schema version**: 1
- **Value**: 347.20
- **Expiry**: 2024-12-15
- **Airline**: DL
- **Added**: 2026-02-01
<!-- CREDITS_END — do not edit this marker -->

## Used/Expired Credits (Archive)

<!-- ARCHIVE_START — do not edit this marker -->
<!-- ARCHIVE_END — do not edit this marker -->
"""


def _v1_store_home(prefix):
    """A pre-v2 store with two miles/points grants mistyped COMP, plus a real cert."""
    home = _mktemp(prefix)
    store = os.path.join(home, ".claude", "travel-credits")
    os.makedirs(store)
    with open(os.path.join(store, "inventory.md"), "w") as fh:
        fh.write(_V1_STORE_WITH_DEPOSITS)
    return home


def test_migration_moves_miles_and_points_grants_out_of_inventory():
    """Deposits have no held-then-applied lifecycle, so they are not inventory.

    An airline granting 25,000 miles deposits them on the spot. Sitting in Active
    they were counted as available forever, and `use` was the only exit — asserting
    an application event that never happened.
    """
    home = _v1_store_home("deposits-migrate-")
    payload = _json_out(run(CREDITS, ["migrate", "--json"], home))

    moved = {m["id"]: m["to_type"] for m in payload["deposits_relocated"]}
    assert moved == {1: "MILES", 3: "POINTS"}, payload
    assert payload["unconsumable"] == 0, payload

    listed = _json_out(run(CREDITS, ["list", "--json"], home))
    assert [c["id"] for c in listed["credits"]] == [2, 4], \
        f"deposits must leave the available set: {listed}"

    history = _json_out(run(CREDITS, ["history", "--json"], home))
    assert [d["id"] for d in history["deposits"]] == [1, 3], history


def test_deposit_classification_covers_the_value_shapes_the_store_uses():
    """Every unit shape seen in the live store, and the non-deposits it must not touch.

    An earlier pattern allowed at most one word between the amount and the unit, so
    "30,000 Hilton Honors points" — taken straight from the store — stayed in Active.
    The first fixture happened to use the one-word variant and passed anyway, which is
    a test written against the implementation rather than the requirement.
    """
    tracker = _load_tracker()
    deposits = {
        "25000 miles": "MILES",
        "30,000 Honors points": "POINTS",
        "30,000 Hilton Honors points": "POINTS",
        "8,000 SkyMiles": "MILES",
        "25,000 American AAdvantage miles": "MILES",
        "5,000 AAdvantage miles": "MILES",
    }
    for value, unit in deposits.items():
        got = tracker.deposit_unit([f"- **Value**: {value}"])
        assert got == unit, f"{value!r} classified {got!r}, expected {unit!r}"

    # A false positive moves a genuine credit out of the available set, so these matter
    # more than the misses: none of them may classify as a deposit. The last two are why
    # the pattern is anchored at both ends — unanchored, each contained a unit and matched.
    for value in ["1 certificate", "347.20", "$200.00", "2 nights", "1 upgrade certificate",
                  "5000 miles voucher", "1 certificate for 5000 miles travel"]:
        got = tracker.deposit_unit([f"- **Value**: {value}"])
        assert got is None, f"{value!r} must not be treated as a deposit, got {got!r}"


def test_migration_relocates_a_deposit_logged_under_any_type():
    """Classification is by Value, not by the type the record happens to carry.

    Keying on COMP alone stranded every deposit logged under another type — and the
    skill's only worked example was `--type VOUCHER`, so those records exist.
    """
    home = _mktemp("deposits-anytype-")
    store = os.path.join(home, ".claude", "travel-credits")
    os.makedirs(store)
    with open(os.path.join(store, "inventory.md"), "w") as fh:
        fh.write(_V1_STORE_WITH_DEPOSITS.replace(
            "### #4 — [ECREDIT] Canceled BNA-JFK\n"
            "- **Schema version**: 1\n"
            "- **Value**: 347.20",
            "### #4 — [VOUCHER] 12,000 SkyMiles goodwill\n"
            "- **Schema version**: 1\n"
            "- **Value**: 12,000 SkyMiles"))

    payload = _json_out(run(CREDITS, ["migrate", "--json"], home))
    moved = {m["id"]: (m["from_type"], m["to_type"]) for m in payload["deposits_relocated"]}
    assert moved.get(4) == ("VOUCHER", "MILES"), f"a VOUCHER-typed deposit must move: {payload}"
    assert _json_out(run(CREDITS, ["list", "--json"], home))["count"] == 1, "only the cert stays"


def test_migration_leaves_a_newer_miles_record_where_it_is():
    """A record this reader cannot read must not be relocated — a move is a rewrite.

    Stamping already leaves a newer record alone. Relocation ran over every active
    record afterwards, so a version-99 miles row would have been moved and retyped by
    a reader with no idea what shape it is in.
    """
    home = _mktemp("deposits-newer-")
    store = os.path.join(home, ".claude", "travel-credits")
    os.makedirs(store)
    with open(os.path.join(store, "inventory.md"), "w") as fh:
        fh.write(_V1_STORE_WITH_DEPOSITS.replace(
            "### #1 — [COMP] 25,000 SkyMiles goodwill (Case 18758214)\n"
            "- **Schema version**: 1",
            "### #1 — [COMP] 25,000 SkyMiles goodwill (Case 18758214)\n"
            "- **Schema version**: 99"))

    payload = _json_out(run(CREDITS, ["migrate", "--json"], home))
    assert payload["skipped_newer"] == 1, payload
    assert 1 not in [m["id"] for m in payload["deposits_relocated"]], \
        f"a newer record must not be relocated: {payload}"

    with open(os.path.join(store, "inventory.md")) as fh:
        after = fh.read()
    active = after.split("<!-- CREDITS_START")[1].split("<!-- CREDITS_END")[0]
    assert "### #1 — [COMP]" in active, f"it must stay put, untyped and unmoved:\n{active}"
    assert "- **Schema version**: 99" in active, "and keep its own version"


def test_migration_renames_comp_to_companion():
    """`COMP` reads as "compensation" and means Companion Certificate.

    Nothing rejected the misreading, and every COMP row in the live store turned out to
    be a mistyped miles or points grant. Those move out on their Value; what remains is
    renamed so the next reader cannot make the same mistake.
    """
    home = _v1_store_home("rename-comp-")
    payload = _json_out(run(CREDITS, ["migrate", "--json"], home))

    renamed = {r["id"]: (r["from_type"], r["to_type"]) for r in payload["types_renamed"]}
    assert renamed == {2: ("COMP", "COMPANION")}, (
        f"only the genuine certificate is renamed; the deposits left on their Value: {payload}")

    listed = _json_out(run(CREDITS, ["list", "--json"], home))
    types = {c["id"]: c["type"] for c in listed["credits"]}
    assert types.get(2) == "COMPANION", types
    assert "COMP" not in types.values(), f"no COMP may survive the migration: {types}"


def test_a_relocated_deposit_is_not_also_renamed():
    """Order matters: a COMP row valued in miles leaves on its Value, unlabelled.

    Renaming before relocating would have stamped COMPANION onto a miles deposit on its
    way out — the exact mislabel this rename exists to end.
    """
    home = _v1_store_home("rename-order-")
    payload = _json_out(run(CREDITS, ["migrate", "--json"], home))

    renamed_ids = {r["id"] for r in payload["types_renamed"]}
    moved_ids = {m["id"] for m in payload["deposits_relocated"]}
    assert not (renamed_ids & moved_ids), f"a record must not be both: {payload}"

    history = _json_out(run(CREDITS, ["history", "--json"], home))
    assert {d["type"] for d in history["deposits"]} == {"MILES", "POINTS"}, history


def test_the_retired_type_is_rejected_with_its_replacement():
    """A retired token gets a message naming what replaced it, not a bare invalid-type."""
    home = _mktemp("rename-retired-")
    run(CREDITS, ["init", "--default"], home)
    r = run(CREDITS, ["add", "--json", "--type", "COMP", "--desc", "Something",
                      "--value", "1 certificate"], home)
    assert r.returncode != 0
    payload = _json_out(r)
    assert payload["error"] == "retired_type", payload
    assert payload["renamed_to"] == "COMPANION", payload
    # The point of the message: say where a miles grant should actually go.
    assert "MILES" in r.stderr and "POINTS" in r.stderr, r.stderr


def test_migration_leaves_a_genuine_companion_certificate_in_place():
    """Classification reads the Value field's unit, not the description's prose."""
    home = _v1_store_home("deposits-keep-")
    run(CREDITS, ["migrate", "--json"], home)

    listed = _json_out(run(CREDITS, ["list", "--json"], home))
    cert = [c for c in listed["credits"] if c["id"] == 2]
    assert cert and cert[0]["type"] == "COMPANION", \
        f"a real companion cert must stay, renamed: {listed}"


def test_migration_preserves_fields_the_formatter_does_not_know():
    """A relocated record moves verbatim apart from its type token."""
    home = _v1_store_home("deposits-preserve-")
    run(CREDITS, ["migrate", "--json"], home)

    with open(os.path.join(home, ".claude", "travel-credits", "inventory.md")) as fh:
        after = fh.read()
    assert "- **Unknown field**: preserve me" in after, f"field dropped:\n{after}"
    assert "Case 18758214" in after


def test_deposits_are_excluded_from_matching_expiry_and_the_total():
    """Never available inventory: not matched, not a deadline, not money on hand."""
    home = _v1_store_home("deposits-excluded-")
    run(CREDITS, ["migrate", "--json"], home)

    checked = _json_out(run(CREDITS, ["check", "--json", "--scenario",
                                      "round-trip domestic DL"], home))
    assert 1 not in [m["id"] for m in checked["matches"]], \
        f"a miles deposit must not be offered as bookable: {checked}"

    expiring = _json_out(run(CREDITS, ["expiring", "--json"], home))
    assert 1 not in [e["id"] for e in expiring["expiring"]], expiring
    assert 1 not in [e["id"] for e in expiring["no_expiry"]], \
        "a deposit is not an undated credit — it is not a credit"

    summary = _json_out(run(CREDITS, ["summary", "--json"], home))
    assert summary["total_monetary_value"] == 347.20, \
        f"deposits must not count as available value: {summary}"


def test_a_deposit_has_no_use_transition():
    """`use` on a deposit asserts an event that never happened — refuse it."""
    home = _v1_store_home("deposits-nouse-")
    run(CREDITS, ["migrate", "--json"], home)

    r = run(CREDITS, ["use", "--json", "--id", "1", "--note", "spent them"], home)
    assert r.returncode != 0, r.stdout
    assert _json_out(r)["error"] == "deposit_has_no_use_transition", r.stdout

    history = _json_out(run(CREDITS, ["history", "--json"], home))
    assert [d["id"] for d in history["deposits"]] == [1, 3], "the record must survive intact"


def test_adding_a_deposit_writes_to_history_not_inventory():
    home = _mktemp("deposits-add-")
    run(CREDITS, ["init", "--default"], home)
    assert run(CREDITS, ["add", "--json", "--type", "MILES", "--desc", "8,000 SkyMiles goodwill",
                         "--value", "8000 miles", "--airline", "DL"], home).returncode == 0

    assert _json_out(run(CREDITS, ["list", "--json"], home))["count"] == 0
    assert _json_out(run(CREDITS, ["history", "--json"], home))["count"] == 1


def test_a_deposit_rejects_an_expiry():
    """A deposit is in the account already; a deadline here would be unenforceable."""
    home = _mktemp("deposits-expiry-")
    run(CREDITS, ["init", "--default"], home)
    r = run(CREDITS, ["add", "--json", "--type", "POINTS", "--desc", "Goodwill points",
                      "--value", "5000 points", "--expiry", "2024-01-01"], home)
    assert r.returncode != 0
    assert _json_out(r)["error"] == "expiry_not_valid_for_deposit", r.stdout


def test_migration_adds_the_compensation_section_to_an_older_store():
    """A store written before the section existed gains it without losing anything."""
    home = _v1_store_home("deposits-section-")
    with open(os.path.join(home, ".claude", "travel-credits", "inventory.md")) as fh:
        assert "COMPENSATION_START" not in fh.read()

    run(CREDITS, ["migrate", "--json"], home)
    with open(os.path.join(home, ".claude", "travel-credits", "inventory.md")) as fh:
        after = fh.read()
    assert "COMPENSATION_START" in after and "COMPENSATION_END" in after
    assert "Canceled BNA-JFK" in after, "untouched records must survive the section append"


def test_the_advocate_skill_reads_history_for_prior_compensation():
    """Step 4's prior-compensation check must reach deposits, not only Active.

    Deposits leave Active in v2, so a Step 4 that ran `list` alone would report "no
    prior compensation" for a passenger the airline has already paid off — throwing
    away the strongest leverage the letter has.
    """
    skill = os.path.normpath(os.path.join(HERE, "..", "SKILL.md"))
    with open(skill, encoding="utf-8") as fh:
        text = fh.read()
    step4 = text.split("## Step 4 —")[1].split("\n## ")[0]
    assert 'Skill(skill: "using-travel-credits")' in step4, \
        "Step 4 must read through the owner skill, which migrates before it reads"
    invocations = [ln for ln in step4.split("\n")
                   if "python3 " in ln and "credits-tracker.py" in ln]
    assert not invocations, (
        "a direct read here is a non-owner read: un-migrated records are skipped and the "
        f"count: 0 gets recorded as evidence of no prior compensation — {invocations}")
    assert "history" in step4, "it must reach deposits, not only the active list"


def test_an_unknown_section_name_fails_loudly():
    """The old two-section form defaulted anything not "active" to the archive."""
    tracker = _load_tracker()
    try:
        tracker.section_markers("activ")
    except ValueError as exc:
        assert "unknown section" in str(exc), exc
    else:
        raise AssertionError("a mistyped section name must not silently resolve")


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
