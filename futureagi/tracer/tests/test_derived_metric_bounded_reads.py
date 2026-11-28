"""Immutable membership contract; live replacement parity lives next door."""

from datetime import UTC, datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from clickhouse_driver.util.escape import escape_param

from tracer.models.derived_metric import MetricStateConflict
from tracer.services.derived_metrics.bounded_reads import (
    MAX_KEY_IN_BYTES,
    MAX_KEY_IN_ITEMS,
    committed_key_pages,
    iter_key_membership_batches,
    key_membership_sql,
    merge_bindings,
    normalize_key_in,
)
from tracer.services.derived_metrics.publication import committed_rows_select
from tracer.services.derived_metrics.storage import (
    BUCKET_TOTALS,
    FIELD_VALUES,
    SOURCE_DEPENDENCIES,
    TABLES,
    UNIT_STATE,
)


def manifest(project=None, *, checkpoint=True, delta=True):
    base, change = uuid4(), uuid4()
    pairs = ([(1, base)] if checkpoint else []) + ([(2, change)] if delta else [])
    return SimpleNamespace(
        project_id=project or uuid4(),
        checkpoint_attempt_id=base if checkpoint else None,
        allowed_attempts=lambda: pairs,
    )


def test_checkpoint_hash_pruning_is_correlated_and_checkpoint_only():
    head = manifest()
    field = uuid4()
    sql, params = committed_rows_select(
        FIELD_VALUES,
        head,
        org_id=uuid4(),
        project_id=head.project_id,
        key_in={("field_id", "unit_id"): ((field, "a"), (field, "b"))},
    )
    assert sql.count("cityHash64(unit_id) %% 8") == 1
    assert "(field_id, unit_id) IN" in sql
    checkpoint, delta = sql.split(" UNION ALL ")
    assert "SELECT cityHash64(arrayJoin(%(pub_unit_partitions)s)) %% 8" in checkpoint
    assert "cityHash64" not in delta
    assert params["pub_unit_partitions"] == ["a", "b"]
    assert params["pub_base"] == tuple(head.allowed_attempts()[:1])
    assert params["pub_deltas"] == tuple(head.allowed_attempts()[1:])


def test_cell_hydration_prunes_only_the_checkpoint_branch():
    from tracer.services.derived_metrics.queries import Scope
    from tracer.services.derived_metrics.unit_reads import committed_unit_values_select

    scope = Scope(uuid4(), uuid4(), uuid4())
    base, delta, field = uuid4(), uuid4(), uuid4()
    ids = (str(uuid4()), str(uuid4()))
    sql, params = committed_unit_values_select(
        scope,
        field,
        1,
        ids,
        {"checkpoint_attempt_id": base, "delta_attempt_ids": (delta,)},
    )
    *checkpoint_branches, delta_sql = sql.split(" UNION ALL ")
    assert len(checkpoint_branches) == 8
    for bucket, branch in enumerate(checkpoint_branches):
        assert f"cityHash64(unit_id) %% 8 = {bucket}" in branch
        assert f"WHERE cityHash64(unit) %% 8 = {bucket}" in branch
        assert "unit_id IN (SELECT unit" in branch
        assert "attempt_id = %(field_read_checkpoint)s" in branch
    assert "cityHash64" not in delta_sql
    assert params["field_read_unit_partitions"] == list(ids)


def key_value(kind):
    if kind == "UUID":
        return str(uuid4())
    if kind.startswith("UInt"):
        return 2
    if kind.startswith("DateTime"):
        return datetime(2026, 1, 1, tzinfo=UTC)
    return "not-a-uuid\\'\n雪"


@pytest.mark.parametrize("table", TABLES.values(), ids=lambda t: t.name)
def test_only_logical_keys_are_accepted_for_every_physical_table(table):
    for name, kind in table.schema:
        if name in table.logical_key:
            value = key_value(kind)
            result = normalize_key_in(table, {name: [value]})
            assert len(result[name]) == 1
            if kind == "UUID":
                assert result[name][0] == UUID(value)
        else:
            with pytest.raises(ValueError, match="immutable"):
                normalize_key_in(table, {name: [key_value(kind)]})
    for bad in ("does_not_exist", "unit_id) OR 1 = 1 --"):
        with pytest.raises(ValueError, match="immutable"):
            normalize_key_in(table, {bad: ["private"]})


@pytest.mark.parametrize("bad", [None, -1, 2**32, 1.2, True, "1"])
def test_unsigned_keys_do_not_accept_lossy_coercions(bad):
    with pytest.raises(ValueError, match="logical-key value"):
        normalize_key_in(FIELD_VALUES, {"field_version": [bad]})


@pytest.mark.parametrize("bad", [None, 3, b"private"])
def test_string_keys_are_strings_not_inferred_uuids(bad):
    with pytest.raises(ValueError, match="logical-key value"):
        normalize_key_in(FIELD_VALUES, {"unit_id": [bad]})


def test_types_are_canonical_and_duplicates_are_deterministic():
    value = uuid4()
    result = normalize_key_in(
        FIELD_VALUES,
        {"field_id": [value, str(value), value.hex], "unit_id": ["b", "a", "b"]},
    )
    assert result == {"field_id": (value,), "unit_id": ("b", "a")}
    at = datetime(2026, 1, 1, 2, tzinfo=timezone(timedelta(hours=2)))
    assert normalize_key_in(BUCKET_TOTALS, {"bucket_start": [at]}) == {
        "bucket_start": (at.astimezone(UTC),)
    }
    with pytest.raises(ValueError, match="immutable"):
        normalize_key_in(FIELD_VALUES, {"bucket_start": [at]})


