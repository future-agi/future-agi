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
    value = {
        "version": 1,
        "sampling_unit": "session",
        "mode": "closed",
        "window": {"start": "2026-10-19T00:00:00Z", "end": "2026-10-20T00:00:00Z"},
        "default": {"target_n": 10},
        **changes,
    }
    if changes.get("mode") == "live" and "window" not in changes:
        value["window"]["end"] = None
    return value


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
    assert "s.start_time >= %(sp_start)s AND s.start_time < %(sp_end)s" in sql
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


def test_preview_reports_hand_computed_precision():
    """Y3: per-cell 95% margins (FPC, p = 0.5) and the design's precision."""
    from tracer.models.eval_task import EvalTask
    from tracer.models.project import Project
    from tracer.services.eval_tasks.sampling.service import preview_policy

    project = Project(id=PROJECT, sampling_contact_attribute="")
    task = EvalTask(
        project=project, row_type="spans", filters={}, sampling_policy=policy()
    )

    def run(rows):
        with patch(
            "tracer.services.eval_tasks.sampling.service.stream_query",
            return_value=iter([rows]),
        ):
            return preview_policy(task, now=NOW)

    preview = run(
        [("a", 1000, 2000, 100, 200, 100, 200), ("b", 500, 1000, 50, 100, 50, 100)]
    )
    margins = [cell["margin_of_error"] for cell in preview["strata"]]
    # 1.959963984540054 * sqrt(0.25/100 * 900/999) and sqrt(0.25/50 * 450/499).
    assert margins == [
        pytest.approx(0.09301577436565599, rel=1e-12),
        pytest.approx(0.13161005701441758, rel=1e-12),
    ]
    precision = preview["precision"]
    assert (precision["confidence"], precision["assumed_proportion"]) == (0.95, 0.5)
    # W = 2/3, 1/3: z * sqrt(4/9 * 0.25/100 * 900/999 + 1/9 * 0.25/50 * 450/499).
    assert precision["stratified_margin_of_error"] == pytest.approx(
        0.07595974389307122, rel=1e-12
    )
    # Proportional design: every weight is N/n = 10, so n_eff = n and deff = 1.
    assert precision["kish_effective_n"] == pytest.approx(150, rel=1e-12)
    assert precision["design_effect"] == pytest.approx(1, rel=1e-12)
    assert precision["unsampled_strata"] == 0
    preview = run([("a", 40, 80, 4, 8, 4, 8), ("b", 10, 10, 0, 0, 0, 0)])
    assert [cell["margin_of_error"] for cell in preview["strata"]] == [
        pytest.approx(1.959963984540054 * (0.25 / 4 * 36 / 39) ** 0.5, rel=1e-12),
        None,
    ]
    assert preview["precision"]["stratified_margin_of_error"] is None
    assert preview["precision"]["unsampled_strata"] == 1
    assert preview["precision"]["design_effect"] == pytest.approx(1, rel=1e-12)


def test_live_policy_is_open_ended_and_discovers_future_and_backdated_event_days():
    from datetime import date, timedelta

    from tracer.services.eval_tasks.sampling.query import live_scan_windows

    value = validate_policy(policy(mode="live", default={"rate": 50}), now=NOW)
    assert value["window"]["end"] is None
    task = SimpleNamespace(project_id="ba052ed0-921f-4aa9-972d-7bbbc79e5d30")
    future = NOW + timedelta(days=40)
    with patch(
        "tracer.services.eval_tasks.sampling.query.stream_query",
        return_value=iter([[(date(2026, 10, 19),), (future.date(),)]]),
    ) as query:
        windows = list(live_scan_windows(task, value, future, NOW))
    assert len(windows) == 2
    assert windows[0]["start"].startswith("2026-10-19")
    assert windows[1]["end"] == future.isoformat()
    sql, parameters = query.call_args.args
    assert "_version >= %(floor_ns)s" in sql
    assert parameters["ceiling"] == future
    assert "LIMIT 367" in sql


def test_fixed_end_is_rejected_for_live_mode():
    with pytest.raises(InvalidSamplingPolicy, match="no end"):
        validate_policy(
            policy(
                mode="live",
                default={"rate": 50},
                window={"start": "2026-10-19T00:00Z", "end": "2026-10-20T00:00Z"},
            ),
            now=NOW,
        )


