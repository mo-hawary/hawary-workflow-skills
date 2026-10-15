"""Authenticated Codex-only live smoke with metadata-only transcript output.

Use a self-hosted runner signed in with ChatGPT; never an API key or PR CI.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills" / "fast-codex-subagents" / "scripts"))
from fast_agents_protocol import AppServerClient, catalog_efforts, catalog_tiers, tested_version_error  # noqa: E402

MARKER = "CODEX_SKILL_SMOKE_OK"


def scrub(kind, method, data=None):
    data = data if isinstance(data, dict) else {}
    turn = data.get("turn") if isinstance(data.get("turn"), dict) else {}
    thread = data.get("thread") if isinstance(data.get("thread"), dict) else {}
    settings = data.get("threadSettings") if isinstance(data.get("threadSettings"), dict) else {}
    item = data.get("item") if isinstance(data.get("item"), dict) else {}
    record = {"kind": kind, "method": method}
    for field, value in (("thread", data.get("threadId") or thread.get("id")),
                         ("turn", data.get("turnId") or turn.get("id"))):
        if isinstance(value, str):
            record[field] = "id-" + hashlib.sha256(value.encode()).hexdigest()[:12]
    for field, value in (
        ("model", data.get("model")), ("service_tier", data.get("serviceTier")),
        ("turn_tier", data.get("serviceTierForTurn")),
        ("settings_tier", settings.get("serviceTier")),
        ("status", turn.get("status")), ("item_type", item.get("type")),
    ):
        if isinstance(value, (str, bool, int)) and len(str(value)) <= 100:
            record[field] = value
    return record


async def live_smoke(codex: str, output: Path):
    # Never fall back to API-key billing when a ChatGPT-plan smoke was requested.
    if os.environ.get("OPENAI_API_KEY") or os.environ.get("CODEX_API_KEY"):
        raise RuntimeError("ChatGPT-only smoke refuses OPENAI_API_KEY/CODEX_API_KEY; remove API credentials")
    login = subprocess.run(
        [codex, "login", "status"], capture_output=True, text=True, timeout=10, check=False
    )
    if login.returncode != 0 or "chatgpt" not in (login.stdout + login.stderr).lower():
        raise RuntimeError(
            "Codex is not signed into ChatGPT for this runner user. Run codex login "
            "on the self-hosted runner, then verify codex login status; no API key is needed"
        )
    version_result = subprocess.run([codex, "--version"], capture_output=True, text=True, check=True, timeout=10)
    match = re.search(r"codex-cli\s+(\S+)", version_result.stdout + version_result.stderr)
    version = match.group(1) if match else "unknown"
    if (error := tested_version_error(version)):
        raise RuntimeError(error)
    output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    records = []
    protocol_errors = []
    done = asyncio.Event()
    passed = False
    effective = None

    async def on_event(message):
        method = str(message.get("method", "unknown"))
        params = message.get("params")
        records.append(scrub("event", method, params))
        if method == "turn/completed":
            done.set()

    async def on_server_request(message):
        method = str(message.get("method", "unknown"))
        records.append(scrub("server_request", method, message.get("params")))
        protocol_errors.append("Unexpected interactive request (not approved)")
        request_id = message.get("id")
        try:
            if isinstance(request_id, (str, int)) and not isinstance(request_id, bool):
                # Answer right away so the smoke fails fast instead of waiting for the turn timeout.
                if method in {"item/commandExecution/requestApproval", "item/fileChange/requestApproval"}:
                    await client.respond(request_id, {"decision": "decline"})
                else:
                    await client.reject_server_request(request_id, "smoke does not approve interactive requests")
        finally:
            done.set()

    async def on_error(message):
        protocol_errors.append("Protocol reader reported a malformed or unexpected message")

    client = AppServerClient(codex, ROOT, output.parent / "codex-stderr.private.log",
                             on_event, on_server_request, on_error)
    real_request = client.request

    async def recorded_request(method, params=None, timeout=30.0):
        records.append(scrub("request", method, params))
        response = await real_request(method, params, timeout)
        records.append(scrub("response", method, response))
        return response

    client.request = recorded_request
    try:
        await client.start()
        catalog = await client.request("model/list", {"limit": 100})
        models = [m for m in catalog.get("data", []) if isinstance(m, dict)
                  and isinstance(m.get("id"), str)
                  and "priority" in catalog_tiers(m) and catalog_efforts(m)]
        if not models:
            raise RuntimeError("No accessible priority-tier Codex model in model/list")
        models.sort(key=lambda m: (m["id"] != "gpt-6-luna", m["id"]))
        model = models[0]
        effort = next((e for e in ("low", "medium", "high", "xhigh", "max")
                       if e in catalog_efforts(model)), sorted(catalog_efforts(model))[0])
        skill = (ROOT / "skills" / "fast-codex-subagents" / "SKILL.md").read_text(encoding="utf-8")
        started = await client.request("thread/start", {
            "model": model["id"], "cwd": str(ROOT), "ephemeral": False,
            "sandbox": "read-only", "approvalPolicy": "on-request",
            "approvalsReviewer": "auto_review", "serviceTier": "priority",
            "developerInstructions": "Read this Codex skill and answer a trivial smoke without commands or network:\n" + skill,
        })
        effective = started.get("serviceTier")
        if effective != "priority":
            raise RuntimeError("Requested Fast tier was not effective on thread/start")
        thread = started.get("thread") if isinstance(started.get("thread"), dict) else {}
        thread_id = thread.get("id")
        if not isinstance(thread_id, str):
            raise RuntimeError("thread/start returned no thread ID")
        started_turn = await client.request("turn/start", {
            "threadId": thread_id,
            "input": [{"type": "text", "text": "Reply exactly CODEX_SKILL_SMOKE_OK. Do not use tools.", "text_elements": []}],
            "effort": effort, "serviceTierForTurn": "priority",
            "sandboxPolicy": {"type": "readOnly", "networkAccess": False},
        })
        turn = started_turn.get("turn") if isinstance(started_turn.get("turn"), dict) else {}
        turn_id = turn.get("id")
        if not isinstance(turn_id, str):
            raise RuntimeError("turn/start returned no turn ID")
        if turn.get("status") in {"completed", "failed", "interrupted"}:
            done.set()
        await asyncio.wait_for(done.wait(), 180)
        if protocol_errors:
            raise RuntimeError("; ".join(protocol_errors))
        history = await client.request("thread/read", {"threadId": thread_id, "includeTurns": True})
        returned = history.get("thread") if isinstance(history.get("thread"), dict) else {}
        matches = [x for x in returned.get("turns", [])
                   if isinstance(x, dict) and x.get("id") == turn_id]
        if len(matches) != 1 or matches[0].get("status") != "completed":
            raise RuntimeError("Trivial Codex turn did not complete")
        messages = [str(x.get("text", "")) for x in matches[0].get("items", [])
                    if isinstance(x, dict) and x.get("type") == "agentMessage"]
        if not any(MARKER in msg for msg in messages):
            raise RuntimeError("Codex model did not return the skill smoke marker")
        passed = True
        print("Live Codex smoke passed; requested priority, effective priority, provider tier unverified")
    finally:
        payload = {
            "schema_version": 1, "source": "real_codex_live_smoke",
            "codex_version": version, "real_model_session": True,
            "passed": passed, "requested_fast_tier": "priority",
            "effective_thread_tier": effective,
            "upstream_execution_verified": False,
            "records": records[:2000], "record_count": len(records),
            "note": "Metadata-only; no prompts, tool arguments, credentials, or raw model text.",
        }
        output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        if client.process is not None:
            await client.close_stdin_and_wait()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--codex", default=shutil.which("codex"))
    parser.add_argument("--capture", type=Path, required=True)
    args = parser.parse_args()
    if args.codex is None:
        parser.error("Codex CLI is not installed")
    asyncio.run(live_smoke(args.codex, args.capture))
