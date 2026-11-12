"""Redeem a one-time sign-in handoff code for the session it carries (CR-07).

The app posts the code it was redirected with; the tokens come back only in
this response body, which is never cached and never sent as a referrer. Only
the app's own origin may redeem, and every failure (spent, expired, unknown or
wrong-kind code, wrong or missing Origin, malformed body) is one categorical
400 that does not say which check failed.
"""

from urllib.parse import urlsplit

from accounts.authentication import decode_token, decrypt_message
from django.conf import settings
from drf_yasg.utils import swagger_auto_schema
from rest_framework import serializers
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import (
    AuthenticationFailed,
    ParseError,
    UnsupportedMediaType,
)
from rest_framework.parsers import JSONParser
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.services.handoff_exchange import (
    KIND_WS,
    LOGIN_HANDOFF_KINDS,
    HandoffRefused,
    issue,
    redeem,
)

HANDOFF_REFUSED_STATUS = 400
HANDOFF_REFUSED_BODY = {
    "error": "handoff_refused",
    "message": "This sign-in link is invalid, already used or expired.",
}


class HandoffRedeemRequestSerializer(serializers.Serializer):
    code = serializers.CharField()
    kind = serializers.ChoiceField(choices=sorted(LOGIN_HANDOFF_KINDS))


class HandoffRedeemResponseSerializer(serializers.Serializer):
    access = serializers.CharField()
    refresh = serializers.CharField(allow_null=True)


class HandoffRefusedResponseSerializer(serializers.Serializer):
    error = serializers.CharField()
    message = serializers.CharField()


def _app_origin():
    """scheme://host[:port] of the configured app, or None when unset."""
    base = getattr(settings, "APP_BASE_URL", "") or ""
    parts = urlsplit(base.strip())
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return None
    return f"{parts.scheme}://{parts.netloc}".lower()


def _refused():
    return Response(dict(HANDOFF_REFUSED_BODY), status=HANDOFF_REFUSED_STATUS)


class HandoffRedeemView(APIView):
    # The code is the credential: no cookie or bearer may stand in for it, and
    # a stale Authorization header must not turn a redeem into a 401.
    authentication_classes = []
    permission_classes = (AllowAny,)
    parser_classes = [JSONParser]

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response["Cache-Control"] = "no-store"
        response["Pragma"] = "no-cache"
        response["Referrer-Policy"] = "no-referrer"
        return response

    @swagger_auto_schema(
        request_body=HandoffRedeemRequestSerializer,
        responses={
            200: HandoffRedeemResponseSerializer,
            400: HandoffRefusedResponseSerializer,
        },
        security=[],
        operation_description=(
            "Redeem the one-time code a sign-in redirect carried (OAuth SSO, SAML "
            "SOS or AWS Marketplace) for that session. JSON POST from the app's own origin only; "
            "a code lives at most 60 seconds and redeems once. The tokens are "
            "returned only in this no-store body. Any refusal is one categorical "
            "400."
        ),
    )
    def post(self, request):
        expected_origin = _app_origin()
        if expected_origin is None or request.headers.get("Origin") != expected_origin:
            return _refused()
        try:
            data = request.data
        except (ParseError, UnsupportedMediaType):
            return _refused()
        if not isinstance(data, dict):
            return _refused()
        kind = data.get("kind")
        # Socket identity and onboarding capabilities have separate consumers;
        # neither may be redeemed into a login response here.
        if not isinstance(kind, str) or kind not in LOGIN_HANDOFF_KINDS:
            return _refused()
        try:
            payload = redeem(data.get("code"), kind)
        except HandoffRefused:
            return _refused()
        return Response({"access": payload["access"], "refresh": payload["refresh"]})


class WebSocketSessionAuthentication(BaseAuthentication):
    """Only an explicit header-authenticated interactive access session."""

    def authenticate_header(self, request):
        return "Bearer"

    def authenticate(self, request):
        parts = request.headers.get("Authorization", "").split()
        if len(parts) != 2 or parts[0].lower() != "bearer":
            raise AuthenticationFailed("Socket ticket refused.")
        try:
            user, _, _ = decode_token(parts[1], transport="websocket", touch=False)
            identity = decrypt_message(parts[1])
            if not user.is_active or str(identity.get("user_id")) != str(user.pk):
                raise ValueError("Invalid session identity.")
            return user, {"user_id": str(user.pk), "session_id": str(identity["id"])}
        except Exception as exc:
            # Never include the header or decoder exception in the response.
            raise AuthenticationFailed("Socket ticket refused.") from exc


class WebSocketTicketRequestSerializer(serializers.Serializer):
    path = serializers.CharField()
    query = serializers.DictField(child=serializers.CharField(), required=False)


class WebSocketTicketResponseSerializer(serializers.Serializer):
    ticket = serializers.CharField()


class WebSocketTicketView(HandoffRedeemView):
    authentication_classes = (WebSocketSessionAuthentication,)
    permission_classes = (IsAuthenticated,)

    @swagger_auto_schema(
        request_body=WebSocketTicketRequestSerializer,
        responses={
            200: WebSocketTicketResponseSerializer,
            400: HandoffRefusedResponseSerializer,
        },
        operation_description="Mint a single-use socket ticket, valid for at most 60 seconds, using a Bearer header.",
    )
    def post(self, request):
        expected_origin = _app_origin()
        if expected_origin is None or request.headers.get("Origin") != expected_origin:
            return _refused()
        try:
            data = request.data
        except (ParseError, UnsupportedMediaType):
            return _refused()
        if not isinstance(data, dict) or set(data) - {"path", "query"}:
            return _refused()
        try:
            ticket = issue(
                KIND_WS,
                {
                    **request.auth,
                    "path": data.get("path"),
                    "query": data.get("query", {}),
                },
            )
        except Exception:
            # Storage failure must also be categorical and no-store, never an
            # uncaught cache exception containing request-adjacent diagnostics.
            return _refused()
        return Response({"ticket": ticket})
