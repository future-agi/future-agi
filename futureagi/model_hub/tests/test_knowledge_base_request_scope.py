"""Knowledge-base references are resolved before saving or scheduling work."""

import json
import sys
import uuid
from contextlib import ExitStack
from types import ModuleType
from unittest.mock import MagicMock, patch

import pytest

from accounts.models.workspace import Workspace
from model_hub.models.develop_dataset import Cell, Dataset, KnowledgeBaseFile
from model_hub.models.eval_groups import EvalGroup
from model_hub.models.evals_metric import EvalTemplate, Feedback, UserEvalMetric
from model_hub.tests.test_dataset_list_tenancy import tenants as tenants_fixture
from tfc.middleware.workspace_context import clear_workspace_context

pytestmark = pytest.mark.django_db
tenants = tenants_fixture


def template_for(tenant):
    clear_workspace_context()
    return EvalTemplate.no_workspace_objects.create(
        name=f"scope-template-{uuid.uuid4().hex[:8]}",
        organization=tenant["org"], workspace=tenant["workspace"], owner="user",
        criteria="Evaluate {{output}}",
        config={"required_keys": [], "mapping": {}, "custom_eval": True,
                "output": "Pass/Fail", "eval_type_id": "CustomPromptEvaluator"},
    )


def kb_for(a, b, scope):
    owner = b if scope == "organization" else a
    workspace = owner["workspace"]
    if scope == "workspace":
        workspace = Workspace.objects.create(
            name="Other workspace", organization=a["org"], created_by=a["user"],
            is_default=False,
        )
    clear_workspace_context()
    kb = KnowledgeBaseFile.no_workspace_objects.create(
        name="Reference KB", organization=owner["org"], workspace=workspace,
        deleted=scope == "deleted",
    )
    return uuid.uuid4() if scope == "unknown" else kb.id


@pytest.mark.parametrize("scope", ["own", "organization", "workspace", "unknown", "deleted"])
@pytest.mark.parametrize("endpoint", ["create_eval", "edit_eval", "playground", "group", "feedback"])
def test_knowledge_base_reference_scope(tenants, scope, endpoint):
    a, b, clients = tenants
    kb_id = kb_for(a, b, scope)
    template = template_for(a)
    dataset = a["dataset"]
    payload = {"name": "scoped-evaluation", "template_id": str(template.id),
               "config": {"mapping": {}}, "model": "gpt-4o-mini", "kb_id": str(kb_id)}
    metric = None
    if endpoint == "create_eval":
        url = f"/model-hub/develops/{dataset.id}/add_user_eval/"
    elif endpoint == "edit_eval":
        metric = UserEvalMetric.no_workspace_objects.create(
            name="Editable evaluation", organization=a["org"], workspace=a["workspace"],
            dataset=dataset, template=template, user=a["user"], config={"mapping": {}},
        )
        url = f"/model-hub/develops/{dataset.id}/edit_and_run_user_eval/{metric.id}/"
    elif endpoint == "playground":
        url = "/model-hub/eval-playground/"
        payload = {k: payload[k] for k in ("template_id", "model", "kb_id")}
    elif endpoint == "group":
        group = EvalGroup.no_workspace_objects.create(
            name="Reference group", organization=a["org"], workspace=a["workspace"],
            created_by=a["user"],
        )
        url = "/model-hub/eval-groups/apply-eval-group/"
        payload = {"eval_group_id": str(group.id), "page_id": "DATASET", "mapping": {},
                   "filters": {"dataset_id": str(dataset.id), "kb_id": str(kb_id)}}
    else:
        from ee.usage.models.usage import APICallLog
        from model_hub.models.choices import SourceChoices
        from tfc.constants.api_calls import APICallStatusChoices

        log = APICallLog.objects.create(
            organization=a["org"], workspace=a["workspace"],
            status=APICallStatusChoices.SUCCESS.value, cost=0,
            source=SourceChoices.EVAL_PLAYGROUND.value, source_id=str(template.id),
            config=json.dumps({"required_keys": [], "mappings": {}, "input_data_types": {},
                               "output": {"output": "Passed", "reason": "fixture"},
                               "model": "gpt-4o-mini", "kb_id": str(kb_id)}),
        )
        url = "/model-hub/eval-playground/feedback/"
        payload = {"log_id": str(log.log_id), "action_type": "recalculate",
                   "value": "failed", "explanation": "Recalculate"}

    before = list(UserEvalMetric.no_workspace_objects.order_by("id").values())
    feedback_count = Feedback.objects.count()
    template_count = EvalTemplate.no_workspace_objects.count()
    with patch("model_hub.views.separate_evals.run_eval_func", return_value={"output": "Passed", "reason": "fixture", "log_id": None}) as run, patch("model_hub.views.eval_group.apply_eval_group", return_value={}) as apply, patch("model_hub.views.separate_evals.run_eval_func_task.delay") as dispatch, patch("model_hub.views.separate_evals.EmbeddingManager") as embeddings:
        embeddings.return_value.data_formatter.return_value = ([], [])
        response = clients["a"].post(url, payload, format="json")
    assert response.status_code == (200 if scope == "own" else 404), response.data
    if scope != "own":
        assert list(UserEvalMetric.no_workspace_objects.order_by("id").values()) == before
        assert Feedback.objects.count() == feedback_count
        assert EvalTemplate.no_workspace_objects.count() == template_count
        run.assert_not_called()
        apply.assert_not_called()
        dispatch.assert_not_called()
    elif endpoint == "create_eval":
        assert UserEvalMetric.no_workspace_objects.filter(dataset=dataset, kb_id=kb_id).exists()
    elif endpoint == "edit_eval":
        metric.refresh_from_db()
        assert metric.kb_id == kb_id
    elif endpoint == "playground":
        assert str(run.call_args.kwargs["kb_id"]) == str(kb_id)
    elif endpoint == "group":
        apply.assert_called_once()
    else:
        dispatch.assert_called_once()


