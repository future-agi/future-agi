# ruff: noqa: F811 -- pytest fixtures are imported from the ClickHouse suite
"""Real ClickHouse proofs for finite-ID recomputation and committed overlays."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from tracer.services.clickhouse.v2.apply_schema_rewriter import split_statements
from tracer.services.derived_metrics.formula import FormulaError, compile_formula
from tracer.services.derived_metrics.queries import (
    QUERY_SETTINGS,
    Scope,
    snapshot_select,
)
from tracer.services.derived_metrics.storage import FIELD_VALUES
from tracer.services.derived_metrics.unit_reads import (
    MAX_UNIT_IDS,
    UNIT_READ_EXTRA_COLUMNS,
    _source_plan,
    committed_unit_values_select,
    field_recipe,
    metric_value_type,
    unit_values_select,
)
from tracer.tests.test_derived_metric_clickhouse import (  # noqa: F401
    SCHEMA,
    metric_ch,
    recipe,
    seed_eval,
    seed_span,
)
from tracer.tests.test_derived_metric_parts import rows_as_dicts
from tracer.tests.test_derived_metric_typed_fields import descriptor

# Publication keys the writer adds; everything else in FIELD_VALUES is unit payload.
PUBLICATION_COLUMNS = (
    "org_id",
    "workspace_id",
    "project_id",
    "field_id",
    "field_version",
    "_version",
    "generation",
    "attempt_id",
    "fencing_epoch",
    "row_sequence",
)
PAYLOAD_COLUMNS = tuple(c for c in FIELD_VALUES.columns if c not in PUBLICATION_COLUMNS)
LINEAGE_COLUMNS = (
    "sample_probability",
    "sampling_scope",
    "sampling_stratum",
    "sampling_design",
    "population_size",
    "invalid_probability",
)


@pytest.fixture
def unit_ch(metric_ch):
    for filename in [
        "031_metric_field_values.sql",
        "034_metric_source_dependencies.sql",
    ]:
        for statement in split_statements((SCHEMA / filename).read_text()):
            metric_ch.execute(statement)
    return metric_ch


def scope_now():
    return Scope(uuid4(), uuid4(), uuid4()), datetime.now(UTC).replace(microsecond=0)


def read(client, definition, scope, ids, now, **kwargs):
    sql, params = unit_values_select(
        definition,
        scope,
        ids,
        start=now - timedelta(days=1),
        end=now,
        as_of=now,
        **kwargs,
    )
    return rows_as_dicts(client, sql, params)


def stage(client, definition, scope, ids, now, attempt, generation, **kwargs):
    """Write unit rows as one attempt, the way the refresh writer stages them."""
    sql, params = unit_values_select(
        definition,
        scope,
        ids,
        start=now - timedelta(days=1),
        end=now,
        as_of=now,
        **kwargs,
    )
    params.update(
        stage_field=str(definition.metric_id),
        stage_version=definition.version,
        stage_generation=generation,
        stage_attempt=str(attempt),
    )
    client.execute(
        f"""INSERT INTO metric_field_values ({", ".join(PUBLICATION_COLUMNS + PAYLOAD_COLUMNS)})
        SELECT %(org_id)s, %(workspace_id)s, %(project_id)s, %(stage_field)s, %(stage_version)s,
            %(stage_generation)s, %(stage_generation)s, %(stage_attempt)s, 1, 0,
            {", ".join(PAYLOAD_COLUMNS)}
        FROM ({sql})""",
        params,
        settings=QUERY_SETTINGS,
    )


def field(kind, inputs, **spec):
    return SimpleNamespace(
        kind=kind,
        inputs=inputs,
        field_spec=spec,
        level="conversation",
        weighting="none",
        cohort_key="",
        formula="mean(value)",
    )


def seed_typed(
    client,
    scope,
    session,
    when,
    *,
    number=None,
    boolean=None,
    text=None,
    trace=None,
    span_id="span",
    updated=None,
    user=None,
    version=1,
    deleted=0,
):
    params = {
        **scope.parameters(),
        "session": str(session),
        "trace": str(trace or uuid4()),
        "span": span_id,
        "user": str(user) if user else None,
        "at": when.strftime("%Y-%m-%d %H:%M:%S.%f"),
        "updated": (updated or when).strftime("%Y-%m-%d %H:%M:%S.%f"),
        "version": version,
        "deleted": deleted,
    }
    for name, values in [("number", number), ("boolean", boolean), ("text", text)]:
        params[f"{name}_keys"] = list(values or {})
        params[f"{name}_values"] = list((values or {}).values())
    client.execute(
        """INSERT INTO spans (id,project_id,org_id,trace_id,trace_session_id,end_user_id,
        start_time,created_at,updated_at,_version,is_deleted,attrs_number,attrs_bool,attrs_string)
        VALUES (%(span)s,%(project_id)s,%(org_id)s,%(trace)s,%(session)s,%(user)s,
            %(at)s,%(at)s,%(updated)s,%(version)s,%(deleted)s,
            mapFromArrays(%(number_keys)s,%(number_values)s),
            mapFromArrays(%(boolean_keys)s,%(boolean_values)s),
            mapFromArrays(%(text_keys)s,%(text_values)s))""",
        params,
    )
    return params["trace"]


def set_eval_output(client, config, *, output_bool=None, output_str=None, span=None):
    """Re-version one config's evaluation rows with typed outputs (NULL allowed)."""
    client.execute(
        """INSERT INTO tracer_eval_logger_v2
        SELECT * REPLACE (CAST(%(bool)s, 'Nullable(UInt8)') AS output_bool,
            CAST(%(text)s, 'Nullable(String)') AS output_str, _version + 1 AS _version)
        FROM tracer_eval_logger_v2 WHERE custom_eval_config_id = %(config)s
            AND (%(span)s IS NULL OR observation_span_id = %(span)s)""",
        {"config": str(config), "bool": output_bool, "text": output_str, "span": span},
    )


