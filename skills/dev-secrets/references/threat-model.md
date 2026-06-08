# Dev Secrets Threat Model

## Summary

`dev-secrets` reduces accidental exposure of local-development env values to agents, chats, summaries, diffs, generated reports, and casual logs.

It does not replace production secret managers, cloud-platform secret stores, team-wide rotation systems, enterprise vaults, or incident response.

## Coverage Table

| Threat | Covered? | Notes |
|---|---|---|
| Agent reading `.env` directly | Yes | Refuse direct reads; use CLI-only local handling |
| Secret in generated manifest | Yes | Manifest must be value-free and contain key metadata only |
| Key names revealing sensitive context | Partially | Names are visible to agent; user is disclosed upfront |
| Child process logging secrets | Partially | Wrap with `dev-secrets run --`; no guarantee on internal app logging |
| Malware or compromised machine | No | Rotate affected values and follow incident response |
| Committed `.env` history | No | Use `git filter-repo` or BFG after rotation; host caches, forks, and clones may persist |
| Production secret management | No | Use the platform or cloud secret manager |

## Protected

- Agent context and chat output.
- Known value-free manifests and example file presence.
- Local workflows where the agent only sees file names, counts, classifications, warnings, and key names allowed by compatible redacted CLI output or known value-free manifests.
- Wrapper-controlled output when known single-line values can be redacted.

## Not Protected

- A compromised live machine.
- A malicious or vulnerable child process that receives injected values.
- Sufficiently privileged local users or processes.
- Logs, crashes, telemetry, snapshots, subprocesses, shell history, or third-party services outside wrapper control.
- Multiline, transformed, hashed, truncated, encoded, or third-party-logged values that evade redaction.

## Safety Boundaries

- The agent must not directly read real env-file contents.
- A local CLI or vault backend may process real values through local prompts.
- A child process may receive values through its environment.
- If a value may have leaked, assume exposure and rotate it.

## Failure Policy

- Missing backend: fail closed with setup instructions.
- Unsafe permissions: fail closed where permissions can be enforced; warn clearly where platform support is limited.
- Tracked env files: stop and recommend rotation plus history cleanup before treating migration as safe.
- Unknown classification: use `unclassified — review before import`, never treat it as safe by default, and surface it in Warnings.
