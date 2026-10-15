from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "fast-codex-subagents" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

common = importlib.import_module("fast_agents_common")
cli = importlib.import_module("fast_agents")


def test_codex_runtime_guard_refuses_claude_host_marker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    home = tmp_path / "codex-home"
    home.mkdir()
    (home / "config.toml").write_text("# configured\n", encoding="utf-8")
    monkeypatch.setenv("CODEX_HOME", str(home))
    monkeypatch.setenv("CLAUDECODE", "1")
    with pytest.raises(common.FastAgentsError, match="Codex-only"):
        cli.require_codex_runtime_environment()
