import asyncio
import base64
import math
import re
import uuid
from dataclasses import dataclass, field
from functools import cached_property
from io import BytesIO
from typing import Any, Literal

import structlog
from agentic_eval.core.llm.llm import LLM
from agentic_eval.core.utils.model_config import ModelConfigs
from ee.evals.llm.agent_evaluator.evaluator import (
    AgentEvaluator,
    _EvalConversation,
    _openai_media_block,
)
from ee.evals.localizer.prompts import (
    EVAL_CONTEXT_PREAMBLE,
    INPUT_SELECTION_PROMPT,
    LOCALIZER_TASK,
    SYSTEM_PROMPT,
)
from PIL import Image
from pydub import AudioSegment
from tfc.utils.storage import (
    audio_bytes_from_url_or_base64,
    image_bytes_from_url_or_base64,
    upload_audio_to_s3,
    upload_image_to_s3,
)

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class LocalizerResult:
    analysis: dict[str, Any]
    selected_key: str | None
    skip_reason: str | None = None
    cost: dict[str, Any] = field(default_factory=lambda: {"total_cost": 0.0})


Modality = Literal["text", "image", "audio"]

_CHUNKABLE_TYPES = frozenset({"text", "image", "audio"})
_THUMBNAIL_MAX_DIM = 512
_ORG_FIELD = {"text": "orgSen", "audio": "orgSegment", "image": "orgPatch"}


def _normalise_images(
    input_type: str | None, input_data: Any
) -> tuple[str | None, Any]:
    if input_type == "images" and isinstance(input_data, list) and len(input_data) == 1:
        return "image", input_data[0]
    return input_type, input_data


def _is_chunkable(input_type: str | None, input_data: Any) -> bool:
    if input_type in _CHUNKABLE_TYPES:
        return True
    return (
        input_type == "images" and isinstance(input_data, list) and len(input_data) == 1
    )


def _split_into_sentences(paragraph: str | None) -> dict[str, dict[str, Any]]:
    paragraph = paragraph or ""
    sentences = re.split(r"(?<!\w\.\w.)(?<![A-Z][a-z]\.)(?<=\.|\?|\!)\s", paragraph)
    result, search_from = {}, 0
    for i, sent in enumerate(sentences):
        sent = sent.strip()
        if not sent:
            continue
        start_idx = paragraph.find(sent, search_from)
        if start_idx == -1:
            start_idx = search_from
        end_idx = start_idx + len(sent)
        result[f"sentence_{i + 1}"] = {
            "text": sent,
            "start_idx": start_idx,
            "end_idx": end_idx,
        }
        search_from = end_idx
    return result


def _create_audio_segments(audio_input: Any) -> dict[str, dict[str, Any]]:
    segments: dict[str, dict[str, Any]] = {}
    try:
        raw = (
            audio_bytes_from_url_or_base64(audio_input)
            if not isinstance(audio_input, bytes)
            else audio_input
        )
        audio = AudioSegment.from_file(BytesIO(raw))
        total_ms = len(audio)
        chunk_ms = total_ms / max(1, int(total_ms / max(5000, int(total_ms * 0.1))))
        num_chunks = max(1, round(total_ms / chunk_ms))
        for i in range(num_chunks):
            start = int(i * chunk_ms)
            end = total_ms if i == num_chunks - 1 else int((i + 1) * chunk_ms)
            buf = BytesIO()
            audio[start:end].export(buf, format="mp3")
            buf.seek(0)
            b64 = base64.b64encode(buf.read()).decode()
            segments[f"segment_{i + 1}"] = {
                "url": upload_audio_to_s3(b64, object_key=f"tempaudio/{uuid.uuid4()}"),
                "duration": (end - start) / 1000,
                "audio_bytes": b64,
                "start_time": start / 1000,
                "end_time": end / 1000,
            }
    except Exception as exc:
        logger.warning(
            "el_audio_chunk_failed", error=str(exc), exc_type=type(exc).__name__
        )
    return segments


