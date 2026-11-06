import pytest
from django.core.cache import cache
from django.utils import timezone

from simulate.models import (
    HarnessAssembledBundle,
    HarnessEnvironmentVersion,
    HarnessSuiteMember,
    HarnessSuiteValidation,
    HarnessSuiteVersion,
    HostedHarnessScenario,
)
from simulate.services import harness_readiness
from simulate.services.harness_readiness import selection
from simulate.services.hosted_harness import HostedHarnessError
from simulate.tests.test_harness_readiness import (
    PASSED,
    PRODUCTION_ONLY,
    _built,
    _certify,
    _environment,
    _pairing,
    _rows,
    _with_a2,
)
from simulate.tests.test_harness_readiness import (
    readiness_on as readiness_on,  # noqa: F401 - fixture
)
from simulate.tests.test_harness_readiness import (
    scheduled as scheduled,  # noqa: F401 - fixture
)
from simulate.tests.test_harness_readiness import (
    storage as storage,  # noqa: F401 - fixture
)
from simulate.tests.test_harness_versioning_assembly import _row
from simulate.tests.test_harness_versioning_runs import _run

pytestmark = pytest.mark.django_db

LOOKUP = "lookup_order"
REFUND = "issue_refund"
SUB_GOALS = [
    {
        "name": "refunded",
        "check": (
            "def check(world, calls):\n"
            "    rows = world.query('select * from refunds')\n"
            "    return None if rows else 'no refund was issued'\n"
        ),
    },
    {
        "name": "looked_up",
        "check": (
            "def check(world, calls):\n"
            f"    if not any(c.name == '{LOOKUP}' for c in calls):\n"
            "        return 'the order was never looked up'\n"
        ),
    },
    {"name": "polite", "judged": "The agent stayed polite throughout"},
    {"name": "broken", "check": "def check(world, calls:\n"},
]
REFUND_KEYS = [f"refund-{n}" for n in range(1, 5)]
LOOKUP_KEYS = [f"status-{n}" for n in range(1, 7)]


def _doc(key, *, solution=(), alternatives=(), sub_goals=("looked_up",), **fields):
    return {
        "name": key,
        "scenario_key": key,
        "persona": {"name": f"caller of {key}"},
        "solution": [{"tool": tool, "arguments": {}} for tool in solution],
        "alternatives": [
            [{"tool": tool, "arguments": {}} for tool in path] for path in alternatives
        ],
        "sub_goals": list(sub_goals),
        "max_turns": 6,
        **fields,
    }


def _ten():
    """Four refunds that call the refund tool on every path, six order lookups."""
    return [
        _doc(key, solution=(LOOKUP, REFUND), sub_goals=("refunded",))
        for key in REFUND_KEYS
    ] + [_doc(key, solution=(LOOKUP,)) for key in LOOKUP_KEYS]


def _suite(environment, documents, *, members=True, numbers=None, sub_goals=SUB_GOALS):
    """Give the default suite version these scenarios and the default environment
    version this sub-goal catalogue; ``numbers`` renumbers the members."""
    suite = environment.suite_version
    HarnessSuiteVersion.no_workspace_objects.filter(id=suite.id).update(
        documents={**(suite.documents or {}), "scenarios": documents}
    )
    target = environment.environment_version
    HarnessEnvironmentVersion.no_workspace_objects.filter(id=target.id).update(
        documents={**(target.documents or {}), "sub_goals": sub_goals}
    )
    HarnessSuiteMember.no_workspace_objects.filter(suite_version=suite).delete()
    if members:
        for index, document in enumerate(documents):
            number = numbers[index] if numbers else index + 1
            row = HostedHarnessScenario.no_workspace_objects.create(
                job=environment, scenario_key=document["scenario_key"], number=number
            )
            HarnessSuiteMember.no_workspace_objects.create(
                suite_version=suite,
                scenario=row,
                scenario_key=row.scenario_key,
                number=number,
                content={"name": document["name"], "persona": document["persona"]},
            )
    environment.refresh_from_db()
    cache.clear()


