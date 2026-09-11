"""
Conversation Metrics Calculator
This module calculates various conversation metrics from message data
including latency, interruption rates, words per minute, and talk ratios.

Role conventions AFTER normalization (both inbound & outbound):
- role="bot"  = test_agent (the agent being tested)
- role="user" = simulated_agent (our FAGI simulated/customer side)

All metrics are therefore from the test_agent's perspective, but the
field names are kept for backwards compatibility with the existing API.
"""

import re
from dataclasses import dataclass, field
from typing import Any

import structlog

from ee.voice.services.types.voice import NormalizedTranscriptData
from tracer.models.observability_provider import ProviderChoices

logger = structlog.get_logger(__name__)


@dataclass
class MessageData:
    """Represents a single message in the conversation"""

    role: str
    time: float
    end_time: float | None
    message: str
    duration: float | None
    seconds_from_start: float


@dataclass
class ConversationMetrics:
    """
    Container for all calculated conversation metrics.

    NOTE:
    - avg_agent_latency_ms is the average latency of the test_agent (bot)
      responding after the simulated_agent (user) stops speaking.
    - user_* fields refer to the simulated_agent side (role="user").
    - bot_* / ai_* fields refer to the test_agent side (role="bot").
    """

    # How long the agent (bot/test_agent) takes to respond after user finishes
    avg_agent_latency_ms: int | None = None

    # How many times the simulated_agent (user) interrupted the test_agent (bot)
    user_interruption_count: int = 0
    user_interruption_rate: float | None = None

    # How many times the test_agent (bot) interrupted the simulated_agent (user)
    ai_interruption_count: int = 0
    ai_interruption_rate: float | None = None

    # Words per minute for each side
    user_wpm: float | None = None
    bot_wpm: float | None = None

    # Talk ratio: bot speaking time / user speaking time
    talk_ratio: float | None = None

    # How quickly the bot stops after being interrupted by user
    avg_stop_time_after_interruption_ms: int | None = None

    # Extra debug / detailed data
    detailed_data: dict[str, Any] | None = field(default=None)


