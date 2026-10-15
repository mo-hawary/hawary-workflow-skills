# Setup and Rollback

Run `scripts/fast_agents.py` from the installed skill folder. Setup commands are opt-in and use previews; do not apply them unless the user requested that setup.

## Install the Skill Folder

Preview the selected skill homes, then rerun with `--apply` after reviewing the destinations and collision state:

```bash
python3 scripts/fast_agents.py install --agents-home /path/to/codex/agents/skills
python3 scripts/fast_agents.py install --agents-home /path/to/codex/agents/skills --apply
```

This skill is **Codex-only**: `install --claude-home` fails without modifying files. Existing helper-owned legacy installations in a Claude skill directory may be removed using `rollback install --claude-home` after review; no new Claude installation is supported. Installation copies the complete `fast-codex-subagents` folder and records hashes for rollback. Existing unmanaged, modified, or outdated targets stop the install. Preview rollback before applying it:

The renamed skill uses a new destination. Existing `fast-agents` skill folders are left untouched; this installer neither adopts nor removes prior folders.

```bash
python3 scripts/fast_agents.py rollback install --agents-home /path/to/codex/agents/skills
python3 scripts/fast_agents.py rollback install --agents-home /path/to/codex/agents/skills --apply
```

## Save Defaults

`defaults show` reads the dedicated defaults file. `defaults set` first prints the complete proposed JSON, the current file hash, and the proposed hash. Fast can be saved for all workers, no workers, or a selected comma-separated subset:

```bash
python3 scripts/fast_agents.py defaults show
python3 scripts/fast_agents.py defaults set --fast-requested true --writer-effort max
python3 scripts/fast_agents.py defaults set --fast-workers none
python3 scripts/fast_agents.py defaults set --fast-workers implement,review
```

When no saved Fast choice exists, `defaults show` reports `recommended all`. Existing saved `none`, selected-worker, and per-role choices remain in effect until changed.

Apply only the displayed preview by supplying both hashes. Use `absent` when the preview says no defaults file exists:

```bash
python3 scripts/fast_agents.py defaults set --fast-requested true --writer-effort max --apply --expected-before BEFORE_SHA --confirm PROPOSED_SHA
python3 scripts/fast_agents.py defaults set --fast-workers none --apply --expected-before BEFORE_SHA --confirm PROPOSED_SHA
python3 scripts/fast_agents.py rollback defaults
python3 scripts/fast_agents.py rollback defaults --apply
```

The defaults file is separate from Codex instructions. For compatibility, its existing `fast-agents/defaults.json` location and schema remain unchanged. Saved settings take precedence over project/user rules; explicit user choices take precedence over saved defaults. Rollback restores the verified backup or removes a file created by this helper, and refuses if the current file changed.

## Add the Managed Codex Instruction Block

Preview the active shared instruction file for the selected Codex home. If `AGENTS.override.md` exists, it takes precedence over `AGENTS.md`. The managed text names `$fast-codex-subagents` for Codex CLI and Codex app, but only clients configured to load that selected file apply it. The setup command cannot inspect or automate Codex app activation; verify that in the app. The preview prints the exact block to add or replace, the current file hash, and the resulting hash:

```bash
python3 scripts/fast_agents.py instructions preview --path /path/to/codex-home
```

Apply that specific preview with its ID and hashes:

```bash
python3 scripts/fast_agents.py instructions apply --plan PREVIEW_ID --expected-before ORIGINAL_SHA --confirm RESULT_SHA
python3 scripts/fast_agents.py rollback instructions
python3 scripts/fast_agents.py rollback instructions --apply
```

The first application creates a backup before writing. Refreshing an existing owned block preserves that original backup. Rollback restores it only if the instruction file and receipt-owned managed block are unchanged. If another instruction is added or edited, rollback refuses and preserves the file.

If an unchanged helper-owned instruction receipt contains the previous `fast-agents` block, preview validates the full file hash, saved backup, and receipt's block hash, then offers to replace only that block. Applying the reviewed preview retains the original backup and rollback baseline. You can also roll back the unchanged old block directly; rollback checks the block against the receipt rather than requiring the current version. Any file or receipt change after preview stops the update.
