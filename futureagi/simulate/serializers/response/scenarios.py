from rest_framework import serializers

from model_hub.models.choices import StatusType
from simulate.models import Scenarios


class SimulatorAgentResponseSerializer(serializers.Serializer):
    """Nested serializer for the simulator agent object in scenario responses."""

    id = serializers.UUIDField(read_only=True)
    name = serializers.CharField(read_only=True)
    prompt = serializers.CharField(read_only=True, allow_null=True)
    voice_provider = serializers.CharField(read_only=True, allow_null=True)
    voice_name = serializers.CharField(read_only=True, allow_null=True)
    model = serializers.CharField(read_only=True, allow_null=True)
    initial_message = serializers.CharField(read_only=True, allow_null=True)


class PromptTemplateDetailResponseSerializer(serializers.Serializer):
    """Nested serializer for prompt template detail in scenario responses."""

    id = serializers.UUIDField(read_only=True)
    name = serializers.CharField(read_only=True)
    description = serializers.CharField(read_only=True, allow_null=True)
    variable_names = serializers.ListField(
        child=serializers.CharField(), read_only=True
    )


class PromptVersionDetailResponseSerializer(serializers.Serializer):
    """Nested serializer for prompt version detail in scenario responses."""

    id = serializers.UUIDField(read_only=True)
    template_version = serializers.IntegerField(read_only=True)
    is_default = serializers.BooleanField(read_only=True)
    commit_message = serializers.CharField(read_only=True, allow_null=True)


class ScenarioResponseSerializer(serializers.ModelSerializer):
    """Core response serializer for a Scenario — mirrors ScenariosSerializer exactly.

    Used for list endpoint responses and nested within create/edit responses.
    All field names match the current ScenariosSerializer to preserve frontend compatibility.
    """

    scenario_type_display = serializers.CharField(
        source="get_scenario_type_display", read_only=True
    )
    source_type_display = serializers.CharField(
        source="get_source_type_display", read_only=True
    )
    dataset_rows = serializers.SerializerMethodField()
    dataset_column_config = serializers.SerializerMethodField()
    graph = serializers.SerializerMethodField()
    agent = serializers.SerializerMethodField()
    agent_type = serializers.SerializerMethodField()
    prompt_template_detail = serializers.SerializerMethodField()
    prompt_version_detail = serializers.SerializerMethodField()

    class Meta:
        model = Scenarios
        fields = [
            "id",
            "name",
            "description",
            "source",
            "scenario_type",
            "scenario_type_display",
            "source_type",
            "source_type_display",
            "organization",
            "dataset",
            "dataset_rows",
            "dataset_column_config",
            "graph",
            "agent",
            "prompt_template",
            "prompt_template_detail",
            "prompt_version",
            "prompt_version_detail",
            "created_at",
            "updated_at",
            "deleted",
            "status",
            "deleted_at",
            "agent_type",
        ]
        read_only_fields = [
            "id",
            "created_at",
            "updated_at",
            "deleted",
            "deleted_at",
            "organization",
        ]

    def get_dataset_rows(self, obj):
        from model_hub.models.develop_dataset import Row

        if hasattr(obj, "_dataset_row_count") and obj._dataset_row_count is not None:
            return obj._dataset_row_count
        if obj.dataset:
            return Row.objects.filter(dataset=obj.dataset, deleted=False).count()
        return 0

    def get_dataset_column_config(self, obj):
        from model_hub.models.develop_dataset import Column

        if obj.dataset:
            column_order = obj.dataset.column_order
            columns = Column.objects.filter(deleted=False, id__in=column_order)
            column_config = {}
            for column in columns:
                column_config[f"{column.id}"] = {
                    "name": column.name,
                    "type": column.data_type,
                }
            return column_config
        return []

    def get_graph(self, obj):
        from simulate.models.scenario_graph import ScenarioGraph

        graph = (
            ScenarioGraph.objects.filter(scenario=obj, is_active=True)
            .order_by("-created_at")
            .first()
        )
        if graph and graph.graph_config:
            return graph.graph_config.get("graph_data", {})
        return {}

    def get_agent(self, obj):
        if obj.simulator_agent:
            return SimulatorAgentResponseSerializer(obj.simulator_agent).data
        return None

    def get_agent_type(self, obj):
        if obj.agent_definition:
            if obj.agent_definition.agent_type == "voice":
                return "inbound" if obj.agent_definition.inbound else "outbound"
            if obj.agent_definition.agent_type == "text":
                return "chat"
        if obj.prompt_version_id:
            return "prompt"
        return None

    def get_prompt_template_detail(self, obj):
        if obj.prompt_template:
            return PromptTemplateDetailResponseSerializer(obj.prompt_template).data
        return None

    def get_prompt_version_detail(self, obj):
        if obj.prompt_version:
            return PromptVersionDetailResponseSerializer(obj.prompt_version).data
        return None
    
    def validate_name(self, value):
        """Validate that name is not empty or just whitespace"""
        if not value.strip():
            raise serializers.ValidationError(
                "Name cannot be empty or just whitespace."
            )
        return value.strip()

    def validate_source(self, value):
        """Validate that source is not empty or just whitespace"""
        if not value.strip():
            raise serializers.ValidationError(
                "Source cannot be empty or just whitespace."
            )
        return value.strip()


