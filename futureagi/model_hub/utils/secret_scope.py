"""Explicit credential scope for request and background dataset operations."""

from django.db.models import Q

from accounts.models.workspace import Workspace
from model_hub.models.api_key import SecretModel


def scoped_secret(secret_id, organization_id, workspace_id):
    """No caller context, or a missing workspace, must never widen credential access."""
    if organization_id is None:
        raise SecretModel.DoesNotExist("Secret not found")
    scope = Q(workspace_id=workspace_id)
    if workspace_id is not None:
        workspace = Workspace.no_workspace_objects.filter(
            id=workspace_id, organization_id=organization_id,
        ).first()
        if workspace is None:
            raise SecretModel.DoesNotExist("Secret not found")
        if workspace.is_default:
            scope |= Q(workspace__isnull=True) | Q(
                workspace__is_default=True, workspace__organization_id=organization_id,
            )
    return SecretModel.no_workspace_objects.get(
        scope, id=secret_id, organization_id=organization_id, deleted=False,
    )
