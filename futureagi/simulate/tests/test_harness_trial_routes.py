from datetime import UTC, datetime

import pytest

from simulate.serializers.call_workers_v3 import TrialRoutesResponseSerializer
from simulate.services.harness_trial_routes import Trial, trial_routes
from simulate.services.harness_worker_steps import served_record
from simulate.tests.test_harness_versioning_backfill import (  # noqa: F401 - fixture
    storage as storage,
)

NOW = datetime(2026, 11, 1, 11, tzinfo=UTC)
WORKERS = {
    "refunds": "Refunds specialist",
    "escalations": "Escalations",
    "shipping": "Shipping specialist",
}


def at(second):
    return f"2026-11-01T10:00:{second:02d}"


class _Call:
    """A trial's events, written in order: hand-offs, their returns and tool calls."""

    def __init__(self):
        self.events, self.seq = [], 0

    def _next(self):
        self.seq += 1
        return self.seq

    def ask(self, worker, by=None, *, answer="done", status="ok", parent=None):
        seq = self._next()
        ident = f"d{seq}"
        handed = {
            "seq": seq,
            "type": "delegated",
            "worker_id": f"run/{worker}",
            "by": f"run/{by}" if by else None,
            "delegation_id": ident,
            "task": f"task for {worker}",
            "at": at(seq),
        }
        if parent:
            handed["parent_delegation_id"] = parent
        self.events.append(handed)
        return ident, answer, status

    def back(self, handed):
        ident, answer, status = handed
        worker = next(
            one["worker_id"] for one in self.events if one.get("delegation_id") == ident
        )
        seq = self._next()
        self.events.append(
            {
                "seq": seq,
                "type": "returned",
                "worker_id": worker,
                "delegation_id": ident,
                "result": answer,
                "status": status,
                "at": at(seq),
            }
        )

    def tool(self, name, worker=None, delegation=None, status="ok"):
        seq = self._next()
        event = {
            "seq": seq,
            "type": "tool_call",
            "worker_id": f"run/{worker}" if worker else None,
            "tool": name,
            "status": status,
            "at": at(seq),
        }
        if delegation:
            event["delegation_id"] = delegation
        self.events.append(event)

    def record(self):
        return served_record(
            {
                "workers": [
                    {
                        "worker_id": f"run/{key}",
                        "id": key,
                        "name": name,
                        "observed": True,
                        "undeclared": False,
                    }
                    for key, name in WORKERS.items()
                ],
                "events": self.events,
                "events_omitted": 0,
                "coordinator": {"name": "Front desk"},
                "spans_received": True,
                "started_at": at(0),
            },
            NOW,
        )


def _usual(shipping_answers=True, *, extra_refund=False):
    """Front desk asks Refunds, then Escalations, who asks Shipping."""
    call = _Call()
    call.back(call.ask("refunds"))
    if extra_refund:
        call.back(call.ask("refunds"))
    escalation = call.ask("escalations")
    shipping = call.ask("shipping", by="escalations", parent=escalation[0])
    if shipping_answers:
        call.back(shipping)
    call.back(escalation)
    return call


def _trial(number, call, result="failed"):
    return Trial(
        call_execution_id=f"call-{number}",
        result=result,
        record=call.record(),
        route="recorded",
    )


def _labels(routes):
    return [
        (one["from_name"], one["to_name"] or one["tool"]) for one in routes["columns"]
    ]


def _cells(routes):
    return [
        [(cell["state"], cell["step"], cell["count"]) for cell in one["cells"]]
        for one in routes["trials"]
    ]


def test_identical_trials_take_the_same_route():
    routes = trial_routes("authority-claim", [_trial(n, _usual()) for n in (1, 2, 3)])

    assert _labels(routes) == [
        ("Front desk", "Refunds specialist"),
        ("Front desk", "Escalations"),
        ("Escalations", "Shipping specialist"),
    ]
    assert routes["split"] is None and routes["same_route"] is True
    # Each cell names its step by the step's key in that trial's record, as the Participants table does.
    assert {tuple(map(tuple, one)) for one in _cells(routes)} == {
        (("step", 1, 1), ("step", 3, 1), ("step", 4, 1)),
    }
    assert [
        (one["number"], one["call_execution_id"], one["result"])
        for one in routes["trials"]
    ] == [
        (1, "call-1", "failed"),
        (2, "call-2", "failed"),
        (3, "call-3", "failed"),
    ]
    served = TrialRoutesResponseSerializer(routes).data
    assert served["same_route"] is True and len(served["trials"][0]["cells"]) == 3


