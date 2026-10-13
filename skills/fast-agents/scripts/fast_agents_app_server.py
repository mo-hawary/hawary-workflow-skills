"""Single-process asyncio supervisor for Codex app-server logical threads."""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fast_agents_common import (
    FastAgentsError,
    SUPPORTED_APP_SERVER_REQUESTS,
    atomic_write_json,
    read_bounded_dependency_context,
    read_json,
)
from fast_agents_ipc import ControlServer
from fast_agents_platform import platform_coverage_label, protect_private_path
from fast_agents_protocol import (
    AppServerClient,
    AppServerError,
    AppServerRpcError,
    catalog_efforts,
    catalog_tiers,
)


MAX_CONTROL_EVENTS = 500
KNOWN_SERVER_REQUESTS = SUPPORTED_APP_SERVER_REQUESTS
TERMINAL_TURN_STATES = {"completed", "failed", "interrupted"}


@dataclass(frozen=True)
class RolePlan:
    name: str
    access: str
    model: str
    effort: str
    outcome: str
    owns: tuple[str, ...]
    constraints: tuple[str, ...]
    depends_on: tuple[str, ...]
    prompt: str
    fast_requested: bool


class AppServerSupervisor:
    def __init__(self, task_dir: Path, runtime_root: Path, codex: str, *, recover: bool = False):
        self.task_dir = task_dir
        self.runtime_root = runtime_root
        self.codex = codex
        self.recover_on_start = recover
        self.manifest = read_json(task_dir / "manifest.json")
        self.status = read_json(task_dir / "status.json")
        self.roles = {
            item["name"]: RolePlan(
                name=item["name"],
                access=item["access"],
                model=item["model"],
                effort=item["effort"],
                outcome=item["outcome"],
                owns=tuple(item.get("owns", [])),
                constraints=tuple(item.get("constraints", [])),
                depends_on=tuple(item.get("depends_on", [])),
                prompt=item.get("prompt", ""),
                fast_requested=bool(item.get("fast_requested", self.manifest.get("fast_requested", False))),
            )
            for item in self.manifest["roles"]
        }
        self.role_by_thread: dict[str, str] = {}
        self.client: AppServerClient | None = None
        self.control: ControlServer | None = None
        self.task_lock_descriptor: int | None = None
        self.lock_descriptor: int | None = None
        self.lock_path: Path | None = None
        self.lock_record_path: Path | None = None
        self.lock_record: dict[str, Any] | None = None
        self.catalog: dict[str, dict[str, Any]] = {}
        self.role_done = {name: asyncio.Event() for name in self.roles}
        self.role_tasks: dict[str, asyncio.Task[None]] = {}
        self.event_summaries: list[dict[str, Any]] = list(self.status.get("control_events", []))
        self.event_sequence = max(
            (item.get("seq", 0) for item in self.event_summaries if isinstance(item, dict) and isinstance(item.get("seq"), int)),
            default=0,
        )
        # A status snapshot is an output projection, never authority to replay an old request.
        self.pending_requests: dict[str, dict[str, Any]] = {}
        self.connection_epoch: str | None = None
        self.transport_lost = asyncio.Event()
        self.recovery_lock = asyncio.Lock()
        self.finished = asyncio.Event()
        self.scheduler_task: asyncio.Task[None] | None = None
        self.recovery_reason: str | None = None

    def _save_status(self) -> None:
        self.status["updated_at"] = _utc_now()
        self.status["control_events"] = self.event_summaries[-MAX_CONTROL_EVENTS:]
        self.status["pending_requests"] = [self._public_pending(item) for item in self.pending_requests.values()]
        private_path = self.task_dir / "pending-requests.private.json"
        atomic_write_json(private_path, {"requests": list(self.pending_requests.values())}, mode=0o600)
        protect_private_path(private_path, directory=False)
        atomic_write_json(self.task_dir / "status.json", self.status)

    @staticmethod
    def _public_pending(pending: dict[str, Any]) -> dict[str, Any]:
        return {
            "request_id": pending.get("request_id"),
            "kind": pending.get("method"),
            "action": "explicit reply required",
            "thread_id": pending.get("thread_id"),
            "turn_id": pending.get("turn_id"),
        }

    def _invalidate_pending(self, reason: str, *, role_name: str | None = None, turn_id: str | None = None) -> None:
        changed = False
        for key, pending in list(self.pending_requests.items()):
            if role_name is not None and pending.get("role") != role_name:
                continue
            if turn_id is not None and pending.get("turn_id") != turn_id:
                continue
            if self.client is not None:
                self.client.server_requests.pop(key, None)
            self.pending_requests.pop(key, None)
            changed = True
        if changed:
            self._summary_event("control/server-request.invalidated", {"reason": reason}, role_name)
            self._save_status()

    def _append_private(self, path: Path, value: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_symlink():
            raise FastAgentsError("private runtime file must not be a symlink")
        flags = os.O_CREAT | os.O_WRONLY | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags, 0o600)
        try:
            protect_private_path(path, directory=False)
            payload = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"
            os.write(descriptor, payload)
        finally:
            os.close(descriptor)

    @staticmethod
    def _request_key(value: str | int) -> str:
        from fast_agents_protocol import request_id_key

        return request_id_key(value)

    def _role_dir(self, role_name: str) -> Path:
        path = self.task_dir / "agents" / role_name
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        protect_private_path(path, directory=True)
        return path

    def _role_name_for_thread(self, thread_id: Any) -> str | None:
        return self.role_by_thread.get(thread_id) if isinstance(thread_id, str) else None

    def _summary_event(self, method: str, params: dict[str, Any], role_name: str | None) -> None:
        self.event_sequence += 1
        summary = _event_summary(method, params)
        item = {
            "seq": self.event_sequence,
            "at": _utc_now(),
            "kind": method,
            "role": role_name,
            "thread_id": params.get("threadId"),
            "turn_id": params.get("turnId"),
            "item_id": params.get("itemId") or (params.get("item", {}).get("id") if isinstance(params.get("item"), dict) else None),
            "summary": summary,
        }
        self.event_summaries.append(item)
        if len(self.event_summaries) > MAX_CONTROL_EVENTS:
            del self.event_summaries[: len(self.event_summaries) - MAX_CONTROL_EVENTS]
        self._append_private(self.task_dir / "supervisor-events.jsonl", item)

    async def _on_protocol_error(self, event: dict[str, Any]) -> None:
        self._append_private(self.task_dir / "protocol-errors.jsonl", event)
        self._summary_event("protocol.error", event, None)
        self._save_status()

    async def _on_event(self, message: dict[str, Any]) -> None:
        method = str(message.get("method", "unknown"))
        params = message.get("params", {})
        if not isinstance(params, dict):
            params = {"malformed_params": params}
        role_name = self._role_name_for_thread(params.get("threadId"))
        target = self._role_dir(role_name) / "events.jsonl" if role_name else self.task_dir / "supervisor-events.jsonl"
        self._append_private(target, {"method": method, "params": params})
        self._summary_event(method, params, role_name)
        if method == "serverRequest/resolved":
            request_id = params.get("requestId")
            request_key = self._request_key(request_id) if isinstance(request_id, (str, int)) and not isinstance(request_id, bool) else None
            if request_key and request_key in self.pending_requests:
                self.pending_requests.pop(request_key, None)
                if self.client:
                    self.client.server_requests.pop(request_key, None)
        if role_name:
            role_state = self.status["roles"][role_name]
            role_state["last_activity"] = _event_summary(method, params)
            if method == "turn/started":
                turn = params.get("turn", {})
                if isinstance(turn, dict) and isinstance(turn.get("id"), str):
                    role_state["turn_id"] = turn["id"]
                    role_state["turn_start_state"] = "acknowledged"
            elif method == "thread/settings/updated":
                settings = params.get("threadSettings", {})
                if isinstance(settings, dict) and "serviceTier" in settings:
                    self._set_effective_tier(role_name, settings.get("serviceTier"))
            elif method == "thread/tokenUsage/updated":
                usage = params.get("tokenUsage")
                if isinstance(usage, dict):
                    role_state["usage"] = usage
            elif "guardian" in method.casefold() and "review" in method.casefold():
                self.status.setdefault("guardian_reviews", []).append(_guardian_summary(method, params, role_name))
            elif method == "turn/completed":
                turn = params.get("turn", {})
                turn_id = turn.get("id") if isinstance(turn, dict) else None
                if isinstance(turn, dict) and (not isinstance(params.get("turnId"), str) or params.get("turnId") == turn_id):
                    self._finish_from_turn(role_name, turn)
            elif method == "error" and params.get("willRetry") is False:
                role_state["error"] = _safe_text((params.get("error") or {}).get("message") if isinstance(params.get("error"), dict) else "Codex reported a non-retryable turn error")
            self._save_status()
        if method == "serverRequest/resolved":
            self._save_status()

    async def _on_server_request(self, message: dict[str, Any]) -> None:
        method = str(message.get("method", "unknown"))
        params = message.get("params", {})
        if not isinstance(params, dict):
            params = {}
        request_id = message.get("id")
        key = self._request_key(request_id)
        thread_id = params.get("threadId")
        role_name = self._role_name_for_thread(thread_id)
        turn_id = params.get("turnId")
        if method not in KNOWN_SERVER_REQUESTS:
            await self._reject_server_request(message, role_name, "unsupported app-server request method")
            return
        if not isinstance(turn_id, str) or not self._request_matches_live_turn(role_name, thread_id, turn_id):
            await self._reject_server_request(message, role_name, "request is not bound to a current live worker turn")
            return
        record = {
            "request_id": request_id,
            "method": method,
            "thread_id": thread_id,
            "turn_id": turn_id,
            "item_id": params.get("itemId"),
            "role": role_name,
            "transport_epoch": self.connection_epoch,
            "received_at": _utc_now(),
            "summary": _request_summary(method, params),
            "params": params,
            "server_request": message,
            "state": "pending",
        }
        self.pending_requests[key] = record
        target = self._role_dir(role_name) / "events.jsonl" if role_name else self.task_dir / "supervisor-events.jsonl"
        self._append_private(target, {"method": method, "id": request_id, "params": params, "state": "pending"})
        self._summary_event(method, params, role_name)
        self._save_status()

    def _request_matches_live_turn(self, role_name: str | None, thread_id: Any, turn_id: str) -> bool:
        if role_name not in self.roles or self.client is None or self.client.closed.is_set() or self.connection_epoch is None:
            return False
        role_state = self.status["roles"][role_name]
        if role_state.get("thread_id") != thread_id or role_state.get("state") not in {"starting", "running"}:
            return False
        current_turn = role_state.get("turn_id")
        if current_turn is not None:
            return current_turn == turn_id and role_state.get("turn_status") not in TERMINAL_TURN_STATES
        return role_state.get("turn_start_state") == "submitted"

    async def _reject_server_request(
        self,
        message: dict[str, Any],
        role_name: str | None,
        reason: str,
    ) -> None:
        request_id = message.get("id")
        self._append_private(
            self.task_dir / "unsupported-server-requests.jsonl",
            {"method": message.get("method"), "id": request_id, "params": message.get("params"), "reason": reason},
        )
        self._summary_event("protocol.unsupported-server-request", {}, role_name)
        if self.client is not None and isinstance(request_id, (str, int)) and not isinstance(request_id, bool):
            await self.client.reject_server_request(request_id, reason)
        if role_name in self.roles and str(message.get("method")) not in KNOWN_SERVER_REQUESTS:
            role_state = self.status["roles"][role_name]
            if role_state.get("state") in {"starting", "running"}:
                role_state.update({"state": "failed", "error": "unsupported app-server request; role stopped safely", "finished_at": _utc_now(), "exit_code": 1})
                self._write_exit(role_name)
                self.role_done[role_name].set()
        self._save_status()

    def _set_effective_tier(self, role_name: str, tier: Any, *, reported: bool = True) -> str | None:
        role_state = self.status["roles"][role_name]
        fast_state = role_state.setdefault("fast", {})
        requested_tier = fast_state.get("requested_tier")
        effective_tier = "default" if reported and tier is None and requested_tier == "default" else tier
        fast_state["effective_tier_reported"] = tier if reported and isinstance(tier, str) else None
        fast_state["effective_tier"] = effective_tier if isinstance(effective_tier, str) else None
        fast_state["effective"] = "matched" if effective_tier == requested_tier else "different" if isinstance(effective_tier, str) else "unverified"
        # The app-server reports requested/effective thread settings, not upstream execution.
        fast_state["upstream"] = "unverified"
        self.status.setdefault("fast", {}).setdefault("workers", {})[role_name] = dict(fast_state)
        return effective_tier if isinstance(effective_tier, str) else None

    def _finish_from_turn(self, role_name: str, turn: dict[str, Any]) -> None:
        role_state = self.status["roles"][role_name]
        turn_id = turn.get("id")
        if not isinstance(turn_id, str):
            return
        current_turn_id = role_state.get("turn_id")
        if current_turn_id is not None and current_turn_id != turn_id:
            return
        if current_turn_id is None and role_state.get("turn_start_state") != "submitted":
            return
        role_state["turn_id"] = turn_id
        state = turn.get("status")
        role_state["turn_status"] = state
        role_state["turn_start_state"] = "acknowledged"
        usage = turn.get("tokenUsage")
        if isinstance(usage, dict):
            role_state["usage"] = usage
        report = _assistant_report(turn)
        if report:
            self._write_final(role_name, report)
        if state in TERMINAL_TURN_STATES:
            role_state["finished_at"] = _utc_now()
            role_state["final_report_available"] = bool(report)
            role_state["exit_code"] = 0 if state == "completed" and report else 1 if state == "failed" else 130 if state == "interrupted" else None
            if state == "completed" and report:
                role_state.update({"state": "completed", "error": None})
            elif state == "interrupted":
                role_state.update({"state": "interrupted", "error": "turn interrupted"})
            elif state == "failed":
                error = turn.get("error")
                message = error.get("message") if isinstance(error, dict) else "turn failed"
                role_state.update({"state": "failed", "error": _safe_text(message)})
            else:
                role_state.update({"state": "unknown", "error": "completed turn has no final assistant report"})
            self._write_exit(role_name)
            self.role_done[role_name].set()
            self._invalidate_pending("worker turn reached a terminal state", role_name=role_name, turn_id=turn_id)
        role_state["updated_at"] = _utc_now()

    def _write_final(self, role_name: str, report: str) -> None:
        final_path = self._role_dir(role_name) / "final.md"
        payload = report.strip() + "\n"
        from fast_agents_common import atomic_write_bytes

        atomic_write_bytes(final_path, payload.encode("utf-8"))

    def _write_exit(self, role_name: str) -> None:
        role_state = self.status["roles"][role_name]
        atomic_write_json(
            self._role_dir(role_name) / "exit.json",
            {
                "finished_at": role_state.get("finished_at"),
                "turn_status": role_state.get("turn_status"),
                "exit_code": role_state.get("exit_code"),
                "final_report_available": role_state.get("final_report_available", False),
            },
        )

    def _role_prompt(self, role: RolePlan, dependency_context: str) -> str:
        sections = [
            f"Task objective: {self.manifest['objective']}",
            f"Shared contract (keep context bounded):\n{self.manifest['shared_contract']}",
            f"Role: {role.name} ({role.access}).",
            f"Expected outcome: {role.outcome}",
            "Do not delegate or start another agent. Respect the task's sandbox and repository instructions.",
        ]
        if role.owns:
            sections.append("Owned files (writers must stay within this list):\n" + "\n".join(f"- {path}" for path in role.owns))
        if role.constraints:
            sections.append("Constraints:\n" + "\n".join(f"- {value}" for value in role.constraints))
        if dependency_context:
            sections.append("Completed dependency reports:\n" + dependency_context)
        if role.prompt:
            sections.append(f"Additional task detail:\n{role.prompt}")
        sections.append("Return a concise report of changes/findings, verification, risks, and blockers.")
        return "\n\n".join(sections) + "\n"

    async def _start_client(self) -> None:
        self._invalidate_pending("app-server transport epoch changed")
        self.connection_epoch = str(uuid.uuid4())
        self.client = AppServerClient(
            self.codex,
            Path(self.manifest["workspace"]),
            self.task_dir / "server.stderr.log",
            self._on_event,
            self._on_server_request,
            self._on_protocol_error,
            self._on_server_started,
        )
        await self.client.start()
        self.status.setdefault("server", {})["state"] = "running"
        if self.lock_record is not None and self.lock_record_path:
            self.lock_record["server_state"] = "running"
            self.lock_record["updated_at"] = _utc_now()
            from fast_agents_runtime import _write_lock_record

            _write_lock_record(self.lock_record_path, self.lock_record)
        self._save_status()
        await self._load_catalog()

    def _on_server_started(self, pid: int) -> None:
        from fast_agents_runtime import capture_process_identity, _write_lock_record

        try:
            identity = capture_process_identity(pid)
        except (OSError, PermissionError) as error:
            raise FastAgentsError(f"app-server process identity is unavailable: {error}") from error
        if identity is None:
            raise FastAgentsError("app-server process identity could not be established")
        self.status.setdefault("server", {}).update({"pid": pid, "identity": identity, "state": "starting"})
        if self.lock_record is not None and self.lock_record_path:
            self.lock_record.update({"server": identity, "server_state": "starting", "updated_at": _utc_now()})
            _write_lock_record(self.lock_record_path, self.lock_record)
        self._save_status()

    async def _load_catalog(self) -> None:
        if self.client is None:
            raise AppServerError("app-server is not connected")
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
            result = await self.client.request("model/list", params)
            page = result.get("data", [])
            if not isinstance(page, list):
                raise AppServerError("model/list returned a malformed catalog")
            models.extend(item for item in page if isinstance(item, dict))
            next_cursor = result.get("nextCursor")
            if next_cursor is not None and not isinstance(next_cursor, str):
                raise AppServerError("model/list returned a malformed pagination cursor")
            cursor = next_cursor or None
            if not cursor:
                break
        self.catalog = {str(model.get("id")): model for model in models if isinstance(model.get("id"), str)}
        for role in self.roles.values():
            model = self.catalog.get(role.model)
            if model is None:
                raise FastAgentsError(f"requested model {role.model} is not available in model/list; choose an available routed option")
            if role.effort not in catalog_efforts(model):
                raise FastAgentsError(f"model {role.model} does not list reasoning effort {role.effort}; choose an available routed option")
            required_tier = "priority" if role.fast_requested else "default"
            if role.fast_requested and required_tier not in catalog_tiers(model):
                raise FastAgentsError(f"model {role.model} does not list the requested {required_tier} service tier; resolve the unavailable tier before launch")
        self.status["model_catalog"] = [
            {
                "id": model.get("id"),
                "efforts": sorted(catalog_efforts(model)),
                "service_tiers": sorted(catalog_tiers(model)),
            }
            for model in models
        ]
        self._save_status()

    async def _run_role(self, role: RolePlan, dependency_context: str) -> None:
        role_state = self.status["roles"][role.name]
        role_dir = self._role_dir(role.name)
        prompt = self._role_prompt(role, dependency_context)
        from fast_agents_common import atomic_write_bytes

        atomic_write_bytes(role_dir / "prompt.txt", prompt.encode("utf-8"))
        stderr_path = role_dir / "stderr.log"
        if not stderr_path.exists():
            descriptor = os.open(stderr_path, os.O_CREAT | os.O_WRONLY, 0o600)
            os.close(descriptor)
            protect_private_path(stderr_path, directory=False)
        role_state.update({"state": "starting", "started_at": _utc_now(), "error": None})
        self._save_status()
        try:
            if self.client is None:
                raise AppServerError("app-server connection is unavailable")
            if not role_state.get("thread_id"):
                role_state["thread_start_state"] = "submitted"
                self._save_status()
                thread_result = await self.client.request(
                    "thread/start",
                    {
                        "model": role.model,
                        "cwd": self.manifest["workspace"],
                        "ephemeral": False,
                        "sandbox": "workspace-write" if role.access == "writer" else "read-only",
                        "approvalPolicy": "on-request",
                        "approvalsReviewer": "auto_review",
                        "serviceTier": "priority" if role.fast_requested else "default",
                        "developerInstructions": _developer_instructions(role),
                    },
                )
                thread = thread_result.get("thread")
                thread_id = thread.get("id") if isinstance(thread, dict) else None
                if not isinstance(thread_id, str):
                    raise AppServerError("thread/start returned no logical thread id")
                role_state["thread_id"] = thread_id
                role_state["thread_start_state"] = "acknowledged"
                role_state["requested_tier"] = "priority" if role.fast_requested else "default"
                self.role_by_thread[thread_id] = role.name
                effective_tier = self._set_effective_tier(
                    role.name,
                    thread_result.get("serviceTier"),
                    reported="serviceTier" in thread_result,
                )
                role_state["effective_settings"] = {
                    "model": thread_result.get("model"),
                    "approval_policy": thread_result.get("approvalPolicy"),
                    "approvals_reviewer": thread_result.get("approvalsReviewer"),
                    "sandbox": thread_result.get("sandbox"),
                }
                mismatch = _thread_settings_mismatch(role, thread_result)
                if mismatch:
                    raise AppServerError(f"thread/start did not retain requested permissions/settings: {mismatch}")
                requested_tier = role_state["requested_tier"]
                if effective_tier != requested_tier:
                    raise AppServerError(f"requested {requested_tier} tier was not effective (reported {thread_result.get('serviceTier')!r}); the turn was not started")
                self._save_status()
            else:
                self.role_by_thread[role_state["thread_id"]] = role.name
            if not role_state.get("turn_id"):
                message_id = role_state.get("client_user_message_id") or str(uuid.uuid4())
                role_state["client_user_message_id"] = message_id
                role_state["turn_start_state"] = "submitted"
                self._save_status()
                turn_params: dict[str, Any] = {
                    "threadId": role_state["thread_id"],
                    "input": [{"type": "text", "text": prompt, "text_elements": []}],
                    "clientUserMessageId": message_id,
                    "effort": role.effort,
                    "serviceTierForTurn": "priority" if role.fast_requested else "default",
                }
                if role.access == "reviewer":
                    turn_params["sandboxPolicy"] = {"type": "readOnly", "networkAccess": False}
                turn_result = await self.client.request(
                    "turn/start",
                    turn_params,
                )
                turn = turn_result.get("turn")
                turn_id = turn.get("id") if isinstance(turn, dict) else None
                if not isinstance(turn_id, str):
                    raise AppServerError("turn/start returned no turn id; execution state is uncertain")
                observed_turn_id = role_state.get("turn_id")
                if observed_turn_id is not None and observed_turn_id != turn_id:
                    role_state.update(
                        {
                            "state": "unknown",
                            "error": "turn/start response conflicts with an already observed turn notification",
                            "recovery_required": True,
                            "finished_at": _utc_now(),
                        }
                    )
                    self.role_done[role.name].set()
                    self._save_status()
                    return
                terminal_notification_seen = (
                    observed_turn_id == turn_id
                    and role_state.get("turn_status") in TERMINAL_TURN_STATES
                    and role_state.get("state") in TERMINAL_TURN_STATES | {"unknown"}
                )
                role_state["turn_id"] = turn_id
                role_state["turn_start_state"] = "acknowledged"
                if not terminal_notification_seen:
                    role_state["turn_status"] = turn.get("status", "inProgress") if isinstance(turn, dict) else "inProgress"
                    role_state["state"] = "running"
                self._save_status()
                if not terminal_notification_seen and isinstance(turn, dict) and turn.get("status") in TERMINAL_TURN_STATES:
                    self._finish_from_turn(role.name, turn)
            if role_state.get("state") not in {"completed", "failed", "interrupted", "unknown"}:
                await self._wait_for_role(role.name)
            if role_state.get("state") == "completed" and not role_state.get("final_report_available"):
                await self._read_final_from_thread(role.name)
            elif role_state.get("turn_status") == "completed" and not role_state.get("final_report_available"):
                await self._read_final_from_thread(role.name)
            self._save_status()
        except AppServerRpcError as error:
            self._invalidate_pending("worker turn failed", role_name=role.name)
            role_state.update({"state": "failed", "error": str(error), "finished_at": _utc_now(), "exit_code": 1})
            self._write_exit(role.name)
            self.role_done[role.name].set()
            self._save_status()
        except (AppServerError, OSError, asyncio.CancelledError) as error:
            if isinstance(error, asyncio.CancelledError):
                raise
            self._invalidate_pending("worker turn could not be reconciled", role_name=role.name)
            if self.client is None or self.client.closed.is_set() or role_state.get("thread_start_state") == "submitted" or role_state.get("turn_start_state") == "submitted":
                role_state.update({"state": "unknown", "error": f"recovery required: {error}", "finished_at": _utc_now()})
                role_state["recovery_required"] = True
                self.recovery_reason = "app-server loss left a submitted request to reconcile"
                if self.client is None or self.client.closed.is_set():
                    self.transport_lost.set()
            else:
                role_state.update({"state": "failed", "error": str(error), "finished_at": _utc_now(), "exit_code": 1})
                self._write_exit(role.name)
            self.role_done[role.name].set()
            self._save_status()
        except FastAgentsError as error:
            self._invalidate_pending("worker turn was blocked", role_name=role.name)
            role_state.update({"state": "blocked", "error": str(error), "finished_at": _utc_now(), "exit_code": 1})
            self._write_exit(role.name)
            self.role_done[role.name].set()
            self._save_status()

    async def _wait_for_role(self, role_name: str) -> None:
        if self.client is None:
            raise AppServerError("app-server is not connected")
        done_task = asyncio.create_task(self.role_done[role_name].wait())
        lost_task = asyncio.create_task(self.client.closed.wait())
        try:
            completed, _ = await asyncio.wait({done_task, lost_task}, return_when=asyncio.FIRST_COMPLETED)
            if lost_task in completed and not self.role_done[role_name].is_set():
                self._invalidate_pending("app-server transport disconnected")
                raise AppServerError("app-server connection closed while the turn was active")
        finally:
            done_task.cancel()
            lost_task.cancel()
            await asyncio.gather(done_task, lost_task, return_exceptions=True)

    async def _read_final_from_thread(self, role_name: str) -> None:
        if self.client is None:
            return
        role_state = self.status["roles"][role_name]
        thread_id = role_state.get("thread_id")
        if not isinstance(thread_id, str):
            return
        result = await self.client.request("thread/read", {"threadId": thread_id, "includeTurns": True})
        thread = result.get("thread", {})
        turns = thread.get("turns", []) if isinstance(thread, dict) else []
        target_turn = role_state.get("turn_id")
        for turn in turns if isinstance(turns, list) else []:
            if isinstance(turn, dict) and turn.get("id") == target_turn:
                report = _assistant_report(turn)
                if report:
                    self._write_final(role_name, report)
                    role_state["final_report_available"] = True
                    if role_state.get("turn_status") == "completed":
                        role_state.update({"state": "completed", "error": None, "exit_code": 0})
                        self._write_exit(role_name)
                return

    async def _schedule(self) -> None:
        max_concurrency = self.manifest.get("max_concurrency")
        if not isinstance(max_concurrency, int) or max_concurrency < 1:
            max_concurrency = min(3, max(1, len(self.roles)))
        while not self.transport_lost.is_set():
            if self.client is None or self.client.closed.is_set():
                self._invalidate_pending("app-server transport disconnected")
                self.transport_lost.set()
                break
            progress = False
            for name, role in self.roles.items():
                role_state = self.status["roles"][name]
                if role_state.get("state") != "waiting" or name in self.role_tasks:
                    continue
                # A terminal notification may still need an asynchronous final-report read.
                # Wait until the dependency coroutine settles before using its outcome.
                if any(dependency in self.role_tasks for dependency in role.depends_on):
                    continue
                dependency_states = [self.status["roles"][dependency].get("state") for dependency in role.depends_on]
                if any(state in {"failed", "blocked", "unknown", "interrupted"} for state in dependency_states):
                    role_state.update({"state": "blocked", "error": "a dependency did not complete successfully", "finished_at": _utc_now()})
                    self._save_status()
                    progress = True
                    continue
                if not all(state == "completed" for state in dependency_states):
                    continue
                if len(self.role_tasks) >= max_concurrency:
                    break
                dependency_context = read_bounded_dependency_context(
                    [
                        (dependency, self.task_dir / "agents" / dependency / "final.md")
                        for dependency in role.depends_on
                    ]
                )
                self.role_tasks[name] = asyncio.create_task(self._run_role(role, dependency_context))
                progress = True

            for name, task in list(self.role_tasks.items()):
                if task.done():
                    try:
                        task.result()
                    except asyncio.CancelledError:
                        pass
                    except Exception as error:
                        role_state = self.status["roles"][name]
                        role_state.update({"state": "failed", "error": f"worker supervisor error: {error}", "finished_at": _utc_now()})
                        self.role_done[name].set()
                    del self.role_tasks[name]
                    progress = True

            if self.transport_lost.is_set():
                break
            if all(value.get("state") in TERMINAL_TURN_STATES | {"blocked", "unknown"} for value in self.status["roles"].values()) and not self.role_tasks:
                break
            if not progress:
                await asyncio.sleep(0.05)
        if self.transport_lost.is_set():
            self._invalidate_pending("app-server transport disconnected")
            self.status["state"] = "recovery_required"
            self.status["action"] = "App-server state needs reconciliation. Use resume --recover; an uncertain turn will not be replayed."
            for name, role_state in self.status["roles"].items():
                if role_state.get("state") in {"running", "starting"}:
                    role_state.update({"state": "unknown", "recovery_required": True, "error": "app-server ended before this turn was reconciled"})
                    self.role_done[name].set()
            self.status["server"]["state"] = "lost"
            self._save_status()
            return
        self._set_terminal_task_state()

    def _set_terminal_task_state(self) -> None:
        states = [item.get("state") for item in self.status["roles"].values()]
        if states and all(state == "completed" for state in states):
            self.status["state"] = "completed"
            self.status["action"] = "Review the final reports and verify the shared contract."
        elif any(state in {"failed", "blocked", "interrupted"} for state in states):
            self.status["state"] = "failed"
            self.status["action"] = "Review the failed role and pending approvals before any explicit relaunch."
        elif any(state == "unknown" for state in states):
            self.status["state"] = "recovery_required"
            self.status["action"] = "A thread or turn could not be proven terminal; reconcile it before starting more work."
        else:
            self.status["state"] = "completed"
            self.status["action"] = "All worker roles reached a terminal state."
        self._save_status()

    async def _reconcile(self) -> dict[str, Any]:
        if self.client is None or self.client.closed.is_set():
            if self.client is not None and self.client.process is not None and self.client.process.returncode is None:
                raise FastAgentsError("previous app-server process is still alive; recovery will not start a second server")
            await self._start_client()
        assert self.client is not None
        unresolved: list[str] = []
        for name, role in self.roles.items():
            state = self.status["roles"][name]
            thread_id = state.get("thread_id")
            if not thread_id:
                if state.get("thread_start_state") == "submitted":
                    unresolved.append(f"{name}: thread/start result was lost before its thread id was saved")
                continue
            self.role_by_thread[thread_id] = name
            try:
                resume_result = await self.client.request("thread/resume", {"threadId": thread_id})
                result = await self.client.request("thread/read", {"threadId": thread_id, "includeTurns": True})
            except AppServerError as error:
                unresolved.append(f"{name}: thread could not be resumed/read: {error}")
                continue
            mismatch = _thread_settings_mismatch(role, resume_result)
            if mismatch:
                unresolved.append(f"{name}: resumed settings differ from the saved role contract: {mismatch}")
                continue
            resumed_tier = self._set_effective_tier(
                name,
                resume_result.get("serviceTier"),
                reported="serviceTier" in resume_result,
            )
            requested_tier = "priority" if role.fast_requested else "default"
            if resumed_tier != requested_tier:
                unresolved.append(f"{name}: saved {requested_tier} tier is not effective after resume")
                continue
            thread = result.get("thread", {})
            turns = thread.get("turns", []) if isinstance(thread, dict) else []
            turn_id = state.get("turn_id")
            matching = [turn for turn in turns if isinstance(turn, dict) and turn.get("id") == turn_id] if isinstance(turns, list) else []
            if not turn_id and state.get("turn_start_state") == "submitted":
                expected_message = state.get("client_user_message_id")
                matching = [turn for turn in turns if isinstance(turn, dict) and _turn_matches_client_message(turn, expected_message)] if isinstance(turns, list) else []
                if len(matching) == 1:
                    turn_id = matching[0].get("id")
                    state["turn_id"] = turn_id
                else:
                    unresolved.append(f"{name}: turn/start outcome cannot be identified without replay")
                    continue
            elif not turn_id and state.get("turn_start_state") != "submitted":
                thread_status = thread.get("status") if isinstance(thread, dict) else None
                if isinstance(thread_status, dict) and thread_status.get("type") == "idle":
                    state.update({"state": "waiting", "thread_start_state": "acknowledged"})
                else:
                    unresolved.append(f"{name}: thread is not proven idle before its first turn")
                    continue
            if turn_id:
                if len(matching) != 1:
                    unresolved.append(f"{name}: persisted turn id is absent or ambiguous in thread history")
                    continue
                turn = matching[0]
                turn_status = turn.get("status")
                usage = turn.get("tokenUsage")
                if isinstance(usage, dict):
                    state["usage"] = usage
                if turn_status in TERMINAL_TURN_STATES:
                    self._finish_from_turn(name, turn)
                else:
                    unresolved.append(f"{name}: turn {turn_id} remains {turn_status or 'unknown'}")
            elif state.get("state") not in {"waiting", "completed", "failed", "blocked"}:
                unresolved.append(f"{name}: worker state cannot be reconciled")

        if unresolved:
            for pending in self.pending_requests.values():
                pending["state"] = "recovery_required"
                pending["recovery_note"] = "request id belongs to the previous app-server connection; wait for a current request before replying"
            self.status["state"] = "recovery_required"
            self.status["recovery_findings"] = unresolved
            self.status["action"] = "Recovery found uncertain thread or turn state; no new turn was started. Resolve the listed state before relaunch."
            self._save_status()
            return {"reconciled": False, "findings": unresolved}
        self.status["state"] = "running"
        self.status["action"] = "Persisted threads and turns were reconciled; unstarted dependency-ready roles may continue."
        self.status.pop("recovery_findings", None)
        self.transport_lost.clear()
        self._save_status()
        self._start_scheduler()
        return {"reconciled": True, "findings": []}

    async def _handle_control(self, request: dict[str, Any]) -> dict[str, Any]:
        command = request.get("command")
        if command == "status":
            status = json.loads(json.dumps(self.status))
            status["pending_requests"] = [self._public_pending(item) for item in self.pending_requests.values()]
            return status
        if command == "events":
            after = request.get("after_seq", 0)
            limit = request.get("limit", 100)
            if not isinstance(after, int) or after < 0 or not isinstance(limit, int) or not 1 <= limit <= 100:
                raise FastAgentsError("events requires after_seq >= 0 and limit between 1 and 100")
            return {"events": [event for event in self.event_summaries if event["seq"] > after][:limit], "latest_seq": self.event_sequence}
        if command == "steer":
            role_name = _required_string(request.get("role"), "role")
            turn_id = _required_string(request.get("turn_id"), "turn_id")
            text = _required_string(request.get("text"), "text")
            if len(text) > 16_000:
                raise FastAgentsError("steer text exceeds 16000 characters")
            role_state = self._require_current_turn(role_name, turn_id)
            assert self.client is not None
            result = await self.client.request(
                "turn/steer",
                {"threadId": role_state["thread_id"], "expectedTurnId": turn_id, "input": [{"type": "text", "text": text, "text_elements": []}]},
            )
            ack_turn = result.get("turnId")
            self._summary_event("control/steer.ack", {"threadId": role_state["thread_id"], "turnId": ack_turn, "itemId": None}, role_name)
            self._save_status()
            return {"acknowledged": True, "role": role_name, "thread_id": role_state["thread_id"], "turn_id": ack_turn}
        if command == "interrupt":
            role_name = _required_string(request.get("role"), "role")
            turn_id = _required_string(request.get("turn_id"), "turn_id")
            role_state = self._require_current_turn(role_name, turn_id)
            assert self.client is not None
            await self.client.request("turn/interrupt", {"threadId": role_state["thread_id"], "turnId": turn_id})
            self._summary_event("control/interrupt.ack", {"threadId": role_state["thread_id"], "turnId": turn_id}, role_name)
            self._save_status()
            return {"acknowledged": True, "role": role_name, "thread_id": role_state["thread_id"], "turn_id": turn_id}
        if command == "reply":
            return await self._reply_pending(request)
        if command == "inspect-request":
            return self._inspect_pending(request)
        if command == "recover":
            async with self.recovery_lock:
                result = await self._reconcile()
            return result
        raise FastAgentsError(f"unsupported control command: {command}")

    def _require_current_turn(self, role_name: str, turn_id: str) -> dict[str, Any]:
        if role_name not in self.roles:
            raise FastAgentsError(f"unknown worker role: {role_name}")
        state = self.status["roles"][role_name]
        if state.get("state") != "running" or state.get("turn_id") != turn_id:
            raise FastAgentsError("worker turn is no longer current; refresh status and use its current turn id")
        if self.client is None or self.client.closed.is_set():
            raise FastAgentsError("app-server is unavailable; recover and reconcile before steering")
        return state

    async def _reply_pending(self, request: dict[str, Any]) -> dict[str, Any]:
        request_id = request.get("request_id")
        key = self._request_key(request_id)
        pending = self.pending_requests.get(key)
        if pending is None:
            raise FastAgentsError("server request is no longer pending")
        if request.get("thread_id") != pending.get("thread_id") or request.get("turn_id") != pending.get("turn_id"):
            raise FastAgentsError("thread or turn id does not match this pending request")
        if not self._pending_is_current(key, pending):
            self._invalidate_pending("request is no longer bound to a live turn", role_name=pending.get("role"), turn_id=pending.get("turn_id"))
            raise FastAgentsError("server request is stale or no longer belongs to the current live turn")
        role = self.roles[pending["role"]]
        response = _validate_server_reply(pending, request, role, Path(self.manifest["workspace"]))
        assert self.client is not None
        await self.client.respond(request_id, response)
        self.pending_requests.pop(key, None)
        self._summary_event("control/server-request.ack", {"threadId": pending.get("thread_id"), "turnId": pending.get("turn_id"), "itemId": pending.get("item_id")}, pending.get("role"))
        self._save_status()
        return {"acknowledged": True, "request_id": request_id, "thread_id": pending.get("thread_id"), "turn_id": pending.get("turn_id")}

    def _pending_is_current(self, key: str, pending: dict[str, Any]) -> bool:
        if self.client is None or self.client.closed.is_set() or self.connection_epoch is None:
            return False
        if pending.get("transport_epoch") != self.connection_epoch:
            return False
        live_request = self.client.server_requests.get(key)
        if not isinstance(live_request, dict) or live_request != pending.get("server_request"):
            return False
        role_name = pending.get("role")
        if role_name not in self.roles:
            return False
        return self._request_matches_live_turn(role_name, pending.get("thread_id"), pending.get("turn_id"))

    def _inspect_pending(self, request: dict[str, Any]) -> dict[str, Any]:
        request_id = request.get("request_id")
        key = self._request_key(request_id)
        pending = self.pending_requests.get(key)
        if pending is None or not self._pending_is_current(key, pending):
            if pending is not None:
                self._invalidate_pending("request is no longer bound to a live turn", role_name=pending.get("role"), turn_id=pending.get("turn_id"))
            raise FastAgentsError("server request is stale or no longer pending")
        return {
            "sensitive": True,
            "warning": "This explicit inspection contains raw command, path, permission, or elicitation details.",
            "request": {"request_id": pending["request_id"], "method": pending["method"], "params": pending["params"]},
        }

    async def run(self) -> int:
        from fast_agents_runtime import _acquire_task_lock, _acquire_workspace_lock, _workspace_lock_paths, _write_lock_record, _write_ready, capture_process_identity

        manager_identity = capture_process_identity(os.getpid())
        if manager_identity is None:
            raise FastAgentsError("supervisor process identity could not be established")
        self.task_lock_descriptor = _acquire_task_lock(self.task_dir / "supervisor.lock")
        self._save_status()  # Remove any pending data written by older versions before serving status.
        try:
            if any(role.access == "writer" for role in self.roles.values()):
                self.lock_path, self.lock_record_path = _workspace_lock_paths(self.runtime_root, Path(self.manifest["workspace"]))
                self.lock_record = {
                    "task_id": self.status["task_id"],
                    "workspace": self.manifest["workspace"],
                    "manager_state": "running",
                    "manager": manager_identity,
                    "server": self.status.get("server", {}).get("identity"),
                    "server_state": "not_started",
                    "processes": [],
                    "updated_at": _utc_now(),
                }
                self.lock_descriptor = _acquire_workspace_lock(
                    self.lock_path,
                    Path(self.manifest["workspace"]),
                    self.lock_record,
                )
        except BaseException as error:
            if self.lock_descriptor is not None:
                os.close(self.lock_descriptor)
                self.lock_descriptor = None
            if self.task_lock_descriptor is not None:
                os.close(self.task_lock_descriptor)
                self.task_lock_descriptor = None
            self.status["state"] = "failed"
            self.status["action"] = str(error)
            self.status.setdefault("manager", {})["state"] = "failed"
            self._save_status()
            _write_ready(self.task_dir, {"ok": False, "error": str(error)})
            raise
        self.status["manager"] = {"pid": os.getpid(), "identity": manager_identity, "state": "running"}
        self.status["backend"] = "app-server"
        self.status["platform_coverage"] = platform_coverage_label()
        self.status["state"] = "starting"
        self._save_status()
        try:
            await self._start_client()
            self.control = ControlServer(self.task_dir, self._handle_control)
            await self.control.start()
            if self.recover_on_start:
                result = await self._reconcile()
                if not result["reconciled"]:
                    self.status["manager"]["state"] = "running"
                    self._save_status()
            else:
                self.status["state"] = "running"
                self.status["action"] = "Use control status/events to poll progress; steering and replies require current ids."
                self._save_status()
                self._start_scheduler()
            _write_ready(self.task_dir, {"ok": True, "task_id": self.status["task_id"], "manager_pid": os.getpid(), "backend": "app-server"})
            await self.finished.wait()
            return await self._shutdown()
        except Exception as error:
            self._invalidate_pending("supervisor stopped")
            self.status["state"] = "failed"
            self.status["action"] = str(error)
            self.status["manager"]["state"] = "failed"
            self._save_status()
            from fast_agents_runtime import _write_ready

            _write_ready(self.task_dir, {"ok": False, "error": str(error)})
            if self.client is not None and self.client.process is not None and self.client.process.returncode is None:
                # Do not kill an app-server on error. Its owned stdio is allowed to close naturally.
                if self.client.process.stdin and not self.client.process.stdin.is_closing():
                    self.client.process.stdin.close()
                await self.client.process.wait()
            if self.control:
                await self.control.close()
            if self.lock_descriptor is not None:
                os.close(self.lock_descriptor)
                self.lock_descriptor = None
            if self.task_lock_descriptor is not None:
                os.close(self.task_lock_descriptor)
                self.task_lock_descriptor = None
            return 2

    async def _shutdown(self) -> int:
        self._invalidate_pending("supervisor stopped")
        exit_result = 0 if self.status.get("state") == "completed" else 1
        if self.client and self.client.process and self.client.process.returncode is None:
            code = await self.client.close_stdin_and_wait()
            self.status.setdefault("server", {}).update({"state": "stopped", "exit_code": code})
            if self.lock_record is not None:
                self.lock_record["server_state"] = "stopped"
            atomic_write_json(self.task_dir / "server.exit.json", {"exit_code": code, "finished_at": _utc_now()})
        if self.control:
            await self.control.close()
        self.status["manager"]["state"] = "completed" if exit_result == 0 else "failed"
        self._save_status()
        if self.lock_record is not None and self.lock_record_path:
            self.lock_record["manager_state"] = self.status["manager"]["state"]
            self.lock_record["updated_at"] = _utc_now()
            from fast_agents_runtime import _write_lock_record

            _write_lock_record(self.lock_record_path, self.lock_record)
        if self.lock_descriptor is not None:
            os.close(self.lock_descriptor)
            self.lock_descriptor = None
        if self.task_lock_descriptor is not None:
            os.close(self.task_lock_descriptor)
            self.task_lock_descriptor = None
        return exit_result

    def _start_scheduler(self) -> None:
        if self.scheduler_task is None or self.scheduler_task.done():
            self.scheduler_task = asyncio.create_task(self._schedule_then_finish())

    async def _schedule_then_finish(self) -> None:
        await self._schedule()
        if self.status.get("state") != "recovery_required":
            self.finished.set()


