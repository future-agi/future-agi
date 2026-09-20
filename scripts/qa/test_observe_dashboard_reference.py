"""Offline dashboard-only QA; native tests reuse the existing disposable RMT."""

from copy import deepcopy
from datetime import datetime, timezone
import math

import pytest

from observe_dashboard_reference import build_dashboard_reference_query, compare_dashboard_rows
from replay_observe_filters import ReplayError

PROJECT = "11111111-1111-4111-8111-111111111111"
START = "2026-09-04T12:15:00+00:00"
END = "2026-09-04T13:00:00+00:00"


def config(aggregations=("avg",), breakdown="string"):
    return {
        "project_ids": [PROJECT], "granularity": "day",
        "time_range": {"custom_start": START, "custom_end": END},
        "metrics": [{"id": agg, "name": "latency_ms", "type": "custom_attribute",
                     "source": "traces", "attribute_key": "latency_ms",
                     "attribute_type": "number", "aggregation": agg} for agg in aggregations],
        "filters": [], "breakdowns": [] if breakdown is None else [{
            "name": "session.id", "type": "custom_attribute", "source": "traces",
            "attribute_type": breakdown,
        }],
    }


def plan_for(body):
    return build_dashboard_reference_query(body, authorized_project_ids=[PROJECT], enabled=True)


def leaf(key, kind, value, op="equals"):
    return {"column_id": key, "filter_config": {
        "col_type": "SPAN_ATTRIBUTE", "filter_type": kind,
        "filter_op": op, "filter_value": value,
    }}


def test_opt_in_closed_allowlist_and_parameterized_scope():
    with pytest.raises(ReplayError, match="OPT_IN_REQUIRED"):
        build_dashboard_reference_query(config(), authorized_project_ids=[PROJECT])
    mutations = [
        lambda b: b.update(granularity="hour"),
        lambda b: b.update(allow_sampled=True),
        lambda b: b.update(project_ids=[]),
        lambda b: b["metrics"][0].update(type="system_metric"),
        lambda b: b["metrics"][0].update(source="datasets"),
        lambda b: b["metrics"][0].update(attribute_type="string"),
        lambda b: b["metrics"][0].update(aggregation="p99"),
        lambda b: b["breakdowns"].append(b["breakdowns"][0]),
        lambda b: b["breakdowns"][0].update(attribute_type="array"),
        lambda b: b.update(filters=[leaf("k", "number", True)]),
        lambda b: b.update(filters=[leaf("k", "number", math.inf)]),
        lambda b: b.update(filters=[leaf("k", "text", "x", "contains")]),
        lambda b: b["time_range"].update(custom_end=START),
    ]
    for mutate in mutations:
        body = config()
        mutate(body)
        with pytest.raises(ReplayError):
            plan_for(body)
    with pytest.raises(ReplayError):
        build_dashboard_reference_query(config(), authorized_project_ids=[], enabled=True)
    incompatible = config(("avg", "max"))
    incompatible["metrics"][1]["filters"] = [leaf("other", "number", 1)]
    with pytest.raises(ReplayError):
        plan_for(incompatible)
    body = config()
    body["metrics"][0]["attribute_key"] = "literal'key"
    plan = plan_for(body)
    assert "literal'key" not in plan.sql and plan.params["metric_key"] == "literal'key"
    inner = plan.sql.split("PREWHERE", 1)[1].split("ARRAY JOIN", 1)[0]
    assert "attrs_" not in inner and "is_deleted" not in inner
    assert "groupArray(tuple(" in plan.sql and "argMax" not in plan.sql
    assert "LIMIT" not in plan.sql and "SAMPLE" not in plan.sql
    from replay_observe_queries_readonly import validate_select
    assert validate_select(plan.sql, plan.params, [PROJECT])


def test_time_filter_must_equal_request_bounds_and_scalar_predicates_are_same_row():
    body = config(("avg", "max"))
    body["filters"] = [{"column_id": "created_at", "filter_config": {
        "col_type": "SYSTEM_METRIC", "filter_type": "datetime",
        "filter_op": "between", "filter_value": [START, END],
    }}, leaf("company_id", "number", 0)]
    for metric in body["metrics"]:
        metric["filters"] = [leaf("ready", "boolean", False)]
    plan = plan_for(body)
    assert "countIf" not in plan.sql and "attrs_number" in plan.sql and "attrs_bool" in plan.sql
    assert type(plan.params["ref_value_0"]) is int and plan.params["ref_value_0"] == 0
    assert plan.params["ref_value_1"] is False
    body["filters"][0]["filter_config"]["filter_value"][0] = END
    with pytest.raises(ReplayError):
        plan_for(body)


