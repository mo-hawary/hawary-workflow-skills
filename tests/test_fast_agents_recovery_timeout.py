"""Recovery and control timeout regressions: recovery is supervisor-owned and status is durable per role."""
from __future__ import annotations

import asyncio
import json
import socket
import sys
import threading
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "fast-codex-subagents" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import fast_agents as cli
import fast_agents_app_server as app_server
import fast_agents_common as common
import fast_agents_ipc as ipc
import fast_agents_runtime as runtime


def make_task(tmp_path: Path, *, count: int = 2):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    roles = tuple(
        common.Role(
            name=f"worker{index}", access="writer", model="gpt-6-luna", effort="low",
            outcome="Finish the bounded change", owns=(f"src/{index}.py",),
            constraints=(), depends_on=(), prompt="", fast_requested=False,
            setting_sources={"model": "explicit", "effort": "explicit", "fast": "explicit"},
        )
        for index in range(count)
    )
    manifest = common.TaskManifest(
        workspace=workspace, objective="Verify a bounded task", shared_contract="Keep the API unchanged",
        fast_requested=False, fast_selection={"mode": "none", "workers": []},
        backend="app-server", max_concurrency=3, plan_mode=False, roles=roles,
        source_path=tmp_path / "source.json",
    )
    task_id = str(uuid.uuid4())
    task_dir = tmp_path / "runtime" / "tasks" / task_id
    task_dir.mkdir(parents=True)
    common.atomic_write_json(task_dir / "manifest.json", runtime._private_task_manifest(manifest))
    status = runtime._initial_status(task_id, manifest)
    common.atomic_write_json(task_dir / "status.json", status)
    return task_dir


def interrupted_status(task_dir: Path, names: list[str]) -> None:
    status = common.read_json(task_dir / "status.json")
    for index, name in enumerate(names):
        status["roles"][name].update({
            "state": "unknown", "thread_id": f"t-{index}", "turn_id": f"u-{index}",
            "turn_start_state": "acknowledged", "turn_status": "inProgress",
        })
    common.atomic_write_json(task_dir / "status.json", status)


def fake_client(delay: float, observed: list[dict], task_dir: Path):
    turns = {}

    async def request(method, params):
        if method == "thread/resume":
            await asyncio.sleep(delay)
            return {"model": "gpt-6-luna", "sandbox": "workspace-write",
                    "approvalPolicy": "on-request", "approvalsReviewer": "auto_review", "serviceTier": "default"}
        if method == "thread/read":
            index = params["threadId"].split("-")[1]
            # Record what is durable on disk at each role boundary.
            observed.append(common.read_json(task_dir / "status.json")["roles"])
            return {"thread": {"turns": [{"id": f"u-{index}", "status": "completed",
                "items": [{"type": "agentMessage", "text": "verified prior turn"}]}]}}
        raise AssertionError(f"unexpected request: {method}")

    return SimpleNamespace(closed=asyncio.Event(), request=request, server_requests={}, turns=turns)


