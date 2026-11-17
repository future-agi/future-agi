"""Signed-in views (SPEC §9.3): me, publishers, tokens, likes and reports.

Hub writes are not workspace data, so every write view carries `@workspace_read_only`; otherwise a
workspace Viewer could not like, report or publish (SPEC §1.2, §9.1).
"""

from __future__ import annotations

from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from accounts.authentication import workspace_read_only
from omega_hub.api.base import HubView, PublicView, body, bytes_response, viewer
from omega_hub.api.lookups import live_entry
from omega_hub.api.serializers import cards as card_shapes
from omega_hub.api.serializers.publishers import owned, profile
from omega_hub.errors import HubError
from omega_hub.models import Entry, HubToken, Like
from omega_hub.permissions import HubAuthenticated, IsSessionUser, TokenScope, is_hub_token, is_maintainer
from omega_hub.services import community, email_verification, publishers as publisher_service, refs, search, storage, tokens
from omega_hub.throttles import HubLikeWriteThrottle, HubReportWriteThrottle, HubTokenCreateThrottle, HubVerifyEmailThrottle
from omega_rollout import readiness
from omega_rollout.hub_gate import require_hub_part


class MeView(HubView):
    token_allowed = True

    def get(self, request):
        user = request.user
        if is_hub_token(request):
            token = request.auth
            return Response({
                "user": {"id": str(user.pk), "email": user.email, "name": user.name},
                "publishers": [owned(token.publisher)],
                "isMaintainer": False,
                "token": {"prefix": token.prefix, "publisher": token.publisher.handle,
                          "expiresAt": token.expires_at.isoformat(), "scopes": token.scopes},
            })
        return Response({
            "user": {"id": str(user.pk), "email": user.email, "name": user.name},
            "publishers": [owned(p) for p in publisher_service.publishers_for(user)],
            "isMaintainer": is_maintainer(user),
            "emailVerified": email_verification.is_verified(user),
        })


@workspace_read_only
class EmailVerificationView(HubView):
    """POST sends the confirmation link to the signed-in account's address (HUB-25)."""

    throttle_classes = [HubVerifyEmailThrottle]

    def post(self, request):
        result = email_verification.send_link(request.user)
        return Response(result, status=202 if result["sent"] else 200)


@workspace_read_only
class EmailVerificationConfirmView(HubView):
    """POST {token}: the link's token, opened while signed in as the account it was sent to."""

    def post(self, request):
        return Response(email_verification.confirm(request.user, body(request).get("token")))


@workspace_read_only
class PublishersCreateView(PublicView):
    """GET is the public index; POST creates a publisher for the signed-in user."""

    def get(self, request):
        from omega_hub.api.public_views import PublishersView

        return PublishersView.get(self, request)

    def get_permissions(self):
        if self.request.method == "POST":
            return [TokenScope(), HubAuthenticated()]
        return [TokenScope(), AllowAny()]

    def post(self, request):
        if is_hub_token(request):
            raise HubError("OMEGA_HUB_TOKEN_SCOPE", 403)
        data = body(request)
        publisher = publisher_service.create_publisher(
            request.user, handle=data.get("handle"), kind=data.get("kind"),
            display_name=data.get("displayName"), organization_id=data.get("organizationId"),
        )
        return Response(owned(publisher), status=201)


@workspace_read_only
class PublisherView(PublicView):
    def get_permissions(self):
        if self.request.method == "PATCH":
            return [TokenScope(), HubAuthenticated()]
        return [TokenScope(), AllowAny()]

    def get(self, request, handle):
        publisher = refs.publisher_or_moved(handle)
        if publisher.suspended_at is not None:
            raise HubError("OMEGA_HUB_GONE", 410)
        totals = search.publisher_totals([publisher])[publisher.pk]
        entries = list(search._base().filter(publisher=publisher))
        return Response({**profile(publisher, totals), "entries": card_shapes.cards(entries, viewer(request))})

    def patch(self, request, handle):
        if is_hub_token(request):
            raise HubError("OMEGA_HUB_TOKEN_SCOPE", 403)
        publisher = community.publisher_by_handle(handle)
        return Response(owned(publisher_service.update_profile(request.user, publisher, body(request))))


