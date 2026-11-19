"""Run readiness Fix: who fixes each row, the builder's message, the flagged
``fix_groups`` and the fix run read."""

import uuid
from unittest.mock import patch

import pytest

from simulate.models import HarnessAgentVersionRuntime, HarnessAssembledBundle
from simulate.serializers.harness_environment import (
    HarnessCapabilityReportSerializer,
    HarnessCapabilitySelectionReportSerializer,
)
from simulate.services.harness_readiness.fix_groups import enabled, fix_groups
from simulate.tests.test_harness_readiness import (
    BASE,
    FAILED_PROBE,
    PASSED,
    PRODUCTION_ONLY,
    REJECTED_TOOL,
    SAME_TOOLS,
    TOOLS,
    WORLD,
    _built,
    _bundle,
    _certify,
    _committed_run,
    _environment,
    _get,
    _report,
    _rows,
    _tool,
    _with_a2,
)
from simulate.tests.test_harness_readiness import (  # noqa: F401 - fixtures
    readiness_on as readiness_on,
)
from simulate.tests.test_harness_readiness import (  # noqa: F401 - fixture
    scheduled as scheduled,
)
from simulate.tests.test_harness_readiness import (  # noqa: F401 - fixture
    storage as storage,
)

pytestmark = pytest.mark.django_db

UNREACHABLE = {"tool_entrypoints": [{"tool": "issue_refund", "mode": "unreachable"}]}
PROBE_ACTION = "Rebuild this environment so the test world's probe passes for {}"
UNREACHABLE_ACTION = (
    "Rebuild this environment with a reachable implementation of issue_refund in"
    " the test world"
)


def _failed_pair(bundle_id, action="issue_refund"):
    """The pairing's check failed on ``action``: the ``named`` rule."""
    HarnessAssembledBundle._base_manager.filter(id=bundle_id).update(
        state="failed",
        object_key=None,
        error={
            "code": "authoring_runtime_validation_failed",
            "source": "validation",
            "failed_actions": [{"action": action, "reason": "live"}],
        },
    )


STATE_MESSAGE = (
    "The test world's schema and seed check failed: fix its seed data so the world"
    " is seeded and its state check passes. If the schema itself is wrong, change"
    " nothing and say the environment must be rebuilt."
)


def _fix_get(auth_client, environment_id):
    return auth_client.get(f"{BASE}/{environment_id}/capabilities/fix/")


