"""Commit-bound session invalidation for an identity actually being erased."""

from functools import partial

from django.core.cache import cache
from django.db import models, transaction


def revoke_erased_identity_sessions(user_id, *, using):
    # A membershipless identity may still have cached sessions. Membership
    # receivers alone cannot revoke those. Keep the ordinary tenant helper
    # scoped; only the User hard-delete receiver calls this global primitive.
    from accounts.models.auth_token import AuthToken

    rows = models.QuerySet(model=AuthToken, using=using).filter(user_id=user_id)
    keys = [
        f"{kind}_token_{pk}" for pk, kind in rows.values_list("pk", "auth_type") if kind
    ]
    rows.update(is_active=False)
    if keys:
        transaction.on_commit(
            partial(cache.delete_many, keys), using=using, robust=True
        )
