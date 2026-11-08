"""Strict, versioned foundation schemas shared by HTTP and generated clients."""

from rest_framework import serializers as s

from model_hub.serializers.skills_hub import FileChunk, FileInput
from model_hub.serializers.skills_hub_v11 import HubV11Serializer, Integer
from model_hub.services.skills_hub.v12 import limits


class ContextSerializer(HubV11Serializer):
    def __init_subclass__(cls, **kwargs):
        explicit_meta = "Meta" in cls.__dict__
        super().__init_subclass__(**kwargs)
        if not explicit_meta:
            cls.Meta = type("Meta", (), {"ref_name": f"SkillsContext{cls.__name__}"})


class Digest(s.RegexField):
    def __init__(self, **kwargs):
        super().__init__(r"^sha256:[a-f0-9]{64}$", **kwargs)


class Selector(ContextSerializer):
    version = Integer(min_value=1, required=False)

    def get_fields(self):
        return {
            **super().get_fields(),
            "label": s.RegexField(r"^[a-z0-9][a-z0-9-]{0,39}$", required=False),
        }

    def validate(self, attrs):
        if len(attrs) != 1:
            raise s.ValidationError("Choose exactly one label or version.")
        return attrs


class LinkInput(ContextSerializer):
    skill_id = s.UUIDField()
    selector = Selector()


class LockedLink(ContextSerializer):
    mount = s.CharField(max_length=255)
    skill_id = s.UUIDField()
    requested = Selector()
    version = Integer(min_value=1)
    version_id = s.UUIDField()
    digest = Digest()


class ContextSpec(ContextSerializer):
    schema = Integer(min_value=1, max_value=1)
    description = s.CharField(min_length=1, max_length=1024, trim_whitespace=False)
    links = LinkInput(many=True, max_length=limits.MAX_LINKS)

    def validate_links(self, links):
        if len({link["skill_id"] for link in links}) != len(links):
            raise s.ValidationError("Link each skill once.")
        return links


class ResolvedLock(ContextSerializer):
    schema = Integer(min_value=1, max_value=1)
    links = LockedLink(many=True, max_length=limits.MAX_LINKS)


class ConsumeGrant(ContextSerializer):
    kind = s.ChoiceField(choices=["skill", "agent_context"])
    resource_id = s.UUIDField()
    labels = s.ListField(
        child=s.RegexField(r"^[a-z0-9][a-z0-9-]{0,39}$"), max_length=20
    )
    version_ids = s.ListField(
        child=s.UUIDField(), max_length=limits.MAX_GRANT_SELECTORS
    )

    def validate(self, attrs):
        if not attrs["labels"] and not attrs["version_ids"]:
            raise s.ValidationError("At least one explicit selector is required.")
        if any(
            len(attrs[key]) != len(set(attrs[key])) for key in ("labels", "version_ids")
        ):
            raise s.ValidationError("Duplicate selectors are not allowed.")
        return attrs


class CredentialInput(ContextSerializer):
    purpose = s.ChoiceField(choices=["consume", "report"])
    grants = ConsumeGrant(many=True, max_length=limits.MAX_GRANTS)
    binding_ids = s.ListField(child=s.UUIDField(), max_length=limits.MAX_GRANTS)
    expires_at = s.DateTimeField()

    def validate(self, attrs):
        if attrs["purpose"] == "consume":
            valid = bool(attrs["grants"]) and not attrs["binding_ids"]
        else:
            valid = bool(attrs["binding_ids"]) and not attrs["grants"]
        if not valid:
            raise s.ValidationError(
                "Grant only the selectors or bindings required by this purpose."
            )
        return attrs


class CredentialMetadata(CredentialInput):
    id = s.UUIDField()
    created_at = s.DateTimeField()
    revoked_at = s.DateTimeField(allow_null=True)
    last_used_at = s.DateTimeField(allow_null=True)


class CredentialIssued(ContextSerializer):
    id = s.UUIDField()
    secret = s.CharField(
        allow_null=True, help_text="Returned once; never available on replay or list."
    )
    secret_available = s.BooleanField()
    metadata = CredentialMetadata()


class PageQuery(ContextSerializer):
    page = Integer(min_value=1, default=1)
    page_size = Integer(min_value=1, max_value=100, default=25)


class CredentialPage(ContextSerializer):
    results = CredentialMetadata(many=True)
    count = Integer()
    page = Integer()
    page_size = Integer()
    total_pages = Integer()


class ResolveQuery(ContextSerializer):
    name = s.RegexField(r"^[a-z0-9]+(-[a-z0-9]+)*$", max_length=64)
    version = Integer(min_value=1, required=False)

    def get_fields(self):
        return {
            **super().get_fields(),
            "label": s.RegexField(r"^[a-z0-9][a-z0-9-]{0,39}$", required=False),
        }

    def validate(self, attrs):
        if "version" in attrs and "label" in attrs:
            raise s.ValidationError("Choose a label or a version, not both.")
        return attrs


class ManifestQuery(ContextSerializer):
    file_offset = Integer(min_value=0, default=0)
    file_limit = Integer(min_value=1, max_value=25, default=25)


class FileQuery(ContextSerializer):
    path = s.CharField(max_length=255, trim_whitespace=False)
    offset = Integer(min_value=0, default=0)
    max_bytes = Integer(min_value=4, max_value=49152, default=32768)


class FileOrigin(ContextSerializer):
    kind = s.ChoiceField(choices=["owned", "linked", "generated"])
    skill_id = s.UUIDField(allow_null=True)
    version_id = s.UUIDField(allow_null=True)
    source_path = s.CharField(allow_null=True)


