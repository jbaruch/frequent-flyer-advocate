---
name: using-travel-credits
description: >
  How an LLM agent reads and updates the shared travel-credits inventory at
  ~/.claude/travel-credits/ — flight credits, vouchers, upgrade certificates,
  and airline or hotel compensation, tracked for a whole family across every
  carrier and brand. Actions: check store readiness and bootstrap it; migrate
  the store to the current record shape; list credits; show what is expiring;
  match credits against a booking scenario; show compensation history; add a
  credit; mark one used; handle errors. Use whenever a question turns on what
  credits, vouchers, or certificates are on hand — before searching flights,
  when presenting an itinerary, after a booking, or when an airline grants
  compensation.
---

# Using the Travel Credits Inventory

This skill is an action router — pick the step that matches the user's intent
and execute only that step. Do not run other steps; do not parallelize.
Explicit chains:

- Steps 3-9 all touch the store — run Step 1 first when readiness is unknown
- Step 1 is both an action and a prerequisite, and the two do not chain alike:
  - Invoked directly, when readiness itself is what the user asked about, it
    reports the state and finishes. It never continues to Step 3, and never to
    "the step matching the user's intent" — that step is Step 1, and continuing
    would loop
  - Reached as a prerequisite to another step, exiting `0` continues to Step 3
    and from there to the step that needed it
- Step 1 exiting non-zero continues to Step 10, either way
- Steps 4-9 run Step 3 first: this skill owns the record shape, so it migrates
  what it is about to read or write. Step 3 is idempotent, so on a current store
  this costs one call and changes nothing
- Step 3 continues to the calling step only when its `unconsumable` count is
  zero. Records the reader cannot consume mean every later command returns a
  subset, so it continues to Step 10 and stops instead
- Step 3 invoked directly finishes there
- Step 10 chains back exactly once, to Step 2, and only for a missing store
- Step 2 re-runs Step 1 once and routes it on the original request, the same
  split as above; it never loops
- Any step reporting a failure continues to Step 10 (Handle Errors)
- Every other Step 10 branch finishes without chaining

This skill is the owner of the inventory artifact. Shape changes to the store
belong to it and to no other skill, and Step 3 is the only place a migration
runs. The record shape, the writer/reader contract, and the migration policy
are documented beside it:

```text
skills/using-travel-credits/state-schema.md
```

Never hand-edit `inventory.md`. Every write goes through the script.

Run the script as `python3 <path>`. From a consumer repo:

```text
.tessl/plugins/jbaruch/frequent-flyer-advocate/skills/frequent-flyer-advocate/scripts/credits-tracker.py
```

Always pass `--json`. Every subcommand then emits exactly one object on stdout
and nothing else — bootstrap narration and warnings go to stderr. Read fields
off that object; never scrape the prose rendering, which exists for the
interactive human path. A failure emits an object too, carrying an `error` code,
so branch on that field rather than on stderr text.

Argument contract, filter flags, accepted types, and the full output shape are
the script's — `--help` on the script and on each subcommand.

## Step 1 — Check Store Readiness

`~/.claude/travel-credits/` must exist as a directory, or a symlink to one. The
script refuses to run rather than create a second inventory beside the real one.

```bash
python3 .tessl/plugins/jbaruch/frequent-flyer-advocate/skills/frequent-flyer-advocate/scripts/credits-tracker.py status --json
```

Branch on the exit code, never on the printed wording:

- `0` — store ready. Invoked directly, report that and finish here. Reached as a
  prerequisite, continue to Step 3 and from there to the step that needed it
- `3` — no store. Continue to Step 10, then return here
- `4` — store unusable. Continue to Step 10; do not bootstrap over it

## Step 2 — Bootstrap the Store

Reached from Step 10 after an exit `3`, with the user's choice already made.
Never pick the choice for them.

```bash
python3 .tessl/plugins/jbaruch/frequent-flyer-advocate/skills/frequent-flyer-advocate/scripts/credits-tracker.py link --json --path "<dir>"
```

