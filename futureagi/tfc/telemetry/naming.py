"""
Span naming utilities for better trace readability.

Makes traces human-friendly by using descriptive span names like:
- "POST /api/v1/experiments/ → ExperimentViewSet.create" (instead of just "POST")
- "SELECT experiments_experiment" (instead of just "SELECT")
- "RunExperimentWorkflow.run" (for Temporal workflows)

Usage:
    # Applied automatically when using instrument_for_django()
    # Or manually:
    from tfc.telemetry.naming import django_request_hook, django_response_hook
    instrument_django(request_hook=django_request_hook, response_hook=django_response_hook)
"""

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from django.http import HttpRequest, HttpResponse
    from opentelemetry.trace import Span


# =============================================================================
# Django HTTP Span Naming
# =============================================================================


def _exported_path(request: "HttpRequest") -> str:
    """The request path with a checkout session redacted (others unchanged)."""
    # Imported on use: this module loads before Django is set up.
    from tfc.logging.checkout import scrub_checkout_payload

    return scrub_checkout_payload(request.path)


def django_request_hook(span: "Span", request: "HttpRequest") -> None:
    """
    Hook called at the start of each Django request.

    Sets initial span attributes. The span name will be updated in response_hook
    once we have access to the resolved view.
    """
    if not span.is_recording():
        return

    try:
        from .redaction import capture_span_values

        # Keep values until the outer instrumentor ends this span, even after
        # the logging middleware resets its scope and these URLs are scrubbed.
        capture_span_values(span, request)
        # Add request path as attribute (useful for filtering)
        span.set_attribute("http.target", _exported_path(request))

        # Add query string if present (masked)
        if request.GET:
            # Just indicate query params exist, don't log values here
            span.set_attribute("http.query_params_count", len(request.GET))

    except Exception:
        pass  # Don't fail the request if hook fails


def django_response_hook(
    span: "Span",
    request: "HttpRequest",
    response: "HttpResponse",
) -> None:
    """
    Hook called at the end of each Django request.

    Updates the span name to include the view name for better readability.
    Examples:
    - "POST /api/v1/experiments/ → ExperimentViewSet.create"
    - "GET /api/v1/users/me/ → UserViewSet.me"
    - "GET /health/ → health_check"
    """
    if not span.is_recording():
        return

    try:
        # Get the resolved view name
        view_name = _get_view_name(request)

        if view_name:
            # Update span name to be more descriptive
            method = request.method or "HTTP"
            # Redact before truncation so no partial session value remains.
            path = _exported_path(request)

            # Truncate long paths
            if len(path) > 50:
                path = path[:47] + "..."

            # Format: "POST /api/v1/experiments/ → ExperimentViewSet.create"
            span.update_name(f"{method} {path} → {view_name}")

            # Also set as attribute for searching
            span.set_attribute("http.view_name", view_name)

        # Add response info
        span.set_attribute("http.status_code", response.status_code)

        # Add user info if authenticated (after auth middleware)
        if hasattr(request, "user") and request.user.is_authenticated:
            span.set_attribute("enduser.id", str(request.user.id))
            if hasattr(request.user, "email"):
                span.set_attribute("enduser.email", request.user.email)

    except Exception:
        pass  # Don't fail the request if hook fails


def _get_view_name(request: "HttpRequest") -> str | None:
    """
    Extract a human-readable view name from the request.

    Returns names like:
    - "ExperimentViewSet.create" (for DRF viewsets)
    - "UserViewSet.list" (for DRF viewsets)
    - "health_check" (for function-based views)
    - "MyView.get" (for class-based views)
    """
    # Try resolver_match first (set by Django URL routing)
    if hasattr(request, "resolver_match") and request.resolver_match:
        match = request.resolver_match

        # Get view name from resolver
        view_name = match.view_name
        if view_name:
            # Clean up DRF-style names like "experiment-list" → "experiment.list"
            view_name = view_name.replace("-", ".")

        # Try to get the actual view class/function name
        func = match.func

        # Handle DRF ViewSets (most common case)
        if hasattr(func, "cls"):
            cls = func.cls
            cls_name = cls.__name__

            # Get the action name (create, list, retrieve, etc.)
            if hasattr(func, "actions"):
                # For ViewSets, actions maps HTTP methods to action names
                method = request.method.lower() if request.method else "get"
                action = func.actions.get(method, method)
                return f"{cls_name}.{action}"

            return cls_name

        # Handle class-based views
        if hasattr(func, "view_class"):
            cls_name = func.view_class.__name__
            method = request.method.lower() if request.method else "get"
            return f"{cls_name}.{method}"

        # Handle function-based views
        if hasattr(func, "__name__"):
            return func.__name__

        # Fall back to URL name
        if view_name:
            return view_name

    return None


