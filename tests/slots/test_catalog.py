import pytest

from slots.catalog import (
    expand_services,
    parse_isolated_infra,
    parse_services,
    private_ports,
)


def test_services_are_validated_and_dependencies_are_expanded():
    assert parse_services("collector") == ("collector",)
    assert expand_services("collector") == ("backend", "collector")
    assert expand_services("none") == ()
    assert set(expand_services("all")) == {
        "backend",
        "simulation",
        "gateway",
        "collector",
        "serving",
        "executor",
        "peerdb",
        "observability",
    }
    with pytest.raises(ValueError, match="unsupported"):
        parse_services("frontend")
    with pytest.raises(ValueError, match="cannot be combined"):
        parse_services("none,backend")


def test_infra_and_ports_are_deterministic():
    assert parse_isolated_infra("postgres,redis") == ("postgres", "redis")
    assert private_ports(3, ("backend", "collector")) == {
        "frontend": 20310,
        "backend": 20311,
        "collector": 20340,
    }
    assert private_ports(3, (), ("postgres",))["postgres"] == 20301
    with pytest.raises(ValueError):
        private_ports(21, ())
