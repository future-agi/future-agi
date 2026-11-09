"""Alias/locking/rollback-checking historical ORM double, never DB evidence."""

import copy
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID

from tfc.migration_database import delegated_database_alias


def record(index, headers=None, secret=""):
    return SimpleNamespace(
        pk=UUID(int=index),
        organization_id=UUID(int=100 + index % 2),
        url="https://fixture.invalid/exact?case=X",
        headers=headers,
        secret=secret,
        encrypted_headers=None,
        encrypted_secret=None,
        credential_storage_version=None,
        deleted=bool(index % 2),
        is_active=not bool(index % 2),
    )


class Rows:
    def __init__(self, store, selected=None, locked=False):
        self.store = store
        self.selected = list(store.records) if selected is None else selected
        self.locked = locked

    def using(self, alias):
        assert alias == self.store.alias == delegated_database_alias()
        return self

    def order_by(self, field):
        assert field == "pk"
        return Rows(self.store, sorted(self.selected))

    def filter(self, **kwargs):
        selected = self.selected
        if "pk__gt" in kwargs:
            selected = [pk for pk in selected if pk > kwargs["pk__gt"]]
        if "pk" in kwargs:
            selected = [pk for pk in selected if pk == kwargs["pk"]]
        return Rows(self.store, selected, self.locked)

    def count(self):
        assert delegated_database_alias() == self.store.alias
        return len(self.store.records)

    def select_for_update(self):
        assert self.store.in_atomic
        assert delegated_database_alias() == self.store.alias
        return Rows(self.store, self.selected, True)

    def __getitem__(self, item):
        assert self.locked and self.store.in_atomic
        assert isinstance(item, slice) and item.stop is not None
        return [copy.copy(self.store.records[pk]) for pk in self.selected[item]]

    def update(self, **kwargs):
        assert self.store.in_atomic
        assert delegated_database_alias() == self.store.alias
        assert set(kwargs) == {
            "encrypted_headers",
            "encrypted_secret",
            "credential_storage_version",
        }
        for pk in self.selected:
            self.store.records[pk].__dict__.update(kwargs)
        self.store.updates += 1
        if self.store.on_update:
            self.store.on_update(self.store)
        return len(self.selected)


class Organizations:
    def __init__(self, store):
        self.store = store

    def using(self, alias):
        assert alias == self.store.alias == delegated_database_alias()
        return self

    def filter(self, **kwargs):
        self.ids = set(kwargs["pk__in"]) & self.store.orgs
        return self

    def values_list(self, field, *, flat):
        assert field == "pk" and flat
        return self.ids


@contextmanager
def fake_database(helper, records, alias="default_direct"):
    store = SimpleNamespace(
        records={row.pk: copy.deepcopy(row) for row in records},
        alias=alias,
        in_atomic=False,
        updates=0,
        on_update=None,
        commits=0,
        orgs={row.organization_id for row in records},
    )
    model = SimpleNamespace(
        _base_manager=Rows(store), _meta=SimpleNamespace(db_table="agentcc_webhook")
    )
    organization = SimpleNamespace(_base_manager=Organizations(store))

    def get_model(app, name):
        assert (app, name) in {
            ("prism", "AgentccWebhook"),
            ("accounts", "Organization"),
        }
        return model if name == "AgentccWebhook" else organization

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def execute(self, sql):
            assert delegated_database_alias() == alias
            assert sql == 'SELECT COUNT(*) FROM "agentcc_webhook"'

        def fetchone(self):
            return (len(store.records),)

    connection = SimpleNamespace(
        alias=alias,
        cursor=Cursor,
        ops=SimpleNamespace(quote_name=lambda name: '"' + name + '"'),
    )
    editor = SimpleNamespace(connection=connection)
    apps = SimpleNamespace(get_model=get_model)

    @contextmanager
    def atomic(*, using):
        assert using == alias == delegated_database_alias()
        assert not store.in_atomic
        # DB buffers are immutable snapshots here; memoryview is not pickleable.
        buffers = {
            id(value): value
            for row in store.records.values()
            for value in vars(row).values()
            if type(value) is memoryview
        }
        before = copy.deepcopy(store.records, buffers)
        store.in_atomic = True
        try:
            yield
        except Exception:
            store.records = before
            raise
        else:
            store.commits += 1
        finally:
            store.in_atomic = False

    with patch.object(helper.transaction, "atomic", atomic):
        yield store, apps, editor
    assert delegated_database_alias() is None
