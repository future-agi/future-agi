"""Supplemental r2 routed preflight controls, authored after the product fix.

NOT_RUN locally. Keep real authentication, domain admission, managers and signals.
"""

from unittest.mock import AsyncMock, patch

import pytest

from accounts.models.workspace import Workspace
from accounts.tests.personal_keys import session_client_for
from model_hub.services import experiment_target_admission as admission
from model_hub.tests import test_experiment_agent_target_admission as old
from model_hub.tests import test_experiment_native_graph as native
from model_hub.tests import test_experiment_target_kind as kind
from model_hub.tests import test_experiment_target_scope as scope

actors = scope.actors
tenants = old.tenants
console_flag = kind.console_flag
pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize(
    "case",
    [
        "missing_default",
        "deleted_default",
        "inactive_default",
        "revoked_org",
        "revoked_workspace",
    ],
)
def test_legacy_source_create_denial_precedes_snapshot_and_intent(
    actors, console_flag, case
):
    actor = actors["a"]
    scope.null_workspace(actor)
    graph, version, _ = native.make_graph(actor)
    body = native.payload(actor, graph, version)
    client = session_client_for(actor["user"], actor["org"], actor["workspace"])
    if case in {"missing_default", "deleted_default", "inactive_default"}:
        change = {
            "missing_default": {"is_default": False},
            "deleted_default": {"deleted": True},
            "inactive_default": {"is_active": False},
        }[case]
        Workspace._base_manager.filter(pk=actor["workspace"].pk).update(**change)
    else:
        kind.unavailable(None, actor, case)
    before = old.state(actor["exp"]["experiment"])
    workspaces = list(Workspace._base_manager.order_by("pk").values())
    with patch(
        "tfc.temporal.experiments.admission.get_client", new_callable=AsyncMock
    ) as transport:
        response = client.post(old.V2, body, format="json")
    assert response.status_code in (401, 403, 404), response.content
    transport.assert_not_awaited()
    assert old.state(actor["exp"]["experiment"]) == before
    assert list(Workspace._base_manager.order_by("pk").values()) == workspaces


@pytest.mark.parametrize("legacy_dataset", [False, True])
def test_operator_update_checks_execution_owner_before_config_and_column_writes(
    actors, console_flag, legacy_dataset
):
    actor = actors["a"]
    if legacy_dataset:
        scope.null_workspace(actor)
    agent = old.stored_agent(actor)
    scope.dependent_outputs(agent)
    _, client = scope.operator_client(actor)
    graph, version, _ = native.make_graph(actor)
    body = {
        "column_id": str(actor["exp"]["output_column"].pk),
        "prompt_config": [
            old.resent_agent(
                agent, agent_id=str(graph.pk), agent_version=str(version.pk)
            )
        ],
    }
    kind.unavailable(agent, actor, "revoked_org")
    before = kind.execution_state(agent)
    with patch(
        "tfc.temporal.experiments.admission.get_client", new_callable=AsyncMock
    ) as transport:
        response = client.put(f"{old.V2}{agent.experiment.pk}/", body, format="json")
    old.assert_refused(response)
    assert response.json() == admission.refusal_response().data
    assert kind.execution_state(agent) == before
    transport.assert_not_awaited()
