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
from simulate.services.harness_scenario_lifecycle import scenario_lifecycle
from simulate.services.hosted_harness import HostedHarnessError
from simulate.tests.test_harness_readiness import (
    NOT_PROBED,
    PASSED,
    PRODUCTION_ONLY,
    SAME_TOOLS,
    WORLD,
    _built,
    _certify,
    _environment,
    _pairing,
    _rows,
    _tool,
    _with_a2,
)
from simulate.tests.test_harness_readiness import (  # noqa: F401 - fixture
    scheduled as scheduled,
)
from simulate.tests.test_harness_readiness import (  # noqa: F401 - fixture
    storage as storage,
)
from simulate.tests.test_harness_versioning_assembly import _row
from simulate.tests.test_harness_versioning_runs import _run

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("readiness_on")]

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


PROVED = {
    "format": 2,
    "holds": True,
    "proved": True,
    "assumed": [],
    "blocked_reason": None,
}


def _suite(
    environment,
    documents,
    *,
    members=True,
    numbers=None,
    sub_goals=SUB_GOALS,
    proved=True,
):
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
    if members and proved:
        # Every case proved on the default pair, so run selection weighs only its rows.
        HarnessSuiteValidation.no_workspace_objects.update_or_create(
            suite_version=suite,
            environment_version=target,
            defaults={
                "status": "passed",
                "proved_at": timezone.now(),
                "proofs": {one["scenario_key"]: PROVED for one in documents},
            },
        )
    environment.refresh_from_db()
    cache.clear()


def _proved_on(environment, environment_version, suite_version=None):
    """Every member proved on this pair, as a validation that certifies and proves writes it."""
    from simulate.tests.harness_validation import prove_pair

    prove_pair(environment, environment_version, suite_version)


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
        # The run dialog names the area holding them back as the server picks it.
        assert chosen["blocking_area"] == "tool_binding"
        routing = _rows(report)["tool_routing"]
        assert routing["needed_by"] == 10
        assert routing["affected"] == {
            "count": 4,
            "of": 10,
            "keys": REFUND_KEYS,
            "truncated": False,
        }

    @pytest.mark.parametrize(
        "read", ["selection_report", "case_page", "select_for_run"]
    )
    def test_each_report_reads_the_pairing_s_evidence_once(
        self, user, workspace, storage, monkeypatch, read
    ):
        from simulate.services.harness_readiness import report as report_module

        environment = _ten_certified(user, workspace, storage, f"once-{read}")
        pairing = _pairing(environment)
        reads = []
        real = selection.read_evidence

        def counted(*args, **kwargs):
            reads.append(args)
            return real(*args, **kwargs)

        monkeypatch.setattr(selection, "read_evidence", counted)
        monkeypatch.setattr(report_module, "read_evidence", counted)
        cache.clear()

        if read == "selection_report":
            harness_readiness.selection_report(pairing, keys=None, trials=1)
        elif read == "case_page":
            harness_readiness.case_page(
                pairing, keys=None, trials=1, bucket=None, row=None, cursor=None
            )
        else:
            harness_readiness.select_for_run(pairing, keys=LOOKUP_KEYS, trials=1)

        assert len(reads) == 1

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

    def _proofs(self, environment, proofs):
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
                "proofs": proofs,
            },
        )

    def test_unvalidated_case_is_held_back(self, user, workspace, storage):
        environment = _environment(user, workspace, storage, "as-unproved")
        _suite(environment, _ten())
        self._proofs(
            environment,
            {key: PROVED for key in REFUND_KEYS + LOOKUP_KEYS if key != LOOKUP_KEYS[0]},
        )

        pairing = _pairing(environment)
        chosen = _select(environment, pairing=pairing)["selection"]
        page = harness_readiness.case_page(
            pairing, keys=None, trials=1, bucket="blocked", row=None, cursor=None
        )

        assert (chosen["blocked"], chosen["eligible"]) == (1, 9)
        assert [(one["key"], one["by"]) for one in page["items"]] == [
            (LOOKUP_KEYS[0], ["not_validated"])
        ]

    def test_kept_as_written_runs_with_warning(self, user, workspace, storage):
        environment = _environment(user, workspace, storage, "as-assumed")
        _suite(environment, _ten())
        kept = {
            "format": 2,
            "holds": True,
            "proved": False,
            "assumed": [LOOKUP],
            "solution_steps": 1,
            "kept_as_written": True,
            "blocked_reason": "solution_assumed",
        }
        leaked = {
            **kept,
            "kept_as_written": False,
            "blocked_reason": "answer_leaked",
            "leaks": [
                {
                    "who": "simulated person",
                    "field": "instruction",
                    "grader_field": "tests",
                    "excerpt": "x",
                }
            ],
        }
        proofs = dict.fromkeys(REFUND_KEYS + LOOKUP_KEYS, PROVED)
        proofs[LOOKUP_KEYS[0]] = kept
        proofs[LOOKUP_KEYS[1]] = leaked
        self._proofs(environment, proofs)

        views = scenario_lifecycle(
            environment,
            suite_version=environment.suite_version,
            environment_version=environment.environment_version,
        )
        chosen = _select(environment)["selection"]

        assert (views[LOOKUP_KEYS[0]]["state"], views[LOOKUP_KEYS[0]]["warning"]) == (
            "kept_as_written",
            "solution_assumed",
        )
        assert (views[LOOKUP_KEYS[1]]["state"], views[LOOKUP_KEYS[1]]["reason"]) == (
            "draft",
            "answer_leaked",
        )
        assert (chosen["blocked"], chosen["eligible"]) == (1, 9)

    def test_certified_pair_without_proofs_is_not_proved(
        self, user, workspace, storage
    ):
        environment = _ten_certified(user, workspace, storage, "as-ready-proves")
        HarnessSuiteValidation.no_workspace_objects.filter(
            suite_version=environment.suite_version
        ).delete()

        chosen = _select(environment)["selection"]

        assert (chosen["blocked"], chosen["eligible"]) == (10, 0)

    def test_archived_case_is_held_back(self, user, workspace, storage):
        environment = _ten_certified(user, workspace, storage, "as-archived")
        HostedHarnessScenario.no_workspace_objects.filter(
            job=environment, scenario_key=LOOKUP_KEYS[0]
        ).update(archived_at=timezone.now(), archive_reason="retired route")
        pairing = _pairing(environment)

        chosen = _select(environment, pairing=pairing)["selection"]
        page = harness_readiness.case_page(
            pairing, keys=None, trials=1, bucket="blocked", row=None, cursor=None
        )

        assert chosen["blocked"] == 1
        assert [(one["key"], one["by"]) for one in page["items"]] == [
            (LOOKUP_KEYS[0], ["scenario_archived"])
        ]

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
        # A case with no proof on the pair is held back as not validated (section 4.9), no
        # longer sent as stale.
        assert counts == {"blocked": 5, "stale": 0, "untested": 1, "eligible": 4}
        assert sum(counts.values()) == chosen["requested"] == 10
        assert chosen["lists"]["untested"]["keys"] == [LOOKUP_KEYS[-1]]
        # Blocked and not proved here: the first match wins.
        assert chosen["lists"]["blocked"]["keys"] == REFUND_KEYS + [LOOKUP_KEYS[0]]
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
                "reason": "dropped when these versions were checked",
            }
        ]
        assert (chosen["requested"], chosen["eligible"]) == (1, 1)

    def test_a_run_holds_back_a_case_certifying_dropped(self, user, workspace, storage):
        """Run all sends every key of the suite: a dropped case is held back and named,
        and the rest still run."""
        environment = _ten_certified(
            user,
            workspace,
            storage,
            "as-dropped-run",
            warnings=[{"code": "scenario_dropped", "keys": [LOOKUP_KEYS[0]]}],
        )

        plan = harness_readiness.select_for_run(
            _pairing(environment),
            keys=[LOOKUP_KEYS[0], LOOKUP_KEYS[1]],
            trials=1,
        )

        assert plan.held == frozenset({LOOKUP_KEYS[0]})
        assert (plan.view["requested"], plan.view["running"]) == (2, 1)
        assert plan.view["held_back"]["by"] == {"scenario_dropped": 1}
        assert plan.view["reasons"] == {
            "scenario_dropped": "dropped when these versions were checked"
        }

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


