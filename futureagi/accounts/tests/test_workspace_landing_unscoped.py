"""Explicitly unscoped selectors must not widen a denied authenticated landing."""

import importlib
import inspect

import pytest

from accounts.authentication import APIKeyAuthentication
from accounts.tests.test_workspace_landing_boundary import (
    authenticate_data_request,
)
from accounts.tests.test_workspace_landing_boundary import (
    boundary as _boundary_fixture,
)
from accounts.tests.test_workspace_landing_boundary import (
    landing as _landing_fixture,
)
from model_hub.models.choices import (
    DataTypeChoices,
    FeedbackSourceChoices,
    SourceChoices,
)
from model_hub.models.develop_dataset import Column, Dataset, Row
from model_hub.models.evals_metric import Feedback
from model_hub.views import develop_dataset
from tfc.middleware.workspace_context import clear_workspace_context

boundary = _boundary_fixture
landing = _landing_fixture

# Each entry was an unbounded Q() for an absent workspace. Test the predicates
# independently of the managers, so either layer's regression is observable.
PREDICATES = (
    "accounts.views.user:_workspace_scope_q",
    "simulate.views.scoping:run_test_workspace_filter",
    "model_hub.queries.tts_voices:_workspace_scope",
    "model_hub.utils.workspace_scope:request_workspace_filter",
    "model_hub.serializers.annotation_queues:AnnotationQueueSerializer._workspace_visibility_q",
    "model_hub.serializers.prompt_base_template:_workspace_filter",
    "model_hub.serializers.dataset_optimization:_workspace_filter",
    "model_hub.views.develop_annotations:AnnotationsViewSet._workspace_filter",
    "model_hub.views.separate_evals:_request_workspace_filter",
    "model_hub.views.run_prompt:_request_workspace_filter",
    "model_hub.views.develop_dataset:_request_workspace_filter",
    "model_hub.views.experiments:_request_workspace_filter",
    "model_hub.views.eval_runner:_request_workspace_filter",
    "model_hub.views.prompt_base_template:PromptBaseTemplateViewSet._workspace_filter",
    "model_hub.views.dataset_optimization:_request_workspace_filter",
    "model_hub.views.dynamic_columns:_request_workspace_filter",
    "model_hub.views.datasets.add_rows.synthetic:_request_workspace_filter",
    "model_hub.views.datasets.add_rows.existing_dataset:_request_workspace_filter",
    "model_hub.views.datasets.add_rows.huggingface:_request_workspace_filter",
    "model_hub.views.datasets.create.synthetic:_request_workspace_filter",
    "model_hub.views.datasets.create.dataset_from_experiment:_request_workspace_filter",
    "model_hub.views.datasets.create.file_upload:_request_workspace_filter",
    "tracer.views.project_version:_project_workspace_scope_q",
    "tracer.views.monitor:UserAlertMonitorView._workspace_scope_q",
    "tracer.views.monitor:UserAlertMonitorLogView._workspace_scope_q",
    "tracer.views.monitor:UserAlertMonitorLogView._alert_workspace_scope_q",
    "tracer.views.dataset:_workspace_scope_q",
    "tracer.views.shared_link:_workspace_scope_q",
    "tracer.views.project:ProjectView._workspace_scope_q",
    "tracer.selectors.eval_tasks.scope:project_workspace_scope_q",
)


def call_predicate(identity, request):
    module, name = identity.split(":")
    parts = name.split(".")
    target = getattr(importlib.import_module(module), parts[0])
    if len(parts) == 2:
        instance = target()
        if hasattr(instance, "fields"):  # DRF serializer request context
            instance._context = {"request": request}
        else:
            instance.request = request
        target = getattr(instance, parts[1])
    values = {
        "request": request,
        "workspace": None,
        "organization": request.organization,
        "organization_id": request.organization.id,
        "field_name": "workspace",
        "lookup": "workspace",
    }
    arguments = {
        name: values[name]
        for name, parameter in inspect.signature(target).parameters.items()
        if parameter.default is inspect.Parameter.empty or name == "request"
    }
    return target(**arguments)