def test_recover_outlives_control_timeout_and_persists_each_role(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    async def exercise():
        task_dir = make_task(tmp_path)
        interrupted_status(task_dir, ["worker0", "worker1"])
        monkeypatch.setattr(ipc, "CONTROL_HANDLER_TIMEOUT", 0.2)
        supervisor = app_server.AppServerSupervisor(task_dir, tmp_path / "runtime", "unused")
        observed: list[dict] = []
        started = []
        supervisor.client = fake_client(0.3, observed, task_dir)
        supervisor.connection_epoch = "test-epoch"
        supervisor._start_scheduler = lambda: started.append(True)

        # The control caller gets an in-progress reply well before the handler cap.
        reply = await supervisor._handle_control({"command": "recover"})
        assert reply["reconciled"] is False and reply["in_progress"] is True

        # The supervisor-owned recovery keeps running and completes.
        result = await supervisor._recovery_task
        assert result["reconciled"] is True
        assert started == [True]
        on_disk = common.read_json(task_dir / "status.json")
        assert on_disk["state"] == "running"
        assert on_disk["roles"]["worker0"]["state"] == "completed"
        assert on_disk["roles"]["worker1"]["state"] == "completed"
        # worker1 was reconciled after worker0's state was already saved.
        assert observed[-1]["worker0"]["state"] == "completed"

    asyncio.run(exercise())


def test_second_recover_during_in_progress_recovery_does_not_reconcile_twice(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    async def exercise():
        task_dir = make_task(tmp_path)
        interrupted_status(task_dir, ["worker0", "worker1"])
        monkeypatch.setattr(ipc, "CONTROL_HANDLER_TIMEOUT", 0.2)
        supervisor = app_server.AppServerSupervisor(task_dir, tmp_path / "runtime", "unused")
        supervisor.client = fake_client(0.3, [], task_dir)
        supervisor.connection_epoch = "test-epoch"
        supervisor._start_scheduler = lambda: None
        reconcile_calls = []
        original_reconcile = supervisor._reconcile

        async def counted_reconcile():
            reconcile_calls.append(True)
            return await original_reconcile()

        supervisor._reconcile = counted_reconcile

        first = await supervisor._handle_control({"command": "recover"})
        second = await supervisor._handle_control({"command": "recover"})
        assert first["in_progress"] is True and second["in_progress"] is True
        result = await supervisor._recovery_task
        assert result["reconciled"] is True
        assert len(reconcile_calls) == 1

    asyncio.run(exercise())


def test_provably_unsubmitted_starting_role_is_requeued_not_stranded(tmp_path: Path):
    async def exercise():
        task_dir = make_task(tmp_path, count=1)
        status = common.read_json(task_dir / "status.json")
        status["roles"]["worker0"].update({"state": "starting"})
        common.atomic_write_json(task_dir / "status.json", status)
        supervisor = app_server.AppServerSupervisor(task_dir, tmp_path / "runtime", "unused")
        supervisor.client = fake_client(0.0, [], task_dir)
        supervisor.connection_epoch = "test-epoch"
        started = []
        supervisor._start_scheduler = lambda: started.append(True)
        result = await supervisor._reconcile()
        assert result["reconciled"] is True
        assert supervisor.status["roles"]["worker0"]["state"] == "waiting"
        assert started == [True]

    asyncio.run(exercise())


def _raw_control_server(handler_delay: float, reply: bool) -> tuple[socket.socket, threading.Thread]:
    """A loopback server that reads one control line, optionally sleeps, then optionally replies."""
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(4)

    def serve():
        connection, _ = listener.accept()
        with connection:
            connection.makefile("rb").readline()
            time.sleep(handler_delay)
            if reply:
                connection.sendall(b'{"ok":true,"result":{"reconciled":true}}\n')

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    return listener, thread


def _write_discovery(task_dir: Path, port: int) -> None:
    common.atomic_write_json(task_dir / "capabilities.json", {"read": "r" * 40, "control": "c" * 40})
    common.atomic_write_json(task_dir / "discovery.json", {
        "host": "127.0.0.1", "port": port, "capability_file": str(task_dir / "capabilities.json"),
    })


def test_client_waits_past_connect_timeout_for_a_slow_accepted_action(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    monkeypatch.setattr(ipc, "CONTROL_CONNECT_TIMEOUT", 0.05)
    monkeypatch.setattr(ipc, "CONTROL_HANDLER_TIMEOUT", 1.0)
    monkeypatch.setattr(ipc, "CONTROL_RESPONSE_MARGIN", 1.0)
    listener, _ = _raw_control_server(handler_delay=0.3, reply=True)
    try:
        _write_discovery(task_dir, listener.getsockname()[1])
        result = ipc.send_control_request(task_dir, "recover")
        assert result == {"reconciled": True}
    finally:
        listener.close()


def test_timeout_after_send_reports_uncertain_delivery_not_unreachable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    monkeypatch.setattr(ipc, "CONTROL_HANDLER_TIMEOUT", 0.05)
    monkeypatch.setattr(ipc, "CONTROL_RESPONSE_MARGIN", 0.05)
    listener, _ = _raw_control_server(handler_delay=0.5, reply=False)
    try:
        _write_discovery(task_dir, listener.getsockname()[1])
        with pytest.raises(common.FastAgentsError, match="delivery may be uncertain") as caught:
            ipc.send_control_request(task_dir, "steer", {"role": "worker0"})
        assert "not reachable" not in str(caught.value)
    finally:
        listener.close()


def test_refused_connection_still_reports_unreachable(tmp_path: Path):
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    closed = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    closed.bind(("127.0.0.1", 0))
    port = closed.getsockname()[1]
    closed.close()
    _write_discovery(task_dir, port)
    with pytest.raises(common.FastAgentsError, match="not reachable"):
        ipc.send_control_request(task_dir, "status")


def test_failed_recovery_is_persisted_with_error(tmp_path: Path):
    async def exercise():
        task_dir = make_task(tmp_path)
        interrupted_status(task_dir, ["worker0", "worker1"])
        supervisor = app_server.AppServerSupervisor(task_dir, tmp_path / "runtime", "unused")

        async def failing_reconcile():
            raise common.FastAgentsError("model/list failed")

        supervisor._reconcile = failing_reconcile
        with pytest.raises(common.FastAgentsError, match="model/list failed"):
            await supervisor._run_recovery()
        on_disk = common.read_json(task_dir / "status.json")
        assert on_disk["recovery"]["state"] == "failed"
        assert on_disk["recovery"]["error"] == "model/list failed"
        assert on_disk["state"] == "recovery_required"
        assert "model/list failed" in on_disk["action"]

    asyncio.run(exercise())


def test_recover_in_progress_prints_supervisor_action(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]):
    action = "Recovery is still running in the supervisor; poll status and do not request another recover yet."
    status = {"backend": "app-server", "state": "recovery_required", "manager": {"identity_state": "alive", "state": "running"}}
    monkeypatch.setattr(cli, "require_codex_runtime_environment", lambda: None)
    monkeypatch.setattr(cli, "read_task_status", lambda task_dir: dict(status))
    monkeypatch.setattr(
        cli, "send_control_request",
        lambda task_dir, command, payload=None: {"reconciled": False, "in_progress": True, "findings": [], "action": action},
    )
    (tmp_path / "runtime" / "tasks" / "12345678-1234-5678-1234-567812345678").mkdir(parents=True)
    args = SimpleNamespace(runtime_root=str(tmp_path / "runtime"), task="12345678-1234-5678-1234-567812345678", recover=True, json=True,
                           watch=False, interval=1, codex=None)
    assert cli._status(args, resume=True) == 0
    out = capsys.readouterr().out
    assert action in out
    assert "state remains uncertain" not in out


def test_status_skips_live_control_once_supervisor_has_finished(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    calls: list[str] = []
    status = {"backend": "app-server", "state": "completed", "manager": {"identity_state": "alive", "state": "completed"}}
    monkeypatch.setattr(cli, "read_task_status", lambda task_dir: dict(status))

    def control(task_dir, command, payload=None):
        calls.append(command)
        raise common.FastAgentsError("task supervisor is not reachable")

    monkeypatch.setattr(cli, "send_control_request", control)
    (tmp_path / "runtime" / "tasks" / "12345678-1234-5678-1234-567812345678").mkdir(parents=True)
    args = SimpleNamespace(runtime_root=str(tmp_path / "runtime"), task="12345678-1234-5678-1234-567812345678", recover=False, json=True,
                           watch=False, interval=1, codex=None)
    assert cli._status(args) == 0
    assert calls == []


def test_eof_after_send_reports_uncertain_delivery(tmp_path: Path):
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    # The server reads the request line and closes without replying.
    listener, _ = _raw_control_server(handler_delay=0.0, reply=False)
    try:
        _write_discovery(task_dir, listener.getsockname()[1])
        with pytest.raises(common.FastAgentsError, match="delivery may be uncertain") as caught:
            ipc.send_control_request(task_dir, "steer", {"role": "worker0"})
        assert "invalid control response" not in str(caught.value)
    finally:
        listener.close()


def test_handler_cancellation_does_not_cancel_supervisor_recovery(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    async def exercise():
        task_dir = make_task(tmp_path)
        interrupted_status(task_dir, ["worker0", "worker1"])
        # The recovery wait is 0.16s here, so the caller gives up well before recovery finishes.
        monkeypatch.setattr(ipc, "CONTROL_HANDLER_TIMEOUT", 0.2)
        supervisor = app_server.AppServerSupervisor(task_dir, tmp_path / "runtime", "unused")
        started = []
        supervisor.client = fake_client(0.3, [], task_dir)
        supervisor.connection_epoch = "test-epoch"
        supervisor._start_scheduler = lambda: started.append(True)

        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(supervisor._handle_control({"command": "recover"}), timeout=0.01)
        task = supervisor._recovery_task
        assert task is not None
        result = await task
        assert not task.cancelled()
        assert result["reconciled"] is True
        assert started == [True]
        on_disk = common.read_json(task_dir / "status.json")
        assert on_disk["state"] == "running"
        assert on_disk["recovery"]["state"] == "completed"

    asyncio.run(exercise())
