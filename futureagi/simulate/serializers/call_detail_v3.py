"""The v3 call detail's typed parts: the recorded function calls, who was on the call, and the trial's World state."""

from typing import get_args

from rest_framework import serializers

from simulate.types import CallActorDelivery

FUNCTION_CALL_KINDS = ("agent_call", "world_actor_action")


class FunctionCallSerializer(serializers.Serializer):
    id = serializers.CharField(required=False)
    name = serializers.CharField(required=False)
    arguments = serializers.JSONField(required=False)
    result = serializers.JSONField(required=False)
    output = serializers.JSONField(required=False)
    duration_ms = serializers.FloatField(required=False)
    start_time_ms = serializers.IntegerField(required=False)
    ok = serializers.BooleanField(required=False)
    error = serializers.CharField(required=False, allow_blank=True)
    refused = serializers.BooleanField(required=False)
    # Epoch seconds the world logged it.
    at = serializers.FloatField(required=False)
    # The world's action log: its id, and how the world took it (committed, refused, faulted or duplicate).
    action_id = serializers.CharField(required=False, allow_null=True)
    outcome = serializers.CharField(required=False, allow_null=True)
    # Set on a world actor's action; the agent's own calls carry none.
    actor_id = serializers.CharField(required=False)
    actor_name = serializers.CharField(required=False)
    actor_role = serializers.CharField(required=False, allow_blank=True)
    refusal_code = serializers.CharField(required=False, allow_null=True)
    parent_event_id = serializers.CharField(required=False, allow_null=True)
    on_behalf_of = serializers.CharField(required=False, allow_null=True)
    kind = serializers.ChoiceField(
        choices=FUNCTION_CALL_KINDS,
        required=False,
        help_text="agent_call for the agent's own tool call, world_actor_action for an actor's action in the world.",
    )

    class Meta:
        ref_name = "SimulateRunV3FunctionCall"


class CallCallActorSerializer(serializers.Serializer):
    """Someone on the person's side in the call; a call stored by an older build may lack any field."""

    id = serializers.CharField(
        required=False,
        allow_blank=True,
        help_text="The actor's id in the environment.",
    )
    name = serializers.CharField(
        required=False, allow_blank=True, help_text="The name the transcript shows."
    )
    role = serializers.CharField(
        required=False, allow_blank=True, help_text="Who they are to the caller."
    )
    channel = serializers.CharField(
        required=False,
        allow_blank=True,
        help_text="The channel id they joined the call on.",
    )
    delivery = serializers.ChoiceField(
        choices=list(get_args(CallActorDelivery)),
        required=False,
        help_text="How far they got in the call; absent on a call stored without a summary.",
    )


class BlockedCallSerializer(serializers.Serializer):
    """One call the agent made to a host outside the test world, refused before it left the sandbox."""

    host = serializers.CharField()
    method = serializers.CharField(required=False, allow_blank=True)
    tool = serializers.CharField(
        required=False, allow_blank=True, help_text="The tool that made it, when known."
    )


class TrialStateChangeSerializer(serializers.Serializer):
    """What one participant (the agent, or an actor) attempted in the trial and what the world made of it."""

    who = serializers.CharField()
    name = serializers.CharField()
    attempted = serializers.IntegerField()
    committed = serializers.IntegerField()
    refused = serializers.IntegerField()
    faulted = serializers.IntegerField()
    duplicate = serializers.IntegerField()
    committed_actions = serializers.ListField(child=serializers.CharField())
    read = serializers.IntegerField(
        required=False,
        help_text="Calls that only read the world: counted in attempted, never in committed; absent before reads were told apart.",
    )
    read_actions = serializers.ListField(
        child=serializers.CharField(allow_blank=True),
        required=False,
        help_text="The reads, in order.",
    )
    no_change = serializers.BooleanField(
        required=False,
        help_text="True when this participant committed nothing: a result shown as 'no change', never left out.",
    )
    blocked = BlockedCallSerializer(
        many=True,
        required=False,
        help_text="Calls to hosts outside the test world, refused before they left: counted in refused, never "
        "a change.",
    )


class ActorDecisionSerializer(serializers.Serializer):
    """One thing an actor did or chose in the trial: scheduled (mocked), decided (live) or written before it (history);
    or the world waiting on it (waiting) and what its fallback made of no answer in time (timeout).
    """

    actor = serializers.CharField()
    name = serializers.CharField(
        required=False,
        help_text="The actor's name, so a pre-run writer who changed nothing in the trial is named, not shown by id.",
    )
    play = serializers.ChoiceField(
        choices=(
            "mocked",
            "live",
            "history",
            "waiting",
            "timeout",
            "problem",
            "not_triggered",
        )
    )
    action = serializers.CharField(required=False, allow_null=True)
    arguments = serializers.DictField(
        required=False,
        help_text="What it sent: for a pre-run message (`history`), the message itself.",
    )
    channel = serializers.CharField(
        required=False,
        help_text="`voice` when decided on the call by a speaking actor.",
    )
    problem = serializers.CharField(
        required=False,
        help_text="For `problem`: no_world_door or not_listed: the scenario's problem, never the agent's.",
    )
    why = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    status = serializers.IntegerField(required=False, allow_null=True)
    timeout_seconds = serializers.FloatField(required=False)
    fallback = serializers.CharField(
        required=False,
        help_text="silent, decline, one of the actor's own actions, or the id of the actor who answers instead",
    )
    outcome = serializers.CharField(
        required=False,
        help_text=(
            "For a timeout: timed_out, declined, fallback_action, fallback_not_committed (the fallback action was "
            "tried and the world did not commit it), answered_by or unresolved_at_end; for an action: what the world "
            "made of it (committed, refused, faulted, duplicate)."
        ),
    )
    action_outcome = serializers.CharField(
        required=False,
        help_text="For a fallback action: what the world made of it (committed, refused, faulted, duplicate).",
    )