@pytest.mark.parametrize(
    "bad",
    [
        datetime(2026, 1, 1),
        datetime(2026, 1, 1, microsecond=1, tzinfo=UTC),
        "2026-01-01",
        datetime(1969, 1, 1, tzinfo=UTC),
    ],
)
def test_datetime_key_requires_exact_timezone_aware_seconds(bad):
    with pytest.raises(ValueError, match="logical-key value"):
        normalize_key_in(BUCKET_TOTALS, {"bucket_start": [bad]})


@pytest.mark.parametrize(
    "membership",
    [
        {"unit_id": "text"},
        {"unit_id": b"text"},
        {"unit_id": iter(["a"])},
        {("unit_id", "unit_id"): [("a", "a")]},
        {("trace_id", "span_id"): [("a",)]},
        {("trace_id", "span_id"): [["a", "b"]]},
        {("trace_id", "span_id"): ["ab"]},
        {("trace_id", "span_id"): [("a", "b")], "span_id": ["b"]},
    ],
)
def test_malformed_memberships_fail_without_echoing_values(membership):
    with pytest.raises(ValueError) as error:
        normalize_key_in(SOURCE_DEPENDENCIES, membership)
    assert "text" not in str(error.value)


@pytest.mark.parametrize(
    "bad", [[], "", "pub;DROP", "0pub", "pub\n", "é", "p" * 49, None]
)
def test_parameter_namespaces_are_validated(bad):
    with pytest.raises(ValueError, match="namespace"):
        committed_rows_select(
            FIELD_VALUES, None, org_id=uuid4(), project_id=uuid4(), param_prefix=bad
        )


@pytest.mark.parametrize("size", [999, 1000, 1001])
def test_item_budget_counts_all_fixed_and_varying_clauses(size):
    membership = {
        "field_id": [uuid4()],
        "field_version": [1],
        "unit_id": [str(i) for i in range(size - 2)],
    }
    if size > MAX_KEY_IN_ITEMS:
        with pytest.raises(ValueError, match="item cap"):
            normalize_key_in(FIELD_VALUES, membership)
    else:
        assert (
            sum(map(len, normalize_key_in(FIELD_VALUES, membership).values())) == size
        )
    with pytest.raises(ValueError, match="item cap"):
        normalize_key_in(FIELD_VALUES, {"unit_id": ["same"] * 1001})


@pytest.mark.parametrize("size", [65535, 65536, 65537])
def test_byte_budget_includes_driver_escaping_and_sequence_syntax(size):
    # Parentheses + quotes are four bytes for one ordinary string member.
    values = ("a" * (size - 4),)
    context = SimpleNamespace(server_info=SimpleNamespace(get_timezone=lambda: "UTC"))
    assert len(escape_param(values, context).encode("utf-8")) == size
    if size > MAX_KEY_IN_BYTES:
        with pytest.raises(ValueError, match="byte cap"):
            normalize_key_in(FIELD_VALUES, {"unit_id": values})
    else:
        assert normalize_key_in(FIELD_VALUES, {"unit_id": values})["unit_id"] == values


def test_multibyte_and_escaped_values_use_rendered_bytes_not_character_count():
    for value in ("雪" * 22000, "'" * 33000, "\\" * 33000, "\x00" * 33000):
        assert len(value) < MAX_KEY_IN_BYTES
        with pytest.raises(ValueError, match="byte cap"):
            normalize_key_in(FIELD_VALUES, {"unit_id": [value]})
    with pytest.raises(ValueError, match="byte cap"):
        normalize_key_in(
            FIELD_VALUES,
            {"unit_id": ["a" * (MAX_KEY_IN_BYTES - 4)], "level": ["conversation"]},
        )


@pytest.mark.parametrize(
    "checkpoint,delta,overlay",
    [
        (True, False, False),
        (False, True, False),
        (False, False, True),
        (True, True, True),
        (False, False, False),
    ],
)
def test_every_branch_gets_membership_keyset_and_exact_attempts(
    checkpoint, delta, overlay
):
    head = manifest(checkpoint=checkpoint, delta=delta)
    proposed = (
        SimpleNamespace(id=uuid4(), generation=3, purpose="compaction")
        if overlay
        else None
    )
    sql, params = committed_rows_select(
        FIELD_VALUES,
        head,
        org_id=uuid4(),
        project_id=head.project_id,
        overlay=proposed,
        key_after={"unit_id": "a"},
        key_in={"unit_id": ["b", "c"]},
        param_prefix="fields",
    )
    count = int(checkpoint) + int(delta) + int(overlay)
    assert sql.count("unit_id IN %(fields_kin_0)s") == count
    assert sql.count("unit_id > %(fields_after_0)s") == count
    assert "FINAL" not in sql and "generation <=" not in sql
    assert all(name.startswith("fields_") for name in params)
    assert sql.count("metric_field_values_checkpoint PREWHERE") == int(
        checkpoint
    ) + int(overlay)
    if delta:
        assert params["fields_deltas"] == (head.allowed_attempts()[-1],)
    if checkpoint:
        assert params["fields_base"] == (head.allowed_attempts()[0],)
    empty, _ = committed_rows_select(
        FIELD_VALUES,
        head,
        org_id=uuid4(),
        project_id=head.project_id,
        overlay=proposed,
        key_in={"unit_id": []},
    )
    assert empty.count("AND (0)") == count


