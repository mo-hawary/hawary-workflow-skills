#!/usr/bin/env python3
"""Stack-aware dependency vulnerability audit wrapper.

The script is intentionally dependency-free. It discovers common Node, Python,
and Flutter/Dart dependency files, runs installed scanners, and writes a
normalized JSON report suitable for hooks and CI.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


SKIP_DIRS = {
    ".git",
    ".next",
    ".open-next",
    ".venv",
    ".wrangler",
    "build",
    "dist",
    "fixtures",
    "node_modules",
    "venv",
}

SEVERITY_ORDER = {
    "unknown": 0,
    "info": 1,
    "low": 2,
    "moderate": 3,
    "medium": 3,
    "high": 4,
    "critical": 5,
}


CVSS_V3_METRICS = {
    "AV": {"N": 0.85, "A": 0.62, "L": 0.55, "P": 0.2},
    "AC": {"L": 0.77, "H": 0.44},
    "UI": {"N": 0.85, "R": 0.62},
    "CIA": {"H": 0.56, "L": 0.22, "N": 0.0},
}


@dataclass
class ProjectSignal:
    ecosystem: str
    path: str
    manifests: list[str] = field(default_factory=list)
    lockfiles: list[str] = field(default_factory=list)
    package_manager: str | None = None
    notes: list[str] = field(default_factory=list)


@dataclass
class Finding:
    source: str
    ecosystem: str
    package: str
    version: str | None
    severity: str
    advisory: str
    title: str
    fixed_versions: list[str] = field(default_factory=list)
    path: str | None = None


@dataclass
class ToolRun:
    tool: str
    command: list[str]
    cwd: str
    available: bool
    exit_code: int | None = None
    stderr: str = ""
    tool_status: str = "ok"

    def __post_init__(self) -> None:
        if not self.available:
            self.tool_status = "unavailable"
        elif self.exit_code not in (0, None) and self.tool_status == "ok":
            self.tool_status = "scanner_error"


@dataclass
class RuntimeInfo:
    ecosystem: str
    tool: str
    version: str | None
    available: bool
    path: str | None = None


@dataclass
class FreshnessFinding:
    ecosystem: str
    package: str
    current: str | None
    wanted: str | None
    latest: str | None
    update_type: str
    source: str
    path: str | None = None
    note: str | None = None


CVE_TOOLS = {"osv-scanner", "npm", "pnpm", "yarn", "pip-audit"}
INCOMPLETE_TOOL_STATUSES = {"no_packages_found", "unavailable", "unsupported"}
UNUSABLE_TOOL_STATUSES = {"unavailable", "unsupported"}
NODE_LOCKFILES_BY_MANAGER = {
    "npm": "package-lock.json",
    "pnpm": "pnpm-lock.yaml",
    "yarn": "yarn.lock",
    "bun": "bun.lock",
}


def debug(message: str, verbose: bool) -> None:
    if verbose:
        print(f"[dependency-audit] {message}", file=sys.stderr)


def severity_value(value: str | None) -> int:
    return SEVERITY_ORDER.get((value or "unknown").lower(), 0)


def severity_from_score(score: float) -> str:
    if score >= 9.0:
        return "critical"
    if score >= 7.0:
        return "high"
    if score >= 4.0:
        return "medium"
    if score > 0:
        return "low"
    return "unknown"


def round_up_one_decimal(value: float) -> float:
    return math.ceil(value * 10) / 10.0


def parse_cvss_v3_score(vector: str) -> float | None:
    if not vector.startswith("CVSS:3."):
        return None
    metrics: dict[str, str] = {}
    for part in vector.split("/")[1:]:
        if ":" not in part:
            continue
        key, value = part.split(":", 1)
        metrics[key] = value

    try:
        scope = metrics["S"]
        confidentiality = CVSS_V3_METRICS["CIA"][metrics["C"]]
        integrity = CVSS_V3_METRICS["CIA"][metrics["I"]]
        availability = CVSS_V3_METRICS["CIA"][metrics["A"]]
        impact_sub_score = 1 - ((1 - confidentiality) * (1 - integrity) * (1 - availability))
        if scope == "U":
            impact = 6.42 * impact_sub_score
        else:
            impact = 7.52 * (impact_sub_score - 0.029) - 3.25 * ((impact_sub_score - 0.02) ** 15)

        if impact <= 0:
            return 0.0

        privilege_required = metrics["PR"]
        if privilege_required == "N":
            privilege_value = 0.85
        elif privilege_required == "L":
            privilege_value = 0.62 if scope == "U" else 0.68
        elif privilege_required == "H":
            privilege_value = 0.27 if scope == "U" else 0.5
        else:
            return None

        exploitability = (
            8.22
            * CVSS_V3_METRICS["AV"][metrics["AV"]]
            * CVSS_V3_METRICS["AC"][metrics["AC"]]
            * privilege_value
            * CVSS_V3_METRICS["UI"][metrics["UI"]]
        )
        if scope == "U":
            return round_up_one_decimal(min(impact + exploitability, 10))
        return round_up_one_decimal(min(1.08 * (impact + exploitability), 10))
    except KeyError:
        return None


def parse_cvss_v4_score(vector: str) -> float | None:
    if not vector.startswith("CVSS:4.0/"):
        return None
    metrics: dict[str, str] = {}
    for part in vector.split("/")[1:]:
        if ":" not in part:
            continue
        key, value = part.split(":", 1)
        metrics[key] = value

    try:
        exploitability = [
            metrics["AV"] == "N",
            metrics["AC"] == "L",
            metrics["AT"] == "N",
            metrics["PR"] == "N",
            metrics["UI"] == "N",
        ]
        impacts = [metrics.get(key, "N") for key in ("VC", "VI", "VA", "SC", "SI", "SA")]
    except KeyError:
        return None

    high_impact_count = impacts.count("H")
    low_impact_count = impacts.count("L")
    exploitability_score = sum(1 for item in exploitability if item)

    if high_impact_count >= 3 and exploitability_score >= 4:
        return 9.0
    if high_impact_count > 0:
        return 7.0
    if low_impact_count > 0:
        return 4.0
    return 0.0


def normalize_osv_severity(vuln: dict[str, Any]) -> str:
    database_specific = vuln.get("database_specific")
    if isinstance(database_specific, dict) and database_specific.get("severity"):
        return str(database_specific["severity"]).lower()

    for severity_item in vuln.get("severity", []):
        if not isinstance(severity_item, dict):
            continue
        score = str(severity_item.get("score") or "")
        if not score:
            continue
        if score.lower() in SEVERITY_ORDER:
            return score.lower()
        numeric_match = re.search(r"\b\d+(?:\.\d+)?\b", score)
        if numeric_match and not score.startswith("CVSS:"):
            return severity_from_score(float(numeric_match.group(0)))
        cvss_score = parse_cvss_v3_score(score)
        if cvss_score is not None:
            return severity_from_score(cvss_score)
        cvss_v4_score = parse_cvss_v4_score(score)
        if cvss_v4_score is not None:
            return severity_from_score(cvss_v4_score)
    return "unknown"


def is_vulnerability_audit_command(command: list[str]) -> bool:
    if len(command) >= 3 and command[:3] == ["osv-scanner", "scan", "source"]:
        return True
    if len(command) >= 3 and command[:3] == ["yarn", "npm", "audit"]:
        return True
    if len(command) >= 2 and command[:2] in (["npm", "audit"], ["pnpm", "audit"]):
        return True
    if command and command[0] == "pip-audit" and "--version" not in command:
        return True
    return False


def is_nonfatal_data_command(command: list[str]) -> bool:
    if len(command) >= 2 and command[:2] in (["npm", "outdated"], ["pnpm", "outdated"]):
        return True
    if len(command) >= 3 and command[1:3] == ["pub", "outdated"]:
        return True
    return len(command) >= 5 and command[:5] == ["python3", "-m", "pip", "list", "--outdated"]


def parse_json_output(stdout: str, ndjson: bool = False) -> Any | None:
    if not ndjson:
        return json.loads(stdout or "{}")

    parsed_items: list[Any] = []
    for line in stdout.splitlines():
        if line.strip():
            parsed_items.append(json.loads(line))
    if not parsed_items:
        return {}

    for item in parsed_items:
        candidate = item.get("data") if isinstance(item, dict) and isinstance(item.get("data"), dict) else item
        if isinstance(candidate, dict) and any(key in candidate for key in ("vulnerabilities", "advisories")):
            return candidate
    return parsed_items[-1]


def is_unsupported_yarn_audit(command: list[str], stderr: str) -> bool:
    if len(command) < 3 or command[:3] != ["yarn", "npm", "audit"]:
        return False
    message = stderr.lower()
    return "command \"npm\" not found" in message or "unknown syntax error" in message or "unknown command" in message


def scanner_error_message(data: Any) -> str | None:
    if not isinstance(data, dict):
        return None
    error = data.get("error")
    if not isinstance(error, dict):
        return None
    message = error.get("message") or error.get("code")
    return str(message) if message else None


def run_json(command: list[str], cwd: Path, ndjson: bool = False) -> tuple[ToolRun, Any | None]:
    tool = command[0]
    if shutil.which(tool) is None:
        return ToolRun(tool=tool, command=command, cwd=str(cwd), available=False), None

    proc = subprocess.run(command, cwd=cwd, text=True, capture_output=True, check=False)
    tool_run = ToolRun(
        tool=tool,
        command=command,
        cwd=str(cwd),
        available=True,
        exit_code=proc.returncode,
        stderr=proc.stderr.strip(),
    )
    try:
        data = parse_json_output(proc.stdout, ndjson=ndjson)
    except json.JSONDecodeError:
        if is_unsupported_yarn_audit(command, tool_run.stderr):
            tool_run.tool_status = "unsupported"
        return tool_run, None
    if is_unsupported_yarn_audit(command, tool_run.stderr):
        tool_run.tool_status = "unsupported"
        return tool_run, data
    if tool_run.exit_code not in (0, None) and not tool_run.stderr:
        message = scanner_error_message(data)
        if message:
            tool_run.stderr = message
    if tool_run.exit_code == 128 and len(command) >= 3 and command[:3] == ["osv-scanner", "scan", "source"]:
        tool_run.tool_status = "no_packages_found"
    elif tool_run.exit_code not in (0, None) and data is not None and is_nonfatal_data_command(command):
        tool_run.tool_status = "ok"
    return tool_run, data


def run_text(command: list[str], cwd: Path) -> tuple[ToolRun, str]:
    tool = command[0]
    if shutil.which(tool) is None:
        return ToolRun(tool=tool, command=command, cwd=str(cwd), available=False), ""

    proc = subprocess.run(command, cwd=cwd, text=True, capture_output=True, check=False)
    return (
        ToolRun(
            tool=tool,
            command=command,
            cwd=str(cwd),
            available=True,
            exit_code=proc.returncode,
            stderr=proc.stderr.strip(),
        ),
        (proc.stdout or proc.stderr).strip(),
    )


def first_semver(text: str) -> str | None:
    match = re.search(r"\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?", text)
    return match.group(0) if match else None


def classify_update(current: str | None, latest: str | None) -> str:
    if not current or not latest:
        return "unknown"
    current_parts = [int(part) for part in re.findall(r"\d+", current)[:3]]
    latest_parts = [int(part) for part in re.findall(r"\d+", latest)[:3]]
    if len(current_parts) < 3 or len(latest_parts) < 3:
        return "unknown"
    if current_parts == latest_parts:
        return "current"
    if latest_parts[0] > current_parts[0]:
        return "major"
    if latest_parts[1] > current_parts[1]:
        return "minor"
    if latest_parts[2] > current_parts[2]:
        return "patch"
    return "current"


def walk_dirs(root: Path) -> list[Path]:
    dirs: list[Path] = []
    for current, dirnames, _ in os.walk(root):
        dirnames[:] = [name for name in dirnames if name not in SKIP_DIRS]
        dirs.append(Path(current))
    return dirs


def rel(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def node_manifest_has_workspaces(project_dir: Path) -> bool:
    data = read_package_json(project_dir)
    return isinstance(data, dict) and bool(data.get("workspaces"))


def read_package_json(project_dir: Path) -> dict[str, Any]:
    package_json = project_dir / "package.json"
    if not package_json.exists():
        return {}
    try:
        data = json.loads(package_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def read_node_package_manager(project_dir: Path) -> str | None:
    package_manager = read_package_json(project_dir).get("packageManager")
    if not isinstance(package_manager, str):
        return None
    manager = package_manager.split("@", 1)[0].lower()
    return manager if manager in NODE_LOCKFILES_BY_MANAGER else None


def requirements_file_has_unpinned_entries(path: Path) -> bool:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return False

    for raw_line in lines:
        line = raw_line.split("#", 1)[0].strip()
        if not line or line.startswith(("-", "--")):
            continue
        if "==" not in line and "===" not in line:
            return True
    return False


def is_python_project_lockfile(name: str) -> bool:
    # pip-audit documents --locked project scans for pyproject.toml and pylock.*.toml;
    # pylock support still depends on the installed pip-audit version.
    # Ref: https://github.com/pypa/pip-audit#usage
    return name in {"poetry.lock", "uv.lock", "Pipfile.lock"} or name == "pylock.toml" or name.startswith("pylock.") and name.endswith(".toml")


def discover_projects(root: Path) -> list[ProjectSignal]:
    projects: dict[tuple[str, Path], ProjectSignal] = {}

    for directory in walk_dirs(root):
        files = {child.name for child in directory.iterdir() if child.is_file()}

        node_locks = sorted(files & {"package-lock.json", "pnpm-lock.yaml", "yarn.lock", "bun.lock"})
        if "package.json" in files or node_locks:
            signal = ProjectSignal("node", rel(directory, root), manifests=[], lockfiles=node_locks)
            if "package.json" in files:
                signal.manifests.append("package.json")
                signal.package_manager = read_node_package_manager(directory)
            if not node_locks:
                signal.notes.append("No Node lockfile found; CVE evidence may be incomplete.")
            if signal.package_manager:
                expected_lockfile = NODE_LOCKFILES_BY_MANAGER[signal.package_manager]
                conflicting_lockfiles = [lockfile for lockfile in node_locks if lockfile != expected_lockfile]
                if expected_lockfile not in node_locks:
                    signal.notes.append(
                        f"packageManager declares {signal.package_manager}, but {expected_lockfile} was not found; native Node audit is incomplete."
                    )
                if conflicting_lockfiles:
                    signal.notes.append(
                        "Conflicting package manager evidence: "
                        f"packageManager declares {signal.package_manager}, but found {', '.join(conflicting_lockfiles)}."
                    )
            elif len(node_locks) > 1:
                signal.notes.append(f"Multiple Node lockfiles found ({', '.join(node_locks)}); native audit uses package-lock, pnpm, then yarn priority.")
            if "bun.lock" in node_locks:
                signal.notes.append("Bun lockfile detected; native Bun audit is not run. Confirm OSV-Scanner support or generate a package-lock.json for broader coverage.")
            projects[(signal.ecosystem, directory)] = signal

        python_locks = sorted(name for name in files if is_python_project_lockfile(name))
        requirements = sorted(name for name in files if name == "requirements.txt" or "requirements" in name and name.endswith(".txt"))
        if "pyproject.toml" in files or python_locks or requirements:
            signal = ProjectSignal("python", rel(directory, root), manifests=[], lockfiles=python_locks + requirements)
            if "pyproject.toml" in files:
                signal.manifests.append("pyproject.toml")
            if not signal.lockfiles:
                signal.notes.append("No Python lockfile or requirements file found; CVE evidence may be incomplete.")
            for requirement_file in requirements:
                if requirements_file_has_unpinned_entries(directory / requirement_file):
                    signal.notes.append(f"{requirement_file} is not a lockfile unless versions are pinned; exact Python CVE evidence is weak.")
            projects[(signal.ecosystem, directory)] = signal

        if "pubspec.yaml" in files or "pubspec.lock" in files:
            signal = ProjectSignal("dart", rel(directory, root), manifests=[], lockfiles=[])
            if "pubspec.yaml" in files:
                signal.manifests.append("pubspec.yaml")
            if "pubspec.lock" in files:
                signal.lockfiles.append("pubspec.lock")
            else:
                signal.notes.append("No pubspec.lock found; Flutter/Dart CVE scan needs exact resolved versions.")
            projects[(signal.ecosystem, directory)] = signal

    return sorted(projects.values(), key=lambda item: (item.path, item.ecosystem))


def detect_runtime(root: Path, projects: list[ProjectSignal]) -> tuple[list[RuntimeInfo], list[ToolRun]]:
    runtime: list[RuntimeInfo] = []
    runs: list[ToolRun] = []
    ecosystems = {project.ecosystem for project in projects}

    def add(ecosystem: str, command: list[str]) -> None:
        run, output = run_text(command, root)
        runs.append(run)
        runtime.append(
            RuntimeInfo(
                ecosystem=ecosystem,
                tool=command[0],
                version=first_semver(output),
                available=run.available,
                path=shutil.which(command[0]) if run.available else None,
            )
        )

    if "node" in ecosystems:
        add("node", ["node", "--version"])
        for tool in ("npm", "pnpm", "yarn", "bun"):
            if shutil.which(tool):
                add("node", [tool, "--version"])

    if "python" in ecosystems:
        add("python", ["python3", "--version"])
        if shutil.which("python"):
            add("python", ["python", "--version"])
        if shutil.which("pip-audit"):
            add("python", ["pip-audit", "--version"])

    if "dart" in ecosystems:
        if shutil.which("flutter"):
            add("dart", ["flutter", "--version"])
        add("dart", ["dart", "--version"])

    return runtime, runs


def parse_npm_audit(data: Any, project_path: str) -> list[Finding]:
    findings: list[Finding] = []
    vulnerabilities = (data or {}).get("vulnerabilities", {})
    for package, item in vulnerabilities.items():
        via_items = item.get("via", [])
        advisory_items = [entry for entry in via_items if isinstance(entry, dict)]
        if not advisory_items and isinstance(via_items, list) and via_items:
            advisory_items = [{"title": ", ".join(str(entry) for entry in via_items)}]
        for advisory in advisory_items:
            fixed = item.get("fixAvailable")
            fixed_versions: list[str] = []
            if isinstance(fixed, dict) and fixed.get("version"):
                fixed_versions.append(str(fixed["version"]))
            findings.append(
                Finding(
                    source="npm audit",
                    ecosystem="npm",
                    package=package,
                    version=None,
                    severity=str(advisory.get("severity") or item.get("severity") or "unknown"),
                    advisory=str(advisory.get("url") or advisory.get("source") or package),
                    title=str(advisory.get("title") or package),
                    fixed_versions=fixed_versions,
                    path=project_path,
                )
            )
    return findings


def parse_yarn_audit(data: Any, project_path: str) -> list[Finding]:
    findings = parse_npm_audit(data, project_path)
    for finding in findings:
        finding.source = "yarn npm audit"
    return findings


def parse_pnpm_audit(data: Any, project_path: str) -> list[Finding]:
    if isinstance(data, dict) and isinstance(data.get("vulnerabilities"), dict):
        findings = parse_npm_audit(data, project_path)
        for finding in findings:
            finding.source = "pnpm audit"
        return findings

    findings: list[Finding] = []
    advisories = (data or {}).get("advisories", {}) if isinstance(data, dict) else {}
    for advisory_id, advisory in advisories.items():
        if not isinstance(advisory, dict):
            continue
        patched_versions = advisory.get("patched_versions", [])
        if isinstance(patched_versions, str):
            fixed_versions = [patched_versions] if patched_versions else []
        elif isinstance(patched_versions, list):
            fixed_versions = [str(item) for item in patched_versions]
        else:
            fixed_versions = []
        findings.append(
            Finding(
                source="pnpm audit",
                ecosystem="npm",
                package=str(advisory.get("module_name") or advisory.get("name") or ""),
                version=str(advisory.get("findings", [{}])[0].get("version")) if advisory.get("findings") else None,
                severity=str(advisory.get("severity") or "unknown"),
                advisory=str(advisory.get("url") or advisory_id),
                title=str(advisory.get("title") or advisory_id),
                fixed_versions=fixed_versions,
                path=project_path,
            )
        )
    return findings


def parse_pip_audit(data: Any, project_path: str) -> list[Finding]:
    findings: list[Finding] = []
    if isinstance(data, list):
        dependencies = data
    elif isinstance(data, dict):
        dependencies = data.get("dependencies", [])
    else:
        dependencies = []
    for dependency in dependencies:
        if not isinstance(dependency, dict):
            continue
        package = dependency.get("name", "")
        version = dependency.get("version")
        for vuln in dependency.get("vulns", []):
            aliases = vuln.get("aliases") or []
            advisory = vuln.get("id") or (aliases[0] if aliases else package)
            findings.append(
                Finding(
                    source="pip-audit",
                    ecosystem="pypi",
                    package=package,
                    version=version,
                    severity=str(vuln.get("severity") or "unknown"),
                    advisory=str(advisory),
                    title=str(vuln.get("description") or vuln.get("summary") or advisory),
                    fixed_versions=[str(item) for item in vuln.get("fix_versions", [])],
                    path=project_path,
                )
            )
    return findings


def parse_osv(data: Any) -> list[Finding]:
    findings: list[Finding] = []

    def handle_vuln(vuln: dict[str, Any], package: str = "", version: str | None = None, path: str | None = None) -> None:
        advisory = str(vuln.get("id") or (vuln.get("aliases") or ["unknown"])[0])
        severity = normalize_osv_severity(vuln)
        fixed_versions: list[str] = []
        for affected in vuln.get("affected", []):
            for range_item in affected.get("ranges", []):
                for event in range_item.get("events", []):
                    if "fixed" in event:
                        fixed_versions.append(str(event["fixed"]))
        findings.append(
            Finding(
                source="osv-scanner",
                ecosystem="osv",
                package=package,
                version=version,
                severity=severity,
                advisory=advisory,
                title=str(vuln.get("summary") or advisory),
                fixed_versions=sorted(set(fixed_versions)),
                path=path,
            )
        )

    if isinstance(data, dict):
        for result in data.get("results", []):
            packages = result.get("packages") or []
            for package_entry in packages:
                package_info = package_entry.get("package", {})
                name = package_info.get("name", "")
                version = package_info.get("version")
                path = package_entry.get("locations", [{}])[0].get("path") if package_entry.get("locations") else None
                for vuln in package_entry.get("vulnerabilities", []):
                    handle_vuln(vuln, name, version, path)
            for vuln in result.get("vulns", []):
                handle_vuln(vuln)
    return findings


def parse_npm_outdated(data: Any, project_path: str) -> list[FreshnessFinding]:
    freshness: list[FreshnessFinding] = []
    if not isinstance(data, dict):
        return freshness
    for package, item in data.items():
        if not isinstance(item, dict):
            continue
        current = str(item.get("current")) if item.get("current") is not None else None
        latest = str(item.get("latest")) if item.get("latest") is not None else None
        freshness.append(
            FreshnessFinding(
                ecosystem="npm",
                package=package,
                current=current,
                wanted=str(item.get("wanted")) if item.get("wanted") is not None else None,
                latest=latest,
                update_type=classify_update(current, latest),
                source="npm outdated",
                path=project_path,
            )
        )
    return freshness


def parse_pnpm_outdated(data: Any, project_path: str) -> list[FreshnessFinding]:
    freshness: list[FreshnessFinding] = []
    if isinstance(data, list):
        iterable = {
            str(item.get("packageName") or item.get("name") or ""): item
            for item in data
            if isinstance(item, dict)
        }
    elif isinstance(data, dict):
        iterable = data
    else:
        return freshness
    for package, item in iterable.items():
        if not isinstance(item, dict):
            continue
        current = str(item.get("current") or item.get("currentVersion")) if (item.get("current") or item.get("currentVersion")) is not None else None
        latest = str(item.get("latest") or item.get("latestVersion")) if (item.get("latest") or item.get("latestVersion")) is not None else None
        freshness.append(
            FreshnessFinding(
                ecosystem="npm",
                package=package,
                current=current,
                wanted=str(item.get("wanted") or item.get("wantedVersion")) if (item.get("wanted") or item.get("wantedVersion")) is not None else None,
                latest=latest,
                update_type=classify_update(current, latest),
                source="pnpm outdated",
                path=project_path,
            )
        )
    return freshness


def mark_vulnerability_run(run: ToolRun, findings: list[Finding]) -> None:
    if findings and run.tool_status in {"ok", "scanner_error"}:
        run.tool_status = "vulnerability_found"


def select_node_package_manager(project: ProjectSignal) -> str | None:
    if project.package_manager:
        expected_lockfile = NODE_LOCKFILES_BY_MANAGER[project.package_manager]
        return project.package_manager if expected_lockfile in project.lockfiles else None
    if "package-lock.json" in project.lockfiles:
        return "npm"
    if "pnpm-lock.yaml" in project.lockfiles:
        return "pnpm"
    if "yarn.lock" in project.lockfiles:
        return "yarn"
    return None


def parse_pub_outdated(data: Any, project_path: str) -> list[FreshnessFinding]:
    freshness: list[FreshnessFinding] = []
    if not isinstance(data, dict):
        return freshness
    packages = data.get("packages")
    if not isinstance(packages, list):
        return freshness
    for item in packages:
        package = str(item.get("package") or item.get("name") or "")
        current = item.get("current") if isinstance(item.get("current"), dict) else {}
        latest = item.get("latest") if isinstance(item.get("latest"), dict) else {}
        current_version = current.get("version") if isinstance(current, dict) else None
        latest_version = latest.get("version") if isinstance(latest, dict) else None
        if not package:
            continue
        freshness.append(
            FreshnessFinding(
                ecosystem="pub",
                package=package,
                current=str(current_version) if current_version is not None else None,
                wanted=None,
                latest=str(latest_version) if latest_version is not None else None,
                update_type=classify_update(str(current_version) if current_version is not None else None, str(latest_version) if latest_version is not None else None),
                source="pub outdated",
                path=project_path,
            )
        )
    return freshness


def parse_pip_outdated(data: Any, project_path: str) -> list[FreshnessFinding]:
    freshness: list[FreshnessFinding] = []
    if not isinstance(data, list):
        return freshness
    for item in data:
        if not isinstance(item, dict):
            continue
        package = str(item.get("name") or "")
        current = str(item.get("version")) if item.get("version") is not None else None
        latest = str(item.get("latest_version") or item.get("latest")) if (item.get("latest_version") or item.get("latest")) is not None else None
        if not package:
            continue
        freshness.append(
            FreshnessFinding(
                ecosystem="pypi",
                package=package,
                current=current,
                wanted=None,
                latest=latest,
                update_type=classify_update(current, latest),
                source="pip list --outdated",
                path=project_path,
            )
        )
    return freshness


def run_native_audits(root: Path, projects: list[ProjectSignal]) -> tuple[list[Finding], list[ToolRun]]:
    findings: list[Finding] = []
    runs: list[ToolRun] = []

    for project in projects:
        project_dir = root / project.path if project.path != "." else root
        if project.ecosystem == "node":
            node_manager = select_node_package_manager(project)
            if node_manager == "npm":
                run, data = run_json(["npm", "audit", "--package-lock-only", "--json"], project_dir)
                runs.append(run)
                if data:
                    parsed_findings = parse_npm_audit(data, project.path)
                    findings.extend(parsed_findings)
                    mark_vulnerability_run(run, parsed_findings)
            elif node_manager == "pnpm":
                run, data = run_json(["pnpm", "audit", "--json"], project_dir)
                runs.append(run)
                if data:
                    parsed_findings = parse_pnpm_audit(data, project.path)
                    findings.extend(parsed_findings)
                    mark_vulnerability_run(run, parsed_findings)
            elif node_manager == "yarn":
                command = ["yarn", "npm", "audit"]
                if node_manifest_has_workspaces(project_dir):
                    command.append("--all")
                command.extend(["--recursive", "--json"])
                run, data = run_json(command, project_dir, ndjson=True)
                runs.append(run)
                if data:
                    parsed_findings = parse_yarn_audit(data, project.path)
                    findings.extend(parsed_findings)
                    mark_vulnerability_run(run, parsed_findings)

        if project.ecosystem == "python":
            ran_locked_audit = False
            for lockfile in project.lockfiles:
                if lockfile.endswith(".txt"):
                    run, data = run_json(["pip-audit", "-r", lockfile, "--format", "json"], project_dir)
                    runs.append(run)
                    if data:
                        parsed_findings = parse_pip_audit(data, project.path)
                        findings.extend(parsed_findings)
                        mark_vulnerability_run(run, parsed_findings)
                elif is_python_project_lockfile(lockfile) and not ran_locked_audit:
                    run, data = run_json(["pip-audit", "--locked", "--format", "json", "."], project_dir)
                    runs.append(run)
                    ran_locked_audit = True
                    if data:
                        parsed_findings = parse_pip_audit(data, project.path)
                        findings.extend(parsed_findings)
                        mark_vulnerability_run(run, parsed_findings)

    return findings, runs


def run_freshness_checks(root: Path, projects: list[ProjectSignal]) -> tuple[list[FreshnessFinding], list[ToolRun]]:
    freshness: list[FreshnessFinding] = []
    runs: list[ToolRun] = []

    for project in projects:
        project_dir = root / project.path if project.path != "." else root
        if project.ecosystem == "node":
            node_manager = select_node_package_manager(project)
            if node_manager == "npm" and (project_dir / "node_modules").exists():
                run, data = run_json(["npm", "outdated", "--json"], project_dir)
                runs.append(run)
                if data:
                    freshness.extend(parse_npm_outdated(data, project.path))
            elif node_manager == "pnpm" and shutil.which("pnpm"):
                run, data = run_json(["pnpm", "outdated", "--format", "json"], project_dir)
                runs.append(run)
                if data:
                    freshness.extend(parse_pnpm_outdated(data, project.path))
            elif node_manager == "yarn" and shutil.which("yarn"):
                continue

        if project.ecosystem == "python":
            for lockfile in project.lockfiles:
                if lockfile.endswith(".txt") and shutil.which("python3"):
                    run, data = run_json(["python3", "-m", "pip", "list", "--outdated", "--format", "json"], project_dir)
                    runs.append(run)
                    if data:
                        freshness.extend(parse_pip_outdated(data, project.path))
                    break

        if project.ecosystem == "dart" and project.manifests:
            tool = "flutter" if shutil.which("flutter") else "dart"
            command = [tool, "pub", "outdated", "--json"]
            run, data = run_json(command, project_dir)
            runs.append(run)
            if data:
                freshness.extend(parse_pub_outdated(data, project.path))

    freshness.sort(key=lambda item: ({"major": 0, "minor": 1, "patch": 2, "unknown": 3, "current": 4}.get(item.update_type, 3), item.package))
    return freshness, runs


def run_osv(root: Path) -> tuple[list[Finding], ToolRun]:
    command = ["osv-scanner", "scan", "source", "--recursive", str(root), "--format", "json"]
    run, data = run_json(command, root)
    if not run.available:
        return [], run
    findings = parse_osv(data)
    mark_vulnerability_run(run, findings)
    return findings, run


def report_status(findings: list[Finding], runs: list[ToolRun], projects: list[ProjectSignal], dry_run: bool = False) -> str:
    if dry_run:
        return "dry_run"
    cve_runs = [run for run in runs if run.tool in CVE_TOOLS]
    missing_cve_tools = [run for run in cve_runs if run.tool_status in INCOMPLETE_TOOL_STATUSES]
    unusable_cve_tools = [run for run in cve_runs if run.tool_status in UNUSABLE_TOOL_STATUSES]
    usable_cve_tools = [run for run in cve_runs if run.available and run.tool_status not in INCOMPLETE_TOOL_STATUSES]
    has_weak_lockfile_evidence = any(project.notes for project in projects)

    if findings and (has_failed_tools(runs) or missing_cve_tools or has_weak_lockfile_evidence):
        return "partial_vulnerable"
    if findings:
        return "vulnerable"
    if has_failed_tools(runs):
        if any(run.tool in CVE_TOOLS and run.tool_status == "ok" for run in runs):
            return "partial_clean"
        return "scanner_error"

    if cve_runs and unusable_cve_tools and not usable_cve_tools:
        return "scanner_unavailable"
    if missing_cve_tools or has_weak_lockfile_evidence:
        return "weak_evidence"
    if not projects:
        return "clean"
    return "clean"


def has_failed_tools(runs: list[ToolRun]) -> bool:
    return any(run.tool_status == "scanner_error" for run in runs)


def setup_hints(runs: list[ToolRun], projects: list[ProjectSignal]) -> list[str]:
    hints: list[str] = []
    seen_tools = {run.tool for run in runs if run.tool_status in INCOMPLETE_TOOL_STATUSES or run.tool_status == "scanner_error"}
    if "osv-scanner" in seen_tools:
        hints.append("Install OSV-Scanner or rerun with --skip-osv when native scanners are enough.")
    if "pip-audit" in seen_tools:
        hints.append("python3 -m pip install pip-audit")
        if shutil.which("uv"):
            hints.append("uv tool install pip-audit")
    if any(run.tool == "yarn" and run.tool_status == "unsupported" for run in runs):
        hints.append("Yarn audit requires Yarn Berry with `yarn npm audit`; Yarn Classic cannot run this native audit.")
    if any(run.tool_status == "scanner_error" and re.search(r"(fetch|network|registry|dns).*(failed|error)|failed.*(fetch|network|registry|dns)", run.stderr or "", re.IGNORECASE) for run in runs):
        hints.append("A scanner could not reach its registry/API. Rerun with network access or check registry/DNS configuration.")
    if any("not a lockfile unless versions are pinned" in note for project in projects for note in project.notes):
        hints.append("Pin Python requirements with exact == versions or use a supported lockfile for stronger CVE evidence.")
    return sorted(set(hints))


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit dependency vulnerabilities across common app stacks.")
    parser.add_argument("--root", default=".", help="Repository or app root to scan.")
    parser.add_argument("--report", default="-", help="JSON report path, or '-' for stdout.")
    parser.add_argument("--fail-on", default="high", choices=sorted(SEVERITY_ORDER), help="Minimum severity that fails.")
    parser.add_argument("--mode", default="ci", choices=("pre-commit", "pre-push", "ci", "scheduled"), help="Preset for hook/CI behavior.")
    parser.add_argument("--skip-native", action="store_true", help="Only run OSV-Scanner discovery/audit.")
    parser.add_argument("--skip-osv", action="store_true", help="Only run native ecosystem audits.")
    parser.add_argument("--skip-freshness", action="store_true", help="Skip runtime and latest-version freshness checks.")
    parser.add_argument("--freshness", action="store_true", help="Force runtime and latest-version freshness checks.")
    parser.add_argument("--fail-on-major-outdated", action="store_true", help="Fail when freshness checks find major-version lag.")
    parser.add_argument("--dry-run", action="store_true", help="Discover projects and show what would be scanned without running scanners.")
    parser.add_argument("--verbose", action="store_true", help="Print scanner discovery and command diagnostics to stderr.")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    projects = discover_projects(root)
    for project in projects:
        debug(f"detected {project.ecosystem} project at {project.path} locks={project.lockfiles or '-'} manifests={project.manifests or '-'}", args.verbose)
        for note in project.notes:
            debug(f"note for {project.path}: {note}", args.verbose)
    findings: list[Finding] = []
    freshness: list[FreshnessFinding] = []
    runtime: list[RuntimeInfo] = []
    runs: list[ToolRun] = []
    run_freshness = args.freshness or (args.mode in {"ci", "scheduled"} and not args.skip_freshness)

    if args.dry_run:
        run_freshness = False

    if not args.dry_run and not args.skip_osv:
        osv_findings, osv_run = run_osv(root)
        findings.extend(osv_findings)
        runs.append(osv_run)
        debug(f"ran {' '.join(osv_run.command)} available={osv_run.available} exit={osv_run.exit_code}", args.verbose)

    if not args.dry_run and not args.skip_native:
        native_findings, native_runs = run_native_audits(root, projects)
        findings.extend(native_findings)
        runs.extend(native_runs)
        for run in native_runs:
            debug(f"ran {' '.join(run.command)} available={run.available} exit={run.exit_code}", args.verbose)

    if not args.dry_run and run_freshness:
        runtime, runtime_runs = detect_runtime(root, projects)
        freshness, freshness_runs = run_freshness_checks(root, projects)
        runs.extend(runtime_runs)
        runs.extend(freshness_runs)
        for run in runtime_runs + freshness_runs:
            debug(f"ran {' '.join(run.command)} available={run.available} exit={run.exit_code}", args.verbose)

    for run in runs:
        if not run.available:
            debug(f"missing tool: {run.tool}", args.verbose)
        elif run.exit_code not in (0, None):
            debug(f"failed tool: {run.tool} exit={run.exit_code} stderr={run.stderr or '-'}", args.verbose)

    findings.sort(key=lambda item: (-severity_value(item.severity), item.package, item.advisory))
    threshold = severity_value(args.fail_on)
    failing = [item for item in findings if severity_value(item.severity) >= threshold]
    failing_freshness = [item for item in freshness if args.fail_on_major_outdated and item.update_type == "major"]

    status = report_status(findings, runs, projects, dry_run=args.dry_run)
    report = {
        "root": str(root),
        "status": status,
        "mode": args.mode,
        "fail_on": args.fail_on,
        "detected_projects": [asdict(item) for item in projects],
        "runtime": [asdict(item) for item in runtime],
        "findings": [asdict(item) for item in findings],
        "freshness": [asdict(item) for item in freshness],
        "tool_runs": [asdict(item) for item in runs],
        "summary": {
            "project_count": len(projects),
            "finding_count": len(findings),
            "failing_count": len(failing),
            "freshness_count": len(freshness),
            "major_outdated_count": len([item for item in freshness if item.update_type == "major"]),
            "missing_tools": sorted({run.tool for run in runs if not run.available}),
            "unsupported_tools": sorted({run.tool for run in runs if run.tool_status == "unsupported"}),
            "vulnerability_tools": sorted({run.tool for run in runs if run.tool_status == "vulnerability_found"}),
            "failed_tools": sorted({run.tool for run in runs if run.tool_status == "scanner_error"}),
            "setup_hints": setup_hints(runs, projects),
        },
    }

    output = json.dumps(report, indent=2)
    if args.report == "-":
        print(output)
    else:
        Path(args.report).write_text(output + "\n", encoding="utf-8")
        print(f"wrote {args.report}")

    return 1 if failing or failing_freshness or has_failed_tools(runs) else 0


if __name__ == "__main__":
    sys.exit(main())
