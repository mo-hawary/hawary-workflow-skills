#!/usr/bin/env python3
"""Coordinate bounded parallel Codex agents with private, inspectable task state."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from fast_agents_common import (
    FastAgentsError,
    default_config_path,
    load_defaults,
    load_manifest,
)
from fast_agents_platform import codex_argv, platform_coverage_label, protect_private_path
from fast_agents_protocol import (
    APP_SERVER_VERSION,
    catalog_efforts,
    catalog_tiers,
    generate_and_verify_schema,
    probe_model_catalog,
)
from fast_agents_runtime import (
    capture_process_identity,
    default_runtime_root,
    launch_from_path,
    read_task_status,
    render_status,
    supervisor_main,
    watch_task,
    cleanup_monitors,
    task_path,
    start_recovery_supervisor,
)
from fast_agents_ipc import send_control_request
from fast_agents_setup import (
    apply_instruction_preview,
    install_skill,
    rollback_defaults,
    rollback_instruction,
    rollback_skill,
    save_defaults,
    show_instruction_preview,
)


VERSION_PATTERN = re.compile(r"codex-cli\s+(\S+)", re.IGNORECASE)


def _bool_arg(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"true", "yes", "on", "1"}:
        return True
    if normalized in {"false", "no", "off", "0"}:
        return False
    raise argparse.ArgumentTypeError("expected true or false")


def _fast_workers_arg(value: str) -> dict[str, Any]:
    normalized = value.strip()
    if normalized in {"all", "none"}:
        return {"mode": normalized, "workers": []}
    workers = [worker.strip() for worker in normalized.split(",") if worker.strip()]
    if not workers:
        raise argparse.ArgumentTypeError("use all, none, or a comma-separated worker list")
    return {"mode": "selected", "workers": workers}


def _run_capture(argv: list[str], timeout: float = 10.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout, shell=False, check=False)


def _exec_capabilities(executable: str | None = None) -> dict[str, Any]:
    resolved = executable or shutil.which("codex")
    if not resolved:
        return {"available": False, "error": "Codex CLI was not found on PATH"}
    version_result = _run_capture(codex_argv(resolved, "--version"))
    version_text = (version_result.stdout or version_result.stderr).strip()
    match = VERSION_PATTERN.search(version_text)
    version = match.group(1) if match else version_text or "unknown"
    help_result = _run_capture(codex_argv(resolved, "exec", "--help"))
    help_text = help_result.stdout + help_result.stderr
    feature_result = _run_capture(codex_argv(resolved, "features", "list"))
    feature_text = feature_result.stdout + feature_result.stderr
    feature_lines = [line.strip() for line in feature_text.splitlines() if re.search(r"\bfast_mode\b", line)]
    feature_line = feature_lines[0] if feature_lines else None
    try:
        process_identity = capture_process_identity(os.getpid())
        identity_report = {
            "available": process_identity is not None,
            "platform": process_identity.get("platform") if process_identity else None,
            "limitation": None if process_identity else "process identity API returned no current-process identity",
        }
    except (OSError, PermissionError) as error:
        identity_report = {"available": False, "platform": None, "limitation": str(error)}
    return {
        "available": version_result.returncode == 0 and help_result.returncode == 0,
        "executable": resolved,
        "version": version,
        "version_exit_code": version_result.returncode,
        "exec_help_exit_code": help_result.returncode,
        "feature_list_exit_code": feature_result.returncode,
        "supported_flags": {
            "-C": "-C" in help_text or "--cd" in help_text,
            "-m": "-m, --model" in help_text,
            "-c": "-c, --config" in help_text,
            "--enable": "--enable" in help_text,
            "--approve-for-me": "--approve-for-me" in help_text,
            "-s read-only": "--sandbox" in help_text,
            "--json": "--json" in help_text,
            "--color": "--color" in help_text,
            "-o": "--output-last-message" in help_text,
        },
        "fast_mode_feature_line": feature_line,
        "fast_mode_feature_listed": feature_line is not None,
        "process_identity": identity_report,
        "fast_mode_capability_note": (
            "Feature listing describes client capability/configuration only; it does not confirm an individual session used fast mode."
        ),
        "fast_config": {
            "service_tier_override": ["-c", 'service_tier="fast"'],
            "feature_enable": ["--enable", "fast_mode"],
            "writer_effort_values": ["low", "max"],
            "reviewer_effort_values": ["xhigh"],
            "runtime_confirmation": "unverified until correlated runtime event evidence is present",
            "config_key_schema": "not enumerated by codex exec --help",
        },
        "backend": "exec",
        "platform_coverage": platform_coverage_label(),
    }


def _app_server_capabilities(executable: str | None = None) -> dict[str, Any]:
    resolved = executable or shutil.which("codex")
    if not resolved:
        return {"available": False, "backend": "app-server", "error": "Codex CLI was not found on PATH"}
    version_result = _run_capture(codex_argv(resolved, "--version"))
    version_text = (version_result.stdout or version_result.stderr).strip()
    match = VERSION_PATTERN.search(version_text)
    version = match.group(1) if match else version_text or "unknown"
    help_result = _run_capture(codex_argv(resolved, "app-server", "--help"))
    help_text = help_result.stdout + help_result.stderr
    schema = generate_and_verify_schema(resolved, version)
    feature_result = _run_capture(codex_argv(resolved, "features", "list"))
    feature_text = feature_result.stdout + feature_result.stderr
    feature_line = next((line.strip() for line in feature_text.splitlines() if re.search(r"\bfast_mode\b", line)), None)
    feature_available = bool(feature_line and re.search(r"\bstable\s+true\b", feature_line))
    try:
        process_identity = capture_process_identity(os.getpid())
        identity = {
            "available": process_identity is not None,
            "platform": process_identity.get("platform") if process_identity else None,
            "limitation": None if process_identity else "process identity API returned no current-process identity",
        }
    except (OSError, PermissionError) as error:
        identity = {"available": False, "platform": None, "limitation": str(error)}
    catalog: list[dict[str, Any]] = []
    catalog_error: str | None = None
    if version == APP_SERVER_VERSION and schema.verified:
        try:
            with tempfile.TemporaryDirectory(prefix="fast-agents-doctor-") as temporary:
                private_temp = Path(temporary)
                protect_private_path(private_temp, directory=True)
                result = asyncio.run(probe_model_catalog(resolved, Path.cwd(), private_temp / "server.stderr.log"))
            catalog = [
                {
                    "id": model.get("id"),
                    "display_name": model.get("displayName"),
                    "efforts": sorted(catalog_efforts(model)),
                    "service_tiers": sorted(catalog_tiers(model)),
                    "default_service_tier": model.get("defaultServiceTier"),
                }
                for model in result.models
                if isinstance(model.get("id"), str)
            ]
        except (FastAgentsError, OSError, subprocess.SubprocessError, asyncio.TimeoutError) as error:
            catalog_error = str(error)
    else:
        catalog_error = "model/list requires the verified 0.162.1 app-server adapter"
    version_supported = version == APP_SERVER_VERSION
    return {
        "available": bool(
            version_result.returncode == 0
            and help_result.returncode == 0
            and version_supported
            and schema.verified
            and "--listen" in help_text
            and "stdio://" in help_text
            and "generate-json-schema" in help_text
            and catalog_error is None
            and identity["available"]
        ),
        "backend": "app-server",
        "executable": resolved,
        "version": version,
        "supported_version": APP_SERVER_VERSION,
        "version_exit_code": version_result.returncode,
        "app_server_options": {
            "listen": "--listen" in help_text,
            "stdio": "stdio://" in help_text,
            "schema_generator": "generate-json-schema" in help_text,
        },
        "protocol": {
            "verified": schema.verified,
            "version": schema.version,
            "methods": list(schema.methods),
            "server_requests": list(schema.server_requests),
            "error": schema.error,
        },
        "fast_mode_feature_line": feature_line,
        "fast_mode_feature_available": feature_available,
        "fast_mode_note": "feature listing and catalog membership are capability evidence; effective thread tier is reported per worker and upstream execution remains unverified without provider telemetry",
        "standard_tier_request_value": "default",
        "standard_tier_catalog_note": "default is the explicit standard-route sentinel, not a model/list service tier id",
        "model_catalog": catalog,
        "model_catalog_error": catalog_error,
        "process_identity": identity,
        "platform_coverage": platform_coverage_label(),
        "schema_probe": "codex app-server generate-json-schema; no model inference",
        "catalog_probe": "app-server model/list; no model inference",
    }


def _codex_capabilities(executable: str | None = None, backend: str = "app-server") -> dict[str, Any]:
    if backend == "exec":
        return _exec_capabilities(executable)
    if backend == "app-server":
        return _app_server_capabilities(executable)
    return {"available": False, "backend": backend, "error": f"unsupported backend: {backend}"}


def _format_doctor_report(report: dict[str, Any], as_json: bool, backend: str) -> str:
    if as_json:
        return json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    if not report["available"]:
        if backend == "app-server":
            reasons = []
            if report.get("version") != APP_SERVER_VERSION:
                reasons.append(f"requires Codex CLI {APP_SERVER_VERSION}; installed {report.get('version', 'unknown')}")
            options = report.get("app_server_options", {})
            missing_options = [name for name in ("listen", "stdio", "schema_generator") if not options.get(name)]
            if missing_options:
                reasons.append(f"missing app-server options: {', '.join(missing_options)}")
            protocol = report.get("protocol", {})
            if not protocol.get("verified") and protocol.get("error"):
                reasons.append(str(protocol["error"]))
            if report.get("model_catalog_error"):
                reasons.append(str(report["model_catalog_error"]))
            identity = report.get("process_identity", {})
            if not identity.get("available") and identity.get("limitation"):
                reasons.append(f"process identity unavailable: {identity['limitation']}")
            return "Codex app-server unavailable: " + ("; ".join(reasons) or "required version, options, schema, catalog, or process identity check failed")
        return f"Codex CLI unavailable: {report.get('error', 'version or exec help failed')}"
    if backend == "app-server":
        protocol = report.get("protocol", {})
        options = report.get("app_server_options", {})
        tier_models = [
            model["id"]
            for model in report.get("model_catalog", [])
            if "priority" in model.get("service_tiers", [])
        ]
        return "\n".join(
            [
                f"Codex CLI: {report['version']} ({report['executable']})",
                f"app-server adapter: {APP_SERVER_VERSION}; schema {'verified' if protocol.get('verified') else 'unverified'}",
                f"app-server options: listen={options.get('listen')}, stdio={options.get('stdio')}, schema generator={options.get('schema_generator')}",
                f"Catalog models: {len(report.get('model_catalog', []))}; priority-tier models: {', '.join(tier_models) or 'none listed'}",
                "Standard tier: explicit `default` route; it is not listed as a model catalog tier.",
                f"fast_mode feature: {report.get('fast_mode_feature_line') or 'not listed'}",
                "Tier evidence: model/list is capability evidence; effective worker tier is recorded after thread/start; upstream execution is unverified without telemetry.",
                f"Process identity: {report['process_identity'].get('platform') or report['process_identity'].get('limitation')}",
                f"Host coverage: {report['platform_coverage']}",
                "Probe: local schema generation and model/list only; no model inference.",
            ]
        )
    flags = report["supported_flags"]
    supported = ", ".join(name for name, present in flags.items() if present) or "none detected"
    missing = ", ".join(name for name, present in flags.items() if not present) or "none"
    lines = [
        f"Codex CLI: {report['version']} ({report['executable']})",
        f"Recognized exec flags: {supported}",
        f"Missing required flag checks: {missing}",
        f"fast_mode feature list: {report['fast_mode_feature_line'] or ('unavailable (exit ' + str(report['feature_list_exit_code']) + ')' if report['feature_list_exit_code'] else 'not listed')}",
        f"Process identity API: {report['process_identity']['platform'] or report['process_identity']['limitation']}",
        'Fast request arguments: -c service_tier="fast" --enable fast_mode (no --fast flag)',
        'Role reasoning overrides: writer `model_reasoning_effort="low"` or `"max"`; reviewer `model_reasoning_effort="xhigh"`.',
        "Fast config keys are passed through -c; exec help does not enumerate the service_tier schema.",
        "A feature-list entry is not runtime evidence. Session fast status remains unverified until correlated effective configuration is emitted in that session's JSONL.",
        "Writer launch uses --approve-for-me with the workspace-write sandbox; reviewer launch uses -s read-only.",
    ]
    return "\n".join(lines)


def doctor(executable: str | None = None, as_json: bool = False, backend: str = "app-server") -> str:
    return _format_doctor_report(_codex_capabilities(executable, backend), as_json, backend)


def _print_lines(lines: list[str]) -> None:
    for line in lines:
        print(line)


def _runtime_root(value: str | None) -> Path:
    return Path(value).expanduser() if value else default_runtime_root()


def _find_codex(value: str | None) -> str:
    if value:
        if not Path(value).expanduser().exists():
            raise FastAgentsError(f"Codex executable does not exist: {value}")
        return str(Path(value).expanduser().resolve())
    found = shutil.which("codex")
    if not found:
        raise FastAgentsError("Codex CLI was not found on PATH; doctor reports local capabilities")
    return found


def _launch_summary(manifest_path: Path, plan_mode: bool) -> tuple[str, list[str], Path, str]:
    manifest = load_manifest(manifest_path)
    if plan_mode and manifest.has_writers:
        raise FastAgentsError("Plan Mode forbids writer roles; no worker was started")
    summary = [
        f"Objective: {manifest.objective}",
        f"Workspace: {manifest.workspace}",
        f"Backend: {manifest.backend}; maximum concurrent workers: {manifest.max_concurrency}",
        f"Fast selection: {manifest.fast_selection['mode']} ({', '.join(manifest.fast_selection['workers']) or 'no named subset'}); tier evidence starts unverified",
        f"Plan Mode: {'on' if (plan_mode or manifest.plan_mode) else 'off'}",
        "Selected team:",
    ]
    for role in manifest.roles:
        ownership = ", ".join(role.owns) if role.owns else "read-only"
        dependencies = ", ".join(role.depends_on) if role.depends_on else "none"
        summary.append(
            f"- {role.name}: {role.access}, {role.model}/{role.effort} ({role.setting_sources['model']} model, {role.setting_sources['effort']} effort); "
            f"Fast {'priority' if role.fast_requested else 'default'} ({role.setting_sources['fast']}); owns {ownership}; depends on {dependencies}"
        )
    summary.append(f"Shared contract: {manifest.shared_contract}")
    return manifest.objective, summary, manifest.workspace, ""


def _launch(args: argparse.Namespace) -> int:
    objective, summary, _workspace, _unused = _launch_summary(args.manifest, args.plan_mode)
    del objective
    manifest = load_manifest(args.manifest)
    codex = _find_codex(args.codex)
    _print_lines(summary)
    capabilities = _codex_capabilities(codex, manifest.backend)
    if manifest.backend == "exec":
        required = {"-C", "-m", "-c", "--enable", "--approve-for-me", "-s read-only", "--json", "--color", "-o"}
        missing = [name for name in required if not capabilities.get("supported_flags", {}).get(name, False)]
        if missing:
            raise FastAgentsError(f"Codex CLI is missing required exec options: {', '.join(sorted(missing))}")
        if manifest.fast_requested and not capabilities.get("fast_mode_feature_listed"):
            raise FastAgentsError("Fast mode is unavailable in the local feature list; resolve the unavailable option before launch")
    else:
        if not capabilities.get("app_server_options", {}).get("listen") or not capabilities.get("app_server_options", {}).get("stdio"):
            raise FastAgentsError("Codex app-server stdio options are unavailable")
        if not capabilities.get("protocol", {}).get("verified"):
            raise FastAgentsError(capabilities.get("protocol", {}).get("error") or "app-server protocol schema is not verified")
        if capabilities.get("version") != APP_SERVER_VERSION:
            raise FastAgentsError(f"app-server adapter supports Codex CLI {APP_SERVER_VERSION}; installed {capabilities.get('version')}")
        by_model = {item.get("id"): item for item in capabilities.get("model_catalog", []) if isinstance(item, dict)}
        for role in manifest.roles:
            model = by_model.get(role.model)
            if model is None:
                raise FastAgentsError(f"requested model {role.model} is unavailable in model/list; resolve the unavailable option before launch")
            if role.effort not in model.get("efforts", []):
                raise FastAgentsError(f"{role.model} does not list effort {role.effort}; resolve the unavailable option before launch")
            requested_tier = "priority" if role.fast_requested else "default"
            if role.fast_requested and requested_tier not in model.get("service_tiers", []):
                raise FastAgentsError(f"{requested_tier} tier is unavailable for {role.name}; resolve the unavailable option before launch")
            if role.fast_requested and not capabilities.get("fast_mode_feature_available"):
                raise FastAgentsError(f"Fast priority is unavailable for {role.name}; resolve the unavailable option before launch")
    print(
        f"Doctor: Codex {capabilities.get('version', 'unknown')} · {manifest.backend} · "
        f"schema {'verified' if capabilities.get('protocol', {}).get('verified') else 'exec options inspected'} · "
        f"host {capabilities.get('platform_coverage', platform_coverage_label())}"
    )
    if manifest.backend == "app-server":
        print(f"Model catalog: {len(capabilities.get('model_catalog', []))} entries · model/list only · no inference")
    if not capabilities["process_identity"]["available"]:
        raise FastAgentsError(
            f"process identity is unavailable; refusing launch: {capabilities['process_identity']['limitation']}"
        )
    task_id, task_dir, manifest = launch_from_path(args.manifest, _runtime_root(args.runtime_root), Path(__file__), codex)
    print(f"Started task {task_id}; runtime: {task_dir}")
    print(f"Writers: {'yes' if manifest.has_writers else 'no'}; selected Fast workers: {', '.join(role.name for role in manifest.roles if role.fast_requested) or 'none'}")
    return 0


def _status(args: argparse.Namespace, resume: bool = False) -> int:
    root = _runtime_root(args.runtime_root).expanduser().resolve()
    task_dir = task_path(root, args.task)
    status = read_task_status(task_dir)
    if status.get("backend") == "app-server":
        recover = bool(getattr(args, "recover", False))
        if resume and recover and status.get("state") == "recovery_required":
            manager_identity_state = status.get("manager", {}).get("identity_state")
            if manager_identity_state == "alive":
                result = send_control_request(task_dir, "recover")
                print(
                    "Recovery: "
                    + ("reconciled persisted turns; waiting roles may continue" if result.get("reconciled") else "state remains uncertain; no turn was replayed")
                )
            elif manager_identity_state in {"dead", "stale_identity"}:
                start_recovery_supervisor(
                    args.task,
                    root,
                    Path(__file__),
                    _find_codex(args.codex) if getattr(args, "codex", None) else _recover_codex(task_dir),
                )
            else:
                raise FastAgentsError("cannot prove the previous supervisor stopped; recovery refused")
            status = read_task_status(task_dir)
        elif status.get("manager", {}).get("identity_state") == "alive":
            # Status and resume reconnect through the supervisor's read-only capability.
            status = send_control_request(task_dir, "status")
    if args.json:
        print(json.dumps(status, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(render_status(task_dir, status))
    if resume and args.watch and status["state"] not in {"completed", "failed"}:
        watch_task(task_dir, continuous=True, interval=args.interval)
    return 0


def _recover_codex(task_dir: Path) -> str:
    task_manifest = json.loads((task_dir / "manifest.json").read_text(encoding="utf-8"))
    codex = task_manifest.get("codex_executable")
    if not isinstance(codex, str) or not codex:
        raise FastAgentsError("Codex CLI was not found on PATH; cannot recover this app-server task")
    return codex


def _control(args: argparse.Namespace) -> int:
    task_dir = task_path(_runtime_root(args.runtime_root).expanduser().resolve(), args.task)
    payload: dict[str, Any] = {}
    if args.control_command == "events":
        payload = {"after_seq": args.after_seq, "limit": args.limit}
    elif args.control_command == "steer":
        payload = {"role": args.role, "turn_id": args.turn_id, "text": args.text}
    elif args.control_command == "interrupt":
        payload = {"role": args.role, "turn_id": args.turn_id}
    elif args.control_command == "reply":
        request_id = _request_id_arg(args.request_id)
        turn_id = None if args.turn_id == "none" else args.turn_id
        payload = {
            "request_id": request_id,
            "thread_id": args.thread_id,
            "turn_id": turn_id,
            "decision": args.decision,
            "payload": json.loads(args.payload_json) if args.payload_json else None,
        }
        if payload["payload"] is not None and not isinstance(payload["payload"], dict):
            raise FastAgentsError("--payload-json must decode to a JSON object")
    elif args.control_command == "inspect-request":
        payload = {"request_id": _request_id_arg(args.request_id)}
    result = send_control_request(task_dir, args.control_command, payload)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def _request_id_arg(value: str) -> str | int:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return value
    if isinstance(parsed, (str, int)) and not isinstance(parsed, bool):
        return parsed
    return value


def _defaults_show(args: argparse.Namespace) -> int:
    target = Path(args.path).expanduser() if args.path else default_config_path()
    defaults = load_defaults(target)
    if args.json:
        print(json.dumps(defaults, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"Dedicated defaults file: {target}")
        fast_selection = defaults.get("fast_selection")
        if fast_selection is None:
            legacy = defaults.get("fast_requested")
            fast_selection = "all" if legacy is True else "none" if legacy is False else "recommended all"
        print(f"Fast workers: {fast_selection}")
        writer = defaults.get("writer", {})
        reviewer = defaults.get("reviewer", {})
        print(f"Writer: {writer.get('model', 'recommended gpt-6-luna')} / {writer.get('effort', 'recommended low')}")
        print(f"Reviewer: {reviewer.get('model', 'recommended gpt-6.1-sol')} / {reviewer.get('effort', 'recommended xhigh')}")
    return 0


def _run_defaults_set(args: argparse.Namespace) -> int:
    if args.fast_requested is None and args.fast_workers is None and args.writer_effort is None:
        raise FastAgentsError("set at least one saved default")
    if args.fast_requested is not None and args.fast_workers is not None:
        raise FastAgentsError("choose either --fast-requested or --fast-workers, not both")
    lines = save_defaults(
        Path(args.path) if args.path else None,
        args.fast_requested,
        args.writer_effort,
        apply=args.apply,
        confirm_sha=args.confirm,
        expected_before=args.expected_before,
        fast_selection=args.fast_workers,
    )
    _print_lines(lines)
    return 0


def _rollback(args: argparse.Namespace) -> int:
    if args.rollback_kind == "install":
        lines = rollback_skill(args.agents_home, args.claude_home, apply=args.apply)
    elif args.rollback_kind == "defaults":
        lines = rollback_defaults(Path(args.path) if args.path else None, apply=args.apply)
    else:
        lines = rollback_instruction(Path(args.path) if args.path else None, apply=args.apply)
    _print_lines(lines)
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fast_agents.py", description="Safe, inspectable Codex agent coordination")
    sub = parser.add_subparsers(dest="command", required=True)

    doctor_parser = sub.add_parser("doctor", help="inspect the local Codex CLI flags and fast-mode capability")
    doctor_parser.add_argument("--codex")
    doctor_parser.add_argument("--backend", choices=["app-server", "exec"], default="app-server")
    doctor_parser.add_argument("--json", action="store_true")

    launch_parser = sub.add_parser("launch", help="launch a validated manifest as a private task")
    launch_parser.add_argument("--manifest", type=Path, required=True)
    launch_parser.add_argument("--runtime-root")
    launch_parser.add_argument("--codex", help="Codex executable path; defaults to PATH discovery")
    launch_parser.add_argument("--plan-mode", action="store_true", help="enforce the host's current Plan Mode")

    status_parser = sub.add_parser("status", help="summarize an existing task without printing raw model output")
    status_parser.add_argument("--task", required=True)
    status_parser.add_argument("--runtime-root")
    status_parser.add_argument("--json", action="store_true")

    watch_parser = sub.add_parser("watch", help="watch existing task events once or continuously")
    watch_parser.add_argument("--task", required=True)
    watch_parser.add_argument("--runtime-root")
    watch_parser.add_argument("--continuous", action="store_true")
    watch_parser.add_argument("--interval", type=float, default=2.0)

    resume_parser = sub.add_parser("resume", help="read existing state; never starts duplicate workers")
    resume_parser.add_argument("--task", required=True)
    resume_parser.add_argument("--runtime-root")
    resume_parser.add_argument("--watch", action="store_true", help="attach a monitor to existing workers")
    resume_parser.add_argument("--interval", type=float, default=2.0)
    resume_parser.add_argument("--json", action="store_true")
    resume_parser.add_argument("--codex", help="Codex executable override used only for explicit recovery")
    resume_parser.add_argument("--recover", action="store_true", help="reconcile persisted threads before continuing")

    control_parser = sub.add_parser("control", help="poll a task or send an explicit current-turn control request")
    control_parser.add_argument("--task", required=True)
    control_parser.add_argument("--runtime-root")
    control_sub = control_parser.add_subparsers(dest="control_command", required=True)
    control_sub.add_parser("status", help="read current task state (read-only)")
    events_parser = control_sub.add_parser("events", help="poll summarized events (read-only)")
    events_parser.add_argument("--after-seq", type=int, default=0)
    events_parser.add_argument("--limit", type=int, default=100)
    steer_parser = control_sub.add_parser("steer", help="send a bounded message to a current worker turn")
    steer_parser.add_argument("--role", required=True)
    steer_parser.add_argument("--turn-id", required=True)
    steer_parser.add_argument("--text", required=True)
    interrupt_parser = control_sub.add_parser("interrupt", help="interrupt a current worker turn")
    interrupt_parser.add_argument("--role", required=True)
    interrupt_parser.add_argument("--turn-id", required=True)
    reply_parser = control_sub.add_parser("reply", help="answer one current pending app-server request")
    reply_parser.add_argument("--request-id", required=True, help='copy request_id from status; quote JSON strings, e.g. "abc"')
    reply_parser.add_argument("--thread-id", required=True)
    reply_parser.add_argument("--turn-id", required=True, help="use none only when the request explicitly has a null turn id")
    reply_parser.add_argument("--decision", choices=["accept", "acceptForSession", "decline", "cancel"])
    reply_parser.add_argument("--payload-json", help="method-specific explicit JSON response object")
    inspect_parser = control_sub.add_parser(
        "inspect-request",
        help="explicitly inspect sensitive details for one current pending request",
    )
    inspect_parser.add_argument("--request-id", required=True)
    control_sub.add_parser("recover", help="reconcile the current persisted thread/turn state before continuing")

    monitor_parser = sub.add_parser("monitor", help="manage only registered task watcher processes")
    monitor_sub = monitor_parser.add_subparsers(dest="monitor_command", required=True)
    cleanup_parser = monitor_sub.add_parser("cleanup", help="stop or remove only registered watchers")
    cleanup_parser.add_argument("--task", required=True)
    cleanup_parser.add_argument("--runtime-root")
    cleanup_parser.add_argument("--apply", action="store_true", help="apply the previewed monitor-only cleanup")

    defaults_parser = sub.add_parser("defaults", help="view or configure the dedicated saved defaults file")
    defaults_sub = defaults_parser.add_subparsers(dest="defaults_command", required=True)
    defaults_show = defaults_sub.add_parser("show")
    defaults_show.add_argument("--path")
    defaults_show.add_argument("--json", action="store_true")
    defaults_set = defaults_sub.add_parser("set")
    defaults_set.add_argument("--path")
    defaults_set.add_argument("--fast-requested", type=_bool_arg)
    defaults_set.add_argument("--fast-workers", type=_fast_workers_arg, help="all, none, or comma-separated worker names")
    defaults_set.add_argument("--writer-effort", choices=["low", "max"])
    defaults_set.add_argument("--apply", action="store_true")
    defaults_set.add_argument("--confirm")
    defaults_set.add_argument("--expected-before")

    install_parser = sub.add_parser("install", help="preview or install the complete skill folder into selected skill homes")
    install_parser.add_argument("--agents-home", type=Path)
    install_parser.add_argument("--claude-home", type=Path)
    install_parser.add_argument("--apply", action="store_true")

    instructions_parser = sub.add_parser("instructions", help="preview or apply the owned Codex instruction block")
    instructions_sub = instructions_parser.add_subparsers(dest="instructions_command", required=True)
    instruction_preview_parser = instructions_sub.add_parser("preview")
    instruction_preview_parser.add_argument("--path", type=Path, help="Codex home directory or an AGENTS.md file")
    instruction_preview_parser.add_argument("--runtime-root")
    instruction_apply_parser = instructions_sub.add_parser("apply")
    instruction_apply_parser.add_argument("--plan", required=True)
    instruction_apply_parser.add_argument("--confirm", required=True)
    instruction_apply_parser.add_argument("--expected-before", required=True)
    instruction_apply_parser.add_argument("--runtime-root")

    rollback_parser = sub.add_parser("rollback", help="preview or roll back unchanged fast-agents-owned setup")
    rollback_sub = rollback_parser.add_subparsers(dest="rollback_kind", required=True)
    rollback_install_parser = rollback_sub.add_parser("install")
    rollback_install_parser.add_argument("--agents-home", type=Path)
    rollback_install_parser.add_argument("--claude-home", type=Path)
    rollback_install_parser.add_argument("--apply", action="store_true")
    rollback_defaults_parser = rollback_sub.add_parser("defaults")
    rollback_defaults_parser.add_argument("--path")
    rollback_defaults_parser.add_argument("--apply", action="store_true")
    rollback_instruction_parser = rollback_sub.add_parser("instructions")
    rollback_instruction_parser.add_argument("--path", type=Path)
    rollback_instruction_parser.add_argument("--apply", action="store_true")

    supervise_parser = sub.add_parser("_supervise", help=argparse.SUPPRESS)
    supervise_parser.add_argument("--task", required=True)
    supervise_parser.add_argument("--runtime-root", required=True)
    supervise_parser.add_argument("--codex", required=True)
    supervise_parser.add_argument("--recover", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "doctor":
            report = _codex_capabilities(args.codex, args.backend)
            print(_format_doctor_report(report, args.json, args.backend))
            if args.backend == "app-server":
                return 0 if report.get("available") else 1
            required = report.get("supported_flags", {})
            needed = {"-C", "-m", "-c", "--enable", "--approve-for-me", "-s read-only", "--json", "--color", "-o"}
            return 0 if report.get("available") and all(required.get(key, False) for key in needed) else 1
        if args.command == "launch":
            return _launch(args)
        if args.command == "status":
            return _status(args)
        if args.command == "watch":
            task_dir = task_path(_runtime_root(args.runtime_root).expanduser().resolve(), args.task)
            watch_task(task_dir, args.continuous, args.interval)
            return 0
        if args.command == "resume":
            return _status(
                argparse.Namespace(
                    task=args.task,
                    runtime_root=args.runtime_root,
                    json=args.json,
                    watch=args.watch,
                    interval=args.interval,
                    recover=args.recover,
                    codex=args.codex,
                ),
                resume=True,
            )
        if args.command == "control":
            return _control(args)
        if args.command == "monitor":
            task_dir = task_path(_runtime_root(args.runtime_root).expanduser().resolve(), args.task)
            _print_lines(cleanup_monitors(task_dir, args.apply))
            return 0
        if args.command == "defaults":
            if args.defaults_command == "show":
                return _defaults_show(args)
            return _run_defaults_set(args)
        if args.command == "install":
            _print_lines(install_skill(args.agents_home, args.claude_home, args.apply))
            return 0
        if args.command == "instructions":
            if args.instructions_command == "preview":
                runtime = _runtime_root(args.runtime_root)
                _print_lines(show_instruction_preview(args.path, runtime))
                return 0
            runtime = _runtime_root(args.runtime_root)
            _print_lines(
                apply_instruction_preview(runtime, args.plan, args.confirm, args.expected_before)
            )
            return 0
        if args.command == "rollback":
            return _rollback(args)
        if args.command == "_supervise":
            return supervisor_main(args.task, Path(args.runtime_root), args.codex, recover=args.recover)
        parser.error("unknown command")
    except KeyboardInterrupt:
        print("Monitoring interrupted; worker processes were left running.", file=sys.stderr)
        return 130
    except (FastAgentsError, OSError, subprocess.SubprocessError, json.JSONDecodeError) as error:
        print(f"fast-agents: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
