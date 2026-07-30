import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import requests
import structlog

from tracer.constants.external_endpoints import ObservabilityRoutes
from tracer.models.observability_provider import ObservabilityProvider, ProviderChoices
from tracer.models.project import VoiceCallLogs

logger = structlog.get_logger(__name__)

VAPI_PAGE_LIMIT = 100
VAPI_MAX_PAGES = 10
OBSERVABILITY_VERIFY_TIMEOUT_SECONDS = 30


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
        elif provider == ProviderChoices.BLAND:
            # Bland validates via its read-only account endpoint and takes the
            # raw key in the authorization header (no Bearer prefix).
            response = requests.get(
                ObservabilityRoutes.BLAND_ME_URL.value,
                headers={"authorization": api_key},
                timeout=OBSERVABILITY_VERIFY_TIMEOUT_SECONDS,
            )
            return response.status_code
        else:
            raise ValueError(f"Invalid choice for provider: {provider}")
        headers = {"Authorization": f"Bearer {api_key}"}
        response = requests.get(
            api_endpoint,
            headers=headers,
            timeout=OBSERVABILITY_VERIFY_TIMEOUT_SECONDS,
        )
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
        elif provider == ProviderChoices.BLAND:
            # Bland's "assistant" is a pathway; the raw key goes in authorization.
            response = requests.get(
                f"{ObservabilityRoutes.BLAND_PATHWAY_URL.value}/{assistant_id}",
                headers={"authorization": api_key},
                timeout=OBSERVABILITY_VERIFY_TIMEOUT_SECONDS,
            )
            return response.status_code
        else:
            raise ValueError(f"Invalid choice for provider: {provider}")

        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        response = requests.get(
            endpoint,
            headers=headers,
            timeout=OBSERVABILITY_VERIFY_TIMEOUT_SECONDS,
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
        elif provider.provider == ProviderChoices.BLAND:
            return ObservabilityService._fetch_bland_logs(
                provider, start_time, end_time
            )
        elif provider.provider == ProviderChoices.TWILIO:
            return ObservabilityService._fetch_twilio_logs(
                provider, start_time, end_time
            )
        elif provider.provider == ProviderChoices.LIVEKIT:
            # LiveKit has no hosted call history; [] avoids a crash-loop.
            return []
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
    def _fetch_bland_logs(
        provider: ObservabilityProvider,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ):
        """Pull Bland.ai calls + per-call detail. Auth: ``authorization: <api_key>`` (no Bearer)."""
        agent = ObservabilityService._get_agent_definition(provider)
        api_key = ObservabilityService._validate_agent_api_key(agent, provider, "Bland")
        if not api_key:
            return []

        headers = {"authorization": api_key}
        params: dict = {"limit": 100, "ascending": False}
        if start_time:
            params["start_date"] = start_time.strftime("%Y-%m-%d")
        if end_time:
            # end_date is date-granular and exclusive; send day+1 to keep same-day calls.
            params["end_date"] = (end_time + timedelta(days=1)).strftime("%Y-%m-%d")

        response = requests.get(
            ObservabilityRoutes.BLAND_CALLS_URL.value,
            headers=headers,
            params=params,
            timeout=30,
        )
        response.raise_for_status()
        calls = response.json().get("calls", []) or []

        detailed_logs = []
        for call in calls:
            call_id = call.get("call_id") or call.get("c_id")
            if not call_id:
                continue
            detail_resp = requests.get(
                f"{ObservabilityRoutes.BLAND_CALLS_URL.value}/{call_id}",
                headers=headers,
                timeout=30,
            )
            if detail_resp.status_code != 200:
                logger.warning(
                    "bland_call_detail_fetch_failed",
                    provider_id=str(provider.id),
                    call_id=call_id,
                    status_code=detail_resp.status_code,
                )
                # Fall back to the listing row (metadata-only, no transcripts).
                detailed_logs.append(call)
                continue
            detailed_logs.append(detail_resp.json())

        return detailed_logs

    @staticmethod
    def _fetch_twilio_logs(
        provider: ObservabilityProvider,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ):
        """Pull Twilio Call resources (metadata only). ``api_key`` is ``"<AccountSid>:<AuthToken>"`` for basic auth."""
        agent = ObservabilityService._get_agent_definition(provider)
        api_key = ObservabilityService._validate_agent_api_key(
            agent, provider, "Twilio"
        )
        if not api_key:
            return []
        if ":" not in api_key:
            logger.warning(
                "twilio_api_key_format_invalid",
                provider_id=str(provider.id),
                message=(
                    "Twilio observability needs api_key formatted as "
                    "'<AccountSid>:<AuthToken>'. Skipping log fetch."
                ),
            )
            return []

        account_sid, auth_token = api_key.split(":", 1)
        params: dict = {"PageSize": 100}
        if start_time:
            params["StartTime>"] = start_time.strftime("%Y-%m-%d")
        if end_time:
            # StartTime< is date-granular and exclusive; send day+1 to keep same-day calls.
            params["StartTime<"] = (end_time + timedelta(days=1)).strftime("%Y-%m-%d")

        response = requests.get(
            ObservabilityRoutes.TWILIO_CALLS_URL.value.format(account_sid=account_sid),
            auth=(account_sid, auth_token),
            params=params,
            timeout=30,
        )
        response.raise_for_status()
        return response.json().get("calls", []) or []

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
    def _process_vapi_logs(
        raw_log: dict,
        span_attributes: dict | None = None,
    ) -> VoiceCallLogs:

        sa = span_attributes or {}

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
        recording_url = (
            sa.get("recording_url")
            or sa.get("conversation.recording.mono.combined")
            or (raw_log.get("artifact") or {}).get("recording", {}).get("mono", {}).get("combinedUrl")
            or raw_log.get("recordingUrl")
        )
        stereo_recording_url = (
            sa.get("stereo_recording_url")
            or sa.get("conversation.recording.stereo")
            or (raw_log.get("artifact") or {}).get("recording", {}).get("stereoUrl")
            or (raw_log.get("artifact") or {}).get("stereoRecordingUrl")
        )

        recording_available = bool(recording_url)
        summary = raw_log_get("summary")
        ended_reason = raw_log_get("endedReason")
        messages = raw_log_get("messages") or []
        transcript_available = len(messages) > 0
        cost = raw_log_get("cost")
        assistant_id = raw_log_get("assistantId")
        duration_seconds = None
        analysis_data = raw_log_get("analysis") or None

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
                                message.get("time") / 1000, tz=timezone.utc
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
            datetime.fromtimestamp(started_at_timestamp, tz=timezone.utc).isoformat()
            if started_at_timestamp
            else None
        )
        ended_at = (
            datetime.fromtimestamp(ended_at_timestamp, tz=timezone.utc).isoformat()
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
                if started_at and seconds_from_start is not None:
                    abs_time = (
                        datetime.fromisoformat(started_at)
                        + timedelta(seconds=seconds_from_start)
                    ).isoformat()
                else:
                    abs_time = None
                processed_transcripts.append(
                    {
                        "id": str(uuid.uuid4()),
                        "role": role,
                        "content": transcript.get("content"),
                        "time": abs_time,
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
        if not raw_log:
            # OTLP export drops raw_log; rebuild the call-log shape from the span's call.* attrs.
            attrs = span_attributes or {}
            sim_meta = (
                (attrs.get("metadata") or {})
                if isinstance(attrs.get("metadata"), dict)
                else {}
            )
            duration = attrs.get("call.duration")
            return {
                "call_id": sim_meta.get("call_execution_id"),
                "status": attrs.get("call.status") or "completed",
                "started_at": None,  # span start_time is authoritative
                "duration_seconds": int(duration) if duration is not None else None,
                "recording_url": attrs.get("conversation.recording.mono.combined"),
                "stereo_recording_url": attrs.get("conversation.recording.stereo"),
                "call_metadata": sim_meta,
            }

        # The `provider` hot column can carry the LLM provider (e.g. 'openai')
        # for a voice span whose assistant runs an OpenAI model — the collector
        # ranks gen_ai.provider.name above gen_ai.system. Resolve the real voice
        # provider: prefer gen_ai.system, then default to vapi (the dominant
        # provider) rather than 400 the whole voice list on an unrecognized label.
        voice_providers = {
            ProviderChoices.VAPI,
            ProviderChoices.RETELL,
            ProviderChoices.ELEVEN_LABS,
            ProviderChoices.BLAND,
            ProviderChoices.TWILIO,
        }
        if provider not in voice_providers:
            provider = (span_attributes or {}).get("gen_ai.system") or provider
        if provider not in voice_providers:
            provider = ProviderChoices.VAPI

        if provider == ProviderChoices.RETELL:
            processed = ObservabilityService._process_retell_logs(raw_log)
        elif provider == ProviderChoices.ELEVEN_LABS:
            processed = ObservabilityService._process_eleven_labs_raw(raw_log)
        elif provider == ProviderChoices.BLAND:
            processed = ObservabilityService._process_bland_raw(raw_log)
        elif provider == ProviderChoices.TWILIO:
            processed = ObservabilityService._process_twilio_raw(raw_log)
        else:  # VAPI, and the default for any still-unrecognized label
            processed = ObservabilityService._process_vapi_logs(raw_log, span_attributes)

        if span_attributes:
            from tracer.utils.vapi_recording import VapiRecordingService

            mono_s3 = (
                span_attributes.get("recording_url")
                or span_attributes.get("conversation.recording.mono.combined")
            )
            stereo_s3 = (
                span_attributes.get("stereo_recording_url")
                or span_attributes.get("conversation.recording.stereo")
            )
            logger.info(
                "process_raw_logs: rehost decision",
                provider=provider,
                mono_s3=mono_s3,
                mono_s3_is_fagi=(
                    VapiRecordingService.is_fagi_s3_url(mono_s3) if mono_s3 else None
                ),
                processed_recording_url=processed.get("recording_url"),
                processed_recording_url_is_fagi=(
                    VapiRecordingService.is_fagi_s3_url(processed.get("recording_url"))
                    if processed.get("recording_url")
                    else None
                ),
                stereo_s3=stereo_s3,
                stereo_s3_is_fagi=(
                    VapiRecordingService.is_fagi_s3_url(stereo_s3) if stereo_s3 else None
                ),
                processed_stereo_url=processed.get("stereo_recording_url"),
                processed_stereo_url_is_fagi=(
                    VapiRecordingService.is_fagi_s3_url(processed.get("stereo_recording_url"))
                    if processed.get("stereo_recording_url")
                    else None
                ),
                fagi_buckets=list(VapiRecordingService._FAGI_S3_BUCKETS),
            )
            if mono_s3 and VapiRecordingService.is_fagi_s3_url(mono_s3) and not VapiRecordingService.is_fagi_s3_url(
                processed.get("recording_url")
            ):
                processed["recording_url"] = mono_s3
                logger.info("process_raw_logs: mono_s3 rehosted", mono_s3=mono_s3)
            if stereo_s3 and VapiRecordingService.is_fagi_s3_url(stereo_s3) and not VapiRecordingService.is_fagi_s3_url(
                processed.get("stereo_recording_url")
            ):
                processed["stereo_recording_url"] = stereo_s3
                logger.info("process_raw_logs: stereo_s3 rehosted", stereo_s3=stereo_s3)

        return processed

    @staticmethod
    def _process_eleven_labs_raw(raw_log: dict) -> dict:
        """ElevenLabs ConvAI conversation raw_log -> VoiceCallLogs dump."""
        metadata = raw_log.get("metadata") or {}
        started_at = None
        if start_unix := metadata.get("start_time_unix_secs"):
            started_at = datetime.fromtimestamp(
                start_unix, tz=timezone.utc
            ).isoformat()

        transcripts = [
            {
                "id": str(uuid.uuid4()),
                "role": msg.get("role"),
                "content": msg.get("message"),
                "time": (
                    str(msg["time_in_call_secs"])
                    if msg.get("time_in_call_secs") is not None
                    else None
                ),
                "duration": None,
            }
            for msg in (raw_log.get("transcript") or [])
            if isinstance(msg, dict) and msg.get("message")
        ]
        cost = metadata.get("cost")
        processed_log = {
            "id": None,
            "call_id": raw_log.get("conversation_id"),
            "phone_number": None,
            # Normalize ConvAI 'done'/'ended' to 'completed' to match other providers.
            "status": (
                "completed"
                if raw_log.get("status") in ("done", "ended")
                else raw_log.get("status")
            ),
            "started_at": started_at,
            "created_at": started_at,
            "duration_seconds": metadata.get("call_duration_secs"),
            "recording_url": None,
            "cost_cents": cost if cost is not None else None,
            "transcript": transcripts,
            "call_metadata": {"agent_id": raw_log.get("agent_id")},
        }
        return VoiceCallLogs(**processed_log).model_dump()

    @staticmethod
    def _process_bland_raw(raw_log: dict) -> dict:
        """Bland.ai call raw_log -> VoiceCallLogs dump (call_length in minutes)."""
        call_length = raw_log.get("call_length")
        duration_seconds = (
            int(round(float(call_length) * 60))
            if call_length not in (None, "")
            else None
        )
        transcripts = [
            {
                "id": str(uuid.uuid4()),
                "role": row.get("user"),
                "content": row.get("text"),
                "time": None,
                "duration": None,
            }
            for row in (raw_log.get("transcripts") or [])
            if isinstance(row, dict) and row.get("text")
        ]
        price = raw_log.get("price")
        processed_log = {
            "id": None,
            "call_id": raw_log.get("call_id"),
            "phone_number": raw_log.get("to"),
            "status": raw_log.get("status"),
            "started_at": raw_log.get("started_at") or raw_log.get("created_at"),
            # The list's date column binds created_at.
            "created_at": raw_log.get("created_at") or raw_log.get("started_at"),
            "duration_seconds": duration_seconds,
            "recording_url": raw_log.get("recording_url"),
            "cost_cents": float(price) * 100 if price not in (None, "") else None,
            "transcript": transcripts,
            "error_message": raw_log.get("error_message"),
            "call_metadata": {
                "from": raw_log.get("from"),
                "summary": raw_log.get("summary"),
            },
        }
        return VoiceCallLogs(**processed_log).model_dump()

    @staticmethod
    def _process_twilio_raw(raw_log: dict) -> dict:
        """Twilio Call resource raw_log -> VoiceCallLogs dump (no transcript)."""
        duration = raw_log.get("duration")
        price = raw_log.get("price")
        processed_log = {
            "id": None,
            "call_id": raw_log.get("sid"),
            "phone_number": raw_log.get("to"),
            "status": raw_log.get("status"),
            "started_at": raw_log.get("start_time"),
            # The list's date column binds created_at.
            "created_at": raw_log.get("start_time") or raw_log.get("date_created"),
            "duration_seconds": int(duration) if duration not in (None, "") else None,
            "recording_url": None,
            "cost_cents": abs(float(price)) * 100 if price not in (None, "") else None,
            "transcript": [],
            "call_metadata": {
                "from": raw_log.get("from"),
                "direction": raw_log.get("direction"),
            },
        }
        return VoiceCallLogs(**processed_log).model_dump()
