from __future__ import annotations

import importlib.util
import os
import shutil
import sys
from pathlib import Path

import pytest


if os.environ.get("RUN_DEPENDENCY_AUDIT_INTEGRATION") != "1":
    pytest.skip("set RUN_DEPENDENCY_AUDIT_INTEGRATION=1 to run real dependency audit CLI fixtures", allow_module_level=True)


pytestmark = pytest.mark.integration

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "skills" / "dependency-security-auditor" / "scripts" / "dependency_audit.py"
FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures" / "integration"


def load_dependency_audit():
    spec = importlib.util.spec_from_file_location("dependency_audit_integration", SCRIPT_PATH)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def require_tool(tool: str) -> None:
    if shutil.which(tool) is None:
        pytest.skip(f"{tool} is not installed")


def copy_fixture(tmp_path: Path, fixture_name: str) -> Path:
    target = tmp_path / fixture_name
    shutil.copytree(FIXTURE_ROOT / fixture_name, target)
    return target


def assert_no_scanner_error(run) -> None:
    if run.tool_status == "scanner_error":
        pytest.fail(f"{run.tool} failed with exit {run.exit_code}: {run.stderr}")


def test_npm_fixture_runs_real_native_audit(tmp_path):
    require_tool("npm")
    module = load_dependency_audit()
    root = copy_fixture(tmp_path, "npm")

    findings, runs = module.run_native_audits(root, module.discover_projects(root))

    npm_run = next(run for run in runs if run.tool == "npm")
    assert npm_run.command == ["npm", "audit", "--package-lock-only", "--json"]
    assert_no_scanner_error(npm_run)
    assert isinstance(findings, list)


def test_pnpm_fixture_runs_real_native_audit(tmp_path):
    require_tool("pnpm")
    module = load_dependency_audit()
    root = copy_fixture(tmp_path, "pnpm")

    findings, runs = module.run_native_audits(root, module.discover_projects(root))

    pnpm_run = next(run for run in runs if run.tool == "pnpm")
    assert pnpm_run.command == ["pnpm", "audit", "--json"]
    if pnpm_run.tool_status == "scanner_error" and "fetch failed" in pnpm_run.stderr.lower():
        pytest.skip("pnpm audit could not reach the registry")
    assert_no_scanner_error(pnpm_run)
    assert isinstance(findings, list)


def test_yarn_workspace_fixture_runs_real_recursive_workspace_audit(tmp_path):
    require_tool("yarn")
    module = load_dependency_audit()
    root = copy_fixture(tmp_path, "yarn-workspace")

    findings, runs = module.run_native_audits(root, module.discover_projects(root))

    yarn_run = next(run for run in runs if run.tool == "yarn")
    assert yarn_run.command == ["yarn", "npm", "audit", "--all", "--recursive", "--json"]
    if yarn_run.tool_status == "unsupported":
        assert any("Yarn" in hint for hint in module.setup_hints(runs, module.discover_projects(root)))
        assert module.report_status(findings, runs, module.discover_projects(root)) == "scanner_unavailable"
        return
    assert_no_scanner_error(yarn_run)
    assert isinstance(findings, list)


def test_python_locked_fixture_runs_real_pip_audit(tmp_path):
    module = load_dependency_audit()
    root = copy_fixture(tmp_path, "python-locked")

    findings, runs = module.run_native_audits(root, module.discover_projects(root))

    pip_audit_run = next(run for run in runs if run.tool == "pip-audit")
    assert pip_audit_run.command == ["pip-audit", "--locked", "--format", "json", "."]
    if pip_audit_run.tool_status == "unavailable":
        assert "python3 -m pip install pip-audit" in module.setup_hints(runs, module.discover_projects(root))
        assert module.report_status(findings, runs, module.discover_projects(root)) == "scanner_unavailable"
        return
    assert_no_scanner_error(pip_audit_run)
    assert isinstance(findings, list)


def test_dart_fixture_runs_real_freshness_check(tmp_path, monkeypatch):
    require_tool("dart")
    module = load_dependency_audit()
    root = copy_fixture(tmp_path, "dart")
    host_which = shutil.which

    def prefer_dart(tool: str) -> str | None:
        if tool == "flutter":
            return None
        return host_which(tool)

    monkeypatch.setattr(module.shutil, "which", prefer_dart)
    freshness, runs = module.run_freshness_checks(root, module.discover_projects(root))

    dart_run = next(run for run in runs if run.tool == "dart")
    assert dart_run.command == ["dart", "pub", "outdated", "--json"]
    assert_no_scanner_error(dart_run)
    assert isinstance(freshness, list)
