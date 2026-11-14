"""check_usage when Redis no longer knows the cached quota Lua script.

After a Redis restart or SCRIPT FLUSH, the server forgets every loaded Lua
script, but the process still holds the old SHA. check_usage must recover and
return its normal allow/deny result instead of raising NoScriptError.

Redis is faked by a real ``redis.Redis`` client whose ``execute_command`` is
served from memory, including a server-side script cache that can be flushed.
That way the real redis-py command methods (evalsha, script_load,
register_script, eval) run unchanged.
"""

import hashlib
from decimal import Decimal

import pytest
import redis
from redis.exceptions import NoScriptError

from ee.usage.schemas.billing_config import BillingConfigSchema
from ee.usage.services import metering
from ee.usage.services.config import BillingConfig

ORG_ID = "org-script-cache"
PERIOD = "2026-11"
USAGE_KEY = f"usage:{ORG_ID}:ai_credits:{PERIOD}"


class InMemoryRedis(redis.Redis):
    """redis.Redis served from memory, with a flushable Lua script cache.

    Any loaded script behaves like ee/usage/lua/check_quota.lua (the only
    script check_usage runs): read-only, returns current usage or -1.
    """

    def __init__(self):
        super().__init__(decode_responses=True)
        self.store = {}
        self.scripts = {}
        self.writes = []

    def flush_scripts(self):
        """What Redis does on restart or SCRIPT FLUSH."""
        self.scripts.clear()

    def _run_quota_script(self, numkeys, keys_and_args):
        numkeys = int(numkeys)
        key = keys_and_args[0]
        limit = float(keys_and_args[numkeys])
        amount = float(keys_and_args[numkeys + 1])
        current = float(self.store.get(key) or 0)
        if limit <= 0:
            return int(current)
        if current + amount > limit:
            return -1
        return int(current)

    def execute_command(self, *args, **options):
        command = str(args[0]).upper()
        if command == "GET":
            return self.store.get(args[1])
        if command in ("SET", "SETEX", "INCRBYFLOAT", "INCRBY", "INCR"):
            self.writes.append(args)
            return True
        if command == "SCRIPT LOAD":
            body = args[1]
            if isinstance(body, str):
                body = body.encode()
            sha = hashlib.sha1(body).hexdigest()
            self.scripts[sha] = body
            return sha
        if command == "EVALSHA":
            if args[1] not in self.scripts:
                raise NoScriptError("No matching script. Please use EVAL.")
            return self._run_quota_script(args[2], args[3:])
        if command == "EVAL":
            body = args[1]
            if isinstance(body, str):
                body = body.encode()
            self.scripts[hashlib.sha1(body).hexdigest()] = body
            return self._run_quota_script(args[2], args[3:])
        raise AssertionError(f"unexpected Redis command in test: {args!r}")


def _billing_config():
    """A real BillingConfig: free plan hard-capped at 100 AI credits."""
    return BillingConfig(
        BillingConfigSchema(
            plans={
                "free": {
                    "display_name": "Free",
                    "platform_fee": Decimal("0"),
                    "usage_caps": "hard",
                },
            },
            dimensions={
                "ai_credits": {
                    "display_name": "AI Credits",
                    "native_unit": "credits",
                    "display_unit": "credits",
                    "native_to_display_divisor": 1,
                    "aggregation": "sum",
                },
            },
            pricing_tiers={},
            entitlements={"free_ai_credits": {"free": 100}},
            api_call_types={
                "turing_large_evaluator": {"dimension": "ai_credits", "per_call": 10},
            },
            ee_bands={},
            ai_credit_models={},
        )
    )


@pytest.fixture
def fake_redis(monkeypatch):
    r = InMemoryRedis()
    # Plan and billing status come from their Redis caches, as in production.
    r.store[f"plan:{ORG_ID}"] = "free"
    r.store[f"billing_status:{ORG_ID}"] = "active"
    monkeypatch.setattr(metering, "get_redis", lambda: r)
    monkeypatch.setattr(metering, "_get_current_period", lambda: PERIOD)
    monkeypatch.setattr(metering, "_lua_sha", None, raising=False)
    monkeypatch.setattr(BillingConfig, "_instance", _billing_config())
    return r


@pytest.mark.unit
class TestCheckUsageScriptCache:
    def test_recovers_when_redis_forgot_the_script(self, fake_redis):
        fake_redis.store[USAGE_KEY] = "40"

        # Warm the process: the script SHA is loaded and cached.
        first = metering.check_usage(ORG_ID, "turing_large_evaluator")
        assert first.allowed is True

        # Redis restarts / flushes scripts; the process still holds the SHA.
        fake_redis.flush_scripts()

        try:
            result = metering.check_usage(ORG_ID, "turing_large_evaluator")
        except NoScriptError as exc:
            result = exc

        assert not isinstance(result, NoScriptError), (
            "check_usage raised NoScriptError instead of reloading the script"
        )
        assert result.allowed is True
        assert result.dimension == "ai_credits"
        assert fake_redis.writes == []

    def test_over_limit_still_denied_when_script_missing_on_every_call(
        self, fake_redis
    ):
        fake_redis.store[USAGE_KEY] = "95"

        results = []
        for _ in range(3):
            fake_redis.flush_scripts()
            try:
                results.append(metering.check_usage(ORG_ID, "turing_large_evaluator"))
            except NoScriptError as exc:
                results.append(exc)

        assert not any(isinstance(r, NoScriptError) for r in results), (
            "check_usage raised NoScriptError instead of reloading the script"
        )
        for result in results:
            assert result.allowed is False
            assert result.error_code == "FREE_TIER_LIMIT"
            assert result.current_usage == 95.0
            assert result.limit == 100.0
        assert fake_redis.writes == []
