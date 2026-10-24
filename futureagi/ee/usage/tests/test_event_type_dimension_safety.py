"""`BillingConfig.event_type_dimension` never raises, and managed spend is prepaid.

An emitter of `gateway_managed_spend` events asks which dimension the event
maps to before it emits one, so any failure to load billing.yaml must answer
None (hold the event back) instead of raising on
the money path. Managed spend is paid from prepaid credits, so the dimension
it maps to must be a `settlement: prepaid` one, or usage would bill it twice.
"""

from unittest.mock import patch

import pytest
from django.test import override_settings
from structlog.testing import capture_logs

from ee.usage.schemas.billing_config import BillingConfigSchema
from ee.usage.schemas.event_types import BillingEventType
from ee.usage.services.config import BillingConfig

MANAGED_SPEND = BillingEventType.GATEWAY_MANAGED_SPEND.value


@pytest.fixture
def billing_singleton():
    """Swap the BillingConfig singleton for a test and restore it after."""
    saved = BillingConfig._instance
    BillingConfig._instance = None
    yield
    BillingConfig._instance = saved


def _managed_spend_config():
    """A billing.yaml mapping for managed spend: a prepaid dimension."""
    return BillingConfig(
        BillingConfigSchema(
            plans={},
            dimensions={
                "managed_spend": {
                    "display_name": "Gateway managed models",
                    "native_unit": "usd_micros",
                    "display_unit": "USD",
                    "native_to_display_divisor": 1_000_000,
                    "aggregation": "sum",
                    "settlement": "prepaid",
                }
            },
            pricing_tiers={},
            entitlements={},
            api_call_types={
                MANAGED_SPEND: {
                    "dimension": "managed_spend",
                    "amount_from": "billed_micros",
                }
            },
            ee_bands={},
            ai_credit_models={},
        )
    )


def _decode_error():
    return UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")


class TestEventTypeDimensionNeverRaises:
    @pytest.mark.parametrize(
        "error",
        [
            _decode_error(),
            RecursionError("maximum recursion depth exceeded"),
            ValueError("boom"),
        ],
        ids=["unicode_decode_error", "recursion_error", "generic_error"],
    )
    def test_event_type_dimension_returns_none_on_any_exception(
        self, billing_singleton, error
    ):
        with (
            patch.object(BillingConfig, "_load", side_effect=error),
            capture_logs() as logs,
        ):
            assert BillingConfig.event_type_dimension(MANAGED_SPEND) is None

        assert [log["event"] for log in logs] == [
            "billing_config_unavailable_for_event_type"
        ]
        assert logs[0]["event_type"] == MANAGED_SPEND
        assert BillingConfig._instance is None

    def test_a_billing_yaml_that_is_not_utf8_returns_none(
        self, billing_singleton, tmp_path
    ):
        # Reading the file raises UnicodeDecodeError, which YAML does not wrap.
        bad = tmp_path / "billing.yaml"
        bad.write_bytes(b"plans:\n  free: \xff\xfe\n")

        with override_settings(BILLING_CONFIG_PATH=str(bad)):
            assert BillingConfig.event_type_dimension(MANAGED_SPEND) is None


class TestManagedSpendIsPrepaid:
    def test_gateway_managed_spend_maps_to_prepaid_dimension(
        self, billing_singleton, capsys
    ):
        loaded = BillingConfig.event_type_dimension(MANAGED_SPEND)

        if loaded is None:
            # The loaded billing.yaml (or the empty OSS default) has no mapping: pin
            # the rule on a fixture config, and say so in the run's output
            # (pytest.ini disables the warnings plugin, so this prints).
            with capsys.disabled():
                print(
                    f"\nNOTE: {MANAGED_SPEND} is not mapped in the loaded billing "
                    "config; asserting the prepaid rule on a fixture config instead."
                )
            config = _managed_spend_config()
            BillingConfig._instance = config
            dimension = BillingConfig.event_type_dimension(MANAGED_SPEND)
        else:
            config, dimension = BillingConfig.get(), loaded

        assert dimension is not None
        assert config.dimension_settlement(dimension) == "prepaid"
