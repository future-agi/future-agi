"""Real CH25 integration: no mocked query compiler or membership selector.

Run on the test sidecar protected by conftest's target guard, never a live DB.
"""

import json
import uuid
from datetime import timedelta

import pytest
from django.utils import timezone

from tracer.models.eval_sampling import EvalSamplingMember
from tracer.models.eval_task import EvalTask, RunType
from tracer.models.observation_span import EvalLogger, ObservationSpan
from tracer.models.trace import Trace
from tracer.models.trace_session import TraceSession
from tracer.services.eval_tasks.reconciler import reconcile
from tracer.services.eval_tasks.sampling.policy import unit_hash
from tracer.services.eval_tasks.sampling.query import (
    SamplingQuery,
    sampling_clock,
    stream_query,
)
from tracer.services.eval_tasks.sampling.service import preview_policy, task_policy
from tracer.tests._ch_seed import _get_ch_client, seed_ch_spans

pytestmark = [pytest.mark.django_db, pytest.mark.integration]


@pytest.fixture(autouse=True)
def immediate_closed_freeze(settings):
    # Most fixtures seed rows and freeze in the same second. The settle margin
    # is exercised explicitly by its own real-ClickHouse test below.
    settings.EVAL_SAMPLING_CLOSED_SETTLE_SECONDS = 0


def make_policy(**overrides):
    now = timezone.now()
    return {
        "version": 1,
        "sampling_unit": "session",
        "mode": "closed",
        "window": {
            "start": (now - timedelta(hours=1)).isoformat(),
            "end": (now - timedelta(minutes=1)).isoformat(),
        },
        "default": {"target_n": 2},
        **overrides,
    }


def seed_contact(
    project, session, attributes, *, traces=2, observation_type="llm", seed=True
):
    spans = []
    for _index in range(traces):
        trace = Trace.objects.create(
            project=project, session=session, name=f"trace-{uuid.uuid4()}"
        )
        root = uuid.uuid4().hex[:16]
        for identity, parent in ((root, None), (uuid.uuid4().hex[:16], root)):
            spans.append(
                ObservationSpan.objects.create(
                    id=identity,
                    project=project,
                    trace=trace,
                    parent_span_id=parent,
                    name="cohort-test",
                    observation_type=observation_type,
                    span_attributes=attributes,
                    start_time=timezone.now() - timedelta(minutes=5),
                )
            )
    if seed:
        seed_ch_spans(spans)
    return spans


def test_twenty_cells_overlap_exact_quotas_late_rows_and_row_filter(
    project, custom_eval_config, ch_seed
):
    sessions = {}
    spans_by_session = {}
    for locale in range(5):
        for category in range(4):
            for unit in range(2 if category == 3 else 3):
                session = TraceSession.objects.create(
                    project=project, name=f"{locale}-{category}-{unit}"
                )
                attributes = {
                    "locale": f"l{locale}",
                    "category": f"c{category}",
                    "positive": category == 3,
                }
                sessions[str(session.id)] = (session, attributes)
                spans_by_session[str(session.id)] = seed_contact(
                    project, session, attributes, seed=False
                )
    seed_ch_spans([span for spans in spans_by_session.values() for span in spans])
    policy = make_policy(
        group_by=[{"key": "locale"}, {"key": "category"}],
        strata=[
            {
                "name": "rare-positive",
                "filters": [{"key": "positive", "op": "in", "values": [True]}],
                "rate": 10,
                "rare_below": 2,
            },
            {"name": "representative-control", "filters": [], "target_n": 2},
        ],
        default={"target_n": 0},
    )
    task = EvalTask.objects.create(
        project=project,
        row_type="spans",
        run_type=RunType.CONTINUOUS,
        filters={"observation_type": ["llm"]},
        sampling_policy=policy,
        spans_limit=1,
        sampling_rate=1,
    )
    task.evals.add(custom_eval_config)
    preview = preview_policy(task)
    assert len(preview["strata"]) == 20
    assert sum(cell["eligible_units"] for cell in preview["strata"]) == 55
    assert sum(cell["selected_units"] for cell in preview["strata"]) == 40
    assert sum(cell["selected_rows"] for cell in preview["strata"]) == 160
    assert preview["estimated_cost"] is None
    reconcile(task)
    members = list(EvalSamplingMember.objects.filter(cohort__task=task))
    assert len(members) == 40
    expected = {
        span.id
        for member in members
        for span in spans_by_session[member.unit_id.split(":", 1)[1]]
    }
    entries = list(EvalLogger.objects.filter(eval_task_id=str(task.id)))
    assert {entry.observation_span_id for entry in entries} == expected
    assert len(expected) == 160  # Legacy row limit/rate must not cut contacts.
    for member in members:
        expected_probability = 1 if "rare-positive" in member.stratum else 2 / 3
        assert member.inclusion_probability == pytest.approx(expected_probability)
    member = next(item for item in members if "rare-positive" in item.stratum)
    session, attributes = sessions[member.unit_id.split(":", 1)[1]]
    late = seed_contact(project, session, attributes, traces=1)
    filtered_out = seed_contact(
        project, session, attributes, traces=1, observation_type="embedding"
    )
    newcomer = TraceSession.objects.create(project=project, name="late-new-unit")
    unseen = seed_contact(project, newcomer, attributes, traces=1)
    reconcile(task)
    actual = set(
        EvalLogger.objects.filter(eval_task_id=str(task.id)).values_list(
            "observation_span_id", flat=True
        )
    )
    assert actual == expected | {span.id for span in late}
    assert not actual.intersection(span.id for span in [*filtered_out, *unseen])
    assert EvalSamplingMember.objects.filter(cohort__task=task).count() == 40


