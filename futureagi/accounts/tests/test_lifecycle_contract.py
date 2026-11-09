"""READINESS lane 3.22: the FA -> EE lifecycle wire format, the rules that turn a captured
workspace or organization deletion into an event, and how EE's answers are read.

``accounts/lifecycle/contract/omega-host-lifecycle.v1.json`` holds the same bytes EE's
``lifecycle-contract.test.ts`` verifies: FA's signer reproduces every valid body here, and EE
parses exactly those and answers each state exactly as the ``answers`` say.
"""

import json
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from accounts.host_events.interpret import collapse, interpret
from accounts.host_events.relay import _batches
from accounts.host_events.signing import encode_body, signature_header
from accounts.host_events.transport import send_batch
from accounts.lifecycle.interpret import (
    EVENT_ORG_DELETED,
    EVENT_WORKSPACE_DELETED,
    interpret_lifecycle,
    split_lifecycle,
)
from accounts.lifecycle.receipts import ReceiptInvalid, validate
from accounts.tests.host_events_support import ReadyConfig

CONTRACT = (
    Path(__file__).resolve().parents[1]
    / "lifecycle"
    / "contract"
    / "omega-host-lifecycle.v1.json"
)
ORG, WS = uuid.uuid4(), uuid.uuid4()


def _contract():
    return json.loads(CONTRACT.read_text())


def _row(source, old, new, *, org=ORG, ws=WS):
    return SimpleNamespace(
        id=1,
        source=source,
        user_id=uuid.uuid4(),
        organization_id=org,
        workspace_id=ws if source == "workspace" else None,
        old_state=old,
        new_state=new,
    )


LIVE = {"is_active": True, "deleted": False}


class TestContract:
    def test_fa_reproduces_every_body_and_signature_byte_for_byte(self):
        doc = _contract()
        secret = doc["keys"][0]["secret"].encode()
        for vector in doc["vectors"]:
            parts = dict(item.split("=", 1) for item in vector["header"].split(","))
            body = vector["body"].encode()
            assert (
                signature_header(secret, parts["kid"], body, now=int(parts["t"]))
                == vector["header"]
            )
            document = json.loads(body)
            assert encode_body(document["issuer"], document["events"]) == body
        valid = {v["name"] for v in doc["vectors"] if v["expect"] == "ok"}
        assert valid == {EVENT_WORKSPACE_DELETED, EVENT_ORG_DELETED}

    def test_every_valid_body_is_what_fa_interprets_from_a_captured_row(self):
        doc = _contract()
        for vector in doc["vectors"]:
            if vector["expect"] != "ok":
                continue
            sent = json.loads(vector["body"])["events"][0]
            row = _row(
                (
                    "workspace"
                    if sent["type"] == EVENT_WORKSPACE_DELETED
                    else "organization"
                ),
                LIVE,
                {"is_active": False, "deleted": False},
                org=sent["organizationId"],
                ws=sent["workspaceId"],
            )
            event = interpret_lifecycle(row)
            assert event == {key: sent[key] for key in event}
            assert set(sent) == set(event) | {"id", "occurredAtMs"}

    @pytest.mark.parametrize(
        "name,outcome,delivered,error",
        [
            ("pending", None, False, "ee_purge_pending"),
            ("applied-with-receipt", "applied", True, ""),
            ("ignored", "ignored", True, ""),
        ],
    )
    def test_transport_reads_each_contract_answer(
        self, name, outcome, delivered, error
    ):
        answer = next(a for a in _contract()["answers"] if a["name"] == name)
        event_id = answer["answer"]["results"][0]["id"]

        class Session:
            def post(self, *args, **kwargs):
                return SimpleNamespace(
                    status_code=200,
                    content=json.dumps(answer["answer"]).encode(),
                    json=lambda: answer["answer"],
                )

        result = send_batch(ReadyConfig(), b"{}", [event_id], session=Session())
        assert (event_id in result.delivered) is delivered
        assert result.error == error
        if outcome is not None:
            assert result.outcomes == {event_id: outcome}
        assert (event_id in result.receipts) is (name == "applied-with-receipt")

    def test_receipt_validation_accepts_the_contract_receipt_and_refuses_the_others(
        self,
    ):
        doc = _contract()
        body = next(v for v in doc["vectors"] if v["name"] == EVENT_WORKSPACE_DELETED)
        event = json.loads(body["body"])["events"][0]
        answers = {a["name"]: a["answer"]["results"][0] for a in doc["answers"]}
        receipt = answers["applied-with-receipt"]["receipt"]
        checked = validate(receipt, event)
        assert checked["stores"]["sandboxes"] == {
            "outcome": "erased",
            "erased": 3,
            "retired": 0,
            "retained": [],
        }
        assert checked["stores"]["agents"]["runsCanceled"] == 1
        # Every store carries its outcome, the kept and not-host-writable ones included.
        assert checked["stores"]["deliverables"]["outcome"] == "kept"
        assert checked["stores"]["shares"]["outcome"] == "not_host_writable"
        assert list(checked["stores"]) == list(receipt["stores"])
        assert checked["audit"] == receipt["audit"]
        for name in (
            "applied-receipt-for-another-workspace",
            "applied-without-receipt",
        ):
            with pytest.raises(ReceiptInvalid):
                validate(answers[name].get("receipt"), event)
        # Unknown fields are dropped, never stored; negative or non-integer counts are refused.
        assert "extra" not in validate({**receipt, "extra": "x"}, event)
        with pytest.raises(ReceiptInvalid):
            validate({**receipt, "tenants": -1}, event)
        with pytest.raises(ReceiptInvalid):
            validate({**receipt, "stores": {"Sandboxes!": {"erased": 1}}}, event)
        with pytest.raises(ReceiptInvalid):
            validate(
                {**receipt, "stores": {"agents": {"erased": 0, "outcome": "gone"}}},
                event,
            )
        # A receipt from before the inventory (no outcome) is still accepted.
        assert validate({**receipt, "stores": {"agents": {"erased": 1}}}, event)[
            "stores"
        ] == {"agents": {"erased": 1, "retired": 0, "retained": []}}
        # EE's whole inventory fits: 40 stores, each with a 400-character line.
        many = {
            f"store-{index}": {
                "outcome": "kept",
                "erased": 0,
                "retired": 0,
                "retained": ["x" * 400],
            }
            for index in range(40)
        }
        assert len(validate({**receipt, "stores": many}, event)["stores"]) == 40


