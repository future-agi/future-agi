"""Composite eval execution for the simulation runners.

Both runners call run_eval_func, which needs a concrete evaluator class. A
composite parent has none, its children do, so composites are executed
through the shared composite helper instead. Kept in one module so the two
runners cannot drift on it.
"""

import structlog

logger = structlog.get_logger(__name__)


def run_composite_eval(
    eval_template,
    eval_config,
    resolved_version,
    updated_mapping,
    organization,
    workspace,
    call_context=None,
    source="composite_eval_simulate",
):
    """Execute a composite eval and return a run_eval_func-shaped result.

    Composite parents carry no eval_type_id, so run_eval_func cannot build an
    evaluator for them; children are executed and aggregated instead. Child
    weights, per-child config and per-child pins all come from the pinned
    version's snapshot, which is where a simulation binding records them
    (there is no per-binding overrides column).
    """
    from model_hub.models.evals_metric import CompositeEvalChild
    from simulate.utils.eval_summary import derive_kpi_output_type
    from model_hub.utils.composite_execution import execute_composite_children_sync

    child_links = list(
        CompositeEvalChild.objects.filter(parent=eval_template, deleted=False)
        .select_related("child", "pinned_version")
        .order_by("order")
    )
    if not child_links:
        raise ValueError(
            f"Composite eval '{eval_template.name}' has no children to run."
        )

    weight_overrides = None
    snapshot_children = (
        (resolved_version.config_snapshot or {}).get("children")
        if resolved_version
        else None
    )
    if isinstance(snapshot_children, list):
        by_child = {
            str(c["child_id"]): c
            for c in snapshot_children
            if isinstance(c, dict) and c.get("child_id")
        }
        weight_overrides = {
            cid: c["weight"]
            for cid, c in by_child.items()
            if c.get("weight") is not None
        } or None
        _apply_snapshot_to_links(child_links, by_child)

    binding_config = eval_config.config or {}
    runtime_config = {k: v for k, v in binding_config.items() if k != "mapping"}

    outcome = execute_composite_children_sync(
        parent=eval_template,
        child_links=child_links,
        mapping=updated_mapping,
        config=runtime_config,
        org=organization,
        workspace=workspace,
        model=eval_config.model or None,
        call_context=call_context,
        error_localizer=bool(eval_config.error_localizer),
        source=source,
        weight_overrides=weight_overrides,
    )

    # Every other simulate result derives this from the template, so a
    # pass/fail composite reports Pass/Fail rather than always a score.
    return {
        "output": outcome.aggregate_score,
        "reason": outcome.summary or "",
        "output_type": derive_kpi_output_type(eval_template),
        # Same drill-down payload the dataset path writes into Cell.value_infos,
        # so a composite result in simulation can show its children rather than
        # a bare aggregate.
        "composite": {
            "composite_id": str(eval_template.id),
            "aggregation_enabled": eval_template.aggregation_enabled,
            "aggregation_function": eval_template.aggregation_function,
            "aggregate_score": outcome.aggregate_score,
            "aggregate_pass": outcome.aggregate_pass,
            "summary": outcome.summary,
            "children": [cr.model_dump() for cr in outcome.child_results],
        },
    }


def _apply_snapshot_to_links(child_links, snapshot_by_child):
    """Overlay the parent version's per-child config and pins onto the links.

    The snapshot records each child's `config` and `pinned_version_id`
    alongside its weight, which is the whole point of versioning a composite
    per binding: two simulations can pin different child prompts off the same
    template. Only the weight was being applied, so the other two silently ran
    whatever the shared template links currently hold.

    The links are in-memory instances that are never saved, so mutating them
    is the overlay.
    """
    from model_hub.models.evals_metric import EvalTemplateVersion

    wanted_version_ids = {
        snap["pinned_version_id"]
        for link in child_links
        for snap in [snapshot_by_child.get(str(link.child_id))]
        if snap and snap.get("pinned_version_id")
    }
    versions_by_id = (
        {
            str(v.id): v
            for v in EvalTemplateVersion.objects.filter(
                id__in=wanted_version_ids, deleted=False
            )
        }
        if wanted_version_ids
        else {}
    )

    for link in child_links:
        snap = snapshot_by_child.get(str(link.child_id))
        if not snap:
            continue
        if isinstance(snap.get("config"), dict) and snap["config"]:
            link.config = snap["config"]
        pinned_id = snap.get("pinned_version_id")
        if pinned_id:
            version = versions_by_id.get(str(pinned_id))
            if version is not None:
                link.pinned_version = version
            else:
                logger.warning(
                    "composite_child_pin_missing",
                    child_id=str(link.child_id),
                    pinned_version_id=str(pinned_id),
                )
