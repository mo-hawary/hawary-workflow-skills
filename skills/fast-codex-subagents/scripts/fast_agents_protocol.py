"""Version-pinned Codex app-server stdio transport and schema checks."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from fast_agents_common import FastAgentsError
from fast_agents_platform import codex_argv, protect_private_path


# Version families are candidates, not permission to execute an untested CLI.
# Add a release only after its protocol fixtures and live smoke are verified.
APP_SERVER_VERSION = "0.162.1"  # currently fixture-backed
CANDIDATE_CODEX_RANGE = ">=0.162.1,<0.163.0"
TESTED_CODEX_VERSIONS = frozenset({"0.162.1"})
MAX_PROTOCOL_LINE = 8 * 1024 * 1024
EventHandler = Callable[[dict[str, Any]], Awaitable[None] | None]
ProcessHandler = Callable[[int], None]


class AppServerError(FastAgentsError):
    """A protocol, server, or server-request failure."""


class AppServerRpcError(AppServerError):
    def __init__(self, method: str, error: dict[str, Any]):
        self.method = method
        self.error = error
        super().__init__(f"app-server {method} failed: {error.get('message', 'unknown RPC error')}")


def tested_version_error(version: str) -> str | None:
    if version in TESTED_CODEX_VERSIONS:
        return None
    tested = ", ".join(sorted(TESTED_CODEX_VERSIONS))
    return (
        f"Codex CLI {version} has no verified protocol fixtures. Tested: {tested}; "
        f"candidate family: {CANDIDATE_CODEX_RANGE}. Follow the upgrade runbook: "
        "capture a sanitized real transcript, add versioned schema fixtures, "
        "run replay and live smoke tests, and explicitly add the release to "
        "TESTED_CODEX_VERSIONS. No fallback or untested version is used."
    )


def request_id_key(value: str | int) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise AppServerError("app-server request id must be a string or integer")
    return f"{type(value).__name__}:{value}"


def app_server_argv(executable: str) -> list[str]:
    path = Path(executable).expanduser().resolve()
    if path.suffix.casefold() == ".py":
        import sys

        return [sys.executable, str(path), "app-server", "--listen", "stdio://"]
    return codex_argv(executable, "app-server", "--listen", "stdio://")


@dataclass(frozen=True)
class ProtocolSchemaReport:
    verified: bool
    version: str
    methods: tuple[str, ...]
    server_requests: tuple[str, ...]
    error: str | None = None


def _method_variants(schema: dict[str, Any]) -> set[str]:
    methods: set[str] = set()
    for variant in schema.get("oneOf", []):
        if not isinstance(variant, dict):
            continue
        method = variant.get("properties", {}).get("method", {})
        constant = method.get("const")
        enum = method.get("enum", [])
        if isinstance(constant, str):
            methods.add(constant)
        methods.update(value for value in enum if isinstance(value, str))
    return methods


def verify_schema_directory(schema_root: Path, version: str) -> ProtocolSchemaReport:
    if (error := tested_version_error(version)) is not None:
        return ProtocolSchemaReport(False, version, (), (), error)
    try:
        v2 = schema_root / "v2"
        client = json.loads((schema_root / "ClientRequest.json").read_text(encoding="utf-8"))
        server = json.loads((schema_root / "ServerRequest.json").read_text(encoding="utf-8"))
        required_properties = {
            "ThreadStartParams.json": {"model", "cwd", "sandbox", "approvalPolicy", "approvalsReviewer", "serviceTier", "developerInstructions", "ephemeral"},
            "TurnStartParams.json": {"threadId", "input", "effort", "serviceTierForTurn"},
            "TurnSteerParams.json": {"threadId", "expectedTurnId", "input"},
            "TurnInterruptParams.json": {"threadId", "turnId"},
            "ModelListResponse.json": {"data"},
        }
        for name, required in required_properties.items():
            schema = json.loads((v2 / name).read_text(encoding="utf-8"))
            properties = set(schema.get("properties", {}))
            if not required.issubset(properties):
                raise ValueError(f"{name} lacks fields: {', '.join(sorted(required - properties))}")
        methods = _method_variants(client)
        expected_methods = {
            "initialize",
            "model/list",
            "thread/start",
            "thread/resume",
            "thread/read",
            "turn/start",
            "turn/steer",
            "turn/interrupt",
        }
        if not expected_methods.issubset(methods):
            raise ValueError(f"ClientRequest schema lacks methods: {', '.join(sorted(expected_methods - methods))}")
        server_requests = _method_variants(server)
        expected_requests = {
            "item/commandExecution/requestApproval",
            "item/fileChange/requestApproval",
            "item/permissions/requestApproval",
            "item/tool/requestUserInput",
            "mcpServer/elicitation/request",
        }
        if not expected_requests.issubset(server_requests):
            raise ValueError(f"ServerRequest schema lacks requests: {', '.join(sorted(expected_requests - server_requests))}")
        model_schema = json.loads((schema_root / "codex_app_server_protocol.v2.schemas.json").read_text(encoding="utf-8"))
        model_definition = model_schema["definitions"]["Model"]["properties"]
        if not {"supportedReasoningEfforts", "serviceTiers", "defaultServiceTier"}.issubset(model_definition):
            raise ValueError("Model catalog schema lacks effort or service-tier evidence")
        return ProtocolSchemaReport(True, version, tuple(sorted(methods)), tuple(sorted(server_requests)))
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        return ProtocolSchemaReport(False, version, (), (), f"app-server schema verification failed: {error}")


def generate_and_verify_schema(executable: str, version: str, timeout: float = 20.0) -> ProtocolSchemaReport:
    if (error := tested_version_error(version)) is not None:
        return ProtocolSchemaReport(False, version, (), (), error)
    with tempfile.TemporaryDirectory(prefix="fast-agents-schema-") as temporary:
        target = Path(temporary)
        try:
            argv = [*codex_argv(executable, "app-server", "generate-json-schema", "--out", str(target))]
            result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, shell=False, check=False)
        except (OSError, subprocess.SubprocessError) as error:
            return ProtocolSchemaReport(False, version, (), (), f"could not generate app-server schema: {error}")
        if result.returncode:
            detail = (result.stderr or result.stdout).strip()
            return ProtocolSchemaReport(False, version, (), (), f"app-server schema generator failed: {detail or result.returncode}")
        return verify_schema_directory(target, version)


@dataclass(frozen=True)
class ModelCatalog:
    models: tuple[dict[str, Any], ...]
    next_cursor: str | None


def catalog_efforts(model: dict[str, Any]) -> set[str]:
    efforts = model.get("supportedReasoningEfforts", [])
    result: set[str] = set()
    if isinstance(efforts, list):
        for effort in efforts:
            if isinstance(effort, str):
                result.add(effort)
            elif isinstance(effort, dict):
                value = effort.get("reasoningEffort") or effort.get("id") or effort.get("effort")
                if isinstance(value, str):
                    result.add(value)
    return result


def catalog_tiers(model: dict[str, Any]) -> set[str]:
    tiers = model.get("serviceTiers", [])
    result: set[str] = set()
    if isinstance(tiers, list):
        for tier in tiers:
            if isinstance(tier, str):
                result.add(tier)
            elif isinstance(tier, dict) and isinstance(tier.get("id"), str):
                result.add(tier["id"])
    default_tier = model.get("defaultServiceTier")
    if isinstance(default_tier, str):
        result.add(default_tier)
    return result


class AppServerClient:
    """One JSONL reader, one serialized writer, and distinct RPC/request maps."""

    def __init__(
        self,
        executable: str,
        cwd: Path,
        stderr_path: Path,
        on_event: EventHandler,
        on_server_request: EventHandler,
        on_protocol_error: EventHandler,
        on_started: ProcessHandler | None = None,
    ) -> None:
        self.executable = executable
        self.cwd = cwd
        self.stderr_path = stderr_path
        self.on_event = on_event
        self.on_server_request = on_server_request
        self.on_protocol_error = on_protocol_error
        self.on_started = on_started
        self.process: asyncio.subprocess.Process | None = None
        self.pending_responses: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self.server_requests: dict[str, dict[str, Any]] = {}
        self.write_lock = asyncio.Lock()
        self.reader_task: asyncio.Task[None] | None = None
        self.stderr_task: asyncio.Task[None] | None = None
        self.sequence = 0
        self.closed = asyncio.Event()
        self.terminal_error: str | None = None

    async def start(self) -> None:
        argv = app_server_argv(self.executable)
        self.process = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self.cwd,
            limit=MAX_PROTOCOL_LINE,
        )
        if self.process.stdout is None or self.process.stdin is None or self.process.stderr is None:
            raise AppServerError("app-server stdio pipes were not created")
        if self.on_started:
            try:
                self.on_started(self.process.pid)
            except Exception:
                self.process.stdin.close()
                await self.process.wait()
                raise
        self.reader_task = asyncio.create_task(self._read_messages())
        self.stderr_task = asyncio.create_task(self._drain_stderr())
        await self.request(
            "initialize",
            {"clientInfo": {"name": "fast-agents", "version": "0.2.0"}, "capabilities": {"experimentalApi": False}},
        )
        await self.notify("initialized")

    async def request(self, method: str, params: dict[str, Any] | None = None, timeout: float | None = 30.0) -> dict[str, Any]:
        if self.process is None or self.process.stdin is None or self.closed.is_set():
            raise AppServerError("app-server transport is not available")
        self.sequence += 1
        request_id = f"fast-agents:{self.sequence}"
        key = request_id_key(request_id)
        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict[str, Any]] = loop.create_future()
        self.pending_responses[key] = future
        message: dict[str, Any] = {"id": request_id, "method": method}
        if params is not None:
            message["params"] = params
        try:
            await self._write_message(message)
            response = await asyncio.wait_for(future, timeout) if timeout is not None else await future
        except (asyncio.TimeoutError, OSError, BrokenPipeError) as error:
            self.pending_responses.pop(key, None)
            if not future.done():
                future.cancel()
            raise AppServerError(f"app-server request {method} did not complete: {error}") from error
        finally:
            self.pending_responses.pop(key, None)
        if "error" in response:
            error = response["error"] if isinstance(response["error"], dict) else {"message": "malformed error response"}
            raise AppServerRpcError(method, error)
        result = response.get("result", {})
        if not isinstance(result, dict):
            raise AppServerError(f"app-server {method} response result must be an object")
        return result

    async def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        message: dict[str, Any] = {"method": method}
        if params is not None:
            message["params"] = params
        await self._write_message(message)

    async def respond(self, request_id: str | int, result: dict[str, Any]) -> None:
        key = request_id_key(request_id)
        # Consume before the await: the serialized transport writer alone
        # does not serialize callers competing for the same request id.
        if self.server_requests.pop(key, None) is None:
            raise AppServerError("server request is no longer pending")
        await self._write_message({"id": request_id, "result": result})

    async def reject_server_request(self, request_id: str | int, message: str) -> None:
        key = request_id_key(request_id)
        if self.server_requests.pop(key, None) is None:
            raise AppServerError("server request is no longer pending")
        await self._write_message({"id": request_id, "error": {"code": -32601, "message": message}})

    async def _write_message(self, value: dict[str, Any]) -> None:
        if self.process is None or self.process.stdin is None:
            raise AppServerError("app-server stdin is not available")
        line = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"
        async with self.write_lock:
            self.process.stdin.write(line)
            await self.process.stdin.drain()

    async def _read_messages(self) -> None:
        assert self.process is not None and self.process.stdout is not None
        try:
            while True:
                raw = await self.process.stdout.readline()
                if not raw:
                    self.terminal_error = "app-server closed stdout"
                    break
                if not raw.endswith(b"\n"):
                    await self._report_protocol_error("unterminated JSONL message", raw)
                    break
                try:
                    message = json.loads(raw.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as error:
                    await self._report_protocol_error(f"malformed JSONL message: {error}", raw)
                    continue
                if not isinstance(message, dict):
                    await self._report_protocol_error("JSONL message must be an object", raw)
                    continue
                if "id" in message and "method" in message:
                    request_id = message.get("id")
                    try:
                        key = request_id_key(request_id)
                    except AppServerError as error:
                        await self._report_protocol_error(str(error), raw)
                        continue
                    self.server_requests[key] = message
                    await _call_handler(self.on_server_request, message)
                    continue
                if "id" in message:
                    try:
                        key = request_id_key(message.get("id"))
                    except AppServerError as error:
                        await self._report_protocol_error(str(error), raw)
                        continue
                    future = self.pending_responses.get(key)
                    if future is not None and not future.done():
                        future.set_result(message)
                    else:
                        await self._report_protocol_error("response did not match an outstanding client request", raw)
                    continue
                if isinstance(message.get("method"), str):
                    await _call_handler(self.on_event, message)
                else:
                    await self._report_protocol_error("message has neither method nor request id", raw)
        except (OSError, ValueError) as error:
            self.terminal_error = f"app-server reader failed: {error}"
        finally:
            self.closed.set()
            for future in self.pending_responses.values():
                if not future.done():
                    future.set_exception(AppServerError(self.terminal_error or "app-server transport ended"))

    async def _report_protocol_error(self, message: str, raw: bytes) -> None:
        event = {"type": "protocol.error", "message": message, "raw": raw.decode("utf-8", errors="replace")}
        await _call_handler(self.on_protocol_error, event)

    async def _drain_stderr(self) -> None:
        assert self.process is not None and self.process.stderr is not None
        descriptor: int | None = None
        try:
            self.stderr_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            if self.stderr_path.is_symlink():
                raise AppServerError("private stderr path must not be a symlink")
            flags = os.O_CREAT | os.O_WRONLY | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(self.stderr_path, flags, 0o600)
            protect_private_path(self.stderr_path, directory=False)
            output = os.fdopen(descriptor, "ab")
            descriptor = None
            with output:
                while True:
                    chunk = await self.process.stderr.read(8192)
                    if not chunk:
                        return
                    output.write(chunk)
                    output.flush()
        except (OSError, FastAgentsError) as error:
            self.terminal_error = f"app-server stderr capture failed: {error}"
            self.closed.set()
            await _call_handler(self.on_protocol_error, {"type": "stderr.error", "message": self.terminal_error})
        finally:
            if descriptor is not None:
                os.close(descriptor)

    async def close_stdin_and_wait(self) -> int:
        if self.process is None:
            return 0
        if self.process.stdin is not None and not self.process.stdin.is_closing():
            self.process.stdin.close()
            try:
                await self.process.stdin.wait_closed()
            except (OSError, AttributeError):
                pass
        return await self.process.wait()


async def _call_handler(handler: EventHandler, message: dict[str, Any]) -> None:
    result = handler(message)
    if asyncio.iscoroutine(result):
        await result


async def probe_model_catalog(executable: str, cwd: Path, stderr_path: Path) -> ModelCatalog:
    async def ignore(_message: dict[str, Any]) -> None:
        return None

    client = AppServerClient(executable, cwd, stderr_path, ignore, ignore, ignore)
    await client.start()
    try:
        models: list[dict[str, Any]] = []
        cursor: str | None = None
        seen_cursors: set[str] = set()
        while True:
            if cursor is not None:
                if cursor in seen_cursors or len(seen_cursors) >= 100:
                    raise AppServerError("model/list pagination repeated or exceeded the page limit")
                seen_cursors.add(cursor)
            params: dict[str, Any] = {"limit": 100}
            if cursor:
                params["cursor"] = cursor
            result = await client.request("model/list", params)
            page = result.get("data", [])
            if not isinstance(page, list) or any(not isinstance(item, dict) for item in page):
                raise AppServerError("model/list returned a malformed model catalog")
            models.extend(page)
            next_cursor = result.get("nextCursor")
            if next_cursor is not None and not isinstance(next_cursor, str):
                raise AppServerError("model/list returned a malformed pagination cursor")
            cursor = next_cursor or None
            if not cursor:
                break
        return ModelCatalog(tuple(models), cursor)
    finally:
        await client.close_stdin_and_wait()
