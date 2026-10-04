"""Regression guard for the Management API OpenAPI document (TH-5323).

Three independent layers, so a failure says which class of problem occurred:

* contract validity: the committed ``api_contracts/openapi/swagger.json`` has
  unique operationIds and passes strict Swagger 2.0 validation;
* generator rule: the collision-aware generator renames only the losing member
  of an operationId collision, deterministically;
* runtime availability: ``GET /docs/?format=openapi`` through the HTTP stack
  returns a parseable document whose advertised host follows the request or
  the configured public base URL, never a hardcoded ``localhost:8000``.
"""

import json
import sys
from pathlib import Path

import pytest
from django.test import override_settings
from django.urls import path
from drf_yasg import openapi
from drf_yasg.generators import OpenAPISchemaGenerator
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.viewsets import ViewSet

from tfc.utils import openapi_contract
from tfc.utils.api_contracts import ManagementAPISchemaGenerator
from tfc.utils.openapi_contract import (
    disambiguated_operation_id,
    find_duplicate_operation_ids,
    normalize_public_base_url,
    plan_operation_id_renames,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
COMMITTED_CONTRACT = REPO_ROOT / "api_contracts" / "openapi" / "swagger.json"
SCHEMA_URL = "/docs/?format=openapi"
OPENAPI_JSON = "application/openapi+json"

pytestmark = pytest.mark.unit


class _ThingView(APIView):
    """Collection and detail routes bound to one APIView: the shape behind every
    operationId collision TH-5323 found (same keys, so same generated ID)."""

    def get(self, request, thing_id=None):
        return Response({})

    def post(self, request, thing_id=None):
        return Response({})


class _AliasViewSet(ViewSet):
    @action(detail=False, methods=["get"])
    def supported_models(self, request):
        return Response([])


def _fixture_patterns():
    return [
        path("things/", _ThingView.as_view()),
        path("things/<uuid:thing_id>/", _ThingView.as_view()),
        path("widgets/supported-models", _AliasViewSet.as_view({"get": "supported_models"})),
        path("widgets/supported_models/", _AliasViewSet.as_view({"get": "supported_models"})),
    ]


def _generate(generator_class):
    info = openapi.Info(title="fixture", default_version="v1")
    generator = generator_class(info, patterns=_fixture_patterns())
    return json.loads(json.dumps(generator.get_schema(request=None, public=True)))


def _operation_ids(document):
    return {
        (method, route): operation["operationId"]
        for route, item in document["paths"].items()
        for method, operation in item.items()
        if method in ("get", "post")
    }


# --- pure rules -----------------------------------------------------------------


def test_duplicate_checker_reports_every_group_with_all_members():
    document = {
        "paths": {
            "/a/": {"post": {"operationId": "a_create"}, "get": {"operationId": "a_list"}},
            "/a/{id}/": {"post": {"operationId": "a_create"}, "get": {"operationId": "a_read"}},
            "/b/": {"delete": {"operationId": "b_delete"}},
            "/b/{x}/": {"delete": {"operationId": "b_delete"}},
            "/b/{x}/{y}/": {"delete": {"operationId": "b_delete"}},
        }
    }
    duplicates = find_duplicate_operation_ids(document)
    assert list(duplicates) == ["a_create", "b_delete"]
    assert duplicates["b_delete"] == [
        {"method": "delete", "path": "/b/"},
        {"method": "delete", "path": "/b/{x}/"},
        {"method": "delete", "path": "/b/{x}/{y}/"},
    ]


def test_rename_plan_keeps_collection_route_and_names_losers_after_their_parameters():
    document = {
        "paths": {
            "/b/{x}/{y}/": {"delete": {"operationId": "b_delete"}},
            "/b/{x}/": {"delete": {"operationId": "b_delete"}},
            "/b/": {"delete": {"operationId": "b_delete"}},
            "/kb/supported_models/": {"get": {"operationId": "kb_supported_models"}},
            "/kb/supported-models": {"get": {"operationId": "kb_supported_models"}},
        }
    }
    renames = plan_operation_id_renames(document)
    assert renames == {
        ("/b/{x}/", "delete"): "b_delete_by_x",
        ("/b/{x}/{y}/", "delete"): "b_delete_by_x_y",
        # The underscore spelling contains every named segment of the hyphenated
        # alias, so it is the route the router always mounts and keeps the
        # historic ID. The shorter hyphenated alias is the loser. (Review
        # finding 2: shorter-path-wins renamed the router path instead.)
        ("/kb/supported-models", "get"): "kb_supported_models_supported-models",
    }
    # Pattern order must not matter: the plan is a function of the route set.
    reordered = {"paths": dict(reversed(list(document["paths"].items())))}
    assert dict(plan_operation_id_renames(reordered)) == dict(renames)


def test_contract_helpers_import_without_django():
    # The helpers are stdlib-only so scripts/check_openapi_contract.py can use
    # them outside Django. pytest-django has already imported Django here, so
    # prove the module's own imports instead of the process state.
    import ast
    source = (Path(openapi_contract.__file__)).read_text()
    tree = ast.parse(source)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert "django" not in imported
    assert "drf_yasg" not in imported


def test_rename_plan_never_reuses_an_existing_operation_id():
    document = {
        "paths": {
            "/c/": {"post": {"operationId": "c_create"}},
            "/c/{id}/": {"post": {"operationId": "c_create"}},
            "/c/by-id/": {"post": {"operationId": "c_create_by_id"}},
        }
    }
    assert plan_operation_id_renames(document) == {("/c/{id}/", "post"): "c_create_by_id_2"}


@pytest.mark.parametrize(
    ("winner", "loser", "expected"),
    [
        ("/users/", "/users/{user_id}/", "users_create_by_user_id"),
        ("/h/{harness_id}/ingress/", "/h/{harness_id}/ingress/{ingress_id}/", "users_create_by_ingress_id"),
        ("/x/", "/x-alt/", "users_create_x-alt"),
    ],
)
def test_disambiguated_operation_id_is_derived_from_the_distinguishing_route_part(
    winner, loser, expected
):
    assert disambiguated_operation_id("users_create", winner, loser) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("https://api.futureagi.com", "https://api.futureagi.com"),
        ("https://api.futureagi.com/", "https://api.futureagi.com"),
        (" https://ai.example.internal:8443/docs/?x=1 ", "https://ai.example.internal:8443"),
        ("http://localhost:8000", "http://localhost:8000"),
        ("api.futureagi.com", None),
        ("ftp://api.futureagi.com", None),
        ("https://", None),
        ("", None),
        (None, None),
    ],
)
def test_normalize_public_base_url_accepts_only_absolute_http_origins(value, expected):
    assert normalize_public_base_url(value) == expected


