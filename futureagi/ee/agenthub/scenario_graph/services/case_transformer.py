"""Case transformation — convert SDA output into normalized scenario cases.

Replaces ESA methods: _convert_sda_data_to_cases, _enrich_cases_with_branch_data,
_normalize_cases, _sample_cases_across_branches.
"""

import ast
import json
import math
import random
from typing import Any, Dict, List, Optional, Union

import structlog

from simulate.models.scenario_graph import NodeType

logger = structlog.get_logger(__name__)


def convert_sda_data_to_cases(
    generated_data,
    no_of_rows: int,
    custom_columns: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """Convert SDA generated data into scenario case dicts.

    When more rows are generated than requested, samples evenly across branches.

    Args:
        generated_data: SDA output — DataFrame, list of dicts, or dict with "data" key.
        no_of_rows: Target number of cases.
        custom_columns: Optional custom column definitions.

    Returns:
        List of case dicts with keys: name, persona, situation, outcome,
        conversation_branch, branch_category, plus any custom column values.
    """
    all_cases: List[Dict[str, Any]] = []

    try:
        # Extract rows from SDA response format
        if isinstance(generated_data, dict) and "data" in generated_data:
            data_rows = generated_data["data"]
        elif isinstance(generated_data, list):
            data_rows = generated_data
        else:
            # DataFrame or other format
            if hasattr(generated_data, "to_dict"):
                data_rows = generated_data.to_dict("records")
            else:
                data_rows = generated_data

        for i, row in enumerate(data_rows):
            if not isinstance(row, dict):
                logger.warning(f"Skipping non-dict row: {type(row)}")
                continue

            persona = row.get("persona", "")
            situation = _safe_str_value(row.get("situation"), "")
            outcome = _safe_str_value(row.get("outcome"), "")
            conversation_branch = _safe_str_value(row.get("branch_name"), "")
            branch_category = _safe_str_value(row.get("branch_category"), "")

            if not situation:
                logger.debug(f"Skipping row {i} - no situation found")
                continue

            # Parse persona
            if isinstance(persona, str):
                try:
                    persona_data = json.loads(persona)
                except json.JSONDecodeError:
                    try:
                        persona_data = ast.literal_eval(persona)
                    except (ValueError, SyntaxError):
                        persona_data = {"description": persona}
            elif isinstance(persona, dict):
                persona_data = {k: v for k, v in persona.items() if k is not None}
            else:
                persona_data = {"description": str(persona) if persona else ""}

            case = {
                "name": f"Case_{i + 1}_{situation[:20].replace(' ', '_')}",
                "persona": persona_data,
                "situation": situation,
                "outcome": outcome,
                "conversation_branch": conversation_branch,
                "branch_category": branch_category,
            }

            # Add custom column values
            if custom_columns:
                for column in custom_columns:
                    column_name = column.get("name")
                    if column_name and column_name in row:
                        col_val = row[column_name]
                        if col_val is None or (
                            isinstance(col_val, float) and math.isnan(col_val)
                        ):
                            case[column_name] = ""
                        else:
                            case[column_name] = col_val

            all_cases.append(case)

        # Sample if more cases than requested
        if no_of_rows and len(all_cases) > no_of_rows:
            cases = sample_cases_across_branches(all_cases, no_of_rows)
        else:
            cases = all_cases

    except Exception as e:
        logger.exception(f"Error converting SDA data to cases: {e}")
        cases = all_cases if all_cases else []

    logger.info(
        f"Converted {len(cases)} cases from SDA data (from {len(all_cases)} total)"
    )
    return cases


def enrich_cases_with_branch_data(
    cases: List[Dict[str, Any]],
    branch_metadata_lookup: Dict[str, Dict[str, Any]],
    custom_columns: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """Enrich cases with branch-specific data and normalize.

    Args:
        cases: Raw cases with conversation_branch field.
        branch_metadata_lookup: Mapping from branch_name to full branch metadata.
        custom_columns: Optional custom column definitions (for normalize).

    Returns:
        Normalized cases with correct detailed_path per branch.
    """
    enriched: List[Dict[str, Any]] = []
    unmatched_branches: set = set()

    for case in cases:
        branch_name = case.get("conversation_branch", "")
        branch_metadata = branch_metadata_lookup.get(branch_name)

        if branch_metadata:
            case["detailed_path"] = branch_metadata.get("detailedPath", [])
        else:
            if branch_name and branch_name not in unmatched_branches:
                logger.warning(f"No metadata found for branch: {branch_name}")
                unmatched_branches.add(branch_name)
            case["detailed_path"] = []

        enriched.append(case)

    if unmatched_branches:
        logger.warning(f"Total unmatched branches: {len(unmatched_branches)}")

    return normalize_cases(enriched, custom_columns)


def normalize_cases(
    cases: List[Dict[str, Any]],
    custom_columns: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """Normalize cases to allowed node types.

    Allowed node types in output: message, condition, end.
    Start is implicit and removed if present.
    """
    # Map of raw type strings to normalized NodeType values
    type_mapping = {
        NodeType.MESSAGE: NodeType.MESSAGE,
        NodeType.CONDITION: NodeType.CONDITION,
        "conditional": NodeType.CONDITION,  # Alias
        NodeType.END: NodeType.END,
    }

    normalized: List[Dict[str, Any]] = []

    for c in cases:
        name = str(c.get("name", "Case")).strip() or "Case"
        persona_raw = c.get("persona", "")
        persona: Union[Dict, str]
        if isinstance(persona_raw, dict):
            persona = persona_raw
        else:
            persona = str(persona_raw).strip()

        single_situation = str(c.get("situation", "")).strip()
        outcome = str(c.get("outcome", "")).strip()

        nodes: List[Dict[str, Any]] = []
        for n in c.get("nodes", []):
            t_raw = str(n.get("type", "")).lower()

            # Skip start nodes (start is implicit)
            if t_raw == NodeType.START:
                continue

            normalized_type = type_mapping.get(t_raw)
            if normalized_type is None:
                continue

            nodes.append(
                {
                    "type": normalized_type,
                    "label": n.get("label")
                    or (
                        "End"
                        if normalized_type == NodeType.END
                        else NodeType.get_display_name(normalized_type)
                    ),
                    "config": n.get("config") or {},
                    "terminal": bool(
                        n.get("terminal", NodeType.is_terminal(normalized_type))
                    ),
                }
            )

        conversation_branch = str(c.get("conversation_branch", "")).strip()
        branch_category = str(c.get("branch_category", "")).strip()
        detailed_path = c.get("detailed_path", [])

        normalized_case = {
            "name": name,
            "persona": persona,
            "situation": single_situation,
            "outcome": outcome,
            "conversation_branch": conversation_branch,
            "branch_category": branch_category,
            "nodes": nodes,
            "detailed_path": detailed_path,
        }

        # Add custom columns to normalized case
        if custom_columns:
            for column in custom_columns:
                column_name = column.get("name")
                if column_name and column_name in c:
                    normalized_case[column_name] = c[column_name]

        normalized.append(normalized_case)

    return normalized


def sample_cases_across_branches(
    cases: List[Dict[str, Any]], target_count: int
) -> List[Dict[str, Any]]:
    """Sample cases evenly across branches to ensure diversity.

    Args:
        cases: All generated cases.
        target_count: Number of cases to return.

    Returns:
        Sampled cases with even distribution across branches.
    """
    if not cases or target_count <= 0:
        return []

    # Group cases by branch
    branch_to_cases: Dict[str, List[Dict[str, Any]]] = {}
    for case in cases:
        branch = case.get("conversation_branch", "unknown")
        if branch not in branch_to_cases:
            branch_to_cases[branch] = []
        branch_to_cases[branch].append(case)

    branches = list(branch_to_cases.keys())
    num_branches = len(branches)

    if num_branches == 0:
        return cases[:target_count]

    base_per_branch = target_count // num_branches
    remainder = target_count % num_branches

    sampled_cases: List[Dict[str, Any]] = []
    for i, branch in enumerate(branches):
        branch_cases = branch_to_cases[branch]
        take_count = base_per_branch + (1 if i < remainder else 0)
        if len(branch_cases) <= take_count:
            sampled_cases.extend(branch_cases)
        else:
            sampled_cases.extend(random.sample(branch_cases, take_count))

    random.shuffle(sampled_cases)

    logger.info(
        f"Sampled {len(sampled_cases)} cases across {num_branches} branches "
        f"(target: {target_count})"
    )
    return sampled_cases


def _safe_str_value(val, default=""):
    """Safely convert value to string, handling None and NaN."""
    if val is None:
        return default
    if isinstance(val, float) and math.isnan(val):
        return default
    return str(val) if val else default
