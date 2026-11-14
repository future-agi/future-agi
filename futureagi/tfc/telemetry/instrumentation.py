"""
Auto-instrumentation for Django, Celery, databases, HTTP clients, LLMs, and cloud services.

This module provides comprehensive OpenTelemetry instrumentation for:
- Web frameworks: Django, ASGI
- Task queues: Celery
- Databases: PostgreSQL (psycopg), Redis, ClickHouse
- HTTP clients: requests, httpx, urllib3, aiohttp
- LLM providers: OpenAI, Anthropic, Bedrock, Google AI, Vertex AI, LiteLLM (via traceAI)
- Agent frameworks: CrewAI
- Cloud services: AWS (boto3/botocore), MinIO
- Other: gRPC, Jinja2, logging

LLM instrumentation uses traceAI (https://github.com/future-agi/traceAI).
"""

from contextlib import contextmanager
from functools import wraps
from typing import Any, Callable, Optional

import structlog

logger = structlog.get_logger(__name__)


# =============================================================================
# Generic Instrumentation Helper
# =============================================================================


def _instrument_library(
    library_name: str,
    instrumentor_import: str,
    instrumentor_class: str,
    *,
    use_tracer_provider: bool = False,
    via: Optional[str] = None,
) -> bool:
    """
    Generic helper to instrument a library with OpenTelemetry.

    This eliminates code duplication across 30+ instrument_* functions.

    Args:
        library_name: Human-readable name for logging (e.g., "openai")
        instrumentor_import: Module path to import from (e.g., "traceai_openai")
        instrumentor_class: Class name to instantiate (e.g., "OpenAIInstrumentor")
        use_tracer_provider: Whether to pass tracer_provider to instrument()
        via: Optional "via" tag for logging (e.g., "traceai")

    Returns:
        True if instrumentation succeeded, False otherwise
    """
    try:
        # Dynamic import of the instrumentor
        import importlib

        module = importlib.import_module(instrumentor_import)
        InstrumentorClass = getattr(module, instrumentor_class)

        instrumentor = InstrumentorClass()
        if instrumentor.is_instrumented_by_opentelemetry:
            logger.debug(f"{library_name} already instrumented, skipping")
            return True

        if use_tracer_provider:
            from opentelemetry import trace

            tracer_provider = trace.get_tracer_provider()
            instrumentor.instrument(tracer_provider=tracer_provider)
        else:
            instrumentor.instrument()

        log_kwargs = {"library": library_name}
        if via:
            log_kwargs["via"] = via
        logger.info("instrumented", **log_kwargs)
        return True

    except ImportError as e:
        logger.warning(
            "instrumentation_unavailable", library=library_name, error=str(e)
        )
        return False
    except Exception as e:
        logger.error("instrumentation_failed", library=library_name, error=str(e))
        return False


# =============================================================================
# LLM Provider Instrumentation (via traceAI - https://github.com/future-agi/traceAI)
# =============================================================================


def instrument_openai() -> bool:
    """Instrument OpenAI SDK for LLM tracing (gen_ai.* attributes)."""
    return _instrument_library(
        "openai",
        "traceai_openai",
        "OpenAIInstrumentor",
        use_tracer_provider=True,
        via="traceai",
    )


def instrument_anthropic() -> bool:
    """Instrument Anthropic SDK for Claude tracing (gen_ai.* attributes)."""
    return _instrument_library(
        "anthropic",
        "traceai_anthropic",
        "AnthropicInstrumentor",
        use_tracer_provider=True,
        via="traceai",
    )


def instrument_bedrock() -> bool:
    """Instrument AWS Bedrock for LLM tracing (gen_ai.* attributes)."""
    return _instrument_library(
        "bedrock",
        "traceai_bedrock",
        "BedrockInstrumentor",
        use_tracer_provider=True,
        via="traceai",
    )


def instrument_google_genai() -> bool:
    """Instrument Google Generative AI (Gemini) for LLM tracing."""
    return _instrument_library(
        "google_genai",
        "traceai_google_genai",
        "GoogleGenAIInstrumentor",
        use_tracer_provider=True,
        via="traceai",
    )


def instrument_vertexai() -> bool:
    """Instrument Google Vertex AI for LLM tracing."""
    return _instrument_library(
        "vertexai",
        "traceai_vertexai",
        "VertexAIInstrumentor",
        use_tracer_provider=True,
        via="traceai",
    )


def instrument_groq() -> bool:
    """Instrument Groq for ultra-fast LLM inference tracing."""
    return _instrument_library(
        "groq",
        "traceai_groq",
        "GroqInstrumentor",
        use_tracer_provider=True,
        via="traceai",
    )


def instrument_mistralai() -> bool:
    """Instrument Mistral AI for LLM tracing."""
    return _instrument_library(
        "mistralai",
        "traceai_mistralai",
        "MistralAIInstrumentor",
        use_tracer_provider=True,
        via="traceai",
    )


def instrument_litellm() -> bool:
    """Instrument LiteLLM for unified LLM provider tracing (100+ providers)."""
    return _instrument_library(
        "litellm",
        "traceai_litellm",
        "LiteLLMInstrumentor",
        use_tracer_provider=True,
        via="traceai",
    )


# =============================================================================
# Agent Framework Instrumentation (via traceAI)
# =============================================================================


def instrument_crewai() -> bool:
    """Instrument CrewAI for multi-agent tracing."""
    return _instrument_library(
        "crewai",
        "traceai_crewai",
        "CrewAIInstrumentor",
        use_tracer_provider=True,
        via="traceai",
    )


def instrument_mcp() -> bool:
    """Instrument Model Context Protocol (MCP) for tool/resource tracing."""
    return _instrument_library(
        "mcp", "traceai_mcp", "MCPInstrumentor", use_tracer_provider=True, via="traceai"
    )


