import json
import re
from typing import Any, Dict, List, Optional

import structlog

from agentic_eval.core.llm.llm import LLM

logger = structlog.get_logger(__name__)


def _build_prompt_bg_sound_selection(
    sounds: List[Dict[str, Any]], situation: str
) -> str:
    """Build prompt for background sound selection."""
    sound_options = [
        "\n".join(
            [
                f"ID: {s['id']}",
                f"Title: {s['title']}",
                f"Environment: {s.get('environment', 'unknown')} | Intensity: {s.get('intensity', 'unknown')}",
                f"Description: {s['description']}",
            ]
        )
        for s in sounds
    ]

    prompt = f"""
Select the most appropriate background sound for this caller's situation.

CALLER SITUATION:
{situation}

AVAILABLE BACKGROUND SOUNDS:
{chr(10).join(sound_options)}

Choose the single best match based on:
- Where the caller likely is (environment)
- How busy/loud their surroundings should be (intensity)
- What fits their situation and context

Output ONLY in this exact format:
SELECTED: BG_XX

Replace BG_XX with the actual ID from the list above. Include nothing else in your response."""

    return prompt.strip()


def _extract_bg_id(response: str) -> Optional[str]:
    """Extract BG_XX ID from LLM response using pattern matching."""
    match = re.search(r"SELECTED:\s*(BG_\d+)", response, re.IGNORECASE)
    if match:
        return match.group(1).upper()

    match = re.search(r"\b(BG_\d+)\b", response, re.IGNORECASE)
    if match:
        return match.group(1).upper()

    return None


def select_background_sound(situation: str) -> Dict[str, Any]:
    """
    Select an appropriate background sound using an LLM.
    Args:
        situation: Description of the current situation/scenario/goal.
    Returns:
        Dict with id, value ('off'|'office'|url), and reason. Falls back to 'office' on error.
    """

    def _load_noises() -> List[Dict[str, Any]]:
        required = {"id", "title", "description", "url"}
        try:
            with open(
                "/app/backend/simulate/data/background_sounds.json",
                "r",
                encoding="utf-8",
            ) as f:
                raw = json.load(f)
            logger.info(f"[BG] Loaded {len(raw)} background sound options (raw)")
        except Exception as e:
            logger.error(
                f"[BG] Failed to load background sounds: {e}; defaulting to 'office'"
            )
            return []

        seen: set[str] = set()
        cleaned: list[dict[str, Any]] = []
        for n in raw if isinstance(raw, list) else []:
            if (
                not isinstance(n, dict)
                or not required.issubset(n)
                or not all(n.get(k) for k in required)
            ):
                continue
            bg_id = str(n["id"]).upper()
            if bg_id in seen:
                continue
            seen.add(bg_id)
            cleaned.append(
                {
                    "id": bg_id,
                    "title": str(n.get("title", "")).strip(),
                    "description": str(n.get("description", "")).strip(),
                    "url": str(n.get("url", "")).strip(),
                    "environment": n.get("environment", "unknown"),
                    "intensity": n.get("intensity", "unknown"),
                }
            )
        logger.info(f"[BG] Filtered to {len(cleaned)} valid background sound options")
        return cleaned

    noises = _load_noises()

    if not noises:
        logger.warning("[BG] No background sounds found; defaulting to 'office'")
        return {"id": None, "value": "office", "reason": "no backgrounds available"}

    noise_by_id = {n["id"]: n for n in noises}
    prompt = _build_prompt_bg_sound_selection(noises, situation)
    logger.info(f"[BG] Built background sound prompt: {prompt}")

    llm = LLM(
        model_name="gemini-2.5-flash",
        temperature=0.2,
        provider="vertex_ai",
        api_key=None,
    )
    messages = [
        {
            "role": "system",
            "content": (
                "You must choose exactly one background noise ID from the provided list. "
                "Respond ONLY with `SELECTED: BG_XX` where BG_XX is an ID from the list. "
                "No other text, no JSON, no explanations. If unsure, pick the closest fit by environment and intensity."
            ),
        },
        {"role": "user", "content": prompt},
    ]

    try:
        raw = llm.call_llm(messages, "vertex_ai").strip()
        logger.info(f"[BG] Raw LLM background sound response: {raw}")
        bg_id = _extract_bg_id(raw)
        if not bg_id:
            raise ValueError("Could not parse background ID from response")
        if bg_id not in noise_by_id:
            raise ValueError(f"Selected ID {bg_id} not in available noises")
        chosen = noise_by_id[bg_id]
        url = chosen.get("url", "").strip()
        if not url or not url.startswith(("http://", "https://")):
            raise ValueError(f"Invalid URL for background ID {bg_id}")

        logger.info(f"[BG] Using background sound {bg_id} -> {url}")
        return {
            "id": bg_id,
            "value": url,
            "reason": f"Selected {bg_id} ({chosen.get('title')})",
        }
    except Exception as e:
        logger.warning(
            f"[BG] Error selecting background sound ({e}); defaulting to 'office'"
        )
        return {
            "id": None,
            "value": "office",
            "reason": f"fallback to office after error: {e}",
        }
