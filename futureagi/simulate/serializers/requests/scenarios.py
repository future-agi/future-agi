import json
import uuid

from rest_framework import serializers

from model_hub.models.choices import DataTypeChoices, SourceChoices
from simulate.models import Scenarios


class ColumnDefinitionSerializer(serializers.Serializer):
    """Typed column definition — replaces DictField() in scenario create/add-columns."""

    name = serializers.CharField(max_length=50)
    data_type = serializers.ChoiceField(choices=DataTypeChoices.get_choices())
    description = serializers.CharField(max_length=200)

    def validate_name(self, value):
        if not value.strip():
            raise serializers.ValidationError(
                "Column name cannot be empty or just whitespace."
            )
        return value.strip()

    def validate_description(self, value):
        if not value.strip():
            raise serializers.ValidationError(
                "Column description cannot be empty or just whitespace."
            )
        return value.strip()


class ScenarioFilterSerializer(serializers.Serializer):
    """Validates GET /scenarios/ query parameters."""

    search = serializers.CharField(required=False, allow_blank=True, default="")
    agent_definition_id = serializers.UUIDField(required=False, allow_null=True)
    agent_type = serializers.CharField(required=False, allow_null=True)
    page = serializers.IntegerField(required=False, default=1, min_value=1)
    limit = serializers.IntegerField(required=False, min_value=1)


class ScenarioMultiDatasetFilterSerializer(serializers.Serializer):
    """Validates GET /scenarios/get-columns/ query parameter.

    Phase 0.3: replaces raw json.loads() in ScenariosListView.get_multi_datasets_column_configs().
    """

    scenarios = serializers.CharField(
        required=True, help_text="JSON array of scenario UUIDs"
    )

    def validate_scenarios(self, value):
        try:
            parsed = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            raise serializers.ValidationError(
                "Must be a valid JSON array of scenario UUIDs."
            )
        if not isinstance(parsed, list):
            raise serializers.ValidationError("Must be a JSON array.")
        validated_ids = []
        for item in parsed:
            try:
                validated_ids.append(uuid.UUID(str(item)))
            except (ValueError, AttributeError):
                raise serializers.ValidationError(f"Invalid UUID: {item}")
        return validated_ids


