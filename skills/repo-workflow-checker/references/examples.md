# Examples

## Prompt

```text
Use repo-workflow-checker to see if this repo is ready for AI-assisted development.
```

## Good Output Shape

- **Status:** ready, partially ready, or missing scaffold.
- **Found:** existing instructions, work queues, specs, skills, and verification commands.
- **Gaps:** missing or weak assets ordered by impact.
- **Suggested Scaffold:** exact files or directories to add.
- **Verification Map:** likely build, lint, test, and format commands.
- **Approval Needed:** changes required to bring the repo up to standard.

## Prompt

```text
Check whether this repo has a healthy agent workflow setup.
```

## Good Output Shape

Do not require one vendor. Recognize `AGENTS.md`, `CLAUDE.md`, `.agents/skills`, `.claude/skills`, and equivalent local conventions.
