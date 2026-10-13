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
SCRIPTS = ROOT / "skills" / "fast-codex-subagents" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import fast_agents_app_server as app_server
import fast_agents_common as common
import fast_agents_ipc as ipc
import fast_agents_runtime as runtime
import fast_agents_setup as setup
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

    requirements = (ROOT / "requirements-windows.txt").read_text(encoding="utf-8").replace("\\\n", " ")
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


@pytest.mark.parametrize(
    ("selected_name", "argument_kind"),
    [
        ("AGENTS.md", "directory"),
        ("AGENTS.override.md", "directory"),
        ("AGENTS.override.md", "ordinary-file"),
    ],
)
def test_instruction_preview_rejects_the_selected_file_when_it_is_a_symlink(
    tmp_path: Path, selected_name: str, argument_kind: str
):
    codex_home = tmp_path / "codex"
    codex_home.mkdir()
    agents = codex_home / "AGENTS.md"
    agents.write_text("Base instructions\n", encoding="utf-8")
    selected = codex_home / selected_name
    external = tmp_path / "external-instructions.md"
    external.write_text("Do not overwrite this target\n", encoding="utf-8")
    selected.unlink(missing_ok=True)
    selected.symlink_to(external)
    target_arg = agents if argument_kind == "ordinary-file" else codex_home

    with pytest.raises(common.FastAgentsError, match="instruction target is a symlink"):
        setup.instruction_preview(target_arg, tmp_path / "runtime")

    assert selected.is_symlink()
    assert external.read_text(encoding="utf-8") == "Do not overwrite this target\n"


def test_instruction_apply_rechecks_symlink_after_preview_and_preserves_symlinked_home(
    tmp_path: Path,
):
    codex_home = tmp_path / "codex"
    codex_home.mkdir()
    agents = codex_home / "AGENTS.md"
    original = "Base instructions\n"
    agents.write_text(original, encoding="utf-8")
    runtime_root = tmp_path / "runtime"
    plan_id, _, before_sha, after_sha = setup.instruction_preview(agents, runtime_root)
    external = tmp_path / "external-instructions.md"
    external.write_text(original, encoding="utf-8")
    agents.unlink()
    agents.symlink_to(external)

    with pytest.raises(common.FastAgentsError, match="instruction target is a symlink"):
        setup.apply_instruction_preview(runtime_root, plan_id, after_sha, before_sha)
    assert agents.is_symlink()
    assert external.read_text(encoding="utf-8") == original

    agents.unlink()
    agents.write_text(original, encoding="utf-8")
    linked_home = tmp_path / "codex-alias"
    linked_home.symlink_to(codex_home, target_is_directory=True)
    next_plan, selected, next_before, next_after = setup.instruction_preview(linked_home, runtime_root)
    assert selected == agents
    setup.apply_instruction_preview(runtime_root, next_plan, next_after, next_before)
    assert agents.read_text(encoding="utf-8").startswith(original)
    assert external.read_text(encoding="utf-8") == original


@pytest.mark.parametrize("argument_kind", ["home", "explicit-file"])
def test_instruction_rollback_uses_owned_target_after_override_is_added(
    tmp_path: Path, argument_kind: str
):
    codex_home = tmp_path / "codex"
    codex_home.mkdir()
    agents = codex_home / "AGENTS.md"
    original = "Original shared instructions\n"
    agents.write_text(original, encoding="utf-8")
    runtime_root = tmp_path / "runtime"
    plan_id, _, before_sha, after_sha = setup.instruction_preview(agents, runtime_root)
    setup.apply_instruction_preview(runtime_root, plan_id, after_sha, before_sha)
    override = codex_home / "AGENTS.override.md"
    override.write_text("New active override stays untouched\n", encoding="utf-8")
    target_arg = codex_home if argument_kind == "home" else agents

    assert str(agents) in " ".join(setup.rollback_instruction(target_arg, apply=False))
    setup.rollback_instruction(target_arg, apply=True)

    assert agents.read_text(encoding="utf-8") == original
    assert override.read_text(encoding="utf-8") == "New active override stays untouched\n"


