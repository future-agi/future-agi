import base64
import json  # Add this import at the top of the file
import re
import time
import traceback
import uuid
from uuid import UUID

import litellm
import requests
import structlog

from tfc.ee_stub import _ee_stub

try:
    from ee.agenthub.prompt_generate_agent.prompt_generate import (
        PromptGenerator,
        PromptSuggestionGenerator,
    )
    from ee.agenthub.synthetic_data_agent.synthetic_data_agent import (
        SyntheticDataAgent,
    )
except ImportError:
    PromptGenerator = _ee_stub("PromptGenerator")
    PromptSuggestionGenerator = _ee_stub("PromptSuggestionGenerator")
    SyntheticDataAgent = _ee_stub("SyntheticDataAgent")
from django.core.cache import cache
from django.db import close_old_connections, models, transaction
from django.db.models import Case, IntegerField, Prefetch, Q, Value, When
from django.db.models.functions import Cast, Substr
from django.forms.models import model_to_dict
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import filters, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import APIException
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.models import User
from accounts.models.organization import Organization

logger = structlog.get_logger(__name__)
import atexit
import concurrent.futures

from agentic_eval.core_evals.fi_evals import *  # noqa: F403
from agentic_eval.core_evals.run_prompt.litellm_response import RunPrompt
from django.db import connection

from analytics.utils import (
    MixpanelEvents,
    MixpanelSources,
    get_mixpanel_properties,
    track_mixpanel_event,
)
from evaluations.constants import FUTUREAGI_EVAL_TYPES
from model_hub.constants import (
    PROMPT_CURL_CODE,
    PROMPT_GO_CODE,
    PROMPT_LANGCHAIN_CODE,
    PROMPT_NODEJS_CODE,
    PROMPT_PYTHON_CODE,
    PROMPT_TYPESCRIPT_CODE,
)

# from model_hub.serializers.prompt_template import CommitSerializer, PromptExecutionFilter, PromptHistoryExecutionFilter, PromptVersionSerializer, PromptTemplateFilter, PromptTemplateSerializer, PromptExecutionSerializer, UserResponseSchemaSerializer, VersionDefaultSerializer
# from model_hub.serializers.prompt_template import CommitSerializer, PromptExecutionFilter, PromptHistoryExecutionFilter, PromptVersionSerializer, PromptTemplateFilter, PromptTemplateSerializer, PromptExecutionSerializer, UserResponseSchemaSerializer, VersionDefaultSerializer
from model_hub.models.choices import ModalityType, OwnerChoices, StatusType
from model_hub.models.develop_dataset import (
    Cell,
    Column,
    Dataset,
    KnowledgeBaseFile,
    Row,
)
from model_hub.models.evals_metric import EvalTemplate
from model_hub.models.prompt_base_template import PromptBaseTemplate
from model_hub.models.prompt_folders import PromptFolder
from model_hub.models.run_prompt import (
    PromptEvalConfig,
    PromptTemplate,
    PromptVersion,
)
from model_hub.serializers.prompt_folder import PromptFolderSerializer
from model_hub.serializers.prompt_template import (
    CommitSerializer,
    CompareVersionsSerializer,
    MultipleDraftSerializer,
    PromptExecutionFilter,
    PromptExecutionSerializer,
    PromptHistoryExecutionFilter,
    PromptHistoryExecutionSerializer,
    PromptTemplateFilter,
    PromptTemplateSerializer,
    SingleEvaluationConfigSerializer,
    UploadFileSerializer,
    UserResponseSchemaSerializer,
    VersionDefaultSerializer,
)
from model_hub.services.prompt_placeholder import validate_and_parse_placeholder
from model_hub.utils.function_eval_params import (
    normalize_eval_runtime_config,
    params_with_defaults_for_response,
)
from model_hub.utils.utils import (
    get_model_mode,
    remove_empty_text_from_messages,
    submit_with_retry,
    track_running_eval_count,
)
from model_hub.utils.websocket_manager import (
    get_websocket_manager,
)
from model_hub.views.eval_runner import EvaluationRunner
from tfc.settings.settings import BASE_URL
from tfc.temporal import temporal_activity
from tfc.utils.base_viewset import (
    BaseModelViewSetMixin,
    BaseModelViewSetMixinWithUserOrg,
)
from tfc.utils.error_codes import get_error_message
from tfc.utils.general_methods import GeneralMethods
from tfc.utils.pagination import ExtendedPageNumberPagination
from tfc.utils.parse_errors import parse_serialized_errors
from tfc.utils.storage import (
    convert_image_from_url_to_base64,
    detect_audio_format,
    download_document_from_url,
    upload_audio_to_s3_duration,
    upload_document_to_s3,
    upload_image_to_s3,
)

# Module-level ThreadPoolExecutor for background tasks that use instance methods
# These can't be migrated to Temporal as they require 'self' context
_PROMPT_TEMPLATE_EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=10)

# Register cleanup handler for graceful shutdown
atexit.register(lambda: _PROMPT_TEMPLATE_EXECUTOR.shutdown(wait=False))


def _safe_background_task(func, *args, **kwargs):
    """
    Wrapper that ensures proper database connection handling in background threads.
    This replicates the safety features of the old IMPROVED_EXECUTOR.
    """

    def wrapped():
        try:
            close_old_connections()
            connection.ensure_connection()
            return func(*args, **kwargs)
        finally:
            close_old_connections()

    return wrapped


try:
    from ee.usage.models.usage import APICallStatusChoices
except ImportError:
    APICallStatusChoices = None
try:
    from ee.usage.utils.usage_entries import APICallTypeChoices, count_text_tokens, log_and_deduct_cost_for_api_request
except ImportError:
    from futureagi._ee_stubs import APICallTypeChoices, count_text_tokens, log_and_deduct_cost_for_api_request

MIME_TO_EXT = {
    # Documents
    "application/pdf": "pdf",
    # Images
    "image/jpeg": "jpeg",
    "image/jpg": "jpg",
    "image/png": "png",
    "image/gif": "gif",
    "image/webp": "webp",
    # Audio
    "audio/mpeg": "mp3",
    "audio/wav": "wav",
    "audio/ogg": "ogg",
    "audio/mp4": "m4a",
    "audio/webm": "webm",
}


def is_valid_uuid(value):
    try:
        UUID(str(value))
        return True
    except ValueError:
        return False


def handle_media(item: dict, model_name: str):
    """
    Handle media files in messages.
    """
    if item["type"] == "image_url":
        if not litellm.utils.supports_vision(model=model_name):
            raise ValueError(f"Model {model_name} does not support image input.")
        if "url" in item["image_url"]:
            if item["image_url"]["url"].startswith("http://") or item["image_url"][
                "url"
            ].startswith("https://"):
                return {
                    "type": "image_url",
                    "image_url": {
                        "url": convert_image_from_url_to_base64(
                            item["image_url"]["url"]
                        )
                    },
                }
    elif item["type"] == "audio_url":
        # Allow audio placeholders for STT/TTS models regardless of litellm modal checks
        model_mode = get_model_mode(model_name)
        if model_mode not in (
            "audio",
            "stt",
            "tts",
        ) and not litellm.utils.supports_audio_input(model=model_name):
            raise ValueError(f"Model {model_name} does not support audio input.")
        try:
            url = item.get("audio_url", {}).get("url")
            if not url:
                raise ValueError("Missing audio URL")

            response = requests.get(url, timeout=120)
            response.raise_for_status()

            bytes_data = response.content
            encoded_string = base64.b64encode(bytes_data).decode("utf-8")
            audio_type = detect_audio_format(bytes_data)

            if not audio_type:
                raise ValueError("Could not detect audio format")

            return {
                "type": "input_audio",
                "input_audio": {"data": encoded_string, "format": audio_type},
            }

        except requests.exceptions.RequestException as e:
            logger.error(f"Request failed for audio URL: {e}")
        except (KeyError, ValueError) as e:
            logger.error(f"Data processing error: {e}")
        except Exception as e:
            logger.error(f"Unexpected error while processing audio: {e}")

    elif item["type"] == "pdf_url":
        if not litellm.utils.supports_pdf_input(model=model_name):
            raise ValueError(f"Model {model_name} does not support PDF input.")
        doc_url = item.get("pdf_url", {}).get("url")
        file_data, content_type = download_document_from_url(doc_url)
        file_name = item.get("pdf_url", {}).get("file_name")
        if not file_name:
            raise ValueError("File Name is required")
        encoded_file = base64.b64encode(file_data).decode("utf-8")
        base64_url = f"data:{content_type};base64,{encoded_file}"
        if doc_url and file_name:
            file_data = {
                "type": "file",
                "file": {"filename": file_name, "file_data": base64_url},
            }
            return file_data

    return None


def replace_variables(
    messages: list[dict], variable_names: dict, model_name: str, template_format: str = None
) -> list[dict]:
    """
    Replace variables in message content with their corresponding values.

    Args:
        messages (List[dict]): List of message dictionaries with 'role' and 'content'
        variable_names (dict): Dictionary of variable names and their values
        model_name (str): The model name (for media handling)
        template_format (str): "jinja" to use Jinja2 engine, otherwise simple replacement.

    Returns:
        List[dict]: Messages with variables replaced in content
    """
    from model_hub.views.run_prompt import render_template, TEMPLATE_FORMAT_JINJA2

    use_jinja = template_format in ("jinja", "jinja2")

    if use_jinja:
        # Parse JSON strings into native types so Jinja2 can iterate/access them.
        # e.g. '["a","b"]' becomes a real list, '{"k":"v"}' becomes a real dict.
        import json as _json

        jinja_context = {}
        for k, v in variable_names.items():
            if isinstance(v, str):
                try:
                    parsed = _json.loads(v)
                    if isinstance(parsed, (list, dict)):
                        jinja_context[k] = parsed
                    else:
                        jinja_context[k] = v
                except (ValueError, TypeError):
                    jinja_context[k] = v
            else:
                jinja_context[k] = v
    else:
        jinja_context = None

    def _render(text):
        if use_jinja:
            return render_template(text, jinja_context, TEMPLATE_FORMAT_JINJA2)
        # Default: simple placeholder replacement (original behaviour)
        for var_name, var_value in variable_names.items():
            placeholder = f"{{{{{var_name}}}}}"
            text = text.replace(placeholder, str(var_value))
        return text

    processed_messages = []
    for message in messages:
        content = message["content"]
        processed_content = []

        if isinstance(content, list):
            # Process each item in the content list
            for item in content:
                if "type" not in item:
                    raise ValueError(
                        "Invalid content format. Expected a list of dictionaries with 'type' and 'text' keys."
                    )

                if item["type"] == "text":
                    text = _render(item["text"])
                    processed_content.append({"type": "text", "text": text})

                else:
                    processed_content.append(handle_media(item, model_name))

        elif isinstance(content, str):
            text = _render(content)
            processed_content.append({"type": "text", "text": text})

        processed_messages.append(
            {"role": message["role"], "content": processed_content}
        )

        logger.info(
            f"Processed content: {[content.keys() for content in processed_content]}"
        )

    return processed_messages


def replace_ids_with_column_name(prompt: str) -> str:
    """
    Replace column ID placeholders in the prompt with their corresponding column names.

    Args:
        prompt: A string containing placeholders in the format {{column_id}}

    Returns:
        The prompt string with column IDs replaced by their names
    """
    try:
        placeholders = re.findall(r"\{\{(.*?)\}\}", prompt)
        if not placeholders:
            return prompt

        # remove non uuid placeholders
        filtered_placeholders = []
        for placeholder in placeholders:
            if is_valid_uuid(placeholder):
                filtered_placeholders.append(placeholder)

        if not filtered_placeholders or len(filtered_placeholders) == 0:
            return prompt

        # Fetch all columns in a single query
        columns = {
            str(col.id): col.name
            for col in Column.objects.filter(id__in=filtered_placeholders)
        }
        for placeholder in filtered_placeholders:
            try:
                if placeholder in columns:
                    prompt = prompt.replace(
                        f"{{{{{placeholder}}}}}", f"{{{{{columns[placeholder]}}}}}"
                    )
            except Exception as e:
                logger.error(f"Error replacing column ID {placeholder}: {str(e)}")
        return prompt
    except Exception as e:
        logger.exception(f"Error replacing IDs with column names: {str(e)}")
        return prompt


async def replace_ids_with_column_name_async(prompt: str) -> str:
    """
    Async version of replace_ids_with_column_name for use in async contexts (e.g., WebSocket consumers).

    Replace column ID placeholders in the prompt with their corresponding column names.

    Args:
        prompt: A string containing placeholders in the format {{column_id}}

    Returns:
        The prompt string with column IDs replaced by their names
    """
    from asgiref.sync import sync_to_async

    try:
        placeholders = re.findall(r"\{\{(.*?)\}\}", prompt)
        if not placeholders:
            return prompt

        # remove non uuid placeholders
        filtered_placeholders = []
        for placeholder in placeholders:
            if is_valid_uuid(placeholder):
                filtered_placeholders.append(placeholder)

        if not filtered_placeholders or len(filtered_placeholders) == 0:
            return prompt

        # Fetch all columns in a single query (wrapped with sync_to_async)
        @sync_to_async
        def fetch_columns():
            return {
                str(col.id): col.name
                for col in Column.objects.filter(id__in=filtered_placeholders)
            }

        columns = await fetch_columns()

        for placeholder in filtered_placeholders:
            try:
                if placeholder in columns:
                    prompt = prompt.replace(
                        f"{{{{{placeholder}}}}}", f"{{{{{columns[placeholder]}}}}}"
                    )
            except Exception as e:
                logger.error(f"Error replacing column ID {placeholder}: {str(e)}")
        return prompt
    except Exception as e:
        logger.exception(f"Error replacing IDs with column names (async): {str(e)}")
        return prompt


# Add this helper method after the replace_ids_with_column_name function
def get_next_version_number(template_id, organization_id):
    """
    Get the next version number atomically to prevent race conditions.
    Uses database-level locking to ensure uniqueness.
    Gets the latest created version and increments its number.
    """

    with transaction.atomic():
        # Use select_for_update to lock the rows and prevent race conditions
        # Get the latest created version
        latest_version = (
            PromptVersion.objects.filter(
                original_template_id=template_id,
                original_template__organization_id=organization_id,
                deleted=False,
            )
            .order_by("-created_at")
            .first()
        )

        if latest_version:
            try:
                # Remove 'v' prefix and convert to integer, then increment
                current_version_num = int(latest_version.template_version.lstrip("v"))
                return current_version_num + 1
            except (ValueError, AttributeError):
                # If version format is invalid, start from 1
                return 1
        else:
            # No versions exist, start from 1
            return 1


class UserResponseSchemaViewSet(
    BaseModelViewSetMixinWithUserOrg, viewsets.ModelViewSet
):
    serializer_class = UserResponseSchemaSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        # Get base queryset with automatic filtering from mixin
        return super().get_queryset()

    def perform_create(self, serializer):
        serializer.save(
            organization=getattr(self.request, "organization", None)
            or self.request.user.organization
        )

    def perform_update(self, serializer):
        serializer.save(
            organization=getattr(self.request, "organization", None)
            or self.request.user.organization
        )


