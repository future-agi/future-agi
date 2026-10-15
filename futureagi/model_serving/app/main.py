import atexit
import os
import time
from contextlib import asynccontextmanager

import structlog

logger = structlog.get_logger(__name__)

# OpenTelemetry initialization state
_otel_initialized = False
_tracer_provider = None


def _init_opentelemetry():
    """Initialize OpenTelemetry for model serving."""
    global _otel_initialized, _tracer_provider
    if _otel_initialized:
        return

    otel_enabled = os.getenv("OTEL_ENABLED", "true").lower() in ("true", "1", "yes")
    if not otel_enabled:
        return

    try:
        from opentelemetry import trace
        from opentelemetry.baggage.propagation import W3CBaggagePropagator
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
            OTLPSpanExporter as OTLPSpanExporterGRPC,
        )
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter as OTLPSpanExporterHTTP,
        )
        from opentelemetry.propagate import set_global_textmap
        from opentelemetry.propagators.b3 import B3MultiFormat
        from opentelemetry.propagators.composite import CompositePropagator
        from opentelemetry.sdk.resources import SERVICE_NAME, SERVICE_VERSION, Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.sdk.trace.sampling import ParentBasedTraceIdRatio
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

        service_name = os.getenv("OTEL_SERVICE_NAME", "model-serving")
        otlp_endpoint = os.getenv(
            "OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317"
        )
        otlp_protocol = os.getenv("OTEL_EXPORTER_OTLP_PROTOCOL", "grpc")
        sample_rate = float(os.getenv("OTEL_TRACES_SAMPLER_ARG", "1.0"))

        resource = Resource.create(
            {
                SERVICE_NAME: service_name,
                SERVICE_VERSION: os.getenv("APP_VERSION", "1.0.0"),
                "deployment.environment": os.getenv("ENVIRONMENT", "embedding"),
                "service.namespace": "futureagi",
            }
        )

        sampler = ParentBasedTraceIdRatio(sample_rate)
        _tracer_provider = TracerProvider(resource=resource, sampler=sampler)

        if otlp_protocol == "http/protobuf":
            endpoint = otlp_endpoint.rstrip("/") + "/v1/traces"
            exporter = OTLPSpanExporterHTTP(endpoint=endpoint)
        else:
            exporter = OTLPSpanExporterGRPC(endpoint=otlp_endpoint, insecure=True)

        _tracer_provider.add_span_processor(
            BatchSpanProcessor(
                exporter,
                max_queue_size=2048,
                max_export_batch_size=512,
                schedule_delay_millis=5000,
            )
        )

        trace.set_tracer_provider(_tracer_provider)
        atexit.register(_shutdown_telemetry)

        _otel_initialized = True
        logger.info(
            "otel_initialized",
            service_name=service_name,
            endpoint=otlp_endpoint,
            protocol=otlp_protocol,
            sample_rate=sample_rate,
        )

    except ImportError:
        pass
    except Exception as e:
        logger.warning("otel_init_failed", error=str(e))


def _shutdown_telemetry():
    """Flush pending spans before shutdown."""
    global _tracer_provider
    if _tracer_provider:
        try:
            _tracer_provider.force_flush(timeout_millis=5000)
            _tracer_provider.shutdown()
        except Exception:
            pass


# Initialize OpenTelemetry early
_init_opentelemetry()

import logging

import sentry_sdk
from app.servable_models.constants import LOGGING_CONFIG
from app.utils.load_model import ModelLoader
from app.v1.endpoints import router as model_serving_router
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.logging import LoggingIntegration

# Configure basic logging for third-party libraries
logging.basicConfig(
    level=getattr(logging, LOGGING_CONFIG["level"].upper(), logging.INFO),
    format=LOGGING_CONFIG["format"],
)

