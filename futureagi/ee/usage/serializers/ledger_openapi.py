"""Swagger projection of the authoritative ledger JSON contracts.

Swagger 2 cannot express conditional JSON Schema constraints. Those remain
attached as x-jsonSchemaConstraints rather than being silently discarded.
Runtime validation continues to use the authoritative contracts.
"""
from __future__ import annotations

import json
from pathlib import Path

from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from tfc.utils.api_contracts import ManagementAPIAutoSchema

CONTRACTS = Path(__file__).resolve().parents[1] / "contracts"
READ = json.loads((CONTRACTS / "billing-ledger-read-v1.schema.json").read_text())
EVENT = json.loads((CONTRACTS / "usage-event-v1.schema.json").read_text())


def _inline_constraints(value, definitions):
    """Keep conditional rules without references outside the Swagger document."""
    if isinstance(value, list):
        return [_inline_constraints(item, definitions) for item in value]
    if not isinstance(value, dict):
        return value
    value = dict(value)
    if "$ref" in value:
        reference = value.pop("$ref")
        if not reference.startswith("#/$defs/"):
            raise ValueError("Unsupported external ledger constraint reference")
        value = {**definitions[reference.removeprefix("#/$defs/")], **value}
    return {key: _inline_constraints(item, definitions) for key, item in value.items()}


def _schema(source, definitions):
    source = dict(source)
    if "$ref" in source:
        ref = source.pop("$ref")
        if not ref.startswith("#/$defs/"):
            raise ValueError("Ledger operation uses an unsupported external schema reference")
        source = {**definitions[ref.removeprefix("#/$defs/")], **source}
    if "anyOf" in source:
        variants = source.pop("anyOf")
        concrete = [item for item in variants if item.get("type") != "null"]
        if len(concrete) != 1 or len(variants) != 2:
            raise ValueError("Only explicit nullable unions are projected to Swagger 2")
        return _nullable(_schema({**concrete[0], **source}, definitions))
    if "const" in source:
        value = source.pop("const")
        source["enum"] = [value]
        source.setdefault("type", "boolean" if isinstance(value, bool) else "integer" if isinstance(value, int) else "string")
    constraints = {key: source.pop(key) for key in ("allOf", "not", "contains", "propertyNames", "maxProperties") if key in source}
    if constraints:
        source["x_json_schema_constraints"] = _inline_constraints(constraints, definitions)
    properties = source.pop("properties", None)
    if properties is not None:
        source["properties"] = {key: _schema(value, definitions) for key, value in properties.items()}
    if "items" in source:
        source["items"] = _schema(source["items"], definitions)
    if isinstance(source.get("additionalProperties"), dict):
        source["additionalProperties"] = _schema(source["additionalProperties"], definitions)
    if source.get("type") == "string" and source.get("pattern") == READ["$defs"]["Uuid"]["pattern"]:
        source["format"] = "uuid"
    return openapi.Schema(**source)


def _nullable(schema):
    schema["x-nullable"] = True
    return schema


def contract_schema(document, name):
    return _schema(document["$defs"][name], document["$defs"])


def errors(*codes):
    return {code: openapi.Response("Ledger error: error.code and error.message.", contract_schema(READ, "Error")) for code in (*codes, "default")}


def _query(name, description, *, type=openapi.TYPE_STRING, **kwargs):
    return openapi.Parameter(name, openapi.IN_QUERY, description=description, type=type, required=False, **kwargs)


def _csv_list(name, description, values):
    return _query(name, description, type=openapi.TYPE_ARRAY,
                  items=openapi.Items(type=openapi.TYPE_STRING, enum=values), collection_format="csv")


