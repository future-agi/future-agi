"""Single service for Vapi recording + call-log operations.

Handles endpoint URLs, Bearer auth (mandatory from 2026-07-15), API-key
resolution, S3 rehost, DB mirroring, dead-URL guards, and call-log
parsing so callers can just import, call a classmethod, and use the
result.
"""

from __future__ import annotations

import gzip
import io
import json
import logging
import uuid
from datetime import datetime
from datetime import timezone as dt_timezone
from typing import Any, Iterable, Optional

import httpx
import requests
import structlog

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Constants — keep in one place; every callsite reads from here.
# ---------------------------------------------------------------------------

VAPI_API_BASE_URL = "https://api.vapi.ai"

# Provider hosts. Any URL matching these hosts requires Bearer auth from
# 2026-07-15 and stops working entirely on 2026-07-25. Read guards treat
# these as "dead" and refuse to surface them to the FE.
_DEAD_PROVIDER_HOSTS = ("storage.vapi.ai", "calllogs.vapi.ai")


class VapiArtifactType(str):
    """Artifact-type path segments for
    ``api.vapi.ai/call/{call_id}/{artifact_type}``.
    """

    MONO = "mono-recording"
    STEREO = "stereo-recording"
    CUSTOMER = "customer-recording"
    ASSISTANT = "assistant-recording"
    VIDEO = "video-recording"
    CALL_LOGS = "call-logs"
    PCAP = "pcap"


# Map from the internal url_type label (used by the rehost recording-keys
# table) to the Vapi artifact path segment.
_URL_TYPE_TO_ARTIFACT: dict[str, str] = {
    "mono_combined": VapiArtifactType.MONO,
    "mono_customer": VapiArtifactType.CUSTOMER,
    "mono_assistant": VapiArtifactType.ASSISTANT,
    "stereo": VapiArtifactType.STEREO,
}


DEFAULT_TIMEOUT_SECONDS = 60.0
S3_URL_MARKERS = ("amazonaws.com", "minio")


# ---------------------------------------------------------------------------
# Typed exceptions — callers can distinguish rotate-key from transient errors.
# ---------------------------------------------------------------------------


class VapiAuthError(Exception):
    """401 or 403 from Vapi. The api_key is rotated or invalid."""


class VapiArtifactNotReadyError(Exception):
    """404 from Vapi. Artifact is not yet finalised (retry later)."""


class VapiRateLimitError(Exception):
    """429 from Vapi. Back off and retry."""


# ---------------------------------------------------------------------------
# The service.
# ---------------------------------------------------------------------------


