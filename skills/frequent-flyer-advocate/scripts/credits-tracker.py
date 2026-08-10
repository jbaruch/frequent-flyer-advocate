#!/usr/bin/env python3
"""
Track flight and hotel credits, vouchers, and upgrade certificates for the whole family.
Inventory stored globally at ~/.claude/travel-credits/inventory.md so any skill can access it.
Run `init` first to set up storage (default or custom location like Google Drive).

Supports multiple passengers and issuers — the primary use is Baruch + Alice travel,
but credits for kids, on non-Delta airlines, or on hotel brands (Hilton, Marriott, …) are
tracked too so nothing expires forgotten. A credit is tagged with --airline (airline issuer)
and/or --brand (hotel/loyalty-program issuer); both filter and surface independently.

Every subcommand takes --json, which replaces the prose rendering with a single
JSON object on stdout (diagnostics stay on stderr). Agent callers pass it; prose
is the interactive human default.

Every expiry decision reads reference_date(), not the wall clock. Set
CREDITS_TRACKER_TODAY=YYYY-MM-DD to freeze it — a test seam, so fixtures can use
fixed past dates instead of future ones that rot. Unset is the production path; a
malformed value is fatal rather than ignored.

Usage:
  python3 credits-tracker.py init [--default | --path DIR] [--json]   # set up new storage
  python3 credits-tracker.py link --path DIR                  # link an existing inventory
  python3 credits-tracker.py list [--type TYPE] [--passenger NAME] [--airline CODE] [--brand NAME] [--verbose]
  python3 credits-tracker.py add --type TYPE --description DESC --value VALUE --passenger NAME [--expiry YYYY-MM-DD] [--airline CODE] [--brand NAME] [--restrictions TEXT] [--confirmation CODE]
  python3 credits-tracker.py use --id ID [--note TEXT]
  python3 credits-tracker.py expiring [--days N] [--passenger NAME]
  python3 credits-tracker.py check --scenario SCENARIO [--passengers NAME,NAME]
  python3 credits-tracker.py summary [--passenger NAME]

Examples:
  python3 credits-tracker.py add --type GUC --description "Diamond GUC 2026 #1" --value "1 certificate" --expiry 2027-01-31 --passenger "Baruch Sadogursky" --airline DL --restrictions "DL-operated international only, paid fare required"
  python3 credits-tracker.py add --type ECREDIT --description "Canceled BNA-JFK Dec 2025" --value 347.20 --expiry 2026-12-15 --passenger "Baruch Sadogursky" --airline DL --confirmation "ABC123"
  python3 credits-tracker.py add --type ECREDIT --description "Canceled BNA-ORD Nov 2025" --value 189.50 --expiry 2026-11-30 --passenger "Kid Sadogursky" --airline AA --confirmation "XYZ789"
  python3 credits-tracker.py add --type COMPANION --description "Delta Reserve companion cert 2026" --value "1 certificate" --expiry 2027-01-31 --passenger "Baruch Sadogursky" --airline DL --restrictions "Round-trip domestic or to/from Canada/Mexico, main cabin or above"
  python3 credits-tracker.py list --passenger baruch
  python3 credits-tracker.py expiring --days 90
  python3 credits-tracker.py add --type VOUCHER --description "Complimentary 2-night stay" --value "2 nights" --expiry 2027-03-31 --passenger "Baruch Sadogursky" --brand Hilton --restrictions "Hilton London Angel Islington only"
  python3 credits-tracker.py check --scenario "American Airlines BNA-ORD economy repo"
  python3 credits-tracker.py check --scenario "Hilton London, 3 nights" --passengers "Baruch,Alice"
  python3 credits-tracker.py check --scenario "Delta business JFK-CDG" --passengers "Baruch,Alice"
  python3 credits-tracker.py use --id 3 --note "Applied to BNA-YUL repo Mar 2026"
"""

import argparse
import contextlib
import json
import os
import re
import sys
from datetime import datetime, timedelta

CREDITS_DIR = os.path.join(os.path.expanduser("~"), ".claude", "travel-credits")
INVENTORY_PATH = os.path.join(CREDITS_DIR, "inventory.md")

# Test seam for "today". Every expiry decision in this script reads reference_date() rather
# than the wall clock, so a suite can freeze the reference and assert against fixed
# past dates — coding-policy: testing-standards Determinism wants exactly that, and
# without it a fixture has to pin a future date that quietly starts failing when the
# real clock passes it.
TODAY_ENV = "CREDITS_TRACKER_TODAY"


def reference_date():
    """The reference date, overridable through TODAY_ENV as YYYY-MM-DD.

    A malformed override is fatal rather than ignored. Falling back to the wall clock
    would let a suite that meant to freeze time run against the real one and pass for
    the wrong reason, which is the failure this seam exists to prevent.
    """
    raw = os.environ.get(TODAY_ENV)
    if not raw:
        return datetime.now().date()
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        print(f"ERROR: {TODAY_ENV}={raw!r} is not a YYYY-MM-DD date. Unset it or fix it; "
              f"it will not be ignored.", file=sys.stderr)
        sys.exit(2)

# Record shape version, per coding-policy: stateful-artifacts, which puts one on
# every record. A record carrying no version field is not consumed — see
# is_readable_version(). The `migrate` subcommand stamps it; no ordinary write
# does, because migration belongs to the owner skill alone (see write_inventory()
# and cmd_migrate()). Bump only alongside a migration in
# skills/using-travel-credits — see its state-schema.md.
#
# v2: miles and points deposits move out of Active into the Compensation History
#     section and take the MILES / POINTS types. They never had a held-then-applied
#     lifecycle, so they never belonged in inventory.
# v3: COMP is renamed COMPANION. The abbreviation reads as "compensation" and means
#     Companion Certificate, and nothing rejected the misreading — every COMP row in
#     the live store was a mistyped miles or points grant.
# v4: miles and points grants sitting in the ARCHIVE move to Compensation History,
#     the same correction v2 made for Active. v2 scoped its scan to Active, so an
#     archived grant was never classified by Value and fell through to v3's rename,
#     landing as COMPANION — the one outcome v3's own note says no COMP row deserved.
#     A grant is in the account from the moment it is made, so the used state those
#     rows carry records an event that cannot happen. strip_used_state() removes those
#     fields and migrate reports them as dropped_used_state, so what was believed at
#     the time is surfaced in the migration output rather than left on the record.
SCHEMA_VERSION = 4

# A value like "25000 miles", "30,000 Hilton Honors points", "8,000 SkyMiles",
# "25,000 American AAdvantage miles". Deliberately anchored on the trailing unit
# word rather than on free-text meaning: script-delegation's Regex Trap allows a
# fully-enumerable pattern, and the unit vocabulary here is exactly two words.
#
# Any number of program-name words may sit between the amount and the unit, and the
# unit may be fused into a program word ("SkyMiles"). An earlier form allowed at most
# one intervening word, which silently missed "30,000 Hilton Honors points" — a shape
# taken straight from the live store.
#
# The error is asymmetric, so the pattern stays conservative. A false negative leaves
# a deposit in Active, which is the visible status quo. A false positive would move a
# genuine credit out of the available set, which is a real loss of function. No
# non-deposit value shape in the store ends in "miles" or "points": "1 certificate",
# "2 nights", "347.20".
# Anchored at both ends: the whole field must BE an amount of miles or points, not
# merely contain one. Unanchored, "5000 miles voucher" and "1 certificate for 5000
# miles travel" both matched and would have been pulled out of available inventory.
DEPOSIT_VALUE_RE = re.compile(
    r"^\s*[\d,]+\s*(?:[A-Za-z]+\s+)*[A-Za-z]*(miles|points)\b[\s.,;]*$", re.IGNORECASE)

VALID_TYPES = ["GUC", "RUC", "COMPANION", "ECREDIT", "VOUCHER", "PARTNER", "AMEX", "OTHER",
               "MILES", "POINTS"]

TYPE_LABELS = {
    "GUC": "Global Upgrade Certificate",
    "RUC": "Regional Upgrade Certificate",
    "COMPANION": "Companion Certificate",
    "ECREDIT": "eCredit",
    "VOUCHER": "Voucher",
    "PARTNER": "Partner Credit",
    "AMEX": "Amex Travel Credit",
    "OTHER": "Other",
    "MILES": "Miles Deposit",
    "POINTS": "Points Deposit",
}

# Types with no held-then-applied lifecycle. An airline that grants 25,000 miles
# deposits them into the loyalty account at the moment of the grant — there is no
# "issued, awaiting use" phase, and once deposited they are fungible with the rest
# of the balance, which this file cannot shadow. They are a compensation EVENT:
# history, not inventory. Recorded in their own section, never counted as available,
# and with no `use` transition, because there is nothing to transition to.
DEPOSIT_TYPES = ("MILES", "POINTS")

# Common airline name → code mappings for scenario matching
AIRLINE_ALIASES = {
    "delta": "DL", "dl": "DL",
    "air france": "AF", "af": "AF",
    "klm": "KL", "kl": "KL",
    "korean air": "KE", "ke": "KE",
    "virgin atlantic": "VS", "vs": "VS",
    "american": "AA", "american airlines": "AA", "aa": "AA",
    "united": "UA", "united airlines": "UA", "ua": "UA",
    "southwest": "WN", "wn": "WN",
    "jetblue": "B6", "b6": "B6",
    "spirit": "NK", "nk": "NK",
    "frontier": "F9", "f9": "F9",
    "alaska": "AS", "alaska airlines": "AS", "as": "AS",
    "air canada": "AC", "ac": "AC",
    "westjet": "WS", "ws": "WS",
    "aeromexico": "AM", "am": "AM",
    "latam": "LA", "la": "LA",
    "sas": "SK", "sk": "SK",
    "el al": "LY", "ly": "LY",
}

# Common hotel/loyalty-program name → brand code mappings for scenario matching.
# Parallel to AIRLINE_ALIASES: the airline dimension is --airline (2-letter IATA codes);
# the hotel/program dimension is --brand (chain-level codes). Sub-brands collapse to their
# parent chain so a "Conrad" or "Waldorf Astoria" stay surfaces a HILTON-tagged credit.
#
# Every alias must be UNAMBIGUOUS in free-form scenario text — either a coined brand word
# that does not occur as ordinary English (Sheraton, Bonvoy, Andaz) or a multi-word phrase
# (Choice Hotels, Courtyard by Marriott). Bare common words (honors, choice, courtyard,
# renaissance, thompson, hampton, curio) are deliberately excluded: as standalone aliases
# they would match prose like "Delta honors the upgrade" or "we had no choice" and surface a
# hotel credit in an unrelated airline scenario. The set stays fully enumerable per
# jbaruch/coding-policy: script-delegation.
HOTEL_ALIASES = {
    # Hilton portfolio
    "hilton": "HILTON", "hilton honors": "HILTON", "conrad": "HILTON",
    "waldorf astoria": "HILTON", "doubletree": "HILTON", "embassy suites": "HILTON",
    "hampton inn": "HILTON", "hampton by hilton": "HILTON", "curio collection": "HILTON",
    # Marriott portfolio
    "marriott": "MARRIOTT", "bonvoy": "MARRIOTT", "ritz-carlton": "MARRIOTT",
    "ritz carlton": "MARRIOTT", "sheraton": "MARRIOTT", "westin": "MARRIOTT",
    "st. regis": "MARRIOTT", "st regis": "MARRIOTT", "le meridien": "MARRIOTT",
    "courtyard by marriott": "MARRIOTT", "renaissance hotel": "MARRIOTT",
    "renaissance hotels": "MARRIOTT",
    # IHG portfolio
    "ihg": "IHG", "intercontinental": "IHG", "holiday inn": "IHG",
    "crowne plaza": "IHG", "kimpton": "IHG", "hotel indigo": "IHG",
    # Hyatt portfolio
    "hyatt": "HYATT", "world of hyatt": "HYATT", "park hyatt": "HYATT",
    "andaz": "HYATT", "grand hyatt": "HYATT", "thompson hotel": "HYATT",
    "thompson hotels": "HYATT",
    # Accor portfolio
    "accor": "ACCOR", "sofitel": "ACCOR", "novotel": "ACCOR",
    "fairmont": "ACCOR", "pullman hotel": "ACCOR", "pullman hotels": "ACCOR",
    "raffles hotel": "ACCOR", "raffles hotels": "ACCOR",
    # Other chains
    "wyndham": "WYNDHAM", "ramada": "WYNDHAM", "days inn": "WYNDHAM",
    "choice hotels": "CHOICE", "choice privileges": "CHOICE",
    "comfort inn": "CHOICE", "quality inn": "CHOICE",
    "best western": "BESTWESTERN",
}