`link` adopts an inventory that already exists, including one in cloud storage.
`init --default` creates a fresh store at the default path. `init --path "<dir>"`
creates one elsewhere and symlinks it back.

Re-run Step 1 once, and route on the original request. Exit `0` reports the
store ready and finishes when readiness itself is what the user asked about;
otherwise it continues to Step 3 and from there to the step that needed the
store. Anything else continues to Step 10 and does not return — a bootstrap that
did not take is reported, never retried.

## Step 3 — Migrate the Store

Needs a ready store: run Step 1 first if readiness is unknown.

```bash
python3 .tessl/plugins/jbaruch/frequent-flyer-advocate/skills/frequent-flyer-advocate/scripts/credits-tracker.py migrate --json
```

This skill owns the record shape, so it upgrades records before consuming them
rather than reading them at a version it has moved past. That is why Steps 4-9
run this first — the owner migrates what it is about to read or write.

Other skills write to this store through the same script, and their writes
deliberately leave records they did not author untouched. A non-owner must not
migrate, so records they wrote at an older version stay as they are until this
runs.

Idempotent: a store already current reports `changed: false` and is not
rewritten, so running it ahead of every read costs one call.

Then gate on `unconsumable`. It is the count of records in the store that the
reader cannot consume after migration, measured by asking the parser rather than
by tallying what migration did — so it holds whatever the cause. `skipped_newer`
and `unreadable` explain it: records written by a newer version of the plugin,
and records whose version line does not parse. Migration leaves both alone by
design, and every later command omits them.

- `unconsumable` zero — the store is wholly readable. Reached from Steps 4-9,
  continue to the step that called it. Invoked directly, report the counts and
  finish here
- `unconsumable` above zero — continue to Step 10 and stop. Do not fall through to
  the calling step: `list`, `expiring`, and `check` would return a subset while
  reading as the whole store, and `add` would write against an inventory it
  cannot fully see

## Step 4 — List Credits

Run Step 3 first — the owner migrates the store before it reads or writes it.

```bash
python3 .tessl/plugins/jbaruch/frequent-flyer-advocate/skills/frequent-flyer-advocate/scripts/credits-tracker.py list --json
```

Lists active credits only. Narrow with `--passenger`, `--airline`, or `--brand`.
Report the `credits` entries as given, and say which filters were applied — the
payload echoes them, so a narrowed list is never presented as the whole store.
Finish here.

## Step 5 — Show What Is Expiring

Run Step 3 first — the owner migrates the store before it reads or writes it.

```bash
python3 .tessl/plugins/jbaruch/frequent-flyer-advocate/skills/frequent-flyer-advocate/scripts/credits-tracker.py expiring --json
```

The window is the script's default, echoed back as `window_days`. Pass `--days`
only when the user names a different horizon.

Run without a passenger filter before a flight search. Surface each `expiring`
entry with its deadline. The `no_expiry` entries are a separate list and are not
a deadline — never fold them into the urgent set. Finish here.

## Step 6 — Match Credits to a Booking Scenario

Run Step 3 first — the owner migrates the store before it reads or writes it.

```bash
python3 .tessl/plugins/jbaruch/frequent-flyer-advocate/skills/frequent-flyer-advocate/scripts/credits-tracker.py check --json --scenario "<itinerary description>" --passengers "Name One,Name Two"
```

`--passengers` is one comma-separated string, not repeated flags and not
space-separated.

Describe the itinerary in the scenario string — airlines, routing, cabin, hotel
brand. Which credits match, and why, is the script's judgment: quote each
match's `reasons` rather than re-deriving them. `other_passenger_matches` holds
credits belonging to family members not on the trip — present it as its own
callout, never merged into `matches`. Finish here.

## Step 7 — Show Compensation History

Run Step 3 first — the owner migrates the store before it reads or writes it.

