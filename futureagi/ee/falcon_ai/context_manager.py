"""
Context compaction for Falcon AI conversations.

When a conversation grows long, we use the LLM to generate a rich summary
of the older messages. This summary is persisted on the Conversation model
and used in place of the raw history for future turns.

Flow:
  1. Load last N messages from DB + any existing context_summary
  2. Check if compaction is needed (message count or token estimate)
  3. If needed → call LLM to summarize old messages → save summary to DB
  4. Build final message list: system + summary + recent messages + user message
"""

import structlog
from channels.db import database_sync_to_async

logger = structlog.get_logger(__name__)


class ContextManager:
    # Token estimation
    CHARS_PER_TOKEN = 3.5

    # Compaction thresholds
    COMPACTION_MESSAGE_THRESHOLD = 20  # Trigger when history has 20+ messages
    COMPACTION_TOKEN_THRESHOLD = 80000  # Or when estimated tokens exceed 80K
    MAX_HISTORY_TOKENS = 120000  # Hard cap for total context sent to LLM

    # Message content limits
    MAX_RESULT_CHARS = 2000  # Max chars for a tool result in history
    MAX_MESSAGE_CHARS = 4000  # Max chars for any single message in history
    KEEP_RECENT_MESSAGES = 8  # Always keep last N messages uncompacted

    # Prompt for the summarization LLM call
    COMPACTION_PROMPT = (
        "You are summarizing a conversation between a user and an AI assistant "
        "called Falcon AI.\n"
        "Create a detailed summary that preserves:\n"
        "- All key facts, decisions, and conclusions reached\n"
        "- Specific data points, numbers, and results mentioned\n"
        "- What tools were called and their important results\n"
        "- What the user's goals and preferences are\n"
        "- Any pending tasks or follow-ups mentioned\n"
        "- Technical context (project names, model names, dataset names, etc.)\n\n"
        "Be thorough — this summary replaces the full conversation history. "
        "Future turns will only see this summary, not the original messages. "
        'Write in third person ("The user asked...", "Falcon found...").\n\n'
        "Do NOT include pleasantries or filler. Focus on factual content that "
        "would be needed to continue the conversation naturally."
    )

    # ── Token estimation ──

    def _estimate_tokens(self, text):
        """Rough token estimate from character count."""
        return int(len(text) / self.CHARS_PER_TOKEN) if text else 0

    def estimate_messages_tokens(self, messages):
        """Estimate total tokens for a list of messages."""
        return sum(self._estimate_tokens(m.get("content", "")) for m in messages)

    # ── Truncation helpers ──

    def truncate_result(self, result_text):
        """Truncate large tool results while keeping head + tail."""
        if len(result_text) <= self.MAX_RESULT_CHARS:
            return result_text
        head = result_text[:1500]
        tail = result_text[-300:]
        omitted = len(result_text) - 1800
        return f"{head}\n\n... [truncated {omitted} chars] ...\n\n{tail}"

    def _truncate_content(self, content):
        """Truncate any message content that's too long."""
        if not content or len(content) <= self.MAX_MESSAGE_CHARS:
            return content
        head = content[:3000]
        tail = content[-500:]
        omitted = len(content) - 3500
        return f"{head}\n\n... [truncated {omitted} chars] ...\n\n{tail}"

    # ── Multi-tier compaction ──

    def get_compaction_tier(self, history_messages):
        """Determine which compaction tier to use.

        Tier 0: No compaction needed
        Tier 1: Light compaction — drop tool call details from old messages
        Tier 2: Full compaction — LLM summarization of old messages
        """
        msg_count = len(history_messages)
        estimated_tokens = self.estimate_messages_tokens(history_messages)

        if msg_count < 12 and estimated_tokens < 40000:
            return 0  # No compaction
        if msg_count < 20 and estimated_tokens < 80000:
            return 1  # Light compaction
        return 2  # Full LLM summarization

    def light_compact(self, history_messages):
        """Tier 1: Strip tool call details from older messages, keep summaries."""
        if len(history_messages) <= self.KEEP_RECENT_MESSAGES:
            return history_messages

        old = history_messages[: -self.KEEP_RECENT_MESSAGES]
        recent = history_messages[-self.KEEP_RECENT_MESSAGES :]

        compacted_old = []
        for msg in old:
            role = msg.get("role", "")
            content = msg.get("content", "")
            tool_calls = msg.get("tool_calls") or []

            if role == "assistant" and tool_calls:
                # Keep only tool names, drop full results
                tool_names = [
                    tc.get("tool_name", "")
                    for tc in tool_calls[:5]
                    if isinstance(tc, dict)
                ]
                summary = (
                    f"[Used: {', '.join(tool_names)}] {content[:500]}"
                    if content
                    else f"[Used: {', '.join(tool_names)}]"
                )
                compacted_old.append({"role": role, "content": summary})
            elif content:
                # Truncate old messages more aggressively
                compacted_old.append({"role": role, "content": content[:1000]})

        return compacted_old + recent

    async def compact_if_needed(self, conversation, history_messages, llm_client):
        """Run compaction if the conversation is too long.

        Returns a tuple of (conversation, compacted_history_messages).
        - Tier 0: No changes.
        - Tier 1: Light compaction (strip tool details, no LLM call).
        - Tier 2: Full LLM summarization saved to DB, recent messages only.
        """
        tier = self.get_compaction_tier(history_messages)

        if tier == 0:
            return conversation, history_messages

        if tier == 1:
            # Light compaction — no LLM call needed
            compacted = self.light_compact(history_messages)
            logger.info(
                "context_light_compaction",
                conversation_id=str(conversation.id),
                message_count=len(history_messages),
                compacted_count=len(compacted),
            )
            return conversation, compacted

        # Tier 2: Full LLM summarization
        # Not enough messages to split into old + recent
        if len(history_messages) <= self.KEEP_RECENT_MESSAGES:
            return conversation, history_messages

        logger.info(
            "context_compaction_triggered",
            conversation_id=str(conversation.id),
            message_count=len(history_messages),
            tier=tier,
        )

        # Split: old messages get summarized, recent stay intact
        old_messages = history_messages[: -self.KEEP_RECENT_MESSAGES]
        recent_messages = history_messages[-self.KEEP_RECENT_MESSAGES :]
        conversation_text = self._build_compaction_text(conversation, old_messages)

        try:
            summary = await llm_client.generate_summary(
                self.COMPACTION_PROMPT, conversation_text
            )
            if not summary:
                return conversation, history_messages

            # Update conversation in memory
            conversation.context_summary = summary
            recent_tokens = self.estimate_messages_tokens(recent_messages)
            summary_tokens = self._estimate_tokens(summary)
            new_total = recent_tokens + summary_tokens
            conversation.total_tokens = new_total

            # Persist to DB
            await database_sync_to_async(self._save_compaction, thread_sensitive=True)(
                conversation.id, summary, new_total
            )

            logger.info(
                "context_compaction_complete",
                conversation_id=str(conversation.id),
                summary_tokens=summary_tokens,
                new_total_tokens=new_total,
            )

            # Return only recent messages — old ones are captured in summary
            return conversation, recent_messages
        except Exception as e:
            logger.warning(
                "context_compaction_failed",
                conversation_id=str(conversation.id),
                error=str(e),
            )

        return conversation, history_messages

    @staticmethod
    def _save_compaction(conversation_id, summary, total_tokens):
        """Sync DB update — called via database_sync_to_async."""
        from ee.falcon_ai.models import Conversation

        Conversation.objects.filter(id=conversation_id).update(
            context_summary=summary,
            total_tokens=total_tokens,
        )

    def _build_compaction_text(self, conversation, old_messages):
        """Build the text to send to the summarization LLM."""
        parts = []

        # Include any previous summary as context
        if conversation.context_summary:
            parts.append(f"Previous summary:\n{conversation.context_summary}")

        for msg in old_messages:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            tool_calls = msg.get("tool_calls") or []

            if not content and not tool_calls:
                continue

            # Append compact tool call info to assistant messages
            if role == "assistant" and tool_calls:
                tool_info = []
                for tc in tool_calls[:5]:
                    if isinstance(tc, dict):
                        name = tc.get("tool_name", "unknown")
                        result = str(tc.get("result", ""))[:300]
                        tool_info.append(f"  - {name}: {result}")
                if tool_info:
                    content = (
                        (content or "")
                        + "\n[Tool calls:\n"
                        + "\n".join(tool_info)
                        + "]"
                    )

            parts.append(f"[{role}]: {content[:2000]}")

        return "\n\n".join(parts)

    # ── Message preparation ──

    def prepare_messages(
        self, system_prompt, history, user_message, context_summary=""
    ):
        """Prepare the final message list for the LLM call.

        Message ordering is cache-friendly for Anthropic prompt caching:
          1. System prompt — stable across all turns (cached)
          2. Context summary — stable after compaction (cached)
          3. Tool definitions — sent separately via API (cached by provider)
          4. Recent conversation messages — change each turn (not cached)
          5. Current user message — always last
        """
        messages = [{"role": "system", "content": system_prompt}]

        # Inject compacted summary right after system prompt (cache-friendly position)
        if context_summary:
            messages.append(
                {
                    "role": "system",
                    "content": (
                        "The following is a summary of the earlier part of this conversation. "
                        "Use it as context but do not repeat or reference the summary itself.\n\n"
                        + context_summary
                    ),
                }
            )

        # Enrich history with tool call summaries and truncate
        enriched = self._enrich_history(history)
        for msg in enriched:
            msg["content"] = self._truncate_content(msg.get("content", ""))

        messages.extend(enriched)
        messages.append({"role": "user", "content": user_message})

        # Final safety: drop oldest messages if over hard token cap
        return self._enforce_token_budget(messages)

    def _enforce_token_budget(self, messages):
        """Drop oldest non-system messages if total tokens exceed hard cap."""
        total = sum(self._estimate_tokens(m.get("content", "")) for m in messages)
        if total <= self.MAX_HISTORY_TOKENS:
            return messages

        # Preserve system messages (first) and user message (last)
        system_msgs = [m for m in messages if m["role"] == "system"]
        user_msg = messages[-1]
        middle = list(messages[len(system_msgs) : -1])

        while total > self.MAX_HISTORY_TOKENS and middle:
            dropped = middle.pop(0)
            total -= self._estimate_tokens(dropped.get("content", ""))

        return system_msgs + middle + [user_msg]

    def _enrich_history(self, history):
        """Embed tool call summaries into assistant messages for context."""
        enriched = []
        for msg in history:
            role = msg.get("role", "")
            content = msg.get("content", "")
            tool_calls = msg.get("tool_calls") or []

            if role == "assistant" and tool_calls:
                tool_parts = []
                for tc in tool_calls[:5]:
                    if isinstance(tc, dict):
                        name = tc.get("tool_name", "")
                        result = str(tc.get("result", ""))[:200]
                        if name:
                            tool_parts.append(
                                f"{name}" + (f" → {result}" if result else "")
                            )
                tool_summary = "; ".join(tool_parts)
                if tool_summary and content:
                    content = f"[Tools: {tool_summary}]\n{content}"
                elif tool_summary:
                    content = f"[Tools: {tool_summary}]"

            if content:
                enriched.append({"role": role, "content": content})

        return enriched

    def truncate_messages(self, messages):
        """Truncate large tool results in the agent loop's message list."""
        for msg in messages:
            if (
                msg.get("role") == "tool"
                and len(msg.get("content", "")) > self.MAX_RESULT_CHARS
            ):
                msg["content"] = self.truncate_result(msg["content"])
        return messages
