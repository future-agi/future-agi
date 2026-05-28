import structlog

logger = structlog.get_logger(__name__)
from model_hub.models.choices import ModelChoices
from tfc.constants.api_calls import APICallTypeChoices

model_to_api_call_type = {
    ModelChoices.TURING_LARGE: APICallTypeChoices.TURING_LARGE_EVALUATOR.value,
    ModelChoices.TURING_SMALL: APICallTypeChoices.TURING_SMALL_EVALUATOR.value,
    ModelChoices.TURING_FLASH: APICallTypeChoices.TURING_FLASH_EVALUATOR.value,
    ModelChoices.PROTECT_FLASH: APICallTypeChoices.PROTECT_FLASH_EVALUATOR.value,
    ModelChoices.PROTECT: APICallTypeChoices.PROTECT_EVALUATOR.value,
}


def _get_api_call_type(model: str):
    try:
        if not model:
            return APICallTypeChoices.TURING_LARGE_EVALUATOR.value

        model_key: ModelChoices | str = model
        if isinstance(model, str):
            model_key = ModelChoices(model)

        return model_to_api_call_type.get(
            model_key, APICallTypeChoices.TURING_LARGE_EVALUATOR.value
        )
    except Exception as e:
        logger.exception(f"Error getting api call type: {e}")
        return APICallTypeChoices.TURING_LARGE_EVALUATOR.value