# --- generator ------------------------------------------------------------------


def test_stock_generator_reproduces_the_collision_shape():
    """Guards the premise: without the fix, drf-yasg emits the same ID twice."""
    duplicates = find_duplicate_operation_ids(_generate(OpenAPISchemaGenerator))
    assert set(duplicates) == {"things_create", "widgets_supported_models"}


def test_collision_aware_generator_renames_only_the_losing_member():
    document = _generate(ManagementAPISchemaGenerator)
    assert find_duplicate_operation_ids(document) == {}
    assert _operation_ids(document) == {
        ("get", "/things/"): "things_list",
        ("post", "/things/"): "things_create",
        ("get", "/things/{thing_id}/"): "things_read",
        ("post", "/things/{thing_id}/"): "things_create_by_thing_id",
        ("get", "/widgets/supported_models/"): "widgets_supported_models",
        ("get", "/widgets/supported-models"): "widgets_supported_models_supported-models",
    }


def test_collision_aware_generator_is_deterministic_across_runs():
    assert _operation_ids(_generate(ManagementAPISchemaGenerator)) == _operation_ids(
        _generate(ManagementAPISchemaGenerator)
    )


def test_generation_failure_names_the_operation():
    class _Explodes(APIView):
        def get(self, request):
            return Response({})

    class _BrokenInspector(ManagementAPISchemaGenerator):
        def get_operation(self, view, path, prefix, method, components, request):
            if "broken" in path:
                raise RuntimeError("serializer blew up")
            return super().get_operation(view, path, prefix, method, components, request)

    info = openapi.Info(title="fixture", default_version="v1")
    generator = _BrokenInspector(info, patterns=[path("broken/", _Explodes.as_view())])
    with pytest.raises(Exception) as excinfo:
        generator.get_schema(request=None, public=True)
    assert "serializer blew up" in str(excinfo.value)


