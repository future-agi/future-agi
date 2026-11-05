"""A multi-agent call's workers, from the spans the app exported: who was delegated what, what each did, what came back."""

import json

import pytest

from simulate.services import harness_workers
from simulate.services.harness_workers import worker_timeline
from simulate.tests.test_harness_versioning_backfill import (  # noqa: F401 - fixture
    storage as storage,
)

RUN = "run-1"
DECLARED = [
    {"id": "order_lookup", "name": "Order-lookup worker"},
    {"id": "refund", "name": "Refund worker"},
    {"id": "audit", "name": "Audit worker"},
]


def _span(span_id, parent, kind, name, start, end=None, **extra):
    return {
        "span_id": span_id,
        "parent_span_id": parent,
        "observation_type": kind,
        "name": name,
        "start_time": start,
        "end_time": end,
        "status": extra.pop("status", "OK"),
        "attributes": extra.pop("attributes", {}),
        **extra,
    }


# A coordinator running each worker as a tool (OpenAI Agents SDK `Agent.as_tool`): the delegating call is a tool span,
# the worker an agent span inside it, the worker's own calls tool spans under that.
SPANS = [
    _span("s1", None, "agent", "Returns coordinator", "t01", "t20"),
    _span("s1a", "s1", "llm", "response", "t02", "t03"),
    _span(
        "s2",
        "s1",
        "tool",
        "order_lookup",
        "t04",
        "t09",
        input='{"input": "inspect order 1042"}',
    ),
    _span(
        "s3",
        "s2",
        "agent",
        "Order-lookup worker",
        "t05",
        "t08",
        attributes={"graph.node.id": "Order-lookup worker"},
        input="inspect order 1042",
        output="order 1042 is eligible; refund RF-7 exists",
    ),
    _span("s4", "s3", "tool", "get_order", "t06", "t06"),
    _span("s5", "s3", "tool", "list_refunds", "t07", "t07"),
    _span("s6", "s1", "tool", "refund", "t10", "t15"),
    _span("s7", "s6", "agent", "Refund worker", "t11", "t14", output="reused RF-7"),
    _span("s8", "s7", "tool", "issue_refund", "t12", "t12", status="ERROR"),
    _span("s9", "s1", "tool", "send_reply", "t16", "t16"),
]


def test_each_worker_has_a_stable_id_and_each_hand_off_a_matching_return():
    timeline = worker_timeline(SPANS, DECLARED, run_key=RUN)

    workers = {one["id"]: one for one in timeline["workers"]}
    assert (
        workers["order_lookup"]["worker_id"] == "run-1/order_lookup"
        and workers["order_lookup"]["observed"]
    )
    assert (
        workers["refund"]["name"] == "Refund worker" and workers["refund"]["observed"]
    )
    # Declared names are not proof: a worker with no spans of its own is not observed.
    assert workers["audit"]["observed"] is False
    assert not any(one["undeclared"] for one in timeline["workers"])

    events = [
        (one["type"], one["worker_id"], one.get("tool")) for one in timeline["events"]
    ]
    assert events == [
        ("delegated", "run-1/order_lookup", None),
        ("tool_call", "run-1/order_lookup", "get_order"),
        ("tool_call", "run-1/order_lookup", "list_refunds"),
        ("returned", "run-1/order_lookup", None),
        ("delegated", "run-1/refund", None),
        ("tool_call", "run-1/refund", "issue_refund"),
        ("returned", "run-1/refund", None),
        # The coordinator's own call names no worker.
        ("tool_call", None, "send_reply"),
    ]
    delegated = {
        one["worker_id"]: one
        for one in timeline["events"]
        if one["type"] == "delegated"
    }
    returned = {
        one["worker_id"]: one for one in timeline["events"] if one["type"] == "returned"
    }
    assert (
        delegated["run-1/order_lookup"]["delegation_id"]
        == returned["run-1/order_lookup"]["delegation_id"]
        == "s2"
    )
    assert delegated["run-1/order_lookup"]["task"] == "inspect order 1042"
    assert returned["run-1/refund"]["result"] == "reused RF-7"
    assert [one["seq"] for one in timeline["events"]] == list(range(1, 9))
    assert {one["attribution"] for one in timeline["events"]} == {"reported"}
    assert (
        next(one for one in timeline["events"] if one.get("tool") == "issue_refund")[
            "status"
        ]
        == "error"
    )


def test_the_same_spans_give_the_same_ids_on_a_retry_of_the_trial():
    first = worker_timeline(SPANS, DECLARED, run_key=RUN)
    again = worker_timeline(list(reversed(SPANS)), DECLARED, run_key=RUN)
    assert first == again


def test_a_worker_nested_without_a_launching_tool_call_and_an_undeclared_one_are_still_seen():
    spans = [
        _span("a", None, "agent", "coordinator", "t1", "t9"),
        _span("al", "a", "llm", "generation", "t1", "t1"),
        _span("b", "a", "agent", "researcher", "t2", None),
        _span("c", "b", "tool", "search", "t3", "t3"),
    ]
    timeline = worker_timeline(spans, [], run_key=RUN)

    [worker] = timeline["workers"]
    assert (worker["worker_id"], worker["undeclared"]) == ("run-1/researcher", True)
    # Still running when the spans were read: delegated, no return.
    assert [(one["type"], one.get("delegation_id")) for one in timeline["events"]] == [
        ("delegated", "b"),
        # The call names the hand-off it ran in, read from the span tree.
        ("tool_call", "b"),
    ]
    assert timeline["events"][1]["worker_id"] == "run-1/researcher"


def test_no_agent_spans_means_no_worker_was_observed():
    timeline = worker_timeline(
        [_span("x", None, "llm", "response", "t1", "t2")], DECLARED, run_key=RUN
    )
    assert [one["observed"] for one in timeline["workers"]] == [False, False, False]
    assert timeline["events"] == []


def test_a_tool_call_with_no_agent_above_it_is_unattributed_not_the_coordinator_s():
    orphan = _span("o1", None, "tool", "send_reply", "t30", "t30")
    events = worker_timeline([*SPANS, orphan], DECLARED, run_key=RUN)["events"]
    by_span = {one.get("span_id"): one for one in events if one["type"] == "tool_call"}
    assert (
        by_span["o1"]["worker_id"] is None
        and by_span["o1"]["attribution"] == "unattributed"
    )
    assert (
        by_span["s9"]["worker_id"] is None
        and by_span["s9"]["attribution"] == "reported"
    )


def test_a_parent_loop_in_broken_spans_does_not_hang():
    spans = [_span("p", "q", "tool", "x", "t1"), _span("q", "p", "tool", "y", "t2")]
    assert [
        one["worker_id"] for one in worker_timeline(spans, [], run_key=RUN)["events"]
    ] == [None, None]


class _Span:
    def __init__(self, raw):
        self.id = raw["span_id"]
        self.parent_span_id = raw["parent_span_id"] or ""
        self.name = raw["name"]
        self.observation_type = raw["observation_type"]
        self.start_time = None
        self.end_time = None
        self.status = raw["status"]
        self.input = raw.get("input", "")
        self.output = raw.get("output", "")
        self.attrs_string = raw["attributes"]
        # The reader's span record (`CHSpan`) carries these too.
        self.trace_id = raw.get("trace_id", "t1")
        self.model = raw.get("model", "")
        self.total_tokens = raw.get("tokens", 0)


class _Reader:
    def __init__(self, spans):
        self.spans, self.asked = spans, None

    def list_by_trace_ids(self, trace_ids, *, project_id=None):
        self.asked = (trace_ids, project_id)
        return [_Span(one) for one in self.spans]

    def close(self):
        pass


def _traced(project_id="p1"):
    from simulate.services.run_traces_v3 import RunTrace, RunTraces

    return RunTraces(
        project_id=project_id,
        traces=[RunTrace("t1", "run", None, "OK", 1, None)] if project_id else [],
    )


