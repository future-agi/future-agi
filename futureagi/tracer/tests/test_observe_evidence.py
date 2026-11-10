"""Reading Observe evidence for an import (``tracer.selectors.observe_evidence``):
a session reads as one exchange per trace, a call as its recorded transcript,
deleted sources stay out, and every part says what could not be read."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from model_hub.models.choices import AnnotationTypeChoices
from model_hub.models.develop_annotations import AnnotationsLabels
from model_hub.models.score import Score
from tracer.models.observation_span import ObservationSpan
from tracer.models.project import Project
from tracer.models.trace import Trace
from tracer.selectors.observe_evidence import (
    MAX_TOOL_CALLS,
    MAX_TURNS,
    TOOL_FIELD_CHARS,
    TURN_CHARS,
    observe_evidence,
)

T0 = datetime(2026, 11, 10, 9, 0, tzinfo=UTC)


def _id() -> str:
    return str(uuid.uuid4())


def _root(trace_id, *, session=None, at=T0, kind="chain", **fields):
    return SimpleNamespace(
        trace_id=trace_id,
        trace_session_id=session,
        observation_type=kind,
        start_time=at,
        end_time=at + timedelta(seconds=3),
        input=fields.get("input", ""),
        output=fields.get("output", ""),
        attrs_string=fields.get("attrs", {}),
        attrs_number={},
        attrs_bool={},
        attributes_extra=fields.get("extra", ""),
    )


def _step(trace_id, name, *, kind="tool", status="OK", at=T0, **fields):
    text_in, text_out = fields.get("input", '{"q": 1}'), fields.get("output", "ok")
    return {
        "trace_id": trace_id,
        "id": _id(),
        "name": name,
        "observation_type": kind,
        "start_time": at,
        "status": status,
        "status_message": fields.get("message", ""),
        "input": text_in,
        "output": text_out,
        "input_chars": fields.get("input_chars", len(text_in)),
        "output_chars": fields.get("output_chars", len(text_out)),
    }


def _read(
    project, source_kind, ids, roots, activity=(), activity_error=None, span_traces=None
):
    """``observe_evidence`` over a stand-in reader. Each activity row's
    ``in_trace`` defaults to how many rows its trace has (nothing left out)."""
    rows = [dict(row) for row in activity]
    for row in rows:
        row.setdefault(
            "in_trace", sum(1 for r in rows if r["trace_id"] == row["trace_id"])
        )
    reader = MagicMock()
    reader.__enter__ = MagicMock(return_value=reader)
    reader.__exit__ = MagicMock(return_value=False)
    reader.roots_by_session_ids.return_value = roots
    reader.roots_by_trace_ids.return_value = roots
    reader.trace_ids_for_span_ids.return_value = span_traces or {}
    if activity_error is not None:
        reader.activity_by_trace_ids.side_effect = activity_error
    else:
        reader.activity_by_trace_ids.return_value = rows
    with patch("tracer.services.clickhouse.v2.get_reader", return_value=reader):
        return observe_evidence(project, source_kind, ids), reader


def _label(organization, workspace, project):
    return AnnotationsLabels.objects.create(
        name=f"Label {uuid.uuid4().hex[:6]}",
        type=AnnotationTypeChoices.THUMBS_UP_DOWN.value,
        settings={},
        organization=organization,
        workspace=workspace,
        project=project,
    )


@pytest.fixture
def project(observe_project):
    return observe_project


@pytest.mark.django_db
class TestSessions:
    def test_one_exchange_per_trace_oldest_first(self, project):
        session, first, second = _id(), _id(), _id()
        roots = [
            _root(
                second,
                session=session,
                at=T0 + timedelta(minutes=1),
                input="Where is my refund?",
                output=json.dumps(
                    [
                        {"role": "user", "content": "Where is my refund?"},
                        {"role": "assistant", "content": "Issued again."},
                    ]
                ),
            ),
            _root(
                first,
                session=session,
                input='{"messages": []}',
                attrs={"input.value": "Refund order 42", "output.value": "Done."},
            ),
        ]

        found, reader = _read(project, "session", [session], roots)

        evidence = found[session]
        assert [(t.role, t.text) for t in evidence.turns] == [
            ("user", "Refund order 42"),
            ("agent", "Done."),
            ("user", "Where is my refund?"),
            ("agent", "Issued again."),
        ]
        assert evidence.trace_ids == (first, second)
        assert evidence.started_at == T0
        assert evidence.tool_calls == ()
        reader.roots_by_session_ids.assert_called_once_with(
            [session], project_id=str(project.id)
        )

    def test_keyed_by_the_id_as_asked(self, project):
        session = uuid.uuid4()
        roots = [_root(_id(), session=str(session), input="hi", output="hello")]

        found, _ = _read(project, "session", [session.hex], roots)

        assert list(found) == [session.hex]

    def test_an_id_with_nothing_retained_is_absent(self, project):
        found, _ = _read(project, "session", [_id()], [])

        assert found == {}

    def test_a_deleted_trace_stays_out(self, observe_project, project):
        session = _id()
        deleted = Trace.objects.create(project=observe_project, name="gone")
        Trace.objects.filter(id=deleted.id).update(deleted=True)
        roots = [
            _root(str(deleted.id), session=session, input="secret", output="x"),
            _root(_id(), session=session, input="kept", output="y"),
        ]

        found, _ = _read(project, "session", [session], roots)

        assert [t.text for t in found[session].turns] == ["kept", "y"]


@pytest.mark.django_db
class TestCalls:
    def test_a_call_reads_its_transcript_without_tool_messages(self, project):
        call = _id()
        transcript = [
            {"role": "user", "content": "I was charged twice."},
            {"role": "tool_calls", "content": "lookup_order"},
            {"role": "assistant", "content": "Let me check."},
        ]
        roots = [
            _root(
                call,
                kind="conversation",
                extra=json.dumps({"provider_transcript": transcript}),
            )
        ]

        found, reader = _read(project, "call", [call], roots)

        assert [(t.role, t.text, t.at) for t in found[call].turns] == [
            ("user", "I was charged twice.", None),
            ("agent", "Let me check.", None),
        ]
        reader.roots_by_trace_ids.assert_called_once_with(
            [call], project_id=str(project.id), include_heavy=True
        )

    def test_a_transcript_kept_as_a_string_is_read(self, project):
        """As collector ingest stores a Retell call: a JSON string in
        attrs_string, here a voicemail where only the caller's side spoke."""
        call = _id()
        greeting = "Hi. You've reached the front desk. Please leave a message."
        roots = [
            _root(
                call,
                kind="conversation",
                attrs={
                    "provider_transcript": json.dumps(
                        [{"role": "user", "content": greeting}]
                    ),
                    "conversation.transcript.0.message.role": "user",
                    "conversation.transcript.0.message.content": greeting,
                    "raw_log": json.dumps({"call_id": "c1"}),
                },
                extra="{}",
            )
        ]

        found, _ = _read(project, "call", [call], roots)

        assert [(t.role, t.text) for t in found[call].turns] == [("user", greeting)]

    def test_a_trace_that_is_not_a_call_is_never_read_as_one(self, project):
        trace = _id()
        roots = [_root(trace, kind="chain", input="hi", output="hello")]

        found, _ = _read(project, "call", [trace], roots)

        assert found == {}


