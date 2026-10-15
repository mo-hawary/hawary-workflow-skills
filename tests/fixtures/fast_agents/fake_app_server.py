#!/usr/bin/env python3
"""Cross-platform fake Codex CLI/app-server for protocol and supervisor tests."""

from __future__ import annotations

import json
import os
import shutil
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any


LOCK = threading.RLock()
STATE_PATH = Path(os.environ.get("FAST_APP_SERVER_STATE", "fake-app-server-state.json"))
SCHEMA_SOURCE = Path(os.environ.get("FAST_APP_SERVER_SCHEMA_SOURCE", ""))
DELAY = float(os.environ.get("FAST_APP_SERVER_DELAY", "0.05"))
LOSS_MODE = os.environ.get("FAST_APP_SERVER_LOSS", "")
LOSS_TURN = int(os.environ.get("FAST_APP_SERVER_LOSS_TURN", "1"))
REQUEST_MODE = os.environ.get("FAST_APP_SERVER_REQUEST", "")
REQUEST_MARKER = os.environ.get("FAST_APP_SERVER_REQUEST_MARKER", "")
MALFORMED_EVENT = os.environ.get("FAST_APP_SERVER_MALFORMED", "")
EFFECTIVE_TIER = os.environ.get("FAST_APP_SERVER_EFFECTIVE_TIER")
NO_PRIORITY = os.environ.get("FAST_APP_SERVER_NO_PRIORITY") == "1"
NO_FAST_FEATURE = os.environ.get("FAST_APP_SERVER_NO_FAST_FEATURE") == "1"
REQUEST_THEN_EXIT = os.environ.get("FAST_APP_SERVER_REQUEST_THEN_EXIT") == "1"


def _read_state() -> dict[str, Any]:
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"threads": {}, "counts": {}, "requests": []}


