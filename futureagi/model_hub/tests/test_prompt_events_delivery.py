"""XP-06 end to end through the WH-1a send path: a real prompt commit and label move, through the one writer, are
fanned out by the Postgres store, signed and sent by the dispatcher, and each endpoint receives its own variant
(webhooks DESIGN.md sections 4.6, 14.1-14.3).

Real Postgres and the real dispatcher; the transport is the core's recording fake (no request leaves the test), as
in ``webhooks/tests/test_store.py``. Each received body verifies with the endpoint's secret and validates against its
frozen v1 schema.
"""

from __future__ import annotations

import json

import pytest

from model_hub.services import prompt_label as writer
from model_hub.tests.prompt_webhook_helpers import (
    make_template,
    make_version,
    system_labels,
)
from webhooks.core.ports import TransportResult
from webhooks.core.signing import new_secret, verify
from webhooks.core.tests.test_conformance_schemas_events import validator
from webhooks.models import Delivery
from webhooks.tests.factories import make_endpoint, use_test_keys
from webhooks.tests.test_store import PUBLIC, Clock, dispatcher

SNAPSHOT = {
    "messages": [
        {"role": "system", "content": [{"type": "text", "text": "Answer refunds."}]},
        {"role": "user", "content": [{"type": "text", "text": "{{question}}"}]},
    ],
    "configuration": {"model": "gpt-4o-mini"},
}


