"""BL-LEDGER phase 2a: the usage-event-v1 ingest (usage-event-v1 sections 4, 5, 10)."""

import json
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from rest_framework.test import APIClient

from accounts.models.organization import Organization
from ee.usage.models.billing_ledger import UsageEventRecord, UsageEventRefusal
from ee.usage.models.usage import UsageEventLog
from ee.usage.services import usage_ingest
from ee.usage.tests.ledger_support import CATALOG_DIR, v1_event, with_catalog

pytestmark = pytest.mark.integration
NOW = datetime(2026, 11, 30, 12, 0, tzinfo=UTC)
URL = "/usage/ledger/v1/events/"


@pytest.fixture(autouse=True)
def catalog():
    with with_catalog():
        yield


def _eval(org, name="e1", **kw):
    args = dict(
        dimension="ai_credits", event_type="error_localizer", product="evals", unit="credits",
        quantity="5", occurred_at="2026-11-30T11:00:00Z",
    )
    args.update(kw)
    return v1_event(org.pk, f"fa:usage:v1:evals:{org.pk}:error_localizer:x:{name}:0", **args)


def _ee(org, ws, name="t_1", **kw):
    args = dict(
        dimension="agent_runs", event_type="task_run", product="agents", unit="runs",
        quantity="1", occurred_at="2026-11-30T11:00:00Z", system="ee_daemon",
        ref=f"task:{name}", workspace_id=ws, agent_id="agent_a", agent_run_id="run_a",
    )
    args.update(kw)
    return v1_event(org.pk, f"omega:usage:v1:{org.pk}:{ws}:task:{name}:run", **args)


def _ingest(items, **kw):
    kw.setdefault("in_process_system", "fa_platform")
    kw.setdefault("received_at", NOW)
    return usage_ingest.ingest_events(items, **kw)


def test_accept_then_duplicate_then_conflict(organization):
    event = _eval(organization)
    (first,) = _ingest([event])
    (again,) = _ingest([dict(event)])
    changed = {**event, "quantity": "6"}
    (conflict,) = _ingest([changed])

    assert (first.status, again.status) == ("accepted", "duplicate")
    assert (conflict.status, conflict.reason) == ("refused", "idempotency_conflict")
    log = UsageEventLog.objects.get(event_id=event["event_id"])
    assert log.amount_raw == Decimal("5")  # never overwritten
    record = UsageEventRecord.objects.get(event=log)
    assert record.payload_sha256 == usage_ingest.payload_sha256(event)
    assert (record.product, record.source_system, record.schema_version) == ("evals", "fa_platform", "1")
    assert UsageEventRefusal.objects.filter(reason="idempotency_conflict").count() == 1


def test_no_per_call_multiplication_and_utc_period(organization):
    event = _eval(organization, quantity="5", occurred_at="2026-11-30T23:59:59Z")
    (result,) = _ingest([event], received_at=NOW + timedelta(hours=12))
    log = UsageEventLog.objects.get(event_id=event["event_id"])
    assert result.status == "accepted"
    assert (log.amount_raw, log.period) == (Decimal("5"), "2026-11")
    (offset,) = _ingest([_eval(organization, "e2", occurred_at="2026-11-30T23:59:59+01:00")])
    assert (offset.status, offset.reason) == ("refused", "malformed_event")


def test_random_event_id_refused(organization):
    event = {**_eval(organization), "event_id": str(uuid.uuid4())}
    (result,) = _ingest([event])
    assert (result.status, result.reason) == ("refused", "event_id_not_derived")


@pytest.mark.parametrize(
    "change, reason",
    [
        ({"dimension": "no_such_dim"}, "unknown_dimension"),
        ({"event_type": "no_such_type"}, "unknown_event_type"),
        ({"event_type": "observe_add"}, "event_type_dimension_mismatch"),
        ({"product": "platform"}, "product_mismatch"),
        ({"unit": "tokens"}, "unit_mismatch"),
        ({"occurred_at": "2026-11-30T12:06:00Z"}, "occurred_at_in_future"),
        ({"occurred_at": "2026-10-25T11:59:59Z"}, "beyond_lateness_window"),
        ({"quantity": "0"}, "malformed_event"),
        ({"org_id": "00000000-0000-0000-0000-000000000000"}, "unknown_org"),
        (
            {"source": {"system": "ee_daemon", "ref": "x"}, "workspace_id": str(uuid.uuid4())},
            "source_not_permitted",
        ),
    ],
)
def test_refusals_are_rows(organization, change, reason):
    event = {**_eval(organization), **change}
    (result,) = _ingest([event])
    assert (result.status, result.reason) == ("refused", reason)
    refusal = UsageEventRefusal.objects.get(reason=reason)
    assert refusal.item_index == 0 and refusal.payload is not None
    assert not UsageEventLog.objects.filter(event_id=event["event_id"]).exists()


