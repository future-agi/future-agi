"""Bridge registration for PromptLabelViewSet."""

from ai_tools.drf_bridge import expose_to_mcp
from model_hub.views.prompt_labels import PromptLabelViewSet

expose_to_mcp(category="prompts")(PromptLabelViewSet)


# ---------------------------------------------------------------------------
# Phase 3A — destructive @actions (confirmation-gated; see PHASES.md 3A and
# ai_tools/confirmations.py) plus the paired apply-label action (mutate)
# needed for one-click Undo.
# ---------------------------------------------------------------------------


def _preview_remove_prompt_label_from_version(params: dict, context) -> str:
    from model_hub.models.prompt_label import PromptLabel
    from model_hub.models.run_prompt import PromptVersion

    label = (
        PromptLabel.no_workspace_objects.filter(id=params.get("label_id"))
        .only("id", "name")
        .first()
    )
    version = (
        PromptVersion.objects.filter(id=params.get("version_id"))
        .only("id", "template_version", "original_template")
        .select_related("original_template")
        .first()
    )
    label_name = label.name if label else f"`{params.get('label_id')}` (not found)"
    if version:
        version_name = (
            f"version '{version.template_version}' of prompt "
            f"'{version.original_template.name}'"
        )
    else:
        version_name = f"version `{params.get('version_id')}` (not found)"
    return (
        f"Will detach label **'{label_name}'** from {version_name}. "
        "Traffic/lookups pinned to this label stop resolving to this "
        "version.\n\n"
        "Undo: re-apply it with `assign_prompt_labels_to_version`."
    )


expose_to_mcp(
    category="prompts",
    tools={
        # remove_label_from_version reads raw request.data (label_id +
        # version_id) — no serializer, so declare the body via query_params.
        "remove_label_from_version": {
            "name": "remove_prompt_label_from_version",
            "entity": "prompt label",
            "execution_policy": "destructive",
            "confirm_preview": _preview_remove_prompt_label_from_version,
            "undo_note": (
                "Undo: re-apply the label with "
                "`assign_prompt_labels_to_version`."
            ),
            "undo_prompt": (
                "Re-apply the prompt label I just removed: call "
                "assign_prompt_labels_to_version with "
                'template_version_id="{version_id}" and '
                'label_ids=["{label_id}"].'
            ),
            "query_params": {
                "label_id": {
                    "type": str,
                    "required": True,
                    "description": (
                        "UUID of the prompt label to detach (from "
                        "`list_prompt_labels`)."
                    ),
                },
                "version_id": {
                    "type": str,
                    "required": True,
                    "description": (
                        "UUID of the prompt version to detach the label "
                        "from (from `list_prompt_versions`)."
                    ),
                },
            },
            "description": (
                "Detach a label from a prompt version. DESTRUCTIVE: "
                "requires user confirmation (preview first, then re-call "
                "with confirm=true)."
            ),
        },
        # Paired apply action (mutate) — also the Undo target above.
        "assign_multiple_labels": {
            "name": "assign_prompt_labels_to_version",
            "entity": "prompt label",
            "execution_policy": "mutate",
            "query_params": {
                "template_version_id": {
                    "type": str,
                    "required": True,
                    "description": (
                        "UUID of the prompt version to label (from "
                        "`list_prompt_versions`)."
                    ),
                },
                "label_ids": {
                    "type": list[str],
                    "required": True,
                    "description": (
                        "List of prompt label UUIDs to apply (from "
                        "`list_prompt_labels`)."
                    ),
                },
            },
            "description": (
                "Apply one or more labels to a prompt version (e.g. "
                "Production/Staging/Development or custom labels). Moving a "
                "single-assignment label re-points it to this version."
            ),
        },
    },
)(PromptLabelViewSet)
