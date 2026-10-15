"""Opt-in skill install, saved defaults, and managed Codex instruction setup."""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import json
import os
import shutil
import stat
import uuid
from pathlib import Path
from typing import Any

from fast_agents_common import (
    FastAgentsError,
    atomic_write_bytes,
    atomic_write_json,
    default_config_path,
    load_defaults,
    read_json,
    sha256_bytes,
    sha256_file,
    validate_defaults,
)


INSTALL_MARKER = ".fast-agents-install.json"
INSTRUCTION_OWNER = ".fast-agents-instruction-owner.json"
DEFAULTS_OWNER_SUFFIX = ".fast-agents-owner.json"
MANAGED_BLOCK = "\n".join(
    [
        "<!-- fast-agents:begin -->",
        "For Codex CLI or Codex app sessions that load this shared instruction file, use the `fast-codex-subagents` skill as the delegation coordinator for substantive execution, including a single bounded worker when that is the smallest useful team. Keep brief factual answers, conversation, and planning in the foreground. Choose the team, model, effort, worker count, and per-worker Fast setting automatically; use Fast for all workers by default, while preserving explicit user routing and choices, saved and project Fast settings, ownership, sandbox, Plan Mode, and on-request approval rules. Avoid routine setup questions.",
        "This block is shared instruction text only; it applies only in clients configured to load the selected Codex home instruction file. The setup command cannot verify Codex app activation, so verify that inside the app.",
        "<!-- fast-agents:end -->",
    ]
)
MANAGED_BEGIN = b"<!-- fast-agents:begin -->"
MANAGED_END = b"<!-- fast-agents:end -->"


def _utc_stamp() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _tree_hashes(folder: Path, exclude_marker: bool = True) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for path in sorted(folder.rglob("*")):
        if path.is_symlink():
            raise FastAgentsError(f"symlinks are not allowed in the installed skill folder: {path.name}")
        if path.is_dir():
            continue
        relative = path.relative_to(folder).as_posix()
        if relative == INSTALL_MARKER or "__pycache__" in path.parts or path.suffix == ".pyc":
            continue
        if not path.is_file():
            raise FastAgentsError(f"unsupported install artifact: {relative}")
        hashes[relative] = sha256_file(path)
    return hashes


def _skill_source() -> Path:
    return Path(__file__).resolve().parent.parent


def _normalize_skill_home(path: Path) -> Path:
    return path.expanduser().resolve()


def _existing_install_status(destination: Path, source_hashes: dict[str, str]) -> tuple[str, str]:
    if destination.is_symlink():
        return "collision", "target is a symlink"
    if not destination.exists():
        return "new", "will install the complete fast-codex-subagents folder"
    if not destination.is_dir():
        return "collision", "target exists and is not a directory"
    marker_path = destination / INSTALL_MARKER
    if not marker_path.is_file():
        return "collision", "target exists without an owned skill install marker"
    try:
        marker = read_json(marker_path)
        current = _tree_hashes(destination)
    except (FastAgentsError, OSError) as error:
        return "collision", f"existing install could not be verified: {error}"
    if marker.get("owner") != "hawary-fast-agents" or not isinstance(marker.get("files"), dict):
        return "collision", "target is owned by another installer"
    if current != marker["files"]:
        return "modified", "owned install files changed; preserved without overwriting"
    if current != source_hashes:
        return "outdated", "this unchanged owned install differs from the current package; upgrade is not automatic"
    return "identical", "already installed unchanged; no files will be written"


def install_preview(agents_home: Path | None, claude_home: Path | None) -> list[dict[str, str]]:
    if claude_home is not None:
        raise FastAgentsError(
            "fast-codex-subagents is Codex-only; --claude-home installation is unsupported. "
            "Use --agents-home to install for Codex. Legacy Claude installs can only be rolled back."
        )
    if agents_home is None:
        raise FastAgentsError("Codex-only install requires --agents-home")
    selected_home = _normalize_skill_home(agents_home)
    if any(part.casefold() == ".claude" for part in selected_home.parts):
        raise FastAgentsError("Codex-only installation cannot target a .claude directory")
    selected = [("agents", selected_home)]
    source = _skill_source()
    source_hashes = _tree_hashes(source, exclude_marker=False)
    preview = []
    for kind, home in selected:
        destination = home / "fast-codex-subagents"
        state, detail = _existing_install_status(destination, source_hashes)
        preview.append({"kind": kind, "home": str(home), "destination": str(destination), "state": state, "detail": detail})
    collisions = [item for item in preview if item["state"] in {"collision", "modified", "outdated"}]
    if collisions:
        descriptions = "; ".join(f"{item['destination']}: {item['detail']}" for item in collisions)
        raise FastAgentsError(descriptions)
    return preview


