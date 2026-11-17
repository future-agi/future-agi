"""The experiment picker consumes the graph list's active version, not its graph id."""
from types import SimpleNamespace
from uuid import UUID

from agent_playground.serializers.graph import GraphListSerializer


def test_graph_list_publishes_active_version_id_not_graph_id():
    graph_id = UUID("00000000-0000-4000-8000-000000000001")
    version_id = UUID("00000000-0000-4000-8000-000000000002")
    graph = SimpleNamespace(
        id=graph_id,
        name="Version contract fixture",
        description="",
        is_template=False,
        created_by=None,
        collaborators=[],
        _active_version_id=version_id,
        _active_version_number=3,
        _node_count=2,
    )
    payload = GraphListSerializer(graph).data
    assert payload["id"] == str(graph_id)
    assert payload["active_version_id"] == str(version_id)
    assert payload["active_version_id"] != payload["id"]
    assert payload["active_version_number"] == 3
    assert "activeVersionId" not in payload
