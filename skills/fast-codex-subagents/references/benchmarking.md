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
