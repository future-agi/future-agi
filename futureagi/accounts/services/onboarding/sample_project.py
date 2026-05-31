import structlog
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from accounts.models import OnboardingSampleProject
from accounts.services.onboarding.activation_events import record_event
from accounts.services.onboarding.sample_artifacts import (
    ensure_observe_sample_artifacts,
    validate_sample_artifacts,
)
from accounts.services.onboarding.sample_manifest import (
    DEFAULT_SAMPLE_MANIFEST_ID,
    DEFAULT_SAMPLE_MANIFEST_VERSION,
    get_sample_manifest,
    sample_idempotency_key,
)

logger = structlog.get_logger(__name__)

REAL_SETUP_HREF = "/dashboard/observe?setup=true&source=onboarding"
SAMPLE_HOME_HREF = "/dashboard/home?sample=true"


def _empty_state(
    *,
    status,
    manifest,
    available,
    created=False,
    is_hidden=False,
    hidden_reason=None,
    blocked_reason=None,
    missing_artifacts=None,
    entry_route=None,
    last_opened_at=None,
    artifact_refs=None,
    health=None,
    is_repairable=False,
):
    entry_routes = [entry_route] if entry_route else []
    href = entry_route or (SAMPLE_HOME_HREF if available and not is_hidden else None)
    return {
        "available": bool(available),
        "created": bool(created),
        "status": status,
        "href": href,
        "version": manifest["manifest_version"] if manifest else None,
        "is_hidden": bool(is_hidden),
        "hidden_reason": hidden_reason,
        "entry_routes": entry_routes,
        "missing_artifacts": list(missing_artifacts or []),
        "last_opened_at": last_opened_at,
        "manifest_id": manifest["manifest_id"] if manifest else None,
        "manifest_version": manifest["manifest_version"] if manifest else None,
        "label": manifest["sample_label"] if manifest else "Sample",
        "entry_route": entry_route,
        "is_repairable": bool(is_repairable),
        "blocked_reason": blocked_reason,
        "artifact_refs": artifact_refs or {},
        "health": health or {},
        "real_setup_href": REAL_SETUP_HREF,
    }


def _unavailable_state(manifest, reason):
    return _empty_state(
        status=OnboardingSampleProject.STATUS_UNAVAILABLE,
        manifest=manifest,
        available=False,
        is_hidden=True,
        hidden_reason=reason,
        blocked_reason=reason,
        health={"blocked_reason": reason},
    )


def _get_sample_project(organization, workspace, manifest):
    if organization is None or workspace is None:
        return None
    return (
        OnboardingSampleProject.no_workspace_objects.filter(
            organization=organization,
            workspace=workspace,
            manifest_id=manifest["manifest_id"],
            manifest_version=manifest["manifest_version"],
        )
        .order_by("-created_at")
        .first()
    )


def _status_from_validation(sample_project, validation):
    if sample_project.hidden_at:
        return OnboardingSampleProject.STATUS_HIDDEN
    if not validation["route_ready"]:
        if sample_project.repair_attempts:
            return OnboardingSampleProject.STATUS_REPAIR_FAILED
        return OnboardingSampleProject.STATUS_PARTIALLY_READY
    return OnboardingSampleProject.STATUS_READY_FOR_OBSERVE


