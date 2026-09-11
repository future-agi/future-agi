from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

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
    _template("no_misselling", ["conversation"], eval_id=202)
    _template("some_unrelated_eval", ["conversation"], eval_id=12)

    offered = {item["name"] for item in offered_evals(organization, workspace, "voice")}
    assert "customer_agent_loop_detection" in offered
    assert "no_misselling" in offered
    assert "some_unrelated_eval" not in offered


@pytest.mark.django_db
def test_dead_air_is_never_offered(organization, workspace):
    """Withheld on both modalities, not just filtered out of text."""
    _template("dead_air_detection", ["input_audio"], eval_id=201)

    for modality in ("voice", "text"):
        offered = {item["name"] for item in offered_evals(organization, workspace, modality)}
        assert "dead_air_detection" not in offered


@pytest.mark.django_db
def test_a_chat_run_is_not_offered_the_voice_only_evals(organization, workspace):
    _template("dead_air_detection", ["conversation"], eval_id=201)
    _template("voicemail_handling", ["conversation"], eval_id=207)
    _template("customer_agent_query_handling", ["conversation"])

    offered = {item["name"] for item in offered_evals(organization, workspace, "text")}
    assert offered == {"customer_agent_query_handling"}


@pytest.mark.django_db
def test_the_manifest_decides_three_ways(organization, workspace):
    """Absent, visible true and visible false are three different answers."""
    from simulate.services.harness_evals import harness_evals_manifest

    body = json.loads(harness_evals_manifest().read_text(encoding="utf-8"))
    by_name = {entry["name"]: entry for entry in body["evals"]}

    assert by_name["customer_agent_query_handling"]["visible"] is True
    assert by_name["dead_air_detection"]["visible"] is False
    assert by_name["dead_air_detection"]["why"]
    assert "customer_agent_single_choice" not in by_name

    _template("customer_agent_query_handling", ["conversation"])
    _template("dead_air_detection", ["conversation"])
    _template("customer_agent_single_choice", ["conversation"])

    offered = {item["name"] for item in offered_evals(organization, workspace, "voice")}
    assert offered == {"customer_agent_query_handling"}


@pytest.mark.django_db
def test_flipping_a_withheld_eval_to_visible_offers_it(organization, workspace):
    """Pins that withholding is what excludes it, not something else in the path."""
    _template("dead_air_detection", ["conversation"])

    assert not offered_evals(organization, workspace, "voice")

    with patch(
        "simulate.services.harness_evals.offerable_eval_names",
        return_value=frozenset({"dead_air_detection"}),
    ):
        offered = {
            item["name"] for item in offered_evals(organization, workspace, "voice")
        }
    assert offered == {"dead_air_detection"}


@pytest.mark.django_db
def test_a_row_absent_from_the_manifest_is_never_offered(organization, workspace):
    """A name with no manifest entry is never offered, whatever the database holds."""
    fixtures = [
        "customer_agent_single_choice",
        "customer_agent_multi_choices",
        "customer_agent_score_with_choices",
    ]
    for name in fixtures:
        _template(name, ["conversation"])
    _template("customer_agent_query_handling", ["conversation"])

    offered = {item["name"] for item in offered_evals(organization, workspace, "voice")}
    assert offered == {"customer_agent_query_handling"}


@pytest.mark.django_db
def test_a_row_absent_from_the_manifest_cannot_be_selected_either(organization, workspace):
    """The catalogue is not the only way in: a name the guest sends is checked against the manifest."""
    from simulate.services.alk_simulate_ingestion import provision_alk_sim_run_test

    _template("customer_agent_single_choice", ["conversation"])
    run_test, _scenarios, _agent = provision_alk_sim_run_test(
        organization,
        workspace=workspace,
        name="undefined-pick",
        personas=[{"name": "Customer", "situation": "Asks", "outcome": "Answered"}],
        modality="voice",
    )

    with pytest.raises(UnknownEvalSelection):
        create_selected_eval_configs(run_test, ["customer_agent_single_choice"], "voice")


