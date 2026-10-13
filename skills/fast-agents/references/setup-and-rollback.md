# Setup and Rollback

Run `scripts/fast_agents.py` from the installed skill folder. Setup commands are opt-in and use previews; do not apply them unless the user requested that setup.

## Install the Skill Folder

Preview the selected skill homes, then rerun with `--apply` after reviewing the destinations and collision state:

```bash
python3 scripts/fast_agents.py install --agents-home /path/to/agents/skills --claude-home /path/to/claude/skills
python3 scripts/fast_agents.py install --agents-home /path/to/agents/skills --claude-home /path/to/claude/skills --apply
```

Installation copies the complete `fast-agents` folder and records hashes for rollback. Existing unmanaged, modified, or outdated targets stop the install. Preview rollback before applying it:

```bash
python3 scripts/fast_agents.py rollback install --agents-home /path/to/agents/skills --claude-home /path/to/claude/skills
python3 scripts/fast_agents.py rollback install --agents-home /path/to/agents/skills --claude-home /path/to/claude/skills --apply
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

The defaults file is separate from Codex instructions. Saved settings take precedence over project/user rules; explicit user choices take precedence over saved defaults. Rollback restores the verified backup or removes a file created by fast-agents, and refuses if the current file changed.

## Add the Managed Codex Instruction Block

Preview the active shared instruction file for the selected Codex home. If `AGENTS.override.md` exists, it takes precedence over `AGENTS.md`. The managed text names both Codex CLI and Codex app, but only clients configured to load that selected file apply it. The setup command cannot inspect or automate Codex app activation; verify that in the app. The preview prints the exact appended block, the original hash, and the resulting hash:

```bash
python3 scripts/fast_agents.py instructions preview --path /path/to/codex-home
```

Apply that specific preview with its ID and hashes:

```bash
python3 scripts/fast_agents.py instructions apply --plan PREVIEW_ID --expected-before ORIGINAL_SHA --confirm RESULT_SHA
python3 scripts/fast_agents.py rollback instructions
python3 scripts/fast_agents.py rollback instructions --apply
```

Applying creates a backup before writing. Rollback restores it only if the instruction file and managed block are unchanged. If another instruction is added or edited, rollback refuses and preserves the file.
