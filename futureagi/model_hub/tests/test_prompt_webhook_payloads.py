"""XP-06: the prompt app's builder inputs reproduce the frozen WH-0 payload vectors byte for byte (webhooks
DESIGN.md sections 14.2 and 14.3; ``webhooks/core/conformance/payloads.json``).

Each prompt case of ``payloads.json`` is turned into prompt rows (template, versions, labels, the person), the
adapter (``model_hub.services.prompt_events``) builds the builder input from those rows, and the body built from it
at fan-out (``webhooks.core.payloads.variant_body``) must equal the vector's bytes. Only the links differ by design:
the vectors use example hosts, so the adapter's links are checked on their own and the vector's are used for the
byte comparison. No database: the rows are unsaved model instances.
"""

from __future__ import annotations

import base64
import copy
import json
import uuid
from datetime import datetime

import pytest
from django.test import override_settings

from accounts.models import User
from model_hub.models.prompt_label import PromptLabel
from model_hub.models.run_prompt import PromptTemplate, PromptVersion
from model_hub.services import prompt_events
from model_hub.services.prompt_label import LabelChange
from webhooks.core.payloads import variant_body
from webhooks.core.tests.test_conformance_schemas_events import load, validator

PROMPT_CASES = [
    c
    for c in load("payloads.json")["build"]
    if c["builder"].startswith("prompt.") and not c["input"].get("test")
]


def _ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _person(p):
    if p is None:
        return None
    return User(id=uuid.UUID(p["id"]), name=p["name"] or "", email="asha@example.test")


def _snapshot(content):
    if content is None:
        return {"messages": [{"role": "user", "content": "unused"}]}, {}
    snap = {
        "messages": [
            {"role": m["role"], "content": [{"type": "text", "text": m["text"]}]}
            for m in content.get("messages", [])
        ],
        "configuration": {"model": content.get("model")},
    }
    return snap, {v: [] for v in content.get("variables", [])}


def _version(ref, template, content=None, **extra):
    if ref is None:
        return None
    snap, variables = _snapshot(content)
    return PromptVersion(
        id=uuid.UUID(ref["id"]),
        original_template=template,
        template_version=ref["version"],
        commit_message=ref.get("commit_message"),
        prompt_config_snapshot=snap,
        variable_names=variables,
        **extra,
    )


def _label(lbl):
    return PromptLabel(id=uuid.UUID(lbl["id"]), name=lbl["name"], type=lbl["type"])


def adapter_input(case) -> dict:
    """The builder input the adapter makes from rows equivalent to the case's input."""
    inp = case["input"]
    template = PromptTemplate(
        id=uuid.UUID(inp["prompt"]["id"]), name=inp["prompt"]["name"]
    )
    content = inp.get("content")
    if case["builder"] == "prompt.version.created":
        v = inp["version"]
        version = _version(v, template, content, is_default=v["is_default"])
        return prompt_events.version_created_input(
            version,
            template,
            actor=_person(v.get("created_by")),
            created_at=_ts(v["created_at"]),
            labels=[_label(lbl) for lbl in v.get("labels", [])],
            previous=_version(inp.get("previous_version"), template),
            content=content is not None,
        )
    change = LabelChange(
        label=_label(inp["label"]),
        revision=inp["revision"],
        from_version=_version(inp.get("from_version"), template),
        to_version=_version(inp.get("to_version"), template, content),
        cause=inp.get("cause", "label_change"),
    )
    return prompt_events.label_changed_input(
        change,
        template,
        actor=_person(inp.get("changed_by")),
        changed_at=_ts(inp["changed_at"]),
        content=content is not None,
    )


@pytest.mark.parametrize("case", PROMPT_CASES, ids=[c["id"] for c in PROMPT_CASES])
def test_the_adapter_reproduces_each_prompt_vector_byte_for_byte(case):
    built = adapter_input(case)
    built["links"] = dict(case["input"]["links"])  # the vectors' example hosts
    inp = case["input"]
    body = variant_body(
        event_id=inp["event_id"],
        type_=case["builder"],
        timestamp="",
        organization_id=inp["organization_id"],
        workspace_id=inp["workspace_id"],
        data={},
        builder_input=built,
        variant=case["variant"],
    )
    assert body == base64.b64decode(case["expected"]["body_bytes_b64"])
    assert list(validator(case["schema"]).iter_errors(json.loads(body))) == []


def test_the_vectors_cover_both_prompt_events_and_every_label_action():
    assert {c["builder"] for c in PROMPT_CASES} == {
        "prompt.version.created",
        "prompt.label.changed",
    }
    actions = {
        json.loads(base64.b64decode(c["expected"]["body_bytes_b64"]))["data"]["action"]
        for c in PROMPT_CASES
        if c["builder"] == "prompt.label.changed"
    }
    assert actions == {"assigned", "moved", "removed"}


