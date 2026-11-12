from rest_framework import serializers

from simulate.serializers.response.run_test import RunTestResponseSerializer


class PromptSimulationTemplateSummarySerializer(serializers.Serializer):
    id = serializers.UUIDField(read_only=True)
    name = serializers.CharField(read_only=True)


class PromptSimulationListResultSerializer(serializers.Serializer):
    count = serializers.IntegerField(read_only=True)
    page = serializers.IntegerField(read_only=True)
    limit = serializers.IntegerField(read_only=True)
    results = RunTestResponseSerializer(many=True, read_only=True)
    prompt_template = PromptSimulationTemplateSummarySerializer(read_only=True)


class PromptSimulationListResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = PromptSimulationListResultSerializer()


class PromptSimulationRunResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = RunTestResponseSerializer()


class PromptSimulationUpdateRequestSerializer(serializers.Serializer):
    prompt_version_id = serializers.CharField(max_length=255, required=False)
    scenario_ids = serializers.ListField(
        child=serializers.UUIDField(), allow_empty=True, required=False
    )
    name = serializers.CharField(max_length=255, required=False)
    description = serializers.CharField(allow_blank=True, required=False)
    enable_tool_evaluation = serializers.BooleanField(required=False)


class ExecutePromptSimulationRequestSerializer(serializers.Serializer):
    """Body of the prompt-simulation execute endpoint.

    Without ``select_all``, ``scenario_ids`` is the selection to run and may name
    only live scenarios of the caller's organization (and workspace, through the
    ``Scenarios.objects`` manager) that the run test includes (T6 #7e); any other
    id is refused with one fixed error, see ``simulate.utils.scenario_ownership``.
    With ``select_all`` the ids only remove scenarios from the run test's own set,
    so they select nothing and are not checked. The check needs ``request``,
    ``prompt_template_id`` and ``run_test_id`` in the serializer context.
    """

    scenario_ids = serializers.ListField(
        child=serializers.UUIDField(), allow_empty=True, required=False, default=list
    )
    select_all = serializers.BooleanField(required=False, default=False)

    def validate(self, attrs):
        if attrs.get("select_all") or not attrs.get("scenario_ids"):
            return attrs

        from accounts.utils import get_request_organization
        from simulate.models import RunTest
        from simulate.utils.scenario_ownership import (
            all_attached_to_run_test,
            scenarios_not_in_run_test_errors,
        )

        request = self.context.get("request")
        run_test_id = self.context.get("run_test_id")
        # The organization the view resolves for this request.
        organization = (
            get_request_organization(request) if request is not None else None
        )
        if organization is None or run_test_id is None:
            raise serializers.ValidationError(scenarios_not_in_run_test_errors())

        run_test_visible = (
            RunTest.objects.filter(
                id=run_test_id,
                prompt_template_id=self.context.get("prompt_template_id"),
                source_type="prompt",
                organization=organization,
                deleted=False,
            )
            .values_list("id", flat=True)
            .first()
        )
        if run_test_visible is None:
            # The view's own lookup refuses this run test before anything runs,
            # as it does when no scenario_ids are sent. It also re-checks the
            # selection itself.
            return attrs

        if not all_attached_to_run_test(
            attrs["scenario_ids"], organization=organization, run_test_id=run_test_id
        ):
            raise serializers.ValidationError(scenarios_not_in_run_test_errors())
        return attrs


class ExecutePromptSimulationResultSerializer(serializers.Serializer):
    message = serializers.CharField(read_only=True)
    execution_id = serializers.UUIDField(read_only=True)
    run_test_id = serializers.UUIDField(read_only=True)
    status = serializers.CharField(read_only=True)
    total_scenarios = serializers.IntegerField(read_only=True)
    total_calls = serializers.IntegerField(read_only=True)
    scenario_ids = serializers.ListField(child=serializers.UUIDField())


class ExecutePromptSimulationResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = ExecutePromptSimulationResultSerializer()


class PromptSimulationScenarioItemSerializer(serializers.Serializer):
    id = serializers.UUIDField(read_only=True)
    name = serializers.CharField(read_only=True)
    description = serializers.CharField(read_only=True, allow_blank=True)
    scenario_type = serializers.CharField(read_only=True)
    dataset_id = serializers.UUIDField(read_only=True, allow_null=True)
    created_at = serializers.DateTimeField(read_only=True)


class PromptSimulationScenariosResultSerializer(serializers.Serializer):
    count = serializers.IntegerField(read_only=True)
    page = serializers.IntegerField(read_only=True)
    limit = serializers.IntegerField(read_only=True)
    results = PromptSimulationScenarioItemSerializer(many=True, read_only=True)


class PromptSimulationScenariosResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = PromptSimulationScenariosResultSerializer()
