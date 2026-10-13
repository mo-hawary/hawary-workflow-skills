from __future__ import annotations

import asyncio
import importlib
import json
import os
import socket
import shutil
import signal
import subprocess
import sys
import time
from types import SimpleNamespace
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "fast-codex-subagents"
SCRIPT = SKILL / "scripts" / "fast_agents.py"
FAKE_FIXTURE = Path(__file__).parent / "fixtures" / "fast_agents" / "fake_codex.py"
FAKE_APP_SERVER_FIXTURE = Path(__file__).parent / "fixtures" / "fast_agents" / "fake_app_server.py"
PROTOCOL_SCHEMA_FIXTURE = Path(__file__).parent / "fixtures" / "fast_agents" / "protocol_schema"
SCRIPT_DIR = str(SCRIPT.parent)
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

common = importlib.import_module("fast_agents_common")
runtime = importlib.import_module("fast_agents_runtime")
app_server = importlib.import_module("fast_agents_app_server")
setup = importlib.import_module("fast_agents_setup")
cli = importlib.import_module("fast_agents")

LEGACY_MANAGED_BLOCK = "\n".join(
    [
        "<!-- fast-agents:begin -->",
        "For Codex CLI or Codex app sessions that load this shared instruction file, use the `fast-agents` skill as the delegation master for substantive execution, including a single bounded worker when that is the smallest useful team. Keep brief factual answers, conversation, and planning in the foreground. Choose the team, model, effort, worker count, and per-worker Fast setting automatically; use Fast for all workers by default, while preserving explicit user routing and choices, saved and project Fast settings, ownership, sandbox, Plan Mode, and on-request approval rules. Avoid routine setup questions.",
        "This block is shared instruction text only; it applies only in clients configured to load the selected Codex home instruction file. The setup command cannot verify Codex app activation, so verify that inside the app.",
        "<!-- fast-agents:end -->",
    ]
)


@dataclass(frozen=True)
class FakeCodex:
    executable: Path
    capture_dir: Path
    control_dir: Path

    def env(self) -> dict[str, str]:
        env = os.environ.copy()
        env["FAST_AGENTS_CAPTURE_DIR"] = str(self.capture_dir)
        env["FAST_AGENTS_CONTROL_DIR"] = str(self.control_dir)
        codex_home = self.control_dir / "codex-home"
        codex_home.mkdir(parents=True, exist_ok=True)
        (codex_home / "config.toml").write_text("# fixture\n", encoding="utf-8")
        env["CODEX_HOME"] = str(codex_home)
        env["XDG_STATE_HOME"] = str(self.control_dir / "xdg-state")
        env["LOCALAPPDATA"] = str(self.control_dir / "local-app-data")
        return env

    def configure(self, role: str, **values: Any) -> None:
        self.control_dir.mkdir(parents=True, exist_ok=True)
        (self.control_dir / f"{role}.json").write_text(json.dumps(values), encoding="utf-8")


