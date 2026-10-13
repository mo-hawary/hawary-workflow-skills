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

`backend` defaults to `auto`: a single bounded independent role selects Codex `exec` to avoid the app-server supervisor, while dependent/multi-role or complex tasks select `app-server`. Explicit `exec` or `app-server` always wins; a failed backend is never silently swapped. The app-server adapter accepts only fixture-tested Codex releases after `doctor` verifies their real generated schema. Currently only Codex CLI 0.162.1 is enabled; see the version upgrade runbook.

Settings resolve per field in this order: explicit user choice, saved defaults, applicable project/user rules, then recommendation. `project_settings` can record the applicable project/user rule values for `writer`, `reviewer`, and `fast_selection`. A bounded writer defaults to Luna low; complex or ambiguous coding defaults to Luna max. A read-only reviewer uses Sol xhigh. Fast defaults to all workers and also supports none or a named subset. Worker-level `fast_requested` can express an explicit override; conflicting explicit Fast settings must be resolved before launch. Unavailable models, effort, or requested Fast priority block launch for an explicit choice to be changed; the helper does not fall back silently. The app-server's `default` value explicitly selects the standard route and is a request sentinel, not a model/list service-tier ID, so it does not need to appear in the catalog.

Writer ownership must use concrete relative POSIX paths inside the workspace. Wildcards, parent traversal, duplicate or ancestor-overlapping ownership are rejected. Each role gets the shared contract; dependent roles receive only bounded completed reports. Dependencies must be acyclic. `max_concurrency` limits simultaneous logical turns to 1–8 and defaults to at most three; tasks are capped at 32 roles. Plan Mode rejects any writer; use no team for planning alone, or a read-only role when review is useful and requested.

The app-server uses one stdio connection with separate logical Codex threads and histories. It sends `on-request` approval policy and `auto_review`, gives writers the `workspace-write` sandbox, and gives reviewers the `read-only` sandbox. Reviewer turns also request `readOnly` with network disabled. The helper validates effective thread settings and does not blanket-approve server requests.
