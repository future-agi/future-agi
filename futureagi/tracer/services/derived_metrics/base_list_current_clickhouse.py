"""Inactive current-fact writer using the immutable publication primitives.

No production refresh invokes this foundation. BL01-Q must first implement
byte-bounded member freezing/root derivation and an oversized-row policy, as
well as complete source coverage and freshness qualification. These relations
are NOT a source-coverage receipt; native reads remain the default.
"""

from tracer.services.clickhouse.query_builders.base import _unix_microseconds
from tracer.services.clickhouse.v2.id_remap_sql import remap_left_join, resolved_id_expr
from tracer.services.derived_metrics.publication import (
    MAX_BATCH_ROWS,
    committed_rows_select,
)
from tracer.services.derived_metrics.storage import (
    LIST_CURRENT_MEMBERS,
    LIST_CURRENT_ROOTS,
)

PHYSICAL_KEY = (
    "project_id",
    "observation_type",
    "service_name",
    "start_hour",
    "trace_id",
    "span_id",
)
SOURCE_COLUMNS = (
    "start_time",
    "end_time",
    "parent_span_id",
    "trace_session_id",
    "end_user_id",
    "name",
    "input",
    "cost",
    "total_tokens",
    "prompt_tokens",
    "completion_tokens",
    "attributes_extra",
    "attrs_string",
    "attrs_number",
    "attrs_bool",
    "is_deleted",
)
_REQUIRED_COLUMNS = frozenset(SOURCE_COLUMNS) | {
    "org_id",
    "project_id",
    "observation_type",
    "service_name",
    "trace_id",
    "id",
    "_version",
    "created_at",
    "updated_at",
}


def source_columns_ready(store):
    """Legacy/minimal sources cannot populate a native current index."""
    return _REQUIRED_COLUMNS <= {
        row[0]
        for row in store.execute(
            "SELECT name FROM system.columns WHERE database = currentDatabase() AND table = 'spans'"
        )
    }


def source_select(*, org_id, project_id, keys, as_of):
    """Collapse complete native physical identities before resolving membership.

    The finite trace/span nominee is immutable. No mutable root, user, session or
    request-time predicate is pushed into replacement collapse. Retain one fact
    per root timestamp, never a request-independent first_seen aggregate.
    """
    if not 0 < len(keys) <= MAX_BATCH_ROWS:
        raise ValueError("current source input must fit one publication batch")
    params = {
        "current_org": str(org_id),
        "current_project": str(project_id),
        "current_keys": tuple(keys),
        "current_as_of_us": _unix_microseconds(as_of),
    }
    source = """SELECT *, toStartOfHour(start_time) AS start_hour, id AS span_id
        FROM spans PREWHERE project_id = toUUID(%(current_project)s)
        AND (trace_id, id) IN %(current_keys)s
        WHERE (org_id = toUUID(%(current_org)s) OR isNull(org_id))
        AND created_at <= fromUnixTimestamp64Micro(%(current_as_of_us)s, 'UTC')
        AND updated_at <= fromUnixTimestamp64Micro(%(current_as_of_us)s, 'UTC')"""
    # String JSON is the stable on-disk contract; tuple protects nullable fields.
    values = [
        "toString(attributes_extra)" if c == "attributes_extra" else c
        for c in SOURCE_COLUMNS
    ]
    payload = "tuple(" + ", ".join(values) + ")"
    key = ", ".join(PHYSICAL_KEY)
    ties = f"SELECT count() FROM (SELECT {key}, _version FROM ({source}) GROUP BY {key}, _version HAVING uniqExact({payload}) > 1)"
    unpack = ", ".join(
        f"payload.{i} AS {name}" for i, name in enumerate(SOURCE_COLUMNS, 1)
    )
    latest = f"SELECT {key}, {unpack}, source_version FROM (SELECT {key}, argMax({payload}, _version) AS payload, max(_version) AS source_version FROM ({source}) GROUP BY {key})"
    session = resolved_id_expr("s.trace_session_id", "sm")
    user = resolved_id_expr("s.end_user_id", "um")
    sql = f"""SELECT s.project_id, s.observation_type, s.service_name, s.start_hour,
        s.trace_id, s.span_id, s.start_time AS unit_start, s.end_time, s.parent_span_id,
        ifNull(toString(s.trace_session_id), '') AS raw_session_id,
        ifNull(toString({session}), '') AS canonical_session_id,
        ifNull(toString(s.end_user_id), '') AS raw_user_id,
        ifNull(toString({user}), '') AS canonical_user_id,
        s.name, s.input, s.cost, s.total_tokens, s.prompt_tokens, s.completion_tokens,
        s.attributes_extra, s.attrs_string, s.attrs_number, s.attrs_bool,
        s.source_version, s.is_deleted
        FROM ({latest}) AS s
        {remap_left_join("s.trace_session_id", "trace_session_id_remap", "sm")}
        {remap_left_join("s.end_user_id", "end_user_id_remap", "um")}"""
    return sql, params, ties


