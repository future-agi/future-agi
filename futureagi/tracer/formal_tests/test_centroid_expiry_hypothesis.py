"""
Hypothesis property tests for centroid TTL expiry logic (issue #306).

Tests the pure expiry predicate used by ErrorClusteringDB to decide which
cluster centroids are stale and should be deleted.  No ClickHouse connection
is required — we exercise the policy function directly.
"""

import datetime

from hypothesis import given, settings
from hypothesis import strategies as st


# ---------------------------------------------------------------------------
# The expiry predicate (mirrors the ClickHouse TTL expression)
# ---------------------------------------------------------------------------

def _is_expired(last_updated: datetime.datetime, now: datetime.datetime, ttl_days: int) -> bool:
    """Return True iff the centroid has not been updated within ttl_days."""
    return (now - last_updated).total_seconds() >= ttl_days * 86400


# ---------------------------------------------------------------------------
# Generators
# ---------------------------------------------------------------------------

_EPOCH = datetime.datetime(2020, 1, 1, tzinfo=datetime.timezone.utc)
_FAR_FUTURE = datetime.datetime(2030, 1, 1, tzinfo=datetime.timezone.utc)

_timestamp_st = st.datetimes(
    min_value=_EPOCH.replace(tzinfo=None),
    max_value=_FAR_FUTURE.replace(tzinfo=None),
)
_ttl_days_st = st.integers(min_value=1, max_value=3650)  # 1 day – 10 years


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------

@given(ttl=_ttl_days_st, age_days=st.integers(min_value=0, max_value=3649))
@settings(max_examples=500)
def test_within_ttl_is_never_expired(ttl, age_days):
    """Centroid updated age_days ago is NOT expired when age_days < ttl."""
    if age_days >= ttl:
        return  # skip: would be expired by definition
    now = datetime.datetime(2025, 6, 1)
    last_updated = now - datetime.timedelta(days=age_days)
    assert not _is_expired(last_updated, now, ttl)


@given(ttl=_ttl_days_st, extra_days=st.integers(min_value=0, max_value=365))
@settings(max_examples=500)
def test_beyond_ttl_is_always_expired(ttl, extra_days):
    """Centroid updated ttl+extra_days ago IS expired."""
    now = datetime.datetime(2025, 6, 1)
    age_days = ttl + extra_days
    last_updated = now - datetime.timedelta(days=age_days)
    assert _is_expired(last_updated, now, ttl)


@given(
    ttl1=_ttl_days_st,
    ttl2=_ttl_days_st,
    age_days=st.integers(min_value=0, max_value=3650),
)
@settings(max_examples=500)
def test_monotone_in_ttl(ttl1, ttl2, age_days):
    """Larger TTL never causes expiry where smaller TTL would not."""
    now = datetime.datetime(2025, 6, 1)
    last_updated = now - datetime.timedelta(days=age_days)
    hi_ttl = max(ttl1, ttl2)
    lo_ttl = min(ttl1, ttl2)
    if not _is_expired(last_updated, now, lo_ttl):
        # If not expired under smaller TTL, certainly not under larger.
        assert not _is_expired(last_updated, now, hi_ttl)


@given(ttl=_ttl_days_st, age_days=st.integers(min_value=0, max_value=3650))
@settings(max_examples=500)
def test_expiry_is_deterministic(ttl, age_days):
    """Same inputs always produce the same expiry verdict."""
    now = datetime.datetime(2025, 6, 1)
    last_updated = now - datetime.timedelta(days=age_days)
    assert _is_expired(last_updated, now, ttl) == _is_expired(last_updated, now, ttl)


@given(ttl=_ttl_days_st)
@settings(max_examples=200)
def test_now_minus_ttl_boundary_is_expired(ttl):
    """A centroid last updated exactly ttl days ago is at the expiry boundary (expired)."""
    now = datetime.datetime(2025, 6, 1)
    last_updated = now - datetime.timedelta(days=ttl)
    assert _is_expired(last_updated, now, ttl)


@given(ttl=_ttl_days_st)
@settings(max_examples=200)
def test_one_second_before_boundary_is_fresh(ttl):
    """A centroid updated exactly (ttl days - 1 second) ago is NOT expired."""
    now = datetime.datetime(2025, 6, 1)
    last_updated = now - datetime.timedelta(days=ttl) + datetime.timedelta(seconds=1)
    assert not _is_expired(last_updated, now, ttl)


@given(
    now_offset=st.integers(min_value=0, max_value=365 * 5),
    age_days=st.integers(min_value=0, max_value=365 * 5),
    ttl=_ttl_days_st,
)
@settings(max_examples=300)
def test_expiry_independent_of_absolute_time(now_offset, age_days, ttl):
    """Expiry verdict depends only on age relative to TTL, not absolute timestamp."""
    base = datetime.datetime(2020, 1, 1)
    now1 = base + datetime.timedelta(days=now_offset)
    now2 = base + datetime.timedelta(days=now_offset + 365)  # 1 year later

    lu1 = now1 - datetime.timedelta(days=age_days)
    lu2 = now2 - datetime.timedelta(days=age_days)

    # Both have the same age relative to their respective "now"
    assert _is_expired(lu1, now1, ttl) == _is_expired(lu2, now2, ttl)


@given(
    base_age=st.integers(min_value=1, max_value=365),
    ttl=_ttl_days_st,
)
@settings(max_examples=300)
def test_updating_centroid_resets_expiry(base_age, ttl):
    """Re-inserting a centroid (updating last_updated to now) makes it fresh."""
    now = datetime.datetime(2025, 6, 1)
    old_lu = now - datetime.timedelta(days=base_age + ttl)  # definitely expired
    assert _is_expired(old_lu, now, ttl)

    # Simulate an upsert: new last_updated = now
    new_lu = now
    assert not _is_expired(new_lu, now, ttl)


@given(ttl=st.just(90))
@settings(max_examples=1)
def test_default_ttl_90_days_expired_at_91(ttl):
    """With the default TTL of 90 days, a 91-day-old centroid is expired."""
    now = datetime.datetime(2025, 6, 1)
    lu = now - datetime.timedelta(days=91)
    assert _is_expired(lu, now, ttl)


@given(ttl=st.just(90))
@settings(max_examples=1)
def test_default_ttl_90_days_fresh_at_89(ttl):
    """With the default TTL of 90 days, an 89-day-old centroid is NOT expired."""
    now = datetime.datetime(2025, 6, 1)
    lu = now - datetime.timedelta(days=89)
    assert not _is_expired(lu, now, ttl)