class TestRules:
    def test_a_workspace_that_stops_being_live_is_deleted_and_nothing_else_is(self):
        gone = {"is_active": False, "deleted": False}
        flagged = {"is_active": True, "deleted": True}
        assert (
            interpret_lifecycle(_row("workspace", LIVE, gone))["type"]
            == EVENT_WORKSPACE_DELETED
        )
        assert interpret_lifecycle(_row("workspace", LIVE, flagged))[
            "workspaceId"
        ] == str(WS)
        assert interpret_lifecycle(_row("workspace", LIVE, None))[
            "organizationId"
        ] == str(ORG)
        # Already gone, renamed, re-activated: nothing to tell EE.
        assert interpret_lifecycle(_row("workspace", gone, None)) is None
        assert interpret_lifecycle(_row("workspace", LIVE, LIVE)) is None
        assert interpret_lifecycle(_row("workspace", gone, LIVE)) is None
        org = interpret_lifecycle(_row("organization", {"id": str(ORG)}, None))
        assert org == {
            "type": EVENT_ORG_DELETED,
            "subject": None,
            "organizationId": str(ORG),
            "workspaceId": None,
            "role": None,
        }
        # The relay's interpret routes the lifecycle sources here (no lookups needed).
        assert interpret(_row("organization", {}, None), lookups=None) == org

    def test_an_org_deletion_covers_its_workspaces_in_the_same_batch_only(self):
        other_org = uuid.uuid4()
        events = [
            (1, interpret_lifecycle(_row("workspace", LIVE, None))),
            (2, interpret_lifecycle(_row("workspace", LIVE, None, org=other_org))),
            (3, interpret_lifecycle(_row("organization", {}, None))),
        ]
        kept = dict(collapse(events))
        assert kept[1] is None
        assert kept[2]["organizationId"] == str(other_org)
        assert kept[3]["type"] == EVENT_ORG_DELETED

    def test_lifecycle_events_never_share_a_body_with_revocations(self):
        def row(source, kind, index):
            return SimpleNamespace(
                source=source, event={"events": [{"id": f"e{index}", "type": kind}]}
            )

        rows = [
            row("organization_membership", "membership.removed", 1),
            row("workspace", EVENT_WORKSPACE_DELETED, 2),
            row("user", "user.deactivated", 3),
            row("organization", EVENT_ORG_DELETED, 4),
        ]
        revocations, lifecycle = split_lifecycle(rows)
        assert [r.event["events"][0]["id"] for r in revocations] == ["e1", "e3"]
        bodies = [
            [event["id"] for event in batch] for batch in _batches(rows, "https://i")
        ]
        assert bodies == [["e1", "e3"], ["e2", "e4"]]
