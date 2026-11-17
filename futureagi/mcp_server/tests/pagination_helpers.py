"""Read real REST page-size bounds for catalog and generator assertions."""

import re

_ANY_ID = "00000000-0000-0000-0000-000000000001"


def _query_serializer(handler):
    """The query serializer a validated_request handler validates with, if any."""
    options = getattr(handler, "_swagger_auto_schema", None) or {}
    if "query_serializer" not in options:
        options = options.get("get", {})
    serializer = options.get("query_serializer")
    return serializer() if isinstance(serializer, type) else serializer


def server_page_size_bound(tool):
    """Resolve the tool's route like the executor does and read the real bound.

    Returns the page-size query field and the largest page size the server
    enforces (None when it enforces none): the paginator's max_page_size and
    any max_value the view's query serializer puts on the same field.
    """
    from django.urls import resolve

    func = resolve(re.sub(r"{[^}]+}", _ANY_ID, tool.request["path"])).func
    view = getattr(func, "cls", None) or func.view_class
    method = tool.request["method"].lower()
    handler = getattr(
        view, func.actions[method] if hasattr(func, "actions") else method
    )
    paginator = view.pagination_class
    assert paginator is not None, f"{tool.name} view has no pagination_class"
    field = paginator.page_size_query_param
    bounds = [paginator.max_page_size]
    serializer = _query_serializer(handler)
    if serializer is not None and field in serializer.fields:
        bounds.append(getattr(serializer.fields[field], "max_value", None))
    bounds = [bound for bound in bounds if bound is not None]
    return field, (min(bounds) if bounds else None)
