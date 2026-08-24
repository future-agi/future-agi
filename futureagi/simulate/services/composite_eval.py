"""Composite eval execution for the simulation runners.

Both runners call run_eval_func, which needs a concrete evaluator class. A
composite parent has none — its children do — so composites are executed
through the shared composite helper instead. Kept in one module so the two
runners cannot drift on it.
"""
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
    evaluator for them — children are executed and aggregated instead. Child
    weights come from the pinned version's snapshot, which is where a
    simulation binding records them (there is no per-binding overrides column).
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
        weight_overrides = {
            str(c["child_id"]): c["weight"]
            for c in snapshot_children
            if isinstance(c, dict) and c.get("child_id") and c.get("weight") is not None
        } or None

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
