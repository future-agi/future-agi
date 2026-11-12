"""Which run tests, and which calls, count as harness runs.

A hosted harness job owns a run test, or the harness SDK leaves its checks on
the calls under ``call_metadata["harness_evaluations"]``. Either makes a
harness run, whose configs with no mapping of their own are result columns
the harness fills.
"""

import uuid
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from model_hub.models.evals_metric import EvalTemplate
from simulate.models import (
    AgentDefinition,
    CallExecution,
    HostedHarnessJob,
    RunTest,
    Scenarios,
    SimulateEvalConfig,
    TestExecution,
)
from simulate.models.agent_version import AgentVersion
from simulate.serializers.run_test import (
    HARNESS_RUN_TESTS_CONTEXT_KEY,
    SimulateEvalConfigSimpleSerializer,
)
from simulate.services.harness_evals import (
    harness_run_test_ids,
    is_harness_call,
    is_harness_run_test,
)
from simulate.services.test_executor import TestExecutor

_HARNESS_CHECKS = [{"name": "ride_booked", "passed": True}]


@pytest.fixture
def agent_definition(db, organization, workspace):
    return AgentDefinition.objects.create(
        agent_name="Harness agent",
        agent_type=AgentDefinition.AgentTypeChoices.VOICE,
        contact_number="+15551230000",
        inbound=True,
        description="Agent a harness scores",
        organization=organization,
        workspace=workspace,
        languages=["en"],
    )


@pytest.fixture
def agent_version(db, agent_definition, organization, workspace):
    return AgentVersion.objects.create(
        agent_definition=agent_definition,
        organization=organization,
        workspace=workspace,
        version_number=1,
        version_name="v1",
        configuration_snapshot={"description": "You are a helpful agent."},
    )


@pytest.fixture
def scenario(db, organization, workspace, agent_definition):
    return Scenarios.objects.create(
        name="Harness scenario",
        source="seed",
        scenario_type=Scenarios.ScenarioTypes.DATASET,
        organization=organization,
        workspace=workspace,
        agent_definition=agent_definition,
    )


@pytest.fixture
def make_run_test(organization, workspace, agent_definition, agent_version, scenario):
    """A run test with one completed run; each call given gets its metadata."""

    def make(*call_metadata, execution_deleted=False):
        run_test = RunTest.objects.create(
            name=f"Run test {uuid.uuid4().hex[:6]}",
            organization=organization,
            workspace=workspace,
            agent_definition=agent_definition,
            enable_tool_evaluation=False,
        )
        execution = TestExecution.objects.create(
            run_test=run_test,
            status=TestExecution.ExecutionStatus.COMPLETED,
            total_scenarios=1,
            total_calls=len(call_metadata),
            agent_definition=agent_definition,
            agent_version=agent_version,
            scenario_ids=[str(scenario.id)],
            deleted=execution_deleted,
        )
        for metadata in call_metadata:
            CallExecution.objects.create(
                test_execution=execution,
                scenario=scenario,
                agent_version=agent_version,
                status=CallExecution.CallStatus.COMPLETED,
                simulation_call_type=CallExecution.SimulationCallType.VOICE,
                provider_call_data={"vapi": {"call_id": "vapi-12345"}},
                call_metadata=metadata,
            )
        return run_test

    return make


def _link_hosted_job(run_test):
    """Link the run test to a hosted job, as registering an environment's
    scenarios does."""
    HostedHarnessJob.no_workspace_objects.create(
        organization=run_test.organization,
        workspace=run_test.workspace,
        run_id=uuid.uuid4(),
        idempotency_key=f"harness-run-test-{uuid.uuid4()}",
        request_digest=f"sha256:{'0' * 64}",
        schema_version="1.4",
        payload={},
        state=HostedHarnessJob.State.COMPLETED,
        seed=1,
        scenario_count=1,
        artifact_level="standard",
        max_artifact_bytes=1,
        deadline_at=timezone.now() + timedelta(hours=1),
        run_test=run_test,
    )


def _config(run_test, mapping, owner="user", required_keys=("conversation",)):
    template = EvalTemplate.objects.create(
        name=f"template_{uuid.uuid4().hex[:8]}",
        config={"required_keys": list(required_keys), "output": "Pass/Fail"},
        owner=owner,
        organization=run_test.organization,
    )
    return SimulateEvalConfig.objects.create(
        name=f"config {uuid.uuid4().hex[:6]}",
        eval_template=template,
        run_test=run_test,
        mapping=mapping,
    )


def _mapped(run_test):
    return _config(run_test, {"conversation": "transcript"})


def _sql_reading(queries, table):
    return [q["sql"] for q in queries.captured_queries if f'"{table}"' in q["sql"]]


