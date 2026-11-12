"""OpenTelemetry configuration and initialization.

Note: OTel imports are lazy to avoid slow startup when OTEL_ENABLED=false.
"""

import os
from typing import TYPE_CHECKING, Optional

import structlog

logger = structlog.get_logger(__name__)

# Type hints only - no runtime import cost
if TYPE_CHECKING:
    from opentelemetry.sdk.trace import TracerProvider


def _str_to_bool(val: str | None) -> bool:
    """Convert string to boolean."""
    if val is None:
        return True
    return val.lower() not in ("false", "0", "no", "off", "disabled")


# Global flag to check if OTel is enabled
OTEL_ENABLED = _str_to_bool(os.getenv("OTEL_ENABLED", "true"))

# Singleton for initialized state
_initialized = False
_tracer_provider: Optional["TracerProvider"] = None


def get_service_name(component: str | None = None) -> str:
    """
    Get the service name for telemetry.

    Args:
        component: Optional component suffix (e.g., "django", "celery", "temporal")

    Returns:
        Service name string (e.g., "futureagi-backend-django")
    """
    base_name = os.getenv("OTEL_SERVICE_NAME", "futureagi-backend")
    if component:
        return f"{base_name}-{component}"
    return base_name


def get_service_version() -> str:
    """Get the service version from environment or default."""
    return os.getenv("SERVICE_VERSION", os.getenv("GIT_SHA", "dev"))


def get_otlp_endpoint() -> str:
    """
    Get the OTLP exporter endpoint.

    Supports both gRPC (default) and HTTP protocols.
    The infra team has commented out variables in docker-compose:
        OTEL_EXPORTER_OTLP_ENDPOINT: http://tempo-distributed-...:4318
        OTEL_EXPORTER_OTLP_PROTOCOL: http/protobuf

    Returns:
        OTLP endpoint URL
    """
    return os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")


def get_otlp_protocol() -> str:
    """
    Get the OTLP protocol.

    Returns:
        "grpc" or "http/protobuf"
    """
    return os.getenv("OTEL_EXPORTER_OTLP_PROTOCOL", "grpc")


def _get_sampler():
    """
    Get the trace sampler based on environment configuration.

    Environment variables:
        OTEL_TRACES_SAMPLER: Sampler type
            - "always_on": Sample all traces
            - "always_off": Sample no traces
            - "traceidratio": Sample based on trace ID ratio
            - "parentbased_traceidratio": Parent-based with ratio (default)
        OTEL_TRACES_SAMPLER_ARG: Sampling ratio (0.0-1.0, default: 1.0)
    """
    from opentelemetry.sdk.trace.sampling import (
        ALWAYS_OFF,
        ALWAYS_ON,
        ParentBasedTraceIdRatio,
        TraceIdRatioBased,
    )

    sampler_type = os.getenv("OTEL_TRACES_SAMPLER", "parentbased_traceidratio").lower()
    sampler_arg = float(os.getenv("OTEL_TRACES_SAMPLER_ARG", "1.0"))

    if sampler_type == "always_on":
        return ALWAYS_ON
    elif sampler_type == "always_off":
        return ALWAYS_OFF
    elif sampler_type == "traceidratio":
        return TraceIdRatioBased(sampler_arg)
    else:  # parentbased_traceidratio (default)
        return ParentBasedTraceIdRatio(sampler_arg)


def _create_exporter():
    """
    Create the OTLP exporter based on protocol.

    Supports both gRPC and HTTP protocols.
    """
    from .checkout_output import span_exporter

    protocol = get_otlp_protocol()
    endpoint = get_otlp_endpoint()
    if protocol == "http/protobuf":
        if not endpoint.endswith("/v1/traces"):
            endpoint = f"{endpoint.rstrip('/')}/v1/traces"
        return span_exporter(protocol, endpoint=endpoint)
    return span_exporter(protocol, endpoint=endpoint, insecure=True)


def build_span_processor(exporter, use_batch_processor: bool = True):
    """The exporting processor, behind the checkout redaction boundary.

    Every span is redacted before the batch queue or the exporter sees it.
    """
    from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor

    from .redaction import CheckoutRedactingSpanProcessor

    if use_batch_processor:
        processor = BatchSpanProcessor(
            exporter,
            max_queue_size=2048,
            max_export_batch_size=512,
            schedule_delay_millis=5000,
        )
    else:
        processor = SimpleSpanProcessor(exporter)
    return CheckoutRedactingSpanProcessor(processor)


