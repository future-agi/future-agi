"""Browser request shapes for hosted HTTP evaluators."""
from rest_framework import serializers


class RemoteAuthSerializer(serializers.Serializer):
    kind = serializers.ChoiceField(choices=("none", "hmac", "bearer", "oauth", "header"), default="hmac")
    token = serializers.CharField(required=False, min_length=16, max_length=8192, write_only=True)
    header_name = serializers.RegexField(r"^[A-Za-z0-9-]+$", required=False, max_length=100)
    oauth = serializers.DictField(required=False)


class RemoteOutputSerializer(serializers.Serializer):
    type = serializers.ChoiceField(choices=("score", "pass_fail", "choice"), default="score")
    score_field = serializers.CharField(default="score", max_length=200)
    reason_field = serializers.CharField(default="reason", allow_blank=True, max_length=200)
    choices = serializers.ListField(child=serializers.DictField(), required=False, max_length=100)


class RemoteDefinitionSerializer(serializers.Serializer):
    url = serializers.CharField(max_length=2048)
    auth = RemoteAuthSerializer(required=False, default=dict)
    mapping = serializers.DictField(child=serializers.CharField(max_length=200), default=dict)
    output = RemoteOutputSerializer(required=False, default=dict)
    timeout_ms = serializers.IntegerField(min_value=5000, max_value=30000, default=15000)
    retries = serializers.IntegerField(min_value=0, max_value=3, default=0)
    retry_on = serializers.ListField(child=serializers.ChoiceField(choices=("429", "5xx", "timeout")), default=list)
    calls_at_a_time = serializers.IntegerField(min_value=1, max_value=8, default=1)
    send = serializers.ChoiceField(choices=("row", "trace"), default="row")


class RemoteSaveSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=255, required=False)
    description = serializers.CharField(max_length=1000, allow_blank=True, required=False)
    remote = RemoteDefinitionSerializer(required=False)
    pass_threshold = serializers.FloatField(min_value=0, max_value=1, required=False)
    rotate_secret = serializers.BooleanField(required=False, default=False)


class RemoteTestSerializer(RemoteSaveSerializer):
    template_id = serializers.UUIDField(required=False)
    dataset_id = serializers.UUIDField(required=False)
    row_id = serializers.UUIDField(required=False)
    inputs = serializers.DictField(required=False, default=dict)
    mapping = serializers.DictField(child=serializers.CharField(max_length=200), required=False)
    output = serializers.JSONField(required=False, default="")


class RemoteScoreRunSerializer(serializers.Serializer):
    run_id = serializers.UUIDField()
    row_ids = serializers.ListField(child=serializers.UUIDField(), required=False, allow_empty=False, max_length=1000)
