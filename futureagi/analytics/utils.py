import os
from enum import Enum

import structlog
from django.conf import settings
from slack_sdk import WebhookClient

from analytics.mixpanel_util import mixpanel_tracker

logger = structlog.get_logger(__name__)


def mixpanel_slack_notfy(msg):
    if not settings.ERROR_LOGS_WEBHOOK:
        logger.debug("Skipping Slack notification: ERROR_LOGS_WEBHOOK is not set")
        return
    try:
        data = msg + "\n" + f"ENV_TYPE: {os.getenv('ENV_TYPE')}"
        webhook = WebhookClient(settings.ERROR_LOGS_WEBHOOK)
        if os.getenv("ENV_TYPE") not in ["local", "test"]:
            webhook.send(text=data)
        logger.info("Slack notification sent successfully")
    except Exception as e:
        logger.error(f"Failed to send Slack notification: {str(e)}")


def track_mixpanel_event(event_name, properties):
    try:
        mixpanel_tracker.track_event(
            event_name,
            properties,
        )
    except Exception as e:
        error_message = f"""
            *Event:* Mixpanel Event
            *Event Name:* {event_name}
            *Event Error:* {str(e)}
            """
        mixpanel_slack_notfy(error_message.strip())
        logger.error(f"Error tracking Mixpanel event '{event_name}': {str(e)}")


def get_mixpanel_properties(
    user=None,
    org=None,
    dataset=None,
    source=None,
    count=None,
    failed=None,
    endpoint=None,
    mode=None,
    file_ids=None,
    optimize_dataset=None,
    eval=None,
    eval_id=None,
    experiment=None,
    is_run=None,
    message=None,
    type=None,
    name=None,
    row_count=None,
    col_count=None,
    model=None,
    platform=None,
    project=None,
    span=None,
    span_id=None,
    experiment_id=None,
    dataset_id=None,
    uid=None,
    prompt_template=None,
    cost=None,
    knowledge_base=None,
):
    try:
        if user:
            try:
                from accounts.utils import get_user_organization

                org = get_user_organization(user)
            except Exception as e:
                logger.error(f"Error retrieving organization for user: {str(e)}")

        properties = {
            "$user_id": str(user.id) if user else None,
            "email": str(user.email) if user else None,
            "org_id": [str(org.id)] if org else None,
            "org_name": (
                org.display_name
                if org and org.display_name
                else (org.name if org else None)
            ),
            "endpoint": endpoint,
            "dataset_id": str(dataset.id) if dataset else dataset_id,
            "project_id": str(project.id) if project else None,
            "project_type": str(project.trace_type) if project else None,
            "optimize_dataset_id": (
                str(optimize_dataset.id) if optimize_dataset else None
            ),
            "source": source,
            "count": count,
            "failed": failed,
            "uid": uid,
            "eval_id": str(eval.id) if eval else eval_id,
            "span_id": str(span.id) if span else span_id,
            "experiment_id": str(experiment.id) if experiment else experiment_id,
            "file_ids": file_ids,
            "mode": mode,
            "is_run": is_run,
            "message": message,
            "type": type,
            "name": name,
            "row_count": row_count,
            "col_count": col_count,
            "model_name": model,
            "platform": platform,
            "prompt_template_id": str(prompt_template.id) if prompt_template else None,
            "Cost": cost,
            "kb_id": str(knowledge_base.id) if knowledge_base else None,
        }
        return {k: v for k, v in properties.items() if v is not None}
    except Exception as e:
        logger.error(f"Error in get_mixpanel_properties: {str(e)}")
        return {}


class MixpanelTypes(Enum):
    EMPTY = "empty"
    LOCAL_FILE = "local_file"
    HUGGINGFACE = "huggingface"
    PROTECT = "protect"
    OBSERVE = "observe"
    EVAL_TASK = "eval_task"
    FEEDBACK = "feedback"


class MixpanelModes(Enum):
    EMAIL = "email"
    SAML = "saml"
    GOOGLE = "google"
    GITHUB = "github"
    MICROSOFT = "microsoft"


class MixpanelEvents(Enum):
    SDK_INIT = "SDK_initialization"
    SSO_SIGNUP = "SSO_signup_clicked"
    SSO_LOGIN = "SSO_login_clicked"
    SIGNUP = "Signup_details_submitted"
    RESET_PASS = "Send_password_clicked"
    LOGIN_CLICK = "Login_clicked"
    SET_UP_ORG = "Set_up_organization_clicked"
    SDK_KB_CREATE = "SDK_kb_create"
    SDK_DATASET_CREATE = "SDK_dataset_create"
    SDK_DATASET_EVAL = "SDK_dataset_eval_stats"
    SDK_PROMPT_CREATE_DRAFT = "SDK_prompt_create_draft"
    SDK_PROMPT_CREATE = "SDK_prompt_create"
    SDK_PROMPT_IMPROVED = "SDK_prompt_improved"
    SDK_PROMPT_IMPROVE = "SDK_prompt_improve"
    SDK_PROMPT_GENERATE = "SDK_prompt_generate"
    SDK_PROMPT_GENERATED = "SDK_prompt_generated"
    SDK_PROMPT_RUN = "SDK_prompt_run_template"
    SDK_PROMPT_COMMIT = "SDK_prompt_commit"
    SDK_CONFIGURE_EVALS = "SDK_configure_evals"
    SDK_OBSERVE_CREATE = "SDK_observe_create"
    EVAL_RUN_STARTED = "Eval_run_started"
    EVAL_RUN_COMPLETED = "Eval_run_finished"


class MixpanelSources(Enum):
    OPTIMIZE = "optimize"
    EXPERIMENT = "experiment"
    DATASET = "dataset"
    WORKBENCH = "workbench"
    SDK = "sdk"
