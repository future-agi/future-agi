"""Execute jury seats using the ordinary evaluator and retain their exact verdicts."""

import copy
import json
import math

from rest_framework.exceptions import ValidationError

from evaluations.engine.instance import resolve_pass_threshold
from model_hub.models.choices import OwnerChoices
from model_hub.serializers.composite_jury import JuryDefinitionSerializer
from model_hub.types import CompositeChildResult
from model_hub.utils.composite_aggregation import aggregate_scores, aggregate_summaries

MAX_STEPS = 32
MAX_CALLS = 64


def accessible_child(child, org, workspace):
    if child.deleted or child.template_type == "composite":
        return False
    if child.owner == OwnerChoices.SYSTEM.value:
        return True
    return (
        child.organization_id == org.id
        and workspace is not None
        and child.workspace_id == workspace.id
    )


def validate_definition(definition, links, org, workspace):
    serializer = JuryDefinitionSerializer(data=definition)
    serializer.is_valid(raise_exception=True)
    links_by_child = {str(link.child_id): link for link in links if not link.deleted}
    for seat in serializer.validated_data["seats"]:
        if seat["kind"] == "check":
            continue
        link = links_by_child.get(seat["child_id"])
        if link is None or not accessible_child(link.child, org, workspace):
            raise ValidationError(
                "Jury child is not an accessible member of this composite."
            )
        if link.pinned_version_id and (
            link.pinned_version.eval_template_id != link.child_id
            or link.pinned_version.deleted
        ):
            raise ValidationError("Jury child version is no longer available.")
    return dict(serializer.validated_data)


def _step_inputs(mapping, seat):
    if seat.get("level", "row") == "row":
        return [mapping]
    steps = mapping.get("steps", [])
    if not isinstance(steps, list) or len(steps) > MAX_STEPS:
        raise ValidationError("Steps must be a list of at most 32 entries.")
    inputs = []
    for step in steps:
        if not isinstance(step, dict) or step.get("kind") not in ("llm", "tool"):
            raise ValidationError("Each step must declare an llm or tool kind.")
        if not isinstance(step.get("mapping"), dict):
            raise ValidationError("Each step requires a mapping object.")
        if step["kind"] == seat["step_kind"]:
            inputs.append(
                {
                    **{k: v for k, v in mapping.items() if k != "steps"},
                    **step["mapping"],
                }
            )
    return inputs


def _check_result(mapping, seat, order):
    checks = mapping.get("checks", [])
    matches = (
        [
            item
            for item in checks
            if isinstance(item, dict) and item.get("key") == seat["check_key"]
        ]
        if isinstance(checks, list)
        else []
    )
    passed = matches[0].get("passed") if len(matches) == 1 else None
    valid = isinstance(passed, bool)
    return CompositeChildResult(
        child_id=f"check:{seat['check_key']}",
        child_name=seat["check_key"],
        order=order,
        weight=seat["weight"],
        score=float(passed) if valid else None,
        output=passed if valid else None,
        status="completed" if valid else "failed",
        error=None if valid else "One boolean check result is required.",
    )


def execute_jury(*, parent, child_links, mapping, org, workspace, **context):
    from model_hub.services.jury_versions import require_jury_scope
    from model_hub.utils.composite_execution import CompositeRunOutcome, _execute_child

    require_jury_scope(parent, org, workspace)
    definition = validate_definition(parent.config["jury"], child_links, org, workspace)
    if (
        not isinstance(mapping, dict)
        or len(json.dumps(mapping, allow_nan=False)) > 65536
    ):
        raise ValidationError("Jury inputs must be an object of at most 64 KiB.")
    links = {str(link.child_id): link for link in child_links}
    inputs = {
        seat["id"]: _step_inputs(mapping, seat)
        for seat in definition["seats"]
        if seat["kind"] == "eval"
    }
    if sum(len(values) for values in inputs.values()) > MAX_CALLS:
        raise ValidationError("Jury exceeds the 64-call limit.")
    results, snapshots, thresholds = [], [], []
    for order, seat in enumerate(definition["seats"]):
        threshold = 0.5
        step_results = []
        if seat["kind"] == "check":
            result = _check_result(mapping, seat, order)
        else:
            link = copy.copy(links[seat["child_id"]])
            link.order = order
            link.config = copy.deepcopy(link.config or {})
            if seat.get("model"):
                link.config["run_config"] = {
                    **link.config.get("run_config", {}),
                    "model": seat["model"],
                }
            threshold = resolve_pass_threshold(
                eval_template=link.child,
                runtime_config=link.config,
                resolved_version=link.pinned_version,
            )
            for values in inputs[seat["id"]]:
                step_results.append(
                    _execute_child(
                        child_template=link.child,
                        link=link,
                        weight=seat["weight"],
                        mapping=values,
                        org=org,
                        workspace=workspace,
                        **context,
                    )
                )
            valid = bool(step_results) and all(
                item.status == "completed"
                and item.score is not None
                and math.isfinite(item.score)
                for item in step_results
            )
            score = None
            if valid:
                scores = [item.score for item in step_results]
                reducer = seat.get("reduce", "mean")
                if reducer == "all_pass":
                    score = float(all(value >= threshold for value in scores))
                    threshold = 0.5
                elif reducer == "min":
                    score = min(scores)
                else:
                    score = sum(scores) / len(scores)
            result = CompositeChildResult(
                child_id=seat["child_id"],
                child_name=link.child.name,
                order=order,
                weight=seat["weight"],
                score=score,
                output=score,
                status="completed" if valid else "failed",
                reason="; ".join(item.reason or "" for item in step_results),
                error=None
                if valid
                else "No matching steps or a judge did not return a numeric score.",
            )
        results.append(result)
        thresholds.append(threshold)
        snapshots.append(
            {
                **seat,
                "child_name": result.child_name,
                "score": result.score,
                "passed": result.score >= threshold
                if result.status == "completed" and result.score is not None
                else None,
                "threshold": threshold,
                "status": result.status,
                "reason": result.reason,
                "error": result.error,
                "steps": [item.model_dump() for item in step_results],
            }
        )
    # A missing or failed seat is not a passing jury of the remaining seats.
    complete = all(
        result.status == "completed" and result.score is not None for result in results
    )
    score = (
        aggregate_scores(
            [(result.score, result.weight) for result in results],
            parent.aggregation_function,
            child_thresholds=thresholds,
        )
        if complete and parent.aggregation_enabled
        else None
    )
    passed = (
        score >= (parent.pass_threshold if parent.pass_threshold is not None else 0.5)
        if score is not None
        else None
    )
    verdicts = {seat["passed"] for seat in snapshots if seat["passed"] is not None}
    report = {
        "seats": snapshots,
        "aggregate_score": score,
        "aggregate_pass": passed,
        "disagreement": len(verdicts) > 1,
        "complete": complete,
    }
    return CompositeRunOutcome(
        child_results=results,
        aggregate_score=score,
        aggregate_pass=passed,
        summary=aggregate_summaries(results),
        error_localizer_results=None,
        jury_report=report,
    )
