---
name: project-docs-cleanup
description: Audit project docs for stale, duplicate, contradictory, or missing workflow artifacts before cleanup or handoff.
---

# Project Docs Cleanup

Audit first, edit only with approval. The goal is to make project docs match reality without erasing useful history.

## When To Use

Use this when the user wants to tidy, archive, reconcile, or prepare documentation for handoff.

Do not use this for a general current-state dashboard; use `project-status-dashboard`. Do not use it to check whether a repo has a full agent scaffold; use `repo-workflow-checker`.

## Workflow

1. Inventory docs:
   - `README.md`, `AGENTS.md`, `CLAUDE.md`, backlog files, bug files, specs, plans, release notes, architecture docs, and archives.
2. Compare docs to reality:
   - Git branches and recent commits.
   - Actual file paths.
   - Existing tests/build commands.
   - Completed or obsolete plans.
3. Classify findings:
   - Stale.
   - Duplicate.
   - Contradictory.
   - Missing.
   - Ready to archive.
   - Needs user decision.
4. Produce a cleanup plan before editing.
5. If approved, make minimal documentation edits and preserve history in an archive when appropriate.

For sample prompts and cleanup reports, see `references/examples.md`.
For archive and stale-doc decision rules, see `references/archive-policy.md`.

## Input Signals

Use active docs, recent commits, branch names, issue exports, spec indexes, release notes, TODO files, CI workflows, and actual file paths. Treat docs as candidates for cleanup, not proof of current behavior.

## Output

Return:

- **Doc Health:** concise overall state.
- **Stale Items:** docs or sections that no longer match repo reality.
- **Contradictions:** conflicting instructions or statuses.
- **Archive Candidates:** completed work that should move out of active docs.
- **Missing Docs:** gaps that would slow future agents or humans.
- **Proposed Edits:** exact files and intent.
- **Approval Needed:** yes/no before modifying files.

## Guardrails

- Do not delete project history unless explicitly approved.
- Do not change implementation, tests, migrations, generated files, or runtime config.
- Do not rewrite product strategy or user-facing wording unless asked.
- Prefer small, reviewable doc edits.

## Avoid

- Do not erase useful context just because it is old.
- Do not merge contradictory docs without naming the evidence that resolves the conflict.
- Do not move active plans into archive without checking current branch/work queue signals.

## Final Checks

- Each proposed edit names the file and reason.
- Completed work is archived or labeled, not silently erased.
- Contradictions are resolved only with evidence or user approval.
- Implementation files remain untouched unless explicitly approved.
