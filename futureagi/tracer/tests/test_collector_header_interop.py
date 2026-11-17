"""The real producer must reproduce the bytes consumed by Go's ingress test."""

import json
import socket
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from simulate.services import sim_collector_emit
from tfc import service_envelope
from tracer.services import collector_ingest


def test_platform_producer_matches_native_ingress_fixture(settings, monkeypatch):
    fixture = json.loads(
        (
            Path(__file__).resolve().parents[3]
            / "fi-collector/pkg/auth/testdata/platform_emit_headers.txt"
        ).read_text()
    )
    attempts = []

    def refuse_network(*args, **kwargs):
        attempts.append(True)
        raise AssertionError("network forbidden in collector producer interop test")

    monkeypatch.setattr(socket, "getaddrinfo", refuse_network)
    monkeypatch.setattr(socket.socket, "connect", refuse_network)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse_network)
    settings.COLLECTOR_SERVICE_GRANTS_ENABLED = True
    settings.API_SECRET_PEPPER = fixture["pepper"]
    settings.SERVICE_ENVELOPE_KID = "k1"
    settings.SERVICE_ENVELOPE_RETIRED_PEPPER_KIDS = ()
    settings.API_SECRET_PEPPERS_RETIRED = ()
    monkeypatch.setattr(
        service_envelope, "time", SimpleNamespace(time=lambda: fixture["now"])
    )
    monkeypatch.setattr(
        service_envelope,
        "uuid",
        SimpleNamespace(uuid4=lambda: uuid.UUID(hex=fixture["identifier"])),
    )
    # Only persistence is substituted. Signing, producer plumbing, span conversion
    # and OTLP exporter construction are the real production functions.
    orgs, workspaces = Mock(), Mock()
    orgs.return_value.exists.return_value = True
    workspaces.return_value.exists.return_value = True
    monkeypatch.setattr(collector_ingest.Organization.objects, "filter", orgs)
    monkeypatch.setattr(collector_ingest.Workspace._base_manager, "filter", workspaces)
    exporter = Mock()
    exporter.export.return_value = sim_collector_emit.SpanExportResult.SUCCESS
    factory = Mock(return_value=exporter)
    monkeypatch.setattr(sim_collector_emit, "OTLPSpanExporter", factory)
    spans = [
        {
            "name": "synthetic-interop",
            "trace_id": "1" * 32,
            "span_id": "2" * 16,
            "start_time": 1,
            "end_time": 2,
            "attributes": {},
        }
    ]
    result = collector_ingest.emit_spans_to_collector(
        spans,
        project_name="interop",
        project_type="observe",
        organization_id=fixture["organization"],
        workspace_id=fixture["workspace"],
        service_name="fi-provider",
    )
    assert result == 1
    factory.assert_called_once()
    assert factory.call_args.kwargs["headers"] == tuple(
        tuple(pair) for pair in fixture["headers"]
    )
    assert "x-fa-grant" not in dict(factory.call_args.kwargs["headers"])
    exporter.export.assert_called_once()
    assert len(exporter.export.call_args.args[0]) == 1
    exporter.shutdown.assert_called_once_with()
    orgs.assert_called_once_with(pk=fixture["organization"])
    workspaces.assert_called_once_with(
        pk=fixture["workspace"],
        organization_id=fixture["organization"],
        is_active=True,
        deleted=False,
    )
    assert attempts == []
