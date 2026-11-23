from __future__ import annotations

from typing import get_args

from rest_framework import serializers

from model_hub.serializers.contracts import JsonObjectRequestField
from simulate.constants.harness_environment import (
    ACTORS_TABS,
    AGENT_TYPE_CHAT,
    AGENT_TYPE_VOICE,
    RUN_KIND_ENVIRONMENT,
    RUN_KIND_SIMULATE,
    STATUS_BUILDING,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_RUNNING,
)
from simulate.constants.run_outcome import OUTCOME_STATUSES
from simulate.constants.scenario_faults import FAULT_MODES
from simulate.models import HarnessNudge, HostedHarnessJob
from simulate.serializers.harness_job import (
    TARGET_SYSTEM_PROMPT_MAX_CHARS,
    HarnessAgentConfigChangeSerializer,
    HarnessRunPinnedVersionsSerializer,
    HarnessRunWarningSerializer,
)
from simulate.serializers.requests.run_test_evals import (
    EvalConfigUpdateRequestSerializer,
)
from simulate.serializers.response.run_test import RunVersionLabelSerializer
from simulate.serializers.trial_pass import TrialPassFieldsSerializer
from simulate.types import WorkerBinding, WorkerEnforcement
from tfc.utils.api_serializers import StrictInputSerializer


class _Labelled(serializers.Serializer):
    """A serializer with a `label` field. `label` is also an attribute every Field has, so the field is added in
    `get_fields` rather than declared on the class."""

    def get_fields(self) -> dict[str, serializers.Field]:
        return {**super().get_fields(), "label": serializers.CharField()}


class HarnessEnvironmentListQuerySerializer(serializers.Serializer):
    """Paging for the environments list.

    ``limit`` is named to match ``ExtendedPageNumberPagination``'s own query
    parameter so the two cannot disagree about what a page is.
    """

    page = serializers.IntegerField(required=False, min_value=1)
    limit = serializers.IntegerField(required=False, min_value=1, max_value=100)
    agent_definition_id = serializers.UUIDField(required=False)


class HarnessEnvironmentRenameSerializer(serializers.Serializer):
    """The only field an environment exposes for editing.

    Blank is rejected rather than treated as a reset: clearing the name would
    silently fall back to the derived one, which reads as the rename having been
    ignored.
    """

    name = serializers.CharField(
        max_length=255, allow_blank=False, trim_whitespace=True
    )


class AgentVersionRefSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    version_number = serializers.IntegerField()


class EnvironmentVersionRefSerializer(AgentVersionRefSerializer):
    pass


class HarnessEnvironmentFieldsSerializer(serializers.Serializer):
    """One row of the environments list.

    ``description``, ``tools_count`` and ``sub_goals_count`` are null until
    authoring has produced the snapshot each comes from; they are reported as
    absent rather than as an empty string or a zero, which would read as "this
    environment has no tools".
    """

    id = serializers.UUIDField()
    name = serializers.CharField()
    description = serializers.CharField(allow_null=True)
    domain = serializers.CharField(allow_null=True)
    source_kind = serializers.CharField()
    agent_type = serializers.ChoiceField(choices=(AGENT_TYPE_VOICE, AGENT_TYPE_CHAT))
    status = serializers.ChoiceField(
        choices=(STATUS_BUILDING, STATUS_RUNNING, STATUS_COMPLETED, STATUS_FAILED)
    )
    # The fine-grained pipeline stage behind ``status``, so a caller that draws a
    # progress bar does not have to infer it from the four-state pill.
    stage = serializers.CharField()
    scenario_count = serializers.IntegerField()
    sub_goals_count = serializers.IntegerField(allow_null=True)
    tools_count = serializers.IntegerField(allow_null=True)
    # 0 or 1 today: a job carries at most one test execution, so this counts
    # "has it been run" until run history becomes a table of its own.
    runs_count = serializers.IntegerField()
    # The agent version this environment tests by default; null before
    # versioning recorded one.
    agent_version = AgentVersionRefSerializer(allow_null=True)
    # Whether the environment follows its agent's production version or stays
    # pinned to the one above.
    agent_policy = serializers.ChoiceField(choices=("follow", "pinned"))
    last_updated = serializers.DateTimeField(allow_null=True)
    created_at = serializers.DateTimeField()


class RunEnvironmentRefSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()


class HarnessRunSummarySerializer(TrialPassFieldsSerializer):
    kind = serializers.ChoiceField(choices=(RUN_KIND_ENVIRONMENT, RUN_KIND_SIMULATE))
    test_execution_id = serializers.UUIDField()
    run_test_id = serializers.UUIDField()
    run_test_name = serializers.CharField(allow_blank=True)
    run_number = serializers.IntegerField(allow_null=True)
    status = serializers.CharField()
    environment = RunEnvironmentRefSerializer(allow_null=True)
    agent_version = AgentVersionRefSerializer(allow_null=True)
    environment_version = EnvironmentVersionRefSerializer(allow_null=True)
    # True when the run covered only some of the environment's scenarios;
    # null for a Simulate run, which has no environment to compare with.
    partial = serializers.BooleanField(allow_null=True)  # type: ignore[assignment]  # the API field is named partial, as BaseSerializer's own
    outcome_passed = serializers.IntegerField(
        allow_null=True, help_text="Passed receipts; null for a run without receipts."
    )
    outcome_failed = serializers.IntegerField(
        allow_null=True, help_text="Failed plus errored receipts."
    )
    outcome_errored = serializers.IntegerField(
        allow_null=True, help_text="Errored receipts, also counted in outcome_failed."
    )
    outcome_skipped = serializers.IntegerField(allow_null=True)
    outcome_cancelled = serializers.IntegerField(
        allow_null=True, help_text="Calls the user stopped; neither errored nor failed."
    )
    outcome_no_score = serializers.IntegerField(
        allow_null=True, help_text="Calls that ran with no pass or fail."
    )
    outcome_status = serializers.ChoiceField(
        choices=OUTCOME_STATUSES,
        allow_null=True,
        help_text="The run's verdict from its receipts; null for a run without receipts.",
    )
    pass_rate = serializers.IntegerField(
        allow_null=True,
        min_value=0,
        max_value=100,
        help_text="Scored passes over every call that ran to a verdict (passed, failed, errored, skipped), as a "
        "whole percent; null when there is none or the run has no receipts.",
    )
    scored = serializers.IntegerField(
        allow_null=True,
        help_text="The pass rate's base; null for a run without receipts.",
    )
    started_at = serializers.DateTimeField(allow_null=True)
    completed_at = serializers.DateTimeField(allow_null=True)


class HarnessEnvironmentSerializer(HarnessEnvironmentFieldsSerializer):
    last_run = HarnessRunSummarySerializer(allow_null=True)


class HarnessEnvironmentListResponseSerializer(serializers.Serializer):
    count = serializers.IntegerField()
    next = serializers.CharField(allow_null=True)
    previous = serializers.CharField(allow_null=True)
    total_pages = serializers.IntegerField()
    current_page = serializers.IntegerField()
    results = HarnessEnvironmentSerializer(many=True)


class HarnessEnvironmentRunLinkSerializer(serializers.Serializer):
    run_test_id = serializers.UUIDField(allow_null=True)
    test_execution_id = serializers.UUIDField(allow_null=True)
    simulation_url = serializers.CharField(allow_null=True)


class HarnessEnvironmentAgentSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField(allow_null=True)
    provider = serializers.CharField(allow_null=True)
    versions_count = serializers.IntegerField()
    active_version = serializers.CharField(allow_null=True)


class HarnessEnvironmentOverviewSerializer(HarnessEnvironmentFieldsSerializer):
    """The list row plus the counts the overview draws; each null until authored."""

    flows_count = serializers.IntegerField(allow_null=True)
    guardrails_count = serializers.IntegerField(allow_null=True)
    personas_count = serializers.IntegerField(allow_null=True)
    evaluations_count = serializers.IntegerField()
    run = HarnessEnvironmentRunLinkSerializer()
    agent = HarnessEnvironmentAgentSerializer(allow_null=True)