def freeze_changes(writer, attempt, head, *, keys, as_of, cutoff=None):
    """Freeze both indices with the SAME attempt, before its first CH INSERT.

    Missing physical identities become tombstones using committed preimages.
    Root-to-child changes retract the root even though the member stays live.
    Replays never query the source again. No incomplete pass grants coverage.
    """
    keys = sorted(set(keys))
    if not keys or not source_columns_ready(writer.store):
        return False
    from tracer.services.derived_metrics.bounded_reads import (
        iter_key_membership_batches,
    )

    pages = []
    # Split the source capture and its prior read together. Splitting only the
    # prior relation would later recombine oversized bindings at freeze time.
    for membership in iter_key_membership_batches(
        LIST_CURRENT_MEMBERS,
        fixed={},
        columns=("trace_id", "span_id"),
        values=keys,
        max_items=MAX_BATCH_ROWS,
    ):
        page = membership[("trace_id", "span_id")]
        sql, params, ties = source_select(
            org_id=writer.org_id,
            project_id=writer.lease.project_id,
            keys=page,
            as_of=as_of,
        )
        # Conservatively reject even historical divergent ties. Do not invent a
        # tie breaker that differs from the native argMax contract.
        if writer.store.execute(ties, params)[0][0]:
            return False
        pages.append((sql, params))
    writer.append(
        attempt,
        "membership_preimage",
        [{"trace_id": trace, "span_id": span} for trace, span in keys],
    )
    from tracer.services.derived_metrics.refresh import payload_select

    for sql, params in pages:
        member_payloads: list = []
        for table in (LIST_CURRENT_MEMBERS, LIST_CURRENT_ROOTS):
            previous, bound = committed_rows_select(
                table,
                head,
                org_id=writer.org_id,
                project_id=writer.lease.project_id,
                key_in={("trace_id", "span_id"): params["current_keys"]},
                param_prefix="current_previous",
            )
            columns = [
                c
                for c in LIST_CURRENT_MEMBERS.columns
                if c not in {"org_id", "generation", "attempt_id", "_version"}
            ]
            if table == LIST_CURRENT_ROOTS:
                # Derive roots from the EXACT frozen member payload, never a
                # second raw read that could observe an arrival/remap mid-plan.
                frozen, frozen_params = payload_select(
                    LIST_CURRENT_MEMBERS, member_payloads, prefix="current_members"
                )
                params.update(frozen_params)
                sql = f"SELECT {', '.join(columns)} FROM ({frozen})"
            live = sql
            if table == LIST_CURRENT_ROOTS:
                live = (
                    "SELECT * REPLACE(toUInt8(is_deleted OR NOT "
                    "(isNull(parent_span_id) OR parent_span_id = '')) AS is_deleted) "
                    f"FROM ({sql}) WHERE isNull(parent_span_id) OR parent_span_id = '' "
                    f"OR ({', '.join(PHYSICAL_KEY)}) IN (SELECT "
                    f"{', '.join(PHYSICAL_KEY)} FROM ({previous}))"
                )
            missing = f"""SELECT {", ".join(columns)} FROM ({previous})
                WHERE (trace_id, span_id) IN %(current_keys)s
                AND ({", ".join(PHYSICAL_KEY)}) NOT IN (
                    SELECT {", ".join(PHYSICAL_KEY)} FROM ({sql}))"""
            combined = f"({live}) UNION ALL (SELECT * REPLACE(toUInt8(1) AS is_deleted) FROM ({missing}))"
            if cutoff is not None:
                # A correction of an EXISTING fact must reach retirement even
                # when its new start expired; dropping it here would leave the
                # prior recent payload current. Unknown expired facts are not
                # admitted. The integrated privacy checkpoint removes proposals
                # that no longer have a retained authoritative owner.
                combined = (
                    f"SELECT * FROM ({combined}) WHERE unit_start >= %(current_cutoff)s"
                    f" OR ({', '.join(PHYSICAL_KEY)}) IN ("
                    f"SELECT {', '.join(PHYSICAL_KEY)} FROM ({previous}))"
                )
                params["current_cutoff"] = cutoff
            frozen = writer.freeze(
                attempt,
                table,
                combined,
                {**params, **bound},
                collect_payloads=table == LIST_CURRENT_MEMBERS,
            )
            if table == LIST_CURRENT_MEMBERS:
                member_payloads = frozen
        from tracer.services.derived_metrics.user_list_current_clickhouse import (
            freeze_user_activity,
        )

        freeze_user_activity(
            writer,
            attempt,
            head,
            member_payloads=member_payloads,
            keys=params["current_keys"],
        )
    return True
