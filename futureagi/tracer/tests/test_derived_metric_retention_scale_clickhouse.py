# ruff: noqa: F811 -- shared isolated CH/PG fixtures
"""Finite-retention refresh over more than a million retained committed fields.

Every finite-retention refresh plans retirement over the WHOLE proposal: the
committed head (all definitions, versions and tombstones) plus the draft. No
pass-scoped capture may copy that head, or the project stops refreshing once
its retained history outgrows a capture budget (FrozenRelationOverflow ->
metric_refresh_failed with backoff on every pass). Conversation-only and mixed
span/conversation heads, on ClickHouse 25.3 (analyzer on) and 25.8.
"""

import json
import os
from datetime import timedelta
from time import monotonic
from uuid import uuid4

import pytest
from django.utils import timezone

from tracer.models.derived_metric import MetricProjectRefresh, MetricRefreshAttempt
from tracer.services.derived_metrics.changes import Dependency
from tracer.services.derived_metrics.publication import (
    MAX_COMPACTION_ROWS,
    WRITE_SETTINGS,
    ClickHousePublicationStore,
    committed_rows_select,
)
from tracer.services.derived_metrics.queries import Scope
from tracer.services.derived_metrics.storage import FIELD_VALUES
from tracer.tests.test_derived_metric_publication_clickhouse import (  # noqa: F401
    live_writer,
    metric_ch,
    publication_store,
)
from tracer.tests.test_derived_metric_refresh_clickhouse import (
    change_for,
    definition_for,
    metric_bucket_rows,
    seed_span_revision,
    span_definition_for,
)

# Above every capture budget (CAPTURE_ROWS = 1,000,000 rows; the 128 MiB byte
# budget trips near 400k retained field rows).
FIELD_ROWS = int(os.environ.get("RETENTION_SCALE_FIELD_ROWS", "1000100"))
SPANS_PER_CONVERSATION = 19
RETENTION_DAYS = 90
# Statements carrying inline frozen batches are bounded by one batch; nothing
# in this pass may scale with the size of the retained head.
MAX_STATEMENT_BYTES = 1_000_000


def _numbered_fields(definition, *, level, count, now):
    """`count` retained, finalized field rows, 2 to 32 days old.

    Span i belongs to conversation i // 19 and shares its start, so every
    span owner is a retained conversation of the head.
    """
    unit = f"concat('scale-{level}-', leftPad(toString(number), 9, '0'))"
    per = 1 if level == "conversation" else SPANS_PER_CONVERSATION
    session = (
        unit
        if level == "conversation"
        else "concat('scale-conversation-', leftPad(toString(intDiv(number, "
        f"{per})), 9, '0'))"
    )
    start = (
        "toDateTime64(%(scale_now)s, 6, 'UTC') - toIntervalSecond(172800 + "
        f"intDiv(number, {per}) %% 2592000)"
    )
    replacements = {
        "field_id": "toUUID(%(scale_metric)s)",
        "field_version": f"toUInt32({definition.version})",
        "level": f"'{level}'",
        "unit_id": unit,
        "session_id": session,
        "user_id": "''",
        "unit_start": start,
        "bucket_start": f"toStartOfFifteenMinutes(toDateTime({start}, 'UTC'))",
        "status": "'ok'",
        "value_number": "toNullable(toFloat64(number %% 7))",
        "part_n": "toNullable(toFloat64(number %% 7))",
        "part_d": "toNullable(toFloat64(1))",
        "inputs": "map('resolved', toNullable(toFloat64(number %% 7)))",
        "sample_probability": "toNullable(toFloat64(1))",
        "sampling_design": "'live'",
        "group_key": "'west'",
        "is_deleted": "toUInt8(0)",
    }
    columns = []
    for name, kind in FIELD_VALUES.schema:
        expr = replacements.get(
            name,
            f"defaultValueOfTypeName('{kind.replace(chr(39), chr(92) + chr(39))}')",
        )
        columns.append(f"{expr} AS {name}")
    return (
        "SELECT " + ", ".join(columns) + " FROM numbers(%(scale_count)s)",
        {
            "scale_metric": str(definition.metric_id),
            "scale_now": now,
            "scale_count": count,
        },
    )


def _seed_head(writer, layout, now):
    """One published compaction checkpoint; 50k-row pages, ~20 statements."""
    attempt = writer.begin(purpose="compaction")
    for definition, level, count in layout:
        sql, params = _numbered_fields(definition, level=level, count=count, now=now)
        writer.freeze(
            attempt,
            FIELD_VALUES,
            sql,
            params,
            row_limit=MAX_COMPACTION_ROWS,
            collect_payloads=False,
        )
    writer.seal(attempt)
    return writer.stage_and_publish(attempt)


