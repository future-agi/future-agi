"""Authentication identities used by internal simulation services, and the calls a
coding agent's API key may make."""

from __future__ import annotations

import secrets
from typing import TYPE_CHECKING, NamedTuple
from uuid import UUID

from django.conf import settings
from rest_framework import status
from rest_framework.authentication import BaseAuthentication, get_authorization_header
from rest_framework.exceptions import APIException, NotFound, PermissionDenied

if TYPE_CHECKING:
    from accounts.models import OrgApiKey
    from simulate.models import HostedHarnessJob


class InternalServiceUser:
    """Minimal DRF identity for a caller holding ``INTERNAL_API_SECRET``."""

    is_authenticated = True
    is_internal_service = True


class InternalServiceAuthentication(BaseAuthentication):
    """Authenticate a trusted service without binding it to a tenant."""

    def authenticate(self, request):
        parts = get_authorization_header(request).split()
        if len(parts) != 2 or parts[0].lower() != b"bearer":
            return None

        configured_secret = getattr(settings, "INTERNAL_API_SECRET", "")
        if not configured_secret:
            return None

        try:
            supplied_secret = parts[1].decode("utf-8")
        except UnicodeDecodeError:
            return None

        if not secrets.compare_digest(supplied_secret, configured_secret):
            return None
        # Authentication has already consumed the bearer credential. Do not
        # retain the fleet-wide secret on request.auth for downstream code or
        # exception/logging integrations to accidentally expose.
        return InternalServiceUser(), None


class HarnessAttemptUser:
    is_authenticated = True
    is_harness_attempt = True

    def __init__(self, attempt) -> None:
        self.attempt = attempt
        self.organization = attempt.job.organization


class HarnessAttemptAuthentication(BaseAuthentication):
    """Authenticate a bearer and fence against one registered hosted attempt."""

    def authenticate(self, request):
        from django.utils import timezone

        from simulate.models import HostedHarnessAttempt
        from simulate.services.hosted_harness import HostedHarnessError, hash_secret

        resolver_match = getattr(request, "resolver_match", None)
        kwargs = getattr(resolver_match, "kwargs", {}) if resolver_match else {}
        attempt_id = kwargs.get("pk") or kwargs.get("attempt_id")
        if not attempt_id:
            raise HostedHarnessError(
                "attempt_mismatch",
                "attempt id is missing from the request path",
                status_code=403,
            )
        parts = get_authorization_header(request).split()
        if len(parts) != 2 or parts[0].lower() != b"bearer":
            raise HostedHarnessError(
                "authentication_required",
                "a harness attempt bearer is required",
                status_code=401,
            )
        try:
            supplied_token = parts[1].decode("utf-8")
        except UnicodeDecodeError as exc:
            raise HostedHarnessError(
                "authentication_invalid",
                "attempt bearer is not valid UTF-8",
                status_code=401,
            ) from exc
        fence = request.headers.get("X-Harness-Fence", "")
        try:
            attempt = HostedHarnessAttempt.no_workspace_objects.select_related(
                "job", "job__organization"
            ).get(id=attempt_id)
        except HostedHarnessAttempt.DoesNotExist as exc:
            raise HostedHarnessError(
                "attempt_not_found",
                "attempt was not found",
                status_code=404,
            ) from exc
        if not secrets.compare_digest(attempt.token_hash, hash_secret(supplied_token)):
            raise HostedHarnessError(
                "authentication_invalid",
                "attempt bearer is invalid",
                status_code=401,
            )
        if not fence or not secrets.compare_digest(
            attempt.fence_hash, hash_secret(fence)
        ):
            raise HostedHarnessError(
                "attempt_fenced",
                "attempt fence is invalid",
                status_code=403,
            )
        if timezone.now() >= attempt.expires_at:
            raise HostedHarnessError(
                "attempt_expired",
                "attempt bearer has expired",
                status_code=401,
            )
        if attempt.attempt_number < attempt.job.current_attempt_number:
            raise HostedHarnessError(
                "attempt_superseded",
                "attempt has been superseded",
                status_code=409,
            )
        return HarnessAttemptUser(attempt), attempt


class AgentFixEngineUser:
    is_authenticated = True
    is_agent_fix_engine = True

    def __init__(self, fix_run) -> None:
        self.fix_run = fix_run
        self.organization = fix_run.organization


