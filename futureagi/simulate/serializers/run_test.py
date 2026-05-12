"""
Run test serializers — internal serializers.

RunTestSerializer and SimulateEvalConfigSimpleSerializer live here for
internal/Temporal use.

The view layer should use:
  - simulate.serializers.requests.run_test  — request/input serializers
  - simulate.serializers.response.run_test  — response/output serializers
"""

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
