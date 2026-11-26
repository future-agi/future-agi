"""Request and response shapes for asking questions about a run's calls."""

from rest_framework import serializers

from simulate.services.ask_engine import AnswerType
from simulate.services.ask_selection import MAX_CALLS, MAX_QUESTIONS
from simulate.services.call_evidence import Kind

# The run page offers the whole run or the calls a person ticked; a scenario group
# and a saved filter are the selection service's too, not offered here yet.
SELECTION_KINDS = ("runs", "calls")


class AskSelectionSerializer(serializers.Serializer):
    kind = serializers.ChoiceField(choices=SELECTION_KINDS)
    call_ids = serializers.ListField(
        child=serializers.UUIDField(), required=False, max_length=MAX_CALLS
    )
    note = serializers.CharField(
        required=False, allow_blank=True, default="", max_length=500
    )

    def validate(self, attrs):
        if attrs["kind"] == "calls" and not attrs.get("call_ids"):
            raise serializers.ValidationError({"call_ids": "Pick at least one call."})
        return attrs


class AskQuestionSerializer(serializers.Serializer):
    question = serializers.CharField(max_length=4000, trim_whitespace=True)
    answer_type = serializers.ChoiceField(
        choices=[one.value for one in AnswerType], default=AnswerType.YES_NO.value
    )
    labels = serializers.ListField(
        child=serializers.CharField(max_length=400), required=False, default=list
    )
    descriptions = serializers.DictField(
        child=serializers.CharField(allow_blank=True, max_length=4000),
        required=False,
        default=dict,
    )
    kinds = serializers.ListField(
        child=serializers.ChoiceField(choices=[one.value for one in Kind]),
        min_length=1,
    )


class AskCheckRequestSerializer(serializers.Serializer):
    selection = AskSelectionSerializer()
    questions = serializers.ListField(
        child=AskQuestionSerializer(), min_length=1, max_length=MAX_QUESTIONS
    )


class AskAcceptedSerializer(serializers.Serializer):
    """What the person saw on the check and accepted."""

    requested = serializers.IntegerField(min_value=1)
    credits_estimate = serializers.FloatField(allow_null=True)


class AskLaunchRequestSerializer(AskCheckRequestSerializer):
    accepted = AskAcceptedSerializer()


# --- responses ------------------------------------------------------------------------


class AskRefusalSerializer(serializers.Serializer):
    code = serializers.CharField()
    message = serializers.CharField()


class AskKindCountsSerializer(serializers.Serializer):
    """How many calls have one kind of evidence, and why the rest are missing."""

    present = serializers.IntegerField()
    not_applicable = serializers.IntegerField()
    # Missing calls by cause (call_evidence.Cause): never uploaded, too large...
    not_captured = serializers.DictField(child=serializers.IntegerField())


class AskCheckedQuestionSerializer(serializers.Serializer):
    name = serializers.CharField()
    kinds = serializers.ListField(child=serializers.CharField())
    ready = serializers.IntegerField()
    unmeasured = serializers.IntegerField()
    not_applicable = serializers.IntegerField()
    by_kind = serializers.DictField(child=AskKindCountsSerializer())


class AskCheckResponseSerializer(serializers.Serializer):
    requested = serializers.IntegerField()
    questions = AskCheckedQuestionSerializer(many=True)
    answers = serializers.IntegerField()
    credits_estimate = serializers.FloatField(allow_null=True)
    # Questions billed on what they use, with no price before they run.
    priced_by_use = serializers.ListField(
        child=serializers.CharField(), required=False, default=list
    )
    # Why the launch would be refused now (no key, no capability, no credits,
    # nothing ready), or null when it would launch.
    refusal = AskRefusalSerializer(allow_null=True)


class AskLaunchResponseSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    status = serializers.CharField()


class AskRateSerializer(serializers.Serializer):
    answered = serializers.IntegerField()
    labels = serializers.DictField(child=serializers.IntegerField())


class AskCountsSerializer(serializers.Serializer):
    requested = serializers.IntegerField()
    answered = serializers.IntegerField()
    unmeasured = serializers.IntegerField()
    not_applicable = serializers.IntegerField()
    grading_error = serializers.IntegerField()
    infrastructure_error = serializers.IntegerField()
    cancelled = serializers.IntegerField()
    pending = serializers.IntegerField()
    rate = AskRateSerializer()


class AskQuestionSummarySerializer(serializers.Serializer):
    key = serializers.CharField()
    name = serializers.CharField()
    answer_type = serializers.CharField()
    labels = serializers.ListField(child=serializers.CharField())
    kinds = serializers.ListField(child=serializers.CharField())
    counts = AskCountsSerializer()


class AskSummarySerializer(serializers.Serializer):
    id = serializers.UUIDField()
    status = serializers.CharField()
    stop_cause = serializers.CharField(allow_blank=True)
    created_at = serializers.DateTimeField()
    asked_by = serializers.CharField(allow_null=True)
    selection_kind = serializers.CharField()
    note = serializers.CharField(allow_blank=True)
    requested = serializers.IntegerField()
    credits_estimate = serializers.FloatField(allow_null=True)
    questions = AskQuestionSummarySerializer(many=True)


class AskListResponseSerializer(serializers.Serializer):
    asks = AskSummarySerializer(many=True)


class AskResultRowSerializer(serializers.Serializer):
    call_execution_id = serializers.UUIDField()
    # The run the call belongs to, so the row links to its call on that run's page.
    test_execution_id = serializers.UUIDField()
    # The call as the run's table names it: its scenario, and its trial.
    call_name = serializers.CharField()
    question_key = serializers.CharField()
    state = serializers.CharField()
    cause = serializers.CharField(allow_blank=True)
    # The answer given (Yes, No or one of the person's answers), or null.
    answer = serializers.CharField(source="label", allow_null=True, allow_blank=True)
    score = serializers.FloatField(allow_null=True)
    confidence = serializers.FloatField(allow_null=True)
    reason = serializers.CharField(allow_null=True, allow_blank=True)
    finished_at = serializers.DateTimeField(allow_null=True)


class AskDetailResponseSerializer(AskSummarySerializer):
    results = AskResultRowSerializer(many=True)
