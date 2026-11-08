"""Skills wire contracts, shared by runtime validation and OpenAPI generation."""

from __future__ import annotations

import json
from functools import cache

from django.conf import settings
from drf_yasg.utils import swagger_serializer_method
from rest_framework import serializers as s

from model_hub.services.skills_hub import limits
from model_hub.services.skills_hub.errors import SkillsHubError
from model_hub.services.skills_hub.resolve import MANIFEST_PAGE_SIZE
from model_hub.services.skills_hub.usage import fetches_7d_map
from model_hub.services.skills_hub.v11.principals import (
    principal_from_request,
    require_scope_access,
    require_skill_admin,
)
from tfc.utils.api_serializers import ManagementAPIErrorResponseSerializer
from tfc.utils.serializer_fields import StringOrObjectField


class HubSerializer(s.Serializer):
    """Namespace OpenAPI definitions without changing public wire field names."""

    Meta: type

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        if "Meta" not in cls.__dict__:
            cls.Meta = type("Meta", (), {"ref_name": f"SkillsHub{cls.__name__}"})


class EmptyRequest(HubSerializer):
    pass


class FileText(s.CharField):
    """Keep file bytes intact; the domain checker reports invalid text and paths."""

    def to_internal_value(self, data):
        if not isinstance(data, str):
            self.fail("invalid")
        return data

    def run_validators(self, value):
        # NUL and control characters must become file_not_text/path_invalid checks,
        # rather than an unrelated DRF validation code. Draft storage rechecks too.
        return None


class FileInput(HubSerializer):
    path = FileText(trim_whitespace=False, allow_blank=True)
    content = FileText(trim_whitespace=False, allow_blank=True)


class CreateSkillRequest(HubSerializer):
    name = s.RegexField(limits.NAME_RE, max_length=limits.MAX_NAME)
    description = s.CharField(max_length=limits.MAX_DESCRIPTION)
    tags = s.ListField(
        child=s.RegexField(limits.TAG_RE), max_length=limits.MAX_TAGS, default=list
    )
    template_id = s.CharField(required=False)


class TagsRequest(HubSerializer):
    tags = s.ListField(child=s.RegexField(limits.TAG_RE), max_length=limits.MAX_TAGS)


class ChecksRequest(HubSerializer):
    files = FileInput(many=True)
    name = s.CharField(required=False)


class ReleaseRequest(HubSerializer):
    expected_digest = s.RegexField(r"^sha256:[0-9a-f]{64}$", required=False)
    expected_release_revision = s.IntegerField(min_value=0, required=False)
    evidence_run_id = s.UUIDField(required=False)


class ArchiveRequest(HubSerializer):
    expected_release_revision = s.IntegerField(min_value=0, required=False)


class PublishOptions(ReleaseRequest):
    note = s.CharField(max_length=limits.MAX_NOTE, allow_blank=True, required=False)
    labels = s.ListField(
        child=s.RegexField(limits.LABEL_RE), max_length=limits.MAX_LABELS, default=list
    )
    acknowledge_warnings = s.BooleanField(default=False)
    force = s.BooleanField(default=False)


class PushRequest(ReleaseRequest):
    files = FileInput(many=True)
    note = s.CharField(max_length=limits.MAX_NOTE, allow_blank=True, default="")
    labels = s.ListField(
        child=s.RegexField(limits.LABEL_RE), max_length=limits.MAX_LABELS, default=list
    )
    tags = s.ListField(
        child=s.RegexField(limits.TAG_RE), max_length=limits.MAX_TAGS, default=list
    )
    acknowledge_warnings = s.BooleanField(default=False)


class PublishVersionRequest(PublishOptions):
    files = FileInput(many=True)
    base_version = s.IntegerField(min_value=1, allow_null=True, default=None)
    note = s.CharField(max_length=limits.MAX_NOTE, allow_blank=True, default="")


class PublishDraftRequest(PublishOptions):
    class Meta:
        # Preserve the existing draft-publish contract name when extending it.
        ref_name = "SkillsHubPublishOptions"

    revision = s.IntegerField(required=False)


class DraftRequest(HubSerializer):
    files = FileInput(many=True)
    note = s.CharField(max_length=limits.MAX_NOTE, allow_blank=True, default="")
    base_version = s.IntegerField(min_value=1, allow_null=True)
    revision = s.IntegerField(min_value=0)


