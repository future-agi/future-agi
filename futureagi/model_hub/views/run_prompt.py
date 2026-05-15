import base64
import json
import os
import re
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
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
from django.shortcuts import get_object_or_404
from jinja2.sandbox import SandboxedEnvironment
from rest_framework import viewsets
from rest_framework.generics import CreateAPIView
from rest_framework.permissions import IsAuthenticated
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
from model_hub.serializers.run_prompt import (
    AddRunPromptSerializer,
    ApiKeySerializer,
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
from model_hub.views.utils.utils import (
    sanitize_uuid_for_jinja,
    sanitize_uuids_in_template,
)
from tfc.telemetry import wrap_for_thread
from tfc.temporal import temporal_activity
from tfc.utils.error_codes import (
    get_error_for_api_status,
    get_error_message,
    get_specific_error_message,
)
from tfc.utils.functions import get_prompt_stats
from tfc.utils.general_methods import GeneralMethods
from tfc.utils.pagination import ExtendedPageNumberPagination
from tfc.utils.parse_errors import parse_serialized_errors
from tfc.utils.storage import (
    convert_image_from_url_to_base64,
    detect_audio_format,
)
try:
    from ee.usage.models.usage import APICallStatusChoices, APICallTypeChoices
except ImportError:
    from futureagi._ee_stubs import APICallStatusChoices, APICallTypeChoices
try:
    from ee.usage.utils.usage_entries import log_and_deduct_cost_for_api_request
except ImportError:
    from futureagi._ee_stubs import log_and_deduct_cost_for_api_request

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

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        if serializer.is_valid():
            validated_data = serializer.validated_data

            # First try to get existing API key
            try:
                config_json = None
                if any(
                    validated_data.get("provider").startswith(json_provider)
                    for json_provider in PROVIDERS_WITH_JSON
                ):
                    config_json = json.loads(validated_data.get("key"))
                    try:
                        if any(
                            isinstance(value, dict) for value in config_json.values()
                        ):
                            raise ValidationError("Invalid JSON format for config_json")
                    except Exception:
                        return self._gm.bad_request(get_error_message("INVALID_FORMAT"))
                api_key = ApiKey.objects.get(
                    provider=validated_data.get("provider"),
                    organization=getattr(request, "organization", None)
                    or request.user.organization,
                )
                # Update existing key
                if config_json:
                    api_key.config_json = config_json
                    api_key.key = None
                else:
                    api_key.key = validated_data.get("key")
                    api_key.config_json = None
                api_key.user = request.user
                api_key.save()
            except ApiKey.DoesNotExist:
                # Create new key if not found
                if config_json:
                    api_key = ApiKey.objects.create(
                        provider=validated_data.get("provider"),
                        organization=getattr(request, "organization", None)
                        or request.user.organization,
                        config_json=config_json,
                        user=request.user,
                    )
                else:
                    api_key = ApiKey.objects.create(
                        provider=validated_data.get("provider"),
                        organization=getattr(request, "organization", None)
                        or request.user.organization,
                        key=validated_data.get("key"),
                        user=request.user,
                    )
            except Exception:
                return self._gm.bad_request(get_error_message("UNABLE_TO_ADD_API_KEY"))

            return self._gm.success_response(
                {
                    "id": str(api_key.id),
                    "provider": api_key.provider,
                    "masked_actual_key": api_key.masked_actual_key,
                    "config_json": api_key.actual_json,
                }
            )
        return self._gm.bad_request(parse_serialized_errors(serializer))

    def perform_update(self, serializer):
        serializer.save(
            organization=getattr(self.request, "organization", None)
            or self.request.user.organization
        )

    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = self.get_serializer(instance)
        data = serializer.data
        # Use the decrypted key in the response
        # if instance.actual_key:
        #     data['key'] = instance.actual_key
        return self._gm.success_response(data)

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

# Jinja2 environment (reusable, sandboxed for security)
_jinja2_env = SandboxedEnvironment()


def render_template(
    template_str: str, context: dict, template_format: str = None
) -> str:
    """
    Render a template string with the given context.
    Supports multiple formats: f-string, mustache, jinja2.

    Args:
        template_str: The template string
        context: Dictionary of variables to substitute
        template_format: One of 'f-string', 'mustache', 'jinja2' (default: jinja2)

    Returns:
        Rendered string
    """
    if not template_str:
        return template_str or ""

    if template_format is None:
        template_format = DEFAULT_TEMPLATE_FORMAT

    if template_format == TEMPLATE_FORMAT_FSTRING:
        return template_str.format(**context)

    elif template_format == TEMPLATE_FORMAT_MUSTACHE:
        return chevron.render(template_str, context)

    elif template_format == TEMPLATE_FORMAT_JINJA2:
        # Pre-process: handle variable names with spaces (Jinja2 doesn't allow them)
        import re

        processed = template_str
        safe_ctx = dict(context)
        raw_vars = re.findall(r"\{\{\s*([^{}]+?)\s*\}\}", processed)
        for var_name in raw_vars:
            stripped = var_name.strip()
            if " " in stripped and stripped in safe_ctx:
                processed = processed.replace(
                    "{{" + var_name + "}}", str(safe_ctx.pop(stripped))
                )
                processed = processed.replace(
                    "{{ " + stripped + " }}", str(context.get(stripped, ""))
                )
        return _jinja2_env.from_string(processed).render(**safe_ctx)

    else:
        raise ValueError(
            f"Unknown template_format: {template_format}. "
            f"Supported: {TEMPLATE_FORMAT_FSTRING}, {TEMPLATE_FORMAT_MUSTACHE}, {TEMPLATE_FORMAT_JINJA2}"
        )


class JsonStr(dict):
    """Dict subclass that renders as its original JSON string via str()/Jinja.
    Allows {{col.key}} via dict attribute access while {{col}} outputs the raw JSON."""
    def __init__(self, data, raw):
        super().__init__(data)
        self._raw = raw
    def __str__(self):
        return self._raw


def populate_placeholders(messages: list[dict], dataset_id, row_id, col_id, model_name, template_format=None):
    try:
        media_error = None
        # Debug: Log input messages to see what template we're processing
        logger.info(f"populate_placeholders called with messages: {messages}")

        dataset = Dataset.objects.get(id=dataset_id)
        column_ids = dataset.column_order

        # Create context for Handlebars with proper nesting
        context: dict[str, Any] = {}
        column_info = {}  # For image handling
        raw_values = {}  # For debugging

        # Collect column values
        for column_id in column_ids:
            try:
                if column_id != str(col_id):
                    column = Column.objects.get(id=column_id)
                    cell = Cell.objects.filter(
                        dataset=dataset, column=column, row__id=row_id
                    ).first()

                    if not cell:
                        continue

                    # Store raw values for debugging
                    raw_values[column.name] = (
                        cell.value if cell.value is not None else ""
                    )

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
                        elif is_valid and isinstance(parsed_json, list) and template_format in ("jinja", "jinja2"):
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
                    sanitized_col_id = sanitize_uuid_for_jinja(column_id)
                    context[sanitized_col_id] = cell_value

                    # Debug: Log what we're adding to context
                    logger.info(
                        f"Added to context: column_name={column.name}, data_type={column.data_type}, "
                        f"value_type={type(cell_value).__name__}, "
                        f"value_preview={str(cell_value)[:100] if cell_value else 'None'}"
                    )
            except Exception as e:
                logger.exception(
                    f"Error processing column {column_id} ({column.name if 'column' in locals() else 'unknown'}): {e}"
                )
                continue

        # Debug: Log final context structure
        logger.info(f"Final context keys: {list(context.keys())}")
        for key, value in context.items():
            if isinstance(value, dict):
                logger.info(
                    f"Context['{key}'] is a dict with keys: {list(value.keys())}"
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
                    )
                elif isinstance(content, str):
                    processed_content = process_string_content(
                        content,
                        column_info,
                        context,
                        image_counter,
                        model_name,
                        template_format=template_format,
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

        except ValueError as e:
            media_error = True
            raise e

    except Exception as e:
        if media_error:
            raise e
        else:
            traceback.print_exc()
            logger.exception(f"Fatal error processing messages: {e}")
            # Return original messages as fallback
            return messages


def process_list_content(content, column_info, context, image_counter, model_name, template_format=None):
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
            )
            processed_content.extend(text_segments)
        else:
            # Handle other media types
            try:
                processed_content.append(handle_media(item, model_name))
            except Exception as e:
                logger.exception(f"Error handling media item: {e}")
                # Keep original item if processing fails
                processed_content.append(item)

    return processed_content


def process_string_content(content, column_info, context, image_counter, model_name, template_format=None):
    """Process string-type content with proper media handling"""
    return process_text_with_media(
        content, column_info, context, image_counter, model_name, template_format=template_format
    )


def process_text_with_media(text, column_info, context, image_counter, model_name, template_format=None):
    """Process text content, handling both templates and media placeholders"""
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
                # Check for original UUID placeholder and column name placeholder
                placeholder = create_placeholder(col_id)
                alt_placeholder = create_placeholder(info["name"])

                for ph in [placeholder, alt_placeholder]:
                    if ph in text:
                        marker = (
                            f"__{info['data_type'].upper()}_MARKER_{uuid.uuid4()}__"
                        )
                        image_markers[marker] = {
                            "url": info["value"],
                            "counter": image_counter,
                            "type": info["data_type"],
                        }
                        text = text.replace(ph, marker)
                        image_counter += 1

            if info["data_type"] == "document" and info["value"]:
                # Check for original UUID placeholder and column name placeholder
                placeholder = create_placeholder(col_id)
                alt_placeholder = create_placeholder(info["name"])

                if placeholder in text or alt_placeholder in text:
                    is_pdf = True
                    pdf_url = info["value"]
                    pdf_name = info["name"]

                    # Replace both UUID and column name placeholders
                    if placeholder in text:
                        text = text.replace(placeholder, info["name"])
                    if alt_placeholder in text:
                        text = text.replace(alt_placeholder, info["name"])

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
                    placeholder = create_placeholder(col_id)
                    alt_placeholder = create_placeholder(info["name"])
                    for ph in [placeholder, alt_placeholder]:
                        if ph in text:
                            # Create markers for ALL images in array
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
                            text = text.replace(ph, all_markers)
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
        text = sanitize_uuids_in_template(text)
        logger.debug(f"Template after UUID sanitization: {text[:500]}")

        # Render template using multi-format renderer
        # Map frontend format names to backend constants
        effective_format = template_format
        if effective_format == "jinja":
            effective_format = TEMPLATE_FORMAT_JINJA2
        try:
            processed_text = render_template(text, context, template_format=effective_format)
        except Exception as render_error:
            logger.exception(
                f"Template rendering failed: {render_error}. Template: {text[:200]}..."
            )
            # Re-raise to see full error - template syntax issue
            raise
        uuid_pattern = r"\{\{[a-fA-F0-9]{8}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{12}\}\}"
        if re.search(uuid_pattern, processed_text, re.IGNORECASE):
            logger.warning(
                f"Found unreplaced UUID placeholders in processed text: {processed_text}"
            )
            processed_text = re.sub(
                uuid_pattern, "", processed_text, flags=re.IGNORECASE
            )
        # Process media markers and create segments
        if image_markers:
            return process_media_markers(processed_text, image_markers, model_name)
        else:
            response = []
            # No media, just return processed text
            if processed_text.strip():
                response.extend([{"type": "text", "text": processed_text}])
            else:
                response.extend([{"type": "text", "text": ""}])

            if is_pdf:
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
    except ValueError as e:
        logger.exception(f"Error VALUEERROR text with media: {e}")
        raise e
    except Exception as e:
        logger.exception(f"Error processing text with media: {e}")
        logger.exception(f"Template text: {text[:200]}...")
        logger.exception(f"Context keys: {list(context.keys())}")
        # Fallback to original text
        response = [{"type": "text", "text": text}]
        if is_pdf:
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


def process_media_markers(text, image_markers, model_name):
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

    def post(self, request, *args, **kwargs):
        from django.db import transaction

        # Validate incoming data with the serializer
        serializer = LitellmSerializer(data=request.data)

        # Check if data is valid
        if serializer.is_valid():
            # Extract validated data
            validated_data = serializer.validated_data
            dataset = Dataset.objects.filter(id=validated_data.get("dataset_id")).get()
            # Retrieve tools based on the IDs from the validated data
            tool_ids = validated_data.get("tools", [])
            tools = Tools.objects.filter(id__in=tool_ids)

            # Use transaction to ensure atomicity
            with transaction.atomic():
                run_prompter = RunPrompter.objects.create(
                    name=validated_data.get("name"),
                    model=validated_data.get("model"),
                    organization=getattr(request, "organization", None)
                    or request.user.organization,
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
        else:
            return self._gm.bad_request(parse_serialized_errors(serializer))


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
            # api_call_log_row = None
            try:
                logger.info(
                    "RunPrompts_process_row_validating_api_call",
                    run_prompt_id=str(self.run_prompt_id),
                    row_id=row_id,
                )
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
                elif api_call_log_row.status != APICallStatusChoices.PROCESSING.value:
                    error_message = get_error_for_api_status(api_call_log_row.status)
                    logger.error(
                        "RunPrompts_process_row_api_call_status_invalid",
                        run_prompt_id=str(self.run_prompt_id),
                        row_id=row_id,
                        status=api_call_log_row.status,
                        error_message=error_message,
                    )
                    raise ValueError(error_message)
                elif api_call_log_row.status == APICallStatusChoices.PROCESSING.value:
                    # by default set the status to success since we are not deducting cost
                    api_call_log_row.status = APICallStatusChoices.SUCCESS.value
                    api_call_log_row.save()
                    logger.info(
                        "RunPrompts_process_row_api_call_status_set_success",
                        run_prompt_id=str(self.run_prompt_id),
                        row_id=row_id,
                    )

                # Dual-write: emit usage event for new billing system
                try:
                    try:
                        from ee.usage.schemas.events import UsageEvent
                    except ImportError:
                        UsageEvent = None
                    try:
                        from ee.usage.services.emitter import emit
                    except ImportError:
                        emit = None

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

                logger.info(
                    "RunPrompts_process_row_populating_placeholders",
                    run_prompt_id=str(self.run_prompt_id),
                    row_id=row_id,
                )
                messages = populate_placeholders(
                    self.run_prompt_model.messages,
                    dataset_id=self.run_prompt_model.dataset.id,
                    row_id=row.id,
                    col_id=column.id,
                    model_name=self.run_prompt_model.model,
                    template_format=(self.run_prompt_model.run_prompt_config or {}).get("template_format"),
                )
                messages = remove_empty_text_from_messages(messages)
                logger.info(
                    "RunPrompts_process_row_placeholders_populated",
                    run_prompt_id=str(self.run_prompt_id),
                    row_id=row_id,
                    message_count=len(messages),
                )

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
                logger.info(
                    "cell_created",
                    cell_id=str(cell.id),
                    row_id=row_id,
                    run_prompt_id=str(self.run_prompt_id),
                    status=status,
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

    def post(self, request, *args, **kwargs):
        from django.db import transaction

        try:
            serializer = AddRunPromptSerializer(data=request.data)
            if not serializer.is_valid():
                return self._gm.bad_request(parse_serialized_errors(serializer))

            validated_data = serializer.validated_data
            dataset_id = validated_data["dataset_id"]
            name = validated_data["name"]
            config = validated_data[
                "config"
            ]  # This is now a validated dict from PromptConfigSerializer
            run_prompt_config = config.get("run_prompt_config", {})

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

    def post(self, request, *args, **kwargs):
        try:
            # Validate incoming data
            serializer = PreviewRunPromptSerializer(data=request.data)
            if not serializer.is_valid():
                return self._gm.bad_request(parse_serialized_errors(serializer))

            validated_data = serializer.validated_data
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

            responses = []
            for row in rows:
                try:
                    output_format = config.get("output_format", "string")
                    messages = populate_placeholders(
                        config.get("messages", []),
                        dataset_id=dataset_id,
                        row_id=row.id,
                        col_id=None,
                        model_name=config.get("model", ""),
                    )
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
                        run_prompt_config=config.get("run_prompt_config"),
                        workspace_id=(
                            dataset.workspace.id
                            if dataset and dataset.workspace
                            else None
                        ),
                    )
                    response, value_infos = run_prompt.litellm_response()

                    # Check if showReasoningProcess is enabled to include thinking content
                    run_prompt_config = config.get("run_prompt_config", {})
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

    def post(self, request, *args, **kwargs):
        from django.db import transaction

        try:
            serializer = EditRunPromptColumnSerializer(data=request.data)
            if not serializer.is_valid():
                return self._gm.bad_request(parse_serialized_errors(serializer))

            validated_data = serializer.validated_data
            dataset_id = validated_data["dataset_id"]
            column_id = validated_data["column_id"]
            config = validated_data["config"]
            name = validated_data["name"]
            run_prompt_config = config.get("run_prompt_config", {})

            # Validate dataset and column exist
            dataset = get_object_or_404(Dataset, id=dataset_id)

            # Enforce organization isolation
            if (
                dataset.organization_id
                != (
                    getattr(request, "organization", None) or request.user.organization
                ).id
            ):
                return self._gm.not_found("Dataset not found")

            column = get_object_or_404(Column, id=column_id, dataset=dataset)

            # Verify column is a run prompt column
            if column.source != SourceChoices.RUN_PROMPT.value:
                return self._gm.bad_request(get_error_message("COLUMN_IS_IN_VALID"))

            # Lock the RunPrompter row to prevent race conditions
            # Use of=('self',) to avoid issues with nullable foreign keys causing outer joins
            with transaction.atomic():
                run_prompter = RunPrompter.objects.select_for_update(of=("self",)).get(
                    id=column.source_id
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

                run_prompter.run_prompt_config = config.get(
                    "run_prompt_config", run_prompter.run_prompt_config
                )

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
                # Set status to FAILED if workflow couldn't start
                RunPrompter.objects.filter(id=run_prompter_id).update(
                    status=StatusType.FAILED.value
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

    def get(self, request):
        try:
            # Get the column and verify it's a run prompt column
            column_id = request.query_params.get("column_id")
            column = get_object_or_404(Column, id=column_id)

            # Enforce organization isolation through the column's dataset
            if (
                column.dataset.organization_id
                != (
                    getattr(request, "organization", None) or request.user.organization
                ).id
            ):
                return self._gm.not_found(
                    "Column or run prompt configuration not found"
                )

            if column.source != SourceChoices.RUN_PROMPT.value:
                return self._gm.bad_request(get_error_message("COLUMN_IS_IN_VALID"))

            # Get associated RunPrompter instance
            run_prompter = get_object_or_404(RunPrompter, id=column.source_id)

            # Get tools configuration
            tools = []
            for tool in run_prompter.tools.all():
                tools.append(
                    {"id": str(tool.id), "name": tool.name, "config": tool.config}
                )
            base_run_prompt_config = run_prompter.run_prompt_config or {}

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
                organization=organization, provider=model_info.get("providers", "")
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

    def post(self, request):
        try:
            # Extract the run_prompt_ids and row_ids from the request data
            run_prompt_ids = request.data.get("run_prompt_ids", [])
            row_ids = request.data.get("row_ids", [])

            selected_all_rows = request.data.get("selected_all_rows", False)

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
            run_prompters = list(RunPrompter.objects.filter(id__in=run_prompt_ids))
            if len(run_prompters) != len(set(map(str, run_prompt_ids))):
                return self._gm.not_found("Run prompt not found")
            for rp in run_prompters:
                if rp.organization_id != user_org_id:
                    return self._gm.not_found("Run prompt not found")

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
