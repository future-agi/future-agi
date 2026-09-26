"""Regression coverage for eval-settings unexpected-error boundaries."""

import ast
import inspect
from contextlib import nullcontext
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from model_hub.views import separate_evals

PRIVATE_ERROR = "private database detail and SQL"

_TARGET_METHODS = (
    ("CellErrorLocalizerView", "post"),
    ("CellErrorLocalizerView", "get"),
    ("GetEvalTemplateNameView", "post"),
    ("GetEvalTemplates", "post"),
    ("EvalTemplateBulkDeleteView", "post"),
    ("EvalTemplateCreateV2View", "post"),
    ("EvalTemplateDetailView", "get"),
    ("EvalTemplateUpdateView", "put"),
    ("EvalTemplateVersionListView", "get"),
    ("EvalTemplateVersionCreateView", "post"),
    ("SetDefaultVersionView", "put"),
    ("RestoreVersionView", "post"),
    ("CompositeEvalCreateView", "post"),
    ("CompositeEvalDetailView", "get"),
    ("CompositeEvalDetailView", "patch"),
    ("CompositeEvalExecuteView", "post"),
    ("CompositeEvalAdhocExecuteView", "post"),
    ("GroundTruthListView", "get"),
    ("GroundTruthDataView", "get"),
    ("GroundTruthStatusView", "get"),
    ("GroundTruthDeleteView", "delete"),
    ("GroundTruthTriggerEmbeddingView", "post"),
    ("EvalFeedbackListView", "get"),
    ("TraceEvalView", "post"),
    ("VersionCompareView", "get"),
    ("EvalPlayGroundAPIView", "post"),
    ("EvalCodeSnippetAPIView", "get"),
    ("EvalPlayGroundFeedbackAPIView", "post"),
    ("UpdateEvalTemplateView", "post"),
    ("DeleteEvalTemplateView", "post"),
    ("DuplicateEvalTemplateView", "post"),
    ("TestEvaluationTemplateAPIView", "post"),
)


def _raise_private_error(*_args, **_kwargs):
    raise RuntimeError(PRIVATE_ERROR)


def _assert_sanitized_400(response):
    assert response.status_code == 400
    assert PRIVATE_ERROR not in str(response.data)
    assert response.data["code"] == "invalid"


def test_eval_template_name_picker_sanitizes_unexpected_errors(monkeypatch):
    monkeypatch.setattr(
        separate_evals.EvalTemplate.no_workspace_objects,
        "filter",
        _raise_private_error,
    )
    organization = SimpleNamespace(id="org-1")
    request = SimpleNamespace(
        validated_data={},
        organization=organization,
        user=SimpleNamespace(organization=organization),
    )

    response = inspect.unwrap(separate_evals.GetEvalTemplateNameView.post)(
        separate_evals.GetEvalTemplateNameView(),
        request,
    )

    _assert_sanitized_400(response)


def test_legacy_eval_template_list_sanitizes_unexpected_errors(monkeypatch):
    manager = SimpleNamespace(filter=_raise_private_error)
    monkeypatch.setattr(
        separate_evals,
        "APICallLog",
        SimpleNamespace(objects=manager),
    )
    organization = SimpleNamespace(id="org-1")
    request = SimpleNamespace(
        validated_data={},
        organization=organization,
        workspace=None,
        user=SimpleNamespace(organization=organization),
    )

    response = inspect.unwrap(separate_evals.GetEvalTemplates.post)(
        separate_evals.GetEvalTemplates(),
        request,
    )

    _assert_sanitized_400(response)


@pytest.mark.parametrize(
    ("method_name", "validated_data_attribute"),
    (("get", "validated_query_data"), ("post", "validated_data")),
)
def test_eval_metrics_sanitize_unexpected_errors(
    monkeypatch, method_name, validated_data_attribute
):
    eval_template = SimpleNamespace(id="11111111-1111-4111-8111-111111111111")
    monkeypatch.setattr(
        separate_evals,
        "APICallLog",
        SimpleNamespace(objects=SimpleNamespace(filter=lambda **_kwargs: object())),
    )
    monkeypatch.setattr(
        separate_evals, "_get_eval_metric_template", lambda *_args: eval_template
    )
    monkeypatch.setattr(
        separate_evals, "_bounded_eval_metric_read", lambda _deadline: nullcontext()
    )
    monkeypatch.setattr(separate_evals, "get_eval_metric_data", _raise_private_error)
    organization = SimpleNamespace(id="org-1")
    request_data = {
        "eval_template_id": eval_template.id,
        "filters": [],
    }
    request = SimpleNamespace(
        organization=organization,
        user=SimpleNamespace(organization=organization),
        **{validated_data_attribute: request_data},
    )

    response = inspect.unwrap(getattr(separate_evals.EvalMetricView, method_name))(
        separate_evals.EvalMetricView(),
        request,
    )

    _assert_sanitized_400(response)
    assert response.data["result"] == "Evaluation metrics could not be loaded"