def days_left(credit, today):
    """Whole days until a credit expires; None when undated or unparseable."""
    if "expiry" not in credit:
        return None
    try:
        return (datetime.strptime(credit["expiry"], "%Y-%m-%d").date() - today).days
    except ValueError:
        return None


def credit_payload(credit, today):
    """One credit as structured data: stored fields plus derived expiry facts."""
    out = {k: v for k, v in credit.items() if not k.startswith("_")}
    out["brand_normalized"] = normalize_brand(credit.get("brand", "")) or None
    out["days_left"] = days_left(credit, today)
    out["expired"] = out["days_left"] is not None and out["days_left"] < 0
    return out


JSON_EMITTED = False


def emit_json(payload):
    """Write one JSON object to stdout — the agent-facing output contract.

    Every command's --json mode goes through here so the shape stays uniform:
    a single object, never a bare array or a stream of lines. Diagnostics stay
    on stderr, per rules/file-hygiene.md I/O Conventions.
    """
    global JSON_EMITTED
    JSON_EMITTED = True
    print(json.dumps(payload, indent=2, ensure_ascii=False))


def normalize_brand(name):
    """Normalize a hotel/program brand to its chain code.

    Maps known aliases (e.g. "Conrad", "Hilton Honors") to the parent chain code (HILTON);
    falls back to the uppercased input so an unknown brand still filters/matches consistently.
    """
    if not name or not name.strip():
        return ""
    key = name.strip().lower()
    if key in HOTEL_ALIASES:
        return HOTEL_ALIASES[key]
    return name.strip().upper()


def _issuer_label(credit):
    """Render a credit's issuer dimensions for display: airline, brand, or both.

    Falls back to "Airline: —" when neither is set, so airline-only callers read unchanged.
    """
    parts = []
    airline = credit.get("airline", "")
    brand = credit.get("brand", "")
    if airline:
        parts.append(f"Airline: {airline}")
    if brand:
        parts.append(f"Brand: {normalize_brand(brand)}")
    return " | ".join(parts) if parts else "Airline: —"


def is_transferable(credit):
    """A credit with no passenger is transferable (gift cards, etc.)."""
    return not credit.get("passenger")


def passenger_matches(credit, filter_name):
    """Match credits by passenger name. Transferable credits (no passenger) always match."""
    if not filter_name:
        return True
    if is_transferable(credit):
        return True
    return filter_name.lower() in credit.get("passenger", "").lower()


def airlines_in_scenario(scenario):
    """Extract airline codes mentioned in a scenario string."""
    scenario_lower = scenario.lower()
    codes = set()
    # Check aliases using word boundary regex (longest first to avoid partial matches)
    for alias in sorted(AIRLINE_ALIASES, key=len, reverse=True):
        if re.search(r'\b' + re.escape(alias) + r'\b', scenario_lower):
            codes.add(AIRLINE_ALIASES[alias])
    # Also grab any 2-letter uppercase codes directly from original
    for word in scenario.split():
        if len(word) == 2 and word.isupper() and word.isalpha():
            codes.add(word)
    return codes


def hotels_in_scenario(scenario):
    """Extract hotel/program brand codes mentioned in a scenario string.

    Parallel to airlines_in_scenario, but brand-only: there is no 2-letter-code fallback
    because hotel brand codes are words (HILTON, MARRIOTT), not IATA pairs.
    """
    scenario_lower = scenario.lower()
    brands = set()
    # Longest alias first so "hilton honors" wins over "hilton" / "honors" on overlap.
    for alias in sorted(HOTEL_ALIASES, key=len, reverse=True):
        if re.search(r'\b' + re.escape(alias) + r'\b', scenario_lower):
            brands.add(HOTEL_ALIASES[alias])
    return brands


COMPENSATION_HEADING = "## Compensation History (Deposited)"

# Section name -> (start marker, end marker). One mapping so a section is added by
# adding a row, not by threading another branch through every reader and writer.
SECTION_MARKERS = {
    "active": ("<!-- CREDITS_START", "<!-- CREDITS_END"),
    "archive": ("<!-- ARCHIVE_START", "<!-- ARCHIVE_END"),
    "compensation": ("<!-- COMPENSATION_START", "<!-- COMPENSATION_END"),
}


def ensure_compensation_section(content):
    """Append the compensation section to a store written before it existed.

    Stores created by an earlier version carry only Active and Archive. Appending on
    demand keeps every other byte untouched — the alternative, reformatting the store
    to the current template, would drop anything the template does not know about.
    """
    if SECTION_MARKERS["compensation"][0] in content:
        return content
    return (content.rstrip("\n")
            + f"\n\n{COMPENSATION_HEADING}\n\n"
            + "<!-- COMPENSATION_START — do not edit this marker -->\n"
            + "<!-- COMPENSATION_END — do not edit this marker -->\n")


def section_markers(section):
    """Markers bounding a section. Unknown names fail loudly rather than defaulting.

    The old two-section form fell through to the archive for anything that was not
    "active", so a typo silently read or wrote the wrong section.
    """
    try:
        return SECTION_MARKERS[section]
    except KeyError:
        raise ValueError(
            f"unknown section {section!r} — expected one of {', '.join(SECTION_MARKERS)}")

EMPTY_INVENTORY = f"""# Flight Credits, Vouchers & Upgrade Certificates Inventory

Track all active credits here. Use `credits-tracker.py` for all updates — do not hand-edit.

## Active Credits

<!-- CREDITS_START — do not edit this marker -->
<!-- CREDITS_END — do not edit this marker -->

## Used/Expired Credits (Archive)

<!-- ARCHIVE_START — do not edit this marker -->
<!-- ARCHIVE_END — do not edit this marker -->

{COMPENSATION_HEADING}

<!-- COMPENSATION_START — do not edit this marker -->
<!-- COMPENSATION_END — do not edit this marker -->
"""


def require_initialized():
    """Fail loudly if the store hasn't been set up yet — never silently auto-create.

    On a machine where the inventory lives in cloud storage (Google Drive/Dropbox/iCloud)
    and just hasn't been linked yet, silently creating an empty default store would fork
    the shared data into two diverging copies. The skill's bootstrap must run `init` or
    `link` first.
    """
    # isdir() follows symlinks, so a symlink to a real directory passes; a dangling
    # symlink, a symlink to a non-directory, and a plain file all fall through to a
    # specific error instead of being mistaken for an initialized store.
    if os.path.isdir(CREDITS_DIR):
        return
    if os.path.islink(CREDITS_DIR):
        target = os.readlink(CREDITS_DIR)
        print(
            f"ERROR: {CREDITS_DIR} is a symlink to '{target}', but that target is missing "
            f"or is not a directory.\n"
            f"Re-link to the real location:  credits-tracker.py link --path <existing-dir>",
            file=sys.stderr,
        )
    elif os.path.exists(CREDITS_DIR):
        print(
            f"ERROR: {CREDITS_DIR} exists but is not a directory. Remove it (or move it "
            f"aside) and re-run init/link.",
            file=sys.stderr,
        )
    else:
        print(
            f"ERROR: credits store not initialized at {CREDITS_DIR}.\n"
            f"  Already have an inventory (e.g. in Google Drive/Dropbox/iCloud)? Link it:\n"
            f"      credits-tracker.py link --path <existing-dir>\n"
            f"  Start a fresh one:\n"
            f"      credits-tracker.py init --default       # store at ~/.claude/travel-credits\n"
            f"      credits-tracker.py init --path <dir>    # store elsewhere, symlinked back",
            file=sys.stderr,
        )
    if "--json" in sys.argv:
        emit_json({"error": "store_not_initialized", "store": CREDITS_DIR,
                   "remedy": "run init --default, init --path DIR, or link --path DIR"})
    sys.exit(2)


def ensure_inventory():
    """Create inventory.md inside the (already-initialized) store if it's missing.

    Assumes require_initialized() has passed — CREDITS_DIR exists (possibly via symlink).
    Does NOT overwrite an existing inventory, so linking to a populated store is safe.
    """
    if not os.path.exists(INVENTORY_PATH):
        real_dir = os.path.realpath(CREDITS_DIR)
        os.makedirs(real_dir, exist_ok=True)
        with open(INVENTORY_PATH, "w") as f:
            f.write(EMPTY_INVENTORY)


def _refuse_unusable_store_path():
    """Before creating a fresh store, refuse if something unusable already sits at CREDITS_DIR.

    Callers check os.path.isdir() first, so reaching here means the path is not a usable
    store. A dangling symlink usually means the real (cloud) store is unmounted — refuse
    rather than orphan it. A plain file (or symlink to a non-directory) would otherwise make
    os.makedirs raise an opaque FileExistsError — refuse with an actionable message instead.
    """
    if os.path.islink(CREDITS_DIR) and not os.path.exists(CREDITS_DIR):
        target = os.readlink(CREDITS_DIR)
        print(
            f"ERROR: {CREDITS_DIR} is a symlink to '{target}', but that target is missing.\n"
            f"  The cloud folder may be unmounted — remount it, or re-link with:\n"
            f"      credits-tracker.py link --path <existing-dir>\n"
            f"  To deliberately start fresh, remove the symlink first: rm {CREDITS_DIR}",
            file=sys.stderr,
        )
        sys.exit(2)
    if os.path.exists(CREDITS_DIR):  # exists but not a directory (callers already checked isdir)
        print(
            f"ERROR: {CREDITS_DIR} exists but is not a directory. Remove it (or move it "
            f"aside) before creating a store here.",
            file=sys.stderr,
        )
        sys.exit(2)


def _init_default():
    """Create a fresh empty store at the default ~/.claude location."""
    if os.path.isdir(CREDITS_DIR):
        print(f"Already initialized. Storage: {os.path.realpath(CREDITS_DIR)}")
        return
    _refuse_unusable_store_path()
    os.makedirs(CREDITS_DIR, exist_ok=True)
    ensure_inventory()
    print(f"✅ Initialized empty inventory at {INVENTORY_PATH}")


