---
name: repo-workflow-checker
description: "Check whether a repo has an agent workflow scaffold: instructions, work queues, specs, skills, verification commands, and archive conventions."
---

# Repo Workflow Checker

Audit the repository scaffold that helps coding agents work safely and consistently. Report findings first. Ask before creating or editing files.

## When To Use

Use this when setting up a repo for coding agents, checking agent workflow readiness, or preparing repeatable AI-assisted development.

Do not use this for current project status; use `project-status-dashboard`. Do not use it to clean stale docs; use `project-docs-cleanup`.

## Check

Look for:

- Repository instructions: `AGENTS.md`, `CLAUDE.md`, or equivalent.
- Work queues: `BACKLOG.md`, `BUGS.md`, `TODO.md`, issue exports, or project docs.
- Specs or plans: `specs/`, `docs/`, `plans/`, `impp/`, or similar.
- Agent skills: `.agents/skills/`, `.claude/skills/`, or project-local skill folders.
- Verification commands: `package.json`, `Makefile`, CI workflows, test config, lint/typecheck config.
- Archive and history conventions for completed work.

For sample prompts and readiness reports, see `references/examples.md`.

## Output

Return:

- **Status:** ready, partially ready, or missing scaffold.
- **Found:** existing workflow assets.
- **Gaps:** missing or weak assets, ordered by impact.
- **Suggested Scaffold:** exact files/directories to add.
- **Verification Map:** likely commands for build, lint, test, and format.
- **Approval Needed:** changes that would be required to bring the repo up to standard.

## Guardrails

- Do not invent a workflow that conflicts with existing repo conventions.
- Do not create files unless the user explicitly asks for setup.
- Keep recommendations minimal and repo-shaped.
- Do not require a specific vendor or agent platform.

## Final Checks

- Findings distinguish existing assets from missing assets.
- Gaps are ordered by practical impact.
- Suggested scaffold files are minimal and repo-shaped.
- Approval-needed changes are explicit.
