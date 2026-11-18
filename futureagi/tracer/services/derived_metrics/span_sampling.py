"""Attempt-scoped, bounded sampler collisions for every span identity page.

Only ambiguous (sampler span id, quarter) keys are retained, once per distinct
saved filter population. No per-span copy, persistent store or replay of mutable
source. Page-budget retries restore the bounded frozen collisions, never recapture
them; an unrelated lost native session still aborts preparation.
"""

from uuid import uuid4

from tracer.services.derived_metrics.publication import MetricVisibilityError
from tracer.services.derived_metrics.queries import Scope
from tracer.services.derived_metrics.unit_reads import (
    field_recipe,
    span_sampling_select,
)

SAMPLING_CONTEXT_ROWS = 50_000
SAMPLING_CONTEXT_BYTES = 64 * 1024 * 1024
SAMPLING_CONTEXT_SCHEMA = (
    ("context_id", "UInt32"),
    ("span_id", "String"),
    ("bucket_start", "DateTime('UTC')"),
)
SAMPLING_CONTEXT_COLUMNS = ", ".join(c for c, _ in SAMPLING_CONTEXT_SCHEMA)


def _population(definition):
    return tuple(sorted((f["field"], f["value"]) for f in definition.filters))


def _needs_context(definition):
    source = field_recipe(definition).source
    return source.weighting == "inverse_probability" and any(
        i["source"] == "eval" for i in source.inputs
    )


class SpanSamplingContext:
    """Freeze all filtered sampling populations before the attempt's first page."""

    def __init__(self, writer, attempt, definitions, *, as_of, dirty=None):
        self.writer = writer
        self.attempt = attempt
        self.definitions = definitions
        self.scope = Scope(writer.org_id, writer.workspace_id, writer.lease.project_id)
        self.as_of = as_of
        self.dirty = dirty
        self.name = "metric_span_sampling_" + uuid4().hex
        self.socket = None
        self.populations = {}
        self.floors = {}
        self._frozen_rows = None

    def _create_table(self):
        store = self.writer.store
        store.execute("SELECT 1")
        self.socket = store.client.connection.socket
        self.check()
        schema = ", ".join(f"{c} {kind}" for c, kind in SAMPLING_CONTEXT_SCHEMA)
        store.execute(f"CREATE TEMPORARY TABLE {self.name} ({schema}) ENGINE=Memory")

    def _read_rows(self):
        self.check()
        rows = tuple(
            self.writer.store.execute(
                f"SELECT {SAMPLING_CONTEXT_COLUMNS} FROM {self.name} "
                f"ORDER BY {SAMPLING_CONTEXT_COLUMNS}"
            )
        )
        self.check()
        return rows

    def restore_for_retry(self):
        """Only a recoverable page-budget failure may replace the native session.

        The driver destroys its temporary tables on a server exception. Keep one
        bounded process-local copy of the complete collision relation, sealed
        before the first page, and send it back as native data (not SQL literals).
        Do not register automatic recovery: unrelated session loss stays fatal.
        """
        if (
            not self.populations
            or self.writer.store.client.connection.socket is self.socket
        ):
            return
        if self._frozen_rows is None:
            raise MetricVisibilityError("span sampling context was not frozen")
        self._create_table()
        if self._frozen_rows:
            self.writer.store.execute(
                f"INSERT INTO {self.name} ({SAMPLING_CONTEXT_COLUMNS}) "
                f"SELECT {SAMPLING_CONTEXT_COLUMNS} FROM sampling_replay",
                external_tables=[
                    {
                        "name": "sampling_replay",
                        "structure": list(SAMPLING_CONTEXT_SCHEMA),
                        "data": self._frozen_rows,
                    }
                ],
            )
        if self._read_rows() != self._frozen_rows:
            raise MetricVisibilityError("span sampling context changed on replay")

    def check(self):
        if self.populations and (
            self.socket is None
            or self.writer.store.client.connection.socket is not self.socket
        ):
            raise MetricVisibilityError("span sampling context lost its native session")

    def select(self, definition):
        self.check()
        if not _needs_context(definition):
            return None
        context_id = self.populations[_population(definition)]
        return (
            f"SELECT span_id, bucket_start FROM {self.name} "
            f"WHERE context_id = {context_id}"
        )

    def __enter__(self):
        from tracer.services.derived_metrics.span_evidence import (
            _bucket_runs,
            _dirty_buckets,
            _span_window_floor,
        )

        if self.dirty is None:
            self.dirty = _dirty_buckets(self.attempt)
        definitions = {}
        for definition in self.definitions:
            if definition.level == "span" and _needs_context(definition):
                definitions.setdefault(_population(definition), definition)
        self.populations = {key: i for i, key in enumerate(definitions)}
        if not definitions:
            return self
        store = self.writer.store
        columns = SAMPLING_CONTEXT_COLUMNS
        try:
            self._create_table()
            count = size = 0
            for at, until in _bucket_runs(self.dirty):
                self.writer.lease.keepalive()
                self.floors[at, until] = _span_window_floor(
                    store, self.scope, at, until
                )
                for key, definition in definitions.items():
                    self.check()
                    sql, params = span_sampling_select(
                        definition,
                        self.scope,
                        start=at,
                        end=until,
                        as_of=self.as_of,
                        start_floor=self.floors[at, until],
                    )
                    # Bound typed bytes in the SAME execution that inserts them.
                    # No lossy LIMIT or count-then-reread of a mutable population.
                    store.execute(
                        f"INSERT INTO {self.name} ({columns}) SELECT {columns} FROM ("
                        "SELECT *, count() OVER () AS context_rows, "
                        f"sum(length(formatRow('RowBinary', {columns}))) OVER () AS context_bytes "
                        f"FROM (SELECT toUInt32({self.populations[key]}) AS context_id, "
                        f"span_id, toDateTime(bucket_start, 'UTC') AS bucket_start FROM ({sql}))) "
                        f"WHERE throwIf(context_rows > {SAMPLING_CONTEXT_ROWS - count} "
                        f"OR context_bytes > {SAMPLING_CONTEXT_BYTES - size}, "
                        "'metric_span_sampling_context_budget') = 0",
                        params,
                    )
                    self.check()
                    count, size = store.execute(
                        f"SELECT count(), sum(length(formatRow('RowBinary', {columns}))) "
                        f"FROM {self.name}"
                    )[0]
                    if count > SAMPLING_CONTEXT_ROWS or size > SAMPLING_CONTEXT_BYTES:
                        raise MetricVisibilityError(
                            "span sampling context budget exhausted"
                        )
            # This relation is already bounded across every population/run. Read
            # only its frozen rows, never the mutable source behind the compiler.
            self._frozen_rows = self._read_rows()
            return self
        except BaseException as exc:
            self.__exit__(type(exc), exc, exc.__traceback__)
            raise

    def __exit__(self, exc_type, exc, tb):
        store = self.writer.store
        try:
            if (
                self.socket is not None
                and store.client.connection.socket is self.socket
            ):
                store.execute(f"DROP TEMPORARY TABLE IF EXISTS {self.name}")
        except Exception:
            store.client.disconnect()
            if exc_type is None:
                raise
        finally:
            self.socket = None
            self._frozen_rows = None