class AgentFixEngineAuthentication(BaseAuthentication):
    """Authenticate the improvement engine's sandbox against its one fix run.

    Every refusal is a 401: a sandbox whose run ended, or whose credentials a relaunch
    rotated, has nothing left to do and exits.
    """

    def authenticate(self, request):
        from django.utils import timezone

        from simulate.models import AgentFixRun
        from simulate.services.hosted_harness import HostedHarnessError, hash_secret

        def refuse(code: str, message: str) -> HostedHarnessError:
            return HostedHarnessError(code, message, status_code=401)

        resolver_match = getattr(request, "resolver_match", None)
        fix_run_id = (getattr(resolver_match, "kwargs", None) or {}).get("pk")
        parts = get_authorization_header(request).split()
        if not fix_run_id or len(parts) != 2 or parts[0].lower() != b"bearer":
            raise refuse("authentication_required", "a fix run bearer is required")
        try:
            token = parts[1].decode("utf-8")
            fix_run = AgentFixRun.no_workspace_objects.select_related(
                "organization"
            ).get(id=fix_run_id)
        except (UnicodeDecodeError, ValueError, AgentFixRun.DoesNotExist) as exc:
            raise refuse(
                "authentication_invalid", "the fix run bearer is invalid"
            ) from exc
        if not fix_run.token_hash or not secrets.compare_digest(
            fix_run.token_hash, hash_secret(token)
        ):
            raise refuse("authentication_invalid", "the fix run bearer is invalid")
        if (
            fix_run.token_expires_at is None
            or timezone.now() >= fix_run.token_expires_at
        ):
            raise refuse("authentication_expired", "the fix run bearer has expired")
        return AgentFixEngineUser(fix_run), fix_run


class HarnessConversationUser:
    is_authenticated = True
    is_harness_conversation = True

    def __init__(self, conversation) -> None:
        self.conversation = conversation
        self.organization = conversation.organization


class HarnessConversationAuthentication(BaseAuthentication):
    """Authenticate the outbound guest holding one conversation lease."""

    def authenticate(self, request):
        from django.utils import timezone

        from simulate.models import HostedHarnessConversationLease
        from simulate.services.hosted_harness import HostedHarnessError, hash_secret

        resolver_match = getattr(request, "resolver_match", None)
        kwargs = getattr(resolver_match, "kwargs", {}) if resolver_match else {}
        conversation_id = kwargs.get("pk") or kwargs.get("conversation_id")
        if not conversation_id:
            raise HostedHarnessError(
                "conversation_mismatch",
                "conversation id is missing from the request path",
                status_code=403,
            )
        parts = get_authorization_header(request).split()
        if len(parts) != 2 or parts[0].lower() != b"bearer":
            raise HostedHarnessError(
                "authentication_required",
                "a harness conversation bearer is required",
                status_code=401,
            )
        try:
            supplied_token = parts[1].decode("utf-8")
        except UnicodeDecodeError as exc:
            raise HostedHarnessError(
                "authentication_invalid",
                "conversation bearer is not valid UTF-8",
                status_code=401,
            ) from exc
        fence = request.headers.get("X-Harness-Conversation-Fence", "")
        try:
            lease = HostedHarnessConversationLease.no_workspace_objects.select_related(
                "conversation", "conversation__organization"
            ).get(conversation_id=conversation_id)
        except HostedHarnessConversationLease.DoesNotExist as exc:
            raise HostedHarnessError(
                "conversation_lease_not_found",
                "conversation lease was not found",
                status_code=404,
            ) from exc
        if not secrets.compare_digest(lease.token_hash, hash_secret(supplied_token)):
            raise HostedHarnessError(
                "authentication_invalid",
                "conversation bearer is invalid",
                status_code=401,
            )
        if not fence or not secrets.compare_digest(
            lease.fence_hash, hash_secret(fence)
        ):
            raise HostedHarnessError(
                "conversation_fenced",
                "conversation fence is invalid",
                status_code=403,
            )
        if timezone.now() >= lease.expires_at:
            raise HostedHarnessError(
                "conversation_lease_expired",
                "conversation lease has expired",
                status_code=401,
            )
        if lease.state in {
            HostedHarnessConversationLease.State.EXPIRED,
            HostedHarnessConversationLease.State.RELEASED,
        }:
            raise HostedHarnessError(
                "conversation_lease_inactive",
                "conversation lease is no longer active",
                status_code=409,
            )
        conversation = lease.conversation
        return HarnessConversationUser(conversation), conversation


class _Target(NamedTuple):
    # The path kwarg holding the id the call acts on.
    kwarg: str
    # The HostedHarnessJob lookup from that id to a run; None when the id is the
    # environment itself.
    run_lookup: str | None


_ENVIRONMENT = _Target("pk", None)
_JOB = _Target("pk", "id")
_EXECUTION = _Target("test_execution_id", "test_execution_id")
_CALL = _Target("call_execution_id", "test_execution__calls__id")

