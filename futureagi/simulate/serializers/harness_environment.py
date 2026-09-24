from __future__ import annotations

from rest_framework import serializers

from simulate.services.harness_environment import (
    AGENT_TYPE_CHAT,
    AGENT_TYPE_VOICE,
    STATUS_BUILDING,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_RUNNING,
)


class HarnessEnvironmentListQuerySerializer(serializers.Serializer):
    """Paging for the environments list.

    ``limit`` is named to match ``ExtendedPageNumberPagination``'s own query
    parameter so the two cannot disagree about what a page is.
    """

    page = serializers.IntegerField(required=False, min_value=1)
    limit = serializers.IntegerField(required=False, min_value=1, max_value=100)


class HarnessEnvironmentRunSerializer(serializers.Serializer):
    """The body of a run request, which carries nothing.

    Starting a run reuses the saved contract and scenario suite, so there is
    nothing for a caller to choose. The serializer exists so the endpoint
    declares that in its schema: a mutation with no declared body reads as an
    undocumented one, and the contract coverage gate counts it as debt.
    """


class HarnessEnvironmentRenameSerializer(serializers.Serializer):
    """The only field an environment exposes for editing.

    Blank is rejected rather than treated as a reset: clearing the name would
    silently fall back to the derived one, which reads as the rename having been
    ignored.
    """

    name = serializers.CharField(
        max_length=255, allow_blank=False, trim_whitespace=True
    )


class HarnessEnvironmentSerializer(serializers.Serializer):
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
    last_updated = serializers.DateTimeField(allow_null=True)
    created_at = serializers.DateTimeField()


class HarnessEnvironmentListResponseSerializer(serializers.Serializer):
    count = serializers.IntegerField()
    next = serializers.CharField(allow_null=True)
    previous = serializers.CharField(allow_null=True)
    total_pages = serializers.IntegerField()
    current_page = serializers.IntegerField()
    results = HarnessEnvironmentSerializer(many=True)


class HarnessEnvironmentRunResponseSerializer(serializers.Serializer):
    """What starting a simulation returns.

    The trigger is accepted, not completed: execution is a separate concern and
    the caller polls the job for progress.
    """

    environment_id = serializers.UUIDField()
    job_id = serializers.UUIDField()
    run_id = serializers.UUIDField()
    state = serializers.CharField()
    stage = serializers.CharField()


class HarnessEnvironmentRunLinkSerializer(serializers.Serializer):
    run_test_id = serializers.UUIDField(allow_null=True)
    test_execution_id = serializers.UUIDField(allow_null=True)
    simulation_url = serializers.CharField(allow_null=True)


class HarnessEnvironmentOverviewSerializer(HarnessEnvironmentSerializer):
    """The list row plus the counts the overview draws; each null until authored."""

    flows_count = serializers.IntegerField(allow_null=True)
    guardrails_count = serializers.IntegerField(allow_null=True)
    personas_count = serializers.IntegerField(allow_null=True)
    evaluations_count = serializers.IntegerField()
    run = HarnessEnvironmentRunLinkSerializer()


class HarnessEnvironmentAddEvaluationSerializer(serializers.Serializer):
    """The one thing adding an eval takes: which eval, by its exact name."""

    name = serializers.CharField(
        max_length=255, allow_blank=False, trim_whitespace=True
    )


class HarnessEnvironmentRunEvaluationQueuedSerializer(serializers.Serializer):
    """What adding an eval from inside a run reports back.

    Not the environment detail: this endpoint's answer is how much grading it
    started and why it started less than the run has finished calls. The client
    refetches the detail itself after an add (frontend contract P29).

    ``completed_calls`` is the finished calls the endpoint looked at, and the
    four others always partition it exactly. ``queued`` means "stamped and
    scheduled for dispatch" -- a call whose stamp committed but whose grading
    job then failed to queue is still counted in ``queued``, not subtracted
    from it; the failure is logged and the stamp is cleared so the next
    request can retry it (contract P19).
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


class HarnessEnvironmentToolCallEvaluationSerializer(serializers.Serializer):
    """The tool-call judge's switch, sent as a whole state rather than a patch.

    Named after the column it sets, ``RunTest.enable_tool_evaluation``, because
    every other surface in this codebase already spells it this way;
    ``grep -rn enable_tool_evaluation`` finds them. A second name for one
    column is the only thing a grep could not find.

    Required, not defaulted: a body that forgets the key is a client bug, and
    silently reading it as ``false`` would turn "I meant to switch this on"
    into "I switched it off".
    """

    enable_tool_evaluation = serializers.BooleanField()


class HarnessEnvironmentEvalInputSerializer(serializers.Serializer):
    """Which stored piece of a call fills one of an eval's required keys.

    ``label`` is the only text a picker shows for a source: the frontend never
    computes which source fills a key (frontend contract P1, F1).
    """

    key = serializers.CharField()
    source = serializers.ChoiceField(
        choices=(
            "voice_recording",
            "transcript",
            "agent_prompt",
            "scenario_columns.situation.value",
        )
    )
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
    data_store = serializers.JSONField(required=False, allow_null=True)
    open_questions = serializers.ListField(
        child=serializers.CharField(), required=False, allow_null=True
    )
    amendments = HarnessEnvironmentAmendmentSerializer(many=True)
    sub_goals = HarnessEnvironmentCatalogueSubGoalSerializer(many=True)
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


class HarnessEnvironmentWorldSerializer(serializers.Serializer):
    runtime = serializers.DictField()
    personas = HarnessEnvironmentPersonaSerializer(many=True)
    stores = HarnessEnvironmentStoreSerializer(many=True)


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
    persona = serializers.DictField(allow_null=True)
    situation = serializers.CharField(allow_null=True)
    outcome = serializers.CharField(allow_null=True)
    status = serializers.CharField()
    call_execution_id = serializers.UUIDField(allow_null=True)


class HarnessEnvironmentSelectedEvalSerializer(HarnessEnvironmentOfferedEvalSerializer):
    """One eval this environment is graded by: the same entry plus its id.

    Only the configs that carry a mapping are listed; the rows ingestion
    creates for the harness's own result columns are bound to the run but were
    never selected (frontend contract P16). ``runnable`` is therefore always
    true here and is kept because the frontend already reads it.
    """

    id = serializers.UUIDField()
    runnable = serializers.BooleanField()


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
    ``enable_tool_evaluation`` is the tool-call judge's switch — read here,
    written by ``PUT evaluations/tool-call/`` (frontend contract v1.9 P31).
    It is never null: an environment with no run test reads ``false``.
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
