"""Read-only, bounded Git scope auditing. This is not test verification."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
from typing import Any


MAX_SNAPSHOT_BYTES = 1024 * 1024
MAX_SNAPSHOT_PATHS = 5000
MAX_VISIBLE_PATHS = 100


def _unavailable(reason: str) -> dict[str, Any]:
    return {"state": "unavailable", "reason": reason}


def _run_git(workspace: Path, *args: str) -> bytes:
    command = ["git", "-c", "core.fsmonitor=false", "-C", str(workspace), *args]
    env = dict(os.environ, GIT_OPTIONAL_LOCKS="0")
    result = subprocess.run(
        command, check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        timeout=15, shell=False, env=env,
    )
    if result.returncode != 0:
        raise OSError("Git snapshot unavailable (not a repository or status failed)")
    if len(result.stdout) > MAX_SNAPSHOT_BYTES:
        raise OSError("Git snapshot exceeds the bounded output limit")
    return result.stdout


def _paths_from_porcelain(raw: bytes) -> list[str]:
    parts = raw.split(b"\0")
    if parts.pop() != b"":
        raise ValueError("incomplete Git status record")
    paths: set[str] = set()
    index = 0
    while index < len(parts):
        item = parts[index]
        if len(item) < 4 or item[2:3] != b" ":
            raise ValueError("malformed Git porcelain record")
        status = item[:2]
        paths.add(item[3:].decode("utf-8", errors="replace"))
        index += 1
        if b"R" in status or b"C" in status:
            if index >= len(parts):
                raise ValueError("incomplete Git rename/copy record")
            paths.add(parts[index].decode("utf-8", errors="replace"))
            index += 1
        if len(paths) > MAX_SNAPSHOT_PATHS:
            raise ValueError("too many paths to audit safely")
    return sorted(paths)


def snapshot_workspace(workspace: Path) -> dict[str, Any]:
    """Record status relative to the repository root, without modifying the workspace."""
    try:
        resolved = workspace.expanduser().resolve()
        git_root = Path(_run_git(resolved, "rev-parse", "--show-toplevel").decode("utf-8").strip()).resolve()
        # Declared ownership is workspace-relative; do not guess translations
        # when the selected directory is a subdirectory of a Git repository.
        if git_root != resolved:
            return _unavailable("workspace is not the Git repository root; scope audit is inconclusive")
        paths = _paths_from_porcelain(
            _run_git(resolved, "status", "--porcelain=v1", "-z", "--untracked-files=all")
        )
        return {"state": "captured", "paths": paths, "git_root": str(git_root)}
    except (OSError, subprocess.TimeoutExpired, UnicodeDecodeError, ValueError) as error:
        return _unavailable(str(error))


def audit_workspace_scope(
    workspace: Path, baseline: dict[str, Any] | None, roles: list[dict[str, Any]]
) -> dict[str, Any]:
    """Identify newly dirty paths outside declared writers; not proof of authorship."""
    owned = [
        path for role in roles if role.get("access") == "writer"
        for path in role.get("owns", []) if isinstance(path, str)
    ]
    if not owned:
        return {"state": "not_applicable", "note": "No writer roles were launched"}
    if not isinstance(baseline, dict) or baseline.get("state") != "captured":
        return {"state": "inconclusive", "reason": (baseline or {}).get("reason", "Git baseline unavailable")}
    after = snapshot_workspace(workspace)
    if after.get("state") != "captured" or after.get("git_root") != baseline.get("git_root"):
        return {"state": "inconclusive", "reason": after.get("reason", "Git repository changed during task")}
    before_paths = baseline.get("paths", [])
    if not isinstance(before_paths, list) or any(not isinstance(item, str) for item in before_paths):
        return {"state": "inconclusive", "reason": "Git baseline paths are invalid"}
    changed = sorted(set(after["paths"]) - set(before_paths))
    outside = [path for path in changed if not any(
        path == allowed or path.startswith(allowed.rstrip("/") + "/") for allowed in owned
    )]
    state = "new_outside_declared_paths" if outside else "within_declared_paths"
    return {
        "state": state,
        "new_changed_count": len(changed),
        "new_changed_paths": changed[:MAX_VISIBLE_PATHS],
        "outside_declared_count": len(outside),
        "outside_declared_paths": outside[:MAX_VISIBLE_PATHS],
        "preexisting_dirty_count": len(before_paths),
        "note": (
            "Read-only path comparison; changes on pre-existing dirty paths and "
            "concurrent external edits cannot be attributed to workers. "
            "It does not prove tests or acceptance checks passed."
        ),
    }
