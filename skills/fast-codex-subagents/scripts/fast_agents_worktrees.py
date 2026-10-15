"""Optional writer worktrees and explicit, acceptance-gated integration.

Worktrees isolate Git changes for attribution, not operating-system authority.
No reset, clean, deletion, or automatic application to the user's tree occurs.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import subprocess
import signal
import tempfile
from typing import Any

from fast_agents_common import FastAgentsError, atomic_write_bytes, atomic_write_json, read_json


def git(workspace: Path, *args: str, input: bytes | None = None) -> bytes:
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0")
    with tempfile.TemporaryDirectory(prefix="codex-empty-hooks-") as hooks:
        result = subprocess.run(
            ["git", "-c", "core.fsmonitor=false", "-c", f"core.hooksPath={hooks}", "-C", str(workspace), *args],
            input=input, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30, shell=False, env=env,
        )
    if result.returncode:
        raise FastAgentsError(f"Git {args[0]} failed; inspect the isolated tree before continuing: "
                              + result.stderr.decode("utf-8", errors="replace")[-1000:])
    return result.stdout


def head(workspace: Path) -> str:
    return git(workspace, "rev-parse", "HEAD").decode().strip()


def require_clean(workspace: Path) -> None:
    if git(workspace, "status", "--porcelain=v1", "--untracked-files=all"):
        raise FastAgentsError("worktree isolation/integration requires a clean source tree; preserve existing changes first")


class WorktreeSession:
    def __init__(self, task_dir: Path, workspace: Path):
        self.task_dir = task_dir
        self.workspace = workspace.resolve()
        self.root = task_dir / "worktrees"
        self.path = task_dir / "worktrees.json"

    def _tree(self, name: str) -> Path:
        path = self.root / name
        if path.is_symlink() or path.resolve(strict=False) != self.root.resolve() / name:
            raise FastAgentsError("isolated worktree path is not owned by this task")
        return path

    def prepare(self) -> None:
        if self.path.exists():
            state = read_json(self.path)
            if state.get("workspace") != str(self.workspace):
                raise FastAgentsError("isolated source workspace changed")
            if state.get("integration_state") != "ready":
                raise FastAgentsError("isolated creation is uncertain; inspect before continuing")
            self._check_tree(self._tree("integration"))
            return
        if Path(git(self.workspace, "rev-parse", "--show-toplevel").decode().strip()).resolve() != self.workspace:
            raise FastAgentsError("worktree isolation requires the Git repository root")
        require_clean(self.workspace)
        if b"160000 " in git(self.workspace, "ls-files", "--stage"):
            raise FastAgentsError("worktree isolation does not initialize submodules")
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        baseline = head(self.workspace)
        atomic_write_json(self.path, {"workspace": str(self.workspace), "baseline": baseline,
                                      "roles": {}, "integration_state": "creating"})
        git(self.workspace, "worktree", "add", "--detach", str(self._tree("integration")), baseline)
        state = read_json(self.path)
        state["integration_state"] = "ready"
        atomic_write_json(self.path, state)

    def _check_tree(self, path: Path) -> None:
        if not path.is_dir() or Path(git(path, "rev-parse", "--show-toplevel").decode().strip()).resolve() != path.resolve():
            raise FastAgentsError("isolated worktree is unavailable; do not recreate or replay it automatically")
        if git(path, "rev-parse", "--git-common-dir") != git(self._tree("integration"), "rev-parse", "--git-common-dir"):
            raise FastAgentsError("isolated worktree belongs to a different Git repository")

    def start_role(self, name: str, access: str) -> Path:
        if access == "reviewer":
            return self._tree("integration")
        state = read_json(self.path)
        path = self._tree("writer-" + name)
        if name in state["roles"]:
            self._check_tree(path)
            return path
        base = head(self._tree("integration"))
        # Save the baseline before creating anything; a failed creation is not retried.
        state["roles"][name] = {"baseline": base, "path": str(path), "state": "creating"}
        atomic_write_json(self.path, state)
        git(self.workspace, "worktree", "add", "--detach", str(path), base)
        state["roles"][name]["state"] = "ready"
        atomic_write_json(self.path, state)
        return path

    def collect(self, name: str, owns: list[str]) -> None:
        state = read_json(self.path)
        record = state["roles"][name]
        if record.get("state") == "integrated":
            return
        if record.get("state") != "ready":
            raise FastAgentsError("isolated integration is uncertain; inspect before any replay")
        path = self._tree("writer-" + name)
        self._check_tree(path)
        # The index belongs exclusively to this isolated worker. Include new files
        # and commits made by the worker, relative to its launch baseline.
        git(path, "add", "--all", "--", ".")
        changed = git(path, "diff", "--cached", "--name-only", "--no-renames", "-z", record["baseline"])
        paths = [value.decode("utf-8", errors="strict") for value in changed.split(b"\0") if value]
        if any(not any(value == own or value.startswith(own.rstrip("/") + "/") for own in owns) for value in paths):
            raise FastAgentsError(f"writer {name} changed paths outside ownership; isolated patch was not integrated")
        patch = git(path, "diff", "--cached", "--binary", "--no-ext-diff", "--no-textconv", record["baseline"], "--")
        atomic_write_bytes(self.task_dir / "agents" / name / "changes.patch", patch)
        integration = self._tree("integration")
        require_clean(integration)
        record["state"] = "applying"
        atomic_write_json(self.path, state)
        if patch:
            git(integration, "apply", "--check", "--index", "-", input=patch)
            git(integration, "apply", "--index", "-", input=patch)
            tree = git(integration, "write-tree").decode().strip()
            commit = git(integration, "-c", "user.name=Codex task", "-c", "user.email=codex-task@localhost",
                         "commit-tree", tree, "-p", head(integration), input=f"Collect {name}\n".encode()).decode().strip()
            git(integration, "update-ref", "HEAD", commit)
        record.update(state="integrated", patch_sha256=hashlib.sha256(patch).hexdigest())
        atomic_write_json(self.path, state)

    def final_patch(self) -> bytes:
        state = read_json(self.path)
        integration = self._tree("integration")
        require_clean(integration)
        return git(integration, "diff", "--binary", "--no-ext-diff", "--no-textconv", state["baseline"], "HEAD", "--")


def run_acceptance(task_dir: Path, workspace: Path, commands: list[list[str]], *, timeout: float = 120,
                   input_bytes: bytes | None = None) -> dict[str, Any]:
    if not commands:
        return {"state": "not_run", "commands": []}
    if os.name == "nt":
        return {"state": "failed", "commands": [], "error": "host acceptance requires POSIX process groups; no command started"}
    results = []
    for index, argv in enumerate(commands):
        log = task_dir / f"acceptance-{index}.private.log"
        process = None
        try:
            descriptor = os.open(log, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(descriptor, "wb") as stream:
                process = subprocess.Popen(argv, cwd=workspace, stdin=subprocess.PIPE if input_bytes is not None else subprocess.DEVNULL, stdout=stream,
                                           stderr=subprocess.STDOUT, shell=False, start_new_session=True)
                timed_out = False
                try:
                    process.communicate(input=input_bytes, timeout=timeout)
                except subprocess.TimeoutExpired:
                    timed_out = True
                # The dedicated session's group is ours, including descendants
                # left behind by a normally exiting parent. Never signal a PID
                # obtained from task status or a caller-supplied process number.
                background = False
                try:
                    os.killpg(process.pid, 0)
                    background = True
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait(timeout=10)
            results.append({"index": index, "exit_code": process.returncode,
                            "timed_out": timed_out, "background_processes": background})
            if process.returncode or timed_out or background:
                break
        except (OSError, subprocess.TimeoutExpired):
            results.append({"index": index, "exit_code": None, "error": "acceptance command/log failed or process cleanup is uncertain"})
            if process is not None:
                return {"state": "recovery_required", "commands": results,
                        "error": "owned acceptance process-group cleanup could not be confirmed; no automatic replay"}
            break
    passed = len(results) == len(commands) and all(row["exit_code"] == 0 and not row.get("timed_out")
                                                 and not row.get("background_processes") for row in results)
    return {"state": "passed" if passed else "failed", "commands": results}


def integration_preview(task_dir: Path, *, apply: bool = False, confirm: str | None = None) -> dict[str, Any]:
    manifest, status = read_json(task_dir / "manifest.json"), read_json(task_dir / "status.json")
    if manifest.get("isolation") != "worktree" or status.get("state") != "completed":
        raise FastAgentsError("only completed isolated tasks can be integrated")
    session = WorktreeSession(task_dir, Path(manifest["workspace"]))
    state = read_json(session.path)
    patch = session.final_patch()
    digest = hashlib.sha256(patch).hexdigest()
    verification = status.get("acceptance", {})
    if status.get("verification_state") != "passed" or verification.get("patch_sha256") != digest:
        raise FastAgentsError("integration requires passing acceptance for this exact patch")
    if head(session.workspace) != state["baseline"]:
        raise FastAgentsError("source HEAD changed since launch; preserve and reconcile the patch manually")
    require_clean(session.workspace)
    result = {"patch_sha256": digest, "patch_bytes": len(patch), "applied": False}
    if apply:
        if confirm != digest:
            raise FastAgentsError("integration confirmation must match the reviewed patch SHA-256")
        if patch:
            git(session.workspace, "apply", "--check", "--index", "-", input=patch)
            git(session.workspace, "apply", "--index", "-", input=patch)
        result["applied"] = True
    return result