class ScenarioCreateRequestSerializer(serializers.Serializer):
    """Request serializer for POST /scenarios/create/.

    Incorporates Phase 0 validations (0.1.1 – 0.1.3) that previously lived in the view.
    """

    name = serializers.CharField(max_length=255)
    description = serializers.CharField(required=False, allow_blank=True)
    dataset_id = serializers.UUIDField(required=False)
    kind = serializers.ChoiceField(
        choices=Scenarios.ScenarioTypes.choices,
        required=False,
        default=Scenarios.ScenarioTypes.DATASET,
    )
    script_url = serializers.URLField(required=False, allow_null=True)
    agent_definition_id = serializers.UUIDField(required=False)
    agent_definition_version_id = serializers.UUIDField(required=False, allow_null=True)
    custom_instruction = serializers.CharField(required=False, allow_blank=True)
    no_of_rows = serializers.IntegerField(
        required=False, default=20, min_value=10, max_value=20000
    )
    generate_graph = serializers.BooleanField(required=False, default=False)
    graph = serializers.JSONField(required=False, allow_null=True)
    source_type = serializers.ChoiceField(
        choices=Scenarios.SourceTypes.choices,
        required=False,
        default=Scenarios.SourceTypes.AGENT_DEFINITION,
    )
    prompt_template_id = serializers.UUIDField(required=False, allow_null=True)
    prompt_version_id = serializers.UUIDField(required=False, allow_null=True)
    add_persona_automatically = serializers.BooleanField(required=False, default=False)
    personas = serializers.ListField(
        child=serializers.UUIDField(),
        required=False,
        allow_empty=True,
    )
    custom_columns = serializers.ListField(
        child=ColumnDefinitionSerializer(),
        required=False,
        allow_empty=True,
        max_length=10,
    )
    agent_name = serializers.CharField(max_length=255, required=False)
    agent_prompt = serializers.CharField(required=False, allow_blank=True)
    voice_provider = serializers.CharField(
        max_length=100, required=False, default="elevenlabs"
    )
    voice_name = serializers.CharField(
        max_length=100, required=False, default="marissa"
    )
    model = serializers.CharField(max_length=100, required=False, default="gpt-4")
    llm_temperature = serializers.FloatField(required=False, default=0.7)
    initial_message = serializers.CharField(required=False, allow_blank=True)
    max_call_duration_in_minutes = serializers.IntegerField(required=False, default=30)
    interrupt_sensitivity = serializers.FloatField(required=False, default=0.5)
    conversation_speed = serializers.FloatField(required=False, default=1.0)
    finished_speaking_sensitivity = serializers.FloatField(required=False, default=0.5)
    initial_message_delay = serializers.IntegerField(required=False, default=0)

    def validate_name(self, value):
        if not value.strip():
            raise serializers.ValidationError(
                "Name cannot be empty or just whitespace."
            )
        return value.strip()

    def validate_dataset_id(self, value):
        if not value:
            return value
        request = self.context.get("request")
        if not request:
            raise serializers.ValidationError("Request context is required.")
        from model_hub.models.develop_dataset import Dataset

        try:
            Dataset.objects.get(
                id=value,
                deleted=False,
                organization=getattr(request, "organization", None)
                or request.user.organization,
            )
            return value
        except Dataset.DoesNotExist as e:
            raise serializers.ValidationError(
                "Dataset not found or not accessible."
            ) from e

    def validate_prompt_template_id(self, value):
        if not value:
            return value
        request = self.context.get("request")
        if not request:
            raise serializers.ValidationError("Request context is required.")
        from model_hub.models.run_prompt import PromptTemplate

        try:
            filters = {
                "id": value,
                "deleted": False,
                "organization": getattr(request, "organization", None)
                or request.user.organization,
            }
            if hasattr(request.user, "workspace") and request.user.workspace:
                filters["workspace"] = request.user.workspace
            PromptTemplate.objects.get(**filters)
            return value
        except PromptTemplate.DoesNotExist as e:
            raise serializers.ValidationError(
                "Prompt template not found or not accessible."
            ) from e

    def validate_prompt_version_id(self, value):
        if not value:
            return value
        request = self.context.get("request")
        if not request:
            raise serializers.ValidationError("Request context is required.")
        from model_hub.models.run_prompt import PromptVersion

        try:
            filters = {
                "id": value,
                "deleted": False,
                "original_template__organization": getattr(
                    request, "organization", None
                )
                or request.user.organization,
            }
            if hasattr(request.user, "workspace") and request.user.workspace:
                filters["original_template__workspace"] = request.user.workspace
            PromptVersion.objects.get(**filters)
            return value
        except PromptVersion.DoesNotExist as e:
            raise serializers.ValidationError(
                "Prompt version not found or not accessible."
            ) from e

    def validate(self, data):
        """Cross-field validation — absorbs Phase 0 checks 0.1.1 – 0.1.3."""
        kind = data.get("kind", Scenarios.ScenarioTypes.DATASET)
        source_type = data.get("source_type", Scenarios.SourceTypes.AGENT_DEFINITION)

        # Basic kind-level requirements
        if kind == Scenarios.ScenarioTypes.DATASET and not data.get("dataset_id"):
            raise serializers.ValidationError(
                {"dataset_id": "dataset_id is required for dataset kind."}
            )

        if kind == Scenarios.ScenarioTypes.SCRIPT and not data.get("script_url"):
            raise serializers.ValidationError(
                {"script_url": "script_url is required for script kind."}
            )

        if kind == Scenarios.ScenarioTypes.GRAPH:
            if not data.get("generate_graph") and not data.get("graph"):
                raise serializers.ValidationError(
                    "Either generate_graph=True with agent_definition_id or graph data is required for graph kind."
                )
            if data.get("generate_graph"):
                if source_type != Scenarios.SourceTypes.PROMPT and not data.get(
                    "agent_definition_id"
                ):
                    raise serializers.ValidationError(
                        "agent_definition_id or prompt_template_id is required when generate_graph=True."
                    )

        # Prompt source type requirements
        if source_type == Scenarios.SourceTypes.PROMPT:
            if not data.get("prompt_template_id"):
                raise serializers.ValidationError(
                    {
                        "prompt_template_id": "prompt_template_id is required for prompt source type."
                    }
                )
            if not data.get("prompt_version_id"):
                raise serializers.ValidationError(
                    {
                        "prompt_version_id": "prompt_version_id is required for prompt source type."
                    }
                )
            from model_hub.models.run_prompt import PromptVersion

            try:
                prompt_version = PromptVersion.objects.get(
                    id=data.get("prompt_version_id"), deleted=False
                )
                if prompt_version.original_template_id != data.get(
                    "prompt_template_id"
                ):
                    raise serializers.ValidationError(
                        "Prompt version does not belong to the specified prompt template."
                    )
            except PromptVersion.DoesNotExist:
                pass  # Already caught in validate_prompt_version_id

        # Phase 0.1.1 — duplicate custom column names (script/graph)
        if kind in (Scenarios.ScenarioTypes.SCRIPT, Scenarios.ScenarioTypes.GRAPH):
            custom_columns = data.get("custom_columns", [])
            if custom_columns:
                names = [col["name"] for col in custom_columns]
                duplicates = {n for n in names if names.count(n) > 1}
                if duplicates:
                    raise serializers.ValidationError(
                        {
                            "custom_columns": f"Duplicate column name(s): {', '.join(duplicates)}"
                        }
                    )

        # Phase 0.1.2 + 0.1.3 — persona column type + duplicate DB column names (dataset)
        if kind == Scenarios.ScenarioTypes.DATASET and data.get("dataset_id"):
            request = self.context.get("request")
            from model_hub.models.develop_dataset import Column, Dataset

            try:
                source_dataset = Dataset.objects.get(
                    id=data["dataset_id"],
                    deleted=False,
                    organization=getattr(request, "organization", None)
                    or request.user.organization,
                )
            except Dataset.DoesNotExist:
                # Already validated in validate_dataset_id; skip DB checks
                return data

            # 0.1.2 — persona column data type
            persona_col = Column.objects.filter(
                dataset=source_dataset, name="persona", deleted=False
            ).first()
            if persona_col and persona_col.data_type != DataTypeChoices.PERSONA.value:
                raise serializers.ValidationError(
                    {
                        "dataset_id": (
                            f"Invalid data type for 'persona' column. "
                            f"Expected '{DataTypeChoices.PERSONA.value}', "
                            f"found '{persona_col.data_type}'."
                        )
                    }
                )

            # 0.1.3 — duplicate column names in source dataset
            source_cols = Column.objects.filter(
                dataset=source_dataset, deleted=False
            ).exclude(
                source__in=[
                    SourceChoices.EXPERIMENT.value,
                    SourceChoices.EXPERIMENT_EVALUATION.value,
                    SourceChoices.EXPERIMENT_EVALUATION_TAGS.value,
                ]
            )
            col_names = [c.name for c in source_cols]
            dupes = {n for n in col_names if col_names.count(n) > 1}
            if dupes:
                raise serializers.ValidationError(
                    {
                        "dataset_id": f"Source dataset contains duplicate column name(s): {', '.join(dupes)}"
                    }
                )

        return data


