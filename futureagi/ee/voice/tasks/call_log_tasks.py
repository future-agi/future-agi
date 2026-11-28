from __future__ import annotations

import traceback
from collections import Counter
from datetime import UTC, datetime

import structlog
from django.db import close_old_connections, connection, transaction
from django.utils import timezone

from ee.voice.services.voice_service_manager import VoiceServiceManager
from simulate.models import (
    CallExecution,
    CallLogEntry,
    ProviderCredentials,
    TestExecution,
)
from tfc.temporal.drop_in import temporal_activity

logger = structlog.get_logger(__name__)
CHUNK_SIZE = 100


def _close_old_connections_if_safe() -> None:
    if not connection.in_atomic_block:
        close_old_connections()


def _coerce_datetime(payload: dict) -> datetime | None:
    """
    Convert Vapi log timestamp fields to timezone-aware datetime.
    Prefers `time` (milliseconds since epoch), falls back to `timestamp`
    (nanoseconds since epoch). Returns None if conversion fails.
    """
    time_value = payload.get("time")
    if isinstance(time_value, (int, float)):
        try:
            return datetime.fromtimestamp(time_value / 1000, tz=UTC)
        except (OverflowError, OSError, ValueError):
            logger.warning("Failed to convert log time to datetime")

    timestamp_value = payload.get("timestamp")
    if isinstance(timestamp_value, (int, float)):
        try:
            return datetime.fromtimestamp(timestamp_value / 1_000_000_000, tz=UTC)
        except (OverflowError, OSError, ValueError):
            logger.warning("Failed to convert log timestamp to datetime")

    iso_value = payload.get("ts")
    if isinstance(iso_value, str):
        try:
            return datetime.fromisoformat(iso_value.replace("Z", "+00:00"))
        except ValueError:
            logger.warning("Failed to parse ISO timestamp for log entry")

    return None


def _vapi_log_key(execution: TestExecution) -> str | None:
    """Read a key for this execution's pin, never the agent's latest version."""
    version = execution.agent_version
    if version is None or version.deleted:
        return None
    agent = version.agent_definition
    if agent.deleted or execution.agent_definition_id not in (None, agent.pk):
        return None
    snapshot = version.configuration_snapshot
    vapi = ProviderCredentials.ProviderType.VAPI
    if not isinstance(snapshot, dict):
        return None
    if (snapshot.get("provider") or agent.provider) != vapi:
        return None

    row: ProviderCredentials | None = getattr(version, "credentials", None)
    if row is None:
        row = getattr(agent, "credentials_legacy", None)
    if row is not None:
        if (
            row.deleted
            or row.provider_type != vapi
            or row.agent_definition_id not in (None, agent.pk)
        ):
            return None
        # A migrated row can have both owner links. Its storage label alone
        # does not distinguish Vapi from other historical voice providers.
        owner = row.agent_version
        if owner is None:
            if (
                snapshot.get("provider") != vapi
                or not row.assistant_id.strip()
                or row.assistant_id != snapshot.get("assistant_id")
            ):
                return None
        else:
            owner_snapshot = owner.configuration_snapshot
            if (
                owner.deleted
                or owner.agent_definition_id != agent.pk
                or not isinstance(owner_snapshot, dict)
                or owner_snapshot.get("provider") != vapi
            ):
                return None
        if row.api_key:
            try:
                return row.get_api_key().strip() or None
            except (ValueError, AttributeError):
                logger.warning("call_log_credential_unreadable")
                return None

    # Only a missing or empty, compatible row permits the definition fallback.
    if agent.provider == vapi:
        return (agent.api_key or "").strip() or None
    return None