class TestGrading:
    """Whether the judge answered in the check decides if judged checks can be graded."""

    def _judged(self, user, workspace, storage, key, checks):
        environment = _environment(user, workspace, storage, key)
        _suite(
            environment,
            [
                _doc("polite-1", sub_goals=("polite",)),
                _doc("polite-2", sub_goals=("looked_up", "polite")),
                _doc("coded", solution=(LOOKUP,)),
            ],
        )
        _certify(environment, storage, checks=checks)
        return environment

    def test_an_unreachable_judge_makes_judged_cases_not_gradable(
        self, user, workspace, storage
    ):
        environment = self._judged(
            user,
            workspace,
            storage,
            "gr-judge-down",
            {**PASSED, "judge_reachable": "failed"},
        )

        grading = _select(environment)["selection"]["grading"]

        assert grading["not_gradable"] == [
            {"key": "polite-1", "reason": "judge_unavailable"},
            {"key": "polite-2", "reason": "judge_unavailable"},
        ]
        assert grading["gradable"] == 1

    @pytest.mark.parametrize("judge", ["passed", "not_run", None])
    def test_an_old_certificate_without_the_judge_check_reports_no_gap(
        self, user, workspace, storage, judge
    ):
        checks = {**PASSED, **({"judge_reachable": judge} if judge else {})}
        environment = self._judged(
            user, workspace, storage, f"gr-judge-{judge}", checks
        )

        grading = _select(environment)["selection"]["grading"]

        assert grading["not_gradable"] == []
        assert grading["gradable"] == 3


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

        reason = "has no check this environment version grades"
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


def _run_metadata(run):
    run.refresh_from_db()
    return run.payload["metadata"]


def test_a_case_held_for_two_reasons_is_counted_once():
    """A refund case missing its tool is held by the tool's reach and by its proof's needs: one case, one line."""
    from types import SimpleNamespace

    from simulate.services.harness_readiness.selection import (
        PROOF_FAILED_ROW,
        TOOL_MISSING_ROW,
        _held_back,
    )

    def held(key, by, why):
        return SimpleNamespace(
            case=SimpleNamespace(key=key, name=key),
            bucket="blocked",
            by=by,
            lifecycle_reason=why,
            hold=SimpleNamespace(held=False, reason=None),
        )

    needs = "Needs issue_refund, which agent version v14 doesn't have."
    placement = SimpleNamespace(
        placed=[
            held("refund-1", ["tool_discovery", TOOL_MISSING_ROW], needs),
            held("refund-2", ["tool_discovery", TOOL_MISSING_ROW], needs),
            held(
                "lookup-1", [PROOF_FAILED_ROW], "The expected way fails its own checks."
            ),
        ],
        dropped=[],
    )
    rows = [
        {
            "id": "tool_discovery",
            "reason": "Not in this agent version: issue_refund",
            "label": "Tools",
        }
    ]

    keys, by, reasons = _held_back(placement, rows, [])

    assert sum(by.values()) == len(keys) == 3
    # Under the case's own reason, the clearer of the two.
    assert dict(by) == {TOOL_MISSING_ROW: 2, PROOF_FAILED_ROW: 1}
    assert reasons[TOOL_MISSING_ROW] == needs
    assert set(reasons) == set(by)