def _committed_fields(writer, head):
    sql, params = committed_rows_select(
        FIELD_VALUES, head, org_id=writer.org_id, project_id=writer.lease.project_id
    )
    return writer.store.execute(
        f"SELECT level, count() FROM ({sql}) WHERE is_deleted = 0 GROUP BY level "
        "ORDER BY level",
        params,
    )


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("mode", ["conversation", "mixed"])
def test_finite_retention_refresh_over_a_million_retained_fields(
    live_writer, workspace, observe_project, monkeypatch, mode
):
    writer, now = live_writer, timezone.now()
    writer.lease.clock = lambda: now
    writer.lease.renew()
    writer.retention_days = lambda: RETENTION_DAYS
    conversation = definition_for(writer, workspace, observe_project)
    definitions = [conversation]
    if mode == "mixed":
        # span_definition_for creates its own base metric of the same name.
        conversation.metric.name = "Scale conversation fixture"
        conversation.metric.save(update_fields=["name"])
        span = span_definition_for(writer, workspace, observe_project)
        definitions.append(span)
        conversations = FIELD_ROWS // (SPANS_PER_CONVERSATION + 1) + 1
        layout = [
            (conversation, "conversation", conversations),
            (span, "span", FIELD_ROWS - conversations),
        ]
    else:
        layout = [(conversation, "conversation", FIELD_ROWS)]
    began = monotonic()
    head = _seed_head(writer, layout, now)
    seeded_seconds = monotonic() - began
    expected = {level: count for _, level, count in layout}
    assert dict(_committed_fields(writer, head)) == expected
    assert sum(expected.values()) >= 1_000_000

    scope = Scope(writer.org_id, writer.workspace_id, observe_project.id)
    session, trace, at = uuid4(), uuid4(), now - timedelta(hours=12)
    seed_span_revision(writer, scope, trace, "fresh", session, at, 6, revision=1)
    dependency = Dependency(str(trace), "fresh", str(session), str(session), "", "", at)

    execute = ClickHousePublicationStore.execute
    largest, slow = [0, ""], []

    def observed_execute(self, sql, params=None, **kwargs):
        # The text plus every substituted parameter value.
        size = len(sql) + sum(
            sql.count(f"%({key})s") * len(str(value))
            for key, value in (params or {}).items()
        )
        if size > largest[0]:
            largest[:] = [size, sql[:160]]
        started = monotonic()
        try:
            return execute(self, sql, params, **kwargs)
        finally:
            elapsed = monotonic() - started
            if elapsed > 1:
                slow.append({"seconds": round(elapsed, 2), "query": sql[:160]})

    monkeypatch.setattr(ClickHousePublicationStore, "execute", observed_execute)
    began = monotonic()
    manifest = writer.refresh(
        change_for(session, dependencies=(dependency,)),
        definitions,
        scope,
        start=now - timedelta(days=2),
        end=now,
        as_of=now,
    )
    refresh_seconds = monotonic() - began
    monkeypatch.setattr(ClickHousePublicationStore, "execute", execute)
    version = writer.store.execute("SELECT version(), getSetting('enable_analyzer')")[0]
    print(
        "RETENTION_SCALE "
        + json.dumps(
            {
                "clickhouse": version,
                "mode": mode,
                "retained_field_rows": expected,
                "seed_seconds": round(seeded_seconds, 2),
                "refresh_seconds": round(refresh_seconds, 2),
                "largest_statement_bytes": largest[0],
                "largest_statement_prefix": largest[1],
                "slow_statements": slow,
                "write_settings": WRITE_SETTINGS,
            },
            default=str,
        ),
        flush=True,
    )
    # The refresh published its own draft: nothing expired, every owner link is
    # retained, so no replacement privacy checkpoint and no retention replan.
    assert manifest is not None
    assert MetricProjectRefresh.for_project(observe_project.id).head_manifest_id == (
        manifest.id
    )
    draft = manifest.published_by
    assert draft.purpose == "incremental"
    assert not draft.source_ceilings.get("logical_expiry")
    assert not MetricRefreshAttempt.objects.filter(
        project_id=observe_project.id, abort_reason="retention_replan"
    ).exists()
    committed = dict(_committed_fields(writer, manifest))
    assert committed["conversation"] == expected["conversation"] + 1
    if mode == "mixed":
        assert committed["span"] == expected["span"] + 1
    # The fresh conversation (12 hours old; the seeded head is 2-32 days old)
    # is the only dirty bucket, and its KPI contribution is published.
    assert [row[2:] for row in metric_bucket_rows(writer, manifest, conversation)] == [
        (1, 6.0, 1.0)
    ]
    assert largest[0] <= MAX_STATEMENT_BYTES, largest