def test_instruction_rollback_still_refuses_tampered_owner_and_edited_target(tmp_path: Path):
    codex_home = tmp_path / "codex"
    codex_home.mkdir()
    agents = codex_home / "AGENTS.md"
    agents.write_text("Original instructions\n", encoding="utf-8")
    runtime_root = tmp_path / "runtime"
    plan_id, _, before_sha, after_sha = setup.instruction_preview(agents, runtime_root)
    setup.apply_instruction_preview(runtime_root, plan_id, after_sha, before_sha)
    owner_path = codex_home / setup.INSTRUCTION_OWNER
    owner = json.loads(owner_path.read_text(encoding="utf-8"))
    owner["target"] = str(tmp_path / "outside.md")
    owner_path.write_text(json.dumps(owner), encoding="utf-8")

    with pytest.raises(common.FastAgentsError, match="ownership could not be verified"):
        setup.rollback_instruction(codex_home, apply=True)
    assert setup.MANAGED_BLOCK in agents.read_text(encoding="utf-8")

    owner["target"] = str(agents)
    owner_path.write_text(json.dumps(owner), encoding="utf-8")
    owner_path.write_text("{", encoding="utf-8")
    with pytest.raises(common.FastAgentsError, match="cannot read valid JSON"):
        setup.rollback_instruction(codex_home, apply=True)
    owner_path.write_text(json.dumps(owner), encoding="utf-8")
    agents.write_text(agents.read_text(encoding="utf-8") + "User edit\n", encoding="utf-8")
    with pytest.raises(common.FastAgentsError, match="changed after setup"):
        setup.rollback_instruction(agents, apply=True)
    assert "User edit" in agents.read_text(encoding="utf-8")


@pytest.mark.parametrize("content", ["omitted", None])
def test_url_mcp_elicitation_accept_allows_optional_content(tmp_path: Path, content: Any):
    pending = {
        "method": "mcpServer/elicitation/request",
        "params": {"mode": "url", "url": "https://example.invalid", "elicitationId": "elicitation-1"},
    }
    payload: dict[str, Any] = {"action": "accept"}
    if content != "omitted":
        payload["content"] = content

    response = app_server._validate_server_reply(
        pending, {"payload": payload}, SimpleNamespace(access="writer"), tmp_path
    )

    assert response == {"action": "accept", "content": None}


def test_form_mcp_elicitation_requires_structured_content_and_accepts_valid_content(tmp_path: Path):
    pending = {
        "method": "mcpServer/elicitation/request",
        "params": {
            "mode": "form",
            "requestedSchema": {
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            },
        },
    }

    with pytest.raises(common.FastAgentsError, match="requires structured content"):
        app_server._validate_server_reply(
            pending, {"payload": {"action": "accept"}}, SimpleNamespace(access="writer"), tmp_path
        )

    content = {"name": "Ada"}
    response = app_server._validate_server_reply(
        pending,
        {"payload": {"action": "accept", "content": content}},
        SimpleNamespace(access="writer"),
        tmp_path,
    )
    assert response == {"action": "accept", "content": content}


def test_permission_grant_scope_strict_auto_review_combinations(tmp_path: Path):
    pending = {
        "method": "item/permissions/requestApproval",
        "params": {"permissions": {"network": {"enabled": False}}},
    }
    role = SimpleNamespace(access="writer")

    turn_strict = app_server._validate_server_reply(
        pending,
        {"payload": {"permissions": {"network": {"enabled": False}}, "scope": "turn", "strictAutoReview": True}},
        role,
        tmp_path,
    )
    turn_non_strict = app_server._validate_server_reply(
        pending,
        {"payload": {"permissions": {"network": {"enabled": False}}, "scope": "turn", "strictAutoReview": False}},
        role,
        tmp_path,
    )
    turn_null_flag = app_server._validate_server_reply(
        pending,
        {"payload": {"permissions": {"network": {"enabled": False}}, "scope": "turn", "strictAutoReview": None}},
        role,
        tmp_path,
    )
    session_without_flag = app_server._validate_server_reply(
        pending,
        {"payload": {"permissions": {"network": {"enabled": False}}, "scope": "session"}},
        role,
        tmp_path,
    )
    session_non_strict = app_server._validate_server_reply(
        pending,
        {"payload": {"permissions": {"network": {"enabled": False}}, "scope": "session", "strictAutoReview": False}},
        role,
        tmp_path,
    )

    assert turn_strict["strictAutoReview"] is True
    assert turn_non_strict["strictAutoReview"] is False
    assert "strictAutoReview" not in turn_null_flag
    assert session_without_flag["scope"] == "session"
    assert "strictAutoReview" not in session_without_flag
    assert session_non_strict == {
        "permissions": {"network": {"enabled": False}},
        "scope": "session",
        "strictAutoReview": False,
    }
    with pytest.raises(common.FastAgentsError, match="strictAutoReview=true requires turn scope"):
        app_server._validate_server_reply(
            pending,
            {"payload": {"permissions": {"network": {"enabled": False}}, "scope": "session", "strictAutoReview": True}},
            role,
            tmp_path,
        )