def test_a_blocked_row_holds_back_only_the_cases_it_blocks():
    """A missing tool blocks the cases that use it; a case it only reads stale for runs, and a case held for another
    reason is not held by it, so the row's count names only the cases the tool holds back. A row that is not blocked
    still counts every case it holds."""
    from types import SimpleNamespace

    from simulate.services.harness_readiness.selection import (
        NOT_VALIDATED_ROW,
        _row_holds,
    )

    def placed(key, bucket, by):
        return SimpleNamespace(
            case=SimpleNamespace(key=key),
            bucket=bucket,
            by=by,
            held_by=frozenset({"tool_discovery", "tool_routing"}),
        )

    cases = [
        placed("refund", "blocked", ["tool_discovery", NOT_VALIDATED_ROW]),
        placed("lookup", "blocked", [NOT_VALIDATED_ROW]),
        placed("status", "stale", ["tool_routing"]),
    ]

    assert _row_holds({"id": "tool_discovery", "status": "blocked"}, cases) == ["refund"]
    assert _row_holds({"id": "tool_routing", "status": "stale"}, cases) == [
        "refund",
        "lookup",
        "status",
    ]


def test_cases_held_for_something_to_fix_are_listed_before_those_only_awaiting_validation():
    from types import SimpleNamespace

    from simulate.services.harness_readiness.selection import (
        NOT_VALIDATED_ROW,
        TOOL_MISSING_ROW,
        _held_back,
    )

    def held(key, by):
        return SimpleNamespace(
            case=SimpleNamespace(key=key, name=key),
            bucket="blocked",
            by=by,
            lifecycle_reason=None,
            hold=SimpleNamespace(held=False, reason=None),
        )

    placement = SimpleNamespace(
        placed=[
            held("lookup-1", [NOT_VALIDATED_ROW]),
            held("refund-1", ["tool_discovery", TOOL_MISSING_ROW]),
            held("lookup-2", [NOT_VALIDATED_ROW]),
            held("refund-2", ["tool_discovery", TOOL_MISSING_ROW]),
        ],
        dropped=[],
    )

    keys, _, _ = _held_back(placement, [], [])

    assert keys == ["refund-1", "refund-2", "lookup-1", "lookup-2"]


@pytest.mark.parametrize(
    ("stale_because", "blocked_by", "kind"),
    [(None, None, "ready"), ("agent_version", None, "stale"), (None, "start", "unchecked")],
)
def test_a_missing_tool_no_selected_case_uses_keeps_its_evidence_s_own_standing(stale_because, blocked_by, kind):
    """The cases do not use the tool the agent lacks, so the row no longer blocks them; evidence measured with
    another version or behind a gate still reads stale or unchecked, never ready."""
    from types import SimpleNamespace

    from simulate.services.harness_readiness.selection import _selected_answer

    row = {
        "id": "tool_discovery",
        "status": "blocked",
        "measured": True,
        "reason_code": "tool_not_in_agent",
        "targets": [{"kind": "tool", "name": "issue_refund", "result": "failed"}],
        "affected": {"count": 0},
        "stale_because": stale_because,
        "blocked_by": blocked_by,
    }
    placed = [SimpleNamespace(case=SimpleNamespace(recorded=True, unknown=False))]

    found = _selected_answer(
        {"kind": "blocked", "worst": "blocked", "reason_code": None}, [row], placed, blocked_workers=0
    )

    assert found["kind"] == kind


def test_each_listed_held_case_names_the_row_it_is_counted_under():
    """The run plan's list says which case is held for which reason, the same row its counts use; a dropped case
    reads dropped, and a case past the list's cap is counted but not listed."""
    from types import SimpleNamespace

    from simulate.services.harness_readiness.selection import (
        DROPPED_ROW,
        LIST_LIMIT,
        NOT_VALIDATED_ROW,
        TOOL_MISSING_ROW,
        _held_back,
        _held_list,
        _held_rows,
    )

    def held(key, by):
        return SimpleNamespace(
            case=SimpleNamespace(key=key, name=key),
            bucket="blocked",
            by=by,
            lifecycle_reason=None,
            hold=SimpleNamespace(held=False, reason=None),
        )

    unproved = [held(f"case-{n:03d}", [NOT_VALIDATED_ROW]) for n in range(LIST_LIMIT)]
    placement = SimpleNamespace(
        placed=[held("refund", ["tool_discovery", TOOL_MISSING_ROW]), *unproved],
        dropped=[{"key": "gone", "code": "scenario_dropped"}],
    )

    keys, by, _ = _held_back(placement, [], [])
    listed = _held_list(keys, {}, _held_rows(placement))

    assert listed["truncated"] is True and len(listed["keys"]) == LIST_LIMIT
    assert set(listed["by_key"]) == set(listed["keys"])
    assert listed["by_key"]["refund"] == TOOL_MISSING_ROW
    assert listed["by_key"]["case-000"] == NOT_VALIDATED_ROW
    # Held for something to fix comes first: the dropped case is listed; the last validate-only one is past the cap.
    assert listed["by_key"]["gone"] == DROPPED_ROW
    assert f"case-{LIST_LIMIT - 1:03d}" not in listed["by_key"]
    assert dict(by) == {
        TOOL_MISSING_ROW: 1,
        NOT_VALIDATED_ROW: LIST_LIMIT,
        DROPPED_ROW: 1,
    }


