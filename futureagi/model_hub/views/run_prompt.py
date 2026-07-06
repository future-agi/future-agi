import base64
import json
import os
import re
import string
import traceback
import uuid
from collections.abc import Collection, Mapping
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import lru_cache
from typing import Any

import chevron
import litellm
import requests
import structlog
import yaml
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.db import close_old_connections
from django.db.models import Q
from django.http import Http404
from drf_yasg.utils import swagger_auto_schema
from jinja2 import StrictUndefined, TemplateSyntaxError, UndefinedError, meta, nodes
from jinja2.sandbox import SandboxedEnvironment
from rest_framework import viewsets
from rest_framework.generics import CreateAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

logger = structlog.get_logger(__name__)
from agentic_eval.core_evals.run_prompt.available_models import AVAILABLE_MODELS

# (available_models always available)
from agentic_eval.core_evals.run_prompt.litellm_models import LiteLLMModelManager
from agentic_eval.core_evals.run_prompt.litellm_response import RunPrompt
from agentic_eval.core_evals.run_prompt.other_services.manager import (
    get_model_parameters,
)
from model_hub.models.api_key import ApiKey
from model_hub.models.choices import (
    CellStatus,
    LiteLlmModelProvider,
    ProviderLogoUrls,
    SourceChoices,
    StatusType,
)
from model_hub.models.custom_models import CustomAIModel
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.models.openai_tools import Tools
from model_hub.models.run_prompt import RunPrompter, UserResponseSchema
from model_hub.queries.tts_voices import get_custom_voices
from model_hub.serializers.contracts import (
    MODEL_HUB_ERROR_RESPONSES,
    DatasetRunPromptStatsResponseSerializer,
    LiteLLMModelVoicesResponseSerializer,
    ModelHubPaginatedResponseSerializer,
    ModelHubStringResultResponseSerializer,
    ModelHubSuccessMessageResponseSerializer,
    ModelParametersResponseSerializer,
    RunPromptColumnConfigResponseSerializer,
    RunPromptForRowsRequestSerializer,
    RunPromptOptionsResponseSerializer,
)
from model_hub.serializers.develop_dataset_contracts import (
    DevelopDatasetMessageResponseSerializer,
    RunPromptColumnPreviewResponseSerializer,
)
from model_hub.serializers.run_prompt import (
    AddRunPromptSerializer,
    ApiKeyListResponseSerializer,
    ApiKeyRequestSerializer,
    ApiKeyResponseSerializer,
    ApiKeySerializer,
    ApiKeySuccessResponseSerializer,
    EditRunPromptColumnSerializer,
    LitellmSerializer,
    PreviewRunPromptSerializer,
)
from model_hub.services.column_service import (
    create_run_prompt_column,
    update_column_for_rerun,
)
from model_hub.utils.model_provider_update import (
    one_time_model_providers_update,
)
from model_hub.utils.utils import (
    get_model_mode,
    remove_empty_text_from_messages,
)
from model_hub.views.prompt_template import handle_media
from model_hub.views.utils.utils import sanitize_uuid_for_jinja
from tfc.telemetry import wrap_for_thread
from tfc.temporal import temporal_activity
from tfc.utils.error_codes import (
    get_error_for_api_status,
    get_error_message,
    get_specific_error_message,
)
from tfc.utils.api_contracts import validated_request
from tfc.utils.functions import get_prompt_stats
from tfc.utils.general_methods import GeneralMethods
from tfc.utils.pagination import ExtendedPageNumberPagination
from tfc.utils.parse_errors import parse_serialized_errors
from tfc.utils.storage import (
    convert_image_from_url_to_base64,
    detect_audio_format,
)
from tfc.constants.api_calls import APICallStatusChoices, APICallTypeChoices

try:
    from ee.usage.utils.usage_entries import log_and_deduct_cost_for_api_request
except ImportError:
    log_and_deduct_cost_for_api_request = None


def _request_organization(request):
    return getattr(request, "organization", None) or request.user.organization


def _request_workspace_filter(request, field_name="workspace"):
    workspace = getattr(request, "workspace", None)
    if not workspace:
        return Q()

    if getattr(workspace, "is_default", False):
        return (
            Q(**{field_name: workspace})
            | Q(
                **{
                    f"{field_name}__is_default": True,
                    f"{field_name}__organization_id": workspace.organization_id,
                }
            )
            | Q(**{f"{field_name}__isnull": True})
        )

    return Q(**{field_name: workspace})


def _request_dataset_queryset(request):
    return Dataset.objects.filter(
        _request_workspace_filter(request),
        organization=_request_organization(request),
        deleted=False,
    )


def _request_column_queryset(request):
    return Column.objects.filter(
        _request_workspace_filter(request, field_name="dataset__workspace"),
        dataset__organization=_request_organization(request),
        deleted=False,
        dataset__deleted=False,
    )


def _request_run_prompter_queryset(request):
    return RunPrompter.objects.filter(
        _request_workspace_filter(request),
        organization=_request_organization(request),
        deleted=False,
    )


def _extract_tool_ids(tools):
    tool_ids = []
    for tool in tools or []:
        if isinstance(tool, dict):
            tool_id = tool.get("id")
        else:
            tool_id = tool
        if tool_id:
            tool_ids.append(tool_id)
    return tool_ids


PROVIDERS_WITH_JSON = ["vertex_ai", "azure", "bedrock", "sagemaker"]

# Re-export for backward compatibility - prefer importing from column_utils directly
from model_hub.utils.column_utils import OUTPUT_FORMAT_TO_DATA_TYPE as DATA_TYPE_MAP
from model_hub.utils.column_utils import (
    get_column_data_type,
)


class ApiKeyViewSet(viewsets.ModelViewSet):
    serializer_class = ApiKeySerializer
    permission_classes = [IsAuthenticated]
    _gm = GeneralMethods()

    # def get_queryset(self):
    #     return ApiKey.objects.filter(organization=getattr(self.request, "organization", None) or self.request.user.organization)

    def get_queryset(self):
        queryset = ApiKey.objects.filter(
            organization=getattr(self.request, "organization", None)
            or self.request.user.organization
        )
        # The decryption will happen automatically through the model's __init__ method
        return queryset

    def _provider_uses_json_config(self, provider):
        return any(
            provider and provider.startswith(json_provider)
            for json_provider in PROVIDERS_WITH_JSON
        )

    def _request_workspace(self, request):
        return getattr(request, "workspace", None)

    def _save_context(self, request):
        context = {
            "organization": getattr(request, "organization", None)
            or request.user.organization
        }
        workspace = self._request_workspace(request)
        if workspace is not None:
            context["workspace"] = workspace
        return context

    def _normalize_provider_key_payload(self, data, instance=None):
        payload = data.copy() if hasattr(data, "copy") else dict(data)
        provider = payload.get("provider") or getattr(instance, "provider", None)

        if not self._provider_uses_json_config(provider):
            if "key" in payload:
                payload["config_json"] = None
            return payload

        has_config_json = "config_json" in payload and payload.get(
            "config_json"
        ) not in (
            None,
            "",
        )
        has_key = "key" in payload and payload.get("key") not in (None, "")

        if not has_config_json and not has_key:
            return payload

        config_json = (
            payload.get("config_json") if has_config_json else payload.get("key")
        )
        if isinstance(config_json, str):
            config_json = json.loads(config_json)

        if not isinstance(config_json, dict) or any(
            isinstance(value, dict) for value in config_json.values()
        ):
            raise ValidationError("Invalid JSON format for config_json")

        payload["config_json"] = config_json
        payload["key"] = None
        return payload

    @swagger_auto_schema(
        responses={200: ApiKeyListResponseSerializer, **MODEL_HUB_ERROR_RESPONSES}
    )
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    @validated_request(
        request_serializer=ApiKeyRequestSerializer,
        responses={200: ApiKeySuccessResponseSerializer, **MODEL_HUB_ERROR_RESPONSES},
    )
    def create(self, request, *args, **kwargs):
        try:
            payload = self._normalize_provider_key_payload(request.data)
        except (json.JSONDecodeError, TypeError, ValidationError):
            return self._gm.bad_request(get_error_message("INVALID_FORMAT"))

        serializer = self.get_serializer(data=payload)
        if serializer.is_valid():
            validated_data = serializer.validated_data

            # First try to get existing API key
            try:
                config_json = validated_data.get("config_json")
                api_key = (
                    self.get_queryset()
                    .filter(
                        provider=validated_data.get("provider"),
                    )
                    .first()
                )
                if api_key:
                    # Update existing key
                    if config_json:
                        api_key.config_json = config_json
                        api_key.key = None
                    else:
                        api_key.key = validated_data.get("key")
                        api_key.config_json = None
                    api_key.user = request.user
                    workspace = self._request_workspace(request)
                    if workspace is not None:
                        api_key.workspace = workspace
                    api_key.save()
                else:
                    # Create new key if not found
                    create_data = {
                        "provider": validated_data.get("provider"),
                        "user": request.user,
                        **self._save_context(request),
                    }
                    if config_json:
                        create_data["config_json"] = config_json
                    else:
                        create_data["key"] = validated_data.get("key")
                    api_key = ApiKey.objects.create(
                        **create_data,
                    )
            except Exception:
                return self._gm.bad_request(get_error_message("UNABLE_TO_ADD_API_KEY"))

            return self._gm.success_response(
                {
                    "id": str(api_key.id),
                    "provider": api_key.provider,
                    "masked_actual_key": api_key.masked_actual_key,
                }
            )
        return self._gm.bad_request(parse_serialized_errors(serializer))

    def perform_update(self, serializer):
        serializer.save(**self._save_context(self.request))

    def _update_provider_key(self, request, *, partial):
        instance = self.get_object()
        payload = self._normalize_provider_key_payload(request.data, instance=instance)
        serializer = self.get_serializer(instance, data=payload, partial=partial)
        serializer.is_valid(raise_exception=True)
        self.perform_update(serializer)
        return Response(serializer.data)

    @swagger_auto_schema(
        responses={200: ApiKeySuccessResponseSerializer, **MODEL_HUB_ERROR_RESPONSES}
    )
    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = self.get_serializer(instance)
        data = serializer.data
        # Use the decrypted key in the response
        # if instance.actual_key:
        #     data['key'] = instance.actual_key
        return self._gm.success_response(data)

    @validated_request(
        request_serializer=ApiKeyRequestSerializer,
        responses={200: ApiKeyResponseSerializer, **MODEL_HUB_ERROR_RESPONSES},
    )
    def update(self, request, *args, **kwargs):
        try:
            return self._update_provider_key(request, partial=False)
        except (json.JSONDecodeError, TypeError, ValidationError):
            return self._gm.bad_request(get_error_message("INVALID_FORMAT"))

    @validated_request(
        request_serializer=ApiKeyRequestSerializer,
        partial_request_validation=True,
        responses={200: ApiKeyResponseSerializer, **MODEL_HUB_ERROR_RESPONSES},
    )
    def partial_update(self, request, *args, **kwargs):
        try:
            return self._update_provider_key(request, partial=True)
        except (json.JSONDecodeError, TypeError, ValidationError):
            return self._gm.bad_request(get_error_message("INVALID_FORMAT"))

    def destroy(self, request, *args, **kwargs):
        """
        Soft-delete an API key.

        ApiKey inherits from BaseModel, so `instance.delete()` sets:
        - deleted=True
        - deleted_at=<timestamp>
        and excludes it from the default manager (`objects`) queries.
        """
        instance = self.get_object()
        instance.delete()
        return self._gm.success_response("success")


def create_placeholder(variable_name):
    """Create a Jinja2/Mustache placeholder like {{variable_name}}"""
    return "{{" + str(variable_name) + "}}"


def fix_double_quotes(text):
    """Fix double quotes that appear as ""quoted text"" to become "quoted text" """
    return re.sub(r'""([^"]*?)""', r'"\1"', text)


def convert_uuids_to_column_names(messages: list, dataset_id: str) -> list:
    """
    Convert column UUIDs in message templates back to column names for display in editor.
    Handles both simple {{uuid}} and nested {{uuid.property}} patterns.

    Args:
        messages: List of message dicts with 'role' and 'content'
        dataset_id: The dataset ID to look up columns

    Returns:
        Messages with UUIDs replaced by column names
    """
    from model_hub.views.utils.utils import replace_uuids_in_messages

    if not messages or not dataset_id:
        return messages

    # Build a mapping of column_id -> column_name for this dataset
    try:
        columns = Column.objects.filter(dataset_id=dataset_id, deleted=False)
        uuid_to_name = {str(col.id): col.name for col in columns}
    except Exception as e:
        logger.warning(f"Could not fetch columns for dataset {dataset_id}: {e}")
        return messages

    return replace_uuids_in_messages(messages, uuid_to_name)


# Template format options
TEMPLATE_FORMAT_FSTRING = "f-string"
TEMPLATE_FORMAT_MUSTACHE = "mustache"
TEMPLATE_FORMAT_JINJA2 = "jinja2"
DEFAULT_TEMPLATE_FORMAT = TEMPLATE_FORMAT_JINJA2


def normalize_template_format(template_format: str | None) -> str | None:
    """Normalize accepted aliases for internal rendering."""
    if template_format == "jinja":
        return TEMPLATE_FORMAT_JINJA2
    return template_format


def normalize_public_template_format(template_format: str | None) -> str | None:
    """Normalize accepted aliases for the public run_prompt_config contract."""
    if template_format == TEMPLATE_FORMAT_JINJA2:
        return "jinja"
    return template_format


def get_run_prompt_template_format(config: dict | None) -> str | None:
    """Return the canonical template format from current or legacy config."""
    config = config or {}
    run_prompt_config = config.get("run_prompt_config", {}) or {}
    legacy_configuration = config.get("configuration", {}) or {}
    return normalize_template_format(
        run_prompt_config.get("template_format")
        or legacy_configuration.get("template_format")
    )


def normalize_run_prompt_config(config: dict | None) -> dict:
    """Persist legacy template-format aliases into run_prompt_config.

    Preview and persisted execution must render with the same template format.
    Older clients may still send ``config.configuration.template_format``;
    normalize that value at the boundary so saved run prompts execute exactly
    like their preview.
    """
    config = config or {}
    run_prompt_config = dict(config.get("run_prompt_config", {}) or {})
    template_format = normalize_public_template_format(
        run_prompt_config.get("template_format")
        or (config.get("configuration", {}) or {}).get("template_format")
    )
    if template_format:
        run_prompt_config["template_format"] = template_format
    return run_prompt_config


def merge_run_prompt_config(existing_config: dict | None, config: dict | None) -> dict:
    """Merge edit payload config without erasing stored run prompt settings."""
    merged_config = dict(existing_config or {})
    normalized_config = normalize_run_prompt_config(config)
    if normalized_config:
        merged_config.update(normalized_config)
    return merged_config


# Jinja2 environment (reusable, sandboxed for security)
_jinja2_env = SandboxedEnvironment()
_jinja2_strict_env = SandboxedEnvironment(undefined=StrictUndefined)


class PromptTemplateSyntaxError(ValueError):
    """Raised when prompt template syntax is invalid before provider dispatch."""


class _MissingPromptValueError(ValueError):
    def __init__(self, placeholder: str):
        self.placeholder = placeholder
        super().__init__(placeholder)


class _MissingPromptValue:
    def __init__(self, placeholder: str):
        self.placeholder = placeholder

    def _raise(self):
        raise _MissingPromptValueError(self.placeholder)

    def __str__(self):
        self._raise()

    def __repr__(self):
        self._raise()

    def __bool__(self):
        self._raise()

    def __iter__(self):
        self._raise()

    def __len__(self):
        self._raise()

    def __getattr__(self, name):
        self._raise()

    def __getitem__(self, key):
        self._raise()

    def __contains__(self, item):
        self._raise()

    def __hash__(self):
        self._raise()

    def __eq__(self, other):
        self._raise()

    def __ne__(self, other):
        self._raise()

    def __lt__(self, other):
        self._raise()

    def __le__(self, other):
        self._raise()

    def __gt__(self, other):
        self._raise()

    def __ge__(self, other):
        self._raise()

    def _binary_op(self, *args, **kwargs):
        self._raise()

    __add__ = __radd__ = _binary_op
    __sub__ = __rsub__ = _binary_op
    __mul__ = __rmul__ = _binary_op
    __truediv__ = __rtruediv__ = _binary_op
    __floordiv__ = __rfloordiv__ = _binary_op
    __mod__ = __rmod__ = _binary_op
    __pow__ = __rpow__ = _binary_op


class UnresolvedPromptPlaceholdersError(ValueError):
    """Raised when strict prompt rendering encounters unresolved placeholders."""

    def __init__(self, placeholders):
        if isinstance(placeholders, str):
            placeholders = [placeholders]
        self.placeholders = [placeholder for placeholder in placeholders if placeholder]
        joined_placeholders = ", ".join(self.placeholders) or "unknown placeholder"
        super().__init__(f"Unresolved prompt placeholders: {joined_placeholders}")


