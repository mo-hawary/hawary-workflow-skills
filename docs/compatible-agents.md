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

Only `SKILL.md` is required. Supporting folders are optional. `agents/openai.yaml` is OpenAI/Codex UI metadata and can be ignored by other agents.

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

If your agent supports repo-local skills, keep them inside the project. If it supports user-wide skills, install them into the user's global skill directory.

## Portability Notes

The skills avoid Codex-only and Claude-only behavior where possible. Platform-specific distribution should wrap the same source folders rather than rewriting the skills.

If a platform needs a bundle name, use `hawary-workflow-skills`. Keep individual skill names descriptive, lowercase, and kebab-case.
