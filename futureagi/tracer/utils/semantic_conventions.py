"""
Semantic Conventions for GenAI Observability

This module provides attribute aliasing to support OTEL GenAI semantic conventions
as the primary standard, with backward compatibility for legacy conventions.

Primary: OTEL GenAI - OpenTelemetry GenAI SIG (gen_ai.*)
Legacy (backward compat): OpenInference, OpenLLMetry

Reference: https://opentelemetry.io/docs/specs/semconv/gen-ai/
"""

from enum import Enum
from typing import Any, Optional


class SemanticConvention(str, Enum):
    """Supported semantic convention sources."""

    FI = "fi"
    OTEL_GENAI = "otel_genai"
    OPENINFERENCE = "openinference"
    OPENLLMETRY = "openllmetry"
    UNKNOWN = "unknown"


class AttributeAliases:
    """
    Attribute name mappings for OTEL GenAI semantic conventions.

    Each canonical attribute maps to a list of aliases in priority order:
    1. OTEL GenAI convention (primary - gen_ai.*)
    2. OpenInference convention (backward compat)
    3. OpenLLMetry convention (backward compat)
    """

    # Model & Provider
    MODEL_NAME = [
        "llm.model_name",  # FI
        "gen_ai.request.model",  # OTEL GenAI (request)
        "gen_ai.response.model",  # OTEL GenAI (response - may differ)
        "llm.request.model",  # OpenLLMetry
    ]

    PROVIDER = [
        "llm.system",  # FI
        "gen_ai.system",  # OTEL GenAI
        "gen_ai.provider.name",  # OTEL GenAI (provider name)
        "llm.vendor",  # OpenLLMetry
    ]

    # Token Usage
    INPUT_TOKENS = [
        "llm.token_count.prompt",  # FI
        "gen_ai.usage.input_tokens",  # OTEL GenAI
        "llm.usage.prompt_tokens",  # OpenLLMetry
    ]

    OUTPUT_TOKENS = [
        "llm.token_count.completion",  # FI
        "gen_ai.usage.output_tokens",  # OTEL GenAI
        "llm.usage.completion_tokens",  # OpenLLMetry
    ]

    TOTAL_TOKENS = [
        "llm.token_count.total",  # FI
        "gen_ai.usage.total_tokens",  # OTEL GenAI
        "llm.usage.total_tokens",  # OpenLLMetry
    ]

    # Span Kind
    SPAN_KIND = [
        "fi.span.kind",  # FI
        "gen_ai.span.kind",  # OTEL GenAI
        "llm.request.type",  # OpenLLMetry
        "openinference.span.kind",  # OpenInference (backward compat)
    ]

    # Operation Name (distinct from span kind)
    # span.kind = what the component IS (llm, agent, tool)
    # operation.name = what it DOES (chat, image_generation, speech_to_text)
    OPERATION_NAME = [
        "gen_ai.operation.name",  # OTEL GenAI
    ]

    # Messages
    INPUT_MESSAGES = [
        "llm.input_messages",  # FI
        "gen_ai.input.messages",  # OTEL GenAI
        "llm.prompts",  # OpenLLMetry
    ]

    OUTPUT_MESSAGES = [
        "llm.output_messages",  # FI
        "gen_ai.output.messages",  # OTEL GenAI
        "llm.completions",  # OpenLLMetry
    ]

    # Request Parameters
    TEMPERATURE = [
        "llm.invocation_parameters.temperature",  # FI
        "gen_ai.request.temperature",  # OTEL GenAI
    ]

    MAX_TOKENS = [
        "llm.invocation_parameters.max_tokens",  # FI
        "gen_ai.request.max_tokens",  # OTEL GenAI
    ]

    TOP_P = [
        "llm.invocation_parameters.top_p",  # FI
        "gen_ai.request.top_p",  # OTEL GenAI
    ]

    TOP_K = [
        "gen_ai.request.top_k",  # OTEL GenAI
    ]

    # Request Parameters (full object)
    REQUEST_PARAMETERS = [
        "gen_ai.request.parameters",  # OTEL GenAI
    ]

    # Additional Request Parameters
    FREQUENCY_PENALTY = [
        "gen_ai.request.frequency_penalty",  # OTEL GenAI
    ]

    PRESENCE_PENALTY = [
        "gen_ai.request.presence_penalty",  # OTEL GenAI
    ]

    SEED = [
        "gen_ai.request.seed",  # OTEL GenAI
    ]

    STOP_SEQUENCES = [
        "gen_ai.request.stop_sequences",  # OTEL GenAI
    ]

    CHOICE_COUNT = [
        "gen_ai.request.choice_count",  # OTEL GenAI
    ]

    ENCODING_FORMATS = [
        "gen_ai.request.encoding_formats",  # OTEL GenAI
    ]

    # Session & User
    SESSION_ID = [
        "session.id",  # FI / OpenInference
        "gen_ai.conversation.id",  # OTEL GenAI
    ]

    USER_ID = [
        "user.id",  # FI / OpenInference
        "enduser.id",  # OTEL GenAI
    ]

    # Tools & Functions
    TOOL_NAME = [
        "tool.name",  # FI
        "gen_ai.tool.name",  # OTEL GenAI
    ]

    TOOL_DEFINITIONS = [
        "llm.tools",  # FI
        "gen_ai.tool.definitions",  # OTEL GenAI
    ]

    TOOL_CALL_ID = [
        "tool_call.id",  # FI
        "gen_ai.tool.call.id",  # OTEL GenAI
    ]

    TOOL_CALL_ARGUMENTS = [
        "gen_ai.tool.call.arguments",  # OTEL GenAI
    ]

    TOOL_TYPE = [
        "gen_ai.tool.type",  # OTEL GenAI
    ]

    TOOL_DESCRIPTION = [
        "gen_ai.tool.description",  # OTEL GenAI
    ]

    TOOL_CALL_RESULT = [
        "gen_ai.tool.call.result",  # OTEL GenAI
    ]

    # Response
    RESPONSE_ID = [
        "gen_ai.response.id",  # OTEL GenAI
    ]

    FINISH_REASONS = [
        "gen_ai.response.finish_reasons",  # OTEL GenAI
    ]

    # Output
    OUTPUT_TYPE = [
        "gen_ai.output.type",  # OTEL GenAI
    ]

    # System Instructions
    SYSTEM_INSTRUCTIONS = [
        "gen_ai.system_instructions",  # OTEL GenAI
    ]

    # Evaluation (OTEL GenAI spec)
    EVAL_NAME = [
        "eval.name",  # FI
        "gen_ai.evaluation.name",  # OTEL GenAI
    ]

    EVAL_SCORE = [
        "eval.score",  # FI
        "gen_ai.evaluation.score.value",  # OTEL GenAI
    ]

    EVAL_LABEL = [
        "eval.label",  # FI
        "gen_ai.evaluation.score.label",  # OTEL GenAI
    ]

    EVAL_EXPLANATION = [
        "gen_ai.evaluation.explanation",  # OTEL GenAI
    ]

    # Agent
    AGENT_ID = [
        "gen_ai.agent.id",  # OTEL GenAI
    ]

    AGENT_NAME = [
        "gen_ai.agent.name",  # OTEL GenAI
    ]

    AGENT_DESCRIPTION = [
        "gen_ai.agent.description",  # OTEL GenAI
    ]

    # Context
    PROMPT_NAME = [
        "gen_ai.prompt.name",  # OTEL GenAI
    ]

    DATA_SOURCE_ID = [
        "gen_ai.data_source.id",  # OTEL GenAI
    ]

    # Embeddings
    EMBEDDINGS_DIMENSION_COUNT = [
        "gen_ai.embeddings.dimension.count",  # OTEL GenAI
    ]

    # Token Type
    TOKEN_TYPE = [
        "gen_ai.token.type",  # OTEL GenAI
    ]

    # Prompt Template (custom extension)
    PROMPT_TEMPLATE_NAME = [
        "gen_ai.prompt.template.name",  # OTEL GenAI (custom extension)
    ]

    PROMPT_TEMPLATE_VERSION = [
        "gen_ai.prompt.template.version",  # OTEL GenAI (custom extension)
    ]

    PROMPT_TEMPLATE_LABEL = [
        "gen_ai.prompt.template.label",  # OTEL GenAI (custom extension)
    ]

    PROMPT_TEMPLATE_VARIABLES = [
        "gen_ai.prompt.template.variables",  # OTEL GenAI (custom extension)
    ]

    # Input/Output Values (OpenInference compat)
    INPUT_VALUE = [
        "fi.llm.input",  # FI
        "input.value",  # OpenInference
    ]

    OUTPUT_VALUE = [
        "fi.llm.output",  # FI
        "output.value",  # OpenInference
    ]

    INPUT_MIME_TYPE = [
        "input.mime_type",  # OpenInference
    ]

    OUTPUT_MIME_TYPE = [
        "output.mime_type",  # OpenInference
    ]

    # =========================================================================
    # New attributes from Unified Tracing Convention (155 total)
    # =========================================================================

    # --- Universal (error & duration) ---
    ERROR_TYPE = [
        "error.type",  # OTEL
    ]

    ERROR_MESSAGE = [
        "error.message",  # OTEL
    ]

    CLIENT_OPERATION_DURATION = [
        "gen_ai.client.operation.duration",  # OTEL GenAI
    ]

    # --- Token Usage (cache) ---
    CACHE_READ_TOKENS = [
        "gen_ai.usage.cache_read_tokens",  # OTEL GenAI
        "llm.token_count.prompt_tokens_details.cached",  # OpenInference
    ]

    CACHE_WRITE_TOKENS = [
        "gen_ai.usage.cache_write_tokens",  # OTEL GenAI
        "llm.token_count.prompt_tokens_details.cache_write",  # OpenInference
    ]

    # --- Cost ---
    COST_INPUT = [
        "gen_ai.cost.input",  # OTEL GenAI
        "llm.cost.prompt",  # OpenInference
    ]

    COST_OUTPUT = [
        "gen_ai.cost.output",  # OTEL GenAI
        "llm.cost.completion",  # OpenInference
    ]

    COST_TOTAL = [
        "gen_ai.cost.total",  # OTEL GenAI
        "llm.cost.total",  # OpenInference
    ]

    COST_CACHE_READ = [
        "gen_ai.cost.cache_read",  # OTEL GenAI
    ]

    COST_CACHE_WRITE = [
        "gen_ai.cost.cache_write",  # OTEL GenAI
    ]

    # --- Agent Graph ---
    AGENT_GRAPH_NODE_ID = [
        "gen_ai.agent.graph.node_id",  # OTEL GenAI
        "graph.node.id",  # OpenInference
    ]

    AGENT_GRAPH_NODE_NAME = [
        "gen_ai.agent.graph.node_name",  # OTEL GenAI
        "graph.node.name",  # OpenInference
    ]

    AGENT_GRAPH_PARENT_NODE_ID = [
        "gen_ai.agent.graph.parent_node_id",  # OTEL GenAI
        "graph.node.parent_id",  # OpenInference
    ]

    # --- Retriever ---
    RETRIEVAL_DOCUMENTS = [
        "gen_ai.retrieval.documents",  # OTEL GenAI
        "retrieval.documents",  # OpenInference
    ]

    RETRIEVAL_QUERY = [
        "gen_ai.retrieval.query",  # OTEL GenAI
    ]

    RETRIEVAL_TOP_K = [
        "gen_ai.retrieval.top_k",  # OTEL GenAI
    ]

    # --- Embedding ---
    EMBEDDING_VECTORS = [
        "gen_ai.embeddings.vectors",  # OTEL GenAI
        "embedding.embeddings",  # OpenInference
    ]

    # --- Evaluation ---
    EVAL_TARGET_SPAN_ID = [
        "gen_ai.evaluation.target_span_id",  # OTEL GenAI
    ]

    # --- Guardrail ---
    GUARDRAIL_NAME = [
        "gen_ai.guardrail.name",  # OTEL GenAI
    ]

    GUARDRAIL_TYPE = [
        "gen_ai.guardrail.type",  # OTEL GenAI
    ]

    GUARDRAIL_RESULT = [
        "gen_ai.guardrail.result",  # OTEL GenAI
    ]

    GUARDRAIL_SCORE = [
        "gen_ai.guardrail.score",  # OTEL GenAI
    ]

    GUARDRAIL_CATEGORIES = [
        "gen_ai.guardrail.categories",  # OTEL GenAI
    ]

    GUARDRAIL_MODIFIED_OUTPUT = [
        "gen_ai.guardrail.modified_output",  # OTEL GenAI
    ]

    # --- Prompt Template (additional) ---
    PROMPT_VENDOR = [
        "gen_ai.prompt.vendor",  # OTEL GenAI
        "prompt.vendor",  # OpenInference
    ]

    PROMPT_ID = [
        "gen_ai.prompt.id",  # OTEL GenAI
        "prompt.id",  # OpenInference
    ]

    # --- Voice / Conversation ---
    VOICE_CALL_ID = [
        "gen_ai.voice.call_id",  # Custom
    ]

    VOICE_PROVIDER = [
        "gen_ai.voice.provider",  # Custom
    ]

    VOICE_CALL_DURATION_SECS = [
        "gen_ai.voice.call_duration_secs",  # Custom
    ]

    VOICE_ENDED_REASON = [
        "gen_ai.voice.ended_reason",  # Custom
    ]

    VOICE_FROM_NUMBER = [
        "gen_ai.voice.from_number",  # Custom
    ]

    VOICE_TO_NUMBER = [
        "gen_ai.voice.to_number",  # Custom
    ]

    VOICE_CHANNEL_TYPE = [
        "gen_ai.voice.channel_type",  # Custom
    ]

    VOICE_TRANSCRIPT = [
        "gen_ai.voice.transcript",  # Custom
    ]

    VOICE_RECORDING_URL = [
        "gen_ai.voice.recording.url",  # Custom
    ]

    VOICE_RECORDING_STEREO_URL = [
        "gen_ai.voice.recording.stereo_url",  # Custom
    ]

    VOICE_RECORDING_CUSTOMER_URL = [
        "gen_ai.voice.recording.customer_url",  # Custom
    ]

    VOICE_RECORDING_ASSISTANT_URL = [
        "gen_ai.voice.recording.assistant_url",  # Custom
    ]

    VOICE_STT_MODEL = [
        "gen_ai.voice.stt.model",  # Custom
    ]

    VOICE_STT_PROVIDER = [
        "gen_ai.voice.stt.provider",  # Custom
    ]

    VOICE_STT_LANGUAGE = [
        "gen_ai.voice.stt.language",  # Custom
    ]

    VOICE_TTS_MODEL = [
        "gen_ai.voice.tts.model",  # Custom
    ]

    VOICE_TTS_PROVIDER = [
        "gen_ai.voice.tts.provider",  # Custom
    ]

    VOICE_TTS_VOICE_ID = [
        "gen_ai.voice.tts.voice_id",  # Custom
    ]

    VOICE_LATENCY_MODEL_AVG_MS = [
        "gen_ai.voice.latency.model_avg_ms",  # Custom
    ]

    VOICE_LATENCY_VOICE_AVG_MS = [
        "gen_ai.voice.latency.voice_avg_ms",  # Custom
    ]

    VOICE_LATENCY_TRANSCRIBER_AVG_MS = [
        "gen_ai.voice.latency.transcriber_avg_ms",  # Custom
    ]

    VOICE_LATENCY_TURN_AVG_MS = [
        "gen_ai.voice.latency.turn_avg_ms",  # Custom
    ]

    VOICE_LATENCY_TTFB_MS = [
        "gen_ai.voice.latency.ttfb_ms",  # Custom
    ]

    VOICE_INTERRUPTIONS_USER_COUNT = [
        "gen_ai.voice.interruptions.user_count",  # Custom
    ]

    VOICE_INTERRUPTIONS_ASSISTANT_COUNT = [
        "gen_ai.voice.interruptions.assistant_count",  # Custom
    ]

    VOICE_COST_TOTAL = [
        "gen_ai.voice.cost.total",  # Custom
    ]

    VOICE_COST_STT = [
        "gen_ai.voice.cost.stt",  # Custom
    ]

    VOICE_COST_TTS = [
        "gen_ai.voice.cost.tts",  # Custom
    ]

    VOICE_COST_LLM = [
        "gen_ai.voice.cost.llm",  # Custom
    ]

    VOICE_COST_TELEPHONY = [
        "gen_ai.voice.cost.telephony",  # Custom
    ]

    # --- Image Generation ---
    IMAGE_PROMPT = [
        "gen_ai.image.prompt",  # Custom
    ]

    IMAGE_NEGATIVE_PROMPT = [
        "gen_ai.image.negative_prompt",  # Custom
    ]

    IMAGE_WIDTH = [
        "gen_ai.image.width",  # Custom
    ]

    IMAGE_HEIGHT = [
        "gen_ai.image.height",  # Custom
    ]

    IMAGE_SIZE = [
        "gen_ai.image.size",  # Custom
    ]

    IMAGE_QUALITY = [
        "gen_ai.image.quality",  # Custom
    ]

    IMAGE_STYLE = [
        "gen_ai.image.style",  # Custom
    ]

    IMAGE_STEPS = [
        "gen_ai.image.steps",  # Custom
    ]

    IMAGE_GUIDANCE_SCALE = [
        "gen_ai.image.guidance_scale",  # Custom
    ]

    IMAGE_SEED = [
        "gen_ai.image.seed",  # Custom
    ]

    IMAGE_FORMAT = [
        "gen_ai.image.format",  # Custom
    ]

    IMAGE_COUNT = [
        "gen_ai.image.count",  # Custom
    ]

    IMAGE_REVISED_PROMPT = [
        "gen_ai.image.revised_prompt",  # Custom
    ]

    IMAGE_OUTPUT_URLS = [
        "gen_ai.image.output_urls",  # Custom
    ]

    # --- Computer Use ---
    COMPUTER_USE_ACTION = [
        "gen_ai.computer_use.action",  # Custom
    ]

    COMPUTER_USE_COORDINATE_X = [
        "gen_ai.computer_use.coordinate_x",  # Custom
    ]

    COMPUTER_USE_COORDINATE_Y = [
        "gen_ai.computer_use.coordinate_y",  # Custom
    ]

    COMPUTER_USE_TEXT = [
        "gen_ai.computer_use.text",  # Custom
    ]

    COMPUTER_USE_KEY = [
        "gen_ai.computer_use.key",  # Custom
    ]

    COMPUTER_USE_BUTTON = [
        "gen_ai.computer_use.button",  # Custom
    ]

    COMPUTER_USE_SCROLL_DIRECTION = [
        "gen_ai.computer_use.scroll_direction",  # Custom
    ]

    COMPUTER_USE_SCROLL_AMOUNT = [
        "gen_ai.computer_use.scroll_amount",  # Custom
    ]

    COMPUTER_USE_SCREENSHOT = [
        "gen_ai.computer_use.screenshot",  # Custom
    ]

    COMPUTER_USE_ENVIRONMENT = [
        "gen_ai.computer_use.environment",  # Custom
    ]

    COMPUTER_USE_VIEWPORT_WIDTH = [
        "gen_ai.computer_use.viewport_width",  # Custom
    ]

    COMPUTER_USE_VIEWPORT_HEIGHT = [
        "gen_ai.computer_use.viewport_height",  # Custom
    ]

    COMPUTER_USE_CURRENT_URL = [
        "gen_ai.computer_use.current_url",  # Custom
    ]

    COMPUTER_USE_ELEMENT_SELECTOR = [
        "gen_ai.computer_use.element_selector",  # Custom
    ]

    COMPUTER_USE_RESULT = [
        "gen_ai.computer_use.result",  # Custom
    ]

    # --- Performance & Streaming ---
    TIME_TO_FIRST_TOKEN = [
        "gen_ai.server.time_to_first_token",  # OTEL GenAI
    ]

    TIME_PER_OUTPUT_TOKEN = [
        "gen_ai.server.time_per_output_token",  # OTEL GenAI
    ]

    SERVER_QUEUE_TIME = [
        "gen_ai.server.queue_time",  # OTEL GenAI
    ]

    # --- Reranker ---
    RERANKER_MODEL = [
        "gen_ai.reranker.model",  # Custom
        "reranker.model_name",  # OpenInference
    ]

    RERANKER_QUERY = [
        "gen_ai.reranker.query",  # Custom
        "reranker.query",  # OpenInference
    ]

    RERANKER_TOP_N = [
        "gen_ai.reranker.top_n",  # Custom
        "reranker.top_k",  # OpenInference
    ]

    RERANKER_INPUT_DOCUMENTS = [
        "gen_ai.reranker.input_documents",  # Custom
        "reranker.input_documents",  # OpenInference
    ]

    RERANKER_OUTPUT_DOCUMENTS = [
        "gen_ai.reranker.output_documents",  # Custom
        "reranker.output_documents",  # OpenInference
    ]

    # --- Audio ---
    AUDIO_URL = [
        "gen_ai.audio.url",  # Custom
        "audio.url",  # OpenInference
    ]

    AUDIO_MIME_TYPE = [
        "gen_ai.audio.mime_type",  # Custom
        "audio.mime_type",  # OpenInference
    ]

    AUDIO_TRANSCRIPT = [
        "gen_ai.audio.transcript",  # Custom
        "audio.transcript",  # OpenInference
    ]

    AUDIO_DURATION_SECS = [
        "gen_ai.audio.duration_secs",  # Custom
    ]

    AUDIO_LANGUAGE = [
        "gen_ai.audio.language",  # Custom
    ]

    # --- Server / Infrastructure ---
    SERVER_ADDRESS = [
        "server.address",  # OTEL
    ]

    SERVER_PORT = [
        "server.port",  # OTEL
    ]