def _build_full_image_block(image_input: Any) -> tuple[list[dict[str, Any]], int, int]:
    image = Image.open(BytesIO(image_bytes_from_url_or_base64(image_input)))
    if image.mode in ("RGBA", "LA", "P"):
        image = image.convert("RGB")
    orig_w, orig_h = image.size
    max_dim = max(orig_w, orig_h)
    if max_dim > _THUMBNAIL_MAX_DIM:
        scale = _THUMBNAIL_MAX_DIM / max_dim
        image = image.resize((int(orig_w * scale), int(orig_h * scale)))
    buf = BytesIO()
    image.save(buf, format="JPEG")
    b64 = base64.b64encode(buf.getvalue()).decode()
    return (
        [
            {
                "type": "text",
                "text": f"<full_image> The full image for global context. ORIGINAL dimensions: {orig_w} x {orig_h} px.",
            },
            _openai_media_block("image", "image/jpeg", b64),
            {"type": "text", "text": "</full_image>"},
        ],
        orig_w,
        orig_h,
    )


def _build_patch_with_position(
    patch_dict: dict[str, Any],
    patch_key: str,
    image_w: int,
    image_h: int,
) -> list[dict[str, Any]]:
    coords = patch_dict.get("coordinates", {})
    tl = coords.get("top_left", (0, 0))
    br = coords.get("bottom_right", (image_w, image_h))
    cx, cy = (tl[0] + br[0]) / 2, (tl[1] + br[1]) / 2
    h_label = (
        "left" if cx < image_w / 3 else ("right" if cx > 2 * image_w / 3 else "center")
    )
    v_label = (
        "top" if cy < image_h / 3 else ("bottom" if cy > 2 * image_h / 3 else "middle")
    )
    return [
        {
            "type": "text",
            "text": f"<{patch_key}> {v_label}-{h_label} region, top_left=({tl[0]}, {tl[1]}) bottom_right=({br[0]}, {br[1]}) of a {image_w}x{image_h} canvas.",
        },
        _openai_media_block("image", "image/jpeg", patch_dict["image_b64"]),
        {"type": "text", "text": f"</{patch_key}>"},
    ]


def _create_overlapping_patches(image_path: Any) -> dict[str, dict[str, Any]]:
    image = Image.open(BytesIO(image_bytes_from_url_or_base64(image_path)))
    if image.mode in ("RGBA", "LA", "P"):
        image = image.convert("RGB")
    w, h = image.size
    aspect = w / h
    best_grid, best_product, best_diff = (1, 1), 0, float("inf")
    for rows in range(1, 21):
        for cols in range(1, 21):
            total = rows * cols
            if total > 20:
                continue
            diff = abs((cols / rows) - aspect)
            if total > best_product or (total == best_product and diff < best_diff):
                best_product, best_diff, best_grid = total, diff, (rows, cols)
    rows, cols = best_grid
    patch_w, patch_h = math.ceil(2 * w / (cols + 1)), math.ceil(2 * h / (rows + 1))
    patches, c = {}, 0
    for row in range(rows):
        for col in range(cols):
            if c >= 20:
                return patches
            x = min(col * math.ceil(patch_w / 2), w - patch_w)
            y = min(row * math.ceil(patch_h / 2), h - patch_h)
            buf = BytesIO()
            image.crop((x, y, x + patch_w, y + patch_h)).save(buf, format="JPEG")
            b64 = base64.b64encode(buf.getvalue()).decode()
            c += 1
            patches[f"patch_{c}"] = {
                "url": upload_image_to_s3(
                    f"data:image/jpeg;base64,{b64}",
                    object_key=f"tempcust/{uuid.uuid4()}",
                ),
                "image_b64": b64,
                "coordinates": {
                    "top_left": (x, y),
                    "top_right": (x + patch_w, y),
                    "bottom_left": (x, y + patch_h),
                    "bottom_right": (x + patch_w, y + patch_h),
                },
            }
    return patches