class RestoreRequest(HubSerializer):
    note = s.CharField(max_length=limits.MAX_NOTE, allow_blank=True, default="")


class RemoveLabelRequest(ArchiveRequest):
    expected_version = s.IntegerField(min_value=1, allow_null=True, required=False)


class SetLabelRequest(RemoveLabelRequest, ReleaseRequest):
    version = s.IntegerField(min_value=1)
    note = s.CharField(max_length=500, allow_blank=True, default="")


class PageQuery(HubSerializer):
    page = s.IntegerField(min_value=1, default=1)
    page_size = s.IntegerField(
        min_value=1, max_value=limits.MAX_PAGE_SIZE, default=limits.DEFAULT_PAGE_SIZE
    )


class LibraryQuery(PageQuery):
    search = s.CharField(required=False, allow_blank=True)
    tag = s.CharField(required=False, allow_blank=True)
    label = s.CharField(required=False, allow_blank=True)  # type: ignore[assignment]
    sort = s.ChoiceField(choices=["updated", "name", "created"], default="updated")
    order = s.ChoiceField(choices=["asc", "desc"], default="desc")


class SelectorQuery(HubSerializer):
    label = s.CharField(required=False)  # type: ignore[assignment]  # DRF removes field declarations.
    version = s.IntegerField(min_value=1, required=False)

    def validate(self, attrs):
        if "label" in attrs and "version" in attrs:
            raise s.ValidationError("Choose a label or a version, not both.")
        return attrs


class ResolveQuery(SelectorQuery):
    name = s.CharField()
    include_content = s.BooleanField(default=False)
    file_offset = s.IntegerField(
        min_value=0,
        max_value=limits.MAX_FILES,
        default=0,
        help_text="Zero-based file offset. Pin version when following next_file_offset.",
    )
    file_limit = s.IntegerField(
        min_value=1,
        max_value=MANIFEST_PAGE_SIZE,
        required=False,
        help_text="Files per manifest page (1–25). REST defaults to all files; MCP defaults to 25.",
    )


class DownloadQuery(SelectorQuery):
    names = s.CharField(required=False)
    tag = s.CharField(required=False)

    def validate(self, attrs):
        attrs = super().validate(attrs)
        names = list(dict.fromkeys(filter(None, attrs.get("names", "").split(","))))
        if not names and not attrs.get("tag"):
            raise s.ValidationError("Choose skill names or a tag.")
        if len(names) > limits.MAX_DOWNLOAD_SKILLS:
            raise SkillsHubError(
                "payload_too_large", "Download up to 50 skills at a time.", status=413
            )
        if "version" in attrs and (len(names) != 1 or attrs.get("tag")):
            raise s.ValidationError(
                "A pinned version requires exactly one name and no tag."
            )
        return attrs


class FileQuery(HubSerializer):
    path = s.CharField(trim_whitespace=False)
    offset = s.IntegerField(min_value=0, default=0)
    max_bytes = s.IntegerField(
        min_value=1,
        max_value=limits.MAX_CHUNK_BYTES,
        default=limits.DEFAULT_CHUNK_BYTES,
    )


class CompareQuery(HubSerializer):
    # 'from' is a Python keyword, not a different wire field.
    def get_fields(self):
        return {
            "from": s.RegexField(r"^(draft|[1-9][0-9]*)$"),
            "to": s.RegexField(r"^(draft|[1-9][0-9]*)$"),
        }


class HistoryQuery(PageQuery):
    label = s.CharField(required=False)  # type: ignore[assignment]  # DRF removes field declarations.


class UsageQuery(HubSerializer):
    days = s.ChoiceField(choices=[7, 30, 90], default=7)


class UploadFilesField(s.FileField):
    """Multipart repeats the `files` part; Swagger 2 describes the part once."""

    def get_value(self, dictionary):
        return (
            dictionary.getlist(self.field_name)
            if hasattr(dictionary, "getlist")
            else dictionary.get(self.field_name, s.empty)
        )

    def to_internal_value(self, data):
        if not isinstance(data, list) or not data:
            raise s.ValidationError("Upload at least one file.")
        return [super(UploadFilesField, self).to_internal_value(item) for item in data]


class ImportPreviewRequest(HubSerializer):
    files = UploadFilesField(
        allow_empty_file=True,
        help_text="Repeat this multipart part for each file. Filename is its relative POSIX path.",
    )


