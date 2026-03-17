#!/usr/bin/env python3
"""
Track filed complaint letters for pattern detection across airlines and passengers.
Storage at ~/.claude/complaint-bank/ so any skill can access it.
Run `init` first to set up storage (default or custom location like Google Drive).

Usage:
  python3 complaints-bank.py init
  python3 complaints-bank.py file --airline CODE --flight FLNUM --flight-date YYYY-MM-DD --route ORIG-DEST --passenger NAME --category CAT --severity SEV --summary "..." --outcome "..."
  python3 complaints-bank.py check --airline CODE [--passenger NAME] [--route ROUTE]
  python3 complaints-bank.py resolve --id ID --resolution STATUS [--note TEXT]
  python3 complaints-bank.py list [--airline CODE] [--passenger NAME] [--category CAT]

Examples:
  python3 complaints-bank.py file --airline DL --flight DL1234 --flight-date 2026-01-15 --route BNA-JFK --passenger "Baruch Sadogursky" --category CANCELLATION --severity MAJOR --summary "Flight cancelled 2hrs before departure, no rebooking for 36hrs" --outcome "Full refund + 75K miles"
  python3 complaints-bank.py check --airline DL --passenger "Baruch Sadogursky"
  python3 complaints-bank.py resolve --id 1 --resolution RESOLVED --note "Received 50K miles + $200 voucher"
"""

import argparse
import os
import re
import sys
from datetime import datetime

BANK_DIR = os.path.join(os.path.expanduser("~"), ".claude", "complaint-bank")
COMPLAINTS_PATH = os.path.join(BANK_DIR, "complaints.md")

VALID_CATEGORIES = [
    "CANCELLATION", "DELAY", "DOWNGRADE", "BAGGAGE", "SERVICE",
    "DENIED_BOARDING", "TARMAC", "OTHER",
]

VALID_SEVERITIES = ["MINOR", "MODERATE", "MAJOR", "RIGHTS_VIOLATION"]

VALID_RESOLUTIONS = ["PENDING", "RESOLVED", "PARTIAL", "DENIED", "ESCALATED", "CLOSED"]

AIRLINE_NAMES = {
    "DL": "Delta Air Lines", "AA": "American Airlines", "UA": "United Airlines",
    "WN": "Southwest Airlines", "B6": "JetBlue", "NK": "Spirit Airlines",
    "F9": "Frontier Airlines", "AS": "Alaska Airlines",
}

EMPTY_BANK = """# Complaint Bank

Filed complaints for pattern tracking. Use `complaints-bank.py` for all updates.

## Filed Complaints

<!-- COMPLAINTS_START — do not edit this marker -->
<!-- COMPLAINTS_END — do not edit this marker -->
"""


def ensure_bank():
    if not os.path.exists(COMPLAINTS_PATH):
        real_dir = os.path.realpath(BANK_DIR)
        os.makedirs(real_dir, exist_ok=True)
        with open(COMPLAINTS_PATH, "w") as f:
            f.write(EMPTY_BANK)


def cmd_init(_args):
    if os.path.exists(BANK_DIR):
        real_path = os.path.realpath(BANK_DIR)
        is_symlink = os.path.islink(BANK_DIR)
        if is_symlink:
            print(f"Already initialized. Storage: {real_path} (symlinked from {BANK_DIR})")
        else:
            print(f"Already initialized. Storage: {real_path}")

        has_complaints = False
        if os.path.exists(COMPLAINTS_PATH):
            content = read_bank()
            has_complaints = bool(parse_complaints(content))

        if has_complaints:
            print("Bank has filed complaints. To change location, move the data manually.")
            return
        else:
            response = input("No complaints filed. Reinitialize with a different location? [y/N] ").strip().lower()
            if response != "y":
                return
            if is_symlink:
                os.unlink(BANK_DIR)
            else:
                import shutil
                shutil.rmtree(BANK_DIR)

    print()
    print("Where should the complaint bank be stored?")
    print()
    print(f"  1. Default: {BANK_DIR}")
    print("  2. Custom path (e.g. Google Drive, Dropbox, iCloud)")
    print()
    choice = input("Choice [1/2]: ").strip()

    if choice == "2":
        custom = input("Enter full path to complaint bank directory: ").strip()
        custom = os.path.expanduser(custom)
        if not custom:
            print("ERROR: No path provided.", file=sys.stderr)
            sys.exit(1)
        os.makedirs(custom, exist_ok=True)
        parent = os.path.dirname(BANK_DIR)
        os.makedirs(parent, exist_ok=True)
        os.symlink(custom, BANK_DIR)
        print(f"Created {custom}")
        print(f"Symlinked {BANK_DIR} -> {custom}")
    else:
        os.makedirs(BANK_DIR, exist_ok=True)
        print(f"Created {BANK_DIR}")

    ensure_bank()
    print(f"Initialized empty complaint bank at {os.path.realpath(COMPLAINTS_PATH)}")


