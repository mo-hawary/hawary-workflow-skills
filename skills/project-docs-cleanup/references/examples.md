# Examples

## Prompt

```text
Use project-docs-cleanup to prepare this repo for handoff.
```

## Good Output Shape

- **Doc Health:** concise overall state.
- **Stale Items:** files or sections that contradict code, git, or current docs.
- **Contradictions:** conflicting instructions or statuses.
- **Archive Candidates:** completed work that should move out of active docs.
- **Missing Docs:** gaps that would slow handoff.
- **Proposed Edits:** exact files and intent.
- **Approval Needed:** yes/no before changes.

## Prompt

```text
Clean up completed plans and stale TODO docs.
```

## Good Output Shape

Audit first. Preserve history by archiving or labeling completed work. Do not delete useful context without explicit approval.
