# ruff: noqa: F811 -- shared isolated CH fixture
"""Real-CH immutable pushdown oracles, typed nomination and physical pruning.

Includes real-writer R01 parity. These are not completion evidence for the
retirement, grouped-backfill or all-proposal lifecycle redesign.
"""

import json
import random
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from time import sleep
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from tracer.services.derived_metrics.bounded_reads import (
    committed_key_pages,
    key_membership_sql,
    merge_bindings,
)
from tracer.services.derived_metrics.publication import (
    WRITE_SETTINGS,
    committed_rows_select,
    physical_table,
)
from tracer.services.derived_metrics.storage import (
    BUCKET_TOTALS,
    FIELD_VALUES,
    LIST_CURRENT_MEMBERS,
    LIST_CURRENT_ROOTS,
    RETIRED_PARTS,
    SOURCE_DEPENDENCIES,
    UNIT_STATE,
)
from tracer.tests.test_derived_metric_publication_clickhouse import (
    live_writer,  # noqa: F401
    metric_ch,  # noqa: F401
    publication_store,  # noqa: F401
)

LOGICAL_TABLES = (
    FIELD_VALUES,
    BUCKET_TOTALS,
    UNIT_STATE,
    SOURCE_DEPENDENCIES,
    RETIRED_PARTS,
    LIST_CURRENT_MEMBERS,
    LIST_CURRENT_ROOTS,
)
AT = datetime(2026, 1, 1, tzinfo=UTC)


def _keys(table, org, project, field):
    for i in range(6):
        row = {"org_id": org, "project_id": project}
        for name in table.logical_key[2:]:
            row[name] = {
                "field_id": field,
                "field_version": 1 + i // 3,
                "metric_id": field,
                "metric_version": 1 + i // 3,
                "level": "conversation",
                "unit_id": f"unit-{i if table == UNIT_STATE else i % 3}",
                "trace_id": f"trace-{i // 2}",
                "span_id": f"span-{i % 2}",
                "bucket_start": AT,
                "group_key": f"group-{i % 3}",
                "scope_key": "scope",
                "stratum_key": "stratum",
                "observation_type": "GENERATION",
                "service_name": "service",
                "start_hour": AT,
            }[name]
        yield row


def _payload(table, rng, index):
    payload = {"is_deleted": int(index % 4 == 0)}
    if table == FIELD_VALUES:
        payload.update(
            status=rng.choice(["ok", "open", "no_value"]),
            session_id=f"session-{index % 3}",
            user_id=f"user-{index % 2}",
            unit_start=AT + timedelta(days=rng.choice([-2, 2])),
            last_arrival_ns=rng.randrange(100),
            value_number=float(index),
            inputs={"n": float(index), "missing": None},
        )
    elif table in (SOURCE_DEPENDENCIES, LIST_CURRENT_MEMBERS, LIST_CURRENT_ROOTS):
        # Remaps change payload ownership, never the immutable physical key.
        payload.update(
            canonical_session_id=f"canonical-{index % 3}",
            raw_session_id="alias",
            canonical_user_id=f"canonical-user-{index % 2}",
            raw_user_id="user-alias",
            unit_start=AT + timedelta(days=rng.choice([-2, 2])),
        )
    elif table == UNIT_STATE:
        payload.update(
            last_arrival_ns=rng.randrange(100),
            reopened=index % 2,
            first_seen=AT - timedelta(days=3),
            due_at=AT + timedelta(days=index % 3),
        )
    else:
        payload.update(unit_count=index, weighted_n=float(index), weighted_d=1.0)
    return payload


def _membership(table, keys, mode):
    if mode == 0:
        return {"project_id": []}
    if table == FIELD_VALUES:
        return {"field_id": [keys[0]["field_id"]], "unit_id": ["unit-0", "unit-2"]}
    if table == UNIT_STATE:
        return {"level": ["conversation"], "unit_id": ["unit-0", "unit-2"]}
    if table in (BUCKET_TOTALS, RETIRED_PARTS):
        return {
            "metric_id": [keys[0]["metric_id"]],
            "bucket_start": [AT],
            "group_key": ["group-0", "group-2"],
        }
    return {("trace_id", "span_id"): [("trace-0", "span-0"), ("trace-1", "span-1")]}


def _matches(row, membership):
    return all(
        (row[cols] if isinstance(cols, str) else tuple(row[col] for col in cols))
        in values
        for cols, values in membership.items()
    )


def _freeze_value(value):
    return tuple(sorted(value.items())) if isinstance(value, dict) else value


def _result(rows):
    return {tuple(_freeze_value(value) for value in row) for row in rows}


