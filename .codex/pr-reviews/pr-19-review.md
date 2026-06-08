# PR 19 Review: dev-secrets Skill

## PR / Branch

- PR: #19, `bootcamp-dev-secrets-skill`
- Base: `origin/main` at `61e9bc4fe369d668603a40629d06e260238bc499`
- Head reviewed: `11903bc5912cb8b1445e78ffb891af147d82a3bd`
- URL: https://github.com/mo-hawary/hawary-workflow-skills/pull/19

## PR Study Summary

Intent: add a public-safe `dev-secrets` Agent Skill for value-free handling of local env files, local CLI gating, manifests, safe run guidance, scans, rotation/recovery guidance, adapters, docs examples, and tests.

Changed-file surface:

- New skill docs under `skills/dev-secrets/`
- New agent metadata under `skills/dev-secrets/agents/`
- New adapter docs under `adapters/`
- README, examples, changelog, skill index, compatibility docs
- Validator metadata compatibility update
- Public package tests for dev-secrets safety constraints

Source artifacts read:

- PR body and commit/file metadata
- `origin/main...origin/bootcamp-dev-secrets-skill` diff
- `skills/dev-secrets/SKILL.md`
- `skills/dev-secrets/references/examples.md`
- `skills/dev-secrets/references/output-templates.md`
- `skills/dev-secrets/references/recovery-playbook.md`
- `skills/dev-secrets/references/threat-model.md`
- `adapters/codex/AGENTS.md`
- `adapters/claude/README.md`
- `adapters/generic-agent.md`
- `docs/agent-compatibility.md`
- `scripts/validate_skills.rb`
- `tests/test_public_package.py`

Missing source artifacts:

- No `specs/` or `impp/` artifacts exist in this repository. Treating PR body plus diff as source of truth because this is a small docs/metadata skill PR.

Open questions:

- None required to review. Fixing findings would require a follow-up edit pass.

## Extracted Requirements

Acceptance criteria:

- Agent never reads, prints, summarizes, transforms, hashes, or fingerprints real env values.
- Skill output remains value-free and status-driven.
- Compatible CLI commands are gated by CLI availability.
- Project-file mutation requires explicit approval.
- Rotation is recommended when exposure cannot be ruled out.
- Public package validation and tests pass.

Invariants:

- Documentation examples and templates must not weaken the canonical `SKILL.md` safety contract.
- Adapters must not broaden the safe-inspection boundary beyond `SKILL.md`.
- Canonical output field names should be consistent across examples/templates/tests.

Contracts / docs obligations:

- `skills-index.json` description must match skill frontmatter.
- Skill references must exist and be tracked.
- Each skill must expose usable agent metadata.
- README and `docs/examples.md` must mention every indexed skill.

Invariant escalation gate:

- Not applicable. This PR is docs/metadata/tests for a public skill. It does not touch payments, auth/session, database state, migrations, queues, or runtime lifecycle state.

## Review Plan

- [x] Identify PR intent and changed-file surface.
- [x] Search for specs/impp and determine source of truth.
- [x] Review canonical skill contract for safety and output shape.
- [x] Review examples/templates/adapters for contract drift.
- [x] Review validator/test updates for coverage gaps.
- [x] Run clean-head validation.
- [x] Record findings and residual risk.
- [x] Defender fixes, approved by Mohamed.
- [x] Fresh attacker pass after defender fixes.

## Validation

Clean detached PR worktree:

- `ruby scripts/validate_skills.rb`: passed, `Validated 10 skills.`
- `pytest`: passed, `44 passed, 1 skipped`
- `git diff --check origin/main...origin/bootcamp-dev-secrets-skill`: passed

Local checkout note:

- The main workspace had pre-existing dirty files unrelated to this review during the initial pass. Validation was run from a clean detached PR worktree to avoid contamination.

## Attacker Round 1 Findings

### 1. Medium: Output templates still contradict the canonical field casing and `Next Command` contract

Issue:

`SKILL.md` defines the canonical output fields as `Safe Evidence`, `Next Command`, and `Approval Needed`, but `references/output-templates.md` still shows lowercase `Safe evidence`, `Next step`, `Next command`, and `Approval needed`. This keeps a copy-ready template that conflicts with the skill's own output contract and with the review fixes already applied to `examples.md`.

Evidence:

- `skills/dev-secrets/SKILL.md:144-148` defines `Safe Evidence`, `Next Command`, and `Approval Needed`.
- `skills/dev-secrets/references/output-templates.md:55-59` uses `Safe evidence`, `Next step`, and `Approval needed`.
- `skills/dev-secrets/references/output-templates.md:65-69` uses `Safe evidence`, `Next command`, and `Approval needed`.
- `tests/test_public_package.py:138` currently asserts the lowercase `Next step` text in the output template, which locks in the mismatch instead of catching it.

