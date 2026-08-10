# Store Bootstrap

One-time setup on a machine that has never run this skill. `SKILL.md` Step 1 owns when;
this file owns the commands and the branches.

## Where the stores live

Both live under `~/.claude/` so every skill shares one copy:

- **Travel credits** — `~/.claude/travel-credits/`, owned by `using-travel-credits` and
  shared with `jbaruch/jbaruch-travel-policy`. All three must point at one directory.
- **Complaint bank** — `~/.claude/complaint-bank/`

## Why the scripts refuse rather than create

Each store's path must exist as a directory, or a symlink to one. A missing path, a
dangling symlink (cloud folder not mounted), or a plain file sitting where the store
should be all fail loudly.

Keep an inventory in cloud storage and leave it unlinked on a new machine, and
auto-creating an empty one forks the data into two diverging copies. An inventory
sitting unlinked in cloud storage is indistinguishable from an absent one, which is why
neither store guesses and why nothing is created unasked.

## Check readiness

Each store's `status` subcommand owns the readiness contract — no shell `test` logic.
Both report `{"state", "store", "reason"}` and exit `0` / `3` / `4` for ready / missing /
invalid. Branch on the exit code, not the printed wording.

```bash
python3 .tessl/plugins/jbaruch/frequent-flyer-advocate/skills/frequent-flyer-advocate/scripts/credits-tracker.py status --json
python3 .tessl/plugins/jbaruch/frequent-flyer-advocate/skills/frequent-flyer-advocate/scripts/complaints-bank.py status --json
```

## Ask before creating anything

For each store reported missing or invalid, ask via `AskUserQuestion`:

> I don't see a `travel-credits` database on this machine. Do you already have one
> (e.g. synced in Google Drive / Dropbox / iCloud), or should I start fresh?
> 1. **Link an existing one** — I'll symlink it into `~/.claude/`
> 2. **Start a fresh one** at `~/.claude/travel-credits/`
> 3. **Start a fresh one at a custom path** (e.g. a cloud folder) — I'll symlink it back

Use the same wording for the complaint bank.

## Run the matching command

```bash
# 1. Link an existing database (ask for the path first):
python3 .tessl/plugins/jbaruch/frequent-flyer-advocate/skills/frequent-flyer-advocate/scripts/credits-tracker.py link --json --path "<existing-dir>"
# 2. Fresh at the default path:
python3 .tessl/plugins/jbaruch/frequent-flyer-advocate/skills/frequent-flyer-advocate/scripts/credits-tracker.py init --json --default
# 3. Fresh at a custom path, symlinked back:
python3 .tessl/plugins/jbaruch/frequent-flyer-advocate/skills/frequent-flyer-advocate/scripts/credits-tracker.py init --json --path "<dir>"
```

`complaints-bank.py` takes the same three, and `--store hotel` selects the hotel bank.

## Dangling symlink

The cloud folder is not mounted. Tell the user; never re-create the store over it.
