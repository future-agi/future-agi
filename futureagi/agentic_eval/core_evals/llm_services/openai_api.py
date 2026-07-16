import json
import time

from openai import OpenAI
from retrying import retry

import structlog

logger = structlog.get_logger(__name__)
from agentic_eval.core_evals.fi_utils.fi_model import Model
from agentic_eval.core_evals.fi_utils.json import JsonHelper
from agentic_eval.core_evals.fi_utils.token_count_helper import calculate_total_cost
from agentic_eval.core_evals.llm_services.abstract_llm import AbstractLlmService

DEFAULT_TEMPERATURE = 0.0


def _first_choice(response, operation: str):
    choices = getattr(response, "choices", None)
    if not choices:
        raise ValueError(f"OpenAI {operation} returned no choices")
    return choices[0]


class OpenAiService(AbstractLlmService):
    _instance = None

    # def __new__(cls):
    #     if not cls._instance:
    #         cls._instance = super(OpenAiService, cls).__new__(cls)
    #     return cls._instance

    def __init__(self, openai_api_key):
        # openai_api_key = OpenAiApiKey.get_key()
        # if openai_api_key is None:
        #     raise NoOpenAiApiKeyException()
        self.openai = OpenAI(api_key=openai_api_key)

    def embeddings(self, text: str, model: str) -> list:  # type: ignore[override]
        """
        Fetches response from OpenAI's Embeddings API.
        """
        try:
            response = self.openai.embeddings.create(
                model=model, input=text, encoding_format="float"
            )
            return response.data[0].embedding
        except Exception as e:
            logger.error(f"Error in Embeddings: {e}")
            raise e

    @retry(stop_max_attempt_number=3, wait_fixed=2000)
    def chat_completion(self, messages, model, **kwargs) -> str:
        """
        Fetches response from OpenAI's ChatCompletion API.
        """
        if "temperature" not in kwargs:
            kwargs["temperature"] = DEFAULT_TEMPERATURE
        try:
            start_time = time.time()
            response = self.openai.chat.completions.create(
                model=model, messages=messages, **kwargs
            )
            choice = _first_choice(response, "chat_completion")
            end_time = time.time()
            completion_time = (end_time - start_time) * 1000
            metadata = {
                "usage": {
                    "completion_tokens": response.usage.completion_tokens,
                    "prompt_tokens": response.usage.prompt_tokens,
                    "total_tokens": response.usage.total_tokens,
                },
                "cost": calculate_total_cost(model, dict(response.usage)),
                "response_time": completion_time,
            }
            if choice.finish_reason == "tool_calls":
                tool_calls = [call.model_dump() for call in choice.message.tool_calls]
                tool_calls_data = [
                    {
                        "arguments": call["function"]["arguments"],
                        "name": call["function"]["name"],
                    }
                    for call in tool_calls
                ]
                return {"value": json.dumps(tool_calls_data), "metadata": metadata}
            else:
                prompt_response = choice.message.content

                if not prompt_response:
                    if choice.message.tool_calls:
                        tool_calls = [
                            call.model_dump() for call in choice.message.tool_calls
                        ]
                        tool_calls_data = [
                            {
                                "arguments": call["function"]["arguments"],
                                "name": call["function"]["name"],
                            }
                            for call in tool_calls
                        ]
                        return {
                            "value": json.dumps(tool_calls_data),
                            "metadata": metadata,
                        }
                    else:
                        return {
                            "value": json.dumps(choice.message.__dict__),
                            "metadata": metadata,
                        }
                return {"value": prompt_response, "metadata": metadata}
        except Exception as e:
            logger.error(f"Error in ChatCompletion: {e}")
            raise e

    # @retry(stop_max_attempt_number=3, wait_fixed=2000)
    def log_probs(
        self,
        messages: list[dict[str, str]],
        model: str = "gpt-4",
        max_tokens=512,
        temperature=0,
        stop=None,
        seed=123,
        tools=None,
        logprobs=None,
        top_logprobs=None,
    ) -> str:
        params = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stop": stop,
            "seed": seed,
            "logprobs": logprobs,
            "top_logprobs": top_logprobs,
        }
        if tools:
            params["tools"] = tools

        response = self.openai.chat.completions.create(**params)
        choice = _first_choice(response, "log_probs")

        top_1_raw_logprobs = [token.logprob for token in choice.logprobs.content]

        return top_1_raw_logprobs

    @retry(stop_max_attempt_number=3, wait_fixed=2000)
    def chat_completion_json(self, messages, model, **kwargs) -> str:
        """
        Fetches response from OpenAI's ChatCompletion API using JSON mode.
        """
        if "temperature" not in kwargs:
            kwargs["temperature"] = DEFAULT_TEMPERATURE
        try:
            response = self.openai.chat.completions.create(
                model=model,
                messages=messages,
                response_format={"type": "json_object"},
                **kwargs,
            )
            choice = _first_choice(response, "chat_completion_json")
            return choice.message.content
        except Exception as e:
            logger.error(f"Error in ChatCompletion: {e}")
            raise e

    def json_completion(self, messages, model, **kwargs):
        """
        Fetches response from OpenAI's ChatCompletion API using JSON mode.
        """
        completion_response = None
        if "temperature" not in kwargs:
            kwargs["temperature"] = DEFAULT_TEMPERATURE
        try:
            if Model.supports_json_mode(model):
                chat_completion_response = self.chat_completion(
                    model=model,
                    messages=messages,
                    **kwargs,
                )
            else:
                chat_completion_response = self.chat_completion(
                    model=model,
                    messages=messages,
                    **kwargs,
                )
            completion_response = chat_completion_response["value"]
            # Extract JSON object from LLM response
            response = JsonHelper.extract_json_from_text(completion_response)

            response["metadata"] = chat_completion_response.get("metadata")
            return response

        except Exception as e:
            logger.error(f"Error in ChatCompletion: {e}")
            raise ValueError(
                f"Failed to parse response from LLM, Response data format is not valid -REASON: {completion_response}"
            ) from e