# --- No services ---------------------------------------------------------------------


def test_unit_api_is_finite_lineage_complete_and_trace_level_stays_off(settings):
    scope, now = scope_now()
    with pytest.raises(ValueError):
        read(None, recipe(), scope, [], now)
    with pytest.raises(ValueError):
        read(None, recipe(), scope, [uuid4() for _ in range(MAX_UNIT_IDS + 1)], now)
    definition = recipe()
    definition.level = "trace"
    with pytest.raises(FormulaError, match="conversation"):
        read(None, definition, scope, [uuid4()], now)
    sql, params = unit_values_select(
        recipe([uuid4(), uuid4()], "inverse_probability"),
        scope,
        [uuid4()],
        start=now - timedelta(days=1),
        end=now,
        as_of=now,
    )
    assert "PREWHERE project_id = %(project_id)s AND trace_session_id IN" in sql
    assert (
        "PREWHERE project_id = %(project_id)s AND ((trace_id, id) IN (SELECT trace_key, span_key FROM unit_source_keys))"
        in sql
    )
    assert "start_time >= %(start)s" not in sql  # T6: no start bound
    final_select = sql[
        sql.rindex("SELECT unit_id, session_id, user_id, 'conversation'") :
    ]
    for column in LINEAGE_COLUMNS:
        assert column in final_select
    # Unit reads carry LOCAL invalid evidence; the window guard runs on combine.
    assert "uniqExactIf(sampling_scope" not in sql
    # Trace-sampled evidence never stands in for a conversation's probability.
    assert params["selection_unit"] == "session"
    assert params["unit_read_ids"]


def test_metric_kind_value_column_mapping_and_legacy_formula_without_services():
    cases = {
        ("share", "boolean"): "value_bool",
        ("share", "number"): "value_bool",
        ("count", "text"): "value_bool",
        ("average", "number"): "value_number",
        ("total", "number"): "value_number",
        ("percentile", "number"): "value_number",
        ("custom", "number"): "value_number",
        ("unique_count", "text"): "value_text",
        ("unique_count", "number"): "value_number",
        ("unique_count", "boolean"): "value_bool",
    }
    for (kind, source_type), column in cases.items():
        value_type = metric_value_type(kind, source_type)
        assert {
            "boolean": "value_bool",
            "number": "value_number",
            "text": "value_text",
        }[value_type] == column
    for kind, source_type in [
        ("average", "text"),
        ("total", "boolean"),
        ("percentile", "text"),
        ("median", "number"),
    ]:
        with pytest.raises(FormulaError):
            metric_value_type(kind, source_type)
    share = field_recipe(field("share", [descriptor("boolean")]))
    assert share.value_column == "value_bool" and share.formula.rate_eligible
    assert share.source.field_spec["conditions"] == [
        {"alias": "value", "operator": "eq", "value": True}
    ]
    total = field_recipe(field("total", [descriptor("number", "sum")]))
    assert total.value_column == "value_number" and total.formula.kind == "sum"
    with pytest.raises(FormulaError):
        field_recipe(field("share", [descriptor("text")]))  # needs a condition
    # Legacy custom recipes compile byte-for-byte as before.
    legacy = recipe()
    compiled = field_recipe(legacy)
    assert compiled.kind == "custom" and compiled.value_column == "value_number"
    assert compiled.formula == compile_formula(
        legacy.formula, [item["alias"] for item in legacy.inputs]
    )
    assert compiled.source.inputs is legacy.inputs
    # The missing reason looks only at inputs the value actually needs.
    mixed = field(
        "share",
        [
            descriptor("text", alias="locale"),
            descriptor(
                "number", source="eval", alias="score", config_id="c", config_hash="h"
            ),
        ],
        conditions=[{"alias": "locale", "value": "fr"}],
    )
    assert [item["alias"] for item in field_recipe(mixed).required_inputs] == ["locale"]


# --- Live ClickHouse -----------------------------------------------------------------


def _explain(client, sql, params):
    plan = client.execute(
        "EXPLAIN PLAN actions=1, indexes=1 " + sql,
        params,
        settings={**QUERY_SETTINGS, "max_result_rows": 0},
    )
    return "\n".join(row[0] for row in plan)


