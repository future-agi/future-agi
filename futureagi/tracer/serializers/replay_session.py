from rest_framework import serializers

from simulate.models import AgentDefinition
from simulate.models.run_test import RunTest
from simulate.models.scenarios import Scenarios
from tracer.models.project import Project
from tracer.models.replay_session import ReplaySession, ReplayType
from tracer.utils.workspace_scope import project_queryset_for_request


class AgentDefinitionNestedSerializer(serializers.ModelSerializer):
    """Nested serializer for agent definition in replay session responses."""

    version_name = serializers.SerializerMethodField()

    class Meta:
        model = AgentDefinition
        fields = [
            "id",
            "agent_name",
            "agent_type",
            "description",
            "version_name",
        ]

    def get_version_name(self, obj):
        """Get the version name from the latest version."""
        version = obj.latest_version
        return version.version_name if version else None


class ScenarioNestedSerializer(serializers.ModelSerializer):
    """Nested serializer for scenario in replay session responses."""

    class Meta:
        model = Scenarios
        fields = [
            "id",
            "name",
            "status",
            "description",
        ]


class RunTestNestedSerializer(serializers.ModelSerializer):
    """Nested serializer for run test in replay session responses."""

    class Meta:
        model = RunTest
        fields = [
            "id",
            "name",
            "description",
        ]


class ReplaySessionSerializer(serializers.ModelSerializer):
    """Serializer for retrieving replay session with nested related data."""

    agent_definition = AgentDefinitionNestedSerializer(read_only=True)
    scenario = ScenarioNestedSerializer(read_only=True)
    run_test = RunTestNestedSerializer(read_only=True)

    class Meta:
        model = ReplaySession
        fields = [
            "id",
            "project",
            "replay_type",
            "ids",
            "select_all",
            "current_step",
            "agent_definition",
            "scenario",
            "run_test",
        ]
        read_only_fields = [
            "id",
            "current_step",
            "agent_definition",
            "scenario",
            "run_test",
        ]


class ReplaySessionResponseSerializer(serializers.ModelSerializer):
    """Lightweight serializer for create/generate-scenario responses with just IDs."""

    agent_definition_id = serializers.UUIDField(
        source="agent_definition.id", read_only=True, allow_null=True
    )
    agent_definition_latest_version_id = serializers.SerializerMethodField()
    scenario_id = serializers.UUIDField(
        source="scenario.id", read_only=True, allow_null=True
    )
    run_test_id = serializers.UUIDField(
        source="run_test.id", read_only=True, allow_null=True
    )

    class Meta:
        model = ReplaySession
        fields = [
            "id",
            "project",
            "replay_type",
            "ids",
            "select_all",
            "current_step",
            "agent_definition_id",
            "agent_definition_latest_version_id",
            "scenario_id",
            "run_test_id",
        ]

    def get_agent_definition_latest_version_id(self, obj):
        """Get the latest version ID from the agent definition."""
        if obj.agent_definition:
            latest_version = obj.agent_definition.latest_version
            return latest_version.id if latest_version else None
        return None


class ReplaySessionListSerializer(serializers.ModelSerializer):
    """Lightweight serializer for listing replay sessions."""

    project_name = serializers.CharField(source="project.name", read_only=True)

    class Meta:
        model = ReplaySession
        fields = [
            "id",
            "project",
            "project_name",
            "replay_type",
            "current_step",
            "created_at",
        ]


class CreateReplaySessionSerializer(serializers.Serializer):
    """Serializer for creating a new replay session."""

    project_id = serializers.UUIDField(required=True)
    replay_type = serializers.ChoiceField(
        choices=ReplayType.choices,
        default=ReplayType.SESSION,
    )
    ids = serializers.ListField(
        child=serializers.UUIDField(),
        required=False,
        default=list,
    )
    select_all = serializers.BooleanField(default=False)

    def validate(self, attrs):
        """Validate that either ids or select_all is provided."""
        ids = attrs.get("ids", [])
        select_all = attrs.get("select_all", False)

        if not ids and not select_all:
            raise serializers.ValidationError(
                "Either 'ids' must be provided or 'select_all' must be True."
            )

        project = self.context.get("project")
        if project is None:
            raise serializers.ValidationError("Project not found.")

        # Validate every supplied ID, including mixed lists and select_all=True.
        # A UUID is not authorization; foreign and missing IDs share one error.
        if ids:
            from tracer.services.clickhouse.v2 import get_reader
            from tracer.services.clickhouse.v2.trace_session_dict_reader import (
                resolve_session_fields,
            )

            requested_ids = {str(identifier) for identifier in ids}
            project_id = str(project.id)
            # CH is authoritative: post-flip sources need not have PG mirrors.
            # Resolve the entire batch inside the already-authorized project.
            if attrs["replay_type"] == ReplayType.TRACE:
                with get_reader() as reader:
                    projects = reader.newest_trace_projects(requested_ids, [project_id])
            else:
                projects = {
                    sid: record["project_id"]
                    for sid, record in resolve_session_fields(
                        requested_ids,
                        project_id=project_id,
                        include_overlay=False,
                    ).items()
                }
            found_ids = {sid for sid, pid in projects.items() if str(pid) == project_id}
            if found_ids != requested_ids:
                raise serializers.ValidationError({"ids": "Replay source not found."})

        return attrs

    def validate_project_id(self, value):
        """Validate project exists and belongs to user's organization. Cache project instance."""
        request = self.context.get("request")
        if not request:
            raise serializers.ValidationError("Project not found.")

        try:
            project = project_queryset_for_request(request).get(id=value)
            # Cache project instance in context for use in view
            self.context["project"] = project
        except Project.DoesNotExist:
            raise serializers.ValidationError("Project not found.") from None

        return value


class GenerateScenarioSerializer(serializers.Serializer):
    """Serializer for generate-scenario action."""

    agent_name = serializers.CharField(max_length=255, required=True)
    agent_description = serializers.CharField(
        required=False, allow_blank=True, default=""
    )
    scenario_name = serializers.CharField(max_length=255, required=True)
    agent_type = serializers.ChoiceField(
        choices=["text", "voice"],
        default="text",
    )
    no_of_rows = serializers.IntegerField(default=20, min_value=1, max_value=1000)
    personas = serializers.ListField(
        child=serializers.UUIDField(),
        required=False,
        default=list,
    )
    custom_columns = serializers.ListField(
        child=serializers.DictField(),
        required=False,
        default=list,
    )
    graph = serializers.DictField(required=False, allow_null=True)
    generate_graph = serializers.BooleanField(default=True)

    def validate_custom_columns(self, value):
        """Validate custom columns don't have duplicate names."""
        if not value:
            return value

        column_names = [col.get("name") for col in value if col.get("name")]
        seen = set()
        duplicates = set()

        for name in column_names:
            if name in seen:
                duplicates.add(name)
            seen.add(name)

        if duplicates:
            raise serializers.ValidationError(
                f"Duplicate column names found: {', '.join(duplicates)}"
            )

        return value
