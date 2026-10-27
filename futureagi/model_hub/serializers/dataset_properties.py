from rest_framework import serializers

from model_hub.models import AIModel, DatasetProperties
from tfc.utils.clickhouse import ClickHouseClientSingleton


class DatasetPropertiesListSerializer(serializers.ModelSerializer):
    class Meta:
        model = DatasetProperties
        fields = ("id", "name")


class DatasetPropertiesDetailsSerializer(serializers.ModelSerializer):
    class Meta:
        model = DatasetProperties
        fields = ("id", "name", "datatype", "values")


class DatasetProperty(serializers.ModelSerializer):
    class Meta:
        model = DatasetProperties
        fields = ("name", "id", "explanation", "created_at")


class Dataset(serializers.ModelSerializer):
    class Meta:
        model = DatasetProperties
        fields = (
            "environment",
            "version",
        )


class CreatePropertySerializer(serializers.ModelSerializer):
    name = serializers.CharField(max_length=255, required=True)
    explanation = serializers.CharField(max_length=255, required=True)
    model_id = serializers.UUIDField(required=True)

    class Meta:
        model = DatasetProperties
        fields = ["name", "explanation", "model_id"]

    def validate_model_id(self, value):
        try:
            AIModel.objects.get(id=value)
        except AIModel.DoesNotExist as e:
            raise serializers.ValidationError(
                "Model with this id does not exist."
            ) from e
        return value

    def create(self, validated_data):
        model_id = validated_data.pop("model_id")

        model = AIModel.objects.get(id=model_id)

        query = f"""
            SELECT DISTINCT Environment,ModelVersion
            FROM events
            WHERE OrgID = '{model.organization.id}'
            AND AIModel = '{model_id}'
            AND deleted=0
        """

        clickhouse_client = ClickHouseClientSingleton()
        raw_environment_combinations = clickhouse_client.execute_read(query)

        for comb in raw_environment_combinations:
            dataset_property = DatasetProperties.objects.create(
                model=model,
                environment=AIModel.EnvTypes.get_env_types(int(comb[0])).value,
                version=comb[1],
                datatype="string",
                values=[],
                organization=model.organization,
                **validated_data,
            )

        return dataset_property
