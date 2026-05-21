# Bootcamp Test Matrix

Use this matrix when hardening `dependency-security-auditor` or checking that a public install behaves clearly across supported dependency surfaces.

The fixture suite lives under `tests/fixtures/bootcamp/`. Each folder is intentionally tiny and public-safe.

## Fixture Environments

| Fixture | Surface | Expected Detection | Expected Native Path | Expected Notes |
| --- | --- | --- | --- | --- |
| `npm` | Node with npm lockfile | `node`, `package-lock.json`, `packageManager=npm` | `npm audit --package-lock-only --json` | None |
| `pnpm` | Node with pnpm lockfile | `node`, `pnpm-lock.yaml`, `packageManager=pnpm` | `pnpm audit --json` | None |
| `yarn` | Node with Yarn lockfile | `node`, `yarn.lock`, `packageManager=yarn` | `yarn npm audit --recursive --json` | May report `unsupported` on Yarn Classic |
| `bun-native-gap` | Node with Bun lockfile | `node`, `bun.lock`, `packageManager=bun` | Native audit intentionally skipped | Bun native audit gap |
| `mixed-npm-with-stale-pnpm` | npm project with stale pnpm lockfile | `node`, both lockfiles, `packageManager=npm` | npm only | Conflicting package-manager evidence |
| `node-manifest-only` | Node manifest without lockfile | `node`, no lockfile | No native audit | Weak evidence |
| `python-requirements-pinned` | Python pinned requirements | `python`, `requirements.txt` | `pip-audit -r requirements.txt --format json` | None |
| `python-requirements-unpinned` | Python unpinned requirements | `python`, `requirements.txt` | `pip-audit -r requirements.txt --format json` | Weak evidence |
| `python-lock` | Python project lockfile | `python`, `pylock.toml` | `pip-audit --locked --format json .` | None |
| `flutter-pub` | Flutter/Dart lockfile | `dart`, `pubspec.lock` | OSV/Trivy CVE coverage; `dart pub outdated --json` for freshness | None |

## Verification Commands

Run the deterministic unit and repository checks:

```bash
pytest
ruby scripts/validate_skills.rb
git diff --check
```

Run the opt-in real CLI integration checks when network access is available:

```bash
RUN_DEPENDENCY_AUDIT_INTEGRATION=1 pytest tests/test_dependency_audit_integration.py -rs
```

Run one dry-run per fixture to confirm discovery behavior:

```bash
for d in tests/fixtures/bootcamp/*; do
  name=$(basename "$d")
  python3 skills/dependency-security-auditor/scripts/dependency_audit.py \
    --root "$d" \
    --dry-run \
    --report "/tmp/dependency-bootcamp-$name.json"
done
```

## Findings From Current Bootcamp Pass

- All bootcamp fixtures are detected as exactly one project.
- npm, pnpm, Yarn, pinned Python requirements, unpinned Python requirements, Python lockfiles, Flutter/Dart pub locks, Bun locks, manifest-only Node, and stale mixed Node lockfiles are covered.
- Stale pnpm lockfiles no longer override `packageManager=npm`.
- Unpinned Python requirements are flagged as weak evidence.
- Missing or unsupported scanners produce setup hints instead of vague failures.
- Yarn Classic is treated as unsupported native audit coverage, not as a mysterious scanner crash.