class ScenarioPromptItemSerializer(serializers.Serializer):
    """Nested serializer for a single prompt item in ScenarioDetailResponseSerializer."""

    role = serializers.ChoiceField(
        choices=["system", "user", "assistant"], read_only=True
    )
    content = serializers.CharField(read_only=True)


class ScenarioDetailResponseSerializer(serializers.Serializer):
    """Response serializer for GET /scenarios/{scenario_id}/.

    Formalizes the ad-hoc dict currently built in ScenarioDetailView.get().
    All field names match the current manual dict construction.
    """

    id = serializers.UUIDField(read_only=True)
    name = serializers.CharField(read_only=True)
    description = serializers.CharField(read_only=True, allow_null=True)
    source = serializers.CharField(read_only=True)
    scenario_type = serializers.ChoiceField(
        choices=Scenarios.ScenarioTypes.choices, read_only=True
    )
    dataset_id = serializers.UUIDField(read_only=True, allow_null=True)
    organization = serializers.UUIDField(read_only=True)
    dataset = serializers.UUIDField(read_only=True, allow_null=True)
    created_at = serializers.DateTimeField(read_only=True)
    updated_at = serializers.DateTimeField(read_only=True)
    deleted = serializers.BooleanField(read_only=True)
    deleted_at = serializers.DateTimeField(read_only=True, allow_null=True)
    status = serializers.ChoiceField(
        choices=StatusType.get_choices(), read_only=True
    )
    agent_type = serializers.CharField(read_only=True, allow_null=True)
    graph = serializers.DictField(read_only=True)
    prompts = serializers.ListField(
        child=ScenarioPromptItemSerializer(), read_only=True
    )
    dataset_rows = serializers.IntegerField(read_only=True)


class ScenarioListResponseSerializer(serializers.Serializer):
    """Paginated response envelope for GET /scenarios/."""

    count = serializers.IntegerField(read_only=True)
    next = serializers.CharField(read_only=True, allow_null=True)
    previous = serializers.CharField(read_only=True, allow_null=True)
    results = ScenarioResponseSerializer(many=True, read_only=True)


class ScenarioCreateResponseSerializer(serializers.Serializer):
    """Response serializer for POST /scenarios/create/ (202 Accepted)."""

    message = serializers.CharField(read_only=True)
    scenario = ScenarioResponseSerializer(read_only=True)
    status = serializers.ChoiceField(
        choices=[("processing", "Processing")], read_only=True
    )


class ScenarioEditResponseSerializer(serializers.Serializer):
    """Response serializer for PUT /scenarios/{scenario_id}/edit/."""

    message = serializers.CharField(read_only=True)
    scenario = ScenarioResponseSerializer(read_only=True)


class ScenarioDeleteResponseSerializer(serializers.Serializer):
    """Response serializer for DELETE /scenarios/{scenario_id}/delete/."""

    message = serializers.CharField(read_only=True)


class ScenarioAddRowsResponseSerializer(serializers.Serializer):
    """Response serializer for POST /scenarios/{scenario_id}/add-rows/ (202 Accepted)."""

    message = serializers.CharField(read_only=True)
    scenario_id = serializers.UUIDField(read_only=True)
    dataset_id = serializers.UUIDField(read_only=True)
    num_rows = serializers.IntegerField(read_only=True)


class ScenarioAddColumnsResponseSerializer(serializers.Serializer):
    """Response serializer for POST /scenarios/{scenario_id}/add-columns/ (202 Accepted)."""

    message = serializers.CharField(read_only=True)
    scenario_id = serializers.UUIDField(read_only=True)
    dataset_id = serializers.UUIDField(read_only=True)
    columns = serializers.ListField(child=serializers.CharField(), read_only=True)


class ScenarioMultiDatasetResponseSerializer(serializers.Serializer):
    """Response serializer for GET /scenarios/get-columns/."""

    column_configs = ScenarioResponseSerializer(many=True, read_only=True)


class ScenarioPromptsUpdateResponseSerializer(serializers.Serializer):
    """Response serializer for PUT /scenarios/{scenario_id}/prompts/."""

    message = serializers.CharField(read_only=True)
    prompts = serializers.CharField(read_only=True)


class ScenarioErrorResponseSerializer(serializers.Serializer):
    """Standardized error response shape — used only for Swagger documentation.

    Not applied to actual response construction (preserves existing behavior).

    Shape:
        {"error": "Human-readable message", "details": {"field": ["validation error"]}}

    `details` is only present for 400 validation errors.
    """

    error = serializers.CharField(read_only=True)
    details = serializers.DictField(required=False, read_only=True)
