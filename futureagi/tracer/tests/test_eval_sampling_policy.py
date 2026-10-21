from copy import deepcopy
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from tracer.services.eval_tasks.sampling.policy import (
    InvalidSamplingPolicy,
    policy_version,
    rate_selects,
    target_size,
    threshold,
    validate_policy,
)
from tracer.services.eval_tasks.sampling.query import READ_SETTINGS, SamplingQuery

NOW = datetime(2026, 10, 21, tzinfo=UTC)


def policy(**changes):
    return {
        "version": 1,
        "sampling_unit": "session",
        "mode": "closed",
        "window": {"start": "2026-10-19T00:00:00Z", "end": "2026-10-20T00:00:00Z"},
        "default": {"target_n": 10},
        **changes,
    }


@pytest.mark.parametrize("unit", ["span", "trace", "session", "user", "attribute"])
def test_sampling_unit_is_independent_of_span_evaluation(unit):
    assert (
        validate_policy(
            policy(sampling_unit=unit), now=NOW, contact_attribute="contact_id"
        )["sampling_unit"]
        == unit
    )


@pytest.mark.parametrize(
    "change",
    [
        {"policy_id": "other-tenant"},
        {"project_id": "other-tenant"},
        {"version": 2},
        {"sampling_unit": "company"},
        {"default": {"rate": float("nan")}},
        {"default": {"rate": float("inf")}},
        {"default": {"target_n": -1}},
        {"default": {"min_n": 20, "max_n": 10}},
        {"default": {"target_n": 1, "rate": 50}},
        {"window": {"start": "2026-10-19", "end": "2026-10-20"}},
        {"window": {"start": "2026-01-01T00:00Z", "end": "2026-10-20T00:00Z"}},
        {"window": {"start": "2026-10-20T00:00Z", "end": "2026-10-22T00:00Z"}},
        {"strata": [{"name": "a", "filters": [{"key": "x", "project_id": "leak"}]}]},
        {"strata": [{"name": "a"}, {"name": "a"}]},
        {"default": {"target_n": True}},
        {"missing_id": "silent"},
    ],
)
def test_invalid_or_cross_tenant_policy_references_rejected(change):
    with pytest.raises(InvalidSamplingPolicy):
        validate_policy(policy(**change), now=NOW)


@pytest.mark.parametrize("quota", ["target_n", "min_n", "max_n", "rare_below"])
def test_live_quota_claims_rejected(quota):
    with pytest.raises(InvalidSamplingPolicy, match="closed window"):
        validate_policy(policy(mode="live", default={quota: 10}), now=NOW)


def test_canonical_policy_resolves_project_attribute_at_save():
    clean = validate_policy(
        policy(sampling_unit="attribute"), now=NOW, contact_attribute="contact_id"
    )
    assert clean["unit_attribute"] == {"source": "span", "key": "contact_id"}
    assert (
        validate_policy(clean, now=NOW, contact_attribute="changed")["unit_attribute"][
            "key"
        ]
        == "contact_id"
    )


@pytest.mark.parametrize(
    "rule,n,expected",
    [
        ({"target_n": 10}, 5, 5),
        ({"target_n": 10}, 50, 10),
        ({"rate": 10}, 101, 11),
        ({"rate": 0, "min_n": 3}, 100, 3),
        ({"rate": 100, "max_n": 4}, 100, 4),
        ({"rate": 10, "rare_below": 5}, 5, 5),
        ({"rate": 10, "rare_below": 5, "max_n": 4}, 5, 4),
        ({"target_n": 0}, 50, 0),
        ({"min_n": 10}, 0, 0),
    ],
)
def test_exact_quota_floors_caps_rare_cells(rule, n, expected):
    assert target_size(rule, n) == expected


def test_twenty_attribute_cells_with_rare_oversampling_have_valid_weights():
    for _locale in range(5):
        for category in range(4):
            n = 3 if category == 3 else 100
            rule = {"target_n": 10, "rare_below": 5}
            k = target_size(rule, n)
            assert k == (3 if category == 3 else 10)
            assert sum(1 / (k / n) for _ in range(k)) == pytest.approx(n)


def test_hash_stability_fractional_rates_and_zero_hundred_edges():
    ids = [f"session:{i}" for i in range(10000)]
    first = {i for i in ids if rate_selects("seed", i, 12.5)}
    assert first == {i for i in reversed(ids) if rate_selects("seed", i, 12.5)}
    assert 1000 < len(first) < 1500
    assert not any(rate_selects("seed", i, 0) for i in ids)
    assert all(rate_selects("seed", i, 100) for i in ids)
    assert threshold(100) == "1" + "0" * 64
    assert threshold(50) == "08" + "0" * 63


