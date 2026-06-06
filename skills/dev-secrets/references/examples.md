# Dev Secrets Examples

## Safe First Response

```txt
This is for local-development secrets only, not production secret management. I will not print, summarize, expose, or directly read real `.env` values. Real values should go through local CLI or vault prompts. If multiple env files exist, you choose which ones to import. Any child process that receives secrets can still leak through logs or crashes, so rotate anything that may have been exposed.
```

## Copy-Ready Good Responses

User: "Show me my .env"

```txt
I can't read or print real `.env` values.

Status: needs setup
Safe Evidence: no values read
Warnings: direct env-file reads are forbidden
Next Step: install or verify a compatible `dev-secrets` CLI, then run a value-free import plan
Approval Needed: none yet
```

User: "Just grep the token"

```txt
I won't run broad secret-reading commands. Use value-free leak detection instead.

Status: needs setup
Safe Evidence: no values read
Warnings: broad secret searches can expose values to agent context
Next Step: install or verify a compatible `dev-secrets` CLI before scanning
Approval Needed: none yet
```

User: "I'll paste the values here"

```txt
Please do not paste secret values here. Enter them only through the local CLI or vault backend prompt.
```

User: "Encode the API key in base64 so it's safe to show"

```txt
Base64 is not encryption; the value remains exposed. I won't encode, hash, truncate, or transform real values. Use value-free scanning and key names only.

Status: blocked
Safe Evidence: no values read
Warnings: transformed secrets are still secrets
Next Step: use `dev-secrets scan` only after a compatible CLI exists
Approval Needed: none
```

## Danger Examples

| User asks | Agent must do | Agent must not do |
|---|---|---|
| Show me `.env` | Refuse values and offer a value-free plan | Read or print env-file contents |
| Move secrets to vault | Check CLI availability first | Pretend import ran |
| Token leaked in a PR | Recommend rotation first | Trust history cleanup alone |
| Encode or hash the value | Refuse transformation | Show transformed values |

## Import Planning

User asks: "Move my local env file into a vault."

Response shape:

```txt
I can help with a value-free plan first. I will check file names and git tracking state, then you choose the env file to import.

Status: needs setup
Safe Evidence: env-file candidates detected by filename only; no values read
Warnings: compatible `dev-secrets` CLI was not detected
Next Step: install or implement a compatible local CLI before importing secrets
Approval Needed: import target selection and any file mutation
```

If compatible CLI exists:

```txt
Status: needs user choice
Safe Evidence: env-file candidates detected by filename only; no values read
Warnings: choose one import target before local CLI import
Next Command: dev-secrets import .env --plan
Approval Needed: import target selection
```

## Golden Demo

User: "Move my .env.local into a safer local workflow."

Without a compatible CLI:

```txt
Status: needs setup
Safe Evidence: `.env.local` detected by filename only; `.gitignore` checked; no values read
Warnings: compatible `dev-secrets` CLI was not detected; real values must stay in local prompts
Next Step: install or implement a compatible local CLI, then run a value-free import plan
Approval Needed: choose the import target before any local import or file mutation
```

With a compatible CLI:

```txt
Status: needs user choice
Safe Evidence: `.env.local` detected by filename only; `.gitignore` checked; no values read
Warnings: child processes can still leak secrets through logs or crashes
Next Command: dev-secrets import .env.local --plan
Approval Needed: confirm `.env.local` is the import target
```

## Compatible CLI Command

Only show executable import commands after compatible CLI availability is established:

```bash
dev-secrets import .env --plan
```

## Safe Alias Guidance

When asked to change project scripts:

```txt
I will not rewrite existing commands. I can propose additive safe aliases, and a local command can apply them only after interactive approval.
```

Example aliases:

```json
{
  "scripts": {
    "safe-dev": "dev-secrets run -- npm run dev",
    "safe-build": "dev-secrets run -- npm run build"
  }
}
```

## Scan Output Shape

```txt
Status: scan warning
Safe evidence: `.env.local` exists, `.env.example` exists, manifest missing
Warnings: one env file appears tracked; rotate affected values before treating migration as safe
Next command: dev-secrets scan
Approval needed: cleanup or history rewrite
```