def test_workspace_not_in_org(organization, user):
    from accounts.models.workspace import Workspace

    other = Organization.objects.create(name="Other")
    ws = Workspace.no_workspace_objects.create(name="o", organization=other, created_by=user)
    (result,) = _ingest([_eval(organization, workspace_id=ws.pk)])
    assert result.reason == "workspace_not_in_org"


def test_managed_spend_must_be_prepaid_guard_d(organization, tmp_path):
    catalog = json.loads((CATALOG_DIR / "price-catalog.v0.json").read_text())
    for row in catalog["dimensions"]:
        if row["id"] == "managed_spend":
            row["settlement_class"] = {"value": "invoice"}
    (tmp_path / "price-catalog.v0.json").write_text(json.dumps(catalog))
    with with_catalog(tmp_path):
        from ee.usage.services import rating

        loaded = rating.catalogs()[0]
        assert "gateway_managed_spend" not in loaded.event_types  # dropped at load
        event = v1_event(
            organization.pk, "agentcc:spend:req-x", dimension="managed_spend",
            event_type="gateway_managed_spend", product="gateway", unit="usd_micros",
            quantity="10", occurred_at="2026-11-30T11:00:00Z", system="fa_gateway_settlement",
            ref="req-x", attributes={"request_id": "req-x"},
        )
        (result,) = usage_ingest.ingest_events(
            [event], in_process_system="fa_gateway_settlement", received_at=NOW
        )
    assert (result.status, result.reason) == ("refused", "unknown_event_type")


def test_batch_items_fail_alone(organization):
    good, bad = _eval(organization, "ok"), {"not": "an event"}
    results = _ingest([good, bad])
    assert [r.status for r in results] == ["accepted", "refused"]
    assert results[1].reason == "malformed_event" and results[1].event_id is None


class TestHttpRoute:
    def _credential(self, org, ws=None, systems=("ee_daemon",), products=("agents",)):
        bindings = [(org, ws)]
        return usage_ingest.create_service_credential(
            "ee-test", systems=list(systems), products=list(products), bindings=bindings
        )

    def test_service_credential_only(self, organization, auth_client):
        event = _eval(organization)
        assert auth_client.post(URL, {"events": [event]}, format="json").status_code == 401
        client = APIClient()
        assert client.post(URL, {"events": [event]}, format="json").status_code == 401
        client.credentials(HTTP_AUTHORIZATION="Bearer fa_ledger_wrong")
        assert client.post(URL, {"events": [event]}, format="json").status_code == 401

    def test_bound_credential_and_org_not_permitted(self, organization, workspace):
        credential, token = self._credential(organization, workspace.pk)
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        recent = (datetime.now(UTC) - timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        mine = _ee(organization, workspace.pk, occurred_at=recent)
        other = Organization.objects.create(name="Not bound")
        theirs = _ee(other, workspace.pk, "t_2", occurred_at=recent)
        missing = {**_ee(organization, workspace.pk, "t_3", occurred_at=recent),
                   "org_id": "00000000-0000-0000-0000-000000000001"}
        wrong_ws = _ee(organization, uuid.uuid4(), "t_4", occurred_at=recent)
        response = client.post(URL, {"events": [mine, theirs, missing, wrong_ws]}, format="json")

        assert response.status_code == 200
        results = response.json()["results"]
        assert [r["status"] for r in results] == ["accepted", "refused", "refused", "refused"]
        assert [r.get("reason") for r in results[1:]] == ["org_not_permitted"] * 3
        assert UsageEventRefusal.objects.filter(credential=credential).count() == 3

    def test_whole_request_errors(self, organization, workspace):
        credential, token = self._credential(organization, workspace.pk)
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        assert client.post(URL, {"items": []}, format="json").status_code == 400
        assert client.post(URL, {"events": [{}] * 501}, format="json").status_code == 413
        credential.enabled = False
        credential.save()
        assert client.post(URL, {"events": [{}]}, format="json").status_code == 403

    def test_token_is_never_stored(self, organization, workspace):
        credential, token = self._credential(organization, workspace.pk)
        assert token.startswith("fa_ledger_")
        assert credential.token_sha256 == usage_ingest.token_sha256(token) != token
