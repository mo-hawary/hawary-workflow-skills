# QA Hunt Template

Use this when converting audit evidence into a fix-ready plan.

```md
# <Area> QA Bug Hunt

## Executive Read

## Audit Tracks

| Track | Reason | Status |
| --- | --- | --- |

## Findings Matrix

| ID | Severity | Finding | Evidence | Affected Surface | Proposed Ticket | Test Gap |
| --- | --- | --- | --- | --- | --- | --- |

## Fix Board

### Now

### Next

### Later

## Impact Gate

- Code touched:
- Data/contract touched:
- Downstream UI/API:
- Logging/audit effect:
- Error/retry/refresh behavior:
- Test proof:

## Definition Of Done

| Ticket | Done When | Validation |
| --- | --- | --- |

## Follow-Up Test Plan

- Automated:
- Manual:
- Regression:

## Hunt Log

### YYYY-MM-DD - <Ticket>

- Hypothesis:
- Red test:
- Fix summary:
- Green test:
- Manual smoke:
- Residual risk:
```
