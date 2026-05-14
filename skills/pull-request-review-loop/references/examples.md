# Examples

## Review A Local Branch

User:

```text
Use pull-request-review-loop to review this branch before I open the PR.
```

Output should include base/head SHAs, changed-file review scope, findings, validation commands, and residual risk.

## Re-Review After Fixes

User:

```text
Use pull-request-review-loop to re-review PR 42 after the latest fixes. Do not post comments.
```

Output should inspect the latest head, revalidate prior findings if available, look for second-order regressions, and avoid GitHub comments unless explicitly requested.