def instrument_all_llm_providers() -> None:
    """
    Instrument LLM providers via traceAI.

    Only instruments packages that are actually installed (per pyproject.toml):
    - LLM Providers: OpenAI, Anthropic, Bedrock, Google GenAI, Vertex AI, LiteLLM
    """
    logger.info("instrument_all_llm_providers_start")

    # Define all LLM providers to instrument
    providers = [
        ("openai", instrument_openai),
        ("anthropic", instrument_anthropic),
        ("bedrock", instrument_bedrock),
        ("google_genai", instrument_google_genai),
        ("vertexai", instrument_vertexai),
        ("litellm", instrument_litellm),
    ]

    results = {}
    for name, func in providers:
        logger.info(f"instrumenting_{name}")
        results[name] = func()

    logger.info("llm_instrumentation_complete", results=results)

    # Warn if critical LLM providers failed to instrument
    critical_providers = ["litellm", "openai", "anthropic"]
    failed_critical = [p for p in critical_providers if not results.get(p, False)]
    if failed_critical:
        logger.warning(
            "critical_llm_instrumentation_failed",
            failed_providers=failed_critical,
            hint="LLM calls may not be traced. Check if traceAI packages are installed.",
        )

    # Verify instrumentation state for debugging
    try:
        verification = verify_litellm_instrumentation()
        logger.info(
            "llm_instrumentation_verification",
            litellm_imported=verification.get("litellm_imported"),
            is_instrumented=verification.get("is_instrumented"),
            tracer_provider=verification.get("tracer_provider"),
            error=verification.get("error"),
        )
    except Exception as e:
        logger.warning("llm_instrumentation_verification_failed", error=str(e))


def verify_litellm_instrumentation() -> dict:
    """
    Verify that litellm is properly instrumented.

    Returns a dict with instrumentation status for debugging.

    Example:
        from tfc.telemetry.instrumentation import verify_litellm_instrumentation
        print(verify_litellm_instrumentation())
    """
    result = {
        "litellm_imported": False,
        "traceai_litellm_installed": False,
        "instrumentor_available": False,
        "is_instrumented": False,
        "tracer_provider": None,
        "error": None,
    }

    try:
        import litellm

        result["litellm_imported"] = True
        result["litellm_version"] = getattr(litellm, "__version__", "unknown")
    except ImportError as e:
        result["error"] = f"litellm not installed: {e}"
        return result

    try:
        from traceai_litellm import LiteLLMInstrumentor

        result["traceai_litellm_installed"] = True
        result["instrumentor_available"] = True

        inst = LiteLLMInstrumentor()
        result["is_instrumented"] = inst.is_instrumented_by_opentelemetry
    except ImportError as e:
        result["error"] = f"traceai-litellm not installed: {e}"
        return result
    except Exception as e:
        result["error"] = f"Error checking instrumentor: {e}"
        return result

    try:
        from opentelemetry import trace

        provider = trace.get_tracer_provider()
        result["tracer_provider"] = type(provider).__name__
    except Exception as e:
        result["tracer_provider_error"] = str(e)

    return result


# =============================================================================
# Cloud Services Instrumentation
# =============================================================================


def instrument_botocore() -> bool:
    """Instrument AWS SDK (botocore/boto3) for cloud service tracing."""
    return _instrument_library(
        "botocore",
        "opentelemetry.instrumentation.botocore",
        "BotocoreInstrumentor",
        use_tracer_provider=True,
    )


def instrument_aiohttp() -> bool:
    """Instrument aiohttp for async HTTP client tracing."""
    return _instrument_library(
        "aiohttp",
        "opentelemetry.instrumentation.aiohttp_client",
        "AioHttpClientInstrumentor",
        use_tracer_provider=True,
    )


# =============================================================================
# Vector Database Instrumentation (optional - packages not installed by default)
# =============================================================================


def instrument_chromadb() -> bool:
    """Instrument ChromaDB for vector database tracing."""
    return _instrument_library(
        "chromadb", "opentelemetry.instrumentation.chromadb", "ChromaInstrumentor"
    )


def instrument_qdrant() -> bool:
    """Instrument Qdrant for vector database tracing."""
    return _instrument_library(
        "qdrant", "opentelemetry.instrumentation.qdrant", "QdrantInstrumentor"
    )


def instrument_pinecone() -> bool:
    """Instrument Pinecone for vector database tracing."""
    return _instrument_library(
        "pinecone", "opentelemetry.instrumentation.pinecone", "PineconeInstrumentor"
    )


def instrument_weaviate() -> bool:
    """Instrument Weaviate for vector database tracing."""
    return _instrument_library(
        "weaviate", "opentelemetry.instrumentation.weaviate", "WeaviateInstrumentor"
    )


def instrument_milvus() -> bool:
    """Instrument Milvus for vector database tracing."""
    return _instrument_library(
        "milvus", "opentelemetry.instrumentation.milvus", "MilvusInstrumentor"
    )


def instrument_all_vector_dbs() -> None:
    """
    Instrument all vector database clients.

    Note: Vector DB instrumentation packages are not currently installed.
    This function is a no-op until those packages are added.

    To enable, add to pyproject.toml:
    - opentelemetry-instrumentation-chromadb
    - opentelemetry-instrumentation-qdrant
    - opentelemetry-instrumentation-pinecone
    - opentelemetry-instrumentation-weaviate
    - opentelemetry-instrumentation-milvus
    """
    # Vector DB instrumentation disabled - packages not installed
    pass


# =============================================================================
# ClickHouse Custom Instrumentation
# =============================================================================

# Global tracer for custom instrumentation
_clickhouse_tracer = None


def _get_clickhouse_tracer():
    """Get or create the ClickHouse tracer."""
    global _clickhouse_tracer
    if _clickhouse_tracer is None:
        from opentelemetry import trace

        _clickhouse_tracer = trace.get_tracer("tfc.clickhouse")
    return _clickhouse_tracer


def trace_clickhouse_query(func: Callable) -> Callable:
    """
    Decorator to trace ClickHouse queries.

    Usage:
        @trace_clickhouse_query
        def execute_query(query, params=None):
            return client.execute(query, params)

    Or wrap existing functions:
        client.execute = trace_clickhouse_query(client.execute)
    """

    @wraps(func)
    def wrapper(*args, **kwargs):
        name, kind, attributes = _clickhouse_span_args(args, kwargs)
        with _get_clickhouse_tracer().start_as_current_span(
            name, kind=kind, attributes=attributes
        ) as span:
            try:
                result = func(*args, **kwargs)
                _record_clickhouse_result(span, result, kwargs)
                return result

            except Exception as e:
                _record_clickhouse_error(span, e)
                raise

    return wrapper


def trace_clickhouse_stream(func: Callable) -> Callable:
    """Trace a streaming query (``execute_iter`` / ``query_row_block_stream``).

    The span ends when the returned iterator or stream context is exhausted,
    closed, or fails, so it carries the real duration and server errors.
    """

    @wraps(func)
    def wrapper(*args, **kwargs):
        name, kind, attributes = _clickhouse_span_args(args, kwargs)
        span = _get_clickhouse_tracer().start_span(
            name, kind=kind, attributes=attributes
        )
        try:
            stream = func(*args, **kwargs)
        except Exception as e:
            _record_clickhouse_error(span, e)
            span.end()
            raise

        def rows():
            try:
                yield from stream
            except Exception as e:
                _record_clickhouse_error(span, e)
                raise
            finally:
                span.end()

        @contextmanager
        def blocks():
            try:
                with stream as entered:
                    yield entered
            except Exception as e:
                _record_clickhouse_error(span, e)
                raise
            finally:
                span.end()

        return blocks() if hasattr(stream, "__enter__") else rows()

    return wrapper


