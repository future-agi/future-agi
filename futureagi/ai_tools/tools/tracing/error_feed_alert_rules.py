"""Falcon tools for Error Feed Slack alert rules.

The tools deliberately adapt the shared feed-alert and Slack integration
services instead of reproducing validation or authorization in Falcon.
"""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, EmptyInput, ToolContext, ToolResult
from ai_tools.formatting import key_value_block, markdown_table, section
from ai_tools.registry import register_tool

TriggerType = Literal[
    "new_issue",
    "severity_reached",
    "escalating",
    "occurrences_crossed",
]


class ErrorFeedAlertFilters(PydanticBaseModel):
    """Supported Error Feed rule predicates.

    Values in the same predicate are ORed; supplied predicates are ANDed.
    The service remains the source of truth for combinations that are valid
    with a chosen trigger.
    """

    sources: list[Literal["scanner", "eval"]] | None = Field(
        default=None,
        description="Only issues from these sources.",
    )
    severities: list[Literal["low", "medium", "high", "critical"]] | None = Field(
        default=None, description="Only issues at these severity levels."
    )
    statuses: (
        list[Literal["escalating", "for_review", "acknowledged", "resolved"]] | None
    ) = Field(
        default=None,
        description="Only issues in these current statuses.",
    )
    issue_groups: list[str] | None = Field(
        default=None,
        description="Only issues in these Error Feed issue groups.",
    )
    issue_categories: list[str] | None = Field(
        default=None,
        description="Only issues in these Error Feed issue categories.",
    )


class ListErrorFeedAlertRulesInput(PydanticBaseModel):
    enabled: bool | None = Field(default=None, description="Filter by enabled state.")
    project_id: UUID | None = Field(
        default=None, description="Only rules scoped to this project."
    )


class GetErrorFeedAlertRuleInput(PydanticBaseModel):
    rule_id: UUID = Field(description="The Error Feed alert rule UUID.")


class CreateErrorFeedAlertRuleInput(PydanticBaseModel):
    name: str = Field(min_length=1, max_length=255, description="Rule name.")
    project_id: UUID | None = Field(
        default=None,
        description="Project whose Error Feed issues the rule evaluates; omit for all workspace projects.",
    )
    trigger_type: TriggerType = Field(
        description=(
            "When to notify: new_issue, severity_reached, escalating, or "
            "occurrences_crossed."
        )
    )
    trigger_value: int | str | None = Field(
        default=None,
        description=(
            "Severity level for severity_reached, or a positive occurrence count "
            "for occurrences_crossed. Leave empty for new_issue and escalating."
        ),
    )
    filters: ErrorFeedAlertFilters | None = Field(
        default=None, description="Optional ANDed Error Feed issue filters."
    )
    slack_connection_id: UUID = Field(
        description="Connected Slack workspace UUID from list_slack_alert_integrations."
    )
    slack_channel_id: str = Field(
        min_length=1,
        description="Slack channel ID from list_slack_alert_channels; never a channel name.",
    )
    cooldown_seconds: int = Field(
        default=3600,
        ge=0,
        le=86400,
        description="Maximum notification frequency per issue and rule, in seconds.",
    )
    enabled: bool = Field(default=True, description="Whether the new rule is active.")


class UpdateErrorFeedAlertRuleInput(PydanticBaseModel):
    rule_id: UUID = Field(description="The Error Feed alert rule UUID.")
    name: str | None = Field(default=None, min_length=1, max_length=255)
    enabled: bool | None = Field(default=None, description="Enable or pause the rule.")
    project_id: UUID | None = Field(default=None, description="Updated project scope.")
    trigger_type: TriggerType | None = Field(default=None)
    trigger_value: int | str | None = Field(default=None)
    filters: ErrorFeedAlertFilters | None = Field(default=None)
    slack_connection_id: UUID | None = Field(default=None)
    slack_channel_id: str | None = Field(
        default=None, min_length=1, description="Replacement Slack channel ID."
    )
    cooldown_seconds: int | None = Field(default=None, ge=0, le=86400)


class DeleteErrorFeedAlertRuleInput(PydanticBaseModel):
    rule_id: UUID = Field(description="The Error Feed alert rule UUID to delete.")


