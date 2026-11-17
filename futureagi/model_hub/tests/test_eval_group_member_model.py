"""An evaluation applied through a group always has a model.

The Apply dialog sends no model. Each member is then applied with the model
its own template is configured with; without one, with Add evaluation's
default under the rule Add evaluation applies to it: its Jev check, then its
Turing check. Where that rule refuses the model, only that member is
refused, with a sentence that names the evaluation and the model, and the
others are applied. The model check comes before the mapping is read. A
model the caller sends is used for every member, as before. Covered on
every page that stores a model, through the real apply-eval-group view. The
group read names each member's model.

The sentence says why by where the model came from, in this order: a model
the request sent, the group's model to change; a built-in evaluation's own
model, that it uses a model which can't run here (each place adds the action
it offers, by the refusal's code); anything else, a blank model included,
the evaluation's model to change. Each refusal carries a code beside its
sentence: "built_in" or "model".

Whether this deployment has the Turing models, and Jev, is pinned in each
test.
"""

import uuid
from unittest.mock import patch

import pytest
from rest_framework import status

from model_hub.models.ai_model import AIModel
from model_hub.models.choices import (
    DatasetSourceChoices,
    DataTypeChoices,
    ModelTypes,
    OwnerChoices,
    SourceChoices,
    StatusType,
)
from model_hub.models.develop_dataset import Column, Dataset
from model_hub.models.eval_groups import EvalGroup
from model_hub.models.evals_metric import EvalTemplate, UserEvalMetric
from model_hub.models.experiments import ExperimentsTable
from model_hub.services.eval_group import create_eval_group
from model_hub.tests.test_function_params_surfaces import (
    dataset_for_eval as dataset_for_eval,
)
from model_hub.tests.test_function_params_surfaces import (
    rag_function_template as rag_function_template,
)
from simulate.models.eval_config import SimulateEvalConfig
from simulate.models.run_test import RunTest
from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.models.project import Project

APPLY = "/model-hub/eval-groups/apply-eval-group/"
TURING_DENIED = "tfc.ee_gates._turing_denied_off_cloud"
JEV_AVAILABLE = "tfc.ee_gates.jev_available"
JEV_DENIED = "tfc.ee_gates._jev_denied_off_cloud"
DEFAULT_MODEL = "turing_large"
PAGES = ["DATASET", "EXPERIMENT", "EVAL_TASK", "SIMULATE"]


def judge_template(user, workspace, *, model=None, config_model=None):
    config = {
        "eval_type_id": "CustomPromptEvaluator",
        "required_keys": ["output"],
        "output": "Pass/Fail",
    }
    if config_model:
        config["model"] = config_model
    return EvalTemplate.objects.create(
        name=f"group-model-{uuid.uuid4().hex[:12]}",
        owner=OwnerChoices.USER.value,
        organization=user.organization,
        workspace=workspace,
        eval_type="llm",
        criteria="Is {{output}} polite?",
        config=config,
        model=model,
    )


@pytest.fixture
def page_targets(user, workspace):
    """One target per page that stores a model, and how to read back what
    was stored there."""
    dataset = Dataset.objects.create(
        name="Group model dataset",
        organization=user.organization,
        workspace=workspace,
        user=user,
        source=DatasetSourceChoices.BUILD.value,
        model_type=ModelTypes.GENERATIVE_LLM.value,
        column_order=[],
    )
    column = Column.objects.create(
        dataset=dataset,
        name="answer",
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.OTHERS.value,
    )
    experiment = ExperimentsTable.objects.create(
        name="Group model experiment",
        dataset=dataset,
        column=column,
        status=StatusType.COMPLETED.value,
    )
    project = Project.objects.create(
        name="Group model project",
        organization=user.organization,
        workspace=workspace,
        user=user,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM.value,
        trace_type="observe",
    )
    run_test = RunTest.objects.create(
        name="Group model simulation",
        organization=user.organization,
        workspace=workspace,
    )

    def stored(page, group):
        if page == "DATASET":
            rows = UserEvalMetric.objects.filter(
                eval_group=group, dataset=dataset, deleted=False
            ).values_list("template_id", "model")
        elif page == "EXPERIMENT":
            rows = UserEvalMetric.objects.filter(
                eval_group=group, source_id=str(experiment.id), deleted=False
            ).values_list("template_id", "model")
        elif page == "EVAL_TASK":
            rows = CustomEvalConfig.objects.filter(
                eval_group=group, project=project, deleted=False
            ).values_list("eval_template_id", "model")
        else:
            rows = SimulateEvalConfig.objects.filter(
                eval_group=group, run_test=run_test, deleted=False
            ).values_list("eval_template_id", "model")
        return {str(template_id): model for template_id, model in rows}

    filters = {
        "DATASET": {"dataset_id": str(dataset.id)},
        "EXPERIMENT": {"experiment_id": str(experiment.id)},
        "EVAL_TASK": {"project_id": str(project.id)},
        "SIMULATE": {"simulate_id": str(run_test.id)},
    }
    mappings = {
        "DATASET": {"output": str(column.id)},
        "EXPERIMENT": {"output": str(column.id)},
        "EVAL_TASK": {"output": "output"},
        "SIMULATE": {"output": "output"},
    }
    return {
        "dataset": dataset,
        "filters": filters,
        "mappings": mappings,
        "stored": stored,
    }


