from rest_framework import serializers

from model_hub.serializers.contracts import JsonObjectRequestField
from simulate.models import HarnessSourceReview
from simulate.serializers.harness_environment import (
    HarnessSourceDraftRefSerializer,
    HarnessSourceSelectionSerializer,
)
from tfc.utils.api_serializers import StrictInputSerializer


class HarnessSourceScopeSerializer(StrictInputSerializer):
    kind = serializers.ChoiceField(choices=HarnessSourceReview.Owner.choices)
    id = serializers.UUIDField()
    revision = serializers.IntegerField(required=False, min_value=1)
    schema_digest = serializers.RegexField(r"\Asha256:[0-9a-f]{64}\Z", required=False)
    environment_version_id = serializers.UUIDField(required=False)
    expected_draft = HarnessSourceDraftRefSerializer(required=False)
    target_table = serializers.CharField(required=False, max_length=255)

    def validate(self, attrs):
        fields = set(attrs) - {"kind", "id"}
        if attrs["kind"] == HarnessSourceReview.Owner.ENVIRONMENT:
            valid = fields - {"target_table"} == {
                "environment_version_id",
                "expected_draft",
            }
        else:
            if attrs["kind"] == HarnessSourceReview.Owner.DESIGN:
                fields -= {"target_table"}
            valid = fields in (set(), {"revision", "schema_digest"})
        if not valid:
            raise serializers.ValidationError(
                "Supply the basis for the selected source owner."
            )
        return attrs


class SourceSchemaRepairSerializer(StrictInputSerializer):
    source_id = serializers.CharField(max_length=255)
    text_fields = serializers.ListField(
        child=serializers.CharField(max_length=255), required=False, max_length=256
    )
    identity_fields = serializers.ListField(
        child=serializers.CharField(max_length=255), required=False, max_length=256
    )
    separate_entity = serializers.BooleanField(required=False)
    manual_relationships = serializers.BooleanField(required=False)


class HarnessDesignSourceSelectionSerializer(StrictInputSerializer):
    document_ids = serializers.ListField(
        child=serializers.UUIDField(), required=False, max_length=100, default=list
    )
    document_purposes = serializers.DictField(
        child=serializers.ChoiceField(choices=("policy", "reference", "seed_records")),
        required=False,
        default=dict,
    )
    knowledge_base = HarnessSourceSelectionSerializer(
        many=True, required=False, default=list, max_length=100
    )

    def validate(self, attrs):
        document_ids = attrs["document_ids"]
        if len(document_ids) != len(set(document_ids)):
            raise serializers.ValidationError("Document IDs must be unique.")
        if not set(attrs["document_purposes"]) <= {
            str(identifier) for identifier in document_ids
        }:
            raise serializers.ValidationError(
                "A purpose must name a selected document."
            )
        return attrs


class HarnessSourceReviewCreateSerializer(HarnessDesignSourceSelectionSerializer):
    scope = HarnessSourceScopeSerializer()
    idempotency_key = serializers.CharField(max_length=128)
    schema_repairs = SourceSchemaRepairSerializer(
        many=True, required=False, max_length=100
    )


class HarnessSourceReviewCancelSerializer(StrictInputSerializer):
    expected_revision = serializers.IntegerField(min_value=1)
    expected_draft_digest = serializers.RegexField(
        r"\Asha256:[0-9a-f]{64}\Z", allow_null=True
    )


class HarnessSourceReviewReprepareSerializer(StrictInputSerializer):
    expected_revision = serializers.IntegerField(min_value=1)
    idempotency_key = serializers.CharField(max_length=128)
    target_table = serializers.CharField(required=False, max_length=255)
    schema_repairs = SourceSchemaRepairSerializer(
        many=True, required=False, allow_empty=False, max_length=100
    )

    def validate(self, attrs):
        if ("target_table" in attrs) == ("schema_repairs" in attrs):
            raise serializers.ValidationError(
                "Choose a destination table or provide schema repairs."
            )
        return attrs


class HarnessSourceReviewAcceptSerializer(HarnessSourceReviewCancelSerializer):
    expected_draft_digest = serializers.RegexField(r"\Asha256:[0-9a-f]{64}\Z")
    expected_schema_digest = serializers.RegexField(r"\Asha256:[0-9a-f]{64}\Z")
    decisions = JsonObjectRequestField()
    idempotency_key = serializers.CharField(max_length=128)


class HarnessSourceReviewReadSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    state = serializers.ChoiceField(choices=HarnessSourceReview.State.choices)
    revision = serializers.IntegerField()
    scope = serializers.JSONField()
    draft_digest = serializers.CharField(allow_null=True)
    preview = serializers.JSONField()
    receipt = serializers.JSONField(allow_null=True)
    expires_at = serializers.DateTimeField()