def test_full_grain_comparison_preserves_rows_aliases_and_float_precision():
    plan = plan_for(config(("avg", "min", "max", "p25", "p50")))
    values = (3.25, 0, 8, 1, 4)
    actual = [{"time_bucket": "2026-09-04 00:00:00", "breakdown_value": f"s{i}",
               **{f"dashboard_metric_value_{j}": v for j, v in enumerate(values)}} for i in range(257)]
    expected = [{"time_bucket": datetime(2026, 9, 4, tzinfo=timezone.utc), "breakdown_value": f"s{i}",
                 **{f"ref_value_{j}": v for j, v in enumerate(values)}} for i in range(257)]
    match = compare_dashboard_rows(plan, actual[::-1], expected)
    assert match["status"] == "AGGREGATE_MATCH" and match["compared_cells"] == 1285
    assert match["full_row_metrics_verified"] and not match["qualification"]
    assert compare_dashboard_rows(plan, actual[:-1], expected)["missing_rows"] == 1
    extra = {**actual[-1], "breakdown_value": "rare-extra"}
    assert compare_dashboard_rows(plan, actual + [extra], expected)["extra_rows"] == 1
    with pytest.raises(ReplayError, match="DUPLICATE_GRAIN"):
        compare_dashboard_rows(plan, actual + [actual[0]], expected)
    wrong = deepcopy(actual)
    wrong[0]["dashboard_metric_value_0"] = math.nextafter(3.25, math.inf)
    assert compare_dashboard_rows(plan, wrong, expected)["different_cells"] == 1
    swapped = [f"dashboard_metric_value_{i}" for i in (0, 2, 1, 4, 3)]
    assert compare_dashboard_rows(plan, actual, expected, value_columns=swapped)["different_cells"] == 1028
    for malformed in ({**actual[0], "extra": 1}, {"time_bucket": START},
                      {**actual[0], "time_bucket": START}):
        with pytest.raises(ReplayError):
            compare_dashboard_rows(plan, [malformed], expected)
    for bad in (None, False, "3.25", math.nan, math.inf, 10**400):
        with pytest.raises(ReplayError):
            compare_dashboard_rows(plan, [{**actual[0], "dashboard_metric_value_0": bad}], expected)
    with pytest.raises(ReplayError, match="COLUMN_MAPPING"):
        compare_dashboard_rows(plan, actual, expected, value_columns=[])
    empty = compare_dashboard_rows(plan, [], [])
    assert empty["status"] == "AGGREGATE_MATCH" and not empty["full_row_metrics_verified"]


def test_reference_preserves_microseconds_and_utc_bounds():
    body = config()
    body["time_range"]["custom_start"] = "2026-09-04T05:15:00.123456-07:00"
    plan = plan_for(body)
    assert plan.params["start"] == "2026-09-04 12:15:00.123456"
    assert plan.params["end"] == "2026-09-04 13:00:00"


@pytest.mark.parametrize("kind,value,wrong", [("string", "0", 0), ("number", 0, False), ("boolean", False, 0)])
def test_breakdown_type_never_conflates_false_zero_and_text(kind, value, wrong):
    plan = plan_for(config(breakdown=kind))
    row = {"time_bucket": "2026-09-04", "breakdown_value": value}
    expected = [{**row, "ref_value_0": 0}]
    assert compare_dashboard_rows(plan, [{**row, "value": 0}], expected)["status"] == "AGGREGATE_MATCH"
    with pytest.raises(ReplayError):
        compare_dashboard_rows(plan, [{**row, "breakdown_value": wrong, "value": 0}], expected)


@pytest.fixture
def dashboard_native(tmp_path):
    # Lazy import keeps pure comparator/allowlist tests independent of Django.
    from tracer.tests.test_dashboard_exact_candidate_optimization import dashboard_native as existing
    yield from existing.__wrapped__(tmp_path)


def test_native_final_array_barrier_same_hour_boundary(dashboard_native, record_property):
    execute = dashboard_native
    execute("""INSERT INTO spans VALUES
        (%(p)s, 'SPAN', 'svc', '2026-09-04 12:20:00', 't', 's',
         map('session.id','moved'), map('latency_ms',99), map(), 0, 1),
        (%(p)s, 'SPAN', 'svc', '2026-09-04 12:10:00', 't', 's',
         map('session.id','moved'), map('latency_ms',1), map(), 0, 2)""", {"p": PROJECT})
    assert execute("SELECT _version FROM spans FINAL") == [{"_version": 2}]
    raw = execute("SELECT _version FROM spans FINAL WHERE start_time >= '2026-09-04 12:15:00'")
    record_property("unbarriered_final_versions", str(raw))
    plan = plan_for(config())
    assert execute(plan.sql, plan.params) == []
    # Separate materialized scalar VALUES, not another FINAL-WHERE assumption.
    assert execute("""SELECT * FROM values('start_time DateTime64(6), value Float64',
        ('2026-09-04 12:10:00', 1)) WHERE start_time >= '2026-09-04 12:15:00'""") == []