# --- committed contract ---------------------------------------------------------


@pytest.fixture(scope="module")
def committed_contract():
    return json.loads(COMMITTED_CONTRACT.read_text(encoding="utf-8"))


def test_committed_contract_has_unique_operation_ids(committed_contract):
    duplicates = find_duplicate_operation_ids(committed_contract)
    assert duplicates == {}, (
        "Regenerate api_contracts/openapi/swagger.json (scripts/generate-openapi-schema.sh); "
        "the collision-aware generator must produce unique IDs: "
        f"{dict(duplicates)}"
    )


def test_committed_contract_passes_strict_swagger_validation(committed_contract):
    openapi_spec_validator = pytest.importorskip("openapi_spec_validator")
    openapi_spec_validator.validate(committed_contract)


def test_committed_contract_keeps_a_deterministic_host(committed_contract):
    # The checked-in file is generated with an explicit --url so SDK generation
    # and contract diffs do not depend on the generating machine.
    assert committed_contract["host"] == "localhost:8000"
    assert committed_contract["schemes"] == ["http"]


# --- runtime --------------------------------------------------------------------


def _fetch_schema(client, **extra):
    response = client.get(SCHEMA_URL, HTTP_ACCEPT=OPENAPI_JSON, **extra)
    assert response.status_code == 200, response.content[:500]
    assert response["Content-Type"] == f"{OPENAPI_JSON}; charset=utf-8"
    document = json.loads(response.content)
    assert document["swagger"] == "2.0"
    assert document["paths"]
    return document


@pytest.mark.api
def test_runtime_schema_view_serves_a_unique_request_derived_document(client):
    document = _fetch_schema(client)
    assert document["host"] == "testserver"
    assert document["schemes"] == ["http"]
    assert find_duplicate_operation_ids(document) == {}


@pytest.mark.api
def test_runtime_schema_view_honours_proxy_headers_django_trusts(client, settings):
    settings.SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    document = _fetch_schema(
        client, HTTP_HOST="api.example.test", HTTP_X_FORWARDED_PROTO="https"
    )
    assert document["host"] == "api.example.test"
    assert document["schemes"] == ["https"]


@pytest.mark.api
def test_runtime_schema_view_prefers_the_configured_public_base_url(client, settings):
    with override_settings(
        SWAGGER_SETTINGS={
            **settings.SWAGGER_SETTINGS,
            "DEFAULT_API_URL": "https://ai.example.internal",
        }
    ):
        document = _fetch_schema(client, HTTP_HOST="internal-pod:8000")
    assert document["host"] == "ai.example.internal"
    assert document["schemes"] == ["https"]


def test_settings_only_publish_a_normalized_public_base_url():
    # drf-yasg raises SwaggerGenerationError (an HTTP 500 for every /docs/
    # visitor) when DEFAULT_API_URL is set and not an absolute http(s) URL. It
    # reads SWAGGER_SETTINGS live, including for the UI renderer's stock
    # generator, so the guard has to live in the setting itself. Review finding 1.
    source = (
        REPO_ROOT / "futureagi" / "tfc" / "settings" / "settings.py"
    ).read_text(encoding="utf-8")
    assert 'normalize_public_base_url(os.getenv("DEFAULT_API_URL"))' in source
    assert 'SWAGGER_SETTINGS["DEFAULT_API_URL"] = _public_base_url' in source
    # The assignment is conditional on a normalized value, so a bad env value
    # leaves the key unset and drf-yasg falls back to the request.
    assignment = source.index('SWAGGER_SETTINGS["DEFAULT_API_URL"]')
    guard = source.rindex("if _public_base_url:", 0, assignment)
    assert assignment - guard < 80


@pytest.mark.api
def test_runtime_schema_view_keeps_json_only_content_negotiation(client):
    # Documented, unchanged behaviour: the schema renderer answers only its own
    # media types, so a generic JSON Accept header is rejected rather than 500.
    response = client.get(SCHEMA_URL, HTTP_ACCEPT="application/json")
    assert response.status_code == 406
