"""Duplicate & edit runs an experiment from before run snapshots.

Such an experiment keeps its prompts only in its inline prompt_config. Its
detail read shows them, one entry per prompt and model, and reading creates
nothing. Saving a copy whose runs name them (copy_of) saves each prompt and
model as a prompt, with the builder the conversion command uses, and the copy
runs it; a second copy finds the same prompt. A model added to or swapped
into a copied card is saved and run with the stored messages. A copy of an
experiment the caller cannot see, or of a prompt it does not have, saves
nothing, even when an earlier run of the same copy saved its prompt first. A
person's copy publishes prompt.version.created for each prompt it saves; the
conversion command publishes none. The same copy sent again answers its
receipt, and after an unconfirmed start its retry is accepted.

A saved prompt is reused only when it is in the source dataset's workspace
and its v1 holds the stored messages, and then its v1 is what runs: one of
the same name in another workspace or with other messages is not reused, and
a later edit or a draft of it is never taken. The same holds for the name of
a model swapped into the copy.

A copy is sent as a person's Duplicate sends it: by the organization's owner,
a member of its workspace, and with an evaluation (the form copies the
source's evaluations and sends no create without one). The create's own rules
take a copied prompt without a saved prompt.

The workflow start and the webhook publish are replaced and recorded.
"""

import uuid
from unittest.mock import patch

import pytest
from rest_framework.exceptions import ValidationError

from accounts.models import Organization, User
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.workspace import Workspace, WorkspaceMembership
from model_hub.management.commands.migrate_experiment_prompts import Command
from model_hub.models.choices import (
    DatasetSourceChoices,
    DataTypeChoices,
    OwnerChoices,
    SourceChoices,
    StatusType,
)
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.models.evals_metric import EvalTemplate, EvalTemplateVersion
from model_hub.models.experiments import ExperimentPromptConfig, ExperimentsTable
from model_hub.models.run_prompt import PromptTemplate, PromptVersion
from model_hub.serializers.experiments import (
    ExperimentCreateV2Serializer,
    PromptConfigEntrySerializer,
)
from model_hub.services.inline_experiment_prompts import (
    get_or_create_prompt_template,
    inline_prompt_entries,
)
from model_hub.tests.prompt_webhook_helpers import (  # noqa: F401 - fixture
    VERSION_EVENT,
    events,
    webhook_endpoint,
)
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles
from tfc.middleware.workspace_context import set_workspace_context

V2 = "/model-hub/experiments/v2/"
START = "tfc.temporal.experiments.start_experiment_v2_workflow"
PUBLISH = "model_hub.services.prompt_events.publish_version_created"
EVALUATION = "copy-output-check"
WITHOUT_A_SAVED_PROMPT = (
    "LLM prompt entries require prompt_id and prompt_version (entry 0)."
)


@pytest.fixture
def organization(db):
    return Organization.objects.create(name="Copies")


@pytest.fixture
def user(db, organization):
    """The organization's owner, with the membership row every person has."""
    user = User.objects.create_user(
        email=f"copies-{uuid.uuid4().hex[:8]}@example.com",
        password="testpassword123",
        name="Copies",
        organization=organization,
        organization_role=OrganizationRoles.OWNER,
    )
    OrganizationMembership.no_workspace_objects.get_or_create(
        user=user,
        organization=organization,
        defaults={
            "role": OrganizationRoles.OWNER,
            "level": Level.OWNER,
            "is_active": True,
        },
    )
    return user


@pytest.fixture
def workspace(db, organization, user):
    """The organization's default workspace, the owner a member of it."""
    workspace = Workspace.objects.create(
        name="Default Workspace",
        organization=organization,
        is_default=True,
        created_by=user,
    )
    membership = OrganizationMembership.no_workspace_objects.get(
        user=user, organization=organization
    )
    WorkspaceMembership.no_workspace_objects.get_or_create(
        user=user,
        workspace=workspace,
        defaults={
            "role": "Workspace Owner",
            "level": Level.OWNER,
            "is_active": True,
            "organization_membership": membership,
        },
    )
    return workspace


