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


class InspectionInput(Preview):
    expected_fingerprint = ctx.Digest()


class CapabilityDelta(ctx.ContextSerializer):
    path = s.CharField()
    category = s.CharField()
    name = s.CharField(allow_null=True)
    change = s.ChoiceField(choices=["added", "removed", "modified"])
    before = s.CharField(allow_null=True)
    after = s.CharField(allow_null=True)


class CapabilityInspection(ctx.ContextSerializer):
    before_digest = s.CharField()
    after_digest = s.CharField()
    changes = CapabilityDelta(many=True)
    scope = s.CharField()
    notice = s.CharField()
    fingerprint = ctx.Digest()


class NativeOutcomeResult(ctx.ContextSerializer):
    state = s.CharField()
    reproduction = s.CharField()
    repair_verified = s.BooleanField()


class NativeCaseOutcome(ctx.ContextSerializer):
    case_id = s.CharField()
    title = s.CharField(allow_blank=True)
    variant = s.CharField()
    version = Integer(min_value=1, allow_null=True)
    repeat_index = Integer(min_value=0)
    status = s.CharField()
    answer = s.CharField()
    selection = s.CharField()
    delivery = s.CharField()
    tools = s.CharField()


class EvidenceSummary(ctx.ContextSerializer):
    source_id = s.UUIDField()
    annotation_revision = Integer(min_value=0)
    disagreements = Integer(min_value=0)
    cannot_assess = Integer(min_value=0)
    planned = Integer(min_value=0, allow_null=True)
    graded = Integer(min_value=0, allow_null=True)
    incomplete = Integer(min_value=0, allow_null=True)
    regressions = Integer(min_value=0, allow_null=True)
    answer = s.CharField()
    selection = s.CharField()
    delivery = s.CharField()
    tools = s.CharField()
    availability = s.ChoiceField(choices=["available", "unavailable"])
    # Completed skills-native runs only: the reduced result and per-case cells.
    result = NativeOutcomeResult(allow_null=True)
    cases = NativeCaseOutcome(many=True)

    def get_fields(self):
        return {
            **super().get_fields(),
            "source": s.ChoiceField(choices=["skills_text", "simulation_native"]),
        }


class ReleaseInspection(ctx.ContextSerializer):
    fingerprint = ctx.Digest()
    capabilities = CapabilityInspection()
    summaries = EvidenceSummary(many=True)
    target_configuration = r3.NativeConfiguration(allow_null=True)

    def to_representation(self, instance):
        value = super().to_representation(instance)
        # Keep canonical integer/float spelling, like BindingOut.
        value["target_configuration"] = instance["target_configuration"]
        return value


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
    name = s.CharField()
    author_name = s.CharField()
    decided_by_name = s.CharField(allow_null=True)
    decision = s.CharField(allow_null=True)
    recipients = s.ListField(child=s.DictField())
    updated_at = s.DateTimeField()
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
    name = s.CharField(required=False)
    subject_id = s.UUIDField(required=False)
    subject_kind = s.CharField(required=False)
    executed_by_name = s.CharField(required=False)
    approved_by_name = s.CharField(required=False)
    executed_at = s.DateTimeField(required=False)
    previous_version = Integer(allow_null=True, required=False)
    request_id = s.UUIDField()
    decision_id = s.UUIDField()
    executed_by = s.UUIDField()
    actor_api_key_id = s.UUIDField(allow_null=True)
    currentness_at_action = Currentness()
    post_move_state = PostMove()


class InboxQuery(ctx.PageQuery):
    status = s.ChoiceField(
        choices=[
            "pending",
            "approved",
            "changes_requested",
            "released",
            "superseded",
        ],
        required=False,
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
    # J4: evidence retention expired; the assigned reviewer must renew explicitly.
    renewal_required = s.BooleanField()


class Inbox(Page):
    results = InboxItem(many=True)
    unread_count = Integer(min_value=0)


class ShareEvidence(ctx.ContextSerializer):
    acknowledge_snapshot_sharing = s.BooleanField()


class OptionsQuery(ctx.ContextSerializer):
    resource_id = s.UUIDField()
    kind = s.ChoiceField(choices=["skill", "agent_context"])
    candidate_version_id = s.UUIDField()


class Reviewer(ctx.ContextSerializer):
    id = s.UUIDField()
    name = s.CharField()


class ReviewTarget(ctx.ContextSerializer):
    name = s.CharField()
    target = r3.TargetConfiguration()


class OptionCounts(ctx.ContextSerializer):
    graded = Integer(min_value=0, allow_null=True)
    planned = Integer(min_value=0, allow_null=True)
    regressed = Integer(min_value=0, allow_null=True)
    disagreements = Integer(min_value=0)


class OptionEvidence(r3.EvidenceRef):
    # Display only, never part of a packet: a completed application run's reduced state.
    outcome = s.CharField(allow_null=True)
    counts = OptionCounts(allow_null=True)


class ReviewOptions(ctx.ContextSerializer):
    recipients = Reviewer(many=True)
    targets = ReviewTarget(many=True)
    evidence = OptionEvidence(many=True)


class AuthoredItem(RequestReceipt):
    updated_at = s.DateTimeField()
    subject_id = s.UUIDField()
    subject_kind = s.ChoiceField(choices=["skill", "agent_context"])
    name = s.CharField()
    author_id = s.UUIDField()
    unread = s.BooleanField()


class AuthoredPage(Page):
    results = AuthoredItem(many=True)
    unread_count = Integer(min_value=0)


# --- J4: explicit reviewer renewal ------------------------------------------
class RenewalChange(ctx.ContextSerializer):
    item = s.CharField(max_length=255)
    before = s.JSONField(allow_null=True)
    after = s.JSONField(allow_null=True)


class RemovedEvidence(ctx.ContextSerializer):
    source_id = s.UUIDField()
    reason = s.ChoiceField(choices=["evidence_expired", "resource_not_found"])

    def get_fields(self):
        return {**super().get_fields(), "source": s.CharField()}


class RenewalPreview(Packet):
    request_id = s.UUIDField()
    revision = Integer(min_value=1)
    status = s.CharField()
    subject_id = s.UUIDField()
    previous_fingerprint = ctx.Digest()
    changes = RenewalChange(many=True)
    removed_evidence = RemovedEvidence(many=True)
    # The rule values behind a changed rule counter (UXF-10). Rows whose values
    # weren't recorded at approval carry nulls; nothing is inferred.
    policy_changes = r3.PolicyChange(many=True)


class Renew(Revision):
    expected_fingerprint = ctx.Digest()


class RenewalReceipt(RequestReceipt):
    renewed_by = s.UUIDField()
    from_revision = Integer(min_value=1)
    changes = RenewalChange(many=True)
    removed_evidence = RemovedEvidence(many=True)