@pytest.mark.parametrize(
    "unit,table,column",
    [
        ("session", "trace_session_id_remap", "trace_session_id"),
        ("user", "end_user_id_remap", "end_user_id"),
    ],
)
def test_canonical_old_and_new_ids_are_one_sampling_unit(
    project, ch_seed, unit, table, column
):
    old_a, old_b, new = (uuid.uuid4() for _ in range(3))
    client = _get_ch_client()
    try:
        client.insert(
            table, [[old_a, new], [old_b, new]], column_names=["old_id", "new_id"]
        )
        now = timezone.now()
        seed_ch_spans(
            [
                {
                    "id": uuid.uuid4().hex[:16],
                    "trace_id": str(uuid.uuid4()),
                    "project_id": str(project.id),
                    "org_id": str(project.organization_id),
                    column: value,
                    "name": "remapped",
                    "observation_type": "llm",
                    "start_time": now - timedelta(minutes=5),
                    "created_at": now - timedelta(seconds=1),
                }
                for value in (old_a, old_b, new)
            ],
            client=client,
        )
    finally:
        client.close()
    task = EvalTask(
        project=project,
        row_type="spans",
        filters={},
        sampling_policy=make_policy(sampling_unit=unit, default={"target_n": 1}),
    )
    policy, version = task_policy(task, now=timezone.now())
    members = [
        row
        for block in stream_query(
            *SamplingQuery(task, policy, version, sampling_clock()).members()
        )
        for row in block
    ]
    assert len(members) == 1
    assert members[0][0] == f"{unit}:{min(str(old_a), str(old_b))}"
    assert members[0][2] == 1
    preview = preview_policy(task)
    assert preview["strata"][0]["eligible_units"] == 1
    assert preview["strata"][0]["selected_rows"] == 3


@pytest.mark.parametrize("missing,expected_units", [("exclude", 1), ("row", 3)])
def test_custom_contact_missing_id_policy_is_explicit(
    project, ch_seed, missing, expected_units
):
    seed_contact(project, None, {"contact_id": "custom-contact"}, traces=1)
    seed_contact(project, None, {}, traces=1)
    task = EvalTask(
        project=project,
        row_type="spans",
        filters={},
        sampling_policy=make_policy(
            sampling_unit="attribute",
            unit_attribute={"key": "contact_id"},
            missing_id=missing,
            default={"rate": 100},
        ),
    )
    preview = preview_policy(task)
    assert sum(cell["eligible_units"] for cell in preview["strata"]) == expected_units
    assert sum(cell["selected_rows"] for cell in preview["strata"]) == (
        2 if missing == "exclude" else 4
    )


def test_hash_order_in_clickhouse_matches_portable_reference(project, ch_seed):
    ids = []
    for index in range(8):
        session = TraceSession.objects.create(project=project, name=str(index))
        seed_contact(project, session, {}, traces=1)
        ids.append(f"session:{session.id}")
    task = EvalTask(
        project=project,
        row_type="spans",
        filters={},
        sampling_policy=make_policy(default={"target_n": 3}),
    )
    policy, version = task_policy(task, now=timezone.now())
    selected = [
        row[0]
        for block in stream_query(
            *SamplingQuery(task, policy, version, sampling_clock()).members()
        )
        for row in block
    ]
    assert sorted(selected) == sorted(
        sorted(ids, key=lambda unit: unit_hash(version, unit))[:3]
    )


def test_live_contact_sampling_continues_beyond_preview_window(
    project, custom_eval_config, ch_seed
):
    from unittest.mock import patch

    initial = sampling_clock()
    first_session = TraceSession.objects.create(project=project, name="ongoing-first")
    first = seed_contact(project, first_session, {"vip": True})
    task = EvalTask.objects.create(
        project=project,
        row_type="spans",
        run_type=RunType.CONTINUOUS,
        sampling_policy={
            "version": 1,
            "sampling_unit": "session",
            "mode": "live",
            "window": {"start": (initial - timedelta(days=1)).isoformat(), "end": None},
            "strata": [
                {
                    "name": "vip",
                    "filters": [{"key": "vip", "op": "in", "values": [True]}],
                    "rate": 100,
                }
            ],
            "default": {"rate": 0},
        },
        filters={},
    )
    task.evals.add(custom_eval_config)
    reconcile(task)
    original_cohort = EvalSamplingMember.objects.get(
        cohort__task=task, selected=True
    ).cohort_id
    assert set(
        EvalLogger.objects.filter(eval_task_id=str(task.id)).values_list(
            "observation_span_id", flat=True
        )
    ) == {span.id for span in first}
    future = sampling_clock() + timedelta(days=40)
    new_session = TraceSession.objects.create(project=project, name="ongoing-future")
    rejected_session = TraceSession.objects.create(
        project=project, name="ongoing-rejected"
    )
    later = []
    expected = {span.id for span in first}
    for session, vip, is_late in [
        (first_session, False, True),
        (new_session, True, False),
        (rejected_session, False, False),
    ]:
        spans = seed_contact(project, session, {"vip": vip}, traces=1, seed=False)
        for span in spans:
            span.created_at = future - timedelta(seconds=1)
            span.updated_at = span.created_at
            if not is_late:
                span.start_time = future - timedelta(minutes=5)
        later.extend(spans)
        if session != rejected_session:
            expected.update(span.id for span in spans)
    seed_ch_spans(later, version_from_created_at=True)
    # The fixture plants future arrival/event timestamps, then advances only
    # the sampler's clock. All discovery/classification/materialization SQL is real.
    with patch(
        "tracer.services.eval_tasks.sampling.service.sampling_clock",
        return_value=future,
    ):
        reconcile(task)
    entries = list(EvalLogger.objects.filter(eval_task_id=str(task.id)))
    assert {entry.observation_span_id for entry in entries} == expected
    assert len(entries) == 8
    assert {
        entry.output_metadata["_task_selection"]["sampling"]["cohort_id"]
        for entry in entries
    } == {str(original_cohort)}
    assert all(
        entry.output_metadata["_task_selection"]["sampling"]["window"]["end"] is None
        for entry in entries
    )
    assert EvalSamplingMember.objects.filter(cohort__task=task).count() == 3


def test_population_filters_use_latest_values_not_stale_versions(project, ch_seed):
    excluded = TraceSession.objects.create(project=project, name="corrected-locale")
    included = TraceSession.objects.create(project=project, name="current-locale")
    old = seed_contact(project, excluded, {"locale": "en"}, traces=1)
    seed_contact(project, included, {"locale": "en"}, traces=1)
    for span in old:
        span.span_attributes = {"locale": "fr"}
        span.updated_at = timezone.now()
    seed_ch_spans(old)
    task = EvalTask(
        project=project,
        row_type="spans",
        filters={
            "filters": [
                {
                    "column_id": "locale",
                    "filter_config": {
                        "col_type": "SPAN_ATTRIBUTE",
                        "filter_type": "text",
                        "filter_op": "in",
                        "filter_value": ["en"],
                    },
                }
            ]
        },
        sampling_policy=make_policy(default={"target_n": 1}),
    )
    preview = preview_policy(task)
    assert sum(cell["eligible_units"] for cell in preview["strata"]) == 1
    assert sum(cell["selected_rows"] for cell in preview["strata"]) == 2
    policy, version = task_policy(task, now=sampling_clock())
    members = [
        row
        for block in stream_query(
            *SamplingQuery(task, policy, version, sampling_clock()).members()
        )
        for row in block
    ]
    assert members[0][0] == f"session:{included.id}"
    assert members[0][2] == 1


