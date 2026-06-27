from pathlib import Path
from types import SimpleNamespace

from rest_framework import serializers
from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework.test import APIRequestFactory
from rest_framework.views import APIView

from tfc.utils.api_contracts import validated_api_request, validated_request
from tfc.utils.api_errors import build_error_envelope
from tfc.utils.api_serializers import EmptyRequestSerializer
from tfc.utils.general_methods import GeneralMethods


class _DemoRequestSerializer(serializers.Serializer):
    name = serializers.CharField()


class _DemoResultSerializer(serializers.Serializer):
    name = serializers.CharField()


class _DemoQuerySerializer(serializers.Serializer):
    page = serializers.IntegerField(min_value=1)


class _FrameworkQuerySerializer(serializers.Serializer):
    status = serializers.CharField(required=False, allow_blank=True)


class _DemoResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = _DemoResultSerializer()


class _DemoView(APIView):
    @validated_request(
        request_serializer=_DemoRequestSerializer,
        responses={200: _DemoResponseSerializer},
        strict_response_validation=True,
    )
    def post(self, request):
        return Response(
            {"status": True, "result": {"name": request.validated_data["name"]}}
        )


class _StrictRequestView(APIView):
    @validated_request(
        request_serializer=_DemoRequestSerializer,
        reject_unknown_fields=True,
    )
    def post(self, request):
        return Response({"status": True, "result": request.validated_data})


class _StrictEmptyRequestView(APIView):
    @validated_request(
        request_serializer=EmptyRequestSerializer,
        reject_unknown_fields=True,
    )
    def post(self, request):
        return Response({"status": True, "result": request.validated_data})


class _SerializerAccessView(APIView):
    @validated_request(
        request_serializer=_DemoRequestSerializer,
        query_serializer=_DemoQuerySerializer,
    )
    def post(self, request):
        return Response(
            {
                "request_serializer": type(request.validated_serializer).__name__,
                "query_serializer": type(request.validated_query_serializer).__name__,
            }
        )


class _SerializerContextView(APIView):
    @validated_request(
        request_serializer=_DemoRequestSerializer,
        serializer_context=lambda request, item_id: {"item_id": item_id},
    )
    def post(self, request, item_id):
        return Response(
            {
                "item_id": request.validated_serializer.context["item_id"],
                "name": request.validated_data["name"],
            }
        )


class _PartialRequestView(APIView):
    @validated_request(
        request_serializer=_DemoRequestSerializer,
        partial_request_validation=True,
        reject_unknown_fields=True,
    )
    def patch(self, request):
        return Response({"status": True, "result": request.validated_data})


class _BadResponseView(APIView):
    @validated_request(
        request_serializer=_DemoRequestSerializer,
        responses={200: _DemoResponseSerializer},
        strict_response_validation=True,
    )
    def post(self, request):
        return Response({"status": True, "result": {"wrong": "shape"}})


class _NonStrictBadResponseView(APIView):
    @validated_request(
        request_serializer=_DemoRequestSerializer,
        responses={200: _DemoResponseSerializer},
    )
    def post(self, request):
        return Response({"status": True, "result": {"wrong": "shape"}})


class _ListResponseView(APIView):
    @validated_request(
        responses={200: _DemoResultSerializer(many=True)},
        strict_response_validation=True,
    )
    def get(self, request):
        return Response([{"name": "Future AGI"}])


class _QueryView(APIView):
    @validated_request(query_serializer=_DemoQuerySerializer)
    def get(self, request):
        return Response({"page": request.validated_query_data["page"]})


class _FrameworkQueryView(APIView):
    @validated_request(
        query_serializer=_FrameworkQuerySerializer,
        reject_unknown_fields=True,
        framework_query_params=("page", "limit"),
    )
    def get(self, request):
        return Response(request.validated_query_data)


class _BadListResponseView(APIView):
    @validated_request(
        responses={200: _DemoResultSerializer(many=True)},
        strict_response_validation=True,
    )
    def get(self, request):
        return Response([{"wrong": "shape"}])


@api_view(["POST"])
@validated_request(
    request_serializer=_DemoRequestSerializer,
    responses={200: _DemoResponseSerializer},
    reject_unknown_fields=True,
)
def _demo_function_view(request):
    return Response(
        {"status": True, "result": {"name": request.validated_data["name"]}}
    )