def test_missing_id_fallback_reports_actual_sampling_unit():
    from tracer.models.eval_sampling import EvalSamplingMember
    from tracer.services.eval_tasks.sampling.service import member_metadata

    member = EvalSamplingMember(
        cohort_id="ba052ed0-921f-4aa9-972d-7bbbc79e5d30",
        unit_id="span:anonymous",
        stratum="fallback",
        selected=True,
        inclusion_probability=0.5,
        population_size=2,
    )
    metadata = member_metadata(
        member,
        validate_policy(policy(missing_id="row"), now=NOW),
        "version",
        frozen_at=NOW,
    )
    assert metadata["sampling_unit"] == "span"
    assert metadata["requested_sampling_unit"] == "session"
    assert metadata["missing_id_fallback"] is True
    assert metadata["sampling_unit_id"] == "span:anonymous"


@pytest.mark.parametrize(
    "kind,key",
    [("EVAL_METRIC", "quality"), ("ANNOTATION", "label"), ("TRACE_END_USER", "tier")],
)
def test_live_policy_rejects_untracked_non_span_change_domains(kind, key):
    from tracer.services.eval_tasks.sampling.policy import validate_live_filter_sources

    value = validate_policy(policy(mode="live", default={"rate": 50}), now=NOW)
    filters = {"filters": [{"column_id": key, "filter_config": {"col_type": kind}}]}
    with pytest.raises(InvalidSamplingPolicy, match="closed window"):
        validate_live_filter_sources(value, filters)
    validate_live_filter_sources(validate_policy(policy(), now=NOW), filters)


def test_live_raw_attribute_named_user_id_is_not_a_profile_filter():
    from tracer.services.eval_tasks.sampling.policy import validate_live_filter_sources

    value = validate_policy(policy(mode="live", default={"rate": 50}), now=NOW)
    validate_live_filter_sources(
        value,
        {
            "filters": [
                {
                    "column_id": "user_id",
                    "filter_config": {"col_type": "SPAN_ATTRIBUTE"},
                }
            ]
        },
    )
    validate_live_filter_sources(value, {"filters": None})


@pytest.mark.parametrize("rate", [7, 14, 28, 56, 57, 99])
def test_closed_rate_integer_boundaries_are_exact(rate):
    assert target_size({"rate": rate}, 100) == rate


def test_closed_rate_precision_and_large_population_have_exact_bounds():
    assert target_size({"rate": 0.000001}, 100_000_000) == 1
    assert target_size({"rate": 0.000001}, 100_000_001) == 2
    assert target_size({"rate": 100}, 2**64 - 1) == 2**64 - 1
    with pytest.raises(InvalidSamplingPolicy, match="six decimal"):
        validate_policy(policy(default={"rate": 0.0000001}), now=NOW)


def test_pending_proof_is_point_scoped_and_not_limited_to_live_preview_tail():
    from tracer.services.eval_tasks.sampling.query import SamplingQuery

    task = SimpleNamespace(
        project_id="ba052ed0-921f-4aa9-972d-7bbbc79e5d30", row_type="spans", filters={}
    )
    value = validate_policy(policy(mode="live", default={"rate": 50}), now=NOW)
    query = SamplingQuery(task, value, "version", NOW)
    sql, params = query.current_rows(["old-pending", "changed-pending"])
    assert "id IN %(sp_current_ids)s" in sql
    assert params["sp_current_ids"] == ("old-pending", "changed-pending")
    assert params["sp_start"].isoformat() == value["window"]["start"]
    assert "source_arrival < %(sp_snapshot)s" not in sql
    assert "_version >= %(sp_arrival_floor_ns)s" not in sql
    with pytest.raises(ValueError, match="1–1000"):
        query.current_rows([str(index) for index in range(1001)])


@pytest.mark.parametrize("unit", ["session", "user", "attribute", "trace", "span"])
def test_live_new_member_proof_has_cross_day_scope_and_bounded_identity_seed(unit):
    from datetime import timedelta

    from tracer.services.eval_tasks.sampling.query import SamplingQuery

    task = SimpleNamespace(
        project_id="ba052ed0-921f-4aa9-972d-7bbbc79e5d30", row_type="spans", filters={}
    )
    value = validate_policy(
        policy(mode="live", default={"rate": 50}, sampling_unit=unit),
        now=NOW,
        contact_attribute="contact_id",
    )
    snapshot = NOW + timedelta(days=40)
    query = SamplingQuery(task, value, "v", snapshot)
    sql, params = query.live_member_decisions(
        [f"{unit}:ba052ed0-921f-4aa9-972d-7bbbc79e5d30"]
    )
    assert params["sp_start"].isoformat() == value["window"]["start"]
    assert params["sp_end"] == snapshot
    assert "sp_arrival_floor_ns" not in params
    assert (
        "project_id = %(sp_project)s AND (observation_type, service_name, toStartOfHour(start_time), trace_id, id) IN"
        in sql
    )
    assert f"sp_new_{unit}_ids" in params
    assert params["sp_new_units"] == (f"{unit}:ba052ed0-921f-4aa9-972d-7bbbc79e5d30",)
    assert (
        "SELECT unit_id, stratum, inclusion_probability, selected FROM classified WHERE unit_id IN %(sp_new_units)s"
        in sql
    )
    if unit in ("session", "user"):
        assert "GROUP BY new_id" in sql