def _select(environment, keys=None, *, trials=1, pairing=None):
    return harness_readiness.selection_report(
        pairing or _pairing(environment), keys=keys, trials=trials
    )


def _ten_certified(user, workspace, storage, key, **certify):
    environment = _environment(user, workspace, storage, key)
    _suite(environment, _ten())
    _certify(environment, storage, **certify)
    return environment


class TestBuckets:
    def test_four_of_ten_blocked_six_eligible(self, user, workspace, storage):
        environment = _ten_certified(user, workspace, storage, "as-four")
        _built(environment, contract=PRODUCTION_ONLY)

        report = _select(environment)

        chosen = report["selection"]
        assert (chosen["requested"], chosen["blocked"], chosen["eligible"]) == (
            10,
            4,
            6,
        )
        assert chosen["lists"]["blocked"] == {"keys": REFUND_KEYS, "truncated": False}
        assert chosen["lists"]["eligible"]["keys"] == LOOKUP_KEYS
        routing = _rows(report)["tool_routing"]
        assert routing["needed_by"] == 10
        assert routing["affected"] == {
            "count": 4,
            "of": 10,
            "keys": REFUND_KEYS,
            "truncated": False,
        }

    def test_other_tool_failing_keeps_case_eligible(self, user, workspace, storage):
        environment = _ten_certified(
            user,
            workspace,
            storage,
            "as-other-tool",
            checks={**PASSED, "action_behavior": "1/2"},
            actions=[
                {"action": LOOKUP, "status": "passed", "reason": ""},
                {"action": REFUND, "status": "failed", "reason": "live"},
            ],
        )

        chosen = _select(environment)["selection"]

        assert chosen["lists"]["blocked"]["keys"] == REFUND_KEYS
        assert chosen["lists"]["eligible"]["keys"] == LOOKUP_KEYS

    def test_unproved_case_is_stale(self, user, workspace, storage):
        environment = _environment(user, workspace, storage, "as-unproved")
        _suite(environment, _ten())
        HarnessAssembledBundle._base_manager.filter(id=_row(environment).id).update(
            state="pending", object_key=None
        )
        _built(environment)
        HarnessSuiteValidation.no_workspace_objects.update_or_create(
            suite_version=environment.suite_version,
            environment_version=environment.environment_version,
            defaults={
                "status": "partial",
                "proved_at": timezone.now(),
                "proofs": {
                    key: {"holds": key != LOOKUP_KEYS[0]}
                    for key in REFUND_KEYS + LOOKUP_KEYS
                },
            },
        )

        pairing = _pairing(environment)
        chosen = _select(environment, pairing=pairing)["selection"]
        page = harness_readiness.case_page(
            pairing, keys=None, trials=1, bucket="stale", row=None, cursor=None
        )

        assert (chosen["stale"], chosen["eligible"]) == (1, 9)
        assert [(one["key"], one["by"]) for one in page["items"]] == [
            (LOOKUP_KEYS[0], ["not_proved_here"])
        ]

    def test_ready_pair_bundle_counts_as_proved(self, user, workspace, storage):
        environment = _ten_certified(user, workspace, storage, "as-ready-proves")
        HarnessSuiteValidation.no_workspace_objects.filter(
            suite_version=environment.suite_version
        ).delete()

        chosen = _select(environment)["selection"]

        assert (chosen["stale"], chosen["eligible"]) == (0, 10)

    def test_buckets_sum_to_requested(self, user, workspace, storage):
        environment = _environment(user, workspace, storage, "as-sum")
        documents = _ten()
        documents[-1] = _doc(LOOKUP_KEYS[-1], sub_goals=("never_written",))
        _suite(environment, documents)
        _certify(environment, storage)
        _built(environment, contract=PRODUCTION_ONLY)
        HarnessSuiteValidation.no_workspace_objects.filter(
            suite_version=environment.suite_version
        ).delete()
        HarnessAssembledBundle._base_manager.filter(id=_row(environment).id).update(
            state="pending", object_key=None
        )
        HarnessSuiteValidation.no_workspace_objects.create(
            suite_version=environment.suite_version,
            environment_version=environment.environment_version,
            status="partial",
            proved_at=timezone.now(),
            proofs={key: {"holds": True} for key in REFUND_KEYS[1:] + LOOKUP_KEYS[1:]},
        )

        chosen = _select(environment)["selection"]

        counts = {bucket: chosen[bucket] for bucket in selection.BUCKETS}
        assert counts == {"blocked": 4, "stale": 1, "untested": 1, "eligible": 4}
        assert sum(counts.values()) == chosen["requested"] == 10
        assert chosen["lists"]["untested"]["keys"] == [LOOKUP_KEYS[-1]]
        # Blocked and not proved here: the first match wins.
        assert chosen["lists"]["blocked"]["keys"] == REFUND_KEYS
        assert chosen["grading"]["not_gradable"] == [
            {"key": LOOKUP_KEYS[-1], "reason": "check_unreadable"}
        ]

    def test_dropped_key_is_listed_not_rejected(self, user, workspace, storage):
        environment = _ten_certified(
            user,
            workspace,
            storage,
            "as-dropped",
            warnings=[{"code": "scenario_dropped", "keys": [LOOKUP_KEYS[0]]}],
        )

        chosen = _select(environment, [LOOKUP_KEYS[0], LOOKUP_KEYS[1]])["selection"]

        assert chosen["dropped"] == [
            {
                "key": LOOKUP_KEYS[0],
                "code": "scenario_dropped",
                "reason": "dropped when the pair was certified",
            }
        ]
        assert (chosen["requested"], chosen["eligible"]) == (1, 1)

    def test_reset_needed_by_every_case_of_a_seeded_world_run(
        self, user, workspace, storage
    ):
        """A case whose checks read only calls still meets what an earlier case left
        in the seeded world, through its tools."""
        environment = _ten_certified(
            user,
            workspace,
            storage,
            "as-reset",
            checks={**PASSED, "reset_equivalence": "failed"},
        )

        alone = _select(environment, [LOOKUP_KEYS[0]])["selection"]
        twice = _select(environment, [LOOKUP_KEYS[0]], trials=2)["selection"]
        suite = _select(environment)["selection"]

        assert (alone["eligible"], twice["blocked"]) == (1, 1)
        assert suite["lists"]["blocked"]["keys"] == REFUND_KEYS + LOOKUP_KEYS

    def test_no_seeded_world_needs_no_reset(self, user, workspace, storage):
        environment = _ten_certified(
            user,
            workspace,
            storage,
            "as-reset-none",
            checks={
                **PASSED,
                "reset_equivalence": "not_applicable",
                "world_isolation": "not_applicable",
            },
        )

        report = _select(environment)

        assert _rows(report)["reset"]["status"] == "unavailable"
        assert report["selection"]["eligible"] == 10

    def test_grading_needs_readable_checks_and_supported_evidence(
        self, user, workspace, storage
    ):
        environment = _environment(user, workspace, storage, "as-grading")
        documents = _ten()
        documents[-1] = _doc(LOOKUP_KEYS[-1], sub_goals=("broken",))
        _suite(environment, documents)
        _certify(environment, storage, checks={**PASSED, "schema_and_seed": "failed"})

        grading = _select(environment)["selection"]["grading"]

        assert grading == {
            "gradable": 5,
            "not_gradable": [
                *(
                    {"key": key, "reason": "evidence_not_supported"}
                    for key in REFUND_KEYS
                ),
                {"key": LOOKUP_KEYS[-1], "reason": "check_unreadable"},
            ],
            "truncated": False,
        }

    def test_unmeasured_row_holds_no_case(self, user, workspace, storage):
        environment = _ten_certified(user, workspace, storage, "as-unmeasured")
        version = environment.environment_version
        HarnessEnvironmentVersion.no_workspace_objects.filter(id=version.id).update(
            documents={**version.documents, "contract": {"modality": "voice"}}
        )
        environment.refresh_from_db()

        report = _select(environment)

        rows = _rows(report)
        for one in ("continuity", "audio"):
            assert rows[one]["status"] == "untested"
            assert rows[one]["needed_by"] == 10
            assert rows[one]["affected"]["count"] == 0
        assert report["selection"]["eligible"] == 10
        assert report["selection"]["grading"]["gradable"] == 10

    def test_voice_case_does_not_need_a_test_message(self, user, workspace, storage):
        environment = _ten_certified(
            user,
            workspace,
            storage,
            "as-voice-message",
            checks={**PASSED, "chat_ingress": "not_applicable"},
        )
        version = environment.environment_version
        HarnessEnvironmentVersion.no_workspace_objects.filter(id=version.id).update(
            documents={**version.documents, "contract": {"modality": "voice"}}
        )
        environment.refresh_from_db()

        report = _select(environment)

        message = _rows(report)["message"]
        assert message["status"] == "unavailable"
        assert message["needed_by"] == 0
        assert message["affected"]["count"] == 0
        assert report["selection"]["eligible"] == 10


