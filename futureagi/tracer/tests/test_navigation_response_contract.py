"""Navigation responses preserve nullable neighbors under GET and read POST."""

import pytest

from tracer.serializers.trace import TraceNavigationResponseSerializer


@pytest.mark.parametrize(
    "neighbors",
    [
        {"next_trace_id": None, "previous_trace_id": None},
        {"next_trace_id": "next", "previous_trace_id": None},
        {"next_trace_id": None, "previous_trace_id": "previous"},
        {"next_trace_id": "next", "previous_trace_id": "previous"},
    ],
)
def test_navigation_response_preserves_neighbors(neighbors):
    payload = {"status": True, "result": neighbors}
    serializer = TraceNavigationResponseSerializer(data=payload)
    assert serializer.is_valid(), serializer.errors
    assert serializer.data == payload


@pytest.mark.parametrize(
    "neighbors",
    [{}, {"next_trace_id": None}, {"previous_trace_id": None}],
)
def test_navigation_response_requires_both_neighbor_fields(neighbors):
    serializer = TraceNavigationResponseSerializer(
        data={"status": True, "result": neighbors}
    )
    assert not serializer.is_valid()
