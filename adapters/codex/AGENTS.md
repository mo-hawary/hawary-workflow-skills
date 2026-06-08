# Codex Adapter For dev-secrets

Codex uses `AGENTS.md`-style persistent guidance. Keep `skills/dev-secrets/SKILL.md` as the canonical source and route local env-secret tasks to that skill.

## Routing

Use `skills/dev-secrets/SKILL.md` when the user asks about:

- `.env`, `.env.local`, `.env.development`, `.env.test`, or `.env.production`
- local secret migration
- vault import
- local secret scanning
- env-file leak detection
- rotation guidance
- value-free dev run commands

## Safety

- Do not read real `.env*` values.
- Do not show values, partial values, hashes, fingerprints, encrypted blobs, or passphrase hints.
- Do not run broad secret-reading commands.
- Inspect safe repo signals only: file names, tracking status, ignore rules, package script names, example file presence only, and redacted manifest metadata.
- Show key names only for manifest, classification, warning, or planning purposes, and only when obtained from compatible redacted CLI output or a known value-free manifest.
- Never inspect full package script command strings, example-file values, copied secrets, encrypted blobs, hashes, fingerprints, partial values, transformed values, or manifest values unless they come from a compatible local CLI or redacted manifest.
- Output value-free plans when no compatible local CLI exists.
- Ask approval before any mutation.

## Expected Codex Response

Return the skill's standard output shape: Status, Safe Evidence, Warnings, Next Command, and Approval Needed.
