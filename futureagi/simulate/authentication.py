"""Authentication identities used by internal simulation services."""

from __future__ import annotations

import secrets

from django.conf import settings
from rest_framework.authentication import BaseAuthentication, get_authorization_header


class InternalServiceUser:
    """Minimal DRF identity for a caller holding ``INTERNAL_API_SECRET``."""

    is_authenticated = True
    is_internal_service = True


class InternalServiceAuthentication(BaseAuthentication):
    """Authenticate a trusted service without binding it to a tenant."""

    def authenticate(self, request):
        parts = get_authorization_header(request).split()
        if len(parts) != 2 or parts[0].lower() != b"bearer":
            return None

        configured_secret = getattr(settings, "INTERNAL_API_SECRET", "")
        if not configured_secret:
            return None

        try:
            supplied_secret = parts[1].decode("utf-8")
        except UnicodeDecodeError:
            return None

        if not secrets.compare_digest(supplied_secret, configured_secret):
            return None
        # Authentication has already consumed the bearer credential. Do not
        # retain the fleet-wide secret on request.auth for downstream code or
        # exception/logging integrations to accidentally expose.
        return InternalServiceUser(), None


class HarnessAttemptUser:
    is_authenticated = True
    is_harness_attempt = True

    def __init__(self, attempt) -> None:
        self.attempt = attempt
        self.organization = attempt.job.organization


class HarnessAttemptAuthentication(BaseAuthentication):
    """Authenticate a bearer and fence against one registered hosted attempt."""

    def authenticate(self, request):
        from django.utils import timezone

        from simulate.models import HostedHarnessAttempt
        from simulate.services.hosted_harness import HostedHarnessError, hash_secret

        resolver_match = getattr(request, "resolver_match", None)
        kwargs = getattr(resolver_match, "kwargs", {}) if resolver_match else {}
        attempt_id = kwargs.get("pk") or kwargs.get("attempt_id")
        if not attempt_id:
            raise HostedHarnessError(
                "attempt_mismatch",
                "attempt id is missing from the request path",
                status_code=403,
            )
        parts = get_authorization_header(request).split()
        if len(parts) != 2 or parts[0].lower() != b"bearer":
            raise HostedHarnessError(
                "authentication_required",
                "a harness attempt bearer is required",
                status_code=401,
            )
        try:
            supplied_token = parts[1].decode("utf-8")
        except UnicodeDecodeError as exc:
            raise HostedHarnessError(
                "authentication_invalid",
                "attempt bearer is not valid UTF-8",
                status_code=401,
            ) from exc
        fence = request.headers.get("X-Harness-Fence", "")
        try:
            attempt = HostedHarnessAttempt.no_workspace_objects.select_related(
                "job", "job__organization"
            ).get(id=attempt_id)
        except HostedHarnessAttempt.DoesNotExist as exc:
            raise HostedHarnessError(
                "attempt_not_found",
                "attempt was not found",
                status_code=404,
            ) from exc
        if not secrets.compare_digest(attempt.token_hash, hash_secret(supplied_token)):
            raise HostedHarnessError(
                "authentication_invalid",
                "attempt bearer is invalid",
                status_code=401,
            )
        if not fence or not secrets.compare_digest(
            attempt.fence_hash, hash_secret(fence)
        ):
            raise HostedHarnessError(
                "attempt_fenced",
                "attempt fence is invalid",
                status_code=403,
            )
        if timezone.now() >= attempt.expires_at:
            raise HostedHarnessError(
                "attempt_expired",
                "attempt bearer has expired",
                status_code=401,
            )
        if attempt.attempt_number < attempt.job.current_attempt_number:
            raise HostedHarnessError(
                "attempt_superseded",
                "attempt has been superseded",
                status_code=409,
            )
        return HarnessAttemptUser(attempt), attempt