def test_empty_membership_does_not_bypass_validation():
    head = manifest()
    with pytest.raises(MetricStateConflict, match="project mismatch"):
        committed_rows_select(
            FIELD_VALUES,
            head,
            org_id=uuid4(),
            project_id=uuid4(),
            key_in={"unit_id": []},
        )
    with pytest.raises(ValueError, match="immutable"):
        committed_rows_select(
            FIELD_VALUES,
            head,
            org_id=uuid4(),
            project_id=head.project_id,
            key_in={"unit_id": [], "status": ["ok"]},
        )
    with pytest.raises(ValueError, match="logical-key value"):
        committed_rows_select(
            FIELD_VALUES,
            head,
            org_id=uuid4(),
            project_id=head.project_id,
            key_in={"unit_id": [], "field_id": ["private-invalid-uuid"]},
        )


def test_none_and_empty_mapping_keep_legacy_sql_and_bindings():
    head, org = manifest(), uuid4()
    args = {"org_id": org, "project_id": head.project_id}
    assert committed_rows_select(FIELD_VALUES, head, **args) == committed_rows_select(
        FIELD_VALUES, head, key_in={}, **args
    )
    _, params = committed_rows_select(FIELD_VALUES, head, **args)
    assert set(params) == {"pub_org", "pub_project", "pub_base", "pub_deltas"}


def test_correlated_pairs_never_turn_into_cartesian_membership():
    sql, params = key_membership_sql(
        SOURCE_DEPENDENCIES, {("trace_id", "span_id"): [("t1", "s1"), ("t2", "s2")]}
    )
    assert sql == "(trace_id, span_id) IN %(pub_kin_0)s"
    assert params["pub_kin_0"] == (("t1", "s1"), ("t2", "s2"))


def test_composite_relations_have_independent_bindings_and_conflicts_fail_closed():
    head, org = manifest(), uuid4()
    bindings = []
    for table, prefix, key in [
        (FIELD_VALUES, "old_fields", "unit_id"),
        (FIELD_VALUES, "admission_anchors", "unit_id"),
        (UNIT_STATE, "prior_states", "unit_id"),
        (BUCKET_TOTALS, "bucket_previous", "group_key"),
    ]:
        _, params = committed_rows_select(
            table,
            head,
            org_id=org,
            project_id=head.project_id,
            key_in={key: [prefix]},
            param_prefix=prefix,
        )
        bindings.append(params)
    merged = merge_bindings(*bindings)
    assert len(merged) == sum(map(len, bindings))
    assert merge_bindings({"x": (1,)}, {"x": (1,)}) == {"x": (1,)}
    with pytest.raises(ValueError, match="conflicting"):
        merge_bindings({"x": (1,)}, {"x": (2,)})


def test_membership_batches_reserve_fixed_count_and_bytes():
    fixed = {"field_id": [uuid4()], "field_version": [1]}
    values = [f"unit-{i}" for i in range(2400)]
    pages = list(
        iter_key_membership_batches(
            FIELD_VALUES, fixed=fixed, columns="unit_id", values=values
        )
    )
    assert [len(page["unit_id"]) for page in pages] == [998, 998, 404]
    assert [value for page in pages for value in page["unit_id"]] == values
    for page in pages:
        assert normalize_key_in(FIELD_VALUES, page) == page
    pages = list(
        iter_key_membership_batches(
            FIELD_VALUES,
            fixed=fixed,
            columns="unit_id",
            values=values[:10],
            max_bytes=70,
        )
    )
    assert len(pages) > 1
    for page in pages:
        normalize_key_in(FIELD_VALUES, page, max_bytes=70)


def test_batching_empty_duplicates_overlaps_and_oversized_member():
    args = {"table": FIELD_VALUES, "fixed": {}, "columns": "unit_id"}
    assert list(iter_key_membership_batches(**args, values=[])) == []
    with pytest.raises(ValueError, match="distinct keys"):
        list(iter_key_membership_batches(**args, values=["a", "b", "a"], max_items=1))
    with pytest.raises(ValueError, match="byte cap"):
        list(iter_key_membership_batches(**args, values=["x" * MAX_KEY_IN_BYTES]))
    with pytest.raises(ValueError, match="overlapping"):
        list(
            iter_key_membership_batches(
                FIELD_VALUES, fixed={"unit_id": ["a"]}, columns="unit_id", values=["b"]
            )
        )
    with pytest.raises(ValueError, match="overlapping"):
        list(
            iter_key_membership_batches(
                SOURCE_DEPENDENCIES,
                fixed={"trace_id": ["t"]},
                columns=("trace_id", "span_id"),
                values=[("t", "s")],
            )
        )


@pytest.mark.parametrize("rows", [0, -1, 501, True])
def test_nomination_cannot_increase_publication_row_limit(rows):
    with pytest.raises(ValueError, match="page size"):
        list(
            committed_key_pages(
                None,
                FIELD_VALUES,
                None,
                org_id=uuid4(),
                project_id=uuid4(),
                target_rows=rows,
            )
        )


@pytest.mark.parametrize("uncovered", [0, 1])
def test_retirement_coverage_pushes_only_immutable_definition_keys(uncovered):
    from tracer.services.derived_metrics.refresh import MetricAdmissionUnproven
    from tracer.services.derived_metrics.span_evidence import (
        require_retirement_evidence,
    )

    head = manifest()
    definition = SimpleNamespace(metric_id=uuid4(), version=2, level="span")
    calls = []

    # The coverage proof is one query returning the count of uncovered buckets.
    def execute(sql, params):
        calls.append((sql, params))
        return [(uncovered,)]

    writer = SimpleNamespace(
        org_id=uuid4(),
        workspace_id=uuid4(),
        lease=SimpleNamespace(project_id=head.project_id),
        store=SimpleNamespace(execute=execute),
    )
    if uncovered:
        # Fail closed: any uncovered bucket blocks retirement.
        with pytest.raises(MetricAdmissionUnproven):
            require_retirement_evidence(writer, head, [definition])
    else:
        require_retirement_evidence(writer, head, [definition])
    assert len(calls) == 1
    sql, params = calls[0]
    for table, prefix in (
        ("metric_field_values", "field"),
        ("metric_bucket_totals", "metric"),
        ("metric_bucket_retired_parts", "metric"),
    ):
        namespace = "retirement_proof_" + table
        assert f"{prefix}_id IN %({namespace}_kin_0)s" in sql
        assert f"{prefix}_version IN %({namespace}_kin_1)s" in sql
        assert params[f"{namespace}_kin_0"] == (definition.metric_id,)
        assert params[f"{namespace}_kin_1"] == (definition.version,)
    # Coverage is proved from the pinned fields/buckets, not mutable telemetry.
    assert "FROM spans" not in sql