def test_corrected_timestamp_outside_window_does_not_resurrect_old_version(
    project, ch_seed
):
    hour = (sampling_clock() - timedelta(hours=2)).replace(
        minute=0, second=0, microsecond=0
    )
    bad = TraceSession.objects.create(project=project, name="moved-outside-window")
    good = TraceSession.objects.create(project=project, name="inside-window")
    changed = seed_contact(project, bad, {}, traces=1, seed=False)
    kept = seed_contact(project, good, {}, traces=1, seed=False)
    for span in [*changed, *kept]:
        span.start_time = hour + timedelta(minutes=5)
    seed_ch_spans([*changed, *kept])
    for span in changed:
        span.start_time = hour + timedelta(minutes=40)
        span.updated_at = timezone.now()
    seed_ch_spans(changed)
    task = EvalTask(
        project=project,
        row_type="spans",
        filters={},
        sampling_policy=make_policy(
            window={
                "start": hour.isoformat(),
                "end": (hour + timedelta(minutes=10)).isoformat(),
            },
            default={"target_n": 1},
        ),
    )
    preview = preview_policy(task)
    assert sum(cell["eligible_units"] for cell in preview["strata"]) == 1
    assert sum(cell["selected_rows"] for cell in preview["strata"]) == 2


@pytest.mark.parametrize("change", ["filter", "unit", "deleted"])
def test_pending_negative_proof_preserves_unaffected_old_backlog(
    project, custom_eval_config, ch_seed, change
):
    chosen = TraceSession.objects.create(project=project, name="pending-selected")
    unaffected = TraceSession.objects.create(project=project, name="pending-old")
    rows = seed_contact(project, chosen, {"locale": "en"}, traces=1, seed=False)
    rows += seed_contact(project, unaffected, {"locale": "en"}, traces=1, seed=False)
    seed_ch_spans(rows)
    task = EvalTask.objects.create(
        project=project,
        row_type="spans",
        run_type=RunType.CONTINUOUS,
        filters={
            "filters": [
                {
                    "column_id": "locale",
                    "filter_config": {
                        "col_type": "SPAN_ATTRIBUTE",
                        "filter_type": "text",
                        "filter_op": "in",
                        "filter_value": ["en"],
                    },
                }
            ]
        },
        sampling_policy=make_policy(default={"rate": 100}),
    )
    task.evals.add(custom_eval_config)
    reconcile(task)
    assert EvalLogger.objects.filter(eval_task_id=str(task.id)).count() == 4
    floor = sampling_clock()
    EvalTask.objects.filter(pk=task.pk).update(continuous_cursor=floor)
    changed = rows[0]
    if change == "filter":
        changed.span_attributes = {"locale": "fr"}
    elif change == "unit":
        other = TraceSession.objects.create(
            project=project, name="not-in-frozen-cohort"
        )
        changed.trace_session_id = other.id
    else:
        changed.deleted = True
    changed.updated_at = timezone.now()
    seed_ch_spans([changed])
    result = reconcile(task)
    assert result.dropped == 1
    remaining = set(
        EvalLogger.objects.filter(eval_task_id=str(task.id)).values_list(
            "observation_span_id", flat=True
        )
    )
    assert remaining == {span.id for span in rows[1:]}
    assert EvalLogger.all_objects.get(
        eval_task_id=str(task.id), observation_span_id=changed.id
    ).deleted


@pytest.mark.parametrize("rate", [7, 14, 28])
def test_closed_percent_quotas_are_exact_on_integer_boundaries(project, ch_seed, rate):
    now = timezone.now()
    seed_ch_spans(
        [
            {
                "id": uuid.uuid4().hex[:16],
                "trace_id": str(uuid.uuid4()),
                "project_id": str(project.id),
                "org_id": str(project.organization_id),
                "trace_session_id": uuid.uuid4(),
                "name": "rate-boundary",
                "observation_type": "llm",
                "start_time": now - timedelta(minutes=5),
                "created_at": now - timedelta(minutes=1),
            }
            for _ in range(100)
        ]
    )
    task = EvalTask(
        project=project,
        row_type="spans",
        filters={},
        sampling_policy=make_policy(default={"rate": rate}),
    )
    preview = preview_policy(task)
    assert sum(cell["selected_units"] for cell in preview["strata"]) == rate
    policy, version = task_policy(task, now=sampling_clock())
    members = [
        row
        for block in stream_query(
            *SamplingQuery(task, policy, version, sampling_clock()).members()
        )
        for row in block
    ]
    assert len(members) == rate
    assert {row[2] for row in members} == {rate / 100}


def test_pending_move_between_selected_units_refreshes_actual_probability(
    project, custom_eval_config, ch_seed
):
    groups = {}
    for index, locale in enumerate(("en", "en", "fr")):
        session = TraceSession.objects.create(
            project=project, name=f"probability-{index}"
        )
        groups[str(session.id)] = seed_contact(
            project, session, {"locale": locale}, traces=1, seed=False
        )
    seed_ch_spans([span for spans in groups.values() for span in spans])
    task = EvalTask.objects.create(
        project=project,
        row_type="spans",
        run_type=RunType.CONTINUOUS,
        filters={},
        sampling_policy=make_policy(
            group_by=[{"key": "locale"}], default={"target_n": 1}
        ),
    )
    task.evals.add(custom_eval_config)
    reconcile(task)
    members = list(EvalSamplingMember.objects.filter(cohort__task=task, selected=True))
    source = next(member for member in members if member.inclusion_probability == 0.5)
    target = next(member for member in members if member.inclusion_probability == 1)
    moving = groups[source.unit_id.split(":", 1)[1]]
    EvalTask.objects.filter(pk=task.pk).update(continuous_cursor=sampling_clock())
    for span in moving:
        span.trace_session_id = target.unit_id.split(":", 1)[1]
        span.updated_at = timezone.now()
    seed_ch_spans(moving)
    reconcile(task)
    entries = EvalLogger.objects.filter(
        eval_task_id=str(task.id), observation_span_id__in=[span.id for span in moving]
    )
    assert entries.count() == 2
    for entry in entries:
        metadata = entry.output_metadata["_task_selection"]["sampling"]
        assert metadata["sampling_unit_id"] == target.unit_id
        assert metadata["stratum"] == target.stratum
        assert metadata["inclusion_probability"] == 1


