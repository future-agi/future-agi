"""PROPOSED trace refusal copy; resource status and exception stay unchanged."""

import socket
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

MESSAGE = (
    "This request could not be completed. Try again later; "
    "if it keeps happening, contact your administrator."
)
pytestmark = pytest.mark.unit


@pytest.fixture
def otel(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("Network disabled for trace usage-copy tests")

    for name in [
        "create_connection",
        "getaddrinfo",
        "gethostbyname",
        "gethostbyname_ex",
    ]:
        monkeypatch.setattr(socket, name, blocked)
    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)
    from tracer.utils import otel

    return otel


@pytest.mark.parametrize("missing", [True, False])
@pytest.mark.parametrize("project_type", ["experiment", "observe"])
def test_trace_refusal_keeps_exception_type_and_uses_neutral_copy(
    otel, monkeypatch, missing, project_type
):
    row = (
        None
        if missing
        else SimpleNamespace(status=otel.APICallStatusChoices.RESOURCE_LIMIT.value)
    )
    log = Mock(return_value=row)
    monkeypatch.setattr(otel, "log_and_deduct_cost_for_resource_request", log)
    with pytest.raises(otel.ResourceLimitError) as raised:
        otel._deduct_project_creation_cost(
            "org",
            project_type,
            existing=True,
            workspace="workspace",
            project_id="project",
        )
    assert str(raised.value) == MESSAGE
    call_type = (
        otel.APICallTypeChoices.PROTOTYPE_ADD.value
        if project_type == "experiment"
        else otel.APICallTypeChoices.OBSERVE_ADD.value
    )
    log.assert_called_once_with(
        "org",
        call_type,
        config={"existing": True, "project_id": "project"},
        workspace="workspace",
    )


def test_allowed_trace_returns_the_original_call_log(otel, monkeypatch):
    row = SimpleNamespace(status=otel.APICallStatusChoices.PROCESSING.value)
    monkeypatch.setattr(
        otel, "log_and_deduct_cost_for_resource_request", Mock(return_value=row)
    )
    assert otel._deduct_project_creation_cost("org", "observe") is row