def read_bank():
    ensure_bank()
    with open(COMPLAINTS_PATH, "r") as f:
        return f.read()


def write_bank(content):
    ensure_bank()
    with open(COMPLAINTS_PATH, "w") as f:
        f.write(content)


def parse_complaints(content):
    start_marker = "<!-- COMPLAINTS_START"
    end_marker = "<!-- COMPLAINTS_END"
    start_idx = content.find(start_marker)
    end_idx = content.find(end_marker)
    if start_idx == -1 or end_idx == -1:
        return []

    block = content[content.index("\n", start_idx) + 1:end_idx].strip()
    if not block:
        return []

    complaints = []
    current = {}
    for line in block.split("\n"):
        line = line.strip()
        if line.startswith("### #"):
            if current:
                complaints.append(current)
            match = re.match(r"### #(\d+)\s*[—–-]\s*\[([A-Z_]+)\]\s*(.*)", line)
            if match:
                current = {
                    "id": int(match.group(1)),
                    "category": match.group(2),
                    "title": match.group(3),
                }
        elif line.startswith("- **") and current:
            kv = re.match(r"- \*\*(.+?)\*\*:\s*(.*)", line)
            if kv:
                key = kv.group(1).lower().replace(" ", "_")
                current[key] = kv.group(2)

    if current:
        complaints.append(current)
    return complaints


def format_complaint(c):
    lines = [f"### #{c['id']} — [{c['category']}] {c.get('flight', '?')} {c.get('route', '?')} {c.get('flight_date', '?')}"]
    for key in ["date_filed", "airline", "flight", "flight_date", "route", "passenger",
                "severity", "summary", "outcome_requested", "resolution", "resolution_note"]:
        if key in c:
            label = key.replace("_", " ").title()
            lines.append(f"- **{label}**: {c[key]}")
    return "\n".join(lines)


def next_id(content):
    all_ids = [c["id"] for c in parse_complaints(content)]
    return max(all_ids, default=0) + 1


def insert_complaint(content, complaint_md):
    marker = "<!-- COMPLAINTS_END"
    idx = content.find(marker)
    if idx == -1:
        print("ERROR: Could not find end marker in bank file", file=sys.stderr)
        sys.exit(1)
    before = content[:idx].rstrip()
    after = content[idx:]
    return f"{before}\n\n{complaint_md}\n\n{after}"


def cmd_file(args):
    cat = args.category.upper()
    if cat not in VALID_CATEGORIES:
        print(f"ERROR: Invalid category '{cat}'. Valid: {', '.join(VALID_CATEGORIES)}", file=sys.stderr)
        sys.exit(1)
    sev = args.severity.upper()
    if sev not in VALID_SEVERITIES:
        print(f"ERROR: Invalid severity '{sev}'. Valid: {', '.join(VALID_SEVERITIES)}", file=sys.stderr)
        sys.exit(1)

    content = read_bank()
    cid = next_id(content)

    complaint = {
        "id": cid,
        "category": cat,
        "date_filed": datetime.now().strftime("%Y-%m-%d"),
        "airline": args.airline.upper(),
        "flight": args.flight,
        "flight_date": args.flight_date,
        "route": args.route.upper(),
        "passenger": args.passenger,
        "severity": sev,
        "summary": args.summary,
        "outcome_requested": args.outcome,
        "resolution": "PENDING",
    }

    complaint_md = format_complaint(complaint)
    content = insert_complaint(content, complaint_md)
    write_bank(content)

    airline_name = AIRLINE_NAMES.get(args.airline.upper(), args.airline.upper())
    print(f"Filed complaint #{cid}: [{cat}] {args.flight} {args.route} ({airline_name})")
    print(f"  Passenger: {args.passenger}")
    print(f"  Severity: {sev}")


