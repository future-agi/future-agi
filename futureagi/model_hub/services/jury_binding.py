"""Validate dataset jury mappings before the generic binding writers persist."""

from copy import deepcopy
from uuid import UUID

from django.db.models import Q
from rest_framework.exceptions import ValidationError

from model_hub.models.choices import OwnerChoices
from model_hub.models.develop_dataset import Column
from model_hub.models.evals_metric import EvalTemplate
from model_hub.services.jury_versions import (
    jury_definition,
    require_jury_scope,
    select_composite_version,
)


def jury_input_keys(definition):
    seats = (definition or {}).get("seats", [])
    return [
        key
        for key, needed in (
            ("steps", any(seat.get("level") == "step" for seat in seats)),
            ("checks", any(seat.get("kind") == "check" for seat in seats)),
        )
        if needed
    ]


def prepare_jury_binding(request, dataset, payload, metric=None):
    org = getattr(request, "organization", None) or request.user.organization
    workspace = getattr(request, "workspace", None)
    try:
        template_id = UUID(
            str(metric.template_id if metric else payload.get("template_id"))
        )
    except (ValueError, TypeError):
        return
    template = (
        metric.template
        if metric
        else EvalTemplate.no_workspace_objects.filter(
            Q(owner=OwnerChoices.SYSTEM.value)
            | Q(organization=org, workspace=workspace)
            | Q(organization=org, workspace__isnull=True),
            id=template_id,
            deleted=False,
        ).first()
    )
    if template is None or template.template_type != "composite":
        return
    config = deepcopy(payload.get("config", (metric.config if metric else {}) or {}))
    if (
        not isinstance(config, dict)
        or not isinstance(config.get("config", {}), dict)
        or not isinstance(config.get("mapping", {}), dict)
    ):
        raise ValidationError("Jury configuration and mapping must be objects.")
    nested = config.setdefault("config", {})
    stored_pin = (
        (metric.config or {}).get("config", {}).get("jury_version_id")
        if metric
        else None
    )
    # Keep this binding-scoped selector until the generic metric writer also
    # persists pinned_version_id. A persisted model pin takes precedence at run time.
    version_id = payload.get(
        "pinned_version_id",
        nested.get(
            "jury_version_id",
            metric.pinned_version_id or stored_pin if metric else None,
        ),
    )
    version, uses_jury = select_composite_version(template, version_id)
    if not uses_jury:
        return
    definition = jury_definition(template, version)
    require_jury_scope(template, org, workspace)
    if payload.get("save_as_template"):
        raise ValidationError(
            "Duplicate a composite in the evaluation editor before binding its jury."
        )
    if version_id:
        nested["jury_version_id"] = str(version.id)
    else:
        nested.pop("jury_version_id", None)
    if metric is not None:
        metric.pinned_version = version if version_id else None
    mapping = config.get("mapping") or {}
    for key in jury_input_keys(definition):
        value = mapping.get(key)
        try:
            column_id = UUID(str(value))
        except (ValueError, TypeError):
            raise ValidationError(
                f"Map {key} to one JSON-array column in this dataset."
            ) from None
        if not Column.objects.filter(
            id=column_id,
            dataset=dataset,
            dataset__organization=org,
            dataset__workspace=dataset.workspace,
            deleted=False,
        ).exists():
            raise ValidationError(f"The {key} column is not in this dataset.")
    payload["config"] = config