@pytest.mark.parametrize(
    ("method_name", "validated_data_attribute"),
    (("get", "validated_query_data"), ("post", "validated_data")),
)
def test_eval_metrics_preserve_missing_template_bad_request(
    monkeypatch, method_name, validated_data_attribute
):
    monkeypatch.setattr(
        separate_evals,
        "APICallLog",
        SimpleNamespace(objects=SimpleNamespace(filter=lambda **_kwargs: object())),
    )
    monkeypatch.setattr(
        separate_evals, "_get_eval_metric_template", lambda *_args: None
    )
    monkeypatch.setattr(
        separate_evals, "_bounded_eval_metric_read", lambda _deadline: nullcontext()
    )
    organization = SimpleNamespace(id="org-1")
    request = SimpleNamespace(
        organization=organization,
        user=SimpleNamespace(organization=organization),
        **{
            validated_data_attribute: {
                "eval_template_id": "11111111-1111-4111-8111-111111111111",
                "filters": [],
            }
        },
    )

    response = inspect.unwrap(getattr(separate_evals.EvalMetricView, method_name))(
        separate_evals.EvalMetricView(),
        request,
    )

    assert response.status_code == 400
    assert response.data["result"] == "EvalTemplate not found"


def test_legacy_eval_template_worker_error_fails_closed(monkeypatch):
    now = datetime.now(tz=UTC)

    class UsedTemplateQuery:
        def filter(self, *_args, **_kwargs):
            return self

        def exclude(self, *_args, **_kwargs):
            return self

        def values_list(self, *_args, **_kwargs):
            return self

        def distinct(self):
            return ["template-1"]

    class LogManager:
        def filter(self, *_args, **kwargs):
            if "created_at__gte" in kwargs:
                log = {
                    "source_id": "template-1",
                    "created_at": now,
                    "updated_at": now,
                    "status": "success",
                    "config": {},
                }
                return SimpleNamespace(values=lambda *_fields: [log])
            return UsedTemplateQuery()

    monkeypatch.setattr(
        separate_evals,
        "APICallLog",
        SimpleNamespace(objects=LogManager()),
    )
    monkeypatch.setattr(
        separate_evals.SQLQueryHandler,
        "get_all_templates",
        lambda *_args, **_kwargs: [("template-1", "Template 1", 1, now.isoformat(), 1)],
    )
    monkeypatch.setattr(
        separate_evals.EvalTemplate.no_workspace_objects,
        "filter",
        lambda **_kwargs: [SimpleNamespace(id="template-1")],
    )
    monkeypatch.setattr(
        separate_evals,
        "calculate_eval_average",
        _raise_private_error,
    )
    monkeypatch.setattr(separate_evals, "wrap_for_thread", lambda function: function)
    organization = SimpleNamespace(id="org-1")
    request = SimpleNamespace(
        validated_data={},
        organization=organization,
        workspace=None,
        user=SimpleNamespace(organization=organization),
    )

    response = inspect.unwrap(separate_evals.GetEvalTemplates.post)(
        separate_evals.GetEvalTemplates(),
        request,
    )

    _assert_sanitized_400(response)
    assert response.data["result"] == "Evaluation templates could not be loaded"


def test_eval_template_bulk_delete_sanitizes_unexpected_errors(monkeypatch):
    monkeypatch.setattr(separate_evals.transaction, "atomic", _raise_private_error)
    organization = SimpleNamespace(id="org-1")
    request = SimpleNamespace(
        validated_data={"template_ids": ["11111111-1111-4111-8111-111111111111"]},
        organization=organization,
        user=SimpleNamespace(organization=organization),
    )

    response = inspect.unwrap(separate_evals.EvalTemplateBulkDeleteView.post)(
        separate_evals.EvalTemplateBulkDeleteView(),
        request,
    )

    _assert_sanitized_400(response)


def _target_method_nodes():
    module_path = Path(separate_evals.__file__)
    tree = ast.parse(module_path.read_text())
    classes = {node.name: node for node in tree.body if isinstance(node, ast.ClassDef)}
    for class_name, method_name in _TARGET_METHODS:
        class_node = classes[class_name]
        yield next(
            node
            for node in class_node.body
            if isinstance(node, ast.FunctionDef) and node.name == method_name
        )


def test_all_target_unexpected_handlers_preserve_sanitized_400_contract():
    for method in _target_method_nodes():
        outer_try = next(node for node in method.body if isinstance(node, ast.Try))
        handler = next(
            item
            for item in outer_try.handlers
            if isinstance(item.type, ast.Name) and item.type.id == "Exception"
        )

        logger_call = next(
            node
            for statement in handler.body
            for node in ast.walk(statement)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "exception"
        )
        assert any(keyword.arg == "error_type" for keyword in logger_call.keywords)

        response_call = next(
            node
            for statement in handler.body
            for node in ast.walk(statement)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "bad_request"
        )
        assert all(
            not isinstance(node, ast.Name) or node.id != handler.name
            for argument in response_call.args
            for node in ast.walk(argument)
        )


def test_trace_eval_failed_result_never_embeds_runtime_exception_text():
    source = inspect.getsource(inspect.unwrap(separate_evals.TraceEvalView.post))

    assert "reason=str(eval_error)" not in source
    assert "Evaluation could not be completed. Please retry." in source
