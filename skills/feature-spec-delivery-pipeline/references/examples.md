# Examples

## Prompt

```text
Use feature-spec-delivery-pipeline to plan saved searches before we code it.
```

## Good Output Shape

- **Summary:** short statement of the user goal and intended outcome.
- **Current Evidence:** source files, docs, schemas, APIs, and tests that define existing behavior.
- **Decisions Needed:** product, UX, naming, permissions, retention, or migration questions.
- **Proposed Contract:** request/response models, schema changes, events, and state transitions.
- **Implementation Plan:** small phases with dependencies.
- **Acceptance Criteria:** observable behaviors that can be tested.
- **Verification:** targeted unit, integration, E2E, and migration checks.
- **Risks and Rollback:** data, compatibility, and release risks.

## Prompt

```text
Turn these five related checkout bugs into one implementation spec.
```

## Good Output Shape

Group bugs by shared contract or workflow, identify duplicate symptoms, and separate confirmed defects from unclear product behavior.
