"""Tests for MCP Server rate limiter."""

import datetime
import time
from unittest.mock import MagicMock, patch

import pytest
from django.core.cache import cache

from mcp_server.constants import RATE_LIMITS
from mcp_server.exceptions import RateLimitExceededError
from mcp_server.rate_limiter import (
    _day_key,
    _next_utc_midnight,
    check_rate_limit,
    get_rate_limit_tier,
)

# Matches the spelling used across the codebase (datetime.UTC is unused here).
UTC = datetime.timezone.utc


class TestGetRateLimitTier:
    """Tests for get_rate_limit_tier()."""

    @patch("ee.usage.models.usage.OrganizationSubscription")
    def test_returns_free_when_no_subscription(self, mock_cls):
        """Returns 'free' when no OrganizationSubscription exists."""

        class _DoesNotExist(Exception):
            pass

        mock_cls.DoesNotExist = _DoesNotExist
        mock_cls.objects.select_related.return_value.get.side_effect = _DoesNotExist()
        org = MagicMock()
        assert get_rate_limit_tier(org) == "free"

    @patch("ee.usage.models.usage.OrganizationSubscription")
    def test_returns_pro_for_basic_tier(self, _mock_cls):
        """Maps 'basic' subscription tier to 'pro' rate limit tier."""
        try:
            from ee.usage.models.usage import OrganizationSubscription
        except ImportError:
            OrganizationSubscription = None

        mock_sub = MagicMock()
        mock_sub.subscription_tier.name = "basic"
        OrganizationSubscription.objects.select_related.return_value.get.return_value = (
            mock_sub
        )
        org = MagicMock()
        assert get_rate_limit_tier(org) == "pro"

    @patch("ee.usage.models.usage.OrganizationSubscription")
    def test_returns_pro_for_basic_yearly_tier(self, _mock_cls):
        """Maps 'basic_yearly' subscription tier to 'pro' rate limit tier."""
        try:
            from ee.usage.models.usage import OrganizationSubscription
        except ImportError:
            OrganizationSubscription = None

        mock_sub = MagicMock()
        mock_sub.subscription_tier.name = "basic_yearly"
        OrganizationSubscription.objects.select_related.return_value.get.return_value = (
            mock_sub
        )
        org = MagicMock()
        assert get_rate_limit_tier(org) == "pro"

    @patch("ee.usage.models.usage.OrganizationSubscription")
    def test_returns_enterprise_for_custom_tier(self, _mock_cls):
        """Maps 'custom' subscription tier to 'enterprise' rate limit tier."""
        try:
            from ee.usage.models.usage import OrganizationSubscription
        except ImportError:
            OrganizationSubscription = None

        mock_sub = MagicMock()
        mock_sub.subscription_tier.name = "custom"
        OrganizationSubscription.objects.select_related.return_value.get.return_value = (
            mock_sub
        )
        org = MagicMock()
        assert get_rate_limit_tier(org) == "enterprise"

    @patch("ee.usage.models.usage.OrganizationSubscription")
    def test_returns_free_for_unknown_tier(self, _mock_cls):
        """Falls back to 'free' for unknown tier names."""
        try:
            from ee.usage.models.usage import OrganizationSubscription
        except ImportError:
            OrganizationSubscription = None

        mock_sub = MagicMock()
        mock_sub.subscription_tier.name = "unknown_tier"
        OrganizationSubscription.objects.select_related.return_value.get.return_value = (
            mock_sub
        )
        org = MagicMock()
        assert get_rate_limit_tier(org) == "free"


