"""The run report of a composition's run (`api_contracts/harness/environment-releases.md` §7.7)."""

from __future__ import annotations

from rest_framework import serializers


class HarnessRunReportQuerySerializer(serializers.Serializer):
    baseline = serializers.UUIDField(required=False)


class HarnessRunReportManifestSerializer(serializers.Serializer):
    """The run's frozen manifest (§2.5), without its schema version."""

    digest = serializers.CharField()
    environment = serializers.ListField(child=serializers.DictField())
    suite = serializers.DictField()
    binding = serializers.DictField()
    agent = serializers.DictField()
    selection = serializers.DictField()
    simulator = serializers.DictField()
    scorer = serializers.DictField()
    runtime = serializers.DictField()


class HarnessRunReportComparabilitySerializer(serializers.Serializer):
    baseline_run_id = serializers.CharField()
    comparable = serializers.BooleanField()
    differing = serializers.ListField(child=serializers.CharField())


class HarnessRunReportCoverageSerializer(serializers.Serializer):
    runnable = serializers.IntegerField()
    suite_scenarios = serializers.IntegerField()
    base_scenarios = serializers.IntegerField()
    unscorable = serializers.ListField(child=serializers.CharField())
    extension_tools = serializers.ListField(child=serializers.CharField())


class HarnessRunReportUseCaseSerializer(serializers.Serializer):
    use_case = serializers.CharField(allow_blank=True)
    scenarios = serializers.IntegerField()
    trials = serializers.IntegerField()
    pass_1 = serializers.FloatField(allow_null=True)
    pass_k = serializers.FloatField(allow_null=True)


class HarnessRunReportScenarioSerializer(serializers.Serializer):
    scenario_key = serializers.CharField()
    name = serializers.CharField()
    use_case = serializers.CharField(allow_blank=True)
    origin = serializers.ChoiceField(choices=("base", "specialized", "custom"))
    # One receipt status per trial index; null until that trial's receipt arrives.
    trials = serializers.ListField(
        child=serializers.ChoiceField(
            choices=("passed", "failed", "errored", "skipped"), allow_null=True
        )
    )
    pass_1 = serializers.FloatField(allow_null=True)
    passed_all = serializers.BooleanField()


class HarnessRunReportSerializer(serializers.Serializer):
    run_mode = serializers.ChoiceField(
        choices=("canonical", "tool_routed", "conversation_only")
    )
    manifest = HarnessRunReportManifestSerializer()
    comparability = HarnessRunReportComparabilitySerializer(allow_null=True)
    coverage = HarnessRunReportCoverageSerializer()
    use_cases = HarnessRunReportUseCaseSerializer(many=True)
    scenarios = HarnessRunReportScenarioSerializer(many=True)
    environment_faults = serializers.IntegerField()
