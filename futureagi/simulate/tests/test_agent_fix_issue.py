"""Run Epoch from an Error Feed issue: the sample of the issue's traces, the job from
them to a running epoch, the review stop, and the links between the issue, its imports
and its epochs.

The chain runs on a real environment with its real suite: the drafts join it with
``add_to_suite``, the baseline is a real selected run, and the epoch starts through the
real ``launch_fix_run``. The builder's drafting turn and Temporal are faked at their
boundary; ClickHouse is a stand-in reader.
"""

from __future__ import annotations

import uuid
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from django.utils import timezone

from accounts.models import Organization, Workspace
from model_hub.models.ai_model import AIModel
from simulate.models import (
    AgentFixIssueJob,
    AgentFixRun,
    EvidenceImport,
    EvidenceImportCase,
    HarnessAgentVersionRuntime,
    HarnessSuiteMember,
    HostedHarnessConversation,
    HostedHarnessJob,
)
from simulate.pydantic_schemas.agent_fix import VARIATIONS_PER_CASE
from simulate.pydantic_schemas.agent_fix_issue import (
    SAMPLE_ALTERNATES,
    SAMPLE_PICKS,
    SAMPLE_POOL,
    IssueJobPhase,
    IssueLaunch,
)
from simulate.selectors import agent_fix_issue as reads
from simulate.selectors.agent_fix import launch_blocker, variations_blocker
from simulate.services import agent_fix_issue as jobs
from simulate.services import harness_usage
from simulate.services.evidence_drafts import _proposal, scenario_key_for
from simulate.services.harness_versioning import archives
from simulate.services.harness_versioning.versions import suite_documents
from simulate.services.hosted_harness import HostedHarnessError
from simulate.services.hosted_harness_conversation import ensure_conversation
from simulate.tests.test_agent_fix_api import BASE as RUNS
from simulate.tests.test_agent_fix_api import _out_of_credits
from simulate.tests.test_agent_fix_api import entitled as entitled
from simulate.tests.test_agent_fix_services import (
    KEYS,
    _fix_run,
    _receipts,
    _run,
)
from simulate.tests.test_agent_fix_services import (
    any_suite_may_launch as any_suite_may_launch,
)
from simulate.tests.test_agent_fix_services import (  # noqa: F401 - fixtures
    environment as environment,
)
from simulate.tests.test_agent_fix_services import temporal as temporal
from simulate.tests.test_agent_fix_services import versioning_on as versioning_on
from simulate.tests.test_harness_amend_archive import NAMES
from simulate.tests.test_harness_versioning_backfill import (  # noqa: F401 - fixture
    storage as storage,
)
from simulate.tests.test_harness_versioning_record import _authoring
from tracer.models.project import Project
from tracer.models.trace_error_analysis import ErrorClusterTraces, TraceErrorGroup
from tracer.services.clickhouse.v2.span_reader import TraceUnit
from tracer.tests.test_observe_evidence import _root

pytestmark = pytest.mark.django_db

ISSUES = "/simulate/api/agent-fix-issues"
T0 = datetime(2026, 11, 10, 9, 0, tzinfo=UTC)
LAUNCH = IssueLaunch(surfaces=["prompt"], budget_credits=500, max_minutes=30)


@pytest.fixture(autouse=True)
def issues_entitled():
    with patch("simulate.views.agent_fix_issue.check_ee_feature") as gate:
        yield gate


@pytest.fixture(autouse=True)
def boundaries():
    """The builder's drafting turn, the baseline's dispatch and the job's workflow."""
    service = "simulate.services.agent_fix_issue"
    with (
        patch(f"{service}.start_drafting") as drafting,
        patch(f"{service}.dispatch_run") as dispatch,
        patch(f"{service}.start_agent_fix_issue_workflow") as workflow,
        patch(f"{service}.cancel_hosted_harness_gateway_workflow") as cancel,
    ):
        yield MagicMock(
            drafting=drafting, dispatch=dispatch, workflow=workflow, cancel=cancel
        )


@pytest.fixture
def project(environment):
    return Project.objects.create(
        name="Support agent",
        organization_id=environment.organization_id,
        workspace_id=environment.workspace_id,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type="observe",
    )


def _issue(project, cluster_id="S-REFUND-1", **fields):
    return TraceErrorGroup.objects.create(
        project=project,
        cluster_id=cluster_id,
        error_type="investigation_finding",
        title="Refund quoted twice",
        **fields,
    )


@pytest.fixture
def issue(project):
    return _issue(project)


def _members(issue, count=None, *, traces=None, sessions=False):
    """Members of ``issue``, the newest first in the order given."""
    ids = traces or [str(uuid.uuid4()) for _ in range(count)]
    column = "trace_session_id" if sessions else "trace_id"
    ErrorClusterTraces.objects.bulk_create(
        [ErrorClusterTraces(cluster=issue, **{column: one}) for one in ids]
    )
    for age, one in enumerate(ids):
        ErrorClusterTraces.objects.filter(cluster=issue, **{column: one}).update(
            created_at=T0 - timedelta(minutes=age)
        )
    return ids


def _roots(by_trace, *, said=None):
    """A stand-in ClickHouse reader whose roots are ``{trace: (type, session)}``: the
    sample reads their units, and an import reads each one as a trace with an
    exchange of its own, or the same ``said`` for every trace."""
    reader = MagicMock()
    reader.__enter__ = MagicMock(return_value=reader)
    reader.__exit__ = MagicMock(return_value=False)
    reader.trace_units.side_effect = lambda ids, project_id: {
        one: TraceUnit(
            observation_type=by_trace[one][0],
            session_id=by_trace[one][1],
            start_time=T0,
        )
        for one in ids
        if one in by_trace
    }
    reader.roots_by_trace_ids.side_effect = lambda ids, **_: [
        _root(
            one,
            kind=by_trace[one][0],
            input=said or f"Refund order {one}",
            output="Refunded twice.",
        )
        for one in ids
        if one in by_trace
    ]
    reader.activity_by_trace_ids.return_value = []
    reader.trace_ids_for_span_ids.return_value = {}
    return patch("tracer.services.clickhouse.v2.get_reader", return_value=reader)


def _sdk(traces):
    return dict.fromkeys(traces, ("chain", None))


