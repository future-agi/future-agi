"""Key- and project-scoped guardrail policies are refused (docs-agentcc-711,
item 2; cloud proof j-20261028t0728-c95dee, record 2-scoped-policies).

The gateway receives only an org's global policies (guardrail_sync), so
POST guardrail-policies/{id}/apply/, which set a policy's scope to the keys or
projects it named, took the policy away from every key of the org, the
targeted ones included: on the proof's gateway both keys were blocked before
the apply and both passed after it. A create or update that set scope to key
or project did the same.

Now:

- apply answers 400 ``scoped_policy_not_supported``, leaves the policy as it
  was and pushes nothing; the policy keeps protecting every key;
- create, PUT and PATCH refuse scope key and project with the same code;
  global (or no scope) is accepted;
- a policy an earlier release left at key or project scope still reads back
  as stored, and PATCH scope global restores it;
- the API schema offers scope global only, and applied keys and projects are
  read-only.

Pushes go to a recording gateway client through the real push code
(config_push._build_payload), as in test_guardrail_section_emptied.py.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from rest_framework import status

from agentcc.models import AgentccAPIKey
from agentcc.models.guardrail_policy import AgentccGuardrailPolicy
from agentcc.models.org_config import AgentccOrgConfig
from agentcc.models.project import AgentccProject
from agentcc.serializers.guardrail_policy import AgentccGuardrailPolicySerializer
from agentcc.services import config_push
from agentcc.views import gateway as gateway_views

POLICIES = "/agentcc/guardrail-policies/"
SYNC = f"{POLICIES}sync/"
CODE = "scoped_policy_not_supported"
CHECK = {
    "name": "keyword-blocklist",
    "action": "block",
    "enabled": True,
    "config": {"words": ["policyword"]},
}


@pytest.fixture
def gateway():
    """Every push goes to a mock that records the payload it was sent."""
    client = MagicMock()
    with (
        patch.object(config_push, "get_gateway_client", return_value=client),
        patch.object(gateway_views, "get_gateway_client", return_value=client),
    ):
        yield client


def _pushes(gateway, org):
    return [
        call.args[1]
        for call in gateway.set_org_config.call_args_list
        if call.args[0] == str(org.id)
    ]


def _pushed_checks(gateway, org):
    sent = _pushes(gateway, org)
    assert sent, "nothing was pushed"
    return (sent[-1].get("guardrails") or {}).get("checks") or {}


def _active_config(org):
    return AgentccOrgConfig.no_workspace_objects.create(
        organization=org, version=1, is_active=True
    )


def _key(org, workspace, name):
    return AgentccAPIKey.no_workspace_objects.create(
        organization=org,
        workspace=workspace,
        gateway_key_id=f"scope-refused-{name}",
        key_prefix=f"pk-{name}",
        key_hash="hash",
        name=name,
        owner="scope-test",
        status=AgentccAPIKey.ACTIVE,
        allowed_models=[],
        allowed_providers=[],
        metadata={},
    )


def _created_policy(auth_client, gateway, organization):
    """A global keyword-blocklist policy made through the API: pushed, so the
    gateway runs it for every key."""
    response = auth_client.post(
        POLICIES, {"name": "words", "checks": [CHECK]}, format="json"
    )
    assert response.status_code == status.HTTP_200_OK, response.json()
    assert list(_pushed_checks(gateway, organization)) == ["keyword-blocklist"]
    return AgentccGuardrailPolicy.no_workspace_objects.get(
        id=response.json()["result"]["id"]
    )


def _refused(response):
    assert response.status_code == status.HTTP_400_BAD_REQUEST, response.json()
    assert response.json()["code"] == CODE, response.json()


def _unchanged(policy):
    policy.refresh_from_db()
    assert policy.scope == AgentccGuardrailPolicy.SCOPE_GLOBAL
    assert policy.applied_keys == []
    assert policy.applied_projects == []


@pytest.mark.integration
@pytest.mark.api
class TestApplyIsRefused:
    @pytest.mark.parametrize("target", ["key_ids", "project_ids"])
    def test_apply_answers_400_and_the_policy_keeps_protecting_every_key(
        self, auth_client, organization, workspace, gateway, target
    ):
        _active_config(organization)
        policy = _created_policy(auth_client, gateway, organization)
        key = _key(organization, workspace, "a")
        project = AgentccProject.no_workspace_objects.create(
            organization=organization, workspace=workspace, name="scope-proj"
        )
        ids = [str(key.id)] if target == "key_ids" else [str(project.id)]
        pushed_before = len(_pushes(gateway, organization))

        _refused(
            auth_client.post(f"{POLICIES}{policy.id}/apply/", {target: ids}, "json")
        )

        _unchanged(policy)
        assert len(_pushes(gateway, organization)) == pushed_before
        active = AgentccOrgConfig.no_workspace_objects.get(
            organization=organization, is_active=True
        )
        assert [c["name"] for c in active.guardrails["checks"]] == ["keyword-blocklist"]
        # The next sync still pushes the check, for every key of the org.
        assert auth_client.post(SYNC, {}, format="json").status_code == 200
        pushed = _pushed_checks(gateway, organization)
        assert pushed["keyword-blocklist"]["config"] == {"words": ["policyword"]}

    @pytest.mark.parametrize(
        "body",
        [{}, {"key_ids": []}, {"key_ids": ["x"], "project_ids": ["y"]}],
        ids=["empty", "no-keys", "both"],
    )
    def test_every_apply_body_is_refused(
        self, auth_client, organization, gateway, body
    ):
        _active_config(organization)
        policy = _created_policy(auth_client, gateway, organization)
        _refused(auth_client.post(f"{POLICIES}{policy.id}/apply/", body, "json"))
        _unchanged(policy)

    def test_another_orgs_policy_is_still_404(self, auth_client, gateway):
        from accounts.models import Organization

        other = Organization.objects.create(name="scope-refused other org")
        policy = AgentccGuardrailPolicy.no_workspace_objects.create(
            organization=other, name="theirs", checks=[CHECK]
        )
        response = auth_client.post(
            f"{POLICIES}{policy.id}/apply/", {"key_ids": []}, format="json"
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.integration
@pytest.mark.api
class TestScopeOnWrites:
    @pytest.mark.parametrize("scope", ["key", "project"])
    def test_create_refuses_key_and_project_scope(
        self, auth_client, organization, gateway, scope
    ):
        _active_config(organization)
        _refused(
            auth_client.post(
                POLICIES,
                {"name": "scoped", "scope": scope, "checks": [CHECK]},
                format="json",
            )
        )
        assert not AgentccGuardrailPolicy.all_objects.filter(
            organization=organization, name="scoped"
        ).exists()
        gateway.set_org_config.assert_not_called()

    @pytest.mark.parametrize("method", ["patch", "put"])
    @pytest.mark.parametrize("scope", ["key", "project"])
    def test_update_refuses_key_and_project_scope(
        self, auth_client, organization, gateway, method, scope
    ):
        _active_config(organization)
        policy = _created_policy(auth_client, gateway, organization)
        pushed_before = len(_pushes(gateway, organization))
        body = {"scope": scope}
        if method == "put":
            body.update({"name": "words", "checks": [CHECK]})

        _refused(getattr(auth_client, method)(f"{POLICIES}{policy.id}/", body, "json"))

        _unchanged(policy)
        assert len(_pushes(gateway, organization)) == pushed_before

    @pytest.mark.parametrize("body", [{"scope": "global"}, {}], ids=["global", "none"])
    def test_create_with_global_or_no_scope_is_global(
        self, auth_client, organization, gateway, body
    ):
        _active_config(organization)
        response = auth_client.post(
            POLICIES, {"name": "g", "checks": [CHECK], **body}, format="json"
        )
        assert response.status_code == status.HTTP_200_OK, response.json()
        assert response.json()["result"]["scope"] == "global"
        assert "keyword-blocklist" in _pushed_checks(gateway, organization)

    def test_applied_keys_sent_on_create_are_not_stored(
        self, auth_client, organization, gateway
    ):
        _active_config(organization)
        response = auth_client.post(
            POLICIES,
            {"name": "g", "checks": [CHECK], "applied_keys": ["k1"]},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK, response.json()
        policy = AgentccGuardrailPolicy.no_workspace_objects.get(
            organization=organization, name="g"
        )
        assert policy.applied_keys == []


@pytest.mark.integration
@pytest.mark.api
class TestAPolicyLeftScopedByAnEarlierRelease:
    def _left_scoped(self, organization):
        return AgentccGuardrailPolicy.no_workspace_objects.create(
            organization=organization,
            name="left-scoped",
            scope=AgentccGuardrailPolicy.SCOPE_KEY,
            applied_keys=["k1"],
            checks=[CHECK],
        )

    def test_reads_back_as_stored_and_is_not_pushed(
        self, auth_client, organization, gateway
    ):
        _active_config(organization)
        policy = self._left_scoped(organization)

        listed = auth_client.get(POLICIES)
        read = auth_client.get(f"{POLICIES}{policy.id}/")
        assert listed.status_code == read.status_code == status.HTTP_200_OK
        assert [row["scope"] for row in listed.json()["result"]] == ["key"]
        assert read.json()["result"]["scope"] == "key"
        assert read.json()["result"]["applied_keys"] == ["k1"]

        assert auth_client.post(SYNC, {}, format="json").status_code == 200
        assert _pushed_checks(gateway, organization) == {}

    def test_patch_scope_global_restores_it(self, auth_client, organization, gateway):
        _active_config(organization)
        policy = self._left_scoped(organization)

        response = auth_client.patch(
            f"{POLICIES}{policy.id}/", {"scope": "global"}, format="json"
        )

        assert response.status_code == status.HTTP_200_OK, response.json()
        policy.refresh_from_db()
        assert policy.scope == AgentccGuardrailPolicy.SCOPE_GLOBAL
        assert "keyword-blocklist" in _pushed_checks(gateway, organization)


def _swagger():
    root = Path(__file__).resolve().parents[3]
    with (root / "api_contracts" / "openapi" / "swagger.json").open() as f:
        return json.load(f)


class TestTheApiSchemaOffersGlobalOnly:
    def test_serializer_scope_is_global_only_and_applied_targets_read_only(self):
        fields = AgentccGuardrailPolicySerializer().fields
        assert list(fields["scope"].choices) == ["global"]
        assert fields["applied_keys"].read_only
        assert fields["applied_projects"].read_only

    def test_the_committed_openapi_schema_matches(self):
        swagger = _swagger()
        props = swagger["definitions"]["AgentccGuardrailPolicy"]["properties"]
        assert props["scope"]["enum"] == ["global"]
        assert props["applied_keys"].get("readOnly") is True
        assert props["applied_projects"].get("readOnly") is True
        apply = swagger["paths"]["/agentcc/guardrail-policies/{id}/apply/"]["post"]
        text = json.dumps(apply)
        assert CODE in text
        assert "Apply this policy to specific keys or projects" not in text
