#!/usr/bin/env python3
"""letter-fit.py — measure a complaint letter against an airline's submission-form limits.

Why this exists: a Southwest draft measured 2472 by Python len(), was declared "under the
2500 limit", and the live form's counter came back 2798. No standard encoding of that text
reproduces 2798 (UTF-8 bytes 2482, CRLF 2495, HTML entities 2500), so a single eyeballed or
inline count is not evidence a letter fits. Call this script instead, and show the user its
output rather than your own arithmetic.

Usage:
  letter-fit.py --airline WN --file letter.txt
  letter-fit.py --airline AA --stdin < letter.txt
  letter-fit.py --airline DL --limit 4000 --file letter.txt   # limit the user read off the form
  letter-fit.py --airline AA --info                           # channels + notes, no letter
  letter-fit.py --list-airlines

Output: one JSON object on stdout, success or failure, diagnostics on stderr. The caller
renders it for the user — the script measures and never writes prose
(rules/script-delegation.md). `--help` is the sole exception: it prints usage and exits 0.

A fit check reports every count, the figure the verdict was judged at, whether that figure is
verified, the headroom, the status, formatting warnings, and the fields the form captures on
its own. `worst_count` and `effective_count` are precomputed so no caller does the arithmetic.

A failure reports {"error": <code>, "message": <text>}, plus whatever context the code
carries (`path`, `known`, `given`, `usage`). Codes: bad_arguments, input_not_found,
input_not_a_file, input_unreadable, input_not_utf8, empty_letter, metadata_missing,
metadata_invalid_json, unknown_airline, unknown_channel. Branch on `error` being present,
never on stderr text.

Data lives in airline-form-metadata.json beside this script; --metadata points at another
copy. Only verified limits belong in it — pass --limit for a form nobody has recorded yet.

Counting: the letter is measured under every encoding in COUNTERS. When the channel's
`counting_method` names one of them, that count is authoritative. When it is "unknown" the
worst count is multiplied by an inflation factor (the channel's `observed_inflation`, else
UNKNOWN_INFLATION_DEFAULT) and the verdict is reported as unverified — the Southwest case is
what that margin exists to absorb.

Formatting: markdown the form may render literally is flagged against the channel's
`formatting` map. A `false` or `"unknown"` entry flags; only an explicit `true` stays silent.

Exit codes: 0 the letter fits (or the channel has no limit), 1 it overflows, 2 argument or
metadata error.
"""

import argparse
import json
import math
import os
import re
import sys
from typing import NoReturn

DEFAULT_METADATA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                     "airline-form-metadata.json")

# Applied when a channel's counting_method is "unknown" and it carries no observed_inflation
# of its own. The only form ever measured (Southwest, 2026-06-13) counted 13.2% above the
# text's codepoints; 1.15 rounds that up and stands as the floor assumption for any form
# whose method nobody has established. Lower it only against a measurement.
UNKNOWN_INFLATION_DEFAULT = 1.15

# Headroom below which a letter is reported TIGHT — it fits, with no room to absorb a
# counting method that turns out to differ from the one assumed here.
TIGHT_HEADROOM_CHARS = 50

# Specials a rich-text field would HTML-escape before storing.
HTML_ESCAPES = (
    ("&", "&amp;"), ("<", "&lt;"), (">", "&gt;"),
    ("—", "&mdash;"), ("–", "&ndash;"), ("•", "&bull;"),
    ("“", "&ldquo;"), ("”", "&rdquo;"),
    ("‘", "&lsquo;"), ("’", "&rsquo;"),
)


def _count_codepoints(text):
    return len(text)


def _count_utf8_bytes(text):
    return len(text.encode("utf-8"))


def _count_crlf(text):
    return len(text.replace("\n", "\r\n"))


def _count_html_entities(text):
    encoded = text
    for raw, entity in HTML_ESCAPES:
        encoded = encoded.replace(raw, entity)
    return len(encoded)


COUNTERS = {
    "codepoints": _count_codepoints,
    "utf8_bytes": _count_utf8_bytes,
    "crlf": _count_crlf,
    "html_entities": _count_html_entities,
}

_LINK_RE = re.compile(r"\[[^\]\n]+\]\([^)\n]+\)")


def _count_bold(text):
    return text.count("**") // 2


def _count_headers(text):
    return sum(1 for ln in text.split("\n") if ln.lstrip().startswith("#"))


def _count_bullets(text):
    return sum(1 for ln in text.split("\n")
               if ln.lstrip()[:2] in ("- ", "* ", "+ "))


def _count_blockquotes(text):
    return sum(1 for ln in text.split("\n") if ln.lstrip().startswith("> "))


def _count_links(text):
    return len(_LINK_RE.findall(text))


def _count_unicode_bullets(text):
    return text.count("•")