def test_pending_trace_refreshes_filter_witness_when_only_children_change(
    project, custom_eval_config, ch_seed
):
    trace = Trace.objects.create(project=project, name="pending-trace-witness")
    timestamp = timezone.now() - timedelta(minutes=5)
    root = ObservationSpan.objects.create(
        id=uuid.uuid4().hex[:16],
        project=project,
        trace=trace,
        name="root",
        observation_type="llm",
        start_time=timestamp,
    )
    first = ObservationSpan.objects.create(
        id=uuid.uuid4().hex[:16],
        project=project,
        trace=trace,
        parent_span_id=root.id,
        name="first",
        observation_type="llm",
        start_time=timestamp + timedelta(seconds=1),
        span_attributes={"locale": "en"},
    )
    second = ObservationSpan.objects.create(
        id=uuid.uuid4().hex[:16],
        project=project,
        trace=trace,
        parent_span_id=root.id,
        name="second",
        observation_type="llm",
        start_time=timestamp + timedelta(seconds=2),
        span_attributes={"locale": "fr"},
    )
    seed_ch_spans([root, first, second])
    task = EvalTask.objects.create(
        project=project,
        row_type="traces",
        run_type=RunType.CONTINUOUS,
        filters={
            "filters": [
                {
                    "column_id": "locale",
                    "filter_config": {
                        "col_type": "SPAN_ATTRIBUTE",
                        "filter_type": "text",
                        "filter_op": "in",
                        "filter_value": ["en"],
                    },
                }
            ]
        },
        sampling_policy=make_policy(sampling_unit="trace", default={"rate": 100}),
    )
    task.evals.add(custom_eval_config)
    reconcile(task)
    entry = EvalLogger.objects.get(eval_task_id=str(task.id))
    sampling = entry.output_metadata["_task_selection"]["sampling"]
    assert (
        entry.output_metadata["_task_selection"]["filter_witnesses"][0]["span_id"]
        == first.id
    )
    EvalTask.objects.filter(pk=task.pk).update(continuous_cursor=sampling_clock())
    first.span_attributes = {"locale": "fr"}
    second.span_attributes = {"locale": "en"}
    first.updated_at = second.updated_at = timezone.now()
    seed_ch_spans([first, second])
    reconcile(task)
    entry.refresh_from_db()
    assert entry.output_metadata["_task_selection"]["sampling"] == sampling
    assert (
        entry.output_metadata["_task_selection"]["filter_witnesses"][0]["span_id"]
        == second.id
    )


@pytest.mark.parametrize("unit", ["session", "user", "attribute"])
@pytest.mark.parametrize("reverse", [False, True])
def test_live_cross_day_first_admission_uses_snapshot_priority_and_earliest_group(
    project, custom_eval_config, ch_seed, monkeypatch, unit, reverse
):
    import json

    from tracer.services.eval_tasks.sampling.query import live_scan_windows

    now = sampling_clock()
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    ordinary_at = midnight - timedelta(days=2) + timedelta(hours=12)
    rare_at = midnight - timedelta(days=1) + timedelta(hours=2)
    session = TraceSession.objects.create(project=project, name="cross-midnight")
    other = TraceSession.objects.create(project=project, name="unselected-control")
    user_id, other_user = uuid.uuid4(), uuid.uuid4()
    ordinary = seed_contact(
        project,
        session,
        {"positive": False, "locale": "ordinary", "contact_id": "one-contact"},
        traces=1,
        seed=False,
    )
    rare = seed_contact(
        project,
        session,
        {"positive": True, "locale": "z-earliest", "contact_id": "one-contact"},
        traces=1,
        seed=False,
    )
    later = seed_contact(
        project,
        session,
        {"positive": True, "locale": "a-later", "contact_id": "one-contact"},
        traces=1,
        seed=False,
    )
    control = seed_contact(
        project,
        other,
        {"positive": False, "locale": "control", "contact_id": "other-contact"},
        traces=1,
        seed=False,
    )
    for spans, event_at in (
        (ordinary, ordinary_at),
        (rare, rare_at),
        (later, rare_at + timedelta(hours=1)),
        (control, rare_at),
    ):
        for span in spans:
            span.start_time = event_at
            span.end_user_id = user_id if spans is not control else other_user
    seed_ch_spans([*ordinary, *rare, *later, *control])
    policy = {
        "version": 1,
        "mode": "live",
        "sampling_unit": unit,
        "window": {
            "start": (ordinary_at - timedelta(hours=1)).isoformat(),
            "end": None,
        },
        "strata": [
            {
                "name": "rare-positive",
                "filters": [{"key": "positive", "op": "in", "values": [True]}],
                "rate": 100,
            }
        ],
        "group_by": [{"key": "locale"}],
        "default": {"rate": 0},
    }
    if unit == "attribute":
        policy["unit_attribute"] = {"key": "contact_id"}
    task = EvalTask.objects.create(
        project=project,
        row_type="spans",
        run_type=RunType.CONTINUOUS,
        sampling_policy=policy,
        filters={},
    )
    task.evals.add(custom_eval_config)
    if reverse:
        monkeypatch.setattr(
            "tracer.services.eval_tasks.sampling.service.live_scan_windows",
            lambda *args: iter(reversed(list(live_scan_windows(*args)))),
        )
    reconcile(task)
    entries = list(EvalLogger.objects.filter(eval_task_id=str(task.id)))
    assert {entry.observation_span_id for entry in entries} == {
        span.id for span in [*ordinary, *rare, *later]
    }
    members = list(EvalSamplingMember.objects.filter(cohort__task=task))
    assert len(members) == 2
    chosen = next(member for member in members if member.selected)
    assert chosen.inclusion_probability == 1
    name, groups = json.loads(chosen.stratum)
    assert name == "rare-positive"
    assert json.loads(groups[0]) == [1, "z-earliest"]
    assert {
        entry.output_metadata["_task_selection"]["sampling"]["stratum"]
        for entry in entries
    } == {chosen.stratum}
    assert (
        next(member for member in members if not member.selected).inclusion_probability
        == 0
    )


