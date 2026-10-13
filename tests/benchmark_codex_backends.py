"""Compare *fake* Codex exec vs app-server coordinator overhead (no model calls).

These numbers isolate local harness startup/process overhead, NOT model latency.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import statistics
import tempfile
import time

from test_fast_agents import (
    FakeAppServer, FakeCodex, FAKE_APP_SERVER_FIXTURE, FAKE_FIXTURE,
    launch_app_server, launch_cli, task_id_from, wait_for_manager,
    write_manifest, writer,
)


def percentile(values, ratio):
    ranked = sorted(values)
    index = (len(ranked) - 1) * ratio
    lo = int(index)
    hi = min(lo + 1, len(ranked) - 1)
    return round(ranked[lo] + (ranked[hi] - ranked[lo]) * (index - lo), 4)


def run_once(root: Path, backend: str):
    workspace = root / "workspace"
    workspace.mkdir(parents=True)
    manifest = write_manifest(
        root / "manifest.json", workspace,
        roles=[writer("bounded")], backend=backend,
        fast_requested=False, fast_selection={"mode": "none", "workers": []},
    )
    runtime_root = root / "runtime"
    if backend == "exec":
        executable = root / "fake-codex.py"
        shutil.copy2(FAKE_FIXTURE, executable)
        executable.chmod(0o755)
        fake = FakeCodex(executable, root / "captures", root / "codex-settings")
        start = time.perf_counter()
        launched = launch_cli(manifest, fake, runtime_root)
    else:
        executable = root / "fake-app-server.py"
        shutil.copy2(FAKE_APP_SERVER_FIXTURE, executable)
        fake = FakeAppServer(executable, root / "fake-app-state.json")
        start = time.perf_counter()
        launched = launch_app_server(manifest, fake, runtime_root)
    if launched.returncode:
        raise RuntimeError("Fake Codex launch failed: " + launched.stderr[-800:])
    status = wait_for_manager(runtime_root, task_id_from(launched), timeout=15)
    if status["state"] != "completed":
        raise RuntimeError("Fake Codex benchmark did not complete: " + str(status.get("state")))
    return round(time.perf_counter() - start, 4)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pairs", type=int, default=3)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not 1 <= args.pairs <= 12:
        parser.error("pairs must be between 1 and 12")
    records = []
    for pair in range(args.pairs):
        # Counterbalance warm-up/order effects across pairs.
        order = ["exec", "app-server"] if pair % 2 == 0 else ["app-server", "exec"]
        for position, backend in enumerate(order):
            with tempfile.TemporaryDirectory(prefix="codex-fake-overhead-") as tmp:
                elapsed = run_once(Path(tmp), backend)
            records.append({"pair": pair, "position": position, "backend": backend,
                            "elapsed_seconds": elapsed, "model_inference": False})
    summary = {
        "kind": "fake_codex_coordinator_overhead",
        "pairs": args.pairs,
        "not_a_live_model_benchmark": True,
        "exec": {}, "app_server": {}, "trials": records,
    }
    for backend, key in (("exec", "exec"), ("app-server", "app_server")):
        values = [row["elapsed_seconds"] for row in records if row["backend"] == backend]
        summary[key] = {"median_seconds": round(statistics.median(values), 4),
                        "p95_seconds": percentile(values, 0.95)}
    encoded = json.dumps(summary, indent=2) + "\n"
    print(encoded)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")


if __name__ == "__main__":
    main()
