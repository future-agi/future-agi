import json
import re

import django_filters
import structlog
import yaml
from django.db.models import Q
from rest_framework import serializers

from agentic_eval.core_evals.run_prompt.litellm_models import LiteLLMModelManager
from model_hub.models.choices import ProviderLogoUrls
from model_hub.models.prompt_folders import PromptFolder
from model_hub.models.run_prompt import (
    PromptTemplate,
    PromptVersion,
    SchemaTypeChoices,
    UserResponseSchema,
)
from model_hub.utils.utils import get_model_mode
from model_hub.utils.workspace_scope import (
    request_organization,
    request_workspace_filter,
)

logger = structlog.get_logger(__name__)


class VersionDefaultSerializer(serializers.Serializer):
    version_name = serializers.CharField(
        required=True,
        help_text=(
            "Version label to make the template's default, e.g. 'v2' "
            "(format v<number>; see list_prompt_versions)."
        ),
    )

    def validate(self, data):
        version = data.get("version_name")
        pattern = r"^v\d+$"  # Match v1, v2, v3, etc.
        if not re.match(pattern, version):
            raise serializers.ValidationError(
                {"version": "Version must be in the format 'v{version_num}'."}
            )

        return data


class UserResponseSchemaSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserResponseSchema
        fields = ["id", "name", "description", "schema", "organization", "schema_type"]
        read_only_fields = ["id", "organization"]

    def _request_organization(self):
        request = self.context.get("request")
        if request is None:
            return None
        return getattr(request, "organization", None) or getattr(
            request.user, "organization", None
        )

    def _request_workspace(self):
        request = self.context.get("request")
        if request is None:
            return None
        return getattr(request, "workspace", None)

    def _workspace_scope(self, workspace, organization):
        if workspace is None:
            return Q(workspace__isnull=True)

        scope = Q(workspace=workspace)
        if getattr(workspace, "is_default", False):
            scope |= Q(workspace__isnull=True)
            if organization is not None:
                scope |= Q(
                    workspace__is_default=True,
                    workspace__organization=organization,
                )
        return scope

    def _normalize_schema(self, schema, schema_type):
        if schema_type == SchemaTypeChoices.YAML.value:
            try:
                return yaml.safe_load(schema)
            except Exception:
                raise serializers.ValidationError("Invalid Yaml Uploaded")  # noqa: B904

        if schema_type == SchemaTypeChoices.JSON.value:
            try:
                if isinstance(schema, str):
                    schema = json.loads(schema)
                    if isinstance(schema, list):
                        raise serializers.ValidationError("Invalid Json")
                    return schema
                elif isinstance(schema, dict):
                    return schema
                else:
                    raise serializers.ValidationError("Invalid JSON")
            except Exception as e:
                logger.exception(f"Error: {e}")
                raise serializers.ValidationError("Invalid Json")  # noqa: B904

        raise serializers.ValidationError("Invalid schema type")

    def validate(self, attrs):
        organization = self._request_organization()
        workspace = self._request_workspace()

        schema_type = attrs.get(
            "schema_type",
            getattr(self.instance, "schema_type", None) or SchemaTypeChoices.JSON.value,
        )
        attrs["schema_type"] = schema_type

        if "schema" in attrs:
            attrs["schema"] = self._normalize_schema(attrs["schema"], schema_type)

        name = attrs.get("name", getattr(self.instance, "name", None))
        if organization is not None and name:
            duplicate_qs = UserResponseSchema.no_workspace_objects.filter(
                self._workspace_scope(workspace, organization),
                organization=organization,
                name__iexact=name,
            )
            if self.instance is not None:
                duplicate_qs = duplicate_qs.exclude(id=self.instance.id)

            if duplicate_qs.exists():
                raise serializers.ValidationError(
                    {"name": "A response schema with this name already exists."}
                )

        return attrs

    def create(self, validated_data):
        organization = self._request_organization()
        workspace = self._request_workspace()
        if organization is not None:
            validated_data["organization"] = organization
        if workspace is not None:
            validated_data["workspace"] = workspace
        return super().create(validated_data)

    def update(self, instance, validated_data):
        organization = self._request_organization()
        workspace = self._request_workspace()
        if organization is not None:
            validated_data["organization"] = organization
        if workspace is not None:
            validated_data["workspace"] = workspace
        return super().update(instance, validated_data)


