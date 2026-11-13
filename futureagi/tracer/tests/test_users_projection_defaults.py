"""Schema defaults must preserve Users' omission-sensitive enrichment boundary."""

from types import SimpleNamespace
from uuid import UUID

import pytest
from rest_framework.test import APIRequestFactory, force_authenticate

from accounts.models.organization import Organization
from accounts.models.user import User
from accounts.models.workspace import Workspace
from tfc.tests.schema_default_helpers import field_schema
from tracer.serializers.trace import UsersQuerySerializer
from tracer.services.users_list_manager import UsersListManager
from tracer.views.trace import UsersView


@pytest.mark.parametrize("query", [True, False], ids=["get", "post"])
def test_users_projection_has_no_documented_default(query):
    field = UsersQuerySerializer().fields["requested_columns"]
    assert field.required is False
    assert "default" not in field_schema(field, query=query)


@pytest.mark.parametrize("method", ["get", "post"])
@pytest.mark.parametrize(
    ("columns", "normalized"),
    [(None, []), ("", []), ("[]", []), ('["num_sessions"]', ["num_sessions"])],
    ids=["omitted", "blank", "empty", "metric"],
)
def test_users_documented_defaults_preserve_enrichment(
    method, columns, normalized, monkeypatch
):
    _assert_projection(method, columns, normalized, monkeypatch)


@pytest.mark.parametrize("columns", [[], ["num_sessions"]], ids=["empty", "metric"])
def test_users_native_post_projection_preserves_enrichment(columns, monkeypatch):
    _assert_projection("post", columns, columns, monkeypatch)


def _assert_projection(method, columns, normalized, monkeypatch):
    project_id = str(UUID(int=11))
    organization = Organization(id=UUID(int=21))
    workspace = Workspace(id=UUID(int=22), organization=organization)
    user = User(id=UUID(int=23), organization=organization)
    data = {"project_id": project_id}
    if columns is not None:
        data["requested_columns"] = columns

    # Model a client materializing the documented default before sending a request.
    schema = field_schema(
        UsersQuerySerializer().fields["requested_columns"], query=method == "get"
    )
    if "default" in schema:
        data.setdefault("requested_columns", schema["default"])
    factory = APIRequestFactory()
    raw = (
        factory.post("/", data, format="json")
        if method == "post"
        else factory.get("/", data)
    )
    raw.organization, raw.workspace = organization, workspace
    force_authenticate(raw, user=user)
    captured = {}

    def project_queryset(request):
        captured["validated"] = request.validated_query_data["requested_columns"]
        return SimpleNamespace(values_list=lambda *args: [(project_id, "Project")])

    def list_payload(manager, **kwargs):
        captured["metrics"] = manager.metric_keys
        captured["evals"] = manager.needs_evals
        return {}

    # Replace I/O only; routing, validation, projection resolution and manager
    # construction all run normally for both the GET and read-query POST alias.
    monkeypatch.setattr(
        "tracer.views.trace._project_queryset_for_request", project_queryset
    )
    monkeypatch.setattr(UsersListManager, "list_payload", list_payload)
    response = UsersView.as_view()(raw)
    assert response.status_code == 200
    assert captured["validated"] == normalized
    assert captured["metrics"] == (
        {
            "num_sessions",
            "avg_session_duration",
            "avg_trace_latency",
            "num_llm_calls",
            "num_guardrails_triggered",
            "num_active_days",
            "num_traces_with_errors",
        }
        if columns is None
        else set(normalized)
    )
    assert captured["evals"] is (columns is None)