async def _start_supervisor(task_dir: Path, runtime_root: Path, codex: str, recover: bool) -> int:
    supervisor = AppServerSupervisor(task_dir, runtime_root, codex, recover=recover)
    return await supervisor.run()


def supervise_app_server(task_dir: Path, runtime_root: Path, codex: str, *, recover: bool = False) -> int:
    return asyncio.run(_start_supervisor(task_dir, runtime_root, codex, recover))


def _utc_now() -> str:
    from fast_agents_runtime import utc_now

    return utc_now()


def _safe_text(value: Any) -> str:
    if not isinstance(value, str):
        return "unknown failure"
    return value[:1000]


def _required_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise FastAgentsError(f"{label} must be a non-empty string")
    return value.strip()


def _developer_instructions(role: RolePlan) -> str:
    access = "read-only reviewer" if role.access == "reviewer" else "workspace-write worker"
    return (
        f"You are a {access} in a user-authorized fast-agents task. Do not delegate or spawn agents. "
        "Follow the loaded repository and user instructions. The supervisor applies on-request approvals "
        "with Codex automatic review; it never blanket-approves a server request."
    )


def _thread_settings_mismatch(role: RolePlan, settings: dict[str, Any]) -> str | None:
    expected_sandbox = "workspace-write" if role.access == "writer" else "read-only"
    actual_sandbox = _sandbox_label(settings.get("sandbox"))
    checks = {
        "model": settings.get("model") == role.model,
        "approvalPolicy": _policy_label(settings.get("approvalPolicy")) == "on-request",
        "approvalsReviewer": _policy_label(settings.get("approvalsReviewer")) in {"auto_review", "guardian_subagent"},
        "sandbox": actual_sandbox == expected_sandbox,
    }
    mismatches = [name for name, matched in checks.items() if not matched]
    return ", ".join(mismatches) if mismatches else None


