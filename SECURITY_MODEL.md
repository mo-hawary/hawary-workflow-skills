# Security Model

This document describes the security-relevant behavior of the bundled dependency-audit helper and the repository validation workflow.

## `dependency_audit.py`

Path: `skills/dependency-security-auditor/scripts/dependency_audit.py`

### Read Scope

- Reads dependency manifests and lockfiles under the requested `--root`.
- Skips common generated or heavy directories such as `.git`, `node_modules`, build output, virtual environments, and test fixtures.
- Reads package-manager metadata only to detect ecosystem, lockfile status, and package-manager choice.
- Read scope: intentionally limited to dependency and project metadata needed for audit evidence.

### Write Scope

- Does not edit manifests, lockfiles, source code, hooks, or CI configuration.
- Writes a JSON report only when called with `--report <path>`.
- Uses process exit codes for severity gates when `--fail-on` or freshness failure flags are provided.
- Write scope: excludes application code and package state.

### Network Behavior

- Network behavior: the helper itself does not make HTTP requests directly.
- Invoked scanners or package managers may contact registries or vulnerability databases, depending on the tool:
  - `osv-scanner`
  - `npm audit`
  - `pnpm audit`
  - `yarn npm audit`
  - `pip-audit`
  - `npm outdated`
  - `pnpm outdated`
  - `dart pub outdated`
  - `flutter pub outdated`

### Command Execution

- Commands are selected from discovered ecosystems and available tools.
- Missing scanners are reported as setup gaps; the helper does not install them.
- Scanner failures are preserved as `scanner_error` instead of being treated as clean results.
- Outdated packages are reported separately from CVEs.
- Command execution: deterministic from discovered stack evidence and explicit CLI flags.

## Repository Validator

Path: `scripts/validate_skills.rb`

- Checks skill frontmatter, naming, referenced files, agent metadata, local Markdown links, tracked symlinks, and public-release hygiene patterns.
- Reads tracked files from git and validates Markdown links against tracked targets.
- Does not write files or change git state.

## Recommended Controls

- Run validation in CI on pull requests and pushes to `main`.
- Pin GitHub Actions by commit SHA.
- Use hash-checked Python test dependencies.
- Review generated release artifacts before publishing.
- Treat new scripts as security-sensitive and document their read, write, network, and command behavior here.
