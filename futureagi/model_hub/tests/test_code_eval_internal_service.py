"""A code eval that calls the platform's model serving ends as a clear ERROR.

The code executor's egress guard denies private and cluster addresses, so
code evals cannot reach model serving. The built-in Embedding Similarity and
Semantic List Contains evals were such code until they moved into the
backend, and a user's copy of either keeps it. The copied code caught the
connection error and scored 0.0, which looks like a real result. The run now
ends as an ERROR: "this eval calls an internal service that custom code can't
reach; use the built-in <name> eval". The user's eval is never rewritten,
by the run or by re-seeding the system evals.

The copies here are the built-ins' code verbatim, as seeded at
SYSTEM_EVALS_VERSION 28 (``model_hub/system_evals/tests/legacy_serving_evals``).
The code sandbox is stubbed with what hosted answered for that code: success,
score 0.0.
"""

from __future__ import annotations

import copy
import uuid
from pathlib import Path
from unittest import mock

import pytest
import yaml
from django.core.cache import cache
from rest_framework import status

from agentic_eval.core_evals.fi_evals.function.functions import custom_code_eval
from agentic_eval.core_evals.fi_utils.exceptions import CodeEvalSetupError
from evaluations.engine.runner import EvalRequest, run_eval
from model_hub.management.commands import seed_system_evals
from model_hub.management.commands.seed_system_evals import (
    load_yaml_evals,
    seed_evals,
)
from model_hub.models.choices import (
    CellStatus,
    DatasetSourceChoices,
    DataTypeChoices,
    ModelTypes,
    OwnerChoices,
    SourceChoices,
)
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.models.evals_metric import EvalTemplate, UserEvalMetric
from model_hub.views.eval_runner import EvaluationRunner
from tfc.utils.error_codes import get_specific_error_message

LEGACY_DIR = (
    Path(__file__).resolve().parents[1]
    / "system_evals"
    / "tests"
    / "legacy_serving_evals"
)
BUILT_INS = ["embedding_similarity", "semantic_list_contains"]
EXECUTE = "agentic_eval.core_evals.fi_evals.function.functions.CodeExecution.execute"
# The code executor's answer on hosted: the eval body caught its own
# connection error and returned 0.0.
HOSTED_ANSWER = {
    "status": "success",
    "data": {
        "result": 0.0,
        "reason": "Embedding error (text/text): <urlopen error [Errno 111] "
        "Connection refused>",
    },
}


def _message(name):
    return (
        "this eval calls an internal service that custom code can't reach; "
        f"use the built-in {name} eval"
    )


def _legacy_config(name):
    return yaml.safe_load((LEGACY_DIR / f"{name}.yaml.txt").read_text())["config"]


@pytest.fixture
def sandbox():
    with mock.patch(EXECUTE, return_value=HOSTED_ANSWER) as execute:
        yield execute


@pytest.fixture
def user_copy(db, organization, workspace):
    """A user's copy of a built-in, made before the built-ins moved. A copy
    may keep the built-in's own name (``same_name``)."""

    def make(name, same_name=False):
        return EvalTemplate.no_workspace_objects.create(
            name=name if same_name else f"my-{name.replace('_', '-')}-copy",
            organization=organization,
            workspace=workspace,
            owner=OwnerChoices.USER.value,
            eval_type="code",
            config=_legacy_config(name),
            visible_ui=True,
            output_type_normalized="percentage",
            pass_threshold=0.5,
        )

    return make


@pytest.mark.parametrize("name", BUILT_INS)
def test_a_copied_built_in_is_refused_before_it_runs(name, sandbox):
    with pytest.raises(CodeEvalSetupError) as raised:
        custom_code_eval(
            _legacy_config(name)["code"],
            output="refund issued",
            expected="money returned",
        )

    assert str(raised.value) == _message(name)
    sandbox.assert_not_called()