def _chunk(
    modality: Modality,
    input_data: Any,
    run_id: str,
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]], tuple[int, int] | None]:
    if modality == "text":
        units = _split_into_sentences(input_data or "")
        blocks = [
            {"type": "text", "text": f"<{k}>{u['text']}</{k}>"}
            for k, u in units.items()
        ]
        return units, blocks, None

    if modality == "audio":
        units = _create_audio_segments(input_data)
        blocks = []
        for k, seg in units.items():
            blocks.append(
                {
                    "type": "text",
                    "text": f"<{k} start={seg.get('start_time', 0)}s end={seg.get('end_time', 0)}s>",
                }
            )
            if seg.get("audio_bytes"):
                blocks.append(
                    _openai_media_block("audio", "audio/mpeg", seg["audio_bytes"])
                )
            blocks.append({"type": "text", "text": f"</{k}>"})
        return units, blocks, None

    # image
    units = _create_overlapping_patches(input_data)
    try:
        thumb_blocks, image_w, image_h = _build_full_image_block(input_data)
    except Exception as exc:
        logger.warning("el_thumbnail_failed", run_id=run_id, error=str(exc))
        thumb_blocks, image_w, image_h = [], 0, 0
        for pdata in units.values():
            br = (pdata.get("coordinates") or {}).get("bottom_right")
            if br:
                image_w, image_h = max(image_w, br[0]), max(image_h, br[1])
    blocks = thumb_blocks + [
        b
        for k, p in units.items()
        for b in _build_patch_with_position(p, k, image_w, image_h)
    ]
    return units, blocks, (image_w, image_h)


def _whole_image_coordinates(image_w: int, image_h: int) -> dict[str, list[int]]:
    return {
        "top_left": [0, 0],
        "top_right": [image_w, 0],
        "bottom_left": [0, image_h],
        "bottom_right": [image_w, image_h],
    }


def _attach_whole_org(
    entry: dict[str, Any],
    modality: Modality,
    input_data: Any,
    units: dict[str, dict[str, Any]] | None,
    image_dims: tuple[int, int] | None,
) -> None:
    """Synthesize orgSen / orgSegment / orgPatch for a whole_X entry when input_data supports it."""
    if modality == "text":
        if isinstance(input_data, str):
            entry["orgSen"] = {
                "text": input_data,
                "start_idx": 0,
                "end_idx": len(input_data),
            }
    elif modality == "audio":
        if isinstance(input_data, str) and input_data:
            total = (
                sum(float(s.get("duration") or 0) for s in (units or {}).values())
                or None
            )
            entry["orgSegment"] = {
                "url": input_data,
                "duration": total,
                "start_time": 0,
                "end_time": total,
            }
    elif modality == "image":
        if image_dims and image_dims[0] > 0 and image_dims[1] > 0:
            entry["orgPatch"] = {"coordinates": _whole_image_coordinates(*image_dims)}


def _clamp_int(value: Any, lo: int, hi: int) -> int:
    try:
        return max(lo, min(hi, int(round(float(value)))))
    except (TypeError, ValueError):
        return lo


def _enforce_verdict(
    entries: list[dict[str, Any]] | None,
    input_type: str | None,
    input_data: Any,
    eval_explanation: str | None,
    image_dims: tuple[int, int] | None,
) -> list[dict[str, Any]]:
    if input_type in ("image", "images"):
        image_w, image_h = image_dims if image_dims else (0, 0)
        valid = []
        for s in entries or []:
            if not isinstance(s, dict):
                continue
            org_patch = s.get("orgPatch")
            if not isinstance(org_patch, dict):
                continue
            coords = org_patch.get("coordinates")
            if not isinstance(coords, dict):
                continue
            tl, br = coords.get("top_left"), coords.get("bottom_right")
            if not (isinstance(tl, (list, tuple)) and isinstance(br, (list, tuple))):
                continue
            if len(tl) < 2 or len(br) < 2:
                continue
            x1, y1 = _clamp_int(tl[0], 0, image_w), _clamp_int(tl[1], 0, image_h)
            x2, y2 = _clamp_int(br[0], 0, image_w), _clamp_int(br[1], 0, image_h)
            if x2 <= x1 or y2 <= y1:
                continue
            coords.update(
                {
                    "top_left": [x1, y1],
                    "top_right": [x2, y1],
                    "bottom_left": [x1, y2],
                    "bottom_right": [x2, y2],
                }
            )
            valid.append(s)
        if valid:
            return valid
        return [
            {
                "rank": "1",
                "unit_key": "whole_image",
                "orgPatch": {
                    "coordinates": {
                        "top_left": [0, 0],
                        "top_right": [image_w, 0],
                        "bottom_left": [0, image_h],
                        "bottom_right": [image_w, image_h],
                    }
                },
                "reason": eval_explanation or "Whole-image failure.",
                "improvement": "Refer to the evaluation explanation.",
                "rank_reason": "Whole-image fallback.",
                "weight": 1.0,
            }
        ]
    if entries:
        return entries
    return [
        {
            "rank": "1",
            "unit_key": "whole_input",
            "reason": eval_explanation or "Whole-input failure.",
            "improvement": "Refer to the evaluation explanation.",
            "rank_reason": "Whole-input fallback.",
            "weight": 1.0,
        }
    ]


