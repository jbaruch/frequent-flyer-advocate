#!/usr/bin/env python3
"""
Track flight credits, vouchers, and upgrade certificates for the whole family.
Inventory stored globally at ~/.claude/travel-credits/inventory.md so any skill can access it.
Run `init` first to set up storage (default or custom location like Google Drive).

Supports multiple passengers and airlines — the primary use is Baruch + Alice travel,
but credits for kids or on non-Delta airlines are tracked too so nothing expires forgotten.

Usage:
  python3 credits-tracker.py init
  python3 credits-tracker.py list [--type TYPE] [--passenger NAME] [--airline CODE] [--verbose]
  python3 credits-tracker.py add --type TYPE --description DESC --value VALUE --passenger NAME [--expiry YYYY-MM-DD] [--airline CODE] [--restrictions TEXT] [--confirmation CODE]
  python3 credits-tracker.py use --id ID [--note TEXT]
  python3 credits-tracker.py expiring [--days N] [--passenger NAME]
  python3 credits-tracker.py check --scenario SCENARIO [--passengers NAME,NAME]
  python3 credits-tracker.py summary [--passenger NAME]

Examples:
  python3 credits-tracker.py add --type GUC --description "Diamond GUC 2026 #1" --value "1 certificate" --expiry 2027-01-31 --passenger "Baruch Sadogursky" --airline DL --restrictions "DL-operated international only, paid fare required"
  python3 credits-tracker.py add --type ECREDIT --description "Canceled BNA-JFK Dec 2025" --value 347.20 --expiry 2026-12-15 --passenger "Baruch Sadogursky" --airline DL --confirmation "ABC123"
  python3 credits-tracker.py add --type ECREDIT --description "Canceled BNA-ORD Nov 2025" --value 189.50 --expiry 2026-11-30 --passenger "Kid Sadogursky" --airline AA --confirmation "XYZ789"
  python3 credits-tracker.py add --type COMP --description "Delta Reserve companion cert 2026" --value "1 certificate" --expiry 2027-01-31 --passenger "Baruch Sadogursky" --airline DL --restrictions "Round-trip domestic or to/from Canada/Mexico, main cabin or above"
  python3 credits-tracker.py list --passenger baruch
  python3 credits-tracker.py expiring --days 90
  python3 credits-tracker.py check --scenario "American Airlines BNA-ORD economy repo"
  python3 credits-tracker.py check --scenario "Delta business JFK-CDG" --passengers "Baruch,Alice"
  python3 credits-tracker.py use --id 3 --note "Applied to BNA-YUL repo Mar 2026"
"""

import argparse
import os
import re
import sys
from datetime import datetime, timedelta

CREDITS_DIR = os.path.join(os.path.expanduser("~"), ".claude", "travel-credits")
INVENTORY_PATH = os.path.join(CREDITS_DIR, "inventory.md")

VALID_TYPES = ["GUC", "RUC", "COMP", "ECREDIT", "VOUCHER", "PARTNER", "AMEX", "OTHER"]

TYPE_LABELS = {
    "GUC": "Global Upgrade Certificate",
    "RUC": "Regional Upgrade Certificate",
    "COMP": "Companion Certificate",
    "ECREDIT": "eCredit",
    "VOUCHER": "Voucher",
    "PARTNER": "Partner Credit",
    "AMEX": "Amex Travel Credit",
    "OTHER": "Other",
}

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


EMPTY_INVENTORY = """# Flight Credits, Vouchers & Upgrade Certificates Inventory

Track all active credits here. Use `credits-tracker.py` for all updates — do not hand-edit.

## Active Credits

<!-- CREDITS_START — do not edit this marker -->
<!-- CREDITS_END — do not edit this marker -->

## Used/Expired Credits (Archive)

<!-- ARCHIVE_START — do not edit this marker -->
<!-- ARCHIVE_END — do not edit this marker -->
"""


def ensure_inventory():
    """Create the inventory file and directory if they don't exist.

    If CREDITS_DIR is a symlink, follow it (user chose a custom location via `init`).
    Otherwise create the default directory.
    """
    if not os.path.exists(INVENTORY_PATH):
        # Resolve symlinks — if CREDITS_DIR is a symlink, the real dir must exist
        real_dir = os.path.realpath(CREDITS_DIR)
        os.makedirs(real_dir, exist_ok=True)
        with open(INVENTORY_PATH, "w") as f:
            f.write(EMPTY_INVENTORY)


