"""Shared load reads; telemetry never stands in for Postgres identity or access."""

from datetime import timedelta

from django.db.models import Q
from django.utils import timezone

from model_hub.models.skills_hub import HubSkillUsageDaily
from model_hub.services.skills_hub.errors import SkillsHubError
from model_hub.services.skills_hub.runtime_usage import (
    MAX_PROJECTS,
    agent_identity,
    read_runtime_statement,
)
from model_hub.services.skills_hub.usage import can_record
from tracer.models.project import Project
from tracer.services.clickhouse.read_budget import ReadDeadline
from tracer.services.clickhouse.v2.query_builders.skill_runtime import (
    RuntimeReadSession,
    SkillRuntimeQueryBuilder,
)
from tracer.services.clickhouse.v2.query_service import V2AnalyticsQueryService


def latest_load(*, resource, workspace, since):
    now = timezone.now()
    if since > now or since < now - timedelta(days=30):
        raise SkillsHubError(
            "invalid_runtime_query", "Choose a start within the last 30 days.", status=400
        )
    # The caller has authorized the resource through get_resource. Match Observe's
    # default-workspace compatibility rule, without widening to another tenant.
    scope = Q(workspace_id=workspace.pk)
    if workspace.is_default:
        scope |= Q(
            workspace__is_default=True,
            workspace__organization_id=resource.organization_id,
        ) | Q(workspace__isnull=True)
    projects = dict(Project.no_workspace_objects.filter(
        scope, organization_id=resource.organization_id,
        deleted=False, trace_type="observe",
    ).order_by("id").values_list("id", "name")[:MAX_PROJECTS + 1])
    if len(projects) > MAX_PROJECTS:
        raise SkillsHubError(
            "runtime_read_too_large", "Too many projects for this load read.", status=503
        )
    projects = {str(key): value for key, value in projects.items()}
    versions = {str(key): value for key, value in resource.versions.values_list("id", "number")}
    bucket = HubSkillUsageDaily.objects.filter(skill=resource).order_by("-last_at", "-id").first()
    fetch = None if bucket is None else {
        "sdk_version": bucket.sdk_version,
        "tracing_mode": bucket.tracing_mode,
        "can_record": can_record(bucket.sdk_version, bucket.tracing_mode),
        "client": bucket.channel,
    }
    result = {"load": None, "fetch": fetch}
    if not projects or not versions or since == now:
        return result
    builder = SkillRuntimeQueryBuilder(project_ids=projects, start=since, end=now)
    session = RuntimeReadSession(V2AnalyticsQueryService(), deadline=ReadDeadline.start(3000))
    rows = read_runtime_statement(session, builder.build_latest_load(
        resource_id=resource.pk, kind=resource.kind, version_ids=versions,
    ), max_rows=1)
    if rows:
        row = rows[0]
        project_id = str(row["project_id"])
        result["load"] = {
            "version": versions[str(row["version_id"])],
            "agent": agent_identity(
                project_id=project_id, project_name=projects[project_id],
                service_name=row["service_name"], agent_name=row["agent_name"],
            ),
            "project": {"id": project_id, "name": projects[project_id]},
            "received_at": row["start_time"],
        }
    return result