def _build_placeholder_display_map(template_str: str) -> dict[str, str]:
    """Map render-time variable names back to the original placeholder text."""
    if not template_str:
        return {}

    placeholder_display_map: dict[str, str] = {}
    for match in re.finditer(r"\{\{\s*([#^/]?)\s*([^{}]+?)\s*\}\}", template_str):
        tag_prefix = match.group(1)
        expression = match.group(2).strip()
        if not expression:
            continue
        root_name = re.split(r"[.[]", expression, maxsplit=1)[0].strip()
        if not root_name:
            continue
        display = f"{{{{{tag_prefix}{root_name}}}}}" if tag_prefix else match.group(0)
        placeholder_display_map.setdefault(root_name, display)
        placeholder_display_map.setdefault(sanitize_uuid_for_jinja(root_name), display)
    return placeholder_display_map


def _resolve_missing_placeholder_reference(
    error_message: str, placeholder_display_map: dict[str, str] | None = None
) -> str:
    """Prefer the original placeholder text when Jinja reports an undefined variable."""
    placeholder_display_map = placeholder_display_map or {}
    undefined_match = re.search(r"'([^']+)' is undefined", error_message)
    if undefined_match:
        missing_name = undefined_match.group(1)
        return placeholder_display_map.get(missing_name, missing_name)
    return error_message


_JINJA_PLACEHOLDER_PATTERN = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")
_UUID_PLACEHOLDER_WITH_WHITESPACE_PATTERN = re.compile(
    r"\{\{\s*([#^/]?)\s*"
    r"([a-fA-F0-9]{8}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-"
    r"[a-fA-F0-9]{4}-[a-fA-F0-9]{12})"
    r"(\.[\w.]+)?\s*\}\}",
    re.IGNORECASE,
)


def _placeholder_display_name(
    placeholder: str, placeholder_display_map: dict[str, str] | None = None
) -> str:
    if placeholder_display_map and placeholder in placeholder_display_map:
        return placeholder_display_map[placeholder]
    return placeholder


def _format_unresolved_placeholder_error(
    placeholders: list[str], placeholder_display_map: dict[str, str] | None = None
) -> UnresolvedPromptPlaceholdersError:
    display_placeholders = []
    seen = set()
    for placeholder in placeholders:
        display_placeholder = _placeholder_display_name(
            placeholder, placeholder_display_map
        )
        if display_placeholder in seen:
            continue
        seen.add(display_placeholder)
        display_placeholders.append(display_placeholder)
    return UnresolvedPromptPlaceholdersError(display_placeholders)


@lru_cache(maxsize=512)
def _extract_jinja_variable_expressions(template_str: str) -> tuple[str, ...]:
    """Return actual Jinja print-expression text, excluding raw/data literals.

    Regex scans over the raw template confuse literal text (for example raw
    blocks or string constants that contain ``{{...}}``) with placeholders. The
    Jinja lexer distinguishes real variable blocks from raw/data content, while
    still letting us inspect human column-name placeholders that the parser
    cannot represent as a normal AST reference.
    """
    if not template_str:
        return ()

    expressions: list[str] = []
    current_tokens: list[str] | None = None
    try:
        tokens = list(_jinja2_env.lex(template_str))
    except TemplateSyntaxError:
        return tuple(
            match.group(1).strip()
            for match in _JINJA_PLACEHOLDER_PATTERN.finditer(template_str)
            if match.group(1).strip()
        )

    for _, token_type, value in tokens:
        if token_type == "variable_begin":
            current_tokens = []
            continue
        if token_type == "variable_end":
            if current_tokens is not None:
                expression = "".join(current_tokens).strip()
                if expression:
                    expressions.append(expression)
            current_tokens = None
            continue
        if current_tokens is not None:
            current_tokens.append(value)

    return tuple(expressions)


_HUMAN_PLACEHOLDER_NAME_PATTERN = re.compile(r"^[\w][\w -]*$")
_OPERATOR_WITH_WHITESPACE_PATTERN = re.compile(r"\s[-+*/%]\s*|[-+*/%]\s")
_NUMERIC_DASH_EXPRESSION_PATTERN = re.compile(r"^[\d\s-]+$")


def _human_named_placeholder_subject(
    expression: str,
    context: dict | None = None,
    unresolved_placeholders: set[str] | None = None,
) -> str | None:
    """Return a single human-named placeholder subject, if expression is one.

    Human dataset columns may be written as Jinja print expressions even when the
    name is not a valid Jinja identifier, for example ``{{Input Column}}`` or
    ``{{customer-name}}``. Those should be treated as one placeholder name, while
    valid operator expressions like ``{{ foo - bar }}`` and literals like
    ``{{ -1 }}`` must remain normal Jinja expressions.
    """
    filter_subject = expression.split("|", maxsplit=1)[0].strip()
    if not filter_subject:
        return None

    # Exact dataset-column matches win before syntax heuristics. Column names are
    # free-form strings, so resolved prompts like ``{{Cost ($)}}`` or
    # ``{{Input / Output}}`` should render instead of being parsed as Jinja code.
    if context is not None and filter_subject in context:
        return filter_subject

    has_human_name_syntax = " " in filter_subject or "-" in filter_subject
    if not has_human_name_syntax:
        return None

    # A spaced arithmetic operator is an expression, not a column name. This
    # deliberately rejects dotted arithmetic such as ``account.total - reserve``
    # while preserving compact hyphenated human names like ``customer-name``.
    if _OPERATOR_WITH_WHITESPACE_PATTERN.search(filter_subject):
        return None

    # Hyphens with dotted references are Jinja subtraction-shaped, e.g.
    # ``account.total-reserve``; dotted human names are handled by normal Jinja
    # references, not this unrenderable-name path.
    if "." in filter_subject and "-" in filter_subject:
        return None

    # Negative numeric literals (and numeric subtraction written without spaces)
    # are valid Jinja expressions, not human placeholders.
    if filter_subject.startswith("-") or _NUMERIC_DASH_EXPRESSION_PATTERN.fullmatch(
        filter_subject
    ):
        return None

    if not _HUMAN_PLACEHOLDER_NAME_PATTERN.fullmatch(filter_subject):
        return None

    if _is_unresolved_placeholder_reference(filter_subject, unresolved_placeholders):
        return filter_subject

    if "-" in filter_subject and context is not None and filter_subject not in context:
        try:
            ast = _jinja2_env.parse("{{ " + expression + " }}")
        except TemplateSyntaxError:
            pass
        else:
            external_roots = meta.find_undeclared_variables(ast)
            if external_roots and all(root in context for root in external_roots):
                return None

    return filter_subject


def _extract_unrenderable_named_placeholders(
    template_str: str,
    context: dict | None = None,
    unresolved_placeholders: set[str] | None = None,
) -> list[str]:
    if not template_str:
        return []

    placeholders = []
    for expression in _extract_jinja_variable_expressions(template_str):
        # Column names can contain spaces/hyphens, but bare Jinja identifiers
        # cannot contain spaces and hyphenated names parse as subtraction. Detect
        # only actual single human placeholder subjects so raw blocks, string
        # constants, and arithmetic expressions remain valid Jinja.
        placeholder = _human_named_placeholder_subject(
            expression, context, unresolved_placeholders
        )
        if placeholder:
            placeholders.append(placeholder)
    return placeholders


def _safe_identifier_for_human_placeholder(name: str, index: int) -> str:
    safe_name = re.sub(r"\W+", "_", name).strip("_") or "placeholder"
    return f"__prompt_placeholder_{index}_{safe_name}"


def _rewrite_unrenderable_jinja_placeholders(
    template_str: str,
    context: dict,
    unresolved_placeholders: set[str] | None = None,
    placeholder_display_map: dict[str, str] | None = None,
) -> tuple[str, dict, set[str]]:
    """Rewrite human-named Jinja variables to safe identifiers token-wise.

    Jinja cannot parse bare variable names containing spaces, and treats hyphens
    as subtraction. Replacing the whole ``{{...}}`` text with the cell value is
    unsafe because a value like ``{{secret}}`` becomes new template source. This
    function rewrites only real Jinja variable blocks (not raw/data text or
    string literals) so the cell value is passed through the render context and
    emitted literally.
    """
    if not template_str:
        return template_str or "", dict(context), set()

    safe_ctx = dict(context)
    synthetic_by_name: dict[str, str] = {}
    rewritten_names: set[str] = set()
    output: list[str] = []
    current_tokens: list[str] | None = None

    def rewrite_expression(expression: str) -> str:
        filter_subject = expression.split("|", maxsplit=1)[0]
        stripped = _human_named_placeholder_subject(
            expression, context, unresolved_placeholders
        )
        if not stripped:
            return expression

        is_unresolved = _is_unresolved_placeholder_reference(
            stripped, unresolved_placeholders
        )
        if stripped not in safe_ctx and not is_unresolved:
            return expression

        synthetic = synthetic_by_name.get(stripped)
        if synthetic is None:
            synthetic = _safe_identifier_for_human_placeholder(
                stripped, len(synthetic_by_name)
            )
            while synthetic in safe_ctx:
                synthetic = f"{synthetic}_{len(synthetic_by_name)}"
            synthetic_by_name[stripped] = synthetic
            if is_unresolved:
                safe_ctx[synthetic] = _MissingPromptValue(
                    _placeholder_display_name(stripped, placeholder_display_map)
                )
            else:
                safe_ctx[synthetic] = safe_ctx[stripped]
        rewritten_names.add(stripped)

        prefix_length = len(filter_subject) - len(filter_subject.lstrip())
        suffix_length = len(filter_subject) - len(filter_subject.rstrip())
        prefix = filter_subject[:prefix_length]
        suffix = (
            filter_subject[len(filter_subject) - suffix_length :]
            if suffix_length
            else ""
        )
        return f"{prefix}{synthetic}{suffix}{expression[len(filter_subject):]}"

    try:
        tokens = list(_jinja2_env.lex(template_str))
    except TemplateSyntaxError:
        def replace_exact_match(match):
            expression = match.group(1).strip()
            stripped = _human_named_placeholder_subject(
                expression, context, unresolved_placeholders
            )
            if not stripped:
                return match.group(0)
            is_unresolved = _is_unresolved_placeholder_reference(
                stripped, unresolved_placeholders
            )
            if stripped not in safe_ctx and not is_unresolved:
                return match.group(0)
            synthetic = synthetic_by_name.get(stripped)
            if synthetic is None:
                synthetic = _safe_identifier_for_human_placeholder(
                    stripped, len(synthetic_by_name)
                )
                while synthetic in safe_ctx:
                    synthetic = f"{synthetic}_{len(synthetic_by_name)}"
                synthetic_by_name[stripped] = synthetic
                if is_unresolved:
                    safe_ctx[synthetic] = _MissingPromptValue(
                        _placeholder_display_name(stripped, placeholder_display_map)
                    )
                else:
                    safe_ctx[synthetic] = safe_ctx[stripped]
            rewritten_names.add(stripped)
            suffix = expression[len(stripped) :]
            return "{{ " + synthetic + suffix + " }}"

        return (
            _JINJA_PLACEHOLDER_PATTERN.sub(replace_exact_match, template_str),
            safe_ctx,
            rewritten_names,
        )

    for _, token_type, value in tokens:
        if token_type == "variable_begin":
            output.append(value)
            current_tokens = []
            continue
        if token_type == "variable_end":
            if current_tokens is not None:
                output.append(rewrite_expression("".join(current_tokens)))
            output.append(value)
            current_tokens = None
            continue
        if current_tokens is not None:
            current_tokens.append(value)
        else:
            output.append(value)

    return "".join(output), safe_ctx, rewritten_names


def _prompt_template_syntax_error(exc: TemplateSyntaxError) -> PromptTemplateSyntaxError:
    detail = str(exc).strip() or exc.__class__.__name__
    return PromptTemplateSyntaxError(f"Invalid prompt template syntax: {detail}")


def _sanitize_uuid_placeholders_for_template(text: str) -> str:
    if not text:
        return text or ""

    def replace_match(match):
        tag_prefix = match.group(1) or ""
        uuid_str = match.group(2)
        suffix = match.group(3) or ""
        return "{{" + tag_prefix + sanitize_uuid_for_jinja(uuid_str) + suffix + "}}"

    return _UUID_PLACEHOLDER_WITH_WHITESPACE_PATTERN.sub(replace_match, text)


def _jinja_reference_from_node(node: nodes.Node) -> str | None:
    if isinstance(node, nodes.Name):
        return node.name
    if isinstance(node, nodes.Getattr):
        base = _jinja_reference_from_node(node.node)
        return f"{base}.{node.attr}" if base else None
    if isinstance(node, nodes.Getitem):
        base = _jinja_reference_from_node(node.node)
        if not base:
            return None
        if isinstance(node.arg, nodes.Const) and isinstance(node.arg.value, str):
            return f"{base}.{node.arg.value}"
        return base
    return None


@lru_cache(maxsize=512)
def _extract_jinja_ast_references(template_str: str) -> tuple[str, ...]:
    """Return external root and dotted references used by Jinja expressions.

    This catches unresolved null-backed placeholders even when Jinja filters (for
    example ``default``) would otherwise render a fallback and hide the missing
    cell. Jinja ASTs also contain local binding names (loop targets, ``set``
    assignments, macro parameters); ``meta.find_undeclared_variables`` is the
    Jinja-supported way to identify variables that must come from the render
    context, so only references rooted at those external names are returned.
    """
    try:
        ast = _jinja2_env.parse(template_str)
    except TemplateSyntaxError:
        return ()

    external_roots = meta.find_undeclared_variables(ast)
    references: list[str] = []
    seen = set()
    for node in ast.find_all((nodes.Name, nodes.Getattr, nodes.Getitem)):
        reference = _jinja_reference_from_node(node)
        if not reference:
            continue
        root = reference.split('.', maxsplit=1)[0]
        if root not in external_roots or reference in seen:
            continue
        seen.add(reference)
        references.append(reference)
    return tuple(references)


def _is_unresolved_placeholder_reference(
    placeholder: str, unresolved_placeholders: set[str] | None
) -> bool:
    if not unresolved_placeholders:
        return False
    return any(
        placeholder == unresolved
        or placeholder.startswith(f"{unresolved}.")
        for unresolved in unresolved_placeholders
    )


def _raise_for_undefined_tests_on_unresolved_references(
    template_str: str,
    context: dict,
    unresolved_placeholders: set[str] | None,
    placeholder_display_map: dict[str, str] | None = None,
) -> None:
    """Fail closed for ``is defined``/``is undefined`` checks on missing values.

    Jinja considers any normal object "defined", including our strict missing
    sentinel. Detect these tests from the AST before rendering so templates
    cannot turn unknown or unresolved prompt values into silently successful
    branches. This remains narrower than a source-wide missing-variable scan, so
    dead branches like ``{% if false %}{{ missing }}{% endif %}`` still render.
    """
    try:
        ast = _jinja2_env.parse(template_str)
    except TemplateSyntaxError:
        return

    external_roots = set(meta.find_undeclared_variables(ast))
    missing_references = []
    seen = set()
    for test_node in ast.find_all(nodes.Test):
        if test_node.name not in {"defined", "undefined"}:
            continue
        reference = _jinja_reference_from_node(test_node.node)
        if not reference:
            continue
        root = reference.split(".", maxsplit=1)[0]
        if root not in external_roots:
            continue
        if root in context and not _is_unresolved_placeholder_reference(
            reference, unresolved_placeholders
        ):
            continue
        if reference in seen:
            continue
        seen.add(reference)
        missing_references.append(reference)

    if missing_references:
        raise _format_unresolved_placeholder_error(
            missing_references, placeholder_display_map
        )


def _iter_jinja_nodes_skipping_static_dead_branches(
    node: nodes.Node, node_type: type[nodes.Node]
):
    """Yield nodes while ignoring branches Jinja can prove unreachable.

    Most strict placeholder failures should happen at render time so dead
    branches stay dead. The one source-level guard we still need is for
    comparison/membership expressions that can otherwise convert a missing value
    into a boolean without touching the missing-value sentinel. Keep that guard
    narrow by skipping literal-false branches.
    """
    if isinstance(node, node_type):
        yield node

    if isinstance(node, nodes.If) and isinstance(node.test, nodes.Const):
        selected_children = node.body if node.test.value else node.else_
        for child in selected_children:
            yield from _iter_jinja_nodes_skipping_static_dead_branches(
                child, node_type
            )
        return

    for child in node.iter_child_nodes():
        yield from _iter_jinja_nodes_skipping_static_dead_branches(child, node_type)


def _missing_external_reference(
    node: nodes.Node,
    context: dict,
    unresolved_placeholders: set[str] | None,
    external_roots: set[str],
) -> str | None:
    reference = _jinja_reference_from_node(node)
    if not reference:
        return None

    root = reference.split(".", maxsplit=1)[0]
    if root not in external_roots:
        return None

    if _is_unresolved_placeholder_reference(reference, unresolved_placeholders):
        return reference

    if root not in context:
        return reference

    return None


