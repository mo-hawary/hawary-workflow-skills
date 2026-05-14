# Codex Install Guide

These paths are tested with Codex-style Agent Skills installs. If your Codex setup uses a different skills directory, copy the same `skills/<skill-name>` folder there. You can also point Codex at this repository and ask it to load the relevant skill folder.

## CLI Install

If your environment uses the open `skills` CLI, install from GitHub:

```bash
npx skills add mo-hawary/hawary-workflow-skills --list
npx skills add mo-hawary/hawary-workflow-skills --skill pull-request-review-loop
```

## User Install

Install all skills for use across repositories:

macOS/Linux:

```bash
mkdir -p ~/.agents/skills
cp -R skills/* ~/.agents/skills/
```

Windows PowerShell:

```powershell
New-Item -ItemType Directory -Force $HOME\.agents\skills
Copy-Item -Recurse skills\* $HOME\.agents\skills\
```

Install one skill:

macOS/Linux:

```bash
mkdir -p ~/.agents/skills
cp -R skills/project-docs-cleanup ~/.agents/skills/
```

Windows PowerShell:

```powershell
New-Item -ItemType Directory -Force $HOME\.agents\skills
Copy-Item -Recurse skills\project-docs-cleanup $HOME\.agents\skills\
```

Restart Codex if the new skill does not appear immediately.

Some Codex environments may also expose a `~/.codex/skills` or `$CODEX_HOME/skills` directory. Use the directory your Codex installation documents.

## Repo Install

Install skills into a project so everyone working in that repo can use them:

macOS/Linux:

```bash
mkdir -p .agents/skills
cp -R skills/project-status-dashboard .agents/skills/
```

Windows PowerShell:

```powershell
New-Item -ItemType Directory -Force .agents\skills
Copy-Item -Recurse skills\project-status-dashboard .agents\skills\
```

Commit `.agents/skills/<skill-name>/SKILL.md` only when the skill is meant to be part of that project's workflow.

## Usage

Invoke a skill explicitly:

```text
Use $project-docs-cleanup to audit this repo's docs.
```

Codex can also invoke a skill implicitly when the request matches the skill description.