class TestErrorFeedAlertRuleInput(PydanticBaseModel):
    rule_id: UUID = Field(description="The Error Feed alert rule UUID to test.")


class ListSlackAlertChannelsInput(PydanticBaseModel):
    connection_id: UUID = Field(
        description="Connected Slack workspace UUID from list_slack_alert_integrations."
    )
    cursor: str = Field(
        default="", description="Cursor returned by an earlier channel-list result."
    )


class StartSlackAlertInstallInput(PydanticBaseModel):
    connection_id: UUID | None = Field(
        default=None,
        description="Existing Slack connection to reinstall, if applicable.",
    )


def _error_result(error: Exception) -> ToolResult:
    """Translate application service errors without exposing implementation detail."""

    status = getattr(error, "status_code", None)
    if status == 404:
        return ToolResult.not_found("Error Feed alert rule", "requested ID")
    if status == 403:
        return ToolResult.permission_denied(str(error))
    if status == 402:
        return ToolResult.feature_unavailable("error_feed")
    return ToolResult.validation_error(str(error))


def _rule_data(
    params: PydanticBaseModel, *, exclude: set[str] | None = None
) -> dict[str, Any]:
    data = params.model_dump(exclude_unset=True, exclude=exclude or set())
    filters = getattr(params, "filters", None)
    if "filters" in data and isinstance(filters, ErrorFeedAlertFilters):
        data["filters"] = filters.model_dump(exclude_none=True)
    return data


def _rule_summary(rule: dict[str, Any]) -> str:
    return key_value_block(
        [
            ("Rule ID", f"`{rule.get('id')}`"),
            ("Name", rule.get("name")),
            ("Status", "Enabled" if rule.get("enabled") else "Paused"),
            ("Project ID", rule.get("project_id")),
            ("Trigger", rule.get("trigger_type")),
            ("Trigger value", rule.get("trigger_value")),
            (
                "Slack channel",
                rule.get("slack_channel_name") or rule.get("slack_channel_id"),
            ),
            ("Cooldown", f"{rule.get('cooldown_seconds')} seconds"),
        ]
    )


@register_tool
class ListErrorFeedAlertRulesTool(BaseTool):
    name = "list_error_feed_alert_rules"
    description = "Lists Error Feed alert rules and their Slack destinations in the current workspace."
    category = "error_feed"
    input_model = ListErrorFeedAlertRulesInput

    def execute(
        self, params: ListErrorFeedAlertRulesInput, context: ToolContext
    ) -> ToolResult:
        from tracer.services.feed_alerts.rules import FeedAlertError, list_rules

        try:
            result = list_rules(
                organization=context.organization, workspace=context.workspace
            )
        except FeedAlertError as error:
            return _error_result(error)

        rules = result.get("rules", []) if isinstance(result, dict) else result
        if params.enabled is not None:
            rules = [rule for rule in rules if rule.get("enabled") is params.enabled]
        if params.project_id is not None:
            rules = [
                rule
                for rule in rules
                if str(rule.get("project_id")) == str(params.project_id)
            ]
        rows = [
            [
                f"`{rule.get('id')}`",
                rule.get("name", "—"),
                rule.get("trigger_type", "—"),
                "Enabled" if rule.get("enabled") else "Paused",
                rule.get("slack_channel_name") or rule.get("slack_channel_id") or "—",
            ]
            for rule in rules
        ]
        return ToolResult(
            content=section(
                "Error Feed Alert Rules",
                markdown_table(
                    ["ID", "Name", "Trigger", "Status", "Slack channel"], rows
                ),
            ),
            data={"rules": rules, "total": len(rules)},
        )


@register_tool
class GetErrorFeedAlertRuleTool(BaseTool):
    name = "get_error_feed_alert_rule"
    description = "Gets an Error Feed alert rule, including its trigger, filters, and Slack destination."
    category = "error_feed"
    input_model = GetErrorFeedAlertRuleInput

    def execute(
        self, params: GetErrorFeedAlertRuleInput, context: ToolContext
    ) -> ToolResult:
        from tracer.services.feed_alerts.rules import FeedAlertError, get_rule

        try:
            rule = get_rule(
                rule_id=params.rule_id,
                organization=context.organization,
                workspace=context.workspace,
            )
        except FeedAlertError as error:
            return _error_result(error)
        return ToolResult(
            content=section("Error Feed Alert Rule", _rule_summary(rule)),
            data={"rule": rule},
        )