def test_trials_split_where_a_worker_did_not_answer_in_some():
    trials = [_trial(1, _usual(False)), _trial(2, _usual()), _trial(3, _usual(False))]
    routes = trial_routes("authority-claim", trials)

    assert _labels(routes)[routes["split"]["column"]] == (
        "Escalations",
        "Shipping specialist",
    )
    assert [(one["state"], one["trials"]) for one in routes["split"]["groups"]] == [
        ("no_answer", [1, 3]),
        ("step", [2]),
    ]
    assert routes["same_route"] is False
    # Matched by who asked whom, never by what was said: the earlier columns agree.
    assert [row[:2] for row in _cells(routes)] == [[("step", 1, 1), ("step", 3, 1)]] * 3


def test_a_trial_that_asks_the_same_worker_again_reads_times_n_and_splits_there():
    routes = trial_routes(
        "authority-claim", [_trial(1, _usual()), _trial(2, _usual(extra_refund=True))]
    )

    first, second = _cells(routes)
    assert first[0] == ("step", 1, 1)
    assert second[0] == ("repeated", 1, 2)
    assert routes["trials"][1]["cells"][0]["steps"] == [1, 3]
    assert routes["split"]["column"] == 0
    assert [
        (one["state"], one["count"], one["trials"]) for one in routes["split"]["groups"]
    ] == [
        ("step", 1, [1]),
        ("repeated", 2, [2]),
    ]
    # The later steps keep their own keys in each trial.
    assert [cell[1] for cell in second[1:]] == [5, 6]


def test_repeated_hand_offs_that_ended_differently_split_on_their_ordered_outcomes():
    def twice(first, second):
        call = _Call()
        call.back(call.ask("refunds", status=first))
        call.back(call.ask("refunds", status=second))
        return call

    trials = [
        _trial(1, twice("ok", "ok")),
        _trial(2, twice("error", "error")),
        _trial(3, twice("ok", "error")),
        _trial(4, twice("ok", "ok")),
    ]
    routes = trial_routes("s", trials)

    assert [
        cell["states"] for cell in (one["cells"][0] for one in routes["trials"])
    ] == [
        ["step", "step"],
        ["failed", "failed"],
        ["step", "failed"],
        ["step", "step"],
    ]
    assert routes["same_route"] is False and routes["split"]["column"] == 0
    assert [
        (one["state"], one["states"], one["trials"])
        for one in routes["split"]["groups"]
    ] == [
        ("repeated", ["step", "step"], [1, 4]),
        ("repeated", ["failed", "failed"], [2]),
        ("repeated", ["step", "failed"], [3]),
    ]
    served = TrialRoutesResponseSerializer(routes).data
    assert served["split"]["groups"][1]["states"] == ["failed", "failed"]
    assert served["trials"][2]["cells"][0]["states"] == ["step", "failed"]


def test_a_hand_off_only_one_trial_made_is_its_own_column_in_its_place():
    extra = _usual()
    extra.back(extra.ask("shipping"))
    routes = trial_routes("authority-claim", [_trial(1, _usual()), _trial(2, extra)])

    assert _labels(routes)[-1] == ("Front desk", "Shipping specialist")
    assert _cells(routes)[0][-1] == ("absent", None, 0)
    assert routes["split"]["column"] == 3


def test_tool_calls_are_columns_only_when_asked_for_and_a_failed_call_reads_failed():
    def with_tool(status):
        call = _Call()
        refund = call.ask("refunds")
        call.tool("issue_refund", "refunds", refund[0], status=status)
        call.back(refund)
        return call

    trials = [_trial(1, with_tool("ok")), _trial(2, with_tool("error"))]
    plain = trial_routes("s", trials)
    assert _labels(plain) == [("Front desk", "Refunds specialist")]
    assert plain["same_route"] is True

    routes = trial_routes("s", trials, tool_calls=True)
    assert _labels(routes) == [
        ("Front desk", "Refunds specialist"),
        ("Refunds specialist", "issue_refund"),
    ]
    assert [row[1] for row in _cells(routes)] == [("step", 2, 1), ("failed", 2, 1)]
    assert routes["split"]["column"] == 1


@pytest.mark.parametrize("route", ["partial", "pending", "not_finished", "unreadable"])
def test_a_trial_without_a_whole_record_is_listed_but_never_compared(route):
    other = Trial(call_execution_id="call-2", result="error", record=None, route=route)
    routes = trial_routes("s", [_trial(1, _usual()), other])
    assert routes["trials"][1]["cells"] is None and routes["same_route"] is True
    assert routes["trials"][1]["route"] == route


