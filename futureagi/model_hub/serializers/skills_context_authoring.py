"""Human context authoring and guarded campaign HTTP contracts."""

from rest_framework import serializers as s

from model_hub.serializers import skills_hub as leaf
from model_hub.serializers import skills_hub_v12 as ctx
from model_hub.serializers.skills_hub_v11 import Integer


class Create(ctx.ContextSerializer):
    name = s.RegexField(r"^[a-z0-9][a-z0-9-]{0,63}$")
    description = s.CharField(min_length=1, max_length=1024, trim_whitespace=False)
    tags = s.ListField(child=s.CharField(max_length=50), max_length=20)
    files = s.ListField(
        child=leaf.FileInput(), allow_null=True, min_length=1, max_length=200
    )


class Revision(ctx.ContextSerializer):
    revision = Integer(min_value=1)


class Receipt(Revision):
    context_id = s.UUIDField()
    draft_id = s.UUIDField()
    digest = ctx.Digest(required=False)
    published = s.BooleanField(required=False)


class Draft(ctx.ContextDraftOut):
    id = s.UUIDField()
    recovery_digest = ctx.Digest()
    head_acknowledged = s.BooleanField()
    review_fingerprint = s.CharField(allow_blank=True)


class OwnDraft(Revision):
    base_version = Integer(min_value=1, allow_null=True)


class Detail(ctx.ContextSerializer):
    def get_fields(self):
        return {**super().get_fields(), "context": ctx.ContextSummary()}

    release_state = ctx.ContextReleaseState()
    latest = leaf.VersionSummary(allow_null=True)
    own_draft = OwnDraft(allow_null=True)


class Page(ctx.ContextSerializer):
    count = Integer(min_value=0)
    page = Integer(min_value=1)
    page_size = Integer(min_value=1)
    total_pages = Integer(min_value=0)


class ContextPage(Page):
    results = ctx.ContextSummary(many=True)


class DependencyChange(ctx.ContextSerializer):
    skill_id = s.UUIDField()
    mount = s.CharField()
    change = s.CharField()
    before = ctx.LockedLink(allow_null=True)
    after = ctx.LockedLink(allow_null=True)
    upstream_version = Integer(min_value=1, allow_null=True)


class DependencyPlan(Revision):
    fingerprint = ctx.Digest()
    candidate_digest = ctx.Digest()
    resolved_lock = ctx.ResolvedLock()
    checks = leaf.ChecksResult()
    changes = DependencyChange(many=True)


class Capabilities(ctx.ContextSerializer):
    campaigns_enabled = s.BooleanField()


class CampaignTargetInput(ctx.ContextSerializer):
    binding_id = s.UUIDField()
    version = Integer(min_value=1)


class CampaignCreate(ctx.ContextSerializer):
    name = s.CharField(min_length=1, max_length=120)
    targets = CampaignTargetInput(many=True, min_length=1, max_length=100)
    parent_campaign_id = s.UUIDField(allow_null=True)


class Campaign(ctx.ContextSerializer):
    id = s.UUIDField()
    name = s.CharField()
    revision = Integer(min_value=1)
    status = s.CharField()
    target_count = Integer(min_value=0)
    manifest_digest = ctx.Digest(allow_null=True)
    parent_campaign_id = s.UUIDField(allow_null=True)
    authorized_at = s.DateTimeField(allow_null=True)
    reason = s.CharField(allow_blank=True)
    created_at = s.DateTimeField()
    provenance = s.ChoiceField(choices=["client_reported"])


class CampaignDetail(Campaign):
    enabled = s.BooleanField()
    can_authorize = s.BooleanField()


class CampaignPage(Page):
    results = Campaign(many=True)


class CampaignChange(ctx.ContextSerializer):
    path = s.CharField()
    action = s.ChoiceField(choices=["add", "remove", "replace"])
    before_sha256 = s.CharField(allow_null=True)
    after_sha256 = s.CharField(allow_null=True)
    ownership = s.CharField()
    diff_preview = s.CharField(allow_blank=True)


class CampaignPlan(ctx.ContextSerializer):
    campaign_revision = Integer(min_value=1)
    binding_revision = Integer(min_value=1)
    repository_id = s.CharField()
    base_commit = s.CharField()
    previous_lock_digest = ctx.Digest(allow_null=True)
    desired_version_id = s.UUIDField()
    materialization_digest = ctx.Digest()
    plan_digest = ctx.Digest()
    local_review_required = s.BooleanField()
    blockers = s.ListField(child=s.CharField())
    changes = CampaignChange(many=True)


class AttemptReceipt(ctx.ContextSerializer):
    state = s.CharField()
    repository_commit = s.CharField(allow_null=True)
    pull_request_number = Integer(min_value=1, allow_null=True)
    reason = s.CharField(allow_blank=True)


class Attempt(ctx.ContextSerializer):
    attempt_id = s.UUIDField()
    claim_generation = Integer(min_value=1)
    expires_at = s.DateTimeField()
    mode = s.CharField()
    state = s.CharField()
    branch = s.CharField()
    marker = s.CharField()
    receipt = AttemptReceipt(allow_null=True)


class CampaignTarget(ctx.ContextSerializer):
    binding_id = s.UUIDField()
    name = s.CharField()
    binding_revision = Integer(min_value=1)
    selector = ctx.Selector()
    desired_version = Integer(min_value=1)
    desired_version_id = s.UUIDField()
    desired_digest = ctx.Digest()
    repository = ctx.RepositoryIdentity(allow_null=True)
    plan = CampaignPlan(allow_null=True)
    plan_digest = ctx.Digest(allow_null=True)
    decision = s.CharField()
    reason = s.CharField(allow_blank=True)
    attempt = Attempt(allow_null=True)
    pr_state = s.CharField()
    merge_state = s.CharField()
    provenance = s.ChoiceField(choices=["client_reported"])