@register_tool
class CreateErrorFeedAlertRuleTool(BaseTool):
    name = "create_error_feed_alert_rule"
    description = "Creates an Error Feed alert rule that sends matching issues to a connected Slack channel."
    category = "error_feed"
    input_model = CreateErrorFeedAlertRuleInput

    def execute(
        self, params: CreateErrorFeedAlertRuleInput, context: ToolContext
    ) -> ToolResult:
        from tracer.services.feed_alerts.rules import FeedAlertError, create_rule

        try:
            rule = create_rule(
                data=_rule_data(params),
                organization=context.organization,
                workspace=context.workspace,
                user=context.user,
            )
        except FeedAlertError as error:
            return _error_result(error)
        return ToolResult(
            content=section("Error Feed Alert Rule Created", _rule_summary(rule)),
            data={"rule": rule},
        )


@register_tool
class UpdateErrorFeedAlertRuleTool(BaseTool):
    name = "update_error_feed_alert_rule"
    description = "Updates an Error Feed alert rule, including pausing it or changing its Slack channel."
    category = "error_feed"
    input_model = UpdateErrorFeedAlertRuleInput

    def execute(
        self, params: UpdateErrorFeedAlertRuleInput, context: ToolContext
    ) -> ToolResult:
        from tracer.services.feed_alerts.rules import FeedAlertError, update_rule

        data = _rule_data(params, exclude={"rule_id"})
        if not data:
            return ToolResult.validation_error("Provide at least one field to update.")
        try:
            rule = update_rule(
                rule_id=params.rule_id,
                data=data,
                organization=context.organization,
                workspace=context.workspace,
                user=context.user,
            )
        except FeedAlertError as error:
            return _error_result(error)
        return ToolResult(
            content=section("Error Feed Alert Rule Updated", _rule_summary(rule)),
            data={"rule": rule},
        )


@register_tool
class DeleteErrorFeedAlertRuleTool(BaseTool):
    name = "delete_error_feed_alert_rule"
    description = "Deletes an Error Feed alert rule. Ask for confirmation before using this destructive action."
    category = "error_feed"
    input_model = DeleteErrorFeedAlertRuleInput

    def execute(
        self, params: DeleteErrorFeedAlertRuleInput, context: ToolContext
    ) -> ToolResult:
        from tracer.services.feed_alerts.rules import FeedAlertError, delete_rule

        try:
            result = delete_rule(
                rule_id=params.rule_id,
                organization=context.organization,
                workspace=context.workspace,
                user=context.user,
            )
        except FeedAlertError as error:
            return _error_result(error)
        return ToolResult(
            content=section(
                "Error Feed Alert Rule Deleted", f"Rule `{params.rule_id}` was deleted."
            ),
            data=result
            if isinstance(result, dict)
            else {"rule_id": str(params.rule_id), "deleted": True},
        )


@register_tool
class TestErrorFeedAlertRuleTool(BaseTool):
    name = "test_error_feed_alert_rule"
    description = "Sends a clearly labelled Slack test message for an Error Feed alert rule without changing its cooldown."
    category = "error_feed"
    input_model = TestErrorFeedAlertRuleInput

    def execute(
        self, params: TestErrorFeedAlertRuleInput, context: ToolContext
    ) -> ToolResult:
        from tracer.services.feed_alerts.rules import FeedAlertError, test_rule

        try:
            result = test_rule(
                rule_id=params.rule_id,
                organization=context.organization,
                workspace=context.workspace,
                user=context.user,
            )
        except FeedAlertError as error:
            return _error_result(error)
        return ToolResult(
            content=section(
                "Error Feed Alert Test",
                "A labelled test notification was sent to the rule's Slack channel.",
            ),
            data=result
            if isinstance(result, dict)
            else {"rule_id": str(params.rule_id), "sent": True},
        )


