"""The v3 call's worker record: which of a multi-agent app's workers did what, as the Participants tab reads it."""

from typing import Any, get_args

from rest_framework import serializers

from simulate.types import (
    HandOffEnd,
    PlacementStatus,
    RouteCellState,
    RouteParty,
    SubGoalUnscored,
    TextFormat,
    TrialRoute,
    Untied,
    WorkerAttribution,
    WorkerCleanupStatus,
    WorkerCollection,
    WorkerEnforcement,
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
    # Every worker has one: `observed_no_end` when it took part but no return or end was recorded.
    state = serializers.ChoiceField(choices=get_args(WorkerState))
    cut_short = serializers.BooleanField()
    # The world refused its later calls past its action deadline; its execution was not stopped.
    past_deadline = serializers.BooleanField()
    # When the harness ended this worker's own process past its deadline.
    stopped_at = serializers.FloatField(required=False)
    # A cut-short worker's cleanup, computed when served; absent for a worker that returned or never ran.
    cleanup = CleanupSerializer(required=False)
    # Its own checks in this call, beside the call's verdict and never deciding it.
    checks = WorkerCheckSerializer(many=True)
    enforcement = serializers.ChoiceField(
        choices=get_args(WorkerEnforcement),
        required=False,
        help_text="How its limits were enforced in this trial: enforced (only when the trial shows its own calls), "
        "not enforced, skipped, or not known.",
    )
    enforcement_label = serializers.CharField(required=False)
    channel_bypassed = serializers.BooleanField(
        default=False,
        help_text="Its calls went through the main app in this trial, so its limits were not enforced and its own "
        "checks were not graded.",
    )


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
        help_text="Why the world refused the call, as a code.",
    )
    # `async` for a hand-off to a background job, placed under the tool call that launched it.
    launch = serializers.ChoiceField(choices=("async",), required=False)
    relationship = serializers.ChoiceField(
        choices=get_args(WorkerRelationship), required=False
    )
    at = serializers.CharField(allow_null=True)
    ended_at = serializers.CharField(
        allow_null=True,
        required=False,
        help_text="When a tool call finished; null when the record does not say.",
    )
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
    participants_declared = serializers.BooleanField(
        help_text="The run's agent version declares at least one participant, so this call's record is worth waiting "
        "for; false for a single-agent app."
    )


class CallWorkersResponseSerializer(serializers.Serializer):
    workers = CallWorkerSerializer(many=True)
    events = CallWorkerEventSerializer(many=True)
    events_omitted = serializers.IntegerField(required=False)
    # Null when it was not seen: the timeline shows it as Unknown.
    coordinator = CallWorkerCoordinatorSerializer(allow_null=True, required=False)
    started_at = serializers.CharField(allow_null=True, required=False)
    spans_received = serializers.BooleanField(required=False)
    collected_at = serializers.CharField()
    project_id = serializers.CharField(
        allow_null=True,
        help_text="The project the call's traces were read from, to open them in Traces; null when none was read.",
    )
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


class TrialRoutesQuerySerializer(serializers.Serializer):
    scenario_key = serializers.CharField(
        help_text="The scenario whose trials to line up: the key the run's calls list serves as `source_scenario_key`."
    )
    tool_calls = serializers.BooleanField(
        required=False,
        default=False,
        help_text="Also a column for each tool called, by who called it and the tool.",
    )


class RouteColumnSerializer(serializers.Serializer):
    key = serializers.CharField()
    kind = serializers.ChoiceField(choices=get_args(WorkerStepKind))
    from_kind = serializers.ChoiceField(
        choices=get_args(RouteParty),
        help_text="Who asked, or called the tool: a worker, the coordinator, or unknown when the record does not say.",
    )
    from_id = serializers.CharField(
        allow_null=True, help_text="A worker's id within the run; null otherwise."
    )
    from_name = serializers.CharField(
        allow_null=True, help_text="Null when the record does not name it."
    )
    to_kind = serializers.ChoiceField(
        choices=get_args(RouteParty),
        allow_null=True,
        help_text="Null on a tool-call column.",
    )
    to_id = serializers.CharField(allow_null=True)
    to_name = serializers.CharField(allow_null=True)
    tool = serializers.CharField(allow_null=True)


class RouteCellSerializer(serializers.Serializer):
    state = serializers.ChoiceField(choices=get_args(RouteCellState))
    step = serializers.IntegerField(
        allow_null=True,
        help_text="The step's key in the trial's worker record (`steps[].key`); the first when it was taken more than once.",
    )
    steps = serializers.ListField(
        child=serializers.IntegerField(), help_text="Every step's key in this cell."
    )
    count = serializers.IntegerField()


class RouteTrialSerializer(serializers.Serializer):
    number = serializers.IntegerField()
    call_execution_id = serializers.CharField()
    result = serializers.CharField()
    route = serializers.ChoiceField(choices=get_args(TrialRoute))
    cells = RouteCellSerializer(
        many=True,
        allow_null=True,
        help_text="One per column; null when the trial has no route to compare.",
    )


class RouteSplitGroupSerializer(serializers.Serializer):
    state = serializers.ChoiceField(choices=get_args(RouteCellState))
    count = serializers.IntegerField()
    trials = serializers.ListField(child=serializers.IntegerField())


class RouteSplitSerializer(serializers.Serializer):
    column = serializers.IntegerField()
    key = serializers.CharField()
    groups = RouteSplitGroupSerializer(many=True)


class TrialRoutesResponseSerializer(serializers.Serializer):
    scenario_key = serializers.CharField()
    tool_calls = serializers.BooleanField()
    trials = RouteTrialSerializer(many=True)
    columns = RouteColumnSerializer(many=True)
    split = RouteSplitSerializer(
        allow_null=True, help_text="Null when every trial took the same route."
    )
    same_route = serializers.BooleanField()