def _save_state(state: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = STATE_PATH.with_suffix(STATE_PATH.suffix + ".tmp")
    temporary.write_text(json.dumps(state), encoding="utf-8")
    temporary.replace(STATE_PATH)


def _send(message: dict[str, Any]) -> None:
    with LOCK:
        sys.stdout.write(json.dumps(message, separators=(",", ":")) + "\n")
        sys.stdout.flush()


def _reply(request_id: Any, result: dict[str, Any] | None = None, error: dict[str, Any] | None = None) -> None:
    response: dict[str, Any] = {"id": request_id}
    response["error" if error is not None else "result"] = error if error is not None else result or {}
    _send(response)


def _event(method: str, params: dict[str, Any]) -> None:
    _send({"method": method, "params": params})


def _models() -> list[dict[str, Any]]:
    tiers = [] if NO_PRIORITY else ["priority"]
    return [
        {"id": "gpt-6-luna", "supportedReasoningEfforts": ["low", "max"], "serviceTiers": tiers, "defaultServiceTier": None},
        {"id": "gpt-6.1-sol", "supportedReasoningEfforts": ["xhigh"], "serviceTiers": tiers, "defaultServiceTier": None},
        {"id": "gpt-6-astra", "supportedReasoningEfforts": ["xhigh"], "serviceTiers": tiers, "defaultServiceTier": None},
    ]


def _thread_settings(params: dict[str, Any]) -> dict[str, Any]:
    effective_tier = EFFECTIVE_TIER if EFFECTIVE_TIER is not None else params.get("serviceTier")
    if EFFECTIVE_TIER is None and params.get("serviceTier") == "default":
        effective_tier = None
    settings = {
        "model": params.get("model"),
        "cwd": params.get("cwd"),
        "sandbox": params.get("sandbox"),
        "approvalPolicy": params.get("approvalPolicy"),
        "approvalsReviewer": params.get("approvalsReviewer"),
        "serviceTier": effective_tier,
    }
    return settings | {
        "thread": {"id": str(uuid.uuid4()), "status": {"type": "idle"}, "turns": []},
        "settings": settings,
    }


def _response_mismatch(method: str, params: dict[str, Any], result: Any) -> str | None:
    if not isinstance(result, dict):
        return "response must be an object"
    if method in {"item/commandExecution/requestApproval", "item/fileChange/requestApproval"}:
        if set(result) != {"decision"} or result.get("decision") not in {"accept", "acceptForSession", "decline", "cancel"}:
            return "approval response does not match the method schema"
        return None
    if method == "item/permissions/requestApproval":
        if set(result) - {"permissions", "scope", "strictAutoReview"} or not isinstance(result.get("permissions"), dict):
            return "permissions response does not match the method schema"
        if result.get("scope", "turn") not in {"turn", "session"}:
            return "permissions response has an invalid scope"
        if result.get("strictAutoReview") is not None and not isinstance(result.get("strictAutoReview"), bool):
            return "permissions response has an invalid strictAutoReview value"
        if result.get("strictAutoReview") is True and result.get("scope", "turn") == "session":
            return "strictAutoReview=true requires turn scope"
        requested = params.get("permissions")
        granted = result["permissions"]
        if not isinstance(requested, dict) or set(granted) - set(requested) or set(granted) - {"fileSystem", "network"}:
            return "permissions response grants an unrequested permission category"
        if "network" in granted and granted["network"] is not None:
            requested_network = requested.get("network")
            network = granted["network"]
            if (
                not isinstance(requested_network, dict)
                or not isinstance(network, dict)
                or set(network) - {"enabled"}
                or not isinstance(requested_network.get("enabled"), bool)
                or not isinstance(network.get("enabled"), bool)
                or (network["enabled"] and not requested_network["enabled"])
            ):
                return "permissions response widens the requested network values"
        if "fileSystem" in granted and granted["fileSystem"] is not None:
            requested_fs = requested.get("fileSystem")
            filesystem = granted["fileSystem"]
            if not isinstance(requested_fs, dict) or not isinstance(filesystem, dict):
                return "permissions response has malformed filesystem values"
            if set(filesystem) - {"entries", "globScanMaxDepth", "read", "write"}:
                return "permissions response has an unknown filesystem field"
            for field in ("read", "write"):
                if field in filesystem and (
                    not isinstance(filesystem[field], list)
                    or not isinstance(requested_fs.get(field), list)
                    or not set(filesystem[field]).issubset(set(requested_fs[field]))
                ):
                    return f"permissions response widens filesystem {field} paths"
            if "entries" in filesystem and (
                not isinstance(filesystem["entries"], list)
                or not isinstance(requested_fs.get("entries"), list)
                or any(entry not in requested_fs["entries"] for entry in filesystem["entries"])
            ):
                return "permissions response widens filesystem entries"
            if "globScanMaxDepth" in filesystem:
                requested_depth = requested_fs.get("globScanMaxDepth")
                if (
                    isinstance(filesystem["globScanMaxDepth"], bool)
                    or not isinstance(filesystem["globScanMaxDepth"], int)
                    or isinstance(requested_depth, bool)
                    or not isinstance(requested_depth, int)
                    or filesystem["globScanMaxDepth"] > requested_depth
                ):
                    return "permissions response widens filesystem scan depth"
        return None
    if method == "item/tool/requestUserInput":
        if set(result) != {"answers"} or not isinstance(result.get("answers"), dict):
            return "user-input response does not match the method schema"
        questions = params.get("questions")
        question_ids = [item.get("id") for item in questions if isinstance(item, dict)] if isinstance(questions, list) else []
        answers = result["answers"]
        if len(question_ids) != len(questions or []) or set(answers) != set(question_ids):
            return "user-input response has missing or unknown question ids"
        for answer in answers.values():
            if (
                not isinstance(answer, dict)
                or set(answer) != {"answers"}
                or not isinstance(answer.get("answers"), list)
                or any(not isinstance(value, str) for value in answer["answers"])
            ):
                return "user-input response must use per-question answers arrays"
        return None
    if method == "mcpServer/elicitation/request":
        if set(result) - {"action", "content"} or result.get("action") not in {"accept", "decline", "cancel"}:
            return "MCP response does not match the elicitation method schema"
        if result["action"] == "accept" and not isinstance(result.get("content"), dict):
            return "accepted MCP response needs structured content"
        if "content" in result and result["content"] is not None and not isinstance(result["content"], dict):
            return "MCP response content must be an object or null"
        if result["action"] != "accept" and result.get("content") is not None:
            return "declined MCP response cannot include content"
        return None
    return f"unsupported server request method: {method}"


def _finish_turn(thread_id: str, turn_id: str, state_name: str = "completed", loss_mode: str = "") -> None:
    if DELAY:
        time.sleep(DELAY)
    current_state = _read_state()
    current_thread = current_state.get("threads", {}).get(thread_id, {})
    current_turn = next((item for item in current_thread.get("turns", []) if item.get("id") == turn_id), {})
    if current_turn.get("status") != "inProgress":
        return
    if REQUEST_MODE:
        with LOCK:
            state = _read_state()
            thread = state["threads"][thread_id]
            should_request = not thread.get("request_sent")
            if should_request:
                thread["request_sent"] = True
                _save_state(state)
        if not should_request:
            REQUEST_MODE_FOR_TURN = False
        else:
            REQUEST_MODE_FOR_TURN = True
    else:
        REQUEST_MODE_FOR_TURN = False
    if REQUEST_MODE_FOR_TURN:
        request_id = f"pending-{thread_id}"
        _event("guardian/review/completed", {"threadId": thread_id, "turnId": turn_id, "review": {"status": "completed"}, "result": "reviewed"})
        method = {
            "approval": "item/commandExecution/requestApproval",
            "file-change": "item/fileChange/requestApproval",
            "user-input": "item/tool/requestUserInput",
            "mcp": "mcpServer/elicitation/request",
        }.get(REQUEST_MODE, "item/permissions/requestApproval")
        with LOCK:
            state = _read_state()
            thread = state["threads"][thread_id]
            turn = thread["turns"][0]
            turn["status"] = "inProgress"
            thread["status"] = {"type": "active"}
            request_params: dict[str, Any] = {
                "threadId": thread_id,
                "turnId": turn_id,
                "itemId": "item-1",
                "startedAtMs": int(time.time() * 1000),
            }
            if method == "item/commandExecution/requestApproval":
                request_params.update({"command": f"echo approval required {REQUEST_MARKER}".rstrip(), "cwd": thread.get("settings", {}).get("cwd")})
            elif method == "item/fileChange/requestApproval":
                request_params["reason"] = f"write a declared file {REQUEST_MARKER}".rstrip()
            elif method == "item/tool/requestUserInput":
                request_params.update({"isBlocking": True, "questions": [{"id": "q1", "header": f"Continue {REQUEST_MARKER}".rstrip(), "question": f"Continue? {REQUEST_MARKER}".rstrip()}]})
            elif method == "mcpServer/elicitation/request":
                request_params["serverName"] = f"fake-server {REQUEST_MARKER}".rstrip()
            elif method == "item/permissions/requestApproval":
                workspace = Path(thread.get("settings", {}).get("cwd", os.getcwd()))
                request_params.update(
                    {
                        "cwd": str(workspace),
                        "permissions": {
                            "network": {"enabled": False},
                            "fileSystem": {"write": [str(workspace / "src" / "writer.py")]},
                        },
                    }
                )
            state["pending_request"] = {"id": request_id, "method": method, "threadId": thread_id, "turnId": turn_id, "params": request_params}
            _save_state(state)
        _send({"id": request_id, "method": method, "params": request_params})
        if REQUEST_THEN_EXIT:
            os._exit(74)
        return
    if loss_mode == "active":
        with LOCK:
            state = _read_state()
            state["threads"][thread_id]["status"] = {"type": "active"}
            _save_state(state)
        os._exit(73)
    if loss_mode == "completed":
        state_name = "completed"
    report_chars = int(os.environ.get("FAST_APP_SERVER_REPORT_CHARS", "0"))
    report = "R" * report_chars if report_chars > 0 else f"Fake {state_name} report for thread {thread_id}."
    with LOCK:
        state = _read_state()
        thread = state["threads"][thread_id]
        turn = next(turn for turn in thread["turns"] if turn["id"] == turn_id)
        turn.update({"status": state_name, "items": [*turn.get("items", []), {"type": "agentMessage", "id": f"msg-{turn_id}", "text": report}], "tokenUsage": {"inputTokens": 120, "outputTokens": 25, "cachedInputTokens": 40}})
        thread["status"] = {"type": "idle"}
        _save_state(state)
    if loss_mode == "completed":
        os._exit(74)
    if MALFORMED_EVENT == "partial":
        with LOCK:
            sys.stdout.write('{"method":"turn/completed"')
            sys.stdout.flush()
        os._exit(76)
    if MALFORMED_EVENT == "bad-json":
        with LOCK:
            sys.stdout.write("{bad json}\n")
            sys.stdout.flush()
    _event("thread/tokenUsage/updated", {"threadId": thread_id, "turnId": turn_id, "tokenUsage": {"inputTokens": 120, "outputTokens": 25, "cachedInputTokens": 40}})
    _event("turn/completed", {"threadId": thread_id, "turnId": turn_id, "turn": {"id": turn_id, "status": state_name, "items": [{"type": "agentMessage", "text": report}], "tokenUsage": {"inputTokens": 120, "outputTokens": 25, "cachedInputTokens": 40}}})


def _respond_server_request(request_id: Any, result: dict[str, Any]) -> None:
    with LOCK:
        state = _read_state()
        pending = state.get("pending_request")
        if not pending:
            state.setdefault("response_mismatches", []).append(
                {"request_id": request_id, "reason": "response has no pending server request", "result": result}
            )
            _save_state(state)
            return
        if request_id != pending.get("id"):
            state.setdefault("response_mismatches", []).append(
                {"request_id": request_id, "method": pending["method"], "reason": "response id does not match the exact pending server request", "result": result}
            )
            _save_state(state)
            return
        mismatch = _response_mismatch(pending["method"], pending.get("params", {}), result)
        if mismatch:
            state.setdefault("response_mismatches", []).append(
                {"request_id": request_id, "method": pending["method"], "reason": mismatch, "result": result}
            )
            _save_state(state)
            return
        thread_id = pending["threadId"]
        turn_id = pending["turnId"]
        state.setdefault("server_responses", []).append({"request_id": request_id, "result": result})
        state.pop("pending_request", None)
        _save_state(state)
    _event("serverRequest/resolved", {"threadId": thread_id, "turnId": turn_id, "requestId": pending["id"]})
    threading.Thread(target=_finish_turn, args=(thread_id, turn_id), daemon=True).start()


def _handle(message: dict[str, Any]) -> None:
    method = message.get("method")
    request_id = message.get("id")
    params = message.get("params") if isinstance(message.get("params"), dict) else {}
    state = _read_state()
    state.setdefault("counts", {})[str(method)] = state.setdefault("counts", {}).get(str(method), 0) + 1
    entry = {"method": method, "params": params}
    if method is None:
        entry["response"] = message
    state.setdefault("requests", []).append(entry)
    _save_state(state)
    if method == "initialized":
        return
    if method == "initialize":
        _reply(request_id, {"serverInfo": {"name": "fake-codex", "version": "0.162.1"}})
    elif method == "model/list":
        _reply(request_id, {"data": _models()})
    elif method == "thread/start":
        thread = _thread_settings(params)
        state = _read_state()
        state["threads"][thread["thread"]["id"]] = thread["thread"] | {"settings": thread["settings"]}
        _save_state(state)
        _reply(request_id, {key: value for key, value in thread.items() if key not in {"thread", "settings"}} | {"thread": thread["thread"]})
    elif method in {"thread/resume", "thread/read"}:
        thread_id = params.get("threadId")
        thread = _read_state()["threads"].get(thread_id)
        if thread is None:
            _reply(request_id, error={"code": -32004, "message": "unknown thread"})
        elif method == "thread/resume":
            # Thread settings are retained separately from turns.
            settings = thread.get("settings", {})
            _reply(request_id, settings | {"thread": thread})
        else:
            _reply(request_id, {"thread": thread})
    elif method == "turn/start":
        thread_id = params.get("threadId")
        turn_id = str(uuid.uuid4())
        turn = {
            "id": turn_id,
            "status": "inProgress",
            "clientUserMessageId": params.get("clientUserMessageId"),
            "items": [{"type": "userMessage", "clientId": params.get("clientUserMessageId")}],
        }
        state = _read_state()
        thread = state["threads"][thread_id]
        thread.setdefault("turns", []).append(turn)
        thread["status"] = {"type": "active"}
        _save_state(state)
        early_terminal = os.environ.get("FAST_APP_SERVER_START_TERMINAL_BEFORE_RESPONSE")
        if early_terminal in {"completed", "failed", "interrupted"}:
            report = f"Fake {early_terminal} report for thread {thread_id}."
            terminal_turn = dict(turn)
            terminal_turn["status"] = early_terminal
            terminal_turn["items"] = [
                *turn.get("items", []),
                *([{"type": "agentMessage", "id": f"msg-{turn_id}", "text": report}] if early_terminal == "completed" else []),
            ]
            if early_terminal == "failed":
                terminal_turn["error"] = {"message": "fake early turn failure"}
            with LOCK:
                state = _read_state()
                thread = state["threads"][thread_id]
                thread["turns"][-1] = terminal_turn
                thread["status"] = {"type": "idle"}
                _save_state(state)
            _event("turn/completed", {"threadId": thread_id, "turnId": turn_id, "turn": terminal_turn})
            _reply(request_id, {"turn": turn})
            return
        _reply(request_id, {"turn": turn})
        _event("turn/started", {"threadId": thread_id, "turn": turn})
        loss_mode = LOSS_MODE if state["counts"]["turn/start"] == LOSS_TURN else ""
        if loss_mode == "completed":
            _finish_turn(thread_id, turn_id, "completed", loss_mode)
        else:
            threading.Thread(target=_finish_turn, args=(thread_id, turn_id, "completed", loss_mode), daemon=True).start()
    elif method == "turn/steer":
        thread_id, expected = params.get("threadId"), params.get("expectedTurnId")
        state = _read_state()
        thread = state["threads"].get(thread_id, {})
        active = next((turn for turn in thread.get("turns", []) if turn.get("status") == "inProgress"), None)
        if active is None or active.get("id") != expected:
            _reply(request_id, error={"code": -32001, "message": "expectedTurnId is not the current turn"})
        else:
            _reply(request_id, {"turnId": expected})
    elif method == "turn/interrupt":
        thread_id, turn_id = params.get("threadId"), params.get("turnId")
        state = _read_state()
        thread = state["threads"].get(thread_id, {})
        for turn in thread.get("turns", []):
            if turn.get("id") == turn_id:
                turn["status"] = "interrupted"
        thread["status"] = {"type": "idle"}
        pending = state.get("pending_request")
        if pending and pending.get("threadId") == thread_id and pending.get("turnId") == turn_id:
            state.pop("pending_request", None)
        _save_state(state)
        _reply(request_id, {"turnId": turn_id})
        _event("turn/completed", {"threadId": thread_id, "turnId": turn_id, "turn": {"id": turn_id, "status": "interrupted", "items": []}})
    elif request_id is not None and method is None:
        _respond_server_request(request_id, message.get("result", {}))
    else:
        _reply(request_id, error={"code": -32601, "message": f"unknown method {method}"})


def _copy_schema(target: Path) -> int:
    if not SCHEMA_SOURCE.is_dir():
        sys.stderr.write("FAST_APP_SERVER_SCHEMA_SOURCE is not set\n")
        return 2
    shutil.copytree(SCHEMA_SOURCE, target, dirs_exist_ok=True)
    return 0


def main() -> int:
    args = sys.argv[1:]
    if args == ["--version"]:
        print("codex-cli 0.162.1")
        return 0
    if args[:2] == ["app-server", "--help"] or args == ["app-server", "--help"]:
        print("app-server --listen stdio://; app-server generate-json-schema --out PATH")
        return 0
    if args[:2] == ["features", "list"]:
        print("other_feature stable true" if NO_FAST_FEATURE else "fast_mode stable true")
        return 0
    if args[:2] == ["app-server", "generate-json-schema"]:
        try:
            index = args.index("--out")
            return _copy_schema(Path(args[index + 1]))
        except (ValueError, IndexError, OSError) as error:
            print(f"schema output failed: {error}", file=sys.stderr)
            return 2
    if args[:3] == ["app-server", "--listen", "stdio://"]:
        for line in sys.stdin:
            try:
                message = json.loads(line)
                if isinstance(message, dict):
                    _handle(message)
            except (json.JSONDecodeError, OSError, KeyError, StopIteration):
                continue
        return 0
    print("unsupported fake Codex CLI arguments", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