@api_view(["GET", "POST"])
@validated_api_request(
    request_serializer=_DemoRequestSerializer,
    request_methods=["POST"],
    reject_unknown_fields=True,
    document=False,
)
def _demo_method_scoped_function_view(request):
    return Response(
        {"method": request.method, "validated_data": request.validated_data}
    )


def _swagger():
    import json

    repo_root = Path(__file__).resolve().parents[3]
    with (repo_root / "api_contracts" / "openapi" / "swagger.json").open() as f:
        return json.load(f)


def _debt_report():
    import json

    repo_root = Path(__file__).resolve().parents[3]
    with (
        repo_root
        / "api_contracts"
        / "openapi"
        / "management-api-contract-debt.generated.json"
    ).open() as f:
        return json.load(f)


def _body_ref(path, method):
    body_param = next(
        parameter
        for parameter in _swagger()["paths"][path][method]["parameters"]
        if parameter.get("in") == "body"
    )
    return body_param["schema"].get("$ref")


def _response_ref(path, method, status_code="200"):
    response = _swagger()["paths"][path][method]["responses"][status_code]
    return response.get("schema", {}).get("$ref")


def test_validated_request_uses_declared_serializer_at_runtime():
    factory = APIRequestFactory()

    response = _DemoView.as_view()(factory.post("/", {"name": "Future AGI"}))

    assert response.status_code == 200
    assert response.data == {"status": True, "result": {"name": "Future AGI"}}


def test_validated_request_rejects_invalid_body():
    factory = APIRequestFactory()

    response = _DemoView.as_view()(factory.post("/", {"wrong": "shape"}))

    assert response.status_code == 400
    assert response.data["status"] is False
    assert response.data["result"] == "name: This field is required."
    assert response.data["message"] == "name: This field is required."
    assert response.data["details"] == {"name": ["This field is required."]}


def test_validated_request_can_reject_unknown_body_fields():
    factory = APIRequestFactory()

    response = _StrictRequestView.as_view()(
        factory.post("/", {"name": "Future AGI", "displayName": "Future AGI"})
    )

    assert response.status_code == 400
    assert response.data["status"] is False
    assert response.data["message"] == "displayName: Unknown field."
    assert response.data["details"] == {"displayName": ["Unknown field."]}


def test_validated_request_rejects_non_empty_empty_request_serializer():
    factory = APIRequestFactory()

    response = _StrictEmptyRequestView.as_view()(
        factory.post("/", {"legacy_extra": "Future AGI"}, format="json")
    )

    assert response.status_code == 400
    assert response.data["status"] is False
    assert response.data["details"] == {
        "non_field_errors": ["This endpoint does not accept a request body."],
        "legacy_extra": ["Unknown field."],
    }


def test_validated_request_exposes_validated_serializers_to_views():
    factory = APIRequestFactory()

    response = _SerializerAccessView.as_view()(
        factory.post("/?page=1", {"name": "Future AGI"})
    )

    assert response.status_code == 200
    assert response.data == {
        "request_serializer": "_DemoRequestSerializer",
        "query_serializer": "_DemoQuerySerializer",
    }


def test_validated_request_passes_view_kwargs_to_serializer_context():
    factory = APIRequestFactory()

    response = _SerializerContextView.as_view()(
        factory.post("/", {"name": "Future AGI"}),
        item_id="item-123",
    )

    assert response.status_code == 200
    assert response.data == {"item_id": "item-123", "name": "Future AGI"}


def test_validated_request_supports_partial_body_validation():
    factory = APIRequestFactory()

    response = _PartialRequestView.as_view()(factory.patch("/", {}, format="json"))
    unknown_response = _PartialRequestView.as_view()(
        factory.patch("/", {"displayName": "Future AGI"}, format="json")
    )

    assert response.status_code == 200
    assert response.data == {"status": True, "result": {}}
    assert unknown_response.status_code == 400
    assert unknown_response.data["details"] == {"displayName": ["Unknown field."]}


