import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import quote, urlencode
from uuid import UUID

import jwt
import requests
import structlog
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.utils import timezone

from accounts.models.organization import Organization
from accounts.models.workspace import Workspace
from integrations.models import (
    ConnectionStatus,
    IntegrationConnection,
    IntegrationPlatform,
)
from integrations.services.credentials import CredentialManager
from integrations.services.oauth_state import consume_state, issue_state, read_state
from integrations.services.source_hosts import (
    Branch,
    Repository,
    RepositoryPage,
    SourceHostError,
)

logger = structlog.get_logger(__name__)

API_URL = "https://api.github.com"
OAUTH_TOKEN_URL = "https://github.com/login/oauth/access_token"
API_VERSION = "2022-11-28"
HOST_URL = "https://github.com"
STATE_TTL_SECONDS = 600
STATE_SALT = "integrations.github.install.v1"
STATE_CACHE_PREFIX = "github-install-state"
LIST_CACHE_SECONDS = 300
PAGE_SIZE = 50
SUSPENDED_MESSAGE = "The GitHub App installation is suspended on GitHub."
_GITHUB_PER_PAGE = 100
_GITHUB_MAX_PAGES = 10

InstallOutcome = Literal["connected", "requested", "already_connected"]


class GitHubAppError(SourceHostError):
    pass


@dataclass(frozen=True)
class InstallResult:
    outcome: InstallOutcome
    connection_id: UUID | None = None


def _setting(name: str) -> str:
    return str(getattr(settings, name, "") or "")


def _send(method: str, url: str, **kwargs: Any) -> requests.Response:
    try:
        return requests.request(method, url, timeout=(5, 30), **kwargs)
    except requests.RequestException as exc:
        raise GitHubAppError(
            "github_unreachable",
            "GitHub could not be reached",
            retryable=True,
        ) from exc


def _headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": API_VERSION,
    }


def _json(response: requests.Response, *, code: str, message: str) -> object:
    if response.status_code != 200:
        raise GitHubAppError(code, message, retryable=response.status_code >= 500)
    try:
        return response.json()
    except ValueError as exc:
        raise GitHubAppError(code, message, retryable=True) from exc


def _paged(
    url: str, token: str, *, key: str | None, code: str, message: str
) -> Iterator[dict[str, Any]]:
    for page in range(1, _GITHUB_MAX_PAGES + 1):
        body = _json(
            _send(
                "get",
                url,
                headers=_headers(token),
                params={"per_page": _GITHUB_PER_PAGE, "page": page},
            ),
            code=code,
            message=message,
        )
        items = body.get(key) if key and isinstance(body, dict) else body
        if not isinstance(items, list):
            raise GitHubAppError(code, message)
        yield from (item for item in items if isinstance(item, dict))
        if len(items) < _GITHUB_PER_PAGE:
            return


def _app_jwt() -> str:
    app_id = _setting("GITHUB_APP_ID")
    private_key = _setting("GITHUB_APP_PRIVATE_KEY").replace("\\n", "\n")
    if not app_id or not private_key:
        raise GitHubAppError(
            "github_app_not_configured",
            "GitHub App credentials are not configured",
            status_code=503,
            retryable=True,
        )
    now = int(time.time())
    return jwt.encode(
        {"iat": now - 30, "exp": now + 540, "iss": app_id},
        private_key,
        algorithm="RS256",
    )


def installation_token(installation_id: str) -> str:
    response = _send(
        "post",
        f"{API_URL}/app/installations/{installation_id}/access_tokens",
        headers=_headers(_app_jwt()),
    )
    if response.status_code != 201:
        raise GitHubAppError(
            "github_installation_token_failed",
            f"GitHub rejected installation {installation_id}",
            retryable=response.status_code >= 500,
        )
    try:
        token = response.json().get("token")
    except (ValueError, AttributeError):
        token = None
    if not token:
        raise GitHubAppError(
            "github_installation_token_missing",
            "GitHub returned no installation token",
            retryable=True,
        )
    return str(token)


def revoke_installation_token(token: str) -> None:
    try:
        response = _send(
            "delete", f"{API_URL}/installation/token", headers=_headers(token)
        )
    except GitHubAppError:
        logger.warning("github_installation_token_not_revoked", exc_info=True)
        return
    if response.status_code != 204:
        logger.warning(
            "github_installation_token_not_revoked", status_code=response.status_code
        )


@contextmanager
def installation_credential(installation_id: str) -> Iterator[str]:
    token = installation_token(installation_id)
    try:
        yield token
    finally:
        revoke_installation_token(token)


