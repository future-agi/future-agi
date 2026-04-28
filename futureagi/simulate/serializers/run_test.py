import traceback

import structlog
from django.db.models import Count, Q
from rest_framework import serializers

logger = structlog.get_logger(__name__)
from simulate.models import (
    AgentDefinition,
    RunTest,
    Scenarios,
    SimulateEvalConfig,
)
from simulate.models.test_execution import CallExecution
from simulate.serializers.response.agent_definition import (
    AgentDefinitionResponseSerializer,
)
from simulate.serializers.response.scenarios import ScenarioResponseSerializer
from simulate.serializers.simulator_agent import SimulatorAgentSerializer


class SimulateEvalConfigSimpleSerializer(serializers.ModelSerializer):
    """Simple serializer for SimulateEvalConfig to avoid circular imports"""

    eval_group = serializers.SerializerMethodField()
    # Expose the underlying eval template id so the frontend's eval picker
    # edit flow can load the template (EvalPickerConfigFull reads evalData.id).
    template_id = serializers.PrimaryKeyRelatedField(
        source="eval_template", read_only=True
    )

    class Meta:
        model = SimulateEvalConfig
        fields = [
            "id",
            "name",
            "config",
            "mapping",
            "filters",
            "error_localizer",
            "model",
            "status",
            "eval_group",
            "template_id",
        ]

    def get_eval_group(self, obj):
        """
        Return the name of the user who created this template.
        Returns None if created_by is None.
        """
        if obj.eval_group:
            return obj.eval_group.name
        return None


class RunTestSerializer(serializers.ModelSerializer):
    """Serializer for the RunTest model"""

    agent_definition_detail = AgentDefinitionResponseSerializer(
        source="agent_definition", read_only=True
    )

    scenarios_detail = ScenarioResponseSerializer(
        source="scenarios", many=True, read_only=True
    )

    simulator_agent_detail = SimulatorAgentSerializer(
        source="simulator_agent", read_only=True
    )

    simulate_eval_configs_detail = SimulateEvalConfigSimpleSerializer(
        source="simulate_eval_configs", many=True, read_only=True
    )

    # Backward compatibility field for frontend
    evals_detail = SimulateEvalConfigSimpleSerializer(
        source="simulate_eval_configs", many=True, read_only=True
    )

    source_type_display = serializers.CharField(
        source="get_source_type_display", read_only=True
    )
    last_run_at = serializers.DateTimeField(
        read_only=True, default=None, allow_null=True
    )
    prompt_template_detail = serializers.SerializerMethodField()
    prompt_version_detail = serializers.SerializerMethodField()

    class Meta:
        model = RunTest
        fields = [
            "id",
            "name",
            "description",
            "agent_definition",
            "agent_version",
            "agent_definition_detail",
            "source_type",
            "source_type_display",
            "prompt_template",
            "prompt_template_detail",
            "prompt_version",
            "prompt_version_detail",
            "scenarios",
            "scenarios_detail",
            "dataset_row_ids",
            "simulator_agent",
            "simulator_agent_detail",
            "simulate_eval_configs",
            "simulate_eval_configs_detail",
            "evals_detail",
            "organization",
            "enable_tool_evaluation",
            "created_at",
            "updated_at",
            "last_run_at",
            "deleted",
            "deleted_at",
        ]
        read_only_fields = [
            "id",
            "created_at",
            "updated_at",
            "deleted",
            "deleted_at",
            "organization",
        ]

    def get_prompt_template_detail(self, instance):
        """Get prompt template details for prompt source type"""
        if instance.prompt_template:
            return {
                "id": str(instance.prompt_template.id),
                "name": instance.prompt_template.name,
                "description": instance.prompt_template.description,
                "variable_names": instance.prompt_template.variable_names,
            }
        return None

    def get_prompt_version_detail(self, instance):
        """Get prompt version details for prompt source type"""
        if instance.prompt_version:
            return {
                "id": str(instance.prompt_version.id),
                "template_version": instance.prompt_version.template_version,
                "is_default": instance.prompt_version.is_default,
                "commit_message": instance.prompt_version.commit_message,
            }
        return None

    def to_representation(self, instance):
        """Custom representation to handle backward compatibility"""
        data = super().to_representation(instance)

        # Add evals field for backward compatibility. ``super()`` already
        # serialized ``simulate_eval_configs`` via ``evals_detail`` /
        # ``simulate_eval_configs_detail`` — reuse that data instead of
        # re-serializing the same queryset a third time.
        data["evals"] = data.get("evals_detail", [])
        try:
            # Only set agent_version for agent_definition source type.
            # Check for soft-deleted agent definition first: FK traversal bypasses
            # BaseModelManager's deleted=False filter, so we check explicitly.
            if instance.agent_definition is not None:
                if instance.agent_definition.deleted:
                    data["agent_definition"] = None
                    data["agent_definition_detail"] = None
                    data["agent_version"] = None
                elif instance.agent_version:
                    # Use the select_related agent_version
                    snapshot = instance.agent_version.configuration_snapshot or {}
                    if "agent_type" not in snapshot:
                        snapshot = {
                            **snapshot,
                            "agent_type": instance.agent_definition.agent_type,
                        }
                    data["agent_version"] = {
                        "id": instance.agent_version.id,
                        "name": instance.agent_version.version_name,
                        "configuration_snapshot": snapshot,
                    }
                else:
                    # Try to use prefetched versions first to avoid N+1.
                    # _prefetched_versions is set by RunTestListView.get() using:
                    #   Prefetch("agent_definition__versions", ..., to_attr="_prefetched_versions")
                    # The versions are ordered by -version_number so first item is latest.
                    latest_version = None
                    if hasattr(instance.agent_definition, "_prefetched_versions"):
                        prefetched = instance.agent_definition._prefetched_versions
                        if prefetched:
                            latest_version = prefetched[0]  # First is latest
                    else:
                        # Fallback - this triggers an additional query if not prefetched.
                        # This path is taken for executions without explicit agent_version
                        # when the view doesn't use the prefetch (e.g., detail views).
                        latest_version = instance.agent_definition.latest_version

                    if latest_version:
                        snapshot = latest_version.configuration_snapshot or {}
                        if "agent_type" not in snapshot:
                            snapshot = {
                                **snapshot,
                                "agent_type": instance.agent_definition.agent_type,
                            }
                        data["agent_version"] = {
                            "id": latest_version.id,
                            "name": latest_version.version_name,
                            "configuration_snapshot": snapshot,
                        }
        except Exception as e:
            logger.exception(
                f"Error getting agent version: {e} for run test {instance.id}"
            )

        return data

    def validate_name(self, value):
        """Validate that name is not empty or just whitespace"""
        if not value.strip():
            raise serializers.ValidationError(
                "Name cannot be empty or just whitespace."
            )
        return value.strip()


