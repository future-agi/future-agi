"""Branch categorization — single or batched LLM calls.

Replaces ESA method: _categorize_branch.
"""

import json
from typing import Dict, List

import json_repair
import structlog

from ee.agenthub.scenario_graph.prompt import (
    BATCH_CATEGORY_PROMPT,
    DEDUPLICATE_CATEGORIES_PROMPT,
    UNIFIED_CATEGORY_PROMPT,
)
from ee.agenthub.scenario_graph.services.llm_factory import create_llm

logger = structlog.get_logger(__name__)

# Max branches per batch call to avoid output truncation
_BATCH_CHUNK_SIZE = 10

# Labels that count as generic/catch-all (case-insensitive)
_GENERIC_LABELS = {"miscellaneous", "general", "other", "unknown", "n/a", "none"}


def _is_generic(category: str) -> bool:
    """Check if a category is a generic/catch-all label."""
    return not category or category.strip().lower() in _GENERIC_LABELS


_SKIP_NODES = {"start", "end", "end_call", "end_call_success", "end_call_failure"}


def _category_from_branch_name(branch_name: str) -> str:
    """Derive a category from branch node names as last-resort fallback.

    Extracts the most meaningful nodes from the branch path, skipping
    generic ones like 'start', 'end', 'end_call'.

    E.g. "start -> identify_intent -> handle_billing -> transfer_agent"
         -> "Handle Billing Transfer Agent"
    """
    parts = [p.strip() for p in branch_name.split("->")]
    meaningful = [p for p in parts if p.lower().replace(" ", "_") not in _SKIP_NODES]
    # Take last 2-3 meaningful nodes (they carry the most info)
    key_nodes = meaningful[-3:] if len(meaningful) > 3 else meaningful
    if not key_nodes:
        key_nodes = parts[-2:] if len(parts) > 1 else parts
    # Convert snake_case to Title Case
    label = " ".join(
        word.replace("_", " ").title() for word in key_nodes
    )
    return label


def categorize_branch(branch_name: str, situations: List[str]) -> str:
    """Categorize a branch using LLM based on its name and associated situations.

    Args:
        branch_name: The branch path string (e.g. "Start -> Greeting -> End").
        situations: List of situation strings associated with this branch.

    Returns:
        Category string from LLM. Returns empty string on total failure.
    """
    try:
        llm = create_llm()
        prompt = UNIFIED_CATEGORY_PROMPT.format(
            branch=branch_name,
            situations=situations[:5],
        )
        messages = [{"role": "user", "content": prompt}]
        response = llm._get_completion_content(messages)
        category = response.strip()
        logger.debug(f"Generated branch category for branch {branch_name}: {category}")
        return category
    except Exception as e:
        logger.warning(f"Error generating branch category for {branch_name}: {e}")
        return ""


def _categorize_chunk(chunk: Dict[str, List[str]]) -> Dict[str, str]:
    """Categorize a chunk of branches in a single LLM call.

    Returns dict mapping branch_name -> category for all branches in chunk.
    Raises on failure (caller handles fallback).
    """
    llm = create_llm(max_tokens=4000)
    branches_json = json.dumps(chunk, ensure_ascii=False)
    prompt = BATCH_CATEGORY_PROMPT.format(branches_json=branches_json)
    messages = [{"role": "user", "content": prompt}]
    response = llm._get_completion_content(messages)

    try:
        result = json.loads(response.strip())
    except json.JSONDecodeError:
        result = json_repair.loads(response.strip())

    if not isinstance(result, dict):
        raise ValueError(f"Expected dict, got {type(result)}")

    # Match results back to input keys; flag generic labels for retry
    categorized = {}
    for branch_name in chunk:
        cat = result.get(branch_name, "")
        categorized[branch_name] = "" if _is_generic(cat) else cat

    return categorized