def test_validated_request_reports_unknown_and_normal_validation_errors():
    factory = APIRequestFactory()

    response = _StrictRequestView.as_view()(
        factory.post("/", {"displayName": "Future AGI"})
    )

    assert response.status_code == 400
    assert response.data["status"] is False
    assert response.data["details"] == {
        "name": ["This field is required."],
        "displayName": ["Unknown field."],
    }


def test_validated_request_rejects_invalid_query_with_error_envelope():
    factory = APIRequestFactory()

    response = _QueryView.as_view()(factory.get("/", {"page": "zero"}))

    assert response.status_code == 400
    assert response.data["status"] is False
    assert response.data["result"] == "page: A valid integer is required."
    assert response.data["message"] == "page: A valid integer is required."
    assert response.data["details"] == {"page": ["A valid integer is required."]}


def test_validated_request_allows_declared_framework_query_params():
    factory = APIRequestFactory()

    response = _FrameworkQueryView.as_view()(
        factory.get("/", {"status": "active", "page": "1", "limit": "10"})
    )
    unknown_response = _FrameworkQueryView.as_view()(
        factory.get("/", {"status": "active", "legacyStatus": "active"})
    )

    assert response.status_code == 200
    assert response.data == {"status": "active"}
    assert unknown_response.status_code == 400
    assert unknown_response.data["details"] == {"legacyStatus": ["Unknown field."]}


def test_validated_request_supports_function_based_views():
    factory = APIRequestFactory()

    response = _demo_function_view(factory.post("/", {"name": "Future AGI"}))

    assert response.status_code == 200
    assert response.data == {"status": True, "result": {"name": "Future AGI"}}


def test_validated_request_function_based_views_reject_unknown_fields():
    factory = APIRequestFactory()

    response = _demo_function_view(
        factory.post("/", {"name": "Future AGI", "displayName": "Future AGI"})
    )

    assert response.status_code == 400
    assert response.data["details"] == {"displayName": ["Unknown field."]}


def test_validated_api_request_can_scope_body_validation_by_method():
    factory = APIRequestFactory()

    get_response = _demo_method_scoped_function_view(factory.get("/"))
    post_response = _demo_method_scoped_function_view(
        factory.post("/", {"name": "Future AGI", "displayName": "Future AGI"})
    )

    assert get_response.status_code == 200
    assert get_response.data == {"method": "GET", "validated_data": {}}
    assert post_response.status_code == 400
    assert post_response.data["details"] == {"displayName": ["Unknown field."]}


def test_validated_request_can_strictly_validate_responses():
    factory = APIRequestFactory()

    response = _BadResponseView.as_view()(factory.post("/", {"name": "Future AGI"}))

    assert response.status_code == 400
    assert "name" in response.data["result"]


def test_validated_request_leaves_response_validation_non_strict_by_default():
    factory = APIRequestFactory()

    response = _NonStrictBadResponseView.as_view()(
        factory.post("/", {"name": "Future AGI"})
    )

    assert response.status_code == 200
    assert response.data == {"status": True, "result": {"wrong": "shape"}}


def test_validated_request_can_validate_many_response_serializers():
    factory = APIRequestFactory()

    response = _ListResponseView.as_view()(factory.get("/"))

    assert response.status_code == 200
    assert response.data == [{"name": "Future AGI"}]


def test_validated_request_rejects_invalid_many_responses():
    factory = APIRequestFactory()

    response = _BadListResponseView.as_view()(factory.get("/"))

    assert response.status_code == 400
    assert response.data["status"] is False
    assert response.data["code"] == "required"
    assert response.data["type"] == "validation_error"
    assert response.data["attr"] == "0.name"
    assert response.data["detail"] == "name: This field is required."
    assert response.data["message"] == "name: This field is required."
    assert response.data["details"] == {"0.name": ["This field is required."]}


def test_error_envelope_preserves_display_alias_and_extra_context():
    response = build_error_envelope(
        "Too many requests",
        status_code=429,
        extra={"retry_after": 30},
    )

    assert response["status"] is False
    assert response["type"] == "rate_limit"
    assert response["code"] == "rate_limited"
    assert response["detail"] == "Too many requests"
    assert response["message"] == "Too many requests"
    assert response["error"] == "Too many requests"
    assert response["result"] == "Too many requests"
    assert response["retry_after"] == 30