class HarnessEnvironmentAddEvaluationSerializer(serializers.Serializer):
    """The one thing adding an eval takes: which eval, by its exact name."""

    name = serializers.CharField(
        max_length=255, allow_blank=False, trim_whitespace=True
    )


class HarnessEnvironmentRunEvaluationQueuedSerializer(serializers.Serializer):
    """What adding an eval from inside a run reports back.

    Not the environment detail -- the client refetches that itself.
    ``completed_calls`` is the finished calls the endpoint looked at, and the
    four others always partition it exactly.
    """

    queued = serializers.IntegerField(
        help_text=(
            "Stamped and scheduled for dispatch -- not yet dispatched. A "
            "call whose stamp committed but whose grading job then failed "
            "to queue is still counted here, not subtracted."
        )
    )
    skipped_existing = serializers.IntegerField()
    skipped_in_flight = serializers.IntegerField()
    skipped_pending = serializers.IntegerField()
    completed_calls = serializers.IntegerField()


class HarnessEnvironmentRunEvaluationsSerializer(serializers.Serializer):
    """Which of the environment's evals to grade a finished run's calls with again."""

    eval_config_ids = serializers.ListField(
        child=serializers.UUIDField(), allow_empty=False, min_length=1
    )
    # allow_null makes a form body without the key read as None (left
    # alone) rather than DRF's html default of False.
    enable_tool_evaluation = serializers.BooleanField(
        required=False,
        allow_null=True,
        help_text="Saved on the environment before grading starts; left as it is when absent.",
    )

    def validate_eval_config_ids(self, value):
        if len(set(value)) != len(value):
            raise serializers.ValidationError("Each evaluation can be sent only once.")
        return value


class HarnessEnvironmentRunEvaluationsQueuedSerializer(serializers.Serializer):
    """What grading a run again reports back."""

    call_execution_count = serializers.IntegerField(
        help_text="How many of the run's calls were queued for grading."
    )


class HarnessEnvironmentEvalEditSerializer(EvalConfigUpdateRequestSerializer):
    """What may change on one of an environment's evals.

    The run-test page's edit fields without the two that also grade (grading
    a run again is its own request here) and without ``template_id``: an
    environment eval's id is derived from its template, so switching the
    template in place would leave a row whose id names a template it no
    longer has, and a later remove and re-add of that template would revive
    it with the wrong template. A switch is a remove and an add, which also
    runs the add route's offer and mapping checks. Every key is optional; the
    view refuses an empty body with its own sentence.
    """

    run = None
    test_execution_id = None
    template_id = None


class HarnessEnvironmentToolCallEvaluationSerializer(serializers.Serializer):
    """The tool-call judge's switch, sent as a whole state rather than a patch.

    Required, not defaulted: a body that forgets the key is a client bug, and
    silently reading it as ``false`` would turn "I meant to switch this on"
    into "I switched it off".
    """

    enable_tool_evaluation = serializers.BooleanField()


class HarnessEnvironmentEvalInputSerializer(serializers.Serializer):
    """Which stored piece of a call fills one of an eval's inputs: one of the harness's own sources, or any field or scenario column a person mapped.

    ``label`` is the only text a picker shows for a source: the frontend never
    computes which source fills a key.
    """

    key = serializers.CharField()
    source = serializers.CharField()
    label = serializers.CharField()


class HarnessEnvironmentOfferedEvalSerializer(serializers.Serializer):
    """One eval in the one list format the harness, the picker and the detail share.

    ``required_keys`` is the template's stored order and ``inputs`` is sorted
    by key; they are not aligned — pair them by ``key``. Every entry offered
    here already resolves all of its keys, so ``inputs`` is a description
    rather than a condition the caller has to check.
    """

    name = serializers.CharField()
    description = serializers.CharField(allow_blank=True)
    source = serializers.ChoiceField(choices=("system", "custom"))
    tags = serializers.ListField(child=serializers.CharField())
    required_keys = serializers.ListField(child=serializers.CharField())
    agent_type = serializers.ChoiceField(choices=(AGENT_TYPE_VOICE, AGENT_TYPE_CHAT))
    modality = serializers.ChoiceField(choices=("voice", "text", "any"))
    credits_per_run = serializers.FloatField()
    charges_judge_tokens = serializers.BooleanField()
    inputs = HarnessEnvironmentEvalInputSerializer(many=True)


class HarnessEnvironmentAvailableEvalsSerializer(serializers.Serializer):
    evaluations = HarnessEnvironmentOfferedEvalSerializer(many=True)


class HarnessEnvironmentProvenanceSerializer(serializers.Serializer):
    source = serializers.DictField()
    built_by = serializers.ChoiceField(choices=("alk", "repository"), allow_null=True)
    attempt = serializers.IntegerField(allow_null=True)
    snapshot = serializers.CharField(allow_null=True)
    digests = serializers.DictField(child=serializers.CharField(allow_null=True))
    authored_at = serializers.DictField(child=serializers.CharField(allow_null=True))


class HarnessEnvironmentAmendmentSerializer(serializers.Serializer):
    """One thing the builder changed after reading the source, and why.

    ``subject`` is blank where ALK's line carried no reason to split on, so the
    whole sentence travels as the note rather than being lost.
    """

    subject = serializers.CharField(allow_blank=True)
    note = serializers.CharField()


class HarnessEnvironmentCallActorSerializer(serializers.Serializer):
    """Someone on the person's side who is not the person, on the environment's list."""

    id = serializers.CharField()
    name = serializers.CharField()
    role = serializers.CharField(allow_blank=True)
    channel = serializers.CharField()
    # The caller persona's fields the voice is chosen from (name, gender, age_group, languages, accent and the rest),
    # kept as the runtime wrote them, any field it adds included.
    persona = JsonObjectRequestField()
    loudness_ratio = serializers.FloatField(required=False, allow_null=True)
    scenarios_count = serializers.IntegerField()


class HarnessEnvironmentCallActorTriggerSerializer(serializers.Serializer):
    """When an actor enters a call, or a scheduled action fires; only the keys its kind uses are present."""

    kind = serializers.ChoiceField(
        choices=(
            "always",
            "after_seconds",
            "on_tool",
            "contacted",
            "probability",
            "mid_journey",
            "on_keyword",
            "conditional",
        ),
        required=False,
    )
    seconds = serializers.FloatField(required=False)
    turn = serializers.IntegerField(required=False)
    tool = serializers.CharField(required=False, allow_blank=True)
    on_call = serializers.IntegerField(required=False)
    words = serializers.ListField(child=serializers.CharField(), required=False)
    p = serializers.FloatField(required=False)
    # `conditional`: the record collection watched and the field values that bring the actor in.
    collection = serializers.CharField(required=False, allow_blank=True)
    where = serializers.JSONField(required=False)


class HarnessEnvironmentScenarioActorActionSerializer(serializers.Serializer):
    """A world action the actor takes in the scenario, with its arguments as the world receives them."""

    action = serializers.CharField()
    arguments = serializers.JSONField(required=False)
    trigger = HarnessEnvironmentCallActorTriggerSerializer(required=False)


class HarnessEnvironmentScenarioCallActorSerializer(serializers.Serializer):
    """An actor a scenario includes: what they say in it and when, what they read, and the world actions they take."""

    id = serializers.CharField()
    instruction = serializers.CharField(
        allow_blank=True, required=False, trim_whitespace=False
    )
    # Kept as the runtime wrote it; any object is valid, with or without a `kind`.
    knowledge = serializers.ListField(child=JsonObjectRequestField(), required=False)
    # Absent or null: the actor comes in the way its own activation says.
    trigger = HarnessEnvironmentCallActorTriggerSerializer(
        required=False, allow_null=True
    )
    actions = HarnessEnvironmentScenarioActorActionSerializer(many=True, required=False)


class HarnessEnvironmentResolvedScenarioActorActionSerializer(
    HarnessEnvironmentScenarioActorActionSerializer
):
    """A scenario actor's world action as a scenario row shows it, with when the world takes it. Response only."""

    when_label = serializers.CharField(
        help_text="When the world takes the action, as a sentence."
    )

    class Meta:
        ref_name = "HarnessEnvironmentScenarioActorAction"


