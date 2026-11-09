"""T6 RED-first regressions: real ORM + HTTP views, external reads replaced only.

Auth lookups, replay validation/persistence, and response extraction are real.
The in-memory telemetry boundary deliberately returns foreign data for unscoped
reads, so a missing project argument cannot pass by returning an empty fixture.
This module is authored for the integration runner; it is not run by the writer.
"""

import json
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIRequestFactory, force_authenticate

from accounts.models import Organization
from accounts.models.workspace import Workspace
from model_hub.models.ai_model import AIModel
from model_hub.models.develop_dataset import Dataset, Row
from simulate.models import CallExecution, RunTest, Scenarios
from simulate.models.test_execution import TestExecution as Execution
from simulate.utils import session_comparison as comparison
from simulate.views.session_comparison_chat_sim import SessionComparisonChatSimView
from tfc.middleware.workspace_context import set_workspace_context, workspace_context
from tracer.models.project import Project
from tracer.models.replay_session import ReplaySession
from tracer.models.trace import Trace
from tracer.models.trace_session import TraceSession
from tracer.services.clickhouse import v2
from tracer.services.clickhouse.v2 import trace_session_dict_reader
from tracer.services.clickhouse.v2.span_reader import CHSpanReader
from tracer.utils import replay_session as replay_utils
from tracer.utils.otel import CallAttributes
from tracer.views import replay_session as replay_views

pytestmark = pytest.mark.django_db


@pytest.fixture
def world(organization, workspace):
    with workspace_context(None, organization=None, user=None):
        set_workspace_context(require_workspace=False)
        user = get_user_model().objects.create_user(
            email=f"t6-{uuid.uuid4().hex}@example.test"
        )
        user.organization = organization
        user.workspace = workspace
        other_org = Organization.objects.create(name="T6 foreign organization")
        other_ws = Workspace.no_workspace_objects.create(
            name="T6 other workspace",
            organization=organization,
            created_by=user,
            is_default=False,
        )
        foreign_ws = Workspace.no_workspace_objects.create(
            name="T6 foreign workspace",
            organization=other_org,
            created_by=user,
            is_default=False,
        )

        def resources(org, ws, label):
            project = Project.no_workspace_objects.create(
                name=f"T6 {label}",
                organization=org,
                workspace=ws,
                model_type=AIModel.ModelTypes.GENERATIVE_LLM,
                trace_type="observe",
            )
            session = TraceSession.no_workspace_objects.create(project=project)
            trace = Trace.no_workspace_objects.create(
                project=project,
                session=session,
                name=f"T6 trace {label}",
                input=f"{label} input",
                output=f"{label} output",
            )
            dataset = Dataset.no_workspace_objects.create(
                name=f"T6 dataset {label}",
                organization=org,
                workspace=ws,
            )
            row = Row.no_workspace_objects.create(
                dataset=dataset,
                order=0,
                metadata={"trace_id": str(trace.id)},
            )
            scenario = Scenarios.no_workspace_objects.create(
                name=f"T6 scenario {label}",
                source="synthetic fixture",
                organization=org,
                workspace=ws,
                dataset=dataset,
            )
            run = RunTest.no_workspace_objects.create(
                name=f"T6 run {label}",
                organization=org,
                workspace=ws,
            )
            execution = Execution.no_workspace_objects.create(run_test=run)
            call = CallExecution.no_workspace_objects.create(
                test_execution=execution,
                scenario=scenario,
                row_id=row.id,
                status=CallExecution.CallStatus.COMPLETED,
                simulation_call_type=CallExecution.SimulationCallType.VOICE,
                duration_seconds=20,
                provider_call_data={
                    "vapi": {
                        "messages": [
                            {"role": "user", "message": "simulated input"},
                            {"role": "bot", "message": "simulated output"},
                        ]
                    }
                },
                conversation_metrics_data={"output_tokens": 10, "turn_count": 1},
            )
            return SimpleNamespace(
                project=project,
                session=session,
                trace=trace,
                dataset=dataset,
                row=row,
                scenario=scenario,
                run=run,
                execution=execution,
                call=call,
                label=label,
            )

        yield SimpleNamespace(
            user=user,
            organization=organization,
            workspace=workspace,
            own=resources(organization, workspace, "own"),
            peer=resources(organization, other_ws, "peer"),
            foreign=resources(other_org, foreign_ws, "foreign"),
            second=resources(organization, workspace, "second"),
        )