def trace_clickhouse_query_async(func: Callable) -> Callable:
    """Async version of trace_clickhouse_query decorator."""

    @wraps(func)
    async def wrapper(*args, **kwargs):
        name, kind, attributes = _clickhouse_span_args(args, kwargs)
        with _get_clickhouse_tracer().start_as_current_span(
            name, kind=kind, attributes=attributes
        ) as span:
            try:
                result = await func(*args, **kwargs)
                _record_clickhouse_result(span, result, kwargs)
                return result

            except Exception as e:
                _record_clickhouse_error(span, e)
                raise

    return wrapper


def _clickhouse_span_args(args: tuple, kwargs: dict) -> tuple[str, Any, dict]:
    """Span name, kind and attributes for a query taken from the call arguments."""
    from opentelemetry.trace import SpanKind

    query = args[0] if args else kwargs.get("query", "unknown")
    if not isinstance(query, str):
        query = str(query)
    stripped = query.strip()
    operation = stripped.split(maxsplit=1)[0].upper() if stripped else "QUERY"
    return (
        f"clickhouse.{operation.lower()}",
        SpanKind.CLIENT,
        {
            "db.system": "clickhouse",
            "db.operation": operation,
            "db.statement": query[:1000] if len(query) > 1000 else query,
        },
    )


def _record_clickhouse_result(span: Any, result: Any, kwargs: dict) -> None:
    """Record row counts; ``with_column_types`` wraps rows in a (rows, types) tuple."""
    if kwargs.get("with_column_types") and isinstance(result, tuple):
        result = result[0]
    if isinstance(result, int):
        span.set_attribute("db.rows_affected", result)
    elif hasattr(result, "__len__") and not isinstance(result, str):
        span.set_attribute("db.rows_returned", len(result))
    elif isinstance(getattr(result, "row_count", None), int):
        # clickhouse-connect QueryResult exposes row_count, not __len__.
        span.set_attribute("db.rows_returned", result.row_count)


def _record_clickhouse_error(span: Any, error: Exception) -> None:
    from opentelemetry.trace import Status, StatusCode

    span.set_status(Status(StatusCode.ERROR, str(error)))
    span.set_attribute("error.type", type(error).__name__)
    span.set_attribute("error.message", str(error))


# Query entry points of clickhouse-driver (native) and clickhouse-connect (HTTP).
_CLICKHOUSE_QUERY_METHODS = ("execute", "query", "command")
# Streaming entry points whose result is consumed after the call returns.
_CLICKHOUSE_STREAM_METHODS = ("execute_iter", "query_row_block_stream")


def instrument_clickhouse_client(client: Any) -> Any:
    """
    Instrument a ClickHouse client instance.

    Usage:
        from clickhouse_driver import Client
        client = Client(host='localhost')
        client = instrument_clickhouse_client(client)

    Args:
        client: ClickHouse client instance

    Returns:
        Instrumented client (unchanged when OTEL is disabled)
    """
    from . import config

    if not config.OTEL_ENABLED or getattr(client, "_otel_instrumented", False) is True:
        return client

    for name in _CLICKHOUSE_QUERY_METHODS + _CLICKHOUSE_STREAM_METHODS:
        method = getattr(client, name, None)
        if not callable(method):
            continue
        tracer = (
            trace_clickhouse_stream
            if name in _CLICKHOUSE_STREAM_METHODS
            else trace_clickhouse_query
        )
        setattr(client, name, tracer(method))
    client._otel_instrumented = True
    logger.debug("instrumented", library="clickhouse")

    return client


# =============================================================================
# Django Channels / WebSocket Custom Instrumentation
# =============================================================================

_channels_tracer = None


def _get_channels_tracer():
    """Get or create the Channels tracer."""
    global _channels_tracer
    if _channels_tracer is None:
        from opentelemetry import trace

        _channels_tracer = trace.get_tracer("tfc.channels")
    return _channels_tracer


def trace_websocket_consumer(cls):
    """
    Class decorator to instrument Django Channels WebSocket consumers.

    Usage:
        from channels.generic.websocket import AsyncWebsocketConsumer
        from tfc.telemetry import trace_websocket_consumer

        @trace_websocket_consumer
        class ChatConsumer(AsyncWebsocketConsumer):
            async def connect(self):
                await self.accept()

            async def receive(self, text_data=None, bytes_data=None):
                # Handle message
                pass

    Creates spans for:
    - websocket.connect
    - websocket.receive
    - websocket.disconnect
    - websocket.send (if traced)
    """
    original_connect = getattr(cls, "connect", None)
    original_receive = getattr(cls, "receive", None)
    original_disconnect = getattr(cls, "disconnect", None)

    if original_connect:

        @wraps(original_connect)
        async def traced_connect(self, *args, **kwargs):
            tracer = _get_channels_tracer()
            consumer_name = self.__class__.__name__

            # Extract connection info
            scope = getattr(self, "scope", {})
            path = scope.get("path", "/ws/unknown")
            client = scope.get("client", ("unknown", 0))

            with tracer.start_as_current_span(
                f"websocket.connect {path}",
                attributes={
                    "websocket.consumer": consumer_name,
                    "websocket.path": path,
                    "websocket.type": "connect",
                    "net.peer.ip": client[0] if client else "unknown",
                    "net.peer.port": client[1] if client and len(client) > 1 else 0,
                },
            ) as span:
                try:
                    result = await original_connect(self, *args, **kwargs)
                    span.set_attribute("websocket.connected", True)
                    return result
                except Exception as e:
                    from opentelemetry.trace import Status, StatusCode

                    span.set_status(Status(StatusCode.ERROR, str(e)))
                    span.set_attribute("error.type", type(e).__name__)
                    span.set_attribute("websocket.connected", False)
                    raise

        cls.connect = traced_connect

    if original_receive:

        @wraps(original_receive)
        async def traced_receive(self, text_data=None, bytes_data=None, **kwargs):
            tracer = _get_channels_tracer()
            consumer_name = self.__class__.__name__

            scope = getattr(self, "scope", {})
            path = scope.get("path", "/ws/unknown")

            # Determine message type and size
            msg_type = "text" if text_data is not None else "binary"
            msg_size = (
                len(text_data) if text_data else (len(bytes_data) if bytes_data else 0)
            )

            with tracer.start_as_current_span(
                f"websocket.receive {path}",
                attributes={
                    "websocket.consumer": consumer_name,
                    "websocket.path": path,
                    "websocket.type": "receive",
                    "websocket.message_type": msg_type,
                    "websocket.message_size": msg_size,
                },
            ) as span:
                try:
                    return await original_receive(
                        self, text_data=text_data, bytes_data=bytes_data, **kwargs
                    )
                except Exception as e:
                    from opentelemetry.trace import Status, StatusCode

                    span.set_status(Status(StatusCode.ERROR, str(e)))
                    span.set_attribute("error.type", type(e).__name__)
                    raise

        cls.receive = traced_receive

    if original_disconnect:

        @wraps(original_disconnect)
        async def traced_disconnect(self, close_code=None, *args, **kwargs):
            tracer = _get_channels_tracer()
            consumer_name = self.__class__.__name__

            scope = getattr(self, "scope", {})
            path = scope.get("path", "/ws/unknown")

            with tracer.start_as_current_span(
                f"websocket.disconnect {path}",
                attributes={
                    "websocket.consumer": consumer_name,
                    "websocket.path": path,
                    "websocket.type": "disconnect",
                    "websocket.close_code": close_code or 1000,
                },
            ) as span:
                try:
                    return await original_disconnect(self, close_code, *args, **kwargs)
                except Exception as e:
                    from opentelemetry.trace import Status, StatusCode

                    span.set_status(Status(StatusCode.ERROR, str(e)))
                    span.set_attribute("error.type", type(e).__name__)
                    raise

        cls.disconnect = traced_disconnect

    logger.info("instrumented", library="channels", consumer=cls.__name__)
    return cls


