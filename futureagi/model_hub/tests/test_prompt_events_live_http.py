"""XP-06 WT-21 over a real socket: the SDK's two prompt calls (commit a version, move a label) sent as HTTP requests
to FA running in a live test server on 127.0.0.1, authenticated with a real API key by FA's own authentication
class. Each produces exactly one event naming the caller (webhooks DESIGN.md sections 14.2, 14.3, 17).
"""

from __future__ import annotations

import json
import urllib.request

import pytest

from accounts.models.user import OrgApiKey
from model_hub.tests.prompt_webhook_helpers import (
    LABEL_EVENT,
    VERSION_EVENT,
    events,
    holders,
    make_template,
    make_version,
    system_labels,
)
from webhooks.tests.factories import make_endpoint, use_test_keys

NO_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _post(url: str, key: OrgApiKey, body: dict) -> tuple[int, dict]:
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-Api-Key": key.api_key,
            "X-Secret-Key": key.secret_key,
        },
    )
    with NO_PROXY.open(req, timeout=30) as resp:
        return resp.status, json.loads(resp.read())


@pytest.mark.django_db(transaction=True)
def test_sdk_commit_and_label_move_over_http_each_publish_once(
    live_server, organization, workspace, user, monkeypatch
):
    from model_hub.views import prompt_template as views

    monkeypatch.setattr(
        views, "track_mixpanel_event", lambda *a, **k: None
    )  # no analytics call leaves the test
    use_test_keys(monkeypatch)
    make_endpoint(organization, workspace, event_types=("prompt.*",))
    # A user key: its user is the caller. (A system key names no person on the label routes: the census.)
    key = OrgApiKey.no_workspace_objects.create(
        organization=organization, workspace=workspace, type="user", user=user
    )
    t = make_template(organization, workspace, name="live-http")
    make_version(t, 1, message="first")
    make_version(t, 2, draft=True)
    P = system_labels()["Production"]
    assert live_server.url.startswith(
        "http://localhost:"
    ) or live_server.url.startswith("http://127.0.0.1:")

    status, _ = _post(
        f"{live_server.url}/model-hub/prompt-templates/{t.id}/commit/",
        key,
        {"version_name": "v2", "message": "Ship it"},
    )
    assert status == 200
    v2_events = [
        e for e in events(VERSION_EVENT) if e.data["version"]["version"] == "v2"
    ]
    assert len(v2_events) == 1
    assert v2_events[0].builder_input["version"]["created_by"] == {
        "id": str(user.id),
        "name": "Test User",
    }

    for target in ("v1", "v2"):
        status, body = _post(
            f"{live_server.url}/model-hub/prompt-labels/{t.id}/{P.id}/assign-label-by-id/",
            key,
            {"version": target},
        )
        assert status == 200, body
    moves = [e.data for e in events(LABEL_EVENT)]
    assert [(d["action"], d["revision"]) for d in moves] == [
        ("assigned", 1),
        ("moved", 2),
    ]
    assert moves[1]["from_version"]["version"] == "v1"
    assert moves[1]["to_version"]["version"] == "v2"
    assert moves[1]["changed_by"] == {"id": str(user.id)}  # stored data: names off
    assert holders(t, P) == ["v2"]
