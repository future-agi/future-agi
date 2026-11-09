"""Each input format, end to end: a pinned vendor spec plus a curated
overlay generate exactly the reviewed tools.generated.json, and the
format's own quirks become correct tool inputs.

Goldens are compared as parsed JSON: the commit hook formats JSON files, so
their bytes may differ from what generate.py writes."""

from __future__ import annotations

import gzip
import hashlib
import json
import re

import pytest
import support
from conftest import edit_overlay, edit_record, golden, output, provider_dir, read_yaml, run_ok, run_refused, tool
from restgen.catalog import OUTPUT_SCHEMA

APPS = {
    # app: (format, the vendor API origin its tools call, base path)
    "gmail": ("google-discovery", "https://gmail.googleapis.com", ""),
    "google-calendar": ("google-discovery", "https://www.googleapis.com", "/calendar/v3"),
    "microsoft-outlook": ("openapi-3", "https://graph.microsoft.com", "/v1.0"),
    "microsoft-teams": ("openapi-3", "https://graph.microsoft.com", "/v1.0"),
    "slack": ("swagger-2", "https://slack.com", "/api"),
    "adyen": ("postman-2.1", "https://balanceplatform-api-test.adyen.com", "/btl/v4"),
}


def _props(t: dict) -> dict:
    return t["inputSchema"]["properties"]


@pytest.mark.parametrize(
    "app",
    [
        pytest.param("gmail", id="discovery-gmail"),
        pytest.param("google-calendar", id="discovery-calendar"),
        pytest.param("microsoft-outlook", id="openapi3-graph-outlook"),
        pytest.param("microsoft-teams", id="openapi3-graph-teams"),
        pytest.param("slack", id="swagger2-slack"),
        pytest.param("adyen", id="postman-adyen"),
    ],
)
def test_format_generates_the_reviewed_tools(generated, app):
    got = output(generated, app)
    assert got == golden(app)
    assert got["schema"] == OUTPUT_SCHEMA
    record = yaml_record(generated, app)
    assert got["spec"] == {"url": record["upstream"]["spec"]["url"], "sha256": record["upstream"]["spec"]["sha256"]}
    assert record["upstream"]["spec"]["format"] == APPS[app][0]
    assert 8 <= len(got["tools"]) <= 25


def yaml_record(catalog, app):
    return read_yaml(provider_dir(catalog, app).parent / "futureagi-rest.yaml")


@pytest.mark.parametrize("app", sorted(APPS))
def test_every_tool_names_its_vendor_origin_and_base_path(generated, app):
    _, origin, base = APPS[app]
    for t in output(generated, app)["tools"]:
        assert t["inputSchema"]["x-origin"] == origin, t["name"]
        assert t["operation"]["path"].startswith(base + "/"), t["name"]


def test_server_outside_the_vendor_domains_refused(app_catalog):
    catalog = app_catalog("slack")
    edit_record(catalog / "apps/slack/app.yaml", lambda d: d["vendor"].update(domains=["slack-edge.com"]))
    err = run_refused(catalog)
    assert "host slack.com is not under the app's vendor.domains (slack-edge.com)" in err


# --- Google Discovery -----------------------------------------------------


def test_discovery_fixed_user_id_fills_the_path_and_is_not_an_input(generated):
    t = tool(output(generated, "gmail"), "search_threads")
    assert t["operation"] == {"method": "GET", "path": "/gmail/v1/users/me/threads"}
    assert "userId" not in _props(t)
    assert sorted(_props(t)) == ["labelIds", "maxResults", "pageToken", "q"]  # includeSpamTrash excluded


def test_discovery_repeated_parameter_is_an_array(generated):
    labels = _props(tool(output(generated, "gmail"), "search_threads"))["labelIds"]
    assert labels["type"] == "array" and labels["items"] == {"type": "string"}


