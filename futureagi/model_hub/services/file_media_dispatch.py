"""Bound the durable file-import handoff without changing table publication.

Metadata lives alongside the existing handoff in Dataset.dataset_config. Scheduling
is outside database transactions; delivery remains at least once. A status read
also expires abandoned pending work, so exhausted workflow retries are visible.
"""

from datetime import timedelta

from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from model_hub.models.develop_dataset import Dataset

# Match the parent activity's default three total attempts. The age bound also
# covers a lost worker, abandoned workflow, or pending data from before this fix.
MAX_MEDIA_DISPATCH_ATTEMPTS = 3
MAX_MEDIA_DISPATCH_AGE = timedelta(hours=24)
PREFIX = "file_media_dispatch_"
MEDIA_FAILURE_MESSAGE = "Media processing failed"


def _locked(dataset_id, organization_id):
    return Dataset.objects.select_for_update(of=("self",)).get(
        id=dataset_id, organization_id=organization_id
    )


def _save(dataset, config):
    dataset.dataset_config = config
    dataset.save(update_fields=["dataset_config"])


def _expire(dataset, config):
    """Caller holds the Dataset lock. Malformed age metadata fails closed."""
    if config.get(PREFIX + "status") != "pending":
        return
    started = (
        config.get(PREFIX + "first_failed_at")
        or config.get(PREFIX + "last_retriggered_at")
        or config.get("file_processing_completed_at")
    )
    try:
        since = parse_datetime(started) if started else dataset.created_at
        if since is not None and timezone.is_naive(since):
            since = timezone.make_aware(since)
    except (TypeError, ValueError):
        since = None
    expired = since is None or timezone.now() - since >= MAX_MEDIA_DISPATCH_AGE
    exhausted = config.get(PREFIX + "attempts", 0) >= MAX_MEDIA_DISPATCH_ATTEMPTS
    if expired or exhausted:
        config[PREFIX + "status"] = "failed"
        config[PREFIX + "failed_at"] = timezone.now().isoformat()
        _save(dataset, config)


def refresh_media_dispatch(dataset):
    """Called only AFTER tenant/workspace admission; never schedules on a GET."""
    if (dataset.dataset_config or {}).get(PREFIX + "status") != "pending":
        return dataset.dataset_config or {}
    with transaction.atomic():
        current = _locked(dataset.id, dataset.organization_id)
        config = dict(current.dataset_config or {})
        _expire(current, config)
    dataset.dataset_config = config
    return config


def dispatch_import_media(dataset_id, organization_id, schedule):
    with transaction.atomic():
        dataset = _locked(dataset_id, organization_id)
        config = dict(dataset.dataset_config or {})
        _expire(dataset, config)
        if config.get(PREFIX + "status") != "pending":
            return
        generation = config.get(PREFIX + "retrigger_count", 0)

    try:
        schedule(str(dataset_id))
    except Exception as error:
        with transaction.atomic():
            dataset = _locked(dataset_id, organization_id)
            config = dict(dataset.dataset_config or {})
            # Do not charge a late result from a previous generation against an
            # operator's new budget. A concurrent success must not be undone.
            config[PREFIX + "total_failures"] = (
                config.get(PREFIX + "total_failures", 0) + 1
            )
            config[PREFIX + "last_error_class"] = type(error).__name__
            if config.get(PREFIX + "retrigger_count", 0) == generation:
                config[PREFIX + "attempts"] = config.get(PREFIX + "attempts", 0) + 1
                config.setdefault(
                    PREFIX + "first_failed_at", timezone.now().isoformat()
                )
                _expire(dataset, config)
            _save(dataset, config)
        # The parent activity needs the failure for its normal retry policy.
        # Never persist the exception's values, message, repr, or traceback.
        raise
    else:
        with transaction.atomic():
            dataset = _locked(dataset_id, organization_id)
            config = dict(dataset.dataset_config or {})
            if (
                config.get(PREFIX + "retrigger_count", 0) == generation
                and config.get(PREFIX + "status") == "pending"
            ):
                config[PREFIX + "status"] = "dispatched"
                _save(dataset, config)


def retry_media_dispatch(dataset_id, organization_id, schedule):
    """Operator-only command entry point; both ids are mandatory and matched.

    A repeated command on pending/dispatched work is refused, not redispatched.
    Keep last error + lifetime failure count, and record each explicit redrive.
    """
    with transaction.atomic():
        dataset = _locked(dataset_id, organization_id)
        config = dict(dataset.dataset_config or {})
        _expire(dataset, config)
        if (
            config.get("file_processing_status") != "completed"
            or config.get(PREFIX + "status") != "failed"
        ):
            raise ValueError("Only failed media dispatch can be retried")
        config.update(
            {
                PREFIX + "status": "pending",
                PREFIX + "attempts": 0,
                PREFIX + "retrigger_count": config.get(PREFIX + "retrigger_count", 0)
                + 1,
                PREFIX + "last_retriggered_at": timezone.now().isoformat(),
            }
        )
        config.pop(PREFIX + "first_failed_at", None)
        config.pop(PREFIX + "failed_at", None)
        _save(dataset, config)
    dispatch_import_media(dataset_id, organization_id, schedule)