def group_of(user, workspace, templates):
    """A group made the way the Evals page makes one (with its members'
    history, which the group read lists them by)."""
    created = create_eval_group(
        f"group-model-{uuid.uuid4().hex[:12]}",
        "",
        [str(template.id) for template in templates],
        user,
        workspace,
    )
    return EvalGroup.no_workspace_objects.get(id=created["id"])


def apply_group(auth_client, page_targets, page, group, model=None):
    filters = dict(page_targets["filters"][page])
    if model is not None:
        filters["model"] = model
    return auth_client.post(
        APPLY,
        {
            "eval_group_id": str(group.id),
            "page_id": page,
            "filters": filters,
            "mapping": page_targets["mappings"][page],
        },
        format="json",
    )


@pytest.mark.django_db
@pytest.mark.parametrize("page", PAGES)
def test_a_member_is_applied_with_the_model_its_template_names(
    auth_client, user, workspace, page_targets, page
):
    on_template = judge_template(user, workspace, model="gpt-4o-mini")
    in_config = judge_template(user, workspace, config_model="claude-3-5-haiku")
    group = group_of(user, workspace, [on_template, in_config])

    with patch(TURING_DENIED, return_value=False):
        response = apply_group(auth_client, page_targets, page, group)

    assert response.status_code == status.HTTP_200_OK, response.content
    assert page_targets["stored"](page, group) == {
        str(on_template.id): "gpt-4o-mini",
        str(in_config.id): "claude-3-5-haiku",
    }
    assert "refused" not in response.json()


@pytest.mark.django_db
@pytest.mark.parametrize("page", PAGES)
def test_a_member_whose_template_names_no_model_gets_add_evaluations_default(
    auth_client, user, workspace, page_targets, page
):
    plain = judge_template(user, workspace)
    group = group_of(user, workspace, [plain])

    with patch(TURING_DENIED, return_value=False):
        response = apply_group(auth_client, page_targets, page, group)

    assert response.status_code == status.HTTP_200_OK, response.content
    assert page_targets["stored"](page, group) == {str(plain.id): DEFAULT_MODEL}


EVALUATION_MODEL = "Change the evaluation's model"
GROUP_MODEL = "Change the group's model"


def reason(model, change=EVALUATION_MODEL):
    """Why a member is left out; ``model`` as the Apply dialog names it."""
    return (
        f"its model, {model}, can't run on this deployment. {change}, then apply again."
    )


def not_added(template, model, change=EVALUATION_MODEL):
    """After Apply: the member left out, by name."""
    return f"{template.name} was not added: {reason(model, change)}"


def wont_be_added(model, change=EVALUATION_MODEL):
    """Before Apply, under the member's row."""
    return f"Won't be added: {reason(model, change)}"


def every_one_of(*sentences):
    """The answer when every member is left out: each named, in either order."""
    first, second = sentences
    return {f"{first} {second}", f"{second} {first}"}


