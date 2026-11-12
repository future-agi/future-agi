"""What the generator refuses, and that a refusal writes nothing: an
operation the spec does not have, a tool or input without a description,
a malformed overlay, a spec that is not the pinned one, and secrets."""

from __future__ import annotations

import gzip

import pytest
import support
from conftest import edit_overlay, edit_record, provider_dir, run_ok, run_refused, tool

GMAIL = "apps/gmail/providers/futureagi-rest/tools.yaml"


def _unchanged(catalog, app="gmail"):
    path = provider_dir(catalog, app) / "tools.generated.json"
    return path.read_bytes() == (provider_dir(support.CATALOG, app) / "tools.generated.json").read_bytes()


def test_missing_operation_refused(app_catalog):
    catalog = app_catalog("gmail")
    edit_overlay(catalog, "gmail", lambda d: tool(d, "get_thread").update(operation={"id": "gmail.users.threads.fetch"}))
    err = run_refused(catalog)
    assert f"{GMAIL}: tools[1] (get_thread): operation id=gmail.users.threads.fetch is not in the spec" in err
    assert _unchanged(catalog)


def test_missing_operation_by_method_and_path_refused(app_catalog):
    catalog = app_catalog("microsoft-outlook")
    edit_overlay(catalog, "microsoft-outlook", lambda d: tool(d, "get_message").update(
        operation={"method": "GET", "path": "/me/message/{message-id}"}))  # fmt: skip
    err = run_refused(catalog)
    assert "operation method=GET, path=/me/message/{message-id} is not in the spec" in err


def test_missing_operation_matcher_refused(app_catalog):
    catalog = app_catalog("gmail")
    edit_overlay(catalog, "gmail", lambda d: tool(d, "get_thread").update(operation={}))
    err = run_refused(catalog)
    assert "tools[1] (get_thread).operation: names the operation by id, operationId, name, or method and path" in err


def test_missing_description_refused(app_catalog):
    catalog = app_catalog("gmail")
    edit_overlay(catalog, "gmail", lambda d: tool(d, "list_labels").pop("description"))
    err = run_refused(catalog)
    assert "tools[3] (list_labels): has no description; every tool states what it does, its formats and an example" in err
    assert _unchanged(catalog)


def test_missing_description_never_falls_back_to_the_spec(app_catalog):
    # Graph's summaries are misattributed (GET /me/messages: "Get open
    # extension"), so a tool's description comes only from the overlay.
    catalog = app_catalog("microsoft-outlook")
    edit_overlay(catalog, "microsoft-outlook", lambda d: tool(d, "list_messages").pop("description"))
    err = run_refused(catalog)
    assert "(list_messages): has no description" in err


def test_missing_description_of_a_parameter_refused(app_catalog):
    catalog = app_catalog("slack")
    edit_overlay(catalog, "slack", lambda d: tool(d, "search_messages").pop("params"))
    err = run_refused(catalog)
    assert "(search_messages): parameter page has no description in the spec; add params.describe.page" in err


def test_missing_description_of_a_body_refused(app_catalog):
    catalog = app_catalog("slack")
    edit_overlay(catalog, "slack", lambda d: tool(d, "add_reaction").pop("body"))
    err = run_refused(catalog)
    assert "(add_reaction): the request body has no description in the spec; add body.description" in err


def test_overlay_unknown_key_refused(app_catalog):
    catalog = app_catalog("gmail")
    edit_overlay(catalog, "gmail", lambda d: tool(d, "get_thread").update(steps=[{"id": "gmail.users.messages.get"}]))
    err = run_refused(catalog)
    assert "tools.yaml: tools[1]: unknown key 'steps' (allowed: name, title, description, operation," in err


def test_overlay_null_refused(app_catalog):
    catalog = app_catalog("gmail")
    edit_overlay(catalog, "gmail", lambda d: tool(d, "get_thread").update(title=None))
    err = run_refused(catalog)
    assert "a null value is not allowed; leave the key out instead" in err


