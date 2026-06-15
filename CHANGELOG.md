# Changelog

## 0.9.7 — 2026-06-15

### Migrate manifest to `.tessl-plugin/plugin.json`
Converted the legacy `tile.json` to the `.tessl-plugin/plugin.json` plugin manifest and renamed the publish workflow to `publish-plugin.yml`.

### Locate existing stores before starting fresh (#1)
The complaint-bank and travel-credits scripts no longer silently create an empty store on first use. They report state via `status`, adopt an existing store with `link --path <dir>`, or start fresh with `init`; the skill now asks the user to locate prior history (iCloud / Drive / Dropbox / a prior install) before starting fresh.
