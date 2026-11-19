"""J6 batch recovery: a plan only observes, an apply executes exactly the reviewed plan."""

import json
from uuid import uuid4

import pytest
from ctxhub_support import recover
from ctxhub_support.store import Store, canonical, digest

BINDING = "00000000-0000-4000-8000-0000000000b1"
ORDERS = {"t1": "synthetic-o1", "t2": "synthetic-o2", "t0": "synthetic-o0"}
STATUS = {"CONSUME_PAUSED": 200, "CONSUME_EXPIRED": 401, "CONSUME_LOST": 200}
ENVIRONMENT = {
    "CTXHUB_REGISTRY_URL": "http://skills-fakes:8081",
    "CTXHUB_PROVIDER_URL": "http://skills-fakes:8082/",
    "CTXHUB_BUSINESS_URL": "http://skills-fakes:8083/",
    "CTXHUB_RUNTIME_DIGEST": "sha256:" + "a" * 64,
    "CONSUME_PAUSED": "paused",
    "CONSUME_EXPIRED": "expired",
    "CONSUME_LOST": "lost",
    "REPLACEMENT": "replacement",
    "REPORT": "report",
}


class Status(Exception):
    def __init__(self, status_code):
        super().__init__(status_code)
        self.status_code = status_code


class Client:
    def __init__(self, name):
        self.name, self.calls = name, []

    def request(self, method, path, body=None, **_):
        self.calls.append({"method": method, "path": path, "body": body})
        if STATUS.get(self.name, 200) != 200:
            raise Status(STATUS[self.name])
        return {"allowed": True}

    def close(self):
        pass


def state(order, *, phase="running", checkpoint=5):
    return {
        "phase": phase,
        "checkpoint": checkpoint,
        "case": {
            "case_id": "case-" + order,
            "order_id": order,
            "request": "Refund 25 units.",
        },
        "messages": [{"role": "assistant", "tool_calls": [{"id": "refund-1"}]}],
        "session": {
            "session_id": str(uuid4()),
            "version_id": str(uuid4()),
            "version": 1,
        },
        "lock": {
            "identity": {"version_id": str(uuid4()), "digest": "sha256:" + "b" * 64}
        },
        "incarnation": str(uuid4()),
    }


@pytest.fixture
def fleet(tmp_path, monkeypatch):
    for name, value in ENVIRONMENT.items():
        monkeypatch.setenv(name, value)
    rows, clients, runs = [], {}, []
    for key, consume in (
        ("t1", "CONSUME_PAUSED"),
        ("t2", "CONSUME_EXPIRED"),
        ("t3", "CONSUME_LOST"),
        ("t0", "CONSUME_PAUSED"),
    ):
        thread = str(uuid4())
        path = tmp_path / f"{key}.sqlite"
        store = Store(path)
        if key != "t3":  # the lost mapping has no durable state at all
            body = state(
                ORDERS.get(key, "synthetic-o3"),
                phase="completed" if key == "t0" else "running",
            )
            store.save(thread, body, "checkpoint")
            store.record_receipt(
                thread, "refund-1", {"name": "issue_refund"}, {"status": "applied"}
            )
        store.close()
        rows.append(
            {
                "key": key,
                "thread": thread,
                "state": str(path),
                "binding_id": BINDING,
                "report_env": "REPORT",
                **({"consume_env": consume} if consume else {}),
                "replacement_env": "REPLACEMENT",
            }
        )
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"schema": 1, "threads": rows}))

    def client(name):
        clients.setdefault(name, []).append(Client(name))
        return clients[name][-1]

    def run(request, state_path, row, consume_env, report_env):
        runs.append(
            {
                "key": row["key"],
                "thread": request["thread"],
                "case": request["case"],
                "state": state_path,
                "consume": consume_env,
            }
        )
        return {
            "status": "completed",
            "checkpoint": 7,
            "session_id": "s",
            "version_id": "v",
            "events": [],
        }

    monkeypatch.setattr(recover, "_client", client)
    monkeypatch.setattr(recover, "_run", run)
    monkeypatch.setattr(
        recover,
        "reconcile",
        lambda entries: {
            "request_count": 3,
            "operation_count": 3,
            "orders": [e["order_id"] for e in entries],
        },
    )
    return {
        "registry": recover.load_registry(registry),
        "rows": rows,
        "clients": clients,
        "runs": runs,
        "path": registry,
    }


