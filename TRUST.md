# Trust

Hawary Workflow Skills is designed as a report-first workflow package for coding agents. The skills should help an agent inspect, plan, review, or verify work before changing a repository.

## Safety Commitments

- Report-first behavior is the default for audits, reviews, QA, docs cleanup, and dependency checks.
- Skills require explicit approval before implementation edits, dependency upgrades, lockfile changes, hook installation, CI changes, or destructive actions.
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

## What The Package Should Not Do

- Change application code without explicit approval.
- Install scanners or package managers without explicit approval.
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