def cmd_init(_args):
    """Interactive setup: choose default or custom storage location."""
    if os.path.exists(CREDITS_DIR):
        real_path = os.path.realpath(CREDITS_DIR)
        is_symlink = os.path.islink(CREDITS_DIR)
        if is_symlink:
            print(f"Already initialized. Storage: {real_path} (symlinked from {CREDITS_DIR})")
        else:
            print(f"Already initialized. Storage: {real_path}")

        has_credits = False
        if os.path.exists(INVENTORY_PATH):
            content = read_inventory()
            has_credits = bool(parse_credits(content, "active"))

        if has_credits:
            print(f"Inventory has active credits. To change location, move the data manually.")
            return
        else:
            response = input("No active credits. Reinitialize with a different location? [y/N] ").strip().lower()
            if response != "y":
                return
            # Clean up existing
            if is_symlink:
                os.unlink(CREDITS_DIR)
            else:
                import shutil
                shutil.rmtree(CREDITS_DIR)

    print()
    print("Where should the credits inventory be stored?")
    print()
    print(f"  1. Default: {CREDITS_DIR}")
    print("  2. Custom path (e.g. Google Drive, Dropbox, iCloud)")
    print()
    choice = input("Choice [1/2]: ").strip()

    if choice == "2":
        custom = input("Enter full path to credits directory: ").strip()
        custom = os.path.expanduser(custom)

        if not custom:
            print("ERROR: No path provided.", file=sys.stderr)
            sys.exit(1)

        # Create the custom directory
        os.makedirs(custom, exist_ok=True)

        # Create symlink from CREDITS_DIR -> custom
        parent = os.path.dirname(CREDITS_DIR)
        os.makedirs(parent, exist_ok=True)
        os.symlink(custom, CREDITS_DIR)

        print(f"✅ Created {custom}")
        print(f"✅ Symlinked {CREDITS_DIR} → {custom}")
    else:
        os.makedirs(CREDITS_DIR, exist_ok=True)
        print(f"✅ Created {CREDITS_DIR}")

    # Create empty inventory
    ensure_inventory()
    print(f"✅ Initialized empty inventory at {os.path.realpath(INVENTORY_PATH)}")


def read_inventory():
    ensure_inventory()
    with open(INVENTORY_PATH, "r") as f:
        return f.read()


def write_inventory(content):
    ensure_inventory()
    with open(INVENTORY_PATH, "w") as f:
        f.write(content)


def parse_credits(content, section="active"):
    """Parse credit entries from the inventory file."""
    if section == "active":
        start_marker = "<!-- CREDITS_START"
        end_marker = "<!-- CREDITS_END"
    else:
        start_marker = "<!-- ARCHIVE_START"
        end_marker = "<!-- ARCHIVE_END"

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
    return credits


def format_credit(c):
    """Format a credit entry as markdown."""
    lines = [f"### #{c['id']} — [{c['type']}] {c['description']}"]
    if "value" in c:
        lines.append(f"- **Value**: {c['value']}")
    if "expiry" in c:
        lines.append(f"- **Expiry**: {c['expiry']}")
    if "passenger" in c:
        lines.append(f"- **Passenger**: {c['passenger']}")
    if "airline" in c:
        lines.append(f"- **Airline**: {c['airline']}")
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
    """Get next available ID from both active and archived credits."""
    all_ids = [c["id"] for c in parse_credits(content, "active")]
    all_ids += [c["id"] for c in parse_credits(content, "archive")]
    return max(all_ids, default=0) + 1