class ErrorLocalizer:
    def __init__(
        self,
        eval_name: str | None,
        rule_prompt: str | None,
        input: dict[str, Any] | None,
        input_type: dict[str, str] | None,
        evaluation_result: Any,
        evaluation_explanation: str | None,
        choices: list[str] | None,
    ) -> None:
        self.eval_name = eval_name
        self.rule_prompt = rule_prompt
        self.input = input
        self.input_type = input_type
        self.evaluation_result = evaluation_result
        self.evaluation_explanation = evaluation_explanation
        self.choices = choices
        self.cost = {"total_cost": 0.0}

    @cached_property
    def _llm(self) -> LLM:
        return LLM(
            model_name=ModelConfigs.VERTEX_GEMINI_2_5_PRO.model_name,
            temperature=ModelConfigs.VERTEX_GEMINI_2_5_PRO.temperature,
            max_tokens=ModelConfigs.VERTEX_GEMINI_2_5_PRO.max_tokens,
            provider=ModelConfigs.VERTEX_GEMINI_2_5_PRO.provider,
        )

    def _accumulate_cost(self, *, stage: str, source: str, contribution: float) -> None:
        running = float(self.cost.get("total_cost", 0) or 0) + float(contribution or 0)
        self.cost["total_cost"] = running
        logger.debug(
            "el_cost_stage",
            stage=stage,
            source=source,
            contribution=float(contribution or 0),
            running_total=running,
        )

    def localize_errors(self) -> LocalizerResult:
        if not self.input_type:
            reason = "no input types provided"
            logger.info("el_skipped", reason=reason)
            return LocalizerResult(
                analysis={}, selected_key=None, skip_reason=reason, cost=self.cost
            )

        selected_input_key = self._select_target_input()
        if selected_input_key is None:
            reason = "no input available for error localization"
            logger.info("el_skipped", reason=reason)
            return LocalizerResult(
                analysis={}, selected_key=None, skip_reason=reason, cost=self.cost
            )

        input_data = self.input.get(selected_input_key)
        input_type, input_data = _normalise_images(
            self.input_type.get(selected_input_key), input_data
        )

        if not _is_chunkable(input_type, input_data):
            reason = (
                f"the target input '{selected_input_key}' is of type "
                f"'{input_type}', which cannot be sub-divided for error localization"
            )
            logger.info(
                "el_skipped_non_chunkable",
                selected_input_key=selected_input_key,
                input_type=input_type,
            )
            return LocalizerResult(
                analysis={},
                selected_key=selected_input_key,
                skip_reason=reason,
                cost=self.cost,
            )

        return self._localize(input_data, selected_input_key, input_type)

    def _select_target_input(self) -> str | None:
        keys = list((self.input_type or {}).keys())
        if not keys:
            return None
        if len(keys) == 1:
            logger.debug(
                "el_step",
                step="[0/5]",
                phase="select_input",
                strategy="single_input_no_llm",
                picked=keys[0],
            )
            return keys[0]

        picked = None
        for _attempt in range(3):
            messages = [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": INPUT_SELECTION_PROMPT.format(
                                input_keys=self.input_type,
                                rule_prompt=self.rule_prompt,
                                eval_name=self.eval_name,
                            ),
                        }
                    ],
                }
            ]
            response = self._llm._get_completion_content(messages=messages)
            open_idx = response.find("<selected_input_key>")
            end = response.find("</selected_input_key>")
            if open_idx != -1 and end != -1:
                start = open_idx + len("<selected_input_key>")
                selected = response[start:end].strip()
                if selected in self.input_type:
                    picked = selected
                    break
                logger.debug("el_select_input_retry", selected=selected)

        if picked is None:
            picked = (
                "output"
                if "output" in self.input_type
                else next(iter(self.input_type), None)
            )
            logger.info("el_select_input_fallback", picked=picked)

        logger.debug(
            "el_step",
            step="[0/5]",
            phase="select_input_response",
            input_keys=keys,
            picked=picked,
        )
        picker_cost = float(
            (getattr(self._llm, "cost", {}) or {}).get("total_cost", 0) or 0
        )
        self._accumulate_cost(
            stage="picker_llm",
            source="self._llm.cost.total_cost",
            contribution=picker_cost,
        )
        return picked

    def _localize(
        self,
        input_data: Any,
        selected_input_key: str,
        modality: Modality,
    ) -> LocalizerResult:
        run_id = uuid.uuid4().hex[:12]
        units, unit_blocks, image_dims = _chunk(modality, input_data, run_id)
        logger.debug(
            "el_step",
            step="[1/5]",
            phase="chunk",
            run_id=run_id,
            modality=modality,
            unit_count=len(units),
            unit_keys=list(units.keys()),
        )

        if not units:
            reason = f"{modality} input could not be chunked"
            logger.warning(
                "el_chunk_empty",
                run_id=run_id,
                modality=modality,
                selected_input_key=selected_input_key,
            )
            return LocalizerResult(
                analysis={},
                selected_key=selected_input_key,
                skip_reason=reason,
                cost=self.cost,
            )

        if modality == "image" and (
            not image_dims or image_dims[0] <= 0 or image_dims[1] <= 0
        ):
            reason = "image could not be decoded for error localization"
            logger.warning(
                "el_image_dims_invalid", run_id=run_id, image_dims=image_dims
            )
            return LocalizerResult(
                analysis={},
                selected_key=selected_input_key,
                skip_reason=reason,
                cost=self.cost,
            )

        entries = self._invoke_llm(modality, selected_input_key, unit_blocks, run_id)

        org_field = _ORG_FIELD[modality]
        whole_key = f"whole_{modality}"
        for entry in entries or []:
            key = (
                (entry.get("unit_key") or "")
                .replace("<", "")
                .replace(">", "")
                .replace("/", "")
            )
            entry["unit_key"] = key
            if org_field in entry:
                continue
            if key in units:
                unit = units[key]
                if modality == "audio":
                    entry[org_field] = {
                        k: unit.get(k)
                        for k in ("url", "duration", "start_time", "end_time")
                    }
                elif modality == "image":
                    entry[org_field] = {"coordinates": unit.get("coordinates")}
                else:  # text
                    entry[org_field] = unit
            elif key == whole_key:
                _attach_whole_org(entry, modality, input_data, units, image_dims)

        entries = _enforce_verdict(
            entries, modality, input_data, self.evaluation_explanation, image_dims
        )
        logger.info(
            "el_step",
            step="[5/5]",
            phase="verdict",
            run_id=run_id,
            modality=modality,
            entry_count=len(entries) if entries else 0,
            unit_keys=[
                e.get("unit_key") for e in (entries or []) if isinstance(e, dict)
            ],
        )
        return LocalizerResult(
            analysis={"input_1": entries},
            selected_key=selected_input_key,
            skip_reason=None,
            cost=self.cost,
        )

    def _invoke_llm(
        self,
        modality: Modality,
        selected_input_key: str,
        unit_blocks: list[dict[str, Any]],
        run_id: str,
    ) -> list[dict[str, Any]]:
        from ai_tools.base import ToolContext
        from ee.evals.llm.agent_evaluator.context.client import EvalLLMClient
        from ee.falcon_ai.agent import AgentLoop

        try:
            rendered_rule_prompt = AgentEvaluator.render_eval_prompt(
                self.rule_prompt,
                self.input,
                self.input_type,
            )
        except Exception as exc:
            logger.warning(
                "el_inputs_failed", run_id=run_id, modality=modality, error=str(exc)
            )
            rendered_rule_prompt = self.rule_prompt or ""

        try:
            _, media_blocks = AgentEvaluator.build_eval_input_blocks(
                rule_prompt="",
                input_dict=self.input,
                input_types=self.input_type,
                tag_keys=True,
            )
        except Exception as exc:
            logger.warning(
                "el_inputs_failed", run_id=run_id, modality=modality, error=str(exc)
            )
            media_blocks = []

        choices_line = f"\nChoices: {self.choices}" if self.choices else ""
        user_message = "\n\n".join(
            filter(
                None,
                [
                    EVAL_CONTEXT_PREAMBLE,
                    rendered_rule_prompt or "(no rule_prompt set)",
                    LOCALIZER_TASK.get(modality, "").format(
                        eval_name=self.eval_name or "",
                        evaluation_result=self.evaluation_result or "",
                        evaluation_explanation=self.evaluation_explanation or "",
                        choices_line=choices_line,
                        selected_input_key=selected_input_key,
                    ),
                    "\n".join(
                        b["text"]
                        for b in unit_blocks
                        if modality == "text"
                        and isinstance(b, dict)
                        and b.get("type") == "text"
                    )
                    or None,
                ],
            )
        )

        file_images = list(media_blocks or []) + (
            [] if modality == "text" else list(unit_blocks or [])
        )

        agent = AgentLoop(
            ToolContext(user=None, organization=None, workspace=None),
            _EvalConversation(),
        )
        agent.MAX_ITERATIONS = 1
        agent.llm_client = EvalLLMClient(
            provider=self._llm.provider,
            model=self._llm.model_name,
            max_tokens=self._llm.max_tokens,
            temperature=self._llm.temperature,
        )
        agent.llm_client.max_iterations = 1
        agent.llm_client.output_type = "structured"
        agent.llm_client.response_format = {
            "type": "json_schema",
            "json_schema": {
                "name": "error_localizer_result",
                "schema": {
                    "type": "object",
                    "properties": {
                        "entries": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "unit_key": {"type": "string"},
                                    "rank": {"type": "string"},
                                    "reason": {"type": "string"},
                                    "improvement": {"type": "string"},
                                    "rank_reason": {"type": "string"},
                                },
                                "required": [
                                    "unit_key",
                                    "rank",
                                    "reason",
                                    "improvement",
                                    "rank_reason",
                                ],
                            },
                        }
                    },
                    "required": ["entries"],
                },
            },
        }

        async def _run():
            async def _no_op(_):
                pass

            return await agent.run(
                user_message=user_message,
                history_messages=[],
                send_callback=_no_op,
                context_page="evaluations",
                context_info={"page": "evaluations", "entity_type": "error_localizer"},
                system_prompt_override=SYSTEM_PROMPT.get(modality, ""),
                tools_override=[],
                file_images=file_images or None,
            )

        result = asyncio.run(_run())

        from agentic_eval.core.utils.json_utils import extract_dict_from_string

        gateway_cost = float(getattr(agent.llm_client, "_gateway_cost", 0) or 0)
        if gateway_cost > 0:
            self._accumulate_cost(
                stage="agentloop_llm",
                source="agent.llm_client._gateway_cost",
                contribution=gateway_cost,
            )
        else:
            agentloop_cost = 0.0
            try:
                from agentic_eval.core_evals.fi_utils.token_count_helper import (
                    calculate_total_cost,
                )

                token_usage = {
                    "prompt_tokens": (
                        (result or {}).get("input_tokens", 0)
                        if isinstance(result, dict)
                        else 0
                    ),
                    "completion_tokens": (
                        (result or {}).get("output_tokens", 0)
                        if isinstance(result, dict)
                        else 0
                    ),
                }
                token_usage["total_tokens"] = (
                    token_usage["prompt_tokens"] + token_usage["completion_tokens"]
                )
                calculated = (
                    calculate_total_cost(self._llm.model_name, token_usage) or {}
                )
                agentloop_cost = float(calculated.get("total_cost", 0) or 0)
            except Exception as exc:
                logger.warning(
                    "el_cost_token_fallback_failed", run_id=run_id, error=str(exc)
                )
            self._accumulate_cost(
                stage="agentloop_llm",
                source="token_count_helper.calculate_total_cost",
                contribution=agentloop_cost,
            )
        content = result.get("content") if isinstance(result, dict) else None

        entries = []
        try:
            parsed = (
                content
                if isinstance(content, dict)
                else extract_dict_from_string(str(content or ""))
            )
            entries = parsed.get("entries", []) if isinstance(parsed, dict) else []
        except Exception:
            logger.warning(
                "el_parse_failed",
                run_id=run_id,
                modality=modality,
                raw_preview=str(content)[:500],
            )

        logger.debug(
            "el_step",
            step="[4/5]",
            phase="parse",
            run_id=run_id,
            modality=modality,
            entry_count=len(entries),
        )
        return entries
