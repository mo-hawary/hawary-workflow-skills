# Hook And CI Examples

## Husky Pre-Push

Use `pre-push`, not `pre-commit`, for full dependency audits.

```bash
#!/usr/bin/env sh
. "$(dirname -- "$0")/_/husky.sh"

python3 skills/dependency-security-auditor/scripts/dependency_audit.py \
  --root . \
  --mode pre-push \
  --skip-freshness \
  --fail-on high \
  --report dependency-security-report.json
```

## pre-commit Framework

```yaml
repos:
  - repo: local
    hooks:
      - id: dependency-security-audit
        name: Dependency security audit
        entry: python3 skills/dependency-security-auditor/scripts/dependency_audit.py --root . --mode pre-push --skip-freshness --fail-on high
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

jobs:
  dependency-security:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Install OSV-Scanner
        run: |
          curl -sSfL https://raw.githubusercontent.com/google/osv-scanner/main/install.sh | sh -s -- -b /usr/local/bin

      - name: Install Python audit tool
        run: python3 -m pip install --user pip-audit

      - name: Run dependency security audit
        run: |
          python3 skills/dependency-security-auditor/scripts/dependency_audit.py \
            --root . \
            --mode ci \
            --fail-on high \
            --report dependency-security-report.json

      - name: Upload dependency report
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: dependency-security-report
          path: dependency-security-report.json
```

## OSV-Scanner Action

For GitHub-native code scanning, use the official OSV-Scanner action or reusable workflows. Pair that with the bundled script when you want local and CI output to match.

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
python3 skills/dependency-security-auditor/scripts/dependency_audit.py --root . --mode pre-push --skip-freshness --fail-on high

# CI gate with freshness report
python3 skills/dependency-security-auditor/scripts/dependency_audit.py --root . --mode ci --fail-on high --report dependency-security-report.json

# Scheduled freshness/security report
python3 skills/dependency-security-auditor/scripts/dependency_audit.py --root . --mode scheduled --fail-on critical --report dependency-security-report.json
```