def test_discovery_string_typed_bounds_and_defaults_become_numbers(generated):
    page = _props(tool(output(generated, "gmail"), "search_threads"))["maxResults"]
    assert page["default"] == 100 and isinstance(page["default"], int)


def test_discovery_api_level_parameters_are_never_inputs(generated):
    api_level = {"key", "access_token", "oauth_token", "alt", "fields", "prettyPrint", "quotaUser", "upload_protocol",
                 "uploadType", "callback", "$.xgafv"}  # fmt: skip
    for app in ("gmail", "google-calendar"):
        for t in output(generated, app)["tools"]:
            assert not api_level & set(_props(t)), (app, t["name"])


def test_discovery_nested_body_projection(generated):
    body = _props(tool(output(generated, "gmail"), "create_draft"))["body"]
    assert body["required"] == ["message"] and body["additionalProperties"] is False
    message = body["properties"]["message"]
    assert sorted(message["properties"]) == ["raw", "threadId"]
    assert message["required"] == ["raw"] and message["additionalProperties"] is False


def test_discovery_post_that_reads_is_read_only(generated):
    t = tool(output(generated, "google-calendar"), "find_free_busy")
    assert t["operation"]["method"] == "POST"
    assert t["annotations"]["readOnlyHint"] is True and t["posture"] == "auto"


# --- OpenAPI 3.x (Microsoft Graph) ----------------------------------------


def test_openapi3_renamed_query_input_carries_its_wire_name(generated):
    props = _props(tool(output(generated, "microsoft-outlook"), "list_messages"))
    assert props["top"]["x-wireName"] == "$top" and props["filter"]["x-wireName"] == "$filter"
    assert not [n for n in props if n.startswith("$")]


def test_openapi3_unportable_input_name_refused_without_rename(app_catalog):
    catalog = app_catalog("microsoft-outlook")
    edit_overlay(catalog, "microsoft-outlook", lambda d: tool(d, "list_messages")["params"].pop("rename"))
    err = run_refused(catalog)
    assert "parameter $top: '$top' is not a portable tool input name" in err
    assert "rename it in params.rename" in err
    written = provider_dir(catalog, "microsoft-outlook") / "tools.generated.json"
    assert written.read_bytes() == (provider_dir(support.CATALOG, "microsoft-outlook") / "tools.generated.json").read_bytes()


def test_openapi3_renamed_path_parameter_renames_its_placeholder(app_catalog):
    catalog = app_catalog("microsoft-teams")
    edit_overlay(catalog, "microsoft-teams", lambda d: tool(d, "list_channels")["params"].update(rename={
        "team-id": "team_id", "$filter": "filter", "$select": "select"}))  # fmt: skip
    run_ok(catalog)
    t = tool(output(catalog, "microsoft-teams"), "list_channels")
    assert t["operation"]["path"] == "/v1.0/teams/{team_id}/channels"
    assert "team_id" in t["inputSchema"]["required"] and "x-wireName" not in _props(t)["team_id"]


def test_openapi3_odata_annotations_are_dropped_from_inputs(generated):
    for app in ("microsoft-outlook", "microsoft-teams"):
        for t in output(generated, app)["tools"]:
            assert "@odata" not in json.dumps(t["inputSchema"]).replace("@odata.nextLink holds", ""), t["name"]


def test_openapi3_comma_separated_array_becomes_a_string(generated):
    select = _props(tool(output(generated, "microsoft-outlook"), "list_messages"))["select"]
    assert select["type"] == "string" and select["description"].endswith("Comma-separated values.")


def test_openapi3_header_parameter_must_be_excluded(app_catalog):
    catalog = app_catalog("microsoft-outlook")
    edit_overlay(catalog, "microsoft-outlook", lambda d: tool(d, "delete_event").pop("params"))
    err = run_refused(catalog)
    assert "parameter If-Match (header): a header parameter cannot be a tool input; exclude it in params.exclude" in err


