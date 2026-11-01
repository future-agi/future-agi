"""A multi-agent call's workers, from the spans the app exported: who was delegated what, what each did, what came back."""

import pytest

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
    _span("s2", "s1", "tool", "order_lookup", "t04", "t09", input='{"input": "inspect order 1042"}'),
    _span("s3", "s2", "agent", "Order-lookup worker", "t05", "t08", attributes={"graph.node.id": "Order-lookup worker"},
          input="inspect order 1042", output="order 1042 is eligible; refund RF-7 exists"),
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
    assert workers["order_lookup"]["worker_id"] == "run-1/order_lookup" and workers["order_lookup"]["observed"]
    assert workers["refund"]["name"] == "Refund worker" and workers["refund"]["observed"]
    # Declared names are not proof: a worker with no spans of its own is not observed.
    assert workers["audit"]["observed"] is False
    assert not any(one["undeclared"] for one in timeline["workers"])

    events = [(one["type"], one["worker_id"], one.get("tool")) for one in timeline["events"]]
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
    delegated = {one["worker_id"]: one for one in timeline["events"] if one["type"] == "delegated"}
    returned = {one["worker_id"]: one for one in timeline["events"] if one["type"] == "returned"}
    assert delegated["run-1/order_lookup"]["delegation_id"] == returned["run-1/order_lookup"]["delegation_id"] == "s2"
    assert delegated["run-1/order_lookup"]["task"] == "inspect order 1042"
    assert returned["run-1/refund"]["result"] == "reused RF-7"
    assert [one["seq"] for one in timeline["events"]] == list(range(1, 9))
    assert {one["attribution"] for one in timeline["events"]} == {"reported"}
    assert next(one for one in timeline["events"] if one.get("tool") == "issue_refund")["status"] == "error"


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
        ("tool_call", None),
    ]
    assert timeline["events"][1]["worker_id"] == "run-1/researcher"


def test_no_agent_spans_means_no_worker_was_observed():
    timeline = worker_timeline([_span("x", None, "llm", "response", "t1", "t2")], DECLARED, run_key=RUN)
    assert [one["observed"] for one in timeline["workers"]] == [False, False, False]
    assert timeline["events"] == []


def test_a_tool_call_with_no_agent_above_it_is_unattributed_not_the_coordinator_s():
    orphan = _span("o1", None, "tool", "send_reply", "t30", "t30")
    events = worker_timeline([*SPANS, orphan], DECLARED, run_key=RUN)["events"]
    by_span = {one.get("span_id"): one for one in events if one["type"] == "tool_call"}
    assert by_span["o1"]["worker_id"] is None and by_span["o1"]["attribution"] == "unattributed"
    assert by_span["s9"]["worker_id"] is None and by_span["s9"]["attribution"] == "reported"


def test_a_parent_loop_in_broken_spans_does_not_hang():
    spans = [_span("p", "q", "tool", "x", "t1"), _span("q", "p", "tool", "y", "t2")]
    assert [one["worker_id"] for one in worker_timeline(spans, [], run_key=RUN)["events"]] == [None, None]


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
def test_a_call_keeps_its_worker_timeline_with_the_declared_workers(user, workspace, storage, settings, monkeypatch):
    from unittest.mock import patch

    from simulate.models import CallExecution, HarnessEnvironmentVersion
    from simulate.services import harness_workers
    from simulate.tests.test_harness_versioning_picks import _create, _environment

    settings.HARNESS_VERSIONING_ENABLED = True
    settings.HARNESS_BUNDLE_CERTIFY_ENABLED = False
    environment = _environment(user, workspace, "workers-record", storage)
    version = environment.environment_version
    HarnessEnvironmentVersion._base_manager.filter(id=version.id).update(
        documents={**(version.documents or {}), "contract": {**(version.documents or {}).get("contract", {}), "workers": DECLARED}}
    )
    run = _create(environment, key="w")
    call = CallExecution.objects.filter(test_execution_id=run.test_execution_id).first()
    reader = _Reader(SPANS)

    with patch("simulate.services.run_traces_v3.list_run_traces", return_value=_traced()), patch(
        "tracer.services.clickhouse.v2.get_reader", return_value=reader
    ):
        timeline = harness_workers.record_call_workers(str(call.id))

    call.refresh_from_db()
    stored = call.call_metadata[harness_workers.WORKERS_KEY]
    assert stored == timeline and stored["collected_at"]
    assert reader.asked == (["t1"], "p1")
    workers = {one["id"]: one for one in stored["workers"]}
    assert workers["order_lookup"]["worker_id"] == f"{run.run_id}/order_lookup" and workers["order_lookup"]["observed"]
    assert workers["audit"]["observed"] is False

    with patch("simulate.services.run_traces_v3.list_run_traces", return_value=_traced(None)):
        assert harness_workers.record_call_workers(str(call.id)) is None

    # No spans reach us, but the runtime named the worker of a sealed tool call: that alone is a timeline.
    call.provider_call_data = {"livekit": {"tool_calls": [{"name": "get_order", "ok": True, "at": 1.0, "by": "order_lookup"}]}}
    call.save(update_fields=["provider_call_data"])
    with patch("simulate.services.run_traces_v3.list_run_traces", return_value=_traced(None)), patch(
        "tracer.services.clickhouse.v2.get_reader", side_effect=AssertionError("no spans to read")
    ):
        reported = harness_workers.record_call_workers(str(call.id))
    assert [(one["worker_id"], one["tool"]) for one in reported["events"]] == [(f"{run.run_id}/order_lookup", "get_order")]


