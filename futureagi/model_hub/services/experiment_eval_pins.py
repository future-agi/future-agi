"""Eval version pins for V2 experiment evals.

An experiment eval can run one version of its eval template instead of the
template's default. The pin is stored on ``UserEvalMetric.pinned_version``,
which every runner already honours through
``EvalTemplateVersion.objects.resolve_for_metric``.

A V2 eval entry states its pin in one of two ways:

* ``pinned_version_id``: an ``EvalTemplateVersion`` id; ``null`` clears the pin
  (unless ``config.version_pin`` names one).
* ``config.version_pin``: what the eval card shows, ``"v<n>"`` (version number
  ``n``), ``"latest"`` (the newest live version when the experiment is saved;
  stored as that concrete ``"v<n>"``, so the pin does not move when a newer
  version is saved later) or a version id.

An entry that names neither runs the template's default version, on create
and on edit alike: an edit's entry replaces the stored eval config, so the pin
lives exactly as long as the config names it.

A pin that names no live version of the template, or a version the caller's
organization cannot see, is refused with ``ValueError`` (the views answer 400).
It is never dropped, so a card never claims a version the run does not use.
"""

from __future__ import annotations

import re
import uuid

from model_hub.models.choices import OwnerChoices
from model_hub.models.evals_metric import EvalTemplate, EvalTemplateVersion

# The entry names no version (create and edit both run the template default).
PIN_UNSET = object()

_VERSION_LABEL = re.compile(r"^[vV](\d+)$")


def _organization_id(organization):
    return getattr(organization, "id", organization)


def visible_template(template_id, organization):
    """The eval template with ``template_id`` if ``organization`` may use it.

    The rule of the eval versions list: system templates are visible to
    everyone; a user template that belongs to an organization is visible only
    to that organization. Returns ``None`` otherwise, so callers answer "not
    found" and never reveal another organization's template.
    """
    template = EvalTemplate.no_workspace_objects.filter(id=template_id).first()
    if template is None:
        return None
    org_id = _organization_id(organization)
    if (
        template.owner == OwnerChoices.USER.value
        and template.organization_id
        and str(template.organization_id) != str(org_id)
    ):
        return None
    return template


def _live_versions(template):
    # Versions are scoped by their template, which the caller has checked is
    # visible (as the versions list does). A version's own organization is not
    # a boundary: the save signals stamp a system template's versions with
    # whichever organization's request created them.
    return list(
        EvalTemplateVersion.objects.filter(eval_template=template, deleted=False)
    )


def _as_uuid(value):
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        return None


def _resolve_label(template, label):
    versions = _live_versions(template)
    text = str(label).strip()
    if text.lower() == "latest":
        if not versions:
            raise ValueError(
                f"Eval '{template.name}' has no saved versions to pin as latest."
            )
        return max(versions, key=lambda v: v.version_number)
    match = _VERSION_LABEL.match(text)
    if match:
        number = int(match.group(1))
        for version in versions:
            if version.version_number == number:
                return version
        raise ValueError(f"Eval '{template.name}' has no version V{number} to pin.")
    version_id = _as_uuid(text)
    if version_id is not None:
        return _resolve_id(template, version_id)
    raise ValueError(
        f"Version pin '{text}' is not a version of eval '{template.name}' "
        "(use v<number>, latest or a version id)."
    )


def _resolve_id(template, version_id):
    for version in _live_versions(template):
        if str(version.id) == str(version_id):
            return version
    raise ValueError(
        f"Selected version is not a saved version of eval '{template.name}'."
    )


def resolve_eval_pin(template, entry):
    """The version an eval entry pins, ``None`` for no pin, or ``PIN_UNSET``.

    ``template`` must be one the caller's organization can use (see
    ``visible_template``). ``pinned_version_id`` and ``config.version_pin``
    must agree when both name a version. ``PIN_UNSET`` means the entry states
    nothing about its pin.
    """
    config = entry.get("config") if isinstance(entry, dict) else None
    label = config.get("version_pin") if isinstance(config, dict) else None
    label = label if label not in (None, "") else None
    has_explicit = isinstance(entry, dict) and "pinned_version_id" in entry
    explicit = entry.get("pinned_version_id") if has_explicit else None

    by_id = _resolve_id(template, explicit) if explicit is not None else None
    by_label = _resolve_label(template, label) if label is not None else None
    if by_id is not None and by_label is not None and by_id.id != by_label.id:
        raise ValueError(
            f"Eval '{template.name}' names two different versions "
            f"(pinned_version_id V{by_id.version_number}, "
            f"version_pin V{by_label.version_number})."
        )
    if by_id is not None:
        return by_id
    if by_label is not None:
        return by_label
    if has_explicit:
        return None
    return PIN_UNSET


def pinned_config(config, pin):
    """``config`` with ``version_pin`` naming the stored pin as ``v<n>``.

    A ``"latest"`` pin is stored as the concrete version it resolved to, so
    the saved card and the run name the same version. Other configs are
    returned unchanged.
    """
    if pin is PIN_UNSET or pin is None:
        return config
    return {**(config or {}), "version_pin": f"v{pin.version_number}"}


def resolve_eval_entries(
    entries, organization, *, accessible_templates=None, existing_metrics=None
):
    """Resolve all templates and pins before writes, using the caller's scope.

    Experiment callers pass their organization/workspace-accessible queryset.
    New or changed templates, and changed pins, require current access. An
    existing metric's unchanged template and pin are retained even after access
    is revoked; that template is read only through the stored metric's own FK.
    An absent pin means the template default, including on updates. Every pin
    must belong to the template it resolved, including a retained stored pin.
    """
    existing_metrics = existing_metrics or {}
    resolved = []
    for entry in entries:
        metric = existing_metrics.get(str(entry.get("id")))
        if metric is not None and str(metric.template_id) == str(entry["template_id"]):
            # This is the metric's own stored reference, never a request lookup.
            stored_template = metric.template
            pin = resolve_eval_pin(stored_template, entry)
            pin = None if pin is PIN_UNSET else pin
            if str(getattr(pin, "id", None)) == str(metric.pinned_version_id):
                stored_pin = metric.pinned_version
                if (
                    stored_pin is not None
                    and stored_pin.eval_template_id != stored_template.id
                ):
                    raise ValueError(
                        "Selected version does not belong to the eval template."
                    )
                resolved.append((stored_template, stored_pin))
                continue

        template = (
            accessible_templates.filter(id=entry["template_id"]).first()
            if accessible_templates is not None
            else visible_template(entry["template_id"], organization)
        )
        if template is None:
            raise ValueError("Eval template not found.")
        resolved.append((template, resolve_eval_pin(template, entry)))
    return resolved