def _scans_of_the_run_tests_calls(queries, run_test):
    """Queries that read calls by their run test rather than by their own id."""
    # The driver may render a UUID with or without its hyphens.
    forms = (str(run_test.id), run_test.id.hex)
    return [
        sql
        for sql in _sql_reading(queries, "simulate_call_execution")
        if any(form in sql for form in forms)
    ]


# --- The single test ---------------------------------------------------------


@pytest.mark.django_db
def test_a_hosted_job_makes_a_harness_run(make_run_test):
    run_test = make_run_test({})
    _link_hosted_job(run_test)

    assert is_harness_run_test(run_test.id) is True
    assert harness_run_test_ids([run_test.id]) == {run_test.id}


@pytest.mark.django_db
@pytest.mark.parametrize(
    "harness_checks", [_HARNESS_CHECKS, None], ids=["a-list", "null"]
)
def test_an_sdk_run_with_harness_checks_is_a_harness_run(make_run_test, harness_checks):
    run_test = make_run_test({}, {"harness_evaluations": harness_checks})
    _config(run_test, {})

    assert is_harness_run_test(run_test.id) is True
    assert harness_run_test_ids([run_test.id]) == {run_test.id}


@pytest.mark.django_db
def test_an_sdk_run_with_code_checks_only_is_a_harness_run(make_run_test):
    """A harness that wrote only code checks leaves no config with an empty
    mapping behind; its calls alone say a harness scored them."""
    run_test = make_run_test({"harness_evaluations": _HARNESS_CHECKS})
    _mapped(run_test)

    assert is_harness_run_test(run_test.id) is True


@pytest.mark.django_db
def test_the_calls_of_a_soft_deleted_run_still_count(make_run_test):
    run_test = make_run_test(
        {"harness_evaluations": _HARNESS_CHECKS}, execution_deleted=True
    )

    assert is_harness_run_test(run_test.id) is True


@pytest.mark.django_db
def test_a_run_with_no_harness_is_not(make_run_test):
    run_test = make_run_test({}, {"eval_started": True})
    _mapped(run_test)
    _mapped(run_test)

    assert is_harness_run_test(run_test.id) is False
    assert harness_run_test_ids([run_test.id]) == set()


@pytest.mark.django_db
def test_an_empty_mapping_config_alone_is_not(make_run_test):
    """A person can save an eval with no mapping; that alone is no harness."""
    run_test = make_run_test({}, {"eval_started": True})
    _config(run_test, {})
    _config(run_test, None)

    assert is_harness_run_test(run_test.id) is False
    assert harness_run_test_ids([run_test.id]) == set()


@pytest.mark.django_db
def test_a_missing_run_test_is_not_a_harness_run():
    assert is_harness_run_test(uuid.uuid4()) is False
    assert harness_run_test_ids([uuid.uuid4()]) == set()


@pytest.mark.django_db
@pytest.mark.parametrize("owned", [False, True], ids=["sdk", "hosted"])
def test_the_single_test_costs_two_queries_at_most(make_run_test, owned):
    run_test = make_run_test({}, {"eval_started": True})
    if owned:
        _link_hosted_job(run_test)

    with CaptureQueriesContext(connection) as queries:
        is_harness_run_test(run_test.id)

    assert len(queries.captured_queries) <= 2, queries.captured_queries
    if owned:
        assert _sql_reading(queries, "simulate_call_execution") == []


# --- The batch ---------------------------------------------------------------


def _five_run_tests(make_run_test):
    """A hosted run, an SDK run with a result column, an SDK run with code
    checks only, a person's empty mapping, and a plain run."""
    hosted = make_run_test({})
    _link_hosted_job(hosted)
    _config(hosted, {})

    sdk_column = make_run_test({"harness_evaluations": _HARNESS_CHECKS})
    _config(sdk_column, {})
    _mapped(sdk_column)

    sdk_code_checks = make_run_test({"harness_evaluations": _HARNESS_CHECKS})
    _mapped(sdk_code_checks)

    person_empty = make_run_test({})
    _config(person_empty, {})

    plain = make_run_test({})
    _mapped(plain)

    return hosted, sdk_column, sdk_code_checks, person_empty, plain


def _flags(config, harness_run):
    data = SimulateEvalConfigSimpleSerializer(
        config,
        context={HARNESS_RUN_TESTS_CONTEXT_KEY: {config.run_test_id: harness_run}},
    ).data
    return data["regradable"], data["editable"]


@pytest.mark.django_db
def test_the_batch_answers_like_the_single_test_where_a_flag_could_differ(
    make_run_test,
):
    run_tests = _five_run_tests(make_run_test)
    hosted, sdk_column, sdk_code_checks, person_empty, plain = run_tests

    batch = harness_run_test_ids([run_test.id for run_test in run_tests])

    assert batch == {hosted.id, sdk_column.id}
    for run_test in (hosted, sdk_column, person_empty, plain):
        assert (run_test.id in batch) is is_harness_run_test(run_test.id)
    configs = SimulateEvalConfig.objects.filter(run_test=sdk_code_checks)
    assert configs.exists()
    for config in configs:
        assert _flags(config, True) == _flags(config, False)