@pytest.fixture
def auth_client(user, workspace):
    from conftest import WorkspaceAwareAPIClient

    client = WorkspaceAwareAPIClient()
    client.force_authenticate(user=user)
    client.set_workspace(workspace)
    set_workspace_context(workspace=workspace, organization=user.organization)
    yield client
    client.stop_workspace_injection()


def dataset_in(organization, workspace):
    dataset = Dataset.objects.create(
        name="Questions",
        organization=organization,
        workspace=workspace,
        source=DatasetSourceChoices.BUILD.value,
    )
    question = Column.objects.create(
        name="question",
        dataset=dataset,
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.OTHERS.value,
    )
    dataset.column_order = [str(question.id)]
    dataset.save(update_fields=["column_order"])
    row = Row.objects.create(dataset=dataset, order=0)
    Cell.objects.create(
        dataset=dataset, column=question, row=row, value="What is two and two?"
    )
    return dataset, question


def older_experiment(user, dataset, question):
    """An experiment as the older create made it: one prompt, two models."""
    prompt = {
        "name": "Prompt A",
        "messages": [
            {"role": "user", "content": "Answer {{" + str(question.id) + "}}"},
        ],
        "model": ["gpt-4o-mini", {"name": "gpt-4o", "display_name": "GPT 4o"}],
        "configuration": {"temperature": 0.2, "tool_choice": "auto"},
        "model_params": {"gpt-4o": {"temperature": 0.7}},
    }
    return ExperimentsTable.objects.create(
        name="before-snapshots-" + uuid.uuid4().hex[:8],
        dataset=dataset,
        column=question,
        prompt_config=[prompt],
        user=user,
        status=StatusType.COMPLETED.value,
    )


def evaluator_in(organization, workspace, user):
    """A published evaluator of the workspace, for the copy's evaluation."""
    evaluator = EvalTemplate.no_workspace_objects.create(
        name=EVALUATION,
        organization=organization,
        workspace=workspace,
        owner=OwnerChoices.USER.value,
        criteria="Evaluate {{output}}",
        config={"output": "score", "eval_type_id": "Equals"},
    )
    EvalTemplateVersion.objects.create_version(
        eval_template=evaluator,
        config_snapshot=evaluator.config,
        criteria=evaluator.criteria,
        model=evaluator.model,
        user=user,
        organization=organization,
        workspace=workspace,
    )
    return evaluator


@pytest.fixture
def world(auth_client, organization, workspace, user):
    dataset, question = dataset_in(organization, workspace)
    experiment = older_experiment(user, dataset, question)
    return {
        "dataset": dataset,
        "question": question,
        "experiment": experiment,
        "evaluator": evaluator_in(organization, workspace, user),
    }


def copy_body(
    world, name, prompt_index=0, experiment_id=None, models=("gpt-4o-mini", "gpt-4o")
):
    experiment_id = experiment_id or world["experiment"].id
    return {
        "name": name,
        "dataset_id": str(world["dataset"].id),
        "experiment_type": "llm",
        "prompt_config": [
            {
                "target_type": "prompt",
                "model": model,
                "copy_of": {
                    "experiment_id": str(experiment_id),
                    "prompt_index": prompt_index,
                },
            }
            for model in models
        ],
        # A person's Duplicate carries the source's evaluations.
        "user_eval_metrics": [
            {
                "name": EVALUATION,
                "template_id": str(world["evaluator"].id),
                "config": {"mapping": {"output": "output"}},
            }
        ],
    }


def migrated(world, model):
    return f"[Migrated] {world['experiment'].name} - Prompt A - {model}"


# The stored prompt's messages, as a saved prompt names its variables.
STORED = [{"role": "user", "content": "Answer {{question}}"}]
OTHER = [{"role": "user", "content": "Answer {{question}} in one word"}]
# A model the stored prompt ran with, and one swapped into the copy.
MODELS = pytest.mark.parametrize(
    "model", ["gpt-4o", "gpt-4.1"], ids=["its-model", "swapped-model"]
)


