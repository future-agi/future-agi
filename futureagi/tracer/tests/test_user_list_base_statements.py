"""Users SQL contract and immutable historical entry points (no source authority)."""

import ast
import hashlib
import json
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest

from tracer.services.clickhouse.v2.query_builders.user_list import (
    UserListQueryBuilderV2,
)
from tracer.services.derived_metrics.base_list_current import CurrentFactsRead
from tracer.tests._base_list_parity import (
    FixtureAuthority,
    FixtureCurrentFactsRead,
    filters,
    fixture_current_facts,
)

ORG, PROJECT = str(UUID(int=10)), str(UUID(int=20))
START, END = datetime(2026, 10, 1, tzinfo=UTC), datetime(2026, 11, 1, tzinfo=UTC)
ORACLE = Path(__file__).parent / "fixtures/base_list_parity/users_old_builder.json"


def old_user_builder():
    fixture = json.loads(ORACLE.read_text())
    assert (
        fixture["header"]["source_commit"] == "8ddeb72ab0fa8d2f46dc38c212252f9ebb0d35ca"
    )
    sources = fixture["builders"]
    for entry in sources.values():
        assert hashlib.sha256(entry["source"].encode()).hexdigest() == entry["sha256"]
    namespace = {"__name__": "baseline_user_builder"}
    exec(
        compile(
            sources["query_builders/user_list.py"]["source"], "baseline_users", "exec"
        ),
        namespace,
    )
    tree = ast.parse(sources["v2/query_builders/user_list.py"]["source"])
    tree.body = [
        node
        for node in tree.body
        if not (
            isinstance(node, ast.ImportFrom)
            and node.module == "tracer.services.clickhouse.query_builders.user_list"
        )
    ]
    exec(compile(tree, "baseline_users_v2", "exec"), namespace)
    return namespace["UserListQueryBuilderV2"]


class UserFixtureRead(FixtureCurrentFactsRead):
    """Synthetic immutable fixture only; cannot qualify a production request."""

    def permits_user_activity(self, project_id):
        return self.permits(project_id) and any(
            batch["table"] == "list_current_user_activity"
            for batch in self.manifest.visibility_receipts.get("batches", [])
        )


@contextmanager
def user_fixture_pin(writer, authority):
    with fixture_current_facts(
        writer.store,
        project_id=writer.lease.project_id,
        org_id=writer.org_id,
        authority=authority,
    ) as base:
        read = UserFixtureRead(base.manifest, base.org_id, base.authority)
        try:
            yield read
        finally:
            read.active = False


def fixture_read(**proof):
    manifest = SimpleNamespace(
        id="fixture",
        project_id=PROJECT,
        remap_revision=1,
        revoked_at=None,
        checkpoint_attempt_id=None,
        delta_attempt_ids=[],
        allowed_attempts=lambda: [(1, str(UUID(int=1000)))],
        visibility_receipts={"batches": [{"table": "list_current_user_activity"}]},
    )
    authority = FixtureAuthority(
        **{
            "manifest_id": "fixture",
            "remap_revision": 1,
            "complete_retention_coverage": True,
            "fresh": True,
            "unique_physical_versions": True,
            "anchor_authoritative": True,
            **proof,
        }
    )
    return UserFixtureRead(manifest, ORG, authority)


def builder(**kwargs):
    return UserListQueryBuilderV2(
        **{
            "organization_id": ORG,
            "project_id": PROJECT,
            "filters": filters(START, END),
            "limit": 26,
            "offset": 0,
            **kwargs,
        }
    )


def test_native_entry_point_is_historical_oracle_without_a_pin():
    native = builder()
    old = old_user_builder()(
        organization_id=ORG,
        project_id=PROJECT,
        filters=filters(START, END),
        limit=26,
        offset=0,
    )
    # Sharing the curated SQL is a whitespace-only change to the native reader.
    assert " ".join(native.build_candidate_page_query()[0].split()) == " ".join(
        old.build_candidate_page_query()[0].split()
    )
    assert native.build_candidate_page_query()[1] == old.build_candidate_page_query()[1]


@pytest.mark.parametrize("enabled", [0, 1])
def test_production_foundation_cannot_claim_coverage(settings, enabled):
    settings.METRICS_ENABLED = enabled
    native = builder()
    baseline = native.build_candidate_page_query()
    read = fixture_read()
    native.bind_current_facts(CurrentFactsRead(read.manifest, ORG))
    assert native.build_candidate_page_query() == baseline
    assert not native._current_facts.permits_user_activity(PROJECT)


