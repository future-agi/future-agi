"""The initiating actor snapshot survives deletion and idempotent rebinding."""

import secrets
from decimal import Decimal

import pytest

from accounts.models import User
from accounts.services.hard_erase import HardEraseRefused, authorized_scope
from agentcc.services.checkout_verification import record_binding
from agentcc.tests.test_managed_credits_api import _user_in
from tfc.constants.levels import Level

pytestmark = pytest.mark.integration


def test_binding_retains_original_actor_after_user_deletion(organization):
    actor, _ = _user_in(organization, Level.ADMIN)
    actor_id = actor.pk
    session = "cs_test_SyntheticActorReceipt" + secrets.token_hex(16)
    binding = record_binding(organization, session, Decimal("25.00"), False, actor)
    assert binding.actor_identifier == actor_id
    # A user root grants no organization authority, including for retained
    # checkout rows whose created_by FK would be cleared. Keep that refusal.
    with pytest.raises(
        HardEraseRefused, match="outside the authorized organization scope"
    ):
        actor.delete()
    assert User.objects.filter(pk=actor_id).exists()
    binding.refresh_from_db()
    assert binding.created_by_id == actor_id
    assert binding.actor_identifier == actor_id

    # This fixture owns exactly this org. Use the existing lifecycle scope,
    # not a raw delete, mocked collector, or direct assignment of created_by.
    with authorized_scope([organization.pk], using="default"):
        actor.delete()
    assert not User.objects.filter(pk=actor_id).exists()
    binding.refresh_from_db()
    assert binding.created_by_id is None
    assert binding.actor_identifier == actor_id

    replacement, _ = _user_in(organization, Level.ADMIN)
    again = record_binding(organization, session, Decimal("25.00"), False, replacement)
    assert again.pk == binding.pk
    assert again.created_by_id is None
    assert again.actor_identifier == actor_id


def test_binding_without_actor_never_invents_an_identity(organization):
    session = "cs_test_SyntheticNoActorReceipt" + secrets.token_hex(16)
    binding = record_binding(organization, session, Decimal("25.00"), False, None)
    assert binding.created_by_id is None
    assert binding.actor_identifier is None
