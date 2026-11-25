from rest_framework import serializers

from simulate.services.harness_scenario_tags import (
    MAX_SCENARIOS_PER_CHANGE,
    MAX_TAG_LENGTH,
    MAX_TAGS_PER_SCENARIO,
)
from tfc.utils.api_serializers import StrictInputSerializer


def _tag_list(**kwargs) -> serializers.ListField:
    return serializers.ListField(
        child=serializers.CharField(max_length=MAX_TAG_LENGTH, allow_blank=True),
        max_length=MAX_TAGS_PER_SCENARIO,
        **kwargs,
    )


class HarnessScenarioTagsSerializer(StrictInputSerializer):
    tags = _tag_list(help_text="The scenario's tags; an empty list clears them.")


class HarnessScenarioTagsChangeSerializer(StrictInputSerializer):
    scenario_keys = serializers.ListField(
        child=serializers.CharField(max_length=255),
        min_length=1,
        max_length=MAX_SCENARIOS_PER_CHANGE,
    )
    add = _tag_list(required=False, default=list)
    remove = _tag_list(required=False, default=list)


class HarnessScenarioTagsResultSerializer(serializers.Serializer):
    scenario_key = serializers.CharField()
    tags = serializers.ListField(child=serializers.CharField())


class HarnessScenarioTagsChangeResultSerializer(serializers.Serializer):
    scenarios = HarnessScenarioTagsResultSerializer(many=True)
    tags = serializers.ListField(
        child=serializers.CharField(),
        help_text="Every tag the environment's scenarios carry after the change.",
    )


class HarnessEnvironmentTagsSerializer(serializers.Serializer):
    tags = serializers.ListField(
        child=serializers.CharField(),
        help_text="Every tag the environment's scenarios carry.",
    )
