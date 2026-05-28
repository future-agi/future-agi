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
import logging
from dataclasses import dataclass
from typing import Any, ClassVar

from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field as PydanticField

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import key_value_block, markdown_table, section

try:
    import structlog

    logger = structlog.get_logger(__name__)
except ImportError:
    logger = logging.getLogger(__name__)

# Registry of pending expose_to_mcp registrations (processed at app ready)
_pending_registrations: list[tuple] = []

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


def _resolve_class(dotted_path: str):
    module_path, class_name = dotted_path.rsplit(".", 1)
    module = importlib.import_module(module_path)
    return getattr(module, class_name)


def _build_drf_request(
    method: str, data: dict, query_params: dict, context: ToolContext
):
    from rest_framework.parsers import JSONParser
    from rest_framework.request import Request
    from rest_framework.test import APIRequestFactory

    factory = APIRequestFactory()

    if method.upper() in ("GET", "HEAD", "OPTIONS"):
        django_request = factory.get("/bridge/", data=query_params)
    elif method.upper() == "DELETE":
        django_request = factory.delete("/bridge/", data=data, format="json")
    elif method.upper() in ("PUT", "PATCH"):
        fn = factory.put if method.upper() == "PUT" else factory.patch
        django_request = fn("/bridge/", data=data, format="json")
    else:
        django_request = factory.post("/bridge/", data=data, format="json")

    django_request.user = context.user
    django_request.workspace = context.workspace
    django_request.organization = context.organization

    drf_request = Request(django_request, parsers=[JSONParser()])
    drf_request.user = context.user
    drf_request.workspace = context.workspace
    drf_request.organization = context.organization

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
            if isinstance(v, (dict, list)):
                continue
            label = k.replace("_", " ").title()
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

        if hasattr(field_obj, "choices") and field_obj.choices:
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
    """
    from rest_framework.serializers import SerializerMetaclass

    if not getattr(action_method, "__closure__", None):
        return None

    serializers_in_closure = []
    for cell in action_method.__closure__:
        try:
            val = cell.cell_contents
        except ValueError:
            continue
        if isinstance(val, SerializerMetaclass):
            serializers_in_closure.append(val)

    if not serializers_in_closure:
        return None
    return serializers_in_closure[0]


def _get_action_serializer(
    viewset_cls, action_name: str, serializer_override: str = None
):
    """Find the serializer for an action.

    Resolution order:
      1. Explicit `serializer` config key in @expose_to_mcp.
      2. drf-yasg `_swagger_auto_schema` metadata on the method
         (the dict form attached by @swagger_auto_schema directly).
      3. Closure inspection of @validated_request — pulls the
         SerializerMetaclass from the wrapper's free variables.
      4. Fall back to viewset.serializer_class.
    """
    if serializer_override:
        module_path = viewset_cls.__module__.rsplit(".", 1)[0]
        serializer_module = module_path.replace(".views", ".serializers")
        try:
            return _resolve_class(f"{serializer_module}.{serializer_override}")
        except (ImportError, AttributeError):
            for base_module in [
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
    if action_method:
        swagger_data = getattr(action_method, "_swagger_auto_schema", None)
        if isinstance(swagger_data, dict):
            request_body = swagger_data.get("request_body")
            if isinstance(request_body, type):
                return request_body
            query_serializer = swagger_data.get("query_serializer")
            if isinstance(query_serializer, type):
                return query_serializer

        from_closure = _extract_serializer_from_validated_request(action_method)
        if from_closure is not None:
            return from_closure

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
            else:
                # ModelViewSet get_object() reads self.kwargs[lookup_url_kwarg
                # or lookup_field]. Set every plausible name so retrieve/update/
                # destroy resolve regardless of the viewset's lookup config.
                lookup_field = getattr(viewset_cls, "lookup_field", "pk") or "pk"
                lookup_kwarg = (
                    getattr(viewset_cls, "lookup_url_kwarg", None) or lookup_field
                )
                keys = {"pk"} | {
                    k for k in (lookup_field, lookup_kwarg) if isinstance(k, str)
                }
                for key in keys:
                    kwargs[key] = pk_value

        if method == "GET":
            query_params = param_dict
            body_data = {}
        else:
            query_params = {}
            body_data = param_dict

        request = _build_drf_request(method, body_data, query_params, context)
        view = _instantiate_view(
            viewset_cls, self.binding.action, method, request, kwargs
        )

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
            logger.exception("drf_bridge_call_failed", tool=self.name)
            return ToolResult.error(f"ViewSet call failed: {e}")

        result_data, is_error = _unwrap_response(response)

        if is_error:
            msg = str(result_data) if result_data else "Request failed"
            return ToolResult.error(msg)

        content = _format_result_for_llm(result_data, self.binding.action)
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

    detail_early = tool_config.get("detail", action_name in DETAIL_ACTIONS)
    entity_early = tool_config.get("entity") or _derive_entity_name(viewset_cls)
    tool_name = tool_config.get("name") or _derive_tool_name(
        action_name, entity_early, detail_early, verb_map=verb_map
    )
    method = tool_config.get("method", ACTION_METHOD_MAP.get(action_name, "GET"))
    detail = tool_config.get("detail", action_name in DETAIL_ACTIONS)
    pk_field = tool_config.get("pk_field", "id" if detail else None)
    serializer_override = tool_config.get("serializer")
    query_params = tool_config.get("query_params")
    include_fields = tool_config.get("include_fields")
    exclude_fields = tool_config.get("exclude_fields")

    entity_name = tool_config.get("entity") or _derive_entity_name(viewset_cls)
    serializer_for_doc = _get_action_serializer(
        viewset_cls, action_name, tool_config.get("serializer")
    )
    description = tool_config.get(
        "description",
    ) or _derive_description(viewset_cls, action_name, entity_name, serializer_for_doc)

    if query_params:
        annotations = {}
        fields_dict = {}
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
        # Default to a small set of universally-safe optional filters so the
        # LLM can call with no args and get the first page.
        input_model = type(
            f"Input_{tool_name}",
            (PydanticBaseModel,),
            {
                "__annotations__": {
                    "search": str | None,
                    "page": int | None,
                    "page_size": int | None,
                },
                "search": PydanticField(
                    default=None,
                    description="Optional case-insensitive name/text filter.",
                ),
                "page": PydanticField(
                    default=None, description="Optional 1-indexed page number."
                ),
                "page_size": PydanticField(
                    default=None,
                    description="Optional page size (number of items to return).",
                ),
            },
        )
    elif detail and action_name in ("retrieve", "destroy"):
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

        input_model = type(
            f"Input_{tool_name}",
            (PydanticBaseModel,),
            {
                "__annotations__": {"id": str},
                "id": PydanticField(description=id_description),
            },
        )
    else:
        serializer_cls = _get_action_serializer(
            viewset_cls, action_name, serializer_override
        )
        if serializer_cls:
            input_model = _serializer_to_pydantic(
                serializer_cls,
                include_fields=include_fields,
                exclude_fields=exclude_fields,
            )
        else:
            from ai_tools.base import EmptyInput

            input_model = EmptyInput

    binding = ViewSetBinding(
        viewset_class=viewset_path,
        action=action_name,
        method=method,
        detail=detail,
        pk_field=pk_field if detail else None,
        serializer_override=serializer_override,
        query_params=query_params,
        pk_kwarg=tool_config.get("pk_kwarg"),
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
        },
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
         exclude_fields, method, detail, pk_field, entity, id_source.

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
            },
        )

        instance = tool_cls()
        from ai_tools.registry import registry

        registry.register(instance)

        return input_model_cls

    return decorator