def _state_from_project(sample_project, manifest, *, is_enabled=True):
    if not is_enabled:
        return _unavailable_state(manifest, "feature_disabled")

    validation = validate_sample_artifacts(sample_project, manifest)
    status = sample_project.status
    if status not in {
        OnboardingSampleProject.STATUS_CREATING,
        OnboardingSampleProject.STATUS_HIDDEN,
    }:
        status = _status_from_validation(sample_project, validation)

    is_hidden = bool(sample_project.hidden_at) or status == (
        OnboardingSampleProject.STATUS_HIDDEN
    )
    route_ready = validation["route_ready"]
    unavailable = status in {
        OnboardingSampleProject.STATUS_UNAVAILABLE,
        OnboardingSampleProject.STATUS_REPAIR_FAILED,
    }
    missing = validation["missing_artifacts"]
    return _empty_state(
        status=status,
        manifest=manifest,
        available=not unavailable
        and not is_hidden
        and (route_ready or status == OnboardingSampleProject.STATUS_CREATING),
        created=True,
        is_hidden=is_hidden,
        hidden_reason="user_hidden" if is_hidden else None,
        blocked_reason=(
            "sample_hidden"
            if is_hidden
            else "sample_artifact_missing"
            if not route_ready and status != OnboardingSampleProject.STATUS_CREATING
            else None
        ),
        missing_artifacts=missing,
        entry_route=validation["entry_route"],
        last_opened_at=sample_project.last_opened_at,
        artifact_refs=sample_project.artifact_refs,
        health=sample_project.health,
        is_repairable=bool(missing) and not is_hidden,
    )


def get_sample_project_state(
    user,
    organization,
    workspace,
    *,
    is_enabled=True,
    can_create=True,
    manifest_id=None,
    manifest_version=None,
):
    manifest = get_sample_manifest(manifest_id, manifest_version)
    if manifest is None:
        return _empty_state(
            status=OnboardingSampleProject.STATUS_UNAVAILABLE,
            manifest={
                "manifest_id": manifest_id or DEFAULT_SAMPLE_MANIFEST_ID,
                "manifest_version": manifest_version or DEFAULT_SAMPLE_MANIFEST_VERSION,
                "sample_label": "Sample",
            },
            available=False,
            is_hidden=True,
            hidden_reason="unknown_manifest",
            blocked_reason="unknown_manifest",
            health={"blocked_reason": "unknown_manifest"},
        )
    if not is_enabled:
        return _unavailable_state(manifest, "feature_disabled")
    if organization is None or workspace is None:
        return _unavailable_state(manifest, "workspace_missing")

    sample_project = _get_sample_project(organization, workspace, manifest)
    if sample_project:
        return _state_from_project(sample_project, manifest, is_enabled=is_enabled)

    return _empty_state(
        status=OnboardingSampleProject.STATUS_NOT_CREATED,
        manifest=manifest,
        available=bool(can_create),
        created=False,
        is_hidden=not can_create,
        hidden_reason=None if can_create else "missing_permission",
        blocked_reason=None if can_create else "missing_permission",
        health={} if can_create else {"blocked_reason": "missing_permission"},
    )


def _save_sample_project(sample_project, *, update_fields):
    fields = set(update_fields)
    fields.add("updated_at")
    sample_project.save(update_fields=sorted(fields))


def _compact_email_context(email_context):
    return {
        key: value
        for key, value in (email_context or {}).items()
        if value not in {None, ""}
    }


def _record_sample_event(
    sample_project,
    *,
    user,
    event_name,
    source,
    reason=None,
    email_context=None,
):
    metadata = {
        "sample_manifest_id": sample_project.manifest_id,
        "sample_manifest_version": sample_project.manifest_version,
        "path": "observe",
        "reason": reason,
        "sample_project_id": str(sample_project.id),
        **_compact_email_context(email_context),
    }
    idempotency_key = f"{sample_project.id}:{event_name}:{getattr(user, 'id', '')}"
    if event_name == "sample_trace_available":
        idempotency_key = f"{sample_project.id}:sample_trace_available"

    return record_event(
        user=user,
        organization=sample_project.organization,
        workspace=sample_project.workspace,
        event_name=event_name,
        source=source or "onboarding_home",
        product_path="sample",
        metadata=metadata,
        is_sample=True,
        idempotency_key=idempotency_key,
    )