@pytest.mark.django_db(transaction=True)
def test_a_commit_and_a_label_move_reach_each_endpoint_as_its_variant(
    organization, workspace, user, monkeypatch
):
    use_test_keys(monkeypatch)
    secrets = {k: new_secret() for k in ("names", "anon", "full", "prod")}
    eps = {
        "names": make_endpoint(
            organization,
            workspace,
            url="https://hooks.acme.ai/names",
            secret=secrets["names"],
        ),
        "anon": make_endpoint(
            organization,
            workspace,
            url="https://hooks.acme.ai/anon",
            secret=secrets["anon"],
            include_people_names=False,
        ),
        "full": make_endpoint(
            organization,
            workspace,
            url="https://hooks.acme.ai/full",
            secret=secrets["full"],
            payload_mode="full",
        ),
        # Only Production label events: the version event and nothing else must be filtered out.
        "prod": make_endpoint(
            organization,
            workspace,
            url="https://hooks.acme.ai/prod",
            secret=secrets["prod"],
            event_types=("prompt.label.changed",),
            filters={"label_names": ["Production"]},
        ),
    }
    t = make_template(organization, workspace, user=user)
    v1 = make_version(t, 1, snapshot=SNAPSHOT)
    with writer.acting_as(user):
        v2 = make_version(t, 2, message="Tighten the refund wording", snapshot=SNAPSHOT)
        writer.move_label(v1, system_labels()["Production"])
        writer.move_label(v2, system_labels()["Production"])
        writer.move_label(v2, system_labels()["Staging"])

    clock = Clock()
    d, tr = dispatcher(clock, TransportResult(status=204, connected_ip=PUBLIC))
    for _ in range(5):
        d.tick()
        d.drain()
    assert set(Delivery.objects.values_list("status", flat=True)) == {"succeeded"}

    got: dict[str, list[dict]] = {k: [] for k in eps}
    for req in tr.requests:
        name = req.target.rsplit("/", 1)[-1]
        assert (
            verify(secrets[name], dict(req.headers), req.body, clock.ms // 1000) == "ok"
        )
        doc = json.loads(req.body)
        schema = f"urn:futureagi:webhooks:v1:{doc['type']}"
        assert list(validator(schema).iter_errors(doc)) == [], doc
        got[name].append(doc)

    def kinds(docs):
        return sorted(
            (
                doc["type"],
                doc["data"].get("action"),
                doc["data"].get("label", {}).get("name"),
            )
            for doc in docs
        )

    everything = sorted(
        [
            ("prompt.version.created", None, None),
            ("prompt.version.created", None, None),
            ("prompt.label.changed", "assigned", "Production"),
            ("prompt.label.changed", "moved", "Production"),
            ("prompt.label.changed", "assigned", "Staging"),
        ]
    )
    assert kinds(got["names"]) == kinds(got["anon"]) == kinds(got["full"]) == everything
    assert kinds(got["prod"]) == [
        ("prompt.label.changed", "assigned", "Production"),
        ("prompt.label.changed", "moved", "Production"),
    ]
    # The same events, each endpoint's own variant.
    by_v2 = {
        k: next(
            d
            for d in docs
            if d["type"] == "prompt.version.created"
            and d["data"]["version"]["version"] == "v2"
        )
        for k, docs in got.items()
        if k != "prod"
    }
    assert by_v2["names"]["data"]["version"]["created_by"] == {
        "id": str(user.id),
        "name": "Test User",
    }
    assert by_v2["anon"]["data"]["version"]["created_by"] == {"id": str(user.id)}
    assert "content" not in by_v2["names"]["data"]
    assert by_v2["full"]["data"]["content"] == {
        "messages": [
            {"role": "system", "text": "Answer refunds."},
            {"role": "user", "text": "{{question}}"},
        ],
        "variables": [],
        "model": "gpt-4o-mini",
    }
    assert by_v2["names"]["id"] == by_v2["anon"]["id"] == by_v2["full"]["id"]
    assert by_v2["names"]["data"]["previous_version"]["version"] == "v1"
    moved = next(d for d in got["prod"] if d["data"]["action"] == "moved")
    assert moved["data"]["from_version"]["version"] == "v1"
    assert moved["data"]["to_version"] == {
        "id": str(v2.id),
        "version": "v2",
        "commit_message": "Tighten the refund wording",
    }
    assert moved["data"]["revision"] == 2
    assert moved["data"]["changed_by"]["name"] == "Test User"
    assert all("@" not in json.dumps(doc) for docs in got.values() for doc in docs)


@pytest.mark.django_db(transaction=True)
def test_a_full_body_that_cannot_fit_reaches_full_endpoints_without_content_and_thin_ones_as_usual(
    organization, workspace, user, monkeypatch
):
    """A full-mode body that cannot fit in 64 KB even with every text cut to nothing (here 100 KB of
    variable names, which are never cut) used to raise in the builder, and the fan-out marked the whole event failed:
    the thin endpoints of the same organization got nothing either. Now the full endpoint gets the event with
    ``content_omitted: "too_large"`` and the thin one gets its usual body."""
    use_test_keys(monkeypatch)
    secrets = {k: new_secret() for k in ("thin", "full")}
    make_endpoint(
        organization,
        workspace,
        url="https://hooks.acme.ai/thin",
        secret=secrets["thin"],
    )
    make_endpoint(
        organization,
        workspace,
        url="https://hooks.acme.ai/full",
        secret=secrets["full"],
        payload_mode="full",
    )
    t = make_template(organization, workspace, user=user)
    big = make_version(t, 1, snapshot=SNAPSHOT, draft=True)
    big.variable_names = {f"{i:04d}" + "v" * 96: [] for i in range(1000)}
    big.save(update_fields=["variable_names"])
    assert writer.commit_version(big)

    clock = Clock()
    d, tr = dispatcher(clock, TransportResult(status=204, connected_ip=PUBLIC))
    for _ in range(5):
        d.tick()
        d.drain()
    assert set(Delivery.objects.values_list("status", flat=True)) == {"succeeded"}
    got = {}
    for req in tr.requests:
        name = req.target.rsplit("/", 1)[-1]
        assert (
            verify(secrets[name], dict(req.headers), req.body, clock.ms // 1000) == "ok"
        )
        doc = json.loads(req.body)
        got[name] = (doc, len(req.body))
    assert len(tr.requests) == 2 and sorted(got) == ["full", "thin"]
    thin, full = got["thin"][0], got["full"][0]
    assert "content" not in thin["data"] and "content_omitted" not in thin["data"]
    assert (
        list(
            validator("urn:futureagi:webhooks:v1:prompt.version.created").iter_errors(
                thin
            )
        )
        == []
    )
    assert full["data"]["content_omitted"] == "too_large"
    assert "content" not in full["data"] and "content_truncated" not in full["data"]
    assert got["full"][1] <= 64 * 1024
    assert thin["id"] == full["id"]
    # The frozen v1 schema knows only "secret_shape"; "too_large" is the additive value handed to the WH-0 owner.
    errors = validator("urn:futureagi:webhooks:v1:prompt.version.created").iter_errors(
        full
    )
    assert [list(e.absolute_path) for e in errors] == [["data", "content_omitted"]]