def test_code_calling_the_configured_serving_address_is_refused(sandbox, monkeypatch):
    monkeypatch.setenv("MODEL_SERVING_URL", "http://ml.internal:9000")
    code = (
        "def evaluate(output, expected, **kwargs):\n"
        "    import urllib.request\n"
        "    urllib.request.urlopen('http://ml.internal:9000/model/v1/embed')\n"
        "    return 1.0\n"
    )

    with pytest.raises(CodeEvalSetupError) as raised:
        custom_code_eval(code, output="a", expected="b")

    assert str(raised.value) == _message("embedding_similarity")
    sandbox.assert_not_called()


@pytest.mark.parametrize(
    "code",
    [
        # An external API on the internet, which the sandbox can reach.
        "def evaluate(output, **kwargs):\n"
        "    import urllib.request\n"
        "    urllib.request.urlopen('https://api.example.com/score')\n"
        "    return 1.0\n",
        # A public host whose name merely starts with "serving".
        "def evaluate(output, **kwargs):\n"
        "    url = 'https://serving.example.com/model/v1/embed'\n"
        "    return 1.0\n",
        # The word alone.
        "def evaluate(output, **kwargs):\n"
        "    return {'score': 1.0, 'reason': 'model serving is fine'}\n",
    ],
    ids=["external-api", "public-serving-host", "the-word"],
)
def test_other_code_still_runs(code, sandbox):
    assert custom_code_eval(code, output="a")["result"] == 0.0
    sandbox.assert_called_once()


@pytest.mark.django_db
@pytest.mark.parametrize("name", BUILT_INS)
def test_run_eval_ends_a_copy_as_an_error(name, user_copy, sandbox, organization):
    """Observe tasks and the SDK run evals through run_eval."""
    template = user_copy(name)
    before = copy.deepcopy(template.config)

    with pytest.raises(CodeEvalSetupError) as raised:
        run_eval(
            EvalRequest(
                eval_template=template,
                inputs={"output": "refund issued", "expected": "money returned"},
                organization_id=str(organization.id),
            )
        )

    assert str(raised.value) == _message(name)
    sandbox.assert_not_called()
    template.refresh_from_db()
    assert template.config == before


# A copy that kept the built-in's name still selects that name's serving
# preflight, which every surface runs after building the evaluator and before
# running it. The copy must be refused first: with serving down, the preflight
# would report "Model serving is not running" instead.


@pytest.mark.django_db
@pytest.mark.parametrize("name", BUILT_INS)
def test_run_eval_refuses_a_same_name_copy_without_serving(
    name, user_copy, sandbox, organization, model_serving_down
):
    template = user_copy(name, same_name=True)

    with pytest.raises(CodeEvalSetupError) as raised:
        run_eval(
            EvalRequest(
                eval_template=template,
                inputs={"output": "refund issued", "expected": "money returned"},
                organization_id=str(organization.id),
            )
        )

    assert str(raised.value) == _message(name)
    sandbox.assert_not_called()


def _run_dataset_row(template, organization, user, workspace):
    """Run ``template`` on one dataset row, as a dataset column run does."""
    dataset = Dataset.objects.create(
        name=f"internal-service-{uuid.uuid4().hex[:8]}",
        organization=organization,
        user=user,
        source=DatasetSourceChoices.BUILD.value,
        model_type=ModelTypes.GENERATIVE_LLM.value,
        column_order=[],
        column_config={},
        workspace=workspace,
    )
    row = Row.objects.create(dataset=dataset, order=0)
    mapping = {}
    for key, value in (("output", "Refunds take five days."), ("expected", "a, b")):
        column = Column.objects.create(
            dataset=dataset,
            name=f"{key}-column",
            data_type=DataTypeChoices.TEXT.value,
            source=SourceChoices.OTHERS.value,
        )
        Cell.objects.create(
            dataset=dataset,
            row=row,
            column=column,
            value=value,
            status=CellStatus.PASS.value,
        )
        mapping[key] = str(column.id)
    metric = UserEvalMetric.objects.create(
        name=f"internal-service-{uuid.uuid4().hex[:8]}",
        dataset=dataset,
        template=template,
        user=user,
        workspace=workspace,
        organization=organization,
        model="turing_large",
        config={"mapping": mapping},
    )
    runner = EvaluationRunner(
        user_eval_metric_id=str(metric.id),
        is_only_eval=True,
        format_output=True,
        organization_id=organization.id,
        workspace_id=workspace.id,
    )
    runner.user_eval_metric = metric
    runner.eval_template = template
    runner.dataset = dataset
    runner.column = mock.MagicMock(id=uuid.uuid4())
    return runner._run_evaluation(row, mapping, config={})