def _rows(sql_params, **settings):
    return [
        row
        for block in stream_query(*sql_params, settings=settings or None)
        for row in block
    ]


def _read_rows(client, comment):
    client.command("SYSTEM FLUSH LOGS")
    found = client.query(
        "SELECT read_rows FROM system.query_log WHERE type = 'QueryFinish'"
        " AND log_comment = {comment:String} AND event_date >= yesterday()",
        parameters={"comment": comment},
    ).result_rows
    assert len(found) == 1, found
    return found[0][0]


def test_min_max_census_and_shortage_compile_to_exact_clickhouse_quotas(
    project, ch_seed
):
    from tracer.services.eval_tasks.sampling.policy import target_size

    cells = {"floor": 5, "cap": 6, "census": 2, "shortage": 3, "control": 4}
    for cell, units in cells.items():
        for index in range(units):
            session = TraceSession.objects.create(
                project=project, name=f"{cell}-{index}"
            )
            seed_contact(project, session, {"cell": cell}, traces=1)
    rules = {
        "floor": {"rate": 10, "min_n": 3},
        "cap": {"rate": 100, "max_n": 2},
        "census": {"rate": 10, "rare_below": 2, "max_n": 1},
        "shortage": {"target_n": 10, "min_n": 8},
    }
    task = EvalTask(
        project=project,
        row_type="spans",
        filters={},
        sampling_policy=make_policy(
            strata=[
                {
                    "name": name,
                    "filters": [{"key": "cell", "op": "in", "values": [name]}],
                    **rule,
                }
                for name, rule in rules.items()
            ],
            default={"rate": 50, "min_n": 1, "max_n": 1},
        ),
    )
    expected = {name: target_size(rule, cells[name]) for name, rule in rules.items()}
    expected["default"] = target_size({"rate": 50, "min_n": 1, "max_n": 1}, 4)
    # Floors, caps, census-under-cap and shortage, as the portable reference.
    assert expected == {"floor": 3, "cap": 2, "census": 1, "shortage": 3, "default": 1}
    preview = preview_policy(task)
    by_name = {json.loads(cell["stratum"])[0]: cell for cell in preview["strata"]}
    assert {name: cell["selected_units"] for name, cell in by_name.items()} == expected
    assert {name: cell["eligible_units"] for name, cell in by_name.items()} == {
        **{name: cells[name] for name in rules},
        "default": cells["control"],
    }
    policy, version = task_policy(task, now=timezone.now())
    members = _rows(SamplingQuery(task, policy, version, sampling_clock()).members())
    counts = {}
    for _unit, stratum, probability, population in members:
        name = json.loads(stratum)[0]
        counts[name] = counts.get(name, 0) + 1
        size = cells["control" if name == "default" else name]
        assert population == size
        assert probability == pytest.approx(expected[name] / size)
    assert counts == expected


@pytest.mark.parametrize("row_type", ["traces", "sessions"])
def test_remapped_session_expands_every_alias_row_for_trace_and_session_rows(
    project, custom_eval_config, ch_seed, row_type
):
    old_a, old_b, new = (uuid.uuid4() for _ in range(3))
    sessions = {
        value: TraceSession.objects.create(id=value, project=project, name=str(value))
        for value in (old_a, old_b, new)
    }
    client = _get_ch_client()
    try:
        client.insert(
            "trace_session_id_remap",
            [[old_a, new], [old_b, new]],
            column_names=["old_id", "new_id"],
        )
    finally:
        client.close()
    spans = []
    for value in (old_a, old_b, new):
        spans += seed_contact(project, sessions[value], {"group": "remapped"}, traces=1)
    other = TraceSession.objects.create(project=project, name="not-remapped")
    spans_other = seed_contact(project, other, {"group": "other"}, traces=1)
    task = EvalTask.objects.create(
        project=project,
        row_type=row_type,
        run_type=RunType.CONTINUOUS,
        filters={},
        sampling_policy=make_policy(
            strata=[
                {
                    "name": "remapped",
                    "filters": [{"key": "group", "op": "in", "values": ["remapped"]}],
                    "target_n": 1,
                }
            ],
            default={"target_n": 0},
        ),
    )
    task.evals.add(custom_eval_config)
    reconcile(task)
    survivor = f"session:{min(str(old_a), str(old_b))}"
    members = list(EvalSamplingMember.objects.filter(cohort__task=task))
    assert [member.unit_id for member in members] == [survivor]
    entries = list(EvalLogger.objects.filter(eval_task_id=str(task.id)))
    if row_type == "traces":
        assert {str(entry.trace_id) for entry in entries} == {
            str(span.trace_id) for span in spans
        }
        assert not {str(entry.trace_id) for entry in entries} & {
            str(span.trace_id) for span in spans_other
        }
    else:
        # Every alias canonicalizes to one session row.
        assert [str(entry.trace_session_id) for entry in entries] == [
            survivor.split(":", 1)[1]
        ]
    assert {
        entry.output_metadata["_task_selection"]["sampling"]["sampling_unit_id"]
        for entry in entries
    } == {survivor}
    assert all(
        entry.output_metadata["_task_selection"]["sampling"]["inclusion_probability"]
        == 1
        for entry in entries
    )


