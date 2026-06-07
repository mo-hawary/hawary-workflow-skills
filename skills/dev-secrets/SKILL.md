---
name: dev-secrets
version: "0.1.0"
allowed-tools:
  - Read
  - Glob
  - Bash
description: "Use when asked to handle .env files, local secrets, vault workflows, or risks exposing real values to an AI agent. Outputs: safe evidence, warnings, next command, recovery."
---

# Dev Secrets

Dev Secrets keeps local `.env` values out of AI agent context while preserving local dev convenience.

## Implementation Status

The `dev-secrets` CLI is not yet implemented by this repository. When no compatible CLI is found in the target project, output the workflow as a plan with `Status: needs setup`. Never pretend commands ran.

This skill is design/spec behavior for local development. It is not production secret management, team-wide secret distribution, compliance-grade vaulting, or malware defense.

## Why This Exists

AI coding agents can accidentally pull real `.env` values into chat, diffs, summaries, or generated files. Once a value enters chat context, treat it as exposed. `dev-secrets` prevents that by keeping the agent on value-free evidence while a local CLI handles real values on the developer's machine, never in chat.

## Two-Layer Model

### Agent Layer

Can see:

- File names.
- Key names.
- Counts.
- Classifications.
- Warnings.
- Value-free manifests and examples.

Cannot see:

- Values.
- Partial values.
- Transformed values.
- Hashes or fingerprints.
- Encrypted blobs.
- Passphrase hints.

### Local CLI Layer

Can do:

- Parse, import, decrypt, and inject values locally.
- Prompt the developer interactively for real values.
- Generate value-free outputs such as manifests, examples, and scan reports.

Cannot do:

- Print values to stdout or stderr.
- Fall back to plaintext if encryption or backend setup fails.
- Act on project files without explicit approval.

## When To Use

Use this when asked to handle `.env`, `.env.local`, `.env.development`, `.env.test`, framework env files, local secret migration, secret-safe dev commands, value-free env manifests, local vault workflows, redacted local runs, or suspected local env leaks.

## Required Disclosure

Before inspecting a repo for env files or recommending import/run commands, tell the user:

1. This is for local-development secrets only, not production secret management.
2. You will not print, summarize, expose, or directly read real secret values.
3. Real values must be handled through local CLI or vault prompts, not chat.
4. If multiple env files exist, the user chooses which file to import.
5. Project-file edits are suggestions-only unless a dedicated local CLI command gets explicit approval.
6. Any child process receiving secrets can still leak through logs, crashes, telemetry, snapshots, or subprocesses.
7. If a value may have leaked, rotate it.

## Non-Negotiable Rules

- Treat real `.env*` files as import-only inputs.
- Do not open, preview, summarize, transform, echo, export, copy, or paste real env-file values.
- Do not run broad secret-reading commands such as `cat .env`, `printenv`, `env`, or recursive searches for token-like names.
- Do not show values, partial values, transformed values, hashes, fingerprints, encrypted blobs, passphrase hints, or provider tokens.
- Allowed user-facing data: file names, key names, counts, classifications, reasons, locations, warnings, and next steps.
- No plaintext fallback: if encryption, permissions, or backend setup fails, fail closed with setup instructions.
- Never delete, overwrite, truncate, or rewrite real `.env*` files without explicit approval.

## Standard Workflow

1. Give the required disclosure.
2. Inspect only safe repo signals: tracked file names, `.gitignore`, package script names, existing examples, and redacted manifest metadata. Do not inspect full package script command strings unless they come from a compatible local CLI or redacted manifest.
3. If env files exist, ask which file or files the user wants to import. Do not read their contents.
4. Recommend a value-free dry run before import.
5. Require local interactive approval before importing values or changing project files.
6. Generate or review value-free outputs only: `.env.example`, `secrets.manifest.json`, warnings, classifications, and alias proposals.
7. Use a local run wrapper for commands that need secret injection.
8. Run leak scans as warning-only unless the user approves cleanup.
9. If exposure is possible, recommend rotation and history cleanup where needed.

## CLI Availability Gate

Check whether a compatible `dev-secrets` CLI exists before recommending executable commands.

- If a compatible CLI exists: return the next value-safe command, such as `dev-secrets import .env --plan`.
- If no compatible CLI exists: return `Status: needs setup` and a next step to install or implement a compatible local CLI before importing secrets.
- If CLI status is unknown: return `Status: needs setup` and recommend `dev-secrets doctor` only as a command shape, not as something already run.

## Allowed Command Shape

These commands assume the `dev-secrets` CLI is installed in the project. If no compatible CLI exists, treat these as planning output and report the workflow as a plan instead.

Use these command shapes when a compatible CLI exists or is being implemented:

```bash
dev-secrets init
dev-secrets doctor
dev-secrets status
dev-secrets import .env --plan
dev-secrets import .env
dev-secrets unlock --ttl 2h
dev-secrets run -- npm run dev
dev-secrets scan
dev-secrets lock
dev-secrets add-alias
```

The `run` command injects values into the requested child process environment, never into command arguments. The child process remains trusted code and can still leak values.

## Before Any Code Is Written

- [ ] Skill behavior exists before encryption code.
- [ ] No command prints real values.
- [ ] No plaintext fallback exists.
- [ ] `.env*` files are import-only.
- [ ] `import --plan` is value-free.
- [ ] `scan` reports locations without values.
- [ ] Existing scripts are never rewritten.
- [ ] Aliases are additive and approval-gated.
- [ ] Tests prove stdout and stderr do not leak fixture values.

## Output

Return:

- **Status:** ready, blocked, needs setup, needs user choice, imported, locked, scan warning, or rotation needed.
- **Safe Evidence:** file names, key names, counts, tracked env-file status, ignore status, and manifest/example presence.
- **Warnings:** leak boundaries, missing backend, unsafe tracked env files, duplicate env files, public-prefixed risky keys, multiline redaction limits.
- **Next Command:** the smallest value-safe command the user can run locally.
- **Approval Needed:** any import, alias addition, file mutation, cleanup, or rotation action.

## References

- For threat model and leak boundaries, see `references/threat-model.md`.
- For agent-safe examples and refusals, see `references/examples.md`.
- For canonical value-free output shapes, see `references/output-templates.md`.
- For rotation and recovery steps, see `references/recovery-playbook.md`.

## Final Checks

- No real values were read or shown.
- The user chose any env import targets.
- All outputs are value-free.
- Local-development scope is explicit.
- Any file mutation or cleanup waits for approval.
- Rotation is recommended whenever exposure cannot be ruled out.