class ApprovalWriteSerializer(serializers.Serializer):
    action = serializers.CharField(allow_null=True)
    outcome = serializers.CharField(allow_null=True)


class ApprovalLineSerializer(serializers.Serializer):
    """One approval, read from the action log: the agent's request, the decision, and the agent's writes around it."""

    request = serializers.DictField()
    decision = serializers.DictField()
    before_decision = ApprovalWriteSerializer(many=True)
    after_decision = ApprovalWriteSerializer(many=True)
    acted_before_decision = serializers.BooleanField()


class ActorUsageSerializer(serializers.Serializer):
    actor = serializers.CharField()
    model = serializers.CharField(allow_blank=True)
    tokens_in = serializers.IntegerField()
    tokens_out = serializers.IntegerField()
    cost_usd = serializers.FloatField()


class ActorUsageLineSerializer(serializers.Serializer):
    """One actor's model use on one model in the trial, summed."""

    actor = serializers.CharField()
    name = serializers.CharField(
        help_text="The actor's name, or its id when the trial did not name it."
    )
    model = serializers.CharField(allow_blank=True)
    calls = serializers.IntegerField()
    tokens = serializers.IntegerField()
    cost_usd = serializers.FloatField()


class ActorUsageTotalSerializer(serializers.Serializer):
    calls = serializers.IntegerField()
    tokens = serializers.IntegerField()
    cost_usd = serializers.FloatField()


class TrialNoticeSerializer(serializers.Serializer):
    code = serializers.CharField(
        help_text="fault_not_applied, fault_not_triggered, start_differs or changed_outside_world"
    )
    message = serializers.CharField(
        help_text="Plain words for the person reading the run."
    )
    action = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    on_call = serializers.IntegerField(required=False, allow_null=True)
    mode = serializers.CharField(required=False, allow_null=True, allow_blank=True)


class DuplicateCommitSerializer(serializers.Serializer):
    """One of the agent's writes the world committed twice: the first's answer was lost, and the agent repeated it."""

    action = serializers.CharField()
    name = serializers.CharField(required=False, allow_blank=True)
    arguments = serializers.JSONField(required=False, allow_null=True)
    first_action_id = serializers.CharField(
        required=False, allow_null=True, allow_blank=True
    )
    repeat_action_id = serializers.CharField(
        required=False, allow_null=True, allow_blank=True
    )


class RecordSkippedSerializer(serializers.Serializer):
    table = serializers.CharField()
    why = serializers.CharField()


class TrialRecordsSerializer(serializers.Serializer):
    # Each `{table, key, kind: added|changed|removed, before, after, fields}`; `fields` names the changed columns.
    changes = serializers.ListField(child=serializers.JSONField())
    skipped = RecordSkippedSerializer(many=True, required=False)
    truncated = serializers.IntegerField(
        required=False, help_text="Changes past the cap, counted, not listed."
    )


class TrialStartSerializer(serializers.Serializer):
    state = serializers.ChoiceField(choices=("seeded", "differs", "unknown"))
    tables = serializers.ListField(child=serializers.CharField(), required=False)


class TrialStateSerializer(serializers.Serializer):
    state_changes = TrialStateChangeSerializer(many=True, required=False)
    duplicates = DuplicateCommitSerializer(
        many=True,
        required=False,
        help_text="Writes the world committed twice after the agent's answer was lost; the trial fails on them.",
    )
    recorded = serializers.BooleanField(
        required=False,
        help_text="False when World changes are not recorded for this agent: its tools run inside it.",
    )
    why = serializers.CharField(
        required=False, allow_blank=True, help_text="Why they were not recorded."
    )
    unreadable = serializers.BooleanField(
        required=False,
        help_text="True when what changed in the world could not be read; checks that needed it read Unmeasured.",
    )
    records = TrialRecordsSerializer(
        required=False, help_text="The records the trial changed, before and after."
    )
    start = TrialStartSerializer(
        required=False,
        help_text="Whether the trial started from the seeded records, as every trial should.",
    )
    decisions = ActorDecisionSerializer(
        many=True,
        required=False,
        help_text="What actors did or chose during the trial; messages written before it are in pre_run.",
    )
    pre_run = ActorDecisionSerializer(
        many=True,
        required=False,
        help_text="Messages actors wrote before the run: the trial's starting point, not a change made during it.",
    )
    approvals = ApprovalLineSerializer(many=True, required=False)
    end_state_url = serializers.URLField(
        required=False,
        help_text="The world's records at the trial's end and its action log, kept for grading it again.",
    )
    usage = ActorUsageSerializer(many=True, required=False)
    usage_summary = ActorUsageLineSerializer(
        many=True,
        required=False,
        help_text="Actors' model use summed per actor and model.",
    )
    usage_total = ActorUsageTotalSerializer(
        required=False,
        help_text="Actors' model use in all; absent when no actor used a model.",
    )
    limits = serializers.DictField(
        required=False,
        help_text="The actor limits this trial ran with; null is no limit.",
    )
    notices = TrialNoticeSerializer(
        many=True,
        required=False,
        help_text="How the trial ran that the person should know, e.g. a scenario fault that did not take effect.",
    )
