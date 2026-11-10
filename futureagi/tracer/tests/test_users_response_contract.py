"""Users provenance accepts exact physical replay without inventing new values."""

import pytest

from tracer.serializers.trace import UsersResultSerializer


@pytest.mark.unit
@pytest.mark.parametrize(
    "provenance",
    [
        "physical_latest_users",
        "span_user_rollup_end_users_candidate",
        "matching_activity_walk",
        "unknown",
    ],
)
def test_users_provenance_response_contract(provenance):
    serializer = UsersResultSerializer(
        data={
            "table": [],
            "total_count": 0,
            "total_pages": 0,
            "query_provenance": provenance,
            "query_exact": False,
            "ordering_exact": False,
        }
    )
    assert serializer.is_valid() is (provenance != "unknown")
    if provenance != "unknown":
        assert serializer.validated_data["query_provenance"] == provenance
        assert serializer.validated_data["query_exact"] is False
        assert serializer.validated_data["ordering_exact"] is False


@pytest.mark.unit
@pytest.mark.parametrize("ordering", ["latest_matching_activity", "unknown", None])
def test_users_ordering_is_declared_only_for_the_matching_activity_walk(ordering):
    data = {"table": [], "total_count": 0, "total_pages": 0}
    if ordering is not None:
        data["ordering"] = ordering
    serializer = UsersResultSerializer(data=data)
    assert serializer.is_valid() is (ordering != "unknown")
    if ordering is None:
        assert "ordering" not in serializer.validated_data
    elif ordering != "unknown":
        assert serializer.validated_data["ordering"] == ordering