class HarnessEnvironmentResolvedScenarioActorSerializer(
    HarnessEnvironmentScenarioCallActorSerializer
):
    """A scenario's actor as a scenario row shows it: as the scenario holds it, plus who the actor is and when it
    comes in in this scenario, resolved against the cast. Response only."""

    class Meta:
        # Published under the scenario rows' existing definition name, which the resolved fields extend.
        ref_name = "HarnessEnvironmentScenarioCallActor"

    name = serializers.CharField(
        help_text="The actor's name; its id when the cast has no such actor."
    )
    role = serializers.CharField(allow_blank=True)
    channel = serializers.CharField(
        allow_null=True,
        help_text="How it speaks in the conversation; null: it acts through the world only.",
    )
    side = serializers.ChoiceField(choices=("caller", "world"))
    effective_trigger = HarnessEnvironmentCallActorTriggerSerializer(
        help_text="The trigger that runs: the scenario's own, else the actor's."
    )
    when_label = serializers.CharField(help_text="When it comes in, as a sentence.")
    timeout_label = serializers.CharField(
        allow_null=True,
        help_text="How long it waits and what then happens; null when it never waits.",
    )
    actions = HarnessEnvironmentResolvedScenarioActorActionSerializer(
        many=True, required=False
    )


class HarnessEnvironmentDoorProblemSerializer(serializers.Serializer):
    """Why a scenario cannot work on this agent as set."""

    code = serializers.ChoiceField(choices=("fault_unreachable", "actor_no_door"))
    message = serializers.CharField()


class HarnessToolArgumentSerializer(serializers.Serializer):
    name = serializers.CharField()
    type = serializers.CharField(allow_null=True)
    unit = serializers.CharField(
        allow_null=True,
        help_text="The unit or format it is written in, where the name does not say.",
    )


class HarnessToolReturnSerializer(serializers.Serializer):
    name = serializers.CharField()
    kind = serializers.CharField(
        allow_blank=True,
        help_text="Its type and, where the name does not say, unit or format.",
    )


class HarnessToolTablesSerializer(serializers.Serializer):
    """The tables a tool reads and changes, where the contract records them."""

    recorded = serializers.BooleanField()
    read = serializers.ListField(child=serializers.CharField())
    changed = serializers.ListField(child=serializers.CharField())

    def get_fields(self) -> dict[str, serializers.Field]:
        return {
            **super().get_fields(),
            "label": serializers.CharField(help_text="What the Changes line says."),
        }


class HarnessEnvironmentToolStatusSerializer(serializers.Serializer):
    """One contract tool as the Tools card shows it, from the contract's own reading of it."""

    tool = serializers.CharField()
    status = serializers.ChoiceField(
        choices=("simulated", "in_process", "unsupported", "unknown"),
        help_text="simulated: the world's door stands in front of it; in_process: it runs inside the agent; "
        "unsupported: it reaches a system the test world has no copy of; unknown: the build read nothing about it.",
    )
    status_label = serializers.CharField(
        help_text="What a test does with it, in words."
    )
    status_reason = serializers.CharField(
        allow_null=True,
        help_text="Why a test cannot run an unsupported tool, as the run's own refusal says it; null otherwise.",
    )
    reset_between_trials = serializers.BooleanField(
        allow_null=True,
        help_text="Restored to the seeded records before each trial; null when not known.",
    )
    runs_at = serializers.CharField(
        allow_null=True,
        help_text="The agent's callable or the tools service's method and route.",
    )
    writes = serializers.BooleanField(
        help_text="It changes records, as the world's door decides: its declared effect, else its HTTP method."
    )
    description = serializers.CharField(allow_blank=True)
    arguments = HarnessToolArgumentSerializer(many=True)
    returns = HarnessToolReturnSerializer(
        many=True, help_text="Empty when the contract did not record what it returns."
    )
    requires = serializers.ListField(
        child=serializers.CharField(),
        help_text="Tools that must have run before it stops refusing.",
    )
    refuses = serializers.CharField(
        allow_blank=True, help_text="When it refuses, in the contract's words."
    )
    effect = serializers.ChoiceField(
        choices=("read", "write", "unknown"),
        help_text="What its entrypoint declares; unknown when it declares neither.",
    )
    effects = serializers.CharField(
        allow_blank=True,
        help_text="What a call changes or clears, in the contract's words.",
    )
    tables = HarnessToolTablesSerializer()


class HarnessApprovalOptionSerializer(_Labelled):
    value = serializers.CharField()


class HarnessFaultModeSerializer(serializers.Serializer):
    mode = serializers.ChoiceField(choices=FAULT_MODES)
    hint = serializers.CharField(help_text="The line shown under the choice.")
    approval_options = HarnessApprovalOptionSerializer(
        many=True,
        required=False,
        help_text="On approval_required only: the world actions the cast's actors hold that the approval could be "
        "recorded by, each with who holds it.",
    )

    def get_fields(self) -> dict[str, serializers.Field]:
        # `label` and `fields` are names Serializer attributes have, so these fields are added here.
        fields = super().get_fields()
        fields["label"] = serializers.CharField()
        fields["fields"] = serializers.ListField(
            child=serializers.ChoiceField(
                choices=(
                    "on_call",
                    "delay_seconds",
                    "status",
                    "message",
                    "approval",
                    "approved_when",
                )
            ),
            help_text="The fault values this mode reads; the editor asks for these and no others.",
        )
        return fields


class HarnessFaultDefaultsSerializer(serializers.Serializer):
    """What a fault that leaves a value out gets."""

    on_call = serializers.IntegerField()
    delay_seconds = serializers.FloatField()
    status = serializers.IntegerField()


class HarnessFaultRangeSerializer(serializers.Serializer):
    min = serializers.FloatField()
    max = serializers.FloatField()


class HarnessFaultFloorSerializer(serializers.Serializer):
    min = serializers.IntegerField()


class HarnessFaultLengthSerializer(serializers.Serializer):
    max_length = serializers.IntegerField()


class HarnessFaultBoundsSerializer(serializers.Serializer):
    on_call = HarnessFaultFloorSerializer()
    delay_seconds = HarnessFaultRangeSerializer()
    status = HarnessFaultRangeSerializer()
    message = HarnessFaultLengthSerializer()


class HarnessEnvironmentFaultTargetSerializer(serializers.Serializer):
    """A contract tool a scenario's fault can name."""

    tool = serializers.CharField()
    action = serializers.CharField(
        help_text="The world action a fault on this tool names."
    )
    reachable = serializers.BooleanField(
        help_text="The world's door stands in front of it, so a fault applies."
    )
    writes = serializers.BooleanField(
        help_text="It changes records, so a timeout after the change commits means something."
    )
    modes = HarnessFaultModeSerializer(
        many=True,
        help_text="The modes the world can apply to it: none when it cannot hold its calls; an answer lost after the "
        "change only when it writes.",
    )
    defaults = HarnessFaultDefaultsSerializer()
    bounds = HarnessFaultBoundsSerializer()


class HarnessEnvironmentCatalogueSubGoalSerializer(serializers.Serializer):
    """One catalogue entry, carrying the code that settles it where there is any.

    ``check`` is the Python ALK wrote against what the run left behind. A judged
    sub-goal has none and carries ``claim`` instead, so exactly one of the two is
    populated.
    """

    name = serializers.CharField()
    what = serializers.CharField(allow_blank=True)
    kind = serializers.ChoiceField(choices=("checkpoint", "judge"))
    claim = serializers.CharField(allow_blank=True)
    check = serializers.CharField(allow_blank=True)


class HarnessEnvironmentEndConditionsSerializer(serializers.Serializer):
    """What stops a run, and the vocabulary its calls end with.

    Both limits are null before authoring: a turn budget lives on the scenarios,
    and neither is invented from a default the run would not actually use.
    """

    max_turns = serializers.IntegerField(allow_null=True)
    max_duration_seconds = serializers.IntegerField(allow_null=True)
    clock = serializers.ChoiceField(choices=("real-time", "stepped"))
    ended_reasons = serializers.ListField(child=serializers.CharField())


