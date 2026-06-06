# Dev Secrets Recovery Playbook

Use this when a local env value may have appeared in chat, logs, commits, screenshots, crash reports, telemetry, shell history, or a third-party service.

## Severity

| Scenario | Urgency | Response |
|---|---|---|
| Value may have entered agent chat or local logs | High | Rotate affected value and remove local copies where possible |
| Value may have entered a private repository commit | Critical | Rotate, clean history, and audit clones or forks |
| Value may have entered a public repository, issue, paste, or artifact | Critical | Rotate immediately, revoke sessions where supported, and treat old value as permanently exposed |
| Value may have reached production logs or telemetry | Critical | Rotate, notify the system owner, and follow incident response |

### If the repo is or was public, even briefly

Assume the value is permanently exposed. GitHub and similar hosts may retain caches, forks, mirrors, release artifacts, and clones that `git filter-repo` or BFG cannot reach. Rotate immediately and treat the old value as burned regardless of cleanup.

## Steps

1. Treat the value as exposed.
2. Regenerate the value in the provider or service that issued it.
3. Put the replacement in an ignored owner-only temporary env file.
4. Import the replacement through the local CLI.
5. Run the affected app or tool through the local wrapper.
6. Verify the workflow that depends on the value.
7. Delete the temporary plaintext file after verification.
8. Run a value-free scan for remaining exposure.
9. Clear shell history if the value may have been typed into a command.
10. If the old value was committed, follow repository history cleanup and assume the old value remains permanently exposed.

## Provider Rotation Patterns

- OpenAI project keys: create a replacement key in the provider console, update the local vault through the CLI, verify affected local commands, then revoke the old key.
- GitHub personal tokens: create a replacement token with the narrowest scopes needed, update the local vault, verify the workflow, then revoke the old token and active sessions where relevant.
- Supabase local development values: rotate database credentials or service credentials in the project dashboard where supported, update local vault entries, verify local app startup, then remove stale plaintext files.
- Stripe test-mode values: create replacement restricted keys where possible, update local vault entries, verify local payment flows in test mode, then revoke the old key.
- Database connection strings: rotate the database user password or create a replacement local-only user, update the vault, verify migrations/app startup, then revoke the old credential.

Keep provider-specific discussion value-free. Name the service and key label only; do not paste old or new values.

## Repository History Cleanup

Use history cleanup only after rotation. History rewrites do not make an exposed value safe again.

Common options:

```bash
git filter-repo --path .env --invert-paths
```

```bash
bfg --delete-files .env
```

After cleanup:

1. Force-push only with explicit repository-owner approval.
2. Ask collaborators to re-clone or carefully reset affected branches.
3. Remove exposed values from releases, artifacts, caches, and issue/PR comments if applicable.
4. Keep treating the old value as exposed forever.

## User-Facing Rule

Never ask the user to paste the old or new value into chat. Keep all recovery discussion value-free: names, locations, services, and actions only.
