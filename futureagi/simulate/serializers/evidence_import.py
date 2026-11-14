"""The evidence import contract: Observe sessions or calls frozen for an
environment, and the bundle a person reviews before a test is drafted from them."""

from rest_framework import serializers

from simulate.models import EvidenceImport, EvidenceImportCase
from simulate.selectors.evidence_import import MAX_SOURCES

# unreadable: recorded, but could not be read; missing: not recorded.
PART_STATUSES = ("present", "partial", "truncated", "missing", "unreadable")


class EvidenceImportCreateSerializer(serializers.Serializer):
    project_id = serializers.UUIDField()
    source_kind = serializers.ChoiceField(choices=EvidenceImport.SourceKind.choices)
    ids = serializers.ListField(
        child=serializers.UUIDField(),
        min_length=1,
        max_length=MAX_SOURCES,
        help_text="Session ids, or the trace ids of calls, in the order picked.",
    )

    def validate_ids(self, value):
        if len(set(value)) != len(value):
            raise serializers.ValidationError("Each id may appear once.")
        return value


class EvidencePartSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=PART_STATUSES)
    reason = serializers.CharField(allow_blank=True)


class EvidenceTurnSerializer(serializers.Serializer):
    role = serializers.ChoiceField(choices=("user", "agent"))
    text = serializers.CharField(allow_blank=True)
    truncated = serializers.BooleanField()
    trace_id = serializers.CharField(
        allow_blank=True, help_text="Blank for a turn no trace recorded."
    )
    at = serializers.DateTimeField(allow_null=True)


class EvidenceTranscriptSerializer(EvidencePartSerializer):
    turns = EvidenceTurnSerializer(many=True)


class EvidenceToolCallSerializer(serializers.Serializer):
    span_id = serializers.CharField()
    trace_id = serializers.CharField()
    name = serializers.CharField(allow_blank=True)
    at = serializers.DateTimeField()
    failed = serializers.BooleanField()
    error = serializers.CharField(allow_blank=True)
    input = serializers.CharField(allow_blank=True)
    output = serializers.CharField(allow_blank=True)
    input_truncated = serializers.BooleanField()
    output_truncated = serializers.BooleanField()


class EvidenceToolCallsSerializer(EvidencePartSerializer):
    calls = EvidenceToolCallSerializer(many=True)


class EvidenceLabelSerializer(serializers.Serializer):
    name = serializers.CharField()
    kind = serializers.CharField()
    value = serializers.JSONField(
        help_text="As the annotator recorded it: input evidence, not a verdict."
    )
    notes = serializers.CharField(allow_blank=True)
    target = serializers.ChoiceField(choices=("session", "trace", "span"))


class EvidenceLabelsSerializer(EvidencePartSerializer):
    labels = EvidenceLabelSerializer(many=True)


class EvidenceNewCheckSerializer(serializers.Serializer):
    name = serializers.CharField()
    what = serializers.CharField(allow_blank=True)


class EvidenceImportCaseSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    source_id = serializers.CharField()
    status = serializers.ChoiceField(choices=EvidenceImportCase.Status.choices)
    reason = serializers.CharField(allow_blank=True)
    source_available = serializers.BooleanField(
        help_text="False once Observe no longer holds the source: its provenance "
        "is unavailable, though what became of the case stands."
    )
    scenario_key = serializers.CharField(
        allow_blank=True, help_text="The drafted scenario's key, once drafted."
    )
    scenario_name = serializers.CharField(
        allow_blank=True,
        help_text="The drafted scenario's title, once drafted; blank when the "
        "builder gave none.",
    )
    new_checks = EvidenceNewCheckSerializer(
        many=True,
        help_text="Checks this case adds to the environment when it is added: each "
        "one is new, never a change to an existing check.",
    )
    changed_since_import = serializers.BooleanField(
        allow_null=True,
        help_text="Null when unknown: the source is gone, or part of it could not "
        "be read now or when the import was made.",
    )
    trace_ids = serializers.ListField(child=serializers.CharField())
    started_at = serializers.DateTimeField(allow_null=True)
    ended_at = serializers.DateTimeField(allow_null=True)
    transcript = EvidenceTranscriptSerializer()
    tool_calls = EvidenceToolCallsSerializer()
    tool_inputs = EvidencePartSerializer(
        help_text="Whether the tool calls recorded their inputs."
    )
    tool_outputs = EvidencePartSerializer(
        help_text="Whether the tool calls recorded their outputs."
    )
    starting_state = EvidencePartSerializer(
        help_text="The records the agent's tools read before the conversation."
    )
    labels = EvidenceLabelsSerializer()


