"""Publisher accounts and profiles (SPEC §2.3, §6.5)."""

from __future__ import annotations

from urllib.parse import urlparse

from django.db import IntegrityError, transaction
from django.utils import timezone

from omega_hub import reserved
from omega_hub.errors import HubError
from omega_hub.models import Publisher
from omega_hub.permissions import can_act_for_publisher, org_level
from omega_hub.services import audit, email_verification, storage
from omega_hub.services.refs import handle_used
from tfc.constants.levels import Level

PROFILE_FIELDS = {"displayName": "display_name", "bio": "bio", "website": "website", "githubLogin": "github_login"}


def check_handle(handle) -> str:
    if not isinstance(handle, str) or not reserved.HANDLE.match(handle):
        raise HubError("OMEGA_HUB_HANDLE_INVALID", 400)
    if reserved.is_reserved_handle(handle):
        raise HubError("OMEGA_HUB_HANDLE_RESERVED", 400)
    if handle_used(handle):
        raise HubError("OMEGA_HUB_HANDLE_TAKEN", 409)
    return handle


def check_display_name(value, handle: str | None) -> str:
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= 60:
        raise HubError("OMEGA_HUB_INVALID", 400, "displayName must be 1-60 characters")
    if reserved.display_name_is_reserved(value, handle):
        raise HubError("OMEGA_HUB_DISPLAY_NAME_RESERVED", 400)
    return value.strip()


def _check_profile(updates: dict, handle: str) -> dict:
    clean = {}
    for key, field in PROFILE_FIELDS.items():
        if key not in updates:
            continue
        value = updates[key]
        if field == "display_name":
            clean[field] = check_display_name(value, handle)
        elif field == "bio":
            if not isinstance(value, str) or len(value) > 160:
                raise HubError("OMEGA_HUB_INVALID", 400, "bio is at most 160 characters")
            clean[field] = value.strip()
        elif field == "website":
            parsed = urlparse(value or "")
            if value and (parsed.scheme != "https" or not parsed.netloc or len(value) > 300):
                raise HubError("OMEGA_HUB_INVALID", 400, "website must be an https URL")
            clean[field] = value or ""
        elif field == "github_login":
            if value and (not isinstance(value, str) or len(value) > 39 or not value.replace("-", "").isalnum()):
                raise HubError("OMEGA_HUB_INVALID", 400, "githubLogin is a GitHub login")
            clean[field] = value or ""
    return clean


@transaction.atomic
def create_publisher(user, *, handle, kind, display_name, organization_id=None) -> Publisher:
    email_verification.require_verified(user)  # decision 13: publishing needs a verified email (HUB-25)
    handle = check_handle(handle)
    display_name = check_display_name(display_name, handle)
    if kind == Publisher.Kind.USER:
        if Publisher.objects.filter(kind=Publisher.Kind.USER, owner_user=user).exists():
            raise HubError("OMEGA_HUB_ONE_USER_PUBLISHER", 409)
        fields = {"owner_user": user}
    elif kind == Publisher.Kind.ORGANIZATION:
        if not organization_id or org_level(user, organization_id) < Level.ADMIN:
            raise HubError("OMEGA_HUB_FORBIDDEN", 403, "Only an Admin or Owner of the organisation can create its publisher.")
        fields = {"owner_org_id": organization_id}
    else:
        raise HubError("OMEGA_HUB_INVALID", 400, "kind is user or organization")
    try:
        with transaction.atomic():
            publisher = Publisher.objects.create(handle=handle, kind=kind, display_name=display_name, **fields)
    except IntegrityError as exc:
        raise HubError("OMEGA_HUB_HANDLE_TAKEN", 409) from exc
    audit.record("publisher.created", target_type="publisher", target_id=publisher.pk, actor=user,
                 publisher=publisher, data={"handle": handle, "kind": kind})
    return publisher


@transaction.atomic
def update_profile(user, publisher: Publisher, updates: dict) -> Publisher:
    if not can_act_for_publisher(user, publisher):
        raise HubError("OMEGA_HUB_NOT_PUBLISHER_OWNER", 403)
    clean = _check_profile(updates, publisher.handle)
    for field, value in clean.items():
        setattr(publisher, field, value)
    publisher.save()
    audit.record("publisher.updated", target_type="publisher", target_id=publisher.pk, actor=user,
                 publisher=publisher, data={"fields": sorted(clean)})
    return publisher


def set_avatar(user, publisher: Publisher, data: bytes) -> Publisher:
    if not can_act_for_publisher(user, publisher):
        raise HubError("OMEGA_HUB_NOT_PUBLISHER_OWNER", 403)
    if len(data) > 256 * 1024:
        raise HubError("OMEGA_HUB_AVATAR_INVALID", 400)
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        ext = "png"
    elif data.startswith(b"\xff\xd8\xff"):
        ext = "jpg"
    else:
        raise HubError("OMEGA_HUB_AVATAR_INVALID", 400)
    key = storage.avatar_key(publisher.pk, ext)
    storage.put_bytes(key, data)
    with transaction.atomic():
        publisher.avatar_key = key
        publisher.save(update_fields=["avatar_key", "updated_at"])
        audit.record("publisher.updated", target_type="publisher", target_id=publisher.pk, actor=user,
                     publisher=publisher, data={"fields": ["avatar"]})
    return publisher


@transaction.atomic
def request_verification(user, publisher: Publisher, evidence) -> Publisher:
    if not can_act_for_publisher(user, publisher):
        raise HubError("OMEGA_HUB_NOT_PUBLISHER_OWNER", 403)
    if not isinstance(evidence, str) or not 1 <= len(evidence.strip()) <= 1000:
        raise HubError("OMEGA_HUB_INVALID", 400, "evidence is 1-1,000 characters")
    publisher.verification_request = evidence.strip()
    publisher.save(update_fields=["verification_request", "updated_at"])
    audit.record("publisher.updated", target_type="publisher", target_id=publisher.pk, actor=user,
                 publisher=publisher, data={"fields": ["verification_request"], "requestedAt": timezone.now().isoformat()})
    return publisher


def publishers_for(user) -> list[Publisher]:
    from accounts.models.organization_membership import OrganizationMembership

    org_ids = list(
        OrganizationMembership.no_workspace_objects.filter(user=user, is_active=True)
        .values_list("organization_id", "level", "role")
    )
    admin_orgs = [org for org, level, role in org_ids if (level if level is not None else Level.STRING_TO_LEVEL.get(role, 0)) >= Level.ADMIN]
    return list(
        Publisher.objects.filter(owner_user=user) | Publisher.objects.filter(owner_org_id__in=admin_orgs)
    )