def trace_channel_layer_send(func: Callable) -> Callable:
    """
    Decorator to trace channel layer send operations.

    Usage:
        channel_layer.send = trace_channel_layer_send(channel_layer.send)
        channel_layer.group_send = trace_channel_layer_send(channel_layer.group_send)
    """

    @wraps(func)
    async def wrapper(channel_or_group, message, *args, **kwargs):
        tracer = _get_channels_tracer()
        msg_type = (
            message.get("type", "unknown") if isinstance(message, dict) else "unknown"
        )

        with tracer.start_as_current_span(
            f"channel.send {msg_type}",
            attributes={
                "messaging.system": "channels",
                "messaging.destination": str(channel_or_group)[:100],
                "messaging.message_type": msg_type,
            },
        ) as span:
            try:
                return await func(channel_or_group, message, *args, **kwargs)
            except Exception as e:
                from opentelemetry.trace import Status, StatusCode

                span.set_status(Status(StatusCode.ERROR, str(e)))
                raise

    return wrapper


# =============================================================================
# MinIO Custom Instrumentation
# =============================================================================

_minio_tracer = None


def _get_minio_tracer():
    """Get or create the MinIO tracer."""
    global _minio_tracer
    if _minio_tracer is None:
        from opentelemetry import trace

        _minio_tracer = trace.get_tracer("tfc.minio")
    return _minio_tracer


def trace_minio_operation(func: Callable) -> Callable:
    """
    Decorator to trace MinIO operations.

    Usage:
        @trace_minio_operation
        def upload_file(bucket, object_name, data):
            return minio_client.put_object(bucket, object_name, data, len(data))
    """

    @wraps(func)
    def wrapper(*args, **kwargs):
        tracer = _get_minio_tracer()
        operation = func.__name__

        # Try to extract bucket and object info from common patterns
        bucket = None
        object_name = None

        # Check kwargs first
        bucket = kwargs.get("bucket_name") or kwargs.get("bucket")
        object_name = kwargs.get("object_name") or kwargs.get("object")

        # Check positional args (common pattern: bucket, object_name, ...)
        if not bucket and len(args) >= 1:
            bucket = args[0] if isinstance(args[0], str) else None
        if not object_name and len(args) >= 2:
            object_name = args[1] if isinstance(args[1], str) else None

        span_name = f"minio.{operation}"
        if bucket:
            span_name = f"minio.{operation} {bucket}"

        attributes = {
            "db.system": "minio",
            "db.operation": operation,
        }
        if bucket:
            attributes["minio.bucket"] = bucket
        if object_name:
            attributes["minio.object"] = (
                object_name[:200] if len(object_name) > 200 else object_name
            )

        with tracer.start_as_current_span(span_name, attributes=attributes) as span:
            try:
                result = func(*args, **kwargs)

                # Capture result metadata for specific operations
                if hasattr(result, "size"):
                    span.set_attribute("minio.object_size", result.size)
                if hasattr(result, "etag"):
                    span.set_attribute("minio.etag", result.etag)

                return result

            except Exception as e:
                from opentelemetry.trace import Status, StatusCode

                span.set_status(Status(StatusCode.ERROR, str(e)))
                span.set_attribute("error.type", type(e).__name__)
                span.set_attribute("error.message", str(e))
                raise

    return wrapper


def instrument_minio_client(client: Any) -> Any:
    """
    Instrument a MinIO client instance.

    Usage:
        from minio import Minio
        client = Minio("localhost:9000", access_key="...", secret_key="...")
        client = instrument_minio_client(client)

    Args:
        client: MinIO client instance

    Returns:
        Instrumented client
    """
    # Common MinIO operations to instrument
    operations = [
        # Bucket operations
        "make_bucket",
        "remove_bucket",
        "list_buckets",
        "bucket_exists",
        # Object operations
        "put_object",
        "get_object",
        "remove_object",
        "stat_object",
        "copy_object",
        "list_objects",
        "fput_object",
        "fget_object",
        # Presigned URLs
        "presigned_get_object",
        "presigned_put_object",
    ]

    for op in operations:
        if hasattr(client, op):
            original = getattr(client, op)
            setattr(client, op, trace_minio_operation(original))

    client._otel_instrumented = True
    logger.info("instrumented", library="minio")
    return client


# =============================================================================
# Messaging: RabbitMQ/Pika & asyncio
# =============================================================================


def instrument_pika() -> bool:
    """Instrument Pika (RabbitMQ client) for message tracing."""
    return _instrument_library(
        "pika",
        "opentelemetry.instrumentation.pika",
        "PikaInstrumentor",
        use_tracer_provider=True,
    )


