"""The v3 call detail's typed parts: the recorded function calls, who was on the call, and the trial's World state."""

from typing import get_args

from rest_framework import serializers

from simulate.selectors.call_detail import (
    ACTION_OUTCOMES,
    DECISION_OUTCOMES,
    PLAYS,
    PROBLEMS,
)
from simulate.serializers.harness_actors import (
    HarnessActorFallbackSerializer,
    HarnessActorLimitsSerializer,
)
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


class CallParticipantSerializer(serializers.Serializer):
    """Someone other than the person and the agent who took part in the call."""

    id = serializers.CharField(
        allow_null=True,
        help_text="The actor's id; null on a turn stored with a name only.",
    )
    name = serializers.CharField(allow_blank=True)
    role = serializers.CharField(allow_blank=True)
    channel = serializers.CharField(
        allow_null=True,
        help_text="How they were heard; null for an actor who only acted in the world.",
    )


class BlockedCallSerializer(serializers.Serializer):
    """One call the agent made to a host outside the test world, refused before it left the sandbox."""

    host = serializers.CharField()
    method = serializers.CharField(required=False, allow_blank=True)
    tool = serializers.CharField(
        required=False, allow_blank=True, help_text="The tool that made it, when known."
    )
    count = serializers.IntegerField(
        required=False,
        help_text="How many calls to this host were refused; absent on older trials (one).",
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

    actor = serializers.CharField(allow_blank=True)
    name = serializers.CharField(
        allow_blank=True,
        help_text="The actor's name, or its id when the trial did not name it.",
    )
    play = serializers.ChoiceField(choices=tuple(PLAYS))
    action = serializers.CharField(required=False, allow_null=True)
    arguments = serializers.DictField(
        required=False,
        help_text="What it sent: for a pre-run message (`history`), the message itself.",
    )
    channel = serializers.CharField(
        required=False,
        help_text="`voice` when decided on the call by a speaking actor.",
    )
    problem = serializers.ChoiceField(
        choices=tuple(PROBLEMS),
        required=False,
        help_text="For `problem`: the scenario's problem, never the agent's.",
    )
    why = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    status = serializers.IntegerField(required=False, allow_null=True)
    timeout_seconds = serializers.FloatField(required=False)
    fallback = HarnessActorFallbackSerializer(
        allow_null=True,
        help_text="What its fallback is, classified: nothing, a refusal, one of its own actions, or another actor "
        "answering; null when it has none.",
    )
    outcome = serializers.ChoiceField(
        choices=DECISION_OUTCOMES,
        required=False,
        help_text=(
            "For a timeout: timed_out, declined, fallback_action, fallback_not_committed (the fallback action was "
            "tried and the world did not commit it), answered_by or unresolved_at_end; for an action: what the world "
            "made of it (committed, refused, faulted, duplicate)."
        ),
    )
    action_outcome = serializers.ChoiceField(
        choices=tuple(ACTION_OUTCOMES),
        required=False,
        help_text="For a fallback action: what the world made of it (committed, refused, faulted, duplicate).",
    )
    verdict = serializers.ChoiceField(
        choices=("approved", "refused"),
        allow_null=True,
        help_text="For an approval: what the actor decided; null when it decided none or the trial predates verdicts.",
    )
    committed_after_refusal = serializers.BooleanField(
        help_text="The agent committed a write after this actor refused it; false on a trial that predates verdicts."
    )
    line = serializers.CharField(
        help_text="The row as one sentence: who, how they played, what it came to, the verdict and why."
    )


class ApprovalWriteSerializer(serializers.Serializer):
    action = serializers.CharField(allow_null=True)
    outcome = serializers.CharField(allow_null=True)


class ApprovalRequestSerializer(serializers.Serializer):
    """The agent's call that asked for the approval."""

    action = serializers.CharField(allow_null=True)
    at = serializers.FloatField(
        allow_null=True, help_text="Epoch seconds the world logged it."
    )
    action_id = serializers.CharField(allow_null=True)


class ApprovalDecisionSerializer(serializers.Serializer):
    """Who decided and how: the actor's own action, or the timeout's fallback (`timeout: <fallback>`, no time)."""

    by = serializers.CharField(allow_null=True)
    verdict = serializers.ChoiceField(
        choices=("approved", "refused"),
        allow_null=True,
        help_text="Null when it decided none or the trial predates verdicts.",
    )
    name = serializers.CharField(
        allow_null=True,
        help_text="Who decided, by display name; their id when nameless; null when nobody did.",
    )
    action = serializers.CharField(allow_null=True)
    outcome = serializers.CharField(allow_null=True)
    at = serializers.FloatField(allow_null=True)


class ApprovalLineSerializer(serializers.Serializer):
    """One approval, read from the action log: the agent's request, the decision, and the agent's writes around it."""

    request = ApprovalRequestSerializer()
    decision = ApprovalDecisionSerializer()
    before_decision = ApprovalWriteSerializer(many=True)
    after_decision = ApprovalWriteSerializer(many=True)
    acted_before_decision = serializers.BooleanField()
    verdict = serializers.ChoiceField(
        choices=("approved", "refused"),
        allow_null=True,
        help_text="Null on a trial that predates verdicts.",
    )
    committed_after_refusal = serializers.BooleanField(
        help_text="The agent committed a write after the refusal; false on a trial that predates verdicts."
    )


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
    """About a fault (`action`, `on_call`, `mode`), or for `evidence_incomplete` how many pieces (`count`)."""

    code = serializers.CharField(
        help_text="fault_not_applied, fault_not_triggered, fault_unknown_action, fault_unreadable, "
        "duplicates_unreadable, evidence_incomplete, start_differs, changed_outside_world, or approval_missing "
        "(the agent's change refused before anyone approved it: `action`, `count`)"
    )
    message = serializers.CharField(
        help_text="Plain words for the person reading the run."
    )
    action = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    on_call = serializers.IntegerField(required=False, allow_null=True)
    mode = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    count = serializers.IntegerField(
        required=False, help_text="For evidence_incomplete."
    )


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


class TrialRecordChangeSerializer(serializers.Serializer):
    """One record the trial added, changed or removed."""

    table = serializers.CharField()
    key = serializers.JSONField(
        help_text="The record's key as the table holds it: one value, or several."
    )
    kind = serializers.ChoiceField(choices=("added", "changed", "removed"))
    before = serializers.DictField(
        child=serializers.JSONField(),
        allow_null=True,
        help_text="Its values before; null when added.",
    )
    after = serializers.DictField(
        child=serializers.JSONField(),
        allow_null=True,
        help_text="Its values after; null when removed.",
    )

    def get_fields(self) -> dict[str, serializers.Field]:
        # `fields` is the name of the Serializer attribute holding them, so this field is added here.
        fields = super().get_fields()
        fields["fields"] = serializers.ListField(
            child=serializers.CharField(),
            help_text="The fields a change changed; empty when added or removed.",
        )
        return fields


class TrialRecordsSerializer(serializers.Serializer):
    changes = TrialRecordChangeSerializer(many=True)
    skipped = RecordSkippedSerializer(many=True, required=False)
    truncated = serializers.IntegerField(
        required=False, help_text="Changes past the cap, counted, not listed."
    )


class TrialStartSerializer(serializers.Serializer):
    state = serializers.ChoiceField(choices=("seeded", "differs", "unknown"))
    tables = serializers.ListField(child=serializers.CharField(), required=False)
    against = serializers.ChoiceField(
        choices=("sealed_seed", "first_trial"),
        required=False,
        help_text="What the start was compared with; absent on trials from before it was said.",
    )
    line = serializers.CharField(
        allow_null=True,
        required=False,
        help_text="What to say about the start; null when there is nothing to say (a start that differs has its "
        "own notice).",
    )


class TrialStateSerializer(serializers.Serializer):
    """A trial's World state. Every list is present, empty when the trial had none."""

    state_changes = TrialStateChangeSerializer(many=True)
    duplicates = DuplicateCommitSerializer(
        many=True,
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
        help_text="True when what changed in the world could not be read; checks that needed it read Unmeasured.",
    )
    records = TrialRecordsSerializer(
        required=False,
        help_text="The records the trial changed, before and after; absent when they could not be read.",
    )
    start = TrialStartSerializer(
        required=False,
        help_text="Whether the trial started from the seeded records, as every trial should.",
    )
    decisions = ActorDecisionSerializer(
        many=True,
        help_text="What actors did or chose during the trial; messages written before it are in pre_run.",
    )
    pre_run = ActorDecisionSerializer(
        many=True,
        help_text="Messages actors wrote before the run: the trial's starting point, not a change made during it.",
    )
    approvals = ApprovalLineSerializer(many=True)
    end_state_url = serializers.URLField(
        required=False,
        help_text="The world's records at the trial's end and its action log, kept for grading it again.",
    )
    usage = ActorUsageSerializer(many=True)
    usage_summary = ActorUsageLineSerializer(
        many=True,
        help_text="Actors' model use summed per actor and model.",
    )
    usage_total = ActorUsageTotalSerializer(
        required=False,
        help_text="Actors' model use in all; absent when no actor used a model.",
    )
    limits = HarnessActorLimitsSerializer(
        required=False,
        help_text="The actor limits this trial ran with; null is no limit.",
    )
    notices = TrialNoticeSerializer(
        many=True,
        help_text="How the trial ran that the person should know, e.g. a scenario fault that did not take effect.",
    )
