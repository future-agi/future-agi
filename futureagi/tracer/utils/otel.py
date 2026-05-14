import json
import re
import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Optional

import structlog
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone
from litellm import cost_per_token, model_cost

from accounts.models import Organization
from accounts.models.workspace import Workspace

logger = structlog.get_logger(__name__)
from agentic_eval.core_evals.fi_evals import *  # noqa: F403
from model_hub.models.ai_model import AIModel
from model_hub.models.choices import StatusType
from model_hub.models.custom_models import CustomAIModel
from model_hub.models.evals_metric import EvalTemplate
from tfc.utils.storage import upload_audio_to_s3, upload_image_to_s3, upload_video_to_s3
from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.models.observation_span import ObservationSpan, UserIdType
from tracer.models.project import PROJECT_TYPES, Project, ProjectSourceChoices
from tracer.models.project_version import ProjectVersion
from tracer.utils.helper import (
    get_default_project_version_config,
    get_default_trace_config,
)
from tracer.utils.semantic_conventions import (
    AttributeRegistry,
    detect_semconv,
    get_attribute,
)

try:
    from ee.usage.models.usage import APICallStatusChoices, APICallTypeChoices
except ImportError:
    APICallStatusChoices = None
    APICallTypeChoices = None
try:
    from ee.usage.utils.usage_entries import log_and_deduct_cost_for_resource_request
except ImportError:
    log_and_deduct_cost_for_resource_request = None


class OtelSpan:
    trace_id: uuid.UUID
    span_id: str
    name: str
    start_time: datetime
    end_time: datetime
    attributes: dict
    events: list
    parent_span_id: str
    project_name: str
    project_type: str
    project_version_name: str


