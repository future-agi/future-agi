from __future__ import annotations

import pytest
from rest_framework.test import APIClient

from model_hub.models.evals_metric import EvalTemplate
from simulate.models import SimulateEvalConfig
from simulate.services.harness_evals import (
    MOST_SELECTED_EVALS,
    UnknownEvalSelection,
    create_selected_eval_configs,
    offered_evals,
    resolve_eval_mapping,
    runnable_eval_config_ids,
)
from simulate.services.hosted_harness import create_hosted_job, register_attempt

from .test_hosted_harness_channels import _headers, _payload

BASE = "/simulate/api/harness/attempts"


def _template(name, required_keys, *, organization=None, workspace=None, eval_id=0):
    return EvalTemplate.objects.create(
        name=name,
        description=f"{name} description",
        config={"required_keys": list(required_keys)},
        eval_id=eval_id,
        organization=organization,
        workspace=workspace,
    )


def _provision(client, capability, **extra):
    body = {
        "operation": "provision",
        "name": "Eval selection",
        "modality": "voice",
        "personas": [
            {
                "scenario_key": "refund-request",
                "name": "Customer",
                "situation": "Asks for a refund",
                "outcome": "Agent follows policy",
            }
        ],
    }
    body.update(extra)
    return client.post(
        f"{BASE}/{capability.attempt.id}/scenarios/",
        body,
        format="json",
        **_headers(capability),
    )


@pytest.mark.django_db
def test_a_voice_conversation_binds_to_the_combined_recording():
    """The whole-conversation source for a spoken call is the combined recording, which is what
    the recording-slot guard names as correct; a chat run has no recording and uses transcript."""
    template = _template("customer_agent_conversation_quality", ["conversation"])
    assert resolve_eval_mapping(template, "voice") == {
        "conversation": "voice_recording"
    }
    assert resolve_eval_mapping(template, "text") == {"conversation": "transcript"}


@pytest.mark.django_db
def test_both_prompt_key_names_resolve_to_the_agent_prompt():
    conformance = _template(
        "customer_agent_prompt_conformance", ["system_prompt", "conversation"]
    )
    completion = _template(
        "customer_agent_task_completion", ["agent_prompt", "conversation"]
    )
    assert resolve_eval_mapping(conformance, "voice")["system_prompt"] == "agent_prompt"
    assert resolve_eval_mapping(completion, "voice")["agent_prompt"] == "agent_prompt"


@pytest.mark.django_db
def test_a_template_with_an_unmappable_key_is_refused_rather_than_half_bound():
    """A partial mapping would hand the evaluator an empty variable, and an eval scoring nothing
    still returns a confident verdict."""
    template = _template("customer_agent_odd", ["conversation", "retrieved_context"])
    assert resolve_eval_mapping(template, "voice") is None


@pytest.mark.django_db
def test_the_catalogue_offers_the_family_and_the_numbered_voice_evals(
    organization, workspace
):
    _template("customer_agent_loop_detection", ["conversation"])
    _template("dead_air_detection", ["conversation"], eval_id=201)
    _template("no_misselling", ["conversation"], eval_id=202)
    _template("some_unrelated_eval", ["conversation"], eval_id=12)

    offered = {item["name"] for item in offered_evals(organization, workspace, "voice")}
    assert "customer_agent_loop_detection" in offered
    assert "dead_air_detection" in offered
    assert "no_misselling" in offered
    assert "some_unrelated_eval" not in offered


@pytest.mark.django_db
def test_a_chat_run_is_not_offered_the_voice_only_evals(organization, workspace):
    _template("dead_air_detection", ["conversation"], eval_id=201)
    _template("voicemail_handling", ["conversation"], eval_id=207)
    _template("customer_agent_query_handling", ["conversation"])

    offered = {item["name"] for item in offered_evals(organization, workspace, "text")}
    assert offered == {"customer_agent_query_handling"}


@pytest.mark.django_db
def test_another_tenants_template_is_never_offered(organization, workspace, django_user_model):
    from accounts.models import Organization

    other = Organization.objects.create(name="other-tenant")
    _template("customer_agent_private", ["conversation"], organization=other)
    _template("customer_agent_shared", ["conversation"])

    offered = {item["name"] for item in offered_evals(organization, workspace, "voice")}
    assert offered == {"customer_agent_shared"}


@pytest.mark.django_db
def test_selecting_another_tenants_template_is_refused(organization, workspace):
    """Multi-tenancy: an invisible name must not resolve to that tenant's template and must not
    be silently dropped either."""
    from accounts.models import Organization

    from simulate.services.alk_simulate_ingestion import provision_alk_sim_run_test

    other = Organization.objects.create(name="other-tenant-selection")
    _template("customer_agent_private", ["conversation"], organization=other)
    run_test, _scenarios, _agent = provision_alk_sim_run_test(
        organization,
        workspace=workspace,
        name="tenancy",
        personas=[{"name": "Customer", "situation": "Asks", "outcome": "Answered"}],
        modality="voice",
    )
    with pytest.raises(UnknownEvalSelection) as raised:
        create_selected_eval_configs(
            run_test, ["customer_agent_private"], "voice"
        )
    assert raised.value.names == ["customer_agent_private"]
    assert not SimulateEvalConfig.objects.filter(run_test=run_test).exists()


