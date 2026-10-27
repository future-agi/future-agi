import structlog

from agentic_eval.core_evals.run_prompt.litellm_models import LiteLLMModelManager
from model_hub.models.choices import ProviderLogoUrls

logger = structlog.get_logger(__name__)


def get_provider_for_model(
    model_name: str, organization_id: str = None, workspace_id: str = None
) -> str | None:
    """Get the provider name for a given model. Returns None for unavailable models."""
    try:
        model_manager = LiteLLMModelManager(
            model_name=model_name, organization_id=organization_id
        )
        return model_manager.get_provider(
            model_name=model_name,
            organization_id=organization_id,
            workspace_id=workspace_id,
        )
    except ValueError:
        logger.warning("provider_lookup_failed", model_name=model_name)
        return None


def get_provider_logo_url(
    model_name: str, organization_id: str = None, workspace_id: str = None
) -> str | None:
    """Get the provider logo URL for a given model."""
    provider = get_provider_for_model(model_name, organization_id, workspace_id)
    if not provider:
        return None
    return ProviderLogoUrls.get_url_by_provider(provider)


# Non-chat models are executed by dedicated handlers (TTS/STT/image) that
# accept bare model names, so catalog matching for them is looser (see below).
_NON_CHAT_MODES = ("audio", "stt", "tts", "image_generation")


def is_model_in_catalog(model_name: str, organization_id=None) -> bool:
    """Check if a model is available at runtime (including custom models).

    Uses LiteLLMModelManager's filtered model list — the same source the
    execution path resolves against — rather than raw AVAILABLE_MODELS,
    which still contains models stripped by the runtime deny-list
    (_remove_failed_models). Custom models are merged in by the manager
    via the default CustomAIModel manager, which already excludes
    soft-deleted rows.

    Audio/image requests send bare model names (e.g. "tts-1", "dall-e-3")
    while the catalog stores provider- or size-prefixed variants
    ("openai/tts-1", "hd/1024-x-1024/dall-e-3"), and their handlers invoke
    litellm with the bare name directly. Match those on the trailing path
    segment against the full catalog so non-chat flows are never blocked.
    """
    from agentic_eval.core_evals.run_prompt.available_models import AVAILABLE_MODELS

    manager = LiteLLMModelManager(
        model_name=model_name, organization_id=organization_id
    )
    if any(m["model_name"] == model_name for m in manager.models):
        return True

    suffix = f"/{model_name}"
    return any(
        m.get("mode") in _NON_CHAT_MODES and m["model_name"].endswith(suffix)
        for m in AVAILABLE_MODELS
    )