def saved_prompt(name, organization, workspace, *versions):
    """A saved prompt with these versions, v1 first: (messages, is_draft)."""
    template = PromptTemplate.all_objects.create(
        name=name, organization=organization, workspace=workspace
    )
    made = [
        PromptVersion.all_objects.create(
            original_template=template,
            template_version=f"v{number}",
            prompt_config_snapshot={"messages": messages},
            is_draft=draft,
        )
        for number, (messages, draft) in enumerate(versions, start=1)
    ]
    return template, made


def copy_with(auth_client, world, name, model):
    """A copy whose one run copies Prompt A with ``model``."""
    body = copy_body(world, name, models=(model,))
    with patch(START, return_value="copy-workflow"):
        return auth_client.post(V2, body, format="json")


def config_of(name):
    copy = ExperimentsTable.no_workspace_objects.get(name=name)
    (config,) = ExperimentPromptConfig.objects.filter(
        experiment_dataset__experiment=copy
    )
    return config


# --- the detail shows the inline prompts ---------------------------------------


@pytest.mark.django_db
def test_its_inline_prompts_one_per_prompt_and_model(world):
    entries = inline_prompt_entries(world["experiment"])

    assert entries == [
        {
            "name": "Prompt A",
            "prompt_index": 0,
            "model": "gpt-4o-mini",
            "model_display_name": None,
            "model_params": {},
            "configuration": {"temperature": 0.2, "tool_choice": "auto"},
            "output_format": "string",
            "messages": [{"role": "user", "content": "Answer {{question}}"}],
        },
        {
            "name": "Prompt A",
            "prompt_index": 0,
            "model": "gpt-4o",
            "model_display_name": "GPT 4o",
            "model_params": {"temperature": 0.7},
            "configuration": {"temperature": 0.2, "tool_choice": "auto"},
            "output_format": "string",
            "messages": [{"role": "user", "content": "Answer {{question}}"}],
        },
    ]


@pytest.mark.django_db
def test_the_detail_read_shows_them_and_creates_nothing(auth_client, world):
    response = auth_client.get(f"{V2}{world['experiment'].id}/")

    assert response.status_code == 200, response.content
    shown = response.json()["result"]["inline_prompt_configs"]
    assert [(entry["model"], entry["prompt_index"]) for entry in shown] == [
        ("gpt-4o-mini", 0),
        ("gpt-4o", 0),
    ]
    assert shown[0]["messages"] == [{"role": "user", "content": "Answer {{question}}"}]
    assert not PromptTemplate.all_objects.filter(name__startswith="[Migrated]").exists()


@pytest.mark.django_db
def test_an_experiment_with_a_snapshot_shows_none(world, organization, workspace):
    snapshot = Dataset.objects.create(
        name="Questions snapshot",
        organization=organization,
        workspace=workspace,
        source=DatasetSourceChoices.EXPERIMENT_SNAPSHOT.value,
    )
    world["experiment"].snapshot_dataset = snapshot
    world["experiment"].save(update_fields=["snapshot_dataset"])

    assert inline_prompt_entries(world["experiment"]) == []


# --- saving a copy saves its prompts ------------------------------------------


@pytest.mark.django_db
def test_saving_a_copy_saves_each_prompt_and_model_once(auth_client, world):
    with patch(START, return_value="copy-workflow"), patch(PUBLISH) as published:
        first = auth_client.post(V2, copy_body(world, "copy-one"), format="json")
        published_by_first = published.call_count
        second = auth_client.post(V2, copy_body(world, "copy-two"), format="json")

    assert first.status_code == 200, first.content
    assert second.status_code == 200, second.content
    # The person's own copy: one prompt.version.created per prompt it saved;
    # the second copy finds them and publishes nothing more.
    assert published_by_first == 2
    assert published.call_count == 2
    templates = PromptTemplate.all_objects.filter(name__startswith="[Migrated]")
    names = sorted(templates.values_list("name", flat=True))
    assert names == sorted([migrated(world, "gpt-4o-mini"), migrated(world, "gpt-4o")])
    for template in templates:
        (version,) = PromptVersion.all_objects.filter(original_template=template)
        assert version.template_version == "v1"
        assert version.prompt_config_snapshot["messages"] == [
            {"role": "user", "content": "Answer {{question}}"}
        ]
    for name in ("copy-one", "copy-two"):
        copy = ExperimentsTable.no_workspace_objects.get(name=name)
        assert copy.snapshot_dataset_id
        configs = ExperimentPromptConfig.objects.filter(
            experiment_dataset__experiment=copy
        )
        pairs = {(config.model, config.prompt_template.name) for config in configs}
        assert pairs == {
            ("gpt-4o-mini", migrated(world, "gpt-4o-mini")),
            ("gpt-4o", migrated(world, "gpt-4o")),
        }