@pytest.mark.django_db
def test_the_batch_costs_three_queries_at_most(
    make_run_test, django_assert_num_queries
):
    ids = [run_test.id for run_test in _five_run_tests(make_run_test)]

    with CaptureQueriesContext(connection) as queries:
        harness_run_test_ids(ids)

    assert len(queries.captured_queries) <= 3, queries.captured_queries
    with django_assert_num_queries(0):
        assert harness_run_test_ids([]) == set()


@pytest.mark.django_db
def test_the_batch_reads_no_call_for_run_tests_without_an_empty_mapping_config(
    make_run_test,
):
    mapped_only = make_run_test({"harness_evaluations": _HARNESS_CHECKS})
    _mapped(mapped_only)
    no_config = make_run_test({"harness_evaluations": _HARNESS_CHECKS})

    with CaptureQueriesContext(connection) as queries:
        batch = harness_run_test_ids([mapped_only.id, no_config.id])

    assert batch == set()
    assert _sql_reading(queries, "simulate_call_execution") == []


# --- The grader's question ---------------------------------------------------


@pytest.mark.django_db
def test_a_call_with_the_harness_key_is_a_harness_call_without_a_query(
    make_run_test, django_assert_num_queries
):
    run_test = make_run_test({"harness_evaluations": None})
    call = CallExecution.objects.get(test_execution__run_test=run_test)

    with django_assert_num_queries(0):
        assert is_harness_call(call, run_test.id) is True


@pytest.mark.django_db
@pytest.mark.parametrize("owned", [False, True], ids=["no-job", "hosted"])
def test_a_call_without_the_key_asks_only_for_a_hosted_job(make_run_test, owned):
    run_test = make_run_test({}, {"harness_evaluations": _HARNESS_CHECKS})
    if owned:
        _link_hosted_job(run_test)
    call = CallExecution.objects.get(
        test_execution__run_test=run_test, call_metadata={}
    )

    with CaptureQueriesContext(connection) as queries:
        answer = is_harness_call(call, run_test.id)

    assert answer is owned
    assert len(queries.captured_queries) == 1, queries.captured_queries
    assert _sql_reading(queries, "simulate_hosted_harness_job")
    assert _sql_reading(queries, "simulate_call_execution") == []


class _NoSkip:
    processing_skipped = False
    processing_skip_reason = ""


_TRANSCRIPT_DATA = {
    "transcript": "Hello. Yes, order 123 shipped.",
    "voice_recording": "s3://bucket/rec.mp3",
    "assistant_recording": "s3://bucket/asst.mp3",
    "customer_recording": "s3://bucket/cust.mp3",
    "stereo_recording": "s3://bucket/stereo.mp3",
    "user_chat_transcript": "",
    "assistant_chat_transcript": "",
}


@pytest.mark.django_db
@patch("simulate.services.test_executor.close_old_connections", lambda: None)
@patch("simulate.services.test_executor.decide_processing_skip", lambda **_: _NoSkip)
@patch.object(TestExecutor, "_get_call_transcript_data", return_value=_TRANSCRIPT_DATA)
@patch.object(TestExecutor, "_run_single_simulate_evaluation")
@pytest.mark.parametrize(
    ("call_metadata", "harness_run"),
    [({"harness_evaluations": _HARNESS_CHECKS}, True), ({}, False)],
    ids=["sdk-call", "no-key"],
)
def test_the_grader_asks_the_call_not_the_run_test(
    mock_single, _mock_transcript, make_run_test, call_metadata, harness_run
):
    """The grader asks once per call; a scan of the run test's calls each
    time would cost the square of their number on a large run test."""
    run_test = make_run_test(call_metadata, {}, {})
    call = CallExecution.objects.filter(
        test_execution__run_test=run_test, call_metadata=call_metadata
    ).first()
    suite = _config(run_test, {}, owner="system")
    with CaptureQueriesContext(connection) as scan:
        is_harness_run_test(run_test.id)
    assert _scans_of_the_run_tests_calls(scan, run_test), "the probe sees no scan"

    with CaptureQueriesContext(connection) as queries:
        TestExecutor(initialize_voice_service=False)._run_simulate_evaluations(
            call, eval_config_ids=[str(suite.id)], skip_existing=False
        )

    assert mock_single.call_count == 1
    assert mock_single.call_args.kwargs.get("harness_run", False) is harness_run
    assert _scans_of_the_run_tests_calls(queries, run_test) == []


@pytest.mark.django_db
def test_a_result_column_stored_with_a_null_mapping_counts_in_the_batch(make_run_test):
    run_test = make_run_test({"harness_evaluations": _HARNESS_CHECKS})
    _config(run_test, None)

    assert harness_run_test_ids([run_test.id]) == {run_test.id}