def _init_custom(custom):
    """Create a fresh store at a custom path and symlink CREDITS_DIR to it."""
    if not custom or not custom.strip():
        print(
            "ERROR: No path provided. Pass --path <dir> for the new store's location.",
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
    if os.path.exists(CREDITS_DIR):
        print(
            f"ERROR: {CREDITS_DIR} already exists (real path {os.path.realpath(CREDITS_DIR)}).",
            file=sys.stderr,
        )
        sys.exit(1)
    _refuse_unusable_store_path()
    dir_existed = os.path.isdir(custom)
    os.makedirs(custom, exist_ok=True)
    parent = os.path.dirname(CREDITS_DIR)
    os.makedirs(parent, exist_ok=True)
    os.symlink(custom, CREDITS_DIR)
    inventory_existed = os.path.exists(INVENTORY_PATH)
    ensure_inventory()
    print(f"✅ {'Using existing directory' if dir_existed else 'Created'} {custom}")
    print(f"✅ Symlinked {CREDITS_DIR} → {custom}")
    if inventory_existed:
        print(f"   Found existing inventory at {os.path.realpath(INVENTORY_PATH)}")
    else:
        print(f"✅ Initialized empty inventory at {os.path.realpath(INVENTORY_PATH)}")


def _link(target):
    """Symlink CREDITS_DIR to an existing inventory directory (shared/cloud-synced)."""
    if not target or not target.strip():
        # Catch empty AND whitespace-only input: abspath('') / abspath('  ') would
        # otherwise resolve against the cwd and link the store somewhere unintended.
        print(
            "ERROR: No path provided. Point --path at the existing travel-credits folder.",
            file=sys.stderr,
        )
        sys.exit(1)
    target = os.path.abspath(os.path.expanduser(target))
    if not os.path.isdir(target):
        print(
            f"ERROR: '{target}' is not a directory. Point --path at the existing "
            f"travel-credits folder.",
            file=sys.stderr,
        )
        sys.exit(1)
    # `link` attaches to an EXISTING inventory; it must not bootstrap one. Silently
    # creating inventory.md here would turn a wrong/empty --path into a second,
    # diverging store — the fork hazard this command exists to avoid. Use `init` for
    # a fresh store.
    if not os.path.isfile(os.path.join(target, "inventory.md")):
        print(
            f"ERROR: '{target}' has no inventory.md — `link` attaches to an existing "
            f"inventory, it does not create one.\n"
            f"  Point --path at the real travel-credits folder, or create a fresh store:\n"
            f"      credits-tracker.py init --path {target}",
            file=sys.stderr,
        )
        sys.exit(1)
    if os.path.exists(CREDITS_DIR):
        # Compare canonical paths so /tmp vs /private/tmp (macOS) reads as already-linked.
        if os.path.realpath(CREDITS_DIR) == os.path.realpath(target):
            print(f"Already linked: {CREDITS_DIR} → {target}")
            return
        print(
            f"ERROR: {CREDITS_DIR} already exists (real path {os.path.realpath(CREDITS_DIR)}).\n"
            f"Move or remove it first if you really want to re-link.",
            file=sys.stderr,
        )
        sys.exit(1)
    if os.path.islink(CREDITS_DIR):  # dangling symlink — safe to replace
        os.unlink(CREDITS_DIR)
    parent = os.path.dirname(CREDITS_DIR)
    os.makedirs(parent, exist_ok=True)
    os.symlink(target, CREDITS_DIR)
    print(f"✅ Linked {CREDITS_DIR} → {target}")
    # inventory.md is guaranteed present (checked above), so this reports the real
    # linked store — it never bootstraps an empty one.
    with open(INVENTORY_PATH, "r") as f:
        active = len(parse_credits(f.read(), "active"))
    print(f"   Found existing inventory ({active} active credit(s)).")


def store_status():
    """Resolve store readiness. Returns (payload, exit_code).

    Single source of truth for "is the store usable?". `state` is one of
    ready / missing / invalid, mirroring the isdir-based contract that
    require_initialized() enforces.
    """
    if os.path.isdir(CREDITS_DIR):
        return {"state": "ready", "store": os.path.realpath(CREDITS_DIR),
                "reason": None}, 0
    if os.path.islink(CREDITS_DIR):
        target = os.readlink(CREDITS_DIR)
        if not os.path.exists(CREDITS_DIR):
            reason = (f"dangling symlink → {target} "
                      f"(cloud folder unmounted? re-link or remove it)")
        else:
            reason = f"symlink → {target} is not a directory"
        return {"state": "invalid", "store": None, "reason": reason}, 4
    if os.path.exists(CREDITS_DIR):
        return {"state": "invalid", "store": None,
                "reason": f"{CREDITS_DIR} exists but is not a directory"}, 4
    return {"state": "missing", "store": None, "reason": None}, 3


def cmd_status(args):
    """Report store readiness so the skill's bootstrap doesn't reimplement the contract.

    Exits 0 (ready), 3 (missing), or 4 (invalid) in both output modes.
    """
    payload, code = store_status()
    if args.json:
        emit_json(payload)
        sys.exit(code)
    if payload["state"] == "ready":
        # Exact, bare readiness token (machine-readable contract); the resolved path
        # goes to stderr so stdout stays a single stable token, like `missing`.
        print("ready")
        print(f"  store: {payload['store']}", file=sys.stderr)
        sys.exit(0)
    if payload["state"] == "invalid":
        print(f"invalid: {payload['reason']}")
        sys.exit(4)
    print("missing")
    sys.exit(3)


def cmd_link(args):
    """Link to an existing inventory directory (non-interactive)."""
    with quiet_stdout(args.json):
        _link(args.path)
    if args.json:
        emit_bootstrap_result()


@contextlib.contextmanager
def quiet_stdout(active):
    """Route human progress lines to stderr while a JSON command runs.

    Bootstrap helpers narrate what they did on stdout. In --json mode stdout
    belongs to the payload, and a progress line ahead of it makes the output
    unparseable — so the narration becomes a diagnostic, per
    rules/file-hygiene.md I/O Conventions.
    """
    if not active:
        yield
        return
    with contextlib.redirect_stdout(sys.stderr):
        yield


def emit_bootstrap_result():
    """Report the store's post-bootstrap state, so a caller confirms rather than assumes."""
    payload, _code = store_status()
    emit_json(payload)


def cmd_init(args):
    """Set up storage. Non-interactive with --default/--path; otherwise interactive."""
    if getattr(args, "default", False):
        with quiet_stdout(args.json):
            _init_default()
        if args.json:
            emit_bootstrap_result()
        return
    if getattr(args, "path", None) is not None:
        # Dispatch on presence, not truthiness: `init --path ""` must reach _init_custom's
        # self-error-handled diagnostic, not fall through to the interactive branch. argparse
        # leaves args.path as None when --path is absent, so None alone means "go interactive".
        with quiet_stdout(args.json):
            _init_custom(os.path.expanduser(args.path))
        if args.json:
            emit_bootstrap_result()
        return

    if args.json:
        # The remaining path prompts for input. An agent must choose the store
        # location explicitly rather than answer prompts on the user's behalf.
        print("ERROR: interactive init cannot run in --json mode; "
              "pass --default or --path DIR", file=sys.stderr)
        emit_json({"error": "interactive_required",
                   "remedy": "re-run with --default or --path DIR"})
        sys.exit(2)

    # Only a real store (a directory, or a symlink to one) counts as "already
    # initialized" and is eligible for reinit. An unusable path — dangling symlink,
    # symlink to a non-directory, or a plain file — is refused, not clobbered, so we
    # honor the same contract as init --default/--path (and never orphan cloud data).
    if os.path.islink(CREDITS_DIR) or os.path.exists(CREDITS_DIR):
        if not os.path.isdir(CREDITS_DIR):
            _refuse_unusable_store_path()
        real_path = os.path.realpath(CREDITS_DIR)
        is_symlink = os.path.islink(CREDITS_DIR)
        if is_symlink:
            print(f"Already initialized. Storage: {real_path} (symlinked from {CREDITS_DIR})")
        else:
            print(f"Already initialized. Storage: {real_path}")

        has_credits = False
        if os.path.exists(INVENTORY_PATH):
            with open(INVENTORY_PATH, "r") as f:
                has_credits = bool(parse_credits(f.read(), "active"))

        if has_credits:
            print("Inventory has active credits. To change location, move the data manually.")
            return
        response = input("No active credits. Reinitialize with a different location? [y/N] ").strip().lower()
        if response != "y":
            return
        if is_symlink:
            os.unlink(CREDITS_DIR)  # drop the link, leave the target data intact
        else:
            import shutil
            shutil.rmtree(CREDITS_DIR)

    print()
    print("Where should the credits inventory live?")
    print()
    print(f"  1. Default — new store at {CREDITS_DIR}")
    print("  2. Link an existing inventory you already have (Google Drive / Dropbox / iCloud)")
    print("  3. New store at a custom path (symlinked back to ~/.claude)")
    print()
    choice = input("Choice [1/2/3]: ").strip()

    if choice == "2":
        existing = input("Path to your existing travel-credits directory: ").strip()
        _link(os.path.expanduser(existing))
    elif choice == "3":
        custom = input("Path for the new store: ").strip()
        _init_custom(os.path.expanduser(custom))
    else:
        _init_default()


def read_inventory():
    require_initialized()
    ensure_inventory()
    with open(INVENTORY_PATH, "r") as f:
        return f.read()


VERSION_LINE_PREFIX = "- **Schema version**:"


def upgrade_record_body(body_lines, _from_version):
    """Transform one record's field lines from from_version to from_version + 1.

    Receives the record's field lines with the version line removed, and returns
    the replacement set. The caller uses the return value, so a future non-identity
    upgrade takes effect rather than bumping the version over an untransformed body.

    Identity today: SCHEMA_VERSION is 1 and no shipped record predates it, so
    there is no v0 shape to reshape. The step exists so a future bump adds a
    branch here instead of inventing the migration machinery at that point.
    """
    return body_lines


def _indent_of(line):
    return line[:len(line) - len(line.lstrip())]


def migrate_record(heading, body):
    """Migrate one record's field lines. Returns (new_body, stats).

    Works on the whole record rather than on the line after the heading. The
    parser accepts `- **Schema version**:` anywhere in a record and keeps the LAST
    occurrence, so deciding "unversioned" from the following line alone spliced a
    second version field into a record that already had one further down.
    """
    stats = {"stamped": 0, "upgraded": 0, "already_current": 0,
             "skipped_newer": 0, "unreadable": 0}
    idxs = [k for k, ln in enumerate(body)
            if ln.strip().startswith(VERSION_LINE_PREFIX)]

    if not idxs:
        stats["stamped"] = 1
        return [f"{_indent_of(heading)}{VERSION_LINE_PREFIX} {SCHEMA_VERSION}"] + body, stats

    # parse_credits() overwrites on each field line, so the last occurrence wins.
    try:
        version = int(body[idxs[-1]].strip()[len(VERSION_LINE_PREFIX):].strip())
    except ValueError:
        stats["unreadable"] = 1
        return body, stats

    if version > SCHEMA_VERSION:
        stats["skipped_newer"] = 1
        return body, stats

    keep = idxs[0]
    if version == SCHEMA_VERSION:
        stats["already_current"] = 1
        if len(idxs) == 1:
            return body, stats  # verbatim — a spacing difference is not a migration
        # Duplicate version fields make the record's version order-dependent.
        # Collapse to one canonical line at the first position.
        collapsed = [f"{_indent_of(body[keep])}{VERSION_LINE_PREFIX} {SCHEMA_VERSION}"]
        collapsed += [ln for k, ln in enumerate(body) if k not in set(idxs)]
        return collapsed, stats

    stats["upgraded"] = 1
    fields = [ln for k, ln in enumerate(body) if k not in set(idxs)]
    while version < SCHEMA_VERSION:
        fields = upgrade_record_body(fields, version)
        version += 1
    return [f"{_indent_of(body[keep])}{VERSION_LINE_PREFIX} {SCHEMA_VERSION}"] + fields, stats


def stamp_schema_version(content):
    """Bring every record up to SCHEMA_VERSION, per stateful-artifacts Migration Policy.

    Absent version: written before versioning, reads as 1 and is stamped.
    Older explicit version: upgraded through upgrade_record_body() and restamped.
    Newer: left untouched — parse_credits() already refuses to consume those, and
    an owner that cannot read a record must not rewrite it either.

    A text-level edit rather than a parse/reformat round-trip: reformatting the
    whole store would drop any field the current formatter does not know and
    rewrite untouched records, so a migration would risk more than it fixes.

    Reached only from cmd_migrate(). Migration is the owner skill's operation and
    no other write path may perform it — see write_inventory().

    Returns (migrated_text, stats).
    """
    stats = {"stamped": 0, "upgraded": 0, "already_current": 0,
             "skipped_newer": 0, "unreadable": 0}
    lines = content.split("\n")
    out = []
    i, n = 0, len(lines)
    while i < n:
        # A heading is recognized exactly as parse_credits() recognizes it — that
        # strips first, so anchoring on column zero made the two disagree and an
        # indented record went unmigrated while still being parsed.
        if not lines[i].strip().startswith("### #"):
            out.append(lines[i])
            i += 1
            continue
        heading = lines[i]
        j = i + 1
        while j < n and not lines[j].strip().startswith("### #"):
            j += 1
        new_body, record_stats = migrate_record(heading, lines[i + 1:j])
        for key, value in record_stats.items():
            stats[key] += value
        out.append(heading)
        out.extend(new_body)
        i = j
    return "\n".join(out), stats


def split_records(block):
    """Split a section body into (preamble_lines, [(heading, body_lines), ...])."""
    lines = block.split("\n")
    first = next((i for i, ln in enumerate(lines) if ln.strip().startswith("### #")), None)
    if first is None:
        return lines, []
    preamble, records = lines[:first], []
    i = first
    while i < len(lines):
        j = i + 1
        while j < len(lines) and not lines[j].strip().startswith("### #"):
            j += 1
        records.append((lines[i], lines[i + 1:j]))
        i = j
    return preamble, records


def replace_section_block(content, section, new_block):
    """Swap a section's body between its markers, leaving the rest byte-identical."""
    start_marker, end_marker = section_markers(section)
    start_idx = content.find(start_marker)
    end_idx = content.find(end_marker)
    if start_idx == -1 or end_idx == -1:
        raise ValueError(f"section {section!r} markers not found")
    block_start = content.index("\n", start_idx) + 1
    return content[:block_start] + new_block + content[end_idx:]


def record_version(body_lines):
    """The record's stamped version, or None when absent or unparseable.

    Reads the last occurrence, matching parse_credits(), which overwrites per field
    line and so ends up holding that one.
    """
    found = None
    for line in body_lines:
        stripped = line.strip()
        if stripped.startswith(VERSION_LINE_PREFIX):
            found = stripped[len(VERSION_LINE_PREFIX):].strip()
    if found is None:
        return None
    try:
        return int(found)
    except ValueError:
        return None


def deposit_unit(body_lines):
    """MILES / POINTS if this record's Value names one, else None.

    Reads the Value field the way parse_credits() does. Nothing else is consulted —
    a description mentioning "miles" is prose, and guessing from it is the judgment
    call script-delegation keeps out of a script.
    """
    for line in body_lines:
        kv = re.match(r"- \*\*(.+?)\*\*:\s*(.*)", line.strip())
        if kv and kv.group(1).lower() == "value":
            match = DEPOSIT_VALUE_RE.search(kv.group(2))
            if match:
                return match.group(1).upper()
            return None
    return None


# A compensation record has no used state (state-schema.md Record Shape), so an
# archived grant sheds these on the way out. The values are surfaced on the
# migration's own output rather than discarded quietly — the marking is a record
# of a redemption that could not have happened, and leaving it on the row would
# assert that miles sitting in the account had been spent.
USED_STATE_FIELDS = ("Used date", "Used note")


def strip_used_state(body_lines):
    """Return (kept_lines, {field: value}) for the used-state fields removed."""
    kept, dropped = [], {}
    for line in body_lines:
        m = re.match(r"\s*-\s*\*\*([\w ]+)\*\*:\s*(.*)", line)
        if m and m.group(1) in USED_STATE_FIELDS:
            dropped[m.group(1)] = m.group(2).strip()
            continue
        kept.append(line)
    return kept, dropped


def relocate_deposits(content, section="active"):
    """Move miles/points grants out of `section` into Compensation History.

    v1 -> v2 for Active; v3 -> v4 for the archive. Archived rows shed their used
    state on the way, reported back rather than dropped silently.

    They never had a held-then-applied lifecycle — the balance is in the loyalty
    account from the moment of the grant — so Active counted them as available
    forever and `use` was the only exit, asserting an event that never happened.

    The record's block is moved verbatim apart from its type token, so every field
    survives, including ones the current formatter does not know. Returns
    (content, moved) where moved lists {id, from_type, to_type}.
    """
    start_marker, end_marker = section_markers(section)
    if content.find(start_marker) == -1 or content.find(end_marker) == -1:
        return content, []

    block_start = content.index("\n", content.find(start_marker)) + 1
    active_block = content[block_start:content.find(end_marker)]
    preamble, records = split_records(active_block)

    kept, moving, moved = [], [], []
    for heading, body in records:
        match = re.match(r"(\s*### #(\d+)\s*[—–-]\s*)\[([A-Z]+)\](.*)", heading)
        # Only records this reader successfully brought to SCHEMA_VERSION. Stamping
        # leaves a newer or unparseable record alone, and relocating one here would
        # rewrite a record the reader cannot read — Migration Policy keeps those as
        # untouched no-usable-prior-state, and a move is a rewrite.
        if match is None or record_version(body) != SCHEMA_VERSION:
            kept.append((heading, body))
            continue
        # Classified by Value, whatever type the record currently carries. Keying on
        # COMP alone would have stranded every deposit logged under another type —
        # and the skill's only worked example was `--type VOUCHER`, so those exist.
        unit = deposit_unit(body)
        if unit is None or match.group(3) in DEPOSIT_TYPES:
            kept.append((heading, body))
            continue
        entry = {"id": int(match.group(2)),
                 "from_type": match.group(3), "to_type": unit}
        if section == "archive":
            body, dropped = strip_used_state(body)
            if dropped:
                entry["dropped_used_state"] = dropped
        moved.append(entry)
        moving.append((f"{match.group(1)}[{unit}]{match.group(4)}", body))

    if not moving:
        return content, []

    def render(preamble_lines, recs):
        out = list(preamble_lines)
        for heading, body in recs:
            out.append(heading)
            out.extend(body)
        return "\n".join(out)

    content = ensure_compensation_section(content)
    content = replace_section_block(content, section, render(preamble, kept))

    comp_start, comp_end = section_markers("compensation")
    comp_block_start = content.index("\n", content.find(comp_start)) + 1
    comp_block = content[comp_block_start:content.find(comp_end)]
    comp_preamble, comp_records = split_records(comp_block)
    content = replace_section_block(
        content, "compensation", render(comp_preamble, comp_records + moving))
    return content, moved


# v2 -> v3 type renames: old token -> current token. A rename is a heading edit, which
# upgrade_record_body() cannot express — it sees only field lines.
RENAMED_TYPES = {"COMP": "COMPANION"}


def rename_legacy_types(content):
    """v2 -> v3: rewrite retired type tokens in place, across every section.

    Only records this reader brought to SCHEMA_VERSION, for the same reason relocation
    is limited that way: a retype is a rewrite, and a record the reader cannot read is
    not one it may rewrite.

    Returns (content, renamed) where renamed lists {id, from_type, to_type}.
    """
    renamed = []
    for section in SECTION_MARKERS:
        start_marker, end_marker = section_markers(section)
        if content.find(start_marker) == -1 or content.find(end_marker) == -1:
            continue
        block_start = content.index("\n", content.find(start_marker)) + 1
        block = content[block_start:content.find(end_marker)]
        preamble, records = split_records(block)

        out, touched = list(preamble), False
        for heading, body in records:
            match = re.match(r"(\s*### #(\d+)\s*[—–-]\s*)\[([A-Z]+)\](.*)", heading)
            new_type = RENAMED_TYPES.get(match.group(3)) if match else None
            if match is None or new_type is None or record_version(body) != SCHEMA_VERSION:
                out.append(heading)
                out.extend(body)
                continue
            renamed.append({"id": int(match.group(2)),
                            "from_type": match.group(3), "to_type": new_type})
            out.append(f"{match.group(1)}[{new_type}]{match.group(4)}")
            out.extend(body)
            touched = True

        if touched:
            content = replace_section_block(content, section, "\n".join(out))
    return content, renamed


def count_record_headings(content):
    """Every `### #` record heading inside every section block.

    Counted the way parse_credits() scans — same markers, same leading-whitespace
    tolerance — so it is the total that view is a subset of. Driven off
    SECTION_MARKERS so a new section is counted without editing this.
    """
    total = 0
    for start_marker, end_marker in SECTION_MARKERS.values():
        start_idx = content.find(start_marker)
        end_idx = content.find(end_marker)
        if start_idx == -1 or end_idx == -1:
            continue
        block = content[content.index("\n", start_idx) + 1:end_idx]
        total += sum(1 for ln in block.split("\n") if ln.strip().startswith("### #"))
    return total


def write_inventory(content):
    """Persist the store verbatim.

    Deliberately does NOT migrate. Every skill that logs compensation reaches
    this script directly, so a migration here would run under a non-owner
    writer — which stateful-artifacts Migration Policy reserves to the owner
    skill. Records this writer did not touch keep whatever version they carry;
    the next `migrate` run by skills/using-travel-credits upgrades them.

    A record this call is itself writing is stamped by format_credit(), which
    is the writer emitting its own record in the current shape, not a migration
    of somebody else's.
    """
    require_initialized()
    ensure_inventory()
    with open(INVENTORY_PATH, "w") as f:
        f.write(content)


def is_readable_version(credit):
    """Whether this script may consume a parsed record.

    Only SCHEMA_VERSION exactly, per coding-policy: stateful-artifacts Migration
    Policy. Every caller of this script other than the owner skill is a non-owner
    reader, and the policy has a reader treat an off-version record as read-only
    "no usable prior state" in both directions:

    - Newer: written by an updated owner. A lagging reader must not read it under
      the wrong shape, nor rewrite it back down.
    - Older: the owner has not upgraded it yet. A non-owner must not migrate, so
      it declines the record and leaves it for the owner's `migrate` run.
    - Absent: stateful-artifacts Required Attributes puts a schema_version on
      every record, so a record without one has no auditable shape and a reader
      cannot know what it is holding. Declined like any other off-version record.
      `migrate` stamps it and it reads normally afterwards — the owner router runs
      that ahead of every read, so a pre-versioning store heals on first use.
    """
    raw = credit.get("schema_version")
    if raw is None:
        print(f"WARNING: credit #{credit.get('id')} carries no schema version — skipping it. "
              f"Run `migrate` from skills/using-travel-credits to stamp the store.",
              file=sys.stderr)
        return False
    try:
        version = int(raw)
    except ValueError:
        print(f"WARNING: credit #{credit.get('id')} has an unreadable schema version "
              f"{raw!r} — skipping it", file=sys.stderr)
        return False
    if version > SCHEMA_VERSION:
        print(f"WARNING: credit #{credit.get('id')} is schema version {version}, newer than "
              f"this script's {SCHEMA_VERSION} — skipping it. Update the plugin to read it.",
              file=sys.stderr)
        return False
    if version < SCHEMA_VERSION:
        print(f"WARNING: credit #{credit.get('id')} is schema version {version}, older than "
              f"this script's {SCHEMA_VERSION} — skipping it. Run `migrate` from "
              f"skills/using-travel-credits to upgrade the store.", file=sys.stderr)
        return False
    return True


def parse_credits(content, section="active"):
    """Parse credit entries from the inventory file.

    Records off SCHEMA_VERSION in either direction are omitted — see
    is_readable_version(). Callers that must account for every record
    regardless of version (next_id) work from the raw content instead.
    """
    start_marker, end_marker = section_markers(section)

    start_idx = content.find(start_marker)
    end_idx = content.find(end_marker)
    if start_idx == -1 or end_idx == -1:
        return []

    # Get content between markers
    block = content[content.index("\n", start_idx) + 1:end_idx].strip()
    if not block:
        return []

    credits = []
    current = {}
    for line in block.split("\n"):
        line = line.strip()
        if line.startswith("### #"):
            if current:
                credits.append(current)
            match = re.match(r"### #(\d+)\s*[—–-]\s*\[([A-Z]+)\]\s*(.*)", line)
            if match:
                current = {
                    "id": int(match.group(1)),
                    "type": match.group(2),
                    "description": match.group(3),
                }
        elif line.startswith("- **") and current:
            kv = re.match(r"- \*\*(.+?)\*\*:\s*(.*)", line)
            if kv:
                key = kv.group(1).lower().replace(" ", "_")
                current[key] = kv.group(2)

    if current:
        credits.append(current)
    return [c for c in credits if is_readable_version(c)]


def format_credit(c):
    """Format a credit entry as markdown."""
    lines = [f"### #{c['id']} — [{c['type']}] {c['description']}"]
    # Always the current version: parse_credits() only yields records at or below
    # it, and the owner upgrades what it rewrites.
    lines.append(f"- **Schema version**: {SCHEMA_VERSION}")
    if "value" in c:
        lines.append(f"- **Value**: {c['value']}")
    if "expiry" in c:
        lines.append(f"- **Expiry**: {c['expiry']}")
    if "passenger" in c:
        lines.append(f"- **Passenger**: {c['passenger']}")
    if "airline" in c:
        lines.append(f"- **Airline**: {c['airline']}")
    if "brand" in c:
        lines.append(f"- **Brand**: {c['brand']}")
    if "confirmation" in c:
        lines.append(f"- **Confirmation**: {c['confirmation']}")
    if "restrictions" in c:
        lines.append(f"- **Restrictions**: {c['restrictions']}")
    if "added" in c:
        lines.append(f"- **Added**: {c['added']}")
    if "used_date" in c:
        lines.append(f"- **Used date**: {c['used_date']}")
    if "used_note" in c:
        lines.append(f"- **Used note**: {c['used_note']}")
    return "\n".join(lines)


def next_id(content):
    """Get next available ID, counting every record in the file.

    Scans headings in the raw content rather than going through parse_credits:
    that view omits records newer than SCHEMA_VERSION, and an id allocated over
    one of those would collide with a record this script cannot see.
    """
    # Leading whitespace tolerated, matching parse_credits() — a heading anchored
    # only at column zero would miss an indented record and reissue its id.
    all_ids = [int(n) for n in re.findall(r"^[ \t]*### #(\d+)", content, re.MULTILINE)]
    return max(all_ids, default=0) + 1


def insert_credit(content, credit_md, section="active"):
    """Insert a formatted credit entry into the inventory."""
    marker = section_markers(section)[1]

    idx = content.find(marker)
    if idx == -1:
        print(f"ERROR: Could not find {marker} in inventory file", file=sys.stderr)
        sys.exit(1)

    # Insert before the end marker with proper spacing
    before = content[:idx].rstrip()
    after = content[idx:]
    return f"{before}\n\n{credit_md}\n\n{after}"


def remove_credit(content, credit_id, section="active"):
    """Remove a credit entry from a section, returning (new_content, removed_credit)."""
    credits = parse_credits(content, section)
    target = None
    for c in credits:
        if c["id"] == credit_id:
            target = c
            break

    if not target:
        return content, None

    # Find and remove the block for this credit
    start_marker, end_marker = section_markers(section)

    start_idx = content.find(start_marker)
    end_idx = content.find(end_marker)
    block_start = content.index("\n", start_idx) + 1
    block = content[block_start:end_idx]

    # Remove the specific credit entry
    pattern = rf"### #{credit_id}\s*[—–-].*?(?=\n### #|\n<!-- |$)"
    new_block = re.sub(pattern, "", block, flags=re.DOTALL).strip()
    new_content = content[:block_start] + ("\n" + new_block + "\n" if new_block else "\n") + content[end_idx:]

    return new_content, target


def cmd_list(args):
    content = read_inventory()
    credits = parse_credits(content, "active")

    if args.type:
        credits = [c for c in credits if c["type"] == args.type.upper()]
    if args.passenger:
        credits = [c for c in credits if passenger_matches(c, args.passenger)]
    if args.airline:
        credits = [c for c in credits if c.get("airline", "").upper() == args.airline.upper()]
    if args.brand:
        target = normalize_brand(args.brand)
        credits = [c for c in credits if normalize_brand(c.get("brand", "")) == target]

    if not credits:
        filters = []
        if args.type:
            filters.append(f"type={args.type.upper()}")
        if args.passenger:
            filters.append(f"passenger={args.passenger}")
        if args.airline:
            filters.append(f"airline={args.airline}")
        if args.brand:
            filters.append(f"brand={normalize_brand(args.brand)}")
        filter_msg = f" matching {', '.join(filters)}" if filters else ""
        if args.json:
            emit_json({"credits": [], "count": 0, "filters": filters})
            return
        print(f"No active credits{filter_msg}.")
        return

    today = reference_date()

    if args.json:
        emit_json({"credits": [credit_payload(c, today) for c in credits],
                   "count": len(credits),
                   "filters": [f for f in (
                       f"type={args.type.upper()}" if args.type else None,
                       f"passenger={args.passenger}" if args.passenger else None,
                       f"airline={args.airline}" if args.airline else None,
                       f"brand={normalize_brand(args.brand)}" if args.brand else None,
                   ) if f]})
        return

    if args.verbose:
        for c in credits:
            print(format_credit(c))
            # Add expiry warning
            if "expiry" in c:
                try:
                    exp = datetime.strptime(c["expiry"], "%Y-%m-%d").date()
                    days_left = (exp - today).days
                    if days_left < 0:
                        print(f"  ⚠️  EXPIRED {-days_left} days ago!")
                    elif days_left <= 30:
                        print(f"  ⚠️  Expires in {days_left} days!")
                    elif days_left <= 90:
                        print(f"  ⏰ Expires in {days_left} days")
                except ValueError:
                    pass
            print()
    else:
        print(f"{'#':<5} {'Type':<10} {'Passenger':<20} {'Airline':<8} {'Brand':<12} {'Description':<30} {'Value':<15} {'Expiry':<12} {'Status':<10}")
        print(f"{'-'*5} {'-'*10} {'-'*20} {'-'*8} {'-'*12} {'-'*30} {'-'*15} {'-'*12} {'-'*10}")
        for c in credits:
            status = ""
            if "expiry" in c:
                try:
                    exp = datetime.strptime(c["expiry"], "%Y-%m-%d").date()
                    days_left = (exp - today).days
                    if days_left < 0:
                        status = "⚠️ EXPIRED"
                    elif days_left <= 30:
                        status = f"⚠️ {days_left}d left"
                    elif days_left <= 90:
                        status = f"⏰ {days_left}d"
                    else:
                        status = f"✅ {days_left}d"
                except ValueError:
                    status = "?"
            desc = c.get("description", "")[:30]
            val = c.get("value", "")[:15]
            exp_str = c.get("expiry", "—")
            pax = c.get("passenger", "—")[:20]
            airline = c.get("airline", "—")[:8]
            # Show the normalized chain code (HILTON, BESTWESTERN) so the column is
            # unambiguous and fits — every code in HOTEL_ALIASES is ≤ 11 chars.
            brand = normalize_brand(c.get("brand", "")) or "—"
            print(f"{c['id']:<5} {c['type']:<10} {pax:<20} {airline:<8} {brand:<12} {desc:<30} {val:<15} {exp_str:<12} {status:<10}")


def cmd_add(args):
    content = read_inventory()
    reject_multiline({flag: getattr(args, flag, None) for flag in
                      ("description", "value", "expiry", "passenger", "airline",
                       "brand", "confirmation", "restrictions")}, args.json)
    ctype = args.type.upper()
    if ctype in RENAMED_TYPES:
        # A retired token gets its own message. Falling through to the generic list
        # would tell a caller that COMP is invalid without saying that the thing it
        # names still exists under another name — and the plain-English misreading of
        # COMP is what put five mistyped rows in the store in the first place.
        replacement = RENAMED_TYPES[ctype]
        print(f"ERROR: '{ctype}' was renamed to '{replacement}' ({TYPE_LABELS[replacement]}). "
              f"Use --type {replacement} if that is what this is. For a compensation grant of "
              f"miles or points, use MILES or POINTS.", file=sys.stderr)
        if args.json:
            emit_json({"error": "retired_type", "given": ctype, "renamed_to": replacement,
                       "label": TYPE_LABELS[replacement]})
        sys.exit(1)
    if ctype not in VALID_TYPES:
        print(f"ERROR: Invalid type '{ctype}'. Valid: {', '.join(VALID_TYPES)}", file=sys.stderr)
        if args.json:
            emit_json({"error": "invalid_type", "given": ctype, "valid": VALID_TYPES})
        sys.exit(1)

    # A deposit has no expiry: the miles or points are in the loyalty account from
    # the moment of the grant, and the program's own expiry rules govern the balance
    # as a whole. Accepting one here would record a deadline this file cannot enforce
    # and `expiring` would then report it as an actionable date.
    if ctype in DEPOSIT_TYPES and args.expiry:
        print(f"ERROR: --expiry is not valid for {ctype}. A deposit lands in the loyalty "
              f"account on grant and has no expiry of its own.", file=sys.stderr)
        if args.json:
            emit_json({"error": "expiry_not_valid_for_deposit", "given_type": ctype,
                       "deposit_types": list(DEPOSIT_TYPES)})
        sys.exit(1)

    # Validate before touching storage. Parsing this after the write persisted the
    # credit and then died in a traceback, leaving a malformed record behind.
    expiry_date = None
    if args.expiry:
        try:
            expiry_date = datetime.strptime(args.expiry, "%Y-%m-%d").date()
        except ValueError:
            print(f"ERROR: Invalid --expiry '{args.expiry}'. Expected YYYY-MM-DD.",
                  file=sys.stderr)
            if args.json:
                emit_json({"error": "invalid_expiry", "given": args.expiry,
                           "expected_format": "YYYY-MM-DD"})
            sys.exit(1)

    cid = next_id(content)
    credit = {
        "id": cid,
        "type": ctype,
        "description": args.description,
        "value": args.value,
        "added": reference_date().isoformat(),
    }
    if args.passenger:
        credit["passenger"] = args.passenger
    if args.expiry:
        credit["expiry"] = args.expiry
    if args.airline:
        credit["airline"] = args.airline
    if args.brand:
        credit["brand"] = args.brand
    if args.restrictions:
        credit["restrictions"] = args.restrictions
    if args.confirmation:
        credit["confirmation"] = args.confirmation

    credit_md = format_credit(credit)
    # A deposit is history, not inventory — it never enters the available set, so it
    # is written to its own section rather than to Active.
    target = "compensation" if ctype in DEPOSIT_TYPES else "active"
    content = ensure_compensation_section(content) if target == "compensation" else content
    content = insert_credit(content, credit_md, target)
    write_inventory(content)

    days_to_expiry = None
    if expiry_date:
        days_to_expiry = (expiry_date - reference_date()).days

    if args.json:
        emit_json({"added": credit, "days_to_expiry": days_to_expiry})
        return

    pax_str = f" ({args.passenger})" if args.passenger else ""
    print(f"✅ Added credit #{cid}: [{ctype}] {args.description}{pax_str}")
    if args.expiry:
        print(f"   Expires: {args.expiry} ({days_to_expiry} days from now)")


def cmd_use(args):
    content = read_inventory()
    reject_multiline({"note": getattr(args, "note", None)}, args.json)

    # A deposit has no use transition. The miles landed in the account on grant, and
    # once there they are fungible with the rest of the balance — the loyalty program
    # owns that number and this file cannot shadow it. Marking one "used" would record
    # an application event that never happened, which is the fiction the separate
    # section exists to prevent.
    for deposit in parse_credits(content, "compensation"):
        if deposit["id"] == args.id:
            print(f"ERROR: #{args.id} is a {deposit['type']} deposit, not a credit. It has no "
                  f"used state — the balance landed in the loyalty account when it was granted.",
                  file=sys.stderr)
            if args.json:
                emit_json({"error": "deposit_has_no_use_transition", "id": args.id,
                           "type": deposit["type"]})
            sys.exit(1)

    content, credit = remove_credit(content, args.id, "active")

    if not credit:
        print(f"ERROR: Credit #{args.id} not found in active credits.", file=sys.stderr)
        if args.json:
            emit_json({"error": "not_found", "id": args.id})
        sys.exit(1)

    # Add usage metadata and move to archive
    credit["used_date"] = reference_date().isoformat()
    if args.note:
        credit["used_note"] = args.note

    credit_md = format_credit(credit)
    content = insert_credit(content, credit_md, "archive")
    write_inventory(content)

    if args.json:
        emit_json({"used": credit})
        return

    print(f"✅ Marked credit #{args.id} as used: [{credit['type']}] {credit['description']}")
    if args.note:
        print(f"   Note: {args.note}")


# --flag -> field label, for `update`. The label is what the record carries and what
# parse_credits() lowercases into a key.
UPDATABLE_FIELDS = {
    "value": "Value",
    "expiry": "Expiry",
    "passenger": "Passenger",
    "airline": "Airline",
    "brand": "Brand",
    "confirmation": "Confirmation",
    "restrictions": "Restrictions",
}


def reject_multiline(values, json_mode):
    """Refuse any value carrying a line break before it reaches the store.

    The record format is line-oriented: one field per line, `### #<id> — [TYPE] desc`
    for a heading. A value containing a newline is not stored as text — it becomes
    structure. `--value "5.00\\n- **Expiry**: 2099-01-01"` writes an expiry the caller
    never passed, and a `--description` carrying a `### #` line splices in a whole
    record, taking the id with it.

    Rejected rather than escaped: no legitimate field is multi-line, and a store whose
    values sometimes carry encoded newlines is harder to reason about than one where
    they cannot appear. `values` is a mapping of flag name to value.
    """
    offenders = sorted(flag for flag, value in values.items()
                       if isinstance(value, str) and ("\n" in value or "\r" in value))
    if not offenders:
        return
    print(f"ERROR: {', '.join('--' + f for f in offenders)} may not contain a line break. "
          f"Record fields are one line each; a newline would be stored as structure, not text.",
          file=sys.stderr)
    if json_mode:
        emit_json({"error": "multiline_value", "fields": offenders})
    sys.exit(1)


def apply_field_updates(body, updates, indent):
    """Set each named field on a record's body lines, returning the new body.

    A text-level edit, for the same reason the migration is one: reserializing through
    format_credit() would write only the ten fields it knows and silently drop anything
    else the record carries. An existing field is replaced where it sits, preserving its
    indentation; a new one is appended after the last recognized field line.
    """
    out, seen = [], set()
    for line in body:
        kv = re.match(r"- \*\*(.+?)\*\*:\s*(.*)", line.strip())
        label = kv.group(1) if kv else None
        if label in updates:
            line_indent = line[:len(line) - len(line.lstrip())]
            out.append(f"{line_indent}- **{label}**: {updates[label]}")
            seen.add(label)
            continue
        out.append(line)

    missing = [(label, value) for label, value in updates.items() if label not in seen]
    if not missing:
        return out

    # Append after the last field line so a new field lands inside the record rather
    # than after whatever blank lines trail it.
    last_field = max((i for i, ln in enumerate(out)
                      if re.match(r"- \*\*(.+?)\*\*:", ln.strip())), default=-1)
    additions = [f"{indent}- **{label}**: {value}" for label, value in missing]
    return out[:last_field + 1] + additions + out[last_field + 1:]


def cmd_update(args):
    """Edit an existing record's fields in place.

    The workflow this exists for: an airline confirms compensation and the expiry,
    voucher number, PIN, and restrictions arrive minutes-to-days later in a second
    email. Without this the only options were hand-editing a file whose header forbids
    it, or `use`-ing the half-entered record and re-adding it — which pollutes the
    archive with a ghost and burns an id.
    """
    content = read_inventory()

    reject_multiline({flag: getattr(args, flag, None)
                      for flag in list(UPDATABLE_FIELDS) + ["description"]}, args.json)

    updates = {}
    for flag, label in UPDATABLE_FIELDS.items():
        value = getattr(args, flag, None)
        if value is not None:
            updates[label] = normalize_brand(value) if flag == "brand" else value

    if not updates and args.description is None:
        print("ERROR: update needs at least one field to change. See --help.", file=sys.stderr)
        if args.json:
            emit_json({"error": "no_fields_given", "id": args.id,
                       "updatable": sorted(UPDATABLE_FIELDS) + ["description"]})
        sys.exit(1)

    if args.expiry is not None:
        try:
            datetime.strptime(args.expiry, "%Y-%m-%d")
        except ValueError:
            print(f"ERROR: Invalid --expiry '{args.expiry}'. Expected YYYY-MM-DD.",
                  file=sys.stderr)
            if args.json:
                emit_json({"error": "invalid_expiry", "given": args.expiry,
                           "expected_format": "YYYY-MM-DD"})
            sys.exit(1)

    # Active and compensation hold current records. The archive holds settled ones —
    # `use` already recorded their outcome, and editing them would rewrite history
    # rather than complete it.
    for section in ("active", "compensation"):
        start_marker, end_marker = section_markers(section)
        if content.find(start_marker) == -1 or content.find(end_marker) == -1:
            continue
        block_start = content.index("\n", content.find(start_marker)) + 1
        block = content[block_start:content.find(end_marker)]
        preamble, records = split_records(block)

        for position, (heading, body) in enumerate(records):
            match = re.match(r"(\s*### #(\d+)\s*[—–-]\s*)\[([A-Z]+)\](.*)", heading)
            if match is None or int(match.group(2)) != args.id:
                continue

            ctype = match.group(3)
            if ctype in DEPOSIT_TYPES and "Expiry" in updates:
                print(f"ERROR: --expiry is not valid for {ctype}. A deposit lands in the "
                      f"loyalty account on grant and has no expiry of its own.", file=sys.stderr)
                if args.json:
                    emit_json({"error": "expiry_not_valid_for_deposit", "id": args.id,
                               "given_type": ctype})
                sys.exit(1)

            new_heading = heading
            if args.description is not None:
                new_heading = f"{match.group(1)}[{ctype}] {args.description}"
            new_body = apply_field_updates(body, updates, _indent_of(heading))

            records[position] = (new_heading, new_body)
            rendered = list(preamble)
            for h, b in records:
                rendered.append(h)
                rendered.extend(b)
            write_inventory(replace_section_block(content, section, "\n".join(rendered)))

            changed = sorted(updates) + (["Description"] if args.description is not None else [])
            description = args.description if args.description is not None else match.group(4).strip()
            if args.json:
                emit_json({"updated": {"id": args.id, "type": ctype,
                                       "description": description, "section": section},
                           "fields_changed": changed})
                return
            print(f"✅ Updated credit #{args.id}: [{ctype}] {description}")
            print(f"   Changed: {', '.join(changed)}")
            return

    # Name the archive explicitly when that is where the record actually is — "not
    # found" would send the caller looking for a record that is sitting right there.
    archived = [c["id"] for c in parse_credits(content, "archive")]
    if args.id in archived:
        print(f"ERROR: Credit #{args.id} is archived. `update` edits current records; a "
              f"settled one keeps the outcome `use` recorded.", file=sys.stderr)
        if args.json:
            emit_json({"error": "record_is_archived", "id": args.id})
        sys.exit(1)

    print(f"ERROR: Credit #{args.id} not found.", file=sys.stderr)
    if args.json:
        emit_json({"error": "not_found", "id": args.id})
    sys.exit(1)


def cmd_history(args):
    """Report deposited compensation — the events, not the inventory.

    What `complaint-patterns` reads to state "third hardware failure in eight months"
    as fact, and what intake reads for prior-compensation context. No expiry, no used
    state, and never part of the available balance.
    """
    content = read_inventory()
    deposits = parse_credits(content, "compensation")

    if args.airline:
        deposits = [d for d in deposits if d.get("airline", "").upper() == args.airline.upper()]
    if args.brand:
        wanted = normalize_brand(args.brand)
        deposits = [d for d in deposits if d.get("brand_normalized") == wanted
                    or normalize_brand(d.get("brand", "")) == wanted]
    if args.passenger:
        needle = args.passenger.lower()
        deposits = [d for d in deposits if needle in d.get("passenger", "").lower()]

    if args.json:
        emit_json({"deposits": [credit_payload(d, reference_date()) for d in deposits],
                   "count": len(deposits)})
        return

    if not deposits:
        print("No compensation deposits recorded.")
        return

    print(f"\n📜 Compensation history — {len(deposits)} deposit(s)\n")
    print(f"{'ID':<5} {'TYPE':<8} {'ISSUER':<12} {'GRANTED':<12} {'AMOUNT':<18} DESCRIPTION")
    print("-" * 100)
    for d in deposits:
        issuer = d.get("airline") or d.get("brand") or "—"
        print(f"{d['id']:<5} {d['type']:<8} {issuer:<12} {d.get('added', '—'):<12} "
              f"{d.get('value', '—'):<18} {d.get('description', '')}")
    print("\nDeposits are history, not inventory — no expiry, no used state.\n")


def cmd_migrate(args):
    """Bring every record in the store up to SCHEMA_VERSION.

    The owner skill's operation. stateful-artifacts Migration Policy reserves
    migration to the owner (skills/using-travel-credits); no other write path
    in this script stamps or upgrades a record it did not itself author, so a
    store written by a non-owner is upgraded the next time this runs.

    Idempotent — a store already at SCHEMA_VERSION is left byte-identical and
    reports changed: false.
    """
    content = read_inventory()
    migrated, stats = stamp_schema_version(content)
    # v1 -> v2 relocates records between sections, which a per-record body transform
    # cannot express. It runs after stamping so every record carries the version it
    # is being moved under.
    migrated, moved = relocate_deposits(migrated)
    # v2 -> v3 renames a type token, also a heading edit. Runs after relocation so a
    # legacy COMP row valued in miles is moved on its Value first and never picks up a
    # COMPANION label on the way out.
    migrated, renamed = rename_legacy_types(migrated)
    # v3 -> v4 catches what v2 could not reach. v2 scanned Active only, so an archived
    # grant reached rename_legacy_types still typed COMP and became COMPANION. Running
    # after the rename means this re-reads those rows and reclassifies them on Value,
    # which is the same authority v2 used and does not depend on the label v3 left.
    migrated, archived_moved = relocate_deposits(migrated, section="archive")
    changed = migrated != content
    if changed:
        write_inventory(migrated)

    # Ask the parser rather than trusting the buckets. The buckets describe what
    # migration did; this asks what the reader can actually consume afterwards,
    # so a future divergence between the two line-matchers surfaces here as a
    # non-zero count instead of as a silently partial inventory downstream.
    # Every section, not just active+archive: count_record_headings() scans them all,
    # so omitting one here would report its records as unconsumable and stop the
    # router's Step 3 on a store that is entirely fine.
    readable = sum(len(parse_credits(migrated, name)) for name in SECTION_MARKERS)
    unconsumable = count_record_headings(migrated) - readable

    if args.json:
        emit_json({"schema_version": SCHEMA_VERSION, "changed": changed,
                   "unconsumable": unconsumable, "deposits_relocated": moved,
                   "archived_deposits_relocated": archived_moved,
                   "types_renamed": renamed, **stats})
        return

    if not changed:
        print(f"✅ Every record already at schema version {SCHEMA_VERSION} — nothing to migrate.")
    else:
        print(f"✅ Migrated the store to schema version {SCHEMA_VERSION}.")
        print(f"   Stamped (no prior version): {stats['stamped']}")
        print(f"   Upgraded from an older version: {stats['upgraded']}")
    if stats["skipped_newer"]:
        print(f"   ⚠️  Left untouched, newer than this script: {stats['skipped_newer']}")
    if stats["unreadable"]:
        print(f"   ⚠️  Unreadable version line: {stats['unreadable']}")
    for entry in moved:
        print(f"   Moved #{entry['id']} to compensation history: "
              f"[{entry['from_type']}] → [{entry['to_type']}]")
    for entry in archived_moved:
        print(f"   Moved #{entry['id']} out of the archive to compensation history: "
              f"[{entry['from_type']}] → [{entry['to_type']}]")
        for field, value in entry.get("dropped_used_state", {}).items():
            print(f"      dropped {field}: {value}")
    for entry in renamed:
        print(f"   Renamed #{entry['id']}: [{entry['from_type']}] → [{entry['to_type']}]")
    if unconsumable:
        print(f"   ⚠️  Records the reader cannot consume: {unconsumable}")


def cmd_expiring(args):
    content = read_inventory()
    credits = parse_credits(content, "active")
    days = args.days or 90
    today = reference_date()
    cutoff = today + timedelta(days=days)

    if args.passenger:
        credits = [c for c in credits if passenger_matches(c, args.passenger)]

    expiring = []
    no_expiry = []
    for c in credits:
        if "expiry" not in c:
            no_expiry.append(c)
            continue
        try:
            exp = datetime.strptime(c["expiry"], "%Y-%m-%d").date()
            if exp <= cutoff:
                days_left = (exp - today).days
                c["_days_left"] = days_left
                expiring.append(c)
        except ValueError:
            pass

    expiring.sort(key=lambda x: x["_days_left"])

    if args.json:
        emit_json({
            "as_of": today.isoformat(),
            "window_days": days,
            "expiring": [credit_payload(c, today) for c in expiring],
            "count": len(expiring),
            "no_expiry_count": len(no_expiry),
            "no_expiry": [credit_payload(c, today) for c in no_expiry],
        })
        return

    if not expiring:
        filter_msg = f" for {args.passenger}" if args.passenger else ""
        print(f"No credits{filter_msg} expiring within {days} days. 🎉")
        return

    print(f"=== Credits expiring within {days} days (as of {today}) ===\n")
    for c in expiring:
        days_left = c["_days_left"]
        if days_left < 0:
            urgency = f"⚠️  EXPIRED {-days_left} days ago!"
        elif days_left == 0:
            urgency = "🔥 EXPIRES TODAY!"
        elif days_left <= 7:
            urgency = f"🔥 {days_left} days left!"
        elif days_left <= 30:
            urgency = f"⚠️  {days_left} days left"
        else:
            urgency = f"⏰ {days_left} days left"

        pax = c.get("passenger", "?")
        print(f"  #{c['id']} [{c['type']}] {c['description']}")
        print(f"     Passenger: {pax} | {_issuer_label(c)} | Value: {c.get('value', '?')}")
        print(f"     Expiry: {c['expiry']} | {urgency}")
        if "restrictions" in c:
            print(f"     Restrictions: {c['restrictions']}")
        print()

    if no_expiry:
        print(f"({len(no_expiry)} credit(s) have no expiry date)")


def cmd_check(args):
    """Suggest applicable credits for a flight scenario.

    Checks ALL passengers by default. Use --passengers to limit to specific travelers
    (e.g. when only Baruch and Alice are flying, but you still want to know if a kid's
    credit on the repo airline could be used on a separate booking).
    """
    content = read_inventory()
    credits = parse_credits(content, "active")
    scenario = args.scenario.lower()
    today = reference_date()

    # Detect before the empty-store return: what the scenario names does not depend
    # on what the store holds, and reporting [] here would misreport a Delta or
    # Hilton scenario purely because no credits exist yet.
    scenario_airlines = airlines_in_scenario(args.scenario)
    scenario_hotels = hotels_in_scenario(args.scenario)

    if not credits:
        if args.json:
            emit_json({"scenario": args.scenario,
                       "airlines_detected": sorted(scenario_airlines),
                       "brands_detected": sorted(scenario_hotels),
                       "matches": [], "other_passenger_matches": [], "match_count": 0})
            return
        print("No active credits to check against.")
        return

    # Parse --passengers filter (comma-separated first names or full names)
    pax_filter = None
    if args.passengers:
        pax_filter = [p.strip().lower() for p in args.passengers.split(",")]

    if not args.json:
        print(f"=== Checking credits for: {args.scenario} ===")
        if scenario_airlines:
            print(f"    Airlines detected: {', '.join(sorted(scenario_airlines))}")
        if scenario_hotels:
            print(f"    Hotel brands detected: {', '.join(sorted(scenario_hotels))}")
        if pax_filter:
            print(f"    Filtering to passengers: {', '.join(args.passengers.split(','))}")
        print()

    applicable = []
    for c in credits:
        # Skip expired
        if "expiry" in c:
            try:
                exp = datetime.strptime(c["expiry"], "%Y-%m-%d").date()
                if exp < today:
                    continue
            except ValueError:
                pass

        # Passenger filter — transferable credits (no passenger) always count as "in filter".
        # Named credits for people not in the filter still show, but flagged as "other family member".
        transferable = is_transferable(c)
        pax_name = c.get("passenger", "")
        pax_in_filter = True
        if pax_filter and not transferable:
            pax_in_filter = any(f in pax_name.lower() for f in pax_filter)

        credit_airline = c.get("airline", "").upper()
        credit_brand = normalize_brand(c.get("brand", ""))
        ctype = c["type"]
        reasons = []

        # Brand (hotel/program) dimension — a credit tagged with --brand surfaces for a matching
        # hotel scenario. This is additive, not exclusive: a credit carrying BOTH --airline and
        # --brand also runs through the airline heuristics below, so it still matches airline
        # scenarios on its airline dimension rather than vanishing from them.
        if credit_brand and credit_brand in scenario_hotels:
            reasons.append(f"{TYPE_LABELS.get(ctype, ctype)} — {c.get('value', '?')} valid at {credit_brand}")

        # Airline dimension — skip ONLY for a brand-only credit (brand set, no airline). A pure
        # hotel credit must not run through the airline-era heuristics: they key off scenario
        # words ("business", "domestic", "companion") and the AMEX always-on note, none of which
        # know the issuer, so routing a hotel credit through them would surface it in an unrelated
        # airline scenario. A credit with both issuers, or with neither, still matches here.
        if credit_airline or not credit_brand:
            if ctype == "GUC":
                if any(w in scenario for w in ["international", "transatlantic", "transpacific",
                                                "tatl", "tpac", "delta one", "business"]):
                    if "DL" in scenario_airlines:
                        reasons.append("GUC can upgrade to Delta One on DL-operated international")
                    else:
                        reasons.append("GUC available — but only on DL-operated flights (check if applicable)")

            elif ctype == "RUC":
                if any(w in scenario for w in ["domestic", "repositioning", "repo", "bna"]):
                    if "DL" in scenario_airlines:
                        reasons.append("RUC can upgrade repositioning to First on DL domestic")
                    else:
                        reasons.append("RUC available — only on DL-operated domestic (check if applicable)")

            elif ctype == "COMPANION":
                if any(w in scenario for w in ["round-trip", "round trip", "domestic", "companion"]):
                    reasons.append("Companion certificate may apply — check route restrictions")

            elif ctype in ("ECREDIT", "VOUCHER"):
                # Match if the credit's airline matches any airline in the scenario
                if credit_airline and credit_airline in scenario_airlines:
                    label = "eCredit" if ctype == "ECREDIT" else "Voucher"
                    reasons.append(f"{label} ${c.get('value', '?')} valid on {credit_airline}")
                elif not credit_airline:
                    # No airline (and, since we're in this branch, no brand) on the credit.
                    reasons.append(f"{c['type']} ${c.get('value', '?')} — airline not specified, check manually")

            elif ctype == "PARTNER":
                if credit_airline and credit_airline in scenario_airlines:
                    reasons.append(f"Partner credit valid on {credit_airline}")

            elif ctype == "AMEX":
                reasons.append("Amex travel credit may offset cost — check card benefit rules")

            elif ctype == "OTHER":
                # Gift cards, misc credits — match by airline
                if credit_airline and credit_airline in scenario_airlines:
                    reasons.append(f"{c.get('description', 'Credit')} — ${c.get('value', '?')} valid on {credit_airline}")

        if reasons:
            applicable.append((c, reasons, pax_in_filter))

    # Split into direct matches and "other passenger" matches
    direct = [(c, r) for c, r, in_filter in applicable if in_filter]
    other_pax = [(c, r) for c, r, in_filter in applicable if not in_filter]

    if args.json:
        def match(entry, on_trip):
            credit, why = entry
            payload = credit_payload(credit, today)
            payload["reasons"] = why
            payload["passenger_on_trip"] = on_trip
            return payload
        emit_json({
            "scenario": args.scenario,
            "airlines_detected": sorted(scenario_airlines),
            "brands_detected": sorted(scenario_hotels),
            "matches": [match(e, True) for e in direct],
            "other_passenger_matches": [match(e, False) for e in other_pax],
            "match_count": len(direct) + len(other_pax),
        })
        return

    if direct:
        print(f"Found {len(direct)} applicable credit(s):\n")
        for c, reasons in direct:
            exp_str = c.get("expiry", "no expiry")
            pax = c.get("passenger", "?")
            issuer = _issuer_label(c)
            print(f"  #{c['id']} [{c['type']}] {c['description']}")
            print(f"     Passenger: {pax} | {issuer} | Value: {c.get('value', '?')} | Expiry: {exp_str}")
            for r in reasons:
                print(f"     → {r}")
            print()

    if other_pax:
        print(f"💡 {len(other_pax)} credit(s) from OTHER family members also match:\n")
        for c, reasons in other_pax:
            exp_str = c.get("expiry", "no expiry")
            pax = c.get("passenger", "?")
            issuer = _issuer_label(c)
            print(f"  #{c['id']} [{c['type']}] {c['description']}")
            print(f"     Passenger: {pax} | {issuer} | Value: {c.get('value', '?')} | Expiry: {exp_str}")
            for r in reasons:
                print(f"     → {r}")
            print(f"     ⚡ {pax} is not on this trip, but could book separately to use this credit")
            print()

    if not direct and not other_pax:
        print("No applicable credits found for this scenario.")
        print("(Credits may still apply — review full inventory with `list --verbose`)")


def cmd_summary(args):
    content = read_inventory()
    active = parse_credits(content, "active")
    archived = parse_credits(content, "archive")
    today = reference_date()

    if args.passenger:
        active = [c for c in active if passenger_matches(c, args.passenger)]
        archived = [c for c in archived if passenger_matches(c, args.passenger)]

    if args.json:
        by_pax = {}
        monetary = 0.0
        soon = 0
        for c in active:
            by_pax.setdefault(c.get("passenger", "Any (transferable)"), []).append(
                credit_payload(c, today))
            try:
                monetary += float(c.get("value", "0").replace("$", "").replace(",", ""))
            except (ValueError, AttributeError):
                pass
            left = days_left(c, today)
            if left is not None and 0 <= left <= 90:
                soon += 1
        emit_json({
            "as_of": today.isoformat(),
            "active_count": len(active),
            "archived_count": len(archived),
            "total_monetary_value": round(monetary, 2),
            "expiring_within_90_days": soon,
            "by_passenger": by_pax,
        })
        return

    filter_msg = f" for {args.passenger}" if args.passenger else ""
    print(f"=== Credits Summary{filter_msg} (as of {today}) ===\n")
    print(f"Active: {len(active)}  |  Used/Expired: {len(archived)}\n")

    if not active:
        print("No active credits.")
        return

    # Group by passenger, then by type
    by_passenger = {}
    total_monetary = 0.0
    expiring_soon = 0

    for c in active:
        pax = c.get("passenger", "Any (transferable)")
        by_passenger.setdefault(pax, []).append(c)
        try:
            val = float(c.get("value", "0").replace("$", "").replace(",", ""))
            total_monetary += val
        except (ValueError, AttributeError):
            pass
        if "expiry" in c:
            try:
                exp = datetime.strptime(c["expiry"], "%Y-%m-%d").date()
                if 0 <= (exp - today).days <= 90:
                    expiring_soon += 1
            except ValueError:
                pass

    for pax in sorted(by_passenger):
        credits = by_passenger[pax]
        print(f"  📋 {pax} ({len(credits)} credit(s)):")
        # Sub-group by type
        by_type = {}
        for c in credits:
            by_type.setdefault(c["type"], []).append(c)
        for t in VALID_TYPES:
            if t in by_type:
                for c in by_type[t]:
                    exp = c.get("expiry", "no expiry")
                    tags = []
                    if c.get("airline"):
                        tags.append(c["airline"])
                    if c.get("brand"):
                        tags.append(normalize_brand(c["brand"]))
                    tag_str = f" [{'/'.join(tags)}]" if tags else ""
                    print(f"    #{c['id']} [{t}]{tag_str} {c.get('description', '')[:45]} — {c.get('value', '?')} (exp: {exp})")
        print()

    if total_monetary > 0:
        print(f"Total monetary value: ${total_monetary:,.2f}")
    if expiring_soon:
        print(f"⚠️  {expiring_soon} credit(s) expiring within 90 days — run `expiring` for details")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Track flight credits, vouchers, and upgrade certificates",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s list                              Show all credits (all people)
  %(prog)s list --passenger baruch           Just Baruch's credits
  %(prog)s list --airline AA                 Credits valid on American
  %(prog)s list --brand Hilton               Credits valid at Hilton (incl. Conrad, etc.)
  %(prog)s add --type ECREDIT \\
    --description "Canceled BNA-JFK" \\
    --value 347.20 --expiry 2026-12-15 \\
    --passenger "Baruch Sadogursky" \\
    --airline DL                             Delta eCredit for Baruch
  %(prog)s add --type ECREDIT \\
    --description "Canceled BNA-ORD" \\
    --value 189.50 --expiry 2026-11-30 \\
    --passenger "Kid Sadogursky" \\
    --airline AA                             Kid's AA credit
  %(prog)s expiring --days 60                All people's expiring credits
  %(prog)s check --scenario \\
    "American Airlines BNA-ORD economy"      What credits apply? (checks everyone)
  %(prog)s check --scenario \\
    "Hilton London, 3 nights"                Surfaces hotel vouchers/comp nights too
  %(prog)s check --scenario \\
    "Delta business JFK-CDG" \\
    --passengers "Baruch,Alice"              These travelers (still flags family)
  %(prog)s summary                           Overview by person
  %(prog)s summary --passenger baruch        Just Baruch
        """,
    )
    # Inherited by every subcommand so `<cmd> --json` works uniformly. Agent
    # callers pass it; the prose default is the interactive human path.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true",
                        help="Emit a JSON object on stdout instead of prose")

    sub = parser.add_subparsers(dest="command")

    # list
    ls = sub.add_parser("list", help="List active credits", parents=[common])
    ls.add_argument("--type", help=f"Filter by type: {', '.join(VALID_TYPES)}")
    ls.add_argument("--passenger", help="Filter by passenger name (substring match)")
    ls.add_argument("--airline", help="Filter by airline code (e.g. DL, AA, AF)")
    ls.add_argument("--brand", help="Filter by hotel/program brand (e.g. Hilton, Marriott); sub-brands collapse to the chain")
    ls.add_argument("--verbose", "-v", action="store_true", help="Show full details")

    # add
    add = sub.add_parser("add", help="Add a new credit", parents=[common])
    add.add_argument("--type", required=True, help=f"Credit type: {', '.join(VALID_TYPES)}")
    add.add_argument("--description", "--desc", required=True, help="Description")
    add.add_argument("--value", required=True, help="Value (dollar amount or '1 certificate')")
    add.add_argument("--passenger", help="Passenger name (who owns this credit — omit for transferable items like gift cards)")
    add.add_argument("--expiry", help="Expiration date (YYYY-MM-DD)")
    add.add_argument("--airline", help="Airline code the credit is valid on (e.g. DL, AA, AF)")
    add.add_argument("--brand", help="Hotel/program brand the credit is valid on (e.g. Hilton, Marriott, IHG, Hyatt)")
    add.add_argument("--restrictions", help="Usage restrictions")
    add.add_argument("--confirmation", help="Confirmation/reference code")

    # use
    use = sub.add_parser("use", help="Mark a credit as used", parents=[common])
    use.add_argument("--id", type=int, required=True, help="Credit ID number")
    use.add_argument("--note", help="Usage note (what it was applied to)")

    # expiring
    exp = sub.add_parser("expiring", help="Show credits expiring soon", parents=[common])
    exp.add_argument("--days", type=int, default=90, help="Days ahead to check (default: 90)")
    exp.add_argument("--passenger", help="Filter by passenger name (substring match)")

    # check
    chk = sub.add_parser("check", help="Check applicable credits for a scenario", parents=[common])
    chk.add_argument("--scenario", required=True, help="Describe the flight scenario (include airline and route)")
    chk.add_argument("--passengers", help="Comma-separated passenger names on this trip (default: check all)")

    # summary
    sm = sub.add_parser("summary", help="Summary of all credits", parents=[common])
    sm.add_argument("--passenger", help="Filter by passenger name (substring match)")

    # init
    init = sub.add_parser("init", help="Set up credits storage (default or custom location like Google Drive)", parents=[common])
    init_mode = init.add_mutually_exclusive_group()
    init_mode.add_argument("--default", action="store_true", help="Non-interactive: create a fresh store at ~/.claude/travel-credits")
    init_mode.add_argument("--path", help="Non-interactive: create a fresh store at this path, symlinked back to ~/.claude")

    # link
    lnk = sub.add_parser("link", help="Link ~/.claude/travel-credits to an existing inventory directory (e.g. cloud-synced)", parents=[common])
    lnk.add_argument("--path", required=True, help="Path to the existing travel-credits directory")

    # status
    sub.add_parser("status", help="Report store readiness: ready (0) / missing (3) / invalid (4)", parents=[common])

    # migrate — owner-skill operation, see cmd_migrate()
    sub.add_parser("migrate", help="Bring every record up to the current schema version (owner skill only)", parents=[common])

    # update — edit an existing record's fields, see cmd_update()
    upd = sub.add_parser("update", help="Edit fields on an existing credit (details that arrive later)", parents=[common])
    upd.add_argument("--id", type=int, required=True, help="Credit ID to update")
    upd.add_argument("--description", help="Replace the description")
    upd.add_argument("--value", help="Replace the value")
    upd.add_argument("--expiry", help="Set the expiry (YYYY-MM-DD)")
    upd.add_argument("--passenger", help="Set the passenger")
    upd.add_argument("--airline", help="Set the airline code")
    upd.add_argument("--brand", help="Set the hotel/loyalty brand")
    upd.add_argument("--confirmation", help="Set the confirmation or case number")
    upd.add_argument("--restrictions", help="Set the restrictions text")

    # history — deposited compensation, see cmd_history()
    hist = sub.add_parser("history", help="Show deposited compensation (miles/points grants) — history, not inventory", parents=[common])
    hist.add_argument("--airline", help="Filter by airline code")
    hist.add_argument("--brand", help="Filter by hotel/loyalty brand")
    hist.add_argument("--passenger", help="Filter by passenger name (substring)")

    # Read before parsing: an argparse failure exits before args exist, and that
    # exit still has to honour the JSON contract.
    json_mode = "--json" in sys.argv

    # Validate the reference-date override up front rather than on first use. Only
    # some paths read a date, so a lazy check would let a malformed value pass
    # silently through the others — and a suite that meant to freeze time would run
    # against the real clock and pass for the wrong reason on exactly those paths.
    reference_date()

    try:
        args = parser.parse_args()
        if not args.command:
            parser.print_help()
            sys.exit(1)

        {
            "list": cmd_list,
            "add": cmd_add,
            "use": cmd_use,
            "expiring": cmd_expiring,
            "check": cmd_check,
            "summary": cmd_summary,
            "init": cmd_init,
            "link": cmd_link,
            "status": cmd_status,
            "migrate": cmd_migrate,
            "history": cmd_history,
            "update": cmd_update,
        }[args.command](args)
    except SystemExit as exc:
        # Any exit that skipped emit_json still owes the caller an object: under
        # --json an empty stdout is unparseable, which reads as a crashed script
        # rather than a reported failure. The diagnostic itself is already on
        # stderr; this only guarantees stdout holds one object.
        code = exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
        if json_mode and code and not JSON_EMITTED:
            emit_json({"error": "command_failed", "exit_code": code,
                       "detail": "diagnostic on stderr"})
        raise
    # outer-boundary-process-contract: the caller reads stdout as JSON, so an
    # unexpected exception surfacing as a traceback with empty stdout is
    # indistinguishable from a crash. This emits a structured failure and
    # re-raises; letting it propagate bare would break the stdout contract.
    except Exception:  # noqa: BLE001
        if json_mode and not JSON_EMITTED:
            emit_json({"error": "unexpected_failure",
                       "detail": "traceback on stderr"})
        raise