def test_closed_expansion_reads_only_its_member_page_with_identical_rows(
    project, ch_seed
):
    client = _get_ch_client()
    client.command("SYSTEM STOP MERGES spans")

    def add_contacts(first, count):
        # One part per contact, so a member page can skip every other contact.
        for index in range(first, first + count):
            session = TraceSession.objects.create(project=project, name=f"page-{index}")
            seed_contact(project, session, {"locale": "en"}, traces=2)

    def measure(task, policy, version, batch, label):
        now = sampling_clock()
        tag = f"{label}-{uuid.uuid4().hex}"
        full = _rows(
            SamplingQuery(task, policy, version, now).rows(),
            log_comment=f"sampling-full-{tag}",
        )
        page = _rows(
            SamplingQuery(task, policy, version, now).rows(unit_ids=batch),
            log_comment=f"sampling-page-{tag}",
        )
        return (
            full,
            page,
            _read_rows(client, f"sampling-full-{tag}"),
            _read_rows(client, f"sampling-page-{tag}"),
        )

    try:
        add_contacts(0, 12)
        task = EvalTask(
            project=project,
            row_type="spans",
            filters={},
            sampling_policy=make_policy(default={"target_n": 1}),
        )
        now = sampling_clock()
        policy, version = task_policy(task, now=now)
        batch = [
            row[0] for row in _rows(SamplingQuery(task, policy, version, now).members())
        ]
        assert len(batch) == 1
        full, page, full_read, page_read = measure(task, policy, version, batch, "a")
        expected = {(row[0], row[1]) for row in full if row[1] in batch}
        assert {(row[0], row[1]) for row in page} == expected
        assert len(page) == len(expected) == 4  # two traces of two spans
        assert len(full) == 48
        # 24 more contacts that are not on this member page.
        add_contacts(12, 24)
        full_more, page_more, full_read_more, page_read_more = measure(
            task, policy, version, batch, "b"
        )
        assert len(full_more) == 144
        assert {(row[0], row[1]) for row in page_more} == expected
        full_growth = full_read_more - full_read
        page_growth = page_read_more - page_read
        # The full-window form scans every new span at least once; the member
        # page skips their parts by its pushed-down unit IDs (bloom-filter
        # false positives may add a few rows, never a scan of the window).
        measured = (full_read, page_read, full_read_more, page_read_more)
        assert full_growth >= 96, measured
        assert page_growth * 8 <= full_growth, measured
        assert page_read_more * 2 < full_read_more, measured
    finally:
        client.command("SYSTEM START MERGES spans")
        client.close()


def _read_stats(client, comment):
    client.command("SYSTEM FLUSH LOGS")
    found = client.query(
        "SELECT read_rows, read_bytes FROM system.query_log WHERE type = 'QueryFinish'"
        " AND log_comment = {comment:String} AND event_date >= yesterday()",
        parameters={"comment": comment},
    ).result_rows
    assert len(found) == 1, found
    return found[0]


def test_remap_lookup_reads_touched_ids_without_another_latest_state_replay(
    project, ch_seed
):
    """ClickHouse inlines a CTE at every reference. The remap maps discover the
    ids they expand from a physical scan of the same immutable scope, so a
    statement replays the latest state once per ``sampling_source`` reference
    (eligibility and projection for this task), never once more per map."""
    payload = "p" * 8192
    spans = []
    for index in range(6):
        session = TraceSession.objects.create(project=project, name=f"bytes-{index}")
        spans += seed_contact(
            project,
            session,
            {"locale": f"l{index % 2}", "payload": payload},
            traces=1,
            seed=False,
        )
    seed_ch_spans(spans)
    task = EvalTask(
        project=project,
        row_type="spans",
        filters={},
        sampling_policy=make_policy(
            group_by=[{"key": "locale"}], default={"rate": 100}
        ),
    )
    policy, version = task_policy(task, now=timezone.now())
    tag = f"sampling-replays-{uuid.uuid4().hex}"
    cells = _rows(
        SamplingQuery(task, policy, version, sampling_clock()).preview(),
        log_comment=tag,
    )
    assert sum(cell[1] for cell in cells) == 6  # eligible units
    assert sum(cell[6] for cell in cells) == len(spans) == 12  # selected rows
    client = _get_ch_client()
    try:
        _read_rows_value, read_bytes = _read_stats(client, tag)
    finally:
        client.close()
    # Every latest-state replay reads the attribute map with the payload of
    # every span (uncompressed bytes); id-only remap scans never read it.
    one_replay = len(spans) * len(payload)
    measured = (read_bytes, one_replay)
    assert read_bytes >= one_replay, measured
    assert read_bytes < 3 * one_replay, measured


def _live_task(project, start, **overrides):
    return EvalTask.objects.create(
        project=project,
        row_type="spans",
        run_type=RunType.CONTINUOUS,
        filters={},
        sampling_policy={
            "version": 1,
            "sampling_unit": "session",
            "mode": "live",
            "window": {"start": start.isoformat(), "end": None},
            "default": {"rate": 100},
            **overrides,
        },
    )


def test_live_first_admission_proof_never_reads_outside_its_eligibility_hours(
    project, custom_eval_config, ch_seed
):
    history = timezone.now() - timedelta(days=40)
    session = TraceSession.objects.create(project=project, name="long-history")
    old_trace = str(uuid.uuid4())
    old_rows = [
        {
            "id": uuid.uuid4().hex[:16],
            "trace_id": old_trace,
            "project_id": str(project.id),
            "org_id": str(project.organization_id),
            "trace_session_id": session.id,
            "name": "old-history",
            "observation_type": "llm",
            "start_time": history,
            "created_at": history,
        }
        for _ in range(3000)
    ]
    seed_ch_spans(old_rows)
    recent = seed_contact(project, session, {}, traces=1)
    # The snapshot follows every arrival: the proof sees the recent rows and
    # must still skip the old history by its event hours alone.
    now = sampling_clock()
    task = _live_task(project, now - timedelta(days=1))
    policy, version = task_policy(task, now=now)
    tag = uuid.uuid4().hex
    decisions = _rows(
        SamplingQuery(task, policy, version, now).live_member_decisions(
            [f"session:{session.id}"]
        ),
        log_comment=f"sampling-proof-{tag}",
    )
    assert decisions == [(f"session:{session.id}", '["default",[]]', 1.0, True)]
    client = _get_ch_client()
    try:
        read = _read_rows(client, f"sampling-proof-{tag}")
    finally:
        client.close()
    # 3000 same-contact spans 40 days before the policy start live in another
    # day partition; the bounded proof never reads them.
    assert read < len(old_rows), read
    assert len(recent) == 2  # one trace of two spans inside the eligibility hours


