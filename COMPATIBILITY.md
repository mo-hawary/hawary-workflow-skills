# Compatibility

This matrix documents how Hawary Workflow Skills should be installed and used across common coding agents. The canonical source is always `skills/<skill-name>/SKILL.md`; platform adapters should point to that source rather than copy rewritten skill bodies.

## Matrix

| Agent | Status | Install Path | Notes |
| --- | --- | --- | --- |
| Codex | Tested | `.agents/skills`, `~/.agents/skills`, or configured Codex skills directory | Uses `SKILL.md` frontmatter and repo `AGENTS.md` instructions. |
| Claude Code | Expected | `~/.claude/skills`, `.claude/skills`, or `.claude-plugin/marketplace.json` | Marketplace entry exposes `./skills` with `strict: false`; validate with `claude plugin validate .` when Claude Code is available. |
| Claude.ai / API | Expected | Custom Skills upload or project knowledge, depending on plan/API wrapper | Skill discovery and automatic selection are not equivalent to Claude Code; reference skills explicitly in conversation or project context. Scripts are optional helper tools. |
| Cursor | Expected | Project rules plus copied `skills/<skill-name>` folders | Cursor may not auto-discover `SKILL.md`; use explicit prompts or rules that point to the folder. |
| GitHub Copilot | Expected | Repository instructions plus copied skill docs | Use as workflow reference in chat or coding-agent instructions; executable helpers require local approval. |
| OpenCode | Expected | Agent skills directory or explicit repo reference | Confirm the runtime reads YAML frontmatter and resolves `references/` relative to each skill. |
| Gemini CLI | Expected | Project instructions plus explicit skill folder references | Treat skills as Markdown workflows unless the CLI supports native skill discovery. |
| Windsurf | Expected | Rules/context files plus copied skill folders | Use explicit invocation prompts if automatic selection is unavailable. |
| Cline / Roo | Expected | Project rules or custom instructions pointing at `skills/` | Keep script execution gated by user approval in the agent configuration. |

## Known Limitations

- Some agents can read `SKILL.md` but do not auto-select skills from frontmatter descriptions.
- Some agents ignore `references/`; in that case, point them at the relevant files manually.
- Helper scripts are optional. Agents that cannot execute scripts can still use the report-first workflows.
- Claude Code marketplace validation requires Claude Code to be installed locally.
- Claude Code marketplace command syntax is documented from CLI help and manifest validation, but install flow should remain `Expected` until a real marketplace install is smoke-tested.
- The compatibility matrix distinguishes `Tested` from `Expected`; do not upgrade an agent to `Tested` without a reproducible install note.

## Smoke Test

For every supported agent, verify:

1. Install one skill and all skills.
2. Invoke one explicit prompt from [docs/examples.md](docs/examples.md).
3. Confirm referenced files under `references/` resolve relative to the skill folder.
4. Confirm the agent asks before editing implementation code when a skill says report-first.
5. Record unsupported behavior or setup gaps before recommending the agent path.