@pytest.mark.parametrize(
    ("placed_by", "expected"),
    [
        # Two holding rows: the case's own lifecycle row wins, as in the counts.
        (["tool_discovery", "not_validated"], "not_validated"),
        # Two pairing rows and no lifecycle row: the first holding row.
        (["tool_routing", "tool_discovery"], "tool_routing"),
        # A worker holds it: its worker key.
        (["worker:researcher"], "worker:researcher"),
    ],
)
def test_a_held_case_s_row_is_the_one_it_is_counted_under(placed_by, expected):
    from collections import Counter
    from types import SimpleNamespace

    from simulate.services.harness_readiness.selection import _held_back, _held_rows

    placement = SimpleNamespace(
        placed=[
            SimpleNamespace(
                case=SimpleNamespace(key="refund / late #1", name=""),
                bucket="blocked",
                by=placed_by,
                lifecycle_reason=None,
                hold=SimpleNamespace(held=False, reason=None),
            ),
            SimpleNamespace(
                case=SimpleNamespace(key="runs", name=""),
                bucket="stale",
                by=["tool_routing"],
                lifecycle_reason=None,
                hold=SimpleNamespace(held=False, reason=None),
            ),
        ],
        dropped=[],
    )

    keys, by, _ = _held_back(placement, [], [])
    rows = _held_rows(placement)

    assert keys == ["refund / late #1"]
    assert rows == {"refund / late #1": expected}
    assert Counter(rows.values()) == by


def test_nothing_held_lists_no_rows():
    from types import SimpleNamespace

    from simulate.services.harness_readiness.selection import _held_list, _held_rows

    placement = SimpleNamespace(placed=[], dropped=[])

    assert _held_list([], {}, _held_rows(placement))["by_key"] == {}


def test_held_back_cases_are_listed_with_their_current_names():
    from types import SimpleNamespace

    from simulate.services.harness_readiness.selection import _case_names, _held_list

    placed = [
        SimpleNamespace(case=SimpleNamespace(key="refuse-30", name="refuse_20_days")),
        SimpleNamespace(case=SimpleNamespace(key="lookup", name="")),
    ]

    held = _held_list(["refuse-30", "lookup"], _case_names(placed))

    assert (held["keys"], held["names"]) == (
        ["refuse-30", "lookup"],
        {"refuse-30": "refuse_20_days"},
    )


class TestHoldBack:
    def test_a_run_holds_back_the_blocked_cases_and_names_them(
        self, user, workspace, storage
    ):
        environment = _ten_certified(user, workspace, storage, "hb-four")
        _built(environment, contract=PRODUCTION_ONLY)

        run = _run(environment, REFUND_KEYS + LOOKUP_KEYS, key="hold")

        metadata = _run_metadata(run)
        chosen = metadata["readiness_selection"]
        assert (chosen["requested"], chosen["running"]) == (10, 6)
        assert chosen["held_back"] == {
            "count": 4,
            "keys": REFUND_KEYS,
            "truncated": False,
            "names": {key: key for key in REFUND_KEYS},
            "by": {"tool_routing": 4},
            "by_key": dict.fromkeys(REFUND_KEYS, "tool_routing"),
        }
        assert "issue_refund reaches production" in chosen["reasons"]["tool_routing"]
        sent = [entry["scenario_key"] for entry in metadata["execution_manifest"]]
        assert sent == LOOKUP_KEYS
        assert metadata["selected_scenario_keys"] == REFUND_KEYS + LOOKUP_KEYS
        assert run.test_execution.total_scenarios == 6
        assert run.test_execution.total_calls == 6

    def test_every_case_blocked_is_refused_and_no_run_is_made(
        self, user, workspace, storage
    ):
        from simulate.models import HostedHarnessJob

        environment = _ten_certified(user, workspace, storage, "hb-all")
        _built(environment, contract=PRODUCTION_ONLY)
        before = HostedHarnessJob.no_workspace_objects.count()

        with pytest.raises(HostedHarnessError) as refused:
            _run(environment, REFUND_KEYS, key="all")

        assert (refused.value.code, refused.value.status_code) == (
            "all_cases_blocked",
            409,
        )
        assert "No run was created" in refused.value.message
        assert "issue_refund reaches production" in refused.value.message
        assert refused.value.details["selection"]["held_back"]["count"] == 4
        assert HostedHarnessJob.no_workspace_objects.count() == before

    def test_tools_the_provider_runs_hold_back_no_case(self, user, workspace, storage):
        """Unavailable means the connection kind does not have it: no case is held."""
        environment = _ten_certified(
            user,
            workspace,
            storage,
            "hb-provider",
            tools=[
                {
                    "tool": tool,
                    "availability": "runtime_only",
                    "entrypoint_mode": "provider",
                }
                for tool in (LOOKUP, REFUND)
            ],
            actions=NOT_PROBED,
        )

        report = _select(environment)
        run = _run(environment, REFUND_KEYS + LOOKUP_KEYS, key="provider")

        routing = _rows(report)["tool_routing"]
        assert (routing["status"], routing["reason_code"]) == (
            "unavailable",
            "tools_in_provider",
        )
        assert report["selection"]["blocked"] == 0
        metadata = _run_metadata(run)
        assert metadata["readiness_selection"]["held_back"]["count"] == 0
        assert len(metadata["execution_manifest"]) == 10

    def test_nothing_blocked_sends_every_case(self, user, workspace, storage):
        environment = _ten_certified(user, workspace, storage, "hb-none")

        run = _run(environment, REFUND_KEYS + LOOKUP_KEYS, key="none")

        metadata = _run_metadata(run)
        assert metadata["readiness_selection"]["held_back"]["count"] == 0
        assert len(metadata["execution_manifest"]) == 10

    def test_pins_moved_after_readiness_was_read_is_refused(
        self, user, workspace, storage, monkeypatch
    ):
        from simulate.services import hosted_harness

        environment = _ten_certified(user, workspace, storage, "hb-moved")
        real = hosted_harness._readiness_plan

        def stale(*args, **kwargs):
            planned = real(*args, **kwargs)
            return hosted_harness._ReadinessPlan(
                pins=(None, None, None), plan=planned.plan
            )

        monkeypatch.setattr(hosted_harness, "_readiness_plan", stale)

        with pytest.raises(HostedHarnessError) as refused:
            _run(environment, LOOKUP_KEYS, key="moved")

        assert refused.value.code == "environment_not_ready"
        assert refused.value.retryable is True


