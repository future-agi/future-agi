"""
Output Sink ABC and Registry.

This module defines the abstract base class for pluggable output sinks and
provides a registry for managing them. Output sinks receive node execution
results and store them in external systems (e.g., model_hub.Cell, custom tables).

Follows the same ABC + Registry pattern as integrations/services/base.py
and tracer/utils/adapters/base.py.

Adding New Sinks:
    1. Create a new file in output_sinks/ (e.g., output_sinks/my_sink.py)
    2. Subclass BaseOutputSink and implement the store() method
    3. Self-register at the bottom: register_sink("my_sink", MySink())
    4. Import the module in output_sinks/__init__.py to trigger registration

Example:
    from agent_playground.services.engine.output_sink import BaseOutputSink, register_sink

    class MySink(BaseOutputSink):
        def store(self, context: OutputSinkContext) -> None:
            # Write outputs to your storage
            my_storage.write(context.outputs)

    register_sink("my_sink", MySink())
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


@dataclass
class OutputSinkContext:
    """Context passed to output sinks when storing node results."""

    node_id: str
    node_name: str
    template_name: str
    inputs: dict[str, Any]
    outputs: dict[str, Any]
    status: str  # "SUCCESS" or "FAILED"
    config: dict[str, Any]  # Per-sink config
    metadata: dict[str, Any] = field(default_factory=dict)


class BaseOutputSink(ABC):
    """
    Abstract base class for output sinks.

    Output sinks receive node execution results and write them to external
    storage systems. They are called AFTER the normal ExecutionData routing,
    and their failures never affect node execution status.
    """

    @abstractmethod
    def store(self, context: OutputSinkContext) -> None:
        """
        Store node execution results.

        Args:
            context: OutputSinkContext with node info, inputs, outputs, and config.

        Raises:
            Exception: Any exception will be caught by call_sinks() and logged.
                       Sink failures never affect node execution.
        """
        ...

    def validate_config(self, config: dict[str, Any]) -> None:  # noqa: B027
        """
        Optional hook: validate sink-specific config before execution.

        Override in subclass to check required keys, types, etc.
        Raise ValueError with a descriptive message on invalid config.

        Args:
            config: The per-sink config dict to validate.
        """
        pass


# =============================================================================
# Registry
# =============================================================================

_SINK_REGISTRY: dict[str, BaseOutputSink] = {}


def register_sink(name: str, sink: BaseOutputSink) -> None:
    """
    Register an output sink.

    Args:
        name: Unique sink identifier (e.g., "cell")
        sink: An instance of BaseOutputSink

    Raises:
        TypeError: If sink doesn't subclass BaseOutputSink
    """
    if not isinstance(sink, BaseOutputSink):
        raise TypeError(
            f"Sink '{name}' must be a subclass of BaseOutputSink. "
            f"Got {type(sink).__name__}"
        )
    _SINK_REGISTRY[name] = sink


def get_sink(name: str) -> BaseOutputSink:
    """
    Get a registered output sink by name.

    Args:
        name: The sink identifier

    Returns:
        The registered BaseOutputSink instance

    Raises:
        KeyError: If no sink is registered with that name
    """
    sink = _SINK_REGISTRY.get(name)
    if sink is None:
        raise KeyError(
            f"No output sink registered for '{name}'. "
            f"Available: {list(_SINK_REGISTRY.keys())}"
        )
    return sink


def has_sink(name: str) -> bool:
    """Check if a sink is registered."""
    return name in _SINK_REGISTRY


def list_sinks() -> list[str]:
    """List all registered sink names."""
    return list(_SINK_REGISTRY.keys())


def clear_sinks() -> None:
    """Clear all registered sinks. Primarily used for testing."""
    _SINK_REGISTRY.clear()


# =============================================================================
# Sink Invocation Helper
# =============================================================================


def call_sinks(
    sink_configs: list[dict[str, Any]],
    context_kwargs: dict[str, Any],
) -> list[dict[str, Any]]:
    """
    Resolve and call output sinks. Catches all errors — sink failures
    never affect node execution.

    Args:
        sink_configs: List of sink config dicts, each with:
            - "name": str — registered sink name
            - "config": dict — per-sink config passed to store()
        context_kwargs: Dict with keys matching OutputSinkContext fields
            (minus "config", which comes from each sink_config).
            Must include: node_id, node_name, template_name, inputs, outputs, status.
            Optional: metadata.

    Returns:
        List of result dicts, one per sink_config:
            - {"sink": name, "status": "SUCCESS"} on success
            - {"sink": name, "status": "FAILED", "error": str} on failure
    """
    results = []

    for sink_config in sink_configs:
        name = sink_config.get("name", "")
        config = sink_config.get("config", {})

        try:
            sink = get_sink(name)

            context = OutputSinkContext(
                config=config,
                **context_kwargs,
            )

            sink.store(context)
            results.append({"sink": name, "status": "SUCCESS"})

        except Exception as e:
            logger.exception(
                "output_sink_failed",
                sink_name=name,
                node_id=context_kwargs.get("node_id", ""),
                error=str(e),
            )
            results.append(
                {
                    "sink": name,
                    "status": "FAILED",
                    "error": str(e),
                    "error_type": type(e).__name__,
                }
            )

    return results
