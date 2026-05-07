import asyncio
import base64
import json
import os
import re
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import wraps
from typing import Any, Dict, List, Optional, Tuple, Union

import anthropic
import boto3
import litellm
import requests
import structlog
from agentic_eval.core.utils.functions import download_image_to_base64
from agentic_eval.core.utils.model_config import ModelConfigs
from agentic_eval.core_evals.fi_utils.token_count_helper import calculate_total_cost
from anthropic import AnthropicBedrock, AsyncAnthropic, AsyncAnthropicBedrock
from botocore.exceptions import ClientError
from clickhouse_driver import Client
from google import genai
from google.genai.types import GenerateContentConfig, HttpOptions, Part, ThinkingConfig
from litellm import completion
from openai import AsyncOpenAI, OpenAI

logger = structlog.get_logger(__name__)
from agentic_eval.core.llm.audio_utils import (
    is_audio_url,
    messages_contain_audio,
    preprocess_messages_for_provider,
)
from tfc.ee_stub import _ee_stub

try:
    from ee.prompts.protect_prompts import (
        PROTECT_FLASH_PROMPT_TEMPLATE,
        build_mm_messages_for_protect,
    )
except ImportError:
    PROTECT_FLASH_PROMPT_TEMPLATE = ""
    build_mm_messages_for_protect = _ee_stub("build_mm_messages_for_protect")

try:
    from ee.prompts.eval_prompts import AUDIO_AGENT_LLM_SYSTEM_INSTRUCTION
except ImportError:
    AUDIO_AGENT_LLM_SYSTEM_INSTRUCTION = ""
from agentic_eval.core_evals.fi_utils.evals_result import BatchRunResult
from model_hub.utils import call_websocket
from tfc.telemetry import wrap_for_thread
from tfc.utils.storage import (
    download_audio_from_url,
    upload_audio_to_s3,
    upload_image_to_s3,
)

# executor = ThreadPoolExecutor(max_workers=10)

# Constants
MAX_RETRIES = 3
RETRY_DELAY = 5  # seconds
LITELLM_REQUEST_TIMEOUT = (
    300  # 5 min — prevents indefinite hangs on rate-limit / stalled connections
)
LITELLM_NUM_RETRIES = 3
LITELLM_RETRY_STRATEGY = "exponential_backoff_retry"
DEFAULT_MAX_TOKENS = 8100
DEFAULT_TEMPERATURE = 0.7

# Type aliases
AudioInput = Union[str, bytes]
LogData = Dict[str, Any]
Message = Dict[str, Any]
Messages = List[Message]
Payload = Dict[str, Any]


def log_to_clickhouse(log_data: LogData) -> None:
    """
    Log LLM usage data to ClickHouse database.

    Args:
        log_data: Dictionary containing log information including timestamp, model name,
                 service name, request/response bodies, token counts, and response time.

    Raises:
        Exception: If there's an error connecting to or writing to ClickHouse.
    """
    try:
        client = Client(
            host=os.getenv("CH_HOST"),
            port=os.getenv("CH_PORT"),
            user=os.getenv("CH_USERNAME"),
            password=os.getenv("CH_PASSWORD"),
            database=os.getenv("CH_DATABASE"),
        )

        query = """
        INSERT INTO llm_logs (
            EventDateTime, EventDate, LLMModelName, ServiceName,
            RequestBody, ResponseBody, RequestTokens, ResponseTokens, ResponseTime
        ) VALUES (
            %(EventDateTime)s, %(EventDate)s, %(LLMModelName)s, %(ServiceName)s,
            %(RequestBody)s, %(ResponseBody)s, %(RequestTokens)s, %(ResponseTokens)s, %(ResponseTime)s
        )
        """

        client.execute(
            query,
            {
                "EventDateTime": log_data["Timestamp"],
                "EventDate": log_data["Timestamp"],
                "LLMModelName": log_data["LLMModelName"],
                "ServiceName": log_data["ServiceName"],
                "RequestBody": str(log_data["RequestBody"]),
                "ResponseBody": str(log_data["ResponseBody"]),
                "RequestTokens": log_data["RequestTokens"],
                "ResponseTokens": log_data["ResponseTokens"],
                "ResponseTime": log_data["ResponseTime"],
            },
        )
    except Exception as e:
        logger.exception("Error logging to ClickHouse: %s", str(e))
        # Don't raise the exception to prevent disrupting the main flow
        # but log it for monitoring purposes


def async_log_llm_usage(func):
    """
    Decorator to asynchronously log LLM usage metrics.

    Args:
        func: The function to be decorated.

    Returns:
        Wrapped function that logs usage metrics after execution.
    """

    @wraps(func)
    def wrapper(self, *args, **kwargs):
        start_time = time.time()
        result = func(self, *args, **kwargs)
        end_time = time.time()
        response_time = end_time - start_time

        # Extract prompt from args or kwargs
        prompt = args[0] if args else kwargs.get("messages")

        # Calculate token usage (placeholder for actual token counting)
        request_tokens = 0
        response_tokens = 0

        log_data = {
            "Timestamp": start_time,
            "LLMModelName": self.model_name,
            "ServiceName": self.provider,
            "RequestBody": str(prompt),
            "ResponseBody": str(result),
            "RequestTokens": request_tokens,
            "ResponseTokens": response_tokens,
            "ResponseTime": response_time,
        }

        # Log asynchronously
        log_to_clickhouse(log_data)
        return result

    return wrapper