def test_native_257_series_generated_queries_against_materialized_final(dashboard_native):
    from tracer.serializers.dashboard import DashboardQuerySerializer
    from tracer.tests.test_dashboard_exact_candidate_optimization import _builder
    execute = dashboard_native
    body = config(("avg", "min", "max", "p25", "p50"))
    body["time_range"]["custom_end"] = "2026-09-05T13:23:00+00:00"
    rows = []

    def add(name, value=100, **changes):
        row = dict(project_id=PROJECT, observation_type="SPAN", service_name="svc",
                   start_time="2026-09-04 12:20:00", trace_id=name, id=name,
                   attrs_string={"session.id": "s000"},
                   attrs_number={"latency_ms": value, "company_id": 0},
                   attrs_bool={"ready": False}, is_deleted=0, _version=1)
        row.update(changes)
        rows.append(row)
        return row

    def revise(row, **changes):
        add(row["id"], **{**row, "_version": 2, **changes})

    for i in range(257):
        for j, value in enumerate((0, 1, 4, 8)):
            add(f"s{i}-{j}", value, attrs_string={"session.id": f"s{i:03}"})
        if i in (0, 128, 256):
            for j, value in enumerate((2, 6)):
                add(f"s{i}-day2-{j}", value, start_time="2026-09-05 12:20:00",
                    attrs_string={"session.id": f"s{i:03}"})
    changes = {
        "metric_clear": {"attrs_number": {"company_id": 0}},
        "deleted": {"is_deleted": 1},
        "filter_clear": {"attrs_number": {"latency_ms": 100}},
        "filter_changed": {"attrs_number": {"latency_ms": 100, "company_id": 1}},
        "breakdown_clear": {"attrs_string": {}},
        "type_changed": {"attrs_number": {"company_id": 0}, "attrs_string": {"session.id": "s000", "latency_ms": "100"}},
        "before_start": {"start_time": "2026-09-04 12:10:00"},
    }
    for name, change in changes.items():
        revise(add(name), **change)
    revise(add("after_end", start_time="2026-09-05 13:20:00"), start_time="2026-09-05 13:23:00")
    revise(add("moved_in", start_time="2026-09-04 12:10:00"), start_time="2026-09-04 12:15:00")
    revise(add("metric_changed"), attrs_number={"company_id": 0, "latency_ms": 42})
    revise(add("breakdown_changed"), attrs_string={"session.id": "s001"})
    for key, value in (("service_name", "other"), ("observation_type", "TOOL"),
                       ("trace_id", "other"), ("start_time", "2026-09-04 13:20:00"),
                       ("project_id", "22222222-2222-4222-8222-222222222222")):
        revise(add("identity_" + key), **{key: value, "is_deleted": 1})
    for name in ("split_spans", "split_versions"):
        add(name, attrs_bool={})
        add(name, id=name + "-other" if name == "split_spans" else name,
            _version=2, attrs_number={"latency_ms": 100})
    add("wrong_type", attrs_number={"latency_ms": 100},
        attrs_string={"session.id": "s000", "company_id": "0"})
    execute("INSERT INTO spans VALUES " + ",".join(f"%(r{i})s" for i in range(len(rows))),
            {f"r{i}": tuple(row.values()) for i, row in enumerate(rows)})
    # The FINAL query has NO mutable filters. Its completed result is the hard
    # client materialization barrier; VALUES aggregation cannot push into spans.
    winners = execute("SELECT project_id,start_time,is_deleted,attrs_number,attrs_string,attrs_bool FROM spans FINAL")
    gold_params = {f"w{i}": (row["project_id"], row["start_time"], row["is_deleted"],
        *[part for name in ("attrs_number", "attrs_string", "attrs_bool")
          for part in (list(row[name]), list(row[name].values()))]) for i, row in enumerate(winners)}
    gold_source = ("(SELECT project_id,start_time,is_deleted,mapFromArrays(nk,nv) AS attrs_number,"
        "mapFromArrays(sk,sv) AS attrs_string,mapFromArrays(bk,bv) AS attrs_bool FROM values("
        "'project_id UUID,start_time DateTime64(6),is_deleted UInt8,"
        "nk Array(String),nv Array(Float64),sk Array(String),sv Array(String),bk Array(String),bv Array(Bool)', ")
    gold_source += ",".join(f"%(w{i})s" for i in range(len(winners))) + "))"
    for filtered in (False, True):
        request = deepcopy(body)
        if filtered:
            request["filters"] = [leaf("company_id", "number", 0)]
            for metric in request["metrics"]:
                metric["filters"] = [leaf("ready", "boolean", False)]
        else:
            request["metrics"] = request["metrics"][:1]
        serializer = DashboardQuerySerializer(data=request)
        assert serializer.is_valid(), serializer.errors
        builder = _builder(serializer.validated_data)
        grouped = builder.build_compatible_metric_group_query(latest_state=True) if filtered else None
        sql, params = (grouped.sql, grouped.params) if grouped else builder.build_metric_query(builder.metrics[0])
        actual = execute(sql, params)  # Actual generated SQL, unchanged.
        plan = plan_for(request)
        reference = execute(plan.sql, plan.params)
        expressions = ("avg", "min", "max", "quantileExact(0.25)", "quantileExact(0.5)") if filtered else ("avg",)
        oracle = execute("SELECT toStartOfDay(start_time) AS time_bucket, attrs_string['session.id'] AS breakdown_value, "
            + ",".join(f"{agg}(attrs_number['latency_ms']) AS ref_value_{i}" for i, agg in enumerate(expressions))
            + " FROM " + gold_source + " WHERE project_id=%(p)s AND is_deleted=0"
            + " AND start_time>='2026-09-04 12:15:00' AND start_time<'2026-09-05 13:23:00'"
            + " AND mapContains(attrs_number,'latency_ms') AND mapContains(attrs_string,'session.id')"
            + (" AND mapContains(attrs_number,'company_id') AND attrs_number['company_id']=0"
               " AND mapContains(attrs_bool,'ready') AND attrs_bool['ready']=false" if filtered else "")
            + " GROUP BY time_bucket,breakdown_value ORDER BY time_bucket,breakdown_value", {**gold_params, "p": PROJECT})
        assert reference == oracle
        compared = compare_dashboard_rows(plan, actual, reference,
                    value_columns=grouped.value_columns if grouped else None)
        assert compared["status"] == "AGGREGATE_MATCH" and compared["reference_rows"] == 260
        assert len({r["breakdown_value"] for r in reference}) == 257
        stable = next(r for r in reference if r["breakdown_value"] == "s100")
        assert [stable[f"ref_value_{i}"] for i in range(len(expressions))] == ([3.25, 0, 8, 1, 4] if filtered else [3.25])