class ImportRequest(ImportPreviewRequest):
    selected = s.CharField(help_text="JSON array of bundle keys returned by preview.")
    labels = s.CharField(default="[]", help_text="JSON array of labels to move.")
    acknowledge_warnings = s.BooleanField(default=False)

    expected_release_revisions = s.CharField(
        default="{}",
        help_text="JSON object mapping preview bundle keys to reviewed release revisions.",
    )

    def validate(self, attrs):
        try:
            revisions = json.loads(attrs["expected_release_revisions"])
        except (ValueError, TypeError) as exc:
            raise s.ValidationError(
                {"expected_release_revisions": "Use a JSON object of revisions."}
            ) from exc
        if (
            not isinstance(revisions, dict)
            or len(revisions) > limits.MAX_IMPORT_SKILLS
            or any(
                not isinstance(key, str) or type(value) is not int or value < 0
                for key, value in revisions.items()
            )
        ):
            raise s.ValidationError(
                {
                    "expected_release_revisions": "Use a JSON object of nonnegative revisions."
                }
            )
        attrs["expected_release_revisions"] = revisions
        for field in ("selected", "labels"):
            try:
                value = json.loads(attrs[field])
            except (ValueError, TypeError) as exc:
                raise s.ValidationError(
                    {field: "Use a JSON array of strings."}
                ) from exc
            if not isinstance(value, list) or any(
                not isinstance(item, str) for item in value
            ):
                raise s.ValidationError({field: "Use a JSON array of strings."})
            attrs[field] = value
        if len(attrs["selected"]) > limits.MAX_IMPORT_SKILLS:
            raise SkillsHubError(
                "payload_too_large", "Import up to 50 skills at a time.", status=413
            )
        return attrs


class Person(HubSerializer):
    class Meta:
        # Swagger 2 references discard field-level nullability; inline nullable DTOs.
        ref_name = None

    id = s.UUIDField()
    name = s.CharField(allow_blank=True)


class Check(HubSerializer):
    code = s.CharField()
    severity = s.ChoiceField(choices=["error", "warning", "info"])
    message = s.CharField()
    path = s.CharField(required=False)
    line = s.IntegerField(required=False)
    detail = s.JSONField(required=False)


class CheckSummary(HubSerializer):
    errors = s.IntegerField()  # type: ignore[assignment]
    warnings = s.IntegerField()
    infos = s.IntegerField()


class Tokens(HubSerializer):
    metadata = s.IntegerField()
    skill_md = s.IntegerField()
    total = s.IntegerField()


class ChecksResult(HubSerializer):
    items = Check(many=True)
    summary = CheckSummary()
    tokens = Tokens()
    frontmatter = s.JSONField(allow_null=True)


class LabelSummary(HubSerializer):
    name = s.CharField()
    version = s.IntegerField(source="version.number")


def _principal(context):
    request = context["request"]
    principal = getattr(request, "skill_principal", None)
    if principal is None:
        principal = principal_from_request(request)
        request.skill_principal = principal
    return principal


def _release_permissions(context, skill):
    cache_key = (str(skill.pk), skill.release_revision)
    cached = context.setdefault("release_permissions", {})
    if cache_key not in cached:
        principal = _principal(context)
        writer = admin = False
        try:
            require_scope_access(principal=principal, write=True)
            writer = True
            require_skill_admin(principal=principal, skill=skill)
            admin = True
        except SkillsHubError:
            pass
        assigned = any(
            label.name in skill.protected_label_names for label in skill.labels.all()
        )
        cached[cache_key] = {
            "writer": writer,
            "can_manage_label_protection": admin,
            "can_archive": writer and (not assigned or admin),
            "can_run_tests": writer
            and principal.kind != "system_key"
            and bool(getattr(settings, "SKILLS_TESTING_ENABLED", False)),
        }
    return cached[cache_key]


class LabelOut(LabelSummary):
    version_id = s.UUIDField()
    updated_at = s.DateTimeField()
    updated_by = Person(allow_null=True)
    is_protected = s.SerializerMethodField()
    can_move = s.SerializerMethodField()
    can_remove = s.SerializerMethodField()
    release_revision = s.IntegerField(source="skill.release_revision")

    def get_is_protected(self, obj) -> bool:
        return obj.name in obj.skill.protected_label_names

    def get_can_move(self, obj) -> bool:
        permissions = _release_permissions(self.context, obj.skill)
        return bool(
            permissions["can_manage_label_protection"]
            if self.get_is_protected(obj)
            else permissions["writer"]
        )

    def get_can_remove(self, obj) -> bool:
        return self.get_can_move(obj)


