# Changelog

### Added

- Miles and points compensation is recorded as **history**, not inventory. A new `## Compensation History (Deposited)` section, `MILES` and `POINTS` types, and a `history` subcommand. Resolves #23.
  - An eCredit has a lifecycle — issued, held, applied, used — and `credits-tracker.py` modelled that correctly. A deposit has none: when an airline grants 25,000 SkyMiles the balance is in the loyalty account at the moment of the grant. Logged as a credit it entered Active, nothing ever moved it out because no application event exists, and `use` was the only exit — so clearing the list meant recording a fiction. Asked whether specific goodwill miles had been redeemed, the account holder's answer was *"I can't tell the goodwill miles from other miles."* Correct, and not fixable by better record-keeping: once deposited they are fungible with the rest of the balance, and the loyalty program owns that number.
  - A deposit never appears in `list`, `check`, or `expiring`, never counts toward the monetary total, and has no `use` transition — `use` on one exits non-zero with `deposit_has_no_use_transition` rather than archiving a redemption that never happened. `add --type MILES --expiry` is refused too: a deadline here would be one this file cannot enforce and `expiring` would report it as actionable.
  - What it is for is the other half. The archive is what `frequent-flyer-advocate` reads for prior-compensation context during intake, and what `complaint-patterns` needs to state "third hardware failure in eight months" as fact rather than editorializing. That value is in the event — airline, date, case number, amount granted — and an event needs no used/unused state.
  - Classification is anchored at both ends: the whole `Value` must BE an amount of miles or points, not merely contain one. Unanchored, `5000 miles voucher` and `1 certificate for 5000 miles travel` both matched and would have been pulled out of available inventory — the false-positive direction, which costs a usable credit rather than leaving a visible one in the wrong place.
  - Relocation touches only records this reader brought to `SCHEMA_VERSION`. Stamping already leaves a newer or unparseable record alone, but the relocation pass ran over every active record afterwards — so a version-99 miles row would have been moved and retyped by a reader with no idea what shape it is in, and a move is a rewrite. Migration Policy keeps those untouched.
  - Relocation is by `Value`, whatever type a record currently carries. Keying on `COMP` alone stranded every deposit logged under another type, and the skill's only worked example was `--type VOUCHER` — so those records exist in the wild.
  - Classification accepts any number of program-name words between the amount and the unit, and a unit fused into a program word: `30,000 Hilton Honors points`, `8,000 SkyMiles`, `25,000 American AAdvantage miles`. The first cut allowed one intervening word, so the Hilton row — taken straight from the live store — would have stayed in Active. The fixture happened to use a one-word variant and passed anyway, which is a test written against the implementation rather than the requirement; the replacement asserts every shape the store uses. The pattern stays conservative because the error is asymmetric: a miss leaves a deposit visible in Active, a false positive removes a genuine credit from the available set.
  - `SCHEMA_VERSION` 2 migrates existing rows. A record is relocated when its `Value` names miles or points; anything else stays put, so a companion certificate valued `1 certificate` is untouched while `25000 miles` and `30,000 Honors points` move. Classification reads the `Value` field only — a description mentioning miles is prose, and `script-delegation`'s Regex Trap allows a fully-enumerable pattern, which two unit words are. The record's block moves verbatim apart from its type token, so fields the current formatter does not know survive the move.
  - The v2 bump was gated on #30 and #30 is closed. `jbaruch/jbaruch-travel-policy` shipped a byte-identical copy of this script and wrote the same store; its 0.7.43 deletes that copy and routes everything through this skill. With one writer there is no skew window for `stateful-artifacts` Cross-Pipeline Schema Bumps to sequence — which is exactly why the de-duplication was the stated prerequisite rather than a nicety. `state-schema.md` said otherwise until this change; the doc was stale, not the constraint.
  - `SECTION_MARKERS` replaces the two-section `if active / else archive` branching that ran through `parse_credits`, `insert_credit`, and `remove_credit`. That form defaulted anything that was not `"active"` to the archive, so a mistyped section name silently read or wrote the wrong one; `section_markers()` raises instead. A store written before the section existed gains it on the next `migrate`.

### Fixed

- The test suite reads `SCHEMA_VERSION` from the script instead of hardcoding `1`. Seventeen assertions pinned the literal, so the v2 bump turned ten of them red for the version rather than for the behaviour — a suite that has to be rewritten on every bump is one that stops guarding the bump.
- `migrate`'s `unconsumable` count covers every section. It summed only active and archive, so the records it had just relocated read as unconsumable and Step 3 stopped the router on a store that was entirely fine.
- `SKILL.md` Step 4 reads through `Skill(skill: "using-travel-credits")` rather than calling the tracker directly, and reads compensation history alongside the active list.
  - The direct call was a non-owner read. After the v2 bump, records the store has not yet been migrated to the current shape are skipped by `parse_credits()`, so the call returns `count: 0` — and `0` at this step is recorded as evidence that the airline has never compensated this passenger. A wrong answer indistinguishable from the true one, in the step whose whole job is finding leverage. The owner skill migrates before it reads and gates on unconsumable records, so a count from it means what it says.
  - Two separate invocations, one per action. `using-travel-credits` is an action router whose preamble says to execute only the selected step, so directing one invocation to run two of its steps is a chain it does not declare.
  - `state-schema.md`'s writer/reader contract was rewritten to match: `frequent-flyer-advocate` is a writer and a caller, not a direct reader, and the rule behind that is stated once — a direct `parse_credits()` read by a non-owner reports an empty view indistinguishable from an empty store. A direct `add` stays, since writing a new record parses nothing.
  - Reading history alongside the list matters for the same reason: Deposits leave Active in v2, so the prior-compensation check would have reported "no prior compensation" for a passenger the airline had already paid off twice — the opposite of the truth, and the strongest leverage the letter has thrown away. The writer/reader contract in `state-schema.md` named this skill a reader; it now reads what it is contracted to.