class HarnessEnvironmentWorkerSerializer(serializers.Serializer):
    id = serializers.CharField()
    name = serializers.CharField(required=False, allow_blank=True)
    display_name = serializers.CharField(
        help_text="What the cards call it: its name, else its id."
    )
    tools = serializers.ListField(child=serializers.CharField())
    # The tools it may use when narrower than what its code holds; empty means all of `tools`.
    allowed_tools = serializers.ListField(child=serializers.CharField())
    # The tools the cards list: its allowed tools when narrower, else every tool its code holds.
    tools_shown = serializers.ListField(child=serializers.CharField())
    # `not_declared` when its contract entry does not say how it reaches the world.
    binding = serializers.ChoiceField(choices=get_args(WorkerBinding))
    # How the world holds it to its limits, and the words the cards show: with no trial, a separate worker's is not
    # known and a shared one's is not enforced.
    enforcement = serializers.ChoiceField(choices=get_args(WorkerEnforcement))
    enforcement_label = serializers.CharField()
    address_env = serializers.CharField(required=False, allow_blank=True)
    # The process that runs it alone, when the app serves it as its own service.
    process = serializers.CharField(required=False, allow_blank=True)


class HarnessEnvironmentContractSerializer(serializers.Serializer):
    """The agent contract as ALK wrote it, passed through, plus how it was built.

    Every field is optional because older jobs predate some of them; absent means
    the reader did not record it, not that it is empty.
    """

    agent = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    one_liner = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    modality = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    call_direction = serializers.CharField(
        required=False, allow_null=True, allow_blank=True
    )
    system_prompt_excerpt = serializers.CharField(
        required=False, allow_null=True, allow_blank=True
    )
    tools = serializers.ListField(
        child=serializers.JSONField(), required=False, allow_null=True
    )
    real_use_cases = serializers.ListField(
        child=serializers.CharField(), required=False, allow_null=True
    )
    hard_constraints = serializers.ListField(
        child=serializers.CharField(), required=False, allow_null=True
    )
    runtime = serializers.JSONField(required=False, allow_null=True)
    dependencies = serializers.ListField(
        child=serializers.JSONField(), required=False, allow_null=True
    )
    runtime_dependencies = serializers.ListField(
        child=serializers.JSONField(), required=False, allow_null=True
    )
    implementation = serializers.CharField(
        required=False, allow_null=True, allow_blank=True
    )
    tool_entrypoints = serializers.ListField(
        child=serializers.JSONField(), required=False, allow_null=True
    )
    workers = HarnessEnvironmentWorkerSerializer(
        many=True,
        required=False,
        allow_null=True,
        help_text="The workers the app's source declares; a run shows which of them took part.",
    )
    data_store = serializers.JSONField(required=False, allow_null=True)
    open_questions = serializers.ListField(
        child=serializers.CharField(), required=False, allow_null=True
    )
    amendments = HarnessEnvironmentAmendmentSerializer(many=True)
    sub_goals = HarnessEnvironmentCatalogueSubGoalSerializer(many=True)
    call_actors = HarnessEnvironmentCallActorSerializer(many=True)
    door_reach = serializers.DictField(
        child=serializers.BooleanField(),
        required=False,
        help_text="Per tool: whether the world's door stands in front of it, so actors can take it and faults apply.",
    )
    production_only = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        help_text="Tools that reach a system the environment has no copy of: Unsupported for this controlled test.",
    )
    tool_status = HarnessEnvironmentToolStatusSerializer(many=True)
    from_draft = serializers.BooleanField(
        required=False,
        help_text="Whether this is an open draft's contract, which no run uses until the draft is saved.",
    )
    fault_targets = HarnessEnvironmentFaultTargetSerializer(many=True)
    end_conditions = HarnessEnvironmentEndConditionsSerializer()
    notes = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    chosen_evals = serializers.ListField(
        child=serializers.CharField(), required=False, allow_null=True
    )
    provenance = HarnessEnvironmentProvenanceSerializer()


class HarnessEnvironmentPersonaSerializer(serializers.Serializer):
    """One distinct caller; ALK's persona fields plus the scenarios it appears in."""

    name = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    scenario_keys = serializers.ListField(child=serializers.CharField())

    def to_representation(self, instance):
        return dict(instance)


class HarnessEnvironmentStoreTableSerializer(serializers.Serializer):
    name = serializers.CharField()
    rows = serializers.IntegerField()


class HarnessEnvironmentStoreSerializer(serializers.Serializer):
    """One built store and the tables it was seeded with.

    Counts come from the sealed build output, so a store appears only once the
    world has been built. ``tables`` is populated for postgres stores; another
    engine records no per-table counts and is left out rather than reported as
    empty.
    """

    capability = serializers.CharField(allow_blank=True)
    engine = serializers.CharField(allow_blank=True)
    strategy = serializers.CharField(allow_blank=True)
    tables = HarnessEnvironmentStoreTableSerializer(many=True)
    total_rows = serializers.IntegerField()


class HarnessEnvironmentTableFieldSerializer(serializers.Serializer):
    name = serializers.CharField()
    type = serializers.CharField()


class HarnessEnvironmentWorldTableSerializer(serializers.Serializer):
    """One of the world's own tables: the harness's tables are never listed."""

    name = serializers.CharField()
    rows = serializers.IntegerField()
    note = serializers.CharField(
        allow_null=True, help_text="The store it lives in: capability and engine."
    )

    def get_fields(self) -> dict[str, serializers.Field]:
        # `fields` is the name of the Serializer attribute holding them, so this field is added here.
        return {
            **super().get_fields(),
            "fields": HarnessEnvironmentTableFieldSerializer(
                many=True,
                help_text="Its fields as the contract's data schema gives them; empty when it gives none.",
            ),
        }


class HarnessEnvironmentWorldSerializer(serializers.Serializer):
    runtime = serializers.DictField()
    personas = HarnessEnvironmentPersonaSerializer(many=True)
    stores = HarnessEnvironmentStoreSerializer(many=True)
    tables = HarnessEnvironmentWorldTableSerializer(many=True)
    total_rows = serializers.IntegerField(help_text="The seeded rows across `tables`.")


class HarnessEnvironmentSubGoalSerializer(serializers.Serializer):
    name = serializers.CharField()
    what = serializers.CharField(required=False, allow_blank=True)
    kind = serializers.ChoiceField(choices=("checkpoint", "judge"), required=False)
    claim = serializers.CharField(required=False, allow_blank=True)


class HarnessEnvironmentScenarioSerializer(serializers.Serializer):
    scenario_key = serializers.CharField()
    scenario_id = serializers.UUIDField()
    name = serializers.CharField(allow_blank=True)
    instruction = serializers.CharField(allow_blank=True)
    use_case = serializers.CharField(allow_null=True)
    branch = serializers.CharField(allow_null=True)
    tests = serializers.CharField(allow_null=True)
    fixture = serializers.DictField(allow_null=True)
    steps = serializers.IntegerField(allow_null=True)
    sub_goals = HarnessEnvironmentSubGoalSerializer(many=True)
    actors = HarnessEnvironmentResolvedScenarioActorSerializer(many=True)
    door_problem = HarnessEnvironmentDoorProblemSerializer(allow_null=True)
    persona = serializers.DictField(allow_null=True)
    situation = serializers.CharField(allow_null=True)
    outcome = serializers.CharField(allow_null=True)
    status = serializers.CharField()
    call_execution_id = serializers.UUIDField(allow_null=True)


class HarnessEnvironmentSelectedEvalSerializer(HarnessEnvironmentOfferedEvalSerializer):
    """One eval this environment is graded by: the same entry plus its id.

    Only the configs that carry a mapping are listed; the rows ingestion
    creates for the harness's own result columns are bound to the run but were
    never selected. ``runnable`` and ``editable`` are therefore always true
    here; both are kept so the frontend reads one rule for every eval list.
    """

    id = serializers.UUIDField()
    runnable = serializers.BooleanField()
    editable = serializers.BooleanField()


