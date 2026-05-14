# Hawary Workflow Skills

This repository contains public-safe Agent Skills. Keep the skills useful across Codex, Claude Code, and other `SKILL.md` consumers.

## Rules

- Keep canonical skill source under `skills/<skill-name>/SKILL.md`.
- Keep `.agents/skills` and `.claude/skills` as local compatibility links only.
- Do not add private project names, customer data, absolute local paths, tokens, or secrets.
- Prefer report-first workflows. Skills that edit files must require explicit user approval before modifying implementation code.
- Keep `SKILL.md` concise. Move long examples or templates into `references/`.
- Use lowercase kebab-case names, 64 characters or fewer.
- Descriptions must state both what the skill does and when to use it.

## Maintainer Checklist

- Check for sensitive terms with `rg` before publishing changes.
- Test installs in both `~/.agents/skills` and `~/.claude/skills` when changing skill layout.
- Invoke each changed skill explicitly once and with a natural-language trigger once.
