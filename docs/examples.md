# Examples

Use these prompts after installing or pointing an agent at this repository.

## Repo Status

```text
Use project-status-dashboard to summarize this repository and recommend the next useful action.
```

## Workflow Readiness

```text
Use repo-workflow-checker to audit whether this repo is ready for repeatable agent work.
```

## Docs Cleanup

```text
Use project-docs-cleanup to audit stale docs and propose cleanup without editing files yet.
```

## Feature Planning

```text
Use feature-spec-delivery-pipeline to turn this feature request into a source-backed spec before implementation.
```

## Contract Audit

```text
Use cross-layer-contract-audit to check whether the UI, API, database, jobs, and docs agree for this feature.
```

## PR Review

```text
Use pull-request-review-loop to review this PR locally. Do not post GitHub comments.
```

## QA Bug Hunt

```text
Use qa-bug-hunt-planner to scan this area, recommend audit tracks, and create fix-ready tickets.
```

## Mobile E2E

```text
Use mobile-maestro-e2e-orchestrator to validate this mobile flow with checkpoints, logs, and targeted retries.
```

## Dependency Security

```text
Use dependency-security-auditor to scan this repo's dependencies and recommend pre-push or CI security checks.
```

## Fast Codex Subagents

Explicit invocation:

```text
Use $fast-codex-subagents to implement this bounded change with one writer and one read-only reviewer. Run all workers in Fast mode.
```

Natural-language trigger:

```text
Split this implementation and its review across a small Codex team. Give each writer separate files, agree on the shared API first, and keep the reviewer read-only.
```

The skill chooses the smallest useful team and its recommended settings automatically. Resolve settings in this order: explicit user choice, saved defaults, project/user rules, then recommendation. Use Fast for all workers when no setting was specified, and ask only about unresolved conflicts or unavailable requested options. Read [the task manifest guide](../skills/fast-codex-subagents/references/manifest-guide.md), inspect the local CLI, and show the team and resolved settings before launching. `doctor` checks schema and model/service-tier availability without inference:

```bash
python3 skills/fast-codex-subagents/scripts/fast_agents.py doctor
```

The preferred backend is one Codex app-server stdio process with separate logical worker threads. It requires Codex CLI 0.162.1 or later. 0.162.1 is the tested release; newer releases are accepted with a warning, and please report anything that breaks. `exec` is an explicit fallback and is never selected silently. Fake-server tests do not make model calls.

For opt-in skill installation, saved defaults, instruction setup, and safe rollback commands, see [the setup guide](../skills/fast-codex-subagents/references/setup-and-rollback.md).
