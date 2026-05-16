# Hook And CI Examples

## Script Path

Hooks and CI should not assume this skill repository is checked into the project being audited. Set one of these variables first:

```bash
export SKILL_ROOT="${SKILL_ROOT:-$HOME/.agents/skills/dependency-security-auditor}"
export DEPENDENCY_AUDIT_SCRIPT="${DEPENDENCY_AUDIT_SCRIPT:-$SKILL_ROOT/scripts/dependency_audit.py}"
```

If you vendor the skill into a repo, `SKILL_ROOT=skills/dependency-security-auditor` is fine. For CI, prefer downloading the script from a pinned tag or commit SHA before running it.

## Husky Pre-Push

Use `pre-push`, not `pre-commit`, for full dependency audits.

```bash
#!/usr/bin/env sh
. "$(dirname -- "$0")/_/husky.sh"

: "${DEPENDENCY_AUDIT_SCRIPT:?Set DEPENDENCY_AUDIT_SCRIPT to dependency_audit.py}"

python3 "$DEPENDENCY_AUDIT_SCRIPT" \
  --root . \
  --mode pre-push \
  --skip-freshness \
  --fail-on high \
  --report dependency-security-report.json
```

## pre-commit Framework (pre-push stage)

```yaml
repos:
  - repo: local
    hooks:
      - id: dependency-security-audit
        name: Dependency security audit
        # Dependency audits can hit registries and scanners, so keep them out of pre-commit.
        entry: bash -c 'python3 "$DEPENDENCY_AUDIT_SCRIPT" --root . --mode pre-push --skip-freshness --fail-on high'
        language: system
        pass_filenames: false
        stages: [pre-push]
```

## GitHub Actions

```yaml
name: Dependency Security

on:
  pull_request:
  push:
    branches: [main]
  schedule:
    - cron: "17 4 * * 1"

permissions:
  contents: read

env:
  HAWARY_WORKFLOW_SKILLS_REF: <tag-or-commit-sha>
  DEPENDENCY_AUDIT_SCRIPT: .github/tools/dependency_audit.py

jobs:
  osv-scan:
    # Broad cross-ecosystem coverage from the official pinned OSV reusable workflow.
    uses: "google/osv-scanner-action/.github/workflows/osv-scanner-reusable.yml@v2.3.8"
    with:
      scan-args: |-
        --recursive
        ./
    permissions:
      contents: read
      security-events: write
      actions: read

  dependency-security:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Download audit script
        run: |
          mkdir -p .github/tools
          curl -sSfL "https://raw.githubusercontent.com/mo-hawary/hawary-workflow-skills/${HAWARY_WORKFLOW_SKILLS_REF}/skills/dependency-security-auditor/scripts/dependency_audit.py" \
            -o "$DEPENDENCY_AUDIT_SCRIPT"
          chmod +x "$DEPENDENCY_AUDIT_SCRIPT"

      - name: Install Python audit tool
        run: python3 -m pip install pip-audit

      - name: Run dependency security audit
        # Skip OSV here because the dedicated job above handles it; keep native audits and freshness in this report.
        run: |
          python3 "$DEPENDENCY_AUDIT_SCRIPT" \
            --root . \
            --mode ci \
            --skip-osv \
            --fail-on high \
            --verbose \
            --report dependency-security-report.json

      - name: Upload dependency report
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: dependency-security-report
          path: dependency-security-report.json
```

## OSV-Scanner Action

For GitHub-native code scanning, use the official OSV-Scanner action or reusable workflows pinned to a version. Pair that with the bundled script using `--skip-osv` when you want native audit and freshness output without installing the OSV-Scanner CLI.

## Runtime And Freshness Modes

The bundled script separates security and freshness:

- `--skip-freshness`: CVE-only mode for fast hooks.
- `--freshness`: force runtime and latest-version checks.
- `--mode ci`: default full mode with freshness checks.
- `--mode scheduled`: same as CI, useful for weekly reports.
- `--fail-on-major-outdated`: optionally fail if any dependency is a major version behind.

Recommended usage:

```bash
# Fast local gate
python3 "$DEPENDENCY_AUDIT_SCRIPT" --root . --mode pre-push --skip-freshness --fail-on high

# CI gate with freshness report
python3 "$DEPENDENCY_AUDIT_SCRIPT" --root . --mode ci --fail-on high --report dependency-security-report.json

# Scheduled freshness/security report
python3 "$DEPENDENCY_AUDIT_SCRIPT" --root . --mode scheduled --fail-on critical --report dependency-security-report.json
```
