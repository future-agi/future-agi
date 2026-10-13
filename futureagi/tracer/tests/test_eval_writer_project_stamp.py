"""TH-4804: all four usage writers preserve provenance and stamp the owner project."""

from types import SimpleNamespace
from uuid import uuid4

import pytest

from tracer.utils import eval as eval_utils


class UsageCaptured(BaseException):
    """Stop at the real usage boundary, before executing an evaluation."""


@pytest.mark.parametrize("path", ["legacy_span", "span", "trace", "session"])
@pytest.mark.parametrize("feedback", [False, True])
def test_writer_stamps_owner_project(monkeypatch, path, feedback):
    project = SimpleNamespace(
        id=uuid4(),
        organization=SimpleNamespace(id=uuid4()),
        workspace=SimpleNamespace(id=uuid4()),
    )
    trace = SimpleNamespace(id=uuid4(), project=project, project_id=project.id)
    span = SimpleNamespace(
        id=str(uuid4()), trace=trace, project=project, project_id=project.id
    )
    session = SimpleNamespace(id=uuid4(), project=project, project_id=project.id)
    template = SimpleNamespace(
        id=uuid4(),
        template_type="single",
        config={"eval_type_id": "CustomCodeEval"},
    )
    config = SimpleNamespace(
        id=uuid4(),
        eval_template=template,
        model="turing_large",
        # Stamp the evaluated owner's project, even if the config was reassigned.
        project_id=uuid4(),
        config={},
    )
    feedback_id = uuid4() if feedback else None
    params = {"output": "stored input"}
    captured = []

    def capture(**kwargs):
        captured.append(kwargs)
        raise UsageCaptured

    monkeypatch.setattr(eval_utils, "log_and_deduct_cost_for_api_request", capture)
    monkeypatch.setattr(eval_utils.CustomEvalConfig.objects, "get", lambda **_: config)
    monkeypatch.setattr(eval_utils, "_stamp_eval_version", lambda *_: None)
    monkeypatch.setattr(
        eval_utils, "_inject_ground_truth", lambda run_params, *_, **__: run_params
    )
    monkeypatch.setattr(eval_utils, "_collect_run_warnings", lambda *_, **__: [])
    monkeypatch.setattr(
        "model_hub.utils.eval_input_validation.validate_eval_inputs",
        lambda _, run_params, **__: (None, run_params),
    )
    metering = pytest.importorskip(
        "ee.usage.services.metering", reason="writer usage capture requires ee/"
    )
    monkeypatch.setattr(
        metering, "check_usage", lambda *_: SimpleNamespace(allowed=True)
    )

    with pytest.raises(UsageCaptured):
        if path == "legacy_span":
            eval_utils._run_evaluation(
                params,
                template,
                None,
                span,
                config,
                None,
                "CustomCodeEval",
                False,
                None,
                params,
                feedback_id=feedback_id,
            )
        elif path == "span":
            eval_utils._execute_evaluation(
                span.id,
                config.id,
                None,
                "observe",
                run_params=params,
                feedback_id=feedback_id,
                observation_span=span,
            )
        elif path == "trace":
            eval_utils._execute_evaluation_for_trace(
                trace=trace,
                anchor_span=span,
                custom_eval_config=config,
                eval_task_id=None,
                run_params=params,
                feedback_id=feedback_id,
            )
        else:
            eval_utils._execute_evaluation_for_session(
                trace_session=session,
                custom_eval_config=config,
                eval_task_id=None,
                run_params=params,
                feedback_id=feedback_id,
            )

    assert len(captured) == 1
    usage = captured[0]
    expected = {
        "project_id": str(project.id),
        "custom_eval_config_id": str(config.id),
        "is_futureagi_eval": False,
        "mappings": params,
        "required_keys": ["output"],
        "source": "tracer",
    }
    if path == "session":
        expected.update(
            reference_id=str(session.id),
            session_id=str(session.id),
            target_type="session",
        )
    else:
        expected.update(
            reference_id=str(trace.id) if path == "trace" else span.id,
            span_id=span.id,
            trace_id=str(trace.id),
        )
        if path == "trace":
            expected["target_type"] = "trace"
    if feedback:
        expected["feedback_id"] = str(feedback_id)
    assert usage["config"] == expected
    assert usage["source_id"] == template.id
    assert usage["source"] == ("feedback" if feedback else "tracer")
    assert usage["workspace"] is project.workspace
    assert usage["organization"] is project.organization