def test_205_seed_independent_oracle_all_logical_kinds_and_bl01(publication_store):
    """Python winner map AND original outer-filter SQL, not new-vs-new parity."""
    store = publication_store
    inserts, cases = defaultdict(list), []
    for seed in range(205):
        rng = random.Random(35880000 + seed)
        org, project, field = (UUID(int=rng.getrandbits(128)) for _ in range(3))
        attempts = [UUID(int=rng.getrandbits(128)) for _ in range(24)]
        shape = seed % 7
        checkpoint = attempts[0] if shape in (0, 3, 5, 6) else None
        delta_count = (20 if seed % 19 == 0 else 2) if shape in (1, 3, 4, 6) else 0
        pairs = ([(1, checkpoint)] if checkpoint else []) + [
            (i + 2, attempts[i + 1]) for i in range(delta_count)
        ]
        head = SimpleNamespace(
            project_id=project,
            checkpoint_attempt_id=checkpoint,
            allowed_attempts=lambda pairs=pairs: pairs,
        )
        overlay = (
            SimpleNamespace(
                id=attempts[22],
                generation=25,
                purpose="compaction" if seed % 2 else "incremental",
            )
            if shape in (2, 4, 5, 6)
            else None
        )
        for table in LOGICAL_TABLES:
            keys = list(_keys(table, org, project, field))
            records = []
            sources = [(g, a, a == checkpoint, True) for g, a in pairs]
            if overlay:
                sources.append(
                    (
                        overlay.generation,
                        overlay.id,
                        overlay.purpose == "compaction",
                        True,
                    )
                )
            sources.append(
                (99, attempts[23], False, False)
            )  # uncommitted high generation
            for generation, attempt, is_checkpoint, allowed in sources:
                for i, key in enumerate(keys):
                    if rng.random() < 0.15:
                        continue
                    for revision in range(1, 3):
                        row = {
                            **key,
                            **_payload(table, rng, generation + i + revision),
                            "generation": generation,
                            "attempt_id": attempt,
                            table.version_column: (100 - generation) * 10 + revision,
                        }
                        columns = tuple(row)
                        target = physical_table(table, checkpoint=is_checkpoint)
                        inserts[(target.name, columns)].append(tuple(row.values()))
                        # Byte-identical retries are defined; divergent ties are not.
                        if rng.random() < 0.2:
                            inserts[(target.name, columns)].append(tuple(row.values()))
                        if allowed:
                            records.append(row)
            membership = _membership(table, keys, seed % 11)
            after_name = next(
                (
                    c
                    for c in ("unit_id", "group_key", "span_id")
                    if c in table.logical_key
                ),
                None,
            )
            after = {after_name: keys[0][after_name]} if seed % 3 == 0 else None
            winners = {}
            for row in records:
                key = tuple(row[c] for c in table.logical_key)
                old = winners.get(key)
                if old is None or (row["generation"], row[table.version_column]) > (
                    old["generation"],
                    old[table.version_column],
                ):
                    winners[key] = row
            columns = (
                tuple(keys[0])
                + tuple(_payload(table, rng, 1))
                + ("generation", "attempt_id", table.version_column)
            )
            expected = [
                row
                for row in winners.values()
                if _matches(row, membership)
                and (not after or row[after_name] > after[after_name])
            ]
            cases.append(
                (seed, table, head, overlay, org, membership, after, columns, expected)
            )
    for (name, columns), rows in inserts.items():
        store.client.execute(f"INSERT INTO {name} ({', '.join(columns)}) VALUES", rows)
    for seed, table, head, overlay, org, membership, after, columns, expected in cases:
        args = {
            "org_id": org,
            "project_id": head.project_id,
            "overlay": overlay,
            "key_after": after,
        }
        legacy, lp = committed_rows_select(table, head, **args, param_prefix="legacy")
        outer, op = key_membership_sql(table, membership, param_prefix="outer")
        pushed, pp = committed_rows_select(
            table, head, **args, key_in=membership, param_prefix="pushed"
        )
        projection = ", ".join(columns)
        reference = _result([tuple(row[c] for c in columns) for row in expected])
        assert (
            _result(
                store.execute(
                    f"SELECT {projection} FROM ({legacy}) WHERE {outer}",
                    merge_bindings(lp, op),
                )
            )
            == reference
        ), (seed, table.name, "legacy")
        assert (
            _result(store.execute(f"SELECT {projection} FROM ({pushed})", pp))
            == reference
        ), (seed, table.name, "pushed")
        predicate = "is_deleted = 0"
        selected = [row for row in expected if not row["is_deleted"]]
        if "unit_start" in columns:
            predicate += " AND unit_start >= toDateTime64(%(cutoff)s, 6, 'UTC')"
            pp["cutoff"] = AT
            selected = [row for row in selected if row["unit_start"] >= AT]
        if table == FIELD_VALUES:
            predicate += " AND status = 'ok'"
            selected = [row for row in selected if row["status"] == "ok"]
        assert _result(
            store.execute(f"SELECT {projection} FROM ({pushed}) WHERE {predicate}", pp)
        ) == _result([tuple(row[c] for c in columns) for row in selected]), (
            seed,
            table.name,
            "mutable-after",
        )


def test_nomination_exhausts_typed_union_and_includes_every_revision(publication_store):
    store = publication_store
    org, project = uuid4(), uuid4()
    attempts = [uuid4() for _ in range(3)]
    head = SimpleNamespace(
        project_id=project,
        checkpoint_attempt_id=attempts[0],
        allowed_attempts=lambda: [(1, attempts[0]), (2, attempts[1])],
    )
    overlay = SimpleNamespace(id=attempts[2], generation=3, purpose="incremental")
    # UUID textual ordering must not be assumed equivalent to the server's order.
    fields = [
        UUID("ffffffff-0000-0000-0000-000000000001"),
        UUID("00000000-0000-0000-ffff-ffffffffffff"),
    ]
    rows = defaultdict(list)
    for source, attempt in enumerate(attempts):
        for i in range(23):
            if (i + source) % 3 == 0:
                continue
            for revision in range(9):
                rows[source].append(
                    (
                        org,
                        project,
                        fields[i % 2],
                        1,
                        "conversation",
                        f"u-{i:02}",
                        source + 1,
                        attempt,
                        revision + 1,
                        i % 2,
                    )
                )
    columns = "org_id, project_id, field_id, field_version, level, unit_id, generation, attempt_id, _version, is_deleted"
    for source, data in rows.items():
        target = physical_table(FIELD_VALUES, checkpoint=source == 0)
        store.client.execute(f"INSERT INTO {target.name} ({columns}) VALUES", data)
    keys = FIELD_VALUES.logical_key[2:]
    legacy, lp = committed_rows_select(
        FIELD_VALUES, head, org_id=org, project_id=project, overlay=overlay
    )
    truth = store.execute(
        f"SELECT {', '.join(keys)} FROM ({legacy}) ORDER BY {', '.join(keys)}", lp
    )
    pages = list(
        committed_key_pages(
            store,
            FIELD_VALUES,
            head,
            org_id=org,
            project_id=project,
            overlay=overlay,
            target_rows=3,
        )
    )
    assert [key for page in pages for key in page] == truth
    assert all(len(page) <= 3 for page in pages)
    actual, finite = [], []
    for page in pages:
        sql, params = committed_rows_select(
            FIELD_VALUES,
            head,
            org_id=org,
            project_id=project,
            overlay=overlay,
            key_in={keys: page},
        )
        finite.extend(
            store.execute(
                f"SELECT unit_id, generation, _version, is_deleted FROM ({sql})", params
            )
        )
        # All rows in some pages can be discarded. That is NOT source exhaustion.
        actual.extend(
            store.execute(f"SELECT {', '.join(keys)} FROM ({sql}) WHERE 0", params)
        )
    assert actual == []
    assert _result(finite) == _result(
        store.execute(
            f"SELECT unit_id, generation, _version, is_deleted FROM ({legacy})", lp
        )
    )
    assert all(row[2] == 9 for row in finite)
    # Physical merges cannot change the exact pinned winner relation.
    for table in (FIELD_VALUES, physical_table(FIELD_VALUES, checkpoint=True)):
        store.client.execute(f"OPTIMIZE TABLE {table.name} FINAL")
    assert [
        key
        for page in committed_key_pages(
            store,
            FIELD_VALUES,
            head,
            org_id=org,
            project_id=project,
            overlay=overlay,
            target_rows=4,
        )
        for key in page
    ] == truth


def _read_receipt(store, sql, params):
    query_id = "perf1_" + uuid4().hex
    rows = store.client.execute(sql, params, query_id=query_id, settings=WRITE_SETTINGS)
    # Query-log events can arrive after a flush while a one-GiB server is
    # finishing inserts/merges. A missing receipt must never count as a pass.
    for _ in range(20):
        store.client.execute("SYSTEM FLUSH LOGS")
        receipt = store.client.execute(
            "SELECT read_rows, read_bytes, memory_usage, ProfileEvents['SelectedMarks'], ProfileEvents['SelectedParts'] "
            "FROM system.query_log WHERE query_id = %(qid)s AND type = 'QueryFinish'",
            {"qid": query_id},
        )
        if receipt:
            break
        sleep(0.1)
    assert len(receipt) == 1
    return rows, dict(
        zip(
            (
                "read_rows",
                "read_bytes",
                "memory_bytes",
                "selected_marks",
                "selected_parts",
            ),
            receipt[0],
            strict=True,
        )
    )


