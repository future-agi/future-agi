"""Declared targets, immutable threads and independent client-reported stage facts."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import NoReturn
from uuid import UUID

from django.db import IntegrityError, transaction
from django.db.models import F, QuerySet
from django.db.models.functions import Greatest
from django.utils import timezone

from model_hub.models.skills_hub import HubSkill, HubSkillVersion
from model_hub.models.skills_hub_v12 import (
    HubSkillContextBinding,
    HubSkillContextOperation,
    HubSkillContextReceipt,
    HubSkillContextResolution,
    HubSkillContextSession,
)
from model_hub.selectors.skills_hub_v12 import get_resource
from model_hub.services.skills_hub.errors import SkillsHubError
from model_hub.services.skills_hub.v11.principals import (
    require_private_principal,
    require_scope_access,
)
from model_hub.services.skills_hub.v11.suites import validate_file_path
from model_hub.services.skills_hub.v11.types import SkillPrincipal, SkillScope
from model_hub.services.skills_hub.v12.credentials import (
    authorize_report_binding,
    canonical_digest,
    record_credential_use,
    require_active_credential,
)
from model_hub.services.skills_hub.v12.limits import (
    MAX_RECEIPT_BYTES,
    RESOLUTION_SECONDS,
)
from model_hub.services.skills_hub.v12.resolution_leases import (
    check_quarantine_closure,
    issue_resolution,
)
from model_hub.services.skills_hub.v12.types import (
    ContextCredentialPrincipal,
    ContextPrincipal,
)

BINDING_FIELDS = {
    "name",
    "kind",
    "selector",
    "update_mode",
    "profile",
    "profile_version",
    "repository",
    "expected_report_interval_seconds",
}
SESSION_FIELDS = {
    "client_session_key",
    "incarnation_id",
    "resume_session_id",
    "adapter_version",
    "runtime_version",
    "runtime_config_digest",
}
RECEIPT_FIELDS = {
    "event_id",
    "session_id",
    "instance_id",
    "sequence",
    "stage",
    "version_id",
    "digest",
    "materialization_digest",
    "observed_at",
    "details",
}
DETAIL_FIELDS = {
    "path",
    "sha256",
    "ranges",
    "runtime_version",
    "adapter_version",
    "diagnostic_codes",
    "trace_ref",
}
STAGES = {
    "resolved",
    "installed",
    "discovered",
    "instructions_delivered",
    "skill_file_delivered",
    "failed",
    "closed",
}


def _invalid(message: str, code: str = "invalid_context_definition") -> NoReturn:
    raise SkillsHubError(code, message, status=400)


def _shape(value: object, fields: set[str]) -> None:
    if not isinstance(value, dict) or set(value) != fields:
        _invalid("Supply exactly the documented fields.")


def _text(value: object, maximum: int) -> None:
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > maximum
        or any(ord(c) < 32 for c in value)
    ):
        _invalid("Invalid bounded text field.")


def _uuid(value: object) -> UUID:
    try:
        return UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        _invalid("Use a UUID.")


def _digest(value: object) -> None:
    if not isinstance(value, str) or not re.fullmatch(r"sha256:[a-f0-9]{64}", value):
        _invalid("Use a SHA-256 digest.")


def _json(value: dict) -> dict:
    def scalar(item: object) -> str:
        if isinstance(item, UUID):
            return str(item)
        if isinstance(item, datetime) and timezone.is_aware(item):
            return item.isoformat()
        raise TypeError("Invalid JSON scalar")

    try:
        result: object = json.loads(json.dumps(value, default=scalar, allow_nan=False))
        if not isinstance(result, dict):
            _invalid("Use a JSON object.")
        return result
    except (TypeError, ValueError, UnicodeError, RecursionError):
        _invalid("Use bounded finite JSON values.")


def _principal_key(principal: ContextPrincipal) -> str:
    if isinstance(principal, ContextCredentialPrincipal):
        return f"credential:{principal.credential_id}"
    return f"{principal.kind}:{principal.api_key_id or principal.user_id}"


def _page(
    rows: QuerySet[HubSkillContextBinding],
    page: int,
    page_size: int,
    render: Callable[[HubSkillContextBinding], dict],
) -> dict:
    if (
        type(page) is not int
        or page < 1
        or type(page_size) is not int
        or not 1 <= page_size <= 100
    ):
        _invalid("Invalid page.", "invalid_selector")
    count = rows.count()
    return {
        "results": [
            render(row) for row in rows[(page - 1) * page_size : page * page_size]
        ],
        "count": count,
        "page": page,
        "page_size": page_size,
        "total_pages": (count + page_size - 1) // page_size,
    }


def binding_output(binding: HubSkillContextBinding) -> dict:
    return {
        **{field: getattr(binding, field) for field in BINDING_FIELDS},
        "id": str(binding.pk),
        "context_id": str(binding.context_id),
        "revision": binding.revision,
        "retired_at": binding.retired_at,
    }


def _validate_binding(request: dict) -> dict:
    _shape(request, BINDING_FIELDS)
    for field, bound in [("name", 120), ("profile", 64), ("profile_version", 64)]:
        _text(request[field], bound)
    selector = request["selector"]
    if not isinstance(selector, dict) or set(selector) not in ({"version"}, {"label"}):
        _invalid("Choose exactly one selector.", "invalid_dependency_selector")
    if "version" in selector and (
        type(selector["version"]) is not int or selector["version"] < 1
    ):
        _invalid("Choose a published version.")
    if "label" in selector and (
        not isinstance(selector["label"], str)
        or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,39}", selector["label"])
    ):
        _invalid("Choose a valid label.")
    interval = request["expected_report_interval_seconds"]
    if type(interval) is not int or not 1 <= interval <= 31536000:
        _invalid("Choose a bounded reporting interval.")
    if request["kind"] == "repository":
        if request["update_mode"] != "repository_lock":
            _invalid("Repository targets use checked-in locks.")
        repository = request["repository"]
        _shape(
            repository, {"provider", "repository_id", "default_branch", "project_scope"}
        )
        if repository["provider"] != "github":
            _invalid("Unsupported repository provider.")
        _text(repository["repository_id"], 200)
        _text(repository["default_branch"], 255)
        if repository["project_scope"] != "":
            validate_file_path(repository["project_scope"])
    elif request["kind"] == "runtime":
        if request["repository"] is not None or request["update_mode"] not in {
            "manual",
            "new_thread",
        }:
            _invalid("Runtime targets use manual or new-thread updates.")
    else:
        _invalid("Unsupported target kind.")
    return _json(request)


def _bindings(principal: ContextPrincipal) -> QuerySet[HubSkillContextBinding]:
    return HubSkillContextBinding.objects.filter(
        organization_id=principal.scope.organization_id,
        workspace_id=principal.scope.workspace_id,
        context__organization_id=principal.scope.organization_id,
        context__workspace_id=principal.scope.workspace_id,
        context__kind="agent_context",
        context__deleted=False,
        retired_at__isnull=True,
    )


def _binding(
    *, principal: ContextPrincipal, binding_id: UUID, lock: bool = False
) -> HubSkillContextBinding:
    rows = _bindings(principal)
    if lock:
        rows = rows.select_for_update(of=("self",))
    binding = rows.select_related("context").filter(pk=binding_id).first()
    if binding is None:
        raise SkillsHubError("resource_not_found", "Binding not found.", status=404)
    return binding


def list_bindings(
    *, principal: SkillPrincipal, context_id: UUID, page: int = 1, page_size: int = 25
) -> dict:
    get_resource(principal=principal, resource_id=context_id, kind="agent_context")
    return _page(
        _bindings(principal).filter(context_id=context_id).order_by("name", "id"),
        page,
        page_size,
        binding_output,
    )


def _operation(
    *, principal: ContextPrincipal, operation: str, key: UUID, request: dict
) -> tuple[dict, str, HubSkillContextOperation | None]:
    lookup = {
        "organization_id": principal.scope.organization_id,
        "workspace_id": principal.scope.workspace_id,
        "principal_key": _principal_key(principal),
        "operation": operation,
        "idempotency_key": _uuid(key),
    }
    digest = canonical_digest(request)
    prior, created = HubSkillContextOperation.objects.get_or_create(
        **lookup, defaults={"request_digest": digest}
    )
    if prior.request_digest != digest:
        raise SkillsHubError(
            "idempotency_conflict",
            "This operation key belongs to a different request.",
            status=409,
        )
    return lookup, digest, None if created else prior


def create_binding(
    *, principal: SkillPrincipal, context_id: UUID, request: dict, idempotency_key: UUID
) -> dict:
    require_private_principal(principal=principal)
    request = _validate_binding(request)
    with transaction.atomic():
        context = get_resource(
            principal=principal, resource_id=context_id, kind="agent_context", lock=True
        )
        lookup, digest, prior = _operation(
            principal=principal,
            operation="create_binding",
            key=idempotency_key,
            request={"context_id": str(context_id), "binding": request},
        )
        if prior:
            return dict(prior.response)
        binding = HubSkillContextBinding.objects.create(
            organization_id=principal.scope.organization_id,
            workspace_id=principal.scope.workspace_id,
            context=context,
            **request,
        )
        response = _json(binding_output(binding))
        HubSkillContextOperation.objects.filter(**lookup).update(
            request_digest=digest,
            result_resource_id=binding.pk,
            response=response,
            completed_at=timezone.now(),
        )
        return response


def update_binding(
    *, principal: SkillPrincipal, binding_id: UUID, revision: int, request: dict | None
) -> dict:
    """A None request explicitly retires; no external files or thread messages change."""
    require_private_principal(principal=principal)
    require_scope_access(principal=principal, write=True)
    binding = _binding(principal=principal, binding_id=binding_id)
    values = _validate_binding(request) if request is not None else None
    with transaction.atomic():
        get_resource(
            principal=principal,
            resource_id=binding.context_id,
            kind="agent_context",
            lock=True,
        )
        binding = _binding(principal=principal, binding_id=binding_id, lock=True)
        if type(revision) is not int or revision != binding.revision:
            raise SkillsHubError(
                "binding_conflict", "Target configuration changed.", status=409
            )
        if values is None:
            binding.retired_at = timezone.now()
        else:
            for field, value in values.items():
                setattr(binding, field, value)
        binding.revision += 1
        binding.save()
        return binding_output(binding)


def _session_principal(principal: ContextPrincipal) -> ContextCredentialPrincipal:
    if not isinstance(principal, ContextCredentialPrincipal):
        raise SkillsHubError(
            "credential_operation_forbidden",
            "Runtime sessions require an isolated consume credential.",
            status=403,
        )
    require_active_credential(principal=principal, purpose="consume")
    return principal


def _session_origin(
    *, principal: ContextPrincipal, session: HubSkillContextSession
) -> dict:
    if session.credential_id != (
        principal.credential_id
        if isinstance(principal, ContextCredentialPrincipal)
        else None
    ):
        raise SkillsHubError("resource_not_found", "Session not found.", status=404)
    operation = (
        HubSkillContextOperation.objects.filter(
            organization_id=principal.scope.organization_id,
            workspace_id=principal.scope.workspace_id,
            principal_key=_principal_key(principal),
            operation="open_session",
            result_resource_id=session.pk,
            response__history_mode="fresh",
        )
        .order_by("created_at")
        .first()
    )
    if operation is None:
        raise SkillsHubError(
            "thread_context_unknown",
            "The original thread mapping is unavailable. Do not re-resolve it.",
            status=409,
        )
    return dict(operation.response)


def _check_session(
    *, principal: ContextPrincipal, session: HubSkillContextSession
) -> dict:
    principal = _session_principal(principal)
    _binding(principal=principal, binding_id=session.binding_id)
    origin = _session_origin(principal=principal, session=session)
    if session.expires_at <= timezone.now():
        raise SkillsHubError(
            "thread_context_unknown",
            "The retained thread mapping expired. Start a clean thread.",
            status=409,
        )
    if session.digest != session.version.digest:
        raise SkillsHubError(
            "session_context_conflict",
            "The immutable thread identity changed.",
            status=409,
        )
    if isinstance(principal, ContextCredentialPrincipal):
        credential = require_active_credential(principal=principal, purpose="consume")
        resolution = HubSkillContextResolution.objects.filter(
            pk=origin["resolution_id"],
            credential=credential,
            version=session.version,
            organization_id=principal.scope.organization_id,
            workspace_id=principal.scope.workspace_id,
        ).first()
        if resolution is None or resolution.grant not in credential.grants:
            raise SkillsHubError(
                "credential_operation_forbidden",
                "The original session grant is unavailable.",
                status=403,
            )
    check_quarantine_closure(scope=principal.scope, version=session.version)
    return origin


def check_session_access(
    *, principal: ContextPrincipal, session_id: UUID, binding_id: UUID | None = None
) -> dict:
    principal = _session_principal(principal)
    rows = HubSkillContextSession.objects.select_related("version__skill").filter(
        pk=session_id,
        organization_id=principal.scope.organization_id,
        workspace_id=principal.scope.workspace_id,
    )
    if binding_id is not None:
        rows = rows.filter(binding_id=binding_id)
    session = rows.first()
    if session is None:
        raise SkillsHubError(
            "thread_context_unknown",
            "Thread mapping unavailable. Start a clean thread instead of re-resolving.",
            status=409,
        )
    _check_session(principal=principal, session=session)
    record_credential_use(principal=principal)
    return {
        "allowed": True,
        "version_id": str(session.version_id),
        "digest": session.digest,
        "checked_at": timezone.now(),
    }


def _desired(binding: HubSkillContextBinding) -> HubSkillVersion:
    selector = binding.selector
    if "version" in selector:
        number = selector["version"]
    elif selector["label"] == "latest":
        number = binding.context.latest_version_number
    else:
        label = binding.context.labels.filter(name=selector["label"]).first()
        if label is None:
            raise SkillsHubError(
                "label_not_found", "The requested label is not assigned.", status=404
            )
        number = label.version.number
    version = (
        binding.context.versions.select_related("skill").filter(number=number).first()
    )
    if version is None:
        raise SkillsHubError(
            "version_not_found", "The requested version is unavailable.", status=404
        )
    return version


def _session_output(
    session: HubSkillContextSession,
    requested: dict,
    mode: str,
    resolution: HubSkillContextResolution | None,
) -> dict:
    prefix = f"/model-hub/skills/contexts/{session.version.skill_id}/versions/{session.version.number}/"
    return {
        "session_id": str(session.pk),
        "version_id": str(session.version_id),
        "version": session.version.number,
        "digest": session.digest,
        "requested": requested,
        "binding_revision": session.binding_revision,
        "label_event_id": (
            str(session.label_event_id) if session.label_event_id else None
        ),
        "manifest_url": prefix + "manifest/",
        "history_mode": mode,
        "access_check_url": f"/model-hub/skills/contexts/bindings/{session.binding_id}/sessions/{session.pk}/access-check/",
        "resolution_id": str(resolution.pk) if resolution else None,
        "resolution_expires_at": (
            resolution.expires_at.isoformat() if resolution else None
        ),
    }


def open_context_session(
    *,
    principal: ContextPrincipal,
    binding_id: UUID,
    request: dict,
    idempotency_key: UUID,
) -> dict:
    principal = _session_principal(principal)
    _shape(request, SESSION_FIELDS)
    for field in ("adapter_version", "runtime_version"):
        _text(request[field], 100)
    _text(request["client_session_key"], 200)
    _digest(request["runtime_config_digest"])
    incarnation = _uuid(request["incarnation_id"])
    binding = _binding(principal=principal, binding_id=binding_id)
    with transaction.atomic():
        # Target mutations/releases serialize on the parent; heartbeats never change it.
        HubSkill._base_manager.select_for_update().get(pk=binding.context_id)
        binding = _binding(principal=principal, binding_id=binding_id, lock=True)
        principal = _session_principal(principal)
        lookup, digest, prior = _operation(
            principal=principal,
            operation="open_session",
            key=idempotency_key,
            request={"binding_id": str(binding_id), **request},
        )
        sessions = HubSkillContextSession.objects.select_related(
            "version__skill"
        ).filter(binding=binding)
        if prior:
            if prior.result_resource_id is None:
                raise SkillsHubError(
                    "thread_context_unknown",
                    "The original session receipt is incomplete.",
                    status=409,
                )
            session = sessions.filter(pk=prior.result_resource_id).first()
            if session is None:
                raise SkillsHubError(
                    "thread_context_unknown", "Thread mapping unavailable.", status=409
                )
            _check_session(principal=principal, session=session)
            record_credential_use(principal=principal)
            return dict(prior.response)
        resolution = None
        if request["resume_session_id"] is not None:
            session = sessions.filter(pk=_uuid(request["resume_session_id"])).first()
            if session is None:
                raise SkillsHubError(
                    "thread_context_unknown",
                    "Resume requires the original thread mapping.",
                    status=409,
                )
            if (
                any(
                    getattr(session, field) != request[field]
                    for field in (
                        "client_session_key",
                        "adapter_version",
                        "runtime_version",
                        "runtime_config_digest",
                    )
                )
                or session.incarnation_id != incarnation
            ):
                raise SkillsHubError(
                    "session_context_conflict",
                    "Resume must retain the original identity and runtime. Start a clean thread to upgrade.",
                    status=409,
                )
            origin = _check_session(principal=principal, session=session)
            requested = origin["requested"]
            if isinstance(principal, ContextCredentialPrincipal):
                original = HubSkillContextResolution.objects.get(
                    pk=origin["resolution_id"]
                )
                credential = require_active_credential(
                    principal=principal, purpose="consume"
                )
                resolution = HubSkillContextResolution.objects.create(
                    organization_id=principal.scope.organization_id,
                    workspace_id=principal.scope.workspace_id,
                    credential=credential,
                    version=session.version,
                    resource_kind="agent_context",
                    digest=session.digest,
                    requested=requested,
                    grant=original.grant,
                    expires_at=min(
                        timezone.now() + timedelta(seconds=RESOLUTION_SECONDS),
                        credential.expires_at,
                    ),
                )
            mode = "resumed"
        else:
            if sessions.filter(
                client_session_key=request["client_session_key"],
                incarnation_id=incarnation,
            ).exists():
                raise SkillsHubError(
                    "session_context_conflict",
                    "This thread already exists. Resume its recorded session.",
                    status=409,
                )
            version = _desired(binding)
            check_quarantine_closure(scope=principal.scope, version=version)
            requested = binding.selector
            if isinstance(principal, ContextCredentialPrincipal):
                resolution = issue_resolution(
                    principal=principal, version=version, requested=requested
                )
            event = None
            if "label" in requested and requested["label"] != "latest":
                event = (
                    binding.context.label_events.filter(label=requested["label"])
                    .order_by("-release_revision", "-created_at")
                    .first()
                )
            session = HubSkillContextSession.objects.create(
                organization_id=principal.scope.organization_id,
                workspace_id=principal.scope.workspace_id,
                binding=binding,
                client_session_key=request["client_session_key"],
                incarnation_id=incarnation,
                version=version,
                digest=version.digest,
                binding_revision=binding.revision,
                label_event=event,
                credential_id=(
                    principal.credential_id
                    if isinstance(principal, ContextCredentialPrincipal)
                    else None
                ),
                adapter_version=request["adapter_version"],
                runtime_version=request["runtime_version"],
                runtime_config_digest=request["runtime_config_digest"],
                last_seen_at=timezone.now(),
                expires_at=timezone.now() + timedelta(days=365),
            )
            mode = "fresh"
        now = timezone.now()
        sessions.filter(pk=session.pk).update(
            last_seen_at=Greatest(F("last_seen_at"), now),
            expires_at=Greatest(F("expires_at"), now + timedelta(days=365)),
        )
        response = _session_output(session, requested, mode, resolution)
        HubSkillContextOperation.objects.filter(**lookup).update(
            request_digest=digest,
            result_resource_id=session.pk,
            response=response,
            completed_at=now,
        )
        record_credential_use(principal=principal)
        return response


def _validate_receipt(receipt: dict, version: HubSkillVersion) -> dict:
    _shape(receipt, RECEIPT_FIELDS)
    for field in ("event_id", "instance_id", "version_id"):
        _uuid(receipt[field])
    if (
        type(receipt["sequence"]) is not int
        or not 0 <= receipt["sequence"] < 2**63
        or receipt["stage"] not in STAGES
    ):
        _invalid("Invalid stage or sequence.", "invalid_receipt")
    if receipt["digest"] != version.digest:
        raise SkillsHubError(
            "receipt_conflict", "The reported immutable identity differs.", status=409
        )
    if receipt["materialization_digest"] is not None:
        _digest(receipt["materialization_digest"])
    observed = receipt["observed_at"]
    if isinstance(observed, str):
        try:
            observed = datetime.fromisoformat(observed.replace("Z", "+00:00"))
        except ValueError:
            _invalid("Invalid observed timestamp.", "invalid_receipt")
    if (
        not isinstance(observed, datetime)
        or not timezone.is_aware(observed)
        or observed > timezone.now() + timedelta(minutes=5)
    ):
        _invalid(
            "Use an aware observed timestamp, not a future observation.",
            "invalid_receipt",
        )
    details = receipt["details"]
    _shape(details, DETAIL_FIELDS)
    _text(details["adapter_version"], 100)
    if details["runtime_version"] is not None:
        _text(details["runtime_version"], 100)
    codes = details["diagnostic_codes"]
    if (
        not isinstance(codes, list)
        or len(codes) > 20
        or any(
            not isinstance(code, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", code)
            for code in codes
        )
    ):
        _invalid("Diagnostics must be codes, not transcripts.", "invalid_receipt")
    if details["trace_ref"] is not None:
        from model_hub.services.skills_hub.v12.trace_source import normalize_trace_ref

        normalize_trace_ref(details["trace_ref"])
    ranges = details["ranges"]
    if not isinstance(ranges, list) or len(ranges) > 200:
        _invalid("Invalid delivered intervals.", "invalid_receipt")
    path = details["path"]
    if path is not None:
        validate_file_path(path)
        file = (
            version.files.select_related("blob")
            .filter(path=path, sha256=details["sha256"])
            .first()
        )
        if file is None:
            _invalid("The file is not in this release.", "invalid_receipt")
        for interval in ranges:
            if (
                not isinstance(interval, list)
                or len(interval) != 2
                or any(type(item) is not int for item in interval)
                or not 0 <= interval[0] <= interval[1] <= len(file.blob.content)
            ):
                _invalid("Invalid delivered interval.", "invalid_receipt")
    elif details["sha256"] is not None or ranges:
        _invalid("Intervals require an exact release file.", "invalid_receipt")
    if receipt["stage"] == "instructions_delivered" and path != "AGENTS.md":
        _invalid("Standing delivery must identify AGENTS.md.", "invalid_receipt")
    if receipt["stage"] == "skill_file_delivered" and (
        not isinstance(path, str) or not path.startswith("skills/")
    ):
        _invalid("Child delivery must identify a linked skill file.", "invalid_receipt")
    result = _json({**receipt, "observed_at": observed})
    if len(json.dumps(result).encode("utf-8")) > MAX_RECEIPT_BYTES:
        raise SkillsHubError("payload_too_large", "Receipt exceeds 64 KiB.", status=413)
    return result


def append_receipt(
    *, principal: ContextPrincipal, binding_id: UUID, receipt: dict
) -> dict:
    if isinstance(principal, ContextCredentialPrincipal):
        authorize_report_binding(principal=principal, binding_id=binding_id)
    else:
        require_private_principal(principal=principal)
        require_scope_access(principal=principal, write=True)
    binding = _binding(principal=principal, binding_id=binding_id)
    version = (
        binding.context.versions.select_related("skill")
        .filter(pk=_uuid(receipt.get("version_id")))
        .first()
    )
    if version is None:
        raise SkillsHubError("resource_not_found", "Version not found.", status=404)
    normalized = _validate_receipt(receipt, version)
    digest = canonical_digest({"binding_id": str(binding_id), **normalized})
    key = {
        "organization_id": principal.scope.organization_id,
        "workspace_id": principal.scope.workspace_id,
        "reporter_key": _principal_key(principal),
        "event_id": normalized["event_id"],
    }
    try:
        with transaction.atomic():
            HubSkill._base_manager.select_for_update().get(pk=binding.context_id)
            binding = _binding(principal=principal, binding_id=binding_id, lock=True)
            if isinstance(principal, ContextCredentialPrincipal):
                authorize_report_binding(principal=principal, binding_id=binding_id)
            else:
                require_scope_access(principal=principal, write=True)
            prior = HubSkillContextReceipt.objects.filter(**key).first()
            if prior:
                if prior.request_digest != digest:
                    raise SkillsHubError(
                        "receipt_conflict",
                        "This event belongs to different content.",
                        status=409,
                    )
                if isinstance(principal, ContextCredentialPrincipal):
                    record_credential_use(principal=principal)
                return {"id": str(prior.pk), "accepted": True}
            session = None
            if normalized["session_id"] is not None:
                session = HubSkillContextSession.objects.filter(
                    pk=_uuid(normalized["session_id"]),
                    binding=binding,
                    version=version,
                    digest=normalized["digest"],
                    incarnation_id=normalized["instance_id"],
                    expires_at__gt=timezone.now(),
                ).first()
                if session is None:
                    raise SkillsHubError(
                        "session_context_conflict",
                        "Receipt does not match a retained session incarnation.",
                        status=409,
                    )
            if normalized["stage"] not in {"failed", "closed"}:
                check_quarantine_closure(scope=principal.scope, version=version)
            if HubSkillContextReceipt.objects.filter(
                binding=binding,
                instance_id=normalized["instance_id"],
                sequence=normalized["sequence"],
            ).exists():
                raise SkillsHubError(
                    "receipt_conflict",
                    "This instance sequence is already recorded.",
                    status=409,
                )
            row = HubSkillContextReceipt.objects.create(
                **key,
                binding=binding,
                session=session,
                instance_id=normalized["instance_id"],
                sequence=normalized["sequence"],
                stage=normalized["stage"],
                version=version,
                digest=normalized["digest"],
                materialization_digest=normalized["materialization_digest"],
                request_digest=digest,
                observed_at=normalized["observed_at"],
                details=normalized["details"],
                origin="client_reported",
                expires_at=timezone.now() + timedelta(days=90),
            )
            if isinstance(principal, ContextCredentialPrincipal):
                record_credential_use(principal=principal)
            return {"id": str(row.pk), "accepted": True}
    except IntegrityError as exc:
        if not any(
            name in str(exc)
            for name in ("hub_ctx_receipt_event", "hub_ctx_receipt_sequence")
        ):
            raise
        raise SkillsHubError(
            "receipt_conflict", "This receipt identity is already recorded.", status=409
        ) from exc


def _fact(row: HubSkillContextReceipt) -> dict:
    return {
        "version_id": str(row.version_id),
        "digest": row.digest,
        "observed_at": row.observed_at,
        "received_at": row.created_at,
        "origin": row.origin,
        "instance_id": str(row.instance_id),
        "sequence": row.sequence,
        "session_id": str(row.session_id) if row.session_id else None,
    }


def _observation(
    binding: HubSkillContextBinding, now: datetime, since: datetime
) -> dict:
    desired, states = None, set()
    try:
        version = _desired(binding)
        desired = {"version_id": str(version.pk), "digest": version.digest}
        check_quarantine_closure(
            scope=SkillScope(binding.organization_id, binding.workspace_id),
            version=version,
        )
    except SkillsHubError as exc:
        if exc.code not in {
            "label_not_found",
            "version_not_found",
            "context_quarantined",
            "dependency_unavailable",
        }:
            raise
        states.add("quarantined" if exc.code == "context_quarantined" else "unknown")
    rows = list(
        binding.hub_skill_receipts.filter(created_at__gte=since, expires_at__gt=now)
        .order_by("instance_id", "stage", "-sequence")
        .distinct("instance_id", "stage")[:1001]
    )
    complete = len(rows) <= 1000
    if not complete:
        rows = rows[:1000]
        states.add("unknown")
    closed = {row.instance_id: row.sequence for row in rows if row.stage == "closed"}
    active = [
        row
        for row in rows
        if row.stage != "closed" and row.sequence > closed.get(row.instance_id, -1)
    ]
    # A successful EOF or a nonempty prefix is not full standing-instruction delivery.
    from django.db.models.functions import Length

    from model_hub.models.skills_hub import HubSkillFile

    lengths = dict(
        HubSkillFile.objects.filter(
            version_id__in={row.version_id for row in active}, path="AGENTS.md"
        )
        .annotate(chars=Length("blob__content"))
        .values_list("version_id", "chars")
    )
    delivered = []
    for row in active:
        if row.stage != "instructions_delivered":
            continue
        covered = 0
        for start, end in sorted(row.details["ranges"]):
            if start > covered:
                break
            covered = max(covered, end)
        if covered == lengths.get(row.version_id) and covered > 0:
            delivered.append(row)
    facts = {}
    for stage, output in [
        ("resolved", "resolved"),
        ("installed", "installed"),
        ("discovered", "discovered"),
        ("instructions_delivered", "loaded"),
    ]:
        observations = (
            delivered
            if output == "loaded"
            else [row for row in active if row.stage == stage]
        )
        facts[output] = (
            _fact(max(observations, key=lambda row: row.created_at))
            if observations
            else None
        )
    # J6: a successful reporter is not evidence for every declared instance or
    # retained thread. Keep historical loaded facts, but never turn partial
    # cohort coverage (or a later failure) into a recovered target.
    delivered_instances = {row.instance_id for row in delivered}
    active_instances = {row.instance_id for row in active}
    delivered_sessions = {row.session_id for row in delivered if row.session_id}
    closed_sessions = {
        row.session_id for row in rows if row.stage == "closed" and row.session_id
    } - {row.session_id for row in active if row.session_id}
    # Expiry is loss of resumability, not evidence that the application closed.
    unobserved_sessions = binding.hub_skill_sessions.exclude(
        pk__in=delivered_sessions | closed_sessions
    ).exists()
    failed_sequences = {
        row.instance_id: row.sequence for row in active if row.stage == "failed"
    }
    if (
        active_instances - delivered_instances
        or unobserved_sessions
        or any(
            failed_sequences.get(row.instance_id, -1) > row.sequence
            for row in delivered
        )
    ):
        states.add("unknown")
    if any(
        (now - row.observed_at).total_seconds()
        > binding.expected_report_interval_seconds
        for row in delivered
    ):
        states.add("stale_observation")
    identities = {str(row.version_id) for row in delivered}
    if len(identities) > 1:
        states.add("mixed_sessions")
    if desired and identities:
        if (
            identities == {desired["version_id"]}
            and complete
            and not states.intersection({"unknown", "quarantined", "stale_observation"})
        ):
            states.add("matches_target")
            if "version" in binding.selector:
                states.add("pinned_as_intended")
        elif identities - {desired["version_id"]}:
            states.add("update_available")
            if any(
                row.session_id and str(row.version_id) != desired["version_id"]
                for row in delivered
            ):
                states.add("old_thread_pinned")
    if not identities:
        states.add("unknown")
    if any("local_modifications" in row.details["diagnostic_codes"] for row in active):
        states.add("local_modifications")
    newest = max((row.created_at for row in active), default=None)
    age = (now - newest).total_seconds() if newest else None
    if age is not None and age > binding.expected_report_interval_seconds:
        states.add("stale_observation")
    if states.intersection(
        {"unknown", "quarantined", "stale_observation", "local_modifications"}
    ):
        states.difference_update({"matches_target", "pinned_as_intended"})
    action = (
        "review_quarantine"
        if "quarantined" in states
        else (
            "review_local_changes"
            if "local_modifications" in states
            else (
                "start_clean_thread"
                if "old_thread_pinned" in states
                else (
                    "observe_runtime"
                    if "unknown" in states
                    else "check_reporter" if "stale_observation" in states else "none"
                )
            )
        )
    )
    return {
        "binding": binding_output(binding),
        "desired": desired,
        **facts,
        "observation_age_seconds": age,
        "states": sorted(states),
        "next_action_code": action,
        "observations_complete": complete,
    }


def adoption_summary(
    *,
    principal: SkillPrincipal,
    context_id: UUID,
    window: dict,
    page: int = 1,
    page_size: int = 25,
) -> dict:
    get_resource(principal=principal, resource_id=context_id, kind="agent_context")
    days = window.get("window_days", 7)
    if type(days) is not int or days not in {7, 30, 90}:
        _invalid("Choose a 7, 30 or 90 day window.", "invalid_selector")
    now = timezone.now()
    rows = (
        _bindings(principal)
        .filter(context_id=context_id)
        .select_related("context")
        .order_by("name", "id")
    )
    return {
        "as_of": now,
        "coverage": "declared_bindings",
        **_page(
            rows,
            page,
            page_size,
            lambda row: _observation(row, now, now - timedelta(days=days)),
        ),
    }


def purge_expired_consumer_metadata(*, batch_size: int = 100) -> dict:
    """Called by the existing retention activity; never starts another scheduler."""
    if type(batch_size) is not int or not 1 <= batch_size <= 1000:
        _invalid("Invalid retention batch size.")
    now = timezone.now()
    with transaction.atomic():
        ids = list(
            HubSkillContextReceipt.objects.filter(expires_at__lte=now)
            .order_by("expires_at")
            .values_list("pk", flat=True)[:batch_size]
        )
        HubSkillContextReceipt.objects.filter(pk__in=ids).delete()
        sessions = list(
            HubSkillContextSession.objects.filter(expires_at__lte=now)
            .order_by("expires_at")
            .values_list("pk", flat=True)[:batch_size]
        )
        HubSkillContextSession.objects.filter(pk__in=sessions).delete()
    return {"receipts": len(ids), "sessions": len(sessions)}
