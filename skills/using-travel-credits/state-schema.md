# Travel Credits Inventory — State Schema

The stateful artifact this skill owns.

## Location

| Path | Shape |
|---|---|
| `~/.claude/travel-credits/` | the store — a directory, or a symlink to one, commonly pointing at cloud storage |
| `~/.claude/travel-credits/inventory.md` | the artifact — a single markdown file inside the store |

The store is global rather than per-project so every consumer reaches the same copy. `CREDITS_DIR` and `INVENTORY_PATH` at the top of `credits-tracker.py` are the authority for both.

## Owner

`skills/using-travel-credits` owns the artifact's shape. Shape changes originate here and nowhere else, per `coding-policy: stateful-artifacts`.

`scripts/credits-tracker.py` is the implementation this skill writes through. It is not the owner; it is how the owner writes.

## File structure

Two sections, in this order:

```markdown
## Active Credits
## Used/Expired Credits (Archive)
```

`use` moves a record from the first to the second. Records are never deleted — the archive is read as prior-compensation history by `frequent-flyer-advocate` during intake, and by `complaint-patterns` for establishing repeat-failure patterns.

## Record shape

```markdown
### #<id> — [<TYPE>] <description>
- **Schema version**: <integer>
- **Value**: <free text: "200.00", "25000 miles", "2 nights">
- **Expiry**: <YYYY-MM-DD>
- **Passenger**: <full name>
- **Airline**: <IATA code>
- **Brand**: <normalized chain code>
- **Confirmation**: <case or confirmation number>
- **Restrictions**: <free text>
- **Added**: <YYYY-MM-DD>
- **Used date**: <YYYY-MM-DD>
- **Used note**: <free text>
```

The heading and `Schema version` are always written. Every other field is written when present and omitted when absent — readers must tolerate any of them missing.

- `<TYPE>` comes from the script's `VALID_TYPES`. The accepted set is the script's, read via `--help`
- `Airline` and `Brand` are independent optional dimensions, not alternatives. A record may carry either, both, or neither; a co-branded credit carries both and matches airline and hotel scenarios alike
- `Brand` is stored normalized to a chain code, so sub-brands collapse to their parent
- `Passenger` absent means a transferable instrument
- `Used date` and `Used note` appear only on archived records

## Writer / reader contract

| Skill | Role | Operations |
|---|---|---|
| `using-travel-credits` | owner, writer, reader | `status`, `link`, `init`, `list`, `expiring`, `check`, `add`, `use` |
| `frequent-flyer-advocate` | writer, reader | logs granted compensation; reads the archive for prior-compensation history |
| `jbaruch/jbaruch-travel-policy` | reader, writer | reads before a search and when presenting an itinerary; marks credits used after a booking |

Every writer promises: writes go through `credits-tracker.py`, never a hand edit. Readers promise: absent fields are absent, not empty — a missing `Expiry` means no expiry, never an expired credit.

## Bootstrap states

`status` reports one of three, and the exit code carries it:

| State | Exit | Meaning |
|---|---|---|
| `ready` | 0 | store present and usable |
| `missing` | 3 | nothing at the path |
| `invalid` | 4 | a plain file, or a dangling symlink whose target is unmounted |

`missing` and `invalid` stay distinct deliberately. An unmounted cloud store is indistinguishable from an absent one to a naive check, and creating a fresh store beside the real one forks the data irrecoverably. The script refuses rather than guesses.

## Versioning

Current version: **1**, the `SCHEMA_VERSION` constant in `credits-tracker.py`.

Every write stamps every record, including records already in the store that predate versioning — `write_inventory()` routes through `stamp_schema_version()`, which inserts the line after any `### #` heading that lacks one. A record with no version line reads as version 1.

The stamp is a text-level insert, not a parse-and-reformat of the whole store. Reformatting would drop any field the current formatter does not know and rewrite records nobody touched, so the migration would risk more than it repairs.

## Migration policy

Only this skill migrates, and it migrates on write. `stamp_schema_version()` walks every record:

| Stored version | Action |
|---|---|
| absent | predates versioning; reads as 1 and is stamped |
| older than `SCHEMA_VERSION` | stepped up one version at a time through `upgrade_record_body()`, then restamped |
| equal | left as is |
| newer | left untouched, never rewritten down |

`upgrade_record_body()` is the per-step transform. It is identity today — version 1 is the first version and no shipped record predates it — and exists so a future bump adds one branch rather than inventing the migration machinery under time pressure.

A record newer than `SCHEMA_VERSION` is not consumed either: `parse_credits()` omits it with a warning, per Migration Policy's rule that a lagging reader treats it as no usable prior state. An owner that cannot read a record must not rewrite it.

Bump `SCHEMA_VERSION` only alongside a migration here. While `jbaruch/jbaruch-travel-policy` still ships its own copy of the script, a bump also falls under `stateful-artifacts` Cross-Pipeline Schema Bumps — two independently-released writers share this store, and the rollout has to account for both. Version 1 is safe under that constraint because both copies' parsers preserve records they did not write and ignore fields they do not know. A version 2 would not be. Removing the duplicate is tracked as the prerequisite in [#30](https://github.com/jbaruch/frequent-flyer-advocate/issues/30).