class TestFixGroup:
    @pytest.mark.parametrize(
        ("row", "certificate", "group", "message"),
        [
            ("start", {"checks": {**PASSED, "processes": "failed"}}, "needs_you", None),
            # A gate's rows wait on it: they are fixed by whoever fixes the gate.
            (
                "message",
                {"checks": {**PASSED, "processes": "failed"}},
                "needs_you",
                None,
            ),
            (
                "message",
                {"checks": {**PASSED, "chat_ingress": "failed"}},
                "needs_you",
                None,
            ),
            (
                "tool_discovery",
                {"checks": {**PASSED, "tool_contract": "1/2"}, "tools": REJECTED_TOOL},
                "needs_you",
                None,
            ),
            (
                "tool_routing",
                {"checks": {**PASSED, "tool_contract": "1/2"}, "tools": REJECTED_TOOL},
                "needs_you",
                None,
            ),
            (
                "tool_routing",
                {
                    "checks": {**PASSED, "action_behavior": "1/2"},
                    "actions": FAILED_PROBE,
                },
                "needs_you",
                None,
            ),
            (
                "state",
                {"checks": {**PASSED, "schema_and_seed": "failed"}},
                "builder",
                STATE_MESSAGE,
            ),
            (
                "reset",
                {"checks": {**PASSED, "schema_and_seed": "failed"}},
                "builder",
                STATE_MESSAGE,
            ),
            (
                "reset",
                {"checks": {**PASSED, "reset_equivalence": "failed"}},
                "needs_you",
                None,
            ),
        ],
    )
    def test_group_and_message_per_certificate_finding(
        self, user, workspace, storage, row, certificate, group, message
    ):
        environment = _environment(
            user, workspace, storage, f"rd-fixg-{row[:6]}-{uuid.uuid4().hex[:6]}"
        )
        _certify(environment, storage, **certificate)

        found = _rows(_report(environment))[row]

        assert (found["fix_group"], found["fix"]["message"]) == (group, message)

    def test_the_state_message_says_a_wrong_schema_needs_a_rebuild(self):
        # The builder cannot change the schema: its own words then end the fix.
        assert "change nothing and say the environment must be rebuilt" in (
            STATE_MESSAGE
        )

    def test_a_row_without_a_fix_has_no_group(self, user, workspace, storage):
        environment = _environment(user, workspace, storage, "rd-fixg-none")
        _certify(environment, storage)

        rows = _rows(_report(environment))

        assert all(row["fix"] is None for row in rows.values())
        assert {row["fix_group"] for row in rows.values()} == {None}

    def test_a_check_not_run_on_a_certified_pair_is_the_users(
        self, user, workspace, storage
    ):
        environment = _environment(user, workspace, storage, "rd-fixg-not-run")
        _certify(
            environment,
            storage,
            checks={**PASSED, "action_behavior": "0/0"},
            actions=[{"action": "issue_refund", "status": "not_run", "reason": ""}],
        )

        routing = _rows(_report(environment))["tool_routing"]

        assert (routing["reason_code"], routing["fix_group"]) == (
            "not_run",
            "needs_you",
        )

    @pytest.mark.parametrize(
        ("contract", "action"),
        [
            (PRODUCTION_ONLY, "Point issue_refund at the test world"),
            (UNREACHABLE, UNREACHABLE_ACTION),
        ],
    )
    def test_the_contract_s_routing_findings_are_the_users(
        self, user, workspace, storage, contract, action
    ):
        environment = _environment(
            user, workspace, storage, f"rd-fixg-contract-{uuid.uuid4().hex[:6]}"
        )
        _certify(environment, storage)
        _built(environment, contract=contract)

        routing = _rows(_report(environment))["tool_routing"]

        assert routing["status"] == "blocked"
        assert (routing["fix_group"], routing["fix"]["message"]) == ("needs_you", None)
        assert routing["fix"]["action"] == action

    def test_a_tool_the_world_lacks_is_the_users_to_rebuild(
        self, user, workspace, storage
    ):
        environment, a2 = _with_a2(
            user,
            workspace,
            storage,
            "rd-fixg-world",
            tools=(_tool("lookup_order", "order_id"), _tool("send_email", "to")),
        )
        _built(environment, contract=WORLD)

        rows = _rows(_report(environment, a2))
        discovery = rows["tool_discovery"]

        # Nothing waiting on discovery goes to the builder either.
        assert rows["tool_routing"]["fix_group"] != "builder"
        assert discovery["reason_code"] == "tool_not_in_world"
        # The world tool the agent lacks is the user's part: never sent to the builder.
        assert {"kind": "tool", "name": "issue_refund", "result": "failed"} in (
            discovery["targets"]
        )
        assert (discovery["fix_group"], discovery["fix"]["message"]) == (
            "needs_you",
            None,
        )
        assert discovery["fix"]["action"] == (
            "Rebuild this environment from this agent version so the test world has"
            " send_email, or test this agent version in an environment that has it"
        )

    def test_a_tool_the_agent_lacks_is_the_users(self, user, workspace, storage):
        environment, a2 = _with_a2(
            user,
            workspace,
            storage,
            "rd-fixg-agent",
            tools=(_tool("lookup_order", "order_id"),),
        )
        _built(environment, contract=WORLD)

        discovery = _rows(_report(environment, a2))["tool_discovery"]

        assert (discovery["reason_code"], discovery["fix_group"]) == (
            "tool_not_in_agent",
            "needs_you",
        )
        assert discovery["fix"]["message"] is None

    def test_a_changed_signature_is_the_users(self, user, workspace, storage):
        environment, a2 = _with_a2(
            user,
            workspace,
            storage,
            "rd-fixg-signature",
            tools=(
                _tool("lookup_order", "order_id"),
                _tool("issue_refund", "order_id", "amount", "reason"),
            ),
        )
        _built(environment, contract=WORLD)
        _certify(environment, storage, agent=a2)

        routing = _rows(_report(environment, a2))["tool_routing"]

        assert (routing["reason_code"], routing["fix_group"]) == (
            "tool_signature_changed",
            "needs_you",
        )
        assert routing["fix"]["message"] is None
        assert routing["fix"]["action"] == (
            "Rebuild this environment from this agent version so issue_refund takes"
            " the arguments the agent sends, or test it in an environment built for"
            " them"
        )

    def test_unread_agent_tools_and_stale_rows_are_a_recheck(
        self, user, workspace, storage
    ):
        environment, a2 = _with_a2(
            user, workspace, storage, "rd-fixg-unread", tools=None
        )
        _built(environment, contract=WORLD)

        rows = _rows(_report(environment, a2))

        assert (rows["tool_discovery"]["reason_code"], rows["message"]["status"]) == (
            "agent_tools_unread",
            "stale",
        )
        assert rows["tool_discovery"]["fix_group"] == "recheck"
        assert rows["message"]["fix_group"] == "recheck"
        assert rows["message"]["fix"]["message"] is None

    @pytest.mark.parametrize("preflight", ["pending", "failed"])
    def test_preflight_is_the_users(self, user, workspace, storage, preflight):
        environment, a2 = _with_a2(
            user, workspace, storage, f"rd-fixg-preflight-{preflight}"
        )
        _built(environment)
        HarnessAgentVersionRuntime.no_workspace_objects.filter(agent_version=a2).update(
            preflight_status=preflight
        )

        start = _rows(_report(environment, a2))["start"]

        # The agent's retry, not a re-check of the pairing.
        assert (start["fix"]["type"], start["fix_group"]) == ("preflight", "needs_you")