def _ingest_call_logs(
    call_execution_id: str,
    log_url: str,
    *,
    source: str = CallLogEntry.LogSource.AGENT,
    verify_ssl: bool = False,
    call_id: str | None = None,
    api_key: str | None = None,
) -> bool:
    """Download call logs, persist entries, and derive latency metrics."""
    try:
        _close_old_connections_if_safe()
        logger.info("Ingesting call logs")
        try:
            calls = CallExecution.objects.all()
            if call_id and api_key is None:
                calls = calls.select_related(
                    "test_execution__agent_version__credentials",
                    "test_execution__agent_version__agent_definition__credentials_legacy__agent_version",
                )
            call_execution = calls.get(id=call_execution_id)
        except CallExecution.DoesNotExist:
            logger.warning("Skipping call log ingestion: call execution not found")
            return False

        if source not in CallLogEntry.LogSource.values:
            source = CallLogEntry.LogSource.AGENT

        # Already-queued jobs can still carry an explicit key.
        if call_id and api_key is None:
            api_key = _vapi_log_key(call_execution.test_execution)

        voice_service_manager = VoiceServiceManager()
        entries_iter = voice_service_manager.iter_call_logs(
            url=log_url,
            verify_ssl=verify_ssl,
            call_id=call_id,
            api_key=api_key,
        )

        now = timezone.now()
        total_entries = 0
        level_counts: Counter[str] = Counter()
        category_counts: Counter[str] = Counter()
        latest_logged_at: datetime | None = None

        def update_latest(dt_value: datetime | None) -> None:
            nonlocal latest_logged_at
            if dt_value is None:
                return
            if latest_logged_at is None or dt_value > latest_logged_at:
                latest_logged_at = dt_value

        with transaction.atomic():
            buffer: list[CallLogEntry] = []

            for payload in entries_iter:
                if not isinstance(payload, dict):
                    payload = {"raw_line": payload}

                total_entries += 1

                logged_at = _coerce_datetime(payload) or now
                update_latest(logged_at)

                level_value = payload.get("level")
                try:
                    level = int(level_value) if level_value is not None else 0
                except (TypeError, ValueError):
                    level = 0
                level_counts[str(level)] += 1

                severity_text = payload.get("severityText") or ""
                body = payload.get("body") or ""

                attributes = payload.get("attributes") or {}
                if not isinstance(attributes, dict):
                    attributes = {}

                category = attributes.get("category") or ""
                category_counts[str(category)] += 1

                entry = CallLogEntry(
                    call_execution=call_execution,
                    source=source,
                    logged_at=logged_at,
                    level=level,
                    severity_text=str(severity_text)[:32],
                    category=str(category)[:128],
                    body=str(body)[:1024],
                    attributes=attributes,
                    payload=payload,
                )
                buffer.append(entry)

                if len(buffer) >= CHUNK_SIZE:
                    CallLogEntry.objects.bulk_create(buffer, batch_size=CHUNK_SIZE)
                    buffer.clear()

            if buffer:
                CallLogEntry.objects.bulk_create(buffer, batch_size=CHUNK_SIZE)

            summary = {
                "total_entries": total_entries,
                "level_counts": dict(level_counts),
                "category_counts": dict(category_counts),
                "last_logged_at": (
                    latest_logged_at.isoformat() if latest_logged_at else None
                ),
            }

            is_customer = source == CallLogEntry.LogSource.CUSTOMER
            update_fields: list[str] = []

            if is_customer:
                call_execution.customer_logs_summary = summary
                update_fields.extend(["customer_logs_summary"])
            else:
                call_execution.logs_summary = summary
                update_fields.extend(["logs_summary"])

            call_execution.logs_ingested_at = timezone.now()
            update_fields.append("logs_ingested_at")

            if update_fields:
                call_execution.save(update_fields=update_fields)

        logger.info("Call log ingestion completed")
        return True

    except Exception as exc:
        print(exc)  # noqa: BLE001
        traceback.print_exc()
        logger.error("Failed to ingest call logs")
        raise
    finally:
        _close_old_connections_if_safe()


@temporal_activity(
    time_limit=900,
    max_retries=3,
    retry_delay=120,
    queue="tasks_l",
)
def ingest_call_logs_task(
    call_execution_id: str,
    log_url: str,
    verify_ssl: bool = False,
    source: str = CallLogEntry.LogSource.AGENT,
    call_id: str | None = None,
    api_key: str | None = None,
):
    """Temporal-activity wrapper around _ingest_call_logs."""
    return _ingest_call_logs(
        call_execution_id=call_execution_id,
        log_url=log_url,
        source=source,
        verify_ssl=verify_ssl,
        call_id=call_id,
        api_key=api_key,
    )
