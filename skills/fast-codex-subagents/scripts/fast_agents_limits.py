"""Opt-in execution limits; observed token counts are never billing estimates."""
from __future__ import annotations

import datetime as dt
import math
from typing import Any

from fast_agents_common import FastAgentsError


def validate_budgets(value: Any, label: str) -> dict[str, Any]:
    allowed = {"max_wall_seconds", "max_idle_seconds", "max_tokens", "action"}
    if not isinstance(value, dict) or set(value) - allowed:
        raise FastAgentsError(f"{label} must contain only {', '.join(sorted(allowed))}")
    if not value:
        return {}
    result = dict(value)
    for key in allowed - {"action"}:
        if key not in value:
            continue
        number = value[key]
        if (isinstance(number, bool) or not isinstance(number, (int, float))
                or not math.isfinite(number) or number <= 0
                or (key == "max_tokens" and not isinstance(number, int))):
            raise FastAgentsError(f"{label}.{key} must be a positive finite {'integer' if key == 'max_tokens' else 'number'}")
    if result.get("action", "warn") not in {"warn", "interrupt"}:
        raise FastAgentsError(f"{label}.action must be warn or interrupt")
    if not any(key in value for key in allowed - {"action"}):
        raise FastAgentsError(f"{label} must specify a limit")
    result.setdefault("action", "warn")
    return result


def age_seconds(timestamp: Any, now: dt.datetime) -> float:
    if not isinstance(timestamp, str):
        return 0.0
    try:
        start = dt.datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        if start.tzinfo is None:
            return 0.0
        return max(0.0, (now - start).total_seconds())
    except ValueError:
        return 0.0


def observed_tokens(usage: Any) -> int | None:
    if not isinstance(usage, dict):
        return None
    # App-server uses cumulative `total`, with cached input already included.
    total = usage.get("total", usage)
    if not isinstance(total, dict):
        return None
    count = total.get("totalTokens")
    return count if isinstance(count, int) and not isinstance(count, bool) and count >= 0 else None


def exceeded(budget: dict[str, Any], *, wall: float, idle: float, tokens: int | None) -> list[str]:
    measures = {"max_wall_seconds": wall, "max_idle_seconds": idle, "max_tokens": tokens}
    return [key for key, observed in measures.items()
            if key in budget and observed is not None and observed >= budget[key]]
