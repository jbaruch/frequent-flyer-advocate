#!/usr/bin/env python3
"""
Track filed complaint letters for pattern detection across airlines, hotels, and passengers.
Storage at ~/.claude/complaint-bank/ so any skill can access it.
Run `init` first to set up storage (default or custom location like Google Drive).

The bank holds two parallel stores in the same directory, selected with `--store` (before the
subcommand; default `airline` for back-compat):
  airline → complaint-bank/complaints.md         (--airline/--flight/--flight-date/--route)
  hotel   → complaint-bank/hotel-complaints.md    (--brand/--property/--reservation/--stay-dates/--loyalty-status)
Both files share the same markdown structure; only the field schema and header template differ.

Usage:
  python3 complaints-bank.py init [--default | --path DIR]   # set up new storage
  python3 complaints-bank.py link --path DIR                  # link an existing bank
  python3 complaints-bank.py [--store airline] file --airline CODE --flight FLNUM --flight-date YYYY-MM-DD --route ORIG-DEST --passenger NAME --category CAT --severity SEV --summary "..." --outcome "..."
  python3 complaints-bank.py --store hotel file --brand BRAND --property NAME --reservation CODE --stay-dates START/END --loyalty-status TIER --passenger NAME --category CAT --severity SEV --summary "..." --outcome "..."
  python3 complaints-bank.py [--store airline] check --airline CODE [--passenger NAME] [--route ROUTE]
  python3 complaints-bank.py --store hotel check --brand BRAND [--passenger NAME] [--property NAME]
  python3 complaints-bank.py [--store {airline,hotel}] resolve --id ID --resolution STATUS [--note TEXT]
  python3 complaints-bank.py [--store {airline,hotel}] list [filters]

Examples:
  python3 complaints-bank.py file --airline DL --flight DL1234 --flight-date 2026-01-15 --route BNA-JFK --passenger "Baruch Sadogursky" --category CANCELLATION --severity MAJOR --summary "Flight cancelled 2hrs before departure, no rebooking for 36hrs" --outcome "Full refund + 75K miles"
  python3 complaints-bank.py --store hotel file --brand Hilton --property "Hilton London Angel Islington" --reservation 3434402137 --stay-dates 2026-05-05/2026-05-08 --loyalty-status "Hilton Honors Gold" --passenger "Baruch Sadogursky" --category HABITABILITY --severity MAJOR --summary "No hot water for 2 of 3 nights, maintenance never resolved it" --outcome "Full refund of the stay + points compensation"
  python3 complaints-bank.py check --airline DL --passenger "Baruch Sadogursky"
  python3 complaints-bank.py --store hotel check --brand Hilton --passenger "Baruch Sadogursky"
  python3 complaints-bank.py --store hotel resolve --id 1 --resolution RESOLVED --note "2-night refund + 30K Honors points"
"""

import argparse
import contextlib
import json
import os
import re
import sys
from datetime import datetime

BANK_DIR = os.path.join(os.path.expanduser("~"), ".claude", "complaint-bank")
COMPLAINTS_PATH = os.path.join(BANK_DIR, "complaints.md")  # airline store = bank-existence marker
HOTEL_COMPLAINTS_PATH = os.path.join(BANK_DIR, "hotel-complaints.md")

# Category vocabularies are per-store. SERVICE/OTHER overlap between them is fine.
VALID_CATEGORIES = [
    "CANCELLATION", "DELAY", "DOWNGRADE", "BAGGAGE", "SERVICE",
    "DENIED_BOARDING", "TARMAC", "OTHER",
]

VALID_HOTEL_CATEGORIES = [
    "HABITABILITY", "SERVICE", "BILLING", "CLEANLINESS", "NOISE", "SAFETY", "OTHER",
]

VALID_SEVERITIES = ["MINOR", "MODERATE", "MAJOR", "RIGHTS_VIOLATION"]


JSON_EMITTED = False
JSON_MODE = False


def emit_json(payload):
    """Write one JSON object to stdout — the agent-facing output contract.

    Every command's --json mode goes through here so the shape stays uniform: a single
    object, never a bare array or a stream of lines. Diagnostics stay on stderr, per
    rules/file-hygiene.md I/O Conventions. Mirrors credits-tracker.py, so both scripts in
    this skill answer the same way.
    """
    global JSON_EMITTED
    JSON_EMITTED = True
    print(json.dumps(payload, indent=2, ensure_ascii=False))


@contextlib.contextmanager
def quiet_stdout(active):
    """Route human progress lines to stderr while a JSON command runs.

    Bootstrap helpers narrate what they did on stdout. In --json mode stdout belongs to the
    payload, and a progress line ahead of it makes the output unparseable.
    """
    if not active:
        yield
        return
    with contextlib.redirect_stdout(sys.stderr):
        yield


def complaint_payload(c):
    """One complaint as structured data: parser keys, minus internals."""
    return {k: v for k, v in c.items() if not k.startswith("_")}


def store_path(store):
    """Resolve the markdown file backing a store. Both live in the shared bank directory."""
    return HOTEL_COMPLAINTS_PATH if store == "hotel" else COMPLAINTS_PATH


def categories_for(store):
    return VALID_HOTEL_CATEGORIES if store == "hotel" else VALID_CATEGORIES

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

