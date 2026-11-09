"""Version-bound release packets, reviewer inbox and execution receipts."""

from rest_framework import serializers as s

from model_hub.serializers import skills_hub_r3 as r3
from model_hub.serializers import skills_hub_v12 as ctx
from model_hub.serializers.skills_context_authoring import Page, Revision
from model_hub.serializers.skills_hub_v11 import Integer


class Preview(ctx.ContextSerializer):
    resource_id = s.UUIDField()
    kind = s.ChoiceField(choices=["skill", "agent_context"])
    candidate_version_id = s.UUIDField()
    target = r3.TargetConfiguration(allow_null=True, default=None)
    evidence = r3.EvidenceSelection(many=True, max_length=20, default=list)
    external_pipeline = r3.ExternalPipelineReference(allow_null=True, default=None)

    def get_fields(self):
        return {
            **super().get_fields(),
            "label": s.RegexField(r"^[a-z0-9][a-z0-9-]{0,39}$"),
        }


class Packet(ctx.ContextSerializer):
    packet = r3.ReleasePacket()
    fingerprint = ctx.Digest()


class Submit(ctx.ContextSerializer):
    packet = r3.ReleasePacket()
    expected_fingerprint = ctx.Digest()
    recipient_ids = s.ListField(child=s.UUIDField(), min_length=1, max_length=20)
    request_id = s.UUIDField(allow_null=True, default=None)
    revision = Integer(min_value=0, default=0)


class RequestReceipt(Revision):
    id = s.UUIDField()
    status = s.CharField()


class RequestDetail(RequestReceipt, Packet):
    author_id = s.UUIDField()
    subject_id = s.UUIDField()


class Decision(Revision):
    expected_fingerprint = ctx.Digest()
    decision = s.ChoiceField(choices=["approve", "request_changes"])
    comment = s.CharField(max_length=2000, allow_blank=True, trim_whitespace=False)


class DecisionReceipt(RequestReceipt):
    request_id = s.UUIDField()
    decision = s.ChoiceField(choices=["approve", "request_changes"])


class Execute(Revision):
    expected_fingerprint = ctx.Digest()


class CurrentEvidence(ctx.ContextSerializer):
    def get_fields(self):
        return {**super().get_fields(), "source": s.CharField()}

    source_id = s.UUIDField()
    annotation_revision = Integer(min_value=0)
    currentness = s.DictField(child=s.BooleanField(allow_null=True))


class Currentness(ctx.ContextSerializer):
    packet_fingerprint = ctx.Digest()
    release_revision = Integer(min_value=0)
    security_revision = Integer(min_value=0)
    target = r3.TargetConfiguration(allow_null=True)
    evidence = CurrentEvidence(many=True)


class PostMove(ctx.ContextSerializer):
    version = Integer(min_value=1)
    release_revision = Integer(min_value=0)
    event_id = s.UUIDField(allow_null=True)

    def get_fields(self):
        return {**super().get_fields(), "label": s.CharField()}


class ExecutionReceipt(Revision):
    request_id = s.UUIDField()
    decision_id = s.UUIDField()
    executed_by = s.UUIDField()
    actor_api_key_id = s.UUIDField(allow_null=True)
    currentness_at_action = Currentness()
    post_move_state = PostMove()


class InboxQuery(ctx.PageQuery):
    status = s.ChoiceField(
        choices=["pending", "approved", "changes_requested", "released"], required=False
    )


class InboxItem(RequestReceipt):
    subject_id = s.UUIDField()
    subject_kind = s.ChoiceField(choices=["skill", "agent_context"])
    name = s.CharField()
    author_id = s.UUIDField()
    unread = s.BooleanField()
    fingerprint = ctx.Digest()
    candidate_version_id = s.UUIDField()
    updated_at = s.DateTimeField()


class Inbox(Page):
    results = InboxItem(many=True)
    unread_count = Integer(min_value=0)
