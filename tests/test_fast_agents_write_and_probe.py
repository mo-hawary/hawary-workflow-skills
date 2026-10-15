from __future__ import annotations

import asyncio
import os
from pathlib import Path
import stat
import sys
import time
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "fast-codex-subagents" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import fast_agents_common as common
import fast_agents_platform
import fast_agents_protocol as protocol


FAKE_APP_SERVER = ROOT / "tests" / "fixtures" / "fast_agents" / "fake_app_server.py"


def _posix_mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")
def test_non_private_write_preserves_user_file_mode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    destination = tmp_path / "AGENTS.md"
    destination.write_bytes(b"old")
    destination.chmod(0o644)

    def fail_protect(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("private=False must not force owner-only permissions")

    monkeypatch.setattr(fast_agents_platform, "protect_private_path", fail_protect)

    common.atomic_write_bytes(destination, b"new", mode=0o644, private=False)

    assert destination.read_bytes() == b"new"
    assert _posix_mode(destination) == 0o644


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")
def test_default_private_write_still_yields_owner_only_mode(tmp_path: Path) -> None:
    destination = tmp_path / "state.json"

    common.atomic_write_bytes(destination, b"{}")

    assert _posix_mode(destination) == 0o600


def test_write_error_does_not_double_close_file_descriptor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    destination = tmp_path / "status.json"
    destination.write_bytes(b"old")
    real_fdopen = common.os.fdopen
    real_close = common.os.close
    closed_fds: list[int] = []
    wrapped_fds: list[int] = []

    class FailingStream:
        def __init__(self, stream: Any) -> None:
            self._stream = stream

        def __enter__(self) -> "FailingStream":
            return self

        def __exit__(self, *exc: Any) -> Any:
            return self._stream.__exit__(*exc)

        def write(self, data: bytes) -> None:
            raise OSError("disk full")

    def fdopen(fd: int, mode: str = "r", *args: Any, **kwargs: Any) -> FailingStream:
        wrapped_fds.append(fd)
        return FailingStream(real_fdopen(fd, mode, *args, **kwargs))

    def close(fd: int) -> None:
        closed_fds.append(fd)
        real_close(fd)

    monkeypatch.setattr(common.os, "fdopen", fdopen)
    monkeypatch.setattr(common.os, "close", close)

    with pytest.raises(OSError, match="disk full"):
        common.atomic_write_bytes(destination, b"new")

    assert len(wrapped_fds) == 1
    assert wrapped_fds[0] not in closed_fds
    assert destination.read_bytes() == b"old"
    assert list(tmp_path.iterdir()) == [destination]


class _HangingStdin:
    def is_closing(self) -> bool:
        return False

    def close(self) -> None:
        return None

    async def wait_closed(self) -> None:
        await asyncio.Event().wait()


class _FakeProcess:
    def __init__(self) -> None:
        self.stdin = _HangingStdin()

    async def wait(self) -> int:
        return 0


def test_close_stdin_and_wait_is_bounded_when_stdin_never_drains(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(protocol, "SHUTDOWN_WAIT_TIMEOUT_SECONDS", 0.05)

    async def ignore(_message: dict[str, Any]) -> None:
        return None

    client = protocol.AppServerClient("unused", tmp_path, tmp_path / "stderr.log", ignore, ignore, ignore)
    client.process = _FakeProcess()  # type: ignore[assignment]

    started = time.monotonic()
    with pytest.raises(protocol.AppServerError, match="process ownership remains uncertain"):
        asyncio.run(client.close_stdin_and_wait())

    assert time.monotonic() - started < 5


def test_probe_model_catalog_shuts_down_client_when_initialize_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clients: list[protocol.AppServerClient] = []
    real_close = protocol.AppServerClient.close_stdin_and_wait
    real_init = protocol.AppServerClient.__init__

    def tracking_init(self: protocol.AppServerClient, *args: Any, **kwargs: Any) -> None:
        real_init(self, *args, **kwargs)
        clients.append(self)

    async def close_spy(self: protocol.AppServerClient) -> int:
        self.closed_by_probe = True  # type: ignore[attr-defined]
        return await real_close(self)

    async def failing_request(self: protocol.AppServerClient, method: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
        if method == "initialize":
            raise protocol.AppServerError("initialize failed")
        raise AssertionError(f"unexpected request {method}")

    monkeypatch.setattr(protocol.AppServerClient, "__init__", tracking_init)
    monkeypatch.setattr(protocol.AppServerClient, "close_stdin_and_wait", close_spy)
    monkeypatch.setattr(protocol.AppServerClient, "request", failing_request)

    with pytest.raises(protocol.AppServerError, match="initialize failed"):
        asyncio.run(protocol.probe_model_catalog(str(FAKE_APP_SERVER), tmp_path, tmp_path / "stderr.log"))

    assert len(clients) == 1
    client = clients[0]
    assert getattr(client, "closed_by_probe", False) is True
    assert client.process is not None
    assert client.process.returncode is not None