class CampaignTargets(Page):
    results = CampaignTarget(many=True)


class CampaignDecision(ctx.ContextSerializer):
    binding_id = s.UUIDField()
    plan_digest = ctx.Digest(allow_null=True)
    decision = s.ChoiceField(choices=["include", "exclude"])
    reason = s.CharField(max_length=500, allow_blank=True)


class CampaignAuthorization(Revision):
    expected_manifest_digest = ctx.Digest()
    decisions = CampaignDecision(many=True, min_length=1, max_length=100)
    acknowledge_repository_writes = s.BooleanField()


class CampaignStatus(Revision):
    reason = s.CharField(min_length=1, max_length=500)


class Observation(ctx.ContextSerializer):
    id = s.UUIDField()
    version_id = s.UUIDField()
    digest = ctx.Digest()
    observed_at = s.DateTimeField()
    origin = s.CharField()
    stale = s.BooleanField()


class Observations(ctx.ContextSerializer):
    binding_id = s.UUIDField()
    coverage = s.ChoiceField(choices=["declared_binding"])
    installed = Observation(allow_null=True)
    discovered = Observation(allow_null=True)
    instructions_delivered = Observation(allow_null=True)


class RollbackTarget(ctx.ContextSerializer):
    binding_id = s.UUIDField()
    previous_lock_digest = ctx.Digest(allow_null=True)
    base_commit = s.CharField(allow_null=True)
    state = s.CharField()
    blockers = s.ListField(child=s.CharField())
    reverse_changes = CampaignChange(many=True)


class Rollback(Revision):
    parent_campaign_id = s.UUIDField()
    executable = s.BooleanField()
    targets = RollbackTarget(many=True)


class PlanRecorded(Revision):
    target = CampaignTarget()
    plan_digest = ctx.Digest()


class ClaimInput(ctx.ContextSerializer):
    plan_digest = ctx.Digest()


class Claim(ctx.ContextSerializer):
    attempt_id = s.UUIDField()
    claim_generation = Integer(min_value=1)
    expires_at = s.DateTimeField()
    approved_plan_digest = ctx.Digest()
    mode = s.CharField()
    branch = s.CharField()
    marker = s.CharField()
    replay = s.BooleanField()
    campaign_status = s.CharField()


class AttemptInput(AttemptReceipt):
    attempt_id = s.UUIDField()
    claim_generation = Integer(min_value=1)


class ReleaseReviewInput(ctx.ContextSerializer):
    operation = s.ChoiceField(
        choices=["publish", "move", "restore", "remove", "archive"]
    )
    target_version = Integer(min_value=1, allow_null=True, default=None)
    evidence_run_id = s.UUIDField(allow_null=True, default=None)
    revision = Integer(min_value=1, allow_null=True, default=None)
    page = Integer(min_value=1, default=1)

    def get_fields(self):
        return {
            **super().get_fields(),
            "label": s.RegexField(
                r"^[a-z0-9][a-z0-9-]{0,39}$", allow_null=True, default=None
            ),
        }


class ReviewCandidate(ctx.ContextSerializer):
    kind = s.ChoiceField(choices=["draft", "version"])
    digest = ctx.Digest()
    revision = Integer(min_value=1, required=False)
    version = Integer(min_value=1, required=False)


class ReviewFile(ctx.ContextSerializer):
    path = s.CharField()
    status = s.ChoiceField(choices=["added", "removed", "modified", "unchanged"])
    from_sha256 = s.CharField(allow_null=True)
    to_sha256 = s.CharField(allow_null=True)
    from_text_url = s.CharField(allow_null=True)
    to_text_url = s.CharField(allow_null=True)


class ReviewDiff(Page):
    to = ReviewCandidate()
    context_id = s.UUIDField()
    files = ReviewFile(many=True)
    summary = leaf.CompareSummary()

    def get_fields(self):
        return {**super().get_fields(), "from": Integer(min_value=1, allow_null=True)}


class ImpactBinding(ctx.ContextSerializer):
    id = s.UUIDField()
    revision = Integer(min_value=1)
    mode = s.CharField()
    selector = ctx.Selector()
    configuration_digest = ctx.Digest()


class ReleaseImpact(ctx.ContextSerializer):
    as_of = s.DateTimeField()
    scope = s.ChoiceField(choices=["known_references"])
    bindings = ImpactBinding(many=True)
    following = Integer(min_value=0)
    pinned = Integer(min_value=0)
    unobserved = Integer(min_value=0)
    more_available = s.BooleanField()
    fingerprint = ctx.Digest()


class ReleaseReview(ctx.ContextSerializer):
    candidate = ReviewCandidate()
    base_version = Integer(min_value=1, allow_null=True)
    latest_version = Integer(min_value=1, allow_null=True)
    release_state = ctx.ContextReleaseState()
    changes = ReviewDiff()
    head_changes = ReviewDiff()
    dependency_changes = DependencyChange(many=True)
    checks = leaf.ChecksResult()
    evidence = leaf.ReleaseEvidence(allow_null=True)
    impact = ReleaseImpact()
    head_review = ctx.HeadReview(allow_null=True)
    review_fingerprint = ctx.Digest()


class DraftFileQuery(ctx.FileQuery):
    revision = Integer(min_value=1)


class DraftChunk(leaf.FileChunk):
    context_id = s.UUIDField()
    revision = Integer(min_value=1)
    digest = ctx.Digest()