def add_evaluation_refusal(auth_client, page_targets, template, model=DEFAULT_MODEL):
    """What Add evaluation answers for ``model`` here: (status, message)."""
    dataset = page_targets["dataset"]
    response = auth_client.post(
        f"/model-hub/develops/{dataset.id}/add_user_eval/",
        {
            "name": "default-check",
            "template_id": str(template.id),
            "config": {"mapping": {}},
            "model": model,
        },
        format="json",
    )
    return response.status_code, response.json()["message"]


@pytest.mark.django_db
def test_add_evaluation_says_turing_cant_run_on_this_deployment(
    auth_client, user, workspace, page_targets
):
    plain = judge_template(user, workspace)

    with patch(TURING_DENIED, return_value=True):
        answer = add_evaluation_refusal(auth_client, page_targets, plain)

    assert answer == (
        402,
        "Turing and Protect models can't run on this deployment. Select a "
        "different model (OpenAI, Anthropic, etc.) or upgrade your plan.",
    )


@pytest.mark.django_db
@pytest.mark.parametrize("page", PAGES)
def test_where_add_evaluation_refuses_the_default_only_that_member_is_refused(
    auth_client, user, workspace, page_targets, page
):
    own = judge_template(user, workspace, model="gpt-4o-mini")
    plain = judge_template(user, workspace)
    group = group_of(user, workspace, [own, plain])

    with patch(TURING_DENIED, return_value=True):
        response = apply_group(auth_client, page_targets, page, group)

    assert response.status_code == status.HTTP_200_OK, response.content
    assert page_targets["stored"](page, group) == {str(own.id): "gpt-4o-mini"}
    assert response.json()["refused"] == [
        {
            "eval_template_id": str(plain.id),
            "name": plain.name,
            "reason": not_added(plain, "Turing Large"),
            "code": "model",
        }
    ]


@pytest.mark.django_db
@pytest.mark.parametrize("page", PAGES)
def test_when_every_member_is_refused_nothing_is_applied(
    auth_client, user, workspace, page_targets, page
):
    plain = judge_template(user, workspace)
    group = group_of(user, workspace, [plain])

    with patch(TURING_DENIED, return_value=True):
        response = apply_group(auth_client, page_targets, page, group)

    assert response.status_code == status.HTTP_400_BAD_REQUEST, response.content
    assert response.json()["message"] == not_added(plain, "Turing Large")
    assert page_targets["stored"](page, group) == {}


@pytest.mark.django_db
@pytest.mark.parametrize("page", PAGES)
def test_a_model_the_caller_sends_is_used_for_every_member(
    auth_client, user, workspace, page_targets, page
):
    """Where the deployment allows the sent model: green before and after."""
    own = judge_template(user, workspace, model="gpt-4o-mini")
    plain = judge_template(user, workspace)
    group = group_of(user, workspace, [own, plain])

    with patch(TURING_DENIED, return_value=False):
        response = apply_group(
            auth_client, page_targets, page, group, model="turing_small"
        )

    assert response.status_code == status.HTTP_200_OK, response.content
    assert page_targets["stored"](page, group) == {
        str(own.id): "turing_small",
        str(plain.id): "turing_small",
    }


@pytest.mark.django_db
@pytest.mark.parametrize("denied", [False, True])
def test_the_group_read_names_the_model_apply_uses_for_each_member(
    auth_client, user, workspace, page_targets, denied
):
    own = judge_template(user, workspace, model="gpt-4o-mini")
    plain = judge_template(user, workspace)
    group = group_of(user, workspace, [own, plain])

    with patch(TURING_DENIED, return_value=denied):
        response = auth_client.get(f"/model-hub/eval-groups/{group.id}/")

    assert response.status_code == status.HTTP_200_OK, response.content
    members = {
        member["eval_template_id"]: member
        for member in response.json()["result"]["members"]
    }
    assert members[str(own.id)]["apply_model"] == "gpt-4o-mini"
    assert members[str(own.id)]["apply_refusal"] is None
    assert members[str(own.id)]["apply_refusal_code"] is None
    if denied:
        assert members[str(plain.id)]["apply_model"] is None
        assert members[str(plain.id)]["apply_refusal"] == wont_be_added("Turing Large")
        assert members[str(plain.id)]["apply_refusal_code"] == "model"
    else:
        assert members[str(plain.id)]["apply_model"] == DEFAULT_MODEL
        assert members[str(plain.id)]["apply_refusal"] is None
        assert members[str(plain.id)]["apply_refusal_code"] is None


