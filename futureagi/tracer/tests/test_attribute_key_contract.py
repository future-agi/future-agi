"""Pure key/DRF regressions; wrapper/endpoint checks are AST-selected smoke only.

No Django app setup or model substitution. The shared helper and exact field,
DRF serializers, QueryDict, signing and cursor digests are real imports. Model-
bound modules contribute only the explicitly named, unchanged AST definitions.
Run with networking denied, plugin autoload off and parent conftests excluded.
"""

import ast
import json
import socket
from datetime import UTC, datetime, timedelta
from importlib import import_module
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import pytest
from django.conf import UserSettingsHolder, settings
from django.core import signing
from django.http import QueryDict
from rest_framework import serializers

from tracer.utils import attribute_suggestion_contract as contract

ROOT = Path(__file__).resolve().parents[2]
READS = "tracer/services/clickhouse/attribute_reads.py"
LATEST = "tracer/services/clickhouse/query_builders/latest_filter_predicates.py"
VALID = [
    "ordinary",
    " MiXeD\t\n",
    " ",
    "\0",
    "\x7f",
    "客户",
    "e\u0301",
    "😀",
    "key'] OR 1=1 --",
    "x" * 513,
    "x" * 4096,
    "é" * 2048,
    "😀" * 1024,
    "\0" * 4096,
]
INVALID = [
    None,
    True,
    42,
    b"key",
    {},
    [],
    "",
    "\ud800",
    "\udfff",
    "\ud83d\ude00",
    "x" * 4097,
    "é" * 2049,
    "😀" * 1025,
]


@pytest.fixture(autouse=True)
def offline_drf(monkeypatch):
    if not settings.configured:
        # Configure settings only, never django.setup().
        settings.configure()
    # The real Django holder avoids reading LazySettings.SECRET_KEY, which
    # raises when another offline suite configured settings without a secret.
    overrides = UserSettingsHolder(settings._wrapped)
    overrides.USE_I18N = False
    overrides.SECRET_KEY = "test-only-offline-key-contract"
    monkeypatch.setattr(settings, "_wrapped", overrides)

    def denied(*args, **kwargs):
        raise AssertionError("network is forbidden in key-contract tests")

    monkeypatch.setattr(socket, "socket", denied)
    monkeypatch.setattr(socket, "create_connection", denied)
    monkeypatch.setattr(socket, "getaddrinfo", denied)


def selected_source(relative, names, namespace=None):
    """Compile real selected definitions, not substitute model/package modules."""
    source = ROOT / relative
    tree = ast.parse(source.read_text(), filename=str(source))
    selected = []
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module in {
            "tracer.utils.attribute_suggestion_contract",
            "tracer.serializers.attribute_key",
        }:
            selected.append(node)
        elif isinstance(node, (ast.ClassDef, ast.FunctionDef)) and node.name in names:
            selected.append(node)
        elif isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id in names
            for target in node.targets
        ):
            selected.append(node)
    result = {"Any": Any, "serializers": serializers, "json": json}
    result.update(namespace or {})
    exec(
        compile(ast.Module(body=selected, type_ignores=[]), str(source), "exec"), result
    )
    return result


@pytest.fixture
def read_wrappers():
    return selected_source(
        READS,
        {
            "ATTRIBUTE_READ_MAX_KEY_BYTES",
            "ATTRIBUTE_READ_MAX_SEARCH_BYTES",
            "InvalidAttributeKey",
            "InvalidAttributeSearch",
            "_validate_text",
            "validate_attribute_key",
            "validate_attribute_search",
        },
    )


@pytest.fixture(params=["shared", "picker_ast", "latest_ast"])
def validator(request, read_wrappers):
    if request.param == "shared":
        return contract.validate_exact_attribute_key, ValueError
    if request.param == "picker_ast":
        return read_wrappers["validate_attribute_key"], read_wrappers[
            "InvalidAttributeKey"
        ]
    latest = selected_source(
        LATEST,
        {
            "_MAX_ATTRIBUTE_KEY_UTF8_BYTES",
            "UnsupportedFilterShapeError",
            "_validate_attribute_key",
        },
    )
    return latest["_validate_attribute_key"], latest["UnsupportedFilterShapeError"]


@pytest.mark.parametrize("key", VALID, ids=lambda key: f"utf8-{len(key.encode())}")
def test_valid_exact_key(validator, key):
    validate, _ = validator
    assert validate(key) is key


@pytest.mark.parametrize("key", INVALID, ids=lambda key: type(key).__name__)
def test_invalid_exact_key_preserves_wrapper_error(validator, key):
    validate, error = validator
    with pytest.raises(error) as caught:
        validate(key)
    assert type(caught.value) is error


