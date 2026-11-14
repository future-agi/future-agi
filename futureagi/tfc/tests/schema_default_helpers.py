"""Assertions shared by request-default contract tests across apps."""

from drf_yasg import openapi
from drf_yasg.inspectors import FieldInspector
from rest_framework import serializers

from tfc.utils.api_contracts import ManagementAPIAutoSchema


def field_schema(field, *, query=True):
    inspector = FieldInspector(
        view=None,
        path="/",
        method="GET" if query else "POST",
        components=openapi.ReferenceResolver(
            openapi.SCHEMA_DEFINITIONS, force_init=True
        ),
        request=None,
        field_inspectors=ManagementAPIAutoSchema.field_inspectors,
    )
    kwargs = (
        {"name": field.field_name or "value", "in_": openapi.IN_QUERY} if query else {}
    )
    return inspector.probe_field_inspectors(
        field,
        openapi.Parameter if query else openapi.Schema,
        use_references=False,
        **kwargs,
    )


def assert_encoded_default(serializer_class, field_name, wire_default, *, query=True):
    field = serializer_class().fields[field_name]
    schema = field_schema(field, query=query)
    assert schema["type"] == "string"
    assert schema["default"] == wire_default
    assert field.run_validation(wire_default) == field.run_validation(serializers.empty)


def assert_blank_choice_default(serializer_class, field_name, *, query=True):
    field = serializer_class().fields[field_name]
    schema = field_schema(field, query=query)
    assert schema["type"] == "string"
    assert schema["default"] == ""
    assert "" in schema["enum"]
    assert field.run_validation("") == field.run_validation(serializers.empty) == ""
    for choice in schema["enum"]:
        assert field.run_validation(choice) == choice
