"""The Users setup example keeps the project name as one Python string literal.

The returned source is only parsed, never executed.
"""

import ast
from unittest.mock import patch

import pytest
from rest_framework.test import APIRequestFactory, force_authenticate

from tracer.models.project import Project
from tracer.views.trace import GetUserCodeExampleView

pytestmark = pytest.mark.django_db


def example(user, organization, workspace, project):
    request = APIRequestFactory().get("/", {"project_id": str(project.pk)})
    force_authenticate(request, user=user)
    request.organization, request.workspace = organization, workspace
    response = GetUserCodeExampleView.as_view()(request)
    assert response.status_code == 200
    tree = ast.parse(response.data["result"])
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "register"
    ]
    assert len(calls) == 1
    assert [keyword.arg for keyword in calls[0].keywords] == [
        "project_type",
        "project_name",
    ]
    literal = calls[0].keywords[1].value
    assert isinstance(literal, ast.Constant)
    return literal.value


@pytest.mark.parametrize(
    "name",
    [
        "Plain Project",
        'quote"name',
        "back\\slash",
        "line\nfeed",
        "tab\tname",
        "\rreturn",
        "\x01control",
        "separator\u2028paragraph\u2029",
        "café 日本語",
        'x", unexpected=True, project_name="y',
        "astral\U0001f680",
    ],
)
def test_stored_project_name_round_trips_as_one_literal(
    user, organization, workspace, observe_project, name
):
    Project.no_workspace_objects.filter(pk=observe_project.pk).update(name=name)
    assert example(user, organization, workspace, observe_project) == name


@pytest.mark.parametrize("name", ["nul\x00name", "surrogate\ud800"])
def test_names_postgres_cannot_store_round_trip_as_one_literal(
    user, organization, workspace, observe_project, name
):
    # PostgreSQL cannot store these values, so only the literal encoding is
    # exercised here; no database write path for NUL or lone surrogates exists.
    observe_project.name = name
    with patch("tracer.views.trace.get_object_or_404", return_value=observe_project):
        assert example(user, organization, workspace, observe_project) == name
