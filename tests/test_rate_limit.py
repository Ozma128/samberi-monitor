from __future__ import annotations

import pytest

from core.rate_limit import SlidingWindowRateLimiter, get_application_guardrails


def test_sliding_window_limits_and_recovers() -> None:
    limiter = SlidingWindowRateLimiter(max_events=2, window_seconds=10)

    assert limiter.try_acquire(now=0) == (True, 0.0)
    assert limiter.try_acquire(now=1) == (True, 0.0)
    allowed, retry_after = limiter.try_acquire(now=2)
    assert allowed is False
    assert retry_after == pytest.approx(8)
    assert limiter.try_acquire(now=10) == (True, 0.0)


@pytest.mark.parametrize(("events", "window"), [(0, 1), (1, 0), (-1, 10)])
def test_invalid_rate_limit_configuration_is_rejected(events: int, window: float) -> None:
    with pytest.raises(ValueError):
        SlidingWindowRateLimiter(events, window)


def test_application_guardrails_are_process_singletons() -> None:
    assert get_application_guardrails() is get_application_guardrails()
