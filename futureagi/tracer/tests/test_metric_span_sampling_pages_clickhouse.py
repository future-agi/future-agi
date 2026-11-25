# ruff: noqa: F811 -- shared isolated ClickHouse and PG fixtures
"""Quarter-wide sampler identity evidence through the real publication writer."""

import json
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from tracer.models.derived_metric import DerivedMetric, DerivedMetricVersion
from tracer.services.derived_metrics import span_evidence
from tracer.services.derived_metrics.changes import ChangeBatch, SourceKey, UnitKey
from tracer.services.derived_metrics.publication import committed_rows_select
from tracer.services.derived_metrics.queries import Scope
from tracer.services.derived_metrics.storage import FIELD_VALUES
from tracer.tests.test_derived_metric_clickhouse import seed_eval
from tracer.tests.test_derived_metric_publication_clickhouse import (  # noqa: F401
    live_writer,
    metric_ch,
    publication_store,
)
from tracer.tests.test_derived_metric_reads_clickhouse import AT

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.usefixtures("enabled_metrics"),
]


def fixture(
    writer,
    workspace,
    project,
    *,
    filters=(),
    config=None,
    inputs_in_field_spec=False,
    weighting="inverse_probability",
):
    metric = DerivedMetric.objects.create(
        organization=workspace.organization,
        workspace=workspace,
        project=project,
        name="Sampled spans " + str(uuid4()),
    )
    config = config or uuid4()
    inputs = [
        {
            "alias": "y",
            "source": "eval",
            "config_id": str(config),
            "config_hash": "a" * 64,
            "reducer": "latest",
        }
    ]
    definition = DerivedMetricVersion.objects.create(
        metric=metric,
        version=1,
        kind="custom",
        level="span",
        formula="mean(y)",
        inputs=inputs
        if not inputs_in_field_spec
        else [{"alias": "y", "source": "span", "field": "cost", "reducer": "latest"}],
        field_spec={"inputs": inputs} if inputs_in_field_spec else {},
        filters=list(filters),
        selection_unit="span",
        weighting=weighting,
        backfill_status="complete",
    )
    scope = Scope(workspace.organization_id, workspace.id, project.id)
    return definition, config, scope


def seed(writer, scope, config, trace, *, at=AT, version=1, deleted=0, model="kept"):
    span_id = "shared-sampler-id"
    writer.store.execute(
        "INSERT INTO spans (id, trace_id, project_id, org_id, start_time, end_time, "
        "created_at, updated_at, _version, is_deleted, model) VALUES "
        "(%(span)s, %(trace)s, %(project_id)s, %(org_id)s, %(at)s, %(at)s, "
        "%(at)s, %(at)s, %(version)s, %(deleted)s, %(model)s)",
        {
            **scope.parameters(),
            "span": span_id,
            "trace": str(trace),
            "at": at + timedelta(minutes=1),
            "version": version,
            "deleted": deleted,
            "model": model,
        },
    )
    if not deleted and version == 1:
        seed_eval(
            writer.store.client,
            config,
            uuid4(),
            at + timedelta(minutes=2),
            2,
            0.5,
            span_id=span_id,
            trace_id=trace,
            selection_overrides={
                "sampling_unit": "span",
                "sampling_unit_id": "span:" + span_id,
                "window": {
                    "start": (AT - timedelta(days=1)).isoformat(),
                    "end": None,
                },
            },
        )
    return SourceKey(str(trace), span_id)


def refresh(writer, definition, scope, sources, *, watermark=1, others=()):
    return writer.refresh(
        ChangeBatch(
            tuple(sources),
            tuple(
                UnitKey(
                    "span", json.dumps([s.trace_id, s.span_id], separators=(",", ":"))
                )
                for s in sources
            ),
            (),
            (),
            (),
            {"watermark_ns": watermark},
        ),
        [definition, *others],
        scope,
        start=AT,
        end=AT + timedelta(hours=1),
        as_of=datetime.now(UTC),
    )