@pytest.mark.django_db
def test_a_prompt_it_does_not_have_saves_nothing(auth_client, world):
    with patch(START, return_value="copy-workflow") as started:
        response = auth_client.post(
            V2, copy_body(world, "copy-missing", prompt_index=5), format="json"
        )

    assert response.status_code == 400, response.content
    assert "The prompt to copy was not found." in response.content.decode()
    started.assert_not_called()
    assert not PromptTemplate.all_objects.filter(name__startswith="[Migrated]").exists()
    missing = ExperimentsTable.no_workspace_objects.filter(name="copy-missing")
    assert not missing.exists()


@pytest.mark.django_db
def test_a_copy_of_an_experiment_it_cannot_see_saves_nothing(
    auth_client, world, organization, user
):
    elsewhere = Workspace.objects.create(
        name="Elsewhere", organization=organization, created_by=user
    )
    dataset, question = dataset_in(organization, elsewhere)
    hidden = older_experiment(user, dataset, question)

    with patch(START, return_value="copy-workflow") as started:
        response = auth_client.post(
            V2,
            copy_body(world, "copy-hidden", experiment_id=hidden.id),
            format="json",
        )

    assert response.status_code == 404, response.content
    started.assert_not_called()
    assert not PromptTemplate.all_objects.filter(name__startswith="[Migrated]").exists()
    hidden_copy = ExperimentsTable.no_workspace_objects.filter(name="copy-hidden")
    assert not hidden_copy.exists()


@pytest.mark.django_db
@pytest.mark.usefixtures("webhook_endpoint")
def test_a_copy_refused_at_a_later_run_keeps_nothing(auth_client, world):
    body = copy_body(world, "copy-rolled-back")
    # Its first run copies a prompt the experiment has, and is saved first;
    # its second names a prompt the experiment does not have.
    body["prompt_config"][1]["copy_of"]["prompt_index"] = 5
    with patch(START, return_value="copy-workflow") as started:
        response = auth_client.post(V2, body, format="json")

    assert response.status_code == 400, response.content
    assert "The prompt to copy was not found." in response.content.decode()
    started.assert_not_called()
    assert not PromptTemplate.all_objects.filter(name__startswith="[Migrated]").exists()
    rolled_back = ExperimentsTable.no_workspace_objects.filter(name="copy-rolled-back")
    assert not rolled_back.exists()
    assert events(VERSION_EVENT) == []


@pytest.mark.django_db
@pytest.mark.usefixtures("webhook_endpoint")
def test_a_saved_copy_writes_one_version_event_per_prompt(auth_client, world):
    with patch(START, return_value="copy-workflow"):
        response = auth_client.post(
            V2, copy_body(world, "copy-with-events"), format="json"
        )

    assert response.status_code == 200, response.content
    assert len(events(VERSION_EVENT)) == 2


@pytest.mark.django_db
@pytest.mark.parametrize(
    "models",
    [("gpt-4o-mini", "gpt-4.1"), ("gpt-4o-mini", "gpt-4o", "gpt-4.1")],
    ids=["swapped", "added"],
)
def test_a_copy_with_a_model_its_prompt_did_not_have_saves_and_runs_it(
    auth_client, world, models
):
    body = copy_body(world, "copy-new-model", models=models)
    with patch(START, return_value="copy-workflow") as started, patch(PUBLISH):
        response = auth_client.post(V2, body, format="json")

    assert response.status_code == 200, response.content
    started.assert_called_once()
    template = PromptTemplate.all_objects.get(name=migrated(world, "gpt-4.1"))
    (version,) = PromptVersion.all_objects.filter(original_template=template)
    assert version.template_version == "v1"
    # The stored experiment's prompt, with the copy's own model.
    assert version.prompt_config_snapshot["messages"] == [
        {"role": "user", "content": "Answer {{question}}"}
    ]
    assert version.prompt_config_snapshot["configuration"]["model"] == "gpt-4.1"
    copy = ExperimentsTable.no_workspace_objects.get(name="copy-new-model")
    configs = ExperimentPromptConfig.objects.filter(experiment_dataset__experiment=copy)
    pairs = {(config.model, config.prompt_template.name) for config in configs}
    assert pairs == {(model, migrated(world, model)) for model in models}


