"""DRF ViewSet → MCP Tool Bridge.

Two registration approaches:

1. Class-level decorator (recommended) — auto-introspects serializers:

    @expose_to_mcp(
        category="tracing",
        tools={
            "list": {"name": "tracing_list_projects"},
            "retrieve": {"name": "tracing_get_project"},
            "create": {"name": "tracing_create_project"},
            "update_project_name": {
                "name": "tracing_update_project_name",
                "serializer": "ProjectNameUpdateSerializer",
            },
        },
    )
    class ProjectView(BaseModelViewSetMixinWithUserOrg, ModelViewSet):
        serializer_class = ProjectSerializer

2. Per-tool decorator (explicit Pydantic model):

    @viewset_tool(name="...", viewset_class="...", action="...", ...)
    class MyInput(PydanticBaseModel): ...
"""

from __future__ import annotations

import importlib
import inspect
import logging
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, ClassVar

from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field as PydanticField

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.confirmations import classify as classify_execution_policy
from ai_tools.formatting import key_value_block, markdown_table, section

try:
    import structlog

    logger = structlog.get_logger(__name__)
except ImportError:
    logger = logging.getLogger(__name__)

# Registry of pending expose_to_mcp registrations (processed at app ready)
_pending_registrations: list[tuple] = []

# APIView verb-handler keys (`_resolve_apiview_handler` fallback).
HTTP_VERB_KEYS = {"get", "post", "put", "patch", "delete"}

# HTTP methods that carry a request body / mutate state.
WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def _strict_registration() -> bool:
    """True when bridge registration failures should raise instead of being
    swallowed: under pytest, with AI_TOOLS_STRICT_BRIDGE=1, or settings.DEBUG.

    Prod (DEBUG=False, no env override) keeps the historical swallow+log
    behavior so one broken bridge can't take the whole app down.
    """
    if os.environ.get("AI_TOOLS_STRICT_BRIDGE") == "1":
        return True
    if "pytest" in sys.modules:
        return True
    try:
        from django.conf import settings

        return bool(settings.DEBUG)
    except Exception:
        # Decorators fire at import time — settings may not be configured
        # yet (ImproperlyConfigured). Default to prod behavior.
        return False

DRF_FIELD_TYPE_MAP = {
    "CharField": (str, "string"),
    "TextField": (str, "string"),
    "SlugField": (str, "string"),
    "EmailField": (str, "string"),
    "URLField": (str, "string"),
    "UUIDField": (str, "string"),
    "IntegerField": (int, "integer"),
    "FloatField": (float, "number"),
    "DecimalField": (float, "number"),
    "BooleanField": (bool, "boolean"),
    "NullBooleanField": (bool | None, "boolean"),
    "DateTimeField": (str, "string"),
    "DateField": (str, "string"),
    "TimeField": (str, "string"),
    "JSONField": (Any, "object"),
    "DictField": (dict, "object"),
    "ListField": (list, "array"),
    "ListSerializer": (list, "array"),
    "ChoiceField": (str, "string"),
    "MultipleChoiceField": (list, "array"),
    "FileField": (str, "string"),
    "ImageField": (str, "string"),
    "SerializerMethodField": (str, "string"),
    "PrimaryKeyRelatedField": (str, "string"),
    "HiddenField": None,
}

DETAIL_ACTIONS = {"retrieve", "update", "partial_update", "destroy"}
LIST_ACTIONS = {"list"}
WRITE_ACTIONS = {"create", "update", "partial_update"}

ACTION_METHOD_MAP = {
    "list": "GET",
    "retrieve": "GET",
    "create": "POST",
    "update": "PUT",
    "partial_update": "PATCH",
    "destroy": "DELETE",
}

ACTION_VERB_MAP = {
    "list": "List",
    "retrieve": "Get",
    "create": "Create a new",
    "update": "Update an existing",
    "partial_update": "Partially update",
    "destroy": "Delete a",
    "get": "List",  # APIView fallback
    "post": "Create or submit a",
    "put": "Replace a",
    "patch": "Partially update a",
    "delete": "Delete a",
}

# Maps action name → tool name prefix. "list" → "list_users",
# "retrieve" → "get_user", "create" → "create_user", etc.
ACTION_TOOL_PREFIX = {
    "list": "list",
    "retrieve": "get",
    "create": "create",
    "update": "update",
    "partial_update": "update",
    "destroy": "delete",
    "get": "list",  # APIView list-style
    "post": "create",
    "put": "update",
    "patch": "update",
    "delete": "delete",
}


def _derive_tool_name(
    action_name: str,
    entity_name: str,
    detail: bool,
    verb_map: dict | None = None,
) -> str:
    """Auto-generate a tool name: {verb}_{entity[s]}.

    list / get-without-detail → plural entity ("list_users", "list_workspaces").
    retrieve / detail HTTP get → singular ("get_user").
    create / update / destroy → singular ("create_user", "delete_user").

    Pass `verb_map` to override the default prefixes per decorator,
    e.g. {"retrieve": "fetch"} → "fetch_user" instead of "get_user".
    """
    effective = dict(ACTION_TOOL_PREFIX)
    if verb_map:
        effective.update(verb_map)
    prefix = effective.get(action_name, action_name)
    pluralize = action_name in LIST_ACTIONS or (action_name == "get" and not detail)
    suffix = "s" if pluralize and not entity_name.endswith("s") else ""
    return f"{prefix}_{entity_name}{suffix}"


# Kept for backward compat with any callers that still reference it.
ACTION_DESCRIPTION_MAP = {
    "list": "List all {entity}s in the workspace.",
    "retrieve": "Get detailed information about a single {entity} by ID.",
    "create": "Create a new {entity}.",
    "update": "Update an existing {entity}.",
    "partial_update": "Partially update an existing {entity}.",
    "destroy": "Delete a {entity}.",
}


