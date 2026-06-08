from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from accounts.models import (
    OnboardingLifecycleEvaluationLog,
    OnboardingLifecycleSendAllowlist,
    OnboardingLifecycleSendLog,
)
from accounts.services.onboarding.lifecycle_digest_review import (
    DIGEST_REVIEW_CAMPAIGNS,
    safe_digest_preview_from_metadata,
)
from accounts.services.onboarding.lifecycle_sender import send_environment

DIGEST_PROMOTION_SOURCE_EVALUATION = "evaluation_log"
DIGEST_PROMOTION_SOURCE_SEND = "send_log"
DIGEST_PROMOTION_SOURCE_TYPES = (
    DIGEST_PROMOTION_SOURCE_EVALUATION,
    DIGEST_PROMOTION_SOURCE_SEND,
)
DIGEST_PROMOTION_SCOPE_TYPES = (
    OnboardingLifecycleSendAllowlist.SCOPE_USER,
    OnboardingLifecycleSendAllowlist.SCOPE_WORKSPACE,
)
MAX_DIGEST_PROMOTION_SOURCES = 100
DEFAULT_DIGEST_PROMOTION_REASON = "Reviewed daily-quality digest preview"


def _source_key(source):
    return (source["source_type"], str(source["source_id"]))


def _preview_for_log(log):
    workspace_id = log.workspace_id
    if isinstance(log, OnboardingLifecycleSendLog) and not workspace_id:
        workspace_id = log.evaluation_log.workspace_id
    return safe_digest_preview_from_metadata(
        (log.metadata or {}).get("digest_preview"),
        workspace_id=workspace_id,
    )


def _fetch_source_logs(*, organization, workspace, sources):
    evaluation_ids = [
        source["source_id"]
        for source in sources
        if source["source_type"] == DIGEST_PROMOTION_SOURCE_EVALUATION
    ]
    send_ids = [
        source["source_id"]
        for source in sources
        if source["source_type"] == DIGEST_PROMOTION_SOURCE_SEND
    ]
    evaluation_logs = {
        str(log.id): log
        for log in OnboardingLifecycleEvaluationLog.no_workspace_objects.filter(
            id__in=evaluation_ids,
            organization=organization,
            workspace=workspace,
        ).select_related("user", "workspace")
    }
    send_logs = {
        str(log.id): log
        for log in OnboardingLifecycleSendLog.no_workspace_objects.filter(
            id__in=send_ids,
            organization=organization,
        ).select_related("evaluation_log", "user", "workspace")
        if (log.workspace_id or log.evaluation_log.workspace_id) == workspace.id
    }
    return {
        DIGEST_PROMOTION_SOURCE_EVALUATION: evaluation_logs,
        DIGEST_PROMOTION_SOURCE_SEND: send_logs,
    }


def _allowlist_target(log, scope_type):
    workspace_id = log.workspace_id
    if isinstance(log, OnboardingLifecycleSendLog) and not workspace_id:
        workspace_id = log.evaluation_log.workspace_id
    if scope_type == OnboardingLifecycleSendAllowlist.SCOPE_WORKSPACE:
        return {
            "scope_type": scope_type,
            "scope_value": str(workspace_id),
            "user_id": str(log.user_id),
            "workspace_id": str(workspace_id),
        }
    return {
        "scope_type": OnboardingLifecycleSendAllowlist.SCOPE_USER,
        "scope_value": str(log.user_id),
        "user_id": str(log.user_id),
        "workspace_id": str(workspace_id),
    }


def _reason(reason, source):
    base = (reason or DEFAULT_DIGEST_PROMOTION_REASON).strip()
    if not base:
        base = DEFAULT_DIGEST_PROMOTION_REASON
    return f"{base}; source={source['source_type']}:{source['source_id']}"[:255]


def _allowlist_lookup(*, target, campaign_group, environment):
    return OnboardingLifecycleSendAllowlist.no_workspace_objects.filter(
        scope_type=target["scope_type"],
        scope_value=target["scope_value"],
        campaign_group=campaign_group,
        environment=environment,
    ).first()


def promote_digest_preview_sources(
    *,
    organization,
    workspace,
    actor,
    sources,
    scope_type=OnboardingLifecycleSendAllowlist.SCOPE_USER,
    dry_run=False,
    reason="",
    now=None,
):
    now = now or timezone.now()
    environment = send_environment()
    source_logs = _fetch_source_logs(
        organization=organization,
        workspace=workspace,
        sources=sources,
    )
    entries = []
    skipped = []
    seen_sources = set()
    seen_targets = set()
    created_count = 0
    updated_count = 0

    for source in sources[:MAX_DIGEST_PROMOTION_SOURCES]:
        source = {
            "source_type": source["source_type"],
            "source_id": str(source["source_id"]),
        }
        key = _source_key(source)
        if key in seen_sources:
            skipped.append({**source, "reason": "duplicate_source"})
            continue
        seen_sources.add(key)

        log = source_logs[source["source_type"]].get(source["source_id"])
        if not log:
            skipped.append({**source, "reason": "not_found"})
            continue
        if log.campaign_key not in DIGEST_REVIEW_CAMPAIGNS:
            skipped.append({**source, "reason": "unsupported_campaign"})
            continue
        if not _preview_for_log(log):
            skipped.append({**source, "reason": "missing_digest_preview"})
            continue

        target = _allowlist_target(log, scope_type)
        target_key = (
            target["scope_type"],
            target["scope_value"],
            log.campaign_group,
            environment,
        )
        if target_key in seen_targets:
            skipped.append({**source, "reason": "duplicate_target"})
            continue
        seen_targets.add(target_key)

        existing = _allowlist_lookup(
            target=target,
            campaign_group=log.campaign_group,
            environment=environment,
        )
        operation = "would_update" if existing else "would_create"
        allowlist_id = str(existing.id) if existing else None
        if not dry_run:
            with transaction.atomic():
                allowlist, created = (
                    OnboardingLifecycleSendAllowlist.no_workspace_objects.update_or_create(
                        scope_type=target["scope_type"],
                        scope_value=target["scope_value"],
                        campaign_group=log.campaign_group,
                        environment=environment,
                        defaults={
                            "enabled": True,
                            "reason": _reason(reason, source),
                            "created_by": actor,
                        },
                    )
                )
            allowlist_id = str(allowlist.id)
            operation = "created" if created else "updated"
            if created:
                created_count += 1
            else:
                updated_count += 1

        entries.append(
            {
                **source,
                "allowlist_id": allowlist_id,
                "operation": operation,
                "scope_type": target["scope_type"],
                "scope_value": target["scope_value"],
                "campaign_group": log.campaign_group,
                "user_id": target["user_id"],
                "workspace_id": target["workspace_id"],
            }
        )

    return {
        "generated_at": now,
        "environment": environment,
        "campaign_key": ",".join(DIGEST_REVIEW_CAMPAIGNS),
        "scope_type": scope_type,
        "dry_run": bool(dry_run),
        "promoted_count": len(entries),
        "skipped_count": len(skipped),
        "created_count": created_count,
        "updated_count": updated_count,
        "entries": entries,
        "skipped": skipped,
    }