EMPTY_HOTEL_BANK = """# Hotel Complaint Bank

Filed hotel-loyalty complaints for pattern tracking. Use `complaints-bank.py --store hotel`
for all updates. Shares the same markers and structure as the airline bank.

## Filed Complaints

<!-- COMPLAINTS_START — do not edit this marker -->
<!-- COMPLAINTS_END — do not edit this marker -->
"""


def require_initialized():
    """Fail loudly if the bank hasn't been set up yet — never silently auto-create.

    On a machine where the complaint bank lives in cloud storage and just hasn't been
    linked yet, silently creating an empty default store would fork the shared data into
    two diverging copies. The skill's bootstrap must run `init` or `link` first.
    """
    # isdir() follows symlinks, so a symlink to a real directory passes; a dangling
    # symlink, a symlink to a non-directory, and a plain file all fall through to a
    # specific error instead of being mistaken for an initialized bank.
    if os.path.isdir(BANK_DIR):
        return
    if os.path.islink(BANK_DIR):
        target = os.readlink(BANK_DIR)
        print(
            f"ERROR: {BANK_DIR} is a symlink to '{target}', but that target is missing "
            f"or is not a directory.\n"
            f"Re-link to the real location:  complaints-bank.py link --path <existing-dir>",
            file=sys.stderr,
        )
    elif os.path.exists(BANK_DIR):
        print(
            f"ERROR: {BANK_DIR} exists but is not a directory. Remove it (or move it "
            f"aside) and re-run init/link.",
            file=sys.stderr,
        )
    else:
        print(
            f"ERROR: complaint bank not initialized at {BANK_DIR}.\n"
            f"  Already have a bank (e.g. in Google Drive/Dropbox/iCloud)? Link it:\n"
            f"      complaints-bank.py link --path <existing-dir>\n"
            f"  Start a fresh one:\n"
            f"      complaints-bank.py init --default       # store at ~/.claude/complaint-bank\n"
            f"      complaints-bank.py init --path <dir>    # store elsewhere, symlinked back",
            file=sys.stderr,
        )
    # The diagnostic above is for the human; a --json caller reads this instead. main()'s
    # SystemExit handler cannot build it, since only here is the reason known.
    if JSON_MODE:
        emit_json({"error": "bank_not_initialized", "store": BANK_DIR,
                   "detail": "run init or link first; diagnostic on stderr"})
    sys.exit(2)


def ensure_bank(store="airline"):
    """Create the store's markdown file inside the (already-initialized) bank if it's missing.

    Assumes require_initialized() has passed — BANK_DIR exists (possibly via symlink).
    Does NOT overwrite an existing file, so linking to a populated bank is safe. The hotel
    store is a second file in the SAME directory, so its lazy creation never forks the bank.
    """
    path = store_path(store)
    if not os.path.exists(path):
        real_dir = os.path.realpath(BANK_DIR)
        os.makedirs(real_dir, exist_ok=True)
        with open(path, "w") as f:
            f.write(EMPTY_HOTEL_BANK if store == "hotel" else EMPTY_BANK)


def _refuse_unusable_store_path():
    """Before creating a fresh bank, refuse if something unusable already sits at BANK_DIR.

    Callers check os.path.isdir() first, so reaching here means the path is not a usable
    bank. A dangling symlink usually means the real (cloud) bank is unmounted — refuse
    rather than orphan it. A plain file (or symlink to a non-directory) would otherwise make
    os.makedirs raise an opaque FileExistsError — refuse with an actionable message instead.
    """
    if os.path.islink(BANK_DIR) and not os.path.exists(BANK_DIR):
        target = os.readlink(BANK_DIR)
        print(
            f"ERROR: {BANK_DIR} is a symlink to '{target}', but that target is missing.\n"
            f"  The cloud folder may be unmounted — remount it, or re-link with:\n"
            f"      complaints-bank.py link --path <existing-dir>\n"
            f"  To deliberately start fresh, remove the symlink first: rm {BANK_DIR}",
            file=sys.stderr,
        )
        sys.exit(2)
    if os.path.exists(BANK_DIR):  # exists but not a directory (callers already checked isdir)
        print(
            f"ERROR: {BANK_DIR} exists but is not a directory. Remove it (or move it "
            f"aside) before creating a bank here.",
            file=sys.stderr,
        )
        sys.exit(2)


def _init_default():
    """Create a fresh empty bank at the default ~/.claude location."""
    if os.path.isdir(BANK_DIR):
        print(f"Already initialized. Storage: {os.path.realpath(BANK_DIR)}")
        return
    _refuse_unusable_store_path()
    os.makedirs(BANK_DIR, exist_ok=True)
    ensure_bank()
    print(f"Initialized empty complaint bank at {COMPLAINTS_PATH}")


