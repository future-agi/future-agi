import json
import uuid
from datetime import datetime

import structlog
from requests.exceptions import HTTPError

from accounts.models.organization import Organization
from simulate.models import AgentDefinition
from tfc.temporal import temporal_activity
from tracer.models.observability_provider import ObservabilityProvider, ProviderChoices
from tracer.models.observation_span import ObservationSpan
from tracer.models.project import ProjectSourceChoices
from tracer.models.trace import Trace
from tracer.serializers.observability_provider import ObservabilityProviderSerializer
from tracer.services.observability_providers import ObservabilityService
from tracer.utils.bland import normalize_bland_data
from tracer.utils.eleven_labs import normalize_eleven_labs_data
from tracer.utils.otel import ResourceLimitError, get_or_create_project
from tracer.utils.retell import normalize_retell_data
from tracer.utils.twilio_calls import normalize_twilio_data
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
                logger.info(
                    "fetch_logs_for_provider: get_call_logs RETURNED",
                    provider_id=str(provider_id),
                    logs_type=type(logs).__name__,
                    logs_is_list=isinstance(logs, list),
                    logs_len=len(logs) if hasattr(logs, "__len__") else "n/a",
                    sample_types=(
                        [type(x).__name__ for x in logs[:5]]
                        if isinstance(logs, (list, tuple))
                        else "n/a"
                    ),
                    first_elem_repr=(
                        repr(logs[0])[:400]
                        if isinstance(logs, (list, tuple)) and logs
                        else "n/a"
                    ),
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


def _create_observation_span(
    project, provider, normalized_data, metadata, provider_log_id=None
):
    """Build the conversation Trace + ObservationSpan in memory for a pulled call.

    CDC is off (CH25): the fi-collector export owns the CH ``spans``/``traces``
    write — there are no PG ``tracer_trace`` / ``tracer_observation_span`` tables.
    The trace id is deterministic (provider + log id) so a re-poll upserts in
    place under the CH RMT sort keys (both include trace_id) instead of duplicating.
    """
    span_kwargs = dict(
        id=uuid.uuid4(),
        project=project,
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
        span_attributes=normalized_data.get("span_attributes", {}),
        prompt_tokens=normalized_data.get("prompt_tokens"),
        completion_tokens=normalized_data.get("completion_tokens"),
        total_tokens=normalized_data.get("total_tokens"),
        latency_ms=normalized_data.get("latency_ms"),
    )
    trace = Trace(
        id=_provider_collector_trace_id(provider.provider, provider_log_id),
        project=project,
        metadata=metadata,
    )
    return ObservationSpan(trace=trace, **span_kwargs)


_PROVIDER_SPAN_NS = uuid.UUID("4d61d4e2-7b3c-4a1e-9f02-2c6a5b8e1d70")


def _provider_collector_span_id(provider: str, provider_log_id: str) -> str:
    """Deterministic id stable across re-polls so CH ``spans`` (ReplacingMergeTree) upserts in place.
    Caveat: re-emits reuse the same ``_version`` (start_time), so late data may lose the merge."""
    return uuid.uuid5(_PROVIDER_SPAN_NS, f"{provider}:{provider_log_id}").hex[:16]


def _provider_collector_trace_id(provider: str, provider_log_id: str) -> uuid.UUID:
    """Deterministic trace id stable across re-polls. The CH ``spans`` and ``traces`` RMT sort keys both include trace_id, so a random id per poll would duplicate; this keys both writes to the call."""
    return uuid.uuid5(_PROVIDER_SPAN_NS, f"trace:{provider}:{provider_log_id}")


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
    # Heuristic: < 1e12 seconds, < 1e15 ms, else ns — normalize to ns.
    if v < 1e12:
        return int(v * 1e9)
    if v < 1e15:  # milliseconds
        return int(v * 1e6)
    return int(v)


def _export_provider_call_to_collector(span, provider: str, provider_log_id: str):
    """Emit a pulled call's CONVERSATION span to the fi-collector, which writes it to CH ``spans``/``traces``. Never raises."""
    try:
        project = span.project
        organization_id = str(getattr(project, "organization_id", "") or "")
        if not organization_id:
            return
        # OTLP can't carry the nested raw_log dict; re-attach it as a JSON string below.
        attrs = {
            k: v for k, v in (span.span_attributes or {}).items() if k != "raw_log"
        }
        attrs["gen_ai.span.kind"] = "CONVERSATION"
        attrs["gen_ai.system"] = provider
        if span.input not in (None, "", [], {}):
            attrs["input.value"] = span.input
        if span.output not in (None, "", [], {}):
            attrs["output.value"] = span.output
        raw_log = (span.span_attributes or {}).get("raw_log") or {}
        if raw_log:
            attrs["raw_log"] = json.dumps(raw_log, default=str)
        # Stamp the normalized transcript (read path falls back to this).
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
        # Stamp OTLP status from call outcome so a failed call isn't recorded as completed (collector copies it into CH `spans.status`).
        _call_status = (
            str(attrs.get("call.status") or getattr(span, "status", "") or "")
            .strip()
            .lower()
        )
        if _call_status in (
            "error",
            "failed",
            "failure",
            "busy",
            "no-answer",
            "no_answer",
            "canceled",
            "cancelled",
        ):
            span_dict["status_code"] = "ERROR"
        from tracer.services.collector_ingest import emit_spans_to_collector

        emit_spans_to_collector(
            [span_dict],
            project_name=project.name,
            project_type=project.trace_type,
            organization_id=organization_id,
            workspace_id=str(project.workspace_id) if project.workspace_id else None,
            service_name="fi-provider",
        )
        # collectTrace is sole `traces` writer (derives it from this root span); no app-side mirror, a second row would never merge.
    except Exception:
        logger.exception(
            "provider_collector_export_failed",
            provider=provider,
            provider_log_id=provider_log_id,
        )


def process_and_store_logs(
    logs: list,
    provider: ObservabilityProvider,
    *,
    api_key: str | None = None,
):
    """
    Processes raw log data and stores it as ObservationSpan objects.

    For Vapi providers, ``api_key`` is threaded through to
    :func:`normalize_vapi_data` so the call-log download can use the
    authenticated endpoint. When ``api_key`` is None it is resolved
    via the Selector; when no key is available the pipeline falls back
    to the legacy unauthenticated fetch.
    """
    project = provider.project

    if provider.provider == ProviderChoices.VAPI and api_key is None:
        try:
            from tracer.selectors import get_agent_api_key

            api_key = get_agent_api_key(project.id, provider.provider)
        except Exception:
            logger.exception(
                "process_and_store_logs: vapi api_key resolution failed",
                provider_id=str(provider.id),
            )

    normalization_functions = {
        ProviderChoices.VAPI: lambda log: normalize_vapi_data(log, api_key=api_key),
        ProviderChoices.RETELL: normalize_retell_data,
        ProviderChoices.ELEVEN_LABS: normalize_eleven_labs_data,
        ProviderChoices.BLAND: normalize_bland_data,
        ProviderChoices.TWILIO: normalize_twilio_data,
    }

    if provider.provider not in normalization_functions:
        return

    normalize_fn = normalization_functions[provider.provider]

    logger.info(
        "process_and_store_logs: LOOP START",
        provider_id=str(provider.id),
        provider_type=provider.provider,
        logs_len=len(logs) if isinstance(logs, (list, tuple)) else "NOT_A_LIST",
        logs_type=type(logs).__name__,
        api_key_present=bool(api_key),
    )
    if not isinstance(logs, (list, tuple)):
        logger.error(
            "process_and_store_logs: logs is NOT a list/tuple",
            logs_type=type(logs).__name__,
            logs_repr=repr(logs)[:1000],
        )
        return

    for log_idx, log in enumerate(logs):
        logger.info(
            "process_and_store_logs: ITER",
            idx=log_idx,
            log_type=type(log).__name__,
            is_dict=isinstance(log, dict),
            log_repr=(repr(log)[:300] if not isinstance(log, dict) else f"keys={list(log.keys())[:15]}"),
        )
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
            # CH25: no PG span/trace store; the fi-collector owns CH `spans` and
            # the deterministic span/trace ids upsert re-polls in CH (RMT), so
            # build the span in memory for the collector export below.
            span = _create_observation_span(
                project, provider, normalized_data, metadata, provider_log_id
            )
        except Exception as e:
            logger.exception(
                f"Error creating observation span for {provider.provider}: {e}"
            )
            continue

        # Emit to the fi-collector: it writes CH `spans`/`traces` (the read store)
        # AND meters ingestion usage, so there is no app-side CH write or usage emit.
        _export_provider_call_to_collector(span, provider.provider, provider_log_id)


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
        # Webhook path already has the AgentDefinition in scope, so pass
        # the api_key directly. process_and_store_logs falls back to the
        # Selector otherwise.
        version = agent_definition.active_version or agent_definition.latest_version
        from simulate.services.agent_definition import resolve_api_key_for_version

        api_key = resolve_api_key_for_version(version) if version else None
        process_and_store_logs([call_log], provider, api_key=api_key)

        logger.info("normalize_and_store_logs completed")

    except Exception as e:
        logger.error(f"Error storing logs:{e}")