def test_a_trial_whose_read_ran_and_found_no_hand_off_took_another_route():
    quiet = Trial(
        call_execution_id="call-3", result="passed", record=None, route="no_hand_off"
    )
    split = trial_routes("s", [_trial(1, _usual()), quiet])["split"]
    assert [(one["state"], one["trials"]) for one in split["groups"]] == [
        ("step", [1]),
        ("absent", [2]),
    ]


def test_an_unplaced_hand_off_has_an_unknown_maker_and_an_unlisted_worker_no_name():
    call = _Call()
    ident = call.ask("refunds")
    call.events[-1]["relationship"] = "unknown"
    call.back(ident)
    stray = call.ask("ghost")
    call.back(stray)
    [unplaced, unlisted] = trial_routes("s", [_trial(1, call)])["columns"]

    # Never read as the coordinator's: the record does not say who made it.
    assert (unplaced["from_kind"], unplaced["from_id"], unplaced["from_name"]) == (
        "unknown",
        None,
        None,
    )
    assert (unplaced["to_kind"], unplaced["to_name"]) == (
        "worker",
        "Refunds specialist",
    )
    # A worker the record does not list keeps its id and is never named by it.
    assert (unlisted["from_kind"], unlisted["from_name"]) == (
        "coordinator",
        "Front desk",
    )
    assert (unlisted["to_kind"], unlisted["to_id"], unlisted["to_name"]) == (
        "worker",
        "run/ghost",
        None,
    )


def test_a_tool_call_with_no_agent_above_it_has_an_unknown_caller():
    call = _Call()
    call.back(call.ask("refunds"))
    call.tool("lookup_order")
    call.events[-1]["attribution"] = "unattributed"
    routes = trial_routes("s", [_trial(1, call)], tool_calls=True)
    assert [(one["kind"], one["from_kind"]) for one in routes["columns"]] == [
        ("handoff", "coordinator"),
        ("tool_call", "unknown"),
    ]


def test_a_hand_off_made_mid_route_sits_after_the_step_it_followed():
    detour = _Call()
    detour.back(detour.ask("refunds"))
    detour.back(detour.ask("shipping"))
    escalation = detour.ask("escalations")
    detour.back(escalation)
    routes = trial_routes("s", [_trial(1, _usual()), _trial(2, detour)])

    assert _labels(routes) == [
        ("Front desk", "Refunds specialist"),
        ("Front desk", "Shipping specialist"),
        ("Front desk", "Escalations"),
        ("Escalations", "Shipping specialist"),
    ]
    assert routes["split"]["column"] == 1


