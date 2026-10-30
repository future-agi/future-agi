"""Runtime EE feature gates for non-URL-bound entry points.

Use these where URL-level gating can't distinguish free vs paid traffic
(e.g. an endpoint that works for any model, but only paid models route
through EE code paths)."""

from __future__ import annotations

from rest_framework.response import Response

from tfc.utils.api_errors import build_error_envelope

JEV_MODEL_IDS = ("jev-1.13.0", "jev-latest")


def is_jev_model(model_name: object) -> bool:
    return model_name in JEV_MODEL_IDS


# The user-facing text when this build doesn't ship Jev's code.
JEV_UNAVAILABLE_MESSAGE = "Jev isn't available in this build."


def jev_available() -> bool:
    """Whether this build ships Jev's code (the evaluator and its client).
    A build with ee/ stripped doesn't, and must refuse Jev rather than fail
    on an import."""
    from tfc.ee_loader import has_ee

    return has_ee("ee.jev") and has_ee("ee.evals.llm.jev_evaluator")


def _jev_denied_off_cloud() -> bool:
    """Jev is a Community feature: every self-hosted install runs it, OSS or
    EE, licensed or not. Cloud defers to the engine's plan check
    (has_jev_models)."""
    from tfc.capabilities import service as capability_service
    from tfc.licensing.types import DeploymentLocation

    if not capability_service.is_configured():
        return False
    if capability_service.get_deployment_location() == DeploymentLocation.CLOUD:
        return False
    return not capability_service.check("jev_models").allowed


def _jev_denied_response(message: str) -> Response:
    return Response(
        build_error_envelope(
            message,
            status_code=402,
            error_type="entitlement_error",
            code="ENTITLEMENT_DENIED",
            extra={"upgrade_required": True, "feature": "jev"},
        ),
        status=402,
    )


def jev_gate_for_template(model_name: object, template_id=None, eval_type=None):
    if isinstance(model_name, str) and model_name.lower().startswith("jev-"):
        if not is_jev_model(model_name):
            return Response(
                build_error_envelope(
                    "Unsupported Jev model. Supported: jev-1.13.0, jev-latest.",
                    code="JEV_MODEL_UNKNOWN",
                    extra={"attr": "model", "feature": "jev"},
                ),
                status=400,
            )
        if not jev_available():
            return _jev_denied_response(JEV_UNAVAILABLE_MESSAGE)
        if _jev_denied_off_cloud():
            return _jev_denied_response(
                "Jev models are not available in this deployment."
            )
    return None


def ee_model_gate_for_template(model_name: object, template_id=None, eval_type=None):
    gate = jev_gate_for_template(model_name, template_id, eval_type)
    if gate is not None:
        return gate
    return turing_oss_gate_for_template(model_name, template_id, eval_type)


def describe_jev_error(exc: BaseException) -> dict | None:
    """The typed code and fixed user-facing text for a failed Jev run, as
    ``{"code", "message", "attr", "details"}``. None when exc is not a Jev
    failure, or when this build has no Jev."""
    try:
        from ee.jev.errors import describe
    except ImportError:
        return None
    return describe(exc)


def jev_error_message(exc: BaseException) -> str | None:
    """The user-facing text for a failed Jev run, or None for any other error."""
    described = describe_jev_error(exc)
    return described["message"] if described else None


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


def insights_turing_models() -> list[str]:
    """The Turing models Insights is configured to call: its fast and
    thinking models (INSIGHTS_FAST_MODEL / INSIGHTS_THINKING_MODEL), which
    are Turing's by default. Empty when a deployment points both at its own
    models, or when this build has no Insights."""
    try:
        from ee.agenthub.insights.llm import FAST, THINKING, model_for
    except ImportError:
        return []
    models = [model_for(FAST), model_for(THINKING)]
    return [
        model
        for model in models
        if is_turing_model(model) or is_turing_model(str(model).rsplit("/", 1)[-1])
    ]


INSIGHTS_DENIED_MESSAGE = "Insights isn't available to this organization."
INSIGHTS_TURING_DENIED_MESSAGE = (
    "Insights runs on Turing models, which aren't available on this "
    "organization's plan."
)
# Self-hosted, Insights is licensed but Turing isn't: the deployment can point
# Insights at its own models instead.
INSIGHTS_TURING_UNLICENSED_MESSAGE = (
    "Insights runs on Turing models, which this Enterprise license doesn't "
    "include. Set INSIGHTS_FAST_MODEL and INSIGHTS_THINKING_MODEL to your own "
    "models, or ask us to add Turing models to the license."
)


def insights_turing_denied_message() -> str:
    """Why insights_turing_denied refused, in this deployment's terms: a
    Cloud plan, or a self-hosted license."""
    from tfc.capabilities import service as capability_service
    from tfc.licensing.types import DeploymentLocation

    if capability_service.get_deployment_location() == DeploymentLocation.CLOUD:
        return INSIGHTS_TURING_DENIED_MESSAGE
    return INSIGHTS_TURING_UNLICENSED_MESSAGE


def insights_turing_denied(org_id) -> bool:
    """Whether Insights' model steps are refused for want of Turing: only
    when Insights is configured to call a Turing model. A deployment that
    points Insights at its own models doesn't need Turing."""
    if not insights_turing_models():
        return False
    from tfc.capabilities import service as capability_service

    return not capability_service.check("turing_models", org_id=str(org_id)).allowed


def voice_sim_oss_gate_response() -> Response | None:
    """Return a 402 response when the voice-sim code isn't available in this
    build (OSS image with ee/ stripped), else None.

    Voice simulation requires the `ee.voice` module. This gate decides CODE
    availability only — license/plan entitlement is the caller's second
    layer (capability service / cloud plan resolver), and voice_sim is open
    on self-hosted deployments regardless of license state, so deployment
    mode alone must never deny here."""
    from tfc.utils.lazy_extras import extra_available

    if not extra_available("livekit"):
        return _voice_sim_code_unavailable_response()

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


def voice_sim_gate_response(user_organization) -> Response | None:
    """Return the complete deployment and plan gate for voice simulation.

    All HTTP and non-HTTP entry points use this helper so code availability
    and the cloud ``has_voice_sim`` entitlement cannot drift apart.
    """
    oss_gate = voice_sim_oss_gate_response()
    if oss_gate is not None:
        return oss_gate

    try:
        from ee.usage.deployment import DeploymentMode
        from ee.usage.services.entitlements import Entitlements
    except ImportError:
        # A partial EE installation cannot safely answer plan entitlements.
        return _voice_sim_code_unavailable_response()

    if not DeploymentMode.is_cloud():
        return None

    feature_check = Entitlements.check_feature(
        str(user_organization.id), "has_voice_sim"
    )
    if feature_check.allowed:
        return None

    message = feature_check.reason or "This feature requires a higher plan"
    return Response(
        build_error_envelope(
            message,
            status_code=403,
            code="permission_denied",
            extra={"upgrade_required": True, "feature": "voice_sim"},
        ),
        status=403,
    )


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
