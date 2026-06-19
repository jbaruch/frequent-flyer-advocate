# Changelog

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
