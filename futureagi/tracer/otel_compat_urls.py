"""Compatibility URL routes for third-party SDKs that use fixed OTLP paths.

The Langfuse SDK (v3+) defaults to POSTing traces at::

    {base_url}/api/public/otel/v1/traces

This module maps that path to the same ``OTLPTraceHTTPView`` that handles
``/tracer/v1/traces``, so Langfuse users only need to set ``LANGFUSE_HOST``
without overriding ``LANGFUSE_OTEL_TRACES_EXPORT_PATH``.
"""

from django.urls import path

urlpatterns = [
    # Migrated to fi-collector service (June 2026)
    # path("v1/traces", OTLPTraceHTTPView.as_view(), name="otel-compat-traces"),
]
