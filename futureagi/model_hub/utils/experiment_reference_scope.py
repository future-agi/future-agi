"""Resolve experiment references before any snapshot, metric or config write."""

from django.db.models import Q
from django.http import Http404

from model_hub.models.choices import OwnerChoices
from model_hub.models.evals_metric import EvalTemplate
from model_hub.models.run_prompt import PromptVersion
from model_hub.utils.workspace_scope import (
    explicit_workspace_filter,
    knowledge_base_id_for,
)


def eval_templates_for(organization, workspace):
    if organization is None:
        return EvalTemplate.no_workspace_objects.none()
    return EvalTemplate.no_workspace_objects.filter(
        (Q(organization=organization) & explicit_workspace_filter(workspace))
        | Q(owner=OwnerChoices.SYSTEM.value, organization__isnull=True),
        deleted=False,
    )


def eval_template_for(organization, workspace, template_id):
    template = eval_templates_for(organization, workspace).filter(id=template_id).first()
    if template is None:
        raise Http404("Eval template not found")
    return template


def validate_eval_references(entries, organization, workspace):
    """Validate the entire batch before a caller changes even its first row."""
    for entry in entries:
        eval_template_for(organization, workspace, entry["template_id"])
        if entry.get("kb_id") and knowledge_base_id_for(organization, workspace, entry["kb_id"]) is None:
            raise Http404("Knowledge base not found")


def prompt_version_for(organization, workspace, prompt_id, version_id):
    if organization is None:
        raise Http404("Prompt version not found")
    version = PromptVersion.no_workspace_objects.filter(
        explicit_workspace_filter(workspace, "original_template__workspace"),
        id=version_id, original_template_id=prompt_id,
        original_template__organization=organization,
        original_template__deleted=False, deleted=False,
    ).select_related("original_template").first()
    if version is None:
        raise Http404("Prompt version not found")
    return version


def agent_version_for(organization, workspace, agent_id, version_id):
    from agent_playground.models.graph_version import GraphVersion

    if organization is None:
        raise Http404("Agent version not found")
    version = GraphVersion.no_workspace_objects.filter(
        explicit_workspace_filter(workspace, "graph__workspace"),
        id=version_id, graph_id=agent_id, graph__organization=organization,
        graph__deleted=False, deleted=False,
    ).select_related("graph").first()
    if version is None:
        raise Http404("Agent version not found")
    return version


def validate_prompt_references(entries, organization, workspace):
    for entry in entries:
        if entry.get("agent_version") or entry.get("agent_id"):
            agent_version_for(organization, workspace, entry.get("agent_id"), entry.get("agent_version"))
        elif entry.get("prompt_version") or entry.get("prompt_id"):
            prompt_version_for(organization, workspace, entry.get("prompt_id"), entry.get("prompt_version"))