@pytest.mark.django_db
def test_a_dataset_cell_shows_the_error(
    user_copy, sandbox, organization, user, workspace
):
    template = user_copy("semantic_list_contains")

    with pytest.raises(CodeEvalSetupError) as raised:
        _run_dataset_row(template, organization, user, workspace)

    # _process_eval_result stores this text on the cell, with status error.
    assert get_specific_error_message(raised.value) == _message(
        "semantic_list_contains"
    )
    sandbox.assert_not_called()


@pytest.mark.django_db
def test_a_dataset_cell_refuses_a_same_name_copy_without_serving(
    user_copy, sandbox, organization, user, workspace, model_serving_down
):
    template = user_copy("semantic_list_contains", same_name=True)

    with pytest.raises(CodeEvalSetupError) as raised:
        _run_dataset_row(template, organization, user, workspace)

    assert get_specific_error_message(raised.value) == _message(
        "semantic_list_contains"
    )
    sandbox.assert_not_called()


def _post_playground(auth_client, template):
    return auth_client.post(
        "/model-hub/eval-playground/",
        {
            "template_id": str(template.id),
            "model": "",
            "mapping": {"output": "refund issued", "expected": "money returned"},
            "config": {"params": {}},
        },
        format="json",
    )


@pytest.mark.django_db
def test_the_playground_shows_the_error(user_copy, sandbox, auth_client):
    response = _post_playground(auth_client, user_copy("embedding_similarity"))

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert _message("embedding_similarity") in str(response.data)
    sandbox.assert_not_called()


@pytest.mark.django_db
def test_the_playground_refuses_a_same_name_copy_without_serving(
    user_copy, sandbox, auth_client, model_serving_down
):
    response = _post_playground(
        auth_client, user_copy("embedding_similarity", same_name=True)
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert _message("embedding_similarity") in str(response.data)
    sandbox.assert_not_called()


@pytest.mark.django_db
def test_seeding_never_rewrites_a_copy_that_kept_a_system_eval_id(
    user_copy, monkeypatch
):
    """A copy duplicates every field, eval_id included. The seeder matches
    templates on eval_id, and must update only the system's own rows."""
    template = user_copy("embedding_similarity")
    template.eval_id = 108
    template.save(update_fields=["eval_id"])
    before = EvalTemplate.no_workspace_objects.filter(pk=template.pk).values().get()
    definitions = [d for d in load_yaml_evals() if d["name"] in BUILT_INS]
    monkeypatch.setattr(
        seed_system_evals, "load_yaml_evals", lambda: copy.deepcopy(definitions)
    )
    # The version cache is process-wide, not rolled back with the test.
    previous = cache.get("system_evals_version")
    try:
        seed_evals(force=True)
    finally:
        if previous is None:
            cache.delete("system_evals_version")
        else:
            cache.set("system_evals_version", previous, timeout=None)

    after = EvalTemplate.no_workspace_objects.filter(pk=template.pk).values().get()
    assert after == before
    system = EvalTemplate.no_workspace_objects.get(
        eval_id=108,
        owner=OwnerChoices.SYSTEM.value,
        organization__isnull=True,
        workspace__isnull=True,
    )
    assert system.config["eval_type_id"] == "EmbeddingSimilarity"
