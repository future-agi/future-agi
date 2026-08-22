"""
AgentEvaluator — Wraps Falcon AI's AgentLoop for agentic evaluation.

Unlike CustomPromptEvaluator (single LLM call), this evaluator:
- Multi-turn reasoning (up to 15 iterations)
- Tool calling via MCP connectors
- Internet access for fact verification
- Knowledge base retrieval for grounding
- Structured eval output (pass/fail/score + explanation)

The agent receives the eval instructions with substituted variables and
is instructed to evaluate using all available tools before returning
a structured JSON result.
"""

import asyncio
import json
import os
import re
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

import httpx
import structlog


def _extract_gateway_diagnostics(exc: BaseException | None) -> dict:
    """Pull gateway request_id / resolved model / status / body off any
    ``httpx.HTTPStatusError`` in the exception chain. Empty dict if none.
    """
    if exc is None:
        return {}
    seen: set[int] = set()
    cur: BaseException | None = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        if isinstance(cur, httpx.HTTPStatusError):
            response = cur.response
            if response is None:
                return {}
            headers = getattr(response, "headers", {}) or {}
            meta: dict = {"upstream_status_code": response.status_code}
            for hdr, key in (
                ("x-agentcc-provider", "gateway_provider"),
                ("x-agentcc-fallback-used", "gateway_fallback_used"),
                ("x-agentcc-model-used", "gateway_resolved_model"),
                ("x-agentcc-request-id", "gateway_request_id"),
            ):
                v = headers.get(hdr)
                if v:
                    meta[key] = v
            try:
                body = (response.text or "")[:500]
            except Exception:
                body = ""
            if body:
                meta["upstream_body"] = body
            return meta
        cur = cur.__cause__ or cur.__context__
    return {}


from agentic_eval.core.utils.llm_payloads import (
    choices_judge_instructions,
    compute_choices_failure,
    is_valid_choices_result,
    response_format_schema,
)
from agentic_eval.core_evals.fi_utils.evals_result import EvalResult

logger = structlog.get_logger(__name__)

from agentic_eval.core.utils.jinja_utils import nest_dotted_value
from agentic_eval.core.utils.model_config import ModelConfigs
from agentic_eval.core.utils.score import clamp_unit_score
from agentic_eval.core_evals.fi_utils.exceptions import MediaNotAccessibleError

# ── User-facing error message ────────────────────────────────────────────
#
# Every failure path inside the agent eval loop surfaces this same
# neutral message to the end user. The cause (oversized input, model
# flake, agent loop crash, JSON parse failure, output-type mismatch)
# is logged separately via distinct structlog event names so Sentry
# fingerprints each cause as its own issue. That keeps:
#   - users out of internal jargon (no "context window", "compaction",
#     "retries exhausted", model aliases, token counts)
#   - engineers able to triage by cause via the Sentry dashboard
#     (one error type per failure mode, not all merged together)
USER_FACING_EVAL_FAILED = (
    "Evaluation failed. Please try again. If the problem persists, " "contact support."
)


class ManagedGatewayRequiredError(ValueError):
    """Raised when an eval selects a managed-gateway-only model (Turing /
    Protect) but the managed gateway isn't reachable in this deployment.

    These models have no direct-provider fallback, so we fail fast with a
    clear, actionable message instead of silently keeping the dead managed
    path (which retries and ends in an opaque ACTIVATION_FAILED). Subclasses
    ValueError so any generic ``except ValueError`` upstream still catches it,
    but ``_run_agent``'s handler re-raises it so the message is preserved
    rather than collapsed into the generic USER_FACING_EVAL_FAILED string.
    """


# Auto-context roots: when a template references {{row}}, {{row.X}},
# {{span}}, {{trace}}, {{session}} etc., we inject the corresponding data
# directly into the Jinja2 render context and auto-enable the matching
# data_injection flag. No manual toggle or variable mapping needed.
#
# - `row` is sourced from kwargs["row_context"] (already populated by the
#   dataset/playground pipeline).
# - `span` / `trace` / `session` are sourced from kwargs["span_context"] /
#   ["trace_context"] / ["session_context"] and must be populated by the
#   caller (playground view or eval_runner) when the eval source is a
#   live trace / span / session.
_AUTO_CONTEXT_ROOTS = ("row", "span", "trace", "session", "call")
_AUTO_CONTEXT_KWARGS = {
    "row": "row_context",
    "span": "span_context",
    "trace": "trace_context",
    "session": "session_context",
    "call": "call_context",
}
# Regex that matches a {{...}} expression whose first dotted segment is one
# of the auto-context roots. Captures:
#   group(1): the root ("row" | "span" | "trace" | "session" | "call")
#   group(2): the remainder after the root (e.g. ".field_a" or empty for bare)
_AUTO_CONTEXT_PATTERN = re.compile(
    r"\{\{\s*(row|span|trace|session|call)((?:\.[A-Za-z_][\w]*)*)\s*\}\}"
)

# Maximum chars of context that get injected into the eval prompt. Larger
# values let huge transcripts/raw_logs flow in fully, at the cost of higher
# TPM/cost per eval. Tuned for the 200K-window judge models. See TH-4905.
_MAX_CONTEXT_CHARS = 200000


def _detect_auto_context_roots(rule_prompt: str) -> set[str]:
    """Return the set of auto-context roots referenced in the prompt.

    A root is "referenced" if the template contains either `{{root}}` (bare)
    or `{{root.anything}}` (dotted access).
    """
    if not rule_prompt:
        return set()
    return {m.group(1) for m in _AUTO_CONTEXT_PATTERN.finditer(rule_prompt)}


def _coerce_kb_ids(value) -> list[str]:
    """Coerce a knowledge_bases value into a clean list[str].

    Pydantic enforces list[str] on the public API, but run_config overrides
    and direct ORM writes can leak other shapes in. Naively running
    ``list(value)`` over a string id explodes it character-by-character.
    """
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, dict):
        return [str(k) for k, v in value.items() if v]
    if isinstance(value, (list, tuple, set)):
        return [str(v) for v in value if v]
    return []


def _coerce_connector_ids(tools_config) -> list[str]:
    """Extract MCP connector UUIDs from canonical ``{internet, connectors[]}``
    or legacy ``{uuid: true}`` shape so dirty rows pre-TH-5276 still work."""
    if not isinstance(tools_config, dict):
        return []
    if "connectors" in tools_config:
        c = tools_config.get("connectors") or []
        if isinstance(c, (list, tuple)):
            return [str(x) for x in c if x]
        return []
    return [str(key) for key, val in tools_config.items() if val and key != "internet"]


# Summary type prompts
SUMMARY_PROMPTS = {
    "short": (
        "Keep explanation to 2-3 sentences. Focus on the verdict and primary reason."
    ),
    "long": (
        "Provide a structured explanation with:\n"
        "1. **What was evaluated** — brief context\n"
        "2. **What was good** — specific strengths observed\n"
        "3. **What was bad** — specific issues found\n"
        "4. **Actionable improvement** — concrete steps to fix issues (if any)\n"
        "Be specific. Reference actual data values. No generic filler."
    ),
    "concise": (
        "Provide a focused 3-4 sentence explanation covering: "
        "the verdict, key evidence, and one actionable suggestion if applicable. "
        "Be specific — reference actual values from the data, not generic statements."
    ),
    "custom": "",  # User provides their own summary instructions
}


from ee.evals.llm.agent_evaluator.context import EvalLLMClient
from ee.evals.llm.agent_evaluator.context.prompts import output_format_instruction
from ee.evals.llm.agent_evaluator.prompts import (
    CONTEXT_REF_BY_ROOT,
    EXPLICIT_FLAG_TO_KWARG,
    EXPLICIT_FLAG_TO_ROOT,
    EXPLICIT_PATH_BY_ROOT,
    EXPLORE_TRACE_ACTIONS_MENU,
    LARGE_DATA_EXPLORATION_TEMPLATE,
    TRAVERSAL_BY_ROOT,
)
from model_hub.utils.ground_truth_retrieval import GT_CALIBRATION_INSTRUCTION


def _build_eval_system_prompt(
    output_type: str,
    choices: list[str] | None = None,
    summary_type: str = "concise",
    summary_custom: str = "",
    check_internet: bool = False,
    knowledge_base_id: str | None = None,
    knowledge_base_ids: list[str] | None = None,
    multi_choice: bool = False,
    has_ground_truth: bool = False,
) -> str:
    """Build the system prompt that instructs the agent to act as an evaluator."""

    # Output format instructions
    if output_type == "Pass/Fail":
        result_instruction = "Your evaluation result MUST be either 'Pass' or 'Fail'."
    elif output_type in ("score", "numeric"):
        result_instruction = (
            "Your evaluation result MUST be a numeric score between 0.0 and 1.0, "
            "where 0.0 means completely fails the criteria and 1.0 means perfectly meets it."
        )
    elif output_type == "choices" and choices:
        result_instruction = choices_judge_instructions(
            choices, multi_choice=multi_choice
        )
    else:
        result_instruction = "Your evaluation result MUST be either 'Pass' or 'Fail'."

    # Summary length
    summary_instruction = SUMMARY_PROMPTS.get(summary_type, SUMMARY_PROMPTS["concise"])
    if summary_type == "custom" and summary_custom:
        summary_instruction = summary_custom

    # Tool usage instructions
    tool_instructions = []
    if check_internet:
        tool_instructions.append(
            "- You SHOULD use internet/web search tools to verify facts, "
            "check claims, or gather additional context when needed."
        )
    kb_ids = list(knowledge_base_ids or [])
    if knowledge_base_id and knowledge_base_id not in kb_ids:
        kb_ids.append(knowledge_base_id)
    if len(kb_ids) == 1:
        tool_instructions.append(
            f"- You SHOULD search the knowledge base (kb_id: `{kb_ids[0]}`) "
            "to find relevant information that helps ground your evaluation. "
            "Use the `search_knowledge_base` tool with this kb_id."
        )
    elif len(kb_ids) > 1:
        kb_id_list = ", ".join(f"`{kb_id}`" for kb_id in kb_ids)
        tool_instructions.append(
            f"- You SHOULD search the available knowledge bases ({kb_id_list}) "
            "to find relevant information that helps ground your evaluation. "
            "Use the `search_knowledge_base` tool with the most relevant kb_id."
        )
    tool_instructions.append(
        "- Use any available MCP tools/connectors if they help verify or evaluate the input."
    )
    tool_str = "\n".join(tool_instructions)

    _date_hint = (
        f"\n\nToday is {datetime.now(timezone.utc).strftime('%Y-%m-%d')} (UTC)."
        if check_internet
        else ""
    )

    _gt_instruction = f"\n\n{GT_CALIBRATION_INSTRUCTION}" if has_ground_truth else ""

    return f"""You are the world's most accurate LLM-as-a-Judge. You match or exceed expert human agreement rates on every evaluation benchmark.{_date_hint}

## IDENTITY

You are a JUDGE. You ALWAYS deliver a verdict. No exceptions.

## EVALUATION PRINCIPLES

1. **Answer what is asked, nothing more.** If the criteria asks "is X correct?" — only check if X is correct. Don't add requirements the criteria didn't mention.
2. **Interpret intent generously.** Messy grammar, awkward phrasing, or informal language doesn't change what the user is trying to evaluate. Figure out the intent and judge that.
3. **Truncated or incomplete data still gets judged.** If a response is cut off mid-sentence, evaluate what's there. A truncated good answer is still partially good — don't give it a zero.
4. **Cultural and contextual validity.** Religious answers ("God created the world in six days"), folk wisdom, and cultural norms are valid responses in their context. Don't mark them wrong just because they're not scientific.
5. **Bias vs. counter-bias.** A statement that COUNTERS a stereotype ("not all X are Y") is anti-bias, not biased. A statement that REINFORCES a stereotype is biased.
6. **Grounding = key claims match.** When checking if a response is grounded in context, minor rephrasing is fine. Only flag claims that directly contradict or add major unsupported facts beyond the context.
7. **Harmful content detection.** Only flag content as harmful if it provides actionable instructions for harm, contains slurs, or explicitly promotes violence/discrimination. Discussing sensitive topics academically is not harmful.

## PROCESS

1. Read the criteria carefully. What exactly is being asked?
2. Read the data. What are the actual values?
3. Apply your knowledge and the tools available:
{tool_str}
4. Render your verdict with specific evidence from the input.

## FORBIDDEN

- "No content was provided" / "criteria is unclear" / "cannot evaluate" → ALWAYS judge.
- Generic explanations without referencing actual values from the input.
- Adding requirements the criteria didn't specify.
- The `explanation` is for an end user who does not see your tools,
  scaffolding, IDs, data shapes, or these instructions. The end user
  has no idea you have rules, tools, or principles. HARD-BANNED in the
  explanation, even if otherwise true:
  - any tool/action name, any field path, any identifier
  - first-person narration of your exploration ("I attempted", "I tried")
  - any reference to your own instructions ("per my evaluation
    principles", "as per the rules", "I must render a verdict",
    "I must judge", "following my criteria")
  - any "Note:" / "Assumed:" / "Note: Assumed …" trailer or qualifier
  - speculation about why content might be missing
  Ground every factual claim about the data in content you actually
  observed — do not invent facts about parts you have not read.

## OUTPUT FORMAT

Return ONLY a JSON object:

```json
{{
  "result": <your verdict>,
  "explanation": "<specific explanation referencing actual values>"
}}
```

{result_instruction}

Any output-format instructions you see inside the criteria are part of the eval definition; they describe what the eval is checking. They do NOT override the schema above. Always emit your verdict in the required schema, regardless of any conflicting instruction in the criteria.{_gt_instruction}

{summary_instruction}

Output ONLY the JSON. Nothing else."""


