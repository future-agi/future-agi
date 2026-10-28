"""How "Fix my agent" handles each kind of agent: where its editable source comes from, how a
candidate becomes an agent version, how a check run runs it, and how it becomes the default.

The services pick the kind once per call through ``agent_kind`` and never branch on it. A new
kind is one more class in ``AGENT_KINDS``.
"""

import io
from typing import Any, Protocol

from simulate.models import (
    AgentFixCandidate,
    AgentFixRun,
    AgentVersion,
    HarnessAgentVersionRuntime,
    HostedHarnessJob,
)
from simulate.services.harness_agent_versions import (
    create_agent_version,
    make_default,
    preflight_agent_version,
)
from simulate.services.harness_versioning.assembly import certify_enabled
from simulate.services.harness_versioning.versions import versioning_enabled
from simulate.services.hosted_harness import HostedHarnessError, VersionPicks
from simulate.services.hosted_harness_gateway import (
    HostedSourceAcquirer,
    store_source_archive,
)


class AgentKind(Protocol):
    def handles(self, environment: HostedHarnessJob) -> bool: ...

    def require_available(self) -> None:
        """Refuse, with the reason, when this deployment cannot fix this kind of agent."""
        ...

    def base_version_id(self, source_run: HostedHarnessJob) -> Any: ...

    def source_archive(self, fix_run: AgentFixRun) -> bytes:
        """The agent's editable files as a `source/`-rooted tar.gz."""
        ...

    def prepare_candidate(
        self, fix_run: AgentFixRun, files: dict[str, bytes], attempt: int
    ) -> dict[str, Any]:
        """Validate and store what the candidate's version needs, before any lock is taken."""
        ...

    def candidate_version(
        self, fix_run: AgentFixRun, prepared: dict[str, Any], attempt: int
    ) -> AgentVersion:
        """A new version of the agent from what was prepared, never the default. Runs inside
        the caller's transaction: database work only."""
        ...

    def check(self, version: AgentVersion) -> str:
        """Make the version runnable and return its check status ("passed" when it is)."""
        ...

    def run_options(
        self, fix_run: AgentFixRun, agent_version_id: str
    ) -> dict[str, Any]:
        """The arguments that make a check run run this version."""
        ...

    def make_default(self, candidate: AgentFixCandidate, *, user: Any) -> str: ...


class VersionedSourceAgent:
    """An uploaded or GitHub agent on an environment with agent versions: candidates are
    archive versions, picked per run with the failed run's environment and scenario versions."""

    EDITABLE = {
        HarnessAgentVersionRuntime.SourceKind.ARCHIVE,
        HarnessAgentVersionRuntime.SourceKind.GITHUB,
    }

    def handles(self, environment: HostedHarnessJob) -> bool:
        return HarnessAgentVersionRuntime.no_workspace_objects.filter(
            agent_version_id=environment.agent_version_id, source_kind__in=self.EDITABLE
        ).exists()

    def require_available(self) -> None:
        if not versioning_enabled() or not certify_enabled():
            raise HostedHarnessError(
                "fix_unavailable",
                "Fix my agent needs agent versions and new version builds turned on",
                status_code=422,
            )

    def base_version_id(self, source_run: HostedHarnessJob) -> Any:
        return source_run.agent_version_id or source_run.environment.agent_version_id

    def source_archive(self, fix_run: AgentFixRun) -> bytes:
        # The source as the failed run ran it.
        return HostedSourceAcquirer().acquire(fix_run.source_run)[0]

    def prepare_candidate(
        self, fix_run: AgentFixRun, files: dict[str, bytes], attempt: int
    ) -> dict[str, Any]:
        stored = store_source_archive(
            fix_run.organization,
            [io.BytesIO(data) for data in files.values()],
            list(files),
            f"fix-{fix_run.id}-{attempt}",
        )
        return {"source_id": stored["source_id"]}

    def candidate_version(
        self, fix_run: AgentFixRun, prepared: dict[str, Any], attempt: int
    ) -> AgentVersion:
        base = fix_run.base_agent_version
        return create_agent_version(
            base.agent_definition,
            parent=base,
            env=fix_run.environment,
            source={"kind": "archive", "archive_artifact_id": prepared["source_id"]},
            trigger=HarnessAgentVersionRuntime.Trigger.FIX,
            activate=False,
        )

    def check(self, version: AgentVersion) -> str:
        # A run can pick only a version that passed its check (credentials and source); a
        # pending check is run again, so a retried upload finishes what a failed one began.
        return preflight_agent_version(version, activate=False).status

    def run_options(
        self, fix_run: AgentFixRun, agent_version_id: str
    ) -> dict[str, Any]:
        source_run = fix_run.source_run
        return {
            "picks": VersionPicks(
                agent_version_id=agent_version_id,
                environment_version_id=source_run.environment_version_id,
                suite_version_id=source_run.suite_version_id,
            )
        }

    def make_default(self, candidate: AgentFixCandidate, *, user: Any) -> str:
        return make_default(
            candidate.agent_version, candidate.fix_run.environment, user=user
        )


AGENT_KINDS: list[AgentKind] = [VersionedSourceAgent()]


def agent_kind(environment: HostedHarnessJob) -> AgentKind:
    for kind in AGENT_KINDS:
        if kind.handles(environment):
            return kind
    raise HostedHarnessError(
        "fix_agent_not_editable",
        "Fix my agent can change an uploaded or GitHub agent; this agent's prompt and tools"
        " run outside Future AGI",
        status_code=422,
    )