def _init_custom(custom):
    """Create a fresh bank at a custom path and symlink BANK_DIR to it."""
    if not custom or not custom.strip():
        print(
            "ERROR: No path provided. Pass --path <dir> for the new bank's location.",
            file=sys.stderr,
        )
        sys.exit(1)
    # Resolve to an absolute path: a relative symlink target resolves against ~/.claude
    # (the symlink's own directory), not the user's cwd — almost never what they meant.
    custom = os.path.abspath(os.path.expanduser(custom))
    if (os.path.islink(custom) or os.path.exists(custom)) and not os.path.isdir(custom):
        # A plain file, a symlink to a non-directory, or a dangling symlink at the
        # target would all make os.makedirs(exist_ok=True) raise an opaque
        # FileExistsError. (exists() is False for a dangling symlink, so islink() is
        # checked too.) Refuse with an actionable message instead.
        print(
            f"ERROR: '{custom}' is not a usable directory (it's a plain file, or a "
            f"symlink to a missing/non-directory target). Pick a different --path, or "
            f"remove it first.",
            file=sys.stderr,
        )
        sys.exit(1)
    if os.path.exists(BANK_DIR):
        print(
            f"ERROR: {BANK_DIR} already exists (real path {os.path.realpath(BANK_DIR)}).",
            file=sys.stderr,
        )
        sys.exit(1)
    _refuse_unusable_store_path()
    dir_existed = os.path.isdir(custom)
    os.makedirs(custom, exist_ok=True)
    parent = os.path.dirname(BANK_DIR)
    os.makedirs(parent, exist_ok=True)
    os.symlink(custom, BANK_DIR)
    bank_existed = os.path.exists(COMPLAINTS_PATH)
    ensure_bank()
    print(f"{'Using existing directory' if dir_existed else 'Created'} {custom}")
    print(f"Symlinked {BANK_DIR} -> {custom}")
    if bank_existed:
        print(f"Found existing complaint bank at {os.path.realpath(COMPLAINTS_PATH)}")
    else:
        print(f"Initialized empty complaint bank at {os.path.realpath(COMPLAINTS_PATH)}")


def _link(target):
    """Symlink BANK_DIR to an existing complaint-bank directory (shared/cloud-synced)."""
    if not target or not target.strip():
        # Catch empty AND whitespace-only input: abspath('') / abspath('  ') would
        # otherwise resolve against the cwd and link the bank somewhere unintended.
        print(
            "ERROR: No path provided. Point --path at the existing complaint-bank folder.",
            file=sys.stderr,
        )
        sys.exit(1)
    target = os.path.abspath(os.path.expanduser(target))
    if not os.path.isdir(target):
        print(
            f"ERROR: '{target}' is not a directory. Point --path at the existing "
            f"complaint-bank folder.",
            file=sys.stderr,
        )
        sys.exit(1)
    # `link` attaches to an EXISTING bank; it must not bootstrap one. Silently
    # creating a store file here would turn a wrong/empty --path into a second,
    # diverging bank — the fork hazard this command exists to avoid. Use `init` for
    # a fresh bank. Either store file (airline or hotel) marks an existing bank.
    if not _bank_files_present(target):
        print(
            f"ERROR: '{target}' has no complaints.md or hotel-complaints.md — `link` "
            f"attaches to an existing bank, it does not create one.\n"
            f"  Point --path at the real complaint-bank folder, or create a fresh bank:\n"
            f"      complaints-bank.py init --path {target}",
            file=sys.stderr,
        )
        sys.exit(1)
    if os.path.exists(BANK_DIR):
        # Compare canonical paths so /tmp vs /private/tmp (macOS) reads as already-linked.
        if os.path.realpath(BANK_DIR) == os.path.realpath(target):
            print(f"Already linked: {BANK_DIR} -> {target}")
            return
        print(
            f"ERROR: {BANK_DIR} already exists (real path {os.path.realpath(BANK_DIR)}).\n"
            f"Move or remove it first if you really want to re-link.",
            file=sys.stderr,
        )
        sys.exit(1)
    if os.path.islink(BANK_DIR):  # dangling symlink — safe to replace
        os.unlink(BANK_DIR)
    parent = os.path.dirname(BANK_DIR)
    os.makedirs(parent, exist_ok=True)
    os.symlink(target, BANK_DIR)
    print(f"Linked {BANK_DIR} -> {target}")
    # At least one store file is guaranteed present (checked above), so this reports the real
    # linked bank — it never bootstraps an empty one. Count across both stores.
    filed = _count_all_complaints(target)
    print(f"   Found existing bank ({filed} filed complaint(s)).")


def cmd_status(args):
    """Report bank readiness so the skill's bootstrap doesn't reimplement the contract.

    Single source of truth for "is the bank usable?": prints one of `ready` / `missing` /
    `invalid: <reason>` and exits 0 (ready), 3 (missing), or 4 (invalid). Mirrors the
    isdir-based contract that require_initialized() enforces.
    """
    state, reason, code = _resolve_status()
    if getattr(args, "json", False):
        emit_json({"state": state, "store": os.path.realpath(BANK_DIR)
                   if state == "ready" else BANK_DIR, "reason": reason})
    elif state == "ready":
        # Exact, bare readiness token (machine-readable contract); the resolved path
        # goes to stderr so stdout stays a single stable token, like `missing`.
        print("ready")
        print(f"  store: {os.path.realpath(BANK_DIR)}", file=sys.stderr)
    elif state == "invalid":
        print(f"invalid: {reason}")
    else:
        print("missing")
    sys.exit(code)


