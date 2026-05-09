import uuid
from datetime import datetime

import structlog
from django.db import transaction
from requests.exceptions import HTTPError

from accounts.models.organization import Organization

logger = structlog.get_logger(__name__)
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
from tracer.utils.eleven_labs import normalize_eleven_labs_data
from tracer.utils.otel import ResourceLimitError, get_or_create_project
from tracer.utils.retell import normalize_retell_data
from tracer.utils.vapi import normalize_vapi_data


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
    """Creates a new Trace and ObservationSpan."""
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


def _update_observation_span(existing_span, normalized_data):
    """Updates an existing ObservationSpan and its associated Trace."""
    attributes = normalized_data.get("span_attributes", {})

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
    }

    if provider.provider not in normalization_functions:
        return

    normalize_fn = normalization_functions[provider.provider]

    for log in logs:
        normalized_data = normalize_fn(log)
        provider_log_id = normalized_data.get("id")

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
                    existing_span = ObservationSpan.objects.filter(
                        id=existing_span_id,
                        project=project,
                        provider=provider.provider,
                    ).first()

                # Fallback to the small/cheap PG-side metadata GIN lookup if
                # CH is unavailable or hasn't indexed this span yet.
                if existing_span is None:
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
                else:
                    span = _create_observation_span(
                        project, provider, normalized_data, metadata
                    )

                _maybe_enqueue_recording_rehost(provider, span)
        except Exception as e:
            logger.exception(
                f"Error updating or creating observation span for {provider.provider}: {e}"
            )
            continue


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
    transaction.on_commit(
        lambda: rehost_external_recordings.delay(span_id=span_id)
    )


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
            logger.warning(f"normalize_and_store_logs: No provider")
            return

        call_log = body.get("call")
        process_and_store_logs([call_log], provider)

        logger.info("normalize_and_store_logs completed")

    except Exception as e:
        logger.error(f"Error storing logs:{e}")
