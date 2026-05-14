---
name: pull-request-review-loop
description: Review pull requests with adversarial findings, fix validation, re-review loops, local artifacts, and source-backed proof before merge.
---

# Pull Request Review Loop

Run PR review as a constructive attacker/defender loop until the branch is functionally proven or residual risk is explicit.

## When To Use

Use this when the user asks to review a PR, re-review after fixes, address review feedback, prove a branch is ready, or run a local review-fix loop before merge.

Do not use this for broad QA discovery; use `qa-bug-hunt-planner`. Do not use it for initial feature planning; use `feature-spec-delivery-pipeline`.

## Team Model

Use two roles, even if one agent performs both:

- **Reviewer:** skeptical, evidence-first, regression-focused.
- **Fixer:** revalidates every finding before changing code, fixes true issues, adds proof, and documents validation.

If the user authorizes subagents or parallel agents, split review lenses by changed surface. Otherwise, perform both roles locally.

## Input Signals

Use the local branch, base/head SHAs, PR diff, changed files, linked specs/issues, tests, CI status, review comments, migrations, security-sensitive surfaces, and prior fix commits. Prefer local source and commands over remote comments when possible.

## Workflow

1. Identify base/head, changed files, and review scope.
2. Run a reviewer pass focused on correctness, contracts, security, data, compatibility, tests, and second-order regressions.
3. Record only actionable findings with issue, evidence, impact, and recommended fix.
4. Revalidate each finding before fixing.
5. Fix valid findings only after the user has approved implementation changes.
6. Run targeted validation and update the local review artifact.
7. Re-review the latest head after every fix commit.
8. Stop only when the latest pass finds no blocker/high/medium correctness issues or the user accepts the remaining risk.

For sample prompts and report shape, see `references/examples.md`.
For a reusable local review artifact, see `references/review-artifact-template.md`.

## Output

Return:

- **PR/Branch:** identifier, base SHA, head SHA.
- **Rounds:** review/fix rounds completed.
- **Findings:** severity-ranked actionable findings or none.
- **Fix Proof:** commits and validation evidence for addressed findings.
- **Artifact:** local review artifact path when created.
- **Residual Risk:** untested areas or user-accepted risks.
- **Merge Readiness:** ready, not ready, or ready with accepted risk.

## Guardrails

- Do not post GitHub comments or submit reviews unless the user explicitly asks.
- Do not fix code unless the user approves implementation changes.
- Do not resolve findings by silence; require evidence or a clear non-reproducible result.
- Treat auth, payments, migrations, data loss, permissions, and destructive actions as high risk.

## Avoid

- Do not call a PR ready because CI is green without reviewing changed behavior.
- Do not produce vague style findings without a concrete correctness or maintainability risk.
- Do not merge while blocker/high findings are unresolved unless the user explicitly accepts the risk.
