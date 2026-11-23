from rest_framework import serializers

from tfc.utils.api_serializers import StrictInputSerializer


class HarnessSourceSelectionSerializer(StrictInputSerializer):
    kind = serializers.ChoiceField(choices=("knowledge_base",))
    kb_id = serializers.UUIDField()
    file_ids = serializers.ListField(
        child=serializers.UUIDField(), required=False, allow_empty=False, max_length=100
    )
    whole_collection = serializers.BooleanField(required=False, default=False)

    def validate(self, attrs):
        if attrs.get("whole_collection") == ("file_ids" in attrs):
            raise serializers.ValidationError(
                "Choose either whole_collection or explicit file_ids."
            )
        if len(set(attrs.get("file_ids", []))) != len(attrs.get("file_ids", [])):
            raise serializers.ValidationError("file_ids must be unique.")
        return attrs


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