def install_skill(agents_home: Path | None, claude_home: Path | None, apply: bool = False) -> list[str]:
    preview = install_preview(agents_home, claude_home)
    messages = [f"{item['destination']}: {item['detail']}" for item in preview]
    if not apply:
        return messages + ["Preview only. Rerun with --apply to install."]

    source = _skill_source()
    created: list[Path] = []
    try:
        for item in preview:
            if item["state"] == "identical":
                continue
            home = Path(item["home"])
            destination = Path(item["destination"])
            home.mkdir(parents=True, exist_ok=True, mode=0o700)
            staging = home / f".fast-agents.install-{uuid.uuid4()}"
            shutil.copytree(
                source,
                staging,
                symlinks=False,
                ignore=shutil.ignore_patterns(INSTALL_MARKER, "__pycache__", "*.pyc"),
            )
            file_hashes = _tree_hashes(staging, exclude_marker=False)
            marker = {
                "owner": "hawary-fast-agents",
                "schema_version": 1,
                "files": file_hashes,
                "installed_at": _utc_stamp(),
            }
            atomic_write_json(staging / INSTALL_MARKER, marker)
            if destination.exists() or destination.is_symlink():
                shutil.rmtree(staging, ignore_errors=True)
                raise FastAgentsError(f"install target appeared during setup: {destination}")
            try:
                os.rename(staging, destination)
            except FileExistsError as error:
                shutil.rmtree(staging, ignore_errors=True)
                raise FastAgentsError(f"install target appeared during setup: {destination}") from error
            created.append(destination)
            messages.append(f"Installed complete fast-codex-subagents folder at {destination}")
    except Exception:
        for destination in reversed(created):
            state, _ = _existing_install_status(destination, _tree_hashes(source, exclude_marker=False))
            if state == "identical":
                shutil.rmtree(destination)
        raise
    return messages


def rollback_skill(agents_home: Path | None, claude_home: Path | None, apply: bool = False) -> list[str]:
    selected = [path for path in (agents_home, claude_home) if path is not None]
    if not selected:
        raise FastAgentsError("choose --agents-home and/or --claude-home explicitly")
    plans: list[tuple[Path, str]] = []
    for home in selected:
        destination = _normalize_skill_home(home) / "fast-codex-subagents"
        state, detail = _existing_install_status(destination, _tree_hashes(_skill_source(), exclude_marker=False))
        if state not in {"identical", "outdated", "modified"}:
            raise FastAgentsError(f"{destination}: cannot verify an owned install ({detail})")
        if state == "modified":
            raise FastAgentsError(f"{destination}: {detail}; rollback refused to preserve modified files")
        plans.append((destination, detail))
    messages = [f"{destination}: {detail}; rollback would remove this unchanged owned folder" for destination, detail in plans]
    if not apply:
        return messages + ["Preview only. Rerun with --apply to remove unchanged owned installs."]
    for destination, _ in plans:
        shutil.rmtree(destination)
        messages.append(f"Removed unchanged owned install at {destination}")
    return messages


def _defaults_owner_path(target: Path) -> Path:
    return target.with_name(target.name + DEFAULTS_OWNER_SUFFIX)


def _defaults_current_sha(target: Path) -> str | None:
    return sha256_file(target) if target.exists() else None