@pytest.fixture
def exact_field():
    return import_module("tracer.serializers.attribute_key").ExactAttributeKeyField


@pytest.mark.parametrize("key", VALID, ids=lambda key: f"utf8-{len(key.encode())}")
def test_real_drf_field_preserves_exact_key(exact_field, key):
    field = exact_field()
    assert field.run_validation(key) is key
    assert field.to_representation(key) is key


@pytest.mark.parametrize("key", INVALID, ids=lambda key: type(key).__name__)
def test_real_drf_field_rejects_invalid_key(exact_field, key):
    with pytest.raises(serializers.ValidationError):
        exact_field().run_validation(key)


@pytest.mark.parametrize("use_querydict", [False, True])
def test_real_drf_optional_present_empty_is_not_omitted(exact_field, use_querydict):
    class Query(serializers.Serializer):
        q = exact_field(required=False)

    data = QueryDict("", mutable=True) if use_querydict else {}
    absent = Query(data=data)
    assert absent.is_valid(), absent.errors
    assert "q" not in absent.validated_data
    data["q"] = ""
    present = Query(data=data)
    assert not present.is_valid()
    assert set(present.errors) == {"q"}


def test_exact_field_schema_and_single_limit(exact_field, read_wrappers):
    assert contract.ATTRIBUTE_KEY_MAX_UTF8_BYTES == 4096
    assert read_wrappers["ATTRIBUTE_READ_MAX_KEY_BYTES"] == 4096
    schema = exact_field.Meta.swagger_schema_fields
    assert schema["type"] == "string"
    assert schema["minLength"] == 1
    assert "4096" in schema["description"] and "UTF-8 bytes" in schema["description"]


def test_real_yasg_query_parameter_has_byte_bound_description(exact_field):
    from drf_yasg import openapi
    from drf_yasg.inspectors import StringDefaultFieldInspector

    inspector = StringDefaultFieldInspector(None, "", "GET", None, None, [])
    field = exact_field(required=False)
    parameter = inspector.field_to_swagger_object(
        field,
        openapi.Parameter,
        False,
        name="q",
        in_=openapi.IN_QUERY,
    )
    assert parameter.type == "string"
    assert parameter.required is False
    assert "4096 UTF-8 bytes" in parameter.description
    schema = inspector.field_to_swagger_object(field, openapi.Schema, False)
    assert schema.type == "string" and schema.min_length == 1
    assert schema.description == parameter.description


@pytest.fixture(
    params=[
        "SpanAttributeProjectQuerySerializer",
        "SpanAttributeValuesQuerySerializer",
        "SpanAttributeDetailQuerySerializer",
        "ObservationAttributeListQuerySerializer",
    ]
)
def endpoint_ast(request, read_wrappers):
    name = request.param
    relative = (
        "tracer/serializers/observation_span.py"
        if name.startswith("Observation")
        else "tracer/serializers/span_attributes.py"
    )
    namespace = selected_source(
        relative,
        {name, "ProjectScopeQueryParamField"},
        read_wrappers,
    )
    project_id = "11111111-1111-4111-8111-111111111111"
    if name.startswith("Observation"):
        data = {"filters": json.dumps({"project_id": project_id})}
        field = "q"
    else:
        data = {"project_id": project_id}
        field = "q" if name == "SpanAttributeProjectQuerySerializer" else "key"
    return namespace[name], data, field


def test_ast_exact_key_matches_checked_in_query_schema(endpoint_ast):
    """Guard the four exported fields; full route export is a separate gate."""
    from drf_yasg import openapi
    from drf_yasg.inspectors import StringDefaultFieldInspector

    routes = {
        "SpanAttributeProjectQuerySerializer": "/api/traces/span-attribute-keys/",
        "SpanAttributeValuesQuerySerializer": "/api/traces/span-attribute-values/",
        "SpanAttributeDetailQuerySerializer": "/api/traces/span-attribute-detail/",
        "ObservationAttributeListQuerySerializer": "/tracer/observation-span/get_span_attributes_list/",
    }
    serializer, _, key = endpoint_ast
    route = routes[serializer.__name__]
    schema = json.loads(
        (ROOT.parent / "api_contracts/openapi/swagger.json").read_text()
    )
    parameters = schema["paths"][route]["get"]["parameters"]
    actual = [p for p in parameters if p.get("in") == "query" and p["name"] == key]
    inspector = StringDefaultFieldInspector(None, route, "GET", None, None, [])
    expected = inspector.field_to_swagger_object(
        serializer().fields[key],
        openapi.Parameter,
        False,
        name=key,
        in_=openapi.IN_QUERY,
    )
    assert actual == [expected]


