import json
import uuid
from datetime import datetime

import structlog
from django.db import transaction
from requests.exceptions import HTTPError

from accounts.models.organization import Organization
from simulate.models import AgentDefinition
from tfc.temporal import temporal_activity
from tracer.models.observability_provider import ObservabilityProvider, ProviderChoices
from tracer.models.observation_span import ObservationSpan
from tracer.models.project import ProjectSourceChoices
from tracer.models.trace import Trace
from tracer.serializers.observability_provider import ObservabilityProviderSerializer
from tracer.services.clickhouse.span_attribute_lookups import (
    span_id_by_provider_log_id,
)
from tracer.services.observability_providers import ObservabilityService
from tracer.tasks.recordings_rehost import (
    RECORDING_KEYS_BY_PROVIDER,
    rehost_external_recordings,
)
from tracer.utils.bland import normalize_bland_data
from tracer.utils.eleven_labs import normalize_eleven_labs_data
from tracer.utils.otel import ResourceLimitError, get_or_create_project
from tracer.utils.retell import normalize_retell_data
from tracer.utils.twilio_calls import normalize_twilio_data
from tracer.utils.usage_emit import emit_span_ingestion_usage
from tracer.utils.vapi import normalize_vapi_data

logger = structlog.get_logger(__name__)


@temporal_activity(
    max_retries=0,
    time_limit=3600 * 3,
    queue="tasks_s",
)
def fetch_observability_logs(
    start_time: str = None,  # ISO format string
    end_time: str = None,  # ISO format string
):
    """
    Fetches observability logs for all enabled providers.

    Args:
        start_time: ISO format datetime string (e.g., "2025-12-30T23:00:00")
        end_time: ISO format datetime string (e.g., "2025-12-31T10:00:00")
    """
    # Convert ISO strings to datetime objects
    start_dt = datetime.fromisoformat(start_time) if start_time else None
    end_dt = datetime.fromisoformat(end_time) if end_time else datetime.now()

    enabled_providers = (
        ObservabilityProvider.objects.filter(enabled=True)
        .values_list("id", flat=True)
        .iterator(chunk_size=750)
    )

    success_count = 0
    failure_count = 0

    for provider_id in enabled_providers:
        try:
            result = fetch_logs_for_provider(
                provider_id=provider_id, start_time=start_dt, end_time=end_dt
            )
            if result is not None:
                success_count += 1
            else:
                failure_count += 1
        except Exception as e:
            failure_count += 1
            logger.exception(
                "Failed to fetch logs for provider, continuing with next provider",
                provider_id=str(provider_id),
                error=str(e),
            )
            continue

    logger.info(
        "Completed fetching observability logs",
        success_count=success_count,
        failure_count=failure_count,
    )