@pytest.mark.django_db
@pytest.mark.parametrize("page", PAGES)
def test_a_template_naming_a_turing_model_is_refused_where_turing_is_not_available(
    auth_client, user, workspace, page_targets, page
):
    """The model Apply would store is checked, not only the default."""
    turing = judge_template(user, workspace, model="turing_small")
    own = judge_template(user, workspace, model="gpt-4o-mini")
    group = group_of(user, workspace, [turing, own])

    with patch(TURING_DENIED, return_value=True):
        response = apply_group(auth_client, page_targets, page, group)

    assert response.status_code == status.HTTP_200_OK, response.content
    assert page_targets["stored"](page, group) == {str(own.id): "gpt-4o-mini"}
    assert response.json()["refused"] == [
        {
            "eval_template_id": str(turing.id),
            "name": turing.name,
            "reason": not_added(turing, "Turing Small"),
            "code": "model",
        }
    ]


@pytest.mark.django_db
@pytest.mark.parametrize("page", PAGES)
def test_a_sent_turing_model_is_refused_where_turing_is_not_available(
    auth_client, user, workspace, page_targets, page
):
    """As Add evaluation refuses the request for that model, every member that
    would store it is refused: the answer is the refusal and nothing is stored.
    """
    own = judge_template(user, workspace, model="gpt-4o-mini")
    plain = judge_template(user, workspace)
    group = group_of(user, workspace, [own, plain])

    with patch(TURING_DENIED, return_value=True):
        response = apply_group(
            auth_client, page_targets, page, group, model="turing_flash"
        )

    assert response.status_code == status.HTTP_400_BAD_REQUEST, response.content
    # Every member is named; the model came with the request.
    assert response.json()["message"] in every_one_of(
        not_added(own, "Turing Flash", GROUP_MODEL),
        not_added(plain, "Turing Flash", GROUP_MODEL),
    )
    assert page_targets["stored"](page, group) == {}


@pytest.mark.django_db
def test_the_group_read_shows_the_refusal_for_a_template_turing_model(
    auth_client, user, workspace, page_targets
):
    turing = judge_template(user, workspace, model="turing_small")
    group = group_of(user, workspace, [turing])

    with patch(TURING_DENIED, return_value=True):
        response = auth_client.get(f"/model-hub/eval-groups/{group.id}/")

    assert response.status_code == status.HTTP_200_OK, response.content
    (member,) = response.json()["result"]["members"]
    assert member["eval_template_id"] == str(turing.id)
    assert member["apply_model"] is None
    assert member["apply_refusal"] == wont_be_added("Turing Small")


# --- the Jev half of Add evaluation's check -----------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize("page", PAGES)
def test_a_sent_unknown_jev_model_is_refused_as_add_evaluation_refuses_it(
    auth_client, user, workspace, page_targets, page
):
    own = judge_template(user, workspace, model="gpt-4o-mini")
    plain = judge_template(user, workspace)
    group = group_of(user, workspace, [own, plain])

    with patch(TURING_DENIED, return_value=False):
        add_status, _ = add_evaluation_refusal(
            auth_client, page_targets, plain, model="jev-9.9.9"
        )
        response = apply_group(
            auth_client, page_targets, page, group, model="jev-9.9.9"
        )

    # Add evaluation refuses the model; so does the group, for every member.
    assert add_status == status.HTTP_400_BAD_REQUEST
    assert response.status_code == status.HTTP_400_BAD_REQUEST, response.content
    assert response.json()["message"] in every_one_of(
        not_added(own, "jev-9.9.9", GROUP_MODEL),
        not_added(plain, "jev-9.9.9", GROUP_MODEL),
    )
    assert page_targets["stored"](page, group) == {}


