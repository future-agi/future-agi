from agentic_eval.core_evals.run_prompt.litellm_models import LiteLLMModelManager


def get_api_key_for_model(
    model_name: str, organization_id: str, workspace_id: str | None = None
) -> str | dict:
    """
    Get the API key or config for a given model and organization.
    """
    model_manager = LiteLLMModelManager(
        model_name=model_name, organization_id=organization_id
    )
    api_key = model_manager.get_api_key(
        organization_id=organization_id, workspace_id=workspace_id
    )

    if not api_key:
        raise ValueError(f"API key not found for {model_name}")

    return api_key
