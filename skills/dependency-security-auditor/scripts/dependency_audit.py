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
    return "unknown"


def run_json(command: list[str], cwd: Path) -> tuple[ToolRun, Any | None]:
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
        return tool_run, json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        return tool_run, None


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


def discover_projects(root: Path) -> list[ProjectSignal]:
    projects: dict[tuple[str, Path], ProjectSignal] = {}

    for directory in walk_dirs(root):
        files = {child.name for child in directory.iterdir() if child.is_file()}

        node_locks = sorted(files & {"package-lock.json", "pnpm-lock.yaml", "yarn.lock", "bun.lock"})
        if "package.json" in files or node_locks:
            signal = ProjectSignal("node", rel(directory, root), manifests=[], lockfiles=node_locks)
            if "package.json" in files:
                signal.manifests.append("package.json")
            if not node_locks:
                signal.notes.append("No Node lockfile found; CVE evidence may be incomplete.")
            projects[(signal.ecosystem, directory)] = signal

        python_locks = sorted(files & {"poetry.lock", "uv.lock", "Pipfile.lock"})
        requirements = sorted(name for name in files if name == "requirements.txt" or "requirements" in name and name.endswith(".txt"))
        if "pyproject.toml" in files or python_locks or requirements:
            signal = ProjectSignal("python", rel(directory, root), manifests=[], lockfiles=python_locks + requirements)
            if "pyproject.toml" in files:
                signal.manifests.append("pyproject.toml")
            if not signal.lockfiles:
                signal.notes.append("No Python lockfile or requirements file found; CVE evidence may be incomplete.")
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


def parse_pnpm_audit(data: Any, project_path: str) -> list[Finding]:
    if isinstance(data, dict) and isinstance(data.get("vulnerabilities"), dict):
        return parse_npm_audit(data, project_path)

    findings: list[Finding] = []
    advisories = (data or {}).get("advisories", {}) if isinstance(data, dict) else {}
    for advisory_id, advisory in advisories.items():
        if not isinstance(advisory, dict):
            continue
        findings.append(
            Finding(
                source="pnpm audit",
                ecosystem="npm",
                package=str(advisory.get("module_name") or advisory.get("name") or ""),
                version=str(advisory.get("findings", [{}])[0].get("version")) if advisory.get("findings") else None,
                severity=str(advisory.get("severity") or "unknown"),
                advisory=str(advisory.get("url") or advisory_id),
                title=str(advisory.get("title") or advisory_id),
                fixed_versions=[str(item) for item in advisory.get("patched_versions", [])] if isinstance(advisory.get("patched_versions"), list) else [],
                path=project_path,
            )
        )
    return findings


def parse_pip_audit(data: Any, project_path: str) -> list[Finding]:
    findings: list[Finding] = []
    for dependency in (data or {}).get("dependencies", []):
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
            if "package-lock.json" in project.lockfiles:
                run, data = run_json(["npm", "audit", "--package-lock-only", "--json"], project_dir)
                runs.append(run)
                if data:
                    findings.extend(parse_npm_audit(data, project.path))
            elif "pnpm-lock.yaml" in project.lockfiles:
                run, data = run_json(["pnpm", "audit", "--json"], project_dir)
                runs.append(run)
                if data:
                    findings.extend(parse_pnpm_audit(data, project.path))
            elif "yarn.lock" in project.lockfiles:
                run, _ = run_json(["yarn", "npm", "audit", "--json"], project_dir)
                runs.append(run)

        if project.ecosystem == "python":
            for lockfile in project.lockfiles:
                if lockfile.endswith(".txt"):
                    run, data = run_json(["pip-audit", "-r", lockfile, "--format", "json"], project_dir)
                    runs.append(run)
                    if data:
                        findings.extend(parse_pip_audit(data, project.path))

    return findings, runs


def run_freshness_checks(root: Path, projects: list[ProjectSignal]) -> tuple[list[FreshnessFinding], list[ToolRun]]:
    freshness: list[FreshnessFinding] = []
    runs: list[ToolRun] = []

    for project in projects:
        project_dir = root / project.path if project.path != "." else root
        if project.ecosystem == "node":
            if "package-lock.json" in project.lockfiles and (project_dir / "node_modules").exists():
                run, data = run_json(["npm", "outdated", "--json"], project_dir)
                runs.append(run)
                if data:
                    freshness.extend(parse_npm_outdated(data, project.path))
            elif "pnpm-lock.yaml" in project.lockfiles and shutil.which("pnpm"):
                run, data = run_json(["pnpm", "outdated", "--format", "json"], project_dir)
                runs.append(run)
                if data:
                    freshness.extend(parse_pnpm_outdated(data, project.path))
            elif "yarn.lock" in project.lockfiles and shutil.which("yarn"):
                run, _ = run_json(["yarn", "npm", "outdated", "--json"], project_dir)
                runs.append(run)

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
    command = ["osv-scanner", "scan", "source", str(root), "--format", "json"]
    run, data = run_json(command, root)
    if not run.available:
        return [], run
    return parse_osv(data), run


def report_status(findings: list[Finding], runs: list[ToolRun], projects: list[ProjectSignal], dry_run: bool = False) -> str:
    if dry_run:
        return "dry_run"
    if findings:
        return "vulnerable"
    if any(run.available and run.exit_code not in (0, None) for run in runs):
        return "scanner_error"
    if not projects:
        return "clean"

    cve_runs = [run for run in runs if run.tool in CVE_TOOLS]
    missing_cve_tools = [run for run in cve_runs if not run.available]
    available_cve_tools = [run for run in cve_runs if run.available]
    has_weak_lockfile_evidence = any(project.notes for project in projects)

    if cve_runs and missing_cve_tools and not available_cve_tools:
        return "scanner_unavailable"
    if missing_cve_tools or has_weak_lockfile_evidence:
        return "weak_evidence"
    return "clean"


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
    args = parser.parse_args()

    root = Path(args.root).resolve()
    projects = discover_projects(root)
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

    if not args.dry_run and not args.skip_native:
        native_findings, native_runs = run_native_audits(root, projects)
        findings.extend(native_findings)
        runs.extend(native_runs)

    if not args.dry_run and run_freshness:
        runtime, runtime_runs = detect_runtime(root, projects)
        freshness, freshness_runs = run_freshness_checks(root, projects)
        runs.extend(runtime_runs)
        runs.extend(freshness_runs)

    findings.sort(key=lambda item: (-severity_value(item.severity), item.package, item.advisory))
    threshold = severity_value(args.fail_on)
    failing = [item for item in findings if severity_value(item.severity) >= threshold]
    failing_freshness = [item for item in freshness if args.fail_on_major_outdated and item.update_type == "major"]

    report = {
        "root": str(root),
        "status": report_status(findings, runs, projects, dry_run=args.dry_run),
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
            "failed_tools": sorted({run.tool for run in runs if run.available and run.exit_code not in (0, None)}),
        },
    }

    output = json.dumps(report, indent=2)
    if args.report == "-":
        print(output)
    else:
        Path(args.report).write_text(output + "\n", encoding="utf-8")
        print(f"wrote {args.report}")

    return 1 if failing or failing_freshness else 0


if __name__ == "__main__":
    sys.exit(main())
