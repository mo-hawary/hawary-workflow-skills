from __future__ import annotations

import errno
import json
from pathlib import Path
import sys
from typing import Any, Callable

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "fast-codex-subagents" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import fast_agents_common as common
import fast_agents_platform


RETRY_DELAYS = [0.02, 0.04, 0.08, 0.16, 0.32]


def _windows_error(winerror: int, *, path: Path | None = None) -> PermissionError:
    error = PermissionError(errno.EACCES, "Permission denied", str(path) if path else None)
    error.winerror = winerror
    return error


def _unmapped_access_denied(path: Path) -> PermissionError:
    error = PermissionError(errno.EACCES, "Permission denied", str(path))
    assert getattr(error, "winerror", None) is None
    return error


def _force_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(common, "_is_windows_host", lambda: True, raising=False)


@pytest.mark.parametrize("winerror", [5, 32, 33])
def test_atomic_write_retries_transient_windows_sharing_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, winerror: int
) -> None:
    _force_windows(monkeypatch)
    destination = tmp_path / "status.json"
    destination.write_bytes(b"old")
    real_replace = common.os.replace
    attempts = 0
    delays: list[float] = []

    def replace(source: Path, target: Path) -> None:
        nonlocal attempts
        attempts += 1
        if attempts <= 2:
            raise _windows_error(winerror, path=target)
        real_replace(source, target)

    monkeypatch.setattr(common.os, "replace", replace)
    monkeypatch.setattr("time.sleep", delays.append)

    common.atomic_write_bytes(destination, b"new")

    assert destination.read_bytes() == b"new"
    assert attempts == 3
    assert delays == RETRY_DELAYS[:2]


@pytest.mark.parametrize("winerror", [5, 32, 33])
def test_read_json_retries_transient_windows_sharing_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, winerror: int
) -> None:
    _force_windows(monkeypatch)
    path = tmp_path / "status.json"
    path.write_text('{"state": "running"}', encoding="utf-8")
    real_read_text = Path.read_text
    attempts = 0
    delays: list[float] = []

    def read_text(target: Path, *args: Any, **kwargs: Any) -> str:
        nonlocal attempts
        if target == path:
            attempts += 1
            if attempts == 1:
                raise _windows_error(winerror, path=target)
        return real_read_text(target, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read_text)
    monkeypatch.setattr("time.sleep", delays.append)

    assert common.read_json(path) == {"state": "running"}
    assert attempts == 2
    assert delays == RETRY_DELAYS[:1]