def _raise_for_missing_value_comparisons(
    template_str: str,
    context: dict,
    unresolved_placeholders: set[str] | None,
    placeholder_display_map: dict[str, str] | None = None,
) -> None:
    """Fail closed when missing prompt values are used as branch decisions.

    Render-time sentinels catch truthiness, stringification, iteration, and most
    operations only when the expression is evaluated, preserving dead Jinja
    branches. Comparisons and membership tests can otherwise turn a missing
    value into a boolean without touching sentinel methods (for example
    ``missing in []``), so guard those AST nodes explicitly.
    """
    try:
        ast = _jinja2_env.parse(template_str)
    except TemplateSyntaxError:
        return

    external_roots = set(meta.find_undeclared_variables(ast))
    missing_references = []
    seen = set()
    for compare_node in _iter_jinja_nodes_skipping_static_dead_branches(
        ast, nodes.Compare
    ):
        candidates = [compare_node.expr, *(operand.expr for operand in compare_node.ops)]
        for candidate in candidates:
            reference = _missing_external_reference(
                candidate,
                context,
                unresolved_placeholders,
                external_roots,
            )
            if not reference or reference in seen:
                continue
            seen.add(reference)
            missing_references.append(reference)

    if missing_references:
        raise _format_unresolved_placeholder_error(
            missing_references, placeholder_display_map
        )


def _apply_unresolved_prompt_sentinels(
    context: dict,
    unresolved_placeholders: set[str] | None,
    placeholder_display_map: dict[str, str] | None = None,
    external_roots: Collection[str] | None = None,
) -> None:
    """Install render-time sentinels for null/missing dataset-backed values."""
    if not unresolved_placeholders:
        return

    for placeholder in unresolved_placeholders:
        if not placeholder:
            continue
        parts = placeholder.split(".")
        display = _placeholder_display_name(placeholder, placeholder_display_map)
        sentinel = _MissingPromptValue(display)

        if len(parts) == 1:
            context[parts[0]] = sentinel
            continue

        root = parts[0]
        if root not in context:
            if external_roots is not None and root not in external_roots:
                continue
            context[root] = sentinel
            continue

        current = context[root]
        for part in parts[1:-1]:
            if isinstance(current, Mapping):
                if part not in current or isinstance(
                    current.get(part), _MissingPromptValue
                ):
                    current[part] = KeyPriorityDict()
                current = current[part]
                continue
            try:
                current = getattr(current, part)
            except AttributeError:
                context[root] = sentinel
                break
        else:
            leaf = parts[-1]
            if isinstance(current, Mapping):
                current[leaf] = sentinel
            else:
                try:
                    setattr(current, leaf, sentinel)
                except Exception:
                    context[root] = sentinel




def _extract_unknown_jinja_external_roots(template_str: str, context: dict) -> list[str]:
    """Return undeclared Jinja roots that are absent from the render context.

    StrictUndefined catches ordinary missing names at render time, but filters such
    as ``default`` can intentionally mask an undefined value. Strict prompt
    rendering must still fail closed for unknown external variables while leaving
    loop/set locals and human-named placeholders to their dedicated handling.
    """
    try:
        ast = _jinja2_env.parse(template_str)
    except TemplateSyntaxError:
        return []

    unknown_roots = [
        root for root in meta.find_undeclared_variables(ast) if root not in context
    ]
    if not unknown_roots:
        return []

    human_expression_roots = set()
    for expression in _extract_jinja_variable_expressions(template_str):
        if not _human_named_placeholder_subject(expression, context):
            continue
        try:
            expression_ast = _jinja2_env.parse("{{ " + expression + " }}")
        except TemplateSyntaxError:
            continue
        human_expression_roots.update(meta.find_undeclared_variables(expression_ast))

    unknown = []
    seen = set()
    for root in unknown_roots:
        if root in human_expression_roots or root in seen:
            continue
        seen.add(root)
        unknown.append(root)
    return unknown


@lru_cache(maxsize=512)
def _extract_jinja_referenced_roots(template_str: str) -> frozenset[str]:
    """Return top-level context roots referenced by a parsed Jinja template."""
    try:
        ast = _jinja2_env.parse(template_str)
    except TemplateSyntaxError:
        return frozenset()
    return frozenset(meta.find_undeclared_variables(ast))


@lru_cache(maxsize=512)
def _mustache_tokens(template_str: str) -> tuple[tuple[str, str], ...]:
    from chevron.tokenizer import tokenize as tokenize_mustache

    return tuple(tokenize_mustache(template_str))


@lru_cache(maxsize=512)
def _extract_mustache_referenced_roots(template_str: str) -> frozenset[str]:
    """Return top-level context roots referenced by Mustache tokens."""
    referenced_roots: set[str] = set()
    for tag, key in _mustache_tokens(template_str):
        if tag not in ("variable", "no escape", "section", "inverted section"):
            continue
        key = key.strip()
        if not key or key == ".":
            continue
        referenced_roots.add(key.split(".", maxsplit=1)[0])
    return frozenset(referenced_roots)


def _wrap_referenced_context_roots(
    context: dict, referenced_roots: Collection[str]
) -> dict:
    """Shallow-copy context and recursively wrap only referenced root values."""
    render_context = dict(context)
    for root in referenced_roots:
        if root in render_context:
            render_context[root] = _wrap_mapping_attribute_values(render_context[root])
    return render_context


def _get_mustache_key_from_scopes(key: str, scopes: list[Any]) -> tuple[bool, Any]:
    if key == ".":
        return True, scopes[0] if scopes else None

    for scope in scopes:
        current = scope
        try:
            for child in key.split("."):
                if isinstance(current, Mapping):
                    current = current[child]
                else:
                    try:
                        current = current[child]
                    except (TypeError, AttributeError, KeyError):
                        current = getattr(current, child)
            return True, current
        except (AttributeError, KeyError, IndexError, TypeError, ValueError):
            continue
    return False, None


def _find_mustache_section_end(tokens: list[tuple[str, str]], start_index: int) -> int:
    section_name = tokens[start_index][1]
    depth = 0
    for index in range(start_index + 1, len(tokens)):
        tag, key = tokens[index]
        if tag in ("section", "inverted section") and key == section_name:
            depth += 1
        elif tag == "end" and key == section_name:
            if depth == 0:
                return index
            depth -= 1
    return len(tokens)


def _is_mustache_truthy(value: Any) -> bool:
    return bool(value)


def _validate_mustache_token_range(
    tokens: list[tuple[str, str]],
    scopes: list[Any],
    start_index: int,
    end_index: int,
    missing_placeholders: list[str],
    unresolved_placeholders: set[str] | None = None,
) -> None:
    index = start_index
    while index < end_index:
        tag, key = tokens[index]
        if tag in ("variable", "no escape"):
            if _is_unresolved_placeholder_reference(key, unresolved_placeholders):
                missing_placeholders.append(key)
            else:
                exists, value = _get_mustache_key_from_scopes(key, scopes)
                if not exists or value is None:
                    missing_placeholders.append(key)
            index += 1
            continue

        if tag == "section":
            section_end = _find_mustache_section_end(tokens, index)
            if _is_unresolved_placeholder_reference(key, unresolved_placeholders):
                missing_placeholders.append(key)
                index = section_end + 1
                continue
            exists, value = _get_mustache_key_from_scopes(key, scopes)
            if not exists:
                missing_placeholders.append(key)
            elif _is_mustache_truthy(value):
                if isinstance(value, list):
                    for item in value:
                        _validate_mustache_token_range(
                            tokens,
                            [item, *scopes],
                            index + 1,
                            section_end,
                            missing_placeholders,
                            unresolved_placeholders,
                        )
                elif isinstance(value, dict):
                    _validate_mustache_token_range(
                        tokens,
                        [value, *scopes],
                        index + 1,
                        section_end,
                        missing_placeholders,
                        unresolved_placeholders,
                    )
                else:
                    _validate_mustache_token_range(
                        tokens,
                        scopes,
                        index + 1,
                        section_end,
                        missing_placeholders,
                        unresolved_placeholders,
                    )
            index = section_end + 1
            continue

        if tag == "inverted section":
            section_end = _find_mustache_section_end(tokens, index)
            if _is_unresolved_placeholder_reference(key, unresolved_placeholders):
                missing_placeholders.append(key)
                index = section_end + 1
                continue
            exists, value = _get_mustache_key_from_scopes(key, scopes)
            if not exists:
                missing_placeholders.append(key)
            elif not _is_mustache_truthy(value):
                _validate_mustache_token_range(
                    tokens,
                    scopes,
                    index + 1,
                    section_end,
                    missing_placeholders,
                    unresolved_placeholders,
                )
            index = section_end + 1
            continue

        index += 1


def _validate_mustache_placeholders(
    template_str: str,
    context: dict,
    placeholder_display_map: dict[str, str] | None = None,
    unresolved_placeholders: set[str] | None = None,
) -> None:
    missing_placeholders = []
    tokens = list(_mustache_tokens(template_str))
    _validate_mustache_token_range(
        tokens,
        [context],
        0,
        len(tokens),
        missing_placeholders,
        unresolved_placeholders,
    )

    if missing_placeholders:
        raise _format_unresolved_placeholder_error(
            missing_placeholders, placeholder_display_map
        )


def _resolve_fstring_missing_placeholder(
    template_str: str, context: dict, exc: Exception
) -> str:
    if isinstance(exc, KeyError) and exc.args:
        return str(exc.args[0])

    formatter = string.Formatter()
    try:
        field_names = [
            field_name
            for _, field_name, _, _ in formatter.parse(template_str)
            if field_name is not None
        ]
    except ValueError:
        field_names = []

    if isinstance(exc, IndexError):
        for field_name in field_names:
            root = field_name.split(".", maxsplit=1)[0].split("[", maxsplit=1)[0]
            if field_name == "" or root.isdigit():
                return field_name or str(exc)
        return str(exc)

    if isinstance(exc, AttributeError):
        attr_match = re.search(r"attribute ['\"]([^'\"]+)['\"]", str(exc))
        missing_attr = attr_match.group(1) if attr_match else None
        if missing_attr:
            for field_name in field_names:
                if field_name.endswith(f".{missing_attr}"):
                    return field_name
            return missing_attr

    return str(exc)


@lru_cache(maxsize=512)
def _extract_fstring_field_references(template_str: str) -> tuple[str, ...]:
    formatter = string.Formatter()
    references: list[str] = []
    seen = set()
    for _, field_name, _, _ in formatter.parse(template_str):
        if field_name is None:
            continue
        # Empty positional fields (``{}``) are invalid with keyword-only
        # formatting below; let ``str.format`` raise the normal IndexError.
        if field_name == "":
            continue
        root = field_name.split(".", maxsplit=1)[0].split("[", maxsplit=1)[0]
        for candidate in (field_name, root):
            if candidate and candidate not in seen:
                seen.add(candidate)
                references.append(candidate)
    return tuple(references)


def _raise_for_fstring_unresolved_references(
    template_str: str,
    unresolved_placeholders: set[str] | None,
    placeholder_display_map: dict[str, str] | None = None,
) -> None:
    if not unresolved_placeholders:
        return

    referenced_unresolved = []
    seen = set()
    for reference in _extract_fstring_field_references(template_str):
        if reference in seen:
            continue
        seen.add(reference)
        if _is_unresolved_placeholder_reference(reference, unresolved_placeholders):
            referenced_unresolved.append(reference)

    if referenced_unresolved:
        raise _format_unresolved_placeholder_error(
            referenced_unresolved, placeholder_display_map
        )


def render_template(
    template_str: str,
    context: dict,
    template_format: str = None,
    strict: bool = False,
    placeholder_display_map: dict[str, str] | None = None,
    unresolved_placeholders: set[str] | None = None,
) -> str:
    """
    Render a template string with the given context.
    Supports multiple formats: f-string, mustache, jinja2.

    Args:
        template_str: The template string
        context: Dictionary of variables to substitute
        template_format: One of 'f-string', 'mustache', 'jinja2' (default: jinja2)
        strict: When True, unresolved placeholders raise ValueError instead of
            rendering as blank output.
        placeholder_display_map: Optional mapping from sanitized placeholder
            names back to their original display form for clearer errors.
        unresolved_placeholders: Optional names that exist in the backing row but
            are unresolved for strict rendering, such as null cell values.

    Returns:
        Rendered string
    """
    if not template_str:
        return template_str or ""

    if template_format is None:
        template_format = DEFAULT_TEMPLATE_FORMAT

    if template_format == TEMPLATE_FORMAT_FSTRING:
        try:
            if strict:
                _raise_for_fstring_unresolved_references(
                    template_str,
                    unresolved_placeholders,
                    placeholder_display_map,
                )
            return template_str.format(**context)
        except UnresolvedPromptPlaceholdersError:
            raise
        except ValueError as exc:
            detail = str(exc).strip() or exc.__class__.__name__
            raise PromptTemplateSyntaxError(
                f"Invalid prompt template syntax: {detail}"
            ) from exc
        except (KeyError, IndexError, AttributeError) as exc:
            missing_placeholder = _resolve_fstring_missing_placeholder(
                template_str, context, exc
            )
            raise _format_unresolved_placeholder_error(
                [missing_placeholder], placeholder_display_map
            ) from exc

    elif template_format == TEMPLATE_FORMAT_MUSTACHE:
        if strict and "{{" not in template_str:
            return template_str
        placeholder_display_map = placeholder_display_map or _build_placeholder_display_map(
            template_str
        )
        template_str = _sanitize_uuid_placeholders_for_template(template_str)
        if strict:
            _validate_mustache_placeholders(
                template_str,
                context,
                placeholder_display_map=placeholder_display_map,
                unresolved_placeholders=unresolved_placeholders,
            )
        referenced_roots = _extract_mustache_referenced_roots(template_str)
        render_context = _wrap_referenced_context_roots(context, referenced_roots)
        return chevron.render(template_str, render_context)

    elif template_format == TEMPLATE_FORMAT_JINJA2:
        if strict and "{{" not in template_str and "{%" not in template_str:
            return template_str
        placeholder_display_map = placeholder_display_map or _build_placeholder_display_map(
            template_str
        )
        try:
            processed, safe_ctx, rewritten_human_placeholders = (
                _rewrite_unrenderable_jinja_placeholders(
                    template_str,
                    context,
                    unresolved_placeholders=unresolved_placeholders,
                    placeholder_display_map=placeholder_display_map,
                )
            )
            referenced_roots = _extract_jinja_referenced_roots(processed)
            safe_ctx = _wrap_referenced_context_roots(safe_ctx, referenced_roots)
            if strict:
                _raise_for_undefined_tests_on_unresolved_references(
                    template_str,
                    context,
                    unresolved_placeholders,
                    placeholder_display_map,
                )
                _raise_for_missing_value_comparisons(
                    template_str,
                    context,
                    unresolved_placeholders,
                    placeholder_display_map,
                )
                unknown_external_roots = _extract_unknown_jinja_external_roots(
                    template_str, context
                )
                for root in unknown_external_roots:
                    safe_ctx[root] = _MissingPromptValue(
                        _placeholder_display_name(root, placeholder_display_map)
                    )
                _apply_unresolved_prompt_sentinels(
                    safe_ctx,
                    unresolved_placeholders,
                    placeholder_display_map,
                    referenced_roots,
                )
                unrenderable_placeholders = []
                for filter_subject in _extract_unrenderable_named_placeholders(
                    template_str, context, unresolved_placeholders
                ):
                    if (
                        filter_subject not in context
                        and filter_subject not in rewritten_human_placeholders
                        and not _is_unresolved_placeholder_reference(
                            filter_subject, unresolved_placeholders
                        )
                    ):
                        unrenderable_placeholders.append(filter_subject)
                if unrenderable_placeholders:
                    raise _format_unresolved_placeholder_error(
                        unrenderable_placeholders, placeholder_display_map
                    )
            jinja_env = _jinja2_strict_env if strict else _jinja2_env
            return jinja_env.from_string(processed).render(**safe_ctx)
        except TemplateSyntaxError as exc:
            raise _prompt_template_syntax_error(exc) from exc
        except _MissingPromptValueError as exc:
            raise UnresolvedPromptPlaceholdersError(exc.placeholder) from exc
        except UndefinedError as exc:
            unresolved_placeholder = _resolve_missing_placeholder_reference(
                str(exc), placeholder_display_map=placeholder_display_map
            )
            raise UnresolvedPromptPlaceholdersError(unresolved_placeholder) from exc

    else:
        raise ValueError(
            f"Unknown template_format: {template_format}. "
            f"Supported: {TEMPLATE_FORMAT_FSTRING}, {TEMPLATE_FORMAT_MUSTACHE}, {TEMPLATE_FORMAT_JINJA2}"
        )


class KeyPriorityDict(dict):
    """Dict whose dot access prefers explicit keys over dict methods.

    Jinja resolves ``account.items`` with attribute lookup before item lookup for
    dict-like objects, so method-like keys (``items``, ``keys``, ``values``)
    otherwise render as bound methods. Only non-private names are key-prioritized
    so Python internals and private state keep normal attribute behavior.
    """

    def __getattribute__(self, name):
        if not name.startswith("_"):
            try:
                return dict.__getitem__(self, name)
            except KeyError:
                pass
        return super().__getattribute__(name)