def init_telemetry(
    service_name: str | None = None,
    component: str | None = None,
    *,
    use_batch_processor: bool = True,
) -> Optional["TracerProvider"]:
    """
    Initialize OpenTelemetry tracing.

    This should be called ONCE at application startup, before Django/Celery
    starts processing requests. For Temporal workers, call this in the
    worker startup BEFORE importing Django models.

    Args:
        service_name: Override full service name
        component: Component suffix (e.g., "django", "celery", "temporal-worker")
        use_batch_processor: Use BatchSpanProcessor (True) or SimpleSpanProcessor (False)

    Returns:
        TracerProvider instance or None if disabled

    Example:
        # In manage.py (top of file, before Django imports):
        from tfc.telemetry import init_telemetry
        init_telemetry(component="django")

        # In celery.py (top of file):
        from tfc.telemetry import init_telemetry
        init_telemetry(component="celery")

        # In temporal worker:
        from tfc.telemetry import init_telemetry
        init_telemetry(component="temporal-worker")
    """
    global _initialized, _tracer_provider

    logger.debug(
        "init_telemetry_called", component=component, otel_enabled=OTEL_ENABLED
    )

    if not OTEL_ENABLED:
        logger.info("otel_disabled")
        return None

    if _initialized:
        logger.debug("otel_already_initialized")
        return _tracer_provider

    # Lazy imports - only load OTel when actually initializing
    from opentelemetry import trace
    from opentelemetry.baggage.propagation import W3CBaggagePropagator
    from opentelemetry.propagate import set_global_textmap
    from opentelemetry.propagators.b3 import B3MultiFormat
    from opentelemetry.propagators.composite import CompositePropagator
    from opentelemetry.sdk.resources import SERVICE_NAME, SERVICE_VERSION, Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.trace.propagation.tracecontext import (
        TraceContextTextMapPropagator,
    )

    # Set up context propagators (critical for trace context flow)
    propagator = CompositePropagator(
        [
            TraceContextTextMapPropagator(),
            W3CBaggagePropagator(),
            B3MultiFormat(),
        ]
    )
    set_global_textmap(propagator)

    if service_name is None:
        service_name = get_service_name(component)

    resource = Resource.create(
        {
            SERVICE_NAME: service_name,
            SERVICE_VERSION: get_service_version(),
            "deployment.environment": os.getenv("ENV_TYPE", "local"),
            "service.namespace": "futureagi",
        }
    )

    sampler = _get_sampler()
    _tracer_provider = TracerProvider(resource=resource, sampler=sampler)

    try:
        exporter = _create_exporter()
        _tracer_provider.add_span_processor(
            build_span_processor(exporter, use_batch_processor)
        )
        logger.info(
            "otel_exporter_configured",
            endpoint=get_otlp_endpoint(),
            protocol=get_otlp_protocol(),
            processor="batch" if use_batch_processor else "simple",
        )
    except Exception as e:
        logger.warning("otel_exporter_failed", error=str(e))

    trace.set_tracer_provider(_tracer_provider)
    _initialized = True

    # Reset Temporal tracing interceptor so it uses the new provider
    try:
        from .temporal import reset_tracing_interceptor

        reset_tracing_interceptor()
    except ImportError:
        pass

    # Verify the provider was set correctly
    verified_provider = trace.get_tracer_provider()
    provider_set_correctly = verified_provider is _tracer_provider

    logger.info(
        "otel_initialized",
        service_name=service_name,
        sampler=os.getenv("OTEL_TRACES_SAMPLER", "parentbased_traceidratio"),
        sampler_arg=os.getenv("OTEL_TRACES_SAMPLER_ARG", "1.0"),
        provider_verified=provider_set_correctly,
    )

    if not provider_set_correctly:
        logger.error(
            "otel_provider_mismatch",
            expected_type=type(_tracer_provider).__name__,
            actual_type=type(verified_provider).__name__,
        )

    return _tracer_provider


def get_tracer_provider() -> Optional["TracerProvider"]:
    """
    Get the initialized tracer provider.

    Returns None if OpenTelemetry is not initialized or disabled.
    """
    return _tracer_provider


def get_tracer(name: str):
    """
    Get a tracer instance for creating spans.

    Args:
        name: Tracer name (typically __name__)

    Returns:
        Tracer instance

    Example:
        tracer = get_tracer(__name__)
        with tracer.start_as_current_span("my-operation") as span:
            span.set_attribute("key", "value")
            do_work()
    """
    from opentelemetry import trace

    return trace.get_tracer(name)


def verify_telemetry() -> dict:
    """
    Verify that OpenTelemetry is properly configured.

    Returns a dict with diagnostic information useful for debugging.

    Usage:
        from tfc.telemetry import verify_telemetry
        info = verify_telemetry()
        print(info)
    """
    from opentelemetry import trace

    provider = trace.get_tracer_provider()
    provider_type = type(provider).__name__

    # Check if it's a real TracerProvider or NoOp
    is_noop = "NoOp" in provider_type or "Proxy" in provider_type

    # Check if our global is set
    has_our_provider = _tracer_provider is not None
    providers_match = provider is _tracer_provider if _tracer_provider else False

    return {
        "otel_enabled": OTEL_ENABLED,
        "initialized": _initialized,
        "provider_type": provider_type,
        "is_noop_provider": is_noop,
        "has_our_provider": has_our_provider,
        "providers_match": providers_match,
        "endpoint": get_otlp_endpoint(),
        "protocol": get_otlp_protocol(),
    }
