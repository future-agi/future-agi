"""agentcc_find_scoped_guardrail_policies (docs-agentcc-711, item 2).

Ops find the guardrail policies an earlier release left at key or project
scope (which protect no key: the gateway runs only global policies) and
repair them. Read only by default; --repair is a dry run; only --repair
--apply restores scope global, and only --sync then pushes.
"""

import re
from datetime import timedelta
from io import StringIO
from unittest.mock import MagicMock, patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import DatabaseError, connection
from django.test.utils import CaptureQueriesContext

from accounts.models import Organization
from accounts.models.audit_log import AuditLog, AuditScope
from agentcc.management.commands.agentcc_find_scoped_guardrail_policies import (
    AUDIT_ACTION,
    Command,
)
from agentcc.models.guardrail_policy import AgentccGuardrailPolicy
from agentcc.models.org_config import AgentccOrgConfig
from agentcc.services import config_push
from tfc.utils.audit import mute_audit_signals

COMMAND = "agentcc_find_scoped_guardrail_policies"
CHECK = {
    "name": "keyword-blocklist",
    "action": "block",
    "enabled": True,
    "config": {"words": ["policyword"]},
}


@pytest.fixture
def gateway():
    client = MagicMock()
    with patch.object(config_push, "get_gateway_client", return_value=client):
        yield client


def _policy(org, name, scope, **fields):
    return AgentccGuardrailPolicy.no_workspace_objects.create(
        organization=org, name=name, scope=scope, checks=[CHECK], **fields
    )


@pytest.fixture
def seeded(db):
    org_a = Organization.objects.create(name="scoped-cmd A")
    org_b = Organization.objects.create(name="scoped-cmd B")
    AgentccOrgConfig.no_workspace_objects.create(
        organization=org_a, version=1, is_active=True
    )
    policies = {
        "a-key": _policy(org_a, "a-key", "key", applied_keys=["k1", "k2"]),
        "a-project": _policy(
            org_a, "a-project", "project", applied_projects=["p1"], is_active=False
        ),
        "a-global": _policy(org_a, "a-global", "global"),
        "a-deleted": _policy(org_a, "a-deleted", "key", deleted=True),
        "b-key": _policy(org_b, "b-key", "key", applied_keys=["k3"]),
    }
    return org_a, org_b, policies


def _run(*args):
    out = StringIO()
    call_command(COMMAND, *args, stdout=out)
    return out.getvalue()


def _scopes(policies):
    return {
        name: AgentccGuardrailPolicy.all_objects.get(pk=policy.pk).scope
        for name, policy in policies.items()
    }


def _snapshot():
    """All row values (including timestamps, secrets and soft-deleted rows)."""
    return {
        model: list(model._base_manager.order_by("pk").values())
        for model in (Organization, AgentccGuardrailPolicy, AgentccOrgConfig, AuditLog)
    }


def _run_read_only(*args):
    before = _snapshot()
    with CaptureQueriesContext(connection) as queries:
        output = _run(*args)
    assert _snapshot() == before  # Both row counts and every field are unchanged.
    assert queries and all(q["sql"].lstrip().startswith("SELECT") for q in queries)
    return output


def _assert_repairs(before, repaired_names, policies):
    repaired = {policies[name].pk for name in repaired_names}
    after = _snapshot()
    old_policies = {row["id"]: row for row in before[AgentccGuardrailPolicy]}
    assert {row["id"] for row in after[AgentccGuardrailPolicy]} == set(old_policies)
    for row in after[AgentccGuardrailPolicy]:
        expected = old_policies[row["id"]]
        if row["id"] in repaired:
            assert row["updated_at"] > expected["updated_at"]
            expected = {**expected, "scope": "global", "updated_at": row["updated_at"]}
        assert row == expected
    assert after[AgentccOrgConfig] == before[AgentccOrgConfig]
    assert after[Organization] == before[Organization]
    old_audits = {row["id"] for row in before[AuditLog]}
    assert [row for row in after[AuditLog] if row["id"] in old_audits] == before[
        AuditLog
    ]
    audits = [row for row in after[AuditLog] if row["id"] not in old_audits]
    assert len(audits) == len(repaired)
    assert {row["target_id"] for row in audits} == repaired
    for audit in audits:
        policy = old_policies[audit["target_id"]]
        assert audit["organization_id"] == policy["organization_id"]
        assert audit["action"] == AUDIT_ACTION
        assert audit["scope"] == AuditScope.ORGANIZATION
        assert audit["actor_id"] is None  # A system command, not a request actor.
        assert audit["changes"] == {"scope": {"old": policy["scope"], "new": "global"}}
        assert audit["metadata"] == {"command": COMMAND}


SEEDED_SCOPES = {
    "a-key": "key",
    "a-project": "project",
    "a-global": "global",
    "a-deleted": "key",
    "b-key": "key",
}