def _import(
    environment, issue, source_ids, *, project=None, kind="trace", status="pending"
):
    """An Observe import of ``environment``; one of ``issue`` unless it is None."""
    made = EvidenceImport.objects.create(
        organization_id=environment.organization_id,
        workspace_id=environment.workspace_id,
        environment=environment,
        project=project or issue.project,
        source_issue=issue,
        source_kind=kind,
    )
    EvidenceImportCase.objects.bulk_create(
        [
            EvidenceImportCase(
                evidence_import=made, position=n, source_id=one, status=status
            )
            for n, one in enumerate(source_ids)
        ]
    )
    return made


def _drafted(environment, case):
    """The builder's draft of ``case``, kept as ``_settle`` keeps it: its scenario
    folder under the case's own key, made on the environment as it is."""
    key = scenario_key_for(case)
    suite = archives.split_authoring(
        archives.read_archive(_authoring([*NAMES[:2], key]))
    ).suite
    document = next(one for one in suite_documents(suite) if one.get("name") == key)
    proposal = _proposal(suite, document, key)
    object_key = archives.suite_key(
        environment.organization_id, environment.id, proposal
    )
    archives.put_archive(object_key, proposal)
    EvidenceImportCase.objects.filter(id=case.id).update(
        status="drafted",
        scenario_key=key,
        scenario_name=key,
        proposal_object_key=object_key,
        environment_hash=environment.environment_version.hash,
    )
    case.refresh_from_db()
    return case


def _job(environment, evidence_import, **fields):
    return AgentFixIssueJob.no_workspace_objects.create(
        organization_id=environment.organization_id,
        workspace_id=environment.workspace_id,
        environment=environment,
        evidence_import=evidence_import,
        **fields,
    )


def _mine(environment):
    """The caller's environments: the one it works in."""
    return HostedHarnessJob.no_workspace_objects.filter(id=environment.id)


def _kept(job):
    """Every case of the job's import, as Run Epoch keeps them."""
    return [str(one) for one in job.evidence_import.cases.values_list("id", flat=True)]


def _in_review(environment, issue, user, *, drafts=1):
    """A job whose cases the builder drafted, waiting for review; the first case."""
    made = _import(environment, issue, [str(uuid.uuid4()) for _ in range(drafts)])
    cases = [_drafted(environment, case) for case in made.cases.order_by("position")]
    return _job(environment, made, created_by=user), cases[0]


class TestSample:
    def test_a_large_issue_gives_twelve_picks_from_a_bounded_read(
        self, issue, environment, django_assert_max_num_queries
    ):
        traces = _members(issue, 5000)

        with _roots(_sdk(traces)) as reader, django_assert_max_num_queries(6):
            sample = reads.issue_sample(issue, environment)

        assert (len(sample.picks), len(sample.alternates)) == (
            SAMPLE_PICKS,
            SAMPLE_ALTERNATES,
        )
        # The newest members, each read once: never all 5,000.
        [asked] = [
            call.args[0] for call in reader.return_value.trace_units.call_args_list
        ]
        assert asked == traces[:SAMPLE_POOL]
        assert [one.member_id for one in sample.picks] == traces[:SAMPLE_PICKS]
        assert {(one.unit, one.reason) for one in sample.picks} == {("trace", "recent")}

    def test_the_same_issue_state_gives_the_same_picks(self, issue, environment):
        traces = _members(issue, 40)

        with _roots(_sdk(traces)):
            first = reads.issue_sample(issue, environment)
            second = reads.issue_sample(issue, environment)

        assert first == second

    def test_one_pick_per_session_and_one_unit(self, issue, environment):
        a, a_again, call, b = _members(issue, 4)
        session_a, session_b = str(uuid.uuid4()), str(uuid.uuid4())

        with _roots(
            {
                a: ("chain", session_a),
                a_again: ("chain", session_a),
                call: ("conversation", None),
                b: ("chain", session_b),
            }
        ):
            sample = reads.issue_sample(issue, environment)

        assert sample.unit == "session"
        assert [(one.member_id, one.source_id) for one in sample.picks] == [
            (a, session_a),
            (b, session_b),
        ]

    def test_traces_the_root_cause_analysis_cited_come_first(self, issue, environment):
        newest, *rest, oldest = _members(issue, 5)
        TraceErrorGroup.objects.filter(id=issue.id).update(
            rca_evidence_trace_ids=[oldest, str(uuid.uuid4())]
        )
        issue.refresh_from_db()

        with _roots(_sdk([newest, *rest, oldest])):
            sample = reads.issue_sample(issue, environment)

        assert [(one.member_id, one.reason) for one in sample.picks[:2]] == [
            (oldest, "cited"),
            (newest, "recent"),
        ]

    def test_a_source_another_import_still_reviews_is_left_out(
        self, issue, environment
    ):
        kept, reviewed = _members(issue, 2)
        _import(environment, None, [reviewed], project=issue.project)

        with _roots(_sdk([kept, reviewed])):
            sample = reads.issue_sample(issue, environment)

        assert [one.member_id for one in sample.picks] == [kept]

    def test_an_issue_of_sessions_picks_its_sessions_without_a_read(
        self, project, environment
    ):
        issue = _issue(project, "E-SESSIONS-1", eval_target_type="session")
        sessions = _members(issue, 3, sessions=True)

        with _roots({}) as reader:
            sample = reads.issue_sample(issue, environment)

        assert [(one.unit, one.source_id) for one in sample.picks] == [
            ("session", one) for one in sessions
        ]
        reader.return_value.trace_units.assert_not_called()


