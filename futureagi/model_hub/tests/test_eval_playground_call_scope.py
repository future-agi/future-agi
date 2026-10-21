from pathlib import Path

from model_hub.serializers.eval_runner import EvalPlayGroundSerializer

_UUIDS = {
    "template_id": "00000000-0000-4000-8000-000000000001",
    "call_id": "00000000-0000-4000-8000-000000000002",
    "run_test_id": "00000000-0000-4000-8000-000000000003",
}


def test_call_id_requires_its_selected_run_test_scope():
    serializer = EvalPlayGroundSerializer(
        data={
            "template_id": _UUIDS["template_id"],
            "call_id": _UUIDS["call_id"],
        }
    )
    assert not serializer.is_valid()
    assert set(serializer.errors) == {"run_test_id"}

    scoped = EvalPlayGroundSerializer(data=_UUIDS)
    assert scoped.is_valid(), scoped.errors


def test_call_lookup_is_bound_to_run_test_tenant_and_workspace():
    source = (
        Path(__file__).resolve().parents[1] / "views" / "separate_evals.py"
    ).read_text()
    assert 'validated_data.get("run_test_id")' in source
    assert (
        'run_test_workspace_filter(\n                                request, "test_execution__run_test"'
        in source
    )
    assert "test_execution__run_test_id=_run_test_id" in source
    assert "test_execution__run_test__organization=org" in source
    assert "test_execution__run_test__deleted=False" in source
    assert "test_execution__deleted=False" in source


def test_simulation_playground_posts_the_selected_run_test_with_call_id():
    source = (
        Path(__file__).resolve().parents[3]
        / "frontend"
        / "src"
        / "sections"
        / "evals"
        / "components"
        / "SimulationTestMode.jsx"
    ).read_text()
    assert "{ call_id: _callId, run_test_id: selectedRunTestId }" in source
