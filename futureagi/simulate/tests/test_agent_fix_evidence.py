"""Fix my agent from evidence: a scenario an evidence import added to the suite offers
Run Epoch, and a fix launched on it stores the evidence it came from (import ids and
source ids only), for its pull request to name."""

from __future__ import annotations

import pytest

from simulate.models import AgentFixRun, EvidenceImport, EvidenceImportCase
from simulate.services.hosted_harness import create_hosted_job
from simulate.tests.test_agent_fix_api import BASE, _launch_body
from simulate.tests.test_agent_fix_api import entitled as entitled
from simulate.tests.test_agent_fix_services import KEYS
from simulate.tests.test_agent_fix_services import (
    any_suite_may_launch as any_suite_may_launch,
)
from simulate.tests.test_agent_fix_services import (  # noqa: F401 - fixtures
    environment as environment,
)
from simulate.tests.test_agent_fix_services import source_run as source_run
from simulate.tests.test_agent_fix_services import temporal as temporal
from simulate.tests.test_agent_fix_services import versioning_on as versioning_on
from simulate.tests.test_harness_versioning_backfill import (  # noqa: F401 - fixture
    storage as storage,
)
from simulate.tests.test_hosted_harness_channels import _payload

pytestmark = pytest.mark.django_db


def _imported(environment, cases, *, deleted=False):
    """A transcript import of ``environment`` whose cases are (scenario key, status)."""
    made = EvidenceImport.objects.create(
        organization_id=environment.organization_id,
        workspace_id=environment.workspace_id,
        environment=environment,
        source_kind=EvidenceImport.SourceKind.TRANSCRIPT,
    )
    EvidenceImportCase.objects.bulk_create(
        [
            EvidenceImportCase(
                evidence_import=made,
                position=n,
                source_id=f"transcript-{n + 1}",
                scenario_key=key,
                status=status,
            )
            for n, (key, status) in enumerate(cases)
        ]
    )
    if deleted:
        EvidenceImport.objects.filter(id=made.id).update(deleted=True)
    return made


def _options(auth_client, source_run):
    response = auth_client.get(
        f"{BASE}/launch-options/",
        {"source_run_id": str(source_run.test_execution_id)},
    )
    assert response.status_code == 200, response.content
    return {
        one["key"]: one["evidence_import_id"] for one in response.json()["scenarios"]
    }


def _launched(auth_client, source_run, django_capture_on_commit_callbacks, **body):
    with django_capture_on_commit_callbacks(execute=True):
        response = auth_client.post(
            f"{BASE}/", _launch_body(source_run, **body), format="json"
        )
    assert response.status_code == 201, response.content
    return AgentFixRun.no_workspace_objects.get(id=response.json()["id"])


class TestImproveAgentIsOffered:
    def test_a_scenario_an_import_added_names_its_import(
        self, auth_client, environment, source_run
    ):
        made = _imported(environment, [(KEYS[0], "added")])

        assert _options(auth_client, source_run) == {
            KEYS[0]: str(made.id),
            KEYS[1]: None,
        }

    def test_an_ordinary_run_offers_it_on_no_scenario(
        self, auth_client, environment, source_run
    ):
        assert _options(auth_client, source_run) == {KEYS[0]: None, KEYS[1]: None}

    @pytest.mark.parametrize(
        "status", ["pending", "drafted", "blocked", "discarded", "unavailable"]
    )
    def test_a_case_never_added_does_not_count(
        self, auth_client, environment, source_run, status
    ):
        _imported(environment, [(KEYS[0], status)])

        assert _options(auth_client, source_run)[KEYS[0]] is None

    def test_a_discarded_import_or_another_environments_does_not_count(
        self, auth_client, environment, source_run, user, workspace
    ):
        _imported(environment, [(KEYS[0], "added")], deleted=True)
        other, _ = create_hosted_job(
            user.organization, _payload(), idempotency_key="other", workspace=workspace
        )
        _imported(other, [(KEYS[0], "added")])

        assert _options(auth_client, source_run)[KEYS[0]] is None


class TestTheFixStoresItsEvidence:
    def test_a_fix_on_an_imported_case_stores_where_it_came_from(
        self,
        auth_client,
        environment,
        source_run,
        temporal,
        django_capture_on_commit_callbacks,
    ):
        made = _imported(
            environment, [("not-in-this-run", "added"), (KEYS[0], "added")]
        )

        fix = _launched(auth_client, source_run, django_capture_on_commit_callbacks)

        assert fix.request["evidence"] == [
            {
                "import_id": str(made.id),
                "source_kind": "transcript",
                "source_ids": ["transcript-2"],
                "scenario_keys": [KEYS[0]],
            }
        ]

    def test_a_fix_of_ordinary_scenarios_stores_none(
        self,
        auth_client,
        environment,
        source_run,
        temporal,
        django_capture_on_commit_callbacks,
    ):
        _imported(environment, [(KEYS[1], "added")])

        fix = _launched(auth_client, source_run, django_capture_on_commit_callbacks)

        assert fix.request["evidence"] == []

    def test_a_request_stored_before_evidence_still_reads(self, environment):
        from simulate.pydantic_schemas.agent_fix import FixRequest

        stored = {
            "target_scenario_keys": [KEYS[0]],
            "failing_sub_goals": ["refunded"],
            "surfaces": ["prompt"],
            "budget_usd": 5.0,
            "max_minutes": 30,
        }

        assert FixRequest.model_validate(stored).evidence == []
