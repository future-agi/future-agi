"""The v3 call's worker record: which of a multi-agent app's workers did what, as the Participants tab reads it."""

from typing import Any, get_args

from rest_framework import serializers

from simulate.types import (
    HandOffEnd,
    PlacementStatus,
    SubGoalUnscored,
    TextFormat,
    Untied,
    WorkerAttribution,
    WorkerCleanupStatus,
    WorkerCollection,
    WorkerEventType,
    WorkerRelationship,
    WorkersState,
    WorkerState,
    WorkerStatus,
    WorkerStepKind,
)
from tfc.utils.serializer_fields import JsonValueField


class CleanupSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=get_args(WorkerCleanupStatus))
    at = serializers.CharField(allow_null=True)


class WorkerCheckSerializer(serializers.Serializer):
    name = serializers.CharField()
    # Null when nothing passed or failed it; `result` then says why.
    passed = serializers.BooleanField(allow_null=True)
    result = serializers.ChoiceField(choices=get_args(SubGoalUnscored), required=False)


class CallWorkerSerializer(serializers.Serializer):
    worker_id = serializers.CharField()
    id = serializers.CharField()
    name = serializers.CharField()
    # Declared names are not proof: true only when the worker's own spans or tool calls arrived.
    observed = serializers.BooleanField()
    undeclared = serializers.BooleanField()
    instance_ids = serializers.ListField(child=serializers.CharField())
    model = serializers.CharField(required=False)
    tokens = serializers.IntegerField(required=False)
    # Absent on a record read before end states were kept.
    state = serializers.ChoiceField(choices=get_args(WorkerState), required=False)
    cut_short = serializers.BooleanField()
    # The world refused its later calls past its action deadline; its execution was not stopped.
    past_deadline = serializers.BooleanField()
    # When the harness ended this worker's own process past its deadline.
    stopped_at = serializers.FloatField(required=False)
    # A cut-short worker's cleanup, computed when served; absent for a worker that returned or never ran.
    cleanup = CleanupSerializer(required=False)
    # Its own checks in this call, beside the call's verdict and never deciding it.
    checks = WorkerCheckSerializer(many=True)


class ShownTextSerializer(serializers.Serializer):
    """A text with how to show it: `code` is data, already indented when it is JSON."""

    format = serializers.ChoiceField(choices=get_args(TextFormat))
    text = serializers.CharField()


class CallWorkerEventSerializer(serializers.Serializer):
    seq = serializers.IntegerField()
    type = serializers.ChoiceField(choices=get_args(WorkerEventType))
    worker_id = serializers.CharField(allow_null=True)
    by = serializers.CharField(allow_null=True, required=False)
    delegation_id = serializers.CharField(required=False)
    task = serializers.CharField(allow_null=True, required=False)
    result = serializers.CharField(allow_null=True, required=False)
    task_shown = ShownTextSerializer(allow_null=True, required=False)
    result_shown = ShownTextSerializer(allow_null=True, required=False)
    tool = serializers.CharField(required=False)
    status = serializers.ChoiceField(choices=get_args(WorkerStatus), required=False)
    # Absent for the sandbox's own record, which has no span to open in Traces.
    span_id = serializers.CharField(required=False)
    trace_id = serializers.CharField(required=False)
    input = JsonValueField(allow_null=True, required=False)
    output = JsonValueField(allow_null=True, required=False)
    # The record's size limit was reached before this step, so that part was not kept.
    input_omitted = serializers.BooleanField(required=False)
    output_omitted = serializers.BooleanField(required=False)
    refusal_code = serializers.CharField(
        allow_blank=True,
        required=False,
        help_text="The world's refusal code, as the harness sealed it.",
    )
    # `async` for a hand-off to a background job, placed under the tool call that launched it.
    launch = serializers.ChoiceField(choices=("async",), required=False)
    relationship = serializers.ChoiceField(
        choices=get_args(WorkerRelationship), required=False
    )
    at = serializers.CharField(allow_null=True)
    attribution = serializers.ChoiceField(choices=get_args(WorkerAttribution))


# Its own definition, because drf-yasg emits one per serializer and `CallWorkerEvent` is also the never-null event.
class CallWorkerEventOrNullSerializer(CallWorkerEventSerializer):
    """A hand-off or its return, null when the record did not see it."""

    class Meta:
        ref_name = "CallWorkerEventOrNull"
        swagger_schema_fields = {"x-nullable": True}


class CallWorkerCoordinatorSerializer(serializers.Serializer):
    name = serializers.CharField()


class CallWorkerTurnSerializer(serializers.Serializer):
    text = serializers.CharField(allow_null=True)
    at = serializers.CharField(allow_null=True)
    shown = ShownTextSerializer(allow_null=True, required=False)