# Lines a ReadFromMergeTree step prints at its own indent, before nested detail.
READ_DETAILS = ("ReadType", "Parts", "Granules", "Prewhere", "Need filter", "Indexes")


def _reads(text, table):
    """Each ReadFromMergeTree node over ``table``, with its detail lines.

    Empty tables plan as NullSource and IN-subquery sets are prepared outside the
    displayed plan, so callers seed rows and explain the nominee CTE separately.
    """
    lines = text.splitlines()

    def indent(line):
        return len(line) - len(line.lstrip())

    blocks = []
    for start, line in enumerate(lines):
        if not ("ReadFromMergeTree (" in line and line.rstrip().endswith(f".{table})")):
            continue
        end = start + 1
        while end < len(lines) and (
            indent(lines[end]) > indent(line)
            or (
                indent(lines[end]) == indent(line)
                and lines[end].strip().startswith(READ_DETAILS)
            )
        ):
            end += 1
        blocks.append("\n".join(lines[start:end]))
    return blocks


def _prewhere(block):
    return " ".join(
        line for line in block.splitlines() if "Prewhere filter column:" in line
    )


def _key_condition(block):
    """The PrimaryKey index condition (its Keys list precedes it)."""
    conditions, inside = [], False
    for line in (line.strip() for line in block.splitlines()):
        inside = (inside or line == "PrimaryKey") and not line.startswith("Parts:")
        if inside and line.startswith("Condition:"):
            conditions.append(line)
    return " ".join(conditions)


def test_unit_reads_match_snapshot_inputs_probability_stratum_and_lineage(unit_ch):
    scope, now = scope_now()
    sessions, configs = [uuid4() for _ in range(4)], [uuid4(), uuid4()]
    for i, session in enumerate(sessions):
        seed_span(
            unit_ch,
            scope,
            str(i),
            session,
            uuid4(),
            now - timedelta(hours=2),
            resolved=i % 2,
            cohort=None if i == 3 else "west",
        )
        for j, config in enumerate(configs):
            if i == 2 and j == 1:
                continue  # one unit misses an evaluation: complete-case evidence
            seed_eval(unit_ch, config, session, now - timedelta(hours=1), 1 - j, 0.5)
    for definition in [recipe(configs, "inverse_probability"), recipe()]:
        definition.cohort_key = ""
        snapshot, params = snapshot_select(
            definition, scope, now - timedelta(days=1), now, now, [], ""
        )
        frozen = {
            row["unit_id"]: row for row in rows_as_dicts(unit_ch, snapshot, params)
        }
        assert len(frozen) == 4
        actual = {
            row["unit_id"]: row
            for row in read(unit_ch, definition, scope, sessions[:3], now)
        }
        assert actual.keys() == {str(session) for session in sessions[:3]}
        for unit_id, row in actual.items():
            old = frozen[unit_id]
            assert row["inputs"] == old["input_values"]
            assert row["sample_probability"] == old["probability"]
            assert row["session_id"] == old["session_id"] == unit_id
            assert row["user_id"] == old["user_id"]
            assert row["source_rows"] == old["source_rows"]
            for name in LINEAGE_COLUMNS[1:]:
                assert row[name] == old[name], name
        assert set(next(iter(actual.values()))) == set(PAYLOAD_COLUMNS) | set(
            UNIT_READ_EXTRA_COLUMNS
        )
    weighted = read(
        unit_ch, recipe(configs, "inverse_probability"), scope, sessions[:3], now
    )
    by_unit = {row["unit_id"]: row for row in weighted}
    assert by_unit[str(sessions[0])]["value_number"] == 1
    assert (
        by_unit[str(sessions[0])]["part_n"] == by_unit[str(sessions[0])]["part_d"] == 1
    )
    missing = by_unit[str(sessions[2])]
    assert (
        missing["status"] == "no_value"
        and missing["no_value_reason"] == "not_evaluated"
    )
    assert missing["sampling_stratum"] == "region-west"