class CommitSerializer(serializers.Serializer):
    message = serializers.CharField(
        required=True,
        allow_blank=True,
        help_text="Commit message describing what changed in this version.",
    )
    is_draft = serializers.BooleanField(
        required=False,
        default=False,
        help_text="Keep the version a draft after saving (default false = commit).",
    )
    set_default = serializers.BooleanField(
        required=False,
        default=False,
        help_text="Also make this version the template's default.",
    )
    version_name = serializers.CharField(
        required=True,
        help_text=(
            "Version label to commit, e.g. 'v2' (format v<number>; see "
            "list_prompt_versions)."
        ),
    )

    def validate(self, data):
        set_default = data.get("set_default", False)
        version = data.get("version_name")

        # Make version required when set_default is True
        if set_default and not version:
            raise serializers.ValidationError(
                {"version": "Version is required when set_default is True"}
            )

        # Validate version format if provided
        if version is not None:
            pattern = r"^v\d+$"  # Match v1, v2, v3, etc.
            if not re.match(pattern, version):
                raise serializers.ValidationError(
                    {"version": "Version must be in the format 'v{version_num}'."}
                )

        return data


class DraftSerializer(serializers.Serializer):
    prompt_config = serializers.ListField(
        help_text=(
            "Prompt configuration array (first item used): [{'messages': "
            "[{'role': 'system'|'user'|'assistant', 'content': [{'text': "
            "'...', 'type': 'text'}]}], 'configuration': {'model': ..., "
            "'temperature': ..., 'max_tokens': ..., 'response_format': "
            "'text'}, 'placeholders': []}]."
        )
    )
    variable_names = serializers.DictField(
        help_text=(
            "Sample values for {{variable}} placeholders: "
            "{'var': ['value1', ...]}. Pass {} when the prompt has none."
        )
    )
    evaluation_configs = serializers.ListField(
        help_text="Evaluation configs to store on the draft (often [])."
    )
    metadata = serializers.JSONField(
        required=False,
        default=dict,
        help_text="Optional metadata object stored on the version.",
    )


class MultipleDraftSerializer(serializers.Serializer):
    new_prompts = serializers.ListField(
        child=DraftSerializer(),
        required=True,
        help_text=(
            "Draft versions to create (auto-numbered v2, v3, ...). Each "
            "item: {'prompt_config': [...], 'variable_names': {...}, "
            "'evaluation_configs': []}."
        ),
    )


class UploadFileSerializer(serializers.Serializer):
    files = serializers.ListField(child=serializers.FileField(), required=False)
    links = serializers.ListField(child=serializers.URLField(), required=False)
    type = serializers.ChoiceField(
        choices=["image", "audio", "pdf", "text"], required=True
    )

    def validate(self, data):
        files = data.get("files")
        links = data.get("links")

        if not files and not links:
            raise serializers.ValidationError(
                "Either 'files' or 'links' must be provided."
            )
        if files and links:
            raise serializers.ValidationError(
                "Provide only one of 'files' or 'links', not both."
            )
        return data


class CompareVersionsSerializer(serializers.Serializer):
    versions = serializers.ListField(
        child=serializers.CharField(),
        required=True,
        help_text=(
            "Version labels to compare side by side, e.g. ['v1', 'v2'] "
            "(max 3; see list_prompt_versions)."
        ),
    )
    is_run = serializers.CharField(
        required=False,
        help_text="Set 'true' to also run the compared versions' prompts.",
    )

    def validate(self, data):
        versions = data.get("versions")
        if len(versions) > 3:
            raise serializers.ValidationError("You can only compare upto 3 versions")
        return data


