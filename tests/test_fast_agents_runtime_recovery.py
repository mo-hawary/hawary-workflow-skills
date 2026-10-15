"""Regression coverage for recovery liveness and monitor ownership in the fast-agents runtime."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "fast-codex-subagents" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import fast_agents as cli
import fast_agents_runtime as runtime

TASK_ID = "12345678-1234-5678-1234-567812345678"


def exited_identity() -> dict:
    """Return the identity of a child process that has been reaped, so inspect_process reports dead."""
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        identity = runtime.capture_process_identity(child.pid)
        assert identity is not None
    finally:
        child.kill()
        child.wait(timeout=10)
    return identity


def make_app_server_task(root: Path, manager: dict, state: str = "recovery_required") -> Path:
    task_dir = root / "tasks" / TASK_ID
    task_dir.mkdir(parents=True)
    (task_dir / "manifest.json").write_text(
        json.dumps({"backend": "app-server", "workspace": str(root), "roles": []}), encoding="utf-8"
    )
    status = {
        "task_id": TASK_ID,
        "backend": "app-server",
        "state": state,
        "roles": {},
        "manager": manager,
        "server": {"pid": None, "identity": None, "state": "lost"},
        "pending_requests": [],
    }
    (task_dir / "status.json").write_text(json.dumps(status), encoding="utf-8")
    return task_dir


def test_recovery_required_manager_identity_is_inspected_after_exit(tmp_path: Path):
    identity = exited_identity()
    manager = {"pid": identity["pid"], "identity": identity, "state": "recovery_required"}
    task_dir = make_app_server_task(tmp_path / "runtime", manager)

    status = runtime.read_task_status(task_dir)

    assert status["manager"]["identity_state"] == "dead"
    assert status["state"] == "recovery_required"


def test_resume_recover_proceeds_when_recovery_required_supervisor_has_exited(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys):
    identity = exited_identity()
    manager = {"pid": identity["pid"], "identity": identity, "state": "recovery_required"}
    runtime_root = tmp_path / "runtime"
    make_app_server_task(runtime_root, manager)
    started: list[tuple] = []

    monkeypatch.setattr(cli, "require_codex_runtime_environment", lambda: None)
    monkeypatch.setattr(cli, "_recover_codex", lambda _task_dir: "codex")
    monkeypatch.setattr(cli, "start_recovery_supervisor", lambda *args: started.append(args))
    monkeypatch.setattr(cli, "send_control_request", lambda *_args: pytest.fail("no live supervisor to contact"))
    args = SimpleNamespace(
        runtime_root=str(runtime_root), task=TASK_ID, recover=True, codex=None, json=True, watch=False
    )

    assert cli._status(args, resume=True) == 0

    assert len(started) == 1
    assert started[0][0] == TASK_ID
    assert '"state": "recovery_required"' in capsys.readouterr().out


def write_monitor(task_dir: Path, argv: list[str], kind: str = "watch") -> Path:
    monitors = task_dir / "monitors"
    monitors.mkdir(parents=True, exist_ok=True)
    record_path = monitors / "4242.json"
    record = {
        "task_id": TASK_ID,
        "kind": kind,
        "pid": 4242,
        "identity": {"pid": 4242, "start_token": "1", "command_hash": "watch"},
        "argv": argv,
    }
    record_path.write_text(json.dumps(record), encoding="utf-8")
    return record_path


def make_monitor_task(root: Path) -> Path:
    task_dir = root / "tasks" / TASK_ID
    task_dir.mkdir(parents=True)
    (task_dir / "status.json").write_text(json.dumps({"task_id": TASK_ID}), encoding="utf-8")
    return task_dir


def test_cleanup_accepts_resume_watch_monitor_and_still_rejects_unrelated_argv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    task_dir = make_monitor_task(tmp_path / "runtime")
    monkeypatch.setattr(runtime, "inspect_process", lambda _identity: ("alive", None))
    monkeypatch.setattr(runtime.os, "kill", lambda *_args: pytest.fail("preview must not signal"))

    resume_argv = ["fast_agents.py", "resume", "--task", TASK_ID, "--runtime-root", "/tmp/x", "--watch"]
    write_monitor(task_dir, resume_argv)
    messages = runtime.cleanup_monitors(task_dir, apply=False)
    assert "owned watcher is active" in messages[0]
    assert "ownership could not be proved" not in messages[0]

    unrelated_cases = [
        ["fast_agents.py", "resume", "--task", TASK_ID],  # resume without --watch
        ["fast_agents.py", "_supervise", "--task", TASK_ID, "--watch"],  # not a resume or watch command
        ["fast_agents.py", "resume", "--watch"],  # --watch without --task or the task id
    ]
    for argv in unrelated_cases:
        write_monitor(task_dir, argv)
        messages = runtime.cleanup_monitors(task_dir, apply=True)
        assert "ownership could not be proved" in messages[0], argv
        assert (task_dir / "monitors" / "4242.json").exists()

    write_monitor(task_dir, resume_argv, kind="supervisor")
    messages = runtime.cleanup_monitors(task_dir, apply=True)
    assert "ownership could not be proved" in messages[0]
    assert (task_dir / "monitors" / "4242.json").exists()


def test_argv_owned_watch_requires_resume_watch_form_or_watch_subcommand():
    assert runtime._argv_is_owned_watch(
        {"kind": "watch", "argv": ["fast_agents.py", "watch", "--task", TASK_ID]}, TASK_ID
    )
    assert runtime._argv_is_owned_watch(
        {"kind": "watch", "argv": ["fast_agents.py", "resume", "--task", TASK_ID, "--watch"]}, TASK_ID
    )
    assert not runtime._argv_is_owned_watch(
        {"kind": "watch", "argv": ["fast_agents.py", "resume", "--task", TASK_ID]}, TASK_ID
    )
    assert not runtime._argv_is_owned_watch(
        {"kind": "watch", "argv": ["fast_agents.py", "resume", "--task", TASK_ID, "--watch"]}, "other-task"
    )
