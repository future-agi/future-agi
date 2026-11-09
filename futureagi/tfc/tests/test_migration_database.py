"""Delegated migration routing is opt-in, scoped and restoration-safe."""
from contextvars import copy_context
from types import SimpleNamespace

import pytest

from tfc.migration_database import delegated_database_alias, migration_database
from tfc.routers import ReadReplicaRouter, force_primary


def test_no_alias_preserves_normal_read_and_write_routing(settings):
    settings.DATABASES = {"default": {}, "replica": {}}
    router = ReadReplicaRouter(opt_in={"Example"})
    model = type("Example", (), {})

    @migration_database
    def helper(*, using=None):
        return router.db_for_read(model), router.db_for_write(model)

    assert helper() == ("replica", "default")
    with force_primary():
        assert helper() == ("default", "default")
    assert delegated_database_alias() is None


def test_nested_aliases_restore_outer_and_leave_other_contexts_unchanged():
    outer_context = copy_context()
    router = ReadReplicaRouter()
    model = SimpleNamespace()

    @migration_database
    def inner(*, using=None):
        assert delegated_database_alias() == "default"
        raise RuntimeError("synthetic inner failure")

    @migration_database
    def outer(*, using=None):
        assert router.db_for_read(model) == "default_direct"
        assert outer_context.run(delegated_database_alias) is None
        with pytest.raises(RuntimeError):
            inner(using="default")
        assert router.db_for_write(model) == "default_direct"

    outer(using="default_direct")
    assert delegated_database_alias() is None
    assert router.db_for_write(model) == "default"


def test_non_migration_alias_is_rejected_before_helper_runs():
    @migration_database
    def helper(*, using=None):
        raise AssertionError("must not execute")

    with pytest.raises(ValueError, match="writable migration alias"):
        helper(using="replica")