class TestAgentGapCases:
    def _a2(self, user, workspace, storage, key, tools, documents=None):
        environment, a2 = _with_a2(user, workspace, storage, key, tools=tools)
        _suite(environment, documents or _ten())
        _built(environment, contract=WORLD)
        _certify(environment, storage, agent=a2)
        return environment, _pairing(environment, a2)

    def test_a_tool_the_world_lacks_holds_back_every_case(
        self, user, workspace, storage
    ):
        environment, pairing = self._a2(
            user,
            workspace,
            storage,
            "gap-cases-world",
            (*SAME_TOOLS, _tool("send_email", "to")),
            # A case calling no tool can still have the agent call send_email.
            [*_ten(), _doc("chat-1", sub_goals=("polite",))],
        )

        report = _select(environment, pairing=pairing)

        chosen = report["selection"]
        assert (chosen["blocked"], chosen["eligible"]) == (11, 0)
        discovery = _rows(report)["tool_discovery"]
        assert discovery["affected"]["count"] == 11
        assert discovery["needed_by"] == 11
        plan = harness_readiness.select_for_run(
            pairing, keys=[*REFUND_KEYS, *LOOKUP_KEYS, "chat-1"], trials=1
        )
        assert (plan.view["running"], plan.view["held_back"]["by"]) == (
            0,
            {"tool_discovery": 11},
        )
        assert plan.view["reasons"]["tool_discovery"] == (
            "Not in this environment: send_email"
        )

    def test_a_world_tool_the_agent_lacks_holds_back_only_its_cases(
        self, user, workspace, storage
    ):
        environment, pairing = self._a2(
            user, workspace, storage, "gap-cases-agent", (SAME_TOOLS[0],)
        )

        report = _select(environment, pairing=pairing)

        chosen = report["selection"]
        # The rest still run: routing waits on discovery, as for any blocked tool.
        assert (chosen["blocked"], chosen["untested"]) == (4, 6)
        assert chosen["lists"]["blocked"]["keys"] == REFUND_KEYS
        assert _rows(report)["tool_discovery"]["affected"]["count"] == 4

    def test_a_world_tool_no_selected_case_uses_does_not_block_the_answer(
        self, user, workspace, storage
    ):
        environment, pairing = self._a2(
            user, workspace, storage, "gap-cases-unused", (SAME_TOOLS[0],)
        )

        whole = _select(environment, pairing=pairing)
        lookups = _select(environment, LOOKUP_KEYS, pairing=pairing)

        assert whole["answer"]["kind"] == "blocked"
        assert _rows(lookups)["tool_discovery"]["affected"]["count"] == 0
        assert lookups["selection"]["blocked"] == 0
        assert lookups["answer"]["kind"] != "blocked"
        # The blocked tool-binding area holds none of the lookups, so it is not named as holding them.
        assert whole["selection"]["blocking_area"] == "tool_binding"
        assert lookups["selection"]["blocking_area"] is None

    def test_a_changed_signature_holds_back_only_the_cases_using_it(
        self, user, workspace, storage
    ):
        environment, pairing = self._a2(
            user,
            workspace,
            storage,
            "gap-cases-signature",
            (SAME_TOOLS[0], _tool(REFUND, "order_id")),
        )

        report = _select(environment, pairing=pairing)

        assert report["selection"]["lists"]["blocked"]["keys"] == REFUND_KEYS
        assert _rows(report)["tool_routing"]["affected"]["count"] == 4

    def test_an_agent_read_with_no_tools_holds_back_the_cases_using_world_tools(
        self, user, workspace, storage
    ):
        environment, pairing = self._a2(user, workspace, storage, "gap-cases-none", ())

        report = _select(environment, pairing=pairing)

        discovery = _rows(report)["tool_discovery"]
        assert (discovery["status"], discovery["reason_code"]) == (
            "blocked",
            "tool_not_in_agent",
        )
        assert report["selection"]["blocked"] == 10
        plan = harness_readiness.select_for_run(
            pairing, keys=REFUND_KEYS + LOOKUP_KEYS, trials=1
        )
        assert plan.view["running"] == 0

    def test_a_changed_signature_keeps_another_tool_the_world_never_received(
        self, user, workspace, storage
    ):
        environment, a2 = _with_a2(
            user,
            workspace,
            storage,
            "gap-cases-routing-both",
            tools=(SAME_TOOLS[0], _tool(REFUND, "order_id")),
        )
        _suite(environment, _ten())
        _built(environment, contract=WORLD)
        _certify(environment, storage, agent=a2)
        pairing = _pairing(environment, a2)
        HarnessAssembledBundle._base_manager.filter(
            id=harness_readiness.pair_bundle(pairing).id
        ).update(
            certificate={
                "checks": PASSED,
                "tool_routing": {
                    "tools": [
                        {"tool": REFUND, "routed": True},
                        {"tool": LOOKUP, "routed": False, "reason": "not_received"},
                    ]
                },
            }
        )
        cache.clear()

        report = _select(environment, pairing=pairing)
        routing = _rows(report)["tool_routing"]

        assert (routing["status"], routing["reason_code"]) == (
            "blocked",
            "tool_signature_changed",
        )
        assert {one["name"] for one in routing["targets"]} == {REFUND, LOOKUP}
        assert "the test world never received it: " + LOOKUP in routing["reason"]
        # A lookup case needs only the tool the world never received: it is held too.
        assert set(report["selection"]["lists"]["blocked"]["keys"]) == set(
            REFUND_KEYS + LOOKUP_KEYS
        )
        plan = harness_readiness.select_for_run(
            pairing, keys=REFUND_KEYS + LOOKUP_KEYS, trials=1
        )
        assert plan.view["running"] == 0

    def test_a_tool_the_agent_lacks_keeps_another_tool_discovery_rejected(
        self, user, workspace, storage
    ):
        environment, a2 = _with_a2(
            user, workspace, storage, "gap-cases-discovery-both", tools=(SAME_TOOLS[0],)
        )
        _suite(environment, _ten())
        _built(environment, contract=WORLD)
        _certify(
            environment,
            storage,
            agent=a2,
            tools=[
                {"tool": LOOKUP, "availability": "rejected", "reason": "no schema"},
                {"tool": REFUND, "availability": "certified", "reason": ""},
            ],
        )
        pairing = _pairing(environment, a2)

        report = _select(environment, pairing=pairing)
        discovery = _rows(report)["tool_discovery"]

        assert (discovery["status"], discovery["reason_code"]) == (
            "blocked",
            "tool_not_in_agent",
        )
        assert {one["name"] for one in discovery["targets"]} == {REFUND, LOOKUP}
        assert "Not discoverable: " + LOOKUP in discovery["reason"]
        # A lookup case needs only the rejected tool: blocked, not left to run Untested.
        assert set(report["selection"]["lists"]["blocked"]["keys"]) == set(
            REFUND_KEYS + LOOKUP_KEYS
        )
        plan = harness_readiness.select_for_run(
            pairing, keys=REFUND_KEYS + LOOKUP_KEYS, trials=1
        )
        assert plan.view["running"] == 0

    def _stale_gap(self, user, workspace, storage, key, *, actions=None):
        """A2 changes the refund tool's signature and has no pair check of its own:
        its other rows read the build certificate, measured with A1. Every case is
        proved, so only the rows can hold one."""
        environment, a2 = _with_a2(
            user,
            workspace,
            storage,
            key,
            tools=(SAME_TOOLS[0], _tool(REFUND, "order_id")),
        )
        _suite(environment, _ten())
        _built(environment, contract=WORLD, actions=actions)
        HarnessSuiteValidation.no_workspace_objects.update_or_create(
            suite_version=environment.suite_version,
            environment_version=environment.environment_version,
            defaults={
                "status": "passed",
                "proved_at": timezone.now(),
                "proofs": {key: {"holds": True} for key in REFUND_KEYS + LOOKUP_KEYS},
            },
        )
        cache.clear()
        return environment, _pairing(environment, a2)

    def test_a_changed_signature_keeps_the_other_cases_stale(
        self, user, workspace, storage
    ):
        environment, pairing = self._stale_gap(
            user, workspace, storage, "gap-cases-stale"
        )

        report = _select(environment, pairing=pairing)
        routing = _rows(report)["tool_routing"]
        chosen = report["selection"]

        assert (routing["status"], routing["reason_code"]) == (
            "blocked",
            "tool_signature_changed",
        )
        assert (routing["stale_because"], routing["was"]) == (
            "agent_version",
            "partial",
        )
        # Without the gap the lookup cases are Stale; the gap must not make them eligible.
        assert chosen["lists"]["blocked"]["keys"] == REFUND_KEYS
        assert chosen["lists"]["stale"]["keys"] == LOOKUP_KEYS
        # The blocked row holds back the cases it blocks; the lookup cases it reads stale for still run.
        assert routing["affected"]["count"] == len(REFUND_KEYS)
        # Its case list names the same cases its count does.
        page = harness_readiness.case_page(
            pairing, keys=None, trials=1, bucket=None, row="tool_routing", cursor=None
        )
        assert [one["key"] for one in page["items"]] == REFUND_KEYS

    def test_a_changed_signature_with_a_stale_failure_holds_both(
        self, user, workspace, storage
    ):
        environment, pairing = self._stale_gap(
            user,
            workspace,
            storage,
            "gap-cases-stale-failed",
            actions=[
                {"action": LOOKUP, "status": "failed", "reason": "500"},
                {"action": REFUND, "status": "passed", "reason": ""},
            ],
        )

        report = _select(environment, pairing=pairing)
        routing = _rows(report)["tool_routing"]

        assert routing["status"] == "blocked"
        assert {one["name"] for one in routing["targets"]} == {REFUND, LOOKUP}
        assert routing["stale_because"] == "agent_version"
        assert set(report["selection"]["lists"]["blocked"]["keys"]) == set(
            REFUND_KEYS + LOOKUP_KEYS
        )

    def test_an_unread_version_holds_back_no_case(self, user, workspace, storage):
        environment, pairing = self._a2(
            user, workspace, storage, "gap-cases-unread", None
        )

        report = _select(environment, pairing=pairing)

        chosen = report["selection"]
        assert (chosen["blocked"], chosen["untested"]) == (0, 10)
        plan = harness_readiness.select_for_run(
            pairing, keys=REFUND_KEYS + LOOKUP_KEYS, trials=1
        )
        assert plan.view["running"] == 10


