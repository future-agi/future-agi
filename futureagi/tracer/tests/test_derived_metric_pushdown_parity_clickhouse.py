# ruff: noqa: F811 -- shared isolated CH fixture
"""Real-CH immutable pushdown oracles, typed nomination and physical pruning.

These are reader primitives, not completion evidence for the full refresh,
retirement, grouped-backfill or all-proposal lifecycle redesign.
"""

import json
import random
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from time import sleep
from types import SimpleNamespace
from uuid import UUID, uuid4

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