def test_live_first_admission_parameters_are_count_and_byte_bounded():
    from tracer.services.eval_tasks.sampling.query import SamplingQuery
    from tracer.services.eval_tasks.sampling.unit_scope import unit_batches

    units = [f"attribute:{index}:" + "x" * 2000 for index in range(1100)]
    batches = list(unit_batches(units))
    assert [unit for batch in batches for unit in batch] == units
    assert all(
        len(batch) <= 1000
        and sum(len(unit.encode()) * 2 + 8 for unit in batch) <= 65536
        for batch in batches
    )
    task = SimpleNamespace(
        project_id="ba052ed0-921f-4aa9-972d-7bbbc79e5d30", row_type="spans", filters={}
    )
    value = validate_policy(policy(mode="live", default={"rate": 50}), now=NOW)
    with pytest.raises(ValueError, match="bounded candidate"):
        SamplingQuery(task, value, "v", NOW).live_member_decisions(
            [f"session:{index}" for index in range(1001)]
        )


def _spans_task(row_type="spans"):
    return SimpleNamespace(
        project_id="ba052ed0-921f-4aa9-972d-7bbbc79e5d30", row_type=row_type, filters={}
    )


@pytest.mark.parametrize("row_type", ["spans", "traces", "sessions"])
@pytest.mark.parametrize("unit", ["session", "user", "attribute"])
def test_closed_member_expansion_pushes_its_units_into_every_physical_read(
    row_type, unit
):
    clean = validate_policy(
        policy(sampling_unit=unit), now=NOW, contact_attribute="contact_id"
    )
    batch = [f"{unit}:a", f"{unit}:b"]
    sql, params = SamplingQuery(_spans_task(row_type), clean, "v", NOW).rows(
        unit_ids=batch
    )
    full, _ = SamplingQuery(_spans_task(row_type), clean, "v", NOW).rows()
    assert params[f"sp_member_{unit}_ids"] == ("a", "b")
    assert params["sp_member_units"] == tuple(batch)
    # Every physical spans scan of the expansion is bounded by the page's units
    # and the window hours, unlike the full-window form.
    assert f"%(sp_member_{unit}_ids)s" in sql
    assert f"%(sp_member_{unit}_ids)s" not in full
    assert "_version < %(sp_snapshot_ns)s" in sql
    assert params["sp_snapshot_ns"] == int(NOW.timestamp() * 1_000_000) * 1000
    assert "r.unit_id IN %(sp_member_units)s" in sql
    assert "row_number()" not in sql
    if unit in ("session", "user"):
        assert "GROUP BY new_id" in sql  # remap aliases of the page's units
    with pytest.raises(ValueError, match="bounded closed page"):
        SamplingQuery(_spans_task(row_type), clean, "v", NOW).rows(
            unit_ids=[f"{unit}:{index}" for index in range(1001)]
        )


def test_live_rows_never_take_a_closed_member_page():
    clean = validate_policy(policy(mode="live", default={"rate": 50}), now=NOW)
    with pytest.raises(ValueError, match="bounded closed page"):
        SamplingQuery(_spans_task(), clean, "v", NOW).rows(unit_ids=["session:a"])


@pytest.mark.parametrize("row_type", ["spans", "traces", "sessions"])
def test_live_first_admission_seed_is_bounded_by_window_hours_and_arrival(row_type):
    from datetime import timedelta

    clean = validate_policy(policy(mode="live", default={"rate": 50}), now=NOW)
    snapshot = NOW + timedelta(days=3)
    sql, params = SamplingQuery(
        _spans_task(row_type), clean, "v", snapshot
    ).live_member_decisions(["session:ba052ed0-921f-4aa9-972d-7bbbc79e5d30"])
    seed = sql[sql.index("%(sp_new_session_ids)s") - 400 :]
    assert "_version < %(sp_snapshot_ns)s" in sql
    assert "toStartOfHour(start_time) >= toStartOfHour(toDateTime64(%(sp_start)s" in sql
    assert params["sp_snapshot_ns"] == int(snapshot.timestamp() * 1_000_000) * 1000
    assert params["sp_start"].isoformat() == clean["window"]["start"]
    assert "FROM spans PREWHERE project_id = %(sp_project)s AND toStartOfHour" in seed
    assert params.proof is True
    assert params.read_settings == {
        "max_rows_to_read": READ_SETTINGS["max_rows_to_read"]
    }
    if row_type != "spans":
        assert "WHERE parent_span_id = ''" in sql  # roots in the eligibility hours