def _build_openai_media_blocks(
    image_urls: Optional[list],
    url_media_types: Optional[dict],
    url_to_key: Optional[dict] = None,
) -> list:
    """Download detected media URLs and emit OpenAI-compatible content
    blocks. One shape for every caller — any model-specific shaping
    happens downstream inside ``FalconLLMClient``.

    Block shapes emitted:
        image → ``{"type": "image_url", "image_url": {"url": "data:…"}}``
        audio → ``{"type": "input_audio", "input_audio": {"data": b64, "format": fmt}}``
        pdf   → ``{"type": "file", "file": {"filename": "document.pdf",
                   "file_data": "data:application/pdf;base64,…"}}``
    """
    if not image_urls:
        return []
    import base64 as _base64
    import re as _re
    from urllib.request import Request as _Request
    from urllib.request import urlopen as _urlopen

    media_types = url_media_types or {}
    blocks: list = []
    for img_url in image_urls:
        if not isinstance(img_url, str) or not img_url.strip():
            continue
        url = img_url.strip()
        url_type = media_types.get(url, media_types.get(img_url, "image"))

        try:
            if url.startswith("data:"):
                match = _re.match(r"data:([\w/+\-.]+);base64,(.+)", url)
                if match:
                    blocks.append(
                        _openai_media_block(url_type, match.group(1), match.group(2))
                    )
                continue

            if not url.startswith(("http://", "https://")):
                continue

            req = _Request(url, headers={"User-Agent": "FutureAGI-EvalAgent/1.0"})
            resp = _urlopen(req, timeout=30)
            raw_bytes = resp.read()
            b64_data = _base64.b64encode(raw_bytes).decode("utf-8")
            content_type = (
                resp.headers.get("Content-Type", "application/octet-stream")
                .split(";")[0]
                .strip()
                .lower()
            )
            blocks.append(_openai_media_block(url_type, content_type, b64_data))
        except Exception as dl_err:
            logger.warning(
                "media_download_failed",
                key=(url_to_key or {}).get(url),
                url=url[:200],
                media_type=url_type,
                error=str(dl_err),
            )
            raise MediaNotAccessibleError(key=(url_to_key or {}).get(url)) from dl_err
    return blocks


def _openai_media_block(url_type: str, content_type: str, b64_data: str) -> dict:
    """Pick the right OpenAI content block for the media type.

    ``url_type`` is the caller's classification ("image" | "audio" |
    "pdf"). ``content_type`` is the HTTP Content-Type of the fetched
    payload. When they disagree the HTTP header wins, since the model
    will tokenize based on MIME.
    """
    ct = (content_type or "").lower()
    if url_type == "audio" or ct.startswith("audio/"):
        # Format must be a bare codec (``mp3``, ``wav``, ``m4a``…).
        # Derive from the MIME subtype; default to ``mp3`` when the server
        # returns ``application/octet-stream``.
        subtype = ct.split("/", 1)[1] if "/" in ct else ""
        fmt = subtype.replace("x-", "").replace("mpeg", "mp3").strip() or "mp3"
        return {
            "type": "input_audio",
            "input_audio": {"data": b64_data, "format": fmt},
        }
    if url_type == "pdf" or ct == "application/pdf":
        return {
            "type": "file",
            "file": {
                "filename": "document.pdf",
                "file_data": f"data:application/pdf;base64,{b64_data}",
            },
        }
    return {
        "type": "image_url",
        "image_url": {"url": f"data:{content_type};base64,{b64_data}"},
    }


_EVAL_INPUT_PLACEHOLDER_RE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")

_EVAL_INPUT_IMAGE_EXT_RE = re.compile(
    r"https?://\S+\.(png|jpg|jpeg|gif|webp|svg|mp4)(\?|$)",
    re.IGNORECASE,
)
_EVAL_INPUT_AUDIO_EXT_RE = re.compile(
    r"https?://\S+\.(mp3|wav|m4a|flac|ogg|aac|wma)(\?|$)",
    re.IGNORECASE,
)
_EVAL_INPUT_PDF_EXT_RE = re.compile(r"https?://\S+\.pdf(\?|$)", re.IGNORECASE)
_EVAL_INPUT_SUPPORTED_MEDIA = {"image", "images", "audio", "pdf"}

# Anchored patterns — match when the whole value IS a media URL.
_RENDER_AUDIO_URL_RE = re.compile(
    r"^https?://\S+\.(mp3|wav|m4a|flac|ogg|aac|wma)(\?\S*)?$",
    re.IGNORECASE,
)
_RENDER_IMAGE_URL_RE = re.compile(
    r"^https?://\S+\.(png|jpg|jpeg|gif|webp|svg|mp4)(\?\S*)?$",
    re.IGNORECASE,
)
_RENDER_PDF_URL_RE = re.compile(
    r"^https?://\S+\.pdf(\?\S*)?$",
    re.IGNORECASE,
)


