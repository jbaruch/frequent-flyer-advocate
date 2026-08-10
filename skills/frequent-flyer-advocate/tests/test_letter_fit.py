#!/usr/bin/env python3
"""Outcome-focused tests for letter-fit.py.

Asserts observable behavior (exit codes, stdout, JSON report) rather than internals. Each
test runs the real CLI in a subprocess. Deterministic: every letter and every metadata
fixture is built programmatically from fixed strings — no randomness, no network, no clock,
no shared state between tests.

Run directly:  python3 test_letter_fit.py   (exit 0 = all passed, 1 = a failure)
Also discoverable by pytest (test_* functions).
"""

import atexit
import json
import os
import shutil
import subprocess
import sys
import tempfile

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
LETTER_FIT = os.path.join(SCRIPTS, "letter-fit.py")
SHIPPED_METADATA = os.path.join(SCRIPTS, "airline-form-metadata.json")

# Exit codes the script promises.
FITS, OVERFLOW, ARG_ERROR = 0, 1, 2

# A short, well-formed letter body used wherever the content itself is not under test.
PLAIN_LETTER = (
    "As an AAdvantage Platinum member of twelve years and 1.2 million lifetime miles, "
    "I am writing about Flight AA100 on 15 January 2026.\n\n"
    "Per FlightAware records the aircraft departed at 19:42, three hours behind schedule.\n\n"
    "I request a response within 21 business days."
)


def run(args, stdin_text=None):
    return subprocess.run(
        [sys.executable, LETTER_FIT, *args],
        capture_output=True, text=True, input=stdin_text,
    )


