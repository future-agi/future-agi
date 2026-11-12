"""External (Langfuse) evals while a deployment pauses custom code evaluations
(CUSTOM_CODE_EVALS_PAUSED).

While it is on, an external eval of a customer's custom code template fails
with the fixed sentence before anything is charged or sent to the code
executor. So does a composite with any customer custom code child: nothing is
charged for the composite, and no child is charged or run. A built-in template,
whose code is a saved system template's own, is charged and runs as before, and
so is a composite of built-in children, and every template with the setting
off. The executor client (urllib in fi_utils.sandbox) and the cost logs are what
the tests watch, through the real run_external_eval_config.
"""

import importlib
import json
import uuid
from types import SimpleNamespace

import pytest
from django.test import override_settings

from agentic_eval.core_evals.fi_utils import sandbox
from model_hub.models.evals_metric import CompositeEvalChild, EvalTemplate
from model_hub.utils.eval_list import derive_eval_type, infer_composite_eval_type
from tfc.constants.api_calls import APICallStatusChoices
from tracer.models.external_eval_config import (
    ExternalEvalConfig,
    PlatformChoices,
    StatusChoices,
)
from tracer.utils import external_eval

SENTENCE = "Custom code evaluations are temporarily unavailable on Future AGI Cloud."
BUILTIN_CODE = (
    "def evaluate(output, expected):\n    return 1.0 if output == expected else 0.0\n"
)
BUILTIN_LENGTH_CODE = (
    "def evaluate(output, expected):\n"
    "    return 1.0 if len(output) == len(expected) else 0.0\n"
)
CUSTOMER_CODE = "def evaluate(output, expected):\n    return 0.5\n"
INPUTS = {"output": "4", "expected": "4"}


def pause_module():
    return importlib.import_module("agentic_eval.core_evals.fi_utils.custom_code_pause")