def instrument_asyncio() -> bool:
    """Instrument asyncio for async task tracing (create_task, gather, etc.)."""
    return _instrument_library(
        "asyncio",
        "opentelemetry.instrumentation.asyncio",
        "AsyncioInstrumentor",
        use_tracer_provider=True,
    )


def instrument_threading() -> bool:
    """Instrument threading for automatic OTel context propagation across threads."""
    return _instrument_library(
        "threading",
        "opentelemetry.instrumentation.threading",
        "ThreadingInstrumentor",
        use_tracer_provider=True,
    )


# =============================================================================
# Web Frameworks & HTTP
# =============================================================================


def instrument_django(
    *,
    is_sql_commentor_enabled: bool = True,
    request_hook: Optional[callable] = None,
    response_hook: Optional[callable] = None,
    use_default_hooks: bool = True,
) -> bool:
    """
    Instrument Django for automatic tracing.

    This automatically traces:
    - HTTP requests (creates spans for each request)
    - Database queries (optional SQL comments with trace context)
    - Template rendering

    Span names are formatted as:
    - "POST /api/v1/experiments/ → ExperimentViewSet.create"
    - "GET /api/v1/users/me/ → UserViewSet.me"

    Best Practices:
    - Use response_hook (not request_hook) to access middleware attributes
      like request.user (from AuthenticationMiddleware)
    - Avoid adding sensitive data (passwords, tokens, PII) to spans
    - Hooks should execute quickly - avoid blocking operations

    Args:
        is_sql_commentor_enabled: Add trace context as SQL comments (for DB query correlation)
        request_hook: Callback for incoming requests (before middleware)
        response_hook: Callback for responses (after middleware, can access request.user)
        use_default_hooks: Use default hooks for better span naming (default: True)

    Returns:
        True if instrumentation succeeded
    """
    try:
        from opentelemetry import trace
        from opentelemetry.instrumentation.django import DjangoInstrumentor

        # Check if already instrumented
        if DjangoInstrumentor().is_instrumented_by_opentelemetry:
            logger.debug("django_already_instrumented")
            return True

        tracer_provider = trace.get_tracer_provider()
        logger.debug(
            "django_tracer_provider", provider_type=type(tracer_provider).__name__
        )

        # Use default hooks for better span naming unless custom hooks provided
        if use_default_hooks:
            from .naming import django_request_hook, django_response_hook

            if request_hook is None:
                request_hook = django_request_hook
            if response_hook is None:
                response_hook = django_response_hook

        DjangoInstrumentor().instrument(
            tracer_provider=tracer_provider,
            is_sql_commentor_enabled=is_sql_commentor_enabled,
            request_hook=request_hook,
            response_hook=response_hook,
        )

        logger.info("instrumented", library="django", better_naming=use_default_hooks)
        return True

    except ImportError as e:
        logger.warning("instrumentation_unavailable", library="django", error=str(e))
        return False
    except Exception as e:
        logger.error("instrumentation_failed", library="django", error=str(e))
        return False


def instrument_celery(
    *,
    propagate: bool = True,
    carrier_format: str = "json",
) -> bool:
    """
    Instrument Celery for automatic task tracing.

    This automatically traces:
    - Task execution (task.apply, task.run)
    - Task retries
    - Task chains and groups

    IMPORTANT: Tracing and instrumentation must be initialized AFTER
    the Celery worker process is initialized. Use the worker_process_init
    signal or call this from within the worker.

    Args:
        propagate: Propagate trace context to task workers
        carrier_format: Trace context carrier format

    Returns:
        True if instrumentation succeeded
    """
    try:
        from opentelemetry import trace
        from opentelemetry.instrumentation.celery import CeleryInstrumentor

        # Check if already instrumented
        if CeleryInstrumentor().is_instrumented_by_opentelemetry:
            logger.debug("Celery already instrumented, skipping")
            return True

        tracer_provider = trace.get_tracer_provider()
        CeleryInstrumentor().instrument(tracer_provider=tracer_provider)

        logger.info("instrumented", library="celery")
        return True

    except ImportError as e:
        logger.warning("instrumentation_unavailable", library="celery", error=str(e))
        return False
    except Exception as e:
        logger.error("instrumentation_failed", library="celery", error=str(e))
        return False


def _requests_request_hook(span, request) -> None:
    """Hook to improve requests span naming on request start."""
    try:
        if not span.is_recording():
            return

        method = request.method or "HTTP"
        url = str(request.url) if request.url else ""

        from .naming import get_http_client_span_name

        span_name = get_http_client_span_name(method, url)
        span.update_name(span_name)

    except Exception:
        pass


def _requests_response_hook(span, request, response) -> None:
    """Hook to capture requests response details."""
    try:
        if not span.is_recording():
            return

        # Add response status
        span.set_attribute("http.status_code", response.status_code)

    except Exception:
        pass


def instrument_requests() -> bool:
    """
    Instrument the requests library for outbound HTTP tracing.

    This automatically traces all HTTP requests made with the requests library,
    adding trace context headers for distributed tracing.

    Span names are formatted as:
    - "POST api.openai.com/v1/chat/completions"
    - "GET api.anthropic.com/v1/messages"

    Returns:
        True if instrumentation succeeded
    """
    try:
        from opentelemetry import trace
        from opentelemetry.instrumentation.requests import RequestsInstrumentor

        # Check if already instrumented
        if RequestsInstrumentor().is_instrumented_by_opentelemetry:
            logger.debug("requests already instrumented, skipping")
            return True

        tracer_provider = trace.get_tracer_provider()
        RequestsInstrumentor().instrument(
            tracer_provider=tracer_provider,
            request_hook=_requests_request_hook,
            response_hook=_requests_response_hook,
        )

        logger.info("instrumented", library="requests", better_naming=True)
        return True

    except ImportError as e:
        logger.warning("instrumentation_unavailable", library="requests", error=str(e))
        return False
    except Exception as e:
        logger.error("instrumentation_failed", library="requests", error=str(e))
        return False


def _httpx_request_hook(span, request) -> None:
    """Hook to improve httpx span naming on request start."""
    try:
        if not span.is_recording():
            return

        method = str(request.method) if request.method else "HTTP"
        url = str(request.url) if request.url else ""

        from .naming import get_http_client_span_name

        span_name = get_http_client_span_name(method, url)
        span.update_name(span_name)

    except Exception:
        pass


def _httpx_response_hook(span, request, response) -> None:
    """Hook to capture httpx response details."""
    try:
        if not span.is_recording():
            return

        span.set_attribute("http.status_code", response.status_code)

    except Exception:
        pass


