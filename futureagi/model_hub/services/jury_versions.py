"""Immutable composite jury definitions and scoped version resolution."""

from copy import copy, deepcopy
from uuid import UUID

from django.db.models import Q
from rest_framework.exceptions import ValidationError

from model_hub.models.choices import OwnerChoices
from model_hub.models.evals_metric import (
    CompositeEvalChild,
    EvalTemplate,
    EvalTemplateVersion,
)


def snapshot_jury(template, snapshot, restore=False):
    """Record the live jury and children with a composite version saved now.

    Every save is recorded this way, even one whose snapshot equals a saved
    version's (a metadata-only composite edit rebuilds the same structural
    snapshot). Only an explicit restore (``restore=True``) copies a saved
    version's snapshot as given: that history keeps its own jury, or its
    absence, and is never enriched with the live template's configuration.
    """
    if restore or template.template_type != "composite":
        return snapshot
    result = deepcopy(snapshot or {})
    result.setdefault("jury", deepcopy((template.config or {}).get("jury")))
    for key in ("aggregation_enabled", "aggregation_function", "composite_child_axis"):
        result.setdefault(key, getattr(template, key))
    result.setdefault(
        "children",
        [
            {
                "child_id": str(link.child_id),
                "order": link.order,
                "weight": link.weight,
                "config": deepcopy(link.config or {}),
                "pinned_version_id": str(link.pinned_version_id)
                if link.pinned_version_id
                else None,
            }
            for link in CompositeEvalChild.objects.filter(
                parent=template, deleted=False
            ).order_by("order")
        ],
    )
    return result


def require_jury_scope(template, org, workspace):
    if (
        template.owner != OwnerChoices.USER.value
        or not template.workspace_id
        or template.organization_id != org.id
        or not workspace
        or template.workspace_id != workspace.id
    ):
        raise ValidationError(
            {
                "detail": "Juries require an owned composite in the active workspace. System and legacy unscoped composites are not supported."
            }
        )


def selected_jury_version(template, version_id):
    if not version_id:
        return EvalTemplateVersion.objects.get_default(template)
    from rest_framework import serializers

    versions = EvalTemplateVersion.objects.filter(eval_template=template, deleted=False)
    if version_id == "latest":
        version = versions.order_by("-version_number").first()
    else:
        version_id = serializers.UUIDField().run_validation(version_id)
        version = versions.filter(id=version_id).first()
    if version is None:
        raise ValidationError("Selected jury version is not available.")
    return version


def jury_definition(template, version):
    """The jury a composite runs with the selected saved version.

    A snapshot that holds the jury key is the saved definition; an explicit
    empty value is an ordinary version. A snapshot saved before juries were
    versioned has no jury key: the live template's jury is the one that ran
    with it (the jury save changed only the live configuration), so it applies.
    """
    snapshot = (version.config_snapshot or {}) if version else {}
    if "jury" in snapshot:
        return snapshot["jury"]
    return (template.config or {}).get("jury")


def select_composite_version(template, version_id):
    """The selected saved version, and whether the composite runs a jury.

    Only a jury needs strict selection, which refuses an unavailable version.
    An ordinary composite keeps the shared pin rule
    (EvalTemplateVersionManager.resolve_for_metric): a missing or soft-deleted
    selection falls back to the template default and never stops its run.
    The unavailable selection itself is classified too: a pin to a saved jury
    stays refused after it is soft-deleted, even when the live configuration
    and the default version are now ordinary.
    """
    live_jury = (template.config or {}).get("jury")
    try:
        version = selected_jury_version(template, version_id)
    except ValidationError:
        fallback = EvalTemplateVersion.objects.get_default(template)
        if (
            live_jury
            or jury_definition(template, fallback)
            or _selects_saved_jury(template, version_id)
        ):
            raise
        return None, False
    return version, bool(live_jury or jury_definition(template, version))


def _selects_saved_jury(template, version_id):
    """Whether a selection names this template's saved jury, soft-deleted or not."""
    try:
        version_id = UUID(str(version_id))
    except (TypeError, ValueError):
        return False
    version = EvalTemplateVersion.all_objects.filter(
        eval_template=template, id=version_id
    ).first()
    return bool(version and jury_definition(template, version))


def jury_result_column_type(metric, fallback):
    """Use the binding's saved aggregation when a dataset precreates its column."""
    template = metric.template
    if template.template_type != "composite":
        return fallback
    binding_version = (metric.config or {}).get("config", {}).get("jury_version_id")
    version, uses_jury = select_composite_version(
        template, metric.pinned_version_id or binding_version
    )
    if not version or not uses_jury:
        return fallback
    from model_hub.utils.eval_result_columns import infer_eval_result_column_data_type

    historical = copy(template)
    historical.aggregation_enabled = (version.config_snapshot or {}).get(
        "aggregation_enabled", template.aggregation_enabled
    )
    return infer_eval_result_column_data_type(historical)


def resolve_jury_runtime(metric, org, workspace):
    template = metric.template
    binding_version = (metric.config or {}).get("config", {}).get("jury_version_id")
    version, uses_jury = select_composite_version(
        template, metric.pinned_version_id or binding_version
    )
    if not uses_jury:
        return template, None, None
    definition = jury_definition(template, version)
    require_jury_scope(template, org, workspace)
    parent = copy(template)
    parent.config = {**deepcopy(template.config or {}), "jury": deepcopy(definition)}
    if not version:
        return parent, None, None
    snap = version.config_snapshot or {}
    for key in ("aggregation_enabled", "aggregation_function", "composite_child_axis"):
        if key in snap:
            setattr(parent, key, snap[key])
    parent.pass_threshold = version.pass_threshold
    if "children" not in snap:
        # Saved before juries were versioned (a binding-edit version keeps the
        # live configuration only): the live children ran with it.
        return parent, None, version
    links = []
    for saved in snap.get("children", []):
        child = EvalTemplate.no_workspace_objects.filter(
            Q(owner=OwnerChoices.SYSTEM.value)
            | Q(organization=org, workspace=workspace),
            id=saved["child_id"],
            deleted=False,
        ).first()
        if child is None:
            raise ValidationError("A saved jury child is no longer available.")
        pin = None
        if saved.get("pinned_version_id"):
            pin = selected_jury_version(child, saved["pinned_version_id"])
        links.append(
            CompositeEvalChild(
                parent=parent,
                child=child,
                order=saved["order"],
                weight=saved["weight"],
                config=deepcopy(saved.get("config", {})),
                pinned_version=pin,
            )
        )
    return parent, links, version
