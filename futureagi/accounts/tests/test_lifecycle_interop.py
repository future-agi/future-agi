"""READINESS lane 3.22 (E02) across both systems: Future AGI's real workspace DELETE endpoint,
the Postgres capture trigger, the outbox and the relay over signed HTTP, against a real Omega EE
server (just-in-time host identity, the host event receiver, the lifecycle purge over the real
conversation store and accounts plane).

Deleting tenant A's side workspace: EE answers ``pending``, purges, and answers with its receipt;
FA records it in ``audit_log`` and ``OmegaPurgeReceipt``; from then on the side workspace's own EE
reads are refused, while tenant A's main workspace and tenant B still read. Then deleting tenant B
(the organization) purges B the same way.

Opt-in, because it needs the EE checkout: set ``OMEGA_EE_SERVER_DIR`` to
``<omega-ee>/packages/server`` (with dependencies installed) and put Node 22 on PATH.
``OMEGA_LIFECYCLE_RECEIPT`` names a JSON file the timings are written to.
"""

import json
import os
import queue
import subprocess
import threading
import time
from pathlib import Path

import pytest
from rest_framework import status

from accounts.host_events import relay
from accounts.host_events.config import READY, EventChannelConfig
from accounts.lifecycle.receipts import ACTION_WORKSPACE_PURGED
from accounts.models.audit_log import AuditLog
from accounts.models.host_event_outbox import HostEventOutbox
from accounts.models.omega_purge_receipt import OmegaPurgeReceipt
from accounts.models.organization import Organization
from accounts.tests.host_events_support import make_tenants

EE_DIR = os.environ.get("OMEGA_EE_SERVER_DIR", "")

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.skipif(
        not EE_DIR, reason="needs OMEGA_EE_SERVER_DIR (the Omega EE server package)"
    ),
]


@pytest.fixture
def tenants(user, organization, workspace):
    return make_tenants(user, organization, workspace)


class Harness:
    def __init__(self, tenants):
        env = {
            "PATH": os.environ["PATH"],
            "HOME": os.environ.get("HOME", "/tmp"),
            "HARNESS_TENANTS": json.dumps(tenants),
        }
        self.process = subprocess.Popen(
            [
                str(Path(EE_DIR) / "node_modules" / ".bin" / "tsx"),
                "test-support/host-lifecycle-interop.harness.ts",
            ],
            cwd=EE_DIR,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        self.lines = queue.Queue()
        self.reads = {}
        threading.Thread(target=self._read, daemon=True).start()
        self.ready = self.wait(lambda line: line.get("ready"), timeout=120)

    def _read(self):
        for raw in self.process.stdout:
            try:
                self.lines.put(json.loads(raw))
            except ValueError:
                continue

    def wait(self, predicate, timeout):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                line = self.lines.get(timeout=max(0.05, deadline - time.monotonic()))
            except queue.Empty:
                break
            if line.get("read"):
                self.reads[line["label"]] = line
            if predicate(line):
                return line
        return None

    def config(self):
        return EventChannelConfig(
            state=READY,
            issuer=self.ready["issuer"],
            url=self.ready["eventsUrl"],
            key_id=self.ready["keyId"],
            secret=self.ready["secret"].encode(),
        )

    def stop(self):
        self.process.terminate()
        try:
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.process.kill()


def _until_receipt(harness, receipt_filter, timeout=60):
    """Drain the relay every 0.5 s (as the 3 s schedule would, faster) until EE's receipt is
    recorded. Rows are retried on their real backoff: nothing here makes a row due early.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        relay.drain(config=harness.config(), purge_old=False)
        receipt = OmegaPurgeReceipt.objects.filter(**receipt_filter).first()
        if receipt is not None:
            return receipt
        harness.wait(lambda line: False, timeout=0.5)
    return None


def test_deleting_a_workspace_and_an_org_purges_them_in_ee(auth_client, tenants):
    specs = [
        {
            "label": label,
            "sub": str(person.id),
            "org": str(org.id),
            "workspace": str(ws.id),
        }
        for label, person, org, ws in (
            ("a_side", tenants["alice"], tenants["a"], tenants["a_side"]),
            ("a_main", tenants["alice"], tenants["a"], tenants["a_main"]),
            ("b_main", tenants["bob"], tenants["b"], tenants["b_main"]),
        )
    ]
    harness = Harness(specs)
    try:
        assert harness.ready, "the EE harness did not start"
        harness.wait(lambda line: len(harness.reads) == 3, timeout=10)
        assert {label: line["status"] for label, line in harness.reads.items()} == {
            "a_side": 200,
            "a_main": 200,
            "b_main": 200,
        }
        HostEventOutbox.objects.all().delete()

        started = time.monotonic()
        response = auth_client.delete(f"/accounts/workspaces/{tenants['a_side'].id}/")
        assert response.status_code == status.HTTP_200_OK, response.json()
        receipt = _until_receipt(harness, {"workspace_id": tenants["a_side"].id})
        workspace_ms = int((time.monotonic() - started) * 1000)
        assert receipt is not None, "no receipt for the deleted workspace"
        assert receipt.outcome == "purged"
        assert receipt.receipt["tenants"] == 1
        assert receipt.receipt["stores"]["conversations"]["retired"] == 1
        assert receipt.receipt["stores"]["accounts"]["erased"] == 1
        audit = AuditLog.objects.get(action=ACTION_WORKSPACE_PURGED)
        assert (audit.organization_id, audit.target_id) == (
            tenants["a"].id,
            tenants["a_side"].id,
        )
        # From now on the deleted workspace's own reads are refused; the others still read.
        harness.wait(
            lambda line: line.get("label") == "a_side" and line.get("status") == 403,
            timeout=5,
        )
        assert harness.reads["a_side"]["status"] == 403
        assert harness.reads["a_main"]["status"] == 200
        assert harness.reads["b_main"]["status"] == 200

        started = time.monotonic()
        Organization.objects.filter(id=tenants["b"].id).delete()
        org_receipt = _until_receipt(
            harness, {"organization_id": tenants["b"].id, "kind": "org.deleted"}
        )
        org_ms = int((time.monotonic() - started) * 1000)
        assert org_receipt is not None, "no receipt for the deleted organization"
        assert org_receipt.receipt["tenants"] == 1
        harness.wait(
            lambda line: line.get("label") == "b_main" and line.get("status") == 403,
            timeout=5,
        )
        assert harness.reads["b_main"]["status"] == 403
        assert harness.reads["a_main"]["status"] == 200

        target = os.environ.get("OMEGA_LIFECYCLE_RECEIPT")
        if target:
            Path(target).write_text(
                json.dumps(
                    {
                        "workspace_delete_to_receipt_recorded_ms": workspace_ms,
                        "org_delete_to_receipt_recorded_ms": org_ms,
                        "workspace_receipt": receipt.receipt,
                        "org_receipt": org_receipt.receipt,
                        "reads_after": {
                            k: v["status"] for k, v in harness.reads.items()
                        },
                    },
                    indent=1,
                )
            )
    finally:
        harness.stop()
