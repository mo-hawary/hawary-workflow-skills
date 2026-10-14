"""Replay audited real Codex transcript golden envelopes.

No real transcript has been captured in this environment; tests skip instead
of mislabeling a fake-server recording as a genuine Codex session.
"""
from __future__ import annotations

import json
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]
CAPTURES = sorted((ROOT / "tests" / "fixtures" / "fast_agents" / "transcripts" / "recorded").glob("*/*.json"))


@pytest.mark.parametrize("path", CAPTURES if CAPTURES else [None])
def test_recorded_live_codex_transcript(path):
    if path is None:
        pytest.skip("Authenticated, sanitized real Codex transcript not yet reviewed/promoted")
    raw = path.read_text(encoding="utf-8")
    payload = json.loads(raw)
    assert payload["schema_version"] == 1
    assert payload["source"] == "real_codex_live_smoke"
    assert payload["codex_version"] == path.parent.name
    assert payload["real_model_session"] is True
    assert payload["passed"] is True
    assert payload["requested_fast_tier"] == "priority"
    assert payload["effective_thread_tier"] == "priority"
    assert payload["upstream_execution_verified"] is False
    records = payload["records"]
    assert 2 <= len(records) <= 2000
    allowed_keys = {"kind", "method", "thread", "turn", "model", "service_tier",
                    "turn_tier", "settings_tier", "status", "item_type"}
    for record in records:
        assert set(record).issubset(allowed_keys)
        assert record["kind"] in {"request", "response", "event", "server_request"}
    methods = {(row["kind"], row["method"]) for row in records}
    assert ("request", "thread/start") in methods
    assert ("response", "thread/start") in methods
    assert ("request", "turn/start") in methods
    assert ("event", "turn/completed") in methods
    for forbidden in ("OPENAI_API_KEY", "Authorization:", "auth.json",
                      "developerInstructions", "clientSecret"):
        assert forbidden not in raw

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
