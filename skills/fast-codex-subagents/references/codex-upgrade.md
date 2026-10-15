# Codex-only version and protocol upgrade

## Current compatibility contract

- Requires Codex CLI 0.162.1 or later. 0.162.1 is the tested release; newer
  releases are accepted with a warning, and please report anything that breaks.
- Releases older than 0.162.1, and an unreadable version, are rejected.
- A newer release than the tested one, including prereleases, runs. `doctor`
  and launch print a warning asking users to report any problem at
  <https://github.com/mo-hawary/hawary-workflow-skills/issues/new?template=compatibility_report.yml>
  with the output of `fast_agents.py doctor --json`.
- `doctor` still checks that the installed CLI's generated app-server schema
  has the methods and request shapes the helper needs. That is a real
  compatibility check, not a version list. If it fails, the message asks the
  user to report it the same way.
- The app-server generated schema is rechecked from the installed CLI before
  a new task and before a recovered supervisor starts.
- The older `exec` backend runs against the installed CLI under the same
  version rules. It never automatically replaces a failed app-server request.

## Marking a newer release as tested

Use this procedure when a maintainer wants a newer Codex CLI release to be
recorded as tested. Adding the exact release to `TESTED_CODEX_VERSIONS`
removes its warning. It is a maintainer step, not a gate for users.

1. Select a *stable* Codex CLI release and read its release notes. Do not
   silently accept preview or prerelease versions as tested.
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
9. Add the exact release to `fast_agents_protocol.TESTED_CODEX_VERSIONS`,
   which removes the newer-release warning for it, and update the public
   compatibility docs and the tested version named above.
10. Update the release notes and CI pin. Record tested OS, CLI SHA/version,
    requested and reported effective Fast tiers, and upstream execution
    verification separately.

## Handling a compatibility report

When a user reports a problem on a newer, untested release:

1. Ask for the report's `fast_agents.py doctor --json` output and the exact
   `codex --version`. Do not ask for prompts, transcripts, or credentials.
2. Reproduce the failure on that release in a separate testing environment,
   then decide whether it is a schema change (update the adapter and fixtures
   as in steps 3 to 8 above) or an isolated bug.
3. Mark the release as tested only after the fixes and the fake and live checks
   above pass. Until then, keep the warning and say which behavior is affected.
4. Never disable the version check or silently pick another service tier to
   work around a report.

## Required environment and troubleshooting

- Codex CLI must be discoverable on PATH or passed explicitly through
  `--codex`, and identify itself as `codex-cli` 0.162.1 or later.
- `CODEX_HOME` must point to an existing Codex configuration directory,
  or the default user Codex directory must exist. A valid `config.toml`,
  `auth.json`, or an API-key environment variable is required for active
  operations. Read-only status inspection may run without credentials.
- The `HWS_AGENT` variable, when set, must be `codex`. Certain known
  non-Codex environment markers are rejected. Environment variables are not
  cryptographic caller authentication; a local terminal with Codex access can
  still invoke the helper.
- If `doctor` reports a mismatched app-server schema or a version older than
  0.162.1, inspect it and update fixtures through this procedure. For a newer
  release with a schema problem, follow "Handling a compatibility report" above.
  Never disable the version check or silently pick an alternative service tier.
- If startup or recovery cannot establish process identity, leave the task
  blocked and inspect the existing private state. Do not kill arbitrary PIDs.
- If Codex requires an unexpected approval, examine that exact live request
  and explicitly approve/decline it. No prompt is auto-approved by this helper.

## Live CI provisioning

The `codex-live-smoke.yml` workflow is **ChatGPT-login only** and needs
**no API key or GitHub secret**. It cannot run a paid/subscription-backed model
turn on an unauthenticated ephemeral GitHub-hosted runner.

To run it, provision a **trusted, dedicated self-hosted runner** on Linux or
macOS, with labels `self-hosted`, `codex-authenticated` and the appropriate
GitHub OS label (`Linux` or `macOS`). Use a dedicated OS account, install
the tested Codex CLI 0.162.1 (or the release under validation), sign into ChatGPT interactively once
with `codex login`, and verify `codex login status` **as the runner user**.
Protect its login files/keychain and keep it away from untrusted code. The
workflow refuses API-key environment variables. Before enabling the runner:

- In the repository Actions settings, set "Require approval for all outside
  collaborators" so fork PR workflows never start automatically.
- Why: any `pull_request` workflow can set `runs-on` to the self-hosted labels
  and then read the runner user's Codex login. Treat the runner as a credential.
- Prefer an ephemeral or just-in-time runner that is discarded after one job.
- Run the runner under a dedicated OS user that holds only the Codex login.
- The live smoke runs only from `main` (the workflow gates on
  `refs/heads/main` and checks out that run's commit). This gate guards
  against mistakes by people with write access, not against attackers: a run
  from another branch uses that branch's copy of the workflow, which can drop
  the gate. The fork-approval setting above is the real control.
- Remove or deregister the runner when it is not in use.

Use manual `workflow_dispatch` with the target OS until the runner has been
validated. For unattended main/nightly smoke, configure repository variable
`CODEX_LIVE_SMOKE_ENABLED=true` and, if needed,
`CODEX_LIVE_SMOKE_OS=Linux` or `macOS` for the registered runner.
Without this opt-in the main/nightly job skips rather than failing or
requesting an API key. One configured runner OS is selected per scheduled
run; dispatch manually on the other OS to establish platform coverage.
The workflow uploads a seven-day metadata-only transcript artifact after a run.

A logged-in runner can consume ChatGPT plan usage and is subject to the
account's usage limits; recurring model turns should be enabled intentionally.
Never copy a local `auth.json` to GitHub secrets or into build artifacts.

To enforce a **required** live check in repository policy, an administrator must
configure the appropriate GitHub ruleset/branch-protection check. A scheduled
workflow alone does not create a required status check. Do not gate every PR
on a paid model call. Cross-platform fake fixtures remain the PR gate.
