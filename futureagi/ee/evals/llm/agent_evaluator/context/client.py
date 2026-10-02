"""Eval-scoped LLM client wrapper — the 4-layer defense orchestrator.

Closes the per-iteration context-management gap for evals (TH-4970):
the underlying ``AgentLoop`` only runs ``ContextManager.compact_if_needed``
once at startup, and for evals (where ``history_messages=[]``) the
tier check short-circuits to no-op. This wrapper intercepts every
LLM call and applies four cascading layers of defense, falling
through to each next layer only if the previous one didn't get the
prompt under the relevant budget.

Layer 1 — per-message head+tail cap (huge single message)
Layer 2 — per-tool-result cap (defensive re-run of falcon_ai helper)
Layer 3 — cumulative budget cascade:
          3a) ``ContextManager.light_compact``
          3b) LLM-driven summarization (mirrors falcon_ai's tier 2)
          3c) hard-drop oldest non-anchor messages in tool-call pairs
Layer 4 — soft-fail marker: if the prompt is still over the guard
          ceiling after every previous layer has been tried, log a
          warning, stash a ``last_oversized_attempt`` marker on the
          client, and pass through to the model anyway. Many prompts in
          the 180K-200K window do succeed; the eval-side check (after
          ``agent.run()`` returns) reads the marker and surfaces a
          deterministic "input too large" error only when content came
          back empty.

Generic across span/trace/session/simulation/voice/custom evals — sits
at the LLM-call boundary, not the eval-type boundary. Multimodal-safe:
messages carrying image/audio/file content are pinned to anchor/recent
regions and never sent to the summarizer.
"""

import json

from ee.falcon_ai.context_manager import ContextManager
from ee.falcon_ai.llm_client import FalconLLMClient

from ee.evals.llm.agent_evaluator.context.budget import (
    COMPACTION_INPUT_CAP_CHARS,
    PER_MESSAGE_CHARS_CAP,
    budgets_for_model,
    has_media,
    split_messages,
    total_tokens,
)
from ee.evals.llm.agent_evaluator.context.digest import attach_digest_to_anchors
from ee.evals.llm.agent_evaluator.context.logging import safe_info, safe_warning
from ee.evals.llm.agent_evaluator.context.prompts import (
    COMPACTION_PROMPT,
    output_format_instruction,
)


