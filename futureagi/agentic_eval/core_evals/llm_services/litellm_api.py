import litellm
from retrying import retry

import structlog

logger = structlog.get_logger(__name__)
from agentic_eval.core_evals.llm_services.abstract_llm import AbstractLlmService


class LitellmService(AbstractLlmService):
    _instance = None
    _api_key = None

    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, api_key):
        self._api_key = api_key

    def embeddings(self, text: str) -> list:
        """
        Fetches response from OpenAI's Embeddings API.
        """
        raise Exception("")

    @retry(stop_max_attempt_number=3, wait_fixed=2000)
    def chat_completion(self, messages, model, **kwargs) -> str:
        """
        Fetches response from Litellm's Completion API.
        """
        try:
            response = litellm.completion(
                api_key=self._api_key, model=model, messages=messages, **kwargs
            )
            return response.choices[0].message.content
        except Exception as e:
            logger.error(f"Error in ChatCompletion: {e}")
            raise e

    @retry(stop_max_attempt_number=3, wait_fixed=2000)
    def chat_completion_json(self, messages, model, **kwargs) -> str:
        raise Exception("")

    def json_completion(self, messages, model, **kwargs):
        raise Exception("")
