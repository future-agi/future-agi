"""The public Contract-1 door: an agent's tool calls reach the harness here.

A hosted agent carries no platform credentials of its own — the capability
token embedded in the URL IS the credential — so this route is deliberately
public and ingress must expose it to the internet for a hosted agent to
reach it at all.
"""

from __future__ import annotations

import json
import re
import uuid

import httpx
from django.core.exceptions import ImproperlyConfigured
from django.http import HttpResponse, JsonResponse
from django.utils import timezone
from rest_framework.permissions import AllowAny
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from simulate.models import RLWorldCopy
from simulate.services.harness_client import (
    NON_STREAMING_TIMEOUT,
    internal_headers,
    resolve_harness_internal_url,
)

# Room template a hosted run stamps at run initiation: hosted-{run_id}-i{index}-{test_case_id}
_ROOM = re.compile(
    r"^hosted-(?P<run>[0-9a-fA-F-]{8,36})-i(?P<index>\d{1,5})-(?P<case>[A-Za-z0-9_.\-]{1,128})$"
)

# A dropped/expired copy is not reachable, but its state is still worth naming
# in the error rather than treating it identically to an unknown token.
_REACHABLE_STATUSES = (RLWorldCopy.Status.READY, RLWorldCopy.Status.IN_CALL)


def _resolve_room(_run_id: str, _index: int, _case_id: str) -> RLWorldCopy | None:
    """Room name -> the world copy leased for it.

    Tokens are registered against room names at run initiation; until that
    wiring exists, every well-formed room is unknown.
    """
    return None


class HarnessRoomConfigView(APIView):
    """Deliberately public for the same reason as ``HarnessHookView``: a
    hosted agent has no platform credentials to authenticate a room lookup
    with before it even has its tool-call token."""

    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "harness_hook"

    def get(self, request, room):
        match = _ROOM.match(room)
        if match is None:
            # The agent must raise on this, never guess — the body needs to be
            # something it can quote back.
            return JsonResponse(
                {"error": "malformed room name", "room": room}, status=400
            )

        copy = _resolve_room(
            match.group("run"), int(match.group("index")), match.group("case")
        )
        if copy is None:
            return JsonResponse({"error": "unknown room", "room": room}, status=404)


class HarnessHookView(APIView):
    """Deliberately public: the capability token in the URL is the only
    credential a hosted agent carries, and an agent cannot CSRF-token its way
    past SessionAuthentication — hence the empty authenticator list rather
    than DRF's default."""

    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "harness_hook"

    def post(self, request, token, tool):
        try:
            token_uuid = uuid.UUID(str(token))
        except ValueError:
            return JsonResponse({"error": "unknown token"}, status=404)

        copy = RLWorldCopy.no_workspace_objects.filter(
            token=token_uuid, deleted=False
        ).first()
        if copy is None:
            return JsonResponse({"error": "unknown token"}, status=404)
        if copy.expires_at is not None and copy.expires_at < timezone.now():
            # The janitor flips status eventually, but a lapsed lease must not
            # be served in the meantime just because status hasn't caught up.
            return JsonResponse({"error": "copy is expired"}, status=404)
        if copy.status not in _REACHABLE_STATUSES:
            # Still a 404: there is no oracle for token validity that would let
            # a caller distinguish "dropped" from "never existed".
            return JsonResponse({"error": f"copy is {copy.status}"}, status=404)

        try:
            body = json.loads(request.body) if request.body else {}
        except ValueError:
            return JsonResponse({"error": "invalid JSON body"}, status=400)

        try:
            headers = internal_headers()
        except ImproperlyConfigured:
            return JsonResponse(
                {"error": "INTERNAL_API_SECRET is not configured"}, status=503
            )

        url = f"{resolve_harness_internal_url()}/internal/hook/{token}/{tool}"
        try:
            answered = httpx.request(
                "POST", url, json=body, headers=headers, timeout=NON_STREAMING_TIMEOUT
            )
        except (httpx.HTTPError, httpx.InvalidURL):
            return JsonResponse({"error": "harness unreachable"}, status=502)

        content_type = answered.headers.get("content-type", "")
        if "application/json" not in content_type:
            # Non-JSON upstream responses pass through untouched.
            return HttpResponse(
                answered.content, status=answered.status_code, content_type=content_type
            )
        try:
            payload = answered.json()
        except ValueError:
            # Upstream advertised JSON but the body doesn't parse; relay the
            # raw bytes rather than turning a bad upstream reply into a 500.
            return HttpResponse(
                answered.content, status=answered.status_code, content_type=content_type
            )
        return JsonResponse(payload, status=answered.status_code, safe=False)
