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
  letter-fit.py --airline AA --file letter.txt --json         # machine-readable report

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


def die(message) -> NoReturn:
    """Exit 2 with an actionable diagnostic on stderr."""
    print(f"ERROR: {message}", file=sys.stderr)
    sys.exit(2)


def load_metadata(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        die(f"metadata file missing at {path}. It ships beside this script — restore it "
            f"from the plugin (tessl install jbaruch/frequent-flyer-advocate).")
    except json.JSONDecodeError as e:
        die(f"{path} is not valid JSON ({e}). Fix the file before rerunning.")


def read_letter(args):
    if args.file and args.stdin:
        die("pass --file or --stdin, not both.")
    if args.file:
        try:
            with open(args.file, encoding="utf-8") as f:
                text = f.read()
        except FileNotFoundError:
            die(f"letter file not found: {args.file}. Write the draft to a file first, "
                f"or pipe it in with --stdin.")
        except IsADirectoryError:
            die(f"--file expects a file, got a directory: {args.file}")
    elif args.stdin:
        text = sys.stdin.read()
    else:
        die("no letter supplied. Pass --file <path> or --stdin (or use --info / "
            "--list-airlines, which need no letter).")
    # Drop the single trailing newline a file or a heredoc adds, so `--file letter.txt` and
    # `cat letter.txt | --stdin` measure the same thing. Interior blank lines are preserved.
    if text.endswith("\n"):
        text = text[:-1]
    if not text.strip():
        die("the letter is empty. Nothing to measure.")
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
            die(f"airline {airline!r} is not in {os.path.basename(metadata_path)} "
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
        die(f"channel {channel_name!r} is not configured for {airline} "
            f"(configured: {', '.join(sorted(channels)) or 'none'}).")
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


STATUS_LINES = {
    "NO_LIMIT": "STATUS: no character limit on this channel",
    "FITS": "STATUS: fits",
    "TIGHT": "STATUS: tight — trim for margin before submitting",
    "OVERFLOW": "STATUS: OVERFLOW — do not present this draft; trim and rerun",
}


def print_report(r):
    print(f"Airline: {r['airline_name']} ({r['airline']})")
    print(f"Channel: {r['channel']}")
    if r["url"]:
        print(f"URL: {r['url']}")
    limit = r["char_limit"]
    if limit is None:
        print("Char limit: none recorded for this channel")
    else:
        verified = "verified" if r["limit_verified"] else "UNVERIFIED"
        source = r["limit_source"] or "no source recorded"
        print(f"Char limit: {limit} ({verified} — {source})")
    print(f"Counting method: {r['counting_method']}")
    print()
    print("Counts:")
    for name, value in r["counts"].items():
        print(f"  {name:<16} {value}")
    print(f"  {'worst case':<16} {max(r['counts'].values())}")

    if r["inflation_applied"] is not None:
        print()
        print(f"Counting method is not established for this form, so the worst count is "
              f"inflated by ×{r['inflation_applied']} → {r['effective_count']}.")
    if limit is not None:
        print()
        print(f"Judged at: {r['effective_count']} / {limit} (headroom {r['headroom']:+d})")
    print(STATUS_LINES[r["status"]])
    if limit is not None and not r["count_verified"]:
        print("         (count UNVERIFIED — the form's own counter is the final word)")

    if r["formatting_warnings"]:
        print()
        print("Formatting warnings:")
        for w in r["formatting_warnings"]:
            print(f"  - {w}")

    prefilled = r["prefilled_fields"]
    if isinstance(prefilled, list) and prefilled:
        print()
        print(f"Form already captures: {', '.join(prefilled)}")
        print("  Drop these from the letter body; keep the loyalty tier in the opener.")

    if r["channel_notes"]:
        print()
        print(f"Notes: {r['channel_notes']}")


def cmd_list_airlines(md, as_json):
    airlines = md.get("airlines", {})
    if as_json:
        print(json.dumps(airlines, indent=2, ensure_ascii=False))
        return 0
    if not airlines:
        print("No airlines in metadata.")
        return 0
    print(f"{'Code':<6} {'Airline':<24} Channels")
    print(f"{'-' * 6} {'-' * 24} {'-' * 40}")
    for code in sorted(airlines):
        meta = airlines[code]
        chans = []
        for name, ch in sorted(meta.get("channels", {}).items()):
            limit = ch.get("char_limit")
            chans.append(f"{name}({limit if limit is not None else 'no limit'})")
        print(f"{code:<6} {meta.get('name', code):<24} {', '.join(chans)}")
    print()
    print("Any airline not listed still works with an explicit --limit <N>.")
    return 0


def cmd_info(airline, airline_meta, as_json):
    if as_json:
        print(json.dumps({airline: airline_meta}, indent=2, ensure_ascii=False))
        return 0
    print(f"{airline_meta.get('name', airline)} ({airline})")
    for name, ch in sorted(airline_meta.get("channels", {}).items()):
        print()
        print(f"  {name}")
        limit = ch.get("char_limit")
        print(f"    char limit:  {limit if limit is not None else 'none'}"
              f"{'' if limit is None else (' (verified)' if ch.get('limit_verified') else ' (UNVERIFIED)')}")
        if ch.get("url"):
            print(f"    url:         {ch['url']}")
        if ch.get("address"):
            print("    address:     " + ch["address"].replace("\n", "\n                 "))
        if ch.get("counting_method"):
            print(f"    counting:    {ch['counting_method']}")
        prefilled = ch.get("prefilled_fields")
        if prefilled:
            shown = ", ".join(prefilled) if isinstance(prefilled, list) else prefilled
            print(f"    prefilled:   {shown}")
    if airline_meta.get("channel_notes"):
        print()
        print(f"  Notes: {airline_meta['channel_notes']}")
    return 0


def main():
    p = argparse.ArgumentParser(
        description="Measure a complaint letter against an airline's submission-form limits.")
    p.add_argument("--airline", help="IATA airline code, e.g. AA, WN")
    p.add_argument("--channel", default="web_form",
                   help="channel to measure against (default: web_form)")
    p.add_argument("--file", help="read the letter from this file")
    p.add_argument("--stdin", action="store_true", help="read the letter from stdin")
    p.add_argument("--limit", type=int,
                   help="character limit read off the live form; overrides the metadata")
    p.add_argument("--json", action="store_true", help="emit the report as JSON")
    p.add_argument("--info", action="store_true",
                   help="print the airline's channels and notes; no letter needed")
    p.add_argument("--list-airlines", action="store_true",
                   help="list airlines the metadata knows; no letter needed")
    p.add_argument("--metadata", default=DEFAULT_METADATA_FILE,
                   help="metadata file to read (default: the one shipped beside this script)")
    args = p.parse_args()

    md = load_metadata(args.metadata)

    if args.list_airlines:
        return cmd_list_airlines(md, args.json)

    if not args.airline:
        die("--airline is required (or use --list-airlines).")
    if args.limit is not None and args.limit <= 0:
        die(f"--limit must be a positive character count, got {args.limit}.")

    airline = args.airline.upper()

    if args.info:
        airline_meta = md.get("airlines", {}).get(airline)
        if airline_meta is None:
            die(f"airline {airline!r} is not in {os.path.basename(args.metadata)} "
                f"(known: {', '.join(sorted(md.get('airlines', {}))) or 'none'}).")
        return cmd_info(airline, airline_meta, args.json)

    text = read_letter(args)
    airline_meta, channel_meta = resolve_channel(md, airline, args.channel, args.limit,
                                                 args.metadata)
    report = build_report(text, airline, args.channel, airline_meta, channel_meta, args.limit)

    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print_report(report)

    return 1 if report["status"] == "OVERFLOW" else 0


if __name__ == "__main__":
    sys.exit(main())