def test_error_envelope_preserves_details_attr_and_extra_metadata():
    response = build_error_envelope(
        {"field": ["Invalid value."]},
        status_code=400,
        details={"field": ["Invalid value."]},
        extra={"request_id": "req_123"},
    )

    assert response["status"] is False
    assert response["type"] == "validation_error"
    assert response["code"] == "invalid"
    assert response["detail"] == "field: Invalid value."
    assert response["message"] == "field: Invalid value."
    assert response["error"] == "field: Invalid value."
    assert response["result"] == "field: Invalid value."
    assert response["attr"] == "field"
    assert response["details"] == {"field": ["Invalid value."]}
    assert response["request_id"] == "req_123"


def test_bad_request_preserves_structured_error_code_result():
    response = GeneralMethods().bad_request(
        {
            "error": "Invalid credentials",
            "error_code": "LOGIN_INVALID_CREDENTIALS",
            "remaining_attempts": 4,
        }
    )

    assert response.status_code == 400
    assert response.data["status"] is False
    assert response.data["code"] == "LOGIN_INVALID_CREDENTIALS"
    assert response.data["detail"] == "Invalid credentials"
    assert response.data["result"]["error_code"] == "LOGIN_INVALID_CREDENTIALS"
    assert response.data["result"]["remaining_attempts"] == 4


def test_not_found_preserves_structured_error_metadata_result():
    response = GeneralMethods().not_found(
        {
            "message": "Some graphs were not found.",
            "missing_ids": ["graph-id"],
        }
    )

    assert response.status_code == 404
    assert response.data["status"] is False
    assert response.data["code"] == "not_found"
    assert response.data["detail"] == "Some graphs were not found."
    assert response.data["result"] == {
        "message": "Some graphs were not found.",
        "missing_ids": ["graph-id"],
    }


def test_usage_limit_response_uses_common_error_envelope():
    response = GeneralMethods().usage_limit_response(
        SimpleNamespace(
            reason="AI credit limit reached",
            error_code="AI_CREDIT_LIMIT",
            dimension="ai_credits",
            current_usage=101,
            limit=100,
            upgrade_cta=None,
        )
    )

    assert response.status_code == 402
    assert response.data["status"] is False
    assert response.data["type"] == "payment_required"
    assert response.data["code"] == "AI_CREDIT_LIMIT"
    assert response.data["detail"] == "AI credit limit reached"
    assert response.data["message"] == "AI credit limit reached"
    assert response.data["error"] == "AI credit limit reached"
    assert response.data["result"] == "AI credit limit reached"
    assert response.data["error_code"] == "AI_CREDIT_LIMIT"
    assert response.data["dimension"] == "ai_credits"
    assert response.data["current_usage"] == 101
    assert response.data["limit"] == 100


def test_core_management_endpoints_have_runtime_backed_contracts():
    expected_responses = {
        ("/health/", "get"): "#/definitions/HealthCheckResponse",
        ("/api/deployment-info/", "get"): "#/definitions/DeploymentInfoResponse",
        ("/api/public/health", "get"): "#/definitions/LangfuseHealthResponse",
        ("/api/public/traces", "get"): "#/definitions/LangfuseTracesResponse",
        ("/call-websocket/", "post"): "#/definitions/CallWebsocketResponse",
    }
    for (path, method), expected_ref in expected_responses.items():
        assert _response_ref(path, method) == expected_ref

    assert _body_ref("/call-websocket/", "post") == "#/definitions/CallWebsocketRequest"
    assert (
        _response_ref("/call-websocket/", "post", "400")
        == "#/definitions/CallWebsocketErrorResponse"
    )


def test_management_api_auto_schema_adds_default_error_contracts():
    assert (
        _response_ref("/model-hub/annotation-queues/", "get", "default")
        == "#/definitions/ManagementAPIErrorResponse"
    )

    assert (
        _response_ref("/call-websocket/", "post", "400")
        == "#/definitions/CallWebsocketErrorResponse"
    )
    assert (
        _response_ref("/call-websocket/", "post", "default")
        == "#/definitions/ManagementAPIErrorResponse"
    )


def test_management_api_has_no_missing_error_response_contract_debt():
    report = _debt_report()

    assert report["summary"]["operations_without_error_response_schema"] == 0
