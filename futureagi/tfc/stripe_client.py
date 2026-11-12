"""The one place a Stripe call in this repo takes its API base from.

STRIPE_API_BASE (``tfc.settings.stripe_billing.resolve_stripe_api_base``) is
unset in production, and then nothing here changes a Stripe call: it goes to
the SDK's default, https://api.stripe.com. A walk stack sets it to stripe-mock
(e2e/stack/docker-compose.stripe-mock.yml) and every Stripe call goes there:

* ``stripe_client`` builds each ``stripe.StripeClient`` (which ignores
  ``stripe.api_base``) with that base for the API;
* ``configure_stripe_module`` points the SDK's module-level resources
  (``stripe.PaymentMethod.retrieve`` and the like) at it.

The setting is read and re-checked on every call, so a settings module that
skipped the startup check still cannot send a call to an unchecked base. A set
base never receives a live key: STRIPE_LIVE refuses it, and ``stripe_client``
also refuses it for a live key it is handed. Nothing here logs, and the API key
is passed through untouched.
"""

from typing import Any

import stripe
from django.conf import settings

from tfc.settings.stripe_billing import (
    refuse_api_base_with_live,
    resolve_stripe_api_base,
    stripe_key_mode,
)


def stripe_api_base() -> str | None:
    """The configured Stripe API base, or None for Stripe's own."""
    base = resolve_stripe_api_base(getattr(settings, "STRIPE_API_BASE", None))
    refuse_api_base_with_live(base, bool(getattr(settings, "STRIPE_LIVE", False)))
    return base or None


def stripe_client(api_key: str, **options: Any) -> stripe.StripeClient:
    """``stripe.StripeClient(api_key, **options)``, on STRIPE_API_BASE when set.
    A set base with a live ``api_key`` is refused, whatever STRIPE_LIVE says."""
    base = stripe_api_base()
    if base:
        refuse_api_base_with_live(base, stripe_key_mode(api_key) == "live")
        addresses = options.get("base_addresses") or {}
        options["base_addresses"] = {**addresses, "api": base}
    return stripe.StripeClient(api_key, **options)


def configure_stripe_module() -> None:
    """Point the SDK's module-level calls at STRIPE_API_BASE when it is set.
    Unset leaves ``stripe.api_base`` as it is."""
    base = stripe_api_base()
    if base:
        stripe.api_base = base
