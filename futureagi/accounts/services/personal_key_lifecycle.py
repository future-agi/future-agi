"""Invalidate a departing person's credentials without affecting other members."""

from django.db import transaction


def revoke_personal_keys(user_id, organization_id=None, *, using="default"):
    from accounts.models import OrgApiKey
    from accounts import api_key_revocation
    from agentcc.models import AgentccAPIKey
    from agentcc.services import auth_bridge
    from accounts.services.service_grants import revoke_person_grants

    revoke_person_grants(user_id, organization_id, using=using)

    keys = OrgApiKey._base_manager.using(using).filter(user_id=user_id, enabled=True)
    gateway_keys = (
        AgentccAPIKey._base_manager.using(using)
        .filter(user_id=user_id)
        .exclude(status=AgentccAPIKey.REVOKED)
    )
    if organization_id is not None:
        keys = keys.filter(organization_id=organization_id)
        gateway_keys = gateway_keys.filter(organization_id=organization_id)
    for key in keys:
        OrgApiKey._base_manager.using(using).filter(pk=key.pk).update(enabled=False)
        key.enabled = False
        transaction.on_commit(
            lambda key=key: api_key_revocation.publish_key_revocation(key),
            using=using,
            robust=True,
        )
    for key in gateway_keys:
        # Mark locally inside the transaction so sync cannot resurrect it. The
        # gateway mutation happens only after a successful membership commit.
        AgentccAPIKey._base_manager.using(using).filter(pk=key.pk).update(
            status=AgentccAPIKey.REVOKED
        )
        transaction.on_commit(
            lambda key=key: auth_bridge.revoke_key(key), using=using, robust=True
        )