def _wrap_mapping_attribute_values(value):
    """Recursively wrap mappings so template dot access sees keys first."""
    if isinstance(value, JsonStr):
        return value
    if isinstance(value, Mapping):
        return KeyPriorityDict(
            {key: _wrap_mapping_attribute_values(child) for key, child in value.items()}
        )
    if isinstance(value, list):
        return [_wrap_mapping_attribute_values(child) for child in value]
    if isinstance(value, tuple):
        return tuple(_wrap_mapping_attribute_values(child) for child in value)
    return value


class JsonStr(KeyPriorityDict):
    """Dict subclass that renders as its original JSON string via str()/Jinja.
    Allows {{col.key}} via dict attribute access while {{col}} outputs the raw JSON."""

    def __init__(self, data, raw):
        super().__init__(
            {key: _wrap_mapping_attribute_values(value) for key, value in data.items()}
        )
        self._raw = raw

    def __str__(self):
        return self._raw


def populate_placeholders(
    messages: list[dict],
    dataset_id,
    row_id,
    col_id,
    model_name,
    template_format=None,
    process_media=True,
    fail_closed=False,
):
    media_error = False
    try:
        logger.debug(
            "populate_placeholders called",
            message_count=len(messages or []),
        )

        dataset = Dataset.objects.get(id=dataset_id)
        column_ids = dataset.column_order

        # Create context for Handlebars with proper nesting
        context: dict[str, Any] = {}
        column_info = {}  # For image handling
        raw_values = {}  # For debugging
        unresolved_placeholders: set[str] = set()

        # Collect column values
        for column_id in column_ids:
            try:
                if column_id != str(col_id):
                    column = Column.objects.get(id=column_id)
                    cell = Cell.objects.filter(
                        dataset=dataset, column=column, row__id=row_id
                    ).first()

                    sanitized_col_id = sanitize_uuid_for_jinja(column_id)
                    if not cell:
                        unresolved_placeholders.update({column.name, sanitized_col_id})
                        continue

                    # Store raw values for debugging
                    raw_values[column.name] = (
                        cell.value if cell.value is not None else ""
                    )

                    if cell.value is None:
                        unresolved_placeholders.update({column.name, sanitized_col_id})

                    # Store column info for image handling
                    column_info[column_id] = {
                        "value": cell.value if cell.value is not None else "",
                        "data_type": column.data_type,
                        "name": column.name,
                    }

                    # Build nested structure based on column name (e.g., account.name)
                    parts = column.name.split(".")
                    current = context

                    # Determine the value to store - parse JSON for dot notation access.
                    # For any column with a JSON string value, parse it into a
                    # JsonStr dict so {{col.key}} works via Jinja attribute access
                    # while {{col}} still renders as the original JSON string.
                    cell_value = cell.value if cell.value is not None else ""
                    if cell_value and isinstance(cell_value, str):
                        from model_hub.utils.json_path_resolver import parse_json_safely

                        parsed_json, is_valid = parse_json_safely(cell_value)
                        if is_valid and isinstance(parsed_json, dict):
                            cell_value = JsonStr(parsed_json, cell_value)
                        elif (
                            is_valid
                            and isinstance(parsed_json, list)
                            and template_format in ("jinja", "jinja2")
                        ):
                            # Only parse lists for Jinja mode ({% for %} iteration).
                            # Mustache/default mode keeps the raw JSON string.
                            cell_value = parsed_json

                    # Create nested objects
                    for i, part in enumerate(parts):
                        if i == len(parts) - 1:
                            # Set the leaf value (parsed dict for JSON, string otherwise)
                            current[part] = cell_value
                        else:
                            # Create intermediate objects if they don't exist
                            if part not in current:
                                current[part] = {}
                            current = current[part]

                    # Store at sanitized column_id level (hyphens -> underscores for Jinja2)
                    context[sanitized_col_id] = cell_value

                    # Debug: Log what we're adding to context
                    logger.debug(
                        "Added prompt placeholder context entry",
                        column_name=column.name,
                        data_type=column.data_type,
                        value_type=type(cell_value).__name__,
                    )
            except Exception as e:
                logger.exception(
                    f"Error processing column {column_id} ({column.name if 'column' in locals() else 'unknown'}): {e}"
                )
                if fail_closed:
                    raise
                continue

        # Debug: Log final context structure
        logger.debug("Final prompt placeholder context keys", keys=list(context.keys()))
        for key, value in context.items():
            if isinstance(value, dict):
                logger.debug(
                    "Prompt placeholder context nested keys",
                    key=key,
                    nested_keys=list(value.keys()),
                )

        # Process messages
        image_counter = 0
        processed_messages = []
        try:
            for message in messages:
                content = message.get("content")
                processed_content = []

                if isinstance(content, list):
                    processed_content = process_list_content(
                        content,
                        column_info,
                        context,
                        image_counter,
                        model_name,
                        template_format=template_format,
                        unresolved_placeholders=unresolved_placeholders,
                        process_media=process_media,
                        fail_closed=fail_closed,
                    )
                elif isinstance(content, str):
                    processed_content = process_string_content(
                        content,
                        column_info,
                        context,
                        image_counter,
                        model_name,
                        template_format=template_format,
                        unresolved_placeholders=unresolved_placeholders,
                        process_media=process_media,
                        fail_closed=fail_closed,
                    )

                # If no content was processed, keep original
                if not processed_content:
                    if isinstance(content, str):
                        processed_content = [{"type": "text", "text": content}]
                    elif isinstance(content, list):
                        processed_content = content

                # Preserve all message keys (name, tool_calls, tool_call_id, etc.)
                processed_messages.append({**message, "content": processed_content})

            return processed_messages

        except (UnresolvedPromptPlaceholdersError, PromptTemplateSyntaxError):
            media_error = True
            raise
        except ValueError:
            media_error = True
            raise

    except (UnresolvedPromptPlaceholdersError, PromptTemplateSyntaxError):
        raise
    except Exception as e:
        if media_error:
            raise
        if fail_closed:
            raise
        traceback.print_exc()
        logger.exception(f"Fatal error processing messages: {e}")
        if fail_closed:
            raise
        # Return original messages as fallback
        return messages


def process_list_content(
    content,
    column_info,
    context,
    image_counter,
    model_name,
    template_format=None,
    unresolved_placeholders=None,
    process_media=True,
    fail_closed: bool = False,
):
    """Process list-type content with proper media handling"""
    processed_content = []

    for item in content:
        if "text" in item:
            # Process text content with templates and media
            text_segments = process_text_with_media(
                item["text"],
                column_info,
                context,
                image_counter,
                model_name,
                template_format=template_format,
                unresolved_placeholders=unresolved_placeholders,
                process_media=process_media,
                fail_closed=fail_closed,
            )
            processed_content.extend(text_segments)
        else:
            # Handle other media types
            try:
                if process_media:
                    processed_content.append(handle_media(item, model_name))
                else:
                    if fail_closed:
                        _validate_media_item_support(item, model_name)
                    processed_content.append(item)
            except Exception as e:
                logger.exception(f"Error handling media item: {e}")
                if fail_closed:
                    raise
                # Keep original item if processing fails
                processed_content.append(item)

    return processed_content


def _validate_media_item_support(item: dict, model_name: str) -> None:
    item_type = item.get("type")
    if item_type == "image_url":
        if not litellm.utils.supports_vision(model=model_name):
            raise ValueError(f"Model {model_name} does not support image input.")
    elif item_type == "audio_url":
        model_mode = get_model_mode(model_name)
        if model_mode not in (
            "audio",
            "stt",
            "tts",
        ) and not litellm.utils.supports_audio_input(model=model_name):
            raise ValueError(f"Model {model_name} does not support audio input.")
    elif item_type == "pdf_url":
        if not litellm.utils.supports_pdf_input(model=model_name):
            raise ValueError(f"Model {model_name} does not support PDF input.")


def _validate_media_marker_support(image_markers: dict, model_name: str) -> None:
    for info in image_markers.values():
        marker_type = info.get("type")
        if marker_type == "image":
            _validate_media_item_support({"type": "image_url"}, model_name)
        elif marker_type == "audio":
            _validate_media_item_support({"type": "audio_url"}, model_name)


_MEDIA_MARKER_PATTERN = re.compile(r"__(?:IMAGE|AUDIO|PDF)_MARKER_[0-9a-f-]+__")


def _messages_require_media_processing(messages: list[dict]) -> bool:
    for message in messages or []:
        content = message.get("content")
        if isinstance(content, list):
            for item in content:
                if item.get("type") != "text":
                    return True
                if _MEDIA_MARKER_PATTERN.search(str(item.get("text", ""))):
                    return True
        elif isinstance(content, str) and _MEDIA_MARKER_PATTERN.search(content):
            return True
    return False


def _replace_template_variable_placeholders(text, variable_names, replacement_factory):
    """Replace standalone ``{{ variable }}`` blocks without touching literals.

    Media placeholders are not rendered through the normal template context; they
    are converted into synthetic markers before rendering. Plain string
    replacement only handled ``{{Column}}`` and missed the common
    ``{{ Column }}`` form. Tokenizing through Jinja keeps raw blocks and
    expression-generated literal braces intact while still matching exact
    variable blocks by their original name/UUID.
    """
    variable_names = {str(name) for name in variable_names if name is not None}
    if not text or "{{" not in text or not variable_names:
        return text, set()

    escaped_names = "|".join(
        re.escape(name) for name in sorted(variable_names, key=len, reverse=True)
    )
    exact_variable_pattern = re.compile(r"\{\{\s*(" + escaped_names + r")\s*\}\}")
    replaced_names = set()

    def replacement_for(expression):
        if expression not in variable_names:
            return None
        replaced_names.add(expression)
        return replacement_factory(expression)

    def replacement_from_raw(raw_block):
        match = exact_variable_pattern.fullmatch(raw_block)
        if not match:
            return None
        return replacement_for(match.group(1))

    try:
        tokens = list(_jinja2_env.lex(text))
    except TemplateSyntaxError:
        return (
            exact_variable_pattern.sub(
                lambda match: replacement_for(match.group(1)) or match.group(0),
                text,
            ),
            replaced_names,
        )

    output = []
    current_tokens = None
    current_source = None

    for _, token_type, value in tokens:
        if token_type == "variable_begin":
            current_tokens = []
            current_source = [value]
            continue

        if token_type == "variable_end" and current_tokens is not None:
            raw_block = "".join(current_source) + value
            expression = "".join(current_tokens).strip()
            replacement = replacement_for(expression)
            if replacement is None:
                replacement = replacement_from_raw(raw_block)

            if replacement is None:
                output.append(raw_block)
            else:
                output.append(replacement)

            current_tokens = None
            current_source = None
            continue

        if current_tokens is not None:
            current_tokens.append(value)
            current_source.append(value)
        else:
            output.append(value)

    if current_tokens is not None:
        # Malformed variable block: leave the source untouched so the normal
        # template-syntax path reports the failure consistently.
        return text, set()

    return "".join(output), replaced_names


def process_string_content(
    content,
    column_info,
    context,
    image_counter,
    model_name,
    template_format=None,
    unresolved_placeholders=None,
    process_media=True,
    fail_closed: bool = False,
):
    """Process string-type content with proper media handling"""
    return process_text_with_media(
        content,
        column_info,
        context,
        image_counter,
        model_name,
        template_format=template_format,
        unresolved_placeholders=unresolved_placeholders,
        process_media=process_media,
        fail_closed=fail_closed,
    )


def process_text_with_media(
    text,
    column_info,
    context,
    image_counter,
    model_name,
    template_format=None,
    unresolved_placeholders=None,
    process_media=True,
    fail_closed: bool = False,
):
    """Process text content, handling both templates and media placeholders"""
    strict_render = False
    try:
        # Get the text and fix doubled-up quotes
        text = fix_double_quotes(text)
        image_markers = {}

        is_pdf = False
        pdf_url = ""
        pdf_name = ""

        # Replace image/audio placeholders with unique markers
        for col_id, info in column_info.items():
            if info["data_type"] in ["image", "audio"] and info["value"]:
                def make_media_marker(_placeholder_name):
                    nonlocal image_counter

                    marker = f"__{info['data_type'].upper()}_MARKER_{uuid.uuid4()}__"
                    image_markers[marker] = {
                        "url": info["value"],
                        "counter": image_counter,
                        "type": info["data_type"],
                    }
                    image_counter += 1
                    return marker

                text, _ = _replace_template_variable_placeholders(
                    text,
                    {col_id, info["name"]},
                    make_media_marker,
                )

            if info["data_type"] == "document" and info["value"]:
                def make_pdf_replacement(_placeholder_name):
                    # During the no-fetch validation pass, keep a synthetic PDF
                    # marker in the rendered text so callers know a second
                    # post-quota media materialization pass is still required.
                    if not process_media:
                        return f"__PDF_MARKER_{uuid.uuid4()}__"
                    return info["name"]

                text, replaced_document_placeholders = (
                    _replace_template_variable_placeholders(
                        text,
                        {col_id, info["name"]},
                        make_pdf_replacement,
                    )
                )

                if replaced_document_placeholders:
                    is_pdf = True
                    pdf_url = info["value"]
                    pdf_name = info["name"]

            # Handle multiple images (images data type)
            if info["data_type"] == "images" and info["value"]:
                try:
                    # Parse JSON array of image URLs
                    images_list = (
                        json.loads(info["value"])
                        if isinstance(info["value"], str)
                        else info["value"]
                    )
                    if not isinstance(images_list, list):
                        images_list = [images_list]

                    # Handle indexed syntax: {{column[0]}}, {{column[1]}}, etc.
                    for idx, img_url in enumerate(images_list):
                        indexed_patterns = [
                            f"{{{{{info['name']}[{idx}]}}}}",
                            f"{{{{{col_id}[{idx}]}}}}",
                        ]
                        for ph in indexed_patterns:
                            if ph in text:
                                marker = f"__IMAGE_MARKER_{uuid.uuid4()}__"
                                image_markers[marker] = {
                                    "url": img_url,
                                    "counter": image_counter,
                                    "type": "image",
                                }
                                text = text.replace(ph, marker)
                                image_counter += 1

                    # Handle full array syntax: {{column}} - include ALL images
                    def make_all_image_markers(_placeholder_name):
                        nonlocal image_counter

                        all_markers = ""
                        for img_url in images_list:
                            marker = f"__IMAGE_MARKER_{uuid.uuid4()}__"
                            image_markers[marker] = {
                                "url": img_url,
                                "counter": image_counter,
                                "type": "image",
                            }
                            all_markers += marker
                            image_counter += 1
                        return all_markers

                    text, _ = _replace_template_variable_placeholders(
                        text,
                        {col_id, info["name"]},
                        make_all_image_markers,
                    )
                except json.JSONDecodeError:
                    logger.warning(f"Failed to parse images array for column {col_id}")

        # Debug: Log the context keys and template for troubleshooting
        logger.debug(f"Template text (first 500 chars): {text[:500]}")
        logger.debug(f"Context keys: {list(context.keys())}")
        for key, value in context.items():
            if isinstance(value, dict):
                logger.debug(f"Context[{key}] is dict with keys: {list(value.keys())}")

        # IMPORTANT: Sanitize UUID placeholders BEFORE Jinja2 rendering
        # UUIDs contain hyphens which Jinja2 interprets as subtraction operators
        # e.g., {{a1b2c3d4-e5f6-...}} is parsed as "a1b2c3d4 - e5f6 - ..." (subtraction)
        # We replace hyphens with underscores to make valid Jinja2 identifiers
        placeholder_display_map = _build_placeholder_display_map(text)
        text = _sanitize_uuid_placeholders_for_template(text)
        logger.debug(f"Template after UUID sanitization: {text[:500]}")

        # Render template using multi-format renderer
        # Map frontend format names to backend constants
        effective_format = template_format or DEFAULT_TEMPLATE_FORMAT
        if effective_format == "jinja":
            effective_format = TEMPLATE_FORMAT_JINJA2
        strict_render = fail_closed and effective_format in (
            TEMPLATE_FORMAT_FSTRING,
            TEMPLATE_FORMAT_JINJA2,
            TEMPLATE_FORMAT_MUSTACHE,
        )
        render_unresolved_placeholders = (
            unresolved_placeholders if strict_render else None
        )
        try:
            processed_text = render_template(
                text,
                context,
                template_format=effective_format,
                strict=strict_render,
                placeholder_display_map=placeholder_display_map,
                unresolved_placeholders=render_unresolved_placeholders,
            )
        except (UnresolvedPromptPlaceholdersError, PromptTemplateSyntaxError):
            raise
        except Exception as render_error:
            logger.exception(
                f"Template rendering failed: {render_error}. Template: {text[:200]}..."
            )
            # Re-raise to see full error - template syntax issue
            raise
        if fail_closed and not process_media:
            if image_markers:
                _validate_media_marker_support(image_markers, model_name)
            if is_pdf:
                _validate_media_item_support({"type": "pdf_url"}, model_name)
        # Process media markers and create segments
        if image_markers and process_media:
            return process_media_markers(
                processed_text,
                image_markers,
                model_name,
                fail_closed=fail_closed,
            )
        else:
            response = []
            # No media, just return processed text
            if processed_text.strip():
                response.extend([{"type": "text", "text": processed_text}])
            else:
                response.extend([{"type": "text", "text": ""}])

            if is_pdf and process_media:
                response.append(
                    handle_media(
                        {
                            "type": "pdf_url",
                            "pdf_url": {
                                "url": pdf_url,
                                "pdf_name": pdf_name,
                                "file_name": pdf_name,
                            },
                        },
                        model_name,
                    )
                )

            return response
    except UnresolvedPromptPlaceholdersError:
        raise
    except PromptTemplateSyntaxError:
        raise
    except ValueError as e:
        logger.exception(f"Error VALUEERROR text with media: {e}")
        raise
    except Exception as e:
        logger.exception(f"Error processing text with media: {e}")
        logger.exception(f"Template text: {text[:200]}...")
        logger.exception(f"Context keys: {list(context.keys())}")
        if fail_closed:
            raise
        if strict_render:
            remaining_placeholders = [
                placeholder.strip()
                for placeholder in _JINJA_PLACEHOLDER_PATTERN.findall(text)
                if placeholder.strip()
            ]
            if remaining_placeholders:
                raise _format_unresolved_placeholder_error(
                    remaining_placeholders, locals().get("placeholder_display_map")
                ) from e
        # Fallback to original text
        response = [{"type": "text", "text": text}]
        if is_pdf and process_media:
            response.append(
                handle_media(
                    {
                        "type": "pdf_url",
                        "pdf_url": {
                            "url": pdf_url,
                            "pdf_name": pdf_name,
                            "file_name": pdf_name,
                        },
                    },
                    model_name,
                )
            )
        return response