class PromptTemplateListRequestSerializer(serializers.Serializer):
    """Query parameters for listing prompt templates in the workspace.

    Returns paginated prompt template records (id, name, folder, modality,
    updated_at). Use this for any "list prompt templates", "show my
    prompts", "find prompt named X" query. Filter by search (matches name),
    modality (chat/completion/image/etc.), or page through results.
    """

    search = serializers.CharField(
        required=False,
        allow_blank=True,
        help_text=(
            "Filter by template name (case-insensitive substring match). "
            "Example: 'summari' matches 'summarization-v3'."
        ),
    )
    page = serializers.IntegerField(
        min_value=1,
        default=1,
        help_text="Page number, 1-indexed. Default 1.",
    )
    page_size = serializers.IntegerField(
        min_value=1,
        max_value=100,
        default=20,
        help_text="Number of templates per page. Range 1-100. Default 20.",
    )
    ordering = serializers.CharField(
        required=False,
        allow_blank=True,
        help_text=(
            "Sort order. One of: 'name', '-name', 'created_at', "
            "'-created_at'. Prefix with '-' for descending."
        ),
    )
    modality = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        help_text=(
            "Filter by model modality. List of strings, e.g. ['chat'], "
            "['completion'], ['image']. Omit to include all modalities."
        ),
    )


class PromptTemplateSerializer(serializers.ModelSerializer):
    """A prompt template is a reusable, versioned LLM prompt definition.

    Templates have versions (drafts, defaults, named labels) that capture
    the actual messages, model config, and execution settings. Use prompt
    folders to organise templates into groups.
    """

    id = serializers.UUIDField(
        read_only=True,
        help_text=(
            "Unique prompt template identifier. UUID v4 format. "
            "Example: '550e8400-e29b-41d4-a716-446655440000'. "
            "**How to get it:** call `list_prompt_templates` to discover "
            "template IDs (optionally filter by 'search' query param to find "
            "by name)."
        ),
    )
    name = serializers.CharField(
        max_length=255,
        help_text=(
            "Human-readable prompt template name. Must be unique within the "
            "workspace. Use kebab-case or descriptive phrases. "
            "Examples: 'customer-support-greeting', 'summarization-v3', "
            "'rag-final-answer'."
        ),
    )
    description = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
        help_text=(
            "Optional free-form description of what the prompt is for. "
            "Example: 'Generates polite customer support replies given a "
            "ticket summary and tone tag.'"
        ),
    )
    variable_names = serializers.JSONField(
        required=False,
        allow_null=True,
        help_text=(
            "List of variable names referenced inside the prompt messages, "
            "e.g. ['user_name', 'ticket_summary', 'tone']. These are the "
            "placeholders that callers must provide at runtime. Auto-derived "
            "from the prompt body if omitted."
        ),
    )
    placeholders = serializers.JSONField(
        required=False,
        allow_null=True,
        help_text=(
            "Optional default values for the template's variables. "
            "Object with shape {variable_name: default_value}. Example: "
            "{'tone': 'friendly', 'user_name': 'there'}. Used in playgrounds "
            "and previews when no explicit value is supplied."
        ),
    )
    prompt_folder = serializers.UUIDField(
        required=False,
        allow_null=True,
        help_text=(
            "UUID of the folder to place this template in. **How to get it:** "
            "call `list_prompt_folders` first. Omit or pass null to leave at "
            "workspace root."
        ),
    )
    organization = serializers.UUIDField(
        read_only=True,
        help_text="Organization UUID. Auto-set from the authenticated user.",
    )
    created_by = serializers.UUIDField(
        read_only=True,
        help_text="UUID of the user who created the template. Auto-set on create.",
    )

    class Meta:
        model = PromptTemplate
        fields = [
            "id",
            "name",
            "description",
            "variable_names",
            "organization",
            "prompt_folder",
            "placeholders",
            "created_by",
        ]
        read_only_fields = ["id", "organization", "created_by"]

    def validate_prompt_folder(self, value):
        if value is None:
            return value

        request = self.context.get("request")
        organization = request_organization(request)
        if organization is None:
            raise serializers.ValidationError("Prompt folder not found")

        if not PromptFolder.no_workspace_objects.filter(
            request_workspace_filter(request),
            organization=organization,
            deleted=False,
            id=value.id,
        ).exists():
            raise serializers.ValidationError("Prompt folder not found")

        return value