def test_openapi3_non_json_body_refused(app_catalog):
    catalog = app_catalog("microsoft-outlook")

    def change(d):
        t = tool(d, "update_message")
        t["operation"] = {"method": "PUT", "path": "/me/messages/{message-id}/$value"}
        t.pop("body")
        t.pop("response")

    edit_overlay(catalog, "microsoft-outlook", change)
    err = run_refused(catalog)
    assert "request body: the request body is application/octet-stream, not JSON; pick another operation" in err


def test_openapi3_1_type_arrays_and_const_pass_through(tmp_path):
    from restgen.openapi3 import OpenAPI3Reader

    spec = {
        "openapi": "3.1.0",
        "servers": [{"url": "https://api.example.com/v2"}],
        "paths": {"/items/{id}": {"patch": {
            "operationId": "items.update",
            "parameters": [{"name": "id", "in": "path", "required": True, "schema": {"type": "string"}}],
            "requestBody": {"required": True, "content": {"application/json": {"schema": {
                "type": "object", "required": ["name"],
                "properties": {"name": {"type": ["string", "null"]}, "kind": {"const": "item"},
                               "size": {"type": "integer", "exclusiveMinimum": 0}}}}}},
            "responses": {"200": {"description": "ok"}}}}},
    }  # fmt: skip
    reader = OpenAPI3Reader(spec)
    op = reader.operation(reader.refs()[0])
    assert reader.servers() == ["https://api.example.com/v2"]
    assert op.body.schema["properties"]["name"] == {"type": ["string", "null"]}
    assert op.body.schema["properties"]["kind"] == {"const": "item"}
    assert op.body.schema["properties"]["size"] == {"type": "integer", "exclusiveMinimum": 0}


def test_openapi3_0_boolean_exclusive_bounds_become_2020_12():
    from restgen.schemaconv import Converter

    conv = Converter(lambda ref: None, input_side=True)
    assert conv.convert({"type": "number", "minimum": 1, "exclusiveMinimum": True}) == {
        "type": "number", "exclusiveMinimum": 1}  # fmt: skip
    assert conv.convert({"type": "string", "nullable": True, "x-ms-enum": {}}) == {"type": "string"}


def test_openapi3_number_or_inf_string_keeps_the_number():
    # Microsoft Graph types a double as oneOf [number (nullable), string
    # (nullable), ReferenceNumeric (string enum -INF, INF, NaN)], for example
    # outlookGeoCoordinates.latitude (event.location.coordinates). A member
    # that says what a value is stays a branch even when it is nullable; only
    # a member that says nothing but "or null" is dropped (IR-CN15c-7).
    from restgen.schemaconv import Converter

    refs = {"#/components/schemas/ReferenceNumeric": {"enum": ["-INF", "INF", "NaN"], "type": "string", "nullable": True}}
    conv = Converter(refs.get, input_side=True)
    latitude = {
        "oneOf": [
            {"type": "number", "format": "double", "nullable": True},
            {"type": "string", "nullable": True},
            {"$ref": "#/components/schemas/ReferenceNumeric"},
        ],
        "description": "The latitude of the location.",
    }
    assert conv.convert(latitude) == {
        "description": "The latitude of the location.",
        "oneOf": [
            {"type": "number", "format": "double"},
            {"type": "string"},
            {"type": "string", "enum": ["-INF", "INF", "NaN"]},
        ],
    }
    # "Or null" alone is still dropped: one member left is the schema itself.
    assert conv.convert({"anyOf": [{"$ref": "#/components/schemas/ReferenceNumeric"}, {"type": "object", "nullable": True}]}) == {
        "type": "string", "enum": ["-INF", "INF", "NaN"]}  # fmt: skip
    assert conv.convert({"anyOf": [{"type": "integer"}, {"type": "null"}]}) == {"type": "integer"}


# --- Swagger 2.0 (Slack) --------------------------------------------------


def test_swagger2_credential_parameter_is_never_an_input(generated):
    for t in output(generated, "slack")["tools"]:
        assert "token" not in _props(t), t["name"]
        assert "token" not in _props(t).get("body", {}).get("properties", {}), t["name"]


