# serializers.py
from django.core.exceptions import ValidationError as ModelValidationError
from drf_yasg.utils import swagger_serializer_method
from rest_framework import serializers
from rest_framework.exceptions import NotFound

from agentic_eval.core_evals.run_prompt.litellm_models import LiteLLMModelManager
from model_hub.models.choices import ProviderLogoUrls
from model_hub.models.dataset_versions import DatasetVersion
from model_hub.models.develop_dataset import Column, Dataset
from model_hub.models.evals_metric import UserEvalMetric
from model_hub.models.experiments import (
    ExperimentAgentConfig,
    ExperimentDatasetTable,
    ExperimentPromptConfig,
    ExperimentRun,
    ExperimentRunItem,
    ExperimentsTable,
)
from model_hub.serializers.experiment_metadata import (
    ExperimentDescriptionField,
    ExperimentNameField,
)
from model_hub.serializers.experiment_run_scope import (
    ExperimentLimitsSerializer,
    ExperimentMetadataField,
    ExperimentSampleSerializer,
    ExperimentSplitRunScopeSerializer,
    RunMetadataField,
)
from model_hub.serializers.results_runs import CanonicalRunSerializer
from model_hub.services.experiment_scope_gate import (
    SCOPE_FIELDS,
    SCOPE_OFF_FIELD_MESSAGE,
    SCOPE_OFF_MESSAGE,
    UNAVAILABLE_ROW_LIMITS,
    UNAVAILABLE_ROW_LIMITS_MESSAGE,
    scope_enabled,
    without_scope_fields,
)
from model_hub.services.run_metadata_public import reject_reserved_metadata_keys
from model_hub.utils.workspace_scope import (
    scoped_column_queryset,
    scoped_dataset_queryset,
    scoped_dataset_version_queryset,
    scoped_user_eval_metric_queryset,
)
from tfc.middleware.workspace_context import get_current_organization
from tfc.utils.serializer_fields import (
    JsonValueField,
    StringOrArrayField,
    StringOrObjectField,
)


class ReservedRunMetadataInputMixin:
    def to_internal_value(self, data):
        reject_reserved_metadata_keys(data)
        return super().to_internal_value(data)


