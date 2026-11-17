"""DB-free ORM and commit boundaries for dynamic-column regression fixtures.

Keep production selection, hashing, tracking and activity decorators intact. These
helpers model persistence and commit callbacks only, not database lock contention.
"""

import logging
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

from django.db import transaction


def _field(record, path):
    parts = path.split("__")
    for index, part in enumerate(parts):
        # Resolve FK comparisons without dereferencing an unsaved model relation.
        if index == len(parts) - 1 and part in {"column", "row", "dataset"}:
            if hasattr(record, part + "_id"):
                return getattr(record, part + "_id")
        if part.endswith("_id") and not hasattr(record, part):
            record = getattr(getattr(record, part[:-3]), "id")
        else:
            record = getattr(record, part)
    return record


def _identity(value):
    return str(getattr(value, "id", value))


def _matches(record, lookup):
    for field, expected in lookup.items():
        membership = field.endswith("__in")
        actual = _field(record, field[:-4] if membership else field)
        if membership:
            if _identity(actual) not in {_identity(item) for item in expected}:
                return False
        elif _identity(actual) != _identity(expected):
            return False
    return True


class MemoryQuerySet(list):
    """A strict, stateful subset of the ORM used by selection and fingerprints."""

    def __init__(self, records=(), *, on_update=None):
        super().__init__(records)
        self.on_update = on_update

    def _clone(self, records):
        return type(self)(records, on_update=self.on_update)

    def filter(self, **lookup):
        return self._clone(item for item in self if _matches(item, lookup))

    def exclude(self, **lookup):
        return self._clone(item for item in self if not _matches(item, lookup))

    def select_related(self, *fields):
        return self

    def order_by(self, *fields):
        records = list(self)
        for field in reversed(fields):
            records.sort(
                key=lambda item: _field(item, field.lstrip("-")),
                reverse=field.startswith("-"),
            )
        return self._clone(records)

    def values_list(self, *fields, flat=False):
        assert fields and (not flat or len(fields) == 1), fields
        return [
            _field(item, fields[0]) if flat else tuple(_field(item, f) for f in fields)
            for item in self
        ]

    def update(self, **fields):
        for item in self:
            for name, value in fields.items():
                setattr(item, name, value)
        if self.on_update is not None:
            self.on_update(**fields)
        return len(self)


class _MemoryManager:
    def __init__(self, records, *, on_update=None, on_create=None):
        self.records = records
        self.on_update = on_update
        self.on_create = on_create

    def filter(self, **lookup):
        return MemoryQuerySet(self.records(), on_update=self.on_update).filter(**lookup)

    def get(self, **lookup):
        found = self.filter(**lookup)
        assert len(found) == 1, (lookup, found)
        return found[0]

    def bulk_create(self, records, **kwargs):
        assert self.on_create is not None, "Unexpected write to read-only fixture"
        return self.on_create(list(records), **kwargs)


@contextmanager
def generation_orm(
    *, column, rows, cells, cell_factory, source_columns=(), on_column_update=None
):
    """Expose the activity's live cell store to the real tracking service.

    Rows/cells are callables so inserts, retries and deletions remain observable.
    The fingerprint functions and decorator are deliberately never replaced.
    """
    from model_hub.services import selective_rerun

    snapshots = {}

    def save_snapshots(records, **kwargs):
        assert kwargs == {
            "update_conflicts": True,
            "unique_fields": ["column", "row"],
            "update_fields": ["fingerprint"],
        }
        for record in records:
            snapshots[(_identity(record.column), str(record.row_id))] = record
        return records

    def service_cell(**kwargs):
        return cell_factory(**kwargs)

    service_cell.objects = _MemoryManager(
        cells, on_create=cell_factory.objects.bulk_create
    )

    def snapshot(**kwargs):
        return SimpleNamespace(**kwargs)

    snapshot.objects = _MemoryManager(
        lambda: snapshots.values(), on_create=save_snapshots
    )
    with (
        patch.object(
            selective_rerun,
            "Column",
            SimpleNamespace(objects=_MemoryManager(
                lambda: [column, *source_columns], on_update=on_column_update
            )),
        ),
        patch.object(selective_rerun, "Row", SimpleNamespace(objects=_MemoryManager(rows))),
        patch.object(selective_rerun, "Cell", service_cell),
        patch.object(selective_rerun, "GeneratedInputSnapshot", snapshot),
    ):
        yield snapshots


@contextmanager
def isolated_transactions():
    """Model default-connection commit ordering, including bound @atomic methods.

    Successful nested savepoints merge callbacks into their parent; exceptions
    discard that frame's callbacks. Only the outer successful exit dispatches.
    Persistence is in-memory, so this is not evidence of real rollback or locking.
    """
    frames = []

    def invoke(callback, robust):
        if not robust:
            callback()
            return
        try:
            callback()
        except Exception:
            logging.getLogger(__name__).exception("Fixture on_commit callback failed")

    def enter(atomic):
        assert atomic.using in (None, "default"), atomic.using
        assert atomic.savepoint, "Fixture models savepoint-enabled atomic blocks only"
        assert not atomic.durable or not frames, "Nested durable atomic block"
        frames.append([])

    def leave(atomic, exc_type, exc_value, traceback):
        callbacks = frames.pop()
        if exc_type is None:
            if frames:
                frames[-1].extend(callbacks)
            else:
                for callback, robust in callbacks:
                    invoke(callback, robust)
        return False

    def on_commit(callback, using=None, robust=False):
        assert using in (None, "default"), using
        if frames:
            frames[-1].append((callback, robust))
        else:
            invoke(callback, robust)

    # Patching the factory alone misses @transaction.atomic bound at import time.
    with (
        patch.object(transaction.Atomic, "__enter__", enter),
        patch.object(transaction.Atomic, "__exit__", leave),
        patch.object(transaction, "on_commit", on_commit),
    ):
        yield SimpleNamespace(frames=frames)
        assert not frames, "An atomic frame leaked out of the fixture"