- Two test fixtures pinned future expiry dates (`2027-01-31`, `2027-01-01`), which `testing-standards` Determinism forbids outside the Live-Upstream carve-out. Replaced with fixed past dates; neither assertion depended on the date being ahead of the clock.
- `SKILL.md` Step 11 says which `--type` a grant takes and warns that `COMP` is a Companion Certificate rather than a type for compensation generally. Nothing told an agent which type to use for a miles grant, and the only worked example was `--type VOUCHER` — so the choice was left to inference from an abbreviation that reads like the wrong word. The rename that removes the ambiguity at its source is #24.

## 0.9.31 — 2026-08-10

### Added

- `skills/using-travel-credits` — an action router over the shared inventory at `~/.claude/travel-credits/`, giving the store one documented invocation surface. Nine steps: readiness, bootstrap, migrate, list, expiring, scenario matching, add, use, errors. Refs #30.
  - The store has two writers. `jbaruch/jbaruch-travel-policy` ships a byte-identical 1148-line copy of `credits-tracker.py` and writes the same file, which `stateful-artifacts` forbids — no single owner for shape changes — and which is why #23 and #24 have nowhere safe to land. That consumer cannot simply call this plugin's script either: `skill-authoring` Script References bars mixing a plugin-mount path among repo-relative ones inside one SKILL.md, and its skill invokes six of its own scripts as `scripts/…`. A typed `Skill(skill: "using-travel-credits")` call is the shape it already uses to reach `using-tripit` and `using-fifty-tabs`. This lands the target; deleting the copy is the follow-up in that repo.
  - Every invocation passes `--json`. 0.9.27 gave the script a JSON contract precisely because "every consumer was parsing tables", and a new consumer reading the prose rendering would have walked straight back into it. The router reads `credits`, `expiring` / `no_expiry`, and each match's `reasons` off the payload, and branches on the `error` field rather than stderr text.
  - Step 9 keeps `missing` and `invalid` distinct. A dangling symlink to an unmounted cloud store looks absent to a naive check, and running `init` beside it forks the data irrecoverably — so the script refuses rather than guesses, and the skill never bootstraps over an `invalid`.
  - `--airline` and `--brand` are documented as independent dimensions rather than alternatives, matching the behavior 0.9.12 landed. The router marks only `--type`, `--description`, and `--value` required, so an agent does not block on a passenger for a transferable gift card or an expiry for a goodwill deposit.
  - `credits-tracker.py` stays at `skills/frequent-flyer-advocate/scripts/`. Moving it under the new skill would split a test file parametrized across both trackers and rewire nine call sites in a published skill, for no gain to the consumer — the router names it at its mount path either way. A decision, not an oversight.
- Step 1 is routed as two things, because it is two things. Asked directly — "is the credits store set up?" — it reports readiness and finishes. Reached as a prerequisite, it continues through Step 3 to the step that needed the store. The single "continue to the step matching the user's intent" rule looped when readiness itself was the intent, since that step is Step 1. Step 2 routes its post-bootstrap re-run on the same split.
- `skills/using-travel-credits/state-schema.md` documents the artifact `stateful-artifacts` requires a schema for: location, file structure, record shape, the writer/reader contract naming all three consuming skills, the three bootstrap states, and the migration policy. The skill claims ownership explicitly; the script is the implementation the owner writes through, not the owner.
- Three tests pin the router's contract — every invocation uses the plugin-mount path, every invocation passes `--json`, and the skill does not restate the script's `--type` vocabulary (`script-as-black-box`; a copied list drifts, and a stale one is how an agent picks a type the script rejects).

### Fixed