class ExperimentRunSerializer(
    ReservedRunMetadataInputMixin, serializers.ModelSerializer
):
    """Stable write/read contract for an experiment execution.

    Dataset versions are represented by their stable primary key and validated
    through the DatasetVersion relation.
    """

    experiment_id = serializers.PrimaryKeyRelatedField(
        source="experiment",
        queryset=ExperimentsTable.objects.all(),
        required=False,
        allow_null=True,
    )
    dataset_version_id = serializers.PrimaryKeyRelatedField(
        source="dataset_version",
        queryset=DatasetVersion.objects.all(),
    )
    metadata = RunMetadataField(required=False)
    model_config = serializers.DictField(required=False)
    evaluator_versions = serializers.ListField(
        child=serializers.JSONField(), required=False
    )

    class Meta:
        model = ExperimentRun
        fields = [
            "id",
            "experiment_id",
            "dataset_version_id",
            "task_kind",
            "status",
            "started_at",
            "completed_at",
            "metadata",
            "model_config",
            "prompt_version_id",
            "agent_version_id",
            "evaluator_versions",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        request = self.context.get("request") if self.context else None
        dataset_versions = scoped_dataset_version_queryset(request)
        self.fields["dataset_version_id"].queryset = dataset_versions
        self.fields["experiment_id"].queryset = ExperimentsTable.objects.filter(
            dataset__in=scoped_dataset_queryset(request),
            deleted=False,
        )

    def validate(self, attrs):
        attrs = super().validate(attrs)
        candidate = ExperimentRun(
            **{
                field: attrs.get(field, getattr(self.instance, field, None))
                for field in (
                    "experiment",
                    "dataset_version",
                    "started_at",
                    "completed_at",
                )
            }
        )
        try:
            candidate.clean()
        except ModelValidationError as exc:
            raise serializers.ValidationError(exc.message_dict) from exc
        return attrs


class ExperimentRunItemSerializer(
    ReservedRunMetadataInputMixin, serializers.ModelSerializer
):
    """Stable write/read contract for one example/trial result."""

    run_id = serializers.PrimaryKeyRelatedField(
        source="run",
        queryset=ExperimentRun.objects.all(),
    )
    trial_number = serializers.IntegerField(required=False, min_value=1)
    latency_ms = serializers.FloatField(required=False, allow_null=True, min_value=0)
    cost = serializers.FloatField(required=False, allow_null=True, min_value=0)
    metadata = RunMetadataField(required=False)
    score_refs = serializers.ListField(child=serializers.JSONField(), required=False)

    class Meta:
        model = ExperimentRunItem
        fields = [
            "id",
            "run_id",
            "example_id",
            "trial_number",
            "status",
            "input",
            "expected",
            "output",
            "error",
            "trace_id",
            "root_span_id",
            "latency_ms",
            "token_usage",
            "cost",
            "score_refs",
            "metadata",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        request = self.context.get("request") if self.context else None
        runs = ExperimentRun.objects.filter(
            dataset_version__in=scoped_dataset_version_queryset(request),
            deleted=False,
        )
        self.fields["run_id"].queryset = runs


class ExperimentsTableSerializer(
    ReservedRunMetadataInputMixin, serializers.ModelSerializer
):
    dataset_id = serializers.PrimaryKeyRelatedField(
        queryset=Dataset.objects.all(), source="dataset"
    )
    column_id = serializers.PrimaryKeyRelatedField(
        queryset=Column.objects.all(), source="column"
    )
    user_eval_template_ids = serializers.PrimaryKeyRelatedField(
        many=True, queryset=UserEvalMetric.objects.all(), required=False
    )

    class Meta:
        model = ExperimentsTable
        fields = [
            "name",
            "dataset_id",
            "prompt_config",
            "user_eval_template_ids",
            "column_id",
        ]
        read_only_fields = ["id", "status"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        request = self.context.get("request") if self.context else None
        self.fields["dataset_id"].queryset = scoped_dataset_queryset(request)
        self.fields["column_id"].queryset = scoped_column_queryset(request)
        # `user_eval_template_ids` is many=True — the per-pk lookup runs on
        # the child_relation, so scope both to keep workspace isolation
        # honest (see develop_optimisation.py for the same pattern).
        scoped_metrics = scoped_user_eval_metric_queryset(request)
        self.fields["user_eval_template_ids"].queryset = scoped_metrics
        self.fields["user_eval_template_ids"].child_relation.queryset = scoped_metrics

    def validate_prompt_config(self, value):
        # Add any specific validation for prompt_config if needed
        return value

    def to_representation(self, instance):
        """
        Add logo_url to each model's params in prompt_config.model_params.

        This keeps the stored prompt_config unchanged while enriching the
        serialized response for the frontend.
        """
        data = super().to_representation(instance)
        prompt_config = data.get("prompt_config")

        def _augment_config(config: dict):
            if not isinstance(config, dict):
                return

            model_params = config.get("model_params")
            if not isinstance(model_params, dict):
                return

            for _model_name, params in model_params.items():
                if not isinstance(params, dict):
                    continue

                if (
                    "logo_url" in params
                    or "provider" in params
                    or "providers" in params
                ):
                    continue

                provider = params.get("provider") or params.get("providers")

                if not provider:
                    model_manager = LiteLLMModelManager(
                        model_name=_model_name,
                        organization_id=(
                            get_current_organization() or instance.user.organization
                        ).id,
                    )

                    model = next(
                        model
                        for model in model_manager.models
                        if _model_name.lower() == model["model_name"].lower()
                    )

                    provider = model.get("providers")

                if not provider:
                    continue

                try:
                    params["logo_url"] = ProviderLogoUrls.get_url_by_provider(provider)
                    params["providers"] = provider
                except Exception:
                    # If we can't resolve the logo URL, skip silently
                    continue

        try:
            if isinstance(prompt_config, list):
                for cfg in prompt_config:
                    _augment_config(cfg)
            elif isinstance(prompt_config, dict):
                _augment_config(prompt_config)
        except Exception:
            # Serialization enrichment should never break the response.
            pass

        return data


class ExperimentsTableUpdateSerializer(ExperimentsTableSerializer):
    experiment_id = serializers.UUIDField()
    re_run = serializers.BooleanField(required=False, default=False)

    class Meta(ExperimentsTableSerializer.Meta):
        fields = [
            "experiment_id",
            "re_run",
            *ExperimentsTableSerializer.Meta.fields,
        ]


class ExperimentsTableGetSerializer(serializers.ModelSerializer):
    class Meta:
        model = ExperimentsTable
        fields = ["id", "name"]


class ColumnSerializer(serializers.ModelSerializer):
    class Meta:
        model = Column
        fields = ["id", "name"]


class DerivedDatasetSerializer(serializers.ModelSerializer):
    experiment = serializers.SerializerMethodField()

    class Meta:
        model = ExperimentDatasetTable
        fields = ["id", "name", "experiment"]

    def get_experiment(self, obj):
        if obj.experiment:
            return ExperimentsTableGetSerializer(obj.experiment).data
        return None


class ExperimentDatasetTableSerializer(serializers.ModelSerializer):
    columns = ColumnSerializer(many=True, read_only=True)

    class Meta:
        model = ExperimentDatasetTable
        fields = ["id", "name", "prompt_config", "status", "columns"]


class ExperimentBasicSerializer(serializers.ModelSerializer):
    experiment_datasets = ExperimentDatasetTableSerializer(many=True, read_only=True)
    column_name = ColumnSerializer(read_only=True)

    class Meta:
        model = ExperimentsTable
        fields = [
            "id",
            "name",
            "column_name",
            "prompt_config",
            "status",
            "experiment_datasets",
        ]


class ColumnSerializer(serializers.ModelSerializer):
    class Meta:
        model = Column
        fields = ["id", "name"]


class ExperimentDatasetTableSerializer(serializers.ModelSerializer):
    columns = ColumnSerializer(many=True, read_only=True)

    class Meta:
        model = ExperimentDatasetTable
        fields = ["id", "name", "prompt_config", "status", "columns"]


class ExperimentDetailSerializer(serializers.ModelSerializer):
    experiment_datasets = ExperimentDatasetTableSerializer(many=True, read_only=True)

    class Meta:
        model = ExperimentsTable
        fields = [
            "id",
            "name",
            "column",
            "dataset",
            "prompt_config",
            "status",
            "experiment_datasets",
        ]


class ExperimentListSerializer(serializers.ModelSerializer):
    eval_templates_count = serializers.SerializerMethodField()
    models_count = serializers.SerializerMethodField()

    class Meta:
        model = ExperimentsTable
        fields = [
            "id",
            "name",
            "status",
            "eval_templates_count",
            "created_at",
            "models_count",
            "dataset",
        ]

    def get_eval_templates_count(self, obj):
        return obj.user_eval_template_ids.count()

    def get_models_count(self, obj):
        # Extract unique models from prompt_config
        models = set()
        for config in obj.prompt_config:
            model_list = config.get("model", [])
            if isinstance(model_list, list):
                for model in model_list:
                    # Handle both string and dict (ModelSpec) formats
                    if isinstance(model, dict):
                        models.add(model.get("name"))
                    else:
                        models.add(model)
            elif model_list:
                models.add(model_list)
        return len(models)


class GroupSerializer(serializers.Serializer):
    id = serializers.CharField()
    name = serializers.CharField()
    data_type = serializers.CharField()
    origin = serializers.CharField()


class ColumnConfigSerializer(serializers.Serializer):
    id = serializers.CharField(source="id")
    name = serializers.CharField()
    origin_type = serializers.CharField()
    data_type = serializers.CharField()
    status = serializers.CharField()
    group = GroupSerializer(required=False, allow_null=True)


class CellValueSerializer(serializers.Serializer):
    cell_value = serializers.CharField()
    status = serializers.CharField()
    metadata = serializers.DictField()
    value_infos = serializers.DictField(required=False)


class TableRowSerializer(serializers.Serializer):
    row_id = serializers.CharField()
    # Dynamic fields will be added in __init__


class MetadataSerializer(serializers.Serializer):
    total_rows = serializers.IntegerField()
    total_pages = serializers.IntegerField()


class ExperimentSerializer(serializers.ModelSerializer):
    column_config = serializers.SerializerMethodField()
    table = serializers.SerializerMethodField()
    metadata = serializers.SerializerMethodField()

    class Meta:
        model = ExperimentsTable
        fields = ["column_config", "table", "metadata"]

    def get_column_config(self, obj):
        # You'll need to implement the logic to transform your columns into the required format
        columns = []
        # Add logic to populate columns based on your data
        return ColumnConfigSerializer(columns, many=True).data

    def get_table(self, obj):
        # Implement logic to transform your data into the required table format
        rows = []
        # Add logic to populate rows based on your data
        return rows

    def get_metadata(self, obj):
        return {
            "total_rows": 0,  # Replace with actual count
            "total_pages": 1,  # Calculate based on your pagination
        }


class ExperimentIdListSerializer(serializers.Serializer):
    experiment_ids = serializers.ListField(
        child=serializers.UUIDField(), required=True, allow_empty=False
    )


class RerunCellEntrySerializer(serializers.Serializer):
    """A classic snapshot cell or an explicitly identified canonical Results cell."""

    column_id = serializers.UUIDField()
    row_id = serializers.UUIDField(required=False)
    run_id = serializers.UUIDField(required=False)
    example_id = serializers.UUIDField(required=False)

    def validate(self, attrs):
        has_row = "row_id" in attrs
        has_run = "run_id" in attrs
        has_example = "example_id" in attrs
        if (has_row and (has_run or has_example)) or (
            not has_row and not (has_run and has_example)
        ):
            raise serializers.ValidationError(
                "Provide either row_id or both run_id and example_id."
            )
        return attrs


class ExperimentRerunCellsSerializer(serializers.Serializer):
    """Validates input for cell/column-level rerun.

    - cells: {column_id, row_id} or {column_id, run_id, example_id} entries.
      BE determines rerun type from column source (output, per-EDT eval, base eval).
    - source_ids: list of EDT IDs — rerun all rows for those columns (bulk)
    - user_eval_metric_ids: list of UserEvalMetric IDs — rerun only those evals (bulk)
    At least one must be provided.
    """

    source_ids = serializers.ListField(
        child=serializers.UUIDField(),
        required=False,
        default=list,
    )
    cells = RerunCellEntrySerializer(
        many=True,
        required=False,
        default=list,
    )
    user_eval_metric_ids = serializers.ListField(
        child=serializers.UUIDField(),
        required=False,
        default=list,
    )
    acknowledge_http_side_effects = serializers.BooleanField(
        required=False,
        help_text="Required true for HTTP retries: a prior attempt may have had side effects even after a timeout.",
    )
    failed_only = serializers.BooleanField(
        required=False,
        default=False,
    )
    max_concurrent_rows = serializers.IntegerField(
        required=False,
        min_value=1,
        default=10,
    )

    def validate(self, attrs):
        has_source_ids = bool(attrs.get("source_ids"))
        has_cells = bool(attrs.get("cells"))
        has_eval_metrics = bool(attrs.get("user_eval_metric_ids"))

        if not has_source_ids and not has_cells and not has_eval_metrics:
            raise serializers.ValidationError(
                "At least one of source_ids, cells, or user_eval_metric_ids must be provided."
            )
        if has_eval_metrics and has_source_ids:
            raise serializers.ValidationError(
                "user_eval_metric_ids cannot be combined with source_ids."
            )
        return attrs


# ---- V2 Serializers (Input) ----


class EvalMetricEntrySerializer(serializers.Serializer):
    """Inline eval metric config for V2 experiment APIs.

    Mirrors fields from AddExperimentEvalView (views/experiments.py:2024-2034).
    On update, `id` is sent back to identify existing UserEvalMetric records.
    """

    id = serializers.UUIDField(required=False, allow_null=True)
    template_id = serializers.UUIDField()
    name = serializers.CharField(max_length=2000)
    config = (
        serializers.JSONField()
    )  # { mapping: {...}, config: {...}, reasonColumn: bool }
    model = serializers.CharField(
        max_length=255, required=False, default="", allow_blank=True
    )
    error_localizer = serializers.BooleanField(required=False, default=False)
    kb_id = serializers.UUIDField(required=False, allow_null=True, default=None)
    # Per-binding weight overrides for composite evals. Ignored for
    # single-template metrics. See Phase 7 wiring plan.
    composite_weight_overrides = serializers.JSONField(
        required=False, allow_null=True, default=None
    )
    # The eval template version this eval runs (an EvalTemplateVersion id).
    # config.version_pin ("v3", "latest") is honoured too. An entry naming
    # neither runs the template default, on update too (the entry's config
    # replaces the stored one); see model_hub/services/experiment_eval_pins.py.
    pinned_version_id = serializers.UUIDField(required=False, allow_null=True)

    def validate_template_id(self, value):
        # External metrics are scored by the caller and never run on
        # the server; refuse them here, before the experiment or any metric
        # is created. With a caller (the request's organization and workspace,
        # else the bound context authentication sets), only a template the
        # caller may use is refused here: any other id, another organization's
        # or workspace's external template included, passes and the view
        # answers it like an id that does not exist. Without one (validated
        # outside a request; a request that resolves no organization is
        # refused by authentication first), every external template is.
        from model_hub.models.evals_metric import EXTERNAL_EVAL_TYPE, EvalTemplate
        from model_hub.services.eval_group import _accessible_eval_template_filter
        from model_hub.services.external_metrics import EXTERNAL_TEMPLATE_REFUSAL
        from tfc.utils.tenant_scope import caller_organization, caller_workspace

        request = (self.context or {}).get("request")
        templates = EvalTemplate.no_workspace_objects.filter(
            id=value, eval_type=EXTERNAL_EVAL_TYPE
        )
        organization = caller_organization(request)
        if organization is not None:
            templates = templates.filter(
                _accessible_eval_template_filter(
                    organization, caller_workspace(request)
                )
            )
        if templates.exists():
            raise serializers.ValidationError(EXTERNAL_TEMPLATE_REFUSAL)
        return value


class _ExtraFieldsMixin:
    """Pass unknown keys through both directions unchanged.

    Provides the typed-keys-plus-escape-hatch pattern:

    * ``to_internal_value`` (request input) — declared fields are validated;
      any additional provider-specific key is accepted as-is.
    * ``to_representation`` (response output) — declared fields render
      normally; any additional key present on a source dict is copied
      straight through. Without this, serializers that promise
      ``additionalProperties: True`` (e.g. ``EvalUsageTableRowSerializer``'s
      dynamic ``input_var_<name>`` cells) would silently drop those keys at
      ``.data`` time.
    """

    def to_internal_value(self, data):
        validated = super().to_internal_value(data)
        for key, value in data.items():
            if key not in self.fields:
                validated[key] = value
        return validated

    def to_representation(self, instance):
        rep = super().to_representation(instance)
        # `instance` can be a dict (views build responses as raw dicts) or a
        # model instance. Only dicts carry surprise keys — model instances
        # already went through ORM field mapping.
        if isinstance(instance, dict):
            for key, value in instance.items():
                if key not in self.fields:
                    rep[key] = value
        return rep


class MessageItemSerializer(_ExtraFieldsMixin, serializers.Serializer):
    """A single prompt message: role + content (string or content-parts array)."""

    role = serializers.CharField()
    # content is either a plain text string or an array of content-part objects
    # (OpenAI multi-part format). StringOrArrayField emits x-string-or-array
    # so orval generates z.union([z.string(), z.array(z.unknown())]) — the
    # correct union type instead of narrowing to object.
    content = StringOrArrayField()
    name = serializers.CharField(required=False)
    tool_calls = JsonValueField(required=False)
    tool_call_id = serializers.CharField(required=False)
    # Client-side id (getRandomId) used as React key on messages list.
    # Declared explicitly so DRF passes it through to_internal_value instead
    # of stripping it, which would cause duplicate keys on reload.
    id = serializers.CharField(required=False)

    class Meta:
        swagger_schema_fields = {"additionalProperties": True}


class PromptModelParamsSerializer(_ExtraFieldsMixin, serializers.Serializer):
    """Known LLM sampling parameters for a prompt config entry.

    Sampling-level params only (temperature, max_tokens, etc.).
    Call-level config (tools, tool_choice) lives in PromptConfigurationSerializer.
    Typed keys are validated; any additional provider-specific key is accepted
    via _ExtraFieldsMixin.  additionalProperties:true in the swagger schema
    triggers .passthrough() in the generated zod (via post-processing in
    generate-openapi-client.mjs) so unknown provider keys are not stripped.
    """

    temperature = serializers.FloatField(required=False)
    max_tokens = serializers.IntegerField(required=False)
    top_p = serializers.FloatField(required=False)
    frequency_penalty = serializers.FloatField(required=False)
    presence_penalty = serializers.FloatField(required=False)
    response_format = StringOrObjectField(required=False)

    def to_internal_value(self, data):
        from model_hub.services.experiment_config_resolver import (
            normalize_prompt_params,
        )

        # Old forms send camelCase keys and null slider defaults. Normalize
        # before typed validation: null is unset, but non-null aliases must
        # obey the same integer/float/schema contract as canonical keys.
        if isinstance(data, dict):
            data = normalize_prompt_params(data)
        return super().to_internal_value(data)

    class Meta:
        swagger_schema_fields = {"additionalProperties": True}


class PromptConfigurationSerializer(_ExtraFieldsMixin, serializers.Serializer):
    """Additional configuration for a prompt config entry.

    Typed keys are validated; any additional key is accepted via _ExtraFieldsMixin.
    """

    tool_choice = serializers.CharField(required=False, allow_blank=True)
    template_format = serializers.CharField(required=False, allow_blank=True)
    tools = serializers.ListField(child=JsonValueField(), required=False)
    output_format = serializers.CharField(required=False, allow_blank=True)
    model_type = serializers.CharField(required=False, allow_blank=True)
    model_detail = JsonValueField(required=False)
    voice_id = serializers.CharField(required=False, allow_blank=True)

    class Meta:
        swagger_schema_fields = {"additionalProperties": True}


class _HttpStrictSerializer(serializers.Serializer):
    def to_internal_value(self, data):
        if isinstance(data, dict):
            unknown = set(data) - set(self.fields)
            if unknown:
                raise serializers.ValidationError(
                    dict.fromkeys(sorted(unknown), "Unknown HTTP source field.")
                )
        return super().to_internal_value(data)


class HttpExperimentHeaderSerializer(_HttpStrictSerializer):
    name = serializers.RegexField(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$", max_length=255)
    value = serializers.CharField(
        required=False, allow_blank=True, trim_whitespace=False, max_length=16384
    )
    secret = serializers.CharField(required=False, allow_blank=True, max_length=255)
    secret_ref = serializers.CharField(required=False, allow_blank=True, max_length=255)
    scheme = serializers.ChoiceField(
        choices=["bearer"],
        required=False,
        help_text="bearer sends the secret's value as 'Bearer <value>'. Needs a secret reference.",
    )

    def validate(self, data):
        refs = [data[key] for key in ("secret", "secret_ref") if data.get(key)]
        if len(set(refs)) > 1 or (refs and data.get("value")):
            raise serializers.ValidationError(
                "Choose one header value or secret reference."
            )
        if data.get("scheme") and not refs:
            raise serializers.ValidationError(
                "A bearer header takes its token from a workspace secret. Pick the secret."
            )
        if any(char in data.get("value", "") for char in ("\r", "\n", "\0")):
            raise serializers.ValidationError(
                "Header values cannot contain control characters."
            )
        if data["name"].lower() in {
            "host",
            "content-length",
            "transfer-encoding",
            "connection",
            "proxy-authorization",
            "proxy-connection",
            "accept-encoding",
        }:
            raise serializers.ValidationError(
                "This header is managed by the HTTP transport."
            )
        return data


class HttpExperimentSettingsSerializer(_HttpStrictSerializer):
    url = serializers.CharField(max_length=4096)
    method = serializers.ChoiceField(
        choices=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
        default="POST",
    )
    headers = HttpExperimentHeaderSerializer(
        many=True, required=False, default=list, max_length=64
    )
    body_template = serializers.CharField(
        required=False,
        default="",
        allow_blank=True,
        trim_whitespace=False,
        max_length=2 * 1024 * 1024,
    )
    output_path = serializers.CharField(
        required=False, default="$.reply", allow_blank=True
    )
    streaming = serializers.BooleanField(required=False, default=False)
    audio = serializers.ChoiceField(choices=["text"], required=False, default="text")
    timeout_seconds = serializers.IntegerField(
        required=False, min_value=1, max_value=30
    )
    # Absent on plain HTTP sources (no default), so their stored settings and
    # readback do not change. "a2a": an A2A agent saved from its agent card.
    protocol = serializers.ChoiceField(
        choices=["a2a"],
        required=False,
        help_text="a2a: each row sends one A2A JSON-RPC message/send; the reply's text is the output.",
    )

    def validate(self, data):
        if data.get("protocol") == "a2a" and data.get("method", "POST") != "POST":
            raise serializers.ValidationError(
                {"method": "A2A agents take POST requests."}
            )
        return data

    def validate_url(self, value):
        from urllib.parse import urlsplit

        from model_hub.services.http_experiment_source import VARIABLE

        try:
            parsed = urlsplit(value)
            valid = (
                parsed.scheme in ("http", "https")
                and parsed.hostname
                and parsed.port != 0
                and parsed.username is None
                and parsed.password is None
                and not parsed.fragment
                and "{" not in parsed.netloc
                and "}" not in parsed.netloc
                and not any(
                    char.isspace() or ord(char) < 32
                    for char in VARIABLE.sub("value", value)
                )
            )
        except ValueError:
            valid = False
        if not valid:
            raise serializers.ValidationError(
                "Use a public HTTP(S) URL with a fixed host and no embedded credentials."
            )
        return value

    def validate_output_path(self, value):
        from model_hub.services.http_experiment_source import path_tokens

        if value:
            if not value.startswith("$"):
                raise serializers.ValidationError(
                    "Use an output path beginning with $."
                )
            try:
                path_tokens(value)
            except (ValueError, SyntaxError) as exc:
                raise serializers.ValidationError(str(exc)) from exc
        return value

    def validate_streaming(self, value):
        if value:
            raise serializers.ValidationError(
                "HTTP experiment runs currently require a non-streaming JSON response."
            )
        return value

    def validate_headers(self, value):
        names = [header["name"].lower() for header in value]
        if len(names) != len(set(names)):
            raise serializers.ValidationError("Header names must be unique.")
        return value


class HttpExperimentEntrySerializer(_HttpStrictSerializer):
    id = serializers.UUIDField(required=False, allow_null=True)
    name = serializers.CharField(required=False, allow_blank=True, max_length=2000)
    target_type = serializers.ChoiceField(choices=["http"])
    http = HttpExperimentSettingsSerializer()
    variable_mapping = serializers.DictField(
        child=JsonValueField(), required=False, default=dict
    )

    def validate_variable_mapping(self, value):
        from uuid import UUID

        from model_hub.services.http_experiment_source import path_tokens

        for name, binding in value.items():
            if not name.strip() or name != name.strip():
                raise serializers.ValidationError(
                    "Mapping names must be non-empty and trimmed."
                )
            if isinstance(binding, str) and binding:
                column, separator, path = binding.partition("::")
                try:
                    UUID(column)
                    if separator:
                        path_tokens(path)
                except (ValueError, SyntaxError) as exc:
                    raise serializers.ValidationError(
                        "Map to a column UUID with an optional key/index path."
                    ) from exc
            elif (
                isinstance(binding, dict)
                and binding.get("kind") == "empty"
                and set(binding) == {"kind"}
            ):
                continue
            elif (
                isinstance(binding, dict)
                and binding.get("kind") == "constant"
                and set(binding) <= {"kind", "value"}
                and isinstance(binding.get("value", ""), str)
            ):
                continue
            else:
                raise serializers.ValidationError(
                    "Use a column reference, a text constant, or an explicit empty value."
                )
        return value


class HttpExperimentConfigOutputSerializer(HttpExperimentEntrySerializer):
    order = serializers.IntegerField(read_only=True)


def _a2a_jsonrpc_url(card):
    """The card's JSON-RPC URL: `url` when its preferred transport is JSON-RPC
    (the A2A 0.3 default), else a JSONRPC entry of additionalInterfaces."""
    transport = str(card.get("preferredTransport") or "JSONRPC").upper()
    if (
        transport == "JSONRPC"
        and isinstance(card.get("url"), str)
        and card["url"].strip()
    ):
        return card["url"].strip()
    for interface in card.get("additionalInterfaces") or []:
        if (
            isinstance(interface, dict)
            and str(interface.get("transport") or "").upper() == "JSONRPC"
            and isinstance(interface.get("url"), str)
            and interface["url"].strip()
        ):
            return interface["url"].strip()
    raise serializers.ValidationError(
        {
            "card": (
                "This agent card names no JSON-RPC URL. Future AGI sends A2A message/send over JSON-RPC: "
                "use an agent that offers it."
            )
        }
    )


def _a2a_auth_headers(auth):
    """Header settings for the card's auth (a2aAuthModel.authOf). Secret names only."""
    if not auth:
        return []
    if not isinstance(auth, dict):
        raise serializers.ValidationError("Choose an auth from the list.")
    kind, secret, header = (
        auth.get("kind"),
        str(auth.get("secret") or "").strip(),
        auth.get("header"),
    )
    if not kind:
        # The older {name, secret} pair: Authorization reads as Bearer, any other header as an API key.
        name = str(auth.get("name") or "").strip()
        if not secret:
            return []
        kind, header = (
            ("bearer", None)
            if not name or name.lower() == "authorization"
            else ("api_key", name)
        )
    if kind == "none":
        return []
    if kind in ("basic", "oauth2"):
        raise serializers.ValidationError(
            "Basic and OAuth 2 sign-in for A2A agents aren't available in this release. "
            "Choose Bearer token, API key header or None."
        )
    if kind not in ("bearer", "api_key"):
        raise serializers.ValidationError("Choose an auth from the list.")
    if not secret:
        raise serializers.ValidationError("Pick the workspace secret this auth sends.")
    if kind == "bearer":
        return [{"name": "Authorization", "secret_ref": secret, "scheme": "bearer"}]
    return [{"name": str(header or "").strip() or "X-Api-Key", "secret_ref": secret}]


class A2aExperimentSettingsSerializer(_HttpStrictSerializer):
    """What the A2A agent card form sends (experimentFormValues r2RunPayload).

    Saved as an HTTP source with http.protocol = "a2a". Only the card's JSON-RPC
    URL, the text column and the auth's secret name are kept, never a secret
    value. The skill is shown to the person; message/send has no skill field.
    """

    card_url = serializers.CharField(required=False, allow_blank=True, max_length=4096)
    card = serializers.DictField(
        child=JsonValueField(),
        error_messages={"required": "Discover the agent card, then run."},
        help_text="The agent card the form discovered. Its JSON-RPC URL is where each row's message goes.",
    )
    skill_id = serializers.CharField(
        required=False, allow_null=True, allow_blank=True, max_length=255
    )
    text_column = serializers.UUIDField(
        error_messages={
            "required": "Pick the column the message text comes from.",
            "null": "Pick the column the message text comes from.",
        },
    )
    data_columns = serializers.ListField(
        child=serializers.UUIDField(), required=False, max_length=64
    )
    context_column = serializers.UUIDField(required=False, allow_null=True)
    auth = serializers.DictField(
        child=JsonValueField(), required=False, allow_null=True
    )

    def validate(self, data):
        if data.get("data_columns"):
            raise serializers.ValidationError(
                {
                    "data_columns": (
                        "Sending columns as a data part isn't available in this release. "
                        "Clear Data part (JSON) from, then run."
                    )
                }
            )
        if data.get("context_column"):
            raise serializers.ValidationError(
                {
                    "context_column": (
                        "Continuing one A2A conversation across rows isn't available in this release. "
                        "Clear the context column, then run."
                    )
                }
            )
        url = _a2a_jsonrpc_url(data["card"])
        try:
            HttpExperimentSettingsSerializer().validate_url(url)
        except serializers.ValidationError as exc:
            raise serializers.ValidationError(
                {
                    "card": (
                        "The agent card's URL is not a public HTTP(S) URL. Check the card, then Discover it again."
                    )
                }
            ) from exc
        try:
            headers = _a2a_auth_headers(data.get("auth"))
        except serializers.ValidationError as exc:
            raise serializers.ValidationError({"auth": exc.detail}) from exc
        return {**data, "agent_url": url, "headers": headers}


class A2aExperimentEntrySerializer(_HttpStrictSerializer):
    id = serializers.UUIDField(required=False, allow_null=True)
    name = serializers.CharField(required=False, allow_blank=True, max_length=2000)
    target_type = serializers.ChoiceField(choices=["a2a"])
    a2a = A2aExperimentSettingsSerializer()

    @staticmethod
    def http_entry(data):
        """The HTTP source entry an A2A entry is saved and run as."""
        from model_hub.services.http_experiment_source import (
            A2A_BODY_TEMPLATE,
            A2A_TEXT_VARIABLE,
        )

        settings = data["a2a"]
        entry = {
            "target_type": "http",
            "http": {
                "protocol": "a2a",
                "url": settings["agent_url"],
                "method": "POST",
                "headers": settings["headers"],
                "body_template": A2A_BODY_TEMPLATE,
                "output_path": "",
                "streaming": False,
                "audio": "text",
            },
            "variable_mapping": {A2A_TEXT_VARIABLE: str(settings["text_column"])},
        }
        if data.get("id") is not None:
            entry["id"] = str(data["id"])
        if "name" in data:
            entry["name"] = data["name"]
        return entry


class PromptConfigEntrySerializer(serializers.Serializer):
    """
    Validates a single entry in the prompt_config array.
    Each entry is a prompt config, an agent config, an HTTP source, or an A2A
    agent. HTTP sources have their own settings and never require prompt/model
    IDs. An A2A entry is validated, then becomes an HTTP source entry with
    http.protocol = "a2a", so it is saved and run by the HTTP source path.
    On update, `id` identifies an existing EPC/EAC record. HTTP readback uses
    the source EDT ID; its create/update dispatch is handled by the view owner.
    """

    id = serializers.UUIDField(required=False, allow_null=True)
    name = serializers.CharField(required=False, allow_blank=True)

    # Declared here as well as on the discriminated serializer so generated
    # contracts retain HTTP fields before the request reaches this serializer.
    target_type = serializers.ChoiceField(
        choices=["prompt", "agent", "http", "a2a"], required=False
    )
    http = HttpExperimentSettingsSerializer(required=False)
    variable_mapping = serializers.DictField(child=JsonValueField(), required=False)
    a2a = A2aExperimentSettingsSerializer(required=False)

    def to_internal_value(self, data):
        if isinstance(data, dict) and data.get("target_type") == "a2a":
            a2a = A2aExperimentEntrySerializer(context=self.context).run_validation(
                data
            )
            return HttpExperimentEntrySerializer(context=self.context).run_validation(
                A2aExperimentEntrySerializer.http_entry(a2a)
            )
        if isinstance(data, dict) and data.get("target_type") == "http":
            # Do not inject prompt/model defaults into an HTTP-only entry.
            return HttpExperimentEntrySerializer(context=self.context).run_validation(
                data
            )
        return super().to_internal_value(data)

    # Prompt identifiers (LLM experiments)
    prompt_id = serializers.UUIDField(required=False, allow_null=True)
    prompt_version = serializers.UUIDField(required=False, allow_null=True)

    # Agent identifiers (LLM experiments only)
    agent_id = serializers.UUIDField(required=False, allow_null=True)
    agent_version = serializers.UUIDField(required=False, allow_null=True)

    # Model config (prompt entries only — plain model name string or ModelSpec dict)
    model = StringOrObjectField(required=False, default=None)
    # Typed known keys + escape hatch for provider-specific extras
    model_params = PromptModelParamsSerializer(required=False, default=dict)
    configuration = PromptConfigurationSerializer(required=False, default=dict)
    output_format = serializers.CharField(required=False, default="string")

    # Inline messages (tts/stt/image experiments)
    messages = serializers.ListField(
        child=MessageItemSerializer(),
        required=False,
    )

    # STT-specific
    voice_input_column_id = serializers.UUIDField(required=False, allow_null=True)

    def validate_model(self, value):
        if value is not None and not isinstance(value, (str, dict)):
            raise serializers.ValidationError(
                "model must be a string or a ModelSpec dict."
            )
        return value

    def validate(self, data):
        if data.get("target_type") == "http":
            return data
        if "http" in data or "variable_mapping" in data:
            raise serializers.ValidationError(
                "HTTP settings and mapping require target_type=http."
            )
        if "a2a" in data:
            raise serializers.ValidationError(
                "A2A agent settings require target_type=a2a."
            )
        has_prompt = bool(data.get("prompt_id")) or bool(data.get("prompt_version"))
        has_agent = bool(data.get("agent_id")) or bool(data.get("agent_version"))

        if has_prompt and has_agent:
            raise serializers.ValidationError(
                "Each entry must be either a prompt config or an agent config, not both."
            )
        if has_agent and data.get("messages"):
            raise serializers.ValidationError("Agent entries cannot have messages.")

        if has_agent:
            if not data.get("agent_id") or not data.get("agent_version"):
                raise serializers.ValidationError(
                    "Agent entries require both agent_id and agent_version."
                )
            if data.get("model"):
                raise serializers.ValidationError("Agent entries cannot have a model.")
        elif not has_agent:
            # Prompt entry — model is required
            if not data.get("model"):
                raise serializers.ValidationError("Prompt entries require a model.")

        return data


class ExperimentCreateV2Serializer(
    ReservedRunMetadataInputMixin, serializers.Serializer
):
    """
    V2 experiment creation serializer.
    Accepts the unified prompt_config array where entries can be either
    prompt configs or agent configs (agents only for experiment_type=llm).

    Each prompt entry specifies a single model. Multiple models require
    multiple entries in the prompt_config array. Each entry maps 1:1 to
    an ExperimentPromptConfig record.
    """

    name = ExperimentNameField()
    description = ExperimentDescriptionField(required=False)
    dataset_id = serializers.UUIDField()
    column_id = serializers.UUIDField(required=False, allow_null=True)
    experiment_type = serializers.ChoiceField(
        choices=["llm", "tts", "stt", "image"],
        default="llm",
    )
    prompt_config = PromptConfigEntrySerializer(many=True)
    user_eval_metrics = EvalMetricEntrySerializer(many=True)
    row_ids = serializers.ListField(
        child=serializers.UUIDField(),
        required=False,
        allow_empty=False,
        max_length=100000,
        help_text=(
            "Experiment run settings are on by default. When a server turns them off, "
            "a create request that sends this field is refused (HTTP 400)."
        ),
    )
    split_ids = serializers.ListField(
        child=serializers.UUIDField(),
        required=False,
        allow_empty=False,
        max_length=100,
        help_text="Union of live dataset splits, intersected with row_ids before sampling.",
    )
    sample = ExperimentSampleSerializer(
        required=False,
        help_text=(
            "Experiment run settings are on by default. When a server turns them off, "
            "a create request that sends this field is refused (HTTP 400) and "
            "experiment detail responses leave it out."
        ),
    )
    metadata = ExperimentMetadataField(
        required=False,
        help_text=(
            "Experiment run settings are on by default. When a server turns them off, "
            "only {} is accepted for this field, as a no-op; any other value is "
            "refused (HTTP 400)."
        ),
    )
    error_policy = serializers.ChoiceField(
        choices=["zero", "exclude"],
        required=False,
        help_text=(
            "How a row whose model call failed is scored. Omitted, the row gets no "
            'score and stays out of averages; "zero" scores it 0; "exclude" marks it '
            "left out of scores. Experiment run settings are on by default. When a "
            "server turns them off, a create request that sends this field is "
            "refused (HTTP 400)."
        ),
    )
    limits = ExperimentLimitsSerializer(
        required=False,
        help_text=(
            "Per-row limits. This release accepts only timeout_s, which is checked "
            "before and after each model call, not during one; tokens, messages and "
            "cost_usd are refused (HTTP 400). Experiment run settings are on by "
            "default. When a server turns them off, a create request that sends "
            "this field is refused (HTTP 400) and experiment detail responses leave "
            "it out."
        ),
    )

    def get_fields(self):
        fields = without_scope_fields(super().get_fields())
        if not scope_enabled():
            fields["split_ids"].help_text = "Union of live dataset splits."
        return fields

    def allows_unknown_field(self, field):
        # Off, run settings are not fields. validate() refuses a request that
        # uses one with a sentence a person can act on, not "Unknown field.".
        return not scope_enabled() and field in SCOPE_FIELDS

    def validate_user_eval_metrics(self, value):
        if not value:
            raise serializers.ValidationError(
                "At least one evaluation metric is required."
            )
        return value

    def validate_prompt_config(self, value):
        if not value:
            raise serializers.ValidationError(
                "At least one prompt config entry is required."
            )
        return value

    def validate(self, data):
        if not scope_enabled():
            sent = getattr(self, "initial_data", {})
            used = [
                field
                for field in SCOPE_FIELDS
                # Older basic wizard bodies include metadata={}: a no-op.
                if field in sent and not (field == "metadata" and sent[field] == {})
            ]
            if used:
                raise serializers.ValidationError(
                    {
                        "non_field_errors": [SCOPE_OFF_MESSAGE],
                        **{field: [SCOPE_OFF_FIELD_MESSAGE] for field in used},
                    }
                )
            if sent.get("metadata") == {}:
                # Submission identity hashes validated_data. Keep an explicit legacy
                # metadata={} identical across ON -> OFF, but leave omission alone.
                data["metadata"] = {}
        # Limits this release does not apply are refused with the wizard's words.
        if any(key in (data.get("limits") or {}) for key in UNAVAILABLE_ROW_LIMITS):
            raise serializers.ValidationError(
                {
                    "non_field_errors": [UNAVAILABLE_ROW_LIMITS_MESSAGE],
                    "limits": [UNAVAILABLE_ROW_LIMITS_MESSAGE],
                }
            )

        experiment_type = data.get("experiment_type", "llm")
        if data.get("limits") and experiment_type != "llm":
            raise serializers.ValidationError(
                {"limits": "Per-row limits require an LLM experiment."}
            )

        for i, entry in enumerate(data.get("prompt_config", [])):
            if entry.get("target_type") == "http":
                if experiment_type != "llm":
                    raise serializers.ValidationError(
                        {
                            "prompt_config": f"HTTP entries require a text experiment (entry {i})."
                        }
                    )
                if data.get("limits"):
                    raise serializers.ValidationError(
                        {
                            "limits": "HTTP sources use timeout_seconds; model token limits do not apply."
                        }
                    )
                continue
            is_agent = bool(entry.get("agent_id")) and bool(entry.get("agent_version"))

            if is_agent:
                # Agents are only allowed in LLM experiments
                if experiment_type != "llm":
                    raise serializers.ValidationError(
                        {
                            "prompt_config": (
                                "Agent configs are only allowed in LLM "
                                f"experiments (entry {i})."
                            )
                        }
                    )
            else:
                # Prompt entry validation based on experiment type
                if experiment_type == "llm":
                    if not entry.get("prompt_id") or not entry.get("prompt_version"):
                        raise serializers.ValidationError(
                            {
                                "prompt_config": (
                                    "LLM prompt entries require prompt_id "
                                    f"and prompt_version (entry {i})."
                                )
                            }
                        )
                else:
                    # tts/stt/image — require inline messages, no prompt refs
                    if not entry.get("messages"):
                        raise serializers.ValidationError(
                            {
                                "prompt_config": (
                                    f"{experiment_type.upper()} entries "
                                    f"require messages (entry {i})."
                                )
                            }
                        )
                    if entry.get("prompt_id") or entry.get("prompt_version"):
                        raise serializers.ValidationError(
                            {
                                "prompt_config": (
                                    f"{experiment_type.upper()} entries must "
                                    f"not have prompt_id/prompt_version (entry {i})."
                                )
                            }
                        )

                # STT requires voice_input_column_id on prompt entries
                if experiment_type == "stt" and not is_agent:
                    if not entry.get("voice_input_column_id"):
                        raise serializers.ValidationError(
                            {
                                "prompt_config": (
                                    "STT entries require "
                                    f"voice_input_column_id (entry {i})."
                                )
                            }
                        )

        return data


# ---- V2 Serializers (Output) ----


class ExperimentPromptConfigOutputSerializer(serializers.ModelSerializer):
    prompt_id = serializers.UUIDField(
        source="prompt_template_id", read_only=True, allow_null=True
    )
    prompt_version = serializers.UUIDField(
        source="prompt_version_id", read_only=True, allow_null=True
    )
    prompt_name = serializers.SerializerMethodField()
    prompt_version_name = serializers.SerializerMethodField()
    voice_input_column_id = serializers.UUIDField(read_only=True, allow_null=True)

    class Meta:
        model = ExperimentPromptConfig
        fields = [
            "id",
            "name",
            "prompt_id",
            "prompt_name",
            "prompt_version",
            "prompt_version_name",
            "model",
            "model_display_name",
            "model_config",
            "model_params",
            "configuration",
            "output_format",
            "messages",
            "voice_input_column_id",
            "order",
        ]

    def get_prompt_name(self, obj):
        return obj.prompt_template.name if obj.prompt_template_id else None

    def get_prompt_version_name(self, obj):
        return obj.prompt_version.template_version if obj.prompt_version_id else None

    def to_representation(self, instance):
        data = super().to_representation(instance)
        model_name = instance.model
        model_params = instance.model_params or {}

        if not model_params.get("logo_url") and not model_params.get("providers"):
            provider = model_params.get("provider") or model_params.get("providers")

            if not provider:
                try:
                    # Use context if available, otherwise fall back to FK chain
                    organization_id = self.context.get("organization_id")
                    if not organization_id:
                        organization_id = (
                            instance.experiment_dataset.experiment.user.organization.id
                        )

                    model_manager = LiteLLMModelManager(
                        model_name=model_name,
                        organization_id=organization_id,
                    )
                    matched = next(
                        (
                            m
                            for m in model_manager.models
                            if model_name.lower() == m["model_name"].lower()
                        ),
                        None,
                    )
                    if matched:
                        provider = matched.get("providers")
                except Exception:
                    pass

            if provider:
                try:
                    data["logo_url"] = ProviderLogoUrls.get_url_by_provider(provider)
                    data["providers"] = provider
                except Exception:
                    pass

        # Convert column UUIDs to names in messages for display
        snapshot_dataset_id = self.context.get("snapshot_dataset_id")
        if data.get("messages") and snapshot_dataset_id:
            from model_hub.views.run_prompt import convert_uuids_to_column_names

            data["messages"] = convert_uuids_to_column_names(
                data["messages"], snapshot_dataset_id
            )

        return data


class ExperimentAgentConfigOutputSerializer(serializers.ModelSerializer):
    agent_id = serializers.UUIDField(source="graph_id", read_only=True)
    agent_version = serializers.UUIDField(source="graph_version_id", read_only=True)
    agent_name = serializers.SerializerMethodField()
    agent_version_name = serializers.SerializerMethodField()

    class Meta:
        model = ExperimentAgentConfig
        fields = [
            "id",
            "name",
            "agent_id",
            "agent_name",
            "agent_version",
            "agent_version_name",
            "order",
        ]

    def get_agent_name(self, obj):
        return obj.graph.name if obj.graph_id else None

    def get_agent_version_name(self, obj):
        return obj.graph_version.version_number if obj.graph_version_id else None


class ExperimentDetailRunQuerySerializer(serializers.Serializer):
    """The inline catalog is bounded; continuation uses dataset runs + experiment_id.

    No detail paging controls are advertised. validated_request rejects unknown
    fields, so a caller cannot mistake an ignored offset for a different page.
    """


class ExperimentCanonicalRunSerializer(serializers.Serializer):
    """The public header; no raw execution or evaluator config."""

    run_id = serializers.UUIDField()
    dataset_id = serializers.UUIDField()
    experiment_id = serializers.UUIDField(allow_null=True)
    task_kind = serializers.CharField()
    label = serializers.CharField(allow_null=True)
    name = serializers.CharField(allow_null=True)
    status = serializers.CharField()
    ordinal = serializers.IntegerField(allow_null=True)
    dataset_version = serializers.DictField()
    dataset_version_id = serializers.UUIDField()
    source = serializers.CharField()
    native_variant_id = serializers.UUIDField(allow_null=True)
    native_config_id = serializers.UUIDField(allow_null=True)
    output_column_id = serializers.UUIDField(allow_null=True)
    execution_id = serializers.UUIDField(allow_null=True)
    prompt_version_id = serializers.UUIDField(allow_null=True)
    agent_version_id = serializers.UUIDField(allow_null=True)
    trials = serializers.IntegerField(allow_null=True)
    concurrency = serializers.IntegerField(allow_null=True)
    trial_reducer = serializers.JSONField(allow_null=True)
    error_policy = serializers.CharField(allow_null=True)
    requested_settings = serializers.DictField()
    effective_settings = serializers.DictField()
    config = serializers.DictField()
    eval_versions = serializers.ListField(child=serializers.DictField())
    counts = serializers.DictField(child=serializers.IntegerField())
    examples_total = serializers.IntegerField()
    attempts = serializers.IntegerField()
    progress = serializers.DictField(child=serializers.IntegerField())
    cost_usd = serializers.FloatField(allow_null=True)
    latency_ms = serializers.FloatField(allow_null=True)
    parent_run_id = serializers.UUIDField(allow_null=True)
    iteration = serializers.IntegerField(allow_null=True)
    kind = serializers.CharField()
    listed = serializers.BooleanField()
    non_final = serializers.BooleanField()
    comparable = serializers.BooleanField()
    availability = serializers.DictField(child=serializers.CharField(allow_null=True))
    classic_url = serializers.CharField(allow_null=True)
    started_at = serializers.DateTimeField(allow_null=True)
    completed_at = serializers.DateTimeField(allow_null=True)


OPENS_IN_FULL_RESULTS_HELP = (
    "True when the runs view can show the whole experiment: it has a "
    "NativeExperimentSnapshot and every live variant has at least one counted "
    "run. False otherwise: the experiment opens in the original results view, "
    "where all of its variants show (it reads their rows and stats). This "
    "includes experiments created before runs were recorded, and experiments "
    "with a variant that has no counted run: one that calls an HTTP endpoint, "
    "one run with an unversioned prompt, or one added by a classic rerun or "
    "append. On the detail it is null in a create, update or append response "
    "when it could not be read."
)


def with_full_results(queryset):
    """Annotate the two facts ``opens_in_full_results`` reads, in the same query.

    The one rule for both the experiments list and the experiment detail: an
    experiment opens in the runs view only when the runs view can show all of
    it. That needs a NativeExperimentSnapshot and, for every live variant
    (ExperimentDatasetTable, not deleted), at least one counted run bound to
    that variant (NativeRunBinding). A counted run is what the detail's
    runs_total counts (results_catalog.live_runs and list_results_runs): live,
    not a quick test, on a live version of the experiment's own dataset, in
    that dataset's organization and workspace. The classic tables hold every
    variant, so any other experiment opens in the original results view and is
    never shown in part.

    Two bare Exists annotations: on a grouped list query Django groups a
    subquery by its outer columns (here the experiment primary key, already
    grouped), never by the subquery itself.
    """
    from django.db.models import Exists, F, OuterRef, Q

    from model_hub.models.native_experiment_runs import (
        NativeExperimentSnapshot,
        NativeRunBinding,
    )

    # live_runs filters workspace_id=dataset.workspace_id, which matches a null
    # version workspace when the dataset's workspace is null.
    workspace_matches = Q(
        run__dataset_version__workspace_id=F("run__experiment__dataset__workspace_id")
    )
    both_workspaces_null = Q(
        run__dataset_version__workspace__isnull=True,
        run__experiment__dataset__workspace__isnull=True,
    )
    quick_test = Q(run__metadata__kind="quick_test")
    not_quick_test = Q(run__metadata__kind__isnull=True) | ~quick_test
    # Relative to one variant (the outer row): a counted run bound to it.
    counted_run_of_variant = NativeRunBinding.objects.filter(
        workspace_matches | both_workspaces_null,
        not_quick_test,
        variant_id=OuterRef("pk"),
        run__experiment_id=OuterRef("experiment_id"),
        run__deleted=False,
        run__dataset_version__deleted=False,
        run__dataset_version__dataset_id=F("run__experiment__dataset_id"),
        run__dataset_version__organization_id=F(
            "run__experiment__dataset__organization_id"
        ),
    )
    variant_missing_from_runs = ExperimentDatasetTable.no_workspace_objects.filter(
        ~Exists(counted_run_of_variant),
        experiment_id=OuterRef("pk"),
    )
    snapshot = NativeExperimentSnapshot.objects.filter(experiment_id=OuterRef("pk"))
    return queryset.annotate(
        full_results_snapshot=Exists(snapshot),
        full_results_variant_missing=Exists(variant_missing_from_runs),
    )


def opens_in_full_results(experiment):
    """The rule for one experiment, annotated by with_full_results or read by pk."""
    facts = ("full_results_snapshot", "full_results_variant_missing")
    if all(hasattr(experiment, fact) for fact in facts):
        values = {fact: getattr(experiment, fact) for fact in facts}
    else:
        one = ExperimentsTable._base_manager.filter(pk=experiment.pk)
        values = with_full_results(one).values(*facts).get()
    has_snapshot = bool(values["full_results_snapshot"])
    return has_snapshot and not values["full_results_variant_missing"]


class ExperimentDetailV2Serializer(serializers.ModelSerializer):
    runs = serializers.SerializerMethodField(allow_null=True)
    runs_total = serializers.SerializerMethodField(allow_null=True)
    runs_has_more = serializers.SerializerMethodField(allow_null=True)
    runs_next = serializers.SerializerMethodField(allow_null=True)
    runs_availability = serializers.SerializerMethodField()
    results_availability = serializers.SerializerMethodField()
    opens_in_full_results = serializers.SerializerMethodField()
    prompt_configs = serializers.SerializerMethodField()
    agent_configs = serializers.SerializerMethodField()
    http_configs = serializers.SerializerMethodField()
    user_eval_metrics = serializers.SerializerMethodField()
    dataset_id = serializers.UUIDField(read_only=True)
    column_id = serializers.UUIDField(read_only=True, allow_null=True)
    snapshot_dataset_id = serializers.UUIDField(read_only=True, allow_null=True)
    run_scope = serializers.JSONField(read_only=True)
    row_ids = serializers.ListField(
        child=serializers.UUIDField(),
        source="run_scope.row_ids",
        read_only=True,
        allow_null=True,
        default=None,
    )
    sample = ExperimentSampleSerializer(
        source="run_scope.sample", read_only=True, allow_null=True, default=None
    )
    metadata = ExperimentMetadataField(source="run_metadata", read_only=True)
    error_policy = serializers.ChoiceField(
        choices=["zero", "exclude"], source="run_policy.error_policy", read_only=True
    )
    limits = ExperimentLimitsSerializer(source="run_policy.limits", read_only=True)

    class Meta:
        model = ExperimentsTable
        fields = [
            "id",
            "name",
            "description",
            "dataset_id",
            "column_id",
            "experiment_type",
            "status",
            "snapshot_dataset_id",
            "run_scope",
            "row_ids",
            "sample",
            "metadata",
            "error_policy",
            "limits",
            "prompt_configs",
            "agent_configs",
            "http_configs",
            "user_eval_metrics",
            "created_at",
            "runs",
            "runs_total",
            "runs_has_more",
            "runs_next",
            "runs_availability",
            "results_availability",
            "opens_in_full_results",
        ]

    def _results_runs(self, instance):
        """Delegate the wire contract without relaxing prior scope checks."""
        from django.db import DatabaseError, transaction

        from model_hub.services.results_catalog import detail_results_projection

        request = self.context.get("request")
        if request is None:
            raise NotFound("Experiment not found")
        cache = getattr(self, "_results_runs_cache", {})
        if instance.pk in cache:
            return cache[instance.pk]
        dataset = (
            scoped_dataset_queryset(request).filter(pk=instance.dataset_id).first()
        )
        if dataset is None or instance.deleted:
            raise NotFound("Experiment not found")
        try:
            # Isolate read failures with a savepoint inside a classic PUT/append.
            with transaction.atomic():
                all_runs = ExperimentRun.no_workspace_objects.filter(
                    experiment=instance, deleted=False
                )
                visible = all_runs.filter(
                    dataset_version__dataset=dataset,
                    dataset_version__deleted=False,
                    dataset_version__organization_id=dataset.organization_id,
                    dataset_version__workspace_id=dataset.workspace_id,
                )
                if all_runs.exclude(pk__in=visible.values("pk")).exists():
                    raise NotFound("Experiment results not found")
                result = detail_results_projection(request, instance)
        except DatabaseError:
            # A read-only optional projection cannot negate committed admission.
            # GET remains non-2xx; never convert unavailable evidence to [].
            if request.method not in {"POST", "PUT", "PATCH"}:
                raise
            result = {
                "runs": None,
                "runs_total": None,
                "runs_has_more": None,
                "runs_next": None,
                "runs_availability": {
                    "available": False,
                    "status": 503,
                    "reason": "Results temporarily unavailable",
                },
            }
        cache[instance.pk] = result
        self._results_runs_cache = cache
        return result

    @swagger_serializer_method(
        serializer_or_field=CanonicalRunSerializer(many=True, allow_null=True)
    )
    def get_runs(self, instance):
        return self._results_runs(instance)["runs"]

    @swagger_serializer_method(
        serializer_or_field=serializers.IntegerField(allow_null=True)
    )
    def get_runs_total(self, instance):
        return self._results_runs(instance)["runs_total"]

    @swagger_serializer_method(
        serializer_or_field=serializers.BooleanField(allow_null=True)
    )
    def get_runs_has_more(self, instance):
        return self._results_runs(instance)["runs_has_more"]

    @swagger_serializer_method(
        serializer_or_field=serializers.IntegerField(allow_null=True)
    )
    def get_runs_next(self, instance):
        return self._results_runs(instance)["runs_next"]

    @swagger_serializer_method(serializer_or_field=serializers.DictField())
    def get_runs_availability(self, instance):
        return self._results_runs(instance)["runs_availability"]

    @swagger_serializer_method(serializer_or_field=serializers.DictField())
    def get_results_availability(self, instance):
        from model_hub.models.native_experiment_runs import NativeExperimentSnapshot

        result = self._results_runs(instance)
        reason = result["runs_availability"].get("reason")
        if result["runs_total"] == 0:
            reason = (
                "native_runs_not_recorded"
                if NativeExperimentSnapshot.objects.filter(experiment=instance).exists()
                else "native_identity_not_recorded"
            )
        return {
            "reason": reason,
            "classic_url": f"/dashboard/develop/experiment/{instance.id}/data",
        }

    @swagger_serializer_method(
        serializer_or_field=serializers.BooleanField(
            help_text=OPENS_IN_FULL_RESULTS_HELP, allow_null=True
        )
    )
    def get_opens_in_full_results(self, instance):
        # Beside results_availability, which keeps its reasons unchanged.
        from django.db import DatabaseError, transaction

        request = self.context.get("request")
        if request is None or request.method not in {"POST", "PUT", "PATCH"}:
            return opens_in_full_results(instance)
        try:
            # A savepoint, as _results_runs: a failed optional read must not
            # turn a committed create, update or append into an error.
            with transaction.atomic():
                return opens_in_full_results(instance)
        except DatabaseError:
            return None

    def get_fields(self):
        fields = without_scope_fields(super().get_fields())
        if not scope_enabled():
            # Preserve the readback without exposing it through the JSON blob.
            fields["run_scope"] = ExperimentSplitRunScopeSerializer(read_only=True)
        return fields

    def to_representation(self, instance):
        data = super().to_representation(instance)
        # Optional selection settings are absent for full-dataset runs.
        for field in ("row_ids", "sample"):
            if data.get(field) is None:
                data.pop(field, None)
        if not scope_enabled() and not data.get("run_scope", {}).get("split_ids"):
            data.pop("run_scope", None)
        return data

    def get_prompt_configs(self, obj):
        # Collect EPCs from prefetched experiment_datasets
        epcs = []
        for edt in obj.experiment_datasets.all():
            try:
                epcs.append(edt.prompt_config)
            except ExperimentPromptConfig.DoesNotExist:
                continue
        epcs.sort(key=lambda x: x.order)
        ctx = {
            **self.context,
            "snapshot_dataset_id": (
                str(obj.snapshot_dataset_id) if obj.snapshot_dataset_id else None
            ),
        }
        return ExperimentPromptConfigOutputSerializer(epcs, many=True, context=ctx).data

    @swagger_serializer_method(
        serializer_or_field=HttpExperimentConfigOutputSerializer(many=True)
    )
    def get_http_configs(self, obj):
        from model_hub.services.http_experiment_source import public_http_source

        sources = [
            public_http_source(edt)
            for edt in obj.experiment_datasets.all()
            if not edt.deleted
            and isinstance(edt.legacy_prompt_config, dict)
            and edt.legacy_prompt_config.get("target_type") == "http"
        ]
        return HttpExperimentConfigOutputSerializer(
            sorted(sources, key=lambda item: item["order"]), many=True
        ).data

    def get_agent_configs(self, obj):
        # Collect EACs from prefetched experiment_datasets
        eacs = []
        for edt in obj.experiment_datasets.all():
            try:
                eacs.append(edt.agent_config)
            except ExperimentAgentConfig.DoesNotExist:
                continue
        eacs.sort(key=lambda x: x.order)
        return ExperimentAgentConfigOutputSerializer(
            eacs, many=True, context=self.context
        ).data

    def get_user_eval_metrics(self, obj):
        # Single query: M2M JOIN + select_related for template FK
        metrics = obj.user_eval_template_ids.select_related(
            "template", "pinned_version"
        ).all()
        return [
            {
                "id": str(m.id),
                "template_id": str(m.template_id),
                "template_details": (
                    {
                        "id": str(m.template.id),
                        "name": m.template.name,
                        "description": m.template.description,
                        "config": m.template.config,
                        "criteria": m.template.criteria,
                        "type": (
                            "user_built"
                            if m.template.owner == "user"
                            else "futureagi_built"
                        ),
                    }
                    if m.template_id
                    else None
                ),
                "name": m.name,
                "config": m.config,
                "model": m.model,
                "error_localizer": m.error_localizer,
                "kb_id": str(m.kb_id) if m.kb_id else None,
                # The version the eval runs when pinned (null: the template's
                # default version at run time).
                "pinned_version_id": (
                    str(m.pinned_version_id) if m.pinned_version_id else None
                ),
                "pinned_version_number": (
                    m.pinned_version.version_number if m.pinned_version else None
                ),
            }
            for m in metrics
        ]


class ExperimentListV2Serializer(serializers.ModelSerializer):
    """V2 list serializer — counts come from structured config models."""

    eval_templates_count = serializers.SerializerMethodField()
    models_count = serializers.SerializerMethodField()
    agents_count = serializers.SerializerMethodField()
    opens_in_full_results = serializers.SerializerMethodField()

    class Meta:
        model = ExperimentsTable
        fields = [
            "id",
            "name",
            "description",
            "status",
            "experiment_type",
            "eval_templates_count",
            "created_at",
            "models_count",
            "agents_count",
            "dataset",
            "opens_in_full_results",
        ]

    @swagger_serializer_method(
        serializer_or_field=serializers.BooleanField(
            help_text=OPENS_IN_FULL_RESULTS_HELP
        )
    )
    def get_opens_in_full_results(self, obj):
        # The list view annotates it (with_full_results): no query per row.
        return opens_in_full_results(obj)

    def get_eval_templates_count(self, obj):
        return obj.user_eval_template_ids.count()

    def get_models_count(self, obj):
        if hasattr(obj, "models_count"):
            return obj.models_count
        return obj.experiment_datasets.filter(
            deleted=False, prompt_config__isnull=False
        ).count()

    def get_agents_count(self, obj):
        if hasattr(obj, "agents_count"):
            return obj.agents_count
        return obj.experiment_datasets.filter(
            deleted=False, agent_config__isnull=False
        ).count()


class ExperimentUpdateV2Serializer(
    ReservedRunMetadataInputMixin, serializers.Serializer
):
    """V2 experiment update serializer. All fields are optional (partial update).

    Send name and/or description alone to edit metadata without changing runs.
    Execution configuration (column_id, prompt_config, user_eval_metrics,
    append_only) must be submitted separately. Experiment type is immutable.
    """

    name = ExperimentNameField(required=False)
    description = ExperimentDescriptionField(required=False)
    column_id = serializers.UUIDField(required=False, allow_null=True)
    prompt_config = PromptConfigEntrySerializer(many=True, required=False)
    user_eval_metrics = EvalMetricEntrySerializer(many=True, required=False)
    append_only = serializers.BooleanField(required=False)
    preview = serializers.BooleanField(
        required=False,
        default=False,
        help_text="With append_only=true and row_ids, return an HTTP add-rows preview without saving or sending requests.",
    )
    row_ids = serializers.ListField(
        child=serializers.UUIDField(),
        required=False,
        allow_empty=True,
        max_length=10000,
        help_text="HTTP append only: candidate original source-dataset row IDs. Preview returns the exact new IDs to submit; an empty candidate list is allowed only for preview.",
    )

    def validate(self, data):
        from model_hub.services.experiment_append_variant import validate_append_payload

        # Judge by the keys the client sent: `preview` has a default, so the
        # validated data always carries it.
        sent = set(getattr(self, "initial_data", None) or data)
        if {"name", "description"}.intersection(sent) and sent - {
            "name",
            "description",
        }:
            raise serializers.ValidationError(
                "Save the name and description separately from run settings."
            )
        if data.get("preview"):
            if not data.get("append_only") or "row_ids" not in data:
                raise serializers.ValidationError(
                    "Preview requires append_only=true and row_ids."
                )
            if set(data) - {"append_only", "preview", "row_ids"}:
                raise serializers.ValidationError(
                    "Preview rows separately from source or evaluation edits."
                )
            return data
        # Preserve existing append validation and request hashes when not previewing.
        data.pop("preview", None)
        if "row_ids" in data and not data["row_ids"]:
            raise serializers.ValidationError("Select at least one row to append.")
        if "row_ids" in data and not data.get("append_only"):
            raise serializers.ValidationError("row_ids requires append_only=true.")
        if "row_ids" in data or any(
            entry.get("target_type") == "http"
            for entry in data.get("prompt_config", [])
        ):
            return data  # HTTP mutations have their own identity-preserving path.
        try:
            validate_append_payload(data)
        except ValueError as error:
            raise serializers.ValidationError(str(error)) from error
        return data


class HttpAppendRowsPreviewSerializer(serializers.Serializer):
    """Read-only HTTP add-rows preview payload, inside the normal result envelope."""

    preview = serializers.BooleanField()
    new_row_ids = serializers.ListField(child=serializers.UUIDField(), max_length=10000)
    request_count = serializers.IntegerField(min_value=0)
    held_count = serializers.IntegerField(min_value=0, max_value=10000)
    limit = serializers.IntegerField(min_value=10000, max_value=10000)


class ExperimentUpdateV2ResultSerializer(ExperimentDetailV2Serializer):
    """PUT returns experiment detail, or only the five preview fields when preview=true.

    Swagger 2 has no oneOf: document both branches as optional typed fields while
    keeping GET/POST detail contracts unchanged. The preview branch has no detail
    fields; it is serialized by HttpAppendRowsPreviewSerializer.
    """

    def get_fields(self):
        fields = super().get_fields()
        fields.update(HttpAppendRowsPreviewSerializer().get_fields())
        for field in fields.values():
            field.required = False
        return fields

    def to_representation(self, instance):
        if isinstance(instance, dict) and instance.get("preview") is True:
            return HttpAppendRowsPreviewSerializer(instance).data
        return super().to_representation(instance)


class ExperimentUpdateV2ResponseSerializer(serializers.Serializer):
    # The union-shaped result flattens these fields, so retain the standalone
    # preview type explicitly for generated clients and schema parity checks.
    swagger_retained_serializers = (HttpAppendRowsPreviewSerializer,)

    status = serializers.BooleanField()
    result = ExperimentUpdateV2ResultSerializer(
        help_text="Experiment detail, or {preview, new_row_ids, request_count, held_count, limit} for an HTTP add-rows preview.",
    )