class CallWorkerStepSerializer(serializers.Serializer):
    """A row of the tab in reading order; `parent` is the `key` of the hand-off it sits under, null at the top."""

    kind = serializers.ChoiceField(choices=get_args(WorkerStepKind))
    key = serializers.IntegerField()
    # Declared here, not DRF's `Field.parent`: the serializer's metaclass takes it off the class.
    parent: Any = serializers.IntegerField(allow_null=True)
    depth = serializers.IntegerField()
    worker_id = serializers.CharField(allow_null=True)
    transfer = serializers.BooleanField(required=False)
    handoff = CallWorkerEventOrNullSerializer(allow_null=True, required=False)
    returned = CallWorkerEventOrNullSerializer(allow_null=True, required=False)
    started_at = serializers.CharField(allow_null=True, required=False)
    ended_at = serializers.CharField(allow_null=True, required=False)
    parallel = serializers.BooleanField(required=False)
    end = serializers.ChoiceField(choices=get_args(HandOffEnd), required=False)
    end_state = serializers.ChoiceField(choices=get_args(WorkerState), required=False)
    # A hand-off that failed or never returned, as `counts.did_not_finish` counts it.
    did_not_finish = serializers.BooleanField(required=False)
    # The worker's first hand-off, which carries its model and token totals.
    first_of_worker = serializers.BooleanField(required=False)
    children = serializers.ListField(child=serializers.IntegerField(), required=False)
    call = CallWorkerEventSerializer(required=False)
    # Turn n starts at the caller's nth message, 0 is before they spoke; null when the record does not say.
    turn = serializers.IntegerField(allow_null=True)
    order = serializers.IntegerField()
    # `unknown` when the record does not say which hand-off this step belongs to: it stays at the top, never guessed.
    relationship = serializers.ChoiceField(choices=get_args(WorkerRelationship))


class CallWorkerRoundSerializer(serializers.Serializer):
    """One turn that had steps; turn null holds the steps whose turn the record does not say."""

    turn = serializers.IntegerField(allow_null=True)
    request = CallWorkerTurnSerializer(allow_null=True)
    response = CallWorkerTurnSerializer(allow_null=True)
    children = serializers.ListField(child=serializers.IntegerField())
    calls_outside = serializers.IntegerField()
    # Every hand-off of the turn, nested ones included, in table order.
    handoffs = serializers.ListField(child=serializers.IntegerField())
    # False when a missing request or reply is not missed: work before the caller spoke, or in no recorded turn.
    expects_messages = serializers.BooleanField()


class HandOffCountsSerializer(serializers.Serializer):
    hand_offs = serializers.IntegerField()
    workers = serializers.IntegerField()
    finished = serializers.IntegerField()
    did_not_finish = serializers.IntegerField()
    passed = serializers.IntegerField()
    cut_off = serializers.IntegerField()


class FailurePlacementSerializer(serializers.Serializer):
    """A failed or unsure sub-goal on its worker's one hand-off, or not tied to a step, with why."""

    name = serializers.CharField()
    status = serializers.ChoiceField(choices=get_args(PlacementStatus))
    step = serializers.IntegerField(allow_null=True)
    # The worker whose own check it is, as the record's `worker_id`; null for a task check.
    worker_id = serializers.CharField(allow_null=True)
    # `no_moment`: a task check, which nothing ties to a moment; `several_hand_offs` / `no_hand_off`: a worker's check
    # whose worker was handed work more than once, or never.
    untied = serializers.ChoiceField(choices=get_args(Untied), allow_null=True)


class CallWorkersQuerySerializer(serializers.Serializer):
    attempt = serializers.UUIDField(
        required=False,
        help_text="An earlier attempt's rerun snapshot id: its record as the rerun kept it; 404 when it is not this "
        "call's, state `not_kept` when it was snapshotted before records were kept.",
    )


class CallWorkersPendingSerializer(serializers.Serializer):
    pending = serializers.BooleanField()
    next_read_at = serializers.CharField()


class CallWorkersResponseSerializer(serializers.Serializer):
    workers = CallWorkerSerializer(many=True)
    events = CallWorkerEventSerializer(many=True)
    events_omitted = serializers.IntegerField(required=False)
    # Null when it was not seen: the timeline shows it as Unknown.
    coordinator = CallWorkerCoordinatorSerializer(allow_null=True, required=False)
    started_at = serializers.CharField(allow_null=True, required=False)
    spans_received = serializers.BooleanField(required=False)
    collected_at = serializers.CharField()
    collection = serializers.ChoiceField(
        choices=get_args(WorkerCollection), required=False
    )
    steps = CallWorkerStepSerializer(many=True)
    turns = CallWorkerRoundSerializer(many=True)
    depth = serializers.IntegerField()
    parallel = serializers.IntegerField()
    # Hand-offs to background jobs, placed under the tool call that launched each.
    asynchronous = serializers.IntegerField()
    overlap_unknown = serializers.BooleanField()
    worker_depths = serializers.DictField(child=serializers.IntegerField())
    counts = HandOffCountsSerializer()
    # The record is not whole, so a missing return may only be one it did not keep.
    cut_off = serializers.BooleanField()
    beyond_boundary = serializers.BooleanField()
    placements = FailurePlacementSerializer(many=True)
    stopped_by_user = serializers.BooleanField()
    # A later read is still due and may change the record; the client reads again once, at `next_read_at`.
    pending = serializers.BooleanField()
    next_read_at = serializers.CharField(required=False)
    # `not_kept`: an earlier attempt snapshotted before records were kept; `unreadable`: the read failed. Both empty.
    state = serializers.ChoiceField(choices=get_args(WorkersState))