def instrument_httpx() -> bool:
    """
    Instrument httpx for outbound HTTP tracing.

    httpx is used by many modern Python libraries (OpenAI, Anthropic, etc.)
    for making HTTP requests. This instruments both sync and async clients.

    Span names are formatted as:
    - "POST api.openai.com/v1/chat/completions"
    - "GET api.anthropic.com/v1/messages"

    Returns:
        True if instrumentation succeeded
    """
    try:
        from opentelemetry import trace
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

        # Check if already instrumented
        if HTTPXClientInstrumentor().is_instrumented_by_opentelemetry:
            logger.debug("httpx already instrumented, skipping")
            return True

        tracer_provider = trace.get_tracer_provider()
        HTTPXClientInstrumentor().instrument(
            tracer_provider=tracer_provider,
            request_hook=_httpx_request_hook,
            response_hook=_httpx_response_hook,
        )

        logger.info("instrumented", library="httpx")
        return True

    except ImportError as e:
        logger.warning("instrumentation_unavailable", library="httpx", error=str(e))
        return False
    except Exception as e:
        logger.error("instrumentation_failed", library="httpx", error=str(e))
        return False


def instrument_grpc() -> bool:
    """
    Instrument gRPC for automatic tracing.

    This instruments both gRPC client and server.

    Returns:
        True if instrumentation succeeded
    """
    try:
        from opentelemetry.instrumentation.grpc import (
            GrpcAioInstrumentorClient,
            GrpcAioInstrumentorServer,
        )

        GrpcAioInstrumentorServer().instrument()
        GrpcAioInstrumentorClient().instrument()

        logger.info("instrumented", library="grpc")
        return True

    except ImportError as e:
        logger.warning("instrumentation_unavailable", library="grpc", error=str(e))
        return False
    except Exception as e:
        logger.error("instrumentation_failed", library="grpc", error=str(e))
        return False


def _psycopg_request_hook(span, args) -> None:
    """
    Capture database query and update span name before execution.

    For psycopg3 (opentelemetry-instrumentation-psycopg):
    - args is a tuple of (query, params)

    This hook updates the span name to include the table name.
    """
    try:
        if not span.is_recording():
            return

        if not args:
            return

        # Extract query from args tuple
        query = args[0] if args else None

        if query:
            # Handle different query types
            if isinstance(query, bytes):
                query = query.decode("utf-8", errors="replace")
            elif hasattr(query, "as_string"):
                # Handle psycopg.sql.Composable objects
                try:
                    query = query.as_string(None)
                except Exception:
                    query = str(query)
            elif not isinstance(query, str):
                query = str(query)

            # Update span name to include table name
            from .naming import get_db_span_name

            span_name = get_db_span_name(query)
            span.update_name(span_name)

    except Exception:
        pass  # Don't fail the query if hook fails


def _psycopg_response_hook(span, cursor) -> None:
    """
    Capture query results metadata after execution.

    For psycopg3 (opentelemetry-instrumentation-psycopg):
    - cursor is the cursor object after execution
    """
    try:
        if not span.is_recording():
            return

        # Capture row count
        if (
            hasattr(cursor, "rowcount")
            and cursor.rowcount is not None
            and cursor.rowcount >= 0
        ):
            span.set_attribute("db.rows_affected", cursor.rowcount)

        # Capture status message if available
        if hasattr(cursor, "statusmessage") and cursor.statusmessage:
            span.set_attribute("db.status_message", cursor.statusmessage)

    except Exception:
        pass


def instrument_psycopg(
    *,
    enable_commenter: bool = True,
    commenter_options: Optional[dict] = None,
    capture_parameters: bool = True,
) -> bool:
    """
    Instrument psycopg (PostgreSQL driver) for database tracing.

    Creates spans for every database query with:
    - db.statement: SQL query (sanitized)
    - db.system: postgresql
    - db.name: database name
    - db.user: database user
    - db.operation: SELECT, INSERT, UPDATE, DELETE, etc.
    - db.rows_affected: Number of rows affected (via response hook)

    Args:
        enable_commenter: Add trace context as SQL comments (for DB query correlation)
        commenter_options: Options for SQL commenter
        capture_parameters: Add hooks to capture query metadata

    Returns:
        True if instrumentation succeeded
    """
    try:
        from opentelemetry import trace
        from opentelemetry.instrumentation.psycopg import PsycopgInstrumentor

        if PsycopgInstrumentor().is_instrumented_by_opentelemetry:
            logger.debug("psycopg already instrumented, skipping")
            return True

        # Get the global tracer provider to ensure spans are linked correctly
        tracer_provider = trace.get_tracer_provider()

        # Build instrumentation kwargs
        instrument_kwargs = {
            "tracer_provider": tracer_provider,
            "enable_commenter": enable_commenter,
            "commenter_options": commenter_options or {},
            "skip_dep_check": True,  # Skip dependency check for flexibility
        }

        # Add hooks for richer context
        if capture_parameters:
            instrument_kwargs["request_hook"] = _psycopg_request_hook
            instrument_kwargs["response_hook"] = _psycopg_response_hook

        PsycopgInstrumentor().instrument(**instrument_kwargs)

        logger.info(
            "instrumented",
            library="psycopg",
            capture_parameters=capture_parameters,
        )
        return True

    except ImportError as e:
        logger.warning("instrumentation_unavailable", library="psycopg", error=str(e))
        return False
    except Exception as e:
        logger.error("instrumentation_failed", library="psycopg", error=str(e))
        return False


def _redis_request_hook(span, instance, args, kwargs) -> None:
    """
    Improve Redis span naming to include command and key.

    Updates span name from generic "GET" to "GET user:session:123".

    In Redis execute_command, args structure is:
    - args[0] = command name (GET, SET, HGET, etc.)
    - args[1] = key (for most commands)
    - args[2+] = additional arguments (value for SET, field for HGET, etc.)
    """
    try:
        if not span.is_recording():
            return

        if not args or len(args) < 1:
            return

        # Extract command name from first arg
        command = args[0]
        if isinstance(command, bytes):
            command = command.decode("utf-8", errors="replace")
        if not isinstance(command, str):
            command = str(command)

        # Extract key from second arg (if present)
        key = None
        if len(args) >= 2:
            key = args[1]
            if isinstance(key, bytes):
                key = key.decode("utf-8", errors="replace")
            if not isinstance(key, str):
                key = str(key) if key is not None else None

        if key:
            # Truncate long keys for readability
            max_key_len = 50
            if len(key) > max_key_len:
                key = key[:max_key_len] + "..."

            # Update span name to include command and key
            span.update_name(f"{command} {key}")

            # Also set as attribute for filtering
            span.set_attribute("db.redis.key", key)

            # For hash commands, capture the field too
            command_upper = command.upper()
            if len(args) >= 3 and command_upper in (
                "HGET",
                "HSET",
                "HDEL",
                "HEXISTS",
                "HINCRBY",
                "HINCRBYFLOAT",
                "HSETNX",
            ):
                field = args[2]
                if isinstance(field, bytes):
                    field = field.decode("utf-8", errors="replace")
                if isinstance(field, str):
                    span.set_attribute("db.redis.field", field)
                    # Update name to include field
                    span.update_name(f"{command} {key} {field}")
        else:
            # Commands without keys (like PING, INFO, etc.)
            span.update_name(command)

    except Exception:
        pass  # Don't fail the Redis operation if hook fails


