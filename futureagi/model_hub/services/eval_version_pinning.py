"""Service for eval version pinning on dataset bindings.

Handles snapshot building, dedup, and atomic version creation
so the view stays thin.
"""

import json

from model_hub.models.evals_metric import EvalTemplateVersion
from model_hub.utils.eval_prompt_variables import sync_required_keys_from_prompt
from model_hub.utils.prompt_migration import config_to_prompt_messages


def maybe_pin_new_version(eval_metric, request_data, user, organization, workspace):
    """Create and pin a new EvalTemplateVersion if config actually changed.

    Mutates eval_metric.pinned_version in place. The caller is responsible
    for persisting eval_metric via save().
    """
    from model_hub.models.choices import OwnerChoices

    # Jury definitions are versioned in the composite editor, not on mapping edits.
    if eval_metric.template.template_type == "composite" and (
        (eval_metric.template.config or {}).get("jury")
        or (getattr(eval_metric.pinned_version, "config_snapshot", None) or {}).get(
            "jury"
        )
    ):
        return None

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
    selected = eval_metric.pinned_version
    # A runtime edit of a historical binding branches from that definition, not
    # the live template. Also avoid importing live-only keys into a complete FE
    # snapshot. A cloned template must not inherit the old template's pin.
    if selected and selected.eval_template_id != tpl.id:
        selected = None
    resolved_model = (
        request_data.get("model")
        or eval_metric.model
        or (selected.model if selected else tpl.model)
        or ""
    )

    # Selected definition → FE config → run_config → top-level fields.
    base_config = (selected.config_snapshot if selected else tpl.config) or {}
    snap = dict(base_config)
    if inner_config:
        snap.update(inner_config)
    if run_config:
        snap.update(run_config)
    snap["model"] = resolved_model

    weight_overrides = request_data.get("composite_weight_overrides")
    if weight_overrides is not None:
        snap["composite_weight_overrides"] = weight_overrides

    rule_prompt = snap.get("rule_prompt")
    criteria = rule_prompt or (selected.criteria if selected else tpl.criteria) or ""
    prompt_edited = "rule_prompt" in inner_config and inner_config[
        "rule_prompt"
    ] != base_config.get("rule_prompt")
    if rule_prompt and (
        not snap.get("messages")
        or (prompt_edited and snap.get("messages") == base_config.get("messages"))
    ):
        # Keep the selected multi-turn prompt on runtime-only edits; regenerate
        # a system message only for a changed prompt without edited messages.
        snap["messages"] = [{"role": "system", "content": rule_prompt}]

    sync_required_keys_from_prompt(snap, mapping=mapping)

    # Dedup: skip if the full canonical snapshot matches the pinned version.
    # Sorting keys ensures stable comparison regardless of insertion order.
    current_pinned = eval_metric.pinned_version
    if current_pinned:
        new_snap_json = json.dumps(snap, sort_keys=True, default=str)
        # hook(F68): create_version stores a composite snapshot with the live
        # jury and children (snapshot_jury), so an identical edit is compared
        # in that stored form too; a version saved before juries holds the
        # snapshot as built.
        from model_hub.services.jury_versions import snapshot_jury

        stored_snap_json = json.dumps(
            snapshot_jury(tpl, snap), sort_keys=True, default=str
        )
        old_snap_json = json.dumps(
            current_pinned.config_snapshot or {}, sort_keys=True, default=str
        )
        if old_snap_json in (new_snap_json, stored_snap_json):
            return None

    prompt_messages = config_to_prompt_messages(
        snap,
        criteria=criteria,
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
