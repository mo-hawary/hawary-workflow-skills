# Evidence Checklist

Use this to keep mobile E2E runs checkpointed and reproducible.

## Before The Run

- Platform and device/simulator ID.
- App flavor/build command.
- Test account or fixture setup.
- Maestro flow path.
- Log capture command.
- Backend/API trace source when relevant.

## Checkpoint Record

| Checkpoint | Command/Flow | Expected Signal | Evidence | Result |
| --- | --- | --- | --- | --- |
| Launch |  |  |  |  |
| Auth/setup |  |  |  |  |
| Main action |  |  |  |  |
| Persistence/API |  |  |  |  |
| Cleanup |  |  |  |  |

## Failure Classification

- App bug.
- Test bug.
- Test data/setup issue.
- Device/environment issue.
- Likely flake with retry evidence.

## Summary Shape

```md
## Mobile E2E Evidence

- Flow:
- Platform/device:
- Build/flavor:
- Result:
- Evidence:
- Failure class:
- Smallest next fix:
- Residual risk:
```