class VersionSummary(HubSerializer):
    class Meta:
        ref_name = None

    id = s.UUIDField()
    number = s.IntegerField()
    digest = s.CharField()
    note = s.CharField(allow_blank=True)
    created_at = s.DateTimeField()
    created_by = Person(allow_null=True)
    file_count = s.IntegerField()
    total_bytes = s.IntegerField()
    labels = s.SerializerMethodField()
    source = s.JSONField()  # type: ignore[assignment]

    @swagger_serializer_method(serializer_or_field=s.ListField(child=s.CharField()))
    def get_labels(self, obj):
        return sorted(label.name for label in obj.labels.all())


class FileMetadata(HubSerializer):
    path = s.CharField()
    size_bytes = s.IntegerField()
    sha256 = s.CharField()


class VersionFile(FileMetadata):
    token_estimate = s.IntegerField()


class VersionDetail(VersionSummary):
    frontmatter = s.JSONField()
    checks = ChecksResult()
    files = VersionFile(many=True)


class SkillSummary(HubSerializer):
    release_revision = s.IntegerField()
    protected_label_names = s.ListField(child=s.CharField())
    id = s.UUIDField()
    name = s.CharField()
    description = s.CharField()
    tags = s.ListField(child=s.CharField())
    latest_version = s.SerializerMethodField()
    labels = LabelSummary(many=True)
    has_draft = s.SerializerMethodField()
    updated_at = s.DateTimeField()
    updated_by = Person(allow_null=True)
    created_at = s.DateTimeField()
    fetches_7d = s.SerializerMethodField()
    file_count = s.SerializerMethodField()

    @swagger_serializer_method(serializer_or_field=s.IntegerField(allow_null=True))
    def get_latest_version(self, obj):
        return obj.latest_version_number or None

    def get_has_draft(self, obj) -> bool:
        principal = _principal(self.context)
        return principal.kind != "system_key" and any(
            draft.user_id == principal.user_id for draft in obj.drafts.all()
        )

    def get_fetches_7d(self, obj) -> int:
        counts = self.context.get("fetch_counts")
        return (
            counts.get(obj.pk, 0)
            if counts is not None
            else fetches_7d_map([obj.pk]).get(obj.pk, 0)
        )

    def _latest_version(self, obj):
        versions = self.context.get("latest_versions")
        return (
            versions.get(obj.pk)
            if versions is not None
            else obj.versions.filter(number=obj.latest_version_number)
            .select_related("created_by")
            .prefetch_related("labels")
            .first()
        )

    @swagger_serializer_method(serializer_or_field=s.IntegerField(allow_null=True))
    def get_file_count(self, obj):
        latest = self._latest_version(obj)
        return latest.file_count if latest else None


class DraftSummary(HubSerializer):
    user = Person()
    updated_at = s.DateTimeField()
    base_version = s.IntegerField(allow_null=True)
    is_mine = s.SerializerMethodField()

    def get_is_mine(self, obj) -> bool:
        principal = _principal(self.context)
        return principal.kind != "system_key" and obj.user_id == principal.user_id


class SkillPermissions(HubSerializer):
    can_manage_label_protection = s.BooleanField()
    can_archive = s.BooleanField()
    can_run_tests = s.BooleanField()


class SkillDetail(SkillSummary):
    source = s.JSONField()  # type: ignore[assignment]
    created_by = Person(allow_null=True)
    labels = LabelOut(many=True)
    latest = s.SerializerMethodField()
    drafts = s.SerializerMethodField()
    permissions = s.SerializerMethodField()

    @swagger_serializer_method(serializer_or_field=DraftSummary(many=True))
    def get_drafts(self, obj):
        if _principal(self.context).kind == "system_key":
            return []
        return DraftSummary(obj.drafts.all(), many=True, context=self.context).data

    @swagger_serializer_method(serializer_or_field=SkillPermissions())
    def get_permissions(self, obj):
        return SkillPermissions(_release_permissions(self.context, obj)).data

    @swagger_serializer_method(serializer_or_field=VersionSummary(allow_null=True))
    def get_latest(self, obj):
        latest = self._latest_version(obj)
        return VersionSummary(latest).data if latest else None


class Stats(HubSerializer):
    total = s.IntegerField()
    in_production = s.IntegerField()
    fetches_7d = s.IntegerField()
    my_drafts = s.IntegerField()


