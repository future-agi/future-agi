from rest_framework import serializers


class ManagedCatalogModelSerializer(serializers.Serializer):
    id = serializers.CharField()
    name = serializers.CharField(required=False, allow_blank=True)
    description = serializers.CharField(required=False, allow_blank=True)
    created = serializers.IntegerField(required=False)
    provider = serializers.CharField()
    vendor = serializers.CharField(allow_blank=True)
    context_length = serializers.IntegerField(required=False)
    max_output_tokens = serializers.IntegerField(required=False)
    input_modalities = serializers.ListField(
        child=serializers.CharField(), required=False
    )
    output_modalities = serializers.ListField(
        child=serializers.CharField(), required=False
    )
    supported_parameters = serializers.ListField(
        child=serializers.CharField(), required=False
    )
    input_per_mtok = serializers.FloatField(required=False)
    output_per_mtok = serializers.FloatField(required=False)
    cache_read_per_mtok = serializers.FloatField(required=False)
    billed_input_per_mtok = serializers.FloatField(required=False)
    billed_output_per_mtok = serializers.FloatField(required=False)
    dynamic_price = serializers.BooleanField(required=False)


class ManagedCatalogProviderSerializer(serializers.Serializer):
    provider = serializers.CharField()
    source = serializers.CharField()
    fetched_at = serializers.CharField()
    models = serializers.IntegerField()


class ManagedCatalogResultSerializer(serializers.Serializer):
    markup_percent = serializers.FloatField()
    providers = ManagedCatalogProviderSerializer(many=True)
    models = ManagedCatalogModelSerializer(many=True)


class ManagedCatalogResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = ManagedCatalogResultSerializer()


class ManagedModelsStatusResultSerializer(serializers.Serializer):
    available = serializers.BooleanField(
        help_text="The gateway offers managed models (it has managed providers)."
    )
    enabled = serializers.BooleanField(
        help_text="This org has opted in (org config managed_models.enabled)."
    )
    balance_usd = serializers.FloatField(allow_null=True)
    markup_percent = serializers.FloatField(allow_null=True)


class ManagedModelsStatusResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = ManagedModelsStatusResultSerializer()
