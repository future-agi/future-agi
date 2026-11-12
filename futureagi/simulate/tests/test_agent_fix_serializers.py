"""A fix's tested environment identity is independent of the current default."""

from types import SimpleNamespace
from uuid import UUID

import pytest

from simulate.serializers.agent_fix import (
    AgentFixRunDetailSerializer,
    AgentFixRunSerializer,
)

pytestmark = pytest.mark.unit


@pytest.fixture(params=[AgentFixRunSerializer, AgentFixRunDetailSerializer])
def serializer(request):
    return request.param()


@pytest.mark.parametrize("current_version_number", [2, None])
def test_fix_environment_keeps_source_version_after_environment_edit(
    serializer, current_version_number
):
    tested_version = SimpleNamespace(version_number=1)
    original_default = UUID(int=1)
    current_default = UUID(int=2)
    environment = SimpleNamespace(
        name="Support checks",
        environment_version=tested_version,
        agent_version_id=original_default,
    )
    source_run = SimpleNamespace(
        environment_version=tested_version,
        agent_version_id=original_default,
    )
    fix_run = SimpleNamespace(environment=environment, source_run=source_run)

    assert serializer.get_environment(fix_run) == {
        "name": "Support checks",
        "version": 1,
        "default_agent_version_id": original_default,
    }

    environment.environment_version = (
        SimpleNamespace(version_number=current_version_number)
        if current_version_number is not None
        else None
    )
    environment.agent_version_id = current_default

    # The checked version stays historical; shipping status follows today's default.
    assert serializer.get_environment(fix_run) == {
        "name": "Support checks",
        "version": 1,
        "default_agent_version_id": current_default,
    }


@pytest.mark.parametrize("has_source_run", [True, False])
@pytest.mark.parametrize("current_version_number", [2, None])
def test_fix_environment_uses_current_version_only_for_legacy_unpinned_runs(
    serializer, has_source_run, current_version_number
):
    current_version = (
        SimpleNamespace(version_number=current_version_number)
        if current_version_number is not None
        else None
    )
    current_default = UUID(int=2)
    environment = SimpleNamespace(
        name="Legacy checks",
        environment_version=current_version,
        agent_version_id=current_default,
    )
    source_run = SimpleNamespace(environment_version=None) if has_source_run else None
    fix_run = SimpleNamespace(environment=environment, source_run=source_run)

    assert serializer.get_environment(fix_run) == {
        "name": "Legacy checks",
        "version": current_version_number,
        "default_agent_version_id": current_default,
    }
