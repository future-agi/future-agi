"""Scoped human HTTP adapters; services retain CAS and immutable publication rules."""

from django.conf import settings
from django.db.models import Q
from rest_framework.response import Response

from model_hub.selectors.skills_hub_v12 import get_resource, resources_for_scope
from model_hub.serializers import skills_context_authoring as s
from model_hub.serializers import skills_hub as leaf
from model_hub.serializers import skills_hub_v12 as ctx
from model_hub.services.skills_hub.drafts import _tombstone
from model_hub.services.skills_hub.errors import SkillsHubError
from model_hub.services.skills_hub.r3.draft_recovery import draft_recovery_digest
from model_hub.services.skills_hub.v12 import authoring, campaigns, releases
from model_hub.views.skills_context_foundation import IDEMPOTENCY, contract, header_uuid
from model_hub.views.skills_hub import SkillsView, page


def summary(resource, principal):
    return {
        "id": resource.pk,
        "kind": resource.kind,
        "name": resource.name,
        "description": resource.description,
        "tags": resource.tags,
        "metadata_revision": resource.metadata_revision,
        "latest_version": resource.latest_version_number or None,
        "labels": [
            {
                "name": row.name,
                "version": row.version.number,
                "version_id": row.version_id,
            }
            for row in resource.labels.select_related("version").order_by("name")
        ],
        "release_revision": resource.release_revision,
        "security_revision": resource.security_revision,
        "protected_label_names": resource.protected_label_names,
        "has_draft": resource.drafts.filter(user_id=principal.user_id).exists(),
        "updated_at": resource.updated_at,
        "updated_by": resource.updated_by,
    }


class ContextHumanView(SkillsView):
    private_resource = True

    def context(self, request, context_id):
        return get_resource(
            principal=request.skill_principal,
            resource_id=context_id,
            kind="agent_context",
        )

    def draft_output(self, request, context_id, *, revision=None):
        # Serialize the same locked revision that was compiled, not a later save.
        with authoring.locked_context_graph(
            principal=request.skill_principal, context_id=context_id, write=False
        ) as (context, draft, _):
            if draft is None:
                raise SkillsHubError(
                    "draft_not_found", "Start a personal draft first.", status=404
                )
            if revision is not None:
                authoring.require_draft_revision(draft, revision)
            compiled = authoring.compile_owned_draft(context=context, draft=draft)
            return self.output(
                request,
                s.Draft,
                {
                    "id": draft.pk,
                    "revision": draft.revision,
                    "base_version": draft.base_version,
                    "files": draft.files,
                    "note": draft.note,
                    "context_spec": draft.context_spec,
                    "resolved_lock": draft.resolved_lock,
                    "digest": compiled.digest,
                    "recovery_digest": draft_recovery_digest(draft),
                    "checks": compiled.checks,
                    "updated_at": draft.updated_at,
                    "expected_head_version": draft.expected_head_version,
                    "head_acknowledged": bool(draft.head_review_fingerprint),
                    "review_fingerprint": draft.head_review_fingerprint,
                },
            )


class ContextListView(ContextHumanView):
    @contract(s.ContextPage, query=leaf.LibraryQuery)
    def get(self, request):
        query = request.validated_query_data
        rows = resources_for_scope(
            scope=request.skill_principal.scope, kind="agent_context"
        )
        if query.get("search"):
            rows = rows.filter(
                Q(name__icontains=query["search"])
                | Q(description__icontains=query["search"])
            )
        if query.get("tag"):
            rows = rows.filter(tags__contains=[query["tag"]])
        if query.get("label"):
            rows = rows.filter(labels__name=query["label"])
        sort_field = {"updated": "updated_at", "created": "created_at", "name": "name"}[
            query["sort"]
        ]
        ordering = ("-" if query["order"] == "desc" else "") + sort_field
        result = page(rows.select_related("updated_by").order_by(ordering, "id"), query)
        result["results"] = [
            summary(row, request.skill_principal) for row in result["results"]
        ]
        return self.output(request, s.ContextPage, result)

    @contract(s.Receipt, body=s.Create, status=201, manual_parameters=[IDEMPOTENCY])
    def post(self, request):
        receipt = authoring.create_context(
            principal=request.skill_principal,
            idempotency_key=header_uuid(request, "Idempotency-Key"),
            **request.validated_data,
        )
        return self.output(request, s.Receipt, receipt, status=201)


