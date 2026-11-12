"""Deployment settings adapter for the shared wire library."""

import os

from django.conf import settings

from agentcc.admin_token import insecure_admin_token_allowed, usable_admin_token
from tfc.service_envelope import PAIR_SETTINGS, EnvelopeRejected


def current_kid():
    return getattr(
        settings, "SERVICE_ENVELOPE_KID", os.environ.get("SERVICE_ENVELOPE_KID", "k1")
    )


def pair_keys(issuer, audience):
    setting = PAIR_SETTINGS.get((issuer, audience))
    if setting is None:
        raise EnvelopeRejected()
    if setting == "FI_API_SECRET_PEPPER":
        value = settings.API_SECRET_PEPPER
    else:
        value = getattr(settings, setting, os.environ.get(setting, ""))
    if setting == "AGENTCC_ADMIN_TOKEN" and not (
        isinstance(value, str)
        and usable_admin_token(value, insecure_admin_token_allowed())
    ):
        # The policy IsAdminToken and the gateway apply: a published,
        # placeholder or short token keys no envelope, so an install left on
        # the compose default accepts no forged gateway>fa-eval request.
        raise EnvelopeRejected()
    value = value.encode("utf-8") if isinstance(value, str) else value
    if not isinstance(value, bytes) or len(value) < 32:
        raise EnvelopeRejected()
    keys = {(issuer, audience, current_kid()): value}
    if setting == "FI_API_SECRET_PEPPER":
        # Labels are operator configuration, never fingerprints of secrets.
        labels = getattr(settings, "SERVICE_ENVELOPE_RETIRED_PEPPER_KIDS", ())
        retired = getattr(settings, "API_SECRET_PEPPERS_RETIRED", ())
        if (
            len(labels) != len(retired)
            or current_kid() in labels
            or len(set(labels)) != len(labels)
        ):
            if labels:
                raise EnvelopeRejected()
        for label, secret in zip(labels, retired, strict=False):
            keys[(issuer, audience, label)] = (
                secret.encode() if isinstance(secret, str) else secret
            )
    return keys