Recommended fix:

Normalize both output template examples to the canonical field labels from `SKILL.md`: `Safe Evidence`, `Next Command`, and `Approval Needed`. For the no-CLI plan, keep the wording as a setup action, but still expose it under `Next Command` only if that remains the canonical field name. Update the test to assert canonical casing in `output-templates.md` instead of lowercase `Next step`.

### 2. Medium: Codex adapter broadens safe inspection beyond `SKILL.md`

Issue:

The Codex adapter says safe inspection includes `package scripts` and `manifests`, while `SKILL.md` narrows that to package script names and redacted manifest metadata, explicitly forbidding full package script command strings unless they come from a compatible CLI or redacted manifest. The adapter is persistent guidance, so this ambiguity can cause Codex to inspect full script commands or manifest content that the canonical skill tells it not to inspect.

Evidence:

- `skills/dev-secrets/SKILL.md:89-90` allows only `package script names` and `redacted manifest metadata`, and says not to inspect full package script command strings unless they come from a compatible local CLI or redacted manifest.
- `adapters/codex/AGENTS.md:22` says: `Inspect safe repo signals only: file names, tracking status, ignore rules, package scripts, examples, and manifests.`

Recommended fix:

Change the Codex adapter safety bullet to mirror the canonical wording: `package script names` and `redacted manifest metadata`; add the same prohibition on full package script command strings unless they come from a compatible local CLI or redacted manifest.

## Defender Responses

Revalidated and addressed in `4a93a2fb6955712e065e73e8c04e5d51911466c7` and `11903bc5912cb8b1445e78ffb891af147d82a3bd`.

1. Output template field casing and `Next Command` contract
   - Validation: `ruby scripts/validate_skills.rb` passed; `pytest` passed with 45 passed and 1 skipped; `git diff --check origin/main...HEAD` passed.
   - Reason: valid. `output-templates.md` and copy-ready examples still had casing drift from the canonical `SKILL.md` output contract.
   - Fix: normalized `skills/dev-secrets/references/output-templates.md` and `skills/dev-secrets/references/examples.md` to `Safe Evidence`, `Next Command`, and `Approval Needed`; updated `tests/test_public_package.py` to assert canonical labels and reject the old labels.
   - Resolution: fixed.

2. Codex adapter safe-inspection boundary
   - Validation: `ruby scripts/validate_skills.rb` passed; `pytest` passed with 45 passed and 1 skipped; focused `rg` sweep found the unsafe wording only inside test forbidden-string assertions.
   - Reason: valid. The pushed adapter wording was broader than `SKILL.md`.
   - Fix: updated `adapters/codex/AGENTS.md` to allow package script names and redacted manifest metadata only, plus the explicit full-command-string prohibition; updated tests to assert the narrower wording.
   - Resolution: fixed.

## Final Attacker Pass

Latest attacker pass reviewed head `11903bc5912cb8b1445e78ffb891af147d82a3bd`.

Result: no blocker/high/medium correctness issues remain from the reviewed docs-contract surface.

## Follow-Up Review Fixes

Later GitHub review feedback identified additional missing design/spec constraints. These were addressed in `9025dbe147786cc0257f375c613fc4a777b3d577`.

Changes made:

- Added `.env.production` to `SKILL.md` while preserving local-development scope and requiring provider/platform rotation if production-looking files are exposed.
- Added key-name sensitivity disclosure and non-negotiable limits: key names can reveal vendors, architecture, data categories, and internal systems, and may be shown only for manifest, classification, warning, or planning purposes.
- Replaced broad example-file inspection wording with example file presence only; key names are allowed only from compatible redacted CLI output or known value-free manifests.
- Added multi-file collision handling for "all files" import requests.
- Expanded run-wrapper warnings for unfamiliar, downloaded, generated, or externally sourced commands.
- Added final-check coverage that `.env*` files were not deleted, overwritten, truncated, or rewritten without explicit approval.
- Updated the threat model for key-name context leakage and `unclassified — review before import`.
- Added examples for agentic-loop `cat .env` refusal and `.env.development` plus `.env.test` separate value-free manifest handling.
- Updated recovery guidance with `chmod 600` and CI/CD/deployment secret-store rotation.
- Updated tests to enforce these constraints and fail on broad wording such as `example file presence and key names`.

Validation after follow-up fixes:

- `ruby scripts/validate_skills.rb`: passed.
- `pytest`: passed, 45 passed and 1 skipped.
- `git diff --check`: passed.
- GitHub `Skill validation`: passed on head `9025dbe147786cc0257f375c613fc4a777b3d577`.

## Residual Risk

- PR #19 still requires review approval before merge.