class UploadFileView(APIView):
    _gm = GeneralMethods()
    permission_classes = [IsAuthenticated]

    def _convert_to_base64(self, file):
        """Convert file content to base64 string with mime type prefix"""
        try:
            audio_content = file.read()
            if not audio_content:
                raise ValueError("Empty file content")

            base64_audio = base64.b64encode(audio_content).decode("utf-8")

            mime_type = file.content_type
            if not mime_type:
                raise ValueError("Missing content type")

            base64_string_with_mime = f"data:{mime_type};base64,{base64_audio}"
            return base64_string_with_mime

        except (AttributeError, UnicodeDecodeError) as e:
            logger.error(f"Error encoding file to base64: {str(e)}")
            raise ValueError(f"Failed to encode file: {str(e)}")  # noqa: B904
        except Exception as e:
            logger.exception(f"Unexpected error in base64 conversion: {str(e)}")
            raise ValueError(f"Failed to process file: {str(e)}")  # noqa: B904

    def post(self, request):
        try:
            serializer = UploadFileSerializer(data=request.data)
            if not serializer.is_valid():
                errors = parse_serialized_errors(serializer)
                return self._gm.bad_request(str(errors))
            validated_data = serializer.validated_data

            files = validated_data.get("files")
            links = validated_data.get("links")
            media_type = validated_data["type"]
            urls = []
            file_name = None
            if media_type in ["image", "audio", "pdf", "text"]:
                upload_func = (
                    upload_image_to_s3
                    if media_type == "image"
                    else (
                        upload_audio_to_s3_duration
                        if media_type == "audio"
                        else upload_document_to_s3
                    )
                )
                if files or links:
                    source_items = files or links
                    for item in source_items:
                        try:
                            if files:
                                item = self._convert_to_base64(item)
                            if links:
                                file_name = item.split("/")[-1].split("?")[0]

                            if upload_func == upload_audio_to_s3_duration:
                                url, _ = upload_func(
                                    item,
                                    bucket_name="fi-customer-data-dev",
                                    object_key=f"tempcust/{uuid.uuid4()}",
                                )
                            else:
                                url = upload_func(
                                    item,
                                    bucket_name="fi-customer-data-dev",
                                    object_key=f"tempcust/{uuid.uuid4()}",
                                )
                            if file_name and "." not in file_name:
                                try:
                                    response = requests.head(url, timeout=10)
                                    content_type = (
                                        response.headers.get("Content-Type", "")
                                        .split(";")[0]
                                        .strip()
                                    )

                                    if content_type in MIME_TO_EXT:
                                        file_name = (
                                            f"{file_name}.{MIME_TO_EXT[content_type]}"
                                        )

                                except requests.RequestException:
                                    pass
                            urls.append({"url": url, "file_name": file_name})
                        except Exception as e:
                            urls.append(
                                {"url": None, "error": str(e), "file_name": None}
                            )
                    return self._gm.success_response(urls)
        except Exception as e:
            logger.error(f"Error uploading file: {str(e)}")
            return self._gm.bad_request("Failed to upload file.")


