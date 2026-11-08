import pytest

from accounts.models import Organization
from simulate.models import HarnessEnvironmentVersion, HostedHarnessJob
from simulate.serializers.harness_environment import (
    HarnessCapabilityCasePageSerializer,
    HarnessCapabilitySelectionReportSerializer,
)
from simulate.services import harness_readiness
from simulate.services.harness_readiness import selection
from simulate.tests.test_harness_affected_scenarios import (
    LOOKUP_KEYS,
    REFUND_KEYS,
    _suite,
    _ten,
    _ten_certified,
)
from simulate.tests.test_harness_readiness import (
    BASE,
    PRODUCTION_ONLY,
    _built,
    _certify,
    _environment,
    _rows,
)
from simulate.tests.test_harness_readiness import (  # noqa: F401 - fixture
    readiness_on as readiness_on,
)
from simulate.tests.test_harness_readiness import (  # noqa: F401 - fixture
    scheduled as scheduled,
)
from simulate.tests.test_harness_readiness import (  # noqa: F401 - fixture
    storage as storage,
)

pytestmark = pytest.mark.django_db


def _get(auth_client, environment_id, path="capabilities", **params):
    return auth_client.get(f"{BASE}/{environment_id}/{path}/", params)


def _post(auth_client, environment_id, **body):
    return auth_client.post(
        f"{BASE}/{environment_id}/capabilities/", body, format="json"
    )


class TestApi:
    def test_post_selection_matches_get(self, user, workspace, storage, auth_client):
        environment = _ten_certified(user, workspace, storage, "as-api-post")
        _built(environment, contract=PRODUCTION_ONLY)
        keys = [REFUND_KEYS[0], LOOKUP_KEYS[0], LOOKUP_KEYS[1]]

        got = _get(auth_client, environment.id, keys=keys, trials=2)
        posted = _post(auth_client, environment.id, scenario_ids=keys, trials=2)

        assert got.status_code == posted.status_code == 200, posted.content
        assert got.json()["selection"] == posted.json()["selection"]
        assert got.json()["selection"]["blocked"] == 1
        assert got.json()["selection"]["suite_total"] == 10
        assert got.json()["selection"]["excluded"]["count"] == 7
        assert [row["affected"] for row in got.json()["rows"]] == [
            row["affected"] for row in posted.json()["rows"]
        ]
        contract = HarnessCapabilitySelectionReportSerializer(data=posted.json())
        assert contract.is_valid(), contract.errors

    def test_cases_page_by_number(
        self, user, workspace, storage, auth_client, monkeypatch
    ):
        environment = _environment(user, workspace, storage, "as-api-cases")
        _suite(environment, _ten(), numbers=[10, 3, 7, 1, 2, 4, 5, 6, 8, 9])
        _certify(environment, storage)
        _built(environment, contract=PRODUCTION_ONLY)
        monkeypatch.setattr(selection, "PAGE_SIZE", 3)

        first = _get(
            auth_client, environment.id, "capabilities/cases", bucket="blocked"
        )
        second = _get(
            auth_client,
            environment.id,
            "capabilities/cases",
            bucket="blocked",
            cursor=first.json()["next"],
        )

        assert first.status_code == 200, first.content
        assert [one["number"] for one in first.json()["items"]] == [1, 3, 7]
        assert first.json()["next"] == "7"
        assert [one["key"] for one in second.json()["items"]] == [REFUND_KEYS[0]]
        assert second.json()["next"] is None
        item = first.json()["items"][0]
        assert item == {
            "key": REFUND_KEYS[3],
            "number": 1,
            "name": REFUND_KEYS[3],
            "persona": f"caller of {REFUND_KEYS[3]}",
            "by": ["tool_routing"],
        }
        by_row = _get(
            auth_client, environment.id, "capabilities/cases", row="tool_routing"
        )
        assert len(by_row.json()["items"]) == 3
        contract = HarnessCapabilityCasePageSerializer(data=first.json())
        assert contract.is_valid(), contract.errors

    def test_memberless_suite_pages_in_document_order(
        self, user, workspace, storage, auth_client
    ):
        environment = _environment(user, workspace, storage, "as-api-memberless")
        _suite(environment, _ten(), members=False)
        _certify(environment, storage)

        page = _get(auth_client, environment.id, "capabilities/cases").json()

        assert [(one["number"], one["key"]) for one in page["items"]] == list(
            enumerate(REFUND_KEYS + LOOKUP_KEYS, start=1)
        )

    @pytest.mark.parametrize(
        ("scenario_ids", "code"),
        [
            ([], "scenario_selection_empty"),
            ([LOOKUP_KEYS[0], LOOKUP_KEYS[0]], "scenario_selection_duplicate"),
            ([LOOKUP_KEYS[0], "never-written"], "scenario_selection_unknown"),
        ],
    )
    def test_selection_refused_with_the_run_routes_codes(
        self, user, workspace, storage, auth_client, scenario_ids, code
    ):
        environment = _ten_certified(user, workspace, storage, f"as-api-{code}")

        posted = _post(auth_client, environment.id, scenario_ids=scenario_ids)

        assert posted.status_code == 400, posted.content
        assert posted.json()["error"] == code
        if scenario_ids:
            got = _get(auth_client, environment.id, keys=scenario_ids)
            assert got.json()["error"] == code

    def test_foreign_suite_version_is_not_pickable(
        self, user, workspace, storage, auth_client
    ):
        environment = _ten_certified(user, workspace, storage, "as-api-foreign")
        other = _environment(user, workspace, storage, "as-api-foreign-other")

        for response in (
            _post(
                auth_client,
                environment.id,
                scenario_ids=[LOOKUP_KEYS[0]],
                suite_version_id=str(other.suite_version_id),
            ),
            _get(
                auth_client,
                environment.id,
                "capabilities/cases",
                suite_version_id=other.suite_version_id,
            ),
        ):
            assert response.status_code == 422, response.content
            assert response.json()["error"] == "version_not_pickable"

    @pytest.mark.parametrize(
        ("method", "path"),
        [
            ("get", "capabilities"),
            ("post", "capabilities"),
            ("get", "capabilities/cases"),
        ],
    )
    def test_another_organizations_environment_is_not_found(
        self, user, workspace, storage, auth_client, method, path
    ):
        environment = _ten_certified(
            user, workspace, storage, f"as-api-org-{method}-{path.replace('/', '-')}"
        )
        other = Organization.objects.create(name="Other org for capabilities")
        HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
            organization=other
        )

        response = (
            _post(auth_client, environment.id, scenario_ids=[LOOKUP_KEYS[0]])
            if method == "post"
            else _get(auth_client, environment.id, path)
        )

        assert response.status_code == 404, response.content