def test_explain_prewhere_has_project_and_unit_predicates_without_window_scan(unit_ch):
    scope, now = scope_now()
    session, config = uuid4(), uuid4()
    seed_span(unit_ch, scope, "s", session, uuid4(), now - timedelta(hours=2))
    seed_eval(unit_ch, config, session, now - timedelta(hours=1), 1, 0.5)
    definition = field(
        "average",
        [descriptor(source="eval", config_id=str(config), config_hash="a" * 64)],
    )
    definition.metric_id, definition.version = uuid4(), 1
    attempt = uuid4()
    stage(unit_ch, definition, scope, [session], now, attempt, 1)
    manifest = {"delta_attempt_ids": [attempt]}
    sql, params = unit_values_select(
        definition,
        scope,
        [session],
        start=now - timedelta(days=1),
        end=now,
        as_of=now,
        manifest=manifest,
    )
    text = _explain(unit_ch, sql, params)
    spans = _reads(text, "spans")
    assert spans, text
    for block in spans:
        # Every span read is the immutable-identity collapse: PREWHERE on the
        # project and the nominated (trace_id, id) keys, PK pruned on project_id,
        # and no time range anywhere (T6: no start bound, no window scan).
        prewhere = _prewhere(block)
        assert "equals(project_id" in prewhere, block
        assert "in(tuple(trace_id, id)" in prewhere, block
        assert "project_id in" in _key_condition(block), block
        assert "start_time" not in prewhere + _key_condition(block), block
    stored = _reads(text, "metric_field_values")
    assert stored, text
    for block in stored:
        prewhere = _prewhere(block)
        assert "equals(project_id" in prewhere and "in(unit_id" in prewhere, block
        assert "project_id in" in _key_condition(block), block
    # The nominee scan is prepared as an IN set, so explain its CTE on its own.
    ctes, _, plan_params = _source_plan(scope, (str(session),), manifest, ())
    nominee_sql = (
        f"WITH {', '.join(ctes)} SELECT trace_key, span_key FROM unit_source_keys"
    )
    nominee = _reads(
        _explain(unit_ch, nominee_sql, {**scope.parameters(), **plan_params}), "spans"
    )
    assert nominee
    for block in nominee:
        prewhere = _prewhere(block)
        assert "equals(project_id" in prewhere and "in(trace_session_id" in prewhere
        assert "project_id in" in _key_condition(block)
        assert "start_time" not in prewhere + _key_condition(block)


def test_remapped_sessions_and_users_and_ambiguous_user_are_canonical(unit_ch):
    scope, now = scope_now()
    old, other_old, new, user_old, user_new = [uuid4() for _ in range(5)]
    for source in [old, other_old]:
        unit_ch.execute(
            "INSERT INTO trace_session_id_remap (old_id,new_id) VALUES (%(old)s,%(new)s)",
            {"old": str(source), "new": str(new)},
        )
    unit_ch.execute(
        "INSERT INTO end_user_id_remap (old_id,new_id) VALUES (%(old)s,%(new)s)",
        {"old": str(user_old), "new": str(user_new)},
    )
    seed_span(unit_ch, scope, "old", old, user_old, now - timedelta(hours=3))
    seed_span(unit_ch, scope, "new", new, user_new, now - timedelta(hours=2))
    row = read(unit_ch, recipe(), scope, [new, other_old], now)[0]
    assert row["unit_id"] == row["session_id"] == str(min(old, other_old, key=str))
    # Both spans name the same end user once remapped: the survivor id is stored.
    assert row["user_id"] == str(user_old) and row["source_rows"] == 2
    seed_span(unit_ch, scope, "different-user", new, uuid4(), now - timedelta(hours=1))
    assert read(unit_ch, recipe(), scope, [new], now)[0]["user_id"] == ""
    no_user, one_user = uuid4(), uuid4()
    seed_typed(
        unit_ch,
        scope,
        no_user,
        now - timedelta(hours=2),
        number={"resolved": 1.0, "escalated": 0.0},
    )
    assert read(unit_ch, recipe(), scope, [no_user], now)[0]["user_id"] == ""
    seed_typed(
        unit_ch,
        scope,
        no_user,
        now - timedelta(hours=1),
        span_id="named",
        user=one_user,
        number={"resolved": 1.0, "escalated": 0.0},
    )
    assert read(unit_ch, recipe(), scope, [no_user], now)[0]["user_id"] == str(one_user)


def test_remap_retires_a_committed_unit_with_a_tombstone_at_its_old_anchor(unit_ch):
    scope, now = scope_now()
    first, second, new = uuid4(), uuid4(), uuid4()
    survivor, retired = sorted([first, second], key=str)
    starts = {survivor: now - timedelta(hours=3), retired: now - timedelta(hours=2)}
    for index, (session, at) in enumerate(starts.items()):
        seed_typed(
            unit_ch,
            scope,
            session,
            at,
            span_id=f"s{index}",
            number={"value": index + 1.0},
        )
    seed_typed(
        unit_ch,
        scope,
        new,
        now - timedelta(hours=1),
        span_id="pre-flip",
        number={"value": 10.0},
    )
    definition = field("total", [descriptor("number", "sum")])
    definition.metric_id, definition.version = uuid4(), 1
    attempt = uuid4()
    stage(unit_ch, definition, scope, [survivor, retired, new], now, attempt, 1)
    manifest = {"delta_attempt_ids": [attempt]}
    committed = {
        row["unit_id"]: row
        for row in read(
            unit_ch, definition, scope, [survivor, retired, new], now, manifest=manifest
        )
    }
    assert set(committed) == {str(survivor), str(retired), str(new)}
    # Now both olds and the pre-flip new id consolidate under one survivor.
    for source in [survivor, retired]:
        unit_ch.execute(
            "INSERT INTO trace_session_id_remap (old_id,new_id) VALUES (%(old)s,%(new)s)",
            {"old": str(source), "new": str(new)},
        )
    rows = {
        row["unit_id"]: row
        for row in read(
            unit_ch, definition, scope, [survivor, retired, new], now, manifest=manifest
        )
    }
    assert set(rows) == {str(survivor), str(retired), str(new)}
    assert rows[str(survivor)]["is_deleted"] == 0
    assert rows[str(survivor)]["value_number"] == 13.0
    assert rows[str(survivor)]["source_rows"] == 3
    for gone in [retired, new]:
        tombstone = rows[str(gone)]
        assert tombstone["is_deleted"] == 1 and tombstone["value_number"] is None
        expected = starts.get(gone, now - timedelta(hours=1))
        assert tombstone["unit_start"].replace(tzinfo=UTC) == expected
    # Without a committed row there is nothing to retire.
    assert [
        row["unit_id"] for row in read(unit_ch, definition, scope, [retired], now)
    ] == [str(survivor)]
    # Once the tombstones are committed, they are not emitted again.
    second = uuid4()
    stage(
        unit_ch,
        definition,
        scope,
        [survivor, retired, new],
        now,
        second,
        2,
        manifest=manifest,
    )
    manifest = {"delta_attempt_ids": [attempt, second]}
    latest = read(
        unit_ch, definition, scope, [survivor, retired, new], now, manifest=manifest
    )
    assert [row["unit_id"] for row in latest] == [str(survivor)]
    sql, params = committed_unit_values_select(
        scope, definition.metric_id, 1, [survivor, retired, new], manifest
    )
    stored = {
        row["unit_id"]: row["is_deleted"] for row in rows_as_dicts(unit_ch, sql, params)
    }
    assert stored == {str(survivor): 0, str(retired): 1, str(new): 1}


