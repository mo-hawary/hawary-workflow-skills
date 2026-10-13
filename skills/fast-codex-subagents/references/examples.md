# Fast Codex Subagents Examples

Explicit trigger:

```text
Use $fast-codex-subagents to implement this bounded change with one writer and one read-only reviewer. Run all workers in Fast mode.
```

Natural-language trigger:

```text
Split this implementation and its review across a small Codex team. Give each writer separate files, agree on the shared API first, and keep the reviewer read-only.
```

Choose the smallest useful team automatically and show the resolved roles, ownership, model, effort, and per-worker Fast choice before launch. Resolve explicit user choices before saved defaults, project/user rules, and recommendations. Use Fast for all workers when unspecified. Ask only about an unresolved conflict or unavailable requested option.

Inspect the local CLI without starting a model session:

```bash
python3 skills/fast-codex-subagents/scripts/fast_agents.py doctor
```

The preferred backend is one app-server stdio process with separate logical threads. The adapter is experimental and accepts Codex CLI 0.162.1 only; `exec` is an explicit fallback. Doctor checks the generated schema and `model/list` without inference.

Read current saved preferences and inspect an existing task:

```bash
python3 skills/fast-codex-subagents/scripts/fast_agents.py defaults show
python3 skills/fast-codex-subagents/scripts/fast_agents.py status --task TASK_ID
python3 skills/fast-codex-subagents/scripts/fast_agents.py watch --task TASK_ID --continuous
python3 skills/fast-codex-subagents/scripts/fast_agents.py resume --task TASK_ID
```

Setup commands are explicit and preview-first:

```bash
python3 skills/fast-codex-subagents/scripts/fast_agents.py install --agents-home /path/to/agent/skills
python3 skills/fast-codex-subagents/scripts/fast_agents.py defaults set --fast-requested true --writer-effort low
python3 skills/fast-codex-subagents/scripts/fast_agents.py defaults set --fast-workers none
python3 skills/fast-codex-subagents/scripts/fast_agents.py instructions preview --path /path/to/codex-home
```