@dataclass(frozen=True)
class FakeAppServer:
    executable: Path
    state_path: Path

    def env(self, **overrides: str) -> dict[str, str]:
        env = os.environ.copy()
        codex_home = self.state_path.parent / "codex-home"
        codex_home.mkdir(parents=True, exist_ok=True)
        (codex_home / "config.toml").write_text("# fixture\n", encoding="utf-8")
        env.update(
            {
                "FAST_APP_SERVER_STATE": str(self.state_path),
                "FAST_APP_SERVER_SCHEMA_SOURCE": str(PROTOCOL_SCHEMA_FIXTURE),
                "CODEX_HOME": str(codex_home),
                "XDG_STATE_HOME": str(self.state_path.parent / "xdg-state"),
                "LOCALAPPDATA": str(self.state_path.parent / "local-app-data"),
            }
        )
        env.update(overrides)
        return env

    def state(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return {"threads": {}, "counts": {}, "requests": []}
        return json.loads(self.state_path.read_text(encoding="utf-8"))


@pytest.fixture
def fake_codex(tmp_path: Path) -> FakeCodex:
    executable = tmp_path / "fake Codex CLI.py"
    shutil.copy2(FAKE_FIXTURE, executable)
    executable.chmod(0o755)
    return FakeCodex(executable, tmp_path / "captures", tmp_path / "control")


@pytest.fixture
def fake_app_server(tmp_path: Path) -> FakeAppServer:
    executable = tmp_path / "fake-app-server.py"
    shutil.copy2(FAKE_APP_SERVER_FIXTURE, executable)
    return FakeAppServer(executable, tmp_path / "app-server-state.json")


def writer(name: str = "writer", owns: list[str] | None = None, **extra: Any) -> dict[str, Any]:
    role = {
        "name": name,
        "access": "writer",
        "model": "gpt-6-luna",
        "effort": "low",
        "outcome": f"Complete the {name} outcome.",
        "owns": owns or [f"src/{name}.py"],
        "constraints": ["Follow workspace instructions."],
        "depends_on": [],
        "prompt": "",
    }
    role.update(extra)
    return role


def reviewer(name: str = "reviewer", depends_on: list[str] | None = None, **extra: Any) -> dict[str, Any]:
    role = {
        "name": name,
        "access": "reviewer",
        "model": "gpt-6.1-sol",
        "effort": "xhigh",
        "outcome": "Review the implementation for correctness and contract risks.",
        "owns": [],
        "constraints": ["Read-only."],
        "depends_on": depends_on or [],
        "prompt": "",
    }
    role.update(extra)
    return role


def write_manifest(
    path: Path,
    workspace: Path,
    roles: list[dict[str, Any]] | None = None,
    *,
    fast_requested: bool = True,
    plan_mode: bool = False,
    shared_contract: str = "The shared API is parse_request(input) -> Result; failures keep the existing Error shape.",
    backend: str = "exec",
    fast_selection: dict[str, Any] | None = None,
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema_version": 1,
        "workspace": str(workspace),
        "objective": "Implement and review a bounded behavior change.",
        "shared_contract": shared_contract,
        "fast_requested": fast_requested,
        "backend": backend,
        "plan_mode": plan_mode,
        "roles": roles if roles is not None else [writer(), reviewer(depends_on=["writer"])],
    }
    if fast_selection is not None:
        manifest["fast_selection"] = fast_selection
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def run_cli(args: list[str], env: dict[str, str] | None = None, timeout: float = 20.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=timeout,
        shell=False,
        check=False,
    )


def launch_cli(
    manifest: Path,
    fake: FakeCodex,
    runtime_root: Path,
    *,
    plan_mode: bool = False,
) -> subprocess.CompletedProcess[str]:
    command = [
        "launch",
        "--manifest",
        str(manifest),
        "--runtime-root",
        str(runtime_root),
        "--codex",
        str(fake.executable),
    ]
    if plan_mode:
        command.append("--plan-mode")
    return run_cli(command, fake.env())


def task_id_from(result: subprocess.CompletedProcess[str]) -> str:
    marker = "Started task "
    assert marker in result.stdout, result.stdout + result.stderr
    return result.stdout.split(marker, 1)[1].split(";", 1)[0]


def wait_for_manager(runtime_root: Path, task_id: str, timeout: float = 10.0) -> dict[str, Any]:
    task_dir = runtime.task_path(runtime_root, task_id)
    deadline = time.monotonic() + timeout
    latest: dict[str, Any] = {}
    while time.monotonic() < deadline:
        latest = runtime.read_task_status(task_dir)
        if latest.get("manager", {}).get("state") in {"completed", "failed"}:
            return latest
        manager_identity = latest.get("manager", {}).get("identity")
        if latest.get("manager", {}).get("state") == "running" and manager_identity:
            state, _ = runtime.inspect_process(manager_identity)
            if state in {"dead", "stale_identity"}:
                return latest
        time.sleep(0.05)
    raise AssertionError(f"task supervisor did not finish: {latest}")


def launch_app_server(
    manifest: Path,
    fake: FakeAppServer,
    runtime_root: Path,
    *,
    env: dict[str, str] | None = None,
    timeout: float = 30.0,
) -> subprocess.CompletedProcess[str]:
    command = [
        "launch",
        "--manifest",
        str(manifest),
        "--runtime-root",
        str(runtime_root),
        "--codex",
        str(fake.executable),
    ]
    return run_cli(command, env or fake.env(), timeout=timeout)


def control_status(fake: FakeAppServer, task_id: str, runtime_root: Path) -> dict[str, Any]:
    result = run_cli(["control", "--task", task_id, "--runtime-root", str(runtime_root), "status"], fake.env())
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def wait_for_control_status(
    fake: FakeAppServer,
    task_id: str,
    runtime_root: Path,
    predicate: Any,
    timeout: float = 12.0,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    latest: dict[str, Any] = {}
    while time.monotonic() < deadline:
        try:
            latest = control_status(fake, task_id, runtime_root)
        except (AssertionError, json.JSONDecodeError):
            time.sleep(0.05)
            continue
        if predicate(latest):
            return latest
        time.sleep(0.05)
    raise AssertionError(f"timed out waiting for task status: {latest}")


def read_capture(fake: FakeCodex, task_id: str, role_name: str) -> dict[str, Any]:
    path = fake.capture_dir / f"{task_id}-{role_name}.json"
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and not path.exists():
        time.sleep(0.02)
    assert path.exists(), f"missing fake CLI capture for {role_name}"
    return json.loads(path.read_text(encoding="utf-8"))


def test_manifest_validation_enforces_ownership_dependencies_contract_and_routing(tmp_path: Path):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    manifest_path = write_manifest(
        tmp_path / "manifest.json",
        workspace,
        [writer("one", ["src"]), writer("two", ["src/parser.py"])],
    )
    with pytest.raises(common.FastAgentsError, match="ownership overlaps"):
        common.load_manifest(manifest_path)

    manifest_path = write_manifest(
        tmp_path / "manifest.json",
        workspace,
        [writer("one", ["src/Parser.py"]), writer("two", ["src/parser.py"])],
    )
    with pytest.raises(common.FastAgentsError, match="ownership overlaps"):
        common.load_manifest(manifest_path)

    manifest_path = write_manifest(tmp_path / "manifest.json", workspace, [writer("one", ["../outside.py"])])
    with pytest.raises(common.FastAgentsError, match="stay inside the workspace"):
        common.load_manifest(manifest_path)

    manifest_path = write_manifest(tmp_path / "manifest.json", workspace, [writer("one", ["src/*.py"])])
    with pytest.raises(common.FastAgentsError, match="wildcard ownership"):
        common.load_manifest(manifest_path)

    manifest_path = write_manifest(tmp_path / "manifest.json", workspace, [writer("one", ["src/a.py"], depends_on=["missing"])])
    with pytest.raises(common.FastAgentsError, match="unknown dependency"):
        common.load_manifest(manifest_path)

    manifest_path = write_manifest(tmp_path / "manifest.json", workspace, [writer("one", ["src/a.py"], model="gpt-6.1-sol")])
    with pytest.raises(common.FastAgentsError, match="writers must use gpt-6-luna"):
        common.load_manifest(manifest_path)

    manifest_path = write_manifest(tmp_path / "manifest.json", workspace, [writer("one", ["src/a.py"])], shared_contract="")
    with pytest.raises(common.FastAgentsError, match="shared_contract"):
        common.load_manifest(manifest_path)

    manifest_path = write_manifest(
        tmp_path / "manifest.json", workspace,
        [writer("only", ["src/only.py"], fast_requested=True)],
        fast_requested=False,
        fast_selection={"mode": "none", "workers": []},
    )
    with pytest.raises(common.FastAgentsError, match="explicit fast_requested conflicts"):
        common.load_manifest(manifest_path, defaults_path=tmp_path / "no-defaults.json")

    manifest_path = write_manifest(tmp_path / "manifest.json", workspace)
    raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    raw["shared_contract"] = "x" * (common.MAX_SHARED_CONTRACT_CHARS + 1)
    manifest_path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(common.FastAgentsError, match="shared_contract exceeds"):
        common.load_manifest(manifest_path, defaults_path=tmp_path / "no-defaults.json")


def test_reviewer_effort_respects_explicit_choice_and_supported_values(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    for effort in ("low", "xhigh"):
        manifest_path = write_manifest(
            tmp_path / f"reviewer-{effort}.json",
            workspace,
            [reviewer(effort=effort)],
            fast_requested=False,
        )
        manifest = common.load_manifest(manifest_path, defaults_path=tmp_path / "no-defaults.json")
        assert manifest.roles[0].model == "gpt-6.1-sol"
        assert manifest.roles[0].effort == effort

    wrong_model_manifest = write_manifest(
        tmp_path / "reviewer-wrong-model.json",
        workspace,
        [reviewer(model="gpt-6-luna", effort="low")],
        fast_requested=False,
    )
    with pytest.raises(common.FastAgentsError, match="read-only reviewers must use gpt-6.1-sol"):
        common.load_manifest(wrong_model_manifest, defaults_path=tmp_path / "no-defaults.json")

    for name, effort in (("unknown", "turbo"), ("list", ["low"]), ("object", {"value": "low"})):
        invalid_effort_manifest = write_manifest(
            tmp_path / f"reviewer-{name}-effort.json",
            workspace,
            [reviewer(effort=effort)],
            fast_requested=False,
        )
        with pytest.raises(common.FastAgentsError, match="reviewers must use a supported reasoning effort"):
            common.load_manifest(invalid_effort_manifest, defaults_path=tmp_path / "no-defaults.json")


def test_plan_mode_rejects_writers_before_any_cli_process(tmp_path: Path, fake_codex: FakeCodex):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    manifest = write_manifest(tmp_path / "manifest.json", workspace, [writer()], plan_mode=True)
    with pytest.raises(common.FastAgentsError, match="Plan Mode forbids writer"):
        common.load_manifest(manifest)

    manifest = write_manifest(tmp_path / "manifest.json", workspace, [writer()])
    result = launch_cli(manifest, fake_codex, tmp_path / "runtime", plan_mode=True)
    assert result.returncode == 2
    assert "Plan Mode forbids writer" in result.stderr
    assert not list(fake_codex.capture_dir.glob("*.json")) if fake_codex.capture_dir.exists() else True


def test_settings_precedence_and_smallest_recommendations():
    saved = {"writer": {"model": "gpt-6-luna", "effort": "max"}, "reviewer": {"model": "gpt-6.1-sol", "effort": "xhigh"}}
    explicit = common.merge_role_settings({"access": "writer", "effort": "low"}, saved, complexity="ambiguous")
    assert explicit["effort"] == "low"
    assert explicit["model"] == "gpt-6-luna"

    saved_writer = common.merge_role_settings({"access": "writer"}, saved, complexity="bounded")
    assert saved_writer["effort"] == "max"
    recommended = common.merge_role_settings({"access": "writer"}, {}, complexity="bounded")
    assert recommended == {"access": "writer", "model": "gpt-6-luna", "effort": "low"}
    reviewer_settings = common.merge_role_settings({"access": "reviewer"}, {}, complexity="ambiguous")
    assert reviewer_settings == {"access": "reviewer", "model": "gpt-6.1-sol", "effort": "xhigh"}
    assert common.resolve_setting(False, True, True) is False
    assert common.resolve_setting(None, False, True) is False
    assert common.resolve_setting(None, None, True) is True
    project_writer = common.merge_role_settings({"access": "writer"}, {}, "bounded", {"writer": {"effort": "max"}})
    assert project_writer["effort"] == "max"
    saved_wins = common.merge_role_settings({"access": "writer"}, saved, "bounded", {"writer": {"effort": "low"}})
    assert saved_wins["effort"] == "max"
    explicit_wins = common.merge_role_settings(
        {"access": "writer", "effort": "low"}, saved, "complex", {"writer": {"effort": "max"}}
    )
    assert explicit_wins["effort"] == "low"


def test_doctor_reports_client_capability_not_session_confirmation(fake_codex: FakeCodex, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("FAST_AGENTS_CAPTURE_DIR", str(fake_codex.capture_dir))
    monkeypatch.setenv("FAST_AGENTS_CONTROL_DIR", str(fake_codex.control_dir))
    report = json.loads(cli.doctor(str(fake_codex.executable), as_json=True, backend="exec"))
    assert report["version"] == "0.162.1"
    assert report["supported_flags"]["--approve-for-me"] is True
    assert report["supported_flags"]["-s read-only"] is True
    assert report["fast_mode_feature_line"] == "fast_mode stable true"
    assert report["fast_config"]["runtime_confirmation"].startswith("unverified")
    assert "does not confirm an individual session" in report["fast_mode_capability_note"]

    monkeypatch.setenv("FAST_AGENTS_FAKE_NO_FAST_FEATURE", "1")
    without_feature = json.loads(cli.doctor(str(fake_codex.executable), as_json=True, backend="exec"))
    assert without_feature["fast_mode_feature_line"] is None
    assert without_feature["fast_mode_feature_listed"] is False


@pytest.mark.skipif(os.name == "nt", reason="exec fallback fixture uses a POSIX executable shebang")
def test_exec_fallback_applies_fast_only_to_selected_workers(tmp_path: Path, fake_codex: FakeCodex):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    manifest = write_manifest(
        tmp_path / "manifest.json",
        workspace,
        [writer("one", ["src/one.py"]), writer("two", ["src/two.py"])],
        fast_requested=False,
        fast_selection={"mode": "selected", "workers": ["one"]},
    )
    runtime_root = tmp_path / "runtime"
    launched = launch_cli(manifest, fake_codex, runtime_root)
    assert launched.returncode == 0, launched.stderr
    task_id = task_id_from(launched)
    status = wait_for_manager(runtime_root, task_id)
    assert status["state"] == "completed"
    one_args = read_capture(fake_codex, task_id, "one")["args"]
    two_args = read_capture(fake_codex, task_id, "two")["args"]
    assert "--enable" in one_args and "service_tier=\"fast\"" in one_args
    assert "--enable" not in two_args and "service_tier=\"fast\"" not in two_args


@pytest.mark.skipif(os.name == "nt", reason="fake Codex fixture uses a POSIX executable shebang")
def test_launch_arguments_paths_private_state_dependencies_and_shell_safety(tmp_path: Path, fake_codex: FakeCodex):
    workspace = tmp_path / "workspace with spaces"
    workspace.mkdir()
    injected_marker = tmp_path / "should not execute"
    prompt_payload = f"Literal content: $(touch '{injected_marker}')"
    roles = [
        writer("implement", ["src/parser.py"], prompt=prompt_payload),
        reviewer("review", depends_on=["implement"]),
    ]
    manifest_path = write_manifest(tmp_path / "task.json", workspace, roles)
    runtime_root = tmp_path / "outside tracked source" / "runtime"
    inside_workspace = run_cli(
        [
            "launch",
            "--manifest",
            str(manifest_path),
            "--runtime-root",
            str(workspace / "runtime"),
            "--codex",
            str(fake_codex.executable),
        ],
        fake_codex.env(),
    )
    assert inside_workspace.returncode == 2
    assert "outside the selected workspace" in inside_workspace.stderr
    assert not (workspace / "runtime").exists()

    repo_root = tmp_path / "nested-repository"
    nested_workspace = repo_root / "packages" / "app"
    nested_workspace.mkdir(parents=True)
    (repo_root / ".git").mkdir()
    nested_manifest = write_manifest(tmp_path / "nested-task.json", nested_workspace, [writer()])
    source_runtime = run_cli(
        [
            "launch",
            "--manifest",
            str(nested_manifest),
            "--runtime-root",
            str(repo_root / "private-state"),
            "--codex",
            str(fake_codex.executable),
        ],
        fake_codex.env(),
    )
    assert source_runtime.returncode == 2
    assert "outside every Git repository" in source_runtime.stderr
    assert not (repo_root / "private-state").exists()

    worktree_root = tmp_path / "linked-worktree"
    worktree_root.mkdir()
    (worktree_root / ".git").write_text("gitdir: ../shared-git/worktrees/test\n", encoding="utf-8")
    outside_workspace = tmp_path / "separate-workspace"
    outside_workspace.mkdir()
    worktree_manifest = write_manifest(tmp_path / "worktree-task.json", outside_workspace, [writer()])
    worktree_runtime = worktree_root / "private" / "state"
    file_marker_runtime = run_cli(
        [
            "launch",
            "--manifest",
            str(worktree_manifest),
            "--runtime-root",
            str(worktree_runtime),
            "--codex",
            str(fake_codex.executable),
        ],
        fake_codex.env(),
    )
    assert file_marker_runtime.returncode == 2
    assert "outside every Git repository" in file_marker_runtime.stderr
    assert not worktree_runtime.exists()

    result = launch_cli(manifest_path, fake_codex, runtime_root)
    assert result.returncode == 0, result.stdout + result.stderr
    task_id = task_id_from(result)
    status = wait_for_manager(runtime_root, task_id)
    assert status["state"] == "completed"
    assert not injected_marker.exists()

    writer_capture = read_capture(fake_codex, task_id, "implement")
    writer_args = writer_capture["args"]
    assert writer_args[:9] == [
        "exec",
        "-C",
        str(workspace.resolve()),
        "-m",
        "gpt-6-luna",
        "-c",
        'model_reasoning_effort="low"',
        "-c",
        'service_tier="fast"',
    ]
    assert writer_args[9:12] == ["--enable", "fast_mode", "--approve-for-me"]
    assert "--fast" not in writer_args
    assert "--dangerously-bypass-approvals-and-sandbox" not in writer_args
    assert "--dangerously-bypass-hook-trust" not in writer_args
    assert writer_args[-1] == "-"
    assert str(runtime_root.resolve()) in writer_args[writer_args.index("-o") + 1]
    assert "Literal content:" in writer_capture["prompt"]
    assert "Shared interface contract" in writer_capture["prompt"]

    review_capture = read_capture(fake_codex, task_id, "review")
    review_args = review_capture["args"]
    assert "-s" in review_args and review_args[review_args.index("-s") + 1] == "read-only"
    assert "--approve-for-me" not in review_args
    assert "FAKE REPORT implement" in review_capture["prompt"]
    assert "Shared interface contract" in review_capture["prompt"]
    assert (runtime_root / "tasks" / task_id / "manifest.json").is_file()
    assert (runtime_root / "tasks" / task_id / "agents" / "implement" / "events.jsonl").is_file()
    assert (runtime_root / "tasks" / task_id / "agents" / "implement" / "stderr.log").is_file()

    status_output = run_cli(["status", "--task", task_id, "--runtime-root", str(runtime_root)])
    assert status_output.returncode == 0
    assert "completed" in status_output.stdout
    assert "FAKE REPORT" not in status_output.stdout


@pytest.mark.skipif(os.name == "nt", reason="fake Codex fixture uses a POSIX executable shebang")
def test_disjoint_writers_run_concurrently_and_workspace_lock_blocks_other_tasks(tmp_path: Path, fake_codex: FakeCodex):
    workspace = tmp_path / "shared workspace"
    workspace.mkdir()
    fake_codex.configure("left", delay=5.0)
    fake_codex.configure("right", delay=5.0)
    runtime_root = tmp_path / "runtime"
    roles = [writer("left", ["src/left.py"]), writer("right", ["src/right.py"])]
    first_manifest = write_manifest(tmp_path / "first.json", workspace, roles)
    first = launch_cli(first_manifest, fake_codex, runtime_root)
    assert first.returncode == 0, first.stderr
    first_id = task_id_from(first)

    second_manifest = write_manifest(tmp_path / "second.json", workspace, [writer("other", ["src/other.py"])])
    alternate_runtime_root = tmp_path / "different-runtime-root"
    second = launch_cli(second_manifest, fake_codex, alternate_runtime_root)
    assert second.returncode == 2
    assert "active writer task" in second.stderr
    assert not list(fake_codex.capture_dir.glob(f"*-other.json"))

    nested_workspace = workspace / "nested"
    nested_workspace.mkdir()
    child_manifest = write_manifest(tmp_path / "child.json", nested_workspace, [writer("child")])
    child = launch_cli(child_manifest, fake_codex, tmp_path / "third-runtime-root")
    assert child.returncode == 2
    assert "overlapping workspace" in child.stderr
    assert not list(fake_codex.capture_dir.glob(f"*-child.json"))

    status = wait_for_manager(runtime_root, first_id)
    assert status["state"] == "completed"
    timeline = [json.loads(line) for line in (fake_codex.capture_dir / "timeline.jsonl").read_text().splitlines()]
    times: list[tuple[float, int]] = []
    for event in timeline:
        times.append((event["time"], 1 if event["action"] == "start" else -1))
    active = maximum = 0
    for _, change in sorted(times):
        active += change
        maximum = max(maximum, active)
    assert maximum == 2


@pytest.mark.skipif(os.name == "nt", reason="exec fallback fixture uses a POSIX executable shebang")
def test_exec_respects_max_concurrency_before_each_launch_and_queues_dependencies_once(
    tmp_path: Path, fake_codex: FakeCodex
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    for name in ("first", "second", "dependent"):
        fake_codex.configure(name, delay=0.12)
    manifest = write_manifest(
        tmp_path / "manifest.json",
        workspace,
        [
            writer("first", ["src/first.py"]),
            writer("second", ["src/second.py"]),
            writer("dependent", ["src/dependent.py"], depends_on=["first"]),
        ],
    )
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["max_concurrency"] = 1
    manifest.write_text(json.dumps(data), encoding="utf-8")
    runtime_root = tmp_path / "runtime"
    launched = launch_cli(manifest, fake_codex, runtime_root)
    assert launched.returncode == 0, launched.stderr
    task_id = task_id_from(launched)
    status = wait_for_manager(runtime_root, task_id)
    assert status["state"] == "completed"
    assert all(status["roles"][name]["state"] == "completed" for name in ("first", "second", "dependent"))
    timeline = [json.loads(line) for line in (fake_codex.capture_dir / "timeline.jsonl").read_text().splitlines()]
    active = maximum = 0
    positions: dict[tuple[str, str], int] = {}
    for index, event in enumerate(timeline):
        positions[(event["role"], event["action"])] = index
        active += 1 if event["action"] == "start" else -1
        maximum = max(maximum, active)
        assert active >= 0
    assert active == 0 and maximum == 1
    assert positions[("first", "finish")] < positions[("second", "start")]
    assert positions[("second", "finish")] < positions[("dependent", "start")]
    assert sum(event["action"] == "start" for event in timeline) == 3


@pytest.mark.skipif(os.name == "nt", reason="exec fallback fixture uses a POSIX executable shebang")
@pytest.mark.parametrize("backend", ["exec", "app-server"])
def test_dependency_report_context_is_bounded_for_both_backends(
    tmp_path: Path, fake_codex: FakeCodex, fake_app_server: FakeAppServer, backend: str
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    roles = [writer("source", ["src/source.py"]), reviewer("review", depends_on=["source"])]
    manifest = write_manifest(
        tmp_path / "manifest.json",
        workspace,
        roles,
        backend=backend,
        fast_requested=False,
        fast_selection={"mode": "none", "workers": []},
    )
    runtime_root = tmp_path / "runtime"
    if backend == "exec":
        fake_codex.configure("source", report_chars=100_000)
        launched = launch_cli(manifest, fake_codex, runtime_root)
    else:
        launched = launch_app_server(
            manifest,
            fake_app_server,
            runtime_root,
            env=fake_app_server.env(FAST_APP_SERVER_REPORT_CHARS="100000"),
        )
    assert launched.returncode == 0, launched.stderr
    task_id = task_id_from(launched)
    status = wait_for_manager(runtime_root, task_id)
    assert status["state"] == "completed"
    if backend == "exec":
        prompt = read_capture(fake_codex, task_id, "review")["prompt"]
    else:
        prompt = (runtime.task_path(runtime_root, task_id) / "agents" / "review" / "prompt.txt").read_text(encoding="utf-8")
    marker = common.DEPENDENCY_CONTEXT_TRUNCATION_MARKER
    assert marker in prompt
    assert len(prompt) < common.MAX_DEPENDENCY_CONTEXT_CHARS + 5_000
    assert "R" * 50_000 not in prompt


@pytest.mark.skipif(os.name == "nt", reason="fake Codex fixture uses a POSIX executable shebang")
@pytest.mark.parametrize(
    ("config", "expected_state", "expected_error"),
    [
        ({"exit_code": 9}, "failed", "code 9"),
        ({"crash": True, "exit_code": 23}, "failed", "code 23"),
        ({"missing_report": True}, "unknown", "without a final report"),
    ],
)
def test_exit_crash_and_missing_report_are_not_misreported_as_success(
    tmp_path: Path,
    fake_codex: FakeCodex,
    config: dict[str, Any],
    expected_state: str,
    expected_error: str,
):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    fake_codex.configure("writer", **config)
    manifest = write_manifest(tmp_path / "task.json", workspace, [writer()])
    runtime_root = tmp_path / "runtime"
    result = launch_cli(manifest, fake_codex, runtime_root)
    assert result.returncode == 0, result.stderr
    task_id = task_id_from(result)
    status = wait_for_manager(runtime_root, task_id)
    assert status["roles"]["writer"]["state"] == expected_state
    assert expected_error in status["roles"]["writer"]["error"]
    if expected_state == "unknown":
        assert status["state"] == "unknown"


@pytest.mark.skipif(os.name == "nt", reason="fake Codex fixture uses a POSIX executable shebang")
def test_fragmented_malformed_partial_jsonl_and_stderr_pressure(tmp_path: Path, fake_codex: FakeCodex):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    fake_codex.configure("writer", malformed=True, partial=True, stderr_bytes=3 * 1024 * 1024)
    manifest = write_manifest(tmp_path / "task.json", workspace, [writer()])
    runtime_root = tmp_path / "runtime"
    result = launch_cli(manifest, fake_codex, runtime_root)
    assert result.returncode == 0, result.stderr
    task_id = task_id_from(result)
    status = wait_for_manager(runtime_root, task_id)
    task_dir = runtime.task_path(runtime_root, task_id)
    assert status["state"] == "completed"
    assert status["roles"]["writer"]["malformed_event_lines"] == 1
    assert status["roles"]["writer"]["last_activity"] == "turn started"
    assert (task_dir / "agents" / "writer" / "stderr.log").stat().st_size >= 3 * 1024 * 1024
    events = (task_dir / "agents" / "writer" / "events.jsonl").read_bytes()
    assert events.endswith(b'{"type":"turn.completed"')


@pytest.mark.skipif(os.name == "nt", reason="fake Codex fixture uses a POSIX executable shebang")
@pytest.mark.parametrize(
    ("config", "expected"),
    [
        ({"fast_result": {"service_tier": "fast", "fast_mode": True}}, "confirmed"),
        ({"fast_result": {"service_tier": "standard", "fast_mode": False}}, "rejected"),
        ({}, "unverified"),
    ],
)
def test_fast_request_and_correlated_runtime_result_are_separate(
    tmp_path: Path, fake_codex: FakeCodex, config: dict[str, Any], expected: str
):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    fake_codex.configure("writer", **config)
    manifest = write_manifest(tmp_path / "task.json", workspace, [writer()])
    runtime_root = tmp_path / "runtime"
    result = launch_cli(manifest, fake_codex, runtime_root)
    assert result.returncode == 0, result.stderr
    task_id = task_id_from(result)
    status = wait_for_manager(runtime_root, task_id)
    assert status["fast"]["choice"] == "confirmed"
    assert status["fast"]["requested"] is True
    assert status["fast"]["runtime"] == expected
    if expected == "unverified":
        # The fake CLI advertised fast_mode to doctor; this remains insufficient proof.
        assert status["fast"]["evidence"] is None
    else:
        assert status["fast"]["evidence"]["thread_id"] == f"session-{task_id}-writer"


@pytest.mark.skipif(os.name == "nt", reason="fake Codex fixture uses a POSIX executable shebang")
def test_rejected_fast_choice_is_distinct_from_runtime_unverified(tmp_path: Path, fake_codex: FakeCodex):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    manifest = write_manifest(tmp_path / "task.json", workspace, [writer()], fast_requested=False)
    result = launch_cli(manifest, fake_codex, tmp_path / "runtime")
    assert result.returncode == 0, result.stderr
    task_id = task_id_from(result)
    status = wait_for_manager(tmp_path / "runtime", task_id)
    assert status["fast"] == {"choice": "rejected", "requested": False, "runtime": "not_requested", "evidence": None}
    args = read_capture(fake_codex, task_id, "writer")["args"]
    assert "--enable" not in args
    assert 'service_tier="fast"' not in args


@pytest.mark.skipif(os.name == "nt", reason="fake Codex fixture uses a POSIX executable shebang")
def test_resume_does_not_duplicate_workers_and_watcher_interruption_only_stops_monitor(
    tmp_path: Path, fake_codex: FakeCodex
):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    fake_codex.configure("writer", delay=1.0)
    manifest = write_manifest(tmp_path / "task.json", workspace, [writer()])
    runtime_root = tmp_path / "runtime"
    launched = launch_cli(manifest, fake_codex, runtime_root)
    assert launched.returncode == 0, launched.stderr
    task_id = task_id_from(launched)
    task_dir = runtime.task_path(runtime_root, task_id)

    watcher = subprocess.Popen(
        [sys.executable, str(SCRIPT), "watch", "--task", task_id, "--runtime-root", str(runtime_root), "--continuous", "--interval", "0.05"],
        cwd=ROOT,
        env=fake_codex.env(),
        stdout=(tmp_path / "watch.log").open("wb"),
        stderr=subprocess.DEVNULL,
        shell=False,
    )
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and not list((task_dir / "monitors").glob("*.json")):
        time.sleep(0.02)
    monitor_records = list((task_dir / "monitors").glob("*.json"))
    assert monitor_records
    watcher.send_signal(signal.SIGINT)
    assert watcher.wait(timeout=5) == 130
    assert not monitor_records[0].exists()

    status = runtime.read_task_status(task_dir)
    worker_state, _ = runtime.inspect_process(status["roles"]["writer"]["identity"])
    assert worker_state == "alive"
    resumed = run_cli(["resume", "--task", task_id, "--runtime-root", str(runtime_root)], fake_codex.env())
    assert resumed.returncode == 0
    read_capture(fake_codex, task_id, "writer")
    assert len(list(fake_codex.capture_dir.glob(f"{task_id}-writer.json"))) == 1
    assert wait_for_manager(runtime_root, task_id)["state"] == "completed"


@pytest.mark.skipif(os.name == "nt", reason="fake Codex fixture uses a POSIX executable shebang")
def test_monitor_cleanup_signals_only_owned_watcher_not_writer_or_supervisor(tmp_path: Path, fake_codex: FakeCodex):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    fake_codex.configure("writer", delay=1.2)
    manifest = write_manifest(tmp_path / "task.json", workspace, [writer()])
    runtime_root = tmp_path / "runtime"
    launched = launch_cli(manifest, fake_codex, runtime_root)
    assert launched.returncode == 0, launched.stderr
    task_id = task_id_from(launched)
    task_dir = runtime.task_path(runtime_root, task_id)

    watcher_log = (tmp_path / "watcher.log").open("wb")
    watcher = subprocess.Popen(
        [sys.executable, str(SCRIPT), "watch", "--task", task_id, "--runtime-root", str(runtime_root), "--continuous", "--interval", "0.05"],
        cwd=ROOT,
        env=fake_codex.env(),
        stdout=watcher_log,
        stderr=subprocess.DEVNULL,
        shell=False,
    )
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and not list((task_dir / "monitors").glob("*.json")):
        time.sleep(0.02)
    assert list((task_dir / "monitors").glob("*.json"))

    preview = run_cli(["monitor", "cleanup", "--task", task_id, "--runtime-root", str(runtime_root)], fake_codex.env())
    assert preview.returncode == 0
    assert "--apply" in preview.stdout
    assert watcher.poll() is None

    applied = run_cli(["monitor", "cleanup", "--task", task_id, "--runtime-root", str(runtime_root), "--apply"], fake_codex.env())
    watcher_log.close()
    assert applied.returncode == 0
    assert "worker processes were not signaled" in applied.stdout
    assert watcher.wait(timeout=5) == 130
    status = runtime.read_task_status(task_dir)
    worker_state, _ = runtime.inspect_process(status["roles"]["writer"]["identity"])
    manager_state, _ = runtime.inspect_process(status["manager"]["identity"])
    assert worker_state == "alive"
    assert manager_state == "alive"
    assert wait_for_manager(runtime_root, task_id)["state"] == "completed"


@pytest.mark.skipif(os.name == "nt", reason="fake Codex fixture uses a POSIX executable shebang")
def test_supervisor_crash_resume_and_explicit_relaunch_wait_for_proven_writer_exit(tmp_path: Path, fake_codex: FakeCodex):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    fake_codex.configure("writer", delay=1.0)
    manifest = write_manifest(tmp_path / "task.json", workspace, [writer()])
    runtime_root = tmp_path / "runtime"
    launched = launch_cli(manifest, fake_codex, runtime_root)
    assert launched.returncode == 0, launched.stderr
    task_id = task_id_from(launched)
    task_dir = runtime.task_path(runtime_root, task_id)
    deadline = time.monotonic() + 5
    status = runtime.read_task_status(task_dir)
    while time.monotonic() < deadline and not status["roles"]["writer"].get("identity"):
        time.sleep(0.02)
        status = runtime.read_task_status(task_dir)
    assert status["roles"]["writer"].get("identity")
    os.kill(status["manager"]["pid"], signal.SIGKILL)

    second = launch_cli(manifest, fake_codex, runtime_root)
    assert second.returncode == 2
    assert "writer is still alive" in second.stderr
    assert len(list(fake_codex.capture_dir.glob("*-writer.json"))) == 1

    worker_state, _ = runtime.inspect_process(status["roles"]["writer"]["identity"])
    assert worker_state == "alive"
    time.sleep(1.1)
    stale = runtime.read_task_status(task_dir)
    assert stale["roles"]["writer"]["state"] == "unknown"
    assert "exit record" in stale["roles"]["writer"]["error"]

    third = launch_cli(manifest, fake_codex, runtime_root)
    assert third.returncode == 0, third.stderr
    third_id = task_id_from(third)
    assert wait_for_manager(runtime_root, third_id)["state"] == "completed"
    assert len(list(fake_codex.capture_dir.glob("*-writer.json"))) == 2


def test_permission_denied_and_unowned_process_records_are_never_killed_or_removed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    task_id = "12345678-1234-5678-1234-567812345678"
    task_dir = tmp_path / "runtime" / "tasks" / task_id
    monitors = task_dir / "monitors"
    monitors.mkdir(parents=True)
    (task_dir / "status.json").write_text(json.dumps({"task_id": task_id}), encoding="utf-8")
    record_path = monitors / "123.json"
    record_path.write_text(
        json.dumps(
            {
                "task_id": task_id,
                "kind": "watch",
                "pid": 123,
                "identity": {"pid": 123, "start_token": "1", "command_hash": "same"},
                "argv": ["fast_agents.py", "watch", "--task", task_id],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(runtime, "inspect_process", lambda _identity: ("permission_denied", None))
    monkeypatch.setattr(runtime.os, "kill", lambda *_args: pytest.fail("must not signal a permission-denied process"))
    messages = runtime.cleanup_monitors(task_dir, apply=True)
    assert "permission_denied" in messages[0]
    assert record_path.exists()

    record = json.loads(record_path.read_text(encoding="utf-8"))
    record["argv"] = ["fast_agents.py", "_supervise", "--task", task_id]
    record_path.write_text(json.dumps(record), encoding="utf-8")
    messages = runtime.cleanup_monitors(task_dir, apply=True)
    assert "ownership could not be proved" in messages[0]
    assert record_path.exists()


def test_stale_identity_lock_is_safe_but_unknown_or_permission_denied_refuses_launch(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    record_path = tmp_path / "workspace.json"
    record_path.write_text(
        json.dumps(
            {
                "manager_state": "running",
                "manager": {"pid": 1, "start_token": "old", "command_hash": "manager"},
                "processes": [
                    {
                        "state": "running",
                        "identity": {"pid": 2, "start_token": "old", "command_hash": "worker"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(runtime, "inspect_process", lambda _identity: ("stale_identity", {"pid": 1}))
    runtime._verify_old_lock_owner(record_path)

    monkeypatch.setattr(runtime, "inspect_process", lambda _identity: ("permission_denied", None))
    with pytest.raises(common.FastAgentsError, match="cannot establish previous supervisor identity"):
        runtime._verify_old_lock_owner(record_path)

    monkeypatch.setattr(runtime, "inspect_process", lambda _identity: ("unknown", None))
    with pytest.raises(common.FastAgentsError, match="cannot establish previous supervisor identity"):
        runtime._verify_old_lock_owner(record_path)

    record = json.loads(record_path.read_text(encoding="utf-8"))
    record["manager_state"] = "mystery"
    record_path.write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(common.FastAgentsError, match="manager state is unknown"):
        runtime._verify_old_lock_owner(record_path)


def test_workspace_lock_domain_ignores_runtime_roots_and_blocks_canonical_overlaps(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "shared-state"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "shared-local-state"))
    workspace = tmp_path / "repo"
    child_workspace = workspace / "packages" / "app"
    child_workspace.mkdir(parents=True)
    first_lock, first_record = runtime._workspace_lock_paths(tmp_path / "runtime-a", workspace)
    other_root_lock, other_root_record = runtime._workspace_lock_paths(tmp_path / "runtime-b", workspace)
    assert (first_lock, first_record) == (other_root_lock, other_root_record)
    child_lock, _ = runtime._workspace_lock_paths(tmp_path / "runtime-c", child_workspace)
    assert child_lock != first_lock

    alias = tmp_path / "repo-alias"
    try:
        alias.symlink_to(workspace, target_is_directory=True)
    except OSError:
        alias = workspace
    alias_lock, _ = runtime._workspace_lock_paths(tmp_path / "runtime-d", alias)
    assert alias_lock == first_lock

    identity = runtime.capture_process_identity(os.getpid())
    assert identity is not None
    record = {
        "task_id": "active-parent",
        "workspace": str(workspace.resolve()),
        "manager_state": "running",
        "manager": identity,
        "processes": [],
    }
    descriptor = runtime._acquire_workspace_lock(first_lock, workspace, record)
    try:
        with pytest.raises(common.FastAgentsError, match="overlapping workspace"):
            runtime._acquire_workspace_lock(
                child_lock,
                child_workspace,
                {"task_id": "child", "manager_state": "running", "manager": identity, "processes": []},
            )
    finally:
        os.close(descriptor)


@pytest.mark.parametrize(
    ("roles", "message"),
    [
        ([writer("Fix"), writer("fix")], "case-insensitive filesystems"),
        ([writer("CON")], "reserved device name"),
        ([writer("status")], "artifact name"),
    ],
)
def test_nonportable_role_ids_fail_before_private_runtime_creation(
    tmp_path: Path, fake_codex: FakeCodex, roles: list[dict[str, Any]], message: str
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    manifest = write_manifest(tmp_path / "manifest.json", workspace, roles)
    runtime_root = tmp_path / "private-runtime"
    launched = launch_cli(manifest, fake_codex, runtime_root)
    assert launched.returncode == 2
    assert message in launched.stderr
    assert not runtime_root.exists()


@pytest.mark.parametrize(
    ("defaults", "message"),
    [
        ({"schema_version": 1, "fast_requested": False, "backend": "exec", "writer": {}, "reviewer": {}}, "unknown fields"),
        ({"schema_version": 1, "writer": {"network": True}, "reviewer": {}}, "contain unknown fields"),
        ({"schema_version": 1, "writer": {"model": "other-model"}, "reviewer": {}}, "writer model must be"),
        ({"schema_version": 1, "writer": {"effort": "../high"}, "reviewer": {}}, "writer effort must be"),
        ({"schema_version": 1, "writer": {"fast_requested": "yes"}, "reviewer": {}}, "true or false"),
        ({"schema_version": 1, "fast_selection": {"mode": "selected", "workers": ["../escape"]}, "writer": {}, "reviewer": {}}, "letters, numbers"),
    ],
)
def test_one_defaults_whitelist_rejects_unknown_and_unsafe_values_everywhere(
    tmp_path: Path, defaults: dict[str, Any], message: str
):
    path = tmp_path / "defaults.json"
    path.write_text(json.dumps(defaults), encoding="utf-8")
    assert setup.validate_defaults is common.validate_defaults
    with pytest.raises(common.FastAgentsError, match=message):
        common.load_defaults(path)
    with pytest.raises(common.FastAgentsError, match=message):
        setup.defaults_preview(path, False, "low")
    with pytest.raises(common.FastAgentsError, match=message):
        setup.save_defaults(path, False, "low", apply=False)
    assert json.loads(path.read_text(encoding="utf-8")) == defaults


def test_defaults_preview_and_save_preserve_supported_per_role_policy(tmp_path: Path):
    path = tmp_path / "defaults.json"
    original = {
        "schema_version": 1,
        "fast_selection": {"mode": "selected", "workers": ["writer"]},
        "writer": {"model": "gpt-6-luna", "effort": "low", "fast_requested": True},
        "reviewer": {"model": "gpt-6.1-sol", "effort": "xhigh", "fast_requested": False},
    }
    path.write_text(json.dumps(original), encoding="utf-8")
    _, preview_data, before_sha, after_sha = setup.defaults_preview(path, None, "max")
    assert preview_data["fast_selection"] == original["fast_selection"]
    assert preview_data["writer"] == {**original["writer"], "effort": "max"}
    assert preview_data["reviewer"] == original["reviewer"]
    setup.save_defaults(path, None, "max", apply=True, confirm_sha=after_sha, expected_before=before_sha)
    saved = common.load_defaults(path)
    assert saved["fast_selection"] == original["fast_selection"]
    assert saved["writer"] == {**original["writer"], "effort": "max"}
    assert saved["reviewer"] == original["reviewer"]

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    routed_roles = [writer(), reviewer(depends_on=["writer"])]
    for role in routed_roles:
        role.pop("model")
        role.pop("effort")
    manifest = write_manifest(
        tmp_path / "task.json", workspace, routed_roles, backend="exec",
        fast_requested=False,
    )
    manifest_data = json.loads(manifest.read_text(encoding="utf-8"))
    manifest_data.pop("fast_requested")
    manifest.write_text(json.dumps(manifest_data), encoding="utf-8")
    routed = common.load_manifest(manifest, defaults_path=path)
    assert routed.backend == "exec"
    assert [(role.model, role.effort, role.fast_requested) for role in routed.roles] == [
        ("gpt-6-luna", "max", True),
        ("gpt-6.1-sol", "xhigh", False),
    ]


def test_managed_instruction_routes_substantive_execution_and_preserves_foreground_scope():
    managed = setup.MANAGED_BLOCK
    assert "delegation coordinator for substantive execution" in managed
    assert "including a single bounded worker" in managed
    assert "brief factual answers, conversation, and planning in the foreground" in managed
    assert "model, effort, worker count, and per-worker Fast setting automatically" in managed
    assert "use Fast for all workers by default" in managed
    assert "explicit user routing and choices" in managed
    assert "saved and project Fast settings" in managed
    assert "Plan Mode" in managed and "on-request approval" in managed
    assert "only in clients configured to load" in managed
    skill = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    assert "Do not use native agent spawning, Rust helpers, or nested delegation" in skill
    assert "Use the app-server backend by default" in skill
    assert "only when the user or project explicitly selects that fallback" in skill


def test_install_is_complete_idempotent_and_refuses_collisions_and_modified_rollback(tmp_path: Path):
    agents_home = tmp_path / ".agents" / "skills"
    claude_home = tmp_path / ".claude" / "skills"
    preview = setup.install_skill(agents_home, claude_home, apply=False)
    destination = agents_home / "fast-codex-subagents"
    claude_destination = claude_home / "fast-codex-subagents"
    assert "Preview only" in preview[-1]
    assert not destination.exists()
    assert not claude_destination.exists()

    applied = setup.install_skill(agents_home, claude_home, apply=True)
    for installed in (destination, claude_destination):
        assert installed.joinpath("SKILL.md").is_file()
        assert "name: fast-codex-subagents" in installed.joinpath("SKILL.md").read_text(encoding="utf-8")
        assert installed.joinpath("scripts", "fast_agents.py").is_file()
        assert installed.joinpath("scripts", "fast_agents_runtime.py").is_file()
        assert installed.joinpath("references", "manifest-guide.md").is_file()
    repeated = setup.install_skill(agents_home, claude_home, apply=True)
    assert "already installed unchanged" in repeated[0]
    assert "already installed unchanged" in repeated[1]
    assert len(applied) == 4

    (destination / "SKILL.md").write_text("user modification\n", encoding="utf-8")
    with pytest.raises(common.FastAgentsError, match="preserve modified files"):
        setup.rollback_skill(agents_home, claude_home, apply=True)
    assert (destination / "SKILL.md").read_text(encoding="utf-8") == "user modification\n"
    assert claude_destination.exists()

    collision_home = tmp_path / "collision"
    collision = collision_home / "fast-codex-subagents"
    collision.mkdir(parents=True)
    (collision / "keep.txt").write_text("unmanaged", encoding="utf-8")
    with pytest.raises(common.FastAgentsError, match="without an owned skill install marker"):
        setup.install_skill(collision_home, None, apply=True)
    assert (collision / "keep.txt").read_text(encoding="utf-8") == "unmanaged"

    clean_agents_home = tmp_path / "clean-agents" / "skills"
    clean_claude_home = tmp_path / "clean-claude" / "skills"
    setup.install_skill(clean_agents_home, clean_claude_home, apply=True)
    rollback_preview = setup.rollback_skill(clean_agents_home, clean_claude_home, apply=False)
    assert "Preview only" in rollback_preview[-1]
    setup.rollback_skill(clean_agents_home, clean_claude_home, apply=True)
    assert not (clean_agents_home / "fast-codex-subagents").exists()
    assert not (clean_claude_home / "fast-codex-subagents").exists()


def test_defaults_preview_confirmation_precedence_and_rollback_protect_modifications(tmp_path: Path):
    defaults_path = tmp_path / "codex home" / "fast-agents" / "defaults.json"
    before_data = {"schema_version": 1, "fast_requested": False, "writer": {"effort": "low"}, "reviewer": {}}
    defaults_path.parent.mkdir(parents=True)
    defaults_path.write_text(json.dumps(before_data), encoding="utf-8")
    preview_path, data, before_sha, after_sha = setup.defaults_preview(defaults_path, True, "max")
    assert preview_path == defaults_path.resolve()
    assert data["fast_requested"] is True and data["writer"]["effort"] == "max"
    preview = setup.save_defaults(defaults_path, True, "max", apply=False)
    assert "Preview only" in preview[-1]
    assert json.loads(defaults_path.read_text(encoding="utf-8"))["fast_requested"] is False

    with pytest.raises(common.FastAgentsError, match="confirmation does not match"):
        setup.save_defaults(defaults_path, True, "max", apply=True, confirm_sha="wrong", expected_before=before_sha)
    setup.save_defaults(defaults_path, True, "max", apply=True, confirm_sha=after_sha, expected_before=before_sha)
    saved = common.load_defaults(defaults_path)
    assert saved["fast_requested"] is True
    assert saved["writer"]["effort"] == "max"

    rollback_preview = setup.rollback_defaults(defaults_path, apply=False)
    assert "Preview only" in rollback_preview[-1]
    setup.rollback_defaults(defaults_path, apply=True)
    assert json.loads(defaults_path.read_text(encoding="utf-8")) == before_data

    absent = tmp_path / "new-defaults.json"
    _, _, absent_before, absent_after = setup.defaults_preview(absent, False, "low")
    setup.save_defaults(absent, False, "low", apply=True, confirm_sha=absent_after, expected_before=absent_before or "absent")
    absent.write_text("modified by user", encoding="utf-8")
    with pytest.raises(common.FastAgentsError, match="changed after setup"):
        setup.rollback_defaults(absent, apply=True)
    assert absent.read_text(encoding="utf-8") == "modified by user"


def test_defaults_generation_keeps_fast_unset_and_reports_all_as_recommendation(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    defaults_path = tmp_path / "new-defaults.json"
    _, preview, before_sha, after_sha = setup.defaults_preview(defaults_path, None, "low")
    assert preview["fast_requested"] is None
    setup.save_defaults(
        defaults_path,
        None,
        "low",
        apply=True,
        confirm_sha=after_sha,
        expected_before=before_sha or "absent",
    )

    assert common.load_defaults(defaults_path)["fast_requested"] is None
    assert cli._defaults_show(SimpleNamespace(path=str(defaults_path), json=False)) == 0
    assert "Fast workers: recommended all" in capsys.readouterr().out


def test_instruction_preview_prefers_override_backs_up_exact_file_and_rolls_back_only_unchanged(tmp_path: Path):
    codex_home = tmp_path / "codex home"
    codex_home.mkdir()
    global_instructions = codex_home / "AGENTS.md"
    override = codex_home / "AGENTS.override.md"
    global_before = b"Global routing stays first.\n"
    override_before = b"Override-specific instructions stay intact.\n"
    global_instructions.write_bytes(global_before)
    override.write_bytes(override_before)
    runtime_root = tmp_path / "runtime"

    plan_id, selected, before_sha, after_sha = setup.instruction_preview(codex_home, runtime_root)
    assert selected == override
    preview = setup.show_instruction_preview(codex_home, runtime_root)
    assert str(override) in preview[0]
    assert setup.sha256_bytes(override_before) == before_sha
    assert "Exact managed block to add or replace:" in preview
    setup.apply_instruction_preview(runtime_root, plan_id, after_sha, before_sha)
    assert global_instructions.read_bytes() == global_before
    assert override.read_bytes().startswith(override_before)
    assert setup.MANAGED_BLOCK.encode() in override.read_bytes()

    owner = json.loads((codex_home / setup.INSTRUCTION_OWNER).read_text(encoding="utf-8"))
    backup = Path(owner["backup"])
    assert backup.read_bytes() == override_before
    assert "already installed unchanged" in " ".join(setup.show_instruction_preview(codex_home, runtime_root))
    assert "Preview only" in setup.rollback_instruction(codex_home, apply=False)[-1]
    setup.rollback_instruction(codex_home, apply=True)
    assert override.read_bytes() == override_before
    assert backup.exists()

    second_home = tmp_path / "second codex home"
    second_home.mkdir()
    second_instructions = second_home / "AGENTS.md"
    second_instructions.write_text("Second instruction file.\n", encoding="utf-8")
    second_plan, selected, before_sha, after_sha = setup.instruction_preview(second_instructions, runtime_root)
    assert selected == second_instructions
    setup.apply_instruction_preview(runtime_root, second_plan, after_sha, before_sha)
    second_instructions.write_bytes(second_instructions.read_bytes() + b"User added another instruction.\n")
    with pytest.raises(common.FastAgentsError, match="changed after setup"):
        setup.rollback_instruction(second_instructions, apply=True)
    assert b"User added another instruction." in second_instructions.read_bytes()


def test_instruction_preview_detects_override_added_after_preview(tmp_path: Path):
    codex_home = tmp_path / "codex"
    codex_home.mkdir()
    agents = codex_home / "AGENTS.md"
    agents.write_text("Original instructions\n", encoding="utf-8")
    runtime_root = tmp_path / "runtime"
    plan_id, _, before_sha, after_sha = setup.instruction_preview(agents, runtime_root)
    (codex_home / "AGENTS.override.md").write_text("Now active\n", encoding="utf-8")
    with pytest.raises(common.FastAgentsError, match="became active after preview"):
        setup.apply_instruction_preview(runtime_root, plan_id, after_sha, before_sha)
    assert agents.read_text(encoding="utf-8") == "Original instructions\n"


def _install_legacy_managed_instructions(codex_home: Path, runtime_root: Path) -> tuple[Path, bytes, bytes]:
    codex_home.mkdir(parents=True)
    target = codex_home / "AGENTS.md"
    baseline = b"Personal instructions before the managed block.\nKeep this text unchanged.\n"
    target.write_bytes(baseline)
    plan_id, _, before_sha, after_sha = setup.instruction_preview(target, runtime_root)
    setup.apply_instruction_preview(runtime_root, plan_id, after_sha, before_sha)

    owner_path = codex_home / setup.INSTRUCTION_OWNER
    owner = json.loads(owner_path.read_text(encoding="utf-8"))
    current = target.read_bytes()
    current_block = (setup.MANAGED_BLOCK + "\n").encode("utf-8")
    legacy_block = (LEGACY_MANAGED_BLOCK + "\n").encode("utf-8")
    assert current.count(current_block) == 1
    legacy_content = current.replace(current_block, legacy_block, 1)
    target.write_bytes(legacy_content)
    owner["after_sha256"] = setup.sha256_bytes(legacy_content)
    owner["block_sha256"] = setup.sha256_bytes(legacy_block)
    setup.atomic_write_json(owner_path, owner)
    return target, baseline, legacy_content


def test_instruction_preview_refreshes_only_an_owned_legacy_block_and_keeps_rollback_baseline(tmp_path: Path):
    codex_home = tmp_path / "codex"
    runtime_root = tmp_path / "runtime"
    target, baseline, legacy_content = _install_legacy_managed_instructions(codex_home, runtime_root)
    owner_path = codex_home / setup.INSTRUCTION_OWNER
    owner_before = json.loads(owner_path.read_text(encoding="utf-8"))
    start, end, _ = setup._managed_instruction_span(legacy_content)

    plan_id, selected, before_sha, after_sha = setup.instruction_preview(target, runtime_root)
    plan = json.loads((runtime_root / "instruction-plans" / f"{plan_id}.json").read_text(encoding="utf-8"))
    assert selected == target
    assert plan["refresh_owner"] is True
    assert before_sha == setup.sha256_bytes(legacy_content)
    preview = setup.show_instruction_preview(target, runtime_root)
    assert "replaces only the unchanged block" in " ".join(preview)
    setup.apply_instruction_preview(runtime_root, plan_id, after_sha, before_sha)

    refreshed = target.read_bytes()
    new_start, new_end, new_block = setup._managed_instruction_span(refreshed)
    assert refreshed[:new_start] == legacy_content[:start]
    assert refreshed[new_end:] == legacy_content[end:]
    assert new_block == (setup.MANAGED_BLOCK + "\n").encode("utf-8")
    owner_after = json.loads(owner_path.read_text(encoding="utf-8"))
    for key in ("before_exists", "before_sha256", "backup"):
        assert owner_after[key] == owner_before[key]
    assert owner_after["after_sha256"] == setup.sha256_bytes(refreshed)
    assert owner_after["block_sha256"] == setup.sha256_bytes(new_block)

    setup.rollback_instruction(codex_home, apply=True)
    assert target.read_bytes() == baseline
    assert Path(owner_before["backup"]).is_file()


def test_instruction_rollback_accepts_an_unchanged_owned_legacy_block(tmp_path: Path):
    codex_home = tmp_path / "codex"
    runtime_root = tmp_path / "runtime"
    target, baseline, _ = _install_legacy_managed_instructions(codex_home, runtime_root)

    setup.rollback_instruction(codex_home, apply=True)
    assert target.read_bytes() == baseline
    assert not (codex_home / setup.INSTRUCTION_OWNER).exists()


@pytest.mark.parametrize("changed_artifact", ["instruction file", "ownership receipt"])
def test_instruction_refresh_rejects_changes_after_preview(tmp_path: Path, changed_artifact: str):
    codex_home = tmp_path / "codex"
    runtime_root = tmp_path / "runtime"
    target, _, _ = _install_legacy_managed_instructions(codex_home, runtime_root)
    owner_path = codex_home / setup.INSTRUCTION_OWNER
    owner_before = owner_path.read_bytes()
    plan_id, _, before_sha, after_sha = setup.instruction_preview(target, runtime_root)
    if changed_artifact == "instruction file":
        target.write_bytes(target.read_bytes() + b"User addition after preview.\n")
    else:
        owner_path.write_bytes(owner_before + b" ")
    changed_content = target.read_bytes()
    changed_owner = owner_path.read_bytes()

    expected_error = "changed after preview" if changed_artifact == "instruction file" else "ownership record changed after preview"
    with pytest.raises(common.FastAgentsError, match=expected_error):
        setup.apply_instruction_preview(runtime_root, plan_id, after_sha, before_sha)
    assert target.read_bytes() == changed_content
    assert owner_path.read_bytes() == changed_owner


def test_instruction_refresh_rejects_a_changed_rollback_backup(tmp_path: Path):
    codex_home = tmp_path / "codex"
    runtime_root = tmp_path / "runtime"
    target, _, legacy_content = _install_legacy_managed_instructions(codex_home, runtime_root)
    owner_path = codex_home / setup.INSTRUCTION_OWNER
    owner = json.loads(owner_path.read_text(encoding="utf-8"))
    backup = Path(owner["backup"])
    backup.write_bytes(b"changed backup")

    with pytest.raises(common.FastAgentsError, match="backup is missing or changed"):
        setup.instruction_preview(target, runtime_root)
    assert target.read_bytes() == legacy_content
    assert json.loads(owner_path.read_text(encoding="utf-8")) == owner


@pytest.mark.parametrize("error_type", [OSError, common.FastAgentsError])
def test_instruction_refresh_restores_pre_refresh_file_and_receipt_if_receipt_write_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error_type: type[Exception]
):
    codex_home = tmp_path / "codex"
    runtime_root = tmp_path / "runtime"
    target, _, legacy_content = _install_legacy_managed_instructions(codex_home, runtime_root)
    owner_path = codex_home / setup.INSTRUCTION_OWNER
    owner_content = owner_path.read_bytes()
    plan_id, _, before_sha, after_sha = setup.instruction_preview(target, runtime_root)

    def fail_receipt_write(*args: Any, **kwargs: Any) -> None:
        raise error_type("simulated receipt write failure")

    monkeypatch.setattr(setup, "atomic_write_json", fail_receipt_write)
    with pytest.raises(error_type, match="simulated receipt write failure"):
        setup.apply_instruction_preview(runtime_root, plan_id, after_sha, before_sha)

    assert target.read_bytes() == legacy_content
    assert owner_path.read_bytes() == owner_content


@pytest.mark.parametrize("trigger_kind", ["explicit", "natural"])
def test_explicit_and_natural_trigger_fixtures_exercise_the_same_observable_workflow(
    trigger_kind: str, tmp_path: Path, fake_app_server: FakeAppServer
):
    """Offline trigger fixtures exercise the actual default backend without model calls."""
    scenarios = json.loads((FAKE_FIXTURE.parent / "trigger_cases.json").read_text(encoding="utf-8"))
    scenario = next(item for item in scenarios if item["kind"] == trigger_kind)
    workspace = tmp_path / f"{trigger_kind} workspace"
    workspace.mkdir()
    roles = [writer("write", ["src/change.py"]), reviewer("review", depends_on=["write"])]
    manifest = write_manifest(
        tmp_path / "manifest.json",
        workspace,
        roles,
        backend="app-server",
        fast_requested=False,
        fast_selection=scenario["fast_selection"],
    )
    manifest_data = json.loads(manifest.read_text(encoding="utf-8"))
    manifest_data["objective"] = scenario["request"]
    manifest_data["complexity"] = scenario["complexity"]
    manifest.write_text(json.dumps(manifest_data), encoding="utf-8")
    runtime_root = tmp_path / "runtime"
    launched = launch_app_server(manifest, fake_app_server, runtime_root)
    assert launched.returncode == 0, launched.stderr
    task_id = task_id_from(launched)
    status = wait_for_manager(runtime_root, task_id)
    assert status["state"] == "completed"
    assert len(status["roles"]) == len(scenario["roles"])
    assert status["backend"] == "app-server"
    selected = set(status["roles"]) if scenario["fast_selection"]["mode"] == "all" else set(scenario["fast_selection"]["workers"])
    assert all(status["roles"][name]["fast"]["requested"] is (name in selected) for name in status["roles"])


def test_trigger_fixtures_cover_smallest_team_plan_mode_and_no_native_route(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    scenarios = json.loads((FAKE_FIXTURE.parent / "trigger_cases.json").read_text(encoding="utf-8"))
    skill = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    assert "Do not use native agent spawning" in skill
    assert "Never ask routine team/model/Fast setup questions" in skill
    for scenario in scenarios:
        assert scenario["native_agents"] is False
        assert scenario["routine_questions"] == []
        assert scenario["launch"] is (not scenario["plan_mode"])
        if scenario["kind"] == "minimal":
            assert scenario["roles"] == ["writer"]
            assert common.recommended_profile("writer", scenario["complexity"])["effort"] == "low"
        elif scenario["kind"] == "plan-mode":
            assert scenario["roles"] == []
        else:
            assert scenario["roles"] == ["writer", "reviewer"]
    workspace = tmp_path / "plan-workspace"
    workspace.mkdir()
    manifest = write_manifest(tmp_path / "plan.json", workspace, [writer()], plan_mode=True)
    with pytest.raises(common.FastAgentsError, match="Plan Mode forbids writer"):
        common.load_manifest(manifest)


def test_fake_app_server_doctor_schema_and_model_list_are_no_inference(fake_app_server: FakeAppServer, monkeypatch: pytest.MonkeyPatch):
    for key, value in fake_app_server.env().items():
        monkeypatch.setenv(key, value)
    report = json.loads(cli.doctor(str(fake_app_server.executable), as_json=True, backend="app-server"))
    assert report["available"] is True
    assert report["version"] == "0.162.1"
    assert report["protocol"]["verified"] is True
    assert {"thread/start", "turn/start", "turn/steer", "turn/interrupt"}.issubset(report["protocol"]["methods"])
    assert report["catalog_probe"] == "app-server model/list; no model inference"
    assert all(item["service_tiers"] == ["priority"] for item in report["model_catalog"])
    assert report["standard_tier_request_value"] == "default"
    assert "not a model/list service tier id" in report["standard_tier_catalog_note"]
    protocol = importlib.import_module("fast_agents_protocol")
    assert protocol.verify_schema_directory(PROTOCOL_SCHEMA_FIXTURE, "0.162.2").verified is False


def test_app_server_routes_concurrent_logical_threads_and_records_effective_tiers_and_usage(
    tmp_path: Path, fake_app_server: FakeAppServer
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    roles = [
        writer("one", ["src/one.py"]),
        writer("two", ["src/two.py"]),
        reviewer("review", depends_on=["one", "two"]),
    ]
    manifest = write_manifest(
        tmp_path / "task.json",
        workspace,
        roles,
        backend="app-server",
        fast_requested=False,
        fast_selection={"mode": "selected", "workers": ["one"]},
    )
    manifest_data = json.loads(manifest.read_text(encoding="utf-8"))
    manifest_data["max_concurrency"] = 2
    manifest.write_text(json.dumps(manifest_data), encoding="utf-8")
    runtime_root = tmp_path / "runtime"
    launched = launch_app_server(manifest, fake_app_server, runtime_root, env=fake_app_server.env(FAST_APP_SERVER_DELAY="0.35"))
    assert launched.returncode == 0, launched.stderr
    assert "selected Fast workers: one" in launched.stdout
    task_id = task_id_from(launched)
    status = wait_for_manager(runtime_root, task_id)
    assert status["state"] == "completed"
    assert all(status["roles"][name]["state"] == "completed" for name in ("one", "two", "review"))
    assert status["roles"]["one"]["fast"]["effective_tier"] == "priority"
    assert status["roles"]["two"]["fast"]["effective_tier"] == "default"
    assert status["roles"]["review"]["fast"]["effective_tier"] == "default"
    assert status["roles"]["two"]["fast"]["effective_tier_reported"] is None
    assert status["roles"]["two"]["fast"]["effective"] == "matched"
    assert status["roles"]["one"]["fast"]["upstream"] == "unverified"
    assert status["roles"]["one"]["usage"] == {"inputTokens": 120, "outputTokens": 25, "cachedInputTokens": 40}
    state = fake_app_server.state()
    assert state["counts"]["thread/start"] == 3
    assert state["counts"]["turn/start"] == 3
    starts = [item["params"] for item in state["requests"] if item.get("method") == "thread/start"]
    assert {item["model"] for item in starts} == {"gpt-6-luna", "gpt-6.1-sol"}
    assert {item["approvalsReviewer"] for item in starts} == {"auto_review"}
    assert {item["approvalPolicy"] for item in starts} == {"on-request"}
    assert {item["sandbox"] for item in starts} == {"workspace-write", "read-only"}
    turn_starts = [item["params"] for item in state["requests"] if item.get("method") == "turn/start"]
    assert {item["serviceTierForTurn"] for item in turn_starts} == {"priority", "default"}
    assert any(item.get("sandboxPolicy") == {"type": "readOnly", "networkAccess": False} for item in turn_starts)
    for role_name in ("one", "two", "review"):
        events = (runtime.task_path(runtime_root, task_id) / "agents" / role_name / "events.jsonl").read_text(encoding="utf-8")
        assert "turn/completed" in events


@pytest.mark.parametrize("terminal_state", ["completed", "failed", "interrupted"])
def test_terminal_notification_before_turn_start_response_stays_terminal_and_schedules_once(
    tmp_path: Path, fake_app_server: FakeAppServer, terminal_state: str
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    manifest = write_manifest(
        tmp_path / "manifest.json",
        workspace,
        [writer("first", ["src/first.py"]), reviewer("dependent", depends_on=["first"])],
        backend="app-server",
        fast_requested=False,
        fast_selection={"mode": "none", "workers": []},
    )
    runtime_root = tmp_path / "runtime"
    launched = launch_app_server(
        manifest,
        fake_app_server,
        runtime_root,
        env=fake_app_server.env(FAST_APP_SERVER_START_TERMINAL_BEFORE_RESPONSE=terminal_state),
    )
    assert launched.returncode == 0, launched.stderr
    task_id = task_id_from(launched)
    status = wait_for_manager(runtime_root, task_id)
    state = fake_app_server.state()
    assert status["roles"]["first"]["turn_status"] == terminal_state
    assert state["counts"]["thread/start"] == (2 if terminal_state == "completed" else 1)
    assert state["counts"]["turn/start"] == (2 if terminal_state == "completed" else 1)
    assert sum(item.get("method") == "thread/start" for item in state["requests"]) == state["counts"]["thread/start"]
    assert sum(item.get("method") == "turn/start" for item in state["requests"]) == state["counts"]["turn/start"]
    if terminal_state == "completed":
        assert status["state"] == "completed"
        assert status["roles"]["first"]["state"] == "completed"
        assert status["roles"]["dependent"]["state"] == "completed"
    else:
        assert status["state"] == "failed"
        assert status["roles"]["first"]["state"] == terminal_state
        assert status["roles"]["dependent"]["state"] == "blocked"


def test_fake_app_server_rejects_malformed_request_user_input_response_shape():
    spec = importlib.util.spec_from_file_location("fast_agents_fake_protocol_contract", FAKE_APP_SERVER_FIXTURE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    method = "item/tool/requestUserInput"
    params = {"questions": [{"id": "q1", "header": "Continue", "question": "Continue?"}]}
    malformed = module._response_mismatch(method, params, {"answers": {"q1": "yes"}})
    assert malformed == "user-input response must use per-question answers arrays"
    valid = module._response_mismatch(method, params, {"answers": {"q1": {"answers": ["yes"]}}})
    assert valid is None


def test_app_server_ipc_steer_ack_stale_turn_interrupt_and_read_only_token(
    tmp_path: Path, fake_app_server: FakeAppServer
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    manifest = write_manifest(
        tmp_path / "task.json", workspace, [writer()], backend="app-server", fast_requested=False,
        fast_selection={"mode": "none", "workers": []},
    )
    runtime_root = tmp_path / "runtime"
    launch_env = fake_app_server.env(FAST_APP_SERVER_DELAY="5")
    launched = launch_app_server(manifest, fake_app_server, runtime_root, env=launch_env)
    assert launched.returncode == 0, launched.stderr
    task_id = task_id_from(launched)
    status = wait_for_control_status(fake_app_server, task_id, runtime_root, lambda item: bool(item["roles"]["writer"].get("turn_id")))
    turn_id = status["roles"]["writer"]["turn_id"]
    resumed = run_cli(["resume", "--task", task_id, "--runtime-root", str(runtime_root), "--json"], fake_app_server.env())
    assert resumed.returncode == 0, resumed.stderr
    assert json.loads(resumed.stdout)["roles"]["writer"]["turn_id"] == turn_id
    assert fake_app_server.state()["counts"]["turn/start"] == 1
    task_dir = runtime.task_path(runtime_root, task_id)
    capabilities = json.loads((task_dir / "capabilities.json").read_text(encoding="utf-8"))
    assert all(token not in launched.stdout for token in capabilities.values() if isinstance(token, str))
    discovery = json.loads((task_dir / "discovery.json").read_text(encoding="utf-8"))
    with socket.create_connection(("127.0.0.1", discovery["port"]), timeout=3) as connection:
        forged = {"command": "steer", "token": capabilities["read"], "role": "writer", "turn_id": turn_id, "text": "not authorized"}
        connection.sendall(json.dumps(forged).encode() + b"\n")
        assert json.loads(connection.makefile("rb").readline())["ok"] is False

    steered = run_cli(
        ["control", "--task", task_id, "--runtime-root", str(runtime_root), "steer", "--role", "writer", "--turn-id", turn_id, "--text", "Continue with the bounded task."],
        fake_app_server.env(),
    )
    assert steered.returncode == 0, steered.stderr
    assert json.loads(steered.stdout)["acknowledged"] is True
    steer_request = next(item for item in fake_app_server.state()["requests"] if item.get("method") == "turn/steer")
    assert steer_request["params"]["expectedTurnId"] == turn_id
    stale = run_cli(
        ["control", "--task", task_id, "--runtime-root", str(runtime_root), "steer", "--role", "writer", "--turn-id", "stale-turn", "--text", "Do not send."],
        fake_app_server.env(),
    )
    assert stale.returncode == 2
    assert "no longer current" in stale.stderr
    interrupted = run_cli(
        ["control", "--task", task_id, "--runtime-root", str(runtime_root), "interrupt", "--role", "writer", "--turn-id", turn_id],
        fake_app_server.env(),
    )
    assert interrupted.returncode == 0, interrupted.stderr
    status = wait_for_manager(runtime_root, task_id)
    assert status["state"] == "failed"
    latest = runtime.read_task_status(task_dir)
    assert latest["roles"]["writer"]["state"] == "interrupted"


@pytest.mark.parametrize(
    ("request_mode", "decision", "payload"),
    [
        ("approval", "accept", None),
        ("file-change", "accept", None),
        ("user-input", None, {"answers": {"q1": {"answers": ["yes"]}}}),
        ("mcp", None, {"action": "decline"}),
        ("permissions", None, {"permissions": {"network": {"enabled": False}}, "scope": "turn"}),
        (
            "permissions",
            None,
            {"permissions": {"network": {"enabled": False}}, "scope": "session", "strictAutoReview": False},
        ),
    ],
)
def test_pending_approval_user_input_and_mcp_requests_need_scoped_explicit_replies(
    tmp_path: Path,
    fake_app_server: FakeAppServer,
    request_mode: str,
    decision: str | None,
    payload: dict[str, Any] | None,
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    manifest = write_manifest(
        tmp_path / "task.json", workspace, [writer()], backend="app-server", fast_requested=False,
        fast_selection={"mode": "none", "workers": []},
    )
    runtime_root = tmp_path / "runtime"
    launched = launch_app_server(
        manifest,
        fake_app_server,
        runtime_root,
        env=fake_app_server.env(FAST_APP_SERVER_REQUEST=request_mode, FAST_APP_SERVER_DELAY="0.05"),
    )
    assert launched.returncode == 0, launched.stderr
    task_id = task_id_from(launched)
    status = wait_for_control_status(fake_app_server, task_id, runtime_root, lambda item: bool(item.get("pending_requests")))
    pending = status["pending_requests"][0]
    assert set(pending) == {"request_id", "kind", "action", "thread_id", "turn_id"}
    assert pending["action"] == "explicit reply required"
    if request_mode == "approval":
        old_request = run_cli(
            ["control", "--task", task_id, "--runtime-root", str(runtime_root), "reply", "--request-id", "old-request", "--thread-id", pending["thread_id"], "--turn-id", pending["turn_id"], "--decision", "accept"],
            fake_app_server.env(),
        )
        assert old_request.returncode == 2
        assert "no longer pending" in old_request.stderr
        wrong_thread = run_cli(
            ["control", "--task", task_id, "--runtime-root", str(runtime_root), "reply", "--request-id", str(pending["request_id"]), "--thread-id", "wrong-thread", "--turn-id", pending["turn_id"], "--decision", "accept"],
            fake_app_server.env(),
        )
        assert wrong_thread.returncode == 2
        assert "does not match" in wrong_thread.stderr
        missing_decision = run_cli(
            ["control", "--task", task_id, "--runtime-root", str(runtime_root), "reply", "--request-id", str(pending["request_id"]), "--thread-id", pending["thread_id"], "--turn-id", pending["turn_id"]],
            fake_app_server.env(),
        )
        assert missing_decision.returncode == 2
        assert "approval decision" in missing_decision.stderr
    command = [
        "control", "--task", task_id, "--runtime-root", str(runtime_root), "reply",
        "--request-id", str(pending["request_id"]), "--thread-id", pending["thread_id"], "--turn-id", pending["turn_id"],
    ]
    if decision:
        command.extend(["--decision", decision])
    if payload is not None:
        command.extend(["--payload-json", json.dumps(payload)])
    replied = run_cli(command, fake_app_server.env())
    assert replied.returncode == 0, replied.stderr
    assert json.loads(replied.stdout)["acknowledged"] is True
    finished = wait_for_manager(runtime_root, task_id)
    assert finished["state"] == "completed"
    assert finished["guardian_reviews"][0]["status"] == "completed"
    state = fake_app_server.state()
    client_responses = [item["response"]["result"] for item in state["requests"] if "response" in item]
    if decision:
        assert {"decision": decision} in client_responses
    if payload is not None and request_mode == "user-input":
        assert payload in client_responses
    if payload is not None and request_mode == "mcp":
        assert {"action": "decline", "content": None} in client_responses
    if payload is not None and request_mode == "permissions":
        assert payload in client_responses
    assert state.get("response_mismatches", []) == []


@pytest.mark.parametrize(
    ("role_kind", "payload", "message"),
    [
        ("writer", {"permissions": {"network": {"enabled": True}}}, "widens the requested access"),
        (
            "writer",
            {"permissions": {"fileSystem": {"write": ["__OUTSIDE_OWNERSHIP__"]}}},
            "outside this writer's declared ownership",
        ),
        (
            "reviewer",
            {"permissions": {"fileSystem": {"write": ["__REQUESTED_PATH__"]}}},
            "read-only roles cannot grant filesystem writes",
        ),
    ],
)
def test_permission_replies_cannot_widen_requested_values_or_role_policy(
    tmp_path: Path,
    fake_app_server: FakeAppServer,
    role_kind: str,
    payload: dict[str, Any],
    message: str,
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    role = writer() if role_kind == "writer" else reviewer()
    manifest = write_manifest(
        tmp_path / "manifest.json",
        workspace,
        [role],
        backend="app-server",
        fast_requested=False,
        fast_selection={"mode": "none", "workers": []},
    )
    runtime_root = tmp_path / "runtime"
    launched = launch_app_server(
        manifest,
        fake_app_server,
        runtime_root,
        env=fake_app_server.env(FAST_APP_SERVER_REQUEST="permissions", FAST_APP_SERVER_DELAY="0.05"),
    )
    assert launched.returncode == 0, launched.stderr
    task_id = task_id_from(launched)
    status = wait_for_control_status(fake_app_server, task_id, runtime_root, lambda item: bool(item.get("pending_requests")))
    pending = status["pending_requests"][0]
    if payload["permissions"].get("fileSystem"):
        paths = payload["permissions"]["fileSystem"]["write"]
        if paths == ["__OUTSIDE_OWNERSHIP__"]:
            paths[:] = [str(workspace / "src" / "other.py")]
        else:
            paths[:] = [str(workspace / "src" / "writer.py")]
    command = [
        "control", "--task", task_id, "--runtime-root", str(runtime_root), "reply",
        "--request-id", str(pending["request_id"]), "--thread-id", pending["thread_id"],
        "--turn-id", pending["turn_id"], "--payload-json", json.dumps(payload),
    ]
    rejected = run_cli(command, fake_app_server.env())
    assert rejected.returncode == 2
    assert message in rejected.stderr
    still_pending = control_status(fake_app_server, task_id, runtime_root)
    assert still_pending["pending_requests"] == [pending]
    assert fake_app_server.state().get("server_responses", []) == []

    interrupted = run_cli(
        ["control", "--task", task_id, "--runtime-root", str(runtime_root), "interrupt", "--role", role["name"], "--turn-id", pending["turn_id"]],
        fake_app_server.env(),
    )
    assert interrupted.returncode == 0, interrupted.stderr
    finished = wait_for_manager(runtime_root, task_id)
    assert finished["pending_requests"] == []
    assert fake_app_server.state().get("pending_request") is None


def test_pending_details_are_private_until_explicit_authenticated_inspection(
    tmp_path: Path, fake_app_server: FakeAppServer
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    manifest = write_manifest(
        tmp_path / "manifest.json", workspace, [writer()], backend="app-server", fast_requested=False,
        fast_selection={"mode": "none", "workers": []},
    )
    runtime_root = tmp_path / "runtime"
    marker = "SYNTHETIC_PRIVATE_REQUEST_MARKER"
    launched = launch_app_server(
        manifest,
        fake_app_server,
        runtime_root,
        env=fake_app_server.env(
            FAST_APP_SERVER_REQUEST="approval",
            FAST_APP_SERVER_REQUEST_MARKER=marker,
            FAST_APP_SERVER_DELAY="0.05",
        ),
    )
    assert launched.returncode == 0, launched.stderr
    task_id = task_id_from(launched)
    status = wait_for_control_status(fake_app_server, task_id, runtime_root, lambda item: bool(item.get("pending_requests")))
    pending = status["pending_requests"][0]
    task_dir = runtime.task_path(runtime_root, task_id)
    public_artifacts = [
        json.dumps(status),
        (task_dir / "status.json").read_text(encoding="utf-8"),
        run_cli(["status", "--task", task_id, "--runtime-root", str(runtime_root)], fake_app_server.env()).stdout,
        run_cli(["watch", "--task", task_id, "--runtime-root", str(runtime_root)], fake_app_server.env()).stdout,
        run_cli(["control", "--task", task_id, "--runtime-root", str(runtime_root), "events"], fake_app_server.env()).stdout,
    ]
    assert all(marker not in output for output in public_artifacts)
    assert set(pending) == {"request_id", "kind", "action", "thread_id", "turn_id"}
    private_artifact = task_dir / "pending-requests.private.json"
    assert marker in private_artifact.read_text(encoding="utf-8")
    if os.name != "nt":
        assert private_artifact.stat().st_mode & 0o777 == 0o600

    inspected = run_cli(
        ["control", "--task", task_id, "--runtime-root", str(runtime_root), "inspect-request", "--request-id", str(pending["request_id"])],
        fake_app_server.env(),
    )
    assert inspected.returncode == 0, inspected.stderr
    inspection = json.loads(inspected.stdout)
    assert inspection["sensitive"] is True and marker in json.dumps(inspection["request"])
    assert "raw command" in inspection["warning"]

    discovery = json.loads((task_dir / "discovery.json").read_text(encoding="utf-8"))
    capabilities = json.loads((task_dir / "capabilities.json").read_text(encoding="utf-8"))
    with socket.create_connection(("127.0.0.1", discovery["port"]), timeout=3) as connection:
        request = {"command": "inspect-request", "token": capabilities["read"], "request_id": pending["request_id"]}
        connection.sendall(json.dumps(request).encode("utf-8") + b"\n")
        assert json.loads(connection.makefile("rb").readline())["error"] == "not authorized"

    interrupted = run_cli(
        ["control", "--task", task_id, "--runtime-root", str(runtime_root), "interrupt", "--role", "writer", "--turn-id", pending["turn_id"]],
        fake_app_server.env(),
    )
    assert interrupted.returncode == 0, interrupted.stderr
    assert wait_for_manager(runtime_root, task_id)["pending_requests"] == []


def test_pending_reply_requires_live_epoch_current_turn_and_exact_server_request(tmp_path: Path):
    role = app_server.RolePlan(
        name="writer", access="writer", model="gpt-6-luna", effort="low", outcome="work",
        owns=("src/writer.py",), constraints=(), depends_on=(), prompt="", fast_requested=False,
    )
    request_message = {"id": "request-1", "method": "item/commandExecution/requestApproval", "params": {"threadId": "thread-1", "turnId": "turn-1", "command": "echo"}}
    key = "str:request-1"
    client = SimpleNamespace(closed=asyncio.Event(), server_requests={key: request_message})
    supervisor = app_server.AppServerSupervisor.__new__(app_server.AppServerSupervisor)
    supervisor.roles = {"writer": role}
    supervisor.client = client
    supervisor.connection_epoch = "epoch-1"
    supervisor.status = {"roles": {"writer": {"thread_id": "thread-1", "turn_id": "turn-1", "turn_start_state": "acknowledged", "turn_status": "inProgress", "state": "running"}}}
    pending = {
        "request_id": "request-1", "method": request_message["method"], "thread_id": "thread-1", "turn_id": "turn-1",
        "role": "writer", "transport_epoch": "epoch-1", "server_request": request_message,
    }
    assert supervisor._pending_is_current(key, pending)
    assert not supervisor._pending_is_current(key, {**pending, "transport_epoch": "old-epoch"})
    client.server_requests[key] = {**request_message, "params": {**request_message["params"], "command": "different request"}}
    assert not supervisor._pending_is_current(key, pending)
    client.server_requests[key] = request_message
    supervisor.status["roles"]["writer"]["turn_id"] = "turn-2"
    assert not supervisor._pending_is_current(key, pending)
    supervisor.status["roles"]["writer"].update({"thread_id": "thread-2", "turn_id": "turn-1"})
    assert not supervisor._pending_is_current(key, pending)
    supervisor.status["roles"]["writer"]["thread_id"] = "thread-1"
    supervisor.status["roles"]["writer"].update({"turn_id": "turn-1", "turn_status": "interrupted", "state": "interrupted"})
    assert not supervisor._pending_is_current(key, pending)
    supervisor.status["roles"]["writer"].update({"turn_status": "inProgress", "state": "running"})
    client.closed.set()
    assert not supervisor._pending_is_current(key, pending)


def test_legacy_pending_projection_drops_unrecognized_or_malformed_values():
    marker = "SYNTHETIC_RAW_COMMAND_MARKER"
    projected = runtime._pending_request_projection(
        [
            {
                "request_id": "request-1",
                "kind": "item/commandExecution/requestApproval",
                "action": "accept",
                "thread_id": "thread-1",
                "turn_id": "turn-1",
                "params": {"command": marker},
            },
            {"request_id": "request-2", "kind": marker, "thread_id": marker, "turn_id": "turn-2"},
            {"request_id": "request-3", "kind": [marker], "thread_id": marker},
            {"request_id": {"raw": marker}, "kind": "item/commandExecution/requestApproval"},
        ]
    )
    assert projected == [
        {
            "request_id": "request-1",
            "kind": "item/commandExecution/requestApproval",
            "action": "explicit reply required",
            "thread_id": "thread-1",
            "turn_id": "turn-1",
        }
    ]


@pytest.mark.parametrize("terminal_state", ["completed", "failed", "interrupted"])
def test_all_terminal_turn_statuses_invalidate_pending_authority(tmp_path: Path, terminal_state: str):
    role = app_server.RolePlan(
        name="writer", access="writer", model="gpt-6-luna", effort="low", outcome="work",
        owns=("src/writer.py",), constraints=(), depends_on=(), prompt="", fast_requested=False,
    )
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    key = "str:request-1"
    server_request = {"id": "request-1", "method": "item/commandExecution/requestApproval", "params": {}}
    client = SimpleNamespace(closed=asyncio.Event(), server_requests={key: server_request})
    supervisor = app_server.AppServerSupervisor.__new__(app_server.AppServerSupervisor)
    supervisor.task_dir = task_dir
    supervisor.manifest = {"workspace": str(tmp_path)}
    supervisor.roles = {"writer": role}
    supervisor.client = client
    supervisor.connection_epoch = "epoch-1"
    supervisor.pending_requests = {
        key: {
            "request_id": "request-1", "method": server_request["method"], "thread_id": "thread-1",
            "turn_id": "turn-1", "role": "writer", "transport_epoch": "epoch-1", "server_request": server_request,
        }
    }
    supervisor.status = {
        "task_id": "task",
        "roles": {"writer": {"thread_id": "thread-1", "turn_id": "turn-1", "turn_start_state": "acknowledged", "turn_status": "inProgress", "state": "running"}},
    }
    supervisor.event_summaries = []
    supervisor.event_sequence = 0
    supervisor.role_done = {"writer": asyncio.Event()}
    turn = {"id": "turn-1", "status": terminal_state, "items": []}
    if terminal_state == "completed":
        turn["items"] = [{"type": "agentMessage", "text": "completed report"}]
    supervisor._finish_from_turn("writer", turn)
    assert supervisor.pending_requests == {}
    assert client.server_requests == {}
    assert supervisor.role_done["writer"].is_set()
    assert json.loads((task_dir / "pending-requests.private.json").read_text(encoding="utf-8"))["requests"] == []


def test_server_reply_policy_keeps_readers_read_only_and_requires_explicit_scoped_choices(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    writer_role = app_server.RolePlan(
        name="writer", access="writer", model="gpt-6-luna", effort="low", outcome="work",
        owns=("src/writer.py",), constraints=(), depends_on=(), prompt="", fast_requested=False,
    )
    reviewer_role = app_server.RolePlan(
        name="reviewer", access="reviewer", model="gpt-6.1-sol", effort="xhigh", outcome="review",
        owns=(), constraints=(), depends_on=(), prompt="", fast_requested=False,
    )
    command_pending = {"method": "item/commandExecution/requestApproval", "params": {}}
    assert app_server._validate_server_reply(
        command_pending, {"decision": "acceptForSession"}, writer_role, workspace
    ) == {"decision": "acceptForSession"}
    assert app_server._validate_server_reply(
        command_pending, {"decision": "decline"}, reviewer_role, workspace
    ) == {"decision": "decline"}
    with pytest.raises(common.FastAgentsError, match="read-only roles cannot approve"):
        app_server._validate_server_reply(command_pending, {"decision": "accept"}, reviewer_role, workspace)

    permission_pending = {
        "method": "item/permissions/requestApproval",
        "params": {
            "permissions": {
                "network": {"enabled": False},
                "fileSystem": {"write": [str(workspace / "src" / "writer.py")]},
            }
        },
    }
    with pytest.raises(common.FastAgentsError, match="read-only roles cannot grant filesystem writes"):
        app_server._validate_server_reply(
            permission_pending,
            {"payload": {"permissions": {"fileSystem": {"write": [str(workspace / "src" / "writer.py")]}}}},
            reviewer_role,
            workspace,
        )
    turn_scoped = app_server._validate_server_reply(
        permission_pending,
        {"payload": {"permissions": {"network": {"enabled": False}}}},
        writer_role,
        workspace,
    )
    assert turn_scoped["scope"] == "turn"
    session_scoped = app_server._validate_server_reply(
        permission_pending,
        {"payload": {"permissions": {"network": {"enabled": False}}, "scope": "session"}},
        writer_role,
        workspace,
    )
    assert session_scoped["scope"] == "session"
    with pytest.raises(common.FastAgentsError, match="unsupported server-request method"):
        app_server._validate_server_reply({"method": "unknown/request"}, {}, writer_role, workspace)


@pytest.mark.parametrize(("loss", "expected_final"), [("active", "recovery_required"), ("completed", "completed")])
def test_app_server_loss_reconciles_threads_without_replaying_uncertain_turns(
    tmp_path: Path, fake_app_server: FakeAppServer, loss: str, expected_final: str
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    roles = [writer("first", ["src/first.py"]), writer("second", ["src/second.py"], depends_on=["first"])]
    manifest = write_manifest(
        tmp_path / "task.json", workspace, roles, backend="app-server", fast_requested=False,
        fast_selection={"mode": "none", "workers": []},
    )
    runtime_root = tmp_path / "runtime"
    launched = launch_app_server(
        manifest, fake_app_server, runtime_root,
        env=fake_app_server.env(FAST_APP_SERVER_LOSS=loss, FAST_APP_SERVER_DELAY="0.05"),
    )
    assert launched.returncode == 0, launched.stderr
    task_id = task_id_from(launched)
    status = wait_for_control_status(fake_app_server, task_id, runtime_root, lambda item: item["state"] == "recovery_required")
    deadline = time.monotonic() + 5
    server_state = "alive"
    while time.monotonic() < deadline:
        server_state, _ = runtime.inspect_process(status["server"].get("identity"))
        if server_state in {"dead", "stale_identity"}:
            break
        time.sleep(0.05)
    assert server_state in {"dead", "stale_identity"}
    if loss == "active":
        blocked_manifest = write_manifest(
            tmp_path / "blocked.json", workspace, [writer("blocked")], backend="app-server", fast_requested=False,
            fast_selection={"mode": "none", "workers": []},
        )
        competing = launch_app_server(blocked_manifest, fake_app_server, tmp_path / "competing-runtime", env=fake_app_server.env())
        assert competing.returncode == 2
        assert "active writer task" in competing.stderr
    recovered = run_cli(["resume", "--task", task_id, "--runtime-root", str(runtime_root), "--recover", "--json"], fake_app_server.env(), timeout=20)
    assert recovered.returncode == 0, recovered.stderr
    if loss == "active":
        status = wait_for_control_status(fake_app_server, task_id, runtime_root, lambda item: item["state"] == "recovery_required")
        assert status["roles"]["first"]["state"] == "unknown"
        assert "no new turn was started" in status["action"]
        assert fake_app_server.state()["counts"]["turn/start"] == 1
    else:
        status = wait_for_manager(runtime_root, task_id)
        assert status["state"] == expected_final
        assert status["roles"]["first"]["state"] == "completed"
        assert status["roles"]["second"]["state"] == "completed"
        assert (runtime.task_path(runtime_root, task_id) / "agents" / "first" / "final.md").exists()
        assert fake_app_server.state()["counts"]["turn/start"] == 2
    assert fake_app_server.state()["counts"]["thread/start"] == (1 if loss == "active" else 2)


def test_disconnect_invalidates_pending_request_and_recovery_does_not_replay_it(
    tmp_path: Path, fake_app_server: FakeAppServer
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    manifest = write_manifest(
        tmp_path / "manifest.json", workspace, [writer()], backend="app-server", fast_requested=False,
        fast_selection={"mode": "none", "workers": []},
    )
    runtime_root = tmp_path / "runtime"
    launched = launch_app_server(
        manifest,
        fake_app_server,
        runtime_root,
        env=fake_app_server.env(FAST_APP_SERVER_REQUEST="approval", FAST_APP_SERVER_REQUEST_THEN_EXIT="1"),
    )
    assert launched.returncode == 0, launched.stderr
    task_id = task_id_from(launched)
    status = wait_for_control_status(fake_app_server, task_id, runtime_root, lambda item: item["state"] == "recovery_required")
    task_dir = runtime.task_path(runtime_root, task_id)
    assert status["pending_requests"] == []
    assert json.loads((task_dir / "pending-requests.private.json").read_text(encoding="utf-8"))["requests"] == []

    # Simulate a previous implementation's stale public snapshot. Recovery must ignore it.
    status_file = task_dir / "status.json"
    saved = json.loads(status_file.read_text(encoding="utf-8"))
    saved["pending_requests"] = [
        {"request_id": "pending-stale", "kind": "item/commandExecution/requestApproval", "action": "explicit reply required", "thread_id": "old-thread", "turn_id": "old-turn"}
    ]
    status_file.write_text(json.dumps(saved), encoding="utf-8")
    recovered = run_cli(
        ["resume", "--task", task_id, "--runtime-root", str(runtime_root), "--recover", "--json"],
        fake_app_server.env(),
        timeout=20,
    )
    assert recovered.returncode == 0, recovered.stderr
    current = control_status(fake_app_server, task_id, runtime_root)
    assert current["pending_requests"] == []
    stale_reply = run_cli(
        [
            "control", "--task", task_id, "--runtime-root", str(runtime_root), "reply",
            "--request-id", "pending-stale", "--thread-id", "old-thread", "--turn-id", "old-turn", "--decision", "accept",
        ],
        fake_app_server.env(),
    )
    assert stale_reply.returncode == 2
    assert "no longer pending" in stale_reply.stderr
    assert fake_app_server.state().get("server_responses", []) == []


def test_omitted_fast_setting_routes_every_app_server_worker_to_priority(
    tmp_path: Path, fake_app_server: FakeAppServer
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    manifest_path = write_manifest(
        tmp_path / "task.json",
        workspace,
        [writer("write", ["src/write.py"]), reviewer("review", depends_on=["write"])],
        backend="app-server",
    )
    raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    raw.pop("fast_requested")
    manifest_path.write_text(json.dumps(raw), encoding="utf-8")

    resolved = common.load_manifest(manifest_path, defaults_path=tmp_path / "missing-defaults.json")
    assert resolved.fast_selection == {"mode": "all", "workers": []}
    assert all(role.fast_requested for role in resolved.roles)
    assert all(role.setting_sources["fast"] == "recommended" for role in resolved.roles)

    runtime_root = tmp_path / "runtime"
    launched = launch_app_server(manifest_path, fake_app_server, runtime_root)
    assert launched.returncode == 0, launched.stderr
    task_id = task_id_from(launched)
    status = wait_for_manager(runtime_root, task_id)
    assert status["state"] == "completed"
    assert all(status["roles"][name]["fast"]["requested_tier"] == "priority" for name in ("write", "review"))
    turn_starts = [
        item["params"]
        for item in fake_app_server.state()["requests"]
        if item.get("method") == "turn/start"
    ]
    assert len(turn_starts) == 2
    assert {item["serviceTierForTurn"] for item in turn_starts} == {"priority"}


@pytest.mark.parametrize(
    ("selection", "effective_tier", "requested_tier"),
    [
        ({"mode": "all", "workers": []}, "default", "priority"),
        ({"mode": "none", "workers": []}, "priority", "default"),
    ],
)
def test_effective_tier_mismatch_blocks_turn_without_retry_or_fallback(
    tmp_path: Path, fake_app_server: FakeAppServer,
    selection: dict[str, Any], effective_tier: str, requested_tier: str,
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    manifest = write_manifest(
        tmp_path / "task.json", workspace, [writer()], backend="app-server", fast_requested=False,
        fast_selection=selection,
    )
    runtime_root = tmp_path / "runtime"
    launched = launch_app_server(
        manifest, fake_app_server, runtime_root,
        env=fake_app_server.env(FAST_APP_SERVER_EFFECTIVE_TIER=effective_tier),
    )
    assert launched.returncode == 0, launched.stderr
    task_id = task_id_from(launched)
    status = wait_for_manager(runtime_root, task_id)
    assert status["roles"]["writer"]["state"] == "failed"
    assert status["roles"]["writer"]["fast"]["requested_tier"] == requested_tier
    assert status["roles"]["writer"]["fast"]["effective_tier"] == effective_tier
    assert status["roles"]["writer"]["fast"]["upstream"] == "unverified"
    state = fake_app_server.state()
    assert state["counts"].get("turn/start", 0) == 0
    assert state["counts"]["thread/start"] == 1


@pytest.mark.parametrize(
    ("override", "fast_selection", "message"),
    [
        ({"FAST_APP_SERVER_NO_PRIORITY": "1"}, {"mode": "all", "workers": []}, "priority tier is unavailable"),
        ({"FAST_APP_SERVER_NO_FAST_FEATURE": "1"}, {"mode": "all", "workers": []}, "Fast priority is unavailable"),
    ],
)
def test_unavailable_requested_service_tier_or_fast_feature_blocks_before_task_start(
    tmp_path: Path,
    fake_app_server: FakeAppServer,
    override: dict[str, str],
    fast_selection: dict[str, Any],
    message: str,
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    manifest = write_manifest(
        tmp_path / "task.json", workspace, [writer()], backend="app-server", fast_requested=False,
        fast_selection=fast_selection,
    )
    runtime_root = tmp_path / "runtime"
    launched = launch_app_server(manifest, fake_app_server, runtime_root, env=fake_app_server.env(**override))
    assert launched.returncode == 2
    assert message in launched.stderr
    assert not (runtime_root / "tasks").exists()


def test_standard_default_is_available_without_a_catalog_tier_id(tmp_path: Path, fake_app_server: FakeAppServer):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    manifest = write_manifest(
        tmp_path / "task.json", workspace, [writer()], backend="app-server", fast_requested=False,
        fast_selection={"mode": "none", "workers": []},
    )
    runtime_root = tmp_path / "runtime"
    launched = launch_app_server(manifest, fake_app_server, runtime_root)
    assert launched.returncode == 0, launched.stderr
    task_id = task_id_from(launched)
    status = wait_for_manager(runtime_root, task_id)
    assert status["state"] == "completed"
    fast = status["roles"]["writer"]["fast"]
    assert fast["requested_tier"] == "default"
    assert fast["effective_tier_reported"] is None
    assert fast["effective_tier"] == "default"
    assert fast["effective"] == "matched"
    state = fake_app_server.state()
    assert state["counts"]["thread/start"] == 1
    assert state["counts"]["turn/start"] == 1


@pytest.mark.parametrize(("malformed", "needs_recovery"), [("bad-json", False), ("partial", True)])
def test_malformed_and_partial_app_server_events_are_private_and_fail_closed(
    tmp_path: Path, fake_app_server: FakeAppServer, malformed: str, needs_recovery: bool
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    manifest = write_manifest(
        tmp_path / "task.json", workspace, [writer()], backend="app-server", fast_requested=False,
        fast_selection={"mode": "none", "workers": []},
    )
    runtime_root = tmp_path / "runtime"
    launched = launch_app_server(manifest, fake_app_server, runtime_root, env=fake_app_server.env(FAST_APP_SERVER_MALFORMED=malformed))
    assert launched.returncode == 0, launched.stderr
    task_id = task_id_from(launched)
    task_dir = runtime.task_path(runtime_root, task_id)
    if needs_recovery:
        status = wait_for_control_status(fake_app_server, task_id, runtime_root, lambda item: item["state"] == "recovery_required")
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and runtime.inspect_process(status["server"].get("identity"))[0] not in {"dead", "stale_identity"}:
            time.sleep(0.05)
        recovered = run_cli(["resume", "--task", task_id, "--runtime-root", str(runtime_root), "--recover", "--json"], fake_app_server.env())
        assert recovered.returncode == 0, recovered.stderr
        status = wait_for_manager(runtime_root, task_id)
    else:
        status = wait_for_manager(runtime_root, task_id)
    assert status["state"] == "completed"
    errors = (task_dir / "protocol-errors.jsonl").read_text(encoding="utf-8")
    assert "malformed" in errors or "unterminated" in errors


def test_platform_adapters_cover_live_host_and_mocked_windows_launch_and_private_acl(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    platform = importlib.import_module("fast_agents_platform")
    assert platform.process_lock_kind("nt") == "msvcrt"
    assert platform.process_lock_kind("posix") == "fcntl"
    assert "creationflags" in platform.detached_process_options("nt")
    assert platform.detached_process_options("posix")["start_new_session"] is True
    assert platform.platform_coverage_label("nt", "win32") == "Windows live host"
    assert platform.platform_coverage_label("posix", "linux") == "Linux live host"
    assert platform.platform_coverage_label("posix", "darwin") == "macOS live host"

    npm = tmp_path / "npm"
    cmd = npm / "codex.cmd"
    cmd.parent.mkdir(parents=True)
    cmd.write_text("wrapper", encoding="utf-8")
    javascript = npm / "node_modules" / "@openai" / "codex" / "bin" / "codex.js"
    javascript.parent.mkdir(parents=True)
    javascript.write_text("", encoding="utf-8")
    node = tmp_path / "node.exe"
    monkeypatch.setattr(platform.shutil, "which", lambda name: str(node) if name in {"node", "node.exe"} else None)
    argv = platform.codex_argv(str(cmd), "--version", os_name="nt")
    assert argv == [str(node), str(javascript.resolve()), "--version"]
    monkeypatch.setattr(runtime, "codex_argv", lambda executable, *arguments: platform.codex_argv(executable, *arguments, os_name="nt"))
    writer_role = common.Role(
        "writer", "writer", "gpt-6-luna", "low", "outcome", ("src/writer.py",), (), (), "", False, {}
    )
    exec_argv = runtime.build_codex_argv(str(cmd), tmp_path, writer_role, False, tmp_path / "task")
    assert exec_argv == [str(node), str(javascript.resolve()), "exec", "-C", str(tmp_path), "-m", "gpt-6-luna", "-c", 'model_reasoning_effort="low"', "-c", 'service_tier="default"', "-c", "features.fast_mode=false", "--approve-for-me", "--json", "--color", "never", "-o", str(tmp_path / "task" / "agents" / "writer" / "final.md"), "-"]
    with pytest.raises(common.FastAgentsError, match="refusing cmd.exe"):
        platform.codex_argv(str(tmp_path / "missing.cmd"), "--version", os_name="nt")

    protected = tmp_path / "private.json"
    protected.write_text("{}", encoding="utf-8")
    if os.name != "nt":
        platform.protect_private_path(protected)
        assert protected.stat().st_mode & 0o777 == 0o600
    else:
        platform.protect_private_path(protected)
    called: list[bool] = []
    monkeypatch.setattr(platform, "_protect_windows_path", lambda _path, directory: called.append(directory))
    monkeypatch.setattr(platform, "os", type("FakeOS", (), {"name": "nt"})())
    platform.protect_private_path(protected)
    assert called == [False]
    live_identity = runtime.capture_process_identity(os.getpid())
    assert live_identity and live_identity["start_token"]
    assert runtime.inspect_process(live_identity)[0] == "alive"
    stale = dict(live_identity, start_token=f"stale-{live_identity['start_token']}")
    assert runtime.inspect_process(stale)[0] == "stale_identity"
