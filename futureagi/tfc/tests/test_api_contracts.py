from pathlib import Path

from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.test import APIRequestFactory
from rest_framework.views import APIView

from tfc.utils.api_contracts import validated_request


class _DemoRequestSerializer(serializers.Serializer):
    name = serializers.CharField()


class _DemoResultSerializer(serializers.Serializer):
    name = serializers.CharField()


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


class _BadResponseView(APIView):
    @validated_request(
        request_serializer=_DemoRequestSerializer,
        responses={200: _DemoResponseSerializer},
        strict_response_validation=True,
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


class _BadListResponseView(APIView):
    @validated_request(
        responses={200: _DemoResultSerializer(many=True)},
        strict_response_validation=True,
    )
    def get(self, request):
        return Response([{"wrong": "shape"}])


def _swagger():
    import json

    repo_root = Path(__file__).resolve().parents[3]
    with (repo_root / "api_contracts" / "openapi" / "swagger.json").open() as f:
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
    assert "name" in response.data


def test_validated_request_can_strictly_validate_responses():
    factory = APIRequestFactory()

    response = _BadResponseView.as_view()(factory.post("/", {"name": "Future AGI"}))

    assert response.status_code == 400
    assert "name" in response.data["result"]


def test_validated_request_can_validate_many_response_serializers():
    factory = APIRequestFactory()

    response = _ListResponseView.as_view()(factory.get("/"))

    assert response.status_code == 200
    assert response.data == [{"name": "Future AGI"}]


def test_validated_request_rejects_invalid_many_responses():
    factory = APIRequestFactory()

    response = _BadListResponseView.as_view()(factory.get("/"))

    assert response.status_code == 400
    assert "name" in response.data[0]


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
        == "#/definitions/ApiErrorResponse"
    )