def test_version_covers_population_filters_and_canonical_policy():
    a = validate_policy(policy(), now=NOW)
    kw = {"project_id": "project", "row_type": "spans", "filters": {}}
    assert policy_version(a, **kw) == policy_version(deepcopy(a), **kw)
    assert policy_version(a, **kw) != policy_version(a, **{**kw, "project_id": "other"})
    assert policy_version(a, **kw) != policy_version(
        a, **{**kw, "filters": {"observation_type": "llm"}}
    )


def test_query_bounds_scope_remap_and_parameterized_attributes():
    clean = validate_policy(
        policy(
            sampling_unit="attribute",
            unit_attribute={"key": "contact' OR 1=1"},
            strata=[
                {
                    "name": "priority",
                    "filters": [{"key": "locale", "values": ["en"], "op": "in"}],
                    "target_n": 2,
                }
            ],
            group_by=[{"key": "locale"}],
        ),
        now=NOW,
    )
    task = SimpleNamespace(
        project_id="ba052ed0-921f-4aa9-972d-7bbbc79e5d30", row_type="spans", filters={}
    )
    with patch(
        "tracer.selectors.eval_tasks.row_resolver._build_sample_query",
        return_value=("SELECT id FROM scoped_test_ids", {}),
    ):
        sql, params = SamplingQuery(task, clean, "version", NOW).members()
    assert "contact' OR" not in sql
    assert "contact' OR 1=1" in params.values()
    assert "project_id = %(sp_project)s" in sql
    assert "start_time >= %(sp_start)s AND start_time < %(sp_end)s" in sql
    assert "created_at < %(sp_snapshot)s" in sql
    assert "fromUnixTimestamp64Nano(toInt64(_version), 'UTC')" in sql
    assert "source_arrival < %(sp_snapshot)s" in sql
    assert "trace_session_id_remap" in sql and "end_user_id_remap" in sql
    assert "GROUP BY any_id" in sql  # many-old-to-one-new cannot fan out
    assert "min(r.rule_index)" in sql  # overlap belongs to first matching stratum
    assert "PARTITION BY stratum ORDER BY unit_hash, unit_id" in sql
    assert "SELECT * FROM spans" not in sql
    assert READ_SETTINGS["read_overflow_mode"] == "throw"
    assert READ_SETTINGS["result_overflow_mode"] == "throw"


def test_legacy_null_policy_stays_null():
    assert validate_policy(None) is None


@pytest.mark.parametrize("row_type", ["spans", "traces", "sessions"])
def test_real_row_filter_builder_accepts_bounded_policy_window(row_type):
    task = SimpleNamespace(
        project_id="ba052ed0-921f-4aa9-972d-7bbbc79e5d30", row_type=row_type, filters={}
    )
    sql, params = SamplingQuery(
        task, validate_policy(policy(), now=NOW), "version", NOW
    ).members()
    assert "sp_project" in params
    assert sql.startswith("WITH raw AS")


def test_closed_late_poll_never_recomputes_bottom_k():
    task = SimpleNamespace(
        project_id="ba052ed0-921f-4aa9-972d-7bbbc79e5d30", row_type="spans", filters={}
    )
    sql, params = SamplingQuery(
        task, validate_policy(policy(), now=NOW), "version", NOW
    ).rows(arrival_floor=NOW)
    assert "row_number()" not in sql
    assert "r.arrival >= %(sp_floor)s" in sql
    assert "WHERE r.unit_id != ''" in sql


def test_live_percentage_does_not_sort_or_rank_an_unlimited_population():
    task = SimpleNamespace(
        project_id="ba052ed0-921f-4aa9-972d-7bbbc79e5d30", row_type="spans", filters={}
    )
    sql, params = SamplingQuery(
        task,
        validate_policy(policy(mode="live", default={"rate": 50}), now=NOW),
        "version",
        NOW,
    ).rows()
    assert "row_number()" not in sql
    assert "concat('0', unit_hash)" in sql


@pytest.mark.parametrize(
    "bad_field", ["policy_id", "project_id", "organization_id", "workspace_id"]
)
def test_wire_policy_rejects_external_reference_fields(bad_field):
    from tracer.serializers.eval_sampling import SamplingPolicySerializer

    serializer = SamplingPolicySerializer(data=policy(**{bad_field: "foreign"}))
    assert not serializer.is_valid()
    assert bad_field in serializer.errors


def test_wire_policy_rejects_external_reference_in_nested_slice():
    from tracer.serializers.eval_sampling import SamplingPolicySerializer

    serializer = SamplingPolicySerializer(
        data=policy(strata=[{"name": "x", "project_id": "foreign", "rate": 100}])
    )
    assert not serializer.is_valid()
    assert "project_id" in serializer.errors["strata"][0]


