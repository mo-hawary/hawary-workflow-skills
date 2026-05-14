# Examples

## Prompt

```text
Use cross-layer-contract-audit to check whether the new invoice status is consistent across the app, API, and database.
```

## Good Output Shape

- **Severity:** high
- **Contract:** API response status enum does not match client model enum.
- **Evidence:** client enum file, API serializer, and migration or schema file.
- **Impact:** users may see an unknown status or fail to submit updates.
- **Fix Direction:** align the source-of-truth enum and regenerate/update dependent layers.
- **Verification:** targeted API test plus client model or integration test.

## Prompt

```text
Audit the auth/session contract for this PR before implementation.
```

## Good Output Shape

List each boundary separately: browser/session storage, API middleware, token refresh route, database session table, and logout behavior. Mark missing evidence as unknown.