def test_takeover_byte_partition_does_not_partition_live_source_capture(monkeypatch):
    from tracer.models.derived_metric import (
        MetricAttemptLedgerChunk,
        MetricProjectRefresh,
    )
    from tracer.services.derived_metrics import refresh

    at = datetime(2026, 1, 2, tzinfo=UTC)
    head = manifest()
    head.published_by = SimpleNamespace(source_ceilings={})
    ids = [f"{i:04}-" + "x" * 200 for i in range(500)]
    chunks = [SimpleNamespace(payload=[{"unit_id": unit} for unit in ids])]
    monkeypatch.setattr(
        MetricProjectRefresh,
        "for_project",
        lambda *_: SimpleNamespace(head_manifest=head),
    )
    monkeypatch.setattr(
        MetricAttemptLedgerChunk.objects,
        "filter",
        lambda **_: SimpleNamespace(exclude=lambda **_: chunks),
    )
    captures, lookups = [], []

    def source(_definition, _scope, page, **_kwargs):
        captures.append(page)
        return "SELECT takeover_source", {}

    def execute(sql, params):
        if "takeover_fields_kin_0" in params:
            page = params["takeover_fields_kin_0"]
            lookups.append(page)
            return [(unit, at) for unit in page]
        assert "takeover_source" in sql
        return [(0,)]

    monkeypatch.setattr(refresh, "unit_values_select", source)
    # The capture's start floor is its own statement, not the source modeled here.
    monkeypatch.setattr(
        refresh.RefreshWriter, "_unit_source_floor", lambda *_args: None
    )
    writer = refresh.RefreshWriter(
        SimpleNamespace(project_id=head.project_id),
        SimpleNamespace(execute=execute),
        org_id=uuid4(),
    )
    assert writer.retained_takeover(
        [SimpleNamespace(level="conversation")],
        None,
        cutoff=at - timedelta(days=1),
        start=at,
        end=at + timedelta(days=1),
        as_of=at,
        settle_minutes=30,
    )
    assert len(lookups) > 1
    assert [unit for page in lookups for unit in page] == ids
    assert captures == [tuple(ids)]


def test_span_dirty_byte_partition_keeps_original_raw_source_pages():
    import json

    from tracer.services.derived_metrics.refresh import RefreshWriter

    at = datetime(2026, 1, 1, tzinfo=UTC)
    head = manifest()
    lookups, field_lookups, captures, ledger = [], [], [], []
    source_ids = [(f"trace-{i:04}" + "x" * 200, "span" + "y" * 200) for i in range(500)]

    def execute(sql, params):
        if "span_dirty_fields_kin_1" in params:
            assert params["span_dirty_fields_kin_0"] == ("span",)
            page = params["span_dirty_fields_kin_1"]
            normalize_key_in(FIELD_VALUES, {"level": ("span",), "unit_id": page})
            field_lookups.append(page)
            return [(at - timedelta(days=1),)]
        if "span_dirty_dependencies_kin_0" in params:
            lookups.append(params["span_dirty_dependencies_kin_0"])
            return [(at,)]
        assert "FROM spans PREWHERE" in sql
        captures.append(params["span_sources"])
        return [(at + timedelta(days=1),)]

    writer = RefreshWriter(
        SimpleNamespace(project_id=head.project_id),
        SimpleNamespace(execute=execute),
        org_id=uuid4(),
    )
    writer.append = lambda _attempt, part, entries: ledger.append((part, entries))
    change = SimpleNamespace(units=(), prior_dependencies=(), dependencies=())
    writer._span_dirty_keys(None, change, head, source_ids)
    assert len(lookups) > 1
    assert [key for page in lookups for key in page] == source_ids
    assert captures == [tuple(source_ids)]
    assert len(field_lookups) > 1
    assert [
        tuple(json.loads(unit)) for page in field_lookups for unit in page
    ] == source_ids
    assert [entry["bucket_start"] for entry in ledger[-1][1]] == [
        at - timedelta(days=1),
        at,
        at + timedelta(days=1),
    ]


