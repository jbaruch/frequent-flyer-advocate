# Changelog

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
