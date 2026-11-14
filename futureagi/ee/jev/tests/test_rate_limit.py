"""Jev runs on the org's own TypeSafe key, so the legacy per-call rate limit
(50 a minute on every plan below Enterprise) never applies to it. Free plans are
still capped by credits in metering.check_usage."""

from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.requires_ee


def _recent_calls(organization, call_type, n):
    from ee.usage.models.usage import APICallLog, APICallStatusChoices, APICallType

    api_call_type = APICallType.objects.get(name=call_type)
    APICallLog.objects.bulk_create(
        [
            APICallLog(
                organization=organization,
                api_call_type=api_call_type,
                cost=0,
                status=APICallStatusChoices.SUCCESS.value,
            )
            for _ in range(n)
        ]
    )


@pytest.mark.django_db
def test_jev_has_its_own_call_type_row():
    from ee.usage.models.usage import APICallType, RateLimit

    assert APICallType.objects.filter(name="jev_evaluator").exists()
    assert not RateLimit.objects.filter(api_call_type__name="jev_evaluator").exists()


@pytest.mark.django_db
def test_jev_runs_past_the_per_minute_cap(organization):
    """60 Jev calls in the last minute: Turing Large's free and Business cap is
    50, but a Jev call is still logged as processing."""
    from ee.usage.models.usage import APICallStatusChoices
    from ee.usage.utils.usage_entries import log_and_deduct_cost_for_api_request

    _recent_calls(organization, "jev_evaluator", 60)
    row = log_and_deduct_cost_for_api_request(
        organization=organization, api_call_type="jev_evaluator", source="tracer"
    )
    assert row.status == APICallStatusChoices.PROCESSING.value
    assert row.api_call_type.name == "jev_evaluator"
    assert float(row.cost) == 0


@pytest.mark.django_db
def test_turing_large_is_still_capped_per_minute(organization):
    """The control: the same load on Turing Large is still rate limited."""
    from ee.usage.models.usage import APICallStatusChoices
    from ee.usage.utils.usage_entries import log_and_deduct_cost_for_api_request

    _recent_calls(organization, "turing_large_evaluator", 60)
    row = log_and_deduct_cost_for_api_request(
        organization=organization,
        api_call_type="turing_large_evaluator",
        source="tracer",
    )
    assert row.status == APICallStatusChoices.RATE_LIMITED.value


@pytest.mark.django_db
def test_the_jev_check_runs_no_queries(organization, django_assert_num_queries):
    from ee.usage.utils.usage_entries import check_if_api_call_is_rate_limited

    with django_assert_num_queries(0):
        assert check_if_api_call_is_rate_limited(organization, "jev_evaluator") == (
            False,
            {},
        )


def _template(model=None, config_model=None):
    return SimpleNamespace(model=model, config={"model": config_model})


@pytest.mark.parametrize(
    "model,kwargs,expected",
    [
        ("jev-latest", {}, "jev_evaluator"),
        ("jev-1.13.0", {}, "jev_evaluator"),
        # The run names no model; the saved template runs Jev.
        (None, {"template": _template(config_model="jev-latest")}, "jev_evaluator"),
        (None, {"template": _template(model="jev-1.13.0")}, "jev_evaluator"),
        (
            "turing_large",
            {
                "template": _template(model="turing_large"),
                "runtime_config": {"run_config": {"model": "jev-latest"}},
            },
            "jev_evaluator",
        ),
        # Everything else keeps the legacy mapping.
        (None, {}, "turing_large_evaluator"),
        (None, {"template": _template(model="turing_flash")}, "turing_large_evaluator"),
        ("turing_flash", {}, "turing_flash_evaluator"),
        ("gpt-5", {"template": _template(model="gpt-5")}, "turing_large_evaluator"),
    ],
)
def test_runs_are_logged_under_the_model_that_runs(model, kwargs, expected):
    from sdk.utils.helpers import _get_api_call_type

    assert _get_api_call_type(model, **kwargs) == expected
