"""check_usage keeps working after Redis drops its loaded Lua scripts.

A Redis restart (or SCRIPT FLUSH) empties the script cache, while each
process still holds the SHA it loaded earlier. The hard-cap quota check must
load the script again and retry once instead of failing with NOSCRIPT.

These tests stub BillingConfig, so they do not need the private billing.yaml.
"""

import os
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from redis.exceptions import NoScriptError, ResponseError

from ee.usage.services import metering

REDIS_TEST_URL = os.environ.get("REDIS_URL", "redis://localhost:16379/0")

_ALLOWANCE = 100.0


def _stub_config():
    config = MagicMock()
    config.get_call_type.return_value = SimpleNamespace(
        dimension="ai_credits", per_call=1
    )
    config.get_plan.return_value = SimpleNamespace(usage_caps="hard")
    config.get_dimension.return_value = SimpleNamespace(
        native_to_display_divisor=1, display_name="credits", native_unit="credits"
    )
    config.get_free_allowance.return_value = _ALLOWANCE
    return config


@pytest.fixture
def free_plan(monkeypatch):
    """A free (hard-cap) org with no cached script SHA in this process."""
    monkeypatch.setattr(metering, "_lua_sha", None)
    with (
        patch.object(metering.BillingConfig, "get", return_value=_stub_config()),
        patch.object(metering, "_get_cached_billing_status", return_value="active"),
        patch.object(metering, "_get_cached_plan", return_value="free"),
    ):
        yield


@pytest.mark.unit
class TestScriptReloadUnit:
    def test_noscript_reloads_script_and_retries_once(self, free_plan):
        client = MagicMock()
        client.script_load.side_effect = ["sha-old", "sha-new"]
        client.evalsha.side_effect = [NoScriptError("No matching script"), 5]
        client.get.return_value = None

        with patch.object(metering, "get_redis", return_value=client):
            result = metering.check_usage("org-1", "turing_large_evaluator")

        assert result.allowed is True
        assert client.script_load.call_count == 2
        assert [c.args[0] for c in client.evalsha.call_args_list] == [
            "sha-old",
            "sha-new",
        ]
        assert metering._lua_sha == "sha-new"

    def test_second_noscript_propagates(self, free_plan):
        client = MagicMock()
        client.script_load.return_value = "sha"
        client.evalsha.side_effect = NoScriptError("No matching script")

        with (
            patch.object(metering, "get_redis", return_value=client),
            pytest.raises(NoScriptError),
        ):
            metering.check_usage("org-1", "turing_large_evaluator")

        assert client.evalsha.call_count == 2

    def test_other_redis_errors_are_not_retried(self, free_plan):
        client = MagicMock()
        client.script_load.return_value = "sha"
        client.evalsha.side_effect = ResponseError("WRONGTYPE")

        with (
            patch.object(metering, "get_redis", return_value=client),
            pytest.raises(ResponseError),
        ):
            metering.check_usage("org-1", "turing_large_evaluator")

        assert client.evalsha.call_count == 1
        assert client.script_load.call_count == 1


@pytest.fixture
def real_redis():
    import redis

    try:
        client = redis.Redis.from_url(REDIS_TEST_URL, decode_responses=True)
        client.ping()
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"Redis unavailable: {exc}")
    with patch.object(metering, "get_redis", return_value=client):
        yield client


@pytest.mark.integration
class TestScriptReloadRealRedis:
    def test_check_succeeds_after_script_flush(self, free_plan, real_redis):
        org_id = f"noscript-{uuid.uuid4().hex}"

        assert metering.check_usage(org_id, "turing_large_evaluator").allowed

        real_redis.script_flush()

        result = metering.check_usage(org_id, "turing_large_evaluator")
        assert result.allowed is True
        assert real_redis.script_exists(metering._lua_sha) == [True]

    def test_limit_still_enforced_after_script_flush(self, free_plan, real_redis):
        org_id = f"noscript-{uuid.uuid4().hex}"
        usage_key = f"usage:{org_id}:ai_credits:{metering._get_current_period()}"
        real_redis.set(usage_key, str(_ALLOWANCE), ex=60)
        try:
            assert not metering.check_usage(org_id, "turing_large_evaluator").allowed

            real_redis.script_flush()

            result = metering.check_usage(org_id, "turing_large_evaluator")
            assert result.allowed is False
            assert result.error_code == "FREE_TIER_LIMIT"
        finally:
            real_redis.delete(usage_key)
