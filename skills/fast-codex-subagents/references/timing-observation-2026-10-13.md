# Historical timing observations: 2026-10-13

These intervals were recorded before the rename under the pre-release public skill ID `fast-agents`, now `fast-codex-subagents`. No skill release version had been published; Codex CLI 0.162.1 is the recorded runtime version. The values below remain historical observations of that earlier source snapshot, not measurements of a new run with the renamed package. They are exploratory intervals from one seeded-inventory trial. The CLI and native intervals use different clocks and boundaries. They are not matched active-work measurements. Do not use them to claim that one path is faster or to infer a speedup, cost, or general performance result.

| Path and stage | Start event | End event | Duration |
|---|---|---|---:|
| CLI review | First recorded command, 13:23:36.510 UTC | Final agent report, 13:24:06.596 UTC | 30.086 s |
| CLI fix | First recorded command, 13:24:27.050 UTC | Final agent report, 13:24:41.406 UTC | 14.356 s |
| Native review | Coordinator worker clock START, 14:37:34 UTC | Full report delivered before END, 14:38:09 UTC | 35 s |
| Native fix | Coordinator worker clock START, 14:38:47 UTC | Full report delivered before END, 14:39:05 UTC | 18 s |

The CLI interval sum is 44.442 seconds, with a 20.454-second gap between stages and a 64.896-second overall span. The native interval sum is 53 seconds, with a 38-second gap and a 91-second span. These arithmetic summaries preserve each path's own boundaries; they do not make the measurements comparable. CLI full-turn records total 84.543 seconds and exclude helper startup and preflight. Startup and preflight were not separately timed.

The clocks, tools, context, handoffs, service settings, cache effects, and instrumentation differ. An earlier exploratory native review stopped its clock before the final report and was excluded. The later native run used a full-report-before-END clock, but cache state and execution order were not controlled. There were no repeated measurements from which to estimate variance.

A separate documentation task used a `gpt-6-luna` writer with requested `max` effort and recorded a 770.582-second full turn, about 12 minutes 51 seconds. That task and effort differed from the `low` fixture trial and required coordinator steering. It is a counterexample to any claim that runs are uniformly fast.

## Reproduce the fixture

The [baseline fixture](fixtures/inventory-baseline/README.md) preserves the original contract. Its source and test files are stored as `.txt` so test discovery will not collect them in this repository. Use fresh copies for separate control paths:

```sh
trial_dir="$(mktemp -d)"
mkdir -p "$trial_dir/tests"
cp skills/fast-codex-subagents/references/fixtures/inventory-baseline/inventory.py.txt "$trial_dir/inventory.py"
cp skills/fast-codex-subagents/references/fixtures/inventory-baseline/test_inventory.py.txt "$trial_dir/tests/test_inventory.py"
cp skills/fast-codex-subagents/references/fixtures/inventory-baseline/README.md "$trial_dir/README.md"
cd "$trial_dir"
python3 -m unittest discover -s tests -v
```

The baseline run has three failing tests and one passing test. Keep the fixture copies intact for review. Run the fix task against a separate fresh copy.

The following are normalized public prompts and task contracts. They are suitable for a manual replay, but they are not claimed to reproduce every private runtime wrapper or to guarantee the same results.

**Review prompt:**

> Review the inventory implementation and tests for correctness against the README contract. Do not edit files. Identify concrete defects, explain their impact, and return a concise report. Focus on quantity validation, repeated SKU price conflicts, ordering and aggregation, and input mutation. Do not assume the seeded defects are exhaustive.

**Fix prompt:**

> Fix the defects identified by the reviewer while preserving the README contract. You own only `inventory.py` and `tests/test_inventory.py`. Add focused regression coverage for the reported defects. Run the fixture's unittest suite and report the result. Do not change unrelated files.

For the recorded CLI configuration, the requested reviewer was `gpt-6.1-sol` with per-turn effort `low`; the requested writer was `gpt-6-luna` with per-turn effort `low`; Fast was disabled. Provider-effective effort and upstream tier execution were not independently verified. The native run used corresponding reviewer and writer roles; its provider telemetry was unavailable. Start the writer only after the user authorizes the fixture code change. Use the [manifest guide](manifest-guide.md) for helper setup and manifest fields. A replay does not guarantee the timing or result in this record.

## Corrected Fast ON/OFF pair

A separate corrected one-pair observation used Fast ON first, then explicit Fast OFF, against the same seeded fixture. Both runs recorded the same baseline outcome and final source scope. The full-turn intervals were 47.917 seconds (ON) and 61.657 seconds (OFF), a 13.740-second observed difference (22.28% of the OFF interval); summed first-command-to-final-report intervals were 31.307 seconds (ON) and 42.112 seconds (OFF), from each role's first recorded command execution to final report completion. This ordered pair is not a causal estimate: execution order was not reversed and cache state and warm-up were uncontrolled. Both turns requested `low` effort, while thread-start notifications reported default `xhigh`; provider-effective effort is unknown. The app-server recorded `priority` for ON and `default` for OFF, without upstream service confirmation. Native Codex tools advertised `priority`, so the earlier native control is not a standard-tier comparison. The pair's combined token counts were higher with ON (325,163) than OFF (300,566); cached input is included within input counts, and the records support no token-saving or cost claim. See the separate [live-validation record](live-validation-2026-10-13.md) for correctness, task scope, and evidence limitations.