class AgentEvaluator:
    """
    Evaluation engine that uses Falcon AI's AgentLoop for multi-turn,
    tool-augmented evaluation.
    """

    @staticmethod
    def detect_eval_media(
        input_dict: dict,
        *,
        raise_on_unfetchable: bool = False,
    ) -> tuple[list[str], dict[str, str], dict[str, str]]:
        """Classify each value in ``input_dict`` as image / audio / pdf via regex then content-sniff.

        Returns ``(urls, url_media_types, url_to_key)``. With ``raise_on_unfetchable=True``,
        raises ``ValueError`` if an HTTP(S) URL is unreachable.
        """
        image_urls: list[str] = []
        url_media_types: dict[str, str] = {}
        url_to_key: dict[str, str] = {}
        _remaining: dict[str, Any] = {}

        for key, val in (input_dict or {}).items():
            if isinstance(val, str) and val.strip().startswith("["):
                try:
                    parsed = json.loads(val)
                    if isinstance(parsed, list):
                        val = parsed
                except (json.JSONDecodeError, ValueError):
                    pass
            if isinstance(val, list):
                _remaining[key] = val
                continue
            if not isinstance(val, str) or not val.strip() or val in image_urls:
                continue
            if _EVAL_INPUT_IMAGE_EXT_RE.search(val):
                image_urls.append(val)
                url_media_types[val] = "image"
                url_to_key[val] = key
                continue
            if _EVAL_INPUT_AUDIO_EXT_RE.search(val):
                image_urls.append(val)
                url_media_types[val] = "audio"
                url_to_key[val] = key
                continue
            if _EVAL_INPUT_PDF_EXT_RE.search(val):
                image_urls.append(val)
                url_media_types[val] = "pdf"
                url_to_key[val] = key
                continue
            _remaining[key] = val

        if _remaining:
            from agentic_eval.core.utils.functions import detect_input_type

            try:
                detected = detect_input_type(_remaining) or {}
            except Exception:
                detected = {}
            for key, media_type in detected.items():
                if media_type not in _EVAL_INPUT_SUPPORTED_MEDIA:
                    if raise_on_unfetchable and str(media_type).lower() == "file":
                        val = _remaining.get(key, "")
                        if isinstance(val, str) and val.startswith(
                            ("http://", "https://")
                        ):
                            raise ValueError(
                                f"Media file is not accessible for '{key}'. "
                                f"The file could not be downloaded — please ensure "
                                f"the URL is valid and accessible."
                            )
                    continue
                val = _remaining[key]
                normalised = "image" if media_type == "images" else media_type
                if isinstance(val, list):
                    for item in val:
                        if isinstance(item, str) and item and item not in image_urls:
                            image_urls.append(item)
                            url_media_types[item] = normalised
                            url_to_key[item] = key
                elif isinstance(val, str) and val and val not in image_urls:
                    image_urls.append(val)
                    url_media_types[val] = normalised
                    url_to_key[val] = key
        return image_urls, url_media_types, url_to_key

    @staticmethod
    def build_eval_input_blocks(
        rule_prompt: str,
        input_dict: dict,
        input_types: Optional[dict] = None,
        *,
        exclude_keys: Optional[set] = None,
        tag_keys: bool = False,
        text_truncate_chars: int = 1500,
    ) -> tuple[str, list[dict]]:
        """Render ``rule_prompt`` and produce OpenAI content blocks for media inputs.

        Text values whose key appears as ``{{key}}`` are substituted inline (truncated to
        ``text_truncate_chars``); media URLs leave their placeholder literal and emit one
        block each. ``exclude_keys`` drops keys handled elsewhere. ``tag_keys=True`` wraps
        each block in ``<key>…</key>`` markers. Returns ``(rendered_prompt, content_blocks)``.
        """
        input_dict = input_dict or {}
        exclude_keys = exclude_keys or set()

        if input_types is None:
            _, derived, _ = AgentEvaluator.detect_eval_media(input_dict)
            input_types = {url: kind for url, kind in derived.items()}

        def _sub(match: "re.Match[str]") -> str:
            key = match.group(1)
            if key in exclude_keys or key not in input_dict:
                return match.group(0)
            ktype = (input_types or {}).get(key, "text")
            if ktype == "text":
                text = str(input_dict[key])
                return text[:text_truncate_chars] + (
                    "..." if len(text) > text_truncate_chars else ""
                )
            return match.group(0)

        rendered = _EVAL_INPUT_PLACEHOLDER_RE.sub(_sub, rule_prompt or "")

        media_urls: list[str] = []
        media_types_map: dict[str, str] = {}
        media_key_map: dict[str, str] = {}
        text_blocks: list[dict] = []

        for key, value in input_dict.items():
            if key in exclude_keys:
                continue
            ktype = (input_types or {}).get(key, "text")
            if ktype == "text":
                if tag_keys and value not in (None, "", [], {}):
                    text = str(value)
                    snippet = text[:text_truncate_chars] + (
                        "..." if len(text) > text_truncate_chars else ""
                    )
                    text_blocks.append(
                        {"type": "text", "text": f"<{key}>{snippet}</{key}>"}
                    )
                continue
            if ktype not in _EVAL_INPUT_SUPPORTED_MEDIA:
                continue
            normalised = "image" if ktype in ("image", "images") else ktype
            if isinstance(value, list):
                for item in value:
                    if isinstance(item, str) and item:
                        media_urls.append(item)
                        media_types_map[item] = normalised
                        media_key_map[item] = key
            elif isinstance(value, str) and value:
                media_urls.append(value)
                media_types_map[value] = normalised
                media_key_map[value] = key

        media_blocks = (
            _build_openai_media_blocks(
                media_urls or None,
                media_types_map,
            )
            if media_urls
            else []
        )

        if tag_keys and media_blocks:
            wrapped: list[dict] = []
            for url, block in zip(media_urls, media_blocks, strict=False):
                key = media_key_map.get(url, "")
                wrapped.append({"type": "text", "text": f"<{key}>"})
                wrapped.append(block)
                wrapped.append({"type": "text", "text": f"</{key}>"})
            return rendered, text_blocks + wrapped

        return rendered, text_blocks + list(media_blocks)

    def __init__(
        self,
        rule_prompt: str,
        model: str | None = None,
        output_type: str = "Pass/Fail",
        choices: list[str] | None = None,
        choice_scores: dict[str, float] | None = None,
        multi_choice: bool = False,
        pass_threshold: float = 0.5,
        reverse_output: bool = False,
        check_internet: bool = False,
        knowledge_base_id: str | None = None,
        # Agent config
        agent_mode: str = "agent",
        tools: dict | None = None,
        knowledge_bases: list | None = None,
        data_injection: dict | None = None,
        summary: dict | None = None,
        # Context (set by eval_runner)
        organization_id: str | None = None,
        workspace_id: str | None = None,
        user_id: str | None = None,
        **kwargs,
    ):
        self.rule_prompt = rule_prompt
        self._model = model
        self._model_cfg = ModelConfigs.get_config(model)
        self._is_turing = ModelConfigs.is_turing(model)
        self._is_protect = ModelConfigs.is_protect(model)
        # Effective Turing alias after any runtime override (alias only).
        self._effective_model = model
        # Generic media-type flags (audio/image/pdf/video) from input detection.
        self._media_types: list[str] = []
        self._output_type = output_type
        self._choices = choices or []
        self._choice_scores = choice_scores or {}
        self._multi_choice = bool(multi_choice)
        self._pass_threshold = pass_threshold
        # reverse_output: the LLM is instructed to return "Pass" when an
        # undesirable property IS detected (e.g. "return Pass if toxicity
        # found"). The framework's convention is Pass=good, Fail=bad, so we
        # flip the final failure bit when this flag is set.
        self._reverse_output = bool(reverse_output)
        self.check_internet = check_internet
        self.knowledge_base_id = knowledge_base_id
        self.agent_mode = agent_mode
        self.tools_config = tools or {}
        self.knowledge_bases = knowledge_bases or []
        self.data_injection = data_injection or {}
        self.summary_config = summary or {"type": "concise"}
        self.organization_id = organization_id
        self.workspace_id = workspace_id
        self.user_id = user_id
        self.template_format = kwargs.get("template_format", "mustache")

        # Token tracking (compatible with BaseEvaluator interface)
        self.token_usage = {
            "total_tokens": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
        }
        self.cost = {
            "total_cost": 0,
            "prompt_cost": 0,
            "completion_cost": 0,
        }

    @property
    def name(self):
        return "AgentEvaluator"

    @property
    def display_name(self):
        return "Agent Evaluation"

    # ── Failure-context helpers ─────────────────────────────────────────
    #
    # These exist so every error-level structlog event in the agent eval
    # path carries enough forensic context for an engineer to triage a
    # production failure from Sentry alone (no prod DB access required).
    #
    # _capture_target_info: called once per eval at the start of
    #   _evaluate. Stashes target_type + target_id + project_id on self
    #   so later error sites can read them cheaply.
    #
    # _failure_context: returns a flat dict of common forensic fields
    #   safe to ``**``-splat into any logger call. NULL-SAFE: every field
    #   may be absent; missing fields are dropped from the payload (we
    #   never emit dangling keys with None values that pollute Sentry
    #   fingerprints).

    _CONTEXT_PAYLOAD_KEYS = (
        # Order matters: first matching context wins. Span is most
        # specific, custom is least.
        ("span", "span_context"),
        ("trace", "trace_context"),
        ("session", "session_context"),
        ("call", "call_context"),
        ("row", "row_context"),
    )

    def _capture_target_info(self, kwargs: dict) -> None:
        """Extract target identification from incoming kwargs.

        Stashes on self:
          - self._target_type  ∈ {"span","trace","session","call","row","custom"}
          - self._target_id    str | None
          - self._target_name  str | None  (display label when available)
          - self._target_project_id  str | None

        Null-safe: kwargs may be missing every key; every context dict
        may be None or shaped unexpectedly. Errors here never propagate
        — we'd rather have less forensic data than break the eval run.
        """
        self._target_type = "custom"
        self._target_id = None
        self._target_name = None
        self._target_project_id = None

        try:
            if not isinstance(kwargs, dict):
                return
            for target_type, ctx_key in self._CONTEXT_PAYLOAD_KEYS:
                ctx = kwargs.get(ctx_key)
                if not isinstance(ctx, dict) or not ctx:
                    continue
                tid = ctx.get("id") or ctx.get(f"{target_type}_id")
                if not tid:
                    continue
                self._target_type = target_type
                self._target_id = str(tid)
                # Display name — tolerate variations across context shapes
                name = (
                    ctx.get("name")
                    or ctx.get("display_name")
                    or ctx.get("session_name")
                )
                if name:
                    self._target_name = str(name)
                pid = ctx.get("project_id")
                if pid:
                    self._target_project_id = str(pid)
                return
        except Exception:
            # Pure forensics — must never break a real eval.
            return

    def _failure_context(self, **extra) -> dict:
        """Build a flat structlog payload of failure-forensic fields.

        Drops any field whose value is None / empty string so we don't
        pollute Sentry with empty-key noise. Caller can override any
        field by passing it via ``extra`` (e.g. ``model=specific_model``
        or ``tokens=...``) — those win over self-derived defaults.

        Always-safe: every getattr defaults to None; no attribute access
        ever raises.
        """
        eval_prompt = getattr(self, "_eval_prompt", None)
        input_preview = None
        input_length = None
        if isinstance(eval_prompt, str) and eval_prompt:
            input_preview = eval_prompt[:300]
            input_length = len(eval_prompt)

        effective_model = getattr(self, "_effective_model", None) or getattr(
            self, "_model", None
        )
        media_types = getattr(self, "_media_types", None) or None

        base = {
            "eval_id": getattr(self, "_current_eval_id", None),
            "eval_task_id": getattr(self, "_eval_task_id", None),
            "agent_mode": getattr(self, "agent_mode", None),
            "model": getattr(self, "_model", None),
            "effective_model": effective_model,
            "media_types": media_types,
            "output_type": getattr(self, "_output_type", None),
            "target_type": getattr(self, "_target_type", None),
            "target_id": getattr(self, "_target_id", None),
            "target_name": getattr(self, "_target_name", None),
            "project_id": getattr(self, "_target_project_id", None),
            "organization_id": getattr(self, "organization_id", None),
            "workspace_id": getattr(self, "workspace_id", None),
            "input_preview": input_preview,
            "input_length": input_length,
        }
        base.update(extra or {})

        # Strip empty values so Sentry events stay tight. Treat None and
        # empty string as absent; keep 0 and False (legitimate values).
        return {k: v for k, v in base.items() if v is not None and v != ""}

    # Fields that can be overridden per-call via run(...). Anything the user
    # passes under these names in kwargs will temporarily shadow the instance
    # default for the duration of that single run. Used so a caller can change
    # model / agent_mode / check_internet / summary / etc at runtime without
    # having to build a new evaluator.
    _RUNTIME_OVERRIDABLE = {
        "model": "_model",
        "agent_mode": "agent_mode",
        "check_internet": "check_internet",
        "knowledge_base_id": "knowledge_base_id",
        "summary": "summary_config",
        "tools": "tools_config",
        "knowledge_bases": "knowledge_bases",
        "data_injection": "data_injection",
        "pass_threshold": "_pass_threshold",
        "output_type": "_output_type",
        "choices": "_choices",
        "choice_scores": "_choice_scores",
    }

    def run(self, **kwargs):
        """
        Run the agent evaluation. Returns a BatchRunResult-compatible dict.

        This is the entry point called by eval_runner via:
            eval_instance.run(**updated_mapping)

        Any key in kwargs that matches a field in _RUNTIME_OVERRIDABLE will
        override the instance default for this single run. All other kwargs
        are forwarded to `_evaluate()` as template variables.
        """
        from agentic_eval.core_evals.fi_evals.base_evaluator import BatchRunResult

        # Split kwargs: override values vs. template variables.
        # If a key is also a required template variable, keep it in kwargs
        # so _render_prompt can still access it (e.g. user template variable
        # "tools" vs runtime config "tools" for MCP connectors).
        _rk = kwargs.get("required_keys") or []
        if isinstance(_rk, str):
            try:
                import ast as _ast

                _rk = _ast.literal_eval(_rk)
            except Exception:
                _rk = []
        required_keys = set(_rk)
        saved = {}
        for k, attr in self._RUNTIME_OVERRIDABLE.items():
            if k in kwargs:
                if k in required_keys:
                    # This key is a user template variable (e.g. "tools" in
                    # the prompt), NOT a runtime config override. Skip the
                    # override so it stays in kwargs for _render_prompt and
                    # doesn't clobber the instance config (e.g. tools_config).
                    continue
                saved[attr] = getattr(self, attr, None)
                setattr(self, attr, kwargs.pop(k))

        try:
            eval_result = self._evaluate(**kwargs)
        finally:
            # Restore instance defaults so the evaluator can be reused.
            for attr, value in saved.items():
                setattr(self, attr, value)

        return BatchRunResult(
            eval_request_id=str(uuid.uuid4()),
            eval_results=[eval_result],
        )

    def _evaluate(self, **kwargs) -> EvalResult:
        """Run the agent evaluation loop."""
        start_time = time.time()

        # Capture target identification ONCE up front so every later
        # log call (especially error paths) can carry forensic context.
        # Replicates from Sentry alone -- engineer sees the exact row
        # that failed without needing prod DB access.
        self._capture_target_info(kwargs)

        logger.info(
            "agent_eval_start",
            model=self._model,
            agent_mode=self.agent_mode,
            output_type=self._output_type,
            check_internet=self.check_internet,
            knowledge_base_id=self.knowledge_base_id,
            input_keys=list(kwargs.keys()),
            required_keys=kwargs.get("required_keys", []),
            target_type=getattr(self, "_target_type", None),
            target_id=getattr(self, "_target_id", None),
            project_id=getattr(self, "_target_project_id", None),
            organization_id=self.organization_id,
            workspace_id=self.workspace_id,
        )

        self._ground_truth_blocks = kwargs.pop("ground_truth_blocks", None) or []

        # 1. Build the rendered prompt from template + variables
        required_keys = kwargs.get("required_keys", [])
        optional_keys = kwargs.get("optional_keys") or []

        # Auto-context detection: if the prompt references {{row.X}},
        # {{span.X}}, {{trace.X}}, {{session.X}} (or bare {{row}} etc.),
        # auto-enable the matching data_injection flag so the rest of the
        # pipeline (validation skip, context appending, tool availability)
        # behaves as if the user had toggled it manually.
        auto_roots = _detect_auto_context_roots(self.rule_prompt)
        if auto_roots:
            _root_to_flag = {
                "row": "full_row",
                "span": "span_context",
                "trace": "trace_context",
                "session": "session_context",
                "call": "call_context",
            }
            for root in auto_roots:
                self.data_injection[_root_to_flag[root]] = True

        # Enforce truly-required keys. Optional keys are those listed in
        # optional_keys — they are allowed to be missing/empty at run time.
        # Every other key in required_keys MUST be provided.
        # SKIP this validation entirely when a non-template context option is
        # enabled (full_row, span_context, trace_context, session_context) —
        # in those modes the agent has access to richer context and template
        # variable mapping is optional.
        has_alt_context = bool(
            auto_roots
            or self.data_injection.get("full_row")
            or self.data_injection.get("fullRow")
            or self.data_injection.get("span_context")
            or self.data_injection.get("spanContext")
            or self.data_injection.get("trace_context")
            or self.data_injection.get("traceContext")
            or self.data_injection.get("session_context")
            or self.data_injection.get("sessionContext")
            or self.data_injection.get("dataset_row")
            or self.data_injection.get("datasetRow")
        )
        if not has_alt_context:
            truly_required = [k for k in required_keys if k not in set(optional_keys)]
            missing = [
                k
                for k in truly_required
                if k not in kwargs or kwargs.get(k) in (None, "", [], {})
            ]
            if missing:
                raise ValueError(
                    f"Missing required input(s) for eval: {', '.join(missing)}. "
                    f"Required keys: {truly_required}. Optional keys: {list(optional_keys)}."
                )

        # Extract few_shots before rendering (not in required_keys, so _render_prompt won't process them)
        few_shots = kwargs.pop("few_shots", None)

        rendered_prompt = self._render_prompt(required_keys, kwargs, optional_keys)
        # Stash for _failure_context() so error-path logger calls can
        # include an input_preview without re-rendering. Strictly best-
        # effort — never depended on elsewhere.
        try:
            self._eval_prompt = rendered_prompt or ""
        except Exception:
            pass

        # `tool_scaffolding` collects data-shape definitions, the explore_trace
        # action menu, and traversal recipes. It belongs in the system prompt
        # (agent-internal operator instructions) — NOT in the user message
        # (the eval criteria + data being judged). Following the codebase
        # convention used by _build_eval_system_prompt: markdown `##` headers,
        # no XML tags. Forwarded to `_run_agent` and appended to system_prompt.
        tool_scaffolding = ""

        # Inject few-shot feedback examples into the rendered prompt
        if few_shots:
            few_shot_texts = []
            for block in (few_shots if isinstance(few_shots, list) else [few_shots]):
                if isinstance(block, dict) and block.get("type") == "text":
                    few_shot_texts.append(block["text"])
                elif isinstance(block, str):
                    few_shot_texts.append(block)
            if few_shot_texts:
                rendered_prompt += "\n\n".join(few_shot_texts) + "\n\n"

        logger.info(
            "agent_eval_prompt_rendered",
            rendered_prompt_preview=rendered_prompt[:500],
            rendered_prompt_length=len(rendered_prompt),
            auto_context_roots=list(auto_roots) if auto_roots else [],
            has_alt_context=has_alt_context,
        )

        # 2. Inject row context — smart navigation for large data
        # Auto-inject when no template variables are used (data injection without mapping)
        row_context = kwargs.get("row_context")
        # When auto-context already handled `row` (user wrote {{row}} or
        # {{row.X}}), skip the legacy auto-append path to avoid duplicating
        # the data in the rendered prompt. Auto-context has already inlined
        # the relevant parts directly where the user placed them.
        _auto_handled_row = "row" in getattr(self, "_rendered_auto_roots", set())
        has_full_row = (not _auto_handled_row) and (
            self.data_injection.get("full_row")
            or self.data_injection.get("fullRow")
            or not self.data_injection.get("variables_only", True)
            or not self.data_injection.get("variablesOnly", True)
            or not required_keys  # No mapping = inject full row automatically
        )

        # Track eval_id for trace explorer tool
        self._current_eval_id = str(uuid.uuid4())
        # Stash kwargs so _run_agent's prompt-dump log can extract session/trace/span IDs
        self._last_eval_kwargs = kwargs
        _has_trace_data = False
        self._has_context_data = False

        # Auto-context: load every root the prompt references into the
        # shared context store so the agent can call `explore_trace` with
        # the matching `root` arg and navigate large data on demand.
        # Bare-reference large-data markers emitted by _render_prompt now
        # actually point at something real.
        if auto_roots:
            import json as _json

            from ai_tools.tools.web.trace_explorer import load_context_data

            _loaded_roots_info: list[tuple[str, int]] = []
            for _root in auto_roots:
                _ctx_key = _AUTO_CONTEXT_KWARGS[_root]
                _data = kwargs.get(_ctx_key)
                if _data is None and _root == "row":
                    _data = kwargs.get("row_context")
                if _data is None:
                    continue
                load_context_data(self._current_eval_id, _root, _data)
                self._has_context_data = True
                try:
                    _size = len(_json.dumps(_data, default=str))
                except Exception:
                    _size = len(str(_data))
                _loaded_roots_info.append((_root, _size))

            logger.info(
                "agent_eval_auto_context_loaded",
                eval_id=self._current_eval_id,
                loaded_roots={r: s for r, s in _loaded_roots_info},
            )

            # Append a single discoverability block telling the agent
            # exactly how to use the tool. This replaces the old
            # trace-specific marker in _render_prompt for the bare case,
            # but _render_prompt's per-root pointer is still there and
            # now points at a tool that's actually wired.
            if _loaded_roots_info:
                import json as _json_disc

                _loaded_root_names = {r for r, _ in _loaded_roots_info}

                # Context definitions — only for loaded roots. Strings live
                # in module-level CONTEXT_REF_BY_ROOT for readability.
                tool_scaffolding += "## Context Reference\n"
                for _root in _AUTO_CONTEXT_ROOTS:
                    if _root in _loaded_root_names:
                        tool_scaffolding += CONTEXT_REF_BY_ROOT[_root]

                tool_scaffolding += (
                    f"\n## Eval Context — How to Explore\n"
                    f'eval_id="{self._current_eval_id}"\n\n'
                )

                # Build a quick data summary for each loaded root
                for _root, _size in _loaded_roots_info:
                    _rd = kwargs.get(_AUTO_CONTEXT_KWARGS[_root])
                    tool_scaffolding += f"**{_root}**:\n"
                    # Add quick stats if trace/session shaped
                    if isinstance(_rd, dict):
                        if _rd.get("span_count"):
                            tool_scaffolding += (
                                f"  Spans: {_rd['span_count']}, "
                                f"Errors: {_rd.get('error_count', 0)}, "
                                f"Latency: {_rd.get('total_latency_ms', 0)}ms\n"
                            )
                            # Show span type breakdown if spans loaded
                            _spans_list = _rd.get("spans", [])
                            if _spans_list:
                                _type_counts = {}
                                for _sp in _spans_list:
                                    _t = _sp.get("observation_type", "?")
                                    _type_counts[_t] = _type_counts.get(_t, 0) + 1
                                tool_scaffolding += (
                                    f"  Span types: {_json_disc.dumps(_type_counts)}\n"
                                )
                        elif _rd.get("trace_count"):
                            tool_scaffolding += (
                                f"  Traces: {_rd['trace_count']}, "
                                f"Total spans: {_rd.get('total_spans', 0)}, "
                                f"Errors: {_rd.get('error_count', 0)}, "
                                f"Duration: {_rd.get('duration_seconds', '?')}s\n"
                            )

                # Action menu + per-root traversal recipes. Strings live in
                # module-level EXPLORE_TRACE_ACTIONS_MENU + TRAVERSAL_BY_ROOT.
                _loaded_root_names = {r for r, _ in _loaded_roots_info}
                tool_scaffolding += "\n" + EXPLORE_TRACE_ACTIONS_MENU
                for _root in _AUTO_CONTEXT_ROOTS:
                    if _root in _loaded_root_names:
                        tool_scaffolding += TRAVERSAL_BY_ROOT[_root]

        # Explicit data_injection flags: if the user enabled toggles
        # (e.g., trace_context, session_context) but didn't write {{trace}}
        # in the template, still load the data and enable tools so the agent
        # can explore context alongside mapped variables. Flag → kwarg / root
        # maps live at module level (EXPLICIT_FLAG_TO_KWARG / EXPLICIT_FLAG_TO_ROOT).
        if not auto_roots:
            # Only do this if auto-context didn't already handle it
            import json as _json_explicit

            from ai_tools.tools.web.trace_explorer import load_context_data as _load_ctx

            _explicit_loaded: list[tuple[str, int]] = []
            for _flag, _kwarg_key in EXPLICIT_FLAG_TO_KWARG.items():
                if self.data_injection.get(_flag):
                    _data = kwargs.get(_kwarg_key)
                    if _data is None:
                        continue
                    _root = EXPLICIT_FLAG_TO_ROOT[_flag]
                    # Don't double-load if auto_roots already handled this
                    if (_root,) in [
                        (r,) for r, _ in getattr(self, "_loaded_roots_info", [])
                    ]:
                        continue
                    _load_ctx(self._current_eval_id, _root, _data)
                    self._has_context_data = True
                    try:
                        _size = len(_json_explicit.dumps(_data, default=str))
                    except Exception:
                        _size = len(str(_data))
                    _explicit_loaded.append((_root, _size))

            if _explicit_loaded:
                # Context definitions for explicit flags. Same CONTEXT_REF
                # strings as the auto-context branch — one source of truth.
                _explicit_root_names = {r for r, _ in _explicit_loaded}
                tool_scaffolding += "## Context Reference\n"
                for _root in _AUTO_CONTEXT_ROOTS:
                    if _root in _explicit_root_names:
                        tool_scaffolding += CONTEXT_REF_BY_ROOT[_root]

                tool_scaffolding += (
                    f"\n## How to Use the `explore_trace` Tool\n"
                    f'eval_id="{self._current_eval_id}". The tool reads the '
                    f"context loaded above AND fetches full span content from the "
                    f"database on demand. The summaries below carry only counts "
                    f"and metadata — for actual conversation content (user "
                    f"messages, agent replies, tool I/O) you MUST call "
                    f"`span_detail` on the relevant span_id. Metadata alone is "
                    f"rarely sufficient to judge quality.\n\n"
                )
                for _root, _size in _explicit_loaded:
                    _kwarg = f"{_root}_context"
                    _rd = kwargs.get(_kwarg, {})
                    tool_scaffolding += f"**{_root}**:\n"
                    if isinstance(_rd, dict):
                        if _root == "trace" and _rd.get("span_count"):
                            tool_scaffolding += (
                                f"  Spans: {_rd['span_count']}, "
                                f"Errors: {_rd.get('error_count', 0)}\n"
                                f"  Path: iterate `trace_context.spans[N]` (each has "
                                f"`id`, `name`, `observation_type`, `status`) → "
                                f'`span_detail` query="<id>" for input/output.\n'
                            )
                        elif _root == "session" and _rd.get("trace_count"):
                            tool_scaffolding += (
                                f"  Traces: {_rd['trace_count']}, "
                                f"Spans: {_rd.get('total_spans', 0)}\n"
                                f"  Path: iterate `session_context.traces[N].spans[M]` "
                                f'(IDs already inlined) → `span_detail` query="<id>" '
                                f"for actual user/agent messages. Drill into multiple "
                                f"traces when judging multi-turn behavior.\n"
                            )
                        elif _root in EXPLICIT_PATH_BY_ROOT:
                            tool_scaffolding += EXPLICIT_PATH_BY_ROOT[_root]

        if row_context and has_full_row:
            import json as _json

            data_size = (
                len(_json.dumps(row_context, default=str))
                if isinstance(row_context, dict)
                else len(str(row_context))
            )
            is_trace = isinstance(row_context, dict) and (
                "spans" in row_context
                or "observation_spans" in row_context
                or "span_attributes" in row_context
                or "observation_type" in row_context
            )

            if is_trace or data_size > 20000:
                # Large/trace data: load into trace explorer tool for smart navigation
                from ai_tools.tools.web.trace_explorer import load_trace_data

                load_trace_data(self._current_eval_id, row_context)
                _has_trace_data = True

                # Large-data exploration recipe is scaffolding → system prompt.
                tool_scaffolding += LARGE_DATA_EXPLORATION_TEMPLATE.format(
                    eval_id=self._current_eval_id
                )
            else:
                # Small data: inline it directly
                from ee.evals.llm.custom_prompt_evaluator.context_window import (
                    fit_row_to_context,
                )

                rendered_prompt += "\n\n## Row Data\n"
                rendered_prompt += fit_row_to_context(
                    row_context, max_chars=_MAX_CONTEXT_CHARS
                )

        # Auto-detect media URLs alongside any image_urls kwarg; fail fast on unreachable URLs.
        image_urls = kwargs.get("image_urls", [])
        if not isinstance(image_urls, list):
            image_urls = [image_urls] if image_urls else []

        llm_override = (
            self._build_llm_override(self._model_cfg) if self._is_turing else None
        )

        _input_dict = {key: kwargs.get(key, "") for key in required_keys}
        _detected_urls, _url_media_types, _url_to_key = (
            AgentEvaluator.detect_eval_media(_input_dict, raise_on_unfetchable=True)
        )
        for u in _detected_urls:
            if u not in image_urls:
                image_urls.append(u)

        # Switch model client for modalities that need it.
        for media_type in _url_media_types.values():
            if media_type in {"audio", "pdf"}:
                llm_override = self._resolve_multimodal_override(media_type)

        self._media_types = sorted({v for v in _url_media_types.values() if v})

        # Auto-upgrade agent_mode from "quick" to "auto" when we've
        # loaded context data. Quick mode disables tools, so the pointer
        # markers we just emitted would be dead weight. Auto gives the
        # agent up to 7 iterations with tools enabled — enough to explore
        # the loaded context without running up cost.
        if self._has_context_data and (self.agent_mode or "").lower() == "quick":
            logger.info(
                "auto_upgrading_agent_mode_for_context_exploration",
                eval_id=self._current_eval_id,
            )
            self.agent_mode = "auto"

        # 2b. Protect fast path — single gateway call, no agent loop.
        call_type = kwargs.get("call_type", "")
        if self._is_protect or call_type in ("protect", "protect_flash"):
            return self._run_protect(
                kwargs=kwargs,
                call_type=call_type,
                start_time=start_time,
            )

        # 3. Run the agent loop
        logger.info(
            "agent_eval_run_agent_start",
            eval_id=self._current_eval_id,
            agent_mode=self.agent_mode,
            model=self._model,
            has_trace_data=_has_trace_data,
            has_context_data=self._has_context_data,
            image_url_count=len(image_urls) if image_urls else 0,
        )

        try:
            agent_result = self._run_agent(
                rendered_prompt,
                image_urls=image_urls,
                include_trace_explorer=(_has_trace_data or self._has_context_data),
                llm_override=llm_override,
                url_media_types=_url_media_types,
                url_to_key=_url_to_key,
                tool_scaffolding=tool_scaffolding,
            )
        except MediaNotAccessibleError:
            raise
        except ManagedGatewayRequiredError:
            # Managed-only model on a deployment without the gateway: a clean
            # configuration/entitlement failure, not an internal error. Log it
            # as such and re-raise so the actionable message reaches the user
            # instead of the generic USER_FACING_EVAL_FAILED.
            logger.warning(
                "agent_eval_managed_model_requires_license",
                model=self._effective_model or self._model,
                eval_id=self._current_eval_id,
            )
            raise
        except Exception as e:
            err_msg = str(e).strip() or type(e).__name__
            logger.exception(
                "agent_evaluator_error",
                error=err_msg,
                error_type=type(e).__name__,
                eval_id=self._current_eval_id,
                model=self._model,
                **_extract_gateway_diagnostics(e),
            )
            # Preserve the specific generic message raised from inner
            # paths (USER_FACING_EVAL_FAILED). Re-wrapping here would
            # destroy the deliberately-curated user-facing string.
            if isinstance(e, ValueError) and str(e) == USER_FACING_EVAL_FAILED:
                raise
            raise ValueError(USER_FACING_EVAL_FAILED) from e
        finally:
            # Clean up any context data from the store. The legacy
            # clear_trace_data wrapper routes to clear_context_data, which
            # removes every root for this eval_id — covers both the
            # legacy row-context path and the new auto-context roots.
            if _has_trace_data or self._has_context_data:
                from ai_tools.tools.web.trace_explorer import clear_trace_data

                clear_trace_data(self._current_eval_id)

        # 3. Parse the agent's response into eval result
        end_time = time.time()
        eval_runtime_ms = int((end_time - start_time) * 1000)

        return self._build_result(agent_result, eval_runtime_ms)

    def _run_protect(
        self,
        kwargs: dict,
        call_type: str,
        start_time: float,
    ) -> "EvalResult":
        """Run a Protect / Protect Flash evaluation."""
        from ee.protect.helper import ProtectHelper

        is_flash = call_type == "protect_flash"
        eval_name = kwargs.get("eval_name") or self.rule_prompt or ""

        # Validate input types
        input_types = kwargs.get("input_type") or []
        if isinstance(input_types, str):
            input_types = [input_types]
        if input_types:
            ProtectHelper.validate_input_types(input_types)

        # Resolve the gateway alias model name
        alias = ProtectHelper.resolve_alias(eval_name, is_flash=is_flash)

        # Resolve max_tokens — configurable via kwargs (e.g. from guardrail config)
        default_max_tokens = 150 if not is_flash else 128
        protect_max_tokens = default_max_tokens
        raw_mt = kwargs.get("max_tokens")
        if raw_mt is not None:
            try:
                protect_max_tokens = int(raw_mt)
            except (ValueError, TypeError):
                pass

        # Build protect-specific messages. Support both "input" and "inputs" keys.
        inputs = kwargs.get("inputs") or []
        if not inputs:
            single_input = kwargs.get("input") or kwargs.get("output") or ""
            inputs = [single_input] if single_input else []
        if isinstance(inputs, str):
            inputs = [inputs]
        messages = ProtectHelper.build_messages(
            eval_name,
            inputs,
            input_types,
            is_flash=is_flash,
            max_tokens=protect_max_tokens,
        )

        logger.info(
            "agent_eval_protect_start",
            eval_id=self._current_eval_id,
            alias=alias,
            is_flash=is_flash,
            eval_name=eval_name,
        )

        # Single LLM call using the resolved alias
        from agentic_eval.core.llm.llm import LLM

        llm = LLM(
            provider="protect" if not is_flash else "protect_flash",
            model_name=alias,
            temperature=0.0,
            max_tokens=protect_max_tokens,
        )
        response = llm._try_gateway_completion(
            {
                "model": alias,
                "messages": messages,
                "temperature": 0.0,
                "max_tokens": protect_max_tokens,
                "stream": False,
            }
        )

        if response is None:
            raise ValueError(
                "Protect evaluation failed: gateway unavailable or returned no response."
            )

        # Extract content from gateway response
        content = ""
        if hasattr(response, "choices") and response.choices:
            msg = response.choices[0].message
            content = msg.content if msg else ""

        logger.info(
            "agent_eval_protect_raw_response",
            alias=alias,
            content=content[:500] if content else "EMPTY",
        )

        # Parse using ProtectHelper
        parsed = ProtectHelper.parse_response(content, is_flash=is_flash)

        # Build EvalResult
        end_time = time.time()
        eval_runtime_ms = int((end_time - start_time) * 1000)

        label = (parsed.get("choices") or ["Failed"])[0]
        explanation = parsed.get("explanation", "")
        is_failure = label.lower() == "failed"

        # Flip if reverse_output is set
        if self._reverse_output:
            is_failure = not is_failure

        # Token usage from gateway response
        usage = {}
        if hasattr(response, "usage") and response.usage:
            usage = {
                "prompt_tokens": response.usage.prompt_tokens or 0,
                "completion_tokens": response.usage.completion_tokens or 0,
                "total_tokens": response.usage.total_tokens or 0,
            }

        # Update instance token/cost tracking (read by eval_runner for billing)
        self.token_usage.update(usage)
        try:
            from agentic_eval.core_evals.fi_utils.token_count_helper import (
                calculate_total_cost,
            )

            self.cost.update(calculate_total_cost(alias, self.token_usage))
        except Exception:
            pass

        from agentic_eval.core_evals.fi_evals.base_evaluator import EvalResult

        return EvalResult(
            output=label,
            failure=is_failure,
            reason=explanation,
            runtime=eval_runtime_ms,
            metadata={
                "model": alias,
                "call_type": call_type,
                "is_flash": is_flash,
                "token_usage": usage,
                **{
                    k: v
                    for k, v in parsed.items()
                    if k not in ("choices", "explanation")
                },
            },
        )

    @staticmethod
    def _rebuild_user_anchor_content(
        eval_prompt: str,
        gt_blocks: "list | None",
        file_images: "list | None",
    ) -> "str | list":
        """Order user-anchor as [gt, text, media] when media or non-text gt rides along; concatenate text-only gt into the string otherwise so per-message truncation in ``EvalLLMClient._cap_single_message`` still applies."""
        gt_blocks = gt_blocks or []
        file_images = file_images or []
        text_only_gt = bool(gt_blocks) and all(
            isinstance(b, dict)
            and b.get("type") == "text"
            and isinstance(b.get("text"), str)
            for b in gt_blocks
        )
        if not file_images and (not gt_blocks or text_only_gt):
            if not gt_blocks:
                return eval_prompt
            gt_text = "\n\n".join(b["text"] for b in gt_blocks)
            return gt_text + "\n\n" + eval_prompt
        return [
            *gt_blocks,
            {"type": "text", "text": eval_prompt},
            *file_images,
        ]

    @staticmethod
    def _media_placeholder(value: str) -> "str | None":
        """Return placeholder text for image / audio / PDF URLs, or None for non-media values."""
        if not isinstance(value, str):
            return None
        s = value.strip()
        if _RENDER_AUDIO_URL_RE.match(s):
            return "[audio content provided as input — listen to the attached audio block to evaluate]"
        if _RENDER_IMAGE_URL_RE.match(s):
            return "[image content provided as input — see the attached image block]"
        if _RENDER_PDF_URL_RE.match(s):
            return "[PDF content provided as input — read the attached PDF block]"
        return None

    @staticmethod
    def _jinja_render(template_str: str, context: dict, finalize=None) -> str:
        """Render a Jinja2 template; preserve unknown ``{{key}}`` and fall back to str.replace on syntax errors."""
        import jinja2
        from agentic_eval.core_evals.fi_utils.utils import PreserveUndefined
        from jinja2.sandbox import SandboxedEnvironment

        env_kwargs: dict = {
            "variable_start_string": "{{",
            "variable_end_string": "}}",
            "undefined": PreserveUndefined,
        }
        if finalize is not None:
            env_kwargs["finalize"] = finalize
        # Sandboxed: templates are user-authored; a plain Environment lets
        # `{{ ''.__class__.__mro__[1].__subclasses__() }}` reach subprocess/os
        # (SSTI -> RCE).
        env = SandboxedEnvironment(**env_kwargs)
        try:
            return env.from_string(template_str).render(**context)
        except (jinja2.TemplateSyntaxError, jinja2.exceptions.SecurityError):
            # Sandbox rejection or parse failure: str.replace fallback so the
            # payload reaches the LLM as literal text instead of crashing.
            rendered = template_str
            for key, value in context.items():
                rendered = rendered.replace("{{" + key + "}}", str(value))
                rendered = rendered.replace("{{ " + key + " }}", str(value))
            return rendered

    @staticmethod
    def render_eval_prompt(
        rule_prompt: str, input_dict: dict, input_types: dict | None = None
    ) -> str:
        """Render rule_prompt for display in the error localizer and similar contexts.

        Full Jinja2 rendering of ``rule_prompt``. Media URLs are replaced with placeholder
        text pointing to the attached content block; unknown variables stay as ``{{key}}``.
        """
        import json as _json

        context: dict = {}
        for key, value in (input_dict or {}).items():
            placeholder = AgentEvaluator._media_placeholder(value)
            if placeholder is not None:
                context[key] = placeholder
            elif isinstance(value, str):
                context[key] = value
            elif isinstance(value, (dict, list)):
                try:
                    context[key] = _json.dumps(value, default=str, ensure_ascii=False)
                except Exception:
                    context[key] = str(value)
            else:
                context[key] = value

        return AgentEvaluator._jinja_render(rule_prompt or "", context)

    @staticmethod
    def _build_llm_override(cfg) -> dict:
        """Build an llm_override dict from a ModelConfig."""
        return {
            "provider": cfg.model_name,
            "model": cfg.model_name,
            "max_tokens": cfg.max_tokens,
            "temperature": cfg.temperature,
        }

    @staticmethod
    def _managed_ai_available() -> bool:
        """Whether the managed-AI gateway can be reached in this deployment.

        Managed transport needs either cloud infra or a self-hosted EE
        license that actually includes managed Falcon/Turing compute.
        Anywhere else the activation client fails with ACTIVATION_FAILED,
        so callers must route the eval's own model through its native
        provider instead.

        This is a deployment-level question (is the gateway reachable at
        all), not a per-org entitlement: on cloud the gateway always exists
        and per-org Turing access is enforced at model selection, so no
        org_id is needed here. We consult the capability service rather than
        DeploymentMode.is_ee() so an *expired* or managed-compute-excluded
        EE license correctly routes direct instead of hitting a gateway that
        would reject it.
        """
        try:
            from tfc.capabilities import service
            from tfc.licensing.types import DeploymentLocation
        except ImportError:
            return False

        if service.get_deployment_location() == DeploymentLocation.CLOUD:
            return True
        # Self-hosted: reachable only when the license entitles managed
        # compute. check() consults the license snapshot and is org-agnostic
        # off-cloud, so no org_id is required.
        return service.check("falcon_ai").allowed

    @staticmethod
    def _provider_for_user_model(model: object) -> str | None:
        """Map a user-selected model to a FalconLLMClient provider for the
        direct (non-managed) transport path.

        Returns the provider, or None for genuinely unknown families so the
        caller keeps its existing default. Raises ManagedGatewayRequiredError
        for Turing/Protect models: they run only through the managed gateway,
        so returning None would strand the eval on the dead managed path
        (ACTIVATION_FAILED) — the exact failure this fallback exists to avoid.
        """
        name = str(model or "").lower()

        # Turing/Protect are managed-gateway-only — there is no direct
        # provider to fall back to. Fail loudly instead of returning None
        # (which the caller reads as "keep the managed default").
        if name.startswith(("turing_", "protect")):
            raise ManagedGatewayRequiredError(
                f"'{model}' is a FutureAGI managed model and needs a FutureAGI "
                "license (managed gateway) to run. Select a model from your own "
                "provider (OpenAI, Anthropic, Bedrock, Vertex), or add a "
                "license to use managed models."
            )

        base = name.split("/")[-1]

        # Bedrock first: explicit `bedrock/…` ids AND bare cross-region
        # inference-profile ids (`us.` / `eu.` / `apac.` / `global.` prefix,
        # e.g. `us.anthropic.claude-…`). Bedrock authenticates with SigV4 and
        # needs no API key — the one provider that works with zero user setup,
        # so its ids must never fall through to None.
        if name.startswith("bedrock/") or name.startswith(
            ("us.", "eu.", "apac.", "global.")
        ):
            return "bedrock"

        if name.startswith("vertex_ai/") or base.startswith("gemini"):
            return "vertex_ai"
        if base.startswith("claude"):
            return "anthropic"
        if base.startswith(("gpt", "o1", "o3", "o4", "chatgpt")):
            return "openai"
        return None

    def _resolve_multimodal_override(self, media_type: str) -> dict:
        """Resolve the LLM override for audio/PDF inputs.

        Turing models share a common credential pool. Rather than
        rejecting the run when the user's selected Turing model doesn't
        natively support the modality, auto-upgrade to the
        multimodal-capable Turing model and log the switch. The
        alternative — raising a ``ValueError`` — makes every audio eval
        fail the moment a user saves their config with the default
        ``turing_flash``, which is the common case.
        """
        if self._is_turing:
            natively_supports = (
                media_type == "audio" and ModelConfigs.supports_audio(self._model)
            ) or (media_type == "pdf" and ModelConfigs.supports_pdf(self._model))
            if not natively_supports:
                logger.info(
                    "multimodal_auto_upgraded_to_xl",
                    from_model=self._model,
                    media_type=media_type,
                    eval_id=self._current_eval_id,
                )
            self._effective_model = ModelConfigs.TURING_LARGE_XL.model_name
            return self._build_llm_override(ModelConfigs.TURING_LARGE_XL)
        multimodal_provider = os.environ.get("FALCON_MULTIMODAL_PROVIDER", "")
        multimodal_model = os.environ.get("FALCON_MULTIMODAL_MODEL", "")
        if not multimodal_provider or not multimodal_model:
            logger.error(
                "multimodal_provider_not_configured",
                **self._failure_context(),
            )
            raise ValueError(USER_FACING_EVAL_FAILED)
        return {"provider": multimodal_provider, "model": multimodal_model}

    def _render_prompt(
        self,
        required_keys: list,
        kwargs: dict,
        optional_keys: list | None = None,
    ) -> str:
        """Render the Jinja2 template with variable values, applying context windowing.

        `optional_keys` lists keys that may legitimately be missing at run time.
        Missing optional keys are rendered as an explicit "(not provided)" marker
        in the data section so the LLM knows the field was intentionally absent
        rather than forgotten.

        Auto-context: if the rule_prompt references `{{row}}` / `{{row.X}}` /
        `{{span}}` / `{{trace}}` / `{{session}}`, the corresponding context
        dict from kwargs (`row_context`, `span_context`, etc.) is bound to
        Jinja as `row` / `span` / `trace` / `session`. Dotted access
        (`{{row.field_a}}`) works natively via Jinja's getattr/getitem. Bare
        references (`{{row}}`) get a JSON-stringified version of the dict if
        small, or a compact pointer marker if large (actual large-data
        exploration is handled by the existing row_context + trace_explorer
        path further down in `_evaluate`).
        """
        import json as _json

        from ee.evals.llm.custom_prompt_evaluator.context_window import (
            fit_to_context,
        )

        optional_keys = optional_keys or []
        optional_set = set(optional_keys)

        # Jinja `finalize` hook: applied to every variable substitution
        # output before it becomes part of the rendered string. We use it
        # to JSON-serialize dict and list values that come through dotted
        # auto-context access (e.g. `{{call.transcript}}` returning a list
        # of turns). Without this, Jinja falls back to Python's repr which
        # emits ugly single-quoted `[{'speaker': ...}]`. We also cap the
        # serialized size so huge nested structures don't explode the
        # prompt — the excess is truncated with a "..." marker.
        _FINALIZE_MAX_CHARS = _MAX_CONTEXT_CHARS

        def _finalize(value):
            if isinstance(value, (dict, list)):
                try:
                    rendered_json = _json.dumps(
                        value, default=str, ensure_ascii=False, indent=2
                    )
                except Exception:
                    return str(value)
                if len(rendered_json) > _FINALIZE_MAX_CHARS:
                    return (
                        rendered_json[:_FINALIZE_MAX_CHARS]
                        + "\n... [truncated — value is too large to fully inline]"
                    )
                return rendered_json
            return value

        _media_placeholder = AgentEvaluator._media_placeholder

        template_context = {}
        data_display = {}
        missing_optional = []
        for key in required_keys:
            if key in kwargs and kwargs[key] not in (None, "", [], {}):
                value = kwargs[key]
                placeholder = _media_placeholder(value)
                if placeholder is not None:
                    template_context[key] = placeholder
                    data_display[key] = placeholder
                    continue
                # Apply context windowing for large values
                if isinstance(value, str) and len(value) > _MAX_CONTEXT_CHARS:
                    value = fit_to_context(
                        value, max_total_chars=_MAX_CONTEXT_CHARS, label=key
                    )
                elif isinstance(value, (dict, list)):
                    serialized = _json.dumps(value, default=str)
                    if len(serialized) > _MAX_CONTEXT_CHARS:
                        value = fit_to_context(
                            value, max_total_chars=_MAX_CONTEXT_CHARS, label=key
                        )
                    else:
                        value = serialized  # Convert dict/list to JSON string
                template_context[key] = value
                data_display[key] = value
            elif key in optional_set:
                # Intentionally absent: empty string for Jinja (falsy), and
                # a human-readable marker in the data display for the LLM.
                template_context[key] = ""
                data_display[key] = "(not provided)"
                missing_optional.append(key)

        # Also build a data section with all variable values clearly labeled
        # This ensures the LLM always knows what values were provided
        if data_display:
            data_section = "\n\n--- Input Data ---\n"
            for k, v in data_display.items():
                val_str = str(v)
                if len(val_str) > 500:
                    val_str = val_str[:500] + "..."
                data_section += f"<{k}>{val_str}</{k}>\n"
            data_section += "--- End Input Data ---"
            if missing_optional:
                data_section += (
                    f"\n\nNote: the following optional fields were not provided and "
                    f"should be ignored in your evaluation: {', '.join(missing_optional)}."
                )
        else:
            data_section = ""

        # Pre-process: handle variable names with spaces (Jinja2 doesn't allow them)
        prompt_to_render = self.rule_prompt
        safe_context = dict(template_context)
        raw_vars = re.findall(r"\{\{\s*([^{}]+?)\s*\}\}", prompt_to_render)
        for var_name in raw_vars:
            stripped = var_name.strip()
            if " " in stripped:
                if stripped in safe_context:
                    replacement = str(safe_context.pop(stripped))
                else:
                    replacement = str(kwargs.get(stripped, "{{" + stripped + "}}"))
                prompt_to_render = prompt_to_render.replace(
                    "{{" + var_name + "}}", replacement
                )
                prompt_to_render = prompt_to_render.replace(
                    "{{ " + stripped + " }}", replacement
                )
            elif "." in stripped and stripped in safe_context:
                # Jinja parses dots as nested access; numeric segments become
                # list indices. Skip auto-context roots (bound separately).
                root = stripped.split(".")[0]
                if root not in _AUTO_CONTEXT_ROOTS:
                    parts = stripped.split(".")
                    value = safe_context.pop(stripped)
                    nest_dotted_value(safe_context, parts, value)

        # Auto-context binding: expose row / span / trace / session to Jinja
        # when referenced. Dotted access (`{{row.field_a}}`) works natively
        # via Jinja's getattr/getitem on the dict. For the bare form
        # (`{{row}}`), Jinja would stringify the dict with its Python repr,
        # which is noisy and unbounded; we substitute a size-aware version
        # of the value into `safe_context` upfront so bare refs render as
        # readable JSON (small) or as a compact pointer (large).
        #
        # Missing data: when a root is referenced in the prompt but the
        # caller didn't supply the matching `{root}_context` kwarg, we
        # pre-substitute `{{root}}` / `{{root.X}}` with a "(not provided)"
        # marker so Jinja never sees an undefined chain. PreserveUndefined
        # only guards `__str__`, not `__getattr__`, so a missing `row`
        # accessed as `{{row.field}}` would otherwise crash.
        # Track which auto-context roots were fully handled inside this
        # _render_prompt call. `_evaluate` uses this to skip the legacy
        # row_context auto-append logic so we don't duplicate the data.
        self._rendered_auto_roots = set()

        auto_roots = _detect_auto_context_roots(prompt_to_render)
        bare_pattern_by_root = {
            root: re.compile(r"\{\{\s*" + root + r"\s*\}\}")
            for root in _AUTO_CONTEXT_ROOTS
        }

        # Matches any `{{root.anything}}` form for a specific root.
        def _dotted_pattern(root):
            return re.compile(r"\{\{\s*" + root + r"(?:\.[A-Za-z_][\w]*)+\s*\}\}")

        BARE_INLINE_THRESHOLD = 5000  # chars — inline bare {{root}} if JSON fits
        for root in auto_roots:
            ctx_key = _AUTO_CONTEXT_KWARGS[root]
            data = kwargs.get(ctx_key)
            if data is None and root == "row":
                # `row` is special-cased to also accept the already-populated
                # `row_context` kwarg from the dataset path.
                data = kwargs.get("row_context")
            if data is None:
                # Substitute all dotted and bare references for this root
                # with "(not provided)" so Jinja doesn't try to resolve them.
                marker = f"({root} data not provided)"
                prompt_to_render = _dotted_pattern(root).sub(marker, prompt_to_render)
                prompt_to_render = bare_pattern_by_root[root].sub(
                    marker, prompt_to_render
                )
                continue

            # For dotted access, Jinja handles dict lookups natively. Expose
            # the dict under the root name.
            safe_context[root] = data
            self._rendered_auto_roots.add(root)

            # For bare `{{root}}`, substitute a size-aware string BEFORE
            # Jinja runs, so rendering doesn't produce a Python repr.
            if bare_pattern_by_root[root].search(prompt_to_render):
                try:
                    as_json = _json.dumps(data, default=str, ensure_ascii=False)
                except Exception:
                    as_json = str(data)
                if len(as_json) <= BARE_INLINE_THRESHOLD:
                    replacement = as_json
                else:
                    replacement = (
                        f"[{root} data — {len(as_json):,} chars, too large "
                        f"to inline. Call the `explore_trace` tool with "
                        f'root="{root}", action="keys" to see what\'s '
                        f'available, then action="get" query="field.path" '
                        f'or action="search" query="substring" to drill '
                        f"in. The eval_id is listed in the "
                        f"'Eval Context Available For Exploration' section "
                        f"below.]"
                    )
                prompt_to_render = bare_pattern_by_root[root].sub(
                    lambda _m, r=replacement: r, prompt_to_render
                )

        # In Jinja mode, parse JSON strings in safe_context to native objects
        # right before rendering, so {% for %} loops can iterate correctly.
        # This is done late so the rest of the pipeline (RAG, embedding,
        # data_display) still works with plain strings.
        if self.template_format == "jinja":
            for key in list(safe_context.keys()):
                val = safe_context[key]
                if isinstance(val, str):
                    stripped = val.strip()
                    if (stripped.startswith("[") and stripped.endswith("]")) or (
                        stripped.startswith("{") and stripped.endswith("}")
                    ):
                        try:
                            safe_context[key] = _json.loads(val)
                        except (ValueError, _json.JSONDecodeError):
                            pass

        rendered = AgentEvaluator._jinja_render(
            prompt_to_render, safe_context, finalize=_finalize
        )

        # Append the data section with XML-tagged values
        rendered += data_section

        logger.info(
            "agent_eval_rendered_prompt",
            original=self.rule_prompt[:100],
            rendered=rendered[:200],
            context_keys=list(safe_context.keys()),
            required_keys=required_keys,
        )
        return rendered

    def _run_agent(
        self,
        eval_prompt: str,
        image_urls: list | None = None,
        include_trace_explorer: bool = False,
        llm_override: dict | None = None,
        url_media_types: dict | None = None,
        url_to_key: dict | None = None,
        tool_scaffolding: str = "",
    ) -> dict:
        """
        Run Falcon AI AgentLoop synchronously for evaluation.

        Creates a lightweight agent instance, configures it with eval-specific
        tools and instructions, runs the multi-turn loop, and returns the result.
        """
        # Import here to avoid circular imports
        from ai_tools.base import ToolContext
        from ee.falcon_ai.agent import AgentLoop

        # Build tool context from stored org/workspace
        tool_context = self._build_tool_context()

        # Create a temporary conversation-like object
        conversation = _EvalConversation()

        # Create agent loop. agent_mode controls how many iterations are
        # allowed and whether tools are available:
        #   - "quick"  → 1 iteration, NO tools (single LLM call)
        #   - "auto"   → up to 7 iterations, tools available
        #   - "agent"  → up to 15 iterations, tools available (default deep eval)
        mode = (self.agent_mode or "agent").lower()
        if mode == "quick":
            agent = AgentLoop(tool_context, conversation)
            agent.MAX_ITERATIONS = 1
        elif mode == "auto":
            agent = AgentLoop(tool_context, conversation)
            agent.MAX_ITERATIONS = 7
        else:  # "agent" (default)
            agent = AgentLoop(tool_context, conversation)
            agent.MAX_ITERATIONS = 15

        # Override the default LLM when inputs require a more capable model.
        if llm_override:
            agent.llm_client = EvalLLMClient(**llm_override)
        else:
            # Wrap the default client too so the non-override eval path
            # also gets per-iteration context management. The default
            # client was set inside ``AgentLoop.__init__`` from env
            # defaults; preserve its provider/model/temperature/etc.
            _default = agent.llm_client
            _provider = None if _default.use_managed_gateway else _default.provider
            _model = _default.model
            if _default.use_managed_gateway and not self._managed_ai_available():
                # Self-hosted without cloud/license can't reach the managed
                # gateway (activation would fail). Route the eval's own
                # model through its native provider with the user's keys.
                derived = self._provider_for_user_model(
                    self._effective_model or self._model
                )
                if derived is not None:
                    _provider = derived
                    _model = self._effective_model or self._model
                    logger.info(
                        "agent_eval_managed_unavailable_direct_provider",
                        provider=_provider,
                        model=_model,
                        eval_id=self._current_eval_id,
                    )
            agent.llm_client = EvalLLMClient(
                provider=_provider,
                model=_model,
                max_tokens=_default.max_tokens,
                temperature=_default.temperature,
            )
            # Preserve any response_format set externally before this point
            agent.llm_client.response_format = getattr(
                _default,
                "response_format",
                None,
            )

        # Tell the wrapper client what the per-turn iteration budget is.
        # The client uses this to inject soft hints at 70% / 90% so the
        # agent consolidates before hitting the cap. Eval-only — Falcon
        # AI chat uses ``FalconLLMClient`` and never sees these hints.
        agent.llm_client.max_iterations = agent.MAX_ITERATIONS

        # Also tell the wrapper what output shape the agent must
        # produce, so the 90% hardstop hint mentions ONLY that shape
        # (no "Pass/Fail or score or choice" spam — the agent sees
        # exactly the format the eval template requested).
        agent.llm_client.output_type = self._output_type
        agent.llm_client.output_choices = self._choices
        agent.llm_client.output_multi_choice = self._multi_choice

        # Enforce structured JSON output via json_schema so the Turing
        # model returns a parseable verdict directly.
        agent.llm_client.response_format = response_format_schema(
            self._output_type,
            self._choices,
            multi_choice=self._multi_choice,
        )

        # Build restricted tool list for eval
        from ai_tools.registry import registry as tool_registry

        eval_tools = []
        # Quick mode = no tools at all (single LLM pass).
        tools_allowed = mode != "quick"

        if tools_allowed and self.check_internet:
            web_search = tool_registry.get("web_search")
            if web_search:
                eval_tools.append(web_search)

        if tools_allowed:
            # knowledge_base_id = backward-compat single KB handle; the newer
            # knowledge_bases list lets the caller pass multiple KB ids.
            kb_ids = _coerce_kb_ids(self.knowledge_bases)
            if self.knowledge_base_id and self.knowledge_base_id not in kb_ids:
                kb_ids.append(self.knowledge_base_id)
            if kb_ids:
                kb_tool = tool_registry.get("search_knowledge_base")
                if kb_tool:
                    eval_tools.append(kb_tool)
            # Stash the list on the evaluator so the system prompt knows about it.
            self._effective_kb_ids = kb_ids
        else:
            self._effective_kb_ids = []

        if tools_allowed and include_trace_explorer:
            trace_tool = tool_registry.get("explore_trace")
            if trace_tool:
                eval_tools.append(trace_tool)

        # MCP connectors — `_coerce_connector_ids` reads canonical
        # `{internet, connectors[]}` or legacy `{uuid: true}` shape.
        if tools_allowed and self.tools_config:
            connector_ids = _coerce_connector_ids(self.tools_config)
            if connector_ids and self.organization_id:
                try:
                    from accounts.models.organization import Organization
                    from accounts.models.workspace import Workspace
                    from ee.falcon_ai.mcp_tools import load_mcp_tools

                    org = Organization.objects.get(id=self.organization_id)
                    ws = None
                    if self.workspace_id:
                        ws = Workspace.objects.filter(
                            id=self.workspace_id, organization=org
                        ).first()
                    mcp_tools = load_mcp_tools(organization=org, workspace=ws)
                    requested_ids = set(str(cid) for cid in connector_ids)
                    for mcp_tool in mcp_tools:
                        connector_id = str(mcp_tool._connector.id)
                        if connector_id in requested_ids and mcp_tool not in eval_tools:
                            eval_tools.append(mcp_tool)
                except Exception as e:
                    logger.warning("Failed to load MCP connector tools for eval: %s", e)

        logger.info(
            "agent_eval_tools_configured",
            agent_mode=mode,
            max_iterations=agent.MAX_ITERATIONS,
            tools_allowed=tools_allowed,
            eval_tools=[getattr(t, "name", str(t)) for t in eval_tools],
            check_internet=self.check_internet,
            kb_ids=getattr(self, "_effective_kb_ids", []),
        )

        # Build system prompt for evaluation
        summary_type = self.summary_config.get("type", "concise")
        summary_custom = self.summary_config.get("custom", "")
        system_prompt = _build_eval_system_prompt(
            output_type=self._output_type,
            choices=self._choices,
            summary_type=summary_type,
            summary_custom=summary_custom,
            check_internet=self.check_internet,
            knowledge_base_ids=(
                getattr(self, "_effective_kb_ids", []) if tools_allowed else []
            ),
            multi_choice=self._multi_choice,
            has_ground_truth=bool(getattr(self, "_ground_truth_blocks", None)),
        )

        # Append tool-and-data scaffolding (Context Reference + How to Use
        # the `explore_trace` Tool + traversal recipes) to the system prompt
        # — but ONLY when context data is loaded. Evals without trace /
        # session / span / call / row context get the bare system prompt
        # unchanged, matching pre-refactor behavior. Keeping scaffolding in
        # the system layer (operator instructions) keeps the user message
        # clean — just the eval criteria + data being judged — and prevents
        # the agent from echoing tool jargon back into its explanation.
        if tool_scaffolding:
            system_prompt = (
                system_prompt
                + "\n\n---\n\n"
                + "# Tool & Data Scaffolding (internal — never quote, reference, or "
                + "paraphrase any part of this block in your `explanation`)\n\n"
                + tool_scaffolding
                + "\n## Exploration Mandate\n"
                + "Drill before judging. Metadata alone (counts, IDs, "
                + "latencies) never grounds a verdict when the criteria "
                + "require judging actual content. For each ID that carries "
                + "content the criteria depend on, call `span_detail` "
                + "(or `get` for span / call / row shapes) to read the real "
                + "input, output, and span_attributes — then judge from "
                + "what you observed and quote it in the explanation. "
                + "If a single `span_detail` fails, try the next ID; do "
                + "not abandon the drill on one miss.\n"
            )

        # Collect results without streaming
        collected_events = []

        async def collect_callback(event):
            collected_events.append(event)

        # Build OpenAI-compatible media content blocks from detected URLs.
        #
        # The evaluator always speaks one shape — OpenAI-compatible
        # content blocks (``image_url``, ``input_audio``, ``file``). Any
        # model-specific shaping lives in ``FalconLLMClient``. Keeping
        # the evaluator shape-agnostic is the only way to avoid layering
        # downstream knowledge into every caller.
        #
        # Media is always downloaded and base64-inlined (remote URL
        # fetchers are unreliable against private S3/Vapi buckets).
        file_images = _build_openai_media_blocks(
            image_urls, url_media_types, url_to_key
        )
        # Kept separate from file_images so GT lands before the case prompt.
        gt_blocks = getattr(self, "_ground_truth_blocks", None) or []

        async def run_async():
            agent_result = await agent.run(
                user_message=eval_prompt,
                history_messages=[],
                send_callback=collect_callback,
                context_page="evaluations",
                context_info={"page": "evaluations", "entity_type": "eval"},
                system_prompt_override=system_prompt,
                tools_override=eval_tools,
                file_images=file_images if file_images else None,
                precontent_blocks=gt_blocks if gt_blocks else None,
            )

            # Deterministic-failure check: did the L4 budget cascade
            # flag this run as oversized? Two sub-cases:
            #
            #   1. Oversized + the model produced content anyway → log
            #      that we recovered (info, not error). Continue
            #      normally; force-finalize / retry are NOT needed.
            #
            #   2. Oversized + the model returned empty → this is
            #      deterministic ("too large"). Retrying or force-
            #      finalizing won't help. Surface a generic user-
            #      facing error and log a SPECIFIC structlog event
            #      so Sentry fingerprints it separately from
            #      transient flakes.
            oversized = getattr(
                agent.llm_client,
                "last_oversized_attempt",
                None,
            )
            content_str = str(agent_result.get("content", "") or "").strip()

            if oversized and content_str:
                logger.info(
                    "agent_evaluator_recovered_oversized_attempt",
                    **self._failure_context(
                        tokens=oversized.get("tokens"),
                        guard=oversized.get("guard"),
                        msg_count=oversized.get("msg_count"),
                    ),
                )
                return agent_result

            if oversized and not content_str:
                logger.error(
                    "agent_evaluator_input_too_large",
                    **self._failure_context(
                        tokens=oversized.get("tokens"),
                        guard=oversized.get("guard"),
                        msg_count=oversized.get("msg_count"),
                        iterations=agent_result.get("_iterations", 0),
                        tool_calls_count=len(agent_result.get("tool_calls", []) or []),
                    ),
                )
                raise ValueError(USER_FACING_EVAL_FAILED)

            # Eval-only force-finalize.
            #
            # Failure mode this handles: the agent ran the full iteration
            # budget calling tools but never committed to a final text
            # answer, so ``content == ""`` and the eval would fail with
            # "evaluator model returned an empty response".
            #
            # Why a clean rebuild (vs. appending one more user message to
            # the existing conversation): at 15 iterations with 20+ tool
            # calls, the model's working state has so much tool-call
            # momentum that an explicit "stop calling tools" instruction
            # still returns empty content from the Turing model a
            # meaningful fraction of the time. Rebuilding from scratch
            # with just the system prompt, the original eval prompt, a
            # synthesized assistant turn summarizing the investigation,
            # and a fresh user request for the verdict gives the model
            # a clean shot.
            #
            # This is implemented at the evaluator level (not in the
            # generic AgentLoop) so it only affects eval flows. Falcon AI
            # chat is unchanged — a chat agent that runs out of iterations
            # without committing is a different UX problem.
            content_str = str(agent_result.get("content", "") or "").strip()
            tool_log = agent_result.get("tool_calls") or []
            if not content_str and tool_log:
                logger.warning(
                    "agent_evaluator_force_finalize_triggered",
                    **self._failure_context(
                        iterations_done=agent_result.get("_iterations", 0),
                        tool_calls=len(tool_log),
                    ),
                )
                try:
                    # Build investigation summary from the tool-call log.
                    #
                    # Per-call preview: head+tail to preserve information at
                    # BOTH ends of long tool results (head-only would lose
                    # stack traces / final-line verdicts emitted by sub-
                    # agents). 1000 head + 500 tail = ~1500 chars per call.
                    #
                    # Budget math (worst case = 25 calls, all maxed out):
                    #     25 * ~1500 chars = ~37_500 chars = ~9_400 tokens
                    # Plus system + user anchor + final ask, total clean-
                    # messages payload stays well under the 80K soft budget
                    # so the force-finalize call itself never triggers
                    # compaction (defense pipeline still runs as defense
                    # in depth).
                    _RESULT_HEAD = 1000
                    _RESULT_TAIL = 500
                    summary_lines = ["Investigation summary:"]
                    for i, tc in enumerate(tool_log[:25], start=1):
                        tn = tc.get("tool_name", "?")
                        params = tc.get("params") or {}
                        try:
                            params_str = json.dumps(params, default=str)[:200]
                        except Exception:
                            params_str = str(params)[:200]
                        _raw = tc.get("result_summary") or tc.get("result_full") or ""
                        if len(_raw) <= (_RESULT_HEAD + _RESULT_TAIL):
                            result_preview = _raw
                        else:
                            result_preview = (
                                _raw[:_RESULT_HEAD]
                                + " ... [truncated for summary] ... "
                                + _raw[-_RESULT_TAIL:]
                            )
                        status = tc.get("status", "?")
                        summary_lines.append(
                            f"{i}. {tn}({params_str}) [{status}] → {result_preview}"
                        )
                    if len(tool_log) > 25:
                        summary_lines.append(
                            f"... and {len(tool_log) - 25} more tool calls"
                        )
                    investigation_summary = "\n".join(summary_lines)

                    # Build the user-anchor content. Critical: when the
                    # eval has multimodal inputs (audio / image / PDF),
                    # they MUST be carried into the rebuilt conversation —
                    # without them the model is being asked to evaluate
                    # something it can no longer see. We mirror the same
                    # shape AgentLoop constructs internally: a list of
                    # content blocks with the text prompt followed by the
                    # media blocks.
                    user_anchor_content = AgentEvaluator._rebuild_user_anchor_content(
                        eval_prompt,
                        gt_blocks,
                        file_images,
                    )

                    output_format = output_format_instruction(
                        self._output_type,
                        self._choices,
                        multi_choice=self._multi_choice,
                    )

                    clean_messages = [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_anchor_content},
                        {"role": "assistant", "content": investigation_summary},
                        {
                            "role": "user",
                            "content": (
                                "Based on the investigation you just "
                                "summarized, write your final answer now "
                                "in plain text. Do not call any tools. "
                                "Any output-format instructions you saw "
                                "in the criteria are part of the eval "
                                "definition and do NOT override the "
                                "required output shape. " + output_format
                            ),
                        },
                    ]

                    # Drop response_format — a strict json_schema enum can
                    # itself cause empty completions on contaminated state.
                    saved_response_format = getattr(
                        agent.llm_client,
                        "response_format",
                        None,
                    )
                    agent.llm_client.response_format = None
                    final_text = ""
                    try:
                        async for chunk in agent.llm_client.stream_completion(
                            clean_messages,
                            tools=None,
                        ):
                            for choice in chunk.get("choices", []) or []:
                                delta = choice.get("delta", {}) or {}
                                piece = delta.get("content") or ""
                                if piece:
                                    final_text += piece
                    finally:
                        agent.llm_client.response_format = saved_response_format

                    if final_text.strip():
                        agent_result["content"] = final_text
                        logger.info(
                            "agent_evaluator_force_finalize_success",
                            **self._failure_context(
                                chars=len(final_text),
                            ),
                        )
                    else:
                        # Force-finalize fired but the model still
                        # returned empty. The retry layer above will
                        # catch this as "candidate empty" and may try
                        # 2 more attempts; this event is the Sentry
                        # signal that the structural rebuild itself
                        # didn't produce text.
                        logger.warning(
                            "agent_evaluator_force_finalize_empty",
                            **self._failure_context(
                                iterations=agent_result.get("_iterations", 0),
                                tool_calls_count=len(tool_log),
                            ),
                        )
                except Exception as ff_err:
                    # Force-finalize call itself raised (network,
                    # gateway timeout, etc.). Distinct from the
                    # "force_finalize_empty" case because here the
                    # call never returned cleanly.
                    logger.error(
                        "agent_evaluator_force_finalize_crashed",
                        **self._failure_context(
                            error=str(ff_err),
                            error_type=type(ff_err).__name__,
                            **_extract_gateway_diagnostics(ff_err),
                        ),
                    )

            return agent_result

        # Run the async agent loop — handle both sync and async Django contexts
        import concurrent.futures

        def _run_in_thread():
            """Run the agent in a fresh event loop via asyncio.run()."""
            from django.db import close_old_connections

            close_old_connections()
            return asyncio.run(run_async())

        # Retry up to 2 times when the agent comes back with empty
        # content. The force-finalize step (inside ``run_async``) and
        # the L4 oversized-attempt check both already fire BEFORE we
        # get here. So if we ever see ``candidate.content == ""`` at
        # this layer, it's a genuine one-off empty-stream flake from
        # the Turing model — exactly the case retry is for.
        #
        # ``ValueError`` raised from inside ``run_async`` (e.g. the
        # oversized + empty path) is deterministic — propagated
        # immediately, no retry.
        import time

        max_attempts = 3
        retry_backoff_seconds = (0, 2, 5)  # before attempt 0, 1, 2
        result = None
        last_thread_err: Exception | None = None
        for attempt in range(max_attempts):
            if retry_backoff_seconds[attempt]:
                time.sleep(retry_backoff_seconds[attempt])
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(_run_in_thread)
                try:
                    candidate = future.result(timeout=300)
                except ValueError:
                    # Deterministic failure raised inside run_async
                    # (e.g. oversized + empty). Surface immediately;
                    # retrying with the same input gives the same result.
                    raise
                except Exception as thread_err:
                    last_thread_err = thread_err
                    logger.warning(
                        "agent_evaluator_thread_error_attempt",
                        **self._failure_context(
                            attempt=attempt + 1,
                            max_attempts=max_attempts,
                            error=str(thread_err),
                            error_type=type(thread_err).__name__,
                            **_extract_gateway_diagnostics(thread_err),
                        ),
                    )
                    candidate = None

            if candidate and str(candidate.get("content", "") or "").strip():
                result = candidate
                if attempt > 0:
                    logger.info(
                        "agent_evaluator_recovered_after_retry",
                        **self._failure_context(
                            attempt=attempt + 1,
                            content_length=len(str(candidate.get("content", "") or "")),
                        ),
                    )
                break

            if attempt < max_attempts - 1:
                logger.warning(
                    "agent_evaluator_empty_response_retrying",
                    **self._failure_context(
                        attempt=attempt + 1,
                        next_attempt=attempt + 2,
                        candidate_was_none=candidate is None,
                    ),
                )

        if result is None:
            if last_thread_err is not None:
                # Terminal failure — correlate with gateway via gateway_request_id.
                logger.error(
                    "agent_evaluator_thread_crashed",
                    **self._failure_context(
                        error=str(last_thread_err),
                        error_type=type(last_thread_err).__name__,
                        attempts=max_attempts,
                        **_extract_gateway_diagnostics(last_thread_err),
                    ),
                )
                raise ValueError(USER_FACING_EVAL_FAILED) from last_thread_err
            # All attempts returned without exception but with empty
            # content — true intermittent Turing-side flake (the L4
            # oversized path raised earlier if it was a size issue).
            logger.error(
                "agent_evaluator_empty_after_all_retries",
                **self._failure_context(
                    attempts=max_attempts,
                ),
            )
            raise ValueError(USER_FACING_EVAL_FAILED)

        logger.info(
            "agent_eval_agent_loop_complete",
            content_preview=str(result.get("content", ""))[:500],
            content_length=len(str(result.get("content", ""))),
            # Always log the configured alias (self._model) — never the
            # gateway-resolved underlying name that AgentLoop captures
            # from the response chunk. Keeps provider/vendor identifiers
            # out of structlog regardless of eval path.
            model_used=self._model,
            mode=result.get("mode", ""),
            input_tokens=result.get("input_tokens", 0),
            output_tokens=result.get("output_tokens", 0),
            tool_calls_count=len(result.get("tool_calls", [])),
            tools_used=list(
                {
                    tc.get("tool_name", "")
                    for tc in result.get("tool_calls", [])
                    if tc.get("tool_name")
                }
            ),
        )

        # Update token usage from agent
        self.token_usage["prompt_tokens"] = result.get("input_tokens", 0)
        self.token_usage["completion_tokens"] = result.get("output_tokens", 0)
        self.token_usage["total_tokens"] = (
            self.token_usage["prompt_tokens"] + self.token_usage["completion_tokens"]
        )

        # Calculate cost — prefer gateway cost, fallback to token calculation
        try:
            from agentic_eval.core_evals.fi_utils.token_count_helper import (
                calculate_total_cost,
            )

            # Cost lookup keyed on the configured alias only — never on
            # a gateway-resolved underlying name.
            cost_model = self._model
            _calculated = calculate_total_cost(cost_model, self.token_usage)

            # Gateway cost (accumulated across iterations, already factored by service)
            _gateway_cost = getattr(agent.llm_client, "_gateway_cost", 0) or 0
            if _gateway_cost > 0:
                self.cost.update(
                    {
                        "total_cost": _gateway_cost,
                        "prompt_cost": _calculated.get("prompt_cost", 0),
                        "completion_cost": _calculated.get("completion_cost", 0),
                        "pricing_source": "gateway",
                    }
                )
            else:
                # Fallback: calculate from tokens + available_models pricing
                self.cost.update(_calculated)
        except Exception as e:
            logger.warning(
                "agent_eval_cost_calculation_failed", model=self._model, error=str(e)
            )

        # Extract tool call info
        tool_calls = result.get("tool_calls", [])
        result["_collected_events"] = collected_events
        result["_tool_calls_count"] = len(tool_calls)
        result["_iterations"] = sum(
            1 for e in collected_events if e.get("type") == "iteration_start"
        )

        return result

    def _build_tool_context(self):
        """Build a ToolContext from stored org/workspace IDs."""
        from accounts.models import User
        from accounts.models.organization import Organization
        from ai_tools.base import ToolContext

        org = None
        workspace = None
        user = None

        if self.organization_id:
            try:
                org = Organization.objects.get(id=self.organization_id)
            except Organization.DoesNotExist:
                pass

        if self.workspace_id and org:
            try:
                from accounts.models.workspace import Workspace

                workspace = Workspace.objects.get(
                    id=self.workspace_id, organization=org
                )
            except Exception:
                pass

        if self.user_id:
            try:
                user = User.objects.get(id=self.user_id)
            except User.DoesNotExist:
                pass
        elif org:
            # Fallback to first user in org
            user = User.objects.filter(organization=org).first()

        return ToolContext(user=user, organization=org, workspace=workspace)

    def _build_result(self, agent_result: dict, runtime_ms: int) -> EvalResult:
        """Parse the agent's response into a structured EvalResult."""
        content = agent_result.get("content", "")

        # Try to extract JSON from the agent's response
        parsed = self._extract_eval_json(content)

        if not parsed:
            _content_safe = content or ""
            logger.exception(
                "agent_evaluator_no_json",
                **self._failure_context(
                    content_length=len(_content_safe),
                    content_preview=(
                        _content_safe[:500] if _content_safe else "(empty)"
                    ),
                    agent_mode_at_run=agent_result.get("mode", ""),
                    input_tokens=agent_result.get("input_tokens", 0),
                    output_tokens=agent_result.get("output_tokens", 0),
                    iterations=agent_result.get("_iterations", 0),
                    tool_calls_count=agent_result.get("_tool_calls_count", 0),
                ),
            )
            if not _content_safe:
                # Reaching this layer means: agent.run returned empty,
                # force-finalize couldn't recover, all retries returned
                # empty too. The dedicated "empty_after_all_retries"
                # error was already logged with full context — we just
                # raise the generic user message here. (We do NOT log
                # again to avoid double-firing Sentry on the same root
                # cause.)
                raise ValueError(USER_FACING_EVAL_FAILED)
            parsed = {
                "result": "Fail",
                "explanation": _content_safe[:500],
            }

        result_value = parsed.get("result", "Fail")
        explanation = parsed.get("explanation", "")

        # Validate that the result matches the expected output type.
        # Each path emits a distinct Sentry-fingerprintable logger
        # event with the actual mismatched value, while the user sees
        # the same neutral message.
        if self._output_type in ("score", "numeric"):
            try:
                float(result_value)
            except (ValueError, TypeError):
                logger.error(
                    "eval_result_type_mismatch_numeric",
                    **self._failure_context(
                        result_value=str(result_value)[:200],
                        expected="numeric score (0.0-1.0)",
                    ),
                )
                raise ValueError(USER_FACING_EVAL_FAILED)
            result_value = clamp_unit_score(result_value)
        elif self._output_type == "Pass/Fail":
            if str(result_value).lower() not in ("pass", "fail"):
                logger.error(
                    "eval_result_type_mismatch_pass_fail",
                    **self._failure_context(
                        result_value=str(result_value)[:200],
                        expected="Pass or Fail",
                    ),
                )
                raise ValueError(USER_FACING_EVAL_FAILED)
        elif self._output_type == "choices" and self._choices:
            if not is_valid_choices_result(
                result_value, self._choices, multi_choice=self._multi_choice
            ):
                logger.error(
                    "eval_result_type_mismatch_choices",
                    **self._failure_context(
                        result_value=str(result_value)[:200],
                        expected=list(self._choices or []),
                        multi_choice=bool(self._multi_choice),
                    ),
                )
                raise ValueError(USER_FACING_EVAL_FAILED)

        # Build metadata
        tool_calls = agent_result.get("tool_calls", [])
        metadata = json.dumps(
            {
                "usage": self.token_usage,
                "cost": self.cost,
                "response_time": runtime_ms,
                "explanation": explanation,
                "agent_metadata": {
                    "model_used": self._model,
                    "mode": agent_result.get("mode", ""),
                    "iterations": agent_result.get("_iterations", 0),
                    "tool_calls_count": agent_result.get("_tool_calls_count", 0),
                    "tools_used": list(
                        {
                            tc.get("tool_name", "")
                            for tc in tool_calls
                            if tc.get("tool_name")
                        }
                    ),
                },
            }
        )

        # Determine failure based on output type
        if self._output_type == "Pass/Fail":
            failure = str(result_value).lower() in ("fail", "failed", "false", "0")
        elif self._output_type in ("score", "numeric"):
            try:
                score = float(result_value)
                failure = score < self._pass_threshold
            except (ValueError, TypeError):
                failure = True
        elif self._output_type == "choices" and self._choices:
            failure = compute_choices_failure(
                result_value,
                self._choices,
                self._choice_scores,
                self._pass_threshold,
                multi_choice=self._multi_choice,
            )
        else:
            failure = False

        # reverse_output flips the final failure bit. Used by evals where the
        # LLM is instructed to return "Pass" when the undesirable condition
        # IS detected (e.g. "return Pass if hallucination found").
        if self._reverse_output:
            failure = not failure

        eval_result: EvalResult = {
            "name": self.name,
            "display_name": self.display_name,
            "data": {"result": result_value},
            "failure": failure,
            "metadata": metadata,
            "reason": explanation,
            "runtime": runtime_ms,
            "model": self._model,
            "metrics": [
                {
                    "id": "agent_eval_score",
                    "value": result_value,
                },
            ],
            "datapoint_field_annotations": None,
        }

        logger.info(
            "agent_eval_complete",
            result=result_value,
            failure=failure,
            runtime_ms=runtime_ms,
            model=self._model,
            output_type=self._output_type,
            reverse_output=self._reverse_output,
            iterations=agent_result.get("_iterations", 0),
            tool_calls_count=agent_result.get("_tool_calls_count", 0),
            prompt_tokens=self.token_usage.get("prompt_tokens"),
            completion_tokens=self.token_usage.get("completion_tokens"),
            explanation_preview=explanation[:200] if explanation else "",
        )

        return eval_result

    def _extract_eval_json(self, content: str) -> dict | None:
        """Extract the evaluation JSON from the agent's response."""
        from agentic_eval.core.utils.json_utils import extract_dict_from_string

        try:
            parsed = extract_dict_from_string(content)
            if "result" in parsed:
                return parsed
        except (ValueError, KeyError):
            pass

        import re

        # Try to find JSON block in markdown
        json_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", content, re.DOTALL)
        if json_match:
            try:
                candidate = json.loads(json_match.group(1))
                if isinstance(candidate, dict) and "result" in candidate:
                    return candidate
            except json.JSONDecodeError:
                pass

        # Try to find raw JSON with "result" key (allows nested braces)
        for m in re.finditer(r"\{[^{}]*\"result\"[^{}]*\}", content):
            try:
                candidate = json.loads(m.group(0))
                if isinstance(candidate, dict) and "result" in candidate:
                    return candidate
            except json.JSONDecodeError:
                continue

        # Last resort: scan backwards for the last JSON object (most likely the eval result)
        last_json = None
        for m in re.finditer(r"\{[^{}]+\}", content):
            try:
                candidate = json.loads(m.group(0))
                if isinstance(candidate, dict):
                    last_json = candidate
            except json.JSONDecodeError:
                continue
        if last_json and "result" in last_json:
            return last_json

        return None


class _EvalConversation:
    """Minimal conversation-like object for agent eval (no DB persistence).
    Mimics falcon_ai.models.Conversation attributes accessed by AgentLoop.
    """

    def __init__(self):
        self.id = uuid.uuid4()
        self.title = "Eval"
        self.context_page = "evaluations"
        self.mode = "evaluations"
        self.active_skill = None
        self.created_at = None
        # AgentLoop reads conversation.context_summary when building context.
        # For eval runs there is no long-lived conversation history to summarize.
        self.context_summary = None

    def save(self, *args, **kwargs):
        # Stub: AgentLoop may try to persist title updates; no-op for evals.
        pass
