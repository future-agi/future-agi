from rest_framework import serializers

from simulate.models import (
    Skill,
    SkillAttachment,
    SkillKind,
    SkillPromotion,
    SkillTag,
    SkillTagName,
    SkillTest,
    SkillVersion,
)
from simulate.services.skills import MAX_FILES, MAX_PATH_LENGTH


class LibrarySkillFileSerializer(serializers.Serializer):
    path = serializers.CharField(max_length=MAX_PATH_LENGTH)
    content = serializers.CharField(allow_blank=True, trim_whitespace=False)


class LibrarySkillCreateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=64)
    kind = serializers.ChoiceField(choices=SkillKind.choices, default=SkillKind.SKILL)
    description = serializers.CharField(
        max_length=1024, required=False, allow_blank=True, default=""
    )
    note = serializers.CharField(
        max_length=255, required=False, allow_blank=True, default=""
    )
    files = LibrarySkillFileSerializer(
        many=True, allow_empty=False, max_length=MAX_FILES
    )


class LibrarySkillVersionCreateSerializer(serializers.Serializer):
    note = serializers.CharField(
        max_length=255, required=False, allow_blank=True, default=""
    )
    files = LibrarySkillFileSerializer(
        many=True, allow_empty=False, max_length=MAX_FILES
    )


class LibrarySkillPromoteSerializer(serializers.Serializer):
    version = serializers.IntegerField(min_value=1)
    tag = serializers.ChoiceField(choices=SkillTagName.choices)
    reason = serializers.CharField(
        max_length=1000, required=False, allow_blank=True, default=""
    )


class LibrarySkillTagSerializer(serializers.ModelSerializer):
    version = serializers.IntegerField(source="skill_version.version_number")

    class Meta:
        model = SkillTag
        fields = ["name", "version", "updated_at"]


class LibrarySkillVersionSerializer(serializers.ModelSerializer):
    created_by = serializers.CharField(
        source="created_by.name", allow_null=True, default=None
    )

    class Meta:
        model = SkillVersion
        fields = [
            "id",
            "version_number",
            "description",
            "note",
            "files_hash",
            "files_manifest",
            "size",
            "created_by",
            "created_at",
        ]


class LibrarySkillPromotionSerializer(serializers.ModelSerializer):
    version = serializers.IntegerField(source="skill_version.version_number")
    created_by = serializers.CharField(
        source="created_by.name", allow_null=True, default=None
    )

    class Meta:
        model = SkillPromotion
        fields = ["version", "tag", "tested", "reason", "created_by", "created_at"]


class LibrarySkillListItemSerializer(serializers.ModelSerializer):
    latest_version = serializers.IntegerField(allow_null=True)
    tags = LibrarySkillTagSerializer(many=True)
    used_by = serializers.IntegerField()
    last_test_version = serializers.IntegerField(allow_null=True)
    last_test_state = serializers.CharField(allow_null=True)
    last_test_verdict = serializers.CharField(allow_null=True, allow_blank=True)

    class Meta:
        model = Skill
        fields = [
            "id",
            "name",
            "kind",
            "description",
            "latest_version",
            "tags",
            "used_by",
            "last_test_version",
            "last_test_state",
            "last_test_verdict",
            "updated_at",
        ]


class LibrarySkillListQuerySerializer(serializers.Serializer):
    search = serializers.CharField(
        max_length=64, required=False, allow_blank=True, default=""
    )
    page = serializers.IntegerField(min_value=1, required=False, default=1)
    limit = serializers.IntegerField(
        min_value=1, max_value=100, required=False, default=25
    )


class LibrarySkillListResponseSerializer(serializers.Serializer):
    count = serializers.IntegerField()
    next = serializers.CharField(allow_null=True)
    previous = serializers.CharField(allow_null=True)
    results = LibrarySkillListItemSerializer(many=True)
    total_pages = serializers.IntegerField()
    current_page = serializers.IntegerField()


class LibrarySkillUsageSerializer(serializers.ModelSerializer):
    agent_definition_id = serializers.UUIDField()
    agent_name = serializers.CharField(source="agent_definition.agent_name")
    version = serializers.IntegerField(
        source="skill_version.version_number", allow_null=True, default=None
    )

    class Meta:
        model = SkillAttachment
        fields = ["agent_definition_id", "agent_name", "tag", "version", "updated_at"]


