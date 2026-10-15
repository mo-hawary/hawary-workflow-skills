"""Opt-in matched live trials: direct, native agents, helper Fast/Standard.

Dry-run by default. Public summaries contain measurements and source hashes;
all raw model output and runtime state remain in a private trial directory.
"""
from __future__ import annotations

import argparse
import json
import hashlib
import os
import platform
from pathlib import Path
import re
import shutil
import statistics
import subprocess
import sys
import time

from live_codex_helper_smoke import ROOT, SCRIPTS, source_fingerprint
from fast_agents_common import atomic_write_json, read_json
from fast_agents_worktrees import git, run_acceptance

MODES = ("direct", "native", "helper-fast", "helper-standard")
TASK = ("Fix invoice_total to multiply unit price by quantity. Fix merge_items to merge duplicate SKUs, "
        "sort by SKU, reject conflicting prices with ValueError, and preserve input immutability. "
        "Edit only inventory.py. Run the existing tests and report verification and remaining risks.")


def plan(rounds: int) -> list[dict]:
    trials = []
    for pair in range(rounds):
        base = MODES if (pair // 4) % 2 == 0 else tuple(reversed(MODES))
        order = list(base[pair % 4:] + base[:pair % 4])
        trials.extend({"pair": pair, "order": index, "mode": mode,
                       "case_id": "inventory_seeded_defects"} for index, mode in enumerate(order))
    return trials


def trial(root: Path, row: dict, codex: str, model: str, effort: str) -> dict:
    started = time.perf_counter()
    workspace = root / "workspace"
    workspace.mkdir(parents=True)
    fixture = SCRIPTS.parent / "references" / "fixtures" / "inventory-baseline"
    (workspace / "inventory.py").write_bytes((fixture / "inventory.py.txt").read_bytes())
    (workspace / "test_inventory.py").write_bytes((fixture / "test_inventory.py.txt").read_bytes())
    (workspace / ".gitignore").write_text("__pycache__/\n.pytest_cache/\n", encoding="utf-8")
    # Keep an external immutable-in-the-workflow acceptance copy; do not trust
    # tests edited by the model inside its workspace as the acceptance oracle.
    oracle = root / "oracle"
    oracle.mkdir()
    (oracle / "test_inventory.py").write_bytes((fixture / "test_inventory.py.txt").read_bytes())
    git(workspace, "init")
    git(workspace, "add", ".")
    git(workspace, "-c", "user.name=Benchmark", "-c", "user.email=benchmark@localhost", "commit", "-m", "baseline")
    check = [sys.executable, "-c", "import runpy,sys; sys.path.insert(0, '.'); oracle=sys.argv.pop(); runpy.run_path(oracle, run_name='__main__')",
             str(oracle / "test_inventory.py")]
    result = dict(row, pass_=False, usage=None, cold_warm="uncontrolled", retries=0,
                  requested_model=model, requested_effort=effort, upstream_execution_verified=False)
    if row["mode"].startswith("helper"):
        manifest = root / "manifest.json"
        atomic_write_json(manifest, {"schema_version": 1, "workspace": str(workspace), "backend": "app-server",
            "objective": TASK, "shared_contract": "Edit only inventory.py; preserve public function names and input objects.",
            "fast_selection": {"mode": "all" if row["mode"] == "helper-fast" else "none"},
            "budgets": {"max_wall_seconds": 300, "action": "interrupt"},
            "roles": [{"name": "writer", "access": "writer", "model": model, "effort": effort,
                       "owns": ["inventory.py"], "outcome": TASK},
                      {"name": "reviewer", "access": "reviewer", "model": model, "effort": effort,
                       "depends_on": ["writer"], "outcome": "Review the implementation against the task contract, read-only"}]})
        runtime = root / "runtime"
        launch = subprocess.run([sys.executable, str(SCRIPTS / "fast_agents.py"), "launch", "--manifest", str(manifest),
                                 "--runtime-root", str(runtime), "--codex", codex], capture_output=True, timeout=60)
        (root / "launch.private.log").write_bytes(launch.stdout + launch.stderr)
        match = re.search(rb"Started task ([a-f0-9-]{36});", launch.stdout)
        if not match:
            result.update(failure_kind="launch_failed", uncertain=bool(list(runtime.glob("tasks/*"))))
        else:
            task = runtime / "tasks" / match.group(1).decode()
            deadline = time.monotonic() + 360
            while time.monotonic() < deadline:
                status = read_json(task / "status.json")
                if status.get("manager", {}).get("state") in {"completed", "failed"}:
                    break
                if status.get("state") == "recovery_required":
                    break
                time.sleep(.2)
            status = read_json(task / "status.json")
            terminal = status.get("manager", {}).get("state") in {"completed", "failed"}
            result.update(execution_state=status.get("state"), uncertain=not terminal,
                          usage={name: role.get("usage") for name, role in status.get("roles", {}).items()})
            result["pass_"] = terminal and status.get("state") == "completed"
    else:
        native = row["mode"] == "native"
        instruction = (f"Delegate implementation to one native {model} worker with {effort} reasoning, then use one native "
                       f"{model} read-only reviewer with {effort} reasoning. Collect both results. " if native
                       else "Complete this in the foreground without spawning subagents. ")
        argv = [codex, "exec", "-C", str(workspace), "-m", model, "-c", f'model_reasoning_effort="{effort}"',
                "-c", 'service_tier="default"', "-c", "features.fast_mode=false", "--approve-for-me", "--json", "--color", "never", "-"]
        execution = root / "execution"
        execution.mkdir()
        state = run_acceptance(execution, workspace, [argv], timeout=300, input_bytes=(instruction + TASK).encode())
        result.update(execution_state=state["state"], uncertain=state["state"] == "recovery_required",
                      pass_=state["state"] == "passed")
        # Native delegation compliance and effective settings require transcript
        # review; a prompt alone is not proof that a baseline used two agents.
        result["native_delegation_verified"] = False if native else None
    if result["pass_"]:
        acceptance = root / "acceptance"
        acceptance.mkdir()
        verification = run_acceptance(acceptance, workspace, [check])
        result.update(pass_=verification["state"] == "passed", acceptance_state=verification["state"],
                      uncertain=verification["state"] == "recovery_required")
    result["wall_seconds"] = round(time.perf_counter() - started, 4)
    result["pass"] = result.pop("pass_")
    return result


def summarize(rows: list[dict]) -> dict:
    result = {}
    for mode in MODES:
        group = [row for row in rows if row["mode"] == mode]
        successes = [row["wall_seconds"] for row in group if row.get("pass")]
        ordered = sorted(successes)
        result[mode] = {"trials": len(group), "passed": len(successes), "failed": len(group) - len(successes),
                        "median_seconds": statistics.median(successes) if successes else None,
                        "p95_seconds": ordered[min(len(ordered) - 1, int(.95 * len(ordered)))] if ordered else None}
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--rounds", type=int, default=10)
    parser.add_argument("--run-live", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--codex", default=shutil.which("codex"))
    parser.add_argument("--model", default="gpt-6-luna")
    parser.add_argument("--effort", default="low")
    args = parser.parse_args()
    if not 1 <= args.rounds <= 20:
        parser.error("rounds must be between 1 and 20")
    schedule = plan(args.rounds)
    if not args.run_live:
        print(json.dumps({"dry_run": True, "model_sessions_started": 0, "trials": schedule}, indent=2))
        return
    if not args.codex or not args.output or args.output.exists():
        parser.error("live runs require Codex and a new --output directory for private evidence")
    if os.environ.get("OPENAI_API_KEY") or os.environ.get("CODEX_API_KEY"):
        parser.error("this runner uses ChatGPT login; remove API-key environment variables")
    login = subprocess.run([args.codex, "login", "status"], capture_output=True, text=True, timeout=10)
    if login.returncode or "chatgpt" not in (login.stdout + login.stderr).lower():
        parser.error("sign into ChatGPT using codex login before running live trials")
    version = subprocess.run([args.codex, "--version"], capture_output=True, text=True, timeout=10, check=True).stdout.strip()
    fixture = SCRIPTS.parent / "references" / "fixtures" / "inventory-baseline"
    case_sha = hashlib.sha256(TASK.encode() + (fixture / "inventory.py.txt").read_bytes()
                              + (fixture / "test_inventory.py.txt").read_bytes()).hexdigest()
    args.output.mkdir(mode=0o700, parents=True)
    rows = []
    for index, row in enumerate(schedule):
        trial_root = args.output / f"trial-{index}"
        started = time.perf_counter()
        try:
            measured = trial(trial_root, row, args.codex, args.model, args.effort)
        except Exception as error:
            measured = dict(row, **{"pass": False, "failure_kind": type(error).__name__, "uncertain": True,
                                   "wall_seconds": round(time.perf_counter() - started, 4)})
        rows.append(measured)
        atomic_write_json(args.output / "summary.json", {"kind": "exploratory_single_fixture_live_trials",
            "codex_version": version, "python_version": platform.python_version(), "os": platform.system(),
            "case_sha256": case_sha,
            "helper_git_sha": git(ROOT, "rev-parse", "HEAD").decode().strip(), "helper_sha256": source_fingerprint(),
            "source_dirty": bool(git(ROOT, "status", "--porcelain")), "summary": summarize(rows), "trials": rows,
            "limitations": ["One synthetic case; not a general workload benchmark", "Cache/warm-up uncontrolled",
                            "Native delegation and effective settings require raw transcript review", "Provider execution unverified"]})
        if measured.get("uncertain"):
            break  # Retain evidence; do not launch more paid turns after uncertainty.
    print(json.dumps(summarize(rows), indent=2))


if __name__ == "__main__":
    main()