sentry_dsn = os.getenv("SENTRY_DSN")
if sentry_dsn:
    sentry_sdk.init(
        dsn=sentry_dsn,
        integrations=[
            FastApiIntegration(
                transaction_style="endpoint",
                failed_request_status_codes={400, 401, 403, 404, *range(500, 600)},
                http_methods_to_capture=("GET", "POST", "PUT", "DELETE", "PATCH"),
            ),
            LoggingIntegration(level=logging.INFO, event_level=logging.ERROR),
        ],
        traces_sample_rate=1,
        profiles_sample_rate=1,
        enable_tracing=True,
        environment=os.getenv("ENVIRONMENT", "embedding"),
        release=os.getenv("APP_VERSION", "1.0.0"),
        server_name="embedding-model-serving",
        max_breadcrumbs=100,
    )
    logger.info("sentry_initialized")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan context manager for startup and shutdown tasks."""
    start_time = time.time()
    try:
        ModelLoader.load_models()
        logger.info("app_started", startup_time=f"{time.time() - start_time:.2f}s")
        yield
    except Exception as e:
        logger.error("app_startup_failed", error=str(e))
        raise
    finally:
        try:
            ModelLoader.unload_all_models()
        except Exception as e:
            logger.error("cleanup_failed", error=str(e))


app = FastAPI(
    title="Model Serving API",
    description="High-performance embedding model serving service",
    version="1.0.0",
    docs_url="/docs" if os.getenv("ENABLE_DOCS", "false").lower() == "true" else None,
    redoc_url="/redoc" if os.getenv("ENABLE_DOCS", "false").lower() == "true" else None,
    lifespan=lifespan,
)

# Instrument FastAPI after app is created
if _otel_initialized:
    try:
        from opentelemetry import trace
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
        from opentelemetry.instrumentation.requests import RequestsInstrumentor

        tracer_provider = trace.get_tracer_provider()
        FastAPIInstrumentor.instrument_app(app, tracer_provider=tracer_provider)
        RequestsInstrumentor().instrument(tracer_provider=tracer_provider)
        HTTPXClientInstrumentor().instrument(tracer_provider=tracer_provider)

        try:
            from opentelemetry.instrumentation.logging import LoggingInstrumentor

            LoggingInstrumentor().instrument(
                tracer_provider=tracer_provider, set_logging_format=True
            )
        except ImportError:
            pass

        logger.info("otel_instrumentation_complete")
    except ImportError:
        pass
    except Exception as e:
        logger.warning("otel_instrumentation_failed", error=str(e))

app.add_middleware(
    CORSMiddleware,
    allow_origins=[],
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

app.add_middleware(GZipMiddleware, minimum_size=1000)


@app.middleware("http")
async def log_requests(request, call_next):
    """Request logging middleware with error tracking."""
    start_time = time.time()
    request_id = f"req_{int(start_time * 1000)}"

    logger.info(
        "request_started",
        request_id=request_id,
        method=request.method,
        path=request.url.path,
        client=request.client.host if request.client else "unknown",
    )

    try:
        response = await call_next(request)
        process_time = time.time() - start_time

        if response.status_code >= 400:
            logger.warning(
                "request_completed",
                request_id=request_id,
                status=response.status_code,
                duration_s=round(process_time, 3),
            )
        else:
            logger.info(
                "request_completed",
                request_id=request_id,
                status=response.status_code,
                duration_s=round(process_time, 3),
            )

        response.headers["X-Request-ID"] = request_id
        response.headers["X-Process-Time"] = str(process_time)

        return response

    except Exception as exc:
        process_time = time.time() - start_time
        error_id = sentry_sdk.capture_exception(exc)

        logger.error(
            "request_failed",
            request_id=request_id,
            error_id=error_id,
            duration_s=round(process_time, 3),
            error=str(exc),
            error_type=exc.__class__.__name__,
        )
        raise


app.include_router(model_serving_router, prefix="/model")


@app.get("/")
async def root():
    """Root endpoint with basic service info."""
    return {"service": "Model Serving API", "version": "1.0.0", "status": "healthy"}


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "healthy"}


@app.exception_handler(500)
async def internal_server_error_handler(request, exc):
    """Handle 500 Internal Server Errors."""
    error_id = sentry_sdk.capture_exception(exc)

    logger.error(
        "internal_server_error",
        error_id=error_id,
        path=request.url.path,
        method=request.method,
        error=str(exc),
        error_type=exc.__class__.__name__,
    )

    return JSONResponse(
        status_code=500,
        content={
            "detail": "Internal server error",
            "error_id": error_id,
            "timestamp": time.time(),
        },
    )


@app.exception_handler(404)
async def not_found_handler(request, exc):
    """Handle 404 Not Found errors."""
    logger.warning(
        "not_found",
        path=request.url.path,
        method=request.method,
        query=request.url.query,
    )

    return JSONResponse(
        status_code=404,
        content={
            "detail": "Not found",
            "path": request.url.path,
            "timestamp": time.time(),
        },
    )


@app.exception_handler(422)
async def validation_error_handler(request, exc):
    """Handle 422 Validation Errors."""
    logger.warning(
        "validation_error",
        path=request.url.path,
        method=request.method,
        error=str(exc),
    )

    return JSONResponse(
        status_code=422,
        content={
            "detail": "Validation error",
            "path": request.url.path,
            "timestamp": time.time(),
        },
    )


@app.exception_handler(Exception)
async def general_exception_handler(request, exc):
    """Catch-all exception handler for unhandled errors."""
    error_id = sentry_sdk.capture_exception(exc)

    logger.error(
        "unhandled_exception",
        error_id=error_id,
        path=request.url.path,
        method=request.method,
        error=str(exc),
        error_type=exc.__class__.__name__,
    )

    return JSONResponse(
        status_code=500,
        content={
            "detail": "An unexpected error occurred",
            "error_id": error_id,
            "timestamp": time.time(),
        },
    )


def main():
    """Main entry point for the application when run as a script."""
    import os

    import uvicorn

    # Use environment variables with safe defaults
    host = os.getenv("MODEL_SERVING_HOST", "127.0.0.1")
    port = int(os.getenv("MODEL_SERVING_PORT", "8080"))
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