def _redis_response_hook(span, instance, response) -> None:
    """
    Capture Redis response metadata.
    """
    try:
        if not span.is_recording():
            return

        # Capture response type for debugging
        if response is not None:
            if isinstance(response, (list, tuple)):
                span.set_attribute("db.redis.result_count", len(response))
            elif isinstance(response, bool):
                span.set_attribute("db.redis.result", str(response))
            elif isinstance(response, int):
                span.set_attribute("db.redis.result", response)

    except Exception:
        pass


def instrument_redis() -> bool:
    """
    Instrument Redis for operation tracing.

    Creates spans for every Redis command with:
    - db.system: redis
    - db.statement: Redis command
    - net.peer.name: Redis host
    - net.peer.port: Redis port
    - db.redis.key: The Redis key being accessed (via hook)

    Span names are formatted as:
    - "GET user:session:123" (instead of just "GET")
    - "SET cache:api:results" (instead of just "SET")
    - "HGET user:preferences theme" (instead of just "HGET")

    Returns:
        True if instrumentation succeeded
    """
    try:
        from opentelemetry import trace
        from opentelemetry.instrumentation.redis import RedisInstrumentor

        if RedisInstrumentor().is_instrumented_by_opentelemetry:
            logger.debug("redis already instrumented, skipping")
            return True

        tracer_provider = trace.get_tracer_provider()
        RedisInstrumentor().instrument(
            tracer_provider=tracer_provider,
            request_hook=_redis_request_hook,
            response_hook=_redis_response_hook,
        )

        logger.info("instrumented", library="redis", better_naming=True)
        return True

    except ImportError as e:
        logger.warning("instrumentation_unavailable", library="redis", error=str(e))
        return False
    except Exception as e:
        logger.error("instrumentation_failed", library="redis", error=str(e))
        return False


def instrument_urllib3() -> bool:
    """
    Instrument urllib3 for HTTP client tracing.

    urllib3 is the underlying library used by requests and many other
    HTTP clients. Instrumenting it captures lower-level HTTP details.

    Returns:
        True if instrumentation succeeded
    """
    try:
        from opentelemetry import trace
        from opentelemetry.instrumentation.urllib3 import URLLib3Instrumentor

        if URLLib3Instrumentor().is_instrumented_by_opentelemetry:
            logger.debug("urllib3 already instrumented, skipping")
            return True

        tracer_provider = trace.get_tracer_provider()
        URLLib3Instrumentor().instrument(tracer_provider=tracer_provider)

        logger.info("instrumented", library="urllib3")
        return True

    except ImportError as e:
        logger.warning("instrumentation_unavailable", library="urllib3", error=str(e))
        return False
    except Exception as e:
        logger.error("instrumentation_failed", library="urllib3", error=str(e))
        return False


def instrument_logging() -> bool:
    """
    Instrument Python logging to inject trace context into LogRecords.

    The LoggingInstrumentor adds otelTraceID, otelSpanID, and otelServiceName
    attributes to every LogRecord. These are then read by structlog's
    add_otel_context_from_record processor for log-trace correlation.

    Note: We set set_logging_format=False because:
    1. We use structlog's ProcessorFormatter for all log formatting
    2. The add_otel_context processor reads trace context directly from spans
    3. For stdlib logs, add_otel_context_from_record reads the injected attributes

    Returns:
        True if instrumentation succeeded
    """
    try:
        from opentelemetry import trace
        from opentelemetry.instrumentation.logging import LoggingInstrumentor

        if LoggingInstrumentor().is_instrumented_by_opentelemetry:
            logger.debug("logging already instrumented, skipping")
            return True

        tracer_provider = trace.get_tracer_provider()

        # Don't modify logging format - structlog handles formatting
        # The instrumentor will still inject otelTraceID/otelSpanID into LogRecords
        LoggingInstrumentor().instrument(
            tracer_provider=tracer_provider,
            set_logging_format=False,  # structlog handles formatting
        )

        logger.info(
            "instrumented", library="logging", note="structlog handles formatting"
        )
        return True

    except ImportError as e:
        logger.warning("instrumentation_unavailable", library="logging", error=str(e))
        return False
    except Exception as e:
        logger.error("instrumentation_failed", library="logging", error=str(e))
        return False


def instrument_jinja2() -> bool:
    """
    Instrument Jinja2 template rendering.

    Creates spans for template rendering operations.

    Returns:
        True if instrumentation succeeded
    """
    try:
        from opentelemetry import trace
        from opentelemetry.instrumentation.jinja2 import Jinja2Instrumentor

        if Jinja2Instrumentor().is_instrumented_by_opentelemetry:
            logger.debug("jinja2 already instrumented, skipping")
            return True

        tracer_provider = trace.get_tracer_provider()
        Jinja2Instrumentor().instrument(tracer_provider=tracer_provider)

        logger.info("instrumented", library="jinja2")
        return True

    except ImportError as e:
        logger.warning("instrumentation_unavailable", library="jinja2", error=str(e))
        return False
    except Exception as e:
        logger.error("instrumentation_failed", library="jinja2", error=str(e))
        return False


def instrument_all_http_clients() -> None:
    """
    Instrument all HTTP client libraries.

    Convenience function to instrument requests, httpx, and urllib3.
    """
    instrument_requests()
    instrument_httpx()
    instrument_urllib3()


def instrument_all_databases() -> None:
    """
    Instrument all database clients.

    Convenience function to instrument PostgreSQL and Redis.
    """
    instrument_psycopg()
    instrument_redis()


