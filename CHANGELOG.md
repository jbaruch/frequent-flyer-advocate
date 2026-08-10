# Changelog

## 0.9.27 — 2026-08-10

### Added

- `scripts/letter-fit.py` + `scripts/airline-form-metadata.json`: the skill now measures a form-mode letter against the airline's actual submission-form limit instead of trusting its own arithmetic. Resolves #4 (channel awareness) and #5 (the script that makes the metadata operational) together — the metadata without the script is documentation, and the script without the intake questions has nothing to measure against.
  - The failure that prompted both issues: a Southwest draft measured 2472 by Python `len()`, was declared under the 2500-char form limit, and the live form's counter came back **2798**. No encoding of that text reproduces 2798 — UTF-8 bytes 2482, CRLF 2495, HTML entities 2500 — so the form counts by a method nobody has identified.
  - `letter-fit.py` counts under all four encodings. When the channel's `counting_method` names one, that count is authoritative. When it is `unknown`, the worst count is multiplied by an inflation factor and the verdict is labeled unverified. WN carries `observed_inflation: 1.14`, derived from that 2798/2472 ratio; `UNKNOWN_INFLATION_DEFAULT = 1.15` covers forms nobody has measured. Replayed against the original draft the script judges it at 2820 vs the form's 2798 and exits 1 — the draft that shipped would have been stopped.
  - Formatting warnings flag markdown bold, headings, bullets, blockquotes, links, and unicode bullets against the channel's `formatting` map. Only an explicit `true` stays silent; a `false` or `"unknown"` entry warns, so an unrecorded field is a reason to check rather than permission to assume.
  - Exit codes: 0 fits, 1 overflows, 2 argument or metadata error. `--limit N` measures an airline the metadata does not record; `--info` and `--list-airlines` need no letter; `--metadata` points at another copy of the data file.
  - stdout is JSON in every mode, per `script-delegation` "JSON-producing: output structured data, not prose". The script measures and the skill renders; `worst_count` and `effective_count` ship precomputed so no caller does arithmetic the script exists to prevent.
  - Seed metadata covers AA (1500-char web form, five prefilled fields, executive-office paper address) and WN (2500-char web form). Both limits carry `limit_verified` + `limit_source`; nothing is recorded that was not observed.
- `tests/test_letter_fit.py`: 38 outcome-focused cases covering counting, the inflation margin, the `--limit` override, formatting detection, input handling, error paths, and a provenance check over the shipped metadata. The reported Southwest draft is pinned as a regression test. Deterministic — fixed inputs, no clock, no network.

### Changed

- `.github/workflows/publish.yml` wires the reusable publish workflow's `pre-publish-script` and `python-version` inputs at the new `.github/scripts/pre-publish-gate.sh`, which runs both test suites (70 cases) before the publish steps. A non-zero exit fails the publish. Resolves the test half of #10 — shipping a new suite while recording that it does not run in CI is the `testing-standards` violation the deferral created, so the CI change is announced in this PR's scope rather than left open.

- `SKILL.md` Phase 1 asks for the submission channel alongside the other always-gather items, and for a web form follows up on the character limit and the fields the form captures itself. Asking after a draft exists is what forced the two compression passes in the June 2026 AA case: one to strip data the form already had, one to fit 1500 chars from ~5500.
- `SKILL.md` Phase 4 adds the form-fit variant and makes `letter-fit.py` a gate — an overflowing draft is never presented, and the user sees the script's output rather than the agent's count.
- `SKILL.md` Phase 5 reads recorded channel reliability from the metadata before recommending where to send. AA's executive customer-relations email is recorded as routing to unmonitored mailboxes since January 2026; the guidance is web form first, paper mail to the executive office for escalation.
- `SKILL.md` converts from `## Phase N:` prose headings to the flat `## Step N — Title` structure `skill-authoring` prescribes, with the execution-mode preamble and an explicit continuation on every step. Eleven steps, one action each; the letter's own anatomy stays unnumbered so it does not read as sub-steps. Step 9 (form-fit verification) states its skip condition explicitly for email and paper mail. Phase 3's blanket "always parallelize" is scoped to concurrency among the 8 research items inside their own step, which no longer reads as license to run steps out of order.
- `rules/letter-quality.md`, `rules/escalation-output.md`, and `rules/complaint-patterns.md` gain the `applyTo:` scope `rule-frontmatter` requires on a conditional rule. All three carried `alwaysApply: false` with scope prose in `description:` alone, which that rule names explicitly as not a substitute — agents could fail to load them in the contexts they govern.
- `rules/letter-quality.md` splits into per-mode sections. The form's own fields may cover passenger name, loyalty number, flight number, date, route, and gates; loyalty **tier** in the opening sentence, the FlightAware timestamp, the verbatim quote, and the 14–21 day deadline all survive compression. Counting a form-mode letter by hand is now forbidden outright.

### Notes

- Both scripts are FFA-only — no mirror exists in `jbaruch/jbaruch-travel-policy`, so no byte-identical-sync ceremony applies.
- `counting_method` stays `"unknown"` for AA and WN. Resolving it needs someone to paste a known-length string into each live form and read the counter back; the inflation margin is the stand-in until then. A verified method makes the margin disappear on its own — the script uses the named counter and drops the multiplier.
- Remaining from #10: the pyright gate. `language-diagnostics` wants the headless engine run in CI at zero findings; the tree is clean today, so it lands green whenever it is wired. Kept out of this PR to hold the CI surface to what the test gate needs.

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
