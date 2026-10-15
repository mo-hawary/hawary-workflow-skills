"""Discover all Fast Codex tests on every platform without shell glob semantics."""
from pathlib import Path
import subprocess
import sys

if __name__ == "__main__":
    tests = sorted(str(path) for path in Path(__file__).parent.glob("test_fast_agents*.py"))
    raise SystemExit(subprocess.call([sys.executable, "-m", "pytest", "-q", *tests, *sys.argv[1:]]))