class TestWorldRows:
    """World failures a sealed environment's chat cannot change: each is the user's,
    sends the builder nothing and says to rebuild."""

    def _changed_lookup(self, user, workspace, storage, key, contract=WORLD):
        environment, a2 = _with_a2(
            user,
            workspace,
            storage,
            key,
            tools=(
                _tool("lookup_order", "order_id", "region"),
                _tool("issue_refund", "order_id", "amount"),
            ),
        )
        _built(environment, contract=contract)
        return environment, a2

    @staticmethod
    def _assert_users(row, reason_code):
        assert (row["reason_code"], row["fix_group"], row["fix"]["message"]) == (
            reason_code,
            "needs_you",
            None,
        )
        assert row["fix"]["action"].startswith("Rebuild this environment")

    def test_a_probe_ratio_naming_no_tool(self, user, workspace, storage):
        environment = _environment(user, workspace, storage, "rd-fixg-ratio")
        _certify(environment, storage, checks={**PASSED, "action_behavior": "1/2"})

        routing = _rows(_report(environment))["tool_routing"]

        self._assert_users(routing, "action_failed")
        assert routing["fix"]["action"] == PROBE_ACTION.format("the failing tools")

    def test_a_named_failed_probe_with_an_unreachable_tool(
        self, user, workspace, storage
    ):
        environment = _environment(user, workspace, storage, "rd-fixg-c")
        _certify(environment, storage)
        _failed_pair(_bundle(environment).id, action="lookup_order")
        _built(environment, contract=UNREACHABLE)

        routing = _rows(_report(environment))["tool_routing"]

        self._assert_users(routing, "action_failed")
        assert routing["fix"]["action"] == PROBE_ACTION.format("lookup_order")

    def test_a_changed_signature_with_a_named_failed_probe(
        self, user, workspace, storage
    ):
        environment, a2 = self._changed_lookup(
            user, workspace, storage, "rd-fixg-a-named"
        )
        _certify(environment, storage, agent=a2)
        _failed_pair(_bundle(environment, a2).id)

        routing = _rows(_report(environment, a2))["tool_routing"]

        self._assert_users(routing, "tool_signature_changed")

    def test_a_changed_signature_with_an_unreachable_tool(
        self, user, workspace, storage
    ):
        environment, a2 = self._changed_lookup(
            user, workspace, storage, "rd-fixg-b", contract={**WORLD, **UNREACHABLE}
        )

        routing = _rows(_report(environment, a2))["tool_routing"]

        self._assert_users(routing, "tool_signature_changed")

    @pytest.mark.parametrize("tools", [REJECTED_TOOL, TOOLS])
    def test_a_rejected_tool_beside_a_tool_the_world_lacks(
        self, user, workspace, storage, tools
    ):
        environment, a2 = _with_a2(
            user,
            workspace,
            storage,
            f"rd-fixg-mixed-measured-{tools is TOOLS}",
            tools=(*SAME_TOOLS, _tool("send_email", "to")),
        )
        _built(environment, contract=WORLD)
        _certify(
            environment,
            storage,
            agent=a2,
            checks={**PASSED, "tool_contract": "1/2"},
            tools=tools,
        )

        discovery = _rows(_report(environment, a2))["tool_discovery"]

        self._assert_users(discovery, "tool_not_in_world")

    def test_a_reset_failure(self, user, workspace, storage):
        environment = _environment(user, workspace, storage, "rd-fixg-reset")
        _certify(environment, storage, checks={**PASSED, "reset_equivalence": "failed"})

        reset = _rows(_report(environment))["reset"]

        self._assert_users(reset, "reset_failed")
        assert reset["fix"]["action"] == (
            "Rebuild this environment so the world returns to its seeded state between"
            " cases"
        )

    def test_a_fix_of_a_world_row_is_refused(
        self, user, workspace, storage, auth_client, settings
    ):
        settings.HARNESS_READINESS_FIX_ENABLED = True
        environment = _environment(user, workspace, storage, "rd-fixg-refused")
        _certify(
            environment,
            storage,
            checks={**PASSED, "action_behavior": "1/2"},
            actions=FAILED_PROBE,
        )

        with patch(
            "simulate.services.harness_versioning.rebuild.sandbox_provider_configured",
            return_value=True,
        ):
            response = auth_client.post(
                f"{BASE}/{environment.id}/capabilities/fix/",
                {
                    "group": "builder",
                    "idempotency_key": uuid.uuid4().hex,
                    "rows": ["tool_routing"],
                },
                format="json",
            )
            routing = _rows(_get(auth_client, environment.id).json())["tool_routing"]

        assert response.status_code == 409, response.content
        assert response.json()["error"] == "nothing_to_fix"
        assert routing["fix_group"] == "needs_you"
        assert routing["fix_action"]["available"] is False