def cmd_check(args):
    content = read_bank()
    complaints = parse_complaints(content)

    if not complaints:
        print("No complaints in the bank.")
        return

    # Filter by airline
    airline = args.airline.upper()
    matches = [c for c in complaints if c.get("airline", "").upper() == airline]

    if args.passenger:
        matches = [c for c in matches if args.passenger.lower() in c.get("passenger", "").lower()]

    if args.route:
        route = args.route.upper()
        matches = [c for c in matches if c.get("route", "").upper() == route]

    if not matches:
        filters = [f"airline={airline}"]
        if args.passenger:
            filters.append(f"passenger={args.passenger}")
        if args.route:
            filters.append(f"route={args.route}")
        print(f"No prior complaints matching {', '.join(filters)}.")
        return

    airline_name = AIRLINE_NAMES.get(airline, airline)
    pax = args.passenger or "all passengers"
    print(f"=== Complaint History: {airline_name} ({airline}) — {pax} ===\n")
    print(f"{len(matches)} prior complaint(s) found.\n")

    # Group by category
    by_cat = {}
    for c in matches:
        cat = c.get("category", "OTHER")
        by_cat.setdefault(cat, []).append(c)

    for cat in VALID_CATEGORIES:
        if cat not in by_cat:
            continue
        group = by_cat[cat]
        label = "occurrence" if len(group) == 1 else "occurrences"
        if len(group) >= 2:
            print(f"PATTERN: {cat} ({len(group)} {label})")
        else:
            print(f"{cat} ({len(group)} {label})")
        for c in group:
            res = c.get("resolution", "PENDING")
            res_note = f" ({c['resolution_note']})" if c.get("resolution_note") else ""
            sev = c.get("severity", "?")
            print(f"  #{c['id']} — {c.get('flight', '?')} {c.get('route', '?')} {c.get('flight_date', '?')} [{sev}] — {c.get('outcome_requested', '?')} -> {res}{res_note}")
        print()

    # Group by route
    by_route = {}
    for c in matches:
        r = c.get("route", "?")
        by_route.setdefault(r, []).append(c)

    route_patterns = {r: cs for r, cs in by_route.items() if len(cs) >= 2}
    if route_patterns:
        for r, cs in route_patterns.items():
            print(f"ROUTE PATTERN: {r} ({len(cs)} complaints)")
        print()

    # Resolution summary
    res_counts = {}
    for c in matches:
        r = c.get("resolution", "PENDING")
        res_counts[r] = res_counts.get(r, 0) + 1

    parts = [f"{status}: {count}" for status, count in sorted(res_counts.items())]
    print(" | ".join(parts))

    # Highlight actionable patterns
    denied = [c for c in matches if c.get("resolution") == "DENIED"]
    if denied:
        print(f"\n{len(denied)} prior complaint(s) DENIED — strengthens escalation language")

    # Check recency
    dates = []
    for c in matches:
        try:
            dates.append(datetime.strptime(c.get("flight_date", ""), "%Y-%m-%d"))
        except ValueError:
            pass
    if len(dates) >= 2:
        dates.sort()
        span = (dates[-1] - dates[0]).days
        if span <= 365:
            months = max(1, span // 30)
            print(f"{len(matches)} complaints in {months} month(s) — shows recurring pattern")


def cmd_resolve(args):
    content = read_bank()
    complaints = parse_complaints(content)

    target = None
    for c in complaints:
        if c["id"] == args.id:
            target = c
            break

    if not target:
        print(f"ERROR: Complaint #{args.id} not found.", file=sys.stderr)
        sys.exit(1)

    res = args.resolution.upper()
    if res not in VALID_RESOLUTIONS:
        print(f"ERROR: Invalid resolution '{res}'. Valid: {', '.join(VALID_RESOLUTIONS)}", file=sys.stderr)
        sys.exit(1)

    # Find and update the resolution line in the raw content
    lines = content.split("\n")
    in_target = False
    for i, line in enumerate(lines):
        if re.match(rf"### #{args.id}\s", line):
            in_target = True
        elif line.startswith("### #") and in_target:
            break
        elif in_target and line.strip().startswith("- **Resolution**:"):
            lines[i] = f"- **Resolution**: {res}"
            # Add or update resolution note
            if args.note:
                if i + 1 < len(lines) and lines[i + 1].strip().startswith("- **Resolution Note**:"):
                    lines[i + 1] = f"- **Resolution Note**: {args.note}"
                else:
                    lines.insert(i + 1, f"- **Resolution Note**: {args.note}")
            break

    write_bank("\n".join(lines))
    print(f"Updated complaint #{args.id}: resolution = {res}")
    if args.note:
        print(f"  Note: {args.note}")


def cmd_list(args):
    content = read_bank()
    complaints = parse_complaints(content)

    if args.airline:
        complaints = [c for c in complaints if c.get("airline", "").upper() == args.airline.upper()]
    if args.passenger:
        complaints = [c for c in complaints if args.passenger.lower() in c.get("passenger", "").lower()]
    if args.category:
        complaints = [c for c in complaints if c.get("category", "").upper() == args.category.upper()]

    if not complaints:
        print("No complaints found.")
        return

    print(f"{'#':<5} {'Date':<12} {'Airline':<8} {'Flight':<10} {'Route':<10} {'Category':<16} {'Severity':<12} {'Resolution':<12}")
    print(f"{'-'*5} {'-'*12} {'-'*8} {'-'*10} {'-'*10} {'-'*16} {'-'*12} {'-'*12}")
    for c in complaints:
        print(f"{c['id']:<5} {c.get('flight_date', '?'):<12} {c.get('airline', '?'):<8} {c.get('flight', '?'):<10} {c.get('route', '?'):<10} {c.get('category', '?'):<16} {c.get('severity', '?'):<12} {c.get('resolution', '?'):<12}")


def cmd_pending(_args):
    content = read_bank()
    complaints = parse_complaints(content)
    pending = [c for c in complaints if c.get("resolution", "PENDING") == "PENDING"]

    if not pending:
        print("No pending complaints.")
        return

    print(f"{len(pending)} complaint(s) awaiting resolution:\n")
    for c in pending:
        airline = c.get("airline", "?")
        airline_name = AIRLINE_NAMES.get(airline, airline)
        filed = c.get("date_filed", "?")
        print(f"  #{c['id']} — {airline_name} {c.get('flight', '?')} {c.get('route', '?')} {c.get('flight_date', '?')}")
        print(f"     Filed: {filed} | {c.get('category', '?')} [{c.get('severity', '?')}]")
        print(f"     Requested: {c.get('outcome_requested', '?')}")
        print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Track filed complaint letters for pattern detection",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("init", help="Set up complaint bank storage")

    fl = sub.add_parser("file", help="File a new complaint")
    fl.add_argument("--airline", required=True, help="Airline code (e.g. DL, AA, UA)")
    fl.add_argument("--flight", required=True, help="Flight number (e.g. DL1234)")
    fl.add_argument("--flight-date", required=True, help="Date of flight (YYYY-MM-DD)")
    fl.add_argument("--route", required=True, help="Route (e.g. BNA-JFK)")
    fl.add_argument("--passenger", required=True, help="Passenger name")
    fl.add_argument("--category", required=True, help=f"Category: {', '.join(VALID_CATEGORIES)}")
    fl.add_argument("--severity", required=True, help=f"Severity: {', '.join(VALID_SEVERITIES)}")
    fl.add_argument("--summary", required=True, help="1-2 sentence summary of what happened")
    fl.add_argument("--outcome", required=True, help="What was requested in the letter")

    chk = sub.add_parser("check", help="Check for complaint patterns")
    chk.add_argument("--airline", required=True, help="Airline code")
    chk.add_argument("--passenger", help="Filter by passenger name")
    chk.add_argument("--route", help="Filter by route")

    res = sub.add_parser("resolve", help="Update complaint resolution")
    res.add_argument("--id", type=int, required=True, help="Complaint ID")
    res.add_argument("--resolution", required=True, help=f"Resolution: {', '.join(VALID_RESOLUTIONS)}")
    res.add_argument("--note", help="Resolution details")

    sub.add_parser("pending", help="List complaints awaiting resolution")

    ls = sub.add_parser("list", help="List complaints")
    ls.add_argument("--airline", help="Filter by airline code")
    ls.add_argument("--passenger", help="Filter by passenger name")
    ls.add_argument("--category", help=f"Filter by category: {', '.join(VALID_CATEGORIES)}")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    {
        "init": cmd_init,
        "file": cmd_file,
        "check": cmd_check,
        "resolve": cmd_resolve,
        "pending": cmd_pending,
        "list": cmd_list,
    }[args.command](args)
