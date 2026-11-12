"""Dispatch claims on PostgreSQL with the hard-erase SQL guard installed.

The guard is installed exactly as AccountsConfig.ready installs it. These cases
need a real PostgreSQL primary; the offline twin is
test_dispatch_claims_guard_offline.
"""

from importlib import import_module

import pytest
from django.apps import apps
from django.db import connection, connections, transaction

from accounts.services import hard_erase_sql
from tfc.routers import force_primary

pytestmark = pytest.mark.django_db(transaction=True)

TABLE = "agentcc_dispatch_claim"


@pytest.fixture
def guarded_claim():
    assert connection.vendor == "postgresql", "Run this contract on PostgreSQL"
    assert not connection.in_atomic_block and connection.get_autocommit()
    # Exactly what Django runs once at startup; every step is idempotent.
    apps.get_app_config("accounts").ready()
    assert hard_erase_sql.guard_execute in connections["default"].execute_wrappers
    assert TABLE in hard_erase_sql._TABLES
    return import_module("agentcc.services.dispatch_claims").claim_dispatch_once


def count_claims(key):
    model = import_module("agentcc.models").AgentccDispatchClaim
    with force_primary():
        return model.objects.filter(idempotency_key=key).count()


def test_first_claim_is_true_and_the_second_is_false(guarded_claim):
    assert guarded_claim("guard-first") is True
    assert not connection.in_atomic_block and connection.get_autocommit()
    assert guarded_claim("guard-first") is False
    assert count_claims("guard-first") == 1


def test_claim_inside_atomic_raises_runtime_error(guarded_claim):
    with transaction.atomic():
        with pytest.raises(RuntimeError, match="autocommit"):
            guarded_claim("guard-nested")
    assert count_claims("guard-nested") == 0
    assert guarded_claim("guard-nested") is True
