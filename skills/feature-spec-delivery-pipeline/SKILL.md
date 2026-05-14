---
name: feature-spec-delivery-pipeline
description: Turn a feature request or bug cluster into a source-backed spec, impact map, acceptance criteria, and delivery plan before coding.
---

# Feature Spec Delivery Pipeline

Convert an idea into a buildable, reviewable plan before implementation.

## When To Use

Use this before multi-file implementation, cross-layer changes, migrations, or user-flow changes.

Do not use this for a read-only health snapshot; use `project-status-dashboard`. Do not use it for a narrow contract mismatch review; use `cross-layer-contract-audit`.

## Workflow

1. Clarify the request:
   - Goal.
   - In-scope and out-of-scope behavior.
   - Product decisions that need user approval.
2. Map existing contracts:
   - Relevant UI, API, database, jobs, integrations, tests, and docs.
3. Identify risk:
   - Auth, payments, sessions, migrations, data loss, compatibility, and user-facing workflow changes.
4. Write the spec:
   - Problem.
   - Current behavior.
   - Proposed behavior.
   - Affected files/components.
   - Contract changes.
   - Implementation phases.
   - Acceptance criteria.
   - Verification plan.
   - Rollout and rollback notes when relevant.
5. Ask for approval before implementation when behavior, layout, copy, or data contracts are not settled.

For sample prompts and spec shape, see `references/examples.md`.

## Output

Use this structure:

- **Summary**
- **Current Evidence**
- **Decisions Needed**
- **Proposed Contract**
- **Implementation Plan**
- **Acceptance Criteria**
- **Verification**
- **Risks and Rollback**

## Guardrails

- Do not invent requirements when evidence is ambiguous.
- Do not make product, UI, or layout decisions without approval.
- Do not edit implementation code unless the user explicitly asks you to proceed.
- Keep the plan narrow enough to review.

## Final Checks

- The spec separates evidence from assumptions.
- Every decision that needs user approval is explicit.
- Acceptance criteria are testable.
- Verification covers the highest-risk changed contracts.