@workspace_read_only
class PublisherAvatarView(PublicView):
    def get_permissions(self):
        if self.request.method == "POST":
            return [TokenScope(), HubAuthenticated()]
        return [TokenScope(), AllowAny()]

    def get(self, request, handle):
        publisher = community.publisher_by_handle(handle)
        if not publisher.avatar_key:
            raise HubError("OMEGA_HUB_NOT_FOUND", 404)
        kind = "image/png" if publisher.avatar_key.endswith(".png") else "image/jpeg"
        return bytes_response(storage.read_bytes(publisher.avatar_key), kind,
                              {"Content-Security-Policy": "default-src 'none'", "Cache-Control": "public, max-age=300"})

    def post(self, request, handle):
        if is_hub_token(request):
            raise HubError("OMEGA_HUB_TOKEN_SCOPE", 403)
        upload = request.FILES.get("avatar") or request.FILES.get("file")
        if upload is None or upload.size > 256 * 1024:
            raise HubError("OMEGA_HUB_AVATAR_INVALID", 400)
        publisher = publisher_service.set_avatar(request.user, community.publisher_by_handle(handle), upload.read())
        return Response(owned(publisher))


@workspace_read_only
class VerificationRequestView(HubView):
    def post(self, request, handle):
        publisher = community.publisher_by_handle(handle)
        return Response(owned(publisher_service.request_verification(request.user, publisher, body(request).get("evidence"))))


def _token_row(token: HubToken) -> dict:
    return {"id": str(token.pk), "name": token.name, "prefix": token.prefix, "publisher": token.publisher.handle,
            "scopes": token.scopes, "createdAt": token.created_at.isoformat(), "expiresAt": token.expires_at.isoformat(),
            "lastUsedAt": token.last_used_at.isoformat() if token.last_used_at else None,
            "revokedAt": token.revoked_at.isoformat() if token.revoked_at else None}


@workspace_read_only
class TokensView(HubView):
    throttle_classes = [HubTokenCreateThrottle]

    def get(self, request):
        rows = HubToken.objects.select_related("publisher").filter(user=request.user)
        return Response([_token_row(token) for token in rows])

    def post(self, request):
        require_hub_part(readiness.HUB_CLI, request)  # a Hub token's only use is `omega hub login`
        data = body(request)
        publisher = community.publisher_by_handle(data.get("publisher") or "")
        days = data.get("days")
        token, plaintext = tokens.create_token(request.user, publisher, data.get("name"), days)
        return Response({**_token_row(token), "token": plaintext}, status=201)


@workspace_read_only
class TokenView(HubView):
    def delete(self, request, token_id):
        return Response(_token_row(tokens.revoke_token(request.user, token_id)))


@workspace_read_only
class LikeView(HubView):
    """Browser session only (SPEC §6.1): a Hub token or an org API key pair gets 403."""

    permission_classes = [IsSessionUser]
    throttle_classes = [HubLikeWriteThrottle]

    def post(self, request, publisher, name):
        likes, liked = community.like(request.user, live_entry(publisher, name))
        return Response({"likes": likes, "likedByMe": liked})

    def delete(self, request, publisher, name):
        entry = refs.entry_or_moved(publisher, name)
        likes, liked = community.unlike(request.user, entry)
        return Response({"likes": likes, "likedByMe": liked})


class MyLikesView(HubView):
    def get(self, request):
        ids = Like.objects.filter(user=request.user).order_by("-created_at").values_list("entry_id", flat=True)
        entries = {e.pk: e for e in search._base().filter(pk__in=list(ids))}
        ordered = [entries[i] for i in ids if i in entries]
        return Response(card_shapes.cards(ordered, request.user))


@workspace_read_only
class ReportsView(HubView):
    throttle_classes = [HubReportWriteThrottle]

    def post(self, request):
        data = body(request)
        handle, name, version = refs.parse_ref(str(data.get("entry") or ""))
        entry = Entry.objects.select_related("publisher").filter(publisher__handle=handle, name=name).first()
        if entry is None:
            raise HubError("OMEGA_HUB_NOT_FOUND", 404)
        report = community.open_report(request.user, entry, version=data.get("version") or version,
                                       reason=data.get("reason"), details=data.get("details"))
        return Response({"id": str(report.pk), "status": "received"}, status=201)