def fetch_logs_for_provider(
    provider_id: str,
    start_time: datetime = None,
    end_time: datetime = None,
):
    """
    Fetches logs for a specific provider.

    Args:
        provider_id: The ID of the provider to fetch logs for
        start_time: Optional start time for the log fetch
        end_time: Optional end time for the log fetch

    Returns:
        List of logs if successful, empty list if skipped, None if error
    """
    try:
        now = datetime.now()

        # Get the provider
        try:
            provider = ObservabilityProvider.objects.get(id=provider_id)
        except ObservabilityProvider.DoesNotExist:
            logger.warning(
                "Provider not found, skipping",
                provider_id=str(provider_id),
            )
            return None

        last_fetched_at = start_time if start_time else provider.last_fetched_at
        end_time_to_use = end_time if end_time else now

        if provider.provider != ProviderChoices.RETELL or not last_fetched_at:
            logger.info(
                "Fetching logs for provider",
                provider_id=str(provider_id),
                provider_type=provider.provider,
                start_time=str(last_fetched_at) if last_fetched_at else None,
                end_time=str(end_time_to_use),
            )

            try:
                logs = ObservabilityService.get_call_logs(
                    provider=provider,
                    start_time=last_fetched_at,
                    end_time=end_time_to_use,
                )
            except HTTPError as e:
                if e.response is not None and e.response.status_code in (401, 403):
                    logger.error(
                        "authentication_failed_for_provider",
                        provider_id=str(provider_id),
                        provider_type=provider.provider,
                        status_code=e.response.status_code,
                    )
                    return None
                logger.exception(
                    "Failed to fetch logs from provider API",
                    provider_id=str(provider_id),
                    provider_type=provider.provider,
                    error=str(e),
                )
                return None
            except Exception as e:
                logger.exception(
                    "Failed to fetch logs from provider API",
                    provider_id=str(provider_id),
                    provider_type=provider.provider,
                    error=str(e),
                )
                return None

            # Only update last_fetched_at if we successfully got logs
            try:
                _update_last_fetched_at(provider, end_time_to_use)
            except Exception as e:
                logger.warning(
                    "Failed to update last_fetched_at for provider",
                    provider_id=str(provider_id),
                    error=str(e),
                )

            # Process and store logs
            try:
                process_and_store_logs(logs, provider)
            except Exception as e:
                logger.exception(
                    "Failed to process and store logs",
                    provider_id=str(provider_id),
                    provider_type=provider.provider,
                    logs_count=len(logs) if logs else 0,
                    error=str(e),
                )
                # Still return logs since we fetched them successfully
                return logs

            logger.info(
                "Successfully fetched and stored logs for provider",
                provider_id=str(provider_id),
                provider_type=provider.provider,
                logs_count=len(logs) if logs else 0,
            )

            return logs

        return []

    except Exception as e:
        logger.exception(
            "Unexpected error fetching logs for provider",
            provider_id=str(provider_id),
            error=str(e),
        )
        return None


def _update_last_fetched_at(provider: ObservabilityProvider, now: datetime):
    provider.last_fetched_at = now
    provider.save(update_fields=["last_fetched_at"])


def _create_observation_span(project, provider, normalized_data, metadata):
    """Creates a new Trace and ObservationSpan.

    CH25-TODO: KEEP-PG. This is a write path — ObservationSpan.objects
    .create() is the dual-write source of truth (D-027). CH receives
    the row via PeerDB CDC; there is no direct CH write API in the
    reader by design.
    """
    trace = Trace.objects.create(
        id=uuid.uuid4(),
        project=project,
        metadata=metadata,
    )

    attributes = normalized_data.get("span_attributes", {})

    return ObservationSpan.objects.create(
        id=uuid.uuid4(),
        project=project,
        trace=trace,
        name=f"{provider.provider.capitalize()} Call Log",
        observation_type="conversation",
        start_time=normalized_data.get("start_time"),
        end_time=normalized_data.get("end_time"),
        input=normalized_data.get("input", {}),
        output=normalized_data.get("output", {}),
        metadata=metadata,
        provider=provider.provider,
        cost=normalized_data.get("cost"),
        status=normalized_data.get("status"),
        span_attributes=attributes,
        prompt_tokens=normalized_data.get("prompt_tokens"),
        completion_tokens=normalized_data.get("completion_tokens"),
        total_tokens=normalized_data.get("total_tokens"),
        latency_ms=normalized_data.get("latency_ms"),
    )


_PROVIDER_SPAN_NS = uuid.UUID("4d61d4e2-7b3c-4a1e-9f02-2c6a5b8e1d70")


def _provider_collector_span_id(provider: str, provider_log_id: str) -> str:
    """Deterministic 64-bit (16 hex) span id for a pulled call, stable across
    re-polls so the CH ``spans`` ReplacingMergeTree upserts in place."""
    return uuid.uuid5(_PROVIDER_SPAN_NS, f"{provider}:{provider_log_id}").hex[:16]


def _to_epoch_ns(value) -> int | None:
    """Coerce a datetime / epoch-seconds / epoch-ns value to epoch nanoseconds."""
    if value is None:
        return None
    if hasattr(value, "timestamp"):
        return int(value.timestamp() * 1e9)
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    # Heuristic: < 1e12 is seconds, else already ns/ms — normalize to ns.
    if v < 1e12:
        return int(v * 1e9)
    if v < 1e15:  # milliseconds
        return int(v * 1e6)
    return int(v)


