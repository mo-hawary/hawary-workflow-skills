# Reproducible Fast vs Standard Evaluation

This is a measurement protocol, **not** a script that launches paid model sessions. Running `doctor` examines local capabilities only. Running `launch` uses an actual Codex session and may consume tokens.

## Acceptance before speed

First fix a task corpus with expected outputs and an objective validation suite. Include implementation, code review, and realistic mixed-team tasks. Record the repository commit, exact task manifest, required files, acceptance commands, OS, CLI adapter/version, model, reasoning effort, process architecture, and worker concurrency.

A comparison is invalid when either run produces a materially different answer, fails acceptance, or takes an unapproved fallback. Report failures and retries separately; do not discard them as outliers. Never revert or clean a user's workspace automatically. Run trials in separately prepared workspaces from the **same Git tree and initial dirty state**, with no concurrent writer tasks.

## Matched trial design

1. Check `python skills/fast-codex-subagents/scripts/fast_agents.py doctor --json`, record the model list and requested tier availability. Fail rather than silently switching unavailable model/tier/backend.
2. Create equivalent manifests that differ **only** by `fast_selection`: `{"mode":"all"}` for Fast and `{"mode":"none"}` for Standard. Keep worker roles, effort, prompts, sandbox and dataset identical.
3. Use at least 10 matched pairs when feasible and counterbalance order (ABBA or randomly balanced). Record cold versus warm starts separately. Avoid interference between concurrent pairs.
4. Launch one task at a time. Observe `status --json` until a terminal state or recovery requirement; **do not replay an uncertain turn**. Capture final role reports, event history, and verification results.
5. Run exactly the same external acceptance checks against each resulting workspace. Preserve raw logs and any failures. Record whether each task passed the acceptance gate before including it in performance comparisons.

## Report schema

Store one local CSV/JSON record per trial with:

| Field | Definition |
| --- | --- |
| `case_id`, `pair_id`, `order` | Same input case and counterbalanced trial pair |
| `git_sha`, `manifest_sha256` | Auditable input snapshot |
| `os`, `python_version`, `codex_version`, `backend` | Runtime provenance |
| `roles`, `concurrency`, `models`, `efforts` | Held constant between pairs |
| `fast_requested`, `tier_reported`, `upstream_verified` | Separate user request, thread report, provider telemetry |
| `started_at`, `finished_at`, `wall_seconds` | Full end-to-end elapsed time, **including** setup and verification |
| `model_input_tokens`, `output_tokens`, `cached_tokens` | Use app-server evidence only where supplied; distinguish missing from zero |
| `pass`, `failure_kind`, `recovery_required` | Correctness and operational status |

Also record median, p95, sample size, failure rate, and paired differences across runs. Separate cold/warm samples, stage timing (doctor/startup/turn/report/acceptance), and outcomes. Do not infer provider-side Fast execution from `model/list` or `thread/settings/updated` alone.

## Interpretation limits

An observed latency difference is not a causal claim without repeated, controlled runs. Compare task completion and correctness **before** throughput or token-cost claims. The historical October 13, 2026 single Fast ON/OFF trial is exploratory evidence only; its result is not a benchmark for all repositories, models, or platforms.

## Executable workflow comparison

Preview the live trial plan without starting Codex:

```bash
python tests/benchmark_codex_workflows.py --rounds 10
```

On a POSIX host with a ChatGPT-authenticated Codex CLI, explicitly start trials:

```bash
python tests/benchmark_codex_workflows.py --rounds 10 --run-live --output /tmp/codex-workflow-trials
```

The runner rotates and reverses balanced groups of four modes: foreground, requested native subagents, helper Fast, and helper Standard. Each trial creates the same seeded inventory fixture from scratch, preserves raw logs privately, includes setup and external acceptance in wall time, and records failures without retries. Acceptance uses an external pristine test copy. An uncertain task stops the campaign; its private artifacts remain for inspection. Model/effort are selectable; defaults hold the same requested model/effort across modes. Missing usage stays null.

This bundled case is a reproducible exploratory test, not a diverse performance corpus. Cache/warm-up remain uncontrolled. Native delegation and effective settings require review of the private transcript: requesting native agents does not prove they ran. Do not publish speedup claims until that compliance review and the broader matched corpus above are complete. Authenticated runs and reviewed current-source goldens are still required; dry runs and fake tests are not substitutes.

## Coordinator overhead: fake Codex, no model calls

Run this diagnostic on a host with Python and the fake CLI fixtures:

```bash
python tests/benchmark_codex_backends.py --pairs 10 --output /tmp/codex-backend-overhead.json
```

It executes matched, counterbalanced fake `exec` and app-server tasks and records
end-to-end startup/process overhead (median and p95). It is a **local harness
overhead** measurement only: fake workers make no inference, have different
process communication paths, and the result does not predict the speed of a
real Codex model turn. CI runs three pairs on Ubuntu for diagnostic regressions.

The routing threshold is functional, not an arbitrary milliseconds claim:
default `auto` chooses `exec` for one bounded independent worker when no
interactive steering/approval-reply control is required; it chooses app-server
for multi-role, dependent, or complex work. An explicit backend always wins.
If real paired measurements establish a better threshold, update and test the
heuristic without silently switching a running task's backend.