class Page(HubSerializer):
    count = s.IntegerField()
    page = s.IntegerField()
    page_size = s.IntegerField()
    total_pages = s.IntegerField()


class SkillPage(Page):
    results = SkillSummary(many=True)
    stats = Stats()


class VersionPage(Page):
    results = VersionSummary(many=True)


class Requested(HubSerializer):
    label = s.CharField(required=False)  # type: ignore[assignment]  # DRF removes field declarations.
    version = s.IntegerField(required=False)


class ManifestFile(FileMetadata):
    content = s.CharField(required=False, allow_blank=True, trim_whitespace=False)


class Manifest(HubSerializer):
    skill_id = s.UUIDField()
    name = s.CharField()
    description = s.CharField()
    version = s.IntegerField()
    version_id = s.UUIDField()
    digest = s.CharField()
    requested = Requested()
    frontmatter = s.JSONField(
        required=False,
        help_text="REST only. MCP callers read the original frontmatter from SKILL.md in chunks.",
    )
    files = ManifestFile(many=True)
    file_count = s.IntegerField()
    file_offset = s.IntegerField()
    next_file_offset = s.IntegerField(allow_null=True)
    created_at = s.DateTimeField()


class FileChunk(HubSerializer):
    path = s.CharField()
    sha256 = s.CharField()
    size_bytes = s.IntegerField()
    total_chars = s.IntegerField()
    offset = s.IntegerField()
    next_offset = s.IntegerField(allow_null=True)
    eof = s.BooleanField()
    text = s.CharField(allow_blank=True, trim_whitespace=False)


class DraftOut(HubSerializer):
    def get_fields(self):
        return {**super().get_fields(), "source": s.JSONField()}

    base_version = s.IntegerField(allow_null=True)
    files = FileInput(many=True)
    note = s.CharField(allow_blank=True)
    revision = s.IntegerField()
    updated_at = s.DateTimeField()


class EvidenceCurrentness(HubSerializer):
    content = s.BooleanField()
    baseline = s.BooleanField(allow_null=True)
    suite = s.BooleanField(allow_null=True)
    competitors = s.BooleanField(allow_null=True)
    payload_available = s.BooleanField()


class ReleaseEvidence(HubSerializer):
    class Meta:
        ref_name = None

    run_id = s.UUIDField()
    currentness = EvidenceCurrentness()
    validity = s.ChoiceField(choices=["reviewed", "diagnostic"])


class PublishResult(HubSerializer):
    release_revision = s.IntegerField()
    evidence = ReleaseEvidence(allow_null=True)
    version = VersionSummary()
    unchanged = s.BooleanField()
    checks = ChecksResult()


class PushResult(HubSerializer):
    release_revision = s.IntegerField()
    evidence = ReleaseEvidence(allow_null=True)
    skill = SkillSummary()
    version = VersionSummary()
    unchanged = s.BooleanField()


class HistoryActor(Person):
    class Meta:
        ref_name = None

    kind = s.ChoiceField(choices=["user", "api_key"])


class LabelEvent(HubSerializer):
    release_revision = s.IntegerField(allow_null=True)
    label = s.CharField()  # type: ignore[assignment]
    from_version = s.IntegerField(allow_null=True)
    to_version = s.IntegerField(allow_null=True)
    actor = s.SerializerMethodField()
    note = s.CharField(allow_blank=True)
    created_at = s.DateTimeField()

    @swagger_serializer_method(serializer_or_field=HistoryActor(allow_null=True))
    def get_actor(self, obj):
        actor = obj.actor_api_key or obj.actor
        if actor is None:
            return None
        return {
            "id": str(actor.pk),
            "name": actor.name or "",
            "kind": "api_key" if obj.actor_api_key else "user",
        }


class HistoryPage(Page):
    results = LabelEvent(many=True)


class CompareFile(HubSerializer):
    path = s.CharField()
    status = s.ChoiceField(choices=["added", "removed", "modified", "unchanged"])
    from_sha256 = s.CharField(allow_null=True)
    to_sha256 = s.CharField(allow_null=True)
    from_text = s.CharField(
        required=False, allow_null=True, allow_blank=True, trim_whitespace=False
    )
    to_text = s.CharField(
        required=False, allow_null=True, allow_blank=True, trim_whitespace=False
    )


class CompareSummary(HubSerializer):
    added = s.IntegerField()
    removed = s.IntegerField()
    modified = s.IntegerField()
    unchanged = s.IntegerField()