class HarnessEnvironmentResultSerializer(serializers.Serializer):
    scenario_key = serializers.CharField()
    status = serializers.CharField()
    attempt_number = serializers.IntegerField()
    evaluations = serializers.ListField(child=serializers.JSONField())
    coverage = serializers.DictField()


class HarnessEnvironmentEvaluationsSerializer(serializers.Serializer):
    """What will judge the runs, and what each run was judged as."""

    selected = HarnessEnvironmentSelectedEvalSerializer(many=True)
    results = HarnessEnvironmentResultSerializer(many=True)


class HarnessEnvironmentCredentialFileSerializer(serializers.Serializer):
    """One uploaded credential file, named by where it is mounted.

    The filename is not carried: on a managed sandbox the file crosses as an
    encrypted JSON secret rather than a stored file, so the environment variable
    it mounts under is the only name that survives.
    """

    environment_name = serializers.CharField()


class HarnessEnvironmentAgentSettingsSerializer(serializers.Serializer):
    """How the agent was reached, with every credential reduced to its name.

    ``secrets`` and ``credential_files`` partition ``secret_refs`` into typed
    values and uploaded files, which are replaced differently: a file cannot be
    shown or re-entered as text. ``secret_refs`` keeps every alias for callers
    written before the split.
    """

    connector = serializers.CharField(allow_null=True)
    mode = serializers.CharField(allow_null=True)
    call_direction = serializers.CharField(allow_null=True)
    config = serializers.DictField()
    secret_refs = serializers.ListField(child=serializers.CharField())
    secrets = serializers.ListField(child=serializers.CharField())
    credential_files = HarnessEnvironmentCredentialFileSerializer(many=True)


class HarnessEnvironmentSettingsSerializer(serializers.Serializer):
    """How this environment runs: the request it was built from, plus one switch.

    Everything but ``enable_tool_evaluation`` is a record of how the
    environment was built and cannot be edited; secrets are names only.
    ``enable_tool_evaluation`` is the tool-call judge's switch, written by
    ``PUT evaluations/tool-call/``. Never null: an environment with no run
    test reads ``false``.
    """

    schema_version = serializers.CharField(allow_null=True)
    source = serializers.DictField()
    agent = HarnessEnvironmentAgentSettingsSerializer()
    runtime = serializers.DictField(allow_null=True)
    security = serializers.DictField(allow_null=True)
    artifacts = serializers.DictField(allow_null=True)
    scenario_count = serializers.IntegerField(allow_null=True)
    seed = serializers.IntegerField(allow_null=True)
    enable_tool_evaluation = serializers.BooleanField()


class HarnessEnvironmentDetailSerializer(serializers.Serializer):
    """One environment. ``contract`` and ``world`` are null until authoring finishes."""

    id = serializers.UUIDField()
    overview = HarnessEnvironmentOverviewSerializer()
    contract = HarnessEnvironmentContractSerializer(allow_null=True)
    world = HarnessEnvironmentWorldSerializer(allow_null=True)
    scenarios = HarnessEnvironmentScenarioSerializer(many=True)
    evaluations = HarnessEnvironmentEvaluationsSerializer()
    settings = HarnessEnvironmentSettingsSerializer()
    actors_tab = serializers.ChoiceField(
        choices=ACTORS_TABS,
        allow_null=True,
        help_text="edit: the cast is edited on the Actors tab; list: its actors are listed read-only; null: no tab.",
    )


class HarnessScenarioFaultSerializer(serializers.Serializer):
    """One test condition as the harness applies it: what the world does to one of the agent's calls in the scenario.
    A value its mode never reads is null; a test condition stored in a shape the harness cannot load shows its
    action and mode as stored, with null values."""

    action = serializers.CharField(allow_blank=True)
    tool = serializers.CharField(
        allow_null=True,
        help_text="The contract tool it names; null when it names none.",
    )
    mode = serializers.CharField(
        allow_blank=True, help_text="One of the fault modes, as stored."
    )
    mode_label = serializers.CharField(allow_blank=True)
    on_call = serializers.IntegerField(allow_null=True)
    delay_seconds = serializers.FloatField(allow_null=True, help_text="For a timeout.")
    status = serializers.IntegerField(allow_null=True, help_text="For an error.")
    message = serializers.CharField(allow_blank=True, help_text="For an error.")
    approval = serializers.CharField(
        allow_null=True,
        help_text="For approval_required: the world action that records the decision.",
    )
    approved_when = serializers.DictField(
        child=serializers.JSONField(),
        help_text="For approval_required: the arguments that action must carry to approve; empty otherwise.",
    )
    line = serializers.CharField(
        help_text="The test condition as one line: its tool (else its action), what happens, on which call, and the "
        "value its mode reads."
    )


class HarnessScenarioPersonaSerializer(serializers.Serializer):
    """The caller as the scenario keeps it; every field is optional because older suites carry fewer."""

    name = serializers.CharField(required=False, allow_blank=True)
    gender = serializers.CharField(required=False, allow_blank=True)
    age_group = serializers.CharField(required=False, allow_blank=True)
    occupation = serializers.CharField(required=False, allow_blank=True)
    location = serializers.CharField(required=False, allow_blank=True)
    personality = serializers.CharField(required=False, allow_blank=True)
    communication_style = serializers.CharField(required=False, allow_blank=True)
    initial_message = serializers.CharField(required=False, allow_blank=True)
    languages = serializers.ListField(child=serializers.CharField(), required=False)
    accent = serializers.CharField(required=False, allow_blank=True)
    multilingual = serializers.BooleanField(required=False)
    keywords = serializers.ListField(child=serializers.CharField(), required=False)
    # Free-form by the scenario schema: the builder may put any keys here.
    metadata = JsonObjectRequestField(required=False)
    scripted_caller = JsonObjectRequestField(required=False, allow_null=True)


class HarnessScenarioCoverageSerializer(serializers.Serializer):
    """The level the scenario holds on each coverage axis; an axis the suite does not use is absent."""

    task = serializers.CharField(required=False, allow_null=True)
    counterparty = serializers.CharField(required=False, allow_null=True)
    disposition = serializers.CharField(required=False, allow_null=True)
    interface = serializers.CharField(required=False, allow_null=True)
    interaction = serializers.CharField(required=False, allow_null=True)
    overlay = serializers.CharField(required=False, allow_null=True)
    overlay_vector = serializers.CharField(required=False, allow_null=True)
    overlay_intensity = serializers.CharField(required=False, allow_null=True)


class HarnessScenarioRowSerializer(serializers.Serializer):
    """One authored scenario on the Scenarios list."""

    id = serializers.UUIDField()
    scenario_id = serializers.UUIDField(allow_null=True)
    scenario_key = serializers.CharField()
    number = serializers.IntegerField(allow_null=True)
    name = serializers.CharField(allow_blank=True)
    use_case = serializers.CharField(allow_blank=True, allow_null=True)
    instruction = serializers.CharField(allow_blank=True, allow_null=True)
    branch = serializers.CharField(allow_blank=True, allow_null=True)
    tests = serializers.CharField(allow_blank=True, allow_null=True)
    persona = HarnessScenarioPersonaSerializer()
    coverage = HarnessScenarioCoverageSerializer()
    sub_goals = serializers.ListField(
        child=serializers.CharField(), help_text="The names of the sub-goals it checks."
    )
    actors = HarnessEnvironmentResolvedScenarioActorSerializer(many=True)
    faults = HarnessScenarioFaultSerializer(many=True)
    door_problem = HarnessEnvironmentDoorProblemSerializer(allow_null=True)
    keywords = serializers.ListField(child=serializers.CharField())
    background_noise = serializers.CharField(allow_blank=True, allow_null=True)
    max_turns = serializers.IntegerField(allow_null=True)
    status = serializers.CharField(
        help_text="Where it got to: `authored` before any call ran, else its call's status."
    )
    call_execution_id = serializers.UUIDField(allow_null=True)
    group = serializers.CharField(
        required=False, help_text="Its section on this page, when the page is grouped."
    )
    draft_change = serializers.ChoiceField(
        choices=("added", "changed", "unchanged"),
        required=False,
        allow_null=True,
        help_text="Present while the list shows unsaved changes.",
    )