@pytest.mark.django_db
def test_a_call_keeps_its_worker_timeline_with_the_declared_workers(
    user, workspace, storage, settings, monkeypatch
):
    from unittest.mock import patch

    from simulate.models import CallExecution, HarnessEnvironmentVersion
    from simulate.services import harness_workers
    from simulate.tests.test_harness_versioning_picks import _create, _environment

    settings.HARNESS_VERSIONING_ENABLED = True
    settings.HARNESS_BUNDLE_CERTIFY_ENABLED = False
    environment = _environment(user, workspace, "workers-record", storage)
    version = environment.environment_version
    HarnessEnvironmentVersion._base_manager.filter(id=version.id).update(
        documents={
            **(version.documents or {}),
            "contract": {
                **(version.documents or {}).get("contract", {}),
                "workers": DECLARED,
            },
        }
    )
    run = _create(environment, key="w")
    call = CallExecution.objects.filter(test_execution_id=run.test_execution_id).first()
    reader = _Reader(SPANS)

    with (
        patch(
            "simulate.services.run_traces_v3.list_run_traces", return_value=_traced()
        ),
        patch("tracer.services.clickhouse.v2.get_reader", return_value=reader),
    ):
        timeline = harness_workers.record_call_workers(str(call.id))

    call.refresh_from_db()
    stored = call.call_metadata[harness_workers.WORKERS_KEY]
    assert stored == timeline and stored["collected_at"]
    assert reader.asked == (["t1"], "p1")
    workers = {one["id"]: one for one in stored["workers"]}
    assert (
        workers["order_lookup"]["worker_id"] == f"{run.run_id}/order_lookup"
        and workers["order_lookup"]["observed"]
    )
    assert workers["audit"]["observed"] is False

    # A declared multi-agent call with no spans and no attributed rows is still recorded: every declared worker
    # reads not observed (Rishav, #3699 review point 1), so the later read runs and the API never answers 204.
    with patch(
        "simulate.services.run_traces_v3.list_run_traces", return_value=_traced(None)
    ):
        unseen = harness_workers.record_call_workers(str(call.id))
    assert unseen is not None and unseen["collection"] == "no_evidence"
    assert unseen["events"] == []
    assert {one["id"] for one in unseen["workers"]} >= {"order_lookup", "audit"}
    assert not any(one["observed"] for one in unseen["workers"])

    # No spans reach us, but the runtime named the worker of a sealed tool call: that alone is a timeline.
    call.provider_call_data = {
        "livekit": {
            "tool_calls": [
                {
                    "name": "get_order",
                    "ok": True,
                    "at": 1.0,
                    "worker_id": "order_lookup",
                }
            ]
        }
    }
    call.save(update_fields=["provider_call_data"])
    with (
        patch(
            "simulate.services.run_traces_v3.list_run_traces",
            return_value=_traced(None),
        ),
        patch(
            "tracer.services.clickhouse.v2.get_reader",
            side_effect=AssertionError("no spans to read"),
        ),
    ):
        reported = harness_workers.record_call_workers(str(call.id))
    assert [(one["worker_id"], one["tool"]) for one in reported["events"]] == [
        (f"{run.run_id}/order_lookup", "get_order")
    ]

    # A hand-off whose launcher the runtime could not name is still read, as the coordinator's.
    call.provider_call_data = {
        "livekit": {
            "tool_calls": [
                {
                    "name": "start_async_task",
                    "ok": True,
                    "at": 1.0,
                    "ended_at": 5.0,
                    "delegated_to": "researcher",
                    "result": "done",
                }
            ]
        }
    }
    call.save(update_fields=["provider_call_data"])
    with (
        patch(
            "simulate.services.run_traces_v3.list_run_traces",
            return_value=_traced(None),
        ),
        patch(
            "tracer.services.clickhouse.v2.get_reader",
            side_effect=AssertionError("no spans to read"),
        ),
    ):
        launched = harness_workers.record_call_workers(str(call.id))
    assert [
        (one["type"], one["worker_id"], one.get("by")) for one in launched["events"]
    ] == [
        ("delegated", f"{run.run_id}/researcher", None),
        ("returned", f"{run.run_id}/researcher", None),
    ]

    # One agent calling tools, no worker declared and no hand-off: nothing is kept on the call.
    single = [
        _span("root", None, "agent", "coordinator", "t0000"),
        _span("c1", "root", "tool", "lookup", "t0001"),
    ]
    call.provider_call_data, call.call_metadata = {}, {}
    call.save(update_fields=["provider_call_data", "call_metadata"])
    with (
        patch(
            "simulate.services.run_traces_v3.list_run_traces", return_value=_traced()
        ),
        patch("tracer.services.clickhouse.v2.get_reader", return_value=_Reader(single)),
        patch.object(harness_workers, "declared_workers", return_value=[]),
    ):
        assert harness_workers.record_call_workers(str(call.id)) is None
    call.refresh_from_db()
    assert harness_workers.WORKERS_KEY not in call.call_metadata


@pytest.mark.django_db
def test_declared_workers_come_from_the_version_the_run_was_pinned_to(
    user, workspace, storage, settings
):
    import uuid

    from simulate.models import HarnessEnvironmentVersion, HostedHarnessJob
    from simulate.services.harness_workers import declared_workers
    from simulate.tests.test_harness_versioning_picks import _create, _environment

    settings.HARNESS_VERSIONING_ENABLED = True
    settings.HARNESS_BUNDLE_CERTIFY_ENABLED = False
    environment = _environment(user, workspace, "workers-pinned", storage)
    pinned = environment.environment_version
    contract = (pinned.documents or {}).get("contract", {})
    HarnessEnvironmentVersion._base_manager.filter(id=pinned.id).update(
        documents={
            **(pinned.documents or {}),
            "contract": {**contract, "workers": DECLARED},
        }
    )
    run = _create(environment, key="p")
    job = HostedHarnessJob.no_workspace_objects.get(
        test_execution_id=run.test_execution_id
    )
    assert job.environment_version_id == pinned.id

    # The environment moves on to a version declaring other workers after the run started.
    moved = HarnessEnvironmentVersion._base_manager.get(id=pinned.id)
    moved.pk, moved.id = None, uuid.uuid4()
    moved.version_number = (pinned.version_number or 0) + 100
    moved.hash = "sha256:moved"
    moved.documents = {
        **(pinned.documents or {}),
        "contract": {**contract, "workers": [{"id": "later"}]},
    }
    moved.save()
    HostedHarnessJob.no_workspace_objects.filter(id=job.environment_id).update(
        environment_version=moved
    )
    job = HostedHarnessJob.no_workspace_objects.get(id=job.id)

    assert [one["id"] for one in declared_workers(job)] == [
        one["id"] for one in DECLARED
    ]


@pytest.mark.django_db
def test_a_declared_worker_not_seen_yet_gets_one_more_read_later():
    from unittest.mock import patch

    from simulate.tasks import alk_sim

    seen = {"workers": [{"observed": True}]}
    missing = {"workers": [{"observed": True}, {"observed": False}]}
    with (
        patch(
            "simulate.services.harness_workers.record_call_workers",
            side_effect=[missing, seen, missing],
        ),
        patch.object(alk_sim.record_call_workers, "apply_async") as again,
    ):
        # Called directly, an activity runs in process.
        alk_sim.record_call_workers("c1")
        assert again.call_args.kwargs["kwargs"] == {"second_read": True}
        again.reset_mock()
        alk_sim.record_call_workers("c1")
        alk_sim.record_call_workers("c1", second_read=True)
        assert not again.called


def test_when_times_tie_a_hand_off_comes_first_and_its_return_last():
    spans = [
        _span("a", None, "agent", "coordinator", "t1", "t1"),
        _span("al", "a", "llm", "generation", "t1", "t1"),
        _span("b", "a", "agent", "researcher", "t1", "t1"),
        _span("c", "b", "tool", "search", "t1", "t1"),
    ]
    assert [
        one["type"] for one in worker_timeline(spans, [], run_key=RUN)["events"]
    ] == [
        "delegated",
        "tool_call",
        "returned",
    ]