class PromptTemplateViewSet(BaseModelViewSetMixin, viewsets.ModelViewSet):
    queryset = PromptTemplate.objects.all()
    serializer_class = PromptTemplateSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = ExtendedPageNumberPagination
    filter_backends = [
        DjangoFilterBackend,
        filters.SearchFilter,
        filters.OrderingFilter,
    ]
    filterset_class = PromptTemplateFilter
    search_fields = ["name"]
    ordering_fields = ["name", "created_at"]
    _gm = GeneralMethods()

    def get_queryset(self):
        # BaseModelManager automatically handles workspace, organization, and soft delete filtering
        queryset = super().get_queryset()

        # Filter templates by modality (configuration.model_detail.type in prompt_config_snapshot)
        modality = self.request.query_params.getlist("modality")
        if modality and ModalityType.ALL not in modality:
            version_filter = Q(
                prompt_config_snapshot__configuration__model_detail__type__in=modality,
            )
            # Records without a type field default to 'chat'
            if ModalityType.CHAT in modality:
                version_filter |= Q(
                    prompt_config_snapshot__configuration__model_detail__type__isnull=True,
                )
            template_ids = (
                PromptVersion.objects.filter(deleted=False)
                .filter(version_filter)
                .values_list("original_template_id", flat=True)
            )
            queryset = queryset.filter(id__in=template_ids)

        return queryset

    def get_serializer_class(self):
        return self.serializer_class

    def retrieve(self, request, *args, **kwargs):
        """
        Retrieve a prompt template with version history and execution data.
        Handles caching and error cases.
        """
        try:
            # Get version and instance
            request.query_params.get("version")
            instance = get_object_or_404(self.get_queryset(), pk=kwargs["pk"])

            # Serialize base instance data
            serializer = self.get_serializer(instance)
            response = Response(serializer.data)

            # Prioritize draft versions — they contain the latest unsaved edits
            # (e.g. template_format changes) that haven't been committed yet.
            org = getattr(self.request, "organization", None) or self.request.user.organization
            base_qs = PromptVersion.objects.filter(
                original_template=instance,
                original_template__organization=org,
                deleted=False,
            )
            draft_execution = base_qs.filter(is_draft=True).order_by("-created_at").first()
            if draft_execution:
                execution = draft_execution
            elif base_qs.filter(is_default=True).exists():
                execution = base_qs.filter(is_default=True).order_by("-is_default", "-created_at").first()
            elif base_qs.filter(is_draft=False).exists():
                execution = base_qs.filter(is_draft=False).order_by("-is_default", "-created_at").first()
            elif base_qs.exists():
                execution = base_qs.order_by("-is_default", "-created_at").first()
            else:
                dummy_data = {
                    "name": "",
                    "prompt_config": [
                        {
                            "messages": [
                                {
                                    "role": "system",
                                    "content": [{"type": "text", "text": ""}],
                                },
                                {
                                    "role": "user",
                                    "content": [{"type": "text", "text": ""}],
                                },
                            ]
                        }
                    ],
                }
                execution = PromptVersion.objects.create(
                    original_template=instance,
                    template_version="v1",
                    prompt_config_snapshot=dummy_data.get("prompt_config")[0],
                    variable_names=instance.variable_names,
                    evaluation_configs={},
                    output=None,
                    is_draft=True,
                )

            # Add metadata
            response.data.update({"last_saved": instance.updated_at})

            # Get output and variables
            response.data["prompt_config"] = (
                [execution.prompt_config_snapshot]
                if isinstance(execution.prompt_config_snapshot, dict)
                else execution.prompt_config_snapshot
            )
            response.data["version"] = execution.template_version
            response.data["variable_names"] = execution.variable_names
            response.data["output"] = execution.output
            response.data["is_draft"] = execution.is_draft
            response.data["metadata"] = execution.metadata

            variable_names = (
                execution.variable_names if execution.variable_names else {}
            )

            # Calculate max length for iterations
            max_len = max(
                (len(values) for values in variable_names.values()), default=1
            )

            # Get error message
            error_message = getattr(execution, "error_message", None)

            # Handle missing output - check cache
            if not response.data["output"]:
                response.data["output"] = []
                version = execution.template_version

                for i in range(max_len):
                    cache_key = (
                        f"prompt_template_{instance.id}_{version}_run_prompt_{i}"
                    )
                    cached_data = cache.get(cache_key)

                    if cached_data:
                        response.data["output"].append(cached_data.get("response"))
                        response.data["last_chunk_pos"] = cached_data.get(
                            "last_chunk_pos"
                        )
                        error_message = cached_data.get("error") or error_message

            response.data["error_message"] = error_message
            return response

        except Exception as e:
            logger.exception(f"Error retrieving prompt template: {str(e)}")
            return self._gm.bad_request(f"Failed to retrieve template: {str(e)}")

    @action(detail=False, methods=["get"], url_path="get-template-by-name")
    def get_template_by_name(self, request):
        """
        Retrieve a prompt template by name.
        If no version is specified, returns the default version (is_default=True).
        If a version is specified, returns that specific version.
        """
        try:
            name = request.query_params.get("name")
            version = request.query_params.get("version")

            if not name:
                return self._gm.bad_request("Template name is required")

            template = get_object_or_404(self.get_queryset(), name__exact=name)

            # Base queryset: all prompt versions for this template
            base_qs = PromptVersion.objects.filter(
                original_template=template,
                original_template__organization=getattr(request, "organization", None)
                or request.user.organization,
                deleted=False,
            )

            execution = None

            # If a specific version is requested, fetch that directly.
            if version:
                try:
                    execution = base_qs.get(template_version=version)
                except PromptVersion.DoesNotExist:
                    return self._gm.bad_request(
                        f"No version '{version}' found for this template"
                    )
            else:
                # If no version specified, try to get the default version
                try:
                    execution = base_qs.get(is_default=True)
                except PromptVersion.DoesNotExist:
                    # If no default version exists, use fallback logic:
                    # 1. Get the latest non-draft version
                    # 2. If no non-draft versions, get the latest version overall
                    try:
                        # First try to get the latest non-draft version
                        execution = (
                            base_qs.filter(is_draft=False)
                            .order_by("-created_at")
                            .first()
                        )
                        if not execution:
                            # If no non-draft versions exist, get the latest version overall
                            execution = base_qs.order_by("-created_at").first()

                        if not execution:
                            return self._gm.bad_request(
                                "No versions found for this template."
                            )

                    except Exception as e:
                        logger.exception(f"Error getting fallback version: {str(e)}")
                        return self._gm.bad_request(
                            "Error retrieving template version. Please specify a version explicitly."
                        )

            if not execution:
                return self._gm.bad_request("No valid version found for this template")

            serializer = PromptTemplateSerializer(template)
            response_data = serializer.data

            response_data["metadata"] = execution.metadata
            # Add execution-specific data
            response_data.update(
                {
                    "prompt_config": (
                        [execution.prompt_config_snapshot]
                        if isinstance(execution.prompt_config_snapshot, dict)
                        else execution.prompt_config_snapshot
                    ),
                    "version": execution.template_version,
                    "variable_names": execution.variable_names,
                    "output": execution.output,
                    "is_draft": execution.is_draft,
                    "is_default": execution.is_default,
                    "is_fallback_version": not execution.is_default
                    and not version,  # Indicates if this is a fallback version
                }
            )

            return Response(response_data)

        except Exception as e:
            logger.exception(f"Error retrieving prompt template: {str(e)}")
            return self._gm.bad_request(f"Failed to retrieve template: {str(e)}")

    @action(detail=True, methods=["get"], url_path="stop-streaming")
    def stop_streaming(self, request, *args, **kwargs):
        versions = request.GET.getlist("version", [])
        template = get_object_or_404(self.get_queryset(), pk=kwargs["pk"])
        session_uuids = request.GET.getlist("session_uuid", [])

        try:
            versions = list(set(versions))
            if len(versions) > 3:
                return self._gm.bad_request(get_error_message("MAX_3_VERSIONS_ALLOWED"))
            if not all(re.match(r"^v\d+$", version) for version in versions):
                raise ValueError("Invalid version format")

            # Use centralized WebSocket manager
            ws_manager = get_websocket_manager(
                getattr(request, "organization", None) or request.user.organization.id
            )
            result = ws_manager.handle_stop_streaming_request(
                str(template.id), versions, session_uuids
            )

            if result["status"] == "success":
                return self._gm.success_response(result["message"])
            else:
                return self._gm.bad_request(result["message"])

        except Exception as e:
            logger.exception(f"Error in stop_streaming: {str(e)}")
            return self._gm.bad_request(get_error_message("INVALID_VERSION_PROVIDED"))

    @action(detail=True, methods=["get"], url_path="all-variables")
    def get_all_variables(self, request, pk=None):
        """Get all variables from template and its executions"""
        try:
            parent_template = self.get_object()

            # # Fetch all related executions efficiently using values()
            # executions = PromptVersion.objects.filter(
            #     original_template=parent_template,
            #     original_template__organization=getattr(request, "organization", None) or request.user.organization,
            #     deleted=False
            # ).values_list('variable_names', flat=True)

            # # Aggregate variables using dictionary comprehension
            # all_variables = {}
            # for vars_dict in executions:
            #     if vars_dict:
            #         all_variables.update(vars_dict)

            return self._gm.success_response(
                {"variable_names": parent_template.variable_names}
            )

        except Exception as e:
            logger.exception(f"Error getting variables: {str(e)}")
            return self._gm.bad_request(get_error_message("UNABLE_TO_GET_VARIABLES"))

    @action(detail=True, methods=["get"], url_path="get-next-version")
    def get_next_version(self, request, pk=None):
        """Get the next version of the PromptTemplate"""
        try:
            parent_template = self.get_object()
            total_executions = PromptVersion.objects.filter(
                original_template=parent_template,
                original_template__organization=getattr(request, "organization", None)
                or request.user.organization,
                deleted=False,
            ).count()
            return self._gm.success_response(
                {"next_version": f"v{total_executions + 1}"}
            )
        except Exception as e:
            logger.exception(f"Error getting next version: {str(e)}")
            return self._gm.bad_request(get_error_message("UNABLE_TO_GET_NEXT_VERSION"))

    @action(detail=True, methods=["post"], url_path="compare-versions")
    def compare_versions(self, request, pk=None):
        """
        Compare different versions of the PromptTemplate.
        """
        try:
            parent_template = self.get_object()
            serializer = CompareVersionsSerializer(data=request.data)
            if not serializer.is_valid():
                return self._gm.bad_request(str(parse_serialized_errors(serializer)))

            versions = serializer.validated_data.get("versions", [])
            is_run = serializer.validated_data.get("is_run", False)

            if len(versions) < 2:
                return self._gm.bad_request(
                    "At least two versions are required for comparison."
                )

            # Get all execution objects in one query
            execution_objs = list(
                PromptVersion.objects.filter(
                    original_template__organization=getattr(
                        self.request, "organization", None
                    )
                    or self.request.user.organization,
                    template_version__in=versions,
                    deleted=False,
                    original_template__id=parent_template.id,
                )
            )
            execution_map = {obj.template_version: obj for obj in execution_objs}
            if len(execution_map) != len(versions):
                return self._gm.bad_request(get_error_message("VERSION_NOT_EXIST"))

            # Process each version
            response = []
            for version in versions:
                obj = execution_map[version]

                # Run if requested
                if is_run:
                    template = obj.original_template
                    # _, created = PromptVersion.objects.get_or_create(
                    #     original_template=template.root_template or template,
                    #     template_version=version if version else template.version,
                    #     defaults={
                    #         'prompt_config_snapshot': template.prompt_config[0],
                    #         'template_name': template.name,
                    #         'variable_names': template.variable_names,
                    #         'evaluation_configs': template.evaluation_configs,
                    #         'output': None,
                    #         'is_draft': False
                    #     }
                    # )
                    # if created:
                    #     template.is_draft = False
                    #     template.save(update_fields=['is_draft'])
                    _PROMPT_TEMPLATE_EXECUTOR.submit(
                        _safe_background_task(
                            self.run,  # Your existing sync function
                            template,
                            obj,
                            getattr(self.request, "organization", None)
                            or self.request.user.organization.id,
                            version,
                            is_run,
                            request.workspace,
                        )
                    )

                # Serialize response
                response.append(PromptHistoryExecutionSerializer(obj).data)

            return self._gm.success_response(
                {"data": response, "next_version": f"v{len(execution_objs) + 1}"}
            )

        except Exception as e:
            logger.exception(f"Error in comparing versions: {str(e)}")
            return self._gm.bad_request(
                f"Failed to compare versions: {get_error_message('VERSION_COMPARISON_FAILED')}"
            )

    @action(detail=True, methods=["post"], url_path="add-new-draft")
    def add_new_draft(self, request, pk=None):
        """
        Create a new draft version of the PromptTemplate and return its details.
        """
        try:
            parent_template = self.get_object()
            validated_data = MultipleDraftSerializer(request.data).data

            # Use atomic version generation for the first draft
            base_version = get_next_version_number(
                parent_template.id,
                getattr(request, "organization", None) or request.user.organization.id,
            )
            logger.info(f"Base version: {base_version}")

            prompt_versions = []
            for i, prompt in enumerate(validated_data.get("new_prompts", [])):
                prompt_updates = prompt.get("prompt_config", [])
                variable_names = prompt.get("variable_names", {})
                evaluation_configs = prompt.get("evaluation_configs", [])
                metadata = prompt.get("metadata", {})

                prompt_versions.append(
                    PromptVersion(
                        template_version=f"v{base_version + i}",
                        original_template=parent_template,
                        prompt_config_snapshot=(
                            prompt_updates[0] if prompt_updates else {}
                        ),
                        variable_names=variable_names,
                        evaluation_configs=evaluation_configs,
                        is_default=False,
                        is_draft=True,
                        output=None,
                        metadata=metadata,
                    )
                )

            PromptVersion.objects.bulk_create(prompt_versions)

            if request.headers.get("X-Api-Key") is not None:
                properties = get_mixpanel_properties(
                    user=request.user, prompt_template=parent_template
                )
                track_mixpanel_event(
                    MixpanelEvents.SDK_PROMPT_CREATE_DRAFT.value, properties
                )

            # Return the draft details
            return self._gm.success_response(
                PromptHistoryExecutionSerializer(prompt_versions, many=True).data
            )

        except Exception as e:
            # Handle potential duplicate version error for bulk_create
            if "unique" in str(e).lower() or "duplicate" in str(e).lower():
                try:
                    time.sleep(0.5)
                    # Retry with fresh version numbers
                    base_version = get_next_version_number(
                        parent_template.id,
                        getattr(request, "organization", None)
                        or request.user.organization.id,
                    )

                    # Recreate prompt_versions with new version numbers
                    prompt_versions = []
                    for i, prompt in enumerate(validated_data.get("new_prompts", [])):
                        prompt_updates = prompt.get("prompt_config", [])
                        variable_names = prompt.get("variable_names", {})
                        evaluation_configs = prompt.get("evaluation_configs", [])
                        metadata = prompt.get("metadata", {})
                        prompt_versions.append(
                            PromptVersion(
                                template_version=f"v{base_version + i}",
                                original_template=parent_template,
                                prompt_config_snapshot=(
                                    prompt_updates[0] if prompt_updates else {}
                                ),
                                variable_names=variable_names,
                                evaluation_configs=evaluation_configs,
                                is_default=False,
                                is_draft=True,
                                output=None,
                                metadata=metadata,
                            )
                        )

                    PromptVersion.objects.bulk_create(prompt_versions)
                    return self._gm.success_response(
                        PromptHistoryExecutionSerializer(
                            prompt_versions, many=True
                        ).data
                    )

                except Exception as retry_e:
                    logger.exception(f"Error in retry bulk_create: {str(retry_e)}")
                    return self._gm.bad_request(
                        f"Failed to create draft after retry: {get_error_message('DRAFT_CREATION_FAILED')}"
                    )

            logger.exception(f"Error in creation of draft: {str(e)}")
            return self._gm.bad_request(
                f"Failed to create draft: {get_error_message('DRAFT_CREATION_FAILED')}"
            )

    @action(detail=False, methods=["post"], url_path="create-draft")
    def create_draft(self, request):
        """
        Create a draft version of the PromptTemplate and return its details.
        """
        try:
            # Get UI data if provided
            prompt_config = request.data.get("prompt_config", [])
            prompt_name = request.data.get("name", "")
            is_draft = request.data.get("is_draft", True)
            variable_names = request.data.get("variable_names", {})
            description = request.data.get("description", "")
            metadata = request.data.get("metadata", {})
            prompt_base_template = request.data.get("prompt_base_template", None)
            prompt_folder = request.data.get("prompt_folder", None)

            if prompt_base_template:
                try:
                    prompt_base_template = PromptBaseTemplate.no_workspace_objects.get(
                        models.Q(workspace=self.request.workspace)
                        | models.Q(is_sample=True),
                        id=prompt_base_template,
                        deleted=False,
                    )
                except Exception as e:
                    return self._gm.bad_request(
                        f"Prompt base template not found: {str(e)}"
                    )

            if prompt_folder:
                try:
                    prompt_folder = PromptFolder.no_workspace_objects.get(
                        id=prompt_folder,
                        organization=getattr(self.request, "organization", None)
                        or self.request.user.organization,
                        deleted=False,
                    )
                except Exception as e:
                    return self._gm.bad_request(f"Prompt folder not found: {str(e)}")

            if not prompt_name:
                # Find the next available number for Untitled-N
                existing_untitled = PromptTemplate.objects.filter(
                    name__startswith="Untitled-",
                    organization=getattr(self.request, "organization", None)
                    or self.request.user.organization,
                ).values_list("name", flat=True)

                # Extract numbers from existing Untitled-N names
                used_numbers = set()
                for name in existing_untitled:
                    try:
                        num = int(name.split("-")[1])
                        used_numbers.add(num)
                    except (IndexError, ValueError):
                        continue

                # Find the first available number
                next_number = 1
                while next_number in used_numbers:
                    next_number += 1

                prompt_name = f"Untitled-{next_number}"
                if prompt_base_template:
                    prompt_name = f"{prompt_name}-{prompt_base_template.name}"

            # Create the draft template with the next available number
            draft_template = PromptTemplate.objects.create(
                name=prompt_name,
                description=description,
                organization=getattr(self.request, "organization", None)
                or self.request.user.organization,
                variable_names=variable_names,
                prompt_folder=prompt_folder,
                created_by=request.user,
            )
            draft_template.collaborators.add(request.user)

            version_obj = PromptVersion.objects.create(
                original_template=draft_template,
                template_version="v1",
                prompt_config_snapshot=prompt_config[0],
                variable_names=variable_names,
                output=None,
                is_draft=is_draft,
                metadata=metadata,
                prompt_base_template=prompt_base_template,
            )
            # Return the draft details
            serializer = PromptHistoryExecutionSerializer(version_obj)
            response = serializer.data
            response["created_version"] = "v1"
            response["id"] = draft_template.id
            response["description"] = draft_template.description
            response["name"] = draft_template.name
            response["root_template"] = draft_template.id
            response["metadata"] = metadata if metadata else {}

            if request.headers.get("X-Api-Key") is not None:
                properties = get_mixpanel_properties(
                    user=request.user, prompt_template=draft_template
                )
                track_mixpanel_event(MixpanelEvents.SDK_PROMPT_CREATE.value, properties)

            return self._gm.success_response(response)

        except Exception as e:
            logger.exception(f"Error in creation of draft: {str(e)}")
            return self._gm.bad_request(f"Failed to create draft: {str(e)}")

    @action(detail=True, methods=["get"], url_path="evaluations")
    def retrieve_evaluations(self, request, pk):
        try:
            parent_template = self.get_object()
            show_var = request.query_params.get("show_var", "false").lower() == "true"
            show_prompts = (
                request.query_params.get("show_prompts", "false").lower() == "true"
            )
            compare = request.query_params.get("compare", "false").lower() == "true"

            versions = request.query_params.get("versions", [])

            if versions and isinstance(versions, str):
                versions = json.loads(versions)

            if not versions:
                return self._gm.bad_request(get_error_message("VERSIONS_REQUIRED"))
            if not compare and len(versions) > 1:
                return self._gm.bad_request(
                    get_error_message("SINGLE_VERSION_REQUIRED")
                )

            response = {}
            executions = list(
                PromptVersion.objects.filter(
                    template_version__in=versions,
                    original_template=parent_template,
                    original_template__organization=getattr(
                        request, "organization", None
                    )
                    or request.user.organization,
                    deleted=False,
                ).order_by("created_at")
            )
            all_variables = {}

            for execution in executions:
                all_variables.update(execution.variable_names)

            response["variables"] = all_variables if show_var else None
            evals_used = list(
                PromptEvalConfig.objects.filter(
                    prompt_template=parent_template, deleted=False
                ).select_related("eval_template", "eval_group")
            )

            evaluation_configs = [
                {
                    "id": configuration.id,
                    "name": configuration.name,
                    "mapping": configuration.mapping,
                    "config": configuration.eval_template.config,
                    "params": params_with_defaults_for_response(
                        configuration.eval_template.config, configuration.config
                    )[1],
                    "reverse_output": configuration.eval_template.config.get(
                        "reverse_output", False
                    ),
                    "updated_at": configuration.updated_at,
                    "eval_required_keys": configuration.eval_template.config.get(
                        "required_keys", []
                    ),
                    "eval_group": (
                        configuration.eval_group.name
                        if configuration.eval_group
                        else None
                    ),
                }
                for configuration in evals_used
            ]

            for execution in executions:
                eval_output = {}
                eval_status = {}

                for eval_dict in evals_used:
                    if not eval_dict or not eval_dict.id:
                        continue

                    execution_evaluation_results = execution.evaluation_results or {}
                    eval_result = execution_evaluation_results.get(
                        str(eval_dict.id), {}
                    )

                    if not eval_result or eval_result == {}:
                        eval_status[str(eval_dict.id)] = (
                            StatusType.NOT_STARTED.value
                            if track_running_eval_count(
                                prompt_config_eval_id=str(eval_dict.id), operation="get"
                            )
                            else StatusType.RUNNING.value
                        )
                        eval_output[str(eval_dict.id)] = []
                    else:
                        eval_result = eval_result.get("results") or []
                        eval_status[str(eval_dict.id)] = (
                            StatusType.COMPLETED.value
                            if track_running_eval_count(
                                prompt_config_eval_id=str(eval_dict.id), operation="get"
                            )
                            else StatusType.RUNNING.value
                        )
                        eval_output[str(eval_dict.id)] = eval_result

                response[execution.template_version] = {
                    "output": execution.output,
                    "eval_names": evaluation_configs or [],
                    "eval_output": eval_output,
                    "eval_status": eval_status,
                    "messages": (
                        execution.prompt_config_snapshot.get("messages", [])
                        if show_prompts
                        else None
                    ),
                    "model_detail": execution.prompt_config_snapshot.get(
                        "configuration", {}
                    ).get("model_detail"),
                }

            return self._gm.success_response(response)
        except Exception as e:
            logger.exception(f"Error retrieving evaluations: {str(e)}")
            return self._gm.internal_server_error_response(
                "Failed to retrieve evaluation data"
            )

    @action(detail=True, methods=["post"])
    def run_template(self, request, pk=None):
        """
        Run a prompt template with the given configuration.

        Args:
            request: HTTP request containing:
                - prompt_config: The prompt configuration
                - name: Template name (optional)
                - version: Version to run (optional)
                - variable_names: Variable values (optional)
                - source: Source of the request (optional, default: "prompt")
                - evaluation_configs: Evaluation configurations (optional)
                - is_run: Whether to run the template (optional, default: False)
                - is_sdk: Whether this is an SDK call (optional, default: False)
                - run_index: Specific index to run (optional, default: None)
                    If provided, only runs the specified index of variable_names.
                    If None, runs all indices.
        """
        try:
            parent_template = self.get_object()
            prompt_updates = request.data.get("prompt_config")
            name = request.data.get("name", parent_template.name)
            version_to_run = request.data.get("version")
            variable_names = request.data.get("variable_names", {})
            source = request.data.get("source", "prompt")
            evaluation_configs = request.data.get("evaluation_configs", [])
            is_run = request.data.get("is_run", False)
            run_index = request.data.get("run_index", None)
            placeholders = request.data.get("placeholders", {})

            if (
                PromptTemplate.objects.filter(
                    name=name,
                    organization=getattr(self.request, "organization", None)
                    or self.request.user.organization,
                    deleted=False,
                )
                .exclude(id=parent_template.id)
                .exists()
            ):
                return self._gm.bad_request(get_error_message("TEMPLATE_ALREADY_EXIST"))

            executions = list(
                PromptVersion.objects.filter(
                    original_template=parent_template,
                    original_template__organization=getattr(
                        request, "organization", None
                    )
                    or request.user.organization,
                    deleted=False,
                ).order_by("created_at")
            )
            execution_map = {obj.template_version: obj for obj in executions}
            all_variables = {}
            all_placeholders = {}
            for execution in executions:
                all_variables.update(execution.variable_names)
                if execution.placeholders and len(execution.placeholders) > 0:
                    all_placeholders.update(execution.placeholders)
            all_variables.update(variable_names)
            all_placeholders.update(placeholders)

            if version_to_run:
                execution = execution_map.get(version_to_run)
                if not execution:
                    return self._gm.bad_request(get_error_message("VERSION_NOT_EXIST"))
                if is_run:
                    try:
                        if request.headers.get("X-Api-Key") is not None:
                            properties = get_mixpanel_properties(
                                user=request.user,
                                is_run=is_run,
                                prompt_template=parent_template,
                            )
                            track_mixpanel_event(
                                MixpanelEvents.SDK_PROMPT_RUN.value, properties
                            )
                        if prompt_updates and len(prompt_updates) > 0:
                            execution.prompt_config_snapshot = prompt_updates[0]
                        if variable_names and len(variable_names) > 0:
                            execution.variable_names = variable_names
                        if placeholders and len(placeholders) > 0:
                            execution.placeholders = placeholders

                        if evaluation_configs and len(evaluation_configs) > 0:
                            execution.evaluation_configs = evaluation_configs
                        parent_template.variable_names = all_variables
                        parent_template.placeholders = all_placeholders
                        parent_template.save()
                        execution.save()

                        # Use the retry helper function
                        submit_with_retry(
                            _PROMPT_TEMPLATE_EXECUTOR,
                            self.run,
                            parent_template,
                            execution,
                            getattr(request, "organization", None)
                            or request.user.organization.id,
                            version_to_run,
                            is_run,
                            None,
                            run_index,
                            request.workspace,
                        )

                        return self._gm.success_response(
                            {
                                "prompt_config": [execution.prompt_config_snapshot],
                                "variable_names": execution.variable_names,
                                "evaluation_configs": evaluation_configs,
                                "template_id": parent_template.id,
                                "placeholders": execution.placeholders,
                            }
                        )
                    except Exception as e:
                        logger.exception(f"Error in running older version: {e}")
                        return self._gm.bad_request(
                            get_error_message("UNABLE_TO_RUN_TEMPLATE").format(
                                parent_template.name
                            )
                        )
                else:
                    if execution.is_draft is True:
                        execution.prompt_config_snapshot = prompt_updates[0]
                        execution.variable_names = variable_names
                        execution.evaluation_configs = evaluation_configs
                        if placeholders and len(placeholders) > 0:
                            execution.placeholders = placeholders
                        execution.save()
                        parent_template.placeholders = all_placeholders
                        parent_template.variable_names = all_variables
                        parent_template.collaborators.add(request.user)
                        parent_template.save()
                        return self._gm.success_response(
                            {
                                "prompt_config": [execution.prompt_config_snapshot],
                                "variable_names": execution.variable_names,
                                "evaluation_configs": evaluation_configs,
                                "template_id": parent_template.id,
                                "placeholders": execution.placeholders,
                            }
                        )

            if source == "dataset":
                next_version = get_next_version_number(
                    parent_template.id,
                    getattr(request, "organization", None)
                    or request.user.organization.id,
                )

                try:
                    PromptVersion.objects.create(
                        original_template=parent_template,
                        template_version=f"v{next_version}",
                        prompt_config_snapshot=(
                            prompt_updates[0]
                            if prompt_updates and len(prompt_updates) > 0
                            else {}
                        ),
                        variable_names=variable_names,
                        evaluation_configs=evaluation_configs,
                        placeholders=placeholders,
                        output=None,
                    )

                except Exception as e:
                    # Handle potential duplicate version error
                    if "unique" in str(e).lower() or "duplicate" in str(e).lower():
                        time.sleep(0.5)
                        # Retry with a fresh version number
                        next_version = get_next_version_number(
                            parent_template.id,
                            getattr(request, "organization", None)
                            or request.user.organization.id,
                        )
                        PromptVersion.objects.create(
                            original_template=parent_template,
                            template_version=f"v{next_version}",
                            prompt_config_snapshot=(
                                prompt_updates[0]
                                if prompt_updates and len(prompt_updates) > 0
                                else {}
                            ),
                            variable_names=variable_names,
                            evaluation_configs=evaluation_configs,
                            placeholders=placeholders,
                            output=None,
                        )
                    else:
                        raise e

                return self._gm.success_response(
                    {
                        "prompt_config": prompt_updates,
                        "variable_names": all_variables,
                        "evaluation_configs": evaluation_configs,
                        # "status": parent_template.status,
                        "template_id": parent_template.id,
                        "created_version": f"v{next_version}",
                        "prompt_name": parent_template.name,
                        "placeholders": all_placeholders,
                    }
                )

            latest_execution = executions[-1]
            if latest_execution.is_draft is True:
                latest_execution.prompt_config_snapshot = prompt_updates[0]
                latest_execution.variable_names = variable_names
                latest_execution.evaluation_configs = evaluation_configs
                latest_execution.placeholders = placeholders
                latest_execution.save()
                version_to_return = latest_execution.template_version
            else:
                next_version = get_next_version_number(
                    parent_template.id,
                    getattr(request, "organization", None)
                    or request.user.organization.id,
                )
                try:
                    PromptVersion.objects.create(
                        original_template=parent_template,
                        template_version=f"v{next_version}",
                        prompt_config_snapshot=prompt_updates[0],
                        variable_names=variable_names,
                        evaluation_configs=evaluation_configs,
                        output=None,
                        is_draft=True,
                        placeholders=placeholders,
                    )
                    version_to_return = f"v{next_version}"
                except Exception as e:
                    # Handle potential duplicate version error
                    if "unique" in str(e).lower() or "duplicate" in str(e).lower():
                        time.sleep(0.5)
                        # Retry with a fresh version number
                        next_version = get_next_version_number(
                            parent_template.id,
                            getattr(request, "organization", None)
                            or request.user.organization.id,
                        )
                        PromptVersion.objects.create(
                            original_template=parent_template,
                            template_version=f"v{next_version}",
                            prompt_config_snapshot=prompt_updates[0],
                            variable_names=variable_names,
                            evaluation_configs=evaluation_configs,
                            output=None,
                            is_draft=True,
                            placeholders=placeholders,
                        )
                        version_to_return = f"v{next_version}"
                    else:
                        raise e
            parent_template.name = name
            parent_template.collaborators.add(request.user)
            parent_template.updated_at = timezone.now()
            parent_template.variable_names = all_variables
            parent_template.placeholders = all_placeholders
            parent_template.save()

            return self._gm.success_response(
                {
                    "prompt_config": prompt_updates,
                    "variable_names": all_variables,
                    "evaluation_configs": evaluation_configs,
                    "template_id": parent_template.id,
                    "version": version_to_return,
                    "placeholders": all_placeholders,
                }
            )
        except Exception as e:
            logger.exception(f"Error in running template: {str(e)}")
            return self._gm.bad_request(get_error_message("UNABLE_TO_RUN_TEMPLATE"))

    @action(detail=True, methods=["get"], url_path="get-run-status")
    def get_run_status(self, request, pk=None):
        """
        Get the current status and results of a template run
        """
        try:
            template = get_object_or_404(
                PromptTemplate.objects.filter(
                    id=pk,
                    organization=getattr(request, "organization", None)
                    or request.user.organization,
                    deleted=False,
                )
            )

            template_version_qp = request.query_params.get("template_version")
            version = template_version_qp

            parent_template = getattr(template, "root_template", None) or template

            # Get associated executions
            executions = list(
                PromptVersion.objects.filter(
                    original_template=parent_template,
                    original_template__organization=getattr(
                        request, "organization", None
                    )
                    or request.user.organization,
                    deleted=False,
                ).order_by("-is_default", "-created_at")
            )

            if version:
                execution = next(
                    (e for e in executions if e.template_version == version), None
                )
            else:
                execution = executions[0]
            data = PromptHistoryExecutionSerializer(execution).data
            # error_message = template.error_message if hasattr(template, 'error_message') else None,
            variable_names = template.variable_names or {}

            # variable_names can be either a dict (normal case) or a list (older templates).
            if isinstance(variable_names, dict):
                max_len = max(
                    (len(values) for values in variable_names.values()), default=1
                )
            elif isinstance(variable_names, list):
                # Treat each element in list as one run iteration
                max_len = max(len(variable_names), 1)
            else:
                max_len = 1

            if not isinstance(data.get("output"), list):
                data["output"] = []

            # Resolve which version we are interested in for cache lookup
            version_to_use = (
                version
                or getattr(execution, "template_version", None)
                or getattr(execution, "templateVersion", None)
            )

            error_message = None
            still_streaming = False
            for i in range(max_len):
                cache_key = (
                    f"prompt_template_{template.id}_{version_to_use}_run_prompt_{i}"
                )
                cached_data = cache.get(cache_key)

                if cached_data:
                    output = cached_data.get("response")
                    last_chunk_pos = cached_data.get("last_chunk_pos")

                    data["output"].append(output)
                    data["last_chunk_pos"] = last_chunk_pos
                    error_message = cached_data.get("error")

                    # Any cache entry means that iteration is still streaming
                    still_streaming = True

                else:
                    error_message = (
                        template.error_message
                        if hasattr(template, "error_message")
                        else None
                    )

            run_status = getattr(template, "status", None) or getattr(
                execution, "status", None
            )
            if run_status is None:
                finished = not still_streaming and len(data["output"]) == max_len
                run_status = "completed" if finished else "running"

            return self._gm.success_response(
                {
                    "status": run_status,
                    "error_message": error_message,
                    "executions_result": data,
                }
            )
        except Exception as e:
            logger.exception(f"Error in getting run status: {str(e)}")
            return self._gm.bad_request(get_error_message("UNABLE_TO_GET_RUN_STATUS"))

    def run(
        self,
        template,
        execution,
        organization_id,
        version_to_run,
        is_run=None,
        is_sdk=False,
        run_index=None,
        workspace=None,
        ws_manager=None,
    ):
        try:
            close_old_connections()
            organization = Organization.objects.get(id=organization_id)
        except Organization.DoesNotExist:
            organization = None

        if not ws_manager:
            ws_manager = get_websocket_manager(organization_id)

        # Notify UI that process has started and all inputs will be associated with this session UUID
        if not is_sdk:
            total_iterations = (
                1
                if run_index is not None
                else max(
                    (
                        len(values)
                        for values in (execution.variable_names or {}).values()
                    ),
                    default=1,
                )
            )
            ws_manager.notify_process_started(
                template_id=str(template.id),
                version=(
                    version_to_run if version_to_run else execution.template_version
                ),
                execution_id=str(execution.id) if execution else None,
                process_type="run_prompt" if is_run == "prompt" else "run_evaluation",
                total_iterations=total_iterations,
            )

        try:
            responses = []
            value_infos = []
            all_evaluations = {}  # Changed to dict instead of list

            # for config in configs:

            variable_names = execution.variable_names or {}
            config = execution.prompt_config_snapshot
            max_len = max(
                (len(values) for values in variable_names.values()), default=1
            )
            parsed_placeholder_messages = []
            placeholders = execution.placeholders
            if placeholders:
                for _placeholder_name, placeholder_messages in placeholders.items():
                    if len(placeholder_messages) > 0:
                        parsed_messages = validate_and_parse_placeholder(
                            placeholder_messages
                        )
                        parsed_placeholder_messages.extend(parsed_messages)

            # If run_index is specified, only process that specific index
            # Otherwise, process all indices
            indices_to_process = (
                [run_index] if run_index is not None else range(max_len)
            )

            # If run_index is specified, validate it's within bounds
            if run_index is not None:
                if run_index < 0 or run_index >= max_len:
                    raise ValueError(
                        f"run_index {run_index} is out of bounds. Valid range: 0 to {max_len - 1}"
                    )

            # Initialize responses and value_infos with existing data if run_index is specified
            if run_index is not None and execution.output:
                responses = (
                    list(execution.output)
                    if isinstance(execution.output, list)
                    else [execution.output]
                )
                value_infos = (
                    list(execution.metadata)
                    if isinstance(execution.metadata, list)
                    else [execution.metadata]
                )
                # Ensure lists are long enough
                while len(responses) < max_len:
                    responses.append(None)
                while len(value_infos) < max_len:
                    value_infos.append(None)
            else:
                responses = []
                value_infos = []
            for i in indices_to_process:
                # Map the current index to the variable values
                variable_combination = {
                    key: values[i] if i < len(values) else None
                    for key, values in variable_names.items()
                }
                try:
                    # Replace variables and get the response for this index
                    prompt_messages = config.get("messages").copy() or []
                    if parsed_placeholder_messages:
                        prompt_messages[:0] = parsed_placeholder_messages

                    messages_with_replacement = replace_variables(
                        prompt_messages,
                        variable_combination,
                        config.get("configuration", {}).get("model"),
                        template_format=config.get("configuration", {}).get("template_format"),
                    )
                    messages_with_replacement = remove_empty_text_from_messages(
                        messages_with_replacement
                    )
                    tools = config.get("configuration", {}).get("tools", [])
                    tools_to_send = []
                    for tool in tools:
                        tool_config = tool.get("config")
                        if tool_config:
                            tools_to_send.append(tool_config)

                    run_prompt = RunPrompt(
                        model=config.get("configuration", {}).get("model"),
                        organization_id=organization_id,
                        messages=messages_with_replacement,
                        temperature=config.get("configuration", {}).get("temperature"),
                        frequency_penalty=config.get("configuration", {}).get(
                            "frequency_penalty"
                        ),
                        presence_penalty=config.get("configuration", {}).get(
                            "presence_penalty"
                        ),
                        max_tokens=config.get("configuration", {}).get("max_tokens"),
                        top_p=config.get("configuration", {}).get("top_p"),
                        response_format=config.get("configuration", {}).get(
                            "response_format"
                        ),
                        tool_choice=config.get("configuration", {}).get("tool_choice"),
                        tools=tools_to_send,
                        output_format=config.get("configuration", {}).get(
                            "output_format"
                        ),
                        ws_manager=ws_manager,
                        workspace_id=workspace.id if workspace else None,
                    )

                    # Get the response and value info
                    if is_run == "prompt":
                        response, value_info = run_prompt.litellm_response(
                            not is_sdk,
                            template.id,
                            (
                                version_to_run
                                if version_to_run
                                else execution.template_version
                            ),
                            i,
                            max_len,
                            "run_prompt",
                        )
                        metadata = value_info.get("metadata", {})
                        token_config = metadata.get("usage", {})
                        # print("old token config is here:",token_config)
                        token_config = {
                            "input_tokens": token_config.get("total_tokens", 0)
                        }
                        # print("token config is here:",token_config)
                        if organization:
                            log_and_deduct_cost_for_api_request(
                                organization,
                                APICallTypeChoices.PROMPT_BENCH.value,
                                config=token_config,
                                source="run_prompt_gen",
                                workspace=workspace,
                            )
                            # log_and_deduct_cost_for_api_request_async = sync_to_async(log_and_deduct_cost_for_api_request)
                            # async_to_sync(log_and_deduct_cost_for_api_request_async)(organization, APICallTypeChoices.PROMPT_BENCH.value, config=token_config, source="run_prompt_gen")

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
                                        org_id=str(organization.id),
                                        event_type=APICallTypeChoices.PROMPT_BENCH.value,
                                        properties={
                                            "source": "run_prompt_gen",
                                            "source_id": str(template.id),
                                        },
                                    )
                                )
                            except Exception:
                                pass  # Metering failure must not break the action

                        if run_index is not None:
                            # Update specific index
                            responses[i] = response
                            value_infos[i] = value_info.get("metadata")
                        else:
                            # Append for all indices
                            responses.append(response)
                            value_infos.append(value_info.get("metadata"))
                        execution.metadata = value_infos
                        execution.output = responses
                        execution.is_draft = False

                except Exception as e:
                    logger.exception(f"Error for index {i}: {e}")
                    if not is_sdk:
                        ws_manager.send_error_message(
                            template_id=str(template.id),
                            version=execution.template_version,
                            error=str(e),
                            result_index=i,
                            num_results=max_len,
                        )
                    if run_index is not None:
                        # Update specific index with error
                        responses[i] = str(e)
                    else:
                        # Append error for all indices
                        responses.append(str(e))
                    execution.output = responses
                    execution.is_draft = False
                    execution.updated_at = timezone.now()

                execution.save(
                    update_fields=["output", "is_draft", "updated_at", "metadata"]
                )
                # Clean up cache data for all indices using WebSocket manager
                if run_index is not None:
                    # Clean up only the specific index
                    ws_manager.cleanup_streaming_data_for_indices(
                        template_id=str(template.id),
                        version=execution.template_version,
                        max_index=run_index + 1,
                    )
                else:
                    # Clean up all indices
                    ws_manager.cleanup_streaming_data_for_indices(
                        template_id=str(template.id),
                        version=execution.template_version,
                        max_index=max_len,
                    )

            # Send all completed message using WebSocket manager
            if not is_sdk:
                ws_manager.send_all_completed_message(
                    template_id=str(template.id), version=execution.template_version
                )

            return {"result": all_evaluations}

        except Exception as e:
            logger.exception(f"Error in run method: {e}")
            responses = [str(e)]
            value_infos = [None]
            if not is_sdk:
                result_index = run_index if run_index is not None else 0
                num_results = 1 if run_index is not None else 1
                ws_manager.send_error_message(
                    template_id=str(template.id),
                    version=(
                        version_to_run if version_to_run else execution.template_version
                    ),
                    error=str(e),
                    result_index=result_index,
                    num_results=num_results,
                )
            return {"responses": responses, "evaluations": value_infos}
        finally:
            close_old_connections()

    @action(detail=True, methods=["get"], url_path="evaluation-configs")
    def get_evaluation_configs(self, request, pk=None):
        """
        Get the evaluation configurations for a specific prompt template.

        Args:
            request: HTTP request
            pk: PromptTemplate ID

        Returns:
            Response with evaluation configurations data
        """
        try:
            # Get the prompt template
            template = self.get_object()

            # Get the evaluation configs
            evaluation_configs = PromptEvalConfig.objects.filter(
                prompt_template=template, deleted=False
            ).select_related("eval_template", "eval_group")

            response = []
            for config in evaluation_configs:
                function_params_schema, params = params_with_defaults_for_response(
                    config.eval_template.config,
                    config.config,
                )
                response.append(
                    {
                        "id": str(config.id),
                        "eval_template_id": str(config.eval_template.id),
                        "name": config.name,
                        "mapping": config.mapping,
                        "config": config.eval_template.config,
                        "params": params,
                        "function_params_schema": function_params_schema,
                        "eval_required_keys": config.eval_template.config.get(
                            "required_keys", []
                        ),
                        "updated_at": config.updated_at.isoformat(),
                        "eval_group": (
                            config.eval_group.name if config.eval_group else None
                        ),
                    }
                )

            return self._gm.success_response(
                {
                    "template_id": str(template.id),
                    "template_name": template.name,
                    "evaluation_configs": response,
                }
            )

        except Exception as e:
            logger.exception(f"Error fetching evaluation configs: {str(e)}")
            return self._gm.bad_request(
                f"Failed to fetch evaluation configurations: {str(e)}"
            )

    @action(detail=True, methods=["post"], url_path="update-evaluation-configs")
    def update_evaluation_configs(self, request, pk=None):
        """
        Add or update evaluation configurations for a PromptTemplate.

        This endpoint allows adding new evaluation configurations or updating
        existing ones in a PromptTemplate. If is_run is true, it will also
        run evaluations on specified versions (or latest version if none specified).

        Args:
            pk: PromptTemplate ID

        Returns:
            Response with updated PromptTemplate data or error message
        """
        try:
            template = self.get_object()

            serializer = SingleEvaluationConfigSerializer(data=request.data)
            if not serializer.is_valid():
                errors = parse_serialized_errors(serializer)
                return self._gm.bad_request(str(errors))

            new_config = serializer.validated_data
            eval_id = new_config.get("id")
            is_run = request.data.get("is_run", False)
            version_to_run = request.data.get("version_to_run", [])

            try:
                eval_template = EvalTemplate.no_workspace_objects.get(id=eval_id)
            except EvalTemplate.DoesNotExist:
                return self._gm.bad_request(
                    f"Evaluation template with ID {eval_id} does not exist."
                )

            eval_name = new_config.get("name")
            if PromptEvalConfig.objects.filter(
                name=eval_name, prompt_template=template, deleted=False
            ).exists():
                return self._gm.bad_request(
                    get_error_message("PROMPT_EVAL_TEMPLATE_EXISTS")
                )

            new_config["eval_required_keys"] = eval_template.config.get(
                "required_keys", []
            )

            kb = None
            if new_config.get("kb_id", None):
                try:
                    kb = KnowledgeBaseFile.objects.get(id=new_config.get("kb_id"))
                except KnowledgeBaseFile.DoesNotExist:
                    pass
            prompt_eval = PromptEvalConfig.objects.create(
                name=eval_name,
                prompt_template=template,
                eval_template=eval_template,
                mapping=new_config.get("mapping", {}),
                config=normalize_eval_runtime_config(
                    eval_template.config,
                    {
                        **(new_config.get("config", {}) or {}),
                        "params": (
                            new_config.get("params", {})
                            if new_config.get("params", {})
                            else (new_config.get("config", {}) or {}).get("params", {})
                        ),
                    },
                ),
                user=request.user,
                kb=kb,
                error_localizer=new_config.get("error_localizer", False),
            )

            # If is_run is true, run evaluations on specified versions
            if is_run:
                try:
                    # If no versions specified, use the latest version
                    if not version_to_run:
                        latest_version = (
                            PromptVersion.objects.filter(
                                original_template=template, deleted=False
                            )
                            .order_by("-created_at")
                            .first()
                        )
                        if latest_version:
                            version_to_run = [latest_version.template_version]

                    if version_to_run:
                        # Check if all versions exist
                        existing_versions = PromptVersion.objects.filter(
                            original_template=template,
                            template_version__in=version_to_run,
                        ).values_list("template_version", flat=True)

                        missing_versions = set(version_to_run) - set(existing_versions)
                        if missing_versions:
                            return self._gm.success_response(
                                {
                                    "message": "Evaluation configuration updated successfully but some versions do not exist",
                                    "prompt_eval_config_id": str(prompt_eval.id),
                                    "missing_versions": list(missing_versions),
                                }
                            )

                        executions = PromptVersion.objects.filter(
                            original_template=template,
                            original_template__organization=getattr(
                                request, "organization", None
                            )
                            or request.user.organization,
                            template_version__in=version_to_run,
                            deleted=False,
                        )

                        if executions.exists():
                            for execution in executions:
                                variable_names = execution.variable_names or {}
                                max_len = max(
                                    (len(values) for values in variable_names.values()),
                                    default=1,
                                )

                                track_running_eval_count(
                                    start=True,
                                    prompt_config_eval_id=prompt_eval.id,
                                    operation="set",
                                    num=max_len,
                                )

                            # Run evaluation using the common logic
                            _PROMPT_TEMPLATE_EXECUTOR.submit(
                                _safe_background_task(
                                    self.run_evals_task,
                                    template,
                                    executions,
                                    [str(prompt_eval.id)],
                                    None,
                                    user_id=str(request.user.id),
                                )
                            )
                            return self._gm.success_response(
                                {
                                    "message": "Evaluation configuration updated successfully and evaluation started",
                                    "prompt_eval_config_id": str(prompt_eval.id),
                                    "versions": version_to_run,
                                }
                            )
                        else:
                            return self._gm.success_response(
                                {
                                    "message": "Evaluation configuration updated successfully. No valid versions available to run evaluations on.",
                                    "prompt_eval_config_id": str(prompt_eval.id),
                                }
                            )
                    else:
                        return self._gm.success_response(
                            {
                                "message": "Evaluation configuration updated successfully. No versions available to run evaluations on.",
                                "prompt_eval_config_id": str(prompt_eval.id),
                            }
                        )
                except Exception as e:
                    logger.exception(
                        f"Error starting evaluation after config creation: {str(e)}"
                    )
                    return self._gm.success_response(
                        {
                            "message": "Evaluation configuration updated successfully but failed to start evaluation",
                            "prompt_eval_config_id": str(prompt_eval.id),
                            "error": str(e),
                        }
                    )

            return self._gm.success_response(
                {
                    "message": "Evaluation configuration updated successfully",
                    "prompt_eval_config_id": str(prompt_eval.id),
                }
            )

        except Exception as e:
            logger.exception(f"Error updating evaluation configs: {str(e)}")
            return self._gm.bad_request(
                f"Failed to update evaluation configurations: {str(e)}"
            )

    @action(detail=True, methods=["delete"], url_path="delete-evaluation-config")
    def delete_evaluation_config(self, request, pk=None):
        """
        Delete an evaluation configuration by name from a PromptTemplate.

        This endpoint allows removing an evaluation configuration from a PromptTemplate
        based on its unique name.

        Args:
            pk: PromptTemplate ID

        Returns:
            Response with updated PromptTemplate data or error message
        """
        try:
            template = PromptTemplate.objects.get(
                id=pk,
                organization=getattr(request, "organization", None)
                or request.user.organization,
            )
            config_id = request.query_params.get("id")

            if not config_id:
                return self._gm.bad_request("Evaluation configuration id is required")

            try:
                eval_config = PromptEvalConfig.objects.get(
                    id=config_id, prompt_template=template
                )
                eval_config.deleted = True
                eval_config.deleted_at = timezone.now()
                eval_config.save(update_fields=["deleted", "deleted_at"])

            except PromptEvalConfig.DoesNotExist:
                return self._gm.bad_request(
                    get_error_message(
                        f"Evaluation Configuration does not exist for {config_id}"
                    )
                )

            return self._gm.success_response(
                "Evaluation configuration deleted successfully"
            )

        except Exception as e:
            logger.exception(f"Error deleting evaluation config: {str(e)}")
            return self._gm.bad_request(
                f"Failed to delete evaluation configuration: {str(e)}"
            )

    @action(detail=True, methods=["post"], url_path="run-evals-on-multiple-versions")
    def run_evals_on_multiple_versions(self, request, pk=None):
        try:
            template = PromptTemplate.objects.get(
                id=pk,
                organization=getattr(request, "organization", None)
                or request.user.organization,
            )
            version_to_run = request.data.get("version_to_run", [])
            prompt_eval_config_ids = request.data.get("prompt_eval_config_ids", [])
            run_index = request.data.get("run_index", None)
            if not prompt_eval_config_ids:
                return self._gm.bad_request(
                    get_error_message("PROMPT_EVAL_CONFIG_IDS_REQUIRED")
                )
            # Check if all versions exist
            # existing_versions = PromptVersion.objects.filter(
            #     original_template=template,
            #     template_version__in=version_to_run
            # ).values_list('template_version', flat=True)

            # # Convert all to strings for comparison
            # existing_eval_configs_str = set(str(eid) for eid in existing_versions)
            # prompt_eval_config_ids_str = set(str(eid) for eid in prompt_eval_config_ids)

            # missing_eval_configs = prompt_eval_config_ids_str - existing_eval_configs_str
            # if missing_eval_configs:
            #     return self._gm.bad_request(f"Following evaluation configs do not exist: {', '.join(missing_eval_configs)}")

            # Check if all PromptEvalConfig IDs exist and belong to this template
            # existing_eval_configs = PromptEvalConfig.objects.filter(
            #     id__in=prompt_eval_config_ids,
            #     prompt_template=template,
            #     deleted=False
            # ).values_list('id', flat=True)

            # missing_eval_configs = set(prompt_eval_config_ids) - set(existing_eval_configs)
            # if missing_eval_configs:
            #     return self._gm.bad_request(f"Following evaluation configs do not exist: {', '.join(missing_eval_configs)}")

            executions = PromptVersion.objects.filter(
                original_template=template,
                original_template__organization=getattr(request, "organization", None)
                or request.user.organization,
                template_version__in=version_to_run,
                deleted=False,
            )

            for execution in executions:  # prompt template versions
                # Initialize evaluation results atomically
                with transaction.atomic():
                    eval_results = execution.evaluation_results or {}
                    variable_names = execution.variable_names or {}
                    max_len = max(
                        (len(values) for values in variable_names.values()), default=1
                    )

                    for prompt_eval_config_id in prompt_eval_config_ids:
                        track_running_eval_count(
                            start=True,
                            prompt_config_eval_id=prompt_eval_config_id,
                            operation="set",
                            num=1 if run_index else max_len,
                        )
                        if (
                            run_index is not None
                            and str(prompt_eval_config_id) in eval_results
                            and len(eval_results[str(prompt_eval_config_id)]["results"])
                            > run_index
                        ):
                            eval_results[str(prompt_eval_config_id)]["results"][
                                run_index
                            ] = {"status": StatusType.RUNNING.value}

                        elif (
                            str(prompt_eval_config_id) in eval_results
                            and len(eval_results[str(prompt_eval_config_id)]["results"])
                            > 0
                        ):
                            results = eval_results[str(prompt_eval_config_id)][
                                "results"
                            ]
                            for result in results:
                                result["status"] = StatusType.RUNNING.value
                            eval_results[str(prompt_eval_config_id)][
                                "results"
                            ] = results

                    execution.evaluation_results = eval_results
                    execution.save(update_fields=["evaluation_results"])

            submit_with_retry(
                _PROMPT_TEMPLATE_EXECUTOR,
                self.run_evals_task,
                template,
                executions,
                prompt_eval_config_ids,
                run_index,
                user_id=str(request.user.id),
            )

            return self._gm.success_response("Evaluation started")

        except Exception as e:
            logger.exception(f"Error starting evaluation job: {str(e)}")
            return self._gm.bad_request(f"Failed to start evaluation job: {str(e)}")

    def run_evaluation(
        self,
        evaluation,
        response,
        messages,
        variable_combination,
        organization_id,
        template=None,
    ):
        try:
            eval_template = None
            try:
                eval_template = EvalTemplate.no_workspace_objects.get(
                    id=evaluation.eval_template.id, deleted=False
                )

            except EvalTemplate.DoesNotExist:
                return None, {
                    "name": "Invalid Evaluation",
                    "data": None,
                    "failure": True,
                    "reason": "Invalid Evaluation ID",
                    "runtime": None,
                    "model": None,
                    "metrics": None,
                    "metadata": None,
                    "output": None,
                }

            futureagi_eval = (
                True
                if eval_template.config.get("eval_type_id") in FUTUREAGI_EVAL_TYPES
                else False
            )
            evaluation_runner = EvaluationRunner(
                eval_template.config.get("eval_type_id"),
                format_output=True,
                futureagi_eval=futureagi_eval,
                source="prompt_template",
                source_id=eval_template.id,
            )
            evaluation_runner.eval_template = eval_template

            # Convert messages and response into chat history format
            chat_history = []
            input_images = []  # Collect image URLs from multi-modal messages
            input_audios = []  # Collect audio URLs from multi-modal messages
            for msg in messages:
                if isinstance(msg["content"], list):
                    # Handle multi-modal content
                    text_content = " ".join(
                        item["text"]
                        for item in msg["content"]
                        if isinstance(item, dict) and "text" in item
                    )
                    chat_history.append({"role": msg["role"], "content": text_content})
                    # Extract media URLs from multi-modal content
                    if msg["role"] == "user":
                        for item in msg["content"]:
                            if isinstance(item, dict):
                                if item.get("type") == "image_url":
                                    image_url = item.get("image_url", {}).get("url", "")
                                    if image_url:
                                        input_images.append(image_url)
                                elif item.get("type") == "audio_url":
                                    audio_url = item.get("audio_url", {}).get("url", "")
                                    if audio_url:
                                        input_audios.append(audio_url)
                else:
                    chat_history.append(msg)

            # Add response as the last message
            chat_history.append({"role": "assistant", "content": response})

            # Get evaluation class and configuration
            from evaluations.engine.registry import get_eval_class

            eval_class = get_eval_class(eval_template.config.get("eval_type_id"))

            data_config = evaluation.eval_template.config
            config = (
                data_config.copy()
                if evaluation.eval_template == OwnerChoices.USER.value
                else data_config.get("config", {}).copy()
            )
            config = evaluation_runner.update_config_list_values(config)
            eval_instance = evaluation_runner._create_eval_instance(
                config=config,
                eval_class=eval_class,
                runtime_config=evaluation.config,
            )
            # Setup evaluation parameters using the helper method
            run_params = self._setup_eval_params(
                chat_history=chat_history,
                mappings=evaluation.mapping,
                variable_combination=variable_combination,
                input_images=input_images,
                input_audios=input_audios,
            )

            data = model_to_dict(evaluation)
            data.update(
                {
                    "description": eval_template.description,
                    "eval_tags": eval_template.eval_tags,
                    "criteria": eval_template.criteria,
                    "multi_choice": eval_template.multi_choice,
                    "choices": eval_template.choices,
                    "config": eval_template.config,
                }
            )
            prompt_queryset = (
                PromptVersion.objects.filter(original_template=template)
                .order_by("-created_at", "-template_version")
                .first()
            )
            source_config = {
                "reference_id": (
                    str(prompt_queryset.id)
                    if prompt_queryset
                    else str(eval_template.id)
                ),
                "is_futureagi_eval": futureagi_eval,
                "source": "prompt_template",
                "prompt_template_id": str(template.id),
                "prompt_version_id": str(prompt_queryset.id),
            }
            source_config.update(
                {
                    "mappings": run_params,
                    "required_keys": eval_template.config.get("required_keys", []),
                }
            )
            org = Organization.objects.get(id=organization_id)
            api_call_log_row = log_and_deduct_cost_for_api_request(
                organization=org,
                api_call_type=APICallTypeChoices.DATASET_EVALUATION.value,
                source="prompt_template",
                source_id=eval_template.id,
                config=source_config,
                workspace=evaluation.prompt_template.workspace,
            )

            if not api_call_log_row:
                raise ValueError(
                    "API call not allowed : Error validating the api call."
                )

            if api_call_log_row.status != APICallStatusChoices.PROCESSING.value:
                raise ValueError("API call not allowed : ", api_call_log_row.status)
            # Run evaluation
            eval_result = eval_instance.run(
                **evaluation_runner.map_fields(
                    list(run_params.keys()), list(run_params.values())
                )
            )

            # Format response
            response = {
                "name": eval_template.name,
                "data": eval_result.eval_results[0].get("data"),
                "failure": eval_result.eval_results[0].get("failure"),
                "reason": eval_result.eval_results[0].get("reason"),
                "runtime": eval_result.eval_results[0].get("runtime"),
                "model": eval_result.eval_results[0].get("model"),
                "metrics": eval_result.eval_results[0].get("metrics"),
                "metadata": eval_result.eval_results[0].get("metadata", {}),
                "output": eval_template.config.get("output"),
            }
            metadata = response.get("metadata") or {}
            if isinstance(metadata, str):
                try:
                    metadata = json.loads(metadata)
                except Exception as e:
                    logger.exception(f"Error in parsing metadata: {str(e)}")
                    metadata = {}

            value = evaluation_runner.format_output(
                result_data=response, eval_template=eval_template
            )

            config_dict = json.loads(api_call_log_row.config)
            config_dict.update(
                {"output": {"output": value, "reason": response["reason"]}}
            )
            api_call_log_row.input_token_count = (
                metadata.get("usage", {}).get("prompt_tokens") or 0
            )
            api_call_log_row.status = APICallStatusChoices.SUCCESS.value
            api_call_log_row.config = json.dumps(config_dict)
            api_call_log_row.save()

            # Dual-write: emit usage event for new billing system
            try:
                try:
                    from ee.usage.schemas.event_types import BillingEventType
                except ImportError:
                    BillingEventType = None
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
                        org_id=str(org.id),
                        event_type=BillingEventType.EVAL_EXPLANATION,
                        properties={
                            "source": "prompt_template",
                            "source_id": str(eval_template.id),
                        },
                    )
                )
            except Exception:
                pass  # Metering failure must not break the action

            return value, response

        except Exception as e:
            try:
                api_call_log_row.status = APICallStatusChoices.ERROR.value
                config_dict = json.loads(api_call_log_row.config)
                config_dict.update({"output": {"output": None, "reason": str(e)}})
                api_call_log_row.config = json.dumps(config_dict)
                api_call_log_row.save()
            except Exception:
                pass
            logger.exception(f"{e} error")
            raise e

    def _setup_eval_params(
        self,
        chat_history,
        mappings,
        variable_combination,
        input_images=None,
        input_audios=None,
    ):
        """Helper method to setup evaluation parameters"""
        try:
            run_params = {}

            # Keys that expect image data from multi-modal messages
            image_keys = {"image", "input_image"}
            # Keys that expect audio data from multi-modal messages
            audio_keys = {"audio", "input_audio", "generated_audio"}

            # Combine all user messages into one content string
            user_content = "\n".join(
                [
                    message["content"]
                    for message in chat_history
                    if message["role"] == "user"
                ]
            )

            # Get the assistant's response (last message)
            assistant_response = chat_history[-1]["content"]

            for key, value in mappings.items():
                if value == "input_prompt":
                    # If the key expects media data and we have it from
                    # multi-modal messages, use the media data instead of text
                    if key in image_keys and input_images:
                        run_params[key] = input_images[0]
                    elif key in audio_keys and input_audios:
                        run_params[key] = input_audios[0]
                    else:
                        run_params[key] = (
                            user_content  # Set both "response" and "query" to user content
                        )
                elif value == "output_prompt":
                    run_params[key] = (
                        assistant_response  # Set "output" to assistant response
                    )
                else:
                    run_params[key] = variable_combination.get(
                        value, ""
                    )  # Otherwise, treat it as a variable name and assign it directly

            return run_params
        except Exception as e:
            logger.exception(f"Error in _setup_eval_params: {e}")
            return {}

    def _update_eval_result_atomically(
        self, execution_id, prompt_eval_config_id, result_data, result_index
    ):
        """
        Atomically update a specific evaluation result to prevent race conditions.

        Args:
            execution_id: ID of the PromptVersion execution
            prompt_eval_config_id: ID of the evaluation config
            result_data: The result data to update
            result_index: Index of the result to update
        """
        with transaction.atomic():
            # Use select_for_update to lock the row and prevent race conditions
            # Use no_workspace_objects manager to avoid the outer join issue with select_for_update
            execution = PromptVersion.no_workspace_objects.select_for_update().get(
                id=execution_id
            )

            # Get current evaluation results
            eval_results = execution.evaluation_results or {}

            # Update the specific result
            if str(prompt_eval_config_id) not in eval_results:
                # Initialize if not exists
                eval_results[str(prompt_eval_config_id)] = {
                    "name": "Unknown",
                    "average_score": None,
                    "results": [],
                }

            # Ensure results list is long enough
            while (
                len(eval_results[str(prompt_eval_config_id)]["results"]) <= result_index
            ):
                eval_results[str(prompt_eval_config_id)]["results"].append(
                    {"status": StatusType.COMPLETED.value}
                )

            # Update the specific result
            eval_results[str(prompt_eval_config_id)]["results"][
                result_index
            ] = result_data

            # Save atomically
            execution.evaluation_results = eval_results
            execution.updated_at = timezone.now()
            execution.save(update_fields=["evaluation_results", "updated_at"])
            track_running_eval_count(
                start=False,
                prompt_config_eval_id=prompt_eval_config_id,
                operation="set",
            )

    def run_eval_id(
        self,
        prompt_eval_config_id,
        response,
        messages_with_replacement,
        variable_combination,
        organization_id,
        template,
        evaluation,
        eval_results,
        execution,
        run_index,
        i,
    ):
        try:
            value, eval_responses = self.run_evaluation(
                evaluation,
                response,
                messages_with_replacement,
                variable_combination,
                organization_id,
                template,
            )

            # Prepare result data
            result_data = {
                "output": eval_responses.get("output", None),
                "value": value,
                "status": StatusType.COMPLETED.value,
                "meta": {
                    "response_time_ms": eval_responses.get("runtime", 0),
                    "reason": eval_responses.get("reason", ""),
                    "failure": False,  # Default to False if no error
                    "token_count": (
                        json.loads(eval_responses.get("metadata"))["usage"][
                            "total_tokens"
                        ]
                        if isinstance(eval_responses.get("metadata"), str)
                        else (eval_responses.get("metadata") or {}).get(
                            "token_count", 0
                        )
                    ),
                },
            }

            # Use atomic update to prevent race conditions
            result_index = run_index if run_index is not None else i
            self._update_eval_result_atomically(
                execution.id, prompt_eval_config_id, result_data, result_index
            )

        except Exception as e:
            logger.exception(f"Error in evaluation: {str(e)}")

            # Prepare error result data
            error_result_data = {
                "output": str(e),
                "value": None,
                "status": StatusType.ERROR.value,
                "meta": {
                    "response_time_ms": 0,
                    "reason": str(e),
                    "failure": True,  # Explicitly set failure to True for errors
                    "token_count": 0,
                },
            }

            # Use atomic update for error result too
            result_index = run_index if run_index is not None else i
            self._update_eval_result_atomically(
                execution.id, prompt_eval_config_id, error_result_data, result_index
            )

    def run_single_eval(
        self,
        execution,
        variable_names,
        i,
        evaluation_configs,
        organization_id,
        template,
        prompt_eval_config_ids,
        run_index,
    ):
        variable_combination = {
            key: values[i] for key, values in variable_names.items()
        }
        try:
            # Replace variables and get the response for this index
            config = execution.prompt_config_snapshot
            messages_with_replacement = replace_variables(
                config.get("messages"),
                variable_combination,
                model_name=config.get("configuration", {}).get("model"),
                template_format=config.get("configuration", {}).get("template_format"),
            )
            response = (
                execution.output[i]
                if execution.output and isinstance(execution.output, list)
                else execution.output
            )
            value_info = {}
            value_info["metadata"] = (
                execution.metadata[i]
                if isinstance(execution.metadata, list)
                else execution.metadata
            )

            # Check if response is complete..
            if value_info.get("metadata"):
                # Process each prompt_eval_config_id
                for prompt_eval_config_id in prompt_eval_config_ids:
                    evaluation = evaluation_configs.get(id=prompt_eval_config_id)
                    self.run_eval_id(
                        prompt_eval_config_id,
                        response,
                        messages_with_replacement,
                        variable_combination,
                        organization_id,
                        template,
                        evaluation,
                        None,
                        execution,
                        run_index,
                        i,
                    )

        except Exception as e:
            logger.exception(f"Error for index {i}: {e}")

    # TODO: should make this celery later
    def run_evals_task(
        self, template, executions, prompt_eval_config_ids, run_index, user_id
    ):
        logger.info(
            f"Starting evaluation task for template {template.id} with executions {executions}"
        )
        try:
            user = User.objects.get(id=user_id)
        except User.DoesNotExist:
            user = None
        try:
            # prompt_configs = template.prompt_config if isinstance(template.prompt_config, list) else [template.prompt_config]
            organization_id = template.organization.id
            evaluation_configs = PromptEvalConfig.objects.filter(
                id__in=prompt_eval_config_ids, prompt_template=template, deleted=False
            )

            def get_max_len(variable_names):
                return (
                    1
                    if run_index is not None
                    else max(
                        (len(v) for v in (variable_names or {}).values()), default=1
                    )
                )

            total_count = sum(
                len(prompt_eval_config_ids) * get_max_len(execution.variable_names)
                for execution in list(executions)
            )
            for prompt_eval_id in prompt_eval_config_ids:
                properties = get_mixpanel_properties(
                    user=user,
                    source=MixpanelSources.WORKBENCH.value,
                    count=total_count,
                    prompt_template=template,
                    eval_id=prompt_eval_id,
                )
                track_mixpanel_event(MixpanelEvents.EVAL_RUN_STARTED.value, properties)

            for execution in executions:  # prompt template versions
                variable_names = execution.variable_names or {}
                max_len = max(
                    (len(values) for values in variable_names.values()), default=1
                )

                # Initialize evaluation results atomically
                with transaction.atomic():
                    # Use no_workspace_objects manager to avoid the outer join issue with select_for_update
                    execution = (
                        PromptVersion.no_workspace_objects.select_for_update().get(
                            id=execution.id
                        )
                    )
                    eval_results = execution.evaluation_results or {}

                    for prompt_eval_config_id in prompt_eval_config_ids:
                        evaluation = evaluation_configs.get(id=prompt_eval_config_id)
                        if run_index is not None:
                            if str(prompt_eval_config_id) not in eval_results:
                                eval_results[str(evaluation.id)] = {
                                    "name": evaluation.name or "Unknown",
                                    "average_score": None,
                                    "results": [
                                        {"status": StatusType.COMPLETED.value}
                                        for _ in range(max_len)
                                    ],
                                }
                            eval_results[str(prompt_eval_config_id)]["results"][
                                run_index
                            ] = {"status": StatusType.RUNNING.value}

                        else:
                            eval_results[str(evaluation.id)] = {
                                "name": evaluation.name or "Unknown",
                                "average_score": None,
                                "results": [
                                    {"status": StatusType.RUNNING.value}
                                    for _ in range(max_len)
                                ],
                            }

                    execution.evaluation_results = eval_results
                    execution.save(update_fields=["evaluation_results"])

            successful_count = 0
            failed = 0
            for execution in executions:
                variable_names = execution.variable_names or {}

                max_len = max(
                    (len(values) for values in variable_names.values()), default=1
                )
                futures = []
                for i in range(max_len):
                    # Map the current index to the variable values
                    if run_index is not None and run_index != i:
                        continue
                    futures.append(
                        submit_with_retry(
                            _PROMPT_TEMPLATE_EXECUTOR,
                            self.run_single_eval,
                            execution,
                            variable_names,
                            i,
                            evaluation_configs,
                            organization_id,
                            template,
                            prompt_eval_config_ids,
                            run_index,
                        )
                    )
                # log error if any of the futures failed
                for future in futures:
                    try:
                        future.result()
                        successful_count += 1
                    except Exception:
                        failed += 1

            for prompt_eval_id in prompt_eval_config_ids:
                properties = get_mixpanel_properties(
                    user=user,
                    source=MixpanelSources.WORKBENCH.value,
                    count=successful_count,
                    failed=failed,
                    prompt_template=template,
                    eval_id=prompt_eval_id,
                )
                track_mixpanel_event(
                    MixpanelEvents.EVAL_RUN_COMPLETED.value, properties
                )

        except Exception as e:
            logger.exception(f"Error in run method: {e}")

    @action(detail=True, methods=["get"])
    def versions(self, request, pk=None):
        try:
            template = self.get_object()
            versions = PromptTemplate.objects.filter(root_template=template).order_by(
                "-created_at"
            )

            page = self.paginate_queryset(versions)
            if page is not None:
                serializer = self.get_serializer(page, many=True)
                return self.paginator.get_paginated_response(serializer.data)

            serializer = self.get_serializer(versions, many=True)
            return self._gm.success_response(serializer.data)
        except Exception as e:
            logger.exception(f"Error in versions method: {e}")
            return self._gm.internal_server_error_response(str(e))

    @action(detail=True, methods=["post"])
    def set_default(self, request, pk=None):
        """
        Set a specific version of a prompt template as default
        """
        template = self.get_object()
        serializer = VersionDefaultSerializer(data=request.data)

        if not serializer.is_valid():
            return self._gm.bad_request(str(serializer.errors))

        validated_data = serializer.validated_data

        try:
            version_obj = PromptVersion.objects.get(
                original_template=template,
                template_version=validated_data.get("version_name"),
            )

            version_obj.is_default = True
            version_obj.updated_at = timezone.now()
            version_obj.save(update_fields=["is_default", "updated_at"])

            return self._gm.success_response(
                PromptHistoryExecutionSerializer(version_obj).data
            )

        except PromptVersion.DoesNotExist:
            return self._gm.bad_request("Prompt Version Not Found")

    @action(detail=False, methods=["post"], url_path="bulk-delete")
    def bulk_delete(self, request):
        """
        Bulk delete prompt templates
        """
        try:
            ids = request.data.get("ids", [])
            if not ids:
                return self._gm.bad_request("No IDs provided for deletion")

            templates = PromptTemplate.objects.filter(
                id__in=ids,
                organization=getattr(request, "organization", None)
                or request.user.organization,
            )
            if not templates.exists():
                return self._gm.bad_request("No valid IDs provided for deletion")

            for template in templates:
                self.perform_destroy(template)

            return self._gm.success_response("Templates deleted successfully")
        except Exception as e:
            logger.exception(f"Error in bulk_delete method: {e}")
            return self._gm.internal_server_error_response(str(e))

    @action(detail=True, methods=["post"])
    def commit(self, request, pk=None):
        try:
            serializer = CommitSerializer(data=request.data)
            if not serializer.is_valid():
                return self._gm.bad_request(parse_serialized_errors(serializer))
            validated_data = serializer.validated_data

            template = self.get_object()
            try:
                version_obj = PromptVersion.objects.get(
                    original_template=template,
                    original_template__organization=getattr(
                        request, "organization", None
                    )
                    or request.user.organization,
                    template_version=validated_data.get("version_name"),
                )
            except PromptVersion.DoesNotExist:
                return self._gm.bad_request("Prompt Version Not Found")

            version_obj.commit_message = validated_data.get("message")
            version_obj.is_draft = validated_data.get("is_draft", False)

            if validated_data.get("set_default"):
                version_obj.is_default = True
            version_obj.updated_at = timezone.now()
            version_obj.save(
                update_fields=["is_default", "commit_message", "updated_at", "is_draft"]
            )

            if request.headers.get("X-Api-Key") is not None:
                properties = get_mixpanel_properties(
                    user=request.user,
                    prompt_template=template,
                    message=validated_data.get("message"),
                )
                track_mixpanel_event(MixpanelEvents.SDK_PROMPT_COMMIT.value, properties)

            return self._gm.success_response(
                f"Commit for {template.name} {version_obj.template_version} has been added"
            )
        except Exception as e:
            logger.exception(f"Error in commit method: {e}")
            return self._gm.internal_server_error_response(
                get_error_message("UNABLE_TO_COMMIT")
            )

    def perform_destroy(self, instance):
        instance.deleted = True
        instance.save()

        # Mark all versions of the template as inactive
        versions = PromptVersion.objects.filter(original_template=instance)
        for version in versions:
            version.deleted = True
            version.save()

    @action(detail=False, methods=["post"], url_path="generate-prompt")
    def generate_prompt(self, request):
        try:
            """
            Generate a new prompt based on a provided statement.

            Args:
                request (Request): The request object containing 'statement' and optionally 'follow_up'.

            Returns:
                Response: A response with the generated prompt.
            """
            statement = request.data.get("statement")
            # Validate the input
            if not statement:
                return self._gm.bad_request(get_error_message("MISSING_STATEMENT"))

            config = {"input_tokens": count_text_tokens(statement)}
            call_log_row = log_and_deduct_cost_for_api_request(
                getattr(request, "organization", None) or request.user.organization,
                APICallTypeChoices.PROMPT_BENCH.value,
                config=config,
                source="run_prompt_gen",
                workspace=request.workspace,
            )
            if (
                call_log_row is None
                or call_log_row.status != APICallStatusChoices.PROCESSING.value
            ):
                return self._gm.bad_request(get_error_message("INSUFFICIENT_CREDITS"))

            # Dual-write: emit usage event for new billing system
            try:
                try:
                    from ee.usage.schemas.event_types import BillingEventType
                except ImportError:
                    BillingEventType = None
                try:
                    from ee.usage.schemas.events import UsageEvent
                except ImportError:
                    UsageEvent = None
                try:
                    from ee.usage.services.emitter import emit
                except ImportError:
                    emit = None

                _org = (
                    getattr(request, "organization", None) or request.user.organization
                )
                emit(
                    UsageEvent(
                        org_id=str(_org.id),
                        event_type=BillingEventType.AI_PROMPT_CREATION,
                        properties={
                            "source": "run_prompt_gen",
                            "source_id": str(call_log_row.log_id),
                        },
                    )
                )
            except Exception:
                pass  # Metering failure must not break the action

            uid = None
            if request.headers.get("X-Api-Key") is not None:
                uid = str(uuid.uuid4())
                properties = get_mixpanel_properties(
                    user=request.user, message=statement, uid=uid
                )
                track_mixpanel_event(
                    MixpanelEvents.SDK_PROMPT_GENERATE.value, properties
                )

            prompt_generator = PromptGenerator()

            generation_payload = {
                "description": statement,
                "organization_id": str(
                    (
                        getattr(request, "organization", None)
                        or request.user.organization
                    ).id
                ),
                "generation_id": f"generate_{uuid.uuid4()}",
                "mixpanel_uid": uid,
                "user_id": str(request.user.id),
            }

            submit_with_retry(
                _PROMPT_TEMPLATE_EXECUTOR,
                prompt_generator.generate_prompt,
                generation_payload,
                call_log_row,
            )
            return self._gm.success_response(
                {"generation_id": generation_payload.get("generation_id")}
            )

        except Exception as e:
            logger.exception(f"Error in generation of prompt: {str(e)}")
            traceback.print_exc()
            return self._gm.bad_request(
                f"Failed to generate prompt: {get_error_message('FAILED_TO_GENERATE_PROMPT')}"
            )

    @action(detail=False, methods=["post"], url_path="improve-prompt")
    def improve_prompt(self, request):
        try:
            from tfc.ee_gating import EEFeature, check_ee_feature

            org = getattr(request, "organization", None) or request.user.organization
            check_ee_feature(EEFeature.OPTIMIZATION, org_id=str(org.id))

            """
            Improve an existing prompt while keeping the original variables intact.

            Args:
                request (Request): The request object containing 'existing_prompt' and improvement requirements.

            Returns:
                Response: A response with the improved prompt.
            """
            existing_prompt = request.data.get("existing_prompt")
            existing_prompt = replace_ids_with_column_name(existing_prompt)
            improvement_requirements = request.data.get("improvement_requirements", "")

            # Validate the input
            if not existing_prompt:
                return self._gm.bad_request(
                    get_error_message("EXISTING_PROMTP_REQUIRED")
                )
            if not improvement_requirements:
                return self._gm.bad_request(
                    get_error_message("MISSING_IMPROVEMENT_REQUIREMENTS")
                )

            config = {
                "input_tokens": count_text_tokens(
                    existing_prompt + improvement_requirements
                )
            }
            call_log_row = log_and_deduct_cost_for_api_request(
                getattr(request, "organization", None) or request.user.organization,
                APICallTypeChoices.PROMPT_BENCH.value,
                config=config,
                source="run_prompt_improve",
                workspace=request.workspace,
            )
            if (
                call_log_row is None
                or call_log_row.status != APICallStatusChoices.PROCESSING.value
            ):
                return self._gm.bad_request(get_error_message("INSUFFICIENT_CREDITS"))

            # Dual-write: emit usage event for new billing system
            try:
                try:
                    from ee.usage.schemas.event_types import BillingEventType
                except ImportError:
                    BillingEventType = None
                try:
                    from ee.usage.schemas.events import UsageEvent
                except ImportError:
                    UsageEvent = None
                try:
                    from ee.usage.services.emitter import emit
                except ImportError:
                    emit = None

                _org = (
                    getattr(request, "organization", None) or request.user.organization
                )
                emit(
                    UsageEvent(
                        org_id=str(_org.id),
                        event_type=BillingEventType.AI_PROMPT_IMPROVEMENT,
                        properties={
                            "source": "run_prompt_improve",
                            "source_id": str(call_log_row.log_id),
                        },
                    )
                )
            except Exception:
                pass  # Metering failure must not break the action

            prompt_improvement_agent = PromptGenerator()
            uid = None
            if request.headers.get("X-Api-Key") is not None:
                uid = str(uuid.uuid4())
                properties = get_mixpanel_properties(
                    user=request.user, uid=uid, message=improvement_requirements
                )
                track_mixpanel_event(
                    MixpanelEvents.SDK_PROMPT_IMPROVE.value, properties
                )
            # Create the payload for improving the prompt
            payload = {
                "original_prompt": existing_prompt,
                "improvement_suggestions": improvement_requirements,
                "improve_id": f"improve_{uuid.uuid4()}",
                "organization_id": str(
                    (
                        getattr(request, "organization", None)
                        or request.user.organization
                    ).id
                ),
                "user_id": str(request.user.id),
                "mixpanel_uid": uid,
            }

            submit_with_retry(
                _PROMPT_TEMPLATE_EXECUTOR,
                prompt_improvement_agent.improve_prompt,
                payload,
                call_log_row,
            )

            return self._gm.success_response({"improve_id": payload.get("improve_id")})

        except APIException:
            raise
        except Exception as e:
            logger.exception(f"Error in improve the prompt: {str(e)}")
            traceback.print_exc()
            return self._gm.bad_request(
                {
                    "error": f"Failed to improve prompt: {get_error_message('FAILED_TO_IMPROVE_PROMPT')}"
                }
            )

    @action(detail=True, methods=["get"], url_path="get-sdk-code/(?P<language>[^/.]+)?")
    def get_sdk_code(self, request, pk=None, language=None):
        """
        Get the prompt code in the requested format. If no format is specified, returns all formats.
        Supported languages: python, typescript, curl, langchain, nodejs, go

        Args:
            language (str, optional): Specific language code to return. Defaults to None (returns all).
        """
        try:
            template = self.get_object()
            api_url = (
                f"{BASE_URL}/model-hub/prompt-templates/{template.id}/run_template/"
            )
            latest_version = (
                PromptVersion.objects.filter(
                    original_template=template,
                    original_template__organization=getattr(
                        request, "organization", None
                    )
                    or request.user.organization,
                )
                .order_by("-is_default", "-created_at")
                .first()
            )

            # Get the template configuration
            prompt_config = latest_version.prompt_config_snapshot or {}
            variable_names = template.variable_names or {}
            evaluation_configs = template.evaluation_configs or {}
            is_run = True
            name = template.name

            # Get model configuration from prompt_config
            model = (
                prompt_config[0]["configuration"].get("model", "gpt-4")
                if prompt_config
                else "gpt-4"
            )
            temperature = (
                prompt_config[0]["configuration"].get("temperature", 0.7)
                if prompt_config
                else 0.7
            )

            # Define available language options and their corresponding code templates
            code_templates = {
                "python": PROMPT_PYTHON_CODE.format(
                    api_url=api_url,
                    name=name,
                    prompt_config=prompt_config,
                    variable_names=variable_names,
                    evaluation_configs=evaluation_configs,
                    is_run=is_run,
                ),
                "typescript": PROMPT_TYPESCRIPT_CODE.format(
                    api_url=api_url,
                    name=name,
                    prompt_config=prompt_config,
                    variable_names=variable_names,
                    evaluation_configs=evaluation_configs,
                    is_run=is_run,
                ),
                "curl": PROMPT_CURL_CODE.format(
                    api_url=api_url,
                    name=name,
                    prompt_config=prompt_config,
                    variable_names=variable_names,
                    evaluation_configs=evaluation_configs,
                    is_run=is_run,
                ),
                "langchain": PROMPT_LANGCHAIN_CODE.format(
                    model=model,
                    temperature=temperature,
                    name=name,
                    prompt_config=prompt_config,
                    variable_names=variable_names,
                    evaluation_configs=evaluation_configs,
                    is_run=is_run,
                ),
                "nodejs": PROMPT_NODEJS_CODE.format(
                    api_url=api_url,
                    name=name,
                    prompt_config=prompt_config,
                    variable_names=variable_names,
                    evaluation_configs=evaluation_configs,
                    is_run=is_run,
                ),
                "go": PROMPT_GO_CODE.format(
                    api_url=api_url,
                    name=name,
                    prompt_config=prompt_config,
                    variable_names=variable_names,
                    evaluation_configs=evaluation_configs,
                    is_run=is_run,
                ),
            }

            # If a specific language is requested
            if language:
                language = language.lower()
                if language in code_templates:
                    return self._gm.success_response(
                        {language: code_templates[language]}
                    )
                else:
                    return self._gm.bad_request(
                        {
                            "error": f"Unsupported language: {language}. Available options are: {', '.join(code_templates.keys())}"
                        }
                    )

            # If no specific language is requested, return all
            return self._gm.success_response(code_templates)

        except Exception as e:
            logger.exception(f"Error in generating SDK: {str(e)}")
            traceback.print_exc()
            return self._gm.bad_request(
                {
                    "error": f"Failed to generate SDK code: {get_error_message('FAILED_TO_GENERATE_SDK_CODE')}"
                }
            )

    @action(detail=False, methods=["post"], url_path="analyze-prompt")
    def analyze_prompt(self, request):
        try:
            """
            Analyze a prompt and provide improvement suggestions.

            Args:
                request (Request): The request object containing 'prompt' and 'explanation'.

            Returns:
                Response: A response with improvement suggestions.
            """
            prompt = request.data.get("prompt")
            prompt = replace_ids_with_column_name(prompt)
            explanation = request.data.get("explanation")
            example = request.data.get("example", {})

            # Validate the input
            if not prompt:
                return self._gm.bad_request(get_error_message("MISSING_PROMPT"))

            if not explanation:
                return self._gm.bad_request(get_error_message("MISSING_EXPLANATION"))

            payload = {"prompt": prompt, "example": example, "feedback": explanation}

            prompt_agent = PromptSuggestionGenerator()
            optimized_suggestion = prompt_agent._prompt_suggestion(payload=payload)

            # Return the analysis results
            return self._gm.success_response(
                {
                    "improvement_suggestions": optimized_suggestion,
                }
            )

        except Exception as e:
            logger.exception(f"Error in analyzing the prompt: {str(e)}")
            traceback.print_exc()
        return self._gm.bad_request(
            {
                "error": f"Failed to analyze prompt: {get_error_message('FAILED_TO_ANALYSED_PROMPT')}"
            }
        )

    @action(detail=False, methods=["post"], url_path="generate-variables")
    def generate_variables(self, request):
        """
        Generate synthetic data for prompt variables using the SyntheticDataAgent.

        Expected payload:
        {
            "prompt_name": "string",
            "prompt_instructions": "list/array" ,
            "variable_names": ["string"],
            "variable_count": "int",
            "generation_type": "prompt"
        }
        """
        try:
            # Validate required fields
            prompt_name = request.data.get("prompt_name")
            variable_names = request.data.get("variable_names")
            variable_count = request.data.get("variable_count", 1)

            if not prompt_name or not variable_names:
                return self._gm.bad_request(
                    get_error_message("MISSING_PROMPT_NAME_AND_VARIABLE_NAME")
                )

            # Create payload for the agent
            payload = {
                "prompt_name": prompt_name,
                "variable_names": variable_names,
                "batch_size": variable_count,
                "generation_type": "prompt",
            }

            # Add optional prompt_instructions if provided
            if prompt_instructions := request.data.get("prompt_instructions"):
                payload["prompt_instructions"] = str(prompt_instructions)

            # Initialize agent and generate data
            agent = SyntheticDataAgent()
            result = agent.generate_and_validate(payload)

            # Convert DataFrame to dictionary of lists
            variables_dict = {}
            for column in result.columns:
                variables_dict[column] = result[column].tolist()

            return self._gm.success_response({"variables": variables_dict})

        except Exception as e:
            logger.exception(f"Error in generating variable values: {str(e)}")
            traceback.print_exc()
            return self._gm.bad_request(f"Failed to generate variable values: {str(e)}")

    @action(detail=True, methods=["post"], url_path="save-name")
    def save_name(self, request, pk=None):
        """
        Save/update the name for a template.

        Args:
            request (Request): The request object containing 'name' in the body
            pk (str): The template ID

        Returns:
            Response: Success/error response with updated template data
        """
        try:
            template = self.get_object()
            name = request.data.get("name")

            if not name:
                return self._gm.bad_request(get_error_message("MISSING_NAME"))

            # Check if name already exists for another template
            if (
                PromptTemplate.objects.filter(
                    name=name,
                    organization=getattr(self.request, "organization", None)
                    or self.request.user.organization,
                    deleted=False,
                )
                .exclude(id=template.id)
                .exists()
            ):
                return self._gm.bad_request(get_error_message("TEMPLATE_ALREADY_EXIST"))

            template.name = name
            template.save()

            return self._gm.success_response(PromptTemplateSerializer(template).data)

        except Exception as e:
            logger.exception(f"Error in saving template name: {str(e)}")
            return self._gm.bad_request(f"Failed to save template name: {str(e)}")

    @action(detail=True, methods=["post"], url_path="save-prompt-folder")
    def save_prompt_folder(self, request, pk=None):
        try:
            template = self.get_object()
            prompt_folder_id = request.data.get("prompt_folder_id")

            if not prompt_folder_id:
                return self._gm.bad_request("Prompt folder information not sent")

            # Get the prompt folder object
            try:
                prompt_folder = PromptFolder.no_workspace_objects.get(
                    id=prompt_folder_id,
                    organization=getattr(self.request, "organization", None)
                    or self.request.user.organization,
                    deleted=False,
                )
            except PromptFolder.DoesNotExist:
                return self._gm.bad_request("Prompt folder not found")

            template.prompt_folder = prompt_folder
            template.save()

            return self._gm.success_response(PromptTemplateSerializer(template).data)

        except Exception as e:
            logger.exception(f"Error in saving prompt folder: {str(e)}")
            return self._gm.bad_request(f"Failed to save prompt folder: {str(e)}")