class SpanAttributes:
    """
    Span attributes using OTEL GenAI semantic conventions.

    Reference: https://opentelemetry.io/docs/specs/semconv/gen-ai/
    """

    # ==========================================================================
    # OTEL GenAI Semantic Conventions (gen_ai.*)
    # ==========================================================================

    # Operation & Provider
    OPERATION_NAME = "gen_ai.operation.name"
    PROVIDER_NAME = "gen_ai.provider.name"
    SYSTEM = "gen_ai.system"

    # Request
    REQUEST_MODEL = "gen_ai.request.model"
    REQUEST_TEMPERATURE = "gen_ai.request.temperature"
    REQUEST_TOP_P = "gen_ai.request.top_p"
    REQUEST_TOP_K = "gen_ai.request.top_k"
    REQUEST_MAX_TOKENS = "gen_ai.request.max_tokens"
    REQUEST_PARAMETERS = "gen_ai.request.parameters"
    REQUEST_FREQUENCY_PENALTY = "gen_ai.request.frequency_penalty"
    REQUEST_PRESENCE_PENALTY = "gen_ai.request.presence_penalty"
    REQUEST_SEED = "gen_ai.request.seed"
    REQUEST_STOP_SEQUENCES = "gen_ai.request.stop_sequences"
    REQUEST_CHOICE_COUNT = "gen_ai.request.choice_count"
    REQUEST_ENCODING_FORMATS = "gen_ai.request.encoding_formats"

    # Response
    RESPONSE_MODEL = "gen_ai.response.model"
    RESPONSE_ID = "gen_ai.response.id"
    RESPONSE_FINISH_REASONS = "gen_ai.response.finish_reasons"

    # Output
    OUTPUT_TYPE = "gen_ai.output.type"

    # Token Usage
    USAGE_INPUT_TOKENS = "gen_ai.usage.input_tokens"
    USAGE_OUTPUT_TOKENS = "gen_ai.usage.output_tokens"
    USAGE_TOTAL_TOKENS = "gen_ai.usage.total_tokens"

    # Messages
    INPUT_MESSAGES = "gen_ai.input.messages"
    OUTPUT_MESSAGES = "gen_ai.output.messages"
    SYSTEM_INSTRUCTIONS = "gen_ai.system_instructions"

    # Tools
    TOOL_NAME = "gen_ai.tool.name"
    TOOL_DESCRIPTION = "gen_ai.tool.description"
    TOOL_TYPE = "gen_ai.tool.type"
    TOOL_CALL_ID = "gen_ai.tool.call.id"
    TOOL_CALL_ARGUMENTS = "gen_ai.tool.call.arguments"
    TOOL_CALL_RESULT = "gen_ai.tool.call.result"
    TOOL_DEFINITIONS = "gen_ai.tool.definitions"

    # Context
    CONVERSATION_ID = "gen_ai.conversation.id"
    PROMPT_NAME = "gen_ai.prompt.name"
    DATA_SOURCE_ID = "gen_ai.data_source.id"

    # Agent
    AGENT_ID = "gen_ai.agent.id"
    AGENT_NAME = "gen_ai.agent.name"
    AGENT_DESCRIPTION = "gen_ai.agent.description"

    # Evaluation
    EVALUATION_NAME = "gen_ai.evaluation.name"
    EVALUATION_SCORE_VALUE = "gen_ai.evaluation.score.value"
    EVALUATION_SCORE_LABEL = "gen_ai.evaluation.score.label"
    EVALUATION_EXPLANATION = "gen_ai.evaluation.explanation"

    # Embeddings
    EMBEDDINGS_DIMENSION_COUNT = "gen_ai.embeddings.dimension.count"

    # Token Type
    TOKEN_TYPE = "gen_ai.token.type"

    # Span Kind
    SPAN_KIND = "gen_ai.span.kind"

    # Prompt Template (custom extension)
    PROMPT_TEMPLATE_NAME = "gen_ai.prompt.template.name"
    PROMPT_TEMPLATE_VERSION = "gen_ai.prompt.template.version"
    PROMPT_TEMPLATE_LABEL = "gen_ai.prompt.template.label"
    PROMPT_TEMPLATE_VARIABLES = "gen_ai.prompt.template.variables"

    # Token Cache
    USAGE_CACHE_READ_TOKENS = "gen_ai.usage.cache_read_tokens"
    USAGE_CACHE_WRITE_TOKENS = "gen_ai.usage.cache_write_tokens"

    # Cost
    COST_INPUT = "gen_ai.cost.input"
    COST_OUTPUT = "gen_ai.cost.output"
    COST_TOTAL = "gen_ai.cost.total"
    COST_CACHE_READ = "gen_ai.cost.cache_read"
    COST_CACHE_WRITE = "gen_ai.cost.cache_write"

    # Error
    ERROR_TYPE = "error.type"
    ERROR_MESSAGE = "error.message"

    # Duration
    CLIENT_OPERATION_DURATION = "gen_ai.client.operation.duration"

    # Agent Graph
    AGENT_GRAPH_NODE_ID = "gen_ai.agent.graph.node_id"
    AGENT_GRAPH_NODE_NAME = "gen_ai.agent.graph.node_name"
    AGENT_GRAPH_PARENT_NODE_ID = "gen_ai.agent.graph.parent_node_id"

    # Evaluation (additional)
    EVALUATION_TARGET_SPAN_ID = "gen_ai.evaluation.target_span_id"

    # Retriever
    GEN_AI_RETRIEVAL_DOCUMENTS = "gen_ai.retrieval.documents"
    RETRIEVAL_QUERY = "gen_ai.retrieval.query"
    RETRIEVAL_TOP_K = "gen_ai.retrieval.top_k"

    # Embedding (additional)
    EMBEDDINGS_VECTORS = "gen_ai.embeddings.vectors"

    # Guardrail
    GUARDRAIL_NAME = "gen_ai.guardrail.name"
    GUARDRAIL_TYPE = "gen_ai.guardrail.type"
    GUARDRAIL_RESULT = "gen_ai.guardrail.result"
    GUARDRAIL_SCORE = "gen_ai.guardrail.score"
    GUARDRAIL_CATEGORIES = "gen_ai.guardrail.categories"
    GUARDRAIL_MODIFIED_OUTPUT = "gen_ai.guardrail.modified_output"

    # Prompt (additional)
    PROMPT_VENDOR = "gen_ai.prompt.vendor"
    PROMPT_ID = "gen_ai.prompt.id"

    # Voice / Conversation
    VOICE_CALL_ID = "gen_ai.voice.call_id"
    VOICE_PROVIDER = "gen_ai.voice.provider"
    VOICE_CALL_DURATION_SECS = "gen_ai.voice.call_duration_secs"
    VOICE_ENDED_REASON = "gen_ai.voice.ended_reason"
    VOICE_FROM_NUMBER = "gen_ai.voice.from_number"
    VOICE_TO_NUMBER = "gen_ai.voice.to_number"
    VOICE_CHANNEL_TYPE = "gen_ai.voice.channel_type"
    VOICE_TRANSCRIPT = "gen_ai.voice.transcript"
    VOICE_RECORDING_URL = "gen_ai.voice.recording.url"
    VOICE_RECORDING_STEREO_URL = "gen_ai.voice.recording.stereo_url"
    VOICE_RECORDING_CUSTOMER_URL = "gen_ai.voice.recording.customer_url"
    VOICE_RECORDING_ASSISTANT_URL = "gen_ai.voice.recording.assistant_url"
    VOICE_STT_MODEL = "gen_ai.voice.stt.model"
    VOICE_STT_PROVIDER = "gen_ai.voice.stt.provider"
    VOICE_STT_LANGUAGE = "gen_ai.voice.stt.language"
    VOICE_TTS_MODEL = "gen_ai.voice.tts.model"
    VOICE_TTS_PROVIDER = "gen_ai.voice.tts.provider"
    VOICE_TTS_VOICE_ID = "gen_ai.voice.tts.voice_id"
    VOICE_LATENCY_MODEL_AVG_MS = "gen_ai.voice.latency.model_avg_ms"
    VOICE_LATENCY_VOICE_AVG_MS = "gen_ai.voice.latency.voice_avg_ms"
    VOICE_LATENCY_TRANSCRIBER_AVG_MS = "gen_ai.voice.latency.transcriber_avg_ms"
    VOICE_LATENCY_TURN_AVG_MS = "gen_ai.voice.latency.turn_avg_ms"
    VOICE_LATENCY_TTFB_MS = "gen_ai.voice.latency.ttfb_ms"
    VOICE_INTERRUPTIONS_USER_COUNT = "gen_ai.voice.interruptions.user_count"
    VOICE_INTERRUPTIONS_ASSISTANT_COUNT = "gen_ai.voice.interruptions.assistant_count"
    VOICE_COST_TOTAL = "gen_ai.voice.cost.total"
    VOICE_COST_STT = "gen_ai.voice.cost.stt"
    VOICE_COST_TTS = "gen_ai.voice.cost.tts"
    VOICE_COST_LLM = "gen_ai.voice.cost.llm"
    VOICE_COST_TELEPHONY = "gen_ai.voice.cost.telephony"

    # Image Generation
    IMAGE_PROMPT = "gen_ai.image.prompt"
    IMAGE_NEGATIVE_PROMPT = "gen_ai.image.negative_prompt"
    IMAGE_WIDTH = "gen_ai.image.width"
    IMAGE_HEIGHT = "gen_ai.image.height"
    IMAGE_SIZE = "gen_ai.image.size"
    IMAGE_QUALITY = "gen_ai.image.quality"
    IMAGE_STYLE = "gen_ai.image.style"
    IMAGE_STEPS = "gen_ai.image.steps"
    IMAGE_GUIDANCE_SCALE = "gen_ai.image.guidance_scale"
    IMAGE_SEED = "gen_ai.image.seed"
    IMAGE_FORMAT = "gen_ai.image.format"
    IMAGE_COUNT = "gen_ai.image.count"
    IMAGE_REVISED_PROMPT = "gen_ai.image.revised_prompt"
    IMAGE_OUTPUT_URLS = "gen_ai.image.output_urls"

    # Computer Use
    COMPUTER_USE_ACTION = "gen_ai.computer_use.action"
    COMPUTER_USE_COORDINATE_X = "gen_ai.computer_use.coordinate_x"
    COMPUTER_USE_COORDINATE_Y = "gen_ai.computer_use.coordinate_y"
    COMPUTER_USE_TEXT = "gen_ai.computer_use.text"
    COMPUTER_USE_KEY = "gen_ai.computer_use.key"
    COMPUTER_USE_BUTTON = "gen_ai.computer_use.button"
    COMPUTER_USE_SCROLL_DIRECTION = "gen_ai.computer_use.scroll_direction"
    COMPUTER_USE_SCROLL_AMOUNT = "gen_ai.computer_use.scroll_amount"
    COMPUTER_USE_SCREENSHOT = "gen_ai.computer_use.screenshot"
    COMPUTER_USE_ENVIRONMENT = "gen_ai.computer_use.environment"
    COMPUTER_USE_VIEWPORT_WIDTH = "gen_ai.computer_use.viewport_width"
    COMPUTER_USE_VIEWPORT_HEIGHT = "gen_ai.computer_use.viewport_height"
    COMPUTER_USE_CURRENT_URL = "gen_ai.computer_use.current_url"
    COMPUTER_USE_ELEMENT_SELECTOR = "gen_ai.computer_use.element_selector"
    COMPUTER_USE_RESULT = "gen_ai.computer_use.result"

    # Performance & Streaming
    TIME_TO_FIRST_TOKEN = "gen_ai.server.time_to_first_token"
    TIME_PER_OUTPUT_TOKEN = "gen_ai.server.time_per_output_token"
    SERVER_QUEUE_TIME = "gen_ai.server.queue_time"

    # Reranker
    RERANKER_MODEL = "gen_ai.reranker.model"
    RERANKER_QUERY = "gen_ai.reranker.query"
    RERANKER_TOP_N = "gen_ai.reranker.top_n"
    RERANKER_INPUT_DOCUMENTS = "gen_ai.reranker.input_documents"
    RERANKER_OUTPUT_DOCUMENTS = "gen_ai.reranker.output_documents"

    # Audio
    AUDIO_URL = "gen_ai.audio.url"
    AUDIO_MIME_TYPE = "gen_ai.audio.mime_type"
    AUDIO_TRANSCRIPT = "gen_ai.audio.transcript"
    AUDIO_DURATION_SECS = "gen_ai.audio.duration_secs"
    AUDIO_LANGUAGE = "gen_ai.audio.language"

    # Server / Infrastructure
    SERVER_ADDRESS = "server.address"
    SERVER_PORT = "server.port"

    # ==========================================================================
    # OpenInference Attributes (for backward compatibility)
    # ==========================================================================

    OUTPUT_VALUE = "output.value"
    OUTPUT_MIME_TYPE = "output.mime_type"
    INPUT_VALUE = "input.value"
    INPUT_MIME_TYPE = "input.mime_type"

    # Embedding
    EMBEDDING_EMBEDDINGS = "embedding.embeddings"
    EMBEDDING_MODEL_NAME = "embedding.model_name"

    # Retrieval
    RETRIEVAL_DOCUMENTS = "retrieval.documents"

    # Metadata & Tags
    METADATA = "metadata"
    TAG_TAGS = "tag.tags"

    # Session & User (OpenInference style)
    SESSION_ID = "session.id"
    USER_ID = "user.id"
    USER_ID_TYPE = "user.id.type"
    USER_ID_HASH = "user.id.hash"
    USER_METADATA = "user.metadata"

    # Input Images
    INPUT_IMAGES = "gen_ai.input.images"

    # Eval Input
    EVAL_INPUT = "eval.input"

    # Raw Input/Output
    RAW_INPUT = "raw.input"
    RAW_OUTPUT = "raw.output"

    # Query/Response
    QUERY = "query"
    RESPONSE = "response"

    # ==========================================================================
    # Legacy LLM Attributes (used by trace adapters: openllmetry, langfuse)
    # ==========================================================================

    # Span kind alias
    FI_SPAN_KIND = SPAN_KIND  # "gen_ai.span.kind"

    # LLM-specific (llm.* prefix — legacy OpenLLMetry/OpenInference convention)
    LLM_MODEL_NAME = "llm.model_name"
    LLM_PROVIDER = "llm.provider"
    LLM_SYSTEM = "llm.system"
    LLM_TOKEN_COUNT_PROMPT = "llm.token_count.prompt"
    LLM_TOKEN_COUNT_COMPLETION = "llm.token_count.completion"
    LLM_TOKEN_COUNT_TOTAL = "llm.token_count.total"
    LLM_INVOCATION_PARAMETERS = "llm.invocation_parameters"

    # Prompt template aliases
    FI_PROMPT_TEMPLATE_NAME = PROMPT_TEMPLATE_NAME  # "gen_ai.prompt.template.name"
    LLM_PROMPT_TEMPLATE_VERSION = (
        PROMPT_TEMPLATE_VERSION  # "gen_ai.prompt.template.version"
    )
    LLM_PROMPT_TEMPLATE = "llm.prompt.template"
    LLM_PROMPT_TEMPLATE_VARIABLES = (
        PROMPT_TEMPLATE_VARIABLES  # "gen_ai.prompt.template.variables"
    )