@pytest.mark.django_db
def test_the_trial_view_reads_the_kept_timeline_and_says_when_there_is_none(
    user, workspace, storage, settings, auth_client
):
    from simulate.models import CallExecution
    from simulate.services.harness_workers import WORKERS_KEY
    from simulate.services.scoring_status import locked_call_metadata_update
    from simulate.tests.test_harness_versioning_picks import _create, _environment

    settings.HARNESS_VERSIONING_ENABLED = True
    settings.HARNESS_BUNDLE_CERTIFY_ENABLED = False
    run = _create(_environment(user, workspace, "workers-view", storage), key="v")
    call = CallExecution.objects.filter(test_execution_id=run.test_execution_id).first()
    url = f"/simulate/v3/call-executions/{call.id}/workers/"

    missing = auth_client.get(url)
    timeline = {
        **worker_timeline(SPANS, DECLARED, run_key=RUN),
        "collected_at": "2026-10-31T12:00:00Z",
    }
    locked_call_metadata_update(
        call.id, lambda metadata: metadata.update({WORKERS_KEY: timeline}) or True
    )
    found = auth_client.get(url)

    assert (missing.status_code, missing.content) == (204, b"")
    assert found.status_code == 200, found.content
    assert [one["type"] for one in found.json()["events"]][:2] == [
        "delegated",
        "tool_call",
    ]
    assert {one["id"] for one in found.json()["workers"]} == {
        "order_lookup",
        "refund",
        "audit",
    }
    # The response carries the documented fields only, not every key the stored record keeps, plus the arranged steps.
    # The call's messages are stored for grouping only: the API returns them as `turns`.
    assert set(found.json()) == (set(timeline) - {"messages"}) | {
        "steps",
        "turns",
        "depth",
        "parallel",
        "overlap_unknown",
        "worker_depths",
    }
    first = found.json()["steps"][0]
    assert (first["kind"], first["parent"], first["depth"]) == ("handoff", None, 1)
    assert found.json()["depth"] >= 1
    # A step read from a span carries its span id, for opening it in Traces; stored-only keys stay out.
    assert found.json()["events"][1]["span_id"] == timeline["events"][1]["span_id"]
    assert "runtime_event_id" not in found.json()["events"][1]


@pytest.mark.django_db
def test_another_workspace_or_organization_cannot_read_a_call_s_workers(
    user, workspace, storage, settings, auth_client
):
    from accounts.models import Organization, Workspace
    from simulate.models import CallExecution, RunTest
    from simulate.services.harness_workers import WORKERS_KEY
    from simulate.services.scoring_status import locked_call_metadata_update
    from simulate.tests.test_harness_versioning_picks import _create, _environment

    settings.HARNESS_VERSIONING_ENABLED = True
    settings.HARNESS_BUNDLE_CERTIFY_ENABLED = False
    run = _create(_environment(user, workspace, "workers-scope", storage), key="s")
    call = CallExecution.objects.filter(test_execution_id=run.test_execution_id).first()
    timeline = {
        **worker_timeline(SPANS, DECLARED, run_key=RUN),
        "collected_at": "2026-10-31T12:00:00Z",
    }
    locked_call_metadata_update(
        call.id, lambda metadata: metadata.update({WORKERS_KEY: timeline}) or True
    )
    url = f"/simulate/v3/call-executions/{call.id}/workers/"
    run_tests = RunTest.no_workspace_objects.filter(id=call.test_execution.run_test_id)
    assert auth_client.get(url).status_code == 200

    other_workspace = Workspace.no_workspace_objects.create(
        name="Other workers workspace",
        organization=workspace.organization,
        is_default=False,
        is_active=True,
        created_by=user,
    )
    run_tests.update(workspace=other_workspace)
    assert auth_client.get(url).status_code == 404

    run_tests.update(
        workspace=workspace,
        organization=Organization.objects.create(name="Other workers organization"),
    )
    assert auth_client.get(url).status_code == 404


def test_framework_containers_and_empty_agent_inputs_as_the_openai_agents_instrumentation_writes_them():
    # traceAI's OpenAI Agents nesting: the SDK's workflow is an agent span above the coordinator, a chain sits between
    # each delegating tool call and its worker, and agent spans carry an empty object for input and output.
    spans = [
        _span("w", None, "agent", "Agent workflow", "t00", "t30"),
        _span("wc", "w", "chain", "Agent workflow", "t00", "t30", input="{}"),
        _span("c", "wc", "agent", "Returns coordinator", "t01", "t29", input="{}"),
        _span("ct", "c", "chain", "turn", "t02", "t10"),
        _span("cl", "ct", "llm", "generation", "t02", "t03"),
        _span(
            "d",
            "ct",
            "tool",
            "order_lookup",
            "t04",
            "t09",
            input='{"input": "Order 1043"}',
            output="1043 is eligible",
        ),
        _span("dc", "d", "chain", "Agent workflow", "t04", "t09", input="{}"),
        _span(
            "o",
            "dc",
            "agent",
            "Order-lookup worker",
            "t05",
            "t08",
            input="{}",
            output="{}",
        ),
        _span("ot", "o", "chain", "turn", "t05", "t07"),
        _span("og", "ot", "tool", "get_order", "t06", "t06"),
    ]
    timeline = worker_timeline(spans, DECLARED[:1], run_key=RUN)

    assert [(one["id"], one["undeclared"]) for one in timeline["workers"]] == [
        ("order_lookup", False)
    ]
    assert [
        (one["type"], one["worker_id"], one.get("tool")) for one in timeline["events"]
    ] == [
        ("delegated", "run-1/order_lookup", None),
        ("tool_call", "run-1/order_lookup", "get_order"),
        ("returned", "run-1/order_lookup", None),
    ]
    delegated, _, returned = timeline["events"]
    assert delegated["delegation_id"] == returned["delegation_id"] == "d"
    assert delegated["by"] is None
    assert (delegated["task"], returned["result"]) == ("Order 1043", "1043 is eligible")


def test_a_parent_running_several_agents_is_their_coordinator_even_without_work_of_its_own():
    spans = [
        _span("crew", None, "agent", "crew", "t1", "t9"),
        _span("r", "crew", "agent", "researcher", "t2", "t4"),
        _span("rl", "r", "llm", "generation", "t2", "t3"),
        _span("w", "crew", "agent", "writer", "t5", "t8"),
        _span("wl", "w", "llm", "generation", "t5", "t6"),
    ]
    workers = worker_timeline(spans, [], run_key=RUN)["workers"]
    assert [one["id"] for one in workers] == ["researcher", "writer"]


def test_tool_calls_a_runtime_attributes_to_a_worker_join_the_timeline_without_repeating_a_span():
    # CrewAI's event bus names the agent and task of an in-process tool call; no span covers it.
    declared = [
        {"id": "shakespearean_bard", "name": "Shakespearean bard"},
        {"id": "x_post_verifier"},
    ]
    traced = [
        {
            "name": "write_post",
            "ok": True,
            "at": 1730454600.5,
            "worker_id": "shakespearean_bard",
            "task_label": "Write a post",
            "runtime_ids": {"event_id": "e2", "parent_event_id": "e1"},
        },
        {
            "name": "verify_post",
            "ok": False,
            "at": 1730454601.0,
            "worker_id": "x_post_verifier",
        },
        {
            "name": "lookup",
            "ok": True,
            "at": 1730454602.0,
            "worker_label": "Researcher",
        },
        {"name": "send_reply", "ok": True, "at": 1730454603.0},
    ]
    timeline = worker_timeline([], declared, run_key=RUN, traced_calls=traced)

    workers = {one["id"]: one for one in timeline["workers"]}
    assert (
        workers["shakespearean_bard"]["observed"]
        and not workers["shakespearean_bard"]["undeclared"]
    )
    assert workers["shakespearean_bard"]["instance_ids"] == ["shakespearean_bard"]
    assert workers["researcher"]["undeclared"]
    events = [
        (one["worker_id"], one["tool"], one["status"]) for one in timeline["events"]
    ]
    assert events == [
        ("run-1/shakespearean_bard", "write_post", "ok"),
        ("run-1/x_post_verifier", "verify_post", "error"),
        ("run-1/researcher", "lookup", "ok"),
    ]
    first = timeline["events"][0]
    assert (
        first["task"],
        first["runtime_event_id"],
        first["runtime_parent_event_id"],
    ) == ("Write a post", "e2", "e1")
    assert (
        first["at"] == "2024-11-01T09:50:00.500000"
        and first["attribution"] == "reported"
    )

    # The same call seen in the worker's spans is shown once.
    both = worker_timeline(
        SPANS,
        DECLARED,
        run_key=RUN,
        traced_calls=[{"name": "get_order", "worker_label": "Order-lookup worker"}],
    )
    assert [one.get("tool") for one in both["events"]].count("get_order") == 1


