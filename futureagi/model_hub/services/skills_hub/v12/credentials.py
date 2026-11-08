"""Isolated consume/report credentials. Secret material never enters receipts."""

from __future__ import annotations

import hashlib
import json
import re
import secrets
from datetime import datetime, timedelta
from typing import Literal, NoReturn
from uuid import UUID, uuid4

from django.conf import settings
from django.db import transaction
from django.db.models import F, Value
from django.db.models.functions import Coalesce, Greatest
from django.utils import timezone
from django.utils.crypto import constant_time_compare, salted_hmac

from accounts.models.workspace import Workspace
from model_hub.models.skills_hub_v12 import (
    HubSkillContextBinding,
    HubSkillContextCredential,
    HubSkillContextOperation,
)
from model_hub.selectors.skills_hub_v12 import resources_for_scope
from model_hub.services.skills_hub.errors import SkillsHubError
from model_hub.services.skills_hub.v11.principals import (
    effective_scope_level,
    require_valid_scope,
)
from model_hub.services.skills_hub.v11.types import SkillPrincipal, SkillScope
from model_hub.services.skills_hub.v12.limits import (
    MAX_CREDENTIAL_DAYS,
    MAX_GRANT_SELECTORS,
    MAX_GRANTS,
)
from model_hub.services.skills_hub.v12.types import ContextCredentialPrincipal
from tfc.constants.levels import Level


def _json_scalar(value: object) -> str:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime) and timezone.is_aware(value):
        return value.isoformat()
    raise TypeError("Use finite JSON and explicit UUID/UTC identifiers.")


def canonical_digest(value: object) -> str:
    raw = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
        default=_json_scalar,
    )
    return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def require_consumption_enabled() -> None:
    if not getattr(settings, "SKILLS_CONTEXT_CONSUMPTION_ENABLED", False):
        raise SkillsHubError(
            "skills_unavailable",
            "Context credential consumption is not enabled.",
            status=503,
        )


def require_credential_admin(*, principal: SkillPrincipal) -> None:
    if effective_scope_level(principal=principal) < Level.WORKSPACE_ADMIN:
        raise SkillsHubError(
            "credential_operation_forbidden",
            "A current administrator is required.",
            status=403,
        )


def credential_metadata(credential: HubSkillContextCredential) -> dict:
    return {
        "id": str(credential.pk),
        "purpose": credential.purpose,
        "grants": credential.grants,
        "binding_ids": credential.binding_ids,
        "expires_at": credential.expires_at,
        "revoked_at": credential.revoked_at,
        "created_at": credential.created_at,
        "last_used_at": credential.last_used_at,
    }


def _invalid_scope() -> NoReturn:
    raise SkillsHubError(
        "invalid_credential_scope",
        "Use explicit grants in this workspace for the selected purpose.",
        status=400,
    )


def _uuid(value: object) -> str:
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        _invalid_scope()


def _validate_grants(
    *, scope: SkillScope, purpose: str, grants: list, binding_ids: list
) -> tuple[list, list]:
    if (
        purpose not in {"consume", "report"}
        or not isinstance(grants, list)
        or not isinstance(binding_ids, list)
    ):
        _invalid_scope()
    if len(grants) > MAX_GRANTS or len(binding_ids) > MAX_GRANTS:
        _invalid_scope()
    if purpose == "report":
        if grants or not binding_ids:
            _invalid_scope()
        ids = sorted({_uuid(value) for value in binding_ids})
        count = HubSkillContextBinding.objects.filter(
            pk__in=ids,
            organization_id=scope.organization_id,
            workspace_id=scope.workspace_id,
            context__organization_id=scope.organization_id,
            context__workspace_id=scope.workspace_id,
            context__kind="agent_context",
            context__deleted=False,
            retired_at__isnull=True,
        ).count()
        if len(ids) != len(binding_ids) or count != len(ids):
            _invalid_scope()
        return [], ids
    if not grants or binding_ids:
        _invalid_scope()
    normalized, seen = [], set()
    for grant in grants:
        if not isinstance(grant, dict) or set(grant) != {
            "kind",
            "resource_id",
            "labels",
            "version_ids",
        }:
            _invalid_scope()
        kind = grant["kind"]
        if kind not in {"skill", "agent_context"}:
            _invalid_scope()
        resource_id = _uuid(grant["resource_id"])
        if resource_id in seen:
            _invalid_scope()
        seen.add(resource_id)
        labels, versions = grant["labels"], grant["version_ids"]
        if (
            not isinstance(labels, list)
            or not isinstance(versions, list)
            or not (labels or versions)
            or len(labels) > 20
            or len(versions) > MAX_GRANT_SELECTORS
        ):
            _invalid_scope()
        if any(
            not isinstance(label, str)
            or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,39}", label)
            for label in labels
        ) or len(set(labels)) != len(labels):
            _invalid_scope()
        version_ids = sorted({_uuid(value) for value in versions})
        resource = (
            resources_for_scope(scope=scope, kind=kind).filter(pk=resource_id).first()
        )
        if (
            resource is None
            or len(version_ids) != len(versions)
            or resource.versions.filter(pk__in=version_ids).count() != len(version_ids)
        ):
            _invalid_scope()
        normalized.append(
            {
                "kind": kind,
                "resource_id": resource_id,
                "labels": sorted(labels),
                "version_ids": version_ids,
            }
        )
    return sorted(normalized, key=lambda grant: grant["resource_id"]), []