class TestCheckRateLimit:
    """Tests for check_rate_limit()."""

    @patch("mcp_server.rate_limiter.cache")
    def test_allows_calls_within_limit(self, mock_cache):
        """Calls within rate limit should succeed without raising."""
        mock_cache.get.return_value = None  # No existing window / count
        # Should not raise
        check_rate_limit("org-123", "free")

    @patch("mcp_server.rate_limiter.cache")
    def test_allows_calls_up_to_limit_minus_one(self, mock_cache):
        """Calls right below the per-minute limit should succeed."""
        now = time.time()
        limit = RATE_LIMITS["free"]["per_minute"]
        timestamps = [now - i * 0.1 for i in range(limit - 1)]

        def side_effect(key, default=None):
            if "min" in key:
                return timestamps
            return 0  # day count

        mock_cache.get.side_effect = side_effect
        # Should not raise (19 < 20)
        check_rate_limit("org-123", "free")

    @patch("mcp_server.rate_limiter.cache")
    def test_raises_when_per_minute_exceeded(self, mock_cache):
        """Exceeding per-minute limit raises RateLimitExceededError."""
        now = time.time()
        limit = RATE_LIMITS["free"]["per_minute"]
        timestamps = [now - i * 0.1 for i in range(limit)]

        def side_effect(key, default=None):
            if "min" in key:
                return timestamps
            return 0  # day count

        mock_cache.get.side_effect = side_effect

        with pytest.raises(RateLimitExceededError) as exc_info:
            check_rate_limit("org-123", "free")

        assert f"{limit} calls/minute" in str(exc_info.value)
        assert exc_info.value.retry_after >= 1

    @patch("mcp_server.rate_limiter.cache")
    def test_raises_when_per_day_exceeded(self, mock_cache):
        """Exceeding per-day limit raises RateLimitExceededError."""

        def side_effect(key, default=None):
            if "min" in key:
                return []  # empty minute window
            return RATE_LIMITS["free"]["per_day"]

        mock_cache.get.side_effect = side_effect

        with pytest.raises(RateLimitExceededError) as exc_info:
            check_rate_limit("org-123", "free")

        assert f"{RATE_LIMITS['free']['per_day']} calls/day" in str(exc_info.value)
        assert exc_info.value.retry_after > 0

    @patch("mcp_server.rate_limiter.cache")
    def test_retry_after_is_reasonable_per_minute(self, mock_cache):
        """retry_after for per-minute limit should be between 1 and 61 seconds."""
        now = time.time()
        timestamps = [now - i * 0.1 for i in range(RATE_LIMITS["free"]["per_minute"])]

        def side_effect(key, default=None):
            if "min" in key:
                return timestamps
            return 0

        mock_cache.get.side_effect = side_effect

        with pytest.raises(RateLimitExceededError) as exc_info:
            check_rate_limit("org-123", "free")

        assert 1 <= exc_info.value.retry_after <= 61

    @patch("mcp_server.rate_limiter.cache")
    def test_records_call_in_cache(self, mock_cache):
        """Successful calls store the minute window and bump the day counter."""
        mock_cache.get.return_value = None

        check_rate_limit("org-123", "free")

        # The minute window is still a stored list with a 120s timeout.
        assert mock_cache.set.call_count == 1
        minute_call = mock_cache.set.call_args_list[0]
        assert "min" in minute_call[0][0]
        assert minute_call[1]["timeout"] == 120 or minute_call[0][2] == 120

        # The day counter is incremented, not re-set — re-setting it was what
        # re-armed the TTL on every call and stopped the quota ever resetting.
        assert mock_cache.incr.called
        assert "day" in mock_cache.incr.call_args[0][0]

    @patch("mcp_server.rate_limiter.cache")
    def test_pro_tier_has_higher_limits(self, mock_cache):
        """Pro tier allows more calls/minute than free."""
        now = time.time()
        # 50 timestamps (above free limit but below pro limit)
        timestamps = [now - i * 0.5 for i in range(50)]

        def side_effect(key, default=None):
            if "min" in key:
                return timestamps
            return 0

        mock_cache.get.side_effect = side_effect

        # Should not raise for pro tier (50 < 100)
        check_rate_limit("org-123", "pro")

    @patch("mcp_server.rate_limiter.cache")
    def test_expired_timestamps_are_pruned(self, mock_cache):
        """Timestamps older than 60 seconds should be pruned from the window."""
        now = time.time()
        # Mix of recent and old timestamps
        timestamps = [now - 10, now - 20, now - 70, now - 80]  # 2 recent, 2 expired

        def side_effect(key, default=None):
            if "min" in key:
                return timestamps
            return 0

        mock_cache.get.side_effect = side_effect

        # Should not raise (only 2 valid timestamps, well under limit)
        check_rate_limit("org-123", "free")

    @patch("mcp_server.rate_limiter.cache")
    def test_falls_back_to_free_for_unknown_tier(self, mock_cache):
        """Unknown tier falls back to free tier limits."""
        now = time.time()
        timestamps = [now - i * 0.1 for i in range(RATE_LIMITS["free"]["per_minute"])]

        def side_effect(key, default=None):
            if "min" in key:
                return timestamps
            return 0

        mock_cache.get.side_effect = side_effect

        # "unknown" tier falls back to free, so a full free window should raise
        with pytest.raises(RateLimitExceededError):
            check_rate_limit("org-123", "unknown")