def categorize_branches_batch(
    branch_situations: Dict[str, List[str]],
) -> Dict[str, str]:
    """Categorize all branches via batched LLM calls.

    Splits into chunks of _BATCH_CHUNK_SIZE to avoid output truncation.
    Falls back to per-branch calls if a chunk fails.
    Retries any branches that get a generic/empty category.

    Args:
        branch_situations: Dict mapping branch_name -> list of situation strings.

    Returns:
        Dict mapping branch_name -> category string.
    """
    if not branch_situations:
        return {}

    # For a single branch, use per-branch call but still apply generic fallback
    if len(branch_situations) == 1:
        branch_name, situations = next(iter(branch_situations.items()))
        cat = categorize_branch(branch_name, situations)
        if _is_generic(cat):
            cat = _category_from_branch_name(branch_name)
        return {branch_name: cat}

    # Split into chunks
    items = list(branch_situations.items())
    categorized = {}

    for i in range(0, len(items), _BATCH_CHUNK_SIZE):
        chunk = dict(items[i : i + _BATCH_CHUNK_SIZE])
        try:
            chunk_result = _categorize_chunk(chunk)
            categorized.update(chunk_result)
            logger.info(
                "batch_categorization_chunk_complete",
                chunk_size=len(chunk),
                categories_returned=sum(1 for v in chunk_result.values() if v),
            )
        except Exception as e:
            logger.warning(
                f"Batch chunk failed, falling back to per-branch: {e}"
            )
            for branch_name, situations in chunk.items():
                categorized[branch_name] = categorize_branch(
                    branch_name, situations
                )

    # Retry any branches that got empty/generic categories via per-branch calls
    empty_branches = [b for b, cat in categorized.items() if _is_generic(cat)]
    if empty_branches:
        logger.info(
            "retrying_generic_branches",
            count=len(empty_branches),
            branches=empty_branches[:5],
        )
        for branch_name in empty_branches:
            situations = branch_situations.get(branch_name, [])
            retry_cat = categorize_branch(branch_name, situations)
            if retry_cat and not _is_generic(retry_cat):
                categorized[branch_name] = retry_cat

    # Last resort: derive category from branch node names for any still-empty
    still_empty = [b for b, cat in categorized.items() if _is_generic(cat)]
    if still_empty:
        logger.warning(
            "category_fallback_from_branch_nodes",
            count=len(still_empty),
        )
        for branch_name in still_empty:
            categorized[branch_name] = _category_from_branch_name(branch_name)

    logger.info(
        "batch_categorization_complete",
        branch_count=len(branch_situations),
        categories_assigned=sum(1 for v in categorized.values() if v and not _is_generic(v)),
        empty_remaining=sum(1 for v in categorized.values() if _is_generic(v)),
    )
    return categorized


def deduplicate_categories(categorized_branches: Dict[str, str]) -> Dict[str, str]:
    """Normalize near-duplicate categories into canonical labels.

    Takes the merged categorized_branches dict (branch_name -> category) from
    all intents, finds near-duplicate category values, and remaps them to
    canonical labels via a single LLM call.

    Args:
        categorized_branches: Dict mapping branch_name -> category string.

    Returns:
        Dict mapping branch_name -> deduplicated category string.
    """
    if not categorized_branches:
        return {}

    # Collect unique category values (excluding empty/generic)
    unique_categories = sorted(set(
        v for v in categorized_branches.values()
        if v and not _is_generic(v)
    ))

    # Nothing to dedup if 0 or 1 unique categories
    if len(unique_categories) <= 1:
        return categorized_branches

    try:
        llm = create_llm(max_tokens=4000)
        categories_json = json.dumps(unique_categories, ensure_ascii=False)
        prompt = DEDUPLICATE_CATEGORIES_PROMPT.format(categories_json=categories_json)
        messages = [{"role": "user", "content": prompt}]
        response = llm._get_completion_content(messages)

        try:
            mapping = json.loads(response.strip())
        except json.JSONDecodeError:
            mapping = json_repair.loads(response.strip())

        if not isinstance(mapping, dict):
            raise ValueError(f"Expected dict, got {type(mapping)}")

        # Apply the dedup mapping
        result = {}
        for branch_name, category in categorized_branches.items():
            if category in mapping:
                result[branch_name] = mapping[category]
            else:
                result[branch_name] = category

        deduped_count = len(unique_categories) - len(set(mapping.values()))
        logger.info(
            "category_deduplication_complete",
            original_categories=len(unique_categories),
            canonical_categories=len(set(mapping.values())),
            duplicates_merged=deduped_count,
        )
        return result

    except Exception as e:
        logger.warning(f"Category deduplication failed, keeping originals: {e}")
        return categorized_branches
