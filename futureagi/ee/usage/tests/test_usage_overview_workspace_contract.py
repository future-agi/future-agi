"""DB-free regression coverage for the organization-wide usage overview contract.

The cloud billing handler lives in the private overlay. Exercise its public
query serializer and real validation decorator, replacing only the aggregation
handler with a recording mock. The cloud decorator rejects unknown fields, so
these tests also guard against losing the scope-specific error to that check.
"""

from unittest.mock import Mock

import pytest
from drf_yasg import openapi
from drf_yasg.inspectors import SwaggerAutoSchema
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.test import APIRequestFactory
from rest_framework.views import APIView

from ee.usage.serializers.contracts import UsagePeriodQuerySerializer
from tfc.utils.api_contracts import validated_api_request

PATH = "/usage/v2/usage-overview/"
WORKSPACE_ID = "8fbcb800-22d3-4ea0-b7c7-8c605482e91d"
SCOPE_ERROR = "Usage overview is organization-wide; workspace_id is not supported."


def _request(query, *, reject_unknown_fields=True):
    response = Response({"status": True, "result": {"total_estimated_cost": 42.0}})
    handler = Mock(return_value=response)

    @validated_api_request(
        query_serializer=UsagePeriodQuerySerializer,
        reject_unknown_fields=reject_unknown_fields,
        document=False,
    )
    def usage_overview(request):
        return handler(request.validated_query_data)

    request = Request(APIRequestFactory().get(PATH + query))
    return usage_overview(request), response, handler


@pytest.mark.parametrize("reject_unknown_fields", [True, False])
@pytest.mark.parametrize(
    "query",
    [
        f"?workspace_id={WORKSPACE_ID}",
        f"?period=2026-10&period_end=2026-11&workspace_id={WORKSPACE_ID}",
        "?workspace_id=",
        "?workspace_id=not-a-uuid",
        f"?workspace_id={WORKSPACE_ID}&workspace_id=",
        f"?workspace_id={WORKSPACE_ID}&unexpected=value",
    ],
)
def test_workspace_selection_returns_explicit_400_before_aggregation(
    query, reject_unknown_fields
):
    response, _, handler = _request(query, reject_unknown_fields=reject_unknown_fields)

    assert response.status_code == 400
    assert response.data["status"] is False
    assert response.data["details"]["workspace_id"] == [SCOPE_ERROR]
    handler.assert_not_called()


@pytest.mark.parametrize(
    "query, expected",
    [
        ("", {}),
        ("?period=2026-10", {"period": "2026-10"}),
        ("?period_end=2026-11", {"period_end": "2026-11"}),
        (
            "?period=2026-10&period_end=2026-11",
            {"period": "2026-10", "period_end": "2026-11"},
        ),
    ],
)
def test_organization_wide_requests_and_responses_are_unchanged(query, expected):
    response, original_response, handler = _request(query)

    assert response is original_response
    assert response.status_code == 200
    handler.assert_called_once_with(expected)


@pytest.mark.parametrize("field", ["period", "period_end", "unexpected"])
def test_existing_query_validation_still_rejects_invalid_input(field):
    response, _, handler = _request(f"?{field}=invalid")

    assert response.status_code == 400
    assert field in response.data["details"]
    handler.assert_not_called()


def test_workspace_selection_is_not_advertised_in_query_schema():
    schema = SwaggerAutoSchema(
        view=APIView(),
        path=PATH,
        method="GET",
        components=openapi.ReferenceResolver(
            openapi.SCHEMA_DEFINITIONS, force_init=True
        ),
        request=None,
        overrides={"query_serializer": UsagePeriodQuerySerializer},
    )

    parameters = schema.get_query_parameters()

    assert {parameter.name for parameter in parameters} == {"period", "period_end"}
    assert all(parameter.in_ == openapi.IN_QUERY for parameter in parameters)
    assert all(not parameter.required for parameter in parameters)