class TestPhase:
    def test_the_phase_comes_from_what_the_job_holds(
        self, environment, issue, source_run
    ):
        made = _import(environment, issue, ["t1", "t2"])
        job = _job(environment, made)
        assert reads.issue_job_phase(job) == IssueJobPhase.BLOCKED

        EvidenceImport.objects.filter(id=made.id).update(drafting_job=source_run)
        job.evidence_import.refresh_from_db()
        assert reads.issue_job_phase(job) == IssueJobPhase.DRAFTING

        EvidenceImport.objects.filter(id=made.id).update(drafting_job=None)
        job.evidence_import.refresh_from_db()
        EvidenceImportCase.objects.filter(evidence_import=made, source_id="t1").update(
            status="drafted"
        )
        assert reads.issue_job_phase(job) == IssueJobPhase.REVIEW

        job.baseline_run = source_run
        assert reads.issue_job_phase(job) == IssueJobPhase.MEASURING

        job.fix_run = _fix_run(source_run)
        assert reads.issue_job_phase(job) == IssueJobPhase.LAUNCHED

        job.ending = {"code": "stopped", "message": "Stopped"}
        assert reads.issue_job_phase(job) == IssueJobPhase.ENDED

    def test_a_repeat_of_a_scenario_not_added_yet_cannot_run(self, environment, issue):
        elsewhere = _import(
            environment, None, ["t1"], project=issue.project, status="drafted"
        )
        made = _import(environment, issue, ["t1"], status="duplicate")
        EvidenceImportCase.objects.filter(evidence_import=made).update(
            duplicate_of=elsewhere.cases.get()
        )
        job = _job(environment, made)
        assert reads.issue_job_phase(job) == IssueJobPhase.BLOCKED

        elsewhere.cases.update(status="added")
        assert reads.issue_job_phase(job) == IssueJobPhase.REVIEW


class TestLinks:
    def test_a_retried_epoch_keeps_its_issue(self, environment, issue, source_run):
        first = _fix_run(source_run, state=AgentFixRun.State.FAILED)
        retry = _fix_run(source_run, retried_from=first)
        job = _job(environment, _import(environment, issue, ["t1"]), fix_run=first)

        assert reads.job_epoch(job) == retry
        assert reads.jobs_of_epochs([retry]) == {retry.id: job}

    def test_a_merged_issue_shows_on_the_issue_it_was_merged_into(
        self, environment, issue, project
    ):
        into = _issue(project, "S-REFUND-2")
        job = _job(environment, _import(environment, issue, ["t1"]))
        TraceErrorGroup.all_objects.filter(id=issue.id).update(
            deleted=True, redirect_to=into
        )
        issue.refresh_from_db()

        assert reads.jobs_of_issues([into]) == {into.id: [job]}
        assert reads.live_issue(issue) == into

    def test_a_deleted_environment_holds_its_issue_no_more(
        self, environment, issue, user
    ):
        _in_review(environment, issue, user)
        HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
            deleted=True
        )

        assert reads.jobs_of_issues([issue]) == {}

    def test_the_limit_counts_only_what_the_suite_still_holds(self, environment, issue):
        made = _import(environment, issue, ["t1", "t2"], status="added")
        EvidenceImportCase.objects.filter(evidence_import=made, source_id="t1").update(
            scenario_key=KEYS[0]
        )
        EvidenceImportCase.objects.filter(evidence_import=made, source_id="t2").update(
            scenario_key="removed-since"
        )

        assert reads.issue_scenarios(environment) == {KEYS[0]: issue}


class TestCreate:
    def test_the_picks_are_imported_and_drafted(
        self, environment, issue, user, boundaries
    ):
        traces = _members(issue, 3)

        with _roots(_sdk(traces)):
            job = jobs.create_issue_job(
                issue,
                environment=environment,
                member_ids=traces[:2],
                user=user,
                endpoint_base_url="https://platform.test",
                environments=_mine(environment),
            )

        made = job.evidence_import
        assert (made.source_issue_id, made.source_kind) == (issue.id, "trace")
        assert list(made.cases.values_list("source_id", "status")) == [
            (traces[0], "pending"),
            (traces[1], "pending"),
        ]
        assert [one["reason"] for one in job.sample] == ["recent", "recent"]
        (drafted, pending), kwargs = boundaries.drafting.call_args
        assert drafted == made
        assert set(pending) == {str(one.id) for one in made.cases.all()}
        assert set(kwargs["shows"].values()) == {"failure"}

    def test_two_picks_of_one_conversation_are_drafted_once(
        self, environment, issue, user, boundaries
    ):
        traces = _members(issue, 2)

        with _roots(_sdk(traces), said="Refund order 42"):
            job = jobs.create_issue_job(
                issue,
                environment=environment,
                member_ids=traces,
                user=user,
                endpoint_base_url="https://platform.test",
                environments=_mine(environment),
            )

        first, repeat = job.evidence_import.cases.order_by("position")
        assert (first.status, repeat.status) == ("pending", "duplicate")
        assert repeat.duplicate_of_id == first.id
        (_made, pending), _kwargs = boundaries.drafting.call_args
        assert pending == [str(first.id)]

    def test_an_issue_with_an_open_job_opens_it_instead(self, environment, issue, user):
        traces = _members(issue, 2)
        open_job, _case = _in_review(environment, issue, user)

        with _roots(_sdk(traces)), pytest.raises(HostedHarnessError) as refused:
            jobs.create_issue_job(
                issue,
                environment=environment,
                member_ids=traces,
                user=user,
                endpoint_base_url="https://platform.test",
                environments=_mine(environment),
            )

        assert refused.value.code == "issue_epoch_open"
        assert refused.value.details == {"job_id": str(open_job.id)}

    def test_an_open_job_the_caller_cannot_open_is_not_named(
        self, environment, issue, user
    ):
        traces = _members(issue, 2)
        _in_review(environment, issue, user)

        with _roots(_sdk(traces)), pytest.raises(HostedHarnessError) as refused:
            jobs.create_issue_job(
                issue,
                environment=environment,
                member_ids=traces,
                user=user,
                endpoint_base_url="https://platform.test",
                environments=HostedHarnessJob.no_workspace_objects.none(),
            )

        assert refused.value.code == "issue_epoch_open"
        assert refused.value.message == reads.OPEN_ELSEWHERE
        assert not refused.value.details

    def test_a_pick_another_import_reviews_is_named_and_nothing_is_kept(
        self, environment, issue, user, boundaries
    ):
        kept, reviewed = _members(issue, 2)
        _import(environment, None, [reviewed], project=issue.project)
        before = EvidenceImport.objects.count()

        with (
            _roots(_sdk([kept, reviewed])),
            pytest.raises(HostedHarnessError) as refused,
        ):
            jobs.create_issue_job(
                issue,
                environment=environment,
                member_ids=[kept, reviewed],
                user=user,
                endpoint_base_url="https://platform.test",
                environments=_mine(environment),
            )

        assert refused.value.code == "issue_epoch_picks_in_review"
        assert refused.value.details == {"member_ids": [reviewed]}
        assert EvidenceImport.objects.count() == before
        assert not AgentFixIssueJob.no_workspace_objects.exists()
        boundaries.drafting.assert_not_called()

    def test_picks_of_two_kinds_or_outside_the_issue_are_refused(
        self, environment, issue, user
    ):
        sdk, call = _members(issue, 2)
        stranger = str(uuid.uuid4())

        with _roots({sdk: ("chain", None), call: ("conversation", None)}):
            for picks, code in (
                ([sdk, call], "issue_epoch_mixed_units"),
                ([sdk, stranger], "issue_epoch_not_member"),
            ):
                with pytest.raises(HostedHarnessError) as refused:
                    jobs.create_issue_job(
                        issue,
                        environment=environment,
                        member_ids=picks,
                        user=user,
                        endpoint_base_url="https://platform.test",
                        environments=_mine(environment),
                    )
                assert refused.value.code == code