def test_late_earlier_span_has_no_cached_bound_and_all_deleted_is_tombstone(unit_ch):
    scope, now = scope_now()
    session, trace = uuid4(), uuid4()
    earlier = now.replace(day=1, hour=0, minute=0, second=0) - timedelta(minutes=1)
    seed_typed(
        unit_ch,
        scope,
        session,
        now - timedelta(hours=2),
        number={"value": 3.0},
        trace=trace,
        span_id="recent",
    )
    seed_typed(
        unit_ch,
        scope,
        session,
        earlier,
        updated=now - timedelta(hours=1),
        number={"value": 7.0},
        trace=trace,
        span_id="late",
    )
    definition = field("total", [descriptor("number", "sum")])
    row = read(unit_ch, definition, scope, [session], now)[0]
    assert row["inputs"] == {"value": 10.0}
    assert row["unit_start"].replace(tzinfo=UTC) == earlier
    assert row["bucket_start"].replace(tzinfo=UTC) == earlier.replace(minute=45)
    for span_id, at in [("recent", now - timedelta(hours=2)), ("late", earlier)]:
        seed_typed(
            unit_ch,
            scope,
            session,
            at,
            updated=now - timedelta(minutes=45),
            trace=trace,
            span_id=span_id,
            version=2,
            deleted=1,
        )
    tombstone = read(unit_ch, definition, scope, [session], now)[0]
    assert tombstone["is_deleted"] == 1 and tombstone["value_number"] is None
    assert tombstone["status"] == "no_value" and tombstone["inputs"] == {}
    assert tombstone["settled_at"] is None and tombstone["part_n"] is None


def test_moved_source_does_not_resurrect_old_membership_and_dependency_is_consulted(
    unit_ch,
):
    scope, now = scope_now()
    old, new, trace, attempt = uuid4(), uuid4(), uuid4(), uuid4()
    # Only the newer physical version remains, but the committed preimage names
    # the old conversation. This still nominates the physical identity safely.
    seed_typed(
        unit_ch,
        scope,
        new,
        now - timedelta(hours=2),
        number={"value": 8.0},
        trace=trace,
    )
    unit_ch.execute(
        """INSERT INTO metric_source_dependencies
        (org_id,project_id,trace_id,span_id,raw_session_id,canonical_session_id,generation,attempt_id,_version)
        VALUES (%(org)s,%(project)s,%(trace)s,'span',%(old)s,%(old)s,1,%(attempt)s,1)""",
        {
            "org": str(scope.organization),
            "project": str(scope.project),
            "trace": str(trace),
            "old": str(old),
            "attempt": str(attempt),
        },
    )
    definition = field("average", [descriptor()])
    definition.metric_id, definition.version = uuid4(), 1
    manifest = {"delta_attempt_ids": [attempt]}
    sql, params = unit_values_select(
        definition,
        scope,
        [old],
        start=now - timedelta(days=1),
        end=now,
        as_of=now,
        manifest=manifest,
    )
    assert "metric_source_dependencies" in sql and params[
        "unit_read_dependency_attempts"
    ] == (str(attempt),)
    assert rows_as_dicts(unit_ch, sql, params)[0]["is_deleted"] == 1
    # Physical old versions are merely nominees too: collapse precedes membership.
    seed_typed(
        unit_ch,
        scope,
        old,
        now - timedelta(hours=2),
        number={"value": 2.0},
        trace=trace,
        version=0,
    )
    assert read(unit_ch, definition, scope, [old], now)[0]["is_deleted"] == 1
    assert read(unit_ch, definition, scope, [new], now)[0]["value_number"] == 8