class TestRunRefusals:
    def test_failed_validation_blocks_every_case_as_runs_are_refused(
        self, user, workspace, storage
    ):
        """A validation failure naming no action leaves every row Supported, but runs
        of the pairing are refused: every case is blocked by it."""
        environment = _ten_certified(user, workspace, storage, "as-pair-failed")
        HarnessAssembledBundle._base_manager.filter(id=_row(environment).id).update(
            state="failed",
            object_key=None,
            error={"code": "certify_incomplete", "source": "validation"},
        )
        _built(environment)
        pairing = _pairing(environment)

        report = _select(environment, pairing=pairing)
        page = harness_readiness.case_page(
            pairing, keys=None, trials=1, bucket="blocked", row=None, cursor=None
        )

        assert report["answer"]["reason_code"] == "pair_failed"
        assert (report["selection"]["blocked"], report["selection"]["requested"]) == (
            10,
            10,
        )
        assert {tuple(one["by"]) for one in page["items"]} == {("pair_failed",)}

    def test_a_validating_pairing_reads_every_case_untested_as_runs_are_refused(
        self, user, workspace, storage
    ):
        """While the pairing is being checked the run gate refuses it and nothing is
        known yet: every case is untested by the check, never eligible."""
        environment = _ten_certified(user, workspace, storage, "as-pair-validating")
        HarnessAssembledBundle._base_manager.filter(id=_row(environment).id).update(
            state="pending", object_key=None, error=None
        )
        _built(environment)
        pairing = _pairing(environment)
        assert harness_readiness.recheck(pairing)["state"] == "validating"

        chosen = _select(environment, pairing=pairing)["selection"]
        page = harness_readiness.case_page(
            pairing, keys=None, trials=1, bucket="untested", row=None, cursor=None
        )
        with pytest.raises(HostedHarnessError) as refused:
            _run(environment, key="as-pair-validating")

        assert refused.value.code == "pair_validating"
        assert {bucket: chosen[bucket] for bucket in selection.BUCKETS} == {
            "eligible": 0,
            "blocked": 0,
            "stale": 0,
            "untested": chosen["requested"],
        }
        assert chosen["requested"] == 10
        assert {tuple(one["by"]) for one in page["items"]} == {("pair_validating",)}

    def test_a_pairing_an_ordinary_run_failed_blocks_no_case(
        self, user, workspace, storage
    ):
        """Runs are refused only after a validation failed, so cases are too."""
        environment = _ten_certified(user, workspace, storage, "as-run-failed")
        HarnessAssembledBundle._base_manager.filter(id=_row(environment).id).update(
            state="failed", object_key=None, error={"code": "certify_incomplete"}
        )
        _built(environment)

        assert _select(environment)["selection"]["blocked"] == 0

    def test_case_grading_no_sub_goal_is_excluded_as_the_run_refuses_it(
        self, user, workspace, storage
    ):
        environment = _ten_certified(user, workspace, storage, "as-ungradable")
        for key, sub_goals in ((LOOKUP_KEYS[0], []), (LOOKUP_KEYS[1], ["unwritten"])):
            HarnessSuiteMember.no_workspace_objects.filter(
                suite_version=environment.suite_version, scenario_key=key
            ).update(content={"name": key, "sub_goals": sub_goals})
        cache.clear()

        chosen = _select(environment)["selection"]

        reason = "grades no sub-goal of this environment version"
        assert chosen["dropped"] == [
            {"key": LOOKUP_KEYS[0], "code": "scenario_ungradable", "reason": reason},
            {"key": LOOKUP_KEYS[1], "code": "scenario_ungradable", "reason": reason},
        ]
        assert (chosen["requested"], chosen["eligible"]) == (8, 8)
        assert chosen["lists"]["eligible"]["keys"] == REFUND_KEYS + LOOKUP_KEYS[2:]