@pytest.mark.parametrize("keep", [7, 26])
def test_overlay_selects_8_to_25_operations(app_catalog, keep):
    catalog = app_catalog("gmail")

    def change(d):
        d["tools"] = (d["tools"] * 3)[:keep]
        for i, t in enumerate(d["tools"]):
            t["name"] = f"{t['name']}_{i}"[:40]

    edit_overlay(catalog, "gmail", change)
    err = run_refused(catalog)
    assert f"tools: lists {keep} operations; an overlay selects 8-25" in err


def test_unknown_parameter_named_refused(app_catalog):
    catalog = app_catalog("gmail")
    edit_overlay(catalog, "gmail", lambda d: tool(d, "get_thread")["params"].update(exclude=["format", "pageSize"]))
    err = run_refused(catalog)
    assert "params.exclude names pageSize, which is not a parameter of gmail.users.threads.get" in err


def test_required_parameter_cannot_be_excluded(app_catalog):
    catalog = app_catalog("gmail")
    edit_overlay(catalog, "gmail", lambda d: tool(d, "get_thread")["params"].update(exclude=["id"]))
    err = run_refused(catalog)
    assert "parameter id is required by the operation, so it cannot be excluded" in err


def test_body_projection_naming_an_unknown_field_refused(app_catalog):
    catalog = app_catalog("gmail")
    edit_overlay(catalog, "gmail", lambda d: tool(d, "create_draft")["body"].update(fields=["message.raw", "message.html"]))
    err = run_refused(catalog)
    assert "body.fields names message.html, which the request body does not declare" in err


def test_response_projection_naming_an_unknown_field_refused(app_catalog):
    catalog = app_catalog("gmail")
    edit_overlay(catalog, "gmail", lambda d: tool(d, "list_labels")["response"].update(fields=["labels.id", "labels.colour"]))
    err = run_refused(catalog)
    assert "response.fields labels.colour is not in the success response of gmail.users.labels.list" in err


def test_spec_outside_fixtures_refused(app_catalog):
    catalog = app_catalog("gmail")
    edit_overlay(catalog, "gmail", lambda d: d.update(spec="gmail-v1.discovery.json.gz"))
    err = run_refused(catalog)
    assert "spec: must be a path under fixtures/ in the provider directory" in err


def test_spec_that_is_not_the_pinned_one_refused(app_catalog):
    catalog = app_catalog("gmail")
    spec = provider_dir(catalog, "gmail") / "fixtures/spec/gmail-v1.discovery.json.gz"
    raw = gzip.decompress(spec.read_bytes()).replace(b'"revision": "20261005"', b'"revision": "20261006"', 1)
    spec.write_bytes(gzip.compress(raw))
    err = run_refused(catalog)
    assert "but the provider pins f651441dcc942f428d92768e8b20c94a28ff82bf485b79872d9eef267a894225" in err
    assert _unchanged(catalog)


def test_spec_in_another_format_than_declared_refused(app_catalog):
    catalog = app_catalog("adyen")
    record = provider_dir(catalog, "adyen").parent / "futureagi-rest.yaml"
    edit_record(record, lambda d: d["upstream"]["spec"].update(format="openapi-3"))
    err = run_refused(catalog)
    assert "is declared openapi-3 in upstream.spec.format, but reads as postman-2.1" in err


def test_secret_shaped_spec_refused_by_rule_name_only(app_catalog):
    import hashlib

    catalog = app_catalog("gmail")
    spec = provider_dir(catalog, "gmail") / "fixtures/spec/gmail-v1.discovery.json.gz"
    shape = "xoxb-" + "1234567890" + "-abcdefghij"  # a Slack bot token's shape, not a token
    raw = gzip.decompress(spec.read_bytes()).replace(b'"revision": "20261005"', f'"revision": "{shape}"'.encode(), 1)
    spec.write_bytes(gzip.compress(raw))
    record = provider_dir(catalog, "gmail").parent / "futureagi-rest.yaml"
    edit_record(record, lambda d: d["upstream"]["spec"].update(sha256=hashlib.sha256(raw).hexdigest()))
    err = run_refused(catalog)
    assert "holds a value shaped like a slack-token" in err
    assert shape not in err


