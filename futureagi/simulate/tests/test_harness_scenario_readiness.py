from datetime import timedelta
from unittest.mock import patch

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from simulate.models import (
    HarnessAssembledBundle,
    HarnessEnvironmentVersion,
    HarnessSuiteMember,
    HarnessSuiteValidation,
    HarnessSuiteVersion,
)
from simulate.services import harness_readiness
from simulate.services.hosted_harness_gateway import _scenario_token
from simulate.tests.test_harness_affected_scenarios import (
    LOOKUP_KEYS,
    REFUND_KEYS,
    _suite,
    _ten,
    _ten_certified,
)
from simulate.tests.test_harness_readiness import (
    _built,
    _environment,
    _pairing,
    _with_a2,
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
from simulate.tests.test_harness_versioning_assembly import _row

pytestmark = pytest.mark.django_db

SCENARIOS = "/simulate/api/harness-jobs"


def _uncertified(user, workspace, storage, key):
    environment = _environment(user, workspace, storage, key)
    _suite(environment, _ten())
    HarnessAssembledBundle._base_manager.filter(id=_row(environment).id).update(
        state="pending", object_key=None
    )
    _built(environment)
    return environment


def _proofs(environment, holds, *, environment_version=None):
    HarnessSuiteValidation.no_workspace_objects.update_or_create(
        suite_version=environment.suite_version,
        environment_version=environment_version or environment.environment_version,
        defaults={"status": "partial", "proved_at": timezone.now(), "proofs": holds},
    )


def _flags(environment):
    return harness_readiness.scenario_readiness(_pairing(environment))


def _environment_version_copy(environment, n):
    current = environment.environment_version
    copy = HarnessEnvironmentVersion.no_workspace_objects.get(id=current.id)
    copy.pk = None
    copy.version_number = (current.version_number or 1) + 100 + n
    copy.hash = "sha256:" + str(n % 10) * 64
    copy.save()
    return copy


def _validated(environment, proofs, *, environment_version, proved_at, suite=None):
    HarnessSuiteValidation.no_workspace_objects.create(
        suite_version=suite or environment.suite_version,
        environment_version=environment_version,
        status="partial",
        proved_at=proved_at,
        proofs=proofs,
    )


def _tokenised(read):
    return {call.args[0] for call in read.call_args_list if call.args}


class TestProofState:
    def test_proved_failed_and_never_proved_read_apart(self, user, workspace, storage):
        environment = _uncertified(user, workspace, storage, "sr-proof")
        held = {key: {"holds": True} for key in REFUND_KEYS + LOOKUP_KEYS[1:]}
        held[LOOKUP_KEYS[1]] = {"holds": False}
        del held[LOOKUP_KEYS[2]]
        _proofs(environment, held)

        flags = _flags(environment)

        assert flags[REFUND_KEYS[0]]["proof"] == {
            "state": "proved",
            "last_proved": None,
        }
        assert flags[LOOKUP_KEYS[1]]["proof"]["state"] == "failed"
        assert flags[LOOKUP_KEYS[2]]["proof"]["state"] == "not_proved"
        assert flags[LOOKUP_KEYS[1]]["bucket"] == "stale"
        assert "not_proved_here" in flags[LOOKUP_KEYS[1]]["by"]

    def test_a_certified_pairing_reads_every_case_proved(
        self, user, workspace, storage
    ):
        environment = _ten_certified(user, workspace, storage, "sr-certified")
        HarnessSuiteValidation.no_workspace_objects.filter(
            suite_version=environment.suite_version
        ).delete()

        flags = _flags(environment)

        assert {one["proof"]["state"] for one in flags.values()} == {"proved"}
        assert {one["bucket"] for one in flags.values()} == {"eligible"}

    def test_a_pairing_certified_with_another_agent_proves_the_same_versions(
        self, user, workspace, storage
    ):
        environment, a2 = _with_a2(user, workspace, storage, "sr-other-agent")
        assert harness_readiness.readiness_report(_pairing(environment))["validation"][
            "state"
        ] in ("ready", "certified")

        flags = harness_readiness.scenario_readiness(_pairing(environment, a2))

        assert flags
        assert {one["proof"]["state"] for one in flags.values()} == {"proved"}

    def test_a_proof_from_an_earlier_environment_version_names_it(
        self, user, workspace, storage
    ):
        environment = _uncertified(user, workspace, storage, "sr-earlier")
        current = environment.environment_version
        earlier = HarnessEnvironmentVersion.no_workspace_objects.get(id=current.id)
        earlier.pk = None
        earlier.version_number = (current.version_number or 1) + 100
        earlier.hash = "sha256:" + "e" * 64
        earlier.save()
        _proofs(
            environment, {LOOKUP_KEYS[0]: {"holds": True}}, environment_version=earlier
        )
        _proofs(environment, {LOOKUP_KEYS[0]: {"holds": False}})

        proof = _flags(environment)[LOOKUP_KEYS[0]]["proof"]

        assert proof["state"] == "failed"
        assert proof["last_proved"] == {
            "environment_version": earlier.version_number,
            "suite_version": environment.suite_version.version_number,
        }

    def test_a_certified_earlier_pairing_counts_as_the_last_proof(
        self, user, workspace, storage
    ):
        environment = _ten_certified(user, workspace, storage, "sr-cert-earlier")
        certified_suite = environment.suite_version
        edited = HarnessSuiteVersion.no_workspace_objects.get(id=certified_suite.id)
        edited.pk = None
        edited.version_number = (certified_suite.version_number or 1) + 1
        edited.hash = edited.certify_hash = "sha256:" + "f" * 64
        edited.save()
        for member in HarnessSuiteMember.no_workspace_objects.filter(
            suite_version=certified_suite
        ):
            member.pk = None
            member.suite_version = edited
            member.save()
        environment.suite_version = edited
        environment.save(update_fields=["suite_version"])

        proof = _flags(environment)[LOOKUP_KEYS[0]]["proof"]

        assert proof == {
            "state": "not_proved",
            "last_proved": {
                "environment_version": environment.environment_version.version_number,
                "suite_version": certified_suite.version_number,
            },
        }

    def test_a_case_never_proved_anywhere_has_no_last_proof(
        self, user, workspace, storage
    ):
        environment = _uncertified(user, workspace, storage, "sr-never")

        proof = _flags(environment)[LOOKUP_KEYS[0]]["proof"]

        assert proof == {"state": "not_proved", "last_proved": None}

    def test_a_case_the_certificate_dropped_is_not_proved_by_it(
        self, user, workspace, storage
    ):
        environment = _ten_certified(user, workspace, storage, "sr-cert-dropped")
        HarnessSuiteValidation.no_workspace_objects.filter(
            suite_version=environment.suite_version
        ).delete()
        HarnessAssembledBundle._base_manager.filter(id=_row(environment).id).update(
            warnings=[{"code": "scenario_dropped", "keys": [LOOKUP_KEYS[0]]}]
        )

        flags = _flags(environment)

        assert flags[LOOKUP_KEYS[0]]["dropped"] == "scenario_dropped"
        assert flags[LOOKUP_KEYS[0]]["proof"] == {
            "state": "not_proved",
            "last_proved": None,
        }
        assert {
            one["proof"]["state"] for key, one in flags.items() if key != LOOKUP_KEYS[0]
        } == {"proved"}

    def test_an_earlier_certificate_does_not_prove_a_case_it_dropped(
        self, user, workspace, storage
    ):
        environment = _ten_certified(user, workspace, storage, "sr-earlier-dropped")
        certified_suite = environment.suite_version
        HarnessSuiteValidation.no_workspace_objects.filter(
            suite_version=certified_suite
        ).delete()
        HarnessAssembledBundle._base_manager.filter(id=_row(environment).id).update(
            warnings=[{"code": "scenario_dropped", "keys": [LOOKUP_KEYS[0]]}]
        )
        edited = HarnessSuiteVersion.no_workspace_objects.get(id=certified_suite.id)
        edited.pk = None
        edited.version_number = (certified_suite.version_number or 1) + 1
        edited.hash = edited.certify_hash = "sha256:" + "f" * 64
        edited.save()
        for member in HarnessSuiteMember.no_workspace_objects.filter(
            suite_version=certified_suite
        ):
            member.pk = None
            member.suite_version = edited
            member.save()
        environment.suite_version = edited
        environment.save(update_fields=["suite_version"])

        flags = _flags(environment)

        assert flags[LOOKUP_KEYS[0]]["proof"]["last_proved"] is None
        assert flags[LOOKUP_KEYS[1]]["proof"]["last_proved"] == {
            "environment_version": environment.environment_version.version_number,
            "suite_version": certified_suite.version_number,
        }

    def test_readiness_for_some_keys_answers_and_reads_only_those(
        self, user, workspace, storage
    ):
        environment = _uncertified(user, workspace, storage, "sr-some-keys")
        earlier = _environment_version_copy(environment, 1)
        _proofs(
            environment, {LOOKUP_KEYS[0]: {"holds": True}}, environment_version=earlier
        )
        pairing = _pairing(environment)
        everything = harness_readiness.scenario_readiness(pairing)

        with CaptureQueriesContext(connection) as seen:
            some = harness_readiness.scenario_readiness(
                pairing, keys=[LOOKUP_KEYS[0], "not-in-suite"]
            )
        with CaptureQueriesContext(connection) as none_seen:
            nothing = harness_readiness.scenario_readiness(pairing, keys=[])

        assert some == {LOOKUP_KEYS[0]: everything[LOOKUP_KEYS[0]]}
        assert some[LOOKUP_KEYS[0]]["proof"]["last_proved"] == {
            "environment_version": earlier.version_number,
            "suite_version": environment.suite_version.version_number,
        }
        assert any(
            "simulate_harness_suite_member" in query["sql"]
            and '"scenario_key" IN' in query["sql"]
            for query in seen.captured_queries
        )
        assert nothing == {}
        assert not none_seen.captured_queries

    def test_the_proof_history_stops_at_the_newest_holder(
        self, user, workspace, storage
    ):
        environment = _uncertified(user, workspace, storage, "sr-history-stop")
        now = timezone.now()
        versions = []
        for n, extra in ((1, {"only-in-oldest": {"holds": True}}), (2, {}), (3, {})):
            version = _environment_version_copy(environment, n)
            _validated(
                environment,
                {LOOKUP_KEYS[0]: {"holds": True}, **extra},
                environment_version=version,
                proved_at=now - timedelta(hours=10 - n),
            )
            versions.append(version)

        with patch(
            "simulate.services.hosted_harness_gateway._scenario_token",
            wraps=_scenario_token,
        ) as read:
            flags = harness_readiness.scenario_readiness(
                _pairing(environment), keys=[LOOKUP_KEYS[0]]
            )

        assert flags[LOOKUP_KEYS[0]]["proof"] == {
            "state": "not_proved",
            "last_proved": {
                "environment_version": versions[2].version_number,
                "suite_version": environment.suite_version.version_number,
            },
        }
        assert "only-in-oldest" not in _tokenised(read)

    def test_the_proof_history_reads_only_suite_versions_with_the_key(
        self, user, workspace, storage
    ):
        environment = _uncertified(user, workspace, storage, "sr-history-suite")
        current = environment.suite_version
        other = HarnessSuiteVersion.no_workspace_objects.get(id=current.id)
        other.pk = None
        other.version_number = (current.version_number or 1) + 50
        other.hash = other.certify_hash = "sha256:" + "a" * 64
        other.save()
        _validated(
            environment,
            {
                LOOKUP_KEYS[0]: {"holds": True},
                "only-in-other-suite": {"holds": True},
            },
            environment_version=environment.environment_version,
            proved_at=timezone.now(),
            suite=other,
        )

        with patch(
            "simulate.services.hosted_harness_gateway._scenario_token",
            wraps=_scenario_token,
        ) as read:
            flags = harness_readiness.scenario_readiness(
                _pairing(environment), keys=[LOOKUP_KEYS[0]]
            )

        assert flags[LOOKUP_KEYS[0]]["proof"] == {
            "state": "not_proved",
            "last_proved": None,
        }
        assert "only-in-other-suite" not in _tokenised(read)


class TestBuckets:
    def test_every_suite_case_has_a_bucket_matching_the_selection(
        self, user, workspace, storage
    ):
        environment = _uncertified(user, workspace, storage, "sr-buckets")
        _proofs(environment, {key: {"holds": True} for key in REFUND_KEYS})

        flags = _flags(environment)
        lists = harness_readiness.selection_report(
            _pairing(environment), keys=None, trials=1
        )["selection"]["lists"]

        for bucket, listed in lists.items():
            assert sorted(listed["keys"]) == sorted(
                key for key, one in flags.items() if one["bucket"] == bucket
            )

    def test_a_stale_row_names_its_reason(self, user, workspace, storage, monkeypatch):
        environment = _ten_certified(user, workspace, storage, "sr-reason")
        real = harness_readiness.selection.readiness_report

        def stale(pairing):
            report = real(pairing)
            for row in report["rows"]:
                if row["id"] == "tool_routing":
                    row.update(status="stale", reason_code="environment_changed")
            return report

        monkeypatch.setattr(harness_readiness.selection, "readiness_report", stale)

        one = _flags(environment)[LOOKUP_KEYS[0]]

        assert one["bucket"] == "stale"
        assert one["reasons"]["tool_routing"] == "environment_changed"


class TestScenarioListField:
    def test_the_list_carries_readiness_only_when_asked(
        self, user, workspace, storage, auth_client
    ):
        environment = _ten_certified(user, workspace, storage, "sr-api")

        plain = auth_client.get(f"{SCENARIOS}/{environment.id}/scenarios/")
        asked = auth_client.get(
            f"{SCENARIOS}/{environment.id}/scenarios/", {"readiness": "1"}
        )

        assert plain.status_code == asked.status_code == 200
        assert "readiness" not in str(plain.json())
        rows = [
            row
            for group in asked.json()["results"]
            for row in (group.get("rows") or [group])
            if isinstance(row, dict) and "readiness" in row
        ]
        assert rows
        assert {row["readiness"]["bucket"] for row in rows} == {"eligible"}

    def test_the_list_reads_readiness_for_its_page_only(
        self, user, workspace, storage, auth_client
    ):
        environment = _ten_certified(user, workspace, storage, "sr-api-page")

        with patch.object(
            harness_readiness,
            "scenario_readiness",
            wraps=harness_readiness.scenario_readiness,
        ) as read:
            response = auth_client.get(
                f"{SCENARIOS}/{environment.id}/scenarios/",
                {"readiness": "1", "limit": 2},
            )

        assert response.status_code == 200
        listed = [
            row["scenario_key"]
            for group in response.json()["results"]
            for row in (group.get("rows") or [group])
            if isinstance(row, dict) and "scenario_key" in row
        ]
        assert len(listed) == 2
        assert sorted(read.call_args.kwargs["keys"]) == sorted(listed)

    def test_an_environment_with_no_versions_yet_lists_its_scenarios_without_flags(
        self, user, workspace, storage, auth_client
    ):
        environment = _ten_certified(user, workspace, storage, "sr-api-unversioned")
        # A build that failed or is still running has no versions to pair yet.
        type(environment).objects.filter(id=environment.id).update(
            environment_version=None, suite_version=None
        )

        asked = auth_client.get(
            f"{SCENARIOS}/{environment.id}/scenarios/", {"readiness": "1"}
        )

        assert asked.status_code == 200
        rows = [
            row
            for group in asked.json()["results"]
            for row in (group.get("rows") or [group])
            if isinstance(row, dict) and "scenario_key" in row
        ]
        assert rows
        assert all(row.get("readiness") is None for row in rows)

    def test_a_bad_version_id_is_refused(self, user, workspace, storage, auth_client):
        environment = _ten_certified(user, workspace, storage, "sr-api-bad")

        response = auth_client.get(
            f"{SCENARIOS}/{environment.id}/scenarios/",
            {"readiness": "1", "environment_version_id": "nope"},
        )

        assert response.status_code == 400

    def test_the_readiness_read_costs_no_query_per_row(
        self, user, workspace, storage, auth_client
    ):
        small = _ten_certified(user, workspace, storage, "sr-q-small")
        large = _environment(user, workspace, storage, "sr-q-large")
        _suite(
            large,
            [
                {**_ten()[n % 10], "scenario_key": f"case-{n}", "name": f"case-{n}"}
                for n in range(220)
            ],
        )

        def count(environment):
            with CaptureQueriesContext(connection) as seen:
                response = auth_client.get(
                    f"{SCENARIOS}/{environment.id}/scenarios/",
                    {"readiness": "1", "limit": 250},
                )
            assert response.status_code == 200
            return len(seen)

        count(small)
        count(large)
        assert count(large) - count(small) < 15
