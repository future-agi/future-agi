import pytest

from simulate.models import HostedHarnessJob
from simulate.services import harness_scenario_needs
from simulate.tests.test_harness_affected_scenarios import (
    LOOKUP,
    REFUND,
    _doc,
    _select,
    _suite,
    _ten,
)
from simulate.tests.test_harness_readiness import _environment
from simulate.tests.test_harness_readiness import (
    readiness_on as readiness_on,  # noqa: F401 - fixture
)
from simulate.tests.test_harness_readiness import (
    scheduled as scheduled,  # noqa: F401 - fixture
)
from simulate.tests.test_harness_readiness import (
    storage as storage,  # noqa: F401 - fixture
)

pytestmark = pytest.mark.django_db


def _needs(environment, documents, **options):
    _suite(environment, documents, **options)
    return {
        one.key: one
        for one in harness_scenario_needs.case_needs(
            environment_version=environment.environment_version,
            suite_version=environment.suite_version,
        )
    }


class TestNeeds:
    def test_tool_on_every_path_is_needed(self, user, workspace, storage):
        environment = _environment(user, workspace, storage, "as-every-path")

        needs = _needs(
            environment,
            [_doc("refund", solution=(LOOKUP, REFUND), alternatives=[(REFUND,)])],
        )["refund"]

        assert needs.tools == {REFUND}
        assert {"tool_discovery", "tool_routing"} <= needs.rows
        assert harness_scenario_needs.affected_by_tool(needs, REFUND)
        assert not harness_scenario_needs.affected_by_tool(needs, LOOKUP)

    def test_tool_on_one_path_is_not(self, user, workspace, storage):
        environment = _environment(user, workspace, storage, "as-one-path")

        needs = _needs(
            environment,
            [_doc("refund", solution=(LOOKUP, REFUND), alternatives=[(LOOKUP,)])],
        )["refund"]

        assert needs.tools == {LOOKUP}

    def test_refusal_needs_no_tool(self, user, workspace, storage):
        environment = _environment(user, workspace, storage, "as-refusal")

        needs = _needs(
            environment,
            [
                _doc("refuse", sub_goals=("polite",)),
                _doc("may-refuse", solution=(REFUND,), alternatives=[()]),
            ],
        )

        for key in ("refuse", "may-refuse"):
            assert needs[key].tools == frozenset()
            assert not needs[key].unknown
            assert not {"tool_discovery", "tool_routing"} & needs[key].rows

    def test_connect_only_tools_come_from_checks(self, user, workspace, storage):
        environment = _environment(user, workspace, storage, "as-connect-only")
        payload = {**environment.payload, "source": {"kind": "provider"}}
        HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
            payload=payload
        )

        needs = _needs(
            environment,
            [_doc("checked"), _doc("judged-only", sub_goals=("polite",))],
        )

        assert needs["checked"].tools == {LOOKUP}
        assert not needs["checked"].unknown
        assert needs["judged-only"].tools == frozenset()
        assert needs["judged-only"].unknown

    def test_check_using_world_needs_state(self, user, workspace, storage):
        environment = _environment(user, workspace, storage, "as-world")

        needs = _needs(
            environment,
            [
                _doc("reads-world", sub_goals=("refunded",)),
                _doc("reads-calls", sub_goals=("looked_up", "polite")),
            ],
        )

        assert "state" in needs["reads-world"].rows
        assert "state" not in needs["reads-calls"].rows

    def test_unknown_sub_goal_is_unknown(self, user, workspace, storage):
        environment = _environment(user, workspace, storage, "as-unknown")

        needs = _needs(
            environment,
            [
                _doc("missing", sub_goals=("looked_up", "never_written")),
                _doc("unparsed", sub_goals=("broken",)),
                _doc("known", sub_goals=("looked_up", "polite")),
            ],
        )

        assert needs["missing"].unknown
        assert needs["unparsed"].unknown
        assert needs["missing"].grading_gap == "check_unreadable"
        assert not needs["known"].unknown
        assert needs["known"].grading_gap is None

    def test_needs_computed_once_per_version_pair(
        self, user, workspace, storage, monkeypatch
    ):
        environment = _environment(user, workspace, storage, "as-cache")
        _suite(environment, _ten())
        computed = []
        compute = harness_scenario_needs._compute

        def counted(*args):
            computed.append(args)
            return compute(*args)

        monkeypatch.setattr(harness_scenario_needs, "_compute", counted)

        for _ in range(3):
            _select(environment)

        assert len(computed) == 1
