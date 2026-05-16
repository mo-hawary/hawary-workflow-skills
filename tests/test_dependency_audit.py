from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "skills" / "dependency-security-auditor" / "scripts" / "dependency_audit.py"
FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures" / "known-vulnerable-node"


def load_dependency_audit():
    spec = importlib.util.spec_from_file_location("dependency_audit", SCRIPT_PATH)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_status_reports_scanner_unavailable_when_projects_exist_and_scanners_are_missing(tmp_path, monkeypatch):
    module = load_dependency_audit()
    (tmp_path / "package.json").write_text('{"dependencies":{"left-pad":"1.3.0"}}\n', encoding="utf-8")
    (tmp_path / "package-lock.json").write_text('{"lockfileVersion":3,"packages":{}}\n', encoding="utf-8")

    def fake_which(tool: str) -> str | None:
        return None if tool in {"osv-scanner", "npm"} else f"/bin/{tool}"

    monkeypatch.setattr(module.shutil, "which", fake_which)

    projects = module.discover_projects(tmp_path)
    runs = [module.run_osv(tmp_path)[1]]
    _, native_runs = module.run_native_audits(tmp_path, projects)
    runs.extend(native_runs)

    assert module.report_status([], runs, projects) == "scanner_unavailable"


def test_skip_freshness_prevents_dart_outdated_during_native_audit(tmp_path, monkeypatch):
    module = load_dependency_audit()
    (tmp_path / "pubspec.yaml").write_text("name: sample\n", encoding="utf-8")
    (tmp_path / "pubspec.lock").write_text("packages: {}\n", encoding="utf-8")
    calls: list[list[str]] = []

    def fake_run_json(command: list[str], cwd: Path):
        calls.append(command)
        return module.ToolRun(command[0], command, str(cwd), available=True, exit_code=0), {}

    monkeypatch.setattr(module, "run_json", fake_run_json)
    monkeypatch.setattr(module.shutil, "which", lambda tool: f"/bin/{tool}")

    module.run_native_audits(tmp_path, module.discover_projects(tmp_path))

    assert calls == []


def test_python_outdated_json_is_normalized_into_freshness_findings(tmp_path, monkeypatch):
    module = load_dependency_audit()
    (tmp_path / "requirements.txt").write_text("django==4.2.0\n", encoding="utf-8")

    def fake_run_json(command: list[str], cwd: Path):
        assert command == ["python3", "-m", "pip", "list", "--outdated", "--format", "json"]
        data = [{"name": "Django", "version": "4.2.0", "latest_version": "5.0.1"}]
        return module.ToolRun(command[0], command, str(cwd), available=True, exit_code=0), data

    monkeypatch.setattr(module, "run_json", fake_run_json)
    monkeypatch.setattr(module.shutil, "which", lambda tool: f"/bin/{tool}")

    freshness, _ = module.run_freshness_checks(tmp_path, module.discover_projects(tmp_path))

    assert freshness[0].ecosystem == "pypi"
    assert freshness[0].package == "Django"
    assert freshness[0].current == "4.2.0"
    assert freshness[0].latest == "5.0.1"
    assert freshness[0].update_type == "major"


def test_osv_standard_cvss_severity_is_normalized_for_fail_thresholds():
    module = load_dependency_audit()
    data = {
        "results": [
            {
                "packages": [
                    {
                        "package": {"name": "example", "version": "1.0.0"},
                        "locations": [{"path": "package-lock.json"}],
                        "vulnerabilities": [
                            {
                                "id": "GHSA-test",
                                "summary": "High impact advisory",
                                "severity": [
                                    {
                                        "type": "CVSS_V3",
                                        "score": "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H",
                                    }
                                ],
                            }
                        ],
                    }
                ]
            }
        ]
    }

    findings = module.parse_osv(data)

    assert findings[0].severity == "critical"
    assert module.severity_value(findings[0].severity) >= module.severity_value("high")


def test_osv_cvss_v4_severity_is_normalized_for_fail_thresholds():
    module = load_dependency_audit()
    data = {
        "results": [
            {
                "packages": [
                    {
                        "package": {"name": "example", "version": "1.0.0"},
                        "vulnerabilities": [
                            {
                                "id": "GHSA-cvss4",
                                "summary": "CVSS v4 advisory",
                                "severity": [
                                    {
                                        "type": "CVSS_V4",
                                        "score": "CVSS:4.0/AV:N/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:H/SC:H/SI:H/SA:H",
                                    }
                                ],
                            }
                        ],
                    }
                ]
            }
        ]
    }

    findings = module.parse_osv(data)

    assert findings[0].severity == "critical"
    assert module.severity_value(findings[0].severity) >= module.severity_value("high")


