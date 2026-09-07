"""
Service for testing a single node before committing it to the workflow.

The functions here call a node's registered runner directly using
caller-supplied (possibly unsaved) configuration and sample inputs. They
never write to Node.config, PromptTemplateNode, GraphExecution, or any other
persisted state — a test run has no effect on the saved workflow.
"""

from typing import Any

import structlog

from agent_playground.models.choices import NodeType
from agent_playground.models.node import Node
from agent_playground.services.engine import get_runner, has_runner

logger = structlog.get_logger(__name__)


class NodeNotTestableError(Exception):
    """Raised when a node cannot be test-run (wrong type or no runner)."""


def build_test_config(
    node: Node, prompt_template_data: dict[str, Any] | None
) -> dict[str, Any]:
    """
    Build the config dict passed to the node's runner for a test run.

    For llm_prompt nodes, mirrors the shape of
    PromptVersion.prompt_config_snapshot (``{"messages": [...],
    "configuration": {...}}``) so LLMPromptRunner can use it in test mode
    instead of reading the saved PromptTemplateNode/PromptVersion. Callers
    that don't supply an override (e.g. non-LLM node types, or an LLM node
    tested with no edits) fall back to the node's saved config.

    Args:
        node: The Node being tested.
        prompt_template_data: Validated ``PromptTemplateDataSerializer``
            output representing the (possibly unsaved) prompt form values,
            or None.

    Returns:
        Config dict to pass to ``runner.run(config, inputs, execution_context)``.
    """
    template_name = node.node_template.name if node.node_template else None

    if template_name != "llm_prompt" or not prompt_template_data:
        return node.config or {}

    pt = prompt_template_data
    configuration = {
        "model": pt.get("model"),
        "temperature": pt.get("temperature"),
        "max_tokens": pt.get("max_tokens"),
        "top_p": pt.get("top_p"),
        "frequency_penalty": pt.get("frequency_penalty"),
        "presence_penalty": pt.get("presence_penalty"),
        "output_format": pt.get("output_format"),
        "response_format": pt.get("response_format", "text"),
        "tools": pt.get("tools") or [],
        "tool_choice": pt.get("tool_choice"),
        "model_detail": pt.get("model_detail") or {"type": "chat"},
        "template_format": pt.get("template_format") or "mustache",
    }
    return {
        "messages": pt.get("messages", []),
        "configuration": configuration,
    }


def run_node_test(
    node: Node,
    prompt_template_data: dict[str, Any] | None,
    inputs: dict[str, Any] | None,
    organization_id: str | None,
    workspace_id: str | None = None,
) -> dict[str, Any]:
    """
    Execute a single node's runner with test data.

    Never persists anything: no Node/PromptTemplateNode writes, no
    GraphExecution or ExecutionData rows, no output sinks. This is a pure
    "try it and see" call so a user can validate a node before saving it.

    Args:
        node: The Node to test (must be atomic, with a registered runner).
        prompt_template_data: Optional unsaved prompt form values (llm_prompt
            nodes only). See ``build_test_config``.
        inputs: Sample input port values (routing key -> value).
        organization_id: Organization the node belongs to.
        workspace_id: Optional workspace the node belongs to.

    Returns:
        Dict with keys:
            status: "SUCCESS" or "FAILED"
            outputs: Dict of output port values (empty on failure)
            error: Error message string, or None on success

    Raises:
        NodeNotTestableError: If the node is not an atomic node with a
            registered runner (e.g. subgraph nodes, or a template with no
            runner implemented yet).
    """
    if node.type != NodeType.ATOMIC or not node.node_template:
        raise NodeNotTestableError("Only atomic nodes can be tested.")

    template_name = node.node_template.name
    if not has_runner(template_name):
        raise NodeNotTestableError(
            f"Testing is not yet supported for node type '{template_name}'."
        )

    config = build_test_config(node, prompt_template_data)
    execution_context = {
        "organization_id": organization_id,
        "workspace_id": workspace_id,
        "node_id": str(node.id),
        "test_mode": True,
    }

    try:
        runner = get_runner(template_name)
        outputs = runner.run(config, inputs or {}, execution_context)
        return {"status": "SUCCESS", "outputs": outputs, "error": None}
    except Exception as e:
        logger.warning(
            "Node test run failed",
            node_id=str(node.id),
            template_name=template_name,
            error=str(e),
        )
        return {"status": "FAILED", "outputs": {}, "error": str(e)}
