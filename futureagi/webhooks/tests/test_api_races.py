"""Interleavings the API must survive, each run for real: a second request or the dispatcher's own turn-off commits on
its own thread and database connection at the exact point where the first request used to be exposed.

- A ``PATCH`` used to load the endpoint before its transaction and save the whole row, so a
  delete or a turn-off that committed meanwhile was undone. Now it re-reads the row under the lock and writes only the
  columns it changes.
- Two rotations of one endpoint used to end in a 500 (``whk_one_active``).
- Turning an endpoint on checked the policy before the lock, so a tightening that committed in
  between left it enabled outside the allowlist.
- The endpoint limit was check-then-insert, so concurrent creates could all pass the count.
"""

from __future__ import annotations

import threading
import time

import pytest
from django.db import connection
from django.utils import timezone

from webhooks import api_endpoint_ops, api_endpoints
from webhooks import audit as audit_mod
from webhooks.models import Endpoint, Policy, Secret
from webhooks.store_settle import disable_endpoint
from webhooks.tests.api_support import EP, client_for, make

pytestmark = pytest.mark.django_db(transaction=True)


def in_thread(fn):
    """Run ``fn`` on its own thread and database connection; returns (thread, outcome dict)."""
    out: dict = {}

    def run():
        try:
            out["value"] = fn()
        except BaseException as exc:  # noqa: BLE001 - asserted by the test
            out["error"] = exc
        finally:
            connection.close()

    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t, out


def _before_patch_transaction(monkeypatch, action):
    """Run ``action`` to its commit while a PATCH is between its request checks and its transaction (the PATCH
    checks the headers there), the window M1 named."""
    real = api_endpoints.check_headers
    fired: dict = {}

    def hooked(headers):
        if not fired:
            t, out = in_thread(action)
            t.join(60)
            fired.update(out, joined=not t.is_alive())
        return real(headers)

    monkeypatch.setattr(api_endpoints, "check_headers", hooked)
    return fired


PATCH = {"description": "edited", "headers": {"X-A": "1"}}


def test_m1_a_patch_racing_a_delete_answers_404_and_the_endpoint_stays_deleted(
    env, owner, organization, workspace, user, monkeypatch
):
    ep_id, _ = make(owner, "https://hooks.acme.ai/x")
    other = client_for(user, organization, workspace)
    fired = _before_patch_transaction(
        monkeypatch, lambda: other.delete(f"{EP}{ep_id}/").status_code
    )
    r = owner.patch(f"{EP}{ep_id}/", PATCH, format="json")
    assert fired == {"value": 200, "joined": True}
    assert (r.status_code, r.json()["code"]) == (404, "WEBHOOK_NOT_FOUND")
    ep = Endpoint.objects.get(id=ep_id)
    assert ep.deleted and ep.url_encrypted is None and ep.headers_encrypted is None
    assert ep.description == "" and ep.header_names == []
    assert not Secret.objects.filter(endpoint_id=ep_id).exists()


@pytest.mark.parametrize("turn_off", ["auto_410", "manual", "policy"])
def test_m1_a_patch_never_undoes_a_turn_off_that_committed_meanwhile(
    env, owner, organization, workspace, user, monkeypatch, turn_off
):
    ep_id, _ = make(owner, "https://hooks.acme.ai/y")
    other = client_for(user, organization, workspace)
    action, expected = {
        "auto_410": (
            lambda: disable_endpoint(ep_id, "gone_410", timezone.now()),
            ("disabled_auto", "gone_410"),
        ),
        "manual": (
            lambda: other.post(f"{EP}{ep_id}/disable/", {}, format="json").status_code,
            ("disabled_manual", "manual"),
        ),
        "policy": (
            lambda: (
                other.put(
                    "/webhooks/policy/",
                    {"allowed_hosts": ["*.other.ai"]},
                    format="json",
                ).status_code
            ),
            ("blocked_by_policy", "policy"),
        ),
    }[turn_off]
    fired = _before_patch_transaction(monkeypatch, action)
    r = owner.patch(f"{EP}{ep_id}/", PATCH, format="json")
    assert fired.get("joined") and "error" not in fired, fired
    assert r.status_code == 200, r.content
    ep = Endpoint.objects.get(id=ep_id)
    assert (ep.status, ep.disabled_reason) == expected
    assert (ep.description, ep.header_names) == ("edited", ["X-A"])
    assert r.json()["result"]["status"] == expected[0]