def _earlier_suite(environment, *, edited=(), missing=()):
    """A discarded copy of the default suite version whose members differ: ``edited``
    keys carry other contents and ``missing`` keys are absent."""
    import uuid

    suite = HarnessSuiteVersion.no_workspace_objects.get(
        id=environment.suite_version_id
    )
    members = list(HarnessSuiteMember.no_workspace_objects.filter(suite_version=suite))
    suite.pk = uuid.uuid4()
    suite.id = suite.pk
    suite.status = "discarded"
    suite.version_number = None
    suite.save()
    for member in members:
        if member.scenario_key in missing:
            continue
        content = dict(member.content)
        if member.scenario_key in edited:
            content["name"] = "an earlier wording"
        HarnessSuiteMember.no_workspace_objects.create(
            suite_version=suite,
            scenario=member.scenario,
            scenario_key=member.scenario_key,
            number=member.number,
            content=content,
        )
    return suite


class TestScope:
    def test_cases_not_selected_are_excluded_and_counted(
        self, user, workspace, storage
    ):
        environment = _ten_certified(user, workspace, storage, "sc-excluded")

        chosen = _select(environment, [LOOKUP_KEYS[0], REFUND_KEYS[1]])["selection"]

        assert chosen["suite_total"] == 10
        assert chosen["requested"] == 2
        assert chosen["excluded"] == {
            "count": 8,
            "keys": [
                key
                for key in REFUND_KEYS + LOOKUP_KEYS
                if key not in (LOOKUP_KEYS[0], REFUND_KEYS[1])
            ],
            "truncated": False,
            "reason": "not_selected",
        }

    def test_the_whole_suite_excludes_nothing(self, user, workspace, storage):
        environment = _ten_certified(user, workspace, storage, "sc-whole")

        chosen = _select(environment)["selection"]

        assert (chosen["suite_total"], chosen["excluded"]["count"]) == (10, 0)

    def test_nothing_changed_without_an_earlier_run(self, user, workspace, storage):
        environment = _ten_certified(user, workspace, storage, "sc-first")

        changed = _select(environment)["selection"]["changed"]

        assert changed == {"count": 0, "keys": [], "truncated": False, "since": None}

    def test_a_run_of_the_same_suite_version_changed_nothing(
        self, user, workspace, storage
    ):
        environment = _ten_certified(user, workspace, storage, "sc-same")
        run = _run(environment, LOOKUP_KEYS, key="same")

        changed = _select(environment)["selection"]["changed"]

        assert changed["count"] == 0
        assert changed["since"]["run_id"] == str(run.id)

    def test_cases_edited_or_added_since_the_last_run_are_changed(
        self, user, workspace, storage
    ):
        from simulate.models import HostedHarnessJob

        environment = _ten_certified(user, workspace, storage, "sc-changed")
        run = _run(environment, LOOKUP_KEYS, key="earlier")
        earlier = _earlier_suite(
            environment, edited=(LOOKUP_KEYS[1],), missing=(REFUND_KEYS[0],)
        )
        HostedHarnessJob.no_workspace_objects.filter(id=run.id).update(
            suite_version=earlier
        )

        changed = _select(environment)["selection"]["changed"]

        assert changed["keys"] == [REFUND_KEYS[0], LOOKUP_KEYS[1]]
        assert changed["count"] == 2
        assert changed["since"]["run_id"] == str(run.id)

    def test_a_check_is_not_a_run_to_compare_with(self, user, workspace, storage):
        from simulate.models import HostedHarnessJob

        environment = _ten_certified(user, workspace, storage, "sc-check")
        run = _run(environment, LOOKUP_KEYS, key="check")
        payload = dict(run.payload)
        payload["metadata"] = {**payload["metadata"], "certify_only": True}
        HostedHarnessJob.no_workspace_objects.filter(id=run.id).update(payload=payload)

        assert _select(environment)["selection"]["changed"]["since"] is None

    def test_a_run_records_its_scope(self, user, workspace, storage):
        environment = _ten_certified(user, workspace, storage, "sc-run")

        run = _run(environment, LOOKUP_KEYS, key="scoped")

        chosen = _run_metadata(run)["readiness_selection"]
        assert chosen["suite_total"] == 10
        assert chosen["excluded"]["keys"] == REFUND_KEYS
        assert chosen["changed"]["since"] is None

    def test_another_agent_versions_run_is_not_compared(self, user, workspace, storage):
        environment, a2 = _with_a2(user, workspace, storage, "sc-other-agent")
        _suite(environment, _ten())
        _certify(environment, storage)
        _run(environment, LOOKUP_KEYS, key="a1-run")

        changed = _select(environment, pairing=_pairing(environment, a2))["selection"][
            "changed"
        ]

        assert changed["since"] is None