class RunTestExecutionsSerializer(serializers.ModelSerializer):
    """Serializer for RunTest with execution metrics for the executions endpoint"""

    # Call metrics fields
    calls_attempted = serializers.SerializerMethodField()
    calls_connected_percentage = serializers.SerializerMethodField()

    # Test executions with their individual call metrics
    executions = serializers.SerializerMethodField()

    class Meta:
        model = RunTest
        fields = [
            "id",
            "name",
            "description",
            "calls_attempted",
            "calls_connected_percentage",
            "executions",
            "organization",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at", "organization"]

    def get_calls_attempted(self, obj):
        """Count calls that are not pending or queued."""

        # Single query to get total and excluded calls
        call_counts = CallExecution.objects.filter(
            test_execution__run_test=obj
        ).aggregate(
            total_calls=Count("id"),
            pending_calls=Count(
                "id", filter=Q(status=CallExecution.CallStatus.PENDING)
            ),
            queued_calls=Count(
                "id", filter=Q(status=CallExecution.CallStatus.REGISTERED)
            ),
        )

        # Calls attempted = total calls - pending calls - queued calls
        return (
            call_counts["total_calls"]
            - call_counts["pending_calls"]
            - call_counts["queued_calls"]
        )

    def get_calls_connected_percentage(self, obj):
        """Calculate percentage of calls connected (duration > 0 seconds)."""

        # Single query to get all needed counts
        call_counts = CallExecution.objects.filter(
            test_execution__run_test=obj
        ).aggregate(
            total_calls=Count("id"),
            pending_calls=Count(
                "id", filter=Q(status=CallExecution.CallStatus.PENDING)
            ),
            queued_calls=Count(
                "id", filter=Q(status=CallExecution.CallStatus.REGISTERED)
            ),
            connected_calls=Count("id", filter=Q(duration_seconds__gt=0)),
        )

        calls_attempted = (
            call_counts["total_calls"]
            - call_counts["pending_calls"]
            - call_counts["queued_calls"]
        )

        if calls_attempted == 0:
            return 0.0

        # Calculate percentage
        percentage = (call_counts["connected_calls"] / calls_attempted) * 100
        return round(percentage, 2)

    def get_executions(self, obj):
        """Get test executions with only their call metrics - optimized version"""

        # Get all test executions for this run test with prefetched calls
        executions = obj.executions.order_by("-created_at").prefetch_related("calls")

        # Get all call execution data in bulk to avoid N+1 queries
        execution_ids = [exec.id for exec in executions]

        # Bulk query to get call counts per execution
        call_counts = (
            CallExecution.objects.filter(test_execution_id__in=execution_ids)
            .values("test_execution_id")
            .annotate(
                total_calls=Count("id"),
                pending_calls=Count(
                    "id", filter=Q(status=CallExecution.CallStatus.PENDING)
                ),
                queued_calls=Count(
                    "id", filter=Q(status=CallExecution.CallStatus.REGISTERED)
                ),
                connected_calls=Count("id", filter=Q(duration_seconds__gt=0)),
            )
        )

        # Create a lookup dictionary for quick access
        call_counts_map = {
            item["test_execution_id"]: {
                "total_calls": item["total_calls"],
                "pending_calls": item["pending_calls"],
                "queued_calls": item["queued_calls"],
                "connected_calls": item["connected_calls"],
            }
            for item in call_counts
        }

        executions_data = []
        for execution in executions:
            counts = call_counts_map.get(
                execution.id,
                {
                    "total_calls": 0,
                    "pending_calls": 0,
                    "queued_calls": 0,
                    "connected_calls": 0,
                },
            )

            calls_attempted = (
                counts["total_calls"] - counts["pending_calls"] - counts["queued_calls"]
            )

            calls_connected_percentage = 0.0
            if calls_attempted > 0:
                calls_connected_percentage = round(
                    (counts["connected_calls"] / calls_attempted) * 100, 2
                )

            executions_data.append(
                {
                    "id": str(execution.id),
                    "calls_attempted": calls_attempted,
                    "calls_connected_percentage": calls_connected_percentage,
                }
            )

        return executions_data


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
    # simulator_agent_id removed - will be derived from scenarios
    eval_config_ids = serializers.ListField(
        child=serializers.UUIDField(), allow_empty=True, required=False, default=list
    )

    # Field for evaluation configuration data from frontend
    evaluations_config = serializers.ListField(
        child=serializers.DictField(),
        allow_empty=True,
        required=False,
        default=list,
        help_text="Evaluation configurations to create",
    )

    # Field for enabling tool evaluation
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

    # validate_simulator_agent_id method removed - simulator agent will be derived from scenarios

    def validate_eval_config_ids(self, value):
        """Validate that all evaluation config IDs exist"""
        if not value:
            return []

        # Filter out any non-UUID strings
        valid_uuids = []
        for item in value:
            try:
                # Try to convert to UUID to validate format
                import uuid

                uuid.UUID(str(item))
                valid_uuids.append(item)
            except (ValueError, AttributeError):
                # Skip invalid UUIDs
                continue

        if not valid_uuids:
            return []

        organization = self.context["request"].user.organization
        existing_ids = set(
            SimulateEvalConfig.objects.filter(
                id__in=valid_uuids, run_test__organization=organization
            ).values_list("id", flat=True)
        )

        # Only return the IDs that exist in the database
        return list(existing_ids)


class UpdateRunTestSerializer(serializers.Serializer):
    """Serializer for updating a RunTest"""

    name = serializers.CharField(max_length=255, required=False)
    description = serializers.CharField(allow_blank=True, required=False)
    agent_definition_id = serializers.UUIDField(required=False)
    scenario_ids = serializers.ListField(
        child=serializers.UUIDField(), allow_empty=False, required=False
    )
    dataset_row_ids = serializers.ListField(
        child=serializers.CharField(max_length=255), allow_empty=True, required=False
    )
    # simulator_agent_id removed - will be derived from scenarios
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

    # Field for evaluation configuration data from frontend
    evaluations_config = serializers.ListField(
        child=serializers.DictField(),
        allow_empty=True,
        required=False,
        default=list,
        help_text="Evaluation configurations to create",
    )

    # Field for enabling tool evaluation
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
        Store the raw value - actual validation happens in validate() with cross-field context.
        This allows us to use prompt_template_id to find the correct version.
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
        """Cross-field validation - resolve prompt_version_id to actual UUID"""
        import uuid

        from model_hub.models.run_prompt import PromptVersion

        prompt_template_id = data.get("prompt_template_id")
        prompt_version_id = data.get("prompt_version_id")
        prompt_version = None

        # Try to find the prompt version by UUID or template_version string
        # Strategy 1: Try as UUID first
        try:
            version_uuid = uuid.UUID(str(prompt_version_id))
            prompt_version = PromptVersion.objects.filter(
                id=version_uuid, original_template_id=prompt_template_id, deleted=False
            ).first()
        except (ValueError, AttributeError):
            pass

        # Strategy 2: Try as template_version (like 'v1')
        if not prompt_version:
            prompt_version = PromptVersion.objects.filter(
                template_version=prompt_version_id,
                original_template_id=prompt_template_id,
                deleted=False,
            ).first()

        if not prompt_version:
            raise serializers.ValidationError(
                {
                    "prompt_version_id": f"Prompt version '{prompt_version_id}' not found for this template."
                }
            )

        # Update data with the resolved UUID
        data["prompt_version_id"] = str(prompt_version.id)

        return data