def insert_credit(content, credit_md, section="active"):
    """Insert a formatted credit entry into the inventory."""
    if section == "active":
        marker = "<!-- CREDITS_END"
    else:
        marker = "<!-- ARCHIVE_END"

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
    if section == "active":
        start_marker = "<!-- CREDITS_START"
        end_marker = "<!-- CREDITS_END"
    else:
        start_marker = "<!-- ARCHIVE_START"
        end_marker = "<!-- ARCHIVE_END"

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

    if not credits:
        filters = []
        if args.type:
            filters.append(f"type={args.type.upper()}")
        if args.passenger:
            filters.append(f"passenger={args.passenger}")
        if args.airline:
            filters.append(f"airline={args.airline}")
        filter_msg = f" matching {', '.join(filters)}" if filters else ""
        print(f"No active credits{filter_msg}.")
        return

    today = datetime.now().date()

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
        print(f"{'#':<5} {'Type':<10} {'Passenger':<20} {'Airline':<8} {'Description':<30} {'Value':<15} {'Expiry':<12} {'Status':<10}")
        print(f"{'-'*5} {'-'*10} {'-'*20} {'-'*8} {'-'*30} {'-'*15} {'-'*12} {'-'*10}")
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
            print(f"{c['id']:<5} {c['type']:<10} {pax:<20} {airline:<8} {desc:<30} {val:<15} {exp_str:<12} {status:<10}")


def cmd_add(args):
    content = read_inventory()
    ctype = args.type.upper()
    if ctype not in VALID_TYPES:
        print(f"ERROR: Invalid type '{ctype}'. Valid: {', '.join(VALID_TYPES)}", file=sys.stderr)
        sys.exit(1)

    cid = next_id(content)
    credit = {
        "id": cid,
        "type": ctype,
        "description": args.description,
        "value": args.value,
        "added": datetime.now().strftime("%Y-%m-%d"),
    }
    if args.passenger:
        credit["passenger"] = args.passenger
    if args.expiry:
        credit["expiry"] = args.expiry
    if args.airline:
        credit["airline"] = args.airline
    if args.restrictions:
        credit["restrictions"] = args.restrictions
    if args.confirmation:
        credit["confirmation"] = args.confirmation

    credit_md = format_credit(credit)
    content = insert_credit(content, credit_md, "active")
    write_inventory(content)
    pax_str = f" ({args.passenger})" if args.passenger else ""
    print(f"✅ Added credit #{cid}: [{ctype}] {args.description}{pax_str}")
    if args.expiry:
        exp = datetime.strptime(args.expiry, "%Y-%m-%d").date()
        days = (exp - datetime.now().date()).days
        print(f"   Expires: {args.expiry} ({days} days from now)")


def cmd_use(args):
    content = read_inventory()
    content, credit = remove_credit(content, args.id, "active")

    if not credit:
        print(f"ERROR: Credit #{args.id} not found in active credits.", file=sys.stderr)
        sys.exit(1)

    # Add usage metadata and move to archive
    credit["used_date"] = datetime.now().strftime("%Y-%m-%d")
    if args.note:
        credit["used_note"] = args.note

    credit_md = format_credit(credit)
    content = insert_credit(content, credit_md, "archive")
    write_inventory(content)
    print(f"✅ Marked credit #{args.id} as used: [{credit['type']}] {credit['description']}")
    if args.note:
        print(f"   Note: {args.note}")