def rows(writer, head):
    sql, params = committed_rows_select(
        FIELD_VALUES,
        head,
        org_id=writer.org_id,
        project_id=writer.lease.project_id,
    )
    return writer.store.execute(
        "SELECT unit_id, status, no_value_reason, sample_probability "
        f"FROM ({sql}) WHERE is_deleted = 0 ORDER BY unit_id",
        params,
    )


@pytest.mark.parametrize("inputs_in_field_spec", [False, True])
def test_sampler_peers_split_across_pages_are_both_invalid(
    live_writer, workspace, observe_project, monkeypatch, inputs_in_field_spec
):
    writer = live_writer
    definition, config, scope = fixture(
        writer, workspace, observe_project, inputs_in_field_spec=inputs_in_field_spec
    )
    sources = [seed(writer, scope, config, UUID(int=i)) for i in (1, 2)]
    monkeypatch.setattr(span_evidence, "SPAN_CAPTURE_ROWS", 1)
    head = refresh(writer, definition, scope, sources)
    actual = rows(writer, head)
    assert len(actual) == 2
    assert [r[1:3] for r in actual] == [
        ("no_value", "invalid_probability"),
        ("no_value", "invalid_probability"),
    ]


def test_ambiguity_is_frozen_before_first_page_then_renominated(
    live_writer, workspace, observe_project, monkeypatch
):
    writer = live_writer
    definition, config, scope = fixture(writer, workspace, observe_project)
    sources = [seed(writer, scope, config, UUID(int=i)) for i in (1, 2)]
    monkeypatch.setattr(span_evidence, "SPAN_CAPTURE_ROWS", 1)
    original = span_evidence._freeze_span_page
    calls = []

    def remove_first_peer(*args, **kwargs):
        result = original(*args, **kwargs)
        calls.append(kwargs["unit_range"])
        if len(calls) == 1:
            # A real new revision arrives between captures, backdated within the
            # attempt's as_of. The second page must keep the frozen ambiguity.
            seed(writer, scope, config, UUID(int=1), version=2, deleted=1)
        return result

    monkeypatch.setattr(span_evidence, "_freeze_span_page", remove_first_peer)
    head = refresh(writer, definition, scope, sources)
    assert len(calls) >= 2
    assert [r[1:3] for r in rows(writer, head)] == [
        ("no_value", "invalid_probability"),
        ("no_value", "invalid_probability"),
    ]
    monkeypatch.setattr(span_evidence, "_freeze_span_page", original)
    next_head = refresh(writer, definition, scope, sources, watermark=2)
    actual = rows(writer, next_head)
    assert len(actual) == 1
    assert actual[0][1:] == ("ok", "", 0.5)


def test_new_peer_between_pages_waits_for_quarter_renomination(
    live_writer, workspace, observe_project, monkeypatch
):
    writer = live_writer
    definition, config, scope = fixture(writer, workspace, observe_project)
    sources = [seed(writer, scope, config, UUID(int=1))]
    monkeypatch.setattr(span_evidence, "SPAN_CAPTURE_ROWS", 1)
    original = span_evidence._freeze_span_page
    calls = []

    def add_peer(*args, **kwargs):
        result = original(*args, **kwargs)
        calls.append(kwargs["unit_range"])
        if len(calls) == 1:
            sources.append(seed(writer, scope, config, UUID(int=2)))
        return result

    monkeypatch.setattr(span_evidence, "_freeze_span_page", add_peer)
    head = refresh(writer, definition, scope, sources)
    actual = rows(writer, head)
    assert len(actual) == 2
    assert all(r[1:] == ("ok", "", 0.5) for r in actual)
    monkeypatch.setattr(span_evidence, "_freeze_span_page", original)
    next_head = refresh(writer, definition, scope, sources, watermark=2)
    assert [r[1:3] for r in rows(writer, next_head)] == [
        ("no_value", "invalid_probability"),
        ("no_value", "invalid_probability"),
    ]