def _redirected(environment):
    """E2: the environment version after the refund tool is pointed at the test world."""
    version = environment.environment_version
    documents = dict(version.documents)
    documents["contract"] = {}
    return HarnessEnvironmentVersion.no_workspace_objects.create(
        job=environment,
        organization_id=version.organization_id,
        workspace_id=version.workspace_id,
        version_number=2,
        status="active",
        origin=version.origin,
        hash="sha256:" + "e" * 64,
        hash_kind=version.hash_kind,
        archive_object_key=version.archive_object_key,
        archive_layout=version.archive_layout,
        archive_sha256=version.archive_sha256,
        archive_size=version.archive_size,
        parent=version,
        documents=documents,
        agent_version_id=version.agent_version_id,
    )


class TestDoneWhen:
    def test_refund_tool_to_production_blocks_four_then_none_after_redirect(
        self, user, workspace, storage, auth_client
    ):
        """An agent whose refund tool reaches production: tool routing is Blocked with
        4 of 10 cases affected, the 4 named with the fix; after the tool is redirected
        and the pair certified, none are, and no run starts."""
        environment = _ten_certified(user, workspace, storage, "as-done-when")
        _built(environment, contract=PRODUCTION_ONLY)
        jobs = HostedHarnessJob.no_workspace_objects.count()

        before = _get(auth_client, environment.id).json()

        routing = _rows(before)["tool_routing"]
        assert routing["status"] == "blocked"
        assert routing["affected"] == {
            "count": 4,
            "of": 10,
            "keys": REFUND_KEYS,
            "truncated": False,
        }
        assert routing["fix"]["action"] == "Point issue_refund at the test world"
        assert before["selection"]["blocked"] == 4

        e2 = _redirected(environment)
        _certify(
            environment,
            storage,
            pairing=harness_readiness.resolve_pairing(
                environment, environment_version_id=e2.id
            ),
        )
        after = _get(auth_client, environment.id, environment_version_id=e2.id).json()

        routing = _rows(after)["tool_routing"]
        assert routing["status"] == "supported"
        assert routing["affected"] == {
            "count": 0,
            "of": 10,
            "keys": [],
            "truncated": False,
        }
        assert (after["selection"]["blocked"], after["selection"]["eligible"]) == (
            0,
            10,
        )
        assert HostedHarnessJob.no_workspace_objects.count() == jobs


class TestRunHoldBackApi:
    def test_the_run_answer_and_the_run_read_name_the_held_back_cases(
        self, user, workspace, storage, auth_client, monkeypatch
    ):
        from simulate.serializers.harness_job import HarnessRunCreateResponseSerializer

        monkeypatch.setattr(
            "simulate.temporal.client.start_hosted_harness_gateway_workflow",
            lambda *args, **kwargs: None,
        )
        monkeypatch.setattr(
            "simulate.services.harness_usage.require_harness_call_usage",
            lambda *args, **kwargs: None,
        )
        environment = _ten_certified(user, workspace, storage, "hb-api")
        _built(environment, contract=PRODUCTION_ONLY)

        response = auth_client.post(
            f"{BASE}/{environment.id}/run/",
            {"scenario_ids": REFUND_KEYS + LOOKUP_KEYS, "trials": 1},
            format="json",
            HTTP_IDEMPOTENCY_KEY="hb-api-run",
        )

        assert response.status_code == 202, response.content
        body = response.json()
        HarnessRunCreateResponseSerializer(data=body).is_valid(raise_exception=True)
        assert body["selection"]["held_back"]["keys"] == REFUND_KEYS
        assert (body["selection"]["running"], body["total_calls"]) == (6, 6)
        assert (
            body["selection"]["suite_total"],
            body["selection"]["excluded"]["count"],
        ) == (10, 0)
        header = auth_client.get(
            f"/simulate/v3/test-executions/{body['test_execution_id']}/calls/"
        ).json()["execution"]
        assert header["readiness_selection"] == body["selection"]

    def test_every_case_blocked_answers_409_with_the_reasons(
        self, user, workspace, storage, auth_client, monkeypatch
    ):
        monkeypatch.setattr(
            "simulate.services.harness_usage.require_harness_call_usage",
            lambda *args, **kwargs: None,
        )
        environment = _ten_certified(user, workspace, storage, "hb-api-all")
        _built(environment, contract=PRODUCTION_ONLY)

        response = auth_client.post(
            f"{BASE}/{environment.id}/run/",
            {"scenario_ids": REFUND_KEYS, "trials": 1},
            format="json",
            HTTP_IDEMPOTENCY_KEY="hb-api-all",
        )

        assert response.status_code == 409, response.content
        body = response.json()
        assert body["error"] == "all_cases_blocked"
        assert body["selection"]["held_back"]["by"] == {"tool_routing": 4}