def _export_provider_call_to_collector(span, provider: str, provider_log_id: str):
    """Dual-write a pulled call's CONVERSATION span to the fi-collector so it
    reaches CH ``spans`` (the table the voice-call/trace UI reads). PG remains
    the source of truth for evals/annotations; this best-effort export is the
    CH-only-reads replacement for the dropped PeerDB CDC. Never raises.
    """
    try:
        project = span.project
        organization_id = str(getattr(project, "organization_id", "") or "")
        if not organization_id:
            return
        # OTLP can't carry the nested provider raw_log; drop it so the read
        # path's empty-raw_log branch derives status/duration/recording from the
        # call.* scalar attrs the provider processors already set.
        attrs = {
            k: v for k, v in (span.span_attributes or {}).items() if k != "raw_log"
        }
        attrs["gen_ai.span.kind"] = "CONVERSATION"
        attrs["gen_ai.system"] = provider
        if span.input not in (None, "", [], {}):
            attrs["input.value"] = span.input
        if span.output not in (None, "", [], {}):
            attrs["output.value"] = span.output
        # The detail drawer builds the transcript from raw_log, which we drop —
        # compute the normalized transcript now and stash it for the
        # CH-only-reads detail path (trace.py reads fi.conversation.transcript).
        raw_log = (span.span_attributes or {}).get("raw_log") or {}
        try:
            processed = ObservabilityService.process_raw_logs(
                raw_log, provider, span_attributes=span.span_attributes or {}
            )
            if processed.get("transcript"):
                attrs["fi.conversation.transcript"] = processed["transcript"]
        except Exception:
            logger.warning(
                "provider_transcript_compute_failed", provider=provider, exc_info=True
            )
        start_ns = _to_epoch_ns(span.start_time)
        span_dict = {
            "trace_id": span.trace.id.hex,
            "span_id": _provider_collector_span_id(provider, provider_log_id),
            "parent_span_id": None,
            "parent_id": None,
            "name": span.name,
            "attributes": attrs,
        }
        if start_ns is not None:
            span_dict["start_time"] = start_ns
        end_ns = _to_epoch_ns(span.end_time)
        if end_ns is not None:
            span_dict["end_time"] = end_ns
        from tracer.services.collector_ingest import emit_spans_to_collector

        emit_spans_to_collector(
            [span_dict],
            project_name=project.name,
            project_type=project.trace_type,
            organization_id=organization_id,
            workspace_id=str(project.workspace_id) if project.workspace_id else None,
        )
        # The collector writes CH `spans` (and curated end_users/trace_sessions)
        # but NOT CH `traces`. trace_dict — which resolves a span/eval back to its
        # project (e.g. the voice-call-list eval column does
        # `dictGet('trace_dict','project_id', trace_id)`) — is fed only by the
        # app-side trace mirror. The app's own SDK ingestion paths already mirror
        # (create_otel_span / trace_ingestion); collector-routed provider pulls
        # must too, else evals (and any trace_dict-keyed read) never resolve to a
        # project for provider calls. Post-commit + best-effort, self-gated on
        # dual_write_enabled() (no-op when CDC is on / PG trace absent).
        from django.db import transaction

        from tracer.services.clickhouse.v2.trace_writer import (
            mirror_traces_to_clickhouse,
        )

        transaction.on_commit(
            lambda tid=str(span.trace_id): mirror_traces_to_clickhouse([tid])
        )
    except Exception:
        logger.exception(
            "provider_collector_export_failed",
            provider=provider,
            provider_log_id=provider_log_id,
        )


