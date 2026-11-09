"""Ledger schema annotations describe the served envelopes and query bounds."""
import json

import pytest

from ee.usage.serializers.ledger_openapi import EVENT, READ, contract_schema
from ee.usage.services.ledger_reads import ENTRY_PARAMS, INVOICE_PARAMS, USAGE_PARAMS
from ee.usage.views.ledger_ingest import UsageEventIngestView
from ee.usage.views.ledger_read import (
    BalanceView, EntriesView, InvoicesView, UsageView,
    WorkspaceEntriesView, WorkspaceUsageView,
)


def test_ingest_has_a_bounded_body_and_http_200_response():
    schema = UsageEventIngestView.post._swagger_auto_schema
    request = schema["request_body"]
    assert request["required"] == ["events"]
    assert request["properties"]["events"]["minItems"] == 1
    assert request["properties"]["events"]["maxItems"] == 500
    assert 200 in schema["responses"]
    assert 201 not in schema["responses"]
    assert {400, 401, 403, 413, 503}.issubset(schema["responses"])
    assert schema["security"] == [{"LedgerServiceCredential": []}]
    from django.conf import settings
    auth = settings.SWAGGER_SETTINGS["SECURITY_DEFINITIONS"]["LedgerServiceCredential"]
    assert (auth["type"], auth["in"], auth["name"]) == ("apiKey", "header", "Authorization")
    assert "Bearer" in auth["description"]
    item = request["properties"]["events"]["items"]
    assert item["required"] == EVENT["$defs"]["Event"]["required"]
    assert set(item["properties"]) == set(EVENT["$defs"]["Event"]["properties"])
    assert item["additionalProperties"] is False
    assert item["properties"]["source"]["type"] == "object"
    assert item["properties"]["attribution"]["type"] == "object"
    assert "ledger service credential" in schema["operation_description"]
    result = schema["responses"][200]["schema"]["properties"]["results"]["items"]
    assert result["properties"]["status"]["enum"] == ["accepted", "duplicate", "refused"]
    assert result["properties"]["event_id"]["x-nullable"] is True
    assert result["properties"]["reason"]["enum"] == EVENT["$defs"]["RefusalReason"]["enum"]


@pytest.mark.parametrize("view, names", [
    (BalanceView, set()), (EntriesView, ENTRY_PARAMS),
    (WorkspaceEntriesView, ENTRY_PARAMS), (UsageView, USAGE_PARAMS),
    (WorkspaceUsageView, USAGE_PARAMS), (InvoicesView, INVOICE_PARAMS),
])
def test_read_query_names_and_error_envelope_match_runtime(view, names):
    schema = view.get._swagger_auto_schema
    assert {p["name"] for p in schema["manual_parameters"] if p["in"] == "query"} == set(names) | {"format"}
    format_parameter = next(p for p in schema["manual_parameters"] if p["name"] == "format")
    assert "json" in format_parameter["enum"]
    assert ("csv" in format_parameter["enum"]) == (view not in (BalanceView, InvoicesView))
    other = [p for p in schema["manual_parameters"] if p["in"] != "query"]
    if view.workspace_route:
        assert {(p["name"], p["in"], p["format"], p["required"]) for p in other} == {
            ("workspace_id", "path", "uuid", True), ("X-Workspace-Id", "header", "uuid", False),
        }
    else:
        assert other == []
    assert {200, 400, 401, 403, 404, 503, "default"}.issubset(schema["responses"])
    for code in (400, 401, 403, 404, 503, "default"):
        error = schema["responses"][code]["schema"]
        assert error["required"] == ["error"]
        assert error["properties"]["error"]["type"] == "object"
        assert error["properties"]["error"]["required"] == ["code", "message"]
    assert schema["responses"][200]["schema"]["type"] == "object"


def test_public_ledger_descriptions_do_not_expose_internal_or_ui_notation():
    from agentcc.serializers.managed_credits import SpendByModelSerializer
    from agentcc.serializers.managed_models import ManagedModelsStatusResultSerializer
    from agentcc.views.managed_credits import ManagedCreditGrantView, ManagedCreditAdjustView

    public = json.dumps({name: contract_schema(READ, name) for name in ("Balance", "Usage")})
    public += json.dumps([
        field.help_text
        for serializer in (SpendByModelSerializer, ManagedModelsStatusResultSerializer)
        for field in serializer().fields.values()
    ])
    public += json.dumps([view.post.__doc__ for view in (ManagedCreditGrantView, ManagedCreditAdjustView)])
    for notation in ("Add credits", "credits-v1", "I1 watermark", "The page labels",
                     "staff or the admin token", "(usage_estimated)", "org config managed_models.enabled"):
        assert notation not in public


def test_entry_kinds_and_nullable_fields_come_from_authoritative_contract():
    entry = contract_schema(READ, "Entry")
    assert entry["properties"]["kind"]["enum"] == READ["$defs"]["Kind"]["enum"]
    assert "usage_debit" in entry["properties"]["kind"]["enum"]
    assert entry["properties"]["workspace_id"]["x-nullable"] is True
    assert "x-jsonSchemaConstraints" in entry
    assert "#/$defs/" not in json.dumps(entry)


def test_projected_ledger_constraints_have_no_dangling_document_references():
    entry = contract_schema(READ, "Entry")
    assert "#/$defs/" not in json.dumps(entry)
    assert any(key.startswith("x-") for key in entry)


@pytest.mark.parametrize("view, maximum, default", [(EntriesView, 200, 50), (InvoicesView, 100, 25)])
def test_page_bounds(view, maximum, default):
    limit = next(p for p in view.get._swagger_auto_schema["manual_parameters"] if p["name"] == "limit")
    assert (limit["minimum"], limit["maximum"], limit["default"]) == (1, maximum, default)