@pytest.mark.parametrize("kind", [None, "number", "boolean"])
def test_native_scalar_breakdown_grains(dashboard_native, kind):
    from tracer.tests.test_dashboard_exact_candidate_optimization import _builder
    execute = dashboard_native
    execute("""INSERT INTO spans VALUES
        (%(p)s,'SPAN','svc','2026-09-04 12:20:00','t','a',map(),
          map('latency_ms',0,'session.id',0),map('session.id',false),0,1),
        (%(p)s,'SPAN','svc','2026-09-04 12:20:00','t','b',map(),
          map('latency_ms',4,'session.id',1),map('session.id',true),0,1)""", {"p": PROJECT})
    body = config(breakdown=kind)
    builder = _builder(body)
    actual = execute(*builder.build_metric_query(builder.metrics[0]))
    plan = plan_for(body)
    expected = execute(plan.sql, plan.params)
    assert compare_dashboard_rows(plan, actual, expected)["status"] == "AGGREGATE_MATCH"
    assert [r["ref_value_0"] for r in expected] == ([2] if kind is None else [0, 4])


def test_native_typed_membership_unicode_requires_full_engine(dashboard_native):
    from tracer.tests.test_dashboard_exact_candidate_optimization import _builder
    execute = dashboard_native
    if not execute("SELECT count() AS n FROM system.functions WHERE name='lowerUTF8'")[0]["n"]:
        pytest.skip("native typed text comparison requires real lowerUTF8; no ASCII rewrite")
    body = config()
    item = leaf("company_id", "text", [0, "0", False], "in")
    item["filter_config"]["attribute_value_types"] = ["number", "string", "boolean"]
    body["filters"] = [item, leaf("prompt_slug", "text", "réponse")]
    for i, (strings, numbers, booleans) in enumerate([
        ({}, {"company_id": 0}, {}), ({"company_id": "0"}, {}, {}), ({}, {}, {"company_id": False}),
    ]):
        execute("INSERT INTO spans VALUES %(row)s", {"row": (PROJECT, "SPAN", "svc", "2026-09-04 12:20:00",
            f"t{i}", f"s{i}", {**strings, "session.id": f"s{i}", "prompt_slug": "RÉPONSE"},
            {**numbers, "latency_ms": (i + 1) * 10}, booleans, 0, 1)})
    builder = _builder(body)
    actual = execute(*builder.build_metric_query(builder.metrics[0]))
    plan = plan_for(body)
    expected = execute(plan.sql, plan.params)
    assert compare_dashboard_rows(plan, actual, expected)["status"] == "AGGREGATE_MATCH"
    assert [(r["breakdown_value"], r["ref_value_0"]) for r in expected] == [("s0", 10), ("s1", 20), ("s2", 30)]