def test_run_osv_scans_recursively(tmp_path, monkeypatch):
    module = load_dependency_audit()
    commands: list[list[str]] = []

    def fake_run_json(command: list[str], cwd: Path):
        commands.append(command)
        return module.ToolRun(command[0], command, str(cwd), available=True, exit_code=0), {}

    monkeypatch.setattr(module, "run_json", fake_run_json)

    module.run_osv(tmp_path)

    assert commands == [["osv-scanner", "scan", "source", "--recursive", str(tmp_path), "--format", "json"]]


def test_yarn_audit_results_are_converted_to_findings(tmp_path, monkeypatch):
    module = load_dependency_audit()
    (tmp_path / "package.json").write_text('{"dependencies":{"left-pad":"1.3.0"}}\n', encoding="utf-8")
    (tmp_path / "yarn.lock").write_text("# yarn lockfile\n", encoding="utf-8")
    audit_data = {
        "vulnerabilities": {
            "left-pad": {
                "severity": "high",
                "via": [{"title": "Example advisory", "url": "GHSA-test", "severity": "high"}],
                "fixAvailable": {"version": "1.3.1"},
            }
        }
    }

    def fake_run_json(command: list[str], cwd: Path):
        assert command == ["yarn", "npm", "audit", "--json"]
        return module.ToolRun(command[0], command, str(cwd), available=True, exit_code=1), audit_data

    monkeypatch.setattr(module, "run_json", fake_run_json)

    findings, _ = module.run_native_audits(tmp_path, module.discover_projects(tmp_path))

    assert findings[0].source == "yarn npm audit"
    assert findings[0].package == "left-pad"
    assert findings[0].severity == "high"


def test_python_lockfile_projects_run_pip_audit_locked(tmp_path, monkeypatch):
    module = load_dependency_audit()
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'sample'\nversion = '0.1.0'\n", encoding="utf-8")
    (tmp_path / "poetry.lock").write_text("# poetry lock\n", encoding="utf-8")
    calls: list[list[str]] = []

    def fake_run_json(command: list[str], cwd: Path):
        calls.append(command)
        return module.ToolRun(command[0], command, str(cwd), available=True, exit_code=0), {"dependencies": []}

    monkeypatch.setattr(module, "run_json", fake_run_json)

    module.run_native_audits(tmp_path, module.discover_projects(tmp_path))

    assert calls == [["pip-audit", "--locked", "--format", "json"]]


def test_npm_pnpm_and_pip_audit_parsers_normalize_findings():
    module = load_dependency_audit()

    npm_findings = module.parse_npm_audit(
        {
            "vulnerabilities": {
                "lodash": {
                    "severity": "high",
                    "via": [{"title": "Prototype pollution", "url": "GHSA-lodash", "severity": "high"}],
                    "fixAvailable": {"version": "4.17.21"},
                }
            }
        },
        ".",
    )
    pnpm_findings = module.parse_pnpm_audit(
        {
            "advisories": {
                "100": {
                    "module_name": "minimist",
                    "severity": "moderate",
                    "url": "GHSA-minimist",
                    "title": "Prototype pollution",
                    "findings": [{"version": "0.0.8"}],
                    "patched_versions": [">=1.2.6"],
                }
            }
        },
        ".",
    )
    pip_findings = module.parse_pip_audit(
        {
            "dependencies": [
                {
                    "name": "django",
                    "version": "2.2.0",
                    "vulns": [
                        {
                            "id": "PYSEC-1",
                            "aliases": ["CVE-1234"],
                            "severity": "HIGH",
                            "fix_versions": ["4.2.0"],
                            "summary": "Example issue",
                        }
                    ],
                }
            ]
        },
        ".",
    )

    assert npm_findings[0].package == "lodash"
    assert npm_findings[0].fixed_versions == ["4.17.21"]
    assert pnpm_findings[0].package == "minimist"
    assert pnpm_findings[0].version == "0.0.8"
    assert pip_findings[0].package == "django"
    assert pip_findings[0].fixed_versions == ["4.2.0"]


def test_main_exits_nonzero_when_scanner_errors_without_findings(tmp_path, monkeypatch):
    module = load_dependency_audit()
    report_path = tmp_path / "report.json"
    (tmp_path / "package.json").write_text('{"dependencies":{"left-pad":"1.3.0"}}\n', encoding="utf-8")
    (tmp_path / "package-lock.json").write_text('{"lockfileVersion":3,"packages":{}}\n', encoding="utf-8")

    monkeypatch.setattr(
        module,
        "run_osv",
        lambda root: ([], module.ToolRun("osv-scanner", ["osv-scanner"], str(root), available=True, exit_code=2, stderr="boom")),
    )
    monkeypatch.setattr(module, "run_native_audits", lambda root, projects: ([], []))
    monkeypatch.setattr(module, "run_freshness_checks", lambda root, projects: ([], []))
    monkeypatch.setattr(module, "detect_runtime", lambda root, projects: ([], []))
    monkeypatch.setattr(module.sys, "argv", ["dependency_audit.py", "--root", str(tmp_path), "--report", str(report_path)])

    assert module.main() == 1
    report = report_path.read_text(encoding="utf-8")
    assert '"status": "scanner_error"' in report
    assert '"failed_tools": [' in report