@pytest.mark.django_db
def test_selection_is_capped_and_idempotent(organization, workspace):
    from simulate.services.alk_simulate_ingestion import provision_alk_sim_run_test

    names = [f"customer_agent_pick_{index}" for index in range(MOST_SELECTED_EVALS + 3)]
    for name in names:
        _template(name, ["conversation"])
    run_test, _scenarios, _agent = provision_alk_sim_run_test(
        organization,
        workspace=workspace,
        name="capped",
        personas=[{"name": "Customer", "situation": "Asks", "outcome": "Answered"}],
        modality="voice",
    )
    first = create_selected_eval_configs(run_test, names, "voice")
    assert len(first) == MOST_SELECTED_EVALS
    again = create_selected_eval_configs(run_test, names, "voice")
    assert {config.id for config in again} == {config.id for config in first}
    assert (
        SimulateEvalConfig.objects.filter(run_test=run_test).count()
        == MOST_SELECTED_EVALS
    )


@pytest.mark.django_db
def test_only_mapped_configs_are_runnable(organization, workspace):
    """A harness result column carries an empty mapping on purpose; running it would feed the
    evaluator nothing, so it must never be dispatched as a platform eval."""
    from simulate.services.alk_simulate_ingestion import (
        _get_or_create_harness_eval_config,
        provision_alk_sim_run_test,
    )

    selected = _template("customer_agent_context_retention", ["conversation"])
    column = _template("harness_column_eval", ["conversation"])
    run_test, _scenarios, _agent = provision_alk_sim_run_test(
        organization,
        workspace=workspace,
        name="runnable",
        personas=[{"name": "Customer", "situation": "Asks", "outcome": "Answered"}],
        modality="voice",
    )
    create_selected_eval_configs(run_test, [selected.name], "voice")
    _get_or_create_harness_eval_config(run_test, column, "booking_created")

    runnable = runnable_eval_config_ids(run_test.id)
    assert len(runnable) == 1
    assert SimulateEvalConfig.objects.get(id=runnable[0]).eval_template_id == selected.id


@pytest.mark.django_db
def test_provision_records_the_agent_prompt_and_creates_a_version(
    organization, workspace
):
    job, _ = create_hosted_job(
        organization,
        _payload(),
        idempotency_key="prompt-key",
        workspace=workspace,
    )
    capability = register_attempt(job.id, endpoint_base_url="https://platform.example")
    response = _provision(
        APIClient(), capability, agent_prompt="You are a refunds agent. Be brief."
    )
    assert response.status_code == 200, response.content

    job.refresh_from_db()
    agent = job.run_test.agent_definition
    assert agent.description == "You are a refunds agent. Be brief."
    version = agent.latest_version
    assert version is not None
    assert version.configuration_snapshot["description"] == (
        "You are a refunds agent. Be brief."
    )


@pytest.mark.django_db
def test_provision_falls_back_to_the_authored_contract_excerpt(
    organization, workspace
):
    job, _ = create_hosted_job(
        organization,
        _payload(),
        idempotency_key="excerpt-key",
        workspace=workspace,
    )
    job.stage_outputs = [
        {
            "kind": "contract",
            "data": {
                "modality": "voice",
                "agent": "uber_voice_agent",
                "call_direction": "inbound",
                "system_prompt_excerpt": "Booked rides only.",
            },
        }
    ]
    job.save(update_fields=["stage_outputs"])
    capability = register_attempt(job.id, endpoint_base_url="https://platform.example")
    response = _provision(APIClient(), capability)
    assert response.status_code == 200, response.content

    job.refresh_from_db()
    agent = job.run_test.agent_definition
    assert agent.description == "Booked rides only."
    assert agent.agent_name == "uber_voice_agent"
    assert agent.inbound is True


@pytest.mark.django_db
def test_provision_creates_configs_for_chosen_evals(organization, workspace):
    _template("customer_agent_human_escalation", ["conversation"])
    job, _ = create_hosted_job(
        organization,
        _payload(),
        idempotency_key="chosen-key",
        workspace=workspace,
    )
    capability = register_attempt(job.id, endpoint_base_url="https://platform.example")
    response = _provision(
        APIClient(),
        capability,
        chosen_evals=["customer_agent_human_escalation"],
    )
    assert response.status_code == 200, response.content

    job.refresh_from_db()
    configs = list(SimulateEvalConfig.objects.filter(run_test=job.run_test))
    assert len(configs) == 1
    assert configs[0].mapping == {"conversation": "voice_recording"}


@pytest.mark.django_db
def test_provision_rejects_an_eval_name_it_never_offered(organization, workspace):
    job, _ = create_hosted_job(
        organization,
        _payload(),
        idempotency_key="unknown-eval-key",
        workspace=workspace,
    )
    capability = register_attempt(job.id, endpoint_base_url="https://platform.example")
    response = _provision(
        APIClient(), capability, chosen_evals=["not_a_real_eval"]
    )
    assert response.status_code == 400, response.content
    assert response.json()["error"] == "eval_selection_unknown"