def test_unsupported_permission_reply_keeps_server_request_pending(tmp_path: Path):
    async def exercise() -> None:
        task_dir, _, _ = make_task(tmp_path)
        supervisor = app_server.AppServerSupervisor(task_dir, tmp_path / "runtime", "unused")
        request_id = "permission-request"
        key = supervisor._request_key(request_id)
        server_request = {
            "id": request_id,
            "method": "item/permissions/requestApproval",
            "params": {"permissions": {"network": {"enabled": False}}},
        }
        pending = {
            "request_id": request_id,
            "method": server_request["method"],
            "params": server_request["params"],
            "thread_id": "thread-1",
            "turn_id": "turn-1",
            "role": "worker0",
            "item_id": "item-1",
            "transport_epoch": "test-epoch",
            "server_request": server_request,
        }
        supervisor.pending_requests[key] = pending
        supervisor.connection_epoch = "test-epoch"
        supervisor._pending_is_current = lambda _key, _pending: True
        responses: list[tuple[str, dict[str, Any]]] = []

        async def respond(reply_id: str, response: dict[str, Any]) -> None:
            responses.append((reply_id, response))

        supervisor.client = SimpleNamespace(
            closed=asyncio.Event(), server_requests={key: server_request}, respond=respond
        )
        request = {
            "request_id": request_id,
            "thread_id": "thread-1",
            "turn_id": "turn-1",
            "payload": {
                "permissions": {"network": {"enabled": False}},
                "scope": "session",
                "strictAutoReview": True,
            },
        }

        with pytest.raises(common.FastAgentsError, match="strictAutoReview=true requires turn scope"):
            await supervisor._reply_pending(request)

        assert supervisor.pending_requests[key] is pending
        assert responses == []

    asyncio.run(exercise())

# Launch, recovery, and approval concurrency regressions (review follow-up).
def test_plan_mode_launch_uses_the_same_resolved_manifest_at_the_boundary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from dataclasses import replace
    from types import SimpleNamespace
    import fast_agents as cli

    _, writer_manifest, _ = make_task(tmp_path)
    reviewer = replace(
        writer_manifest.roles[0], name="reviewer", access="reviewer",
        model="gpt-6.1-sol", effort="xhigh", owns=(),
    )
    reviewed = replace(writer_manifest, roles=(reviewer,), backend="exec", fast_requested=False)
    changed = replace(writer_manifest, backend="exec")
    reads = []
    launched = []

    def changing_manifest(_path):
        reads.append(True)
        return reviewed if len(reads) == 1 else changed

    def fake_launch(manifest, runtime_root, helper_script, codex, **kwargs):
        launched.append((manifest, kwargs))
        return "run-id", tmp_path / "runtime" / "tasks" / "run-id"

    monkeypatch.setattr(cli, "load_manifest", changing_manifest)
    monkeypatch.setattr(cli, "_find_codex", lambda _: "fake-codex")
    monkeypatch.setattr(cli, "launch_task", fake_launch)
    monkeypatch.setattr(cli, "_codex_capabilities", lambda *_: {
        "supported_flags": {flag: True for flag in
            ("-C", "-m", "-c", "--enable", "--approve-for-me", "-s read-only", "--json", "--color", "-o")},
        "process_identity": {"available": True},
        "version": "0.162.1", "platform_coverage": "unit-test",
    })
    args = SimpleNamespace(manifest=tmp_path / "source.json", plan_mode=True, codex=None, runtime_root=str(tmp_path / "runtime"))
    assert cli._launch(args) == 0
    assert len(reads) == 1
    assert launched[0][0] is reviewed
    assert launched[0][1]["enforce_plan_mode"] is True

    with pytest.raises(common.FastAgentsError, match="Plan Mode forbids writer"):
        runtime.launch_task(changed, tmp_path / "never-created", tmp_path / "missing-helper.py", "fake", enforce_plan_mode=True)
    assert not (tmp_path / "never-created").exists()


