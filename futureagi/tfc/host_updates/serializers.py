"""Strict, typed DRF serializers and Swagger 2 documentation for the S01 wire.

The shape source is packaged with the app and pinned to both frozen schemas.
DRF's usual coercion/unknown-field behavior is intentionally not used for this
untrusted public-file boundary. Validation reconstructs accepted scalars.
"""

from rest_framework import serializers

from tfc.host_updates.status import RESPONSE_SCHEMA, StatusUnavailable, _shape


def _swagger_shape(schema):
    """Swagger 2 uses x-nullable, and cannot describe a native oneOf.

    The two maintenance-window variants are documented as a discriminated
    object with conditional fields. x-oneOf retains their exact shapes for the
    repository generator; Swagger 2 tooling can use the flattened description.
    """
    if "anyOf" in schema:
        return {**_swagger_shape(schema["anyOf"][0]), "x-nullable": True}
    if "oneOf" in schema:
        variants = schema["oneOf"]
        properties = {}
        for variant in variants:
            properties.update(variant["properties"])
        properties["kind"] = {
            "type": "string",
            "enum": [v["properties"]["kind"]["const"] for v in variants],
        }
        return {
            "type": "object",
            "additionalProperties": False,
            "required": ["kind", "timezone"],
            "description": "always: kind/timezone only; interval: also requires days_of_week/start/end.",
            "x-oneOf": [_swagger_shape(variant) for variant in variants],
            "properties": {
                key: _swagger_shape(value) for key, value in properties.items()
            },
        }
    result = {key: value for key, value in schema.items() if key != "const"}
    if "const" in schema:
        result["enum"] = [schema["const"]]
    if "properties" in result:
        result["properties"] = {
            key: _swagger_shape(value) for key, value in result["properties"].items()
        }
    if "items" in result:
        result["items"] = _swagger_shape(result["items"])
    return result


class _ClosedSerializer(serializers.Serializer):
    wire_schema = None

    def to_internal_value(self, data):
        try:
            return _shape(data, self.wire_schema)
        except StatusUnavailable:
            raise serializers.ValidationError(
                {"non_field_errors": ["Invalid host status response."]}
            ) from None

    def to_representation(self, instance):
        # Not serializer.data over an arbitrary mapping: validate the exact
        # accepted keys/types before constructing the wire representation.
        return self.to_internal_value(instance)


def _field(schema, name):
    kwargs = {"required": True}
    if "anyOf" in schema:
        schema = schema["anyOf"][0]
        kwargs["allow_null"] = True
    if "oneOf" in schema:
        # The enclosing closed serializer validates exact variant semantics.
        return serializers.DictField(**kwargs)
    if schema["type"] == "object":
        return _serializer(schema, name)(**kwargs)
    if schema["type"] == "array":
        return serializers.ListField(
            child=_field(schema["items"], name + "Item"),
            min_length=schema["minItems"],
            max_length=schema["maxItems"],
            **kwargs,
        )
    if schema["type"] == "boolean":
        return serializers.BooleanField(**kwargs)
    if schema["type"] == "integer":
        return serializers.IntegerField(
            min_value=schema.get("minimum"), max_value=schema.get("maximum"), **kwargs
        )
    if "enum" in schema or "const" in schema:
        return serializers.ChoiceField(
            choices=schema.get("enum", [schema.get("const")]), **kwargs
        )
    if "pattern" in schema:
        return serializers.RegexField(
            schema["pattern"],
            min_length=schema.get("minLength", 0),
            max_length=schema.get("maxLength"),
            trim_whitespace=False,
            **kwargs,
        )
    return serializers.CharField(
        min_length=schema.get("minLength", 0),
        max_length=schema.get("maxLength"),
        trim_whitespace=False,
        **kwargs,
    )


def _serializer(schema, name):
    fields = {
        key: _field(value, name + "".join(word.title() for word in key.split("_")))
        for key, value in schema["properties"].items()
    }
    fields["wire_schema"] = schema
    fields["Meta"] = type(
        "Meta", (), {"swagger_schema_fields": _swagger_shape(schema), "ref_name": name}
    )
    return type(name + "Serializer", (_ClosedSerializer,), fields)


HostUpdateStatusResponseSerializer = _serializer(
    RESPONSE_SCHEMA, "HostUpdateStatusResponse"
)


class HostUpdateStatusErrorSerializer(serializers.Serializer):
    """Closed string-only errors, including the existing 2FA/refresh codes."""

    status = serializers.BooleanField()
    code = serializers.ChoiceField(
        choices=(
            "invalid",
            "not_authenticated",
            "permission_denied",
            "2fa_required",
            "saml_scope_conflict",
            "not_found",
            "method_not_allowed",
            "service_unavailable",
        )
    )
    detail = serializers.CharField()

    class Meta:
        swagger_schema_fields = {"additionalProperties": False}


class HostUpdateStatusOptionsSerializer(serializers.Serializer):
    api_schema_version = serializers.IntegerField()
    methods = serializers.ListField(
        child=serializers.ChoiceField(choices=("GET", "HEAD", "OPTIONS"))
    )

    class Meta:
        swagger_schema_fields = {"additionalProperties": False}
