"""Explicit admitted authority for credential-bearing dataset operations.

The envelope is minted at HTTP admission, not taken from operation JSON or from
resource owners. It contains identities only and survives task/thread boundaries.
Every use reloads the actor and current entitlements; missing old-job scope is a
controlled refusal, never an implicit Default/NULL or ambient-context grant.
"""

from uuid import UUID

from django.db.models import Q
from rest_framework.exceptions import PermissionDenied

from accounts.models.user import User
from accounts.models.workspace import Workspace
from model_hub.models.api_key import ApiKey, SecretModel
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from tfc.utils.tenant_scope import (
    acts_in_organization,
    caller_organization,
    scope_to_accessible_workspace,
)

SCOPE_DENIED = "Operation is not permitted in the selected workspace."


def _refuse():
    raise PermissionDenied(SCOPE_DENIED, code="operation_scope_denied")


def _identity(value):
    try:
        return str(UUID(str(value)))
    except (TypeError, ValueError, AttributeError):
        _refuse()


def request_authority(request, dataset_id, *, require_write=True):
    """Capture only authenticated request scope, with no workspace fallback."""
    organization = caller_organization(request)
    workspace = getattr(request, "workspace", None)
    user = getattr(request, "user", None)
    authority = {
        "version": 1,
        "actor_id": _identity(getattr(user, "pk", None)),
        "organization_id": _identity(getattr(organization, "pk", None)),
        "workspace_id": _identity(getattr(workspace, "pk", None)),
        "dataset_id": _identity(dataset_id),
    }
    if require_write:
        authorized_dataset(authority)
    elif (
        not _scoped(Dataset, authority, require_write=False)
        .filter(pk=authority["dataset_id"])
        .exists()
    ):
        _refuse()
    return authority


def live_authority(
    authority, organization_id=None, workspace_id=None, *, require_write=True
):
    """No cached request user/membership decision is an execution-time grant."""
    if not isinstance(authority, dict) or authority.get("version") != 1:
        _refuse()
    ids = {
        key: _identity(authority.get(key))
        for key in ("actor_id", "organization_id", "workspace_id", "dataset_id")
    }
    if (
        organization_id is not None
        and _identity(organization_id) != ids["organization_id"]
    ):
        _refuse()
    if workspace_id is not None and _identity(workspace_id) != ids["workspace_id"]:
        _refuse()
    user = User.objects.filter(pk=ids["actor_id"], is_active=True).first()
    workspace = (
        Workspace.no_workspace_objects.filter(
            pk=ids["workspace_id"],
            organization_id=ids["organization_id"],
            is_active=True,
            deleted=False,
        )
        .select_related("organization")
        .first()
    )
    if (
        user is None
        or workspace is None
        or not acts_in_organization(user, workspace.organization)
        or not (
            user.can_write_to_workspace(workspace)
            if require_write
            else user.can_access_workspace(workspace)
        )
    ):
        _refuse()
    return user, workspace.organization, workspace


def _scoped(model, authority, *, require_write=True):
    user, organization, workspace = live_authority(
        authority, require_write=require_write
    )
    return scope_to_accessible_workspace(
        model.no_workspace_objects.filter(deleted=False),
        organization,
        workspace,
        user=user,
    ).filter(
        Q(workspace__isnull=True)
        | Q(
            workspace__is_active=True,
            workspace__deleted=False,
            workspace__organization=organization,
        )
    )


def authorized_dataset(
    authority,
    dataset_id=None,
    *,
    destination_id=None,
    source_id=None,
    row_id=None,
):
    """Revalidate bindings before a queued status/cell write or credential use."""
    # Validate even when no resource lookup was supplied by a legacy caller.
    live_authority(authority)
    admitted_id = _identity(authority.get("dataset_id"))
    if dataset_id is not None and _identity(dataset_id) != admitted_id:
        _refuse()
    dataset = _scoped(Dataset, authority).filter(pk=admitted_id).first()
    if dataset is None:
        _refuse()
    bound_destination = authority.get("destination_id")
    if destination_id is not None:
        if bound_destination is None or _identity(destination_id) != _identity(
            bound_destination
        ):
            _refuse()
    for column_id in (bound_destination, source_id):
        if (
            column_id is not None
            and not Column.no_workspace_objects.filter(
                pk=_identity(column_id),
                dataset=dataset,
                deleted=False,
            ).exists()
        ):
            _refuse()
    if (
        row_id is not None
        and not Row.no_workspace_objects.filter(
            pk=_identity(row_id),
            dataset=dataset,
            deleted=False,
        ).exists()
    ):
        _refuse()
    if bound_destination is not None:
        # A Cell has independent dataset/row/column FKs. Refuse inconsistent
        # destination bindings even before the queued RUNNING status update.
        if (
            Cell.no_workspace_objects.filter(
                column_id=bound_destination,
                deleted=False,
            )
            .exclude(dataset=dataset)
            .exists()
        ):
            _refuse()
    return dataset