def request(world, method="get", body=None):
    req = getattr(APIRequestFactory(), method)(
        "/t6-regression/", body or {}, format="json"
    )
    req.organization = world.organization
    req.workspace = world.workspace
    force_authenticate(req, user=world.user)
    return req


def compare(world, call=None):
    return SessionComparisonChatSimView.as_view()(
        request(world), call_execution_id=(call or world.own.call).id
    )


def result(response):
    return response.data.get("result", response.data)


@pytest.fixture
def telemetry(world, monkeypatch):
    class Reader:
        def __init__(self):
            self.reads = []
            self.membership_reads = []
            self.resources = (world.foreign, world.peer, world.own, world.second)
            self.trace_projects = {
                str(r.trace.id): [str(r.project.id)] for r in self.resources
            }
            self.trace_projects[str(world.own.trace.id)].insert(
                0, str(world.foreign.project.id)
            )
            self.sessions = {
                str(r.session.id): str(r.project.id) for r in self.resources
            }
            self.session_traces = {
                str(r.session.id): [str(r.trace.id)] for r in self.resources
            }
            self.remaps = {}
            self.voice = True

        def newest_trace_projects(self, trace_ids, project_ids):
            ids, pids = tuple(map(str, trace_ids)), tuple(map(str, project_ids))
            self.membership_reads.append(("trace", ids, pids))
            return {
                tid: next(pid for pid in self.trace_projects[tid] if pid in pids)
                for tid in ids
                if any(pid in pids for pid in self.trace_projects.get(tid, []))
            }

        def session_trace_ids(self, project_id, session_id):
            self.reads.append(("session_traces", str(session_id), str(project_id)))
            sid = self.remaps.get(str(session_id), str(session_id))
            if self.sessions.get(sid) != str(project_id):
                return []
            return self.session_traces.get(sid, [])

        def list_by_trace_ids(self, trace_ids, *, project_id=None, **kwargs):
            return [
                span
                for tid in trace_ids
                for span in self.list_by_trace(tid, project_id=project_id)
            ]

        @staticmethod
        def attributes_extra_as_dict(span):
            return span.extra

        def list_by_trace(self, trace_id, *, project_id=None, **kwargs):
            self.reads.append(("voice", str(trace_id), project_id))
            spans = []
            # Foreign project has a copy of an otherwise valid trace ID, too.
            for resource in (world.foreign, world.peer, world.own, world.second):
                trace_ids = {str(resource.trace.id)}
                if resource is world.foreign:
                    trace_ids.add(str(world.own.trace.id))
                if str(trace_id) not in trace_ids:
                    continue
                if project_id is not None and str(project_id) != str(
                    resource.project.id
                ):
                    continue
                raw_log = {
                    # synthetic test value (fi-security e126d4a6)
                    "assistantId": f"FAKE_{resource.label}_ASSISTANT",
                    "assistant": {"firstMessage": f"{resource.label} first message"},
                    "messages": [
                        {"role": "system", "message": f"{resource.label} system prompt"}
                    ],
                    "phoneNumber": {"number": "FAKE_CONTACT_NUMBER"},
                    "phoneCallProvider": "vapi",
                }
                spans.append(
                    SimpleNamespace(
                        id="FAKE_CONVERSATION_SPAN",
                        trace_id=str(trace_id),
                        attributes_extra="{}",
                        observation_type="conversation" if self.voice else "llm",
                        attrs_string={
                            comparison.RECORDING_ATTR_KEYS[
                                "stereo"
                            ]: f"https://example.invalid/{resource.label}.wav",
                            "raw_log": json.dumps(raw_log),
                            **(
                                {"fi.simulator.call_execution_id": "FAKE_FOREIGN_CALL"}
                                if resource is world.foreign
                                else {}
                            ),
                        },
                        attrs_number={
                            CallAttributes.DURATION: 987654
                            if resource is world.foreign
                            else 10
                        },
                        attrs_bool={},
                        extra={
                            "provider_transcript": [
                                {
                                    "role": "user",
                                    "content": f"{resource.label} baseline input",
                                },
                                {
                                    "role": "assistant",
                                    "content": f"{resource.label} baseline output",
                                },
                            ]
                        },
                    )
                )
            return spans

        def per_trace_root_span_start_times(self, trace_ids, project_ids=None):
            assert project_ids == [str(world.own.project.id)]
            self.reads.append(("root_times", tuple(trace_ids), project_ids[0]))
            return {}

        def aggregate_by_session_ids(self, session_ids, *, project_id=None):
            self.reads.append(("metrics", tuple(session_ids), project_id))
            now = datetime(2026, 1, 1, tzinfo=UTC)
            return {
                sid: {
                    "start_time": now,
                    "end_time": now + timedelta(seconds=10),
                    "tokens": 20,
                    "traces_count": 1,
                }
                for sid in session_ids
            }

        def _resolve_session_ids_to_canonical(self, session_ids):
            return {sid: self.remaps.get(sid, sid) for sid in session_ids}

        def count_with_filters(self, **kwargs):
            self.reads.append(("tools", kwargs["session_id"], kwargs.get("project_id")))
            assert kwargs["session_id"] not in self.remaps, (
                "count needs the canonical input ID"
            )
            return 1

    reader = Reader()

    @contextmanager
    def get_reader():
        yield reader

    class SessionClient:
        def query(self, sql, *, parameters):
            ids = tuple(parameters["ids"])
            pids = tuple(parameters.get("pids", (parameters.get("pid"),)))
            assert pids and None not in pids, "membership must be project scoped"
            assert "ts.project_id" in sql
            reader.membership_reads.append(("session", ids, pids))
            now = datetime(2026, 1, 1, tzinfo=UTC)
            return SimpleNamespace(
                result_rows=[
                    (
                        sid,
                        reader.remaps.get(sid, sid),
                        "FAKE_SESSION",
                        now,
                        reader.sessions[reader.remaps.get(sid, sid)],
                    )
                    for sid in ids
                    if reader.sessions.get(reader.remaps.get(sid, sid)) in pids
                ]
            )

    monkeypatch.setattr(v2, "get_reader", get_reader)
    monkeypatch.setattr(
        trace_session_dict_reader, "_get_client", lambda: SessionClient()
    )
    return reader


