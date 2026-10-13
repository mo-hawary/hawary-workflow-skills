"""Small standard-library adapters for private files and local process launch."""

from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from fast_agents_common import FastAgentsError


def protect_private_path(path: Path, *, directory: bool = False) -> None:
    """Enforce user-only access; fail closed when the host cannot apply it."""
    if path.is_symlink():
        raise FastAgentsError(f"private runtime path must not be a symlink: {path.name}")
    if os.name == "nt":
        _protect_windows_path(path, directory=directory)
        return
    mode = 0o700 if directory else 0o600
    path.chmod(mode)
    actual = path.stat().st_mode & 0o777
    if actual != mode:
        raise FastAgentsError(f"could not enforce private permissions on {path.name}")


def _protect_windows_path(path: Path, *, directory: bool) -> None:
    """Set and verify a protected DACL granting access only to the object owner."""
    from ctypes import wintypes

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    descriptor = ctypes.c_void_p()
    sddl = "D:P(A;OICI;FA;;;OW)" if directory else "D:P(A;;FA;;;OW)"
    convert = advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW
    convert.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p]
    convert.restype = wintypes.BOOL
    if not convert(sddl, 1, ctypes.byref(descriptor), None):
        raise OSError(ctypes.get_last_error(), "cannot create owner-only Windows security descriptor")
    try:
        set_security = advapi32.SetFileSecurityW
        set_security.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p]
        set_security.restype = wintypes.BOOL
        flags = 0x00000004 | 0x80000000  # DACL_SECURITY_INFORMATION | PROTECTED_DACL_SECURITY_INFORMATION
        if not set_security(str(path), flags, descriptor):
            raise OSError(ctypes.get_last_error(), "cannot set owner-only Windows DACL")
        _verify_windows_dacl(path, advapi32, kernel32, wintypes, directory=directory)
    finally:
        kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        kernel32.LocalFree.restype = ctypes.c_void_p
        kernel32.LocalFree(descriptor)


def _verify_windows_dacl(path: Path, advapi32: Any, kernel32: Any, wintypes: Any, *, directory: bool) -> None:
    required = wintypes.DWORD()
    get_security = advapi32.GetFileSecurityW
    get_security.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    get_security.restype = wintypes.BOOL
    flags = 0x00000004
    get_security(str(path), flags, None, 0, ctypes.byref(required))
    if not required.value:
        raise OSError(ctypes.get_last_error(), "cannot read back Windows DACL")
    buffer = ctypes.create_string_buffer(required.value)
    if not get_security(str(path), flags, buffer, required.value, ctypes.byref(required)):
        raise OSError(ctypes.get_last_error(), "cannot verify Windows DACL")

    class ACL(ctypes.Structure):
        _fields_ = [
            ("revision", ctypes.c_ubyte),
            ("reserved", ctypes.c_ubyte),
            ("size", wintypes.WORD),
            ("ace_count", wintypes.WORD),
            ("reserved2", wintypes.WORD),
        ]

    dacl_present = wintypes.BOOL()
    dacl_defaulted = wintypes.BOOL()
    dacl = ctypes.c_void_p()
    get_dacl = advapi32.GetSecurityDescriptorDacl
    get_dacl.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.BOOL), ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.BOOL)]
    get_dacl.restype = wintypes.BOOL
    if not get_dacl(buffer, ctypes.byref(dacl_present), ctypes.byref(dacl), ctypes.byref(dacl_defaulted)):
        raise OSError(ctypes.get_last_error(), "cannot inspect Windows DACL")
    if not dacl_present.value or not dacl.value or ACL.from_address(dacl.value).ace_count != 1:
        raise FastAgentsError("Windows private DACL is absent or grants access beyond the owner")

    class ACEHeader(ctypes.Structure):
        _fields_ = [("ace_type", ctypes.c_ubyte), ("ace_flags", ctypes.c_ubyte), ("ace_size", wintypes.WORD)]

    class AccessAllowedACE(ctypes.Structure):
        _fields_ = [("header", ACEHeader), ("mask", wintypes.DWORD), ("sid_start", wintypes.DWORD)]

    ace_pointer = ctypes.c_void_p()
    get_ace = advapi32.GetAce
    get_ace.argtypes = [ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p)]
    get_ace.restype = wintypes.BOOL
    if not get_ace(dacl, 0, ctypes.byref(ace_pointer)) or not ace_pointer.value:
        raise OSError(ctypes.get_last_error(), "cannot inspect Windows owner ACE")
    ace = AccessAllowedACE.from_address(ace_pointer.value)
    if ace.header.ace_type != 0 or ace.mask != 0x001F01FF:
        raise FastAgentsError("Windows private DACL does not grant full access only to the owner")
    if directory and ace.header.ace_flags & 0x03 != 0x03:
        raise FastAgentsError("Windows private directory does not protect child files and directories")

    owner_sid = ctypes.c_void_p()
    convert_sid = advapi32.ConvertStringSidToSidW
    convert_sid.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p)]
    convert_sid.restype = wintypes.BOOL
    if not convert_sid("S-1-3-4", ctypes.byref(owner_sid)):
        raise OSError(ctypes.get_last_error(), "cannot resolve Windows Owner Rights SID")
    try:
        equal_sid = advapi32.EqualSid
        equal_sid.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        equal_sid.restype = wintypes.BOOL
        ace_sid = ctypes.c_void_p(ace_pointer.value + AccessAllowedACE.sid_start.offset)
        if not equal_sid(ace_sid, owner_sid):
            raise FastAgentsError("Windows private DACL grants access to a SID other than the object owner")
    finally:
        kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        kernel32.LocalFree.restype = ctypes.c_void_p
        kernel32.LocalFree(owner_sid)

    control = wintypes.WORD()
    revision = wintypes.DWORD()
    get_control = advapi32.GetSecurityDescriptorControl
    get_control.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.WORD), ctypes.POINTER(wintypes.DWORD)]
    get_control.restype = wintypes.BOOL
    if not get_control(buffer, ctypes.byref(control), ctypes.byref(revision)) or not (control.value & 0x1000):
        raise FastAgentsError("Windows private DACL is not protected from inherited access")


