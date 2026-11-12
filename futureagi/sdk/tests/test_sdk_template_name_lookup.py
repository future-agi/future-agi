"""Which evaluation templates the SDK sees, and which one a name means.

The SDK's list of evaluations (/sdk/api/v1/get-evals/), configure
(/sdk/api/v1/configure-evaluations/) and run (/sdk/api/v1/new-eval/) agree:

- A caller sees the built-ins and its organization's templates in its
  workspace. From the organization's default workspace it also sees the
  organization's templates saved with no workspace (from before workspaces),
  the rule the shared model manager applies. From any other workspace it does
  not, and it never sees another organization's.
- When old rows share a name, the built-in comes first, then the caller's
  template in its workspace, then one with no workspace.

The real views and serializers run; only the evaluation engine, billing,
analytics and workflow transport are replaced (as in
test_standalone_input_shape_scope.py), and the engine records which template ran.
"""

from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest

from accounts.models.organization import Organization
from accounts.models.workspace import Workspace
from model_hub.models.evals_metric import EvalTemplate
from sdk.utils import evaluations as sdk_evaluations
from tfc.constants.api_calls import APICallStatusChoices
from tracer.models.external_eval_config import ExternalEvalConfig, PlatformChoices

pytestmark = pytest.mark.django_db

CONFIG = {"eval_type_id": "TestEval", "required_keys": ["query"], "output": "score"}
# For a row that must lose a shared name: a request with {"query": ...} checked
# against it would be refused for an unexpected key, so the request check and
# the view cannot quietly pick different rows.
LOSER_CONFIG = {
    "eval_type_id": "TestEval",
    "required_keys": ["other"],
    "output": "score",
}
SHARED = "lookup_shared_name"


def template(
    name, organization, workspace, owner="user", stored_name=None, config=None
):
    """A template saved with exactly this organization, workspace and name.

    save() stamps the context's workspace on a row saved while a workspace
    context is set, and refuses a customer name that a built-in or another of
    the organization's templates has; old rows have both, so they are written
    again without save()."""
    row = EvalTemplate.no_workspace_objects.create(
        name=name,
        owner=owner,
        organization=organization,
        workspace=workspace,
        config=dict(config or CONFIG),
    )
    EvalTemplate.no_workspace_objects.filter(pk=row.pk).update(
        organization=organization, workspace=workspace, name=stored_name or name
    )
    row.refresh_from_db()
    assert row.organization_id == (organization.id if organization else None)
    assert row.workspace_id == (workspace.id if workspace else None)
    return row


@pytest.fixture
def sdk(auth_client, monkeypatch):
    # The root conftest supplies these same interfaces in OSS runs.
    from ee.usage.services import config, emitter, metering
    from evaluations import engine
    from model_hub.services.ground_truth_service import GroundTruthService
    from sdk.views import evaluations as views
    from tfc.temporal import evaluations as temporal_evaluations
    from tfc.temporal.evaluations import activities

    ran = []

    def ledger_write(**kwargs):
        return SimpleNamespace(
            status=APICallStatusChoices.PROCESSING.value,
            config="{}",
            log_id=uuid4(),
            save=Mock(),
        )

    def engine_run(request):
        ran.append(request.eval_template.pk)
        return SimpleNamespace(
            data=dict(request.inputs),
            failure=None,
            reason="lookup test",
            runtime=0.01,
            model_used="turing_large",
            metrics={},
            metadata={},
            output_type="score",
            value="0.75",
            cost={"total_cost": 0.03},
            token_usage={},
            evaluator=None,
        )

    billing = SimpleNamespace(
        get_eval_per_run_fee=lambda: 0.02,
        calculate_ai_credits=lambda cost: 5.0,
    )
    monkeypatch.setattr(
        sdk_evaluations,
        "log_and_deduct_cost_for_api_request",
        Mock(side_effect=ledger_write),
    )
    monkeypatch.setattr(engine, "run_eval", Mock(side_effect=engine_run))
    monkeypatch.setattr(config.BillingConfig, "get", Mock(return_value=billing))
    monkeypatch.setattr(emitter, "emit", Mock())
    monkeypatch.setattr(
        metering, "check_usage", Mock(return_value=SimpleNamespace(allowed=True))
    )
    monkeypatch.setattr(GroundTruthService, "inject_context", Mock())
    monkeypatch.setattr(views, "get_mixpanel_properties", lambda **kwargs: kwargs)
    monkeypatch.setattr(views, "track_mixpanel_event", Mock())
    monkeypatch.setattr(temporal_evaluations, "start_evaluation_workflow", Mock())
    # Keep pytest's transaction connection open when draining the activity body.
    monkeypatch.setattr(activities, "close_old_connections", lambda: None)

    def listed():
        response = auth_client.get("/sdk/api/v1/get-evals/")
        assert response.status_code == 200, response.content.decode()[:500]
        return [entry["name"] for entry in response.json()["result"]]

    def configure(name):
        # The stored configuration requires every Langfuse setting; these are never used.
        settings = dict.fromkeys(
            ExternalEvalConfig.get_required_credentials(PlatformChoices.LANGFUSE),
            "unused",
        )
        return auth_client.post(
            "/sdk/api/v1/configure-evaluations/",
            {
                "eval_config": {"eval_templates": name, "inputs": {"query": "query"}},
                "platform": "langfuse",
                "custom_eval_name": f"{name} on langfuse",
                **settings,
            },
            format="json",
        )

    def run(name):
        return auth_client.post(
            "/sdk/api/v1/new-eval/",
            {
                "eval_name": name,
                "inputs": {"query": "q"},
                "model": "turing_large",
                "is_async": False,
            },
            format="json",
        )

    return SimpleNamespace(
        client=auth_client, ran=ran, listed=listed, configure=configure, run=run
    )