def _resolve_status():
    """Readiness as data: (state, reason, exit_code). One contract, two renderings."""
    if os.path.isdir(BANK_DIR):
        return "ready", None, 0
    if os.path.islink(BANK_DIR):
        target = os.readlink(BANK_DIR)
        if not os.path.exists(BANK_DIR):
            return ("invalid",
                    f"dangling symlink -> {target} "
                    f"(cloud folder unmounted? re-link or remove it)", 4)
        return "invalid", f"symlink -> {target} is not a directory", 4
    if os.path.exists(BANK_DIR):
        return "invalid", f"{BANK_DIR} exists but is not a directory", 4
    return "missing", None, 3


def cmd_link(args):
    """Link to an existing complaint-bank directory (non-interactive)."""
    with quiet_stdout(getattr(args, "json", False)):
        _link(args.path)
    if getattr(args, "json", False):
        emit_json({"linked": BANK_DIR, "target": os.path.realpath(BANK_DIR),
                   "filed": _count_all_complaints(os.path.realpath(BANK_DIR))})


def cmd_init(args):
    """Set up storage. Non-interactive with --default/--path; otherwise interactive."""
    json_mode = getattr(args, "json", False)
    if getattr(args, "default", False):
        with quiet_stdout(json_mode):
            _init_default()
        if json_mode:
            emit_json({"initialized": os.path.realpath(BANK_DIR), "linked_from": BANK_DIR})
        return
    if getattr(args, "path", None) is not None:
        # Dispatch on presence, not truthiness: `init --path ""` must reach _init_custom's
        # self-error-handled diagnostic, not fall through to the interactive branch. argparse
        # leaves args.path as None when --path is absent, so None alone means "go interactive".
        with quiet_stdout(json_mode):
            _init_custom(os.path.expanduser(args.path))
        if json_mode:
            emit_json({"initialized": os.path.realpath(BANK_DIR), "linked_from": BANK_DIR})
        return

    if json_mode:
        # Interactive init prompts on stdin; a JSON caller has no way to answer.
        emit_json({"error": "interactive_required",
                   "detail": "pass --default or --path with --json"})
        sys.exit(1)

    # Only a real bank (a directory, or a symlink to one) counts as "already
    # initialized" and is eligible for reinit. An unusable path — dangling symlink,
    # symlink to a non-directory, or a plain file — is refused, not clobbered, so we
    # honor the same contract as init --default/--path (and never orphan cloud data).
    if os.path.islink(BANK_DIR) or os.path.exists(BANK_DIR):
        if not os.path.isdir(BANK_DIR):
            _refuse_unusable_store_path()
        real_path = os.path.realpath(BANK_DIR)
        is_symlink = os.path.islink(BANK_DIR)
        if is_symlink:
            print(f"Already initialized. Storage: {real_path} (symlinked from {BANK_DIR})")
        else:
            print(f"Already initialized. Storage: {real_path}")

        # Populated if EITHER store file has complaints — never just complaints.md, or a
        # hotel-only bank would read as empty and the reinit path below would wipe it.
        has_complaints = _count_all_complaints(BANK_DIR) > 0

        if has_complaints:
            print("Bank has filed complaints. To change location, move the data manually.")
            return
        response = input("No complaints filed. Reinitialize with a different location? [y/N] ").strip().lower()
        if response != "y":
            return
        if is_symlink:
            os.unlink(BANK_DIR)  # drop the link, leave the target data intact
        else:
            import shutil
            shutil.rmtree(BANK_DIR)

    print()
    print("Where should the complaint bank live?")
    print()
    print(f"  1. Default — new bank at {BANK_DIR}")
    print("  2. Link an existing bank you already have (Google Drive / Dropbox / iCloud)")
    print("  3. New bank at a custom path (symlinked back to ~/.claude)")
    print()
    choice = input("Choice [1/2/3]: ").strip()

    if choice == "2":
        existing = input("Path to your existing complaint-bank directory: ").strip()
        _link(os.path.expanduser(existing))
    elif choice == "3":
        custom = input("Path for the new bank: ").strip()
        _init_custom(os.path.expanduser(custom))
    else:
        _init_default()


def read_bank(store="airline"):
    require_initialized()
    ensure_bank(store)
    with open(store_path(store), "r") as f:
        return f.read()


def write_bank(content, store="airline"):
    require_initialized()
    ensure_bank(store)
    with open(store_path(store), "w") as f:
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


# Both store files are valid bank-existence markers — a bank may hold only hotel complaints,
# only airline complaints, or both. link/init must treat the bank as existing/populated if
# EITHER file is present/non-empty, never just complaints.md (else a hotel-only bank reads as
# missing and the interactive-init reinit path would wipe live hotel data).
_BANK_FILES = ("complaints.md", "hotel-complaints.md")


def _bank_files_present(directory):
    """Names of the store files that actually exist in a bank directory (may be empty)."""
    return [f for f in _BANK_FILES if os.path.isfile(os.path.join(directory, f))]


def _count_all_complaints(directory):
    """Total filed complaints across both store files in a bank directory."""
    total = 0
    for f in _bank_files_present(directory):
        with open(os.path.join(directory, f), "r") as fh:
            total += len(parse_complaints(fh.read()))
    return total


AIRLINE_FIELDS = ["date_filed", "airline", "flight", "flight_date", "route", "passenger",
                  "severity", "summary", "outcome_requested", "resolution", "resolution_note"]