def test_a_declared_worker_a_runtime_names_by_an_opaque_id_and_its_label_is_a_worker_not_the_coordinator():
    # CrewAI's observer gives each agent a hashed key and its role as the label; a flow's verifier may be the only
    # agent that calls a tool, with no hand-off recorded.
    declared = [
        {"id": "shakespearean_bard", "name": "Shakespearean Bard"},
        {"id": "x_post_verifier", "name": "X Post Verifier"},
    ]
    traced = [
        {
            "name": "Character Counter Tool",
            "ok": True,
            "at": 1730454600.0,
            "worker_id": "1853305b0351f75f",
            "worker_label": "X Post Verifier\n",
            "task_label": "verify_x_post",
        }
    ]

    timeline = worker_timeline([], declared, run_key=RUN, traced_calls=traced)

    workers = {one["id"]: one for one in timeline["workers"]}
    assert (
        workers["x_post_verifier"]["observed"]
        and not workers["x_post_verifier"]["undeclared"]
    )
    assert workers["x_post_verifier"]["instance_ids"] == ["1853305b0351f75f"]
    assert not workers["shakespearean_bard"]["observed"]
    assert [(one["worker_id"], one["tool"]) for one in timeline["events"]] == [
        ("run-1/x_post_verifier", "Character Counter Tool")
    ]


@pytest.mark.parametrize(
    "hand_off",
    [
        # A crew's agent run sealed as a tool call named for the worker.
        {"name": "X Post Verifier", "arguments": {"task": "Verify the post"}},
        # The same hand-off sealed as its own record kind, with no tool name.
        {"kind": "handoff", "arguments": {"task": "Verify the post"}},
    ],
)
def test_a_crew_hand_off_reads_the_same_as_a_tool_call_or_as_its_own_record(hand_off):
    row = {
        **hand_off,
        "ok": True,
        "at": 10.0,
        "ended_at": 12.0,
        "worker_label": "ShakespeareXPostFlow",
        "delegated_to": "X Post Verifier",
        "result": "valid",
    }
    declared = [{"id": "x_post_verifier", "name": "X Post Verifier"}]

    timeline = worker_timeline([], declared, run_key=RUN, traced_calls=[row])

    assert [
        (one["type"], one["worker_id"], one.get("task"), one.get("result"))
        for one in timeline["events"]
    ] == [
        ("delegated", "run-1/x_post_verifier", "Verify the post", None),
        ("returned", "run-1/x_post_verifier", None, "valid"),
    ]
    assert timeline["coordinator"] == {"name": "ShakespeareXPostFlow"}


def test_hand_offs_in_the_sealed_trace_give_each_worker_a_delegation_and_a_return_without_spans():
    # The sandbox reads any framework's spans and seals who made each call and which call handed work out.
    traced = [
        {
            "name": "order_lookup",
            "ok": True,
            "at": 1730454600.0,
            "ended_at": 1730454602.0,
            "worker_label": "Returns coordinator",
            "delegated_to": "Order-lookup worker",
            "arguments": {"input": "order 1043"},
            "result": "delivered 3 days ago",
            "runtime_ids": {"event_id": "t1"},
        },
        {
            "name": "get_order",
            "ok": True,
            "at": 1730454601.0,
            "worker_label": "Order-lookup worker",
        },
        {
            "name": "refund_worker",
            "ok": False,
            "at": 1730454603.0,
            "ended_at": 1730454604.0,
            "worker_label": "Returns coordinator",
            "delegated_to": "Refund worker",
            "arguments": {"input": "refund 1043"},
        },
        {
            "name": "issue_refund",
            "ok": False,
            "at": 1730454603.5,
            "worker_label": "Refund worker",
        },
        {
            "name": "send_reply",
            "ok": True,
            "at": 1730454605.0,
            "worker_label": "Returns coordinator",
        },
    ]
    timeline = worker_timeline([], DECLARED, run_key=RUN, traced_calls=traced)

    assert sorted(one["id"] for one in timeline["workers"] if one["observed"]) == [
        "order_lookup",
        "refund",
    ]
    events = [
        (
            one["type"],
            one["worker_id"],
            one.get("tool") or one.get("task") or one.get("result"),
            one.get("status"),
        )
        for one in timeline["events"]
    ]
    assert events == [
        ("delegated", "run-1/order_lookup", "order 1043", None),
        ("tool_call", "run-1/order_lookup", "get_order", "ok"),
        ("returned", "run-1/order_lookup", "delivered 3 days ago", "ok"),
        ("delegated", "run-1/refund", "refund 1043", None),
        ("tool_call", "run-1/refund", "issue_refund", "error"),
        ("returned", "run-1/refund", None, "error"),
        ("tool_call", None, "send_reply", "ok"),
    ]
    first, back = timeline["events"][0], timeline["events"][2]
    assert (
        first["by"] is None and first["delegation_id"] == back["delegation_id"] == "t1"
    )


def test_a_long_answer_is_cut_with_a_mark_that_it_goes_on_and_a_short_one_is_kept_whole():
    long_answer = "word " * 200
    traced = [
        {
            "name": "ask",
            "ok": True,
            "at": 1730454600.0,
            "ended_at": 1730454602.0,
            "worker_label": "Coordinator",
            "delegated_to": "Writer",
            "arguments": {"input": "draft"},
            "result": long_answer,
        },
        {
            "name": "ask",
            "ok": True,
            "at": 1730454603.0,
            "ended_at": 1730454604.0,
            "worker_label": "Coordinator",
            "delegated_to": "Writer",
            "arguments": {"input": "draft"},
            "result": "short",
        },
    ]
    returned = [
        one["result"]
        for one in worker_timeline([], [], run_key=RUN, traced_calls=traced)["events"]
        if one["type"] == "returned"
    ]
    assert (
        returned[0].endswith("word…")
        and len(returned[0]) <= 501
        and long_answer.startswith(returned[0][:-1])
    )
    assert returned[1] == "short"


def test_the_one_agent_of_an_app_that_hands_nothing_out_is_not_a_worker():
    traced = [
        {
            "name": "lookup",
            "ok": True,
            "at": 1730454600.0,
            "worker_label": "Support agent",
        }
    ]
    timeline = worker_timeline([], [], run_key=RUN, traced_calls=traced)
    assert timeline["workers"] == []
    assert [(one["worker_id"], one["tool"]) for one in timeline["events"]] == [
        (None, "lookup")
    ]


def test_a_declared_worker_that_hands_work_out_stays_a_worker_under_the_root_that_passed_it_control():
    # The root passes control with a tool, not a nested hand-off; the declared worker then runs another agent as a tool.
    traced = [
        {
            "name": "transfer_to_agent",
            "ok": True,
            "at": 1730454600.0,
            "worker_label": "root_agent",
            "arguments": {"agent_name": "researcher"},
            # As the harness records a control pass: its kind and the agent it went to.
            "kind": "transferred",
            "delegated_to": "researcher",
        },
        {
            "name": "find_sources",
            "ok": True,
            "at": 1730454601.0,
            "ended_at": 1730454603.0,
            "worker_label": "researcher",
            "kind": "delegated",
            "delegated_to": "source_finder",
            "arguments": {"request": "tides"},
            "result": "three sources",
        },
    ]
    declared = [
        {"id": "researcher", "name": "researcher"},
        {"id": "writer", "name": "writer"},
    ]
    timeline = worker_timeline([], declared, run_key=RUN, traced_calls=traced)

    workers = {
        one["id"]: (one["observed"], one["undeclared"]) for one in timeline["workers"]
    }
    # The root that passed control is the coordinator, and the control pass is the node the hand-off nests under.
    assert workers == {
        "researcher": (True, False),
        "writer": (False, False),
        "source_finder": (True, True),
    }
    assert timeline["coordinator"] == {"name": "root_agent"}
    assert [
        (one["type"], one["worker_id"], one.get("by")) for one in timeline["events"]
    ] == [
        ("transferred", "run-1/researcher", None),
        ("delegated", "run-1/source_finder", "run-1/researcher"),
        ("returned", "run-1/source_finder", None),
    ]


