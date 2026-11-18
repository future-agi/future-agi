"""Documentation and parser regressions; no database or provider I/O.

Full canonical generation is a separate release gate. These exercise the real
route annotations, Swagger generator, strict parser and signature verifier.
"""

import ast
import base64
import hashlib
import io
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import UUID

import jwt
import pytest
from django.db.backends.base.base import BaseDatabaseWrapper
from django.test import override_settings
from django.urls import path
from drf_yasg import openapi
from drf_yasg.generators import OpenAPISchemaGenerator
from rest_framework.test import APIRequestFactory

from tracer.services.attachments import provider_recordings, wire
from tracer.services.attachments.contract import AttachmentError
from tracer.views import attachments as views

UID = "11111111-1111-4111-8111-111111111111"
ROUTES = (
    (
        "tracer/v1/attachments/providers/livekit/events",
        views.AttachmentLiveKitEventView,
    ),
    (
        "tracer/v1/projects/{project_id}/attachments/provider-recordings",
        views.AttachmentProviderRegistrationView,
    ),
    (
        "tracer/v1/projects/{project_id}/attachments/source-envelopes",
        views.AttachmentSourceEnvelopeView,
    ),
)


@pytest.fixture(autouse=True)
def no_database(monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail("Control contract tests must not access any database")

    monkeypatch.setattr(BaseDatabaseWrapper, "ensure_connection", unexpected)


@pytest.fixture
def swagger():
    generator = OpenAPISchemaGenerator(
        info=openapi.Info(title="Attachment control contracts", default_version="v1"),
        patterns=[
            path(route.replace("{project_id}", "<uuid:project_id>"), view.as_view())
            for route, view in ROUTES
        ],
    )
    return generator.get_schema(request=None, public=True).as_odict()


def expand(schema, swagger):
    if isinstance(schema, list):
        return [expand(item, swagger) for item in schema]
    if not isinstance(schema, dict):
        return schema
    if "$ref" in schema:
        return expand(swagger["definitions"][schema["$ref"].split("/")[-1]], swagger)
    return {key: expand(value, swagger) for key, value in schema.items()}


def body_schema(swagger, route):
    # A focused generator factors its common /tracer/v1 prefix into basePath.
    route = "/" + route
    route = route.removeprefix(swagger.get("basePath", ""))
    operation = swagger["paths"][route]["post"]
    parameters = [p for p in operation["parameters"] if p["in"] == "body"]
    assert len(parameters) == 1
    assert parameters[0]["required"] is True
    return expand(parameters[0]["schema"], swagger)


@pytest.mark.parametrize("route,view", ROUTES)
def test_control_post_annotation_is_documentation_only(route, view):
    assert view.post._swagger_auto_schema["request_body"] is not None
    assert not hasattr(view.post, "__wrapped__"), (
        "Do not run serializer validation before authorization/signature verification"
    )


@pytest.mark.parametrize("route,view", ROUTES)
def test_control_posts_generate_required_typed_body(swagger, route, view):
    schema = body_schema(swagger, route)
    assert schema["type"] == "object"
    assert schema["properties"]


def test_registration_has_exact_six_keys_and_closed_association(swagger):
    schema = body_schema(swagger, ROUTES[1][0])
    expected = {
        "connection_id",
        "egress_id",
        "room_name",
        "recording_id",
        "association",
        "direction",
    }
    assert set(schema["properties"]) == set(schema["required"]) == expected
    assert schema["additionalProperties"] is False
    fields = schema["properties"]
    assert fields["egress_id"]["pattern"] == "^EG_[A-Za-z0-9]{1,61}$"
    assert fields["room_name"]["minLength"] == 1
    assert fields["room_name"]["maxLength"] == 256
    assert fields["direction"]["enum"] == ["input", "output", "mixed"]
    canonical = wire._schema()["$defs"]
    assert fields["recording_id"]["pattern"] == canonical["UUID"]["pattern"]
    association = fields["association"]
    assert association["additionalProperties"] is False
    assert set(association["required"]) == set(canonical["Association"]["required"])
    parent = association["properties"]["parent"]
    assert parent["additionalProperties"] is False
    assert set(parent["required"]) == {"trace_id", "span_id"}
    assert parent["properties"]["span_id"] == canonical["SpanID"]
    assert parent["properties"]["trace_id"]["x-oneOf"] == [
        {"type": "string", **canonical["TraceInput"]["oneOf"][0]},
        canonical["UUID"],
    ]


def test_source_envelope_is_only_opaque_provider_file(swagger):
    schema = body_schema(swagger, ROUTES[2][0])
    assert schema["required"] == ["source"]
    assert set(schema["properties"]) == {"source"}
    assert schema["additionalProperties"] is False
    source = schema["properties"]["source"]
    canonical = wire._schema()["$defs"]["ImportSource"]["oneOf"][1]
    assert source["additionalProperties"] is False
    assert set(source["required"]) == set(canonical["required"])
    assert set(source["properties"]) == set(canonical["properties"])
    assert source["properties"]["kind"]["enum"] == ["ProviderFile"]
    for name in ("connection_id", "registered_output_id"):
        assert (
            source["properties"][name]["pattern"]
            == wire._schema()["$defs"]["UUID"]["pattern"]
        )
    assert "url" not in source["properties"]
    assert "envelope_id" not in source["properties"]


def test_webhook_documents_provider_extensions_and_integer_variants(swagger):
    schema = body_schema(swagger, ROUTES[0][0])
    assert schema["additionalProperties"] is True
    assert not schema.get("required")
    assert set(schema["properties"]) == {"id", "event", "egressInfo"}
    info = schema["properties"]["egressInfo"]
    assert info["additionalProperties"] is True
    fields = info["properties"]
    assert set(fields) == {
        "egressId",
        "roomName",
        "status",
        "updatedAt",
        "endedAt",
        "fileResults",
    }
    assert fields["status"]["x-anyOf"] == [{"type": "string"}, {"type": "integer"}]
    for field in (
        fields["updatedAt"],
        fields["endedAt"],
        fields["fileResults"]["items"]["properties"]["size"],
    ):
        assert field["x-anyOf"] == [
            {"type": "integer"},
            {"type": "string", "pattern": "^[0-9]+$", "maxLength": 20},
        ]


class RawRequest:
    def __init__(self, body):
        self._request = io.BytesIO(body)
        self.headers = {"Authorization": "synthetic-token"}

    @property
    def data(self):
        pytest.fail("Webhook documentation must never access request.data")


def test_webhook_passes_original_bytes_to_verifier_before_processing(monkeypatch):
    raw = b'{ "id": "event-1", "providerExtra": [1, 2] }\n'
    events = []
    connection, event = object(), object()

    def verify(body, auth):
        assert body == raw
        assert auth == "synthetic-token"
        events.append("verified")
        return connection, event

    def process(actual_connection, actual_event):
        assert events == ["verified"]
        assert actual_connection is connection and actual_event is event
        events.append("processed")

    monkeypatch.setattr(provider_recordings, "verify_livekit_webhook", verify)
    monkeypatch.setattr(provider_recordings, "process_livekit_event", process)
    response = views.AttachmentLiveKitEventView().post(RawRequest(raw))
    assert events == ["verified", "processed"]
    assert response.status_code == 204


def test_webhook_read_remains_bounded_and_failure_never_dispatches(monkeypatch):
    body = b"x" * (provider_recordings.WEBHOOK_MAX_BYTES + 10)
    process = Mock()

    def reject(raw, authorization):
        assert len(raw) == provider_recordings.WEBHOOK_MAX_BYTES + 1
        raise AttachmentError("not_found", 404)

    monkeypatch.setattr(provider_recordings, "verify_livekit_webhook", reject)
    monkeypatch.setattr(provider_recordings, "process_livekit_event", process)
    with pytest.raises(AttachmentError):
        views.AttachmentLiveKitEventView().post(RawRequest(body))
    process.assert_not_called()


@pytest.fixture
def signed_webhook(monkeypatch):
    secret = "synthetic-control-test-key-32-bytes-only"
    connection = SimpleNamespace(
        id=UUID(UID),
        scope_id=UUID(UID),
        key_id="test",
        secret_nonce=b"test",
        secret_ciphertext=b"test",
    )
    monkeypatch.setattr(
        provider_recordings.AttachmentProviderConnection.objects,
        "filter",
        Mock(return_value=SimpleNamespace(first=lambda: connection)),
    )
    monkeypatch.setattr(
        provider_recordings.envelopes, "open_bytes", lambda *args: secret.encode()
    )

    def signed(raw):
        return jwt.encode(
            {
                "iss": "control-test",
                "exp": datetime.now(UTC) + timedelta(minutes=1),
                "sha256": base64.b64encode(hashlib.sha256(raw).digest()).decode(),
            },
            secret,
            algorithm="HS256",
        )

    return connection, signed


def test_real_verifier_binds_signature_to_raw_body(signed_webhook):
    connection, signed = signed_webhook
    raw = b'{ "event": "room_started", "providerExtra": {"value": 1} }\n'
    token = signed(raw)
    actual, event = provider_recordings.verify_livekit_webhook(raw, token)
    assert actual is connection
    assert event["providerExtra"] == {"value": 1}
    normalized = json.dumps(json.loads(raw), separators=(",", ":")).encode()
    assert normalized != raw
    with pytest.raises(AttachmentError) as exc:
        provider_recordings.verify_livekit_webhook(normalized, token)
    assert exc.value.code == "not_found"


@pytest.mark.parametrize("raw", [b'{"id":"one","id":"two"}', b'{"value":NaN}', b"[]"])
def test_real_verifier_keeps_strict_json_after_signature(signed_webhook, raw):
    _, signed = signed_webhook
    with pytest.raises(AttachmentError):
        provider_recordings.verify_livekit_webhook(raw, signed(raw))


@pytest.mark.parametrize(
    "raw", [b'{"source":{},"source":{}}', b'{"source":NaN}', b"{}" * 40000]
)
def test_control_strict_parser_unchanged(raw):
    with pytest.raises(AttachmentError):
        views.StrictJSONParser().parse(io.BytesIO(raw))


@pytest.mark.parametrize(
    "view",
    [views.AttachmentProviderRegistrationView, views.AttachmentSourceEnvelopeView],
)
def test_control_authorization_remains_before_body_access(view, monkeypatch):
    def deny(*args):
        raise AttachmentError("not_found", 404)

    monkeypatch.setattr(view, "writer", deny)
    with pytest.raises(AttachmentError):
        view().post(RawRequest(b"{}"), UID)


@pytest.mark.parametrize(
    "body",
    [
        {"source": {"kind": "RemoteUrlEnvelope", "envelope_id": UID}},
        {
            "source": {
                "kind": "ProviderFile",
                "connection_id": UID,
                "registered_output_id": UID,
                "url": "https://example.test/file",
            }
        },
        {
            "source": {
                "kind": "ProviderFile",
                "connection_id": UID,
                "registered_output_id": UID,
            },
            "url": "https://example.test/file",
        },
    ],
)
def test_envelope_route_rejects_locators_and_other_import_variants(body, monkeypatch):
    monkeypatch.setattr(
        views.AttachmentSourceEnvelopeView, "writer", lambda *args: (object(), object())
    )
    monkeypatch.setattr(views.broker, "check_writer", lambda *args: None)
    lookup = Mock(
        side_effect=AssertionError("Invalid source must not resolve a registration")
    )
    monkeypatch.setattr(provider_recordings, "completed_output", lookup)
    with pytest.raises(AttachmentError):
        views.AttachmentSourceEnvelopeView().post(SimpleNamespace(data=body), UID)
    lookup.assert_not_called()


@pytest.mark.parametrize(
    "extra", [{"url": "https://example.test/file"}, {"extra": True}]
)
def test_provider_registration_route_rejects_unknown_keys(extra, monkeypatch):
    monkeypatch.setattr(
        views.AttachmentProviderRegistrationView,
        "writer",
        lambda *args: (object(), object()),
    )
    monkeypatch.setattr(views.broker, "check_writer", lambda *args: None)
    register = Mock(
        side_effect=AssertionError("Unknown fields must not reach registration")
    )
    monkeypatch.setattr(provider_recordings, "register", register)
    body = (
        dict.fromkeys(
            (
                "connection_id",
                "egress_id",
                "room_name",
                "recording_id",
                "association",
                "direction",
            )
        )
        | extra
    )
    with pytest.raises(AttachmentError):
        views.AttachmentProviderRegistrationView().post(SimpleNamespace(data=body), UID)
    register.assert_not_called()


def test_dark_webhook_does_not_read_or_verify_body(monkeypatch):
    verify = Mock(side_effect=AssertionError("Dark route must not verify"))
    monkeypatch.setattr(provider_recordings, "verify_livekit_webhook", verify)
    request = APIRequestFactory().post(
        "/events", b"not json", content_type="application/webhook+json"
    )
    with override_settings(FI_ATTACHMENTS_ENABLED=False):
        response = views.AttachmentLiveKitEventView.as_view()(request)
    assert response.status_code == 404
    verify.assert_not_called()


@pytest.mark.parametrize(
    "route,status",
    [(ROUTES[0][0], "204"), (ROUTES[1][0], "201"), (ROUTES[2][0], "200")],
)
def test_request_schemas_are_not_inferred_as_success_responses(swagger, route, status):
    route = ("/" + route).removeprefix(swagger.get("basePath", ""))
    responses = swagger["paths"][route]["post"]["responses"]
    assert status in responses
    assert "schema" not in responses[status]
    assert not any(key.startswith("2") and key != status for key in responses)


@pytest.mark.parametrize(
    "trace,valid",
    [
        ("0123456789abcdef0123456789abcdef", True),
        (UID, True),
        ("0" * 32, False),
        ("not-a-trace", False),
    ],
)
def test_plain_swagger_trace_pattern_preserves_canonical_alternatives(
    swagger, trace, valid
):
    from jsonschema import Draft7Validator

    schema = body_schema(swagger, ROUTES[1][0])
    trace_schema = schema["properties"]["association"]["properties"]["parent"][
        "properties"
    ]["trace_id"]
    assert Draft7Validator(trace_schema).is_valid(trace) is valid
    assert wire.is_valid("TraceInput", trace) is valid


@pytest.mark.parametrize("route,view", ROUTES)
def test_runtime_route_declaration_matches_documented_control_view(route, view):
    # Inspect the real route declaration without importing every unrelated app
    # URL. Full-root imports can activate the EE licensing database path, which
    # this no-database test must not permit. Canonical generation separately
    # proves these routes appear in the complete application schema.
    source = Path(views.__file__).parents[1] / "urls.py"
    expected = route.removeprefix("tracer/").replace("{project_id}", "<str:project_id>")
    matches = [
        node
        for node in ast.walk(ast.parse(source.read_text()))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "path"
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and node.args[0].value == expected
    ]
    assert len(matches) == 1
    assert ast.unparse(matches[0].args[1]) == f"attachments.{view.__name__}.as_view()"


def test_schema_generation_does_not_mutate_canonical_wire_schema(swagger):
    assert wire._schema() == json.loads(wire.SCHEMA_PATH.read_text())
