import operator
from functools import reduce
from typing import TYPE_CHECKING, Any, TypedDict
from uuid import UUID

from django.db.models import (
    BooleanField,
    Case,
    CharField,
    Count,
    Exists,
    F,
    OuterRef,
    Q,
    QuerySet,
    Subquery,
    Value,
    When,
    Window,
)
from django.db.models.fields.json import KT
from django.db.models.functions import Coalesce, RowNumber

from simulate.models import (
    AgentDefinition,
    AgentVersion,
    AgentVersionSetup,
    HarnessAgentVersionRuntime,
    HostedHarnessJob,
)
from simulate.selectors.runs import user_executions
from simulate.services.harness_environment import environment_name, run_summaries

if TYPE_CHECKING:
    from django_stubs_ext import WithAnnotations

ENVIRONMENTS_PER_ROW = 5

_OBSERVE_LINKED = Q(
    observability_provider__enabled=True,
    observability_provider__project__deleted=False,
)
_ENVIRONMENT_CONDITIONS = {
    "with": Q(_in_environment=True),
    "without": Q(_in_environment=False),
}
_OBSERVE_CONDITIONS = {"linked": Q(_observed=True), "not_linked": Q(_observed=False)}
_SOURCE_VALUES = {
    "github": ("github",),
    "archive": ("archive",),
    "livekit": ("livekit", "livekit_bridge"),
    "vapi": ("vapi",),
    "retell": ("retell",),
    "bland": ("bland",),
    "eleven_labs": ("eleven_labs",),
}
_FILTER_CONDITIONS = {
    "agent_type": lambda value: Q(agent_type=value),
    "source": lambda value: Q(_source__in=_SOURCE_VALUES[value]),
    "environments": _ENVIRONMENT_CONDITIONS.__getitem__,
    "observe": _OBSERVE_CONDITIONS.__getitem__,
}

AGENT_ENVIRONMENT_FILTERS = tuple(_ENVIRONMENT_CONDITIONS)
AGENT_OBSERVE_FILTERS = tuple(_OBSERVE_CONDITIONS)
AGENT_SOURCE_FILTERS = tuple(_SOURCE_VALUES)


class _JobOwner(TypedDict):
    _owner: Any


def _environment_jobs() -> "QuerySet[WithAnnotations[HostedHarnessJob, _JobOwner]]":
    # An environment of a deleted test is gone from the page, so it is not
    # counted, as recent runs leave its runs out. One not yet given a test
    # still holds the agent through the version it pins.
    return HostedHarnessJob.no_workspace_objects.filter(
        environment__isnull=True, deleted=False
    ).annotate(
        _owner=Case(
            When(run_test__isnull=True, then=F("agent_version__agent_definition_id")),
            When(run_test__deleted=False, then=F("run_test__agent_definition_id")),
            default=None,
        )
    )


def _environments() -> "QuerySet[WithAnnotations[HostedHarnessJob, _JobOwner]]":
    return _environment_jobs().filter(_owner=OuterRef("pk"))


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
    version_source_kinds = (
        HarnessAgentVersionRuntime.no_workspace_objects.filter(
            agent_definition=OuterRef("pk"),
            agent_version__deleted=False,
        )
        .order_by("-agent_version__version_number")
        .values("source_kind")
    )
    return queryset.annotate(
        _agent_kind=Subquery(stored_setups.values("agent_kind")[:1]),
        _version_source_kind=Subquery(version_source_kinds[:1]),
        _build_source_kind=Coalesce(
            Subquery(source_kinds[:1]),
            Subquery(version_source_kinds[:1]),
            output_field=CharField(),
        ),
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
                environments.values("_owner")
                .annotate(total=Count("id"))
                .values("total")
            ),
            0,
        ),
        _in_environment=Exists(environments),
    ).annotate(
        _source=Case(
            When(_agent_kind="source", then=F("_build_source_kind")),
            When(
                _agent_kind__isnull=True,
                _version_source_kind__in=[
                    HarnessAgentVersionRuntime.SourceKind.GITHUB,
                    HarnessAgentVersionRuntime.SourceKind.ARCHIVE,
                ],
                then=F("_version_source_kind"),
            ),
            default=F("provider"),
            output_field=CharField(),
        ),
        _observed=Case(
            When(_OBSERVE_LINKED, then=Value(True)),
            default=Value(False),
            output_field=BooleanField(),
        ),
    )