# Every call a coding agent's key may make, by resolved view name and method, and
# what in its path ties it to an environment. Anything not listed is refused, so a
# new route stays closed to the key until it is added here. A ``None`` target
# names no environment: the source upload only stores an archive, and the agent
# version made from it names the environment.
CODING_AGENT_ROUTES: dict[tuple[str, str], _Target | None] = {
    ("simulate:harness-job-source-upload", "POST"): None,
    ("simulate:harness-environment-detail", "GET"): _ENVIRONMENT,
    ("simulate:harness-environment-versions", "GET"): _ENVIRONMENT,
    ("simulate:harness-environment-create-agent-version", "POST"): _ENVIRONMENT,
    ("simulate:harness-environment-suite-members", "GET"): _ENVIRONMENT,
    ("simulate:harness-environment-run-plan", "GET"): _ENVIRONMENT,
    ("simulate:harness-environment-run", "POST"): _ENVIRONMENT,
    ("simulate:harness-environment-verify", "POST"): _ENVIRONMENT,
    ("simulate:harness-environment-verify-detail", "GET"): _ENVIRONMENT,
    ("simulate:harness-job-detail", "GET"): _JOB,
    ("simulate:test-execution-detail", "GET"): _EXECUTION,
    ("simulate:v3-test-execution-calls", "GET"): _EXECUTION,
    ("simulate:v3-call-execution-detail", "GET"): _CALL,
}


class CodingAgentNeedsSuiteVersion(APIException):
    """No case can be held back from a coding agent whose environment has no suite
    version, or on an install with versioning off; its key is refused, so it is never
    served every case."""

    status_code = status.HTTP_409_CONFLICT
    default_code = "coding_agent_needs_suite_version"
    default_detail = (
        "A coding agent key needs an environment with a scenario version and"
        " versioning on, to hold cases back from it"
    )


def _can_hold_back(environment_id: UUID) -> bool:
    """Whether the environment has a suite version to hold cases back from."""
    from simulate.models import HostedHarnessJob
    from simulate.services.harness_versioning.versions import versioning_enabled

    return (
        versioning_enabled()
        and HostedHarnessJob.no_workspace_objects.filter(
            id=environment_id, suite_version__isnull=False
        ).exists()
    )


def refuse_coding_agent_request(request, org_api_key: OrgApiKey) -> None:
    """Raise unless ``request`` is a call in ``CODING_AGENT_ROUTES`` that this coding
    agent key may make.

    Fails closed: a request with no resolved route is refused, and so is every call
    while the key's environment has nothing to hold back (409). Another environment
    is a 403. A run, execution or call id is a 404 unless it belongs to a user run
    of the key's environment that this key started, as ``coding_agent_key_id`` in
    the run's metadata records. A person's runs of the same environment can
    include cases the coding agent must not read, and a hidden run reads as one
    that does not exist.
    """
    environment_id = org_api_key.simulation_environment_id
    match = getattr(request, "resolver_match", None)
    route = (getattr(match, "view_name", None), getattr(request, "method", None))
    if route not in CODING_AGENT_ROUTES or environment_id is None:
        raise PermissionDenied(
            "A coding agent key cannot make this call",
            code="coding_agent_route_refused",
        )
    if not _can_hold_back(environment_id):
        raise CodingAgentNeedsSuiteVersion()
    target = CODING_AGENT_ROUTES[route]
    if target is None:
        return
    try:
        target_id = UUID(str(match.kwargs[target.kwarg]))
    except (KeyError, ValueError):
        target_id = None
    if target.run_lookup is None:
        if target_id != environment_id:
            raise PermissionDenied(
                "A coding agent key acts on its own environment only",
                code="coding_agent_environment_refused",
            )
        return

    from simulate.models import HostedHarnessJob
    from simulate.selectors.runs import user_runs

    if (
        target_id is None
        or not HostedHarnessJob.no_workspace_objects.filter(
            user_runs(),
            environment_id=environment_id,
            payload__metadata__coding_agent_key_id=str(org_api_key.id),
            **{target.run_lookup: target_id},
        ).exists()
    ):
        raise NotFound()


def bindable_environment(request, environment_id: UUID) -> HostedHarnessJob | None:
    """The environment, if the caller of ``request`` can see it, that a coding agent
    key they mint may be bound to; same tenancy as the environment routes. One it
    could hold nothing back on is refused (409), as every call of the key would be."""
    from simulate.models import HostedHarnessJob
    from simulate.services.harness_provider import request_organization, scope_jobs

    environment = scope_jobs(
        HostedHarnessJob.no_workspace_objects.filter(
            id=environment_id,
            organization=request_organization(request),
            environment__isnull=True,
        ),
        request,
    ).first()
    if environment is not None and not _can_hold_back(environment.id):
        raise CodingAgentNeedsSuiteVersion()
    return environment
