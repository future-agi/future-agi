"""ClickHouse load-evidence contracts. Cloud-only, isolated owned databases.

The adapter path uses the existing seed helper; the JSONEachRow path keeps
attributes_extra as an object exactly as the collector converter/writer do.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from conftest import (
    _ch_test_apply_v2_schema,
    _ch_test_owned_database,
    _open_ch_test_http_client,
)
from tracer.services.clickhouse.query_builders.filters import SKILL_VERSION_IDS_SQL
from tracer.services.clickhouse.v2.adapter import CH_INSERT_COLUMNS, adapt, row_to_tuple
from tracer.services.clickhouse.v2.query_builders.filters import (
    ClickHouseFilterBuilderV2,
)
from tracer.tests._ch_seed import seed_ch_spans

pytestmark = pytest.mark.integration
START = datetime(2026, 11, 1, 12, tzinfo=UTC)
VERSION = "11111111-1111-4111-8111-111111111111"
SKILL = "22222222-2222-4222-8222-222222222222"
CONTEXT = "33333333-3333-4333-8333-333333333333"
CONTEXT_VERSION = "44444444-4444-4444-8444-444444444444"
BINDING = "55555555-5555-4555-8555-555555555555"


@pytest.fixture(scope="module")
def skill_client():
    with _ch_test_owned_database("test_skill_loads_") as database:
        _ch_test_apply_v2_schema(database)
        client = _open_ch_test_http_client(database=database)
        try:
            yield client
        finally:
            client.close()


def assert_no_skill_objects(client):
    installed = {name for (name,) in client.query("SHOW TABLES").result_rows}
    assert {
        "skill_trace_links",
        "skill_trace_links_mv",
        "skill_load_failures",
        "skill_load_failures_mv",
    }.isdisjoint(installed)


def test_packaged_schema_creates_no_skill_objects(skill_client):
    assert_no_skill_objects(skill_client)


def load_span(**overrides):
    return {
        "project_id": str(uuid4()),
        "trace_id": str(uuid4()),
        "id": uuid4().hex[:16],
        "name": "skill.load",
        "observation_type": "unknown",
        "resource_attributes": {"service.name": "review-service"},
        "start_time": START,
        "end_time": START + timedelta(milliseconds=2),
        "created_at": START,
        "updated_at": START,
        "status": "OK",
        "span_attributes": {
            "fi.skill.id": SKILL,
            "fi.skill.version_id": VERSION,
            "fi.skill.name": "policy",
            "fi.skill.version": 3,
            "fi.skill.digest": "sha256:" + "a" * 64,
            "fi.skill.requested_version": 3,
            "fi.skill.load.via": "context.read",
            "fi.skill.load.evidence": "delivered",
            "fi.skill.load.cached": True,
            "fi.skill.load.root_file": False,
            "fi.skill.via_context.version_id": CONTEXT_VERSION,
            "gen_ai.agent.name": "reviewer",
            "fi.context.name": "review-context",
            "fi.context.id": CONTEXT,
            "fi.context.version_id": CONTEXT_VERSION,
            "fi.context.binding_id": BINDING,
            "fi.skills.version_ids": [VERSION],
        },
        **overrides,
    }


def insert_span(client, span, writer="python"):
    if writer == "python":
        seed_ch_spans([span], client=client, version_from_created_at=True)
        return
    assert writer == "json_each_row"
    row = dict(zip(CH_INSERT_COLUMNS, row_to_tuple(adapt(span)), strict=True))
    # Match Convert -> chwriter's JSON-natural payload, not the native helper's
    # String encoding. This pins migration 013's flat dotted key preservation.
    for key in ("attributes_extra", "resource_attrs", "metadata"):
        row[key] = json.loads(row[key])
    for key, value in row.items():
        if isinstance(value, datetime):
            row[key] = value.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")
    delta = span["created_at"] - datetime(1970, 1, 1, tzinfo=UTC)
    row["_version"] = (
        (delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds
    ) * 1000
    client.raw_insert("spans", insert_block=json.dumps(row).encode(), fmt="JSONEachRow")


def evidence_rows(client, span, *, failed=False):
    project = span["project_id"]
    source = ClickHouseFilterBuilderV2(
        project_id=project, candidate_ids_param="traces"
    ).skill_live_rows_sql(failed=failed)
    return list(
        client.query(
            source,
            parameters={
                "project_id": project,
                "traces": (span["trace_id"],),
                "start_date": START,
                "end_date": START + timedelta(hours=1),
            },
        ).named_results()
    )


@pytest.mark.parametrize("writer", ["python", "json_each_row"])
def test_load_populates_correlated_evidence_and_indexed_list(skill_client, writer):
    span = load_span()
    insert_span(skill_client, span, writer)
    rows = evidence_rows(skill_client, span)
    assert len(rows) == 1
    row = rows[0]
    expected = {
        "span_id": span["id"],
        "trace_id": span["trace_id"],
        "skill_id": SKILL,
        "skill_version_id": VERSION,
        "skill_name": "policy",
        "skill_version": 3,
        "skill_digest": "sha256:" + "a" * 64,
        "requested_label": "",
        "requested_version": 3,
        "load_via": "context.read",
        "evidence": "delivered",
        "cached": 1,
        "root_file": 0,
        "status": "OK",
        "service_name": "review-service",
        "agent_name": "reviewer",
        "context_id": CONTEXT,
        "context_version_id": CONTEXT_VERSION,
        "via_context_version_id": CONTEXT_VERSION,
        "binding_id": BINDING,
        "is_deleted": 0,
    }
    assert {key: row[key] for key in expected} == expected
    assert evidence_rows(skill_client, span, failed=True) == []
    # Scope is read from span attributes; spans gains no column or index.
    result = skill_client.query(
        f"SELECT {SKILL_VERSION_IDS_SQL} FROM spans WHERE project_id = %(p)s AND id = %(id)s",
        parameters={"p": span["project_id"], "id": span["id"]},
    ).result_rows
    assert result == [([VERSION],)]
    added = skill_client.query(
        "SELECT count() FROM system.columns WHERE database = currentDatabase() "
        "AND table = 'spans' AND name = 'skill_version_ids'",
    ).result_rows
    assert added == [(0,)]


@pytest.mark.parametrize("writer", ["python", "json_each_row"])
@pytest.mark.parametrize("key_only", [False, True], ids=["full-row", "key-only"])
def test_full_row_reversion_retry_and_tombstone(skill_client, writer, key_only):
    span = load_span()
    insert_span(skill_client, span, writer)
    insert_span(skill_client, span, writer)
    assert len(evidence_rows(skill_client, span)) == 1
    newer = {
        **span,
        "created_at": START + timedelta(seconds=1),
        "span_attributes": {
            **span["span_attributes"],
            "fi.skill.load.cached": False,
            "fi.skill.load.root_file": True,
        },
    }
    insert_span(skill_client, newer, writer)
    row = evidence_rows(skill_client, span)[0]
    assert (row["cached"], row["root_file"]) == (0, 1)
    # An out-of-order original row cannot overwrite the later revision.
    insert_span(skill_client, span, writer)
    assert evidence_rows(skill_client, span)[0]["cached"] == 0
    tombstone = {**newer, "created_at": START + timedelta(seconds=2), "deleted": True}
    if key_only:
        tombstone.update(name="", span_attributes={})
    insert_span(skill_client, tombstone, writer)
    insert_span(skill_client, newer, writer)
    assert evidence_rows(skill_client, span) == []
    # Physical replacement must not resurrect the replayed load.
    skill_client.command("OPTIMIZE TABLE spans FINAL")
    insert_span(skill_client, span, writer)
    assert evidence_rows(skill_client, span) == []


@pytest.mark.parametrize(
    "context,expected", [("review-context", "review-context"), (None, "")]
)
def test_agent_fallback_and_label_selector(skill_client, context, expected):
    span = load_span()
    attrs = span["span_attributes"]
    attrs.pop("gen_ai.agent.name")
    attrs.pop("fi.skill.requested_version")
    attrs["fi.skill.requested_label"] = "production"
    if context is None:
        attrs.pop("fi.context.name")
    insert_span(skill_client, span)
    row = evidence_rows(skill_client, span)[0]
    assert row["agent_name"] == expected
    assert row["requested_label"] == "production"
    assert row["requested_version"] == 0


@pytest.mark.parametrize("name", ["context.load", "llm.chat"])
def test_non_load_spans_never_create_evidence(skill_client, name):
    span = load_span(name=name)
    insert_span(skill_client, span)
    assert evidence_rows(skill_client, span) == []
    assert evidence_rows(skill_client, span, failed=True) == []
