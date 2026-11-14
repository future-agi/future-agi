"""The evidence import contract: Observe sessions or calls, dataset rows or
transcripts frozen for an environment, and the bundle a person reviews before a
test is drafted from them."""

from rest_framework import serializers

from simulate.models import EvidenceImport, EvidenceImportCase
from simulate.selectors.dataset_evidence import MAX_LABEL_COLUMNS
from simulate.selectors.evidence_import import MAX_SOURCES
from simulate.utils.transcript_parse import FORMATS, MAX_TRANSCRIPT_CHARS
from tfc.utils.serializer_fields import JsonValueField

# unreadable: recorded, but could not be read; missing: not recorded.
PART_STATUSES = ("present", "partial", "truncated", "missing", "unreadable")
TRANSCRIPT_NAME_CHARS = 120


class EvidenceTranscriptInputSerializer(serializers.Serializer):
    name = serializers.CharField(
        max_length=TRANSCRIPT_NAME_CHARS,
        help_text="What the person calls it, such as the file's name; redacted "
        "before it is stored.",
    )
    format = serializers.ChoiceField(
        choices=FORMATS,
        help_text='json: a list of {role, content} messages, or {"messages": '
        '[...]}. text: lines that start "User:" or "Agent:". csv: a header with '
        "a role column and a text column.",
    )
    text = serializers.CharField(
        max_length=MAX_TRANSCRIPT_CHARS,
        trim_whitespace=False,
        help_text="The transcript as pasted or uploaded.",
    )


class EvidenceDatasetColumnsSerializer(serializers.Serializer):
    """Which of a dataset's columns its rows are read through, by id."""

    conversation = serializers.UUIDField(
        help_text="The conversation: chat messages as JSON, or lines that start "
        '"User:" or "Agent:".'
    )
    labels = serializers.ListField(
        child=serializers.UUIDField(),
        max_length=MAX_LABEL_COLUMNS,
        required=False,
        default=list,
        help_text="Columns read as the row's labels.",
    )
    expected = serializers.UUIDField(
        allow_null=True,
        required=False,
        default=None,
        help_text="The column that says what should happen.",
    )

    def validate(self, attrs):
        picked = [attrs["conversation"], *attrs["labels"]]
        if attrs["expected"] is not None:
            picked.append(attrs["expected"])
        if len(set(picked)) != len(picked):
            raise serializers.ValidationError("Each column may be picked once.")
        return attrs


class EvidenceImportCreateSerializer(serializers.Serializer):
    """An Observe import (session or call) names its project and ids; a dataset
    import its dataset, row ids and columns; a transcript import carries its
    transcripts instead."""

    # What each kind needs and may take; any other source field is refused.
    NEEDS = {
        EvidenceImport.SourceKind.SESSION: ("project_id", "ids"),
        EvidenceImport.SourceKind.CALL: ("project_id", "ids"),
        EvidenceImport.SourceKind.TRANSCRIPT: ("transcripts",),
        EvidenceImport.SourceKind.DATASET: ("dataset_id", "ids", "columns"),
    }
    TAKES = {
        EvidenceImport.SourceKind.TRANSCRIPT: ("production",),
        EvidenceImport.SourceKind.DATASET: ("production",),
    }
    SOURCE_FIELDS = (
        "project_id",
        "ids",
        "transcripts",
        "production",
        "dataset_id",
        "columns",
    )

    source_kind = serializers.ChoiceField(choices=EvidenceImport.SourceKind.choices)
    project_id = serializers.UUIDField(
        required=False, help_text="Session and call imports: the Observe project."
    )
    ids = serializers.ListField(
        child=serializers.UUIDField(),
        min_length=1,
        max_length=MAX_SOURCES,
        required=False,
        help_text="Session, call and dataset imports: session ids, the trace ids "
        "of calls, or row ids, in the order picked.",
    )
    dataset_id = serializers.UUIDField(
        required=False, help_text="Dataset imports: the dataset."
    )
    columns = EvidenceDatasetColumnsSerializer(
        required=False, help_text="Dataset imports: the columns to read."
    )
    transcripts = serializers.ListField(
        child=EvidenceTranscriptInputSerializer(),
        min_length=1,
        max_length=MAX_SOURCES,
        required=False,
        help_text="Transcript imports: one case each, in this order.",
    )
    production = serializers.BooleanField(
        required=False,
        help_text="Transcript and dataset imports: the person says they come from "
        "production. Unsaid, they are not called production evidence.",
    )

    def validate_ids(self, value):
        if len(set(value)) != len(value):
            raise serializers.ValidationError("Each id may appear once.")
        return value

    def validate(self, attrs):
        kind = attrs["source_kind"]
        needed = self.NEEDS[kind]
        allowed = {*needed, *self.TAKES.get(kind, ())}
        refused = [field for field in self.SOURCE_FIELDS if field not in allowed]
        errors = {
            **{
                field: [f"A {kind} import needs it."]
                for field in needed
                if field not in attrs
            },
            **{
                field: [f"A {kind} import does not take it."]
                for field in refused
                if field in attrs
            },
        }
        if errors:
            raise serializers.ValidationError(errors)
        return attrs


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
    value = JsonValueField(
        help_text="As the annotator recorded it: input evidence, not a verdict."
    )
    notes = serializers.CharField(allow_blank=True)
    target = serializers.ChoiceField(
        choices=("session", "trace", "span", "row"),
        help_text="row: a dataset row's label column.",
    )