def test_live_first_admission_refuses_lookback_beyond_configured_bound(settings):
    from datetime import timedelta

    from tracer.services.eval_tasks.sampling.errors import SamplingProofBoundExceeded

    settings.EVAL_SAMPLING_LIVE_PROOF_MAX_DAYS = 2
    clean = validate_policy(policy(mode="live", default={"rate": 50}), now=NOW)
    query = SamplingQuery(_spans_task(), clean, "v", NOW + timedelta(days=3))
    with pytest.raises(SamplingProofBoundExceeded, match="2 days"):
        query.live_member_decisions(["session:a"])
    settings.EVAL_SAMPLING_LIVE_PROOF_MAX_ROWS = 1000
    settings.EVAL_SAMPLING_LIVE_PROOF_MAX_DAYS = 30
    _, params = SamplingQuery(
        _spans_task(), clean, "v", NOW + timedelta(days=3)
    ).live_member_decisions(["session:a"])
    assert params.read_settings == {"max_rows_to_read": 1000}
    settings.EVAL_SAMPLING_LIVE_PROOF_MAX_ROWS = READ_SETTINGS["max_rows_to_read"] + 1
    with pytest.raises(ValueError, match="must not exceed"):
        SamplingQuery(
            _spans_task(), clean, "v", NOW + timedelta(days=3)
        ).live_member_decisions(["session:a"])


def test_oversized_identity_markers_name_their_bound_per_group_position():
    clean = validate_policy(
        policy(group_by=[{"key": "locale"}, {"key": "category"}]), now=NOW
    )
    sql, _ = SamplingQuery(_spans_task(), clean, "v", NOW).members()
    assert "throwIf(length(p.unit_id) > 2048, 'EVAL_SAMPLING_OVERSIZED_UNIT')" in sql
    assert (
        "throwIf(length(p.groups[1]) > 1024, 'EVAL_SAMPLING_OVERSIZED_GROUP_0')" in sql
    )
    assert (
        "throwIf(length(p.groups[2]) > 1024, 'EVAL_SAMPLING_OVERSIZED_GROUP_1')" in sql
    )


def test_read_errors_are_typed_without_leaking_values():
    from tracer.selectors.eval_tasks.row_resolver import (
        EvalTaskReadBudgetExceeded,
        EvalTaskSelectionRejected,
    )
    from tracer.services.eval_tasks.sampling.errors import (
        SamplingProofBoundExceeded,
        SamplingReadCeilingExceeded,
        SamplingReadPressure,
        SamplingValueTooLarge,
        classify_read_error,
    )

    clean = validate_policy(
        policy(
            sampling_unit="attribute",
            unit_attribute={"key": "contact_id"},
            group_by=[{"key": "locale"}, {"key": "category"}],
        ),
        now=NOW,
    )

    def server(code, text):
        exc = RuntimeError(f"Code: {code}. DB::Exception: {text} secret-value")
        exc.code = code
        return exc

    unit = classify_read_error(
        server(395, "EVAL_SAMPLING_OVERSIZED_UNIT"), policy=clean
    )
    group = classify_read_error(
        server(395, "EVAL_SAMPLING_OVERSIZED_GROUP_1: while executing"), policy=clean
    )
    assert isinstance(unit, SamplingValueTooLarge) and "'contact_id'" in str(unit)
    assert isinstance(group, SamplingValueTooLarge) and "'category'" in str(group)
    assert "secret-value" not in str(unit) + str(group)
    # The server echoes the whole failing AND expression after the thrown
    # marker (shape captured from ClickHouse 25.3), so the other bound checks'
    # markers appear later in the text. Only the leftmost one was thrown.
    echoed = (
        "EVAL_SAMPLING_OVERSIZED_GROUP_0: while executing 'FUNCTION and("
        "equals(throwIf(greater(length(__table1.unit_id), 2048_UInt16), "
        "'EVAL_SAMPLING_OVERSIZED_UNIT'_String), 0_UInt8) :: 5, "
        "equals(throwIf(greater(length(arrayElement(__table1.groups, 1_UInt8)), "
        "1024_UInt16), 'EVAL_SAMPLING_OVERSIZED_GROUP_0'_String), 0_UInt8) :: 3)"
    )
    thrown_group = classify_read_error(server(395, echoed), policy=clean)
    assert isinstance(thrown_group, SamplingValueTooLarge)
    assert "'locale'" in str(thrown_group) and "contact_id" not in str(thrown_group)
    thrown_unit = classify_read_error(
        server(
            395,
            "EVAL_SAMPLING_OVERSIZED_UNIT: while executing 'FUNCTION throwIf("
            "greater(length(__table1.unit_id), 2048_UInt16) :: 0, "
            "'EVAL_SAMPLING_OVERSIZED_UNIT'_String :: 1)'",
        ),
        policy=clean,
    )
    assert "'contact_id'" in str(thrown_unit) and "locale" not in str(thrown_unit)
    ceiling = classify_read_error(server(158, "Limit for rows"), policy=clean)
    proof = classify_read_error(
        server(307, "Limit for bytes"), policy=clean, proof=True
    )
    assert type(ceiling) is SamplingReadCeilingExceeded
    assert isinstance(proof, SamplingProofBoundExceeded)
    assert "no partial decision" in str(proof)
    assert isinstance(ceiling, EvalTaskSelectionRejected)  # fail fast, no retry
    pressure = classify_read_error(server(159, "Timeout exceeded"), policy=clean)
    assert isinstance(pressure, SamplingReadPressure)
    assert isinstance(pressure, EvalTaskReadBudgetExceeded)
    assert not isinstance(pressure, EvalTaskSelectionRejected)  # retry/defer
    assert isinstance(
        classify_read_error(TimeoutError("deadline")), SamplingReadPressure
    )
    assert classify_read_error(server(62, "Syntax error")) is None
    assert classify_read_error(ValueError("other")) is None


