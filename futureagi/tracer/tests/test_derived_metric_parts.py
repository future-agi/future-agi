# ruff: noqa: F811 -- pytest fixtures are imported from the ClickHouse suite
"""Parts retain raw moments, scope evidence and support until the read window."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from tracer.services.clickhouse.v2.apply_schema_rewriter import split_statements
from tracer.services.derived_metrics.formula import compile_formula
from tracer.services.derived_metrics.parts import (
    LEGACY_SCOPE,
    SCALAR_PART_COLUMNS,
    combine_parts_select,
    field_rows_as_units,
    stratum_parts_select,
)
from tracer.services.derived_metrics.queries import (
    QUERY_SETTINGS,
    UNCERTAINTY_COLUMNS,
    WINDOW_SCOPE_GUARD_SQL,
    Scope,
    aggregate_select,
)
from tracer.services.derived_metrics.storage import BUCKET_STAT_SCHEMA
from tracer.services.derived_metrics.unit_reads import committed_unit_values_select
from tracer.tests.test_derived_metric_clickhouse import (  # noqa: F401
    SCHEMA,
    metric_ch,
)
from tracer.tests.test_derived_metric_uncertainty import (
    insert_units,
    reference,
    unit,
)

FORMULAS = ("mean(y)", "mean(y > 0)", "sum(y)", "count_if(y > 0)", "ratio(y, x)")
# Every metric value, numerator/denominator, count and uncertainty column.
RESULT_COLUMNS = (
    "group_key",
    "value",
    "numerator",
    "denominator",
    "status",
    "unit_count",
    "valid_count",
    "missing_count",
    "invalid_probability_count",
    "std_error",
    "ci_low",
    "ci_high",
    "effective_n",
    "ci_df",
    "ci_method",
    "ci_status",
    "ci_reason",
    "ci_interval",
)


def rows_as_dicts(client, sql, params):
    rows, columns = client.execute(
        sql, params, with_column_types=True, settings=QUERY_SETTINGS
    )
    return [dict(zip([c[0] for c in columns], row, strict=True)) for row in rows]


def assert_same(actual, expected):
    assert actual.keys() == expected.keys()
    for key, value in expected.items():
        if isinstance(value, float):
            assert actual[key] == pytest.approx(value, rel=1e-9, abs=1e-12), key
        else:
            assert actual[key] == value, key


def by_group(rows):
    return {row["group_key"]: row for row in rows}


# --- No services ---------------------------------------------------------------------


def test_aggregate_is_exactly_the_parts_pipeline_without_services():
    for text in FORMULAS:
        formula = compile_formula(text, ["y", "x"])
        for rollup in ["overall", "cohort", "user", "session", "day"]:
            assert aggregate_select(
                "SELECT * FROM units", formula, rollup=rollup
            ) == combine_parts_select(
                stratum_parts_select(
                    "SELECT * FROM units",
                    formula,
                    rollup=rollup,
                    scope_column=LEGACY_SCOPE,
                ),
                formula,
                rollup=rollup,
            )
    assert set(SCALAR_PART_COLUMNS) == {name for name, _ in BUCKET_STAT_SCHEMA} - {
        "uniq_state",
        "quantile_state",
    }
    live = stratum_parts_select("SELECT * FROM units", formula)
    assert "sampling_scope AS scope_key" in live
    assert "scope_min" in live and "nonempty_scope_count" in live
    assert "fpc_h" not in live  # never freeze a per-bucket finite-population correction
    assert "uniq_state" not in live and "quantile_state" not in live  # DCM4
    # Legacy snapshot sources (and ad hoc units) may carry no scope column at all.
    legacy = stratum_parts_select(
        "SELECT * FROM units", formula, scope_column=LEGACY_SCOPE
    )
    assert "sampling_scope" not in legacy and "'' AS scope_key" in legacy
    bucketed = stratum_parts_select(
        "SELECT * FROM units", formula, bucket_column="bucket_start"
    )
    assert bucketed.startswith("SELECT bucket_start, group_key, scope_key")
    assert "GROUP BY bucket_start, scope_key, stratum_key, group_key" in bucketed
    with pytest.raises(ValueError):
        stratum_parts_select("SELECT 1", formula, scope_column="cohort")
    with pytest.raises(ValueError):
        stratum_parts_select("SELECT 1", formula, bucket_column="unit_start")
    adapter = field_rows_as_units("SELECT * FROM rows")
    assert "inputs AS input_values" in adapter
    assert "sample_probability AS probability" in adapter
    assert "group_key AS cohort" in adapter
    assert "is_deleted = 0 AND status != 'open'" in adapter


def test_period_partition_and_requested_domains_keep_the_default_scope_guard():
    formula = compile_formula("sum(y)", ["y"])
    ordinary = combine_parts_select("SELECT * FROM parts", formula)
    batched = combine_parts_select("SELECT * FROM parts", formula, per_period=True)
    grouped = combine_parts_select("SELECT * FROM parts", formula, rollup="cohort")
    assert "period_key" not in ordinary
    assert "OVER ()" in ordinary
    assert "GROUP BY scope_key, stratum_key\n" in ordinary
    assert "SELECT '' AS group_key, scope_key, stratum_key" in ordinary
    assert "OVER (PARTITION BY period_key)" in batched
    assert "GROUP BY period_key, scope_key, stratum_key\n" in batched
    assert "SELECT period_key AS group_key" in batched
    assert "OVER ()" in grouped
    assert "GROUP BY scope_key, stratum_key, group_key" in grouped
    assert "SELECT group_key, scope_key, stratum_key" in grouped
    for rollup in ["cohort", "day", "session", "user"]:
        with pytest.raises(ValueError, match="overall rollup"):
            combine_parts_select(
                "SELECT * FROM parts", formula, rollup=rollup, per_period=True
            )


# --- Live ClickHouse -----------------------------------------------------------------


@pytest.mark.parametrize("design", ["closed", "live", "unweighted"])
@pytest.mark.parametrize("text", FORMULAS)
def test_live_disjoint_bucket_parts_match_direct_units_and_independent_oracle(
    metric_ch, design, text
):
    scope, snapshot = Scope(uuid4(), uuid4(), uuid4()), uuid4()
    values = [1.0, 0.0, 3.0, 2.0, 0.0, 5.0]
    probabilities = [1.0] * 6 if design == "unweighted" else [0.5] * 6
    units = [
        unit(
            y,
            p,
            "same-stratum",
            design=design,
            population=12 if design == "closed" else None,
            x=float(i + 1),
        )
        for i, (y, p) in enumerate(zip(values, probabilities, strict=True))
    ]
    insert_units(metric_ch, scope, snapshot, units)
    source = "SELECT * FROM derived_metric_inputs WHERE snapshot_id = %(snapshot)s"
    formula = compile_formula(text, ["y", "x"])
    params = {"snapshot": str(snapshot), **formula.parameters}
    direct = rows_as_dicts(
        metric_ch, aggregate_select(source, formula, rollup="overall"), params
    )[0]
    # Three disjoint unit batches; one stratum spans every batch, so correcting
    # each batch before summing would be wrong.
    batches = []
    for offset in range(0, 6, 2):
        name = f"ids_{offset}"
        params[name] = tuple(row[0] for row in units[offset : offset + 2])
        batches.append(
            stratum_parts_select(source + f" AND unit_id IN %({name})s", formula)
        )
    merged = rows_as_dicts(
        metric_ch,
        combine_parts_select(
            " UNION ALL ".join(f"({batch})" for batch in batches), formula
        ),
        params,
    )[0]
    assert_same(merged, direct)
    y = [float(value > 0) for value in values] if "> 0" in text else values
    expected = reference(
        formula.kind,
        design,
        y=y,
        p=probabilities,
        x=[float(i + 1) for i in range(6)] if text.startswith("ratio") else None,
        stratum=["same-stratum"] * 6,
        population=[12] * 6,
        rate=text == "mean(y > 0)",
    )
    assert merged["value"] == pytest.approx(expected["value"], rel=1e-9)
    if merged["ci_status"] == "ok":
        for name in ["std_error", "ci_low", "ci_high", "effective_n"]:
            assert merged[name] == pytest.approx(expected[name], rel=1e-9, abs=1e-12)
    assert merged["unit_count"] == merged["valid_count"] == 6


def _cohort(rows, cohort):
    return [(row[0], cohort, *row[2:]) for row in rows]


# The hand-computed vectors of test_derived_metric_uncertainty.py (closed, live
# and unweighted designs, censuses, padding, support and insufficient-design
# cases), re-used unchanged as frozen units.
UNCERTAINTY_FIXTURES = {
    "closed_stratified_ratio": lambda: [
        unit(1, 0.5, "a", population=4),
        unit(0, 0.5, "a", population=4),
        *[unit(y, 1.0, "b", population=3) for y in (1, 1, 0)],
    ],
    "large_offset_cancellation": lambda: [
        unit(1e13 + y, 0.5, population=8) for y in (1, 2, 3, 4)
    ],
    "more_units_than_design": lambda: [unit(y, 0.5, population=4) for y in (0, 1, 0)],
    "census_of_certainty_units": lambda: [
        unit(y, 1.0, "a", population=3) for y in (1, 1, 0)
    ],
    "unanimous_sample": lambda: [unit(1, 0.5, "a", population=8) for _ in range(4)],
    "zero_event_closed_sample": lambda: [
        unit(0, 0.005, population=10_000) for _ in range(50)
    ],
    "zero_event_live_sample": lambda: [
        unit(0, 0.005, design="live", population=10_000) for _ in range(50)
    ],
    "zero_event_census": lambda: [
        unit(0, 1.0, design="live", population=50) for _ in range(50)
    ],
    "closed_domain_padding": lambda: [
        unit(1, 0.4, population=10),
        unit(3, 0.4, population=10),
    ],
    "live_mixed_probability": lambda: [
        unit(1, p, "live", design="live") for p in (0.5, 0.25, 1.0)
    ],
    "live_poisson": lambda: [
        *[unit(y, p, str(p), design="live") for p in (0.5, 0.25) for y in (0, 1)],
        unit(1, 1.0, "known", design="live"),
    ],
    "unweighted_mean": lambda: [
        unit(y, 1.0, "", design="unweighted") for y in (1, 2, 3, 6)
    ],
    "single_unit_stratum": lambda: [
        unit(1, 0.2, "a", population=5),
        unit(0, 0.5, "b", population=4),
    ],
    "missing_population": lambda: [unit(1, 0.5), unit(0, 0.5)],
    "inconsistent_probability": lambda: [
        unit(1, 0.5, population=8),
        unit(0, 0.25, population=8),
    ],
    "legacy_snapshot": lambda: [unit(1, 0.5, design=""), unit(0, 0.5, design="")],
    "mixed_designs": lambda: [
        unit(1, 0.5, population=4),
        unit(0, 0.5, design="live"),
    ],
    "zero_denominator": lambda: [
        unit(1, 0.5, population=4, x=0),
        unit(0, 0.5, population=4, x=0),
    ],
    "missing_and_invalid": lambda: [
        unit(1, 0.5, population=8),
        unit(None, 0.5, population=8),
        unit(0, 0.5, population=8, invalid=1),
        unit(2, 0.5, population=8),
    ],
    "rollup_domains": lambda: [
        *_cohort([unit(y, 0.5, population=8) for y in (1, 0)], "east"),
        *_cohort([unit(y, 0.5, population=8) for y in (1, 1)], "west"),
    ],
}


@pytest.mark.parametrize("fixture", sorted(UNCERTAINTY_FIXTURES))
def test_packed_projection_matches_every_unpacked_statistic(metric_ch, fixture):
    """The shallow read projection is equivalent to the persisted CI pipeline.

    Cover every frozen uncertainty fixture, including cancellation, absent design,
    missing/invalid inputs, padding and heterogeneous weights. Compare all fields,
    not just the point or the presence of an interval, at the existing tolerance.
    """
    scope, snapshot = Scope(uuid4(), uuid4(), uuid4()), uuid4()
    insert_units(metric_ch, scope, snapshot, UNCERTAINTY_FIXTURES[fixture]())
    source = "SELECT * FROM derived_metric_inputs WHERE snapshot_id = %(snapshot)s"
    for text in FORMULAS:
        formula = compile_formula(text, ["y", "x"])
        params = {"snapshot": str(snapshot), **formula.parameters}
        for rollup in ("overall", "cohort"):
            parts = stratum_parts_select(
                source, formula, rollup=rollup, scope_column=LEGACY_SCOPE
            )
            expected = by_group(
                rows_as_dicts(
                    metric_ch,
                    combine_parts_select(parts, formula, rollup=rollup),
                    params,
                )
            )
            actual = rows_as_dicts(
                metric_ch,
                combine_parts_select(
                    parts, formula, rollup=rollup, packed_uncertainty=True
                ),
                params,
            )
            assert by_group(actual).keys() == expected.keys(), (text, rollup)
            for row in actual:
                row.update(zip(UNCERTAINTY_COLUMNS, row.pop("ci"), strict=True))
                assert_same(row, expected[row["group_key"]])


@pytest.fixture
def parts_ch(metric_ch):
    for filename in ["031_metric_field_values.sql", "032_metric_bucket_totals.sql"]:
        for statement in split_statements((SCHEMA / filename).read_text()):
            metric_ch.execute(statement)
    return metric_ch


def _stored_units(client, scope, field_id, attempt, rows, base):
    """Frozen units as committed field rows spread over 15-minute buckets."""
    values = []
    for index, row in enumerate(rows):
        identity, cohort, inputs, probability, stratum, design, population, invalid = (
            row
        )
        # Three buckets, and units of one stratum land in different buckets.
        start = base + timedelta(minutes=15 * (index % 3), seconds=index)
        values.append(
            (
                scope.organization,
                scope.workspace,
                scope.project,
                field_id,
                1,
                "conversation",
                identity,
                identity,
                "",
                start,
                start.replace(minute=start.minute - start.minute % 15, second=0),
                "ok",
                inputs,
                probability,
                "scope" if design in {"closed", "live"} else "",
                stratum,
                design,
                population,
                invalid,
                cohort,
                0,
                1,
                1,
                attempt,
            )
        )
    # Open and deleted units must never reach the parts: they would add counts.
    for status, deleted in [("open", 0), ("ok", 1)]:
        identity = str(uuid4())
        values.append(
            (
                scope.organization,
                scope.workspace,
                scope.project,
                field_id,
                1,
                "conversation",
                identity,
                identity,
                "",
                base,
                base,
                status,
                {"y": 1000.0, "x": 1.0},
                0.5,
                "other-scope",
                "h1",
                "closed",
                8,
                0,
                "east",
                deleted,
                1,
                1,
                attempt,
            )
        )
    client.execute(
        """INSERT INTO metric_field_values (org_id, workspace_id, project_id, field_id,
        field_version, level, unit_id, session_id, user_id, unit_start, bucket_start,
        status, inputs, sample_probability, sampling_scope, sampling_stratum,
        sampling_design, population_size, invalid_probability, group_key, is_deleted,
        _version, generation, attempt_id) VALUES""",
        values,
    )
    return [value[6] for value in values]


@pytest.mark.parametrize("fixture", sorted(UNCERTAINTY_FIXTURES))
def test_live_per_fifteen_minute_bucket_parts_equal_direct_units(parts_ch, fixture):
    """Stored bucket parts, summed across buckets, equal the direct estimator."""
    scope, snapshot, field_id, attempt = (
        Scope(uuid4(), uuid4(), uuid4()),
        *(uuid4() for _ in range(3)),
    )
    rows = UNCERTAINTY_FIXTURES[fixture]()
    insert_units(parts_ch, scope, snapshot, rows)
    base = datetime(2026, 10, 28, 23, 30, tzinfo=UTC)  # buckets cross a day boundary
    ids = _stored_units(parts_ch, scope, field_id, attempt, rows, base)
    committed, committed_params = committed_unit_values_select(
        scope, field_id, 1, ids, {"delta_attempt_ids": [attempt]}
    )
    units = field_rows_as_units(committed)
    frozen = "SELECT * FROM derived_metric_inputs WHERE snapshot_id = %(snapshot)s"
    bucket_count = parts_ch.execute(
        f"SELECT uniqExact(bucket_start) FROM ({units})", committed_params
    )[0][0]
    assert bucket_count == min(3, len(rows))
    for text in FORMULAS:
        formula = compile_formula(text, ["y", "x"])
        for rollup in ["overall", "cohort"]:
            metric = uuid4()
            params = {
                **committed_params,
                **formula.parameters,
                "snapshot": str(snapshot),
                "metric": str(metric),
            }
            direct = rows_as_dicts(
                parts_ch, aggregate_select(units, formula, rollup=rollup), params
            )
            snapshot_rows = rows_as_dicts(
                parts_ch, aggregate_select(frozen, formula, rollup=rollup), params
            )
            # The committed field rows reproduce the frozen-snapshot estimator.
            assert by_group(direct).keys() == by_group(snapshot_rows).keys()
            for key, row in by_group(snapshot_rows).items():
                assert_same(by_group(direct)[key], row)
            parts = stratum_parts_select(
                units, formula, rollup=rollup, bucket_column="bucket_start"
            )
            columns = ", ".join(SCALAR_PART_COLUMNS)
            parts_ch.execute(
                f"""INSERT INTO metric_bucket_totals (org_id, workspace_id, project_id,
                metric_id, metric_version, bucket_start, group_key, scope_key, stratum_key,
                {columns}, is_deleted, version, generation, attempt_id)
                SELECT %(org_id)s, %(workspace_id)s, %(project_id)s, %(metric)s, 1,
                    bucket_start, group_key, scope_key, stratum_key, {columns}, 0, 1, 1,
                    %(metric)s
                FROM ({parts})""",
                params,
                settings=QUERY_SETTINGS,
            )
            stored = (
                f"SELECT group_key, scope_key, stratum_key, {columns} "
                "FROM metric_bucket_totals WHERE metric_id = %(metric)s"
            )
            assert (
                parts_ch.execute(
                    "SELECT uniqExact(bucket_start) FROM metric_bucket_totals "
                    "WHERE metric_id = %(metric)s",
                    params,
                )[0][0]
                == bucket_count
            )
            combined = by_group(
                rows_as_dicts(
                    parts_ch,
                    combine_parts_select(stored, formula, rollup=rollup),
                    params,
                )
            )
            assert combined.keys() == by_group(direct).keys(), (text, rollup)
            for key, row in by_group(direct).items():
                assert_same(combined[key], row)
                assert tuple(row) == RESULT_COLUMNS


def test_live_parts_preserve_missing_invalid_and_empty_design_extrema(metric_ch):
    scope, snapshot = Scope(uuid4(), uuid4(), uuid4()), uuid4()
    units = [
        unit(1, 0.5, population=8),
        unit(None, 0.5, population=8),
        unit(0, 0.5, population=8, invalid=1),
        unit(2, 0.5, population=8),
    ]
    insert_units(metric_ch, scope, snapshot, units)
    formula = compile_formula("mean(y)", ["y"])
    source = "SELECT * FROM derived_metric_inputs WHERE snapshot_id = %(snapshot)s"
    params = {"snapshot": str(snapshot)}
    parts = []
    for i, row in enumerate(units):
        params[f"id_{i}"] = row[0]
        parts.append(
            stratum_parts_select(source + f" AND unit_id = %(id_{i})s", formula)
        )
    direct = rows_as_dicts(
        metric_ch, aggregate_select(source, formula, rollup="overall"), params
    )[0]
    merged = rows_as_dicts(
        metric_ch,
        combine_parts_select(" UNION ALL ".join(f"({p})" for p in parts), formula),
        params,
    )[0]
    assert_same(merged, direct)
    assert merged["unit_count"] == 4 and merged["missing_count"] == 1
    assert merged["invalid_probability_count"] == 1 and merged["valid_count"] == 2


@pytest.mark.parametrize(
    "second_missing,second_invalid", [(False, False), (True, False), (True, True)]
)
def test_p6_two_closed_scopes_sharing_label_reject_across_batches_and_reruns(
    metric_ch, second_missing, second_invalid
):
    scope, snapshot = Scope(uuid4(), uuid4(), uuid4()), uuid4()
    units = [
        unit(y, p, "shared", population=n, invalid=int(second_invalid and i > 1))
        for i, (y, p, n) in enumerate(
            [
                (1.0, 0.5, 4),
                (0.0, 0.5, 4),
                (None if second_missing else 4.0, 0.25, 8),
                (None if second_missing else 2.0, 0.25, 8),
            ]
        )
    ]
    insert_units(metric_ch, scope, snapshot, units)
    formula = compile_formula("mean(y)", ["y"])
    # Assign two scopes in the source; the second scope can consist solely of
    # missing/invalid units. Each batch alone holds exactly one scope.
    source = """SELECT * REPLACE (if(probability = .5, 'scope-a', 'scope-b') AS sampling_scope)
        FROM derived_metric_inputs WHERE snapshot_id = %(snapshot)s"""
    params = {"snapshot": str(snapshot)}
    batches = [
        stratum_parts_select(source + f" AND probability = {p}", formula)
        for p in (0.5, 0.25)
    ]
    for batch in batches:
        alone = rows_as_dicts(metric_ch, combine_parts_select(batch, formula), params)
        locally_invalid = second_invalid and "probability = 0.25" in batch
        # A batch alone holds one scope, so nothing rejects it yet.
        assert alone[0]["invalid_probability_count"] == (2 if locally_invalid else 0)
        assert alone[0]["status"] != "invalid_probability" or locally_invalid
    # Overlapping reruns replace their complete bucket, they do not add a second
    # contribution. Mimic the committed latest-bucket collapse before combining.
    history = " UNION ALL ".join(
        f"SELECT *, {revision} AS revision FROM ({batch})"
        for batch, revision in [(batches[0], 1), (batches[1], 1), (batches[0], 2)]
    )
    latest = f"SELECT * FROM ({history}) ORDER BY revision DESC LIMIT 1 BY group_key, scope_key, stratum_key"
    result = rows_as_dicts(metric_ch, combine_parts_select(latest, formula), params)[0]
    # The existing rejection: snapshot_select's window scope guard, then the
    # legacy estimator over the guarded units.
    guarded = f"""SELECT * REPLACE (toUInt8({WINDOW_SCOPE_GUARD_SQL}) AS invalid_probability)
        FROM (SELECT *, invalid_probability AS local_invalid_probability FROM ({source}))"""
    direct = rows_as_dicts(
        metric_ch, aggregate_select(guarded, formula, rollup="overall"), params
    )[0]
    assert_same(result, direct)
    # Independent oracle: >1 nonempty scope invalidates ALL four scoped rows,
    # before complete-case counting or closed-moment pooling, even for equal labels.
    assert result["status"] == "invalid_probability"
    assert result["value"] is result["numerator"] is result["denominator"] is None
    assert result["unit_count"] == result["invalid_probability_count"] == 4
    assert result["valid_count"] == 0 and result["ci_reason"] == "no_value"
    assert result["missing_count"] == (2 if second_missing else 0)


def test_live_empty_parts_overall_still_returns_no_data(metric_ch):
    formula = compile_formula("mean(y)", ["y"])
    for scope_column in ["sampling_scope", LEGACY_SCOPE]:
        result = rows_as_dicts(
            metric_ch,
            combine_parts_select(
                stratum_parts_select(
                    "SELECT * FROM derived_metric_inputs WHERE 0",
                    formula,
                    scope_column=scope_column,
                ),
                formula,
            ),
            {},
        )[0]
        assert result["status"] == "no_data" and result["unit_count"] == 0