def process_lock_kind(os_name: str) -> str:
    if os_name == "nt":
        return "msvcrt"
    if os_name == "posix":
        return "fcntl"
    raise FastAgentsError(f"no supported process lock for OS family {os_name!r}")


def detached_process_options(os_name: str) -> dict[str, Any]:
    if os_name == "nt":
        return {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)}
    if os_name == "posix":
        return {"start_new_session": True}
    raise FastAgentsError(f"no detached supervisor adapter for OS family {os_name!r}")


def codex_argv(executable: str, *arguments: str, os_name: str | None = None) -> list[str]:
    """Resolve a Codex command without invoking shell wrappers such as cmd.exe."""
    platform = os.name if os_name is None else os_name
    path = Path(executable).expanduser().resolve()
    if path.suffix.casefold() == ".py":
        return [sys.executable, str(path), *arguments]
    if platform != "nt":
        return [str(path), *arguments]

    if path.suffix.casefold() != ".cmd" and path.suffix.casefold() != ".bat":
        if path.suffix.casefold() == ".js":
            node = shutil.which("node.exe") or shutil.which("node")
            if not node:
                raise FastAgentsError("Node.js is required to launch the Codex JavaScript entrypoint")
            return [node, str(path), *arguments]
        return [str(path), *arguments]

    executable_path = path.with_suffix(".exe")
    if executable_path.is_file():
        return [str(executable_path), *arguments]
    javascript_candidates = [
        path.parent / "node_modules" / "@openai" / "codex" / "bin" / "codex.js",
        path.parent.parent / "@openai" / "codex" / "bin" / "codex.js",
    ]
    node = shutil.which("node.exe") or shutil.which("node")
    javascript = next((candidate for candidate in javascript_candidates if candidate.is_file()), None)
    if javascript is not None and node:
        return [node, str(javascript.resolve()), *arguments]
    raise FastAgentsError("Windows Codex launcher must be codex.exe or a resolvable Node.js launcher; refusing cmd.exe wrappers")


def platform_coverage_label(os_name: str | None = None, platform: str | None = None) -> str:
    name = os.name if os_name is None else os_name
    host = sys.platform if platform is None else platform
    if name == "nt":
        return "Windows live host"
    if host == "darwin":
        return "macOS live host"
    if host.startswith("linux"):
        return "Linux live host"
    return f"unsupported live host ({host})"