@register_tool
class ListSlackAlertIntegrationsTool(BaseTool):
    name = "list_slack_alert_integrations"
    description = (
        "Lists active Slack workspaces available as Error Feed alert destinations."
    )
    category = "error_feed"
    input_model = EmptyInput

    def execute(self, params: EmptyInput, context: ToolContext) -> ToolResult:
        from integrations.models import ConnectionStatus, IntegrationConnection, IntegrationPlatform

        connections = IntegrationConnection.objects.filter(
            organization=context.organization,
            workspace=context.workspace,
            platform=IntegrationPlatform.SLACK,
            status=ConnectionStatus.ACTIVE,
            deleted=False,
        ).order_by("display_name")
        integrations = [
            {
                "id": str(connection.id),
                "name": connection.display_name,
                "status": connection.status,
            }
            for connection in connections
        ]
        rows = [
            [f"`{item['id']}`", item["name"], item["status"]] for item in integrations
        ]
        return ToolResult(
            content=section(
                "Connected Slack Workspaces",
                markdown_table(["Connection ID", "Name", "Status"], rows),
            ),
            data={"integrations": integrations},
        )


@register_tool
class ListSlackAlertChannelsTool(BaseTool):
    name = "list_slack_alert_channels"
    description = "Lists Slack channels available to a selected connected Slack workspace for Error Feed alerts."
    category = "error_feed"
    input_model = ListSlackAlertChannelsInput

    def execute(
        self, params: ListSlackAlertChannelsInput, context: ToolContext
    ) -> ToolResult:
        from integrations.models import (
            ConnectionStatus,
            IntegrationConnection,
            IntegrationPlatform,
        )
        from integrations.services.credentials import CredentialManager
        from integrations.services.slack_service import SlackApiError, SlackService

        try:
            connection = IntegrationConnection.objects.get(
                id=params.connection_id,
                organization=context.organization,
                workspace=context.workspace,
                platform=IntegrationPlatform.SLACK,
                status=ConnectionStatus.ACTIVE,
                deleted=False,
            )
        except IntegrationConnection.DoesNotExist:
            return ToolResult.not_found(
                "Active Slack integration", str(params.connection_id)
            )

        try:
            credentials = CredentialManager.decrypt(
                bytes(connection.encrypted_credentials)
            )
            result = SlackService().get_channels(credentials, cursor=params.cursor)
        except SlackApiError as error:
            return ToolResult.error(
                str(error), error_code=getattr(error, "code", "SLACK_API_ERROR")
            )
        except Exception:
            return ToolResult.error(
                "The Slack integration needs to be reconnected before its channels can be listed.",
                error_code="SLACK_INTEGRATION_ERROR",
            )

        channels = result.get("channels", [])
        rows = [
            [
                f"`{channel.get('id')}`",
                channel.get("name", "—"),
                "Private" if channel.get("is_private") else "Public",
            ]
            for channel in channels
        ]
        return ToolResult(
            content=section(
                "Available Slack Channels",
                markdown_table(["Channel ID", "Name", "Visibility"], rows),
            ),
            data={"channels": channels, "next_cursor": result.get("next_cursor", "")},
        )


@register_tool
class StartSlackAlertInstallTool(BaseTool):
    name = "start_slack_alert_install"
    description = "Starts the Slack OAuth connection flow and returns a browser consent URL for Error Feed alerts."
    category = "error_feed"
    input_model = StartSlackAlertInstallInput

    def execute(
        self, params: StartSlackAlertInstallInput, context: ToolContext
    ) -> ToolResult:
        from integrations.services.slack_oauth import start_slack_install

        try:
            result = start_slack_install(
                organization=context.organization,
                workspace=context.workspace,
                user=context.user,
                connection_id=params.connection_id,
            )
        except ValueError as error:
            return ToolResult.validation_error(str(error))
        return ToolResult(
            content=section(
                "Connect Slack",
                f"Open this URL to authorize Slack: {result['authorization_url']}",
            ),
            data={"authorization_url": result["authorization_url"]},
        )