def write_letter(text, name="letter.txt"):
    path = os.path.join(_mktemp(prefix="ffa-letter-"), name)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def write_metadata(airlines):
    """Write a throwaway metadata file so counting/formatting rules are testable directly."""
    path = os.path.join(_mktemp(prefix="ffa-meta-"), "airline-form-metadata.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"_version": 1, "airlines": airlines}, f)
    return path


def channel(**overrides):
    ch = {"char_limit": 1000, "limit_verified": True, "limit_source": "fixture",
          "counting_method": "codepoints", "formatting": {}}
    ch.update(overrides)
    return ch


def one_airline(code="ZZ", name="Fixture Air", **channel_overrides):
    return {code: {"name": name, "channels": {"web_form": channel(**channel_overrides)}}}


def report(args, stdin_text=None):
    """Run a fit check and return (parsed JSON report, exit code)."""
    r = run(args, stdin_text=stdin_text)
    assert r.returncode in (FITS, OVERFLOW), f"expected a report, got {r.returncode}\n{r.stderr}"
    return json.loads(r.stdout), r.returncode


# ── the failure this script exists to prevent ─────────────────────────────────

def test_reported_southwest_draft_is_caught():
    # The 2026-06-13 case: a 2472-codepoint draft was declared under Southwest's 2500 limit
    # and the live form's counter came back 2798. With the recorded inflation for WN, the
    # script must refuse the draft rather than let it be presented.
    text = "As an A-List Preferred member, I am writing about Flight WN1234.\n\n"
    text += "x" * (2472 - len(text))
    letter = write_letter(text)
    assert len(text) == 2472
    r = run(["--airline", "WN", "--file", letter])
    assert r.returncode == OVERFLOW, f"the reported draft must overflow:\n{r.stdout}{r.stderr}"
    assert "OVERFLOW" in r.stdout, r.stdout


def test_plain_len_under_limit_is_not_enough_on_its_own():
    # Same shape, stated as a property: a letter whose codepoint count fits can still be
    # rejected, because an unestablished counting method is judged with a margin.
    text = "y" * 2300
    rep, code = report(["--airline", "WN", "--file", write_letter(text)])
    assert rep["counts"]["codepoints"] < rep["char_limit"], rep
    assert rep["effective_count"] > rep["counts"]["codepoints"], rep
    assert code == OVERFLOW and rep["status"] == "OVERFLOW", rep


# ── counting ──────────────────────────────────────────────────────────────────

def test_verified_counting_method_is_used_exactly():
    md = write_metadata(one_airline(counting_method="codepoints"))
    rep, code = report(["--airline", "ZZ", "--metadata", md, "--file", write_letter("abc")])
    assert rep["effective_count"] == 3, rep
    assert rep["inflation_applied"] is None, rep
    assert rep["count_verified"] is True, rep
    assert code == FITS


def test_utf8_counting_method_charges_for_non_ascii():
    md = write_metadata(one_airline(counting_method="utf8_bytes"))
    rep, _ = report(["--airline", "ZZ", "--metadata", md, "--file", write_letter("a—b")])
    assert rep["counts"]["codepoints"] == 3, rep
    assert rep["effective_count"] == 5, rep  # em-dash is 3 bytes in UTF-8


def test_unknown_counting_method_inflates_the_worst_count():
    md = write_metadata(one_airline(counting_method="unknown"))
    rep, _ = report(["--airline", "ZZ", "--metadata", md, "--file", write_letter("z" * 100)])
    assert rep["effective_count"] > max(rep["counts"].values()), rep
    assert rep["inflation_applied"] is not None, rep
    assert rep["count_verified"] is False, rep


def test_channel_inflation_overrides_the_default():
    lenient = write_metadata(one_airline(counting_method="unknown", observed_inflation=1.01))
    strict = write_metadata(one_airline(counting_method="unknown", observed_inflation=2.0))
    letter = write_letter("z" * 100)
    low, _ = report(["--airline", "ZZ", "--metadata", lenient, "--file", letter])
    high, _ = report(["--airline", "ZZ", "--metadata", strict, "--file", letter])
    assert low["effective_count"] < high["effective_count"], (low, high)
    assert high["effective_count"] == 200, high


def test_tight_headroom_is_reported_but_still_fits():
    md = write_metadata(one_airline(counting_method="codepoints", char_limit=1000))
    rep, code = report(["--airline", "ZZ", "--metadata", md, "--file", write_letter("z" * 990)])
    assert rep["status"] == "TIGHT", rep
    assert code == FITS, "a tight letter still fits — only overflow exits non-zero"


def test_channel_without_a_limit_reports_no_limit():
    md = write_metadata(one_airline(char_limit=None))
    rep, code = report(["--airline", "ZZ", "--metadata", md, "--file", write_letter("z" * 9000)])
    assert rep["status"] == "NO_LIMIT", rep
    assert rep["headroom"] is None, rep
    assert code == FITS


# ── the --limit override ──────────────────────────────────────────────────────

def test_unknown_airline_without_limit_is_rejected():
    r = run(["--airline", "QQ", "--file", write_letter(PLAIN_LETTER)])
    assert r.returncode == ARG_ERROR, r.stdout
    assert "--limit" in r.stderr, f"the error must name the way forward:\n{r.stderr}"


def test_unknown_airline_with_limit_is_measured():
    rep, code = report(["--airline", "QQ", "--limit", "9000",
                        "--file", write_letter(PLAIN_LETTER)])
    assert rep["char_limit"] == 9000, rep
    assert rep["count_verified"] is False, "no known counting method for an unknown airline"
    assert code == FITS


def test_limit_override_beats_the_metadata_limit():
    letter = write_letter(PLAIN_LETTER)
    wide, wide_code = report(["--airline", "WN", "--file", letter])
    narrow, narrow_code = report(["--airline", "WN", "--limit", "50", "--file", letter])
    assert wide["char_limit"] == 2500 and wide_code == FITS, wide
    assert narrow["char_limit"] == 50 and narrow_code == OVERFLOW, narrow


def test_limit_override_counts_as_a_verified_limit():
    rep, _ = report(["--airline", "QQ", "--limit", "9000", "--file", write_letter(PLAIN_LETTER)])
    assert rep["limit_verified"] is True, rep
    assert "--limit" in (rep["limit_source"] or ""), rep


def test_nonpositive_limit_is_rejected():
    for bad in ("0", "-5"):
        r = run(["--airline", "AA", "--limit", bad, "--file", write_letter(PLAIN_LETTER)])
        assert r.returncode == ARG_ERROR, f"--limit {bad}: {r.stdout}{r.stderr}"
        assert "positive" in r.stderr, r.stderr


# ── formatting warnings ───────────────────────────────────────────────────────

def _warnings_for(text, formatting):
    md = write_metadata(one_airline(formatting=formatting))
    rep, _ = report(["--airline", "ZZ", "--metadata", md, "--file", write_letter(text)])
    return rep["formatting_warnings"]


def test_unsupported_markdown_is_flagged():
    cases = [
        ("markdown_bold", "I am a **Platinum** member."),
        ("markdown_headers", "Incident\n\n# What happened\n\nIt was late."),
        ("markdown_bullets", "Losses:\n\n- one hotel night\n- one meal"),
        ("markdown_blockquotes", 'Your plan says:\n\n> "We treat you well."'),
        ("markdown_links", "See [FlightAware](https://flightaware.com) for the record."),
        ("unicode_bullets", "Losses:\n\n• one hotel night"),
    ]
    for key, text in cases:
        warnings = _warnings_for(text, {key: False})
        assert any(key in w for w in warnings), f"{key} must be flagged, got {warnings}"


def test_unknown_formatting_support_is_flagged_like_unsupported():
    # An unrecorded field is not permission to use markdown — it is the reason to check.
    assert _warnings_for("I am **Platinum**.", {"markdown_bold": "unknown"}), \
        "an 'unknown' formatting entry must still warn"
    assert _warnings_for("I am **Platinum**.", {}), \
        "a missing formatting entry must still warn"


def test_confirmed_formatting_support_is_silent():
    assert _warnings_for("I am **Platinum**.", {"markdown_bold": True}) == [], \
        "only an explicit true suppresses the warning"


def test_clean_letter_produces_no_warnings():
    assert _warnings_for(PLAIN_LETTER, {}) == [], "plain prose must not trip any check"


# ── input handling ────────────────────────────────────────────────────────────

def test_file_and_stdin_measure_the_same_letter():
    letter = write_letter(PLAIN_LETTER + "\n")  # a file's terminating newline
    from_file, _ = report(["--airline", "AA", "--file", letter])
    from_stdin, _ = report(["--airline", "AA", "--stdin"], stdin_text=PLAIN_LETTER + "\n")
    assert from_file["counts"] == from_stdin["counts"], (from_file, from_stdin)
    assert from_file["counts"]["codepoints"] == len(PLAIN_LETTER), from_file


def test_only_the_authors_trailing_newline_survives():
    # One newline comes off unconditionally: a file appends exactly one the author did not
    # type. A deliberate trailing blank line arrives as "…\n\n" and must keep one newline,
    # so an authored blank line is counted once — never zero times, never twice.
    no_blank, _ = report(["--airline", "AA", "--stdin"], stdin_text="abc\n")
    one_blank, _ = report(["--airline", "AA", "--stdin"], stdin_text="abc\n\n")
    assert no_blank["counts"]["codepoints"] == 3, no_blank
    assert one_blank["counts"]["codepoints"] == 4, one_blank


def test_interior_blank_lines_are_preserved():
    spaced, _ = report(["--airline", "AA", "--stdin"], stdin_text="a\n\n\nb")
    assert spaced["counts"]["codepoints"] == 5, spaced


def test_missing_letter_file_is_rejected():
    r = run(["--airline", "AA", "--file", os.path.join(_mktemp("ffa-gone-"), "nope.txt")])
    assert r.returncode == ARG_ERROR
    assert "not found" in r.stderr, r.stderr


def test_file_and_stdin_together_are_rejected():
    r = run(["--airline", "AA", "--file", write_letter(PLAIN_LETTER), "--stdin"], stdin_text="x")
    assert r.returncode == ARG_ERROR
    assert "not both" in r.stderr, r.stderr


def test_no_letter_source_is_rejected():
    r = run(["--airline", "AA"])
    assert r.returncode == ARG_ERROR
    assert "--file" in r.stderr and "--stdin" in r.stderr, r.stderr


def test_empty_letter_is_rejected():
    r = run(["--airline", "AA", "--stdin"], stdin_text="   \n\n  \n")
    assert r.returncode == ARG_ERROR
    assert "empty" in r.stderr, r.stderr


def test_airline_code_is_case_insensitive():
    lower, _ = report(["--airline", "aa", "--file", write_letter(PLAIN_LETTER)])
    assert lower["airline"] == "AA", lower


def test_unconfigured_channel_is_rejected():
    r = run(["--airline", "WN", "--channel", "telegram", "--file", write_letter(PLAIN_LETTER)])
    assert r.returncode == ARG_ERROR
    assert "web_form" in r.stderr, f"the error must list what is configured:\n{r.stderr}"


def test_unreadable_metadata_is_rejected():
    path = os.path.join(_mktemp("ffa-badmeta-"), "airline-form-metadata.json")
    with open(path, "w", encoding="utf-8") as f:
        f.write("{not json")
    r = run(["--airline", "AA", "--metadata", path, "--file", write_letter(PLAIN_LETTER)])
    assert r.returncode == ARG_ERROR
    assert "not valid JSON" in r.stderr, r.stderr


# ── report surfaces the skill reads ───────────────────────────────────────────

def test_json_report_carries_the_fields_the_skill_acts_on():
    rep, _ = report(["--airline", "AA", "--file", write_letter(PLAIN_LETTER)])
    for key in ("airline", "channel", "char_limit", "counts", "worst_count",
                "effective_count", "count_verified", "headroom", "status",
                "formatting_warnings", "prefilled_fields", "channel_notes"):
        assert key in rep, f"{key} missing from the JSON report: {sorted(rep)}"


def test_prefilled_fields_tell_the_skill_what_to_drop():
    rep, _ = report(["--airline", "AA", "--file", write_letter(PLAIN_LETTER)])
    assert "loyalty_number" in rep["prefilled_fields"], rep
    assert "flight_number" in rep["prefilled_fields"], rep


def test_every_counting_method_is_reported():
    rep, _ = report(["--airline", "AA", "--file", write_letter(PLAIN_LETTER)])
    assert set(rep["counts"]) == {"codepoints", "utf8_bytes", "crlf", "html_entities"}, rep
    assert rep["worst_count"] == max(rep["counts"].values()), rep


def test_stdout_is_json_in_every_mode():
    # script-delegation: the script emits structured data; the skill renders the prose.
    for args in (["--list-airlines"], ["--airline", "AA", "--info"],
                 ["--airline", "AA", "--file", write_letter(PLAIN_LETTER)]):
        r = run(args)
        assert r.returncode == FITS, f"{args}: {r.stderr}"
        json.loads(r.stdout)  # raises if the mode emitted prose


def test_list_airlines_names_the_seeded_carriers():
    r = run(["--list-airlines"])
    assert r.returncode == FITS, r.stderr
    assert set(json.loads(r.stdout)["airlines"]) >= {"AA", "WN"}, r.stdout


def test_info_reports_channel_notes_without_a_letter():
    r = run(["--airline", "AA", "--info"])
    assert r.returncode == FITS, r.stderr
    notes = json.loads(r.stdout)["metadata"]["channel_notes"]
    assert "executive" in notes.lower(), f"AA's channel notes must surface: {notes}"


def test_info_on_an_unknown_airline_is_rejected():
    r = run(["--airline", "QQ", "--info"])
    assert r.returncode == ARG_ERROR
    assert "not in" in r.stderr, r.stderr


# ── the shipped metadata itself ───────────────────────────────────────────────

def test_shipped_metadata_records_provenance_for_every_limit():
    with open(SHIPPED_METADATA, encoding="utf-8") as f:
        md = json.load(f)
    known_methods = {"codepoints", "utf8_bytes", "crlf", "html_entities", "unknown"}
    assert md["airlines"], "the shipped metadata must seed at least one airline"
    for code, airline in md["airlines"].items():
        assert airline.get("name"), f"{code}: missing name"
        for chan_name, chan in airline["channels"].items():
            where = f"{code}.{chan_name}"
            if chan.get("char_limit") is None:
                continue
            assert isinstance(chan["char_limit"], int), f"{where}: char_limit must be an int"
            assert "limit_verified" in chan, f"{where}: unrecorded whether the limit is verified"
            assert chan.get("limit_source"), f"{where}: a limit with no source is a guess"
            assert chan.get("counting_method") in known_methods, \
                f"{where}: counting_method {chan.get('counting_method')!r} is not a known name"


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