@pytest.mark.django_db
@pytest.mark.parametrize("page", PAGES)
def test_a_template_naming_an_unknown_jev_model_is_left_out(
    auth_client, user, workspace, page_targets, page
):
    unknown = judge_template(user, workspace, model="jev-9.9.9")
    own = judge_template(user, workspace, model="gpt-4o-mini")
    group = group_of(user, workspace, [unknown, own])

    with patch(TURING_DENIED, return_value=False):
        response = apply_group(auth_client, page_targets, page, group)

    assert response.status_code == status.HTTP_200_OK, response.content
    assert page_targets["stored"](page, group) == {str(own.id): "gpt-4o-mini"}
    assert response.json()["refused"] == [
        {
            "eval_template_id": str(unknown.id),
            "name": unknown.name,
            "reason": not_added(unknown, "jev-9.9.9"),
            "code": "model",
        }
    ]


@pytest.mark.django_db
@pytest.mark.parametrize("page", PAGES)
def test_a_jev_template_is_left_out_on_a_build_without_jev(
    auth_client, user, workspace, page_targets, page
):
    jev = judge_template(user, workspace, model="jev-latest")
    own = judge_template(user, workspace, model="gpt-4o-mini")
    group = group_of(user, workspace, [jev, own])

    with (
        patch(TURING_DENIED, return_value=False),
        patch(JEV_AVAILABLE, return_value=False),
        patch(JEV_DENIED, return_value=False),
    ):
        response = apply_group(auth_client, page_targets, page, group)

    assert response.status_code == status.HTTP_200_OK, response.content
    assert page_targets["stored"](page, group) == {str(own.id): "gpt-4o-mini"}
    assert response.json()["refused"] == [
        {
            "eval_template_id": str(jev.id),
            "name": jev.name,
            "reason": not_added(jev, "jev-latest"),
            "code": "model",
        }
    ]


@pytest.mark.django_db
def test_the_model_is_checked_before_the_mapping(
    auth_client, user, workspace, rag_function_template, dataset_for_eval
):
    """A payload whose model and mapping are both refused gets the model's
    sentence, and nothing is stored. With the model allowed, the same payload
    is refused for its mapping."""
    dataset, _hypothesis_col, reference_col = dataset_for_eval
    group = EvalGroup.objects.create(
        name=f"group-model-order-{uuid.uuid4().hex[:12]}",
        organization=user.organization,
        workspace=workspace,
        created_by=user,
    )
    group.eval_templates.add(rag_function_template)
    body = {
        "eval_group_id": str(group.id),
        "page_id": "DATASET",
        "filters": {"dataset_id": str(dataset.id)},
        "mapping": {
            "hypothesis": {"source": "span_attribute", "column_id": "output.value"},
            "reference": str(reference_col.id),
        },
    }

    with patch(TURING_DENIED, return_value=True):
        refused = auth_client.post(APPLY, body, format="json")
    with patch(TURING_DENIED, return_value=False):
        mapping_refused = auth_client.post(APPLY, body, format="json")

    assert refused.status_code == status.HTTP_400_BAD_REQUEST, refused.content
    assert refused.json()["message"] == built_in_not_added(
        rag_function_template, "Turing Large"
    )
    assert "attribute path strings" not in str(refused.json())
    assert mapping_refused.status_code == status.HTTP_400_BAD_REQUEST
    assert "attribute path strings" in str(mapping_refused.json())
    assert not UserEvalMetric.objects.filter(
        eval_group=group, dataset=dataset, deleted=False
    ).exists()


def test_both_sentences_share_one_reason_clause():
    from model_hub.services import eval_group

    clause = eval_group.GROUP_MEMBER_MODEL_REASON.format(
        model="Turing Large", change=eval_group.CHANGE_EVALUATION_MODEL
    )
    assert clause == (
        "its model, Turing Large, can't run on this deployment. Change the "
        "evaluation's model, then apply again."
    )
    not_added_line = eval_group.GROUP_MEMBER_NOT_ADDED.format(
        evaluation="Tone", reason=clause
    )
    assert not_added_line == (
        "Tone was not added: its model, Turing Large, can't run on this "
        "deployment. Change the evaluation's model, then apply again."
    )
    assert eval_group.GROUP_MEMBER_WONT_BE_ADDED.format(reason=clause) == (
        "Won't be added: its model, Turing Large, can't run on this deployment. "
        "Change the evaluation's model, then apply again."
    )
    assert eval_group.model_display_name("turing_large") == "Turing Large"
    assert eval_group.model_display_name("gpt-4o-mini") == "gpt-4o-mini"