class WorkflowAttributes:
    """
    Workflow id of the log
    """

    WORKFLOW_ID = "workflow.id"
    """
    Workflow name of the log
    """
    WORKFLOW_NAME = "workflow.name"
    """
    Voicemail detection status of the workflow
    """
    WORKFLOW_VOICEMAIL_DETECTION = "workflow.voicemail_detection"
    """
    Is background sound enabled in the workflow
    """
    WORKFLOW_BACKGROUND_SOUND = "workflow.background_sound"
    """
    Voicemail message of the workflow
    """
    WORKFLOW_VOICEMAIL_MESSAGE = "workflow.voicemail_message"


class TurnLatencyAttributes:
    MODEL_LATENCY = "model_latency"
    """
    The latency of the model
    """
    VOICE_LATENCY = "voice_latency"
    """
    The latency of the voice
    """
    TRANSCRIBER_LATENCY = "transcriber_latency"
    """
    The latency of the transcriber
    """
    ENDPOINTING_LATENCY = "endpointing_latency"
    """
    The latency of the endpointing
    """
    TURN_LATENCY = "turn_latency"
    """
    The latency of the turn
    """


class PerformanceMetrics:
    """
    Turn Latencies
    """

    TURN_LATENCIES = "performance_metrics.turn_latencies"
    """
    Average model latency
    """
    MODEL_LATENCY_AVERAGY = "performance_metrics.model_latency_average"
    """
    Average voice latency
    """
    VOICE_LATENCY_AVERAGE = "performance_metrics.voice_latency_average"
    """
    Average transcriber latency
    """
    TRANSCRIBER_LATENCY_AVERAGE = "performance_metrics.transcriber_latency_average"
    """
    Average endpointing latency
    """
    ENDPOINTING_LATENCY_AVERAGE = "performance_metrics.endpointing_latency_average"
    """
    Average turn latency
    """
    TURN_LATENCY_AVERAGE = "performance_metrics.turn_latency_average"
    """
    Average from transport latency
    """
    FROM_TRANSPORT_LATENCY_AVERAGE = (
        "performance_metrics.from_transport_latency_average"
    )
    """
    Average to transport latency
    """
    TO_TRANSPORT_LATENCY_AVERAGE = "performance_metrics.to_transport_latency_average"
    """
    Number of times user was interrupted
    """
    NUM_USER_INTERRUPTED = "performance_metrics.num_user_interrupted"
    """
    Number of times assistant was interrupted
    """
    NUM_ASSISTANT_INTERRUPTED = "performance_metrics.num_assistant_interrupted"


class MessageAttributes:
    """
    Attributes for a message sent to or from an LLM
    """

    MESSAGE_ROLE = "message.role"
    """
    The role of the message, such as "user", "agent", "function".
    """
    MESSAGE_CONTENT = "message.content"
    """
    The duration of the message (be it the customer or the agent)
    """
    MESSAGE_DURATION = "duration"
    """
    The time when the user/agent started speaking
    """
    MESSAGE_START_TIME = "start_time"
    """
    The content of the message to or from the llm, must be a string.
    """
    MESSAGE_CONTENTS = "message.contents"
    """
    The message contents to the llm, it is an array of
    `message_content` prefixed attributes.
    """
    MESSAGE_NAME = "message.name"
    """
    The name of the message, often used to identify the function
    that was used to generate the message.
    """
    MESSAGE_TOOL_CALLS = "message.tool_calls"
    """
    The tool calls generated by the model, such as function calls.
    """
    MESSAGE_FUNCTION_CALL_NAME = "message.function_call_name"
    """
    The function name that is a part of the message list.
    This is populated for role 'function' or 'agent' as a mechanism to identify
    the function that was called during the execution of a tool.
    """
    MESSAGE_FUNCTION_CALL_ARGUMENTS_JSON = "message.function_call_arguments_json"
    """
    The JSON string representing the arguments passed to the function
    during a function call.
    """
    MESSAGE_TOOL_CALL_ID = "message.tool_call_id"
    """
    The id of the tool call.
    """


class MessageContentAttributes:
    """
    Attributes for the contents of user messages sent to an LLM.
    """

    MESSAGE_CONTENT_TYPE = "message_content.type"
    """
    The type of the content, such as "text" or "image" or "audio" or "video".
    """

    MESSAGE_CONTENT_TEXT = "message_content.text"
    """
    The text content of the message, if the type is "text".
    """
    MESSAGE_CONTENT_IMAGE = "message_content.image"
    """
    The image content of the message, if the type is "image".
    An image can be made available to the model by passing a link to
    the image or by passing the base64 encoded image directly in the
    request.
    """
    MESSAGE_CONTENT_AUDIO = "message_content.audio"
    """
    The audio content of the message, if the type is "audio".
    An audio file can be made available to the model by passing a link to
    the audio file or by passing the base64 encoded audio directly in the
    request.
    """
    MESSAGE_AUDIO_TRANSCRIPT = "message_content.audio.transcript"
    """
    Represents the transcript of the audio content in the message.
    """
    MESSAGE_CONTENT_VIDEO = "message_content.video"
    """
    The video content of the message, if the type is "video".
    """


class ConversationAttributes:
    """
    Attributes for a conversation.
    """

    CONVERSATION_TRANSCRIPT = "conversation.transcript"
    """
    The transcript of the conversation.
    """
    CONVERSATION_RECORDING = "conversation.recording"
    """
    The recording of the conversation.
    """
    STEREO = "stereo"
    """
    The stereo recording of the conversation.
    """
    MONO_COMBINED = "mono.combined"
    """
    The combined recording of the conversation.
    """
    MONO_CUSTOMER = "mono.customer"
    """
    The customer recording of the conversation.
    """
    MONO_ASSISTANT = "mono.assistant"
    """
    The assistant recording of the conversation.
    """


