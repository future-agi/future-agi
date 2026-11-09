import os

import structlog
from django.conf import settings
from slack_sdk import WebhookClient

logger = structlog.get_logger(__name__)


def send_critical_slack_notification(message: str) -> bool:
    """
    Send a notification to the #critical Slack channel.

    Args:
        message: The message to send

    Returns:
        True if notification was sent successfully, False otherwise
    """
    try:
        env_type = os.getenv("ENV_TYPE", "local")

        # Don't send in local/test environments
        if env_type in ["local", "test"]:
            logger.info(
                "Skipping Slack notification in local/test environment",
                message=message[:100],
            )
            return True

        # Unset on a self-hosted install: nothing to post to, so no client, no
        # request and nothing logged above debug.
        if not str(getattr(settings, "ERROR_LOGS_WEBHOOK", "") or "").strip():
            logger.debug(
                "Skipping critical Slack notification: ERROR_LOGS_WEBHOOK is not set"
            )
            return False

        formatted_message = f"{message}\n\n*Environment:* {env_type}"
        webhook = WebhookClient(settings.ERROR_LOGS_WEBHOOK)
        response = webhook.send(text=formatted_message)

        if response.status_code == 200:
            logger.info("Critical Slack notification sent successfully")
            return True
        else:
            logger.error(
                "Failed to send critical Slack notification",
                status_code=response.status_code,
            )
            return False

    except Exception as e:
        logger.error(f"Failed to send critical Slack notification: {str(e)}")
        return False
