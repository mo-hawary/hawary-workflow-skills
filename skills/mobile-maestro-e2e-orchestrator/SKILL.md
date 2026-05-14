---
name: mobile-maestro-e2e-orchestrator
description: Run evidence-driven mobile E2E tests with Maestro, device logs, checkpoints, and targeted retries for iOS or Android flows.
---

# Mobile Maestro E2E Orchestrator

Run mobile E2E tests using evidence, not blind waiting. Prefer small checkpoints, logs, and targeted retries.

## When To Use

Use this when Maestro is the test runner or when the user asks to validate mobile flows with device logs and checkpoints.

Do not use this for generic web QA, unit tests, or non-Maestro mobile frameworks unless the user wants the workflow adapted.

## Workflow

1. Identify the target flow and platforms.
2. Discover app run commands, device IDs, Maestro flows, and test data setup.
3. Start app logs before running the flow.
4. Run Maestro in short checkpoints:
   - Keep UI waits short.
   - Use logs or visible state to decide when to continue.
   - Capture failure screenshots and logs.
5. On failure:
   - Classify as app bug, test bug, setup issue, environment issue, or flake.
   - Re-run only the smallest useful checkpoint.
   - Propose a fix path with evidence.
6. Summarize proof and residual risk.

For sample prompts and evidence summaries, see `references/examples.md`.

## Output

Return:

- **Flow:** scenario and platforms tested.
- **Setup:** devices, commands, app flavor, and test data assumptions.
- **Results:** pass/fail per checkpoint.
- **Evidence:** relevant logs, screenshots, routes, API status, or state transitions.
- **Diagnosis:** failure class and likely cause.
- **Next Fix:** smallest change or test adjustment needed.
- **Residual Risk:** untested platforms, flakes, or missing assertions.

## Guardrails

- Do not rely on long arbitrary sleeps as proof.
- Do not treat a visible screen alone as full business-flow success when logs/API/state matter.
- Ask before changing app code, test flows, product behavior, or UI layout.
- Keep commands platform-specific and explicit.

## Final Checks

- Each pass/fail result has evidence beyond a guess.
- Failures are classified before proposing fixes.
- Retries are targeted to the smallest useful checkpoint.
- Untested platforms, devices, or app flavors are listed as residual risk.