def test_background_jobs_launched_by_an_unnamed_agent_are_its_hand_offs_and_overlap():
    # Two jobs a supervisor launched run at once; the runtime names each job's agent but not the supervisor.
    traced = [
        {
            "name": "start_async_task",
            "ok": True,
            "at": 1730454608.4,
            "ended_at": 1730454645.5,
            "delegated_to": "coder",
            "arguments": "coder",
            "result": "code done",
            "runtime_ids": {"event_id": "a"},
        },
        {
            "name": "start_async_task",
            "ok": True,
            "at": 1730454608.5,
            "ended_at": 1730454655.2,
            "delegated_to": "researcher",
            "arguments": "researcher",
            "result": "research done",
            "runtime_ids": {"event_id": "b"},
        },
        {
            "name": "analyze_topic",
            "ok": True,
            "at": 1730454612.6,
            "worker_label": "researcher",
        },
        {
            "name": "run_code_check",
            "ok": True,
            "at": 1730454624.1,
            "worker_label": "coder",
        },
    ]
    events = worker_timeline([], [], run_key=RUN, traced_calls=traced)["events"]
    assert [
        (one["type"], (one["worker_id"] or "").split("/")[-1], one.get("by"))
        for one in events
    ] == [
        ("delegated", "coder", None),
        ("delegated", "researcher", None),
        ("tool_call", "researcher", None),
        ("tool_call", "coder", None),
        ("returned", "coder", None),
        ("returned", "researcher", None),
    ]


def test_a_record_says_whether_the_app_s_spans_were_received():
    assert worker_timeline(SPANS, DECLARED, run_key=RUN)["spans_received"] is True
    assert (
        worker_timeline([], DECLARED, run_key=RUN, traced_calls=_hand_offs())[
            "spans_received"
        ]
        is False
    )


def test_a_tool_named_like_a_control_pass_is_an_ordinary_call_without_the_recorded_kind():
    # A tool's name is not a record that control passed: only the harness's recorded kind is (MA-2).
    traced = [
        {
            "name": "transfer_to_billing_agent",
            "ok": True,
            "at": 1730454600.0,
            "worker_label": "triage",
        },
        {
            "name": "lookup",
            "ok": True,
            "at": 1730454601.0,
            "ended_at": 1730454602.0,
            "worker_label": "billing_agent",
            "delegated_to": "ledger_agent",
            "result": "found",
        },
    ]
    events = worker_timeline([], [], run_key=RUN, traced_calls=traced)["events"]
    assert "transferred" not in [one["type"] for one in events]
    assert ("delegated", "run-1/ledger_agent") in [
        (one["type"], one["worker_id"]) for one in events
    ]


def test_a_hand_off_stamped_after_it_ended_comes_before_its_return_with_its_start_unknown():
    # A runtime that writes a hand-off line once it is over stamps the line's own time, later than its end.
    traced = [
        {
            "name": "transfer_to_agent",
            "ok": True,
            "at": 1730454620.0,
            "ended_at": 1730454614.0,
            "worker_label": "root_agent",
            "arguments": {"agent_name": "inspiration_agent"},
            "kind": "transferred",
            "delegated_to": "inspiration_agent",
        },
        {
            "name": "place_agent",
            "ok": True,
            "at": 1730454620.9,
            "ended_at": 1730454618.9,
            "worker_label": "inspiration_agent",
            "kind": "delegated",
            "delegated_to": "place_agent",
            "result": "three places",
        },
        {
            "name": "poi_agent",
            "ok": True,
            "at": 1730454636.9,
            "ended_at": 1730454634.7,
            "worker_label": "inspiration_agent",
            "kind": "delegated",
            "delegated_to": "poi_agent",
            "result": "five sights",
        },
    ]
    events = worker_timeline([], [], run_key=RUN, traced_calls=traced)["events"]

    assert [(one["type"], one["worker_id"].split("/")[-1]) for one in events] == [
        ("transferred", "inspiration_agent"),
        ("delegated", "place_agent"),
        ("returned", "place_agent"),
        ("delegated", "poi_agent"),
        ("returned", "poi_agent"),
    ]
    assert [one["at"] for one in events if one["type"] == "delegated"] == [None, None]
    assert events[0]["at"] == "2024-11-01T09:50:14"


def test_a_hand_off_and_its_return_say_who_what_and_when():
    timeline = worker_timeline(SPANS, DECLARED, run_key=RUN)

    delegated = next(one for one in timeline["events"] if one["type"] == "delegated")
    returned = next(one for one in timeline["events"] if one["type"] == "returned")
    assert (delegated["worker_id"], delegated["task"], delegated["at"]) == (
        "run-1/order_lookup",
        "inspect order 1042",
        "t05",
    )
    assert (returned["worker_id"], returned["result"], returned["at"]) == (
        "run-1/order_lookup",
        "order 1042 is eligible; refund RF-7 exists",
        "t08",
    )


def test_simulated_people_in_the_world_are_never_shown_as_workers():
    # The world's own record of what simulated actors did: their rows name the actor, never a worker.
    acted = [
        {
            "name": "crm.approve_discount",
            "ok": True,
            "at": 1730454600.5,
            "actor_id": "manager",
        },
        {
            "name": "slack.post_message",
            "ok": True,
            "at": 1730454601.0,
            "actor_id": "coworker",
        },
    ]
    with_actors = worker_timeline(SPANS, DECLARED, run_key=RUN, traced_calls=acted)

    assert with_actors == worker_timeline(SPANS, DECLARED, run_key=RUN)
    assert not {"manager", "coworker"} & {one["id"] for one in with_actors["workers"]}


def _hand_offs(result="delivered 3 days ago"):
    return [
        {
            "name": "order_lookup",
            "ok": True,
            "at": 1730454600.0,
            "ended_at": 1730454602.0,
            "worker_label": "Returns coordinator",
            "delegated_to": "Order-lookup worker",
            "arguments": {"input": "order 1043"},
            "result": "eligible",
        },
        {
            "name": "get_order",
            "ok": True,
            "at": 1730454601.0,
            "worker_label": "Order-lookup worker",
            "arguments": {"order_id": "1043"},
            "result": result,
        },
        {
            "name": "list_refunds",
            "ok": False,
            "at": 1730454601.5,
            "worker_label": "Order-lookup worker",
            "arguments": {"order_id": "1043"},
            "error": "store unavailable",
        },
    ]


def test_each_tool_call_keeps_its_input_and_output_and_a_long_one_is_cut():
    long_result = {"rows": ["x" * 50] * 100}
    timeline = worker_timeline(
        [], DECLARED, run_key=RUN, traced_calls=_hand_offs(long_result)
    )
    calls = {
        one["tool"]: one for one in timeline["events"] if one["type"] == "tool_call"
    }

    assert calls["get_order"]["input"] == {"order_id": "1043"}
    assert (
        calls["get_order"]["output"].endswith("…")
        and len(calls["get_order"]["output"]) == 2001
    )
    # A failed call shows what it failed with.
    assert calls["list_refunds"]["output"] == "store unavailable"
    short = worker_timeline(
        [], DECLARED, run_key=RUN, traced_calls=_hand_offs({"status": "delivered"})
    )
    assert [
        one["output"] for one in short["events"] if one.get("tool") == "get_order"
    ] == [{"status": "delivered"}]
    # Spans carry theirs as text: JSON reads back as data, anything else stays text, nothing is None.
    spans = worker_timeline(SPANS, DECLARED, run_key=RUN)
    by_tool = {
        one["tool"]: one for one in spans["events"] if one["type"] == "tool_call"
    }
    assert (
        by_tool["get_order"]["input"] is None and by_tool["get_order"]["output"] is None
    )


def _team(leads, helpers, leaves, calls):
    """A coordinator with `leads` workers, each handing to `helpers`, each to `leaves`, each making `calls` tool
    calls with 2 KB inputs and outputs; distinct names, so every agent is its own worker."""
    big = "y" * 2000
    spans, count = [_span("root", None, "agent", "coordinator", "t0000")], [0]

    def at():
        count[0] += 1
        return f"t{count[0]:04d}"

    for lead in range(leads):
        lead_id = f"l{lead}"
        spans.append(_span(lead_id, "root", "agent", f"lead {lead}", at()))
        for helper in range(helpers):
            helper_id = f"{lead_id}h{helper}"
            spans.append(
                _span(helper_id, lead_id, "agent", f"helper {lead}.{helper}", at())
            )
            for leaf in range(leaves):
                leaf_id = f"{helper_id}f{leaf}"
                spans.append(
                    _span(
                        leaf_id,
                        helper_id,
                        "agent",
                        f"leaf {lead}.{helper}.{leaf}",
                        at(),
                    )
                )
                for call in range(calls):
                    spans.append(
                        _span(
                            f"{leaf_id}c{call}",
                            leaf_id,
                            "tool",
                            "lookup",
                            at(),
                            input=big,
                            output=big,
                        )
                    )
    return spans