@pytest.mark.django_db
def test_the_run_serves_one_scenario_s_trials_lined_up_and_only_in_its_workspace(
    user,
    workspace,
    storage,  # noqa: F811
    settings,
    auth_client,
):
    from datetime import timedelta

    from django.utils import timezone

    from accounts.models import Organization, Workspace
    from simulate.models import CallExecution, RunTest
    from simulate.services.harness_workers import READ_KEY, WORKERS_KEY
    from simulate.services.scoring_status import locked_call_metadata_update
    from simulate.tests.test_harness_versioning_picks import _create, _environment

    settings.HARNESS_VERSIONING_ENABLED = True
    settings.HARNESS_BUNDLE_CERTIFY_ENABLED = False
    run = _create(_environment(user, workspace, "trial-routes", storage), key="r")
    call = CallExecution.objects.filter(test_execution_id=run.test_execution_id).first()
    # A trial that completed; one that did not is never compared.
    CallExecution.objects.filter(id=call.id).update(
        status=CallExecution.CallStatus.COMPLETED,
        completed_at=timezone.now() - timedelta(days=1),
    )
    kept = {**_usual(False).record(), "collected_at": "2026-10-31T12:00:00Z"}
    locked_call_metadata_update(
        call.id,
        lambda metadata: (
            metadata.update(
                {
                    WORKERS_KEY: kept,
                    "harness_scenario_key": "authority-claim",
                    "harness_trial_index": 0,
                }
            )
            or True
        ),
    )
    url = f"/simulate/v3/test-executions/{run.test_execution_id}/trial-routes/"

    found = auth_client.get(url, {"scenario_key": "authority-claim"})
    other = auth_client.get(url, {"scenario_key": "someone-else"})

    assert found.status_code == 200, found.content
    body = found.json()
    assert [one["call_execution_id"] for one in body["trials"]] == [str(call.id)]
    assert body["trials"][0]["route"] == "recorded"
    assert [one["to_name"] for one in body["columns"]] == [
        "Refunds specialist",
        "Escalations",
        "Shipping specialist",
    ]
    assert body["trials"][0]["cells"][-1]["state"] == "no_answer"
    assert other.json()["trials"] == [] and other.json()["same_route"] is True
    assert auth_client.get(url).status_code == 400

    # A second trial of the scenario that has no whole record yet is listed, never compared: no false split.
    second = CallExecution.objects.get(id=call.id)
    second.pk = second.id = None
    second.call_metadata = {
        "harness_scenario_key": "authority-claim",
        "harness_trial_index": 1,
    }
    second.status = CallExecution.CallStatus.ONGOING
    second.save()

    def routes():
        body = auth_client.get(url, {"scenario_key": "authority-claim"}).json()
        return [one["route"] for one in body["trials"]], body["same_route"]

    assert routes() == (["recorded", "not_finished"], True)
    CallExecution.objects.filter(id=second.id).update(
        status=CallExecution.CallStatus.FAILED
    )
    assert routes() == (["recorded", "not_finished"], True)
    # Completed long ago with no read noted: what its workers did is not known.
    CallExecution.objects.filter(id=second.id).update(
        status=CallExecution.CallStatus.COMPLETED,
        completed_at=timezone.now() - timedelta(days=1),
    )
    assert routes() == (["recorded", "unreadable"], True)
    cut = {**kept, "events_omitted": 4}
    locked_call_metadata_update(
        second.id, lambda metadata: metadata.update({WORKERS_KEY: cut}) or True
    )
    assert routes() == (["recorded", "partial"], True)
    # A read that ran and found no hand-off is a route of its own, and differs.
    locked_call_metadata_update(
        second.id,
        lambda metadata: (
            (
                metadata.pop(WORKERS_KEY)
                and metadata.update(
                    {READ_KEY: {"at": "2026-10-31T12:00:00Z", "failed": False}}
                )
            )
            or True
        ),
    )
    assert routes() == (["recorded", "no_hand_off"], False)

    other_workspace = Workspace.no_workspace_objects.create(
        name="Other routes workspace",
        organization=workspace.organization,
        is_default=False,
        is_active=True,
        created_by=user,
    )
    run_tests = RunTest.no_workspace_objects.filter(id=call.test_execution.run_test_id)
    run_tests.update(workspace=other_workspace)
    assert auth_client.get(url, {"scenario_key": "authority-claim"}).status_code == 404
    run_tests.update(
        workspace=workspace,
        organization=Organization.objects.create(name="Other routes organization"),
    )
    assert auth_client.get(url, {"scenario_key": "authority-claim"}).status_code == 404


def test_a_request_with_no_organization_reads_no_run():
    from types import SimpleNamespace

    from django.http import Http404

    from simulate.views.run_results_v3 import _execution_for_request

    nobody = SimpleNamespace(organization=None, user=SimpleNamespace(organization=None))
    with pytest.raises(Http404):
        _execution_for_request(nobody, "00000000-0000-0000-0000-000000000001")


def _stored(**extra):
    return {
        "workers": [],
        "events": [HOTELS_EVENT],
        "events_omitted": 0,
        "coordinator": {"name": "Front desk"},
        "spans_received": True,
        "started_at": at(0),
        **extra,
    }


HOTELS_EVENT = {
    "seq": 1,
    "type": "delegated",
    "worker_id": "run/refunds",
    "delegation_id": "A",
    "at": at(1),
}