# (formatting key, counter, what was found, how it fails)
FORMAT_CHECKS = (
    ("markdown_bold", _count_bold, "**bold** pair(s)", "render as literal asterisks"),
    ("markdown_headers", _count_headers, "markdown heading line(s)", "render as literal #"),
    ("markdown_bullets", _count_bullets, "markdown bullet line(s)", "render as literal -/*/+"),
    ("markdown_blockquotes", _count_blockquotes, "markdown blockquote line(s)",
     "render as literal > and lose the quoted-policy framing"),
    ("markdown_links", _count_links, "markdown link(s)", "render as literal [text](url)"),
    ("unicode_bullets", _count_unicode_bullets, "unicode bullet(s)",
     "render as a replacement glyph"),
)


def die(code, message, **fields) -> NoReturn:
    """Emit a structured failure on stdout, an actionable diagnostic on stderr, exit 2.

    stdout carries a JSON object on every run that measures or fails, so a caller parsing
    the documented interface handles failures the same way it handles verdicts. The prose
    on stderr is for the human reading the terminal, and mirrors the `message` field.
    """
    emit({"error": code, "message": message, **fields})
    print(f"ERROR: {message}", file=sys.stderr)
    sys.exit(2)


class JsonArgumentParser(argparse.ArgumentParser):
    """argparse exits 2 with bare usage text on a bad flag, which would be the one hole
    left in the stdout contract. Route it through die() like every other failure."""

    def error(self, message) -> NoReturn:
        die("bad_arguments", message, usage=self.format_usage().strip())


def read_text_file(path, what, missing_hint):
    """Read a UTF-8 text file, routing every expected I/O failure through die().

    Exit 2 is a documented part of this script's interface, so a directory, a permission
    denial, or non-UTF-8 bytes has to reach the caller as an actionable diagnostic rather
    than a traceback. Subclasses of OSError are caught before the OSError catch-all.
    """
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        die("input_not_found", f"{what} not found: {path}. {missing_hint}", path=path)
    except IsADirectoryError:
        die("input_not_a_file", f"{what} path is a directory, not a file: {path}", path=path)
    except PermissionError:
        die("input_unreadable",
            f"cannot read {what} at {path}: permission denied. "
            f"Grant read access (chmod +r {path}) and rerun.", path=path)
    except UnicodeDecodeError:
        die("input_not_utf8",
            f"{what} at {path} is not valid UTF-8. Re-save it as UTF-8 and rerun.",
            path=path)
    except OSError as e:
        die("input_unreadable", f"cannot read {what} at {path}: {e.strerror or e}.", path=path)


def load_metadata(path):
    raw = read_text_file(
        path, "metadata file",
        "It ships beside this script — restore it from the plugin "
        "(tessl install jbaruch/frequent-flyer-advocate), or point --metadata at your copy.")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        die("metadata_invalid_json",
            f"{path} is not valid JSON ({e}). Fix the file before rerunning.", path=path)


def read_letter(args):
    if args.file and args.stdin:
        die("bad_arguments", "pass --file or --stdin, not both.")
    if args.file:
        text = read_text_file(
            args.file, "letter file",
            "Write the draft to a file first, or pipe it in with --stdin.")
    elif args.stdin:
        try:
            text = sys.stdin.read()
        except UnicodeDecodeError:
            die("input_not_utf8", "the text piped in on stdin is not valid UTF-8. "
                "Re-encode it as UTF-8 and rerun.")
        except OSError as e:
            die("input_unreadable", f"cannot read the letter from stdin: {e.strerror or e}.")
    else:
        die("bad_arguments", "no letter supplied. Pass --file <path> or --stdin "
            "(or use --info / --list-airlines, which need no letter).")
    # A file or heredoc appends exactly one newline the author did not type, so exactly one
    # comes off — unconditionally, whatever the count. Content ending in a deliberate blank
    # line arrives as "…\n\n" and correctly keeps one newline. Stripping only when a single
    # newline is present would leave that authored blank line double-counted. Interior blank
    # lines are untouched.
    if text.endswith("\n"):
        text = text[:-1]
    if not text.strip():
        die("empty_letter", "the letter is empty. Nothing to measure.")
    return text


def resolve_channel(md, airline, channel_name, limit_override, metadata_path):
    """Return (airline_meta, channel_meta) for the requested airline/channel.

    An airline absent from the metadata is usable when the caller supplies --limit: the
    letter still gets measured, against an unknown counting method.
    """
    airlines = md.get("airlines", {})
    airline_meta = airlines.get(airline)
    if airline_meta is None:
        if limit_override is None:
            die("unknown_airline",
                known=sorted(airlines),
                message=f"airline {airline!r} is not in {os.path.basename(metadata_path)} "
                f"(known: {', '.join(sorted(airlines)) or 'none'}).\n"
                f"  Ask the user for the form's character limit and pass it: "
                f"--airline {airline} --limit <N>\n"
                f"  Then add the verified limit to the metadata file so the next run "
                f"needs no override.")
        return ({"name": airline, "channels": {}}, {"char_limit": None,
                                                    "counting_method": "unknown"})

    channels = airline_meta.get("channels", {})
    channel_meta = channels.get(channel_name)
    if channel_meta is None:
        die("unknown_channel",
            f"channel {channel_name!r} is not configured for {airline} "
            f"(configured: {', '.join(sorted(channels)) or 'none'}).",
            known=sorted(channels))
    return airline_meta, channel_meta