@pytest.mark.parametrize("bound", ["rows", "days"])
def test_live_first_admission_bound_fails_explicitly_without_partial_decisions(
    project, custom_eval_config, ch_seed, settings, bound
):
    from tracer.services.eval_tasks.sampling.errors import SamplingProofBoundExceeded

    now = sampling_clock()
    session = TraceSession.objects.create(project=project, name="bounded-proof")
    seed_contact(project, session, {}, traces=2)
    task = _live_task(project, now - timedelta(days=3))
    task.evals.add(custom_eval_config)
    if bound == "rows":
        settings.EVAL_SAMPLING_LIVE_PROOF_MAX_ROWS = 1
    else:
        settings.EVAL_SAMPLING_LIVE_PROOF_MAX_DAYS = 1
    with pytest.raises(SamplingProofBoundExceeded) as raised:
        reconcile(task)
    assert "no partial decision" in str(raised.value)
    assert not EvalSamplingMember.objects.filter(cohort__task=task).exists()
    assert not EvalLogger.objects.filter(eval_task_id=str(task.id)).exists()
    task.refresh_from_db()
    assert task.continuous_cursor is None
    assert task.sampling_cohorts.get().lease_token is None


@pytest.mark.parametrize("kind", ["unit", "group"])
def test_oversized_identity_fails_with_an_explicit_named_cause(
    project, ch_seed, auth_client, kind
):
    from django.core.cache import cache

    from tracer.services.eval_tasks.sampling.errors import SamplingValueTooLarge

    cache.clear()
    long_value = "x" * 3000
    attributes = (
        {"contact_id": long_value, "locale": "en"}
        if kind == "unit"
        else {"contact_id": "ok", "locale": long_value}
    )
    seed_contact(project, None, attributes, traces=1)
    seed_contact(project, None, {"contact_id": "fine", "locale": "fr"}, traces=1)
    policy = make_policy(
        sampling_unit="attribute",
        unit_attribute={"key": "contact_id"},
        group_by=[{"key": "locale"}],
        default={"rate": 100},
    )
    task = EvalTask(
        project=project, row_type="spans", filters={}, sampling_policy=policy
    )
    with pytest.raises(SamplingValueTooLarge) as raised:
        preview_policy(task)
    named = "'contact_id'" if kind == "unit" else "'locale'"
    assert named in str(raised.value)
    assert long_value not in str(raised.value)
    response = auth_client.post(
        "/tracer/eval-task/sampling_preview/",
        {"project": str(project.id), "sampling_policy": policy},
        format="json",
    )
    assert response.status_code == 400
    assert named in response.content.decode()


def test_closed_freeze_settle_margin_excludes_unsettled_arrivals(
    project, custom_eval_config, ch_seed, settings
):
    settings.EVAL_SAMPLING_CLOSED_SETTLE_SECONDS = 600
    now = sampling_clock()
    settled = TraceSession.objects.create(project=project, name="settled")
    unsettled = TraceSession.objects.create(project=project, name="unsettled")
    spans = {}
    for session, arrived in (
        (settled, now - timedelta(minutes=20)),
        (unsettled, now - timedelta(minutes=2)),
    ):
        rows = seed_contact(project, session, {}, traces=1, seed=False)
        for span in rows:
            span.start_time = now - timedelta(minutes=40)
            span.created_at = span.updated_at = arrived
        seed_ch_spans(rows, version_from_created_at=True)
        spans[session.id] = rows
    task = EvalTask.objects.create(
        project=project,
        row_type="spans",
        run_type=RunType.CONTINUOUS,
        filters={},
        sampling_policy=make_policy(
            window={
                "start": (now - timedelta(hours=1)).isoformat(),
                "end": (now - timedelta(minutes=30)).isoformat(),
            },
            default={"rate": 100},
        ),
    )
    task.evals.add(custom_eval_config)
    reconcile(task)
    cohort = task.sampling_cohorts.get()
    assert now - timedelta(minutes=11) < cohort.frozen_at < now - timedelta(minutes=9)
    assert list(
        EvalSamplingMember.objects.filter(cohort=cohort).values_list(
            "unit_id", flat=True
        )
    ) == [f"session:{settled.id}"]
    assert set(
        EvalLogger.objects.filter(eval_task_id=str(task.id)).values_list(
            "observation_span_id", flat=True
        )
    ) == {span.id for span in spans[settled.id]}


def test_identity_merge_after_freeze_is_a_new_unit_and_never_rewrites_decisions(
    project, custom_eval_config, ch_seed
):
    first = uuid.UUID("f" + str(uuid.uuid4())[1:])
    target = uuid.uuid4()
    later = uuid.UUID("0" + str(uuid.uuid4())[1:])
    for value in (first, target):
        TraceSession.objects.create(id=value, project=project, name=str(value))
    client = _get_ch_client()
    try:
        client.insert(
            "trace_session_id_remap",
            [[first, target]],
            column_names=["old_id", "new_id"],
        )
    finally:
        client.close()
    rows = seed_contact(
        project, TraceSession.objects.get(id=first), {}, traces=1
    ) + seed_contact(project, TraceSession.objects.get(id=target), {}, traces=1)
    task = EvalTask.objects.create(
        project=project,
        row_type="spans",
        run_type=RunType.CONTINUOUS,
        filters={},
        sampling_policy=make_policy(default={"target_n": 1}),
    )
    task.evals.add(custom_eval_config)
    reconcile(task)
    member = EvalSamplingMember.objects.get(cohort__task=task)
    assert member.unit_id == f"session:{first}"
    assert EvalLogger.objects.filter(eval_task_id=str(task.id)).count() == 4
    # A later merge makes an older alias canonical for the same contact.
    client = _get_ch_client()
    try:
        client.insert(
            "trace_session_id_remap",
            [[later, target]],
            column_names=["old_id", "new_id"],
        )
    finally:
        client.close()
    EvalTask.objects.filter(pk=task.pk).update(continuous_cursor=sampling_clock())
    late = seed_contact(project, TraceSession.objects.get(id=target), {}, traces=1)
    reconcile(task)
    member.refresh_from_db()
    # Documented semantics: the decision keeps its decision-time identity; the
    # merged identity is a unit first seen after the freeze, so its pending
    # work is dropped and its late rows are not admitted.
    assert member.unit_id == f"session:{first}" and member.selected
    assert EvalSamplingMember.objects.filter(cohort__task=task).count() == 1
    assert not EvalLogger.objects.filter(eval_task_id=str(task.id)).exists()
    archived = EvalLogger.all_objects.filter(eval_task_id=str(task.id))
    assert {entry.observation_span_id for entry in archived} == {
        span.id for span in rows
    }
    assert all(entry.deleted for entry in archived)
    assert not archived.filter(
        observation_span_id__in=[span.id for span in late]
    ).exists()


