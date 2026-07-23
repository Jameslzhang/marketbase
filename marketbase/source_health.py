"""In-process source health tracking shared by snapshot and daily collectors."""

from __future__ import annotations

import threading
import time
from typing import Any


class SourceHealth:
    """Track per-source success/failure counts with automatic cooldown.

    Each module (snapshot, daily) instantiates its own instance so state is
    isolated per data domain.
    """

    def __init__(
        self,
        failure_threshold: int = 3,
        cooldown_seconds: float = 300.0,
    ) -> None:
        self._lock = threading.Lock()
        self._health: dict[str, dict[str, object]] = {}
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds

    # ------------------------------------------------------------------
    # public helpers
    # ------------------------------------------------------------------

    def disabled_reason(self, source: str) -> str | None:
        now = time.monotonic()
        with self._lock:
            state = self._health.get(source)
            if not state:
                return None
            disabled_until = float(state.get("disabled_until", 0.0))
            if disabled_until <= now:
                if disabled_until:
                    state["disabled_until"] = 0.0
                return None
            return (
                f"temporarily disabled for {disabled_until - now:.1f}s "
                "after repeated failures"
            )

    def record_success(self, source: str, *, rows: int | None = None) -> None:
        with self._lock:
            state = self._health.setdefault(
                source, {"failures": 0.0, "disabled_until": 0.0}
            )
            successes = float(state.get("successes", 0.0)) + 1.0
            state["successes"] = successes
            state["failures"] = 0.0
            state["disabled_until"] = 0.0
            state["last_success_at"] = time.time()
            if rows is not None:
                state["last_rows"] = float(rows)
                previous_avg = float(state.get("avg_rows", rows))
                state["avg_rows"] = previous_avg + (float(rows) - previous_avg) / successes

    def record_failure(self, source: str, error: object | None = None) -> None:
        now = time.monotonic()
        with self._lock:
            state = self._health.setdefault(
                source, {"failures": 0.0, "disabled_until": 0.0}
            )
            failures = float(state.get("failures", 0.0)) + 1.0
            state["failures"] = failures
            state["total_failures"] = float(state.get("total_failures", 0.0)) + 1.0
            state["last_failure_at"] = time.time()
            if error is not None:
                state["last_error"] = " ".join(str(error).split())
            if failures >= self.failure_threshold:
                state["disabled_until"] = now + self.cooldown_seconds

    def snapshot(
        self, sources: tuple[str, ...] | None = None
    ) -> dict[str, dict[str, float | bool | str]]:
        now = time.monotonic()
        requested = tuple(sources or tuple(self._health))
        with self._lock:
            result: dict[str, dict[str, float | bool | str]] = {}
            for source in requested:
                state = dict(self._health.get(source, {}))
                disabled_until = float(state.get("disabled_until", 0.0))
                cooldown_remaining = max(disabled_until - now, 0.0)
                result[source] = {
                    "successes": float(state.get("successes", 0.0)),
                    "failures": float(state.get("failures", 0.0)),
                    "total_failures": float(state.get("total_failures", 0.0)),
                    "last_rows": float(state.get("last_rows", 0.0)),
                    "avg_rows": float(state.get("avg_rows", 0.0)),
                    "disabled": disabled_until > now,
                    "cooldown_remaining_seconds": round(cooldown_remaining, 4),
                    "last_success_at": float(state.get("last_success_at", 0.0)),
                    "last_failure_at": float(state.get("last_failure_at", 0.0)),
                    "last_error": str(state.get("last_error", "")),
                }
        return result

    def order_by_health(
        self, sources: tuple[str, ...]
    ) -> tuple[tuple[str, ...], list[str]]:
        """Move unhealthy sources later while preserving default order ties."""
        now = time.monotonic()
        with self._lock:
            health = {s: dict(self._health.get(s, {})) for s in sources}
        default_order = {s: idx for idx, s in enumerate(sources)}

        def _key(s: str) -> tuple[int, float, int]:
            st = health.get(s, {})
            disabled = float(st.get("disabled_until", 0.0)) > now
            failures = float(st.get("failures", 0.0))
            return (1 if disabled else 0, failures, default_order[s])

        ordered = tuple(sorted(sources, key=_key))
        if ordered == sources:
            return sources, []
        return ordered, [
            f"source order adjusted by health: {','.join(ordered)}"
        ]

    def reset(self) -> None:
        """Clear all tracked health state (useful for tests)."""
        with self._lock:
            self._health.clear()