class CallAttributes:
    """Provider-agnostic attributes for voice call metadata."""

    TOTAL_TURNS = "call.total_turns"
    """Total number of user/agent conversation turns in the call."""

    DURATION = "call.duration"
    """Total call duration in seconds."""

    PARTICIPANT_PHONE_NUMBER = "call.participant_phone_number"
    """Phone number of the call participant."""

    STATUS = "call.status"
    """Raw provider call status (e.g., 'ended', 'error', 'done')."""

    USER_WPM = "call.user_wpm"
    """User's words per minute during the call."""

    BOT_WPM = "call.bot_wpm"
    """Bot's words per minute during the call."""

    TALK_RATIO = "call.talk_ratio"
    """Ratio of bot talk time to user talk time."""


class OpenInferenceSpanKindValues(Enum):
    TOOL = "TOOL"
    CHAIN = "CHAIN"
    LLM = "LLM"
    RETRIEVER = "RETRIEVER"
    EMBEDDING = "EMBEDDING"
    AGENT = "AGENT"
    RERANKER = "RERANKER"
    UNKNOWN = "UNKNOWN"
    GUARDRAIL = "GUARDRAIL"
    EVALUATOR = "EVALUATOR"
    CONVERSATION = "CONVERSATION"


class FiLLMProviderValues(Enum):
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    COHERE = "cohere"
    MISTRALAI = "mistralai"
    GOOGLE = "google"
    AZURE = "azure"
    AWS = "aws"
    VERTEXAI = "vertexai"


class AttributeDecoder:
    @staticmethod
    def get_nested_value(attributes: dict[str, Any], key: str) -> Any:
        """Get nested attribute value using dot notation."""
        if not attributes or not key:
            return None

        parts = key.split(".")
        current = attributes

        for part in parts:
            if not isinstance(current, dict):
                return None

            # Handle array indexing
            if "[" in part and "]" in part:
                try:
                    base_key, index_str = part[:-1].split("[")
                    index = int(index_str)
                    array_value = current.get(base_key)
                    if not isinstance(array_value, list | tuple) or index >= len(
                        array_value
                    ):
                        return None
                    current = array_value[index]
                except (ValueError, KeyError, IndexError):
                    return None
            else:
                current = current.get(part)
                if current is None:
                    return None

        return current

    @staticmethod
    def parse_json_string(value: str | None) -> Any:
        try:
            if value is None:
                return None

            # If already a dict, list, or primitive type, return as is
            if isinstance(value, dict | list | int | float | bool):
                return value

            # Handle string values
            if isinstance(value, str):
                try:
                    return json.loads(value)
                except (json.JSONDecodeError, TypeError):
                    # If not valid JSON, return the string as is
                    return value

            # For any other type, try to convert to string representation
            return str(value)

        except Exception as e:
            # Log error and return None in case of unexpected errors
            logger.exception(f"Error parsing value in parse_json_string: {str(e)}")
            return None

    @staticmethod
    def parse_nested_json(data: Any) -> Any:
        """
        Recursively parse any JSON strings within a data structure.
        Works on dictionaries, lists, and individual values.

        Args:
            data: Input data that may contain JSON strings

        Returns:
            Parsed data structure with all JSON strings converted to Python objects
        """
        try:
            # Handle dictionaries
            if isinstance(data, dict):
                return {
                    k: AttributeDecoder.parse_nested_json(v) for k, v in data.items()
                }

            # Handle lists
            if isinstance(data, list):
                return [AttributeDecoder.parse_nested_json(item) for item in data]

            # Handle strings that might be JSON
            if isinstance(data, str):
                try:
                    parsed = json.loads(data)
                    return AttributeDecoder.parse_nested_json(
                        parsed
                    )  # Recursively parse the result
                except json.JSONDecodeError:
                    return data  # Return original string if not valid JSON

            # Return other types as-is
            return data

        except Exception as e:
            logger.warning(f"Error in parse_nested_json: {str(e)}")
            return data

    @classmethod
    def decode_messages(
        cls, attributes: dict[str, Any], prefix: str
    ) -> list[dict[str, Any]]:
        """Decode message arrays from attributes."""
        messages = []
        index = 0

        while True:
            base_key = f"{prefix}.{index}.message"
            content = cls.get_nested_value(attributes, f"{base_key}.content")

            if content is None:
                break

            role = cls.get_nested_value(attributes, f"{base_key}.role")
            tool_calls = cls.get_nested_value(attributes, f"{base_key}.tool_calls")

            message = {"role": role, "content": content}
            if tool_calls:
                message["tool_calls"] = tool_calls

            messages.append(message)
            index += 1

        return messages


def get_user_id_type(user_id_type: str | None) -> str | None:
    """
    Determines the type of user ID based on its format.
    """
    if user_id_type is None:
        return None
    match user_id_type:
        case "email":
            return UserIdType.EMAIL.value
        case "phone":
            return UserIdType.PHONE.value
        case "uuid":
            return UserIdType.UUID.value
        case _:
            return UserIdType.CUSTOM.value


class ResourceLimitError(Exception):
    """Custom exception for resource limit errors."""

    pass


DECODER = AttributeDecoder()


