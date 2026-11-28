import pytest

from agent_playground.models import ExecutionData, GraphExecution, NodeExecution
from agent_playground.models.choices import NodeExecutionStatus
from agent_playground.services.engine.analyzer import GraphAnalyzer
from agent_playground.services.engine.readiness import check_node_readiness


@pytest.mark.django_db
@pytest.mark.parametrize("has_output", [False, True], ids=["missing", "invalid"])
def test_unusable_connected_output(graph_version, edge, has_output):
    topology = GraphAnalyzer.analyze(graph_version.id)
    source = topology.ports[edge.source_port_id]
    target = topology.ports[edge.target_port_id]
    execution = GraphExecution.no_workspace_objects.create(graph_version=graph_version)
    producer = NodeExecution.no_workspace_objects.create(
        graph_execution=execution,
        node_id=source.node_id,
        status=NodeExecutionStatus.RUNNING,
    )

    def readiness():
        return check_node_readiness(target.node_id, topology, execution.id)

    # A still-running producer can supply the input, even if no row exists yet.
    waiting = readiness()
    assert not waiting.is_ready and not waiting.should_skip

    if has_output:
        # The source port requires a string.
        output = ExecutionData.no_workspace_objects.create(
            node_execution=producer, port=source, payload=42
        )
        assert not output.is_valid
    producer.status = NodeExecutionStatus.SUCCESS
    producer.save(update_fields=["status"])

    blocked = readiness()
    assert not blocked.is_ready
    assert blocked.should_skip
    (port,) = blocked.unsatisfied_ports
    assert port.port_id == target.id
    assert port.has_data is has_output

    # Even an empty string is a configured default, not a missing one.
    target.default_value = ""
    fallback = readiness()
    assert fallback.is_ready and not fallback.should_skip
