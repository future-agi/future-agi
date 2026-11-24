from inspect import getsource, unwrap
from types import SimpleNamespace
from uuid import UUID

import pytest
from rest_framework import status
from rest_framework.test import APIRequestFactory, force_authenticate

from simulate.serializers.requests.run_test import (
    PromptSimulationListQuerySerializer,
    RunTestFilterSerializer,
)
from simulate.views.prompt_simulation import PromptSimulationListCreateView
from simulate.views.run_test import RunTestAPIView, RunTestListView

# Fixed, not uuid4(): this value is part of the pytest ids. Every CI shard
# collects the tests in its own process, and pytest-split runs each case
# exactly once only when all shards see the same ids.
PROMPT_TEMPLATE_ID = UUID("00000000-0000-4000-8000-000000000001")


@pytest.mark.parametrize(
    "serializer_class",
    [RunTestFilterSerializer, PromptSimulationListQuerySerializer],
)
def test_simulation_list_query_serializers_allow_page_and_limit_at_hard_cap(
    serializer_class,
):
    serializer = serializer_class(data={"page": "100", "limit": "100"})

    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data["page"] == 100
    assert serializer.validated_data["limit"] == 100


@pytest.mark.parametrize(
    ("serializer_class", "field", "value"),
    [
        (serializer_class, field, value)
        for serializer_class in (
            RunTestFilterSerializer,
            PromptSimulationListQuerySerializer,
        )
        for field in ("page", "limit")
        for value in ("0", "101", "not-an-integer")
    ],
)
def test_simulation_list_query_serializers_reject_invalid_or_over_cap_values(
    serializer_class, field, value
):
    serializer = serializer_class(data={field: value})

    assert not serializer.is_valid()
    assert field in serializer.errors


def test_prompt_simulation_list_uses_validated_query_values():
    source = getsource(unwrap(PromptSimulationListCreateView.get))

    assert "request.validated_query_data" in source
    assert 'request.query_params.get("limit"' not in source
    assert 'request.query_params.get("page"' not in source


@pytest.mark.parametrize(
    ("view_class", "path", "view_kwargs"),
    [
        (RunTestListView, "/simulate/run-tests/", {}),
        (RunTestAPIView, "/simulate/api/run-tests/", {}),
        (
            PromptSimulationListCreateView,
            f"/simulate/prompt-templates/{PROMPT_TEMPLATE_ID}/simulations/",
            {"prompt_template_id": PROMPT_TEMPLATE_ID},
        ),
    ],
)
@pytest.mark.parametrize(
    ("field", "value"),
    [
        (field, value)
        for field in ("page", "limit")
        for value in (0, 101, "not-an-integer")
    ],
)
def test_simulation_list_views_return_400_before_database_work_for_invalid_values(
    view_class, path, view_kwargs, field, value
):
    request = APIRequestFactory().get(path, {field: value})
    force_authenticate(
        request,
        user=SimpleNamespace(is_authenticated=True),
    )

    response = view_class.as_view()(request, **view_kwargs)

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert field in response.data["details"]