def convert_otel_span_to_observation_span(
    otel_span, organization_id=None, user_id=None, workspace_id=None
):
    """
    Convert an OTel span dictionary to an ObservationSpan object with mapped fields.

    Supports multiple semantic conventions (FI, OTEL GenAI, OpenLLMetry, OpenInference)
    through attribute aliasing. Implements dual storage - storing both normalized
    column values and raw attributes for future-proofing.
    """
    try:
        attributes = process_attributes(
            otel_span.get("attributes", {}), org_id=organization_id
        )

        # Detect semantic convention source
        semconv_source = detect_semconv(attributes)

        # Use AttributeRegistry for span kind (supports multiple conventions)
        # Try gen_ai.span.kind / openinference.span.kind first
        raw_span_kind = get_attribute(attributes, "span_kind", "")
        if not raw_span_kind:
            # Fallback: derive span kind from operation name
            raw_span_kind = get_attribute(attributes, "operation_name", "")
        span_kind = AttributeRegistry.normalize_span_kind(raw_span_kind).upper()

        # Extract operation_name (what the span DOES, separate from span kind)
        operation_name = get_attribute(attributes, "operation_name")

        # Initialize decoder
        decoder = AttributeDecoder()
        # Get common data
        eval_tags = otel_span.get("eval_tags")
        project = get_or_create_project(
            otel_span.get("project_name"),
            organization_id,
            otel_span.get("project_type"),
            user_id,
            workspace_id,
        )
        metadata = otel_span.get("metadata") or attributes.get(SpanAttributes.METADATA)
        project_version_id = otel_span.get("project_version_id")
        project_version = get_or_create_project_version(
            project_id=project.id,
            project_version_name=otel_span.get("project_version_name"),
            project_version_id=project_version_id,
            eval_tags=eval_tags,
            metadata=metadata,
            project_type=otel_span.get("project_type"),
        )
        trace_id = otel_span.get("trace_id")
        span_id = otel_span.get("span_id")
        latency = otel_span.get("latency")
        session_name = attributes.get(SpanAttributes.SESSION_ID)

        attributes[SpanAttributes.RESPONSE] = decoder.parse_json_string(
            get_attribute(attributes, "output_value", "")
        )

        # Process input value
        input_val = get_attribute(attributes, "input_value")
        if input_val in [None, "", "[]", []]:
            if attributes.get(SpanAttributes.RAW_INPUT) is not None:
                input_val = attributes.get(SpanAttributes.RAW_INPUT, None)
        input_val = decoder.parse_nested_json(input_val) if input_val else None

        # Process output value
        output_val = get_attribute(attributes, "output_value")
        if output_val in [None, "", "[]", []]:
            if attributes.get(SpanAttributes.RAW_OUTPUT) is not None:
                output_val = attributes.get(SpanAttributes.RAW_OUTPUT, None)
        output_val = decoder.parse_nested_json(output_val) if output_val else None

        end_user = None
        if attributes.get(SpanAttributes.USER_ID):
            end_user = {
                "user_id": attributes.get(SpanAttributes.USER_ID),
                "user_id_type": get_user_id_type(
                    attributes.get(SpanAttributes.USER_ID_TYPE)
                ),
                "user_id_hash": attributes.get(SpanAttributes.USER_ID_HASH),
                "metadata": attributes.get(SpanAttributes.USER_METADATA, {}),
            }
        prompt_details = None

        if attributes.get(SpanAttributes.PROMPT_TEMPLATE_NAME):
            prompt_details = {
                "prompt_template_name": attributes.get(
                    SpanAttributes.PROMPT_TEMPLATE_NAME, None
                ),
                "prompt_template_version": attributes.get(
                    SpanAttributes.PROMPT_TEMPLATE_VERSION, None
                ),
                "prompt_template_label": attributes.get(
                    SpanAttributes.PROMPT_TEMPLATE_LABEL, None
                ),
                "prompt_template_variables": attributes.get(
                    SpanAttributes.PROMPT_TEMPLATE_VARIABLES, None
                ),
            }

        # Use AttributeRegistry for token counts (supports multiple conventions)
        prompt_tokens = get_attribute(attributes, "input_tokens")
        completion_tokens = get_attribute(attributes, "output_tokens")
        total_tokens = get_attribute(attributes, "total_tokens")

        # Use AttributeRegistry for model name (supports multiple conventions)
        model = get_attribute(attributes, "model_name")
        cost = calculate_cost(
            attributes=attributes,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            model=model,
            organization_id=organization_id,
        )
        user_cost_input = attributes.get("gen_ai.cost.input") or attributes.get(
            "llm.cost.prompt"
        )
        user_cost_output = attributes.get("gen_ai.cost.output") or attributes.get(
            "llm.cost.completion"
        )
        if cost is not None:
            try:
                cost = float(cost)
            except (ValueError, TypeError):
                cost = 0
        elif user_cost_input is not None or user_cost_output is not None:
            try:
                cost = float(user_cost_input or 0) + float(user_cost_output or 0)
            except (ValueError, TypeError):
                cost = 0
        else:
            cost = calculate_cost_from_tokens(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                model=model,
                organization_id=organization_id,
            )

        # Use AttributeRegistry for provider (supports multiple conventions)
        provider = attributes.get(SpanAttributes.PROVIDER_NAME) or get_attribute(
            attributes, "provider"
        )

        # Extract resource attributes if present (for OTLP format)
        resource_attributes = otel_span.get("resource", {}).get("attributes", {})

        # Parse attributes once for both fields (backward compatibility)
        parsed_attributes = decoder.parse_nested_json(attributes)

        observation_span = {
            "id": span_id,
            "name": otel_span.get("name", ""),
            "observation_type": get_observation_type(span_kind),
            "operation_name": operation_name,
            "start_time": timezone.make_aware(
                datetime.fromtimestamp(otel_span.get("start_time", 0) / 1e9)
            ),
            "end_time": timezone.make_aware(
                datetime.fromtimestamp(otel_span.get("end_time", 0) / 1e9)
            ),
            "input": input_val,
            "output": output_val,
            "model": model,
            "model_parameters": decoder.parse_json_string(
                attributes.get(SpanAttributes.REQUEST_PARAMETERS)
            ),
            "latency_ms": latency,
            "org_id": organization_id,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
            "response_time": float(latency) if latency else None,
            "tags": decoder.parse_json_string(
                attributes.get(SpanAttributes.TAG_TAGS, [])
            ),
            "metadata": decoder.parse_json_string(metadata) if metadata else None,
            "span_events": decoder.parse_json_string(otel_span.get("events", [])),
            "status": otel_span.get("status", "UNSET"),
            "status_message": otel_span.get("status_message"),
            "provider": provider,
            "parent_span_id": otel_span.get("parent_id"),
            "project": project,
            **({"project_version": project_version} if project_version else {}),
            "trace_id": trace_id,
            # Unified attribute storage - span_attributes is the canonical source
            # eval_attributes kept for backward compatibility (same data)
            "span_attributes": parsed_attributes,
            "eval_attributes": parsed_attributes,  # Deprecated: same as span_attributes
            "eval_status": StatusType.NOT_STARTED.value,
            "resource_attributes": resource_attributes,
            "semconv_source": semconv_source,
        }

        return {
            "end_user": end_user,
            "prompt_details": prompt_details,
            "trace": trace_id,
            "observation_span": observation_span,
            "project": project,
            "project_version": project_version,
            "eval_tags": project_version.eval_tags if project_version else [],
            "session_name": session_name,
            "project_type": (
                otel_span.get("project_type")
                if otel_span.get("project_type")
                else "experiment"
            ),
        }

    except ResourceLimitError:
        raise
    except Exception as e:
        raise ValueError(
            f"Error converting OTel span to Observation span: {str(e)}"
        ) from e


def filter_eval_attributes(attributes: dict[str, Any]) -> dict[str, Any]:
    """
    DEPRECATED: This function is no longer needed.

    Previously filtered out specific keys from attributes, but now we store
    all attributes in span_attributes without filtering.

    This function is kept for backward compatibility but simply returns
    the attributes unchanged. It will be removed in a future release.

    Args:
        attributes: Dictionary of attributes

    Returns:
        The same dictionary unchanged (no longer filters)
    """
    # DEPRECATED: No longer filtering - all attributes go to span_attributes
    # The excluded keys (fi.llm.input, fi.llm.output, eval.input) are already
    # stored in dedicated columns (input, output), so having them in
    # span_attributes doesn't cause duplication issues.
    return attributes


def upload_content_to_s3(content: Any, content_type: str, org_id=None) -> str:
    if content_type not in ["audio", "image", "video"]:
        raise ValueError(
            f"Invalid content_type: {content_type}. Must be 'audio', 'image' or 'video'"
        )

    if content_type == "image":
        return upload_image_to_s3(content, org_id=org_id)
    elif content_type == "audio":
        return upload_audio_to_s3(content, org_id=org_id)
    elif content_type == "video":
        return upload_video_to_s3(content, thumbnail=True, org_id=org_id)


def process_attributes(attributes: dict[str, Any], org_id=None) -> dict[str, Any]:
    pattern = re.compile(
        rf"^(?:{SpanAttributes.OUTPUT_MESSAGES}|{SpanAttributes.INPUT_MESSAGES}).*\.({MessageContentAttributes.MESSAGE_CONTENT_IMAGE}|{MessageContentAttributes.MESSAGE_CONTENT_AUDIO}|{MessageContentAttributes.MESSAGE_CONTENT_VIDEO})$"
    )

    updated_attributes = attributes.copy()

    for key in attributes.keys():
        match = pattern.match(key)
        if match:
            content = attributes[key]
            content_type = match.group(1).split(".")[-1]

            try:
                if content_type == "video":
                    result = upload_content_to_s3(content, content_type, org_id=org_id)
                    if isinstance(result, tuple):
                        video_url, thumbnail_url = result
                        updated_attributes[key] = video_url
                        if thumbnail_url:
                            thumbnail_key = f"{key}.thumbnail"
                            updated_attributes[thumbnail_key] = thumbnail_url
                    else:
                        updated_attributes[key] = result
                else:
                    url = upload_content_to_s3(content, content_type, org_id=org_id)
                    updated_attributes[key] = url
            except Exception as e:
                logger.exception(
                    f"Failed to upload {content_type} content for {key}: {str(e)}"
                )

    return updated_attributes