```bash
python3 .tessl/plugins/jbaruch/frequent-flyer-advocate/skills/frequent-flyer-advocate/scripts/credits-tracker.py history --json
```

Deposited compensation: miles and points an airline or hotel granted for a
service failure. Narrow with `--airline`, `--brand`, or `--passenger`.

These are events, not inventory. The balance landed in the loyalty account when
it was granted, and once there it is fungible with the rest — the loyalty
program owns that number and this store cannot shadow it. So a deposit never
appears in Steps 4-6, never counts toward available value, and has no used
state.

What it is for: prior-compensation context during a complaint intake, and the
record that lets a repeat-failure claim be stated as fact rather than
impression. Report the `deposits` entries with their issuer, date, and amount.
Finish here.

## Step 8 — Add a Credit

Run Step 3 first — the owner migrates the store before it reads or writes it.

`--type`, `--description`, and `--value` are the required flags:

```bash
python3 .tessl/plugins/jbaruch/frequent-flyer-advocate/skills/frequent-flyer-advocate/scripts/credits-tracker.py add --json --type <TYPE> --description "..." --value <amount>
```

Everything else is optional — add `--passenger`, `--expiry`, `--airline`,
`--brand`, `--restrictions`, `--confirmation` when the fact is known. Never
block on a missing optional; a transferable gift card has no passenger and a
goodwill deposit has no expiry.

`--airline` and `--brand` are independent dimensions, not alternatives. A
co-branded credit carries both, and each matches its own scenario type in
Step 6.

Confirm the type against `--help` before writing. The type is recorded verbatim
and drives Step 6's matching; never infer one from an abbreviation's plain
reading.

An airline's offer is not a credit until it exists. Add it when the user
confirms they hold it, never from an alert or a promise. Finish here.

## Step 9 — Mark a Credit Used

Run Step 3 first — the owner migrates the store before it reads or writes it.

```bash
python3 .tessl/plugins/jbaruch/frequent-flyer-advocate/skills/frequent-flyer-advocate/scripts/credits-tracker.py use --json --id <N> --note "<what it was applied to>"
```

Marking used moves the row to the archive with a used date. The record survives
in full — description, case number, issuer, amount — and stays readable as
prior-compensation history.

There is no reverse subcommand. Confirm with the user that the credit was
actually applied before writing; a wrong `use` is undone by hand-editing the
store, which this skill forbids. Finish here.

## Step 10 — Handle Errors

- Exit `3`, no store at the path. Ask the user which bootstrap they want, then
  continue to Step 2 to run it. Never create one unasked: a store may exist
  unlinked in cloud storage, and `init` beside it forks the data.
- Exit `4`, store unusable. The path is a plain file, or a dangling symlink
  whose target is not mounted. A dangling symlink is never recreated
  automatically. Report it and stop — remounting or re-linking the real store
  is the user's action, not this skill's.
- Step 3 reported `skipped_newer` above zero. Records were written by a newer
  version of this plugin than the one installed. Report the count, say the
  plugin needs updating to read them, and stop. Step 3 does not fix this and
  neither does re-running it.
- Step 3 reported `unreadable` above zero. A record's version line does not
  parse as an integer, which means the store was hand-edited or truncated.
  Report the count and stop. Repairing it is the user's action — this skill
  will not guess at the intended version.
- Step 3 reported `unconsumable` above zero with both other counts at zero.
  Records exist that the reader drops for a reason migration did not classify.
  Report the count and stop; do not present the partial inventory as the store.
- Unknown `--id` on Step 9. The id is not in the active section. The usual cause
  is a credit already marked used, which now sits in the archive under that same
  id — ids are stable and a record keeps its own through archiving. Report that
  and stop; listing the active credits is Step 4, on the user's next ask.
- Rejected `--type` on Step 8. Read `--help` for the accepted set. Never guess
  from an abbreviation's plain-English reading.

Report the reason in one line. Every branch except exit `3` finishes here.