class ColumnValuesAPIView(APIView):
    _gm = GeneralMethods()
    permission_classes = [IsAuthenticated]

    def post(self, request, *args, **kwargs):
        try:
            dataset_id = request.data.get("dataset_id")
            column_placeholders = request.data.get("column_placeholders")

            # Ensure dataset_id and column_placeholders are provided
            if not dataset_id or not column_placeholders:
                return self._gm.bad_request(
                    get_error_message("MISSING_REQUIRED_FIELDS")
                )

            organization = (
                getattr(request, "organization", None) or request.user.organization
            )
            try:
                dataset = Dataset.objects.get(id=dataset_id)
            except Dataset.DoesNotExist:
                return self._gm.not_found("Dataset not found")

            if dataset.organization_id != organization.id:
                return self._gm.not_found("Dataset not found")

            # Initialize the response dictionary to hold column values
            column_values = {}

            # Iterate over the column placeholders and fetch corresponding column values
            for placeholder_key, column_id in column_placeholders.items():
                try:
                    # Fetch the column based on column_id
                    column = Column.objects.get(id=column_id, dataset=dataset)
                except Column.DoesNotExist:
                    return self._gm.bad_request(
                        {
                            "error": f"{get_error_message('COLUMN_NOT_FOUND')} {column_id}"
                        }
                    )

                # Fetch the rows for the dataset
                rows = Row.objects.filter(dataset=dataset).order_by("order")[:10]

                # Get column values for the rows
                values = self._get_column_values_for_rows(dataset, column, rows)

                # Add column_id and column_name to the response
                column_values[placeholder_key] = {
                    "column_id": column.id,  # Add column id
                    "column_name": column.name,  # Add column name
                    "values": values,  # Add the column values
                }

            return self._gm.success_response({"result": column_values})

        except Exception as e:
            logger.exception(f"Error in fetching column values: {str(e)}")
            return self._gm.bad_request(
                get_error_message("FAILED_TO_GET_COLUMN_VALUES")
            )

    def _get_column_values_for_rows(self, dataset, column, rows):
        """
        Helper method to fetch the values of a given column for all rows in the dataset.
        If no value is found, it appends "unavailable".
        """
        column_values = []

        for row in rows:
            try:
                cell = Cell.objects.get(dataset=dataset, column=column, row=row)
                if cell.value:
                    column_values.append(cell.value)
                else:
                    column_values.append("")
            except Cell.DoesNotExist:
                column_values.append("")

        return column_values