class ContextDetailView(ContextHumanView):
    @contract(s.Detail)
    def get(self, request, context_id):
        context = self.context(request, context_id)
        draft = context.drafts.filter(user_id=request.skill_principal.user_id).first()
        return self.output(
            request,
            s.Detail,
            {
                "context": summary(context, request.skill_principal),
                "release_state": releases.context_release_state(
                    principal=request.skill_principal, context_id=context_id
                ),
                "latest": context.versions.filter(
                    number=context.latest_version_number
                ).first(),
                "own_draft": draft,
            },
        )


class ContextDraftView(ContextHumanView):
    @contract(s.Draft)
    def get(self, request, context_id):
        return self.draft_output(request, context_id)

    @contract(s.Draft, body=ctx.DraftSave)
    def put(self, request, context_id):
        draft = authoring.save_context_draft(
            principal=request.skill_principal,
            context_id=context_id,
            **request.validated_data,
        )
        return self.draft_output(request, context_id, revision=draft.revision)

    @contract(body=s.Revision, status=204)
    def delete(self, request, context_id):
        with authoring.locked_context_graph(
            principal=request.skill_principal, context_id=context_id
        ) as (_, draft, _):
            draft = authoring.require_draft_revision(
                draft, request.validated_data["revision"]
            )
            _tombstone(draft)
        return Response(status=204)


class ContextDraftStartView(ContextHumanView):
    @contract(s.Draft, body=ctx.DraftStart)
    def post(self, request, context_id):
        draft = authoring.start_context_draft(
            principal=request.skill_principal,
            context_id=context_id,
            **request.validated_data,
        )
        return self.draft_output(request, context_id, revision=draft.revision)


class ContextDependenciesPreviewView(ContextHumanView):
    @contract(s.DependencyPlan, body=ctx.DependencyPreview)
    def post(self, request, context_id):
        return self.output(
            request,
            s.DependencyPlan,
            authoring.preview_dependencies(
                principal=request.skill_principal,
                context_id=context_id,
                **request.validated_data,
            ),
        )


class ContextDependenciesApplyView(ContextHumanView):
    @contract(s.Receipt, body=ctx.DependencyApply, manual_parameters=[IDEMPOTENCY])
    def post(self, request, context_id):
        return self.output(
            request,
            s.Receipt,
            authoring.apply_dependencies(
                principal=request.skill_principal,
                context_id=context_id,
                request=request.validated_data,
                idempotency_key=header_uuid(request, "Idempotency-Key"),
            ),
        )


class ContextVersionsView(ContextHumanView):
    @contract(leaf.VersionPage, query=ctx.PageQuery)
    def get(self, request, context_id):
        context = self.context(request, context_id)
        return self.output(
            request,
            leaf.VersionPage,
            page(context.versions.order_by("-number"), request.validated_query_data),
        )


class ContextVersionView(ContextHumanView):
    @contract(leaf.VersionDetail)
    def get(self, request, context_id, number):
        context = self.context(request, context_id)
        version = context.versions.filter(number=number).first()
        if version is None:
            raise SkillsHubError("version_not_found", "Version not found.", status=404)
        return self.output(request, leaf.VersionDetail, version)


class ContextHeadReviewView(ContextHumanView):
    @contract(ctx.HeadReview, body=s.Revision)
    def post(self, request, context_id):
        return self.output(
            request,
            ctx.HeadReview,
            authoring.review_context_head(
                principal=request.skill_principal,
                context_id=context_id,
                **request.validated_data,
            ),
        )


class ContextHeadAcknowledgeView(ContextHumanView):
    @contract(s.Draft, body=ctx.HeadAcknowledgment)
    def post(self, request, context_id):
        draft = authoring.acknowledge_context_head(
            principal=request.skill_principal,
            context_id=context_id,
            **request.validated_data,
        )
        return self.draft_output(request, context_id, revision=draft.revision)


class ContextPublishView(ContextHumanView):
    @contract(
        leaf.PublishResult,
        body=ctx.ContextPublish,
        status=201,
        manual_parameters=[IDEMPOTENCY],
    )
    def post(self, request, context_id):
        return self.published(
            request,
            authoring.publish_context_draft(
                principal=request.skill_principal,
                context_id=context_id,
                idempotency_key=header_uuid(request, "Idempotency-Key"),
                **request.validated_data,
            ),
        )


class ContextCapabilitiesView(ContextHumanView):
    @contract(s.Capabilities)
    def get(self, request):
        return self.output(
            request,
            s.Capabilities,
            {
                "campaigns_enabled": bool(
                    getattr(settings, "SKILLS_CONTEXT_CAMPAIGNS_ENABLED", False)
                )
            },
        )


