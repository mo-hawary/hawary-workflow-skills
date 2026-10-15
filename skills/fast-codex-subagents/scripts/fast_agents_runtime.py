"""Private task runtime, Codex supervisor, event reader, and safe monitor cleanup."""

from __future__ import annotations

import datetime as dt
import errno
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any, TextIO

from fast_agents_common import (
    FastAgentsError,
    Role,
    TaskManifest,
    atomic_write_bytes,
    atomic_write_json,
    load_manifest,
    read_json,
    read_bounded_dependency_context,
    sha256_bytes,
    SUPPORTED_APP_SERVER_REQUESTS,
)
from fast_agents_platform import codex_argv, detached_process_options, process_lock_kind, protect_private_path


SAFE_TASK_ID = re.compile(r"\A[0-9a-f-]{36}\Z")
KNOWN_ACTIVITY = {
    "thread.started": "session started",
    "turn.started": "turn started",
    "turn.completed": "turn completed",
    "turn.failed": "turn failed",
    "item.started": "work item started",
    "item.completed": "work item finished",
    "error": "reported an error",
}


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def default_runtime_root() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")).expanduser()
        return base / "fast-agents"
    state_home = os.environ.get("XDG_STATE_HOME")
    base = Path(state_home).expanduser() if state_home else Path.home() / ".local" / "state"
    return base / "fast-agents"


def private_mkdir(path: Path) -> None:
    missing: list[Path] = []
    current = path
    while not current.exists():
        missing.append(current)
        current = current.parent
    for directory in reversed(missing):
        directory.mkdir(mode=0o700)
        protect_private_path(directory, directory=True)
    protect_private_path(path, directory=True)


def task_path(runtime_root: Path, task_id: str) -> Path:
    if not SAFE_TASK_ID.fullmatch(task_id):
        raise FastAgentsError("task id must be a valid UUID")
    result = runtime_root / "tasks" / task_id
    if not result.is_dir():
        raise FastAgentsError(f"task not found: {task_id}")
    return result


def _iso_parse(value: str) -> dt.datetime | None:
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.timezone.utc)


def elapsed_label(started_at: str, now: dt.datetime | None = None) -> str:
    start = _iso_parse(started_at)
    if start is None:
        return "elapsed time unknown"
    seconds = max(0, int(((now or dt.datetime.now(dt.timezone.utc)) - start).total_seconds()))
    minutes, remainder = divmod(seconds, 60)
    if minutes >= 60:
        hours, minutes = divmod(minutes, 60)
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {remainder}s"
    return f"{remainder}s"


def _linux_process_identity(pid: int) -> dict[str, Any] | None:
    proc = Path("/proc") / str(pid)
    try:
        stat_text = (proc / "stat").read_text(encoding="utf-8")
        tail = stat_text[stat_text.rfind(")") + 2 :].split()
        start_ticks = tail[19]
        argv = [value.decode("utf-8", errors="replace") for value in (proc / "cmdline").read_bytes().split(b"\0") if value]
        executable = os.readlink(proc / "exe")
    except FileNotFoundError:
        return None
    except PermissionError:
        raise
    if not argv:
        return None
    command_bytes = json.dumps(argv, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return {
        "pid": pid,
        "start_token": start_ticks,
        "command_hash": hashlib.sha256(command_bytes).hexdigest(),
        "argv": argv,
        "executable": executable,
        "platform": "linux-proc",
    }


def _windows_process_identity(pid: int) -> dict[str, Any] | None:
    import ctypes
    from ctypes import wintypes

    class FILETIME(ctypes.Structure):
        _fields_ = [("low", wintypes.DWORD), ("high", wintypes.DWORD)]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    query_limited = 0x1000
    synchronize = 0x00100000
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetProcessTimes.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(FILETIME),
        ctypes.POINTER(FILETIME),
        ctypes.POINTER(FILETIME),
        ctypes.POINTER(FILETIME),
    ]
    kernel32.GetProcessTimes.restype = wintypes.BOOL
    kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    handle = kernel32.OpenProcess(query_limited | synchronize, False, pid)
    if not handle:
        error = ctypes.get_last_error()
        if error in {87, 1168}:
            return None
        if error == 5:
            raise PermissionError(error, "OpenProcess access denied")
        raise OSError(error, "OpenProcess failed")

    def process_exited() -> bool:
        result = kernel32.WaitForSingleObject(handle, 0)
        if result == 0:  # WAIT_OBJECT_0: the process object is signaled after exit.
            return True
        if result == 0x102:  # WAIT_TIMEOUT: the process is still nonsignaled.
            return False
        error = ctypes.get_last_error()
        if error == 5:
            raise PermissionError(error, "process wait access denied")
        raise OSError(error or errno.EIO, "process exit state could not be established")

    try:
        # An exited process can retain a handle while its executable image is gone.
        # Prove exit before requiring image metadata, and recheck metadata failures
        # for an exit that raced with these queries. Unknown failures stay closed.
        if process_exited():
            return None
        creation = FILETIME()
        exit_time = FILETIME()
        kernel_time = FILETIME()
        user_time = FILETIME()
        if not kernel32.GetProcessTimes(handle, ctypes.byref(creation), ctypes.byref(exit_time), ctypes.byref(kernel_time), ctypes.byref(user_time)):
            error = ctypes.get_last_error()
            if process_exited():
                return None
            if error == 5:
                raise PermissionError(error, "GetProcessTimes access denied")
            raise OSError(error, "GetProcessTimes failed")
        buffer = ctypes.create_unicode_buffer(32768)
        size = wintypes.DWORD(len(buffer))
        if not kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            error = ctypes.get_last_error()
            if process_exited():
                return None
            if error == 5:
                raise PermissionError(error, "QueryFullProcessImageName access denied")
            raise OSError(error, "QueryFullProcessImageName failed")
        signaled = process_exited()
        start_token = (creation.high << 32) | creation.low
        return {
            "pid": pid,
            "start_token": str(start_token),
            "command_hash": hashlib.sha256(buffer.value.casefold().encode("utf-8")).hexdigest(),
            "argv": [buffer.value],
            "executable": buffer.value,
            "platform": "windows-process-times",
            "exited": signaled,
        }
    finally:
        kernel32.CloseHandle(handle)


def _macos_process_identity(pid: int) -> dict[str, Any] | None:
    import ctypes
    import ctypes.util

    class BSDInfo(ctypes.Structure):
        _fields_ = [
            ("flags", ctypes.c_uint32),
            ("status", ctypes.c_uint32),
            ("xstatus", ctypes.c_uint32),
            ("pid", ctypes.c_uint32),
            ("ppid", ctypes.c_uint32),
            ("uid", ctypes.c_uint32),
            ("gid", ctypes.c_uint32),
            ("ruid", ctypes.c_uint32),
            ("rgid", ctypes.c_uint32),
            ("svuid", ctypes.c_uint32),
            ("svgid", ctypes.c_uint32),
            ("reserved", ctypes.c_uint32),
            ("command", ctypes.c_char * 16),
            ("name", ctypes.c_char * 32),
            ("open_files", ctypes.c_uint32),
            ("process_group", ctypes.c_uint32),
            ("job_count", ctypes.c_uint32),
            ("tty_device", ctypes.c_uint32),
            ("tty_process_group", ctypes.c_uint32),
            ("nice", ctypes.c_int32),
            ("start_seconds", ctypes.c_uint64),
            ("start_microseconds", ctypes.c_uint64),
        ]

    library_name = ctypes.util.find_library("proc")
    if not library_name:
        raise OSError("macOS libproc is unavailable; process identity cannot be established")
    library = ctypes.CDLL(library_name, use_errno=True)
    get_info = library.proc_pidinfo
    get_info.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_uint64, ctypes.c_void_p, ctypes.c_int]
    get_info.restype = ctypes.c_int
    info = BSDInfo()
    size = get_info(pid, 3, 0, ctypes.byref(info), ctypes.sizeof(info))
    if size != ctypes.sizeof(info):
        error = ctypes.get_errno()
        if error == errno.ESRCH or size == 0 and error == 0:
            return None
        if error == errno.EPERM or error == errno.EACCES:
            raise PermissionError(error, "process identity access denied")
        raise OSError(error or errno.EIO, "macOS process identity could not be read")

    get_path = library.proc_pidpath
    get_path.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
    get_path.restype = ctypes.c_int
    executable_buffer = ctypes.create_string_buffer(4096)
    path_size = get_path(pid, executable_buffer, ctypes.sizeof(executable_buffer))
    if path_size <= 0:
        error = ctypes.get_errno()
        if error == errno.ESRCH:
            return None
        if error == errno.EPERM or error == errno.EACCES:
            raise PermissionError(error, "process executable identity access denied")
        raise OSError(error or errno.EIO, "macOS process executable identity could not be read")
    executable = executable_buffer.value.decode("utf-8", errors="replace")
    command = info.command.decode("utf-8", errors="replace")
    name = info.name.decode("utf-8", errors="replace")
    command_bytes = "\0".join((executable, command, name)).encode("utf-8")
    return {
        "pid": pid,
        "start_token": f"{info.start_seconds}:{info.start_microseconds}",
        "command_hash": hashlib.sha256(command_bytes).hexdigest(),
        "argv": [name or command],
        "executable": executable,
        "platform": "macos-libproc",
    }


