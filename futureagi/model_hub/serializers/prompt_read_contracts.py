"""Response contracts for prompt, label and model-catalogue reads.

Declaration-only serializers: the views keep producing the same bodies and
these describe them for swagger.json. Captured handler output in
``model_hub/tests/fixtures/contracts/th8216/captured/`` is validated against
the generated schema by ``test_th8216_prompt_contract_parity.py``.
"""

from drf_yasg import openapi
from rest_framework import serializers

from model_hub.serializers.prompt_label import PromptLabelSerializer
from model_hub.serializers.prompt_template import (
    PromptHistoryExecutionSerializer,
    PromptTemplateSerializer,
    PromptVersionLabelSerializer,
)
from tfc.utils.serializer_fields import JsonValueField


class ExtendedPageResponseSerializer(serializers.Serializer):
    """Body of ``ExtendedPageNumberPagination.get_paginated_response``."""

    count = serializers.IntegerField()
    next = serializers.URLField(allow_null=True)
    previous = serializers.URLField(allow_null=True)
    total_pages = serializers.IntegerField()
    current_page = serializers.IntegerField()


class PromptVersionHistoryPageSerializer(ExtendedPageResponseSerializer):
    results = PromptHistoryExecutionSerializer(many=True)


class PromptTemplatePageSerializer(ExtendedPageResponseSerializer):
    results = PromptTemplateSerializer(many=True)


class PromptLabelPageSerializer(ExtendedPageResponseSerializer):
    results = PromptLabelSerializer(many=True)


class PromptTemplateSelectedVersionSerializer(PromptTemplateSerializer):
    """Template fields merged with one version's snapshot.

    ``variable_names`` here is the version's stored value, not the template's.
    """

    prompt_config = JsonValueField(
        help_text="List of prompt configs (a dict snapshot is wrapped in a list)."
    )
    version = serializers.CharField()
    variable_names = JsonValueField(allow_null=True)
    output = JsonValueField(allow_null=True)
    is_draft = serializers.BooleanField()
    metadata = JsonValueField(allow_null=True)

    class Meta(PromptTemplateSerializer.Meta):
        ref_name = "PromptTemplateSelectedVersion"
        fields = [
            *PromptTemplateSerializer.Meta.fields,
            "prompt_config",
            "version",
            "output",
            "is_draft",
            "metadata",
        ]
        read_only_fields = fields


class PromptTemplateDetailResponseSerializer(PromptTemplateSelectedVersionSerializer):
    """GET /prompt-templates/{id}/: draft, else default, else latest version."""

    last_saved = serializers.DateTimeField()
    error_message = serializers.CharField(allow_null=True)
    last_chunk_pos = JsonValueField(
        required=False,
        help_text="Present only while a cached streaming run exists.",
    )

    class Meta(PromptTemplateSelectedVersionSerializer.Meta):
        ref_name = "PromptTemplateDetailResponse"
        fields = [
            *PromptTemplateSelectedVersionSerializer.Meta.fields,
            "last_saved",
            "error_message",
            "last_chunk_pos",
        ]
        read_only_fields = fields


class PromptLabelledVersionSerializer(PromptTemplateSelectedVersionSerializer):
    labels = PromptVersionLabelSerializer(many=True)

    class Meta(PromptTemplateSelectedVersionSerializer.Meta):
        ref_name = "PromptLabelledVersion"
        fields = [*PromptTemplateSelectedVersionSerializer.Meta.fields, "labels"]
        read_only_fields = fields


class PromptLabelLookupResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = PromptLabelledVersionSerializer()


class PromptTemplateVersionLabelsSerializer(serializers.Serializer):
    version = serializers.CharField()
    labels = serializers.ListField(child=serializers.CharField())
    is_default = serializers.BooleanField()
    is_draft = serializers.BooleanField()


class PromptTemplateLabelsResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = PromptTemplateVersionLabelsSerializer(many=True)


class ModelCatalogEntrySerializer(serializers.Serializer):
    """One row of GET /api/models_list/; carries no credential values."""

    model_name = serializers.CharField()
    providers = serializers.CharField(allow_blank=True)
    is_available = serializers.BooleanField(
        help_text="Whether the organisation has a configured key for the provider."
    )
    logo_url = serializers.CharField(allow_null=True)
    best_for = JsonValueField(allow_null=True)
    use_case = JsonValueField(allow_null=True)
    cutoff = JsonValueField(allow_null=True)
    rate_limits = JsonValueField(allow_null=True)
    latency = JsonValueField(allow_null=True)
    pricing = JsonValueField(allow_null=True)
    type = serializers.CharField(help_text="Model mode, 'text' when unknown.")


class ModelCatalogPageSerializer(ExtendedPageResponseSerializer):
    results = ModelCatalogEntrySerializer(many=True)


def _query(name, description, *, required=False, type_=openapi.TYPE_STRING):
    return openapi.Parameter(
        name, openapi.IN_QUERY, description=description, required=required, type=type_
    )


MODELS_LIST_QUERY_PARAMETERS = [
    _query("name", "Return only the model with this exact (case-insensitive) name."),
    _query("search", "Case-insensitive substring filter on model_name."),
    _query("model_type", "One of llm, stt, tts, image; other values do not filter."),
    openapi.Parameter(
        "exclude_providers",
        openapi.IN_QUERY,
        description="Providers to leave out; repeat the parameter for several.",
        type=openapi.TYPE_ARRAY,
        items=openapi.Items(type=openapi.TYPE_STRING),
        collection_format="multi",
    ),
    _query("page", "Page number.", type_=openapi.TYPE_INTEGER),
    _query("limit", "Page size (default 10).", type_=openapi.TYPE_INTEGER),
]

MODEL_PARAMETERS_QUERY_PARAMETERS = [
    _query("model", "Model name.", required=True),
    _query("provider", "Provider name.", required=True),
    _query("model_type", "One of llm, stt, tts, image.", required=True),
]
