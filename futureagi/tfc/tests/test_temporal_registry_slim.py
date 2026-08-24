"""Slim-image behavior of the temporal workflow registry.

On the slim OSS image the `voice` extra's packages are stripped, so
importing `ee.voice.temporal.workflows.*` raises ImportError. Historically
one try/except wrapped BOTH the voice workflows and the plain text-sim
orchestrators (TestExecutionWorkflow / RerunCoordinatorWorkflow), so a
missing voice extra silently took out text simulation too. The registry
now registers them in separate guarded blocks; this test simulates the
slim image by blocking `ee.voice` imports and asserts text simulation
still registers on the `tasks_l` queue.
"""

from __future__ import annotations

import importlib.abc
import sys

import pytest
from tfc.temporal.common import registry


class _BlockEeVoice(importlib.abc.MetaPathFinder):
    """Meta-path finder that makes any `ee.voice*` import fail, the way it
    does on a slim image where the voice extra's deps are absent."""

    def find_spec(self, fullname, path=None, target=None):
        if fullname == "ee.voice" or fullname.startswith("ee.voice."):
            raise ImportError(f"blocked to simulate slim image: {fullname}")
        return None


@pytest.fixture
def slim_registry():
    """Fresh registry state with ee.voice imports blocked; restores
    everything (registry contents, flag, sys.modules, meta_path) after."""
    saved_workflows = dict(registry._workflow_registry)
    saved_flag = registry._workflows_registered
    registry._workflow_registry.clear()
    registry._workflows_registered = False

    purged = {
        name: sys.modules.pop(name)
        for name in list(sys.modules)
        if name == "ee.voice" or name.startswith("ee.voice.")
    }
    blocker = _BlockEeVoice()
    sys.meta_path.insert(0, blocker)
    try:
        yield registry
    finally:
        sys.meta_path.remove(blocker)
        sys.modules.update(purged)
        registry._workflow_registry.clear()
        registry._workflow_registry.update(saved_workflows)
        registry._workflows_registered = saved_flag


def test_text_simulation_registers_without_voice_extra(slim_registry):
    workflows = slim_registry.get_workflows_for_queue("tasks_l")
    names = {w.__name__ for w in workflows}

    # Text-simulation orchestrators must survive the missing voice extra.
    assert "TestExecutionWorkflow" in names
    assert "RerunCoordinatorWorkflow" in names

    # Voice workflows are legitimately absent on the slim image.
    assert "CallExecutionWorkflow" not in names
    assert "CallDispatcherWorkflow" not in names
    assert "PhoneNumberDispatcherWorkflow" not in names


def test_voice_workflows_register_when_ee_voice_importable():
    """Inverse guard: in a full (all-extras) environment the voice
    workflows do register — the split must not drop them."""
    saved_workflows = dict(registry._workflow_registry)
    saved_flag = registry._workflows_registered
    registry._workflow_registry.clear()
    registry._workflows_registered = False
    try:
        names = {w.__name__ for w in registry.get_workflows_for_queue("tasks_l")}
    finally:
        registry._workflow_registry.clear()
        registry._workflow_registry.update(saved_workflows)
        registry._workflows_registered = saved_flag

    assert {
        "TestExecutionWorkflow",
        "RerunCoordinatorWorkflow",
        "CallExecutionWorkflow",
        "CallDispatcherWorkflow",
        "PhoneNumberDispatcherWorkflow",
    } <= names