def configured(row):
    return ExternalEvalConfig.no_workspace_objects.filter(eval_template=row).count()


def assert_found(sdk, row):
    """Listed, configured and run as ``row`` by its name."""
    assert row.name in sdk.listed()
    response = sdk.configure(row.name)
    assert response.status_code == 200, response.content.decode()[:500]
    assert configured(row) == 1
    response = sdk.run(row.name)
    assert response.status_code == 200, response.content.decode()[:500]
    assert sdk.ran == [row.pk]


def assert_not_found(sdk, row):
    """Not listed; configure and run answer 400; nothing is stored or run."""
    before = ExternalEvalConfig.no_workspace_objects.count()
    assert row.name not in sdk.listed()
    for response in (sdk.configure(row.name), sdk.run(row.name)):
        assert response.status_code == 400
        assert "not found" in response.content.decode()
    assert ExternalEvalConfig.no_workspace_objects.count() == before
    assert sdk.ran == []


def test_own_template_without_a_workspace_is_found_from_the_default_workspace(
    sdk, organization, workspace
):
    assert workspace.is_default
    assert_found(sdk, template("lookup_no_workspace", organization, None))


def test_own_template_without_a_workspace_is_not_found_from_another_workspace(
    sdk, user, organization
):
    legacy = template("lookup_no_workspace_elsewhere", organization, None)
    second = Workspace.objects.create(
        name="Second Workspace",
        organization=organization,
        is_default=False,
        is_active=True,
        created_by=user,
    )
    sdk.client.set_workspace(second)
    assert_not_found(sdk, legacy)


def test_another_organizations_template_without_a_workspace_is_not_found(sdk):
    other = Organization.objects.create(name="Other Organization")
    assert_not_found(sdk, template("lookup_other_no_workspace", other, None))


def test_own_template_in_the_workspace_is_found(sdk, organization, workspace):
    assert_found(sdk, template("lookup_in_workspace", organization, workspace))


# In each shared-name test the row that must lose is saved first, so it is the
# older one: only the rank, not the order of saving, can pick the winner.


def test_builtin_comes_before_an_own_template_of_the_same_name(
    sdk, organization, workspace
):
    own = template(
        "lookup_shared_own",
        organization,
        workspace,
        stored_name=SHARED,
        config=LOSER_CONFIG,
    )
    builtin = template(
        "lookup_shared_builtin", None, None, owner="system", stored_name=SHARED
    )
    assert own.created_at < builtin.created_at
    assert sdk.listed().count(SHARED) == 2
    assert_found(sdk, builtin)
    assert configured(own) == 0


def test_builtin_comes_before_an_own_template_without_a_workspace_of_the_same_name(
    sdk, organization
):
    own = template(
        "lookup_shared_legacy",
        organization,
        None,
        stored_name=SHARED,
        config=LOSER_CONFIG,
    )
    builtin = template(
        "lookup_shared_builtin_legacy", None, None, owner="system", stored_name=SHARED
    )
    assert own.created_at < builtin.created_at
    assert sdk.listed().count(SHARED) == 2
    assert_found(sdk, builtin)
    assert configured(own) == 0


def test_own_template_in_the_workspace_comes_before_one_without_a_workspace(
    sdk, organization, workspace
):
    legacy = template(
        "lookup_pair_no_workspace",
        organization,
        None,
        stored_name=SHARED,
        config=LOSER_CONFIG,
    )
    in_workspace = template(
        "lookup_pair_in_workspace", organization, workspace, stored_name=SHARED
    )
    assert legacy.created_at < in_workspace.created_at
    assert sdk.listed().count(SHARED) == 2
    assert_found(sdk, in_workspace)
    assert configured(legacy) == 0
