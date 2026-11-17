"""Likes, reports, counts and the workspace Viewer (SPEC §6.1, §6.2, §6.6, §9.1)."""

import pytest
from django.core.cache import cache

from accounts.models import OrgApiKey
from omega_hub.models import DownloadDaily, Entry, Like
from omega_hub.tests.flows import publish
from omega_hub.tests.helpers import make_person, session_client, token_client
from tfc.constants.levels import Level

pytestmark = pytest.mark.django_db
LIKE = "/omega-hub/v1/entries/future-agi/first-agent/like"
BUNDLE = "/omega-hub/v1/entries/future-agi/first-agent/versions/1.0.0/bundle"


def test_a_session_can_like_and_unlike(world):
    publish(world)
    liked = world.c.post(LIKE)
    assert liked.status_code == 200 and liked.json() == {"likes": 1, "likedByMe": True}
    assert world.c.post(LIKE).json()["likes"] == 1
    assert world.c.get("/omega-hub/v1/entries/future-agi/first-agent").json()["likedByMe"] is True
    assert world.c.delete(LIKE).json() == {"likes": 0, "likedByMe": False}
    assert Entry.objects.get().like_count == 0


def test_likes_refuse_hub_tokens_and_org_api_keys(world):
    publish(world)
    token = token_client(world.token_c).post(LIKE)
    assert token.status_code == 403 and token.json()["error"]["code"] == "OMEGA_HUB_LIKE_NEEDS_SESSION"
    key = OrgApiKey.objects.create(organization=world.carol.organization, api_key="ak-carol-0123456789",
                                   secret_key="sk-carol-0123456789", type="user", user=world.carol.user,
                                   workspace=world.carol.workspace)
    from rest_framework.test import APIClient

    client = APIClient()
    client.credentials(HTTP_X_API_KEY=key.api_key, HTTP_X_SECRET_KEY=key.secret_key)
    response = client.post(LIKE)
    assert response.status_code == 403, response.content
    assert response.json()["error"]["code"] == "OMEGA_HUB_LIKE_NEEDS_SESSION"
    assert Like.objects.count() == 0


def test_no_public_surface_lists_likers(world):
    publish(world)
    world.c.post(LIKE)
    email = world.carol.email
    for path in ("/omega-hub/v1/entries/future-agi/first-agent", "/omega-hub/v1/entries", "/omega-hub/v1/export",
                 "/omega-hub/v1/publishers/future-agi"):
        assert email not in world.c.get(path).content.decode(), path
    likers = world.b.get("/omega-hub/v1/review/entries/future-agi/first-agent/likers")
    assert likers.status_code == 200 and likers.json()[0]["user"] == email
    assert world.c.get("/omega-hub/v1/review/entries/future-agi/first-agent/likers").status_code == 403
    mine = world.c.get("/omega-hub/v1/me/likes").json()
    assert [card["ref"] for card in mine] == ["future-agi/first-agent"]


def test_a_workspace_viewer_can_like_report_create_a_publisher_and_submit(world):
    publish(world)
    viewer = make_person("viewer", org_level=Level.VIEWER, workspace_level=Level.WORKSPACE_VIEWER)
    client = session_client(viewer)
    assert client.post(LIKE).status_code == 200
    report = client.post("/omega-hub/v1/reports", {"entry": "future-agi/first-agent", "reason": "broken",
                                                   "details": "It crashes on start with a stack trace."}, format="json")
    assert report.status_code == 201, report.content
    created = client.post("/omega-hub/v1/publishers", {"handle": "vic-tools", "kind": "user", "displayName": "Vic"},
                          format="json")
    assert created.status_code == 201, created.content
    from omega_hub.tests.helpers import entry_files, submit

    assert submit(client, "vic-tools", entry_files("first-agent")).status_code == 201


def test_downloads_count_once_per_fingerprint_per_day_and_store_no_ip(world):
    publish(world)
    anon = __import__("rest_framework.test", fromlist=["APIClient"]).APIClient()
    for _ in range(2):
        assert anon.get(BUNDLE, REMOTE_ADDR="203.0.113.9", HTTP_USER_AGENT="omega/1").status_code == 200
    row = DownloadDaily.objects.get()
    assert row.count == 1 and row.source == "cli"
    anon.get(BUNDLE, REMOTE_ADDR="203.0.113.10", HTTP_USER_AGENT="omega/1")
    assert DownloadDaily.objects.get().count == 2
    stored = " ".join(str(value) for value in DownloadDaily.objects.values().first().values())
    assert "203.0.113" not in stored and "omega/1" not in stored
    keys = list(getattr(cache, "_cache", {}).keys())
    assert keys and not any("203.0.113" in str(key) for key in keys)
    detail = world.c.get("/omega-hub/v1/entries/future-agi/first-agent").json()
    assert detail["installs30d"] == 2 and detail["installsDaily30"][-1] == 2


def test_opens_count_only_for_a_session(world):
    publish(world)
    anon = __import__("rest_framework.test", fromlist=["APIClient"]).APIClient()
    anon.get(BUNDLE + "?source=studio")
    assert not DownloadDaily.objects.filter(source="studio").exists()
    world.c.get(BUNDLE + "?source=studio")
    assert DownloadDaily.objects.get(source="studio").count == 1
    detail = world.c.get("/omega-hub/v1/entries/future-agi/first-agent").json()
    assert detail["opens30d"] == 1 and detail["installs30d"] == 0


def test_reports_need_sign_in_and_stay_private(world):
    publish(world)
    from rest_framework.test import APIClient

    anon = APIClient().post("/omega-hub/v1/reports", {"entry": "future-agi/first-agent", "reason": "spam",
                                                       "details": "x" * 30}, format="json")
    assert anon.status_code == 401
    short = world.c.post("/omega-hub/v1/reports", {"entry": "future-agi/first-agent", "reason": "spam",
                                                   "details": "too short"}, format="json")
    assert short.status_code == 400
    ok = world.c.post("/omega-hub/v1/reports", {"entry": "future-agi/first-agent", "reason": "security",
                                                "details": "It reads ~/.aws/credentials at start."}, format="json")
    assert ok.status_code == 201
    queue = world.b.get("/omega-hub/v1/review/reports").json()
    assert queue[0]["reporter"] == world.carol.email
    assert "aws" not in world.c.get("/omega-hub/v1/export").content.decode()