class PromptExecutionViewSet(BaseModelViewSetMixin, viewsets.ReadOnlyModelViewSet):
    queryset = PromptTemplate.objects.all()
    serializer_class = PromptExecutionSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = ExtendedPageNumberPagination
    filter_backends = [
        DjangoFilterBackend,
        filters.SearchFilter,
        filters.OrderingFilter,
    ]
    filterset_class = PromptExecutionFilter
    search_fields = ["name"]
    ordering_fields = ["created_at", "name", "updated_at"]
    _gm = GeneralMethods()

    def get_queryset(self):
        latest_versions = (
            PromptVersion.objects.filter(deleted=False)
            .annotate(
                version_number=Cast(Substr("template_version", 2), IntegerField())
            )
            .order_by("-is_default", "-version_number")
        )

        # Get base queryset with automatic filtering from mixin
        queryset = super().get_queryset()

        send_all = self.request.query_params.get("send_all", "").lower() == "true"
        if send_all:
            queryset = PromptTemplate.objects.filter(
                organization=getattr(self.request, "organization", None)
                or self.request.user.organization,
                deleted=False,
            ).select_related("prompt_folder", "created_by")

        # Apply prompt_folder filtering if provided via query parameter
        prompt_folder_id = self.request.query_params.get("prompt_folder")
        if prompt_folder_id:
            queryset = queryset.filter(prompt_folder_id=prompt_folder_id)

        # Apply additional ViewSet-specific optimizations
        queryset = queryset.select_related(
            "organization", "prompt_folder", "created_by"
        ).prefetch_related(
            Prefetch(
                "collaborators", queryset=User.objects.only("id", "email", "name")
            ),
            Prefetch(
                "all_executions",
                queryset=latest_versions,
                to_attr="prefetched_versions",
            ),
        )

        # Filter templates by modality (configuration.model_detail.type in prompt_config_snapshot)
        modality = self.request.query_params.getlist("modality")
        if modality and ModalityType.ALL not in modality:
            version_filter = Q(
                prompt_config_snapshot__configuration__model_detail__type__in=modality,
            )
            # Records without a type field default to 'chat'
            if ModalityType.CHAT in modality:
                version_filter |= Q(
                    prompt_config_snapshot__configuration__model_detail__type__isnull=True,
                )
            template_ids = (
                PromptVersion.objects.filter(deleted=False)
                .filter(version_filter)
                .values_list("original_template_id", flat=True)
            )
            queryset = queryset.filter(id__in=template_ids)

        return queryset

    def list(self, request, *args, **kwargs):
        try:
            # Dynamically override pagination page size if provided
            page_size = request.query_params.get("page_size")
            send_all = request.query_params.get("send_all", "").lower() == "true"

            if page_size:
                try:
                    self.paginator.page_size = int(page_size)
                except (ValueError, TypeError):
                    logger.warning(f"Invalid page_size parameter: {page_size}")
                    pass

            if send_all:
                # Get combined results of prompt templates and folders
                combined_data, prompt_count = self._get_combined_templates_and_folders()

                # Handle pagination for combined data
                paginator = self.pagination_class()
                if page_size:
                    try:
                        paginator.page_size = int(page_size)
                    except (ValueError, TypeError):
                        pass

                page = paginator.paginate_queryset(combined_data, request)
                if page is not None:
                    # Override the count in paginated response to show only prompt count
                    response = paginator.get_paginated_response(page)
                    if (
                        hasattr(response, "data")
                        and isinstance(response.data, dict)
                        and "count" in response.data
                    ):
                        response.data["count"] = prompt_count
                    return response

                return self._gm.success_response(combined_data)
            else:
                # Original behavior for prompt templates only
                queryset = self.filter_queryset(self.get_queryset())

                # Pin specific IDs to top of results
                pinned_ids_param = request.query_params.get("pinned_ids", "")
                pinned_ids = (
                    [i.strip() for i in pinned_ids_param.split(",") if i.strip()]
                    if pinned_ids_param
                    else []
                )
                if pinned_ids:
                    queryset = queryset.annotate(
                        is_pinned=Case(
                            When(id__in=pinned_ids, then=Value(0)),
                            default=Value(1),
                            output_field=IntegerField(),
                        )
                    ).order_by("is_pinned", "-updated_at")

                page = self.paginate_queryset(queryset)

                serializer = self.get_serializer(
                    page if page is not None else queryset, many=True
                )

                # Add prompt folder name to each template
                serialized_data = serializer.data

                if page is not None:
                    return self.get_paginated_response(serialized_data)

                return self._gm.success_response(serialized_data)

        except Exception as e:
            logger.exception(f"Error in list view: {str(e)}")
            return self._gm.bad_request(f"Failed to retrieve templates: {str(e)}")

    def _get_combined_templates_and_folders(self):
        """
        Get combined list of prompt templates and folders, sorted by updated_at.
        Adds a 'type' field to distinguish between PROMPT and FOLDER objects.
        Returns tuple: (combined_data, prompt_count)
        """
        # Get prompt templates
        templates_queryset = self.filter_queryset(self.get_queryset())
        templates_serializer = self.get_serializer(templates_queryset, many=True)
        templates_data = templates_serializer.data

        # Count of prompt templates (for response count)
        prompt_count = len(templates_data)

        # Get root folders (no parent) and sample folders
        folders_queryset = PromptFolder.no_workspace_objects.filter(
            models.Q(workspace=self.request.workspace) | models.Q(is_sample=True),
            deleted=False,
        )

        name_fiilter = self.request.query_params.get("name")
        if name_fiilter:
            folders_queryset = folders_queryset.filter(name__icontains=name_fiilter)

        folders_serializer = PromptFolderSerializer(folders_queryset, many=True)
        folders_data = folders_serializer.data
        # Add type field to folders
        for folder in folders_data:
            folder["type"] = "FOLDER"

        # Add type field to templates
        for template in templates_data:
            template["type"] = "PROMPT"

        # Combine and sort by updated_at (most recent first)
        combined_data = templates_data + folders_data

        sort_by = self.request.query_params.get("sort_by", "updated_at")
        sort_direction = self.request.query_params.get("sort_order", "desc")

        # Define case-insensitive sorting key function
        def get_sort_key(item):
            value = item[sort_by]
            # For string fields, use lowercase for case-insensitive sorting
            if isinstance(value, str):
                return value.lower()
            return value

        if sort_direction.lower() == "desc":
            combined_data.sort(key=get_sort_key, reverse=True)
        else:
            combined_data.sort(key=get_sort_key, reverse=False)

        # Pin specific IDs to top of results
        pinned_ids_param = self.request.query_params.get("pinned_ids", "")
        pinned_ids = (
            [i.strip() for i in pinned_ids_param.split(",") if i.strip()]
            if pinned_ids_param
            else []
        )
        if pinned_ids:
            pinned_set = set(pinned_ids)
            pinned = [
                item for item in combined_data if str(item.get("id")) in pinned_set
            ]
            rest = [
                item for item in combined_data if str(item.get("id")) not in pinned_set
            ]
            combined_data = pinned + rest

        return combined_data, prompt_count