def start_github_install(
    *, organization: Organization, workspace: Workspace, user: Any
) -> str:
    slug = _setting("GITHUB_APP_SLUG")
    if (
        not slug
        or not _setting("GITHUB_APP_CLIENT_ID")
        or not _setting("GITHUB_APP_CLIENT_SECRET")
    ):
        raise ValueError("The GitHub App is not configured.")
    if (
        workspace.organization_id != organization.id
        or not user.is_authenticated
        or not user.can_write_to_workspace(workspace)
    ):
        raise ValueError("A valid user and workspace are required to connect GitHub.")
    state = issue_state(
        {
            "user_id": str(user.pk),
            "organization_id": str(organization.pk),
            "workspace_id": str(workspace.pk),
        },
        salt=STATE_SALT,
        cache_prefix=STATE_CACHE_PREFIX,
        ttl_seconds=STATE_TTL_SECONDS,
        label="GitHub",
    )
    return f"{HOST_URL}/apps/{slug}/installations/new?{urlencode({'state': state})}"


def _revoke_user_token(token: str) -> None:
    client_id = _setting("GITHUB_APP_CLIENT_ID")
    try:
        response = _send(
            "delete",
            f"{API_URL}/applications/{client_id}/token",
            auth=(client_id, _setting("GITHUB_APP_CLIENT_SECRET")),
            headers={
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": API_VERSION,
            },
            json={"access_token": token},
        )
    except GitHubAppError:
        logger.warning("github_user_token_not_revoked", exc_info=True)
        return
    if response.status_code != 204:
        logger.warning(
            "github_user_token_not_revoked", status_code=response.status_code
        )


def _verify_user_installation(code: str, installation_id: str) -> None:
    body = _json(
        _send(
            "post",
            OAUTH_TOKEN_URL,
            headers={"Accept": "application/json"},
            data={
                "client_id": _setting("GITHUB_APP_CLIENT_ID"),
                "client_secret": _setting("GITHUB_APP_CLIENT_SECRET"),
                "code": code,
            },
        ),
        code="github_oauth_failed",
        message="GitHub did not authorize the user",
    )
    user_token = body.get("access_token") if isinstance(body, dict) else None
    if not user_token:
        raise GitHubAppError(
            "github_oauth_failed", "GitHub did not authorize the user", status_code=403
        )
    try:
        for installation in _paged(
            f"{API_URL}/user/installations",
            str(user_token),
            key="installations",
            code="github_installations_unreadable",
            message="GitHub did not list the user's installations",
        ):
            if str(installation.get("id")) != installation_id:
                continue
            if _administers(str(user_token), installation):
                return
            raise GitHubAppError(
                "github_installation_not_admin",
                "Only an admin of this GitHub account can connect its installation",
                status_code=403,
            )
    finally:
        _revoke_user_token(str(user_token))
    raise GitHubAppError(
        "github_installation_not_yours",
        "That GitHub App installation is not accessible to you",
        status_code=403,
    )


def _administers(user_token: str, installation: dict[str, Any]) -> bool:
    account = installation.get("account")
    if not isinstance(account, dict) or not account.get("login"):
        return False
    if account.get("type") == "Organization":
        response = _send(
            "get",
            f"{API_URL}/user/memberships/orgs/{quote(str(account['login']), safe='')}",
            headers=_headers(user_token),
        )
        if response.status_code in (403, 404):
            return False
        membership = _json(
            response,
            code="github_membership_unreadable",
            message="GitHub did not return the user's organization role",
        )
        return (
            isinstance(membership, dict)
            and membership.get("state") == "active"
            and membership.get("role") == "admin"
        )
    viewer = _json(
        _send("get", f"{API_URL}/user", headers=_headers(user_token)),
        code="github_user_unreadable",
        message="GitHub did not return the signed-in user",
    )
    return isinstance(viewer, dict) and viewer.get("id") == account.get("id")


def _installation_account(installation_id: str) -> tuple[str, bool]:
    body = _json(
        _send(
            "get",
            f"{API_URL}/app/installations/{installation_id}",
            headers=_headers(_app_jwt()),
        ),
        code="github_installation_unreadable",
        message=f"GitHub did not return installation {installation_id}",
    )
    account = body.get("account") if isinstance(body, dict) else None
    login = account.get("login") if isinstance(account, dict) else None
    if not login:
        raise GitHubAppError(
            "github_installation_unreadable",
            f"GitHub did not return installation {installation_id}",
        )
    return str(login), bool(body.get("suspended_at"))


