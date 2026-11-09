"""XP-06 WT-21, graph activation: activating a graph version commits each linked draft prompt version through the
one writer, so each commit is one ``prompt.version.created`` (webhooks DESIGN.md section 14.3).

Before XP-06, activation committed the drafts with ``.update(is_draft=False)``, which bypasses ``save()`` and
published nothing. Driven over the activate route with FA's whole Django stack.
"""

from __future__ import annotations

import pytest
from django.urls import reverse
from rest_framework import status

from agent_playground.models.choices import GraphVersionStatus, NodeType
from agent_playground.models.graph_version import GraphVersion
from agent_playground.models.node import Node
from agent_playground.models.prompt_template_node import PromptTemplateNode
from model_hub.models.run_prompt import PromptVersion
from model_hub.tests.prompt_webhook_helpers import (  # noqa: F401 - fixture
    VERSION_EVENT,
    events,
    webhook_endpoint,
)

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("webhook_endpoint")]


def _node(version, template, name):
    return Node.no_workspace_objects.create(
        graph_version=version,
        node_template=template,
        type=NodeType.ATOMIC,
        name=name,
        config={},
        position={"x": 0, "y": 0},
    )


def test_activating_a_graph_version_publishes_one_event_per_committed_draft(
    authenticated_client,
    graph,
    dynamic_node_template,
    prompt_template,
    other_prompt_template,
    draft_prompt_version,
):
    inactive = GraphVersion.no_workspace_objects.create(
        graph=graph, version_number=2, status=GraphVersionStatus.INACTIVE, tags=[]
    )
    other_draft = PromptVersion.no_workspace_objects.create(
        original_template=other_prompt_template,
        template_version="v1",
        prompt_config_snapshot={"messages": [{"role": "user", "content": "x"}]},
        is_draft=True,
    )
    # Two nodes on the same draft count once; a third node points at the other prompt's draft.
    for i, (tmpl, pv) in enumerate(
        [
            (prompt_template, draft_prompt_version),
            (prompt_template, draft_prompt_version),
            (other_prompt_template, other_draft),
        ]
    ):
        PromptTemplateNode.no_workspace_objects.create(
            node=_node(inactive, dynamic_node_template, f"n{i}"),
            prompt_template=tmpl,
            prompt_version=pv,
        )
    assert events(VERSION_EVENT) == []
    url = reverse(
        "graph-version-activate",
        kwargs={"pk": str(graph.id), "version_id": str(inactive.id)},
    )
    response = authenticated_client.post(url)
    assert response.status_code == status.HTTP_200_OK, response.content

    published = sorted(
        (e.data["prompt"]["id"], e.data["version"]["id"]) for e in events(VERSION_EVENT)
    )
    assert published == sorted(
        [
            (str(prompt_template.id), str(draft_prompt_version.id)),
            (str(other_prompt_template.id), str(other_draft.id)),
        ]
    )
    for pv in (draft_prompt_version, other_draft):
        pv.refresh_from_db()
        assert pv.is_draft is False


def _save_node_prompt(node, template, version, save, text):
    from agent_playground.services.node_crud import update_node

    return {
        "prompt_template": {
            "messages": [
                {
                    "id": "msg-0",
                    "role": "user",
                    "content": [{"type": "text", "text": text}],
                }
            ],
            "prompt_template_id": template.id,
            "prompt_version_id": version.id,
            "save_prompt_version": save,
        }
    }, update_node


@pytest.mark.parametrize(
    ("start_draft", "save", "expect"),
    [
        (True, True, 1),  # commit the draft in place: one event
        (False, True, 1),  # a new committed version: one event
        (False, False, 0),  # a new draft: none
        (True, False, 0),  # the draft updated in place: none
    ],
)
def test_saving_a_prompt_node_publishes_only_for_a_commit(
    node,
    prompt_template,
    prompt_version,
    draft_prompt_version,
    prompt_template_node,
    user,
    organization,
    workspace,
    start_draft,
    save,
    expect,
):
    start = draft_prompt_version if start_draft else prompt_version
    prompt_template_node.prompt_version = start
    prompt_template_node.save()
    before = len(events(VERSION_EVENT))
    body, update_node = _save_node_prompt(
        node, prompt_template, start, save, "node text"
    )
    update_node(node, body, user, organization, workspace)
    new = events(VERSION_EVENT)[before:]
    assert len(new) == expect
    if expect:
        prompt_template_node.refresh_from_db()
        assert new[0].data["version"]["id"] == str(
            prompt_template_node.prompt_version_id
        )
