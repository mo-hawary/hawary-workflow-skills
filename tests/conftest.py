"""Shared pytest setup.

The fast-codex-subagents guard refuses to run inside other agent hosts. Strip
those host markers so the suite behaves the same from a Claude Code or Gemini
shell; tests that need a host marker set it explicitly with monkeypatch.
"""
from __future__ import annotations

import pytest

AGENT_HOST_VARIABLES = ("CLAUDECODE", "GEMINI_CLI", "HWS_AGENT")


@pytest.fixture(autouse=True)
def _isolate_agent_host_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in AGENT_HOST_VARIABLES:
        monkeypatch.delenv(name, raising=False)
