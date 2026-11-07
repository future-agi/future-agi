"""Serializer source of truth for the additive Skills v1.1 wire contract.

Swagger 2 cannot express oneOf. Discriminated request serializers list optional
branch fields and validate the exact branch at runtime; help text documents it.
All nested request objects reject unknown fields, including review metadata.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from decimal import Decimal

from rest_framework import serializers as s

from model_hub.serializers.skills_hub import (
    ChecksResult,
    CompareResult,
    DraftOut,
    FileChunk,
    FileInput,
    HistoryActor,
    Person,
    SkillSummary,
)
from model_hub.serializers.skills_hub import (
    SkillsError as V1SkillsError,
)
from model_hub.services.skills_hub import limits as v1_limits
from model_hub.services.skills_hub.errors import SkillsHubError
from model_hub.services.skills_hub.v11 import limits as v11_limits
from model_hub.services.skills_hub.v11.suites import validate_cases, validate_check
from model_hub.services.skills_hub.v11.types import (
    ACTIVE_JOB_STATUSES,
    TERMINAL_JOB_STATUSES,
)

JOB_STATUSES = sorted(ACTIVE_JOB_STATUSES | TERMINAL_JOB_STATUSES)
VARIANTS = ["candidate", "production", "without_target"]
VERDICTS = ["pass", "fail", "not_applicable", "not_graded", "unverifiable"]
ATTEMPT_STATUSES = [
    "not_started",
    "running",
    "completed",
    "error",
    "cancelled",
    "incomplete",
    "unknown",
]


class HubV11Serializer(s.Serializer):
    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        if "Meta" not in cls.__dict__:
            cls.Meta = type("Meta", (), {"ref_name": f"SkillsHubV11{cls.__name__}"})

    def to_internal_value(self, data):
        if isinstance(data, Mapping):
            unknown = set(data) - set(self.fields)
            if unknown:
                raise s.ValidationError(
                    dict.fromkeys(sorted(unknown), "Unknown field.")
                )
        return super().to_internal_value(data)


class SkillsError(V1SkillsError):
    """The existing GeneralMethods envelope, with structured domain details."""

    class Meta:
        ref_name = "SkillsHubV11Error"

    detail = s.JSONField(
        required=False,
        allow_null=True,
        help_text="Structured domain conflict/check details, or the standard authentication/validation message.",
    )


class Integer(s.IntegerField):
    def to_internal_value(self, data):
        if isinstance(data, bool):
            self.fail("invalid")
        return super().to_internal_value(data)


class Boolean(s.BooleanField):
    def to_internal_value(self, data):
        if type(data) is not bool:
            self.fail("invalid", input=data)
        return data


class USD(s.DecimalField):
    def __init__(self, **kwargs):
        super().__init__(
            max_digits=18, decimal_places=8, coerce_to_string=True, **kwargs
        )

    def to_internal_value(self, data):
        if not isinstance(data, str):
            raise s.ValidationError("USD amounts must be decimal strings.")
        return super().to_internal_value(data)


class Digest(s.RegexField):
    def __init__(self, **kwargs):
        super().__init__(r"^sha256:[a-f0-9]{64}$", **kwargs)


class Fingerprint(s.RegexField):
    def __init__(self, **kwargs):
        super().__init__(r"^(?:sha256:)?[a-f0-9]{64}$", **kwargs)


class TrueAcknowledgment(Boolean):
    def to_internal_value(self, data):
        value = super().to_internal_value(data)
        if value is not True:
            raise s.ValidationError("Explicit acknowledgment is required.")
        return value


def _branch(attrs: dict, expected: set[str]) -> dict:
    if set(attrs) != expected:
        raise s.ValidationError(
            "Supply exactly the fields for the selected request kind."
        )
    return attrs


class EmptyRequest(HubV11Serializer):
    pass


class PageQuery(HubV11Serializer):
    page = Integer(min_value=1, default=1)
    page_size = Integer(min_value=1, max_value=100, default=25)


class ProfileLimits(HubV11Serializer):
    max_cases = Integer()
    max_variants = Integer()
    repetitions = Integer()
    max_attempts = Integer()
    deadline_seconds = Integer()
    max_task_turns_per_attempt = Integer()
    max_tool_calls_per_attempt = Integer()
    task_phase_seconds = Integer()
    judge_phase_seconds = Integer()
    provider_call_seconds = Integer()
    max_task_output_tokens = Integer()
    max_judge_output_tokens = Integer()
    max_delivered_bytes_per_attempt = Integer()
    max_total_tokens = Integer()


class Profiles(HubV11Serializer):
    quick = ProfileLimits()
    repeated = ProfileLimits()


class Limits(HubV11Serializer):
    """Effective deployment overrides; seconds, UTF-8 bytes, tokens, decimal USD."""

    profiles = Profiles()
    max_competitors = Integer()
    max_active_attempts_per_run = Integer()
    max_active_provider_calls_per_organization = Integer()
    max_active_model_jobs_per_organization = Integer()
    max_queue_wait_seconds = Integer()
    max_estimated_cost_usd = USD()


class ModelConfig(HubV11Serializer):
    model_id = s.CharField(max_length=255)
    temperature = s.FloatField()
    max_output_tokens = Integer(
        min_value=1, max_value=v11_limits.MAX_ASSIST_OUTPUT_TOKENS
    )

    def validate_temperature(self, value):
        if not math.isfinite(value):
            raise s.ValidationError("Temperature must be finite.")
        return value


class ExecutionConfiguration(HubV11Serializer):
    model = ModelConfig()
    judge = ModelConfig(allow_null=True)
    harness_version = s.CharField()
    adapter_version = s.CharField()
    evaluator_version = s.CharField()


class ModelOption(HubV11Serializer):
    model_id = s.CharField()
    provider = s.CharField()
    configured = Boolean()
    tool_support = s.ChoiceField(choices=["supported", "unsupported"])
    context_limit = Integer()
    pricing_status = s.ChoiceField(choices=["known", "unknown"])
    can_run = Boolean()
    disabled_reason = s.CharField(allow_null=True)


class TestingOptions(HubV11Serializer):
    enabled = Boolean()
    github_enabled = Boolean()
    models = ModelOption(many=True)
    limits = Limits()
    harness_version = s.CharField()
    evaluator_version = s.CharField()


class Fixture(HubV11Serializer):
    id = s.UUIDField()
    name = s.CharField(trim_whitespace=False)
    text = s.CharField(trim_whitespace=False, allow_blank=True)


class CheckConfig(HubV11Serializer):
    text = s.CharField(max_length=2000, trim_whitespace=False, required=False)
    case_sensitive = Boolean(required=False)
    pointer = s.CharField(
        max_length=2000, trim_whitespace=False, allow_blank=True, required=False
    )
    value = s.JSONField(allow_null=True, required=False)
    schema = s.JSONField(required=False)
    path = s.CharField(max_length=255, trim_whitespace=False, required=False)
    complete = Boolean(required=False)
    requirement = s.CharField(max_length=2000, trim_whitespace=False, required=False)


class Check(HubV11Serializer):
    id = s.UUIDField()
    kind = s.ChoiceField(
        choices=[
            "contains",
            "not_contains",
            "json_valid",
            "json_equals",
            "json_schema",
            "file_delivered",
            "semantic",
        ]
    )
    critical = Boolean()
    config = CheckConfig()

    def validate(self, attrs):
        return validate_check(attrs)


class CaseInput(HubV11Serializer):
    id = s.UUIDField()
    title = s.CharField(max_length=120, trim_whitespace=False)
    source_group_id = s.CharField(trim_whitespace=False)
    prompt = s.CharField(trim_whitespace=False)
    fixtures = Fixture(many=True)
    expected_trigger = Boolean(allow_null=True)
    checks = Check(many=True, max_length=10)
    reviewed = Boolean()

    def validate(self, attrs):
        return validate_cases([attrs])[0]


class CaseOut(CaseInput):
    reviewed_by = Person(allow_null=True)
    reviewed_at = s.DateTimeField(allow_null=True)
    origin = s.ChoiceField(choices=["manual", "template", "assist"])
    exposed_to_assist = Boolean()


class SuiteOut(HubV11Serializer):
    revision = Integer()
    digest = Digest(allow_null=True)
    cases = CaseOut(many=True)
    updated_at = s.DateTimeField(allow_null=True)
    updated_by = Person(allow_null=True)
    visibility = s.ChoiceField(choices=["workspace"])


class SaveSuiteRequest(HubV11Serializer):
    revision = Integer(min_value=0)
    cases = CaseInput(many=True, max_length=20)

    def validate_cases(self, value):
        return validate_cases(value)


class CandidateRef(HubV11Serializer):
    kind = s.ChoiceField(choices=["draft", "version"])
    revision = Integer(min_value=1, required=False)
    version = Integer(min_value=1, required=False)
    digest = Digest()

    def validate(self, attrs):
        return _branch(
            attrs,
            {"kind", "digest", "revision" if attrs["kind"] == "draft" else "version"},
        )


class CompetitorRef(HubV11Serializer):
    skill_id = s.UUIDField()
    version = Integer(min_value=1)


class CaseSelection(HubV11Serializer):
    kind = s.ChoiceField(choices=["saved", "ad_hoc"])
    suite_revision = Integer(min_value=1, required=False)
    case_ids = s.ListField(
        child=s.UUIDField(), min_length=1, max_length=20, required=False
    )
    cases = CaseInput(many=True, min_length=1, max_length=20, required=False)

    def validate(self, attrs):
        if attrs["kind"] == "saved":
            _branch(attrs, {"kind", "suite_revision", "case_ids"})
            if len(set(attrs["case_ids"])) != len(attrs["case_ids"]):
                raise s.ValidationError("Case IDs must be unique.")
        else:
            _branch(attrs, {"kind", "cases"})
            attrs["cases"] = validate_cases(attrs["cases"], require_reviewed=True)
        return attrs


class RunBudget(HubV11Serializer):
    max_total_tokens = Integer(min_value=1, max_value=1_000_000)
    max_estimated_cost_usd = USD(min_value=Decimal("0.00000001"))


class RunConfig(HubV11Serializer):
    candidate = CandidateRef()
    cases = CaseSelection()
    baselines = s.ListField(
        child=s.ChoiceField(choices=["production", "without_target"]),
        min_length=1,
        max_length=2,
    )
    competitors = CompetitorRef(many=True, max_length=5)
    model = ModelConfig()
    judge = ModelConfig(allow_null=True)
    profile = s.ChoiceField(choices=["quick", "repeated"])
    repetitions = s.ChoiceField(choices=[1, 3])
    limits = RunBudget()
    purpose = s.ChoiceField(choices=["regression", "diagnostic"])
    parent_run_id = s.UUIDField(allow_null=True)

    def validate(self, attrs):
        v11_limits.validate_run_limits(attrs, skill_id=self.context.get("skill_id"))
        return attrs


class SnapshotIdentity(HubV11Serializer):
    skill_id = s.UUIDField()
    version_id = s.UUIDField(allow_null=True)
    version = Integer(allow_null=True)
    draft_revision = Integer(allow_null=True)
    digest = Digest()
    execution_manifest_digest = Digest()


class ErrorOut(HubV11Serializer):
    code = s.CharField()
    message = s.CharField()
    detail = s.DictField()


class UsageOut(HubV11Serializer):
    known_input_tokens = Integer(allow_null=True)
    known_output_tokens = Integer(allow_null=True)
    known_total_tokens = Integer(allow_null=True)
    unknown_calls = Integer()
    complete = Boolean()


class CostOut(HubV11Serializer):
    known_estimate_usd = USD(allow_null=True)
    reserved_estimate_usd = USD()
    unknown_calls = Integer()
    complete = Boolean()
    pricing_source = s.CharField(allow_blank=True)


class Metrics(HubV11Serializer):
    task_usage = UsageOut()
    judge_usage = UsageOut()
    task_cost = CostOut()
    judge_cost = CostOut()
    elapsed_ms = Integer(allow_null=True)
    provider_duration_ms = Integer(allow_null=True)


class Currentness(HubV11Serializer):
    content = Boolean()
    baseline = Boolean(allow_null=True)
    suite = Boolean(allow_null=True)
    competitors = Boolean(allow_null=True)
    payload_available = Boolean()


class JobAck(HubV11Serializer):
    id = s.UUIDField()
    status = s.ChoiceField(choices=JOB_STATUSES)
    poll_after_ms = s.ChoiceField(choices=[2000])
    deadline_at = s.DateTimeField()


class FindingLocation(HubV11Serializer):
    """Discriminated sanitized coordinates; no input excerpts or secret values."""

    kind = s.ChoiceField(
        choices=["bundle_file", "case_prompt", "fixture", "check", "assist_input"]
    )
    skill_id = s.UUIDField(required=False)
    digest = Digest(required=False)
    path = s.CharField(max_length=1024, required=False)
    case_id = s.UUIDField(required=False)
    fixture_id = s.UUIDField(required=False)
    check_id = s.UUIDField(required=False)
    pointer = s.CharField(
        max_length=2000,
        required=False,
        help_text="RFC6901 pointer distinguishes instruction, context, source cases and version diff within assistance input.",
    )
    line = Integer(min_value=1, allow_null=True)

    def validate(self, attrs):
        fields = {
            "bundle_file": {"skill_id", "digest", "path"},
            "case_prompt": {"case_id"},
            "fixture": {"case_id", "fixture_id"},
            "check": {"case_id", "check_id"},
            "assist_input": {"pointer"},
        }
        return _branch(attrs, {"kind", "line"} | fields[attrs["kind"]])


class OutboundFinding(HubV11Serializer):
    finding_id = s.CharField(max_length=128)
    code = s.CharField(max_length=64)
    location = FindingLocation()
    explanation = s.CharField(
        max_length=500, help_text="Redacted explanation; never the detected secret."
    )


class Estimate(HubV11Serializer):
    upper_bound_usd = USD()
    pricing_source = s.CharField()


class InventoryFile(HubV11Serializer):
    path = s.CharField()
    sha256 = s.CharField()
    size_bytes = Integer()
    flags = s.ListField(
        child=s.ChoiceField(
            choices=[
                "evaluation_artifact",
                "case_overlap",
                "secret_suspected",
                "executable_content",
            ]
        )
    )


class InventoryBundle(HubV11Serializer):
    skill_id = s.UUIDField()
    digest = Digest()
    files = InventoryFile(many=True)


class WarningOut(HubV11Serializer):
    code = s.CharField()
    message = s.CharField()


class ProviderDisclosure(HubV11Serializer):
    task_provider = s.CharField()
    judge_provider = s.CharField(allow_null=True)


class TestRunPreview(HubV11Serializer):
    fingerprint = Fingerprint(
        help_text="Plan fingerprint excludes subsequently submitted review decisions."
    )
    candidate = SnapshotIdentity()
    production = SnapshotIdentity(allow_null=True)
    production_event_id = s.UUIDField(allow_null=True)
    release_revision = Integer()
    competitors = SnapshotIdentity(many=True)
    case_count = Integer()
    variant_count = Integer()
    repetitions = Integer()
    attempt_count = Integer()
    max_task_calls = Integer()
    max_judge_calls = Integer()
    estimate = Estimate()
    limits = Limits()
    inventory = InventoryBundle(many=True)
    outbound_findings = OutboundFinding(
        many=True, max_length=v11_limits.MAX_OUTBOUND_FINDINGS
    )
    warnings = WarningOut(many=True)
    provider_disclosure = ProviderDisclosure()


class SecretFindingReview(HubV11Serializer):
    finding_id = s.CharField(max_length=128)
    reason = s.CharField(max_length=2000)


class SecretReview(HubV11Serializer):
    fingerprint = Fingerprint()
    false_positive_secret_findings = SecretFindingReview(
        many=True, max_length=v11_limits.MAX_OUTBOUND_FINDINGS
    )

    def validate_false_positive_secret_findings(self, value):
        if len({item["finding_id"] for item in value}) != len(value):
            raise s.ValidationError("Review each finding once.")
        return value


class FileClassification(HubV11Serializer):
    skill_id = s.UUIDField()
    path = s.CharField(max_length=255, trim_whitespace=False)
    sha256 = s.RegexField(r"^[a-f0-9]{64}$")
    classification = s.ChoiceField(
        choices=["reference_material", "evaluation_material"]
    )
    reason = s.CharField(max_length=2000)


class InventoryReview(SecretReview):
    reviewed_all_files = TrueAcknowledgment()
    no_known_answer_keys = Boolean()
    classifications = FileClassification(many=True, max_length=1400)


class CreateTestRunRequest(HubV11Serializer):
    configuration = RunConfig()
    expected_fingerprint = Fingerprint()
    acknowledge_provider_processing = TrueAcknowledgment()
    inventory_review = InventoryReview()

    def validate(self, attrs):
        if attrs["expected_fingerprint"] != attrs["inventory_review"]["fingerprint"]:
            raise SkillsHubError(
                "invalid_inventory_review", "Review the exact preview plan.", status=400
            )
        return attrs


class TestRunAck(JobAck):
    visibility = s.ChoiceField(choices=["private"])
    attempt_count = Integer()
    execution_fingerprint = Fingerprint(
        help_text="Hash of the plan fingerprint plus canonical accepted review."
    )


class RunCounts(HubV11Serializer):
    planned = Integer()
    finished = Integer()
    answered = Integer()
    graded = Integer()
    errors = Integer()
    incomplete = Integer()
    not_started = Integer()


class Decision(HubV11Serializer):
    state = s.ChoiceField(
        choices=[
            "works_on_examples",
            "needs_changes",
            "inconclusive",
            "routing_only",
            "diagnostic",
        ]
    )
    improved_cases = Integer()
    regressed_cases = Integer(
        help_text="Includes mixed cases (positive and negative deltas across selected baselines)."
    )
    unchanged_cases = Integer()
    unmatched_cases = Integer()


class AnswerComparison(HubV11Serializer):
    baseline = s.ChoiceField(choices=["production", "without_target"])
    matched_cases = Integer()
    expected_cases = Integer()
    delta_percentage_points = s.FloatField(allow_null=True)


class RunSummary(HubV11Serializer):
    id = s.UUIDField()
    status = s.ChoiceField(choices=JOB_STATUSES)
    profile = s.ChoiceField(choices=["quick", "repeated"])
    visibility = s.ChoiceField(choices=["private", "workspace"])
    created_at = s.DateTimeField()
    started_at = s.DateTimeField(allow_null=True)
    finished_at = s.DateTimeField(allow_null=True)
    deadline_at = s.DateTimeField()
    parent_run_id = s.UUIDField(allow_null=True)
    execution_fingerprint = Fingerprint()
    execution_configuration = ExecutionConfiguration()
    candidate = SnapshotIdentity()
    production = SnapshotIdentity(allow_null=True)
    competitors = SnapshotIdentity(many=True)
    suite_revision = Integer(allow_null=True)
    selected_case_ids = s.ListField(child=s.UUIDField())
    full_suite = Boolean()
    repetitions = Integer()
    counts = RunCounts()
    decision = Decision()
    answer_comparisons = AnswerComparison(many=True)
    validity = s.ChoiceField(choices=["reviewed", "diagnostic"])
    validity_reasons = s.ListField(child=s.CharField())
    currentness = Currentness()
    metrics = Metrics()
    error = ErrorOut(allow_null=True)
    payload_expires_at = s.DateTimeField(allow_null=True)
    payload_expired = Boolean()


class ResultsQuery(PageQuery):
    filter = s.ChoiceField(
        choices=[
            "all",
            "regressions",
            "wrong_selection",
            "inconsistent",
            "errors",
            "not_graded",
        ],
        default="all",
    )


class AttemptSummary(HubV11Serializer):
    id = s.UUIDField()
    repeat_index = Integer()
    status = s.ChoiceField(choices=ATTEMPT_STATUSES)
    routing_verdict = s.ChoiceField(choices=VERDICTS)
    delivery_verdict = s.ChoiceField(choices=VERDICTS)
    answer_verdict = s.ChoiceField(choices=VERDICTS)


class VariantResult(HubV11Serializer):
    variant = s.ChoiceField(choices=VARIANTS)
    planned = Integer()
    completed = Integer()
    loaded = Integer()
    answer_passed = Integer()
    answer_graded = Integer()
    delivery_passed = Integer()
    inconsistent = Boolean()
    attempts = AttemptSummary(many=True)


class CaseResult(HubV11Serializer):
    case_id = s.UUIDField()
    title = s.CharField()
    expected_trigger = Boolean(allow_null=True)
    change = s.ChoiceField(
        choices=[
            "improved",
            "regressed",
            "unchanged",
            "mixed",
            "unmatched",
            "not_graded",
        ]
    )
    variants = VariantResult(many=True)
    # Expose each comparison rather than concealing opposite baseline outcomes.
    answer_comparisons = AnswerComparison(many=True)


class CheckResult(HubV11Serializer):
    check_id = s.UUIDField()
    category = s.ChoiceField(choices=["answer", "delivery"])
    verdict = s.ChoiceField(choices=["pass", "fail", "not_applicable", "unverifiable"])
    expected = s.JSONField(allow_null=True)
    actual = s.JSONField(allow_null=True)
    evidence = s.CharField(allow_null=True, allow_blank=True)
    reason = s.CharField(allow_null=True, allow_blank=True)


class Receipt(HubV11Serializer):
    call_id = s.UUIDField()
    tool_call_id = s.CharField()
    skill_id = s.UUIDField()
    version_id = s.UUIDField(allow_null=True)
    digest = Digest()
    path = s.CharField()
    sha256 = s.CharField(allow_null=True)
    offset = Integer()
    next_offset = Integer(allow_null=True)
    total_chars = Integer(allow_null=True)
    eof = Boolean(allow_null=True)
    delivered_bytes = Integer()
    outcome = s.ChoiceField(choices=["delivered", "rejected"])
    error_code = s.CharField(allow_null=True)


class CallSummary(HubV11Serializer):
    id = s.UUIDField()
    sequence = Integer()
    kind = s.ChoiceField(choices=["task", "judge", "assist"])
    state = s.ChoiceField(
        choices=["prepared", "in_flight", "completed", "failed", "unknown"]
    )
    requested_model = s.CharField()
    returned_model = s.CharField(allow_null=True)
    assistant_message = s.DictField(allow_null=True)
    finish_reason = s.CharField(allow_null=True)
    provider_request_id = s.CharField(allow_null=True)
    usage = UsageOut()
    cost = CostOut()
    latency_ms = Integer(allow_null=True)
    error = ErrorOut(allow_null=True)


class AttemptDetail(AttemptSummary):
    case = CaseOut()
    variant = s.ChoiceField(choices=VARIANTS)
    answer = s.CharField(allow_null=True, allow_blank=True)
    target_requested = Boolean()
    target_loaded = Boolean(allow_null=True)
    root_fully_delivered = Boolean(allow_null=True)
    checks = CheckResult(many=True)
    receipts = Receipt(many=True)
    calls = CallSummary(many=True)
    metrics = Metrics()
    error = ErrorOut(allow_null=True)


class SnapshotFileQuery(HubV11Serializer):
    snapshot_skill_id = s.UUIDField()
    variant = s.ChoiceField(choices=VARIANTS)
    path = s.CharField(max_length=255, trim_whitespace=False)
    offset = Integer(min_value=0, default=0)
    max_bytes = Integer(min_value=1, max_value=49152, default=24000)


class SnapshotFileOut(FileChunk):
    class Meta:
        ref_name = "SkillsHubV11SnapshotFileOut"

    skill_id = s.UUIDField()
    version_id = s.UUIDField(allow_null=True)
    digest = Digest()


class CancelAck(HubV11Serializer):
    id = s.UUIDField()
    status = s.ChoiceField(choices=JOB_STATUSES)


class ShareRunRequest(HubV11Serializer):
    acknowledge_snapshot_sharing = TrueAcknowledgment()


class ShareRunOut(HubV11Serializer):
    id = s.UUIDField()
    visibility = s.ChoiceField(choices=["workspace"])
    shared_at = s.DateTimeField()


class EvidenceQuery(HubV11Serializer):
    candidate = s.ChoiceField(choices=["draft", "version"])
    revision = Integer(min_value=1, required=False)
    version = Integer(min_value=1, required=False)

    def validate(self, attrs):
        return _branch(
            attrs,
            {"candidate", "revision" if attrs["candidate"] == "draft" else "version"},
        )


class EvidenceRun(HubV11Serializer):
    run_id = s.UUIDField()
    status = s.ChoiceField(choices=JOB_STATUSES)
    visibility = s.ChoiceField(choices=["private", "workspace"])
    currentness = Currentness()
    validity = s.ChoiceField(choices=["reviewed", "diagnostic"])
    selected_cases = Integer()
    suite_cases = Integer(allow_null=True)
    repetitions = Integer()
    answer_regressions = Integer()
    routing_failures = Integer()
    delivery_failures = Integer()
    error_attempts = Integer()
    created_at = s.DateTimeField()
    can_attach = Boolean()


class TestEvidenceOut(HubV11Serializer):
    candidate_digest = Digest()
    current_release_revision = Integer()
    runs = EvidenceRun(many=True)
    reason_if_none = s.CharField(allow_null=True)


_ASSIST_COMMON = {"kind", "model", "max_estimated_cost_usd"}
_ASSIST_FIELDS = {
    "draft": _ASSIST_COMMON
    | {"revision", "expected_digest", "instruction", "context_text"},
    "description": _ASSIST_COMMON
    | {"revision", "expected_digest", "source_run_id", "case_ids"},
    "summary": _ASSIST_COMMON | {"from_version", "to"},
}


class AssistInput(HubV11Serializer):
    """Non-paid preview input; no job, acknowledgment or release fields."""

    kind = s.ChoiceField(choices=["draft", "description", "summary"])
    revision = Integer(min_value=1, required=False)
    expected_digest = Digest(required=False)
    instruction = s.CharField(trim_whitespace=False, required=False)
    context_text = s.CharField(trim_whitespace=False, allow_blank=True, required=False)
    source_run_id = s.UUIDField(required=False)
    case_ids = s.ListField(
        child=s.UUIDField(), min_length=1, max_length=20, required=False
    )
    from_version = Integer(min_value=1, allow_null=True, required=False)
    to = CandidateRef(required=False)
    model = ModelConfig()
    max_estimated_cost_usd = USD(min_value=Decimal("0.00000001"))

    def validate(self, attrs):
        _branch(attrs, _ASSIST_FIELDS[attrs["kind"]])
        if attrs["max_estimated_cost_usd"] > Decimal(
            v11_limits.effective_limits()["max_estimated_cost_usd"]
        ):
            raise s.ValidationError("Spend stop exceeds the deployment ceiling.")
        if "case_ids" in attrs and len(set(attrs["case_ids"])) != len(
            attrs["case_ids"]
        ):
            raise s.ValidationError("Select each source case once.")
        return attrs


class CreateAssistRequest(AssistInput):
    expected_input_fingerprint = Fingerprint()
    acknowledge_provider_processing = TrueAcknowledgment()
    outbound_review = SecretReview()

    def validate(self, attrs):
        extras = {
            key: attrs[key]
            for key in (
                "expected_input_fingerprint",
                "acknowledge_provider_processing",
                "outbound_review",
            )
        }
        base = {key: value for key, value in attrs.items() if key not in extras}
        super().validate(base)
        if (
            extras["expected_input_fingerprint"]
            != extras["outbound_review"]["fingerprint"]
        ):
            raise SkillsHubError(
                "invalid_inventory_review",
                "Review the exact assistance preview.",
                status=400,
            )
        return attrs


class AssistProviderDisclosure(HubV11Serializer):
    provider = s.CharField()


class AssistAllowance(HubV11Serializer):
    input_bytes = Integer()
    max_input_bytes = Integer()
    max_total_tokens = Integer()
    max_output_tokens = Integer()
    max_output_bytes = Integer()
    max_files = Integer()
    deadline_seconds = Integer()


class AssistPreview(HubV11Serializer):
    input_fingerprint = Fingerprint()
    provider_disclosure = AssistProviderDisclosure()
    allowance = AssistAllowance()
    estimate = Estimate()
    outbound_findings = OutboundFinding(
        many=True, max_length=v11_limits.MAX_OUTBOUND_FINDINGS
    )


class AssistAck(JobAck):
    kind = s.ChoiceField(choices=["draft", "description", "summary"])
    base_revision = Integer(allow_null=True)
    base_digest = Digest()


class AssistProposedContent(HubV11Serializer):
    files = FileInput(many=True, allow_null=True)
    description = s.CharField(allow_null=True)
    summary = s.CharField(allow_null=True)


class AssistProposalOut(HubV11Serializer):
    id = s.UUIDField()
    status = s.ChoiceField(choices=JOB_STATUSES)
    kind = s.ChoiceField(choices=["draft", "description", "summary"])
    base_revision = Integer(allow_null=True)
    base_digest = Digest()
    input_fingerprint = Fingerprint()
    proposal = AssistProposedContent(allow_null=True)
    missing_information = s.ListField(child=s.CharField())
    checks = ChecksResult(allow_null=True)
    exposed_case_ids = s.ListField(child=s.UUIDField())
    metrics = Metrics()
    error = ErrorOut(allow_null=True)
    payload_expired = Boolean()


class ApplyAssistRequest(HubV11Serializer):
    revision = Integer(min_value=1)
    expected_digest = Digest()


class GitHubPreviewRequest(HubV11Serializer):
    """Initial {url,path,ref} OR continuation {parent_preview_id,path}."""

    url = s.CharField(max_length=2048, required=False)
    path = s.CharField(
        max_length=1024, allow_null=True, allow_blank=True, required=False
    )
    ref = s.CharField(max_length=255, allow_null=True, required=False)
    parent_preview_id = s.UUIDField(required=False)

    def validate(self, attrs):
        if "parent_preview_id" in attrs:
            _branch(attrs, {"parent_preview_id", "path"})
            if attrs["path"] is None:
                raise s.ValidationError("Select a discovered root path.")
        else:
            if "url" not in attrs:
                raise s.ValidationError("Provide a repository URL.")
            attrs.setdefault("path", None)
            attrs.setdefault("ref", None)
            _branch(attrs, {"url", "path", "ref"})
        return attrs


class GitHubSourceIdentity(HubV11Serializer):
    repo = s.CharField(allow_blank=True)
    repository_id = Integer(allow_null=True)
    path = s.CharField(allow_blank=True)
    ref = s.CharField(allow_blank=True)
    ref_kind = s.ChoiceField(choices=["branch", "tag", "commit", "default"])
    commit_sha = s.CharField(allow_null=True)


class GitHubBundleFile(HubV11Serializer):
    path = s.CharField()
    sha256 = s.CharField()
    size_bytes = Integer()


class GitHubBundle(HubV11Serializer):
    name = s.CharField()
    digest = Digest()
    files = GitHubBundleFile(many=True)
    checks = ChecksResult()


class LicenseFile(HubV11Serializer):
    path = s.CharField()
    sha256 = s.CharField()
    url_at_commit = s.CharField()
    text = s.CharField(allow_blank=True)


class LicenseEvidence(HubV11Serializer):
    status = s.ChoiceField(choices=["identified", "unknown", "custom", "restricted"])
    declared = s.CharField(allow_null=True)
    files = LicenseFile(many=True)
    requires_admin_review = Boolean()


class ConsumedPreview(HubV11Serializer):
    skill_id = s.UUIDField()
    draft_revision = Integer()


class GitHubPreviewOut(HubV11Serializer):
    id = s.UUIDField()
    status = s.ChoiceField(choices=JOB_STATUSES)
    operation = s.ChoiceField(choices=["import", "update"])
    expires_at = s.DateTimeField(allow_null=True)
    source = GitHubSourceIdentity()
    outcome = s.ChoiceField(
        choices=[
            "pending",
            "needs_path",
            "ready",
            "unchanged_commit",
            "unchanged_content",
            "changed",
            "pinned",
            "source_missing",
            "blocked",
            "error",
        ]
    )
    roots = s.ListField(child=s.CharField())
    bundle = GitHubBundle(allow_null=True)
    license = LicenseEvidence()
    diff = CompareResult(allow_null=True)
    local_diverged = Boolean()
    source_revision = Integer(allow_null=True)
    expected_latest_version = Integer(allow_null=True)
    expected_latest_digest = Digest(allow_null=True)
    can_stage = Boolean()
    error = ErrorOut(allow_null=True)
    retry_at = s.DateTimeField(allow_null=True)
    consumed = ConsumedPreview(allow_null=True)


class RightsReview(HubV11Serializer):
    confirmed = TrueAcknowledgment()
    review_note = s.CharField(max_length=2000, allow_blank=True)


class StageGitHubImportRequest(HubV11Serializer):
    preview_id = s.UUIDField()
    rights = RightsReview()


class SourceCheckRequest(HubV11Serializer):
    expected_source_revision = Integer(min_value=1)


class StageSourceUpdateRequest(StageGitHubImportRequest):
    revision = s.ChoiceField(choices=[0])
    expected_source_revision = Integer(min_value=1)
    expected_latest_version = Integer(min_value=1)
    expected_latest_digest = Digest()
    accept_replacement = Boolean()


class GitHubSourceOut(HubV11Serializer):
    provider = s.ChoiceField(choices=["github"])
    revision = Integer()
    repo = s.CharField()
    repository_id = Integer()
    path = s.CharField(allow_blank=True)
    ref = s.CharField()
    ref_kind = s.ChoiceField(choices=["branch", "tag", "commit", "default"])
    imported_sha = s.CharField()
    upstream_digest = Digest()
    associated_version = Integer()
    checked_sha = s.CharField(allow_null=True)
    checked_at = s.DateTimeField(allow_null=True)
    check_status = s.CharField(allow_null=True)


class SourceDraftOut(DraftOut):
    class Meta:
        ref_name = "SkillsHubV11SourceDraftOut"

    source = s.DictField(
        help_text="Server-verified provenance; not accepted by draft PUT."
    )


class StageSourceOut(HubV11Serializer):
    draft = SourceDraftOut()
    published = s.ChoiceField(choices=[False])


class StageGitHubImportOut(StageSourceOut):
    skill = SkillSummary()


class ProtectionRequest(HubV11Serializer):
    protected_label_names = s.ListField(
        child=s.RegexField(v1_limits.LABEL_RE, max_length=40), max_length=20
    )
    expected_release_revision = Integer(min_value=0)

    def validate_protected_label_names(self, value):
        if "latest" in value or len(value) != len(set(value)):
            raise s.ValidationError("Use unique assignable labels; latest is reserved.")
        return value


class ProtectionOut(HubV11Serializer):
    protected_label_names = s.ListField(child=s.CharField())
    release_revision = Integer()
    can_manage = Boolean()


class PolicyEventOut(HubV11Serializer):
    old_names = s.ListField(child=s.CharField())
    new_names = s.ListField(child=s.CharField())
    from_revision = Integer()
    to_revision = Integer()
    actor = HistoryActor(allow_null=True)
    created_at = s.DateTimeField()


class AdoptionQuery(PageQuery):
    label = s.RegexField(v1_limits.LABEL_RE, default="production")
    days = s.ChoiceField(choices=[7, 30, 90], default=7)


class AdoptionTarget(HubV11Serializer):
    label = s.CharField()
    version = Integer()
    version_id = s.UUIDField()
    promotion_event_id = s.UUIDField(allow_null=True)
    promoted_at = s.DateTimeField(allow_null=True)


class AdoptionWindow(HubV11Serializer):
    start = s.DateTimeField()
    end = s.DateTimeField()
    as_of = s.DateTimeField()


class AdoptionCounts(HubV11Serializer):
    current_only = Integer()
    other_only = Integer()
    mixed = Integer()
    not_seen = Integer()
    ambiguous = Integer()


class ObservedVersion(HubV11Serializer):
    version = Integer()
    version_id = s.UUIDField()
    last_at = s.DateTimeField()


class ObservedConsumer(HubV11Serializer):
    consumer_id = s.CharField()
    consumer_label = s.CharField()
    channel = s.ChoiceField(choices=["api", "download", "mcp"])
    state = s.ChoiceField(
        choices=["current_only", "other_only", "mixed", "not_seen", "ambiguous"]
    )
    versions = ObservedVersion(many=True)
    last_at = s.DateTimeField()


class AdoptionOut(HubV11Serializer):
    target = AdoptionTarget(allow_null=True)
    window = AdoptionWindow()
    coverage = s.ChoiceField(choices=["observed_fetches_only"])
    historical_timestamp_quality = s.ChoiceField(choices=["best_effort"])
    summary = AdoptionCounts()
    results = ObservedConsumer(many=True)
    count = Integer()
    page = Integer()
    page_size = Integer()
    total_pages = Integer()


def page_of(item: type[s.Serializer]) -> type[s.Serializer]:
    return type(
        f"{item.__name__}Page",
        (HubV11Serializer,),
        {
            "results": item(many=True),
            "count": Integer(),
            "page": Integer(),
            "page_size": Integer(),
            "total_pages": Integer(),
        },
    )


RunPage = page_of(RunSummary)
CaseResultPage = page_of(CaseResult)
PolicyEventPage = page_of(PolicyEventOut)
