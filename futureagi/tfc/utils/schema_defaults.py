"""Document encoded input defaults without changing serializer runtime values."""

from drf_yasg import openapi
from drf_yasg.inspectors import FieldInspector
from rest_framework import serializers


class EncodedInputDefaultInspector(FieldInspector):
    """Use an explicitly declared wire equivalent for an empty parsed default.

    Custom query fields parse strings into lists/dicts. drf-yasg otherwise emits
    their internal default beside a string type, including for read-query POST
    aliases. Do not change object/array schemas, invent missing defaults, or
    replace non-empty defaults. Each opt-in field must accept the wire value
    and normalize it to the same value as omission.
    """

    def process_result(self, result, method_name, obj, **kwargs):
        wire_default = getattr(obj, "swagger_empty_input_default", serializers.empty)
        if (
            isinstance(result, (openapi.Schema, openapi.Parameter))
            and result.get("type") == openapi.TYPE_STRING
            and result.get("default", serializers.empty) in ([], {})
            and wire_default is not serializers.empty
        ):
            result["default"] = wire_default
        return result