# Hotel keeps an explicit Category bullet (airline carries category only in the header) to
# match the hand-maintained hotel-complaints.md schema.
HOTEL_FIELDS = ["date_filed", "brand", "property", "reservation", "stay_dates", "loyalty_status",
                "passenger", "category", "severity", "summary", "outcome_requested",
                "resolution", "resolution_note"]


def format_complaint(c, store="airline"):
    if store == "hotel":
        header = f"### #{c['id']} — [{c['category']}] {c.get('property', '?')} {c.get('stay_dates', '?')}"
        fields = HOTEL_FIELDS
    else:
        header = f"### #{c['id']} — [{c['category']}] {c.get('flight', '?')} {c.get('route', '?')} {c.get('flight_date', '?')}"
        fields = AIRLINE_FIELDS
    lines = [header]
    for key in fields:
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


def _require_store_args(args, store):
    """Enforce the per-store required `file` args (argparse can't, since --store is a parent
    option resolved alongside them). Exits 1 with an actionable message listing what's missing.
    """
    shared = ["passenger", "category", "severity", "summary", "outcome"]
    needed = (["brand", "property", "reservation", "stay_dates", "loyalty_status"] + shared
              if store == "hotel"
              else ["airline", "flight", "flight_date", "route"] + shared)
    missing = [n for n in needed if not getattr(args, n, None)]
    if missing:
        flags = ", ".join("--" + n.replace("_", "-") for n in missing)
        if getattr(args, "json", False):
            emit_json({"error": "missing_required_args", "store": store,
                       "missing": ["--" + n.replace("_", "-") for n in missing]})
        print(f"ERROR: `--store {store} file` requires: {flags}", file=sys.stderr)
        sys.exit(1)


def cmd_file(args):
    store = args.store
    _require_store_args(args, store)

    cat = args.category.upper()
    valid_cats = categories_for(store)
    if cat not in valid_cats:
        if getattr(args, "json", False):
            emit_json({"error": "invalid_category", "given": cat, "store": store,
                       "valid": valid_cats})
        print(f"ERROR: Invalid category '{cat}' for --store {store}. Valid: {', '.join(valid_cats)}", file=sys.stderr)
        sys.exit(1)
    sev = args.severity.upper()
    if sev not in VALID_SEVERITIES:
        if getattr(args, "json", False):
            emit_json({"error": "invalid_severity", "given": sev,
                       "valid": VALID_SEVERITIES})
        print(f"ERROR: Invalid severity '{sev}'. Valid: {', '.join(VALID_SEVERITIES)}", file=sys.stderr)
        sys.exit(1)

    content = read_bank(store)
    cid = next_id(content)

    complaint = {
        "id": cid,
        "category": cat,
        "date_filed": datetime.now().strftime("%Y-%m-%d"),
        "passenger": args.passenger,
        "severity": sev,
        "summary": args.summary,
        "outcome_requested": args.outcome,
        "resolution": "PENDING",
    }
    if store == "hotel":
        complaint.update({
            "brand": args.brand,
            "property": args.property,
            "reservation": args.reservation,
            "stay_dates": args.stay_dates,
            "loyalty_status": args.loyalty_status,
        })
    else:
        complaint.update({
            "airline": args.airline.upper(),
            "flight": args.flight,
            "flight_date": args.flight_date,
            "route": args.route.upper(),
        })

    complaint_md = format_complaint(complaint, store)
    content = insert_complaint(content, complaint_md)
    write_bank(content, store)

    if getattr(args, "json", False):
        emit_json({"filed": complaint_payload(complaint), "store": store})
        return

    if store == "hotel":
        print(f"Filed complaint #{cid}: [{cat}] {args.property} {args.stay_dates} ({args.brand})")
    else:
        airline_name = AIRLINE_NAMES.get(args.airline.upper(), args.airline.upper())
        print(f"Filed complaint #{cid}: [{cat}] {args.flight} {args.route} ({airline_name})")
    print(f"  Passenger: {args.passenger}")
    print(f"  Severity: {sev}")


def _complaint_line(c, store):
    """One-line summary of a complaint for `check`, per store."""
    sev = c.get("severity", "?")
    res = c.get("resolution", "PENDING")
    res_note = f" ({c['resolution_note']})" if c.get("resolution_note") else ""
    if store == "hotel":
        ident = f"{c.get('property', '?')} {c.get('stay_dates', '?')}"
    else:
        ident = f"{c.get('flight', '?')} {c.get('route', '?')} {c.get('flight_date', '?')}"
    return f"  #{c['id']} — {ident} [{sev}] — {c.get('outcome_requested', '?')} -> {res}{res_note}"


def _recency_date(c, store):
    """Parse the complaint's reference date for recency detection. Hotel uses the stay's start
    (the part before '/' in stay_dates); airline uses flight_date. Returns None if unparseable.
    """
    raw = (c.get("stay_dates", "").split("/")[0] if store == "hotel" else c.get("flight_date", ""))
    try:
        return datetime.strptime(raw.strip(), "%Y-%m-%d")
    except ValueError:
        return None