def test_preview_and_execution_ignore_a_hidden_task_date_range(
    project, custom_eval_config, ch_seed, auth_client
):
    from django.core.cache import cache

    cache.clear()
    early = TraceSession.objects.create(project=project, name="early-in-window")
    late = TraceSession.objects.create(project=project, name="late-in-window")
    now = sampling_clock()
    for session, offset in ((early, 50), (late, 10)):
        rows = seed_contact(project, session, {}, traces=1, seed=False)
        for span in rows:
            span.start_time = now - timedelta(minutes=offset)
        seed_ch_spans(rows)
    policy = make_policy(default={"rate": 100})
    hidden = {
        "date_range": [
            (now - timedelta(minutes=20)).isoformat(),
            (now - timedelta(minutes=5)).isoformat(),
        ],
        "date_preset": "custom",
    }
    response = auth_client.post(
        "/tracer/eval-task/sampling_preview/",
        {"project": str(project.id), "sampling_policy": policy, "filters": hidden},
        format="json",
    )
    assert response.status_code == 200, response.content
    previewed = response.json()["result"]["strata"]
    assert sum(cell["eligible_units"] for cell in previewed) == 2
    created = auth_client.post(
        "/tracer/eval-task/",
        {
            "project": str(project.id),
            "name": "historical policy",
            "run_type": "historical",
            "sampling_rate": 100,
            "sampling_policy": policy,
            "evals": [str(custom_eval_config.id)],
            "filters": {"project_id": str(project.id), **hidden},
        },
        format="json",
    )
    assert created.status_code == 200, created.data
    task = EvalTask.objects.get(pk=created.data["result"]["id"])
    assert "date_range" not in task.filters
    reconcile(task)
    assert set(
        EvalSamplingMember.objects.filter(cohort__task=task).values_list(
            "unit_id", flat=True
        )
    ) == {f"session:{early.id}", f"session:{late.id}"}


@pytest.mark.parametrize("start", [0, 87.5])
def test_coordinated_prn_sql_matches_reference_nests_and_persists(
    project, custom_eval_config, ch_seed, start
):
    """Y1: ClickHouse's coordinated selection equals the portable PRN reference.

    Closed bottom-k and live Bernoulli (two rules; at start 87.5% the 25%
    interval wraps past the top of the hash circle) agree unit for unit with
    ``prn_order_key``/``prn_selects``; quotas nest; a reconcile persists the
    shared seed and marks every result as coordinated.
    """
    from tracer.models.eval_sampling import EvalSamplingCohort
    from tracer.services.eval_tasks.sampling.policy import prn_order_key, prn_selects

    units, spans, gold = [], [], set()
    for index in range(40):
        session = TraceSession.objects.create(project=project, name=f"prn-{index}")
        tier = "gold" if index % 4 == 0 else "std"
        spans += seed_contact(project, session, {"tier": tier}, traces=1, seed=False)
        units.append(f"session:{session.id}")
        if tier == "gold":
            gold.add(units[-1])
    seed_ch_spans(spans)
    coordination = {"coordination": "project"}
    if start:
        coordination["prn_start"] = start
    seed = f"prn-sha256-v1:{project.id}:"
    reference = sorted(units, key=lambda unit: (prn_order_key(seed, unit, start), unit))

    def closed_members(target):
        task = EvalTask(
            project=project,
            row_type="spans",
            filters={},
            sampling_policy=make_policy(default={"target_n": target}, **coordination),
        )
        policy, version = task_policy(task, now=timezone.now())
        statement = SamplingQuery(task, policy, version, sampling_clock()).members()
        rows = [row for block in stream_query(*statement) for row in block]
        assert {row[2] for row in rows} == {target / 40}  # p = k / N
        return version, {row[0] for row in rows}

    small_version, small = closed_members(5)
    large_version, large = closed_members(12)
    assert small_version != large_version  # each edit is its own cohort ...
    assert small == set(reference[:5])  # ... drawn from the same numbers
    assert large == set(reference[:12])
    assert small < large

    now = sampling_clock()
    live_task = EvalTask(
        project=project,
        row_type="spans",
        run_type=RunType.CONTINUOUS,
        filters={},
        sampling_policy={
            "version": 1,
            "sampling_unit": "session",
            "mode": "live",
            "window": {"start": (now - timedelta(days=1)).isoformat(), "end": None},
            "strata": [
                {
                    "name": "gold",
                    "filters": [{"key": "tier", "op": "in", "values": ["gold"]}],
                    "rate": 50,
                }
            ],
            "default": {"rate": 25},
            **coordination,
        },
    )
    policy, version = task_policy(live_task, now=now)
    decided = {}
    for block in stream_query(*SamplingQuery(live_task, policy, version, now).rows()):
        for _row, unit, _stratum, probability, selected in block:
            assert probability == (0.5 if unit in gold else 0.25)
            assert decided.setdefault(str(unit), bool(selected)) == bool(selected)
    assert set(decided) == set(units)
    assert decided == {
        unit: prn_selects(seed, unit, 50 if unit in gold else 25, start)
        for unit in units
    }

    task = EvalTask.objects.create(
        project=project,
        row_type="spans",
        run_type=RunType.CONTINUOUS,
        filters={},
        sampling_policy=make_policy(default={"target_n": 5}, **coordination),
    )
    task.evals.add(custom_eval_config)
    reconcile(task)
    cohort = EvalSamplingCohort.objects.get(task=task)
    assert cohort.selection_context["coordination"] == {
        "mode": "project",
        "panel": "",
        "prn_start": start,
        "seed": seed,
    }
    assert {m.unit_id for m in cohort.members.all()} == set(reference[:5])
    entries = list(EvalLogger.objects.filter(eval_task_id=str(task.id)))
    assert len(entries) == 10  # 5 contacts x 1 trace x 2 spans
    assert {
        entry.output_metadata["_task_selection"]["sampling"]["coordination"]
        for entry in entries
    } == {"project"}
