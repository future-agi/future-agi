"""Public contract consumed by dataset.rowExtras.activity."""

from rest_framework import serializers

from tracer.serializers.filters import StrictInputSerializer


class RowActivityQuerySerializer(StrictInputSerializer):
    page_size = serializers.IntegerField(required=False, default=50, min_value=1, max_value=100)
    cursor = serializers.CharField(required=False, max_length=2048, trim_whitespace=False)


class RowActivitySourceSerializer(serializers.Serializer):
    type = serializers.ChoiceField(choices=["upload"])


class RowActivityEventSerializer(serializers.Serializer):
    # A string avoids JavaScript precision loss for the database sequence.
    id = serializers.CharField()
    type = serializers.ChoiceField(choices=["created", "edit", "deleted", "restored"])
    subject = serializers.ChoiceField(choices=["row", "cell"])
    action = serializers.ChoiceField(choices=["create", "edit", "delete", "restore"])
    at = serializers.DateTimeField()
    actor_id = serializers.CharField(allow_null=True)
    actor = serializers.CharField()
    origin = serializers.ChoiceField(choices=["write", "import"])
    cell_id = serializers.UUIDField(allow_null=True)
    column_id = serializers.UUIDField(allow_null=True)
    column = serializers.CharField(allow_null=True, allow_blank=True)
    before = serializers.CharField(allow_null=True, allow_blank=True, trim_whitespace=False)
    after = serializers.CharField(allow_null=True, allow_blank=True, trim_whitespace=False)
    before_truncated = serializers.BooleanField()
    after_truncated = serializers.BooleanField()
    text = serializers.CharField()
    source = RowActivitySourceSerializer(allow_null=True)


class RowActivityResultSerializer(serializers.Serializer):
    events = RowActivityEventSerializer(many=True)
    next_cursor = serializers.CharField(allow_null=True)
    has_more = serializers.BooleanField()


class RowActivityResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = RowActivityResultSerializer()
