"""The registry's enums are StrEnum: a member reads as its value wherever it is
formatted, and still compares equal to that value."""

import pytest

from tfc.capabilities.registry import (
    FEATURE_REGISTRY,
    MANAGED_SERVICE_FEATURES,
    AirGapBehavior,
    ExecutionLocation,
    GraceBehavior,
)


@pytest.mark.parametrize(
    "member",
    [*ExecutionLocation, *GraceBehavior, *AirGapBehavior],
    ids=lambda member: f"{type(member).__name__}.{member.name}",
)
def test_a_member_formats_and_compares_as_its_value(member):
    assert member == member.value
    assert str(member) == member.value
    assert f"{member}" == member.value


def test_managed_service_features_are_the_futureagi_service_ones():
    assert MANAGED_SERVICE_FEATURES == {
        feature.id
        for feature in FEATURE_REGISTRY.values()
        if feature.execution_location.value == "futureagi_service"
    }
