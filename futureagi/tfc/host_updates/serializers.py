"""Strict, typed DRF serializers and Swagger 2 documentation for the S01 wire.

The shape source is packaged with the app and pinned to both frozen schemas.
DRF's usual coercion/unknown-field behavior is intentionally not used for this
untrusted public-file boundary. Validation reconstructs accepted scalars.
The response's installation.production remains a required host-reported boolean,
independent of the app's optional expected-identity comparison and ENV_TYPE.
"""

from rest_framework import serializers

from tfc.host_updates.status import RESPONSE_SCHEMA, StatusUnavailable, _shape


def _swagger_shape(schema):
    """Document unions without leaking branch-local constraints.

    Swagger 2 uses vendor extensions for nullability and exact alternatives.
    Grouped legacy/restore objects already carry a shared outer object shape;
    keep that envelope and preserve the alternatives in x-anyOf. In particular,
    the legacy branch's maxProperties must never constrain the complete object.
    """
    if "anyOf" in schema:
        variants = schema["anyOf"]
        nonnull = [variant for variant in variants if variant.get("type") != "null"]
        nullable = len(nonnull) != len(variants)
        if nullable and len(nonnull) == 1:
            return {**_swagger_shape(nonnull[0]), "x-nullable": True}
        result = _swagger_shape(
            {key: value for key, value in schema.items() if key != "anyOf"}
        )
        result["x-anyOf"] = [_swagger_shape(variant) for variant in nonnull]
        if nullable:
            result["x-nullable"] = True
        return result
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
        variants = schema["anyOf"]
        nonnull = [variant for variant in variants if variant.get("type") != "null"]
        if len(nonnull) != len(variants):
            kwargs["allow_null"] = True
            if len(nonnull) == 1:
                schema = nonnull[0]
    if "oneOf" in schema or ("anyOf" in schema and "type" not in schema):
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


# The shared envelope receives only the view's constant strings, so it never
# emits structured result/details/attr here. Keep all seven fields required and
# reject unknown keys and DRF scalar coercions just as for the success contract.
HostUpdateStatusErrorSerializer = _serializer(
    {
        "type": "object",
        "additionalProperties": False,
        "required": ["status", "type", "code", "detail", "message", "error", "result"],
        "properties": {
            "status": {"type": "boolean", "const": False},
            "type": {
                "type": "string",
                "enum": [
                    "validation_error",
                    "authentication_error",
                    "permission_error",
                    "not_found",
                    "api_error",
                    "service_unavailable",
                ],
            },
            "code": {
                "type": "string",
                "enum": [
                    "invalid",
                    "not_authenticated",
                    "permission_denied",
                    "2fa_required",
                    "saml_scope_conflict",
                    "not_found",
                    "method_not_allowed",
                    "service_unavailable",
                ],
            },
            **{
                key: {"type": "string", "minLength": 1, "maxLength": 128}
                for key in ("detail", "message", "error", "result")
            },
        },
    },
    "HostUpdateStatusError",
)


class HostUpdateStatusOptionsSerializer(serializers.Serializer):
    api_schema_version = serializers.IntegerField()
    methods = serializers.ListField(
        child=serializers.ChoiceField(choices=("GET", "HEAD", "OPTIONS"))
    )

    class Meta:
        swagger_schema_fields = {"additionalProperties": False}