def test_a_large_team_s_record_stays_bounded():
    # 10 leads x 3 helpers x 3 leaves: 129 agents, 3 levels below the coordinator, 900 tool calls with 2 KB each way.
    timeline = worker_timeline(_team(10, 3, 3, 10), [], run_key=RUN)

    calls = [one for one in timeline["events"] if one["type"] == "tool_call"]
    assert len(timeline["events"]) <= harness_workers._MAX_EVENTS
    assert (
        len(json.dumps(timeline))
        # Each step now also carries its hand-off and trace ids for placement and Open in Traces.
        < harness_workers._PAYLOAD_BUDGET + 400 * harness_workers._MAX_EVENTS
    )
    # The first calls keep their input and output; past the size limit a call keeps its tool, worker and time.
    assert calls[0]["input"] == "y" * 2000 and "input_omitted" not in calls[0]
    assert calls[-1]["input"] is None and calls[-1]["input_omitted"] is True
    assert calls[-1]["tool"] == "lookup" and calls[-1]["worker_id"] and calls[-1]["at"]


def test_a_record_past_its_step_limit_keeps_the_first_steps_and_counts_the_rest():
    timeline = worker_timeline(_team(5, 2, 2, 120), [], run_key=RUN)

    assert len(timeline["events"]) == harness_workers._MAX_EVENTS
    assert (
        timeline["events_omitted"]
        == 5 * 2 * 2 * 120 + 5 + 5 * 2 + 5 * 2 * 2 - harness_workers._MAX_EVENTS
    )
    assert [one["seq"] for one in timeline["events"][:3]] == [1, 2, 3]


def test_the_caller_s_request_and_the_coordinator_s_reply_frame_the_hand_offs():
    turns = [
        {"role": "user", "text": "Hello", "at": "2024-11-01T09:49:56"},
        {
            "role": "user",
            "text": "Please refund order 1043",
            "at": "2024-11-01T09:49:58",
        },
        {"role": "agent", "text": "Let me check", "at": "2024-11-01T09:49:59"},
        {"role": "user", "text": "Still there?", "at": "2024-11-01T09:50:01"},
        {"role": "agent", "text": "Your refund is issued", "at": "2024-11-01T09:50:03"},
        {"role": "agent", "text": "Anything else?", "at": "2024-11-01T09:50:05"},
    ]
    timeline = worker_timeline(
        [],
        DECLARED,
        run_key=RUN,
        traced_calls=_hand_offs(),
        turns=turns,
        started_at="2024-11-01T09:49:55",
    )

    assert timeline["request"] == {
        "text": "Please refund order 1043",
        "at": "2024-11-01T09:49:58",
    }
    assert timeline["response"] == {
        "text": "Your refund is issued",
        "at": "2024-11-01T09:50:03",
    }
    assert timeline["coordinator"] == {"name": "Returns coordinator"}
    assert timeline["started_at"] == "2024-11-01T09:49:55"
    # Every message is kept on the steps' clock, so the API can group the steps by turn.
    assert [(one["role"], one["text"]) for one in timeline["messages"]] == [
        (one["role"], one["text"]) for one in turns
    ]


def test_a_request_reply_or_coordinator_not_seen_is_none_so_it_reads_unknown():
    # No messages: the hand-offs are known, the request and the reply are not.
    unseen = worker_timeline([], DECLARED, run_key=RUN, traced_calls=_hand_offs())
    assert (
        unseen["request"] is None
        and unseen["response"] is None
        and unseen["started_at"] is None
    )
    # Nothing handed out: no request or reply belongs to a hand-off.
    alone = worker_timeline(
        [],
        DECLARED,
        run_key=RUN,
        turns=[{"role": "user", "text": "hi", "at": "2024-11-01T09:49:58"}],
    )
    assert alone["request"] is None and alone["coordinator"] is None
    # From spans alone the coordinator is the top-level agent.
    assert worker_timeline(SPANS, DECLARED, run_key=RUN)["coordinator"] == {
        "name": "Returns coordinator"
    }


@pytest.mark.django_db
def test_a_recorded_timeline_places_the_call_s_messages_on_the_tool_calls_clock(
    user, workspace, storage, settings
):
    from unittest.mock import patch

    from simulate.models import CallExecution
    from simulate.models.chat_message import ChatMessageModel
    from simulate.services import harness_workers
    from simulate.tests.test_harness_versioning_picks import _create, _environment

    settings.HARNESS_VERSIONING_ENABLED = True
    settings.HARNESS_BUNDLE_CERTIFY_ENABLED = False
    environment = _environment(user, workspace, "workers-turns", storage)
    run = _create(environment, key="t")
    call = CallExecution.objects.filter(test_execution_id=run.test_execution_id).first()
    # The first hand-off was sealed 5.9 s into the call, at this epoch second.
    sealed = _hand_offs()
    sealed[0]["start_time_ms"] = 5900
    call.provider_call_data = {"livekit": {"tool_calls": sealed}}
    call.save(update_fields=["provider_call_data"])
    for role, text, offset in (
        ("user", "Refund order 1043", 4300),
        ("assistant", "Refund issued", 17100),
    ):
        ChatMessageModel.objects.create(
            call_execution=call,
            role=role,
            messages=[text],
            content=[{"role": role, "content": text, "start_time_ms": offset}],
            session_id=f"alk-chat-{call.id}",
            tool_calls=[],
            organization=call.test_execution.run_test.organization,
            workspace=call.test_execution.run_test.workspace,
        )
    # A sub-actor's line is neither the caller's request nor the agent's reply.
    ChatMessageModel.objects.create(
        call_execution=call,
        role="system",
        messages=["Meera: ask for 20%"],
        content=[
            {"role": "system", "content": "Meera: ask for 20%", "start_time_ms": 5000}
        ],
        session_id=f"alk-chat-{call.id}",
        tool_calls=[],
        organization=call.test_execution.run_test.organization,
        workspace=call.test_execution.run_test.workspace,
    )

    with patch(
        "simulate.services.run_traces_v3.list_run_traces", return_value=_traced(None)
    ):
        timeline = harness_workers.record_call_workers(str(call.id))

    assert timeline["started_at"] == "2024-11-01T09:49:54.100000"
    assert timeline["request"] == {
        "text": "Refund order 1043",
        "at": "2024-11-01T09:49:58.400000",
    }
    assert timeline["response"] == {
        "text": "Refund issued",
        "at": "2024-11-01T09:50:11.200000",
    }


@pytest.mark.django_db
def test_a_requester_with_no_organization_is_refused_before_any_call_is_read(
    django_assert_num_queries,
):
    from types import SimpleNamespace

    from django.http import Http404

    from simulate.views.run_results_v3 import _call_for_request

    request = SimpleNamespace(
        organization=None, user=SimpleNamespace(organization=None)
    )
    with django_assert_num_queries(0), pytest.raises(Http404):
        _call_for_request(request, "00000000-0000-0000-0000-000000000000")


@pytest.mark.django_db
def test_a_voice_call_s_messages_come_from_its_transcript_when_it_has_no_chat_messages(
    user, workspace, storage, settings
):
    from simulate.models import CallExecution, CallTranscript
    from simulate.services import harness_workers
    from simulate.tests.test_harness_versioning_picks import _create, _environment

    settings.HARNESS_VERSIONING_ENABLED = True
    settings.HARNESS_BUNDLE_CERTIFY_ENABLED = False
    run = _create(_environment(user, workspace, "workers-voice", storage), key="o")
    call = CallExecution.objects.filter(test_execution_id=run.test_execution_id).first()
    sealed = _hand_offs()
    sealed[0]["start_time_ms"] = 5900
    for role, text, offset in (
        ("user", "Refund order 1043", 4300),
        ("sub_actor", "Ask for 20%", 5000),
        ("assistant", "Refund issued", 17100),
    ):
        CallTranscript.objects.create(
            call_execution=call, speaker_role=role, content=text, start_time_ms=offset
        )

    turns = harness_workers._call_turns(call, sealed)

    # Only the caller's and the agent's lines, on the tool calls' clock.
    assert turns == [
        {
            "role": "user",
            "text": "Refund order 1043",
            "at": "2024-11-01T09:49:58.400000",
        },
        {"role": "agent", "text": "Refund issued", "at": "2024-11-01T09:50:11.200000"},
    ]