def planned(fleet):
    return recover.build_plan(
        fleet["registry"],
        orders={"t3": "synthetic-o3"},
        rechecks={"t0": ORDERS["t0"]},
        request="Verify the receipt; take no new action.",
        recheck_request="Refund 25 units for an order purchased 30 days ago.",
    )


def test_plan_observes_every_thread_and_executes_nothing(fleet):
    plan = planned(fleet)
    assert {entry["key"]: entry["action"] for entry in plan["entries"]} == {
        "t1": "resume",
        "t2": "close_and_reconstruct",
        "t3": "reconstruct",
        "t0": "close_and_recheck",
    }
    by_key = {entry["key"]: entry for entry in plan["entries"]}
    assert (
        by_key["t2"]["reason"] == "authority_unavailable"
        and by_key["t3"]["reason"] == "thread_state_missing"
    )
    assert (
        by_key["t1"]["pending_tool_calls"] == ["refund-1"]
        and by_key["t1"]["durable_receipts"]
    )
    assert [entry["phase"] for entry in plan["entries"]] == [
        "threads",
        "threads",
        "threads",
        "recheck",
    ]
    assert plan["plan_sha256"] == recover.plan_digest(plan)
    assert fleet["runs"] == []
    # Only access checks were made: no label resolution, session open or receipt posted.
    posted = [
        call["path"]
        for calls in fleet["clients"].values()
        for client in calls
        for call in client.calls
    ]
    assert all(path.endswith("/access-check/") for path in posted)


def test_plan_requires_the_typed_order_and_request_only_where_state_is_gone(fleet):
    with pytest.raises(ValueError, match="reconstruction_requires_order_and_request"):
        recover.build_plan(
            fleet["registry"],
            orders={},
            rechecks={"t0": "o"},
            request="Verify.",
            recheck_request="Refund.",
        )
    with pytest.raises(ValueError, match="recheck_requires_thread_order_and_request"):
        recover.build_plan(
            fleet["registry"],
            orders={"t3": "o"},
            rechecks={"t0": "o"},
            request="Verify.",
            recheck_request=None,
        )


def test_apply_refuses_what_was_not_reviewed_before_any_effect(fleet):
    plan = planned(fleet)
    with pytest.raises(ValueError, match="plan_not_reviewed"):
        recover.apply_plan(
            fleet["registry"], plan, phase="threads", reviewed="sha256:" + "0" * 64
        )
    edited = json.loads(json.dumps(plan))
    edited["entries"][2]["order_id"] = "synthetic-other"
    with pytest.raises(ValueError, match="plan_not_reviewed"):
        recover.apply_plan(
            fleet["registry"], edited, phase="threads", reviewed=plan["plan_sha256"]
        )
    changed = json.loads(json.dumps(fleet["registry"]))
    changed["threads"][0]["report_env"] = "OTHER_REPORT"
    with pytest.raises(ValueError, match="registry_changed"):
        recover.apply_plan(changed, plan, phase="threads", reviewed=plan["plan_sha256"])
    assert fleet["runs"] == []


def test_apply_refuses_a_plan_whose_thread_moved_and_applies_nothing(fleet):
    plan = planned(fleet)
    row = fleet["rows"][0]
    store = Store(row["state"])
    moved = store.load(row["thread"])
    moved["checkpoint"] += 2  # another process advanced the paused thread after review
    store.save(row["thread"], moved)
    store.close()
    with pytest.raises(ValueError, match="plan_stale"):
        recover.apply_plan(
            fleet["registry"], plan, phase="threads", reviewed=plan["plan_sha256"]
        )
    assert fleet["runs"] == []
    assert not [
        call for calls in fleet["clients"].get("REPORT", []) for call in calls.calls
    ]