@pytest.mark.django_db
def test_a_declared_worker_not_seen_yet_gets_one_more_read_later():
    from unittest.mock import patch

    from simulate.tasks import alk_sim

    seen = {"workers": [{"observed": True}]}
    missing = {"workers": [{"observed": True}, {"observed": False}]}
    with patch("simulate.services.harness_workers.record_call_workers", side_effect=[missing, seen, missing]), patch.object(
        alk_sim.record_call_workers, "apply_async"
    ) as again:
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
    assert [one["type"] for one in worker_timeline(spans, [], run_key=RUN)["events"]] == [
        "delegated",
        "tool_call",
        "returned",
    ]


@pytest.mark.django_db
def test_the_trial_view_reads_the_kept_timeline_and_says_when_there_is_none(user, workspace, storage, settings, auth_client):
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
    timeline = {**worker_timeline(SPANS, DECLARED, run_key=RUN), "collected_at": "2026-10-31T12:00:00Z"}
    locked_call_metadata_update(call.id, lambda metadata: metadata.update({WORKERS_KEY: timeline}) or True)
    found = auth_client.get(url)

    assert (missing.status_code, missing.json()["code"]) == (404, "workers_unavailable")
    assert found.status_code == 200, found.content
    assert [one["type"] for one in found.json()["events"]][:2] == ["delegated", "tool_call"]
    assert {one["id"] for one in found.json()["workers"]} == {"order_lookup", "refund", "audit"}


def test_framework_containers_and_empty_agent_inputs_as_the_openai_agents_instrumentation_writes_them():
    # traceAI's OpenAI Agents nesting: the SDK's workflow is an agent span above the coordinator, a chain sits between
    # each delegating tool call and its worker, and agent spans carry an empty object for input and output.
    spans = [
        _span("w", None, "agent", "Agent workflow", "t00", "t30"),
        _span("wc", "w", "chain", "Agent workflow", "t00", "t30", input="{}"),
        _span("c", "wc", "agent", "Returns coordinator", "t01", "t29", input="{}"),
        _span("ct", "c", "chain", "turn", "t02", "t10"),
        _span("cl", "ct", "llm", "generation", "t02", "t03"),
        _span("d", "ct", "tool", "order_lookup", "t04", "t09", input='{"input": "Order 1043"}', output="1043 is eligible"),
        _span("dc", "d", "chain", "Agent workflow", "t04", "t09", input="{}"),
        _span("o", "dc", "agent", "Order-lookup worker", "t05", "t08", input="{}", output="{}"),
        _span("ot", "o", "chain", "turn", "t05", "t07"),
        _span("og", "ot", "tool", "get_order", "t06", "t06"),
    ]
    timeline = worker_timeline(spans, DECLARED[:1], run_key=RUN)

    assert [(one["id"], one["undeclared"]) for one in timeline["workers"]] == [("order_lookup", False)]
    assert [(one["type"], one["worker_id"], one.get("tool")) for one in timeline["events"]] == [
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
    declared = [{"id": "shakespearean_bard", "name": "Shakespearean bard"}, {"id": "x_post_verifier"}]
    traced = [
        {"name": "write_post", "ok": True, "at": 1730454600.5, "by": "shakespearean_bard", "task": "Write a post",
         "runtime_ids": {"event_id": "e2", "parent_event_id": "e1"}},
        {"name": "verify_post", "ok": False, "at": 1730454601.0, "by": "x_post_verifier"},
        {"name": "lookup", "ok": True, "at": 1730454602.0, "by": "Researcher"},
        {"name": "send_reply", "ok": True, "at": 1730454603.0},
    ]
    timeline = worker_timeline([], declared, run_key=RUN, traced_calls=traced)

    workers = {one["id"]: one for one in timeline["workers"]}
    assert workers["shakespearean_bard"]["observed"] and not workers["shakespearean_bard"]["undeclared"]
    assert workers["shakespearean_bard"]["instance_ids"] == ["shakespearean_bard"]
    assert workers["researcher"]["undeclared"]
    events = [(one["worker_id"], one["tool"], one["status"]) for one in timeline["events"]]
    assert events == [
        ("run-1/shakespearean_bard", "write_post", "ok"),
        ("run-1/x_post_verifier", "verify_post", "error"),
        ("run-1/researcher", "lookup", "ok"),
    ]
    first = timeline["events"][0]
    assert (first["task"], first["runtime_event_id"], first["runtime_parent_event_id"]) == ("Write a post", "e2", "e1")
    assert first["at"] == "2024-11-01T09:50:00.500000" and first["attribution"] == "reported"

    # The same call seen in the worker's spans is shown once.
    both = worker_timeline(SPANS, DECLARED, run_key=RUN, traced_calls=[{"name": "get_order", "by": "Order-lookup worker"}])
    assert [one.get("tool") for one in both["events"]].count("get_order") == 1