def capture_process_identity(pid: int) -> dict[str, Any] | None:
    if os.name == "nt":
        return _windows_process_identity(pid)
    if Path("/proc").is_dir():
        return _linux_process_identity(pid)
    if sys.platform == "darwin":
        return _macos_process_identity(pid)
    raise OSError("this platform does not provide a supported process identity API")


def inspect_process(expected: dict[str, Any] | None) -> tuple[str, dict[str, Any] | None]:
    if not isinstance(expected, dict) or not isinstance(expected.get("pid"), int):
        return "unknown", None
    pid = expected["pid"]
    if os.name != "nt":
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return "dead", None
        except PermissionError:
            return "permission_denied", None
        except OSError as error:
            if error.errno == errno.ESRCH:
                return "dead", None
            if error.errno == errno.EPERM:
                return "permission_denied", None
            return "unknown", None
    try:
        actual = capture_process_identity(pid)
    except PermissionError:
        return "permission_denied", None
    except OSError:
        return "unknown", None
    if actual is None:
        return "dead", None
    if actual.get("exited"):
        return "dead", actual
    # exec() may replace the image after Popen returns (for example the env shebang).
    # PID plus the kernel creation token identifies the process instance across that handoff.
    same_process = actual.get("pid") == expected.get("pid") and str(actual.get("start_token")) == str(
        expected.get("start_token")
    )
    return ("alive", actual) if same_process else ("stale_identity", actual)


def _private_task_manifest(manifest: TaskManifest) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "workspace": str(manifest.workspace),
        "objective": manifest.objective,
        "shared_contract": manifest.shared_contract,
        "fast_requested": manifest.fast_requested,
        "fast_selection": manifest.fast_selection,
        "backend": manifest.backend,
        "max_concurrency": manifest.max_concurrency,
        "plan_mode": manifest.plan_mode,
        "budgets": manifest.budgets,
        "isolation": manifest.isolation,
        "acceptance": [list(command) for command in manifest.acceptance],
        "roles": [
            {
                "name": role.name,
                "access": role.access,
                "model": role.model,
                "effort": role.effort,
                "outcome": role.outcome,
                "owns": list(role.owns),
                "constraints": list(role.constraints),
                "depends_on": list(role.depends_on),
                "prompt": role.prompt,
                "fast_requested": role.fast_requested,
                "setting_sources": role.setting_sources,
                "budgets": role.budgets,
            }
            for role in manifest.roles
        ],
    }


def _git_root_containing(path: Path) -> Path | None:
    candidate = path.expanduser().resolve(strict=False)
    for parent in (candidate, *candidate.parents):
        marker = parent / ".git"
        if marker.is_dir() or marker.is_file() or marker.is_symlink():
            return parent
    return None


def _require_outside_git_repositories(path: Path, label: str) -> Path:
    resolved = path.expanduser().resolve(strict=False)
    git_root = _git_root_containing(resolved)
    if git_root is not None:
        raise FastAgentsError(f"{label} must be outside every Git repository; found repository root {git_root}")
    return resolved


def _require_outside_workspace(path: Path, workspace: Path) -> Path:
    resolved = path.expanduser().resolve(strict=False)
    canonical_workspace = workspace.expanduser().resolve(strict=False)
    if resolved == canonical_workspace or canonical_workspace in resolved.parents:
        raise FastAgentsError("task runtime must be outside the selected workspace")
    return resolved


def _initial_status(task_id: str, manifest: TaskManifest) -> dict[str, Any]:
    fast_status: dict[str, Any] = {
        "choice": "confirmed" if manifest.fast_requested else "rejected",
        "requested": manifest.fast_requested,
        "runtime": "unverified" if manifest.fast_requested else "not_requested",
        "evidence": None,
    }
    if manifest.backend == "app-server":
        fast_status["choice"] = "requested" if manifest.fast_requested else "not_requested"
        fast_status["workers"] = {
            role.name: {
                "requested": role.fast_requested,
                "requested_tier": "priority" if role.fast_requested else "default",
                "effective_tier": None,
                "effective": "unverified",
                "upstream": "unverified",
            }
            for role in manifest.roles
        }
    return {
        "schema_version": 1,
        "task_id": task_id,
        "workspace": str(manifest.workspace),
        "objective": manifest.objective,
        "created_at": utc_now(),
        "updated_at": utc_now(),
        "state": "queued",
        "fast": fast_status,
        "backend": manifest.backend,
        "server": {"pid": None, "identity": None, "state": "not_started", "exit_code": None},
        "roles": {
            role.name: {
                "access": role.access,
                "model": role.model,
                "effort": role.effort,
                "state": "waiting",
                "started_at": None,
                "finished_at": None,
                "pid": None,
                "identity": None,
                "exit_code": None,
                "final_report_available": False,
                "last_activity": None,
                "error": None,
                "fast": {
                    "requested": role.fast_requested,
                    "requested_tier": "priority" if role.fast_requested else "default",
                    "effective_tier": None,
                    "effective": "unverified",
                    "upstream": "unverified",
                },
                "thread_start_state": "not_submitted",
                "turn_start_state": "not_submitted",
                "thread_id": None,
                "turn_id": None,
                "usage": None,
            }
            for role in manifest.roles
        },
        "manager": {"pid": None, "identity": None, "state": "starting"},
        "action": "",
    }


def build_codex_argv(
    codex_executable: str,
    workspace: Path,
    role: Role,
    fast_requested: bool,
    task_dir: Path,
) -> list[str]:
    agent_dir = task_dir / "agents" / role.name
    arguments = [
        "exec",
        "-C",
        str(workspace),
        "-m",
        role.model,
        "-c",
        f'model_reasoning_effort="{role.effort}"',
    ]
    if role.fast_requested:
        arguments.extend(["-c", 'service_tier="fast"', "--enable", "fast_mode"])
    else:
        # Omitting overrides would inherit Fast from user or project configuration.
        arguments.extend(["-c", 'service_tier="default"', "-c", "features.fast_mode=false"])
    if role.access == "reviewer":
        arguments.extend(["-s", "read-only"])
    else:
        arguments.append("--approve-for-me")
    arguments.extend(
        [
            "--json",
            "--color",
            "never",
            "-o",
            str(agent_dir / "final.md"),
            "-",
        ]
    )
    return codex_argv(codex_executable, *arguments)