@override_settings(
    FRONTEND_BASE_URL="https://app.example.test/", BASE_URL="https://api.example.test"
)
def test_links_point_at_the_workbench_page_and_the_template_route():
    t = PromptTemplate(id=uuid.UUID("6c0e4b0a-1d2e-4f3a-8b4c-5d6e7f8a9b0c"), name="x")
    assert prompt_events.links(t) == {
        "app": "https://app.example.test/dashboard/workbench/create/6c0e4b0a-1d2e-4f3a-8b4c-5d6e7f8a9b0c",
        "api": "https://api.example.test/model-hub/prompt-templates/6c0e4b0a-1d2e-4f3a-8b4c-5d6e7f8a9b0c/",
    }


@override_settings(FRONTEND_BASE_URL="", BASE_URL="http://localhost:8000/")
def test_links_fall_back_to_the_api_host_when_no_frontend_url_is_set():
    """Only when neither FRONTEND_URL nor APP_URL is set (FRONTEND_BASE_URL empty): a single-host install, where the
    API host also serves the UI."""
    t = PromptTemplate(id=uuid.UUID("6c0e4b0a-1d2e-4f3a-8b4c-5d6e7f8a9b0c"), name="x")
    assert prompt_events.links(t)["app"].startswith(
        "http://localhost:8000/dashboard/workbench/create/"
    )


def test_people_are_a_display_name_or_null_never_an_email():
    uid = uuid.uuid4()
    assert prompt_events.person(User(id=uid, name="Asha Rao", email="asha@x.test")) == {
        "id": str(uid),
        "name": "Asha Rao",
    }
    assert (
        prompt_events.person(User(id=uid, name="", email="asha@x.test"))["name"] is None
    )
    assert (
        prompt_events.person(User(id=uid, name="Asha@X.test", email="asha@x.test"))[
            "name"
        ]
        is None
    )
    assert prompt_events.person(None) is None


def test_an_invited_user_named_after_their_email_is_named_by_no_name():
    """FA's invite flows store ``name = email.split("@")[0]`` (``accounts/views/
    rbac_views.py``, ``accounts/services/workspace_members.py``), so such a name is an email fallback: null. So is
    any name with an ``@`` in it. A display name the person set is kept."""
    uid = uuid.uuid4()

    def name(n, email="asha.rao@x.test"):
        return prompt_events.person(User(id=uid, name=n, email=email))["name"]

    email = "asha.rao@x.test"
    assert (
        name(email.split("@")[0], email) is None
    )  # exactly what the invite flow stores
    assert name("Asha.Rao") is None  # the same, cased differently
    assert name("someone@else.test") is None
    assert name("asha@") is None
    assert name("Asha Rao") == "Asha Rao"
    assert name("asha.rao.ops") == "asha.rao.ops"


def test_content_keeps_roles_and_text_parts_only():
    v = PromptVersion(
        prompt_config_snapshot={
            "messages": [
                {"role": "system", "content": "Plain string"},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "a"},
                        {
                            "type": "image_url",
                            "image_url": {"url": "https://x.test/i.png"},
                        },
                        {"type": "text", "text": "b"},
                    ],
                },
            ],
            "configuration": {
                "model": "gpt-4o-mini",
                "tools": [{"name": "t"}],
                "temperature": 0.2,
            },
        },
        variable_names={"question": ["x"], "name": []},
    )
    assert prompt_events.content_of(v) == {
        "messages": [
            {"role": "system", "text": "Plain string"},
            {"role": "user", "text": "a\nb"},
        ],
        "variables": ["question", "name"],
        "model": "gpt-4o-mini",
    }


def test_an_oversized_full_body_from_real_rows_is_cut_and_schema_valid():
    case = copy.deepcopy(next(c for c in PROMPT_CASES if c["id"] == "pvc-full"))
    case["input"]["content"]["messages"][0]["text"] = "x" * 100_000
    built = adapter_input(case)
    body = variant_body(
        event_id=case["input"]["event_id"],
        type_="prompt.version.created",
        timestamp="",
        organization_id=case["input"]["organization_id"],
        workspace_id=case["input"]["workspace_id"],
        data={},
        builder_input={**built, "links": case["input"]["links"]},
        variant=case["variant"],
    )
    doc = json.loads(body)
    assert len(body) <= 64 * 1024 and doc["data"]["content_truncated"] is True
    assert list(validator(case["schema"]).iter_errors(doc)) == []