def test_a_worker_returning_its_whole_conversation_is_summarised_by_its_last_message():
    from simulate.services.harness_workers import _summary

    returned = json.dumps(
        {
            "messages": [
                "content='Implement a token bucket rate limiter' additional_kwargs={}",
                "content='' additional_kwargs={'refusal': None}",
                "content='Done: limiter.py with tests' additional_kwargs={}",
                {"content": "", "type": "ai"},
            ]
        }
    )

    assert _summary(returned) == "Done: limiter.py with tests"
    assert _summary("Refund RF-8 issued") == "Refund RF-8 issued"


def test_a_finished_job_is_one_hand_off_and_one_still_running_has_no_return():
    from simulate.services.harness_workers import worker_timeline

    finished = {
        "name": "start_async_task",
        "delegated_to": "coder",
        "arguments": "build",
        "at": 10.0,
        "ended_at": 40.0,
        "result": "done",
        "worker_label": "agent",
    }
    running = {
        "name": "start_async_task",
        "delegated_to": "researcher",
        "arguments": "read",
        "at": 11.0,
        "worker_label": "agent",
    }
    tool = {
        "name": "analyze_topic",
        "worker_label": "researcher",
        "at": 20.0,
        "ended_at": 20.5,
    }

    timeline = worker_timeline(
        [], [], run_key="run-1", traced_calls=[finished, running, tool]
    )

    kinds = [(e["type"], e["worker_id"].split("/")[-1]) for e in timeline["events"]]
    assert kinds.count(("delegated", "coder")) == 1
    assert ("returned", "coder") in kinds
    assert ("delegated", "researcher") in kinds and (
        "returned",
        "researcher",
    ) not in kinds


def test_a_falsy_answer_is_kept_and_only_a_missing_one_is_unknown():
    from simulate.services.harness_workers import _summary

    assert [_summary(one) for one in (0, False, None, "")] == ["0", "False", None, None]


def test_escaped_line_breaks_in_an_answer_read_as_line_breaks():
    from simulate.services.harness_workers import _summary

    assert (
        _summary("# Plan\\n\\nStep one\\nIt\\'s done")
        == "# Plan\n\nStep one\nIt's done"
    )
    assert (
        _summary("Line one\nLine two with a literal \\n kept")
        == "Line one\nLine two with a literal \\n kept"
    )


def _sealed(
    name, at, *, by, kind, span, parent="", relationship="observed", to=None, ended=None
):
    """A sealed tool-trace row as the harness writes it with recorded relationships."""
    row = {
        "name": name,
        "ok": True,
        "at": at,
        "arguments": {},
        "result": "ok",
        "worker_label": by,
        "kind": kind,
        "span_id": span,
        "parent_span_id": parent,
        "relationship": relationship,
    }
    if to:
        row["delegated_to"] = to
    if ended:
        row["ended_at"] = ended
    return row


def _placed(timeline):
    from simulate.services.harness_worker_steps import worker_steps

    steps = worker_steps(timeline["events"])["steps"]
    keys = {s["key"]: s for s in steps}
    return [
        (
            s["kind"],
            (s.get("call") or {}).get("tool") or s["worker_id"],
            (
                (s["handoff"] or {}).get("delegation_id")
                if s["kind"] == "handoff"
                else None
            ),
            (
                (keys[s["parent"]]["handoff"] or {}).get("delegation_id")
                if s["parent"] is not None
                else None
            ),
            s["relationship"],
        )
        for s in steps
    ]


def test_two_overlapping_hand_offs_to_one_worker_place_each_call_by_its_recorded_parent():
    # Rishav's case: delegated A, delegated B, search_b, search_a, returned B, returned A.
    rows = [
        _sealed(
            "search",
            1.0,
            by="Lead",
            kind="delegated",
            span="A",
            to="Searcher",
            ended=6.0,
        ),
        _sealed(
            "search",
            2.0,
            by="Lead",
            kind="delegated",
            span="B",
            to="Searcher",
            ended=5.0,
        ),
        _sealed(
            "search_b", 3.0, by="Searcher", kind="tool_call", span="t1", parent="B"
        ),
        _sealed(
            "search_a", 4.0, by="Searcher", kind="tool_call", span="t2", parent="A"
        ),
    ]
    placed = _placed(worker_timeline([], DECLARED, run_key=RUN, traced_calls=rows))
    tools = {name: parent for kind, name, _, parent, _ in placed if kind == "tool_call"}
    assert tools == {"search_b": "B", "search_a": "A"}


def test_a_control_pass_is_read_from_the_recorded_kind_and_the_leaf_tool_sits_under_it():
    rows = [
        _sealed(
            "transfer_to_agent",
            1.0,
            by="root",
            kind="transferred",
            span="T",
            to="researcher",
            relationship="unknown",
        ),
        _sealed(
            "search", 2.0, by="researcher", kind="tool_call", span="t1", parent="T"
        ),
    ]
    timeline = worker_timeline([], DECLARED, run_key=RUN, traced_calls=rows)
    assert [e["type"] for e in timeline["events"]] == ["transferred", "tool_call"]
    placed = _placed(timeline)
    assert [(kind, parent) for kind, _, _, parent, _ in placed] == [
        ("handoff", None),
        ("tool_call", "T"),
    ]


def test_a_call_the_harness_could_not_place_stays_at_the_top_as_unknown():
    rows = [
        _sealed(
            "lookup",
            1.0,
            by="Lead",
            kind="delegated",
            span="A",
            to="Searcher",
            ended=5.0,
        ),
        _sealed(
            "search",
            2.0,
            by="Searcher",
            kind="tool_call",
            span="t1",
            relationship="unknown",
        ),
    ]
    placed = _placed(worker_timeline([], DECLARED, run_key=RUN, traced_calls=rows))
    assert [(kind, parent, rel) for kind, _, _, parent, rel in placed] == [
        ("handoff", None, "observed"),
        ("tool_call", None, "unknown"),
    ]


def test_per_field_omitted_flags_are_kept_from_the_harness():
    rows = [
        _sealed(
            "lookup",
            1.0,
            by="Lead",
            kind="delegated",
            span="A",
            to="Searcher",
            ended=5.0,
        ),
        {
            **_sealed(
                "search", 2.0, by="Searcher", kind="tool_call", span="t1", parent="A"
            ),
            "input_omitted": True,
        },
    ]
    events = worker_timeline([], DECLARED, run_key=RUN, traced_calls=rows)["events"]
    tool = next(e for e in events if e["type"] == "tool_call")
    assert tool.get("input_omitted") is True and not tool.get("output_omitted")


def test_each_worker_carries_the_model_and_tokens_of_its_own_model_calls():
    spans = [
        _span("a", None, "agent", "coordinator", "t1", "t9"),
        _span("al", "a", "llm", "plan", "t1", "t1", model="coord-model", tokens=50),
        _span("b", "a", "agent", "researcher", "t2", "t8"),
        _span("bl1", "b", "llm", "think", "t3", "t3", model="worker-model", tokens=100),
        _span("bl2", "b", "llm", "answer", "t4", "t4", model="worker-model", tokens=20),
    ]
    [worker] = worker_timeline(spans, [], run_key=RUN)["workers"]
    # The coordinator's own model call is not the worker's.
    assert (worker["model"], worker["tokens"]) == ("worker-model", 120)