@pytest.mark.parametrize("boundary", ["absent"], indirect=True)
@pytest.mark.parametrize("identity", PREDICATES)
def test_empty_landing_predicate_denies_private_workspace(boundary, identity):
    request = authenticate_data_request(boundary)
    APIKeyAuthentication()._set_workspace_context(request, boundary.user)
    request.user = boundary.user
    assert request.workspace is None
    predicate = call_predicate(identity, request)
    # The base manager intentionally bypasses the product manager's guard here.
    assert Dataset._base_manager.filter(pk=boundary.hidden.pk).exists()
    assert not Dataset._base_manager.filter(predicate, pk=boundary.hidden.pk).exists()
    assert not boundary.user.can_access_workspace(boundary.landing.other)


@pytest.fixture
def payloads(boundary):
    column = Column.objects.create(
        name="Private column",
        dataset=boundary.hidden,
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.OTHERS.value,
    )
    row = Row.objects.create(dataset=boundary.hidden, order=0)
    feedback = Feedback.no_workspace_objects.create(
        source=FeedbackSourceChoices.DATASET.value,
        source_id=str(boundary.hidden.pk),
        user=boundary.landing.owner,
        organization=boundary.landing.org,
        workspace=boundary.landing.other,
        value="Failed",
        explanation="Private feedback",
    )
    return {
        "dataset": boundary.hidden,
        "column": column,
        "row": row,
        "feedback": feedback,
    }


@pytest.mark.parametrize("boundary", ["absent", "deactivated"], indirect=True)
@pytest.mark.parametrize("kind", ["dataset", "column", "row", "feedback"])
@pytest.mark.parametrize("manager", ["objects", "all_objects", "no_workspace_objects"])
def test_empty_landing_confines_direct_and_related_managers(
    boundary, payloads, kind, manager
):
    instance = payloads[kind]
    request = authenticate_data_request(boundary)
    APIKeyAuthentication()._set_workspace_context(request, boundary.user)
    queryset = getattr(type(instance), manager).filter(pk=instance.pk)
    assert not queryset.exists()
    assert queryset.update(deleted=True) == 0
    assert queryset.delete()[0] == 0
    instance.refresh_from_db()
    assert instance.deleted is False
    clear_workspace_context()
    assert getattr(type(instance), manager).filter(pk=instance.pk).exists()


@pytest.mark.parametrize("boundary", ["absent", "deactivated"], indirect=True)
def test_real_auth_feedback_read_is_empty(boundary, payloads):
    response = develop_dataset.FeedbackViewSet.as_view({"get": "get_feedback_details"})(
        authenticate_data_request(boundary)
    )
    assert response.status_code == 200, response.data
    payload = response.data.get("result", response.data)
    assert payload["feedback"] == []
    assert not boundary.user.can_access_workspace(boundary.landing.other)


@pytest.mark.parametrize("boundary", ["absent"], indirect=True)
@pytest.mark.parametrize("kind", ["dataset", "column", "row", "feedback"])
def test_develop_request_selectors_are_empty(boundary, payloads, kind):
    request = authenticate_data_request(boundary)
    APIKeyAuthentication()._set_workspace_context(request, boundary.user)
    request.user = boundary.user
    selector = getattr(develop_dataset, f"_request_{kind}_queryset")
    assert not selector(request).filter(pk=payloads[kind].pk).exists()


@pytest.mark.parametrize("boundary", ["absent"], indirect=True)
def test_access_metadata_remains_available_after_empty_landing(boundary):
    request = authenticate_data_request(boundary)
    APIKeyAuthentication()._set_workspace_context(request, boundary.user)
    # Scope denial must not alter permission metadata or another user's access.
    assert boundary.user.can_access_organization(boundary.landing.org)
    assert boundary.landing.user.can_access_workspace(boundary.landing.other)
    APIKeyAuthentication()._set_workspace_context(request, boundary.landing.user)
    assert request.workspace is not None
    assert boundary.landing.user.can_access_workspace(request.workspace)
