"""Shared rows and readers for the XP-06 prompt webhook tests (webhooks DESIGN.md sections 14.2, 14.3 and 17)."""

from __future__ import annotations

import pytest

from model_hub.models.prompt_label import LabelTypeChoices, PromptLabel, PromptLabelHead
from model_hub.models.run_prompt import PromptTemplate, PromptVersion
from webhooks.models import Event
from webhooks.tests.factories import make_endpoint, use_test_keys

LABEL_EVENT = "prompt.label.changed"
VERSION_EVENT = "prompt.version.created"


@pytest.fixture
def webhook_endpoint(monkeypatch, organization, workspace):
    """One live endpoint for every prompt event, so ``publish()`` writes event rows (it skips an organization with
    no endpoint)."""
    use_test_keys(monkeypatch)
    return make_endpoint(organization, workspace, event_types=("prompt.*",))


def system_labels() -> dict[str, PromptLabel]:
    PromptLabel.create_default_system_labels()
    return {
        lbl.name: lbl
        for lbl in PromptLabel.no_workspace_objects.filter(
            organization__isnull=True, type=LabelTypeChoices.SYSTEM.value, deleted=False
        )
    }


def make_template(organization, workspace, name="support-reply", user=None):
    return PromptTemplate.objects.create(
        name=name, organization=organization, workspace=workspace, created_by=user
    )


def make_version(template, n: int, *, draft=False, message="", snapshot=None):
    return PromptVersion.objects.create(
        original_template=template,
        template_version=f"v{n}",
        prompt_config_snapshot=snapshot
        or {
            "messages": [{"role": "user", "content": [{"type": "text", "text": "hi"}]}]
        },
        is_draft=draft,
        commit_message=message,
    )


def events(type_: str | None = None, **data_match) -> list[Event]:
    qs = Event.objects.order_by("created_at", "id")  # insert order: chronological
    if type_:
        qs = qs.filter(type=type_)
    out = list(qs)
    for k, v in data_match.items():
        out = [e for e in out if e.data.get(k) == v]
    return out


def label_events(template=None, label_name=None) -> list[dict]:
    """The stored ``data`` of every label event, in the order they were published."""
    return [
        e.data
        for e in events(LABEL_EVENT)
        if (template is None or e.data["prompt"]["id"] == str(template.id))
        and (label_name is None or e.data["label"]["name"] == label_name)
    ]


def holders(template, label) -> list[str]:
    """The live versions holding ``label`` within ``template``, by the attachment table."""
    return sorted(
        PromptVersion.labels.through.objects.filter(
            promptlabel_id=label.id,
            promptversion__original_template=template,
            promptversion__deleted=False,
        ).values_list("promptversion__template_version", flat=True)
    )


def head(template, label) -> PromptLabelHead:
    return PromptLabelHead.objects.get(template=template, label=label)
