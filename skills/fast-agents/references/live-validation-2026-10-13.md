# Live validation record: 2026-10-13

This record describes seeded-fixture validation of `fast-agents` on macOS with Codex CLI 0.162.1. In one CLI trial, a read-only reviewer found all three seeded defects, the dependent writer fixed them, all four tests passed, and the final tracked source diff contained only the two assigned files. The tested source was a pending local patch based on public PR 28 head `60ce275068004b9a659d10985f5ab78b76408bf0`; it was not a published release. The accompanying [curated evidence summary](evidence/2026-10-13-summary.json) identifies source and evidence types. Private runtime records are not published because they can contain local identifiers and authentication capabilities.

## Seeded inventory task

The baseline had three failures and one pass. The final tracked source diff contained only `inventory.py` and `tests/test_inventory.py`.

The team requested `gpt-6.1-sol` as a read-only reviewer and `gpt-6-luna` as the writer. Both per-turn effort values were configured as `low`. App-server thread-start notifications reported `xhigh` as the default reasoning effort for both threads. The helper submitted the configured per-turn effort in `turn/start`; provider-effective effort was not independently available. Treat `low` here as the requested setting, not a verified provider setting.

Fast was disabled. The CLI task recorded requested and effective `default` routing. Native Codex tools advertised `priority`, but native provider telemetry was unavailable. Neither record verifies upstream service execution.

A separate native-control run on the same fixture also found all three seeded defects, passed all four tests, and ended with a source diff limited to the two assigned files. Native token metadata was unavailable. This is one correctness observation per control path, not a reliability estimate.

## Corrected Fast ON/OFF pair

A later corrected matched pair used the same seeded baseline, base task instructions, Codex CLI version, requested models, and requested `low` effort for both roles. Complete generated inputs differed, including workspace paths and the fixer's review report. Both runs found all three seeded defects, passed all four tests, and ended with the two assigned source paths changed. Fast ON recorded 47.917 seconds of full-turn time versus 61.657 seconds for explicit Fast OFF, 22.28% shorter in this observation. Summed first-command-to-final-report intervals were 31.307 seconds (ON) and 42.112 seconds (OFF); each interval starts at the role's first recorded command execution and ends when its final report completed. The pair covered two tasks and four worker turns; four manager/server process IDs were absent when audited.

This is one ordered pair with Fast ON first; cache state and warm-up were uncontrolled. Both roles requested `low`, while thread-start notifications reported the default `xhigh`; provider-effective effort was unverified. The app-server recorded `priority` for ON and `default` for OFF, without upstream service confirmation. Native tools advertised `priority`, so the native run above is not a standard-tier comparison. The ON run recorded 325,163 total tokens versus 300,566 for OFF; cached input is a subset of input. These observations do not establish a causal or general speed advantage, a native comparison, or a token/cost saving.

## Other observed work

The original validation batch covered five completed CLI tasks and six completed worker turns. All five recorded app-server exit codes were zero. Ten recorded manager and server process IDs were absent when the records were audited. These observations describe that batch only; they do not include the later corrected Fast ON/OFF pair.

The task records retained on-request approvals, an `auto_review` reviewer, a read-only reviewer sandbox with network disabled, and a workspace-write writer sandbox. File ownership remained a coordination declaration. The trial did not test adversarial sandbox enforcement or per-file operating-system confinement.

One CLI writer created incidental `__pycache__` directories. The final source diff was limited to the two assigned source paths; that does not mean the writer made only two filesystem writes.

In a separate documentation review, a read-only reviewer found two safety inconsistencies in `dev-secrets`: a source-dependent exception to the secret-read prohibition and a rule that treated executable presence as compatibility evidence. A writer changed seven approved paths. The report records ten public-package tests passing, Ruby validation of ten skills, and a clean diff check.

Two forward tests then invoked the patched `dev-secrets` guidance explicitly and through a natural-language request under `fast-agents`. Each loaded the patched skill and had 17 runtime files. Neither runtime file set contained either of two synthetic value sentinels. The recorded commands inspected a mock wrapper's source and did not execute the incompatible mock CLI. This is observed `dev-secrets` behavior in those two runs, not a secret-protection guarantee from `fast-agents`.

## Setup and limits

Before the completed fixture runs, the validator rejected an explicitly configured `low` reviewer effort. A local fix allowed supported explicit efforts and added malformed-value guards. Managed sandbox restrictions also blocked local CLI catalog and state access during setup; the coordinator used scoped approved launcher privileges. The worker roles retained their own sandbox settings. These setup observations are not a reliability percentage.

This evidence does not measure imports, live approval replies, interruption, recovery, Codex app activation, cross-platform behavior, or provider-side model, effort, and service-tier execution. The results do not establish speed, cost, general quality, security, or leak prevention.

See [timing observations and fixture replay instructions](timing-observation-2026-10-13.md).