def test_a_nested_hand_off_from_spans_names_the_hand_off_its_maker_ran_in():
    spans = [
        _span("a", None, "agent", "coordinator", "t1", "t9"),
        _span("al", "a", "llm", "route", "t1", "t1"),
        _span("b", "a", "agent", "planner", "t2", "t8"),
        _span("bl", "b", "llm", "plan", "t2", "t2"),
        _span("c", "b", "agent", "searcher", "t3", "t7"),
        _span("ct", "c", "tool", "search", "t4", "t4", trace_id="tr1"),
    ]
    events = worker_timeline(spans, [], run_key=RUN)["events"]
    nested = next(
        e
        for e in events
        if e["type"] == "delegated" and e["worker_id"] == "run-1/searcher"
    )
    tool = next(e for e in events if e["type"] == "tool_call")
    assert (nested["delegation_id"], nested["parent_delegation_id"]) == ("c", "b")
    assert (tool["delegation_id"], tool["trace_id"]) == ("c", "tr1")


def test_a_receipt_sub_goal_names_the_task_or_one_worker_as_its_subject():
    from simulate.serializers.hosted_harness import HarnessSubGoalSerializer

    worker = {
        "name": "lookup_read_only",
        "held": False,
        "reason": "wrote an order",
        "judged": False,
        "verdict": "fail",
        "subject": "worker:order_lookup",
    }
    task = {"name": "reply_truthful", "held": True, "reason": None, "judged": False}
    for goal in (worker, task):
        assert HarnessSubGoalSerializer(data=goal).is_valid()
    assert not HarnessSubGoalSerializer(
        data={**task, "subject": "order_lookup"}
    ).is_valid()


def _ended(trial, past_deadline=()):
    from simulate.services.harness_workers import worker_states

    spans = [
        _span("c", None, "agent", "coordinator", "t0", "t9"),
        _span("cl", "c", "llm", "generation", "t0", "t0"),
        _span("d1", "c", "tool", "order_lookup", "t1", "t3"),
        _span("o", "d1", "agent", "Order-lookup worker", "t1", "t3"),
        _span("og", "o", "tool", "get_order", "t2", "t2"),
        _span("d2", "c", "tool", "refund", "t4", "t5"),
        _span("r", "d2", "agent", "Refund worker", "t4", "t5", status="ERROR"),
        _span("d3", "c", "tool", "audit", "t6", None),
        _span("a", "d3", "agent", "Audit worker", "t6", None),
        _span("al", "a", "llm", "generation", "t6", "t6"),
    ]
    declared = [*DECLARED, {"id": "notifier", "name": "Notifier"}]
    timeline = worker_states(
        worker_timeline(spans, declared, run_key=RUN),
        trial=trial,
        past_deadline=past_deadline,
    )
    return {one["id"]: one["state"] for one in timeline["workers"]}, timeline


def test_each_worker_ends_in_one_state_and_a_worker_still_out_takes_the_trial_s_ending():
    states, timeline = _ended("ended")
    # Returned: done, or failed when its return carries an error. Still out when the trial ended: no return, nothing
    # more claimed about why.
    assert states == {
        "order_lookup": "completed",
        "refund": "failed",
        "audit": "no_return",
        "notifier": "not_observed",
    }
    # Its partial events are kept, and no return is made up for it (MA-1).
    kinds = [(one["type"], one["worker_id"]) for one in timeline["events"]]
    assert ("delegated", "run-1/audit") in kinds
    assert ("returned", "run-1/audit") not in kinds
    for trial, still_out in [
        ("infrastructure", "infrastructure"),
        ("cancel_requested", "cancel_requested"),
        ("cancelled", "cancelled"),
    ]:
        states, _ = _ended(trial)
        # A worker that already ended keeps its state whatever happened to the trial after.
        assert (states["order_lookup"], states["refund"], states["audit"]) == (
            "completed",
            "failed",
            still_out,
        )


def test_a_worker_the_world_refused_past_its_deadline_ends_timed_out_even_after_a_late_return():
    from types import SimpleNamespace

    from simulate.services.harness_workers import deadline_refused
    from simulate.services.hosted_harness_ingestion import TRIAL_STATE_KEY

    states, _ = _ended("ended", past_deadline={"order_lookup"})
    assert (states["order_lookup"], states["refund"]) == ("timed_out", "failed")

    def entry(who, code):
        return {"who": who, "refused_actions": [{"action": "x", "code": code}]}

    call = SimpleNamespace(
        call_metadata={
            TRIAL_STATE_KEY: {
                "state_changes": [
                    entry("worker:refund", "deadline_passed"),
                    entry("worker:order_lookup", "not_permitted"),
                    entry("agent", "deadline_passed"),
                ]
            }
        }
    )
    assert deadline_refused(call) == {"refund"}
    assert deadline_refused(SimpleNamespace(call_metadata={})) == set()


def test_a_trial_s_ending_is_read_from_its_call_its_receipt_and_its_job():
    from types import SimpleNamespace

    from simulate.models import CallExecution, HostedHarnessJob
    from simulate.services.harness_workers import trial_ending

    def call(status, receipt=None):
        return SimpleNamespace(
            status=status,
            call_metadata={"hosted_harness_receipt": receipt} if receipt else {},
        )

    stopped = SimpleNamespace(state=HostedHarnessJob.State.CANCELED, failure=None)
    stopping = SimpleNamespace(state=HostedHarnessJob.State.RUNNING, failure=None)
    lost = SimpleNamespace(
        state=HostedHarnessJob.State.FAILED, failure={"domain": "infrastructure"}
    )
    cancelled = CallExecution.CallStatus.CANCELLED
    assert trial_ending(call(cancelled), stopped) == "cancelled"
    # Stopping our observation is not a confirmed cancel.
    assert trial_ending(call(cancelled), stopping) == "cancel_requested"
    assert (
        trial_ending(
            call("completed", {"failure": {"domain": "connectivity"}}), stopping
        )
        == "infrastructure"
    )
    assert (
        trial_ending(call("completed", {"failure": {"domain": "agent"}}), stopping)
        == "ended"
    )
    assert trial_ending(call("completed", {"failure": None}), stopping) == "ended"
    # A call the job closed without a receipt ended the way the job did.
    assert trial_ending(call(CallExecution.CallStatus.FAILED), lost) == "infrastructure"


@pytest.mark.django_db
def test_a_cancelled_run_reads_each_call_it_cut_short(
    user, workspace, storage, settings, django_capture_on_commit_callbacks
):
    from unittest.mock import patch

    from django.utils import timezone

    from simulate.models import CallExecution, HostedHarnessJob
    from simulate.services.hosted_harness import project_terminal_job_onto_run
    from simulate.tests.test_harness_versioning_picks import _create, _environment

    settings.HARNESS_VERSIONING_ENABLED = True
    settings.HARNESS_BUNDLE_CERTIFY_ENABLED = False
    run = _create(_environment(user, workspace, "workers-cancel", storage), key="c")
    call = CallExecution.objects.filter(test_execution_id=run.test_execution_id).first()
    CallExecution.objects.filter(id=call.id).update(
        status=CallExecution.CallStatus.ONGOING
    )
    HostedHarnessJob.no_workspace_objects.filter(id=run.id).update(
        state=HostedHarnessJob.State.CANCELED
    )
    run.refresh_from_db()
    with (
        patch(
            "simulate.services.hosted_harness_ingestion._dispatch_worker_read"
        ) as read,
        django_capture_on_commit_callbacks(execute=True),
    ):
        project_terminal_job_onto_run(run, timezone.now())
    # A call cut short keeps what its workers did before the end.
    assert [one.args for one in read.call_args_list] == [(call.id,)]


def test_a_worker_seen_only_through_its_tool_calls_gets_no_invented_end_state():
    from simulate.services.harness_workers import worker_states

    traced = [
        {"name": "get_order", "ok": True, "at": 1.0, "worker_id": "order_lookup"},
        {"name": "issue_refund", "ok": True, "at": 2.0, "worker_id": "refund"},
    ]
    timeline = worker_timeline([], DECLARED, run_key=RUN, traced_calls=traced)
    workers = worker_states(timeline, trial="ended")["workers"]
    # No hand-off was recorded, so nothing says the worker was still out when the trial ended.
    assert [one.get("state") for one in workers if one["observed"]] == [None, None]
    # The world refusing one past its deadline is still a recorded end.
    late = worker_states(timeline, trial="ended", past_deadline={"refund"})["workers"]
    assert {one["id"]: one.get("state") for one in late}["refund"] == "timed_out"