class FileMeta(ContextSerializer):
    path = s.CharField()
    sha256 = s.RegexField(r"^[a-f0-9]{64}$")
    size_bytes = Integer(min_value=0)
    origin = FileOrigin()


class ConsumptionManifest(ContextSerializer):
    resource_id = s.UUIDField()
    kind = s.ChoiceField(choices=["skill", "agent_context"])
    name = s.CharField()
    description = s.CharField()
    version = Integer(min_value=1)
    version_id = s.UUIDField()
    digest = Digest()
    requested = Selector()
    entrypoint = s.CharField()
    descriptor_path = s.CharField(allow_null=True)
    files = FileMeta(many=True)
    file_count = Integer(min_value=1)
    file_offset = Integer(min_value=0)
    next_file_offset = Integer(allow_null=True)
    created_at = s.DateTimeField()
    resolution_id = s.UUIDField()
    resolution_expires_at = s.DateTimeField()


class ConsumptionChunk(FileChunk):
    resource_id = s.UUIDField()
    version_id = s.UUIDField()
    digest = Digest()

    class Meta:
        ref_name = "SkillsContextConsumptionChunk"


class AccessCheck(ContextSerializer):
    allowed = s.BooleanField()
    version_id = s.UUIDField()
    digest = Digest()
    checked_at = s.DateTimeField()


class DraftStart(ContextSerializer):
    revision = Integer(min_value=0, max_value=0)
    from_version = Integer(min_value=1, allow_null=True)


class DraftSave(ContextSerializer):
    revision = Integer(min_value=1)
    base_version = Integer(min_value=1, allow_null=True)
    files = s.ListField(child=FileInput(), min_length=1, max_length=200)
    description = s.CharField(min_length=1, max_length=1024, trim_whitespace=False)
    note = s.CharField(max_length=2000, allow_blank=True, trim_whitespace=False)


class HeadAcknowledgment(ContextSerializer):
    revision = Integer(min_value=1)
    expected_digest = Digest()
    expected_head_version = Integer(min_value=1, allow_null=True)
    review_fingerprint = Digest()


class ContextPublish(HeadAcknowledgment):
    base_version = Integer(min_value=1, allow_null=True)
    note = s.CharField(max_length=2000, allow_blank=True, trim_whitespace=False)
    acknowledge_warnings = s.BooleanField()


class DependencyPreview(ContextSerializer):
    revision = Integer(min_value=1)
    links = LinkInput(many=True, max_length=20)
    refresh_skill_ids = s.ListField(child=s.UUIDField(), max_length=20)


class DependencyApply(DependencyPreview):
    expected_fingerprint = Digest()


class RepositoryIdentity(ContextSerializer):
    provider = s.ChoiceField(choices=["github"])
    repository_id = s.CharField(max_length=200)
    default_branch = s.CharField(max_length=255)
    project_scope = s.CharField(max_length=255, allow_blank=True)


class BindingInput(ContextSerializer):
    name = s.CharField(max_length=120)
    kind = s.ChoiceField(choices=["repository", "runtime"])
    selector = Selector()
    update_mode = s.ChoiceField(choices=["repository_lock", "manual", "new_thread"])
    profile = s.CharField(max_length=64)
    profile_version = s.CharField(max_length=64)
    repository = RepositoryIdentity(allow_null=True)
    expected_report_interval_seconds = Integer(min_value=1, max_value=31536000)


class BindingOut(BindingInput):
    id = s.UUIDField()
    context_id = s.UUIDField()
    revision = Integer(min_value=1)
    retired_at = s.DateTimeField(allow_null=True)


class TraceRef(ContextSerializer):
    project_id = s.UUIDField()
    trace_id = s.CharField(max_length=200)
    span_id = s.CharField(max_length=200)
    start_time = s.DateTimeField()


class ReceiptDetails(ContextSerializer):
    path = s.CharField(max_length=255, allow_null=True)
    sha256 = s.RegexField(r"^[a-f0-9]{64}$", allow_null=True)
    ranges = s.ListField(
        child=s.ListField(child=Integer(min_value=0), min_length=2, max_length=2),
        max_length=200,
    )
    runtime_version = s.CharField(max_length=100, allow_null=True)
    adapter_version = s.CharField(max_length=100)
    diagnostic_codes = s.ListField(child=s.CharField(max_length=64), max_length=20)
    trace_ref = TraceRef(allow_null=True)


class ReceiptInput(ContextSerializer):
    event_id = s.UUIDField()
    session_id = s.UUIDField(allow_null=True)
    instance_id = s.UUIDField()
    sequence = Integer(min_value=0)
    stage = s.ChoiceField(
        choices=[
            "resolved",
            "installed",
            "discovered",
            "instructions_delivered",
            "skill_file_delivered",
            "failed",
            "closed",
        ]
    )
    version_id = s.UUIDField()
    digest = Digest()
    materialization_digest = Digest(allow_null=True)
    observed_at = s.DateTimeField()
    details = ReceiptDetails()


class SessionInput(ContextSerializer):
    client_session_key = s.CharField(max_length=200)
    incarnation_id = s.UUIDField()
    resume_session_id = s.UUIDField(allow_null=True)
    adapter_version = s.CharField(max_length=100)
    runtime_version = s.CharField(max_length=100)
    runtime_config_digest = Digest()


class QuarantineInput(ContextSerializer):
    revision = Integer(min_value=0)
    quarantined = s.BooleanField()
    reason = s.CharField(min_length=1, max_length=2000)
    replacement_version_id = s.UUIDField(allow_null=True)


class EmptyInput(ContextSerializer):
    pass
