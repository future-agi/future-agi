"""Joint evidence, target and retained-input contracts for round three."""

from rest_framework import serializers as s

from model_hub.serializers.skills_hub_v11 import USD, Integer, ModelConfig
from model_hub.serializers.skills_hub_v12 import ContextSerializer, Digest


class PackagePin(ContextSerializer):
    name = s.CharField(max_length=200)
    version = s.CharField(max_length=100)
    sha256 = s.RegexField(r"^[a-f0-9]{64}$")


class NativeConfiguration(ContextSerializer):
    code_commit = s.RegexField(r"^[a-f0-9]{40,64}$")
    code_digest = Digest()
    entrypoint = s.CharField(max_length=255)
    image_digest = Digest()
    runtime_version = s.CharField(max_length=100)
    packages = PackagePin(many=True, min_length=1, max_length=200)
    package_lock_digest = Digest()
    model = ModelConfig()
    connection_id = s.UUIDField()
    connection_revision = s.CharField(max_length=100)
    tools_digest = Digest()
    retrieval_fixture_digest = Digest()
    state_schema = s.CharField(max_length=100)
    checkpoint_digest = Digest()
    side_effect_policy = s.ChoiceField(
        choices=["read_only", "fixture_only", "idempotent_approved"]
    )
    reset_entrypoint = s.CharField(max_length=255)
    context_mount = s.ChoiceField(choices=["/fi_context/"])
    observable_boundaries = s.ListField(
        child=s.ChoiceField(
            choices=[
                "catalog",
                "child_selection",
                "root_delivery",
                "file_delivery",
                "model_request",
                "tool_result",
                "checkpoint",
            ]
        ),
        min_length=1,
        max_length=7,
    )


class TextConfiguration(ContextSerializer):
    model = ModelConfig()
    judge = ModelConfig(allow_null=True)
    harness_version = s.CharField(max_length=100)
    adapter_version = s.CharField(max_length=100)
    evaluator_version = s.CharField(max_length=100)


class EvidenceConfiguration(ContextSerializer):
    schema = Integer(min_value=1, max_value=1)
    digest = Digest()
    native = NativeConfiguration(required=False)
    text = TextConfiguration(required=False)

    def validate(self, attrs):
        if ("native" in attrs) == ("text" in attrs):
            raise s.ValidationError("Choose exactly one configuration identity.")
        return attrs


class EvidenceSource(ContextSerializer):
    def get_fields(self):
        return {
            **super().get_fields(),
            "source": s.ChoiceField(choices=["skills_text", "simulation_native"]),
        }


class EvidenceRef(EvidenceSource):
    schema = Integer(min_value=1, max_value=1)
    source_id = s.UUIDField()
    subject_kind = s.ChoiceField(choices=["skill", "agent_context"])
    subject_id = s.UUIDField()
    candidate_digest = Digest()
    execution_fingerprint = Digest()
    status = s.ChoiceField(
        choices=["completed", "failed", "cancelled", "timed_out", "interrupted"]
    )
    visibility = s.ChoiceField(choices=["private", "workspace"])
    payload_expires_at = s.DateTimeField()
    configuration = EvidenceConfiguration()
    annotation_revision = Integer(min_value=0)

    def validate(self, attrs):
        key = "native" if attrs["source"] == "simulation_native" else "text"
        if key not in attrs["configuration"]:
            raise s.ValidationError("Evidence and configuration kinds must agree.")
        return attrs


class EvidenceSelection(EvidenceSource):
    source_id = s.UUIDField()
    annotation_revision = Integer(min_value=0)


class EvidenceInput(ContextSerializer):
    evidence_run_id = s.UUIDField(required=False, allow_null=True)
    evidence_refs = EvidenceSelection(many=True, max_length=20, required=False)

    def validate(self, attrs):
        if "evidence_run_id" in attrs and "evidence_refs" in attrs:
            raise s.ValidationError(
                "Use the legacy evidence field or typed references, not both."
            )
        refs = attrs.get("evidence_refs", [])
        if len({(ref["source"], ref["source_id"]) for ref in refs}) != len(refs):
            raise s.ValidationError("Choose each evidence source once.")
        return attrs