def test_flag_off_preserves_native_even_for_test_pin(settings):
    settings.METRICS_ENABLED = 0
    native = builder()
    baseline = native.build_candidate_page_query()
    native.bind_current_facts(fixture_read())
    assert native.build_candidate_page_query() == baseline


@pytest.mark.parametrize(
    "proof",
    [
        {"fresh": False},
        {"complete_retention_coverage": False},
        {"unique_physical_versions": False},
        {"remap_revision": 2},
    ],
)
def test_unproven_state_falls_back(settings, proof):
    settings.METRICS_ENABLED = 1
    native = builder()
    baseline = native.build_candidate_page_query()
    native.bind_current_facts(fixture_read(**proof))
    assert native.build_candidate_page_query() == baseline


@pytest.mark.parametrize(
    "change", ["expired", "wrong_org", "missing_activity_receipt", "revoked"]
)
def test_invalid_pin_falls_back(settings, change):
    settings.METRICS_ENABLED = 1
    native = builder()
    baseline = native.build_candidate_page_query()
    read = fixture_read()
    if change == "expired":
        read.active = False
    elif change == "wrong_org":
        read.org_id = str(UUID(int=999))
    elif change == "missing_activity_receipt":
        read.manifest.visibility_receipts = {}
    else:
        read.manifest.revoked_at = END
    native.bind_current_facts(read)
    assert native.build_candidate_page_query() == baseline


@pytest.mark.parametrize(
    "kwargs",
    [
        {"project_ids": [PROJECT, str(UUID(int=30))]},
        {"sort_params": [{"column_id": "total_cost", "direction": "desc"}]},
        {
            "filters": filters(START, END)
            + [
                {
                    "column_id": "total_cost",
                    "filter_config": {"filter_op": "greater_than", "filter_value": 1},
                }
            ]
        },
    ],
)
def test_non_base_shapes_do_not_post_filter_limited_candidates(settings, kwargs):
    settings.METRICS_ENABLED = 1
    native = builder(**kwargs)
    baseline = native.build_candidate_page_query()
    native.bind_current_facts(fixture_read())
    assert native.build_candidate_page_query() == baseline


def test_activity_admission_has_no_usage_or_approximate_session_aggregate(settings):
    settings.METRICS_ENABLED = 1
    native = builder()
    native.bind_current_facts(fixture_read())
    sql, _ = native.build_dimension_candidate_query(
        limit=26, window_start=START, window_end=END
    )
    assert "list_current_user_activity" in sql
    admission = sql.split("page_usage AS (", 1)[0]
    assert "sum(" not in admission and "uniqExact(" not in admission
    assert "sum(cost)" in sql  # hydrated only after finite candidate admission
    assert (
        "(canonical_user_id) IN (SELECT toString(end_user_id) FROM admitted_users)"
        in sql
    )
    assert "FROM spans" not in sql and "span_user_rollup" not in sql
    assert "ORDER BY last_active DESC NULLS LAST, end_user_id DESC" in sql
    assert "INNER JOIN activity_by_user" in sql


def test_usage_is_finite_and_count_precedes_limit(settings):
    settings.METRICS_ENABLED = 1
    native = builder()
    native.bind_current_facts(fixture_read())
    sql, params = native.build_candidate_page_query()
    assert "count() OVER() AS total_count" in sql
    assert (
        "(canonical_user_id) IN (SELECT toString(end_user_id) FROM admitted_users)"
        in sql
    )
    assert "toUInt64(0) AS num_sessions" in sql
    assert not native.embedded_page_metric_fields
    assert params["activity_limit"] == 26
    assert "FROM spans" not in sql


def test_null_activity_cursor_uses_uuid_order(settings):
    settings.METRICS_ENABLED = 1
    native = builder()
    native.bind_current_facts(fixture_read())
    sql, params = native.build_candidate_page_query(
        cursor_mode=True, cursor_before=(None, str(UUID(int=31)))
    )
    assert "WHERE last_active IS NULL AND end_user_id < toUUID" in sql
    assert "count() OVER()" not in sql
    assert params["activity_before_id"] == str(UUID(int=31))
