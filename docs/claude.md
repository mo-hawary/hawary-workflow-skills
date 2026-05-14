# Claude Install Guide

Claude Code reads skills from `.claude/skills` in a project and from `~/.claude/skills` for personal use. Claude Code skills use the same `SKILL.md` folder pattern as this repository.

Claude web and desktop can also use uploaded custom Skills.

## Claude Code User Install

Install all skills for use across repositories:

macOS/Linux:

```bash
mkdir -p ~/.claude/skills
cp -R skills/* ~/.claude/skills/
```

Windows PowerShell:

```powershell
New-Item -ItemType Directory -Force $HOME\.claude\skills
Copy-Item -Recurse skills\* $HOME\.claude\skills\
```

Install one skill:

macOS/Linux:

```bash
mkdir -p ~/.claude/skills
cp -R skills/project-docs-cleanup ~/.claude/skills/
```

Windows PowerShell:

```powershell
New-Item -ItemType Directory -Force $HOME\.claude\skills
Copy-Item -Recurse skills\project-docs-cleanup $HOME\.claude\skills\
```

Restart Claude Code if the new skill does not appear immediately.

## Optional CLI Install

If your environment uses the open `skills` CLI, install from GitHub and then copy or adapt the installed folder into Claude's skills directory when needed:

```bash
npx skills add mo-hawary/hawary-workflow-skills --list
npx skills add mo-hawary/hawary-workflow-skills --skill project-docs-cleanup
```

If `npx skills` is not available in your environment or does not support this repository, use the copy method above.

## Claude Code Project Install

Install skills into a project:

macOS/Linux:

```bash
mkdir -p .claude/skills
cp -R skills/repo-workflow-checker .claude/skills/
```

Windows PowerShell:

```powershell
New-Item -ItemType Directory -Force .claude\skills
Copy-Item -Recurse skills\repo-workflow-checker .claude\skills\
```

Commit `.claude/skills/<skill-name>/SKILL.md` only when the skill should be shared with that project.

## Claude Web Or Desktop

Zip one skill folder at a time and upload it through Claude's custom Skills interface.

The zip should contain the skill folder itself:

```text
project-docs-cleanup.zip
`-- project-docs-cleanup/
    `-- SKILL.md
```

On Windows, create the zip from PowerShell:

```powershell
Compress-Archive -Path skills\project-docs-cleanup -DestinationPath project-docs-cleanup.zip -Force
```

## Sharing

For Team and Enterprise plans, Claude supports organization-managed and organization-shared skills. Use the canonical folders under `skills/` as the source when preparing shared uploads.

## References

- [Claude Code skills](https://docs.claude.com/en/docs/claude-code/skills)
- [Claude Agent Skills](https://docs.claude.com/en/docs/agents-and-tools/agent-skills)