def test_uncertain_worker_takes_precedence_over_blocked_dependent_and_keeps_recovery_alive():
    async def exercise():
        supervisor = app_server.AppServerSupervisor.__new__(app_server.AppServerSupervisor)
        supervisor.status = {"roles": {"writer": {"state": "unknown"}, "dependent": {"state": "blocked"}}}
        supervisor._save_status = lambda: None
        supervisor.finished = asyncio.Event()

        async def schedule():
            supervisor._set_terminal_task_state()

        supervisor._schedule = schedule
        await supervisor._schedule_then_finish()
        assert supervisor.status["state"] == "recovery_required"
        assert not supervisor.finished.is_set()

    asyncio.run(exercise())


def test_reconcile_restores_dependent_only_after_upstream_is_verified(tmp_path: Path):
    async def exercise():
        task_dir, _, _ = make_task(tmp_path)
        manifest = common.read_json(task_dir / "manifest.json")
        writer = manifest["roles"][0]
        # Reverse the dependency order to require more than one requeue pass.
        manifest["roles"].append({**writer, "name": "grandchild", "access": "reviewer", "owns": [], "depends_on": ["dependent"]})
        manifest["roles"].append({**writer, "name": "dependent", "access": "reviewer", "owns": [], "depends_on": ["worker0"]})
        common.atomic_write_json(task_dir / "manifest.json", manifest)
        status = common.read_json(task_dir / "status.json")
        status["roles"]["dependent"] = {**status["roles"]["worker0"]}
        status["roles"]["dependent"].update({"state": "blocked", "error": "a dependency did not complete successfully"})
        status["roles"]["grandchild"] = {**status["roles"]["dependent"]}
        status["roles"]["worker0"].update({
            "state": "unknown", "thread_id": "t-1", "turn_id": "u-1",
            "turn_start_state": "acknowledged", "turn_status": "inProgress",
        })
        common.atomic_write_json(task_dir / "status.json", status)
        supervisor = app_server.AppServerSupervisor(task_dir, tmp_path / "runtime", "unused")
        started = []

        async def request(method, params):
            if method == "thread/resume":
                return {"model": "gpt-6-luna", "sandbox": "workspace-write",
                        "approvalPolicy": "on-request", "approvalsReviewer": "auto_review", "serviceTier": "default"}
            if method == "thread/read":
                return {"thread": {"turns": [{"id": "u-1", "status": "completed",
                    "items": [{"type": "agentMessage", "text": "verified prior turn"}]}]}}
            pytest.fail(f"unexpected request: {method}")

        supervisor.client = SimpleNamespace(closed=asyncio.Event(), request=request, server_requests={})
        supervisor.connection_epoch = "test-epoch"
        supervisor._start_scheduler = lambda: started.append(True)
        result = await supervisor._reconcile()
        assert result["reconciled"] is True
        assert supervisor.status["roles"]["worker0"]["state"] == "completed"
        assert supervisor.status["roles"]["dependent"]["state"] == "waiting"
        assert supervisor.status["roles"]["dependent"]["error"] is None
        assert supervisor.status["roles"]["grandchild"]["state"] == "waiting"
        assert started == [True]

    asyncio.run(exercise())


def test_simultaneous_approval_responses_have_one_wire_winner(tmp_path: Path):
    from fast_agents_protocol import AppServerClient, AppServerError

    async def ignore(_message):
        return None

    async def exercise():
        client = AppServerClient("unused", tmp_path, tmp_path / "stderr", ignore, ignore, ignore)
        request = {"id": "req", "method": "item/commandExecution/requestApproval", "params": {}}
        client.server_requests = {"str:req": request}
        entered = asyncio.Event()
        release = asyncio.Event()
        emitted = []

        async def blocked_write(message):
            entered.set()
            await release.wait()
            emitted.append(message)

        client._write_message = blocked_write
        first = asyncio.create_task(client.respond("req", {"decision": "accept"}))
        await asyncio.wait_for(entered.wait(), 2)
        with pytest.raises(AppServerError, match="no longer pending"):
            await client.respond("req", {"decision": "decline"})
        release.set()
        await asyncio.wait_for(first, 2)
        assert emitted == [{"id": "req", "result": {"decision": "accept"}}]

    asyncio.run(exercise())


