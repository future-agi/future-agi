# Pytest fixtures are imported from a sibling sweep module.
# ruff: noqa: F811
"""R-07/R-09 and findings F-01/F-02 through the real authenticated routes.

Only the external evaluation executor and ClickHouse transport are replaced.
Tenant ORM queries, auth, membership checks, request serializers, EE entry gates,
resolve_session_fields, and session/trace path resolution run their real code.
This proves admission/data-selection boundaries, not a live evaluator or CH run.
"""

from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

import pytest
from django.utils import timezone

from model_hub.models.ai_model import AIModel
from model_hub.models.choices import EvalTemplateType
from model_hub.tests.test_tenancy_sweep_eval_templates_r1 import (
    assert_missing_parity,
    eval_factory,  # noqa: F401 - shared fixture
    routed,
    sweep_scope,  # noqa: F401 - shared fixture
    target_space,
)
from tracer.models.project import Project
from tracer.models.trace import Trace

pytestmark = pytest.mark.django_db


@pytest.fixture
def execution(sweep_scope):
    from model_hub.views import separate_evals

    # No provider/network work. Autospec catches a drift in the execution call.
    with patch.object(
        separate_evals,
        "run_eval_func",
        autospec=True,
        return_value={"score": 1, "reason": "synthetic execution boundary"},
    ) as execute:
        yield execute


def log_ids(scope):
    from model_hub.views import separate_evals

    assert separate_evals.APICallLog is not None, (
        "Log assertions require real EE usage models"
    )
    return set(
        separate_evals.APICallLog.no_workspace_objects.filter(
            organization=scope.organization
        ).values_list("log_id", flat=True)
    )


def playground(scope, template_id, **fields):
    # Explicit model=None avoids a spurious Turing entitlement failure for the
    # missing-id comparator. The real gate is never monkeypatched.
    return routed(
        scope,
        "post",
        "/model-hub/eval-playground/",
        {"template_id": str(template_id), "model": None, **fields},
    )


@pytest.mark.parametrize("boundary", ["workspace", "organization"])
def test_r07_foreign_template_is_not_executed(
    sweep_scope, eval_factory, execution, boundary
):
    template = eval_factory(boundary)
    missing_id = uuid4()
    before = log_ids(sweep_scope)
    missing = playground(sweep_scope, missing_id, mapping={"input": "synthetic"})
    execution.assert_not_called()
    denied = playground(sweep_scope, template.pk, mapping={"input": "synthetic"})
    execution.assert_not_called()
    assert log_ids(sweep_scope) == before
    assert_missing_parity(denied, missing, template.pk, missing_id, statuses=(400, 404))


def test_r07_same_workspace_template_reaches_executor(
    sweep_scope, eval_factory, execution
):
    template = eval_factory()
    response = playground(sweep_scope, template.pk, mapping={"input": "synthetic"})
    assert response.status_code == 200, response.data
    execution.assert_called_once()
    args, kwargs = execution.call_args
    assert args[1] == {"input": "synthetic"}
    assert args[2].pk == template.pk
    assert args[3].pk == sweep_scope.organization.pk
    assert kwargs["workspace"].pk == sweep_scope.workspace.pk


class SessionTransport:
    """Synthetic CH rows behind the REAL identity resolver, not a scope stub.

    Rows model net-new CH sessions (no PG TraceSession row). Respect every
    project parameter produced by the production SQL builder so a fix that
    supplies project_id/project_ids also exercises this fixture correctly.
    Unsupported transport calls fail rather than returning permissive data.
    """

    def __init__(self, sessions):
        self.sessions = sessions
        self.trace_set_reads = []

    def query(self, sql, parameters):
        assert "trace_sessions" in sql and "ids" in parameters, sql
        permitted = None
        if "pid" in parameters:
            assert "ts.project_id = %(pid)s" in sql
            permitted = {str(parameters["pid"])}
        elif "pids" in parameters:
            assert "ts.project_id IN %(pids)s" in sql
            permitted = {str(pk) for pk in parameters["pids"]}
        rows = []
        for requested_id in parameters["ids"]:
            session = self.sessions.get(str(requested_id))
            if session is None:
                continue
            if permitted is not None and str(session.project.pk) not in permitted:
                continue
            rows.append(
                (
                    str(session.id),
                    str(session.id),
                    session.name,
                    session.first_seen,
                    str(session.project.pk),
                )
            )
        return SimpleNamespace(result_rows=rows)

    def session_trace_ids(self, project_id, session_id):
        self.trace_set_reads.append((str(project_id), str(session_id)))
        session = self.sessions.get(str(session_id))
        if session is None or str(session.project.pk) != str(project_id):
            return []
        return [str(session.trace.pk)]

    def list_by_trace(self, trace_id, project_id=None):
        # Empty span sets are valid. The test's actual trace input is in PG.
        return []

    def list_by_session(self, session_id, project_id=None):
        return []

    def _resolve_session_ids_to_canonical(self, session_ids):
        # Net-new sessions have no consolidation group: each id is its own
        # survivor, matching the real resolver's unmapped-id behaviour.
        return {str(s): str(s) for s in session_ids if s}


