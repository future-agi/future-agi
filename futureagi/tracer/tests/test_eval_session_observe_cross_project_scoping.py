"""``evaluate_trace_session_observe`` scopes its ClickHouse session lookup by
the attached eval CONFIG's project, not by the eval TASK's. A task may run
configs authored in a sibling project (the M2M has no same-project
constraint — see ``fix(eval-tasks): scope runtime target loads by the task's
project``, which fixed the identical bug in ``run_entry``'s CH-direct span /
trace / session loads) while its sessions live in the task's own project:
scoping the CH-direct read by the config's project instead finds nothing and
raises "TraceSession ... does not exist" even though the session is sitting
in ClickHouse the whole time, under the task's project.

This does not touch the separate, intentional org-scope/billing anchor
(``custom_eval_config.project``, still used for the returned vehicle's
``.project`` and downstream cost-deduction) — only the CH lookup scope needs
to follow the data.
"""

import pytest

from model_hub.models.ai_model import AIModel
from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.models.eval_task import EvalTask, EvalTaskStatus, RunType
from tracer.models.observation_span import EvalLogger
from tracer.models.project import Project
from tracer.models.trace_session import TraceSession
from tracer.tests._ch_seed import seed_ch_trace_sessions
from tracer.utils.eval import evaluate_trace_session_observe


@pytest.fixture
def config_project(db, organization, workspace):
    """The sibling project the borrowed config is authored in — it never
    holds the evaluated session."""
    return Project.objects.create(
        name="Config Author Project",
        organization=organization,
        workspace=workspace,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type="observe",
    )


def _borrowed_config(config_project, eval_template, mapping):
    return CustomEvalConfig.objects.create(
        name="Borrowed Eval",
        project=config_project,
        eval_template=eval_template,
        config={"threshold": 0.8},
        mapping=mapping,
        filters={},
    )


def _task_with(project, config):
    task = EvalTask.objects.create(
        project=project,
        name="Cross-project Session Eval Task",
        filters={},
        sampling_rate=1.0,
        run_type=RunType.CONTINUOUS,
        status=EvalTaskStatus.PENDING,
        spans_limit=100,
    )
    task.evals.add(config)
    return task


@pytest.mark.integration
@pytest.mark.django_db
class TestEvaluateTraceSessionObserveCrossProjectScoping:
    def test_completes_when_config_lives_in_sibling_project(
        self,
        observe_project,
        config_project,
        eval_template,
        stub_run_eval,
        stub_cost_log,
    ):
        # "name" is a session field; "input" is not — same mapping shape the
        # sibling run_entry test uses for its session case.
        config = _borrowed_config(config_project, eval_template, {"input": "name"})
        task = _task_with(observe_project, config)
        session = TraceSession.objects.create(project=observe_project, name="sess")
        seed_ch_trace_sessions([session])

        result = evaluate_trace_session_observe(
            session_id=str(session.id),
            custom_eval_config_id=str(config.id),
            eval_task_id=str(task.id),
        )

        assert result is True
        entry = EvalLogger.objects.get(
            trace_session_id=str(session.id),
            custom_eval_config_id=str(config.id),
            eval_task_id=str(task.id),
        )
        assert not entry.error

    def test_still_works_when_config_and_task_share_a_project(
        self, observe_project, eval_template, stub_run_eval, stub_cost_log
    ):
        """Regression guard: the common case (config and task in the same
        project) must keep working unchanged."""
        config = _borrowed_config(observe_project, eval_template, {"input": "name"})
        task = _task_with(observe_project, config)
        session = TraceSession.objects.create(project=observe_project, name="sess")
        seed_ch_trace_sessions([session])

        result = evaluate_trace_session_observe(
            session_id=str(session.id),
            custom_eval_config_id=str(config.id),
            eval_task_id=str(task.id),
        )

        assert result is True
        entry = EvalLogger.objects.get(
            trace_session_id=str(session.id),
            custom_eval_config_id=str(config.id),
            eval_task_id=str(task.id),
        )
        assert not entry.error