@pytest.mark.parametrize("peer", ["next_quarter", "filtered", "deleted", "moved"])
def test_context_uses_live_filtered_winners_in_each_quarter(
    live_writer, workspace, observe_project, monkeypatch, peer
):
    writer = live_writer
    definition, config, scope = fixture(
        writer,
        workspace,
        observe_project,
        filters=[{"field": "model", "value": "kept"}],
    )
    sources = [seed(writer, scope, config, UUID(int=1))]
    sources.append(
        seed(
            writer,
            scope,
            config,
            UUID(int=2),
            at=AT + timedelta(minutes=15) if peer == "next_quarter" else AT,
            model="excluded" if peer == "filtered" else "kept",
        )
    )
    if peer in {"deleted", "moved"}:
        seed(
            writer,
            scope,
            config,
            UUID(int=2),
            version=2,
            deleted=int(peer == "deleted"),
            at=AT - timedelta(minutes=15) if peer == "moved" else AT,
        )
    # Multiple physical revisions of one trace are not sampler collisions.
    seed(writer, scope, config, UUID(int=1), version=2)
    monkeypatch.setattr(span_evidence, "SPAN_CAPTURE_ROWS", 1)
    actual = rows(writer, refresh(writer, definition, scope, sources))
    assert actual
    assert all(r[1:] == ("ok", "", 0.5) for r in actual)


@pytest.mark.parametrize("bound", ["SAMPLING_CONTEXT_ROWS", "SAMPLING_CONTEXT_BYTES"])
def test_context_overflow_aborts_before_pages_and_preserves_head(
    live_writer, workspace, observe_project, monkeypatch, bound
):
    from clickhouse_driver.errors import ServerException

    from tracer.models.derived_metric import MetricProjectRefresh
    from tracer.services.derived_metrics import span_sampling

    writer = live_writer
    definition, config, scope = fixture(writer, workspace, observe_project)
    sources = [seed(writer, scope, config, UUID(int=1))]
    head = refresh(writer, definition, scope, sources)
    before = rows(writer, head)
    sources.append(seed(writer, scope, config, UUID(int=2)))
    monkeypatch.setattr(span_sampling, bound, 0)
    pages = []
    original = span_evidence._freeze_span_page

    def page(*args, **kwargs):
        pages.append(True)
        return original(*args, **kwargs)

    monkeypatch.setattr(span_evidence, "_freeze_span_page", page)
    with pytest.raises(ServerException, match="metric_span_sampling_context_budget"):
        refresh(writer, definition, scope, sources, watermark=2)
    assert not pages
    assert (
        MetricProjectRefresh.objects.get(project=observe_project).head_manifest_id
        == head.id
    )
    assert rows(writer, head) == before
    assert writer.store.execute(
        "SELECT count() FROM system.tables WHERE is_temporary "
        "AND startsWith(name, 'metric_span_sampling_')"
    ) == [(0,)]


def test_context_is_shared_by_definitions_but_respects_distinct_filters(
    live_writer, workspace, observe_project, monkeypatch
):
    writer = live_writer
    definition, config, scope = fixture(writer, workspace, observe_project)
    same, _, _ = fixture(writer, workspace, observe_project, config=config)
    filtered, _, _ = fixture(
        writer,
        workspace,
        observe_project,
        config=config,
        filters=[{"field": "model", "value": "kept"}],
    )
    sources = [
        seed(writer, scope, config, UUID(int=1)),
        seed(writer, scope, config, UUID(int=2), model="excluded"),
    ]
    monkeypatch.setattr(span_evidence, "SPAN_CAPTURE_ROWS", 1)
    original = writer.store.execute
    captures = []

    def execute(sql, *args, **kwargs):
        if sql.startswith("INSERT INTO metric_span_sampling_"):
            captures.append(sql)
        return original(sql, *args, **kwargs)

    monkeypatch.setattr(writer.store, "execute", execute)
    head = refresh(writer, definition, scope, sources, others=[same, filtered])
    assert len(captures) == 2  # one per filtered population, not per definition/page
    actual = rows(writer, head)
    assert len(actual) == 5
    assert sum(r[1:3] == ("no_value", "invalid_probability") for r in actual) == 4
    assert sum(r[1:] == ("ok", "", 0.5) for r in actual) == 1