def test_current_fact_long_keys_split_source_and_prior_together(monkeypatch):
    from tracer.services.derived_metrics import base_list_current_clickhouse as current
    from tracer.services.derived_metrics import refresh
    from tracer.services.derived_metrics.storage import LIST_USER_ACTIVITY

    at = datetime(2026, 1, 2, tzinfo=UTC)
    head = manifest()
    keys = [(f"trace-{i:04}-" + "x" * 160, "span-" + "y" * 160) for i in range(500)]
    captures, frozen, ledger = [], [], []
    monkeypatch.setattr(current, "source_columns_ready", lambda _: True)
    monkeypatch.setattr(
        refresh, "payload_select", lambda *_args, **_kwargs: ("SELECT 0 WHERE 0", {})
    )

    def execute(sql, params):
        assert "current_keys" in params
        captures.append(params["current_keys"])
        return [(0,)]

    writer = SimpleNamespace(
        org_id=uuid4(),
        lease=SimpleNamespace(project_id=head.project_id),
        store=SimpleNamespace(execute=execute),
        append=lambda _attempt, kind, rows: ledger.append((kind, rows)),
    )

    def freeze(_attempt, table, sql, params, **_kwargs):
        if table == LIST_USER_ACTIVITY:
            # Activity inherits the same bounded frozen member page. It must
            # not re-read source spans or use the other tables' namespace.
            page = params["user_activity_previous_kin_0"]
            assert page in captures
            assert "user_activity_previous_kin_0" in sql
            assert "FROM spans" not in sql
        else:
            assert "current_previous_kin_0" in params
            assert params["current_previous_kin_0"] == params["current_keys"]
            assert "current_keys" in sql
            page = params["current_keys"]
        normalize_key_in(table, {("trace_id", "span_id"): page})
        frozen.append((table.name, page))
        return []

    writer.freeze = freeze
    assert current.freeze_changes(writer, uuid4(), head, keys=keys, as_of=at)
    assert len(captures) > 1
    assert [key for page in captures for key in page] == keys
    assert [
        key
        for table, page in frozen
        if table == current.LIST_CURRENT_MEMBERS.name
        for key in page
    ] == keys
    assert [
        key
        for table, page in frozen
        if table == current.LIST_CURRENT_ROOTS.name
        for key in page
    ] == keys
    assert [
        page for table, page in frozen if table == LIST_USER_ACTIVITY.name
    ] == captures
    assert [
        key
        for table, page in frozen
        if table == LIST_USER_ACTIVITY.name
        for key in page
    ] == keys
    assert len(ledger) == 1 and len(ledger[0][1]) == 500


def test_checkpoint_freeze_advances_past_empty_nominated_page(monkeypatch):
    from tracer.services.derived_metrics import refresh

    head = manifest()
    org = uuid4()
    field = uuid4()
    batches = [
        ((field, 1, "conversation", "obsolete"),),
        ((field, 1, "conversation", "surviving"),),
    ]
    visited = []
    frozen = []

    def nominate(*args, **kwargs):
        assert kwargs["target_rows"] == refresh.MAX_BATCH_ROWS
        for batch in batches:
            visited.append(batch)
            yield batch

    monkeypatch.setattr(refresh, "committed_key_pages", nominate)
    writer = SimpleNamespace(
        org_id=org,
        workspace_id=None,
        lease=SimpleNamespace(project_id=head.project_id),
        store=object(),
        freeze=lambda *args, **kwargs: frozen.append((args, kwargs)),
    )
    assert (
        refresh.RefreshWriter.freeze_current(
            writer,
            object(),
            FIELD_VALUES,
            head,
            where="field_version NOT IN %(obsolete_versions)s",
            params={"obsolete_versions": (1,)},
        )
        == []
    )
    assert visited == batches
    assert len(frozen) == 2
    assert all("field_version NOT IN" in call[0][2] for call in frozen)
    assert frozen[0][0][3]["pub_kin_0"] == batches[0]
    assert frozen[1][0][3]["pub_kin_0"] == batches[1]
    assert all(call[1]["collect_payloads"] is False for call in frozen)


@pytest.mark.parametrize(
    "page_size, expected", [(1, ["a"]), (2, ["a", "b"]), (3, ["a", "b", "z"])]
)
def test_grouped_backfill_scans_all_storage_pages_before_cursor(
    monkeypatch, page_size, expected
):
    from tracer.services.derived_metrics import backfill

    at = datetime(2026, 1, 1, tzinfo=UTC)
    pages = [
        [{"unit_id": "z", "keyset_start": at + timedelta(hours=5)}],
        [{"unit_id": "b", "keyset_start": at + timedelta(hours=4)}],
        [{"unit_id": "a", "keyset_start": at + timedelta(hours=3)}],
        [{"unit_id": "a", "keyset_start": at + timedelta(hours=1)}],
        [],
    ]
    visited = []

    def nominate(*args, **kwargs):
        field = uuid4()
        for index in range(len(pages)):
            yield ((field, 1, "conversation", f"physical-{index}"),)

    def read(*args, **kwargs):
        index = len(visited)
        visited.append(index)
        return pages[index]

    monkeypatch.setattr(backfill, "committed_key_pages", nominate)
    monkeypatch.setattr(backfill.changes, "read", read)
    head = manifest()
    writer = SimpleNamespace(
        org_id=uuid4(),
        lease=SimpleNamespace(project_id=head.project_id),
        store=object(),
    )
    result = backfill._grouped_committed_page(
        None,
        writer,
        head,
        FIELD_VALUES,
        ("unit_id",),
        None,
        at,
        at + timedelta(days=1),
        page_size,
        backfill.changes.MetricReadBudget(),
    )
    assert [row["unit_id"] for row in result] == expected
    assert result[0]["keyset_start"] == at + timedelta(hours=1)
    assert visited == list(range(len(pages)))


