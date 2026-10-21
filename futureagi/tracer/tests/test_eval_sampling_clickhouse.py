"""Real CH25 integration: no mocked query compiler or membership selector.

Run on the test sidecar protected by conftest's target guard, never a live DB.
"""

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