@transaction.atomic
def issue_credential(
    *, principal: SkillPrincipal, request: dict, idempotency_key: UUID
) -> dict:
    require_credential_admin(principal=principal)
    require_consumption_enabled()
    if set(request) != {"purpose", "grants", "binding_ids", "expires_at"}:
        _invalid_scope()
    # Serialize issue/replay on an existing scope row, not an absent receipt.
    Workspace.no_workspace_objects.select_for_update().get(
        pk=principal.scope.workspace_id, organization_id=principal.scope.organization_id
    )
    expiry = request["expires_at"]
    if not isinstance(expiry, datetime) or timezone.is_naive(expiry):
        _invalid_scope()
    digest = canonical_digest({**request, "expires_at": expiry.isoformat()})
    lookup = {
        "organization_id": principal.scope.organization_id,
        "workspace_id": principal.scope.workspace_id,
        "principal_key": f"{principal.kind}:{principal.api_key_id or principal.user_id}",
        "operation": "issue_credential",
        "idempotency_key": idempotency_key,
    }
    existing = HubSkillContextOperation.objects.filter(**lookup).first()
    if existing:
        if existing.request_digest != digest:
            raise SkillsHubError(
                "idempotency_conflict",
                "This operation key has a different request.",
                status=409,
            )
        if existing.result_resource_id is None:
            raise SkillsHubError(
                "resource_not_found", "Credential receipt is unavailable.", status=404
            )
        credential = HubSkillContextCredential.objects.get(
            pk=existing.result_resource_id,
            organization_id=principal.scope.organization_id,
            workspace_id=principal.scope.workspace_id,
        )
        return {
            "id": str(credential.pk),
            "secret": None,
            "secret_available": False,
            "metadata": credential_metadata(credential),
        }
    now = timezone.now()
    if not now < expiry <= now + timedelta(days=MAX_CREDENTIAL_DAYS):
        _invalid_scope()
    grants, binding_ids = _validate_grants(
        scope=principal.scope,
        purpose=request["purpose"],
        grants=request["grants"],
        binding_ids=request["binding_ids"],
    )
    credential_id, random_secret = uuid4(), secrets.token_urlsafe(32)
    token = f"{credential_id}.{random_secret}"
    credential = HubSkillContextCredential.objects.create(
        id=credential_id,
        organization_id=principal.scope.organization_id,
        workspace_id=principal.scope.workspace_id,
        purpose=request["purpose"],
        grants=grants,
        binding_ids=binding_ids,
        secret_hash=_hash_secret(token),
        expires_at=expiry,
        created_by_id=principal.user_id,
    )
    HubSkillContextOperation.objects.create(
        **lookup,
        request_digest=digest,
        result_resource_id=credential.pk,
        response={"id": str(credential.pk)},
        completed_at=now,
    )
    return {
        "id": str(credential.pk),
        "secret": token,
        "secret_available": True,
        "metadata": credential_metadata(credential),
    }


def _hash_secret(token: str) -> str:
    return salted_hmac(
        "skills-context-credential-v1", token, algorithm="sha256"
    ).hexdigest()