def _save_connection(
    *,
    organization: Organization,
    workspace: Workspace,
    user: Any,
    installation_id: str,
    account: str,
    suspended: bool,
) -> InstallResult:
    status = ConnectionStatus.PAUSED if suspended else ConnectionStatus.ACTIVE
    status_message = SUSPENDED_MESSAGE if suspended else ""
    with transaction.atomic():
        existing = (
            IntegrationConnection.no_workspace_objects.select_for_update()
            .filter(
                organization=organization,
                workspace=workspace,
                platform=IntegrationPlatform.GITHUB,
            )
            .first()
        )
        if existing is not None and existing.external_project_name != installation_id:
            return InstallResult(outcome="already_connected", connection_id=existing.id)
        if existing is not None:
            existing.display_name = account
            existing.status = status
            existing.status_message = status_message
            existing.save(
                update_fields=["display_name", "status", "status_message", "updated_at"]
            )
            connection = existing
        else:
            connection = _create_connection(
                organization=organization,
                workspace=workspace,
                created_by=user,
                platform=IntegrationPlatform.GITHUB,
                display_name=account,
                host_url=HOST_URL,
                encrypted_credentials=CredentialManager.encrypt(
                    {"installation_id": installation_id}
                ),
                project=None,
                external_project_name=installation_id,
                status=status,
                status_message=status_message,
                backfill_completed=True,
            )
            if connection is None:
                return InstallResult(outcome="already_connected")
    logger.info(
        "github_connection_saved",
        connection_id=str(connection.id),
        status=status,
    )
    return InstallResult(outcome="connected", connection_id=connection.id)


def _create_connection(**fields: Any) -> IntegrationConnection | None:
    try:
        with transaction.atomic():
            return IntegrationConnection.no_workspace_objects.create(**fields)
    except IntegrityError:
        logger.info(
            "github_connection_raced",
            workspace_id=str(fields["workspace"].pk),
        )
        return None


def _consume(state: str) -> None:
    consume_state(
        state,
        salt=STATE_SALT,
        cache_prefix=STATE_CACHE_PREFIX,
        ttl_seconds=STATE_TTL_SECONDS,
        label="GitHub",
    )


def complete_github_install(
    *, state: str, setup_action: str, code: str, installation_id: str, user: Any
) -> InstallResult:
    """Finish an install the signed-in ``user`` started: the state names them, and
    only they may complete it, so a link forwarded to someone else binds nothing."""
    context = read_state(
        state,
        salt=STATE_SALT,
        cache_prefix=STATE_CACHE_PREFIX,
        ttl_seconds=STATE_TTL_SECONDS,
        label="GitHub",
    )
    if str(getattr(user, "pk", "")) != str(context.get("user_id") or ""):
        raise GitHubAppError(
            "github_install_not_yours",
            "This GitHub installation was started by another user.",
            status_code=403,
        )
    user_model = get_user_model()
    try:
        user = user_model.objects.get(pk=context["user_id"])
        organization = Organization.objects.get(pk=context["organization_id"])
        workspace = Workspace.objects.get(
            pk=context["workspace_id"], organization=organization, is_active=True
        )
    except (
        user_model.DoesNotExist,
        Organization.DoesNotExist,
        Workspace.DoesNotExist,
    ) as exc:
        raise ValueError(
            "The GitHub installation no longer matches a workspace."
        ) from exc
    if not user.is_active or not user.can_write_to_workspace(workspace):
        raise ValueError("The user can no longer connect GitHub in this workspace.")
    if setup_action == "request":
        _consume(state)
        return InstallResult(outcome="requested")
    if not code or not installation_id.isdigit():
        raise ValueError("GitHub did not return an installation.")
    _verify_user_installation(code, installation_id)
    account, suspended = _installation_account(installation_id)
    _consume(state)
    return _save_connection(
        organization=organization,
        workspace=workspace,
        user=user,
        installation_id=installation_id,
        account=account,
        suspended=suspended,
    )


def installation_connected(
    *, organization_id: UUID, workspace_id: UUID | None, installation_id: str
) -> bool:
    return IntegrationConnection.no_workspace_objects.filter(
        organization_id=organization_id,
        workspace_id=workspace_id,
        platform=IntegrationPlatform.GITHUB,
        external_project_name=installation_id,
        status=ConnectionStatus.ACTIVE,
    ).exists()


def _repositories_key(installation_id: str) -> str:
    return f"github-installation-repositories:{installation_id}"


def forget_repositories(installation_id: str) -> None:
    cache.delete(_repositories_key(installation_id))


