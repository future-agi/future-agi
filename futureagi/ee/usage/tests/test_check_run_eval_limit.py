"""A fix's check runs neither meet an organization's eval limits nor count towards them."""

import pytest

from ee.usage.models.usage import APICallLog, APICallType, RateLimit
from ee.usage.utils.usage_entries import (
    check_if_api_call_is_rate_limited,
    create_organization_subscription_if_not_exists,
)
from tfc.constants.api_calls import (
    FIX_CHECK_RUN_EVAL_SOURCE,
    APICallStatusChoices,
    APICallTypeChoices,
)

pytestmark = pytest.mark.django_db

# Limited on its own, and limited together with the other eval calls.
CALL_TYPES = [
    APICallTypeChoices.TURING_LARGE_EVALUATOR.value,
    APICallTypeChoices.DATASET_EVALUATION.value,
]


@pytest.fixture(params=CALL_TYPES)
def call_type(request, organization):
    """A call type the organization may use once in a minute."""
    subscription = create_organization_subscription_if_not_exists(organization)
    for name in CALL_TYPES:
        one, _ = APICallType.objects.get_or_create(name=name)
        RateLimit.objects.update_or_create(
            api_call_type=one,
            subscription_tier=subscription.subscription_tier,
            organization=None,
            defaults={
                "minute_limit": 1,
                "hour_limit": 100,
                "day_limit": 100,
                "month_limit": 100,
            },
        )
    return APICallType.objects.get(name=request.param)


def _logged(organization, call_type, source):
    APICallLog.objects.create(
        organization=organization,
        api_call_type=call_type,
        cost=0,
        status=APICallStatusChoices.SUCCESS.value,
        source=source,
    )


def _limited(organization, call_type, source):
    return check_if_api_call_is_rate_limited(organization, call_type.name, source)[0]


def test_an_organization_at_its_limit_is_limited(organization, call_type):
    _logged(organization, call_type, "simulate")

    assert _limited(organization, call_type, "simulate") is True


def test_a_check_run_eval_passes_an_organization_at_its_limit(organization, call_type):
    _logged(organization, call_type, "simulate")

    assert _limited(organization, call_type, FIX_CHECK_RUN_EVAL_SOURCE) is False


def test_a_check_run_eval_does_not_use_the_limit_up(organization, call_type):
    _logged(organization, call_type, FIX_CHECK_RUN_EVAL_SOURCE)

    assert _limited(organization, call_type, "simulate") is False