class CampaignListView(ContextHumanView):
    @contract(s.CampaignPage, query=ctx.PageQuery)
    def get(self, request):
        return self.output(
            request,
            s.CampaignPage,
            campaigns.list_campaigns(
                principal=request.skill_principal, **request.validated_query_data
            ),
        )

    @contract(
        s.Campaign, body=s.CampaignCreate, status=201, manual_parameters=[IDEMPOTENCY]
    )
    def post(self, request):
        return self.output(
            request,
            s.Campaign,
            campaigns.create_campaign(
                principal=request.skill_principal,
                request=request.validated_data,
                idempotency_key=header_uuid(request, "Idempotency-Key"),
            ),
            status=201,
        )


class CampaignDetailView(ContextHumanView):
    @contract(s.CampaignDetail)
    def get(self, request, campaign_id):
        return self.output(
            request,
            s.CampaignDetail,
            campaigns.get_campaign(
                principal=request.skill_principal, campaign_id=campaign_id
            ),
        )


class CampaignTargetsView(ContextHumanView):
    @contract(s.CampaignTargets, query=ctx.PageQuery)
    def get(self, request, campaign_id):
        return self.output(
            request,
            s.CampaignTargets,
            campaigns.list_campaign_targets(
                principal=request.skill_principal,
                campaign_id=campaign_id,
                **request.validated_query_data,
            ),
        )


class CampaignAuthorizeView(ContextHumanView):
    @contract(s.Campaign, body=s.CampaignAuthorization)
    def post(self, request, campaign_id):
        return self.output(
            request,
            s.Campaign,
            campaigns.authorize_campaign(
                principal=request.skill_principal,
                campaign_id=campaign_id,
                **request.validated_data,
            ),
        )


class CampaignStatusView(ContextHumanView):
    campaign_action = ""

    @contract(s.Campaign, body=s.CampaignStatus)
    def post(self, request, campaign_id):
        return self.output(
            request,
            s.Campaign,
            campaigns.change_campaign_status(
                principal=request.skill_principal,
                campaign_id=campaign_id,
                action=self.campaign_action,
                **request.validated_data,
            ),
        )


class CampaignRollbackView(ContextHumanView):
    @contract(s.Rollback, body=s.Revision)
    def post(self, request, campaign_id):
        return self.output(
            request,
            s.Rollback,
            campaigns.preview_campaign_rollback(
                principal=request.skill_principal,
                campaign_id=campaign_id,
                **request.validated_data,
            ),
        )


class CampaignObservationsView(ContextHumanView):
    @contract(s.Observations)
    def get(self, request, campaign_id, binding_id):
        return self.output(
            request,
            s.Observations,
            campaigns.campaign_target_observations(
                principal=request.skill_principal,
                campaign_id=campaign_id,
                binding_id=binding_id,
            ),
        )


class CampaignPlanView(ContextHumanView):
    @contract(s.PlanRecorded, body=s.CampaignPlan)
    def put(self, request, campaign_id, binding_id):
        return self.output(
            request,
            s.PlanRecorded,
            campaigns.record_target_plan(
                principal=request.skill_principal,
                campaign_id=campaign_id,
                binding_id=binding_id,
                request=request.validated_data,
            ),
        )


class CampaignClaimView(ContextHumanView):
    @contract(s.Claim, body=s.ClaimInput, manual_parameters=[IDEMPOTENCY])
    def post(self, request, campaign_id, binding_id):
        return self.output(
            request,
            s.Claim,
            campaigns.claim_campaign_target(
                principal=request.skill_principal,
                campaign_id=campaign_id,
                binding_id=binding_id,
                idempotency_key=header_uuid(request, "Idempotency-Key"),
                **request.validated_data,
            ),
        )


class CampaignAttemptView(ContextHumanView):
    @contract(s.Attempt, body=s.AttemptInput)
    def post(self, request, campaign_id, binding_id):
        return self.output(
            request,
            s.Attempt,
            campaigns.record_campaign_attempt(
                principal=request.skill_principal,
                campaign_id=campaign_id,
                binding_id=binding_id,
                request=request.validated_data,
            ),
        )


class ContextReleaseReviewView(ContextHumanView):
    @contract(s.ReleaseReview, body=s.ReleaseReviewInput)
    def post(self, request, context_id):
        return self.output(
            request,
            s.ReleaseReview,
            releases.review_context_release(
                principal=request.skill_principal,
                context_id=context_id,
                **request.validated_data,
            ),
        )


class ContextDraftFileView(ContextHumanView):
    @contract(s.DraftChunk, query=s.DraftFileQuery)
    def get(self, request, context_id):
        response = self.output(
            request,
            s.DraftChunk,
            authoring.read_context_draft_file(
                principal=request.skill_principal,
                context_id=context_id,
                **request.validated_query_data,
            ),
        )
        response["Cache-Control"] = "private, no-store"
        return response