@pytest.mark.django_db
class TestToolCalls:
    def test_tool_calls_with_their_inputs_outputs_and_failures(self, project):
        session, trace = _id(), _id()
        roots = [_root(trace, session=session, input="refund", output="ok")]
        activity = [
            _step(
                trace,
                "lookup_order",
                input='{"order": 42}',
                output='{"refunded": true}',
            ),
            _step(
                trace,
                "issue_refund",
                status="ERROR",
                at=T0 + timedelta(seconds=1),
                message="already refunded",
            ),
            _step(trace, "llm", kind="llm", status="ERROR"),
        ]

        found, reader = _read(project, "session", [session], roots, activity)

        calls = found[session].tool_calls
        assert [(c.name, c.failed, c.error) for c in calls] == [
            ("lookup_order", False, ""),
            ("issue_refund", True, "already refunded"),
        ]
        assert calls[0].input == '{"order": 42}'
        assert calls[0].output == '{"refunded": true}'
        assert (
            reader.activity_by_trace_ids.call_args.kwargs["per_trace"] == MAX_TOOL_CALLS
        )

    def test_unreadable_tool_calls_read_as_none_not_as_no_calls(self, project):
        session = _id()
        roots = [_root(_id(), session=session, input="hi", output="hello")]

        found, _ = _read(
            project, "session", [session], roots, activity_error=RuntimeError("CH")
        )

        assert found[session].tool_calls is None
        assert found[session].turns

    def test_cut_values_say_so(self, project):
        session, trace = _id(), _id()
        roots = [_root(trace, session=session, input="x", output="y")]
        activity = [
            _step(trace, "search", input_chars=TOOL_FIELD_CHARS + 1, output_chars=1)
        ]
        activity += [
            _step(trace, f"t{i}", at=T0 + timedelta(seconds=i + 1))
            for i in range(MAX_TOOL_CALLS - 1)
        ]
        # The trace held more steps than the read returned.
        for row in activity:
            row["in_trace"] = MAX_TOOL_CALLS + 5

        found, _ = _read(project, "session", [session], roots, activity)

        evidence = found[session]
        assert evidence.tool_calls[0].input_truncated is True
        assert evidence.tool_calls[0].output_truncated is False
        assert evidence.tool_calls_truncated is True