def test_contiguous_and_scattered_page_pruning_at_one_million(publication_store):
    """Dedicated fixture only: no change to production DDL or M7 layout claim."""
    store = publication_store
    checkpoint = physical_table(FIELD_VALUES, checkpoint=True)
    # Granularity is immutable on an existing MergeTree. Recreate only this
    # empty table inside the fixture-owned database, never a production schema.
    store.client.execute(f"DROP TABLE {checkpoint.name} SYNC")
    structure = ", ".join(f"{name} {kind}" for name, kind in checkpoint.schema)
    store.client.execute(
        f"CREATE TABLE {checkpoint.name} ({structure}) "
        f"ENGINE = ReplacingMergeTree({checkpoint.version_column}) "
        f"PARTITION BY {checkpoint.partition_by} ORDER BY ({', '.join(checkpoint.order_by)}) "
        "SETTINGS index_granularity = 256"
    )
    org, project, field, attempt = (uuid4() for _ in range(4))
    head = SimpleNamespace(
        project_id=project,
        checkpoint_attempt_id=attempt,
        allowed_attempts=lambda: [(1, attempt)],
    )
    params = {"org": org, "project": project, "field": field, "attempt": attempt}
    # Keep the measured part/granule domain stable and avoid background merge
    # memory competing with the one-GiB sidecar's foreground assertion.
    store.client.execute(f"SYSTEM STOP MERGES {checkpoint.name}")
    # Each insert stays below the sidecar's one-GiB RSS cap even though the
    # complete fixture still contains one million independently sorted keys.
    for offset in range(0, 1_000_000, 10_000):
        store.client.execute(
            f"INSERT INTO {checkpoint.name} (org_id, project_id, field_id, field_version, level, unit_id, generation, attempt_id, _version) "
            "SELECT %(org)s, %(project)s, %(field)s, 1, 'conversation', hex(MD5(toString(number))), 1, %(attempt)s, 1 "
            "FROM numbers(%(offset)s, 10000)",
            {**params, "offset": offset},
            settings=WRITE_SETTINGS,
        )
    legacy, lp = committed_rows_select(
        FIELD_VALUES, head, org_id=org, project_id=project
    )
    # Fixture-only key sampling is not a payload read. There is exactly one
    # revision per key here; do not spend the oracle's collapse budget sampling.
    source = (
        f"SELECT unit_id FROM {checkpoint.name} WHERE org_id = %(org)s "
        "AND project_id = %(project)s AND field_id = %(field)s "
        "AND field_version = 1 AND level = 'conversation'"
    )
    pages = {
        "contiguous": [
            row[0]
            for row in store.execute(
                f"{source} ORDER BY unit_id LIMIT 50 OFFSET 500000", params
            )
        ],
        "scattered": [
            row[0]
            for row in store.execute(
                f"{source} ORDER BY sipHash64(unit_id) LIMIT 50", params
            )
        ],
    }
    broad_receipt = None
    for label, ids in pages.items():
        membership = {
            "field_id": [field],
            "field_version": [1],
            "level": ["conversation"],
            "unit_id": ids,
        }
        outer, op = key_membership_sql(FIELD_VALUES, membership, param_prefix="outer")
        pushed, pp = committed_rows_select(
            FIELD_VALUES, head, org_id=org, project_id=project, key_in=membership
        )
        if broad_receipt is None:
            old_rows, broad_receipt = _read_receipt(
                store,
                f"SELECT unit_id FROM ({legacy}) WHERE {outer}",
                merge_bindings(lp, op),
            )
            assert _result(old_rows) == {(value,) for value in ids}
        # The unchanged broad collapse is measured once: issuing a second
        # million-key LIMIT BY after the first can exceed the one-GiB sidecar.
        # Both finite pages still get their own query-log read-row receipts.
        new_rows, new_receipt = _read_receipt(
            store, f"SELECT unit_id FROM ({pushed})", pp
        )
        assert _result(new_rows) == {(value,) for value in ids}
        assert new_receipt["read_rows"] < broad_receipt["read_rows"]
        assert (
            new_receipt["read_rows"]
            <= (new_receipt["selected_marks"] + 2 * new_receipt["selected_parts"]) * 256
        )
        explain = store.execute(f"EXPLAIN indexes=1 SELECT unit_id FROM ({pushed})", pp)
        assert any("PrimaryKey" in row[0] for row in explain)
        print(
            json.dumps(
                {
                    "fixture_rows": 1_000_000,
                    "page": label,
                    "broad_baseline_contiguous": broad_receipt,
                    "pushed": new_receipt,
                }
            )
        )


def test_opaque_bucket_states_are_not_rebuilt_by_pushdown(publication_store):
    from tracer.tests.test_derived_metric_publication_clickhouse import (
        default_row_select,
    )

    store = publication_store
    org, project, metric, base, delta = (uuid4() for _ in range(5))
    head = SimpleNamespace(
        project_id=project,
        checkpoint_attempt_id=base,
        allowed_attempts=lambda: [(1, base), (2, delta)],
    )
    for generation, attempt in ((1, base), (2, delta)):
        overrides = {
            "org_id": "toUUID(%(org)s)",
            "project_id": "toUUID(%(project)s)",
            "metric_id": "toUUID(%(metric)s)",
            "metric_version": "toUInt32(1)",
            "bucket_start": "toDateTime(%(at)s, 'UTC')",
            "group_key": "'group'",
            "generation": "toUInt64(%(generation)s)",
            "attempt_id": "toUUID(%(attempt)s)",
            "version": "toUInt64(100 - %(generation)s)",
            "uniq_state": "initializeAggregation('uniqCombined64State', toString(%(generation)s))",
            "quantile_state": "initializeAggregation('quantileTDigestState', toFloat64(%(generation)s))",
        }
        store.execute(
            f"INSERT INTO {RETIRED_PARTS.name} "
            + default_row_select(RETIRED_PARTS, overrides),
            {
                "org": str(org),
                "project": str(project),
                "metric": str(metric),
                "at": AT,
                "generation": generation,
                "attempt": str(attempt),
            },
        )
    membership = {"metric_id": [metric], "metric_version": [1], "bucket_start": [AT]}
    legacy, lp = committed_rows_select(
        RETIRED_PARTS, head, org_id=org, project_id=project
    )
    pushed, pp = committed_rows_select(
        RETIRED_PARTS, head, org_id=org, project_id=project, key_in=membership
    )
    outer, op = key_membership_sql(
        RETIRED_PARTS, membership, param_prefix="opaque_outer"
    )
    serialized = "hex(formatRow('RowBinary', " + ", ".join(RETIRED_PARTS.columns) + "))"
    assert store.execute(
        f"SELECT {serialized} FROM ({legacy}) WHERE {outer}", merge_bindings(lp, op)
    ) == store.execute(f"SELECT {serialized} FROM ({pushed})", pp)
    assert store.execute(
        f"SELECT finalizeAggregation(uniq_state), finalizeAggregation(quantile_state) FROM ({pushed})",
        pp,
    ) == [(1, 2.0)]


