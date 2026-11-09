from typing import Any
from uuid import UUID

from django.db.models import (
    Case,
    CharField,
    Count,
    F,
    OuterRef,
    QuerySet,
    Subquery,
    When,
    Window,
)
from django.db.models.fields.json import KT
from django.db.models.functions import Coalesce, RowNumber

from simulate.models import (
    AgentDefinition,
    AgentVersion,
    AgentVersionSetup,
    HostedHarnessJob,
    TestExecution,
)
from simulate.services.harness_environment import environment_name, run_summaries

ENVIRONMENTS_PER_ROW = 5


def _environments() -> QuerySet[HostedHarnessJob]:
    # An environment of a deleted test is gone from the page, so it is not
    # counted, as recent runs leave its runs out.
    return HostedHarnessJob.no_workspace_objects.filter(
        run_test__agent_definition=OuterRef("pk"),
        run_test__deleted=False,
        environment__isnull=True,
        deleted=False,
    )


def annotate_agent_rows(
    queryset: QuerySet[AgentDefinition],
) -> QuerySet[AgentDefinition]:
    environments = _environments()
    first_environment = environments.order_by("created_at")
    stored_setups = AgentVersionSetup.no_workspace_objects.filter(
        agent_version__agent_definition=OuterRef("pk"),
        agent_version__deleted=False,
        status=AgentVersionSetup.Status.STORED,
    ).order_by("-agent_version__version_number")
    versions = AgentVersion.objects.filter(
        agent_definition=OuterRef("pk"), deleted=False
    )
    source_kind = KT("payload__source__kind")
    source_kinds = first_environment.annotate(kind=source_kind).values("kind")
    return queryset.annotate(
        _agent_kind=Subquery(stored_setups.values("agent_kind")[:1]),
        _build_source_kind=Subquery(source_kinds[:1]),
        _version_count=Coalesce(
            Subquery(
                versions.values("agent_definition")
                .annotate(total=Count("id"))
                .values("total")
            ),
            0,
        ),
        _environment_count=Coalesce(
            Subquery(
                environments.values("run_test__agent_definition")
                .annotate(total=Count("id"))
                .values("total")
            ),
            0,
        ),
    ).annotate(
        _source=Case(
            When(_agent_kind="source", then=F("_build_source_kind")),
            default=F("provider"),
            output_field=CharField(),
        )
    )


def environment_refs(agents: list[AgentDefinition]) -> dict[UUID, list[dict[str, str]]]:
    refs: dict[UUID, list[dict[str, str]]] = {}
    jobs = (
        HostedHarnessJob.no_workspace_objects.filter(
            run_test__agent_definition__in=[agent.id for agent in agents],
            run_test__deleted=False,
            environment__isnull=True,
            deleted=False,
        )
        .annotate(
            position=Window(
                RowNumber(),
                partition_by=F("run_test__agent_definition_id"),
                order_by=[F("created_at").asc(), F("id").asc()],
            )
        )
        .filter(position__lte=ENVIRONMENTS_PER_ROW)
        .select_related("run_test")
        .order_by("created_at", "id")
    )
    for job in jobs:
        refs.setdefault(job.run_test.agent_definition_id, []).append(
            {"id": str(job.id), "name": environment_name(job)}
        )
    return refs


def observability_on(agent: AgentDefinition) -> bool:
    row = agent.observability_provider
    return bool(row and row.enabled and not row.project.deleted)


def recent_agent_runs(agent: AgentDefinition, limit: int) -> list[dict[str, Any]]:
    # A run of a deleted test or environment links to a page that no longer
    # exists, and the environment count already leaves those environments out.
    executions = list(
        TestExecution.no_workspace_objects.filter(
            agent_definition=agent, deleted=False, run_test__deleted=False
        )
        .exclude(hosted_harness_job__deleted=True)
        .exclude(hosted_harness_job__environment__deleted=True)
        .select_related("agent_version", "run_test")
        .order_by("-created_at")[:limit]
    )
    summaries = run_summaries(executions)
    return [summaries[execution.id] for execution in executions]