def test_stream_preserves_budgets_inside_application_mode():
    from contextlib import contextmanager

    from tracer.services.clickhouse.application_read_policy import (
        application_read_context,
        is_application_read,
    )
    from tracer.services.eval_tasks.sampling.query import stream_query

    @contextmanager
    def execute(sql, params, **options):
        assert not is_application_read()
        assert options["timeout_ms"] == 30000
        assert options["block_size"] == 1000
        assert options["settings"] == READ_SETTINGS
        yield iter([[("row",)]])

    with patch(
        "tracer.services.clickhouse.v2.query_service.get_v2_query_client",
        return_value=SimpleNamespace(execute_read_block_stream=execute),
    ):
        with application_read_context():
            assert list(stream_query("SELECT 1", {})) == [[("row",)]]
            assert is_application_read()


def test_native_parameters_preserve_microsecond_window_edges():
    from tracer.services.eval_tasks.sampling.query import native_parameters

    precise = NOW.replace(microsecond=654321)
    parameters = {
        "cutoff": precise,
        "naive": precise.replace(tzinfo=None),
        "project": "p",
    }
    assert native_parameters(parameters) == {
        "cutoff": "2026-10-21 00:00:00.654321",
        "naive": "2026-10-21 00:00:00.654321",
        "project": "p",
    }
    assert parameters["cutoff"] == precise


def test_trace_witness_reader_is_batched_and_rejects_incomplete_proof():
    from tracer.selectors.eval_tasks.row_resolver import EvalTaskSelectionRejected
    from tracer.services.eval_tasks.sampling.witnesses import TraceWitnessReader

    task = SimpleNamespace(
        project_id="ba052ed0-921f-4aa9-972d-7bbbc79e5d30",
        row_type="traces",
        filters={
            "filters": [
                {
                    "column_id": "locale",
                    "filter_config": {
                        "col_type": "SPAN_ATTRIBUTE",
                        "filter_type": "text",
                        "filter_op": "in",
                        "filter_value": ["en"],
                    },
                }
            ]
        },
    )
    reader = TraceWitnessReader(task)
    query, params = reader.build_query(["a", "b"])
    assert "filter_witness_0" in query
    with patch(
        "tracer.services.eval_tasks.sampling.witnesses.stream_query",
        return_value=iter([[("a", ("child-a", NOW)), ("b", ("child-b", NOW))]]),
    ):
        witnesses = reader.read(["a", "b"])
    assert {(item.trace_id, item.span_id) for item in witnesses} == {
        ("a", "child-a"),
        ("b", "child-b"),
    }
    with patch(
        "tracer.services.eval_tasks.sampling.witnesses.stream_query",
        return_value=iter([[("a", ("child-a", NOW))]]),
    ):
        with pytest.raises(EvalTaskSelectionRejected):
            reader.read(["a", "b"])


def test_closed_row_query_is_restricted_to_frozen_member_batch():
    task = SimpleNamespace(
        project_id="ba052ed0-921f-4aa9-972d-7bbbc79e5d30", row_type="spans", filters={}
    )
    sql, params = SamplingQuery(
        task, validate_policy(policy(), now=NOW), "version", NOW
    ).rows(unit_ids=["session:chosen"])
    assert "r.unit_id IN %(sp_member_units)s" in sql
    assert params["sp_member_units"] == ("session:chosen",)
    assert "row_number()" not in sql


def test_aggregate_policy_payload_is_bounded():
    oversized = policy(
        strata=[
            {"name": str(index), "filters": [{"key": "x", "values": ["x" * 500] * 100}]}
            for index in range(2)
        ]
    )
    with pytest.raises(InvalidSamplingPolicy, match="64 KiB"):
        validate_policy(oversized, now=NOW)


def test_preview_counts_evaluation_runs_without_fabricated_pricing():
    from tracer.models.eval_task import EvalTask
    from tracer.models.project import Project
    from tracer.services.eval_tasks.sampling.service import preview_policy

    project = Project(
        id="ba052ed0-921f-4aa9-972d-7bbbc79e5d30", sampling_contact_attribute=""
    )
    task = EvalTask(
        project=project, row_type="spans", filters={}, sampling_policy=policy()
    )
    with patch(
        "tracer.services.eval_tasks.sampling.service.stream_query",
        return_value=iter([[("cell", 10, 40, 2, 8, 2, 8)]]),
    ):
        preview = preview_policy(task, now=NOW, evaluator_count=3)
    assert preview["evaluator_count"] == 3
    assert preview["expected_evaluations"] == 24
    assert preview["selected_evaluations"] == 24
    assert preview["estimated_cost"] is None
