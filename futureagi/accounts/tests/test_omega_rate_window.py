"""Omega's credential limits hold under a concurrent burst (READINESS L03, SB W2-KEYS round 3).

The verifier's oracle: a 20-way parallel wrong-secret flood for one key passed about 4,300 attempts
a minute through a 1,800-a-minute limit, because DRF's throttle reads, appends and writes back its
history and concurrent requests overwrite each other's appends. These tests burst the real
throttle classes, at their default numbers, from 20, 100 and 500 threads against Redis, the store
deployed Django uses (``django_redis``), and require that exactly the limit is admitted.

The Redis tests use ``REDIS_URL``: ``tfc/settings/test.py`` defaults it to ``docker-compose.test.yml``'s
Redis 7, which backend CI starts. They skip only when ``REDIS_URL`` is not set at all; a set but
unreachable Redis fails them.
"""

import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from unittest import skipUnless
from unittest.mock import patch

from django.core.cache import cache, caches
from django.test import RequestFactory, SimpleTestCase, override_settings

from accounts import omega_rate_window
from accounts.omega_rate_window import RateLimitUnavailable, Window, parse_rate, take
from accounts.omega_throttles import (
    API_KEY_EXCHANGE_CEILING_RATE,
    API_KEY_EXCHANGE_RATE,
    OmegaApiKeyExchangeThrottle,
    OmegaObserveTargetThrottle,
)

REDIS_URL = os.environ.get("REDIS_URL")
REDIS_CACHES = {
    "default": {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": REDIS_URL or "redis://127.0.0.1:9/0",
        "OPTIONS": {
            "CLIENT_CLASS": "django_redis.client.DefaultClient",
            "SOCKET_CONNECT_TIMEOUT": 2,
            "SOCKET_TIMEOUT": 5,
            # One connection per burst thread, so the burst is concurrent at Redis too.
            "CONNECTION_POOL_KWARGS": {"max_connections": 600},
        },
    }
}
FACTORY = RequestFactory()


def attempt(api_key, source, secret="not-the-secret"):
    return FACTORY.post(
        "/accounts/omega/session-exchange/",
        HTTP_X_API_KEY=api_key,
        HTTP_X_SECRET_KEY=secret,
        REMOTE_ADDR=source,
    )


def burst(throttle_class, requests, workers):
    """Run every request through a fresh throttle on `workers` threads; outcomes in order."""

    def one(request):
        throttle = throttle_class()
        return throttle.allow_request(request, None), throttle.refused_by

    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(one, requests))


class RateWindowLockedTests(SimpleTestCase):
    """The process-locked path a non-Redis cache takes (tests, a locmem development stack)."""

    def setUp(self):
        cache.clear()

    def test_parse_rate_accepts_drf_strings_and_refuses_anything_else(self):
        self.assertEqual(parse_rate("1800/min"), (1800, 60))
        self.assertEqual(parse_rate("5/s"), (5, 1))
        self.assertEqual(parse_rate("10/hour"), (10, 3600))
        for bad in ("", "0/min", "-1/min", "ten/min", "10/fortnight", "10"):
            with self.subTest(bad=bad), self.assertRaises(Exception):
                parse_rate(bad)

    def test_a_refusal_records_nothing_in_any_window(self):
        tight, loose = Window("t:tight", 1, 60), Window("t:loose", 5, 60)
        self.assertTrue(take([tight, loose]).allowed)
        for _ in range(5):
            refused = take([tight, loose])
            self.assertFalse(refused.allowed)
            self.assertEqual(refused.refused_by, 0)
            self.assertGreaterEqual(refused.wait_seconds, 1)
        # Five refusals spent none of the loose window: four more pass on it alone.
        self.assertEqual(
            [take([loose]).allowed for _ in range(5)], [True] * 4 + [False]
        )

    def test_threads_never_pass_more_than_the_limit(self):
        requests = [attempt("pub-locked", "198.51.100.7") for _ in range(300)]
        with patch.dict(
            os.environ, {"OMEGA_SESSION_EXCHANGE_KEY_THROTTLE_RATE": "120/min"}
        ):
            outcomes = burst(OmegaApiKeyExchangeThrottle, requests, 50)
        self.assertEqual(sum(allowed for allowed, _ in outcomes), 120)


