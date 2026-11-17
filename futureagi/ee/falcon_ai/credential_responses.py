"""Value-free Falcon credential refusals at REST boundaries."""

from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from ee.falcon_ai.credentials import AUTH_REQUIRED, FalconCredentialError


def credential_auth_required_response():
    # 409 is connector state, not a failure of the user's application session.
    return Response(
        {"status": False, "message": AUTH_REQUIRED, "code": AUTH_REQUIRED},
        status=status.HTTP_409_CONFLICT,
    )


class FalconCredentialAPIView(APIView):
    def handle_exception(self, exc):
        if isinstance(exc, FalconCredentialError):
            return credential_auth_required_response()
        return super().handle_exception(exc)