def test_main_respects_fail_on_threshold_for_native_vulnerability_exit_codes(tmp_path, monkeypatch):
    module = load_dependency_audit()
    report_path = tmp_path / "report.json"
    (tmp_path / "package.json").write_text('{"dependencies":{"left-pad":"1.3.0"}}\n', encoding="utf-8")
    (tmp_path / "package-lock.json").write_text('{"lockfileVersion":3,"packages":{}}\n', encoding="utf-8")
    low_finding = module.Finding(
        source="npm audit",
        ecosystem="npm",
        package="left-pad",
        version="1.3.0",
        severity="low",
        advisory="GHSA-low",
        title="Low issue",
        path=".",
    )

    monkeypatch.setattr(module, "run_osv", lambda root: ([], module.ToolRun("osv-scanner", ["osv-scanner"], str(root), available=True, exit_code=0)))
    monkeypatch.setattr(
        module,
        "run_native_audits",
        lambda root, projects: ([low_finding], [module.ToolRun("npm", ["npm", "audit"], str(root), available=True, exit_code=1)]),
    )
    monkeypatch.setattr(module, "run_freshness_checks", lambda root, projects: ([], []))
    monkeypatch.setattr(module, "detect_runtime", lambda root, projects: ([], []))
    monkeypatch.setattr(module.sys, "argv", ["dependency_audit.py", "--root", str(tmp_path), "--fail-on", "high", "--report", str(report_path)])

    assert module.main() == 0
    assert '"status": "vulnerable"' in report_path.read_text(encoding="utf-8")


def test_verbose_mode_prints_diagnostics(tmp_path, monkeypatch, capsys):
    module = load_dependency_audit()
    (tmp_path / "package.json").write_text('{"dependencies":{"left-pad":"1.3.0"}}\n', encoding="utf-8")
    (tmp_path / "bun.lock").write_text("# bun lock\n", encoding="utf-8")

    monkeypatch.setattr(module, "run_osv", lambda root: ([], module.ToolRun("osv-scanner", ["osv-scanner"], str(root), available=False)))
    monkeypatch.setattr(module, "run_native_audits", lambda root, projects: ([], []))
    monkeypatch.setattr(module, "run_freshness_checks", lambda root, projects: ([], []))
    monkeypatch.setattr(module, "detect_runtime", lambda root, projects: ([], []))
    monkeypatch.setattr(module.sys, "argv", ["dependency_audit.py", "--root", str(tmp_path), "--verbose"])

    module.main()
    captured = capsys.readouterr()

    assert "detected node project" in captured.err
    assert "missing tool: osv-scanner" in captured.err


def test_bun_lock_adds_note_about_native_audit_gap(tmp_path):
    module = load_dependency_audit()
    (tmp_path / "package.json").write_text('{"dependencies":{"hono":"4.0.0"}}\n', encoding="utf-8")
    (tmp_path / "bun.lock").write_text("# bun lock\n", encoding="utf-8")

    projects = module.discover_projects(tmp_path)

    assert any("Bun" in note for note in projects[0].notes)


def test_known_vulnerable_fixture_is_discovered_as_locked_node_project():
    module = load_dependency_audit()

    projects = module.discover_projects(FIXTURE_ROOT)

    assert projects[0].ecosystem == "node"
    assert projects[0].lockfiles == ["package-lock.json"]
    assert projects[0].manifests == ["package.json"]


def test_dry_run_reports_detected_projects_without_running_scanners(tmp_path, monkeypatch):
    module = load_dependency_audit()
    report_path = tmp_path / "report.json"
    (tmp_path / "requirements.txt").write_text("requests==2.31.0\n", encoding="utf-8")
    called = False

    def fail_if_called(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("scanner should not run during dry-run")

    monkeypatch.setattr(module, "run_osv", fail_if_called)
    monkeypatch.setattr(module, "run_native_audits", fail_if_called)
    monkeypatch.setattr(module, "run_freshness_checks", fail_if_called)
    monkeypatch.setattr(module.sys, "argv", ["dependency_audit.py", "--root", str(tmp_path), "--dry-run", "--report", str(report_path)])

    assert module.main() == 0
    assert called is False
    assert '"status": "dry_run"' in report_path.read_text(encoding="utf-8")