class PromptExecutionSerializer(serializers.ModelSerializer):
    model = serializers.SerializerMethodField()
    model_detail = serializers.SerializerMethodField()
    collaborators = serializers.SerializerMethodField()
    prompt_folder_name = serializers.SerializerMethodField()
    created_by = serializers.SerializerMethodField()

    class Meta:
        model = PromptTemplate
        fields = [
            "id",
            "name",
            "updated_at",
            "model",
            "collaborators",
            "model_detail",
            "prompt_folder",
            "is_sample",
            "prompt_folder_name",
            "created_by",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.model_manager = LiteLLMModelManager(model_name="", organization_id=None)

    def get_latest_execution(self, obj):
        """
        Fetch the latest execution for the given template.
        """
        return (
            obj.prefetched_versions[0]
            if hasattr(obj, "prefetched_versions") and obj.prefetched_versions
            else None
        )

    def get_model_and_details(self, latest_execution):
        """
        Extract model and model_detail from prompt_config_snapshot.
        """
        if latest_execution:
            prompt_config_snapshot = latest_execution.prompt_config_snapshot or {}
            if isinstance(prompt_config_snapshot, list):
                prompt_config_snapshot = (
                    prompt_config_snapshot[0] if prompt_config_snapshot else {}
                )
            if isinstance(prompt_config_snapshot, dict):
                config = prompt_config_snapshot.get("configuration", {})
            else:
                config = {}
            return config.get("model"), config.get("model_detail")
        return None, None

    def get_model(self, obj):
        latest_execution = self.get_latest_execution(obj)
        model, _ = self.get_model_and_details(latest_execution)
        return model

    def get_model_detail(self, obj):
        latest_execution = self.get_latest_execution(obj)
        _, model_detail = self.get_model_and_details(latest_execution)

        # If model_detail is not available, generate it from the model name
        if model_detail is None:
            model_name = self.get_model(obj)
            if model_name:
                # Get the provider from the model manager
                try:
                    # Initialize model manager with the specific model name
                    model_manager = LiteLLMModelManager(
                        model_name=model_name, organization_id=None
                    )
                    provider = model_manager.get_provider(model_name=model_name)

                    # Generate model detail structure
                    model_detail = {
                        "logo_url": ProviderLogoUrls.get_url_by_provider(provider),
                        "providers": provider,
                        "model_name": model_name,
                        "is_available": True,  # Default to True since we can't check API keys here
                        "type": get_model_mode(model_name),
                    }
                except Exception as e:
                    logger.warning(
                        f"Could not generate model detail for {model_name}: {str(e)}"
                    )
                    # Fallback model detail
                    model_detail = {
                        "logo_url": None,
                        "providers": "unknown",
                        "model_name": model_name,
                        "is_available": False,
                        "type": "chat",
                    }

        return model_detail

    def get_collaborators(self, obj):
        collaborators = obj.collaborators.all()
        return [{"email": user.email, "name": user.name} for user in collaborators]

    def get_prompt_folder_name(self, obj):
        """
        Return the name of the prompt folder if it exists.
        Returns None if prompt_folder is None.
        """
        if obj.prompt_folder:
            return obj.prompt_folder.name
        return None

    def get_created_by(self, obj):
        """
        Return the name of the user who created this template.
        Returns None if created_by is None.
        """
        if obj.created_by:
            return obj.created_by.name
        return obj.organization.name if obj.organization else None


class PromptTemplateFilter(django_filters.FilterSet):
    name = django_filters.CharFilter(lookup_expr="icontains")
    version = django_filters.CharFilter(lookup_expr="iexact")
    created_at = django_filters.DateTimeFromToRangeFilter()
    # is_default = django_filters.BooleanFilter()

    class Meta:
        model = PromptTemplate
        fields = ["name", "version", "created_at"]


class PromptExecutionFilter(django_filters.FilterSet):
    name = django_filters.CharFilter(lookup_expr="icontains")

    class Meta:
        model = PromptTemplate
        fields = [
            "name",
        ]


class PromptHistoryTemplateAddonSerializer(serializers.ModelSerializer):
    template_name = serializers.SerializerMethodField()
    output = serializers.SerializerMethodField()
    prompt_config_snapshot = serializers.SerializerMethodField()
    original_template = serializers.SerializerMethodField()
    evaluation_results = serializers.SerializerMethodField()
    template_version = serializers.SerializerMethodField()

    class Meta:
        model = PromptTemplate
        fields = [
            "id",
            "template_version",
            "output",
            "prompt_config_snapshot",
            "template_name",
            "original_template",
            "variable_names",
            "evaluation_results",
            "evaluation_configs",
            "created_at",
            "is_default",
            "is_draft",
            "updated_at",
        ]

    def get_template_name(self, obj):
        return obj.name

    def get_output(self, obj):
        return []

    def get_prompt_config_snapshot(self, obj):
        if isinstance(obj.prompt_config, list):
            return obj.prompt_config[0]
        else:
            return obj.prompt_config

    def get_original_template(self, obj):
        return str(obj.root_template.id)

    def get_evaluation_results(self, obj):
        return {}

    def get_template_version(self, obj):
        return obj.version


class PromptHistoryExecutionSerializer(serializers.ModelSerializer):
    template_name = serializers.SerializerMethodField()
    variable_names = serializers.SerializerMethodField()
    prompt_config_snapshot = serializers.SerializerMethodField()
    labels = serializers.SerializerMethodField()

    class Meta:
        model = PromptVersion
        fields = [
            "id",
            "template_version",
            "output",
            "prompt_config_snapshot",
            "template_name",
            "original_template",
            "metadata",
            "variable_names",
            "evaluation_results",
            "evaluation_configs",
            "created_at",
            "is_default",
            "commit_message",
            "updated_at",
            "is_draft",
            "labels",
            "placeholders",
            "prompt_base_template",
        ]
        read_only_fields = ["id", "output", "created_at", "updated_at"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.model_manager = LiteLLMModelManager(model_name="", organization_id=None)

    def get_template_name(self, obj):
        return obj.original_template.name

    def get_prompt_config_snapshot(self, obj):
        """
        Get prompt_config_snapshot with backward compatibility for modelDetail.
        If modelDetail is missing from configuration, generate it from the model name.
        """
        config_snapshot = obj.prompt_config_snapshot

        # Handle both list and dict formats
        if isinstance(config_snapshot, list):
            # Process each config in the list
            for config in config_snapshot:
                if isinstance(config, dict):
                    self._ensure_model_detail(config)
        elif isinstance(config_snapshot, dict):
            self._ensure_model_detail(config_snapshot)

        return config_snapshot

    def get_labels(self, obj):
        # Single optimized query: join through table with PromptLabel
        # Uses values() to avoid ORM object instantiation overhead
        # Filters deleted=False to match no_workspace_objects behavior (bypasses workspace filtering)
        labels_data = obj.labels.through.objects.filter(
            promptversion_id=obj.id, promptlabel__deleted=False
        ).values("promptlabel__id", "promptlabel__name", "promptlabel__type")

        return [
            {
                "id": str(item["promptlabel__id"]),
                "name": item["promptlabel__name"],
                "type": item["promptlabel__type"],
            }
            for item in labels_data
        ]

    def _ensure_model_detail(self, config):
        """
        Ensure model_detail is present in the configuration.
        If missing, generate it from the model name.
        """
        if "configuration" in config:
            configuration = config["configuration"]
            if "model" in configuration and "model_detail" not in configuration:
                model_name = configuration["model"]
                if model_name:
                    try:
                        # Initialize model manager with the specific model name
                        model_manager = LiteLLMModelManager(
                            model_name=model_name, organization_id=None
                        )
                        provider = model_manager.get_provider(model_name=model_name)

                        # Generate model detail structure
                        model_detail = {
                            "logo_url": ProviderLogoUrls.get_url_by_provider(provider),
                            "providers": provider,
                            "model_name": model_name,
                            "is_available": True,  # Default to True since we can't check API keys here
                            "type": get_model_mode(model_name),
                        }
                        configuration["model_detail"] = model_detail
                    except Exception as e:
                        logger.warning(
                            f"Could not generate model detail for {model_name}: {str(e)}"
                        )
                        # Fallback model detail
                        configuration["model_detail"] = {
                            "logo_url": None,
                            "providers": "unknown",
                            "model_name": model_name,
                            "is_available": False,
                            "type": "chat",
                        }

    def get_variable_names(self, obj):
        var_names = obj.variable_names.copy()
        if isinstance(var_names, list):
            var_names = {}
        for key in list(var_names):
            if isinstance(obj.original_template.variable_names, list):
                og_temp_var_names = {}
            else:
                og_temp_var_names = obj.original_template.variable_names
            value = og_temp_var_names.get(key)
            if value is not None:
                var_names[key] = value
        max_len = max((len(values) for values in var_names.values()), default=1)
        for _key, value in var_names.items():
            if len(value) < max_len:
                value.extend([""] * (max_len - len(value)))
        return var_names


class PromptVersionSerializer(serializers.ModelSerializer):
    class Meta:
        model = PromptVersion
        fields = [
            "id",
            "template_version",
            "output",
            "prompt_config_snapshot",
            "template_name",
            "original_template",
            "metadata",
            "variable_names",
            "evaluation_results",
            "evaluation_configs",
            "created_at",
            "is_default",
            "commit_message",
            "updated_at",
            "is_draft",
            "prompt_base_template",
        ]
        read_only_fields = ["id", "output", "created_at", "updated_at"]


class PromptHistoryExecutionFilter(django_filters.FilterSet):
    template_name = django_filters.CharFilter(lookup_expr="icontains")
    template_version = django_filters.CharFilter(lookup_expr="iexact")
    created_at = django_filters.DateTimeFromToRangeFilter()

    class Meta:
        model = PromptVersion
        fields = ["template_name", "template_version", "created_at"]


class SavePromptTemplateSerializer(serializers.Serializer):
    prompt_config = serializers.ListField(child=serializers.DictField(), required=True)
    name = serializers.CharField(max_length=255, required=False)
    variable_names = serializers.JSONField(required=False)
    description = serializers.CharField(max_length=255, required=False)


class SaveNewVersionSerializer(serializers.Serializer):
    prompt_id = serializers.UUIDField(required=True)
    is_draft = serializers.BooleanField(required=False, default=False)
    prompt_config = serializers.ListField(child=serializers.DictField(), required=False)
    set_default = serializers.BooleanField(required=False, default=False)
    version_name = serializers.CharField(required=False)

    def validate(self, data):
        set_default = data.get("set_default", False)
        version = data.get("version_name")

        # Make version required when set_default is True
        if set_default and not version:
            raise serializers.ValidationError(
                {"version": "Version is required when set_default is True"}
            )

        # Validate version format if provided
        if version is not None:
            pattern = r"^v\d+$"  # Match v1, v2, v3, etc.
            if not re.match(pattern, version):
                raise serializers.ValidationError(
                    {"version": "Version must be in the format 'v{version_num}'."}
                )

        return data


class SingleEvaluationConfigSerializer(serializers.Serializer):
    """Serializer for a single evaluation configuration in PromptTemplate"""

    id = serializers.CharField(required=True)
    name = serializers.CharField(required=True)
    config = serializers.JSONField(required=False, default=dict)
    mapping = serializers.JSONField(required=False, default=dict)
    params = serializers.JSONField(required=False, default=dict)

    def validate(self, data):
        """Validate the evaluation configuration"""
        if not data.get("id"):
            raise serializers.ValidationError(
                "Evaluation configuration must contain an 'id' field"
            )

        if not data.get("name"):
            raise serializers.ValidationError(
                "Evaluation configuration must contain a 'name' field"
            )

        return data