@pytest.mark.django_db
def test_another_tenants_template_is_never_offered(organization, workspace, django_user_model):
    from accounts.models import Organization

    other = Organization.objects.create(name="other-tenant")
    _template("customer_agent_private", ["conversation"], organization=other)
    _template("customer_agent_shared", ["conversation"])

    # Synthetic names, so the manifest is stubbed: this test is about tenant scope.
    with patch(
        "simulate.services.harness_evals.offerable_eval_names",
        return_value=frozenset({"customer_agent_private", "customer_agent_shared"}),
    ):
        offered = {
            item["name"] for item in offered_evals(organization, workspace, "voice")
        }
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
    # Synthetic names again; this test is about the cap, not the manifest.
    gate = patch(
        "simulate.services.harness_evals.offerable_eval_names",
        return_value=frozenset(names),
    )
    with gate:
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
    # Base derives human-readable names, so the snake_case value arrives title-cased.
    assert agent.agent_name == "Uber Voice Agent"
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


def test_a_fresh_run_is_offered_both_modalities(db, django_assert_num_queries=None):
    """At launch there is no contract, so filtering on a guess is what broke a voice run.

    The catalogue was filtered to the modality read off `stage_outputs`, which is empty on a fresh
    run because authoring happens inside the sandbox afterwards. It defaulted to text, so a voice
    run was offered only text entries and the guest refused every one of them as cross-modality,
    leaving `chosen_evals` empty. Measured on run 57baa0fe: 20 entries, all marked text, on a voice
    run.
    """
    from simulate.services.hosted_harness_gateway import _authored_modality

    class _Job:
        stage_outputs: list = []

    assert _authored_modality(_Job()) == "", "absence must not read as text"

    authored = _Job()
    authored.stage_outputs = [{"kind": "contract", "data": {"modality": "voice"}}]
    assert _authored_modality(authored) == "voice"


def test_a_fresh_run_offers_each_name_once(db):
    """Two entries for one name made the guest refuse the model's correct choices.

    Measured on run a5ffa58d, whose guest log reads "chosen_evals names evals belonging to another
    modality than 'voice'": the catalogue offered every name twice, once per modality, the guest
    kept whichever it read last, and a correct voice choice was rejected. A name both sets offer is
    marked `any`, which the guest accepts for either modality; a name only one set offers keeps it.
    """
    from collections import Counter

    from simulate.services.hosted_harness_gateway import _offered_eval_catalogue

    class _Job:
        stage_outputs: list = []
        organization = None
        workspace = None
        id = "00000000-0000-0000-0000-000000000000"

    offered = _offered_eval_catalogue(_Job())
    names = Counter(str(entry.get("name")) for entry in offered)
    assert not [name for name, count in names.items() if count > 1], "a name must appear once"
    voice_only = {"dead_air_detection", "voice_mail_detection", "voicemail_handling"}
    for entry in offered:
        if str(entry.get("name")) in voice_only:
            assert entry.get("modality") == "voice"
        else:
            assert entry.get("modality") in ("any", "voice", "text")


@pytest.mark.django_db
def test_a_template_with_no_model_still_gets_one(organization, workspace):
    """Agent-type templates name no model, and the evaluator's default cannot consume audio, so a bound
    recording reached the judge as a link and every harness-created eval scored 0.0. The same eval added
    by hand, with a model, read the call and scored it."""
    from simulate.services.alk_simulate_ingestion import provision_alk_sim_run_test
    from simulate.services.harness_evals import FALLBACK_EVAL_MODEL

    _template(
        "customer_agent_objection_handling",
        ["conversation"],
        organization=organization,
        workspace=workspace,
    )
    run_test, _scenarios, _agent = provision_alk_sim_run_test(
        organization,
        workspace=workspace,
        name="fallback-model",
        personas=[{"name": "Customer", "situation": "Objects", "outcome": "Handled"}],
        modality="voice",
    )
    configs = create_selected_eval_configs(
        run_test, ["customer_agent_objection_handling"], "voice"
    )
    assert configs, "the template is mappable, so it must produce a config"
    assert configs[0].model == FALLBACK_EVAL_MODEL


def test_every_manifest_name_is_a_real_eval_definition():
    """A typo drops an eval silently: a name matching no template just never appears."""
    from simulate.services.harness_evals import harness_evals_manifest

    root = Path(__file__).resolve().parents[2] / "model_hub" / "system_evals"
    defined = {path.stem for path in root.rglob("*.yaml")}
    manifest = json.loads(harness_evals_manifest().read_text(encoding="utf-8"))
    entries = manifest["evals"] if isinstance(manifest, dict) else manifest
    named = {str(entry["name"]) for entry in entries}

    missing = sorted(named - defined)
    assert not missing, f"manifest names with no YAML definition: {missing}"