def test_grouped_backfill_restarts_after_full_scan_and_does_not_emit_partial(
    monkeypatch,
):
    from tracer.services.derived_metrics import backfill

    at = datetime(2026, 1, 1, tzinfo=UTC)
    head = manifest()
    writer = SimpleNamespace(
        org_id=uuid4(),
        lease=SimpleNamespace(project_id=head.project_id),
        store=object(),
    )
    pages = [
        [
            {
                "level": "conversation",
                "unit_id": "z",
                "keyset_start": at + timedelta(hours=4),
            }
        ],
        [
            {
                "level": "conversation",
                "unit_id": "a",
                "keyset_start": at + timedelta(hours=2),
            }
        ],
        [
            {
                "level": "conversation",
                "unit_id": "a",
                "keyset_start": at + timedelta(hours=1),
            }
        ],
    ]
    field = uuid4()

    def nominate(*args, **kwargs):
        for index in range(len(pages)):
            yield ((field, 1, "conversation", f"physical-{index}"),)

    monkeypatch.setattr(backfill, "committed_key_pages", nominate)
    interrupted = [True]
    seen = []

    def read(_analytics, _query, params, _budget, **_kwargs):
        index = len(seen) % len(pages)
        seen.append(params.get("history_group_after_1"))
        if interrupted[0] and index == 1:
            raise backfill.changes.MetricChangeReadError("scan interrupted")
        after = params.get("history_group_after_1")
        return [r for r in pages[index] if after is None or r["unit_id"] > after]

    monkeypatch.setattr(backfill.changes, "read", read)

    def page(after):
        return backfill._grouped_committed_page(
            None,
            writer,
            head,
            FIELD_VALUES,
            ("level", "unit_id"),
            after,
            at,
            at + timedelta(days=1),
            1,
            backfill.changes.MetricReadBudget(),
        )

    with pytest.raises(backfill.changes.MetricChangeReadError, match="interrupted"):
        page(None)
    interrupted[0] = False
    seen.clear()
    first = page(None)
    assert first == [
        {
            "level": "conversation",
            "unit_id": "a",
            "keyset_start": at + timedelta(hours=1),
        }
    ]
    seen.clear()
    second = page((first[-1]["level"], first[-1]["unit_id"]))
    assert [r["unit_id"] for r in second] == ["z"]
    assert seen == ["a", "a", "a"]


class SourceStream:
    def __init__(self, rows, *, fail=False):
        self.rows, self.fail, self.calls, self.disconnected = rows, fail, [], False

    def execute_iter(self, sql, params, **kwargs):
        self.calls.append((sql, params, kwargs))
        yield from self.rows
        if self.fail:
            raise RuntimeError("source transport failed after last row")

    def disconnect(self):
        self.disconnected = True


def test_capture_is_one_exhausted_stream_partitioned_by_emitted_keys():
    from tracer.services.derived_metrics.bounded_reads import capture_source_output

    client = SourceStream(
        [
            ("conversation", "survivor-outside-candidates", "0102"),
            ("conversation", "retired-alias", "0304"),
        ]
    )
    result = capture_source_output(
        SimpleNamespace(client=client),
        "SELECT source",
        {},
        field_id=uuid4(),
        field_version=2,
        candidate_count=1,
    )
    pages = list(result.pages(max_items=1))
    assert [p.unit_ids for p in pages] == [
        ("retired-alias",),
        ("survivor-outside-candidates",),
    ]
    assert len(client.calls) == 1
    sql, _, kw = client.calls[0]
    assert "formatRow('RowBinary'" in sql and "LIMIT" not in sql
    assert kw["chunk_size"] == 1
    assert kw["settings"]["max_block_size"] == 500
    assert kw["query_id"].startswith("metric-source-capture-")
    assert kw["settings"]["result_overflow_mode"] == "throw"
    assert kw["settings"]["result_overflow_mode"] == "throw"
    assert kw["settings"]["max_result_rows"] == 2
    assert "source_rows" in pages[0].select()[1]["captured_source_schema"]
    assert "generation" not in pages[0].select()[1]["captured_source_schema"]


@pytest.mark.parametrize("failure", ["transport", "count", "bytes", "duplicate", "key"])
def test_capture_never_returns_partial_handle(monkeypatch, failure):
    from tracer.services.derived_metrics import bounded_reads as bounded

    rows = [("conversation", "alias", "0001")]
    if failure == "count":
        rows += [("conversation", "b", "00"), ("conversation", "c", "00")]
    elif failure == "bytes":
        monkeypatch.setattr(bounded, "SOURCE_CAPTURE_BYTES", 1)
    elif failure == "duplicate":
        rows += [("conversation", "alias", "0203")]
    elif failure == "key":
        rows = [("conversation", "x" * MAX_KEY_IN_BYTES, "00")]
    client = SourceStream(rows, fail=failure == "transport")
    with pytest.raises((ValueError, RuntimeError)):
        bounded.capture_source_output(
            SimpleNamespace(client=client),
            "SELECT source",
            {},
            field_id=uuid4(),
            field_version=1,
            candidate_count=1,
        )
    assert client.disconnected


@pytest.mark.parametrize("failure", ["transport", "count", "bytes", "conflict"])
def test_member_capture_never_returns_partial_dependency_proof(monkeypatch, failure):
    from tracer.services.derived_metrics import bounded_reads as bounded
    from tracer.services.derived_metrics import changes

    member = ("trace", "span", "a", "a", "", "", datetime(2026, 1, 1, tzinfo=UTC), 0)
    members = [member]
    if failure == "count":
        monkeypatch.setattr(changes, "MAX_KEYS", 1)
        members.append(("trace", "other", *member[2:]))
    elif failure == "bytes":
        monkeypatch.setattr(bounded, "SOURCE_CAPTURE_BYTES", 1)
    elif failure == "conflict":
        members.append(("trace", "span", "b", "b", *member[4:]))
    client = SourceStream(
        [("conversation", "a", "00", members)], fail=failure == "transport"
    )
    with pytest.raises((ValueError, RuntimeError)):
        bounded.capture_source_output(
            SimpleNamespace(client=client),
            "SELECT source",
            {},
            field_id=uuid4(),
            field_version=1,
            candidate_count=1,
            include_dependencies=True,
        )
    assert client.disconnected