# =============================================================================
# Database Span Naming
# =============================================================================


def get_db_span_name(query: str, operation: str | None = None) -> str:
    """
    Create a descriptive span name for database queries.

    Examples:
    - "SELECT experiments_experiment" (instead of just "SELECT")
    - "INSERT accounts_user"
    - "UPDATE tracer_trace"
    """
    if not query:
        return operation or "DB"

    query_upper = query.strip().upper()

    # Extract operation
    if not operation:
        for op in ["SELECT", "INSERT", "UPDATE", "DELETE", "CREATE", "DROP", "ALTER"]:
            if query_upper.startswith(op):
                operation = op
                break
        else:
            operation = "QUERY"

    # Extract table name
    table_name = _extract_table_name(query)

    if table_name:
        return f"{operation} {table_name}"
    return operation


def _extract_table_name(query: str) -> str | None:
    """Extract the main table name from a SQL query."""
    query = query.strip()

    # Patterns for different query types
    patterns = [
        # SELECT ... FROM table_name
        r"FROM\s+[\"']?(\w+)[\"']?",
        # INSERT INTO table_name
        r"INSERT\s+INTO\s+[\"']?(\w+)[\"']?",
        # UPDATE table_name
        r"UPDATE\s+[\"']?(\w+)[\"']?",
        # DELETE FROM table_name
        r"DELETE\s+FROM\s+[\"']?(\w+)[\"']?",
    ]

    for pattern in patterns:
        match = re.search(pattern, query, re.IGNORECASE)
        if match:
            return match.group(1)

    return None


# =============================================================================
# Temporal Span Naming
# =============================================================================


def get_workflow_span_name(workflow_type: str, workflow_id: str) -> str:
    """
    Create a descriptive span name for Temporal workflows.

    Example: "RunExperimentWorkflow [exp-123]"
    """
    # Clean up workflow type name
    if "." in workflow_type:
        workflow_type = workflow_type.split(".")[-1]

    # Truncate long workflow IDs
    if len(workflow_id) > 20:
        workflow_id = workflow_id[:17] + "..."

    return f"{workflow_type} [{workflow_id}]"


def get_activity_span_name(activity_type: str) -> str:
    """
    Create a descriptive span name for Temporal activities.

    Example: "run_single_datapoint"
    """
    # Clean up activity type name
    if "." in activity_type:
        activity_type = activity_type.split(".")[-1]

    return activity_type


# =============================================================================
# HTTP Client Span Naming
# =============================================================================


def get_http_client_span_name(method: str, url: str) -> str:
    """
    Create a descriptive span name for outgoing HTTP requests.

    Examples:
    - "POST api.openai.com/v1/chat/completions"
    - "GET api.anthropic.com/v1/messages"
    """
    try:
        from urllib.parse import urlparse

        parsed = urlparse(url)
        host = parsed.netloc or "unknown"
        path = parsed.path or "/"

        # Truncate long paths
        if len(path) > 40:
            path = path[:37] + "..."

        # Remove common prefixes for cleaner names
        host = host.replace("www.", "")

        return f"{method} {host}{path}"
    except Exception:
        return f"{method} HTTP"


# =============================================================================
# Utility Functions
# =============================================================================


def sanitize_span_name(name: str, max_length: int = 100) -> str:
    """
    Sanitize and truncate a span name.

    - Removes newlines and excessive whitespace
    - Truncates to max_length
    - Ensures it's a valid string
    """
    if not name:
        return "unknown"

    # Clean up whitespace
    name = " ".join(name.split())

    # Truncate
    if len(name) > max_length:
        name = name[: max_length - 3] + "..."

    return name


__all__ = [
    "django_request_hook",
    "django_response_hook",
    "get_db_span_name",
    "get_workflow_span_name",
    "get_activity_span_name",
    "get_http_client_span_name",
    "sanitize_span_name",
]