class CompareRef(s.JSONField):
    """The service returns integer version numbers or the literal draft."""

    class Meta:
        swagger_schema_fields = {
            "x-oneOf": [
                {"type": "integer", "minimum": 1},
                {"type": "string", "enum": ["draft"]},
            ],
        }


class CompareResult(HubSerializer):
    files = CompareFile(many=True)
    summary = CompareSummary()

    def get_fields(self):
        fields = super().get_fields()
        # Version numbers or the literal 'draft'. JSON preserves the integer.
        return {
            "from": CompareRef(
                help_text="Positive version number or the literal draft."
            ),
            "to": CompareRef(help_text="Positive version number or the literal draft."),
            **fields,
        }


class UsageDay(HubSerializer):
    day = s.DateField()
    version = s.IntegerField()
    count = s.IntegerField()


class UsageVersion(HubSerializer):
    version = s.IntegerField()
    count = s.IntegerField()
    last_at = s.DateTimeField()


class Consumer(HubSerializer):
    label = s.CharField(allow_blank=True)  # type: ignore[assignment]
    channel = s.ChoiceField(choices=["api", "download", "mcp"])
    count = s.IntegerField()
    last_at = s.DateTimeField()


class Channels(HubSerializer):
    api = s.IntegerField()
    download = s.IntegerField()
    mcp = s.IntegerField()


class Usage(HubSerializer):
    days = s.IntegerField()
    total = s.IntegerField()
    by_day = UsageDay(many=True)
    by_channel = Channels()
    by_version = UsageVersion(many=True)
    consumers = Consumer(many=True)
    last_fetched_at = s.DateTimeField(allow_null=True)


class TemplateFile(HubSerializer):
    path = s.CharField()
    size_bytes = s.IntegerField()


class Template(HubSerializer):
    id = s.CharField()
    name = s.CharField()
    title = s.CharField()
    description = s.CharField()
    tags = s.ListField(child=s.CharField())
    files = TemplateFile(many=True)


class ImportBundle(HubSerializer):
    key = s.CharField()
    root = s.CharField(allow_blank=True)  # type: ignore[assignment]
    name = s.CharField()
    file_count = s.IntegerField()
    total_bytes = s.IntegerField()
    status = s.ChoiceField(choices=["new", "changed", "unchanged", "invalid"])
    skill_id = s.UUIDField(required=False)
    latest_version = s.IntegerField(required=False, allow_null=True)
    checks = ChecksResult()


class Rejected(HubSerializer):
    path = s.CharField()
    reason = s.CharField()


class ImportPreview(HubSerializer):
    bundles = ImportBundle(many=True)
    rejected = Rejected(many=True)
    skipped = s.ListField(child=s.CharField())


class Imported(HubSerializer):
    key = s.CharField()
    name = s.CharField()
    skill_id = s.UUIDField()
    version = s.IntegerField()
    version_id = s.UUIDField()
    digest = s.CharField()


class ImportFailure(HubSerializer):
    key = s.CharField()
    name = s.CharField()
    code = s.CharField()
    message = s.CharField()


class ImportResult(HubSerializer):
    created = Imported(many=True)
    updated = Imported(many=True)
    unchanged = Imported(many=True)
    failed = ImportFailure(many=True)


class SkillsError(ManagementAPIErrorResponseSerializer):
    """Retain standard aliases and structured domain detail for release failures."""

    def get_fields(self):
        return {**super().get_fields(), "detail": StringOrObjectField(required=False)}


class ConflictDetail(HubSerializer):
    base_version = s.IntegerField(required=False, allow_null=True)
    latest_version = s.IntegerField(required=False, allow_null=True)
    current_version = s.IntegerField(required=False, allow_null=True)
    revision = s.IntegerField(required=False)
    updated_at = s.DateTimeField(required=False, allow_null=True)
    release_revision = s.IntegerField(required=False)


class SkillsConflictError(SkillsError):
    detail = ConflictDetail()  # type: ignore[assignment]


class SkillsChecksError(SkillsError):
    detail = ChecksResult()  # type: ignore[assignment]


@cache
def envelope(serializer, *, many=False):
    """Name each success envelope so generated clients preserve its exact shape."""
    return type(
        f"Skills{serializer.__name__}{'List' if many else ''}Response",
        (s.Serializer,),
        {"status": s.BooleanField(), "result": serializer(many=many)},
    )