def test_supervisor_second_reply_fails_without_invalidating_in_flight_claim(tmp_path: Path):
    from fast_agents_protocol import AppServerClient
    async def ignore(_message):
        return None

    async def exercise():
        task_dir, _, _ = make_task(tmp_path)
        supervisor = app_server.AppServerSupervisor(task_dir, tmp_path / "runtime", "unused")
        supervisor.status["roles"]["worker0"].update({
            "state": "running", "thread_id": "t-1", "turn_id": "u-1", "turn_status": "inProgress",
        })
        request = {"id": "req", "method": "item/commandExecution/requestApproval",
                   "params": {"threadId": "t-1", "turnId": "u-1", "command": "echo hi"}}
        client = AppServerClient("unused", tmp_path, tmp_path / "stderr", ignore, ignore, ignore)
        client.server_requests = {"str:req": request}
        entered, release = asyncio.Event(), asyncio.Event()
        writes = []

        async def slow_write(payload):
            entered.set()
            await release.wait()
            writes.append(payload)

        client._write_message = slow_write
        supervisor.client = client
        supervisor.connection_epoch = "test-epoch"
        supervisor.pending_requests["str:req"] = {
            "request_id": "req", "method": request["method"], "thread_id": "t-1", "turn_id": "u-1",
            "role": "worker0", "transport_epoch": "test-epoch", "server_request": request, "state": "pending",
            "params": request["params"],
        }
        reply = {"request_id": "req", "thread_id": "t-1", "turn_id": "u-1"}
        first = asyncio.create_task(supervisor._reply_pending({**reply, "decision": "accept"}))
        await asyncio.wait_for(entered.wait(), 2)
        assert supervisor._public_pending(supervisor.pending_requests["str:req"])["action"] == "reply in progress"
        with pytest.raises(common.FastAgentsError, match="already in progress"):
            await supervisor._reply_pending({**reply, "decision": "decline"})
        assert "str:req" in supervisor.pending_requests
        release.set()
        assert (await asyncio.wait_for(first, 2))["acknowledged"] is True
        assert supervisor.pending_requests == {}
        assert writes == [{"id": "req", "result": {"decision": "accept"}}]

    asyncio.run(exercise())


def test_recovery_rejects_changed_or_unverified_codex_before_starting_supervisor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import fast_agents as cli
    task_dir, _, status = make_task(tmp_path)
    status["manager"] = {"identity": {"pid": 654321}}
    common.atomic_write_json(task_dir / "status.json", status)
    sentinel = task_dir / "ready.json"
    sentinel.write_text('{"ok":true}', encoding="utf-8")
    monkeypatch.setattr(runtime, "inspect_process", lambda _: ("dead", None))
    monkeypatch.setattr(cli, "_codex_capabilities", lambda *args: {
        "available": False, "version": "0.163.0", "protocol": {"error": "schema incompatible"},
    })
    with pytest.raises(common.FastAgentsError, match="recovery compatibility preflight failed"):
        runtime._start_recovery_supervisor_locked(
            status["task_id"], tmp_path / "runtime", tmp_path / "helper.py", "codex-upgraded"
        )
    assert sentinel.exists()

def test_state_projection_distinguishes_recovery_from_missing_final_output():
    classify = app_server.classify_task_roles
    assert classify({
        "writer": {"state": "unknown", "turn_status": "inProgress"},
        "dependent": {"state": "blocked"},
    })[0] == "recovery_required"
    assert classify({
        "writer": {"state": "unknown", "turn_status": "completed"},
        "dependent": {"state": "blocked"},
    })[0] == "failed"
    assert classify({
        "writer": {"state": "completed", "turn_status": "completed"},
        "dependent": {"state": "waiting"},
    })[0] == "running"
    assert classify({
        "writer": {"state": "completed"},
        "dependent": {"state": "completed"},
    })[0] == "completed"


