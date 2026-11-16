"""Run readiness Fix: who fixes each row, the builder's message, the flagged
``fix_groups`` and the fix run read."""

import uuid

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

SIGNATURE_MESSAGE = (
    "The agent calls lookup_order with other arguments than this environment takes:"
    " change lookup_order in the test world to take the arguments the agent sends."
)
PROBE_MESSAGE = (
    "issue_refund failed its probe in the test world: fix issue_refund in the test"
    " world so its probe passes."
)
UNREACHABLE = {"tool_entrypoints": [{"tool": "issue_refund", "mode": "unreachable"}]}
UNREACHABLE_MESSAGE = (
    "issue_refund has no reachable implementation in the test world: give"
    " issue_refund one there that the agent's calls reach."
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
    "The test world's schema and seed check failed: fix the environment's schema"
    " and seed data so the world is seeded and its state check passes."
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
                "builder",
                "issue_refund failed its probe in the test world: fix issue_refund in"
                " the test world so its probe passes.",
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
                "builder",
                "The world did not return to its seeded state between cases: fix the"
                " environment's reset so every case starts from the seeded data.",
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
        ("contract", "group", "message"),
        [
            (PRODUCTION_ONLY, "needs_you", None),
            (
                {"tool_entrypoints": [{"tool": "issue_refund", "mode": "unreachable"}]},
                "builder",
                "issue_refund has no reachable implementation in the test world: give"
                " issue_refund one there that the agent's calls reach.",
            ),
        ],
    )
    def test_the_contract_s_routing_findings(
        self, user, workspace, storage, contract, group, message
    ):
        environment = _environment(
            user, workspace, storage, f"rd-fixg-contract-{group}"
        )
        _certify(environment, storage)
        _built(environment, contract=contract)

        routing = _rows(_report(environment))["tool_routing"]

        assert routing["status"] == "blocked"
        assert (routing["fix_group"], routing["fix"]["message"]) == (group, message)

    def test_a_tool_the_world_lacks_is_the_builders_and_names_only_that_tool(
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

        discovery = _rows(_report(environment, a2))["tool_discovery"]

        assert discovery["reason_code"] == "tool_not_in_world"
        # The world tool the agent lacks is the user's part: never sent to the builder.
        assert {"kind": "tool", "name": "issue_refund", "result": "failed"} in (
            discovery["targets"]
        )
        assert discovery["fix_group"] == "builder"
        assert discovery["fix"]["message"] == (
            "The agent calls send_email, which this environment does not have: add"
            " send_email to the test world so the agent's calls land there and its"
            " probe passes."
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

    def test_a_changed_signature_is_the_builders(self, user, workspace, storage):
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
            "builder",
        )
        assert routing["fix"]["message"] == (
            "The agent calls issue_refund with other arguments than this environment"
            " takes: change issue_refund in the test world to take the arguments the"
            " agent sends."
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


class TestMixedRows:
    """A builder reason with the user's part on the same row: the row stays the
    builder's and its message names only the builder's part."""

    def test_a_probe_failure_naming_no_tool_sends_its_reason(
        self, user, workspace, storage
    ):
        environment = _environment(user, workspace, storage, "rd-fixg-ratio")
        _certify(environment, storage, checks={**PASSED, "action_behavior": "1/2"})

        routing = _rows(_report(environment))["tool_routing"]

        assert (routing["reason_code"], routing["fix_group"]) == (
            "action_failed",
            "builder",
        )
        assert routing["fix"]["message"] == (
            "1 of 2 tool probes passed: fix the failing tools in the test world so"
            " their probes pass."
        )

    def test_a_production_tool_on_a_changed_signature_row_is_left_out(
        self, user, workspace, storage
    ):
        environment, a2 = _with_a2(
            user,
            workspace,
            storage,
            "rd-fixg-mixed-structural",
            tools=(
                _tool("lookup_order", "order_id", "region"),
                _tool("issue_refund", "order_id", "amount"),
            ),
        )
        _built(environment, contract={**WORLD, **PRODUCTION_ONLY})

        routing = _rows(_report(environment, a2))["tool_routing"]

        assert routing["reason_code"] == "tool_signature_changed"
        assert [one["name"] for one in routing["targets"]] == [
            "lookup_order",
            "issue_refund",
        ]
        assert routing["fix_group"] == "builder"
        assert routing["fix"]["message"] == (
            "The agent calls lookup_order with other arguments than this environment"
            " takes: change lookup_order in the test world to take the arguments the"
            " agent sends."
        )

    @pytest.mark.parametrize("tools", [REJECTED_TOOL, TOOLS])
    def test_a_rejected_tool_beside_a_tool_the_world_lacks_is_left_out(
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

        assert discovery["reason_code"] == "tool_not_in_world"
        assert discovery["fix_group"] == "builder"
        assert discovery["fix"]["message"] == (
            "The agent calls send_email, which this environment does not have: add"
            " send_email to the test world so the agent's calls land there and its"
            " probe passes."
        )


class TestMixedBuilderRows:
    """A row holding several builder parts sends every one of them."""

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

    def test_a_changed_signature_with_a_named_failed_probe(
        self, user, workspace, storage
    ):
        environment, a2 = self._changed_lookup(
            user, workspace, storage, "rd-fixg-a-named"
        )
        _certify(environment, storage, agent=a2)
        _failed_pair(_bundle(environment, a2).id)

        routing = _rows(_report(environment, a2))["tool_routing"]

        assert (routing["reason_code"], routing["fix_group"]) == (
            "tool_signature_changed",
            "builder",
        )
        assert routing["fix"]["message"] == f"{SIGNATURE_MESSAGE} {PROBE_MESSAGE}"

    def test_a_changed_signature_with_a_failed_probe_ratio(
        self, user, workspace, storage
    ):
        environment, a2 = self._changed_lookup(
            user, workspace, storage, "rd-fixg-a-ratio"
        )
        _certify(
            environment, storage, agent=a2, checks={**PASSED, "action_behavior": "1/2"}
        )

        routing = _rows(_report(environment, a2))["tool_routing"]

        # Neither side names a target the other does: the row keeps none.
        assert (routing["reason_code"], routing["targets"]) == (
            "tool_signature_changed",
            [],
        )
        assert routing["fix"]["message"] == (
            f"{SIGNATURE_MESSAGE} 1 of 2 tool probes passed: fix the failing tools in"
            " the test world so their probes pass."
        )

    def test_a_changed_signature_with_an_unreachable_tool(
        self, user, workspace, storage
    ):
        environment, a2 = self._changed_lookup(
            user, workspace, storage, "rd-fixg-b", contract={**WORLD, **UNREACHABLE}
        )

        routing = _rows(_report(environment, a2))["tool_routing"]

        assert routing["reason_code"] == "tool_signature_changed"
        assert routing["fix"]["message"] == (
            f"{SIGNATURE_MESSAGE} {UNREACHABLE_MESSAGE}"
        )

    def test_a_failed_probe_with_an_unreachable_tool(self, user, workspace, storage):
        environment = _environment(user, workspace, storage, "rd-fixg-c")
        _certify(environment, storage)
        _failed_pair(_bundle(environment).id, action="lookup_order")
        _built(environment, contract=UNREACHABLE)

        routing = _rows(_report(environment))["tool_routing"]

        assert (routing["reason_code"], routing["fix_group"]) == (
            "action_failed",
            "builder",
        )
        assert routing["fix"]["message"] == (
            f"{UNREACHABLE_MESSAGE} lookup_order failed its probe in the test world:"
            " fix lookup_order in the test world so its probe passes."
        )


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
