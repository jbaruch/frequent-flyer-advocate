"""Shared storage logic for the frequent-flyer-advocate stateful stores.

Both the complaint bank (~/.claude/complaint-bank/) and the travel-credits
inventory (~/.claude/travel-credits/) accumulate value over time. Per
jbaruch/coding-policy rules/stateful-artifacts.md, we must never silently
create a fresh empty store when the user may already have real history
elsewhere — doing so discards escalation leverage without telling anyone.

This module centralizes locate -> link -> create-empty so both scripts behave
identically. These scripts are run by an agent (the skill), never interactively
by a human, so they NEVER prompt. When a store's data file is missing, the read/
write guard prints guidance and exits STORE_MISSING_EXIT; the skill is responsible
for asking the user (see SKILL.md "locate the stateful stores") and then calling
`link --path <dir>` (adopt an existing store) or `init` (start fresh).

A store that exists but is *empty* is treated as ready: emptiness is only
suspicious when it was created silently, and this module never does that.
"""

import os
import sys

# Exit code that signals "store missing — ask the user before creating".
STORE_MISSING_EXIT = 3

SUGGESTED_LOCATIONS = (
    "~/Library/Mobile Documents/com~apple~CloudDocs/  (iCloud Drive)",
    "~/Google Drive/",
    "~/Dropbox/",
    "~/Documents/travel/",
    "a prior install under ~/Projects/",
)


class Store:
    """One stateful store plus its canonical (default) on-disk location."""

    def __init__(self, name, store_dir, data_path, empty_content, is_empty=None):
        self.name = name                  # human label, e.g. "complaint bank"
        self.store_dir = store_dir        # canonical dir, e.g. ~/.claude/complaint-bank
        self.data_path = data_path        # data file inside store_dir
        self.empty_content = empty_content
        self._is_empty = is_empty         # callable(content) -> bool, or None

    @property
    def command(self):
        """How this script was invoked, for copy-pasteable guidance."""
        return f"python3 {sys.argv[0]}"

    # --- state -------------------------------------------------------------
    def state(self):
        """Return 'present', 'empty', or 'missing'."""
        if not os.path.exists(self.data_path):
            return "missing"
        if self._is_empty is not None:
            try:
                with open(self.data_path) as f:
                    if self._is_empty(f.read()):
                        return "empty"
            except OSError:
                return "missing"
        return "present"

    # --- mutations ---------------------------------------------------------
    def write_empty(self):
        """Write the empty template if the data file is absent.

        Follows a symlinked store_dir to its real target, so `link` followed by a
        first write populates the user's chosen location rather than ~/.claude.
        """
        if not os.path.exists(self.data_path):
            os.makedirs(os.path.realpath(self.store_dir), exist_ok=True)
            with open(self.data_path, "w") as f:
                f.write(self.empty_content)

    def link(self, target):
        """Symlink the canonical store_dir to an existing directory `target`."""
        target = os.path.abspath(os.path.expanduser(target))
        if not os.path.isdir(target):
            sys.exit(f"ERROR: {target} is not a directory.")
        if os.path.lexists(self.store_dir):
            if os.path.realpath(self.store_dir) == os.path.realpath(target):
                print(f"Already linked: {self.store_dir} -> {target}")
                return
            sys.exit(
                f"ERROR: {self.store_dir} already exists (-> "
                f"{os.path.realpath(self.store_dir)}). Move or remove it first."
            )
        os.makedirs(os.path.dirname(self.store_dir), exist_ok=True)
        os.symlink(target, self.store_dir)
        print(f"Linked {self.store_dir} -> {target}")
        self.write_empty()

    # --- guard -------------------------------------------------------------
    def ensure_ready(self):
        """Run before any read/write data command.

        If the data file exists (even empty), proceed. If it is missing, never
        create one silently: print guidance and exit STORE_MISSING_EXIT so the
        skill asks the user whether they have an existing store, then calls
        `link --path <dir>` or `init`.
        """
        if os.path.exists(self.data_path):
            return
        lines = [
            f"No {self.name} found at {self.store_dir}.",
            "Do NOT assume the user has no history. Ask whether they have an existing",
            f"{self.name} elsewhere before starting fresh. Common locations:",
        ]
        lines += [f"  - {loc}" for loc in SUGGESTED_LOCATIONS]
        lines += [
            f"If they point to one:  {self.command} link --path <dir>",
            f"To start fresh:        {self.command} init",
        ]
        print("\n".join(lines), file=sys.stderr)
        sys.exit(STORE_MISSING_EXIT)