class EvalLLMClient(FalconLLMClient):
    """Eval-scoped subclass of ``FalconLLMClient``.

    Reuses ``ContextManager`` for all the heavy lifting (token
    estimation, structural compaction primitives, head+tail truncation
    helpers). Owns only the *wiring*: when each layer runs, in what
    order, and how the layers cascade.

    Single-threaded re-entrancy is guarded by ``_compacting`` so that
    the summarizer LLM call (which itself uses this same client) does
    not recurse into the defense pipeline.
    """

    _CTX = ContextManager()  # shared estimator + helpers; stateless usage

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._compacting = False
        # Iteration-budget pressure (set by the eval caller after init).
        # When ``max_iterations`` is left at None the budget tiers are
        # disabled — the client otherwise behaves identically to before.
        self.max_iterations: int | None = None
        self._iteration_count = 0
        self._iter_caution_warned = False
        self._iter_hardstop_warned = False
        # Output-shape (also set by the eval caller after init). Used by
        # the hardstop hint to tell the agent exactly what form its
        # final answer must take — only the relevant output type is
        # mentioned (no "Pass/Fail or score or choice" spam).
        self.output_type: str | None = None
        self.output_choices: list | None = None
        # Set by the L4 path when the cumulative budget cascade was
        # unable to bring the prompt under the guard. The eval-side
        # check (after agent.run() returns) reads this to decide
        # whether to surface a deterministic "input too large" error
        # or fall through to force-finalize / retry. We do NOT raise
        # at L4 anymore — the LLM call still happens, since prompts
        # in the 180K–200K band sometimes succeed.
        self.last_oversized_attempt: dict | None = None

    # ── Public stream override ────────────────────────────────────────

    async def stream_completion(self, messages, tools=None):
        """Apply the 4-layer defense, then delegate to the parent stream.

        Any unexpected exception in the defense pipeline is logged and
        the original messages are passed through — context management
        must NEVER itself break the eval call.
        """
        if not self._compacting:
            # Iteration-budget pressure (two tiers). The agent loop
            # invokes ``stream_completion`` exactly once per iteration,
            # so counting calls is equivalent to counting iterations.
            # We inject the hint into the message list before forwarding
            # so the model sees it at the start of its next reasoning
            # turn. Each tier fires once.
            try:
                messages = self._maybe_inject_iteration_hint(messages)
            except Exception as exc:
                safe_warning(
                    "eval_iteration_hint_unexpected_error",
                    error=str(exc),
                    error_type=type(exc).__name__,
                )
            try:
                messages = await self._apply_defense(messages)
            except Exception as exc:
                safe_warning(
                    "eval_context_defense_unexpected_error",
                    error=str(exc),
                    error_type=type(exc).__name__,
                )
        async for chunk in super().stream_completion(messages, tools):
            yield chunk

    # ── Iteration-budget hint injection ───────────────────────────────

    def _maybe_inject_iteration_hint(self, messages):
        """Inject a soft system hint when the agent crosses 70% or 90%
        of its iteration budget. Eval-only — Falcon AI chat never sees
        these hints because they live on the wrapper client used only
        by ``AgentEvaluator``.

        Pattern mirrors Falcon's existing token-budget warning at
        ``agent.py``:267 — single-fire, natural language, fires from
        within the LLM client so the agent loop is not modified.
        """
        max_iter = self.max_iterations
        if not max_iter or max_iter <= 1:
            return messages

        # Increment AFTER capturing the value so the hint reflects
        # iterations *completed*, not the one we're about to run.
        completed = self._iteration_count
        self._iteration_count += 1
        ratio = completed / max_iter

        if ratio >= 0.70 and not self._iter_caution_warned:
            self._iter_caution_warned = True
            remaining = max_iter - completed
            safe_info(
                "eval_iteration_budget_caution",
                completed=completed,
                max_iterations=max_iter,
                remaining=remaining,
            )
            messages = list(messages) + [
                {
                    "role": "system",
                    "content": (
                        f"You have {remaining} tool-call step(s) remaining "
                        f"out of {max_iter}. Start consolidating what you "
                        "have learned and prepare a final response. Make "
                        "any last tool calls you truly need, then commit "
                        "to your final answer in text."
                    ),
                }
            ]

        if ratio >= 0.90 and not self._iter_hardstop_warned:
            self._iter_hardstop_warned = True
            safe_warning(
                "eval_iteration_budget_hardstop",
                completed=completed,
                max_iterations=max_iter,
            )
            output_format = output_format_instruction(
                self.output_type, self.output_choices,
                multi_choice=getattr(self, "output_multi_choice", False),
            )
            messages = list(messages) + [
                {
                    "role": "system",
                    "content": (
                        "This is your final step. Do NOT call any more "
                        "tools. Based on everything you have already "
                        "learned, write your final answer now in plain "
                        "text. " + output_format
                    ),
                }
            ]

        return messages

    # ── Defense pipeline orchestrator ─────────────────────────────────

    async def _apply_defense(self, messages):
        """Run layers L1 → L2 → L3a → L3b → L3c → L4 in cascade.

        Always returns a messages list. L4 logs + stashes a marker on
        the client (``last_oversized_attempt``) when the prompt is still
        over the guard, but never raises.
        """
        if not messages:
            return messages

        # Defensive shallow copy of every dict, drop None entries. The
        # downstream layers (L2's truncate_messages in particular) mutate
        # ``msg["content"]`` in place — copying here guarantees we never
        # mutate the agent loop's underlying messages list.
        messages = [
            {**m} if isinstance(m, dict) else m
            for m in messages
            if m is not None
        ]

        # L1: per-message head+tail cap
        messages = [self._cap_single_message(m) for m in messages]

        # L2: per-tool-result cap (defensive, idempotent)
        try:
            messages = self._CTX.truncate_messages(messages)
        except Exception as exc:
            safe_warning(
                "eval_context_l2_truncate_failed",
                error=str(exc),
                error_type=type(exc).__name__,
            )

        # Short-circuit if already under the soft budget
        soft, hard, guard = budgets_for_model(self.model)
        tokens = total_tokens(messages)
        if tokens <= soft:
            return messages

        # L3a: structural strip (no LLM call)
        try:
            messages = self._CTX.light_compact(messages)
        except Exception as exc:
            safe_warning(
                "eval_context_l3a_light_compact_failed",
                error=str(exc),
                error_type=type(exc).__name__,
            )
        tokens = total_tokens(messages)
        if tokens <= soft:
            safe_info(
                "eval_context_l3a_resolved",
                model=self.model, tokens=tokens, soft=soft,
            )
            return messages

        # L3b: LLM summarization of compactable middle
        try:
            messages = await self._summarize_middle(messages)
        except Exception as exc:
            safe_warning(
                "eval_context_l3b_summarize_failed",
                error=str(exc),
                error_type=type(exc).__name__,
            )
        tokens = total_tokens(messages)
        if tokens <= hard:
            return messages

        # L3c: hard-drop oldest non-anchor messages
        try:
            messages = self._drop_oldest_to_fit(messages, target=hard)
        except Exception as exc:
            safe_warning(
                "eval_context_l3c_drop_failed",
                error=str(exc),
                error_type=type(exc).__name__,
            )
        tokens = total_tokens(messages)
        if tokens <= guard:
            return messages

        # L4: cumulative budget cascade couldn't get the prompt under
        # the guard. Soft-fail: log a warning, stash an "oversized
        # attempt" marker on the client, and fall through to the LLM
        # call. Many prompts in the 180K–200K window actually succeed,
        # and pre-emptively rejecting them penalises evals for our
        # pessimism. If the call DOES return empty, the eval-side
        # check (after agent.run()) reads the marker and surfaces a
        # deterministic "input too large" error WITHOUT retrying
        # (deterministic — retry can't help).
        safe_warning(
            "eval_context_oversized_attempt",
            model=self.model,
            tokens=tokens,
            guard=guard,
            msg_count=len(messages),
        )
        self.last_oversized_attempt = {
            "tokens": tokens,
            "guard": guard,
            "msg_count": len(messages),
            "model": self.model,
        }
        return messages

    # ── Layer 1: per-message head+tail cap ────────────────────────────

    @staticmethod
    def _cap_single_message(msg):
        """Head+tail truncate any single message whose string content
        exceeds ``PER_MESSAGE_CHARS_CAP``. Multimodal (list) content
        is left untouched — those parts have their own bounds and we
        must never head+tail base64 payload bytes.
        """
        if not isinstance(msg, dict):
            return msg
        content = msg.get("content")
        if not isinstance(content, str) or len(content) <= PER_MESSAGE_CHARS_CAP:
            return msg
        head_size = PER_MESSAGE_CHARS_CAP // 2
        tail_size = PER_MESSAGE_CHARS_CAP // 4
        head = content[:head_size]
        tail = content[-tail_size:]
        omitted = len(content) - head_size - tail_size
        new_content = (
            f"{head}\n\n"
            f"... [truncated {omitted} chars for context budget] ...\n\n"
            f"{tail}"
        )
        return {**msg, "content": new_content}

    # ── Layer 3b: LLM summarization of compactable middle ─────────────

    async def _summarize_middle(self, messages):
        """Compress middle (non-anchor, non-recent) turns into a digest
        spliced into the first user anchor's content.

        Multimodal messages in the middle region are pinned to the
        recent region — they cannot be safely summarized in text and
        their base64 payloads must not be sent to the summarizer.

        Returns the new messages list. On any failure (summarizer
        empty / errored), returns the messages with only multimodal
        pinning applied so the cascade's next layer can take over.
        """
        anchors, middle, recent = split_messages(messages)

        compactable = []
        pinned = []
        for m in middle:
            (pinned if has_media(m) else compactable).append(m)

        if not compactable:
            return anchors + pinned + recent

        # Serialize compactable middle for the summarizer call. Sanitize
        # media (defense-in-depth — should be unreachable given has_media
        # filter above, but cheap insurance).
        try:
            serialized = json.dumps(
                [self._sanitize_for_compaction(m) for m in compactable],
                default=str,
                indent=2,
            )
        except (TypeError, ValueError):
            serialized = "\n\n".join(
                str(self._sanitize_for_compaction(m)) for m in compactable
            )

        # Cap the summarizer call's own input
        if len(serialized) > COMPACTION_INPUT_CAP_CHARS:
            omitted = len(serialized) - COMPACTION_INPUT_CAP_CHARS
            serialized = (
                serialized[:COMPACTION_INPUT_CAP_CHARS]
                + f"\n... [+{omitted} chars truncated to keep "
                "compaction call bounded]"
            )

        compaction_msgs = [
            {"role": "system", "content": COMPACTION_PROMPT},
            {"role": "user", "content": serialized},
        ]

        # Run the summarizer through this same client. Clear
        # response_format (summarizer returns plain text) and tools
        # (no tool use during summarization). Re-entrancy guard
        # prevents the summarizer call from re-entering the defense.
        saved_response_format = self.response_format
        self.response_format = None
        self._compacting = True
        digest = ""
        try:
            async for chunk in super().stream_completion(
                compaction_msgs, tools=None,
            ):
                for choice in chunk.get("choices") or []:
                    delta = choice.get("delta") or {}
                    if delta.get("content"):
                        digest += delta["content"]
        finally:
            self._compacting = False
            self.response_format = saved_response_format

        if not digest.strip():
            safe_warning(
                "eval_context_summarize_empty_digest",
                middle_msgs=len(compactable),
            )
            return anchors + pinned + recent

        new_anchors = attach_digest_to_anchors(anchors, digest.strip())
        result = new_anchors + pinned + recent

        safe_info(
            "eval_context_summarized",
            model=self.model,
            tokens_before=total_tokens(messages),
            tokens_after=total_tokens(result),
            middle_msgs_summarized=len(compactable),
            multimodal_msgs_pinned=len(pinned),
            digest_chars=len(digest),
        )
        return result

    # ── Layer 3c: hard-drop oldest non-anchor pairs ───────────────────

    def _drop_oldest_to_fit(self, messages, target: int):
        """Drop oldest middle messages until total ≤ ``target``.

        Drops in matched assistant-tool_call / tool-result pairs so
        we never leave a tool-result message without its parent
        assistant tool_call entry — providers error on that imbalance.

        Anchors (system + first user) and the recent region are never
        dropped.
        """
        anchors, middle, recent = split_messages(messages)
        if not middle:
            return messages

        kept = list(middle)
        while (
            total_tokens(anchors + kept + recent) > target
            and kept
        ):
            head = kept[0]
            drop_count = 1
            if (
                isinstance(head, dict)
                and head.get("role") == "assistant"
                and head.get("tool_calls")
            ):
                # Only collect non-empty / non-None tool_call ids so we
                # never falsely match an orphaned tool message whose
                # ``tool_call_id`` is missing or empty (would otherwise
                # match because ``None in {None}`` is True).
                tc_ids = {
                    tc.get("id")
                    for tc in head["tool_calls"]
                    if isinstance(tc, dict) and tc.get("id")
                }
                if tc_ids:
                    for nxt in kept[1:]:
                        nxt_id = (
                            nxt.get("tool_call_id")
                            if isinstance(nxt, dict)
                            else None
                        )
                        if (
                            isinstance(nxt, dict)
                            and nxt.get("role") == "tool"
                            and nxt_id
                            and nxt_id in tc_ids
                        ):
                            drop_count += 1
                        else:
                            break
            kept = kept[drop_count:]
        return anchors + kept + recent

    # ── Sanitization helper for the summarizer's input ────────────────

    @staticmethod
    def _sanitize_for_compaction(msg):
        """Replace any media payloads inside ``content`` with text
        placeholders before serializing for the summarizer. Should be
        unreachable given the ``has_media`` filter in ``_summarize_middle``
        — kept as defense-in-depth so a future bypass of that filter
        cannot send raw base64 bytes to the summarizer.
        """
        if not isinstance(msg, dict):
            return msg
        content = msg.get("content")
        if not isinstance(content, list):
            return msg
        new_content = []
        for part in content:
            if isinstance(part, dict):
                ptype = part.get("type")
                if ptype == "image_url":
                    new_content.append(
                        {"type": "text", "text": "[image attached]"},
                    )
                    continue
                if ptype in ("input_audio", "audio"):
                    new_content.append(
                        {"type": "text", "text": "[audio attached]"},
                    )
                    continue
                if ptype == "file":
                    new_content.append(
                        {"type": "text", "text": "[file attached]"},
                    )
                    continue
            new_content.append(part)
        return {**msg, "content": new_content}