def instrument_for_django() -> None:
    """
    Apply all relevant instrumentation for Django application.

    This enables comprehensive tracing for:
    - HTTP requests/responses (Django views, middleware)
    - Database queries (PostgreSQL, ClickHouse)
    - Redis operations
    - Outgoing HTTP calls (requests, httpx, urllib3, aiohttp)
    - gRPC calls
    - Template rendering (Jinja2)
    - LLM providers (OpenAI, Anthropic, Bedrock, Cohere, Google AI, Vertex AI, HuggingFace)
    - LLM frameworks (LangChain, LlamaIndex)
    - Cloud services (AWS via botocore)
    - Vector databases (ChromaDB, Qdrant, Pinecone, Weaviate, Milvus)
    - Messaging (RabbitMQ via Pika)
    - Async context propagation (asyncio)
    - Logging correlation (trace context in logs)
    - OTLP log export (if OTEL_LOGS_ENABLED=true)

    Note: Django Channels/WebSocket consumers require manual decoration with
    @trace_websocket_consumer. MinIO clients need manual instrumentation via
    instrument_minio_client(client).

    Call this after init_telemetry() in Django startup (manage.py).
    """
    instrument_django()
    instrument_all_databases()
    instrument_all_http_clients()
    instrument_aiohttp()
    instrument_grpc()
    instrument_jinja2()
    instrument_logging()

    # LLM providers and frameworks
    instrument_all_llm_providers()

    # Cloud services
    instrument_botocore()

    # Vector databases
    instrument_all_vector_dbs()

    # Messaging (RabbitMQ)
    instrument_pika()

    # Async context propagation
    instrument_asyncio()

    # Initialize OTLP log export if enabled
    try:
        from .logs import OTEL_LOGS_ENABLED, init_otel_logging

        if OTEL_LOGS_ENABLED:
            init_otel_logging()
    except ImportError:
        pass


def instrument_for_celery() -> None:
    """
    Apply all relevant instrumentation for Celery workers.

    This enables comprehensive tracing for:
    - Task execution (start, success, failure, retry)
    - Database queries (PostgreSQL, ClickHouse)
    - Redis operations
    - Outgoing HTTP calls (requests, httpx, urllib3, aiohttp)
    - LLM providers (OpenAI, Anthropic, Bedrock, Cohere, Google AI, Vertex AI, HuggingFace)
    - LLM frameworks (LangChain, LlamaIndex)
    - Cloud services (AWS via botocore)
    - Vector databases (ChromaDB, Qdrant, Pinecone, Weaviate, Milvus)
    - Messaging (RabbitMQ via Pika)
    - Async context propagation (asyncio)
    - Logging correlation (trace context in logs)
    - OTLP log export (if OTEL_LOGS_ENABLED=true)

    Note: MinIO clients need manual instrumentation via instrument_minio_client(client).

    Call this AFTER the Celery worker process is initialized,
    typically from the worker_process_init signal.
    """
    instrument_celery()
    instrument_all_databases()
    instrument_all_http_clients()
    instrument_aiohttp()
    instrument_logging()

    # LLM providers and frameworks
    instrument_all_llm_providers()

    # Cloud services
    instrument_botocore()

    # Vector databases
    instrument_all_vector_dbs()

    # Messaging (RabbitMQ)
    instrument_pika()

    # Async context propagation
    instrument_asyncio()

    # Initialize OTLP log export if enabled
    try:
        from .logs import OTEL_LOGS_ENABLED, init_otel_logging

        if OTEL_LOGS_ENABLED:
            init_otel_logging()
    except ImportError:
        pass


def instrument_for_temporal() -> None:
    """
    Apply all relevant instrumentation for Temporal workers.

    This enables comprehensive tracing for:
    - Database queries (PostgreSQL, ClickHouse)
    - Redis operations
    - Outgoing HTTP calls (requests, httpx, urllib3, aiohttp)
    - LLM providers (OpenAI, Anthropic, Bedrock, Cohere, Google AI, Vertex AI, HuggingFace)
    - LLM frameworks (LangChain, LlamaIndex)
    - Cloud services (AWS via botocore)
    - Vector databases (ChromaDB, Qdrant, Pinecone, Weaviate, Milvus)
    - Messaging (RabbitMQ via Pika)
    - Thread context propagation (ThreadPoolExecutor - critical for sync_to_async)
    - Async context propagation (asyncio)
    - Logging correlation (trace context in logs)
    - OTLP log export (if OTEL_LOGS_ENABLED=true)

    Note: Temporal workflow/activity tracing is handled via TracingInterceptor.
    MinIO clients need manual instrumentation via instrument_minio_client(client).

    Call this in temporal worker startup.
    """
    instrument_all_databases()
    instrument_all_http_clients()
    instrument_aiohttp()
    instrument_logging()

    # LLM providers and frameworks
    instrument_all_llm_providers()

    # Cloud services
    instrument_botocore()

    # Vector databases
    instrument_all_vector_dbs()

    # Messaging (RabbitMQ)
    instrument_pika()

    # NOTE: Do NOT use instrument_asyncio() here - it wraps coroutines with tracing
    # that interacts badly with litellm's async logging worker when asyncio.run() is
    # called inside ThreadPoolExecutor threads (e.g., SyntheticDataAgent).
    # The combination causes "task_done() called too many times" and
    # "cannot reuse already awaited coroutine" errors that corrupt the main event
    # loop, turning all worker pods into zombies (Rust SDK polls but Python is frozen).
    # See: TH-2979 investigation of create_graph_scenario stuck in PENDING.
    # instrument_asyncio()

    # NOTE: Do NOT use instrument_threading() here - it causes "Failed to detach context"
    # errors when combined with Django's sync_to_async. The threading instrumentation
    # copies context including OTel span tokens, which causes ValueError when spans
    # try to detach in the copied context.

    # Initialize OTLP log export if enabled
    try:
        from .logs import OTEL_LOGS_ENABLED, init_otel_logging

        if OTEL_LOGS_ENABLED:
            init_otel_logging()
    except ImportError:
        pass


def uninstrument_all() -> None:
    """
    Remove all instrumentation.

    Useful for testing or hot-reloading scenarios.
    """
    try:
        from opentelemetry.instrumentation.django import DjangoInstrumentor

        DjangoInstrumentor().uninstrument()
    except Exception:
        pass

    try:
        from opentelemetry.instrumentation.celery import CeleryInstrumentor

        CeleryInstrumentor().uninstrument()
    except Exception:
        pass

    try:
        from opentelemetry.instrumentation.requests import RequestsInstrumentor

        RequestsInstrumentor().uninstrument()
    except Exception:
        pass

    try:
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

        HTTPXClientInstrumentor().uninstrument()
    except Exception:
        pass
