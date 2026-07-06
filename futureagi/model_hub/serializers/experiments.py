# serializers.py
from rest_framework import serializers
from tfc.utils.serializer_fields import (
    JsonValueField,
    StringOrArrayField,
    StringOrObjectField,
)

from agentic_eval.core_evals.run_prompt.litellm_models import LiteLLMModelManager
from model_hub.models.choices import ProviderLogoUrls
from model_hub.models.develop_dataset import Column, Dataset
from model_hub.models.evals_metric import UserEvalMetric
from model_hub.models.experiments import (
    ExperimentAgentConfig,
    ExperimentDatasetTable,
    ExperimentPromptConfig,
    ExperimentsTable,
)
from tfc.middleware.workspace_context import get_current_organization


class ExperimentsTableSerializer(serializers.ModelSerializer):
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
    """A single cell to rerun, identified by column + row."""

    column_id = serializers.UUIDField()
    row_id = serializers.UUIDField()


class ExperimentRerunCellsSerializer(serializers.Serializer):
    """Validates input for cell/column-level rerun.

    - cells: list of {column_id, row_id} — rerun specific cells.
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


class _ExtraFieldsMixin:
    """Pass unknown keys through to_internal_value unchanged.

    Provides the typed-keys-plus-escape-hatch pattern: declared fields are
    validated; any additional provider-specific key is accepted as-is.
    """

    def to_internal_value(self, data):
        validated = super().to_internal_value(data)
        for key, value in data.items():
            if key not in self.fields:
                validated[key] = value
        return validated


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


class PromptConfigEntrySerializer(serializers.Serializer):
    """
    Validates a single entry in the prompt_config array.
    Each entry is EITHER a prompt config (with prompt_id/prompt_version or messages)
    OR an agent config (with agent_id/agent_version, LLM only).
    On update, `id` identifies an existing EPC/EAC record.
    """

    id = serializers.UUIDField(required=False, allow_null=True)
    name = serializers.CharField(required=False, allow_blank=True)

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


class ExperimentCreateV2Serializer(serializers.Serializer):
    """
    V2 experiment creation serializer.
    Accepts the unified prompt_config array where entries can be either
    prompt configs or agent configs (agents only for experiment_type=llm).

    Each prompt entry specifies a single model. Multiple models require
    multiple entries in the prompt_config array. Each entry maps 1:1 to
    an ExperimentPromptConfig record.
    """

    name = serializers.CharField(max_length=255)
    dataset_id = serializers.UUIDField()
    column_id = serializers.UUIDField(required=False, allow_null=True)
    experiment_type = serializers.ChoiceField(
        choices=["llm", "tts", "stt", "image"],
        default="llm",
    )
    prompt_config = PromptConfigEntrySerializer(many=True)
    user_eval_metrics = EvalMetricEntrySerializer(many=True)

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
        experiment_type = data.get("experiment_type", "llm")

        for i, entry in enumerate(data.get("prompt_config", [])):
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


class ExperimentDetailV2Serializer(serializers.ModelSerializer):
    prompt_configs = serializers.SerializerMethodField()
    agent_configs = serializers.SerializerMethodField()
    user_eval_metrics = serializers.SerializerMethodField()
    dataset_id = serializers.UUIDField(read_only=True)
    column_id = serializers.UUIDField(read_only=True, allow_null=True)
    snapshot_dataset_id = serializers.UUIDField(read_only=True, allow_null=True)

    class Meta:
        model = ExperimentsTable
        fields = [
            "id",
            "name",
            "dataset_id",
            "column_id",
            "experiment_type",
            "status",
            "snapshot_dataset_id",
            "prompt_configs",
            "agent_configs",
            "user_eval_metrics",
            "created_at",
        ]

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
        metrics = obj.user_eval_template_ids.select_related("template").all()
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
            }
            for m in metrics
        ]


class ExperimentListV2Serializer(serializers.ModelSerializer):
    """V2 list serializer — counts come from structured config models."""

    eval_templates_count = serializers.SerializerMethodField()
    models_count = serializers.SerializerMethodField()
    agents_count = serializers.SerializerMethodField()

    class Meta:
        model = ExperimentsTable
        fields = [
            "id",
            "name",
            "status",
            "experiment_type",
            "eval_templates_count",
            "created_at",
            "models_count",
            "agents_count",
            "dataset",
        ]

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


class ExperimentUpdateV2Serializer(serializers.Serializer):
    """V2 experiment update serializer. All fields are optional (partial update).

    Editable: column_id, prompt_config, user_eval_metrics.
    NOT editable: name, experiment_type (set at creation time).
    """

    column_id = serializers.UUIDField(required=False, allow_null=True)
    prompt_config = PromptConfigEntrySerializer(many=True, required=False)
    user_eval_metrics = EvalMetricEntrySerializer(many=True, required=False)