def _deduct_project_creation_cost(
    organization, project_type, existing=False, workspace=None, project_id=None
):
    """
    Deduct cost for project creation.
    Returns the call log row if successful, raises ResourceLimitError if not allowed.
    """
    call_type = (
        APICallTypeChoices.PROTOTYPE_ADD.value
        if project_type == "experiment"
        else APICallTypeChoices.OBSERVE_ADD.value
    )

    call_log_row = log_and_deduct_cost_for_resource_request(
        organization,
        call_type,
        config={"existing": existing, "project_id": project_id},
        workspace=workspace,
    )

    if (
        call_log_row is None
        or call_log_row.status == APICallStatusChoices.RESOURCE_LIMIT.value
    ):
        raise ResourceLimitError(
            "Trace creation not allowed due to plan limits or insufficient credits."
        )

    return call_log_row


def _update_call_log_status(call_log_row):
    call_log_row.status = APICallStatusChoices.SUCCESS.value
    call_log_row.save(update_fields=["status"])


def _get_project_type(project_type: str) -> str:
    valid_project_types = [t[0] for t in PROJECT_TYPES]
    return "experiment" if project_type not in valid_project_types else project_type


def get_or_create_project(
    project_name: str,
    organization_id: str,
    project_type: str,
    user_id: str,
    workspace_id=None,
    source: str = ProjectSourceChoices.PROTOTYPE.value,
) -> Project | None:
    try:
        project_type = _get_project_type(project_type)

        # Resolve organization and workspace upfront so all queries
        # match the full unique constraint (name, trace_type, org, workspace).
        try:
            organization = Organization.objects.get(id=organization_id)
        except Organization.DoesNotExist as e:
            raise Exception(f"Organization {organization_id} does not exist") from e

        workspace = None
        if workspace_id:
            workspace = Workspace.objects.get(id=workspace_id)

        if not workspace:
            workspace = Workspace.objects.get(
                organization=organization, is_default=True, is_active=True
            )

        # Check if project already exists (no locks)
        try:
            return Project.no_workspace_objects.get(
                name=project_name,
                trace_type=project_type,
                organization=organization,
                workspace=workspace,
            )
        except Project.DoesNotExist:
            pass

        # Create project, relying on the DB unique constraint
        # (unique_project_per_org_type) to handle concurrent races instead
        # of SELECT FOR UPDATE which causes lock contention under high
        # concurrency on the same organization.
        try:
            with transaction.atomic():
                project = Project.no_workspace_objects.create(
                    name=project_name,
                    trace_type=project_type,
                    organization=organization,
                    workspace=workspace,
                    model_type=AIModel.ModelTypes.GENERATIVE_LLM,
                    config=get_default_project_version_config(),
                    user_id=user_id,
                    source=source,
                )
                return project
        except IntegrityError:
            # Another worker created it first — just fetch it
            return Project.no_workspace_objects.get(
                name=project_name,
                trace_type=project_type,
                organization=organization,
                workspace=workspace,
            )

    except Exception as e:
        raise Exception(f"Failed to create or get project: {str(e)}")


def _get_eval_template(eval_template_name, project):
    eval_template = EvalTemplate.no_workspace_objects.filter(
        Q(name=eval_template_name, organization=project.organization)
        | Q(name=eval_template_name, organization=None)
    ).first()

    if not eval_template:
        raise Exception(f"EvalTemplate '{eval_template_name}' not found")

    return eval_template


def _process_eval_tags(eval_tags, project):
    """
    Process and validate eval_tags.
    Returns a list of processed eval tag data ready for database operations.
    """
    if not eval_tags:
        return []

    # Parse JSON string if needed
    if isinstance(eval_tags, str):
        try:
            eval_tags = json.loads(eval_tags)
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON format for eval_tags: {str(e)}") from e

    processed_eval_tags = []

    for eval_tag in eval_tags:
        mapping = eval_tag.get("mapping")
        custom_eval_name = eval_tag.get("custom_eval_name")
        eval_template_name = eval_tag.get("eval_name")
        model = eval_tag.get("model", None)

        eval_template = _get_eval_template(eval_template_name, project)

        if not eval_template:
            raise Exception(f"EvalTemplate '{eval_template_name}' not found")

        # Parse mapping JSON if it's a string
        if isinstance(mapping, str):
            try:
                mapping = json.loads(mapping)
            except json.JSONDecodeError as e:
                logger.exception(f"Invalid JSON format for mapping: {str(e)}")
                raise Exception(f"Invalid JSON format for mapping: {str(e)}") from e

        processed_eval_tags.append(
            {
                "eval_tag": eval_tag,
                "mapping": mapping,
                "custom_eval_name": custom_eval_name,
                "eval_template": eval_template,
                "model": model,
            }
        )

    return processed_eval_tags


def _create_custom_eval_configs(processed_eval_tags, project):
    """
    Create CustomEvalConfig objects from processed eval tags.
    """
    final_eval_tags = []

    for tag_data in processed_eval_tags:
        eval_tag = tag_data["eval_tag"]
        mapping = tag_data["mapping"]
        custom_eval_name = tag_data["custom_eval_name"]
        eval_template = tag_data["eval_template"]
        model = tag_data["model"]

        custom_eval_config, created = CustomEvalConfig.objects.get_or_create(
            project=project,
            name=custom_eval_name,
            defaults={
                "mapping": mapping,
                "eval_template": eval_template,
                "model": model,
            },
        )

        final_eval_tags.append(
            {
                "custom_eval_config_id": str(custom_eval_config.id),
                "type": eval_tag.get("type"),
                "value": eval_tag.get("value"),
            }
        )

    return final_eval_tags


def _get_project_version_number(versions_qs):
    """
    Get project version number.
    """
    latest_version = versions_qs.order_by("-created_at").first()

    version = "v1"
    if latest_version and latest_version.version:
        try:
            version_num = int(latest_version.version.replace("v", ""))
            version = f"v{version_num + 1}"
        except ValueError:
            if "_v" in latest_version.version:
                version_num = int(latest_version.version.split("_v")[1])
                version = f"{latest_version.version.split('_v')[0]}_v{version_num + 1}"
            else:
                version = f"{latest_version.version}_v2"

    return version


