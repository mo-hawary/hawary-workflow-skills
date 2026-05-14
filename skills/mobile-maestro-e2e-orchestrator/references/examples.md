# Examples

## Prompt

```text
Use mobile-maestro-e2e-orchestrator to validate signup on iOS and Android.
```

## Good Output Shape

- **Flow:** signup happy path and one validation failure.
- **Setup:** app flavor, simulator/emulator IDs, Maestro flow names, test account assumptions.
- **Results:** pass/fail by checkpoint.
- **Evidence:** Maestro output, screenshots, device logs, API status, or persisted state.
- **Diagnosis:** app bug, test bug, setup issue, environment issue, or flake.
- **Next Fix:** smallest code or test-flow change needed.
- **Residual Risk:** untested platform, flaky assertion, or missing backend confirmation.

## Prompt

```text
Debug why this Maestro checkout flow is flaky.
```

## Good Output Shape

Start logs before rerunning, isolate the smallest failing checkpoint, compare visible UI with logs/state, and avoid broad retries until the failure class is known.
