"""Walker resolution for `scenario_columns.<name>.value` and bare-head paths."""

from simulate.temporal.activities.xl import (
    PATH_MISSING,
    walk_subject_path,
)


def _subjects_with(scenario_columns=None, scenario_graph=None):
    subjects = {
        "call": object(),
        "agent": None,
        "agent_version": None,
        "persona": None,
        "prompt": None,
        "scenario": None,
        "simulation": None,
    }
    if scenario_columns is not None:
        subjects["scenario_columns"] = scenario_columns
    if scenario_graph is not None:
        subjects["scenario_graph"] = scenario_graph
    return subjects


def test_walker_resolves_scenario_columns_dot_path():
    subjects = _subjects_with(
        {
            "Ideal Outcome": {
                "value": "the golden path",
                "column_name": "Ideal Outcome",
                "dataset_column_id": "col-uuid-1",
            }
        }
    )
    assert (
        walk_subject_path(subjects, "scenario_columns.Ideal Outcome.value")
        == "the golden path"
    )


def test_walker_resolves_column_name_and_dataset_column_id_subpaths():
    subjects = _subjects_with(
        {"persona": {"value": "Sarah", "column_name": "persona", "dataset_column_id": "u"}}
    )
    assert walk_subject_path(subjects, "scenario_columns.persona.column_name") == "persona"
    assert (
        walk_subject_path(subjects, "scenario_columns.persona.dataset_column_id") == "u"
    )


def test_walker_returns_none_for_unknown_column():
    subjects = _subjects_with({"Ideal Outcome": {"value": "x"}})
    # missing intermediate → None (caller stringifies to empty)
    assert walk_subject_path(subjects, "scenario_columns.NoSuchColumn.value") is None


def test_walker_returns_none_when_scenario_columns_dict_is_empty():
    # head matches, sub-segment misses → None (not PATH_MISSING)
    subjects = _subjects_with({})
    assert walk_subject_path(subjects, "scenario_columns.anything.value") is None


def test_walker_returns_path_missing_when_scenario_columns_key_absent():
    # No scenario_columns key → PATH_MISSING (caller falls to mismatch error)
    subjects = {
        "call": object(),
        "agent": None,
        "agent_version": None,
        "persona": None,
        "prompt": None,
        "scenario": None,
        "simulation": None,
    }
    assert (
        walk_subject_path(subjects, "scenario_columns.anything.value") is PATH_MISSING
    )


def test_walker_resolves_scenario_graph_node_path():
    subjects = _subjects_with(
        scenario_graph={
            "nodes": [{"id": "n1", "type": "intent", "data": {"label": "Greet"}}],
            "edges": [{"source": "n1", "target": "n2"}],
        }
    )
    assert (
        walk_subject_path(subjects, "scenario_graph.nodes.0.type") == "intent"
    )
    assert (
        walk_subject_path(subjects, "scenario_graph.nodes.0.data.label") == "Greet"
    )
    assert (
        walk_subject_path(subjects, "scenario_graph.edges.0.source") == "n1"
    )


def test_walker_returns_none_for_unknown_scenario_graph_path():
    subjects = _subjects_with(scenario_graph={"nodes": []})
    assert walk_subject_path(subjects, "scenario_graph.nodes.99.type") is None


def test_walker_returns_none_when_scenario_graph_dict_is_empty():
    subjects = _subjects_with(scenario_graph={})
    assert walk_subject_path(subjects, "scenario_graph.nodes.0.type") is None


def test_walker_returns_path_missing_when_scenario_graph_key_absent():
    subjects = _subjects_with(scenario_columns={})
    assert (
        walk_subject_path(subjects, "scenario_graph.nodes.0.type") is PATH_MISSING
    )


class _CallStub:
    """CallExecution stub for the walker's bare-head fall-through."""

    call_type = "Inbound"
    duration = 42
    overall_score = 0.87
    audio_url = None
    avg_agent_latency_ms = 850


