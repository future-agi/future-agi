"""Resolve explicit dataset binding selections before any persistent change."""

from uuid import UUID

from model_hub.models.evals_metric import EvalTemplateVersion


def selected_dataset_eval_version(template, selection):
    """A scoped template's live version, or None for its moving default."""
    if selection is None:
        return None
    versions = EvalTemplateVersion.objects.filter(eval_template=template, deleted=False)
    if selection == "latest":
        version = versions.order_by("-version_number").first()
    else:
        try:
            version_id = UUID(str(selection))
        except (TypeError, ValueError, AttributeError) as exc:
            raise ValueError("Selected version must be a UUID or latest") from exc
        version = versions.filter(id=version_id).first()
    if version is None:
        raise ValueError("Selected version not found")
    return version