def _workspace_lock_paths(runtime_root: Path, workspace: Path) -> tuple[Path, Path]:
    del runtime_root  # Runtime overrides must not create a second writer-coordination domain.
    lock_dir = _require_outside_git_repositories(default_runtime_root() / "workspace-locks", "workspace lock directory")
    private_mkdir(lock_dir)
    canonical_workspace = workspace.expanduser().resolve(strict=False)
    lock_key = os.path.normcase(str(canonical_workspace)) if os.name == "nt" else str(canonical_workspace)
    digest = hashlib.sha256(lock_key.encode("utf-8")).hexdigest()
    return lock_dir / f"{digest}.lock", lock_dir / f"{digest}.json"


def _acquire_file_lock(path: Path, conflict_message: str) -> int:
    if path.is_symlink():
        raise FastAgentsError("private lock path must not be a symlink")
    flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    try:
        protect_private_path(path, directory=False)
        lock_kind = process_lock_kind(os.name)
        if lock_kind == "msvcrt":
            import msvcrt

            if os.fstat(descriptor).st_size == 0:
                os.write(descriptor, b"\0")
            os.lseek(descriptor, 0, os.SEEK_SET)
            try:
                msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
            except OSError as error:
                raise FastAgentsError(conflict_message) from error
        elif lock_kind == "fcntl":
            import fcntl

            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise FastAgentsError(conflict_message) from error
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _paths_overlap(first: Path, second: Path) -> bool:
    first_real = first.expanduser().resolve(strict=False)
    second_real = second.expanduser().resolve(strict=False)
    if os.name == "nt":
        first_real = Path(os.path.normcase(str(first_real)))
        second_real = Path(os.path.normcase(str(second_real)))
    try:
        second_real.relative_to(first_real)
        return True
    except ValueError:
        try:
            first_real.relative_to(second_real)
            return True
        except ValueError:
            return False


def _acquire_workspace_lock(path: Path, workspace: Path, record: dict[str, Any]) -> int:
    lock_dir = path.parent
    registry_descriptor = _acquire_file_lock(
        lock_dir / "registry.lock",
        "workspace writer coordination is busy; retry after refreshing task status",
    )
    descriptor: int | None = None
    canonical_workspace = workspace.expanduser().resolve(strict=False)
    record_path = path.with_suffix(".json")
    try:
        for other_record_path in sorted(lock_dir.glob("*.json")):
            if other_record_path == record_path:
                continue
            if other_record_path.is_symlink():
                raise FastAgentsError("workspace lock record must not be a symlink")
            old = read_json(other_record_path)
            old_workspace = old.get("workspace")
            if not isinstance(old_workspace, str) or not Path(old_workspace).is_absolute():
                raise FastAgentsError("workspace lock record has no canonical workspace; refusing a risky launch")
            if not _paths_overlap(canonical_workspace, Path(old_workspace)):
                continue
            other_lock = other_record_path.with_suffix(".lock")
            try:
                check_descriptor = _acquire_file_lock(other_lock, "overlapping workspace already has an active writer task")
            except FastAgentsError as error:
                raise FastAgentsError("an overlapping workspace already has an active writer task") from error
            try:
                _verify_old_lock_owner(other_record_path)
            finally:
                os.close(check_descriptor)

        descriptor = _acquire_file_lock(path, "workspace already has an active writer task")
        if record_path.is_symlink():
            raise FastAgentsError("workspace lock record must not be a symlink")
        if record_path.exists():
            _verify_old_lock_owner(record_path)
        record["workspace"] = str(canonical_workspace)
        record["manager_state"] = record.get("manager_state", "starting")
        _write_lock_record(record_path, record)
        return descriptor
    except BaseException:
        if descriptor is not None:
            os.close(descriptor)
        raise
    finally:
        os.close(registry_descriptor)


def _acquire_task_lock(path: Path) -> int:
    return _acquire_file_lock(path, "task already has an active supervisor")


def _verify_old_lock_owner(path: Path) -> None:
    if not path.exists():
        return
    try:
        old = read_json(path)
    except FastAgentsError as error:
        raise FastAgentsError("workspace lock record is unreadable; refusing a risky launch") from error
    manager_state = old.get("manager_state")
    known_manager_states = {"starting", "running", "recovery_required", "completed", "failed"}
    if manager_state not in known_manager_states:
        raise FastAgentsError("workspace lock manager state is unknown; refusing a risky launch")
    manager = old.get("manager")
    if not isinstance(manager, dict):
        raise FastAgentsError("previous supervisor identity is missing; refusing a risky launch")
    process_state, _ = inspect_process(manager)
    if process_state == "alive":
        raise FastAgentsError("previous task supervisor is still alive")
    if process_state not in {"dead", "stale_identity"}:
        raise FastAgentsError("cannot establish previous supervisor identity; refusing a risky launch")
    server_state = old.get("server_state")
    server = old.get("server")
    known_server_states = {None, "not_started", "starting", "running", "stopped", "lost"}
    if server_state not in known_server_states:
        raise FastAgentsError("previous app-server state is unknown; refusing a risky launch")
    if server_state in {"starting", "running", "lost"} and not isinstance(server, dict):
        raise FastAgentsError("previous app-server identity is missing; refusing a risky launch")
    if server is not None:
        if not isinstance(server, dict):
            raise FastAgentsError("previous app-server identity is invalid; refusing a risky launch")
        process_state, _ = inspect_process(server)
        if process_state == "alive":
            raise FastAgentsError("previous app-server is still alive and cannot be safely reattached")
        if process_state not in {"dead", "stale_identity"}:
            raise FastAgentsError("cannot establish previous app-server identity; refusing a risky launch")
    processes = old.get("processes", [])
    if not isinstance(processes, list):
        raise FastAgentsError("workspace lock process list is invalid; refusing a risky launch")
    known_process_states = {"starting", "running", "launch_error", "finished", "failed"}
    for process in processes:
        if not isinstance(process, dict):
            raise FastAgentsError("workspace lock has an invalid process record; refusing a risky launch")
        state = process.get("state")
        if state not in known_process_states:
            raise FastAgentsError("workspace lock process state is unknown; refusing a risky launch")
        identity = process.get("identity")
        if state in {"starting", "running"} and not isinstance(identity, dict):
            raise FastAgentsError("previous writer identity is missing; refusing a risky launch")
        if identity is not None:
            if not isinstance(identity, dict):
                raise FastAgentsError("previous writer identity is invalid; refusing a risky launch")
            process_state, _ = inspect_process(identity)
            if process_state == "alive":
                raise FastAgentsError("a previous workspace writer is still alive")
            if process_state not in {"dead", "stale_identity"}:
                raise FastAgentsError("cannot establish previous writer identity; refusing a risky launch")


def _write_ready(task_dir: Path, value: dict[str, Any]) -> None:
    atomic_write_json(task_dir / "ready.json", value)


