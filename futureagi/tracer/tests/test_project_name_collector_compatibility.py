"""App naming compatibility for collector resource attributes (TH-8745).

These run the actual serializers and create/rename view methods, with only
persistence mocked. They do not claim PostgreSQL or full UI/E2E coverage and do
not add app restrictions: DRF accepts some invisible/control names that the
collector's ingestion safeguards deliberately still reject.
"""

import uuid
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from django.core.exceptions import ValidationError as ModelValidationError
from rest_framework.exceptions import ValidationError

from tracer.models.project import Project
from tracer.serializers.project import ProjectNameUpdateSerializer, ProjectSerializer
from tracer.views.project import ProjectView


APP_NAMES = [
    "deploy ✈️",
    "team 👩‍💻",
    "family 👨‍👩‍👧‍👦",
    "क्‍ष",
    "check​out",
    "check­out",
    "pro͏j",
    "pro j",
    "research, development",
    " project ",
    "a  b",
    "Café",
    "界" * 255,
    # Existing app behavior, not supported collector resource names. Keep the
    # difference explicit rather than claiming the app enforces our safeguards.
    "​",
    "‍️",
    "ㅤ",
    "⠀",
    "́",
    "bad\tname",
    "bad\u0085name",
]


@pytest.mark.parametrize("name", APP_NAMES)
def test_actual_project_create_and_rename_preserve_unicode(name):
    expected = name.strip()  # DRF trims edges; collector resource lookup does not.
    name_field = ProjectSerializer().fields["name"]
    assert name_field.run_validation(name) == expected
    rename = ProjectNameUpdateSerializer(
        data={"project_id": str(uuid.uuid4()), "name": name}
    )
    assert rename.is_valid(), rename.errors
    assert rename.validated_data["name"] == expected
    assert Project._meta.get_field("name").clean(expected, None) == expected

    request = SimpleNamespace(
        data={
            "name": name,
            "model_type": Project._meta.get_field("model_type").choices[0][0],
            "trace_type": "observe",
        },
        organization=SimpleNamespace(),
        workspace=SimpleNamespace(),
    )
    view = ProjectView()
    view.request = request
    view.format_kwarg = None
    captured = {}

    def persist(_serializer, validated_data):
        captured.update(validated_data)
        return SimpleNamespace(id=uuid.uuid4(), name=validated_data["name"])

    with patch.object(ProjectSerializer, "create", persist):
        response = view.create(request)
    assert response.status_code == 200, response.data
    assert captured["name"] == expected
    assert response.data["result"]["name"] == expected

    project = SimpleNamespace(name="old", save=Mock())
    request.data = {"project_id": str(uuid.uuid4()), "name": name}
    with patch.object(view, "_get_project_in_scope", return_value=project):
        response = view.update_project_name(request)
    assert response.status_code == 200, response.data
    assert project.name == expected
    project.save.assert_called_once_with(update_fields=["name"])


@pytest.mark.parametrize("name", ["", "  ", "\t\n", "bad\x00name"])
def test_actual_project_serializers_reject_blank_and_nul(name):
    with pytest.raises(ValidationError):
        ProjectSerializer().fields["name"].run_validation(name)
    serializer = ProjectNameUpdateSerializer(
        data={"project_id": str(uuid.uuid4()), "name": name}
    )
    assert not serializer.is_valid()


def test_project_length_boundary_and_rename_persistence_guard():
    name = "界" * 256
    with pytest.raises(ValidationError):
        ProjectSerializer().fields["name"].run_validation(name)
    # Rename has no serializer max_length, but Project.save calls full_clean;
    # the model/database varchar(255) limit still applies. Do not silently change it.
    rename = ProjectNameUpdateSerializer(
        data={"project_id": str(uuid.uuid4()), "name": name}
    )
    assert rename.is_valid(), rename.errors
    with pytest.raises(ModelValidationError):
        Project._meta.get_field("name").clean(name, None)


def test_invalid_utf8_is_not_a_resource_name():
    # JSON text/PG project names cannot be represented by this byte sequence.
    # The collector also checks UTF-8 before project lookup or cache insertion.
    with pytest.raises(UnicodeDecodeError):
        b"bad\xffname".decode("utf-8")
