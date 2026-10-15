"""Regression tests for lifecycle, configuration, limits, and isolated integration."""
from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import sys
import time
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills" / "fast-codex-subagents" / "scripts"))
import fast_agents_app_server as app_server
import fast_agents_common as common
from fast_agents_limits import observed_tokens, validate_budgets
from fast_agents_protocol import AppServerError
from fast_agents_worktrees import WorktreeSession, git, head, integration_preview, run_acceptance
from test_fast_agents import write_manifest, writer, reviewer
from test_fast_agents_turn_and_identity import make_task


def supervisor(tmp_path):
    task = make_task(tmp_path, backend="app-server", names=["writer"])
    result = app_server.AppServerSupervisor(task, tmp_path / "runtime", "unused")
    result.status["roles"]["writer"].update(state="running", thread_id="thread-a", turn_id="turn-a",
                                            turn_status="inProgress", turn_start_state="acknowledged")
    result.role_by_thread["thread-a"] = "writer"
    return result


def test_unknown_request_does_not_finish_live_worker_or_disable_interrupt(tmp_path):
    async def scenario():
        service = supervisor(tmp_path)
        calls = []
        async def reject(request_id, reason):
            calls.append((request_id, reason))
        async def request(method, params):
            calls.append((method, params))
            return {}
        service.client = SimpleNamespace(closed=asyncio.Event(), reject_server_request=reject,
                                         request=request, server_requests={})
        await service._on_server_request({"id": 7, "method": "future/approval", "params": {
            "threadId": "thread-a", "turnId": "turn-a"}})
        state = service.status["roles"]["writer"]
        assert state["state"] == "running"
        assert state["turn_status"] == "inProgress"
        assert not service.role_done["writer"].is_set()
        assert not (service.task_dir / "agents" / "writer" / "exit.json").exists()
        response = await service._handle_control({"command": "interrupt", "role": "writer", "turn_id": "turn-a"})
        assert response["acknowledged"]
        assert calls[-1][0] == "turn/interrupt"
        assert state["state"] == "running"  # Acknowledgement still isn't terminal evidence.
        service._finish_from_turn("writer", {"id": "turn-a", "status": "interrupted", "items": []})
        assert service.role_done["writer"].is_set()
    asyncio.run(scenario())


@pytest.mark.parametrize("value", [True, 0, -1, float("inf"), float("nan"), "10"])
def test_invalid_budgets_rejected(value):
    with pytest.raises(common.FastAgentsError):
        validate_budgets({"max_wall_seconds": value}, "budget")


