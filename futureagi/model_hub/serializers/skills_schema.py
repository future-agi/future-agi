"""Swagger helpers for Skills result contracts."""

from copy import copy

from drf_yasg import openapi
from drf_yasg.inspectors import NotHandled, ReferencingSerializerInspector
from rest_framework import serializers


class NullableResultRefInspector(ReferencingSerializerInspector):
    """Keep nullability on a result property, not on its shared definition."""

    def field_to_swagger_object(
        self, field, swagger_object_type, use_references, **kwargs
    ):
        if not (
            use_references
            and swagger_object_type is openapi.Schema
            and isinstance(field, serializers.Serializer)
            and field.allow_null
            and getattr(field.parent, "field_name", None) == "result"
            and self.get_serializer_ref_name(field)
        ):
            return NotHandled
        # drf-yasg builds named definitions from their first field instance. Build
        # the object with its non-null type, then annotate this use of it. A pure
        # Swagger 2 Reference Object cannot carry sibling schema constraints.
        non_null = copy(field)
        non_null.allow_null = False
        ref = super().field_to_swagger_object(
            non_null, swagger_object_type, use_references, **kwargs
        )
        return {"allOf": [ref], "x-nullable": True}