def test_source_output_pages_obey_both_rendered_key_and_payload_caps(monkeypatch):
    from tracer.services.derived_metrics import bounded_reads as bounded

    client = SourceStream([("conversation", f"unit-{i}", "00" * 3) for i in range(6)])
    result = bounded.capture_source_output(
        SimpleNamespace(client=client),
        "SELECT source",
        {},
        field_id=uuid4(),
        field_version=1,
        candidate_count=3,
    )
    monkeypatch.setattr(bounded, "SOURCE_PAGE_BYTES", 5)
    assert len(list(result.pages())) == 6
    monkeypatch.setattr(bounded, "SOURCE_PAGE_BYTES", 100)
    assert len(list(result.pages(max_bytes=110))) == 6
    assert sum(len(p.records) for p in result.pages(max_items=2)) == 6


@pytest.mark.parametrize("matching_page", [0, 2, None])
def test_admission_reduces_positive_anchor_matches_before_absence(
    monkeypatch, matching_page
):
    from tracer.services.derived_metrics import refresh

    visited = []
    field = uuid4()
    nominations = [
        ((field, version, "conversation", "canonical"),) for version in (1, 2, 3)
    ]

    def nominate(*args, **kwargs):
        assert kwargs["key_in"] == {
            "level": ("conversation",),
            "unit_id": ("canonical",),
        }
        yield from nominations

    def committed(table, head, **kwargs):
        assert kwargs["param_prefix"] == "admission_anchors"
        index = nominations.index(
            kwargs["key_in"][("field_id", "field_version", "level", "unit_id")]
        )
        visited.append(index)
        return f"SELECT anchor_{index}", {"anchor_page": index}

    class Store:
        def execute(self, sql, params):
            if "anchor_page" not in params:
                return [("canonical",)]
            assert "is_deleted = 0" in sql and "f.unit_start = a.unit_start" in sql
            return [("canonical",)] if params["anchor_page"] == matching_page else []

    monkeypatch.setattr(refresh, "committed_key_pages", nominate)
    monkeypatch.setattr(refresh, "committed_rows_select", committed)
    writer = refresh.RefreshWriter(
        SimpleNamespace(project_id=uuid4()), Store(), org_id=uuid4()
    )
    unproven = writer._unproven_source_admission(
        None,
        SimpleNamespace(unit_ids=("canonical",)),
        "SELECT source",
        {},
        "SELECT old",
        {},
        datetime(2026, 1, 1, tzinfo=UTC),
    )
    assert unproven is (matching_page is None)
    assert visited == list(range(3 if matching_page is None else matching_page + 1))


def test_five_hundred_candidates_can_emit_one_thousand_bounded_keys():
    from tracer.services.derived_metrics.bounded_reads import capture_source_output

    client = SourceStream(
        [("conversation", str(UUID(int=i)), "00") for i in range(1000)]
    )
    captured = capture_source_output(
        SimpleNamespace(client=client),
        "SELECT source",
        {},
        field_id=uuid4(),
        field_version=1,
        candidate_count=500,
    )
    pages = list(captured.pages())
    assert len(pages) == 2
    assert sum(len(page.records) for page in pages) == 1000
    for page in pages:
        membership = normalize_key_in(
            FIELD_VALUES,
            {
                "field_id": (page.field_id,),
                "field_version": (page.field_version,),
                "unit_id": page.unit_ids,
            },
        )
        assert sum(len(values) for values in membership.values()) == 502


def test_identical_chain_observations_share_one_key_page_without_losing_multiplicity():
    from tracer.services.derived_metrics.bounded_reads import capture_source_output

    client = SourceStream([("conversation", "both-canonical-and-retired", "0102")] * 2)
    captured = capture_source_output(
        SimpleNamespace(client=client),
        "SELECT source",
        {},
        field_id=uuid4(),
        field_version=1,
        candidate_count=1,
    )
    pages = list(captured.pages(max_items=1))
    assert len(pages) == 1 and len(pages[0].field_keys) == 1
    assert len(pages[0].records) == 2
    assert pages[0].select()[1]["captured_source_rows"] == "01020102"


def test_relational_membership_validates_keys_and_reaches_every_branch():
    from tracer.services.derived_metrics.bounded_reads import KeyRelation

    head = manifest()
    overlay = SimpleNamespace(id=uuid4(), generation=3, purpose="incremental")
    relation = KeyRelation(
        ("level", "unit_id"),
        "SELECT level, unit_id FROM compiler_candidates WHERE bucket_start = %(candidate_at)s",
        {"candidate_at": datetime(2026, 1, 1, tzinfo=UTC)},
    )
    sql, params = committed_rows_select(
        FIELD_VALUES,
        head,
        org_id=uuid4(),
        project_id=head.project_id,
        overlay=overlay,
        key_relation=relation,
    )
    assert (
        sql.count("(level, unit_id) IN (SELECT level, unit_id FROM compiler_candidates")
        == 3
    )
    assert params["candidate_at"] == relation.params["candidate_at"]
    for columns in (("bucket_start",), ("is_deleted",), ("unit_id", "unit_id")):
        with pytest.raises(ValueError, match="immutable"):
            committed_rows_select(
                FIELD_VALUES,
                head,
                org_id=uuid4(),
                project_id=head.project_id,
                key_relation=KeyRelation(columns, "SELECT 1", {}),
            )
    for name in ("pub_project", "pub_base", "pub_overlay"):
        with pytest.raises(ValueError, match="conflicting"):
            committed_rows_select(
                FIELD_VALUES,
                head,
                org_id=uuid4(),
                project_id=head.project_id,
                overlay=overlay,
                key_relation=KeyRelation(
                    ("unit_id",), "SELECT 'x'", {name: "different"}
                ),
            )


