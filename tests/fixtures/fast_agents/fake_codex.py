#!/usr/bin/env python3
"""A local-only Codex CLI stand-in used by fast-agents tests."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path


def emit(event: dict[str, object], fragments: bool = False) -> None:
    raw = json.dumps(event, separators=(",", ":")).encode("utf-8") + b"\n"
    if fragments:
        split_at = max(1, len(raw) // 2)
        os.write(1, raw[:split_at])
        time.sleep(0.01)
        os.write(1, raw[split_at:])
    else:
        os.write(1, raw)


def append_timeline(root: Path, action: str, role: str) -> None:
    entry = {"action": action, "role": role, "time": time.time()}
    with (root / "timeline.jsonl").open("ab") as stream:
        stream.write(json.dumps(entry).encode("utf-8") + b"\n")


def main() -> int:
    args = sys.argv[1:]
    if args == ["--version"]:
        print("codex-cli 0.162.1")
        return 0
    if args == ["exec", "--help"]:
        print("-C, --cd -m, --model -c, --config --enable --approve-for-me -s, --sandbox --json --color --output-last-message")
        return 0
    if args == ["features", "list"]:
        if os.environ.get("FAST_AGENTS_FAKE_NO_FAST_FEATURE") == "1":
            print("other_feature stable true")
        else:
            print("fast_mode stable true")
        return 0
    if not args or args[0] != "exec":
        print("unsupported fake codex invocation", file=sys.stderr)
        return 2

    role = os.environ.get("FAST_AGENTS_ROLE", "unknown")
    task_id = os.environ.get("FAST_AGENTS_TASK_ID", "task")
    capture_root = Path(os.environ["FAST_AGENTS_CAPTURE_DIR"])
    control_root = Path(os.environ["FAST_AGENTS_CONTROL_DIR"])
    capture_root.mkdir(parents=True, exist_ok=True)
    control_root.mkdir(parents=True, exist_ok=True)
    try:
        config = json.loads((control_root / f"{role}.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        config = {}

    prompt = sys.stdin.read()
    capture_path = capture_root / f"{task_id}-{role}.json"
    capture_path.write_text(
        json.dumps({"args": args, "prompt": prompt, "pid": os.getpid()}, ensure_ascii=False),
        encoding="utf-8",
    )
    append_timeline(capture_root, "start", role)

    stderr_bytes = int(config.get("stderr_bytes", 0))
    if stderr_bytes:
        block = b"private stderr fixture\n" * 4096
        remaining = stderr_bytes
        while remaining:
            chunk = block[:remaining]
            os.write(2, chunk)
            remaining -= len(chunk)

    session_id = f"session-{task_id}-{role}"
    emit({"type": "thread.started", "thread_id": session_id}, fragments=True)
    emit({"type": "turn.started", "thread_id": session_id}, fragments=True)
    if config.get("malformed"):
        os.write(1, b"{not-json}\n")
    if config.get("fast_result"):
        fast_result = config["fast_result"]
        emit(
            {
                "type": "runtime_config",
                "thread_id": session_id,
                "runtime_config": {
                    "effective": True,
                    "service_tier": fast_result.get("service_tier", "fast"),
                    "fast_mode": fast_result.get("fast_mode", True),
                },
            }
        )
    if config.get("partial"):
        os.write(1, b'{"type":"turn.completed"')

    time.sleep(float(config.get("delay", 0.0)))
    if config.get("crash"):
        os._exit(int(config.get("exit_code", 23)))

    output_path = None
    if "-o" in args:
        output_path = Path(args[args.index("-o") + 1])
    if not config.get("missing_report") and output_path:
        report_chars = config.get("report_chars")
        report = "R" * report_chars if isinstance(report_chars, int) and report_chars > 0 else f"FAKE REPORT {role}\n"
        output_path.write_text(report, encoding="utf-8")
    append_timeline(capture_root, "finish", role)
    return int(config.get("exit_code", 0))


if __name__ == "__main__":
    raise SystemExit(main())