def cmd_check(args):
    store = args.store
    json_mode = getattr(args, "json", False)
    if store == "hotel":
        if not args.brand:
            if json_mode:
                emit_json({"error": "missing_required_args", "store": store,
                           "missing": ["--brand"]})
            print("ERROR: `--store hotel check` requires --brand.", file=sys.stderr)
            sys.exit(1)
        primary = args.brand
        primary_label = f"{args.brand}"
        secondary_field, secondary_flag = "property", "property"
        secondary_val = args.property
    else:
        if not args.airline:
            if json_mode:
                emit_json({"error": "missing_required_args", "store": store,
                           "missing": ["--airline"]})
            print("ERROR: `check` requires --airline.", file=sys.stderr)
            sys.exit(1)
        primary = args.airline.upper()
        primary_label = f"{AIRLINE_NAMES.get(primary, primary)} ({primary})"
        secondary_field, secondary_flag = "route", "route"
        secondary_val = args.route

    content = read_bank(store)
    complaints = parse_complaints(content)

    primary_field = "brand" if store == "hotel" else "airline"
    if not complaints:
        if json_mode:
            emit_json({"store": store, primary_field: primary, "count": 0, "matches": [],
                       "category_patterns": {}, "secondary_patterns": {},
                       "resolutions": {}, "denied_count": 0, "recurring": None})
            return
        print("No complaints in the bank.")
        return
    matches = [c for c in complaints if c.get(primary_field, "").upper() == primary.upper()]

    if args.passenger:
        matches = [c for c in matches if args.passenger.lower() in c.get("passenger", "").lower()]

    if secondary_val:
        matches = [c for c in matches if c.get(secondary_field, "").upper() == secondary_val.upper()]

    if not matches:
        if json_mode:
            emit_json({"store": store, primary_field: primary, "count": 0, "matches": [],
                       "category_patterns": {}, "secondary_patterns": {},
                       "resolutions": {}, "denied_count": 0, "recurring": None})
            return
        filters = [f"{primary_field}={primary}"]
        if args.passenger:
            filters.append(f"passenger={args.passenger}")
        if secondary_val:
            filters.append(f"{secondary_flag}={secondary_val}")
        print(f"No prior complaints matching {', '.join(filters)}.")
        return

    if json_mode:
        emit_json(_check_payload(matches, store, primary_field, primary,
                                 secondary_field, secondary_flag))
        return

    pax = args.passenger or "all passengers"
    print(f"=== Complaint History: {primary_label} — {pax} ===\n")
    print(f"{len(matches)} prior complaint(s) found.\n")

    # Group by category
    by_cat = {}
    for c in matches:
        cat = c.get("category", "OTHER")
        by_cat.setdefault(cat, []).append(c)

    for cat in categories_for(store):
        if cat not in by_cat:
            continue
        group = by_cat[cat]
        label = "occurrence" if len(group) == 1 else "occurrences"
        if len(group) >= 2:
            print(f"PATTERN: {cat} ({len(group)} {label})")
        else:
            print(f"{cat} ({len(group)} {label})")
        for c in group:
            print(_complaint_line(c, store))
        print()

    # Group by secondary dimension (airline: route; hotel: property)
    by_secondary = {}
    for c in matches:
        key = c.get(secondary_field, "?")
        by_secondary.setdefault(key, []).append(c)

    secondary_patterns = {k: cs for k, cs in by_secondary.items() if len(cs) >= 2}
    if secondary_patterns:
        for k, cs in secondary_patterns.items():
            print(f"{secondary_flag.upper()} PATTERN: {k} ({len(cs)} complaints)")
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
    dates = [d for d in (_recency_date(c, store) for c in matches) if d is not None]
    if len(dates) >= 2:
        dates.sort()
        span = (dates[-1] - dates[0]).days
        if span <= 365:
            months = max(1, span // 30)
            print(f"{len(matches)} complaints in {months} month(s) — shows recurring pattern")


def _check_payload(matches, store, primary_field, primary, secondary_field, secondary_flag):
    """The same groupings `check` prints, as data.

    A pattern is a group of 2+ — the threshold the prose rendering already uses, kept in one
    place so the JSON and the tables can never disagree about what counts as a pattern.
    """
    by_cat = {}
    for c in matches:
        by_cat.setdefault(c.get("category", "OTHER"), []).append(c)

    by_secondary = {}
    for c in matches:
        by_secondary.setdefault(c.get(secondary_field, "?"), []).append(c)

    resolutions = {}
    for c in matches:
        r = c.get("resolution", "PENDING")
        resolutions[r] = resolutions.get(r, 0) + 1

    recurring = None
    dates = [d for d in (_recency_date(c, store) for c in matches) if d is not None]
    if len(dates) >= 2:
        dates.sort()
        span = (dates[-1] - dates[0]).days
        if span <= 365:
            recurring = {"complaints": len(matches), "span_days": span,
                         "span_months": max(1, span // 30)}

    return {
        "store": store,
        primary_field: primary,
        "count": len(matches),
        "matches": [complaint_payload(c) for c in matches],
        "category_patterns": {k: len(v) for k, v in by_cat.items() if len(v) >= 2},
        "categories": {k: len(v) for k, v in by_cat.items()},
        "secondary_field": secondary_flag,
        "secondary_patterns": {k: len(v) for k, v in by_secondary.items() if len(v) >= 2},
        "resolutions": resolutions,
        "denied_count": sum(1 for c in matches if c.get("resolution") == "DENIED"),
        "recurring": recurring,
    }


def cmd_resolve(args):
    store = args.store
    content = read_bank(store)
    complaints = parse_complaints(content)

    target = None
    for c in complaints:
        if c["id"] == args.id:
            target = c
            break

    if not target:
        if getattr(args, "json", False):
            emit_json({"error": "not_found", "id": args.id, "store": store})
        print(f"ERROR: Complaint #{args.id} not found.", file=sys.stderr)
        sys.exit(1)

    res = args.resolution.upper()
    if res not in VALID_RESOLUTIONS:
        if getattr(args, "json", False):
            emit_json({"error": "invalid_resolution", "given": res,
                       "valid": VALID_RESOLUTIONS})
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

    write_bank("\n".join(lines), store)
    if getattr(args, "json", False):
        updated = dict(target)
        updated["resolution"] = res
        if args.note:
            updated["resolution_note"] = args.note
        emit_json({"updated": complaint_payload(updated), "store": store})
        return
    print(f"Updated complaint #{args.id}: resolution = {res}")
    if args.note:
        print(f"  Note: {args.note}")


def cmd_list(args):
    store = args.store
    content = read_bank(store)
    complaints = parse_complaints(content)

    if store == "hotel":
        if args.brand:
            complaints = [c for c in complaints if c.get("brand", "").upper() == args.brand.upper()]
        if args.property:
            complaints = [c for c in complaints if args.property.lower() in c.get("property", "").lower()]
    else:
        if args.airline:
            complaints = [c for c in complaints if c.get("airline", "").upper() == args.airline.upper()]
    if args.passenger:
        complaints = [c for c in complaints if args.passenger.lower() in c.get("passenger", "").lower()]
    if args.category:
        complaints = [c for c in complaints if c.get("category", "").upper() == args.category.upper()]

    if getattr(args, "json", False):
        emit_json({"store": store, "count": len(complaints),
                   "complaints": [complaint_payload(c) for c in complaints]})
        return

    if not complaints:
        print("No complaints found.")
        return

    if store == "hotel":
        print(f"{'#':<5} {'Stay':<24} {'Brand':<12} {'Property':<28} {'Category':<14} {'Severity':<12} {'Resolution':<12}")
        print(f"{'-'*5} {'-'*24} {'-'*12} {'-'*28} {'-'*14} {'-'*12} {'-'*12}")
        for c in complaints:
            print(f"{c['id']:<5} {c.get('stay_dates', '?'):<24} {c.get('brand', '?'):<12} {c.get('property', '?')[:28]:<28} {c.get('category', '?'):<14} {c.get('severity', '?'):<12} {c.get('resolution', '?'):<12}")
    else:
        print(f"{'#':<5} {'Date':<12} {'Airline':<8} {'Flight':<10} {'Route':<10} {'Category':<16} {'Severity':<12} {'Resolution':<12}")
        print(f"{'-'*5} {'-'*12} {'-'*8} {'-'*10} {'-'*10} {'-'*16} {'-'*12} {'-'*12}")
        for c in complaints:
            print(f"{c['id']:<5} {c.get('flight_date', '?'):<12} {c.get('airline', '?'):<8} {c.get('flight', '?'):<10} {c.get('route', '?'):<10} {c.get('category', '?'):<16} {c.get('severity', '?'):<12} {c.get('resolution', '?'):<12}")


def cmd_pending(args):
    store = args.store
    content = read_bank(store)
    complaints = parse_complaints(content)
    pending = [c for c in complaints if c.get("resolution", "PENDING") == "PENDING"]

    if getattr(args, "json", False):
        emit_json({"store": store, "count": len(pending),
                   "pending": [complaint_payload(c) for c in pending]})
        return

    if not pending:
        print("No pending complaints.")
        return

    print(f"{len(pending)} complaint(s) awaiting resolution:\n")
    for c in pending:
        filed = c.get("date_filed", "?")
        if store == "hotel":
            print(f"  #{c['id']} — {c.get('brand', '?')} {c.get('property', '?')} {c.get('stay_dates', '?')}")
        else:
            airline = c.get("airline", "?")
            airline_name = AIRLINE_NAMES.get(airline, airline)
            print(f"  #{c['id']} — {airline_name} {c.get('flight', '?')} {c.get('route', '?')} {c.get('flight_date', '?')}")
        print(f"     Filed: {filed} | {c.get('category', '?')} [{c.get('severity', '?')}]")
        print(f"     Requested: {c.get('outcome_requested', '?')}")
        print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Track filed complaint letters for pattern detection",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    # Parent option — must precede the subcommand: `complaints-bank.py --store hotel file ...`.
    # Defaults to airline so every existing call site is byte-unchanged.
    parser.add_argument("--store", choices=["airline", "hotel"], default="airline",
                        help="Which complaint store to act on (default: airline)")

    # Inherited by every subcommand so `<cmd> --json` works uniformly. Agent callers pass
    # it; the prose default is the interactive human path, unchanged.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true",
                        help="Emit a JSON object on stdout instead of prose")

    sub = parser.add_subparsers(dest="command")

    init = sub.add_parser("init", help="Set up complaint bank storage", parents=[common])
    init_mode = init.add_mutually_exclusive_group()
    init_mode.add_argument("--default", action="store_true", help="Non-interactive: create a fresh bank at ~/.claude/complaint-bank")
    init_mode.add_argument("--path", help="Non-interactive: create a fresh bank at this path, symlinked back to ~/.claude")

    lnk = sub.add_parser("link", help="Link ~/.claude/complaint-bank to an existing bank directory (e.g. cloud-synced)", parents=[common])
    lnk.add_argument("--path", required=True, help="Path to the existing complaint-bank directory")

    sub.add_parser("status", help="Report bank readiness: ready (0) / missing (3) / invalid (4)", parents=[common])

    # `file`: store-specific required args (--airline… vs --brand…) can't be argparse-required
    # because --store is resolved alongside them; cmd_file enforces them per store. The shared
    # args stay argparse-required since both stores need them.
    fl = sub.add_parser("file", help="File a new complaint", parents=[common])
    fl.add_argument("--airline", help="[airline] Airline code (e.g. DL, AA, UA)")
    fl.add_argument("--flight", help="[airline] Flight number (e.g. DL1234)")
    fl.add_argument("--flight-date", help="[airline] Date of flight (YYYY-MM-DD)")
    fl.add_argument("--route", help="[airline] Route (e.g. BNA-JFK)")
    fl.add_argument("--brand", help="[hotel] Hotel brand (e.g. Hilton, Marriott)")
    fl.add_argument("--property", help="[hotel] Property name (e.g. Hilton London Angel Islington)")
    fl.add_argument("--reservation", help="[hotel] Reservation/confirmation number")
    fl.add_argument("--stay-dates", help="[hotel] Stay dates (YYYY-MM-DD/YYYY-MM-DD)")
    fl.add_argument("--loyalty-status", help="[hotel] Loyalty tier (e.g. Hilton Honors Gold)")
    fl.add_argument("--passenger", required=True, help="Passenger name")
    fl.add_argument("--category", required=True,
                    help=f"Category — airline: {', '.join(VALID_CATEGORIES)}; hotel: {', '.join(VALID_HOTEL_CATEGORIES)}")
    fl.add_argument("--severity", required=True, help=f"Severity: {', '.join(VALID_SEVERITIES)}")
    fl.add_argument("--summary", required=True, help="1-2 sentence summary of what happened")
    fl.add_argument("--outcome", required=True, help="What was requested in the letter")

    chk = sub.add_parser("check", help="Check for complaint patterns", parents=[common])
    chk.add_argument("--airline", help="[airline] Airline code (required for --store airline)")
    chk.add_argument("--brand", help="[hotel] Hotel brand (required for --store hotel)")
    chk.add_argument("--passenger", help="Filter by passenger name")
    chk.add_argument("--route", help="[airline] Filter by route")
    chk.add_argument("--property", help="[hotel] Filter by property name")

    res = sub.add_parser("resolve", help="Update complaint resolution", parents=[common])
    res.add_argument("--id", type=int, required=True, help="Complaint ID")
    res.add_argument("--resolution", required=True, help=f"Resolution: {', '.join(VALID_RESOLUTIONS)}")
    res.add_argument("--note", help="Resolution details")

    sub.add_parser("pending", help="List complaints awaiting resolution", parents=[common])

    ls = sub.add_parser("list", help="List complaints", parents=[common])
    ls.add_argument("--airline", help="[airline] Filter by airline code")
    ls.add_argument("--brand", help="[hotel] Filter by hotel brand")
    ls.add_argument("--property", help="[hotel] Filter by property name (substring)")
    ls.add_argument("--passenger", help="Filter by passenger name")
    ls.add_argument("--category", help="Filter by category (store-appropriate vocab)")

    # Read before parsing: an argparse failure exits before args exist, and that exit still
    # has to honour the JSON contract. require_initialized() reads the module-level flag for
    # the same reason — it fires below the dispatch, where args are not in scope.
    JSON_MODE = "--json" in sys.argv
    globals()["JSON_MODE"] = JSON_MODE

    try:
        args = parser.parse_args()
        if not args.command:
            parser.print_help()
            sys.exit(1)

        {
            "init": cmd_init,
            "link": cmd_link,
            "status": cmd_status,
            "file": cmd_file,
            "check": cmd_check,
            "resolve": cmd_resolve,
            "pending": cmd_pending,
            "list": cmd_list,
        }[args.command](args)
    except SystemExit as exc:
        # Any exit that skipped emit_json still owes the caller an object: under --json an
        # empty stdout is unparseable, which reads as a crashed script rather than a
        # reported failure. The diagnostic is already on stderr; this only guarantees
        # stdout holds one object.
        code = exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
        if JSON_MODE and code and not JSON_EMITTED:
            emit_json({"error": "command_failed", "exit_code": code,
                       "detail": "diagnostic on stderr"})
        raise
    # outer-boundary-process-contract: the caller reads stdout as JSON, so an unexpected
    # exception surfacing as a traceback with empty stdout is indistinguishable from a
    # crash. This emits a structured failure and re-raises; letting it propagate bare
    # would break the stdout contract.
    except Exception:  # noqa: BLE001
        if JSON_MODE and not JSON_EMITTED:
            emit_json({"error": "unexpected_failure", "detail": "traceback on stderr"})
        raise
