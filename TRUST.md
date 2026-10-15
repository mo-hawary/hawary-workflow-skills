# Trust

Hawary Workflow Skills is designed as a report-first workflow package for coding agents. The skills should help an agent inspect, plan, review, or verify work before changing a repository.

## Safety Commitments

- Report-first behavior is the default for audits, reviews, QA, docs cleanup, and dependency checks.
- Skills require explicit approval before implementation edits, dependency upgrades, lockfile changes, hook installation, CI changes, or destructive actions.
- `fast-codex-subagents` chooses the smallest useful team automatically, launches writers only for a user-requested implementation, and records requested/effective/upstream tier evidence separately.
- `fast-codex-subagents` setup commands preview skill installs, saved defaults, and shared-instruction edits; they make no setup change without an explicit apply step.
- The Codex-only helper defaults to one schema-pinned app-server process with separate logical threads, `on-request` approvals, automatic review, and role-specific sandboxes. It never blanket-approves requests or retries another backend/tier silently.
- The app-server adapter is experimental and requires Codex CLI 0.162.1 or later (0.162.1 is the tested release; newer releases warn and ask for a compatibility report). Doctor checks the generated protocol schema and `model/list` without inference; fake tests are not evidence of a paid model tier.
- No hidden credential collection is part of any skill or bundled script.
- No automatic package upgrades are performed by the dependency auditor.
- Users adopting new skills or scripts should review pull request changes before running them, even in this package.
- Scripts are helper tools, not hidden instructions; agents can still use the Markdown workflow without running scripts.
- Documentation and examples must avoid customer data, local absolute paths, proprietary code, and sensitive project names.

## What The Package Can Do

- Read project files to understand repo state, docs, contracts, dependencies, and tests.
- Produce source-backed reports, specs, review findings, QA plans, and verification steps.
- Run the dependency-audit helper only when the user or agent environment allows command execution.
- Write a JSON dependency report only when the helper is invoked with a report path.
- Launch scoped Codex app-server logical writer/reviewer threads when `fast-codex-subagents` is selected, with authenticated loopback control and private local runtime state.
- Modify selected skill homes, a dedicated defaults file, or a selected Codex instruction file only through the explicit setup/apply commands.

## What The Package Should Not Do

- Change application code without explicit approval.
- Install scanners or package managers without explicit approval.
- Treat an enabled `fast_mode` feature or observed response speed as proof that a task session used fast mode.
- Stop worker processes during monitor cleanup or restart a task from saved state without a new explicit launch.
- Send repository contents to an external service by itself.
- Claim verification passed without observed evidence.
- Treat documentation as source of truth when implementation contradicts it.

## Maintainer Checklist

- Run `ruby scripts/validate_skills.rb`.
- Run `pytest`.
- Run `git diff --check`.
- Check for sensitive terms before publishing.
- Keep canonical skill source under `skills/`.
- Keep public adapters pointed at canonical skill folders.
