import pytest

from simulate.services.harness_capacity import select_capacity


def profile(name, width):
    return {
        "name": name,
        "cpu_units": width + 1,
        "memory_mb": (width + 2) * 1024,
        "disk_gb": 20,
        "max_parallelism": width,
        "connectors": ["livekit", "vapi", "retell", "chat"],
        "snapshot_name": name,
        "snapshot_digest": "sha256:" + "a" * 64,
    }


@pytest.mark.parametrize("connector", ["livekit", "vapi", "retell", "chat"])
def test_selects_smallest_profile_meeting_demand(connector):
    result = select_capacity(
        {"parallelism": 3},
        scenario_count=10,
        connector=connector,
        profiles=[profile("large", 8), profile("small", 4)],
    )
    assert result.name == "small"
    assert result.parallelism == 3


def test_large_requested_width_is_capped_not_rejected():
    result = select_capacity(
        {"parallelism": 1000},
        scenario_count=200,
        connector="vapi",
        profiles=[profile("small", 4), profile("large", 8)],
    )
    assert result.parallelism == 8
    assert result.cpu_units == 9


def test_scenario_count_limits_resources():
    result = select_capacity(
        {"parallelism": 100},
        scenario_count=2,
        connector="chat",
        profiles=[profile("small", 2), profile("large", 8)],
    )
    assert result.name == "small"
    assert result.parallelism == 2


def test_lower_operator_ceiling_caps_an_existing_larger_profile():
    result = select_capacity(
        {"parallelism": 10},
        scenario_count=10,
        connector="chat",
        profiles=[profile("large", 8)],
        ceiling=2,
    )
    assert result.parallelism == 2


def test_unknown_connector_fails_closed():
    with pytest.raises(ValueError, match="no certified"):
        select_capacity(
            {"parallelism": 2},
            scenario_count=4,
            connector="unknown",
            profiles=[profile("small", 2)],
        )


@pytest.mark.parametrize("connectors", [None, "vapi", ["vapi", 1]])
def test_malformed_connector_catalog_fails_closed(connectors):
    item = {**profile("malformed", 2), "connectors": connectors}
    with pytest.raises(ValueError, match="invalid certified"):
        select_capacity(
            {"parallelism": 2}, scenario_count=4, connector="vapi", profiles=[item]
        )


def test_snapshot_profile_requires_digest():
    item = profile("small", 2)
    item.pop("snapshot_digest")
    with pytest.raises(ValueError, match="invalid certified"):
        select_capacity(
            {"parallelism": 2}, scenario_count=4, connector="chat", profiles=[item]
        )


def test_without_catalog_resources_are_not_automatically_increased():
    result = select_capacity(
        {"parallelism": 10, "cpu_units": 2, "memory_mb": 4096},
        scenario_count=20,
        connector="chat",
        profiles=[],
    )
    assert (result.cpu_units, result.memory_mb, result.parallelism) == (2, 4096, 1)


@pytest.mark.parametrize(
    "runtime",
    [
        {"cpu_units": 1, "memory_mb": 8192},
        {"cpu_units": 8, "memory_mb": 1024},
    ],
)
def test_fixed_resources_must_fit_even_one_world(runtime):
    with pytest.raises(ValueError, match="insufficient"):
        select_capacity(runtime, scenario_count=1, connector="chat", profiles=[])


def test_configured_selection_does_not_offer_parallel_slots_on_unlisted_image(settings):
    from simulate.services.harness_capacity import configured_capacity

    settings.HARNESS_PARALLELISM_ENABLED = True
    settings.ALK_DAYTONA_DOCKERFILE = ""
    settings.HARNESS_PARALLEL_SNAPSHOT_DIGESTS = ["sha256:" + "b" * 64]
    small = profile("certified", 2)
    small["snapshot_digest"] = "sha256:" + "b" * 64
    settings.HARNESS_RESOURCE_PROFILES = [profile("uncertified", 8), small]
    result = configured_capacity(
        {
            "runtime": {"parallelism": 10},
            "scenario_count": 10,
            "agent": {"connector": "chat"},
        }
    )
    assert result.name == "certified"
    assert result.parallelism == 2


