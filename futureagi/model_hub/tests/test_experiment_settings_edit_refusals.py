"""An edit refuses a run setting only for a reason a person can act on.

While a run is going, an edit that would change a setting waits for it, and
one that sends the settings as they are stored goes through: it changes
nothing. Per-row limits an experiment cannot take are refused in create's
words and in create's shape, under details.limits."""

# Imported fixtures are injected by pytest.
# ruff: noqa: F811

from copy import deepcopy
from unittest.mock import patch

import pytest

from model_hub.models.choices import StatusType
from model_hub.models.experiments import ExperimentsTable
from model_hub.tests.test_experiment_run_scope import (
    CREATE_URL,
    request_body,
    scope_world,  # noqa: F401
)
from model_hub.tests.test_experiment_settings_edit import START, created, edit
from model_hub.tests.test_experiment_version_params import (
    version_world,  # noqa: F401
)
from model_hub.tests.test_http_experiment_source import tenants  # noqa: F401
from model_hub.tests.test_http_mutations import finished  # noqa: F401
from model_hub.tests.test_http_mutations import put as http_put
from tfc.utils.api_errors import build_error_envelope

pytestmark = pytest.mark.django_db(transaction=True)
WAIT = "Wait for the current execution to finish before changing its run settings."
NOT_LLM = "Per-row limits require an LLM experiment."
HTTP_LIMITS = "HTTP sources use timeout_seconds; model token limits do not apply."
# The fields of create's refusal a client reads.
REFUSAL = ("message", "details", "attr", "code", "type")
STORED = {
    "metadata": {"purpose": "kept"},
    "error_policy": "exclude",
    "limits": {"timeout_s": 30},
}


def going(experiment):
    """A classic run of the experiment is going: it reads Running, while no
    native run of it is unfinished (the edits this path always allowed)."""
    ExperimentsTable.no_workspace_objects.filter(pk=experiment.pk).update(
        status=StatusType.RUNNING.value
    )
    experiment.refresh_from_db()
    return experiment


def settings_of(experiment):
    experiment.refresh_from_db()
    return deepcopy(experiment.run_metadata), deepcopy(experiment.run_policy)


@pytest.mark.parametrize(
    ("stored", "sent"),
    [
        pytest.param(STORED, STORED, id="every-setting-as-stored"),
        pytest.param(STORED, {"error_policy": "exclude"}, id="policy-as-stored"),
        pytest.param(
            STORED,
            {"metadata": {"purpose": "kept"}, "limits": {"timeout_s": 30}},
            id="metadata-and-limits-as-stored",
        ),
        pytest.param(
            {},
            {"metadata": {}, "error_policy": None, "limits": {}},
            id="defaults-as-stored",
        ),
    ],
)
def test_settings_sent_as_stored_do_not_wait_for_the_current_run(
    auth_client, scope_world, stored, sent
):
    record = created(auth_client, scope_world, **stored)
    experiment = going(record.experiment_dataset.experiment)
    before = settings_of(experiment)
    response, start = edit(auth_client, experiment, sent)
    assert response.status_code == 200, response.content
    assert WAIT not in response.content.decode()
    start.assert_not_called()
    assert settings_of(experiment) == before


@pytest.mark.parametrize(
    "sent",
    [
        pytest.param({**STORED, "error_policy": "zero"}, id="policy-differs"),
        pytest.param({**STORED, "limits": {}}, id="limits-cleared"),
        pytest.param({"metadata": {"purpose": "changed"}}, id="metadata-differs"),
        pytest.param({"error_policy": None}, id="policy-cleared"),
    ],
)
def test_a_setting_that_would_change_waits_for_the_current_run(
    auth_client, scope_world, sent
):
    record = created(auth_client, scope_world, **STORED)
    experiment = going(record.experiment_dataset.experiment)
    before = settings_of(experiment)
    response, start = edit(auth_client, experiment, sent)
    assert response.status_code == 400, response.content
    assert WAIT in response.content.decode()
    start.assert_not_called()
    assert settings_of(experiment) == before


def test_limits_on_an_experiment_that_is_not_llm_are_refused_as_create_refuses_them(
    auth_client, scope_world
):
    sent = {"limits": {"timeout_s": 30}}
    with patch(START) as start:
        refused = auth_client.post(
            CREATE_URL,
            request_body(scope_world, experiment_type="tts", **sent),
            format="json",
        )
    assert refused.status_code == 400, refused.content
    start.assert_not_called()
    expected = refused.json()
    assert expected["details"] == {"limits": [NOT_LLM]}
    record = created(auth_client, scope_world)
    experiment = record.experiment_dataset.experiment
    ExperimentsTable.no_workspace_objects.filter(pk=experiment.pk).update(
        experiment_type="tts"
    )
    experiment.refresh_from_db()
    before = settings_of(experiment)
    response, start = edit(auth_client, experiment, sent)
    assert response.status_code == 400, response.content
    body = response.json()
    for field in REFUSAL:
        assert body.get(field) == expected.get(field), field
    start.assert_not_called()
    assert settings_of(experiment) == before


@pytest.mark.django_db
def test_an_endpoint_experiment_refuses_limits_as_create_refuses_them(finished):
    actor, experiment, _source, _plan = finished
    before = settings_of(experiment)
    response = http_put(actor, experiment, {"limits": {"timeout_s": 30}})
    assert response.status_code == 400, response.content
    body = response.json()
    # Create's serializer refuses these limits as {"limits": [sentence]}.
    expected = build_error_envelope({"limits": [HTTP_LIMITS]})
    assert body["details"] == {"limits": [HTTP_LIMITS]}
    for field in REFUSAL:
        assert body.get(field) == expected.get(field), field
    assert settings_of(experiment) == before
