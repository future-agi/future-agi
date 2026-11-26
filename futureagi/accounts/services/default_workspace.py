"""Create-or-read of an organization's default workspace.

Two first-login requests (or a request and the onboarding/demo seeding that
runs beside it) can both miss the default workspace and both try to insert
it. The loser's INSERT fails on whichever unique index Postgres checks first:
the ``(organization, name)`` unique-together index or the partial
``unique_default_workspace_per_org`` index. Either way the winner's row is the
answer, so this module treats any ``IntegrityError`` as "someone else created
it" and re-reads it.
"""

import structlog
from django.db import IntegrityError, transaction

from accounts.models.workspace import Workspace

logger = structlog.get_logger(__name__)

DEFAULT_WORKSPACE_NAME = "Default Workspace"


def get_or_create_default_workspace(organization, created_by):
    """Return ``(workspace, created)`` for the organization's default workspace.

    The INSERT runs in a savepoint so a failed insert never poisons the
    caller's transaction. When the insert conflicts, the active default
    workspace another writer committed is returned with ``created=False``.
    Re-raises the ``IntegrityError`` when no such workspace exists, i.e. the
    conflict was not a concurrent default-workspace create.
    """
    try:
        workspace = Workspace.objects.get(
            organization=organization, is_default=True, is_active=True
        )
        return workspace, False
    except Workspace.DoesNotExist:
        pass

    try:
        with transaction.atomic():
            workspace = Workspace.objects.create(
                name=DEFAULT_WORKSPACE_NAME,
                organization=organization,
                is_default=True,
                is_active=True,
                created_by=created_by,
            )
        return workspace, True
    except IntegrityError:
        concurrent = Workspace.objects.filter(
            organization=organization, is_default=True, is_active=True
        ).first()
        if concurrent is None:
            raise
        workspace = concurrent
        logger.info(
            "default_workspace_created_concurrently",
            organization_id=str(organization.id),
            workspace_id=str(workspace.id),
        )
        return workspace, False