def test_stream_query_types_errors_and_only_tightens_budgets():
    from contextlib import contextmanager

    from tracer.services.eval_tasks.sampling.errors import SamplingValueTooLarge
    from tracer.services.eval_tasks.sampling.query import SamplingParams, stream_query

    seen = []

    @contextmanager
    def execute(sql, params, **options):
        seen.append(options["settings"])

        def blocks():
            exc = RuntimeError("Code: 395. EVAL_SAMPLING_OVERSIZED_GROUP_0")
            exc.code = 395
            raise exc
            yield  # pragma: no cover

        yield blocks()

    params = SamplingParams(sp_project="p")
    params.policy = {"group_by": [{"key": "locale"}]}
    params.read_settings = {"max_rows_to_read": 10}
    with patch(
        "tracer.services.clickhouse.v2.query_service.get_v2_query_client",
        return_value=SimpleNamespace(execute_read_block_stream=execute),
    ):
        with pytest.raises(SamplingValueTooLarge, match="'locale'"):
            list(stream_query("SELECT 1", params))
        with pytest.raises(ValueError, match="only tighten"):
            list(stream_query("SELECT 1", {}, settings={"max_rows_to_read": 10**12}))
    assert seen == [{**READ_SETTINGS, "max_rows_to_read": 10}]


def test_oversized_statement_is_a_typed_rejection_before_any_read():
    from tracer.selectors.eval_tasks.row_resolver import EvalTaskSelectionRejected
    from tracer.services.eval_tasks.sampling.errors import SamplingStatementTooLarge
    from tracer.services.eval_tasks.sampling.query import stream_query

    oversized = "SELECT 1" + " " * READ_SETTINGS["max_query_size"]
    with patch(
        "tracer.services.clickhouse.v2.query_service.get_v2_query_client"
    ) as client:
        with pytest.raises(SamplingStatementTooLarge, match="compiler budget") as err:
            list(stream_query(oversized, {}))
    # Deterministic for the policy: fail fast as a client error, never retried
    # as read pressure, and no statement is sent.
    assert isinstance(err.value, EvalTaskSelectionRejected)
    client.assert_not_called()


@pytest.mark.parametrize(
    "filters,expected",
    [
        (
            {
                "project_id": "p",
                "date_range": ["2026-10-01T00:00:00Z", "2026-10-02T00:00:00Z"],
                "date_preset": "custom",
                "observation_type": ["llm"],
            },
            {"project_id": "p", "observation_type": ["llm"]},
        ),
        (None, {}),
        (
            {
                "filters": [
                    {
                        "column_id": "created_at",
                        "filter_config": {"col_type": "SPAN_ATTRIBUTE"},
                    }
                ]
            },
            {
                "filters": [
                    {
                        "column_id": "created_at",
                        "filter_config": {"col_type": "SPAN_ATTRIBUTE"},
                    }
                ]
            },
        ),
    ],
)
def test_policy_window_is_the_only_time_authority(filters, expected):
    from tracer.services.eval_tasks.sampling.policy import policy_task_filters

    assert policy_task_filters(filters) == expected