def cmd_expiring(args):
    content = read_inventory()
    credits = parse_credits(content, "active")
    days = args.days or 90
    today = datetime.now().date()
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
        airline = c.get("airline", "")
        airline_str = f" ({airline})" if airline else ""
        print(f"  #{c['id']} [{c['type']}] {c['description']}")
        print(f"     Passenger: {pax} | Airline: {airline_str or '—'} | Value: {c.get('value', '?')}")
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
    today = datetime.now().date()

    if not credits:
        print("No active credits to check against.")
        return

    # Parse --passengers filter (comma-separated first names or full names)
    pax_filter = None
    if args.passengers:
        pax_filter = [p.strip().lower() for p in args.passengers.split(",")]

    # Extract airlines mentioned in the scenario
    scenario_airlines = airlines_in_scenario(args.scenario)

    print(f"=== Checking credits for: {args.scenario} ===")
    if scenario_airlines:
        print(f"    Airlines detected: {', '.join(sorted(scenario_airlines))}")
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
        ctype = c["type"]
        reasons = []

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

        elif ctype == "COMP":
            if any(w in scenario for w in ["round-trip", "round trip", "domestic", "companion"]):
                reasons.append("Companion certificate may apply — check route restrictions")

        elif ctype in ("ECREDIT", "VOUCHER"):
            # Match if the credit's airline matches any airline in the scenario
            if credit_airline and credit_airline in scenario_airlines:
                label = "eCredit" if ctype == "ECREDIT" else "Voucher"
                reasons.append(f"{label} ${c.get('value', '?')} valid on {credit_airline}")
            elif not credit_airline:
                # No airline specified on the credit — flag it as potentially applicable
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

    if direct:
        print(f"Found {len(direct)} applicable credit(s):\n")
        for c, reasons in direct:
            exp_str = c.get("expiry", "no expiry")
            pax = c.get("passenger", "?")
            airline = c.get("airline", "—")
            print(f"  #{c['id']} [{c['type']}] {c['description']}")
            print(f"     Passenger: {pax} | Airline: {airline} | Value: {c.get('value', '?')} | Expiry: {exp_str}")
            for r in reasons:
                print(f"     → {r}")
            print()

    if other_pax:
        print(f"💡 {len(other_pax)} credit(s) from OTHER family members also match:\n")
        for c, reasons in other_pax:
            exp_str = c.get("expiry", "no expiry")
            pax = c.get("passenger", "?")
            airline = c.get("airline", "—")
            print(f"  #{c['id']} [{c['type']}] {c['description']}")
            print(f"     Passenger: {pax} | Airline: {airline} | Value: {c.get('value', '?')} | Expiry: {exp_str}")
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
    today = datetime.now().date()

    if args.passenger:
        active = [c for c in active if passenger_matches(c, args.passenger)]
        archived = [c for c in archived if passenger_matches(c, args.passenger)]

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
                    airline = c.get("airline", "")
                    airline_str = f" [{airline}]" if airline else ""
                    print(f"    #{c['id']} [{t}]{airline_str} {c.get('description', '')[:45]} — {c.get('value', '?')} (exp: {exp})")
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
    "Delta business JFK-CDG" \\
    --passengers "Baruch,Alice"              These travelers (still flags family)
  %(prog)s summary                           Overview by person
  %(prog)s summary --passenger baruch        Just Baruch
        """,
    )
    sub = parser.add_subparsers(dest="command")

    # list
    ls = sub.add_parser("list", help="List active credits")
    ls.add_argument("--type", help=f"Filter by type: {', '.join(VALID_TYPES)}")
    ls.add_argument("--passenger", help="Filter by passenger name (substring match)")
    ls.add_argument("--airline", help="Filter by airline code (e.g. DL, AA, AF)")
    ls.add_argument("--verbose", "-v", action="store_true", help="Show full details")

    # add
    add = sub.add_parser("add", help="Add a new credit")
    add.add_argument("--type", required=True, help=f"Credit type: {', '.join(VALID_TYPES)}")
    add.add_argument("--description", "--desc", required=True, help="Description")
    add.add_argument("--value", required=True, help="Value (dollar amount or '1 certificate')")
    add.add_argument("--passenger", help="Passenger name (who owns this credit — omit for transferable items like gift cards)")
    add.add_argument("--expiry", help="Expiration date (YYYY-MM-DD)")
    add.add_argument("--airline", help="Airline code the credit is valid on (e.g. DL, AA, AF)")
    add.add_argument("--restrictions", help="Usage restrictions")
    add.add_argument("--confirmation", help="Confirmation/reference code")

    # use
    use = sub.add_parser("use", help="Mark a credit as used")
    use.add_argument("--id", type=int, required=True, help="Credit ID number")
    use.add_argument("--note", help="Usage note (what it was applied to)")

    # expiring
    exp = sub.add_parser("expiring", help="Show credits expiring soon")
    exp.add_argument("--days", type=int, default=90, help="Days ahead to check (default: 90)")
    exp.add_argument("--passenger", help="Filter by passenger name (substring match)")

    # check
    chk = sub.add_parser("check", help="Check applicable credits for a scenario")
    chk.add_argument("--scenario", required=True, help="Describe the flight scenario (include airline and route)")
    chk.add_argument("--passengers", help="Comma-separated passenger names on this trip (default: check all)")

    # summary
    sm = sub.add_parser("summary", help="Summary of all credits")
    sm.add_argument("--passenger", help="Filter by passenger name (substring match)")

    # init
    sub.add_parser("init", help="Set up credits storage (default or custom location like Google Drive)")

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
    }[args.command](args)