def _installation_suspended(installation_id: str) -> bool:
    try:
        _account, suspended = _installation_account(installation_id)
    except GitHubAppError:
        logger.warning(
            "github_installation_state_unreadable",
            installation_id=installation_id,
            exc_info=True,
        )
        return True
    return suspended


def apply_installation_event(*, installation_id: str, action: str) -> None:
    connections = IntegrationConnection.no_workspace_objects.filter(
        platform=IntegrationPlatform.GITHUB, external_project_name=installation_id
    )
    now = timezone.now()
    if action == "deleted":
        changed = connections.update(deleted=True, deleted_at=now, updated_at=now)
    elif action in ("suspend", "unsuspend"):
        if _installation_suspended(installation_id):
            changed = connections.update(
                status=ConnectionStatus.PAUSED,
                status_message=SUSPENDED_MESSAGE,
                updated_at=now,
            )
        else:
            changed = connections.update(
                status=ConnectionStatus.ACTIVE, status_message="", updated_at=now
            )
    else:
        return
    forget_repositories(installation_id)
    logger.info(
        "github_installation_event_applied",
        installation_id=installation_id,
        action=action,
        connections=changed,
    )


def _installation_repositories(installation_id: str) -> list[Repository]:
    key = _repositories_key(installation_id)
    cached = cache.get(key)
    if cached is not None:
        return cached
    with installation_credential(installation_id) as token:
        repositories = [
            Repository(
                id=int(item["id"]),
                full_name=str(item["full_name"]),
                default_branch=str(item.get("default_branch") or ""),
                private=bool(item.get("private")),
            )
            for item in _paged(
                f"{API_URL}/installation/repositories",
                token,
                key="repositories",
                code="github_repositories_unreadable",
                message="GitHub did not list the installation's repositories",
            )
            if item.get("id") and item.get("full_name")
        ]
    cache.set(key, repositories, LIST_CACHE_SECONDS)
    return repositories


def list_repositories(installation_id: str, *, query: str, page: int) -> RepositoryPage:
    needle = query.strip().lower()
    matches = [
        repository
        for repository in _installation_repositories(installation_id)
        if needle in repository.full_name.lower()
    ]
    start = (page - 1) * PAGE_SIZE
    return RepositoryPage(
        repositories=matches[start : start + PAGE_SIZE],
        next_page=page + 1 if start + PAGE_SIZE < len(matches) else None,
    )


def list_branches(installation_id: str, *, repository_id: int) -> list[Branch] | None:
    repository = next(
        (
            candidate
            for candidate in _installation_repositories(installation_id)
            if candidate.id == repository_id
        ),
        None,
    )
    if repository is None:
        return None
    key = f"github-repository-branches:{installation_id}:{repository_id}"
    cached = cache.get(key)
    if cached is not None:
        return cached
    with installation_credential(installation_id) as token:
        names = [
            str(item["name"])
            for item in _paged(
                f"{API_URL}/repositories/{repository_id}/branches",
                token,
                key=None,
                code="github_branches_unreadable",
                message="GitHub did not list the repository's branches",
            )
            if item.get("name")
        ]
    branches = sorted(
        (
            Branch(name=name, is_default=name == repository.default_branch)
            for name in names
        ),
        key=lambda branch: not branch.is_default,
    )
    cache.set(key, branches, LIST_CACHE_SECONDS)
    return branches


def branch_head(
    installation_id: str,
    *,
    repository: str,
    repository_id: int | None,
    ref: str | None,
) -> str | None:
    """The commit ``ref`` (the default branch when ``None``) points at, or None when
    the repository or branch is gone."""
    path = (
        f"repositories/{int(repository_id)}"
        if repository_id is not None
        else f"repos/{quote(repository, safe='/')}"
    )
    with installation_credential(installation_id) as token:
        response = _send(
            "get",
            f"{API_URL}/{path}/commits/{quote(ref or 'HEAD', safe='/')}",
            headers={**_headers(token), "Accept": "application/vnd.github.sha"},
        )
    if response.status_code in (404, 422):
        return None
    if response.status_code != 200:
        raise GitHubAppError(
            "github_head_unreadable",
            "GitHub did not return the branch's head",
            retryable=response.status_code >= 500,
        )
    return response.text.strip() or None


class GitHubSourceHost:
    def list_repositories(
        self, connection: IntegrationConnection, *, query: str, page: int
    ) -> RepositoryPage:
        return list_repositories(
            connection.external_project_name, query=query, page=page
        )

    def list_branches(
        self, connection: IntegrationConnection, *, repository_id: int
    ) -> list[Branch] | None:
        return list_branches(
            connection.external_project_name, repository_id=repository_id
        )