class TestRunEpoch:
    def test_the_drafts_join_the_suite_and_the_epoch_starts_from_the_baseline(
        self,
        environment,
        issue,
        user,
        boundaries,
        temporal,
        django_capture_on_commit_callbacks,
    ):
        job, case = _in_review(environment, issue, user)

        with django_capture_on_commit_callbacks(execute=True):
            jobs.run_issue_job(job, case_ids=[str(case.id)], launch=LAUNCH, user=user)

        job.refresh_from_db()
        environment.refresh_from_db()
        held = set(
            HarnessSuiteMember.no_workspace_objects.filter(
                suite_version_id=environment.suite_version_id
            ).values_list("scenario_key", flat=True)
        )
        assert case.scenario_key in held
        assert EvidenceImportCase.objects.get(id=case.id).status == "added"
        baseline = job.baseline_run
        assert baseline.suite_version_id == environment.suite_version_id
        assert reads.issue_job_phase(job) == IssueJobPhase.MEASURING
        boundaries.dispatch.assert_called_once()
        boundaries.workflow.assert_called_once_with(str(job.id))

        _receipts(baseline, {case.scenario_key: {"refunded": False}})
        HostedHarnessJob.no_workspace_objects.filter(id=baseline.id).update(
            state="completed"
        )
        with django_capture_on_commit_callbacks(execute=True):
            assert jobs.advance_issue_job(str(job.id)) is True

        job.refresh_from_db()
        epoch = job.fix_run
        assert epoch.source_run_id == baseline.id
        assert epoch.request["target_scenario_keys"] == [case.scenario_key]
        assert [one["import_id"] for one in epoch.request["evidence"]] == [
            str(job.evidence_import_id)
        ]
        temporal.start.assert_called_once_with(str(epoch.id))

    def test_the_drafts_not_kept_are_discarded(
        self, environment, issue, user, django_capture_on_commit_callbacks
    ):
        job, kept = _in_review(environment, issue, user, drafts=2)

        with django_capture_on_commit_callbacks(execute=True):
            jobs.run_issue_job(job, case_ids=[str(kept.id)], launch=LAUNCH, user=user)

        assert dict(job.evidence_import.cases.values_list("id", "status")) == {
            kept.id: "added",
            job.evidence_import.cases.exclude(id=kept.id).get().id: "discarded",
        }

    def test_an_environment_still_building_refuses_the_run_before_the_baseline(
        self, environment, issue, user
    ):
        job, _case = _in_review(environment, issue, user)
        HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
            state="running", environment_version=None
        )

        with pytest.raises(HostedHarnessError) as refused:
            jobs.run_issue_job(job, case_ids=_kept(job), launch=LAUNCH, user=user)

        assert refused.value.code == "environment_not_built"
        job.refresh_from_db()
        assert job.baseline_run_id is None

    def test_variations_are_refused_while_the_environment_is_still_building(
        self, environment, source_run
    ):
        HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
            state="running", environment_version=None
        )
        source_run.refresh_from_db()

        refused = variations_blocker(source_run)

        assert refused is not None and refused.code == "environment_not_built"

    # With the floor at 2 and both drafts kept, the epoch writes variations only if
    # one passes, which the baseline has not shown yet: its checks run first anyway.
    def test_a_builder_that_cannot_write_variations_is_named_before_the_baseline(
        self, environment, issue, user, settings
    ):
        settings.AGENT_FIX_MIN_FAILING_CASES = 2
        job, _case = _in_review(environment, issue, user, drafts=2)
        HostedHarnessConversation.no_workspace_objects.filter(
            id=ensure_conversation(environment).id
        ).update(state="retired")
        suite = environment.suite_version_id

        with pytest.raises(HostedHarnessError) as refused:
            jobs.run_issue_job(job, case_ids=_kept(job), launch=LAUNCH, user=user)

        assert refused.value.code == "conversation_workspace_not_ready"
        environment.refresh_from_db()
        assert environment.suite_version_id == suite
        job.refresh_from_db()
        assert job.baseline_run_id is None

    def test_the_variations_room_is_checked_on_the_most_that_can_fail(
        self, environment, issue, user, settings
    ):
        settings.AGENT_FIX_MIN_FAILING_CASES = 2
        job, _case = _in_review(environment, issue, user, drafts=2)
        held = HarnessSuiteMember.no_workspace_objects.filter(
            suite_version_id=environment.suite_version_id
        ).count()

        # Room for the two drafts, not for variations of one of them.
        with (
            patch(
                "simulate.services.agent_fix_runs.CHAT_CHECKPOINT_MAX_SCENARIOS",
                held + 2 + VARIATIONS_PER_CASE - 1,
            ),
            pytest.raises(HostedHarnessError) as refused,
        ):
            jobs.run_issue_job(job, case_ids=_kept(job), launch=LAUNCH, user=user)

        assert refused.value.code == "scenario_limit_exceeded"
        job.refresh_from_db()
        assert job.baseline_run_id is None

    def test_the_time_limit_counts_the_variations_there_can_be(
        self, environment, issue, user, settings, django_capture_on_commit_callbacks
    ):
        settings.AGENT_FIX_MIN_FAILING_CASES = 2
        job, _case = _in_review(environment, issue, user, drafts=2)

        with (
            patch(
                "simulate.services.agent_fix_issue.default_max_minutes",
                side_effect=lambda cases: cases,
            ),
            django_capture_on_commit_callbacks(execute=True),
        ):
            jobs.run_issue_job(
                job,
                case_ids=_kept(job),
                launch=IssueLaunch(surfaces=["prompt"], budget_credits=500),
                user=user,
            )

        job.refresh_from_db()
        environment.refresh_from_db()
        suite = HarnessSuiteMember.no_workspace_objects.filter(
            suite_version_id=environment.suite_version_id
        ).count()
        # One of the two can fail while the other passes: variations of that one.
        assert job.launch["max_minutes"] == suite + VARIATIONS_PER_CASE

    def test_fewer_scenarios_than_an_epoch_starts_from_are_refused_first(
        self, environment, issue, user, settings
    ):
        settings.AGENT_FIX_MIN_FAILING_CASES = 12
        job, case = _in_review(environment, issue, user)
        suite = environment.suite_version_id

        with pytest.raises(HostedHarnessError) as refused:
            jobs.run_issue_job(job, case_ids=[str(case.id)], launch=LAUNCH, user=user)

        assert refused.value.code == "fix_targets_too_few"
        environment.refresh_from_db()
        assert environment.suite_version_id == suite
        job.refresh_from_db()
        assert (job.baseline_run_id, job.ending) == (None, None)

    def test_a_scenario_not_ready_yet_leaves_the_job_to_try_again(
        self, environment, issue, user
    ):
        job, case = _in_review(environment, issue, user)
        suite = environment.suite_version_id

        with (
            patch(
                "simulate.services.agent_fix_issue.create_selected_harness_run",
                side_effect=HostedHarnessError(
                    "scenario_unavailable", "try again in a moment", status_code=409
                ),
            ),
            pytest.raises(HostedHarnessError),
        ):
            jobs.run_issue_job(job, case_ids=[str(case.id)], launch=LAUNCH, user=user)

        job.refresh_from_db()
        environment.refresh_from_db()
        assert job.ending is None
        assert environment.suite_version_id == suite
        assert EvidenceImportCase.objects.get(id=case.id).status == "drafted"

    def test_past_the_limit_nothing_joins_the_suite(
        self, environment, issue, user, settings, boundaries
    ):
        settings.AGENT_FIX_ISSUE_SCENARIOS_MAX = 0
        job, case = _in_review(environment, issue, user)
        suite = environment.suite_version_id

        with pytest.raises(HostedHarnessError) as refused:
            jobs.run_issue_job(job, case_ids=[str(case.id)], launch=LAUNCH, user=user)

        assert refused.value.code == "issue_scenarios_limit"
        environment.refresh_from_db()
        assert environment.suite_version_id == suite
        job.refresh_from_db()
        assert (job.baseline_run_id, job.ending) == (None, None)
        assert EvidenceImportCase.objects.get(id=case.id).status == "drafted"

    def test_a_draft_made_on_an_older_environment_ends_the_job(
        self, environment, issue, user
    ):
        job, case = _in_review(environment, issue, user)
        EvidenceImportCase.objects.filter(id=case.id).update(environment_hash="older")

        with pytest.raises(HostedHarnessError) as refused:
            jobs.run_issue_job(job, case_ids=[str(case.id)], launch=LAUNCH, user=user)

        assert refused.value.code == "evidence_draft_stale"
        job.refresh_from_db()
        assert job.ending["code"] == "evidence_draft_stale"
        assert job.baseline_run_id is None
        # The stale draft can go no further, so it holds back no later import.
        assert EvidenceImportCase.objects.get(id=case.id).status == "discarded"

    def test_an_epoch_running_in_the_environment_is_named(
        self, environment, issue, user, source_run
    ):
        job, case = _in_review(environment, issue, user)
        _fix_run(source_run)

        with pytest.raises(HostedHarnessError) as refused:
            jobs.run_issue_job(job, case_ids=[str(case.id)], launch=LAUNCH, user=user)

        assert refused.value.code == "fix_run_active"
        job.refresh_from_db()
        assert job.ending is None