def test_lost_context_session_aborts_instead_of_recapturing_source(
    live_writer, workspace, observe_project, monkeypatch
):
    from tracer.models.derived_metric import MetricProjectRefresh
    from tracer.services.derived_metrics.publication import MetricVisibilityError

    writer = live_writer
    definition, config, scope = fixture(writer, workspace, observe_project)
    sources = [seed(writer, scope, config, UUID(int=i)) for i in (1, 2)]
    monkeypatch.setattr(span_evidence, "SPAN_CAPTURE_ROWS", 1)
    original = span_evidence._freeze_span_page

    def disconnect_after_page(*args, **kwargs):
        result = original(*args, **kwargs)
        writer.store.client.disconnect()
        return result

    monkeypatch.setattr(span_evidence, "_freeze_span_page", disconnect_after_page)
    with pytest.raises(MetricVisibilityError, match="span sampling context lost"):
        refresh(writer, definition, scope, sources)
    assert (
        MetricProjectRefresh.objects.get(project=observe_project).head_manifest_id
        is None
    )


@pytest.mark.parametrize("remove_peer", [False, True])
def test_byte_retry_preserves_frozen_sampling_context(
    live_writer, workspace, observe_project, monkeypatch, remove_peer
):
    from clickhouse_driver.errors import ServerException

    from tracer.services.derived_metrics.bounded_reads import SOURCE_SCHEMA
    from tracer.services.derived_metrics.unit_reads import span_values_select

    writer = live_writer
    definition, config, scope = fixture(writer, workspace, observe_project)
    sources = [seed(writer, scope, config, UUID(int=i)) for i in (1, 2)]
    # Size the real typed rows: either fits individually, never both together.
    sql, params = span_values_select(
        definition,
        scope,
        start=AT,
        end=AT + timedelta(minutes=15),
        as_of=datetime.now(UTC),
    )
    sizes = writer.store.execute(
        f"SELECT length(formatRow('RowBinary', {', '.join(c for c, _ in SOURCE_SCHEMA)})) "
        f"FROM ({sql})",
        params,
    )
    assert len(sizes) == 2
    monkeypatch.setattr(span_evidence, "SPAN_CAPTURE_ROWS", 2)
    monkeypatch.setattr(span_evidence, "SPAN_CAPTURE_BYTES", max(s[0] for s in sizes))
    original = span_evidence._freeze_span_page
    overflows, captures = [], []
    execute = writer.store.execute

    def record_context(sql, *args, **kwargs):
        if (
            sql.startswith("INSERT INTO metric_span_sampling_")
            and "AS context_rows" in sql
        ):
            captures.append(sql)
        return execute(sql, *args, **kwargs)

    def page(*args, **kwargs):
        try:
            return original(*args, **kwargs)
        except ServerException as exc:
            if "metric_span_capture_budget" not in str(exc):
                raise
            overflows.append(kwargs["unit_range"])
            # This is a real native failure: the driver dropped every temporary
            # table. Recovery must replay frozen collisions, never source truth.
            assert writer.store.client.connection.socket is None
            if remove_peer:
                seed(writer, scope, config, UUID(int=1), version=2, deleted=1)
            raise

    monkeypatch.setattr(writer.store, "execute", record_context)
    monkeypatch.setattr(span_evidence, "_freeze_span_page", page)
    head = refresh(writer, definition, scope, sources)
    actual = rows(writer, head)
    assert len(overflows) == 1
    assert len(captures) == 1  # no whole-quarter source recapture after shrinking
    assert len(actual) == (1 if remove_peer else 2)
    assert all(r[1:3] == ("no_value", "invalid_probability") for r in actual)
    assert writer.store.execute(
        "SELECT count() FROM system.tables WHERE is_temporary "
        "AND startsWith(name, 'metric_span_sampling_')"
    ) == [(0,)]