def process_media_markers(text, image_markers, model_name, fail_closed: bool = False):
    """Process media markers in text and create appropriate segments"""
    segments = []
    current_text = text

    # Sort markers by their position in the text to process them in order
    marker_positions = []
    for marker in image_markers:
        pos = current_text.find(marker)
        if pos != -1:
            marker_positions.append((pos, marker))

    # Sort by position
    marker_positions.sort(key=lambda x: x[0])

    # Process markers in order
    for _pos, marker in marker_positions:
        info = image_markers[marker]

        # Find the marker in current text
        marker_pos = current_text.find(marker)
        if marker_pos == -1:
            continue

        # Add text before marker
        text_before = current_text[:marker_pos]
        if text_before.strip():
            segments.append({"type": "text", "text": text_before})

        # Add media content
        if info["type"] == "image":
            if not litellm.utils.supports_vision(model=model_name):
                raise ValueError(f"Model {model_name} does not support image input.")
            segments.append(
                {
                    "type": "text",
                    "text": f"Image Input_{info['counter']} is given below:",
                }
            )
            try:
                # Convert image to base64
                segments.append(
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": convert_image_from_url_to_base64(info["url"])
                        },
                    }
                )
            except Exception as e:
                logger.exception(f"Error converting image to base64: {e}")
                if fail_closed:
                    raise
                segments.append({"type": "image_url", "image_url": info["url"]})

        elif info["type"] == "audio":
            # Allow audio for models explicitly marked as audio (tts) or stt
            model_mode = get_model_mode(model_name)
            if model_mode not in (
                "audio",
                "stt",
                "tts",
            ) and not litellm.utils.supports_audio_input(model=model_name):
                raise ValueError(f"Model {model_name} does not support audio input.")
            segments.append(
                {
                    "type": "text",
                    "text": f"Audio Input_{info['counter']} is given below:",
                }
            )
            try:
                # Download and encode audio
                response = requests.get(info["url"], timeout=120)
                response.raise_for_status()

                bytes_data = response.content
                encoded_string = base64.b64encode(bytes_data).decode("utf-8")
                audio_type = detect_audio_format(bytes_data)

                segments.append(
                    {
                        "type": "input_audio",
                        "input_audio": {"data": encoded_string, "format": audio_type},
                    }
                )
            except ValueError as e:
                raise e

            except Exception as e:
                logger.exception(f"Error processing audio from {info['url']}: {e}")
                if fail_closed:
                    raise
                # segments.append({
                #     "type": "input_audio",
                #     "input_audio": f"[Error loading audio from {info['url']}]"
                # })

        # Update current text to remaining part
        current_text = current_text[marker_pos + len(marker) :]

    # Add any remaining text
    if current_text.strip():
        segments.append({"type": "text", "text": current_text})

    return segments


class LitellmAPIView(CreateAPIView):
    _gm = GeneralMethods()
    permission_classes = [IsAuthenticated]

    def process_row(self, row, validated_data, dataset, column, request):
        # Call litellm with the validated data
        status = CellStatus.PASS.value
        try:
            messages = populate_placeholders(
                validated_data.get("messages"),
                dataset_id=dataset.id,
                row_id=row.id,
                col_id=column.id,
                model_name=validated_data.get("model"),
                template_format=get_run_prompt_template_format(validated_data),
                fail_closed=True,
            )
            messages = remove_empty_text_from_messages(messages)

            run_prompt = RunPrompt(
                model=validated_data.get("model"),
                organization_id=getattr(request, "organization", None)
                or request.user.organization.id,
                messages=messages,
                temperature=validated_data.get("temperature"),
                frequency_penalty=validated_data.get("frequency_penalty"),
                presence_penalty=validated_data.get("presence_penalty"),
                max_tokens=validated_data.get("max_tokens"),
                top_p=validated_data.get("top_p"),
                response_format=validated_data.get("response_format"),
                tool_choice=validated_data.get("tool_choice"),
                tools=validated_data.get("tools"),
                output_format=validated_data.get("output_format"),
                run_prompt_config=validated_data.get("run_prompt_config"),
                workspace_id=dataset.workspace.id if dataset.workspace else None,
            )

            response, value_info = run_prompt.litellm_response()
            value_info["reason"] = value_info.get("data", {}).get("response")

        except Exception as e:
            logger.exception(f"Error in processing the row: {str(e)}")
            error_message = get_specific_error_message(e)
            response = error_message
            value_info = {"reason": error_message}
            status = CellStatus.ERROR.value

        # Create a Cell object for each processed row
        Cell.objects.update_or_create(
            dataset=dataset,
            column=column,
            row=row,
            defaults={
                "value_infos": json.dumps(value_info) if value_info else json.dumps({}),
                "value": str(response),
                "status": status,
            },
        )

    @validated_request(
        request_serializer=LitellmSerializer,
        responses={
            200: ModelHubStringResultResponseSerializer,
            **MODEL_HUB_ERROR_RESPONSES,
        },
        reject_unknown_fields=True,
    )
    def post(self, request, *args, **kwargs):
        from django.db import transaction

        validated_data = request.validated_data
        # `validated_request` owns request-shape validation; from here the view
        # handles only domain execution errors.
        organization = _request_organization(request)
        dataset = (
            _request_dataset_queryset(request)
            .filter(id=validated_data.get("dataset_id"))
            .first()
        )
        if not dataset:
            return self._gm.not_found("Dataset not found")
        # Retrieve tools based on the IDs from the validated data
        tool_ids = _extract_tool_ids(validated_data.get("tools"))
        tools = Tools.objects.filter(
            _request_workspace_filter(request),
            id__in=tool_ids,
            organization=organization,
            deleted=False,
        )

        # Use transaction to ensure atomicity
        with transaction.atomic():
            run_prompter = RunPrompter.objects.create(
                name=validated_data.get("name"),
                model=validated_data.get("model"),
                organization=organization,
                messages=validated_data.get("messages"),
                temperature=validated_data.get("temperature"),
                frequency_penalty=validated_data.get("frequency_penalty"),
                presence_penalty=validated_data.get("presence_penalty"),
                max_tokens=validated_data.get("max_tokens"),
                top_p=validated_data.get("top_p"),
                response_format=validated_data.get("response_format"),
                tool_choice=validated_data.get("tool_choice"),
                output_format=validated_data.get("output_format"),
                dataset=dataset,
                workspace=dataset.workspace,
                concurrency=validated_data.get("concurrency"),
                run_prompt_config=validated_data.get("run_prompt_config"),
                status=StatusType.NOT_STARTED.value,  # Start with NOT_STARTED
            )
            if tools:
                # Associate the tools with the RunPrompter instance
                run_prompter.tools.set(tools)

            run_prompter_id = str(run_prompter.id)

        # After transaction commits, trigger workflow and update status
        from model_hub.tasks.run_prompt import process_prompts_single

        try:
            # Set status to RUNNING before triggering workflow
            RunPrompter.objects.filter(id=run_prompter_id).update(
                status=StatusType.RUNNING.value
            )

            result = process_prompts_single.apply_async(
                args=({"type": "not_started", "prompt_id": run_prompter_id},)
            )
            logger.info(
                "run_prompt_workflow_started",
                run_prompt_id=run_prompter_id,
                workflow_id=str(result.id) if result else "None",
            )
        except Exception as e:
            logger.exception(
                "run_prompt_workflow_start_failed",
                run_prompt_id=run_prompter_id,
                error=str(e),
            )
            # Set status to FAILED if workflow couldn't start
            RunPrompter.objects.filter(id=run_prompter_id).update(
                status=StatusType.FAILED.value
            )
            return self._gm.internal_server_error_response(
                "Failed to start run prompt workflow"
            )

        return self._gm.success_response("success")