class TestAdvance:
    def _measuring(self, environment, issue, user, *, state, held):
        job, case = _in_review(environment, issue, user)
        baseline = _run(environment, key=f"baseline-{job.id}")
        _receipts(baseline, dict.fromkeys(KEYS, held))
        HostedHarnessJob.no_workspace_objects.filter(id=baseline.id).update(state=state)
        AgentFixIssueJob.no_workspace_objects.filter(id=job.id).update(
            baseline_run=baseline, launch=LAUNCH.model_dump(mode="json")
        )
        job.refresh_from_db()
        return job

    def test_a_plan_limit_at_launch_ends_the_job_and_frees_the_environment(
        self, environment, issue, user, monkeypatch
    ):
        job = self._measuring(
            environment, issue, user, state="completed", held={"refunded": False}
        )
        monkeypatch.setattr(harness_usage, "is_oss", lambda: False)

        with patch(
            "simulate.services.agent_fix_issue.launch_fix_run",
            side_effect=_out_of_credits(),
        ):
            assert jobs.advance_issue_job(str(job.id)) is True

        job.refresh_from_db()
        assert job.ending["code"] == "usage_limit"
        assert reads.measuring_job(environment) is None

    def test_a_baseline_that_did_not_finish_is_not_could_not_reproduce(
        self, environment, issue, user
    ):
        job = self._measuring(
            environment, issue, user, state="failed", held={"refunded": True}
        )

        assert jobs.advance_issue_job(str(job.id)) is True

        job.refresh_from_db()
        assert job.ending["code"] == "issue_epoch_baseline_failed"

    def test_a_baseline_where_nothing_failed_could_not_reproduce(
        self, environment, issue, user
    ):
        job = self._measuring(
            environment, issue, user, state="completed", held={"refunded": True}
        )

        assert jobs.advance_issue_job(str(job.id)) is True

        job.refresh_from_db()
        assert job.ending["code"] == "fix_targets_passed"
        assert job.fix_run_id is None

    def test_a_launch_that_keeps_failing_ends_on_the_job_s_clock(
        self, environment, issue, user
    ):
        job = self._measuring(
            environment, issue, user, state="completed", held={"refunded": False}
        )
        HostedHarnessJob.no_workspace_objects.filter(id=job.baseline_run_id).update(
            created_at=timezone.now() - timedelta(hours=13)
        )

        with patch(
            "simulate.services.agent_fix_issue.launch_fix_run",
            side_effect=HostedHarnessError(
                "fix_traces_unreadable", "later", retryable=True
            ),
        ):
            assert jobs.advance_issue_job(str(job.id)) is True

        job.refresh_from_db()
        assert job.ending["code"] == "issue_epoch_timed_out"

    def test_a_baseline_still_running_waits(self, environment, issue, user):
        job = self._measuring(
            environment, issue, user, state="running", held={"refunded": False}
        )

        assert jobs.advance_issue_job(str(job.id)) is False

    def test_another_issue_s_baseline_holds_the_environment(
        self, environment, issue, user, source_run
    ):
        # Finished but not launched from yet: the job still holds the environment.
        job = self._measuring(
            environment, issue, user, state="completed", held={"refunded": False}
        )

        refused = launch_blocker(source_run)

        assert refused is not None and refused.code == "issue_epoch_measuring"
        job.refresh_from_db()
        assert launch_blocker(job.baseline_run) is None


