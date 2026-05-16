from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "skills" / "dependency-security-auditor" / "scripts" / "dependency_audit.py"


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
