"""Test fixtures that preserve capability configuration without changing defaults."""

import pytest


def _capability_bindings():
    from tfc.capabilities import service

    # configure() replaces all five bindings, including *both* resolvers.
    return {
        name: getattr(service, name)
        for name in (
            "_configured",
            "_deployment_flavor",
            "_deployment_location",
            "_license_resolver",
            "_cloud_plan_resolver",
        )
    }


@pytest.fixture
def preserve_capability_service():
    from tfc.capabilities import service

    before = _capability_bindings()
    try:
        yield
    finally:
        for name, previous in before.items():
            setattr(service, name, previous)


@pytest.fixture(scope="module")
def check_capability_isolation():
    """Independent guard for the ordered CI run, outside all function fixtures."""
    from ee.licensing import state

    before = _capability_bindings()
    snapshot = state.get_snapshot()
    yield
    for name, current in _capability_bindings().items():
        if current is not before[name]:
            pytest.fail(f"Capability tests leaked {name}", pytrace=False)
    if state.get_snapshot() is not snapshot:
        pytest.fail("Capability tests leaked the license snapshot", pytrace=False)
