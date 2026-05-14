---
name: qa-bug-hunt-planner
description: Run source-backed QA discovery, recommend audit tracks, build fix-ready bug-hunt plans, and define verification gates.
---

# QA Bug Hunt Planner

Turn a fuzzy QA request into evidence-backed findings, audit tracks, fix tickets, and verification gates.

## When To Use

Use this when the user asks for QA, bug hunting, reliability review, test planning, impact analysis, mismatch review, or "what else should we audit?"

Do not use this for PR-specific review loops; use `pull-request-review-loop`. Do not use it for narrow field-level contract drift only; use `cross-layer-contract-audit`.

## Workflow

1. Scan the requested area before proposing tracks.
2. Recommend 3-8 audit tracks with short reasons.
3. Ask which tracks to run unless the user already said to run all.
4. Execute selected tracks with source evidence.
5. Convert findings into fix-ready tickets, definitions of done, impact gates, and tests.
6. Hand off to a spec or implementation workflow only after evidence is clear.

For sample prompts and report shape, see `references/examples.md`.
For a reusable bug-hunt plan template, see `references/qa-hunt-template.md`.

## Input Signals

Use source files, routes, screens, logs, test folders, issue reports, API schemas, screenshots, metrics, support reports, recent incidents, and known flaky areas. If the scope is broad, start with a bounded scan and recommend tracks before doing deep work.

## Output

Return:

- **Executive Read:** highest-risk area and why.
- **Audit Tracks:** what to run and why.
- **Findings Matrix:** severity, finding, evidence, affected surface, proposed ticket, test gap.
- **Fix Board:** now/next/later ordering.
- **Impact Gate:** checklist to complete before each fix.
- **Follow-Up Tests:** automated and manual verification paths.
- **Open Questions:** product or ownership decisions needed.

## Guardrails

- Do not invent bugs without evidence.
- Do not edit implementation code unless the user explicitly approves fixes.
- Do not skip the track recommendation step when the scope is fuzzy.
- Treat destructive actions, auth, payments, admin tools, migrations, and data integrity as high risk.

## Avoid

- Do not stop at a generic test plan when the user needs fix-ready tickets.
- Do not bury severity or affected surface in prose.
- Do not recommend a rewrite when a smaller stabilization ticket would prove or reduce the risk.