def test_secret_shaped_overlay_refused(app_catalog):
    catalog = app_catalog("gmail")
    shape = "AKIA" + "ABCDEFGHIJKLMNOP"
    edit_overlay(catalog, "gmail", lambda d: tool(d, "get_thread").update(title=f"Read {shape}"))
    err = run_refused(catalog)
    assert "holds a value shaped like a aws-access-key-id; overlay data never holds a secret" in err
    assert shape not in err


def test_overlay_beside_a_non_rest_provider_refused(app_catalog):
    catalog = app_catalog("gmail")
    record = provider_dir(catalog, "gmail").parent / "futureagi-rest.yaml"
    edit_record(record, lambda d: d.update(kind="vendor_mcp"))
    err = run_refused(catalog)
    assert "is not a rest provider" in err


def test_secret_rules_match_the_catalog_loader():
    """The generator refuses exactly the shapes CN1's loader refuses."""
    import re

    from restgen import secrets

    go = (support.HERE.parents[3] / "agentcc-gateway/internal/connectorcatalog/manifest/secrets.go").read_text()
    rules = re.findall(r'\{"([a-z-]+)", regexp\.MustCompile\(`([^`]+)`\)\}', go)
    assert rules == [(name, pattern.pattern) for name, pattern in secrets._RULES]


ADYEN_RECORD = "apps/adyen/providers/futureagi-rest.yaml"


def _parts(*parts: dict):
    return lambda d: d["auth"].update(credentialParts=list(parts))


def _part(where: str, name: str, **extra) -> dict:
    return {"in": where, "name": name, "secret": True, "label": "API key", **extra}


@pytest.mark.parametrize(
    "part",
    [_part("query", "limit"), _part("query", "LIMIT"), _part("query", "limit", secret=False, value="v4")],
    ids=["secret part", "any case", "constant"],
)
def test_query_input_under_a_credential_name_refused(app_catalog, part):
    # IR-CN10b-3: agentcc-connectors refuses at load a query input that goes out under the name of a
    # credential part or constant the provider places in the query (compared without case), so the
    # vendor never gets a model-chosen value beside the user's key under the key's name. The generator
    # refuses it first, and writes nothing.
    catalog = app_catalog("adyen")
    edit_record(catalog / ADYEN_RECORD, _parts(part))
    err = run_refused(catalog)
    assert "(list_transactions): parameter limit would go out as query parameter limit" in err
    assert "(list_transfers): parameter limit would go out as query parameter limit" in err
    assert _unchanged(catalog, "adyen")


def test_renamed_query_input_keeps_its_wire_name_for_the_clash(app_catalog):
    # The runtime compares the name on the wire (x-wireName), not the model-facing one.
    catalog = app_catalog("adyen")
    edit_record(catalog / ADYEN_RECORD, _parts(_part("query", "limit")))
    rename = {"limit": "pageSize"}
    edit_overlay(catalog, "adyen", lambda d: tool(d, "list_transfers").setdefault("params", {}).update(rename=rename))
    err = run_refused(catalog)
    assert "(list_transfers): parameter limit would go out as query parameter limit" in err


def test_a_header_part_of_the_same_name_is_no_clash(app_catalog):
    # The runtime sends a header part as a header, so a query input of its name is no clash.
    catalog = app_catalog("adyen")
    edit_record(catalog / ADYEN_RECORD, _parts(_part("header", "limit")))
    run_ok(catalog, "--check")


def test_a_listed_credential_param_is_no_clash(app_catalog):
    # A parameter the overlay lists in credentialParams is the credential itself, never an input.
    catalog = app_catalog("adyen")
    edit_record(catalog / ADYEN_RECORD, _parts(_part("query", "limit")))
    edit_overlay(catalog, "adyen", lambda d: d.update(credentialParams=["limit"]))
    code, out, err = support.run("--catalog", str(catalog))
    assert code == 0 and "would go out as query parameter" not in out + err, out + err
