from django.db.models import Q
from rest_framework import serializers

from tracer.models.project import Project
from tracer.models.project_version import ProjectVersion
from tracer.serializers.filters import (
    MetricSortParamListQueryParamField,
    StrictInputSerializer,
    filter_list_query_param_field,
)


class ProjectVersionSerializer(serializers.ModelSerializer):
    """A named version (run/experiment) within a trace project — each version groups the spans/traces produced by one experiment run so they can be compared side by side. Use these tools to manage experiment versions of a project: list/read via list_project_version / get_project_version, create a new run with create_project_version, and rename or update its metadata via update_project_version. The auto-incrementing `version` label (v1, v2, ...) is read-only; `avg_eval_score` and `eval_tags` summarize the evaluation results for the run."""

    project = serializers.PrimaryKeyRelatedField(
        queryset=Project.objects.all(),
        many=False,
        help_text="UUID of the trace project this version belongs to (from list_projects).",
    )

    class Meta:
        model = ProjectVersion
        fields = [
            "id",
            "project",
            "name",
            "metadata",
            "start_time",
            "end_time",
            "error",
            "eval_tags",
            "avg_eval_score",
            "version",
            "annotations",
        ]
        read_only_fields = ["version"]
        extra_kwargs = {
            "id": {"help_text": "UUID of this project version (from list_project_version)."},
            "name": {"help_text": "Human-readable name for this experiment version/run."},
            "metadata": {"help_text": "Arbitrary JSON metadata describing this run (e.g. config, parameters)."},
            "start_time": {"help_text": "Timestamp when this version's run started."},
            "end_time": {"help_text": "Timestamp when this version's run finished."},
            "error": {"help_text": "JSON error details if the run failed, otherwise null."},
            "eval_tags": {"help_text": "List of evaluation tags computed for this run."},
            "avg_eval_score": {"help_text": "Average evaluation score across this run's spans/traces."},
            "version": {"help_text": "Read-only auto-assigned version label (v1, v2, v3, ...), unique per project."},
            "annotations": {"help_text": "UUID of the annotations group associated with this version, if any."},
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        request = self.context.get("request")
        if not request:
            return

        organization = getattr(request, "organization", None) or getattr(
            request.user, "organization", None
        )
        if not organization:
            return

        project_scope = Q(organization=organization)
        workspace = getattr(request, "workspace", None)
        if workspace:
            if getattr(workspace, "is_default", False):
                project_scope &= (
                    Q(workspace=workspace)
                    | Q(workspace__is_default=True, workspace__organization=organization)
                    | Q(workspace__isnull=True)
                )
            else:
                project_scope &= Q(workspace=workspace)

        project_manager = getattr(Project, "no_workspace_objects", Project.objects)
        self.fields["project"].queryset = project_manager.filter(
            project_scope, deleted=False
        )


class ProjectVersionRunsQuerySerializer(StrictInputSerializer):
    project_id = serializers.UUIDField()
    filters = filter_list_query_param_field(required=False, default=list)
    sort_params = MetricSortParamListQueryParamField(required=False, default=list)
    page_number = serializers.IntegerField(required=False, default=0, min_value=0)
    page_size = serializers.IntegerField(
        required=False, default=30, min_value=1, max_value=500
    )
