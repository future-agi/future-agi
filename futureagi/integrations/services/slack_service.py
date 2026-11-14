"""Slack Web API client for workspace alert actions."""

from typing import Any

import requests
from django.conf import settings

from integrations.services.base import BaseIntegrationService, register_service

SLACK_API_URL = "https://slack.com/api"
TIMEOUT = 15


class SlackApiError(ValueError):
    """A rejected or failed Slack Web API operation."""

    def __init__(self, code: str, retry_after: int | None = None):
        self.code = code
        self.retry_after = retry_after
        super().__init__(code)


def slack_api(
    method: str, token: str, *, params: dict | None = None, payload: dict | None = None
) -> dict:
    if not token or not token.startswith("xoxb-"):
        raise SlackApiError("invalid_auth")
    api_base = (getattr(settings, "SLACK_API_URL", "") or SLACK_API_URL).rstrip("/")
    try:
        if payload is None:
            response = requests.get(
                f"{api_base}/{method}",
                headers={"Authorization": f"Bearer {token}"},
                params=params,
                timeout=TIMEOUT,
            )
        else:
            response = requests.post(
                f"{api_base}/{method}",
                headers={"Authorization": f"Bearer {token}"},
                json=payload,
                timeout=TIMEOUT,
            )
        if response.status_code == 429:
            try:
                retry_after = max(1, int(response.headers.get("Retry-After", "30")))
            except ValueError:
                retry_after = 30
            raise SlackApiError("ratelimited", retry_after=retry_after)
        response.raise_for_status()
        body = response.json()
    except SlackApiError:
        raise
    except (requests.RequestException, ValueError) as exc:
        raise SlackApiError("slack_unavailable") from exc
    if not isinstance(body, dict) or not body.get("ok"):
        raise SlackApiError(
            str(body.get("error", "slack_api_error"))
            if isinstance(body, dict)
            else "slack_api_error"
        )
    return body


class SlackService(BaseIntegrationService):
    """Channel discovery and message delivery using an installed bot token."""

    def validate_credentials(
        self, host_url: str, credentials: dict, ca_certificate: str | None = None
    ) -> dict:
        try:
            auth = slack_api("auth.test", credentials.get("bot_token", ""))
            expected_team = credentials.get("team_id")
            if expected_team and auth.get("team_id") != expected_team:
                return {
                    "valid": False,
                    "error": "Slack workspace does not match the installation.",
                }
            return {
                "valid": True,
                "team_id": auth.get("team_id"),
                "team_name": auth.get("team"),
                "bot_user_id": auth.get("user_id"),
                "total_traces": 0,
            }
        except SlackApiError as exc:
            return {"valid": False, "error": exc.code}

    def fetch_traces(self, host_url: str, credentials: dict, **kwargs) -> dict:
        return {"traces": [], "has_more": False, "next_page": 1, "total_items": 0}

    def fetch_trace_detail(
        self, host_url: str, credentials: dict, trace_id: str, **kwargs
    ) -> dict[str, Any]:
        return {}

    def get_channels(self, credentials: dict, cursor: str = "") -> dict:
        """Return public channels and private channels the bot has joined."""
        params = {
            "types": "public_channel,private_channel",
            "limit": 200,
            "exclude_archived": "true",
        }
        if cursor:
            params["cursor"] = cursor
        body = slack_api(
            "conversations.list", credentials.get("bot_token", ""), params=params
        )
        return {
            "channels": [
                {
                    "id": channel["id"],
                    "name": channel["name"],
                    "is_private": bool(channel.get("is_private")),
                    "is_member": bool(channel.get("is_member")),
                }
                for channel in body.get("channels", [])
                if channel.get("id")
                and channel.get("name")
                and (not channel.get("is_private") or channel.get("is_member"))
            ],
            "next_cursor": body.get("response_metadata", {}).get("next_cursor") or None,
        }

    def validate_channel(
        self, credentials: dict, channel_id: str, *, join_public: bool = False
    ) -> dict:
        """Resolve a channel and optionally join it before a rule is saved."""
        if (
            not channel_id
            or len(channel_id) > 32
            or not channel_id.startswith(("C", "G"))
            or not channel_id[1:].isalnum()
        ):
            raise SlackApiError("invalid_channel")
        body = slack_api(
            "conversations.info",
            credentials.get("bot_token", ""),
            params={"channel": channel_id},
        )
        channel = body.get("channel") or {}
        if channel.get("is_archived"):
            raise SlackApiError("is_archived")
        if not channel.get("is_member"):
            if channel.get("is_private") or not join_public:
                raise SlackApiError("not_in_channel")
            joined = slack_api(
                "conversations.join",
                credentials.get("bot_token", ""),
                payload={"channel": channel_id},
            ).get("channel") or {}
            if not joined.get("is_member"):
                raise SlackApiError("not_in_channel")
            channel = joined
        return {
            "id": channel.get("id", channel_id),
            "name": channel.get("name", ""),
            "is_private": bool(channel.get("is_private")),
        }

    def post_message(
        self,
        credentials: dict,
        channel_id: str,
        text: str,
        blocks: list[dict] | None = None,
    ) -> dict:
        """Post an alert and return the Slack channel and message timestamp."""
        if not text or len(text) > 40000:
            raise ValueError(
                "Slack message text must be between 1 and 40000 characters."
            )
        self.validate_channel(credentials, channel_id)
        payload: dict = {
            "channel": channel_id,
            "text": text,
            "unfurl_links": False,
            "unfurl_media": False,
        }
        if blocks is not None:
            payload["blocks"] = blocks
        body = slack_api(
            "chat.postMessage", credentials.get("bot_token", ""), payload=payload
        )
        return {"channel": body.get("channel", channel_id), "ts": body.get("ts")}


register_service("slack", SlackService())