class EvidenceRedactionFindingSerializer(serializers.Serializer):
    case_id = serializers.UUIDField()
    part = serializers.ChoiceField(choices=("transcript", "tool_calls", "labels"))
    index = serializers.IntegerField(min_value=0)
    field = serializers.ChoiceField(
        choices=("text", "input", "output", "error", "notes", "value")
    )
    entity = serializers.CharField()
    start = serializers.IntegerField(min_value=0)
    end = serializers.IntegerField(min_value=0)
    value = serializers.CharField()
    replacement = serializers.CharField()


class EvidenceRedactionSerializer(serializers.Serializer):
    method = serializers.ChoiceField(
        choices=("presidio", "basic"),
        help_text="basic: a fixed-shape match, for a server without Presidio; it "
        "finds no names or addresses.",
    )
    reason = serializers.CharField(allow_blank=True)
    findings = EvidenceRedactionFindingSerializer(many=True)
    total = serializers.IntegerField(
        min_value=0, help_text="Every finding, including those past the listed ones."
    )


class EvidenceImportSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    environment_id = serializers.UUIDField()
    project_id = serializers.UUIDField(
        allow_null=True,
        help_text="The Observe project; null for evidence from outside Observe.",
    )
    agent_version_id = serializers.UUIDField(allow_null=True)
    source_kind = serializers.ChoiceField(choices=EvidenceImport.SourceKind.choices)
    created_at = serializers.DateTimeField()
    simulated = serializers.BooleanField(
        help_text="True when the sources come from a Simulator project: simulated "
        "conversations, not production evidence."
    )
    drafting = serializers.BooleanField(
        help_text="True while the environment's builder is drafting cases."
    )
    cases = EvidenceImportCaseSerializer(many=True)
    redaction = EvidenceRedactionSerializer()


class EvidenceDraftRefSerializer(serializers.Serializer):
    id = serializers.UUIDField(allow_null=True)
    revision = serializers.IntegerField(min_value=0)


class EvidenceExpectedDraftsSerializer(serializers.Serializer):
    environment = EvidenceDraftRefSerializer()
    suite = EvidenceDraftRefSerializer()


class EvidenceImportAddSerializer(serializers.Serializer):
    case_ids = serializers.ListField(
        child=serializers.UUIDField(), min_length=1, max_length=MAX_SOURCES
    )
    expected_drafts = EvidenceExpectedDraftsSerializer(
        help_text="The unsaved changes the review saw (GET drafts); none means the "
        "suite is saved with the cases."
    )
    idempotency_key = serializers.CharField(max_length=128)


class EvidenceImportAddResponseSerializer(serializers.Serializer):
    saved = serializers.BooleanField(
        help_text="True: the suite was saved with the cases. False: they joined the "
        "unsaved changes beside other edits."
    )
    evidence_import = EvidenceImportSerializer()


class EvidenceImportDraftSerializer(serializers.Serializer):
    case_ids = serializers.ListField(
        child=serializers.UUIDField(),
        min_length=1,
        max_length=MAX_SOURCES,
        help_text="The import's waiting cases the environment's builder drafts.",
    )
    shows = serializers.DictField(
        child=serializers.ChoiceField(choices=("failure", "success", "partial")),
        required=False,
        default=dict,
        help_text="What the person says each case records, by case id.",
    )