class HarnessScenarioGroupSerializer(serializers.Serializer):
    name = serializers.CharField()
    count = serializers.IntegerField(help_text="Its scenarios on this page.")
    total = serializers.IntegerField(
        required=False, help_text="Its scenarios in the whole filtered suite."
    )


class HarnessScenarioFieldSerializer(_Labelled):
    """A field the list filters on; an enum carries its values counted over the whole filtered suite."""

    value = serializers.CharField()
    type = serializers.ChoiceField(choices=("string", "enum"))
    category = serializers.CharField()
    choices = serializers.ListField(child=serializers.CharField(), required=False)
    counts = serializers.DictField(
        child=serializers.IntegerField(),
        required=False,
        help_text="Scenarios per value, keyed by the value.",
    )
    choice_labels = serializers.DictField(
        child=serializers.CharField(),
        required=False,
        help_text="The name to show for each value, keyed by the value (actors only).",
    )


class HarnessScenarioEditingSerializer(serializers.Serializer):
    """What an amend will take, and what needs a re-proof."""

    editable_fields = serializers.ListField(child=serializers.CharField())
    persona_fields = serializers.ListField(child=serializers.CharField())
    persona_choices = serializers.DictField(
        child=serializers.ListField(child=serializers.CharField()),
        help_text="The values each persona field may take, keyed by the field.",
    )
    noise_choices = serializers.ListField(child=serializers.CharField())
    rework_fields = serializers.ListField(child=serializers.CharField())


class HarnessScenarioGroupingSerializer(_Labelled):
    value = serializers.CharField(allow_blank=True)


class HarnessScenarioDraftRefSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    revision = serializers.IntegerField()


class HarnessScenarioListResponseSerializer(serializers.Serializer):
    """One page of an environment's authored scenarios, with what the list filters and groups by."""

    count = serializers.IntegerField()
    next = serializers.CharField(allow_null=True)
    previous = serializers.CharField(allow_null=True)
    total_pages = serializers.IntegerField()
    current_page = serializers.IntegerField()
    results = HarnessScenarioRowSerializer(many=True)
    draft = HarnessScenarioDraftRefSerializer(
        required=False,
        help_text="The unsaved changes the page shows, when it shows them.",
    )
    groups = HarnessScenarioGroupSerializer(many=True)
    group_by = serializers.CharField(allow_blank=True)
    scenario_editing = HarnessScenarioEditingSerializer()
    groupings = HarnessScenarioGroupingSerializer(many=True)
    level_labels = serializers.DictField(
        child=serializers.CharField(),
        help_text="The name to show for each coverage level, sub-goal and noise bed on the page, keyed by it.",
    )

    def get_fields(self):
        # `fields` names the serializer's own field map, so the response key is declared here.
        declared = super().get_fields()
        declared["fields"] = HarnessScenarioFieldSerializer(many=True)
        return declared


class HarnessVersionPersonSerializer(serializers.Serializer):
    id = serializers.CharField()
    name = serializers.CharField(allow_blank=True)


class HarnessVersionSerializer(serializers.Serializer):
    """One agent, environment or suite version, as the version lists show it."""

    id = serializers.UUIDField()
    label = serializers.CharField(allow_null=True)
    status = serializers.ChoiceField(choices=("draft", "active", "discarded"))
    is_default = serializers.BooleanField()
    created_at = serializers.DateTimeField()
    created_by = HarnessVersionPersonSerializer(allow_null=True)
    origin = serializers.CharField(allow_null=True)
    summary = serializers.CharField(allow_blank=True)
    hash = serializers.CharField(allow_null=True)
    parent_id = serializers.UUIDField(allow_null=True)
    derived_from_agent_version_id = serializers.UUIDField(allow_null=True)
    scenario_count = serializers.IntegerField(allow_null=True)
    source = serializers.JSONField(
        allow_null=True, help_text="An agent version's non-secret source facts."
    )
    prompt = serializers.CharField(
        allow_null=True,
        allow_blank=True,
        help_text="The default agent version's prompt; null on every other item.",
    )
    preflight_status = serializers.CharField(allow_null=True)
    preflight_report = serializers.JSONField(
        allow_null=True,
        help_text="An agent version's last check: check statuses and names only.",
    )
    preflighted_at = serializers.DateTimeField(allow_null=True)
    check_started_at = serializers.DateTimeField(
        allow_null=True,
        help_text="When an agent version's latest check was asked for.",
    )
    credential_names = serializers.ListField(
        child=serializers.CharField(),
        allow_null=True,
        help_text="An agent version's credential names, provider keys included.",
    )
    required_variable_names = serializers.ListField(
        child=serializers.CharField(),
        allow_null=True,
        help_text="The variables an agent version's source reads, when known.",
    )
    activation_outcome = serializers.CharField(
        allow_null=True,
        help_text=(
            "Why a passed agent version is or isn't this environment's default, as"
            " last recorded."
        ),
    )
    requested_here = serializers.BooleanField(
        allow_null=True,
        help_text="Whether this environment asked for an agent version.",
    )
    pickable = serializers.BooleanField(
        allow_null=True,
        help_text=(
            "Whether an agent version can be picked for a run of this environment"
            " or made its default."
        ),
    )
    discardable = serializers.BooleanField(
        allow_null=True,
        help_text="Whether this environment can discard an agent version.",
    )


class HarnessAgentCredentialsResponseSerializer(serializers.Serializer):
    """The agent version a credential change made; its check runs next."""

    agent_version = HarnessVersionSerializer()


class HarnessAgentPayloadChangeSerializer(StrictInputSerializer):
    config = HarnessAgentConfigChangeSerializer()


class HarnessAgentVersionCreateSerializer(serializers.Serializer):
    """One change to an environment's agent: a new upload, or its prompt and settings."""

    archive_artifact_id = serializers.UUIDField(
        required=False, help_text="A folder uploaded through source-upload."
    )
    prompt = serializers.CharField(
        required=False,
        max_length=TARGET_SYSTEM_PROMPT_MAX_CHARS,
        trim_whitespace=True,
        allow_blank=False,
    )
    agent_payload = HarnessAgentPayloadChangeSerializer(required=False)
    make_default = serializers.BooleanField(
        required=False,
        help_text=(
            "False only checks the new version: once it passes, a run can pick it"
            " and a move makes it the default. Omitted, it is true, except for a"
            " coding agent key, whose push is never the default."
        ),
    )

    def validate(self, attrs):
        given = [
            name
            for name in ("archive_artifact_id", "prompt", "agent_payload")
            if name in attrs
        ]
        if not given:
            raise serializers.ValidationError(
                "Give archive_artifact_id, prompt or agent_payload."
            )
        if "archive_artifact_id" in attrs and len(given) > 1:
            raise serializers.ValidationError(
                {"archive_artifact_id": "An upload is a change of its own."}
            )
        if "agent_payload" in attrs and not attrs["agent_payload"]["config"]:
            raise serializers.ValidationError({"agent_payload": "Nothing to change."})
        return attrs


class HarnessAgentPolicySerializer(serializers.Serializer):
    """Whether the environment follows its agent's production version or is pinned
    to one; a pin may name the version to move to."""

    policy = serializers.ChoiceField(choices=("follow", "pinned"))
    version_id = serializers.UUIDField(required=False)

    def validate(self, attrs):
        if attrs.get("version_id") is not None and attrs["policy"] != "pinned":
            raise serializers.ValidationError(
                {"version_id": ["only a pin names a version"]}
            )
        return attrs


class HarnessAgentPolicyResponseSerializer(serializers.Serializer):
    policy = serializers.ChoiceField(choices=("follow", "pinned"), read_only=True)
    agent_version = HarnessVersionSerializer(read_only=True, allow_null=True)


class HarnessVersionsSerializer(serializers.Serializer):
    """Every version of an environment's three pieces, newest first."""

    agent = HarnessVersionSerializer(many=True)
    environment = HarnessVersionSerializer(many=True)
    suite = HarnessVersionSerializer(many=True)