class TestStop:
    def test_before_run_epoch_the_import_is_discarded(self, environment, issue, user):
        job, _case = _in_review(environment, issue, user)

        jobs.stop_issue_job(job)

        job.refresh_from_db()
        assert job.ending == {"code": "stopped", "message": "Stopped"}
        assert EvidenceImport.all_objects.get(id=job.evidence_import_id).deleted

    def test_a_stop_that_fails_part_way_can_be_asked_again(
        self, environment, issue, user
    ):
        job, _case = _in_review(environment, issue, user)

        with (
            patch(
                "simulate.services.agent_fix_issue.discard_evidence_import",
                side_effect=RuntimeError("storage blip"),
            ),
            pytest.raises(RuntimeError),
        ):
            jobs.stop_issue_job(job)

        job.refresh_from_db()
        assert job.ending is None
        jobs.stop_issue_job(job)
        job.refresh_from_db()
        assert job.ending["code"] == "stopped"

    def test_a_measuring_baseline_is_canceled(
        self, environment, issue, user, boundaries
    ):
        job, _case = _in_review(environment, issue, user)
        baseline = _run(environment, key="baseline-stop", state="running")
        AgentFixIssueJob.no_workspace_objects.filter(id=job.id).update(
            baseline_run=baseline
        )
        job.refresh_from_db()

        jobs.stop_issue_job(job)

        baseline.refresh_from_db()
        assert baseline.cancel_reason == "user_canceled"
        boundaries.cancel.assert_called_once_with(str(baseline.id))

    def test_a_baseline_its_workflow_cannot_be_told_of_is_canceled_at_the_gateway(
        self, environment, issue, user, boundaries
    ):
        job, _case = _in_review(environment, issue, user)
        baseline = _run(environment, key="baseline-unreachable", state="running")
        AgentFixIssueJob.no_workspace_objects.filter(id=job.id).update(
            baseline_run=baseline
        )
        job.refresh_from_db()
        boundaries.cancel.side_effect = RuntimeError("temporal is unreachable")

        with patch("simulate.services.agent_fix_issue.HostedHarnessGateway") as gateway:
            jobs.stop_issue_job(job)

        (canceled,) = gateway.return_value.cancel.call_args.args
        assert canceled.id == baseline.id
        assert gateway.return_value.cancel.call_args.kwargs == {
            "reason": "user_canceled"
        }
        job.refresh_from_db()
        assert job.ending["code"] == "stopped"

    def test_a_stop_whose_cancels_both_fail_still_ends_the_job(
        self, environment, issue, user, boundaries
    ):
        job, _case = _in_review(environment, issue, user)
        baseline = _run(environment, key="baseline-both-fail", state="running")
        AgentFixIssueJob.no_workspace_objects.filter(id=job.id).update(
            baseline_run=baseline
        )
        job.refresh_from_db()
        boundaries.cancel.side_effect = RuntimeError("temporal is unreachable")

        with patch("simulate.services.agent_fix_issue.HostedHarnessGateway") as gateway:
            gateway.return_value.cancel.side_effect = RuntimeError("gateway is down")
            stopped = jobs.stop_issue_job(job)

        assert stopped.ending["code"] == "stopped"
        gateway.return_value.cancel.assert_called_once()

    def test_stopping_again_cancels_a_baseline_the_first_stop_left_running(
        self, environment, issue, user, boundaries
    ):
        job, _case = _in_review(environment, issue, user)
        baseline = _run(environment, key="baseline-again", state="running")
        AgentFixIssueJob.no_workspace_objects.filter(id=job.id).update(
            baseline_run=baseline, ending=jobs.STOPPED.model_dump()
        )
        job.refresh_from_db()

        jobs.stop_issue_job(job)

        baseline.refresh_from_db()
        assert baseline.cancel_reason == "user_canceled"
        boundaries.cancel.assert_called_once_with(str(baseline.id))

    def test_a_started_epoch_stops_on_its_own_page(
        self, environment, issue, user, source_run
    ):
        job, _case = _in_review(environment, issue, user)
        AgentFixIssueJob.no_workspace_objects.filter(id=job.id).update(
            fix_run=_fix_run(source_run)
        )
        job.refresh_from_db()

        with pytest.raises(HostedHarnessError) as refused:
            jobs.stop_issue_job(job)

        assert refused.value.code == "issue_epoch_launched"