def test_read_json_retries_windows_eacces_without_winerror(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _force_windows(monkeypatch)
    path = tmp_path / "status.json"
    path.write_text('{"state": "running"}', encoding="utf-8")
    real_read_text = Path.read_text
    attempts = 0
    delays: list[float] = []

    def read_text(target: Path, *args: Any, **kwargs: Any) -> str:
        nonlocal attempts
        if target == path:
            attempts += 1
            if attempts == 1:
                raise _unmapped_access_denied(target)
        return real_read_text(target, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read_text)
    monkeypatch.setattr("time.sleep", delays.append)

    assert common.read_json(path) == {"state": "running"}
    assert attempts == 2
    assert delays == RETRY_DELAYS[:1]


def test_atomic_write_exhaustion_preserves_destination_and_cleans_temp_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _force_windows(monkeypatch)
    destination = tmp_path / "status.json"
    destination.write_bytes(b"old")
    attempts = 0
    delays: list[float] = []

    def replace(source: Path, target: Path) -> None:
        nonlocal attempts
        attempts += 1
        raise _windows_error(5, path=target)

    monkeypatch.setattr(common.os, "replace", replace)
    monkeypatch.setattr("time.sleep", delays.append)

    with pytest.raises(PermissionError) as raised:
        common.atomic_write_bytes(destination, b"new")

    assert raised.value.winerror == 5
    assert attempts == 6
    assert delays == RETRY_DELAYS
    assert destination.read_bytes() == b"old"
    assert list(tmp_path.glob(".status.json.*")) == []


def test_read_json_exhaustion_preserves_original_error_as_cause(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _force_windows(monkeypatch)
    path = tmp_path / "status.json"
    errors: list[PermissionError] = []
    delays: list[float] = []

    def read_text(target: Path, *args: Any, **kwargs: Any) -> str:
        error = _unmapped_access_denied(target)
        errors.append(error)
        raise error

    monkeypatch.setattr(Path, "read_text", read_text)
    monkeypatch.setattr("time.sleep", delays.append)

    with pytest.raises(common.FastAgentsError) as raised:
        common.read_json(path)

    assert raised.value.__cause__ is errors[-1]
    assert raised.value.__cause__.errno == errno.EACCES
    assert getattr(raised.value.__cause__, "winerror", None) is None
    assert len(errors) == 6
    assert delays == RETRY_DELAYS


@pytest.mark.parametrize(
    ("windows", "error_factory"),
    [
        (False, lambda path: _windows_error(5, path=path)),
        (True, lambda path: _windows_error(13, path=path)),
        (True, _unmapped_access_denied),
    ],
)
def test_atomic_write_does_not_retry_other_platform_or_error_codes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    windows: bool,
    error_factory: Callable[[Path], PermissionError],
) -> None:
    monkeypatch.setattr(common, "_is_windows_host", lambda: windows, raising=False)
    destination = tmp_path / "status.json"
    destination.write_bytes(b"old")
    attempts = 0
    delays: list[float] = []

    def replace(source: Path, target: Path) -> None:
        nonlocal attempts
        attempts += 1
        raise error_factory(target)

    monkeypatch.setattr(common.os, "replace", replace)
    monkeypatch.setattr("time.sleep", delays.append)

    with pytest.raises(PermissionError):
        common.atomic_write_bytes(destination, b"new")

    assert attempts == 1
    assert delays == []
    assert destination.read_bytes() == b"old"
    assert list(tmp_path.glob(".status.json.*")) == []


def test_read_json_does_not_retry_eacces_on_posix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(common, "_is_windows_host", lambda: False, raising=False)
    path = tmp_path / "status.json"
    attempts = 0
    delays: list[float] = []

    def read_text(target: Path, *args: Any, **kwargs: Any) -> str:
        nonlocal attempts
        attempts += 1
        raise _unmapped_access_denied(target)

    monkeypatch.setattr(Path, "read_text", read_text)
    monkeypatch.setattr("time.sleep", delays.append)

    with pytest.raises(common.FastAgentsError):
        common.read_json(path)

    assert attempts == 1
    assert delays == []


def test_read_json_does_not_retry_eacces_with_explicit_other_winerror(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _force_windows(monkeypatch)
    path = tmp_path / "status.json"
    attempts = 0
    delays: list[float] = []

    def read_text(target: Path, *args: Any, **kwargs: Any) -> str:
        nonlocal attempts
        attempts += 1
        raise _windows_error(13, path=target)

    monkeypatch.setattr(Path, "read_text", read_text)
    monkeypatch.setattr("time.sleep", delays.append)

    with pytest.raises(common.FastAgentsError):
        common.read_json(path)

    assert attempts == 1
    assert delays == []


def test_atomic_write_does_not_retry_acl_error_or_leave_temp_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _force_windows(monkeypatch)
    destination = tmp_path / "status.json"
    destination.write_bytes(b"old")
    protect_calls = 0
    replace_calls = 0
    delays: list[float] = []

    def protect(path: Path, *, directory: bool = False) -> None:
        nonlocal protect_calls
        protect_calls += 1
        raise _windows_error(5, path=path)

    def replace(source: Path, target: Path) -> None:
        nonlocal replace_calls
        replace_calls += 1
        raise AssertionError("replace must not run after ACL verification fails")

    monkeypatch.setattr(fast_agents_platform, "protect_private_path", protect)
    monkeypatch.setattr(common.os, "replace", replace)
    monkeypatch.setattr("time.sleep", delays.append)

    with pytest.raises(PermissionError) as raised:
        common.atomic_write_bytes(destination, b"new")

    assert raised.value.winerror == 5
    assert protect_calls == 1
    assert replace_calls == 0
    assert delays == []
    assert destination.read_bytes() == b"old"
    assert list(tmp_path.glob(".status.json.*")) == []


def test_read_json_does_not_retry_json_parse_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _force_windows(monkeypatch)
    path = tmp_path / "status.json"
    path.write_text("{}", encoding="utf-8")
    real_read_text = Path.read_text
    read_calls = 0
    parse_calls = 0
    delays: list[float] = []

    def read_text(target: Path, *args: Any, **kwargs: Any) -> str:
        nonlocal read_calls
        read_calls += 1
        return real_read_text(target, *args, **kwargs)

    def loads(value: str) -> dict[str, Any]:
        nonlocal parse_calls
        parse_calls += 1
        raise json.JSONDecodeError("invalid status", value, 0)

    monkeypatch.setattr(Path, "read_text", read_text)
    monkeypatch.setattr(common.json, "loads", loads)
    monkeypatch.setattr("time.sleep", delays.append)

    with pytest.raises(common.FastAgentsError):
        common.read_json(path)

    assert read_calls == 1
    assert parse_calls == 1
    assert delays == []


def test_read_json_does_not_retry_missing_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _force_windows(monkeypatch)
    path = tmp_path / "missing.json"
    attempts = 0
    delays: list[float] = []
    real_read_text = Path.read_text

    def read_text(target: Path, *args: Any, **kwargs: Any) -> str:
        nonlocal attempts
        if target == path:
            attempts += 1
        return real_read_text(target, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read_text)
    monkeypatch.setattr("time.sleep", delays.append)

    with pytest.raises(FileNotFoundError):
        common.read_json(path)

    assert attempts == 1
    assert delays == []