def _update_observation_span(existing_span, normalized_data):
    """Updates an existing ObservationSpan and its associated Trace.

    CH25-TODO: KEEP-PG. This is a write path — ``existing_span.save()``
    below is the dual-write source-of-truth update (D-027). CH receives
    the updated row via PeerDB CDC. Same rationale as
    ``_create_observation_span`` above; the two helpers form the
    upsert pair driven by ``process_and_store_logs``.
    """
    attributes = normalized_data.get("span_attributes", {})

    # Preserve recording URLs we've already rehosted to S3 — the provider
    # response only contains its own (expiring) URLs, so a wholesale
    # overwrite would otherwise drop the durable S3 links the rehost task
    # wrote on a previous poll.
    for k, v in (existing_span.span_attributes or {}).items():
        if isinstance(v, str) and ("amazonaws.com" in v or "minio" in v):
            attributes[k] = v

    existing_span.start_time = normalized_data.get("start_time")
    existing_span.end_time = normalized_data.get("end_time")
    existing_span.input = normalized_data.get("input", {})
    existing_span.output = normalized_data.get("output", {})
    existing_span.cost = normalized_data.get("cost")
    existing_span.status = normalized_data.get("status")
    existing_span.span_attributes = attributes
    existing_span.prompt_tokens = normalized_data.get("prompt_tokens")
    existing_span.completion_tokens = normalized_data.get("completion_tokens")
    existing_span.total_tokens = normalized_data.get("total_tokens")
    existing_span.latency_ms = normalized_data.get("latency_ms")

    existing_span.save(
        update_fields=[
            "start_time",
            "end_time",
            "input",
            "output",
            "cost",
            "status",
            "span_attributes",
            "prompt_tokens",
            "completion_tokens",
            "total_tokens",
            "latency_ms",
        ]
    )
    return existing_span


def process_and_store_logs(logs: list, provider: ObservabilityProvider):
    """
    Processes raw log data and stores it as ObservationSpan objects.
    """
    project = provider.project

    normalization_functions = {
        "vapi": normalize_vapi_data,
        "retell": normalize_retell_data,
        "eleven_labs": normalize_eleven_labs_data,
        "bland": normalize_bland_data,
        "twilio": normalize_twilio_data,
    }

    if provider.provider not in normalization_functions:
        return

    normalize_fn = normalization_functions[provider.provider]

    created_count = 0
    created_payload_bytes = 0

    for log in logs:
        provider_log_id = None
        try:
            normalized_data = normalize_fn(log)
            provider_log_id = normalized_data.get("id")
        except Exception:
            logger.exception(f"Failed to normalize log for {provider.provider}")

        if not provider_log_id:
            logger.error(f"No provider log id found for {provider.provider}")
            continue

        metadata = {
            "provider": provider.provider,
            "provider_log_id": provider_log_id,
        }

        try:
            with transaction.atomic():
                # The PG path used to OR three JSONB containment checks
                # (``metadata__provider_log_id``, ``span_attributes__raw_log__id``,
                # ``eval_attributes__raw_log__id``). The two GIN indexes that
                # made the latter two cheap were dropped (migration 0074).
                # Resolve the candidate span_id via ClickHouse and then fetch
                # the row from PG by primary key.
                existing_span_id = span_id_by_provider_log_id(
                    project_id=str(project.id),
                    provider=provider.provider,
                    provider_log_id=provider_log_id,
                )
                existing_span = None
                if existing_span_id:
                    # CH25-TODO: KEEP-PG. Read-by-PK after CH found the
                    # span_id — we need the live PG row to drive the
                    # subsequent dual-write update inside this atomic
                    # block (see _update_observation_span below). The
                    # CH row alone can't be mutated by the writer.
                    existing_span = ObservationSpan.objects.filter(
                        id=existing_span_id,
                        project=project,
                        provider=provider.provider,
                    ).first()

                # Fallback to the small/cheap PG-side metadata GIN lookup if
                # CH is unavailable or hasn't indexed this span yet.
                if existing_span is None:
                    # CH25-TODO: KEEP-PG. Documented fallback for when CH
                    # is unavailable or hasn't ingested the row yet. The
                    # PG ``metadata`` GIN index makes this cheap; the
                    # equivalent CH path would lose the freshness
                    # guarantee that the PG primary store provides for
                    # the upsert decision below. Pairs with the
                    # span_id_by_provider_log_id CH lookup above.
                    existing_span = (
                        ObservationSpan.objects.filter(
                            metadata__provider_log_id=provider_log_id,
                            project=project,
                            provider=provider.provider,
                        )
                        .order_by("-updated_at")
                        .first()
                    )

                if existing_span:
                    span = _update_observation_span(existing_span, normalized_data)
                    was_created = False
                else:
                    span = _create_observation_span(
                        project, provider, normalized_data, metadata
                    )
                    was_created = True

                _maybe_enqueue_recording_rehost(provider, span)
        except Exception as e:
            logger.exception(
                f"Error updating or creating observation span for {provider.provider}: {e}"
            )
            continue

        # Dual-write the pulled call to the fi-collector AFTER the PG commit so
        # it reaches CH `spans` (the read store) without the dropped PeerDB CDC.
        _export_provider_call_to_collector(span, provider.provider, provider_log_id)

        if was_created:
            created_count += 1
            for piece in (
                normalized_data.get("input"),
                normalized_data.get("output"),
                normalized_data.get("span_attributes"),
                metadata,
            ):
                if piece is None:
                    continue
                try:
                    created_payload_bytes += len(json.dumps(piece, default=str))
                except (TypeError, ValueError):
                    continue

    if created_count:
        emit_span_ingestion_usage(
            organization_id=project.organization_id,
            num_traces=0,
            num_spans=created_count,
            payload_bytes=created_payload_bytes,
            source="voice_observability",
        )


