# Scanner Policy

## Preferred Stack

Use multiple sources because no single vulnerability database catches everything at the same time.

| Layer | Tool | Use |
| --- | --- | --- |
| Cross-ecosystem CVEs | OSV-Scanner | Default scanner for mixed repos and lockfiles. |
| Native Node audit | npm, pnpm, Yarn audit | Ecosystem-specific npm advisory checks. |
| Native Python audit | pip-audit | PyPI/PyPA vulnerability checks for Python environments and requirements files. |
| Flutter/Dart CVEs | OSV-Scanner or Trivy | Scan `pubspec.lock`; `pub outdated` is freshness, not CVEs. |
| CI backstop | Trivy | Repository/container/SBOM scan, useful in CI and scheduled jobs. |
| SBOM generation | Syft | Optional CycloneDX/SPDX inventory for release workflows. |
| SBOM vulnerability scan | Grype or Trivy | Optional when an organization wants SBOM-first reporting. |

## Lockfile Rules

- Prefer exact lockfiles over manifests.
- Treat no lockfile as weak evidence unless the package manager can resolve exact versions in the scan.
- For Node, use the lockfile matching the package manager.
- For Python, prefer pinned `requirements.txt`, `uv.lock`, `poetry.lock`, or `Pipfile.lock`.
- For Flutter applications, commit `pubspec.lock` and scan it.

## Failure Policy

Recommended defaults:

| Context | Fail On | Notes |
| --- | --- | --- |
| pre-commit | changed lockfile with critical/high only | Keep commits fast. |
| pre-push | high/critical | Good local safety gate. |
| PR CI | high/critical or newly introduced vulnerable dependency | Use annotations or uploaded report. |
| release CI | moderate with fix available, high, critical | Stricter before release. |
| scheduled CI | no hard fail or issue creation | Alert on newly disclosed issues. |

## Runtime And Freshness Policy

Runtime/freshness checks answer a different question from CVE scans:

- Runtime: which tool versions are actually available where the scan runs.
- Locked/current: which dependency versions the project resolves to.
- Wanted: latest version allowed by the current manifest constraint.
- Latest: newest version available from the registry.

Treat freshness as prioritization, not vulnerability evidence:

| Lag | Meaning | Default Action |
| --- | --- | --- |
| patch | likely safe maintenance update | Recommend after tests. |
| minor | feature-level drift | Review release notes. |
| major | possible breaking change | Plan separately from CVE fixes. |

Use freshness in CI and scheduled jobs by default. Skip it in pre-push if registry calls are too slow.

## Triage Rules

- First check whether the locked version falls inside the advisory range.
- Then check whether the vulnerable package is direct or transitive.
- Then check whether the vulnerable code path exists in runtime.
- If a scanner suggests a downgrade or a major upgrade, verify with advisory ranges before recommending it.
- Prefer minimal patches, overrides, or parent dependency upgrades before broad major upgrades.

## Tool Notes

- OSV-Scanner supports many lockfiles, including Node lockfiles, Python lockfiles, `requirements.txt`, and Dart `pubspec.lock`.
- Trivy can scan repository files and language-specific packages, and is useful as a CI backstop.
- `pip-audit` is Python-focused and maintained in the PyPA ecosystem.
- `npm audit` requires a package lock or shrinkwrap for normal audit behavior.
- `dart pub outdated` and `flutter pub outdated` identify stale dependencies but do not replace CVE scanning.