class RunPrompts:
    def __init__(self, run_prompt_id):
        self.run_prompt_id = run_prompt_id
        self.run_prompt_model = None
        self.tools_config = []
        logger.info(
            "RunPrompts_init",
            run_prompt_id=str(run_prompt_id),
        )

    def load_run_prompt_id(self):
        """Load run_prompt_model based on ID."""
        logger.info(
            "RunPrompts_load_run_prompt_id_started",
            run_prompt_id=str(self.run_prompt_id),
        )
        try:
            self.run_prompt_model = RunPrompter.objects.get(id=self.run_prompt_id)
            logger.info(
                "RunPrompts_load_run_prompt_id_model_loaded",
                run_prompt_id=str(self.run_prompt_id),
                model=self.run_prompt_model.model,
                status=self.run_prompt_model.status,
            )
            tools = (
                self.run_prompt_model.tools.all()
            )  # This will give you the related Tools instances
            for tool in tools:
                self.tools_config.append(tool.config)
            logger.info(
                "RunPrompts_load_run_prompt_id_tools_loaded",
                run_prompt_id=str(self.run_prompt_id),
                tools_count=len(self.tools_config),
            )
        except ObjectDoesNotExist:
            logger.error(
                "RunPrompts_load_run_prompt_id_not_found",
                run_prompt_id=str(self.run_prompt_id),
            )
            raise ValueError("Invalid run prompt ID or  does not exist.")  # noqa: B904

    def run_prompt(self, edit_mode=False):
        try:
            self.load_run_prompt_id()

            # Capture updated_at at start to detect if prompt was edited during processing
            start_updated_at = self.run_prompt_model.updated_at

            dataset = Dataset.objects.filter(id=self.run_prompt_model.dataset.id).get()
            self.is_editing = True if edit_mode else False

            if not self.is_editing:
                column_order = dataset.column_order
                column, created = create_run_prompt_column(
                    dataset=dataset,
                    source_id=self.run_prompt_id,
                    name=self.run_prompt_model.name,
                    output_format=self.run_prompt_model.output_format,
                    response_format=self.run_prompt_model.response_format,
                )
                if created:
                    column_order.append(str(column.id))
                    dataset.column_order = column_order
                    dataset.save()
            elif self.is_editing:
                column = Column.objects.filter(
                    source_id=self.run_prompt_id, dataset=self.run_prompt_model.dataset
                ).get()
                # Update column data_type in case response_format changed
                update_column_for_rerun(
                    column=column,
                    output_format=self.run_prompt_model.output_format,
                    response_format=self.run_prompt_model.response_format,
                    status=None,  # Don't change status here
                )

            rows = Row.objects.filter(
                dataset_id=self.run_prompt_model.dataset.id, deleted=False
            ).order_by("order")

            # Execute with a maximum of 5 threads
            # Wrap process_row with OTel context propagation for thread safety
            # This ensures trace context flows from Temporal activity into thread pool workers
            wrapped_process_row = wrap_for_thread(self.process_row)

            with ThreadPoolExecutor(
                max_workers=self.run_prompt_model.concurrency
            ) as executor:
                futures = [
                    executor.submit(wrapped_process_row, row, column) for row in rows
                ]

                # Ensure all futures complete
                for future in as_completed(futures):
                    future.result()  # This will raise exceptions if any occurred in a thread

            # Check if prompt was edited during processing by comparing updated_at
            # This prevents this workflow from overwriting status when a new workflow was started
            current_prompt = (
                RunPrompter.objects.filter(id=self.run_prompt_id)
                .values("status", "updated_at")
                .first()
            )

            if not current_prompt:
                logger.warning(
                    f"run_prompt {self.run_prompt_id} was deleted during processing"
                )
                return

            current_status = current_prompt["status"]
            current_updated_at = current_prompt["updated_at"]

            # Only set COMPLETED if:
            # 1. Status is still RUNNING
            # 2. updated_at hasn't changed (no edit happened during processing)
            if (
                current_status == StatusType.RUNNING.value
                and current_updated_at == start_updated_at
            ):
                RunPrompter.objects.filter(id=self.run_prompt_id).update(
                    status=StatusType.COMPLETED.value
                )
            else:
                # Either status changed or prompt was edited during processing
                # Don't overwrite - let the new workflow handle final status
                logger.info(
                    f"run_prompt {self.run_prompt_id} was modified during processing "
                    f"(status={current_status}, updated_at changed={current_updated_at != start_updated_at}). "
                    "Not setting to COMPLETED."
                )

        except Exception as e:
            # Set status to FAILED so it doesn't get stuck in RUNNING
            logger.exception(f"run_prompt failed for {self.run_prompt_id}: {e}")
            try:
                # Check current state before setting FAILED
                current_prompt = (
                    RunPrompter.objects.filter(id=self.run_prompt_id)
                    .values("status", "updated_at")
                    .first()
                )

                if not current_prompt:
                    logger.warning(f"run_prompt {self.run_prompt_id} was deleted")
                    raise

                current_status = current_prompt["status"]
                current_updated_at = current_prompt["updated_at"]

                # Only set FAILED if:
                # 1. Status is still RUNNING
                # 2. updated_at hasn't changed (if we captured it)
                should_set_failed = current_status == StatusType.RUNNING.value
                if should_set_failed and "start_updated_at" in dir():
                    should_set_failed = current_updated_at == start_updated_at

                if should_set_failed:
                    RunPrompter.objects.filter(id=self.run_prompt_id).update(
                        status=StatusType.FAILED.value
                    )
                else:
                    # Prompt was modified during processing - don't overwrite with FAILED
                    logger.info(
                        f"run_prompt {self.run_prompt_id} was modified during failed execution "
                        f"(status={current_status}). Not setting to FAILED."
                    )
            except Exception:
                pass
            raise

    def process_row(self, row, column, edit_mode=False):
        row_id = str(row.id)
        logger.info(
            "RunPrompts_process_row_started",
            run_prompt_id=str(self.run_prompt_id),
            row_id=row_id,
            column_id=str(column.id),
            edit_mode=edit_mode,
        )
        try:
            # Call litellm with the validated data
            if edit_mode:
                self.is_editing = True
            status = CellStatus.PASS.value
            is_llm_error = False
            api_call_log_row = None
            api_call_error_status_attempted = False
            cell_persisted = False
            cell = None

            def persist_api_call_terminal_status(target_status):
                nonlocal api_call_error_status_attempted
                if (
                    not api_call_log_row
                    or api_call_log_row.status
                    != APICallStatusChoices.PROCESSING.value
                ):
                    return False

                previous_status = api_call_log_row.status
                api_call_log_row.status = target_status

                for attempt in range(2):
                    try:
                        api_call_log_row.save()
                        if target_status == APICallStatusChoices.ERROR.value:
                            api_call_error_status_attempted = True
                        return True
                    except Exception:
                        if attempt == 0:
                            logger.warning(
                                "RunPrompts_process_row_api_call_terminal_status_save_retry",
                                run_prompt_id=str(self.run_prompt_id),
                                row_id=row_id,
                                target_status=target_status,
                            )
                            continue
                        break

                try:
                    manager = getattr(type(api_call_log_row), "objects", None)
                    if manager is not None and getattr(api_call_log_row, "id", None):
                        updated = manager.filter(
                            id=api_call_log_row.id,
                            status=APICallStatusChoices.PROCESSING.value,
                        ).update(status=target_status)
                        if updated:
                            if target_status == APICallStatusChoices.ERROR.value:
                                api_call_error_status_attempted = True
                            return True
                except Exception:
                    logger.exception(
                        "RunPrompts_process_row_api_call_terminal_status_update_failed",
                        run_prompt_id=str(self.run_prompt_id),
                        row_id=row_id,
                        target_status=target_status,
                    )

                api_call_log_row.status = previous_status
                if target_status == APICallStatusChoices.ERROR.value:
                    logger.error(
                        "RunPrompts_process_row_api_call_error_status_persist_failed",
                        run_prompt_id=str(self.run_prompt_id),
                        row_id=row_id,
                    )
                    raise RuntimeError("Failed to persist API call error status")

                logger.error(
                    "RunPrompts_process_row_api_call_success_status_persist_failed",
                    run_prompt_id=str(self.run_prompt_id),
                    row_id=row_id,
                    target_status=target_status,
                )
                raise RuntimeError("Failed to persist API call success status")

            def mark_persisted_cell_error(error_message):
                nonlocal status, response, value_info
                if not cell_persisted or cell is None:
                    raise RuntimeError(error_message)
                status = CellStatus.ERROR.value
                response = error_message
                value_info = {"reason": error_message}
                cell.value = str(response)
                cell.value_infos = json.dumps(value_info)
                cell.status = status
                if hasattr(cell, "save"):
                    cell.save()

            def emit_usage_event():
                # Dual-write: emit usage event for new billing system only after
                # placeholder validation, provider dispatch, cell persistence, and
                # SUCCESS api-call status persistence all succeed.
                try:
                    try:
                        from ee.usage.schemas.events import UsageEvent
                    except ImportError:
                        UsageEvent = None
                    try:
                        from ee.usage.services.emitter import emit
                    except ImportError:
                        emit = None

                    if emit is not None and UsageEvent is not None:
                        emit(
                            UsageEvent(
                                org_id=str(self.run_prompt_model.organization.id),
                                event_type=APICallTypeChoices.DATASET_RUN_PROMPT.value,
                                properties={
                                    "source": "dataset_run_prompt",
                                    "source_id": str(self.run_prompt_id),
                                },
                            )
                        )
                except Exception:
                    pass  # Metering failure must not break the action

            try:
                logger.info(
                    "RunPrompts_process_row_populating_placeholders",
                    run_prompt_id=str(self.run_prompt_id),
                    row_id=row_id,
                )
                validated_messages = populate_placeholders(
                    self.run_prompt_model.messages,
                    dataset_id=self.run_prompt_model.dataset.id,
                    row_id=row.id,
                    col_id=column.id,
                    model_name=self.run_prompt_model.model,
                    template_format=normalize_template_format(
                        (self.run_prompt_model.run_prompt_config or {}).get(
                            "template_format"
                        )
                    ),
                    process_media=False,
                    fail_closed=True,
                )
                logger.info(
                    "RunPrompts_process_row_placeholders_populated",
                    run_prompt_id=str(self.run_prompt_id),
                    row_id=row_id,
                )

                logger.info(
                    "RunPrompts_process_row_validating_api_call",
                    run_prompt_id=str(self.run_prompt_id),
                    row_id=row_id,
                )
                if log_and_deduct_cost_for_api_request is not None:
                    try:
                        api_call_config = {"reference_id": str(self.run_prompt_id)}
                        api_call_log_row = log_and_deduct_cost_for_api_request(
                            self.run_prompt_model.organization,
                            APICallTypeChoices.DATASET_RUN_PROMPT.value,
                            config=api_call_config,
                            workspace=row.dataset.workspace,
                        )
                        logger.info(
                            "RunPrompts_process_row_api_call_logged",
                            run_prompt_id=str(self.run_prompt_id),
                            row_id=row_id,
                            api_call_log_row_id=(
                                str(api_call_log_row.id) if api_call_log_row else None
                            ),
                        )
                    except Exception as api_err:
                        logger.error(
                            "RunPrompts_process_row_api_call_validation_error",
                            run_prompt_id=str(self.run_prompt_id),
                            row_id=row_id,
                            error=str(api_err),
                        )
                        raise ValueError("Error in API call validation")  # noqa: B904
                    if not api_call_log_row:
                        logger.error(
                            "RunPrompts_process_row_api_call_log_row_none",
                            run_prompt_id=str(self.run_prompt_id),
                            row_id=row_id,
                        )
                        raise ValueError("Error in API call validation")
                    elif (
                        api_call_log_row.status != APICallStatusChoices.PROCESSING.value
                    ):
                        error_message = get_error_for_api_status(
                            api_call_log_row.status
                        )
                        logger.error(
                            "RunPrompts_process_row_api_call_status_invalid",
                            run_prompt_id=str(self.run_prompt_id),
                            row_id=row_id,
                            status=api_call_log_row.status,
                            error_message=error_message,
                        )
                        raise ValueError(error_message)
                    elif (
                        api_call_log_row.status == APICallStatusChoices.PROCESSING.value
                    ):
                        logger.info(
                            "RunPrompts_process_row_api_call_status_processing",
                            run_prompt_id=str(self.run_prompt_id),
                            row_id=row_id,
                        )

                if _messages_require_media_processing(validated_messages):
                    messages = populate_placeholders(
                        self.run_prompt_model.messages,
                        dataset_id=self.run_prompt_model.dataset.id,
                        row_id=row.id,
                        col_id=column.id,
                        model_name=self.run_prompt_model.model,
                        template_format=normalize_template_format(
                            (self.run_prompt_model.run_prompt_config or {}).get(
                                "template_format"
                            )
                        ),
                        fail_closed=True,
                    )
                else:
                    messages = validated_messages
                messages = remove_empty_text_from_messages(messages)

                logger.info(
                    "RunPrompts_process_row_creating_run_prompt",
                    run_prompt_id=str(self.run_prompt_id),
                    row_id=row_id,
                    model=self.run_prompt_model.model,
                )

                run_prompt = RunPrompt(
                    model=self.run_prompt_model.model,
                    organization_id=self.run_prompt_model.organization.id,
                    messages=messages,
                    temperature=self.run_prompt_model.temperature,
                    frequency_penalty=self.run_prompt_model.frequency_penalty,
                    presence_penalty=self.run_prompt_model.presence_penalty,
                    max_tokens=None,  # Let run_prompt_config handle this
                    top_p=self.run_prompt_model.top_p,
                    response_format=self.run_prompt_model.response_format,
                    tool_choice=self.run_prompt_model.tool_choice,
                    tools=self.tools_config,
                    output_format=self.run_prompt_model.output_format,
                    run_prompt_config=self.run_prompt_model.run_prompt_config,
                    workspace_id=(
                        self.run_prompt_model.dataset.workspace.id
                        if self.run_prompt_model.dataset
                        and self.run_prompt_model.dataset.workspace
                        else None
                    ),
                )
                is_llm_error = True
                logger.info(
                    "RunPrompts_process_row_calling_litellm_response",
                    run_prompt_id=str(self.run_prompt_id),
                    row_id=row_id,
                )
                response, value_info = run_prompt.litellm_response()
                logger.info(
                    "RunPrompts_process_row_litellm_response_received",
                    run_prompt_id=str(self.run_prompt_id),
                    row_id=row_id,
                    response_length=len(str(response)) if response else 0,
                )
                value_info["reason"] = value_info.get("data", {}).get("response")

            except Exception as e:
                logger.exception(
                    "RunPrompts_process_row_error",
                    run_prompt_id=str(self.run_prompt_id),
                    row_id=row_id,
                    error=str(e),
                    is_llm_error=is_llm_error,
                )
                error_message = get_specific_error_message(e, is_llm_error)
                logger.error(
                    "RunPrompts_process_row_error_message",
                    run_prompt_id=str(self.run_prompt_id),
                    row_id=row_id,
                    error_message=error_message,
                )
                response = str(e)
                value_info = {"reason": error_message}
                status = CellStatus.ERROR.value

            # if status == CellStatus.ERROR.value:
            #     try:
            #         if api_call_log_row:
            #             api_call_log_row.status = APICallStatusChoices.ERROR.value
            #             api_call_log_row.save()
            #         refund_config = {"evaluation_id": str(self.user_eval_metric_id)}
            #         refund_cost_for_api_call(api_call_log_row, config=refund_config)
            #     except Exception as e:
            #         print(f"Error refunding cost for api call: {str(e)}")
            # else:
            #     try:
            #         if api_call_log_row:
            #             api_call_log_row.status = APICallStatusChoices.SUCCESS.value
            #             api_call_log_row.save()
            #             print(
            #                 f"Updated api call status to processed: {api_call_log_row.id}"
            #             )
            #     except Exception as e:
            #         print(f"Error updating api call status to processed: {str(e)}")

            if self.is_editing:
                logger.info(
                    "RunPrompts_process_row_editing_mode_saving_cell",
                    run_prompt_id=str(self.run_prompt_id),
                    row_id=row_id,
                )
                try:
                    # First try to get the existing cell
                    cell = Cell.objects.get(
                        dataset=self.run_prompt_model.dataset,
                        column=column,
                        row=row,
                        deleted=False,  # Add this to ensure we only get active cells
                    )
                    logger.info(
                        "RunPrompts_process_row_existing_cell_found",
                        run_prompt_id=str(self.run_prompt_id),
                        row_id=row_id,
                        cell_id=str(cell.id),
                    )
                    # Update the existing cell
                    # Note: Media (image/audio) is already uploaded to S3 in litellm_response()
                    cell.value = str(response)
                    cell.value_infos = (
                        json.dumps(value_info) if value_info else json.dumps({})
                    )
                    cell.status = status

                    if value_info:
                        cell.prompt_tokens = (
                            value_info.get("metadata", {})
                            .get("usage", {})
                            .get("prompt_tokens", None)
                        )
                        cell.completion_tokens = (
                            value_info.get("metadata", {})
                            .get("usage", {})
                            .get("completion_tokens", None)
                        )
                        cell.response_time = value_info.get("metadata", {}).get(
                            "response_time", None
                        )

                    cell.save()
                    cell_persisted = True
                    logger.info(
                        "cell_updated",
                        cell_id=str(cell.id),
                        row_id=row_id,
                        run_prompt_id=str(self.run_prompt_id),
                        status=status,
                    )
                except Cell.DoesNotExist:
                    logger.info(
                        "RunPrompts_process_row_cell_not_found_creating_new",
                        run_prompt_id=str(self.run_prompt_id),
                        row_id=row_id,
                    )
                    # Create a new cell if none exists
                    prompt_tokens = None
                    completion_tokens = None
                    response_time = None
                    if value_info:
                        prompt_tokens = (
                            value_info.get("metadata", {})
                            .get("usage", {})
                            .get("prompt_tokens", None)
                        )
                        completion_tokens = (
                            value_info.get("metadata", {})
                            .get("usage", {})
                            .get("completion_tokens", None)
                        )
                        response_time = value_info.get("metadata", {}).get(
                            "response_time", None
                        )

                    cell = Cell.objects.create(
                        dataset=self.run_prompt_model.dataset,
                        column=column,
                        row=row,
                        value=str(response),
                        value_infos=(
                            json.dumps(value_info) if value_info else json.dumps({})
                        ),
                        status=status,
                        prompt_tokens=prompt_tokens,
                        completion_tokens=completion_tokens,
                        response_time=response_time,
                    )
                    cell_persisted = True
                    logger.info(
                        "cell_created_in_edit_mode",
                        cell_id=str(cell.id),
                        row_id=row_id,
                        run_prompt_id=str(self.run_prompt_id),
                        status=status,
                    )
            else:
                logger.info(
                    "RunPrompts_process_row_creating_new_cell",
                    run_prompt_id=str(self.run_prompt_id),
                    row_id=row_id,
                )
                prompt_tokens = (None,)
                completion_tokens = (None,)
                response_time = (None,)
                if value_info:
                    prompt_tokens = (
                        value_info.get("metadata", {})
                        .get("usage", {})
                        .get("prompt_tokens", None)
                    )
                    completion_tokens = (
                        value_info.get("metadata", {})
                        .get("usage", {})
                        .get("completion_tokens", None)
                    )
                    response_time = value_info.get("metadata", {}).get(
                        "response_time", None
                    )

                # Create a Cell object for each processed row
                # Note: Media (image/audio) is already uploaded to S3 in litellm_response()
                cell = Cell.objects.create(
                    dataset=self.run_prompt_model.dataset,
                    column=column,
                    row=row,
                    value=str(response),
                    value_infos=(
                        json.dumps(value_info) if value_info else json.dumps({})
                    ),
                    status=status,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    response_time=response_time,
                )
                cell_persisted = True
                logger.info(
                    "cell_created",
                    cell_id=str(cell.id),
                    row_id=row_id,
                    run_prompt_id=str(self.run_prompt_id),
                    status=status,
                )
            if status == CellStatus.ERROR.value and cell_persisted:
                if persist_api_call_terminal_status(APICallStatusChoices.ERROR.value):
                    logger.info(
                        "RunPrompts_process_row_api_call_status_set_error",
                        run_prompt_id=str(self.run_prompt_id),
                        row_id=row_id,
                    )
            elif status == CellStatus.PASS.value:
                try:
                    if persist_api_call_terminal_status(
                        APICallStatusChoices.SUCCESS.value
                    ):
                        logger.info(
                            "RunPrompts_process_row_api_call_status_set_success",
                            run_prompt_id=str(self.run_prompt_id),
                            row_id=row_id,
                        )
                        emit_usage_event()
                except Exception as terminal_status_error:
                    terminal_error_message = (
                        "Provider response was saved, but final API call status "
                        f"could not be persisted: {terminal_status_error}"
                    )
                    mark_persisted_cell_error(terminal_error_message)
                    if persist_api_call_terminal_status(
                        APICallStatusChoices.ERROR.value
                    ):
                        logger.info(
                            "RunPrompts_process_row_api_call_status_set_error",
                            run_prompt_id=str(self.run_prompt_id),
                            row_id=row_id,
                        )

            logger.info(
                "RunPrompts_process_row_completed",
                run_prompt_id=str(self.run_prompt_id),
                row_id=row_id,
                status=status,
            )
        except Exception as e:
            logger.exception(
                "RunPrompts_process_row_fatal_error",
                run_prompt_id=str(self.run_prompt_id),
                row_id=row_id,
                error=str(e),
            )
            if (
                "persist_api_call_terminal_status" in locals()
                and not api_call_error_status_attempted
            ):
                try:
                    if persist_api_call_terminal_status(
                        APICallStatusChoices.ERROR.value
                    ):
                        logger.info(
                            "RunPrompts_process_row_api_call_status_set_error",
                            run_prompt_id=str(self.run_prompt_id),
                            row_id=row_id,
                        )
                except Exception:
                    logger.exception(
                        "RunPrompts_process_row_api_call_error_status_after_fatal_failed",
                        run_prompt_id=str(self.run_prompt_id),
                        row_id=row_id,
                    )
            raise
        finally:
            logger.info(
                "RunPrompts_process_row_cleanup",
                run_prompt_id=str(self.run_prompt_id),
                row_id=row_id,
            )
            close_old_connections()

    def empty_column(self, column):
        cells = Cell.objects.filter(
            dataset=self.run_prompt_model.dataset, column=column, deleted=False
        ).all()

        for cell in cells:
            cell.value = ""  # Empty string instead of None since it's a TextField
            cell.value_infos = json.dumps({})  # Default empty list for JSONField
            cell.status = CellStatus.RUNNING.value
            cell.save()


