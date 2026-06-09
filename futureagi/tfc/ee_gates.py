"""Runtime EE feature gates for non-URL-bound entry points.

Use these where URL-level gating can't distinguish free vs paid traffic
(e.g. an endpoint that works for any model, but only paid models route
through EE code paths)."""

from __future__ import annotations

from rest_framework.response import Response

_TURING_MODELS = frozenset(
    {
        "turing_large",
        "turing_large_xl",
        "turing_small",
        "turing_flash",
        "protect",
        "protect_flash",
    }
)


def is_turing_model(model_name: object) -> bool:
    if not model_name:
        return False
    return str(model_name).lower() in _TURING_MODELS


def voice_sim_oss_gate_response() -> Response | None:
    """Return a 402 response if the deployment is OSS (ee/ stripped or
    `CLOUD_DEPLOYMENT`/`EE_LICENSE_KEY` unset), else None.

    Voice simulation requires livekit/vapi integration and the `ee.voice`
    module. Use at the top of any view that starts or re-runs a voice
    call."""
    try:
        from ee.usage.deployment import DeploymentMode

        if not DeploymentMode.is_oss():
            return None
    except ImportError:
        pass  # ee.usage absent → treat as OSS

    return Response(
        {
            "error": (
                "Voice simulation is not available on OSS. "
                "Upgrade to cloud or enterprise to run voice calls."
            ),
            "upgrade_required": True,
            "feature": "voice_sim",
        },
        status=402,
    )


def _is_oss() -> bool:
    """True when the deployment is OSS (ee/ stripped or DeploymentMode.is_oss)."""
    try:
        from ee.usage.deployment import DeploymentMode

        return DeploymentMode.is_oss()
    except ImportError:
        return True  # ee.usage absent → OSS


def strip_turing_from_config_options(
    config_params_option: dict | None,
) -> dict:
    """When running OSS, drop Turing/Protect models from the `model` option
    list so the frontend dropdown never offers a model the gate would 402.

    Returns a copy; the original dict is not mutated. No-op on cloud."""
    if not config_params_option:
        return config_params_option or {}
    if not _is_oss():
        return config_params_option

    model_options = config_params_option.get("model")
    if not isinstance(model_options, list):
        return config_params_option

    filtered = [m for m in model_options if not is_turing_model(m)]
    return {**config_params_option, "model": filtered}


def turing_oss_gate_for_template(
    model_name: object,
    template_id: object = None,
    eval_type: object = None,
) -> Response | None:
    """Variant of `turing_oss_gate_response` that skips the gate for code
    eval templates. Code evals execute Python/JS — the model field is
    irrelevant for them, so we shouldn't 402 when the frontend leaves the
    model defaulted to a Turing value.

    Pass `eval_type` directly when the caller already knows it (avoids a
    DB lookup); otherwise we resolve it from `template_id`."""
    if eval_type and str(eval_type).lower() == "code":
        return None

    if template_id:
        try:
            from model_hub.models.evals_metric import EvalTemplate

            tpl = (
                EvalTemplate.no_workspace_objects.filter(
                    id=template_id, deleted=False
                )
                .only("eval_type")
                .first()
            )
            if tpl is not None and tpl.eval_type == "code":
                return None
        except Exception:
            pass  # fall through to normal gate

    return turing_oss_gate_response(model_name)


def turing_oss_gate_response(model_name: object) -> Response | None:
    """Return a 402 response if the model is a Turing/Protect model AND
    the deployment is OSS. Return None otherwise so the caller proceeds.

    Use at the top of any view that accepts a model selection and would
    otherwise route into ee/turing code."""
    if not is_turing_model(model_name):
        return None

    try:
        from ee.usage.deployment import DeploymentMode

        if not DeploymentMode.is_oss():
            return None
    except ImportError:
        pass  # ee.usage absent → treat as OSS

    return Response(
        {
            "error": (
                "Turing and Protect models are not available on OSS. "
                "Select a different model (OpenAI, Anthropic, etc.) "
                "or upgrade your plan."
            ),
            "upgrade_required": True,
            "feature": "turing",
        },
        status=402,
    )