def test_typed_locale_boolean_order_sum_count_and_complete_evidence(unit_ch):
    scope, now = scope_now()
    session, trace = uuid4(), UUID("00000000-0000-0000-0000-000000000001")
    at = now - timedelta(hours=3)
    seed_typed(
        unit_ch,
        scope,
        session,
        at,
        trace=trace,
        span_id="a",
        text={"value": "fr"},
        number={"value": 2.0},
        boolean={"value": 0},
        updated=now - timedelta(hours=1),
    )
    seed_typed(
        unit_ch,
        scope,
        session,
        at,
        trace=trace,
        span_id="z",
        text={"value": "en"},
        number={"value": 6.0},
        boolean={"value": 1},
        updated=now - timedelta(hours=2),
    )
    # A later span without the attribute is not an observation of it. It was
    # also updated last, so the legacy updated_at order selects it.
    seed_typed(
        unit_ch,
        scope,
        session,
        at + timedelta(minutes=5),
        trace=trace,
        span_id="zz",
        updated=now - timedelta(minutes=40),
    )
    # Equal start times and trace: (start, trace, span id) breaks the tie.
    for reducer, expected in [
        ("first", 2.0),
        ("latest", 6.0),
        ("sum", 8.0),
        ("count", 2.0),
    ]:
        row = read(
            unit_ch,
            field("average", [descriptor("number", reducer)]),
            scope,
            [session],
            now,
        )[0]
        assert row["value_number"] == expected and row["inputs"] == {"value": expected}
    for reducer, expected in [("any", 1), ("all", 0)]:
        row = read(
            unit_ch,
            field("share", [descriptor("boolean", reducer)]),
            scope,
            [session],
            now,
        )[0]
        assert row["value_bool"] == expected and row["inputs_bool"] == {
            "value": expected
        }
        assert row["inputs"] == {"field_condition": float(expected)}
    for reducer, expected in [("first", 1), ("latest", 0)]:
        row = read(
            unit_ch,
            field(
                "share",
                [descriptor("text", reducer)],
                conditions=[{"alias": "value", "value": "fr"}],
            ),
            scope,
            [session],
            now,
        )[0]
        assert row["value_bool"] == expected and row["status"] == "ok"
        assert row["inputs_text"] == {"value": "fr" if reducer == "first" else "en"}
        assert row["inputs"] == {"field_condition": float(expected)}
    # Legacy custom latest remains updated_at-based, not sentence start/id order,
    # and still selects the latest span even when it lacks the attribute.
    custom = SimpleNamespace(
        inputs=[
            {
                "alias": "value",
                "source": "span",
                "field": "attribute_number",
                "attribute_key": "value",
                "reducer": "latest",
            }
        ],
        formula="mean(value)",
        selection_unit="session",
        weighting="none",
        cohort_key="",
    )
    legacy = read(unit_ch, custom, scope, [session], now)[0]
    assert legacy["inputs"] == {"value": None} and legacy["status"] == "no_value"
    seed_typed(
        unit_ch,
        scope,
        session,
        at + timedelta(minutes=5),
        trace=trace,
        span_id="zz",
        updated=now - timedelta(hours=3),
        version=2,
    )
    assert read(unit_ch, custom, scope, [session], now)[0]["value_number"] == 2.0


def test_boolean_any_all_three_valued_with_unknown_evaluations(unit_ch):
    scope, now = scope_now()
    config = uuid4()
    outcomes = {
        "decisive-true": (1, None),
        "decisive-false": (0, None),
        "known": (1, 1),
    }
    sessions = {}
    for name, values in outcomes.items():
        session, trace = uuid4(), uuid4()
        sessions[name] = session
        for index, value in enumerate(values):
            span = f"{name}-{index}"
            seed_typed(
                unit_ch,
                scope,
                session,
                now - timedelta(hours=2),
                trace=trace,
                span_id=span,
            )
            seed_eval(
                unit_ch,
                config,
                session,
                now - timedelta(hours=1),
                0,
                1.0,
                span_id=span,
                trace_id=trace,
            )
            set_eval_output(unit_ch, config, output_bool=value, span=span)
        # An unevaluated span is not an observation (no eval row exists for it).
        seed_typed(
            unit_ch,
            scope,
            session,
            now - timedelta(hours=2),
            trace=trace,
            span_id=f"{name}-x",
        )
    expected = {
        "any": {"decisive-true": 1, "decisive-false": None, "known": 1},
        "all": {"decisive-true": None, "decisive-false": 0, "known": 1},
    }
    for reducer, answers in expected.items():
        item = descriptor(
            "boolean",
            reducer,
            source="eval",
            config_id=str(config),
            config_hash="a" * 64,
        )
        rows = {
            row["unit_id"]: row
            for row in read(
                unit_ch, field("share", [item]), scope, list(sessions.values()), now
            )
        }
        for name, answer in answers.items():
            row = rows[str(sessions[name])]
            assert row["inputs_bool"] == {"value": answer}, (reducer, name)
            assert row["value_bool"] == answer
            if answer is None:
                # Unknown is No value, never "Doesn't count".
                assert (
                    row["status"] == "no_value"
                    and row["no_value_reason"] == "not_evaluated"
                )
                assert row["inputs"] == {"field_condition": None}