def test_apply_runs_one_reviewed_phase_and_never_copies_old_threads(fleet):
    plan = planned(fleet)
    first = recover.apply_plan(
        fleet["registry"], plan, phase="threads", reviewed=plan["plan_sha256"]
    )
    assert [(row["key"], row["status"]) for row in first["threads"]] == [
        ("t1", "completed"),
        ("t2", "completed"),
        ("t3", "completed"),
    ]
    runs = {run["key"]: run for run in fleet["runs"]}
    assert set(runs) == {"t1", "t2", "t3"}  # the recheck phase did not run
    originals = {row["key"]: row["thread"] for row in fleet["rows"]}
    assert (
        runs["t1"]["thread"] == originals["t1"]
        and runs["t1"]["consume"] == "CONSUME_PAUSED"
    )
    for key in ("t2", "t3"):
        assert (
            runs[key]["thread"] != originals[key]
            and runs[key]["consume"] == "REPLACEMENT"
        )
        assert set(runs[key]["case"]) == {
            "case_id",
            "order_id",
            "request",
        }  # no messages, session or receipts
        assert str(runs[key]["state"]).endswith(f"clean-{key}.sqlite")
    # The expired-credential thread is closed (never counted as recovered); the lost one has nothing to close.
    closed = Store(fleet["rows"][1]["state"])
    assert closed.load(originals["t2"])["phase"] == "closed"
    closed.close()
    reports = [
        call for client in fleet["clients"].get("REPORT", []) for call in client.calls
    ]
    assert [call["body"]["stage"] for call in reports] == ["closed"]
    assert first["reconciliation"]["request_count"] == 3
    assert all(isinstance(row["elapsed_ms"], int) for row in first["threads"])
    second = recover.apply_plan(
        fleet["registry"], plan, phase="recheck", reviewed=plan["plan_sha256"]
    )
    assert [row["key"] for row in second["threads"]] == ["t0"] and second["threads"][0][
        "closed"
    ]["reason"] == "incident_superseded"
    assert {run["key"] for run in fleet["runs"]} == {"t1", "t2", "t3", "t0"}


def test_a_second_apply_of_a_finished_phase_is_refused_not_repeated(fleet):
    plan = planned(fleet)
    recover.apply_plan(
        fleet["registry"], plan, phase="threads", reviewed=plan["plan_sha256"]
    )
    count = len(fleet["runs"])
    with pytest.raises(ValueError, match="plan_stale"):
        recover.apply_plan(
            fleet["registry"], plan, phase="threads", reviewed=plan["plan_sha256"]
        )
    assert len(fleet["runs"]) == count


def test_the_registry_holds_no_incident_decision_the_operator_names_the_recheck(fleet):
    # The registry only describes the application's threads; which one is rechecked comes from the typed flag.
    assert all("role" not in row for row in fleet["registry"]["threads"])
    plan = recover.build_plan(
        fleet["registry"],
        orders={"t3": "synthetic-o3"},
        rechecks={},
        request="Verify the receipt; take no new action.",
        recheck_request=None,
    )
    assert {entry["key"]: entry["role"] for entry in plan["entries"]}["t0"] == "recover"
    assert plan["entries"][-1]["action"] == "none"  # a completed thread needs nothing


def test_plan_output_shows_what_the_operator_reviews_and_no_secret(
    fleet, capsys, tmp_path
):
    out = tmp_path / "plan.json"
    argv = [
        "plan",
        "--registry",
        str(fleet["path"]),
        "--output",
        str(out),
        "--request",
        "Verify the receipt; take no new action.",
        "--order",
        "t3=synthetic-o3",
        "--recheck",
        "t0=" + ORDERS["t0"],
        "--recheck-request",
        "Refund 25 units.",
    ]
    assert recover.main(argv) == 0
    printed = json.loads(capsys.readouterr().out)
    by_key = {row["key"]: row for row in printed["threads"]}
    assert by_key["t1"]["pending_tool_calls"] == ["refund-1"]
    assert by_key["t1"]["durable_receipt_calls"] == ["refund-1"]
    assert (
        by_key["t2"]["reason"] == "authority_unavailable"
        and by_key["t2"]["action"] == "close_and_reconstruct"
    )
    assert by_key["t3"]["known"] is False and by_key["t0"]["phase"] == "recheck"
    assert printed["plan_sha256"] == json.loads(out.read_text())["plan_sha256"]
    assert set(by_key["t1"]) == {
        "key",
        "action",
        "reason",
        "phase",
        "known",
        "checkpoint",
        "session_id",
        "order_id",
        "pending_tool_calls",
        "durable_receipt_calls",
    }


def test_registry_must_name_credentials_never_contain_them(tmp_path):
    path = tmp_path / "registry.json"
    base = {
        "key": "t1",
        "thread": "x",
        "state": "/s",
        "binding_id": BINDING,
        "report_env": "REPORT",
        "consume_env": "CONSUME",
    }
    for bad in (
        {**base, "consume_env": "lowercase-secret-value"},
        {**base, "secret": "x"},
        {**base, "key": "BAD"},
    ):
        path.write_text(json.dumps({"schema": 1, "threads": [bad]}))
        with pytest.raises(ValueError, match="invalid_recovery_registry"):
            recover.load_registry(path)
    assert digest({"a": 1}) == digest(json.loads(canonical({"a": 1})))