def test_swagger2_credential_parameter_must_be_declared(app_catalog):
    catalog = app_catalog("slack")
    edit_overlay(catalog, "slack", lambda d: d.pop("credentialParams"))
    err = run_refused(catalog)
    assert "parameter token (header): a header parameter cannot be a tool input" in err


def test_swagger2_form_fields_become_a_json_body(generated):
    t = tool(output(generated, "slack"), "post_message")
    assert t["operation"] == {"method": "POST", "path": "/api/chat.postMessage"}
    body = _props(t)["body"]
    assert sorted(body["properties"]) == ["channel", "text", "thread_ts"]
    assert body["required"] == ["channel", "text"]


def test_swagger2_form_without_json_refused():
    from restgen.swagger2 import Swagger2Reader

    spec = {
        "swagger": "2.0", "host": "api.example.com", "basePath": "/v1", "schemes": ["https"],
        "paths": {"/upload": {"post": {
            "operationId": "upload", "consumes": ["application/x-www-form-urlencoded"],
            "parameters": [{"name": "title", "in": "formData", "type": "string"}],
            "responses": {"200": {"description": "ok"}}}}},
    }  # fmt: skip
    reader = Swagger2Reader(spec)
    op = reader.operation(reader.refs()[0])
    assert op.body.unsupported == "the operation takes form fields as application/x-www-form-urlencoded, not JSON"


# --- Postman Collection v2.1 (Adyen) --------------------------------------


def test_postman_request_selected_by_name_with_collection_variables_filled(generated):
    t = tool(output(generated, "adyen"), "list_transactions")
    assert t["operation"] == {"method": "GET", "path": "/btl/v4/transactions"}
    assert "balanceAccountId" in _props(t) and _props(t)["limit"]["type"] == "string"


def test_postman_apikey_header_is_never_an_input(generated):
    for t in output(generated, "adyen")["tools"]:
        assert "X-API-Key" not in json.dumps(t["inputSchema"]), t["name"]


def test_postman_body_schema_inferred_from_the_saved_example(generated):
    body = _props(tool(output(generated, "adyen"), "transfer_between_balance_accounts"))["body"]
    assert body["properties"]["amount"] == {
        "type": "object", "properties": {"value": {"type": "integer"}, "currency": {"type": "string"}}}  # fmt: skip


def test_postman_path_variable_becomes_a_required_input(generated):
    t = tool(output(generated, "adyen"), "get_transfer")
    assert t["operation"]["path"] == "/btl/v4/transfers/{id}"
    assert t["inputSchema"]["required"] == ["id"]


def test_postman_ambiguous_request_must_be_named_exactly(app_catalog):
    catalog = app_catalog("adyen")
    edit_overlay(catalog, "adyen", lambda d: tool(d, "transfer_between_balance_accounts").update(
        operation={"method": "POST", "path": "/transfers"}))  # fmt: skip
    err = run_refused(catalog)
    assert "operation method=POST, path=/transfers matches 8 operations" in err
    assert "name it more exactly" in err


def test_each_app_reads_the_spec_its_provider_pins():
    for app in APPS:
        spec = yaml_record(support.CATALOG, app)["upstream"]["spec"]
        overlay = read_yaml(provider_dir(support.CATALOG, app) / "tools.yaml")
        stored = support.SPECS / overlay["spec"].rsplit("/", 1)[1]
        entry = support.source(stored.name)
        assert (entry["url"], entry["sha256"], entry["format"]) == (spec["url"], spec["sha256"], spec["format"])
        assert hashlib.sha256(gzip.decompress(stored.read_bytes())).hexdigest() == spec["sha256"]
        # A commit-pinned URL serves the same bytes on every fetch.
        assert re.match(r"^https://raw[.]githubusercontent[.]com/[^/]+/[^/]+/[0-9a-f]{40}/", spec["url"]), spec["url"]