def test_absent_unevaluated_zero_defaults_open_and_trace_probability(unit_ch):
    scope, now = scope_now()
    session, config = uuid4(), uuid4()
    seed_typed(unit_ch, scope, session, now - timedelta(hours=2))
    row = read(unit_ch, field("average", [descriptor()]), scope, [session], now)[0]
    assert row["status"] == "no_value" and row["no_value_reason"] == "attribute_absent"
    for measure in [
        "latency_ms",
        "cost",
        "total_tokens",
        "prompt_tokens",
        "completion_tokens",
    ]:
        item = descriptor(field=measure)
        row = read(unit_ch, field("average", [item]), scope, [session], now)[0]
        assert row["inputs"]["value"] is None and row["value_number"] is None
        assert row["no_value_reason"] == "attribute_absent"
    eval_input = descriptor(source="eval", config_id=str(config), config_hash="a" * 64)
    definition = field("average", [eval_input])
    row = read(unit_ch, definition, scope, [session], now)[0]
    assert row["no_value_reason"] == "not_evaluated"
    seed_eval(
        unit_ch,
        config,
        session,
        now - timedelta(hours=1),
        3,
        0.25,
        selection_overrides={"sampling_unit": "trace", "sampling_unit_id": "trace:any"},
    )
    definition.weighting = "inverse_probability"
    row = read(unit_ch, definition, scope, [session], now)[0]
    # A trace's probability is never reused for its conversation.
    assert (
        row["no_value_reason"] == "invalid_probability" and row["value_number"] is None
    )
    assert row["invalid_probability"] == 1
    seed_typed(
        unit_ch,
        scope,
        session,
        now - timedelta(minutes=1),
        span_id="fresh",
        number={"value": 0.0},
    )
    row = read(unit_ch, field("average", [descriptor()]), scope, [session], now)[0]
    assert row["status"] == "open" and row["settled_at"] is None
    assert row["inputs"] == {"value": 0.0} and row["value_number"] is None
    # A measured zero is a real zero once settled.
    row = read(
        unit_ch,
        field("average", [descriptor()]),
        scope,
        [session],
        now,
        settle_minutes=0,
    )[0]
    assert row["status"] == "ok" and row["value_number"] == 0.0


def test_committed_overlay_excludes_uncommitted_revisions_before_collapse(unit_ch):
    scope, now = scope_now()
    session, field_id, checkpoint, delta, aborted = [uuid4() for _ in range(5)]
    params = {
        **scope.parameters(),
        "unit": str(session),
        "field": str(field_id),
        "at": now - timedelta(hours=2),
    }
    # The aborted attempt has the highest generation and _version: only the
    # allowlist, never a numeric head, keeps it out.
    for table, attempt, generation, version, number, deleted in [
        ("metric_field_values_checkpoint", checkpoint, 1, 50, 1.0, 0),
        ("metric_field_values", delta, 2, 2, 2.0, 1),
        ("metric_field_values", aborted, 999, 999, 999.0, 0),
    ]:
        unit_ch.execute(
            f"""INSERT INTO {table} (org_id,workspace_id,project_id,field_id,field_version,
            level,unit_id,session_id,unit_start,value_number,generation,attempt_id,_version,is_deleted)
            VALUES (%(org_id)s,%(workspace_id)s,%(project_id)s,%(field)s,1,'conversation',%(unit)s,%(unit)s,
                %(at)s,%(number)s,%(generation)s,%(attempt)s,%(version)s,%(deleted)s)""",
            {
                **params,
                "attempt": str(attempt),
                "generation": generation,
                "version": version,
                "number": number,
                "deleted": deleted,
            },
        )
    manifest = {"checkpoint_attempt_id": checkpoint, "delta_attempt_ids": []}
    sql, params = committed_unit_values_select(scope, field_id, 1, [session], manifest)
    assert rows_as_dicts(unit_ch, sql, params)[0]["value_number"] == 1
    # The newer generation wins although its _version is lower than the base's.
    manifest["delta_attempt_ids"] = [delta]
    sql, params = committed_unit_values_select(scope, field_id, 1, [session], manifest)
    assert rows_as_dicts(unit_ch, sql, params)[0]["is_deleted"] == 1
    sql, params = committed_unit_values_select(
        scope, field_id, 1, [session], manifest, include_deleted=False
    )
    assert rows_as_dicts(unit_ch, sql, params) == []
    # A raw-source tombstone uses the old committed anchor, not epoch/now, so
    # the writer can invalidate the original bucket even after spans vanished.
    definition = field("average", [descriptor()])
    definition.metric_id, definition.version = field_id, 1
    deleted = read(unit_ch, definition, scope, [session], now, manifest=manifest)[0]
    assert deleted["is_deleted"] == 1
    assert deleted["unit_start"].replace(tzinfo=UTC) == now - timedelta(hours=2)
    for table in ["metric_field_values", "metric_field_values_checkpoint"]:
        unit_ch.execute(f"OPTIMIZE TABLE {table} FINAL")
    sql, params = committed_unit_values_select(
        scope, field_id, 1, [session], manifest, include_deleted=False
    )
    assert rows_as_dicts(unit_ch, sql, params) == []


