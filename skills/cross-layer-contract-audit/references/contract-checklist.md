# Contract Checklist

Use this when comparing two or more layers.

## Boundary Map

Record each side of the contract:

| Layer | Source | Contract Owner | Notes |
| --- | --- | --- | --- |
| Client/UI |  |  |  |
| API/service |  |  |  |
| Database/storage |  |  |  |
| Job/webhook/integration |  |  |  |
| Tests/CI |  |  |  |

## Field Checks

- Names and aliases.
- Type and encoding.
- Nullability and defaults.
- Required vs optional behavior.
- Enum/status values.
- Units, timezone, precision, and rounding.
- Sort, pagination, and filtering semantics.
- Error shape, status code, retryability, and user-visible message.

## Risk Checks

- Auth, authorization, tenancy, and ownership boundaries.
- Idempotency and duplicate submission behavior.
- Data migration or backfill compatibility.
- Backward compatibility for older clients.
- Observability, audit logging, and failure visibility.

## Finding Shape

```md
### <Severity> - <Contract mismatch>

- Boundary: <layer A> -> <layer B>
- Evidence:
  - `<path>`: <symbol/route/schema>
  - `<path>`: <symbol/route/schema>
- Impact:
- Fix direction:
- Verification:
```