@pytest.mark.parametrize(
    "filters",
    [
        {"created_at": "2026-10-01T00:00:00Z"},
        {"filters": [{"column_id": "start_time", "filter_config": {}}]},
        {
            "span_attributes_filters": [
                {
                    "column_id": "created_at",
                    "filter_config": {"col_type": "SYSTEM_METRIC"},
                }
            ]
        },
    ],
)
def test_explicit_policy_task_time_filters_are_rejected_not_intersected(filters):
    from tracer.services.eval_tasks.sampling.policy import (
        policy_task_filters,
        require_policy_time_authority,
    )

    with pytest.raises(InvalidSamplingPolicy, match="policy window"):
        policy_task_filters(filters)
    with pytest.raises(InvalidSamplingPolicy):
        require_policy_time_authority(filters)


def test_normalized_policy_edit_is_not_a_population_change():
    from tracer.services.eval_tasks.sampling.policy import (
        normalize_policy_update,
        population_changed,
    )

    task = SimpleNamespace(
        sampling_policy=policy(),
        filters={"project_id": "p"},
        project="project",
        row_type="spans",
    )
    changes = {"filters": {"project_id": "p", "date_range": ["a", "b"]}}
    normalize_policy_update(task, changes)
    assert changes == {"filters": {"project_id": "p"}}
    assert not population_changed(task, changes)
    legacy = SimpleNamespace(sampling_policy=None, filters={})
    untouched = {"filters": {"date_range": ["a", "b"]}}
    normalize_policy_update(legacy, untouched)
    assert untouched == {"filters": {"date_range": ["a", "b"]}}


# --- Coordinated (permanent-random-number) sampling ------------------------

PROJECT = "ba052ed0-921f-4aa9-972d-7bbbc79e5d30"
# Computed with the policy module as committed before coordination existed
# (61977cc93..6975d9407 share it): an omitted or explicit "independent"
# coordination must keep every existing policy version byte for byte.
GOLDEN_VERSIONS = {
    "closed": "5621bacf07847bc1573226f853c294815018fc9a7290a60d614486a83c4cf473",
    "live": "16fa1b4030dcb25cfced0c7ce908a3c4d842b6b3a87c21e66c7ff9c4222abd13",
}
GOLDEN_POLICIES = {
    "closed": policy(),
    "live": policy(
        sampling_unit="user",
        mode="live",
        window={"start": "2026-10-19T00:00:00Z", "end": None},
        strata=[
            {
                "name": "vip",
                "filters": [{"key": "tier", "values": ["gold"]}],
                "rate": 50,
            }
        ],
        group_by=[{"key": "locale"}],
        default={"rate": 12.5},
    ),
}


@pytest.mark.parametrize("kind", ["closed", "live"])
@pytest.mark.parametrize("explicit", [False, True])
def test_independent_default_keeps_existing_policy_versions_and_seed(kind, explicit):
    from tracer.services.eval_tasks.sampling.policy import selection_seed

    raw = deepcopy(GOLDEN_POLICIES[kind])
    if explicit:
        raw["coordination"] = "independent"
    clean = validate_policy(raw, now=NOW)
    assert "coordination" not in clean and "panel" not in clean
    version = policy_version(clean, project_id=PROJECT, row_type="spans", filters={})
    assert version == GOLDEN_VERSIONS[kind]
    # The historical seed: SHA-256(policy_version + ':' + unit_id).
    assert selection_seed(clean, version=version, project_id=PROJECT) == version


@pytest.mark.parametrize(
    "change,message",
    [
        ({"coordination": "global"}, "coordination must be"),
        ({"panel": "a"}, "require project coordination"),
        ({"prn_start": 5}, "require project coordination"),
        ({"coordination": "project", "panel": "-lead"}, "panel must be"),
        ({"coordination": "project", "panel": "x" * 65}, "panel must be"),
        ({"coordination": "project", "panel": 7}, "panel must be"),
        ({"coordination": "project", "prn_start": 100}, "prn_start must be"),
        ({"coordination": "project", "prn_start": -1}, "prn_start must be"),
        ({"coordination": "project", "prn_start": True}, "prn_start must be"),
        ({"coordination": "project", "prn_start": float("nan")}, "prn_start must be"),
        ({"coordination": "project", "prn_start": 1.0000001}, "six decimal"),
    ],
)
def test_coordination_fields_are_validated(change, message):
    with pytest.raises(InvalidSamplingPolicy, match=message):
        validate_policy(policy(**change), now=NOW)