def test_progress_event_snapshots_are_coalesced_but_terminal_status_is_durable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    async def exercise():
        task_dir, _, _ = make_task(tmp_path)
        supervisor = app_server.AppServerSupervisor(task_dir, tmp_path / "runtime", "unused")
        supervisor.role_by_thread["thread"] = "worker0"
        supervisor.status["roles"]["worker0"].update({
            "state": "running", "turn_id": "turn", "turn_start_state": "acknowledged",
            "turn_status": "inProgress",
        })
        counter = []
        real_writer = app_server.atomic_write_json

        def counting_write(path, payload, *args, **kwargs):
            counter.append(Path(path).name)
            return real_writer(path, payload, *args, **kwargs)

        monkeypatch.setattr(app_server, "atomic_write_json", counting_write)
        for _ in range(16):
            await supervisor._on_event({
                "method": "item/started",
                "params": {"threadId": "thread", "item": {"type": "commandExecution"}},
            })
        assert "status.json" not in counter
        assert not (task_dir / "pending-requests.private.json").exists()
        assert supervisor._progress_flush_task is not None
        await asyncio.wait_for(supervisor._progress_flush_task, 2)
        assert counter.count("status.json") == 1
        assert counter.count("pending-requests.private.json") == 1

        await supervisor._on_event({
            "method": "turn/completed",
            "params": {"threadId": "thread",
                       "turn": {"id": "turn", "status": "completed",
                                "items": [{"type": "agentMessage", "text": "ok"}]}},
        })
        assert counter.count("status.json") >= 2
        # No needless rewrite of unchanged pending-request authority.
        assert counter.count("pending-requests.private.json") == 1
        persisted = common.read_json(task_dir / "status.json")
        assert persisted["roles"]["worker0"]["state"] == "completed"

    asyncio.run(exercise())

def test_git_scope_audit_reports_new_paths_without_claiming_test_verification(tmp_path: Path):
    import shutil
    import fast_agents_verification as verification

    if shutil.which("git") is None:
        pytest.skip("Git unavailable")
    workspace = tmp_path / "git-workspace"
    workspace.mkdir()
    subprocess.run(["git", "init", "-q", str(workspace)], check=True)
    (workspace / "preexisting.txt").write_text("already dirty", encoding="utf-8")
    baseline = verification.snapshot_workspace(workspace)
    assert baseline["state"] == "captured"
    (workspace / "src").mkdir()
    (workspace / "src" / "owned.py").write_text("pass", encoding="utf-8")
    (workspace / "src" / "not-owned.py").write_text("needs review", encoding="utf-8")
    scoped = verification.audit_workspace_scope(
        workspace, baseline, [{"access": "writer", "owns": ["src/owned.py"]}]
    )
    assert scoped["state"] == "new_outside_declared_paths"
    assert scoped["outside_declared_paths"] == ["src/not-owned.py"]
    assert scoped["preexisting_dirty_count"] == 1
    assert scoped["new_changed_count"] == 2
    assert "preexisting.txt" not in scoped["new_changed_paths"]
    assert verification.audit_workspace_scope(
        workspace, baseline, [{"access": "writer", "owns": ["src"]}]
    )["state"] == "within_declared_paths"
    nested = verification.snapshot_workspace(workspace / "src")
    assert nested["state"] == "unavailable"


def test_git_scope_audit_gracefully_degrades_without_git(tmp_path: Path):
    import fast_agents_verification as verification

    workspace = tmp_path / "not-a-repository"
    workspace.mkdir()
    baseline = verification.snapshot_workspace(workspace)
    assert baseline["state"] == "unavailable"
    state = verification.audit_workspace_scope(
        workspace, baseline, [{"access": "writer", "owns": ["src/owned.py"]}]
    )
    assert state["state"] == "inconclusive"
    assert verification.audit_workspace_scope(workspace, baseline, [
        {"access": "reviewer", "owns": []},
    ])["state"] == "not_applicable"

def test_codex_only_manifest_scope_is_machine_validatable():
    skill = (ROOT / "skills" / "fast-codex-subagents" / "SKILL.md").read_text(encoding="utf-8")
    catalog = json.loads((ROOT / "skills-index.json").read_text(encoding="utf-8"))
    entry = next(item for item in catalog["skills"] if item["name"] == "fast-codex-subagents")
    assert 'agents: ["codex"]' in skill
    assert entry["agents"] == ["codex"]