class HarnessDraftItemSerializer(HarnessVersionSerializer):
    """An unsaved draft of the environment or the suite, as the Save bar shows it."""

    stale = serializers.BooleanField(
        help_text="Whether the default moved since the draft started; Save refuses it."
    )
    revision = serializers.IntegerField(help_text="Bumped on every edit to the draft.")
    last_edited_by = HarnessVersionPersonSerializer(allow_null=True)
    updated_at = serializers.DateTimeField()
    parent_label = serializers.CharField(
        allow_null=True, help_text="The version the draft started from."
    )
    default_label = serializers.CharField(
        allow_null=True, help_text="The piece's default now."
    )
    changes = serializers.DictField(
        child=serializers.IntegerField(),
        help_text=(
            "Counts against the parent: added, removed and changed for the suite;"
            " actors, tools, rules, sub_goals and world_files for the environment."
        ),
    )


class HarnessDraftsSerializer(serializers.Serializer):
    """The environment's open drafts, and drafts an update set aside."""

    environment = HarnessDraftItemSerializer(allow_null=True)
    suite = HarnessDraftItemSerializer(allow_null=True)
    set_aside = HarnessDraftItemSerializer(many=True)


class HarnessDraftSaveItemSerializer(StrictInputSerializer):
    piece = serializers.ChoiceField(choices=("environment", "suite"))
    expected_parent_id = serializers.UUIDField(
        allow_null=True, help_text="The draft's parent the caller saw."
    )
    expected_revision = serializers.IntegerField(
        required=False, min_value=0, help_text="The draft's revision the caller saw."
    )


class HarnessDraftSaveSerializer(serializers.Serializer):
    drafts = HarnessDraftSaveItemSerializer(many=True, min_length=1, max_length=2)
    idempotency_key = serializers.RegexField(
        r"^[A-Za-z0-9_-]+$",
        max_length=128,
        help_text="Repeating a Save with the same key repeats its answer.",
    )

    def validate_drafts(self, value):
        pieces = [one["piece"] for one in value]
        if len(set(pieces)) != len(pieces):
            raise serializers.ValidationError("Each piece may be saved once.")
        return value


class HarnessDraftSaveResponseSerializer(serializers.Serializer):
    saved = serializers.DictField(
        child=HarnessVersionSerializer(),
        help_text="Each saved piece's new default version, by piece.",
    )
    versions = HarnessVersionsSerializer()


class HarnessDefaultMoveSerializer(serializers.Serializer):
    expected_default_id = serializers.UUIDField(
        allow_null=True,
        help_text="The default the caller saw; a newer default refuses the move.",
    )


class HarnessVersionDiffQuerySerializer(serializers.Serializer):
    against = serializers.UUIDField(required=False)


class HarnessVersionDiffSerializer(serializers.Serializer):
    """What changed, against the version's parent or the one named by ``against``."""

    summary = serializers.CharField(allow_blank=True)
    diff = serializers.JSONField(allow_null=True)
    includes_draft_edits = serializers.BooleanField(
        required=False,
        help_text="A rebuild made someone's unsaved edits live with this version.",
    )
    snapshot_diff = serializers.JSONField(
        required=False,
        allow_null=True,
        help_text="Those unsaved edits, against the default they started from.",
    )


RUN_PLAN_MAX_SCENARIOS = 1000


class HarnessRunPlanQuerySerializer(serializers.Serializer):
    agent_version_id = serializers.UUIDField(required=False)
    environment_version_id = serializers.UUIDField(required=False)
    suite_version_id = serializers.UUIDField(required=False)
    scenario_keys = serializers.CharField(
        required=False,
        allow_blank=True,
        help_text="The scenarios the run would select, comma-separated; absent or blank selects every scenario.",
    )

    def validate_scenario_keys(self, value: str) -> list[str] | None:
        keys = [key.strip() for key in value.split(",") if key.strip()]
        if not keys:
            return None
        if len(keys) > RUN_PLAN_MAX_SCENARIOS:
            raise serializers.ValidationError(
                f"Select at most {RUN_PLAN_MAX_SCENARIOS} scenarios."
            )
        return keys


class HarnessBlockedScenarioSerializer(serializers.Serializer):
    key = serializers.CharField()
    name = serializers.CharField()


class HarnessBlockedToolSerializer(serializers.Serializer):
    """A tool that reaches production, blocked in a controlled test, with the scenarios that cannot pass without it."""

    tool = serializers.CharField()
    scenarios = HarnessBlockedScenarioSerializer(many=True)


class HarnessRunPlanSerializer(serializers.Serializer):
    """What a run of the picked versions would use and should be warned about."""

    versions = HarnessRunPinnedVersionsSerializer()
    warnings = HarnessRunWarningSerializer(many=True)
    bundle_cached = serializers.BooleanField(
        allow_null=True,
        help_text=(
            "Whether the run replays a cached bundle (true) or prepares one (false);"
            " null when it runs without one, as the defaults do before their bundle"
            " is cached."
        ),
    )
    blocked_tools = HarnessBlockedToolSerializer(
        many=True,
        help_text="Tools blocked in this controlled test because they reach production, each with the selected "
        "scenarios that need it; empty when none is. A run whose every selected scenario needs one is refused.",
    )
    runnable = serializers.BooleanField(
        help_text="False when every selected scenario needs a blocked tool: the run would be refused."
    )
    blocked_code = serializers.ChoiceField(
        choices=("production_only",),
        allow_null=True,
        help_text="Why the run would be refused; null when it is runnable.",
    )
    held_back = serializers.IntegerField(
        min_value=0,
        help_text="Selected scenarios that need a blocked tool, when the run would be refused; 0 when it runs.",
    )


class HarnessCompareQuerySerializer(serializers.Serializer):
    execution_id = serializers.UUIDField()
    against_execution_id = serializers.UUIDField(
        required=False,
        help_text="The run to compare against; the previous completed run if absent.",
    )


class HarnessCompareSideSerializer(serializers.Serializer):
    passed = serializers.IntegerField()
    trials = serializers.IntegerField(help_text="Trials a verdict was reached on.")
    undecided = serializers.IntegerField(
        help_text="Trials that ran to an end but a sub-goal check was undecided."
    )
    pass_rate = serializers.FloatField(allow_null=True)
    eval_pass_rate = serializers.FloatField(
        allow_null=True, help_text="Context only; never decides the verdict."
    )


class HarnessCompareIntervalSerializer(serializers.Serializer):
    low = serializers.FloatField()
    high = serializers.FloatField()


class HarnessComparePairSerializer(serializers.Serializer):
    scenario_key = serializers.CharField()
    scenario = serializers.CharField()
    band = serializers.ChoiceField(
        choices=("paired", "criteria_changed", "new", "not_applicable")
    )
    verdict = serializers.ChoiceField(
        choices=("improved", "regressed", "no_meaningful_change", "inconclusive"),
        allow_null=True,
    )
    difference = serializers.FloatField(
        allow_null=True, help_text="Pass-rate change in percentage points."
    )
    interval = HarnessCompareIntervalSerializer(
        allow_null=True, help_text="Newcombe 95% interval of the difference."
    )
    current = HarnessCompareSideSerializer(allow_null=True)
    against = HarnessCompareSideSerializer(allow_null=True)
    changed_sub_goals = serializers.ListField(child=serializers.CharField())
    reason = serializers.CharField(allow_null=True)
    removed_in = RunVersionLabelSerializer(allow_null=True)


class HarnessCompareSummarySerializer(serializers.Serializer):
    paired = serializers.IntegerField()
    improved = serializers.IntegerField()
    regressed = serializers.IntegerField()
    no_meaningful_change = serializers.IntegerField()
    inconclusive = serializers.IntegerField()
    criteria_changed = serializers.IntegerField()
    new = serializers.IntegerField()
    not_applicable = serializers.IntegerField()
    current_pass_rate = serializers.FloatField(allow_null=True)
    against_pass_rate = serializers.FloatField(allow_null=True)
    margin = serializers.FloatField()
    excluded_not_comparable = serializers.IntegerField(
        help_text="Trials of either run left out because they did not start from the seeded records."
    )