def launch_task(
    manifest: TaskManifest,
    runtime_root: Path,
    helper_script: Path,
    codex_executable: str,
    handshake_timeout: float = 15.0,
    enforce_plan_mode: bool = False,
) -> tuple[str, Path]:
    if (enforce_plan_mode or manifest.plan_mode) and manifest.has_writers:
        raise FastAgentsError("Plan Mode forbids writer roles; no worker was started")
    runtime_root = _require_outside_workspace(runtime_root, manifest.workspace)
    runtime_root = _require_outside_git_repositories(runtime_root, "task runtime")
    task_id = str(uuid.uuid4())
    task_dir = runtime_root / "tasks" / task_id
    private_mkdir(task_dir)
    private_mkdir(task_dir / "agents")
    private_mkdir(task_dir / "prompts")
    private_mkdir(task_dir / "monitors")
    private_manifest = _private_task_manifest(manifest)
    private_manifest["plan_mode"] = bool(enforce_plan_mode or manifest.plan_mode)
    private_manifest["codex_executable"] = codex_executable
    atomic_write_json(task_dir / "manifest.json", private_manifest)
    status = _initial_status(task_id, manifest)
    from fast_agents_verification import snapshot_workspace

    status["verification_state"] = "not_run"
    status["workspace_scope"] = {"state": "not_checked"}
    status["workspace_baseline"] = (
        snapshot_workspace(manifest.workspace) if manifest.has_writers
        else {"state": "not_applicable"}
    )
    atomic_write_json(task_dir / "status.json", status)

    manager_log = (task_dir / "manager.log").open("ab")
    manager_error = (task_dir / "manager.stderr.log").open("ab")
    protect_private_path(task_dir / "manager.log", directory=False)
    protect_private_path(task_dir / "manager.stderr.log", directory=False)
    argv = [
        sys.executable,
        str(helper_script.resolve()),
        "_supervise",
        "--task",
        task_id,
        "--runtime-root",
        str(runtime_root),
        "--codex",
        codex_executable,
    ]
    creation_kwargs = detached_process_options(os.name)
    try:
        process = subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=manager_log,
            stderr=manager_error,
            cwd=manifest.workspace,
            shell=False,
            close_fds=True,
            **creation_kwargs,
        )
    finally:
        manager_log.close()
        manager_error.close()

    deadline = time.monotonic() + handshake_timeout
    ready_path = task_dir / "ready.json"
    while time.monotonic() < deadline:
        if ready_path.exists():
            ready = read_json(ready_path)
            if not ready.get("ok"):
                raise FastAgentsError(str(ready.get("error", "supervisor could not start")))
            return task_id, task_dir
        if process.poll() is not None:
            break
        time.sleep(0.05)
    if ready_path.exists():
        ready = read_json(ready_path)
        if not ready.get("ok"):
            raise FastAgentsError(str(ready.get("error", "supervisor could not start")))
        return task_id, task_dir
    raise FastAgentsError(
        f"supervisor startup is unverified for task {task_id}; inspect its private runtime status before relaunching"
    )


def _role_prompt(manifest: dict[str, Any], role: dict[str, Any], dependency_context: str) -> str:
    sections = [
        f"Task objective: {manifest['objective']}",
        f"Shared interface contract (must follow):\n{manifest['shared_contract']}",
        f"Your role: {role['name']} ({role['access']}).",
        f"Expected outcome: {role['outcome']}",
    ]
    owns = role.get("owns", [])
    if owns:
        sections.append("Files this writer owns (do not edit other files):\n" + "\n".join(f"- {path}" for path in owns))
    if role.get("constraints"):
        sections.append("Constraints:\n" + "\n".join(f"- {value}" for value in role["constraints"]))
    if dependency_context:
        sections.append("Completed dependency reports:\n" + dependency_context)
    if role.get("prompt"):
        sections.append(f"Additional task detail:\n{role['prompt']}")
    sections.append(
        "Report what you changed or found, verification performed, remaining risks, and any blocked work. "
        "Respect repository instructions and the configured sandbox."
    )
    return "\n\n".join(sections) + "\n"


