import uuid
from datetime import datetime, timedelta
from typing import Any

import requests
import structlog

from tracer.constants.external_endpoints import ObservabilityRoutes

logger = structlog.get_logger(__name__)
from tracer.models.observability_provider import ObservabilityProvider, ProviderChoices
from tracer.models.project import VoiceCallLogs

logger = structlog.get_logger(__name__)

VAPI_PAGE_LIMIT = 100
VAPI_MAX_PAGES = 10


class ObservabilityService:
    """
    A global service class to fetch data from different observability providers.
    """

    @staticmethod
    def verify_api_key(
        provider: str,
        api_key: str,
    ):
        if provider == ProviderChoices.VAPI:
            api_endpoint = f"{ObservabilityRoutes.VAPI_CALL_URL.value}?limit=0"
        elif provider == ProviderChoices.RETELL:
            api_endpoint = (
                f"{ObservabilityRoutes.RETELL_LIST_ASSISTANTS_URL.value}?limit=1"
            )
        else:
            raise ValueError(f"Invalid choice for provider: {provider}")
        headers = {"Authorization": f"Bearer {api_key}"}
        response = requests.get(api_endpoint, headers=headers, timeout=30)
        return response.status_code

    @staticmethod
    def verify_assistant_id(
        provider: str,
        assistant_id: str,
        api_key: str,
    ):
        endpoint = None
        if provider == ProviderChoices.VAPI:
            endpoint = f"{ObservabilityRoutes.VAPI_ASSISTANT_URL.value}/{assistant_id}"
        elif provider == ProviderChoices.RETELL:
            endpoint = (
                f"{ObservabilityRoutes.RETELL_GET_ASSISTANT_URL.value}/{assistant_id}"
            )
        else:
            raise ValueError(f"Invalid choice for provider: {provider}")

        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        response = requests.get(
            endpoint,
            headers=headers,
            timeout=30,
        )
        return response.status_code

    @staticmethod
    def get_call_logs(
        provider: ObservabilityProvider,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ):
        """
        Fetches call logs from the specified provider.
        """
        if provider.provider == ProviderChoices.VAPI:
            return ObservabilityService._fetch_vapi_logs(provider, start_time, end_time)
        elif provider.provider == ProviderChoices.RETELL:
            return ObservabilityService._fetch_retell_logs(
                provider, start_time, end_time
            )
        elif provider.provider == ProviderChoices.ELEVEN_LABS:
            return ObservabilityService._fetch_eleven_labs_logs(
                provider, start_time, end_time
            )
        else:
            raise NotImplementedError(f"Provider {provider.provider} not implemented.")

    @staticmethod
    def _fetch_vapi_logs(
        provider: ObservabilityProvider,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ):
        """
        Fetches call logs from Vapi using time-based pagination.

        Fetches in batches of VAPI_PAGE_LIMIT, using the max `updatedAt`
        from each batch as the cursor for the next request. Stops when a
        batch returns fewer results than the limit or after VAPI_MAX_PAGES.

        Returns:
            List of logs, or empty list if API key is missing.
        """
        agent = ObservabilityService._get_agent_definition(provider)
        api_key = ObservabilityService._validate_agent_api_key(agent, provider, "VAPI")
        if not api_key:
            return []

        headers = {"Authorization": f"Bearer {api_key}"}
        assistant_id = getattr(agent, "assistant_id", None)
        all_logs: list[dict] = []
        current_start = start_time

        for page in range(VAPI_MAX_PAGES):
            params: dict[str, Any] = {
                "assistantId": assistant_id,
                "limit": VAPI_PAGE_LIMIT,
            }
            if current_start:
                params["updatedAtGt"] = current_start.isoformat()
            if end_time:
                params["updatedAtLe"] = end_time.isoformat()

            response = requests.get(
                ObservabilityRoutes.VAPI_CALL_URL.value,
                headers=headers,
                params=params,
                timeout=120,
            )
            response.raise_for_status()
            batch = response.json()
            all_logs.extend(batch)

            if len(batch) < VAPI_PAGE_LIMIT:
                break

            # Use max updatedAt from batch as cursor for next page
            timestamps = [log["updatedAt"] for log in batch if log.get("updatedAt")]
            if not timestamps:
                break
            max_updated_at = max(timestamps)
            current_start = datetime.fromisoformat(
                max_updated_at.replace("Z", "+00:00")
            )

            logger.debug(
                "vapi_pagination_progress",
                provider_id=str(provider.id),
                page=page + 1,
                batch_size=len(batch),
                total_fetched=len(all_logs),
            )

        return all_logs

    @staticmethod
    def _fetch_retell_logs(
        provider: ObservabilityProvider,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ):
        """
        Fetches call logs from Retell AI.

        Returns:
            List of logs, or empty list if API key is missing.
        """
        agent = ObservabilityService._get_agent_definition(provider)
        api_key = ObservabilityService._validate_agent_api_key(
            agent, provider, "Retell"
        )
        if not api_key:
            return []

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        agent_assistant_id = getattr(agent, "assistant_id", None) if agent else None
        data: dict[str, Any] = {
            "limit": 1000,
            "filter_criteria": {
                # Using assistant_id as the agent identifier
                "agent_id": [agent_assistant_id] if agent_assistant_id else [],
                "call_status": ["ended", "error"],
            },
        }
        if start_time and end_time:
            data["filter_criteria"]["start_timestamp"] = {
                "lower_threshold": int(start_time.timestamp() * 1000),
                "upper_threshold": int(end_time.timestamp() * 1000),
            }

        response = requests.post(
            ObservabilityRoutes.RETELL_LIST_CALLS_URL.value,
            headers=headers,
            json=data,
            timeout=30,
        )
        response.raise_for_status()
        return response.json()

    @staticmethod
    def _list_eleven_labs_conversations(
        provider: ObservabilityProvider,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ):
        """
        Lists all conversations for a given ElevenLabs agent.

        Returns:
            List of conversations, or empty list if API key is missing.
        """
        agent = ObservabilityService._get_agent_definition(provider)
        api_key = ObservabilityService._validate_agent_api_key(
            agent, provider, "ElevenLabs"
        )
        if not api_key:
            return []

        headers = {"xi-api-key": api_key}
        params = {
            # Using assistant_id as the agent identifier
            "agent_id": getattr(agent, "assistant_id", None),
            "page_size": 50,
            "summary_mode": "include",
        }
        if start_time:
            params["call_start_after_unix"] = int(start_time.timestamp())
        if end_time:
            params["call_start_before_unix"] = int(end_time.timestamp())

        response = requests.get(
            ObservabilityRoutes.ELEVEN_LABS_CONVERSATIONS_URL.value,
            headers=headers,
            params=params,
            timeout=30,
        )
        response.raise_for_status()
        return response.json().get("conversations", [])

    @staticmethod
    def _fetch_eleven_labs_conversation_details(
        provider: ObservabilityProvider, conversation_id: str
    ):
        """
        Fetches the detailed log for a single ElevenLabs conversation.

        Returns:
            Conversation details dict, or None if API key is missing.
        """
        agent = ObservabilityService._get_agent_definition(provider)
        api_key = ObservabilityService._validate_agent_api_key(
            agent, provider, "ElevenLabs"
        )
        if not api_key:
            return None

        headers = {"xi-api-key": api_key}
        detail_url = f"{ObservabilityRoutes.ELEVEN_LABS_CONVERSATIONS_URL.value}/{conversation_id}"
        response = requests.get(detail_url, headers=headers, timeout=30)
        response.raise_for_status()
        return response.json()

    @staticmethod
    def _fetch_eleven_labs_logs(
        provider: ObservabilityProvider,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ):
        """
        Fetches call logs from ElevenLabs by first listing conversations
        and then fetching details for each one.

        Returns:
            List of detailed logs, or empty list if API key is missing.
        """
        conversations = ObservabilityService._list_eleven_labs_conversations(
            provider, start_time, end_time
        )

        detailed_logs = []
        for conv in conversations:
            details = ObservabilityService._fetch_eleven_labs_conversation_details(
                provider, conv["conversation_id"]
            )
            if details:  # Skip None results
                detailed_logs.append(details)

        return detailed_logs

    @staticmethod
    def _validate_agent_api_key(
        agent, provider: ObservabilityProvider, provider_name: str
    ) -> str | None:
        """
        Validates that the agent and API key exist.

        Args:
            agent: The agent definition object
            provider: The ObservabilityProvider instance
            provider_name: Human-readable provider name for error messages

        Returns:
            The API key if valid, None if missing (logs a warning)
        """
        api_key = getattr(agent, "api_key", None) if agent else None
        if not api_key:
            logger.warning(
                "missing_api_key_for_provider",
                provider_id=str(provider.id),
                provider_name=provider_name,
                message=f"Missing API key for {provider_name} provider. Skipping log fetch.",
            )
            return None
        return api_key

    @staticmethod
    def _get_agent_definition(provider: ObservabilityProvider):
        """
        Access the related AgentDefinition via reverse foreign key.
        Returns the first associated AgentDefinition if multiple exist.
        """
        # related_name on AgentDefinition is "agent_definitions"
        try:
            return provider.agent_definition
        except Exception:
            return None

    @staticmethod
    def _process_vapi_logs(raw_log: dict) -> VoiceCallLogs:

        def raw_log_get(key: str) -> Any:
            return raw_log.get(key)

        call_id = raw_log_get("id")
        customer = raw_log_get("customer") or {}
        call_type = (
            "inbound" if raw_log_get("type") == "inboundPhoneCall" else "outbound"
        )
        started_at = raw_log_get("startedAt")
        created_at = raw_log_get("createdAt")
        ended_at = raw_log_get("endedAt")
        status = "completed" if raw_log_get("status") == "ended" else "in-progress"
        stereo_recording_url = (
            raw_log_get("artifact").get("stereoRecordingUrl")
            if raw_log_get("artifact")
            else None
        )
        summary = raw_log_get("summary")
        ended_reason = raw_log_get("endedReason")
        recording_url = raw_log_get("recordingUrl")
        recording_available = bool(recording_url)
        messages = raw_log_get("messages") or []
        transcript_available = len(messages) > 0
        cost = raw_log_get("cost")
        assistant_id = raw_log_get("assistantId")
        duration_seconds = None
        analysis_data = raw_log_get("analysis")

        # Cost breakdown (STT/LLM/TTS)
        raw_cost_breakdown = raw_log_get("costBreakdown") or {}
        cost_breakdown = (
            {
                "stt": raw_cost_breakdown.get("stt"),
                "llm": raw_cost_breakdown.get("llm"),
                "tts": raw_cost_breakdown.get("tts"),
                "vapi": raw_cost_breakdown.get("vapi"),
                "transport": raw_cost_breakdown.get("transport"),
                "total": raw_cost_breakdown.get("total") or cost,
            }
            if raw_cost_breakdown
            else None
        )

        # Assistant phone number
        phone_number_obj = raw_log_get("phoneNumber") or {}
        assistant_phone = (
            phone_number_obj.get("number")
            if isinstance(phone_number_obj, dict)
            else None
        )
        # Prefer startedAt (actual call start), fall back to createdAt
        # (always present) if startedAt is missing (queued/scheduled calls).
        effective_start = started_at or created_at
        if effective_start and ended_at:
            start_datetime = datetime.fromisoformat(effective_start)
            ended_at_datetime = datetime.fromisoformat(ended_at)
            duration_seconds = int((ended_at_datetime - start_datetime).total_seconds())

        transcripts = []
        for i in range(len(messages)):
            message = messages[i]
            start_time = (
                message.get("secondsFromStart")
                if message.get("secondsFromStart")
                else None
            )
            duration = (
                message.get("duration") / 1000 if message.get("duration") else None
            )
            end_time = (
                timedelta(seconds=start_time + duration)
                if start_time and duration
                else None
            )
            new_message_dict = {
                **message,
                "time": start_time,
                "end_time": end_time.total_seconds() if end_time else None,
                "duration": round(duration, 2) if duration else None,
                "seconds_from_start": start_time,
            }
            messages[i] = new_message_dict
            if i > 0:
                if message.get("role") in ["user", "bot"]:
                    transcripts.append(
                        {
                            "id": str(uuid.uuid4()),
                            "role": message.get("role"),
                            "content": message.get("message"),
                            "time": datetime.fromtimestamp(
                                message.get("time") / 1000
                            ).isoformat(),
                            "duration": round(duration, 2) if duration else None,
                        }
                    )

        # Compute talk ratio from messages
        user_talk_seconds = 0
        bot_talk_seconds = 0
        for msg in messages:
            dur = msg.get("duration") or 0
            role = msg.get("role", "")
            if role == "user":
                user_talk_seconds += dur
            elif role in ("bot", "assistant"):
                bot_talk_seconds += dur
        total_talk = user_talk_seconds + bot_talk_seconds
        talk_ratio = (
            {
                "user": round(user_talk_seconds, 1),
                "bot": round(bot_talk_seconds, 1),
                "user_pct": (
                    round((user_talk_seconds / total_talk) * 100)
                    if total_talk > 0
                    else 0
                ),
                "bot_pct": (
                    round((bot_talk_seconds / total_talk) * 100)
                    if total_talk > 0
                    else 0
                ),
            }
            if total_talk > 0
            else None
        )

        processed_log = {
            "id": None,
            "phone_number": customer.get("number"),
            "customer_name": customer.get("number"),
            "call_id": call_id,
            "status": status,
            "started_at": started_at,
            "ended_at": ended_at,
            "duration_seconds": duration_seconds,
            "recording_url": recording_url,
            "cost_cents": cost * 100 if cost else None,
            "cost_breakdown": cost_breakdown,
            "call_metadata": raw_log_get("callMetadata"),
            "error_message": raw_log_get("errorMessage"),
            "transcript": transcripts,
            "created_at": created_at,
            "recording": {},
            "stereo_recording_url": stereo_recording_url,
            "call_summary": summary,
            "ended_reason": ended_reason,
            "overall_score": raw_log_get("overallScore"),
            "response_time_ms": raw_log_get("responseTimeMs"),
            "response_time_seconds": raw_log_get("responseTimeSeconds"),
            "messages": messages,
            "assistant_id": assistant_id,
            "assistant_phone_number": assistant_phone,
            "call_type": call_type,
            "analysis_data": analysis_data,
            "evaluation_data": None,
            "message_count": len(messages),
            "transcript_available": transcript_available,
            "recording_available": recording_available,
            "observation_span": None,
            "talk_ratio": talk_ratio,
        }

        return VoiceCallLogs(**processed_log).model_dump()

    @staticmethod
    def _process_retell_logs(raw_log: dict) -> VoiceCallLogs:

        def raw_log_get(key: str) -> Any:
            return raw_log.get(key)

        call_id = raw_log_get("call_id")
        call_type = raw_log_get("direction")
        assistant_id = raw_log_get("agent_id")
        status = "completed" if raw_log_get("call_status") == "ended" else "in-progress"
        started_at_timestamp = (
            raw_log_get("start_timestamp") / 1000
            if raw_log_get("start_timestamp")
            else None
        )
        ended_at_timestamp = (
            raw_log_get("end_timestamp") / 1000
            if raw_log_get("end_timestamp")
            else None
        )
        started_at = (
            datetime.fromtimestamp(started_at_timestamp).isoformat()
            if started_at_timestamp
            else None
        )
        ended_at = (
            datetime.fromtimestamp(ended_at_timestamp).isoformat()
            if ended_at_timestamp
            else None
        )
        recording_url = raw_log_get("recording_url")
        transcripts = raw_log_get("transcript_with_tool_calls") or []
        call_cost_object = raw_log_get("call_cost") or {}
        duration_seconds = None
        if started_at_timestamp and ended_at_timestamp:
            duration_seconds = int(ended_at_timestamp - started_at_timestamp)
        cost_cents = call_cost_object.get("combined_cost")
        phone_number = raw_log_get("to_number")
        metadata = raw_log_get("metadata") or {}
        call_analysis = raw_log_get("call_analysis") or {}
        ended_reason = raw_log_get("disconnection_reason")
        stereo_recording_url = raw_log_get("recording_multi_channel_url")
        customer_name = raw_log_get("agent_name")
        messages = []
        processed_transcripts = []
        for transcript in transcripts:
            transcript_exists = (
                transcript.get("words") and len(transcript.get("words")) > 0
            )
            seconds_from_start = None
            end_time = None
            duration = None
            if transcript_exists:
                words = transcript.get("words")
                seconds_from_start = words[0].get("start")
                end_time = words[-1].get("end")
                start_timedelta = timedelta(seconds=seconds_from_start)
                end_timedelta = timedelta(seconds=end_time)
                duration = end_timedelta - start_timedelta

            duration = round(duration.total_seconds(), 2) if duration else None
            seconds_from_start = (
                round(seconds_from_start, 2) if seconds_from_start else None
            )
            role = transcript.get("role")
            messages.append(
                {
                    "role": role,
                    "message": transcript.get("content"),
                    "duration": duration,
                    "time": seconds_from_start,
                    "source": None,
                    "end_time": round(end_time, 2) if end_time else None,
                    "seconds_from_start": seconds_from_start,
                    "metadata": transcript.get("metadata"),
                }
            )
            if transcript.get("role") in ["user", "agent"]:
                processed_transcripts.append(
                    {
                        "id": str(uuid.uuid4()),
                        "role": role,
                        "content": transcript.get("content"),
                        "time": (
                            datetime.fromisoformat(started_at)
                            + timedelta(seconds=seconds_from_start)
                        ).isoformat(),
                        "duration": duration,
                    }
                )

        # Compute talk ratio from messages
        user_talk_secs = 0
        bot_talk_secs = 0
        for msg in messages:
            dur = msg.get("duration") or 0
            role = msg.get("role", "")
            if role == "user":
                user_talk_secs += dur
            elif role in ("agent", "assistant", "bot"):
                bot_talk_secs += dur
        total_talk_secs = user_talk_secs + bot_talk_secs
        talk_ratio = (
            {
                "user": round(user_talk_secs, 1),
                "bot": round(bot_talk_secs, 1),
                "user_pct": (
                    round((user_talk_secs / total_talk_secs) * 100)
                    if total_talk_secs > 0
                    else 0
                ),
                "bot_pct": (
                    round((bot_talk_secs / total_talk_secs) * 100)
                    if total_talk_secs > 0
                    else 0
                ),
            }
            if total_talk_secs > 0
            else None
        )

        # Retell cost breakdown from call_cost
        retell_cost_breakdown = None
        if call_cost_object:
            retell_cost_breakdown = {
                "stt": call_cost_object.get("stt_cost"),
                "llm": call_cost_object.get("llm_cost"),
                "tts": call_cost_object.get("tts_cost"),
                "total": call_cost_object.get("combined_cost"),
            }

        processed_log = {
            "id": None,
            "phone_number": phone_number,
            "customer_name": customer_name,
            "call_id": call_id,
            "status": status,
            "started_at": started_at,
            "completed_at": ended_at,
            "duration_seconds": duration_seconds,
            "recording_url": recording_url,
            "recording_available": bool(recording_url),
            "cost_cents": cost_cents,
            "cost_breakdown": retell_cost_breakdown,
            "call_metadata": metadata,
            "error_message": raw_log.get("error_message"),
            "transcript": processed_transcripts,
            "transcript_available": len(transcripts) > 0,
            "created_at": started_at,
            "recording": {},
            "stereo_recording_url": stereo_recording_url,
            "call_summary": call_analysis.get("call_summary"),
            "ended_reason": ended_reason,
            "overall_score": raw_log.get("overallScore"),
            "response_time_ms": raw_log.get("responseTimeMs"),
            "response_time_seconds": raw_log.get("responseTimeSeconds"),
            "messages": messages,
            "assistant_id": assistant_id,
            "assistant_phone_number": raw_log.get("from_number"),
            "call_type": call_type,
            "ended_at": ended_at,
            "analysis_data": call_analysis,
            "message_count": len(messages),
            "evaluation_data": None,
            "observation_span": None,
            "talk_ratio": talk_ratio,
        }

        return VoiceCallLogs(**processed_log).model_dump()

    @staticmethod
    def process_raw_logs(
        raw_log: dict,
        provider: str,
        span_attributes: dict | None = None,
    ) -> VoiceCallLogs:
        """
        Processes a raw log from a voice provider into a structured format.

        Args:
            raw_log: Raw call log from the provider
            provider: One of ProviderChoices.VAPI or ProviderChoices.RETELL
            span_attributes: Optional ObservationSpan.span_attributes. When
                provided, the canonical recording URLs from the span (which
                may be FAGI-S3-rehosted) override the provider URLs read from
                ``raw_log``.

        Returns:
            VoiceCallLogs object containing processed call logs

        Raises:
            ValueError: If provider is not recognized
        """
        if provider == ProviderChoices.VAPI:
            processed = ObservabilityService._process_vapi_logs(raw_log)
        elif provider == ProviderChoices.RETELL:
            processed = ObservabilityService._process_retell_logs(raw_log)
        else:
            raise ValueError(f"Invalid choice for provider: {provider}")

        if span_attributes:
            mono_combined = span_attributes.get("conversation.recording.mono.combined")
            stereo = span_attributes.get("conversation.recording.stereo")
            if mono_combined:
                processed["recording_url"] = mono_combined
            if stereo:
                processed["stereo_recording_url"] = stereo

        return processed
