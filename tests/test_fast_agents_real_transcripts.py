"""Replay audited real Codex transcript golden envelopes.

No real transcript has been captured in this environment; tests skip instead
of mislabeling a fake-server recording as a genuine Codex session.
"""
from __future__ import annotations

import json
import asyncio
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]
CAPTURES = sorted((ROOT / "tests" / "fixtures" / "fast_agents" / "transcripts" / "recorded").glob("*/*.json"))


@pytest.mark.parametrize("path", CAPTURES if CAPTURES else [None])
def test_recorded_live_codex_transcript(path, tmp_path):
    if path is None:
        pytest.skip("Authenticated, sanitized real Codex transcript not yet reviewed/promoted")
    raw = path.read_text(encoding="utf-8")
    payload = json.loads(raw)
    assert payload["schema_version"] == 1
    assert payload["source"] in {"real_codex_live_smoke", "real_codex_helper_smoke"}
    assert payload["codex_version"].removeprefix("codex-cli ") == path.parent.name
    assert payload["real_model_session"] is True
    assert payload["passed"] is True
    if payload["source"] == "real_codex_live_smoke":
        assert payload["requested_fast_tier"] == "priority"
        assert payload["effective_thread_tier"] == "priority"
    else:
        assert payload["verification_state"] == "passed"
        assert payload["source_dirty"] is False
        assert len(payload["git_sha"]) == 40
        assert len(payload["helper_sha256"]) == 64
        assert len(payload["roles"]) == 2
        assert all(role["state"] == "completed" and role["effective_tier"] == "priority" for role in payload["roles"])
    assert payload["upstream_execution_verified"] is False
    records = payload["records"]
    assert 2 <= len(records) <= 2000
    allowed_keys = {"kind", "method", "thread", "turn", "model", "service_tier",
                    "turn_tier", "settings_tier", "status", "item_type"}
    for record in records:
        assert set(record).issubset(allowed_keys)
        assert record["kind"] in {"request", "response", "event", "server_request"}
    methods = {(row["kind"], row["method"]) for row in records}
    if payload["source"] == "real_codex_live_smoke":
        assert ("request", "thread/start") in methods
        assert ("response", "thread/start") in methods
        assert ("request", "turn/start") in methods
    assert ("event", "turn/completed") in methods
    for forbidden in ("OPENAI_API_KEY", "Authorization:", "auth.json",
                      "developerInstructions", "clientSecret"):
        assert forbidden not in raw
    replay_supervisor_projection(records, tmp_path)


def replay_supervisor_projection(records, tmp_path):
    """Replay real envelope ordering through runtime state transitions.

    Payload bodies are deliberately safe reconstructions, not original model
    text or full wire replay. Thread/turn correlations and terminal states come
    from the audited capture. Full payload variants belong to fake fixtures.
    """
    import sys
    from types import SimpleNamespace
    scripts = ROOT / "skills" / "fast-codex-subagents" / "scripts"
    sys.path.insert(0, str(scripts))
    from fast_agents_app_server import AppServerSupervisor
    from fast_agents_common import atomic_write_json
    threads = sorted({row["thread"] for row in records if row.get("thread")})
    assert 1 <= len(threads) <= 8
    roles = [{"name": f"worker{index}", "access": "reviewer", "model": "gpt-6-luna", "effort": "low",
              "outcome": "sanitized replay", "depends_on": []} for index in range(len(threads))]
    task = tmp_path / "task"
    task.mkdir()
    atomic_write_json(task / "manifest.json", {"workspace": str(tmp_path), "objective": "replay",
                                              "shared_contract": "replay", "roles": roles})
    states = {role["name"]: {"state": "running", "thread_id": thread, "turn_start_state": "submitted",
                             "turn_status": "inProgress"} for role, thread in zip(roles, threads)}
    atomic_write_json(task / "status.json", {"roles": states})
    supervisor = AppServerSupervisor(task, tmp_path, "unused")
    supervisor.role_by_thread = {thread: role["name"] for role, thread in zip(roles, threads)}

    async def replay():
        supervisor.client = SimpleNamespace(server_requests={})
        for row in records:
            if row["kind"] != "event" or not row.get("thread"):
                continue
            method = row["method"]
            params = {"threadId": row["thread"]}
            if row.get("turn"):
                params["turnId"] = row["turn"]
            if method in {"turn/started", "turn/completed"}:
                assert row.get("turn")
                params["turn"] = {"id": row["turn"], "status": row.get("status", "inProgress"), "items": []}
                if row.get("status") == "completed":
                    params["turn"]["items"] = [{"type": "agentMessage", "text": "safe reconstructed report"}]
            if method == "thread/settings/updated":
                params["threadSettings"] = {"serviceTier": row.get("settings_tier")}
            await supervisor._on_event({"method": method, "params": params})
        assert all(state["state"] == "completed" for state in supervisor.status["roles"].values())
        assert all(event.is_set() for event in supervisor.role_done.values())
        if supervisor._progress_flush_task:
            await supervisor._progress_flush_task
    asyncio.run(replay())


def test_projection_replay_exercises_terminal_correlations(tmp_path):
    records = [
        {"kind": "event", "method": "turn/started", "thread": "safe-thread", "turn": "safe-turn", "status": "inProgress"},
        {"kind": "event", "method": "turn/completed", "thread": "safe-thread", "turn": "stale-turn", "status": "failed"},
        {"kind": "event", "method": "turn/completed", "thread": "safe-thread", "turn": "safe-turn", "status": "completed"},
    ]
    replay_supervisor_projection(records, tmp_path)

def test_live_capture_scrubs_commands_prompts_credentials_and_turn_identifiers():
    import sys
    script_dir = str(ROOT / "tests")
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)
    from live_codex_smoke import scrub
    raw = {
        "threadId": "private-thread-id",
        "turnId": "private-turn-id",
        "model": "gpt-6-luna",
        "serviceTier": "priority",
        "developerInstructions": "do not publish this",
        "command": "read all secrets",
        "item": {"type": "agentMessage", "text": "raw model output"},
    }
    record = scrub("event", "turn/completed", raw)
    assert record["thread"].startswith("id-")
    assert record["turn"].startswith("id-")
    assert record["model"] == "gpt-6-luna"
    assert record["service_tier"] == "priority"
    encoded = json.dumps(record)
    assert "private-thread-id" not in encoded
    assert "private-turn-id" not in encoded
    assert "do not publish" not in encoded
    assert "read all secrets" not in encoded
    assert "raw model output" not in encoded


def test_live_workflow_is_non_pr_and_requires_authentication():
    workflow = (ROOT / ".github" / "workflows" / "codex-live-smoke.yml").read_text(encoding="utf-8")
    assert "workflow_dispatch:" in workflow
    assert "schedule:" in workflow
    assert "branches: [main]" in workflow
    assert "pull_request:" not in workflow
    assert "CODEX_SMOKE_API_KEY" not in workflow
    assert "OPENAI_API_KEY: ${{ secrets." not in workflow
    assert "CODEX_LIVE_SMOKE_ENABLED" in workflow
    assert "self-hosted" in workflow
    assert "codex-authenticated" in workflow
    assert "codex login status" in workflow