class VapiRecordingService:
    """Single entry point for Vapi recording + call-log operations.

    All methods are classmethods. Callers do not instantiate.
    """

    # =====================================================================
    # URL builders + guards
    # =====================================================================

    @classmethod
    def build_artifact_url(cls, call_id: str, artifact_type: str) -> str:
        """Build the authenticated endpoint URL for a call artifact."""
        return f"{VAPI_API_BASE_URL}/call/{call_id}/{artifact_type}"

    @classmethod
    def artifact_for_url_type(cls, url_type: str) -> Optional[str]:
        """Map a rehost-pipeline url_type to a Vapi artifact path.

        Returns None for unknown mappings (e.g. Retell keys) — callers
        should fall back to the non-Vapi download path.
        """
        return _URL_TYPE_TO_ARTIFACT.get(url_type)

    @classmethod
    def is_dead_provider_url(cls, url: Optional[str]) -> bool:
        """True if ``url`` points at a Vapi provider host that requires
        auth from 2026-07-15. Read guards refuse to surface these URLs
        because a browser cannot attach the Authorization header to an
        ``<audio src>`` tag.
        """
        if not url:
            return False
        return any(host in url for host in _DEAD_PROVIDER_HOSTS)

    @classmethod
    def is_s3_url(cls, url: Optional[str]) -> bool:
        """True if ``url`` is a durable S3 / MinIO URL (safe to serve)."""
        if not url:
            return False
        return any(marker in url for marker in S3_URL_MARKERS)

    # =====================================================================
    # API key resolution
    # =====================================================================
    #
    # AgentDefinition carries the customer's provider API key in two
    # places: the plaintext ``api_key`` column (legacy) and the latest
    # ``AgentVersion.configuration_snapshot["api_key"]`` (versioned
    # snapshot). Prefer the version snapshot when available so we always
    # use the key that was current when the observability integration
    # was last configured.

    @classmethod
    def get_api_key_for_project(cls, project_id: Any) -> Optional[str]:
        """Resolve the customer's Vapi API key for a project.

        Order:
          1. ``ObservabilityProvider`` (provider=vapi, enabled) ->
             ``agent_definition.latest_version.configuration_snapshot["api_key"]``
          2. ``ObservabilityProvider.agent_definition.api_key`` (legacy
             plaintext column)
          3. Any voice ``AgentDefinition`` on the same project with a
             Vapi provider carrying a non-empty api_key
          4. ``None``

        Returns None on any error (fail-open — the caller falls back to
        the legacy unauthenticated download path).
        """
        if project_id is None:
            return None

        try:
            provider = cls._get_vapi_provider_for_project(project_id)
        except Exception:
            logger.exception(
                "vapi_recording_service.get_api_key_project_lookup_failed",
                project_id=str(project_id),
            )
            return None

        if provider is not None:
            key = cls._api_key_from_provider_row(provider)
            if key:
                return key

        try:
            return cls._api_key_from_any_agent_on_project(project_id)
        except Exception:
            logger.exception(
                "vapi_recording_service.get_api_key_agent_lookup_failed",
                project_id=str(project_id),
            )
            return None

    @classmethod
    def get_api_key_for_agent_definition(
        cls, agent_definition_id: Any
    ) -> Optional[str]:
        """Resolve the api_key for a specific AgentDefinition id.

        Prefers the latest version's configuration snapshot; falls back
        to the AgentDefinition's plaintext ``api_key`` column. Returns
        None on missing / errors.
        """
        if agent_definition_id is None:
            return None

        try:
            from simulate.models.agent_definition import AgentDefinition
        except Exception:
            return None

        if isinstance(agent_definition_id, str):
            try:
                agent_definition_id = uuid.UUID(agent_definition_id)
            except ValueError:
                return None

        agent_def = (
            AgentDefinition.objects.filter(id=agent_definition_id)
            .only("id", "api_key")
            .first()
        )
        if agent_def is None:
            return None
        return cls._api_key_from_agent_definition(agent_def)

    # ----- Internal key-resolution helpers --------------------------------

    @classmethod
    def _get_vapi_provider_for_project(cls, project_id: Any):
        from tracer.models.observability_provider import (
            ObservabilityProvider,
            ProviderChoices,
        )

        return (
            ObservabilityProvider.objects.filter(
                project_id=project_id,
                provider=ProviderChoices.VAPI,
                enabled=True,
            )
            .first()
        )

    @classmethod
    def _api_key_from_provider_row(cls, provider) -> Optional[str]:
        agent_def = None
        try:
            agent_def = provider.agent_definition
        except Exception:
            agent_def = None
        if agent_def is None:
            return None
        return cls._api_key_from_agent_definition(agent_def)

    @classmethod
    def _api_key_from_any_agent_on_project(
        cls, project_id: Any
    ) -> Optional[str]:
        try:
            from simulate.models.agent_definition import AgentDefinition
        except Exception:
            return None

        agent_def = (
            AgentDefinition.objects.filter(
                observability_provider__project_id=project_id,
                provider="vapi",
            )
            .exclude(api_key__isnull=True)
            .exclude(api_key="")
            .only("id", "api_key")
            .first()
        )
        if agent_def is None:
            return None
        return cls._api_key_from_agent_definition(agent_def)

    @classmethod
    def _api_key_from_agent_definition(cls, agent_def) -> Optional[str]:
        # Prefer the versioned snapshot: it reflects the credential that
        # was active when the observability integration was last saved.
        try:
            latest_version = getattr(agent_def, "latest_version", None)
        except Exception:
            latest_version = None
        if latest_version is not None:
            snapshot = getattr(latest_version, "configuration_snapshot", None) or {}
            snapshot_key = snapshot.get("api_key") if isinstance(snapshot, dict) else None
            if snapshot_key:
                return snapshot_key

        # Fall back to the plaintext column.
        raw_key = getattr(agent_def, "api_key", None) or None
        return raw_key or None

    # =====================================================================
    # Download primitives — hit api.vapi.ai with Bearer, follow 302.
    # =====================================================================

    @classmethod
    async def download_artifact_async(
        cls,
        call_id: str,
        artifact_type: str,
        api_key: str,
        *,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> bytes:
        """Download a Vapi artifact via the authenticated endpoint.

        ``httpx.AsyncClient(follow_redirects=True)`` follows the 302 to
        the short-lived signed URL. The redirect target is single-use;
        callers must re-invoke this method for retries — never cache
        the redirected URL.
        """
        cls._require_download_args(call_id, artifact_type, api_key)
        url = cls.build_artifact_url(call_id, artifact_type)
        headers = {"Authorization": f"Bearer {api_key}"}

        async with httpx.AsyncClient(
            follow_redirects=True, timeout=timeout_seconds
        ) as client:
            try:
                response = await client.get(url, headers=headers)
            except httpx.HTTPError as exc:
                logger.warning(
                    "vapi_artifact_download_transport_error",
                    call_id=call_id,
                    artifact_type=artifact_type,
                    error=str(exc),
                )
                raise
            return cls._raise_or_return_body(response, call_id, artifact_type)

    @classmethod
    def download_artifact_sync(
        cls,
        call_id: str,
        artifact_type: str,
        api_key: str,
        *,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> bytes:
        """Synchronous variant of :meth:`download_artifact_async`."""
        cls._require_download_args(call_id, artifact_type, api_key)
        url = cls.build_artifact_url(call_id, artifact_type)
        headers = {"Authorization": f"Bearer {api_key}"}

        with httpx.Client(
            follow_redirects=True, timeout=timeout_seconds
        ) as client:
            try:
                response = client.get(url, headers=headers)
            except httpx.HTTPError as exc:
                logger.warning(
                    "vapi_artifact_download_transport_error",
                    call_id=call_id,
                    artifact_type=artifact_type,
                    error=str(exc),
                )
                raise
            return cls._raise_or_return_body(response, call_id, artifact_type)

    @staticmethod
    def _require_download_args(
        call_id: str, artifact_type: str, api_key: str
    ) -> None:
        if not call_id:
            raise ValueError("call_id is required")
        if not artifact_type:
            raise ValueError("artifact_type is required")
        if not api_key:
            raise ValueError("api_key is required")

    @staticmethod
    def _raise_or_return_body(response, call_id: str, artifact_type: str) -> bytes:
        status = response.status_code
        if status in (401, 403):
            logger.warning(
                "vapi_artifact_download_auth_failed",
                call_id=call_id,
                artifact_type=artifact_type,
                status=status,
            )
            raise VapiAuthError(
                f"Vapi returned {status} for {artifact_type} on {call_id}"
            )
        if status == 404:
            raise VapiArtifactNotReadyError(
                f"Vapi returned 404 for {artifact_type} on {call_id}"
            )
        if status == 429:
            raise VapiRateLimitError(
                f"Vapi returned 429 for {artifact_type} on {call_id}"
            )
        response.raise_for_status()
        return response.content

    # =====================================================================
    # Rehost — download + upload + return durable S3 URL.
    # =====================================================================

    @classmethod
    async def rehost_recording_bytes(
        cls,
        call_id: str,
        artifact_type: str,
        url_type: str,
        api_key: str,
    ) -> tuple[str, int]:
        """Download the artifact bytes via Bearer and upload to FA S3.

        Returns ``(s3_url, bytes_uploaded)``. The returned URL is ALWAYS
        a durable S3 URL — this method never returns a raw provider URL.
        On failure the method raises; callers decide whether to retry.
        """
        audio_bytes = await cls.download_artifact_async(
            call_id, artifact_type, api_key
        )

        # S3 upload is sync; run in the worker's thread pool.
        import asyncio

        from tfc.utils.storage import upload_audio_to_s3

        loop = asyncio.get_running_loop()

        def _do_upload() -> str:
            object_key = f"call-recordings/{call_id}/{url_type}.mp3"
            return upload_audio_to_s3(
                {"bytes": audio_bytes}, object_key=object_key
            )

        s3_url = await loop.run_in_executor(None, _do_upload)
        return s3_url, len(audio_bytes)

    @classmethod
    def mirror_s3_url_to_consumer_fields(
        cls,
        *,
        span,
        call_id: str,
        s3_url_by_url_type: dict[str, str],
    ) -> dict[str, Any]:
        """Propagate the S3 URL to every stored copy of the recording URL
        so no downstream consumer ever reads a raw provider URL from the
        DB.

        Updates in order (each guarded so a durable S3 URL is never
        clobbered by a later run):

        * ``span_attributes["recording_url"]`` (flat alias for
          ``mono_combined``).
        * ``span_attributes["stereo_recording_url"]`` (flat alias for
          ``stereo``).
        * ``CallExecution.recording_url`` /
          ``CallExecution.stereo_recording_url`` on the row linked by
          ``service_provider_call_id == call_id`` (best-effort — no-op
          if no CallExecution row exists).
        * ``CallExecutionSnapshot.recording_url`` /
          ``CallExecutionSnapshot.stereo_recording_url`` for every
          snapshot linked by the same ``service_provider_call_id``
          (best-effort — snapshots are immutable but pre-mirror
          snapshots must not surface the dead URL).

        Callers pass an already-computed dict mapping
        ``url_type -> s3_url``. The method mutates ``span.span_attributes``
        in place and returns the mutated attrs dict for the caller to
        persist alongside its own writes (single ``.save`` per span).
        """
        attrs = dict(span.span_attributes or {}) if span is not None else {}

        mono_s3 = s3_url_by_url_type.get("mono_combined")
        stereo_s3 = s3_url_by_url_type.get("stereo")

        if mono_s3 and not cls.is_s3_url(attrs.get("recording_url")):
            attrs["recording_url"] = mono_s3
        if stereo_s3 and not cls.is_s3_url(attrs.get("stereo_recording_url")):
            attrs["stereo_recording_url"] = stereo_s3

        if call_id and (mono_s3 or stereo_s3):
            cls._mirror_to_call_execution(call_id, mono_s3, stereo_s3)
            cls._mirror_to_call_execution_snapshot(call_id, mono_s3, stereo_s3)

        return attrs

    @classmethod
    def _mirror_to_call_execution(
        cls,
        call_id: str,
        mono_s3: Optional[str],
        stereo_s3: Optional[str],
    ) -> None:
        try:
            from simulate.models.test_execution import CallExecution
        except Exception:
            return

        row = (
            CallExecution.objects.filter(service_provider_call_id=call_id)
            .only("id", "recording_url", "stereo_recording_url")
            .first()
        )
        if row is None:
            return

        update_fields: list[str] = []
        if mono_s3 and not cls.is_s3_url(row.recording_url or ""):
            row.recording_url = mono_s3
            update_fields.append("recording_url")
        if stereo_s3 and not cls.is_s3_url(row.stereo_recording_url or ""):
            row.stereo_recording_url = stereo_s3
            update_fields.append("stereo_recording_url")
        if update_fields:
            row.save(update_fields=update_fields)

    @classmethod
    def _mirror_to_call_execution_snapshot(
        cls,
        call_id: str,
        mono_s3: Optional[str],
        stereo_s3: Optional[str],
    ) -> None:
        try:
            from simulate.models.test_execution import CallExecutionSnapshot
        except Exception:
            return

        rows = list(
            CallExecutionSnapshot.objects.filter(service_provider_call_id=call_id)
            .only("id", "recording_url", "stereo_recording_url")
        )
        for row in rows:
            update_fields: list[str] = []
            if mono_s3 and not cls.is_s3_url(row.recording_url or ""):
                row.recording_url = mono_s3
                update_fields.append("recording_url")
            if stereo_s3 and not cls.is_s3_url(row.stereo_recording_url or ""):
                row.stereo_recording_url = stereo_s3
                update_fields.append("stereo_recording_url")
            if update_fields:
                row.save(update_fields=update_fields)

    # =====================================================================
    # Call logs — Tier 1 (Bearer) with fallback to Tier 2 legacy URL.
    # =====================================================================

    @classmethod
    def fetch_and_parse_call_logs(
        cls,
        call_id: Optional[str],
        api_key: Optional[str],
        *,
        legacy_url: Optional[str] = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        verify_ssl: bool = True,
    ) -> Optional[list[dict[str, Any]]]:
        """Fetch and parse gzip-JSONL call logs.

        Tier 1 (preferred): ``GET api.vapi.ai/call/{id}/call-logs`` with
        ``Authorization: Bearer <api_key>`` (302 -> signed URL). Requires
        both ``call_id`` and ``api_key``.

        Tier 2 (grace fallback): unauthenticated GET against
        ``legacy_url`` — the ``artifact.logUrl`` pointing at
        ``calllogs.vapi.ai``. Works until 2026-07-25.

        Returns the parsed entries list on success, or ``None`` if
        neither tier could fetch anything (fail-open — the caller
        continues without ``call_logs`` on the span).
        """
        content = cls._fetch_call_logs_content(
            call_id=call_id,
            api_key=api_key,
            legacy_url=legacy_url,
            timeout_seconds=timeout_seconds,
            verify_ssl=verify_ssl,
        )
        if content is None:
            return None
        try:
            return cls.parse_call_log_content(content)
        except Exception:
            logger.warning(
                "vapi_call_logs_parse_failed", call_id=call_id, exc_info=True
            )
            return None

    @classmethod
    def fetch_call_logs_content(
        cls,
        call_id: Optional[str],
        api_key: Optional[str],
        *,
        legacy_url: Optional[str] = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        verify_ssl: bool = True,
    ) -> Optional[bytes]:
        """Fetch raw gzip-JSONL bytes for a call's logs via Tier 1/Tier 2.

        Public entry point for callers (e.g. ``ee.voice.services``) that
        need to stream entries themselves. Returns None on failure.
        """
        return cls._fetch_call_logs_content(
            call_id=call_id,
            api_key=api_key,
            legacy_url=legacy_url,
            timeout_seconds=timeout_seconds,
            verify_ssl=verify_ssl,
        )

    @classmethod
    def _fetch_call_logs_content(
        cls,
        call_id: Optional[str],
        api_key: Optional[str],
        legacy_url: Optional[str],
        timeout_seconds: float,
        verify_ssl: bool,
    ) -> Optional[bytes]:
        if call_id and api_key:
            try:
                url = cls.build_artifact_url(
                    call_id, VapiArtifactType.CALL_LOGS
                )
                headers = {"Authorization": f"Bearer {api_key}"}
                response = requests.get(
                    url,
                    headers=headers,
                    timeout=timeout_seconds,
                    verify=verify_ssl,
                    allow_redirects=True,
                )
                if response.status_code in (401, 403):
                    logger.warning(
                        "vapi_call_logs_auth_failed_falling_back",
                        call_id=call_id,
                        status=response.status_code,
                    )
                else:
                    response.raise_for_status()
                    return response.content
            except Exception:
                logger.warning(
                    "vapi_call_logs_tier1_failed_falling_back",
                    call_id=call_id,
                    exc_info=True,
                )

        if legacy_url:
            try:
                response = requests.get(
                    legacy_url,
                    timeout=timeout_seconds,
                    verify=verify_ssl,
                    stream=True,
                )
                response.raise_for_status()
                return response.content
            except Exception:
                logger.warning(
                    "vapi_call_logs_tier2_failed",
                    call_id=call_id,
                    legacy_url=legacy_url,
                    exc_info=True,
                )
        return None

    @classmethod
    def parse_call_log_content(cls, content: bytes) -> list[dict[str, Any]]:
        """Decode gzipped JSONL bytes into the ``call_logs`` list schema
        (compact records with severity, category, body, attributes).
        """
        entries: list[dict[str, Any]] = []
        buffer = io.BytesIO(content)
        with gzip.GzipFile(fileobj=buffer) as gz:
            for raw_line in gz:
                line = raw_line.decode("utf-8").strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    payload = {"raw_line": line}
                entries.append(cls._normalise_call_log_entry(payload))
        return entries

    @classmethod
    def iter_parsed_call_log_records(
        cls,
        call_id: Optional[str],
        api_key: Optional[str],
        *,
        legacy_url: Optional[str] = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        verify_ssl: bool = True,
    ) -> Iterable[dict[str, Any]]:
        """Yield RAW parsed JSON records from the gzip-JSONL log file.

        Preserves the original per-line dict (each line is one JSON
        record produced by the provider) without post-processing. Used
        by call-log ingestion pipelines that need the original schema.
        """
        content = cls._fetch_call_logs_content(
            call_id=call_id,
            api_key=api_key,
            legacy_url=legacy_url,
            timeout_seconds=timeout_seconds,
            verify_ssl=verify_ssl,
        )
        if content is None:
            return

        buffer = io.BytesIO(content)
        with gzip.GzipFile(fileobj=buffer) as gz:
            for raw_line in gz:
                line = raw_line.decode("utf-8").strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    logger.warning(
                        "vapi_call_logs_line_json_decode_failed",
                        call_id=call_id,
                    )
                    yield {"raw_line": line}

    @staticmethod
    def _normalise_call_log_entry(payload: dict[str, Any]) -> dict[str, Any]:
        level_value = payload.get("level")
        try:
            level = int(level_value) if level_value is not None else 0
        except (TypeError, ValueError):
            level = 0

        severity_text = payload.get("severityText") or ""
        body = payload.get("body") or ""
        attributes = payload.get("attributes") or {}
        if not isinstance(attributes, dict):
            attributes = {}
        category = attributes.get("category") or ""

        return {
            "id": str(uuid.uuid4()),
            "logged_at": VapiRecordingService._coerce_log_datetime(payload),
            "level": level,
            "severity_text": str(severity_text)[:32],
            "category": str(category)[:128],
            "body": str(body)[:1024],
            "attributes": attributes,
            "payload": payload,
        }

    @staticmethod
    def _coerce_log_datetime(payload: dict[str, Any]) -> Optional[str]:
        ts = payload.get("timestamp") or payload.get("time")
        if isinstance(ts, (int, float)):
            try:
                return datetime.fromtimestamp(
                    ts / 1_000_000_000, tz=dt_timezone.utc
                ).isoformat()
            except (OverflowError, OSError, ValueError):
                return None
        iso_value = payload.get("ts")
        if isinstance(iso_value, str):
            try:
                return datetime.fromisoformat(
                    iso_value.replace("Z", "+00:00")
                ).isoformat()
            except ValueError:
                return None
        return None
