# Examples

## Prompt

```text
Use project-status-dashboard. What is going on in this repo?
```

## Good Output Shape

- **Now:** the most important state in one or two sentences.
- **Branch:** current branch, dirty files, and upstream drift if known.
- **Open Work:** active specs, backlog items, bugs, or PR-prep items.
- **Verification:** commands likely available and commands actually run.
- **Risks:** stale docs, unclear state, broken workflow clues, or missing checks.
- **Next Move:** the smallest useful next action.

## Prompt

```text
Give me a health check before I start coding.
```

## Good Output Shape

Stay read-only. Prefer git status, recent commits, docs, and verification hints. Label unknown CI or test state instead of guessing.
