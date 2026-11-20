"""A case the builder adds is validated with the unsaved changes, saved with its proof, and runs."""

from unittest.mock import patch

import pytest

from simulate.models import HarnessSuiteValidation, HostedHarnessJob
from simulate.services.harness_scenario_lifecycle import scenario_lifecycle
from simulate.services.harness_versioning import drafts, versions
from simulate.services.harness_versioning.versions import record_proofs
from simulate.tests.test_harness_actors import CRM_CONTRACT, NAMES
from simulate.tests.test_harness_affected_scenarios import PROVED
from simulate.tests.test_harness_candidates import (
    _certify,
    _drafts,
    _result,
    _save,
    _validate,
)
from simulate.tests.test_harness_review_api import _review, _with_world
from simulate.tests.test_harness_versioning_backfill import (  # noqa: F401 - fixture
    storage as storage,
)
from simulate.tests.test_harness_versioning_drafts import (
    EXTRA,
    EXTRA_KEY,
    _finish,
    _publish,
)

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def versioning_on(settings):
    settings.HARNESS_VERSIONING_ENABLED = True
    settings.HARNESS_BUNDLE_CERTIFY_ENABLED = True


def _selected(environment, keys):
    from simulate.services.harness_readiness import Pairing, select_for_run
    from simulate.services.hosted_harness import resolve_run_pins

    resolved = resolve_run_pins(environment)
    pairing = Pairing(
        environment=environment,
        agent_version=resolved.agent_version,
        environment_version=resolved.environment_version,
        suite_version=resolved.suite_version,
        runtime=resolved.runtime,
    )
    return select_for_run(pairing, keys=keys, trials=1)


def test_a_builder_added_case_validated_with_the_drafts_is_saved_active_and_runs(
    user, workspace, storage, auth_client
):
    environment = _with_world(user, workspace, "journey-draft-case", storage)
    _publish(
        environment,
        storage,
        names=(NAMES[0], EXTRA),
        sequence=10,
        user=user,
        contract=CRM_CONTRACT,
    )
    _finish(environment)
    assert drafts.open_draft(environment, "suite") is not None

    validated = _validate(
        auth_client, environment, _review(auth_client, environment)["review_digest"]
    )
    assert validated.status_code == 202, validated.content
    body = _result(validated)
    job = HostedHarnessJob.no_workspace_objects.get(id=body["validation"]["job_id"])
    # The gateway records what the validation proved before the sandbox goes, under the scenario folders' names.
    record_proofs(
        job.suite_version,
        job.environment_version,
        {EXTRA: PROVED, NAMES[0]: PROVED},
        job,
    )
    _certify(environment)

    assert _drafts(auth_client, environment)["validation"]["state"] == "ready"
    candidate = scenario_lifecycle(
        environment,
        suite_version=job.suite_version,
        environment_version=job.environment_version,
    )
    assert candidate[EXTRA_KEY]["state"] == "validated"

    with patch.object(
        versions, "_store_file_sources", wraps=versions._store_file_sources
    ) as kept:
        saved = _save(auth_client, environment, body["candidates"])
    assert saved.status_code == 200, saved.content
    # Saved with its identity row, the proof also keeps what it read of a cited file on that row.
    assert any(
        key == EXTRA_KEY and row is not None
        for call in kept.call_args_list
        for key, row, _ in call.args[2]
    )

    environment.refresh_from_db()
    default = scenario_lifecycle(
        environment,
        suite_version=environment.suite_version,
        environment_version=environment.environment_version,
    )
    assert default[EXTRA_KEY]["state"] == "active"
    assert HarnessSuiteValidation.no_workspace_objects.get(
        suite_version=environment.suite_version,
        environment_version=environment.environment_version,
    ).proofs[EXTRA_KEY]["proved"]
    plan = _selected(environment, [EXTRA_KEY])
    assert EXTRA_KEY not in plan.held