@pytest.mark.parametrize("scope", ["own", "organization", "workspace", "unknown", "deleted"])
@pytest.mark.parametrize("endpoint", ["create", "update", "add_rows"])
def test_synthetic_knowledge_base_reference_scope(tenants, scope, endpoint):
    a, b, clients = tenants
    kb_id = kb_for(a, b, scope)
    dataset = a["dataset"]
    dataset.synthetic_dataset_config = {"initial": True}
    dataset.save(update_fields=["synthetic_dataset_config"])
    payload = {
        "num_rows": 10, "kb_id": str(kb_id),
        "dataset": {"name": "Scoped synthetic", "description": "", "objective": "", "patterns": ""},
        "columns": [{"name": "question", "data_type": "text", "description": "", "property": {}}],
    }
    module = ModuleType("ee.agenthub.synthetic_data_agent.synthetic_data_agent")
    module.SyntheticDataAgent = MagicMock()
    module_name = "model_hub.views.datasets.create.synthetic"
    before = list(Cell.all_objects.order_by("id").values())
    dataset_count = Dataset.no_workspace_objects.count()
    with ExitStack() as stack:
        stack.enter_context(patch.dict(sys.modules, {module.__name__: module}))
        stack.enter_context(patch(f"{module_name}.log_and_deduct_cost_for_resource_request", None))
        tasks = stack.enter_context(patch(f"{module_name}.SyntheticTaskManager"))
        dispatch = stack.enter_context(patch(f"{module_name}.create_synthetic_dataset.delay"))
        stack.enter_context(patch(f"{module_name}.generate_new_rows.delay"))
        stack.enter_context(patch(f"{module_name}.generate_new_columns.delay"))
        stack.enter_context(patch("ee.usage.services.entitlements.Entitlements.check_feature", return_value=MagicMock(allowed=True)))
        stack.enter_context(patch("model_hub.views.datasets.add_rows.synthetic.generate_new_rows.delay"))
        stack.enter_context(patch("model_hub.views.datasets.add_rows.synthetic.generate_new_columns.delay"))
        if endpoint == "create":
            response = clients["a"].post("/model-hub/develops/create-synthetic-dataset/", payload, format="json")
        elif endpoint == "update":
            response = clients["a"].put(f"/model-hub/develops/{dataset.id}/update-synthetic-config/", payload, format="json")
        else:
            response = clients["a"].post(f"/model-hub/develops/{dataset.id}/add_synthetic_data/", payload, format="json")
    assert response.status_code == (200 if scope == "own" else 404), response.data
    if scope != "own":
        assert Dataset.no_workspace_objects.count() == dataset_count
        dataset.refresh_from_db()
        assert dataset.synthetic_dataset_config == {"initial": True}
        assert list(Cell.all_objects.order_by("id").values()) == before
        tasks.assert_not_called()
        dispatch.assert_not_called()
