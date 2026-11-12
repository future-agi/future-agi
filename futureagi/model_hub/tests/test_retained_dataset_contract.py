"""The old client type survives generation without replacing the settings body."""

from drf_yasg import openapi
from drf_yasg.inspectors import SwaggerAutoSchema
from rest_framework.views import APIView

from model_hub.serializers.contracts import DatasetBehaviorRequestSerializer
from model_hub.serializers.develop_dataset_contracts import (
    DatasetSettingsRequestSerializer,
)
from tfc.utils.api_contracts import ManagementAPIAutoSchema


def inspector(schema_class=ManagementAPIAutoSchema):
    return schema_class(
        view=APIView(),
        path="/settings/",
        method="PUT",
        components=openapi.ReferenceResolver(
            openapi.SCHEMA_DEFINITIONS, force_init=True
        ),
        request=None,
        overrides={},
    )


def test_retained_schema_matches_legacy_serializer_without_changing_active_body():
    legacy = inspector(SwaggerAutoSchema)
    legacy_ref = legacy.serializer_to_schema(DatasetBehaviorRequestSerializer())
    expected = dict(openapi.resolve_ref(legacy_ref, legacy.components))

    current = inspector()
    body = current.serializer_to_schema(DatasetSettingsRequestSerializer())
    assert body["$ref"] == "#/definitions/DatasetSettingsRequest"
    definitions = current.components.with_scope(openapi.SCHEMA_DEFINITIONS)
    retained = dict(definitions.get("DatasetBehaviorRequest"))
    assert retained.pop("x-client-retained") is True
    assert retained == expected
    active = openapi.resolve_ref(body, current.components)
    assert {"description", "tags"} <= set(active["properties"])
    assert not {"description", "tags"} & set(retained["properties"])
    assert "x-client-retained" not in active


def test_retention_is_idempotent_and_does_not_mark_other_serializers():
    current = inspector()
    plain = current.serializer_to_schema(DatasetBehaviorRequestSerializer())
    assert "x-client-retained" not in openapi.resolve_ref(plain, current.components)
    first = current.serializer_to_schema(DatasetSettingsRequestSerializer())
    second = current.serializer_to_schema(DatasetSettingsRequestSerializer())
    assert first == second
    assert set(current.components.with_scope(openapi.SCHEMA_DEFINITIONS).keys()) == {
        "DatasetBehaviorRequest",
        "DatasetSettingsRequest",
    }