def test_nomination_supports_all_current_table_key_types(publication_store):
    store = publication_store
    org, project, field, attempt = (uuid4() for _ in range(4))
    head = SimpleNamespace(
        project_id=project,
        checkpoint_attempt_id=attempt,
        allowed_attempts=lambda: [(1, attempt)],
    )
    for table in LOGICAL_TABLES:
        keys = list(_keys(table, org, project, field))
        columns = (*table.logical_key, "generation", "attempt_id", table.version_column)
        data = [
            (*tuple(key[c] for c in table.logical_key), 1, attempt, revision)
            for key in keys
            for revision in (1, 2)
        ]
        target = physical_table(table, checkpoint=True)
        store.client.execute(
            f"INSERT INTO {target.name} ({', '.join(columns)}) VALUES", data
        )
        key_columns = table.logical_key[2:]
        sql, params = committed_rows_select(table, head, org_id=org, project_id=project)
        reference = store.execute(
            f"SELECT {', '.join(key_columns)} FROM ({sql}) ORDER BY {', '.join(key_columns)}",
            params,
        )
        assert [
            key
            for page in committed_key_pages(
                store, table, head, org_id=org, project_id=project, target_rows=2
            )
            for key in page
        ] == reference, table.name
        assert (
            list(
                committed_key_pages(
                    store,
                    table,
                    head,
                    org_id=org,
                    project_id=project,
                    target_rows=2,
                    key_in={"project_id": []},
                )
            )
            == []
        )


def test_captured_source_matches_broad_with_alias_chain_and_committed_revisions(
    publication_store,
):
    from tracer.services.derived_metrics.bounded_reads import (
        SOURCE_SCHEMA,
        capture_source_output,
    )
    from tracer.services.derived_metrics.queries import Scope
    from tracer.services.derived_metrics.unit_reads import unit_values_select
    from tracer.tests.test_derived_metric_clickhouse import seed_span

    store = publication_store
    scope = Scope(uuid4(), uuid4(), uuid4())
    survivor, alias, shared, chained, missing = [UUID(int=i) for i in range(1, 6)]
    field = uuid4()
    definition = SimpleNamespace(
        metric_id=field,
        version=1,
        kind="average",
        level="conversation",
        formula="mean(resolved)",
        weighting="none",
        cohort_key="region",
        filters=[],
        inputs=[
            {
                "alias": "resolved",
                "source": "span",
                "field": "attribute_number",
                "attribute_key": "resolved",
                "reducer": "latest",
            }
        ],
    )
    checkpoint, delta, overlay_id = uuid4(), uuid4(), uuid4()
    # Source's exact allowed-attempt view includes a frozen overlay as an allowed
    # delta; the generic reader oracle below exercises the explicit overlay arm.
    head = SimpleNamespace(
        project_id=scope.project,
        checkpoint_attempt_id=checkpoint,
        delta_attempt_ids=[delta, overlay_id],
        allowed_attempts=lambda: [(1, checkpoint), (2, delta), (3, overlay_id)],
    )
    for generation, attempt in head.allowed_attempts():
        table = physical_table(FIELD_VALUES, checkpoint=attempt == checkpoint)
        for unit in (survivor, alias, shared, missing):
            store.client.execute(
                f"INSERT INTO {table.name} (org_id, workspace_id, project_id, field_id, field_version, level, unit_id, unit_start, generation, attempt_id, _version, is_deleted) VALUES",
                [
                    (
                        scope.organization,
                        scope.workspace,
                        scope.project,
                        field,
                        1,
                        "conversation",
                        str(unit),
                        AT - timedelta(days=generation),
                        generation,
                        attempt,
                        generation,
                        int(unit == missing and generation == 3),
                    )
                ],
            )
    for i, unit in enumerate((survivor, alias, shared, chained)):
        seed_span(
            store.client, scope, f"capture-{i}", unit, uuid4(), AT, resolved=i + 1
        )
    # Chains retain the actual id_remap_sql contract (not an invented transitive
    # closure); a requested alias emits an out-of-page survivor plus its tombstone.
    for old, new in [(survivor, shared), (alias, shared), (shared, chained)]:
        store.execute(
            "INSERT INTO trace_session_id_remap (old_id,new_id) VALUES (%(old)s,%(new)s)",
            {"old": str(old), "new": str(new)},
        )
    source, bound = unit_values_select(
        definition,
        scope,
        (str(alias), str(shared), str(missing), str(chained)),
        start=AT - timedelta(days=10),
        end=AT + timedelta(days=1),
        as_of=AT + timedelta(hours=1),
        manifest=head,
    )
    captured = capture_source_output(
        store, source, bound, field_id=field, field_version=1, candidate_count=4
    )
    columns = ", ".join(
        "CAST(" + n + ", '" + t.replace("'", "\\'") + "')" for n, t in SOURCE_SCHEMA
    )
    broad = Counter(
        row[0]
        for row in store.execute(
            f"SELECT hex(formatRow('RowBinary', {columns})) FROM ({source})", bound
        )
    )
    actual = Counter()
    pages = list(captured.pages(max_items=1))
    for page in pages:
        sql, params = page.select()
        actual.update(
            row[0]
            for row in store.execute(
                f"SELECT hex(formatRow('RowBinary', {columns})) FROM ({sql})", params
            )
        )
    assert actual == broad
    assert (
        str(survivor) in captured.unit_ids
        and str(survivor) not in bound["unit_read_ids"]
    )
    assert str(alias) in captured.unit_ids
    # Replaying captured rows never observes a later remap/source mutation.
    store.execute("TRUNCATE TABLE spans")
    store.execute("TRUNCATE TABLE trace_session_id_remap")
    assert sum(len(store.execute(*p.select())) for p in pages) == sum(broad.values())
    base = SimpleNamespace(
        project_id=scope.project,
        checkpoint_attempt_id=checkpoint,
        allowed_attempts=lambda: [(1, checkpoint), (2, delta)],
    )
    overlay = SimpleNamespace(id=overlay_id, generation=3, purpose="incremental")
    broad, bp = committed_rows_select(
        FIELD_VALUES,
        base,
        org_id=scope.organization,
        project_id=scope.project,
        overlay=overlay,
    )
    for page in pages:
        membership = {
            ("field_id", "field_version", "level", "unit_id"): page.field_keys
        }
        pushed, pp = committed_rows_select(
            FIELD_VALUES,
            base,
            org_id=scope.organization,
            project_id=scope.project,
            overlay=overlay,
            key_in=membership,
        )
        outer, op = key_membership_sql(FIELD_VALUES, membership, param_prefix="outer")
        assert store.execute(
            f"SELECT unit_id, is_deleted, generation FROM ({pushed})", pp
        ) == store.execute(
            f"SELECT unit_id, is_deleted, generation FROM ({broad}) WHERE {outer}",
            merge_bindings(bp, op),
        )


# The original broad readers are substituted at the REAL refresh call sites;
# source capture, admission, freeze, receipts and publication all still run.


