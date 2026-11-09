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
    readiness_on as readiness_on,
)
from simulate.tests.test_harness_readiness import (  # noqa: F401 - fixture
    scheduled as scheduled,
)
from simulate.tests.test_harness_readiness import (  # noqa: F401 - fixture
    storage as storage,
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


def _run_metadata(run):
    run.refresh_from_db()
    return run.payload["metadata"]


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
            "by": {"tool_routing": 4},
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