def measure(text, channel_meta, limit_override):
    """Count the letter every way and pick the figure to judge it by."""
    counts = {name: fn(text) for name, fn in COUNTERS.items()}
    method = channel_meta.get("counting_method", "unknown")

    if method in COUNTERS:
        effective = counts[method]
        inflation = None
        verified = True
    else:
        inflation = channel_meta.get("observed_inflation", UNKNOWN_INFLATION_DEFAULT)
        effective = math.ceil(max(counts.values()) * inflation)
        verified = False

    limit = limit_override if limit_override is not None else channel_meta.get("char_limit")
    if limit is None:
        status, headroom = "NO_LIMIT", None
    else:
        headroom = limit - effective
        if headroom < 0:
            status = "OVERFLOW"
        elif headroom < TIGHT_HEADROOM_CHARS:
            status = "TIGHT"
        else:
            status = "FITS"

    return {
        "counts": counts,
        "worst_count": max(counts.values()),
        "counting_method": method,
        "inflation_applied": inflation,
        "effective_count": effective,
        "count_verified": verified,
        "char_limit": limit,
        "headroom": headroom,
        "status": status,
    }


def format_warnings(text, formatting):
    """Flag markup the channel does not confirm it renders. Only an explicit true stays silent."""
    warnings = []
    for key, counter, found, consequence in FORMAT_CHECKS:
        declared = formatting.get(key, "unknown")
        if declared is True:
            continue
        n = counter(text)
        if n:
            warnings.append(f"{n} {found} — {key}={declared!r} for this channel; "
                            f"will {consequence}. Strip before submitting.")
    return warnings


def build_report(text, airline, channel_name, airline_meta, channel_meta, limit_override):
    # An operator passing --limit read the number off the live form — the same provenance
    # every verified limit in the metadata has, so it outranks whatever the file records.
    overridden = limit_override is not None
    report = {
        "airline": airline,
        "airline_name": airline_meta.get("name", airline),
        "channel": channel_name,
        "url": channel_meta.get("url"),
        "limit_verified": True if overridden else channel_meta.get("limit_verified", False),
        "limit_source": ("--limit override, read off the live form" if overridden
                         else channel_meta.get("limit_source")),
        "prefilled_fields": channel_meta.get("prefilled_fields"),
        "channel_notes": airline_meta.get("channel_notes"),
    }
    report.update(measure(text, channel_meta, limit_override))
    report["formatting_warnings"] = format_warnings(text, channel_meta.get("formatting", {}))
    return report


def emit(payload):
    """Write the structured result to stdout. Every mode returns data, never prose."""
    print(json.dumps(payload, indent=2, ensure_ascii=False))


def main():
    p = JsonArgumentParser(
        description="Measure a complaint letter against an airline's submission-form limits.")
    p.add_argument("--airline", help="IATA airline code, e.g. AA, WN")
    p.add_argument("--channel", default="web_form",
                   help="channel to measure against (default: web_form)")
    p.add_argument("--file", help="read the letter from this file")
    p.add_argument("--stdin", action="store_true", help="read the letter from stdin")
    p.add_argument("--limit", type=int,
                   help="character limit read off the live form; overrides the metadata")
    p.add_argument("--info", action="store_true",
                   help="emit the airline's channels and notes; no letter needed")
    p.add_argument("--list-airlines", action="store_true",
                   help="emit the airlines the metadata knows; no letter needed")
    p.add_argument("--metadata", default=DEFAULT_METADATA_FILE,
                   help="metadata file to read (default: the one shipped beside this script)")
    args = p.parse_args()

    md = load_metadata(args.metadata)

    if args.list_airlines:
        emit({"airlines": md.get("airlines", {})})
        return 0

    if not args.airline:
        die("bad_arguments", "--airline is required (or use --list-airlines).")
    if args.limit is not None and args.limit <= 0:
        die("bad_arguments", f"--limit must be a positive character count, got {args.limit}.", given=args.limit)

    airline = args.airline.upper()

    if args.info:
        airline_meta = md.get("airlines", {}).get(airline)
        if airline_meta is None:
            die("unknown_airline",
                f"airline {airline!r} is not in {os.path.basename(args.metadata)} "
                f"(known: {', '.join(sorted(md.get('airlines', {}))) or 'none'}).",
                known=sorted(md.get("airlines", {})))
        emit({"airline": airline, "metadata": airline_meta})
        return 0

    text = read_letter(args)
    airline_meta, channel_meta = resolve_channel(md, airline, args.channel, args.limit,
                                                 args.metadata)
    report = build_report(text, airline, args.channel, airline_meta, channel_meta, args.limit)
    emit(report)
    return 1 if report["status"] == "OVERFLOW" else 0


if __name__ == "__main__":
    sys.exit(main())
