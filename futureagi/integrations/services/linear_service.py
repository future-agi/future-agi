"""
Linear GraphQL API client.

Credentials dict shape:
    api_key (str): Personal API key from Linear Settings > Security & Access.

Auth header: ``Authorization: <api_key>`` (no "Bearer" prefix for personal keys).
Endpoint: https://api.linear.app/graphql
"""

import logging
from typing import Any

import requests

from integrations.services.base import BaseIntegrationService, register_service

logger = logging.getLogger(__name__)

GRAPHQL_URL = "https://api.linear.app/graphql"
TIMEOUT = 15


def _headers(api_key: str) -> dict:
    return {
        "Authorization": api_key,
        "Content-Type": "application/json",
    }


def _gql(api_key: str, query: str, variables: dict | None = None) -> dict:
    """Execute a GraphQL request against Linear's API."""
    payload: dict[str, Any] = {"query": query}
    if variables:
        payload["variables"] = variables

    resp = requests.post(
        GRAPHQL_URL,
        json=payload,
        headers=_headers(api_key),
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    body = resp.json()

    if body.get("errors"):
        raise ValueError(body["errors"][0].get("message", "Unknown GraphQL error"))

    return body.get("data", {})


class LinearService(BaseIntegrationService):
    """Linear integration — validate key & create issues from Error Feed clusters."""

    # ── BaseIntegrationService interface ──────────────────────────────────

    def validate_credentials(
        self,
        host_url: str,
        credentials: dict,
        ca_certificate: str | None = None,
    ) -> dict:
        api_key = credentials.get("api_key", "")
        if not api_key:
            return {"valid": False, "error": "API key is required."}

        try:
            data = _gql(
                api_key,
                """
                query {
                    teams {
                        nodes { id name }
                    }
                    viewer { id name email }
                }
                """,
            )
            teams = [
                {"id": t["id"], "name": t["name"]}
                for t in data.get("teams", {}).get("nodes", [])
            ]
            viewer = data.get("viewer", {})
            return {
                "valid": True,
                "projects": teams,
                "total_traces": 0,
                "viewer": {
                    "id": viewer.get("id"),
                    "name": viewer.get("name"),
                    "email": viewer.get("email"),
                },
            }
        except requests.exceptions.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else 0
            if status == 401:
                return {"valid": False, "error": "Invalid API key."}
            return {"valid": False, "error": f"Linear API error (HTTP {status})."}
        except requests.exceptions.ConnectionError:
            return {"valid": False, "error": "Could not reach Linear API."}
        except requests.exceptions.Timeout:
            return {"valid": False, "error": "Linear API request timed out."}
        except Exception as exc:
            logger.exception("Unexpected error validating Linear credentials")
            return {"valid": False, "error": str(exc)}

    def fetch_traces(self, host_url, credentials, **kwargs) -> dict:
        """Not used — Linear is an issue tracker, not a trace source."""
        return {"traces": [], "has_more": False, "next_page": 1, "total_items": 0}

    def fetch_trace_detail(self, host_url, credentials, trace_id, **kwargs) -> dict:
        """Not used — Linear is an issue tracker, not a trace source."""
        return {}

    # ── Linear-specific methods ───────────────────────────────────────────

    def get_teams(self, credentials: dict) -> list[dict]:
        """Fetch teams for the team picker dropdown."""
        api_key = credentials.get("api_key", "")
        data = _gql(
            api_key,
            """
            query {
                teams {
                    nodes { id name key }
                }
            }
            """,
        )
        return [
            {"id": t["id"], "name": t["name"], "key": t["key"]}
            for t in data.get("teams", {}).get("nodes", [])
        ]

    def create_issue(
        self,
        credentials: dict,
        team_id: str,
        title: str,
        description: str = "",
        priority: int = 0,
        label_ids: list[str] | None = None,
    ) -> dict:
        """Create a Linear issue and return its id, identifier, and url.

        Args:
            credentials: Must contain ``api_key``.
            team_id: Linear team UUID.
            title: Issue title.
            description: Markdown description.
            priority: 0=None, 1=Urgent, 2=High, 3=Normal, 4=Low.
            label_ids: Optional list of label UUIDs.
        """
        api_key = credentials.get("api_key", "")
        variables: dict[str, Any] = {
            "teamId": team_id,
            "title": title,
            "description": description,
            "priority": priority,
        }
        if label_ids:
            variables["labelIds"] = label_ids

        data = _gql(
            api_key,
            """
            mutation CreateIssue($teamId: String!, $title: String!, $description: String, $priority: Int, $labelIds: [String!]) {
                issueCreate(input: { teamId: $teamId, title: $title, description: $description, priority: $priority, labelIds: $labelIds }) {
                    success
                    issue {
                        id
                        identifier
                        url
                        title
                    }
                }
            }
            """,
            variables,
        )
        result = data.get("issueCreate", {})
        if not result.get("success"):
            raise ValueError("Linear issueCreate failed")

        issue = result.get("issue", {})
        return {
            "id": issue.get("id"),
            "identifier": issue.get("identifier"),
            "url": issue.get("url"),
            "title": issue.get("title"),
        }

    def create_comment(self, credentials: dict, issue_ref: str, body: str) -> dict:
        """Post a markdown comment on an issue.

        ``issue_ref`` may be the issue UUID or the human identifier
        (e.g. ``TH-123``) — the issue query resolves either, and
        commentCreate requires the UUID.
        """
        api_key = credentials.get("api_key", "")
        data = _gql(
            api_key,
            "query Issue($id: String!) { issue(id: $id) { id } }",
            {"id": issue_ref},
        )
        issue_uuid = (data.get("issue") or {}).get("id")
        if not issue_uuid:
            raise ValueError(f"Linear issue not found: {issue_ref}")

        data = _gql(
            api_key,
            """
            mutation CreateComment($issueId: String!, $body: String!) {
                commentCreate(input: { issueId: $issueId, body: $body }) {
                    success
                    comment { id url }
                }
            }
            """,
            {"issueId": issue_uuid, "body": body},
        )
        result = data.get("commentCreate", {})
        if not result.get("success"):
            raise ValueError("Linear commentCreate failed")
        comment = result.get("comment", {})
        return {"id": comment.get("id"), "url": comment.get("url")}


# Self-register on module import
_linear_service = LinearService()
register_service("linear", _linear_service)
