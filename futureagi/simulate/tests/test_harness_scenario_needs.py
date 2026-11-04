import pytest

from simulate.models import HostedHarnessJob
from simulate.services import harness_check_reading, harness_scenario_needs
from simulate.tests.test_harness_affected_scenarios import (
    LOOKUP,
    REFUND,
    SUB_GOALS,
    _doc,
    _select,
    _suite,
    _ten,
)
from simulate.tests.test_harness_readiness import (
    PRODUCTION_ONLY,
    _built,
    _certify,
    _environment,
)
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


def _check(*lines):
    return "def check(world, calls):\n" + "".join(f"    {line}\n" for line in lines)


REFUSAL = {
    "name": "no_refund",
    "check": _check(
        f"wrong = [c for c in calls if c.name == '{REFUND}' and c.ok]",
        "if wrong:",
        "    return 'refunded'",
    ),
}
EITHER_TOOL = {
    "name": "either_tool",
    "check": _check(
        f"if not any(c.name in ('{LOOKUP}', '{REFUND}') for c in calls):",
        "    return 'neither was called'",
    ),
}


def _connect_only(environment):
    payload = {**environment.payload, "source": {"kind": "provider"}}
    HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
        payload=payload
    )
    environment.refresh_from_db()


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
        _connect_only(environment)

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


@pytest.mark.parametrize(
    ("lines", "needed", "clear"),
    [
        (
            (f"if not any(c.name == '{LOOKUP}' for c in calls):", "    return 'no'"),
            {LOOKUP},
            True,
        ),
        (
            (
                f"done = [c for c in calls if c.name == '{LOOKUP}' and c.ok]",
                "return None if done else 'no'",
            ),
            {LOOKUP},
            True,
        ),
        (
            (
                f"a = [c for c in calls if c.name == '{LOOKUP}']",
                f"b = [c for c in calls if c.name == '{REFUND}']",
                "if not a or not b:",
                "    return 'no'",
            ),
            {LOOKUP, REFUND},
            True,
        ),
        (
            (
                "for c in calls:",
                f"    if c.name == '{REFUND}':",
                "        return 'refunded'",
            ),
            set(),
            True,
        ),
        (
            (
                f"if any(c.name == '{REFUND}' and c.ok for c in calls) and world:",
                "    return 'refunded'",
            ),
            set(),
            True,
        ),
        (
            (
                f"if len([c for c in calls if c.name == '{LOOKUP}']) == 0:",
                "    return 'no'",
            ),
            set(),
            False,
        ),
        (
            (
                f"if not any(c.name == '{LOOKUP}' or c.name == '{REFUND}' for c in calls):",
                "    return 'no'",
            ),
            set(),
            False,
        ),
        (
            (
                f"a = [c for c in calls if c.name == '{LOOKUP}']",
                f"b = [c for c in calls if c.name == '{REFUND}']",
                "if not a and not b:",
                "    return 'no'",
            ),
            set(),
            False,
        ),
    ],
)
def test_check_reading_tells_a_needed_tool_from_a_forbidden_one(lines, needed, clear):
    reading = harness_check_reading.read_check(_check(*lines))

    assert reading.tools == needed
    assert reading.clear is clear


class TestConnectOnlyRoles:
    def test_forbidden_tool_is_not_needed(self, user, workspace, storage):
        environment = _environment(user, workspace, storage, "as-forbidden")
        _connect_only(environment)

        needs = _needs(
            environment,
            [
                _doc("refuses", sub_goals=("no_refund",)),
                _doc("looks-up", sub_goals=("looked_up", "no_refund")),
            ],
            sub_goals=[*SUB_GOALS, REFUSAL],
        )

        assert (needs["refuses"].tools, needs["refuses"].unknown) == (
            frozenset(),
            False,
        )
        assert (needs["looks-up"].tools, needs["looks-up"].unknown) == (
            {LOOKUP},
            False,
        )

    def test_unreadable_role_is_untested_never_blocked(self, user, workspace, storage):
        """A refund tool reaching production holds back neither the refusal case nor
        the case whose check takes either tool: one needs no tool, the other's need
        cannot be read."""
        environment = _environment(user, workspace, storage, "as-either")
        _connect_only(environment)
        _suite(
            environment,
            [
                _doc("refuses", sub_goals=("no_refund",)),
                _doc("either", sub_goals=("either_tool",)),
            ],
            sub_goals=[*SUB_GOALS, REFUSAL, EITHER_TOOL],
        )
        _certify(environment, storage)
        _built(environment, contract=PRODUCTION_ONLY)

        chosen = _select(environment)["selection"]

        assert chosen["lists"]["eligible"]["keys"] == ["refuses"]
        assert chosen["lists"]["untested"]["keys"] == ["either"]
        assert chosen["blocked"] == 0


class TestCache:
    def test_needs_follow_the_environments_connect_only_reading(
        self, user, workspace, storage
    ):
        environment = _environment(user, workspace, storage, "as-cache-connect")
        _suite(environment, [_doc("checked")])

        def tools():
            (one,) = harness_scenario_needs.case_needs(
                environment_version=environment.environment_version,
                suite_version=environment.suite_version,
            )
            return one.tools

        before = tools()
        _connect_only(environment)

        assert (before, tools()) == (frozenset(), {LOOKUP})

    def test_needs_are_cached_with_an_expiry(
        self, user, workspace, storage, monkeypatch
    ):
        environment = _environment(user, workspace, storage, "as-cache-expiry")
        _suite(environment, _ten())
        timeouts = []
        store = harness_scenario_needs.cache.set

        def recorded(key, value, timeout=None):
            timeouts.append(timeout)
            return store(key, value, timeout=timeout)

        monkeypatch.setattr(harness_scenario_needs.cache, "set", recorded)

        _select(environment)

        assert timeouts and all(isinstance(one, int) and one > 0 for one in timeouts)
