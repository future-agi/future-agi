"""Evict bearer snapshots on platform authority changes, including admin saves.

This is cache hygiene, not the authorization boundary: decode_token also checks
these bits against the primary DB on every hit, including queryset/bulk updates
(which do not emit save signals) and stale cache refills racing this eviction.
"""

from accounts.services.auth_cache import revoke_cached_access_tokens

import structlog
from django.core.cache import cache
from django.db import transaction
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

from accounts.models.auth_token import AuthToken, AuthTokenType

AUTHORITY_FIELDS = frozenset({"is_staff", "is_superuser", "is_active"})
logger = structlog.get_logger(__name__)


@receiver(pre_save, sender="accounts.User")
def remember_authority(sender, instance, using, update_fields=None, raw=False, **kwargs):
    instance._auth_cache_previous_authority = None
    if raw or instance._state.adding:
        return
    fields = AUTHORITY_FIELDS
    if update_fields is not None:
        fields = fields.intersection(update_fields)
    if fields:
        instance._auth_cache_previous_authority = (
            sender._base_manager.using(using)
            .filter(pk=instance.pk)
            .values(*sorted(fields))
            .first()
        )


def _evict_user_access_cache(user_id, using):
    try:
        token_ids = (
            AuthToken._base_manager.using(using)
            .filter(user_id=user_id, auth_type=AuthTokenType.ACCESS)
            .values_list("pk", flat=True)
        )
        revoke_cached_access_tokens(token_ids, cache_backend=cache)
    except Exception:
        # Cache failure must not roll back an authority revocation. The primary
        # DB liveness comparison remains mandatory even when eviction fails.
        logger.warning("user_authority_cache_eviction_failed")


@receiver(post_save, sender="accounts.User")
def evict_changed_authority(sender, instance, using, raw=False, **kwargs):
    previous = instance.__dict__.pop("_auth_cache_previous_authority", None)
    if raw or previous is None:
        return
    if not any(getattr(instance, field) != old for field, old in previous.items()):
        return
    user_id = instance.pk
    # Evict now for same-transaction consumers, and again after commit so a
    # request that observed the old committed row cannot leave a stale refill.
    _evict_user_access_cache(user_id, using)
    transaction.on_commit(lambda: _evict_user_access_cache(user_id, using), using=using)
