"""Regression coverage for PR #28: dependency settling, IPC sizing, and tier opt-out."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import uuid

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "fast-agents" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import fast_agents_app_server as app_server
import fast_agents_common as common
import fast_agents_ipc as ipc
import fast_agents_runtime as runtime
from fast_agents_protocol import AppServerRpcError


def make_task(tmp_path: Path, *, backend: str = "app-server", fast: tuple[bool, ...] = (False,)):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    roles = tuple(
        common.Role(
            name=f"worker{index}", access="writer", model="gpt-6-luna", effort="low",
            outcome="Finish the bounded change", owns=(f"src/{index}.py",),
            constraints=(), depends_on=(), prompt="", fast_requested=requested,
            setting_sources={"model": "explicit", "effort": "explicit", "fast": "explicit"},
        )
        for index, requested in enumerate(fast)
    )
    manifest = common.TaskManifest(
        workspace=workspace, objective="Verify a bounded task", shared_contract="Keep the API unchanged",
        fast_requested=any(fast), fast_selection={"mode": "none", "workers": []},
        backend=backend, max_concurrency=3, plan_mode=False, roles=roles,
        source_path=tmp_path / "source.json",
    )
    task_id = str(uuid.uuid4())
    task_dir = tmp_path / "runtime" / "tasks" / task_id
    task_dir.mkdir(parents=True)
    common.atomic_write_json(task_dir / "manifest.json", runtime._private_task_manifest(manifest))
    status = runtime._initial_status(task_id, manifest)
    common.atomic_write_json(task_dir / "status.json", status)
    return task_dir, manifest, status


@pytest.mark.parametrize("report_result", ["present", "missing", "rpc-error"])
def test_dependency_waits_for_report_rpc_before_accepting_or_blocking(tmp_path: Path, report_result: str):
    async def exercise():
        task_dir, _, _ = make_task(tmp_path)
        manifest = common.read_json(task_dir / "manifest.json")
        writer = manifest["roles"][0]
        manifest["roles"] += [
            {**writer, "name": "dependent", "access": "reviewer", "owns": [], "depends_on": ["worker0"]},
            {**writer, "name": "independent", "access": "reviewer", "owns": []},
        ]
        common.atomic_write_json(task_dir / "manifest.json", manifest)
        status = common.read_json(task_dir / "status.json")
        for name in ("dependent", "independent"):
            status["roles"][name] = {**status["roles"]["worker0"], "fast": dict(status["roles"]["worker0"]["fast"])}
        status["roles"]["worker0"].update({"thread_id": "thread-0", "turn_id": "turn-0", "turn_status": "inProgress"})
        common.atomic_write_json(task_dir / "status.json", status)
        supervisor = app_server.AppServerSupervisor(task_dir, tmp_path / "runtime", "unused")
        report_started, release_report = asyncio.Event(), asyncio.Event()
        started: list[str] = []

        async def request(method, params):
            assert method == "thread/read"
            report_started.set()
            await release_report.wait()
            if report_result == "rpc-error":
                raise AppServerRpcError(method, {"message": "report read failed"})
            items = [{"type": "agentMessage", "text": "Writer finished."}] if report_result == "present" else []
            return {"thread": {"turns": [{"id": "turn-0", "status": "completed", "items": items}]}}

        supervisor.client = SimpleNamespace(closed=asyncio.Event(), request=request, server_requests={})
        supervisor.connection_epoch = "test-epoch"
        real_run_role = supervisor._run_role

        async def run_role(role, dependency_context):
            started.append(role.name)
            if role.name == "worker0":
                await real_run_role(role, dependency_context)
            else:
                if role.name == "dependent":
                    assert "Writer finished." in dependency_context
                supervisor.status["roles"][role.name].update({"state": "completed", "final_report_available": True})
                supervisor._write_final(role.name, "Read-only review finished.")

        supervisor._run_role = run_role
        scheduler = asyncio.create_task(supervisor._schedule())
        try:
            for _ in range(200):
                if supervisor.status["roles"]["worker0"]["state"] == "starting":
                    break
                await asyncio.sleep(0.005)
            else:
                pytest.fail("writer did not start")
            await supervisor._on_event({
                "method": "turn/completed",
                "params": {"threadId": "thread-0", "turn": {"id": "turn-0", "status": "completed", "items": []}},
            })
            await asyncio.wait_for(report_started.wait(), 2)
            # Leave enough time for multiple scheduler ticks while the RPC is still pending.
            await asyncio.sleep(0.15)
            assert supervisor.status["roles"]["dependent"]["state"] == "waiting"
            assert "independent" in started
            release_report.set()
            await asyncio.wait_for(scheduler, 3)
            dependent = supervisor.status["roles"]["dependent"]
            if report_result == "present":
                assert dependent["state"] == "completed"
                assert started.count("dependent") == 1
                assert supervisor.status["state"] == "completed"
            else:
                assert dependent["state"] == "blocked"
                assert "dependent" not in started
                assert supervisor.status["state"] == "failed"
            assert supervisor.status["roles"]["independent"]["state"] == "completed"
        finally:
            release_report.set()
            scheduler.cancel()
            await asyncio.gather(scheduler, *supervisor.role_tasks.values(), return_exceptions=True)

    asyncio.run(exercise())


def event_history(count: int, summary: str = "commandExecution completed") -> list[dict]:
    return [
        {"seq": index, "at": "2026-01-01T00:00:00+00:00", "kind": "item/completed", "role": "worker0",
         "thread_id": str(uuid.uuid4()), "turn_id": str(uuid.uuid4()), "item_id": str(uuid.uuid4()), "summary": summary}
        for index in range(1, count + 1)
    ]


@pytest.mark.parametrize("summary", ["commandExecution completed", "\U0001f680" * 1000])
def test_full_history_status_and_byte_bounded_event_pagination(tmp_path: Path, summary: str):
    async def exercise():
        task_dir, _, status = make_task(tmp_path)
        history = event_history(500, summary)
        assert len(json.dumps(history, ensure_ascii=False).encode()) > ipc.MAX_CONTROL_LINE
        status["control_events"] = history
        common.atomic_write_json(task_dir / "status.json", status)
        supervisor = app_server.AppServerSupervisor(task_dir, tmp_path / "runtime", "unused")
        server = ipc.ControlServer(task_dir, supervisor._handle_control)
        await server.start()
        try:
            projected = await asyncio.to_thread(ipc.send_control_request, task_dir, "status")
            assert "control_events" not in projected
            assert projected["event_history"] == {"retained": 500, "oldest_seq": 1, "latest_seq": 500}
            assert supervisor.status["control_events"] == history
            assert common.read_json(task_dir / "status.json")["control_events"] == history
            after = 0
            received = []
            while after < 500:
                page = await asyncio.to_thread(ipc.send_control_request, task_dir, "events", {"after_seq": after, "limit": 100})
                assert len(ipc._encode_response({"ok": True, "result": page})) <= ipc.MAX_CONTROL_LINE
                assert after < page["next_seq"] <= 500
                assert page["latest_seq"] == 500
                assert page["has_more"] == (page["next_seq"] < 500)
                received.extend(page["events"])
                after = page["next_seq"]
            assert received == history
            empty = await asyncio.to_thread(ipc.send_control_request, task_dir, "events", {"after_seq": after, "limit": 100})
            assert empty["events"] == [] and empty["has_more"] is False
        finally:
            await server.close()

    asyncio.run(exercise())


def test_live_status_and_resume_cli_with_500_events(tmp_path: Path):
    async def exercise():
        task_dir, _, status = make_task(tmp_path)
        status["control_events"] = event_history(500)
        status["state"] = "running"
        status["manager"] = {"state": "running", "identity": runtime.capture_process_identity(os.getpid()), "pid": os.getpid()}
        common.atomic_write_json(task_dir / "status.json", status)
        supervisor = app_server.AppServerSupervisor(task_dir, tmp_path / "runtime", "unused")
        server = ipc.ControlServer(task_dir, supervisor._handle_control)
        await server.start()
        try:
            for command in ("status", "resume"):
                process = await asyncio.create_subprocess_exec(
                    sys.executable, str(SCRIPTS / "fast_agents.py"), command,
                    "--task", status["task_id"], "--runtime-root", str(tmp_path / "runtime"), "--json",
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await asyncio.wait_for(process.communicate(), 10)
                assert process.returncode == 0, stderr.decode()
                assert json.loads(stdout)["event_history"]["retained"] == 500
        finally:
            await server.close()

    asyncio.run(exercise())


def test_oversized_single_event_and_other_responses_fail_with_bounded_errors(tmp_path: Path):
    async def exercise():
        async def handler(request):
            if request["command"] == "events":
                return {"events": event_history(1, "x" * ipc.MAX_CONTROL_LINE), "latest_seq": 1}
            return {"detail": "x" * ipc.MAX_CONTROL_LINE}

        server = ipc.ControlServer(tmp_path, handler)
        await server.start()
        try:
            with pytest.raises(common.FastAgentsError, match="event exceeds the control response limit"):
                await asyncio.to_thread(ipc.send_control_request, tmp_path, "events")
            with pytest.raises(common.FastAgentsError, match="control response exceeds the byte limit"):
                await asyncio.to_thread(ipc.send_control_request, tmp_path, "inspect-request")
        finally:
            await server.close()

    asyncio.run(exercise())


@pytest.mark.parametrize("requested", [(False, False), (True, False)])
def test_exec_workers_override_inherited_fast_configuration(tmp_path: Path, requested: tuple[bool, ...]):
    task_dir, manifest, _ = make_task(tmp_path, backend="exec", fast=requested)
    home = tmp_path / "codex-home"
    home.mkdir()
    (home / "config.toml").write_text('service_tier="fast"\n[features]\nfast_mode=true\n', encoding="utf-8")
    fake = tmp_path / "inspect_config.py"
    fake.write_text('''import json, os, pathlib, sys, tomllib
config = tomllib.loads((pathlib.Path(os.environ["CODEX_HOME"]) / "config.toml").read_text())
args = sys.argv[1:]
for index, arg in enumerate(args):
    if arg == "-c":
        key, value = args[index + 1].split("=", 1)
        parsed = tomllib.loads("value=" + value)["value"]
        if key == "service_tier": config[key] = parsed
        if key == "features.fast_mode": config["features"]["fast_mode"] = parsed
    if arg == "--enable" and args[index + 1] == "fast_mode": config["features"]["fast_mode"] = True
print(json.dumps(config))
''', encoding="utf-8")
    for role, fast in zip(manifest.roles, requested):
        argv = runtime.build_codex_argv(str(fake), manifest.workspace, role, manifest.fast_requested, task_dir)
        result = subprocess.run(argv, env={**os.environ, "CODEX_HOME": str(home)}, capture_output=True, text=True, check=True, timeout=10)
        effective = json.loads(result.stdout)
        assert effective["service_tier"] == ("fast" if fast else "default")
        assert effective["features"]["fast_mode"] is fast
        if not fast:
            assert 'service_tier="default"' in argv
            assert "features.fast_mode=false" in argv
    assert 'service_tier="fast"' in (home / "config.toml").read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("requested", "tier", "enabled", "expected"),
    [(False, "default", False, "confirmed"), (False, "priority", True, "rejected"),
     (False, "fast", True, "rejected"), (False, None, False, "unverified"),
     (True, "priority", True, "confirmed"), (True, "fast", True, "confirmed"),
     (True, "default", False, "rejected")],
)
def test_exec_evidence_is_checked_even_after_opt_out(requested, tier, enabled, expected):
    events = [
        {"type": "thread.started", "thread_id": "session-1"},
        {"type": "session.config", "thread_id": "session-1", "runtime_config": {"effective": True, "service_tier": tier, "fast_mode": enabled}},
    ]
    state, evidence = runtime._fast_runtime_result(events, requested)
    assert state == expected
    if expected != "unverified":
        assert evidence["thread_id"] == "session-1"
    assert runtime._fast_runtime_result([], requested) == ("unverified", None)
    events[-1]["thread_id"] = "unrelated-session"
    assert runtime._fast_runtime_result(events, requested) == ("unverified", None)


def test_mixed_worker_tier_mismatch_cannot_be_hidden_by_a_matching_worker(tmp_path: Path):
    # The opted-out worker appears first; an opted-in worker must not overwrite its mismatch.
    task_dir, manifest, status = make_task(tmp_path, backend="exec", fast=(False, True))
    status["manager"]["state"] = "completed"
    for role in manifest.roles:
        directory = task_dir / "agents" / role.name
        directory.mkdir(parents=True)
        status["roles"][role.name]["state"] = "completed"
        (directory / "final.md").write_text("Finished", encoding="utf-8")
        common.atomic_write_json(directory / "exit.json", {"exit_code": 0})
        events = [
            {"type": "thread.started", "thread_id": role.name},
            {"type": "runtime_config", "thread_id": role.name, "runtime_config": {"effective": True, "service_tier": "fast", "fast_mode": True}},
        ]
        (directory / "events.jsonl").write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")
    common.atomic_write_json(task_dir / "status.json", status)
    projected = runtime.read_task_status(task_dir)
    assert projected["fast"]["runtime"] == "rejected"
    assert projected["fast"]["evidence"]["thread_id"] == "worker0"
    assert projected["roles"]["worker0"]["fast"]["requested"] is False
    assert projected["roles"]["worker0"]["fast"]["effective"] == "different"
    assert projected["roles"]["worker1"]["fast"]["effective"] == "matched"
    assert all(role["fast"]["upstream"] == "unverified" for role in projected["roles"].values())


def test_windows_pytest_dependency_is_explicitly_pinned_and_hashed():
    from packaging.requirements import Requirement

    requirements = (ROOT / "requirements-dev.txt").read_text(encoding="utf-8").replace("\\\n", " ")
    entries = [line for line in requirements.splitlines() if line.startswith("colorama==")]
    assert len(entries) == 1
    requirement = Requirement(entries[0].split("--hash=", 1)[0].strip())
    assert str(requirement.specifier) == "==0.4.6"
    assert requirement.marker.evaluate({"sys_platform": "win32"})
    assert not requirement.marker.evaluate({"sys_platform": "linux"})
    assert not requirement.marker.evaluate({"sys_platform": "darwin"})
    assert "sha256:4f1d9991f5acc0ca119f9d443620b77f9d6b33703e51011c16baf57afb285fc6" in entries[0]
    assert "sha256:08695f5cb7ed6e0531a20572697297273c47b8cae5a63ffc6d6ed5c201be6e44" in entries[0]


@pytest.mark.parametrize(
    ("waits", "times_ok", "image_ok", "error_code", "expected"),
    [
        ([0], False, False, 31, "dead"),
        ([0x102, 0], False, False, 31, "dead"),
        ([0x102, 0], True, False, 31, "dead"),
        ([0x102, 0x102], True, False, 31, "error"),
        ([0x102, 0x102], True, False, 5, "permission"),
        ([0x102, 0x102], False, False, 31, "error"),
        ([0xFFFFFFFF], True, True, 6, "error"),
        ([0x102, 0x102], True, True, 0, "alive"),
        ([0x102, 0], True, True, 0, "exited"),
    ],
)
def test_windows_identity_uses_exit_evidence_before_image_metadata(
    monkeypatch: pytest.MonkeyPatch, waits, times_ok, image_ok, error_code, expected
):
    import ctypes

    calls = []
    wait_results = iter(waits)

    class Function:
        def __init__(self, name, implementation):
            self.name = name
            self.implementation = implementation

        def __call__(self, *args):
            calls.append(self.name)
            return self.implementation(*args)

    def get_times(handle, creation, *_args):
        creation._obj.low = 123
        creation._obj.high = 0
        return times_ok

    def get_image(handle, flags, buffer, size):
        buffer.value = r"C:\tools\python.exe"
        return image_ok

    kernel = SimpleNamespace(
        OpenProcess=Function("open", lambda *_args: 1),
        GetProcessTimes=Function("times", get_times),
        QueryFullProcessImageNameW=Function("image", get_image),
        WaitForSingleObject=Function("wait", lambda *_args: next(wait_results)),
        CloseHandle=Function("close", lambda *_args: True),
    )
    monkeypatch.setattr(ctypes, "WinDLL", lambda *_args, **_kwargs: kernel, raising=False)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: error_code, raising=False)
    if expected in {"error", "permission"}:
        exception = PermissionError if expected == "permission" else OSError
        with pytest.raises(exception):
            runtime._windows_process_identity(12345)
    else:
        result = runtime._windows_process_identity(12345)
        if expected == "dead":
            assert result is None
        else:
            assert result["start_token"] == "123"
            assert result["exited"] is (expected == "exited")
    assert calls[:2] == ["open", "wait"]
    assert calls[-1] == "close" and calls.count("close") == 1
    if waits[0] in {0, 0xFFFFFFFF}:
        assert "times" not in calls and "image" not in calls
    if not times_ok:
        assert "image" not in calls


@pytest.mark.skipif(os.name != "nt", reason="requires a native Windows process handle")
def test_windows_exited_child_is_dead_while_parent_retains_handle():
    process = subprocess.Popen(
        [sys.executable, "-c", "import sys; sys.stdin.buffer.read(1)"],
        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        identity = runtime.capture_process_identity(process.pid)
        assert identity and runtime.inspect_process(identity)[0] == "alive"
        process.communicate(b"x", timeout=10)
        # Popen retains its process HANDLE even after wait/communicate completes.
        assert runtime.inspect_process(identity)[0] == "dead"
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)
        if process.stdin is not None:
            process.stdin.close()
