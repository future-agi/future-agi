"""Projection-boundary contracts for existing trace APIs (cloud execution only).

No new tracer route is registered: the lean API review requires these to be
integrated into the existing trace projections by their owning lane.
"""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from model_hub.services.skills_hub.errors import SkillsHubError
from model_hub.services.skills_hub.runtime_usage import trace_load_projection
from tracer.services.clickhouse.v2.query_builders.skill_runtime import SkillRuntimeQueryBuilder

START = datetime(2026, 11, 1, tzinfo=timezone.utc)
CATALOG = {"version": {"skill_id": "skill", "name": "Published name", "version": 3, "digest": "digest"}}


def load(**kwargs):
    return {
        "project_id": "project", "trace_id": "trace", "span_id": "span",
        "skill_id": "skill", "skill_version_id": "version", "skill_name": "client-claimed-name",
        "skill_digest": "digest", "requested_label": "production",
        "service_name": "service", "agent_name": "worker", "evidence": "delivered",
        "start_time": START, **kwargs,
    }


def project(rows, *, failed_rows=(), catalog=CATALOG, trace_ids=("trace",)):
    return trace_load_projection(rows=rows, failed_rows=failed_rows, trace_ids=trace_ids, project_id="project", catalog=catalog)


def test_enrichment_uses_workspace_catalog_not_client_claimed_name():
    result = project([load(), load(span_id="second")])["traces"]["trace"]
    assert result["skills"] == [{
        "skill_id": "skill", "name": "Published name", "version_id": "version", "version": 3,
        "requested_label": "production", "agent": ("project", "service", "worker"),
        "evidence": "delivered", "digest_status": "verified", "loads": 2,
    }]
    assert result["more"] == 0 and result["failed"] == []


@pytest.mark.parametrize("row", [load(skill_version_id="foreign"), load(skill_id="foreign")])
def test_unknown_or_cross_skill_version_has_no_name_or_navigable_identity(row):
    skill = project([row])["traces"]["trace"]["skills"][0]
    assert skill["name"] == "Unknown skill"
    assert skill["skill_id"] is skill["version_id"] is skill["version"] is None
    assert skill["digest_status"] == "mismatch"


def test_digest_mismatch_does_not_merge_with_verified_loads():
    skills = project([load(), load(skill_digest="wrong")])["traces"]["trace"]["skills"]
    assert len(skills) == 2
    assert {row["digest_status"] for row in skills} == {"verified", "mismatch"}
    assert all(row["loads"] == 1 for row in skills)


def test_agent_and_evidence_are_distinct_not_fetch_identities():
    rows = [load(), load(agent_name="other"), load(evidence="agent_read")]
    assert len(project(rows)["traces"]["trace"]["skills"]) == 3


def test_list_caps_at_eight_and_reports_more_in_first_load_order():
    rows = [load(agent_name=f"worker-{i}", start_time=START + timedelta(seconds=i)) for i in reversed(range(11))]
    result = project(rows)["traces"]["trace"]
    assert len(result["skills"]) == 8 and result["more"] == 3
    assert result["skills"][0]["agent"][-1] == "worker-0"


def test_failed_loads_never_acquire_verified_version_identity():
    failed = {"project_id": "project", "trace_id": "trace", "skill_name": "reported-name", "error_type": "integrity"}
    result = project([], failed_rows=[failed, failed])["traces"]["trace"]
    assert result["skills"] == []
    assert result["failed"] == [{"skill_name": "reported-name", "error_type": "integrity", "loads": 2}]


@pytest.mark.parametrize("kwargs", [{"project_id": "private"}, {"trace_id": "not-requested"}])
def test_reader_scope_violation_fails_instead_of_returning_private_row(kwargs):
    with pytest.raises(SkillsHubError) as error:
        project([load(**kwargs)])
    assert error.value.code == "runtime_scope_mismatch"
    assert "private" not in error.value.message


def test_empty_traces_remain_in_batch_and_batch_cap_is_enforced():
    assert project([], trace_ids=("trace", "empty"))["traces"]["empty"] == {"skills": [], "failed": [], "more": 0}
    with pytest.raises(SkillsHubError):
        project([], trace_ids=[str(i) for i in range(101)])


def test_query_hydration_cannot_cross_project_or_omit_trace_bound():
    project_id = str(uuid4())
    query = SkillRuntimeQueryBuilder(project_ids=[project_id], start=START, end=START + timedelta(hours=1))
    sql, params = query.build_trace_loads(trace_ids=["trace'", "second"])
    assert "trace'" not in sql and params["trace_ids"] == ("trace'", "second")
    assert "LIMIT 10001" in sql and "trace_id IN %(trace_ids)s" in sql
    assert sql.count("project_id IN %(project_ids)s") >= 2
    with pytest.raises(ValueError):
        query.build_trace_loads(trace_ids=[])
    with pytest.raises(ValueError):
        query.build_trace_loads(trace_ids=[str(i) for i in range(101)])
    multi = SkillRuntimeQueryBuilder(project_ids=[project_id, str(uuid4())], start=START, end=START + timedelta(hours=1))
    with pytest.raises(ValueError):
        multi.build_trace_loads(trace_ids=["trace"])