class LLM:
    """
    A class representing a Language Model instance with support for multiple providers.

    This class handles interactions with various LLM providers including OpenAI, Anthropic,
    Groq, Vertex AI, and AWS Bedrock. It provides a unified interface for making requests
    to these services while handling provider-specific configurations and fallbacks.

    Attributes:
        provider (str): The LLM provider (e.g., 'openai', 'anthropic', 'groq').
        model_name (str): The specific model to use from the provider.
        temperature (float): Sampling temperature for generation (0.0 to 1.0).
        max_tokens (int): Maximum number of tokens to generate.
        config (Optional[Dict]): Additional provider-specific configuration.
        optimizer (bool): Whether to use optimized model settings.
        api_key (Optional[str]): API key for the provider.
    """

    def __init__(
        self,
        provider: str = ModelConfigs.CLAUDE_4_5_SONNET_BEDROCK_ARN.provider,
        model_name: str = ModelConfigs.CLAUDE_4_5_SONNET_BEDROCK_ARN.model_name,
        temperature: float = ModelConfigs.CLAUDE_4_5_SONNET_BEDROCK_ARN.temperature,
        max_tokens: int = ModelConfigs.CLAUDE_4_5_SONNET_BEDROCK_ARN.max_tokens,
        config: Optional[Dict[str, Any]] = None,
        api_key: Optional[Union[str, Dict[str, Any]]] = None,
        optimizer: bool = False,
        org_id: Optional[str] = None,
    ):
        """
        Initialize the LLM instance.

        Args:
            provider: The LLM provider to use.
            model_name: The specific model to use.
            temperature: Sampling temperature for generation.
            max_tokens: Maximum number of tokens to generate.
            config: Additional provider-specific configuration.
            api_key: API key for the provider.
            optimizer: Whether to use optimized model settings.
            org_id: Organization ID for gateway routing (X-Org-Id header).
        """
        self.provider = provider
        self.model_name = model_name
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.history: List[Message] = []
        self.config = config or {}
        self.optimizer = optimizer
        self.api_key = api_key
        self.org_id = org_id
        self.last_finish_reason: Optional[str] = None

        # Initialize usage tracking
        self.cost = {
            "total_cost": 0,
            "prompt_cost": 0,
            "completion_cost": 0,
        }
        self.token_usage = {
            "total_tokens": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
        }
        # Initialize the appropriate client
        self._init_client(api_key)

    def _init_client(self, api_key: Optional[Union[str, Dict[str, Any]]]) -> None:
        """
        Initialize the appropriate LLM client based on the provider.

        Args:
            api_key: Optional API key to override environment variables.

        Raises:
            ValueError: If required environment variables are missing.
        """
        self.provider_api_keys = {
            "groq": os.getenv("GORQ_API_KEY") or api_key,
            "openai": os.getenv("OPENAI_API_KEY") or api_key,
            "anthropic": os.getenv("ANTHROPIC_API_KEY") or api_key,
            "openrouter": os.getenv("OPENROUTER_API_KEY") or api_key,
            "xai": os.getenv("XAI_API_KEY") or api_key,
            "vllm": {
                "server_url": os.getenv("VLLM_SERVER_URL", ""),
                "model_name": os.getenv("VLLM_MODEL_NAME", "vllm_model"),
            },
            "protect": {
                "toxicity": os.getenv(
                    "VLLM_PROTECT_TOXICITY_SERVER_URL",
                    "",
                ),
                "bias": os.getenv(
                    "VLLM_PROTECT_SEXISM_SERVER_URL",
                    "",
                ),
                "privacy": os.getenv(
                    "VLLM_PROTECT_PRIVACY_SERVER_URL",
                    "",
                ),
                "prompt_injection": os.getenv(
                    "VLLM_PROTECT_PROMPTINJ_SERVER_URL",
                    "",
                ),
                "api_key": os.getenv("VLLM_PROTECT_API_KEY", ""),
            },
            "protect_flash": {
                "server_url": os.getenv("VLLM_PROTECT_FLASH_SERVER_URL", ""),
                "model_name": os.getenv(
                    "VLLM_PROTECT_FLASH_MODEL_NAME", "protect_flash"
                ),
            },
            "vertex_ai": {
                "credentials_path": os.getenv("GOOGLE_APPLICATION_CREDENTIALS"),
                "project_id": os.getenv("GOOGLE_PROJECT_ID"),
            },
            "aws_bedrock_anthropic": {
                "aws_access_key": os.getenv("AWS_ACCESS_KEY_ID"),
                "aws_secret_key": os.getenv("AWS_SECRET_ACCESS_KEY"),
                "aws_region": os.getenv("AWS_BEDROCK_REGION", "us-east-1"),
            },
            "aws_bedrock": {
                "aws_access_key": os.getenv("AWS_ACCESS_KEY_ID"),
                "aws_secret_key": os.getenv("AWS_SECRET_ACCESS_KEY"),
                "aws_region": "us-west-2",
            },
        }

        # Initialize provider-specific client
        if self.provider == "openai":
            openai_key = self.provider_api_keys.get("openai")
            self.llm_client = OpenAI(
                api_key=openai_key if isinstance(openai_key, str) else None
            )
            self.llm_client_async = AsyncOpenAI(
                api_key=openai_key if isinstance(openai_key, str) else None
            )
        elif self.provider == "anthropic":
            anthropic_key = self.provider_api_keys.get("anthropic")
            self.llm_client = anthropic.Client(
                api_key=anthropic_key if isinstance(anthropic_key, str) else None
            )  # type: ignore
            self.llm_client_async = AsyncAnthropic(
                api_key=anthropic_key if isinstance(anthropic_key, str) else None
            )
        elif self.provider == "openrouter":
            openrouter_key = self.provider_api_keys.get("openrouter")
            self.llm_client = OpenAI(
                base_url="https://openrouter.ai/api/v1",
                api_key=openrouter_key if isinstance(openrouter_key, str) else None,
            )
            self.llm_client_async = AsyncOpenAI(
                base_url="https://openrouter.ai/api/v1",
                api_key=openrouter_key if isinstance(openrouter_key, str) else None,
            )
        elif self.provider == "xai":
            xai_key = self.provider_api_keys.get("xai")
            self.llm_client = OpenAI(
                base_url="https://api.x.ai/v1",
                api_key=xai_key if isinstance(xai_key, str) else None,
            )
            self.llm_client_async = AsyncOpenAI(
                base_url="https://api.x.ai/v1",
                api_key=xai_key if isinstance(xai_key, str) else None,
            )
        elif self.provider == "vllm":
            vllm_config = self.provider_api_keys.get("vllm", {})
            if isinstance(vllm_config, dict):
                self.server_url = vllm_config.get("server_url", "")
                self.vllm_model = vllm_config.get("model_name", "")
        elif self.provider == "protect":
            protect_config = self.provider_api_keys.get("protect", {})
            if isinstance(protect_config, dict):
                self.protect_api_key = str(protect_config.get("api_key") or "")
                self.protect_urls = {
                    k: v for k, v in protect_config.items() if k != "api_key"
                }
            else:
                self.protect_api_key = ""
                self.protect_urls = {}
        elif self.provider == "protect_flash":
            protect_flash_config = self.provider_api_keys.get("protect_flash", {})
            if isinstance(protect_flash_config, dict):
                self.server_url = protect_flash_config.get("server_url", "")
                self.vllm_model = protect_flash_config.get("model_name", "")
        elif self.provider == "aws_bedrock_anthropic":
            aws_creds = self.provider_api_keys.get("aws_bedrock_anthropic", {})
            if isinstance(aws_creds, dict):
                self.llm_client = AnthropicBedrock(  # type: ignore
                    aws_access_key=aws_creds.get("aws_access_key"),
                    aws_secret_key=aws_creds.get("aws_secret_key"),
                    aws_region=aws_creds.get("aws_region"),
                )
                self.llm_client_async = AsyncAnthropicBedrock(  # type: ignore
                    aws_access_key=aws_creds.get("aws_access_key"),
                    aws_secret_key=aws_creds.get("aws_secret_key"),
                    aws_region=aws_creds.get("aws_region"),
                )
        elif self.provider == "aws_bedrock":
            aws_creds = self.provider_api_keys.get("aws_bedrock", {})
            if isinstance(aws_creds, dict):
                session = boto3.Session(
                    aws_access_key_id=aws_creds.get("aws_access_key"),
                    aws_secret_access_key=aws_creds.get("aws_secret_key"),
                    region_name=aws_creds.get("aws_region"),
                )
                self.client = session.client("bedrock-runtime")
        elif self.provider == "vertex_ai":
            if not os.getenv("GOOGLE_APPLICATION_CREDENTIALS"):
                raise ValueError(
                    "GOOGLE_APPLICATION_CREDENTIALS environment variable must be set"
                )
            if not os.getenv("GOOGLE_CLOUD_PROJECT"):
                raise ValueError(
                    "GOOGLE_CLOUD_PROJECT environment variable must be set"
                )
            self.llm_client = genai.Client(http_options=HttpOptions(api_version="v1"))  # type: ignore
            self.llm_client_async = genai.Client(
                http_options=HttpOptions(api_version="v1")
            )  # type: ignore

        # Initialize gateway client for internal LLM routing (all providers)
        self._gateway_client = None
        try:
            try:
                from ee.usage.services.gateway_llm_client import get_gateway_client
            except ImportError:
                get_gateway_client = None

            self._gateway_client = get_gateway_client()
        except (ImportError, Exception):
            pass  # Gateway not available — litellm fallback will be used

        # Initialize async gateway client for non-blocking LLM routing
        self._async_gateway_client = None
        try:
            try:
                from ee.usage.services.gateway_llm_client import get_async_gateway_client
            except ImportError:
                get_async_gateway_client = None

            self._async_gateway_client = get_async_gateway_client()
        except (ImportError, Exception):
            pass

    GATEWAY_MAX_ATTEMPTS = 3
    GATEWAY_RETRY_BACKOFF = (0.5, 1.0, 2.0)

    def _try_gateway_completion(
        self, payload: dict, tools: Optional[list] = None
    ) -> Optional[Any]:
        """Attempt completion via the Agentcc gateway. Returns response or None on failure."""
        if not getattr(self, "_gateway_client", None):
            return None
        if getattr(self, "api_key", None):
            return None

        extra_headers = {}
        if getattr(self, "org_id", None):
            extra_headers["X-Org-Id"] = self.org_id
        kwargs = self._build_gateway_request_kwargs(
            payload=payload,
            extra_headers=extra_headers or None,
        )
        if tools:
            kwargs["tools"] = tools

        for attempt in range(self.GATEWAY_MAX_ATTEMPTS):
            try:
                return self._gateway_client.chat.completions.create(**kwargs)
            except Exception as gw_err:
                logger.warning(
                    "gateway_attempt_failed",
                    error=str(gw_err),
                    model=payload.get("model", self.model_name),
                    attempt=attempt + 1,
                    max_attempts=self.GATEWAY_MAX_ATTEMPTS,
                )
                if attempt < self.GATEWAY_MAX_ATTEMPTS - 1:
                    time.sleep(self.GATEWAY_RETRY_BACKOFF[attempt])
        return None

    def _build_gateway_request_kwargs(
        self,
        payload: dict,
        extra_headers: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        kwargs = dict(
            model=payload.get("model", self.model_name),
            messages=payload["messages"],
            temperature=payload.get("temperature", self.temperature),
            max_tokens=payload.get("max_tokens", self.max_tokens),
            extra_headers=extra_headers,
        )

        for field in ("response_format", "stream", "stream_options", "tool_choice"):
            if field in payload:
                kwargs[field] = payload[field]

        return kwargs

    async def _try_gateway_completion_async(
        self, payload: dict, tools: Optional[list] = None
    ) -> Optional[Any]:
        """Async version: attempt completion via Agentcc gateway."""
        if not getattr(self, "_async_gateway_client", None):
            return None
        if getattr(self, "api_key", None):
            return None

        extra_headers = {}
        if getattr(self, "org_id", None):
            extra_headers["X-Org-Id"] = self.org_id
        kwargs = self._build_gateway_request_kwargs(
            payload=payload,
            extra_headers=extra_headers or None,
        )
        if tools:
            kwargs["tools"] = tools

        for attempt in range(self.GATEWAY_MAX_ATTEMPTS):
            try:
                return await self._async_gateway_client.chat.completions.create(**kwargs)
            except Exception as gw_err:
                logger.warning(
                    "async_gateway_attempt_failed",
                    error=str(gw_err),
                    model=payload.get("model", self.model_name),
                    attempt=attempt + 1,
                    max_attempts=self.GATEWAY_MAX_ATTEMPTS,
                )
                if attempt < self.GATEWAY_MAX_ATTEMPTS - 1:
                    await asyncio.sleep(self.GATEWAY_RETRY_BACKOFF[attempt])
        return None

    def _update_token_usage(self, response: Any) -> None:
        """
        Update token usage statistics from the response.

        Args:
            response: The completion response containing usage information.
        """
        if hasattr(response, "usage") and response.usage:
            self.token_usage.update(
                {
                    "total_tokens": self.token_usage["total_tokens"]
                    + (response.usage.total_tokens or 0),
                    "prompt_tokens": self.token_usage["prompt_tokens"]
                    + (response.usage.prompt_tokens or 0),
                    "completion_tokens": self.token_usage["completion_tokens"]
                    + (response.usage.completion_tokens or 0),
                }
            )
            # Track cache tokens if available (Anthropic/Bedrock prompt caching)
            cache_create = (
                getattr(response.usage, "cache_creation_input_tokens", 0) or 0
            )
            cache_read = getattr(response.usage, "cache_read_input_tokens", 0) or 0
            if cache_create or cache_read:
                self.token_usage["cache_creation_input_tokens"] = (
                    self.token_usage.get("cache_creation_input_tokens", 0)
                    + cache_create
                )
                self.token_usage["cache_read_input_tokens"] = (
                    self.token_usage.get("cache_read_input_tokens", 0) + cache_read
                )

    def _update_cost(self, response: Any = None) -> None:
        """Update cost statistics. Prioritizes response-level cost from litellm,
        falls back to calculate_total_cost from token counts."""
        response_cost = 0.0
        if response is not None:
            # litellm ModelResponse stores cost in _hidden_params
            hidden = getattr(response, "_hidden_params", None)
            if hidden and isinstance(hidden, dict):
                response_cost = hidden.get("response_cost", 0) or 0

        if response_cost > 0:
            self.cost["total_cost"] = self.cost.get("total_cost", 0) + response_cost
        else:
            self.cost.update(calculate_total_cost(self.model_name, self.token_usage))

    def _set_last_finish_reason_from_response(self, response: Any) -> None:
        self.last_finish_reason = None
        choices = getattr(response, "choices", None)
        if not choices:
            return
        self.last_finish_reason = getattr(choices[0], "finish_reason", None)

    @async_log_llm_usage
    def _get_completion_content(
        self,
        messages: Messages,
        model: Optional[str] = None,
        response_format: Optional[Dict[str, Any]] = None,
        drop_params: Optional[bool] = None,
        streaming=False,
        type: Optional[str] = None,
        uuid: Optional[str] = None,
        organization_id: Optional[str] = None,
        current_activity: Optional[str] = None,
        reasoning_effort: Optional[str] = None,
        thinking_budget: Optional[int] = None,
        disable_thinking: Optional[bool] = None,
    ) -> str:
        """
        Get completion content with retries and fallbacks.

        Args:
            messages: List of message dictionaries.
            model: Optional model override.
            response_format: Optional response format specification.

        Returns:
            The completion text content.

        Raises:
            Exception: If all retry attempts and fallbacks fail.
        """
        tried_alternate_format = False
        payload = self._prepare_completion_payload(
            messages,
            response_format,
            model,
            reasoning_effort,
            thinking_budget,
            disable_thinking,
            drop_params,
        )
        # logging:
        try:
            _model = (
                payload.get("model") if isinstance(payload, dict) else self.model_name
            )
            _prov = self.provider

        except Exception:
            pass

        for attempt in range(MAX_RETRIES):
            try:
                if self.provider == "vertex_ai":
                    payload.pop("max_tokens", None)

                litellm.set_verbose = False

                # Handle different providers
                if self.provider == "vllm":
                    vllm_result = self._handle_vllm_completion(payload)
                    return str(vllm_result)
                elif self.provider == "protect":
                    protect_result = self._handle_protect_completion(payload)
                    # Return the full protect_result dict for DeterministicEvaluator
                    return protect_result
                elif self.provider == "protect_flash":
                    protect_flash_result = self._handle_protect_flash_completion(
                        payload
                    )
                    return protect_flash_result
                elif self.provider == "xai":
                    # For XAI, use OpenAI client directly instead of litellm
                    response = self.llm_client.chat.completions.create(
                        model=self.model_name,
                        messages=messages,  # type: ignore[arg-type]
                        max_tokens=self.max_tokens,
                        temperature=self.temperature,
                    )
                    self._set_last_finish_reason_from_response(response)
                    self._update_token_usage(response)
                    self._update_cost(response)
                    content = response.choices[0].message.content
                    return content if content is not None else ""

                # Default litellm handling for all other providers
                else:
                    gw_response = self._try_gateway_completion(payload)
                    if gw_response is not None:
                        self._set_last_finish_reason_from_response(gw_response)
                        self._update_token_usage(gw_response)
                        self._update_cost(gw_response)
                        content = gw_response.choices[0].message.content
                        return content if content is not None else ""

                    if self.provider == "turing":
                        _resolved = os.environ.get(f"{payload['model'].upper()}_MODEL")
                        if _resolved:
                            payload["model"] = _resolved

                    if streaming:
                        payload["stream"] = True
                        payload["stream_options"] = {"include_usage": True}
                    # run this *after* you build payload, before litellm.completion(**payload)

                    # 1) unwrap single-element tuples/lists on top-level fields
                    for k in ("temperature", "max_tokens", "model", "provider"):
                        v = payload.get(k)
                        if isinstance(v, (list, tuple)) and len(v) == 1:
                            payload[k] = v[0]

                    # Preprocess messages to convert audio_content to provider-specific format
                    # This fixes CORE-BACKEND-YR0 "Unsupported image format: mp3" errors
                    if "messages" in payload:
                        payload["messages"] = preprocess_messages_for_provider(
                            payload["messages"], self.provider
                        )

                    response = litellm.completion(
                        **payload,
                        timeout=LITELLM_REQUEST_TIMEOUT,
                        num_retries=LITELLM_NUM_RETRIES,
                        retry_strategy=LITELLM_RETRY_STRATEGY,
                    )
                    if streaming:
                        response_content = ""
                        message = {
                            "type": type,
                            "uuid": uuid,
                            "chunk": "",
                            "streaming_status": "started",
                            "current_activity": current_activity,
                        }
                        call_websocket(
                            organization_id,
                            message=message,
                            send_to_uuid=True,
                            uuid=uuid,
                        )
                        for i, chunk in enumerate(response):
                            try:
                                if chunk.choices and chunk.choices[0].delta:
                                    if chunk.choices[0].delta.content:
                                        chunk_message = chunk.choices[0].delta.content
                                        if chunk_message:
                                            message["chunk"] = chunk_message
                                            message["chunk_pos"] = str(i)
                                            message["streaming_status"] = "running"
                                            call_websocket(
                                                organization_id,
                                                message=message,
                                                send_to_uuid=True,
                                                uuid=uuid,
                                            )
                                            response_content += chunk_message
                            except Exception as e:
                                logger.exception(f"An error occurred: {str(e)}")
                                raise e
                        message["streaming_status"] = "completed"
                        message["chunk"] = response_content
                        call_websocket(
                            organization_id, message, send_to_uuid=True, uuid=uuid
                        )
                        response = chunk

                    self._set_last_finish_reason_from_response(response)
                    self._update_token_usage(response)
                    self._update_cost(response)
                    if streaming:
                        return response_content or ""
                    if not response.choices:
                        raise ValueError("Empty response from model")
                    content = response.choices[0].message.content
                    return content if content is not None else ""

            except Exception as e:
                logger.exception(
                    f"{self.provider} API error: {str(e)}",
                    attempt=attempt,
                    payload={**payload, "messages": "[REDACTED]"},
                )

                if self._should_try_alternate_format(
                    e, attempt, tried_alternate_format
                ):
                    messages = self._try_alternate_image_format(messages)
                    payload["messages"] = messages
                    tried_alternate_format = True
                    continue

                if attempt == MAX_RETRIES - 1:
                    return self._handle_final_fallback(messages, payload)

                time.sleep(RETRY_DELAY)

        raise ValueError("FAILED_TO_PROCESS_EVALUATION")

    def _get_completion_with_tools(
        self,
        messages: Messages,
        tools: list,
        model: Optional[str] = None,
        tool_choice: Optional[Any] = None,
        drop_params: Optional[bool] = None,
    ) -> Any:
        """
        Get completion with tool calling support. Returns the full response.

        Unlike _get_completion_content() which returns a string, this returns the
        full ModelResponse so callers can access tool_calls on the response message.
        Uses the same retry logic and provider infrastructure.

        Args:
            messages: List of message dictionaries.
            tools: List of tool definitions (OpenAI function calling format).
            model: Optional model override.
            drop_params: Whether to drop unsupported params.

        Returns:
            The full litellm ModelResponse object.

        Raises:
            Exception: If all retry attempts fail.
        """
        payload = self._prepare_completion_payload(
            messages,
            response_format=None,
            model=model,
            drop_params=drop_params,
        )
        payload["tools"] = tools
        if tool_choice is not None:
            payload["tool_choice"] = tool_choice

        for attempt in range(MAX_RETRIES):
            try:
                if self.provider == "vertex_ai":
                    payload.pop("max_tokens", None)

                litellm.set_verbose = False

                # Unwrap single-element tuples/lists on top-level fields
                for k in ("temperature", "max_tokens", "model", "provider"):
                    v = payload.get(k)
                    if isinstance(v, (list, tuple)) and len(v) == 1:
                        payload[k] = v[0]

                # Preprocess messages for provider (handles audio format conversion)
                if "messages" in payload:
                    payload["messages"] = preprocess_messages_for_provider(
                        payload["messages"], self.provider
                    )

                gw_response = self._try_gateway_completion(payload, tools=tools)
                if gw_response is not None:
                    self._set_last_finish_reason_from_response(gw_response)
                    self._update_token_usage(gw_response)
                    self._update_cost(gw_response)
                    if not gw_response.choices:
                        raise ValueError("Empty response from gateway")
                    return gw_response

                if self.provider == "turing":
                    _resolved = os.environ.get(f"{payload['model'].upper()}_MODEL")
                    if _resolved:
                        payload["model"] = _resolved

                response = litellm.completion(
                    **payload,
                    num_retries=LITELLM_NUM_RETRIES,
                    retry_strategy=LITELLM_RETRY_STRATEGY,
                )

                self._set_last_finish_reason_from_response(response)
                self._update_token_usage(response)
                self._update_cost(response)

                if not response.choices:
                    raise ValueError("Empty response from model")

                return response

            except Exception as e:
                logger.exception(
                    f"{self.provider} API error (tool completion): {str(e)}",
                    attempt=attempt,
                    payload={**payload, "messages": "[REDACTED]"},
                )

                if attempt == MAX_RETRIES - 1:
                    raise

                time.sleep(RETRY_DELAY)

        raise ValueError("FAILED_TO_PROCESS_TOOL_COMPLETION")

    async def _get_completion_content_async(
        self,
        messages: Messages,
        model: Optional[str] = None,
        response_format: Optional[Dict[str, Any]] = None,
        streaming: bool = False,
        type: Optional[str] = None,
        uuid: Optional[str] = None,
        organization_id: Optional[str] = None,
        current_activity: Optional[str] = None,
        reasoning_effort: Optional[str] = None,
        thinking_budget: Optional[int] = None,
        disable_thinking: Optional[bool] = None,
        ws_manager: Optional[Any] = None,
    ) -> str:
        """
        Async version of _get_completion_content that properly handles WebSocket streaming.

        Args:
            messages: List of message dictionaries.
            model: Optional model override.
            response_format: Optional response format specification.
            streaming: Whether to stream the response.
            type: Message type (e.g., "improve_id", "generation_id").
            uuid: UUID for tracking the request.
            organization_id: Organization ID.
            current_activity: Current activity name for progress tracking.
            ws_manager: WebSocketDirectManager instance for real-time updates.

        Returns:
            The completion text content.

        Raises:
            Exception: If all retry attempts and fallbacks fail.
        """
        tried_alternate_format = False
        payload = self._prepare_completion_payload(
            messages,
            response_format,
            model,
            reasoning_effort,
            thinking_budget,
            disable_thinking,
        )

        for attempt in range(MAX_RETRIES):
            try:
                if self.provider == "vertex_ai":
                    payload.pop("max_tokens", None)

                litellm.set_verbose = False

                # Handle different providers
                if self.provider == "vllm":
                    vllm_result = self._handle_vllm_completion(payload)
                    return str(vllm_result)
                elif self.provider == "protect":
                    protect_result = self._handle_protect_completion(payload)
                    return protect_result
                elif self.provider == "protect_flash":
                    protect_flash_result = self._handle_protect_flash_completion(
                        payload
                    )
                    return protect_flash_result
                elif self.provider == "xai":
                    # For XAI, use OpenAI client directly instead of litellm
                    response = await self.llm_client_async.chat.completions.create(
                        model=self.model_name,
                        messages=messages,  # type: ignore[arg-type]
                        max_tokens=self.max_tokens,
                        temperature=self.temperature,
                    )
                    self._update_token_usage(response)
                    self._update_cost(response)
                    content = response.choices[0].message.content
                    return content if content is not None else ""

                # Default litellm handling for all other providers
                else:
                    # Skip gateway for WebSocket streaming — gateway returns
                    # non-streaming responses, bypassing incremental chunk emission.
                    if not (streaming and ws_manager):
                        gw_response = await self._try_gateway_completion_async(payload)
                        if gw_response is not None:
                            self._update_token_usage(gw_response)
                            self._update_cost(gw_response)
                            content = gw_response.choices[0].message.content
                            return content if content is not None else ""

                    if streaming:
                        payload["stream"] = True
                        payload["stream_options"] = {"include_usage": True}

                    # Unwrap single-element tuples/lists on top-level fields
                    for k in ("temperature", "max_tokens", "model", "provider"):
                        v = payload.get(k)
                        if isinstance(v, (list, tuple)) and len(v) == 1:
                            payload[k] = v[0]

                    # Preprocess messages to convert audio_content to provider-specific format
                    # This fixes CORE-BACKEND-YR0 "Unsupported image format: mp3" errors
                    if "messages" in payload:
                        payload["messages"] = preprocess_messages_for_provider(
                            payload["messages"], self.provider
                        )

                    response = await litellm.acompletion(
                        **payload,
                        drop_params=True,
                        timeout=LITELLM_REQUEST_TIMEOUT,
                        num_retries=LITELLM_NUM_RETRIES,
                        retry_strategy=LITELLM_RETRY_STRATEGY,
                    )
                    if streaming and ws_manager:
                        response_content = ""
                        chunk_pos = 0

                        # Send started message
                        if type in ("improve_id", "improve_prompt"):
                            await ws_manager.send_improve_prompt_activity_message(
                                improve_id=uuid or "",
                                current_activity=current_activity or "",
                                status="started",
                            )
                        elif type in ("generation_id", "generate_prompt"):
                            await ws_manager.send_generate_prompt_activity_message(
                                generation_id=uuid or "",
                                current_activity=current_activity or "",
                                status="started",
                            )
                        else:
                            message = {
                                "type": type,
                                "uuid": uuid,
                                "chunk": "",
                                "streaming_status": "started",
                                "current_activity": current_activity,
                            }
                            await ws_manager._send_to_channel(message)

                        # Stream chunks
                        i = 0
                        async for chunk in response:
                            try:
                                if chunk.choices and chunk.choices[0].delta:
                                    if chunk.choices[0].delta.content:
                                        chunk_message = chunk.choices[0].delta.content
                                        if chunk_message:
                                            response_content += chunk_message
                                            chunk_pos = i

                                            # Check if streaming is stopped (for improve_prompt)
                                            if type in ("improve_id", "improve_prompt"):
                                                if await ws_manager.is_improve_prompt_stopped(
                                                    uuid or ""
                                                ):
                                                    await ws_manager.send_improve_prompt_activity_message(
                                                        improve_id=uuid or "",
                                                        current_activity=current_activity
                                                        or "",
                                                        status="stopped",
                                                        chunk=response_content,
                                                        chunk_pos=chunk_pos,
                                                    )
                                                    return response_content
                                            elif type in (
                                                "generation_id",
                                                "generate_prompt",
                                            ):
                                                if await ws_manager.is_generate_prompt_stopped(
                                                    uuid or ""
                                                ):
                                                    await ws_manager.send_generate_prompt_activity_message(
                                                        generation_id=uuid or "",
                                                        current_activity=current_activity
                                                        or "",
                                                        status="stopped",
                                                        chunk=response_content,
                                                        chunk_pos=chunk_pos,
                                                    )
                                                    return response_content

                                            # Send chunk via ws_manager
                                            if type in ("improve_id", "improve_prompt"):
                                                await ws_manager.send_improve_prompt_activity_message(
                                                    improve_id=uuid or "",
                                                    current_activity=current_activity
                                                    or "",
                                                    status="running",
                                                    chunk=chunk_message,
                                                    chunk_pos=chunk_pos,
                                                )
                                            elif type in (
                                                "generation_id",
                                                "generate_prompt",
                                            ):
                                                await ws_manager.send_generate_prompt_activity_message(
                                                    generation_id=uuid or "",
                                                    current_activity=current_activity
                                                    or "",
                                                    status="running",
                                                    chunk=chunk_message,
                                                    chunk_pos=chunk_pos,
                                                )
                                            else:
                                                message = {
                                                    "type": type,
                                                    "uuid": uuid,
                                                    "chunk": chunk_message,
                                                    "chunk_pos": str(chunk_pos),
                                                    "streaming_status": "running",
                                                    "current_activity": current_activity,
                                                }
                                                await ws_manager._send_to_channel(
                                                    message
                                                )
                            except Exception as e:
                                logger.exception(
                                    f"An error occurred during streaming: {str(e)}"
                                )
                                raise e
                            finally:
                                i += 1

                        # Send completed message
                        if type in ("improve_id", "improve_prompt"):
                            await ws_manager.send_improve_prompt_activity_message(
                                improve_id=uuid or "",
                                current_activity=current_activity or "",
                                status="completed",
                                chunk=response_content,
                                chunk_pos=chunk_pos,
                            )
                        elif type in ("generation_id", "generate_prompt"):
                            await ws_manager.send_generate_prompt_activity_message(
                                generation_id=uuid or "",
                                current_activity=current_activity or "",
                                status="completed",
                                chunk=response_content,
                                chunk_pos=chunk_pos,
                            )
                        else:
                            message = {
                                "type": type,
                                "uuid": uuid,
                                "chunk": response_content,
                                "streaming_status": "completed",
                                "current_activity": current_activity,
                            }
                            await ws_manager._send_to_channel(message)

                        self._update_token_usage(chunk)
                        self._update_cost()
                        return response_content or ""

                    self._update_token_usage(response)
                    self._update_cost(response)
                    if not response.choices:
                        raise ValueError("Empty response from model")
                    content = response.choices[0].message.content
                    return content if content is not None else ""

            except Exception as e:
                logger.exception(
                    "API error",
                    provider=self.provider,
                    error=str(e),
                    attempt=attempt,
                    payload={**payload, "messages": "[REDACTED]"},
                )

                if self._should_try_alternate_format(
                    e, attempt, tried_alternate_format
                ):
                    messages = self._try_alternate_image_format(messages)
                    payload["messages"] = messages
                    tried_alternate_format = True
                    continue

                if attempt == MAX_RETRIES - 1:
                    return await self._handle_final_fallback_async(messages, payload)

                await asyncio.sleep(RETRY_DELAY)

        raise ValueError("FAILED_TO_PROCESS_EVALUATION")

    async def _handle_final_fallback_async(
        self, messages: Messages, payload: Payload
    ) -> str:
        """Async version of final fallback handling."""
        payload = dict(payload)
        payload["drop_params"] = True
        payload.pop("max_output_tokens", None)
        payload.pop("thinking", None)
        original_max_tokens = payload.get("max_tokens")

        vertex_cfg = ModelConfigs.VERTEX_GEMINI_2_5_PRO
        openai_cfg = ModelConfigs.OPENAI_GPT_5_1

        base_messages = payload.get("messages", messages)

        try:
            logger.info("Final fallback: attempting Vertex Gemini 2.5 Pro")
            vertex_payload = dict(payload)
            vertex_payload["model"] = vertex_cfg.model_name
            vertex_payload["temperature"] = vertex_cfg.temperature
            vertex_payload["max_tokens"] = min(
                int(original_max_tokens or vertex_cfg.max_tokens),
                vertex_cfg.max_tokens,
            )
            vertex_payload["messages"] = preprocess_messages_for_provider(
                base_messages, vertex_cfg.provider
            )
            response = await litellm.acompletion(
                **vertex_payload,
                num_retries=LITELLM_NUM_RETRIES,
                retry_strategy=LITELLM_RETRY_STRATEGY,
            )
            self._update_token_usage(response)
            self._update_cost(response)
            if not response.choices:
                raise ValueError("Empty response from model")
            content = response.choices[0].message.content
            return content if content is not None else ""
        except Exception as vertex_error:
            logger.exception(
                f"Vertex fallback error: {str(vertex_error)}",
                payload={**payload, "messages": "[REDACTED]"},
            )

        try:
            logger.info("Final fallback: attempting OpenAI GPT-5.1")
            openai_payload = dict(payload)
            openai_payload["model"] = openai_cfg.model_name
            openai_payload["temperature"] = openai_cfg.temperature
            openai_payload["max_tokens"] = min(
                int(original_max_tokens or openai_cfg.max_tokens),
                openai_cfg.max_tokens,
            )
            openai_messages = base_messages
            if self.provider == "vertex_ai":
                openai_messages = self.handle_vertex_ai_fallback(openai_messages)
            openai_payload["messages"] = preprocess_messages_for_provider(
                openai_messages, openai_cfg.provider
            )
            response = await litellm.acompletion(
                **openai_payload,
                num_retries=LITELLM_NUM_RETRIES,
                retry_strategy=LITELLM_RETRY_STRATEGY,
            )
            self._update_token_usage(response)
            self._update_cost(response)
            if not response.choices:
                raise ValueError("Empty response from model")
            content = response.choices[0].message.content
            return content if content is not None else ""
        except Exception as openai_error:
            logger.exception(f"OpenAI fallback error: {str(openai_error)}")
            logger.error("Max retries reached for all APIs")
            raise ValueError("FAILED_TO_PROCESS_EVALUATION")

    def _prepare_completion_payload(
        self,
        messages: Messages,
        response_format: Optional[Dict[str, Any]],
        model: Optional[str] = None,
        reasoning_effort: Optional[str] = None,
        thinking_budget: Optional[int] = None,
        disable_thinking: Optional[bool] = None,
        drop_params: Optional[bool] = None,
    ) -> Payload:
        """Prepare the completion payload based on provider and configuration."""
        _max_tokens = self.max_tokens
        _model = model if model else self.model_name

        # Safeguard: cap max_tokens based on model limits.
        # First check internal ModelConfigs, then litellm's model registry.
        threshold = ModelConfigs.get_max_tokens(_model)
        if not threshold:
            try:
                info = litellm.get_model_info(_model)
                max_output = info.get("max_output_tokens") or info.get("max_tokens")
                if max_output:
                    threshold = max_output
            except Exception:
                pass
        if threshold and _max_tokens > threshold:
            _max_tokens = threshold

        payload = {
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": _max_tokens,
            "model": _model,
        }

        if response_format:
            payload["response_format"] = response_format

        if drop_params is True:
            payload["drop_params"] = True

        if reasoning_effort:
            payload["reasoning_effort"] = reasoning_effort

        # Set provider-specific model and parameters
        if self.provider == "vertex_ai":
            payload.update({"max_output_tokens": self.max_tokens})
            if disable_thinking:
                payload["thinking"] = {"type": "disabled"}
            elif thinking_budget is not None:
                payload["thinking"] = {
                    "type": "enabled",
                    "budget_tokens": thinking_budget,
                }
            if messages and isinstance(messages, list) and len(messages) > 0:
                first_msg = messages[0] if messages else {}
                if isinstance(first_msg, dict) and "parts" in first_msg:
                    messages_list = [
                        {
                            "role": msg.get("role", "user"),
                            "content": msg.get("parts", ""),
                        }
                        for msg in messages
                        if isinstance(msg, dict)
                    ]
                    payload["messages"] = [
                        {
                            "role": "system",
                            "content": AUDIO_AGENT_LLM_SYSTEM_INSTRUCTION,
                        }
                    ] + messages_list

        # ------------- PROTECT MULTI-MODAL -------------
        if self.provider == "protect":
            # Handle protect mode where messages might be a dict or list
            # Type narrowing for protect mode
            messages_for_protect: Any = messages
            if isinstance(messages_for_protect, dict):
                inputs = messages_for_protect.get("inputs") or []
                input_types = messages_for_protect.get("input_type") or []
                # get the first input and the first input_type.
                inp = inputs[0] if inputs else None
                itype = (
                    (input_types[0] or "text").strip().lower()
                    if input_types
                    else "text"
                )
            else:
                inp = None
                itype = "text"
            metric = (model or self.model_name or "toxicity").strip().lower()
            if metric not in ("toxicity", "bias", "privacy", "prompt_injection"):
                metric = "toxicity"

            suffix_style = "explanation_assistant"  # One of {"vanilla", "thinking_assistant", "explanation_assistant", "both_tokens"}
            payload["messages"] = build_mm_messages_for_protect(
                metric=metric,
                input_type=itype,
                input_value=inp or "",
                suffix_style=suffix_style,
            )

        if self.provider == "protect_flash":
            messages_for_protect_flash: Any = messages
            if isinstance(messages_for_protect_flash, dict):
                inputs = messages_for_protect_flash.get("inputs") or []
                input_types = messages_for_protect_flash.get("input_type") or []
                inp = str(inputs[0]) if inputs else ""
                itype = (
                    (input_types[0] or "text").strip().lower()
                    if input_types
                    else "text"
                )
            elif isinstance(messages_for_protect_flash, list):
                user_text = ""
                for msg in messages_for_protect_flash:
                    if isinstance(msg, dict) and msg.get("role") == "user":
                        content = msg.get("content", "")
                        user_text = (
                            content if isinstance(content, str) else str(content)
                        )
                        break
                inp = user_text
                itype = "text"
            else:
                inp = ""
                itype = "text"

            if itype == "audio" and inp:
                inp = self.transcribe_audio(inp)

            payload["messages"] = [
                {
                    "role": "user",
                    "content": self._build_protect_flash_prompt(prompt=inp),
                }
            ]

        return payload

    def _build_protect_flash_prompt(self, prompt: str, response: str = "") -> str:
        return PROTECT_FLASH_PROMPT_TEMPLATE.format(
            prompt=prompt,
            response=response or "[No response provided]",
        )

    def _parse_protect_flash_output(self, output: str) -> Dict[str, str]:
        match = re.match(r".*:(.*)\n.*:(.*)\n.*:(.*)", output)
        if match is None:
            return {
                "prompt_harmfulness": "parse_error",
                "response_refusal": "parse_error",
                "response_harmfulness": "parse_error",
            }

        harmful_request, response_refusal, harmful_response = match.groups()
        return {
            "prompt_harmfulness": (
                "yes" if harmful_request.strip().lower() == "yes" else "no"
            ),
            "response_refusal": (
                "yes"
                if response_refusal.strip().lower() == "yes"
                else ("no" if response_refusal.strip().lower() == "no" else "n/a")
            ),
            "response_harmfulness": (
                "yes"
                if harmful_response.strip().lower() == "yes"
                else ("no" if harmful_response.strip().lower() == "no" else "n/a")
            ),
        }

    def _should_try_alternate_format(
        self, error: Exception, attempt: int, tried_alternate: bool
    ) -> bool:
        """Determine if alternate format should be tried based on error and attempt."""
        return (
            not tried_alternate
            and not "timeout" in str(error).lower()
            and "image" in str(error).lower()
            and attempt == 5
        )

    def _handle_protect_completion(self, payload: Payload) -> Dict[str, Any]:
        metric = (payload.get("model") or self.model_name or "toxicity").strip().lower()
        if metric not in ("toxicity", "bias", "privacy", "prompt_injection"):
            metric = "toxicity"

        if not hasattr(self, "protect_urls") or not self.protect_urls:
            raise ValueError(
                "'protect_urls' argument not found. Unable to reach the server."
            )

        metric = (metric or "").strip().lower()
        allowed_metrics = {"toxicity", "bias", "privacy", "prompt_injection"}
        url_key = metric if metric in allowed_metrics else "toxicity"
        protect_url = self.protect_urls.get(url_key)
        base_url = (
            (protect_url.rstrip("/") if isinstance(protect_url, str) else "")
            if protect_url
            else ""
        )
        if not base_url:
            raise ValueError(
                f"Protect route for metric '{metric}' not found in self.protect_urls (key tried: '{url_key}')."
            )

        # Choose model id via env; keep your overrides
        model_env = {
            "toxicity": os.getenv("VLLM_PROTECT_TOXICITY_MODEL", ""),
            "bias": os.getenv("VLLM_PROTECT_BIAS_MODEL", ""),
            "privacy": os.getenv("VLLM_PROTECT_PRIVACY_MODEL", ""),
            "prompt_injection": os.getenv("VLLM_PROTECT_PROMPT_INJECTION_MODEL", ""),
        }
        model_name = model_env.get(metric, model_env["toxicity"])

        url = f"{base_url}/v1/chat/completions"
        req = {
            "model": model_name,
            "messages": payload[
                "messages"
            ],  # message blocks from _prepare_completion_payload
            "max_tokens": 150,
            "temperature": 0.0,
            "stream": False,
        }

        protect_api_key = getattr(self, "protect_api_key", "")
        headers = {"Content-Type": "application/json"}
        if protect_api_key:
            headers["Authorization"] = f"Bearer {protect_api_key}"
        logger.info("protect_completion_request_started", metric=metric)

        try:
            resp = requests.post(
                url,
                headers=headers,
                json=req,
                timeout=(10, 60),  # connect=10s, read=60s
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.exception(
                "protect_completion_request_failed", metric=metric, error=str(e)
            )
            raise Exception(f"Protect call failed: {str(e)}")

        usage = data.get("usage") or {}

        # Extract content
        choices = data.get("choices") or []
        content = ""
        if choices:
            choice0 = choices[0] or {}
            msg = choice0.get("message") or {}
            content = msg.get("content") or choice0.get("text") or ""

            if not content:
                logger.warning(
                    "protect_response_empty_content",
                    metric=metric,
                    finish_reason=choice0.get("finish_reason"),
                    usage_prompt_tokens=usage.get("prompt_tokens"),
                    usage_completion_tokens=usage.get("completion_tokens"),
                    choice_keys=list(choice0.keys()),
                )

                retry_req = dict(req)
                retry_req["max_tokens"] = 300
                try:
                    retry_resp = requests.post(
                        url,
                        headers=headers,
                        json=retry_req,
                        timeout=(10, 60),
                    )
                    retry_resp.raise_for_status()
                    retry_data = retry_resp.json()
                    retry_choices = retry_data.get("choices") or []
                    if retry_choices:
                        retry_choice0 = retry_choices[0] or {}
                        retry_msg = retry_choice0.get("message") or {}
                        content = (
                            retry_msg.get("content") or retry_choice0.get("text") or ""
                        )
                except Exception as retry_exc:
                    logger.warning(
                        "protect_response_retry_failed",
                        metric=metric,
                        error=str(retry_exc),
                        usage_prompt_tokens=usage.get("prompt_tokens"),
                        usage_completion_tokens=usage.get("completion_tokens"),
                    )
        # Parse <label> + <explanation>
        label_regex = re.compile(
            r"<\s*label\b[^>]*>\s*['\"`]*\s*(passed|failed)\s*['\"`]*\s*</\s*label\s*>",
            re.I | re.S,
        )
        expl_regex = re.compile(
            r"<\s*explanation\b[^>]*>(.*?)</\s*explanation\s*>", re.I | re.S
        )

        m_label = label_regex.search(content or "")
        label = m_label.group(1).capitalize() if m_label else "Failed"

        m_expl = expl_regex.search(content or "")
        explanation = m_expl.group(1).strip() if m_expl else ""

        return {"choices": [label], "explanation": explanation, "usage": 0}

    def _handle_vllm_completion(self, payload: Payload) -> str:
        """Handle VLLM-specific completion logic."""
        prompt_text = ""
        for msg in payload["messages"]:
            if msg["role"] == "user":
                prompt_text += f"{msg['content']}\n"

        model_name = payload["model"]
        if self.provider == "protect_flash":
            model_name = getattr(self, "vllm_model", "") or os.getenv(
                "VLLM_PROTECT_FLASH_MODEL_NAME", "protect_flash"
            )

        vllm_payload = {
            "model": model_name,
            "prompt": prompt_text,
            "max_tokens": payload.get("max_tokens", self.max_tokens),
            "temperature": payload.get("temperature", self.temperature),
        }

        response = requests.post(
            f"{self.server_url}/v1/completions", json=vllm_payload, timeout=30
        )

        if response.status_code == 200:
            result = response.json()
            text = result.get("choices", [{}])[0].get("text", "")
            return text if text is not None else ""
        else:
            raise Exception(
                f"VLLM server error: {response.status_code} - {response.text}"
            )

    def _handle_protect_flash_completion(self, payload: Payload) -> Dict[str, Any]:
        raw_output = self._handle_vllm_completion(payload)
        parsed = self._parse_protect_flash_output(raw_output)
        is_harmful = parsed.get("prompt_harmfulness") == "yes"
        explanation = (
            f"Content detected as harmful. Prompt harmfulness: {parsed.get('prompt_harmfulness', 'no')}"
            if is_harmful
            else "Content appears to be safe and appropriate."
        )
        return {
            "is_harmful": is_harmful,
            "choices": ["Failed" if is_harmful else "Passed"],
            "explanation": explanation,
            "prompt_harmful": is_harmful,
            "response_harmful": None,
            "details": parsed,
            "model": self.model_name,
            "parsed": parsed,
            "raw_output": raw_output,
        }

    def _handle_final_fallback(self, messages: Messages, payload: Payload) -> str:
        """Handle final fallback attempts when primary provider fails."""
        payload = dict(payload)
        payload["drop_params"] = True
        payload.pop("max_output_tokens", None)
        payload.pop("thinking", None)
        original_max_tokens = payload.get("max_tokens")

        vertex_cfg = ModelConfigs.VERTEX_GEMINI_2_5_PRO
        openai_cfg = ModelConfigs.OPENAI_GPT_5_1

        base_messages = payload.get("messages", messages)

        try:
            logger.info("Final fallback: attempting Vertex Gemini 2.5 Pro")
            vertex_payload = dict(payload)
            vertex_payload["model"] = vertex_cfg.model_name
            vertex_payload["temperature"] = vertex_cfg.temperature
            vertex_payload["max_tokens"] = min(
                int(original_max_tokens or vertex_cfg.max_tokens),
                vertex_cfg.max_tokens,
            )
            vertex_payload["messages"] = preprocess_messages_for_provider(
                base_messages, vertex_cfg.provider
            )
            response = litellm.completion(
                **vertex_payload,
                num_retries=LITELLM_NUM_RETRIES,
                retry_strategy=LITELLM_RETRY_STRATEGY,
            )
            self._update_token_usage(response)
            self._update_cost(response)
            if not response.choices:
                raise ValueError("Empty response from model")
            content = response.choices[0].message.content
            return content if content is not None else ""
        except Exception as vertex_error:
            logger.exception(
                f"Vertex fallback error: {str(vertex_error)}",
                payload={**payload, "messages": "[REDACTED]"},
            )

        try:
            logger.info("Final fallback: attempting OpenAI GPT-5.1")
            openai_payload = dict(payload)
            openai_payload["model"] = openai_cfg.model_name
            openai_payload["temperature"] = openai_cfg.temperature
            openai_payload["max_tokens"] = min(
                int(original_max_tokens or openai_cfg.max_tokens),
                openai_cfg.max_tokens,
            )
            openai_messages = base_messages
            if self.provider == "vertex_ai":
                openai_messages = self.handle_vertex_ai_fallback(openai_messages)
            openai_payload["messages"] = preprocess_messages_for_provider(
                openai_messages, openai_cfg.provider
            )
            response = litellm.completion(
                **openai_payload,
                num_retries=LITELLM_NUM_RETRIES,
                retry_strategy=LITELLM_RETRY_STRATEGY,
            )
            self._update_token_usage(response)
            self._update_cost(response)
            if not response.choices:
                raise ValueError("Empty response from model")
            content = response.choices[0].message.content
            return content if content is not None else ""
        except Exception as openai_error:
            logger.exception(f"OpenAI fallback error: {str(openai_error)}")
            logger.error("Max retries reached for all APIs")
            raise ValueError("FAILED_TO_PROCESS_EVALUATION")

    def _try_alternate_image_format(self, messages):
        new_messages = []
        for msg in messages:
            if "content" in msg:
                new_content = []
                for content in msg["content"]:
                    if content.get("type") == "image_url":
                        # Convert to base64 format
                        try:
                            url = content["image_url"]["url"]
                            new_content.append(
                                {
                                    "type": "image",
                                    "source": {
                                        "type": "base64",
                                        "media_type": "image/jpeg",
                                        "data": download_image_to_base64(url),
                                    },
                                }
                            )
                        except:
                            # If conversion fails, keep original format
                            new_content.append(content)
                    elif (
                        content.get("type") == "image"
                        and content.get("source", {}).get("type") == "base64"
                    ):
                        # Convert to image_url format
                        try:
                            data = content["source"]["data"]
                            url = upload_image_to_s3(
                                data
                            )  # You'll need to implement this
                            new_content.append(
                                {"type": "image_url", "image_url": {"url": url}}
                            )
                        except:
                            # If conversion fails, keep original format
                            new_content.append(content)
                    else:
                        new_content.append(content)
                msg["content"] = new_content
            new_messages.append(msg)
        return new_messages

    def handle_vertex_ai_fallback(self, messages):
        """
        Convert Vertex AI message format to OpenAI format.
        Vertex AI uses a different structure than OpenAI for multimodal content.
        """
        new_messages = []
        for msg in messages:
            if "role" in msg and "content" in msg:
                role = msg["role"]
                new_content = []

                # Handle different content formats
                if isinstance(msg["content"], list):
                    # Process each content item in the list
                    for content_item in msg["content"]:
                        if content_item.get("type") == "image_url":
                            url = content_item.get("image_url", {}).get("url", "")
                            # Convert audio passed via image_url into OpenAI input_audio.
                            # Preserve actual images as image_url blocks.
                            if isinstance(url, str) and url.startswith("data:audio/"):
                                try:
                                    _header, data = url.split(",", 1)
                                except ValueError:
                                    data = url
                                new_content.append(
                                    {
                                        "type": "input_audio",
                                        "input_audio": {"data": data, "format": "mp3"},
                                    }
                                )
                            else:
                                new_content.append(content_item)
                        else:
                            # Keep other content types as is
                            new_content.append(content_item)
                elif isinstance(msg["content"], dict):
                    # Convert single content item
                    content_item = msg["content"]
                    if content_item.get("type") == "text":
                        new_content.append(
                            {"type": "text", "text": content_item.get("text", "")}
                        )
                    elif content_item.get("type") == "audio_input":
                        audio_url = content_item.get("audio_input", {}).get(
                            "audio_input", ""
                        )
                        if audio_url:
                            new_content.append(
                                {
                                    "type": "input_audio",
                                    "input_audio": {
                                        "data": audio_url,  # Already base64 encoded
                                        "format": "mp3",  # Assuming mp3 format, adjust if needed
                                    },
                                }
                            )
                else:
                    # Simple string content
                    new_content.append({"type": "text", "text": str(msg["content"])})

                new_messages.append({"role": role, "content": new_content})
            else:
                # Pass through any messages that don't match expected format
                new_messages.append(msg)

        return new_messages

    def run(self, **kwargs) -> BatchRunResult:
        """
        Run a single LLM evaluation.

        Args:
            **kwargs: Evaluation parameters.

        Returns:
            BatchRunResult containing the evaluation result.
        """
        eval_result = self._evaluate(**kwargs)  # type: ignore[attr-defined]
        return BatchRunResult(
            eval_request_id="eval_request_id",
            eval_results=[eval_result],
        )

    def run_batch(
        self,
        data: List[Dict[str, Any]],
        max_parallel_evals: int = 5,
        upload_to_fi: bool = False,
    ) -> BatchRunResult:
        """
        Run evaluations on a batch of data.

        Args:
            data: List of dictionaries containing evaluation parameters.
            max_parallel_evals: Maximum number of parallel evaluations.
            upload_to_fi: Whether to upload results to FI.

        Returns:
            BatchRunResult containing all evaluation results.
        """
        if max_parallel_evals > 1:
            eval_results = self._run_batch_generator_async(data, max_parallel_evals)
        else:
            eval_results = list(self._run_batch_generator(data))

        return BatchRunResult(eval_results=eval_results)

    def _run_batch_generator_async(
        self, data: List[Dict[str, Any]], max_parallel_evals: int
    ) -> List[Optional[Any]]:
        """
        Run batch evaluations asynchronously.

        Args:
            data: List of evaluation parameters.
            max_parallel_evals: Maximum number of parallel evaluations.

        Returns:
            List of evaluation results in original order.
        """
        # Wrap function with OTel context propagation for thread safety
        wrapped_evaluate = wrap_for_thread(self._evaluate)

        with ThreadPoolExecutor(max_workers=max_parallel_evals) as executor:
            # Submit all tasks to the executor and store them with their original index
            future_to_index = {
                executor.submit(wrapped_evaluate, **entry): i  # type: ignore[attr-defined]
                for i, entry in enumerate(data)
            }

            # Create a list to store results in the original order
            results = [None] * len(data)

            for future in as_completed(future_to_index):
                index = future_to_index[future]
                try:
                    results[index] = future.result()
                except Exception as e:
                    logger.error(f"Error running batch async at index {index}: {e}")
                    traceback.print_exc()
                    results[index] = None

            return results

    def _run_batch_generator(self, data: List[Dict[str, Any]]):
        """
        Generator function for running batch evaluations sequentially.

        Args:
            data: List of evaluation parameters.

        Yields:
            Evaluation results or None if evaluation fails.
        """
        for entry in data:
            try:
                yield self._evaluate(**entry)  # type: ignore[attr-defined]
            except Exception as e:
                logger.error(f"Error evaluating entry {entry}: {e}")
                traceback.print_exc()
                yield None

    def validate_args(self, **kwargs) -> None:
        """
        Validate that all required arguments are present and not None.

        Args:
            **kwargs: Arguments to validate.

        Raises:
            ValueError: If any required argument is missing or None.
        """
        for arg in self.required_args:  # type: ignore[attr-defined]
            if arg not in kwargs:
                raise ValueError(f"Missing required argument: {arg}")
            elif kwargs[arg] is None:
                raise ValueError(f"{arg} cannot be None")

    def transcribe_audio(self, audio_input: AudioInput) -> str:
        """
        Transcribe audio input using appropriate API.

        Tries Groq via litellm first, then falls back to OpenAI.

        Args:
            audio_input: Can be a file path, URL, Base64 encoded string, or raw audio bytes.

        Returns:
            The transcribed text.

        Raises:
            ValueError: If audio input is invalid or transcription fails.
        """
        try:
            logger.info("Starting audio transcription")
            audio_bytes = self._process_audio_input(audio_input)

            # Try Groq transcription first
            try:
                logger.info("Attempting Groq transcription")
                transcription = self._try_groq_transcription(audio_bytes)
                logger.info("Groq transcription successful")
                return transcription
            except Exception as e:
                logger.warning(f"Groq transcription failed: {str(e)}")

                # Fallback to OpenAI
                try:
                    logger.info("Attempting OpenAI transcription")
                    transcription = self._try_openai_transcription(audio_bytes)
                    logger.info("OpenAI transcription successful")
                    return transcription
                except Exception as e2:
                    error_msg = f"All transcription methods failed: Groq: {str(e)}, OpenAI: {str(e2)}"
                    logger.error(error_msg)
                    raise ValueError(error_msg)

        except Exception as e:
            logger.error(f"Transcription failed: {str(e)}")
            traceback.print_exc()
            return f"[Transcription failed: {str(e)}]"

    def _process_audio_input(self, audio_input: AudioInput) -> bytes:
        """
        Process audio input into bytes.

        Args:
            audio_input: Audio input in various formats.

        Returns:
            Audio data as bytes.

        Raises:
            ValueError: If input format is invalid.
        """
        if isinstance(audio_input, str):
            if audio_input.startswith("data:audio/") and ";base64," in audio_input:
                logger.info("Processing data URI audio format")
                base64_data = audio_input.split(";base64,")[1]
                try:
                    return base64.b64decode(base64_data, validate=True)
                except Exception as ex:
                    raise ValueError(
                        f"Failed to decode base64 data from URI: {str(ex)}"
                    )

            abs_path = os.path.abspath(audio_input)
            if os.path.exists(abs_path):
                logger.info("Processing local audio file")
                with open(abs_path, "rb") as f:
                    return f.read()
            elif audio_input.startswith(("http://", "https://")):
                logger.info("Processing audio URL")
                audio_bytes_from_url = download_audio_from_url(audio_input)
                return (
                    audio_bytes_from_url
                    if isinstance(audio_bytes_from_url, bytes)
                    else b""
                )
            else:
                try:
                    candidate_bytes = base64.b64decode(audio_input, validate=True)
                    if self._is_valid_audio_format(candidate_bytes):
                        logger.info("Processing base64 audio data")
                        return candidate_bytes
                    else:
                        logger.warning(
                            "Base64 string decoded but no common audio signature identified"
                        )
                        return candidate_bytes
                except Exception as ex:
                    raise ValueError(
                        "Audio input is not a valid file path, URL, or Base64 encoded audio string"
                    )

        elif isinstance(audio_input, bytes):
            logger.info("Processing raw audio bytes")
            return audio_input
        else:
            raise ValueError("Unsupported audio input type. Expected str or bytes.")

    def _is_valid_audio_format(self, data: bytes) -> bool:
        """Check if bytes contain valid audio format signatures."""
        return (
            (data.startswith(b"RIFF") and b"WAVE" in data[0:12])
            or data.startswith(b"ID3")
            or data.startswith(b"OggS")
            or data.startswith(b"fLaC")
            or data.startswith(b"\xff\xf1")
            or data.startswith(b"\xff\xf9")
        )

    def _try_groq_transcription(self, audio_bytes: bytes) -> str:
        """Attempt transcription using Groq."""
        transcription = litellm.transcription(
            model="groq/whisper-large-v3",
            file=("audio.wav", audio_bytes),
            prompt="",
            temperature=0,
            response_format="json",
        )
        return str(transcription.text) if hasattr(transcription, "text") else ""

    def _try_openai_transcription(self, audio_bytes: bytes) -> str:
        """Attempt transcription using OpenAI."""
        openai_api_key = os.environ.get("OPENAI_API_KEY") or getattr(
            self, "provider_api_keys", {}
        ).get("openai")
        if not openai_api_key:
            raise ValueError(
                "OPENAI_API_KEY not found in environment or provider_api_keys"
            )

        openai_client = OpenAI(api_key=openai_api_key)
        response = openai_client.audio.transcriptions.create(
            model="whisper-1", file=("audio.wav", audio_bytes)
        )
        return response.text

    def call_llm(self, prompt: Messages, provider: str) -> str:
        """
        Call the LLM with the given prompt.

        Routes through Agentcc gateway when available, falls back to litellm.
        Gateway handles: routing, fallback, cost tracking, rate limiting.

        Args:
            prompt: List of message dictionaries.

        Returns:
            The LLM response text.
        """
        logger.info(
            "llm_call_start",
            model=self.model_name,
            provider=provider,
            message_count=len(prompt) if isinstance(prompt, list) else 1,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
        )

        # Try gateway first — but only when the caller has NOT supplied their
        # own API key. When api_key is set the request must go through the
        # customer's own provider credentials via litellm, not our gateway.
        if not getattr(self, "api_key", None):
            try:
                try:
                    from ee.usage.services.gateway_llm_client import get_gateway_client
                except ImportError:
                    get_gateway_client = None

                gateway = get_gateway_client()
                if gateway is not None:
                    logger.info("llm_call_routing", route="agentcc_gateway", model=self.model_name)
                    response = gateway.chat.completions.create(
                        model=self.model_name,
                        messages=prompt,
                        temperature=self.temperature,
                        max_tokens=self.max_tokens,
                    )
                    self._update_token_usage(response)
                    self._update_cost(response)
                    content = response.choices[0].message.content
                    logger.info(
                        "llm_call_complete",
                        model=self.model_name,
                        route="agentcc_gateway",
                        prompt_tokens=self.token_usage.get("prompt_tokens"),
                        completion_tokens=self.token_usage.get("completion_tokens"),
                        total_cost=self.cost.get("total_cost"),
                        response_length=len(content) if content else 0,
                    )
                    return content if content is not None else ""
            except ImportError:
                pass  # usage module not available — fall through to litellm
            except Exception as e:
                logger.debug(f"Gateway call failed, falling back to litellm: {str(e)}")

        # Fallback: existing litellm path
        try:
            payload = {
                "model": self.model_name,
                "messages": prompt,
                "temperature": self.temperature,
            }
            if not getattr(self, "api_key", None):
                payload["max_tokens"] = self.max_tokens

            # Handle API key scenarios
            if isinstance(self.api_key, dict):
                payload["custom_llm_provider"] = provider
                if provider == "bedrock" or provider.startswith("azure"):
                    payload.update(self.api_key)
                elif provider.startswith("vertex_ai"):
                    vertex_location = (
                        self.api_key.get("location")
                        if isinstance(self.api_key, dict)
                        else None
                    )
                    creds = (
                        {k: v for k, v in self.api_key.items() if k != "location"}
                        if isinstance(self.api_key, dict)
                        else self.api_key
                    )
                    payload["vertex_credentials"] = json.dumps(creds)
                    if vertex_location:
                        payload["vertex_location"] = vertex_location
                elif provider == "openai":
                    if "key" in self.api_key:
                        api_key_dict = self.api_key.copy()
                        if "api_base" in api_key_dict:
                            api_key_dict["api_key"] = api_key_dict.pop("key")
                            payload.update(api_key_dict)
                        else:
                            key_value = api_key_dict.get("key")
                            if isinstance(key_value, str):
                                payload["api_key"] = key_value

                response = litellm.completion(
                    **payload,
                    num_retries=LITELLM_NUM_RETRIES,
                    retry_strategy=LITELLM_RETRY_STRATEGY,
                )
            else:
                response = litellm.completion(
                    **payload,
                    api_key=self.api_key,
                    num_retries=LITELLM_NUM_RETRIES,
                    retry_strategy=LITELLM_RETRY_STRATEGY,
                )

            self._update_token_usage(response)
            self._update_cost(response)
            content = response.choices[0].message.content
            logger.info(
                "llm_call_complete",
                model=self.model_name,
                provider=provider,
                prompt_tokens=self.token_usage.get("prompt_tokens"),
                completion_tokens=self.token_usage.get("completion_tokens"),
                total_cost=self.cost.get("total_cost"),
                response_length=len(content) if content else 0,
            )
            return content if content is not None else ""
        except Exception as e:
            # Walk the exception chain to find the provider error.
            # traceai_litellm can crash with TypeError/json errors that
            # mask the real provider error underneath.
            root_msg = str(e)
            current = e
            while current.__cause__ or current.__context__:
                current = current.__cause__ or current.__context__
                if not isinstance(current, (TypeError, KeyError)):
                    root_msg = str(current)
                    break
            # Strip any litellm references from user-facing message
            import re as _re
            root_msg = _re.sub(r"litellm\.\w+:\s*", "", root_msg)
            root_msg = _re.sub(r"litellm\.\w+", "", root_msg)
            logger.error(
                "llm_call_failed",
                model=self.model_name,
                provider=provider,
                error=root_msg,
                error_type=type(current).__name__,
                exc_info=True,
            )
            raise ValueError(f"LLM call failed ({self.model_name}): {root_msg}") from e