_READERS = {"source_old_fields", "state_floor", "prior_states", "admission_anchors"}
_METADATA = {
    "org_id",
    "project_id",
    "workspace_id",
    "field_id",
    "metric_id",
    "generation",
    "attempt_id",
    "fencing_epoch",
    "row_sequence",
}


def _rows(rows):
    return Counter(tuple(_freeze_value(value) for value in row) for row in rows)


def _install_reader_parity(monkeypatch, refresh, store, calls, *, broad_project=None):
    """Wrap the REAL refresh.py call sites.

    Every watched reader executes its pushed relation and the original broad
    (post-collapse outer-filter) relation at the writer's actual bindings and
    asserts identical rows. A project named broad_project then CONSUMES the
    broad relation, so twin runs compare complete staged/published output.
    """
    original = refresh.committed_rows_select

    def reader(table, manifest, **kwargs):
        prefix = kwargs.get("param_prefix")
        if prefix not in _READERS:
            return original(table, manifest, **kwargs)
        assert kwargs.get("key_in"), prefix
        pushed, pushed_params = original(table, manifest, **kwargs)
        broad_kwargs = {k: v for k, v in kwargs.items() if k != "key_in"}
        broad, broad_params = original(table, manifest, **broad_kwargs)
        outer, outer_params = key_membership_sql(
            table, kwargs["key_in"], param_prefix=f"{prefix}_outer"
        )
        old = f"SELECT * FROM ({broad}) WHERE {outer}"
        old_params = merge_bindings(broad_params, outer_params)
        columns = ", ".join(table.columns)
        got = store.execute(f"SELECT {columns} FROM ({pushed})", pushed_params)
        want = store.execute(f"SELECT {columns} FROM ({old})", old_params)
        assert _rows(got) == _rows(want), prefix
        calls.append(
            {
                "prefix": prefix,
                "project": str(kwargs["project_id"]),
                "pushed_rows": len(got),
                "project_rows": store.execute(
                    f"SELECT count() FROM ({broad})", broad_params
                )[0][0],
                "pushed": (pushed, pushed_params),
                "broad": (old, old_params),
            }
        )
        if broad_project is not None and str(kwargs["project_id"]) == str(
            broad_project
        ):
            return old, old_params
        return pushed, pushed_params

    admission = refresh.RefreshWriter._unproven_source_admission

    def check_admission(writer, head, page, query, params, old, old_params, floor):
        # Reader equality alone cannot catch OR-ing absence across version pages.
        # Compare the actual decision with the original single broad anti-join,
        # on the SAME captured source and pinned manifest used by the writer.
        broad, bound = original(
            FIELD_VALUES,
            head,
            org_id=writer.org_id,
            project_id=writer.lease.project_id,
            param_prefix="oracle_admission",
        )
        expected = bool(
            writer.store.execute(
                f"SELECT count() FROM ({query}) WHERE is_deleted = 0 AND unit_id NOT IN "
                f"(SELECT unit_id FROM ({broad}) WHERE field_id = %(oracle_field)s "
                "AND field_version = %(oracle_version)s AND is_deleted = 0) "
                f"AND (unit_id, unit_start) NOT IN (SELECT unit_id, unit_start FROM ({broad}) "
                "WHERE is_deleted = 0 AND level = 'conversation' "
                "AND unit_start >= toDateTime64(%(oracle_floor)s, 6, 'UTC'))",
                merge_bindings(
                    params,
                    bound,
                    {
                        "oracle_field": page.field_id,
                        "oracle_version": page.field_version,
                        "oracle_floor": floor,
                    },
                ),
            )[0][0]
        )
        actual = admission(writer, head, page, query, params, old, old_params, floor)
        assert actual == expected
        return actual

    monkeypatch.setattr(refresh, "committed_rows_select", reader)
    monkeypatch.setattr(
        refresh.RefreshWriter, "_unproven_source_admission", check_admission
    )
    return original


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("seed", [1907, 8103])
def test_real_writer_twin_projects_broad_and_pushed_publish_identical_rows(
    live_writer, publication_store, workspace, observe_project, monkeypatch, seed
):
    """Same randomized remap/tombstone history in two projects; only the lookup
    SQL differs. The old broad form and the pushed form must publish identical
    fields, lifecycle state, buckets, staged bytes and dirty ledgers; a granule
    receipt on the real writer SQL proves it reads candidate granules only."""
    from tracer.models.derived_metric import (
        DerivedMetric,
        DerivedMetricVersion,
        MetricAttemptLedgerChunk,
    )
    from tracer.models.project import Project
    from tracer.services.derived_metrics import refresh
    from tracer.services.derived_metrics.bounded_reads import CapturedSourceOutput
    from tracer.services.derived_metrics.changes import ChangeBatch, UnitKey
    from tracer.services.derived_metrics.lease import project_lease
    from tracer.services.derived_metrics.publication import FrozenRows
    from tracer.services.derived_metrics.queries import Scope
    from tracer.services.derived_metrics.refresh import RefreshWriter, payload_select
    from tracer.tests.test_derived_metric_refresh_clickhouse import (
        definition_for,
        seed_span_revision,
    )

    project_b = Project.objects.create(
        name="Pushed twin",
        organization=workspace.organization,
        workspace=workspace,
        model_type=observe_project.model_type,
        trace_type="observe",
    )
    calls = []
    original = _install_reader_parity(
        monkeypatch, refresh, publication_store, calls, broad_project=observe_project.id
    )
    original_pages = CapturedSourceOutput.pages
    # One key per emitted-output subpage, independent of the raw page-wide flag.
    monkeypatch.setattr(
        CapturedSourceOutput, "pages", lambda self: original_pages(self, max_items=1)
    )
    now = datetime.now(UTC).replace(microsecond=0)
    at = now - timedelta(hours=2)
    groups = [
        (UUID(int=100 + i), UUID(int=200 + i), UUID(int=300 + i)) for i in range(4)
    ]

    def change(ids, cursor):
        return ChangeBatch(
            (),
            tuple(UnitKey("conversation", str(u)) for u in ids),
            (),
            (),
            (),
            {"watermark_ns": cursor},
        )

    args = {
        "start": at - timedelta(days=1),
        "end": now + timedelta(days=1),
        "as_of": now,
    }

    def build(writer, project, definition):
        scope = Scope(workspace.organization_id, workspace.id, project.id)
        rng = random.Random(seed)
        for i, (survivor, alias, _) in enumerate(groups):
            for unit, start in ((survivor, at), (alias, at - timedelta(minutes=15))):
                seed_span_revision(
                    writer,
                    scope,
                    UUID(int=400 + i),
                    f"stable-{unit}",
                    unit,
                    start,
                    rng.randint(-20, 40),
                    revision=1,
                    group=f"g-{rng.randrange(3)}",
                )
            if i < 3:
                seed_span_revision(
                    writer,
                    scope,
                    UUID(int=400 + i),
                    f"latest-{i}",
                    survivor,
                    at,
                    rng.randint(50, 80),
                    revision=1,
                    arrival=now - timedelta(minutes=5),
                )
        return scope

    def stage(writers, number, ids):
        return [w.refresh(change(ids, number), [d], s, **args) for w, s, d in writers]

    def published(writer, scope, manifest, definition):
        result = {}
        for table, kind in (
            (FIELD_VALUES, "field_values"),
            (UNIT_STATE, "unit_state"),
            (BUCKET_TOTALS, "bucket_totals"),
        ):
            columns = ", ".join(
                f"finalizeAggregation({column})"
                if kind.startswith("AggregateFunction(")
                else column
                for column, kind in table.schema
                if column not in _METADATA | {table.version_column}
            )
            sql, params = original(
                table, manifest, org_id=writer.org_id, project_id=scope.project
            )
            result[kind] = _rows(
                writer.store.execute(f"SELECT {columns} FROM ({sql})", params)
            )
            payloads = [
                FrozenRows(tuple(p["rows"]))
                for p in writer.plans(manifest.published_by, kind=kind)
            ]
            sql, params = payload_select(table, payloads, prefix="twin_staged")
            result["staged_" + kind] = _rows(
                writer.store.execute(f"SELECT {columns} FROM ({sql})", params)
            )
        for part in ("dirty_before", "dirty_after", "vanished"):
            entries = [
                json.dumps(e, sort_keys=True, default=str)
                for chunk in MetricAttemptLedgerChunk.objects.filter(
                    attempt=manifest.published_by, part=part
                )
                for e in chunk.payload
            ]
            result["ledger_" + part] = Counter(entries)
        return result

    with project_lease(project_b.id, holder="perf1-twin-pushed") as lease_b:
        writer_a = live_writer
        writer_b = RefreshWriter(
            lease_b,
            publication_store,
            org_id=workspace.organization_id,
            workspace_id=workspace.id,
            retention_days=lambda: 0,
        )
        definition_a = definition_for(writer_a, workspace, observe_project)
        metric_b = DerivedMetric.objects.create(
            organization=workspace.organization,
            workspace=workspace,
            project=project_b,
            name="Test metric twin",
        )
        definition_b = DerivedMetricVersion.objects.create(
            metric=metric_b, version=1, **definition_a.semantics()
        )
        scope_a = build(writer_a, observe_project, definition_a)
        scope_b = build(writer_b, project_b, definition_b)
        worlds = [(writer_a, scope_a, definition_a), (writer_b, scope_b, definition_b)]
        stage(worlds, 1, [u for a, b, _ in groups for u in (a, b)])
        # Canonical tombstone wins over its older live revision but retains its
        # arrival floor; the alias tombstone must be emitted exactly once.
        for unit in (groups[0][0], groups[1][1]):
            writer_a.store.execute(
                "ALTER TABLE spans DELETE WHERE trace_session_id = toUUID(%(unit)s) SETTINGS mutations_sync=2",
                {"unit": str(unit)},
            )
        stage(worlds, 2, [groups[0][0], groups[1][1]])
        for writer, _, _ in worlds:
            writer.compact()
        writer_a.store.execute(
            "ALTER TABLE spans DELETE WHERE startsWith(id, 'latest-') SETTINGS mutations_sync=2"
        )
        for survivor, alias, shared in groups:
            for old in (survivor, alias):
                writer_a.store.execute(
                    "INSERT INTO trace_session_id_remap (old_id,new_id) VALUES (%(old)s,%(new)s)",
                    {"old": str(old), "new": str(shared)},
                )
        calls.clear()
        # Raw candidates are ONLY aliases; every canonical survivor is emitted
        # outside the raw pass, and one alias tombstone shares each survivor.
        manifest_a, manifest_b = stage(worlds, 3, [b for _, b, _ in groups])
        assert published(writer_a, scope_a, manifest_a, definition_a) == published(
            writer_b, scope_b, manifest_b, definition_b
        ), seed
        # Independent truth, not only twin equality.
        for writer, scope, manifest in (
            (writer_a, scope_a, manifest_a),
            (writer_b, scope_b, manifest_b),
        ):
            fields, params = original(
                FIELD_VALUES, manifest, org_id=writer.org_id, project_id=scope.project
            )
            rows = {
                r[0]: r[1:]
                for r in writer.store.execute(
                    f"SELECT unit_id, status, last_arrival_ns, is_deleted, value_number, settled_at FROM ({fields})",
                    params,
                )
            }
            floor = int((now - timedelta(minutes=5)).timestamp()) * 1_000_000_000
            for i, (survivor, alias, _) in enumerate(groups):
                assert rows[str(alias)][2] == 1  # retired alias tombstone
                if i < 3:
                    assert rows[str(survivor)][0] == "open"
                    assert rows[str(survivor)][1] >= floor
                    assert rows[str(survivor)][3:] == (None, None)
                else:
                    assert rows[str(survivor)][0] == "ok"
            buckets, bp = original(
                BUCKET_TOTALS, manifest, org_id=writer.org_id, project_id=scope.project
            )
            shifted = at - timedelta(minutes=15)
            assert writer.store.execute(
                f"SELECT bucket_start, sum(unit_count) FROM ({buckets}) WHERE is_deleted = 0 GROUP BY bucket_start",
                bp,
            ) == [(shifted.replace(minute=shifted.minute // 15 * 15, second=0), 1)]
        used = {c["prefix"] for c in calls}
        assert {"source_old_fields", "state_floor", "prior_states"} <= used
        assert any(c["project_rows"] > c["pushed_rows"] for c in calls)
        # Raw alias candidates are never the output domain.
        assert any(
            c["prefix"] == "source_old_fields" and c["pushed_rows"] for c in calls
        )

        # Real writer SQL on a fixture with many non-candidate units in the
        # SAME checkpoint part: read_rows is bounded by candidate granules.
        call = next(
            c
            for c in reversed(calls)
            if c["prefix"] == "source_old_fields" and c["project"] == str(project_b.id)
        )
        sql, params = call["pushed"]
        broad_sql, broad_params = call["broad"]
        generation, attempt = params["source_old_fields_base"][0]
        checkpoint = physical_table(FIELD_VALUES, checkpoint=True)
        previous = None
        for size in (65536, 131072):
            publication_store.client.execute(
                f"INSERT INTO {checkpoint.name} (org_id, project_id, field_id, field_version, level, unit_id, generation, attempt_id, _version) "
                "SELECT %(org)s, %(project)s, %(field)s, 1, 'conversation', concat('noise-', leftPad(toString(number), 8, '0')), %(generation)s, %(attempt)s, 1 FROM numbers(%(start)s, 65536)",
                {
                    "org": workspace.organization_id,
                    "project": project_b.id,
                    "field": definition_b.metric_id,
                    "generation": generation,
                    "attempt": attempt,
                    "start": size - 65536,
                },
                settings=WRITE_SETTINGS,
            )
            publication_store.client.execute(f"OPTIMIZE TABLE {checkpoint.name} FINAL")
            _, pushed = _read_receipt(
                publication_store, f"SELECT unit_id, is_deleted FROM ({sql})", params
            )
            _, broad = _read_receipt(
                publication_store,
                f"SELECT unit_id, is_deleted FROM ({broad_sql})",
                broad_params,
            )
            assert pushed["read_rows"] <= 2 * 8192 * max(1, pushed["selected_parts"])
            assert pushed["read_rows"] < size // 2 < broad["read_rows"], (pushed, broad)
            if previous is not None:
                assert pushed["read_rows"] <= previous + 8192, (previous, pushed)
            previous = pushed["read_rows"]
            print(
                json.dumps(
                    {
                        "seed": seed,
                        "non_candidate_rows": size,
                        "writer_lookup": "source_old_fields",
                        "pushed": pushed,
                        "broad": broad,
                    }
                )
            )


@pytest.mark.django_db(transaction=True)
def test_real_writer_chain_uses_captured_source_after_remap_mutation(
    live_writer, workspace, observe_project, monkeypatch
):
    from tracer.services.derived_metrics import refresh
    from tracer.services.derived_metrics.changes import ChangeBatch, UnitKey
    from tracer.services.derived_metrics.publication import FrozenRows
    from tracer.services.derived_metrics.queries import Scope
    from tracer.tests.test_derived_metric_refresh_clickhouse import (
        definition_for,
        seed_span_revision,
    )

    writer = live_writer
    scope = Scope(workspace.organization_id, workspace.id, observe_project.id)
    definition = definition_for(writer, workspace, observe_project)
    survivor, alias, shared, chained = [UUID(int=i) for i in range(41, 45)]
    now = datetime.now(UTC).replace(microsecond=0)
    at = now - timedelta(hours=2)
    for i, unit in enumerate((survivor, alias, shared, chained)):
        seed_span_revision(
            writer, scope, UUID(int=50 + i), "source", unit, at, i + 1, revision=1
        )

    def run(ids):
        return writer.refresh(
            ChangeBatch(
                (), tuple(UnitKey("conversation", str(u)) for u in ids), (), (), (), {}
            ),
            [definition],
            scope,
            start=at - timedelta(days=1),
            end=now + timedelta(days=1),
            as_of=now,
        )

    run((survivor, alias, shared, chained))
    writer.compact()
    for old, new in ((survivor, shared), (alias, shared), (shared, chained)):
        writer.store.execute(
            "INSERT INTO trace_session_id_remap (old_id,new_id) VALUES (%(old)s,%(new)s)",
            {"old": str(old), "new": str(new)},
        )
    calls = []
    original = _install_reader_parity(monkeypatch, refresh, writer.store, calls)
    capture = refresh.capture_source_output
    captures, expected = [], []
    columns = "unit_id, unit_start, status, value_number, is_deleted, inputs"

    def mutate_after_capture(*args, **kwargs):
        result = capture(*args, **kwargs)
        captures.append(result)
        for page in result.pages():
            sql, params = page.select()
            expected.extend(
                writer.store.execute(f"SELECT {columns} FROM ({sql})", params)
            )
        # Deliberately destroy the live source and map after complete capture.
        # The real writer must use S and its emitted domain, not execute S again.
        writer.store.execute("TRUNCATE TABLE spans")
        writer.store.execute("TRUNCATE TABLE trace_session_id_remap")
        return result

    monkeypatch.setattr(refresh, "capture_source_output", mutate_after_capture)
    # Exercise the chained map with disjoint emitted keys. Requesting shared as
    # well emits it twice (canonical + retired); final-proposal dedup is R03.
    manifest = run((alias, chained))
    assert len(captures) == 1
    assert str(survivor) in captures[0].unit_ids
    assert any(row[0] == str(alias) and row[4] == 1 for row in expected)
    fields, params = original(
        FIELD_VALUES, manifest, org_id=scope.organization, project_id=scope.project
    )
    assert _result(
        writer.store.execute(
            f"SELECT {columns} FROM ({fields}) WHERE unit_id IN %(emitted)s",
            {**params, "emitted": captures[0].unit_ids},
        )
    ) == _result(expected)
    payloads = [
        FrozenRows(tuple(p["rows"]))
        for p in writer.plans(manifest.published_by, kind="field_values")
    ]
    staged, params = refresh.payload_select(
        FIELD_VALUES, payloads, prefix="chain_staged"
    )
    # Freeze/stage the exact captured observations, not a later live source read.
    assert _rows(
        writer.store.execute(f"SELECT {columns} FROM ({staged})", params)
    ) == _rows(expected)
    assert {c["prefix"] for c in calls} >= {
        "source_old_fields",
        "state_floor",
        "prior_states",
    }


@pytest.mark.django_db(transaction=True)
def test_real_writer_cross_version_anchor_last_page_and_exact_start_rejection(
    live_writer, workspace, observe_project, monkeypatch
):
    from tracer.models.derived_metric import DerivedMetricVersion, MetricProjectRefresh
    from tracer.services.derived_metrics import refresh
    from tracer.services.derived_metrics.queries import Scope
    from tracer.tests.test_derived_metric_publication_clickhouse import field_query
    from tracer.tests.test_derived_metric_refresh_clickhouse import (
        change_for,
        definition_for,
        seed_span_revision,
    )

    writer = live_writer
    scope = Scope(workspace.organization_id, workspace.id, observe_project.id)
    first = definition_for(writer, workspace, observe_project)
    definitions = [first] + [
        DerivedMetricVersion.objects.create(
            metric=first.metric, version=v, **first.semantics()
        )
        for v in (2, 3)
    ]
    current = definitions[-1]
    now = datetime.now(UTC).replace(microsecond=0)
    at = now - timedelta(hours=2)
    # X: v1 tombstone then v2 live anchor, same start as the source (admit).
    # Y: only a v2 anchor at a DIFFERENT start (reject, same ID is not enough).
    x, x_alias, x_shared, y, y_alias, y_shared = [
        UUID(int=i) for i in (21, 22, 23, 31, 32, 33)
    ]
    attempt = writer.begin(
        purpose="compaction",
        ceilings={"retention_floor": now - timedelta(days=1)},
        versions=[
            {
                "id": d.id,
                "metric_id": d.metric_id,
                "version": d.version,
                "definition_hash": d.definition_hash,
                "level": d.level,
            }
            for d in definitions[:2]
        ],
    )
    for unit, version, start, deleted in (
        (x, 1, at, 1),
        (x, 2, at, 0),
        (y, 2, at - timedelta(hours=1), 0),
    ):
        sql, params = field_query(
            str(unit), metric=first.metric_id, start=start, deleted=deleted
        )
        writer.freeze(
            attempt,
            FIELD_VALUES,
            f"SELECT * REPLACE(toUInt32({version}) AS field_version) FROM ({sql})",
            params,
        )
    writer.seal(attempt)
    head = writer.stage_and_publish(attempt)
    for group, alias, shared, trace in (
        (x, x_alias, x_shared, UUID(int=24)),
        (y, y_alias, y_shared, UUID(int=34)),
    ):
        seed_span_revision(writer, scope, trace, "source", alias, at, 7, revision=1)
        for old in (group, alias):
            writer.store.execute(
                "INSERT INTO trace_session_id_remap (old_id,new_id) VALUES (%(old)s,%(new)s)",
                {"old": str(old), "new": str(shared)},
            )

    nominate = refresh.committed_key_pages
    anchor_pages = []

    def tiny_pages(*args, **kwargs):
        # Compaction uses the same nominator; only the admission scan is shrunk.
        anchors = kwargs.get("param_prefix") == "anchor_keys"
        if anchors:
            kwargs["target_rows"] = 1
        for page in nominate(*args, **kwargs):
            if anchors:
                anchor_pages.append(page)
            yield page

    monkeypatch.setattr(refresh, "committed_key_pages", tiny_pages)
    calls = []
    original = _install_reader_parity(monkeypatch, refresh, writer.store, calls)

    def run(alias):
        return writer.refresh(
            change_for(alias),
            [current],
            scope,
            start=at - timedelta(days=1),
            end=now + timedelta(days=1),
            as_of=now,
        )

    manifest = run(x_alias)
    sql, params = original(
        FIELD_VALUES, manifest, org_id=scope.organization, project_id=scope.project
    )
    assert writer.store.execute(
        f"SELECT unit_id, unit_start, value_number FROM ({sql}) WHERE field_version = 3 AND is_deleted = 0",
        params,
    ) == [(str(x), at, 7.0)]
    # The v1 tombstone page cannot prove absence; the v2 live match is page two.
    assert len(anchor_pages) == 2
    assert {c["prefix"] for c in calls} >= {"admission_anchors", "source_old_fields"}
    before = MetricProjectRefresh.objects.get(project_id=scope.project)
    anchor_pages.clear()
    with pytest.raises(refresh.MetricAdmissionUnproven):
        run(y_alias)
    assert len(anchor_pages) == 1  # every candidate anchor page was exhausted
    # The refresh's own preliminary compaction may publish, but the rejected pass
    # advances no cursor and writes no current-version field for Y.
    after = MetricProjectRefresh.objects.get(project_id=scope.project)
    assert after.watermark_ns == before.watermark_ns
    assert after.head_manifest.published_by.purpose in {"compaction", "incremental"}
    sql, params = original(
        FIELD_VALUES,
        after.head_manifest,
        org_id=scope.organization,
        project_id=scope.project,
    )
    assert writer.store.execute(
        f"SELECT count() FROM ({sql}) WHERE unit_id = %(y)s AND field_version = 3",
        {**params, "y": str(y)},
    ) == [(0,)]
    assert head.id != after.head_manifest_id


@pytest.mark.django_db(transaction=True)
def test_writer_capture_transport_failure_never_freezes_or_advances(
    live_writer, workspace, observe_project, monkeypatch
):
    from tracer.models.derived_metric import MetricProjectRefresh, MetricRefreshAttempt
    from tracer.services.derived_metrics.queries import Scope
    from tracer.tests.test_derived_metric_refresh_clickhouse import (
        change_for,
        definition_for,
        seed_span_revision,
    )

    writer = live_writer
    scope = Scope(workspace.organization_id, workspace.id, observe_project.id)
    definition = definition_for(writer, workspace, observe_project)
    unit = uuid4()
    now = datetime.now(UTC).replace(microsecond=0)
    seed_span_revision(
        writer, scope, uuid4(), "source", unit, now - timedelta(hours=1), 1, revision=1
    )
    stream = writer.store.client.execute_iter

    def fail_after_last_row(*args, **kwargs):
        yield from stream(*args, **kwargs)
        raise RuntimeError("transport failed after final data block")

    frozen = []
    monkeypatch.setattr(writer.store.client, "execute_iter", fail_after_last_row)
    monkeypatch.setattr(writer, "freeze", lambda *args, **kwargs: frozen.append(args))
    with pytest.raises(RuntimeError, match="transport failed"):
        writer.refresh(
            change_for(unit),
            [definition],
            scope,
            start=now - timedelta(days=1),
            end=now + timedelta(days=1),
            as_of=now,
        )
    assert frozen == []
    assert not MetricRefreshAttempt.objects.filter(project_id=scope.project).exists()
    state = MetricProjectRefresh.objects.get(project_id=scope.project)
    assert state.head_manifest_id is None and state.watermark_ns == 0


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("raw_has_before", [False, True])
def test_original_raw_page_guard_survives_emitted_output_subpages(
    live_writer, workspace, observe_project, monkeypatch, raw_has_before
):
    from tracer.services.derived_metrics import refresh
    from tracer.services.derived_metrics.bounded_reads import CapturedSourceOutput
    from tracer.services.derived_metrics.queries import Scope
    from tracer.tests.test_derived_metric_publication_clickhouse import field_query
    from tracer.tests.test_derived_metric_refresh_clickhouse import (
        change_for,
        definition_for,
        seed_span_revision,
    )

    writer = live_writer
    scope = Scope(workspace.organization_id, workspace.id, observe_project.id)
    definition = definition_for(writer, workspace, observe_project)
    survivor, alias, shared = [UUID(int=i) for i in (31, 32, 33)]
    now = datetime.now(UTC).replace(microsecond=0)
    attempt = writer.begin(purpose="compaction")
    for unit in [survivor, alias] if raw_has_before else [survivor]:
        sql, params = field_query(
            str(unit), metric=definition.metric_id, start=now - timedelta(days=2)
        )
        writer.freeze(attempt, FIELD_VALUES, sql, params)
    writer.seal(attempt)
    writer.stage_and_publish(attempt)
    for old in (survivor, alias):
        writer.store.execute(
            "INSERT INTO trace_session_id_remap (old_id,new_id) VALUES (%(old)s,%(new)s)",
            {"old": str(old), "new": str(shared)},
        )
    seed_span_revision(
        writer, scope, uuid4(), "recent", alias, now - timedelta(hours=2), 5, revision=1
    )
    writer.retention_days = lambda: 1
    pages = CapturedSourceOutput.pages
    monkeypatch.setattr(
        CapturedSourceOutput, "pages", lambda self: pages(self, max_items=1)
    )

    def run():
        return writer.refresh(
            change_for(alias),
            [definition],
            scope,
            start=now - timedelta(days=3),
            end=now + timedelta(days=1),
            as_of=now,
        )

    if raw_has_before:
        # The raw alias's before row is in a DIFFERENT output subpage from B.
        # Its original page-wide flag must nevertheless guard B's moved start.
        with pytest.raises(refresh.MetricAdmissionUnproven):
            run()
    else:
        # A supplemental canonical dirty nomination must NOT invent raw before.
        manifest = run()
        sql, params = committed_rows_select(
            FIELD_VALUES, manifest, org_id=scope.organization, project_id=scope.project
        )
        assert writer.store.execute(
            f"SELECT unit_id, value_number FROM ({sql}) WHERE is_deleted = 0", params
        ) == [(str(survivor), 5.0)]
