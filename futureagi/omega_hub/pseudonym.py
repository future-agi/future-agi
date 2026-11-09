"""Stable pseudonyms for people in Hub records (HUB-09).

A report and an audit row store the pseudonym of the account behind them when they are written, so
they still say "the same account did these" after the account is deleted, without naming anyone.
The label is an HMAC of the account id under the server secret: stable for an account, the same in
every Hub record, and not reversible without the secret. It is computed once and stored, so a later
secret rotation does not change what was recorded.
"""

from __future__ import annotations

import hashlib
import hmac

from django.conf import settings

PREFIX = "account-"


def label_for_id(account_id) -> str:
    secret = settings.SECRET_KEY if isinstance(settings.SECRET_KEY, bytes) else settings.SECRET_KEY.encode()
    digest = hmac.new(secret, f"omega_hub|pseudonym|{account_id}".encode(), hashlib.sha256).hexdigest()
    return PREFIX + digest[:12]


def label_for(user) -> str:
    """The pseudonym of a user, or "" for no user (a system or checker action)."""
    pk = getattr(user, "pk", None)
    return label_for_id(pk) if pk else ""