class EvidenceLabelsSerializer(EvidencePartSerializer):
    labels = EvidenceLabelSerializer(many=True)


class EvidenceExpectedOutcomeSerializer(EvidencePartSerializer):
    text = serializers.CharField(allow_blank=True)


class EvidenceNewCheckSerializer(serializers.Serializer):
    name = serializers.CharField()
    what = serializers.CharField(allow_blank=True)


class EvidenceDuplicateOfSerializer(serializers.Serializer):
    import_id = serializers.UUIDField(help_text="The import holding the case.")
    case_id = serializers.UUIDField()
    scenario_name = serializers.CharField(
        allow_blank=True, help_text="Its drafted scenario's title; blank until drafted."
    )


class EvidenceImportCaseSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    source_id = serializers.CharField()
    name = serializers.CharField(
        allow_blank=True,
        help_text="A transcript's name as the person gave it, redacted; a dataset "
        "row's place in the import and its dataset ('Row 2 · Support evals'); "
        "blank for an Observe source.",
    )
    status = serializers.ChoiceField(choices=EvidenceImportCase.Status.choices)
    reason = serializers.CharField(allow_blank=True)
    source_available = serializers.BooleanField(
        help_text="False once Observe or the dataset no longer holds the source: "
        "its provenance is unavailable, though what became of the case stands. "
        "Always true for a transcript, whose text is stored."
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
        "be read now or when the import was made. Always null for a transcript, "
        "which has no source to read again.",
    )
    also_from = serializers.IntegerField(
        min_value=0,
        help_text="How many more sources hold this case: the same source or the "
        "same conversation imported again, in this import or a later one.",
    )
    duplicate_of = EvidenceDuplicateOfSerializer(
        allow_null=True,
        help_text="For a case already imported (status duplicate): the case of "
        "another import it repeats, proposed there. Null otherwise, or once that "
        "case is gone.",
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
    expected_outcome = EvidenceExpectedOutcomeSerializer(
        help_text="What a dataset row's expected outcome column says should happen."
    )


class EvidenceRedactionFindingSerializer(serializers.Serializer):
    case_id = serializers.UUIDField()
    part = serializers.ChoiceField(
        choices=("transcript", "tool_calls", "labels", "expected_outcome")
    )
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
    dataset_id = serializers.UUIDField(
        allow_null=True, help_text="A dataset import's dataset; null otherwise."
    )
    agent_version_id = serializers.UUIDField(allow_null=True)
    source_kind = serializers.ChoiceField(choices=EvidenceImport.SourceKind.choices)
    created_at = serializers.DateTimeField()
    simulated = serializers.BooleanField(
        help_text="True when the sources come from a Simulator project: simulated "
        "conversations, not production evidence."
    )
    production = serializers.BooleanField(
        help_text="True for production evidence: an Observe project that is not a "
        "Simulator project, or transcripts or dataset rows the person said come "
        "from production."
    )
    drafting = serializers.BooleanField(
        help_text="True while the environment's builder is drafting cases."
    )
    cases = EvidenceImportCaseSerializer(many=True)
    redaction = EvidenceRedactionSerializer()


class EvidenceImportListItemSerializer(serializers.Serializer):
    """One import of an environment a person can still finish."""

    id = serializers.UUIDField()
    source_kind = serializers.ChoiceField(choices=EvidenceImport.SourceKind.choices)
    source_name = serializers.CharField(
        allow_null=True,
        help_text="The Observe project's or dataset's name; a transcript import's "
        "first transcript name, or null when it has none.",
    )
    source_available = serializers.BooleanField(
        help_text="False once the Observe project or dataset was deleted."
    )
    case_count = serializers.IntegerField(min_value=0)
    added_count = serializers.IntegerField(
        min_value=0, help_text="Cases already added to the suite."
    )
    status = serializers.ChoiceField(
        choices=("waiting", "drafting", "drafted", "partly_added", "blocked"),
        help_text="drafting: the builder is drafting its cases now. partly_added: "
        "some cases were added and some can still be. blocked: only blocked cases "
        "are left, to retry by importing again or to discard.",
    )
    created_at = serializers.DateTimeField()
    can_discard = serializers.BooleanField(
        help_text="False while the builder drafts its cases. Discarding an import "
        "with added cases discards only the cases not added."
    )


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
