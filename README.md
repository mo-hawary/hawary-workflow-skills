# Hawary Workflow Skills

Reusable workflow skills for coding agents.

[![Skills](https://img.shields.io/badge/Agent%20Skills-SKILL.md-2563eb)](docs/compatible-agents.md)
[![Codex](https://img.shields.io/badge/Codex-ready-111827)](docs/codex.md)
[![Claude](https://img.shields.io/badge/Claude-ready-d97706)](docs/claude.md)
[![License: MIT](https://img.shields.io/badge/License-MIT-10b981.svg)](LICENSE)

**Hawary Workflow Skills** packages repeatable engineering workflows for agents that understand the `SKILL.md` format: repo status, workflow readiness, docs cleanup, feature specs, cross-layer audits, and mobile E2E orchestration.

If these skills save you time, please star the repo. It helps other developers discover it.

## Use With Your Agent

Point your coding agent at this repository, or copy individual folders from `skills/` into the skill directory your agent uses.

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

## Skills

| Skill | Use When | Output |
| --- | --- | --- |
| [`project-status-dashboard`](skills/project-status-dashboard/SKILL.md) | You need to know what is going on in a repo. | Current state, branch/drift, open work, verification hints, risks, next move. |
| [`repo-workflow-checker`](skills/repo-workflow-checker/SKILL.md) | You want to know if a repo is ready for repeatable agent work. | Found workflow assets, gaps, suggested scaffold, verification map. |
| [`project-docs-cleanup`](skills/project-docs-cleanup/SKILL.md) | Docs, plans, backlog files, or specs may be stale. | Doc health, stale items, contradictions, archive candidates, proposed edits. |
| [`feature-spec-delivery-pipeline`](skills/feature-spec-delivery-pipeline/SKILL.md) | A feature or bug cluster needs a plan before coding. | Source-backed spec, decisions needed, contract changes, phases, acceptance criteria. |
| [`cross-layer-contract-audit`](skills/cross-layer-contract-audit/SKILL.md) | Client, API, database, jobs, or docs may disagree. | Severity-ranked contract mismatches with evidence, impact, fix direction, verification. |
| [`mobile-maestro-e2e-orchestrator`](skills/mobile-maestro-e2e-orchestrator/SKILL.md) | You need evidence-driven mobile E2E testing with Maestro. | Flow setup, checkpoint results, logs/screenshots/state evidence, diagnosis, residual risk. |

## Choosing A Skill

| If you are thinking... | Use |
| --- | --- |
| "What is the state of this repo?" | `project-status-dashboard` |
| "Is this repo set up well for agents?" | `repo-workflow-checker` |
| "Which docs are stale?" | `project-docs-cleanup` |
| "Plan this feature before implementation." | `feature-spec-delivery-pipeline` |
| "Do these layers agree?" | `cross-layer-contract-audit` |
| "Prove this mobile flow works." | `mobile-maestro-e2e-orchestrator` |

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

`SKILL.md` is the portable source. `references/` keeps examples out of the main skill body. `agents/openai.yaml` is optional OpenAI/Codex UI metadata; other agents can ignore it.

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

## Changelog

See [CHANGELOG.md](CHANGELOG.md).

## License

MIT. See [LICENSE](LICENSE).