@pytest.fixture
def suggestions(monkeypatch):
    external = Mock(return_value=(False, {"agent_type": "voice"}, None))
    monkeypatch.setattr(replay_views, "get_agent_suggestions", external)
    return external


@pytest.mark.parametrize("kind", ["trace", "session"])
@pytest.mark.parametrize("target", ["foreign", "peer", "second"])
@pytest.mark.parametrize("select_all", [False, True])
def test_create_refuses_every_foreign_id_like_missing(
    world, telemetry, suggestions, kind, target, select_all
):
    valid = getattr(world.own, kind).id
    invalid = getattr(getattr(world, target), kind).id
    before = ReplaySession.no_workspace_objects.count()
    replies = []
    for identifier in (invalid, uuid.uuid4()):
        replies.append(
            replay_views.ReplaySessionView.as_view({"post": "create"})(
                request(
                    world,
                    "post",
                    {
                        "project_id": str(world.own.project.id),
                        "replay_type": kind,
                        "ids": [str(valid), str(identifier)],
                        "select_all": select_all,
                    },
                )
            )
        )
    assert [r.status_code for r in replies] == [400, 400]
    assert replies[0].data == replies[1].data
    assert ReplaySession.no_workspace_objects.count() == before
    suggestions.assert_not_called()
    encoded = json.dumps(replies[0].data, default=str)
    assert str(invalid) not in encoded


