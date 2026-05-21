# Roadmap

Hawary Workflow Skills is intentionally small and practical. The goal is to keep each skill portable, inspectable, and useful across Codex, Claude, and compatible agents.

## Current State

- Public MIT-licensed repository with protected `main` and squash-merged pull requests.
- Canonical skills live under `skills/` and use portable `SKILL.md` files.
- Current skill coverage includes repo status, workflow readiness, docs cleanup, feature specs, contract audits, PR review loops, QA bug hunts, and Maestro-based mobile E2E orchestration.
- Install docs cover Codex, Claude, and compatible Agent Skills consumers.
- English and Egyptian Arabic READMEs are available at the repository root.
- CI validates skill metadata, tracked references, local links, Python tests, whitespace, and public-release hygiene on pull requests.

## Near Term

- Add more public-safe workflow skills from repeated real-world usage.
- Expand compatibility notes for Codex, Claude Code, and other Agent Skills consumers.
- Add lightweight smoke tests for skill folder structure and referenced files.
- Add behavior-oriented skill smoke tests that check output shape from sample prompts without depending on private projects.
- Improve social preview and project examples as the collection grows.

## Later

- Package stable skill bundles for agent-specific marketplaces when the ecosystem settles.
- Add a skill authoring checklist that validates examples, metadata, references, and install docs.
- Track compatibility reports from the community.
- Publish a dedicated project page on mohawary.com.

## Not Planned

- Vendor-specific rewrites of the same skill body.
- Hidden network calls or install-time execution.
- Private project assumptions in public skills.
