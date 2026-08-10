import time
from abc import abstractmethod

from agentic_eval.core_evals.fi_metrics.metric_type import MetricType
from agentic_eval.core_evals.fi_utils.evals_result import EvalResult, EvalResultMetric
from agentic_eval.core_evals.fi_utils.logging import logger
from agentic_eval.core_evals.llm_services.abstract_llm import AbstractLlmService
from agentic_eval.core_evals.llm_services.openai_api import OpenAiService
from agentic_eval.core_evals.fi_evals.base_evaluator import BaseEvaluator
from ee.evals.llm.example import FewShotExample


class LlmEvaluator(BaseEvaluator):
    llm_service: AbstractLlmService
    _model: str
    _system_message_template: str | None = None
    _user_message_template: str | None = None

    TEMPERATURE = 0.0

    RETURN_FORMAT_INSTRUCTIONS = """
    You MUST return a JSON object with the following fields:
    - result: Result must be either 'Pass' or 'Fail'.
    - explanation: An explanation of why the result is Pass or Fail.
    - score: (Optional) Use the scoring criteria specified.
    """

    DEFAULT_SYSTEM_MESSAGE_TEMPLATE = """
    ### INSTRUCTIONS ###
    You are an expert at evaluating responses by an AI.

    Based on the instructions provided, you will evaluate the response and determine if it passes or fails.

    """

    DEFAULT_USER_MESSAGE_TEMPLATE = """
    ### GRADING CRITERIA ###
    {grading_criteria}

    ### EXAMPLES ###
    {examples}

    ### RESPONSE TO EVALUATE ###
    {response}
    """

    EXAMPLES: FewShotExample = []

    def __init__(
        self,
        model: str | None = None,
        api_key : str | None = None,
        system_message_template: str | None = None,
        user_message_template: str | None = None,
        llm_service: AbstractLlmService | None = None,
        **kwargs,
    ):
        if llm_service is not None and isinstance(llm_service, AbstractLlmService):
            self.llm_service = llm_service
        else:
            self.llm_service = OpenAiService(openai_api_key=api_key)
        if model is None:
            self._model = self.default_model
        else:
            self._model = model

        # Initialize message templates
        if system_message_template is None:
            self._system_message_template = (
                self.DEFAULT_SYSTEM_MESSAGE_TEMPLATE + self.RETURN_FORMAT_INSTRUCTIONS
            )
        else:
            self._system_message_template = system_message_template

        if user_message_template is None:
            self._user_message_template = self.DEFAULT_USER_MESSAGE_TEMPLATE
        else:
            self._user_message_template = user_message_template


    @property
    @abstractmethod
    def default_model(self):
        """The default model for the evaluator."""
        pass

    def __str__(self):
        formatted_args = [str(value) for value in self.required_args]
        return f"Docstring: {self.__doc__}\nRequired Arguments: {formatted_args}"


    def _system_message(self) -> str:
        return self._system_message_template or self.DEFAULT_SYSTEM_MESSAGE_TEMPLATE


    @abstractmethod
    def _user_message(self, **kwargs) -> str:
        """Format the user message with the provided kwargs."""
        pass

    def _prompt_messages(self, **kwargs) -> list[dict]:
        return [
            {
                "role": "system",
                "content": self._system_message(),
            },
            {
                "role": "user",
                "content": self._user_message(**kwargs),
            },
        ]

    def _evaluate(self, **kwargs) -> EvalResult:
        """
        Run the LLM evaluator.
        """
        start_time = time.time()
        # Validate that correct args were passed
        self.validate_args(**kwargs)

        # Construct Prompt
        messages = self._prompt_messages(**kwargs)
        # Run the LLM Completion

        chat_completion_response_json: dict = self.llm_service.json_completion(
            model=self._model,
            messages=messages,
            temperature=self.TEMPERATURE,
        )

        metrics = []
        try:
            result = chat_completion_response_json["result"]
            explanation = chat_completion_response_json["explanation"]
            failure = self.is_failure(result)
            passed_value = 1.0 - (1.0 if failure else 0.0)
            metrics.append(EvalResultMetric(id=MetricType.PASSED.value, value=passed_value))

        except Exception as e:
            logger.error(f"Error occurred during eval: {e}")
            raise e

        end_time = time.time()
        eval_runtime_ms = int((end_time - start_time) * 1000)
        llm_eval_result: EvalResult = {
            "name": self.name,
            "display_name": self.display_name,
            "data": kwargs,
            "failure": failure,
            "reason": explanation,
            "metadata": chat_completion_response_json.get('metadata'),
            "runtime": eval_runtime_ms,
            "model": self._model,
            "metrics": metrics,
            "datapoint_field_annotations": None,
        }
        return llm_eval_result

    def _get_completion_content(self, messages, model=None, temperature=None, response_format=None):
        """
        Get the raw content from a completion response instead of JSON.
        Useful for audio and other multimodal inputs.

        Args:
            messages: The messages to send to the model
            model: Optional model override
            temperature: Optional temperature override
            response_format: Optional response format specification

        Returns:
            The raw text content from the model response
        """
        try:
            # Use provided values or fall back to defaults
            model_to_use = model if model is not None else self._model
            temp_to_use = temperature if temperature is not None else self.TEMPERATURE

            # Set up parameters
            params = {
                "model": model_to_use,
                "messages": messages,
                "temperature": temp_to_use
            }

            # Add response_format if provided
            if response_format:
                params["response_format"] = response_format

            # Get the completion - use chat_completion instead of completion
            chat_completion_response = self.llm_service.chat_completion(**params)

            # Return just the content string
            return chat_completion_response

        except Exception as e:
            logger.error(f"Error getting completion content: {e}")
            raise e