@pytest.mark.parametrize(
    "key",
    [" MiXeD\t\n", " ", "\0" * 4096, "é" * 2048],
    ids=lambda key: f"utf8-{len(key.encode())}",
)
def test_endpoint_ast_real_drf_accepts_exact_key(endpoint_ast, key):
    serializer, defaults, field = endpoint_ast
    data = QueryDict("", mutable=True)
    data.update({**defaults, field: key})
    query = serializer(data=data)
    assert query.is_valid(), query.errors
    assert query.validated_data[field] == key


@pytest.mark.parametrize(
    "key",
    [" \tMiXeD\n ", "\0", "+", "%00"],
    ids=["whitespace", "nul", "literal-plus", "literal-percent00"],
)
def test_endpoint_ast_urlencoded_key_roundtrip(endpoint_ast, key):
    """Real URL decoding/DRF validation, not an HTTP or proxy qualification."""
    serializer, defaults, field = endpoint_ast
    query = serializer(data=QueryDict(urlencode({**defaults, field: key})))
    assert query.is_valid(), query.errors
    assert query.validated_data[field] == key


@pytest.mark.parametrize(
    "key",
    ["", 42, "\ud800", "é" * 2049],
    ids=lambda key: type(key).__name__,
)
def test_endpoint_ast_real_drf_rejects_invalid_key(endpoint_ast, key):
    serializer, defaults, field = endpoint_ast
    data = QueryDict("", mutable=True)
    data.update({**defaults, field: key})
    query = serializer(data=data)
    assert not query.is_valid()
    assert set(query.errors) == {field}


def test_ast_value_search_and_scope_guards_unchanged(read_wrappers):
    namespace = selected_source(
        "tracer/serializers/span_attributes.py",
        {
            "SpanAttributeValuesQuerySerializer",
            "SpanAttributeProjectQuerySerializer",
        },
        read_wrappers,
    )
    values = namespace["SpanAttributeValuesQuerySerializer"]
    data = {"project_id": "11111111-1111-4111-8111-111111111111", "key": "safe"}
    query = values(data={**data, "q": "  Needle  "})
    assert query.is_valid(), query.errors
    assert query.validated_data["q"] == "Needle"
    for bad in ["\0", "\x7f", "x" * 513]:
        query = values(data={**data, "q": bad})
        assert not query.is_valid() and set(query.errors) == {"q"}
    project = namespace["SpanAttributeProjectQuerySerializer"]
    for bad in [
        {},
        {"workspace_scope": True},
        {
            "project_id": data["project_id"],
            "workspace_scope": True,
            "page_size": 1,
        },
        {"project_id": data["project_id"], "cursor": "token"},
    ]:
        assert not project(data=bad).is_valid()
    assert project().fields["cursor"].max_length == 8192
    for bad in ["\0", "\x7f", "é" * 257]:
        with pytest.raises(read_wrappers["InvalidAttributeSearch"]):
            read_wrappers["validate_attribute_search"](bad)


def test_real_cursor_and_seen_binding_are_exact_and_fixed_size():
    from tracer.services.clickhouse.attribute_cursor_state import (
        attribute_cursor_binding_digest,
    )
    from tracer.services.clickhouse.list_cursor import (
        CURSOR_SALT,
        ListCursorError,
        decode_list_cursor,
        encode_list_cursor,
        normalize_cursor_query,
    )

    end = datetime.now(UTC)
    options = {
        "resource": "span_attribute_keys",
        "scope": {"project_id": "one"},
        "page_size": 1,
    }
    for key in [" Key ", "\0" * 4096]:
        query = {"q": key}
        token = encode_list_cursor(
            **options,
            query=query,
            window_start=end - timedelta(days=1),
            window_end=end,
            order=(end, (), ()),
            seen_rows=0,
        )
        assert len(token) < 8192
        payload = signing.loads(token, key=settings.SECRET_KEY, salt=CURSOR_SALT)
        assert len(payload["query"]) == 64
        assert "q" not in payload
        decode_list_cursor(token, **options, query=query)
        other = {"q": key.strip() if key.strip() != key else key[:-1]}
        with pytest.raises(ListCursorError, match="does not match"):
            decode_list_cursor(token, **options, query=other)
        assert attribute_cursor_binding_digest(
            resource=options["resource"], binding=query
        ) != (
            attribute_cursor_binding_digest(resource=options["resource"], binding=other)
        )
    assert normalize_cursor_query({"q": " Key ", "search": " value "}) == {
        "q": " Key ",
        "search": "value",
    }
