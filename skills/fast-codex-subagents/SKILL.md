---
name: fast-codex-subagents
description: "Codex-only: Coordinate scoped Codex CLI/app-server workers with role, dependency, approvals, and Fast-tier evidence. Use for delegated Codex execution."
agents: ["codex"]
---

# Fast Codex Subagents

**Codex-only.** This skill is executable only using a verified Codex CLI/app-server runtime. The `agents: ["codex"]` metadata marks its intended host; other agent installers may still display the skill but must not execute it. A terminal can invoke the helper after configuring Codex, but the helper cannot cryptographically identify the invoking chat UI.

Use this workflow for Codex CLI and Codex app when the loaded instructions expose this skill. It coordinates scoped subagents, including a single bounded worker when that is the smallest useful team. The bundled helper launches Codex CLI's app-server; the instruction installer cannot verify that Codex app loaded the same instruction file. Other agents can read the workflow but cannot launch this Codex-specific runtime. The installer refuses `--claude-home` and accepts only Codex skill homes for new installation.

The bundled helper is experimental public beta. It requires Python 3.10+ and a fixture-verified Codex CLI release. Currently 0.162.1 is the only tested version; the candidate family is >=0.162.1,<0.163.0, but unverified patch releases fail closed. See the [Codex upgrade runbook](references/codex-upgrade.md). Fake/unit tests have passed on macOS, Linux, and Windows; some Windows `exec` fixtures are skipped. Real Codex model sessions have been demonstrated on macOS only. This does not establish production readiness or provider-side model, effort, or service-tier execution.

## Workflow

1. Inspect the request and applicable user/project instructions. Keep brief factual answers, conversation, and planning in the foreground; use this workflow as the delegation master for substantive execution, even when one bounded worker is sufficient. Treat an explicit user request as authorization to make the requested implementation changes; otherwise, get approval before modifying implementation code. Choose the smallest useful team automatically: one writer for one coherent change; add writers only for independent workstreams with disjoint files; add a Sol reviewer when independent review materially helps or the user asks for one. In Plan Mode, do not launch writers. Do not use native agent spawning, Rust helpers, or nested delegation.
2. Resolve model, effort, Fast per worker, and backend in this order: explicit user choice, saved defaults where supported, project/user rules, recommendation. Resolve worker count and team composition automatically from the smallest useful team. Use Luna low for bounded coding and Luna max for complex or ambiguous coding. Use Sol xhigh for read-only review. Fast supports all, selected workers, or none; use all workers when no applicable choice sets a different selection. Ask only when applicable instructions conflict or a requested option is unavailable. Never override explicit user routing, backend, model, count, Fast, or approval choices. Never ask routine team/model/Fast setup questions.
3. Inspect the repository contract, choose non-overlapping concrete writer ownership, agree on the shared interface, and stage dependent review after implementation. Keep shared context and dependency reports bounded. Read [the manifest guide](references/manifest-guide.md).
4. Run `scripts/fast_agents.py doctor` before launch. It checks the fixture-verified Codex CLI version, the generated app-server schema, server options, process identity, and `model/list`; this performs no model inference. Show the selected team, ownership, dependencies, models, efforts, Fast choice, and effective configuration before starting.
5. Select Codex `exec` automatically for a single bounded independent role, or the app-server backend for complex/multi-role/dependent execution or interactive steering: one `codex app-server --listen stdio://` process hosts separate logical worker threads and histories. Threads use `on-request` approvals with `auto_review`; writers receive `workspace-write`, reviewers `read-only` with network disabled. Never blanket-approve server requests. Keep pending approval, user-input, and MCP requests visible for an explicit scoped reply. A single bounded independent role also selects `exec` through documented `auto` routing; an explicit backend always wins. Never retry another backend or tier silently.
6. Poll `status` or `control status/events`. Steering, interrupting, and replying require explicit commands with the current thread/turn/request IDs. Status and watch are read-only. `resume` reconnects to the existing supervisor; use `resume --recover` only to reconcile saved thread/turn state. An uncertain submitted turn is never replayed automatically. Read [runtime and safety details](references/runtime-and-safety.md).
7. Review private role reports, the scope audit, and the workspace diff. Treat declared file ownership as a coordination boundary, not a per-file sandbox. Never interpret successful agent execution or a path-scope check as proof of passing tests or acceptance. Report effective Fast tier separately from upstream execution, which remains unverified without provider telemetry. For repeatable performance comparisons, including the fake backend overhead probe, use [the benchmark guide](references/benchmarking.md).

The dated trial records were collected before this rename under the public skill ID `fast-agents`, using an unpublished source snapshot and Codex CLI 0.162.1. The current package name is `fast-codex-subagents`; the records remain historical evidence and their source hashes identify the former snapshot. See the [validation record](references/live-validation-2026-10-13.md) and [timing observations](references/timing-observation-2026-10-13.md).

## Setup

Setup only previews or installs skill instructions/defaults. **Setup does not start model sessions**, and `doctor` performs no model inference. `launch` starts actual Codex sessions and may consume tokens. Active controls require an existing Codex configuration home. Install, defaults, and shared Codex instruction changes are opt-in commands with previews. Skill installs use the `fast-codex-subagents` folder; saved defaults and runtime state retain their existing `fast-agents` paths and formats. The managed instruction block targets both Codex CLI and Codex app only through clients that load the selected shared instruction file; verify app activation in Codex app itself. Read [setup and rollback](references/setup-and-rollback.md).

## Codex backend and Fast evidence

The default `auto` backend selects `exec` for one independent bounded role, otherwise `app-server`. Use explicit `app-server` when thread steering, approvals, multiple workers, or recovery are needed. The `exec` route has no live steering/reply capability; neither backend is retried automatically after failure. Up to eight workers run concurrently; the task manifest is capped at 32 roles. These are functional routing thresholds, **not measured speedup guarantees**.

Example `status --json` after a real thread reports its settings:

```json
{
  "backend": "app-server",
  "roles": {
    "implement": {
      "fast": {
        "requested": true,
        "requested_tier": "priority",
        "effective_tier_reported": "priority",
        "effective_tier": "priority",
        "effective": "matched",
        "upstream": "unverified"
      }
    }
  }
}
```

The example is illustrative, not provider telemetry. The current app-server reports effective *thread configuration*; actual upstream Fast routing remains unverified without provider evidence. A `priority` request that is not confirmed effective fails before its turn.

For unsupported Codex versions, absent `CODEX_HOME` or configuration, missing model tiers, lost app-server responses, or locked workspace state, see [upgrade and troubleshooting](references/codex-upgrade.md) and [runtime safety](references/runtime-and-safety.md). The authenticated Linux/macOS live smoke uses ChatGPT `codex login` on a dedicated self-hosted runner; **no API key** is required. Main/nightly runs are disabled until `CODEX_LIVE_SMOKE_ENABLED=true` is configured, and it never runs on PRs. See the [runbook](references/codex-upgrade.md).
