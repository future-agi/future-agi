"""Failed loads stay separate from verified loads, with replay-safe scope evidence."""

from datetime import timedelta

import pytest

from tracer.services.clickhouse.v2.query_builders.skill_runtime import (
    SkillRuntimeQueryBuilder,
)
from tracer.tests.test_skill_trace_links import (
    CONTEXT,
    CONTEXT_VERSION,
    SKILL,
    START,
    evidence_rows,
    insert_span,
    load_span,
)
from tracer.tests.test_skill_trace_links import (
    skill_client as skill_client,
)

pytestmark = pytest.mark.integration


def failed_span(**overrides):
    return load_span(
        status="ERROR",
        span_attributes={
            "fi.skill.name": "policy",
            "fi.skill.requested_label": "production",
            "fi.skill.load.via": "sdk.read_file",
            "error.type": "integrity",
            "gen_ai.agent.name": "reviewer",
            "fi.context.name": "review-context",
            "fi.context.id": CONTEXT,
            "fi.context.version_id": CONTEXT_VERSION,
        },
        **overrides,
    )


@pytest.mark.parametrize("writer", ["python", "json_each_row"])
def test_failed_load_has_no_verified_link_and_preserves_scope(skill_client, writer):
    span = failed_span()
    span["span_attributes"].update(
        {
            "fi.skill.id": SKILL,
            "fi.skill.digest": "sha256:" + "a" * 64,
            "fi.skill.version": 3,
        }
    )
    insert_span(skill_client, span, writer)
    assert evidence_rows(skill_client, span) == []
    rows = evidence_rows(skill_client, span, failed=True)
    assert len(rows) == 1
    row = rows[0]
    expected = {
        "trace_id": span["trace_id"],
        "span_id": span["id"],
        "skill_name": "policy",
        "requested_label": "production",
        "requested_version": 0,
        "load_via": "sdk.read_file",
        "error_type": "integrity",
        "service_name": "review-service",
        "agent_name": "reviewer",
        "context_id": CONTEXT,
        "context_version_id": CONTEXT_VERSION,
        "is_deleted": 0,
    }
    assert {key: row[key] for key in expected} == expected
    identity_fields = {"skill_id", "skill_version_id", "skill_digest", "skill_version"}
    assert identity_fields.isdisjoint(row)
    query = SkillRuntimeQueryBuilder(
        project_ids=[span["project_id"]], start=START, end=START + timedelta(hours=1)
    )
    sql, params = query.build_usage_loads(
        identities=[], skill_names=["policy"], failed=True
    )
    (usage,) = skill_client.query(sql, parameters=params).named_results()
    assert usage["span_id"] == span["id"]
    assert identity_fields.isdisjoint(usage)
    sql, params = query.build_trace_loads(trace_ids=[span["trace_id"]], failed=True)
    (hydrated,) = skill_client.query(sql, parameters=params).named_results()
    assert hydrated["span_id"] == span["id"]
    assert identity_fields.isdisjoint(hydrated)
    sql, params = query.build_trace_loads(trace_ids=[span["trace_id"]])
    assert skill_client.query(sql, parameters=params).result_rows == []


@pytest.mark.parametrize("writer", ["python", "json_each_row"])
@pytest.mark.parametrize("key_only", [False, True], ids=["full-row", "key-only"])
def test_failure_reversion_retry_and_tombstone(skill_client, writer, key_only):
    span = failed_span()
    insert_span(skill_client, span, writer)
    insert_span(skill_client, span, writer)
    assert len(evidence_rows(skill_client, span, failed=True)) == 1
    newer = {
        **span,
        "created_at": START + timedelta(seconds=1),
        "span_attributes": {
            **span["span_attributes"],
            "error.type": "authorization",
        },
    }
    insert_span(skill_client, newer, writer)
    insert_span(skill_client, span, writer)
    (row,) = evidence_rows(skill_client, span, failed=True)
    assert row["error_type"] == "authorization"
    tombstone = {**newer, "created_at": START + timedelta(seconds=2), "deleted": True}
    if key_only:
        tombstone.update(name="", span_attributes={})
    insert_span(skill_client, tombstone, writer)
    insert_span(skill_client, newer, writer)
    assert evidence_rows(skill_client, span, failed=True) == []
    skill_client.command("OPTIMIZE TABLE spans FINAL")
    insert_span(skill_client, span, writer)
    assert evidence_rows(skill_client, span, failed=True) == []
    assert evidence_rows(skill_client, span) == []


def test_failed_exact_version_and_context_name_fallback(skill_client):
    span = failed_span()
    attrs = span["span_attributes"]
    attrs.pop("gen_ai.agent.name")
    attrs.pop("fi.skill.requested_label")
    attrs["fi.skill.requested_version"] = 3
    insert_span(skill_client, span)
    row = evidence_rows(skill_client, span, failed=True)[0]
    assert (row["requested_label"], row["requested_version"]) == ("", 3)
    assert row["agent_name"] == "review-context"
    assert evidence_rows(skill_client, span) == []


@pytest.mark.parametrize(
    "changes", [{"status": "OK"}, {"name": "context.load"}, {"name": "llm.chat"}]
)
def test_failure_requires_skill_load_and_error_status(skill_client, changes):
    span = {**failed_span(), **changes}
    insert_span(skill_client, span)
    assert evidence_rows(skill_client, span, failed=True) == []
    assert evidence_rows(skill_client, span) == []


def test_same_name_in_another_project_stays_separate(skill_client):
    first = failed_span()
    other = failed_span(trace_id=first["trace_id"], id=first["id"])
    insert_span(skill_client, first)
    insert_span(skill_client, other)
    (row,) = evidence_rows(skill_client, first, failed=True)
    assert str(row["project_id"]) == first["project_id"]
    assert row["skill_name"] == "policy"


@pytest.mark.parametrize("writer", ["python", "json_each_row"])
def test_latest_error_without_version_reclassifies_load(skill_client, writer):
    loaded = load_span()
    insert_span(skill_client, loaded, writer)
    assert len(evidence_rows(skill_client, loaded)) == 1
    assert evidence_rows(skill_client, loaded, failed=True) == []

    failed = failed_span(
        project_id=loaded["project_id"],
        trace_id=loaded["trace_id"],
        id=loaded["id"],
        created_at=START + timedelta(seconds=1),
    )
    insert_span(skill_client, failed, writer)
    insert_span(skill_client, loaded, writer)
    assert evidence_rows(skill_client, loaded) == []
    (row,) = evidence_rows(skill_client, loaded, failed=True)
    assert (row["span_id"], row["error_type"]) == (loaded["id"], "integrity")
    assert "skill_version_id" not in row
