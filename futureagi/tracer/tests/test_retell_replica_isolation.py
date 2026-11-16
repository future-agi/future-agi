"""The lagging-replica regression must not change other test modules' databases."""

import pytest
from django.db import connections
from django.utils.connection import ConnectionDoesNotExist


@pytest.mark.unit
def test_replica_alias_is_not_registered_outside_replica_module(settings):
    assert "replica" not in settings.DATABASES
    assert "replica" not in connections
    with pytest.raises(ConnectionDoesNotExist):
        connections["replica"]