@pytest.mark.parametrize("kind", ["trace", "session"])
@pytest.mark.parametrize("all_only", [False, True])
def test_create_same_project_control(world, telemetry, suggestions, kind, all_only):
    ids = [] if all_only else [str(getattr(world.own, kind).id)] * 2
    response = replay_views.ReplaySessionView.as_view({"post": "create"})(
        request(
            world,
            "post",
            {
                "project_id": str(world.own.project.id),
                "replay_type": kind,
                "ids": ids,
                "select_all": all_only,
            },
        )
    )
    assert response.status_code == 201
    saved = ReplaySession.no_workspace_objects.get(id=result(response)["id"])
    assert saved.project_id == world.own.project.id
    assert saved.ids == ids
    suggestions.assert_called_once()


@pytest.mark.parametrize("target", ["foreign", "peer"])
@pytest.mark.parametrize("source", ["trace_id", "intent_id", "session_id", "fallback"])
def test_comparison_refuses_foreign_baseline_before_telemetry(
    world, telemetry, target, source
):
    own = world.own
    hidden = getattr(world, target)
    replies = []
    if source == "session_id":
        own.call.simulation_call_type = CallExecution.SimulationCallType.TEXT
        own.call.save(update_fields=["simulation_call_type"])
    for identifier in (
        hidden.session.id if source == "session_id" else hidden.trace.id,
        uuid.uuid4(),
    ):
        ReplaySession.no_workspace_objects.filter(run_test=own.run).delete()
        own.row.metadata = {} if source == "fallback" else {source: str(identifier)}
        own.row.save(update_fields=["metadata"])
        if source == "fallback":
            # Legacy poisoned row: bypass the now-protected creation endpoint.
            ReplaySession.no_workspace_objects.create(
                project=own.project,
                run_test=own.run,
                replay_type="trace",
                ids=[str(identifier)],
            )
        replies.append(compare(world))
    assert [r.status_code for r in replies] == [404, 404]
    assert replies[0].data == replies[1].data
    assert telemetry.reads == []
    body = json.dumps(replies[0].data, default=str)
    for marker in (
        str(hidden.trace.id),
        "baseline input",
        ".wav",
        "comparison_metrics",
    ):
        assert marker not in body


@pytest.mark.parametrize("target", ["foreign", "peer"])
@pytest.mark.parametrize("surface", ["call", "row", "replay"])
def test_foreign_objects_are_indistinguishable_from_missing(
    world, telemetry, target, surface
):
    own, hidden = world.own, getattr(world, target)
    replies = []
    for missing in (False, True):
        if surface == "call":
            call = SimpleNamespace(id=uuid.uuid4()) if missing else hidden.call
            replies.append(compare(world, call))
        elif surface == "row":
            own.call.row_id = uuid.uuid4() if missing else hidden.row.id
            own.call.save(update_fields=["row_id"])
            replies.append(compare(world))
        else:
            own.row.metadata = {}
            own.row.save(update_fields=["metadata"])
            ReplaySession.no_workspace_objects.filter(run_test=own.run).delete()
            if not missing:
                ReplaySession.no_workspace_objects.create(
                    project=hidden.project,
                    run_test=own.run,
                    replay_type="trace",
                    ids=[str(hidden.trace.id)],
                )
            replies.append(compare(world))
    assert [r.status_code for r in replies] == [404, 404]
    assert replies[0].data == replies[1].data
    assert telemetry.reads == []


def test_fallback_trace_must_belong_to_replay_project(world, telemetry):
    world.own.row.metadata = {}
    world.own.row.save(update_fields=["metadata"])
    ReplaySession.no_workspace_objects.create(
        project=world.own.project,
        run_test=world.own.run,
        replay_type="trace",
        ids=[str(world.second.trace.id)],
    )
    assert compare(world).status_code == 404
    assert telemetry.reads == []