def _role_by_name(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {role["name"]: role for role in manifest["roles"]}


def _read_event_file(path: Path) -> tuple[list[dict[str, Any]], int]:
    events: list[dict[str, Any]] = []
    malformed = 0
    try:
        content = path.read_bytes()
    except FileNotFoundError:
        return events, malformed
    for raw_line in content.splitlines(keepends=True):
        if not raw_line.endswith(b"\n"):
            continue
        try:
            decoded = json.loads(raw_line)
        except (UnicodeDecodeError, json.JSONDecodeError):
            malformed += 1
            continue
        if isinstance(decoded, dict):
            events.append(decoded)
        else:
            malformed += 1
    return events, malformed


def _fast_runtime_result(events: list[dict[str, Any]], requested: bool) -> tuple[str, dict[str, Any] | None]:
    session_ids: set[str] = set()
    for event in events:
        if event.get("type") in {"thread.started", "session_meta"}:
            session_id = event.get("thread_id") or event.get("session_id")
            if isinstance(session_id, str) and session_id:
                session_ids.add(session_id)
    for event in events:
        event_type = event.get("type")
        session_id = event.get("thread_id") or event.get("session_id")
        runtime = event.get("runtime_config")
        if event_type not in {"runtime_config", "session.config"} or not isinstance(runtime, dict):
            continue
        if not isinstance(session_id, str) or session_id not in session_ids or runtime.get("effective") is not True:
            continue
        service_tier = runtime.get("service_tier")
        fast_mode = runtime.get("fast_mode")
        evidence = {
            "event": event_type,
            "thread_id": session_id,
            "service_tier": service_tier,
            "fast_mode": fast_mode,
            "effective": True,
        }
        if not requested:
            if service_tier == "default":
                return "confirmed", evidence
            if isinstance(service_tier, str):
                return "rejected", evidence
            # A disabled feature alone does not prove the effective service route.
            continue
        if service_tier in {"fast", "priority"} and fast_mode is True:
            return "confirmed", evidence
        if service_tier is not None or fast_mode is False:
            return "rejected", evidence
    return "unverified", None


def _safe_activity(event: dict[str, Any]) -> str | None:
    event_type = event.get("type")
    return KNOWN_ACTIVITY.get(event_type) if isinstance(event_type, str) else None


def _event_progress(agent_dir: Path) -> dict[str, Any]:
    events, malformed = _read_event_file(agent_dir / "events.jsonl")
    latest = None
    latest_at = None
    for event in events:
        activity = _safe_activity(event)
        if activity:
            latest = activity
            timestamp = event.get("timestamp")
            if isinstance(timestamp, str) and _iso_parse(timestamp):
                latest_at = timestamp
    return {
        "events": events,
        "malformed": malformed,
        "latest": latest,
        "latest_at": latest_at,
        "valid_count": len(events),
    }


def _final_report_available(agent_dir: Path) -> bool:
    report = agent_dir / "final.md"
    try:
        return report.is_file() and report.stat().st_size > 0
    except OSError:
        return False


def _derive_role_state(role_state: dict[str, Any], agent_dir: Path) -> tuple[str, str | None]:
    if role_state.get("state") == "blocked":
        return "blocked", role_state.get("error") or "a dependency did not complete successfully"
    exit_path = agent_dir / "exit.json"
    if exit_path.exists():
        try:
            exit_data = read_json(exit_path)
        except FastAgentsError:
            return "unknown", "exit record is unreadable"
        code = exit_data.get("exit_code")
        if not isinstance(code, int):
            return "unknown", "exit code is missing"
        role_state["exit_code"] = code
        has_report = _final_report_available(agent_dir)
        role_state["final_report_available"] = has_report
        if code != 0:
            return "failed", f"Codex exited with code {code}"
        if not has_report:
            return "unknown", "Codex exited without a final report"
        return "completed", None

    identity = role_state.get("identity")
    if not identity:
        identity_error = role_state.get("identity_error")
        if isinstance(identity_error, str) and "permission denied" in identity_error.lower():
            return "permission_denied", identity_error
        return "unknown", identity_error or "process identity or exit evidence is missing"
    process_state, _ = inspect_process(identity)
    if process_state == "alive":
        return "running", None
    if process_state == "permission_denied":
        return "permission_denied", "permission denied while checking process identity"
    if process_state == "stale_identity":
        return "unknown", "PID now belongs to a different process; exit evidence is missing"
    if process_state == "dead":
        return "unknown", "process ended without an exit record or final report"
    return "unknown", "process identity could not be established"


def _pending_request_projection(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    result: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        request_id = item.get("request_id")
        kind = item.get("kind", item.get("method"))
        if (
            isinstance(request_id, bool)
            or not isinstance(request_id, (str, int))
            or not isinstance(kind, str)
            or kind not in SUPPORTED_APP_SERVER_REQUESTS
        ):
            continue
        result.append(
            {
                "request_id": request_id,
                "kind": kind,
                "action": "explicit reply required",
                "thread_id": item.get("thread_id") if isinstance(item.get("thread_id"), str) else None,
                "turn_id": item.get("turn_id") if isinstance(item.get("turn_id"), str) else None,
            }
        )
    return result


def read_task_status(task_dir: Path) -> dict[str, Any]:
    status = read_json(task_dir / "status.json")
    status["pending_requests"] = _pending_request_projection(status.get("pending_requests"))
    manifest = read_json(task_dir / "manifest.json")
    if manifest.get("backend", "exec") == "app-server":
        manager = status.get("manager", {})
        # Recovery decides supervisor liveness from identity_state, so inspect the recorded
        # identity whatever the saved manager state is (for example recovery_required after a
        # shutdown timeout). Only a live manager still in starting/running is reconciled below.
        manager_state, _ = inspect_process(manager.get("identity"))
        manager["identity_state"] = manager_state
        if manager.get("state") in {"starting", "running"} and manager_state in {"dead", "stale_identity"} and status.get("state") not in {"completed", "failed", "recovery_required"}:
            status["state"] = "recovery_required"
            status["action"] = "Supervisor identity ended; run resume --recover to reconcile persisted thread and turn ids before continuing."
        return status
    manager = status.get("manager", {})
    manager_active = False
    if manager.get("state") in {"starting", "running"}:
        manager_process_state, _ = inspect_process(manager.get("identity"))
        manager["identity_state"] = manager_process_state
        if manager_process_state == "alive":
            manager_active = True
        elif manager_process_state in {"dead", "stale_identity"}:
            manager["state"] = "stopped_unexpectedly"

    for role in manifest["roles"]:
        name = role["name"]
        role_state = status["roles"][name]
        agent_dir = task_dir / "agents" / name
        if role_state.get("state") == "waiting" and not role_state.get("pid"):
            if manager.get("state") == "failed":
                role_state["state"] = "blocked"
                role_state["error"] = status.get("action") or "supervisor could not start"
            elif manager_active:
                if str(role_state.get("error", "")).startswith("supervisor identity "):
                    role_state["error"] = None
                continue
            elif manager.get("state") in {"starting", "running"}:
                identity_state = manager.get("identity_state", "unknown")
                role_state["error"] = f"supervisor identity {identity_state}; role has not started"
            else:
                role_state["state"] = "unknown"
                role_state["error"] = "supervisor stopped before this role was launched"
            continue
        state, error = _derive_role_state(role_state, agent_dir)
        role_state["state"] = state
        if error:
            role_state["error"] = error
        elif role_state.get("error") in {
            "permission denied while checking process identity",
            "process identity could not be established",
            "process ended without an exit record or final report",
        }:
            role_state["error"] = None
        progress = _event_progress(agent_dir)
        role_state["last_activity"] = progress["latest"]
        role_state["malformed_event_lines"] = progress["malformed"]
        requested = bool(role.get("fast_requested", manifest.get("fast_requested", False)))
        fast_state, evidence = _fast_runtime_result(progress["events"], requested)
        effective_tier = evidence.get("service_tier") if evidence else None
        role_state.setdefault("fast", {}).update(
            {
                "requested": requested,
                "requested_tier": "priority" if requested else "default",
                "runtime": fast_state,
                "effective_tier": "priority" if effective_tier == "fast" else effective_tier,
                "effective": "matched" if fast_state == "confirmed" else "different" if fast_state == "rejected" else "unverified",
                "evidence": evidence,
                "upstream": "unverified",
            }
        )

    # A matching worker must not hide a different worker's rejected route.
    worker_fast = [role.get("fast", {}) for role in status["roles"].values()]
    rejected = next((item for item in worker_fast if item.get("runtime") == "rejected"), None)
    if rejected is not None:
        status["fast"].update({"runtime": "rejected", "evidence": rejected.get("evidence")})
    elif worker_fast and all(item.get("runtime") == "confirmed" for item in worker_fast):
        status["fast"].update({"runtime": "confirmed", "evidence": worker_fast[0].get("evidence")})
    else:
        # Preserve the legacy task request label, never as effective-tier proof.
        status["fast"].update({"runtime": "unverified" if status["fast"]["requested"] else "not_requested", "evidence": None})

    role_values = [value["state"] for value in status["roles"].values()]
    if role_values and all(state == "completed" for state in role_values):
        status["state"] = "completed"
        status["action"] = "Review the final reports and keep the private task artifacts as needed."
        # The worker exit records and non-empty final reports prove task completion even
        # if a reader observed the supervisor between its final status write and exit.
        if manager.get("state") == "stopped_unexpectedly":
            manager["state"] = "completed"
    elif any(state in {"failed", "blocked"} for state in role_values):
        status["state"] = "failed"
        status["action"] = "Review the actionable role error and final report before any explicit relaunch."
    elif any(state == "running" for state in role_values) or manager_active and any(
        state == "waiting" for state in role_values
    ):
        status["state"] = "running"
        if manager.get("state") == "stopped_unexpectedly":
            status["action"] = "Supervisor stopped unexpectedly; workers may still run. Watch existing logs and prove they exited before relaunch."
        else:
            status["action"] = "Use watch to follow current processes; permission-denied identity checks need manual inspection."
    else:
        status["state"] = "unknown"
        status["action"] = "Resume to inspect existing state; relaunch only after proving no writer remains."
    # This is a derived view. Only the supervisor writes durable status snapshots,
    # so a concurrent read cannot overwrite its newer manager/role state.
    return status


def _format_age(timestamp: str | None) -> str:
    if not timestamp:
        return "no activity recorded"
    return f"last activity {elapsed_label(timestamp)} ago"


def render_status(task_dir: Path, status: dict[str, Any] | None = None) -> str:
    data = status or read_task_status(task_dir)
    lines = [
        f"Task {data['task_id']} · {data['state']} · elapsed {elapsed_label(data['created_at'])}",
        f"Workspace: {data['workspace']}",
        f"Supervisor: {data['manager'].get('state')} (identity {data['manager'].get('identity_state', 'not checked')})",
        f"Fast: {'requested' if data['fast']['requested'] else 'not requested'} · runtime {data['fast']['runtime']}",
    ]
    for name, role in data["roles"].items():
        detail = f"{name}: {role['state']} · {role['model']} ({role['effort']})"
        if role.get("last_activity"):
            detail += f" · {role['last_activity']}"
        if role.get("error"):
            detail += f" · {role['error']}"
        if role.get("state") == "running":
            detail += f" · {_format_age(role.get('started_at'))}"
        fast = role.get("fast", {})
        if data.get("backend") == "app-server" and fast:
            detail += f" · tier {fast.get('requested_tier', '?')} / {fast.get('effective_tier') or 'unverified'}"
        if role.get("thread_id"):
            detail += f" · thread {role['thread_id']} / turn {role.get('turn_id') or 'pending'}"
        lines.append(detail)
    pending = data.get("pending_requests") or []
    if pending:
        lines.append(f"Pending operator actions: {len(pending)}; inspect current request IDs with control status.")
    if "workspace_scope" in data:
        scope = data["workspace_scope"]
        lines.append(
            f"Verification: tests {data.get('verification_state', 'not_run')} · "
            f"workspace scope {scope.get('state', 'not_checked')}"
        )
        if scope.get("outside_declared_count"):
            lines.append(f"Review {scope['outside_declared_count']} newly dirty path(s) outside writer ownership.")
    if data.get("action"):
        lines.append(f"Next: {data['action']}")
    return "\n".join(lines)


def _store_manager_state(task_dir: Path, status: dict[str, Any], manager_state: str) -> None:
    status["manager"]["state"] = manager_state
    status["updated_at"] = utc_now()
    atomic_write_json(task_dir / "status.json", status)


def _write_lock_record(path: Path, data: dict[str, Any]) -> None:
    atomic_write_json(path, data)


def _launch_role(
    task_dir: Path,
    manifest: dict[str, Any],
    status: dict[str, Any],
    role: dict[str, Any],
    dependency_context: str,
    codex_executable: str,
    lock_path: Path | None,
    lock_record_path: Path | None,
    lock_record: dict[str, Any] | None,
) -> subprocess.Popen[bytes]:
    name = role["name"]
    agent_dir = task_dir / "agents" / name
    private_mkdir(agent_dir)
    prompt = _role_prompt(manifest, role, dependency_context)
    prompt_path = task_dir / "prompts" / f"{name}.txt"
    atomic_write_bytes(prompt_path, prompt.encode("utf-8"))

    if lock_record is not None:
        lock_record["processes"].append(
            {"role": name, "state": "starting", "pid": None, "identity": None, "access": role["access"]}
        )
        _write_lock_record(lock_record_path, lock_record)

    args = build_codex_argv(
        codex_executable,
        Path(manifest["workspace"]),
        Role(
            name=role["name"],
            access=role["access"],
            model=role["model"],
            effort=role["effort"],
            outcome=role["outcome"],
            owns=tuple(role["owns"]),
            constraints=tuple(role["constraints"]),
            depends_on=tuple(role["depends_on"]),
            prompt=role["prompt"],
            fast_requested=bool(role.get("fast_requested", manifest.get("fast_requested", False))),
            setting_sources=role.get("setting_sources", {"model": "explicit", "effort": "explicit", "fast": "explicit"}),
        ),
        bool(role.get("fast_requested", manifest.get("fast_requested", False))),
        task_dir,
    )
    event_stream = (agent_dir / "events.jsonl").open("wb")
    stderr_stream = (agent_dir / "stderr.log").open("wb")
    try:
        protect_private_path(agent_dir / "events.jsonl", directory=False)
        protect_private_path(agent_dir / "stderr.log", directory=False)
    except BaseException:
        event_stream.close()
        stderr_stream.close()
        raise
    prompt_stream = prompt_path.open("rb")
    env = os.environ.copy()
    env["FAST_AGENTS_TASK_ID"] = status["task_id"]
    env["FAST_AGENTS_ROLE"] = name
    try:
        process = subprocess.Popen(
            args,
            cwd=manifest["workspace"],
            stdin=prompt_stream,
            stdout=event_stream,
            stderr=stderr_stream,
            shell=False,
            close_fds=True,
            env=env,
        )
    except OSError as error:
        event_stream.close()
        stderr_stream.close()
        prompt_stream.close()
        if lock_record is not None:
            lock_record["processes"][-1]["state"] = "launch_error"
            _write_lock_record(lock_record_path, lock_record)
        exit_record = {"finished_at": utc_now(), "exit_code": 127, "launch_error": str(error)}
        atomic_write_json(agent_dir / "exit.json", exit_record)
        status["roles"][name].update(
            {"state": "failed", "finished_at": exit_record["finished_at"], "exit_code": 127, "error": f"could not start Codex CLI: {error}"}
        )
        atomic_write_json(task_dir / "status.json", status)
        raise
    event_stream.close()
    stderr_stream.close()
    prompt_stream.close()

    identity_error = None
    try:
        identity = capture_process_identity(process.pid)
    except PermissionError:
        identity = None
        identity_error = "permission denied while establishing process identity"
    except OSError as error:
        identity = None
        identity_error = f"process identity could not be established: {error}"
    process_record = {
        "role": name,
        "state": "running",
        "pid": process.pid,
        "identity": identity,
        "identity_error": identity_error,
        "access": role["access"],
    }
    if lock_record is not None:
        lock_record["processes"][-1] = process_record
        _write_lock_record(lock_record_path, lock_record)
    status["roles"][name].update(
        {
            # The scheduler owns the Popen handle and settles this role from its
            # real exit, so a missing identity must not block dependents early.
            "state": "running",
            "started_at": utc_now(),
            "pid": process.pid,
            "identity": identity,
            "identity_error": identity_error,
            "error": identity_error,
        }
    )
    status["updated_at"] = utc_now()
    atomic_write_json(task_dir / "status.json", status)
    return process


def supervise(task_dir: Path, runtime_root: Path, codex_executable: str, *, recover: bool = False) -> int:
    manifest = read_json(task_dir / "manifest.json")
    if manifest.get("backend", "exec") == "app-server":
        from fast_agents_app_server import supervise_app_server

        return supervise_app_server(task_dir, runtime_root, codex_executable, recover=recover)
    if recover:
        raise FastAgentsError("exec fallback cannot resume or steer a prior turn; inspect the existing task before a new explicit launch")
    status = read_json(task_dir / "status.json")
    has_writers = any(role["access"] == "writer" for role in manifest["roles"])
    lock_descriptor: int | None = None
    lock_path: Path | None = None
    lock_record_path: Path | None = None
    lock_record: dict[str, Any] | None = None
    try:
        manager_identity = capture_process_identity(os.getpid())
    except (OSError, PermissionError) as error:
        manager_identity = None
        status["state"] = "failed"
        status["action"] = f"supervisor process identity could not be established: {error}"
        atomic_write_json(task_dir / "status.json", status)
        _write_ready(task_dir, {"ok": False, "error": status["action"]})
        return 2
    if manager_identity is None:
        status["state"] = "failed"
        status["action"] = "supervisor process identity could not be established"
        atomic_write_json(task_dir / "status.json", status)
        _write_ready(task_dir, {"ok": False, "error": status["action"]})
        return 2
    try:
        if has_writers:
            lock_path, lock_record_path = _workspace_lock_paths(runtime_root, Path(manifest["workspace"]))
            lock_record = {
                "task_id": status["task_id"],
                "workspace": manifest["workspace"],
                "manager_state": "running",
                "manager": manager_identity,
                "processes": [],
                "updated_at": utc_now(),
            }
            lock_descriptor = _acquire_workspace_lock(lock_path, Path(manifest["workspace"]), lock_record)

        status["manager"] = {"pid": os.getpid(), "identity": manager_identity, "state": "running"}
        status["state"] = "running"
        status["updated_at"] = utc_now()
        atomic_write_json(task_dir / "status.json", status)
        _write_ready(task_dir, {"ok": True, "task_id": status["task_id"], "manager_pid": os.getpid()})
    except Exception as error:
        status["state"] = "failed"
        status["action"] = str(error)
        status["updated_at"] = utc_now()
        atomic_write_json(task_dir / "status.json", status)
        _write_ready(task_dir, {"ok": False, "error": str(error)})
        if lock_descriptor is not None:
            os.close(lock_descriptor)
        return 2

    roles = _role_by_name(manifest)
    max_concurrency = manifest.get("max_concurrency")
    if not isinstance(max_concurrency, int) or isinstance(max_concurrency, bool) or max_concurrency < 1:
        max_concurrency = min(3, max(1, len(roles)))
    processes: dict[str, subprocess.Popen[bytes]] = {}
    role_map = {role["name"]: role for role in manifest["roles"]}
    failed_launch = False

    while True:
        progress = False
        # Start every role whose dependency stage has completed successfully.
        for name, role in role_map.items():
            if status["roles"][name]["state"] != "waiting":
                continue
            dependency_states = [status["roles"][dependency]["state"] for dependency in role["depends_on"]]
            if any(state in {"failed", "blocked", "unknown", "permission_denied"} for state in dependency_states):
                status["roles"][name].update({"state": "blocked", "error": "a dependency did not complete successfully"})
                progress = True
                continue
            if not all(state == "completed" for state in dependency_states):
                continue
            if len(processes) >= max_concurrency:
                break
            dependency_context = read_bounded_dependency_context(
                [
                    (dependency, task_dir / "agents" / dependency / "final.md")
                    for dependency in role["depends_on"]
                ]
            )
            try:
                processes[name] = _launch_role(
                    task_dir,
                    manifest,
                    status,
                    role,
                    dependency_context,
                    codex_executable,
                    lock_path,
                    lock_record_path,
                    lock_record,
                )
            except OSError:
                failed_launch = True
            progress = True

        for name, process in list(processes.items()):
            exit_code = process.poll()
            if exit_code is None:
                continue
            finished_at = utc_now()
            agent_dir = task_dir / "agents" / name
            final_available = _final_report_available(agent_dir)
            atomic_write_json(
                agent_dir / "exit.json",
                {"finished_at": finished_at, "exit_code": exit_code, "final_report_available": final_available},
            )
            role_state = status["roles"][name]
            if exit_code != 0:
                role_state.update({"state": "failed", "error": f"Codex exited with code {exit_code}"})
            elif not final_available:
                role_state.update({"state": "unknown", "error": "Codex exited without a final report"})
            else:
                role_state.update({"state": "completed", "error": None})
            role_state.update(
                {"finished_at": finished_at, "exit_code": exit_code, "final_report_available": final_available}
            )
            del processes[name]
            if lock_record is not None:
                for item in lock_record["processes"]:
                    if item.get("role") == name and item.get("state") in {"starting", "running"}:
                        item["state"] = "finished"
                        item["exit_code"] = exit_code
                lock_record["updated_at"] = finished_at
                _write_lock_record(lock_record_path, lock_record)
            progress = True

        status["updated_at"] = utc_now()
        atomic_write_json(task_dir / "status.json", status)

        all_terminal = all(
            status["roles"][name]["state"] in {"completed", "failed", "blocked", "unknown"}
            for name in roles
        )
        if all_terminal and not processes:
            break
        if failed_launch and not processes:
            for role_name, role_state in status["roles"].items():
                if role_state["state"] == "waiting":
                    role_state.update({"state": "blocked", "error": "a required Codex process could not start"})
            break
        if not progress:
            time.sleep(0.2)

    states = [item["state"] for item in status["roles"].values()]
    if states and all(value == "completed" for value in states):
        status["state"] = "completed"
        status["action"] = "Review each final report and verify the shared contract."
        exit_result = 0
    elif any(value in {"failed", "blocked"} for value in states):
        status["state"] = "failed"
        status["action"] = "Review the failed or blocked role's exit record, stderr summary, and final report before relaunching."
        exit_result = 1
    else:
        status["state"] = "unknown"
        status["action"] = "One or more roles lack successful exit and final-report evidence; inspect status before relaunch."
        exit_result = 1
    status["updated_at"] = utc_now()
    status["manager"]["state"] = "completed"
    from fast_agents_verification import audit_workspace_scope

    status["workspace_scope"] = audit_workspace_scope(
        Path(manifest["workspace"]), status.get("workspace_baseline"), manifest["roles"]
    )
    status["verification_state"] = "not_run"
    atomic_write_json(task_dir / "status.json", status)
    if lock_record is not None:
        lock_record["manager_state"] = "completed"
        lock_record["updated_at"] = utc_now()
        _write_lock_record(lock_record_path, lock_record)
    if lock_descriptor is not None:
        os.close(lock_descriptor)
    return exit_result


def launch_from_path(
    manifest_path: Path,
    runtime_root: Path | None,
    helper_script: Path,
    codex_executable: str,
) -> tuple[str, Path, TaskManifest]:
    manifest = load_manifest(manifest_path)
    runtime = runtime_root or default_runtime_root()
    task_id, task_dir = launch_task(manifest, runtime, helper_script, codex_executable)
    return task_id, task_dir, manifest


def status_for_id(runtime_root: Path | None, task_id: str) -> tuple[Path, dict[str, Any]]:
    root = (runtime_root or default_runtime_root()).expanduser().resolve()
    task_dir = task_path(root, task_id)
    return task_dir, read_task_status(task_dir)


def resume_task(runtime_root: Path | None, task_id: str, continuous: bool = False, interval: float = 2.0) -> str:
    task_dir, status = status_for_id(runtime_root, task_id)
    output = render_status(task_dir, status)
    if continuous and status["state"] in {"running", "unknown"}:
        watch_task(task_dir, continuous=True, interval=interval)
    return output


def _incremental_events(path: Path, offset: int) -> tuple[int, list[dict[str, Any]], int]:
    try:
        with path.open("rb") as stream:
            stream.seek(offset)
            chunk = stream.read()
    except FileNotFoundError:
        return offset, [], 0
    complete = chunk.split(b"\n")
    trailing = complete.pop()
    consumed = len(chunk) - len(trailing)
    events: list[dict[str, Any]] = []
    malformed = 0
    for line in complete:
        if not line:
            continue
        try:
            event = json.loads(line)
        except (UnicodeDecodeError, json.JSONDecodeError):
            malformed += 1
            continue
        if isinstance(event, dict):
            events.append(event)
        else:
            malformed += 1
    return offset + consumed, events, malformed


def _monitor_record(task_dir: Path, task_id: str) -> Path:
    identity = capture_process_identity(os.getpid())
    record = {
        "task_id": task_id,
        "kind": "watch",
        "pid": os.getpid(),
        "identity": identity,
        "created_at": utc_now(),
        "argv": list(sys.argv),
    }
    monitor_path = task_dir / "monitors" / f"{os.getpid()}.json"
    private_mkdir(monitor_path.parent)
    atomic_write_json(monitor_path, record)
    return monitor_path


def _watch_signature(status: dict[str, Any]) -> tuple[Any, ...]:
    roles = tuple(
        (name, role["state"], role.get("last_activity"), role.get("error"))
        for name, role in status["roles"].items()
    )
    return status["state"], status["fast"]["runtime"], status["manager"].get("state"), roles


def _print_watch_summary(
    task_dir: Path,
    offsets: dict[str, int],
    malformed: dict[str, int],
    reported_malformed: dict[str, int],
    initial: bool,
    last_signature: tuple[Any, ...] | None,
) -> tuple[dict[str, Any], tuple[Any, ...]]:
    status = read_task_status(task_dir)
    signature = _watch_signature(status)
    if initial or signature != last_signature:
        print(render_status(task_dir, status), flush=True)
    manifest = read_json(task_dir / "manifest.json")
    for role in manifest["roles"]:
        name = role["name"]
        event_path = task_dir / "agents" / name / "events.jsonl"
        offset, events, bad = _incremental_events(event_path, offsets.get(name, 0))
        offsets[name] = offset
        malformed[name] = malformed.get(name, 0) + bad
        for event in events:
            activity = _safe_activity(event)
            if activity:
                print(f"{name}: {activity}", flush=True)
        if malformed.get(name, 0) > reported_malformed.get(name, 0):
            print(f"{name}: ignored {malformed[name]} malformed JSONL line(s)", flush=True)
            reported_malformed[name] = malformed[name]
        role_status = status["roles"][name]["state"]
        if (initial or signature != last_signature) and role_status in {"failed", "unknown", "permission_denied", "blocked"}:
            message = status["roles"][name].get("error") or role_status
            print(f"{name}: action needed: {message}", flush=True)
    if initial and status["fast"]["requested"] and status["fast"]["runtime"] == "unverified":
        print("Fast was requested; Codex runtime confirmation is unverified.", flush=True)
    return status, signature


def watch_task(task_dir: Path, continuous: bool = False, interval: float = 2.0) -> None:
    if interval <= 0:
        raise FastAgentsError("watch interval must be greater than zero")
    task_id = read_json(task_dir / "status.json")["task_id"]
    record_path = _monitor_record(task_dir, task_id)
    offsets: dict[str, int] = {}
    malformed: dict[str, int] = {}
    reported_malformed: dict[str, int] = {}
    can_set_signal = threading_main_thread()
    previous_term = None
    if can_set_signal:
        previous_term = signal.getsignal(signal.SIGTERM)
        signal.signal(signal.SIGTERM, lambda _signum, _frame: (_ for _ in ()).throw(KeyboardInterrupt()))
    try:
        first = True
        last_signature = None
        last_heartbeat = 0.0
        while True:
            status, signature = _print_watch_summary(
                task_dir,
                offsets,
                malformed,
                reported_malformed,
                initial=first,
                last_signature=last_signature,
            )
            if signature != last_signature:
                last_signature = signature
                last_heartbeat = time.monotonic()
            elif time.monotonic() - last_heartbeat >= 30:
                print(render_status(task_dir, status), flush=True)
                last_heartbeat = time.monotonic()
            first = False
            role_states = {role["state"] for role in status["roles"].values()}
            if not continuous or status["state"] in {"completed", "failed"} or (
                status["state"] == "unknown" and not role_states.intersection({"running", "waiting"})
            ):
                return
            time.sleep(interval)
    finally:
        if can_set_signal:
            signal.signal(signal.SIGTERM, previous_term)
        try:
            record = read_json(record_path)
            if record.get("pid") == os.getpid() and record.get("task_id") == task_id:
                record_path.unlink(missing_ok=True)
        except (FastAgentsError, OSError):
            pass


def threading_main_thread() -> bool:
    # Signal handlers can only be changed from the main thread.
    import threading

    return threading.current_thread() is threading.main_thread()


def _argv_is_owned_watch(record: dict[str, Any], task_id: str) -> bool:
    argv = record.get("argv")
    if not isinstance(argv, list) or not all(isinstance(item, str) for item in argv):
        return False
    # Monitors start either as `watch --task ID` or as `resume --task ID --watch`; both write kind "watch".
    watch_command = "watch" in argv or ("resume" in argv and "--watch" in argv)
    return watch_command and "--task" in argv and task_id in argv and record.get("kind") == "watch"


def cleanup_monitors(task_dir: Path, apply: bool = False) -> list[str]:
    task_id = read_json(task_dir / "status.json")["task_id"]
    monitor_dir = task_dir / "monitors"
    if not monitor_dir.exists():
        return ["No monitor processes are registered for this task."]
    messages: list[str] = []
    for record_path in sorted(monitor_dir.glob("*.json")):
        try:
            record = read_json(record_path)
        except FastAgentsError:
            messages.append(f"{record_path.name}: unreadable record; left unchanged")
            continue
        if record.get("task_id") != task_id or not _argv_is_owned_watch(record, task_id):
            messages.append(f"{record_path.name}: ownership could not be proved; left unchanged")
            continue
        state, _ = inspect_process(record.get("identity"))
        if state == "alive":
            if not apply:
                messages.append(f"{record_path.name}: owned watcher is active; rerun with --apply to stop only this watcher")
                continue
            pid = record["identity"]["pid"]
            # Recheck the same process identity immediately before signaling it.
            second_state, _ = inspect_process(record.get("identity"))
            if second_state != "alive":
                messages.append(f"{record_path.name}: watcher identity changed during cleanup; left unchanged")
                continue
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                messages.append(f"{record_path.name}: watcher already exited")
                continue
            except PermissionError:
                messages.append(f"{record_path.name}: permission denied; watcher left running")
                continue
            messages.append(f"{record_path.name}: stopped the registered watcher; worker processes were not signaled")
            continue
        if state in {"permission_denied", "unknown"}:
            messages.append(f"{record_path.name}: process identity is {state}; record left unchanged")
            continue
        if apply:
            record_path.unlink(missing_ok=True)
            messages.append(f"{record_path.name}: removed stale watcher record ({state})")
        else:
            messages.append(f"{record_path.name}: stale watcher record ({state}); rerun with --apply to remove it")
    return messages or ["No watcher cleanup was needed."]


def supervisor_main(task_id: str, runtime_root: Path, codex_executable: str, *, recover: bool = False) -> int:
    root = runtime_root.expanduser().resolve()
    task_dir = task_path(root, task_id)
    return supervise(task_dir, root, codex_executable, recover=recover)


def start_recovery_supervisor(
    task_id: str,
    runtime_root: Path,
    helper_script: Path,
    codex_executable: str,
    handshake_timeout: float = 15.0,
) -> Path:
    root = runtime_root.expanduser().resolve()
    task_dir = task_path(root, task_id)
    startup_lock = _acquire_file_lock(
        task_dir / "recovery-start.lock",
        "recovery startup is already in progress; refresh status before retrying",
    )
    try:
        return _start_recovery_supervisor_locked(task_id, root, helper_script, codex_executable, handshake_timeout)
    finally:
        os.close(startup_lock)


def _start_recovery_supervisor_locked(
    task_id: str,
    runtime_root: Path,
    helper_script: Path,
    codex_executable: str,
    handshake_timeout: float = 15.0,
) -> Path:
    root = runtime_root.expanduser().resolve()
    task_dir = task_path(root, task_id)
    manifest = read_json(task_dir / "manifest.json")
    if manifest.get("backend") != "app-server":
        raise FastAgentsError("only the app-server backend supports explicit resume recovery")
    status = read_json(task_dir / "status.json")
    manager_state, _ = inspect_process(status.get("manager", {}).get("identity"))
    if manager_state == "alive":
        raise FastAgentsError("supervisor is still alive; reconnect with resume instead of starting another")
    if manager_state in {"permission_denied", "unknown"}:
        raise FastAgentsError("cannot prove the previous supervisor stopped; recovery refused")
    server_identity = status.get("server", {}).get("identity")
    if server_identity:
        server_state, _ = inspect_process(server_identity)
        if server_state == "alive":
            raise FastAgentsError("previous app-server is still alive; recovery refused to create a second server")
        if server_state in {"permission_denied", "unknown"}:
            raise FastAgentsError("cannot prove the previous app-server stopped; recovery refused")
    elif status.get("server", {}).get("state") in {"starting", "running"}:
        raise FastAgentsError("previous app-server identity is missing; recovery refused")

    # Re-run the same pinned app-server preflight before starting a replacement
    # supervisor. The executable can have changed since the original task launch.
    from fast_agents import _codex_capabilities

    capabilities = _codex_capabilities(codex_executable, "app-server")
    if not capabilities.get("available"):
        details = capabilities.get("model_catalog_error") or capabilities.get("protocol", {}).get("error")
        raise FastAgentsError(
            f"recovery compatibility preflight failed for Codex {capabilities.get('version', 'unknown')}: "
            f"{details or 'pinned CLI version, schema, model catalog, or process identity is unavailable'}"
        )

    ready_path = task_dir / "ready.json"
    ready_path.unlink(missing_ok=True)
    manager_log = (task_dir / "manager.log").open("ab")
    manager_error = (task_dir / "manager.stderr.log").open("ab")
    protect_private_path(task_dir / "manager.log", directory=False)
    protect_private_path(task_dir / "manager.stderr.log", directory=False)
    argv = [
        sys.executable,
        str(helper_script.resolve()),
        "_supervise",
        "--task",
        task_id,
        "--runtime-root",
        str(root),
        "--codex",
        codex_executable,
        "--recover",
    ]
    try:
        process = subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=manager_log,
            stderr=manager_error,
            cwd=Path(manifest["workspace"]),
            shell=False,
            close_fds=True,
            **detached_process_options(os.name),
        )
    finally:
        manager_log.close()
        manager_error.close()
    deadline = time.monotonic() + handshake_timeout
    while time.monotonic() < deadline:
        if ready_path.exists():
            ready = read_json(ready_path)
            if not ready.get("ok"):
                raise FastAgentsError(str(ready.get("error", "recovery supervisor could not start")))
            return task_dir
        if process.poll() is not None:
            break
        time.sleep(0.05)
    raise FastAgentsError("recovery supervisor startup is unverified; inspect the private task runtime before retrying")
