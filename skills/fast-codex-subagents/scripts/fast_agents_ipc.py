"""Authenticated loopback IPC for status, events, and explicit worker control."""

from __future__ import annotations

import asyncio
import hmac
import json
import secrets
import socket
from pathlib import Path
from typing import Any, Awaitable, Callable

from fast_agents_common import FastAgentsError, atomic_write_json, read_json


MAX_CONTROL_LINE = 128 * 1024
MAX_ACTIVE_CONTROLS = 24
CONTROL_HANDLER_TIMEOUT = 45.0
CONTROL_WRITE_TIMEOUT = 5.0
CONTROL_CLOSE_TIMEOUT = 3.0
ControlHandler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


def _encode_response(value: dict[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"


def _control_projection(command: str, result: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
    """Keep wire projections bounded without discarding the private event history."""
    result = dict(result)
    if command == "status":
        history = result.pop("control_events", [])
        result["event_history"] = {
            "retained": len(history),
            "oldest_seq": history[0]["seq"] if history else None,
            "latest_seq": history[-1]["seq"] if history else 0,
        }
        reviews = result.get("guardian_reviews")
        if isinstance(reviews, list):
            result["guardian_review_count"] = len(reviews)
            result["guardian_reviews"] = reviews[-20:]
    elif command == "events":
        events = list(result.get("events", []))
        after = request.get("after_seq", 0)
        latest = result.get("latest_seq", 0)
        while True:
            next_seq = events[-1]["seq"] if events else after
            result.update({"events": events, "next_seq": next_seq, "has_more": next_seq < latest})
            if len(_encode_response({"ok": True, "result": result})) <= MAX_CONTROL_LINE:
                break
            if len(events) <= 1:
                raise FastAgentsError("event exceeds the control response limit; inspect the private event log")
            events.pop()
    return result


class ControlServer:
    def __init__(self, task_dir: Path, handler: ControlHandler):
        self.task_dir = task_dir
        self.handler = handler
        self.read_token = secrets.token_urlsafe(32)
        self.control_token = secrets.token_urlsafe(32)
        self.server: asyncio.AbstractServer | None = None
        self.active_connections = 0
        self.discovery_path = task_dir / "discovery.json"
        self.capability_path = task_dir / "capabilities.json"

    async def start(self) -> None:
        self.server = await asyncio.start_server(self._accept, host="127.0.0.1", port=0, limit=MAX_CONTROL_LINE, backlog=32)
        sockets = self.server.sockets or []
        if not sockets:
            raise FastAgentsError("could not bind local control IPC")
        port = int(sockets[0].getsockname()[1])
        atomic_write_json(
            self.capability_path,
            {"schema_version": 1, "read": self.read_token, "control": self.control_token},
        )
        atomic_write_json(
            self.discovery_path,
            {
                "schema_version": 1,
                "host": "127.0.0.1",
                "port": port,
                "capability_file": str(self.capability_path),
            },
        )

    async def _accept(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        if self.active_connections >= MAX_ACTIVE_CONTROLS:
            await self._reply(writer, {"ok": False, "error": "control endpoint is at capacity; retry read-only polling later; do not replay uncertain control actions"})
            writer.close()
            return
        self.active_connections += 1
        try:
            raw = await asyncio.wait_for(reader.readline(), timeout=5.0)
            if not raw or len(raw) > MAX_CONTROL_LINE or not raw.endswith(b"\n"):
                await self._reply(writer, {"ok": False, "error": "invalid control request"})
                return
            try:
                request = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                await self._reply(writer, {"ok": False, "error": "invalid control request"})
                return
            if not isinstance(request, dict) or not isinstance(request.get("command"), str):
                await self._reply(writer, {"ok": False, "error": "invalid control request"})
                return
            command = request["command"]
            read_only = command in {"status", "events"}
            supplied = request.get("token")
            expected = self.read_token if read_only else self.control_token
            if not isinstance(supplied, str) or not hmac.compare_digest(supplied, expected):
                await self._reply(writer, {"ok": False, "error": "not authorized"})
                return
            request.pop("token", None)
            result = await asyncio.wait_for(self.handler(request), timeout=CONTROL_HANDLER_TIMEOUT)
            result = _control_projection(command, result, request)
            await self._reply(writer, {"ok": True, "result": result})
        except asyncio.TimeoutError:
            await self._reply(writer, {"ok": False, "error": "control action timed out; delivery may be uncertain; inspect status before any new action"})
        except Exception as error:
            await self._reply(writer, {"ok": False, "error": str(error)})
        finally:
            self.active_connections -= 1
            writer.close()
            try:
                await asyncio.wait_for(writer.wait_closed(), CONTROL_CLOSE_TIMEOUT)
            except OSError:
                pass

    @staticmethod
    async def _reply(writer: asyncio.StreamWriter, value: dict[str, Any]) -> None:
        if writer.is_closing():
            return
        try:
            encoded = _encode_response(value)
            if len(encoded) > MAX_CONTROL_LINE:
                encoded = _encode_response({
                    "ok": False,
                    "error": "control response exceeds the byte limit; use paginated events or inspect private task artifacts",
                })
            writer.write(encoded)
            await asyncio.wait_for(writer.drain(), CONTROL_WRITE_TIMEOUT)
        except (OSError, asyncio.TimeoutError):
            return

    async def close(self) -> None:
        if self.server is not None:
            self.server.close()
            await self.server.wait_closed()


def send_control_request(task_dir: Path, command: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    discovery = read_json(task_dir / "discovery.json")
    if discovery.get("host") != "127.0.0.1":
        raise FastAgentsError("task control endpoint is not loopback-only")
    port = discovery.get("port")
    if not isinstance(port, int) or not 1 <= port <= 65535:
        raise FastAgentsError("task control endpoint has an invalid port")
    capability_file = Path(str(discovery.get("capability_file", "")))
    if not capability_file.is_absolute() or capability_file.parent != task_dir:
        raise FastAgentsError("task capability file is outside the task runtime")
    capabilities = read_json(capability_file)
    read_only = command in {"status", "events"}
    token = capabilities.get("read" if read_only else "control")
    if not isinstance(token, str) or len(token) < 32:
        raise FastAgentsError("task capability file is invalid")
    request: dict[str, Any] = {"command": command, "token": token}
    if payload:
        request.update(payload)
    encoded = json.dumps(request, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=5.0) as connection:
            connection.sendall(encoded)
            stream = connection.makefile("rb")
            response = stream.readline(MAX_CONTROL_LINE + 1)
    except OSError as error:
        raise FastAgentsError(f"task supervisor is not reachable: {error}") from error
    if not response or len(response) > MAX_CONTROL_LINE or not response.endswith(b"\n"):
        raise FastAgentsError("task supervisor returned an invalid control response")
    try:
        decoded = json.loads(response.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise FastAgentsError("task supervisor returned malformed control JSON") from error
    if not isinstance(decoded, dict) or decoded.get("ok") is not True:
        message = decoded.get("error", "control request failed") if isinstance(decoded, dict) else "control request failed"
        raise FastAgentsError(str(message))
    result = decoded.get("result")
    if not isinstance(result, dict):
        raise FastAgentsError("task supervisor returned a malformed result")
    return result
