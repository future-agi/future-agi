"""
Request serializers for the run_test module.

These serializers validate inbound request payloads for run-test endpoints.
They are the single source of truth for what fields are accepted on POST/PATCH
requests and what query parameters are valid on GET requests.

Internal/Temporal code should continue to import from
``simulate.serializers.run_test`` directly.
"""

import uuid

from rest_framework import serializers

from simulate.models import AgentDefinition, RunTest, Scenarios, SimulateEvalConfig


class RunTestFilterSerializer(serializers.Serializer):
    """
    Validates GET /run-tests/ query parameters.
    """

    search = serializers.CharField(required=False, allow_blank=True, default="")
    simulation_type = serializers.ChoiceField(
        choices=RunTest.SourceTypes.choices,
        required=False,
        allow_blank=True,
        default="",
    )
    prompt_template_id = serializers.UUIDField(required=False, allow_null=True)
    page = serializers.IntegerField(required=False, default=1, min_value=1)
    limit = serializers.IntegerField(required=False, min_value=1)


class CreateRunTestSerializer(serializers.Serializer):
    """Serializer for creating a new RunTest"""

    name = serializers.CharField(max_length=255)
    description = serializers.CharField(allow_blank=True, required=False)
    agent_definition_id = serializers.UUIDField()
    scenario_ids = serializers.ListField(
        child=serializers.UUIDField(), allow_empty=False
    )
    dataset_row_ids = serializers.ListField(
        child=serializers.CharField(max_length=255), allow_empty=True, required=False
    )
    eval_config_ids = serializers.ListField(
        child=serializers.UUIDField(), allow_empty=True, required=False, default=list
    )
    evaluations_config = serializers.ListField(
        child=serializers.DictField(),
        allow_empty=True,
        required=False,
        default=list,
        help_text="Evaluation configurations to create",
    )
    enable_tool_evaluation = serializers.BooleanField(
        required=False,
        default=False,
        help_text="Enable automatic tool evaluation for this test run",
    )
    replay_session_id = serializers.UUIDField(
        required=False,
        allow_null=True,
        help_text="Optional replay session ID to mark as completed after run test creation",
    )

    def validate_agent_definition_id(self, value):
        """Validate that the agent definition exists"""
        organization = self.context["request"].user.organization
        if not AgentDefinition.objects.filter(
            id=value, organization=organization
        ).exists():
            raise serializers.ValidationError("Agent definition not found.")
        return value

    def validate_scenario_ids(self, value):
        """Validate that all scenario IDs exist"""
        organization = self.context["request"].user.organization
        existing_ids = set(
            Scenarios.objects.filter(
                id__in=value, organization=organization
            ).values_list("id", flat=True)
        )
        invalid_ids = set(value) - existing_ids
        if invalid_ids:
            raise serializers.ValidationError(f"Scenarios not found: {invalid_ids}")
        return value

    def validate_eval_config_ids(self, value):
        """Validate that all evaluation config IDs exist"""
        if not value:
            return []

        valid_uuids = []
        for item in value:
            try:
                uuid.UUID(str(item))
                valid_uuids.append(item)
            except (ValueError, AttributeError):
                continue

        if not valid_uuids:
            return []

        organization = self.context["request"].user.organization
        existing_ids = set(
            SimulateEvalConfig.objects.filter(
                id__in=valid_uuids, run_test__organization=organization
            ).values_list("id", flat=True)
        )
        return list(existing_ids)


class UpdateRunTestSerializer(serializers.Serializer):
    """Serializer for updating a RunTest (PATCH)"""

    name = serializers.CharField(max_length=255, required=False)
    description = serializers.CharField(allow_blank=True, required=False)
    agent_definition_id = serializers.UUIDField(required=False)
    scenario_ids = serializers.ListField(
        child=serializers.UUIDField(), allow_empty=False, required=False
    )
    dataset_row_ids = serializers.ListField(
        child=serializers.CharField(max_length=255), allow_empty=True, required=False
    )
    eval_config_ids = serializers.ListField(
        child=serializers.UUIDField(), allow_empty=True, required=False
    )


class CreatePromptSimulationSerializer(serializers.Serializer):
    """Serializer for creating a new prompt-based simulation run"""

    name = serializers.CharField(max_length=255)
    description = serializers.CharField(allow_blank=True, required=False)
    prompt_template_id = serializers.UUIDField(
        help_text="Prompt template to use as the agent source"
    )
    prompt_version_id = serializers.CharField(
        max_length=255, help_text="Prompt version ID (UUID) or template_version string"
    )
    scenario_ids = serializers.ListField(
        child=serializers.UUIDField(), allow_empty=False
    )
    dataset_row_ids = serializers.ListField(
        child=serializers.CharField(max_length=255), allow_empty=True, required=False
    )
    evaluations_config = serializers.ListField(
        child=serializers.DictField(),
        allow_empty=True,
        required=False,
        default=list,
        help_text="Evaluation configurations to create",
    )
    enable_tool_evaluation = serializers.BooleanField(
        required=False,
        default=False,
        help_text="Enable automatic tool evaluation for this simulation run",
    )

    def validate_prompt_template_id(self, value):
        """Validate that the prompt template exists"""
        from model_hub.models.run_prompt import PromptTemplate

        organization = self.context["request"].user.organization
        if not PromptTemplate.objects.filter(
            id=value, organization=organization, deleted=False
        ).exists():
            raise serializers.ValidationError("Prompt template not found.")
        return value

    def validate_prompt_version_id(self, value):
        """
        Store the raw value — actual validation happens in validate() with
        cross-field context. This allows us to use prompt_template_id to
        find the correct version.
        """
        return value

    def validate_scenario_ids(self, value):
        """Validate that all scenario IDs exist"""
        organization = self.context["request"].user.organization
        existing_ids = set(
            Scenarios.objects.filter(
                id__in=value, organization=organization
            ).values_list("id", flat=True)
        )
        invalid_ids = set(value) - existing_ids
        if invalid_ids:
            raise serializers.ValidationError(f"Scenarios not found: {invalid_ids}")
        return value

    def validate(self, data):
        """Cross-field validation — resolve prompt_version_id to actual UUID"""
        import uuid as uuid_module

        from model_hub.models.run_prompt import PromptVersion

        prompt_template_id = data.get("prompt_template_id")
        prompt_version_id = data.get("prompt_version_id")
        prompt_version = None

        # Strategy 1: Try as UUID first
        try:
            version_uuid = uuid_module.UUID(str(prompt_version_id))
            prompt_version = PromptVersion.objects.filter(
                id=version_uuid,
                original_template_id=prompt_template_id,
                deleted=False,
            ).first()
        except (ValueError, AttributeError):
            pass

        # Strategy 2: Try as template_version string (e.g. 'v1')
        if not prompt_version:
            prompt_version = PromptVersion.objects.filter(
                template_version=prompt_version_id,
                original_template_id=prompt_template_id,
                deleted=False,
            ).first()

        if not prompt_version:
            raise serializers.ValidationError(
                {
                    "prompt_version_id": (
                        f"Prompt version '{prompt_version_id}' not found for this template."
                    )
                }
            )

        data["prompt_version_id"] = str(prompt_version.id)
        return data
