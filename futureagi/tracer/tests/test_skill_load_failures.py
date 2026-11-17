"""Failed loads stay separate from verified loads, with replay-safe scope evidence."""

from datetime import timedelta

import pytest

from tracer.tests.test_skill_trace_links import (
    CONTEXT,
    CONTEXT_VERSION,
    START,
    evidence_rows,
    insert_span,
    load_span,
    skill_client as skill_client,
)

pytestmark = pytest.mark.integration


def failed_span(**overrides):
    return load_span(status="ERROR", span_attributes={
        "fi.skill.name": "policy",
        "fi.skill.requested_label": "production",
        "fi.skill.load.via": "sdk.read_file",
        "error.type": "integrity",
        "gen_ai.agent.name": "reviewer",
        "fi.context.name": "review-context",
        "fi.context.id": CONTEXT,
        "fi.context.version_id": CONTEXT_VERSION,
    }, **overrides)


@pytest.mark.parametrize("writer", ["python", "json_each_row"])
def test_failed_load_has_no_verified_link_and_preserves_scope(skill_client, writer):
    span = failed_span()
    insert_span(skill_client, span, writer)
    assert evidence_rows(skill_client, span) == []
    rows = evidence_rows(skill_client, span, "skill_load_failures")
    assert len(rows) == 1
    row = rows[0]
    expected = {
        "trace_id": span["trace_id"], "span_id": span["id"],
        "skill_name": "policy", "requested_label": "production", "requested_version": 0,
        "load_via": "sdk.read_file", "error_type": "integrity", "service_name": "review-service",
        "agent_name": "reviewer", "context_id": CONTEXT, "context_version_id": CONTEXT_VERSION,
        "is_deleted": 0,
    }
    assert {key: row[key] for key in expected} == expected
    assert "skill_version_id" not in row
    assert "skill_id" not in row
    assert "skill_digest" not in row


@pytest.mark.parametrize("writer", ["python", "json_each_row"])
def test_failure_reversion_retry_and_tombstone(skill_client, writer):
    span = failed_span()
    insert_span(skill_client, span, writer)
    insert_span(skill_client, span, writer)
    assert len(evidence_rows(skill_client, span, "skill_load_failures")) == 1
    newer = {**span, "created_at": START + timedelta(seconds=1), "span_attributes": {
        **span["span_attributes"], "error.type": "authorization",
    }}
    insert_span(skill_client, newer, writer)
    insert_span(skill_client, span, writer)
    assert evidence_rows(skill_client, span, "skill_load_failures")[0]["error_type"] == "authorization"
    tombstone = {**newer, "created_at": START + timedelta(seconds=2), "deleted": True}
    insert_span(skill_client, tombstone, writer)
    insert_span(skill_client, newer, writer)
    assert evidence_rows(skill_client, span, "skill_load_failures") == []
    skill_client.command("OPTIMIZE TABLE skill_load_failures FINAL")
    insert_span(skill_client, span, writer)
    assert evidence_rows(skill_client, span, "skill_load_failures") == []
    assert evidence_rows(skill_client, span) == []


def test_failed_exact_version_and_context_name_fallback(skill_client):
    span = failed_span()
    attrs = span["span_attributes"]
    attrs.pop("gen_ai.agent.name")
    attrs.pop("fi.skill.requested_label")
    attrs["fi.skill.requested_version"] = 3
    insert_span(skill_client, span)
    row = evidence_rows(skill_client, span, "skill_load_failures")[0]
    assert (row["requested_label"], row["requested_version"]) == ("", 3)
    assert row["agent_name"] == "review-context"
    assert evidence_rows(skill_client, span) == []


@pytest.mark.parametrize("changes", [{"status": "OK"}, {"name": "context.load"}, {"name": "llm.chat"}])
def test_failure_requires_skill_load_and_error_status(skill_client, changes):
    span = {**failed_span(), **changes}
    insert_span(skill_client, span)
    assert evidence_rows(skill_client, span, "skill_load_failures") == []
    assert evidence_rows(skill_client, span) == []


def test_same_name_in_another_project_stays_separate(skill_client):
    first, other = failed_span(), failed_span()
    insert_span(skill_client, first)
    insert_span(skill_client, other)
    rows = list(skill_client.query(
        "SELECT toString(project_id), trace_id FROM skill_load_failures FINAL "
        "WHERE project_id = %(p)s AND skill_name = 'policy' AND is_deleted = 0",
        parameters={"p": first["project_id"]},
    ).result_rows)
    assert rows == [(first["project_id"], first["trace_id"])]