class ScenarioEditRequestSerializer(serializers.Serializer):
    """Request serializer for PUT /scenarios/{scenario_id}/edit/."""

    name = serializers.CharField(max_length=255, required=False, allow_blank=True)
    description = serializers.CharField(required=False, allow_blank=True)
    graph = serializers.JSONField(required=False, allow_null=True)
    prompt = serializers.CharField(required=False, allow_blank=True)

    def validate_name(self, value):
        if not value.strip():
            raise serializers.ValidationError(
                "Name cannot be empty or just whitespace."
            )
        return value.strip()


class ScenarioEditPromptsRequestSerializer(serializers.Serializer):
    """Request serializer for PUT /scenarios/{scenario_id}/prompts/."""

    prompts = serializers.CharField(max_length=10000)


class ScenarioAddRowsRequestSerializer(serializers.Serializer):
    """Request serializer for POST /scenarios/{scenario_id}/add-rows/."""

    num_rows = serializers.IntegerField(min_value=10, max_value=20000)
    description = serializers.CharField(required=False, allow_blank=True)

    def validate_num_rows(self, value):
        if value < 10:
            raise serializers.ValidationError("Number of rows must be at least 10.")
        if value > 20000:
            raise serializers.ValidationError("Number of rows cannot exceed 20000.")
        return value


class ScenarioAddColumnsRequestSerializer(serializers.Serializer):
    """Request serializer for POST /scenarios/{scenario_id}/add-columns/.

    Incorporates Phase 0 validations (0.2.1 – 0.2.2) that previously lived in the view.
    """

    # Use many=True so drf-yasg renders this as a proper array-of-objects schema.
    # ListField(child=Serializer()) causes drf-yasg to hoist child fields to the top level.
    columns = ColumnDefinitionSerializer(many=True, required=True)

    def validate_columns(self, value):
        """min_length=1, max_length=10 (replaces allow_empty=False / max_length on ListField)."""
        if not value:
            raise serializers.ValidationError("At least one column is required.")
        if len(value) > 10:
            raise serializers.ValidationError(
                "Cannot add more than 10 columns at once."
            )
        return value

    def validate(self, data):
        """Phase 0.2.1 — duplicate names in request; Phase 0.2.2 — already exists in DB."""
        columns = data.get("columns", [])

        # 0.2.1 — duplicates within the request payload
        names = [col["name"] for col in columns]
        duplicates = {n for n in names if names.count(n) > 1}
        if duplicates:
            raise serializers.ValidationError(
                {"columns": f"Duplicate column name(s): {', '.join(duplicates)}"}
            )

        # 0.2.2 — names already present in the scenario's dataset
        scenario = self.context.get("scenario")
        if scenario and scenario.dataset:
            from model_hub.models.develop_dataset import Column

            existing = set(
                Column.objects.filter(
                    dataset=scenario.dataset, deleted=False
                ).values_list("name", flat=True)
            )
            for col in columns:
                if col["name"] in existing:
                    raise serializers.ValidationError(
                        {
                            "columns": f"Column '{col['name']}' already exists in the dataset."
                        }
                    )

        return data