class AddRunPromptColumnView(APIView):
    _gm = GeneralMethods()
    permission_classes = [IsAuthenticated]

    @validated_request(
        request_serializer=AddRunPromptSerializer,
        responses={
            200: DevelopDatasetMessageResponseSerializer,
            **MODEL_HUB_ERROR_RESPONSES,
        },
        reject_unknown_fields=True,
    )
    def post(self, request, *args, **kwargs):
        from django.db import transaction

        try:
            validated_data = request.validated_data
            dataset_id = validated_data["dataset_id"]
            name = validated_data["name"]
            config = validated_data[
                "config"
            ]  # This is now a validated dict from PromptConfigSerializer
            run_prompt_config = normalize_run_prompt_config(config)

            # Get dataset and enforce organization isolation
            organization = (
                getattr(request, "organization", None) or request.user.organization
            )
            try:
                dataset = Dataset.objects.get(id=dataset_id)
            except Dataset.DoesNotExist:
                return self._gm.not_found("Dataset not found")

            if dataset.organization_id != organization.id:
                return self._gm.not_found("Dataset not found")

            if Column.objects.filter(
                name=name, dataset=dataset, deleted=False
            ).exists():
                return self._gm.bad_request(get_error_message("COLUMN_NAME_EXISTS"))

            output_format = config.get("output_format")
            messages = config.get("messages", [])
            if output_format != "audio":
                messages = remove_empty_text_from_messages(messages)

            # Use transaction to ensure atomicity of all DB operations
            # Create with NOT_STARTED first, then set RUNNING only after workflow starts
            with transaction.atomic():
                run_prompter = RunPrompter.objects.create(
                    name=name,
                    model=config.get(
                        "model", ""
                    ),  # Add default values for potentially None fields
                    organization=getattr(request, "organization", None)
                    or request.user.organization,
                    messages=messages,  # Default empty message
                    temperature=run_prompt_config.get("temperature"),
                    frequency_penalty=run_prompt_config.get("frequency_penalty"),
                    presence_penalty=run_prompt_config.get("presence_penalty"),
                    max_tokens=run_prompt_config.get("max_tokens"),
                    top_p=run_prompt_config.get("top_p"),
                    response_format=config.get("response_format"),
                    tool_choice=config.get("tool_choice"),
                    output_format=config.get(
                        "output_format", "string"
                    ),  # Default to string if not specified
                    dataset=dataset,
                    run_prompt_config=run_prompt_config,
                    concurrency=config.get("concurrency", 5),
                    status=StatusType.NOT_STARTED.value,  # Start with NOT_STARTED
                )
                column_order = dataset.column_order

                column, created = create_run_prompt_column(
                    dataset=dataset,
                    source_id=run_prompter.id,
                    name=run_prompter.name,
                    output_format=run_prompter.output_format,
                    response_format=run_prompter.response_format,
                )
                if created:
                    column_order.append(str(column.id))
                    dataset.column_order = column_order
                    dataset.save()

                # Handle tools if provided in config
                tools = config.get("tools", [])
                if tools:
                    tool_ids = [tool.get("id") for tool in tools if "id" in tool]
                    if tool_ids:
                        tools_queryset = Tools.objects.filter(id__in=tool_ids)
                        run_prompter.tools.set(tools_queryset)

                run_prompter_id = str(run_prompter.id)

            # After transaction commits, trigger workflow and update status
            from model_hub.tasks.run_prompt import process_prompts_single

            try:
                # Set status to RUNNING before triggering workflow
                RunPrompter.objects.filter(id=run_prompter_id).update(
                    status=StatusType.RUNNING.value
                )

                result = process_prompts_single.apply_async(
                    args=({"type": "not_started", "prompt_id": run_prompter_id},)
                )
                logger.info(
                    "run_prompt_workflow_started",
                    run_prompt_id=run_prompter_id,
                    workflow_id=str(result.id) if result else "None",
                )
            except Exception as e:
                logger.exception(
                    "run_prompt_workflow_start_failed",
                    run_prompt_id=run_prompter_id,
                    error=str(e),
                )
                # Set status to FAILED if workflow couldn't start
                RunPrompter.objects.filter(id=run_prompter_id).update(
                    status=StatusType.FAILED.value
                )
                return self._gm.internal_server_error_response(
                    "Failed to start run prompt workflow"
                )

            return self._gm.success_response("Run prompt column added successfully")

        except Exception as e:
            traceback.print_exc()
            error_message = get_specific_error_message(e)
            logger.exception(f"Error in adding run prompt column: {error_message}")
            return self._gm.internal_server_error_response(error_message)


class PreviewRunPromptColumnView(APIView):
    _gm = GeneralMethods()
    permission_classes = [IsAuthenticated]

    @validated_request(
        request_serializer=PreviewRunPromptSerializer,
        responses={
            200: RunPromptColumnPreviewResponseSerializer,
            **MODEL_HUB_ERROR_RESPONSES,
        },
        reject_unknown_fields=True,
    )
    def post(self, request, *args, **kwargs):
        try:
            validated_data = request.validated_data
            dataset_id = validated_data["dataset_id"]
            config = validated_data["config"]

            first_n_rows = validated_data.get("first_n_rows")
            orders = list(
                Row.objects.filter(dataset_id=dataset_id, deleted=False)
                .order_by("order")
                .values_list("order", flat=True)
            )
            if first_n_rows:
                row_indices = orders[:first_n_rows]
            else:
                row_indices = []
                for index in validated_data["row_indices"]:
                    if 0 <= index - 1 < len(orders):
                        row_indices.append(orders[index - 1])

            # Get dataset and selected rows
            dataset = Dataset.objects.filter(id=dataset_id, deleted=False).first()

            # Enforce organization isolation
            if not dataset:
                return self._gm.not_found("Dataset not found")
            if (
                dataset.organization_id
                != (
                    getattr(request, "organization", None) or request.user.organization
                ).id
            ):
                return self._gm.not_found("Dataset not found")

            rows = Row.objects.filter(
                dataset_id=dataset_id, order__in=row_indices, deleted=False
            )

            if not rows:
                return self._gm.bad_request(get_error_message("ROW_INDICES_NOT_EXIST"))

            # Process tools if provided in config
            tools_config = []
            if config.get("tools"):
                tool_ids = [tool.get("id") for tool in config["tools"] if "id" in tool]
                if tool_ids:
                    tools = Tools.objects.filter(id__in=tool_ids)
                    tools_config = [tool.config for tool in tools]

            rf = config.get("response_format")
            if rf and not isinstance(rf, dict):
                try:
                    uuid.UUID(rf, version=4)
                    rf = UserResponseSchema.objects.get(id=rf)
                    rf = rf.schema
                except Exception:
                    pass

            run_prompt_config = normalize_run_prompt_config(config)
            responses = []
            for row in rows:
                try:
                    output_format = config.get("output_format", "string")
                    template_format = get_run_prompt_template_format(
                        {"run_prompt_config": run_prompt_config}
                    )
                    validated_messages = populate_placeholders(
                        config.get("messages", []),
                        dataset_id=dataset_id,
                        row_id=row.id,
                        col_id=None,
                        model_name=config.get("model", ""),
                        template_format=template_format,
                        process_media=False,
                        fail_closed=True,
                    )
                    if _messages_require_media_processing(validated_messages):
                        messages = populate_placeholders(
                            config.get("messages", []),
                            dataset_id=dataset_id,
                            row_id=row.id,
                            col_id=None,
                            model_name=config.get("model", ""),
                            template_format=template_format,
                            fail_closed=True,
                        )
                    else:
                        messages = validated_messages
                    if output_format != "audio":
                        messages = remove_empty_text_from_messages(messages)

                    run_prompt = RunPrompt(
                        model=config.get("model", ""),
                        organization_id=getattr(request, "organization", None)
                        or request.user.organization.id,
                        messages=messages,
                        temperature=config.get("temperature"),
                        frequency_penalty=config.get("frequency_penalty"),
                        presence_penalty=config.get("presence_penalty"),
                        max_tokens=config.get("max_tokens"),
                        top_p=config.get("top_p"),
                        response_format=rf,
                        tool_choice=config.get("tool_choice"),
                        tools=tools_config,
                        output_format=config.get("output_format", "string"),
                        run_prompt_config=run_prompt_config,
                        workspace_id=(
                            dataset.workspace.id
                            if dataset and dataset.workspace
                            else None
                        ),
                    )
                    response, value_infos = run_prompt.litellm_response()

                    # Check if showReasoningProcess is enabled to include thinking content
                    reasoning_config = run_prompt_config.get("reasoning", {})
                    show_reasoning = reasoning_config.get(
                        "showReasoningProcess"
                    ) or reasoning_config.get("show_reasoning_process")

                    if show_reasoning:
                        # Use value_infos["data"]["response"] which includes thinking content
                        response_with_thinking = value_infos.get("data", {}).get(
                            "response", response
                        )
                        responses.append(response_with_thinking)
                    else:
                        responses.append(response)

                except Exception as e:
                    responses.append(str(e))
                    value_infos = {"metadata": {"usage": {}, "cost": {}}}
            return self._gm.success_response(
                {
                    "responses": responses,
                    "token_usage": value_infos.get("metadata", {}).get("usage", {}),
                    "cost": value_infos.get("metadata", {}).get("cost", {}),
                }
            )

        except Exception as e:
            error_message = get_specific_error_message(e)
            logger.exception(f"Error in preview run prompt column: {error_message}")
            return self._gm.internal_server_error_response(error_message)


class EditRunPromptColumnView(APIView):
    _gm = GeneralMethods()
    permission_classes = [IsAuthenticated]

    @validated_request(
        request_serializer=EditRunPromptColumnSerializer,
        responses={
            200: DevelopDatasetMessageResponseSerializer,
            **MODEL_HUB_ERROR_RESPONSES,
        },
        reject_unknown_fields=True,
    )
    def post(self, request, *args, **kwargs):
        from django.db import transaction

        try:
            validated_data = request.validated_data
            dataset_id = validated_data["dataset_id"]
            column_id = validated_data["column_id"]
            config = validated_data.get("config") or {}
            name = validated_data.get("name")

            dataset = _request_dataset_queryset(request).filter(id=dataset_id).first()
            if not dataset:
                return self._gm.not_found("Dataset not found")

            column = (
                _request_column_queryset(request)
                .filter(id=column_id, dataset=dataset)
                .first()
            )
            if not column:
                return self._gm.not_found("Column or dataset not found")

            # Verify column is a run prompt column
            if column.source != SourceChoices.RUN_PROMPT.value:
                return self._gm.bad_request(get_error_message("COLUMN_IS_IN_VALID"))

            # Lock the RunPrompter row to prevent race conditions
            # Use of=('self',) to avoid issues with nullable foreign keys causing outer joins
            with transaction.atomic():
                run_prompter = (
                    _request_run_prompter_queryset(request)
                    .select_for_update(of=("self",))
                    .filter(id=column.source_id, dataset=dataset)
                    .first()
                )
                if not run_prompter:
                    return self._gm.not_found(
                        "Column or run prompt configuration not found"
                    )

                # Check if currently running - warn but allow edit
                was_running = run_prompter.status == StatusType.RUNNING.value
                if was_running:
                    logger.warning(
                        "edit_run_prompt_while_running",
                        run_prompt_id=str(run_prompter.id),
                        message="Editing run prompt while it's running. Current run will be cancelled.",
                    )

                Cell.objects.filter(column=column).update(
                    value="",
                    value_infos=json.dumps({}),
                    status=CellStatus.RUNNING.value,
                )

                run_prompt_config = merge_run_prompt_config(
                    run_prompter.run_prompt_config, config
                )
                messages = config.get("messages", run_prompter.messages)
                output_format = config.get("output_format", run_prompter.output_format)

                if output_format != "audio":
                    messages = remove_empty_text_from_messages(messages)

                # Update RunPrompter instance
                run_prompter.name = name if name is not None else run_prompter.name
                run_prompter.model = config.get("model", run_prompter.model)
                run_prompter.messages = messages
                run_prompter.temperature = run_prompt_config.get(
                    "temperature", run_prompter.temperature
                )
                run_prompter.frequency_penalty = run_prompt_config.get(
                    "frequency_penalty", run_prompter.frequency_penalty
                )
                run_prompter.presence_penalty = run_prompt_config.get(
                    "presence_penalty", run_prompter.presence_penalty
                )
                run_prompter.max_tokens = run_prompt_config.get(
                    "max_tokens", run_prompter.max_tokens
                )
                run_prompter.top_p = run_prompt_config.get("top_p", run_prompter.top_p)
                run_prompter.response_format = config.get(
                    "response_format", run_prompter.response_format
                )
                run_prompter.tool_choice = config.get(
                    "tool_choice", run_prompter.tool_choice
                )
                run_prompter.output_format = config.get(
                    "output_format", run_prompter.output_format
                )
                run_prompter.concurrency = config.get(
                    "concurrency", run_prompter.concurrency
                )
                run_prompter.status = (
                    StatusType.RUNNING.value
                )  # Set to RUNNING immediately

                run_prompter.run_prompt_config = run_prompt_config

                # Handle tools update - first clear existing tools
                run_prompter.tools.clear()

                # Handle tools update if provided
                tools = config.get("tools")
                if tools:
                    tool_ids = [tool.get("id") for tool in tools if "id" in tool]
                    if tool_ids:
                        tools_queryset = Tools.objects.filter(id__in=tool_ids)
                        run_prompter.tools.set(tools_queryset)

                run_prompter.save()

                # Update column
                update_column_for_rerun(
                    column=column,
                    output_format=run_prompter.output_format,
                    response_format=run_prompter.response_format,
                    name=name if name is not None else run_prompter.name,
                    status=None,  # Don't update status here
                )

                # Store run_prompter id for triggering workflow after transaction
                run_prompter_id = str(run_prompter.id)

            # Directly trigger the Temporal workflow after transaction commits
            from model_hub.tasks.run_prompt import process_prompts_single

            try:
                result = process_prompts_single.apply_async(
                    args=({"type": "editing", "prompt_id": run_prompter_id},)
                )
                logger.info(
                    "run_prompt_edit_workflow_started",
                    run_prompt_id=run_prompter_id,
                    workflow_id=str(result.id) if result else "None",
                )
            except Exception as e:
                logger.exception(
                    "run_prompt_edit_workflow_start_failed",
                    run_prompt_id=run_prompter_id,
                    error=str(e),
                )
                # Set status to FAILED if workflow couldn't start and terminalize
                # cells that were already blanked/requeued in the edit transaction.
                RunPrompter.objects.filter(id=run_prompter_id).update(
                    status=StatusType.FAILED.value
                )
                Cell.objects.filter(column=column).update(
                    value=None,
                    value_infos=json.dumps(
                        {"reason": "Failed to start run prompt workflow"}
                    ),
                    status=CellStatus.ERROR.value,
                )
                return self._gm.internal_server_error_response(
                    "Failed to start run prompt workflow"
                )

            return self._gm.success_response("Run prompt column updated successfully")

        except Http404:
            return self._gm.not_found("Column or dataset not found")
        except Exception as e:
            error_message = get_specific_error_message(e)
            logger.exception(f"Error in updating run prompt column: {error_message}")
            return self._gm.internal_server_error_response(error_message)


class RetrieveRunPromptColumnConfigView(APIView):
    _gm = GeneralMethods()
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        responses={
            200: RunPromptColumnConfigResponseSerializer,
            **MODEL_HUB_ERROR_RESPONSES,
        }
    )
    def get(self, request):
        try:
            # Get the column and verify it's a run prompt column
            column_id = request.query_params.get("column_id")
            column = _request_column_queryset(request).filter(id=column_id).first()
            if not column:
                return self._gm.not_found(
                    "Column or run prompt configuration not found"
                )

            if column.source != SourceChoices.RUN_PROMPT.value:
                return self._gm.bad_request(get_error_message("COLUMN_IS_IN_VALID"))

            # Get associated RunPrompter instance
            run_prompter = (
                _request_run_prompter_queryset(request)
                .filter(id=column.source_id, dataset=column.dataset)
                .first()
            )
            if not run_prompter:
                return self._gm.not_found(
                    "Column or run prompt configuration not found"
                )

            # Get tools configuration
            tools = []
            for tool in run_prompter.tools.all():
                tools.append(
                    {"id": str(tool.id), "name": tool.name, "config": tool.config}
                )
            base_run_prompt_config = dict(run_prompter.run_prompt_config or {})
            if base_run_prompt_config.get("template_format"):
                base_run_prompt_config["template_format"] = (
                    normalize_public_template_format(
                        base_run_prompt_config["template_format"]
                    )
                )

            if not base_run_prompt_config.get("model_type"):
                # Determine model_type based on output_format
                model_type = "tts" if run_prompter.output_format == "audio" else "llm"
            else:
                model_type = run_prompter.run_prompt_config.get("model_type")

            run_prompt_config = {
                **base_run_prompt_config,
                "temperature": run_prompter.temperature,
                "frequency_penalty": run_prompter.frequency_penalty,
                "presence_penalty": run_prompter.presence_penalty,
                "top_p": run_prompter.top_p,
                "model_type": model_type,
            }

            # Convert any column UUIDs in messages back to column names for display in editor
            converted_messages = convert_uuids_to_column_names(
                run_prompter.messages, str(run_prompter.dataset.id)
            )

            config = {
                "dataset_id": str(run_prompter.dataset.id),
                "name": run_prompter.name,
                "model": run_prompter.model,
                "messages": converted_messages,
                "temperature": run_prompter.temperature,
                "frequency_penalty": run_prompter.frequency_penalty,
                "presence_penalty": run_prompter.presence_penalty,
                "max_tokens": run_prompter.max_tokens,
                "top_p": run_prompter.top_p,
                "response_format": run_prompter.response_format,
                "tool_choice": run_prompter.tool_choice,
                "tools": tools,
                "output_format": run_prompter.output_format,
                "concurrency": run_prompter.concurrency,
                "run_prompt_config": run_prompt_config,
            }

            return self._gm.success_response({"config": config})

        except Http404:
            return self._gm.not_found("Column or run prompt configuration not found")
        except Exception as e:
            error_message = get_specific_error_message(e)
            logger.exception(f"Error in fetching run prompt column: {error_message}")
            return self._gm.internal_server_error_response(error_message)