class AttributeRegistry:
    """
    Central registry for attribute aliasing using OTEL GenAI semantic conventions.

    Usage:
        model = AttributeRegistry.get_value(attributes, 'model_name')
        # This will try gen_ai.request.model, gen_ai.response.model
    """

    # Map canonical names to their alias lists
    _CANONICAL_TO_ALIASES = {
        "model_name": AttributeAliases.MODEL_NAME,
        "provider": AttributeAliases.PROVIDER,
        "input_tokens": AttributeAliases.INPUT_TOKENS,
        "output_tokens": AttributeAliases.OUTPUT_TOKENS,
        "total_tokens": AttributeAliases.TOTAL_TOKENS,
        "span_kind": AttributeAliases.SPAN_KIND,
        "operation_name": AttributeAliases.OPERATION_NAME,
        "input_messages": AttributeAliases.INPUT_MESSAGES,
        "output_messages": AttributeAliases.OUTPUT_MESSAGES,
        "temperature": AttributeAliases.TEMPERATURE,
        "max_tokens": AttributeAliases.MAX_TOKENS,
        "top_p": AttributeAliases.TOP_P,
        "top_k": AttributeAliases.TOP_K,
        "request_parameters": AttributeAliases.REQUEST_PARAMETERS,
        "session_id": AttributeAliases.SESSION_ID,
        "user_id": AttributeAliases.USER_ID,
        "tool_name": AttributeAliases.TOOL_NAME,
        "tool_definitions": AttributeAliases.TOOL_DEFINITIONS,
        "tool_call_id": AttributeAliases.TOOL_CALL_ID,
        "tool_call_arguments": AttributeAliases.TOOL_CALL_ARGUMENTS,
        "tool_type": AttributeAliases.TOOL_TYPE,
        "tool_description": AttributeAliases.TOOL_DESCRIPTION,
        "tool_call_result": AttributeAliases.TOOL_CALL_RESULT,
        "response_id": AttributeAliases.RESPONSE_ID,
        "finish_reasons": AttributeAliases.FINISH_REASONS,
        "output_type": AttributeAliases.OUTPUT_TYPE,
        "system_instructions": AttributeAliases.SYSTEM_INSTRUCTIONS,
        "eval_name": AttributeAliases.EVAL_NAME,
        "eval_score": AttributeAliases.EVAL_SCORE,
        "eval_label": AttributeAliases.EVAL_LABEL,
        "eval_explanation": AttributeAliases.EVAL_EXPLANATION,
        "agent_id": AttributeAliases.AGENT_ID,
        "agent_name": AttributeAliases.AGENT_NAME,
        "agent_description": AttributeAliases.AGENT_DESCRIPTION,
        "prompt_name": AttributeAliases.PROMPT_NAME,
        "data_source_id": AttributeAliases.DATA_SOURCE_ID,
        "embeddings_dimension_count": AttributeAliases.EMBEDDINGS_DIMENSION_COUNT,
        "token_type": AttributeAliases.TOKEN_TYPE,
        "prompt_template_name": AttributeAliases.PROMPT_TEMPLATE_NAME,
        "prompt_template_version": AttributeAliases.PROMPT_TEMPLATE_VERSION,
        "prompt_template_label": AttributeAliases.PROMPT_TEMPLATE_LABEL,
        "prompt_template_variables": AttributeAliases.PROMPT_TEMPLATE_VARIABLES,
        "input_value": AttributeAliases.INPUT_VALUE,
        "output_value": AttributeAliases.OUTPUT_VALUE,
        "input_mime_type": AttributeAliases.INPUT_MIME_TYPE,
        "output_mime_type": AttributeAliases.OUTPUT_MIME_TYPE,
        # Additional request parameters
        "frequency_penalty": AttributeAliases.FREQUENCY_PENALTY,
        "presence_penalty": AttributeAliases.PRESENCE_PENALTY,
        "seed": AttributeAliases.SEED,
        "stop_sequences": AttributeAliases.STOP_SEQUENCES,
        "choice_count": AttributeAliases.CHOICE_COUNT,
        "encoding_formats": AttributeAliases.ENCODING_FORMATS,
        # Universal (error & duration)
        "error_type": AttributeAliases.ERROR_TYPE,
        "error_message": AttributeAliases.ERROR_MESSAGE,
        "client_operation_duration": AttributeAliases.CLIENT_OPERATION_DURATION,
        # Token cache
        "cache_read_tokens": AttributeAliases.CACHE_READ_TOKENS,
        "cache_write_tokens": AttributeAliases.CACHE_WRITE_TOKENS,
        # Cost
        "cost_input": AttributeAliases.COST_INPUT,
        "cost_output": AttributeAliases.COST_OUTPUT,
        "cost_total": AttributeAliases.COST_TOTAL,
        "cost_cache_read": AttributeAliases.COST_CACHE_READ,
        "cost_cache_write": AttributeAliases.COST_CACHE_WRITE,
        # Agent graph
        "agent_graph_node_id": AttributeAliases.AGENT_GRAPH_NODE_ID,
        "agent_graph_node_name": AttributeAliases.AGENT_GRAPH_NODE_NAME,
        "agent_graph_parent_node_id": AttributeAliases.AGENT_GRAPH_PARENT_NODE_ID,
        # Retriever
        "retrieval_documents": AttributeAliases.RETRIEVAL_DOCUMENTS,
        "retrieval_query": AttributeAliases.RETRIEVAL_QUERY,
        "retrieval_top_k": AttributeAliases.RETRIEVAL_TOP_K,
        # Embedding
        "embedding_vectors": AttributeAliases.EMBEDDING_VECTORS,
        # Evaluation
        "eval_target_span_id": AttributeAliases.EVAL_TARGET_SPAN_ID,
        # Guardrail
        "guardrail_name": AttributeAliases.GUARDRAIL_NAME,
        "guardrail_type": AttributeAliases.GUARDRAIL_TYPE,
        "guardrail_result": AttributeAliases.GUARDRAIL_RESULT,
        "guardrail_score": AttributeAliases.GUARDRAIL_SCORE,
        "guardrail_categories": AttributeAliases.GUARDRAIL_CATEGORIES,
        "guardrail_modified_output": AttributeAliases.GUARDRAIL_MODIFIED_OUTPUT,
        # Prompt template (additional)
        "prompt_vendor": AttributeAliases.PROMPT_VENDOR,
        "prompt_id": AttributeAliases.PROMPT_ID,
        # Voice / Conversation
        "voice_call_id": AttributeAliases.VOICE_CALL_ID,
        "voice_provider": AttributeAliases.VOICE_PROVIDER,
        "voice_call_duration_secs": AttributeAliases.VOICE_CALL_DURATION_SECS,
        "voice_ended_reason": AttributeAliases.VOICE_ENDED_REASON,
        "voice_from_number": AttributeAliases.VOICE_FROM_NUMBER,
        "voice_to_number": AttributeAliases.VOICE_TO_NUMBER,
        "voice_channel_type": AttributeAliases.VOICE_CHANNEL_TYPE,
        "voice_transcript": AttributeAliases.VOICE_TRANSCRIPT,
        "voice_recording_url": AttributeAliases.VOICE_RECORDING_URL,
        "voice_recording_stereo_url": AttributeAliases.VOICE_RECORDING_STEREO_URL,
        "voice_recording_customer_url": AttributeAliases.VOICE_RECORDING_CUSTOMER_URL,
        "voice_recording_assistant_url": AttributeAliases.VOICE_RECORDING_ASSISTANT_URL,
        "voice_stt_model": AttributeAliases.VOICE_STT_MODEL,
        "voice_stt_provider": AttributeAliases.VOICE_STT_PROVIDER,
        "voice_stt_language": AttributeAliases.VOICE_STT_LANGUAGE,
        "voice_tts_model": AttributeAliases.VOICE_TTS_MODEL,
        "voice_tts_provider": AttributeAliases.VOICE_TTS_PROVIDER,
        "voice_tts_voice_id": AttributeAliases.VOICE_TTS_VOICE_ID,
        "voice_latency_model_avg_ms": AttributeAliases.VOICE_LATENCY_MODEL_AVG_MS,
        "voice_latency_voice_avg_ms": AttributeAliases.VOICE_LATENCY_VOICE_AVG_MS,
        "voice_latency_transcriber_avg_ms": AttributeAliases.VOICE_LATENCY_TRANSCRIBER_AVG_MS,
        "voice_latency_turn_avg_ms": AttributeAliases.VOICE_LATENCY_TURN_AVG_MS,
        "voice_latency_ttfb_ms": AttributeAliases.VOICE_LATENCY_TTFB_MS,
        "voice_interruptions_user_count": AttributeAliases.VOICE_INTERRUPTIONS_USER_COUNT,
        "voice_interruptions_assistant_count": AttributeAliases.VOICE_INTERRUPTIONS_ASSISTANT_COUNT,
        "voice_cost_total": AttributeAliases.VOICE_COST_TOTAL,
        "voice_cost_stt": AttributeAliases.VOICE_COST_STT,
        "voice_cost_tts": AttributeAliases.VOICE_COST_TTS,
        "voice_cost_llm": AttributeAliases.VOICE_COST_LLM,
        "voice_cost_telephony": AttributeAliases.VOICE_COST_TELEPHONY,
        # Image generation
        "image_prompt": AttributeAliases.IMAGE_PROMPT,
        "image_negative_prompt": AttributeAliases.IMAGE_NEGATIVE_PROMPT,
        "image_width": AttributeAliases.IMAGE_WIDTH,
        "image_height": AttributeAliases.IMAGE_HEIGHT,
        "image_size": AttributeAliases.IMAGE_SIZE,
        "image_quality": AttributeAliases.IMAGE_QUALITY,
        "image_style": AttributeAliases.IMAGE_STYLE,
        "image_steps": AttributeAliases.IMAGE_STEPS,
        "image_guidance_scale": AttributeAliases.IMAGE_GUIDANCE_SCALE,
        "image_seed": AttributeAliases.IMAGE_SEED,
        "image_format": AttributeAliases.IMAGE_FORMAT,
        "image_count": AttributeAliases.IMAGE_COUNT,
        "image_revised_prompt": AttributeAliases.IMAGE_REVISED_PROMPT,
        "image_output_urls": AttributeAliases.IMAGE_OUTPUT_URLS,
        # Computer use
        "computer_use_action": AttributeAliases.COMPUTER_USE_ACTION,
        "computer_use_coordinate_x": AttributeAliases.COMPUTER_USE_COORDINATE_X,
        "computer_use_coordinate_y": AttributeAliases.COMPUTER_USE_COORDINATE_Y,
        "computer_use_text": AttributeAliases.COMPUTER_USE_TEXT,
        "computer_use_key": AttributeAliases.COMPUTER_USE_KEY,
        "computer_use_button": AttributeAliases.COMPUTER_USE_BUTTON,
        "computer_use_scroll_direction": AttributeAliases.COMPUTER_USE_SCROLL_DIRECTION,
        "computer_use_scroll_amount": AttributeAliases.COMPUTER_USE_SCROLL_AMOUNT,
        "computer_use_screenshot": AttributeAliases.COMPUTER_USE_SCREENSHOT,
        "computer_use_environment": AttributeAliases.COMPUTER_USE_ENVIRONMENT,
        "computer_use_viewport_width": AttributeAliases.COMPUTER_USE_VIEWPORT_WIDTH,
        "computer_use_viewport_height": AttributeAliases.COMPUTER_USE_VIEWPORT_HEIGHT,
        "computer_use_current_url": AttributeAliases.COMPUTER_USE_CURRENT_URL,
        "computer_use_element_selector": AttributeAliases.COMPUTER_USE_ELEMENT_SELECTOR,
        "computer_use_result": AttributeAliases.COMPUTER_USE_RESULT,
        # Performance & streaming
        "time_to_first_token": AttributeAliases.TIME_TO_FIRST_TOKEN,
        "time_per_output_token": AttributeAliases.TIME_PER_OUTPUT_TOKEN,
        "server_queue_time": AttributeAliases.SERVER_QUEUE_TIME,
        # Reranker
        "reranker_model": AttributeAliases.RERANKER_MODEL,
        "reranker_query": AttributeAliases.RERANKER_QUERY,
        "reranker_top_n": AttributeAliases.RERANKER_TOP_N,
        "reranker_input_documents": AttributeAliases.RERANKER_INPUT_DOCUMENTS,
        "reranker_output_documents": AttributeAliases.RERANKER_OUTPUT_DOCUMENTS,
        # Audio
        "audio_url": AttributeAliases.AUDIO_URL,
        "audio_mime_type": AttributeAliases.AUDIO_MIME_TYPE,
        "audio_transcript": AttributeAliases.AUDIO_TRANSCRIPT,
        "audio_duration_secs": AttributeAliases.AUDIO_DURATION_SECS,
        "audio_language": AttributeAliases.AUDIO_LANGUAGE,
        # Server / Infrastructure
        "server_address": AttributeAliases.SERVER_ADDRESS,
        "server_port": AttributeAliases.SERVER_PORT,
    }

    # Attribute prefixes that indicate which convention is being used
    _CONVENTION_PREFIXES = {
        "fi.": SemanticConvention.FI,
        "gen_ai.": SemanticConvention.OTEL_GENAI,
        "openinference.": SemanticConvention.OPENINFERENCE,
        "llm.vendor": SemanticConvention.OPENLLMETRY,
    }

    @classmethod
    def get_value(
        cls, attributes: dict[str, Any], canonical_name: str, default: Any = None
    ) -> Any:
        """
        Get attribute value by trying all known aliases in priority order.

        Args:
            attributes: Dictionary of span attributes
            canonical_name: The canonical attribute name (e.g., 'model_name')
            default: Default value if no alias is found

        Returns:
            The first non-None value found, or the default
        """
        aliases = cls._CANONICAL_TO_ALIASES.get(canonical_name, [])

        for alias in aliases:
            value = cls._get_nested_value(attributes, alias)
            if value is not None:
                return value

        return default

    @classmethod
    def get_value_with_source(
        cls, attributes: dict[str, Any], canonical_name: str, default: Any = None
    ) -> tuple[Any, Optional[str]]:
        """
        Get attribute value and the alias that matched.

        Returns:
            Tuple of (value, matched_alias) or (default, None)
        """
        aliases = cls._CANONICAL_TO_ALIASES.get(canonical_name, [])

        for alias in aliases:
            value = cls._get_nested_value(attributes, alias)
            if value is not None:
                return value, alias

        return default, None

    @classmethod
    def detect_convention(cls, attributes: dict[str, Any]) -> SemanticConvention:
        """
        Detect which semantic convention the span is using based on attribute prefixes.

        Args:
            attributes: Dictionary of span attributes

        Returns:
            The detected SemanticConvention enum value
        """
        # Handle None input
        if attributes is None:
            return SemanticConvention.UNKNOWN

        # Check for convention-specific prefixes
        for key in attributes.keys():
            for prefix, convention in cls._CONVENTION_PREFIXES.items():
                if key.startswith(prefix):
                    return convention

        return SemanticConvention.UNKNOWN

    @classmethod
    def normalize_span_kind(cls, raw_value: str) -> str:
        """
        Normalize span kind/operation to the internal format.

        Maps various convention values to our internal span kinds:
        - llm, chain, tool, retriever, embedding, agent, reranker, guardrail, evaluator, conversation
        """
        if not raw_value:
            return "unknown"

        value = raw_value.lower().strip()

        # Direct mappings
        direct_map = {
            # OTEL GenAI operations
            "chat": "llm",
            "generate_content": "llm",
            "text_completion": "llm",
            "embeddings": "embedding",
            "execute_tool": "tool",
            "invoke": "chain",
            "invoke_agent": "agent",
            # Standard span kinds
            "llm": "llm",
            "chain": "chain",
            "tool": "tool",
            "retriever": "retriever",
            "embedding": "embedding",
            "agent": "agent",
            "reranker": "reranker",
            "guardrail": "guardrail",
            "evaluator": "evaluator",
            "conversation": "conversation",
            "internal": "internal",
        }

        return direct_map.get(value, "unknown")

    @staticmethod
    def _get_nested_value(attributes: dict[str, Any], key: str) -> Any:
        """
        Get nested attribute value using dot notation.

        Handles both flat keys (e.g., "gen_ai.request.model") and
        nested dicts (e.g., attributes["gen_ai"]["request"]["model"]).
        """
        if not attributes or not key:
            return None

        # First try direct lookup (flat key)
        if key in attributes:
            return attributes[key]

        # Then try nested lookup
        parts = key.split(".")
        current = attributes

        for part in parts:
            if not isinstance(current, dict):
                return None
            current = current.get(part)
            if current is None:
                return None

        return current

    @classmethod
    def get_all_values(cls, attributes: dict[str, Any]) -> dict[str, Any]:
        """
        Extract all canonical values from attributes.

        Returns:
            Dictionary mapping canonical names to their values
        """
        result = {}
        for canonical_name in cls._CANONICAL_TO_ALIASES.keys():
            value = cls.get_value(attributes, canonical_name)
            if value is not None:
                result[canonical_name] = value
        return result


# Convenience functions for direct use
def get_attribute(
    attributes: dict[str, Any], canonical_name: str, default: Any = None
) -> Any:
    """Convenience function to get attribute value using aliasing."""
    return AttributeRegistry.get_value(attributes, canonical_name, default)


def detect_semconv(attributes: dict[str, Any]) -> str:
    """Convenience function to detect semantic convention source."""
    return AttributeRegistry.detect_convention(attributes).value
