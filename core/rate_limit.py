"""Small thread-safe in-process sliding-window rate limiter."""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass
from functools import lru_cache
from threading import BoundedSemaphore, Lock


class SlidingWindowRateLimiter:
    """Bound event starts within a monotonic time window.

    This is a process-local guardrail. Public production deployments should
    additionally enforce request throttling at the reverse proxy or WAF.
    """

    def __init__(self, max_events: int, window_seconds: float) -> None:
        if max_events < 1 or window_seconds <= 0:
            raise ValueError("Rate limiter bounds must be positive.")
        self.max_events = max_events
        self.window_seconds = float(window_seconds)
        self._events: deque[float] = deque()
        self._lock = Lock()

    def try_acquire(self, *, now: float | None = None) -> tuple[bool, float]:
        current = time.monotonic() if now is None else float(now)
        with self._lock:
            cutoff = current - self.window_seconds
            while self._events and self._events[0] <= cutoff:
                self._events.popleft()
            if len(self._events) >= self.max_events:
                retry_after = max(0.0, self._events[0] + self.window_seconds - current)
                return False, retry_after
            self._events.append(current)
            return True, 0.0


@dataclass(frozen=True)
class ApplicationGuardrails:
    analysis_starts: SlidingWindowRateLimiter
    concurrent_analyses: BoundedSemaphore


@lru_cache(maxsize=1)
def get_application_guardrails() -> ApplicationGuardrails:
    """Return process-wide guards that survive Streamlit script reruns."""

    return ApplicationGuardrails(
        analysis_starts=SlidingWindowRateLimiter(max_events=8, window_seconds=600),
        concurrent_analyses=BoundedSemaphore(value=1),
    )