@pytest.mark.parametrize("fallback", [False, True])
def test_same_project_voice_control_excludes_duplicate_foreign_span(
    world, telemetry, fallback
):
    if fallback:
        world.own.row.metadata = {}
        world.own.row.save(update_fields=["metadata"])
        ReplaySession.no_workspace_objects.create(
            project=world.own.project,
            run_test=world.own.run,
            replay_type="trace",
            ids=[str(world.own.trace.id)],
        )
    response = compare(world)
    assert response.status_code == 200
    data = result(response)
    duration = next(
        metric
        for metric in data["comparison_metrics"]
        if metric["metric"] == "duration"
    )
    assert duration["value"] == 20
    assert duration["change"] == 10
    assert data["comparison_transcripts"]["base_session_transcripts"][0][
        "messages"
    ] == ["own baseline input"]
    assert (
        data["comparison_recordings"]["baseline"]["stereo"]
        == "https://example.invalid/own.wav"
    )
    assert "foreign" not in json.dumps(data, default=str)
    assert telemetry.reads == [
        ("voice", str(world.own.trace.id), str(world.own.project.id))
    ]


def test_session_control_scopes_metrics_and_mixed_project_transcripts(world, telemetry):
    own = world.own
    own.call.simulation_call_type = CallExecution.SimulationCallType.TEXT
    own.call.save(update_fields=["simulation_call_type"])
    own.row.metadata = {"session_id": str(own.session.id)}
    own.row.save(update_fields=["metadata"])
    Trace.no_workspace_objects.create(
        project=world.foreign.project,
        session_id=own.session.id,
        input="foreign mixed input",
        output="foreign mixed output",
    )
    response = compare(world)
    assert response.status_code == 200
    data = result(response)
    assert data["comparison_metrics"]
    assert data["comparison_transcripts"]["base_session_transcripts"][0][
        "messages"
    ] == ["own input"]
    assert "foreign" not in json.dumps(data, default=str)
    assert len(telemetry.reads) == 3
    assert all(str(read[2]) == str(own.project.id) for read in telemetry.reads)


def test_metrics_guard_uses_run_test_organization(world, telemetry):
    assert not hasattr(world.own.execution, "organization")
    with pytest.raises(ValueError):
        comparison.fetch_comparison_metrics(
            world.own.call, str(world.foreign.session.id)
        )
    assert telemetry.reads == []


def test_transcript_helper_filters_project_and_organization(world, telemetry):
    Trace.no_workspace_objects.create(
        project=world.foreign.project,
        session_id=world.own.session.id,
        input="foreign mixed input",
        output="foreign mixed output",
    )
    data = comparison.fetch_base_session_transcripts(
        str(world.own.session.id),
        organization=world.organization,
        project_id=str(world.own.project.id),
    )
    assert data[0]["messages"] == ["own input"]
    assert "foreign" not in json.dumps(data, default=str)


def test_voice_helper_requires_project_before_read(world, telemetry):
    with pytest.raises(ValueError):
        comparison.fetch_voice_conversation_span(str(world.foreign.trace.id))
    assert telemetry.reads == []
    span = comparison.fetch_voice_conversation_span(
        str(world.own.trace.id), project_id=str(world.own.project.id)
    )
    assert span["span_attributes"][comparison.RECORDING_ATTR_KEYS["stereo"]] == (
        "https://example.invalid/own.wav"
    )


def test_scoped_voice_extractors_work_without_prefetched_span(world, telemetry):
    kwargs = {"project_id": str(world.own.project.id)}
    trace_id = str(world.own.trace.id)
    assert (
        comparison.fetch_voice_trace_baseline_metrics(trace_id, **kwargs)["duration"]
        == 10
    )
    assert comparison.fetch_voice_trace_baseline_transcripts(trace_id, **kwargs)[0][
        "messages"
    ] == ["own baseline input"]
    assert comparison.fetch_baseline_trace_recordings(trace_id, **kwargs)["stereo"] == (
        "https://example.invalid/own.wav"
    )
    assert len(telemetry.reads) == 3
    assert all(read[2] == kwargs["project_id"] for read in telemetry.reads)


