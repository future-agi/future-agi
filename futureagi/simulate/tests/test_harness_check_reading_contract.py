"""ALK's cases for whether a check reads world state (agent-learning-kit ``fi.alk.harness.checks.check_reads``),
which the platform's one copy must answer the same way."""

import pytest

from simulate.services.harness_check_reading import read_check

# (check source, reads world state) as ALK's check_reads answers.
ALK_CASES = [
    (
        "def check(world, calls):\n    return None if world.orders else 'no order'\n",
        True,
    ),
    (
        "def check(world, calls):\n    return None if world.events else 'nothing logged'\n",
        False,
    ),
    ("def check(world, calls):\n    return None if calls else 'no call'\n", False),
    ("def check(world, calls):\n    return helper(world)\n", True),
    (
        "def check(world, calls):\n    seen = world\n    return None if seen.events else 'no'\n",
        True,
    ),
    (
        "def check(world, calls):\n    return None if world.events and world.refunds else 'no'\n",
        True,
    ),
]


@pytest.mark.parametrize("source, state", ALK_CASES)
def test_world_state_reading_matches_alk(source, state):
    reading = read_check(source)
    assert reading is not None
    assert reading.world is state
