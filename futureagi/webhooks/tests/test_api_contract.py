"""The webhooks API's OpenAPI contract (DESIGN.md section 10), generated in-process with FA's own schema class:
every operation has a typed 2xx response and FA's default error response, every mutation that takes a body declares
it, and the routes are exactly the ones the design lists (the ``cli_listen`` routes are WH-4b's). The committed
``api_contracts/openapi/swagger.json`` is regenerated with ``scripts/generate-openapi-schema.sh``, which needs the
EE checkout.
"""

from __future__ import annotations

from django.urls import include, path
from drf_yasg import openapi
from drf_yasg.generators import OpenAPISchemaGenerator

EXPECTED = {
    ("/webhooks/endpoints/", "get"),
    ("/webhooks/endpoints/", "post"),
    ("/webhooks/endpoints/{endpoint_id}/", "get"),
    ("/webhooks/endpoints/{endpoint_id}/", "patch"),
    ("/webhooks/endpoints/{endpoint_id}/", "delete"),
    ("/webhooks/endpoints/{endpoint_id}/enable/", "post"),
    ("/webhooks/endpoints/{endpoint_id}/disable/", "post"),
    ("/webhooks/endpoints/{endpoint_id}/resume/", "post"),
    ("/webhooks/endpoints/{endpoint_id}/rotate-secret/", "post"),
    ("/webhooks/endpoints/{endpoint_id}/revoke-previous-secret/", "post"),
    ("/webhooks/endpoints/{endpoint_id}/test/", "post"),
    ("/webhooks/endpoints/{endpoint_id}/deliveries/", "get"),
    ("/webhooks/endpoints/{endpoint_id}/activity/", "get"),
    ("/webhooks/preview/", "post"),
    ("/webhooks/deliveries/", "get"),
    ("/webhooks/deliveries/redeliver/", "post"),
    ("/webhooks/deliveries/{delivery_id}/", "get"),
    ("/webhooks/deliveries/{delivery_id}/redeliver/", "post"),
    ("/webhooks/events/", "get"),
    ("/webhooks/event-types/", "get"),
    ("/webhooks/egress-ips/", "get"),
    ("/webhooks/policy/", "get"),
    ("/webhooks/policy/", "put"),
    ("/webhooks/policy/workspace/{workspace_id}/", "get"),
    ("/webhooks/policy/workspace/{workspace_id}/", "put"),
}
NO_BODY = {
    ("/webhooks/endpoints/{endpoint_id}/", "delete"),
}
# Body-less POSTs declare an explicit empty object (``EmptyRequestSerializer``), so FA's contract coverage check
# (``scripts/check-management-api-contract-coverage.mjs``, baseline 0) counts no mutation without a body schema.
EMPTY_BODY = {
    ("/webhooks/endpoints/{endpoint_id}/revoke-previous-secret/", "post"),
    ("/webhooks/deliveries/{delivery_id}/redeliver/", "post"),
}


def _schema():
    gen = OpenAPISchemaGenerator(
        openapi.Info(title="webhooks", default_version="v1"),
        patterns=[path("webhooks/", include("webhooks.urls"))],
    )
    return gen.get_schema(request=None, public=True)


def test_every_webhooks_operation_has_a_typed_contract():
    schema = _schema()
    base = schema.get("basePath", "/").rstrip(
        "/"
    )  # drf_yasg moves the common "/webhooks" prefix here
    found = set()
    for short, item in schema["paths"].items():
        route = base + short
        for method in ("get", "post", "put", "patch", "delete"):
            op = item.get(method)
            if op is None:
                continue
            key = (route, method)
            found.add(key)
            responses = op["responses"]
            assert "default" in responses, key
            ok = [c for c in responses if c.startswith("2")]
            assert ok and all(responses[c].get("schema") for c in ok), key
            assert op.get("tags") == ["webhooks"], key
            has_body = any(p.get("in") == "body" for p in op.get("parameters", []))
            if method in ("post", "put", "patch"):
                assert has_body == (key not in NO_BODY), key
            if key in EMPTY_BODY:
                (body,) = [p for p in op["parameters"] if p.get("in") == "body"]
                ref = body["schema"].get("$ref", "")
                shape = (
                    schema["definitions"][ref.rsplit("/", 1)[-1]]
                    if ref
                    else body["schema"]
                )
                assert shape.get("properties", {}) == {}, key
                assert shape.get("additionalProperties") is False, key
    assert found == EXPECTED


def test_operation_ids_are_unique_and_definitions_are_named():
    schema = _schema()
    ids = [
        op["operationId"]
        for item in schema["paths"].values()
        for m, op in item.items()
        if m in ("get", "post", "put", "patch", "delete")
    ]
    assert len(ids) == len(set(ids))
    for name in (
        "WebhookEndpointCreatedResponse",
        "WebhookDeliveryDetailResponse",
        "WebhookPolicyResponse",
    ):
        assert name in schema["definitions"], name
    base = schema.get("basePath", "/").rstrip("/")
    paths = {base + k: v for k, v in schema["paths"].items()}
    test_op = paths["/webhooks/endpoints/{endpoint_id}/test/"]["post"]
    assert (
        "webhooks_deliveries_redeliver_one" in ids
        and "webhooks_deliveries_redeliver_bulk" in ids
    )
    assert {"200", "202"} <= set(test_op["responses"])


def test_the_error_copy_has_one_source_and_every_raised_code_is_declared():
    """``api_errors.MESSAGES`` is read from FA's registry (``tfc/utils/error_codes.py``), and every
    ``WEBHOOK_*`` code the app's modules name is declared there (the one warning code aside)."""
    import re
    from pathlib import Path

    from tfc.utils.error_codes import err_dict, get_error_message
    from webhooks.api_errors import CODES, MESSAGES

    assert set(CODES) == {k for k in err_dict if k.startswith("WEBHOOK_")}
    assert MESSAGES == {c: get_error_message(c) for c in CODES}
    app = Path(__file__).resolve().parents[1]
    named = set()
    for p in app.glob("*.py"):
        named |= set(re.findall(r'"(WEBHOOK_[A-Z_]+)"', p.read_text()))
    assert named - set(CODES) == {
        "WEBHOOK_URL_QUERY_SECRET"
    }  # a create warning, not an error
