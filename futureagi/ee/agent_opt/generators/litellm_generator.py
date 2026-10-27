import litellm
from typing import Dict, List, Optional

from ..types import LLMMessage
from ..base.base_generator import BaseGenerator

import structlog

logger = structlog.get_logger(__name__)


class LiteLLMGenerator(BaseGenerator):
    """
    A Generator that uses LiteLLM to call any supported language model.
    """

    def __init__(self, model: str, prompt_template: str):
        """
        Initializes the LiteLLMGenerator.

        Args:
            model: The name of the model to use (e.g., "gpt-4o-mini").
            prompt_template: A string template for the prompt, with placeholders
                             in f-string format (e.g., "Summarize this: {text}").
        """
        self.model = model
        self.prompt_template = prompt_template
        # LiteLLM is stateless, so no further setup is needed here.

    def generate(self, prompt_vars: Dict[str, str], **litellm_kwargs) -> str:
        """
        Fills the prompt template and calls the LiteLLM API.

        Args:
            prompt_vars: A dictionary of variables to fill the prompt template.
            litellm_kwargs: Any litellm supported kwargs, including:
                - api_key: Optional API key to use for the request.
                           If provided, detailed errors are shown to user.
                           If not provided (using env keys), generic errors are shown.

        Returns:
            The string content of the model's response.
        """
        prompt = self.prompt_template.format(**prompt_vars)

        response_format = litellm_kwargs.get("response_format")
        if (
            isinstance(response_format, dict)
            and response_format.get("type") == "json_object"
            and "json" not in prompt.lower()
        ):
            prompt = f"{prompt}\n\nRespond with a valid json object."

        messages = [LLMMessage(role="user", content=prompt)]
        messages_for_litellm = [msg.model_dump(exclude_none=True) for msg in messages]

        # Check if user provided their own api_key
        user_provided_api_key = litellm_kwargs.get("api_key") is not None

        try:
            # Extract api_key separately to ensure it's never dropped
            # drop_params=True will drop unsupported model params but not authentication params
            api_key = litellm_kwargs.pop("api_key", None)

            response = litellm.completion(
                model=self.model,
                messages=messages_for_litellm,
                api_key=api_key,
                drop_params=True,
                **litellm_kwargs
            )
            return response.choices[0].message.content
        except Exception as e:
            if user_provided_api_key:
                # User provided their own API key - show them the actual error
                error_msg = f"LLM generation failed: {str(e)}"
                logger.error(error_msg)
                raise RuntimeError(error_msg) from e
            else:
                # Using internal/env keys - show generic error message
                logger.error(
                    f"[FixYourAgent] LLM generation failed (litellmgenerator): {e}"
                )
                raise RuntimeError(
                    "Content generation failed. Please try again later."
                ) from e

    @property
    def model_name(self) -> str:
        return self.model

    def get_prompt_template(self) -> str:
        """Returns the current prompt template."""
        return self.prompt_template

    def set_prompt_template(self, template: str):
        """Updates the prompt template."""
        self.prompt_template = template