def _get_or_create_sample_project(
    user,
    organization,
    workspace,
    manifest,
    *,
    mark_opened=True,
):
    key = sample_idempotency_key(workspace, manifest)
    now = timezone.now()
    defaults = {
        "organization": organization,
        "workspace": workspace,
        "first_opened_by": user if mark_opened else None,
        "last_opened_by": user if mark_opened else None,
        "manifest_id": manifest["manifest_id"],
        "manifest_version": manifest["manifest_version"],
        "status": OnboardingSampleProject.STATUS_CREATING,
        "idempotency_key": key,
        "last_opened_at": now if mark_opened else None,
        "metadata": {
            "sample_label": manifest["sample_label"],
            "display_name": manifest["display_name"],
            "domain": manifest["domain"],
            "path": "observe",
        },
    }
    try:
        sample_project, _created = (
            OnboardingSampleProject.no_workspace_objects.select_for_update().get_or_create(
                workspace=workspace,
                manifest_id=manifest["manifest_id"],
                manifest_version=manifest["manifest_version"],
                defaults=defaults,
            )
        )
    except IntegrityError:
        sample_project = (
            OnboardingSampleProject.no_workspace_objects.select_for_update().get(
                workspace=workspace,
                manifest_id=manifest["manifest_id"],
                manifest_version=manifest["manifest_version"],
            )
        )
    return sample_project


def _prepare_sample_project_artifacts(sample_project, manifest):
    try:
        artifacts = ensure_observe_sample_artifacts(sample_project, manifest)
        missing = artifacts["missing_artifacts"]
        route_ready = artifacts["route_ready"]
        status = (
            OnboardingSampleProject.STATUS_READY_FOR_OBSERVE
            if route_ready
            else OnboardingSampleProject.STATUS_PARTIALLY_READY
        )
        sample_project.artifact_refs = artifacts["artifact_refs"]
        sample_project.missing_artifacts = missing
        sample_project.health = {
            "route_ready": route_ready,
            "last_validated_at": timezone.now().isoformat(),
            "optional_paths": manifest["optional_paths"],
        }
        sample_project.status = status
        _save_sample_project(
            sample_project,
            update_fields=[
                "artifact_refs",
                "missing_artifacts",
                "health",
                "status",
            ],
        )
    except Exception as exc:
        sample_project.repair_attempts += 1
        sample_project.last_repair_attempt_at = timezone.now()
        sample_project.status = OnboardingSampleProject.STATUS_REPAIR_FAILED
        sample_project.health = {
            "route_ready": False,
            "error": str(exc),
            "last_validated_at": timezone.now().isoformat(),
        }
        _save_sample_project(
            sample_project,
            update_fields=[
                "repair_attempts",
                "last_repair_attempt_at",
                "status",
                "health",
            ],
        )
        logger.exception(
            "Onboarding sample project creation failed",
            sample_project_id=str(sample_project.id),
            workspace_id=str(sample_project.workspace_id),
            error=str(exc),
        )


def ensure_sample_project_ready(
    user,
    organization,
    workspace,
    *,
    is_enabled=True,
    can_create=True,
    manifest_id=None,
    manifest_version=None,
):
    manifest = get_sample_manifest(manifest_id, manifest_version)
    if manifest is None:
        return get_sample_project_state(
            user,
            organization,
            workspace,
            is_enabled=is_enabled,
            can_create=can_create,
            manifest_id=manifest_id,
            manifest_version=manifest_version,
        )
    if not is_enabled or organization is None or workspace is None or not can_create:
        return get_sample_project_state(
            user,
            organization,
            workspace,
            is_enabled=is_enabled,
            can_create=can_create,
            manifest_id=manifest_id,
            manifest_version=manifest_version,
        )

    with transaction.atomic():
        sample_project = _get_or_create_sample_project(
            user,
            organization,
            workspace,
            manifest,
            mark_opened=False,
        )
        if sample_project.hidden_at:
            return _state_from_project(sample_project, manifest, is_enabled=is_enabled)

        validation = validate_sample_artifacts(sample_project, manifest)
        already_ready = (
            validation["route_ready"]
            and sample_project.status
            == OnboardingSampleProject.STATUS_READY_FOR_OBSERVE
        )
        repair_failed = (
            sample_project.status == OnboardingSampleProject.STATUS_REPAIR_FAILED
            and sample_project.repair_attempts > 0
        )
        if not already_ready and not repair_failed:
            sample_project.status = OnboardingSampleProject.STATUS_CREATING
            _save_sample_project(sample_project, update_fields=["status"])
            _prepare_sample_project_artifacts(sample_project, manifest)

    return _state_from_project(sample_project, manifest, is_enabled=is_enabled)