@pytest.mark.django_db
@pytest.mark.parametrize("key", [None, "copy-sent-again"], ids=["no-key", "key"])
def test_the_same_copy_sent_again_answers_its_receipt(auth_client, world, key):
    headers = {"HTTP_IDEMPOTENCY_KEY": key} if key else {}
    body = copy_body(world, "copy-again")
    with patch(START, return_value="copy-workflow") as started:
        first = auth_client.post(V2, body, format="json", **headers)
        again = auth_client.post(V2, body, format="json", **headers)

    assert first.status_code == 200, first.content
    assert again.status_code == 200, again.content
    assert again.json() == first.json()
    started.assert_called_once()
    assert ExperimentsTable.no_workspace_objects.filter(name="copy-again").count() == 1
    migrated_prompts = PromptTemplate.all_objects.filter(name__startswith="[Migrated]")
    assert migrated_prompts.count() == 2


@pytest.mark.django_db
@pytest.mark.parametrize("key", [None, "copy-retried"], ids=["no-key", "key"])
def test_a_copy_retried_after_an_unconfirmed_start_is_accepted(auth_client, world, key):
    headers = {"HTTP_IDEMPOTENCY_KEY": key} if key else {}
    body = copy_body(world, "copy-retried")
    with patch(START, side_effect=TimeoutError("unconfirmed start")) as failed:
        first = auth_client.post(V2, body, format="json", **headers)

    assert first.status_code == 503, first.content
    assert first.json()["code"] == "experiment_admission_unconfirmed"
    copy = ExperimentsTable.no_workspace_objects.get(name="copy-retried")

    with patch(START, return_value="copy-workflow") as started:
        retried = auth_client.post(V2, body, format="json", **headers)

    assert retried.status_code == 200, retried.content
    assert str(copy.id) in retried.json()["result"]
    started.assert_called_once()
    assert started.call_args == failed.call_args
    retried_copies = ExperimentsTable.no_workspace_objects.filter(name="copy-retried")
    assert retried_copies.count() == 1
    migrated_prompts = PromptTemplate.all_objects.filter(name__startswith="[Migrated]")
    assert migrated_prompts.count() == 2


# --- which saved prompt a copy reuses -------------------------------------------


@pytest.mark.django_db
@MODELS
def test_one_of_that_name_in_another_workspace_is_not_reused(
    auth_client, world, organization, user, model
):
    elsewhere = Workspace.objects.create(
        name="Elsewhere", organization=organization, created_by=user
    )
    theirs, _ = saved_prompt(
        migrated(world, model), organization, elsewhere, (STORED, False)
    )

    response = copy_with(auth_client, world, "copy-workspace", model)

    assert response.status_code == 200, response.content
    config = config_of("copy-workspace")
    assert config.prompt_template_id != theirs.id
    assert config.prompt_template.name == migrated(world, model)
    assert config.prompt_template.workspace_id == world["dataset"].workspace_id
    assert config.prompt_version.template_version == "v1"
    assert config.prompt_version.prompt_config_snapshot["messages"] == STORED
    assert PromptVersion.all_objects.filter(original_template=theirs).count() == 1


@pytest.mark.django_db
@MODELS
def test_one_of_that_name_with_other_messages_is_not_reused(
    auth_client, world, organization, workspace, model
):
    theirs, _ = saved_prompt(
        migrated(world, model), organization, workspace, (OTHER, False)
    )

    response = copy_with(auth_client, world, "copy-messages", model)

    assert response.status_code == 200, response.content
    config = config_of("copy-messages")
    assert config.prompt_template_id != theirs.id
    assert config.prompt_template.name == migrated(world, model)
    assert config.prompt_version.prompt_config_snapshot["messages"] == STORED


