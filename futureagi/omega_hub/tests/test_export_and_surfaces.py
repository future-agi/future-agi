"""The registry export (SPEC §9.5), and no conversation surface anywhere (§2.2)."""

import json
from pathlib import Path

import jsonschema
import pytest
from django.apps import apps
from django.urls import get_resolver

from omega_hub.models import Submission
from omega_hub.tests.flows import publish
from omega_hub.tests.helpers import entry_files, submit, token_client

pytestmark = pytest.mark.django_db
SCHEMAS = Path(__file__).resolve().parents[1] / "schemas"
CONVERSATION = ("comment", "discussion", "question", "reply", "thread")


def schema(name):
    return json.loads((SCHEMAS / name).read_text())


def test_export_validates_and_lists_only_approved_listed_entries(world):
    publish(world, "first-agent")
    publish(world, "notes-plugin", token=world.token_c, publisher="c-test")
    submit(token_client(world.token_a), "future-agi", entry_files("engineering-team"))  # still received
    world.b.post("/omega-hub/v1/review/entries/c-test/notes-plugin/remove",
                 {"category": "breaks Hub rules", "note": "Spam."}, format="json")
    response = world.c.get("/omega-hub/v1/export")
    assert response.status_code == 200 and response["Cache-Control"] == "public, max-age=300"
    document = response.json()
    jsonschema.validate(document, schema("omega-hub-registry-v1.schema.json"))
    assert [entry["ref"] for entry in document["entries"]] == ["future-agi/first-agent"]
    assert document["entries"][0]["tier"] == "official"
    assert document["tombstones"][0]["ref"] == "c-test/notes-plugin"
    assert Submission.objects.filter(status="received").exists()
    text = response.content.decode()
    for secret in (world.maint_b.email, world.carol.email, "Spam.", '"disclosure_draft"'):
        assert secret not in text
    assert world.c.get("/omega-hub/v1/export", HTTP_IF_NONE_MATCH=response["ETag"]).status_code == 304


def test_blocklist_document_validates(world):
    publish(world, "first-agent")
    world.b.post("/omega-hub/v1/review/entries/future-agi/first-agent/remove",
                 {"category": "security issue", "note": "Bad."}, format="json")
    jsonschema.validate(world.c.get("/omega-hub/v1/blocklist").json(), schema("omega-hub-blocklist-v1.schema.json"))


def _patterns(resolver, prefix=""):
    for pattern in resolver.url_patterns:
        if hasattr(pattern, "url_patterns"):
            yield from _patterns(pattern, prefix + str(pattern.pattern))
        else:
            yield prefix + str(pattern.pattern)


def test_no_url_pattern_is_a_conversation_surface():
    hub = [p for p in _patterns(get_resolver()) if p.startswith("omega-hub/")]
    assert len(hub) > 50
    for route in hub:
        assert not any(word in route.lower() for word in CONVERSATION), route


def test_no_model_has_a_conversation_field():
    for model in apps.get_app_config("omega_hub").get_models():
        names = [model.__name__.lower(), *(field.name.lower() for field in model._meta.get_fields())]
        for name in names:
            assert not any(word in name for word in CONVERSATION), (model.__name__, name)


def test_discord_link_on_entries(world):
    publish(world, "first-agent")
    detail = world.c.get("/omega-hub/v1/entries/future-agi/first-agent").json()
    assert detail["discord"] == "https://discord.com/invite/n2tCUKBkAw"
    assert world.c.get("/omega-hub/v1/rules").json()["contacts"]["discord"] == detail["discord"]
