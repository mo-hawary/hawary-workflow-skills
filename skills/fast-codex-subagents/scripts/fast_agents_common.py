"""Shared manifest, configuration, and durable-file helpers for Fast Codex Subagents."""

from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import tempfile
import time
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, TypeVar


SCHEMA_VERSION = 1
WRITER_MODEL = "gpt-6-luna"
REVIEWER_MODEL = "gpt-6.1-sol"
SUPPORTED_REASONING_EFFORTS = frozenset({"none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"})
ROLE_NAME = re.compile(r"\A[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}\Z")
MAX_OBJECTIVE_CHARS = 4_000
MAX_SHARED_CONTRACT_CHARS = 12_000
MAX_ROLE_PROMPT_CHARS = 16_000
MAX_DEPENDENCY_CONTEXT_CHARS = 12_000
DEPENDENCY_CONTEXT_TRUNCATION_MARKER = "[dependency context truncated at 12000 characters]"
SUPPORTED_APP_SERVER_REQUESTS = frozenset(
    {
        "item/commandExecution/requestApproval",
        "item/fileChange/requestApproval",
        "item/permissions/requestApproval",
        "item/tool/requestUserInput",
        "mcpServer/elicitation/request",
    }
)
WINDOWS_DEVICE_NAMES = {
    "con",
    "prn",
    "aux",
    "nul",
    "conin$",
    "conout$",
    "clock$",
    *(f"com{index}" for index in range(1, 10)),
    *(f"lpt{index}" for index in range(1, 10)),
}
ROLE_ARTIFACT_STEMS = {"agents", "events", "exit", "final", "manifest", "prompt", "server", "status", "stderr"}
_WINDOWS_SHARING_VIOLATIONS = frozenset({5, 32, 33})
_WINDOWS_SHARING_RETRY_DELAYS = (0.02, 0.04, 0.08, 0.16, 0.32)
_Result = TypeVar("_Result")


class FastAgentsError(ValueError):
    """A user-correctable manifest or runtime configuration error."""


@dataclass(frozen=True)
class Role:
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
    setting_sources: dict[str, str]
    budgets: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TaskManifest:
    workspace: Path
    objective: str
    shared_contract: str
    fast_requested: bool
    fast_selection: dict[str, Any]
    backend: str
    max_concurrency: int
    plan_mode: bool
    roles: tuple[Role, ...]
    source_path: Path
    budgets: dict[str, Any] = field(default_factory=dict)
    isolation: str = "shared"
    acceptance: tuple[tuple[str, ...], ...] = ()

    @property
    def has_writers(self) -> bool:
        return any(role.access == "writer" for role in self.roles)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 128), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_windows_host() -> bool:
    return os.name == "nt"


def _retry_windows_sharing_violation(
    operation: Callable[[], _Result], *, retry_unmapped_access_denied: bool = False
) -> _Result:
    """Retry only bounded Windows file-sharing conflicts around the supplied I/O call."""
    if not _is_windows_host():
        return operation()

    for delay in _WINDOWS_SHARING_RETRY_DELAYS:
        try:
            return operation()
        except OSError as error:
            winerror = getattr(error, "winerror", None)
            retry = winerror in _WINDOWS_SHARING_VIOLATIONS
            # Path.read_text on Windows can report a sharing denial as EACCES without winerror.
            if winerror is None and retry_unmapped_access_denied:
                retry = error.errno == errno.EACCES
            if not retry:
                raise
            time.sleep(delay)

    return operation()