@pytest.mark.django_db
@pytest.mark.parametrize("page", PAGES)
def test_a_refused_member_of_a_group_whose_model_was_sent_names_the_groups_model(
    auth_client, user, workspace, page_targets, page
):
    """A model the request sends is the group's (the drawer's Language Model
    field); one the evaluation names is the evaluation's."""
    turing = judge_template(user, workspace, model="turing_small")
    group = group_of(user, workspace, [turing])

    with patch(TURING_DENIED, return_value=True):
        sent = apply_group(auth_client, page_targets, page, group, model="turing_flash")
        own = apply_group(auth_client, page_targets, page, group)

    assert sent.json()["message"] == not_added(turing, "Turing Flash", GROUP_MODEL)
    assert own.json()["message"] == not_added(turing, "Turing Small")
    assert page_targets["stored"](page, group) == {}


# --- what the sentence says, by where the model came from --------------------


def system_template(*, model=None):
    """A built-in evaluation: its page reads "Read-only (system eval)"."""
    return EvalTemplate.no_workspace_objects.create(
        name=f"group-model-system-{uuid.uuid4().hex[:12]}",
        owner=OwnerChoices.SYSTEM.value,
        organization=None,
        workspace=None,
        eval_type="llm",
        criteria="Is {{output}} polite?",
        config={
            "eval_type_id": "CustomPromptEvaluator",
            "required_keys": ["output"],
            "output": "Pass/Fail",
        },
        model=model,
    )


def built_in_not_added(template, model):
    """After Apply: a built-in evaluation left out, named once."""
    return f"{template.name} was not added: it uses {model}, which can't run here."


def test_the_built_in_reason_word_for_word():
    from model_hub.services import eval_group

    built_in = eval_group.BUILT_IN_MEMBER_REASON.format(model="Turing Large")
    assert built_in == "it uses Turing Large, which can't run here."
    after = eval_group.GROUP_MEMBER_NOT_ADDED.format(evaluation="Tone", reason=built_in)
    assert after == "Tone was not added: it uses Turing Large, which can't run here."
    before = eval_group.GROUP_MEMBER_WONT_BE_ADDED.format(reason=built_in)
    assert before == "Won't be added: it uses Turing Large, which can't run here."
    assert eval_group.REFUSAL_BUILT_IN == "built_in"
    assert eval_group.REFUSAL_MODEL == "model"
    # The Language Model sentence is gone.
    assert not hasattr(eval_group, "GROUP_MEMBER_SELECT_MODEL_REASON")


@pytest.mark.django_db
@pytest.mark.parametrize("page", PAGES)
def test_a_built_in_member_left_out_is_named_once_with_its_model(
    auth_client, user, workspace, page_targets, page
):
    """After a partial success the reason says it all: the rest was applied."""
    built_in = system_template()
    own = judge_template(user, workspace, model="gpt-4o-mini")
    group = group_of(user, workspace, [built_in, own])

    with patch(TURING_DENIED, return_value=True):
        response = apply_group(auth_client, page_targets, page, group)

    assert response.status_code == status.HTTP_200_OK, response.content
    assert page_targets["stored"](page, group) == {str(own.id): "gpt-4o-mini"}
    assert response.json()["refused"] == [
        {
            "eval_template_id": str(built_in.id),
            "name": built_in.name,
            "reason": built_in_not_added(built_in, "Turing Large"),
            "code": "built_in",
        }
    ]


@pytest.mark.django_db
def test_the_group_read_gives_a_built_in_members_reason_and_code_before_apply(
    auth_client, user, workspace
):
    built_in = system_template(model="turing_small")
    group = group_of(user, workspace, [built_in])

    with patch(TURING_DENIED, return_value=True):
        response = auth_client.get(f"/model-hub/eval-groups/{group.id}/")

    assert response.status_code == status.HTTP_200_OK, response.content
    (member,) = response.json()["result"]["members"]
    assert member["eval_template_id"] == str(built_in.id)
    assert member["apply_model"] is None
    assert member["apply_refusal"] == (
        "Won't be added: it uses Turing Small, which can't run here."
    )
    assert member["apply_refusal_code"] == "built_in"


