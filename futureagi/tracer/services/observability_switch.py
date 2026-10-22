"""The one place observability is turned on or off for an agent.

Every caller that switches an agent's observability goes through
``set_agent_observability``. A turn-on is refused, with one reason per field,
when the agent lacks what tracing needs: a key, the provider's agent id, or, for
an agent whose own code runs in our sandbox, an instrumenter in that code.
Otherwise it creates an observe project and a provider row. A new row always
gets a project of its own: when an observe project of the agent's name already
exists in the workspace, a short tag from the agent's id is added to the name.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING

import structlog
from django.db import connection, transaction
from django.utils import timezone

from accounts.models.workspace import Workspace
from simulate.models import AgentDefinition
from simulate.services import agent_setup
from tfc.utils.api_errors import build_error_envelope
from tracer.models.observability_provider import (
    ObservabilityProvider,
    ProviderChoices,
)
from tracer.models.project import Project
from tracer.services.simulation_project import agent_id_in_simulation_project_name
from tracer.utils.observability_provider import create_observability_provider

if TYPE_CHECKING:
    from accounts.models.organization import Organization

logger = structlog.get_logger(__name__)

REFUSAL_CODE = "observability_prerequisite_missing"


@dataclass(frozen=True)
class MissingPrerequisite:
    code: str
    field: str
    message: str


PREREQUISITES = {
    "credential": MissingPrerequisite(
        "credential_missing",
        "api_key",
        "API key is required when observability is enabled",
    ),
    "provider_agent_id": MissingPrerequisite(
        "provider_agent_id_missing",
        "assistant_id",
        "Assistant ID is required when observability is enabled",
    ),
    "instrumentation": MissingPrerequisite(
        "instrumentation_missing",
        "instrumentation",
        "The agent's code carries no OpenTelemetry or traceAI instrumentation "
        "the sandbox can configure (an HTTP exporter, not gRPC alone); add one "
        "and author the agent again",
    ),
}


class ObservabilityRefused(Exception):
    def __init__(self, missing):
        missing = tuple(missing)
        if not missing:
            raise ValueError("a refusal names at least one missing prerequisite")
        super().__init__("; ".join(entry.message for entry in missing))
        self.missing = missing


class ObservabilityProjectError(Exception):
    pass


PROJECT_NAME_LIMIT = 255
SHORT_TAG_LENGTH = 8


# Callers catch the reader's own error; the switch passes it through unchanged.
AgentNotFound = agent_setup.AgentNotFound


class ProviderListNotPublished(Exception):
    pass


def _agent_summary(agent_definition_id) -> agent_setup.AgentSummary:
    # The one place the switch reads an agent, so tests can stand a summary in.
    return agent_setup.agent_summary(agent_definition_id)


def _read_provider_list():
    # Until a provider list is published, no provider counts as listed.
    raise ProviderListNotPublished()


def _is_dead(row: ObservabilityProvider) -> bool:
    return row.deleted or row.project.deleted


def observability_is_on(agent_definition: AgentDefinition) -> bool:
    # The link may still point at a deleted row or project, which counts as off.
    row = agent_definition.observability_provider
    return row is not None and row.enabled and not _is_dead(row)


def snapshot_as_shown(snapshot, agent_definition: AgentDefinition):
    """A stored version snapshot as every read of it shows it.

    A snapshot written while the project was live still says on. A form that
    starts from it sends the on back, which would make a new project after the
    user deleted the old one. So a stored on reads off while the link is off or
    dead. A stored off is never turned on. The stored value is not rewritten.
    """
    if not isinstance(snapshot, dict) or not snapshot.get("observability_enabled"):
        return snapshot
    if observability_is_on(agent_definition):
        return snapshot
    return {**snapshot, "observability_enabled": False}


def agent_is_source(agent_definition: AgentDefinition) -> bool:
    return _agent_summary(agent_definition.id).kind == "source"


def _row_provider(
    agent_definition: AgentDefinition, summary: agent_setup.AgentSummary
) -> str:
    # An unlisted code agent's own provider word may fail the row's validation.
    column = agent_definition.provider
    if summary.kind != "source":
        return column
    try:
        listed = summary.provider in _read_provider_list().providers
    except ProviderListNotPublished:
        return ProviderChoices.OTHERS.value
    return column if listed else ProviderChoices.OTHERS.value


def check_observability_prerequisites(agent_definition: AgentDefinition) -> None:
    _check_prerequisites(agent_definition, _agent_summary(agent_definition.id))


def _check_prerequisites(
    agent_definition: AgentDefinition, summary: agent_setup.AgentSummary
) -> None:
    agent_id = str(agent_definition.id)
    try:
        listed = summary.provider in _read_provider_list().providers
    except ProviderListNotPublished:
        logger.warning(
            "observability_switch_unchecked",
            agent_definition_id=agent_id,
            provider=_logged_provider(summary.provider),
            reason="provider_list_not_published",
        )
        listed = False
        if summary.kind != "source":
            return
    if listed:
        missing = tuple(
            PREREQUISITES[word] for word in summary.missing if word in PREREQUISITES
        )
        if missing:
            _refuse(agent_id, summary.provider, missing)
        if summary.credential_id is None and summary.key_present:
            logger.warning(
                "observability_switch_plain_column_key",
                agent_definition_id=agent_id,
                provider=_logged_provider(summary.provider),
            )
        return
    if summary.kind == "source":
        if summary.instrumentation not in ("traceai", "opentelemetry"):
            _refuse(agent_id, summary.provider, (PREREQUISITES["instrumentation"],))
        return


def _logged_provider(provider: str) -> str:
    # The provider column is free text; only our own words may reach a log line.
    return provider if provider in ProviderChoices.values else "unknown"


def _refuse(agent_id: str, provider: str, missing: tuple[MissingPrerequisite, ...]):
    logger.info(
        "observability_switch_refused",
        agent_definition_id=agent_id,
        provider=_logged_provider(provider),
        codes=[entry.code for entry in missing],
    )
    raise ObservabilityRefused(missing)


def _project_error(agent_id: str, reason: str) -> ObservabilityProjectError:
    # The reason is one of our own words; the agent id is the only other field.
    logger.error(
        "observability_switch_project_error",
        agent_definition_id=agent_id,
        reason=reason,
    )
    return ObservabilityProjectError("observability project could not be created")


def _resolved_workspace_id(organization: Organization, workspace, agent_id: str) -> str:
    # The project lands in the organization's default workspace when none is
    # given, so the name is checked there too.
    if workspace:
        try:
            return str(uuid.UUID(str(getattr(workspace, "id", workspace))))
        except ValueError as error:
            raise _project_error(agent_id, "bad_workspace_id") from error
    try:
        default = Workspace.objects.get(
            organization=organization, is_default=True, is_active=True
        )
    except Workspace.DoesNotExist as error:
        raise _project_error(agent_id, "no_default_workspace") from error
    return str(default.id)


def _workspace_lock_key(organization: Organization, workspace_id: str) -> int:
    # Postgres takes a signed 64-bit key.
    digest = hashlib.blake2b(
        f"observability_project:{organization.id}:{workspace_id}".encode(),
        digest_size=8,
    ).digest()
    return int.from_bytes(digest, "big", signed=True)


def _lock_workspace_projects(organization: Organization, workspace_id: str):
    # Held until the surrounding transaction ends, so a second agent turned on
    # in the same workspace waits here and then sees the first one's project.
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_advisory_xact_lock(%s)",
            [_workspace_lock_key(organization, workspace_id)],
        )


def _project_name_taken(
    organization: Organization, workspace_id: str, name: str
) -> bool:
    # The unique constraint's scope, read on the primary: a lagging replica
    # could call a just-created name free.
    return (
        Project.no_workspace_objects.using("default")
        .filter(
            name=name,
            trace_type="observe",
            organization=organization,
            workspace_id=workspace_id,
            deleted=False,
        )
        .exists()
    )


def _tagged_name(name: str, tag: str) -> str:
    suffix = f" {tag}"
    return name[: PROJECT_NAME_LIMIT - len(suffix)] + suffix


def _agent_tags(agent_definition: AgentDefinition) -> tuple[str, str]:
    agent_hex = str(agent_definition.id).replace("-", "")
    return agent_hex[:SHORT_TAG_LENGTH], agent_hex


def _project_name_for_new_row(
    agent_definition: AgentDefinition, organization: Organization, workspace
) -> str:
    """The agent's name if no project of that name exists, else the name tagged.

    Must run inside the transaction that creates the row, which keeps the lock.
    """
    agent_id = str(agent_definition.id)
    workspace_id = _resolved_workspace_id(organization, workspace, agent_id)
    _lock_workspace_projects(organization, workspace_id)
    name = agent_definition.agent_name
    candidates = (
        name,
        *(_tagged_name(name, tag) for tag in _agent_tags(agent_definition)),
    )
    for candidate in candidates:
        if agent_id_in_simulation_project_name(candidate) is not None:
            # A name reserved for a hidden project is refused at the create, and
            # the name check cannot see hidden projects, so it is never offered.
            continue
        if not _project_name_taken(organization, workspace_id, candidate):
            return candidate
    raise _project_error(agent_id, "names_exhausted")


def set_agent_observability(
    *,
    agent_definition: AgentDefinition,
    enabled: bool,
    user_id: str | None,
    organization: Organization,
    workspace: Workspace | str | None,
) -> ObservabilityProvider | None:
    row = agent_definition.observability_provider
    if row is not None and _is_dead(row):
        # A deleted row or project is never reused. Cleared only while the link
        # is still the dead one, so a new link another request made meanwhile
        # is kept and switched instead.
        cleared = AgentDefinition.no_workspace_objects.filter(
            pk=agent_definition.pk, observability_provider_id=row.pk
        ).update(observability_provider=None, updated_at=timezone.now())
        if not cleared:
            # The primary: a lagging replica would still show the dead link.
            agent_definition.refresh_from_db(
                using="default", fields=["observability_provider"]
            )
            if agent_definition.observability_provider_id != row.pk:
                return set_agent_observability(
                    agent_definition=agent_definition,
                    enabled=enabled,
                    user_id=user_id,
                    organization=organization,
                    workspace=workspace,
                )
        agent_definition.observability_provider = None
        # The user deleted the project, so the link is off. Turning it on again
        # makes a new project and row, as a deleted row alone does. A save with
        # false makes nothing. The turn-on path below locks the agent row and
        # re-reads the link, so two quick turn-ons still make one project.
        row = None
    if row is not None:
        if enabled and not row.enabled:
            check_observability_prerequisites(agent_definition)
        # Only these columns, so any other column written meanwhile is kept.
        row.enabled = enabled
        row.save(update_fields=["enabled", "updated_at"])
        return row
    if not enabled:
        return None
    # Read once: the check and the row's provider word both need it.
    summary = _agent_summary(agent_definition.id)
    _check_prerequisites(agent_definition, summary)
    with transaction.atomic():
        # A second turn-on of this agent that reaches here finds the first one's row.
        locked = AgentDefinition.no_workspace_objects.select_for_update(
            no_key=True
        ).get(pk=agent_definition.pk)
        if locked.observability_provider_id is not None:
            agent_definition.observability_provider = locked.observability_provider
            return set_agent_observability(
                agent_definition=agent_definition,
                enabled=True,
                user_id=user_id,
                organization=organization,
                workspace=workspace,
            )
        row = create_observability_provider(
            enabled=True,
            user_id=user_id,
            organization=organization,
            workspace=workspace,
            project_name=_project_name_for_new_row(
                agent_definition, organization, workspace
            ),
            provider=_row_provider(agent_definition, summary),
        )
        if not isinstance(row, ObservabilityProvider):
            # The helper returns its errors instead of raising. Their text can
            # carry the free-text provider word or name, so only names and codes.
            logger.error(
                "observability_switch_project_error",
                agent_definition_id=str(agent_definition.id),
                **_error_summary(row),
            )
            raise ObservabilityProjectError(
                "observability project could not be created"
            )
        agent_definition.observability_provider = row
        # Only the link, so the caller's stale copy of other fields is not written.
        agent_definition.save(update_fields=["observability_provider", "updated_at"])
    return row


def _error_summary(errors) -> dict:
    if not isinstance(errors, dict):
        return {"error_type": type(errors).__name__}
    details = errors.get("details") if "error" in errors else errors
    if not isinstance(details, dict):
        return {"error_type": type(details).__name__}
    return {
        "fields": sorted(str(field) for field in details),
        "codes": sorted(
            {
                str(getattr(item, "code", "unknown"))
                for items in details.values()
                for item in (items if isinstance(items, list) else [items])
            }
        ),
    }


def refusal_body(error: ObservabilityRefused) -> dict:
    reasons = "; ".join(entry.message for entry in error.missing)
    return build_error_envelope(
        f"Observability cannot be turned on: {reasons}",
        status_code=400,
        code=REFUSAL_CODE,
        details={entry.field: [entry.message] for entry in error.missing},
    )