def _maybe_enqueue_recording_rehost(
    provider: ObservabilityProvider, span: ObservationSpan
) -> None:
    """Enqueue S3 rehost for recording URLs on this span, if applicable.

    Scheduled via transaction.on_commit so the worker won't race the upsert.
    Opt out per provider via metadata["rehost_recordings"] = False.
    """
    if (provider.metadata or {}).get("rehost_recordings", True) is False:
        return

    keys = RECORDING_KEYS_BY_PROVIDER.get(provider.provider) or []
    attrs = span.span_attributes or {}
    if not any(attrs.get(key) for key, _ in keys):
        return

    span_id = str(span.id)
    transaction.on_commit(lambda: rehost_external_recordings.delay(span_id=span_id))


def create_observability_provider(
    enabled: bool,
    user_id: str,
    organization: Organization,
    workspace: str,
    project_name: str,
    provider: str,
):
    try:
        if not enabled:
            return None

        from accounts.models.workspace import Workspace as WorkspaceModel

        # Resolve workspace to a model instance — callers may pass either
        # a string UUID (MCP tools) or a Workspace instance (REST views).
        if workspace and isinstance(workspace, str):
            workspace_instance = WorkspaceModel.objects.get(id=workspace)
            workspace_id = workspace
        elif workspace:
            workspace_instance = workspace
            workspace_id = str(workspace.id)
        else:
            workspace_instance = None
            workspace_id = None

        project = get_or_create_project(
            project_name=project_name,
            organization_id=organization.id,
            project_type="observe",
            user_id=user_id,
            workspace_id=workspace_id,
            source=ProjectSourceChoices.SIMULATOR.value,
        )

        serializer = ObservabilityProviderSerializer(
            data={
                "project": project.id if project else None,
                "provider": provider,
                "enabled": True,
                "organization": organization.id,
                "workspace": workspace_id,
            }
        )
        if not serializer.is_valid():
            return serializer.errors

        obj = serializer.save(
            project=project,
            organization=organization,
            workspace=workspace_instance,
        )
        return obj
    except ResourceLimitError:
        raise
    except Exception as e:
        return {"error": "Invalid data", "details": e}


@temporal_activity(
    max_retries=2,
    time_limit=600,
    queue="default",
)
def normalize_and_store_logs(body, agent_definition_id) -> None:
    try:
        agent_definition = AgentDefinition.objects.get(id=agent_definition_id)
        logger.info(f"normalize_and_store_logs started {agent_definition.assistant_id}")
        provider = agent_definition.observability_provider
        if not provider:
            logger.warning("normalize_and_store_logs: No provider")
            return

        call_log = body.get("call")
        process_and_store_logs([call_log], provider)

        logger.info("normalize_and_store_logs completed")

    except Exception as e:
        logger.error(f"Error storing logs:{e}")
