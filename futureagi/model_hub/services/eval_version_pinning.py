"""Service for eval version pinning on dataset bindings.

Handles snapshot building, dedup, and atomic version creation
so the view stays thin.
"""

import json

from model_hub.models.evals_metric import EvalTemplateVersion
from model_hub.utils.eval_prompt_variables import sync_required_keys_from_prompt
from model_hub.utils.prompt_migration import config_to_prompt_messages


def is_versioned_template(eval_template):
    """Whether this template carries versions.

    Only user-owned (custom) templates do. System templates run off the
    binding's run_config and must never hold a pin.
    """
    from model_hub.models.choices import OwnerChoices

    return (
        eval_template is not None
        and eval_template.owner == OwnerChoices.USER.value
    )


def resolve_pin_for_new_binding(eval_template, pinned_version_id=None):
    """Version to store on a binding being created or edited.

    Write path: pinned_version_id arrives on a request and is untrusted, so
    it is looked up and must belong to this template. Falls back to the
    template default. Returns None for system templates, so an explicit
    pinned_version_id is ignored there.
    """
    if not is_versioned_template(eval_template):
        return None
    if pinned_version_id:
        selected = EvalTemplateVersion.objects.filter(
            id=pinned_version_id,
            eval_template=eval_template,
            deleted=False,
        ).first()
        if selected:
            return selected
    return EvalTemplateVersion.objects.get_default(eval_template)


def resolve_version_for_binding(eval_template, pinned_version):
    """Version that will actually run for a binding.

    Read path: pinned_version is the already-loaded FK, so this issues no
    query and is safe inside the per-eval runner loop. A live pin wins; a
    soft-deleted one falls back to the template default. Returns None for
    system templates, leaving the engine on its own get_default() path.

    Mirrors EvalTemplateVersion.objects.resolve_for_metric, which cannot be
    reused directly because SimulateEvalConfig names its FK `eval_template`
    rather than `template`.
    """
    if not is_versioned_template(eval_template):
        return None
    if pinned_version is not None and not getattr(pinned_version, "deleted", False):
        return pinned_version
    return EvalTemplateVersion.objects.get_default(eval_template)


def maybe_pin_new_version(eval_metric, request_data, user, organization, workspace):
    """Create and pin a new EvalTemplateVersion if config actually changed.

    Mutates eval_metric.pinned_version in place. The caller is responsible
    for persisting eval_metric via save().
    """
    from model_hub.models.choices import OwnerChoices

    has_config_changes = bool(
        request_data.get("config")
        or request_data.get("composite_weight_overrides") is not None
    )
    if not has_config_changes:
        return None
    if eval_metric.template.owner != OwnerChoices.USER.value:
        return None

    tpl = eval_metric.template
    req_config = request_data.get("config") or {}
    inner_config = req_config.get("config", {})
    run_config = req_config.get("run_config", {})
    mapping = req_config.get("mapping") or (eval_metric.config or {}).get("mapping", {})
    resolved_model = (
        request_data.get("model") or eval_metric.model
        or tpl.model or ""
    )

    # Build snapshot: template base → FE config → run_config → top-level fields
    snap = dict(tpl.config or {})
    if inner_config:
        snap.update(inner_config)
    if run_config:
        snap.update(run_config)
    snap["model"] = resolved_model

    weight_overrides = request_data.get("composite_weight_overrides")
    if weight_overrides is not None:
        snap["composite_weight_overrides"] = weight_overrides

    rule_prompt = inner_config.get("rule_prompt")
    criteria = rule_prompt or tpl.criteria or ""
    if rule_prompt:
        snap["messages"] = [{"role": "system", "content": rule_prompt}]

    sync_required_keys_from_prompt(snap, mapping=mapping)

    # Dedup: skip if the full canonical snapshot matches the pinned version.
    # Sorting keys ensures stable comparison regardless of insertion order.
    current_pinned = eval_metric.pinned_version
    if current_pinned:
        new_snap_json = json.dumps(snap, sort_keys=True, default=str)
        old_snap_json = json.dumps(current_pinned.config_snapshot or {}, sort_keys=True, default=str)
        if new_snap_json == old_snap_json:
            return None

    prompt_messages = config_to_prompt_messages(
        snap, criteria=criteria,
        eval_type_id=snap.get("eval_type_id"),
    )

    ver = EvalTemplateVersion.objects.create_version(
        eval_template=tpl,
        prompt_messages=prompt_messages,
        config_snapshot=snap,
        criteria=criteria,
        model=resolved_model,
        user=user,
        organization=organization,
        workspace=workspace,
    )
    eval_metric.pinned_version = ver
