import functools
import importlib
import threading
import time
import traceback
from collections.abc import Callable
from typing import Any

import openai

import structlog

logger = structlog.get_logger(__name__)
from agentic_eval.core_evals.fi_utils.fi_meta import FiMeta
from agentic_eval.core_evals.fi_utils.token_count_helper import (
    get_completion_tokens_openai_chat_completion,
    get_prompt_tokens_openai_chat_completion,
)
from agentic_eval.core_evals.llm_services.inference_logger import InferenceLogger

# Check OpenAI version
openai_version = openai.__version__
version_numbers = tuple(map(int, openai_version.split(".")))


def log_to_fi(result: dict, args: dict, fi_meta: FiMeta):
    try:
        prompt_slug = "default"
        context = None
        customer_id = None
        customer_user_id = None
        response_time_ms = None
        session_id = None
        user_query = None
        tool_calls = None
        environment = "production"
        external_reference_id = None
        custom_attributes = None
        custom_eval_metrics = None

        if fi_meta:
            prompt_slug = fi_meta.prompt_slug
            context = fi_meta.context
            response_time_ms = fi_meta.response_time
            customer_id = fi_meta.customer_id
            customer_user_id = fi_meta.customer_user_id
            session_id = fi_meta.session_id
            user_query = fi_meta.user_query
            environment = fi_meta.environment or "production"
            external_reference_id = fi_meta.external_reference_id
            custom_attributes = fi_meta.custom_attributes
            custom_eval_metrics = fi_meta.custom_eval_metrics
            tool_calls = fi_meta.tool_calls
        InferenceLogger.log_inference(
            prompt_slug=prompt_slug,
            prompt=args["messages"],
            language_model_id=args["model"],
            response=result,
            context=context,
            tool_calls=tool_calls,
            response_time=response_time_ms,
            customer_id=customer_id,
            customer_user_id=customer_user_id,
            session_id=session_id,
            user_query=user_query,
            prompt_tokens=result["usage"]["prompt_tokens"],
            completion_tokens=result["usage"]["completion_tokens"],
            total_tokens=result["usage"]["total_tokens"],
            environment=environment,
            external_reference_id=external_reference_id,
            custom_attributes=custom_attributes,
            custom_eval_metrics=custom_eval_metrics,
        )
    except Exception as e:
        logger.exception("Exception while logging to fi: ", e)


