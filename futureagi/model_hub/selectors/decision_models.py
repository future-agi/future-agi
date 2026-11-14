"""Read-only decision catalog using execution's scoped credential resolver."""

from uuid import UUID

from agentic_eval.core_evals.run_prompt.litellm_models import LiteLLMModelManager

from evaluations.decision_models import CONNECTION_LABELS, model_groups
from evaluations.types import DecisionModelGroup


def get_decision_catalog(
    *,
    organization_id: UUID | str | None,
    workspace_id: UUID | str | None,
    search: str = "",
) -> list[DecisionModelGroup]:
    if organization_id is None:
        return []
    manager = LiteLLMModelManager("")
    workspace_id = workspace_id or manager._resolve_default_workspace_id(
        organization_id
    )
    configured = set()
    # Four fixed connections, not one query per model. Only presence is exposed.
    for provider in CONNECTION_LABELS:
        entry = manager._find_api_key_entry(organization_id, workspace_id, provider)
        if (
            entry is not None
            and isinstance(entry.actual_key, str)
            and entry.actual_key.strip()
        ):
            configured.add(provider)
    groups = model_groups(configured)
    search = search.strip().lower()
    if search:
        groups = [
            group
            for group in groups
            if search
            in " ".join(
                [
                    group["model_name"],
                    group["label"],
                    group["author"],
                    *(connection["label"] for connection in group["connections"]),
                ]
            ).lower()
        ]
    # Models the caller can run now come first. Otherwise keep the catalog's
    # curated order (established decision families before newer or niche
    # ones); sorted() is stable, so only readiness moves a group.
    return sorted(
        groups,
        key=lambda group: (
            not any(connection["is_available"] for connection in group["connections"])
        ),
    )