def test_configured_selection_resolves_auto_from_secret_alias(settings):
    from simulate.services.harness_capacity import configured_capacity

    settings.HARNESS_PARALLELISM_ENABLED = True
    settings.ALK_DAYTONA_DOCKERFILE = ""
    settings.HARNESS_PARALLEL_SNAPSHOT_DIGESTS = ["sha256:" + "b" * 64]
    settings.HARNESS_RESOURCE_PROFILES = [profile("vapi", 4)]
    settings.HARNESS_RESOURCE_PROFILES[0]["snapshot_digest"] = "sha256:" + "b" * 64
    result = configured_capacity(
        {
            "runtime": {"parallelism": 3},
            "scenario_count": 5,
            "agent": {
                "connector": "auto",
                "config": {},
                "secret_refs": {"VAPI_API_KEY": {"purpose": "target_provider"}},
            },
        }
    )
    assert result.name == "vapi"
    assert result.parallelism == 3


def test_e2b_capacity_uses_template_resources_and_certified_build(settings):
    from simulate.services.harness_capacity import configured_capacity

    settings.HOSTED_SANDBOX_PROVIDER = "e2b"
    settings.ALK_DAYTONA_DOCKERFILE = "/hosted/Dockerfile"
    settings.ALK_E2B_TEMPLATE_REFERENCE = "alk-hosted-e2b:build-123"
    settings.ALK_E2B_TEMPLATE_BUILD_ID = "build-123"
    settings.ALK_E2B_TEMPLATE_CPU_UNITS = 4
    settings.ALK_E2B_TEMPLATE_MEMORY_MB = 8192
    settings.ALK_E2B_TEMPLATE_DISK_GB = 12
    settings.HARNESS_PARALLELISM_ENABLED = True
    settings.HARNESS_PARALLEL_SNAPSHOT_DIGESTS = ["build-123"]
    settings.HARNESS_RESOURCE_PROFILES = []

    payload = {
        "runtime": {"parallelism": 4, "cpu_units": 4, "memory_mb": 8192},
        "scenario_count": 4,
        "agent": {"connector": "chat"},
    }
    assert configured_capacity(payload).parallelism == 4
    defaulted = configured_capacity(
        {
            **payload,
            "runtime": {"parallelism": 4},
        }
    )
    assert (
        defaulted.cpu_units,
        defaulted.memory_mb,
        defaulted.disk_gb,
    ) == (4, 8192, 12)
    with pytest.raises(ValueError, match="exceeds provider runtime resources"):
        configured_capacity(
            {**payload, "runtime": {**payload["runtime"], "cpu_units": 8}}
        )
    settings.HARNESS_RESOURCE_PROFILES = [
        {
            **profile("wrong-template", 2),
            "cpu_units": 4,
            "memory_mb": 8192,
            "disk_gb": 10,
        }
    ]
    with pytest.raises(ValueError, match="does not match provider runtime"):
        configured_capacity(payload)


@pytest.mark.parametrize(
    "resources",
    [
        {"cpu_units": 1, "memory_mb": 8192},
        {"cpu_units": 4, "memory_mb": 1024},
    ],
)
def test_profile_must_fit_control_process_and_at_least_one_world(resources):
    item = {**profile("too-small", 1), **resources}
    with pytest.raises(ValueError, match="invalid certified"):
        select_capacity({}, scenario_count=1, connector="chat", profiles=[item])


def test_profile_width_cannot_exceed_guest_resource_budget():
    item = {**profile("limited-memory", 4), "memory_mb": 2048}
    result = select_capacity(
        {"parallelism": 4},
        scenario_count=4,
        connector="chat",
        profiles=[item],
    )
    assert result.parallelism == 1