@pytest.mark.django_db
@MODELS
def test_a_later_edit_of_the_saved_prompt_is_not_taken(
    auth_client, world, organization, workspace, model
):
    template, (first, _later) = saved_prompt(
        migrated(world, model), organization, workspace, (STORED, False), (OTHER, False)
    )

    response = copy_with(auth_client, world, "copy-later-edit", model)

    assert response.status_code == 200, response.content
    config = config_of("copy-later-edit")
    assert config.prompt_template_id == template.id
    assert config.prompt_version_id == first.id


@pytest.mark.django_db
@MODELS
def test_a_draft_of_the_saved_prompt_is_not_taken(
    auth_client, world, organization, workspace, model
):
    template, (first, _draft) = saved_prompt(
        migrated(world, model), organization, workspace, (STORED, False), (OTHER, True)
    )

    response = copy_with(auth_client, world, "copy-draft", model)

    assert response.status_code == 200, response.content
    config = config_of("copy-draft")
    assert config.prompt_template_id == template.id
    assert config.prompt_version_id == first.id


@pytest.mark.django_db
def test_an_edit_does_not_take_a_copied_prompt(auth_client, world):
    with patch(START, return_value="copy-workflow"):
        created = auth_client.post(V2, copy_body(world, "copy-edit"), format="json")
    assert created.status_code == 200, created.content
    copy = ExperimentsTable.no_workspace_objects.get(name="copy-edit")

    with patch(START, return_value="copy-workflow") as started:
        response = auth_client.put(
            f"{V2}{copy.id}/",
            {"prompt_config": copy_body(world, "unused")["prompt_config"]},
            format="json",
        )

    assert response.status_code == 400, response.content
    body = response.content.decode()
    assert "A copied prompt is saved when its copy is created." in body
    started.assert_not_called()


@pytest.mark.django_db
def test_the_create_rules_take_a_copied_prompt_without_a_saved_prompt():
    copied = PromptConfigEntrySerializer(
        data={
            "model": "gpt-4o",
            "copy_of": {"experiment_id": str(uuid.uuid4()), "prompt_index": 0},
        }
    )
    assert copied.is_valid(), copied.errors
    plain = PromptConfigEntrySerializer(data={"model": "gpt-4o"})
    assert plain.is_valid(), plain.errors
    rules = ExperimentCreateV2Serializer()

    data = {"experiment_type": "llm", "prompt_config": [copied.validated_data]}
    assert rules.validate(data)["prompt_config"] == [copied.validated_data]
    # An entry with neither a copied nor a saved prompt is still refused.
    with pytest.raises(ValidationError) as refused:
        rules.validate(
            {"experiment_type": "llm", "prompt_config": [plain.validated_data]}
        )
    assert str(refused.value.detail["prompt_config"]) == WITHOUT_A_SAVED_PROMPT


# --- one builder ----------------------------------------------------------------


@pytest.mark.django_db
def test_the_conversion_command_builds_with_the_same_function(world):
    messages = [{"role": "user", "content": "Answer {{question}}"}]
    shared = get_or_create_prompt_template(
        world["experiment"], "Prompt A", messages, {}, "string", "gpt-4o-mini"
    )
    command = Command()._get_or_create_prompt_template(
        world["experiment"], "Prompt A", messages, {}, "string", "gpt-4o-mini"
    )

    assert command == shared
    kept = PromptTemplate.all_objects.filter(name=migrated(world, "gpt-4o-mini"))
    assert kept.count() == 1


@pytest.mark.django_db
def test_only_the_conversion_command_suppresses_the_version_event(world):
    messages = [{"role": "user", "content": "Answer {{question}}"}]
    with patch(PUBLISH) as published:
        Command()._get_or_create_prompt_template(
            world["experiment"], "Converted", messages, {}, "string", "gpt-4o"
        )
        by_command = published.call_count
        get_or_create_prompt_template(
            world["experiment"], "Copied", messages, {}, "string", "gpt-4o"
        )

    assert by_command == 0
    assert published.call_count == 1