@pytest.mark.django_db
def test_long_turns_and_many_turns_are_cut(project):
    session = _id()
    roots = [
        _root(
            _id(),
            session=session,
            at=T0 + timedelta(seconds=i),
            input=f"q{i}",
            output="a",
        )
        for i in range(MAX_TURNS)
    ]
    roots[0].input = "word " * (TURN_CHARS // 5 + 5)

    found, _ = _read(project, "session", [session], roots)

    evidence = found[session]
    assert len(evidence.turns) == MAX_TURNS
    assert evidence.turns_truncated is True
    assert evidence.turns[0].truncated is True
    assert len(evidence.turns[0].text) == TURN_CHARS
    # The first and last turns stay; the middle is left out and counted.
    assert evidence.turns[-1].text == "a"
    assert evidence.turns_omitted == 2 * MAX_TURNS - MAX_TURNS


@pytest.mark.django_db
def test_media_never_enters_the_text(project):
    session = _id()
    blob = "data:image/png;base64," + "A" * 300
    roots = [_root(_id(), session=session, input=f"see {blob}", output="ok")]

    found, _ = _read(project, "session", [session], roots)

    assert found[session].turns[0].text == "see [media]"


@pytest.mark.django_db
def test_labels_on_the_session_and_its_traces_are_input_evidence(
    organization, workspace, observe_project, trace_session, user
):
    trace = Trace.objects.create(
        project=observe_project, session=trace_session, name="turn"
    )
    label = AnnotationsLabels.objects.create(
        name="Resolved",
        type=AnnotationTypeChoices.THUMBS_UP_DOWN.value,
        settings={},
        organization=organization,
        workspace=workspace,
        project=observe_project,
    )
    for target in ({"trace_session": trace_session}, {"trace": trace}):
        Score.no_workspace_objects.create(
            source_type="trace_session" if "trace_session" in target else "trace",
            label=label,
            value={"value": "down"},
            notes="promised a second refund",
            annotator=user,
            organization=organization,
            workspace=workspace,
            **target,
        )
    session = str(trace_session.id)
    roots = [_root(str(trace.id), session=session, input="hi", output="ok")]

    found, _ = _read(observe_project, "session", [session], roots)

    assert sorted((lab.target, lab.notes) for lab in found[session].labels) == [
        ("session", "promised a second refund"),
        ("trace", "promised a second refund"),
    ]
    assert found[session].labels[0].value == {"value": "down"}


@pytest.mark.django_db
def test_exactly_the_cap_is_not_called_truncated(project):
    session, trace = _id(), _id()
    roots = [_root(trace, session=session, input="x", output="y")]
    activity = [
        _step(trace, f"t{i}", at=T0 + timedelta(seconds=i))
        for i in range(MAX_TOOL_CALLS)
    ]

    found, _ = _read(project, "session", [session], roots, activity)

    assert len(found[session].tool_calls) == MAX_TOOL_CALLS
    assert found[session].tool_calls_truncated is False


@pytest.mark.django_db
def test_an_unparseable_transcript_reads_as_unreadable(project):
    call = _id()
    roots = [
        _root(
            call,
            kind="conversation",
            attrs={"provider_transcript": "[{not json"},
            extra="{}",
        )
    ]

    found, _ = _read(project, "call", [call], roots)

    assert found[call].turns == ()
    assert found[call].transcript_unreadable is True


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("output", "reply"),
    [
        (
            {
                "messages": [
                    {"role": "user", "content": "hi"},
                    {"role": "assistant", "content": "hello"},
                ]
            },
            "hello",
        ),
        (
            [{"role": "user", "content": "hi"}, {"role": "model", "content": "hey"}],
            "hey",
        ),
        ([{"role": "user", "content": "only the user spoke"}], None),
        (
            [{"role": "user", "content": "hi"}, {"role": "assistant", "content": None}],
            None,
        ),
        ({"content": "plain reply"}, "plain reply"),
    ],
    ids=[
        "messages-object",
        "gemini-model-role",
        "no-agent-message",
        "null-content",
        "content-key",
    ],
)
def test_the_agent_turn_never_holds_the_users_words(project, output, reply):
    session = _id()
    roots = [_root(_id(), session=session, input="hi", output=json.dumps(output))]

    found, _ = _read(project, "session", [session], roots)

    agent = [t.text for t in found[session].turns if t.role == "agent"]
    assert agent == ([reply] if reply else [])