def get_or_create_project_version(
    project_id: uuid.UUID,
    project_version_name: str,
    project_version_id: uuid.UUID | None,
    eval_tags: list | None,
    metadata: dict | None,
    project_type: str,
) -> ProjectVersion | None:
    try:
        if project_type == "observe":
            return None

        if project_version_id:
            existing_version = ProjectVersion.objects.filter(
                id=project_version_id
            ).first()
            if existing_version:
                return existing_version

        with transaction.atomic():
            # Lock the parent project to serialize version creation and prevent race conditions.
            try:
                # Use no_workspace_objects manager to avoid the outer join issue with select_for_update
                project = Project.no_workspace_objects.select_for_update().get(
                    id=project_id
                )
            except Project.DoesNotExist as e:
                raise Project.DoesNotExist(f"Project not found: {str(e)}")  # noqa: B904

            if project_version_id:
                existing_version = ProjectVersion.objects.filter(
                    id=project_version_id
                ).first()
                if existing_version:
                    return existing_version

            if not project_version_id:
                project_version_id = uuid.uuid4()

            processed_eval_tags = _process_eval_tags(eval_tags, project)
            final_eval_tags = _create_custom_eval_configs(processed_eval_tags, project)

            versions_qs = ProjectVersion.objects.filter(project=project)
            version = _get_project_version_number(versions_qs)

            project_version = ProjectVersion.objects.create(
                id=project_version_id,
                project=project,
                name=project_version_name or f"Version {version}",
                version=version,
                eval_tags=final_eval_tags,
                start_time=datetime.now(),
                metadata=metadata or {},
                config=get_default_trace_config(),
            )
            return project_version

    except Project.DoesNotExist:
        raise
    except Exception as e:
        raise Exception(f"Failed to create project version: {str(e)}")  # noqa: B904


def get_observation_type(span_kind: str) -> str:
    return (
        span_kind.lower()
        if span_kind.lower() in [t[0] for t in ObservationSpan.OBSERVATION_SPAN_TYPES]
        else "unknown"
    )


def _extract_project_and_version_keys(
    otel_spans, organization_id, user_id, workspace_id
):
    """
    Extracts unique keys for projects and versions from a list of OTel spans.
    """
    project_keys = set()
    version_keys = set()

    for otel_span in otel_spans:
        project_name = otel_span.get("project_name")
        project_type = otel_span.get("project_type")
        if project_name and project_type:
            project_keys.add(
                (project_name, organization_id, project_type, user_id, workspace_id)
            )

            version_keys.add(
                (
                    project_name,
                    project_type,
                    otel_span.get("project_version_name"),
                    otel_span.get("project_version_id"),
                    otel_span.get("eval_tags"),
                )
            )

    return project_keys, version_keys


def _bulk_get_or_create_projects(project_keys):
    """
    Efficiently gets or creates multiple projects in bulk.
    """
    projects = {}

    for p_name, org_id, p_type, u_id, w_id in project_keys:
        project = get_or_create_project(p_name, org_id, p_type, u_id, w_id)
        projects[(p_name, org_id, p_type)] = project
    return projects


def _bulk_get_or_create_project_versions(version_keys, projects, organization_id):
    """
    Efficiently gets or creates multiple project versions in bulk.
    """
    project_versions = {}
    for p_name, p_type, v_name, v_id, eval_tags in version_keys:
        project = projects.get((p_name, organization_id, p_type))
        if project:
            version_key = (project.id, v_name, v_id)
            if version_key not in project_versions:
                project_version = get_or_create_project_version(
                    project_id=project.id,
                    project_version_name=v_name,
                    project_version_id=v_id,
                    eval_tags=eval_tags,
                    metadata=None,
                    project_type=p_type,
                )
                project_versions[version_key] = project_version
    return project_versions


def _convert_single_span(otel_span, projects, project_versions, organization_id):
    """
    Converts a single OTel span dictionary into the target ObservationSpan format.

    Supports multiple semantic conventions through attribute aliasing and
    implements dual storage for future-proofing.
    """
    attributes = process_attributes(
        otel_span.get("attributes", {}), org_id=organization_id
    )

    # Detect semantic convention source
    semconv_source = detect_semconv(attributes)

    # Use AttributeRegistry for span kind (supports multiple conventions)
    # Try gen_ai.span.kind / openinference.span.kind first
    raw_span_kind = get_attribute(attributes, "span_kind", "")
    if not raw_span_kind:
        # Fallback: derive span kind from operation name
        raw_span_kind = get_attribute(attributes, "operation_name", "")
    span_kind = AttributeRegistry.normalize_span_kind(raw_span_kind).upper()

    # Extract operation_name (what the span DOES, separate from span kind)
    operation_name = get_attribute(attributes, "operation_name")

    # Link Project and Version
    project_name = otel_span.get("project_name")
    project_type = otel_span.get("project_type")
    project = projects.get((project_name, organization_id, project_type))

    project_version = None
    if project:
        project_version_name = otel_span.get("project_version_name")
        project_version_id = otel_span.get("project_version_id")
        version_key = (project.id, project_version_name, project_version_id)
        project_version = project_versions.get(version_key)
    else:
        raise Exception(f"Project not found for version data: {version_key}")

    # Process Input/Output
    input_val = get_attribute(attributes, "input_value")
    if (
        input_val in [None, "", "[]", []]
        and attributes.get(SpanAttributes.RAW_INPUT) is not None
    ):
        input_val = attributes.get(SpanAttributes.RAW_INPUT)
    input_val = DECODER.parse_nested_json(input_val) if input_val else None

    output_val = get_attribute(attributes, "output_value")
    if (
        output_val in [None, "", "[]", []]
        and attributes.get(SpanAttributes.RAW_OUTPUT) is not None
    ):
        output_val = attributes.get(SpanAttributes.RAW_OUTPUT)
    output_val = DECODER.parse_nested_json(output_val) if output_val else None

    # Prepare End User data
    end_user = None
    if attributes.get(SpanAttributes.USER_ID):
        end_user = {
            "user_id": attributes.get(SpanAttributes.USER_ID),
            "user_id_type": get_user_id_type(
                attributes.get(SpanAttributes.USER_ID_TYPE)
            ),
            "user_id_hash": attributes.get(SpanAttributes.USER_ID_HASH),
            "metadata": attributes.get(SpanAttributes.USER_METADATA, {}),
            "project": project,
        }

    # Prepare Prompt Details
    prompt_details = None
    if attributes.get(SpanAttributes.PROMPT_TEMPLATE_NAME):
        prompt_details = {
            "prompt_template_name": attributes.get(SpanAttributes.PROMPT_TEMPLATE_NAME),
            "prompt_template_version": attributes.get(
                SpanAttributes.PROMPT_TEMPLATE_VERSION
            ),
            "prompt_template_label": attributes.get(
                SpanAttributes.PROMPT_TEMPLATE_LABEL
            ),
            "prompt_template_variables": attributes.get(
                SpanAttributes.PROMPT_TEMPLATE_VARIABLES
            ),
        }

    # Construct the final ObservationSpan dictionary
    trace_id = otel_span.get("trace_id")
    latency = otel_span.get("latency")

    # Use AttributeRegistry for token counts (supports multiple conventions)
    prompt_tokens = get_attribute(attributes, "input_tokens")
    completion_tokens = get_attribute(attributes, "output_tokens")
    total_tokens = get_attribute(attributes, "total_tokens")

    # Use AttributeRegistry for model name (supports multiple conventions)
    model = get_attribute(attributes, "model_name")
    cost = calculate_cost(
        attributes=attributes,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        model=model,
        organization_id=organization_id,
    )
    user_cost_input = attributes.get("gen_ai.cost.input") or attributes.get(
        "llm.cost.prompt"
    )
    user_cost_output = attributes.get("gen_ai.cost.output") or attributes.get(
        "llm.cost.completion"
    )
    if cost is not None:
        try:
            cost = float(cost)
        except (ValueError, TypeError):
            cost = 0
    elif user_cost_input is not None or user_cost_output is not None:
        try:
            cost = float(user_cost_input or 0) + float(user_cost_output or 0)
        except (ValueError, TypeError):
            cost = 0
    else:
        cost = calculate_cost_from_tokens(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            model=model,
            organization_id=organization_id,
        )

    # Use AttributeRegistry for provider (supports multiple conventions)
    provider = attributes.get(SpanAttributes.PROVIDER_NAME) or get_attribute(
        attributes, "provider"
    )

    # Extract resource attributes if present (for OTLP format)
    resource_attributes = otel_span.get("resource", {}).get("attributes", {})

    # Parse attributes once for both fields (backward compatibility)
    parsed_attributes = DECODER.parse_nested_json(attributes)

    observation_span_dict = {
        "id": otel_span.get("span_id"),
        "name": otel_span.get("name", ""),
        "observation_type": get_observation_type(span_kind),
        "operation_name": operation_name,
        "start_time": timezone.make_aware(
            datetime.fromtimestamp(otel_span.get("start_time", 0) / 1e9)
        ),
        "end_time": timezone.make_aware(
            datetime.fromtimestamp(otel_span.get("end_time", 0) / 1e9)
        ),
        "input": input_val,
        "output": output_val,
        "model": model,
        "model_parameters": DECODER.parse_json_string(
            attributes.get(SpanAttributes.REQUEST_PARAMETERS)
        ),
        "latency_ms": latency,
        "org_id": organization_id,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "response_time": float(latency) if latency else None,
        "tags": DECODER.parse_json_string(attributes.get(SpanAttributes.TAG_TAGS, [])),
        "metadata": (
            DECODER.parse_json_string(otel_span.get("metadata"))
            if otel_span.get("metadata")
            else None
        ),
        "span_events": DECODER.parse_json_string(otel_span.get("events", [])),
        "status": otel_span.get("status", "UNSET"),
        "status_message": otel_span.get("status_message"),
        "provider": provider,
        "parent_span_id": otel_span.get("parent_id"),
        "project": project,
        **({"project_version": project_version} if project_version else {}),
        "trace_id": trace_id,
        # Unified attribute storage - span_attributes is the canonical source
        # eval_attributes kept for backward compatibility (same data)
        "span_attributes": parsed_attributes,
        "eval_attributes": parsed_attributes,  # Deprecated: same as span_attributes
        "eval_status": StatusType.NOT_STARTED.value,
        "cost": cost,
        "resource_attributes": resource_attributes,
        "semconv_source": semconv_source,
    }

    return {
        "end_user": end_user,
        "prompt_details": prompt_details,
        "trace": trace_id,
        "observation_span": observation_span_dict,
        "project": project,
        "project_version": project_version,
        "eval_tags": project_version.eval_tags if project_version else [],
        "session_name": attributes.get(SpanAttributes.SESSION_ID),
        "project_type": project_type if project_type else "experiment",
    }


