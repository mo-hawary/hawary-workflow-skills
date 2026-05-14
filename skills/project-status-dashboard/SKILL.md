---
name: project-status-dashboard
description: Build a read-only project health dashboard from git state, docs, CI hints, open work, branch drift, and likely next steps.
---

# Project Status Dashboard

Produce a read-only snapshot of the current repository. Do not edit files.

## When To Use

Use this when the user asks for status, a health check, a catch-up, current state, dashboard, or what is going on in a repo.

Do not use this for cleanup edits; use `project-docs-cleanup`. Do not use it for scaffold readiness; use `repo-workflow-checker`.

## Workflow

1. Load lightweight project context:
   - `AGENTS.md`, `CLAUDE.md`, or equivalent repo instructions when present.
   - `README.md`, backlog files, bug trackers, specs, plans, and release notes when present.
2. Inspect git state:
   - Current branch.
   - Working tree status.
   - Recent commits.
   - Upstream drift when a remote exists.
3. Inspect work queues:
   - Backlog, bugs, specs, TODO docs, issue exports, or local planning files.
   - Stale or contradictory status notes.
4. Inspect verification hints:
   - Package scripts, Makefile targets, CI workflow names, test folders, and recent local logs if available.
5. Return a concise dashboard.

For sample prompts and dashboard outputs, see `references/examples.md`.

## Output

Use this structure:

- **Now:** the most important current state in one or two sentences.
- **Branch:** current branch, dirty files, upstream drift if known.
- **Open Work:** active specs, backlog items, bugs, or PR-prep items.
- **Verification:** likely test/build commands and whether anything has recently run.
- **Risks:** stale docs, unclear state, broken workflow clues, or missing checks.
- **Next Move:** the smallest useful next action.

## Guardrails

- Never mark work as verified without evidence.
- Never treat docs as source of truth when code or git evidence contradicts them.
- If data is missing, say what is unknown instead of guessing.
- Do not run long or destructive commands.

## Final Checks

- The dashboard is read-only and does not propose broad refactors.
- Verification distinguishes likely commands from commands actually run.
- Unknowns are labeled clearly.
- The next move is small and immediately actionable.