def atomic_write_bytes(path: Path, content: bytes, mode: int = 0o600, *, private: bool = True) -> None:
    """Replace path atomically. private=False keeps user-owned files at mode and skips owner-only ACL/chmod."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    from fast_agents_platform import protect_private_path

    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    fd_open = True
    try:
        if private:
            protect_private_path(temporary, directory=False)
        if os.name != "nt":
            os.chmod(temporary, mode)
        stream = os.fdopen(fd, "wb")
        # Once fdopen succeeds, the stream owns fd and closes it; never os.close it again.
        fd_open = False
        with stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        _retry_windows_sharing_violation(lambda: os.replace(temporary, path))
        if private:
            protect_private_path(path, directory=False)
    except BaseException:
        if fd_open:
            try:
                os.close(fd)
            except OSError:
                pass
        temporary.unlink(missing_ok=True)
        raise


def atomic_write_json(path: Path, value: Any, mode: int = 0o600, *, private: bool = True) -> None:
    encoded = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    atomic_write_bytes(path, encoded, mode=mode, private=private)


def read_json(path: Path) -> dict[str, Any]:
    try:
        raw = _retry_windows_sharing_violation(
            lambda: path.read_text(encoding="utf-8"), retry_unmapped_access_denied=True
        )
        value = json.loads(raw)
    except FileNotFoundError:
        raise
    except (OSError, json.JSONDecodeError) as error:
        raise FastAgentsError(f"cannot read valid JSON from {path.name}: {error}") from error
    if not isinstance(value, dict):
        raise FastAgentsError(f"{path.name} must contain a JSON object")
    return value


def _required_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise FastAgentsError(f"{label} must be a non-empty string")
    return value.strip()


def _validate_role_name(value: Any, label: str) -> str:
    name = _required_text(value, label)
    if not ROLE_NAME.fullmatch(name):
        raise FastAgentsError(f"{label} must use letters, numbers, '_' or '-' and be at most 64 characters")
    portable_name = unicodedata.normalize("NFKC", name).casefold().rstrip(" .")
    device_stem = portable_name.split(".", 1)[0]
    if device_stem in WINDOWS_DEVICE_NAMES:
        raise FastAgentsError(f"{label} is a reserved device name on common filesystems")
    if portable_name in ROLE_ARTIFACT_STEMS:
        raise FastAgentsError(f"{label} conflicts with a Fast Codex Subagents artifact name")
    return name


def read_bounded_dependency_context(reports: list[tuple[str, Path]]) -> str:
    """Read dependency reports into one bounded prompt section without loading whole files."""
    if not reports:
        return ""
    marker = DEPENDENCY_CONTEXT_TRUNCATION_MARKER
    body_limit = MAX_DEPENDENCY_CONTEXT_CHARS - len(marker) - 2
    sections: list[str] = []
    used = 0
    truncated = False
    for index, (name, path) in enumerate(reports):
        prefix = "\n\n" if sections else ""
        heading = f"--- {name} ---\n"
        available = body_limit - used - len(prefix) - len(heading)
        if available <= 0:
            truncated = True
            break
        try:
            with path.open("r", encoding="utf-8", errors="replace") as stream:
                report = stream.read(available + 1)
        except OSError:
            report = "(dependency final report unavailable)"
        if len(report) > available:
            report = report[:available]
            truncated = True
        sections.append(prefix + heading + report)
        used += len(prefix) + len(heading) + len(report)
        if truncated:
            break
        if index + 1 < len(reports) and used >= body_limit:
            truncated = True
            break
    context = "".join(sections)
    if truncated:
        context = context.rstrip() + "\n\n" + marker
    return context[:MAX_DEPENDENCY_CONTEXT_CHARS]


def _string_list(value: Any, label: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise FastAgentsError(f"{label} must be an array of non-empty strings")
    return tuple(item.strip() for item in value)


def _owned_path(raw: str, role_name: str, workspace: Path) -> str:
    if "\\" in raw or "\x00" in raw or any(char in raw for char in '<>:"|'):
        raise FastAgentsError(f"role {role_name}: owned paths must use safe relative POSIX paths")
    path = PurePosixPath(raw)
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in raw.split("/")):
        raise FastAgentsError(f"role {role_name}: owned path must stay inside the workspace: {raw}")
    if any(char in raw for char in "*?[]"):
        raise FastAgentsError(f"role {role_name}: wildcard ownership is ambiguous: {raw}")
    reserved_names = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
    if any(part.endswith((".", " ")) or part.split(".", 1)[0].upper() in reserved_names for part in path.parts):
        raise FastAgentsError(f"role {role_name}: owned path is not portable across common filesystems: {raw}")
    candidate = (workspace / Path(*path.parts)).resolve(strict=False)
    try:
        candidate.relative_to(workspace)
    except ValueError as error:
        raise FastAgentsError(f"role {role_name}: owned path escapes the workspace: {raw}") from error
    return candidate.relative_to(workspace).as_posix()


def _check_ownership(roles: tuple[Role, ...]) -> None:
    owned_by: list[tuple[tuple[str, ...], str]] = []
    for role in roles:
        if role.access != "writer":
            continue
        for raw_path in role.owns:
            parts = tuple(unicodedata.normalize("NFC", part).casefold() for part in PurePosixPath(raw_path).parts)
            for other_parts, other_role in owned_by:
                shared_prefix = parts[: min(len(parts), len(other_parts))] == other_parts[: min(len(parts), len(other_parts))]
                if shared_prefix:
                    raise FastAgentsError(f"writer ownership overlaps: {other_role} and {role.name} share a file path")
            owned_by.append((parts, role.name))


def _check_dependency_graph(roles: tuple[Role, ...]) -> None:
    by_name = {role.name: role for role in roles}
    for role in roles:
        for dependency in role.depends_on:
            if dependency not in by_name:
                raise FastAgentsError(f"role {role.name}: unknown dependency {dependency}")
            if dependency == role.name:
                raise FastAgentsError(f"role {role.name}: cannot depend on itself")

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(name: str) -> None:
        if name in visiting:
            raise FastAgentsError(f"dependency cycle includes role {name}")
        if name in visited:
            return
        visiting.add(name)
        for dependency in by_name[name].depends_on:
            visit(dependency)
        visiting.remove(name)
        visited.add(name)

    for name in by_name:
        visit(name)


def _fast_selection(value: Any, label: str, role_names: set[str]) -> dict[str, Any]:
    if isinstance(value, bool):
        value = {"mode": "all" if value else "none"}
    if isinstance(value, str):
        value = {"mode": value}
    if not isinstance(value, dict):
        raise FastAgentsError(f"{label} must be all, none, or a selected worker list")
    mode = value.get("mode")
    if mode not in {"all", "none", "selected"}:
        raise FastAgentsError(f"{label}.mode must be all, none, or selected")
    workers = value.get("workers", [])
    if not isinstance(workers, list) or any(not isinstance(name, str) or not name for name in workers):
        raise FastAgentsError(f"{label}.workers must be an array of worker names")
    if mode == "selected" and not workers:
        raise FastAgentsError(f"{label}.workers must name at least one worker for selected fast mode")
    if mode != "selected" and workers:
        raise FastAgentsError(f"{label}.workers is allowed only when mode is selected")
    unknown = sorted(set(workers) - role_names)
    if unknown:
        raise FastAgentsError(f"{label} names unknown workers: {', '.join(unknown)}")
    return {"mode": mode, "workers": sorted(set(workers))}


def load_manifest(path: Path, defaults_path: Path | None = None) -> TaskManifest:
    source = path.expanduser().resolve()
    raw = read_json(source)
    if raw.get("schema_version") != SCHEMA_VERSION:
        raise FastAgentsError(f"manifest schema_version must be {SCHEMA_VERSION}")

    workspace_value = _required_text(raw.get("workspace"), "workspace")
    workspace = Path(workspace_value).expanduser()
    if not workspace.is_absolute():
        workspace = source.parent / workspace
    workspace = workspace.resolve()
    if not workspace.is_dir():
        raise FastAgentsError(f"workspace is not an existing directory: {workspace}")

    objective = _required_text(raw.get("objective"), "objective")
    shared_contract = _required_text(raw.get("shared_contract"), "shared_contract")
    if len(objective) > MAX_OBJECTIVE_CHARS:
        raise FastAgentsError(f"objective exceeds {MAX_OBJECTIVE_CHARS} characters")
    if len(shared_contract) > MAX_SHARED_CONTRACT_CHARS:
        raise FastAgentsError(f"shared_contract exceeds {MAX_SHARED_CONTRACT_CHARS} characters")
    plan_mode = raw.get("plan_mode", False)
    if not isinstance(plan_mode, bool):
        raise FastAgentsError("plan_mode must be true or false")

    raw_roles = raw.get("roles")
    if not isinstance(raw_roles, list) or not raw_roles:
        raise FastAgentsError("roles must be a non-empty array")
    if len(raw_roles) > 32:
        raise FastAgentsError("one task supports at most 32 Codex roles")

    complexity = raw.get("complexity", "bounded")
    if complexity not in {"bounded", "complex", "ambiguous"}:
        raise FastAgentsError("complexity must be bounded, complex, or ambiguous")
    backend_selection = raw.get("backend", "auto")
    if backend_selection not in {"auto", "app-server", "exec"}:
        raise FastAgentsError("backend must be auto, app-server, or exec")
    from fast_agents_limits import validate_budgets

    budgets = validate_budgets(raw.get("budgets", {}), "task budgets")
    isolation = raw.get("isolation", "shared")
    if not isinstance(isolation, str) or isolation not in {"shared", "worktree"}:
        raise FastAgentsError("isolation must be shared or worktree")
    acceptance_raw = raw.get("acceptance", [])
    if not isinstance(acceptance_raw, list) or len(acceptance_raw) > 20:
        raise FastAgentsError("acceptance must be an array of at most 20 argument arrays")
    acceptance = []
    for command in acceptance_raw:
        if (not isinstance(command, list) or not command or len(command) > 100
                or any(not isinstance(arg, str) or not arg or "\0" in arg or len(arg) > 4000 for arg in command)):
            raise FastAgentsError("each acceptance command must be a non-empty argument array without NULs")
        acceptance.append(tuple(command))
    if plan_mode and acceptance:
        raise FastAgentsError("Plan Mode forbids host acceptance commands")
    # Auto-routing is a documented choice made before preflight, never a
    # fallback after a failed launch. Explicit backend selections always win.
    trivial = (
        complexity == "bounded" and len(raw_roles) == 1
        and isinstance(raw_roles[0], dict)
        and not raw_roles[0].get("depends_on")
    )
    backend = ("exec" if trivial else "app-server") if backend_selection == "auto" else backend_selection
    needs_supervisor = bool(budgets or isolation == "worktree" or acceptance or any(
        isinstance(item, dict) and item.get("budgets") for item in raw_roles
    ))
    if needs_supervisor:
        if backend_selection == "exec":
            raise FastAgentsError("budgets, acceptance, and worktree isolation require the app-server backend")
        backend = "app-server"
    project_settings = raw.get("project_settings", {})
    if not isinstance(project_settings, dict):
        raise FastAgentsError("project_settings must be an object")
    defaults = load_defaults(defaults_path)
    max_concurrency = raw.get("max_concurrency")
    if max_concurrency is None:
        max_concurrency = min(3, len(raw_roles))
    if not isinstance(max_concurrency, int) or isinstance(max_concurrency, bool) or not 1 <= max_concurrency <= 8:
        raise FastAgentsError("max_concurrency must be an integer between 1 and 8")

    roles: list[Role] = []
    names: set[str] = set()
    portable_names: set[str] = set()
    for index, item in enumerate(raw_roles):
        label = f"roles[{index}]"
        if not isinstance(item, dict):
            raise FastAgentsError(f"{label} must be an object")
        name = _validate_role_name(item.get("name"), f"{label}.name")
        portable_name = unicodedata.normalize("NFKC", name).casefold()
        if portable_name in portable_names:
            raise FastAgentsError(f"role names must be unique across case-insensitive filesystems: {name}")
        names.add(name)
        portable_names.add(portable_name)

        access = item.get("access")
        if access not in {"writer", "reviewer"}:
            raise FastAgentsError(f"role {name}: access must be writer or reviewer")
        project_role = project_settings.get(access, {})
        saved_role = defaults.get(access, {})
        if not isinstance(project_role, dict) or not isinstance(saved_role, dict):
            raise FastAgentsError(f"role {name}: {access} settings must be objects")
        recommendation = recommended_profile(access, "ambiguous" if complexity in {"complex", "ambiguous"} else "bounded")
        model = resolve_setting(
            item.get("model"),
            saved_role.get("model"),
            resolve_setting(project_role.get("model"), None, recommendation["model"]),
        )
        effort = resolve_setting(
            item.get("effort"),
            saved_role.get("effort"),
            resolve_setting(project_role.get("effort"), None, recommendation["effort"]),
        )
        setting_sources = {
            "model": "explicit" if item.get("model") is not None else "saved" if saved_role.get("model") is not None else "project" if project_role.get("model") is not None else "recommended",
            "effort": "explicit" if item.get("effort") is not None else "saved" if saved_role.get("effort") is not None else "project" if project_role.get("effort") is not None else "recommended",
        }
        model = _required_text(model, f"role {name}.model")
        if len(model) > 200 or any(char.isspace() for char in model):
            raise FastAgentsError(f"role {name}: model must be a catalog identifier of at most 200 characters")
        if not isinstance(effort, str) or effort not in SUPPORTED_REASONING_EFFORTS:
            choices = ", ".join(sorted(SUPPORTED_REASONING_EFFORTS))
            raise FastAgentsError(f"role {name}: {access}s must use a supported reasoning effort ({choices})")

        owns_raw = _string_list(item.get("owns", []), f"role {name}.owns")
        if access == "writer" and not owns_raw:
            raise FastAgentsError(f"writer role {name} must declare owned files")
        if access == "reviewer" and owns_raw:
            raise FastAgentsError(f"reviewer role {name} cannot own files")
        owns = tuple(_owned_path(value, name, workspace) for value in owns_raw)
        constraints = _string_list(item.get("constraints", []), f"role {name}.constraints")
        if any(len(value) > 4_000 for value in constraints):
            raise FastAgentsError(f"role {name}: each constraint is limited to 4000 characters")
        depends_on = _string_list(item.get("depends_on", []), f"role {name}.depends_on")
        if len(set(depends_on)) != len(depends_on):
            raise FastAgentsError(f"role {name}: duplicate dependencies are not allowed")
        prompt = item.get("prompt", "")
        if not isinstance(prompt, str):
            raise FastAgentsError(f"role {name}.prompt must be text")
        if len(prompt) > MAX_ROLE_PROMPT_CHARS:
            raise FastAgentsError(f"role {name}.prompt exceeds {MAX_ROLE_PROMPT_CHARS} characters")
        roles.append(
            Role(
                name=name,
                access=access,
                model=model,
                effort=effort,
                outcome=_required_text(item.get("outcome"), f"role {name}.outcome"),
                owns=owns,
                constraints=constraints,
                depends_on=depends_on,
                prompt=prompt.strip(),
                fast_requested=False,
                setting_sources=setting_sources,
                budgets=validate_budgets(item.get("budgets", {}), f"role {name} budgets"),
            )
        )

    role_names = {role.name for role in roles}
    explicit_fast = raw.get("fast_selection", raw.get("fast_requested"))
    saved_fast = defaults.get("fast_selection")
    if saved_fast is None and defaults.get("fast_requested") is not None:
        saved_fast = defaults["fast_requested"]
    project_fast = project_settings.get("fast_selection")
    resolved_fast = resolve_setting(
        explicit_fast,
        saved_fast,
        resolve_setting(project_fast, None, {"mode": "all", "workers": []}),
    )
    selection = _fast_selection(resolved_fast, "fast selection", role_names)
    selected_names = role_names if selection["mode"] == "all" else set(selection["workers"])
    if selection["mode"] == "none":
        selected_names = set()
    resolved_roles: list[Role] = []
    for role, item in zip(roles, raw_roles):
        saved_role = defaults.get(role.access, {})
        project_role = project_settings.get(role.access, {})
        explicit_role_fast = item.get("fast_requested")
        if explicit_role_fast is not None and explicit_fast is not None:
            selected_by_manifest = role.name in selected_names
            if explicit_role_fast != selected_by_manifest:
                raise FastAgentsError(
                    f"role {role.name}: explicit fast_requested conflicts with the manifest fast_selection; resolve the conflict before launch"
                )
        saved_role_fast = saved_role.get("fast_requested")
        project_role_fast = project_role.get("fast_requested")
        if explicit_role_fast is not None:
            requested, fast_source = explicit_role_fast, "explicit"
        elif explicit_fast is not None:
            requested, fast_source = role.name in selected_names, "explicit"
        elif saved_role_fast is not None:
            requested, fast_source = saved_role_fast, "saved"
        elif saved_fast is not None:
            requested, fast_source = role.name in selected_names, "saved"
        elif project_role_fast is not None:
            requested, fast_source = project_role_fast, "project"
        elif project_fast is not None:
            requested, fast_source = role.name in selected_names, "project"
        else:
            requested, fast_source = role.name in selected_names, "recommended"
        if not isinstance(requested, bool):
            raise FastAgentsError(f"role {role.name}: fast_requested must be true or false")
        resolved_roles.append(
            Role(
                **{
                    **role.__dict__,
                    "fast_requested": requested,
                    "setting_sources": {**role.setting_sources, "fast": fast_source},
                }
            )
        )
    role_tuple = tuple(resolved_roles)
    _check_ownership(role_tuple)
    _check_dependency_graph(role_tuple)
    if isolation == "worktree":
        by_name = {role.name: role for role in role_tuple}
        writers = {role.name for role in role_tuple if role.access == "writer"}
        def ancestors(name: str) -> set[str]:
            result: set[str] = set()
            pending = list(by_name[name].depends_on)
            while pending:
                parent = pending.pop()
                if parent not in result:
                    result.add(parent)
                    pending.extend(by_name[parent].depends_on)
            return result
        for role in role_tuple:
            if role.access == "reviewer" and not writers.issubset(ancestors(role.name)):
                raise FastAgentsError("isolated reviewers must depend on all writers, directly or transitively")
    if plan_mode and any(role.access == "writer" for role in role_tuple):
        raise FastAgentsError("Plan Mode forbids writer roles; remove writers before launch")

    return TaskManifest(
        workspace=workspace,
        objective=objective,
        shared_contract=shared_contract,
        fast_requested=any(role.fast_requested for role in role_tuple),
        fast_selection=selection,
        backend=backend,
        max_concurrency=max_concurrency,
        plan_mode=plan_mode,
        roles=role_tuple,
        source_path=source,
        budgets=budgets,
        isolation=isolation,
        acceptance=tuple(acceptance),
    )


def recommended_profile(access: str, complexity: str = "bounded") -> dict[str, str]:
    if access == "writer":
        return {"model": WRITER_MODEL, "effort": "low" if complexity == "bounded" else "max"}
    if access == "reviewer":
        return {"model": REVIEWER_MODEL, "effort": "xhigh"}
    raise FastAgentsError(f"unsupported role type: {access}")


def resolve_setting(explicit: Any, saved: Any, recommendation: Any) -> Any:
    """Resolve a single setting using explicit > saved defaults > recommendation."""
    if explicit is not None:
        return explicit
    if saved is not None:
        return saved
    return recommendation


def default_config_path() -> Path:
    codex_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")).expanduser()
    return codex_home / "fast-agents" / "defaults.json"


def validate_defaults(data: Any) -> None:
    if not isinstance(data, dict):
        raise FastAgentsError("saved defaults must be an object")
    allowed_root = {"schema_version", "fast_requested", "fast_selection", "writer", "reviewer"}
    unknown_root = set(data) - allowed_root
    if unknown_root:
        raise FastAgentsError(f"saved defaults contain unknown fields: {', '.join(sorted(unknown_root))}")
    if type(data.get("schema_version")) is not int or data["schema_version"] != SCHEMA_VERSION:
        raise FastAgentsError("saved defaults schema_version is not supported")

    fast = data.get("fast_requested")
    if fast is not None and not isinstance(fast, bool):
        raise FastAgentsError("saved defaults fast_requested must be true or false")
    selection = data.get("fast_selection")
    if selection is not None:
        if isinstance(selection, str):
            selection = {"mode": selection}
        if not isinstance(selection, dict) or set(selection) - {"mode", "workers"}:
            raise FastAgentsError("saved defaults fast_selection must contain only mode and workers")
        mode = selection.get("mode")
        if mode not in {"all", "none", "selected"}:
            raise FastAgentsError("saved defaults fast_selection must be all, none, or selected")
        workers = selection.get("workers", [])
        if not isinstance(workers, list):
            raise FastAgentsError("saved defaults fast_selection workers must be an array of role names")
        normalized_workers: set[str] = set()
        for worker in workers:
            name = _validate_role_name(worker, "saved defaults fast_selection worker")
            normalized = unicodedata.normalize("NFKC", name).casefold()
            if normalized in normalized_workers:
                raise FastAgentsError("saved defaults fast_selection workers must not contain case-insensitive aliases")
            normalized_workers.add(normalized)
        if mode == "selected" and not workers:
            raise FastAgentsError("saved selected fast_selection needs worker names")
        if mode != "selected" and workers:
            raise FastAgentsError("saved fast_selection workers require selected mode")
        if fast is not None:
            raise FastAgentsError("saved defaults must use fast_requested or fast_selection, not both")

    for access, model, efforts in (
        ("writer", WRITER_MODEL, SUPPORTED_REASONING_EFFORTS),
        ("reviewer", REVIEWER_MODEL, SUPPORTED_REASONING_EFFORTS),
    ):
        role = data.get(access, {})
        if not isinstance(role, dict):
            raise FastAgentsError(f"saved defaults {access} must be an object")
        unknown = set(role) - {"model", "effort", "fast_requested"}
        if unknown:
            raise FastAgentsError(f"saved defaults {access} contain unknown fields: {', '.join(sorted(unknown))}")
        selected_model = _required_text(role.get("model", model), f"saved defaults {access} model")
        if len(selected_model) > 200 or any(char.isspace() for char in selected_model):
            raise FastAgentsError(f"saved defaults {access} model must be a catalog identifier")
        default_effort = "low" if access == "writer" else "xhigh"
        if not isinstance(role.get("effort", default_effort), str) or role.get("effort", default_effort) not in efforts:
            choices = " or ".join(sorted(efforts))
            raise FastAgentsError(f"saved defaults {access} effort must be {choices}")
        role_fast = role.get("fast_requested")
        if role_fast is not None and not isinstance(role_fast, bool):
            raise FastAgentsError("per-role fast_requested defaults must be true or false")


def load_defaults(path: Path | None = None) -> dict[str, Any]:
    target = path or default_config_path()
    if not target.exists():
        data = {"schema_version": SCHEMA_VERSION, "fast_requested": None, "writer": {}, "reviewer": {}}
        validate_defaults(data)
        return data
    data = read_json(target)
    validate_defaults(data)
    return data


def merge_role_settings(
    role: dict[str, Any],
    defaults: dict[str, Any],
    complexity: str = "bounded",
    project_settings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    access = str(role.get("access", ""))
    recommendation = recommended_profile(access, complexity)
    saved = defaults.get(access, {})
    project = (project_settings or {}).get(access, {})
    return {
        **role,
        "model": resolve_setting(role.get("model"), saved.get("model"), resolve_setting(project.get("model"), None, recommendation["model"])),
        "effort": resolve_setting(role.get("effort"), saved.get("effort"), resolve_setting(project.get("effort"), None, recommendation["effort"])),
    }