def test_model_profiles_and_supervisor_features_are_preserved(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    path = write_manifest(tmp_path / "manifest.json", workspace,
                          [writer(model="gpt-6.1-sol", effort="high")], backend="auto", fast_requested=False)
    raw = common.read_json(path)
    raw.update(budgets={"max_wall_seconds": 60, "action": "interrupt"},
               acceptance=[[sys.executable, "-c", "print('ok')"]])
    common.atomic_write_json(path, raw)
    manifest = common.load_manifest(path, defaults_path=tmp_path / "absent")
    assert manifest.roles[0].model == "gpt-6.1-sol"
    assert manifest.roles[0].effort == "high"
    assert manifest.backend == "app-server"
    assert manifest.budgets["max_wall_seconds"] == 60
    raw["backend"] = "exec"
    common.atomic_write_json(path, raw)
    with pytest.raises(common.FastAgentsError, match="require the app-server"):
        common.load_manifest(path, defaults_path=tmp_path / "absent")


def test_custom_model_defaults_preview_save_and_launch_contract(tmp_path):
    import fast_agents_setup as setup
    target = tmp_path / "defaults.json"
    profiles = {"writer": {"model": "gpt-6.1-sol", "effort": "high"},
                "reviewer": {"model": "gpt-6-luna", "effort": "medium"}}
    _, data, before, after = setup.defaults_preview(target, profiles=profiles)
    setup.save_defaults(target, None, None, apply=True, expected_before=before or "absent", confirm_sha=after, profiles=profiles)
    assert common.load_defaults(target) == data
    assert data["writer"] == profiles["writer"]


def test_token_budget_does_not_double_count_cached_input_or_invent_telemetry():
    assert observed_tokens({"total": {"totalTokens": 100, "cachedInputTokens": 60}}) == 100
    assert observed_tokens(None) is None
    assert observed_tokens({"total": {"totalTokens": True}}) is None


@pytest.mark.parametrize("action", ["warn", "interrupt"])
def test_budget_alert_and_interrupt_wait_for_terminal_evidence(tmp_path, action):
    async def scenario():
        service = supervisor(tmp_path)
        service.manifest["budgets"] = {"max_tokens": 100, "action": action}
        state = service.status["roles"]["writer"]
        state.update(started_at=dt.datetime.now(dt.timezone.utc).isoformat(), usage={"total": {"totalTokens": 110}})
        calls = []
        async def request(method, params):
            calls.append(method)
            return {}
        service.client = SimpleNamespace(request=request, closed=asyncio.Event())
        await service._check_budgets()
        await service._check_budgets()
        assert calls == (["turn/interrupt"] if action == "interrupt" else [])
        assert state["state"] == "running"
        assert not service.role_done["writer"].is_set()
        assert state["budget"]["alerts"] == ["max_tokens"]
    asyncio.run(scenario())


def test_uncertain_budget_interrupt_is_not_replayed_and_requires_reconciliation(tmp_path, monkeypatch):
    async def scenario():
        service = supervisor(tmp_path)
        service.manifest["budgets"] = {"max_wall_seconds": 1, "action": "interrupt"}
        service.status["created_at"] = "2000-01-01T00:00:00Z"
        calls = []
        async def request(method, params):
            calls.append(method)
            raise AppServerError("reply lost")
        service.client = SimpleNamespace(request=request, closed=asyncio.Event())
        await service._check_budgets()
        service.status["roles"]["writer"]["budget"]["interrupt_at"] = "2000-01-01T00:00:00Z"
        await service._check_budgets()
        assert calls == ["turn/interrupt"]
        assert service.transport_lost.is_set()
        assert not service.role_done["writer"].is_set()
    asyncio.run(scenario())


def source_tree(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    git(source, "init")
    (source / "one.txt").write_text("original\n")
    git(source, "add", ".")
    git(source, "-c", "user.name=Fixture", "-c", "user.email=fixture@localhost", "commit", "-m", "baseline")
    task = tmp_path / "task"
    task.mkdir()
    session = WorktreeSession(task, source)
    session.prepare()
    return source, task, session


@pytest.mark.skipif(os.name == "nt", reason="host acceptance requires POSIX process groups")
def test_isolated_writers_merge_and_acceptance_gates_explicit_application(tmp_path):
    source, task, session = source_tree(tmp_path)
    for name, file in (("left", "one.txt"), ("right", "two.txt")):
        path = session.start_role(name, "writer")
        (path / file).write_text(name + "\n")
        (task / "agents" / name).mkdir(parents=True)
        session.collect(name, [file])
    integration = session.start_role("review", "reviewer")
    assert (integration / "one.txt").read_text() == "left\n"
    assert (integration / "two.txt").read_text() == "right\n"
    assert (source / "one.txt").read_text() == "original\n"
    patch_sha = hashlib.sha256(session.final_patch()).hexdigest()
    common.atomic_write_json(task / "manifest.json", {"workspace": str(source), "isolation": "worktree"})
    status = {"state": "completed", "verification_state": "not_run"}
    common.atomic_write_json(task / "status.json", status)
    with pytest.raises(common.FastAgentsError, match="passing acceptance"):
        integration_preview(task)
    result = run_acceptance(task, integration, [[sys.executable, "-c", "from pathlib import Path; assert Path('two.txt').read_text() == 'right\\n'"]])
    assert result["state"] == "passed"
    result["patch_sha256"] = patch_sha
    status.update(verification_state="passed", acceptance=result)
    common.atomic_write_json(task / "status.json", status)
    assert integration_preview(task)["patch_sha256"] == patch_sha
    with pytest.raises(common.FastAgentsError, match="confirmation"):
        integration_preview(task, apply=True, confirm="wrong")
    assert integration_preview(task, apply=True, confirm=patch_sha)["applied"]
    assert (source / "two.txt").read_text() == "right\n"


def test_isolated_patch_outside_ownership_never_reaches_integration(tmp_path):
    source, task, session = source_tree(tmp_path)
    path = session.start_role("writer", "writer")
    (path / "one.txt").write_text("unexpected")
    (task / "agents" / "writer").mkdir(parents=True)
    with pytest.raises(common.FastAgentsError, match="outside ownership"):
        session.collect("writer", ["allowed.txt"])
    assert (session.start_role("review", "reviewer") / "one.txt").read_text() == "original\n"
    assert (source / "one.txt").read_text() == "original\n"


def test_worktree_reviewer_requires_all_writers_and_dirty_source_is_preserved(tmp_path):
    source, task, session = source_tree(tmp_path)
    path = write_manifest(tmp_path / "manifest.json", source,
                          [writer("one"), writer("two"), reviewer(depends_on=["one"])], backend="auto")
    raw = common.read_json(path)
    raw["isolation"] = "worktree"
    common.atomic_write_json(path, raw)
    with pytest.raises(common.FastAgentsError, match="depend on all writers"):
        common.load_manifest(path, defaults_path=tmp_path / "absent")
    (source / "one.txt").write_text("user work")
    other_task = tmp_path / "other-task"
    other_task.mkdir()
    with pytest.raises(common.FastAgentsError, match="clean source tree"):
        WorktreeSession(other_task, source).prepare()
    assert (source / "one.txt").read_text() == "user work"


@pytest.mark.skipif(os.name == "nt", reason="host acceptance requires POSIX process groups")
def test_acceptance_timeout_stops_owned_descendants_and_keeps_existing_log(tmp_path):
    task = tmp_path / "task"
    task.mkdir()
    child = "import time; from pathlib import Path; time.sleep(.4); Path('late-write').write_text('bad')"
    parent = f"import subprocess,sys,time; subprocess.Popen([sys.executable,'-c',{child!r}]); time.sleep(5)"
    result = run_acceptance(task, tmp_path, [[sys.executable, "-c", parent]], timeout=.1)
    assert result["state"] == "failed"
    time.sleep(.5)
    assert not (tmp_path / "late-write").exists()
    before = (task / "acceptance-0.private.log").read_bytes()
    result = run_acceptance(task, tmp_path, [[sys.executable, "-c", "raise Exception('should not run')"]])
    assert result["state"] == "failed"
    assert (task / "acceptance-0.private.log").read_bytes() == before


def test_recovered_completed_writer_is_collected_before_task_finishes(tmp_path):
    async def scenario():
        source, task, session = source_tree(tmp_path)
        path = session.start_role("writer", "writer")
        (path / "one.txt").write_text("recovered\n")
        common.atomic_write_json(task / "manifest.json", {"workspace": str(source), "isolation": "worktree",
            "objective": "fixture", "shared_contract": "fixture", "max_concurrency": 1,
            "roles": [writer("writer", ["one.txt"])]})
        common.atomic_write_json(task / "status.json", {"state": "running", "roles": {"writer": {
            "state": "completed", "turn_status": "completed", "final_report_available": True}},
            "workspace_baseline": {"state": "captured", "paths": []}})
        service = app_server.AppServerSupervisor(task, tmp_path, "unused")
        service.client = SimpleNamespace(closed=asyncio.Event())
        await service._schedule()
        assert service.status["roles"]["writer"]["patch_collected"]
        assert (session._tree("integration") / "one.txt").read_text() == "recovered\n"
    asyncio.run(scenario())


def test_scheduler_exception_is_reported_and_releases_waiter_when_turns_are_terminal(tmp_path):
    async def scenario():
        service = supervisor(tmp_path)
        service.status["roles"]["writer"].update(state="completed", turn_status="completed")
        async def fail():
            raise common.FastAgentsError("verification could not read the isolated patch")
        service._schedule = fail
        await service._schedule_then_finish()
        assert service.finished.is_set()
        assert service.status["state"] == "failed"
        assert "verification" in service.status["action"]
    asyncio.run(scenario())


def test_reconciliation_restores_interrupt_control_for_proven_active_turn(tmp_path):
    async def scenario():
        service = supervisor(tmp_path)
        state = service.status["roles"]["writer"]
        state["state"] = "unknown"
        service.role_done["writer"].set()
        async def request(method, params):
            if method == "thread/resume":
                return {"model": "gpt-6-luna", "sandbox": "workspace-write", "approvalPolicy": "on-request",
                        "approvalsReviewer": "auto_review", "serviceTier": "default"}
            return {"thread": {"turns": [{"id": "turn-a", "status": "inProgress"}]}}
        service.client = SimpleNamespace(request=request, closed=asyncio.Event())
        unresolved = []
        await service._reconcile_role("writer", service.roles["writer"], unresolved)
        assert unresolved
        assert service._require_current_turn("writer", "turn-a") is state
        assert not service.role_done["writer"].is_set()
    asyncio.run(scenario())


def test_live_benchmark_dry_run_never_starts_a_process_and_balances_order(monkeypatch, capsys):
    import benchmark_codex_workflows as benchmark
    monkeypatch.setattr(sys, "argv", ["benchmark_codex_workflows.py", "--rounds", "8"])
    def forbidden(*args, **kwargs):
        raise AssertionError("dry run must never start a process")
    monkeypatch.setattr(benchmark.subprocess, "run", forbidden)
    benchmark.main()
    output = json.loads(capsys.readouterr().out)
    assert output["model_sessions_started"] == 0
    for mode in benchmark.MODES:
        positions = [row["order"] for row in output["trials"] if row["mode"] == mode]
        assert sorted(positions) == [0, 0, 1, 1, 2, 2, 3, 3]
    summary = benchmark.summarize([{"mode": "direct", "pass": True, "wall_seconds": 2},
                                   {"mode": "direct", "pass": False, "wall_seconds": 50}])
    assert summary["direct"]["failed"] == 1
    assert summary["direct"]["median_seconds"] == 2
