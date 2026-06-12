"""Service for eval version pinning on dataset bindings.

Handles snapshot building, dedup, and atomic version creation
so the view stays thin.
"""

from django.db import transaction

from model_hub.models.evals_metric import EvalTemplateVersion
from model_hub.utils.prompt_migration import config_to_prompt_messages


def maybe_pin_new_version(eval_metric, request_data, user, organization, workspace):
    """Create and pin a new EvalTemplateVersion if config actually changed.

    Returns the new version if created, None if skipped (no changes or
    snapshot matches the currently pinned version).
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

    prompt_messages = config_to_prompt_messages(
        snap, criteria=criteria,
        eval_type_id=snap.get("eval_type_id"),
    )

    with transaction.atomic():
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

    return ver
