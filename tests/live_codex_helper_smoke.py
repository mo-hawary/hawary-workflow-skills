"""Authenticated actual-helper smoke in a disposable source tree.

Uses ChatGPT login only. Retains private runtime state for inspection; it never
deletes uncertain tasks or publishes their prompts, paths, reports, or credentials.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "fast-codex-subagents" / "scripts"
sys.path.insert(0, str(SCRIPTS))
from fast_agents_common import atomic_write_json, read_json
from fast_agents_worktrees import git
from live_codex_smoke import scrub


def source_fingerprint() -> str:
    digest = hashlib.sha256()
    for path in sorted(SCRIPTS.glob("*.py")):
        digest.update(path.name.encode() + b"\0" + path.read_bytes())
    digest.update((SCRIPTS.parent / "SKILL.md").read_bytes())
    return digest.hexdigest()


def run_smoke(codex: str, capture: Path) -> None:
    if os.environ.get("OPENAI_API_KEY") or os.environ.get("CODEX_API_KEY"):
        raise RuntimeError("ChatGPT-only smoke refuses API-key credentials")
    login = subprocess.run([codex, "login", "status"], capture_output=True, text=True, timeout=10)
    if login.returncode or "chatgpt" not in (login.stdout + login.stderr).lower():
        raise RuntimeError("This runner must be signed into ChatGPT with codex login")
    version = subprocess.run([codex, "--version"], capture_output=True, text=True, timeout=10, check=True).stdout.strip()
    root = Path(tempfile.mkdtemp(prefix="codex-helper-smoke-"))
    root.chmod(0o700)
    workspace = root / "source"
    workspace.mkdir()
    git(workspace, "init")
    (workspace / "README.md").write_text("Disposable helper smoke. Only edit result.txt.\n", encoding="utf-8")
    git(workspace, "add", ".")
    git(workspace, "-c", "user.name=Smoke", "-c", "user.email=smoke@localhost", "commit", "-m", "baseline")
    manifest = root / "manifest.json"
    atomic_write_json(manifest, {
        "schema_version": 1, "workspace": str(workspace), "backend": "app-server", "isolation": "worktree",
        "objective": "Write result.txt containing exactly CODEX_HELPER_SMOKE_OK followed by a newline, then review it.",
        "shared_contract": "Only result.txt may change. Do not use network, delegate, or alter Git administration.",
        "fast_selection": {"mode": "all"}, "max_concurrency": 2,
        "budgets": {"max_wall_seconds": 240, "action": "interrupt"},
        "acceptance": [[sys.executable, "-c", "from pathlib import Path; assert Path('result.txt').read_text() == 'CODEX_HELPER_SMOKE_OK\\n'"]],
        "roles": [
            {"name": "writer", "access": "writer", "model": "gpt-6-luna", "effort": "low",
             "outcome": "Write the exact requested file", "owns": ["result.txt"]},
            {"name": "reviewer", "access": "reviewer", "model": "gpt-6.1-sol", "effort": "low",
             "outcome": "Read result.txt and verify its exact content without edits", "depends_on": ["writer"]},
        ],
    })
    runtime = root / "runtime"
    helper = [sys.executable, str(SCRIPTS / "fast_agents.py")]
    task = None
    passed = False
    status = {}
    payload = {"schema_version": 1, "source": "real_codex_helper_smoke", "real_model_session": True,
               "codex_version": version, "git_sha": git(ROOT, "rev-parse", "HEAD").decode().strip(),
               "source_dirty": bool(git(ROOT, "status", "--porcelain")), "helper_sha256": source_fingerprint(),
               "upstream_execution_verified": False}
    try:
        launched = subprocess.run([*helper, "launch", "--manifest", str(manifest), "--runtime-root", str(runtime), "--codex", codex],
                                  capture_output=True, text=True, timeout=60, check=False)
        (root / "launch.private.log").write_text(launched.stdout + launched.stderr, encoding="utf-8")
        match = re.search(r"Started task ([a-f0-9-]{36});", launched.stdout)
        if launched.returncode or not match:
            raise RuntimeError("Actual helper launch failed; inspect private runner files")
        task_id = match.group(1)
        task = runtime / "tasks" / task_id
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            polled = subprocess.run([*helper, "control", "--task", task_id, "--runtime-root", str(runtime), "status"],
                                    capture_output=True, text=True, timeout=50, check=False)
            status = read_json(task / "status.json")
            if status.get("manager", {}).get("state") in {"completed", "failed"}:
                break
            if polled.returncode and status.get("state") == "recovery_required":
                raise RuntimeError("Actual helper requires reconciliation; no turn was replayed")
            time.sleep(.5)
        status = read_json(task / "status.json")
        passed = (status.get("state") == "completed" and status.get("verification_state") == "passed"
                  and all(role.get("state") == "completed" for role in status.get("roles", {}).values()))
        if not passed:
            # Request cancellation only for exact currently running turns. Retain
            # private state regardless; a failed control response is never retried.
            for name, role in status.get("roles", {}).items():
                if role.get("state") == "running" and role.get("turn_id"):
                    subprocess.run([*helper, "control", "--task", task_id, "--runtime-root", str(runtime),
                                    "interrupt", "--role", name, "--turn-id", role["turn_id"]],
                                   capture_output=True, timeout=50, check=False)
            raise RuntimeError("Actual helper/acceptance smoke did not complete; private state retained")
    finally:
        records = []
        if task is not None:
            for path in sorted((task / "agents").glob("*/events.jsonl")):
                for line in path.read_text(encoding="utf-8").splitlines():
                    row = json.loads(line)
                    records.append(scrub("event", row.get("method", "unknown"), row.get("params")))
        payload.update(passed=passed, verification_state=status.get("verification_state"),
                       roles=[{"name": name, "state": role.get("state"), "model": role.get("model"),
                               "effort": role.get("effort"), "effective_tier": role.get("fast", {}).get("effective_tier")}
                              for name, role in status.get("roles", {}).items()], records=records[:2000])
        capture.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(capture, payload)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--codex", default=shutil.which("codex"))
    parser.add_argument("--capture", type=Path, required=True)
    args = parser.parse_args()
    if args.codex is None:
        parser.error("Codex CLI is not installed")
    run_smoke(args.codex, args.capture)