class PromptHistoryExecutionViewSet(
    BaseModelViewSetMixin, viewsets.ReadOnlyModelViewSet
):
    queryset = PromptVersion.objects.all()
    serializer_class = PromptHistoryExecutionSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = ExtendedPageNumberPagination
    filter_backends = [
        DjangoFilterBackend,
        filters.SearchFilter,
        filters.OrderingFilter,
    ]
    filterset_class = PromptHistoryExecutionFilter
    search_fields = ["template_version"]
    ordering_fields = ["created_at"]
    _gm = GeneralMethods()

    def get_queryset(self):
        # Get base queryset with automatic filtering from mixin
        queryset = (
            super()
            .get_queryset()
            .select_related("original_template", "original_template__organization")
            .filter(
                original_template__organization=getattr(
                    self.request, "organization", None
                )
                or self.request.user.organization,
                original_template__deleted=False,
            )
            .order_by("-is_default", "-created_at")
        )

        # Get template_id from query params
        template_id = self.request.query_params.get("template_id")
        is_commit = self.request.query_params.get("is_commit", "").lower() == "true"

        if template_id:
            queryset = queryset.filter(
                original_template_id=template_id, original_template__deleted=False
            )

        if is_commit:
            queryset = queryset.filter(commit_message__isnull=False).exclude(
                commit_message=""
            )

        # Filter versions by modality (configuration.model_detail.type)
        modality = self.request.query_params.getlist("modality")
        if modality and ModalityType.ALL not in modality:
            version_filter = Q(
                prompt_config_snapshot__configuration__model_detail__type__in=modality,
            )
            # Records without a type field default to 'chat'
            if ModalityType.CHAT in modality:
                version_filter |= Q(
                    prompt_config_snapshot__configuration__model_detail__type__isnull=True,
                )
            queryset = queryset.filter(version_filter)

        return queryset

    @action(
        detail=False,
        methods=["get"],
        url_path="execution-details/(?P<execution_id>[^/.]+)",
    )
    def get_execution_details(self, request, execution_id=None):
        """
        Get detailed information about a specific PromptVersion
        """
        try:
            execution = get_object_or_404(
                PromptVersion.objects.select_related(
                    "original_template", "original_template__organization"
                ).filter(
                    original_template__organization=getattr(
                        self.request, "organization", None
                    )
                    or self.request.user.organization,
                    original_template__deleted=False,
                ),
                id=execution_id,
            )

            serializer = PromptHistoryExecutionSerializer(execution)
            return self._gm.success_response(serializer.data)

        except Exception as e:
            logger.exception(f"Error in fetching prompt execution details: {str(e)}")
            return self._gm.bad_request(
                {
                    "error": f"Failed to fetch execution details: {get_error_message('FAILED_TO_FETCH_EXECUTION_DATA')}"
                }
            )

    def list(self, request, *args, **kwargs):
        try:
            queryset = self.filter_queryset(self.get_queryset())
            template_id = request.query_params.get("template_id")

            if (
                template_id
                and not PromptTemplate.objects.filter(
                    id=template_id,
                    organization=getattr(request, "organization", None)
                    or request.user.organization,
                    deleted=False,
                ).exists()
            ):
                return self._gm.bad_request(get_error_message("TEMPLATE_NOT_EXISTS"))

            has_default = queryset.filter(is_default=True).exists()

            # Paginate if applicable
            page = self.paginate_queryset(queryset)
            result_set = page if page is not None else queryset
            serializer = self.get_serializer(result_set, many=True)
            data = list(serializer.data)

            for i, item in enumerate(data):
                item["created_by"] = (
                    result_set[i].original_template.created_by.name
                    if result_set[i].original_template.created_by
                    else result_set[i].original_template.organization.name
                )

            if not has_default and data:
                # Find the latest non-draft version
                non_draft_items = [
                    i for i, item in enumerate(data) if not item.get("is_draft", False)
                ]
                if non_draft_items:
                    # Get the first non-draft item (which should be the latest due to ordering)
                    latest_non_draft_index = non_draft_items[0]
                    # Move it to the top if it's not already there
                    if latest_non_draft_index != 0:
                        data.insert(0, data.pop(latest_non_draft_index))
                    # Mark it as default
                    data[0]["is_default"] = True

            if page is not None:
                return self.get_paginated_response(data)

            return self._gm.success_response(data)
        except Exception as e:
            logger.exception(f"Error in list view: {str(e)}")
            return self._gm.bad_request(
                get_error_message("UNABLE_TO_FETCH_TEMPLATE_HISTORY")
            )


@temporal_activity(time_limit=3600, queue="default")
def run_template_task(
    template_id,
    evaluation_configs,
    organization_id,
    is_run,
    version_to_run,
    run_index=None,
):
    """
    Celery task to run the template asynchronously
    """
    try:
        template = PromptTemplate.objects.get(id=template_id)
        view = PromptTemplateViewSet()
        template.status = StatusType.RUNNING.value
        template.save()
        result = view.run(
            template,
            organization_id,
            version_to_run,
            is_run,
            is_sdk=False,
            run_index=run_index,
        )

        # Update template version

        template.status = StatusType.COMPLETED.value
        template.save()

        return result
    except Exception as e:
        logger.exception(f"error {e}")
        template.status = StatusType.FAILED.value

        template.error_message = str(e)
        template.save()
        raise e