def require_active_credential(
    *, principal: ContextCredentialPrincipal, purpose: str | None = None
) -> HubSkillContextCredential:
    require_consumption_enabled()
    credential = HubSkillContextCredential.objects.filter(
        pk=principal.credential_id,
        organization_id=principal.scope.organization_id,
        workspace_id=principal.scope.workspace_id,
    ).first()
    if credential is None or credential.purpose != principal.purpose:
        raise SkillsHubError(
            "context_credential_invalid", "Invalid Context credential.", status=401
        )
    if credential.revoked_at is not None:
        raise SkillsHubError(
            "context_credential_revoked", "Context credential was revoked.", status=403
        )
    if credential.expires_at <= timezone.now():
        raise SkillsHubError(
            "context_credential_expired", "Context credential expired.", status=401
        )
    require_valid_scope(scope=principal.scope)
    if purpose is not None and credential.purpose != purpose:
        raise SkillsHubError(
            "credential_operation_forbidden",
            "This credential cannot perform this operation.",
            status=403,
        )
    return credential


def record_credential_use(
    *, principal: ContextCredentialPrincipal, observed_at: datetime | None = None
) -> None:
    """Account for a successful authorized operation, never mere authentication.

    The optional time is a server observation, not a client timestamp. Greatest
    prevents a delayed request from moving the rotation signal backwards.
    """
    require_active_credential(principal=principal)
    now = timezone.now()
    observed_at = observed_at or now
    if timezone.is_naive(observed_at) or observed_at > now:
        raise ValueError("Credential use requires a past aware server timestamp.")
    updated = HubSkillContextCredential.objects.filter(
        pk=principal.credential_id,
        organization_id=principal.scope.organization_id,
        workspace_id=principal.scope.workspace_id,
        purpose=principal.purpose,
        revoked_at__isnull=True,
        expires_at__gt=now,
    ).update(
        last_used_at=Greatest(
            Coalesce(F("last_used_at"), Value(observed_at)), Value(observed_at)
        )
    )
    if not updated:
        # A revoke/expiry racing this final accounting boundary must fail closed.
        require_active_credential(principal=principal)
        raise SkillsHubError(
            "context_credential_invalid", "Invalid Context credential.", status=401
        )


def authenticate_credential(*, token: str) -> ContextCredentialPrincipal:
    parts = token.split(".")
    try:
        credential_id = UUID(parts[0])
    except (ValueError, IndexError):
        credential_id = None
    credential = (
        HubSkillContextCredential.objects.filter(pk=credential_id).first()
        if credential_id
        else None
    )
    # Always calculate/compare a keyed digest; errors never echo the token.
    expected = credential.secret_hash if credential else "0" * 64
    valid = constant_time_compare(expected, _hash_secret(token))
    if not valid or credential is None or len(parts) != 2 or len(parts[1]) < 43:
        raise SkillsHubError(
            "context_credential_invalid", "Invalid Context credential.", status=401
        )
    purpose: Literal["consume", "report"]
    if credential.purpose == "consume":
        purpose = "consume"
    elif credential.purpose == "report":
        purpose = "report"
    else:
        raise SkillsHubError(
            "context_credential_invalid", "Invalid Context credential.", status=401
        )
    principal = ContextCredentialPrincipal(
        scope=SkillScope(
            organization_id=credential.organization_id,
            workspace_id=credential.workspace_id,
        ),
        credential_id=credential.pk,
        purpose=purpose,
    )
    require_active_credential(principal=principal)
    return principal


def authorize_report_binding(
    *, principal: ContextCredentialPrincipal, binding_id: UUID
) -> HubSkillContextBinding:
    credential = require_active_credential(principal=principal, purpose="report")
    binding = HubSkillContextBinding.objects.filter(
        pk=binding_id,
        pk__in=credential.binding_ids,
        organization_id=principal.scope.organization_id,
        workspace_id=principal.scope.workspace_id,
        context__organization_id=principal.scope.organization_id,
        context__workspace_id=principal.scope.workspace_id,
        context__kind="agent_context",
        context__deleted=False,
        retired_at__isnull=True,
    ).first()
    if binding is None:
        raise SkillsHubError("resource_not_found", "Binding not found.", status=404)
    return binding


@transaction.atomic
def revoke_credential(*, principal: SkillPrincipal, credential_id: UUID) -> None:
    require_credential_admin(principal=principal)
    credential = (
        HubSkillContextCredential.objects.select_for_update()
        .filter(
            pk=credential_id,
            organization_id=principal.scope.organization_id,
            workspace_id=principal.scope.workspace_id,
        )
        .first()
    )
    if credential is None:
        raise SkillsHubError("resource_not_found", "Resource not found.", status=404)
    if credential.revoked_at is None:
        credential.revoked_at = timezone.now()
        credential.save(update_fields=["revoked_at"])