def scoped_secret(secret_id, organization_id, workspace_id, *, authority=None):
    """Authorize before actual_key; NULL is allowed only via explicit Default."""
    live_authority(authority, organization_id, workspace_id)
    authorized_dataset(authority)
    secret = _scoped(SecretModel, authority).filter(pk=_identity(secret_id)).first()
    if secret is None:
        _refuse()
    return secret


def scoped_embedding_key(provider, authority):
    authorized_dataset(authority)
    keys = _scoped(ApiKey, authority).filter(provider=provider)
    # Prefer the explicitly selected workspace; Default legacy compatibility is
    # supplied solely by the canonical authorized scope, never None fallback.
    key = keys.filter(workspace_id=authority["workspace_id"]).first() or keys.first()
    if key is None:
        _refuse()
    return key


def validate_vector_config(config, authority):
    """Admission is read-only: validate both credentials before either is revealed."""
    if not isinstance(config, dict) or not config.get("column_id"):
        _refuse()
    authorized_dataset(authority, source_id=_identity(config["column_id"]))
    if config.get("sub_type") not in {"pinecone", "qdrant", "weaviate"}:
        _refuse()
    embedding = config.get("embedding_config")
    if not isinstance(embedding, dict):
        _refuse()
    provider = embedding.get("type")
    if provider == "sentence_transformers":
        provider = "huggingface"
    if provider not in {"openai", "huggingface"}:
        _refuse()
    scoped_secret(
        config.get("api_key"),
        authority["organization_id"],
        authority["workspace_id"],
        authority=authority,
    )
    scoped_embedding_key(provider, authority)


def validate_conditional_config(config, authority):
    """Inspect every branch, including untaken if/elif/else retrieval branches."""
    authorized_dataset(authority)
    if not isinstance(config, list):
        _refuse()
    for branch in config:
        if not isinstance(branch, dict):
            _refuse()
        node = branch.get("branch_node_config", {})
        if not isinstance(node, dict) or not isinstance(node.get("config", {}), dict):
            _refuse()
        node_config = node.get("config", {})
        if node_config.get("column_id"):
            authorized_dataset(authority, source_id=_identity(node_config["column_id"]))
        if node.get("type") == "retrieval":
            validate_vector_config(node_config, authority)


def _read_secret_reference(secret_id, authority):
    # Existence is checked only inside current authorized scope; no key fields
    # are loaded or decoded and absent, revoked and foreign refs look identical.
    if (
        not _scoped(SecretModel, authority, require_write=False)
        .filter(pk=_identity(secret_id))
        .exists()
    ):
        _refuse()


def _read_vector_references(config, authority):
    if not isinstance(config, dict):
        _refuse()
    secret_id = config.get("api_key")
    if secret_id is not None and secret_id != "":
        _read_secret_reference(secret_id, authority)
    embedding = config.get("embedding_config")
    if embedding is None:
        return
    # The actual serializer stores only type/model, not an embedding-key ID.
    # Legacy arbitrary JSON must not smuggle credential fields through this GET.
    if not isinstance(embedding, dict) or set(embedding) - {"type", "model"}:
        _refuse()
    if "model" in embedding and not isinstance(embedding["model"], str):
        _refuse()
    provider = embedding.get("type")
    if provider is None or provider == "":
        return
    if not isinstance(provider, str):
        _refuse()
    if provider == "sentence_transformers":
        provider = "huggingface"
    if provider not in {"openai", "huggingface"}:
        _refuse()
    if (
        not _scoped(ApiKey, authority, require_write=False)
        .filter(provider=provider)
        .exists()
    ):
        _refuse()


def validate_operation_config(metadata, authority, *, source):
    """Authorize saved references without executing or rewriting editable JSON.

    Walk dicts/lists, including all conditional branches, not just the taken one.
    Read permission is explicit code policy, never a flag borrowed from metadata;
    execution helpers retain their write-required defaults. Invalid references
    refuse the entire response opaquely rather than return a partially safe blob.
    """
    if not isinstance(metadata, dict):
        _refuse()
    pending = [(metadata, source == "vector_db")]
    while pending:
        node, vector = pending.pop()
        if isinstance(node, list):
            pending.extend((child, False) for child in node)
        elif isinstance(node, dict):
            if (
                vector
                or "embedding_config" in node
                or ("sub_type" in node and "api_key" in node)
            ):
                _read_vector_references(node, authority)
            # Conditional branches persist branch_node_config={type, config}.
            # Recognize the representation even in legacy nested containers or
            # when the column source tag is stale; that tag is not authority.
            if node.get("type") == "retrieval":
                _read_vector_references(node.get("config"), authority)
            if node.get("type") == "Secret":
                _read_secret_reference(node.get("value"), authority)
            pending.extend((child, False) for child in node.values())
