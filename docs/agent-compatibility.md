# Agent Compatibility

`skills/dev-secrets/SKILL.md` is the canonical portable source of truth. Adapters may explain install paths or persistent guidance for a specific agent, but they must not fork the safety model.

## Compatibility Matrix

| Agent | Skill Source | Invocation | Notes |
|---|---|---|---|
| Claude Code | `.claude/skills/dev-secrets/SKILL.md` | `/dev-secrets` or natural-language trigger | Use the canonical skill folder; do not read real `.env*` values |
| Codex | `skills/dev-secrets/SKILL.md` plus AGENTS.md guidance | Natural-language trigger | Route env-secret tasks to the skill and keep outputs value-free |
| Cursor | Imported skill folder or repo instructions | Natural-language trigger | Use canonical `SKILL.md`; adapter support depends on local setup |
| Windsurf | Imported skill folder or repo instructions | Natural-language trigger | Use canonical `SKILL.md`; adapter support depends on local setup |
| Cline | Imported skill folder or custom rule | Natural-language trigger | Keep tool scope read/glob/bash-only where supported |
| Generic agents | Any Agent Skills-compatible directory | Skill description match | Resolve references relative to `skills/dev-secrets/` |

## Safety Model

All agents must preserve these rules:

- No real values read.
- No partial values, hashes, fingerprints, encrypted blobs, or passphrase hints shown.
- No broad secret-reading commands.
- Value-free outputs only.
- Local CLI handles real values.
- Fail closed when safety checks fail.
- Approval before mutation.

## Adapters

- Codex persistent guidance: [../adapters/codex/AGENTS.md](../adapters/codex/AGENTS.md)
- Claude install/use notes: [../adapters/claude/README.md](../adapters/claude/README.md)
- Generic adapter notes: [../adapters/generic-agent.md](../adapters/generic-agent.md)
