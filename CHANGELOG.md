# Changelog

All notable changes to Hawary Workflow Skills will be documented in this file.

This project follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and uses semantic version tags.

## [Unreleased]

### Added

- `dependency-security-auditor` for dependency CVE scans, freshness checks, and hook/CI audit design.
- Prompt examples for `dependency-security-auditor`.
- Fixture-backed bootcamp test matrix for dependency-auditor ecosystem coverage.
- CI coverage for the dependency-auditor Python tests and whitespace checks.
- Pinned CI validation inputs for GitHub Actions and hash-checked Python test dependencies.
- Egyptian Arabic README with language links from the English README.
- Current-state documentation in the README, roadmap, and skill authoring guide.

### Changed

- Hardened `dependency-security-auditor` package-manager detection, partial-status reporting, pnpm audit parsing, and Python requirements evidence notes.
- Added dependency-auditor setup hints and unsupported-tool handling for friendlier local installs.
- Added bootcamp fixtures for npm, pnpm, Yarn, Bun, Flutter/Dart, pinned and unpinned Python requirements, Python lockfiles, manifest-only Node, and stale mixed Node lockfiles.
- Expanded skill validation to check referenced files, agent metadata, local Markdown links, and README/example coverage.
- Tightened dependency-auditor status handling so findings from one scanner run cannot mask another run's scanner error.
- Scoped dependency-auditor OSV scans to discovered lockfiles so test fixtures and stale conflicting lockfiles do not create live dependency findings.
- Polished the Egyptian Arabic README intro for clearer, more natural wording.

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
