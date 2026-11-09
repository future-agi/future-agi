"""Base views for the Hub (SPEC §9.1): no URL versioning, Hub auth classes, the Hub error envelope,
explicit permissions on every view, a token scope check on every view, and no org API key acting as a
person on any view (SPEC §9.3)."""

from __future__ import annotations

from django.http import HttpResponse
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.authentication import APIKeyAuthentication
from omega_hub.auth import CheckerAuthentication, HubTokenAuthentication
from omega_hub.body_limit import refuse_oversized
from omega_hub.errors import HubError, hub_exception_handler
from omega_hub.permissions import (
    HubAuthenticated,
    IsChecker,
    IsHubMaintainer,
    TokenScope,
    refuse_org_api_key,
    uses_org_api_key,
)
from omega_hub.api.request_schema import HubAutoSchema
from omega_rollout.hub_gate import check_checker_request, check_hub_request
from omega_hub.throttles import HubAnonReadThrottle, HubUserReadThrottle

DEFAULT_PAGE_SIZE = 24
MAX_PAGE_SIZE = 60


class HubView(APIView):
    versioning_class = None
    authentication_classes = [HubTokenAuthentication, APIKeyAuthentication]
    permission_classes = [TokenScope, HubAuthenticated]
    throttle_classes = [HubUserReadThrottle]
    token_allowed = False
    swagger_schema = HubAutoSchema  # request bodies in the API contract come from openapi.yaml
    # A download that installs an entry: the kill switch stops it only when installs are paused too.
    hub_install = False

    def get_exception_handler(self):
        return hub_exception_handler

    def initial(self, request, *args, **kwargs):
        # Before authentication, throttles and any parse of the body (HUB-17). Under ASGI the
        # HubBodyLimit middleware has already refused it; this covers WSGI and the test client.
        refuse_oversized(request)
        super().initial(request, *args, **kwargs)

    def check_permissions(self, request):
        """The rollout check first (READINESS 3.13: an org not admitted to the Hub gets the same 404
        whatever the view's own rules would say), then the view's own permissions (so likes keep
        OMEGA_HUB_LIKE_NEEDS_SESSION), then, on every Hub view whatever its permission list, an org API
        key pair is refused unless the request is a public read. A `system` key authenticates as the
        org's first user, who may be a maintainer."""
        check_hub_request(request, self)
        super().check_permissions(request)
        refuse_org_api_key(request, self.get_permissions())


class PublicView(HubView):
    """Public reads: anyone; an optional session adds `likedByMe`. Hub tokens may read too."""

    permission_classes = [TokenScope, AllowAny]
    throttle_classes = [HubAnonReadThrottle, HubUserReadThrottle]
    token_allowed = True


class MaintainerView(HubView):
    permission_classes = [TokenScope, HubAuthenticated, IsHubMaintainer]


class CheckerView(APIView):
    versioning_class = None
    authentication_classes = [CheckerAuthentication]
    permission_classes = [IsChecker]
    throttle_classes: list = []
    swagger_schema = HubAutoSchema

    def get_exception_handler(self):
        return hub_exception_handler

    def initial(self, request, *args, **kwargs):
        refuse_oversized(request)
        super().initial(request, *args, **kwargs)

    def check_permissions(self, request):
        super().check_permissions(request)
        check_checker_request(self, request)  # a paused Hub drains its checks (READINESS 3.13)

    def checker_id(self, request) -> str:
        return (request.headers.get("X-Omega-Hub-Checker-Id") or "checker")[:120]


def viewer(request):
    """The signed-in person behind a public read, if any. An org API key pair is nobody (SPEC §9.3)."""
    if uses_org_api_key(request):
        return None
    user = getattr(request, "user", None)
    return user if user is not None and user.is_authenticated else None


def paginate(request, items, render) -> Response:
    try:
        page = max(1, int(request.query_params.get("page", 1)))
        size = min(MAX_PAGE_SIZE, max(1, int(request.query_params.get("page_size", DEFAULT_PAGE_SIZE))))
    except ValueError as exc:
        raise HubError("OMEGA_HUB_INVALID", 400, "page and page_size are whole numbers") from exc
    total = len(items)
    start = (page - 1) * size
    chunk = items[start:start + size]

    def link(number):
        params = request.query_params.copy()
        params["page"] = str(number)
        return f"{request.path}?{params.urlencode()}"

    return Response({
        "count": total,
        "next": link(page + 1) if start + size < total else None,
        "previous": link(page - 1) if page > 1 else None,
        "results": render(chunk),
    })


def bytes_response(data: bytes, content_type: str, headers: dict | None = None, status: int = 200) -> HttpResponse:
    response = HttpResponse(data, content_type=content_type, status=status)
    response["X-Content-Type-Options"] = "nosniff"
    for key, value in (headers or {}).items():
        response[key] = value
    return response


def body(request) -> dict:
    data = request.data
    if hasattr(data, "dict"):
        data = data.dict()
    if not isinstance(data, dict):
        raise HubError("OMEGA_HUB_INVALID", 400, "the body must be a JSON object")
    return data