def _subjects_with_call(call):
    return {
        "call": call,
        "agent": None,
        "agent_version": None,
        "persona": None,
        "prompt": None,
        "scenario": None,
        "simulation": None,
    }


def test_walker_resolves_bare_head_via_call_subject():
    subjects = _subjects_with_call(_CallStub())
    assert walk_subject_path(subjects, "call_type") == "Inbound"
    assert walk_subject_path(subjects, "duration") == 42
    assert walk_subject_path(subjects, "overall_score") == 0.87
    assert walk_subject_path(subjects, "avg_agent_latency_ms") == 850


def test_walker_returns_none_for_bare_head_attr_with_none_value():
    subjects = _subjects_with_call(_CallStub())
    assert walk_subject_path(subjects, "audio_url") is None


def test_walker_returns_path_missing_for_bare_head_not_on_any_subject():
    subjects = _subjects_with_call(_CallStub())
    assert walk_subject_path(subjects, "totally_made_up_field") is PATH_MISSING


def test_walker_returns_path_missing_for_empty_string():
    subjects = _subjects_with_call(_CallStub())
    assert walk_subject_path(subjects, "") is PATH_MISSING


class _AgentStub:
    """AgentDefinition stub with a secret-shaped field the walker must refuse."""

    agent_name = "Public Agent Name"
    api_key = "sk-super-secret-do-not-leak"
    api_secret = "shhh"
    credentials = object()  # simulate a related-model traversal target


def test_walker_resolves_deep_dict_value_under_scenario_columns():
    subjects = _subjects_with(
        {
            "persona": {
                "value": {"name": "Eleanor", "personality": "Anxious"},
                "column_name": "persona",
                "dataset_column_id": "u",
            }
        }
    )
    assert (
        walk_subject_path(subjects, "scenario_columns.persona.value.personality")
        == "Anxious"
    )
    assert (
        walk_subject_path(subjects, "scenario_columns.persona.value.name")
        == "Eleanor"
    )


def test_walker_blocks_secret_shaped_attrs_on_model_subjects():
    class _Stub:
        agent_name = "Public Agent Name"

    blocked = [
        "api_key", "api_secret", "secret", "secret_key", "password",
        "token", "access_token", "refresh_token", "access_key",
        "private_key", "credentials", "credentials_legacy",
    ]
    stub = _Stub()
    for name in blocked:
        setattr(stub, name, f"leaked:{name}")
    subjects = {
        "call": _CallStub(), "agent": stub,
        "agent_version": None, "persona": None, "prompt": None,
        "scenario": None, "simulation": None,
    }
    for name in blocked:
        for prefix in ("agent.", ""):
            path = f"{prefix}{name}"
            result = walk_subject_path(subjects, path)
            assert result in (PATH_MISSING, None), (
                f"path {path!r} leaked {result!r}; blocklist must cover attribute access"
            )
    # camelCase coercion also blocks
    assert walk_subject_path(subjects, "agent.apiKey") in (PATH_MISSING, None)
    assert walk_subject_path(subjects, "agent.accessToken") in (PATH_MISSING, None)
    # sanity: non-blocked public attr resolves
    assert walk_subject_path(subjects, "agent.agent_name") == "Public Agent Name"


def test_walker_blocks_secret_shaped_keys_in_dict_traversal():
    """Dict-key lookups (JSONField payloads) must also respect the blocklist."""
    subjects = {
        "call": type("C", (), {
            "provider_call_data": {"vapi": {"api_key": "sk-LEAK", "id": "call_xyz"}},
        })(),
        "agent": None, "agent_version": None, "persona": None,
        "prompt": None, "scenario": None, "simulation": None,
    }
    for path in ("provider_call_data.vapi.api_key",
                 "provider_call_data.vapi.apiKey",
                 "call.provider_call_data.vapi.api_secret"):
        result = walk_subject_path(subjects, path)
        assert result in (PATH_MISSING, None), (
            f"dict-key leak: {path!r} returned {result!r}"
        )
    # sibling non-secret key still resolves
    assert walk_subject_path(subjects, "provider_call_data.vapi.id") == "call_xyz"
