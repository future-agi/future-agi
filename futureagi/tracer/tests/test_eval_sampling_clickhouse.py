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
from tracer.services.eval_tasks.sampling.query import SamplingQuery, stream_query
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


def seed_contact(project, session, attributes, *, traces=2, observation_type="llm"):
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
                    project, session, attributes
                )
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
            *SamplingQuery(task, policy, version, timezone.now()).members()
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
            *SamplingQuery(task, policy, version, timezone.now()).members()
        )
        for row in block
    ]
    assert sorted(selected) == sorted(
        sorted(ids, key=lambda unit: unit_hash(version, unit))[:3]
    )