def test_a_production_only_tool_holds_its_cases_for_another_agent_version_too(
    user, workspace, storage
):
    from simulate.services.hosted_harness import (
        VersionPicks,
        create_selected_harness_run,
    )

    environment, a2 = _with_a2(user, workspace, storage, "prod-a2")
    _suite(environment, _ten())
    _certify(environment, storage)
    _built(environment, contract=PRODUCTION_ONLY)
    cache.clear()

    # a2 has no pair check of its own: its routing evidence is Stale, but the contract
    # still says the refund tool reaches production.
    routing = _rows(_select(environment, pairing=_pairing(environment, a2)))[
        "tool_routing"
    ]
    run, _ = create_selected_harness_run(
        environment,
        scenario_keys=REFUND_KEYS + LOOKUP_KEYS,
        trials=1,
        idempotency_key="prod-a2",
        picks=VersionPicks(agent_version_id=a2.id),
    )
    sent = [one["scenario_key"] for one in _run_metadata(run)["execution_manifest"]]

    assert (routing["status"], routing["reason_code"]) == ("blocked", "production_only")
    # Only a new agent version moves the tool off production: the fix opens the agent's versions, never a re-check.
    assert routing["fix"] == {
        "type": "production_only",
        "category": "agent_version",
        "target": REFUND,
        "action": f"Upload a new agent version that serves {REFUND} from its tools service, or remove it",
        "would": f"{REFUND} runs in the test world instead of reaching production",
        "recheck": None,
    }
    assert not set(REFUND_KEYS) & set(sent)
    assert set(LOOKUP_KEYS) <= set(sent)


