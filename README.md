# Hawary Workflow Skills

Reusable workflow skills for coding agents, AI pair programmers, and teams that want source-backed delivery habits.

[![Skills](https://img.shields.io/badge/Agent%20Skills-SKILL.md-2563eb)](docs/compatible-agents.md)
[![Codex](https://img.shields.io/badge/Codex-ready-111827)](docs/codex.md)
[![Claude](https://img.shields.io/badge/Claude-ready-d97706)](docs/claude.md)
[![Release](https://img.shields.io/github/v/release/mo-hawary/hawary-workflow-skills?color=0ea5e9)](https://github.com/mo-hawary/hawary-workflow-skills/releases)
[![Validate](https://github.com/mo-hawary/hawary-workflow-skills/actions/workflows/validate.yml/badge.svg)](https://github.com/mo-hawary/hawary-workflow-skills/actions/workflows/validate.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-10b981.svg)](LICENSE)

**Hawary Workflow Skills** packages repeatable engineering workflows for agents that understand the `SKILL.md` format: repo status, workflow readiness, docs cleanup, feature specs, cross-layer audits, PR review loops, QA bug hunts, and mobile E2E orchestration.

If these skills save you time, please star the repo. It helps other developers discover it.

## Use With Your Agent

Point your coding agent at this repository or copy individual folders from `skills/` into the skill directory your agent uses. If your environment supports the `skills` CLI, you can use that as an optional shortcut.

| Agent | Guide | Install target |
| --- | --- | --- |
| Codex | [Install on Codex](docs/codex.md) | `.agents/skills`, `~/.agents/skills`, or your Codex skills directory |
| Claude | [Install on Claude](docs/claude.md) | `.claude/skills`, `~/.claude/skills`, or Claude custom Skills upload |
| OpenClaw, Qwen, and compatible agents | [Install on other agents](docs/compatible-agents.md) | any Agent Skills-compatible directory |

Quick copy example:

```bash
cp -R skills/project-docs-cleanup /path/to/agent/skills/
```

Windows PowerShell:

```powershell
Copy-Item -Recurse skills\project-docs-cleanup C:\path\to\agent\skills\
```

Optional `skills` CLI path when your agent supports it:

```bash
npx skills add mo-hawary/hawary-workflow-skills --list
npx skills add mo-hawary/hawary-workflow-skills --skill project-docs-cleanup
```

If `npx skills` is not available in your environment or does not support this repository, use the copy method above.

## Skills

| Skill | Use When | Output |
| --- | --- | --- |
| [`project-status-dashboard`](skills/project-status-dashboard/SKILL.md) | You need to know what is going on in a repo. | Current state, branch/drift, open work, verification hints, risks, next move. |
| [`repo-workflow-checker`](skills/repo-workflow-checker/SKILL.md) | You want to know if a repo is ready for repeatable agent work. | Found workflow assets, gaps, suggested scaffold, verification map. |
| [`project-docs-cleanup`](skills/project-docs-cleanup/SKILL.md) | Docs, plans, backlog files, or specs may be stale. | Doc health, stale items, contradictions, archive candidates, proposed edits. |
| [`feature-spec-delivery-pipeline`](skills/feature-spec-delivery-pipeline/SKILL.md) | A feature or bug cluster needs a plan before coding. | Source-backed spec, decisions needed, contract changes, phases, acceptance criteria. |
| [`cross-layer-contract-audit`](skills/cross-layer-contract-audit/SKILL.md) | Client, API, database, jobs, or docs may disagree. | Severity-ranked contract mismatches with evidence, impact, fix direction, verification. |
| [`pull-request-review-loop`](skills/pull-request-review-loop/SKILL.md) | A PR or branch needs adversarial review, fix validation, and re-review before merge. | Findings, fix proof, review rounds, validation, residual risk, merge readiness. |
| [`qa-bug-hunt-planner`](skills/qa-bug-hunt-planner/SKILL.md) | A feature area needs QA discovery, audit tracks, and fix-ready tickets. | Audit tracks, findings matrix, fix board, impact gates, test plan. |
| [`mobile-maestro-e2e-orchestrator`](skills/mobile-maestro-e2e-orchestrator/SKILL.md) | You need evidence-driven mobile E2E testing with Maestro. | Flow setup, checkpoint results, logs/screenshots/state evidence, diagnosis, residual risk. |

## Choosing A Skill

| If you are thinking... | Use |
| --- | --- |
| "What is the state of this repo?" | `project-status-dashboard` |
| "Is this repo set up well for agents?" | `repo-workflow-checker` |
| "Which docs are stale?" | `project-docs-cleanup` |
| "Plan this feature before implementation." | `feature-spec-delivery-pipeline` |
| "Do these layers agree?" | `cross-layer-contract-audit` |
| "Review this PR without posting comments." | `pull-request-review-loop` |
| "What should we QA before fixing?" | `qa-bug-hunt-planner` |
| "Prove this mobile flow works." | `mobile-maestro-e2e-orchestrator` |

## Examples

See [docs/examples.md](docs/examples.md) for copy/paste prompts for each skill.

## Structure

```text
skills/
`-- skill-name/
    |-- SKILL.md
    |-- agents/
    |   `-- openai.yaml
    `-- references/
        `-- examples.md
```

`SKILL.md` is the portable source. These skills are Markdown-first by design; Ruby is used only for repository validation. `references/` keeps examples, checklists, and templates out of the main skill body. `agents/openai.yaml` is optional OpenAI/Codex UI metadata; other agents can ignore it.

## For AI Agents

When a developer points you at this repository, read the relevant folder under `skills/`, then load only the referenced files you need from that skill's `references/` directory. If a referenced file is missing, report the gap and continue from the main `SKILL.md`. Do not assume project-specific paths, products, secrets, or private company rules. Ask before editing implementation code when a skill says report-first or approval-required.

## Adding Skills

Use the same workflow for every new skill:

1. Extract the reusable behavior from real usage.
2. Generalize names, paths, and examples.
3. Keep `SKILL.md` concise and move templates/checklists into `references/`.
4. Add `agents/openai.yaml` metadata.
5. Update this README, docs, and changelog.
6. Run validation before opening a PR.

## Tested With

- macOS and Linux copy commands
- Windows PowerShell copy commands
- Codex-style `.agents/skills` installs
- Claude-style `.claude/skills` installs
- Generic Agent Skills folder layouts

## Validate

Run the same checks used by CI:

```bash
ruby scripts/validate_skills.rb
```

The validator checks skill frontmatter, description length, naming, symlinks, and common public-release hygiene patterns.

## References

- [Claude Code skills](https://docs.claude.com/en/docs/claude-code/skills)
- [Claude Agent Skills](https://docs.claude.com/en/docs/agents-and-tools/agent-skills)
- [Skills CLI](https://skills.sh/docs/cli)
- [Vercel Agent Skills directory](https://vercel.com/docs/agent-resources/skills)

## Project Docs

- [Examples](docs/examples.md)
- [Roadmap](docs/roadmap.md)
- [Skill authoring workflow](docs/skill-authoring.md)
- [Contributing](CONTRIBUTING.md)
- [Support](SUPPORT.md)
- [Security](SECURITY.md)
- [Code of Conduct](CODE_OF_CONDUCT.md)

## Changelog

See [CHANGELOG.md](CHANGELOG.md).

## License

MIT. See [LICENSE](LICENSE).
