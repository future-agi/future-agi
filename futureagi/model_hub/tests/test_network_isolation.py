"""The test network guard refuses non-local attempts before transport."""

import socket

import pytest

from model_hub.models.choices import DataTypeChoices, SourceChoices
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.tests.network_isolation import (
    GUARD,
    ExternalNetworkRefused,
    configured_service_hosts,
    fail_on_attempts,
    stub_eval_recommender,
    stub_url_resolution,
)

# Reserved targets: TEST-NET-1 (RFC 5737) and the .invalid TLD (RFC 2606),
# plus the cloud metadata service the guard must always refuse.
CONTROL_ADDRESS = "192.0.2.1"
CONTROL_NAME = "lookup-control.invalid"
METADATA_ADDRESS = "169.254.169.254"
METADATA_NAME = "metadata.google.internal"


def test_guard_refuses_lookups_connects_and_datagrams_before_transport():
    with GUARD.watch() as attempts:
        with pytest.raises(ExternalNetworkRefused, match="before transport"):
            socket.getaddrinfo(CONTROL_NAME, 443)
        with pytest.raises(ExternalNetworkRefused, match="before transport"):
            socket.gethostbyname(CONTROL_NAME)
        with socket.socket() as stream:
            stream.settimeout(0.5)
            with pytest.raises(ExternalNetworkRefused, match="before transport"):
                stream.connect((CONTROL_ADDRESS, 443))
        with socket.socket(type=socket.SOCK_DGRAM) as datagram:
            with pytest.raises(ExternalNetworkRefused, match="before transport"):
                datagram.sendto(b"control", (CONTROL_ADDRESS, 9))
        with pytest.raises(ExternalNetworkRefused, match="before transport"):
            socket.getaddrinfo(METADATA_NAME, 80)
        with pytest.raises(ExternalNetworkRefused, match="before transport"):
            socket.getaddrinfo(METADATA_ADDRESS, 80)

    assert [(a.event, a.host) for a in attempts] == [
        ("socket.getaddrinfo", CONTROL_NAME),
        ("socket.gethostbyname", CONTROL_NAME),
        ("socket.connect", CONTROL_ADDRESS),
        ("socket.sendto", CONTROL_ADDRESS),
        ("socket.getaddrinfo", METADATA_NAME),
        ("socket.getaddrinfo", METADATA_ADDRESS),
    ]
    assert not GUARD.active


def test_guard_allows_loopback_traffic():
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]
        with GUARD.watch() as attempts:
            socket.getaddrinfo("localhost", port)
            with socket.create_connection(("127.0.0.1", port), timeout=2):
                pass
    assert attempts == []


def test_guard_is_inert_outside_a_watch():
    assert not GUARD.active
    assert GUARD.allowed("127.0.0.1")
    assert GUARD.allowed("::1")
    assert not GUARD.allowed(CONTROL_ADDRESS)


def test_metadata_service_is_never_a_configured_service_host():
    hosts = configured_service_hosts(
        {
            "PG_HOST": METADATA_ADDRESS,
            "REDIS_URL": "redis://localhost:6379/0",
            "MINIO_ENDPOINT": "minio:9000",
        }
    )
    assert METADATA_ADDRESS not in hosts
    assert "minio" in hosts
    assert "localhost" not in hosts


def test_any_recorded_attempt_fails_the_test():
    with GUARD.watch() as attempts:
        with pytest.raises(ExternalNetworkRefused):
            socket.getaddrinfo(CONTROL_NAME, 443)
    with pytest.raises(pytest.fail.Exception, match="1 network attempt"):
        fail_on_attempts(attempts)
    fail_on_attempts([])


def test_stub_url_resolution_answers_only_the_named_hosts(monkeypatch):
    import tfc.utils.ssrf_guard as ssrf_guard

    asked = stub_url_resolution(
        monkeypatch, {METADATA_ADDRESS: [METADATA_ADDRESS]}, "tfc.utils.ssrf_guard"
    )
    with GUARD.watch() as attempts:
        with pytest.raises(ssrf_guard.SsrfBlocked, match="private/internal"):
            ssrf_guard.assert_url_host_public(f"http://{METADATA_ADDRESS}/latest/")
        with pytest.raises(ssrf_guard.SsrfBlocked, match="Cannot resolve"):
            ssrf_guard.assert_url_host_public(f"http://{CONTROL_NAME}/")
    assert asked == [METADATA_ADDRESS, CONTROL_NAME]
    assert attempts == []


@pytest.mark.requires_ee
@pytest.mark.django_db
def test_stub_eval_recommender_answers_recommend_evals(
    organization, workspace, user, monkeypatch
):
    from model_hub.views.utils.utils import get_recommendations

    dataset = Dataset.no_workspace_objects.create(
        name="Recommender Stub Dataset",
        organization=organization,
        workspace=workspace,
        user=user,
        column_order=[],
        column_config={},
    )
    column = Column.no_workspace_objects.create(
        name="question",
        dataset=dataset,
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.OTHERS.value,
    )
    dataset.column_order = [str(column.id)]
    dataset.save(update_fields=["column_order"])
    row = Row.no_workspace_objects.create(dataset=dataset, order=0)
    Cell.no_workspace_objects.create(
        dataset=dataset, column=column, row=row, value="What is 2 + 2?"
    )
    recommender = stub_eval_recommender(monkeypatch, recommended=("Toxicity",))

    with GUARD.watch() as attempts:
        get_recommendations(dataset)

    assert recommender.installed
    recommender.assert_reached(columns=["question"])
    assert "What is 2 + 2?" in recommender.prompts[0]
    dataset.refresh_from_db()
    assert dataset.dataset_config["eval_recommendations"] == [
        "Toxicity",
        "Deterministic Evals",
    ]
    assert attempts == []