class OpenAiMiddleware:
    _fi_meta: FiMeta | None
    _args: any | None
    _kwargs: dict | None
    fi_response = ""

    def __init__(self):
        pass

    def _with_fi_logging(self, func):

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            # Extract args from OpenAI call
            self._args = args
            self._kwargs = kwargs
            self._fi_meta = kwargs.pop("fi_meta", None)

            # Make the OpenAI call and measure response time
            start_time = time.time()
            openai_response = func(*self._args, **self._kwargs)
            end_time = time.time()
            response_time_ms = int((end_time - start_time) * 1000)

            # Return if no result was returned from OpenAI
            if openai_response is None:
                logger.info("No result was returned from OpenAI")
                return openai_response

            # Construct the fi Meta object and log to fi
            try:
                # Construct the fi Meta object
                if self._fi_meta is not None:
                    self._fi_meta.response_time = response_time_ms
                else:
                    self._fi_meta = FiMeta(
                        prompt_slug="default",
                        response_time=response_time_ms,
                        environment="default",
                    )

                return self._response_interceptor(
                    openai_response,
                    ("stream" in self._kwargs and self._kwargs["stream"]),
                )
            except Exception as e:
                logger.exception("Exception in fi logging: ", e)
                traceback.print_exc()
                return openai_response

        return wrapper

    def _response_interceptor(
        self, response, is_streaming=False, send_response: Callable[[dict], None] = None
    ):

        tool_calls_data = []

        try:
            # Check for tool_calls finish_reason in the response
            if response.choices[0].finish_reason == "tool_calls":
                tool_calls = [
                    call.model_dump() for call in response.choices[0].message.tool_calls
                ]
                tool_calls_data = [
                    {
                        "arguments": call["function"]["arguments"],
                        "name": call["function"]["name"],
                    }
                    for call in tool_calls
                ]

            # Handle typical prompt response
            prompt_response = response.choices[0].message.content

            if not prompt_response and response.choices[0].message.tool_calls:
                # Fallback to handle if prompt is empty but tool calls are present
                tool_calls = [
                    call.model_dump() for call in response.choices[0].message.tool_calls
                ]
                tool_calls_data = [
                    {
                        "arguments": call["function"]["arguments"],
                        "name": call["function"]["name"],
                    }
                    for call in tool_calls
                ]

                # Update fi_meta with tool_calls if available
            if tool_calls_data:
                self._fi_meta.tool_calls = (
                    tool_calls_data  # Capture tool_calls for logging
                )

            # You may want to decide what constitutes self.fi_response here
            self.fi_response = tool_calls_data or response.choices[0].message.__dict__

        except Exception as ex:
            logger.exception(f"Exception in response interception: {ex}")

        def generator_intercept_packets():
            for r in response:
                self.collect_stream_inference_by_chunk(r)
                yield r
            self._log_stream_to_fi()

        if is_streaming:
            return generator_intercept_packets()
        else:
            api_thread = threading.Thread(
                target=log_to_fi,
                kwargs={
                    "result": response
                    if version_numbers < (1, 0, 0)
                    else response.model_dump(),
                    "args": self._kwargs,
                    "fi_meta": self._fi_meta,
                },
            )
            api_thread.start()
            return response

    def _get_text_from_stream_chunk(self, stream_chunk):
        """
        gets the text from the stream chunk
        """
        try:
            text = ""
            choices = stream_chunk.get("choices", [])
            if choices and len(choices) > 0 and "delta" in choices[0]:
                delta = choices[0].get("delta", {})
                if "content" in delta and delta["content"] is not None:
                    text = delta.get("content", "")

            return text
        except Exception as e:
            raise e

    def collect_stream_inference_by_chunk(self, stream_chunk):
        """
        collects the inference from the log stream of openai chat completion chunk by chunk
        """
        try:
            if isinstance(stream_chunk, dict):
                self.fi_response += self._get_text_from_stream_chunk(stream_chunk)
            else:
                self.fi_response += self._get_text_from_stream_chunk(
                    stream_chunk.model_dump()
                )
        except Exception as e:
            raise e

    def _log_stream_to_fi(self):
        """
        logs the stream response to the fi
        """
        try:
            prompt_tokens = self._get_prompt_tokens(
                prompt=self._kwargs["messages"], language_model_id=self._kwargs["model"]
            )

            completion_tokens = self._get_completion_tokens(
                response=self.fi_response, language_model_id=self._kwargs["model"]
            )
            if prompt_tokens is not None and completion_tokens is not None:
                total_tokens = prompt_tokens + completion_tokens
            else:
                total_tokens = None
            payload = {
                "prompt_slug": self._fi_meta.prompt_slug,
                "prompt": self._kwargs["messages"],
                "language_model_id": self._kwargs["model"],
                "response": self.fi_response,
                "response_time": self._fi_meta.response_time,
                "context": self._fi_meta.context,
                "environment": self._fi_meta.environment,
                "customer_id": str(self._fi_meta.customer_id)
                if self._fi_meta.customer_id is not None
                else None,
                "customer_user_id": str(self._fi_meta.customer_user_id)
                if self._fi_meta.customer_user_id is not None
                else None,
                "session_id": str(self._fi_meta.session_id)
                if self._fi_meta.session_id is not None
                else None,
                "user_query": str(self._fi_meta.user_query)
                if self._fi_meta.user_query is not None
                else None,
                "external_reference_id": str(self._fi_meta.external_reference_id)
                if self._fi_meta.external_reference_id is not None
                else None,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": total_tokens,
                "custom_attributes": self._fi_meta.custom_attributes,
                "custom_eval_metrics": self._fi_meta.custom_eval_metrics,
            }
            # Remove None fields from the payload
            payload = {k: v for k, v in payload.items() if v is not None}
            InferenceLogger.log_inference(**payload)
        except Exception as e:
            raise e

    def _get_prompt_tokens(self, prompt: list[dict[str, Any]], language_model_id: str):
        """
        gets the prompt tokens given the prompt for the openai chat model completion
        """
        try:
            tokens = get_prompt_tokens_openai_chat_completion(
                prompt=prompt, language_model_id=language_model_id
            )
            return tokens
        except Exception:
            return None

    def _get_completion_tokens(self, response: str, language_model_id: str):
        """
        gets the completion tokens given the prompt response from the openai chat model completion
        """
        try:
            tokens = get_completion_tokens_openai_chat_completion(
                response=response, language_model_id=language_model_id
            )
            return tokens
        except Exception:
            return None

    # Apply the fi logging wrapper to OpenAI methods
    def apply_fi(self, openai_instance=None):
        openai_version = openai.__version__
        version_numbers = tuple(map(int, openai_version.split(".")))
        if version_numbers < (1, 0, 0):
            ChatCompletion = importlib.import_module(
                "openai.api_resources"
            ).ChatCompletion
            openai_method_name = "create"
            openai_method = getattr(ChatCompletion, openai_method_name)

            # Override the create method with the fi logging wrapper
            fi_method = self._with_fi_logging(openai_method)
            setattr(ChatCompletion, openai_method_name, fi_method)
        else:
            openai_method_name = "create"
            if openai_instance is not None:
                openai_method = getattr(
                    openai_instance.chat.completions, openai_method_name
                )

                # Override the chat.completions.create method with the fi logging wrapper
                fi_method = self._with_fi_logging(openai_method)
                setattr(openai_instance.chat.completions, openai_method_name, fi_method)


middleware = OpenAiMiddleware()
middleware.apply_fi()


if version_numbers > (1, 0, 0):
    # Monkey-patch the constructor of openai.OpenAI
    original_openai_constructor = importlib.import_module("openai").OpenAI.__init__

    def new_openai_constructor(self, *args, **kwargs):
        original_openai_constructor(self, *args, **kwargs)
        middleware.apply_fi(self)

    importlib.import_module("openai").OpenAI.__init__ = new_openai_constructor
