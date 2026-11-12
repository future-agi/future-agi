"""
Run test serializers — internal serializers.

RunTestSerializer and SimulateEvalConfigSimpleSerializer live here for
internal/Temporal use.

The view layer should use:
  - simulate.serializers.requests.run_test  — request/input serializers
  - simulate.serializers.response.run_test  — response/output serializers
"""

import structlog
from drf_yasg.utils import swagger_serializer_method
from rest_framework import serializers

from simulate.models import (
    AgentDefinition,
    AgentVersion,
    RunTest,
    Scenarios,
    SimulateEvalConfig,
)
from simulate.serializers.response.agent_definition import (
    AgentDefinitionResponseSerializer,
)
from simulate.serializers.response.scenarios import ScenarioResponseSerializer
from simulate.serializers.simulator_agent import SimulatorAgentSerializer
from simulate.services.eval_config_edit import has_own_mapping
from simulate.services.harness_evals import (
    effective_simulation_mapping,
    harness_run_test_ids,
    is_harness_run_test,
    regrade_mapping,
)
from simulate.utils.owned_relations import owned_run_for_serialization
from simulate.utils.snapshot_projection import scrub_snapshot_secrets
from tracer.services.observability_switch import snapshot_as_shown

logger = structlog.get_logger(__name__)

# Serializer context key holding {run test id: is a harness run}.
HARNESS_RUN_TESTS_CONTEXT_KEY = "_harness_run_tests"


def harness_run_tests_context(run_tests) -> dict:
    """Serializer context that answers every run test's harness lookup in one query.

    A list page passes this to ``RunTestSerializer(..., many=True)`` so its
    evals' ``regradable`` costs one query for the page rather than one per
    run test. ``run_tests`` must already be evaluated (a list or a page).
    """
    ids = [run_test.id for run_test in run_tests]
    owned = harness_run_test_ids(ids)
    return {HARNESS_RUN_TESTS_CONTEXT_KEY: {rt_id: rt_id in owned for rt_id in ids}}


class SimulateEvalConfigSimpleSerializer(serializers.ModelSerializer):
    """Simple serializer for SimulateEvalConfig to avoid circular imports"""

    eval_group = serializers.SerializerMethodField()
    # Expose the underlying eval template id so the frontend's eval picker
    # edit flow can load the template (EvalPickerConfigFull reads evalData.id).
    template_id = serializers.PrimaryKeyRelatedField(
        source="eval_template", read_only=True
    )
    # The run page tags each eval with the type the picker shows for it.
    eval_type = serializers.CharField(source="eval_template.eval_type", read_only=True)
    regradable = serializers.SerializerMethodField()
    editable = serializers.SerializerMethodField()

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
            "eval_type",
            "regradable",
            "editable",
        ]

    def to_representation(self, instance):
        data = super().to_representation(instance)
        if isinstance(data["mapping"], dict):
            data["mapping"] = effective_simulation_mapping(
                instance.eval_template, data["mapping"]
            )
        return data

    def get_eval_group(self, obj):
        """
        Return the name of the user who created this template.
        Returns None if created_by is None.
        """
        if obj.eval_group:
            return obj.eval_group.name
        return None

    # The rule run-new-evals refuses by, so the page never offers to grade
    # what the endpoint would turn away. Only a harness run can hold a result
    # column the platform can't grade, and that lookup is made once per run
    # test for the whole response, however many configs it lists. A list page
    # answers it for every run test up front (harness_run_tests_context).
    @swagger_serializer_method(serializer_or_field=serializers.BooleanField())
    def get_regradable(self, obj) -> bool:
        harness_runs = self.context.setdefault(HARNESS_RUN_TESTS_CONTEXT_KEY, {})
        if obj.run_test_id not in harness_runs:
            harness_runs[obj.run_test_id] = is_harness_run_test(obj.run_test_id)
        return not harness_runs[obj.run_test_id] or regrade_mapping(obj) is not None

    # The rule the environment's eval edit refuses by. A row the harness fills
    # is the harness's: later harness runs write into it by its fixed id, so
    # changing it would change what they write into. Shares the lookup above.
    @swagger_serializer_method(serializer_or_field=serializers.BooleanField())
    def get_editable(self, obj) -> bool:
        harness_runs = self.context.setdefault(HARNESS_RUN_TESTS_CONTEXT_KEY, {})
        if obj.run_test_id not in harness_runs:
            harness_runs[obj.run_test_id] = is_harness_run_test(obj.run_test_id)
        return not harness_runs[obj.run_test_id] or has_own_mapping(obj)


