"""The lifecycle lock budget never sends PostgreSQL an invalid lock_timeout."""

from tracer.services.derived_metrics.deletion_protocol import (
    _PG_MAX_LOCK_TIMEOUT_MS,
    bounded_lock_timeout_ms,
)


def test_postgres_int_max_is_the_cap():
    assert _PG_MAX_LOCK_TIMEOUT_MS == 2**31 - 1


def test_values_at_and_past_the_cap_are_bounded_exactly():
    assert bounded_lock_timeout_ms(2**31 - 1) == 2**31 - 1
    assert bounded_lock_timeout_ms(2**31) == 2**31 - 1
    assert bounded_lock_timeout_ms(2**31 - 2) == 2**31 - 2
    assert bounded_lock_timeout_ms(10**12) == 2**31 - 1


def test_real_lease_budgets_and_the_floor_are_unchanged():
    assert bounded_lock_timeout_ms(300_000) == 300_000  # the default 5-minute lease
    assert bounded_lock_timeout_ms(1) == 1
    assert bounded_lock_timeout_ms(0) == 1  # 0 would disable the timeout
    assert bounded_lock_timeout_ms(-5) == 1