def test_s2_two_concurrent_rotations_both_succeed(
    env, owner, organization, workspace, user, monkeypatch
):
    ep_id, first = make(owner, "https://hooks.acme.ai/z")
    other = client_for(user, organization, workspace)
    real_record = audit_mod.record
    st: dict = {}

    def hooked(**kw):
        if kw.get("action") == audit_mod.SECRET_ROTATED and "t" not in st:
            st["t"], st["out"] = in_thread(
                lambda: other.post(f"{EP}{ep_id}/rotate-secret/", {}, format="json")
            )
            time.sleep(2.0)  # rotation 2 has started and waits on this one's locks
        return real_record(**kw)

    monkeypatch.setattr(audit_mod, "record", hooked)
    r1 = owner.post(f"{EP}{ep_id}/rotate-secret/", {}, format="json")
    st["t"].join(60)
    r2 = st["out"].get("value")
    assert "error" not in st["out"], st["out"]
    assert (r1.status_code, r2.status_code) == (200, 200), (r1.content, r2.content)
    rows = dict(Secret.objects.filter(endpoint_id=ep_id).values_list("tail", "state"))
    second = r1.json()["result"]["secret"][-4:]
    third = r2.json()["result"]["secret"][-4:]
    # Serialized: rotation 2 saw rotation 1's secret, made it previous, and revoked the first at once.
    assert rows == {first[-4:]: "revoked", second: "previous", third: "active"}
    assert r2.json()["result"]["revoked_tails"] == [first[-4:]]


def test_s3_turning_on_while_a_policy_tightens_ends_blocked(
    env, owner, organization, workspace, user, monkeypatch
):
    ep_id, _ = make(owner, "https://hooks.acme.ai/p")
    assert owner.post(f"{EP}{ep_id}/disable/", {}, format="json").status_code == 200
    other = client_for(user, organization, workspace)
    real = api_endpoint_ops.check_url
    st: dict = {}

    def hooked(org_id, ws_id, url):
        d = real(org_id, ws_id, url)  # allowed under the policy as it is now
        if "t" not in st:
            st["t"], st["out"] = in_thread(
                lambda: (
                    other.put(
                        "/webhooks/policy/",
                        {"allowed_hosts": ["*.other.ai"]},
                        format="json",
                    ).status_code
                )
            )
            time.sleep(
                1.0
            )  # time enough for a tightening that does not wait for this request to commit
        return d

    monkeypatch.setattr(api_endpoint_ops, "check_url", hooked)
    r = owner.post(f"{EP}{ep_id}/enable/", {}, format="json")
    st["t"].join(60)
    assert st["out"] == {"value": 200}
    assert r.status_code == 200, r.content
    ep = Endpoint.objects.get(id=ep_id)
    # Either order ends outside "enabled": the tightening re-applies after the turn-on commits.
    assert (ep.status, ep.disabled_reason) == ("blocked_by_policy", "policy")


def test_s9_concurrent_creates_cannot_pass_the_workspace_limit_together(
    env, organization, workspace, user
):
    Policy.objects.create(organization=organization, max_endpoints_per_workspace=2)
    make(client_for(user, organization, workspace), "https://hooks.acme.ai/first")
    clients = [client_for(user, organization, workspace) for _ in range(4)]
    gate = threading.Barrier(len(clients))

    def create(c, i):
        def go():
            gate.wait(30)
            r = c.post(
                EP,
                {
                    "name": f"racer-{i}",
                    "url": "https://hooks.acme.ai/r",
                    "subscriptions": [{"event_type": "prompt.*"}],
                },
                format="json",
            )
            return r.status_code, r.json().get("code")

        return go

    runs = [in_thread(create(c, i)) for i, c in enumerate(clients)]
    for t, _ in runs:
        t.join(60)
    got = sorted(
        out.get("value") or ("error", repr(out.get("error"))) for _, out in runs
    )
    assert got == [(201, None)] + [(400, "WEBHOOK_ENDPOINT_LIMIT")] * 3, got
    assert (
        Endpoint.objects.filter(
            organization=organization, workspace=workspace, deleted=False
        ).count()
        == 2
    )