def filter_agent_rows(
    queryset: QuerySet[AgentDefinition], filters: dict[str, list[str]]
) -> QuerySet[AgentDefinition]:
    for field, condition in _FILTER_CONDITIONS.items():
        if values := filters.get(field):
            queryset = queryset.filter(reduce(operator.or_, map(condition, values)))
    return queryset


def environment_refs(agents: list[AgentDefinition]) -> dict[UUID, list[dict[str, str]]]:
    refs: dict[UUID, list[dict[str, str]]] = {}
    jobs = (
        _environment_jobs()
        .filter(_owner__in=[agent.id for agent in agents])
        .annotate(
            position=Window(
                RowNumber(),
                partition_by=F("_owner"),
                order_by=[F("created_at").asc(), F("id").asc()],
            )
        )
        .filter(position__lte=ENVIRONMENTS_PER_ROW)
        .order_by("created_at", "id")
    )
    for job in jobs:
        refs.setdefault(job._owner, []).append(
            {"id": str(job.id), "name": environment_name(job)}
        )
    return refs


def observability_on(agent: AgentDefinition) -> bool:
    row = agent.observability_provider
    return bool(row and row.enabled and not row.deleted and not row.project.deleted)


def observe_project(agent: AgentDefinition, kind: str | None) -> dict[str, str] | None:
    """The Observe project the agent's production traces or calls land in.

    A code agent's is the one the user linked; a hosted agent's is the one its
    calls are pulled into while observability is on.
    """
    if kind == "source":
        project = agent.production_project
        if project is None or project.deleted:
            return None
    elif observability_on(agent) and agent.observability_provider is not None:
        project = agent.observability_provider.project
    else:
        return None
    return {"id": str(project.id), "name": project.name}


def environment_counts(agent_ids: list[UUID]) -> dict[UUID, int]:
    """How many environments test each agent, keyed by agent id; absent when none."""
    rows = (
        _environment_jobs()
        .filter(_owner__in=agent_ids)
        .values("_owner")
        .annotate(total=Count("id"))
    )
    return {row["_owner"]: row["total"] for row in rows}


def version_list_context(versions: list[AgentVersion]) -> dict[str, Any]:
    """What the versions list needs beside each row: its side row and how many
    environments have it as their default."""
    from simulate.models import HarnessAgentVersionRuntime

    ids = [version.id for version in versions]
    runtimes = {
        runtime.agent_version_id: runtime
        for runtime in HarnessAgentVersionRuntime.no_workspace_objects.filter(
            agent_version_id__in=ids
        ).defer("credential_blob", "diff")
    }
    counts = (
        HostedHarnessJob.no_workspace_objects.filter(
            agent_version_id__in=ids,
            run_test__deleted=False,
            environment__isnull=True,
            deleted=False,
        )
        .values("agent_version_id")
        .annotate(total=Count("id"))
    )
    return {
        "runtimes": runtimes,
        "environments_count": {row["agent_version_id"]: row["total"] for row in counts},
    }


def environments_testing(version: AgentVersion) -> int:
    """How many environments have this version as their default."""
    return HostedHarnessJob.no_workspace_objects.filter(
        agent_version=version,
        run_test__deleted=False,
        environment__isnull=True,
        deleted=False,
    ).count()


def recent_agent_runs(agent: AgentDefinition, limit: int) -> list[dict[str, Any]]:
    # A run of a deleted test or environment links to a page that no longer
    # exists, and the environment count already leaves those environments out.
    executions = list(
        user_executions()
        .filter(agent_definition=agent, run_test__deleted=False)
        .exclude(hosted_harness_job__deleted=True)
        .exclude(hosted_harness_job__environment__deleted=True)
        .select_related("agent_version", "run_test")
        .order_by("-created_at")[:limit]
    )
    summaries = run_summaries(executions)
    return [summaries[execution.id] for execution in executions]