- `credits-tracker.py` stamps a `schema_version` on every record, which `stateful-artifacts` Required Attributes demands and no record carried. Closes #26.
  - Migration is an explicit owner-only operation — a new `migrate` subcommand, reached from the router's Step 3. `write_inventory()` does not migrate. Every skill that logs compensation calls this script directly, so migrating on the write path would run a migration under a non-owner writer, which Migration Policy reserves to the owner skill. A non-owner's `add` or `use` stamps only the record it is itself authoring and leaves everyone else's at whatever version they carry.
  - `migrate` walks the store: absent is stamped, older is stepped up through `upgrade_record_body()` and restamped, equal is untouched, newer is left alone. It reports counts per bucket, and is idempotent — a store already current is left byte-identical and reports `changed: false`. `upgrade_record_body()` is identity at version 1 by construction — nothing shipped predates it — and exists so the next bump adds a branch instead of building the migration machinery under pressure.
  - The owner migrates what it is about to consume: the router runs Step 3 ahead of every store-touching step (4-8), not only when a user asks to migrate. Migration Policy has the owner detect an older record on read, upgrade it, and rewrite it — so an older record does not survive an owner read, only a non-owner one. Affordable because `migrate` is idempotent: a current store reports `changed: false` and is not rewritten.
  - Migration works on whole records rather than line by line. It decided "unversioned" from the line immediately after the heading, while the parser accepts the field anywhere in a record and keeps the last occurrence — so a record whose version field sat below another field got a *second* one spliced in, making its version depend on read order. `migrate_record()` now locates the field anywhere the parser would, emits exactly one, and collapses duplicates it finds.
  - `upgrade_record_body()`'s return value is used. The upgrade loop called it and discarded the result, so a `SCHEMA_VERSION > 1` rollout would have bumped every record's version line over an untransformed body — silent corruption inside the mechanism that exists to make shape changes auditable. It now receives the record's field lines and its output replaces them, pinned by a test that fakes a v2 upgrade and fails if the transform is dropped.
  - Migration recognizes a field line exactly as `parse_credits()` does. It anchored on column zero while the parser strips first, so an indented version line was invisible to migration and visible to the parser: `migrate` spliced in a second version line, reported the store wholly readable, and every later command then dropped the record — a partial inventory presented as the whole one, through the very gate that exists to prevent it. Reproduced before fixing: an indented `Schema version: 99` gave `skipped_newer: 0` from `migrate` and `count: 0` from `list`. Indentation is preserved on re-emit. `next_id()` carried the same column-zero assumption and would have reissued an indented record's id.
  - `migrate` reports `unconsumable`, measured by asking the parser how many records it can actually consume rather than by tallying what migration did. That is the field Step 3 gates on, so a future divergence between the two line-matchers surfaces as a non-zero count instead of as a silently partial inventory. `skipped_newer` and `unreadable` stay as the explanation.
  - Step 3 gates the chain rather than falling through. Records the script cannot consume are omitted from every later command, so continuing would have let `list`, `expiring`, and `check` return a subset that reads as the whole store, and `add` write against an inventory it cannot fully see. A non-zero count stops at Step 9 instead. `unreadable` — a version line that does not parse — had no branch at all before; it is now counted, reported, and never guessed at.
  - `migrate` leaves an already-current version line verbatim instead of re-rendering it canonically. Canonicalizing meant a record whose line differed only in spacing reported `changed: true` and rewrote the store — and since the owner now runs `migrate` ahead of every read, a cosmetic diff would have rewritten it on every one. Idempotence is the property the pre-read chain depends on, so it is pinned by a test.
  - A passive default was not enough. An earlier cut relied on `format_credit()` defaulting the version on "the next rewrite", which never happens: `cmd_add` splices markdown rather than reserializing, so pre-existing records are never re-emitted. A store written before versioning stays unversioned until `migrate` runs, and the tests pin that split — a non-owner write stamps one record, `migrate` stamps the rest.
  - The stamp is a text-level insert after each `### #` heading, deliberately. `format_credit()` writes only the ten fields it knows, so a parse-and-reformat round-trip would silently drop anything else and rewrite untouched records — a migration that risks more than it repairs.
  - `parse_credits()` consumes only records at `SCHEMA_VERSION` exactly, omitting anything off-version in either direction with a per-record stderr warning that names the recovery. Migration Policy has a reader treat an off-version record as read-only "no usable prior state", and every caller other than the owner skill is a non-owner reader — an older record means the owner has not upgraded it yet, and reading it anyway is the non-owner migration the policy forbids. A record carrying no version field is declined on the same grounds — Required Attributes puts a `schema_version` on every record, and without one a reader cannot know the shape it is holding. `migrate` stamps it and it reads normally afterwards, and the router runs `migrate` ahead of every read, so a pre-versioning store heals on first use. One consequence worth stating plainly: until the duplicate copy in `jbaruch/jbaruch-travel-policy` is removed (#30), a credit added there carries no version field and this copy will not consume it until the next `migrate`. Nothing is lost — the record is preserved verbatim — but the two writers do not see the same inventory in between, which is one more reason #30 is the prerequisite it is described as. `next_id()` scans raw `### #` headings rather than that filtered view, so an id is never allocated over a record the script cannot see.
  - Fixed a stale `SCHEMA_VERSION` comment claiming records are stamped "on the next rewrite", which stopped being true when migration moved off the write path, and a Step 9 line claiming ids are unstable once archived. They are stable: `use` moves a record into the archive keeping its own id, so an unknown `--id` means the credit is archived, not renumbered.
  - A version **2** stays gated on de-duplication (#30). Version 1 is safe with two writers because both parsers preserve records they did not write and ignore fields they do not know. That property does not survive a shape change.

## 0.9.30 — 2026-08-10

### Changed

- CI pins bumped: `actions/checkout` 5.1.0 → 7.0.1, `actions/setup-python` 6.3.0 → 7.0.0, and pyright 1.1.408 → 1.1.411 in the hash-locked `.github/requirements.txt`. Supersedes the three separate Dependabot PRs (#33, #34, #35) — the two action bumps edit adjacent lines of `tests.yml` and would have conflicted with each other, and merging three bare bumps would have published three versions with no CHANGELOG entry apiece, reopening the gap #32 just closed.
  - Verified locally rather than assumed: the gate runs clean under pyright 1.1.411 at 0 errors, 0 warnings, 0 informations. A diagnostics-engine bump is the one dependency bump that can turn a green tree red on its own.
  - `.github/scripts/lock-requirements.py` moves to 1.1.411 alongside the lock it generates. Dependabot edits the lock only, so the generator's `TOP_LEVEL` still said 1.1.408 — and `file-hygiene` admits the lock as a platform-required generated artifact on the condition that source and generated form stay reproducible together. Running the documented no-argument regeneration would have silently reverted the bump.
  - `test_generator_top_level_matches_the_committed_lock` fails when the two drift, so the next Dependabot lock bump cannot land half-applied. Confirmed it actually catches the drift rather than passing vacuously: reverting `TOP_LEVEL` to 1.1.408 turns it red.

## 0.9.29 — 2026-08-10

### Fixed

- `publish.yml` passes `stamp-changelog: true`, so the pipeline writes the `## <version> — <date>` heading instead of leaving it to the author. Resolves #32. The input defaults to `false` in the reusable workflow, so the stamp step's `if: inputs.stamp-changelog` never fired and `context-artifacts` CHANGELOG Hygiene put the heading on the author — with nothing failing when one was skipped. `tesslio/patch-version-publish` bumped the manifest and published regardless, thirteen times.
  - An author-written heading was always a guess. The version a PR will publish as is not knowable while the PR sits in review: PR #25 carried `## 0.9.26` until 0.9.26 was taken out from under it. The stamp step reads the version the publish step is about to assign, so the heading cannot be stale.
  - Authors now add un-headed `### ` blocks at the top of `CHANGELOG.md` and the pipeline heads them. This entry is written in that shape.
- **Backfilled 0.9.14 through 0.9.26** — thirteen published versions with no CHANGELOG entry. `CHANGELOG.md` jumped from `## 0.9.27` straight to `## 0.9.13` while all thirteen bump commits sat on `main`. Reconstructed from the merge commits and their diffs; each heading carries the date of that version's `Bump …` commit, which is the date it actually published. The stretch is almost entirely CI and reviewer-architecture churn — four successive reviewer migrations (gh-aw upgrade → Codex CLI → central fleet reviewer → PR-time trigger), the move to the canonical reusable publish workflow, and a gitignore change that landed, reverted, and re-landed across 0.9.23–0.9.25.
  - `context-writing-style` names CHANGELOG as where rule rationale and incident detail live, so the gap cost more than tidiness — rules point at an archive that was lying about what shipped.

## 0.9.28 — 2026-08-10

### Added

- `scripts/letter-fit.py` + `scripts/airline-form-metadata.json`: the skill now measures a form-mode letter against the airline's actual submission-form limit instead of trusting its own arithmetic. Resolves #4 (channel awareness) and #5 (the script that makes the metadata operational) together — the metadata without the script is documentation, and the script without the intake questions has nothing to measure against.
  - The failure that prompted both issues: a Southwest draft measured 2472 by Python `len()`, was declared under the 2500-char form limit, and the live form's counter came back **2798**. No encoding of that text reproduces 2798 — UTF-8 bytes 2482, CRLF 2495, HTML entities 2500 — so the form counts by a method nobody has identified.
  - `letter-fit.py` counts under all four encodings. When the channel's `counting_method` names one, that count is authoritative. When it is `unknown`, the worst count is multiplied by an inflation factor and the verdict is labeled unverified. WN carries `observed_inflation: 1.14`, derived from that 2798/2472 ratio; `UNKNOWN_INFLATION_DEFAULT = 1.15` covers forms nobody has measured. Replayed against the original draft the script judges it at 2820 vs the form's 2798 and exits 1 — the draft that shipped would have been stopped.
  - Formatting warnings flag markdown bold, headings, bullets, blockquotes, links, and unicode bullets against the channel's `formatting` map. Only an explicit `true` stays silent; a `false` or `"unknown"` entry warns, so an unrecorded field is a reason to check rather than permission to assume.
  - Exit codes: 0 fits, 1 overflows, 2 argument or metadata error. `--limit N` measures an airline the metadata does not record; `--info` and `--list-airlines` need no letter; `--metadata` points at another copy of the data file.
  - `observed_inflation` must be finite and at least 1. `json.loads` accepts `NaN` and `Infinity`, and a factor below 1 shrinks the worst count instead of padding it: `observed_inflation: -1` turned a 200-char letter into an effective `-200` against a 100 limit and reported FITS — an overflow-passes-as-fits hole inside the very margin that exists to prevent one. `1.0` stays legal, meaning "no padding needed".
  - `load_metadata` validates shape, not just syntax. `[]` is valid JSON and reached `md.get(...)` as a traceback with stdout empty — breaking the structured-failure contract in the one place a caller most needs it. `validate_metadata` checks the root object, `airlines`, each airline, each channel, and the types of `char_limit`, `formatting`, and `observed_inflation`, reporting `metadata_invalid_shape` with an `at` path to the offending node. `bool` is rejected for `char_limit` explicitly, since it subclasses `int` and would otherwise read as a limit of 1. Unknown keys still pass, so the metadata can grow without a schema bump.
  - Failures are structured too: stdout carries `{"error": <code>, "message": <text>}` plus context (`path`, `known`, `given`, `usage`), so a caller branches on a field instead of scraping stderr. A `JsonArgumentParser` subclass routes argparse's own bad-flag exit through the same path, which was the one hole left in the contract. `--help` stays human text at exit 0 and is documented as the sole exception. Matches the error-payload shape `credits-tracker.py --json` established in 0.9.27.
  - stdout is JSON in every mode, per `script-delegation` "JSON-producing: output structured data, not prose". The script measures and the skill renders; `worst_count` and `effective_count` ship precomputed so no caller does arithmetic the script exists to prevent.
  - Seed metadata covers AA (1500-char web form, five prefilled fields, executive-office paper address) and WN (2500-char web form). Both limits carry `limit_verified` + `limit_source`; nothing is recorded that was not observed.
- `tests/test_letter_fit.py`: 50 outcome-focused cases covering counting, the inflation margin, the `--limit` override, formatting detection, input handling, error paths, and a provenance check over the shipped metadata. The reported Southwest draft is pinned as a regression test. Deterministic — fixed inputs, no clock, no network.

### Changed

- `complaints-bank.py` takes `--json` on every subcommand — `init`, `link`, `status`, `file`, `check`, `resolve`, `pending`, `list` — across both the airline and hotel stores, mirroring what 0.9.27 did for `credits-tracker.py`. `script-delegation` Script Requirements makes a skill-invoked deterministic script JSON-producing, and this one emitted tables the skill was parsing at four call sites. Prose stays the default so every existing invocation is byte-unchanged; the flag rides a parent parser so `<cmd> --json` works uniformly.
  - Failures are structured too — `invalid_category`, `invalid_severity`, `invalid_resolution`, `not_found`, `missing_required_args`, `interactive_required`, `bank_not_initialized` — and a `SystemExit`/`Exception` guard in `main()` guarantees stdout holds one object even on a path that exits before emitting. An empty stdout reads as a crash, not a result.
  - `check` returns the groupings it already computed rather than making the reader recount: `category_patterns` and `secondary_patterns` hold the 2+ groups, alongside `resolutions`, `denied_count`, and `recurring`. The pattern threshold now lives in one place instead of in the renderer and the reader separately.
  - `_resolve_status()` splits readiness resolution from its rendering, so prose and JSON exit `0`/`3`/`4` from one contract instead of two copies of the branching.
- `.gitattributes` marks `.github/requirements.txt` as `linguist-generated=true merge=ours`, the marking `file-hygiene` requires for a generated artifact committed under its exception. The lock is machine-written, so a merge takes our side and it is regenerated rather than reconciled line by line.

- CI gains a deterministic gate, announced as this PR's CI scope per `ci-safety`. `.github/scripts/pre-publish-gate.sh` runs pyright at zero findings and then all three suites (117 cases). Two workflows call the same script so pre-merge and pre-publish cannot drift: the new `.github/workflows/tests.yml` on every pull request, and `.github/workflows/publish.yml` after merge via the reusable workflow's `pre-publish-script` / `python-version` inputs. Resolves #10 in full.
  - A gate that ran only post-merge would not have satisfied `commit-conventions` "CI must pass before merging" — the publish-side run guards the registry, the PR-side run guards the merge.
  - `pyrightconfig.json` scopes the engine to `scripts/` and `tests/` at Python 3.12. `language-diagnostics` makes gate adoption its own focused change only on a dirty tree; this one reported zero findings across the repo before the gate existed, so it lands green with no fixes attached.
  - `.github/scripts/lock-requirements.py` ships with `test_lock_requirements.py` (18 cases) and is covered by the pyright gate — `pyrightconfig.json` now includes `.github/scripts`, not just the skill's own tree. pip and PyPI are injected as `resolver` / `fetch` parameters, so the suite is deterministic with no network. Every failure path is exercised: pip resolution failure, a pip too old for `--report`, missing `curl`, a curl non-zero exit, non-JSON from PyPI, a release with no wheel or sdist, and a mid-run failure leaving the committed lock byte-identical.
  - The generator raises `LockError` with an actionable message instead of letting `CalledProcessError` or `JSONDecodeError` escape as a traceback, and resolves everything before rendering, so a network blip cannot truncate the committed lock into something that fails `--require-hashes` on every later run.
  - `.github/requirements.txt` is a fully resolved, hash-pinned lock — pyright plus its transitive tree — generated by `.github/scripts/lock-requirements.py` from pip's own resolution and PyPI's published digests. The gate installs with `--require-hashes`, so an artifact not in the lock aborts the run. Pinning the top-level package alone left the transitive tree floating, which `dependency-management` Pinning does not accept. Dependabot's new `pip` ecosystem renews it on the same weekly cadence as the action refs. The install runs unconditionally and the engine is invoked as `python3 -m pyright`, so the pinned build executes even where another pyright sits earlier on PATH. It ships from PyPI, so CI needs no Node toolchain.
  - `tests.yml` pins `actions/checkout` and `actions/setup-python` to full commit SHAs with the major tag in a trailing comment; the existing `github-actions` Dependabot ecosystem renews them.

- `SKILL.md` Phase 1 asks for the submission channel alongside the other always-gather items, and for a web form follows up on the character limit and the fields the form captures itself. Asking after a draft exists is what forced the two compression passes in the June 2026 AA case: one to strip data the form already had, one to fit 1500 chars from ~5500.
- `SKILL.md` Phase 4 adds the form-fit variant and makes `letter-fit.py` a gate — an overflowing draft is never presented, and the user sees the script's output rather than the agent's count.
- `SKILL.md` Step 8 routes on all four recorded channels. The first cut branched on email/paper vs everything else, so an `undecided` channel fell into the form-mode branch and reached Step 9's fit measurement with no form to measure against. Undecided now resolves before the letter is built, and declining to choose yields the long form plus a note that a web form needs a fitted variant.
- `SKILL.md` no longer tells the agent to stand up a user-owned copy of the metadata to accumulate limits in. That copy was written and read across invocations, making it a stateful artifact under `stateful-artifacts` with no owner skill, schema doc, `schema_version`, or migration path. Since a live limit now supersedes the recorded one on every run, the copy bought nothing that `--limit` does not. Evidence still routes upstream, where it ships to everyone.
- `SKILL.md` treats a limit the user reads off the live form as authoritative over the recorded one, for every airline rather than only unrecorded ones. The earlier wording passed `--limit` only when the metadata had no entry, so a recorded airline whose form had since tightened would be measured against the stale ceiling and an oversized letter could pass.
- `SKILL.md` Step 10 reads `metadata.channel_notes` rather than restating any airline's routing. An earlier draft spelled out AA's dead executive-email channel inline, which `script-as-black-box` forbids: the copy keeps directing agents by stale policy once the metadata moves on. A test now fails if a shipped airline's name or char limit appears in SKILL.md.
- `SKILL.md` Phase 5 reads recorded channel reliability from the metadata before recommending where to send. AA's executive customer-relations email is recorded as routing to unmonitored mailboxes since January 2026; the guidance is web form first, paper mail to the executive office for escalation.
- `SKILL.md` replaces every `<this-skill-dir>` placeholder with the plugin-mount path `.tessl/plugins/jbaruch/frequent-flyer-advocate/skills/frequent-flyer-advocate`. `skill-authoring` Script References wants the path that resolves at the invocation site, and a consumer copying a placeholder as written gets a broken command. All 18 invocations moved together — converting only the new letter-fit calls would have mixed conventions inside one file, which the same rule forbids. A test now pins it.
- `SKILL.md` converts from `## Phase N:` prose headings to the flat `## Step N — Title` structure `skill-authoring` prescribes, with the execution-mode preamble and an explicit continuation on every step. Eleven steps, one action each; the letter's own anatomy stays unnumbered so it does not read as sub-steps. Step 9 (form-fit verification) states its skip condition explicitly for email and paper mail. Phase 3's blanket "always parallelize" is scoped to concurrency among the 8 research items inside their own step, which no longer reads as license to run steps out of order.
- `rules/letter-quality.md`, `rules/escalation-output.md`, and `rules/complaint-patterns.md` gain the `applyTo:` scope `rule-frontmatter` requires on a conditional rule. All three carried `alwaysApply: false` with scope prose in `description:` alone, which that rule names explicitly as not a substitute — agents could fail to load them in the contexts they govern.
- `rules/letter-quality.md` splits into per-mode sections. The form's own fields may cover passenger name, loyalty number, flight number, date, route, and gates; loyalty **tier** in the opening sentence, the FlightAware timestamp, the verbatim quote, and the 14–21 day deadline all survive compression. Counting a form-mode letter by hand is now forbidden outright.

### Notes

- Both scripts are FFA-only — no mirror exists in `jbaruch/jbaruch-travel-policy`, so no byte-identical-sync ceremony applies.
- `counting_method` stays `"unknown"` for AA and WN. Resolving it needs someone to paste a known-length string into each live form and read the counter back; the inflation margin is the stand-in until then. A verified method makes the margin disappear on its own — the script uses the named counter and drops the multiplier.

## 0.9.27 — 2026-08-09

### Added

- `credits-tracker.py` takes `--json` on every subcommand, emitting one JSON object on stdout in place of the prose rendering. `coding-policy: script-delegation` Script Requirements makes a skill-invoked deterministic script JSON-producing, and this one emitted prose from 69 `print` calls across 9 commands — every consumer was parsing tables. Prose stays the default so the interactive human path and the existing `frequent-flyer-advocate` call sites are unchanged; the flag is inherited through a parent parser so `<cmd> --json` works uniformly rather than only before the subcommand.
- Error paths emit structured payloads too — `{"error": "invalid_type", …}`, `{"error": "not_found", …}` — so a caller reads a failure from the object instead of scraping `ERROR:` off stderr. Diagnostics still go to stderr as well.
- `store_status()` splits readiness resolution from its rendering, so prose and JSON exit `0`/`3`/`4` from one contract rather than two copies of the branching. `credit_payload()` and `days_left()` do the same for records, adding derived `days_left`, `expired`, and `brand_normalized` fields the prose rendering previously computed inline and threw away.
- `check --json` reports `matches` and `other_passenger_matches` separately, each entry carrying its `reasons` and a `passenger_on_trip` boolean. The family-member callout was prose-only before, so a consumer had to infer from an emoji line that a credit belonged to someone off the trip.
- Bootstrap narration moves to stderr under `--json` via `quiet_stdout()`. `init --json --default` printed its "✅ Initialized empty inventory" line to stdout ahead of the payload, leaving stdout unparseable — caught by the test asserting every command emits exactly one object, not by inspection.
- Bare `init --json` exits `2` with `{"error": "interactive_required"}`. The remaining branch prompts for a store location, and an agent must choose one explicitly rather than answer prompts on the user's behalf.
- `--expiry` is validated before anything is written. `add` parsed the date after `write_inventory()`, so a malformed value persisted the credit and then died in a traceback with empty stdout — a bug that predates this change and that the JSON contract made unignorable. It now emits `{"error": "invalid_expiry", …}`, exits non-zero, and leaves the store byte-identical, with regression tests asserting that in both output modes.
- The skill's own agent-facing invocations pass `--json` and state what comes back: `status` reports `{"state", "store", "reason"}` alongside its exit code, `list` reports `credits` and `count` with a zero count as a valid answer, and `add` reports the stored record plus `days_to_expiry`. `skill-authoring` Script References puts the input/output contract on the skill, and a script that emits JSON while its own skill parses prose would have left the contract undocumented on both sides.
- The skill declares its execution mode. `skill-authoring` Title and Preamble puts `Process steps in order. Do not skip ahead.` on the first content line after the H1 for a sequential workflow; this one opened with descriptive prose, leaving an agent free to parallelize or skip phases of a letter-construction flow whose order is load-bearing — verification before research, research before drafting.
- Every `--json` failure emits an object. `list --json` on an uninitialized store exited from `require_initialized()` with prose on stderr and empty stdout — unparseable, which reads to a caller as a crashed script rather than a reported failure. `require_initialized()` now emits `{"error": "store_not_initialized", …}`, and the `__main__` boundary guarantees the rest: any exit that skipped `emit_json` gets `{"error": "command_failed", "exit_code": N}`, including argparse failures, which is why `--json` is read from `sys.argv` before parsing. An unexpected exception emits `{"error": "unexpected_failure"}` and re-raises, under `error-handling` Outer-Boundary Carve-Out — the caller reads stdout as JSON, so a bare traceback breaks the contract it exists to serve.
- `check --json` detects scenario airlines and brands before the empty-store return. A Delta scenario reported `airlines_detected: []` purely because no credits existed yet, making the store's contents change what the scenario was understood to say.
- Thirteen tests cover the contract: one object per command, status states matching exit codes across all three branches, the `check` split, a structured error, the interactive refusal, prose remaining the default, a rejected `--expiry` leaving the store untouched in both modes, every failure path emitting an object, and scenario detection holding on an empty store.

## 0.9.26 — 2026-08-10

### Changed

- `review-trigger.yml` and `.env.example` upgraded to the current fleet-review setup (#28). Published from the merge of `feat/upgrade-coding-policy-review`.

## 0.9.25 — 2026-08-09

### Changed

- `.gitignore` covers the per-developer / per-agent files `tessl install` regenerates (`.tessl/`, `.agents/skills/`, and the per-agent skill mirrors), analogous to `node_modules`. Only `tessl.json` stays committed; the shared `.github` and `.vscode` trees keep everything else. Re-land of the 0.9.23 change with the ignore set narrowed after the 0.9.24 revert.

## 0.9.24 — 2026-08-09

### Fixed

- Reverted the 0.9.23 gitignore change. Its ignore set removed `.tessl/.gitignore` and `.agents/skills/.gitignore` while ignoring paths the working tree still needed, so a fresh clone lost directories the agents read.

## 0.9.23 — 2026-08-09

### Changed

- First attempt at gitignoring the tessl-generated artifacts. Reverted in 0.9.24 and re-landed correctly in 0.9.25.

## 0.9.22 — 2026-07-21

### Changed

- `publish.yml` becomes a thin caller of the canonical fleet pipeline, `jbaruch/coding-policy/.github/workflows/publish-plugin.yml` (jbaruch/coding-policy#206), replacing the 51-line per-repo `publish-plugin.yml` (#22). Same review → lint → publish sequence, maintained in one place. The display name is preserved so run-name watchers keep working, and the secret is scoped to `TESSL_TOKEN` rather than `inherit`.
- `.github/dependabot.yml` added so the reusable-workflow SHA pin has a stated renewal mechanism, which `dependency-management` Freshness requires and this repo had no scanner for.

## 0.9.21 — 2026-07-21

### Added

- `.github/workflows/review-trigger.yml` fires a single-PR fleet review in `jbaruch/coding-policy` on each PR event, so the policy verdict lands before merge rather than waiting on the scheduled poll (jbaruch/coding-policy#202). Needs the `FLEET_DISPATCH_TOKEN` secret.

## 0.9.20 — 2026-07-21

### Changed

- Review moves to the central fleet reviewer: `.github/fleet-review-enabled` enrolls the repo in the `coding-policy-fleet-reviewer` App, and the per-repo `review-codex.yml` plus `.github/codex-review/` are removed (jbaruch/coding-policy#202). The reviewer credential now lives only in `coding-policy`, not in each consumer.

## 0.9.19 — 2026-07-20

### Fixed

- `.github/copilot-instructions.md` pointed Copilot at a non-existent `AGENTS.md ## Review guidelines` section and called the workflow reviewer an "app", both left over from the reviewer-architecture migration one version earlier. Repointed at `.github/workflows/review-codex.yml`. Backfill for jbaruch/coding-policy#196.

## 0.9.18 — 2026-07-20

### Changed

- Review migrates to the Codex CLI subscription reviewer (#21): `.github/workflows/review-codex.yml` plus `.github/codex-review/` (prompt, schema, `post-review.sh`, `mask-secrets.sh`, `assert-no-secret-leak.sh`) replace the two gh-aw reviewers. Net −3972 lines — `review-anthropic` and `review-openai` and their 1800-line compiled `.lock.yml` files are gone, along with `.github/aw/actions-lock.json`. `.github/copilot-instructions.md` is added to scope Copilot to the correctness lane.

## 0.9.17 — 2026-07-18

### Changed

- The skill-review step tolerates a tessl out-of-credits outage (#20): only a credit-outage signature — the fixed-string "run out of credits" phrase **and** a 403 — skips; every other non-zero exit still blocks the publish. This is the `context-artifacts` Credit-Outage Review Carve-Out, opted into explicitly. The pinned review script was vendored inline at the time, pending jbaruch/coding-policy#188.

## 0.9.16 — 2026-07-03

### Changed

- `review-anthropic` and `review-openai` upgraded again, both `.md` sources and their compiled `.lock.yml` files.

## 0.9.15 — 2026-07-02

### Changed

- Follow-up upgrade to the `jbaruch/coding-policy` PR review workflows (#19), correcting the prompts landed in 0.9.14.

## 0.9.14 — 2026-07-01

### Changed

- `jbaruch/coding-policy` PR review workflows upgraded (#18) — `review-anthropic` and `review-openai` prompts rewritten and their `.lock.yml` files recompiled, plus a refreshed `.github/aw/actions-lock.json`.

## 0.9.13 — 2026-06-22

### Added

- `complaints-bank.py`: hotel-loyalty complaints are now first-class via a `--store {airline,hotel}` flag (before the subcommand; default `airline` for back-compat). The June 2026 Hilton habitability complaint — which produced a 2-night refund + 30K Honors points — could not be filed before because `file` hard-required `--airline/--flight/--flight-date/--route`. Resolves #3 (the complaints half; #13 was the credits half).
  - `--store hotel file` takes `--brand --property --reservation --stay-dates --loyalty-status` (plus the shared `--passenger/--category/--severity/--summary/--outcome`), writing to `complaint-bank/hotel-complaints.md` alongside the airline `complaints.md` in the same bank directory.
  - `check` / `list` / `pending` / `resolve` are store-aware: hotel `check` filters and finds patterns on `--brand` / `--property` (parallel to airline's `--airline` / `--route`); hotel `list` shows brand/property/stay columns.
  - Category vocabulary is per-store: hotel uses `HABITABILITY, SERVICE, BILLING, CLEANLINESS, NOISE, SAFETY, OTHER`; airline vocab unchanged. Each store rejects the other's categories.
  - The two stores have independent ID spaces and never leak into each other's `list`/`check`.
- `--store airline` (the default) is unchanged — existing call sites and `complaints.md` output are byte-identical.

### Notes

- FFA-only change: `complaints-bank.py` has no mirror in `jbaruch/jbaruch-travel-policy` (it ships only `credits-tracker.py`), so no byte-identical-sync ceremony applies. The live hotel data was already hand-maintained to the target schema, so nothing to migrate.
- Out of scope (follow-up): wiring the letter-writing skill flow to drive hotel complaints end-to-end. This lands the bank/tracker storage + retrieval; the airline-specialist letter rules (DOT, Contract of Carriage, FlightAware) stay shared.

## 0.9.12 — 2026-06-22

### Fixed

- `credits-tracker.py`: a credit tagged with **both** `--airline` and `--brand` no longer vanishes from airline scenarios in `check`. The 0.9.11 brand gate was mutually exclusive (`if credit_brand: … elif ctype …`), so once a brand was set the airline heuristics never ran and a co-branded credit only matched hotel scenarios. The brand match is now additive, and the airline heuristics are skipped only for a brand-only credit (`if credit_airline or not credit_brand`). Each issuer dimension now matches independently; the no-cross-bleed guarantee for brand-only credits is unchanged (#15).
- `credits-tracker.py`: `expiring` now renders issuer rows via the shared `_issuer_label()` helper (`Airline: DL`, brand rows show their chain) instead of the `Airline:  (DL)` double-space/redundant-parens form.

### Notes

- This reconverges FFA's `credits-tracker.py` **byte-identical** with the copy in `jbaruch/jbaruch-travel-policy` (v0.7.13, PR #10). The regression was found there while porting the 0.9.11 `--brand` work downstream; this lands the same fix upstream. `sha256(credits-tracker.py)` now matches across both repos.

## 0.9.11 — 2026-06-22

### Added

- `credits-tracker.py`: hotel/loyalty-program credits are now first-class. A new `--brand` dimension (e.g. `Hilton`, `Marriott`, `IHG`, `Hyatt`) sits alongside `--airline` on `add` and `list`. The June 2026 migration merged real hotel credits (a Hilton stay voucher, Honors points) into the shared `~/.claude/travel-credits/` inventory; before this they stored fine but were invisible to retrieval and matching (resolves #13).
  - `add --brand NAME` tags a credit with a hotel/program issuer; `list --brand NAME` filters by it. A `HOTEL_ALIASES` map collapses sub-brands to their chain code, so a `Conrad` or `Waldorf Astoria` credit is found by `--brand Hilton`. Aliases are restricted to unambiguous brand tokens or multi-word phrases (`Choice Hotels`, `Courtyard by Marriott`) — bare common words (`honors`, `choice`, `courtyard`) are excluded so they can't false-match ordinary airline prose.
  - `check` now detects hotel brands in the scenario (`hotels_in_scenario()`, parallel to `airlines_in_scenario()`) and surfaces brand-matched credits the same way airline credits surface. A scenario like `"Hilton London, 3 nights"` now fires the use-it-or-lose-it prompt for the Hilton voucher — airline-only matching never could.
  - `list` / `summary` / `expiring` / `check` gained brand visibility. The legacy "airline not specified" note in `check` no longer fires for a brand-tagged credit, so hotel credits don't read as noise in airline scenarios.
- `--airline` is unchanged (full back-compat): airline-only credits and scenarios behave exactly as before.

### Notes

- This change widens FFA's divergence from the byte-identical `credits-tracker.py` in `jbaruch/jbaruch-travel-policy`. The same `--brand` / `HOTEL_ALIASES` / `hotels_in_scenario()` additions must be ported upstream (mirror issue) to reconverge the two copies — cf. the 0.9.10 `cmd_init` divergence and #11.

## 0.9.10 — 2026-06-21

### Changed

- `credits-tracker.py` / `complaints-bank.py`: ported six bootstrap-hardening deltas from the shared `credits-tracker.py` in `jbaruch/travel-policy`, with the parallel guards mirrored into `complaints-bank.py`:
  - `link` refuses a `--path` dir with no `inventory.md` / `complaints.md` instead of bootstrapping a second, diverging store.
  - `link` / `init --path` reject whitespace-only paths (not just empty) with actionable guidance.
  - `init --path` refuses a plain file, symlink-to-non-dir, or dangling symlink at the target instead of crashing on `os.makedirs`.
  - Interactive `init` refuses an unusable store path (dangling symlink, plain file) rather than clobbering it.
  - `status` distinguishes a dangling symlink from a symlink to an existing non-directory.
  - `status` prints the bare `ready` token on stdout (resolved path moved to stderr) per the machine-readable contract.
- `credits-tracker.py` / `complaints-bank.py`: `cmd_init()` dispatches the non-interactive custom-path branch on argument presence (`args.path is not None`) instead of truthiness, so `init --path ""` reaches the empty-path diagnostic rather than falling into the interactive branch. This one-line fix makes FFA's `credits-tracker.py` diverge from `jbaruch/travel-policy` by exactly this commit; the same patch must be ported upstream to reconverge the two copies byte-identical.

## 0.9.9 — 2026-06-19

### Added

- `credits-tracker.py` / `complaints-bank.py`: `init --default` and `init --path DIR` (non-interactive setup), `link --path DIR` (point at an existing, e.g. cloud-synced, store), and `status` (report readiness: ready / missing / invalid).

### Changed

- The trackers no longer silently auto-create a store on first use. They fail loudly with recovery guidance, so a not-yet-linked cloud inventory can't be forked into a second, diverging empty copy. `~/.claude/travel-credits/` is shared with `jbaruch/travel-policy`.
- `init` refuses to clobber a dangling symlink (likely an unmounted cloud folder) or a plain file at the store path, instead of silently replacing it or crashing.

## 0.9.8 — 2026-06-19

### Changed

- Migrate the manifest from the legacy `tile.json` to the Tessl plugin form (`.tessl-plugin/plugin.json`).
- Add `.tesslignore` so CI, editor/agent tooling, and generated files stay out of the published plugin package.
- Add a project `README.md` (registry badge, install instructions, rules + skills tables) and this `CHANGELOG.md`.
- Rename the publish workflow `publish-tile.yml` → `publish-plugin.yml`.
- Remove the stale committed `.claude/skills/frequent-flyer-advocate/` snapshot that shadowed the canonical source under `skills/`.