class DefaultProviderView(APIView):
    _gm = GeneralMethods()
    permission_classes = [IsAuthenticated]

    def get(self, request, *args, **kwargs):
        """
        Get the default provider configuration for the authenticated user's organization
        """
        try:
            # Get the organization's default provider settings
            api_key = ApiKey.objects.filter(
                organization=getattr(request, "organization", None)
                or request.user.organization,
                is_default=True,
            ).first()

            if api_key:
                data = {"provider": api_key.provider, "key": api_key.key}
                return self._gm.success_response(data)

            return self._gm.not_found(get_error_message("PROVIDER_CONFIG_NOT_FOUND"))

        except Exception as e:
            error_message = get_specific_error_message(e)
            logger.exception(
                f"Error in fetching provider's configurations: {error_message}"
            )
            return self._gm.internal_server_error_response(error_message)

    def post(self, request, *args, **kwargs):
        """
        Set a provider as default for the authenticated user's organization
        """
        try:
            provider = request.data.get("provider")

            if not provider:
                return self._gm.bad_request(get_error_message("PROVIDER_MISSING"))

            # Reset all providers to non-default
            ApiKey.objects.filter(
                organization=getattr(request, "organization", None)
                or request.user.organization
            ).update(is_default=False)

            # Set the specified provider as default
            api_key = ApiKey.objects.filter(
                organization=getattr(request, "organization", None)
                or request.user.organization,
                provider=provider,
            ).first()

            if not api_key:
                return self._gm.not_found(get_error_message("PROVIDER_NOT_FOUND"))

            api_key.is_default = True
            api_key.save()

            return self._gm.success_response("Default provider updated successfully")

        except Exception as e:
            error_message = get_specific_error_message(e)
            logger.exception(f"Error in setting provider as default: {error_message}")
            return self._gm.internal_server_error_response(error_message)


class RetrieveRunPromptOptionsView(APIView):
    _gm = GeneralMethods()
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        responses={200: RunPromptOptionsResponseSerializer, **MODEL_HUB_ERROR_RESPONSES}
    )
    def get(self, request, *args, **kwargs):
        try:
            # Get available models from LiteLLM model manager
            model_manager = LiteLLMModelManager(
                model_name="",
                organization_id=getattr(self.request, "organization", None)
                or self.request.user.organization.id,
            )
            available_models = model_manager.models

            # Get provider status
            providers = LiteLlmModelProvider.get_choices()
            existing_keys = ApiKey.objects.filter(
                organization=getattr(request, "organization", None)
                or request.user.organization
            ).values_list("provider", flat=True)

            # Create provider lookup dictionary directly
            provider_has_key = {
                provider[0]: provider[0] in existing_keys for provider in providers
            }

            # Add is_available based on provider status
            for model in available_models:
                provider = model.get("providers")
                model["is_available"] = provider_has_key.get(provider, False)

            # Get available tools for the organization
            given_tools = Tools.objects.filter(
                organization=getattr(request, "organization", None)
                or request.user.organization
            ).values("id", "name", "config", "config_type", "description")
            tools = []
            for tool in given_tools:
                yaml_config = None
                if tool.get("config_type") == "yaml":
                    yaml_config = yaml.dump(
                        tool.get("config"), default_flow_style=False
                    )
                    config = tool.get("config")
                else:
                    config = tool.get("config")
                tools.append(
                    {
                        "id": tool.get("id"),
                        "name": tool.get("name"),
                        "yaml_config": yaml_config,
                        "config": config,
                        "config_type": tool.get("config_type"),
                        "description": tool.get("description"),
                    }
                )

            # Get output format choices from RunPrompter model
            output_format_choices = [
                {"value": choice[0], "label": choice[1]}
                for choice in RunPrompter.OUTPUT_FORMAT_CHOICES
            ]

            # Get tool choice options from RunPrompter model
            tool_choices = [
                {"value": choice[0], "label": choice[1]}
                for choice in RunPrompter.TOOL_CHOICES
                if choice[0] is not None
            ]
            empty_tool = (
                Tools()
            )  # Creates a new empty Tools instance with default fields

            # Prepare data for serialization
            data = {
                "models": available_models,
                "tool_config": empty_tool.config,
                "available_tools": list(tools),
                "output_formats": output_format_choices,
                "tool_choices": tool_choices,
            }

            return self._gm.success_response(data)

        except Exception as e:
            error_message = get_specific_error_message(e)
            logger.exception(f"Error in fetching run prompt options: {error_message}")
            return self._gm.internal_server_error_response(error_message)


class DatasetRunPromptStatsView(APIView):
    permission_classes = [IsAuthenticated]
    _gm = GeneralMethods()

    @swagger_auto_schema(
        responses={
            200: DatasetRunPromptStatsResponseSerializer,
            **MODEL_HUB_ERROR_RESPONSES,
        }
    )
    def get(self, request, dataset_id):
        try:
            # Enforce organization isolation - verify dataset belongs to user's org
            dataset = Dataset.objects.filter(id=dataset_id, deleted=False).first()
            if (
                not dataset
                or dataset.organization_id
                != (
                    getattr(request, "organization", None) or request.user.organization
                ).id
            ):
                return self._gm.not_found("Dataset not found")

            # Get all run prompt columns for this dataset
            prompt_ids = request.query_params.get("prompt_ids", "")

            if prompt_ids and len(prompt_ids) > 0:
                prompt_ids = prompt_ids.split(",")
                run_prompters = RunPrompter.objects.filter(
                    id__in=prompt_ids, dataset_id=dataset_id, deleted=False
                )

                if len(run_prompters) == 0:
                    return self._gm.success_response(
                        {"avg_tokens": 0, "avg_cost": 0, "avg_time": 0, "prompts": []}
                    )
            else:
                run_prompters = RunPrompter.objects.filter(
                    dataset_id=dataset_id, deleted=False
                )

            response = get_prompt_stats(run_prompters, dataset_id)

            return self._gm.success_response(response)

        except Exception as e:
            error_message = get_specific_error_message(e)
            logger.exception(f"Error in fetching run prompt data: {error_message}")
            return self._gm.bad_request(error_message)


class LiteLLMModelListView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        responses={
            200: ModelHubPaginatedResponseSerializer,
            **MODEL_HUB_ERROR_RESPONSES,
        }
    )
    def get(self, request, *args, **kwargs):
        # Get the organization from the request
        organization = (
            getattr(request, "organization", None) or request.user.organization
        )
        one_time_model_providers_update()
        exclude_providers = request.GET.getlist("exclude_providers", [])

        # Optimization: Fetch all ApiKeys for the org and build a set of providers with valid keys/configs
        # Do this early to avoid unnecessary model processing if we need to filter by availability
        valid_providers = set(
            ApiKey.objects.filter(organization=organization)
            .filter(
                Q(key__regex=r"^(?!\s*$).+")
                | Q(config_json__regex=r"^(?!(\s*|{}|null)$).+")
            )
            .values_list("provider", flat=True)
        )

        valid_providers.update(
            CustomAIModel.objects.filter(organization=organization).values_list(
                "provider", flat=True
            )
        )

        # Single Model Details - if requesting a specific model, skip fetching all models
        model_name = request.query_params.get("name", None)
        if model_name:
            model_manager = LiteLLMModelManager(
                model_name=model_name,
                organization_id=organization.id,
                exclude_providers=exclude_providers,
            )
            available_models = [
                next(
                    (
                        model
                        for model in model_manager.models
                        if model_name.lower() == model["model_name"].lower()
                    ),
                    None,
                )
            ]
        else:
            # Search functionality - only fetch all models if needed
            search_query = request.query_params.get("search", None)
            model_type = request.query_params.get("model_type")
            model_manager = LiteLLMModelManager(
                model_name="",
                organization_id=organization.id,
                exclude_providers=exclude_providers,
            )

            models_to_filter = iter(model_manager.models)

            if search_query:
                models_to_filter = (
                    m
                    for m in models_to_filter
                    if search_query.lower() in m.get("model_name", "").lower()
                )

            if model_type:
                allowed_modes = set()
                if model_type == "llm":
                    allowed_modes = {"chat"}
                elif model_type == "stt":
                    allowed_modes = {"stt", "audio_transcription"}
                elif model_type == "tts":
                    allowed_modes = {"tts", "audio"}
                elif model_type == "image":
                    allowed_modes = {"image_generation"}

                if allowed_modes:
                    models_to_filter = (
                        m for m in models_to_filter if m.get("mode") in allowed_modes
                    )

            available_models = list(models_to_filter)

        # Use list comprehension with direct attribute access for better performance
        # Cache provider logo URLs to avoid repeated lookups
        logo_cache = {}
        response_data = []

        # Pre-compute provider checks
        json_provider_set = set(PROVIDERS_WITH_JSON)

        for model in available_models:
            if model is None:
                continue

            provider = model.get("providers", "")

            # Combine provider exclusion checks
            if exclude_providers and provider in exclude_providers:
                continue

            # Cache logo URL lookup
            if provider not in logo_cache:
                logo_cache[provider] = ProviderLogoUrls.get_url_by_provider(provider)

            # Simplified key_type determination
            key_type = model.get("mode") if model.get("mode") else "text"

            # Use dict comprehension for model data
            model_data = {
                "model_name": model["model_name"],
                "providers": provider,
                "is_available": provider in valid_providers,
                "logo_url": logo_cache[provider],
                "best_for": model.get("best_for"),
                "use_case": model.get("use_case"),
                "cutoff": model.get("cutoff"),
                "rate_limits": model.get("rate_limits"),
                "latency": model.get("latency"),
                "pricing": model.get("pricing"),
                "type": key_type,
            }
            response_data.append(model_data)

        # Sort by isAvailable (available models first)
        response_data.sort(key=lambda x: not x["is_available"])

        # Pagination
        paginator = ExtendedPageNumberPagination()
        paginated_models = paginator.paginate_queryset(response_data, request)

        return paginator.get_paginated_response(paginated_models)


class LiteLLMModelVoicesView(APIView):
    """
    API endpoint to get available voices and formats for a specific TTS model.
    Query params:
        - model: Model name (required)
    """

    permission_classes = [IsAuthenticated]
    _gm = GeneralMethods()

    @swagger_auto_schema(
        responses={
            200: LiteLLMModelVoicesResponseSerializer,
            **MODEL_HUB_ERROR_RESPONSES,
        }
    )
    def get(self, request, *args, **kwargs):
        try:
            model_name = request.query_params.get("model", None)

            if not model_name:
                return self._gm.bad_request(
                    "Model name is required. Use ?model=<model_name>"
                )

            # Get the organization from the request
            organization = (
                getattr(request, "organization", None) or request.user.organization
            )

            # Initialize model manager to get model details
            model_manager = LiteLLMModelManager(
                model_name=model_name, organization_id=organization.id
            )

            # Find the specific model
            model_info = next(
                (
                    model
                    for model in model_manager.models
                    if model_name.lower() == model["model_name"].lower()
                ),
                None,
            )

            if not model_info:
                return self._gm.not_found(f"Model '{model_name}' not found")

            # Extract voice and format information
            system_voices = model_info.get("supported_voices", [])
            # Format system voices
            voices_list = [
                {"id": v, "name": v, "type": "system"} for v in system_voices
            ]

            # Fetch custom voices
            custom_voices = get_custom_voices(
                organization=organization,
                provider=model_info.get("providers", ""),
                workspace=getattr(request, "workspace", None),
            )

            # Add custom voices
            for cv in custom_voices:
                voices_list.append(
                    {
                        "id": str(cv.id),  # Use UUID for custom voices
                        "name": cv.name,
                        "type": "custom",
                    }
                )

            provider = model_info.get("providers", "")
            custom_voice_supported = provider in ["elevenlabs", "cartesia"]

            response_data = {
                "model_name": model_info["model_name"],
                "provider": provider,
                "custom_voice_supported": custom_voice_supported,
                "supported_voices": voices_list,
                "supported_formats": model_info.get("supported_formats", []),
                "default_voice": (
                    model_info.get("supported_voices", ["alloy"])[0]
                    if model_info.get("supported_voices")
                    else None
                ),
                "default_format": (
                    model_info.get("supported_formats", ["mp3"])[0]
                    if model_info.get("supported_formats")
                    else None
                ),
            }

            return self._gm.success_response(response_data)

        except Exception as e:
            error_message = get_specific_error_message(e)
            logger.exception(f"Error fetching model voices: {error_message}")
            return self._gm.internal_server_error_response(error_message)


class ModelParametersView(APIView):
    permission_classes = [IsAuthenticated]
    _gm = GeneralMethods()

    @swagger_auto_schema(
        responses={200: ModelParametersResponseSerializer, **MODEL_HUB_ERROR_RESPONSES}
    )
    def get(self, request, *args, **kwargs):
        try:
            model_name = request.query_params.get("model")
            provider = request.query_params.get("provider")
            model_type = request.query_params.get("model_type")

            if not model_name or not provider or not model_type:
                return self._gm.bad_request(
                    "Missing required query parameters: 'model', 'provider', and 'model_type'"
                )

            parameters = get_model_parameters(provider, model_name, model_type)
            return self._gm.success_response(parameters)

        except Exception as e:
            error_message = get_specific_error_message(e)
            logger.exception(f"Error fetching model parameters: {error_message}")
            return self._gm.internal_server_error_response(error_message)


class RunPromptForRowsView(APIView):
    permission_classes = [IsAuthenticated]
    _gm = GeneralMethods()

    @validated_request(
        request_serializer=RunPromptForRowsRequestSerializer,
        responses={
            200: ModelHubSuccessMessageResponseSerializer,
            **MODEL_HUB_ERROR_RESPONSES,
        },
        reject_unknown_fields=True,
    )
    def post(self, request):
        try:
            data = request.validated_data
            run_prompt_ids = data.get("run_prompt_ids", [])
            row_ids = data.get("row_ids", [])
            selected_all_rows = data.get("selected_all_rows", False)

            if not run_prompt_ids:
                return self._gm.bad_request(
                    get_error_message("RUN_PROMPTS_IDS_MISSING")
                )
            if not row_ids and not selected_all_rows:
                return self._gm.bad_request(get_error_message("MISSING_ROW_IDS"))

            # Enforce organization isolation - verify all run_prompts belong to user's org
            user_org = (
                getattr(request, "organization", None) or request.user.organization
            )
            user_org_id = user_org.id if hasattr(user_org, "id") else user_org
            run_prompters = list(
                RunPrompter.objects.filter(id__in=run_prompt_ids, deleted=False)
            )
            if len(run_prompters) != len(set(map(str, run_prompt_ids))):
                return self._gm.not_found("Run prompt not found")
            for rp in run_prompters:
                if rp.organization_id != user_org_id:
                    return self._gm.not_found("Run prompt not found")

            dataset_ids = {rp.dataset_id for rp in run_prompters}
            if len(dataset_ids) != 1:
                return self._gm.bad_request(
                    "Run prompts must belong to the same dataset"
                )
            dataset_id = next(iter(dataset_ids))

            if row_ids and not selected_all_rows:
                requested_row_ids = set(map(str, row_ids))
                scoped_rows = Row.objects.filter(
                    id__in=row_ids, dataset_id=dataset_id, deleted=False
                ).values_list("id", flat=True)
                if set(map(str, scoped_rows)) != requested_row_ids:
                    return self._gm.not_found("Row not found")

            # Run all evaluations in a single async task
            run_prompt = None
            if selected_all_rows:
                run_prompt = RunPrompter.objects.get(id=run_prompt_ids[0])
                if row_ids and len(row_ids) > 0:
                    row_ids = list(
                        Row.objects.filter(dataset=run_prompt.dataset, deleted=False)
                        .exclude(id__in=row_ids)
                        .values_list("id", flat=True)
                    )
                else:
                    row_ids = list(
                        Row.objects.filter(
                            dataset=run_prompt.dataset, deleted=False
                        ).values_list("id", flat=True)
                    )
            run_all_prompts_task.apply_async(args=(run_prompt_ids, row_ids))
            return self._gm.success_response(
                {"success": "Run prompts queued for processing."}
            )
        except Exception as e:
            error_message = get_specific_error_message(e)
            logger.exception(f"Error in running prompt on rows: {error_message}")
            return self._gm.internal_server_error_response(error_message)


@temporal_activity(time_limit=3600, queue="tasks_l")
def run_all_prompts_task(run_prompt_ids, row_ids):
    try:
        for run_prompt_id in run_prompt_ids:
            run_prompt = RunPrompter.objects.get(id=run_prompt_id)
            run_prompt.status = StatusType.RUNNING.value
            run_prompt.save(update_fields=["status"])

            # Initialize the RunPrompts with the provided run_prompt_id
            run_prompts = RunPrompts(run_prompt_id=run_prompt_id)
            run_prompts.load_run_prompt_id()

            # Update the status of the cells to RUNNING
            Cell.objects.filter(
                row_id__in=row_ids, column__source_id=run_prompt_id, deleted=False
            ).update(
                status=StatusType.RUNNING.value, value=None, value_infos=json.dumps({})
            )

            # Run the prompt for each row ID
            for row_id in row_ids:
                try:
                    row = Row.objects.get(id=row_id)
                    column = Column.objects.get(source_id=run_prompt_id)
                    run_prompts.process_row(row, column, edit_mode=True)
                except Exception as e:
                    run_prompt.status = StatusType.FAILED.value
                    run_prompt.save(update_fields=["status"])
                    raise e

            run_prompt.status = StatusType.COMPLETED.value
            run_prompt.save(update_fields=["status"])

    except Exception as e:
        # Handle exceptions and log errors
        error_message = get_specific_error_message(e)
        logger.exception(f"Error in run all prompts task: {error_message}")
        # Optionally update the run prompt status to FAILED
        try:
            run_prompt = RunPrompter.objects.get(id=run_prompt_id)
            run_prompt.status = StatusType.FAILED.value
            run_prompt.save(update_fields=["status"])
        except Exception:
            pass