class ConversationMetricsCalculator:
    """
    Calculates conversation metrics from message data.

    After normalization:
    - role="bot"  = test_agent
    - role="user" = simulated_agent
    """

    def __init__(self, voice_service_provider=ProviderChoices.VAPI):
        self.word_pattern = re.compile(r"\b\w+\b")
        self.voice_service_provider = voice_service_provider

    def _normalize_roles_for_test_agent(
        self, messages: list[MessageData], is_outbound: bool | None
    ) -> list[MessageData]:
        """
        Normalize roles so that:
         - role == "bot" always refers to the test_agent (agent under test), and
         - role == "user" refers to the FAGI simulated/customer side.

        The swap decision is delegated to the centralised transcript_roles
        module (is_simulator). If "bot" maps to the simulator for this
        provider + direction, we swap bot <-> user so that "bot" ends up
        meaning tested agent (the internal metrics convention).
        """
        if is_outbound is None or not messages:
            return messages

        from simulate.utils.speaker_roles import SpeakerRoleResolver

        # Check if "bot" means simulator for this provider + direction.
        # If yes, swap so that "bot" = tested agent (metrics convention).
        # If "bot" already means tested agent (e.g. VAPI outbound), no swap.
        needs_swap = SpeakerRoleResolver.is_simulator(
            "bot",
            provider=self.voice_service_provider,
            is_outbound=is_outbound,
        )
        if not needs_swap:
            return messages

        for msg in messages:
            if msg.role == "bot":
                msg.role = "user"
            elif msg.role == "user":
                msg.role = "bot"
        return messages

    def __extract_messages_from_raw_log(
        self, raw_log: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """
        Extracts messages based on the voice service provider of the system
        Args:
            messages: List of message dictionaries with
                role, time, endTime, message, duration, secondsFromStart
        Returns:
            list[dict[str,any]] object with the list of messages from the raw logs
        """
        try:
            if self.voice_service_provider == ProviderChoices.VAPI:
                if isinstance(raw_log, dict):
                    return raw_log.get("messages", [])
        except Exception:
            logger.exception("Exception while extracting messages from raw log")
            return []

    def calculate_metrics(
        self, raw_log: dict[str, Any], is_outbound: bool | None = None
    ) -> ConversationMetrics:
        """
        Calculate all conversation metrics from message data

        Args:
            messages: List of message dictionaries with
                role, time, endTime, message, duration, secondsFromStart
            is_outbound: Type of call (used for role normalization)

        Returns:
            ConversationMetrics object with all calculated metrics
        """
        messages = self.__extract_messages_from_raw_log(raw_log)
        if not messages:
            return ConversationMetrics()

        # Parse messages into MessageData objects and sort by time
        parsed_messages = self._parse_messages(messages)
        parsed_messages.sort(key=lambda m: m.time)
        parsed_messages = self._normalize_roles_for_test_agent(
            parsed_messages, is_outbound
        )

        # Calculate individual metrics
        metrics = ConversationMetrics()

        # Average latency of the agent (bot/test_agent) responding after user
        metrics.avg_agent_latency_ms = self._calculate_agent_latency(parsed_messages)

        # Interruption metrics
        user_interruptions, ai_interruptions = self._calculate_interruptions(
            parsed_messages
        )
        metrics.user_interruption_count = len(user_interruptions)
        metrics.ai_interruption_count = len(ai_interruptions)

        # Interruption rates (per minute)
        total_duration_minutes = self._get_total_conversation_duration_minutes(
            parsed_messages
        )
        if total_duration_minutes > 0:
            metrics.user_interruption_rate = (
                metrics.user_interruption_count / total_duration_minutes
            )
            metrics.ai_interruption_rate = (
                metrics.ai_interruption_count / total_duration_minutes
            )

        # Words per minute (user and bot)
        metrics.user_wpm, metrics.bot_wpm = self._calculate_wpm(parsed_messages)

        # Talk ratio (bot speaking time / user speaking time)
        metrics.talk_ratio = self._calculate_talk_ratio(parsed_messages)

        # Stop time after interruption (how long bot continues after user interrupts)
        metrics.avg_stop_time_after_interruption_ms = (
            self._calculate_stop_time_after_interruption(
                parsed_messages, user_interruptions
            )
        )

        # Store detailed data (for debugging / analytics)
        user_message_count = len([m for m in parsed_messages if m.role == "user"])
        bot_message_count = len([m for m in parsed_messages if m.role == "bot"])
        metrics.detailed_data = {
            "user_interruptions": user_interruptions,
            "ai_interruptions": ai_interruptions,
            "total_duration_minutes": total_duration_minutes,
            "message_count": len(parsed_messages),
            # For parity with chat simulation turn_count semantics,
            # voice turn_count represents agent turns (role="bot").
            "turn_count": bot_message_count,
            "user_message_count": user_message_count,
            "bot_message_count": bot_message_count,
        }

        # print("::::::::::::: Parsed Messages: ", parsed_messages)
        # import json
        # print("=========== Here you go: ", metrics)

        return metrics

    def calculate_metrics_from_normalized(
        self, data: NormalizedTranscriptData, is_outbound: bool | None = None
    ) -> ConversationMetrics:
        """Calculate metrics from provider-agnostic NormalizedTranscriptData.

        This is the preferred entry point for new code. The older
        calculate_metrics(raw_log, ...) method is kept for backward compatibility.

        LiveKit TranscriptMessage stores times in seconds (from DB ms / 1000),
        so we convert to ms. VAPI TranscriptMessage already has times in ms
        (passed through from raw log), so no conversion needed.
        """
        if not data.messages:
            return ConversationMetrics()

        # LiveKit normalizes times to seconds; VAPI keeps raw ms values.
        needs_sec_to_ms = self.voice_service_provider == ProviderChoices.LIVEKIT
        factor = 1000 if needs_sec_to_ms else 1

        # Map speaker roles to the canonical "bot"/"user" expected internally.
        role_map = {"assistant": "bot", "agent": "bot", "customer": "user"}

        parsed_messages: list[MessageData] = []
        for msg in data.messages:
            role = role_map.get(msg.role, msg.role)
            parsed_messages.append(
                MessageData(
                    role=role,
                    time=msg.time * factor,
                    end_time=(
                        msg.end_time * factor if msg.end_time is not None else None
                    ),
                    message=msg.content,
                    duration=(
                        msg.duration * factor if msg.duration is not None else None
                    ),
                    seconds_from_start=msg.time,
                )
            )

        parsed_messages.sort(key=lambda m: m.time)
        parsed_messages = self._normalize_roles_for_test_agent(
            parsed_messages, is_outbound
        )

        metrics = ConversationMetrics()
        metrics.avg_agent_latency_ms = self._calculate_agent_latency(parsed_messages)

        user_interruptions, ai_interruptions = self._calculate_interruptions(
            parsed_messages
        )
        metrics.user_interruption_count = len(user_interruptions)
        metrics.ai_interruption_count = len(ai_interruptions)

        total_duration_minutes = self._get_total_conversation_duration_minutes(
            parsed_messages
        )
        if total_duration_minutes > 0:
            metrics.user_interruption_rate = (
                metrics.user_interruption_count / total_duration_minutes
            )
            metrics.ai_interruption_rate = (
                metrics.ai_interruption_count / total_duration_minutes
            )

        metrics.user_wpm, metrics.bot_wpm = self._calculate_wpm(parsed_messages)
        metrics.talk_ratio = self._calculate_talk_ratio(parsed_messages)
        metrics.avg_stop_time_after_interruption_ms = (
            self._calculate_stop_time_after_interruption(
                parsed_messages, user_interruptions
            )
        )

        user_message_count = len([m for m in parsed_messages if m.role == "user"])
        bot_message_count = len([m for m in parsed_messages if m.role == "bot"])

        metrics.detailed_data = {
            "user_interruptions": user_interruptions,
            "ai_interruptions": ai_interruptions,
            "total_duration_minutes": total_duration_minutes,
            "message_count": len(parsed_messages),
            # For parity with chat simulation turn_count semantics,
            # voice turn_count represents agent turns (role="bot").
            "turn_count": bot_message_count,
            "user_message_count": user_message_count,
            "bot_message_count": bot_message_count,
        }

        return metrics

    def _parse_messages(self, messages: list[dict[str, Any]]) -> list[MessageData]:
        """Parse raw message data into MessageData objects.

        VAPI raw logs store time/endTime in ms and duration in ms.
        Values are passed through as-is (already in ms).
        """
        parsed: list[MessageData] = []
        for msg in messages:
            parsed.append(
                MessageData(
                    role=msg.get("role", ""),
                    time=msg.get("time", 0),
                    end_time=msg.get("endTime"),
                    message=msg.get("message", ""),
                    duration=msg.get("duration"),
                    seconds_from_start=msg.get("secondsFromStart", 0),
                )
            )
        return parsed

    @staticmethod
    def _reliable_end_time(msg: MessageData) -> float | None:
        """End time of a message, or None when the provider gives no duration.

        Callers must skip on None rather than fall back to the start time, which
        would count the inter-utterance gap (the other party's whole turn) as
        latency or overlap.
        """
        if msg.end_time is not None:
            return msg.end_time
        if msg.duration is not None:
            return msg.time + msg.duration
        return None

    def _calculate_agent_latency(self, messages: list[MessageData]) -> int | None:
        """
        Calculate average agent latency (time taken by bot/test_agent
        to respond after the simulated_agent/user pauses).

        After normalization:
        - role="user" = simulated_agent
        - role="bot"  = test_agent

        We look for: user → bot transitions.
        """
        latencies: list[float] = []

        for i in range(len(messages) - 1):
            current_msg = messages[i]
            next_msg = messages[i + 1]

            # user message followed by bot message
            if current_msg.role == "user" and next_msg.role == "bot":
                user_end_time = self._reliable_end_time(current_msg)
                if user_end_time is None:
                    continue  # no duration → can't tell latency from the gap
                bot_start_time = next_msg.time
                latency_ms = bot_start_time - user_end_time
                if latency_ms >= 0:
                    latencies.append(latency_ms)

        return int(sum(latencies) / len(latencies)) if latencies else None

    def _calculate_interruptions(
        self, messages: list[MessageData]
    ) -> tuple[list[dict], list[dict]]:
        """
        Calculate interruptions from both sides.

        Returns: (user_interruptions, ai_interruptions)

        - user_interruptions: simulated_agent (user) interrupted test_agent (bot)
        - ai_interruptions:   test_agent (bot) interrupted simulated_agent (user)
        """
        user_interruptions: list[dict] = []
        ai_interruptions: list[dict] = []

        for i in range(len(messages) - 1):
            current_msg = messages[i]
            next_msg = messages[i + 1]

            # User interrupting bot: user starts before bot finishes
            if current_msg.role == "bot" and next_msg.role == "user":
                bot_end_time = self._reliable_end_time(current_msg)
                if bot_end_time is None:
                    continue  # no duration → overlap undetectable
                user_start_time = next_msg.time

                if user_start_time < bot_end_time:
                    user_interruptions.append(
                        {
                            "interrupted_message_index": i,
                            "interrupting_message_index": i + 1,
                            "interruption_time": next_msg.time,
                            "bot_was_supposed_to_end": bot_end_time,
                            "interruption_duration_ms": bot_end_time - user_start_time,
                        }
                    )

            # Bot interrupting user: bot starts before user finishes
            elif current_msg.role == "user" and next_msg.role == "bot":
                user_end_time = self._reliable_end_time(current_msg)
                if user_end_time is None:
                    continue
                bot_start_time = next_msg.time

                if bot_start_time < user_end_time:
                    ai_interruptions.append(
                        {
                            "interrupted_message_index": i,
                            "interrupting_message_index": i + 1,
                            "interruption_time": next_msg.time,
                            "user_was_supposed_to_end": user_end_time,
                            "interruption_duration_ms": user_end_time - bot_start_time,
                        }
                    )

        return user_interruptions, ai_interruptions

    def _calculate_wpm(
        self, messages: list[MessageData]
    ) -> tuple[float | None, float | None]:
        """
        Calculate words per minute for user (simulated_agent) and bot (test_agent).

        Returns: (user_wpm, bot_wpm)
        """
        user_words = 0
        user_duration = 0.0
        bot_words = 0
        bot_duration = 0.0

        for msg in messages:
            if msg.role in ["user", "bot"] and msg.message and msg.duration:
                word_count = len(self.word_pattern.findall(msg.message))

                # Only count messages with reasonable duration (>100ms)
                if msg.duration >= 100:
                    if msg.role == "user":
                        user_words += word_count
                        user_duration += msg.duration
                    elif msg.role == "bot":
                        bot_words += word_count
                        bot_duration += msg.duration

        user_wpm = (
            (user_words / (user_duration / 1000.0) * 60) if user_duration > 0 else None
        )
        bot_wpm = (
            (bot_words / (bot_duration / 1000.0) * 60) if bot_duration > 0 else None
        )

        # Cap WPM at a reasonable human max
        if user_wpm and user_wpm > 300:
            user_wpm = 300
        if bot_wpm and bot_wpm > 300:
            bot_wpm = 300

        return user_wpm, bot_wpm

    def _calculate_talk_ratio(self, messages: list[MessageData]) -> float | None:
        """
        Calculate talk ratio (bot speaking time / user speaking time).

        Returns None when user didn't speak (to avoid infinity in JSON).
        """
        user_duration = 0.0
        bot_duration = 0.0

        for msg in messages:
            if msg.role == "user" and msg.duration:
                user_duration += msg.duration
            elif msg.role == "bot" and msg.duration:
                bot_duration += msg.duration

        if user_duration > 0:
            return bot_duration / user_duration
        else:
            # No user speech → avoid inf; return None
            return None

    def _calculate_stop_time_after_interruption(
        self, messages: list[MessageData], user_interruptions: list[dict]
    ) -> int | None:
        """
        Calculate average stop time after the test_agent (bot) is interrupted by the simulated_agent (user).

        After normalization:
        - role="bot"  = test_agent
        - role="user" = simulated_agent

        The `user_interruptions` list contains events where:
        - current_msg.role == "bot"
        - next_msg.role == "user"
        - user_start_time < bot_end_time

        For each such interruption, we measure:
            stop_time_ms = (actual end time of the interrupted bot message)
                           - (interruption_time)

        This answers: "How long does the test_agent continue speaking after being interrupted?"
        """
        if not user_interruptions:
            return None

        stop_times: list[float] = []

        for interruption in user_interruptions:
            interrupted_msg_index = interruption.get(
                "interrupted_message_index"
            )  # bots message that is interrupted
            interruption_time = interruption.get(
                "interruption_time"
            )  # time when user started interrupting

            if (
                interrupted_msg_index is None
                or interruption_time is None
                or interrupted_msg_index < 0
                or interrupted_msg_index >= len(messages)
            ):
                continue

            interrupted_msg = messages[
                interrupted_msg_index
            ]  # bots message that is interrupted

            # Compute when the test_agent (bot) actually stopped speaking
            if interrupted_msg.duration:
                if interrupted_msg.end_time is not None:
                    actual_stop_time = interrupted_msg.end_time
                else:
                    actual_stop_time = interrupted_msg.time + interrupted_msg.duration
            else:
                # Fallback: use whichever timestamp we have
                actual_stop_time = interrupted_msg.end_time or interrupted_msg.time

            stop_time_ms = actual_stop_time - interruption_time
            if stop_time_ms >= 0:
                stop_times.append(stop_time_ms)

        return int(sum(stop_times) / len(stop_times)) if stop_times else None

    def _get_total_conversation_duration_minutes(
        self, messages: list[MessageData]
    ) -> float:
        """Get total conversation duration in minutes.

        Falls back to a message's start time when it has no end (Bland), so the
        total is the start-time span, never negative; counts a t=0 first message.
        """
        if not messages:
            return 0.0

        starts = [msg.time for msg in messages if msg.time is not None]
        if not starts:
            return 0.0

        # Floor the end at the last start; bump it with any real end times.
        max_end_time = max(starts)
        for msg in messages:
            if msg.time is None:
                continue
            end = self._reliable_end_time(msg)
            if end is not None and end > max_end_time:
                max_end_time = end

        total_duration_ms = max_end_time - min(starts)
        return max(0.0, total_duration_ms) / (1000.0 * 60.0)
