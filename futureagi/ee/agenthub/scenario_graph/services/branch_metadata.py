"""Branch metadata processing — extract and enrich raw branches.

Replaces ESA methods: _create_branch_metadata_dict, _generate_branch_description,
_extract_conversation_flow, _create_conversation_flow_description,
_create_branch_metadata_strings.
"""

import json
from typing import Any, Dict, List

import structlog

from ee.agenthub.scenario_graph.prompt import BRANCH_DESCRIPTION_PROMPT
from ee.agenthub.scenario_graph.services.llm_factory import create_llm

logger = structlog.get_logger(__name__)


def create_branch_metadata_dict(detailed_branch: Dict[str, Any]) -> tuple:
    detailed_path = detailed_branch.get("detailedPath", [])
    conversation_flow = create_conversation_flow_description(detailed_path)
    branch_description, llm_cost = generate_branch_description(detailed_branch)

    path_nodes = detailed_branch.get("path", [])
    branch_name = (
        " -> ".join(path_nodes)
        if path_nodes
        else (detailed_branch.get("end_node") or "unknown")
    )
    start_node = detailed_branch.get("start_node") or "unknown"
    end_node = detailed_branch.get("end_node") or "unknown"

    return {
        "branch_name": branch_name,
        "start_node": start_node,
        "end_node": end_node,
        "path_length": detailed_branch.get("length", 0),
        "path": path_nodes,
        "detailedPath": detailed_path,
        "description": branch_description,
        "conversation_flow": conversation_flow,
    }, llm_cost


def generate_branch_description(detailed_branch: Dict[str, Any]) -> tuple:
    path = detailed_branch.get("path", [])
    start_node = detailed_branch.get("start_node") or "unknown"
    end_node = detailed_branch.get("end_node") or "unknown"

    prompt = BRANCH_DESCRIPTION_PROMPT.format(
        path_nodes=", ".join(path),
        start_node=start_node,
        end_node=end_node,
    )

    fallback = f"conversation ending at {end_node.lower().replace('_', ' ')}"
    try:
        llm = create_llm()
        messages = [{"role": "user", "content": prompt}]
        response = llm._get_completion_content(messages)
        llm_cost = dict(llm.cost)
        llm_cost["token_usage"] = dict(llm.token_usage)
        return response.strip().strip('"').strip("'"), llm_cost
    except Exception as e:
        logger.warning(f"Error generating branch description: {e}")
        return fallback, {}


def extract_conversation_flow(detailed_branch: Dict[str, Any]) -> str:
    """Extract conversation flow from detailedPath, keeping only essential node data.

    Returns a JSON string of flow nodes.
    """
    detailed_path = detailed_branch.get("detailedPath", [])

    flow_nodes = []
    for node in detailed_path:
        node_info = {}
        if node.get("name"):
            node_info = {"name": node["name"]}
        if node.get("type"):
            node_info["type"] = node["type"]
        if node.get("messagePlan"):
            node_info["messagePlan"] = node["messagePlan"]
        if node.get("prompt"):
            node_info["prompt"] = node["prompt"]
        if node.get("firstMessage"):
            node_info["firstMessage"] = node["firstMessage"]
        if node.get("edgeCondition"):
            node_info["edgeCondition"] = node["edgeCondition"]
        if node.get("tool"):
            node_info["tool"] = node["tool"]
        if node.get("globalNodePlan"):
            node_info["globalNodePlan"] = node["globalNodePlan"]
        if node.get("variableExtractionPlan"):
            node_info["variableExtractionPlan"] = node["variableExtractionPlan"]

        flow_nodes.append(node_info)

    return json.dumps(flow_nodes, indent=2)


def create_conversation_flow_description(detailed_path: List[Dict[str, Any]]) -> str:
    """Create a human-readable conversation flow description from detailed path.

    Args:
        detailed_path: List of node dicts from branch's detailedPath.

    Returns:
        Multi-line string describing the step-by-step flow.
    """
    try:
        flow_parts = []

        for i, node_info in enumerate(detailed_path):
            node_name = node_info.get("name", "unknown")
            node_type = node_info.get("type", "conversation")
            prompt = node_info.get("prompt", "")
            first_message = node_info.get("firstMessage", "")
            edge_condition = node_info.get("edgeCondition", {})

            flow_parts.append(f"Step {i + 1}: {node_name} ({node_type})")

            if prompt:
                flow_parts.append(f"  Prompt: {prompt}")

            if first_message:
                flow_parts.append(f"  Message: {first_message}")

            if edge_condition and edge_condition.get("prompt"):
                flow_parts.append(f"  Condition: {edge_condition['prompt']}")

            flow_parts.append("")  # Empty line for readability

        return "\n".join(flow_parts)

    except Exception as e:
        logger.exception(f"Error creating conversation flow description: {e}")
        return "Error creating conversation flow"


def create_single_branch_metadata_string(metadata: Dict[str, Any]) -> str:
    """Create a formatted metadata string for a single branch (for SDA injection).

    Args:
        metadata: Branch metadata dict with branch_name, description, etc.

    Returns:
        Multi-line metadata string.
    """
    branch_name = metadata.get("branch_name", "unknown")
    metadata_lines = [
        "Conversation Branch Information:",
        f"- Branch Name: {branch_name}",
        f"- Description: {metadata.get('description', '')}",
        f"- Start Node: {metadata.get('start_node', 'unknown')}",
        f"- End Node: {metadata.get('end_node', 'unknown')}",
        f"- Conversation Flow: {metadata.get('conversation_flow', '')}",
    ]
    return "\n".join(metadata_lines)


def create_branch_metadata_strings(
    branch_metadatas: List[Dict[str, Any]],
) -> List[Dict[str, str]]:
    """Create formatted metadata string payloads for each branch (for SDA injection).

    Args:
        branch_metadatas: List of branch metadata dicts.

    Returns:
        List of dicts with 'branch_name' and 'metadata_string' keys.
    """
    metadata_payloads: List[Dict[str, str]] = []
    for metadata in branch_metadatas:
        metadata_payloads.append(
            {
                "branch_name": metadata.get("branch_name", "unknown"),
                "metadata_string": create_single_branch_metadata_string(metadata),
            }
        )
    return metadata_payloads
