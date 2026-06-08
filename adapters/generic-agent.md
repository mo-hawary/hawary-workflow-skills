# Generic Agent Adapter For dev-secrets

Use this adapter for agents that can load Agent Skills-compatible folders but do not have a dedicated adapter.

## Install Shape

Copy the canonical folder:

```bash
cp -R skills/dev-secrets /path/to/agent/skills/dev-secrets
```

The agent should use `skills/dev-secrets/SKILL.md` as the source of truth and resolve references relative to that folder.

## Tool Scope

Where the agent supports per-skill tool limits, use read/glob/bash-only access. Do not grant edit/write tools for this skill by default.

## Invocation

Route requests involving `.env*` files, local secret migration, vault import, local leak scanning, rotation guidance, or value-free dev run commands to `dev-secrets`.

If no compatible local CLI exists, return a value-free setup plan with `Status: needs setup`.

## Safe Inspection

Agents may inspect file names, tracking status, ignore rules, package script names, example file presence and key names, and redacted manifest metadata. Do not inspect full package script command strings, example values, or manifest values unless they come from a compatible local CLI or redacted manifest.