class TestFixGroups:
    def test_absent_until_switched_on_then_read_from_the_cached_rows(
        self, user, workspace, storage, auth_client, settings
    ):
        environment = _environment(user, workspace, storage, "rd-fixgroups")
        _certify(environment, storage, checks={**PASSED, "schema_and_seed": "failed"})

        off = _get(auth_client, environment.id).json()
        settings.HARNESS_READINESS_FIX_ENABLED = True
        on = _get(auth_client, environment.id).json()

        assert "fix_groups" not in off
        # The same cached report: only the flag changed.
        assert on["computed_at"] == off["computed_at"]
        assert on["fix_groups"] == {
            "builder": {"rows": ["state", "reset"]},
            "needs_you": {"rows": []},
            "recheck": {"rows": []},
        }
        assert HarnessCapabilitySelectionReportSerializer(data=on).is_valid()

    def test_off_while_versioning_is_off(self, settings):
        settings.HARNESS_READINESS_FIX_ENABLED = True
        settings.HARNESS_VERSIONING_ENABLED = False

        assert enabled() is False

    def test_a_run_snapshot_never_keeps_them(
        self, user, workspace, storage, settings, django_capture_on_commit_callbacks
    ):
        settings.HARNESS_READINESS_FIX_ENABLED = True
        environment = _environment(user, workspace, storage, "rd-fixg-run")
        _certify(environment, storage, checks={**PASSED, "schema_and_seed": "failed"})

        run = _committed_run(environment, django_capture_on_commit_callbacks)

        assert run.capability_snapshot["rows"]
        assert "fix_groups" not in run.capability_snapshot

    def test_rows_by_group(self):
        rows = [
            {"id": "start", "fix_group": "needs_you"},
            {"id": "tool_discovery", "fix_group": "recheck"},
            {"id": "tool_routing", "fix_group": "builder"},
            {"id": "state", "fix_group": "builder"},
            # A report cached before rows were grouped.
            {"id": "reset"},
            {"id": "cancel", "fix_group": None},
        ]

        assert fix_groups(rows) == {
            "builder": {"rows": ["tool_routing", "state"]},
            "needs_you": {"rows": ["start"]},
            "recheck": {"rows": ["tool_discovery"]},
        }

    def test_a_snapshot_from_before_fix_groups_still_reads(
        self, user, workspace, storage
    ):
        environment = _environment(user, workspace, storage, "rd-fixg-snapshot")
        _certify(environment, storage, checks={**PASSED, "schema_and_seed": "failed"})
        report = _report(environment)
        for row in report["rows"]:
            row.pop("fix_group")
            if row["fix"]:
                row["fix"].pop("message")

        assert HarnessCapabilityReportSerializer(data=report).is_valid()


class TestFixRunRead:
    def test_no_fix_run_yet(self, user, workspace, storage, auth_client):
        environment = _environment(user, workspace, storage, "rd-fixrun")

        response = _fix_get(auth_client, environment.id)

        assert response.status_code == 200, response.content
        assert response.json() == {"fix": None}

    def test_unknown_environment_is_404(self, auth_client):
        assert _fix_get(auth_client, uuid.uuid4()).status_code == 404

    def test_versioning_disabled_is_404(
        self, user, workspace, storage, auth_client, settings
    ):
        environment = _environment(user, workspace, storage, "rd-fixrun-off")
        settings.HARNESS_VERSIONING_ENABLED = False

        response = _fix_get(auth_client, environment.id)

        assert response.status_code == 404
        assert response.json()["error"] == "versioning_disabled"

    def test_another_workspace_cannot_read_it(
        self, user, workspace, storage, auth_client
    ):
        from accounts.models import Workspace

        environment = _environment(user, workspace, storage, "rd-fixrun-tenant")
        elsewhere = Workspace.objects.create(
            name="Other workspace",
            organization=user.organization,
            is_default=False,
            is_active=True,
            created_by=user,
        )
        auth_client.set_workspace(elsewhere)

        assert _fix_get(auth_client, environment.id).status_code == 404
