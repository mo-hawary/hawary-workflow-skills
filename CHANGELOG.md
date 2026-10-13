# Changelog

All notable changes to Hawary Workflow Skills will be documented in this file.

This project follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and uses semantic version tags.

## [Unreleased]

### Added

- Codex-only skill metadata and runtime checks; fixture-tested CLI release registry, versioned schema fixture archives, and a Codex upgrade runbook.
- Authenticated main/nightly Codex smoke workflow for Linux/macOS (requires configured credential), metadata-only transcript recorder and review-gated real golden replay suite; no unverified real transcript committed.
- Bounded control IPC, steering duplicate rejection, finite app-server timeouts, limits on approval requests and team sizes, and explicit parent-exit/cancellation semantics.
- Automatic routing to direct Codex exec for a single bounded independent role; app-server for multi-role/dependent or complex tasks, without silent backend fallback.
- Bounded read-only Git scope snapshots for Codex writer tasks, clearly separated from test/acceptance verification; explicit limits for nested workspaces, pre-existing dirtiness, and concurrent edits.
- Cross-platform regression cases for launch snapshots, recovery classification, approval reply races, progress persistence, and scope auditing.
- Repeatable Fast ON/OFF benchmarking protocol with paired runs, correctness gates, and tier-evidence separation.
- `fast-codex-subagents`, an experimental public-beta skill with a Codex CLI 0.162.1-pinned app-server supervisor, separate logical worker threads, smallest-team guidance, authenticated local control, and explicit requested/effective Fast-tier evidence.
- Fake Codex CLI/app-server fixtures and cross-platform `test_fast_agents.py` coverage for protocol routing, approvals, IPC steering, recovery, process ownership, setup, and rollback without paid model calls.
- `dependency-security-auditor` for dependency CVE scans, freshness checks, and hook/CI audit design.
- Prompt examples for `dependency-security-auditor`.
- Fixture-backed bootcamp test matrix for dependency-auditor ecosystem coverage.
- CI coverage for the dependency-auditor Python tests and whitespace checks.
- Pinned CI validation inputs for GitHub Actions and hash-checked Python test dependencies.
- Egyptian Arabic README with language links from the English README.
- Current-state documentation in the README, roadmap, and skill authoring guide.
- Compatibility matrix, trust model, and security model for public agent adoption.
- Machine-readable `skills-index.json` catalog for agent directories and package tooling.
- Claude Code marketplace adapter at `.claude-plugin/marketplace.json`.
- Package-surface tests for `COMPATIBILITY.md`, `TRUST.md`, `skills-index.json`, and `.claude-plugin/marketplace.json`.

### Changed

- Fixed Plan Mode launch drift by passing the validated manifest unchanged into the launch boundary and enforcing the host's Plan Mode there.
- Preserved recovery authority for uncertain turns even when dependent roles are blocked; verified completed turns missing reports remain terminal failures. Recoverable dependents are requeued only after proven success.
- Made app-server approval replies single-claim before any asynchronous write; duplicate concurrent decisions are rejected and uncertain delivery is never blindly retried.
- Rechecked pinned Codex compatibility before launching a replacement supervisor during recovery.
- Coalesced routine progress status writes while keeping submission, approval, and recovery boundaries durable; skipped redundant pending-authority file rewrites.
- Extracted pure execution-state classification and expanded compact status output with current turn IDs, tiers, operator actions, and separated workspace scope evidence.
- Renamed the pre-release `fast-agents` skill to `fast-codex-subagents`; internal helper filenames and existing runtime-state and ownership identifiers remain stable.
- Hardened `dependency-security-auditor` package-manager detection, partial-status reporting, pnpm audit parsing, and Python requirements evidence notes.
- Added dependency-auditor setup hints and unsupported-tool handling for friendlier local installs.
- Added bootcamp fixtures for npm, pnpm, Yarn, Bun, Flutter/Dart, pinned and unpinned Python requirements, Python lockfiles, manifest-only Node, and stale mixed Node lockfiles.
- Expanded skill validation to check referenced files, agent metadata, local Markdown links, and README/example coverage.
- Tightened dependency-auditor status handling so findings from one scanner run cannot mask another run's scanner error.
- Scoped dependency-auditor OSV scans to discovered lockfiles so test fixtures and stale conflicting lockfiles do not create live dependency findings.
- Added Bun `package-lock.json` audit fallback and weak-evidence handling for Python requirements includes.
- Polished the Egyptian Arabic README intro for clearer, more natural wording.
- Expanded validation to check `skills-index.json` and Claude Code marketplace metadata.

## [0.3.0] - 2026-05-14

### Added

- Premium open-source polish: issue templates, support guide, code of conduct, roadmap, skill-authoring guide, Dependabot config, and social preview asset.
- README release and validation badges.
- Project docs links for examples, roadmap, skill authoring, support, security, and conduct.

## [0.2.0] - 2026-05-14

### Added

- `pull-request-review-loop` for adversarial PR review, fix validation, and re-review.
- `qa-bug-hunt-planner` for source-backed QA discovery, audit tracks, and fix-ready bug tickets.
- Reusable reference templates and checklists for audit, spec, cleanup, status, QA, PR review, repo workflow, and mobile E2E runs.
- `skills` CLI install examples for compatible agent environments.
- AI-agent README guidance for loading `SKILL.md` and referenced files safely.

## [0.1.0] - 2026-05-14

### Added

- Initial Hawary Workflow Skills collection:
  - `project-status-dashboard`
  - `repo-workflow-checker`
  - `project-docs-cleanup`
  - `feature-spec-delivery-pipeline`
  - `cross-layer-contract-audit`
  - `mobile-maestro-e2e-orchestrator`
- Codex, Claude, and compatible-agent install guides.
- Example prompts and output shapes for each skill.
- Optional OpenAI/Codex `agents/openai.yaml` metadata for each skill.
- Validation workflow and local skill validator.
- Public contribution, security, ownership, and PR template files.