def test_another_agent_s_tool_gap_keeps_the_production_only_tool_held(
    user, workspace, storage
):
    environment, a2 = _with_a2(
        user,
        workspace,
        storage,
        "gap-prod",
        tools=(
            _tool("lookup_order", "order_id"),
            _tool("issue_refund", "order_ref", "amount"),
        ),
    )
    _suite(environment, _ten())
    _certify(environment, storage)
    _certify(environment, storage, agent=a2)
    _built(
        environment,
        contract={
            **WORLD,
            "tool_entrypoints": [{"tool": "lookup_order", "mode": "in_process"}],
            "dependencies": [
                {
                    "name": "orders-db",
                    "engine": "",
                    "reached": {"host": "orders.example.com"},
                    "used_by": ["lookup_order"],
                }
            ],
        },
    )
    cache.clear()

    report = _select(environment, pairing=_pairing(environment, a2))
    row = _rows(report)["tool_routing"]

    # Both findings hold: the signature gap on issue_refund and production lookup_order.
    assert row["status"] == "blocked"
    assert {"lookup_order", "issue_refund"} <= {one["name"] for one in row["targets"]}
    assert set(report["selection"]["lists"]["blocked"]["keys"]) == set(
        REFUND_KEYS + LOOKUP_KEYS
    )


@pytest.mark.parametrize(
    "stale_because, blocked_by, expected",
    [
        (None, None, "supported"),
        ("agent_version", None, "stale"),
        ("built_with_unknown", None, "untested"),
        (None, "tool_discovery", "untested"),
    ],
)
def test_a_case_beside_a_tool_row_s_targets_keeps_what_its_evidence_says(
    stale_because, blocked_by, expected
):
    from simulate.services.harness_scenario_needs import CaseNeeds

    row = {
        "id": "tool_routing",
        "status": "blocked",
        "targets": [{"kind": "tool", "name": REFUND, "result": "failed"}],
        "stale_because": stale_because,
        "blocked_by": blocked_by,
    }
    lookup = CaseNeeds(
        key="status-1",
        number=1,
        name="status-1",
        persona="",
        rows=frozenset({"tool_routing"}),
        tools=frozenset({LOOKUP}),
        unknown=False,
        recorded=False,
        grading_gap=None,
    )

    assert selection._status_for(row, lookup) == expected


def test_each_held_case_carries_its_own_reason_never_the_others_under_its_row():
    from types import SimpleNamespace

    from simulate.services.harness_readiness.selection import (
        NOT_VALIDATED_ROW,
        _held_back,
        _held_list,
        _held_rows,
        _own_reasons,
    )

    def held(key, reason):
        return SimpleNamespace(
            case=SimpleNamespace(key=key, name=""),
            bucket="blocked",
            by=[NOT_VALIDATED_ROW],
            lifecycle_reason=reason,
            hold=SimpleNamespace(held=False, reason=None),
        )

    placement = SimpleNamespace(
        placed=[
            held("refund", "Its checks pass when the agent does nothing."),
            held("late", "Validation did not finish this case; validate again."),
            held("plain", None),
        ],
        dropped=[],
    )

    keys, by, reasons = _held_back(placement, [], [])
    listed = _held_list(keys, {}, _held_rows(placement), _own_reasons(placement))

    assert listed["reason_by_key"] == {
        "refund": "Its checks pass when the agent does nothing.",
        "late": "Validation did not finish this case; validate again.",
    }
    # The row keeps its label and count; each case reads its own reason.
    assert by[NOT_VALIDATED_ROW] == 3
    assert set(listed["by_key"].values()) == {NOT_VALIDATED_ROW}
