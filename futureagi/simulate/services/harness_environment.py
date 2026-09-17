"""Read model for the environments surface.

An environment is, today, the job that built it: the world itself is a tarball in
object storage addressed only by ``payload.metadata.authoring_object_key``. This
module is the projection of a job into the row the environments list renders, so
the surface can exist before the environment becomes a table of its own.

Everything here is derived from columns and stage outputs already stored. Nothing
new is computed at read time, and no field is invented when its source is absent
-- an absent value is ``None`` rather than a plausible-looking zero.
"""

from __future__ import annotations

from typing import Any

from django.db.models import Count, F, Prefetch, Q, QuerySet

from simulate.models import HostedHarnessJob, HostedHarnessStageOutput

# The four states the list's status pill knows. The pipeline reports far finer
# stages; the raw one travels alongside as ``stage`` so a caller that wants
# progress can still derive it.
STATUS_BUILDING = "building"
STATUS_RUNNING = "running"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"

_TERMINAL_FAILED = frozenset(
    {HostedHarnessJob.State.FAILED, HostedHarnessJob.State.CANCELED}
)

# A run that is winding down, or being cancelled, is still a run from the
# outside; reporting it as "building" would say the environment is not ready.
_RUN_STATES = frozenset(
    {
        HostedHarnessJob.State.RUNNING,
        HostedHarnessJob.State.FINALIZING,
        HostedHarnessJob.State.CLEANING_UP,
    }
)

# Transports that carry audio. A source that serves both voice and HTTP lists
# both connectors, and voice wins: the recording is the thing that would be lost
# if the environment were rendered as chat.
_VOICE_CONNECTORS = frozenset({"livekit", "vapi", "retell"})

AGENT_TYPE_VOICE = "voice"
AGENT_TYPE_CHAT = "chat"


def annotate_for_list(
    queryset: QuerySet[HostedHarnessJob],
) -> QuerySet[HostedHarnessJob]:
    """Annotate an already-scoped job queryset for the list, without N+1 reads.

    Tenancy is the caller's to apply, using the same helper the jobs list uses,
    so an environment can never be visible on one surface and absent from the
    other.
    """
    return (
        queryset.annotate(
            # An annotation walks the relation directly, so the soft-delete
            # filter the model's manager would apply has to be restated here.
            registered_scenarios=Count(
                "scenario_registrations",
                filter=Q(scenario_registrations__deleted=False),
                distinct=True,
            )
        )
        .prefetch_related(
            # Only the contract snapshot is needed, and only for two fields, so
            # the environment and scenario snapshots are left in the database.
            Prefetch(
                "normalized_stage_outputs",
                queryset=HostedHarnessStageOutput.no_workspace_objects.filter(
                    kind="contract", deleted=False
                ),
                to_attr="contract_outputs",
            )
        )
        # Newest change first. The column is nullable for any row a backfill
        # could not date, and Postgres sorts nulls first on DESC, which would
        # float exactly the least-informative rows to the top of the list.
        .order_by(F("content_updated_at").desc(nulls_last=True), "-created_at")
    )


def environment_row(job: HostedHarnessJob) -> dict[str, Any]:
    """One list row. Safe to call on a job that has never finished building."""
    contract = _contract_data(job)
    return {
        "id": str(job.id),
        "name": environment_name(job),
        "description": _description(contract),
        "source_kind": _source_kind(job),
        "agent_type": agent_type(job, contract),
        "status": status_for(job),
        "stage": job.current_stage,
        "scenario_count": scenario_count(job),
        "tools_count": _tools_count(contract),
        "last_updated": _isoformat(job.content_updated_at or job.created_at),
        "created_at": _isoformat(job.created_at),
    }


def environment_name(job: HostedHarnessJob) -> str:
    """The name a person recognises, in the order the run detail already uses."""
    payload = job.payload or {}
    metadata = payload.get("metadata") or {}
    for key in ("agent_name", "name"):
        value = str(metadata.get(key) or "").strip()
        if value:
            return value[:255]
    source = payload.get("source") or {}
    repository = str(source.get("repository") or "").strip()
    if repository:
        return repository.rsplit("/", 1)[-1] or repository
    return f"simulation-{str(job.id)[:8]}"


def status_for(job: HostedHarnessJob) -> str:
    """Collapse the lifecycle into the four states the list draws.

    Everything before the calls start is "building", because from the outside
    acquiring a source and seeding a world are the same activity: the
    environment is not ready yet.
    """
    if job.state == HostedHarnessJob.State.COMPLETED:
        return STATUS_COMPLETED
    if job.state in _TERMINAL_FAILED:
        return STATUS_FAILED
    if job.state in _RUN_STATES:
        return STATUS_RUNNING
    return STATUS_BUILDING


def agent_type(job: HostedHarnessJob, contract: dict[str, Any] | None = None) -> str:
    """Voice or chat, from the strongest evidence available.

    The user's explicit connector wins. Under ``auto`` it is genuinely unknown
    until authoring reports a modality, and chat is the safe default: a chat run
    rendered as chat merely looks plainer, whereas a voice run rendered as chat
    loses its recordings.
    """
    agent = (job.payload or {}).get("agent") or {}
    connector = str(agent.get("connector") or "").strip().lower()
    if connector in _VOICE_CONNECTORS:
        return AGENT_TYPE_VOICE
    if connector and connector != "auto":
        return AGENT_TYPE_CHAT
    if contract is None:
        contract = _contract_data(job)
    modality = str((contract or {}).get("modality") or "").strip().lower()
    return AGENT_TYPE_VOICE if modality == "voice" else AGENT_TYPE_CHAT


def scenario_count(job: HostedHarnessJob) -> int:
    """How many scenarios this environment has, not how many were asked for.

    Before authoring registers them the only number available is the request, so
    that is what is reported; afterwards the registrations are the truth, and
    they can differ when a suite is extended.
    """
    registered = getattr(job, "registered_scenarios", None)
    if registered is None:
        registered = job.scenario_registrations.filter(deleted=False).count()
    return int(registered or 0) or int(job.scenario_count or 0)


def _source_kind(job: HostedHarnessJob) -> str:
    source = (job.payload or {}).get("source") or {}
    return str(source.get("kind") or "") or "unknown"


def _contract_data(job: HostedHarnessJob) -> dict[str, Any]:
    """The contract snapshot, preferring the verified copy over the live one."""
    prefetched = getattr(job, "contract_outputs", None)
    if prefetched:
        data = prefetched[0].data
        if isinstance(data, dict):
            return data
    for output in job.stage_outputs or []:
        if not isinstance(output, dict) or output.get("kind") != "contract":
            continue
        data = output.get("data")
        if isinstance(data, dict):
            return data
    return {}


def _description(contract: dict[str, Any]) -> str | None:
    one_liner = str(contract.get("one_liner") or "").strip()
    return one_liner[:1000] or None


def _tools_count(contract: dict[str, Any]) -> int | None:
    tools = contract.get("tools")
    return len(tools) if isinstance(tools, list) else None


def _isoformat(value) -> str | None:
    return value.isoformat() if value else None