def _policy_label(value: Any) -> Any:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return value.get("type") or value.get("approvalPolicy") or value.get("approvalsReviewer")
    return value


def _sandbox_label(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        label = value.get("type") or value.get("mode")
        return {
            "readOnly": "read-only",
            "workspaceWrite": "workspace-write",
            "workspace-write": "workspace-write",
            "read-only": "read-only",
        }.get(label, label if isinstance(label, str) else None)
    return None


def _assistant_report(turn: dict[str, Any]) -> str:
    items = turn.get("items", [])
    if not isinstance(items, list):
        return ""
    messages = [item.get("text", "") for item in items if isinstance(item, dict) and item.get("type") == "agentMessage" and isinstance(item.get("text"), str)]
    return "\n\n".join(message for message in messages if message.strip()).strip()


def _turn_matches_client_message(turn: dict[str, Any], client_message_id: Any) -> bool:
    if not isinstance(client_message_id, str):
        return False
    items = turn.get("items", [])
    for item in items if isinstance(items, list) else []:
        if isinstance(item, dict) and item.get("type") == "userMessage" and item.get("clientId") == client_message_id:
            return True
    return False


def _event_summary(method: str, params: dict[str, Any]) -> str:
    if method == "turn/completed":
        turn = params.get("turn", {})
        return f"turn {turn.get('status', 'completed')}" if isinstance(turn, dict) else "turn completed"
    if method in {"item/started", "item/completed"}:
        item = params.get("item", {})
        kind = item.get("type", "item") if isinstance(item, dict) else "item"
        return f"{kind} {'started' if method.endswith('started') else 'completed'}"
    if "guardian" in method.casefold() and "review" in method.casefold():
        return "Codex automatic approval review updated"
    if method == "thread/tokenUsage/updated":
        return "thread token usage updated"
    if method == "error":
        error = params.get("error", {})
        return _safe_text(error.get("message") if isinstance(error, dict) else error)
    return method.replace("/", " ")


def _guardian_summary(method: str, params: dict[str, Any], role_name: str) -> dict[str, Any]:
    review = params.get("review")
    review_status = review.get("status") if isinstance(review, dict) else None
    return {
        "method": method,
        "role": role_name,
        "thread_id": params.get("threadId"),
        "turn_id": params.get("turnId"),
        "review_id": params.get("reviewId"),
        "action": params.get("action"),
        "decision_source": params.get("decisionSource"),
        "status": review_status,
    }


def _request_summary(method: str, params: dict[str, Any]) -> str:
    if method == "item/commandExecution/requestApproval":
        return _safe_text(params.get("command") or params.get("reason") or "command approval requested")
    if method == "item/fileChange/requestApproval":
        return _safe_text(params.get("reason") or "file change approval requested")
    if method == "item/permissions/requestApproval":
        return _safe_text(params.get("reason") or "permissions approval requested")
    if method == "item/tool/requestUserInput":
        return "worker is waiting for user input"
    if method == "mcpServer/elicitation/request":
        return f"MCP server {params.get('serverName', 'unknown')} requests user input"
    return f"pending app-server request: {method}"


def _path_is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(root.resolve(strict=False))
        return True
    except ValueError:
        return False


def _permission_path(value: Any, workspace: Path, role: RolePlan, access: str) -> Path:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise FastAgentsError("filesystem permission paths must be non-empty strings")
    raw = Path(value).expanduser()
    resolved = (raw if raw.is_absolute() else workspace / raw).resolve(strict=False)
    canonical_workspace = workspace.resolve(strict=False)
    if not _path_is_within(resolved, canonical_workspace):
        raise FastAgentsError("filesystem permission path escapes the worker workspace")
    if access == "write":
        if role.access != "writer":
            raise FastAgentsError("read-only roles cannot grant filesystem writes")
        owned_roots = [
            (canonical_workspace / Path(*item.split("/"))).resolve(strict=False)
            for item in role.owns
        ]
        if not any(_path_is_within(resolved, owned) for owned in owned_roots):
            raise FastAgentsError("filesystem write grant is outside this writer's declared ownership")
    return resolved


def _validate_permission_entry(entry: Any, requested_entries: list[Any], workspace: Path, role: RolePlan) -> None:
    if not isinstance(entry, dict) or set(entry) != {"access", "path"}:
        raise FastAgentsError("filesystem permission entries must contain only access and path")
    access = entry.get("access")
    if access not in {"read", "write", "deny"}:
        raise FastAgentsError("filesystem permission entry has an unsupported access mode")
    path_spec = entry.get("path")
    if not isinstance(path_spec, dict):
        raise FastAgentsError("filesystem permission entry path must be a tagged object")
    path_type = path_spec.get("type")
    if path_type != "path" or set(path_spec) != {"type", "path"}:
        raise FastAgentsError("filesystem grants support only confined path entries")
    resolved = _permission_path(path_spec.get("path"), workspace, role, access)
    if entry not in requested_entries:
        raise FastAgentsError("filesystem permission entry was not requested")
    requested_path = next(
        item["path"]["path"]
        for item in requested_entries
        if isinstance(item, dict) and item.get("access") == access and item.get("path") == path_spec
    )
    _permission_path(requested_path, workspace, role, access)
    if not _path_is_within(resolved, _permission_path(requested_path, workspace, role, access)):
        raise FastAgentsError("filesystem permission path widens the requested path")


def _validate_filesystem_grant(
    granted: Any,
    requested: Any,
    workspace: Path,
    role: RolePlan,
) -> dict[str, Any] | None:
    if granted is None:
        return None
    if not isinstance(granted, dict) or set(granted) - {"entries", "globScanMaxDepth", "read", "write"}:
        raise FastAgentsError("filesystem permissions contain an unsupported field")
    if not isinstance(requested, dict) or set(requested) - {"entries", "globScanMaxDepth", "read", "write"}:
        raise FastAgentsError("requested filesystem permissions have an unsupported shape")
    result: dict[str, Any] = {}
    if "entries" in granted:
        requested_entries = requested.get("entries")
        granted_entries = granted["entries"]
        if not isinstance(granted_entries, list) or not isinstance(requested_entries, list):
            raise FastAgentsError("filesystem entries may be granted only when requested as an array")
        for entry in granted_entries:
            _validate_permission_entry(entry, requested_entries, workspace, role)
        result["entries"] = granted_entries
    if "globScanMaxDepth" in granted:
        requested_depth = requested.get("globScanMaxDepth")
        granted_depth = granted["globScanMaxDepth"]
        if (
            isinstance(granted_depth, bool)
            or not isinstance(granted_depth, int)
            or isinstance(requested_depth, bool)
            or not isinstance(requested_depth, int)
            or granted_depth < 1
            or granted_depth > requested_depth
        ):
            raise FastAgentsError("filesystem glob scan depth may not exceed the requested depth")
        result["globScanMaxDepth"] = granted_depth
    for access in ("read", "write"):
        if access not in granted:
            continue
        requested_paths = requested.get(access)
        granted_paths = granted[access]
        if not isinstance(granted_paths, list) or not isinstance(requested_paths, list):
            raise FastAgentsError(f"filesystem {access} paths may be granted only when requested as an array")
        safe_paths: list[str] = []
        for value in granted_paths:
            candidate = _permission_path(value, workspace, role, access)
            requested_candidates = [
                _permission_path(requested_value, workspace, role, access)
                for requested_value in requested_paths
                if isinstance(requested_value, str)
            ]
            if not any(_path_is_within(candidate, requested_candidate) for requested_candidate in requested_candidates):
                raise FastAgentsError(f"filesystem {access} grant widens or changes the requested paths")
            safe_paths.append(value)
        result[access] = safe_paths
    return result


def _validate_permission_grant(payload: Any, pending: dict[str, Any], role: RolePlan, workspace: Path) -> dict[str, Any]:
    if not isinstance(payload, dict) or set(payload) - {"permissions", "scope", "strictAutoReview"}:
        raise FastAgentsError("permissions reply must match the 0.162.1 response schema")
    permissions = payload.get("permissions")
    requested = pending.get("params", {}).get("permissions")
    if not isinstance(permissions, dict) or set(permissions) - {"fileSystem", "network"}:
        raise FastAgentsError("permissions reply requires only fileSystem and network grants")
    if not isinstance(requested, dict) or set(requested) - {"fileSystem", "network"}:
        raise FastAgentsError("requested permissions have an unsupported shape")
    result_permissions: dict[str, Any] = {}
    if "fileSystem" in permissions and permissions["fileSystem"] is not None:
        if "fileSystem" not in requested:
            raise FastAgentsError("filesystem permissions were not requested")
        result_permissions["fileSystem"] = _validate_filesystem_grant(
            permissions["fileSystem"], requested["fileSystem"], workspace, role
        )
    if "network" in permissions and permissions["network"] is not None:
        network = permissions["network"]
        requested_network = requested.get("network")
        if not isinstance(network, dict) or set(network) - {"enabled"}:
            raise FastAgentsError("network permissions must contain only enabled")
        if not isinstance(requested_network, dict) or "enabled" not in requested_network:
            raise FastAgentsError("network permission was not requested")
        enabled = network.get("enabled")
        requested_enabled = requested_network.get("enabled")
        if not isinstance(enabled, bool) or not isinstance(requested_enabled, bool):
            raise FastAgentsError("network enabled must be a requested boolean value")
        if enabled and not requested_enabled:
            raise FastAgentsError("network reply widens the requested access")
        if role.access == "reviewer" and enabled:
            raise FastAgentsError("read-only roles cannot grant network access")
        result_permissions["network"] = {"enabled": enabled}
    scope = payload.get("scope", "turn")
    if scope not in {"turn", "session"}:
        raise FastAgentsError("permissions reply scope must be turn or session")
    strict_auto_review = payload.get("strictAutoReview")
    if strict_auto_review is False:
        raise FastAgentsError("permission grants must retain automatic review")
    if strict_auto_review is not None and not isinstance(strict_auto_review, bool):
        raise FastAgentsError("strictAutoReview must be true, null, or omitted")
    result: dict[str, Any] = {"permissions": result_permissions, "scope": scope}
    if strict_auto_review is True:
        result["strictAutoReview"] = True
    return result


def _validate_server_reply(
    pending: dict[str, Any],
    request: dict[str, Any],
    role: RolePlan,
    workspace: Path,
) -> dict[str, Any]:
    method = pending.get("method")
    payload = request.get("payload")
    if method in {"item/commandExecution/requestApproval", "item/fileChange/requestApproval"}:
        if payload is not None:
            raise FastAgentsError("approval replies accept only the explicit decision field")
        decision = request.get("decision")
        if decision not in {"accept", "acceptForSession", "decline", "cancel"}:
            raise FastAgentsError("approval decision must be accept, acceptForSession, decline, or cancel")
        if role.access != "writer" and decision in {"accept", "acceptForSession"}:
            raise FastAgentsError("read-only roles cannot approve commands or file changes")
        return {"decision": decision}
    if method == "item/permissions/requestApproval":
        if request.get("decision") is not None:
            raise FastAgentsError("permissions replies accept only the method-specific payload")
        return _validate_permission_grant(payload, pending, role, workspace)
    if method == "item/tool/requestUserInput":
        if request.get("decision") is not None or not isinstance(payload, dict) or set(payload) != {"answers"}:
            raise FastAgentsError("user-input reply must match the requestUserInput response schema")
        questions = pending.get("params", {}).get("questions")
        if not isinstance(questions, list) or not questions:
            raise FastAgentsError("pending user-input request has no valid questions")
        question_ids = [question.get("id") for question in questions if isinstance(question, dict)]
        if len(question_ids) != len(questions) or any(not isinstance(item, str) or not item for item in question_ids):
            raise FastAgentsError("pending user-input request has invalid question ids")
        if len(set(question_ids)) != len(question_ids):
            raise FastAgentsError("pending user-input request repeats a question id")
        answers = payload.get("answers")
        if not isinstance(answers, dict) or set(answers) != set(question_ids):
            raise FastAgentsError("user-input reply must answer each current question exactly once")
        for question_id, answer in answers.items():
            if not isinstance(answer, dict) or set(answer) != {"answers"}:
                raise FastAgentsError("each user-input answer must contain only an answers array")
            values = answer.get("answers")
            if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
                raise FastAgentsError("each user-input answers field must be an array of strings")
        return {"answers": answers}
    if method == "mcpServer/elicitation/request":
        if request.get("decision") is not None or not isinstance(payload, dict) or set(payload) - {"action", "content"}:
            raise FastAgentsError("MCP reply must match the elicitation response schema")
        action = payload.get("action")
        if action not in {"accept", "decline", "cancel"}:
            raise FastAgentsError("MCP reply requires action accept, decline, or cancel")
        content = payload.get("content")
        if action == "accept" and not isinstance(content, dict):
            raise FastAgentsError("accepted MCP elicitation requires structured content")
        if action != "accept" and content is not None:
            raise FastAgentsError("declined or cancelled MCP elicitation cannot include content")
        return {"action": action, "content": content if action == "accept" else None}
    raise FastAgentsError(f"unsupported server-request method: {method}")