class HarnessCompareVersionsSerializer(serializers.Serializer):
    current = HarnessRunPinnedVersionsSerializer()
    against = HarnessRunPinnedVersionsSerializer(allow_null=True)


class HarnessCompareSerializer(serializers.Serializer):
    """Two runs of one environment, scenario by scenario."""

    execution_id = serializers.UUIDField()
    against_execution_id = serializers.UUIDField(allow_null=True)
    pairs = HarnessComparePairSerializer(many=True)
    summary = HarnessCompareSummarySerializer()
    versions = HarnessCompareVersionsSerializer()


class HarnessSuiteMemberSerializer(serializers.Serializer):
    scenario_key = serializers.CharField()
    number = serializers.IntegerField()
    name = serializers.CharField(allow_blank=True)
    use_case = serializers.CharField(allow_blank=True)
    # The Error Feed issue whose traces Run Epoch drafted this scenario from; null for
    # any other scenario.
    issue_cluster_id = serializers.CharField(allow_null=True)
    issue_project_id = serializers.UUIDField(allow_null=True)


class HarnessVerifyCreateSerializer(serializers.Serializer):
    agent_version_id = serializers.UUIDField(
        help_text=(
            "The agent version to verify. Its completed verify is final; after one"
            " that failed or was canceled, a new one starts."
        )
    )
    trials = serializers.IntegerField(min_value=1, max_value=20, default=3)


class HarnessVerifyCreateResponseSerializer(serializers.Serializer):
    verify_id = serializers.UUIDField()
    state = serializers.ChoiceField(choices=HostedHarnessJob.State.values)


class HarnessVerifyResultSerializer(serializers.Serializer):
    """How an agent version did on the held-back cases: counts only, never a case."""

    state = serializers.ChoiceField(choices=HostedHarnessJob.State.values)
    passed = serializers.IntegerField(min_value=0)
    failed = serializers.IntegerField(min_value=0)
    # Trials asked for; those neither passed nor failed are unfinished or undecided.
    total = serializers.IntegerField(min_value=0)
    trials = serializers.IntegerField(min_value=1, max_value=20)
    held_back_pass_rate = serializers.ListField(
        child=serializers.FloatField(min_value=0, max_value=1),
        min_length=2,
        max_length=2,
        allow_null=True,
        help_text="95% range of the trial pass rate, [low, high]; null until it ends.",
    )
    limited_validation = serializers.BooleanField(
        help_text="Too few cases were held back to call a version verified."
    )


NUDGE_STATES = tuple(HarnessNudge.State.values)


class HarnessNudgeFileSerializer(serializers.Serializer):
    path = serializers.CharField()
    status = serializers.ChoiceField(
        choices=("added", "removed", "modified", "renamed", "copied")
    )
    previous_path = serializers.CharField(allow_null=True)
    additions = serializers.IntegerField(allow_null=True)
    deletions = serializers.IntegerField(allow_null=True)
    has_patch = serializers.BooleanField()
    patch_omitted = serializers.CharField(
        allow_null=True,
        help_text=(
            "Why the file's patch isn't kept: secret_path, too_large, binary, budget"
            " or redaction_unavailable."
        ),
    )


class HarnessNudgeVariablesSerializer(serializers.Serializer):
    """Variable names only; never a value."""

    added = serializers.ListField(child=serializers.CharField())
    removed = serializers.ListField(child=serializers.CharField())
    changed = serializers.ListField(child=serializers.CharField())
    unknown = serializers.ListField(
        child=serializers.CharField(),
        help_text="Names whose values couldn't be read, so they may have changed.",
    )


class HarnessNudgeFactsSerializer(serializers.Serializer):
    """What changed: an environment nudge's agent facts, or a suite nudge's environment
    diff. Names, paths, statuses and counts; never a value or a patch."""

    source = serializers.JSONField(
        required=False,
        help_text="The agent source's kind, whether it changed, and its from/to ids.",
    )
    changed_files = HarnessNudgeFileSerializer(many=True, required=False)
    files_total = serializers.IntegerField(required=False, allow_null=True)
    files_truncated = serializers.BooleanField(required=False)
    reversed = serializers.BooleanField(
        required=False, help_text="A roll back to an earlier commit."
    )
    files_unavailable = serializers.CharField(
        required=False,
        allow_null=True,
        help_text=(
            "Why the file changes aren't listed: private_repository, no_commit,"
            " repository_changed, not_linear, rate_limited, github_error,"
            " archive_missing, archive_unreadable or archive_too_large."
        ),
    )
    prompt_changed = serializers.BooleanField(required=False)
    variables = HarnessNudgeVariablesSerializer(required=False)
    settings_changed = serializers.ListField(
        child=serializers.CharField(), required=False
    )
    agent_default_moved = serializers.BooleanField(
        required=False,
        help_text="Whether the agent version is already this environment's default.",
    )
    environment_diff = serializers.JSONField(required=False, allow_null=True)
    environment_summary = serializers.CharField(required=False, allow_blank=True)
    from_environment_version_id = serializers.UUIDField(required=False, allow_null=True)


class HarnessNudgeReplacementSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    trigger_label = serializers.CharField(allow_null=True)


class HarnessNudgeProblemSerializer(serializers.Serializer):
    item = serializers.CharField(allow_blank=True)
    kind = serializers.CharField(allow_blank=True)
    message = serializers.CharField(allow_blank=True)
    in_your_unsaved_edits = serializers.BooleanField()


class HarnessNudgeErrorSerializer(serializers.Serializer):
    code = serializers.CharField(
        help_text=(
            "stopped_by_user, stopped_by_newer, environment_deleted, checks_failed,"
            " no_report, not_runnable, turn_failed, turn_interrupted,"
            " rebuild_not_started, rebuild_stuck, landing_failed, or a refused save's"
            " code."
        )
    )
    message = serializers.CharField(required=False)
    problems = HarnessNudgeProblemSerializer(many=True, required=False)


class HarnessNudgeSerializer(serializers.Serializer):
    """An offer to rebuild the environment or its suite after a change."""

    id = serializers.UUIDField()
    target = serializers.ChoiceField(choices=HarnessNudge.Target.values)
    state = serializers.ChoiceField(choices=NUDGE_STATES)
    facts = HarnessNudgeFactsSerializer()
    trigger_agent_version = RunVersionLabelSerializer(allow_null=True)
    base_agent_version = RunVersionLabelSerializer(allow_null=True)
    base_environment_version = RunVersionLabelSerializer(allow_null=True)
    base_suite_version = RunVersionLabelSerializer(allow_null=True)
    result_environment_version = RunVersionLabelSerializer(allow_null=True)
    result_suite_version = RunVersionLabelSerializer(allow_null=True)
    summary = serializers.JSONField(allow_null=True)
    error = HarnessNudgeErrorSerializer(allow_null=True)
    closed_at = serializers.DateTimeField(allow_null=True)
    queued_behind_input = serializers.BooleanField()
    can_stop = serializers.BooleanField()
    replaced_by = HarnessNudgeReplacementSerializer(allow_null=True)
    card_message_id = serializers.UUIDField(
        allow_null=True, help_text="The nudge's card in the environment's chat."
    )
    answered_by = HarnessVersionPersonSerializer(allow_null=True)
    answered_at = serializers.DateTimeField(allow_null=True)
    created_at = serializers.DateTimeField()


class HarnessNudgeListQuerySerializer(serializers.Serializer):
    state = serializers.CharField(
        required=False, help_text="Comma-separated nudge states to list."
    )

    def validate_state(self, value):
        states = [one.strip() for one in value.split(",") if one.strip()]
        unknown = sorted(set(states) - set(NUDGE_STATES))
        if unknown:
            raise serializers.ValidationError(f"Unknown state: {', '.join(unknown)}")
        return states


class HarnessNudgeAnswerSerializer(serializers.Serializer):
    answer = serializers.ChoiceField(choices=("yes", "no"))