class TestRateLimitTierOrdering:
    """The tier table must never throttle a paid tier harder than a cheaper one."""

    TIERS_CHEAPEST_FIRST = ["free", "pro", "enterprise"]

    @pytest.mark.parametrize("limit_name", ["per_minute", "per_day"])
    def test_rate_limits_are_monotonic_across_tiers(self, limit_name):
        """free <= pro <= enterprise for every numeric limit.

        Regression test: `free` shipped with per_minute=200 against `pro`'s 100,
        so every paying non-enterprise org was throttled at half the free rate.
        """
        values = [RATE_LIMITS[t][limit_name] for t in self.TIERS_CHEAPEST_FIRST]
        assert values == sorted(
            values
        ), f"{limit_name} is not monotonic across tiers: " + ", ".join(
            f"{t}={v}" for t, v in zip(self.TIERS_CHEAPEST_FIRST, values, strict=True)
        )

    def test_concurrent_sessions_monotonic_with_unlimited_last(self):
        """`concurrent_sessions` allows None (unlimited), which must sort last."""
        values = [
            RATE_LIMITS[t]["concurrent_sessions"] for t in self.TIERS_CHEAPEST_FIRST
        ]
        finite = [v for v in values if v is not None]
        assert finite == sorted(finite)
        # None may only appear as a suffix — an unlimited middle tier would mean
        # the tier above it is more restrictive.
        first_none = next((i for i, v in enumerate(values) if v is None), len(values))
        assert all(v is None for v in values[first_none:])


class TestDayKeyHelpers:
    """Pure helpers behind the daily quota — no cache, no clock mocking."""

    def test_day_key_is_stamped_with_the_utc_date(self):
        now_dt = datetime.datetime(2026, 8, 5, 13, 30, tzinfo=UTC)
        assert _day_key("org-1", now_dt) == "mcp_rl:day:org-1:2026-08-05"

    def test_day_key_changes_across_the_utc_midnight_boundary(self):
        before = datetime.datetime(2026, 8, 5, 23, 59, 59, tzinfo=UTC)
        after = datetime.datetime(2026, 8, 6, 0, 0, 0, tzinfo=UTC)
        assert _day_key("org-1", before) != _day_key("org-1", after)

    def test_day_key_is_stable_within_a_day(self):
        morning = datetime.datetime(2026, 8, 5, 0, 0, 1, tzinfo=UTC)
        evening = datetime.datetime(2026, 8, 5, 23, 59, 58, tzinfo=UTC)
        assert _day_key("org-1", morning) == _day_key("org-1", evening)

    def test_day_keys_are_per_organization(self):
        now_dt = datetime.datetime(2026, 8, 5, 13, 30, tzinfo=UTC)
        assert _day_key("org-1", now_dt) != _day_key("org-2", now_dt)

    @pytest.mark.parametrize(
        "now_dt,expected",
        [
            (
                datetime.datetime(2026, 8, 5, 0, 0, 0, tzinfo=UTC),
                datetime.datetime(2026, 8, 6, 0, 0, 0, tzinfo=UTC),
            ),
            (
                datetime.datetime(2026, 8, 5, 23, 59, 59, tzinfo=UTC),
                datetime.datetime(2026, 8, 6, 0, 0, 0, tzinfo=UTC),
            ),
            # Month rollover
            (
                datetime.datetime(2026, 8, 31, 12, 0, 0, tzinfo=UTC),
                datetime.datetime(2026, 9, 1, 0, 0, 0, tzinfo=UTC),
            ),
            # Year rollover
            (
                datetime.datetime(2026, 12, 31, 12, 0, 0, tzinfo=UTC),
                datetime.datetime(2027, 1, 1, 0, 0, 0, tzinfo=UTC),
            ),
        ],
    )
    def test_next_utc_midnight(self, now_dt, expected):
        assert _next_utc_midnight(now_dt) == expected