@pytest.mark.django_db
@pytest.mark.parametrize("page", PAGES)
def test_when_every_member_is_refused_the_answer_lists_each_with_its_code(
    auth_client, user, workspace, page_targets, page
):
    """The 400 keeps its joined sentence and lists each member with its code,
    so each place can add the action it offers to a built-in one."""
    plain = judge_template(user, workspace)
    built_in = system_template()
    group = group_of(user, workspace, [plain, built_in])

    with patch(TURING_DENIED, return_value=True):
        response = apply_group(auth_client, page_targets, page, group)

    assert response.status_code == status.HTTP_400_BAD_REQUEST, response.content
    body = response.json()
    assert body["message"] in every_one_of(
        not_added(plain, "Turing Large"), built_in_not_added(built_in, "Turing Large")
    )
    refused = sorted(body["refused"], key=lambda entry: entry["code"])
    assert refused == [
        {
            "eval_template_id": str(built_in.id),
            "name": built_in.name,
            "reason": built_in_not_added(built_in, "Turing Large"),
            "code": "built_in",
        },
        {
            "eval_template_id": str(plain.id),
            "name": plain.name,
            "reason": not_added(plain, "Turing Large"),
            "code": "model",
        },
    ]
    assert page_targets["stored"](page, group) == {}


@pytest.mark.django_db
@pytest.mark.parametrize("page", PAGES)
def test_a_blank_model_gets_the_evaluations_model_clause(
    auth_client, user, workspace, page_targets, page
):
    """A request that sends its model blank sent no model: the general
    clause, exactly."""
    plain = judge_template(user, workspace)
    group = group_of(user, workspace, [plain])

    with patch(TURING_DENIED, return_value=True):
        response = apply_group(auth_client, page_targets, page, group, model="")

    assert response.status_code == status.HTTP_400_BAD_REQUEST, response.content
    assert response.json()["message"] == (
        f"{plain.name} was not added: its model, Turing Large, can't run on this "
        "deployment. Change the evaluation's model, then apply again."
    )
    assert page_targets["stored"](page, group) == {}


@pytest.mark.django_db
@pytest.mark.parametrize("page", PAGES)
def test_a_built_in_member_with_a_blank_model_gets_its_own_reason(
    auth_client, user, workspace, page_targets, page
):
    """In order: a model the request sent; a built-in evaluation; anything
    else. A blank model is not a sent model, so a built-in member keeps its
    reason."""
    built_in = system_template()
    group = group_of(user, workspace, [built_in])

    with patch(TURING_DENIED, return_value=True):
        response = apply_group(auth_client, page_targets, page, group, model="")

    assert response.status_code == status.HTTP_400_BAD_REQUEST, response.content
    assert response.json()["message"] == built_in_not_added(built_in, "Turing Large")
    assert response.json()["refused"][0]["code"] == "built_in"
    assert page_targets["stored"](page, group) == {}


@pytest.mark.django_db
@pytest.mark.parametrize("page", PAGES)
def test_a_built_in_member_with_a_sent_model_names_the_groups_model(
    auth_client, user, workspace, page_targets, page
):
    """A model the request sent comes first: the group's model, for a
    built-in evaluation too."""
    built_in = system_template()
    group = group_of(user, workspace, [built_in])

    with patch(TURING_DENIED, return_value=True):
        response = apply_group(
            auth_client, page_targets, page, group, model="turing_flash"
        )

    assert response.status_code == status.HTTP_400_BAD_REQUEST, response.content
    assert response.json()["message"] == not_added(
        built_in, "Turing Flash", GROUP_MODEL
    )
    assert response.json()["refused"][0]["code"] == "model"
    assert page_targets["stored"](page, group) == {}


@pytest.mark.parametrize(
    ("model", "name"),
    [
        ("turing_large", "Turing Large"),
        ("turing_small", "Turing Small"),
        ("turing_flash", "Turing Flash"),
        ("protect", "protect"),
        ("protect_flash", "protect_flash"),
        ("turing_large_xl", "turing_large_xl"),
        ("jev-latest", "jev-latest"),
    ],
)
def test_the_sentences_name_a_model_by_the_backends_map(model, name):
    """The backend's map names the three Turing models; any other model,
    Protect, Protect Flash and Turing Large XL among them, keeps its id."""
    from model_hub.services import eval_group

    assert eval_group.model_display_name(model) == name