def test_coordinated_seed_ignores_rates_strata_window_and_filters():
    from tracer.services.eval_tasks.sampling.policy import selection_seed

    first = validate_policy(
        policy(coordination="project", default={"rate": 10}), now=NOW
    )
    second = validate_policy(
        policy(
            coordination="project",
            window={"start": "2026-10-01T00:00:00Z", "end": "2026-10-02T00:00:00Z"},
            strata=[
                {"name": "a", "filters": [{"key": "x", "values": ["1"]}], "target_n": 3}
            ],
            default={"rate": 20},
        ),
        now=NOW,
    )
    versions = [
        policy_version(first, project_id=PROJECT, row_type="spans", filters={}),
        policy_version(second, project_id=PROJECT, row_type="traces", filters={"a": 1}),
    ]
    # Each edit is still its own cohort (lineage), but draws the same numbers.
    assert versions[0] != versions[1]
    seeds = {
        selection_seed(value, version=version, project_id=PROJECT)
        for value, version in zip((first, second), versions, strict=True)
    }
    assert seeds == {f"prn-sha256-v1:{PROJECT}:"}
    panel = validate_policy(policy(coordination="project", panel="q4.audit"), now=NOW)
    assert (
        selection_seed(panel, version="v", project_id=PROJECT)
        == f"prn-sha256-v1:{PROJECT}:q4.audit"
    )
    assert selection_seed(first, version="v", project_id="other") != next(iter(seeds))
    # Zero start and an empty panel normalize away: one canonical stored form.
    assert validate_policy(
        policy(coordination="project", panel="", prn_start=0), now=NOW
    ) == validate_policy(policy(coordination="project"), now=NOW)


def _bottom_k(seed, units, k, start=0):
    from tracer.services.eval_tasks.sampling.policy import prn_order_key

    return set(sorted(units, key=lambda u: (prn_order_key(seed, u, start), u))[:k])


def test_prn_nesting_overlap_and_rotation():
    """T9: nested rates/quotas, cross-task overlap and disjoint rotations."""
    import random

    from tracer.services.eval_tasks.sampling.policy import (
        prn_selects,
        selection_seed,
        unit_hash,
    )

    units = [f"session:{index:05d}" for index in range(10_000)]
    coordinated = validate_policy(policy(coordination="project"), now=NOW)
    seed = selection_seed(coordinated, version="unused", project_id=PROJECT)

    def live(rate, start=0, population=units):
        return {u for u in population if prn_selects(seed, u, rate, start)}

    # (a) Raising a live rate keeps every previously selected unit.
    samples = {rate: live(rate) for rate in (1, 5, 10, 20, 50, 100)}
    for low, high in zip(sorted(samples)[:-1], sorted(samples)[1:], strict=True):
        assert samples[low] <= samples[high]
    assert samples[100] == set(units)
    assert 900 <= len(samples[10]) <= 1100  # Bernoulli(0.1) over 10,000
    # ... and raising a closed quota k keeps every previously selected unit.
    tops = {k: _bottom_k(seed, units, k) for k in (50, 100, 500, 1000)}
    assert tops[50] < tops[100] < tops[500] < tops[1000]
    assert tops[1000] == set(
        sorted(units, key=lambda u: (unit_hash(seed, u), u))[:1000]
    )
    # (b) Two tasks (different rates/strata/windows, same project) agree on
    # every unit at the same rate: 100% overlap, not ~rate x rate.
    other = validate_policy(
        policy(coordination="project", default={"target_n": 3}), now=NOW
    )
    other_seed = selection_seed(other, version="different", project_id=PROJECT)
    assert other_seed == seed
    assert {u for u in units if prn_selects(other_seed, u, 10)} == samples[10]
    # Independent policies draw separate samples: about 1% of units overlap.
    independent = [
        {u for u in units if prn_selects(version, u, 10)} for version in ("v1", "v2")
    ]
    overlap = len(independent[0] & independent[1])
    assert 50 <= overlap <= 160, overlap
    # (c) Disjoint start intervals give disjoint samples whose union is the
    # wider interval; the same width elsewhere on the circle is the same size.
    rotated = [live(10, start) for start in (0, 10, 95)]
    assert not rotated[0] & rotated[1]
    assert rotated[0] | rotated[1] == samples[20]
    assert not rotated[1] & rotated[2]
    assert rotated[2] == {u for u in units if prn_selects(seed, u, 5, 95)} | {
        u for u in units if prn_selects(seed, u, 5, 0)
    }  # [95%, 100%) plus the wrapped [0%, 5%)
    # Rotated closed samples nest too and never depend on input order.
    rotated_tops = [_bottom_k(seed, units, k, 37.5) for k in (100, 300)]
    assert rotated_tops[0] < rotated_tops[1]
    # Panels are independent coordinated families.
    panel_seed = selection_seed(
        validate_policy(policy(coordination="project", panel="b"), now=NOW),
        version="x",
        project_id=PROJECT,
    )
    assert (
        50
        <= len(samples[10] & {u for u in units if prn_selects(panel_seed, u, 10)})
        <= 160
    )
    # Stable under reordering and pagination: decisions are per unit.
    shuffled = units[:]
    random.Random(20261023).shuffle(shuffled)
    pages = [shuffled[i : i + 1000] for i in range(0, len(shuffled), 1000)]
    assert set().union(*(live(10, 0, page) for page in pages)) == samples[10]
    assert _bottom_k(seed, shuffled, 500) == tops[500]