def defaults_preview(
    target: Path | None,
    fast_requested: bool | None = None,
    writer_effort: str | None = None,
    fast_selection: dict[str, Any] | None = None,
) -> tuple[Path, dict[str, Any], str | None, str]:
    path = (target or default_config_path()).expanduser().resolve()
    old_sha = _defaults_current_sha(path)
    existing = load_defaults(path)
    if fast_requested is not None:
        existing["fast_requested"] = fast_requested
        existing.pop("fast_selection", None)
    if fast_selection is not None:
        existing["fast_selection"] = fast_selection
        existing.pop("fast_requested", None)
    if writer_effort is not None:
        if writer_effort not in {"low", "max"}:
            raise FastAgentsError("writer effort default must be low or max")
        existing.setdefault("writer", {})["effort"] = writer_effort
    existing.setdefault("writer", {}).setdefault("model", "gpt-6-luna")
    existing.setdefault("reviewer", {}).setdefault("model", "gpt-6.1-sol")
    existing.setdefault("reviewer", {}).setdefault("effort", "xhigh")
    validate_defaults(existing)
    encoded = json.dumps(existing, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    return path, existing, old_sha, sha256_bytes(encoded)


def save_defaults(
    target: Path | None,
    fast_requested: bool | None,
    writer_effort: str | None,
    apply: bool = False,
    confirm_sha: str | None = None,
    expected_before: str | None = None,
    fast_selection: dict[str, Any] | None = None,
) -> list[str]:
    path, data, before_sha, after_sha = defaults_preview(target, fast_requested, writer_effort, fast_selection)
    encoded = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    messages = [f"Dedicated defaults file: {path}", f"Current SHA-256: {before_sha or 'absent'}", "Planned contents:", encoded.decode("utf-8").rstrip(), f"New SHA-256: {after_sha}"]
    if not apply:
        return messages + ["Preview only. Rerun with --apply, --expected-before, and --confirm."]
    if before_sha == after_sha:
        return messages + ["Defaults already match; no changes were written."]
    if confirm_sha != after_sha or expected_before != (before_sha or "absent"):
        raise FastAgentsError("defaults confirmation does not match the reviewed preview")
    if _defaults_current_sha(path) != before_sha:
        raise FastAgentsError("defaults file changed after preview; no changes were written")

    owner_path = _defaults_owner_path(path)
    history: dict[str, Any] = {"owner": "hawary-fast-agents-defaults", "changes": []}
    if owner_path.exists():
        history = read_json(owner_path)
        if history.get("owner") != "hawary-fast-agents-defaults" or not isinstance(history.get("changes"), list):
            raise FastAgentsError("defaults rollback record belongs to another setup")
    backup_path: Path | None = None
    if path.exists():
        backup_path = path.with_name(f"{path.name}.backup.{_utc_stamp()}.{uuid.uuid4().hex[:8]}")
        shutil.copy2(path, backup_path)
    history["changes"].append(
        {
            "before_sha256": before_sha,
            "after_sha256": after_sha,
            "backup": str(backup_path) if backup_path else None,
        }
    )
    try:
        atomic_write_bytes(path, encoded)
        atomic_write_json(owner_path, history)
    except OSError:
        if backup_path:
            shutil.copy2(backup_path, path)
        else:
            path.unlink(missing_ok=True)
        raise
    return messages + [f"Saved defaults at {path}"]


def rollback_defaults(target: Path | None, apply: bool = False) -> list[str]:
    path = (target or default_config_path()).expanduser().resolve()
    owner_path = _defaults_owner_path(path)
    if not owner_path.is_file():
        raise FastAgentsError(f"no owned defaults rollback record found for {path}")
    history = read_json(owner_path)
    changes = history.get("changes")
    if history.get("owner") != "hawary-fast-agents-defaults" or not isinstance(changes, list) or not changes:
        raise FastAgentsError("defaults rollback history is invalid")
    latest = changes[-1]
    current_sha = _defaults_current_sha(path)
    if current_sha != latest.get("after_sha256"):
        raise FastAgentsError("defaults file changed after setup; rollback refused to overwrite it")
    backup = Path(latest["backup"]) if latest.get("backup") else None
    if backup and (not backup.is_file() or sha256_file(backup) != latest.get("before_sha256")):
        raise FastAgentsError("defaults backup is missing or changed; rollback refused")
    messages = [f"Defaults file: {path}", "Rollback would restore the last backup or remove the file created by this helper."]
    if not apply:
        return messages + ["Preview only. Rerun with --apply to roll back the unchanged owned defaults."]
    if backup:
        shutil.copy2(backup, path)
    else:
        path.unlink(missing_ok=True)
    changes.pop()
    if changes:
        atomic_write_json(owner_path, history)
    else:
        owner_path.unlink(missing_ok=True)
    return messages + ["Rolled back the latest unchanged defaults setup."]


def _resolve_instruction_target(value: Path | None) -> Path:
    if value is None:
        codex_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")).expanduser()
        candidate = codex_home
    else:
        candidate = value.expanduser()
    if candidate.name not in {"AGENTS.md", "AGENTS.override.md"}:
        directory = candidate.resolve()
        override = directory / "AGENTS.override.md"
        target = override if override.is_file() else directory / "AGENTS.md"
    else:
        if candidate.is_symlink():
            raise FastAgentsError("instruction target is a symlink; select its actual Codex home file explicitly")
        candidate = candidate.resolve()
        override = candidate.parent / "AGENTS.override.md"
        target = override if candidate.name == "AGENTS.md" and override.is_file() else candidate
    if target.is_symlink():
        raise FastAgentsError("instruction target is a symlink; select its actual Codex home file explicitly")
    return target.resolve()


def _instruction_owner_path(target: Path) -> Path:
    return target.parent / INSTRUCTION_OWNER


def _managed_instruction_span(content: bytes) -> tuple[int, int, bytes]:
    """Return the one complete generated block, including its trailing LF."""
    if content.count(MANAGED_BEGIN) != 1 or content.count(MANAGED_END) != 1:
        raise FastAgentsError("managed instruction markers are missing or duplicated")
    start = content.find(MANAGED_BEGIN)
    end_marker = content.find(MANAGED_END)
    if end_marker <= start or content[start + len(MANAGED_BEGIN):start + len(MANAGED_BEGIN) + 1] != b"\n":
        raise FastAgentsError("managed instruction markers are malformed")
    if start and content[start - 1:start] != b"\n":
        raise FastAgentsError("managed instruction markers are malformed")
    end = end_marker + len(MANAGED_END)
    if content[end:end + 1] == b"\n":
        end += 1
    return start, end, content[start:end]


def _validate_owned_instruction(target: Path, content: bytes, owner: dict[str, Any]) -> tuple[int, int, bytes]:
    """Verify the setup receipt, original rollback baseline, and owned block."""
    if owner.get("owner") != "hawary-fast-agents-instructions" or owner.get("target") != str(target):
        raise FastAgentsError("instruction ownership record belongs to another tool")
    if owner.get("schema_version") != 1 or sha256_bytes(content) != owner.get("after_sha256"):
        raise FastAgentsError("instruction file changed after managed setup; refusing to replace it")

    before_exists = owner.get("before_exists")
    before_sha = owner.get("before_sha256")
    backup_value = owner.get("backup")
    if before_exists is True:
        if not isinstance(before_sha, str) or not isinstance(backup_value, str):
            raise FastAgentsError("instruction rollback ownership could not be verified")
        backup = Path(backup_value)
        if (
            backup.parent != target.parent
            or backup.is_symlink()
            or not backup.is_file()
            or sha256_file(backup) != before_sha
        ):
            raise FastAgentsError("instruction backup is missing or changed; rollback refused")
    elif before_exists is False:
        if before_sha is not None or backup_value is not None:
            raise FastAgentsError("instruction rollback ownership could not be verified")
    else:
        raise FastAgentsError("instruction rollback ownership could not be verified")

    start, end, block = _managed_instruction_span(content)
    if sha256_bytes(block) != owner.get("block_sha256"):
        raise FastAgentsError("the managed instruction block changed; refusing to replace it")
    return start, end, block


def instruction_preview(target_arg: Path | None, runtime_root: Path) -> tuple[str, Path, str, str]:
    target = _resolve_instruction_target(target_arg)
    exists = target.is_file()
    original = target.read_bytes() if exists else b""
    try:
        original.decode("utf-8")
    except UnicodeDecodeError as error:
        raise FastAgentsError("selected Codex instruction file is not UTF-8; left unchanged") from error
    owner_path = _instruction_owner_path(target)
    refresh_owner = False
    owner_before_sha: str | None = None
    if owner_path.exists():
        owner = read_json(owner_path)
        if not target.is_file():
            raise FastAgentsError("instruction file changed after managed setup; refusing to replace it")
        start, end, current_block = _validate_owned_instruction(target, original, owner)
        current_block_sha = sha256_bytes((MANAGED_BLOCK + "\n").encode("utf-8"))
        if sha256_bytes(current_block) == current_block_sha:
            return "unchanged", target, owner.get("before_sha256") or "absent", owner["after_sha256"]
        updated = original[:start] + (MANAGED_BLOCK + "\n").encode("utf-8") + original[end:]
        refresh_owner = True
        owner_before_sha = sha256_file(owner_path)
    else:
        existing_text = original.decode("utf-8")
        if "<!-- fast-agents:begin -->" in existing_text or "<!-- fast-agents:end -->" in existing_text:
            raise FastAgentsError("managed instruction markers already exist without an ownership record; refusing collision")
        separator = "" if not existing_text or existing_text.endswith("\n") else "\n"
        updated = (existing_text + separator + MANAGED_BLOCK + "\n").encode("utf-8")
    before_sha = sha256_bytes(original) if exists else "absent"
    after_sha = sha256_bytes(updated)

    runtime = runtime_root.expanduser().resolve()
    plans_dir = runtime / "instruction-plans"
    plans_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    plan_id = str(uuid.uuid4())
    plan = {
        "owner": "hawary-fast-agents-instruction-plan",
        "plan_id": plan_id,
        "target": str(target),
        "before_exists": exists,
        "before_sha256": before_sha,
        "after_sha256": after_sha,
        "mode": stat.S_IMODE(target.stat().st_mode) if exists else 0o600,
        "after_bytes_b64": base64.b64encode(updated).decode("ascii"),
        "refresh_owner": refresh_owner,
        "owner_before_sha256": owner_before_sha,
        "created_at": _utc_stamp(),
    }
    atomic_write_json(plans_dir / f"{plan_id}.json", plan)
    return plan_id, target, before_sha, after_sha


def _print_instruction_preview(target: Path, before_sha: str, after_sha: str) -> list[str]:
    return [
        f"Selected active Codex instruction file: {target}",
        f"Original SHA-256: {before_sha}",
        "Exact managed block to add or replace:",
        MANAGED_BLOCK,
        f"Result SHA-256: {after_sha}",
    ]


def show_instruction_preview(target_arg: Path | None, runtime_root: Path) -> list[str]:
    plan_id, target, before_sha, after_sha = instruction_preview(target_arg, runtime_root)
    if plan_id == "unchanged":
        return [f"Selected active Codex instruction file: {target}", "The owned instruction block is already installed unchanged."]
    plan = read_json(runtime_root.expanduser().resolve() / "instruction-plans" / f"{plan_id}.json")
    lines = _print_instruction_preview(target, before_sha, after_sha)
    if plan.get("refresh_owner") is True:
        lines.append("This preview replaces only the unchanged block in the existing owned setup.")
    return lines + [f"Preview ID: {plan_id}", "No global instruction file was changed."]


def apply_instruction_preview(
    runtime_root: Path,
    plan_id: str,
    confirm_sha: str,
    expected_before: str,
) -> list[str]:
    try:
        uuid.UUID(plan_id)
    except ValueError as error:
        raise FastAgentsError("instruction preview id is invalid") from error
    plan_path = runtime_root.expanduser().resolve() / "instruction-plans" / f"{plan_id}.json"
    plan = read_json(plan_path)
    if plan.get("owner") != "hawary-fast-agents-instruction-plan" or plan.get("plan_id") != plan_id:
        raise FastAgentsError("instruction preview ownership could not be verified")
    target = Path(plan["target"])
    active_target = _resolve_instruction_target(target)
    if active_target != target:
        if target.name == "AGENTS.md":
            raise FastAgentsError("AGENTS.override.md became active after preview; no instruction file was changed")
        raise FastAgentsError("selected instruction target changed after preview; no file was changed")
    current_exists = target.is_file()
    current_sha = sha256_file(target) if current_exists else "absent"
    current_content = target.read_bytes() if current_exists else b""
    if current_sha != plan["before_sha256"] or expected_before != plan["before_sha256"]:
        raise FastAgentsError("instruction file changed after preview; no changes were written")
    if confirm_sha != plan["after_sha256"]:
        raise FastAgentsError("confirmation hash does not match the exact instruction preview")
    after_bytes = base64.b64decode(plan["after_bytes_b64"], validate=True)
    if sha256_bytes(after_bytes) != plan["after_sha256"]:
        raise FastAgentsError("instruction preview integrity check failed")
    owner_path = _instruction_owner_path(target)
    refresh_owner = plan.get("refresh_owner") is True
    backup_path: Path | None = None
    prior_owner_content: bytes | None = None
    if refresh_owner:
        if not current_exists or not owner_path.is_file():
            raise FastAgentsError("instruction ownership changed after preview; no changes were written")
        prior_owner_content = owner_path.read_bytes()
        if sha256_bytes(prior_owner_content) != plan.get("owner_before_sha256"):
            raise FastAgentsError("instruction ownership record changed after preview; no changes were written")
        prior_owner = read_json(owner_path)
        _validate_owned_instruction(target, target.read_bytes(), prior_owner)
        owner = dict(prior_owner)
        owner["after_sha256"] = plan["after_sha256"]
        owner["block_sha256"] = sha256_bytes((MANAGED_BLOCK + "\n").encode("utf-8"))
    else:
        if owner_path.exists():
            raise FastAgentsError("instruction ownership record appeared after preview; no changes were written")
        if current_exists:
            backup_path = target.with_name(f"{target.name}.fast-agents-backup.{_utc_stamp()}.{uuid.uuid4().hex[:8]}")
            shutil.copy2(target, backup_path)
            if sha256_file(backup_path) != current_sha:
                backup_path.unlink(missing_ok=True)
                raise FastAgentsError("instruction backup verification failed; no changes were written")
        owner = {
            "owner": "hawary-fast-agents-instructions",
            "schema_version": 1,
            "target": str(target),
            "before_exists": current_exists,
            "before_sha256": current_sha if current_exists else None,
            "after_sha256": plan["after_sha256"],
            "block_sha256": sha256_bytes((MANAGED_BLOCK + "\n").encode("utf-8")),
            "backup": str(backup_path) if backup_path else None,
        }
    try:
        atomic_write_bytes(target, after_bytes, mode=int(plan.get("mode", 0o600)), private=False)
        atomic_write_json(owner_path, owner)
    except (OSError, FastAgentsError):
        if refresh_owner and current_exists:
            atomic_write_bytes(target, current_content, mode=int(plan.get("mode", 0o600)), private=False)
            if prior_owner_content is not None:
                atomic_write_bytes(owner_path, prior_owner_content)
        elif backup_path:
            shutil.copy2(backup_path, target)
        else:
            target.unlink(missing_ok=True)
        raise
    if refresh_owner:
        preserved_backup = owner.get("backup") or "none; the original setup created the instruction file"
        result = _print_instruction_preview(target, plan["before_sha256"], plan["after_sha256"])
        result.extend([f"Preserved rollback baseline backup: {preserved_backup}", "Refreshed only the displayed managed block."])
        return result
    return _print_instruction_preview(target, plan["before_sha256"], plan["after_sha256"]) + [
        f"Backup: {backup_path if backup_path else 'none; this command created the instruction file'}",
        "Applied only the displayed managed block.",
    ]


def rollback_instruction(target_arg: Path | None, apply: bool = False) -> list[str]:
    if target_arg is None:
        requested_path = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")).expanduser()
    else:
        requested_path = target_arg.expanduser()
    explicit_target: Path | None = None
    if requested_path.name in {"AGENTS.md", "AGENTS.override.md"}:
        if requested_path.is_symlink():
            raise FastAgentsError("instruction target is a symlink; select its actual Codex home file explicitly")
        directory = requested_path.parent.resolve()
        explicit_target = directory / requested_path.name
    else:
        directory = requested_path.resolve()
    owner_path = directory / INSTRUCTION_OWNER
    if not owner_path.is_file():
        raise FastAgentsError(f"no owned instruction record found for {directory}")
    owner = read_json(owner_path)
    recorded_target = owner.get("target")
    if owner.get("owner") != "hawary-fast-agents-instructions" or not isinstance(recorded_target, str):
        raise FastAgentsError("instruction rollback ownership could not be verified")
    target = Path(recorded_target)
    if (
        not target.is_absolute()
        or target.parent != directory
        or target.name not in {"AGENTS.md", "AGENTS.override.md"}
        or (explicit_target is not None and target != explicit_target)
        or target.is_symlink()
    ):
        raise FastAgentsError("instruction rollback ownership could not be verified")
    if not target.is_file() or sha256_file(target) != owner.get("after_sha256"):
        raise FastAgentsError("instruction file changed after setup; rollback refused to overwrite modified instructions")
    _validate_owned_instruction(target, target.read_bytes(), owner)
    backup = Path(owner["backup"]) if owner.get("backup") else None
    messages = [f"Instruction file: {target}", "Rollback is limited to the unchanged file owned by this helper."]
    if not apply:
        return messages + ["Preview only. Rerun with --apply to roll back the unchanged managed setup."]
    if backup:
        shutil.copy2(backup, target)
    else:
        target.unlink()
    owner_path.unlink()
    return messages + [f"Rolled back the owned instruction setup; retained backup: {backup or 'none'}"]