class TestApi:
    def test_the_first_plan_names_no_environment(self, auth_client, environment, issue):
        traces = _members(issue, 3)

        with _roots(_sdk(traces)):
            response = auth_client.get(
                f"{ISSUES}/plan/",
                {"cluster_id": issue.cluster_id, "project_id": str(issue.project_id)},
            )

        assert response.status_code == 200, response.content
        body = response.json()
        assert body["environment"] is None and body["ship"] is None
        # One of the caller's environments can run it: the person picks it.
        assert body["blocked"]["code"] == "environment_not_picked"
        assert [one["member_id"] for one in body["sample"]["picks"]] == traces
        assert body["defaults"]["budget_credits"] > 0

    def test_a_plan_with_no_environment_to_run_on_says_build_one(
        self, auth_client, environment, issue
    ):
        traces = _members(issue, 3)
        HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
            deleted=True
        )

        with _roots(_sdk(traces)):
            body = auth_client.get(
                f"{ISSUES}/plan/",
                {"cluster_id": issue.cluster_id, "project_id": str(issue.project_id)},
            ).json()

        assert body["environment"] is None
        assert body["blocked"]["code"] == "environment_none"

    @pytest.mark.parametrize(
        ("built", "state", "extending", "rebuilding", "code"),
        [
            (False, "running", False, False, "environment_not_built"),
            (False, "failed", False, False, "environment_build_failed"),
            (False, "canceled", False, False, "environment_build_failed"),
            (True, "failed", False, False, "environment_update_failed"),
            # More scenarios run the environment again: its state says so.
            (True, "queued", True, False, "environment_updating"),
            (True, "completed", False, True, "environment_updating"),
            # A settled environment's leftover extend marker holds nothing up.
            (True, "completed", True, False, None),
            (True, "completed", False, False, None),
        ],
    )
    def test_the_plan_says_why_the_environment_cannot_take_an_epoch_yet(
        self,
        auth_client,
        environment,
        issue,
        built,
        state,
        extending,
        rebuilding,
        code,
    ):
        traces = _members(issue, 3)
        meta = {"scenario_extend": {"target_count": 9}} if extending else {}
        HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
            state=state,
            payload={
                **environment.payload,
                "metadata": {**(environment.payload.get("metadata") or {}), **meta},
            },
            **({} if built else {"environment_version": None}),
        )
        refuse = patch(
            "simulate.services.harness_versioning.drafts.require_no_rebuild",
            side_effect=HostedHarnessError("rebuild_running", "busy", status_code=409),
        )

        with _roots(_sdk(traces)), refuse if rebuilding else nullcontext():
            body = auth_client.get(
                f"{ISSUES}/plan/",
                {
                    "cluster_id": issue.cluster_id,
                    "project_id": str(issue.project_id),
                    "environment_id": str(environment.id),
                },
            ).json()

        assert (body["blocked"] or {}).get("code") == code

    def test_the_plan_says_how_the_winner_ships_before_any_run(
        self, auth_client, environment, issue
    ):
        traces = _members(issue, 3)
        runtime = HarnessAgentVersionRuntime.no_workspace_objects.get(
            agent_version_id=environment.agent_version_id
        )
        HarnessAgentVersionRuntime.no_workspace_objects.filter(id=runtime.id).update(
            source_kind="github",
            source={"repository": "acme/agent", "ref": "refs/heads/main"},
            agent_payload={"source_access": {"installation_id": "42"}},
        )

        with _roots(_sdk(traces)):
            body = auth_client.get(
                f"{ISSUES}/plan/",
                {
                    "cluster_id": issue.cluster_id,
                    "project_id": str(issue.project_id),
                    "environment_id": str(environment.id),
                },
            ).json()

        # No run has checked a commit out yet, so none is asked for.
        assert body["ship"] == {"path": "pull_request", "blocked": None}

    def _plan(self, auth_client, issue, environment=None):
        traces = _members(issue, 3)
        query = {"cluster_id": issue.cluster_id, "project_id": str(issue.project_id)}
        if environment is not None:
            query["environment_id"] = str(environment.id)
        with _roots(_sdk(traces)):
            response = auth_client.get(f"{ISSUES}/plan/", query)
        assert response.status_code == 200, response.content
        return response.json()

    def _pickable(self, auth_client, environment):
        """The environment's row on the environments list the picker reads."""
        response = auth_client.get("/simulate/api/harness-environments/")
        assert response.status_code == 200, response.content
        rows = {row["id"]: row for row in response.json()["results"]}
        return rows[str(environment.id)]["epoch_pickable"]

    @staticmethod
    def _source(environment, kind, **columns):
        HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
            payload={**environment.payload, "source": {"kind": kind}}, **columns
        )
        environment.refresh_from_db()

    @pytest.mark.parametrize("kind", ["github", "archive"])
    def test_an_environment_made_before_agent_versions_is_listed_and_says_what_to_do(
        self, auth_client, environment, issue, kind
    ):
        # Completed with no versions at all, as one made before they were kept.
        self._source(environment, kind, agent_version=None, environment_version=None)

        assert self._pickable(auth_client, environment) is True
        body = self._plan(auth_client, issue, environment)

        assert body["blocked"]["code"] == "environment_agent_unversioned"
        assert body["ship"] is None
        # With none picked, it is one the person can pick.
        unpicked = self._plan(auth_client, issue)
        assert unpicked["blocked"]["code"] == "environment_not_picked"

    def test_an_only_environment_still_building_is_picked_then_said_to_be_building(
        self, auth_client, environment, issue
    ):
        self._source(
            environment,
            "github",
            state="running",
            agent_version=None,
            environment_version=None,
        )

        assert self._pickable(auth_client, environment) is True
        assert self._plan(auth_client, issue)["blocked"]["code"] == (
            "environment_not_picked"
        )
        picked = self._plan(auth_client, issue, environment)
        assert picked["blocked"]["code"] == "environment_not_built"

    @pytest.mark.parametrize("kind", ["provider", "remote"])
    def test_an_agent_epoch_cannot_change_is_not_listed_and_ships_nothing(
        self, auth_client, environment, issue, kind
    ):
        HarnessAgentVersionRuntime.no_workspace_objects.filter(
            agent_version_id=environment.agent_version_id
        ).update(source_kind="provider")
        self._source(environment, kind)

        assert self._pickable(auth_client, environment) is False
        picked = self._plan(auth_client, issue, environment)
        assert picked["blocked"]["code"] == "fix_agent_not_editable"
        assert picked["ship"] is None
        assert self._plan(auth_client, issue)["blocked"]["code"] == "environment_none"

    def test_the_status_read_spans_projects(
        self, auth_client, environment, issue, user
    ):
        job, _case = _in_review(environment, issue, user)
        other = Project.objects.create(
            name="Billing agent",
            organization_id=environment.organization_id,
            workspace_id=environment.workspace_id,
            model_type=AIModel.ModelTypes.GENERATIVE_LLM,
            trace_type="observe",
        )
        _issue(other, "S-OTHER-1")

        response = auth_client.get(
            f"{ISSUES}/", {"cluster_ids": f"{issue.cluster_id},S-OTHER-1"}
        )

        assert response.status_code == 200, response.content
        [result] = response.json()["results"]
        assert (result["cluster_id"], result["project_id"]) == (
            issue.cluster_id,
            str(issue.project_id),
        )
        assert (result["job"]["id"], result["job"]["phase"]) == (str(job.id), "review")

    def test_the_detail_carries_the_review_and_the_form_s_defaults(
        self, auth_client, environment, issue, user
    ):
        job, case = _in_review(environment, issue, user)

        response = auth_client.get(f"{ISSUES}/{job.id}/")

        assert response.status_code == 200, response.content
        body = response.json()
        assert body["phase"] == "review"
        assert [one["id"] for one in body["evidence_import"]["cases"]] == [str(case.id)]
        assert body["defaults"]["surfaces"]
        assert body["ending"] is None and body["epoch"] is None

    def test_an_epoch_names_the_issue_it_came_from(
        self, auth_client, environment, issue, source_run
    ):
        epoch = _fix_run(source_run)
        job = _job(environment, _import(environment, issue, ["t1"]), fix_run=epoch)

        response = auth_client.get(f"{RUNS}/{epoch.id}/")

        assert response.status_code == 200, response.content
        assert response.json()["issue"] == {
            "job_id": str(job.id),
            "cluster_id": issue.cluster_id,
            "project_id": str(issue.project_id),
        }

    def test_the_environment_page_leaves_an_issue_import_alone(
        self, auth_client, environment, issue, user
    ):
        job, case = _in_review(environment, issue, user)
        base = f"/simulate/api/harness-environments/{environment.id}/evidence-imports"

        listed = auth_client.get(f"{base}/")
        discarded = auth_client.delete(f"{base}/{job.evidence_import_id}/")

        assert listed.status_code == 200, listed.content
        assert str(job.evidence_import_id) not in {one["id"] for one in listed.json()}
        assert discarded.status_code == 409, discarded.content
        assert discarded.json()["error"] == "evidence_import_of_issue"

    def test_the_suite_names_the_issue_a_scenario_came_from(
        self, auth_client, environment, issue
    ):
        made = _import(environment, issue, ["t1"], status="added")
        made.cases.update(scenario_key=KEYS[0])

        response = auth_client.get(
            f"/simulate/api/harness-environments/{environment.id}/versions/suite/"
            f"{environment.suite_version_id}/members/"
        )

        assert response.status_code == 200, response.content
        by_key = {one["scenario_key"]: one for one in response.json()}
        assert by_key[KEYS[0]]["issue_cluster_id"] == issue.cluster_id
        assert by_key[KEYS[1]]["issue_cluster_id"] is None

    @pytest.mark.parametrize("elsewhere", ["workspace", "organization"])
    def test_another_workspace_or_organization_reaches_nothing(
        self, auth_client, environment, issue, user, organization, elsewhere
    ):
        job, case = _in_review(environment, issue, user)
        owner = (
            organization
            if elsewhere == "workspace"
            else Organization.objects.create(name="Other org")
        )
        other = Workspace.no_workspace_objects.create(
            name="Other workspace",
            organization=owner,
            is_default=False,
            is_active=True,
            created_by=user,
        )
        moved = {"organization_id": owner.id, "workspace_id": other.id}
        HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(**moved)
        Project.no_workspace_objects.filter(id=issue.project_id).update(**moved)
        EvidenceImport.no_workspace_objects.filter(id=job.evidence_import_id).update(
            **moved
        )
        AgentFixIssueJob.no_workspace_objects.filter(id=job.id).update(**moved)
        named = {"cluster_id": issue.cluster_id, "project_id": str(issue.project_id)}

        planned = auth_client.get(f"{ISSUES}/plan/", named)
        read = auth_client.get(f"{ISSUES}/{job.id}/")
        ran = auth_client.post(
            f"{ISSUES}/{job.id}/run/",
            {"case_ids": [str(case.id)], "surfaces": ["prompt"], "budget_credits": 500},
            format="json",
        )
        stopped = auth_client.post(f"{ISSUES}/{job.id}/stop/", {}, format="json")
        listed = auth_client.get(f"{ISSUES}/", {"cluster_ids": issue.cluster_id})

        assert [one.status_code for one in (planned, read, ran, stopped)] == [
            404
        ] * 4, [one.content for one in (planned, read, ran, stopped)]
        assert listed.status_code == 200, listed.content
        assert listed.json()["results"] == []
        job.refresh_from_db()
        assert (job.ending, job.baseline_run_id) == (None, None)

    def test_a_caller_with_no_workspace_is_told_of_another_s_epoch_without_its_id(
        self, api_client, environment, issue, user, organization
    ):
        # A caller with no workspace reads every project of its organization, but
        # opens only the environments that have no workspace either.
        job, _case = _in_review(environment, issue, user)
        other = Workspace.no_workspace_objects.create(
            name="Other workspace",
            organization=organization,
            is_default=False,
            is_active=True,
            created_by=user,
        )
        moved = {"workspace_id": other.id}
        HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(**moved)
        Project.no_workspace_objects.filter(id=issue.project_id).update(**moved)
        EvidenceImport.no_workspace_objects.filter(id=job.evidence_import_id).update(
            **moved
        )
        AgentFixIssueJob.no_workspace_objects.filter(id=job.id).update(**moved)
        api_client.force_authenticate(user=user)

        with _roots({}):
            planned = api_client.get(
                f"{ISSUES}/plan/",
                {"cluster_id": issue.cluster_id, "project_id": str(issue.project_id)},
            )
        read = api_client.get(f"{ISSUES}/{job.id}/")

        assert planned.status_code == 200, planned.content
        body = planned.json()
        assert body["open_job_id"] is None
        assert body["blocked"] == {
            "code": "issue_epoch_open",
            "message": reads.OPEN_ELSEWHERE,
        }
        assert read.status_code == 404, read.content


def test_the_pull_request_links_the_issue(environment, issue, source_run, settings):
    from simulate.services.agent_fix_github import _issue_link

    settings.FRONTEND_BASE_URL = "https://app.test"
    epoch = _fix_run(source_run)
    _job(environment, _import(environment, issue, ["t1"]), fix_run=epoch)
    candidate = MagicMock(fix_run=epoch, fix_run_id=epoch.id)

    assert _issue_link(candidate) == (
        f"Run from Error Feed issue [{issue.cluster_id}]"
        f"(https://app.test/dashboard/error-feed/{issue.cluster_id})."
    )


@pytest.fixture
def source_run(environment):
    """A finished run of the environment's two scenarios."""
    run = _run(environment)
    _receipts(run, {KEYS[0]: {"refunded": False}, KEYS[1]: {"refunded": True}})
    return run
