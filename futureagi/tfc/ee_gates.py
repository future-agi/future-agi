"""Runtime EE feature gates for non-URL-bound entry points.

Use these where URL-level gating can't distinguish free vs paid traffic
(e.g. an endpoint that works for any model, but only paid models route
through EE code paths)."""

from __future__ import annotations

from rest_framework.response import Response
from tfc.utils.api_errors import build_error_envelope

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
    """Return a 402 response when the voice-sim code isn't available in this
    build (OSS image with ee/ stripped), else None.

    Voice simulation requires the `ee.voice` module. This gate decides CODE
    availability only — license/plan entitlement is the caller's second
    layer (capability service / cloud plan resolver), and voice_sim is open
    on self-hosted deployments regardless of license state, so deployment
    mode alone must never deny here."""
    try:
        from tfc.capabilities import service as capability_service
        from tfc.licensing.types import DenialReason

        if capability_service.is_configured():
            decision = capability_service.check("voice_sim")
            if decision.reason_code != DenialReason.EE_CODE_UNAVAILABLE.value:
                # Allowed, or a license/plan concern → the caller's
                # entitlement layer decides. Only missing code denies here.
                return None
            return _voice_sim_code_unavailable_response()
    except Exception:
        pass  # capability service unusable → fall through to code probe

    # Early-startup fallback: probe the module directly.
    from tfc.ee_loader import has_ee

    if has_ee("ee.voice"):
        return None
    return _voice_sim_code_unavailable_response()


def _voice_sim_code_unavailable_response() -> Response:
    message = (
        "Voice simulation is not available on OSS. "
        "Upgrade to cloud or enterprise to run voice calls."
    )
    return Response(
        build_error_envelope(
            message,
            status_code=402,
            error_type="entitlement_error",
            code="ENTITLEMENT_DENIED",
            extra={
                "upgrade_required": True,
                "feature": "voice_sim",
            },
        ),
        status=402,
    )


def _is_oss() -> bool:
    """True when the deployment is OSS (ee/ stripped or DeploymentMode.is_oss).

    Early-startup fallback only — prefer `_turing_denied_off_cloud()`, which
    consults the capability service and also catches self-hosted EE installs
    whose license doesn't include Turing (a case `is_oss()` alone misses)."""
    try:
        from ee.usage.deployment import DeploymentMode

        return DeploymentMode.is_oss()
    except ImportError:
        return True  # ee.usage absent → OSS


def _turing_denied_off_cloud() -> bool:
    """Single boundary helper: True when Turing/Protect models aren't
    available in this deployment and the gate should block/hide them.

    Turing is an ``oss_locked`` capability, so off-cloud it needs either the
    OSS build (never) or a self-hosted EE license that actually includes it.
    We route through ``capability_service.check("turing_models")`` so an
    EE deployment *without* Turing in its license is correctly denied —
    something the older ``DeploymentMode.is_oss()`` probe let slip through
    (is_oss() is False for any EE flavor, licensed or not).

    Cloud is intentionally a no-op here (returns False): per-org Turing
    entitlement on cloud is enforced by the usage/entitlement layer, not this
    gate, so blocking here would double-gate and can't see the org context.

    Falls back to the deployment probe during early startup, before the
    capability service is wired in ``AppConfig.ready``."""
    try:
        from tfc.capabilities import service as capability_service
        from tfc.licensing.types import DeploymentLocation
    except Exception:
        return _is_oss()  # capability layer unimportable → probe deployment

    if not capability_service.is_configured():
        return _is_oss()  # early startup → probe deployment

    if capability_service.get_deployment_location() == DeploymentLocation.CLOUD:
        return False  # cloud: enforced by the per-org entitlement layer

    return not capability_service.check("turing_models").allowed


def strip_turing_from_config_options(
    config_params_option: dict | None,
) -> dict:
    """When Turing models aren't available in this deployment, drop
    Turing/Protect models from the `model` option list so the frontend
    dropdown never offers a model the gate would 402.

    Returns a copy; the original dict is not mutated. No-op on cloud."""
    if not config_params_option:
        return config_params_option or {}
    if not _turing_denied_off_cloud():
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
                EvalTemplate.no_workspace_objects.filter(id=template_id, deleted=False)
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
    the deployment doesn't include Turing (OSS build, or a self-hosted EE
    install whose license omits it). Return None otherwise so the caller
    proceeds — including on cloud, where per-org entitlement is enforced by
    the usage layer rather than this gate.

    Use at the top of any view that accepts a model selection and would
    otherwise route into ee/turing code."""
    if not is_turing_model(model_name):
        return None

    if not _turing_denied_off_cloud():
        return None

    message = (
        "Turing and Protect models are not available on OSS. "
        "Select a different model (OpenAI, Anthropic, etc.) "
        "or upgrade your plan."
    )
    return Response(
        build_error_envelope(
            message,
            status_code=402,
            error_type="entitlement_error",
            code="ENTITLEMENT_DENIED",
            extra={
                "upgrade_required": True,
                "feature": "turing",
            },
        ),
        status=402,
    )