@pytest.mark.parametrize("bound", ["SAMPLING_CONTEXT_ROWS", "SAMPLING_CONTEXT_BYTES"])
@pytest.mark.parametrize("inputs_in_field_spec", [False, True])
def test_unweighted_eval_spans_do_not_build_sampling_context(
    live_writer, workspace, observe_project, monkeypatch, bound, inputs_in_field_spec
):
    from tracer.services.derived_metrics import span_sampling

    writer = live_writer
    definition, config, scope = fixture(
        writer,
        workspace,
        observe_project,
        weighting="none",
        inputs_in_field_spec=inputs_in_field_spec,
    )
    sources = [seed(writer, scope, config, UUID(int=i)) for i in (1, 2)]
    monkeypatch.setattr(span_evidence, "SPAN_CAPTURE_ROWS", 1)
    monkeypatch.setattr(span_sampling, bound, 0)
    execute = writer.store.execute
    captures = []

    def record_context(sql, *args, **kwargs):
        if sql.startswith("CREATE TEMPORARY TABLE metric_span_sampling_"):
            captures.append(sql)
        return execute(sql, *args, **kwargs)

    monkeypatch.setattr(writer.store, "execute", record_context)
    head = refresh(writer, definition, scope, sources)
    actual = rows(writer, head)
    assert not captures
    assert len(actual) == 2
    assert all(r[1:] == ("ok", "", 1.0) for r in actual)
    sql, params = committed_rows_select(
        FIELD_VALUES, head, org_id=writer.org_id, project_id=writer.lease.project_id
    )
    assert writer.store.execute(
        f"SELECT value_number FROM ({sql}) WHERE is_deleted = 0", params
    ) == [(2.0,), (2.0,)]


def test_unweighted_population_cannot_block_weighted_definition(
    live_writer, workspace, observe_project, monkeypatch
):
    from tracer.services.derived_metrics import span_sampling

    writer = live_writer
    unweighted, config, scope = fixture(
        writer, workspace, observe_project, weighting="none"
    )
    weighted, _, _ = fixture(
        writer,
        workspace,
        observe_project,
        config=config,
        filters=[{"field": "model", "value": "kept"}],
    )
    sources = [
        seed(writer, scope, config, UUID(int=1)),
        seed(writer, scope, config, UUID(int=2), model="excluded"),
    ]
    monkeypatch.setattr(span_evidence, "SPAN_CAPTURE_ROWS", 1)
    monkeypatch.setattr(span_sampling, "SAMPLING_CONTEXT_ROWS", 0)
    head = refresh(writer, unweighted, scope, sources, others=[weighted])
    actual = rows(writer, head)
    assert len(actual) == 3
    assert sum(r[1:] == ("ok", "", 1.0) for r in actual) == 2
    assert sum(r[1:] == ("ok", "", 0.5) for r in actual) == 1


def test_single_identity_byte_overflow_preserves_published_head(
    live_writer, workspace, observe_project, monkeypatch
):
    from clickhouse_driver.errors import ServerException

    from tracer.models.derived_metric import MetricProjectRefresh

    writer = live_writer
    definition, config, scope = fixture(writer, workspace, observe_project)
    sources = [seed(writer, scope, config, UUID(int=1))]
    head = refresh(writer, definition, scope, sources)
    before = rows(writer, head)
    monkeypatch.setattr(span_evidence, "SPAN_CAPTURE_ROWS", 2)
    monkeypatch.setattr(span_evidence, "SPAN_CAPTURE_BYTES", 1)
    with pytest.raises(ServerException, match="metric_span_capture_budget"):
        refresh(writer, definition, scope, sources, watermark=2)
    assert (
        MetricProjectRefresh.objects.get(project=observe_project).head_manifest_id
        == head.id
    )
    assert rows(writer, head) == before
