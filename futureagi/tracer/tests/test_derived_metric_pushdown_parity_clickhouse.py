# ruff: noqa: F811 -- shared isolated CH fixture
"""Real-CH immutable pushdown oracles, typed nomination and physical pruning.

Includes real-writer R01 parity. These are not completion evidence for the
retirement, grouped-backfill or all-proposal lifecycle redesign.
"""

import json
import random
from collections import Counter, defaultdict
from contextlib import contextmanager
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
    # Keep ingestion fragmentation deterministic, then qualify the required
    # one-part-per-tuple layout before measuring point-read pruning.
    store.client.execute(f"SYSTEM STOP MERGES {checkpoint.name}")
    # Keep each insert bounded even though the complete fixture has one million
    # independently sorted keys across the eight immutable subpartitions.
    for offset in range(0, 1_000_000, 10_000):
        store.client.execute(
            f"INSERT INTO {checkpoint.name} (org_id, project_id, field_id, field_version, level, unit_id, generation, attempt_id, _version) "
            "SELECT %(org)s, %(project)s, %(field)s, 1, 'conversation', hex(MD5(toString(number))), 1, %(attempt)s, 1 "
            "FROM numbers(%(offset)s, 10000)",
            {**params, "offset": offset},
            settings=WRITE_SETTINGS,
        )
    # A hundred small inserts produce 800 overlapping parts after N=8. With
    # five 256-row marks per small part, scattered probes legitimately read the
    # entire fragmented fixture. Do not disguise that as successful pruning.
    # This is an isolated diagnostic database, not the publication/GC protocol;
    # that protocol and real pins are exercised by checkpoint_partitioning tests.
    store.client.execute(f"SYSTEM START MERGES {checkpoint.name}")
    for part in store.checkpoint_layout(project):
        assert part.active_parts <= 512 and part.bytes_on_disk <= 2 * 1024**3
        if part.active_parts > 1:
            store.optimize_checkpoint(
                part.partition_id, "pruning-layout-" + uuid4().hex, 60
            )
    layout = store.checkpoint_layout(project)
    assert len(layout) == 8 and all(part.active_parts == 1 for part in layout)
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
        from tracer.services.derived_metrics.queries import Scope
        from tracer.services.derived_metrics.unit_reads import _committed_rows_sql

        routed, rp = _committed_rows_sql(
            Scope(org, UUID(int=0), project),
            field,
            1,
            head,
            "unit_id IN %(field_read_units)s",
            checkpoint_unit_ids=ids,
        )
        rp["field_read_units"] = tuple(ids)
        routed_rows, routed_receipt = _read_receipt(
            store, f"SELECT unit_id FROM ({routed})", rp
        )
        assert _result(routed_rows) == _result(new_rows)
        # At most the useful mark and its boundary neighbor for each requested
        # cell, plus one boundary per nonempty tuple; no eightfold id fan-out.
        assert routed_receipt["selected_marks"] <= 2 * len(ids) + 8
        if label == "scattered":
            assert routed_receipt["read_rows"] < new_receipt["read_rows"] / 2
        print(
            json.dumps(
                {
                    "fixture_rows": 1_000_000,
                    "page": label,
                    "broad_baseline_contiguous": broad_receipt,
                    "pushed": new_receipt,
                    "bucket_routed": routed_receipt,
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
    from tracer.models.derived_metric import (
        MetricProjectRefresh,
        MetricRefreshAttempt,
        MetricStateConflict,
    )
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
    freeze = writer.freeze

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
    # Preparation is durable now; an interrupted capture may leave its private
    # attempt, but no frozen output, stageable ledger, publication or cursor.
    failed = MetricRefreshAttempt.objects.get(project_id=scope.project)
    assert failed.state == "prepared" and failed.ledger_sealed_at is None
    assert list(writer.plans(failed)) == [] and not failed.batches.exists()
    state = MetricProjectRefresh.objects.get(project_id=scope.project)
    assert state.head_manifest_id is None and state.watermark_ns == 0
    with pytest.raises(MetricStateConflict, match="ledger is not sealed"):
        writer.stage_and_publish(failed)
    failed.refresh_from_db()
    assert failed.state == "prepared"
    state.refresh_from_db()
    assert state.head_manifest_id is None and state.watermark_ns == 0
    for table in (FIELD_VALUES, BUCKET_TOTALS):
        assert writer.store.execute(f"SELECT count() FROM {table.name}") == [(0,)]

    monkeypatch.setattr(writer.store.client, "execute_iter", stream)
    monkeypatch.setattr(writer, "freeze", freeze)
    head = writer.refresh(
        change_for(unit),
        [definition],
        scope,
        start=now - timedelta(days=1),
        end=now + timedelta(days=1),
        as_of=now,
    )
    failed.refresh_from_db()
    assert failed.state == "aborted"
    assert head.published_by_id != failed.id and failed.id not in head.delta_attempt_ids
    assert not failed.batches.exists()
    state.refresh_from_db()
    assert state.head_manifest_id == head.id and state.watermark_ns == 1
    sql, params = committed_rows_select(
        FIELD_VALUES, head, org_id=scope.organization, project_id=scope.project
    )
    assert writer.store.execute(
        f"SELECT unit_id, value_number FROM ({sql}) WHERE is_deleted = 0", params
    ) == [(str(unit), 1.0)]


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


@pytest.mark.parametrize("seed", [8975, 1102, 3588])
def test_bucket_window_raw_history_parity_and_writer_paths(
    publication_store, monkeypatch, seed
):
    """Late moves both ways, alias tombstones, mixed levels/versions and branches."""
    from tracer.services.derived_metrics.bounded_reads import windowed_rows_select
    from tracer.services.derived_metrics.refresh import RefreshWriter

    store = publication_store
    rng = random.Random(seed)
    org, project, metric = uuid4(), uuid4(), uuid4()
    attempts = [uuid4() for _ in range(4)]
    head = SimpleNamespace(
        project_id=project,
        checkpoint_attempt_id=attempts[0],
        allowed_attempts=lambda: [(1, attempts[0]), (2, attempts[1])],
    )
    keys = list(FIELD_VALUES.logical_key)
    projection = keys + [
        "bucket_start",
        "unit_start",
        "user_id",
        "is_deleted",
        "generation",
        "_version",
    ]
    histories = []
    for branch in range(4):
        data = []
        for i in range(80):
            for revision in range(1, 3):
                # Include a canonical and retired alias key, never group by owner.
                key = f"{'alias' if i % 2 else 'canonical'}-{i:03}"
                at = AT + timedelta(minutes=15 * ((i + branch + revision) % 2))
                row = dict(
                    zip(
                        projection,
                        (
                            org,
                            project,
                            metric,
                            1 + (i % 9 == 0),
                            "conversation" if i % 7 else "other",
                            key,
                            at,
                            at,
                            f"owner-{rng.randrange(3)}",
                            int((branch + i + revision) % 5 == 0),
                            branch + 1,
                            revision,
                        ),
                        strict=True,
                    )
                )
                data.append(tuple(row[c] for c in projection) + (attempts[branch],))
                if branch < 3:
                    histories.append(row)
        table = physical_table(FIELD_VALUES, checkpoint=branch == 0)
        store.client.execute(
            f"INSERT INTO {table.name} ({', '.join(projection)}, attempt_id) VALUES",
            data,
        )
    overlay = (
        "SELECT * FROM metric_field_values WHERE project_id = %(overlay_project)s "
        "AND attempt_id = %(overlay_attempt)s"
    )
    overlay_params = {"overlay_project": project, "overlay_attempt": attempts[2]}
    legacy, legacy_params = committed_rows_select(
        FIELD_VALUES,
        head,
        org_id=org,
        project_id=project,
    )
    legacy = (
        f"SELECT * FROM (({legacy}) UNION ALL ({overlay})) "
        f"ORDER BY generation DESC, _version DESC LIMIT 1 BY {', '.join(keys)}"
    )
    winners = {}
    for row in histories:
        identity = tuple(row[c] for c in keys)
        old = winners.get(identity)
        if old is None or (row["generation"], row["_version"]) > (
            old["generation"],
            old["_version"],
        ):
            winners[identity] = row
    for at in (AT, AT + timedelta(minutes=15)):
        sql, params = windowed_rows_select(
            FIELD_VALUES,
            head,
            org_id=org,
            project_id=project,
            key_in={"field_id": (metric,), "field_version": (1,)},
            candidate_columns=("level", "unit_id"),
            predicate="bucket_start = %(at)s",
            params={"at": at},
            frozen_overlay=(overlay, overlay_params),
        )
        expected = _result(
            [
                tuple(row[c] for c in projection)
                for row in winners.values()
                if row["bucket_start"] == at and row["field_version"] == 1
            ]
        )
        assert (
            _result(
                store.execute(f"SELECT {', '.join(projection)} FROM ({sql})", params)
            )
            == expected
        )
        assert (
            _result(
                store.execute(
                    f"SELECT {', '.join(projection)} FROM ({legacy}) WHERE bucket_start = %(at)s AND field_version = 1",
                    merge_bindings(legacy_params, overlay_params, {"at": at}),
                )
            )
            == expected
        )

    # Exercise the actual writer entry, including the no-collapse complete overlay
    # and retired-override path, rather than testing only the helper's SQL.
    writer = RefreshWriter(
        SimpleNamespace(project_id=project, keepalive=lambda: None), store, org_id=org
    )
    attempt = SimpleNamespace(
        purpose="incremental",
        source_ceilings={"retention_floor": (AT - timedelta(days=1)).isoformat()},
        ledger_chunks=SimpleNamespace(
            filter=lambda **_: [
                SimpleNamespace(payload=[{"bucket_start": AT.isoformat()}]),
            ]
        ),
    )
    definition = SimpleNamespace(metric_id=metric, version=1)
    captured = []

    @contextmanager
    def capture(_attempt, _definition, fields, retired, params):
        captured.append(
            _result(
                [
                    row
                    for page, bound in fields.scan(store)
                    for row in store.execute(
                        f"SELECT {', '.join(projection)} FROM ({page})", bound
                    )
                ]
            )
        )
        assert store.execute(f"SELECT count() FROM ({retired})", params) == [(0,)]
        yield "SELECT * FROM metric_bucket_totals WHERE 0"

    monkeypatch.setattr(writer, "_anonymous_bucket_parts", capture)
    monkeypatch.setattr(writer, "append", lambda *a, **k: None)
    monkeypatch.setattr(writer, "freeze", lambda *a, **k: [])
    for complete in (False, True):
        writer._freeze_buckets(
            attempt,
            head,
            definition,
            overlay,
            overlay_params,
            complete_fields=complete,
            retired_override=("SELECT * FROM metric_bucket_retired_parts WHERE 0", {}),
        )
        reference = overlay if complete else legacy
        assert captured[-1] == _result(
            store.execute(
                f"SELECT {', '.join(projection)} FROM ({reference}) WHERE bucket_start = %(at)s AND field_version = 1",
                merge_bindings(legacy_params, overlay_params, {"at": AT}),
            )
        )


def test_bucket_window_memory_excludes_outside_units(
    publication_store, record_property
):
    """Small local diagnostic, not the required 10M M2 qualification."""
    from tracer.services.derived_metrics.bounded_reads import windowed_rows_select

    store = publication_store
    org, project, metric, attempt = uuid4(), uuid4(), uuid4(), uuid4()
    head = SimpleNamespace(
        project_id=project,
        checkpoint_attempt_id=attempt,
        allowed_attempts=lambda: [(1, attempt)],
    )
    store.client.execute(
        """INSERT INTO metric_field_values_checkpoint
        (org_id, project_id, field_id, field_version, level, unit_id, bucket_start,
         value_text, generation, attempt_id, _version)
        SELECT %(org)s, %(project)s, %(metric)s, 1, 'conversation', toString(number),
            toDateTime(%(at)s, 'UTC') + if(number < 64, 0, 900),
            repeat('evidence', 64), 1, %(attempt)s, 1 FROM numbers(32768)""",
        {
            "org": org,
            "project": project,
            "metric": metric,
            "at": AT,
            "attempt": attempt,
        },
    )
    broad, bp = committed_rows_select(
        FIELD_VALUES, head, org_id=org, project_id=project
    )
    pushed, pp = windowed_rows_select(
        FIELD_VALUES,
        head,
        org_id=org,
        project_id=project,
        key_in={"field_id": (metric,), "field_version": (1,)},
        candidate_columns=("level", "unit_id"),
        predicate="bucket_start = %(at)s",
        params={"at": AT},
    )
    # Consume payload to prevent count-only optimizations from hiding collapse memory.
    projection = "SELECT sum(length(value_text)) FROM ({})"
    old, old_receipt = _read_receipt(
        store,
        projection.format(f"SELECT * FROM ({broad}) WHERE bucket_start = %(at)s"),
        {**bp, "at": AT},
    )
    new, new_receipt = _read_receipt(store, projection.format(pushed), pp)
    assert old == new == [(64 * 8 * 64,)]
    assert new_receipt["memory_bytes"] < old_receipt["memory_bytes"]
    assert (
        new_receipt["memory_bytes"] <= WRITE_SETTINGS["max_memory_usage"] < 2 * 1024**3
    )
    record_property(
        "window_memory", json.dumps({"broad": old_receipt, "pushed": new_receipt})
    )


def test_span_window_nomination_preserves_broad_compiler_and_expiry(
    publication_store, monkeypatch
):
    from tracer.services.derived_metrics import unit_reads
    from tracer.services.derived_metrics.queries import Scope
    from tracer.services.derived_metrics.retention import _expired_span_buckets

    store = publication_store
    org, project, session, user = uuid4(), uuid4(), uuid4(), uuid4()
    scope = Scope(org, uuid4(), project)
    rng = random.Random(11028975)
    definition = SimpleNamespace(
        level="span",
        kind="average",
        selection_unit="span",
        formula="mean(y)",
        inputs=[
            {
                "alias": "y",
                "source": "span",
                "field": "attribute_number",
                "attribute_key": "y",
                "reducer": "latest",
            }
        ],
        field_spec={},
        weighting="none",
        cohort_key="",
        filters=[],
    )
    rows, dependencies = [], defaultdict(list)
    attempts = [uuid4(), uuid4()]
    head = SimpleNamespace(
        project_id=project,
        checkpoint_attempt_id=attempts[0],
        allowed_attempts=lambda: [(1, attempts[0]), (2, attempts[1])],
    )
    for i in range(64):
        for revision in (1, 2, 3):
            at = AT + timedelta(minutes=15 * ((i + revision) % 3))
            deleted = int((i + revision) % 11 == 0)
            # Repeated span IDs under different trace IDs exercise ambiguity.
            rows.append(
                (
                    org,
                    project,
                    f"trace-{i}",
                    f"span-{i % 3}",
                    session,
                    user,
                    at,
                    at + timedelta(seconds=1),
                    at,
                    at,
                    revision,
                    deleted,
                    {"y": float(rng.randrange(20))},
                )
            )
            generation = 1 if revision == 1 else 2
            dependencies[generation].append(
                (
                    org,
                    project,
                    f"trace-{i}",
                    f"span-{i % 3}",
                    at,
                    deleted,
                    generation,
                    attempts[generation - 1],
                    revision,
                )
            )
    store.client.execute(
        "INSERT INTO spans (org_id, project_id, trace_id, id, trace_session_id, end_user_id, "
        "start_time, end_time, created_at, updated_at, _version, is_deleted, attrs_number) VALUES",
        rows,
    )
    for rows in dependencies.values():
        store.client.execute(
            "INSERT INTO metric_source_dependencies (org_id, project_id, trace_id, span_id, "
            "unit_start, is_deleted, generation, attempt_id, _version) VALUES",
            rows,
        )
    original = unit_reads.snapshot_select

    def broad(*args, **kwargs):
        kwargs.pop("unit_predicate", None)
        kwargs.pop("source_ctes", None)
        return original(*args, **kwargs)

    for minute in (0, 15, 30):
        start = AT + timedelta(minutes=minute)
        end = start + timedelta(minutes=15)
        sql, params = unit_reads.span_values_select(
            definition,
            scope,
            start=start,
            end=end,
            as_of=AT + timedelta(days=1),
        )
        actual = _result(store.execute(sql, params))
        with monkeypatch.context() as patch:
            patch.setattr(unit_reads, "snapshot_select", broad)
            legacy, lp = unit_reads.span_values_select(
                definition,
                scope,
                start=start,
                end=end,
                as_of=AT + timedelta(days=1),
            )
        assert actual == _result(store.execute(legacy, lp))
        dep_sql, dp = committed_rows_select(
            SOURCE_DEPENDENCIES,
            head,
            org_id=org,
            project_id=project,
        )
        expected = {
            r[0]
            for r in store.execute(
                f"SELECT DISTINCT toStartOfFifteenMinutes(unit_start) FROM ({dep_sql}) WHERE unit_start < %(cutoff)s",
                {**dp, "cutoff": end},
            )
        }
        writer = SimpleNamespace(
            store=store, org_id=org, lease=SimpleNamespace(project_id=project)
        )
        assert _expired_span_buckets(writer, head, end) == expected


@pytest.mark.parametrize("deleted", [False, True])
@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("same_trace", [False, True])
def test_span_window_preserves_retargeted_evaluation_winner(
    publication_store, monkeypatch, deleted, reverse, same_trace
):
    """Window nomination must not resurrect an evaluation's old target revision."""
    from tracer.services.derived_metrics import unit_reads
    from tracer.services.derived_metrics.queries import Scope
    from tracer.tests.test_derived_metric_clickhouse import seed_eval

    store = publication_store
    org, project, config, evaluation, session = (uuid4() for _ in range(5))
    scope = Scope(org, uuid4(), project)
    traces = [uuid4(), uuid4()]
    if same_trace:
        traces[1] = traces[0]
    definition = SimpleNamespace(
        level="span",
        kind="average",
        selection_unit="span",
        formula="mean(y)",
        inputs=[
            {
                "alias": "y",
                "source": "eval",
                "config_id": str(config),
                "config_hash": "a" * 64,
                "reducer": "latest",
            }
        ],
        field_spec={},
        weighting="none",
        cohort_key="",
        filters=[],
    )
    # Keep both physical evaluation revisions so an incidental merge cannot hide
    # a pre-collapse membership bug. Both spans belong to one session; only the
    # evaluation's target identity changes, in either direction across a bucket.
    store.client.execute("SYSTEM STOP MERGES tracer_eval_logger_v2")
    try:
        for index, trace in enumerate(traces):
            at = AT + timedelta(minutes=15 * index)
            store.client.execute(
                "INSERT INTO spans (id, project_id, org_id, trace_id, trace_session_id, "
                "start_time, end_time, created_at, updated_at) VALUES",
                [
                    (
                        f"span-{index}",
                        project,
                        org,
                        str(trace),
                        session,
                        at,
                        at + timedelta(seconds=1),
                        AT,
                        AT,
                    )
                ],
            )
        old, new = (1, 0) if reverse else (0, 1)
        for revision, target, value, tombstone in (
            (1, old, 7.0, False),
            (2, new, 9.0, deleted),
        ):
            seed_eval(
                store.client,
                config,
                session,
                AT + timedelta(minutes=1),
                value,
                1,
                span_id=f"span-{target}",
                trace_id=traces[target],
                row_id=evaluation,
                version=revision,
                deleted=int(tombstone),
            )
        original = unit_reads.snapshot_select

        def broad(*args, **kwargs):
            kwargs.pop("unit_predicate", None)
            kwargs.pop("source_ctes", None)
            return original(*args, **kwargs)

        for index in (0, 1):
            start = AT + timedelta(minutes=15 * index)
            arguments = {
                "start": start,
                "end": start + timedelta(minutes=15),
                "as_of": AT + timedelta(days=1),
            }
            sql, params = unit_reads.span_values_select(definition, scope, **arguments)
            actual = _result(store.execute(sql, params))
            with monkeypatch.context() as patch:
                patch.setattr(unit_reads, "snapshot_select", broad)
                legacy, lp = unit_reads.span_values_select(
                    definition, scope, **arguments
                )
            assert actual == _result(store.execute(legacy, lp))
            expected = (
                (9.0, "ok", "")
                if index == new and not deleted
                else (None, "no_value", "not_evaluated")
            )
            assert store.execute(
                f"SELECT value_number, status, no_value_reason FROM ({sql})", params
            ) == [expected]
    finally:
        store.client.execute("SYSTEM START MERGES tracer_eval_logger_v2")


def _numbered_fields(count, *, text_bytes=0, uuid_units=False):
    from tracer.tests.test_derived_metric_publication_clickhouse import field_query

    sql, params = field_query(metric=uuid4())
    unit = (
        "toString(reinterpretAsUUID(MD5(toString(number))))"
        if uuid_units
        else "concat('unit-', leftPad(toString(number), 9, '0'))"
    )
    sql = sql.replace("%(test_unit)s", unit)
    if text_bytes:
        sql = sql.replace("%(test_group)s", "repeat('x', %(text_bytes)s)")
        params["text_bytes"] = text_bytes
    return sql + " FROM numbers(%(fixture_count)s)", {**params, "fixture_count": count}


def _field_payload_bytes(writer, manifest):
    sql, params = committed_rows_select(
        FIELD_VALUES,
        manifest,
        org_id=writer.org_id,
        project_id=writer.lease.project_id,
    )
    stamp = {"_version", "generation", "attempt_id", "fencing_epoch", "row_sequence"}
    columns = ", ".join(c for c in FIELD_VALUES.columns if c not in stamp)
    return writer.store.execute(
        f"SELECT hex(formatRow('RowBinary', {columns})) FROM ({sql}) ORDER BY unit_id",
        params,
    )


@pytest.mark.django_db(transaction=True)
def test_large_checkpoint_crash_replay_byte_parity_and_partition_overlap(
    live_writer, monkeypatch
):
    from tracer.models.derived_metric import MetricProjectRefresh, MetricRefreshAttempt
    from tracer.services.derived_metrics.publication import MAX_BATCH_ROWS
    from tracer.services.derived_metrics.storage import FIELD_CHECKPOINT

    writer = live_writer
    sql, params = _numbered_fields(1201)
    initial = writer.begin()
    writer.freeze(initial, FIELD_VALUES, sql, params)
    writer.seal(initial)
    head = writer.stage_and_publish(initial)
    expected = _field_payload_bytes(writer, head)
    original_insert = writer.store.insert

    def lost_ack(table, payload):
        original_insert(table, payload)
        if table == FIELD_CHECKPOINT:
            raise RuntimeError("lost checkpoint response")

    with monkeypatch.context() as patch:
        patch.setattr(writer.store, "insert", lost_ack)
        with pytest.raises(RuntimeError, match="lost checkpoint"):
            writer.compact()
    attempt = MetricRefreshAttempt.objects.get(
        project_id=writer.lease.project_id, state="staging"
    )
    plans = list(writer.plans(attempt, kind="field_values"))
    assert len(plans) == 1 and len(plans[0]["rows"]) == 1201 > MAX_BATCH_ROWS
    assert (
        MetricProjectRefresh.objects.get(
            project_id=writer.lease.project_id
        ).head_manifest_id
        == head.id
    )
    # Force the retry to coexist physically with the ambiguous first INSERT.
    writer.store.client.execute(f"SYSTEM STOP MERGES {FIELD_CHECKPOINT.name}")
    try:
        from tracer.services.derived_metrics.publication import FrozenRows

        original_insert(FIELD_CHECKPOINT, FrozenRows.from_plan(plans[0]))
        assert any(
            part.active_parts >= 2
            for part in writer.store.checkpoint_layout(writer.lease.project_id)
        )
        original_consolidate = writer.store.consolidate_checkpoint
        overlaps = []

        def consolidate(project, *, deadline=None):
            overlaps.append(
                sum(p.active_parts for p in writer.store.checkpoint_layout(project))
            )
            original_consolidate(project, deadline=deadline)

        with monkeypatch.context() as patch:
            patch.setattr(writer.store, "consolidate_checkpoint", consolidate)
            # OPTIMIZE can execute an explicit merge while background merges are stopped.
            writer.store.client.execute(f"SYSTEM START MERGES {FIELD_CHECKPOINT.name}")
            from tracer.models.derived_metric import MetricErasureRequest
            from tracer.services.derived_metrics.publication import (
                MetricCheckpointLayoutPending,
            )

            with pytest.raises(MetricCheckpointLayoutPending):
                writer.stage_and_publish(attempt)
            # The second INSERT's success does NOT drain the first submission.
            # This harness saw original_insert return synchronously before the
            # injected crash, so simulate that transport owner's late durable
            # acknowledgement. Production cannot infer this from empty queries.
            old_submission = MetricErasureRequest.objects.get(
                store_payload__protocol="metric_submission",
                store_payload__attempt=str(attempt.id),
                status="queued",
            )
            MetricErasureRequest.objects.filter(id=old_submission.id).update(
                status="done",
                completed_at=writer.lease.clock(),
                receipts={"synchronous_response": {"drained": 1}},
            )
            compacted = writer.stage_and_publish(attempt)
        assert _field_payload_bytes(writer, compacted) == expected
        layout = writer.store.checkpoint_layout(writer.lease.project_id)
        assert layout and all(part.active_parts == 1 for part in layout)
        assert overlaps and overlaps[0] >= 1
        # Compare with the previous 500-row compactor, not just a logical-value oracle.
        legacy = writer.begin(purpose="compaction")
        writer.freeze_current(legacy, FIELD_VALUES, compacted)
        writer.seal(legacy)
        assert all(len(p["rows"]) <= MAX_BATCH_ROWS for p in writer.plans(legacy))
        legacy_head = writer.stage_and_publish(legacy)
        assert _field_payload_bytes(writer, legacy_head) == expected
    finally:
        writer.store.client.execute(f"SYSTEM START MERGES {FIELD_CHECKPOINT.name}")


@pytest.mark.django_db(transaction=True)
def test_checkpoint_empty_ranges_advance_and_byte_splits_keep_every_key(
    live_writer, monkeypatch
):
    from tracer.services.derived_metrics import refresh
    from tracer.services.derived_metrics.publication import (
        MAX_COMPACTION_BYTES,
        MAX_COMPACTION_ROWS,
    )

    writer = live_writer
    initial = writer.begin()
    sql, params = _numbered_fields(6)
    writer.freeze(initial, FIELD_VALUES, sql, params)
    writer.seal(initial)
    head = writer.stage_and_publish(initial)
    nominate = refresh.committed_key_ranges
    visited = []

    def small_ranges(*args, **kwargs):
        kwargs["target_rows"] = 2
        for relation in nominate(*args, **kwargs):
            visited.append(relation)
            yield relation

    with monkeypatch.context() as patch:
        patch.setattr(refresh, "committed_key_ranges", small_ranges)
        checkpoint = writer.begin(purpose="compaction")
        writer.freeze_current(
            checkpoint,
            FIELD_VALUES,
            head,
            large_pages=True,
            where="unit_id >= 'unit-000000004'",
        )
        writer.seal(checkpoint)
    result = writer.stage_and_publish(checkpoint)
    assert len(visited) == 3
    assert len(_field_payload_bytes(writer, result)) == 2
    # Force the byte bound, not the row bound, to end several transport pages.
    large = writer.begin(purpose="compaction")
    sql, params = _numbered_fields(1201, text_bytes=20_000)
    writer.freeze(large, FIELD_VALUES, sql, params, row_limit=MAX_COMPACTION_ROWS)
    plans = list(writer.plans(large))
    assert len(plans) >= 2
    assert sum(len(p["rows"]) for p in plans) == 1201
    assert all(
        sum(len(r) // 2 for r in p["rows"]) <= MAX_COMPACTION_BYTES for p in plans
    )
    writer.seal(large)
    original_insert = writer.store.insert
    inserted = []

    def lose_middle_range_response(table, payload):
        original_insert(table, payload)
        inserted.append(payload.digest)
        if len(inserted) == 2:
            raise RuntimeError("lost response inside byte-split range")

    with monkeypatch.context() as patch:
        patch.setattr(writer.store, "insert", lose_middle_range_response)
        with pytest.raises(RuntimeError, match="inside byte-split range"):
            writer.stage_and_publish(large)
    assert inserted == [p["sha256"] for p in plans[:2]]
    with monkeypatch.context() as patch:
        # Replay must never reread the source, even after an ambiguous INSERT.
        patch.setattr(
            writer.store,
            "freeze_select",
            lambda *a, **k: pytest.fail("recomputed frozen range"),
        )
        replayed = writer.stage_and_publish(large)
    assert len(_field_payload_bytes(writer, replayed)) == 1201


@pytest.mark.parametrize("seed", [17, 41, 93])
def test_bounded_eval_histories_random_retargets_match_full_history_oracle(
    publication_store, monkeypatch, seed
):
    from tracer.services.derived_metrics import unit_reads
    from tracer.services.derived_metrics.queries import Scope
    from tracer.tests.test_derived_metric_clickhouse import seed_eval

    store = publication_store
    rng = random.Random(seed)
    org, project, session, config = (uuid4() for _ in range(4))
    scope = Scope(org, uuid4(), project)
    traces = [UUID(int=rng.getrandbits(128)) for _ in range(12)]
    definition = SimpleNamespace(
        level="span",
        kind="average",
        selection_unit="span",
        formula="mean(y)",
        inputs=[
            {
                "alias": "y",
                "source": "eval",
                "config_id": str(config),
                "config_hash": "a" * 64,
                "reducer": "latest",
            }
        ],
        field_spec={},
        weighting="none",
        cohort_key="",
        filters=[],
    )
    store.client.execute("SYSTEM STOP MERGES tracer_eval_logger_v2")
    try:
        for index, trace in enumerate(traces):
            at = AT + timedelta(minutes=15 * (index % 3))
            store.client.execute(
                "INSERT INTO spans (id, project_id, org_id, trace_id, trace_session_id, "
                "start_time, end_time, created_at, updated_at) VALUES",
                [
                    (
                        f"span-{index}",
                        project,
                        org,
                        str(trace),
                        session,
                        at,
                        at + timedelta(seconds=1),
                        AT,
                        AT,
                    )
                ],
            )
        winners = {}
        for index in range(30):
            evaluation = UUID(int=rng.getrandbits(128))
            created = AT + timedelta(seconds=index)
            for revision in range(1, rng.randrange(2, 6)):
                target = rng.randrange(len(traces))
                deleted = rng.random() < 0.2
                value = float(index * 10 + revision)
                seed_eval(
                    store.client,
                    config,
                    session,
                    created,
                    value,
                    1,
                    span_id=f"span-{target}",
                    trace_id=traces[target],
                    row_id=evaluation,
                    version=revision,
                    deleted=int(deleted),
                )
            if not deleted:
                # Distinct created_at orders the latest evaluation per target.
                winners[target] = value
        original = unit_reads.snapshot_select

        def broad(*args, **kwargs):
            kwargs.pop("unit_predicate", None)
            kwargs.pop("source_ctes", None)
            return original(*args, **kwargs)

        for bucket in range(3):
            arguments = {
                "start": AT + timedelta(minutes=15 * bucket),
                "end": AT + timedelta(minutes=15 * (bucket + 1)),
                "as_of": AT + timedelta(days=1),
            }
            sql, params = unit_reads.span_values_select(definition, scope, **arguments)
            assert "eval_candidate_ids AS" in sql and "span_window_keys AS" in sql
            actual = store.execute(f"SELECT unit_id, value_number FROM ({sql})", params)
            assert dict(actual) == {
                json.dumps(
                    [str(traces[i]), f"span-{i}"], separators=(",", ":")
                ): winners.get(i)
                for i in range(12)
                if i % 3 == bucket
            }
            with monkeypatch.context() as patch:
                patch.setattr(unit_reads, "snapshot_select", broad)
                legacy, bound = unit_reads.span_values_select(
                    definition, scope, **arguments
                )
            assert _result(store.execute(sql, params)) == _result(
                store.execute(legacy, bound)
            )
    finally:
        store.client.execute("SYSTEM START MERGES tracer_eval_logger_v2")


def test_conversation_source_trace_index_pushdown_receipt(publication_store):
    """Measure the current 500-owner source compiler, not a bucket-only proxy."""
    from tracer.services.derived_metrics.queries import Scope
    from tracer.services.derived_metrics.unit_reads import unit_values_select

    store = publication_store
    # Mirror the production sorting/primary key and immutable trace skip index.
    # Its mutable leading components cannot safely constrain winner collapse.
    ddl = store.client.execute("SHOW CREATE TABLE spans")[0][0]
    store.client.execute("DROP TABLE spans SYNC")
    # The fixture has exactly one closing column-list delimiter before ENGINE.
    position = ddl.index(")\nENGINE")
    ddl = (
        ddl[:position]
        + ", observation_type LowCardinality(String) DEFAULT 'GENERATION', INDEX idx_trace_id trace_id TYPE bloom_filter(0.001) GRANULARITY 1\n"
        + ddl[position:]
    )
    ddl = ddl.replace(
        "ORDER BY (project_id, start_time, trace_id, id)",
        "PRIMARY KEY (project_id, observation_type, service_name, toStartOfHour(start_time)) ORDER BY (project_id, observation_type, service_name, toStartOfHour(start_time), trace_id, id)",
    )
    store.client.execute(ddl)
    org, project = uuid4(), uuid4()
    scope = Scope(org, uuid4(), project)
    store.client.execute(
        "INSERT INTO spans (org_id, project_id, id, trace_id, trace_session_id, start_time, end_time, created_at, updated_at, attrs_number) "
        "SELECT %(org)s, %(project)s, toString(number), leftPad(toString(number), 12, '0'), reinterpretAsUUID(MD5(toString(number))), "
        "%(at)s, %(at)s, %(at)s, %(at)s, map('resolved', toFloat64(number %% 7)) FROM numbers(262144)",
        {"org": org, "project": project, "at": AT},
        settings=WRITE_SETTINGS,
    )
    ids = [
        str(r[0])
        for r in store.execute(
            "SELECT trace_session_id FROM spans ORDER BY trace_id LIMIT 500"
        )
    ]
    definition = SimpleNamespace(
        level="conversation",
        kind="average",
        selection_unit="session",
        formula="mean(y)",
        inputs=[
            {
                "alias": "y",
                "source": "span",
                "field": "attribute_number",
                "attribute_key": "resolved",
                "reducer": "sum",
                "value_type": "number",
            }
        ],
        field_spec={},
        weighting="none",
        cohort_key="",
        filters=[],
    )
    sql, params = unit_values_select(
        definition,
        scope,
        ids,
        start=AT,
        end=AT + timedelta(days=1),
        as_of=AT + timedelta(days=1),
    )
    scalar = "trace_id IN (SELECT trace_key FROM unit_source_keys) AND "
    assert scalar not in sql
    old, before = _read_receipt(
        store, sql.replace("(trace_id, id) IN", scalar + "(trace_id, id) IN"), params
    )
    actual, after = _read_receipt(store, sql, params)
    assert _result(actual) == _result(old)
    assert len(actual) == 500
    assert after["read_rows"] <= before["read_rows"]
    assert after["memory_bytes"] <= WRITE_SETTINGS["max_memory_usage"]
    explain = store.execute(f"EXPLAIN indexes=1 {sql}", params)
    print(
        json.dumps(
            {
                "measurement": "500_owner_source",
                "fixture_rows": 262144,
                "before": before,
                "after": after,
                "explain": [r[0] for r in explain],
            }
        )
    )


@pytest.mark.benchmark
@pytest.mark.django_db(transaction=True)
def test_checkpoint_throughput_receipt(live_writer):
    """Opt-in real compaction through seal, publication, consolidation and GC."""
    import os
    from hashlib import sha256
    from time import monotonic

    from tracer.services.derived_metrics.publication import MAX_COMPACTION_ROWS
    from tracer.services.derived_metrics.storage import FIELD_CHECKPOINT

    count = int(os.environ.get("PERF1_COMPACTION_FIXTURE_ROWS", "1000000"))
    assert 50_000 <= count <= 10_000_000
    writer = live_writer
    initial = writer.begin(purpose="compaction")
    sql, params = _numbered_fields(count, uuid_units=True)
    writer.freeze(
        initial,
        FIELD_VALUES,
        sql,
        params,
        row_limit=MAX_COMPACTION_ROWS,
        collect_payloads=False,
    )
    writer.seal(initial)
    writer.stage_and_publish(initial)
    stamp = {"_version", "generation", "attempt_id", "fencing_epoch", "row_sequence"}
    columns = ", ".join(c for c in FIELD_VALUES.columns if c not in stamp)

    def digest(attempt):
        result = sha256()
        rows = 0
        for row in writer.store.client.execute_iter(
            f"SELECT formatRow('RowBinary', {columns}) FROM {FIELD_CHECKPOINT.name} "
            "WHERE project_id = %(project)s AND attempt_id = %(attempt)s "
            "ORDER BY org_id, project_id, field_id, field_version, level, unit_id",
            {"project": writer.lease.project_id, "attempt": attempt.id},
            settings={**WRITE_SETTINGS, "strings_as_bytes": True},
        ):
            result.update(row[0])
            rows += 1
        return rows, result.hexdigest()

    expected = digest(initial)
    assert expected[0] == count
    prefix = "perf1r5_compact_" + uuid4().hex
    original = writer.store.execute
    calls = []

    def execute(sql, params=None, **kwargs):
        qid = prefix + "_" + str(len(calls))
        calls.append(qid)
        return writer.store.client.execute(
            sql, params or {}, settings=WRITE_SETTINGS, query_id=qid, **kwargs
        )

    writer.store.execute = execute
    started = monotonic()
    try:
        manifest = writer.compact()
    finally:
        elapsed = monotonic() - started
        writer.store.execute = original
    assert digest(manifest.published_by) == expected
    writer.store.client.execute("SYSTEM FLUSH LOGS")
    receipt = writer.store.execute(
        "SELECT count(), sum(read_rows), max(memory_usage), sum(query_duration_ms) FROM system.query_log "
        "WHERE startsWith(query_id, %(prefix)s) AND type = 'QueryFinish'",
        {"prefix": prefix},
    )[0]
    assert receipt[0] == len(calls)
    assert receipt[2] <= WRITE_SETTINGS["max_memory_usage"]
    layout = writer.store.checkpoint_layout(writer.lease.project_id)
    assert layout and all(part.active_parts == 1 for part in layout)
    from tracer.services.derived_metrics.queries import Scope
    from tracer.services.derived_metrics.unit_reads import committed_unit_values_select

    ids = [
        row[0]
        for row in writer.store.execute(
            f"SELECT unit_id FROM {FIELD_CHECKPOINT.name} "
            "WHERE project_id = %(project)s AND attempt_id = %(attempt)s "
            "ORDER BY unit_id LIMIT 50",
            {
                "project": str(writer.lease.project_id),
                "attempt": str(manifest.published_by_id),
            },
        )
    ]
    assert len(ids) == 50
    hydration, bindings = committed_unit_values_select(
        Scope(writer.org_id, writer.workspace_id, writer.lease.project_id),
        UUID(params["test_metric"]),
        1,
        ids,
        manifest,
    )
    warm = []
    for _ in range(10):
        started = monotonic()
        assert len(writer.store.execute(hydration, bindings)) == 50
        warm.append(monotonic() - started)
    m7_warm_p95 = sorted(warm)[-1]
    print(
        json.dumps(
            {
                "measurement": "real_compaction",
                "m7_50_cell_warm_p95_seconds": m7_warm_p95,
                "fixture_rows": count,
                "seconds": elapsed,
                "rows_per_second": count / elapsed,
                "estimated_10m_seconds_linear_not_measurement": elapsed
                * 10_000_000
                / count,
                "statements": receipt[0],
                "read_rows": receipt[1],
                "max_statement_memory_bytes": receipt[2],
                "query_ms": receipt[3],
                "payload_sha256": expected[1],
            }
        )
    )