def test_post_real_suggestions_never_exposes_duplicate_foreign_config(world, telemetry):
    response = replay_views.ReplaySessionView.as_view({"post": "create"})(
        request(
            world,
            "post",
            {
                "project_id": str(world.own.project.id),
                "replay_type": "trace",
                "ids": [str(world.own.trace.id)],
            },
        )
    )
    assert response.status_code == 201
    data = result(response)
    config = data["suggestions"]["original_voice_config"]
    assert config["assistant_id"] == "FAKE_own_ASSISTANT"
    assert config["description"] == "own system prompt"
    assert config["first_message"] == "own first message"
    assert "foreign" not in json.dumps(data, default=str)
    assert "peer" not in json.dumps(data, default=str)
    assert len(telemetry.reads) == 2
    assert all(read[2] == str(world.own.project.id) for read in telemetry.reads)


def test_replay_recording_and_configuration_helpers_are_scoped(world, telemetry):
    query = Trace.no_workspace_objects.filter(project=world.own.project)
    kwargs = {"project_id": str(world.own.project.id)}
    assert (
        replay_utils._extract_voice_trace_system_prompt(query, **kwargs)
        == "own system prompt"
    )
    data = replay_utils.get_transcripts(
        str(world.own.project.id), "trace", [str(world.own.trace.id)], False
    )
    assert (
        data[str(world.own.trace.id)]["audio_url"] == "https://example.invalid/own.wav"
    )
    assert "foreign" not in json.dumps(data)
    assert all(read[2] == str(world.own.project.id) for read in telemetry.reads)


@pytest.mark.parametrize("kind", ["trace", "session"])
def test_text_replay_auxiliary_telemetry_reads_are_project_scoped(
    world, telemetry, kind
):
    telemetry.voice = False
    value = [{"role": "user", "content": "own input"}]
    Trace.no_workspace_objects.filter(id=world.own.trace.id).update(input=value)
    data = replay_utils.get_transcripts(
        str(world.own.project.id), kind, [str(getattr(world.own, kind).id)], False
    )
    transcript = json.loads(data[str(getattr(world.own, kind).id)]["transcript"])
    # A foreign simulator marker must not reinterpret this ordinary input.
    assert transcript[0]["input"] == value
    assert all(read[2] == str(world.own.project.id) for read in telemetry.reads)


@pytest.mark.parametrize("kind", ["trace", "session"])
@pytest.mark.parametrize("remapped", [False, True])
def test_create_ch_only_sources_batches_membership_without_pg_rows(
    world, telemetry, kind, remapped
):
    own = world.own
    Trace.no_workspace_objects.filter(project=own.project).delete()
    TraceSession.no_workspace_objects.filter(project=own.project).delete()
    assert not Trace.no_workspace_objects.filter(project=own.project).exists()
    assert not TraceSession.no_workspace_objects.filter(project=own.project).exists()
    first = str(getattr(own, kind).id)
    second = str(uuid.UUID(int=601))
    if kind == "trace":
        telemetry.trace_projects[second] = [str(own.project.id)]
    else:
        telemetry.sessions[second] = str(own.project.id)
        if remapped:
            alias = str(uuid.UUID(int=602))
            telemetry.remaps[alias] = first
            first = alias
    response = replay_views.ReplaySessionView.as_view({"post": "create"})(
        request(
            world,
            "post",
            {
                "project_id": str(own.project.id),
                "replay_type": kind,
                "ids": [first, second, first],
            },
        )
    )
    assert response.status_code == 201
    data = result(response)
    assert data["ids"] == [first, second, first]
    assert "suggestions" in data and "exists" in data
    assert len(telemetry.membership_reads) == 1
    read_kind, ids, pids = telemetry.membership_reads[0]
    assert read_kind == kind
    assert set(ids) == {first, second}
    assert pids == (str(own.project.id),)