def _derive_entity_name(viewset_cls) -> str:
    """Turn a ViewSet/APIView class name into a snake_case singular entity.

    Strips common suffixes in the right order (APIView before View before
    ViewSet) so `UserListAPIView` → `user`, `PromptTemplateViewSet` →
    `prompt_template`, `ProjectView` → `project`. Also drops list/detail
    intent suffixes that obscure the entity.
    """
    import re

    name = viewset_cls.__name__
    for suffix in ("APIView", "GenericViewSet", "ViewSet", "View"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
            break
    for intent_suffix in ("List", "Detail", "Operations"):
        if name.endswith(intent_suffix):
            name = name[: -len(intent_suffix)]
            break
    # CamelCase -> snake_case, treating runs of capitals (acronyms like
    # APIKey, TTSVoice) as a single token: APIKey -> api_key, TTSVoice -> tts_voice.
    snake = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", name)
    snake = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", snake)
    snake = snake.lower()
    return snake or viewset_cls.__name__.lower()


def _clean_docstring(text: str | None) -> str:
    if not text:
        return ""
    lines = [line.strip() for line in text.strip().splitlines() if line.strip()]
    out: list[str] = []
    for line in lines:
        if (
            line.startswith("Args")
            or line.startswith("Returns")
            or line.startswith(":param")
        ):
            break
        out.append(line)
    return " ".join(out).strip()


def _first_docstring_paragraph(text: str | None) -> str:
    """First paragraph of a docstring, cleaned for use as a description.

    Custom @action docstrings tend to open with a good one-line summary and
    then go into query-param details — only the opening paragraph belongs in
    the tool description.
    """
    if not text:
        return ""
    first = text.strip().split("\n\n", 1)[0]
    return _clean_docstring(first)


def _derive_description(
    viewset_cls,
    action_name: str,
    entity_name: str,
    serializer_cls,
) -> str:
    """Compose a tool description from the serializer's docstring.

    Output shape: "{verb} {entity}s. {serializer_doc}"
    Falls back to the ViewSet's docstring, then a generic action-only string.
    """
    verb = ACTION_VERB_MAP.get(action_name, action_name)
    plural_marker = "s" if action_name in LIST_ACTIONS or action_name == "get" else ""

    serializer_doc = ""
    if serializer_cls is not None:
        serializer_doc = _clean_docstring(getattr(serializer_cls, "__doc__", None))

    if not serializer_doc:
        serializer_doc = _clean_docstring(getattr(viewset_cls, "__doc__", None))

    base = f"{verb} {entity_name}{plural_marker}."
    if serializer_doc:
        return f"{base} {serializer_doc}"
    return base


@dataclass
class ViewSetBinding:
    viewset_class: str
    action: str
    method: str
    detail: bool
    pk_field: str | None
    serializer_override: str | None = None
    query_params: dict | None = None
    pk_kwarg: str | None = (
        None  # APIView path-kwarg name for the id (e.g. call_execution_id)
    )
    # Extra REQUIRED URL path kwargs beyond the pk routing, normalized to
    # {kwarg_name: {"description": ..., "id_source": ...}}. Each becomes a
    # required str input field and is routed into view.kwargs in execute()
    # IN ADDITION to pk_field/pk_kwarg (e.g. complete_item(request,
    # queue_id=None, pk=None) = pk_field "item_id" + path_kwargs ["queue_id"]).
    path_kwargs: dict | None = None
    # Tool name that yields valid ids for this tool's pk_field — consumed for
    # input-description hints AND by verify_bridges.py id harvesting (A7).
    id_source: str | None = None
    # TH-4667 filter integrity: for GET-collection tools whose input model was
    # auto-built (no explicit query_params), maps each ADVERTISED universal
    # param ("search"/"page"/"page_size") to the query-param name the view
    # actually honors (e.g. {"search": "name", "page_size": "limit"}). Params
    # absent from the map are NOT advertised. None on every other tool shape.
    collection_param_map: dict | None = None
    # F1 scope-honesty: optional callable ``(params: dict, data: Any) -> str | None``
    # that returns a one-line SCOPE LABEL prepended to the result content. Used so
    # a list/count tool whose total reflects the *applied* filters (or lack of
    # them) cannot be misread by the model — e.g. ``list_trace_projects`` labels
    # whether its count is observe-only / experiment-only / both, and that it is
    # always scoped to the active workspace. ``params`` is the (exclude_none)
    # dict the model actually sent; ``data`` is the unwrapped response.
    result_scope: Callable | None = None


def _resolve_class(dotted_path: str):
    module_path, class_name = dotted_path.rsplit(".", 1)
    module = importlib.import_module(module_path)
    return getattr(module, class_name)


def _build_drf_request(
    method: str,
    data: dict,
    query_params: dict,
    context: ToolContext,
    extra_query: dict | None = None,
):
    from rest_framework.parsers import JSONParser
    from rest_framework.request import Request
    from rest_framework.test import APIRequestFactory

    factory = APIRequestFactory()

    import json as _json

    # Params routed with "in": "query" on a write method land on the query
    # string (APIRequestFactory honors query strings embedded in the path),
    # so handlers that read request.query_params on POST/PUT still work.
    # Non-scalar values are JSON-encoded — urlencode would otherwise emit
    # Python reprs / iterate dict keys, which no handler can parse.
    path = "/bridge/"
    if extra_query:
        from urllib.parse import urlencode

        path = "/bridge/?" + urlencode(
            {
                k: _json.dumps(v) if isinstance(v, (dict, list)) else v
                for k, v in extra_query.items()
            }
        )

    if method.upper() in ("GET", "HEAD", "OPTIONS"):
        merged_query = {**query_params, **(extra_query or {})}
        # dict values become JSON strings (handlers json.loads them, e.g.
        # the observe `filters={"project_id": ...}` query params). LISTS are
        # deliberately left alone: Django's test factory urlencodes them
        # doseq-style (?k=a&k=b), which `request.query_params.getlist`-style
        # handlers (e.g. root_spans' trace_ids) depend on.
        merged_query = {
            k: _json.dumps(v) if isinstance(v, dict) else v
            for k, v in merged_query.items()
        }
        django_request = factory.get("/bridge/", data=merged_query)
    elif method.upper() == "DELETE":
        django_request = factory.delete(path, data=data, format="json")
    elif method.upper() in ("PUT", "PATCH"):
        fn = factory.put if method.upper() == "PUT" else factory.patch
        django_request = fn(path, data=data, format="json")
    else:
        django_request = factory.post(path, data=data, format="json")

    django_request.user = context.user
    django_request.workspace = context.workspace
    django_request.organization = context.organization

    drf_request = Request(django_request, parsers=[JSONParser()])
    drf_request.user = context.user
    drf_request.workspace = context.workspace
    drf_request.organization = context.organization
    # Pin auth state (3B). The bridge Request has no authenticators, so the
    # first access to `request.auth` / `request.successful_authenticator`
    # would lazily run DRF's `_authenticate()` -> `_not_authenticated()`,
    # which CLOBBERS the stamped user back to AnonymousUser. Setting `.auth`
    # (and `_authenticator`) up front makes the stamped identity stable for
    # permission classes and handlers alike.
    drf_request.auth = None
    drf_request._authenticator = None

    return drf_request


def _instantiate_view(viewset_class, action: str, method: str, request, kwargs: dict):
    view = viewset_class()
    view.request = request
    view.action = action
    view.kwargs = kwargs
    view.format_kwarg = None
    view.args = ()
    view.headers = view.default_response_headers
    return view


def _is_apiview_class(cls) -> bool:
    """True if the class is a bare APIView (no ViewSet semantics)."""
    try:
        from rest_framework.views import APIView
        from rest_framework.viewsets import ViewSetMixin
    except ImportError:
        return False
    if not isinstance(cls, type):
        return False
    try:
        return issubclass(cls, APIView) and not issubclass(cls, ViewSetMixin)
    except TypeError:
        return False


def _resolve_apiview_handler(view, method: str, action_name: str):
    """Find the right callable on an APIView for a given HTTP method.

    APIViews expose handlers as `.get()`, `.post()`, `.put()`, `.patch()`,
    `.delete()`. The bridge config's `action` is treated as a hint:
      - if the APIView has a method matching `action_name`, use it
      - otherwise fall back to the HTTP method name (get/post/put/etc.)
    """
    if hasattr(view, action_name) and callable(getattr(view, action_name)):
        return getattr(view, action_name)
    return getattr(view, method.lower(), None)


def _unwrap_response(response) -> tuple[Any, bool]:
    # Non-DRF responses (FileResponse / HttpResponse text/csv) have no `.data`
    # attribute — DRF Response objects always do. Bridged export actions (CSV /
    # file downloads) return these, so extract the body as text instead of
    # collapsing to {} (TH-5415 / TH-5386). JSON tools return DRF Responses and
    # are unaffected (they have `.data`, so this branch is skipped).
    if not hasattr(response, "data"):
        try:
            if getattr(response, "streaming", False) or hasattr(
                response, "streaming_content"
            ):
                body = b"".join(response.streaming_content)
            else:
                body = getattr(response, "content", b"")
            text = (
                body.decode("utf-8", errors="replace")
                if isinstance(body, (bytes, bytearray))
                else str(body)
            )
        except Exception:
            text = ""
        return text, getattr(response, "status_code", 200) >= 400

    data = getattr(response, "data", None) or {}
    status_code = getattr(response, "status_code", 200)
    is_error = status_code >= 400

    if isinstance(data, dict) and "status" in data:
        if data.get("status") is True and "result" in data:
            return data["result"], False
        elif data.get("status") is False and "result" in data:
            result = data["result"]
            if isinstance(result, dict) and "error" in result:
                return result["error"], True
            return result, True

    return data, is_error


_NOT_FOUND_MESSAGE_RE = None


def _status_to_error_code(status_code: int, message: str) -> str:
    """Map a DRF response's HTTP status (and message) to a ToolResult
    error_code so denial semantics stay legible on the bridge path (3B):
    cross-tenant calls must surface as NOT_FOUND / PERMISSION_DENIED, not a
    generic INTERNAL_ERROR.

    Views commonly catch their own Http404/DoesNotExist and re-emit it as a
    400 ("Failed to retrieve X: No X matches the given query"), so a
    narrowly-scoped message heuristic upgrades those to NOT_FOUND.
    """
    import re

    global _NOT_FOUND_MESSAGE_RE
    if _NOT_FOUND_MESSAGE_RE is None:
        _NOT_FOUND_MESSAGE_RE = re.compile(
            r"(?i)(not found|does not exist|no \S[^.!]* matches the given query)"
        )

    if status_code in (401, 403):
        return "PERMISSION_DENIED"
    if status_code == 404:
        return "NOT_FOUND"
    if status_code == 429:
        return "RATE_LIMITED"
    # `status: false` envelopes ride on 200s; views re-emit 404s as 400s —
    # the message heuristic covers both.
    if _NOT_FOUND_MESSAGE_RE.search(message or ""):
        return "NOT_FOUND"
    if 400 <= status_code < 500:
        return "VALIDATION_ERROR"
    return "INTERNAL_ERROR"


def _format_result_for_llm(data: Any, action: str) -> str:
    if isinstance(data, str):
        return data

    if isinstance(data, dict):
        list_keys = [k for k in data if isinstance(data[k], list)]
        if list_keys:
            list_key = list_keys[0]
            return _format_list_response(data, list_key, list_key.rstrip("s"))

        pairs = []
        for k, v in data.items():
            label = k.replace("_", " ").title()
            if isinstance(v, (dict, list)):
                # Render nested values as compact (truncated) JSON instead of
                # dropping them — otherwise fields like an eval config's
                # `mapping`, `config`, or `filters` are invisible to Falcon,
                # since it only ever sees ToolResult.content (TH-5442).
                if not v:
                    continue
                import json as _json

                try:
                    rendered = _json.dumps(v, default=str)
                except Exception:
                    rendered = str(v)
                if len(rendered) > 800:
                    rendered = rendered[:800] + "…"
                pairs.append((label, rendered))
                continue
            if "id" in k.lower() and v:
                pairs.append((label, f"`{v}`"))
            else:
                pairs.append((label, v))
        return key_value_block(pairs) if pairs else str(data)

    if isinstance(data, list):
        if not data:
            return "_No results found._"
        if isinstance(data[0], dict):
            headers = list(data[0].keys())[:8]
            rows = [[item.get(h, "—") for h in headers] for item in data[:25]]
            return markdown_table(headers, rows)
        return "\n".join(f"- {item}" for item in data[:50])

    return str(data)


def _format_list_response(data: dict, list_key: str, entity_name: str) -> str:
    items = data.get(list_key, [])
    total = data.get("total_count", data.get("count", len(items)))

    if not items:
        return f"_No {entity_name}s found._"

    if not isinstance(items[0], dict):
        return "\n".join(f"- {item}" for item in items[:50])

    display_fields = ["name", "id", "trace_type", "type", "status", "created_at"]
    headers = [f for f in display_fields if any(f in item for item in items[:3])]
    if not headers:
        headers = list(items[0].keys())[:6]

    rows = []
    for item in items[:25]:
        row = []
        for h in headers:
            val = item.get(h, "—")
            if "id" in h.lower() and val and val != "—":
                row.append(f"`{val}`")
            elif "created" in h.lower() or "updated" in h.lower():
                row.append(str(val)[:10] if val else "—")
            else:
                row.append(str(val) if val is not None else "—")
        rows.append(row)

    display_headers = [h.replace("_", " ").title() for h in headers]
    table = markdown_table(display_headers, rows)
    return f"Showing {len(items)} of {total} {entity_name}(s)\n\n{table}"


def _serializer_to_pydantic(serializer_cls, include_fields=None, exclude_fields=None):
    """Convert a DRF serializer class to a Pydantic model for MCP schema generation."""
    serializer = serializer_cls()
    fields_dict = {}
    annotations = {}

    skip_fields = {
        "organization",
        "workspace",
        "deleted",
        "deleted_at",
        "created_at",
        "updated_at",
    }
    if exclude_fields:
        skip_fields.update(exclude_fields)

    for field_name, field_obj in serializer.fields.items():
        if field_name in skip_fields:
            continue
        if include_fields and field_name not in include_fields:
            continue
        if getattr(field_obj, "read_only", False):
            continue

        field_class_name = type(field_obj).__name__
        type_info = DRF_FIELD_TYPE_MAP.get(field_class_name)
        if type_info is None:
            continue
        python_type, _ = type_info

        help_text = getattr(field_obj, "help_text", None) or ""
        description = (
            str(help_text) if help_text else f"{field_name.replace('_', ' ').title()}"
        )

        is_required = getattr(field_obj, "required", False)

        pydantic_kwargs = {"description": description}

        # Relational fields (PrimaryKeyRelatedField / ManyRelatedField / model-
        # backed ModelChoiceField) build `.choices` by materializing their ENTIRE
        # related queryset into memory — for a FK to a large table that loads
        # millions of rows (OOM) and runs a DB query at import/registration time
        # (which also breaks whenever the live schema differs). They're already
        # mapped to `str`, so never enumerate them; only enumerate static
        # ChoiceField enums, which carry no `queryset`/`child_relation`.
        _is_relational = hasattr(field_obj, "queryset") or hasattr(
            field_obj, "child_relation"
        )
        if not _is_relational and hasattr(field_obj, "choices") and field_obj.choices:
            raw_choices = field_obj.choices
            if isinstance(raw_choices, dict):
                choices = list(raw_choices.keys())
            else:
                choices = [
                    c[0] if isinstance(c, (list, tuple)) else c for c in raw_choices
                ]
            if choices:
                from typing import Literal

                try:
                    if all(isinstance(c, str) for c in choices):
                        python_type = Literal[tuple(choices)]
                except Exception:
                    pass

        if hasattr(field_obj, "min_value") and field_obj.min_value is not None:
            pydantic_kwargs["ge"] = field_obj.min_value
        if hasattr(field_obj, "max_value") and field_obj.max_value is not None:
            pydantic_kwargs["le"] = field_obj.max_value
        if hasattr(field_obj, "max_length") and field_obj.max_length is not None:
            pydantic_kwargs["max_length"] = field_obj.max_length

        if is_required:
            annotations[field_name] = python_type
            fields_dict[field_name] = PydanticField(**pydantic_kwargs)
        else:
            default = getattr(field_obj, "default", None)
            if callable(default):
                default = None
            annotations[field_name] = python_type | None
            fields_dict[field_name] = PydanticField(default=default, **pydantic_kwargs)

    if not fields_dict:
        from ai_tools.base import EmptyInput

        return EmptyInput

    model = type(
        f"Auto_{serializer_cls.__name__}",
        (PydanticBaseModel,),
        {"__annotations__": annotations, **fields_dict},
    )
    return model


def _extract_serializer_from_validated_request(action_method):
    """Pull the serializer class out of a @validated_request wrapper's closure.

    `@validated_request(request_serializer=X)` produces a `wrapper(*args, **kwargs)`
    closure that captures `X` (and optionally `query_serializer=Y`) as free variables.
    Inspecting `wrapper.__closure__` lets us recover those classes without the
    bridge config repeating them.

    Some wrapper shapes (tfc.utils.api_contracts.validated_request) capture the
    serializer one level down — the wrapper closes over a `prepare_request`
    helper whose OWN closure holds the serializer. When the direct closure has
    no serializer, descend exactly one level into closed-over functions.
    """
    from rest_framework.serializers import SerializerMetaclass

    if not getattr(action_method, "__closure__", None):
        return None

    serializers_in_closure = []
    nested_functions = []
    for cell in action_method.__closure__:
        try:
            val = cell.cell_contents
        except ValueError:
            continue
        if isinstance(val, SerializerMetaclass):
            serializers_in_closure.append(val)
        elif callable(val) and getattr(val, "__closure__", None):
            nested_functions.append(val)

    if not serializers_in_closure:
        for fn in nested_functions:
            for cell in fn.__closure__:
                try:
                    val = cell.cell_contents
                except ValueError:
                    continue
                if isinstance(val, SerializerMetaclass):
                    serializers_in_closure.append(val)
            if serializers_in_closure:
                break

    if not serializers_in_closure:
        return None
    return serializers_in_closure[0]


def _get_action_serializer(
    viewset_cls,
    action_name: str,
    serializer_override: str = None,
    method: str = None,
    allow_class_fallback: bool = True,
):
    """Find the serializer for an action.

    Resolution order:
      1. Explicit `serializer` config key in @expose_to_mcp.
      2. drf-yasg `_swagger_auto_schema` metadata on the method
         (the dict form attached by @swagger_auto_schema directly).
      3. Closure inspection of @validated_request — pulls the
         SerializerMetaclass from the wrapper's free variables.
      4. Fall back to viewset.serializer_class — UNLESS
         `allow_class_fallback=False` (custom @actions must never inherit
         the create-shaped model serializer as their input schema).

    For APIViews the handler is named by HTTP verb (`.post`/`.get`), not by
    action (`create`/`list`), so when no action-named attribute exists we fall
    back to the verb handler (via `method`) for steps 2–3 — otherwise a
    write APIView's request_serializer is never found and the tool gets 0
    params.
    """
    if serializer_override:
        module_path = viewset_cls.__module__.rsplit(".", 1)[0]
        serializer_module = module_path.replace(".views", ".serializers")
        # Serializers usually live in the submodule that mirrors the view's
        # module name (e.g. simulate.views.agent_definition ->
        # simulate.serializers.agent_definition), so try that first — that's
        # where request serializers like AgentDefinitionSerializer live.
        view_submodule = viewset_cls.__module__.rsplit(".", 1)[-1]
        try:
            return _resolve_class(f"{serializer_module}.{serializer_override}")
        except (ImportError, AttributeError):
            for base_module in [
                f"{serializer_module}.{view_submodule}",
                f"{serializer_module}.project",
                f"{serializer_module}.dataset",
                f"{serializer_module}.user",
                f"{serializer_module}.workspace",
                f"{serializer_module}.prompt_template",
                serializer_module,
            ]:
                try:
                    return _resolve_class(f"{base_module}.{serializer_override}")
                except (ImportError, AttributeError):
                    continue

    action_method = getattr(viewset_cls, action_name, None)
    if action_method is None and method:
        # APIView: no attribute named after the action (create/list/execute);
        # the real handler is the verb method (.post/.get/.put). Use it so the
        # @validated_request / @swagger_auto_schema request serializer resolves.
        action_method = getattr(viewset_cls, method.lower(), None)
    if action_method:
        swagger_data = getattr(action_method, "_swagger_auto_schema", None)
        if isinstance(swagger_data, dict):
            # On @action methods drf-yasg stores the data METHOD-KEYED
            # ({"post": {...}}) instead of flat — unwrap by HTTP method.
            for candidate in (
                swagger_data,
                swagger_data.get((method or "").lower())
                if isinstance(swagger_data.get((method or "").lower()), dict)
                else None,
            ):
                if not isinstance(candidate, dict):
                    continue
                request_body = candidate.get("request_body")
                if isinstance(request_body, type):
                    return request_body
                query_serializer = candidate.get("query_serializer")
                if isinstance(query_serializer, type):
                    return query_serializer

        from_closure = _extract_serializer_from_validated_request(action_method)
        if from_closure is not None:
            return from_closure

    if not allow_class_fallback:
        return None
    return getattr(viewset_cls, "serializer_class", None)


class DRFBridgeTool(BaseTool):
    binding: ClassVar[ViewSetBinding]

    def execute(self, params: PydanticBaseModel, context: ToolContext) -> ToolResult:
        param_dict = params.model_dump(exclude_none=True)

        method = self.binding.method.upper()
        viewset_cls = _resolve_class(self.binding.viewset_class)
        is_apiview = _is_apiview_class(viewset_cls)
        kwargs = {}

        if self.binding.detail and self.binding.pk_field:
            pk_value = param_dict.pop(self.binding.pk_field, None)
            if not pk_value:
                return ToolResult.validation_error(
                    f"'{self.binding.pk_field}' is required for this action."
                )
            pk_value = str(pk_value)
            if is_apiview:
                # APIView handlers take the id as a named URL kwarg whose name
                # is view-specific (e.g. call_execution_id). Use the configured
                # pk_kwarg, else fall back to "pk".
                kwargs[self.binding.pk_kwarg or "pk"] = pk_value
            elif self.binding.pk_kwarg:
                # Custom ModelViewSet @action whose handler takes a named URL
                # kwarg (e.g. assign_items(request, queue_id=...)). Route ONLY to
                # that kwarg — the action signature won't accept a bare `pk`.
                kwargs[self.binding.pk_kwarg] = pk_value
            else:
                # Standard ModelViewSet CRUD via get_object(), which reads
                # self.kwargs[lookup_url_kwarg or lookup_field]. Set every
                # plausible name so retrieve/update/destroy resolve regardless
                # of the viewset's lookup config.
                lookup_field = getattr(viewset_cls, "lookup_field", "pk") or "pk"
                lookup_kwarg = (
                    getattr(viewset_cls, "lookup_url_kwarg", None) or lookup_field
                )
                keys = {"pk"} | {
                    k for k in (lookup_field, lookup_kwarg) if isinstance(k, str)
                }
                for key in keys:
                    kwargs[key] = pk_value

        # Extra URL path kwargs (A5) — required, routed into view.kwargs in
        # addition to the pk routing above (e.g. queue_id alongside pk).
        for kw_name in self.binding.path_kwargs or {}:
            kw_value = param_dict.pop(kw_name, None)
            if not kw_value:
                return ToolResult.validation_error(
                    f"'{kw_name}' is required for this action."
                )
            kwargs[kw_name] = str(kw_value)

        extra_query: dict = {}
        if method == "GET":
            # TH-4667: advertised universal list params may map to a
            # differently-named query param the view actually honors
            # (e.g. search -> 'name' on DatasetView, page_size -> 'limit'
            # for PageNumberPagination subclasses).
            cmap = getattr(self.binding, "collection_param_map", None) or {}
            for advertised, actual in cmap.items():
                if actual != advertised and advertised in param_dict:
                    param_dict[actual] = param_dict.pop(advertised)
            # Same honesty fix for hand-declared query_params: an entry may
            # declare {"actual": "limit"} when the LLM-facing name differs
            # from the query param the view reads (keeps the advertised
            # vocabulary consistent across list tools).
            qp_config = self.binding.query_params or {}
            for advertised in list(param_dict):
                entry = qp_config.get(advertised)
                if isinstance(entry, dict):
                    actual = entry.get("actual")
                    if actual and actual != advertised:
                        param_dict[actual] = param_dict.pop(advertised)
            query_params = param_dict
            body_data = {}
        else:
            # Mixed query/body routing (A6): a query_params entry may declare
            # "in": "query" to land on the query string even on writes (for
            # handlers that read request.query_params on POST/PUT). Default
            # routing (everything in the body) is unchanged.
            qp_config = self.binding.query_params or {}
            body_data = {}
            for k, v in param_dict.items():
                entry = qp_config.get(k)
                if isinstance(entry, dict) and entry.get("in") == "query":
                    extra_query[k] = v
                else:
                    body_data[k] = v
            query_params = {}

        request = _build_drf_request(
            method, body_data, query_params, context, extra_query=extra_query
        )
        view = _instantiate_view(
            viewset_cls, self.binding.action, method, request, kwargs
        )

        # Phase 3B: the bridge never calls dispatch(), so DRF's
        # check_permissions would otherwise be skipped entirely — evaluate
        # the view's real permission_classes (plus the authenticated-user
        # floor) against the synthetic request BEFORE the action runs.
        # Object-level perms still flow through get_object() ->
        # check_object_permissions inside the handler.
        from ai_tools.authz import enforce_view_permissions

        denied = enforce_view_permissions(view, request, self.name)
        if denied is not None:
            return denied

        try:
            if is_apiview:
                action_method = _resolve_apiview_handler(
                    view, method, self.binding.action
                )
                if action_method is None:
                    return ToolResult.error(
                        f"APIView {viewset_cls.__name__} has no handler for "
                        f"method '{method}' or action '{self.binding.action}'."
                    )
            else:
                action_method = getattr(view, self.binding.action)
            response = action_method(request, **kwargs)
        except Exception as e:
            from ai_tools.error_codes import code_from_exception

            logger.exception("drf_bridge_call_failed", tool=self.name)
            return ToolResult.error(
                f"ViewSet call failed: {e}",
                error_code=code_from_exception(e),
            )

        result_data, is_error = _unwrap_response(response)

        if is_error:
            msg = str(result_data) if result_data else "Request failed"
            return ToolResult.error(
                msg,
                error_code=_status_to_error_code(
                    getattr(response, "status_code", 500), msg
                ),
            )

        content = _format_result_for_llm(result_data, self.binding.action)

        # F1 scope-honesty: a list/count tool can attach a SCOPE LABEL that
        # states exactly what its total reflects (which filters were applied,
        # and that it is workspace-scoped), so the model can't echo a
        # cross-scope number (e.g. "47 total" -> "47 observe projects").
        if self.binding.result_scope is not None:
            try:
                scope_label = self.binding.result_scope(param_dict, result_data)
            except Exception:
                logger.exception("result_scope_label_failed", tool=self.name)
                scope_label = None
            if scope_label:
                content = f"{scope_label}\n\n{content}"

        return ToolResult(
            content=section(self.description, content),
            data=result_data
            if isinstance(result_data, dict)
            else {"result": result_data},
        )


def _find_list_tool_for_viewset(viewset_path: str) -> str | None:
    """Look up an already-registered list-style tool on the same ViewSet.

    Returns the tool name (e.g. 'list_projects') for the LLM to call first,
    so detail/destroy actions can advertise where to obtain a valid ID.
    """
    from ai_tools.registry import registry

    for tool in registry.list_all():
        binding = getattr(tool, "binding", None)
        if not binding:
            continue
        if binding.viewset_class != viewset_path:
            continue
        if binding.action == "list" or binding.method == "GET" and not binding.detail:
            return tool.name
    return None


UNIVERSAL_LIST_PARAMS = ("search", "page", "page_size")


def _resolve_query_serializer(viewset_cls, action_name: str, method: str | None):
    """Find the drf-yasg/`@validated_request` QUERY serializer for an action.

    `@validated_request(query_serializer=X)` (and bare `@swagger_auto_schema`)
    attach a `_swagger_auto_schema` dict to the handler — flat on APIView verb
    methods, method-keyed on @action methods. The query serializer is the
    endpoint's validated query-param contract, so its field names are the
    ground truth for which list params the view honors.
    """
    handler = getattr(viewset_cls, action_name, None)
    if handler is None and method:
        handler = getattr(viewset_cls, method.lower(), None)
    if handler is None:
        return None
    swagger_data = getattr(handler, "_swagger_auto_schema", None)
    if not isinstance(swagger_data, dict):
        return None
    candidates = [swagger_data]
    nested = swagger_data.get((method or "").lower())
    if isinstance(nested, dict):
        candidates.append(nested)
    for candidate in candidates:
        query_serializer = candidate.get("query_serializer")
        if isinstance(query_serializer, type):
            return query_serializer
    return None


def _detect_collection_params(
    viewset_cls, action_name: str, method: str | None, tool_config: dict
) -> dict[str, str]:
    """TH-4667: decide which universal list params the view ACTUALLY honors.

    The bridge used to advertise `search`/`page`/`page_size` on every
    GET-collection tool as "universally safe" — but DRF views without
    SearchFilter (or a custom handler) silently IGNORE unknown query params,
    so the model believed it had name-filtered when it had not, then made
    absence/presence claims off unfiltered data. This function is the
    registration-time honesty gate: a param is only advertised when a
    concrete, honored view input is detected (or explicitly documented).

    Returns {advertised_name: actual_query_param} for the subset of
    UNIVERSAL_LIST_PARAMS the view honors. Detection order:

      1. A resolvable QUERY serializer (`@validated_request(query_serializer=…)`)
         defines the contract EXCLUSIVELY — strict serializers reject unknown
         params, so nothing outside its fields may be advertised.
         search -> 'search' field; page -> 'page'; page_size -> 'page_size'
         or 'limit' field.
      2. Otherwise, native DRF machinery, but only when the handler actually
         runs it (DRF mixin handler, `super().list(`, or a source-visible
         `filter_queryset`/`paginate_queryset` call):
           - SearchFilter in filter_backends + non-empty search_fields
             -> search (backend's `search_param`).
           - pagination_class -> page (`page_query_param`) and page_size
             (`page_size_query_param`, only if the paginator declares one).
      3. Explicit declaration for documented custom handlers, merged LAST
         (wins over detection; a None value removes a param):
           - `mcp_list_params` attribute on the ViewSet, or
           - `"list_params"` in the @expose_to_mcp tool config.
         e.g. {"search": "search_text"} — the tool still advertises `search`;
         execute() remaps it to the honored name.
    """
    detected: dict[str, str] = {}

    handler = getattr(viewset_cls, action_name, None)
    if handler is None and method:
        handler = getattr(viewset_cls, method.lower(), None)
    handler_module = getattr(handler, "__module__", "") or ""
    is_drf_default = handler_module.startswith("rest_framework")
    handler_src = ""
    if handler is not None and not is_drf_default:
        try:
            handler_src = inspect.getsource(handler)
        except (OSError, TypeError):
            handler_src = ""
    runs_filter_backends = (
        is_drf_default
        or "super().list(" in handler_src
        or "filter_queryset" in handler_src
    )
    runs_paginator = (
        is_drf_default
        or "super().list(" in handler_src
        or "paginate_queryset" in handler_src
    )

    query_serializer = _resolve_query_serializer(viewset_cls, action_name, method)
    if query_serializer is not None:
        try:
            serializer_fields = query_serializer().fields
        except Exception:
            serializer_fields = {}
        if "search" in serializer_fields:
            detected["search"] = "search"
        if "page" in serializer_fields:
            detected["page"] = "page"
        if "page_size" in serializer_fields:
            detected["page_size"] = "page_size"
        elif "limit" in serializer_fields:
            detected["page_size"] = "limit"
    else:
        backends = getattr(viewset_cls, "filter_backends", None) or []
        search_fields = getattr(viewset_cls, "search_fields", None) or []
        if search_fields and runs_filter_backends:
            try:
                from rest_framework.filters import SearchFilter

                for backend in backends:
                    if isinstance(backend, type) and issubclass(
                        backend, SearchFilter
                    ):
                        detected["search"] = (
                            getattr(backend, "search_param", None) or "search"
                        )
                        break
            except ImportError:
                pass
        if runs_paginator:
            paginator = getattr(viewset_cls, "pagination_class", None)
            if paginator is not None:
                page_param = getattr(paginator, "page_query_param", None)
                if page_param:
                    detected["page"] = page_param
                size_param = getattr(paginator, "page_size_query_param", None)
                if size_param:
                    detected["page_size"] = size_param

    for declared in (
        getattr(viewset_cls, "mcp_list_params", None),
        tool_config.get("list_params"),
    ):
        if not declared:
            continue
        for advertised, actual in declared.items():
            if advertised not in UNIVERSAL_LIST_PARAMS:
                raise ValueError(
                    f"list_params/mcp_list_params on "
                    f"{viewset_cls.__module__}.{viewset_cls.__name__}: "
                    f"'{advertised}' is not a universal list param "
                    f"({'/'.join(UNIVERSAL_LIST_PARAMS)})."
                )
            if actual is None:
                detected.pop(advertised, None)
            elif isinstance(actual, str):
                detected[advertised] = actual
            else:
                raise ValueError(
                    f"list_params/mcp_list_params on "
                    f"{viewset_cls.__module__}.{viewset_cls.__name__}: value "
                    f"for '{advertised}' must be a query-param name or None."
                )
    return detected


def _register_bridge_tool(
    viewset_path: str,
    viewset_cls,
    action_name: str,
    tool_config: dict,
    category: str,
    verb_map: dict | None = None,
):
    """Build and register a single bridge tool from config."""
    from ai_tools.registry import registry

    # --- A1: derive method/detail from DRF @action metadata ----------------
    # @action sets .mapping ({http_method: handler_name}) and .detail on the
    # decorated function. Precedence: explicit config > derived > static maps.
    action_fn = getattr(viewset_cls, action_name, None)
    drf_mapping = getattr(action_fn, "mapping", None)
    derived_method = None
    derived_detail = None
    if drf_mapping:
        try:
            derived_method = next(iter(drf_mapping)).upper()
            derived_detail = bool(getattr(action_fn, "detail", False))
        except (TypeError, StopIteration):
            derived_method = None
            derived_detail = None

    # Custom action = not a CRUD action key and not an APIView verb key.
    is_custom_action = (
        action_name not in ACTION_METHOD_MAP and action_name not in HTTP_VERB_KEYS
    )

    # --- A2: registration-time validation ----------------------------------
    # On bare APIViews a non-attribute action key is a deliberate alias:
    # _resolve_apiview_handler treats it as a hint and falls back to the verb
    # handler (e.g. RunTestExecutionView 'execute' -> .post). Allow it when
    # that verb handler actually exists for the configured/derived method.
    _config_method = (
        tool_config.get("method")
        or derived_method
        or ACTION_METHOD_MAP.get(action_name, "GET")
    )
    _apiview_verb_fallback_ok = _is_apiview_class(viewset_cls) and callable(
        getattr(viewset_cls, _config_method.lower(), None)
    )
    if (
        action_name not in STANDARD_CRUD
        and action_name not in HTTP_VERB_KEYS
        and not hasattr(viewset_cls, action_name)
        and not _apiview_verb_fallback_ok
    ):
        raise ValueError(
            f"@expose_to_mcp on {viewset_path}: tools key '{action_name}' is "
            f"not standard CRUD ({'/'.join(STANDARD_CRUD)}), not an HTTP verb "
            f"(get/post/put/patch/delete), not an attribute on the class, and "
            f"the class has no '{_config_method.lower()}' verb handler to fall "
            f"back to — probably a typo."
        )
    if is_custom_action and not tool_config.get("name"):
        raise ValueError(
            f"@expose_to_mcp on {viewset_path}: custom action '{action_name}' "
            f"requires an explicit 'name'. Use the {{verb}}_{{noun_phrase}} "
            f"convention with a search_tools verb (list/get/create/update/"
            f"delete/run/execute/export/assign/save/compare/generate/analyze/"
            f"stop/restore/duplicate/complete/skip/review/preview/import)."
        )

    detail_default = (
        derived_detail
        if derived_detail is not None
        else action_name in DETAIL_ACTIONS
    )
    detail = tool_config.get("detail", detail_default)
    entity_name = tool_config.get("entity") or _derive_entity_name(viewset_cls)
    tool_name = tool_config.get("name") or _derive_tool_name(
        action_name, entity_name, detail, verb_map=verb_map
    )
    method = (
        tool_config.get("method")
        or derived_method
        or ACTION_METHOD_MAP.get(action_name, "GET")
    )
    pk_field = tool_config.get("pk_field", "id" if detail else None)
    serializer_override = tool_config.get("serializer")
    query_params = tool_config.get("query_params")
    path_kwargs = tool_config.get("path_kwargs")
    if isinstance(path_kwargs, (list, tuple)):
        path_kwargs = {kw: {} for kw in path_kwargs}
    include_fields = tool_config.get("include_fields")
    exclude_fields = tool_config.get("exclude_fields")
    id_source = tool_config.get("id_source")

    # A3: resolve the serializer ONCE, strictly for custom actions (the
    # viewset's create-shaped serializer_class must never leak into a custom
    # action's input schema or description).
    resolved_serializer = _get_action_serializer(
        viewset_cls,
        action_name,
        serializer_override,
        method=method,
        allow_class_fallback=not is_custom_action,
    )

    # A2: a write-shaped custom action with no resolvable input schema would
    # register a tool that sends an empty body — refuse it up front.
    if (
        is_custom_action
        and method in WRITE_METHODS
        and resolved_serializer is None
        and query_params is None
    ):
        raise ValueError(
            f"@expose_to_mcp on {viewset_path}: write action '{action_name}' "
            f"({method}) has no resolvable serializer (no 'serializer' config, "
            f"no @swagger_auto_schema request_body, no @validated_request) and "
            f"no 'query_params'. Declare one of them (query_params={{}} for a "
            f"deliberately body-less detail action)."
        )

    # A4: description precedence — config override, then the custom action
    # function's docstring first paragraph, then serializer/ViewSet-derived.
    description = tool_config.get("description")
    if not description and is_custom_action:
        description = _first_docstring_paragraph(getattr(action_fn, "__doc__", None))
    if not description:
        description = _derive_description(
            viewset_cls, action_name, entity_name, resolved_serializer
        )

    # TH-4667: only set for the auto-built GET-collection branch below.
    collection_param_map: dict | None = None

    if query_params:
        annotations = {}
        fields_dict = {}
        # Detail actions still need their pk/id input even when query_params are
        # declared — execute() pops it and routes it to the URL kwarg, then the
        # remaining params become query-string params. Without this, a detail +
        # query_params tool (e.g. a CSV export keyed by id with a required
        # `type` filter) would lose its id field entirely (TH-5386).
        if detail and pk_field:
            annotations[pk_field] = str
            _qp_id_desc = (
                f"UUID of the {entity_name} to {action_name} (UUID v4 format)."
            )
            _qp_id_hint = tool_config.get("id_source") or _find_list_tool_for_viewset(
                viewset_path
            )
            if _qp_id_hint:
                _qp_id_desc += (
                    f" **How to get it:** call `{_qp_id_hint}` first and copy the 'id'."
                )
            fields_dict[pk_field] = PydanticField(description=_qp_id_desc)
        for param_name, param_info in query_params.items():
            if isinstance(param_info, str):
                param_info = {"type": str, "description": param_info, "required": False}
            python_type = param_info.get("type", str)
            is_required = param_info.get("required", False)
            desc = param_info.get("description", param_name.replace("_", " ").title())
            default = param_info.get("default")

            if is_required:
                annotations[param_name] = python_type
                fields_dict[param_name] = PydanticField(description=desc)
            else:
                annotations[param_name] = python_type | None
                fields_dict[param_name] = PydanticField(
                    default=default, description=desc
                )

        input_model = type(
            f"Input_{tool_name}",
            (PydanticBaseModel,),
            {"__annotations__": annotations, **fields_dict},
        )
    elif (
        action_name in LIST_ACTIONS or (method == "GET" and not detail)
    ) and not query_params:
        # List/GET-collection actions take OPTIONAL filter/pagination params,
        # never the create-shaped model serializer (which has required fields).
        # TH-4667: a param is only advertised when the view DETECTABLY honors
        # it (see _detect_collection_params) — the old behavior advertised
        # search/page/page_size on every list tool, and views without
        # SearchFilter silently ignored them, so the model believed it had
        # filtered when it had not. extra="forbid" makes a non-advertised
        # param a loud VALIDATION_ERROR (with a schema hint) instead of a
        # silent no-op.
        collection_param_map = _detect_collection_params(
            viewset_cls, action_name, method, tool_config
        )
        from pydantic import ConfigDict

        _list_annotations: dict = {}
        _list_fields: dict = {}
        if "search" in collection_param_map:
            _list_annotations["search"] = str | None
            _list_fields["search"] = PydanticField(
                default=None,
                description="Optional case-insensitive name/text filter.",
            )
        if "page" in collection_param_map:
            _list_annotations["page"] = int | None
            _list_fields["page"] = PydanticField(
                default=None, description="Optional 1-indexed page number."
            )
        if "page_size" in collection_param_map:
            _list_annotations["page_size"] = int | None
            _list_fields["page_size"] = PydanticField(
                default=None,
                description="Optional page size (number of items to return).",
            )
        input_model = type(
            f"Input_{tool_name}",
            (PydanticBaseModel,),
            {
                "__annotations__": _list_annotations,
                **_list_fields,
                "model_config": ConfigDict(extra="forbid"),
            },
        )
    elif detail and (
        action_name in ("retrieve", "destroy")
        # A3: custom detail GET @actions with no explicit schema get the same
        # pk-only input (previously inexpressible — query_params={} is falsy).
        or (is_custom_action and method == "GET" and resolved_serializer is None)
    ):
        list_tool_hint = tool_config.get("id_source")
        if not list_tool_hint:
            list_tool_hint = _find_list_tool_for_viewset(viewset_path)

        id_description = (
            f"UUID of the {entity_name} to {action_name}. UUID v4 format, "
            f"e.g. '550e8400-e29b-41d4-a716-446655440000'."
        )
        if list_tool_hint:
            id_description += (
                f" **How to get it:** call `{list_tool_hint}` first to discover "
                f"available {entity_name}s and copy the 'id' field. "
                f"Do NOT pass the {entity_name} name here — the API requires the UUID."
            )

        # retrieve/destroy keep the historical literal "id" field; custom
        # detail GETs honor their configured pk_field (e.g. item_id).
        _pk_input_name = (
            "id" if action_name in ("retrieve", "destroy") else (pk_field or "id")
        )
        input_model = type(
            f"Input_{tool_name}",
            (PydanticBaseModel,),
            {
                "__annotations__": {_pk_input_name: str},
                _pk_input_name: PydanticField(description=id_description),
            },
        )
    else:
        if resolved_serializer:
            input_model = _serializer_to_pydantic(
                resolved_serializer,
                include_fields=include_fields,
                exclude_fields=exclude_fields,
            )
        else:
            from ai_tools.base import EmptyInput

            input_model = EmptyInput

    # Detail actions need the record id in their input. The retrieve/destroy
    # branch already adds it; serializer-based detail actions (update + custom
    # detail @actions like submit) otherwise expose no id field, leaving
    # pk_field required-but-unsettable — so the tool can't target a record.
    # Inject the pk_field so callers can identify which record to act on.
    if (
        detail
        and pk_field
        and pk_field not in (getattr(input_model, "model_fields", {}) or {})
    ):
        _id_hint = tool_config.get("id_source") or _find_list_tool_for_viewset(
            viewset_path
        )
        _id_desc = f"UUID of the {entity_name} to {action_name}. UUID v4 format."
        if _id_hint:
            _id_desc += f" **How to get it:** call `{_id_hint}` and copy the 'id'."
        input_model = type(
            f"Input_{tool_name}",
            (input_model,),
            {
                "__annotations__": {pk_field: str},
                pk_field: PydanticField(description=_id_desc),
            },
        )

    # A5: every declared path kwarg becomes a REQUIRED str input field (with
    # the same id-hint mechanics as pk fields) and is routed into view.kwargs
    # by execute() in addition to the pk routing.
    if path_kwargs:
        kw_annotations = {}
        kw_fields = {}
        existing_fields = getattr(input_model, "model_fields", {}) or {}
        for kw_name, kw_info in path_kwargs.items():
            if kw_name in existing_fields or kw_name in kw_annotations:
                continue
            kw_info = kw_info or {}
            kw_desc = kw_info.get("description") or (
                f"'{kw_name}' URL path parameter (UUID) this action requires."
            )
            kw_hint = kw_info.get("id_source")
            if kw_hint:
                kw_desc += f" **How to get it:** call `{kw_hint}` and copy the 'id'."
            kw_annotations[kw_name] = str
            kw_fields[kw_name] = PydanticField(description=kw_desc)
        if kw_annotations:
            input_model = type(
                f"Input_{tool_name}",
                (input_model,),
                {"__annotations__": kw_annotations, **kw_fields},
            )

    binding = ViewSetBinding(
        viewset_class=viewset_path,
        action=action_name,
        method=method,
        detail=detail,
        pk_field=pk_field if detail else None,
        serializer_override=serializer_override,
        query_params=query_params,
        pk_kwarg=tool_config.get("pk_kwarg"),
        path_kwargs=path_kwargs,
        id_source=id_source,
        collection_param_map=collection_param_map,
        result_scope=tool_config.get("result_scope"),
    )

    # Phase 3A: classify the tool read|mutate|destructive. Config override
    # ("execution_policy") wins; otherwise derived from action/method/name.
    # Destructive tools are gated behind the server-held confirmation in
    # BaseTool.run (ai_tools/confirmations.py).
    execution_policy = classify_execution_policy(
        tool_name,
        action=action_name,
        method=method,
        override=tool_config.get("execution_policy"),
    )

    tool_cls = type(
        f"DRFBridge_{tool_name}",
        (DRFBridgeTool,),
        {
            "name": tool_name,
            "description": description,
            "category": category,
            "input_model": input_model,
            "binding": binding,
            "execution_policy": execution_policy,
            "undo_note": tool_config.get("undo_note"),
            "undo_prompt": tool_config.get("undo_prompt"),
            "exec_timeout": tool_config.get("exec_timeout"),
        },
    )

    # Phase 3A: per-tool confirmation-preview builder (design §1.9).
    if tool_config.get("confirm_preview") is not None:
        from ai_tools import confirmations as _confirmations

        _confirmations.register_preview_builder(
            tool_name, tool_config["confirm_preview"]
        )

    instance = tool_cls()
    registry.register(instance)
    logger.debug(
        "bridge_tool_registered",
        tool=tool_name,
        viewset=viewset_path,
        action=action_name,
    )
    return instance


STANDARD_CRUD = ("list", "retrieve", "create", "update", "destroy")


def expose_to_mcp(category: str, tools=None, verb_map: dict | None = None):
    """Class decorator that registers ViewSet actions as MCP/Falcon tools.

    The `tools` argument accepts three forms:

      1. None — auto-expose the standard CRUD actions
         (list/retrieve/create/update/destroy). Tool names are derived
         from the action verb + serializer's entity name.

         @expose_to_mcp(category="prompts")(PromptTemplateViewSet)

      2. List of action names — same as None but lets you cherry-pick
         which actions to expose.

         @expose_to_mcp(category="prompts", tools=["list", "retrieve"])
         class PromptTemplateViewSet(...):

      3. Dict of action → config — full control. Each config can override
         name, description, query_params, serializer, include_fields,
         exclude_fields, method, detail, pk_field, pk_kwarg, entity,
         id_source, path_kwargs, execution_policy (read|mutate|destructive;
         normally auto-classified — see ai_tools/confirmations.py),
         undo_note (confirmation-preview note for cheap compensations),
         undo_prompt (str.format template over the validated args, rendered
         as data["undo"]["prompt"] on the executed destructive leg),
         confirm_preview (callable(params: dict, context) -> str preview
         builder for the confirmation card; read-only, workspace-scoped),
         and list_params (TH-4667 — GET-collection actions only: declares
         which universal params a CUSTOM handler honors and under what
         query-param name, e.g. {"search": "search_text"}; merged over
         auto-detection, None removes a param. The equivalent
         `mcp_list_params` class attribute on the ViewSet covers
         bare/auto-CRUD registrations. See _detect_collection_params).

         Custom @actions: method/detail are auto-derived from the DRF
         @action decorator (config still wins); an explicit "name" is
         REQUIRED; write actions need a resolvable serializer or
         query_params. `path_kwargs` (list or {name: {description,
         id_source}}) adds extra required URL kwargs alongside pk routing.
         A query_params entry may declare "in": "query" to land on the
         query string even on POST/PUT/PATCH/DELETE. A GET entry may
         declare "actual": "<param>" when the view reads a different
         query-param name than the advertised one (TH-4667 — e.g.
         page_size entries with "actual": "limit" for
         PageNumberPagination-backed views).

         @expose_to_mcp(category="tracing", tools={
             "list": {"query_params": {...}},
             "update_project_name": {"serializer": "ProjectNameUpdateSerializer"},
         })

    Per-tool config keys are all optional. The bridge auto-derives:
      - tool name from {verb}_{entity}[s]
      - tool description from {verb} {entity}. + serializer.__doc__
      - input schema from serializer fields (skipping read_only)
      - method from action name
      - detail flag for retrieve/update/destroy
      - the `id` field's "How to get it" hint pointing at the list tool
    """

    def decorator(viewset_cls):
        viewset_path = f"{viewset_cls.__module__}.{viewset_cls.__name__}"

        if tools is None:
            tool_iter = [(a, {}) for a in STANDARD_CRUD]
        elif isinstance(tools, list):
            tool_iter = [(a, {}) for a in tools]
        else:
            tool_iter = list(tools.items())

        ordered_actions = sorted(
            tool_iter,
            key=lambda kv: (
                0
                if kv[0] in LIST_ACTIONS or kv[0] == "get"
                else (1 if kv[0] == "create" else 2)
            ),
        )
        for action_name, tool_config in ordered_actions:
            if isinstance(tool_config, str):
                tool_config = {"name": tool_config}
            try:
                _register_bridge_tool(
                    viewset_path,
                    viewset_cls,
                    action_name,
                    tool_config,
                    category,
                    verb_map=verb_map,
                )
            except Exception:
                logger.exception(
                    "bridge_registration_failed",
                    action=action_name,
                    viewset=viewset_path,
                )
                # A8: fail LOUD under pytest / DEBUG / AI_TOOLS_STRICT_BRIDGE=1
                # so a broken bridge can't ship silently; prod keeps the
                # historical swallow+log behavior.
                if _strict_registration():
                    raise
        return viewset_cls

    return decorator


def viewset_tool(
    name: str,
    description: str,
    category: str,
    viewset_class: str,
    action: str,
    method: str = "GET",
    detail: bool = False,
    pk_field: str | None = None,
):
    """Decorator that turns a Pydantic input model into a registered DRF bridge tool."""

    def decorator(input_model_cls: type[PydanticBaseModel]):
        binding = ViewSetBinding(
            viewset_class=viewset_class,
            action=action,
            method=method,
            detail=detail,
            pk_field=pk_field,
        )

        tool_cls = type(
            f"DRFBridge_{name}",
            (DRFBridgeTool,),
            {
                "name": name,
                "description": description,
                "category": category,
                "input_model": input_model_cls,
                "binding": binding,
                # Phase 3A: same classification as _register_bridge_tool.
                "execution_policy": classify_execution_policy(
                    name, action=action, method=method
                ),
            },
        )

        instance = tool_cls()
        from ai_tools.registry import registry

        registry.register(instance)

        return input_model_cls

    return decorator