@pytest.fixture
def telemetry(sweep_scope, monkeypatch):
    from tracer.services.clickhouse import v2
    from tracer.services.clickhouse.v2 import trace_session_dict_reader
    from tracer.services.clickhouse.v2.eval_loader import eval_read_source
    from tracer.utils import eval as eval_utils

    entries = {}
    for boundary in ("own", "workspace", "organization"):
        space = target_space(sweep_scope, boundary)
        project = Project.no_workspace_objects.create(
            organization=space.organization,
            workspace=space,
            name=f"sweep-project-{uuid4().hex}",
            model_type=AIModel.ModelTypes.GENERATIVE_LLM,
            trace_type="observe",
        )
        trace = Trace.no_workspace_objects.create(
            project=project,
            name=f"sweep-trace-{uuid4().hex}",
            input=f"trace-input-{boundary}-{uuid4().hex}",
            output="synthetic output",
        )
        entries[boundary] = SimpleNamespace(
            id=uuid4(),
            project=project,
            trace=trace,
            name=f"session-name-{boundary}-{uuid4().hex}",
            first_seen=timezone.now(),
        )
    transport = SessionTransport({str(s.id): s for s in entries.values()})
    monkeypatch.setattr(trace_session_dict_reader, "_get_client", lambda: transport)
    monkeypatch.setattr(v2, "get_reader", lambda: nullcontext(transport))
    # This is a call-through observer: it does not replace the mapping result
    # or any authorization check. It establishes that a refused reference is
    # rejected BEFORE traversing that session's data.
    mapping_spy = Mock(wraps=eval_utils._process_session_mapping)
    monkeypatch.setattr(eval_utils, "_process_session_mapping", mapping_spy)
    # Exercise the real PG Trace lookup identified in the finding, regardless of
    # the runner's default. This public context manager changes only storage.
    with eval_read_source("postgres"):
        yield SimpleNamespace(
            entries=entries, transport=transport, mapping_spy=mapping_spy
        )


@pytest.mark.parametrize("boundary", ["organization", "workspace"])
@pytest.mark.parametrize("path", ["name", "traces.0.input"])
def test_f01_foreign_session_is_rejected_before_mapping(
    sweep_scope, eval_factory, execution, telemetry, boundary, path
):
    own_template = eval_factory()
    foreign = telemetry.entries[boundary]
    missing_id = uuid4()
    before = log_ids(sweep_scope)
    missing = playground(
        sweep_scope,
        own_template.pk,
        session_id=str(missing_id),
        mapping_paths={"input": path},
    )
    execution.assert_not_called()
    telemetry.mapping_spy.assert_not_called()
    denied = playground(
        sweep_scope,
        own_template.pk,
        session_id=str(foreign.id),
        mapping_paths={"input": path},
    )
    telemetry.mapping_spy.assert_not_called()
    execution.assert_not_called()
    assert (
        str(foreign.project.pk),
        str(foreign.id),
    ) not in telemetry.transport.trace_set_reads
    assert log_ids(sweep_scope) == before
    # F-01 allows the legacy session-not-found 400 or a uniform 404. Only an
    # echoed caller-supplied id is normalized; error structure/text must match.
    assert_missing_parity(denied, missing, foreign.id, missing_id, statuses=(400, 404))


@pytest.mark.parametrize("path", ["name", "traces.0.input"])
def test_f01_same_workspace_session_is_really_mapped(
    sweep_scope, eval_factory, execution, telemetry, path
):
    own_template = eval_factory()
    own = telemetry.entries["own"]
    response = playground(
        sweep_scope,
        own_template.pk,
        session_id=str(own.id),
        mapping_paths={"input": path},
    )
    assert response.status_code == 200, response.data
    telemetry.mapping_spy.assert_called_once()
    execution.assert_called_once()
    args, kwargs = execution.call_args
    assert args[1] == {"input": own.name if path == "name" else own.trace.input}
    assert args[2].pk == own_template.pk
    assert kwargs["workspace"].pk == sweep_scope.workspace.pk
    assert kwargs["session_context"]["id"] == str(own.id)