def create_or_get_sample_project(
    user,
    organization,
    workspace,
    *,
    source="onboarding_home",
    reason="manual_open",
    is_enabled=True,
    can_create=True,
    manifest_id=None,
    manifest_version=None,
    email_context=None,
):
    manifest = get_sample_manifest(manifest_id, manifest_version)
    if manifest is None:
        raise ValidationError({"manifest_id": "Unknown sample project manifest."})
    if not is_enabled:
        return _unavailable_state(manifest, "feature_disabled")
    if organization is None or workspace is None:
        return _unavailable_state(manifest, "workspace_missing")
    if not can_create:
        return _unavailable_state(manifest, "missing_permission")

    with transaction.atomic():
        sample_project = _get_or_create_sample_project(
            user,
            organization,
            workspace,
            manifest,
        )
        if sample_project.first_opened_by_id is None:
            sample_project.first_opened_by = user
        sample_project.last_opened_by = user
        sample_project.last_opened_at = timezone.now()
        sample_project.hidden_at = None
        sample_project.status = OnboardingSampleProject.STATUS_CREATING
        _save_sample_project(
            sample_project,
            update_fields=[
                "first_opened_by",
                "last_opened_by",
                "last_opened_at",
                "hidden_at",
                "status",
            ],
        )

        _prepare_sample_project_artifacts(sample_project, manifest)

    state = _state_from_project(sample_project, manifest, is_enabled=is_enabled)
    _record_sample_event(
        sample_project,
        user=user,
        event_name="onboarding_sample_project_opened",
        source=source,
        reason=reason,
        email_context=email_context,
    )
    if state["entry_route"]:
        _record_sample_event(
            sample_project,
            user=user,
            event_name="sample_trace_available",
            source=source,
            reason=reason,
            email_context=email_context,
        )
    return state


def repair_sample_project(user, organization, workspace, sample_project):
    return create_or_get_sample_project(
        user,
        organization,
        workspace,
        source="sample_repair",
        reason="repair",
        manifest_id=sample_project.manifest_id,
        manifest_version=sample_project.manifest_version,
    )


def hide_sample_project(
    user,
    organization,
    workspace,
    *,
    source="onboarding_home",
    reason="user_dismissed",
    is_enabled=True,
):
    manifest = get_sample_manifest()
    if not is_enabled:
        return _unavailable_state(manifest, "feature_disabled")
    if organization is None or workspace is None:
        return _unavailable_state(manifest, "workspace_missing")
    with transaction.atomic():
        sample_project = _get_or_create_sample_project(
            user,
            organization,
            workspace,
            manifest,
        )
        sample_project.last_opened_by = user
        sample_project.hidden_at = timezone.now()
        sample_project.status = OnboardingSampleProject.STATUS_HIDDEN
        sample_project.health = {
            **(sample_project.health or {}),
            "hidden_reason": reason,
            "hidden_source": source,
        }
        _save_sample_project(
            sample_project,
            update_fields=["last_opened_by", "hidden_at", "status", "health"],
        )
    return _state_from_project(sample_project, manifest, is_enabled=is_enabled)


def restore_sample_project(user, organization, workspace):
    manifest = get_sample_manifest()
    sample_project = _get_sample_project(organization, workspace, manifest)
    if not sample_project:
        return get_sample_project_state(
            user,
            organization,
            workspace,
            is_enabled=True,
        )
    sample_project.hidden_at = None
    sample_project.status = OnboardingSampleProject.STATUS_PARTIALLY_READY
    _save_sample_project(sample_project, update_fields=["hidden_at", "status"])
    return _state_from_project(sample_project, manifest)


def resolve_sample_entry_route(sample_project):
    validation = validate_sample_artifacts(sample_project)
    return validation["entry_route"]
