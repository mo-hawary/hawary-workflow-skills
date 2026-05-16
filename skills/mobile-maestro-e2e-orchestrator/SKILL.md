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
3. Check whether Maestro is installed and the requested flow files are present before attempting execution:
   - If Maestro is missing or not configured, report the setup gap and the smallest install/configuration step needed.
   - Still produce a checkpoint-based test plan from the available app and flow context.
   - Do not claim E2E validation ran when only a plan was produced.
4. Start app logs before running the flow.
5. Run Maestro in short checkpoints:
   - Keep UI waits short.
   - Use logs or visible state to decide when to continue.
   - Capture failure screenshots and logs.
6. On failure:
   - Classify as app bug, test bug, setup issue, environment issue, or flake.
   - Re-run only the smallest useful checkpoint.
   - Propose a fix path with evidence.
7. Summarize proof and residual risk.

For sample prompts and evidence summaries, see `references/examples.md`.
For checkpoint and evidence capture guidance, see `references/evidence-checklist.md`.

## Input Signals

Use the flow name, platform, device or simulator IDs, app flavor, Maestro YAML files, test account setup, logs, screenshots, backend traces, and any known flaky step. If the platform or runner is missing, report the missing setup before attempting a run.

## Output

Return:

- **Flow:** scenario and platforms tested.
- **Setup:** devices, commands, app flavor, and test data assumptions.
- **Results:** pass/fail per checkpoint, or `not run` when setup is missing.
- **Evidence:** relevant logs, screenshots, routes, API status, state transitions, or setup-gap proof.
- **Diagnosis:** failure class and likely cause.
- **Next Fix:** smallest change or test adjustment needed.
- **Residual Risk:** untested platforms, flakes, missing assertions, or unavailable runner setup.

## Guardrails

- Do not rely on long arbitrary sleeps as proof.
- Do not treat a visible screen alone as full business-flow success when logs/API/state matter.
- Ask before changing app code, test flows, product behavior, or UI layout.
- Keep commands platform-specific and explicit.

## Avoid

- Do not rerun the full suite when one checkpoint is enough.
- Do not call a failure a flake without evidence from a retry or environment signal.
- Do not edit app code or test flows during diagnosis unless the user approves.
- Do not present a setup-only plan as a completed E2E run.

## Final Checks

- Each pass/fail result has evidence beyond a guess.
- Failures are classified before proposing fixes.
- Retries are targeted to the smallest useful checkpoint.
- Untested platforms, devices, app flavors, or missing runner setup are listed as residual risk.