@pytest.mark.integration
class TestReadOnlyByDefault:
    def test_lists_each_scoped_policy_per_org_and_changes_nothing(
        self, seeded, gateway
    ):
        org_a, org_b, policies = seeded

        output = _run_read_only()

        assert f"org {org_a.id}: 2 policy(ies)" in output
        assert f"org {org_b.id}: 1 policy(ies)" in output
        for name in ("a-key", "a-project", "b-key"):
            assert str(policies[name].id) in output
        assert "scope key (2 key(s) named), active" in output
        assert "scope project (1 project(s) named), inactive" in output
        for name in ("a-global", "a-deleted"):
            assert str(policies[name].id) not in output
        assert "3 policy(ies) in 2 org(s) protect no key" in output
        assert _scopes(policies) == SEEDED_SCOPES
        gateway.set_org_config.assert_not_called()

    def test_repair_alone_is_a_dry_run(self, seeded, gateway):
        _, _, policies = seeded

        output = _run_read_only("--repair")

        assert "Dry run" in output and "Nothing was changed" in output
        assert _scopes(policies) == SEEDED_SCOPES
        gateway.set_org_config.assert_not_called()

    def test_org_limits_inventory(self, seeded, gateway):
        org_a, org_b, policies = seeded
        output = _run_read_only("--org", str(org_a.id))
        assert str(policies["a-key"].id) in output
        assert str(policies["b-key"].id) not in output
        assert str(org_b.id) not in output
        gateway.set_org_config.assert_not_called()

    def test_nothing_scoped(self, db, gateway):
        assert "No guardrail policy is at key or project scope." in _run_read_only()


