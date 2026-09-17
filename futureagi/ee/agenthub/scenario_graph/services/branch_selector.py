"""Branch selection and filtering — choose branches for case generation.

Replaces ESA methods: _select_branches, _filter_branches_with_llm, process_branches (selection part).
"""

import random
import re
from typing import Any, Dict, List, Optional, Tuple

import structlog

from ee.agenthub.scenario_graph.services.llm_factory import create_llm

logger = structlog.get_logger(__name__)


def select_branches(
    branches_metadata: List[Dict[str, Any]],
    needed: int,
    custom_instruction: Optional[str] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    """Select branches for case generation.

    If custom_instruction is provided and there are more branches than needed,
    uses LLM-based filtering. Otherwise uses random sampling.

    Args:
        branches_metadata: All available branch metadata dicts.
        needed: Number of branches to select.
        custom_instruction: Optional instruction for LLM-based filtering.

    Returns:
        Tuple of (selected_metadata, branch_metadata_lookup).
    """
    if not branches_metadata:
        return [], {}

    selected = branches_metadata

    if custom_instruction and len(branches_metadata) > needed:
        selected = _filter_branches_with_llm(
            branches_metadata, needed, custom_instruction
        )
    elif len(branches_metadata) > needed:
        selected = random.sample(branches_metadata, needed)

    branch_metadata_lookup = {
        bm.get("branch_name", ""): bm for bm in selected if bm.get("branch_name")
    }

    return selected, branch_metadata_lookup


def _filter_branches_with_llm(
    branches_metadata: List[Dict[str, Any]],
    needed: int,
    custom_instruction: str,
) -> List[Dict[str, Any]]:
    """Use LLM to select the most relevant branches based on custom instruction.

    Falls back to random sampling on LLM failure.
    """
    try:
        llm = create_llm()

        branch_descriptions = []
        for i, bm in enumerate(branches_metadata):
            branch_descriptions.append(
                f"{i}. {bm.get('branch_name', 'Unknown')}: "
                f"{bm.get('description', 'No description')}"
            )

        prompt = (
            f"Given the following conversation branches and the user instruction:\n"
            f"Instruction: {custom_instruction}\n\n"
            f"Branches:\n" + "\n".join(branch_descriptions) + "\n\n"
            f"Select the {needed} most relevant branch indices (0-based) "
            f"for generating test cases. Return ONLY a JSON array of integers."
        )
        messages = [{"role": "user", "content": prompt}]
        response = llm._get_completion_content(messages)

        indices = [
            int(x)
            for x in re.findall(r"\d+", response)
            if int(x) < len(branches_metadata)
        ]
        indices = indices[:needed]

        if indices:
            return [branches_metadata[i] for i in indices]
        else:
            return random.sample(
                branches_metadata, min(needed, len(branches_metadata))
            )
    except Exception:
        logger.exception("LLM branch filtering failed, falling back to random sample")
        return random.sample(branches_metadata, min(needed, len(branches_metadata)))