class TargetConfiguration(ContextSerializer):
    binding_id = s.UUIDField(allow_null=True)
    environment_id = s.CharField(max_length=200, allow_null=True)
    revision = Integer(min_value=0)
    configuration_digest = Digest()

    def validate(self, attrs):
        if (attrs["binding_id"] is None) == (attrs["environment_id"] is None):
            raise s.ValidationError("Name exactly one binding or environment.")
        return attrs


class PointerTransition(ContextSerializer):
    def get_fields(self):
        return {
            **super().get_fields(),
            "label": s.RegexField(r"^[a-z0-9][a-z0-9-]{0,39}$"),
        }

    expected_version = Integer(min_value=1, allow_null=True)
    version = Integer(min_value=1)
    expected_release_revision = Integer(min_value=0)
    expected_security_revision = Integer(min_value=0)
    expected_impact_fingerprint = Digest()


class ExternalPipelineReference(ContextSerializer):
    url = s.URLField(max_length=2000)
    configuration_identity = s.CharField(max_length=255)
    decision = s.CharField(max_length=2000)
    trust = s.ChoiceField(choices=["operator_recorded_unimported"])
    recorded_by = s.UUIDField()


class ReleasePacket(ContextSerializer):
    schema = Integer(min_value=1, max_value=1)
    subject_kind = s.ChoiceField(choices=["skill", "agent_context"])
    subject_id = s.UUIDField()
    candidate_version_id = s.UUIDField()
    candidate_digest = Digest()
    transition = PointerTransition()
    target = TargetConfiguration(allow_null=True)
    evidence = EvidenceRef(many=True, max_length=20)
    external_pipeline = ExternalPipelineReference(allow_null=True)


class ChildSelection(ContextSerializer):
    skill_id = s.UUIDField()
    expectation = s.ChoiceField(choices=["required", "forbidden", "optional"])


class NativeSelection(ContextSerializer):
    child_selection = ChildSelection(many=True, max_length=20)
    catalog_digest = Digest()
    invocation_mode = s.ChoiceField(choices=["natural", "forced"])

    def validate_child_selection(self, rows):
        if len({row["skill_id"] for row in rows}) != len(rows):
            raise s.ValidationError("Use unique child identities.")
        return rows


class HumanAnnotation(EvidenceSource):
    source_id = s.UUIDField()
    annotation_revision = Integer(min_value=0)
    check_id = s.UUIDField(allow_null=True)
    attempt_id = s.UUIDField(allow_null=True)
    verdict = s.ChoiceField(choices=["agree", "disagree", "cannot_assess"])
    cited_text = s.CharField(max_length=2000, allow_blank=True, trim_whitespace=False)
    comment = s.CharField(max_length=2000, allow_blank=True, trim_whitespace=False)


class RetainedPlanAcceptance(ContextSerializer):
    plan_id = s.UUIDField()
    expected_fingerprint = Digest()
    item_key = s.CharField(max_length=255)
    mode = s.ChoiceField(choices=["snapshot", "patch"])
    expected_head_version = Integer(min_value=1, allow_null=True)
    expected_head_digest = Digest(allow_null=True)
    rights_confirmed = s.BooleanField()
    tags = s.ListField(
        child=s.RegexField(r"^[a-z0-9][a-z0-9._-]{0,39}$"),
        max_length=20,
        required=False,
    )


class CheckpointRestore(ContextSerializer):
    checkpoint_id = s.UUIDField()
    revision = Integer(min_value=0)
    expected_digest = Digest()


class SpendPolicyInput(ContextSerializer):
    revision = Integer(min_value=0)
    enabled = s.BooleanField()
    period_limit_usd = USD(min_value=0)
