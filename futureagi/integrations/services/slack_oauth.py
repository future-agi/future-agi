"""Short-lived, single-use Slack OAuth installation state."""

import secrets
from urllib.parse import urlencode

import requests
from django.conf import settings
from django.core import signing
from django.core.cache import cache

from integrations.models import IntegrationConnection, IntegrationPlatform

AUTHORIZE_URL = "https://slack.com/oauth/v2/authorize"
ACCESS_URL = "https://slack.com/api/oauth.v2.access"
SCOPES = "channels:read,channels:join,groups:read,chat:write"
STATE_TTL_SECONDS = 600
STATE_SALT = "integrations.slack.oauth.v1"


def slack_redirect_uri() -> str:
    return getattr(settings, "SLACK_REDIRECT_URI", "") or (
        f"{settings.BASE_URL.rstrip('/')}/integrations/slack/callback/"
    )


def _state_cache_key(nonce: str) -> str:
    return f"slack-oauth-state:{nonce}"


def start_slack_install(*, organization, workspace, user, connection_id=None) -> dict:
    """Return a Slack consent URL for this specific user/org/workspace.

    ``connection_id`` rotates an existing Slack installation after consent.
    State is signed, expires after ten minutes, and is consumed once.
    """
    client_id = getattr(settings, "SLACK_CLIENT_ID", "")
    if not client_id or not getattr(settings, "SLACK_CLIENT_SECRET", ""):
        raise ValueError("Slack OAuth is not configured.")
    if (
        not organization
        or not workspace
        or workspace.organization_id != organization.id
        or not user
        or not user.is_authenticated
        or not user.can_write_to_workspace(workspace)
    ):
        raise ValueError("A valid user and workspace are required to connect Slack.")
    if (
        connection_id is not None
        and not IntegrationConnection.objects.filter(
            id=connection_id,
            organization=organization,
            workspace=workspace,
            platform=IntegrationPlatform.SLACK,
            deleted=False,
        ).exists()
    ):
        raise ValueError("Slack connection was not found in this workspace.")

    state_data = {
        "nonce": secrets.token_urlsafe(32),
        "user_id": str(user.pk),
        "organization_id": str(organization.pk),
        "workspace_id": str(workspace.pk),
        "connection_id": str(connection_id) if connection_id else None,
    }
    if not cache.add(
        _state_cache_key(state_data["nonce"]), state_data, timeout=STATE_TTL_SECONDS
    ):
        raise ValueError("Could not start Slack installation. Please try again.")
    state = signing.dumps(state_data, salt=STATE_SALT)
    authorize_url = getattr(settings, "SLACK_OAUTH_AUTHORIZE_URL", "") or AUTHORIZE_URL
    authorization_url = f"{authorize_url}?{urlencode({'client_id': client_id, 'scope': SCOPES, 'redirect_uri': slack_redirect_uri(), 'state': state})}"
    return {"authorization_url": authorization_url}


def consume_slack_state(state: str) -> dict:
    """Verify and consume a signed state before exchanging an OAuth code."""
    try:
        state_data = signing.loads(state, salt=STATE_SALT, max_age=STATE_TTL_SECONDS)
    except signing.BadSignature as exc:
        raise ValueError("Invalid or expired Slack installation state.") from exc
    nonce = state_data.get("nonce", "")
    key = _state_cache_key(nonce)
    stored = cache.get(key)
    if not stored or stored != state_data:
        raise ValueError("Slack installation state has expired or was already used.")
    if not cache.add(f"{key}:used", True, timeout=STATE_TTL_SECONDS):
        raise ValueError("Slack installation state was already used.")
    cache.delete(key)
    return state_data


def exchange_slack_code(code: str) -> dict:
    """Exchange the authorization code for a Slack bot installation."""
    api_base = getattr(settings, "SLACK_API_URL", "")
    access_url = f"{api_base.rstrip('/')}/oauth.v2.access" if api_base else ACCESS_URL
    try:
        response = requests.post(
            access_url,
            data={
                "client_id": settings.SLACK_CLIENT_ID,
                "client_secret": settings.SLACK_CLIENT_SECRET,
                "code": code,
                "redirect_uri": slack_redirect_uri(),
            },
            timeout=15,
        )
        response.raise_for_status()
        body = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise ValueError("Could not finish Slack installation.") from exc
    if not isinstance(body, dict):
        raise ValueError("Slack did not return a valid bot installation.")
    token = body.get("access_token", "")
    if body.get("refresh_token") or body.get("expires_in"):
        raise ValueError("Slack token rotation is not supported by this integration.")
    team = body.get("team") or {}
    granted_scopes = set((body.get("scope") or "").split(","))
    if (
        not body.get("ok")
        or not token.startswith("xoxb-")
        or not isinstance(team, dict)
        or not team.get("id")
        or not set(SCOPES.split(",")).issubset(granted_scopes)
    ):
        raise ValueError("Slack did not return a valid bot installation.")
    return {
        "bot_token": token,
        "team_id": team["id"],
        "team_name": team.get("name", ""),
        "bot_user_id": body.get("bot_user_id", ""),
        "scope": body.get("scope", ""),
    }
