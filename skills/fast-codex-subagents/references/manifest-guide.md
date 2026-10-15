# Task Manifest

The helper accepts a JSON manifest. Keep it in the workspace or another user-selected location; launch copies the normalized plan into a private runtime directory. The coordinator chooses the role count and ownership after inspecting the task, repository instructions, and shared contracts. Do not pad the team with routine reviewers or overlapping writers.

```json
{
  "schema_version": 1,
  "workspace": "../sample-project",
  "objective": "Add the requested behavior and review the resulting changes.",
  "shared_contract": "All roles use parse_request(input) -> Result and preserve the existing Error shape.",
  "complexity": "bounded",
  "backend": "app-server",
  "fast_selection": {"mode": "selected", "workers": ["implement"]},
  "max_concurrency": 2,
  "project_settings": {
    "writer": {"effort": "low"},
    "reviewer": {"effort": "xhigh"}
  },
  "plan_mode": false,
  "roles": [
    {
      "name": "implement",
      "access": "writer",
      "model": "gpt-6-luna",
      "effort": "low",
      "outcome": "Implement the parser behavior and focused coverage.",
      "owns": ["src/parser.py", "tests/test_parser.py"],
      "constraints": ["Follow repository instructions.", "Preserve public error semantics."],
      "depends_on": [],
      "prompt": "Handle empty input and invalid encoding explicitly."
    },
    {
      "name": "review",
      "access": "reviewer",
      "model": "gpt-6.1-sol",
      "effort": "xhigh",
      "outcome": "Review the completed implementation for correctness and contract risks.",
      "owns": [],
      "constraints": ["Read-only review."],
      "depends_on": ["implement"],
      "prompt": "Check callers and failure paths."
    }
  ]
}
```

`backend` defaults to `auto`: a single bounded independent role selects Codex `exec` to avoid the app-server supervisor, while dependent/multi-role or complex tasks select `app-server`. Explicit `exec` or `app-server` always wins; a failed backend is never silently swapped. The app-server adapter requires Codex CLI 0.162.1 or later, and `doctor` verifies the installed CLI's real generated schema. 0.162.1 is the tested release; newer releases are accepted with a warning, and please report anything that breaks. See the version upgrade runbook.

Settings resolve per field in this order: explicit user choice, saved defaults, applicable project/user rules, then recommendation. `project_settings` can record the applicable project/user rule values for `writer`, `reviewer`, and `fast_selection`. A bounded writer defaults to Luna low; complex or ambiguous coding defaults to Luna max. A read-only reviewer uses Sol xhigh. Fast defaults to all workers and also supports none or a named subset. Worker-level `fast_requested` can express an explicit override; conflicting explicit Fast settings must be resolved before launch. Unavailable models, effort, or requested Fast priority block launch for an explicit choice to be changed; the helper does not fall back silently. The app-server's `default` value explicitly selects the standard route and is a request sentinel, not a model/list service-tier ID, so it does not need to appear in the catalog.

Writer ownership must use concrete relative POSIX paths inside the workspace. Wildcards, parent traversal, duplicate or ancestor-overlapping ownership are rejected. Each role gets the shared contract; dependent roles receive only bounded completed reports. Dependencies must be acyclic. `max_concurrency` limits simultaneous logical turns to 1–8 and defaults to at most three; tasks are capped at 32 roles. Plan Mode rejects any writer; use no team for planning alone, or a read-only role when review is useful and requested.

The app-server uses one stdio connection with separate logical Codex threads and histories. It sends `on-request` approval policy and `auto_review`, gives writers the `workspace-write` sandbox, and gives reviewers the `read-only` sandbox. Reviewer turns also request `readOnly` with network disabled. The helper validates effective thread settings and does not blanket-approve server requests.

The recommended Luna/Sol profiles are defaults, not role restrictions. Explicit and saved model IDs and efforts may select other catalog-supported combinations. Custom `exec` profiles also require the live app-server catalog before any inference; unsupported choices fail without switching backend or model.

## Optional execution controls

```json
{
  "budgets": {"max_wall_seconds": 900, "max_tokens": 500000, "action": "interrupt"},
  "isolation": "worktree",
  "acceptance": [["python3", "-m", "pytest", "-q"]]
}
```

Add these fields to a normal manifest. Defaults retain existing shared-workspace behavior. These controls select app-server under `auto`; an explicit `exec` selection is rejected before launch.

- Task `max_wall_seconds` measures time since task creation; `max_tokens` sums observed cumulative usage across started workers. Cached input is already included. Missing usage is unknown, never zero, so token limits are best-effort, not a billing cap.
- Task `max_idle_seconds` applies separately to active workers. Role-level `budgets` independently apply these fields to that worker. Limits must be positive finite values; `max_tokens` must be an integer. `action` defaults to `warn`; `interrupt` submits cancellation once and keeps the slot occupied until terminal evidence. After 30 seconds without terminal evidence, supervision requires reconciliation instead of replay.
- `isolation: worktree` requires a clean Git repository root without submodules. Each writer starts from the current integration commit in its own detached worktree. Completed patches must stay within ownership before integration. Reviewers must depend on all writers, directly or transitively, to see a stable result. Recovered completed writers are collected before dependents start.
- Worktrees provide Git attribution and rollback boundaries, not OS isolation. The source tree stays unchanged; Git administration records the extra worktrees. Keep them for inspection and remove them deliberately with Git once terminal. The helper never resets, cleans, or deletes user work.
- `acceptance` contains argument arrays, never shell strings. Commands run on the host in the integration tree (or shared workspace), with a 120-second per-command timeout. Use trusted repository checks that do not daemonize or escape their process group. POSIX owned process groups are cleaned up; background descendants fail acceptance. Windows refuses host acceptance without starting commands. Commands and private logs are never automatically replayed on recovery.
- Passing acceptance is tied to the exact reviewed isolated patch. Checks that modify the integration tree fail verification. Execution completion alone leaves `verification_state: not_run` when no checks were configured.

Preview and explicitly apply a completed, accepted isolated patch:

```bash
python3 scripts/fast_agents.py integrate --task TASK_ID
python3 scripts/fast_agents.py integrate --task TASK_ID --apply --confirm PATCH_SHA256
```

Application requires the preview hash, passing acceptance for that patch, and the original clean source HEAD. It stages the patch with `git apply --index`; it never commits or merges. If the source changed, preserve the isolated patch and reconcile manually.
