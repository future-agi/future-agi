"""Mounted Django admin regressions, executed only by fixture-cloud runner."""

import copy
import json

import pytest
from django.contrib import admin
from django.contrib.auth.models import Permission
from django.db import connection
from django.test import Client, RequestFactory
from django.urls import reverse

from ee.falcon_ai.models import Conversation, Message
from simulate.models import AgentDefinition, AgentVersion
from simulate.models.run_test import RunTest

SNAPSHOT_TOOLS = (
    "list_agents",
    "get_agent",
    "create_agent",
    "update_agent",
    "create_agent_version",
    "list_simulation_tests",
    "get_simulation_test",
    "create_simulation_test",
)
SECRET_KEYS = frozenset(
    {
        "api_key",
        "api_secret",
        "encrypted_api_key",
        "encrypted_api_secret",
        "livekit_api_key",
        "livekit_api_secret",
        "public_api_key",
        "token",
        "password",
        "credentials",
        "credentials_legacy",
    }
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def stored(user, organization, workspace):
    user.is_staff = True
    user.is_superuser = True
    user.save(update_fields=["is_staff", "is_superuser"])
    conversation = Conversation.objects.create(
        user=user, organization=organization, workspace=workspace, title="Admin fixture"
    )
    snapshot = {
        "agent_type": "voice",
        **dict.fromkeys(SECRET_KEYS, "stored-snapshot-fixture"),
        "nested": [dict.fromkeys(SECRET_KEYS, "stored-snapshot-fixture")],
        "keep": "visible",
    }
    message = Message.objects.create(
        conversation=conversation,
        role="assistant",
        content="visible",
        thoughts=["visible"],
        tool_calls=[
            {
                "tool_name": "get_simulation_test",
                "result_full": json.dumps({"configuration_snapshot": snapshot}),
                "result_summary": "safe summary",
            }
        ],
    )
    client = Client()
    client.force_login(user)
    return client, user, conversation, message


def raw_json(message):
    # PostgreSQL's actual stored textual representation, not a Python re-encode.
    table = connection.ops.quote_name(Message._meta.db_table)
    with connection.cursor() as cursor:
        cursor.execute(
            f"SELECT tool_calls::text FROM {table} WHERE id = %s", [message.pk]
        )
        return cursor.fetchone()[0]


def clean(response):
    assert response.status_code == 200, "admin response not 200"
    assert b"stored-snapshot-fixture" not in response.content, "raw closed key rendered"


def change_url(message):
    return reverse("admin:falcon_ai_message_change", args=[message.pk])


@pytest.mark.parametrize("tool", sorted(SNAPSHOT_TOOLS))
@pytest.mark.parametrize("view_only", [False, True])
def test_mounted_admin_change_projects_stored_results(stored, tool, view_only):
    client, user, _, message = stored
    calls = copy.deepcopy(message.tool_calls)
    calls[0]["tool_name"] = tool
    Message.objects.filter(pk=message.pk).update(tool_calls=calls)
    if view_only:
        user.is_superuser = False
        user.save(update_fields=["is_superuser"])
        user.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="falcon_ai", codename="view_message"
            )
        )
    before = raw_json(message)
    response = client.get(change_url(message))
    clean(response)
    assert (
        response.wsgi_request.resolver_match.view_name
        == "admin:falcon_ai_message_change"
    )
    form = response.context["adminform"].form
    assert "tool_calls" not in form.fields
    assert b"safe summary" in response.content
    assert raw_json(message) == before


def test_mounted_admin_truncated_and_changelist(stored):
    client, _, _, message = stored
    calls = copy.deepcopy(message.tool_calls)
    calls[0]["result_full"] = calls[0]["result_full"][:-3]
    Message.objects.filter(pk=message.pk).update(tool_calls=calls)
    before = raw_json(message)
    clean(client.get(change_url(message)))
    clean(client.get(reverse("admin:falcon_ai_message_changelist")))
    clean(client.get(reverse("admin:falcon_ai_message_changelist"), {"q": "visible"}))
    assert raw_json(message) == before


def test_admin_save_ignores_submitted_raw_json(stored):
    client, _, _, message = stored
    before = raw_json(message)
    response = client.post(
        change_url(message),
        {
            "conversation": str(message.conversation_id),
            "role": "assistant",
            "content": "edited",
            "thoughts": '["visible"]',
            "tool_calls": '[{"replacement": true}]',
            "files": "[]",
            "token_count": "0",
            "input_tokens": "0",
            "output_tokens": "0",
            "latency_ms": "0",
            "_save": "Save",
        },
    )
    assert response.status_code == 302, "admin fixture did not save: " + ",".join(
        response.context["adminform"].form.errors
    )
    message.refresh_from_db()
    assert message.content == "edited"
    assert raw_json(message) == before, "admin save rewrote stored tool calls"
    clean(client.get(change_url(message)))


def test_admin_invalid_post_does_not_reflect_raw_json(stored):
    client, _, _, message = stored
    before = raw_json(message)
    response = client.post(
        change_url(message),
        {
            "role": "not-a-role",
            "tool_calls": json.dumps(message.tool_calls),
        },
    )
    clean(response)
    assert response.context["adminform"].form.errors
    assert raw_json(message) == before


def test_admin_registry_closes_relations_inlines_and_exports(stored):
    _, user, _, message = stored
    assert not any(
        model in admin.site._registry
        for model in (AgentVersion, AgentDefinition, RunTest)
    )
    request = RequestFactory().get("/admin/")
    request.user = user
    entry = admin.site._registry[Message]
    assert "tool_calls" not in entry.get_form(request, message).base_fields
    assert "display_tool_calls" in entry.get_readonly_fields(request, message)
    assert entry.get_inlines(request, message) == ()
    assert set(entry.get_actions(request)) <= {"delete_selected"}, (
        "unexpected export action"
    )
    assert "tool_calls" not in entry.get_list_display(request)
    assert "tool_calls" not in entry.get_search_fields(request)
    # No parent or foreign registration can reintroduce a Message inline.
    for registered in admin.site._registry.values():
        stored_fields = {field.name for field in registered.model._meta.concrete_fields}
        assert "configuration_snapshot" not in stored_fields
        if "tool_calls" in stored_fields:
            assert registered.model is Message, "new tool-result admin needs projection"
        assert all(
            inline.model not in (Message, AgentVersion, RunTest)
            for inline in registered.inlines
        )
    assert admin.site._registry[Conversation].inlines == ()


def test_other_mounted_admin_surfaces_are_clean(stored):
    client, _, conversation, message = stored
    before = raw_json(message)
    urls = [
        reverse("admin:falcon_ai_message_add"),
        reverse("admin:falcon_ai_message_delete", args=[message.pk]),
        reverse("admin:falcon_ai_message_history", args=[message.pk]),
        reverse("admin:falcon_ai_conversation_change", args=[conversation.pk]),
        reverse("admin:falcon_ai_conversation_changelist"),
    ]
    for url in urls:
        clean(client.get(url))
    assert raw_json(message) == before


def test_admin_anonymous_is_denied(stored):
    response = Client().get(change_url(stored[3]))
    assert response.status_code == 302
    assert "/admin/login/" in response.url