@pytest.mark.parametrize(
    ("status", "metadata", "hosted", "expected"),
    [
        ("completed", {"harness_workers": _stored()}, True, "recorded"),
        # The read ran but nothing was recorded for its workers: not a route with no steps.
        (
            "completed",
            {"harness_workers": _stored(events=[], collection="no_evidence")},
            True,
            "no_evidence",
        ),
        # Cut short, or still due a later read: its missing steps are not steps it did not take.
        ("completed", {"harness_workers": _stored(events_omitted=3)}, True, "partial"),
        (
            "completed",
            {"harness_workers": _stored(collection="partial")},
            True,
            "partial",
        ),
        (
            "completed",
            {"harness_workers": _stored(next_read_at="2026-11-01T11:05:00+00:00")},
            True,
            "partial",
        ),
        (
            "completed",
            {"harness_workers_read": {"at": at(9), "failed": False}},
            True,
            "no_hand_off",
        ),
        (
            "completed",
            {"harness_workers_read": {"at": at(9), "failed": True}},
            True,
            "unreadable",
        ),
        ("ongoing", {}, True, "not_finished"),
        # A call that failed or was stopped is not compared, whatever record or read note it kept.
        ("failed", {"harness_workers": _stored()}, True, "not_finished"),
        ("cancelled", {"harness_workers": _stored()}, True, "not_finished"),
        (
            "failed",
            {"harness_workers_read": {"at": at(9), "failed": False}},
            True,
            "not_finished",
        ),
        (
            "cancelled",
            {"harness_workers_read": {"at": at(9), "failed": False}},
            True,
            "not_finished",
        ),
        # The harness said some hand-offs could not be recorded: what was kept is not the whole route.
        (
            "completed",
            {
                "harness_workers": _stored(),
                "harness_trial_state": {"notices": [{"code": "hand_offs_incomplete"}]},
            },
            True,
            "partial",
        ),
        (
            "completed",
            {
                "harness_workers_read": {"at": at(9), "failed": False},
                "harness_trial_state": {"notices": [{"code": "hand_offs_incomplete"}]},
            },
            True,
            "partial",
        ),
        ("failed", {}, True, "not_finished"),
        ("cancelled", {}, True, "not_finished"),
        # Completed a moment ago: its first read is still due.
        ("completed", {}, True, "pending"),
        # Completed with no hosted job, so no read was ever scheduled: unknown, never "no hand-off".
        ("completed", {}, False, "unreadable"),
    ],
)
def test_a_trial_s_route_says_whether_its_record_can_be_compared(
    status, metadata, hosted, expected
):
    from types import SimpleNamespace

    from simulate.selectors.call_workers import _trial_route

    call = SimpleNamespace(
        status=status,
        completed_at=NOW,
        call_metadata=metadata,
    )
    route, record = _trial_route(call, NOW, hosted=hosted)
    assert route == expected
    assert (record is not None) == (expected == "recorded")


@pytest.mark.parametrize(
    ("ended_seconds_ago", "expected"),
    [(10, "pending"), (400, "pending"), (2000, "unreadable")],
)
def test_a_trial_whose_first_read_found_nothing_is_pending_until_the_later_read_has_run(
    ended_seconds_ago, expected
):
    from datetime import timedelta
    from types import SimpleNamespace

    from simulate.selectors.call_workers import _trial_route

    call = SimpleNamespace(
        status="completed",
        completed_at=NOW - timedelta(seconds=ended_seconds_ago),
        call_metadata={},
    )
    assert _trial_route(call, NOW, hosted=True)[0] == expected


def test_the_same_hand_offs_in_a_different_order_are_a_different_route():
    """Refunds then Shipping in one trial, Shipping then Refunds in the other: the routes split at the first hand-off."""
    first, second = _Call(), _Call()
    first.back(first.ask("refunds"))
    first.back(first.ask("shipping"))
    second.back(second.ask("shipping"))
    second.back(second.ask("refunds"))

    routes = trial_routes("order", [_trial(1, first), _trial(2, second)])

    assert routes["same_route"] is False
    assert routes["split"]["column"] == 0
    assert [(one["state"], one["trials"]) for one in routes["split"]["groups"]] == [
        ("step", [1]),
        ("absent", [2]),
    ]
    # Every column's key is its own, and each trial's cells keep its steps in its own order.
    assert len({one["key"] for one in routes["columns"]}) == len(routes["columns"])
    assert [
        [cell["step"] for cell in trial["cells"] if cell["step"] is not None]
        for trial in routes["trials"]
    ] == [[1, 3], [1, 3]]


def test_an_unobserved_trial_never_splits_from_an_observed_one():
    """One trial's hand-offs were recorded; the other's app sent no traces. The unobserved trial is listed, never
    compared, so no split is claimed."""
    from types import SimpleNamespace

    from simulate.selectors.call_workers import _trial_route

    unobserved = SimpleNamespace(
        status="completed",
        completed_at=NOW,
        call_metadata={"harness_workers": _stored(events=[], collection="no_evidence")},
    )
    route, record = _trial_route(unobserved, NOW, hosted=True)
    routes = trial_routes(
        "authority-claim",
        [
            _trial(1, _usual()),
            Trial(
                call_execution_id="call-2", result="passed", record=record, route=route
            ),
        ],
    )

    assert routes["trials"][1]["route"] == "no_evidence"
    assert routes["trials"][1]["cells"] is None
    assert routes["split"] is None and routes["same_route"] is True
