# Claude Adapter For dev-secrets

Install the canonical skill folder into Claude Code:

```bash
mkdir -p .claude/skills
cp -R skills/dev-secrets .claude/skills/dev-secrets
```

Claude Code can then invoke the skill with:

```txt
/dev-secrets
```

Natural-language requests about `.env` files, local secret migration, vault import, leak scans, rotation guidance, or value-free run commands should also route to the skill.

## Safety

Claude should read `.claude/skills/dev-secrets/SKILL.md` and its referenced files, not real `.env*` values.

Preserve the canonical safety model:

- No real values read.
- No hashes or fingerprints.
- No broad secret commands.
- Inspect package script names and redacted manifest metadata only; do not inspect full command strings unless a compatible local CLI or redacted manifest provides them.
- Value-free outputs only.
- Local CLI handles real values.
- Fail closed when setup is unsafe.
- Approval before mutation.
