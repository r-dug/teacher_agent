"""Tests for the token-bucket rate limiter."""

import time
import pytest
from frontend.rate_limiter import RateLimiter


@pytest.fixture
def limiter():
    # Small capacity for predictable test behaviour
    return RateLimiter(capacity=5.0, refill_rate=1.0)


def test_allows_up_to_capacity(limiter):
    for _ in range(5):
        assert limiter.allow("key") is True


def test_rejects_over_capacity(limiter):
    for _ in range(5):
        limiter.allow("key")
    assert limiter.allow("key") is False


def test_refills_over_time(limiter):
    for _ in range(5):
        limiter.allow("key")
    assert limiter.allow("key") is False

    # Wait slightly more than 1 second for 1 token to refill
    time.sleep(1.1)
    assert limiter.allow("key") is True


def test_independent_keys(limiter):
    for _ in range(5):
        limiter.allow("key-a")
    # key-b has a fresh bucket
    assert limiter.allow("key-b") is True


def test_remove_clears_bucket(limiter):
    for _ in range(5):
        limiter.allow("key")
    limiter.remove("key")
    # After removal, a new bucket is created with full capacity
    assert limiter.allow("key") is True


def test_zero_cost_always_allowed(limiter):
    for _ in range(100):
        assert limiter.allow("key", tokens=0.0) is True


def test_high_cost_rejected_on_empty_bucket(limiter):
    for _ in range(5):
        limiter.allow("key", tokens=1.0)
    assert limiter.allow("key", tokens=3.0) is False


def test_len(limiter):
    assert len(limiter) == 0
    limiter.allow("a")
    limiter.allow("b")
    assert len(limiter) == 2
    limiter.remove("a")
    assert len(limiter) == 1
