"""Run authorizations for the Console's delegated model calls.

POST /agentcc/console-run/authorizations/ mints one when a person starts a
Console chat: a registered fe1r token (kind console-run-auth) naming the run,
the models the run may call (each a row of the person's own Console list) and
the agent service deployment it is handed to. FA's host page passes it to the
Console, which passes it to the agent service; FA's console-run signer is the
only reader that decides on it (fi-security 1d8b0ce0; wire record
lane-status gwc2197-CONSOLE-RUN-SIGN-WIRE-1114.md section 2). The token is never
logged.
"""

import uuid

import structlog
from django.db import transaction

from accounts.services.service_grants import issue_person_grant
from agentcc.services.console_models import (
    ConsoleModelsUnavailable,
    list_console_models,
)
from agentcc.services.console_run_signer_keys import registered_deployment
from agentcc.services.delegated_availability import console_run_enabled
from tfc.service_envelope import CONSOLE_RUN_MODELS_MAX, EnvelopeRejected

audit = structlog.get_logger("agentcc.console_run.audit")

# Minted shorter than the 12 hour cap (fi-security: shorter where the run
# allows); a chat that outlives it needs a new start.
RUN_AUTHORIZATION_TTL = 8 * 3600
INTERACTIVE_SESSION = "interactive_session"


class ConsoleRunRefused(Exception):
    """A typed refusal: HTTP status, code and a fixed sentence."""

    def __init__(self, status, code, message):
        super().__init__(code)
        self.status = status
        self.code = code
        self.message = message


def _model_ids(models):
    if (
        not isinstance(models, list)
        or not 1 <= len(models) <= CONSOLE_RUN_MODELS_MAX
        or not all(isinstance(m, str) and m for m in models)
        or len(set(models)) != len(models)
    ):
        raise ConsoleRunRefused(
            400,
            "invalid_models",
            "Name 1 to 8 different models from the Console's model list.",
        )
    return models


def authorize_console_run(request, models):
    """The run authorization for ``request``'s person, or ConsoleRunRefused."""
    if not console_run_enabled():
        raise ConsoleRunRefused(
            403,
            "console_run_disabled",
            "Console model calls are turned off on this deployment.",
        )
    if getattr(request, "credential_provenance", None) != INTERACTIVE_SESSION:
        raise ConsoleRunRefused(
            403,
            "console_run_session_required",
            "Start a Console run from your signed-in browser session.",
        )
    organization = request.organization
    workspace = getattr(request, "workspace", None)
    if workspace is None:
        raise ConsoleRunRefused(
            400, "workspace_required", "Choose a workspace to start a Console run."
        )
    ids = _model_ids(models)
    deployment = registered_deployment()
    if deployment is None:
        # Inert until an operator registers an agent service key: refused,
        # never attempted.
        raise ConsoleRunRefused(
            409,
            "console_run_not_configured",
            "Console model calls are not set up on this deployment yet.",
        )
    try:
        listed = list_console_models(organization, workspace)["models"]
    except ConsoleModelsUnavailable as unavailable:
        raise ConsoleRunRefused(
            unavailable.status,
            "console_models_unavailable",
            "The model list is temporarily unavailable. Please retry.",
        ) from None
    if not set(ids) <= {row["model"] for row in listed}:
        raise ConsoleRunRefused(
            403,
            "model_not_in_list",
            "Pick models from your Console model list.",
        )
    run_id = str(uuid.uuid4())
    try:
        with transaction.atomic():
            token, claims, _ = issue_person_grant(
                kind="console-run-auth",
                org=organization.pk,
                user=request.user.pk,
                workspace=workspace.pk,
                sub="chat:" + run_id,
                ttl=RUN_AUTHORIZATION_TTL,
                models=ids,
                ee=deployment,
            )
    except EnvelopeRejected:
        raise ConsoleRunRefused(
            403,
            "console_run_not_a_member",
            "You are not a member of this workspace.",
        ) from None
    audit.info(
        "console_run_authorized",
        run_id=run_id,
        authorization_id=claims["id"],
        org_id=str(organization.pk),
        user_id=str(request.user.pk),
        deployment=deployment,
        models=len(ids),
    )
    return {"run_authorization": token, "run_id": run_id, "exp": int(claims["exp"])}