class LibrarySkillDetailSerializer(serializers.ModelSerializer):
    versions = LibrarySkillVersionSerializer(many=True, source="version_rows")
    attachments = LibrarySkillUsageSerializer(many=True, source="attachment_rows")
    tags = LibrarySkillTagSerializer(many=True)
    promotions = LibrarySkillPromotionSerializer(many=True, source="promotion_rows")
    created_by = serializers.CharField(
        source="created_by.name", allow_null=True, default=None
    )

    class Meta:
        model = Skill
        fields = [
            "id",
            "name",
            "kind",
            "description",
            "versions",
            "tags",
            "promotions",
            "attachments",
            "created_by",
            "created_at",
            "updated_at",
        ]


class LibrarySkillVersionFilesSerializer(serializers.Serializer):
    version = serializers.IntegerField()
    files = LibrarySkillFileSerializer(many=True)


class LibrarySkillAttachSerializer(serializers.Serializer):
    agent_definition_id = serializers.UUIDField()
    tag = serializers.ChoiceField(
        choices=SkillTagName.choices, required=False, allow_null=True, default=None
    )
    version = serializers.IntegerField(
        min_value=1, required=False, allow_null=True, default=None
    )


class LibrarySkillDetachSerializer(serializers.Serializer):
    agent_definition_id = serializers.UUIDField()


class LibrarySkillAttachmentSerializer(serializers.ModelSerializer):
    agent_definition_id = serializers.UUIDField()
    version = serializers.IntegerField(
        source="skill_version.version_number", allow_null=True, default=None
    )

    class Meta:
        model = SkillAttachment
        fields = ["skill_id", "agent_definition_id", "tag", "version", "updated_at"]


class LibrarySkillAttachedQuerySerializer(serializers.Serializer):
    agent_definition_id = serializers.UUIDField()


class LibrarySkillAttachedSerializer(serializers.Serializer):
    skill_id = serializers.UUIDField()
    name = serializers.CharField()
    kind = serializers.ChoiceField(choices=SkillKind.choices)
    tag = serializers.CharField(allow_null=True)
    pinned_version = serializers.IntegerField(allow_null=True)
    resolved_version = serializers.IntegerField(allow_null=True)
    attached_at = serializers.DateTimeField()


class LibrarySkillTestCreateSerializer(serializers.Serializer):
    version = serializers.IntegerField(min_value=1)
    agent_definition_id = serializers.UUIDField()
    environment_id = serializers.UUIDField()
    scenario_keys = serializers.ListField(
        child=serializers.CharField(max_length=255), allow_empty=True, max_length=200
    )
    trials = serializers.IntegerField(min_value=1, max_value=20, default=1)


class LibrarySkillTestSerializer(serializers.ModelSerializer):
    version = serializers.IntegerField(source="skill_version.version_number")
    baseline_version = serializers.IntegerField(
        source="baseline_version.version_number", allow_null=True, default=None
    )
    agent_definition_id = serializers.UUIDField()
    agent_name = serializers.CharField(source="agent_definition.agent_name")
    environment_id = serializers.UUIDField()
    run_a_execution_id = serializers.UUIDField(
        source="run_a.test_execution_id", allow_null=True, default=None
    )
    run_b_execution_id = serializers.UUIDField(
        source="run_b.test_execution_id", allow_null=True, default=None
    )
    summary = serializers.JSONField(allow_null=True)
    created_by = serializers.CharField(
        source="created_by.name", allow_null=True, default=None
    )

    class Meta:
        model = SkillTest
        fields = [
            "id",
            "version",
            "baseline_version",
            "agent_definition_id",
            "agent_name",
            "environment_id",
            "run_a_execution_id",
            "run_b_execution_id",
            "scenario_keys",
            "trials",
            "state",
            "verdict",
            "summary",
            "created_by",
            "created_at",
            "finished_at",
        ]


class LibrarySkillInCodeQuerySerializer(serializers.Serializer):
    agent_definition_id = serializers.UUIDField()


class LibrarySkillInCodeItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = SkillVersion
        fields = [
            "id",
            "name",
            "kind",
            "path",
            "files_hash",
            "description",
            "readable",
            "problem",
        ]


class LibrarySkillInCodeSerializer(serializers.Serializer):
    agent_version_number = serializers.IntegerField(allow_null=True)
    status = serializers.ChoiceField(
        choices=(
            "scanned",
            "source_missing",
            "too_large",
            "not_scanned",
            "unsupported_source",
            "no_version",
        )
    )
    items = LibrarySkillInCodeItemSerializer(many=True)


class LibrarySkillInCodeFilesSerializer(serializers.Serializer):
    files = LibrarySkillFileSerializer(many=True)


class LibrarySkillCopySerializer(serializers.Serializer):
    agent_definition_id = serializers.UUIDField()