class RunTestListAgentSerializer(serializers.ModelSerializer):
    """Agent fields rendered by the paginated run-test list."""

    class Meta:
        model = AgentDefinition
        fields = ["id", "agent_name", "agent_type", "provider", "contact_number"]
        read_only_fields = fields


class RunTestListScenarioSerializer(serializers.ModelSerializer):
    """Scenario fields rendered by the paginated run-test list."""

    scenario_type_display = serializers.CharField(
        source="get_scenario_type_display", read_only=True
    )
    dataset_rows = serializers.SerializerMethodField()

    class Meta:
        model = Scenarios
        fields = [
            "id",
            "name",
            "description",
            "scenario_type",
            "scenario_type_display",
            "dataset_rows",
        ]
        read_only_fields = fields

    def get_dataset_rows(self, obj):
        return getattr(obj, "_dataset_row_count", 0) or 0


class RunTestListEvalSerializer(serializers.ModelSerializer):
    """Evaluation fields rendered by the paginated run-test list."""

    eval_group = serializers.CharField(
        source="eval_group.name", read_only=True, allow_null=True
    )
    model_type = serializers.CharField(source="model", read_only=True, allow_null=True)

    class Meta:
        model = SimulateEvalConfig
        fields = ["id", "name", "model_type", "status", "eval_group"]
        read_only_fields = fields