class _Response:
    def __init__(self, body):
        self.body = body

    def read(self, limit=-1):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def executor(monkeypatch):
    """Every request the executor client sends, answered with a score."""
    sent = []

    def urlopen(request, timeout=None):
        sent.append(json.loads(request.data))
        answer = {"status": "success", "data": {"result": 1.0, "reason": "scored"}}
        return _Response(json.dumps(answer).encode())

    monkeypatch.setattr(sandbox.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(sandbox, "CODE_EXECUTOR_URL", "http://code-executor.test:8060")
    return sent


@pytest.fixture(autouse=True)
def fresh_system_code(monkeypatch):
    try:
        monkeypatch.setattr(pause_module(), "_system_code_cache", None)
    except ImportError:
        pass


def _recorded_charge(charges):
    """A cost log that records each charge and lets the run go on: a row in
    PROCESSING whose config is stored as JSON, as run_eval_func reads it back
    to record the child's output."""

    def log_and_deduct(**kwargs):
        charges.append(kwargs)
        return SimpleNamespace(
            status=APICallStatusChoices.PROCESSING.value,
            log_id=uuid.uuid4(),
            config=json.dumps(kwargs.get("config") or {}, default=str),
            save=lambda *args, **kw: None,
        )

    return log_and_deduct


@pytest.fixture
def billing(monkeypatch):
    """Every charge the external eval path makes: the cost rows it logs and
    the usage events it emits, for the config itself and, on a composite, for
    each child (run_eval_func charges a child where it runs). The usage
    pre-check (Redis-backed with ee/) always allows, and the Langfuse send
    does nothing."""
    charged = []
    emitted = []
    child_charged = []
    child_events = []

    metering = importlib.import_module("ee.usage.services.metering")
    monkeypatch.setattr(
        metering,
        "check_usage",
        lambda *args, **kwargs: SimpleNamespace(allowed=True, reason=""),
    )
    monkeypatch.setattr(
        external_eval, "log_and_deduct_cost_for_api_request", _recorded_charge(charged)
    )
    monkeypatch.setattr(
        external_eval,
        "_emit_external_eval_usage",
        lambda config: emitted.append(str(config.id)),
    )
    monkeypatch.setattr(
        "model_hub.views.utils.evals.log_and_deduct_cost_for_api_request",
        _recorded_charge(child_charged),
    )
    monkeypatch.setattr(
        "ee.usage.services.emitter.emit", lambda event: child_events.append(event)
    )
    monkeypatch.setattr(external_eval, "_send_eval_result", lambda *args: None)
    return SimpleNamespace(
        charged=charged,
        emitted=emitted,
        child_charged=child_charged,
        child_events=child_events,
    )


def system_code_template(name, code):
    template = EvalTemplate.no_workspace_objects.create(
        name=name,
        owner="system",
        organization=None,
        workspace=None,
        eval_type="code",
        config={"eval_type_id": "CustomCodeEval", "code": code},
    )
    # save() stamps the request's organization and workspace on a row saved while a
    # workspace context is set; a built-in has neither, so clear them without save().
    EvalTemplate.no_workspace_objects.filter(pk=template.pk).update(
        organization=None, workspace=None
    )
    template.refresh_from_db()
    return template


@pytest.fixture
def builtin_template(db):
    return system_code_template("pause_external_builtin_code", BUILTIN_CODE)


@pytest.fixture
def second_builtin_template(db):
    # A db-only fixture listed before organization/workspace: a row saved after the
    # workspace context is set would be stamped with that organization.
    return system_code_template("pause_external_builtin_length", BUILTIN_LENGTH_CODE)


def customer_template(
    organization, workspace, name="pause_external_customer_code", **fields
):
    return EvalTemplate.no_workspace_objects.create(
        name=name,
        owner="user",
        organization=organization,
        workspace=workspace,
        eval_type="code",
        config={"eval_type_id": "CustomCodeEval", "code": CUSTOMER_CODE},
        **fields,
    )


def composite_template(
    organization, workspace, children, name="pause_external_composite"
):
    """A composite saved as the composite create view saves one: the parent's
    own config is empty and its eval type comes from its children, and each
    child is linked in order with the default weight, no pinned version and no
    link config."""
    parent = EvalTemplate.no_workspace_objects.create(
        name=name,
        organization=organization,
        workspace=workspace,
        owner="user",
        eval_tags=[],
        config={},
        description="",
        template_type="composite",
        eval_type=infer_composite_eval_type(
            derive_eval_type(child) for child in children
        ),
        visible_ui=True,
    )
    for order, child in enumerate(children):
        CompositeEvalChild.objects.create(
            parent=parent,
            child=child,
            order=order,
            weight=1.0,
            pinned_version=None,
            config={},
        )
    return parent


def external_config(organization, workspace, template):
    # The config refuses to save without Langfuse settings; the send is
    # patched, so they are never used.
    credentials = dict.fromkeys(
        ExternalEvalConfig.get_required_credentials(PlatformChoices.LANGFUSE), "unused"
    )
    return ExternalEvalConfig.objects.create(
        organization=organization,
        workspace=workspace,
        eval_template=template,
        name="pause-external",
        config={},
        mapping=dict(INPUTS),
        model=None,
        platform=PlatformChoices.LANGFUSE,
        credentials=credentials,
        status=StatusChoices.PENDING,
    )


def run(config):
    """The entry point the external eval task runs, then the stored config."""
    external_eval.run_external_eval_config(str(config.id))
    config.refresh_from_db()
    return config


# The activity wrapper closes DB connections, so these need real transactions.
@pytest.mark.django_db(transaction=True)
@override_settings(CUSTOM_CODE_EVALS_PAUSED=True)
def test_paused_customer_template_fails_before_it_is_charged(
    executor, billing, organization, workspace
):
    template = customer_template(organization, workspace)
    config = run(external_config(organization, workspace, template))
    assert executor == []
    assert billing.charged == []
    assert billing.emitted == []
    assert config.status == StatusChoices.FAILED
    assert config.error_message == SENTENCE


# The activity wrapper closes DB connections, so these need real transactions.
@pytest.mark.django_db(transaction=True)
@override_settings(CUSTOM_CODE_EVALS_PAUSED=True)
def test_paused_composite_with_a_custom_code_child_fails_before_it_is_charged(
    executor, billing, builtin_template, organization, workspace
):
    # The built-in child comes first, so the refusal has to stop it as well.
    parent = composite_template(
        organization,
        workspace,
        [
            builtin_template,
            customer_template(
                organization, workspace, name="pause_external_composite_child"
            ),
        ],
    )
    assert parent.config == {}
    config = run(external_config(organization, workspace, parent))
    assert executor == []
    assert billing.charged == []
    assert billing.emitted == []
    assert billing.child_charged == []
    assert billing.child_events == []
    assert config.status == StatusChoices.FAILED
    assert config.error_message == SENTENCE


# The activity wrapper closes DB connections, so these need real transactions.
@pytest.mark.django_db(transaction=True)
@override_settings(CUSTOM_CODE_EVALS_PAUSED=True)
def test_paused_composite_of_builtin_children_is_still_charged_and_run(
    executor,
    billing,
    builtin_template,
    second_builtin_template,
    organization,
    workspace,
):
    second = second_builtin_template
    assert second.organization_id is None and second.workspace_id is None
    parent = composite_template(organization, workspace, [builtin_template, second])
    config = run(external_config(organization, workspace, parent))
    assert [request["code"] for request in executor] == [
        BUILTIN_CODE,
        BUILTIN_LENGTH_CODE,
    ]
    assert [charge["source_id"] for charge in billing.charged] == [config.id]
    assert [charge["source_id"] for charge in billing.child_charged] == [
        builtin_template.id,
        second.id,
    ]
    assert config.status == StatusChoices.COMPLETED
    assert config.error_message is None
    assert [child["status"] for child in config.eval_results["children"]] == [
        "completed",
        "completed",
    ]


# The activity wrapper closes DB connections, so these need real transactions.
@pytest.mark.django_db(transaction=True)
@override_settings(CUSTOM_CODE_EVALS_PAUSED=True)
def test_paused_builtin_template_is_still_charged_and_run(
    executor, billing, builtin_template, organization, workspace
):
    config = run(external_config(organization, workspace, builtin_template))
    assert [request["code"] for request in executor] == [BUILTIN_CODE]
    assert [charge["source_id"] for charge in billing.charged] == [config.id]
    assert billing.emitted == [str(config.id)]
    assert config.status == StatusChoices.COMPLETED
    assert config.error_message is None
    assert config.eval_results["reason"] == "scored"


# The activity wrapper closes DB connections, so these need real transactions.
@pytest.mark.django_db(transaction=True)
@override_settings(CUSTOM_CODE_EVALS_PAUSED=False)
def test_without_the_setting_customer_template_is_charged_and_run_as_before(
    executor, billing, organization, workspace
):
    template = customer_template(organization, workspace)
    config = run(external_config(organization, workspace, template))
    assert [request["code"] for request in executor] == [CUSTOMER_CODE]
    assert [charge["source_id"] for charge in billing.charged] == [config.id]
    assert billing.emitted == [str(config.id)]
    assert config.status == StatusChoices.COMPLETED
    assert config.error_message is None
    assert config.eval_results["reason"] == "scored"


# The activity wrapper closes DB connections, so these need real transactions.
@pytest.mark.django_db(transaction=True)
@override_settings(CUSTOM_CODE_EVALS_PAUSED=False)
def test_without_the_setting_composite_with_a_custom_code_child_runs_as_before(
    executor, billing, builtin_template, organization, workspace
):
    child = customer_template(
        organization, workspace, name="pause_external_composite_child_off"
    )
    parent = composite_template(organization, workspace, [builtin_template, child])
    config = run(external_config(organization, workspace, parent))
    assert [request["code"] for request in executor] == [BUILTIN_CODE, CUSTOMER_CODE]
    assert [charge["source_id"] for charge in billing.charged] == [config.id]
    assert [charge["source_id"] for charge in billing.child_charged] == [
        builtin_template.id,
        child.id,
    ]
    assert config.status == StatusChoices.COMPLETED
    assert config.error_message is None