def test_typed_eval_and_span_conditions_preserve_all_three_evidence_maps(unit_ch):
    scope, now = scope_now()
    session, config = uuid4(), uuid4()
    seed_typed(unit_ch, scope, session, now - timedelta(hours=3), text={"value": "fr"})
    seed_eval(unit_ch, config, session, now - timedelta(minutes=10), 4.0, 0.5)
    set_eval_output(unit_ch, config, output_bool=1, output_str="accepted")
    inputs = [
        descriptor("text", alias="locale"),
        descriptor(
            "boolean",
            source="eval",
            alias="resolved",
            config_id=str(config),
            config_hash="a" * 64,
        ),
        descriptor(
            "text",
            source="eval",
            alias="tag",
            config_id=str(config),
            config_hash="a" * 64,
        ),
        descriptor(
            "number",
            source="eval",
            alias="score",
            config_id=str(config),
            config_hash="a" * 64,
        ),
    ]
    definition = field(
        "share",
        inputs,
        conditions=[
            {"alias": "locale", "value": "fr"},
            {"alias": "resolved", "value": True},
        ],
    )
    definition.weighting = "inverse_probability"
    row = read(unit_ch, definition, scope, [session], now)[0]
    assert row["value_bool"] == 1 and row["status"] == "ok"
    assert row["inputs"] == {"score": 4.0, "field_condition": 1.0}
    assert row["inputs_bool"] == {"resolved": 1}
    assert row["inputs_text"] == {"locale": "fr", "tag": "accepted"}
    assert row["sample_probability"] == 0.5
    # Eval arrival does not reopen an otherwise settled conversation.
    assert row["last_arrival_ns"] < int((now - timedelta(hours=2)).timestamp() * 1e9)
    missing = uuid4()
    seed_typed(unit_ch, scope, missing, now - timedelta(hours=2), text={"value": "en"})
    incomplete = read(unit_ch, definition, scope, [missing], now)[0]
    assert incomplete["status"] == "no_value"
    assert incomplete["no_value_reason"] == "not_evaluated"
    assert incomplete["inputs"]["field_condition"] is None
    # A span attribute that is absent while the evaluation exists is attribute_absent.
    absent = uuid4()
    seed_typed(unit_ch, scope, absent, now - timedelta(hours=2))
    seed_eval(unit_ch, config, absent, now - timedelta(minutes=10), 4.0, 0.5)
    set_eval_output(unit_ch, config, output_bool=1, output_str="accepted")
    row = read(unit_ch, definition, scope, [absent], now)[0]
    assert row["status"] == "no_value" and row["no_value_reason"] == "attribute_absent"


def test_unit_kind_value_columns_and_two_group_dimensions(unit_ch):
    scope, now = scope_now()
    session = uuid4()
    seed_typed(
        unit_ch,
        scope,
        session,
        now - timedelta(hours=2),
        number={"value": 8.0},
        text={"value": "fr", "region": "west"},
        boolean={"value": 1},
    )
    cases = [
        ("share", "boolean", "value_bool", 1),
        ("count", "boolean", "value_bool", 1),
        ("average", "number", "value_number", 8.0),
        ("total", "number", "value_number", 8.0),
        ("custom", "number", "value_number", 8.0),
        ("percentile", "number", "value_number", 8.0),
        ("unique_count", "text", "value_text", "fr"),
    ]
    for kind, source_type, column, expected in cases:
        definition = field(kind, [descriptor(source_type)])
        definition.group_dims = ["model", "attribute:region"]
        row = read(unit_ch, definition, scope, [session], now)[0]
        assert row[column] == expected and row["status"] == "ok"
        assert all(
            row[other] is None
            for other in {"value_bool", "value_number", "value_text"} - {column}
        )
        assert "west" in row["group_key"]
    # A user metric still persists the canonical conversation, not a raw user unit.
    definition = field("share", [descriptor("boolean")])
    definition.level, definition.user_rule = "user", "any"
    row = read(unit_ch, definition, scope, [session], now)[0]
    assert row["level"] == "conversation" and row["unit_id"] == str(session)


def test_observation_predicate_is_distinct_from_default_reduce_then_condition(unit_ch):
    scope, now = scope_now()
    session = uuid4()
    for i, value in enumerate([0.0, 10.0]):
        seed_typed(
            unit_ch,
            scope,
            session,
            now - timedelta(hours=2),
            span_id=str(i),
            number={"value": value},
        )
    after = field(
        "share",
        [descriptor("number", "mean")],
        conditions=[{"alias": "value", "operator": "gt", "value": 5}],
    )
    before = field(
        "share",
        [
            descriptor(
                "number",
                "any",
                condition_stage="observation",
                condition={"operator": "gt", "value": 5},
            )
        ],
    )
    assert read(unit_ch, after, scope, [session], now)[0]["value_bool"] == 0
    explicit = read(unit_ch, before, scope, [session], now)[0]
    assert explicit["value_bool"] == 1 and explicit["inputs_bool"] == {"value": 1}
