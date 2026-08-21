from rest_framework import serializers


class CekuraRunWebhookRequestSerializer(serializers.Serializer):
    """Body of Cekura's run-completed webhook.

    Not a ``StrictInputSerializer``: Cekura owns this payload and a field
    added on their side must not start answering 400 here. Only the fields
    this integration reads are declared.

    ``metrics`` items stay untyped dicts for the same reason, the way the
    gateway's shadow-result webhook does it — the per-metric shape is checked
    while transforming, where an unusable metric is skipped instead of
    failing the whole delivery.
    """

    run_id = serializers.CharField(max_length=255)
    status = serializers.CharField(required=False, allow_blank=True)
    name = serializers.CharField(required=False, allow_blank=True)
    test_name = serializers.CharField(required=False, allow_blank=True)
    # Timestamps stay strings here and are parsed in the transformer: a run's
    # scores are worth keeping even when its clock fields are malformed.
    started_at = serializers.CharField(required=False, allow_blank=True)
    completed_at = serializers.CharField(required=False, allow_blank=True)
    metrics = serializers.ListField(child=serializers.DictField(), required=False)


class CekuraIngestResultSerializer(serializers.Serializer):
    ingested = serializers.IntegerField()
    skipped = serializers.IntegerField()


class CekuraIngestResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = CekuraIngestResultSerializer()