PERIOD = _query("period", "UTC billing month; defaults to the current month.", pattern=r"^[0-9]{4}-(0[1-9]|1[0-2])$")
CURSOR = _query("cursor", "Opaque cursor from the prior response; bound to the same filters.")
FORMAT = _query("format", "Omit or use json for JSON; csv returns all matching rows as a CSV document.", enum=["json", "csv"])
JSON_FORMAT = _query("format", "Omit or use json for JSON.", enum=["json"])
PRODUCT = _query("product", "Filter by product.")
ENTRY_QUERY = [
    _query("from", "Inclusive timestamp, with a timezone offset.", format="date-time"),
    _query("to", "Exclusive timestamp, with a timezone offset.", format="date-time"),
    PERIOD,
    _csv_list("kind", "Entry kinds, comma-separated.", READ["$defs"]["Kind"]["enum"]),
    PRODUCT,
    *[_query(name, "Exact-match filter.") for name in ("dimension", "funding", "agent_id", "api_key_id", "purpose")],
    CURSOR,
    _query("limit", "JSON page size.", type=openapi.TYPE_INTEGER, minimum=1, maximum=200, default=50),
    FORMAT,
]
USAGE_QUERY = [
    PERIOD,
    _csv_list("group_by", "Grouping keys, comma-separated; product is always included.",
              ["product", "dimension", "workspace", "agent", "api_key", "purpose", "late"]),
    PRODUCT, FORMAT,
]
INVOICE_QUERY = [
    CURSOR,
    _query("limit", "JSON page size.", type=openapi.TYPE_INTEGER, minimum=1, maximum=100, default=25),
    _csv_list("kind", "Invoice kinds, comma-separated.", ["usage", "plan", "topup", "one_off", "legacy"]),
]


class LedgerJSONAutoSchema(ManagementAPIAutoSchema):
    def get_produces(self):
        return ["application/json"]


def read_contract(name, query=(), *, workspace=False, csv=False):
    description = ("Read the authenticated organization's billing ledger. Organization Owner or Admin access is required. "
                   "No payment-provider request is made. Unknown query parameters are rejected. ")
    if workspace:
        description += "A workspace administrator may read this workspace. A foreign workspace is not found; a conflicting X-Workspace-Id is forbidden. "
    if csv:
        description += "The 200 schema describes JSON; format=csv returns a CSV document instead. "
    else:
        description += "JSON only; format=csv is not supported. "
    return swagger_auto_schema(
        operation_description=description,
        **({} if csv else {"auto_schema": LedgerJSONAutoSchema}),
        manual_parameters=list(query) + ([] if csv else [JSON_FORMAT]) + ([
            openapi.Parameter("workspace_id", openapi.IN_PATH, type=openapi.TYPE_STRING,
                              format="uuid", required=True, description="Workspace within the authenticated organization."),
            openapi.Parameter("X-Workspace-Id", openapi.IN_HEADER, type=openapi.TYPE_STRING,
                              format="uuid", required=False, description="When supplied, must equal the workspace in the path."),
        ] if workspace else []),
        responses={200: openapi.Response(name, contract_schema(READ, name)), **errors(400, 401, 403, 404, 503)},
    )


INGEST_REQUEST = contract_schema(EVENT, "IngestRequest")
# Publish the valid event shape while preserving the batch's per-item refusal
# semantics. Runtime validation still accepts and reports malformed peers.
INGEST_REQUEST["properties"]["events"]["items"] = contract_schema(EVENT, "Event")
INGEST_REQUEST["properties"]["events"]["description"] = (
    "Usage events in request order. Each item is validated independently; "
    "a malformed item is refused without discarding valid peers."
)

INGEST_CONTRACT = swagger_auto_schema(
    operation_description=("Ingest usage events with a ledger service credential in Authorization: Bearer fa_ledger_<credential>. "
                           "Session cookies and personal API keys are not accepted. Each item is validated independently; "
                           "a malformed item is refused without discarding valid peers. A successful batch returns HTTP 200, "
                           "with one result per item in request order. Retry identical event bytes after 503."),
    security=[{"LedgerServiceCredential": []}],
    request_body=INGEST_REQUEST,
    responses={200: openapi.Response("Per-event ingest results", contract_schema(EVENT, "IngestResponse")),
               **errors(400, 401, 403, 413, 503)},
)
