# Skill Authoring

Use this workflow when adding a new public skill.

## Current Repository Shape

The canonical source for every public skill is `skills/<skill-name>/SKILL.md`. Supporting examples, templates, and checklists belong in `skills/<skill-name>/references/`. Agent-specific metadata can live under `skills/<skill-name>/agents/` when useful, but the Markdown skill remains the portable source.

Keep these project-level docs in sync when a change affects them:

- `README.md` and `README.ar.md` for the public overview.
- `docs/codex.md`, `docs/claude.md`, and `docs/compatible-agents.md` for install behavior.
- `docs/examples.md` for prompt examples.
- `docs/roadmap.md` for project direction.
- `CHANGELOG.md` for user-visible changes.

## 1. Extract

Start from a repeated workflow that has clear value outside one private project. Capture what triggers the skill, what evidence it should inspect, and what output it should produce.

## 2. Generalize

Remove private names, local paths, customer data, secrets, product assumptions, and vendor-specific behavior unless the skill is explicitly for that vendor.

## 3. Shape The Skill

Create:

```text
skills/<skill-name>/
|-- SKILL.md
|-- agents/openai.yaml
`-- references/examples.md
```

Keep `SKILL.md` concise. Move templates, checklists, long examples, and matrices into `references/`.

## 4. Validate Metadata

- `name` is lowercase kebab-case.
- `description` says what the skill does and when to use it.
- `agents/openai.yaml` matches the skill's public behavior.
- README, docs, and changelog mention the new skill when relevant.

## 5. Prove Portability

Run:

```bash
ruby scripts/validate_skills.rb
git diff --check
```

For install changes, smoke-test in a temporary directory with isolated `HOME` and npm cache.

## 6. Open A PR

Use a pull request, keep the diff small, and include validation notes. Do not push directly to `main`.