class TestDailyCounterAgainstRealCache:
    """Exercises the real cache backend (locmem per tfc.settings.test).

    The mocked tests above cannot observe key naming, expiry, or persistence —
    which is precisely where the daily-quota bug lived.
    """

    @pytest.fixture(autouse=True)
    def _clear_cache(self):
        cache.clear()
        yield
        cache.clear()

    def _today_key(self, org_id):
        return _day_key(org_id, datetime.datetime.now(UTC))

    def test_counter_increments_once_per_call(self):
        for expected in (1, 2, 3):
            check_rate_limit("org-real", "free")
            assert cache.get(self._today_key("org-real")) == expected

    def test_counter_is_scoped_per_organization(self):
        check_rate_limit("org-a", "free")
        check_rate_limit("org-a", "free")
        check_rate_limit("org-b", "free")

        assert cache.get(self._today_key("org-a")) == 2
        assert cache.get(self._today_key("org-b")) == 1

    def test_yesterdays_exhausted_quota_does_not_block_today(self):
        """The regression test for the quota that never reset.

        Previously the day counter lived under a single un-stamped key whose TTL
        was re-armed on every call, so an org that exhausted its daily quota
        stayed blocked indefinitely. Today's key is distinct from yesterday's, so
        a saturated yesterday is irrelevant.
        """
        now_dt = datetime.datetime.now(UTC)
        yesterday = now_dt - datetime.timedelta(days=1)
        cache.set(_day_key("org-real", yesterday), RATE_LIMITS["free"]["per_day"] * 10)

        # Must not raise — yesterday's counter is never consulted.
        check_rate_limit("org-real", "free")

        assert cache.get(self._today_key("org-real")) == 1

    def test_day_limit_still_blocks_once_todays_quota_is_spent(self):
        cache.set(self._today_key("org-real"), RATE_LIMITS["free"]["per_day"])

        with pytest.raises(RateLimitExceededError) as exc_info:
            check_rate_limit("org-real", "free")

        assert f"{RATE_LIMITS['free']['per_day']} calls/day" in str(exc_info.value)

    def test_retry_after_matches_the_actual_reset_boundary(self):
        """retry_after must point at the instant the quota really resets.

        The old code advertised seconds-until-midnight while the counter expired
        24h after the last call, so a client that honoured retry_after woke up
        still blocked.
        """
        cache.set(self._today_key("org-real"), RATE_LIMITS["free"]["per_day"])

        with pytest.raises(RateLimitExceededError) as exc_info:
            check_rate_limit("org-real", "free")

        now_dt = datetime.datetime.now(UTC)
        expected = int((_next_utc_midnight(now_dt) - now_dt).total_seconds())
        assert abs(exc_info.value.retry_after - expected) <= 2
        assert 0 < exc_info.value.retry_after <= 86400


class TestDailyCounterTTL:
    """The day key's TTL must track the midnight boundary, not a rolling 24h."""

    @patch("mcp_server.rate_limiter.cache")
    def test_first_call_of_the_day_adds_key_with_ttl_bounded_by_midnight(
        self, mock_cache
    ):
        mock_cache.get.return_value = None
        # incr raises when the key is absent — the first call of a new UTC day.
        mock_cache.incr.side_effect = ValueError("key not found")
        mock_cache.add.return_value = True

        check_rate_limit("org-123", "free")

        assert mock_cache.add.called
        key, value = mock_cache.add.call_args[0][0], mock_cache.add.call_args[0][1]
        ttl = (
            mock_cache.add.call_args[0][2]
            if len(mock_cache.add.call_args[0]) > 2
            else mock_cache.add.call_args[1]["timeout"]
        )

        assert "day" in key
        assert value == 1
        now_dt = datetime.datetime.now(UTC)
        expected = int((_next_utc_midnight(now_dt) - now_dt).total_seconds()) + 60
        assert abs(ttl - expected) <= 2
        # The bug was a flat 86400 re-armed on every call.
        assert ttl <= 86400 + 60

    @patch("mcp_server.rate_limiter.cache")
    def test_subsequent_calls_do_not_touch_the_ttl(self, mock_cache):
        """An existing key is incremented only — its expiry is left alone."""
        mock_cache.get.return_value = None
        mock_cache.incr.return_value = 2  # key exists

        check_rate_limit("org-123", "free")

        assert mock_cache.incr.called
        assert not mock_cache.add.called
        # No cache.set against the day key would re-arm its TTL.
        day_sets = [c for c in mock_cache.set.call_args_list if "day" in str(c[0][0])]
        assert day_sets == []

    @patch("mcp_server.rate_limiter.cache")
    def test_lost_add_race_falls_back_to_incr(self, mock_cache):
        """If another worker created the key first, we increment theirs."""
        mock_cache.get.return_value = None
        mock_cache.incr.side_effect = [ValueError("key not found"), 2]
        mock_cache.add.return_value = False  # someone else won the race

        check_rate_limit("org-123", "free")

        assert mock_cache.add.called
        assert mock_cache.incr.call_count == 2
