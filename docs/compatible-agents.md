# Compatible Agents Guide

Hawary Workflow Skills are written to be portable across tools that understand the Agent Skills pattern.

The canonical skill format is:

```text
skills/
`-- skill-name/
    |-- SKILL.md
    |-- agents/
    |   `-- openai.yaml
    |-- references/
    |-- scripts/
    `-- assets/
```

Only `SKILL.md` is required. Supporting folders are optional. `agents/openai.yaml` is named for OpenAI/Codex UI metadata, not the generic skill contract, and can be ignored by other agents.

## Required Behavior

A compatible agent should:

- discover skill folders under a configured skills directory
- read YAML frontmatter from `SKILL.md`
- use `name` as the skill identifier
- use `description` to decide when the skill applies
- load the full `SKILL.md` only when the skill is selected
- resolve referenced files relative to the skill folder
- treat scripts as optional helper tools, not hidden instructions

## Install

Copy any folder under `skills/` into your agent's skill directory:

macOS/Linux:

```bash
cp -R skills/cross-layer-contract-audit /path/to/agent/skills/
```

Windows PowerShell:

```powershell
Copy-Item -Recurse skills\cross-layer-contract-audit C:\path\to\agent\skills\
```

If your agent supports repo-local skills, keep them inside the project. If it supports user-wide skills, install them into the user's global skill directory. If your agent can read repositories directly, point it at this repo and ask it to use the matching `skills/<skill-name>/SKILL.md` folder.

## Optional CLI Install

If your environment uses the open `skills` CLI, install from GitHub:

```bash
npx skills add mo-hawary/hawary-workflow-skills --list
npx skills add mo-hawary/hawary-workflow-skills --skill project-docs-cleanup
```

If `npx skills` is not available in your environment or does not support this repository, use the copy method above.

## Portability Notes

The skills avoid Codex-only and Claude-only behavior where possible. Platform-specific distribution should wrap the same source folders rather than rewriting the skills.

If a platform needs a bundle name, use `hawary-workflow-skills`. Keep individual skill names descriptive, lowercase, and kebab-case.

## References

- [Skills CLI](https://skills.sh/docs/cli)
- [Vercel Agent Skills directory](https://vercel.com/docs/agent-resources/skills)
