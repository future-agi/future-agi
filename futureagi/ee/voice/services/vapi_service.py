import asyncio
import gzip
import io
import json
import os
import threading
import time
import traceback
import uuid
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import requests  # type: ignore[import-not-found]
import structlog
from django.conf import settings  # type: ignore[import-not-found]
from django.core.cache import cache  # type: ignore[import-not-found]

from tfc.ee_stub import _ee_stub

try:
    from ee.agenthub.scenario_graph.graph_generator import (
        ConversationGraphGenerator,
    )
except ImportError:
    ConversationGraphGenerator = _ee_stub("ConversationGraphGenerator")
from ee.voice.exceptions import VapiApiError
from simulate.pydantic_schemas.chat import (
    ChatMessage,
    ChatRole,
    ChatSessionResponse,
    ChatSessionSendMessageResponse,
)
from ee.voice.semantics import FAGICallData, RecordingPayload
from simulate.semantics import (
    CallExecutionStatus,
    CallType,
    ToolCallingSupportedProviders,
)
from ee.voice.services.types.voice import (
    CallResult,
    CostBreakdown,
    CustomerMetrics,
    EndCallInput,
    FindClientCallInput,
    GetCallInput,
    InboundCallInput,
    NormalizedTranscriptData,
    OutboundCallInput,
    OutboundCallResult,
    PersistAudioInput,
    RecordingUrls,
    TranscriptMessage,
)
from ee.voice.services.voice_engine import VoiceServiceBlueprint
from tfc.utils.slack import send_critical_slack_notification
from tfc.utils.storage import download_audio_from_url, upload_audio_to_s3
from tracer.models.observability_provider import ProviderChoices

logger = structlog.get_logger(__name__)


def _update_recording_payload(
    provider_data: dict[str, Any], recording_urls: dict[str, str]
) -> None:
    recording = provider_data.get("recording")
    if not isinstance(recording, dict):
        recording = {}
        provider_data["recording"] = recording
    recording.update({key: url for key, url in recording_urls.items() if url})

    artifact = provider_data.get("artifact")
    if not isinstance(artifact, dict):
        return
    artifact_recording = artifact.get("recording")
    if not isinstance(artifact_recording, dict):
        return
    mono = artifact_recording.get("mono")
    artifact_keys = {"stereo": (artifact_recording, "stereoUrl")}
    if isinstance(mono, dict):
        artifact_keys.update(
            {
                "combined": (mono, "combinedUrl"),
                "customer": (mono, "customerUrl"),
                "assistant": (mono, "assistantUrl"),
            }
        )
    for key, url in recording_urls.items():
        target = artifact_keys.get(key)
        if target and url:
            target[0][target[1]] = url

# User-facing error messages for VAPI API HTTP status codes.
# 429 is deliberately excluded -- it is handled by _make_api_request_with_retry in the base class.
VAPI_STATUS_MESSAGES: dict[int, str] = {
    400: "Invalid request. Please check the configuration and try again.",
    401: "Authentication failed.",
    403: "Access denied.",
    404: "The requested resource was not found. It may have been deleted.",
    # 408: "The request to VAPI timed out. Please try again.",
    409: "A conflict occurred. The resource may already exist.",
    422: "The request data could not be processed. Please check the configuration.",
    500: "We are experiencing an internal error. Please try again later.",
    502: "We are experiencing a temporary outage. Please try again later.",
    503: "We are experiencing a temporary unavailability. Please try again later.",
}
_VAPI_DEFAULT_ERROR_MESSAGE = "An unexpected error occurred while communicating with VAPI. Please try again later."


def _extract_vapi_message_content(item: dict, role: str) -> str:
    """Extract display content from a Vapi artifact message."""
    if role == "tool_calls":
        calls = item.get("toolCalls") or []
        return ", ".join(tc.get("function", {}).get("name", "") for tc in calls)
    if role == "tool_call_result":
        return item.get("name", "")
    return item.get("message", "")