@pytest.mark.parametrize("boundary", ["workspace", "organization"])
def test_f02_foreign_trace_is_indistinguishable_from_missing(
    sweep_scope, eval_factory, execution, telemetry, boundary
):
    own_template = eval_factory()
    foreign = telemetry.entries[boundary].trace
    missing_id = uuid4()
    missing = playground(
        sweep_scope,
        own_template.pk,
        trace_id=str(missing_id),
        mapping={"input": "caller-owned"},
    )
    missing_context = None
    if missing.status_code == 200:
        execution.assert_called_once()
        missing_context = execution.call_args.kwargs.get("trace_context")
        assert not missing_context
    else:
        assert missing.status_code in (400, 404), missing.data
        execution.assert_not_called()
    execution.reset_mock()
    denied = playground(
        sweep_scope,
        own_template.pk,
        trace_id=str(foreign.pk),
        mapping={"input": "caller-owned"},
    )
    if denied.status_code == 200:
        # The finding explicitly allows empty context instead of refusing the run.
        assert missing.status_code == 200
        execution.assert_called_once()
        assert execution.call_args.kwargs.get("trace_context") == missing_context
        assert execution.call_args.args[1] == {"input": "caller-owned"}
        assert denied.json() == missing.json()
    else:
        execution.assert_not_called()
        assert_missing_parity(
            denied, missing, foreign.pk, missing_id, statuses=(400, 404)
        )


def test_f02_same_workspace_trace_context_reaches_executor(
    sweep_scope, eval_factory, execution, telemetry
):
    own_template = eval_factory()
    own = telemetry.entries["own"].trace
    response = playground(
        sweep_scope,
        own_template.pk,
        trace_id=str(own.pk),
        mapping={"input": "caller-owned"},
    )
    assert response.status_code == 200, response.data
    execution.assert_called_once()
    context = execution.call_args.kwargs["trace_context"]
    assert context["id"] == str(own.pk)
    assert context["project_id"] == str(own.project_id)
    assert context["input"] == own.input


def function_request(scope, eval_type_id):
    # template_id is intentionally ABSENT: this is R-09's by-type fallback.
    return routed(
        scope,
        "post",
        "/model-hub/test-evaluation/",
        {
            "name": "synthetic-function-test",
            "template_type": EvalTemplateType.FUNCTION.value,
            "eval_type": "code",
            "eval_type_id": eval_type_id,
            "output_type": "Pass/Fail",
            "model": None,
            "required_keys": ["input"],
            "config": {},
            "mapping": {"input": "caller-owned"},
        },
    )


@pytest.mark.parametrize("boundary", ["workspace", "organization"])
def test_r09_function_fallback_never_selects_foreign_template(
    sweep_scope, eval_factory, execution, boundary
):
    foreign = eval_factory(boundary)
    response = function_request(sweep_scope, foreign.config["eval_type_id"])
    if response.status_code == 404:
        execution.assert_not_called()
        return
    # R-09 also permits a caller-workspace/system template (including the
    # existing unsaved caller-owned fallback), but never the foreign one.
    assert response.status_code == 200, response.data
    execution.assert_called_once()
    selected = execution.call_args.args[2]
    assert selected.pk != foreign.pk
    assert selected.organization_id in (None, sweep_scope.organization.pk)
    if selected.owner != "system":
        assert selected.organization_id == sweep_scope.organization.pk
        assert selected.workspace_id == sweep_scope.workspace.pk
    assert execution.call_args.kwargs["workspace"].pk == sweep_scope.workspace.pk


def test_r09_function_fallback_selects_same_workspace_template(
    sweep_scope, eval_factory, execution
):
    own = eval_factory()
    response = function_request(sweep_scope, own.config["eval_type_id"])
    assert response.status_code == 200, response.data
    execution.assert_called_once()
    assert execution.call_args.args[2].pk == own.pk
    assert execution.call_args.kwargs["eval_id"] == own.config["eval_type_id"]
    assert execution.call_args.kwargs["workspace"].pk == sweep_scope.workspace.pk