@pytest.mark.integration
class TestRepair:
    @pytest.mark.parametrize(
        "args",
        [(), ("--repair",), ("--repair", "--apply")],
        ids=["inventory", "preview", "apply"],
    )
    def test_only_applied_repairs_advance_updated_at(self, seeded, gateway, args):
        org_a, _, policies = seeded
        before = {name: policy.updated_at for name, policy in policies.items()}
        repair_time = max(before.values()) + timedelta(seconds=1)
        with patch("django.utils.timezone.now", return_value=repair_time):
            _run("--org", str(org_a.id), *args)
        for name, policy in policies.items():
            policy.refresh_from_db()
            repaired = "--apply" in args and name in {"a-key", "a-project"}
            assert policy.updated_at == (repair_time if repaired else before[name])
        gateway.set_org_config.assert_not_called()

    def test_apply_restores_scope_global_only(self, seeded, gateway):
        _, _, policies = seeded
        before = _snapshot()

        output = _run("--repair", "--apply")

        _assert_repairs(before, {"a-key", "a-project", "b-key"}, policies)

        assert "Set scope global on 3 policy(ies) in 2 org(s)." in output
        assert "POST /agentcc/guardrail-policies/sync/" in output
        assert "again with --sync" not in output
        assert _scopes(policies) == {
            **SEEDED_SCOPES,
            "a-key": "global",
            "a-project": "global",
            "b-key": "global",
        }
        restored = AgentccGuardrailPolicy.no_workspace_objects.get(
            pk=policies["a-key"].pk
        )
        assert restored.applied_keys == ["k1", "k2"]
        assert restored.is_active is True
        assert not AgentccGuardrailPolicy.no_workspace_objects.get(
            pk=policies["a-project"].pk
        ).is_active
        gateway.set_org_config.assert_not_called()

    @pytest.mark.parametrize("scope", ["key", "project"])
    def test_scope_only_repair_then_documented_sync_restores_gateway_policy(
        self, auth_client, organization, gateway, scope
    ):
        active = AgentccOrgConfig.no_workspace_objects.create(
            organization=organization, version=1, is_active=True
        )
        policy = _policy(organization, "restore-me", scope)
        other_org = Organization.objects.create(name="other repair org")
        other = _policy(other_org, "leave-me", scope)

        output = _run("--org", str(organization.id), "--repair", "--apply")

        policy.refresh_from_db()
        assert policy.scope == "global"
        gateway.set_org_config.assert_not_called()
        active.refresh_from_db()
        assert not active.guardrails
        assert "again with --sync" not in output
        endpoint = re.search(r"POST (\S+)", output)
        assert endpoint, output
        assert endpoint.group(1) == "/agentcc/guardrail-policies/sync/"
        # Follow the printed second step as the repaired org's administrator;
        # use the real sync and config push with only the gateway client mocked.
        response = auth_client.post(endpoint.group(1), {}, format="json")

        assert response.status_code == 200, response.json()
        assert response.json()["result"]["gateway_synced"] is True
        gateway.set_org_config.assert_called_once()
        pushed_org, config = gateway.set_org_config.call_args.args
        assert pushed_org == str(organization.id)
        assert config["guardrails"]["checks"]["keyword-blocklist"]["config"] == {
            "words": ["policyword"]
        }
        active.refresh_from_db()
        assert active.guardrails["checks"][0]["_policy"] == str(policy.id)
        other.refresh_from_db()
        assert other.scope == scope

    def test_org_limits_the_repair(self, seeded, gateway):
        org_a, _, policies = seeded
        before = _snapshot()

        _run("--org", str(org_a.id), "--repair", "--apply")

        _assert_repairs(before, {"a-key", "a-project"}, policies)
        scopes = _scopes(policies)
        assert scopes["a-key"] == scopes["a-project"] == "global"
        assert scopes["b-key"] == "key"

    def test_sync_pushes_each_repaired_org(self, seeded, gateway):
        org_a, org_b, _ = seeded

        output = _run("--repair", "--apply", "--sync")

        assert f"org {org_a.id}: gateway_synced=True" in output
        # Org B has no active config version, so there is nothing to push.
        assert f"org {org_b.id}: gateway_synced=False" in output
        pushed = [c.args for c in gateway.set_org_config.call_args_list]
        assert [org for org, _ in pushed] == [str(org_a.id)]
        checks = pushed[0][1]["guardrails"]["checks"]
        assert checks["keyword-blocklist"]["config"] == {"words": ["policyword"]}

    @pytest.mark.parametrize(
        "args",
        [
            ["--apply"],
            ["--sync"],
            ["--apply", "--sync"],
            ["--repair", "--sync"],
            ["--org", "not-a-uuid"],
        ],
        ids=[
            "apply-alone",
            "sync-alone",
            "sync-without-repair",
            "sync-without-apply",
            "bad-org",
        ],
    )
    def test_refused_command_lines_change_nothing(self, seeded, gateway, args):
        _, _, policies = seeded
        before = _snapshot()

        with pytest.raises(CommandError):
            _run(*args)

        assert _snapshot() == before
        assert _scopes(policies) == SEEDED_SCOPES
        gateway.set_org_config.assert_not_called()

    def test_repeated_apply_does_not_duplicate_audits(self, seeded, gateway):
        _run("--repair", "--apply")
        assert "No guardrail policy" in _run_read_only("--repair", "--apply")
        gateway.set_org_config.assert_not_called()

    def test_audit_failure_rolls_back_repair(self, seeded, gateway):
        before = _snapshot()
        with patch.object(
            AuditLog.objects, "create", side_effect=DatabaseError("offline")
        ):
            with pytest.raises(CommandError, match="repair was rolled back"):
                _run("--repair", "--apply", "--sync")
        assert _snapshot() == before
        gateway.set_org_config.assert_not_called()

    def test_partial_audit_failure_reports_committed_repairs(self, seeded, gateway):
        org_a, _, policies = seeded
        before = _snapshot()
        create_audit = AuditLog.objects.create
        err = StringIO()

        def fail_second_audit(**kwargs):
            if kwargs["target_id"] == policies["a-project"].pk:
                raise DatabaseError("offline")
            return create_audit(**kwargs)

        with patch.object(AuditLog.objects, "create", side_effect=fail_second_audit):
            with pytest.raises(CommandError, match="repair was rolled back"):
                call_command(
                    COMMAND,
                    "--org",
                    str(org_a.id),
                    "--repair",
                    "--apply",
                    "--sync",
                    stdout=StringIO(),
                    stderr=err,
                )
        assert (
            "Stopped after committing 1 policy repair(s) in 1 org(s)." in err.getvalue()
        )
        assert "No immediate gateway sync was attempted." in err.getvalue()
        _assert_repairs(before, {"a-key"}, policies)
        gateway.set_org_config.assert_not_called()

    def test_muted_audit_rolls_back_repair(self, seeded, gateway):
        before = _snapshot()
        with mute_audit_signals():
            with pytest.raises(CommandError, match="repair was rolled back"):
                _run("--repair", "--apply")
        assert _snapshot() == before
        gateway.set_org_config.assert_not_called()

    @pytest.mark.parametrize("change", [{"scope": "global"}, {"deleted": True}])
    def test_stale_inventory_is_not_repaired_or_audited(self, seeded, change):
        _, _, policies = seeded
        policy = policies["a-key"]
        AgentccGuardrailPolicy.all_objects.filter(pk=policy.pk).update(**change)
        before = _snapshot()
        assert Command._repair_policy(policy) is False
        assert _snapshot() == before

    def test_audit_contains_only_scope_and_command(self, seeded, gateway):
        org_a, _, policies = seeded
        AgentccGuardrailPolicy.all_objects.filter(pk=policies["a-key"].pk).update(
            description="private operator description",
            checks=[{"config": {"token": "synthetic-private-value"}}],
            encrypted_check_configs=b"synthetic-encrypted-config",
        )
        before = _snapshot()
        _run("--org", str(org_a.id), "--repair", "--apply")
        _assert_repairs(before, {"a-key", "a-project"}, policies)