class VapiService(VoiceServiceBlueprint):
    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.getenv("VAPI_API_KEY")
        self.base_url = os.getenv("VAPI_API_BASE_URL")

        if not self.api_key:
            raise ValueError("VAPI_API_KEY environment variable is required")

        self.headers = {
            "authorization": f"Bearer {self.api_key}",
            "content-type": "application/json",
        }

    def _handle_error_response(
        self,
        response: requests.Response,
        action: str,
        use_provider_message: bool = False,
    ) -> None:
        """
        Centralized error handler for VAPI API responses.

        Logs the full response body for debugging, then raises VapiApiError.
        When *use_provider_message* is True (outbound calls), extracts the
        provider's own error message from the JSON body so it is baked into
        str(e) — custom exception attributes like response_body can be lost
        when the exception crosses sync_to_async / thread-pool boundaries.
        """
        response_text = response.text
        logger.error(
            "vapi_api_error",
            action=action,
            status_code=response.status_code,
            response_body=response_text,
        )
        provider_message = None
        if use_provider_message:
            try:
                data = response.json()
                msg = data.get("message")
                if isinstance(msg, str) and msg.strip():
                    provider_message = msg.strip()
            except Exception:
                pass

        if provider_message:
            user_message = provider_message
        else:
            user_message = VAPI_STATUS_MESSAGES.get(
                response.status_code, _VAPI_DEFAULT_ERROR_MESSAGE
            )
        raise VapiApiError(
            message=f"Failed to {action}: {user_message}",
            status_code=response.status_code,
            action=action,
            response_body=response_text,
        )

    def _resolve_background_sound(self, voice_settings: Dict[str, Any] | None) -> str:
        """
        Determine the backgroundSound value with validation.
        Falls back to 'office' for invalid/unreachable URLs, 'off' when explicitly disabled.
        """
        raw_bg = voice_settings.get("background_sound") if voice_settings else None
        logger.info(f"[BG] Raw background_sound from voice_settings: {raw_bg}")

        if isinstance(raw_bg, bool):
            return "office" if raw_bg else "off"

        if isinstance(raw_bg, str):
            candidate = raw_bg.strip()
            if candidate.lower() in ("off", "office"):
                return candidate.lower()
            if candidate.startswith(("http://", "https://")):
                if self._is_url_reachable(candidate):
                    return candidate
                logger.warning(
                    f"[BG] URL not reachable ({candidate}); defaulting to 'office'"
                )
                return "office"
            logger.warning(
                f"[BG] background_sound not URL/builtin ({candidate}); defaulting to 'office'"
            )
            return "office"

        if raw_bg:
            logger.warning(
                f"[BG] Unexpected background_sound type {type(raw_bg)}; defaulting to 'office'"
            )
        return "office"

    def _select_transcriber(self, normalized_language: str) -> dict[str, Any]:
        """Pick a VAPI transcriber whose STT model supports the language."""
        lang_lc = (normalized_language or "").lower()
        if lang_lc.split("-", 1)[0] == "ar":
            return {"provider": "azure", "language": "ar-SA"}
        if lang_lc.startswith("es"):
            return {"provider": "deepgram", "model": "nova-3", "language": "multi"}
        return {
            "provider": "deepgram",
            "model": "nova-3",
            "language": normalized_language,
        }

    def create_assistant(
        self,
        name: str,
        system_prompt: str,
        voice_settings: dict[str, Any] | None = None,
        background_sound: str | None = None,  # currently passed in voice_settings
        language: str = "en-US",
        assistant_type: str = "voice",  # "voice" or "chat"
    ) -> dict[str, Any]:
        """
        Create a new assistant with Vapi

        Args:
            name: Assistant name
            system_prompt: System prompt for the LLM
            voice_settings: Voice-specific settings (for voice assistants)
            background_sound: Background sound setting (for voice assistants)
            language: Language code (e.g., "en-US")
            assistant_type: Type of assistant - "voice" for voice calls, "chat" for text chat
            chat_config: Optional dict with chat-specific config:
                - clientMessages: dict with enabled, schema, etc.
                - serverMessages: dict with enabled, url, etc.
                - modelOutputInMessagesEnabled: bool
                - firstMessageMode: str ("assistant", "user", or "none")
        """
        default_customer_hooks = [
            {
                "on": "customer.speech.timeout",
                "name": "startup_nudge_once",
                "options": {
                    "timeoutSeconds": 5.0,  # this is the minimum value, less than 5 will raise an error
                    "triggerMaxCount": 1,
                    "triggerResetMode": "never",
                },
                "do": [{"type": "say", "exact": ["Hello?"]}],
            }
        ]
        try:
            assistant_type_lower = (assistant_type or "voice").lower()

            # LLM config (varies by assistant type)
            llm_provider = "openai"
            if assistant_type_lower == "chat":
                llm_model = "gpt-5.2-chat-latest"
                llm_temperature = 0.9
            else:
                llm_model = "gpt-4.1"
                llm_temperature = 0.2
            llm_max_tokens = 800

            # Handle max duration (common for both)
            max_duration = 15  # Default 15 seconds
            if (
                voice_settings
                and voice_settings.get("max_call_duration_in_minutes") is not None
            ):
                duration_minutes = voice_settings.get("max_call_duration_in_minutes")
                if isinstance(duration_minutes, (int, float)) and duration_minutes > 0:
                    max_duration = int(
                        duration_minutes * 60
                    )  # Convert minutes to seconds

            # Base assistant data structure
            assistant_data = {
                "name": name[:35],
                "model": {
                    "provider": llm_provider,
                    "model": llm_model,
                    "messages": [{"role": "system", "content": system_prompt}],
                    "temperature": llm_temperature,
                    "maxTokens": llm_max_tokens,
                },
                "maxDurationSeconds": max_duration,
                "analysisPlan": {
                    "successEvaluationPrompt": "Evaluate the call based on the customer satisfaction score. The score should be between 1 and 10. 1 is the lowest score and 10 is the highest score.",
                    "successEvaluationRubric": "NumericScale",
                },
            }

            # VOICE-SPECIFIC CONFIGURATION
            if assistant_type_lower == "voice":
                normalized_language = self._normalize_language_code(language)
                transcriber_config = self._select_transcriber(normalized_language)

                # VAPI uses 11Labs for TTS — never Cartesia.
                # If a Cartesia UUID arrives via voice_settings (e.g. from
                # a LiveKit-oriented voice selection), ignore it and use
                # the 11Labs default.
                import re

                req_voice_id = (
                    voice_settings.get("voice_id") if voice_settings else None
                )
                if req_voice_id and re.match(
                    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
                    req_voice_id,
                    re.IGNORECASE,
                ):
                    logger.warning(
                        "ignoring_cartesia_uuid_for_vapi",
                        voice_id=req_voice_id,
                    )
                    req_voice_id = None

                default_voice = {
                    "provider": "11labs",
                    "voiceId": req_voice_id or "marissa",
                    "speed": 1.0,
                    "stability": 0.5,
                    "similarityBoost": 0.75,
                    "model": "eleven_multilingual_v2",
                }

                # ==================== START SPEAKING PLAN ====================
                # Finished Speaking Sensitivity (1-10): how quickly persona interprets agent's pause as end of turn
                # Lower (1-3): Patient - waits longer before responding
                # Higher (8-10): Impatient - responds quickly
                finished_speaking_sensitivity = 5  # Default middle value
                if (
                    voice_settings
                    and voice_settings.get("finished_speaking_sensitivity") is not None
                ):
                    sensitivity = voice_settings.get("finished_speaking_sensitivity")
                    if isinstance(sensitivity, (int, float)):
                        finished_speaking_sensitivity = max(1, min(10, sensitivity))

                # Normalize to 0-1 scale where 0 = most patient, 1 = most impatient
                sensitivity_scale = (finished_speaking_sensitivity - 1) / 9.0

                # RANGES (for marking)
                WAIT_MAX, WAIT_MIN = 2, 0.1  # patient → impatient
                PUNC_MAX, PUNC_MIN = 1.2, 0.1  # patient → impatient
                NOPUNC_MAX, NOPUNC_MIN = 2.5, 0.1  # patient → impatient
                NUM_MAX, NUM_MIN = 1.2, 0.1  # patient → impatient

                ROUND_TO = 1
                # Linear mappings
                wait_seconds = round(
                    WAIT_MAX - sensitivity_scale * (WAIT_MAX - WAIT_MIN), ROUND_TO
                )
                on_punctuation_seconds = round(
                    PUNC_MAX - sensitivity_scale * (PUNC_MAX - PUNC_MIN), ROUND_TO
                )
                on_no_punctuation_seconds = round(
                    NOPUNC_MAX - sensitivity_scale * (NOPUNC_MAX - NOPUNC_MIN), ROUND_TO
                )
                on_number_seconds = round(
                    NUM_MAX - sensitivity_scale * (NUM_MAX - NUM_MIN), ROUND_TO
                )

                # Override waitSeconds if explicitly provided
                if (
                    voice_settings
                    and voice_settings.get("initial_message_delay") is not None
                ):
                    wait_seconds = float(
                        voice_settings.get("initial_message_delay", wait_seconds)
                    )

                start_speaking_plan = {
                    "waitSeconds": wait_seconds,
                    "transcriptionEndpointingPlan": {
                        "onPunctuationSeconds": on_punctuation_seconds,
                        "onNoPunctuationSeconds": on_no_punctuation_seconds,
                        "onNumberSeconds": on_number_seconds,
                    },
                }

                # ==================== STOP SPEAKING PLAN ====================
                # Interrupt Sensitivity (1-10): how sensitive persona is to interruptions from agent
                # Lower (1-3): NOT easy to interrupt - keeps talking, needs significant agent speech
                # Higher (8-10): Easy to interrupt - stops quickly with minimal agent speech
                interrupt_sensitivity = 5  # Default middle value
                if (
                    voice_settings
                    and voice_settings.get("interrupt_sensitivity") is not None
                ):
                    sensitivity = voice_settings.get("interrupt_sensitivity")
                    if isinstance(sensitivity, (int, float)):
                        interrupt_sensitivity = max(1, min(10, sensitivity))

                # Normalize to 0-1 scale where 0 = hard to interrupt, 1 = easy to interrupt
                interrupt_scale = (interrupt_sensitivity - 1) / 9.0

                # RANGES (for marking)
                VOICE_MAX, VOICE_MIN = 0.4, 0  # hard → easy to interrupt
                NUMWORDS_MAX, NUMWORDS_MIN = 5, 1  # hard → easy to interrupt
                BACKOFF_MAX, BACKOFF_MIN = 3, 0  # hard → easy to interrupt

                ROUND_TO = 1
                # Linear mappings
                voice_seconds = round(
                    VOICE_MAX - interrupt_scale * (VOICE_MAX - VOICE_MIN), ROUND_TO
                )
                num_words = int(
                    NUMWORDS_MAX - interrupt_scale * (NUMWORDS_MAX - NUMWORDS_MIN)
                )
                backoff_seconds = round(
                    BACKOFF_MAX - interrupt_scale * (BACKOFF_MAX - BACKOFF_MIN),
                    ROUND_TO,
                )

                stop_speaking_plan = {
                    "numWords": num_words,
                    "voiceSeconds": voice_seconds,
                    "backoffSeconds": backoff_seconds,
                }

                # Add voice-specific fields to assistant_data
                first_message_mode = (voice_settings or {}).get(
                    "first_message_mode"
                ) or "assistant-waits-for-user"
                first_message = (voice_settings or {}).get("initial_message", "")
                assistant_data.update(
                    {
                        "voice": default_voice,
                        "startSpeakingPlan": start_speaking_plan,
                        "stopSpeakingPlan": stop_speaking_plan,
                        "firstMessageMode": first_message_mode,
                        "firstMessage": first_message,
                        # NOTE: previously we did not send firstMessageMode for voice.
                        # Vapi defaulted to assistant-speaks-first with empty firstMessage,
                        # which behaves like assistant-waits-for-user. We now set this
                        # explicitly for customer assistants and allow overrides.
                        "hooks": default_customer_hooks,
                        "transcriber": transcriber_config,
                        "artifactPlan": {
                            "recordingEnabled": True,
                            "recordingFormat": "mp3",
                        },
                        "endCallPhrases": ["goodbye", "bye", "end call", "hang up"],
                        "backgroundSound": self._resolve_background_sound(
                            voice_settings
                        ),
                    }
                )

                # Voice-specific tools
                assistant_data["model"]["tools"] = [
                    {"type": "dtmf"},
                    {"type": "endCall"},
                ]

            # CHAT-SPECIFIC CONFIGURATION
            elif assistant_type_lower == "chat":
                initial_message = voice_settings.get("initial_message")
                if not initial_message or len(initial_message) == 0:
                    initial_message = "Hi!"
                assistant_data.update(
                    {
                        "firstMessageMode": "assistant-speaks-first",
                        "firstMessage": initial_message,
                    }
                )

                # Chat-specific tools (no DTMF, but keep endCall if needed)
                assistant_data["model"]["tools"] = [
                    {"type": "endCall"},  # Can end chat session
                ]
            else:
                raise ValueError(
                    f"Invalid assistant_type: {assistant_type}. Must be 'voice' or 'chat'"
                )

            # Use retry mechanism with custom parameters for create_assistant
            response = self._make_api_request_with_retry(
                requests.post,
                f"{self.base_url}/assistant",
                headers=self.headers,
                json=assistant_data,
                timeout=30,
            )

            if response.status_code == 201:
                result = response.json()
                logger.info(
                    f"Assistant created successfully ({assistant_type}): {result.get('id')}"
                )
                return result
            else:
                self._handle_error_response(response, "create assistant")

        except Exception as e:
            logger.exception(f"Error creating assistant ({assistant_type}): {str(e)}")
            raise

    def create_phone_call(
        self,
        phone_number_id: str,
        to_number: str,
        assistant_id: str | None = None,
        system_prompt: str | None = None,
        background_sound: str | None = None,
        metadata: dict[str, Any] | None = None,
        voice_settings: dict[str, Any] | None = None,
        workflow: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Create a new outbound phone call using Vapi

        Args:
            phone_number_id: The UUID of the phone number in Vapi (not the actual phone number)
            to_number: The destination phone number (will be formatted to E.164)
            assistant_id: Optional assistant ID to use for the call
            system_prompt: Optional system prompt if creating temporary assistant
            background_sound: Optional background sound
            metadata: Optional call metadata
            voice_settings: Optional voice settings
        """
        if metadata is None:
            metadata = {}
        try:
            voice_settings = voice_settings or {}
            # If no assistant_id provided, create a temporary assistant
            if not assistant_id and system_prompt:
                assistant_response = self.create_assistant(
                    name=f"Temp Assistant {to_number}",
                    system_prompt=system_prompt,
                    voice_settings=voice_settings,
                    background_sound=background_sound,
                    language=(
                        voice_settings.get("language", "en-US")
                        if voice_settings
                        else "en-US"
                    ),
                )
                assistant_id = assistant_response.get("id")
                if not assistant_id:
                    raise Exception("Failed to get assistant ID from creation response")
            elif not assistant_id:
                raise ValueError(
                    "Either assistant_id or system_prompt must be provided"
                )

            # Format the destination number to E.164 format
            formatted_to_number = self._format_phone_number_e164(to_number)

            # Prepare call data
            call_data = {
                "type": "outboundPhoneCall",
                "phoneNumberId": phone_number_id,  # This should be a UUID from Vapi
                "customer": {
                    "number": formatted_to_number  # Now properly formatted to E.164
                },
                "assistantId": assistant_id,
            }

            # # Add metadata
            # if metadata:
            #     call_data['metadata'] = metadata

            # Handle workflow parameter - parse if it's a string, use as-is if it's already an object/array
            if workflow:
                if isinstance(workflow, str):
                    try:
                        call_data["workflow"] = json.loads(workflow)
                    except json.JSONDecodeError as e:
                        logger.error(f"Failed to parse workflow JSON: {e}")
                        raise ValueError(f"Invalid workflow JSON format: {e}") from e
                else:
                    call_data["workflow"] = workflow

            row_data = metadata.get("row_data")
            if row_data:
                if isinstance(row_data, dict):
                    workflow = row_data.get("scenario_flow")
                    if workflow:
                        agent_def_id = metadata.get("agent_definition_id", "")
                        graph_generator = ConversationGraphGenerator(
                            agent_def_id
                            if isinstance(agent_def_id, str)
                            else str(agent_def_id)
                        )
                        extract_nodes_and_edges_from_detailed_path = (
                            graph_generator.extract_nodes_and_edges_from_detailed_path(
                                json.loads(workflow)
                            )
                        )
                        call_data["workflow"] = {
                            "name": metadata.get("scenario_name"),
                            "nodes": extract_nodes_and_edges_from_detailed_path.get(
                                "nodes"
                            ),
                            "edges": extract_nodes_and_edges_from_detailed_path.get(
                                "edges"
                            ),
                        }

            # Make the API call with retry mechanism
            response = self._make_api_request_with_retry(
                requests.post,
                f"{self.base_url}/call",
                headers=self.headers,
                json=call_data,
                timeout=30,
            )

            if response.status_code == 201:
                result = response.json()
                logger.info(f"Call created successfully: {result.get('id')}")
                return result
            else:
                self._handle_error_response(response, "create call")

        except Exception as e:
            logger.error(f"Error creating call: {str(e)}")
            raise

    def fetch_call(self, call_id: str) -> dict[str, Any]:
        """
        Fetch raw call data from Vapi API (unprocessed).
        """
        try:
            response = self._make_api_request_with_retry(
                requests.get,
                f"{self.base_url}/call/{call_id}",
                headers=self.headers,
                timeout=30,
            )

            if response.status_code == 200:
                return response.json()
            else:
                self._handle_error_response(response, "get call")

        except Exception as e:
            logger.error(f"Error getting call: {str(e)}")
            raise

    def list_calls(
        self,
        limit: int = 20,
        offset: int = 0,
        assistant_id: Optional[str] = None,
        call_type: Optional[str] = None,
        created_at_gt: Optional[str] = None,
        created_at_lt: Optional[str] = None,
        created_at_ge: Optional[str] = None,
        created_at_le: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        List calls from Vapi with optional filtering

        Args:
            limit: Maximum number of calls to return
            offset: Offset for pagination (not supported by VAPI)
            assistant_id: Filter by assistant ID
            call_type: Filter by call type (e.g., 'inboundPhoneCall', 'outboundPhoneCall')
                      Note: VAPI API doesn't support filtering by call type. This parameter is
                      kept for API compatibility but won't filter API results.
            created_at_gt: Filter calls created after this datetime (greater than)
            created_at_lt: Filter calls created before this datetime (less than)
            created_at_ge: Filter calls created at or after this datetime (greater than or equal)
            created_at_le: Filter calls created at or before this datetime (less than or equal)
        """
        try:
            params = {"limit": limit}

            # Add optional filters
            if assistant_id:
                params["assistantId"] = assistant_id

            # Note: VAPI API doesn't support filtering by call type in list_calls endpoint
            # The call_type parameter is kept for API compatibility but not used in the request
            # if call_type:
            #     params['type'] = call_type

            if created_at_gt:
                params["createdAtGt"] = created_at_gt

            if created_at_lt:
                params["createdAtLt"] = created_at_lt

            if created_at_ge:
                params["createdAtGe"] = created_at_ge

            if created_at_le:
                params["createdAtLe"] = created_at_le

            # VAPI doesn't support offset parameter
            if offset > 0:
                logger.warning(
                    f"VAPI doesn't support offset parameter, ignoring offset={offset}"
                )

            response = self._make_api_request_with_retry(
                requests.get,
                f"{self.base_url}/call",
                headers=self.headers,
                params=params,
                timeout=30,
            )

            if response.status_code == 200:
                return response.json()
            else:
                self._handle_error_response(response, "list calls")

        except Exception as e:
            logger.error(f"Error listing calls: {str(e)}")
            raise

    def update_call(
        self,
        call_id: str,
        metadata: dict[str, Any] | None = None,
        status: str | None = None,
    ) -> dict[str, Any]:
        """
        Update call metadata or status
        """
        try:
            payload: dict[str, Any] = {}
            if metadata:
                payload["metadata"] = metadata
            if status:
                payload["status"] = status

            response = self._make_api_request_with_retry(
                requests.patch,
                f"{self.base_url}/call/{call_id}",
                headers=self.headers,
                json=payload,
                timeout=30,
            )

            if response.status_code == 200:
                return response.json()
            else:
                self._handle_error_response(response, "update call")

        except Exception as e:
            logger.error(f"Error updating call: {str(e)}")
            raise

    def delete_call(self, controlUrl: str) -> bool:
        """
        Cancel/end a call - runs in a separate thread to avoid blocking
        """

        def _end_call():
            try:
                # Vapi uses POST to websocket control endpoint to end calls
                # controlUrl already contains the full path with call_id and /control
                logger.info(f"Attempting to end call via {controlUrl}")

                response = requests.post(
                    controlUrl,
                    headers={"content-type": "application/json"},
                    json={"type": "end-call"},
                    timeout=10,  # Reduced timeout for websocket endpoints
                )

                logger.info(
                    f"Response status: {response.status_code}, text: {response.text}"
                )

                if response.status_code == 200:
                    logger.info("Call ended successfully")
                else:
                    logger.error(
                        f"Failed to end call: {response.status_code} - {response.text}"
                    )

            except requests.exceptions.Timeout:
                logger.warning(
                    "Timeout when ending call - this may be normal for websocket endpoints"
                )
                # For websocket endpoints, timeout might be expected behavior
                # The call might still be ended even if we don't get a response
                logger.info("Call ended (timeout treated as success)")
            except Exception as e:
                logger.error(f"Error ending call: {str(e)}")

        # Start the call ending in a separate thread
        thread = threading.Thread(target=_end_call, daemon=True)
        thread.start()

        # Return immediately to avoid blocking the main thread
        logger.info(f"Call ending initiated in background thread for {controlUrl}")
        return True

    def get_call_recording(self, call_id: str) -> dict[str, Any]:
        """
        Get call recording URL from Vapi
        """
        try:
            # Get call details which include recording information
            call_data = self.fetch_call(call_id)

            # Vapi stores recording info in artifact object
            artifact = call_data.get("artifact", {})
            recording = artifact.get("recording", {})

            return recording

        except Exception as e:
            logger.error(f"Error getting recording: {str(e)}")
            raise

    def get_call_transcript(
        self, call_data: dict[str, Any], call_type: CallType = CallType.INBOUND
    ) -> dict[str, Any]:
        """
        Format call transcript from Vapi according to inbound and outbound
        """
        try:
            # Vapi stores transcript info in artifact object
            call_id = call_data.get("id")
            artifact = call_data.get("artifact", {})
            messages = artifact.get("messages", [])
            duration = 0

            # Format transcript for consistency
            formatted_transcript = []
            for index, item in enumerate(messages):
                role = item.get("role", "tool")

                if index == len(messages) - 1:
                    duration = item.get("secondsFromStart")

                # NOTE: Role normalisation for inbound calls is handled in
                # fetch_and_store_call_data() where call_metadata is available
                # to determine the simulation direction (not VAPI's call type).

                content = _extract_vapi_message_content(item, role)

                formatted_item = {
                    "speaker_role": role,  # 'user', 'assistant', 'tool_calls', etc.
                    "content": content,
                    "start_time_ms": int(item.get("time", 0)),
                    "end_time_ms": int(item.get("endTime", 0)),
                    "confidence_score": 1.0,  # Vapi doesn't provide confidence scores
                    "created_at": datetime.utcnow().isoformat(),
                }
                formatted_transcript.append(formatted_item)

            return {
                "call_id": call_id,
                "duration": duration,
                "transcripts": formatted_transcript,
                "status": "available" if formatted_transcript else "not_available",
            }

        except Exception as e:
            logger.error(f"Error getting transcript: {str(e)}")
            raise

    def list_phone_numbers(self) -> dict[str, Any]:
        """
        List all phone numbers registered with Vapi.

        Uses limit=1000 to avoid the default 100-result cap that causes
        phone number lookups to fail for accounts with >100 numbers.
        """
        try:
            response = self._make_api_request_with_retry(
                requests.get,
                f"{self.base_url}/phone-number",
                headers=self.headers,
                params={"limit": 200},
                timeout=30,
            )

            if response.status_code == 200:
                result = response.json()
                logger.info(f"Retrieved {len(result)} phone numbers")
                return result
            else:
                self._handle_error_response(response, "list phone numbers")

        except Exception as e:
            logger.error(f"Error listing phone numbers: {str(e)}")
            raise

    def get_phone_number(self, phone_number_id: str) -> dict[str, Any]:
        """
        Get details of a specific phone number by ID
        """
        try:
            response = self._make_api_request_with_retry(
                requests.get,
                f"{self.base_url}/phone-number/{phone_number_id}",
                headers=self.headers,
                timeout=30,
            )

            if response.status_code == 200:
                return response.json()
            else:
                self._handle_error_response(response, "get phone number")

        except Exception as e:
            logger.error(f"Error getting phone number: {str(e)}")
            raise

    def create_phone_number(
        self, number: str, provider: str = "twilio"
    ) -> dict[str, Any]:
        """
        Create/import a phone number in Vapi
        """
        try:
            phone_data = {
                "provider": provider,
                "number": number,
                "name": f"Phone Number {number}",
            }

            response = self._make_api_request_with_retry(
                requests.post,
                f"{self.base_url}/phone-number",
                headers=self.headers,
                json=phone_data,
                timeout=30,
            )

            if response.status_code == 201:
                result = response.json()
                logger.info(f"Phone number created successfully: {result.get('id')}")
                return result
            else:
                self._handle_error_response(response, "create phone number")

        except Exception as e:
            logger.error(f"Error creating phone number: {str(e)}")
            raise

    def list_assistants(self, limit: int = 100) -> dict[str, Any]:
        """
        List all assistants
        """
        try:
            params = {"limit": limit}

            response = self._make_api_request_with_retry(
                requests.get,
                f"{self.base_url}/assistant",
                headers=self.headers,
                params=params,
                timeout=30,
            )

            if response.status_code == 200:
                return response.json()
            else:
                self._handle_error_response(response, "list assistants")

        except Exception as e:
            logger.error(f"Error listing assistants: {str(e)}")
            raise

    def get_assistant(self, assistant_id: str) -> dict[str, Any]:
        """
        Get assistant details
        """
        try:
            response = self._make_api_request_with_retry(
                requests.get,
                f"{self.base_url}/assistant/{assistant_id}",
                headers=self.headers,
                timeout=30,
            )

            if response.status_code == 200:
                return response.json()
            else:
                self._handle_error_response(response, "get assistant")

        except Exception as e:
            logger.error(f"Error getting assistant: {str(e)}")
            raise

    def update_assistant(self, assistant_id: str, **kwargs) -> dict[str, Any]:
        """
        Update assistant configuration
        """
        try:
            response = requests.patch(
                f"{self.base_url}/assistant/{assistant_id}",
                headers=self.headers,
                json=kwargs,
                timeout=30,
            )

            if response.status_code == 200:
                return response.json()
            else:
                self._handle_error_response(response, "update assistant")

        except Exception as e:
            logger.error(f"Error updating assistant: {str(e)}")
            raise

    def delete_assistant(self, assistant_id: str) -> bool:
        """
        Delete an assistant
        """
        try:
            response = requests.delete(
                f"{self.base_url}/assistant/{assistant_id}",
                headers=self.headers,
                timeout=30,
            )

            if response.status_code == 200:
                logger.info(f"Assistant deleted successfully: {assistant_id}")
                return True
            else:
                logger.error(
                    "vapi_api_error",
                    action="delete assistant",
                    status_code=response.status_code,
                    response_body=response.text,
                )
                return False

        except Exception as e:
            logger.error(f"Error deleting assistant: {str(e)}")
            return False

    def validate_api_key(self) -> bool:
        """
        Validate the API key by making a simple request
        """
        try:
            response = requests.get(
                f"{self.base_url}/call",
                headers=self.headers,
                params={"limit": 1},
                timeout=10,
            )
            return response.status_code == 200
        except Exception:
            return False

    def get_call_status_batch(self, call_ids: list[str]) -> dict[str, dict[str, Any]]:
        """
        Get status for multiple calls efficiently
        """
        try:
            results = {}

            # Vapi doesn't have a batch endpoint, so we'll make individual requests
            # In production, you might want to implement rate limiting here
            for call_id in call_ids:
                try:
                    call_data = self.fetch_call(call_id)
                    results[call_id] = {
                        "status": call_data.get("status"),
                        "duration": call_data.get("duration"),
                        "endedReason": call_data.get("endedReason"),
                        "recordingUrl": call_data.get("artifact", {})
                        .get("recording", {})
                        .get("stereoUrl"),
                        "cost": call_data.get("cost"),
                        "success": True,
                    }
                except Exception as e:
                    logger.warning(f"Failed to get status for call {call_id}: {e}")
                    results[call_id] = {"success": False, "error": str(e)}

            return results

        except Exception as e:
            logger.error(f"Error in batch status check: {e}")
            return {}

    def is_call_active(self, call_id: str) -> bool:
        """
        Quick check if a call is still active/ongoing
        """
        try:
            call_data = self.fetch_call(call_id)
            status = call_data.get("status", "").lower()
            return status in ["queued", "ringing", "in-progress", "forwarding"]
        except Exception:
            return False

    def get_phone_number_id_by_number(self, phone_number: str) -> Optional[str]:
        """
        Find phone number ID in VAPI account by phone number
        Uses cache to avoid repeated API calls for the same phone number.

        Args:
            phone_number: Phone number in E.164 format (e.g., +15551234567)

        Returns:
            Phone number UUID or None if not found
        """
        try:
            # Format phone number to E.164
            formatted_number = self._format_phone_number_e164(phone_number)

            # Check cache first
            cache_key = f"vapi:phone_id:{formatted_number}"
            cached_phone_id = cache.get(cache_key)

            if cached_phone_id:
                return cached_phone_id

            # Cache miss - fetch from VAPI
            phone_numbers = self.list_phone_numbers()

            phone_id = None
            for phone in phone_numbers:
                if phone.get("number") == formatted_number:
                    phone_id = phone.get("id")
                    break

            if phone_id:
                cache.set(cache_key, phone_id, timeout=3600)
                return phone_id
            else:
                return None

        except Exception as e:
            logger.exception(
                f"Error finding phone number ID for {phone_number}: {str(e)}"
            )
            return None

    def assign_assistant_to_phone(
        self, phone_number_id: str, assistant_id: str
    ) -> Dict[str, Any]:
        """
        Assign an assistant to a phone number in VAPI

        Args:
            phone_number_id: VAPI phone number ID
            assistant_id: VAPI assistant ID to assign

        Returns:
            Updated phone number data
        """
        try:
            response = self._make_api_request_with_retry(
                requests.patch,
                f"{self.base_url}/phone-number/{phone_number_id}",
                headers=self.headers,
                json={"assistantId": assistant_id},
                timeout=30,
            )

            if response.status_code == 200:
                result = response.json()
                logger.info(
                    f"Assigned assistant {assistant_id} to phone number {phone_number_id}"
                )
                return result
            else:
                self._handle_error_response(
                    response, "assign assistant to phone number"
                )

        except Exception as e:
            logger.error(f"Error assigning assistant to phone: {str(e)}")
            raise

    def create_outbound_call(
        self,
        assistant_id: str,
        from_phone_number: str,
        to_phone_number: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Create an outbound call where the assistant calls the customer
        Used for outbound simulation where user's agent calls simulation customer

        Args:
            assistant_id: The assistant ID that will make the call (user's agent)
            from_phone_number: Phone number to call from in E.164 format (will look up ID)
            to_phone_number: Destination phone number in E.164 format (simulation phone)
            metadata: Optional call metadata

        Returns:
            Call data from VAPI
        """
        try:
            # Format the destination number to E.164 format
            formatted_to_number = self._format_phone_number_e164(to_phone_number)

            # Look up phone number ID from user's VAPI account
            from_phone_number_id = self.get_phone_number_id_by_number(from_phone_number)

            if not from_phone_number_id:
                raise Exception(
                    f"Phone number {from_phone_number} not found in user's VAPI account. "
                    f"Please add this phone number to the user's VAPI account first."
                )

            # Prepare call data
            call_data = {
                "type": "outboundPhoneCall",
                "assistantId": assistant_id,
                "phoneNumberId": from_phone_number_id,  # Now using UUID from lookup
                "customer": {"number": formatted_to_number},
                "assistantOverrides": {
                    "analysisPlan": {
                        "successEvaluationPlan": {"rubric": "NumericScale"}
                    }
                },
            }

            # Add metadata if provided
            if metadata:
                call_data["metadata"] = metadata
            # Make the API call with retry mechanism
            response = self._make_api_request_with_retry(
                requests.post,
                f"{self.base_url}/call",
                headers=self.headers,
                json=call_data,
                timeout=30,
            )

            if response.status_code == 201:
                result = response.json()
                logger.info(f"Outbound call created successfully: {result.get('id')}")
                return result
            else:
                self._handle_error_response(
                    response, "create outbound call", use_provider_message=True
                )

        except Exception as e:
            logger.error(f"Error creating outbound call: {str(e)}")
            raise

    def iter_call_log_entries(
        self,
        url: str,
        verify_ssl: bool = False,
        timeout: int = 60,
        *,
        call_id: str | None = None,
        api_key: str | None = None,
    ) -> Iterable[Dict[str, Any]]:
        """Yield parsed log entries from a call's gzipped JSONL log file."""
        from tracer.utils.vapi_recording import VapiRecordingService

        logger.debug("Fetching call logs from VAPI")

        return VapiRecordingService.iter_parsed_call_log_records(
            call_id=call_id,
            api_key=api_key,
            legacy_url=url,
            timeout_seconds=timeout,
            verify_ssl=verify_ssl,
        )

    def download_call_logs(
        self,
        url: str,
        output_dir: Path | str | None = None,
        filename: str = "calllogs.jsonl",
        verify_ssl: bool = False,
        *,
        call_id: str | None = None,
        api_key: str | None = None,
    ) -> Dict[str, Any]:
        """
        Download gzipped call logs from the given URL, decompress the JSONL content,
        and persist it to disk.

        Args:
            url: Direct download URL for the gzipped JSONL file.
            output_dir: Destination directory path or string; defaults to BASE_DIR/tmp.
            filename: Name for the decompressed JSONL file.
            verify_ssl: Whether to enforce TLS certificate verification.

        Returns:
            Dict containing output path, record count, and a preview of the first record.
        """
        output_directory = (
            Path(output_dir)
            if output_dir is not None
            else Path(getattr(settings, "BASE_DIR", Path.cwd())) / "tmp"
        )
        output_directory.mkdir(parents=True, exist_ok=True)
        output_path = output_directory / filename

        logger.info("Downloading call logs from VAPI")

        record_count = 0
        first_record: Dict[str, Any] | str | None = None

        with output_path.open("w", encoding="utf-8") as file_handle:
            for record in self.iter_call_log_entries(
                url,
                verify_ssl=verify_ssl,
                call_id=call_id,
                api_key=api_key,
            ):
                file_handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                record_count += 1
                if first_record is None:
                    first_record = record

        logger.info("Call log download complete")

        return {
            "output_path": str(output_path),
            "records": record_count,
            "first_record": first_record,
        }

    def create_chat_session(
        self,
        assistant_id: str,
        name: str,
        initial_message: Optional[ChatMessage] = None,
    ) -> ChatSessionResponse | None:
        """
        Create a chat session
        """
        try:
            messages = []
            if initial_message and len(initial_message.content) > 0:
                # Convert Pydantic model to dict for JSON serialization
                messages.append(initial_message.model_dump(exclude_none=True))
            else:
                # Convert Pydantic model to dict for JSON serialization
                default_message = ChatMessage(role=ChatRole.ASSISTANT, content="Hi!")
                messages.append(default_message.model_dump(exclude_none=True))

            response = self._make_api_request_with_retry(
                requests.post,
                f"{self.base_url}/session",
                headers=self.headers,
                json={"assistantId": assistant_id, "name": name, "messages": messages},
                timeout=30,
            )

            if response.status_code == 201:
                result = response.json()
                session_id = result.get("id")

                messages = result.get("messages")
                messages = [ChatMessage(**msg) for msg in messages]

                if not session_id:
                    raise Exception("Failed to get session ID from creation response")
                logger.info(f"Chat session created successfully: {session_id}")
                return ChatSessionResponse(
                    id=session_id,
                    name=result.get("name"),
                    status=result.get("status"),
                    assistant_id=result.get("assistantId"),
                    messages=messages,
                )
            else:
                self._handle_error_response(response, "create chat session")

        except Exception as e:
            logger.exception(f"Error creating chat session: {str(e)}")
            raise

    def get_chat_session(
        self,
        chat_session_id: str,
    ) -> ChatSessionResponse | None:
        """
        Get a chat session
        """
        try:
            response = self._make_api_request_with_retry(
                requests.get,
                f"{self.base_url}/session/{chat_session_id}",
                headers=self.headers,
                timeout=30,
            )

            if response.status_code == 200:
                result = response.json()
                messages = result.get("messages")
                messages = [ChatMessage(**msg) for msg in messages]
                logger.info(f"Chat session retrieved successfully: {chat_session_id}")
                return ChatSessionResponse(
                    id=result.get("id"),
                    name=result.get("name"),
                    status=result.get("status"),
                    assistant_id=result.get("assistantId"),
                    messages=messages,
                )
            else:
                logger.error(
                    "vapi_api_error",
                    action="get chat session",
                    status_code=response.status_code,
                    response_body=response.text,
                )
                return None

        except Exception as e:
            logger.exception(f"Error getting chat session: {str(e)}")
            return None

    def send_message_to_chat(
        self,
        chat_session_id: str,
        messages: List[ChatMessage],
    ) -> ChatSessionSendMessageResponse:
        """
        Send a message to a chat session
        """
        try:
            # Convert Pydantic models to dicts for JSON serialization
            messages_dict = self.parse_user_message_for_vapi(messages)
            if not messages_dict or len(messages_dict) == 0:
                raise Exception("No user messages to send to VAPI")

            response = self._make_api_request_with_retry(
                requests.post,
                f"{self.base_url}/chat/",
                headers=self.headers,
                json={"input": messages_dict, "sessionId": chat_session_id},
                timeout=30,
            )

            if response.status_code == 200 or response.status_code == 201:
                result = response.json()
                logger.info(
                    f"Message sent to chat session successfully: {result.get('id')}"
                )

                output_messages = result.get("output")
                output_messages = [ChatMessage(**msg) for msg in output_messages]

                # Filter out output_messages with content 'STOP' and set has_chat_ended if any were present
                has_chat_ended = any(
                    (
                        msg.role == ChatRole.ASSISTANT
                        and msg.tool_calls
                        and any(tc.function.name == "endCall" for tc in msg.tool_calls)
                    )
                    for msg in output_messages
                )

                return ChatSessionSendMessageResponse(
                    input=messages,
                    output=output_messages,
                    id=result.get("id"),
                    session_id=result.get("sessionId"),
                    has_chat_ended=has_chat_ended,
                )
            else:
                self._handle_error_response(response, "send message to chat session")

        except Exception as e:
            logger.exception(f"Error sending message to chat session: {str(e)}")
            raise Exception(f"Error sending message to chat session: {str(e)}")

    def parse_user_message_for_vapi(
        self, user_messages: List[ChatMessage]
    ) -> List[Dict]:
        """
        Parse a user message for VAPI
        """
        try:
            response = []

            for message in user_messages:
                if message.role == ChatRole.USER and message.content is not None:
                    response.append(
                        {
                            "role": "user",
                            "content": message.content,
                        }
                    )

            return response

        except Exception as e:
            logger.exception(f"Error parsing user message for VAPI: {str(e)}")
            raise Exception(f"Error parsing user message for VAPI: {str(e)}")

    # =========================================================================
    # High-level engine contract (VoiceServiceBlueprint implementations)
    # =========================================================================

    # -- Call lifecycle --------------------------------------------------------

    def get_call(self, input: GetCallInput) -> FAGICallData:
        """Fetch call from VAPI and return normalized FAGICallData."""
        call_data = self.fetch_call(call_id=input.call_id)
        return self._normalize_to_fagi_call_data(
            call_data=call_data, call_data_stored=input.call_data_stored
        )

    def normalize_call_data(
        self, raw_data: dict[str, Any], call_data_stored: bool
    ) -> FAGICallData:
        """Normalize already-fetched raw VAPI data to FAGICallData."""
        return self._normalize_to_fagi_call_data(
            call_data=raw_data, call_data_stored=call_data_stored
        )

    async def get_call_async(self, input: GetCallInput) -> FAGICallData:
        """Async version of get_call using run_in_executor."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, lambda: self.get_call(input))

    def initiate_inbound_call(self, input: InboundCallInput) -> CallResult:
        """Create simulator assistant + call user's agent (inbound for user)."""
        try:
            # 1. Create simulator assistant
            assistant_name = f"simulator-inbound-{input.call_id[:8]}"
            voice_settings = input.voice_settings or {}
            assistant_response = self.create_assistant(
                name=assistant_name,
                system_prompt=input.system_prompt,
                voice_settings=voice_settings,
                background_sound=voice_settings.get("background_sound"),
                language=voice_settings.get("language", "en-US"),
            )
            assistant_id = assistant_response.get("id")
            if not assistant_id:
                return CallResult(
                    success=False, error="No assistant ID returned from VAPI"
                )

            # 2. Call user's phone
            phone_number_id = os.getenv("VAPI_PHONE_NUMBER_ID")
            if not phone_number_id:
                return CallResult(
                    success=False,
                    error="VAPI_PHONE_NUMBER_ID not set in environment",
                )

            logger.info(
                "initiating_inbound_call",
                user_phone=input.user_phone_number,
                assistant_id=assistant_id,
            )

            response = self.create_phone_call(
                phone_number_id=phone_number_id,
                to_number=input.user_phone_number,
                assistant_id=assistant_id,
                voice_settings=voice_settings,
                metadata=input.metadata or {},
            )

            provider_call_id = response.get("id")
            if not provider_call_id:
                return CallResult(success=False, error="No call ID returned from VAPI")

            logger.info(
                "inbound_call_initiated",
                provider_call_id=provider_call_id,
            )

            return CallResult(
                success=True,
                provider_call_id=provider_call_id,
                assistant_id=assistant_id,
                provider_data=response,
            )

        except Exception as e:
            logger.exception(f"Failed to initiate inbound call: {e}")
            return CallResult(success=False, error=str(e))

    def initiate_outbound_call(self, input: OutboundCallInput) -> OutboundCallResult:
        """Create simulator assistant + assign to phone for outbound calls.

        Phone is already acquired by the workflow and passed via input.
        """
        try:
            # 1. Create simulator assistant
            voice_settings = input.voice_settings or {}
            assistant_name = f"simulator-outbound-{input.call_execution_id[:8]}"
            assistant_response = self.create_assistant(
                name=assistant_name,
                system_prompt=input.system_prompt,
                voice_settings=voice_settings,
                background_sound=voice_settings.get("background_sound"),
                language=voice_settings.get("language", "en-US"),
                assistant_type="voice",
            )
            assistant_id = assistant_response.get("id")
            if not assistant_id:
                return OutboundCallResult(
                    success=False, error="No assistant ID returned from VAPI"
                )

            # 2. Assign assistant to phone
            self.assign_assistant_to_phone(
                phone_number_id=input.provider_phone_id,
                assistant_id=assistant_id,
            )

            logger.info(
                "outbound_call_setup_complete",
                assistant_id=assistant_id,
                phone_number=input.phone_number,
            )

            return OutboundCallResult(
                success=True,
                assistant_id=assistant_id,
                phone_number_id=input.provider_phone_id,
                phone_number=input.phone_number,
                provider_data=assistant_response,
            )

        except Exception as e:
            logger.exception("outbound_call_setup_failed")
            return OutboundCallResult(success=False, error=str(e))

    def end_call(self, input: EndCallInput) -> bool:
        """Terminate an active VAPI call via its controlUrl."""
        payload = input.provider_call_payload
        if not payload:
            raise ValueError("Missing provider_call_payload to end VAPI call")

        monitor = payload.get("monitor") or {}
        control_url = monitor.get("controlUrl")
        if not isinstance(control_url, str) or not control_url:
            raise ValueError("Missing controlUrl in provider_call_payload")

        return bool(self.delete_call(control_url))

    # -- Data extraction ------------------------------------------------------

    def get_recording_urls(self, payload: dict[str, Any] | None) -> RecordingPayload:
        """Extract recording URLs from VAPI call data payload."""
        if not payload:
            return {}
        return self._extract_recording_urls(payload)

    def persist_audio_to_s3(self, input: PersistAudioInput) -> str:
        """Download VAPI audio and re-upload to S3. Returns S3 URL."""
        audio_url = input.audio_url
        if not audio_url:
            return audio_url

        # Check URL hostname to determine if conversion is needed
        from urllib.parse import urlparse

        parsed = urlparse(str(audio_url))
        hostname = parsed.hostname or ""

        # Already on S3
        if hostname.endswith(".amazonaws.com"):
            logger.info(f"{input.url_type} URL is already S3: {audio_url}")
            return audio_url

        # Not a VAPI URL
        if not hostname.endswith("vapi.ai"):
            logger.info(f"{input.url_type} URL is not a Vapi URL: {audio_url}")
            return audio_url

        try:
            logger.info(f"Converting {input.url_type} URL from Vapi to S3: {audio_url}")

            audio_bytes = None
            try:
                from tracer.utils.vapi_recording import VapiRecordingService

                artifact_type = VapiRecordingService.artifact_for_url_type(
                    input.url_type
                )
                if artifact_type and input.call_id and self.api_key:
                    audio_bytes = VapiRecordingService.download_artifact_sync(
                        call_id=str(input.call_id),
                        artifact_type=artifact_type,
                        api_key=self.api_key,
                    )
            except Exception:
                audio_bytes = None

            if audio_bytes is None:
                audio_bytes = download_audio_from_url(audio_url)
            file_extension = "mp3"

            object_key = (
                f"call-recordings/{input.call_id}/{uuid.uuid4()}.{file_extension}"
            )
            audio_data = {"bytes": audio_bytes}
            s3_url = upload_audio_to_s3(audio_data, object_key=object_key)

            logger.info(f"Successfully converted {input.url_type} URL to S3: {s3_url}")
            return s3_url

        except Exception as e:
            logger.error(f"Error converting {input.url_type} URL to S3: {str(e)}")
            traceback.print_exc()
            return audio_url

    # -- Client call matching -------------------------------------------------

    def find_client_call(self, input: FindClientCallInput) -> str | None:
        """Find matching call in customer's VAPI account."""
        try:
            if input.customer_voice_service_provider and (
                input.customer_voice_service_provider
                not in [p.value for p in ToolCallingSupportedProviders]
            ):
                return None

            return self._find_customer_vapi_call_id(
                customer_api_key=input.customer_api_key,
                customer_assistant_id=input.customer_assistant_id,
                our_call_data=input.our_call_data,
                time_window_seconds=input.time_window_seconds,
            )

        except Exception as e:
            logger.error(f"Error finding customer VAPI call ID: {str(e)}")
            traceback.print_exc()
            return None

    # -- Metrics --------------------------------------------------------------

    def get_customer_metrics(self, call_data: FAGICallData) -> CustomerMetrics:
        """Normalize VAPI customer metrics/costs to FAGI convention."""
        try:
            normalized_metrics = self._build_customer_system_metrics(
                call_data.performance_metrics.get(ProviderChoices.VAPI.value)
            )
            raw_payload = call_data.raw_log.get(ProviderChoices.VAPI.value, {})
            cost_breakdown, total_cost = self._build_customer_cost_breakdown(
                raw_payload.get("costs", []) if isinstance(raw_payload, dict) else []
            )
            return CustomerMetrics(
                system_metrics=normalized_metrics,
                cost_breakdown=cost_breakdown,
                total_cost=total_cost,
            )
        except Exception as e:
            logger.exception(f"Error building customer metrics: {e}")
            return CustomerMetrics()

    def build_customer_metrics_from_provider_data(
        self, provider_data: dict[str, Any]
    ) -> CustomerMetrics:
        """Build CustomerMetrics from stored provider_call_data['vapi']."""
        perf = (provider_data.get("artifact") or {}).get("performanceMetrics")
        system_metrics = self._build_customer_system_metrics(perf)
        cost_breakdown, total_cost = self._build_customer_cost_breakdown(
            provider_data.get("costs", [])
        )
        return CustomerMetrics(
            system_metrics=system_metrics,
            cost_breakdown=cost_breakdown,
            total_cost=total_cost,
        )

    # -- Logs -----------------------------------------------------------------

    def iter_call_logs(
        self,
        url: str,
        verify_ssl: bool,
        *,
        call_id: str | None = None,
        api_key: str | None = None,
        **kwargs: Any,
    ) -> Iterable[dict]:
        """Yield parsed log entries from VAPI log URL."""
        del kwargs
        return self.iter_call_log_entries(
            url=url,
            verify_ssl=verify_ssl,
            call_id=call_id,
            api_key=api_key,
        )

    # =========================================================================
    # Private helpers (moved from VoiceServiceManager)
    # =========================================================================

    def _normalize_to_fagi_call_data(
        self, call_data: dict[str, Any], call_data_stored: bool
    ) -> FAGICallData:
        """Normalize raw VAPI call data to FAGICallData."""
        try:
            raw_status = str(call_data.get("status") or "").lower()
            raw_type = str(call_data.get("type") or "")

            if "outbound" in raw_type.lower():
                call_type = CallType.OUTBOUND
            elif "web" in raw_type.lower():
                call_type = CallType.WEB_CALL
            else:
                call_type = CallType.INBOUND

            if raw_status in {"ended", "completed"}:
                status = (
                    CallExecutionStatus.COMPLETED
                    if call_data_stored
                    else CallExecutionStatus.ANALYZING
                )
            elif raw_status in {"failed", "error"}:
                status = CallExecutionStatus.FAILED
            elif raw_status in {"cancelled", "canceled"}:
                status = CallExecutionStatus.CANCELLED
            elif raw_status in {"ongoing", "in-progress"}:
                status = CallExecutionStatus.ONGOING
            elif raw_status in {"registered", "queued", "ringing", "scheduled"}:
                status = CallExecutionStatus.REGISTERED
            else:
                status = CallExecutionStatus.PENDING

            transcript_resp = self.get_call_transcript(
                call_data=call_data, call_type=call_type
            )
            transcript_available = bool(transcript_resp.get("transcripts"))

            artifact = call_data.get("artifact") or {}
            analysis = call_data.get("analysis") or {}
            recording_obj = artifact.get("recording") or {}

            recording_urls = self._extract_recording_urls(call_data)
            recording_url = recording_urls.get("stereo") or recording_urls.get(
                "combined"
            )
            recording_available = bool(recording_url)

            system_phone_number_id = str(call_data.get("phoneNumberId") or "")
            system_phone_number = str(
                (call_data.get("phoneNumber") or {}).get("twilioPhoneNumber") or ""
            )
            customer_phone_number = str(
                (call_data.get("customer") or {}).get("number") or ""
            )

            return FAGICallData(
                call_id=str(call_data.get("id") or ""),
                call_type=call_type,
                status=status,
                assistant_id=str(call_data.get("assistantId") or ""),
                system_phone_number=system_phone_number,
                customer_phone_number=customer_phone_number,
                system_phone_number_id=system_phone_number_id,
                transcript_available=transcript_available,
                recording_available=recording_available,
                ended_reason=str(call_data.get("endedReason") or "") or None,
                summary=str(analysis.get("summary") or "") or None,
                cost_breakdown={
                    ProviderChoices.VAPI.value: (call_data.get("costBreakdown") or {})
                },
                transcript={ProviderChoices.VAPI.value: transcript_resp},
                recording_url=recording_url,
                recording={ProviderChoices.VAPI.value: recording_obj},
                log_url=str(artifact.get("logUrl") or "") or None,
                analysis_data={ProviderChoices.VAPI.value: analysis},
                evaluation_data={ProviderChoices.VAPI.value: {}},
                metadata={
                    ProviderChoices.VAPI.value: {
                        "monitor": call_data.get("monitor"),
                        "recording_urls": recording_urls,
                    }
                },
                created_at=call_data.get("createdAt"),
                started_at=call_data.get("startedAt"),
                ended_at=call_data.get("endedAt"),
                updated_at=call_data.get("updatedAt"),
                performance_metrics={
                    ProviderChoices.VAPI.value: (
                        artifact.get("performanceMetrics") or {}
                    )
                },
                cost=float(call_data.get("cost") or 0.0),
                duration_seconds=float(transcript_resp.get("duration") or 0.0),
                raw_log={ProviderChoices.VAPI.value: call_data},
            )
        except Exception as e:
            logger.error(f"Error normalizing VAPI call payload: {e}")
            raise ValueError(
                "An error occurred while parsing the data sent from VAPI"
            ) from e

    def _extract_recording_urls(self, call_data: dict[str, Any]) -> dict[str, str]:
        """Extract recording URLs from VAPI call data artifact."""
        artifact = call_data.get("artifact") or {}
        recording = artifact.get("recording") or {}
        mono = recording.get("mono") or {}

        urls: dict[str, str] = {}
        stereo = recording.get("stereoUrl")
        if isinstance(stereo, str) and stereo:
            urls["stereo"] = stereo

        combined = mono.get("combinedUrl")
        if isinstance(combined, str) and combined:
            urls["combined"] = combined

        assistant = mono.get("assistantUrl")
        if isinstance(assistant, str) and assistant:
            urls["assistant"] = assistant

        customer = mono.get("customerUrl")
        if isinstance(customer, str) and customer:
            urls["customer"] = customer

        return urls

    def _build_customer_system_metrics(
        self, performance_metrics: dict[str, Any] | None
    ) -> dict[str, float] | None:
        """Build normalized system metrics from VAPI performance data."""
        if not isinstance(performance_metrics, dict) or not performance_metrics:
            return None

        def _safe_number(value: Any) -> float | None:
            try:
                return float(value) if value is not None else None
            except (TypeError, ValueError):
                return None

        system_metrics = {
            "endpointing": _safe_number(
                performance_metrics.get("endpointingLatencyAverage")
            ),
            "transcriber": _safe_number(
                performance_metrics.get("transcriberLatencyAverage")
            ),
            "model": _safe_number(performance_metrics.get("modelLatencyAverage")),
            "voice": _safe_number(performance_metrics.get("voiceLatencyAverage")),
            "turn": _safe_number(performance_metrics.get("turnLatencyAverage")),
        }

        return {k: v for k, v in system_metrics.items() if v is not None}

    def _build_customer_cost_breakdown(
        self, cost_entries: list[dict[str, Any]] | None
    ) -> tuple[dict[str, dict[str, Any]], float]:
        """Build normalized cost breakdown from VAPI cost entries.

        When VAPI returns cost=0 for a component but includes usage data
        (tokens, minutes, characters) and model info, we compute the cost
        ourselves using litellm (for LLM) and LiveKit pricing rates (for
        STT/TTS) as a fallback.
        """
        if not isinstance(cost_entries, list):
            return {}, 0.0

        type_aliases = {
            "transcriber": "stt",
            "model": "llm",
            "voice": "tts",
        }

        aggregated: dict[str, dict[str, Any]] = {}
        total_cost = 0.0

        for entry in cost_entries:
            if not isinstance(entry, dict):
                continue

            raw_type = entry.get("type")
            if not raw_type:
                continue

            key = type_aliases.get(raw_type, raw_type)
            bucket = aggregated.setdefault(key, {"cost": 0.0})

            try:
                cost_value = float(entry.get("cost", 0) or 0)
            except (TypeError, ValueError):
                cost_value = 0.0

            bucket["cost"] += cost_value
            total_cost += cost_value

            if raw_type == "model":
                prompt_tokens = entry.get("promptTokens")
                completion_tokens = entry.get("completionTokens")
                if prompt_tokens is not None:
                    bucket["promptTokens"] = bucket.get("promptTokens", 0) + int(
                        prompt_tokens
                    )
                if completion_tokens is not None:
                    bucket["completionTokens"] = bucket.get(
                        "completionTokens", 0
                    ) + int(completion_tokens)

            if raw_type == "transcriber":
                minutes = entry.get("minutes")
                if minutes is not None:
                    try:
                        bucket["minutes"] = bucket.get("minutes", 0.0) + float(minutes)
                    except (TypeError, ValueError):
                        pass

            if raw_type == "voice":
                characters = entry.get("characters")
                if characters is not None:
                    try:
                        bucket["characters"] = bucket.get("characters", 0) + int(
                            characters
                        )
                    except (TypeError, ValueError):
                        pass

        # -----------------------------------------------------------------
        # Fallback: compute our own costs when VAPI returns 0 for components
        # but includes usage data. Uses litellm for LLM and LiveKit pricing
        # rates for STT/TTS as reasonable cross-provider estimates.
        # -----------------------------------------------------------------
        self._compute_fallback_costs(aggregated, cost_entries)

        # Recalculate total from (possibly updated) component costs
        total_cost = 0.0
        for item in aggregated.values():
            item["cost"] = round(item["cost"], 6)
            total_cost += item["cost"]

        return aggregated, total_cost

    @staticmethod
    def _compute_fallback_costs(
        aggregated: dict[str, dict[str, Any]],
        cost_entries: list[dict[str, Any]],
    ) -> None:
        """Compute costs from usage data when VAPI returns cost=0.

        Modifies *aggregated* in-place. Only computes for stt/llm/tts
        buckets where cost == 0 and usage data is present.
        """
        from ee.voice.services.livekit.pricing import (
            calculate_llm_cost,
            calculate_stt_cost,
            calculate_tts_cost,
        )

        # Build a model-name lookup from the original cost entries
        model_names: dict[str, str] = {}
        for entry in cost_entries:
            raw_type = entry.get("type", "")
            if raw_type == "model":
                model_info = entry.get("model", {})
                if isinstance(model_info, dict) and model_info.get("model"):
                    model_names["llm"] = model_info["model"]
            elif raw_type == "transcriber":
                transcriber_info = entry.get("transcriber", {})
                if isinstance(transcriber_info, dict) and transcriber_info.get("model"):
                    model_names["stt"] = transcriber_info["model"]
            elif raw_type == "voice":
                voice_info = entry.get("voice", {})
                if isinstance(voice_info, dict) and voice_info.get("model"):
                    model_names["tts"] = voice_info["model"]

        # STT: compute from minutes if cost is 0
        stt_bucket = aggregated.get("stt")
        if stt_bucket and stt_bucket["cost"] == 0 and stt_bucket.get("minutes"):
            duration_seconds = float(stt_bucket["minutes"]) * 60
            computed = calculate_stt_cost(
                duration_seconds=duration_seconds,
                model=model_names.get("stt", ""),
            )
            stt_bucket["cost"] = float(computed)

        # LLM: compute from tokens via litellm if cost is 0
        llm_bucket = aggregated.get("llm")
        if llm_bucket and llm_bucket["cost"] == 0:
            prompt_tokens = llm_bucket.get("promptTokens", 0)
            completion_tokens = llm_bucket.get("completionTokens", 0)
            if prompt_tokens or completion_tokens:
                computed = calculate_llm_cost(
                    prompt_tokens=int(prompt_tokens),
                    completion_tokens=int(completion_tokens),
                    model=model_names.get("llm", ""),
                )
                llm_bucket["cost"] = float(computed)

        # TTS: compute from characters if cost is 0
        tts_bucket = aggregated.get("tts")
        if tts_bucket and tts_bucket["cost"] == 0 and tts_bucket.get("characters"):
            computed = calculate_tts_cost(
                characters=int(tts_bucket["characters"]),
                model=model_names.get("tts", ""),
            )
            tts_bucket["cost"] = float(computed)

    def _find_customer_vapi_call_id(
        self,
        customer_api_key: str,
        customer_assistant_id: str,
        our_call_data: FAGICallData,
        time_window_seconds: int = 10,
        min_match_score_threshold: float = 200.0,
    ) -> str | None:
        """Find the customer's VAPI call ID by matching with our call data."""
        try:
            customer_vapi_service = VapiService(api_key=customer_api_key)

            our_start_time_str = our_call_data.started_at
            our_end_time_str = our_call_data.ended_at

            if not our_start_time_str:
                logger.warning("No startedAt time found in our call data")
                return None

            try:
                our_start_time = datetime.fromisoformat(
                    our_start_time_str.replace("Z", "+00:00")
                )
            except ValueError:
                logger.error(f"Invalid datetime format: {our_start_time_str}")
                return None

            our_duration = None
            if our_end_time_str:
                our_end_time = datetime.fromisoformat(
                    our_end_time_str.replace("Z", "+00:00")
                )
                our_duration = (our_end_time - our_start_time).total_seconds()

            search_start = our_start_time - timedelta(seconds=time_window_seconds)
            search_end = our_start_time + timedelta(seconds=time_window_seconds)

            our_call_type = str(
                our_call_data.call_type.value
                if hasattr(our_call_data.call_type, "value")
                else our_call_data.call_type
            ).lower()
            if our_call_type == "outbound":
                client_call_type = "outboundPhoneCall"
            else:
                client_call_type = "inboundPhoneCall"

            logger.info(
                f"Searching for customer call between {search_start} and "
                f"{search_end}, our_call_type={our_call_type}, "
                f"client_call_type={client_call_type}"
            )

            customer_calls_response = customer_vapi_service.list_calls(
                limit=50,
                assistant_id=customer_assistant_id,
                call_type=client_call_type,
                created_at_ge=search_start.isoformat(),
                created_at_le=search_end.isoformat(),
            )

            customer_calls = (
                customer_calls_response
                if isinstance(customer_calls_response, list)
                else []
            )

            logger.info(
                f"API returned {len(customer_calls)} calls with filters applied"
            )

            if customer_calls:
                filtered_calls = [
                    call
                    for call in customer_calls
                    if call.get("type") == client_call_type
                ]
                logger.info(
                    f"Filtered to {len(filtered_calls)} {client_call_type} calls"
                )
                customer_calls = filtered_calls

            matching_calls = []
            for call in customer_calls:
                call_start_str = call.get("startedAt")
                if not call_start_str:
                    continue

                call_start_time = datetime.fromisoformat(
                    call_start_str.replace("Z", "+00:00")
                )

                if search_start <= call_start_time <= search_end:
                    matching_calls.append(call)

            logger.info(f"Found {len(matching_calls)} calls within time window")

            if not matching_calls:
                logger.warning("No matching calls found within time window")
                return None

            if len(matching_calls) == 1:
                customer_call_id = matching_calls[0].get("id")
                logger.info(f"Found single matching call: {customer_call_id}")
                return customer_call_id

            # Multiple matches — score and rank
            best_match = None
            best_match_score = 0

            raw_log = our_call_data.raw_log.get(ProviderChoices.VAPI.value, {})
            our_artifact = raw_log.get("artifact", {})
            our_variables = our_artifact.get("variableValues", {}) or our_artifact.get(
                "variables", {}
            )
            our_phone_obj = our_variables.get("phoneNumber", {})
            our_phone_number = (
                our_phone_obj.get("number", "")
                if isinstance(our_phone_obj, dict)
                else ""
            )

            our_customer_number = our_call_data.customer_phone_number
            our_messages = raw_log.get("messages", [])
            our_conversation_messages = [
                msg
                for msg in our_messages
                if msg.get("role") in ["user", "assistant", "bot"]
            ]

            our_transcript_parts = []
            for msg in our_conversation_messages:
                role = msg.get("role", "")
                content = msg.get("message", "")
                if content:
                    our_transcript_parts.append(f"{role}: {content}")
            our_transcript = "\n".join(our_transcript_parts).lower().strip()

            def normalize_phone(phone):
                return (
                    phone.replace("+", "")
                    .replace("-", "")
                    .replace(" ", "")
                    .replace("(", "")
                    .replace(")", "")
                )

            for call in matching_calls:
                match_score = 0

                # 1. Phone number validation (mandatory)
                call_artifact = call.get("artifact", {})
                call_variables = call_artifact.get(
                    "variableValues", {}
                ) or call_artifact.get("variables", {})
                call_phone_obj = call_variables.get("phoneNumber", {})
                call_phone_number = (
                    call_phone_obj.get("number", "")
                    if isinstance(call_phone_obj, dict)
                    else ""
                )
                call_customer_number = call.get("customer", {}).get("number", "")

                phone_match = False
                if (
                    our_customer_number
                    and our_phone_number
                    and call_phone_number
                    and call_customer_number
                ):
                    our_cust_norm = normalize_phone(our_customer_number)
                    our_phone_norm = normalize_phone(our_phone_number)
                    call_phone_norm = normalize_phone(call_phone_number)
                    call_cust_norm = normalize_phone(call_customer_number)

                    if (
                        our_cust_norm == call_phone_norm
                        and our_phone_norm == call_cust_norm
                    ):
                        phone_match = True
                    else:
                        continue
                elif our_customer_number and call_customer_number:
                    if normalize_phone(our_customer_number) == normalize_phone(
                        call_customer_number
                    ):
                        phone_match = True
                    else:
                        continue
                else:
                    phone_match = True

                # 2. Time difference score (max 80 pts)
                call_start_str = call.get("startedAt")
                try:
                    call_start_time = datetime.fromisoformat(
                        call_start_str.replace("Z", "+00:00")
                    )
                except ValueError:
                    continue
                time_diff = abs((call_start_time - our_start_time).total_seconds())
                time_score = max(0, 80 - (time_diff * 8))
                match_score += time_score

                # 3. Duration similarity score (max 40 pts)
                duration_score = 0
                if our_duration:
                    call_end_str = call.get("endedAt")
                    if call_end_str:
                        call_end_time = datetime.fromisoformat(
                            call_end_str.replace("Z", "+00:00")
                        )
                        call_duration = (
                            call_end_time - call_start_time
                        ).total_seconds()
                        duration_diff = abs(call_duration - our_duration)
                        duration_score = max(0, 40 - duration_diff)
                match_score += duration_score

                # 4. Transcript fuzzy matching (max 180 pts)
                call_messages = call.get("messages", [])
                call_conversation_messages = [
                    msg
                    for msg in call_messages
                    if msg.get("role") in ["user", "assistant", "bot"]
                ]

                call_transcript_parts = []
                for msg in call_conversation_messages:
                    role = msg.get("role", "")
                    content = msg.get("message", "")
                    if content:
                        call_transcript_parts.append(f"{role}: {content}")
                call_transcript = "\n".join(call_transcript_parts).lower().strip()

                transcript_score = 0
                if our_transcript and call_transcript:
                    similarity_ratio = SequenceMatcher(
                        None, our_transcript, call_transcript
                    ).ratio()
                    transcript_score = similarity_ratio * 180

                    logger.info(
                        f"Call {call.get('id')}: Transcript similarity = "
                        f"{similarity_ratio:.2%} "
                        f"(our: {len(our_transcript)} chars, "
                        f"theirs: {len(call_transcript)} chars)"
                    )

                match_score += transcript_score

                logger.info(
                    f"Call {call.get('id')} match score: "
                    f"{match_score:.1f}/300 "
                    f"(time: {time_score:.1f}, "
                    f"duration: {duration_score:.1f}, "
                    f"transcript: {transcript_score:.1f})"
                )

                if match_score > best_match_score:
                    best_match_score = match_score
                    best_match = call

            if best_match:
                customer_call_id = best_match.get("id")
                logger.info(
                    f"Best matching call found: {customer_call_id} "
                    f"with score {best_match_score:.1f}/300"
                )

                if best_match_score < min_match_score_threshold:
                    our_call_id = our_call_data.raw_log.get(
                        ProviderChoices.VAPI.value, {}
                    ).get("id", "unknown")
                    alert_message = (
                        f"*Low Confidence Call Match Alert*\n\n"
                        f"*Our Call ID:* {our_call_id}\n"
                        f"*Matched Customer Call ID:* {customer_call_id}\n"
                        f"*Match Score:* {best_match_score:.1f}/300 "
                        f"(threshold: {min_match_score_threshold})\n"
                        f"*Customer Assistant ID:* "
                        f"{customer_assistant_id}\n\n"
                        f"_This match may be incorrect. "
                        f"Please verify manually._"
                    )
                    send_critical_slack_notification(alert_message)

                return customer_call_id

            logger.warning("No suitable match found among multiple candidates")
            return None

        except Exception as e:
            logger.error(f"Error finding customer VAPI call ID: {str(e)}")
            traceback.print_exc()
            return None

    # -- Provider-agnostic data extraction (for Temporal activities) -----------

    async def get_normalized_transcript_data(
        self, call_execution_id: str
    ) -> NormalizedTranscriptData:
        """Return provider-agnostic transcript + usage data from stored VAPI data.

        Reads provider_call_data["vapi"] from CallExecution, extracts messages
        from artifact.messages and token usage from costs array.
        """
        from simulate.models.test_execution import CallExecution

        call = await CallExecution.objects.aget(id=call_execution_id)
        provider_call_data = call.provider_call_data or {}
        provider_data = provider_call_data.get(ProviderChoices.VAPI.value, {})

        # Extract messages from VAPI artifact
        artifact = provider_data.get("artifact") or {}
        raw_messages = artifact.get("messages", [])

        messages = []
        for msg in raw_messages:
            role = msg.get("role", "unknown")
            content = msg.get("message", "")
            time_val = float(msg.get("time", 0))
            end_time_val = float(msg.get("endTime", 0)) if msg.get("endTime") else None
            duration_val = (
                float(msg.get("duration", 0)) if msg.get("duration") else None
            )

            messages.append(
                TranscriptMessage(
                    role=role,
                    content=content,
                    time=time_val,
                    end_time=end_time_val,
                    duration=duration_val,
                )
            )

        # Extract token usage from VAPI costs array (normalize to snake_case)
        cost_entries = provider_data.get("costs", [])
        token_usage: dict[str, Any] = {}
        if isinstance(cost_entries, list):
            for entry in cost_entries:
                if not isinstance(entry, dict):
                    continue
                if entry.get("type") == "model":
                    llm_bucket = token_usage.setdefault("llm", {})
                    if entry.get("promptTokens") is not None:
                        llm_bucket["prompt_tokens"] = llm_bucket.get(
                            "prompt_tokens", 0
                        ) + int(entry["promptTokens"])
                    if entry.get("completionTokens") is not None:
                        llm_bucket["completion_tokens"] = llm_bucket.get(
                            "completion_tokens", 0
                        ) + int(entry["completionTokens"])

        return NormalizedTranscriptData(messages=messages, token_usage=token_usage)

    async def extract_and_persist_recordings(
        self, call_execution_id: str
    ) -> RecordingUrls:
        """Extract VAPI recordings and persist to S3."""
        from simulate.models.test_execution import CallExecution
        from simulate.temporal.utils.async_storage import (
            convert_audio_url_to_s3_async_with_size,
        )
        from tracer.utils.vapi_recording import VapiArtifactType

        call = await CallExecution.objects.select_related(
            "test_execution__agent_definition__observability_provider",
            "test_execution__run_test",
        ).aget(id=call_execution_id)
        provider_call_data = call.provider_call_data or {}
        provider_data = provider_call_data.get(ProviderChoices.VAPI.value, {})

        recording_urls_raw = self._extract_recording_urls(provider_data)
        fagi = self._normalize_to_fagi_call_data(provider_data, call_data_stored=True)
        main_recording_url = fagi.recording_url

        result = RecordingUrls()
        call_id_str = str(call_execution_id)
        # Vapi call id from the ingest payload — used by api.vapi.ai/call/{id}/{artifact}.
        # Distinct from call_execution_id (FA CallExecution row).
        vapi_call_id = provider_data.get("id") or fagi.call_id
        vapi_provider = ProviderChoices.VAPI.value
        agent_definition = call.test_execution.agent_definition
        observability_provider = (
            agent_definition.observability_provider if agent_definition else None
        )
        project_id = (
            str(observability_provider.project_id) if observability_provider else None
        )
        organization_id = str(call.test_execution.run_test.organization_id)

        def emit_recording_storage_usage(url_type: str, payload_bytes: int) -> None:
            if not payload_bytes:
                return
            try:
                from ee.usage.schemas.event_types import BillingEventType
                from ee.usage.schemas.events import UsageEvent
                from ee.usage.services.emitter import emit

                emit(
                    UsageEvent(
                        event_id=str(
                            uuid.uuid5(
                                uuid.NAMESPACE_URL,
                                f"futureagi:simulate-recording:{call_execution_id}:{url_type}",
                            )
                        ),
                        org_id=organization_id,
                        event_type=BillingEventType.VOICE_RECORDING_STORAGE,
                        amount=payload_bytes,
                        properties={
                            "source": "simulate",
                            "source_id": str(call_execution_id),
                            "artifact_type": url_type,
                        },
                    )
                )
            except Exception:
                logger.exception("simulation_recording_storage_usage_failed")

        # Convert main recording URL
        if main_recording_url:
            try:
                s3_url, payload_bytes = await convert_audio_url_to_s3_async_with_size(
                    call_id_str, main_recording_url, "recording",
                    provider=vapi_provider,
                    api_key=self.api_key,
                    vapi_call_id=vapi_call_id,
                    artifact_type=VapiArtifactType.MONO,
                    project_id=project_id,
                )
                result.recording_url = s3_url
                emit_recording_storage_usage("recording", payload_bytes)
            except Exception:
                logger.warning(
                    "vapi_recording_conversion_failed",
                    call_id=call_id_str,
                    url_type="recording",
                )

        # Convert stereo recording URL
        stereo_url = recording_urls_raw.get("stereo")
        if stereo_url:
            try:
                s3_url, payload_bytes = await convert_audio_url_to_s3_async_with_size(
                    call_id_str, stereo_url, "stereo_recording",
                    provider=vapi_provider,
                    api_key=self.api_key,
                    vapi_call_id=vapi_call_id,
                    artifact_type=VapiArtifactType.STEREO,
                    project_id=project_id,
                )
                result.stereo_recording_url = s3_url
                emit_recording_storage_usage("stereo_recording", payload_bytes)
            except Exception:
                logger.warning(
                    "vapi_recording_conversion_failed",
                    call_id=call_id_str,
                    url_type="stereo",
                )

        # Convert assistant recording URL
        assistant_url = recording_urls_raw.get("assistant")
        if assistant_url:
            try:
                s3_url, payload_bytes = await convert_audio_url_to_s3_async_with_size(
                    call_id_str, assistant_url, "assistant_recording",
                    provider=vapi_provider,
                    api_key=self.api_key,
                    vapi_call_id=vapi_call_id,
                    artifact_type=VapiArtifactType.ASSISTANT,
                    project_id=project_id,
                )
                result.assistant_recording_url = s3_url
                emit_recording_storage_usage("assistant_recording", payload_bytes)
            except Exception:
                logger.warning(
                    "vapi_recording_conversion_failed",
                    call_id=call_id_str,
                    url_type="assistant",
                )

        # Convert customer recording URL
        customer_url = recording_urls_raw.get("customer")
        if customer_url:
            try:
                s3_url, payload_bytes = await convert_audio_url_to_s3_async_with_size(
                    call_id_str, customer_url, "customer_recording",
                    provider=vapi_provider,
                    api_key=self.api_key,
                    vapi_call_id=vapi_call_id,
                    artifact_type=VapiArtifactType.CUSTOMER,
                    project_id=project_id,
                )
                result.customer_recording_url = s3_url
                emit_recording_storage_usage("customer_recording", payload_bytes)
            except Exception:
                logger.warning(
                    "vapi_recording_conversion_failed",
                    call_id=call_id_str,
                    url_type="customer",
                )

        recording_object = {
            key: url
            for key, url in {
                "combined": result.recording_url,
                "stereo": result.stereo_recording_url,
                "assistant": result.assistant_recording_url,
                "customer": result.customer_recording_url,
            }.items()
            if url
        }
        if recording_object:
            _update_recording_payload(provider_data, recording_object)
            provider_call_data[ProviderChoices.VAPI.value] = provider_data
            result.provider_call_data = provider_call_data

        return result

    async def extract_costs(self, call_execution_id: str) -> CostBreakdown:
        """Extract VAPI cost breakdown from stored provider data."""
        from simulate.models.test_execution import CallExecution

        call = await CallExecution.objects.aget(id=call_execution_id)
        provider_data = (
            call.provider_call_data.get(ProviderChoices.VAPI.value, {})
            if call.provider_call_data
            else {}
        )

        total_cost = float(provider_data.get("cost") or 0.0)
        cost_breakdown_raw = provider_data.get("costBreakdown") or {}

        return CostBreakdown(
            total=total_cost,
            stt=float(cost_breakdown_raw.get("stt") or 0.0),
            llm=float(cost_breakdown_raw.get("llm") or 0.0),
            tts=float(cost_breakdown_raw.get("tts") or 0.0),
            transport=float(cost_breakdown_raw.get("vapi") or 0.0),
        )

    async def fetch_and_store_call_data(
        self,
        call_execution_id: str,
        provider_call_id: str,
        status: str,
        duration_seconds: float | None = None,
        end_reason: str | None = None,
        provider_data: dict[str, Any] | None = None,
    ) -> tuple[int, bool, bool]:
        """Fetch call data from VAPI API, store to CallExecution, save transcripts.

        Returns (message_count, has_agent_message, has_customer_message).
        """
        from simulate.models.test_execution import CallExecution, CallTranscript

        call = await CallExecution.objects.aget(id=call_execution_id)
        update_fields: list[str] = []

        # Fetch from VAPI API
        fagi_call_data = None
        if provider_call_id:
            try:
                loop = asyncio.get_running_loop()
                fagi_call_data = await loop.run_in_executor(
                    None,
                    lambda: self.get_call(
                        GetCallInput(call_id=provider_call_id, call_data_stored=True)
                    ),
                )
            except Exception as e:
                logger.warning(
                    "vapi_fetch_call_data_failed",
                    call_id=call_execution_id,
                    error=str(e),
                )

        message_count = 0
        has_agent_message = False
        has_customer_message = False

        if fagi_call_data:
            provider_key = ProviderChoices.VAPI.value

            # Deep-merge provider data to preserve metadata written by
            # initiate_outbound_call (assistant_id, phone config, etc.)
            existing_data = call.provider_call_data or {}
            if isinstance(fagi_call_data.raw_log, dict):
                existing_data.update(fagi_call_data.raw_log)
            else:
                existing_data = fagi_call_data.raw_log
            call.provider_call_data = existing_data
            call.customer_cost_breakdown = {}
            call.service_provider_call_id = fagi_call_data.call_id
            call.call_summary = fagi_call_data.summary
            update_fields.extend(
                [
                    "provider_call_data",
                    "customer_cost_breakdown",
                    "service_provider_call_id",
                    "call_summary",
                ]
            )

            if fagi_call_data.assistant_id:
                call.assistant_id = fagi_call_data.assistant_id
                update_fields.append("assistant_id")

            if fagi_call_data.customer_phone_number:
                call.customer_number = fagi_call_data.customer_phone_number
                update_fields.append("customer_number")

            if fagi_call_data.call_type:
                call.call_type = (
                    fagi_call_data.call_type.value
                    if hasattr(fagi_call_data.call_type, "value")
                    else fagi_call_data.call_type
                )
                update_fields.append("call_type")

            # Store ended_reason
            if fagi_call_data.ended_reason:
                call.ended_reason = fagi_call_data.ended_reason
            elif end_reason:
                call.ended_reason = end_reason
            update_fields.append("ended_reason")

            # Store timestamps
            if fagi_call_data.started_at:
                try:
                    call.started_at = datetime.fromisoformat(
                        fagi_call_data.started_at.replace("Z", "+00:00")
                    )
                    update_fields.append("started_at")
                except (ValueError, AttributeError):
                    pass

            if fagi_call_data.ended_at:
                try:
                    call.ended_at = datetime.fromisoformat(
                        fagi_call_data.ended_at.replace("Z", "+00:00")
                    )
                    update_fields.append("ended_at")
                except (ValueError, AttributeError):
                    from django.utils import timezone

                    call.ended_at = timezone.now()
                    update_fields.append("ended_at")
            else:
                from django.utils import timezone

                call.ended_at = timezone.now()
                update_fields.append("ended_at")

            # Save transcripts.
            transcript_data = fagi_call_data.transcript.get(provider_key, {})
            messages = transcript_data.get("transcripts", [])

            # Store raw provider shape at write time; direction-aware
            # interpretation happens at the read-time serializer boundary.
            raw_role_to_db_role = {
                "bot": CallTranscript.SpeakerRole.ASSISTANT,
                "assistant": CallTranscript.SpeakerRole.ASSISTANT,
                "agent": CallTranscript.SpeakerRole.ASSISTANT,
                "user": CallTranscript.SpeakerRole.USER,
                "customer": CallTranscript.SpeakerRole.USER,
                "system": CallTranscript.SpeakerRole.SYSTEM,
            }
            agent_roles = frozenset(
                {
                    CallTranscript.SpeakerRole.ASSISTANT,
                    "bot",
                    "agent",
                }
            )
            customer_roles = frozenset({CallTranscript.SpeakerRole.USER, "customer"})

            if messages:
                transcript_records = []
                for msg in messages:
                    raw_role = msg.get("speaker_role") or msg.get("role", "unknown")
                    content = msg.get("content") or msg.get("message", "")
                    role = raw_role_to_db_role.get(
                        (raw_role or "").lower(), raw_role
                    )

                    role_lower = role.lower()
                    has_content = bool(content and content.strip())
                    if has_content and role_lower in agent_roles:
                        has_agent_message = True
                    if has_content and role_lower in customer_roles:
                        has_customer_message = True

                    transcript_records.append(
                        CallTranscript(
                            call_execution=call,
                            speaker_role=role,
                            content=content,
                            start_time_ms=msg.get("start_time_ms", 0),
                            end_time_ms=msg.get("end_time_ms", 0),
                        )
                    )

                await CallTranscript.objects.abulk_create(transcript_records)
                message_count = len(messages)

        else:
            # No provider data — store basic fields
            if end_reason:
                call.ended_reason = end_reason
                update_fields.append("ended_reason")
            from django.utils import timezone

            call.ended_at = timezone.now()
            update_fields.append("ended_at")

        # Store duration and status
        if duration_seconds is not None:
            call.duration_seconds = int(duration_seconds)
            update_fields.append("duration_seconds")

        # NOTE: Unlike LiveKit (where the workflow owns status transitions),
        # VAPI sets status here because the old Celery path relied on it.
        # The Temporal workflow's update_call_status activity will overwrite
        # this with the final status shortly after.
        call.status = status
        update_fields.append("status")

        call.transcript_available = message_count > 0
        call.message_count = message_count
        update_fields.extend(["transcript_available", "message_count"])

        await call.asave(update_fields=update_fields)

        return message_count, has_agent_message, has_customer_message
