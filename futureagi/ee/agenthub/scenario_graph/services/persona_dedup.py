"""Deduplicate persona names across generated cases.

After all intents produce cases independently, some persona names repeat
because each LLM call has no awareness of names from other calls.
This module detects duplicate names and generates unique replacements
via a single batched LLM call.

NOTE: Currently DISABLED in the v3 pipeline (see activities.py
validate_and_enrich_cases_activity). LLM-based name generation has high
collision rates at scale (500+ failures for 1000 rows) and causes
activity timeouts. Retained for potential future use with a different
approach (e.g., deterministic name pool instead of LLM generation).
"""

import json
import time
from collections import defaultdict
from typing import Any, Dict, List

import json_repair
import structlog

from ee.agenthub.scenario_graph.services.llm_factory import create_llm

logger = structlog.get_logger(__name__)

# Only dedup if a name appears more than this many times
_DEDUP_THRESHOLD = 2

# Retry config for LLM name generation
_MAX_RETRIES = 3
_RETRY_DELAY = 5  # seconds


def deduplicate_persona_names(cases: List[Dict[str, Any]]) -> None:
    """Deduplicate persona names in-place across all cases.

    Detects names that appear more than _DEDUP_THRESHOLD times, then
    generates unique replacement names via batched LLM calls, respecting
    each persona's gender and location.

    Args:
        cases: List of case dicts, each with a "persona" dict containing "name",
               "gender", "location", etc. Modified in-place.
    """
    # 1. Index cases by persona name
    name_to_indices: Dict[str, List[int]] = defaultdict(list)
    for i, case in enumerate(cases):
        persona = case.get("persona")
        if not isinstance(persona, dict):
            continue
        name = persona.get("name", "")
        if name:
            name_to_indices[name].append(i)

    # 2. Find duplicate names exceeding threshold
    duplicates: Dict[str, List[int]] = {
        name: indices
        for name, indices in name_to_indices.items()
        if len(indices) > _DEDUP_THRESHOLD
    }

    if not duplicates:
        logger.info("persona_dedup_skipped", reason="no duplicates above threshold")
        return

    total_to_rename = sum(len(idx) - 1 for idx in duplicates.values())
    logger.info(
        "persona_dedup_start",
        duplicate_names=len(duplicates),
        total_to_rename=total_to_rename,
    )

    # 3. For each duplicate name group, generate replacements
    all_used_names = set(name_to_indices.keys())
    renamed_count = 0
    failed_count = 0

    for name, indices in duplicates.items():
        # Keep the first occurrence, rename the rest
        to_rename = indices[1:]

        # Group by gender+location for efficient batching
        groups: Dict[str, List[int]] = defaultdict(list)
        for idx in to_rename:
            persona = cases[idx]["persona"]
            key = f"{persona.get('gender', 'unknown')}|{persona.get('location', 'unknown')}"
            groups[key].append(idx)

        for demo_key, group_indices in groups.items():
            gender, location = demo_key.split("|", 1)
            count = len(group_indices)

            new_names = _generate_unique_names(
                count=count,
                gender=gender,
                location=location,
                avoid_names=all_used_names,
            )

            for i, idx in enumerate(group_indices):
                if i < len(new_names):
                    new_name = new_names[i]
                    cases[idx]["persona"]["name"] = new_name
                    all_used_names.add(new_name)
                    renamed_count += 1
                else:
                    failed_count += 1

            if len(new_names) < count:
                logger.warning(
                    "persona_dedup_partial",
                    name=name,
                    gender=gender,
                    location=location,
                    requested=count,
                    received=len(new_names),
                    skipped=count - len(new_names),
                )

    logger.info(
        "persona_dedup_complete",
        duplicate_names=len(duplicates),
        renamed=renamed_count,
        failed=failed_count,
    )


def _generate_unique_names(
    count: int,
    gender: str,
    location: str,
    avoid_names: set,
) -> List[str]:
    """Generate unique full names via LLM with retry.

    Args:
        count: Number of names to generate.
        gender: Gender for the names.
        location: Geographic location/culture for the names.
        avoid_names: Set of names already in use (to avoid).

    Returns:
        List of unique name strings.
    """
    # Sample some avoid names for context (don't send the full set)
    avoid_sample = sorted(list(avoid_names))[:30]
    avoid_str = ", ".join(avoid_sample)

    prompt = (
        f"Generate exactly {count} unique, realistic full names.\n\n"
        f"Requirements:\n"
        f"- Gender: {gender}\n"
        f"- Cultural background / location: {location}\n"
        f"- Each name must be completely unique — no duplicates\n"
        f"- Names should be diverse (mix of common and uncommon names)\n"
        f"- Do NOT use any of these names: {avoid_str}\n\n"
        f"Return a JSON array of {count} name strings. Nothing else.\n"
        f'Example: ["Amara Osei", "Kenji Nakamura", "Sofia Petrova"]'
    )

    for attempt in range(_MAX_RETRIES):
        try:
            llm = create_llm(max_tokens=max(500, count * 30))
            messages = [{"role": "user", "content": prompt}]
            response = llm._get_completion_content(messages)

            try:
                names = json.loads(response.strip())
            except json.JSONDecodeError:
                names = json_repair.loads(response.strip())

            if not isinstance(names, list):
                raise ValueError(f"Expected list, got {type(names)}")

            # Filter out any names that collide with existing ones
            unique_names = []
            for n in names:
                n_str = str(n).strip()
                if n_str and n_str not in avoid_names:
                    unique_names.append(n_str)
                    avoid_names.add(n_str)  # Prevent within-batch dupes

            if unique_names:
                return unique_names

            # Got a valid response but all names collided — retry with fresh prompt
            logger.warning(
                "persona_name_gen_all_collided",
                attempt=attempt + 1,
                count=count,
            )

        except Exception as e:
            logger.warning(
                "persona_name_gen_failed",
                attempt=attempt + 1,
                error=str(e),
                count=count,
                gender=gender,
                location=location,
            )

        if attempt < _MAX_RETRIES - 1:
            time.sleep(_RETRY_DELAY)

    logger.error(
        "persona_name_gen_exhausted",
        count=count,
        gender=gender,
        location=location,
    )
    return []
