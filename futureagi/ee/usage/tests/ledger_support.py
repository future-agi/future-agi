"""Shared helpers for the BL-LEDGER phase-2a tests (no test collected here).

``FakeGateway`` answers the gateway admin calls the ledger makes with the
semantics of credits-granted-push-v1 and credits-v1: a granted push applies
only when its version is newer, ``spent = max(spent, floor)`` always applies,
and ``available = granted - spent - held``. ``settle`` and ``hold`` stand in for
the gateway's own hot path.
"""

import json
import threading
import time
import uuid
from pathlib import Path
from unittest.mock import patch

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import override_settings

from agentcc.services.gateway_client import GatewayClientError

FIXTURES = Path(__file__).resolve().parent / "fixtures"
CATALOG_DIR = FIXTURES / "ledger_catalog"
EXAMPLES_DIR = Path(__file__).resolve().parents[4] / "api_contracts" / "gateway" / "examples"
CONTRACTS_DIR = Path(__file__).resolve().parents[1] / "contracts"
GATEWAY_ADMIN = "agentcc.services.gateway_admin.credits.GatewayAdminClient"


def golden(name):
    return json.loads((EXAMPLES_DIR / name).read_text())


def spend_record(org, request_id="req_l1", billed=11_815, **overrides):
    """The managed-settlement-v1 golden, as the outbox carries it, for ``org``."""
    record = golden("managed-settlement-v1.json")
    record.update(
        org_id=str(org.pk),
        request_id=request_id,
        generation_id=request_id,
        billed_micros=str(billed),
    )
    record.update({k: str(v) for k, v in overrides.items()})
    return record


class FakeGateway:
    def __init__(self, managed_off=False, push_delay=0.0):
        self.managed_off = managed_off
        self.push_delay = push_delay
        self.accounts = {}
        self.pushes = []
        self.in_flight = {}
        self.max_in_flight = {}
        self.outbox_pending = 0
        self.lock = threading.Lock()

    def _acct(self, org_id):
        return self.accounts.setdefault(
            org_id, {"granted": 0, "version": -1, "spent": 0, "held": 0}
        )

    def settle(self, org_id, micros):
        with self.lock:
            self._acct(str(org_id))["spent"] += micros

    def hold(self, org_id, micros):
        with self.lock:
            self._acct(str(org_id))["held"] = micros

    def state(self, org_id):
        a = self._acct(str(org_id))
        return {
            "granted_total_micros": a["granted"],
            "granted_version": max(a["version"], 0),
            "spent_micros": a["spent"],
            "held_micros": a["held"],
            "available_micros": a["granted"] - a["spent"] - a["held"],
        }

    def request(self, method, path, json=None, params=None):
        parts = path.strip("/").split("/")
        org_id = parts[2]
        if self.managed_off:
            raise GatewayClientError(
                'Gateway returned 404: {"error":{"code":"managed_models_disabled"}}',
                status_code=404,
            )
        if method == "PUT":
            with self.lock:
                self.in_flight[org_id] = self.in_flight.get(org_id, 0) + 1
                self.max_in_flight[org_id] = max(
                    self.max_in_flight.get(org_id, 0), self.in_flight[org_id]
                )
            try:
                if self.push_delay:
                    time.sleep(self.push_delay)
                with self.lock:
                    self.pushes.append((org_id, dict(json)))
                    a = self._acct(org_id)
                    applied = json["version"] > a["version"]
                    if applied:
                        a["granted"], a["version"] = json["granted_total_micros"], json["version"]
                    a["spent"] = max(a["spent"], json["settled_spend_floor_micros"])
                    return {"applied": applied, **self.state(org_id)}
            finally:
                with self.lock:
                    self.in_flight[org_id] -= 1
        with self.lock:
            body = self.state(org_id)
            return {
                "org_id": org_id,
                **body,
                "balance_usd": body["available_micros"] / 1_000_000,
                "markup_percent": 5,
                "outbox_pending": self.outbox_pending,
                "ledger": "redis",
            }

    def pushes_for(self, org):
        return [p for o, p in self.pushes if o == str(org.pk)]


def patched_gateway(fake):
    """``with patched_gateway(FakeGateway()) as fake:`` routes admin calls to ``fake``."""

    class _Ctx:
        def __enter__(self_inner):
            self_inner.p = patch(GATEWAY_ADMIN)
            client_class = self_inner.p.start()
            client_class.return_value.request.side_effect = fake.request
            return fake

        def __exit__(self_inner, *exc):
            self_inner.p.stop()

    return _Ctx()


def with_catalog(directory=CATALOG_DIR, **extra):
    """Settings for the fixture catalog (no prices), plus the cache reset."""
    from ee.usage.services import rating

    rating.reset_catalog_cache()
    return override_settings(BILLING_PRICE_CATALOG_DIR=str(directory), **extra)


def migrate_usage_to_latest():
    """Bring ``usage`` back to its latest migration before a committing test.

    ``test_deployment_telemetry_migration.py`` leaves ``usage`` at 0026 for the
    rest of the session (lane E1 integrator request 1).
    """
    executor = MigrationExecutor(connection)
    leaves = executor.loader.graph.leaf_nodes("usage")
    if executor.migration_plan(leaves):
        executor.migrate(leaves)


def run_concurrently(calls, timeout=60):
    """Start every call at once, each on its own thread and DB connection."""
    barrier = threading.Barrier(len(calls))
    results, errors = [], []
    lock = threading.Lock()

    def run(call):
        try:
            barrier.wait(timeout=10)
            value = call()
            with lock:
                results.append(value)
        except Exception as exc:  # surfaced by the callers' assertions
            with lock:
                errors.append(exc)
        finally:
            connection.close()

    threads = [threading.Thread(target=run, args=(call,)) for call in calls]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=timeout)
    return results, errors


def v1_event(org_id, name, *, dimension, event_type, product, unit, quantity, occurred_at,
             system="fa_platform", ref="src-1", workspace_id=None, attributes=None,
             agent_id=None, agent_run_id=None):
    from ee.usage.services.usage_ingest import derive_event_id

    return {
        "v": "1",
        "event_id": derive_event_id(name),
        "org_id": str(org_id),
        "workspace_id": str(workspace_id) if workspace_id else None,
        "product": product,
        "dimension": dimension,
        "event_type": event_type,
        "quantity": str(quantity),
        "unit": unit,
        "occurred_at": occurred_at,
        "source": {"system": system, "ref": ref},
        "attribution": {
            "agent_id": agent_id,
            "agent_run_id": agent_run_id,
            "api_key_id": None,
            "purpose": None,
        },
        "attributes": dict(attributes or {}),
    }


def new_id():
    return uuid.uuid4().hex[:12]