class TestListsAndCounts:
    def test_lists_past_the_limit_are_cut_and_flagged(
        self, user, workspace, storage, monkeypatch
    ):
        environment = _ten_certified(
            user,
            workspace,
            storage,
            "as-capped",
            checks={**PASSED, "schema_and_seed": "failed"},
        )
        monkeypatch.setattr(selection, "LIST_LIMIT", 3)

        report = _select(environment)

        chosen = report["selection"]
        # The failed seed leaves reset unchecked, which every case of the run needs.
        assert (chosen["blocked"], chosen["untested"]) == (4, 6)
        assert chosen["lists"]["blocked"] == {
            "keys": REFUND_KEYS[:3],
            "truncated": True,
        }
        assert chosen["lists"]["untested"] == {
            "keys": LOOKUP_KEYS[:3],
            "truncated": True,
        }
        assert chosen["lists"]["stale"] == {"keys": [], "truncated": False}
        assert _rows(report)["state"]["affected"] == {
            "count": 4,
            "of": 10,
            "keys": REFUND_KEYS[:3],
            "truncated": True,
        }
        assert len(chosen["grading"]["not_gradable"]) == 3
        assert chosen["grading"]["truncated"] is True

    def test_a_member_with_no_document_is_untested_and_unrecorded(
        self, user, workspace, storage
    ):
        environment = _environment(user, workspace, storage, "as-unrecorded")
        documents = _ten()
        _suite(environment, documents)
        suite = environment.suite_version
        HarnessSuiteVersion.no_workspace_objects.filter(id=suite.id).update(
            documents={**suite.documents, "scenarios": documents[:-1]}
        )
        _certify(environment, storage)
        cache.clear()

        everything = _select(environment)["selection"]
        recorded = _select(environment, LOOKUP_KEYS[:2])["selection"]

        assert everything["requirements_recorded"] is False
        assert everything["lists"]["untested"]["keys"] == [LOOKUP_KEYS[-1]]
        assert recorded["requirements_recorded"] is True

    def test_an_untested_row_counts_the_cases_it_holds(self, user, workspace, storage):
        environment = _ten_certified(
            user,
            workspace,
            storage,
            "as-untested-row",
            checks={**PASSED, "chat_ingress": "not_run"},
        )

        report = _select(environment)

        message = _rows(report)["message"]
        assert message["status"] == "untested"
        assert message["affected"]["count"] == 10
        assert report["selection"]["untested"] == 10

    def test_a_stale_row_counts_the_cases_it_holds(self, user, workspace, storage):
        environment, a2 = _with_a2(user, workspace, storage, "as-stale-row")
        _suite(environment, _ten())
        _built(environment)

        report = _select(environment, pairing=_pairing(environment, a2))

        start = _rows(report)["start"]
        assert start["status"] == "stale"
        assert start["affected"]["count"] == 10
        assert report["selection"]["stale"] == 10