class RunTestListSummarySerializer(serializers.ModelSerializer):
    """Bounded list-card shape; detail views keep using RunTestSerializer."""

    agent_definition_detail = serializers.SerializerMethodField(allow_null=True)
    scenarios_detail = RunTestListScenarioSerializer(
        source="scenarios", many=True, read_only=True
    )
    evals_detail = RunTestListEvalSerializer(
        source="simulate_eval_configs", many=True, read_only=True
    )
    source_type_display = serializers.CharField(
        source="get_source_type_display", read_only=True
    )
    last_run_at = serializers.DateTimeField(
        read_only=True, default=None, allow_null=True
    )

    class Meta:
        model = RunTest
        fields = [
            "id",
            "name",
            "source_type",
            "source_type_display",
            "agent_definition_detail",
            "scenarios_detail",
            "evals_detail",
            "last_run_at",
        ]
        read_only_fields = fields

    @swagger_serializer_method(
        serializer_or_field=RunTestListAgentSerializer(allow_null=True)
    )
    def get_agent_definition_detail(self, instance):
        if hasattr(instance, "_owned_agent_definition_id"):
            if instance._owned_agent_definition_id is None:
                return None
            agent = {
                field: getattr(instance, f"_owned_agent_definition_{field}")
                for field in RunTestListAgentSerializer.Meta.fields
            }
        else:
            agent = owned_run_for_serialization(
                instance, "agent_definition"
            ).agent_definition
            if agent is None:
                return None
        return RunTestListAgentSerializer(agent, context=self.context).data


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
        """Resolve a version only inside the caller's organization and workspace."""
        if not instance.prompt_version_id:
            return None
        request = self.context.get("request")
        organization = getattr(request, "organization", None) or getattr(
            getattr(request, "user", None), "organization", None
        )
        if request is not None and organization is None:
            return None
        organization_id = (
            organization.pk if organization is not None else instance.organization_id
        )
        workspace = getattr(request, "workspace", None)
        workspace_id = workspace.pk if workspace is not None else instance.workspace_id

        if hasattr(instance, "_prompt_version_organization_id"):
            # The read helper joins only ownership keys alongside the version.
            # This keeps page/detail serialization query-free, including mixed FKs.
            if (
                instance._prompt_version_organization_id != organization_id
                or instance._prompt_version_workspace_id != workspace_id
            ):
                return None
            version = instance.prompt_version
        else:
            from model_hub.models.run_prompt import PromptVersion

            # Never traverse an uncached FK before proving its parent tenant.
            version = PromptVersion.objects.filter(
                id=instance.prompt_version_id,
                original_template__organization_id=organization_id,
                original_template__workspace_id=workspace_id,
                deleted=False,
            ).first()
        if version is None:
            return None
        return {
            "id": str(version.id),
            "template_version": version.template_version,
            "is_default": version.is_default,
            "commit_message": version.commit_message,
        }

    def _caller_tenant_ids(self, instance):
        """The (organization_id, workspace_id) pair get_prompt_version_detail uses.

        None denies: a request without an organization sees no versions.
        """
        request = self.context.get("request")
        organization = getattr(request, "organization", None) or getattr(
            getattr(request, "user", None), "organization", None
        )
        if request is not None and organization is None:
            return None
        workspace = getattr(request, "workspace", None)
        return (
            organization.pk if organization is not None else instance.organization_id,
            workspace.pk if workspace is not None else instance.workspace_id,
        )

    def _scope_agent_versions(self, instance):
        """Expose only versions inside the caller's tenant to every version reader.

        The latest-version pick below and the nested agent detail's active/latest
        credential pick both read ``_prefetched_versions``; without it they follow
        the definition FK alone. Install it before ``super()`` runs: query-free
        when the read helper prefetched, otherwise one exact-tenant query.
        """
        if instance.agent_definition_id is None:
            return
        agent = instance.agent_definition
        tenant = self._caller_tenant_ids(instance)
        if tenant is None:
            agent._prefetched_versions = []
            return
        organization_id, workspace_id = tenant
        versions = getattr(agent, "_prefetched_versions", None)
        if versions is None:
            versions = (
                AgentVersion.no_workspace_objects.filter(
                    agent_definition_id=agent.pk,
                    organization_id=organization_id,
                    workspace_id=workspace_id,
                    deleted=False,
                )
                .select_related("credentials")
                .order_by("-version_number")
            )
        agent._prefetched_versions = [
            version
            for version in versions
            if version.organization_id == organization_id
            and version.workspace_id == workspace_id
            and not version.deleted
        ]

    def to_representation(self, instance):
        """Custom representation to handle backward compatibility"""
        # Null both the raw FK and nested detail on a read-only copy. The shared
        # read queryset has already excluded foreign targets from its LEFT JOIN.
        instance = owned_run_for_serialization(instance, "simulator_agent")
        self._scope_agent_versions(instance)
        data = super().to_representation(instance)
        if data.get("prompt_version_detail") is None:
            # The raw PK field must not leak the version hidden by the detail field.
            data["prompt_version"] = None

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
                    snapshot = scrub_snapshot_secrets(
                        instance.agent_version.configuration_snapshot or {}
                    )
                    if "agent_type" not in snapshot:
                        snapshot = {
                            **snapshot,
                            "agent_type": instance.agent_definition.agent_type,
                        }
                    agent_version_data = {
                        "id": instance.agent_version.id,
                        "name": instance.agent_version.version_name,
                        "configuration_snapshot": snapshot_as_shown(
                            snapshot, instance.agent_definition
                        ),
                    }
                    try:
                        creds = instance.agent_version.credentials
                        agent_version_data["api_key"] = creds.get_masked_api_key()
                    except Exception:
                        pass
                    data["agent_version"] = agent_version_data
                else:
                    # _scope_agent_versions installed the caller-scoped list,
                    # ordered by -version_number, so the first item is latest.
                    # Never the FK-only agent_definition.latest_version property.
                    prefetched = (
                        getattr(instance.agent_definition, "_prefetched_versions", None)
                        or []
                    )
                    latest_version = prefetched[0] if prefetched else None

                    if latest_version:
                        snapshot = scrub_snapshot_secrets(
                            latest_version.configuration_snapshot or {}
                        )
                        if "agent_type" not in snapshot:
                            snapshot = {
                                **snapshot,
                                "agent_type": instance.agent_definition.agent_type,
                            }
                        agent_version_data = {
                            "id": latest_version.id,
                            "name": latest_version.version_name,
                            "configuration_snapshot": snapshot_as_shown(
                                snapshot, instance.agent_definition
                            ),
                        }
                        try:
                            creds = latest_version.credentials
                            agent_version_data["api_key"] = creds.get_masked_api_key()
                        except Exception:
                            pass
                        data["agent_version"] = agent_version_data
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
