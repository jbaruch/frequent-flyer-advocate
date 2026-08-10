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

Three sections, in this order:

```markdown
## Active Credits
## Used/Expired Credits (Archive)
## Compensation History (Deposited)
```

`use` moves a record from the first to the second. Records are never deleted — the archive is read as prior-compensation history by `frequent-flyer-advocate` during intake, and by `complaint-patterns` for establishing repeat-failure patterns.

**Compensation History** holds instruments with no lifecycle: miles and points a program deposited at the moment of the grant. They are events, not inventory — never in `list`, `expiring`, `check`, or the monetary total, and with no `use` transition, because there is nothing to transition to. `history` reads them.

The section names are the `SECTION_MARKERS` mapping in `credits-tracker.py`; a store written before a section existed gains it on the next `migrate`.

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
| `using-travel-credits` | owner, writer, reader | `status`, `link`, `init`, `migrate`, `list`, `expiring`, `check`, `history`, `add`, `use` |
| `frequent-flyer-advocate` | writer, reader | logs granted compensation; reads the archive for prior-compensation history |
| `jbaruch/jbaruch-travel-policy` | reader, writer | reads before a search and when presenting an itinerary; marks credits used after a booking |

`migrate` is the owner's alone. No other skill in this table may run it, and no other write path performs one.

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

Current version: **2**, the `SCHEMA_VERSION` constant in `credits-tracker.py`.

**v1 → v2** moved miles and points grants out of Active into Compensation History and gave them the `MILES` / `POINTS` types. They never had a held-then-applied lifecycle, so Active counted them as available indefinitely and `use` was the only exit — recording an application event that never happened. A record is relocated when its `Value` names one of those two units; anything else stays where it is, so a genuine companion certificate valued "1 certificate" is untouched. The record's block moves verbatim apart from its type token, so fields the formatter does not know survive.

A writer stamps the record it is itself writing — `format_credit()` emits the current version on every record it formats. It does **not** stamp records it did not author, and no reader consumes a record that carries no version field.

The stamp is a text-level insert, not a parse-and-reformat of the whole store. Reformatting would drop any field the current formatter does not know and rewrite records nobody touched, so the migration would risk more than it repairs.

## Migration policy

Only this skill migrates, and it migrates through one explicit operation: the `migrate` subcommand, reached from Step 3.

The owner migrates what it is about to consume. Migration Policy has the owner detect an older record on read, upgrade it, and rewrite it — so the router runs Step 3 ahead of every store-touching step (4-9), not only when a user asks to migrate.

A non-owner does the opposite: it declines the record. `parse_credits()` consumes only records at `SCHEMA_VERSION` exactly, omitting anything off-version in either direction with a per-record stderr warning naming the recovery. Older means the owner has not upgraded it yet; newer means the owner is ahead of this reader. Both are read-only *no usable prior state*, and neither is a record a non-owner may migrate. A record carrying no version field is declined on the same grounds: Required Attributes puts a `schema_version` on every record, and without one a reader cannot know the shape it is holding. `migrate` stamps it and it reads normally afterwards.

`write_inventory()` deliberately does not migrate. Every skill in the writer table below calls `credits-tracker.py` directly, so a migration on the write path would run under a non-owner writer — which Migration Policy reserves to the owner. A non-owner's `add` or `use` therefore leaves everyone else's records at whatever version they carry; the next owner run upgrades them.

`migrate` is idempotent: a store already current is left byte-identical and reports `changed: false`. That is what makes running it ahead of every owner read affordable.

`stamp_schema_version()` walks every record:

| Stored version | Action |
|---|---|
| absent | not consumed by any reader; stamped at `SCHEMA_VERSION` |
| older than `SCHEMA_VERSION` | stepped up one version at a time through `upgrade_record_body()`, then restamped |
| equal | left as is |
| newer | left untouched, never rewritten down |

`upgrade_record_body()` is the per-step field transform. It is identity through v1 → v2, whose change is a relocation between sections rather than a reshaped record body — `relocate_deposits()` handles that, since moving a record between sections is not something a body transform can express. A bump that reshapes fields adds its branch here.

An owner that cannot read a record must not rewrite it, which is why `migrate` leaves a newer record alone rather than stepping it down.

Bump `SCHEMA_VERSION` only alongside a migration here. While `jbaruch/jbaruch-travel-policy` still ships its own copy of the script, a bump also falls under `stateful-artifacts` Cross-Pipeline Schema Bumps — two independently-released writers share this store, and the rollout has to account for both.

That older copy knows nothing of versioning, so a credit it adds carries no version field and this copy will not consume it until the next `migrate`. The store is not corrupted and nothing is lost — the record is preserved verbatim and reads normally once stamped — but the two writers no longer see the same inventory between an unversioned write and the next owner run. A version **2** would be worse: the older copy would keep writing v1-shaped records indefinitely. Removing the duplicate is the prerequisite, tracked in [#30](https://github.com/jbaruch/frequent-flyer-advocate/issues/30).