@skipUnless(REDIS_URL, "REDIS_URL is not set (scripts/django-test.sh sets it)")
@override_settings(CACHES=REDIS_CACHES)
class RateWindowRedisTests(SimpleTestCase):
    """The atomic script on a real Redis, at the default numbers."""

    def setUp(self):
        # A set but unreachable Redis is a failure here, never a skip.
        caches["default"].get("omega-rate-window-test-ping")
        self.api_key = f"pub-{uuid.uuid4().hex}"

    def test_the_cache_under_test_is_redis(self):
        self.assertIsNotNone(omega_rate_window._redis_client())

    def test_20_100_and_500_way_bursts_admit_exactly_the_per_source_limit(self):
        limit, _ = parse_rate(API_KEY_EXCHANGE_RATE)
        for workers in (20, 100, 500):
            with self.subTest(workers=workers):
                api_key = f"{self.api_key}-{workers}"
                requests = [
                    attempt(api_key, "198.51.100.7") for _ in range(limit + 200)
                ]
                outcomes = burst(OmegaApiKeyExchangeThrottle, requests, workers)
                admitted = sum(allowed for allowed, _ in outcomes)
                self.assertEqual(
                    admitted, limit, f"{workers} threads admitted {admitted}"
                )
                self.assertEqual({by for allowed, by in outcomes if not allowed}, {0})

    def test_a_500_way_burst_from_many_sources_admits_exactly_the_key_ceiling(self):
        per_source, _ = parse_rate(API_KEY_EXCHANGE_RATE)
        ceiling, _ = parse_rate(API_KEY_EXCHANGE_CEILING_RATE)
        sources = [
            f"203.0.113.{n}" for n in range(1, 13)
        ]  # 12 sources x 1,800 > 18,000
        requests = [
            attempt(self.api_key, s) for _ in range(per_source) for s in sources
        ]
        outcomes = burst(OmegaApiKeyExchangeThrottle, requests, 500)
        self.assertEqual(sum(allowed for allowed, _ in outcomes), ceiling)
        admitted_by_source = {}
        for request, (allowed, _) in zip(requests, outcomes):
            admitted_by_source[request.META["REMOTE_ADDR"]] = admitted_by_source.get(
                request.META["REMOTE_ADDR"], 0
            ) + int(allowed)
        self.assertLessEqual(max(admitted_by_source.values()), per_source)
        # Once the ceiling is spent, a source that has sent nothing is refused by the ceiling.
        fresh = OmegaApiKeyExchangeThrottle()
        self.assertFalse(
            fresh.allow_request(attempt(self.api_key, "192.0.2.200"), None)
        )
        self.assertEqual(fresh.refused_by, 1)
        self.assertGreaterEqual(fresh.wait(), 1)

    def test_a_flooded_source_leaves_the_owner_another_source(self):
        per_source, _ = parse_rate(API_KEY_EXCHANGE_RATE)
        flood = [
            attempt(self.api_key, "198.51.100.66") for _ in range(per_source + 500)
        ]
        outcomes = burst(OmegaApiKeyExchangeThrottle, flood, 100)
        self.assertEqual(sum(allowed for allowed, _ in outcomes), per_source)
        same = OmegaApiKeyExchangeThrottle()
        self.assertFalse(
            same.allow_request(attempt(self.api_key, "198.51.100.66", "right"), None)
        )
        self.assertEqual(same.refused_by, 0)
        other = OmegaApiKeyExchangeThrottle()
        self.assertTrue(
            other.allow_request(attempt(self.api_key, "192.0.2.10", "right"), None)
        )

    def test_observe_target_holds_its_limit_under_a_burst_too(self):
        with patch.dict(os.environ, {"OMEGA_OBSERVE_TARGET_THROTTLE_RATE": "60/min"}):
            requests = [attempt(self.api_key, "198.51.100.7") for _ in range(400)]
            outcomes = burst(OmegaObserveTargetThrottle, requests, 100)
        self.assertEqual(sum(allowed for allowed, _ in outcomes), 60)

    def test_retry_after_is_when_the_oldest_admitted_attempt_leaves_the_window(self):
        window = Window(omega_rate_window.window_key("test", self.api_key), 2, 60)
        self.assertTrue(take([window]).allowed)
        self.assertTrue(take([window]).allowed)
        refused = take([window])
        self.assertFalse(refused.allowed)
        self.assertTrue(55 <= refused.wait_seconds <= 60, refused.wait_seconds)

    def test_a_store_failure_refuses_with_503_and_retry_after(self):
        class Broken:
            def register_script(self, script):
                raise ConnectionError("redis is down")

        with patch.object(omega_rate_window, "_redis_client", return_value=Broken()):
            with self.assertRaises(RateLimitUnavailable) as raised:
                take([Window("t:any", 1, 60)])
        self.assertEqual(raised.exception.status_code, 503)
        self.assertEqual(raised.exception.wait, 2)
