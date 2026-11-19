"""Public retained migration review and reversible export contracts."""

from rest_framework import serializers as s

from model_hub.serializers import skills_hub as legacy
from model_hub.serializers import skills_hub_v11 as v11
from model_hub.serializers import skills_hub_v12 as v12
from model_hub.serializers.skills_workflows import IntakeComparison


class LangfuseMember(v12.ContextSerializer):
    resource = s.CharField(max_length=200)
    revision = s.CharField(max_length=200)
    digest = v12.Digest()
    files = legacy.FileInput(many=True)


class LangfuseExport(v12.ContextSerializer):
    format = s.ChoiceField(choices=["langfuse-skills-v1"])
    origin = s.CharField(max_length=255)
    project = s.CharField(max_length=200)
    skills = LangfuseMember(many=True, min_length=1, max_length=50)


class MigrationReviewInput(v12.ContextSerializer):
    item_key = s.CharField(max_length=255)
    expected_fingerprint = v12.Digest()


class MigrationReview(v12.ContextSerializer):
    source_hub = legacy.CompareResult()
    base_source = legacy.CompareResult(allow_null=True)
    base_hub = legacy.CompareResult(allow_null=True)
    basis_payload_available = v11.Boolean()
    basis = IntakeComparison()
    expected_head_version = v11.Integer(allow_null=True)
    expected_head_digest = v12.Digest(allow_null=True)
    has_draft = v11.Boolean()


class MigrationMapping(v12.ContextSerializer):
    id = s.UUIDField()
    source_origin = s.CharField()
    source_project = s.CharField()
    source_resource = s.CharField()
    source_revision = s.CharField()
    source_digest = v12.Digest()
    relation = s.ChoiceField(choices=["identical", "derived"])
    destination_version_id = s.UUIDField()
    destination_digest = v12.Digest()


class MigrationExit(v12.ContextSerializer):
    source_modified = v11.Boolean()
    credentials_revoked = v11.Boolean()
    local_removal = s.ChoiceField(choices=["reviewed_client_plan_required"])


class MigrationExport(v12.ContextSerializer):
    archive_base64 = s.CharField()
    mapping_receipts = MigrationMapping(many=True)
    resource_id = s.UUIDField()
    version_id = s.UUIDField()
    digest = v12.Digest()
    exit = MigrationExit()