@pytest.mark.django_db
def test_span_labels_are_read_through_their_trace(
    organization, workspace, observe_project, user
):
    """A span's annotation often leaves trace_id empty; it belongs to the
    source whose trace holds the span."""
    trace = Trace.objects.create(project=observe_project, name="turn")
    span = ObservationSpan.objects.create(
        id=f"span_{uuid.uuid4().hex[:16]}",
        project=observe_project,
        trace=trace,
        name="lookup",
        observation_type="tool",
        start_time=T0,
    )
    label = _label(organization, workspace, observe_project)
    Score.no_workspace_objects.create(
        source_type="observation_span",
        observation_span=span,
        label=label,
        value={"value": "down"},
        notes="looked up the wrong order",
        annotator=user,
        organization=organization,
        workspace=workspace,
        tracer_project_id=observe_project.id,
    )
    call = str(trace.id)
    roots = [_root(call, kind="conversation", attrs={"provider_transcript": "[]"})]

    found, reader = _read(
        observe_project, "call", [call], roots, span_traces={span.id: call}
    )

    assert [(lab.target, lab.notes) for lab in found[call].labels] == [
        ("span", "looked up the wrong order")
    ]
    reader.trace_ids_for_span_ids.assert_called_once_with(
        [span.id], project_id=str(observe_project.id)
    )


@pytest.mark.django_db
def test_labels_of_another_project_stay_out(
    organization, workspace, observe_project, trace_session, user
):
    other = Project.objects.create(
        name="Other",
        organization=organization,
        workspace=workspace,
        model_type=observe_project.model_type,
        trace_type="observe",
    )
    trace = Trace.objects.create(
        project=observe_project, session=trace_session, name="t"
    )
    Score.no_workspace_objects.create(
        source_type="trace",
        trace=trace,
        label=_label(organization, workspace, other),
        value={"value": "up"},
        notes="from the other project",
        annotator=user,
        organization=organization,
        workspace=workspace,
        tracer_project_id=other.id,
    )
    session = str(trace_session.id)
    roots = [_root(str(trace.id), session=session, input="hi", output="ok")]

    found, _ = _read(observe_project, "session", [session], roots)

    assert found[session].labels == ()


@pytest.mark.django_db
def test_a_late_failed_tool_call_survives_the_cap(project):
    session, trace = _id(), _id()
    roots = [_root(trace, session=session, input="x", output="y")]
    activity = [
        _step(trace, f"t{i}", at=T0 + timedelta(seconds=i))
        for i in range(MAX_TOOL_CALLS + 20)
    ]
    activity.append(
        _step(
            trace,
            "refund",
            status="ERROR",
            at=T0 + timedelta(hours=1),
            message="already refunded",
        )
    )

    found, _ = _read(project, "session", [session], roots, activity)

    calls = found[session].tool_calls
    assert len(calls) == MAX_TOOL_CALLS
    assert (calls[-1].name, calls[-1].failed) == ("refund", True)
    assert [c.at for c in calls] == sorted(c.at for c in calls)
    assert found[session].tool_calls_truncated is True