def test_window_nomination_has_only_one_collapse_after_all_memberships():
    from tracer.services.derived_metrics.bounded_reads import windowed_rows_select

    head = manifest()
    sql, params = windowed_rows_select(
        FIELD_VALUES,
        head,
        org_id=uuid4(),
        project_id=head.project_id,
        predicate="bucket_start = %(at)s",
        params={"at": datetime(2026, 1, 1, tzinfo=UTC)},
        key_in={"field_id": (uuid4(),), "field_version": (1,)},
        candidate_columns=("level", "unit_id"),
        frozen_overlay=("SELECT * FROM frozen_fields", {}),
    )
    assert sql.count("LIMIT 1 BY") == 1
    assert sql.count("(level, unit_id) IN (") == 3
    # The mutable predicate nominates raw keys, and still follows the collapse.
    assert sql.endswith("unit_id) WHERE bucket_start = %(at)s")
    assert sql.count("SELECT DISTINCT level, unit_id") == 1
    assert sql.count("SELECT * FROM frozen_fields") == 1
    assert "window_kin_2" not in params  # candidates never materialize as a literal


def test_checkpoint_ranges_reject_mutable_bounds_and_reach_all_branches():
    from tracer.services.derived_metrics.bounded_reads import KeyRange

    head = manifest()
    org = uuid4()
    relation = KeyRange(("unit_id",), ("a",), ("z",))
    sql, params = committed_rows_select(
        FIELD_VALUES,
        head,
        org_id=org,
        project_id=head.project_id,
        key_relation=relation,
    )
    assert sql.count("NOT (unit_id > %(checkpoint_range_upper_0)s)") == 2
    assert sql.count("unit_id > %(checkpoint_range_lower_0)s") == 2
    assert params["checkpoint_range_upper_0"] == "z"
    for key in ("status", "unit_start", "is_deleted"):
        with pytest.raises(ValueError, match="immutable"):
            committed_rows_select(
                FIELD_VALUES,
                head,
                org_id=org,
                project_id=head.project_id,
                key_relation=KeyRange((key,), None, ("z",)),
            )


def test_checkpoint_payload_cap_is_explicit_and_legacy_plans_still_replay():
    from tracer.services.derived_metrics.publication import (
        MAX_BATCH_BYTES,
        MAX_BATCH_ROWS,
        MAX_COMPACTION_ROWS,
        FrozenRows,
    )

    rows = ("00",) * (MAX_BATCH_ROWS + 1)
    with pytest.raises(ValueError, match="row cap"):
        FrozenRows(rows)
    checkpoint = FrozenRows(rows, row_limit=MAX_COMPACTION_ROWS)
    assert (
        FrozenRows.from_plan({"rows": rows, "row_limit": MAX_COMPACTION_ROWS}).digest
        == checkpoint.digest
    )
    assert FrozenRows.from_plan({"rows": ["00"]}).rows == ("00",)
    with pytest.raises(ValueError, match="byte cap"):
        FrozenRows(("00" * (MAX_BATCH_BYTES + 1),), row_limit=MAX_COMPACTION_ROWS)
    with pytest.raises(ValueError, match="row cap"):
        FrozenRows((), row_limit=MAX_COMPACTION_ROWS + 1)


def test_checkpoint_optimize_uses_actual_partition_id_and_hard_limits():
    from tracer.services.derived_metrics.publication import ClickHousePublicationStore

    timeouts, statements = [], []
    socket = SimpleNamespace(gettimeout=lambda: 300, settimeout=timeouts.append)

    def execute(sql, params, **kwargs):
        statements.append((sql, params, kwargs))
        return SimpleNamespace(packet_generator=iter(()))

    store = ClickHousePublicationStore(
        SimpleNamespace(
            connection=SimpleNamespace(socket=socket, force_connect=lambda: None),
            execute_with_progress=execute,
        )
    )
    store.optimize_checkpoint("actual-tuple-id", "durable-query-id", 60)
    assert len(statements) == 1
    sql, params, options = statements[0]
    assert (
        sql
        == "OPTIMIZE TABLE metric_field_values_checkpoint PARTITION ID %(partition)s FINAL"
    )
    assert params == {"partition": "actual-tuple-id"}
    assert options["query_id"] == "durable-query-id"
    assert options["settings"]["max_execution_time"] == 60
    assert options["settings"]["max_threads"] == 2
    assert options["settings"]["max_memory_usage"] == 512 * 1024**2
    assert all(0 < timeout <= 60 for timeout in timeouts[:-1])
    assert timeouts[-1] == 300
    for seconds in (0, 61):
        with pytest.raises(ValueError, match="deadline"):
            store.optimize_checkpoint("actual-tuple-id", "another-query-id", seconds)
    assert len(statements) == 1
    # Real tuple byte/part guards and durable timeout/replay obligations are
    # exercised by test_metric_checkpoint_partitioning_clickhouse.py.


@pytest.mark.parametrize(
    "engine", ["ReplicatedReplacingMergeTree", "SharedReplacingMergeTree"]
)
def test_checkpoint_maintenance_requires_qualified_writer_coordination(engine):
    from tracer.services.derived_metrics.publication import (
        ClickHousePublicationStore,
        MetricVisibilityError,
    )

    store = ClickHousePublicationStore(object())
    statements = []

    def execute(sql, params):
        statements.append(sql)
        return [(engine,)]

    store.execute = execute
    with pytest.raises(MetricVisibilityError, match="topology is not qualified"):
        store.consolidate_checkpoint(uuid4())
    assert len(statements) == 1 and "system.tables" in statements[0]
    assert not any("OPTIMIZE" in sql for sql in statements)
