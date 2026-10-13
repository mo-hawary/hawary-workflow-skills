# Codex-only version and protocol upgrade

## Current compatibility contract

- Tested Codex CLI version: **0.162.1**.
- Candidate family for deliberate upgrade: **>=0.162.1,<0.163.0**.
- Acceptance is intentionally stricter than the numeric range: only releases in
  `TESTED_CODEX_VERSIONS` execute. Other versions fail with upgrade instructions.
- The app-server generated schema is rechecked from the installed CLI before
  a new task and before a recovered supervisor starts.
- The older `exec` backend also runs only against a verified release.
  It never automatically replaces a failed app-server request.

## Deliberate upgrade procedure

1. Select a *stable* Codex CLI release and read its release notes. Do not
   silently accept preview or prerelease versions.
2. Install it in a separate testing environment; record its exact
   `codex --version` output. Do not change the production binary yet.
3. Run `codex app-server generate-json-schema --out <directory>` and compare
   the real generated client-request, server-request, and model/turn schemas
   with the previous tested version.
4. Add a new **versioned** directory under
   `tests/fixtures/fast_agents/protocol_schema/<version>/`. The existing
   0.162.1 fixture is reduced and synthetic; do not label it a real transcript.
5. Update the protocol adapter for any changed request method/field, approval
   response, service tier, sandbox, cancellation, or turn-notification behavior.
   Keep the old version fixture intact for regression coverage.
6. Run fake protocol tests on Linux, macOS, Windows. Inject late replies,
   transport stalls, cancellation, malformed JSON, and repeated steering.
7. With valid Codex credentials, run the authenticated live smoke on Linux and
   macOS. Review its **metadata-only** capture; do not commit raw prompts,
   model messages, full command lines, credentials, or filesystem paths.
8. Promote a reviewed sanitized capture to
   `tests/fixtures/fast_agents/transcripts/recorded/<version>/` and run
   the transcript replay suite. Windows live coverage is optional until a
   Windows credential and host are available.
9. Only then add the exact release to
   `fast_agents_protocol.TESTED_CODEX_VERSIONS`. If the release exceeds the
   candidate family, expand `CANDIDATE_CODEX_RANGE` with an explicit review
   and update the public compatibility docs.
10. Update the release notes and CI pin. Record tested OS, CLI SHA/version,
    requested and reported effective Fast tiers, and upstream execution
    verification separately.

## Required environment and troubleshooting

- Codex CLI must be discoverable on PATH or passed explicitly through
  `--codex`, and identify itself as a fixture-verified `codex-cli`.
- `CODEX_HOME` must point to an existing Codex configuration directory,
  or the default user Codex directory must exist. A valid `config.toml`,
  `auth.json`, or an API-key environment variable is required for active
  operations. Read-only status inspection may run without credentials.
- The `HWS_AGENT` variable, when set, must be `codex`. Certain known
  non-Codex environment markers are rejected. Environment variables are not
  cryptographic caller authentication; a local terminal with Codex access can
  still invoke the helper.
- If `doctor` reports a mismatched app-server schema or unsupported version,
  inspect it and update fixtures through this procedure; never disable the
  version gate or silently pick an alternative service tier.
- If startup or recovery cannot establish process identity, leave the task
  blocked and inspect the existing private state. Do not kill arbitrary PIDs.
- If Codex requires an unexpected approval, examine that exact live request
  and explicitly approve/decline it. No prompt is auto-approved by this helper.

## Live CI provisioning

The `codex-live-smoke.yml` workflow runs on main pushes, nightly, or manual
dispatch (not pull requests). Its Linux/macOS jobs require the repository
secret `CODEX_SMOKE_API_KEY`; missing authentication **fails** the job.
The workflow does not create or manage this secret. It installs Codex CLI
0.162.1 and uploads a seven-day metadata-only transcript artifact on completion.

To enforce a **required** live check in repository policy, an administrator must
configure the appropriate GitHub ruleset/branch-protection check. A scheduled
workflow alone does not create a required status check. Do not gate every PR
on a paid model call. Cross-platform fake fixtures remain the PR gate.
