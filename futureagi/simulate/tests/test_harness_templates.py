from __future__ import annotations

from unittest.mock import patch

import pytest
from django.test import override_settings
from rest_framework.test import APIClient

from simulate.harness_templates import (
    get_template,
    iter_template_files,
    list_templates,
    load_seed_scenarios,
)

EXPECTED_SLUGS = {
    "debt_collection",
    "insurance_fnol",
    "healthcare_scheduling",
    "banking_support",
}

TEMPLATES_URL = "/simulate/api/harness-jobs/templates/"


def _instantiate_url(slug: str) -> str:
    return f"/simulate/api/harness-jobs/templates/{slug}/instantiate/"


def test_catalog_lists_the_four_seed_agents():
    templates = list_templates()
    assert {t["slug"] for t in templates} == EXPECTED_SLUGS
    # Curated order is stable and deterministic.
    assert [t["slug"] for t in templates][0] == "debt_collection"
    for template in templates:
        assert template["display_name"]
        assert template["channel"] in {"voice", "chat"}
        assert template["direction"] in {"inbound", "outbound"}
        assert template["default_scenario_count"] >= 1
        assert template["phase_count"] >= 1
        assert template["tool_count"] >= 1
        assert template["vertical"]
        assert template["icon"]


def test_get_template_unknown_returns_none():
    assert get_template("does-not-exist") is None


def test_every_template_ships_ingestion_ready_seed_scenarios():
    # TH-7808: each seeded agent carries 10–20 persona-scenarios in its own
    # source, in the shape ``provision_alk_sim_run_test(personas=...)`` ingests.
    for slug in EXPECTED_SLUGS:
        scenarios = load_seed_scenarios(slug)
        assert 10 <= len(scenarios) <= 20, f"{slug}: {len(scenarios)} seeded"
        names = [entry.get("scenario_name") for entry in scenarios]
        assert all(names), f"{slug}: a scenario is missing scenario_name"
        # A duplicate name hides a coverage gap while looking full.
        assert len(names) == len(set(names)), f"{slug}: duplicate scenario names"
        for entry in scenarios:
            label = f"{slug}/{entry.get('scenario_name')}"
            # Fields provision_alk_sim_run_test reads off each persona dict.
            assert entry.get("situation"), label
            assert entry.get("outcome"), label
            persona = entry.get("persona")
            assert isinstance(persona, dict), label
            assert persona.get("name"), label
            assert persona.get("communication_style"), label


def test_summary_reports_seeded_scenario_count():
    for template in list_templates():
        assert template["seeded_scenario_count"] == len(
            load_seed_scenarios(template["slug"])
        )


def test_load_seed_scenarios_unknown_is_empty():
    assert load_seed_scenarios("does-not-exist") == []


def test_iter_template_files_bundles_runtime_scaffold():
    contents = dict(iter_template_files("debt_collection"))
    assert {
        "agent.py",
        "config.json",
        "README.md",
        "requirements.txt",
        "Dockerfile",
        "docker-compose.yml",
    } <= set(contents)
    # The agent is the vendored LiveKit worker, and its deps are LiveKit's.
    assert contents["agent.py"].startswith(b'"""')
    assert b"livekit" in contents["requirements.txt"]
    # The runtime scaffold we added must be present so the harness can build it.
    assert b"python agent.py" in contents["Dockerfile"]


def test_iter_template_files_unknown_raises():
    with pytest.raises(KeyError):
        list(iter_template_files("does-not-exist"))


@pytest.mark.django_db
def test_templates_endpoint_returns_catalog(user):
    client = APIClient()
    client.force_authenticate(user=user)
    response = client.get(TEMPLATES_URL)
    assert response.status_code == 200
    body = response.json()
    assert {t["slug"] for t in body} == EXPECTED_SLUGS


@pytest.mark.django_db
@override_settings(HARNESS_PROVIDER="daytona")
def test_instantiate_template_packs_source_for_daytona(user):
    client = APIClient()
    client.force_authenticate(user=user)
    stored = {
        "source_id": "63ef3598-a84d-4ce0-a7a1-53c4e27f69f7",
        "name": "Collections — Payment Reminder",
        "file_count": 6,
        "total_bytes": 4096,
    }
    with patch(
        "simulate.services.hosted_harness_gateway.store_source_archive",
        return_value=stored,
    ) as store:
        response = client.post(_instantiate_url("debt_collection"), format="json")

    assert response.status_code == 201
    body = response.json()
    assert body["source_id"] == stored["source_id"]
    assert (
        body["scenario_count"]
        == get_template("debt_collection")["default_scenario_count"]
    )
    assert body["template"]["slug"] == "debt_collection"
    assert body["config"] == {}
    # The whole vendored folder (including the runtime scaffold we authored) is
    # handed to the packer as one source archive.
    _, files_arg, paths_arg, _name = store.call_args.args
    assert "agent.py" in paths_arg
    assert "Dockerfile" in paths_arg
    assert len(files_arg) == len(paths_arg)


@pytest.mark.django_db
@override_settings(HARNESS_PROVIDER="daytona")
def test_instantiate_unknown_template_returns_404(user):
    client = APIClient()
    client.force_authenticate(user=user)
    response = client.post(_instantiate_url("does-not-exist"), format="json")
    assert response.status_code == 404
