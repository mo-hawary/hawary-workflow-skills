# Dev Secrets Output Templates

Use these canonical shapes for value-free generated outputs. Do not include real values, partial values, hashes, fingerprints, encrypted blobs, or passphrase hints.

## `.env.example`

```dotenv
# Generated value-free example. Fill values through local vault workflow, not chat.
DATABASE_URL=
PUBLIC_APP_ORIGIN=
FEATURE_FLAG_NAME=
```

Rules:

- Include key names only when generated from compatible redacted CLI output or a known value-free manifest.
- Leave values blank.
- Comments may describe purpose, environment, or required/optional status.
- Public-prefixed keys are still reviewed; public exposure does not make a value safe.

## `secrets.manifest.json`

```json
{
  "version": 1,
  "keys": [
    {
      "name": "DATABASE_URL",
      "required": true,
      "environment": "local",
      "classification": "secret",
      "description": "Local database connection"
    },
    {
      "name": "PUBLIC_APP_ORIGIN",
      "required": false,
      "environment": "local",
      "classification": "publicly_exposed",
      "description": "Local app origin used by browser-visible code"
    }
  ]
}
```

Allowed classifications:

- `secret`
- `publicly_exposed`
- `needs_review`
- `non_secret_config`
- `unclassified — review before import`

## Value-Free Plan

```txt
Status: needs setup
Safe Evidence: CLI not found; 2 env files detected by name; no values read
Warnings: workflow-only skill; no CLI is installed by default; generated outputs must stay value-free
Next Command: install or implement a compatible local CLI before importing secrets; do not run `dev-secrets ...` until `command -v dev-secrets` succeeds
Approval Needed: import target selection and any file mutation
```

## Compatible CLI Plan

```txt
Status: needs user choice
Safe Evidence: 2 env files detected by name; no values read
Warnings: choose one import target; generated outputs must stay value-free
Next Command: confirm the import target, then run a compatible value-free import plan for the selected file
Approval Needed: import target selection
```