def test_prn_hash_uniformity_chi_square_100k():
    """The PRN key is uniform on [0, 1) for every start point (100,000 ids)."""
    from scipy.stats import chi2

    from tracer.services.eval_tasks.sampling.policy import (
        HASH_SPACE,
        prn_order_key,
        selection_seed,
    )

    seed = selection_seed(
        validate_policy(policy(coordination="project"), now=NOW),
        version="unused",
        project_id=PROJECT,
    )
    ids = [f"user:customer-{index}" for index in range(100_000)]
    bins = 100
    for start in (0, 62.5):
        counts = [0] * bins
        for unit in ids:
            counts[prn_order_key(seed, unit, start) * bins // HASH_SPACE] += 1
        expected = len(ids) / bins
        statistic = sum((count - expected) ** 2 / expected for count in counts)
        # A uniform key fails this only 1 time in 1,000 (df = 99).
        assert chi2.sf(statistic, bins - 1) > 0.001, (start, statistic)
        assert max(abs(count - expected) for count in counts) < 0.15 * expected


def _compile(policy_value, *, live=False):
    task = SimpleNamespace(project_id=PROJECT, row_type="spans", filters={})
    query = SamplingQuery(task, validate_policy(policy_value, now=NOW), "version", NOW)
    return query.rows() if live else query.members()


def test_coordinated_statements_bind_the_shared_seed_and_circle_order():
    sql, params = _compile(policy())
    assert "hex(SHA256(concat(%(sp_seed)s, ':', unit_id)))" in sql
    assert params["sp_seed"] == "version"  # independent: the policy version
    assert "PARTITION BY stratum ORDER BY unit_hash, unit_id" in sql
    sql, params = _compile(policy(coordination="project", panel="p1"))
    assert params["sp_seed"] == f"prn-sha256-v1:{PROJECT}:p1"
    assert "PARTITION BY stratum ORDER BY unit_hash, unit_id" in sql
    sql, params = _compile(policy(coordination="project", prn_start=50))
    start = "08" + "0" * 63
    assert start in params.values()
    assert "ORDER BY concat('0', unit_hash) < %(sp_value_" in sql


def test_live_start_interval_compiles_plain_and_wrapped_ranges():
    from tracer.services.eval_tasks.sampling.policy import prn_start_offset, threshold

    sql, params = _compile(
        policy(
            mode="live",
            coordination="project",
            prn_start=95,
            strata=[{"name": "all", "filters": [], "rate": 100}],
            default={"rate": 10},
        ),
        live=True,
    )
    begin = f"{int(threshold(95), 16):065X}"
    wrapped = f"{int(threshold(95), 16) + int(threshold(10), 16) - (1 << 256):065X}"
    assert begin in params.values() and wrapped in params.values()
    assert " OR concat('0', unit_hash) < " in sql  # [95%, 100%) U [0%, 5%)
    sql, params = _compile(
        policy(mode="live", coordination="project", prn_start=5, default={"rate": 10}),
        live=True,
    )
    # The end is S + T with the reference's own integers (floor(5%) +
    # floor(10%)), which is one below floor(15%): never re-round the sum.
    end = prn_start_offset(5) + int(threshold(10), 16)
    assert end == int(threshold(15), 16) - 1
    assert f"{end:065X}" in params.values()
    assert " AND concat('0', unit_hash) < " in sql  # [5%, 15%)
