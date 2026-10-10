"""Save-time Jev validation with a CE-safe, lazy EE boundary."""

from rest_framework.response import Response

from tfc.ee_gates import is_jev_model, jev_gate_for_template
from tfc.utils.api_errors import build_error_envelope


def validate_jev_template_save(
    config,
    *,
    model,
    previous_model=None,
    mapping_provided=False,
    choice_scores=None,
    multi_choice=False,
):
    jev_prefix = isinstance(model, str) and model.lower().startswith("jev-")
    if jev_prefix and (not is_jev_model(model) or model != previous_model):
        gate = jev_gate_for_template(model)
        if gate is not None:
            return gate
    if not is_jev_model(model) and not mapping_provided:
        return None
    try:
        from ee.jev.mapping import JevMappingError, validate_jev_mapping
    except ImportError:
        return Response(
            build_error_envelope(
                "Jev models are not available in this deployment.",
                status_code=402,
                code="ENTITLEMENT_DENIED",
                extra={"feature": "jev"},
            ),
            status=402,
        )
    try:
        mapping = validate_jev_mapping(
            None,
            output=config.get("output", "Pass/Fail"),
            choice_scores=choice_scores,
            multi_choice=multi_choice,
            config=config,
        )
    except JevMappingError as exc:
        return Response(
            build_error_envelope(
                exc.detail,
                code="JEV_MAPPING_INVALID",
                details=exc.details,
                extra={"feature": "jev"},
            ),
            status=400,
        )
    config["jev_mapping"] = mapping.to_dict()
    return None


def validate_jev_binding(template, *, model, runtime_config=None):
    """Reject incompatible effective bindings before a view queues work."""
    from copy import deepcopy

    runtime = runtime_config or {}
    overrides = runtime.get("run_config") or {}
    effective_model = (
        overrides.get("model")
        or runtime.get("model")
        or model
        or (template.config or {}).get("model")
        or template.model
    )
    gate = jev_gate_for_template(effective_model)
    if gate is not None:
        return gate
    if not is_jev_model(effective_model):
        return None
    config = deepcopy(template.config or {})
    config.update(runtime.get("config") or {})
    config.update({key: value for key, value in overrides.items() if value is not None})
    if "output_type" in overrides:
        config["output"] = overrides["output_type"]
    return validate_jev_template_save(
        config,
        model=effective_model,
        previous_model=effective_model,
        choice_scores=overrides.get("choice_scores", template.choice_scores),
        multi_choice=bool(template.multi_choice or config.get("multi_choice")),
    )
