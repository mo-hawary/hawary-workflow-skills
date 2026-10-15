"""Regression coverage: turn/start failures after an observed turn, and identity capture failures at launch."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import shutil
import sys
from types import SimpleNamespace
import uuid

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "fast-codex-subagents" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import fast_agents_app_server as app_server
import fast_agents_common as common
import fast_agents_runtime as runtime
from fast_agents_protocol import AppServerError, AppServerRpcError
from fast_agents_verification import snapshot_workspace

FAKE_FIXTURE = Path(__file__).parent / "fixtures" / "fast_agents" / "fake_codex.py"


def make_task(tmp_path: Path, *, backend: str, names: list[str], depends: dict[str, tuple[str, ...]] | None = None) -> Path:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    depends = depends or {}
    roles = tuple(
        common.Role(
            name=name, access="writer", model="gpt-6-luna", effort="low",
            outcome="Finish the bounded change", owns=(f"src/{name}.py",),
            constraints=(), depends_on=depends.get(name, ()), prompt="", fast_requested=False,
            setting_sources={"model": "explicit", "effort": "explicit", "fast": "explicit"},
        )
        for name in names
    )
    manifest = common.TaskManifest(
        workspace=workspace, objective="Verify a bounded task", shared_contract="Keep the API unchanged",
        fast_requested=False, fast_selection={"mode": "none", "workers": []},
        backend=backend, max_concurrency=2, plan_mode=False, roles=roles,
        source_path=tmp_path / "source.json",
    )
    task_id = str(uuid.uuid4())
    task_dir = tmp_path / "runtime" / "tasks" / task_id
    task_dir.mkdir(parents=True)
    common.atomic_write_json(task_dir / "manifest.json", runtime._private_task_manifest(manifest))
    status = runtime._initial_status(task_id, manifest)
    status["workspace_baseline"] = snapshot_workspace(workspace)
    common.atomic_write_json(task_dir / "status.json", status)
    return task_dir


def run_worker_turn(tmp_path: Path, request_factory) -> tuple[app_server.AppServerSupervisor, Path]:
    """Drive one app-server role through turn/start with a thread already acknowledged."""
    task_dir = make_task(tmp_path, backend="app-server", names=["worker0"])
    supervisor = app_server.AppServerSupervisor(task_dir, tmp_path / "runtime", "unused")
    supervisor.status["roles"]["worker0"].update({"thread_id": "thread-0"})

    async def request(method, params):
        assert method == "turn/start"
        return await request_factory(supervisor, method, params)

    supervisor.client = SimpleNamespace(closed=asyncio.Event(), request=request, server_requests={})
    asyncio.run(supervisor._run_role(supervisor.roles["worker0"], ""))
    return supervisor, task_dir


@pytest.mark.parametrize(
    "error_factory",
    [
        lambda: AppServerError("app-server request turn/start did not complete: timed out"),
        lambda: OSError("broken pipe while writing turn/start"),
    ],
    ids=["request-timeout", "transport-oserror"],
)
def test_turn_start_failure_after_observed_turn_stays_unknown_not_failed(tmp_path: Path, error_factory):
    async def request_factory(supervisor, method, params):
        # The server announces the turn before the turn/start response is lost.
        await supervisor._on_event({
            "method": "turn/started",
            "params": {"threadId": "thread-0", "turn": {"id": "turn-0", "status": "inProgress"}},
        })
        raise error_factory()

    supervisor, task_dir = run_worker_turn(tmp_path, request_factory)
    role_state = supervisor.status["roles"]["worker0"]
    assert role_state["turn_id"] == "turn-0"
    assert role_state["state"] == "unknown"
    assert role_state["state"] != "failed"
    assert role_state["recovery_required"] is True
    assert role_state["error"].startswith("recovery required")
    assert not (supervisor._role_dir("worker0") / "exit.json").exists()
    assert supervisor.role_done["worker0"].is_set()
    durable = common.read_json(task_dir / "status.json")["roles"]["worker0"]
    assert durable["state"] == "unknown"
    assert durable["recovery_required"] is True


def test_turn_start_rpc_rejection_without_observed_turn_is_failed(tmp_path: Path):
    async def request_factory(supervisor, method, params):
        raise AppServerRpcError(method, {"message": "invalid turn input"})

    supervisor, task_dir = run_worker_turn(tmp_path, request_factory)
    role_state = supervisor.status["roles"]["worker0"]
    # An explicit server rejection is definitive: the turn never started.
    assert role_state["state"] == "failed"
    assert role_state["exit_code"] == 1
    assert "invalid turn input" in role_state["error"]
    assert (supervisor._role_dir("worker0") / "exit.json").exists()
    assert not role_state.get("recovery_required")


def test_turn_start_transport_error_without_observed_turn_is_unknown(tmp_path: Path):
    async def request_factory(supervisor, method, params):
        raise AppServerError("app-server request turn/start did not complete: timed out")

    supervisor, _ = run_worker_turn(tmp_path, request_factory)
    role_state = supervisor.status["roles"]["worker0"]
    # turn_start_state is "submitted" before the RPC, so an unobserved turn is never reported as failed.
    assert role_state["turn_id"] is None
    assert role_state["state"] == "unknown"
    assert role_state["recovery_required"] is True


@pytest.mark.skipif(os.name == "nt", reason="fake Codex fixture uses a POSIX executable shebang")
def test_identity_capture_failure_keeps_source_running_and_dependent_still_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    task_dir = make_task(tmp_path, backend="exec", names=["alpha", "beta"], depends={"beta": ("alpha",)})
    task_id = task_dir.name
    executable = tmp_path / "fake Codex CLI.py"
    shutil.copy2(FAKE_FIXTURE, executable)
    executable.chmod(0o755)
    capture_dir = tmp_path / "captures"
    control_dir = tmp_path / "control"
    (control_dir / "codex-home").mkdir(parents=True)
    (control_dir / "codex-home" / "config.toml").write_text("# fixture\n", encoding="utf-8")
    (control_dir / "alpha.json").write_text(json.dumps({"delay": 0.2}), encoding="utf-8")
    monkeypatch.setenv("FAST_AGENTS_CAPTURE_DIR", str(capture_dir))
    monkeypatch.setenv("FAST_AGENTS_CONTROL_DIR", str(control_dir))
    monkeypatch.setenv("CODEX_HOME", str(control_dir / "codex-home"))
    monkeypatch.setenv("XDG_STATE_HOME", str(control_dir / "xdg-state"))
    monkeypatch.setenv("LOCALAPPDATA", str(control_dir / "local-app-data"))

    real_identity = runtime.capture_process_identity

    def identity_for_children(pid: int):
        # The manager's own identity is still captured; only the launched Codex children are denied.
        if pid == os.getpid():
            return real_identity(pid)
        raise PermissionError("denied while reading process identity")

    monkeypatch.setattr(runtime, "capture_process_identity", identity_for_children)

    result = runtime.supervise(task_dir, tmp_path / "runtime", str(executable))

    assert result == 0
    status = common.read_json(task_dir / "status.json")
    assert status["roles"]["alpha"]["state"] == "completed"
    assert status["roles"]["beta"]["state"] == "completed"
    assert status["state"] == "completed"
    # The identity gap is recorded on the launched role, not turned into a blocking unknown.
    assert status["roles"]["alpha"]["identity"] is None
    assert status["roles"]["alpha"]["identity_error"]
    timeline = [json.loads(line) for line in (capture_dir / "timeline.jsonl").read_text().splitlines()]
    positions = {(event["role"], event["action"]): index for index, event in enumerate(timeline)}
    assert positions[("alpha", "finish")] < positions[("beta", "start")]
    assert (capture_dir / f"{task_id}-beta.json").exists()
