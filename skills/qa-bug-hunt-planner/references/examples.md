# Examples

## Recommend Audit Tracks

User:

```text
Use qa-bug-hunt-planner to audit checkout reliability. Tell me what you would test.
```

Output should scan source first, recommend audit tracks, and ask whether to run all if the user has not already approved deep execution.

## Build A Fix-Ready Hunt Plan

User:

```text
Use qa-bug-hunt-planner to run all useful audits for this admin surface and turn findings into tickets.
```

Output should include a findings matrix, fix board, impact gates, definitions of done, and follow-up tests.
