"""Wire contracts for the workspace tag registry and an experiment's tags."""

from rest_framework import serializers

from model_hub.models.workspace_tags import (
    TAG_COLORS,
    TAG_DESCRIPTION_MAX,
    TAG_NAME_MAX,
    normalise_tag_name,
)
from tfc.utils.api_serializers import StrictInputSerializer

EXPERIMENT_TAGS_MAX = 50


class TagNameField(serializers.CharField):
    default_error_messages = {
        "blank": "Give the tag a name.",
        "null": "Give the tag a name.",
        "required": "Give the tag a name.",
        "invalid": "Give the tag a name as text.",
        "max_length": f"Use {TAG_NAME_MAX} characters or fewer for the tag name.",
    }

    def __init__(self, **kwargs):
        super().__init__(max_length=TAG_NAME_MAX, trim_whitespace=True, **kwargs)

    def to_internal_value(self, data):
        if not isinstance(data, str):
            self.fail("invalid")
        return normalise_tag_name(super().to_internal_value(data))


class TagDescriptionField(serializers.CharField):
    default_error_messages = {
        "null": "Enter a description as text, or leave it empty.",
        "invalid": "Enter a description as text, or leave it empty.",
        "max_length": (
            f"Use {TAG_DESCRIPTION_MAX} characters or fewer for the description."
        ),
    }

    def __init__(self, **kwargs):
        super().__init__(
            max_length=TAG_DESCRIPTION_MAX,
            allow_blank=True,
            trim_whitespace=True,
            **kwargs,
        )

    def to_internal_value(self, data):
        if not isinstance(data, str):
            self.fail("invalid")
        return super().to_internal_value(data)


class TagColorField(serializers.ChoiceField):
    default_error_messages = {
        "invalid_choice": "Pick one of the eight tag colours.",
        "null": "Pick one of the eight tag colours.",
    }

    def __init__(self, **kwargs):
        super().__init__(choices=TAG_COLORS, **kwargs)


class TagQuerySerializer(StrictInputSerializer):
    include = serializers.ChoiceField(
        choices=["experiments"],
        required=False,
        help_text="experiments: also return each experiment's tag ids.",
    )


class TagCreateSerializer(StrictInputSerializer):
    name = TagNameField()
    color = TagColorField(required=False)
    description = TagDescriptionField(required=False)


class TagUpdateSerializer(StrictInputSerializer):
    name = TagNameField(required=False)
    color = TagColorField(required=False)
    description = TagDescriptionField(required=False)

    def validate(self, attrs):
        if not attrs:
            raise serializers.ValidationError(
                "Change the name, the colour or the description."
            )
        return attrs


class ExperimentTagsSerializer(StrictInputSerializer):
    tag_ids = serializers.ListField(
        child=serializers.UUIDField(
            error_messages={"invalid": "Each tag id must be a tag's id."}
        ),
        allow_empty=True,
        max_length=EXPERIMENT_TAGS_MAX,
        error_messages={
            "required": "Send the experiment's tag ids as tag_ids.",
            "null": "Send the experiment's tag ids as tag_ids.",
            "not_a_list": "Send the experiment's tag ids as a list.",
            "max_length": f"Put {EXPERIMENT_TAGS_MAX} tags or fewer on an experiment.",
        },
    )


class TagUsedOnSerializer(serializers.Serializer):
    experiments = serializers.IntegerField()
    datasets = serializers.IntegerField()
    rows = serializers.IntegerField()


class TagSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()
    color = serializers.ChoiceField(choices=TAG_COLORS)
    description = serializers.CharField(allow_blank=True)
    used_on = TagUsedOnSerializer()
    created_by = serializers.CharField(allow_null=True)
    created_at = serializers.DateTimeField()


class TagListResultSerializer(serializers.Serializer):
    results = TagSerializer(many=True)
    experiments = serializers.DictField(
        child=serializers.ListField(child=serializers.UUIDField()),
        required=False,
        help_text="With include=experiments: experiment id to its tag ids.",
    )


class TagListResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = TagListResultSerializer()


class TagResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = TagSerializer()


class TagDeletedResultSerializer(serializers.Serializer):
    id = serializers.UUIDField()


class TagDeletedResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = TagDeletedResultSerializer()


class ExperimentTagsResultSerializer(serializers.Serializer):
    experiment_id = serializers.UUIDField()
    tag_ids = serializers.ListField(child=serializers.UUIDField())


class ExperimentTagsResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = ExperimentTagsResultSerializer()