@pytest.mark.parametrize("kind", ["trace", "session"])
@pytest.mark.parametrize("remapped", [False, True])
def test_comparison_ch_only_sources_keep_success_shape(
    world, telemetry, kind, remapped
):
    own = world.own
    Trace.no_workspace_objects.filter(project=own.project).delete()
    TraceSession.no_workspace_objects.filter(project=own.project).delete()
    assert not Trace.no_workspace_objects.filter(project=own.project).exists()
    assert not TraceSession.no_workspace_objects.filter(project=own.project).exists()
    baseline = str(getattr(own, kind).id)
    if kind == "session":
        if remapped:
            alias = str(uuid.UUID(int=603))
            telemetry.remaps[alias] = baseline
            baseline = alias
        own.call.simulation_call_type = CallExecution.SimulationCallType.TEXT
        own.call.save(update_fields=["simulation_call_type"])
    own.row.metadata = {f"{kind}_id": baseline}
    own.row.save(update_fields=["metadata"])
    response = compare(world)
    assert response.status_code == 200
    data = result(response)
    assert isinstance(data["comparison_metrics"], list)
    assert isinstance(data["comparison_transcripts"]["base_session_transcripts"], list)
    assert data["comparison_metrics"]
    if kind == "trace":
        assert (
            data["comparison_recordings"]["baseline"]["stereo"]
            == "https://example.invalid/own.wav"
        )
    assert "foreign" not in json.dumps(data, default=str)
    assert all(str(read[2]) == str(own.project.id) for read in telemetry.reads)


def test_remapped_session_transcripts_use_span_membership_not_dead_pg_fk(
    world, telemetry
):
    own = world.own
    alias = str(uuid.UUID(int=604))
    telemetry.remaps[alias] = str(own.session.id)
    Trace.no_workspace_objects.filter(id=own.trace.id).update(session_id=None)
    TraceSession.no_workspace_objects.filter(id=own.session.id).delete()
    data = comparison.fetch_base_session_transcripts(
        alias, organization=world.organization, project_id=str(own.project.id)
    )
    assert data[0]["messages"] == ["own input"]
    assert telemetry.reads == [("session_traces", alias, str(own.project.id))]


@pytest.mark.parametrize("target", ["foreign", "peer", "second"])
def test_remapped_foreign_session_refused_in_creation_and_replay_scope(
    world, telemetry, target
):
    alias = str(uuid.UUID(int=605))
    telemetry.remaps[alias] = str(getattr(world, target).session.id)
    response = replay_views.ReplaySessionView.as_view({"post": "create"})(
        request(
            world,
            "post",
            {
                "project_id": str(world.own.project.id),
                "replay_type": "session",
                "ids": [alias],
            },
        )
    )
    assert response.status_code == 400
    assert telemetry.reads == []
    with pytest.raises(ValueError, match="Comparison source not found"):
        comparison.fetch_base_session_metrics(
            alias, organization=world.organization, project_id=str(world.own.project.id)
        )


def test_session_utility_rejects_same_org_other_workspace(world, telemetry):
    with pytest.raises(ValueError, match="Comparison source not found"):
        comparison.fetch_comparison_metrics(world.own.call, str(world.peer.session.id))
    assert telemetry.reads == []


@pytest.mark.parametrize("kind", ["trace", "session"])
def test_pg_mirror_does_not_authorize_missing_canonical_telemetry(
    world, telemetry, kind
):
    if kind == "trace":
        telemetry.trace_projects.clear()
    else:
        telemetry.sessions.clear()
    response = replay_views.ReplaySessionView.as_view({"post": "create"})(
        request(
            world,
            "post",
            {
                "project_id": str(world.own.project.id),
                "replay_type": kind,
                "ids": [str(getattr(world.own, kind).id)],
            },
        )
    )
    assert response.status_code == 400
    assert telemetry.reads == []


@pytest.mark.parametrize(
    "project_id", [None, "", "00000000-0000-4000-8000-000000000001"]
)
def test_reader_project_predicate_and_legacy_signature(project_id):
    client = Mock()
    client.query.return_value.result_rows = []
    reader = object.__new__(CHSpanReader)
    reader._client = client
    kwargs = {} if project_id is None else {"project_id": project_id}
    assert reader.list_by_trace("00000000-0000-4000-8000-000000000002", **kwargs) == []
    args, query_kwargs = client.query.call_args
    if project_id is None:
        assert "project_id = %(pid)s" not in args[0]
        assert "pid" not in query_kwargs["parameters"]
    else:
        assert "project_id = %(pid)s" in args[0]
        assert query_kwargs["parameters"]["pid"] == project_id