def bulk_convert_otel_spans_to_observation_spans(
    otel_spans, organization_id=None, user_id=None, workspace_id=None
):
    """
    Converts a list of OTel span dictionaries to ObservationSpan objects with mapped fields in bulk.
    This function orchestrates the extraction, fetching/creation, and transformation of data.
    """
    try:
        # 1. Extract unique keys for projects and versions to fetch
        project_keys, version_keys = _extract_project_and_version_keys(
            otel_spans, organization_id, user_id, workspace_id
        )

        # 2. Bulk fetch or create all necessary related objects
        projects = _bulk_get_or_create_projects(project_keys)
        project_versions = _bulk_get_or_create_project_versions(
            version_keys, projects, organization_id
        )

        # 3. Process each span individually with the pre-fetched data
        results = [
            _convert_single_span(span, projects, project_versions, organization_id)
            for span in otel_spans
        ]

        return results

    except ResourceLimitError:
        raise
    except Exception as e:
        import traceback

        logger.error(
            f"Error converting OTel spans in bulk: {str(e)}\n{traceback.format_exc()}"
        )
        raise ValueError(
            f"Error converting OTel spans to Observation spans in bulk: {str(e)}"
        )


def calculate_cost(
    attributes: dict,
    prompt_tokens: Optional[int],
    completion_tokens: Optional[int],
    model: Optional[str],
    organization_id: Optional[str] = None,
):
    """
    Calculates cost for a span. Checks user-provided cost attributes first
    (gen_ai.cost.total, gen_ai.cost.input, gen_ai.cost.output and their
    OpenInference equivalents), then falls back to token-based calculation.
    """
    # Check user-provided cost attributes (uses AttributeRegistry aliasing
    # to support gen_ai.cost.* and llm.cost.* conventions automatically)
    user_cost_total = get_attribute(attributes, "cost_total")
    user_cost_input = get_attribute(attributes, "cost_input")
    user_cost_output = get_attribute(attributes, "cost_output")

    if user_cost_total is not None:
        try:
            return float(user_cost_total)
        except (ValueError, TypeError):
            return 0

    if user_cost_input is not None or user_cost_output is not None:
        try:
            return float(user_cost_input or 0) + float(user_cost_output or 0)
        except (ValueError, TypeError):
            return 0

    # Fall back to token-based calculation
    return calculate_cost_from_tokens(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        model=model,
        organization_id=organization_id,
    )


def calculate_cost_from_tokens(
    prompt_tokens: Optional[int],
    completion_tokens: Optional[int],
    model: Optional[str],
    organization_id: Optional[str] = None,
):
    """
    Calculates the cost of a model call based on the number of prompt and completion tokens.
    """
    prompt_tokens = int(prompt_tokens) if prompt_tokens is not None else 0
    completion_tokens = int(completion_tokens) if completion_tokens is not None else 0
    cost = 0
    if model and (prompt_tokens > 0 or completion_tokens > 0):
        model_cost_obj = model_cost.get(model)
        if model_cost_obj:
            prompt_tokens_cost_usd_dollar, completion_tokens_cost_usd_dollar = (
                cost_per_token(
                    model=model,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                )
            )
            cost = prompt_tokens_cost_usd_dollar + completion_tokens_cost_usd_dollar
        else:
            if organization_id is not None:
                cache_key = f"custom_model_pricing:{organization_id}:{model}"
                custom_pricing = cache.get(cache_key)
                if custom_pricing is None:
                    try:
                        custom_model = CustomAIModel.objects.get(
                            organization_id=organization_id, user_model_id=model
                        )
                        input_cost = (
                            custom_model.input_token_cost
                            if custom_model.input_token_cost is not None
                            else 0.0
                        )
                        output_cost = (
                            custom_model.output_token_cost
                            if custom_model.output_token_cost is not None
                            else 0.0
                        )

                        # Ensure costs are floats for consistent calculations
                        if not isinstance(input_cost, float):
                            input_cost = float(input_cost)

                        if not isinstance(output_cost, float):
                            output_cost = float(output_cost)

                        custom_pricing = {
                            "input_cost": input_cost,
                            "output_cost": output_cost,
                        }
                        cache.set(cache_key, custom_pricing, 86400)  # 24 hours
                    except CustomAIModel.DoesNotExist:
                        logger.warning(
                            f"Custom model pricing not found for org_id={organization_id}, model={model}"
                        )
                        cache.set(cache_key, {"not_found": True}, 86400)  # 24 hours
                        cost = 0
                        return cost

                if custom_pricing.get("not_found"):
                    cost = 0
                else:
                    prompt_tokens_cost_usd_dollar = prompt_tokens * (
                        custom_pricing["input_cost"] / 1000
                    )
                    completion_tokens_cost_usd_dollar = completion_tokens * (
                        custom_pricing["output_cost"] / 1000
                    )
                    cost = (
                        prompt_tokens_cost_usd_dollar
                        + completion_tokens_cost_usd_dollar
                    )

    return cost
