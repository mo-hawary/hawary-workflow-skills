# Scaffold Matrix

Use this to score agent workflow readiness.

| Area | Ready Signal | Weak Signal | Missing Signal |
| --- | --- | --- | --- |
| Agent instructions | `AGENTS.md`, `CLAUDE.md`, or equivalent with repo rules | Generic instructions only | No agent instructions |
| Work queue | Backlog, issues export, TODO, project board docs | Scattered TODOs | No visible queue |
| Specs/plans | Active spec/plan directory with index | Old or unindexed plans | No planning convention |
| Skills | Repo-local or documented user skills | Mentioned but not installable | None |
| Verification | Clear lint/test/build commands and CI | Partial commands | No check path |
| Review flow | PR template, owners, branch protection notes | Informal review notes | No review convention |
| Archive | Completed work has a home | Mixed active/history docs | No archive convention |

## Report Shape

```md
## Repo Workflow Readiness

- Status:
- Strongest signals:
- Highest-impact gaps:
- Suggested scaffold:
- Verification map:
- Approval needed:
```
