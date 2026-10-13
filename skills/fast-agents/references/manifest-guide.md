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

`backend` defaults to `app-server`. The explicit `exec` backend is a compatibility fallback and keeps its separate Codex exec command path. The app-server adapter accepts Codex 0.162.1 only after `doctor` verifies the installed generated schema.

Settings resolve per field in this order: explicit user choice, saved defaults, applicable project/user rules, then recommendation. `project_settings` can record the applicable project/user rule values for `writer`, `reviewer`, and `fast_selection`. A bounded writer defaults to Luna low; complex or ambiguous coding defaults to Luna max. A read-only reviewer uses Sol xhigh. Fast defaults to none and can select all workers, none, or a named subset. Worker-level `fast_requested` can express an explicit override; conflicting explicit Fast settings must be resolved before launch. Unavailable models, effort, or requested Fast priority block launch for an explicit choice to be changed; the helper does not fall back silently. The app-server's `default` value explicitly selects the standard route and is a request sentinel, not a model/list service-tier ID, so it does not need to appear in the catalog.

Writer ownership must use concrete relative POSIX paths inside the workspace. Wildcards, parent traversal, duplicate or ancestor-overlapping ownership are rejected. Each role gets the shared contract; dependent roles receive only bounded completed reports. Dependencies must be acyclic. `max_concurrency` limits simultaneous logical turns and defaults to at most three. Plan Mode rejects any writer; use no team for planning alone, or a read-only role when review is useful and requested.

The app-server uses one stdio connection with separate logical Codex threads and histories. It sends `on-request` approval policy and `auto_review`, gives writers the `workspace-write` sandbox, and gives reviewers the `read-only` sandbox. Reviewer turns also request `readOnly` with network disabled. The helper validates effective thread settings and does not blanket-approve server requests.
