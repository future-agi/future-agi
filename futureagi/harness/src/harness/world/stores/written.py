"""A store the build stage wrote, for an engine nobody taught the harness in advance.

This is what keeps "whatever the agent uses" from meaning "whatever we got around to
shipping". When the build stage finds an agent on an engine with no store in the tree, it
writes one: the image to run, the port it listens on, how to build a connection string for it,
and five functions saying how to talk to it.

None of that is trusted. A reset written by a model for an engine nobody reviewed is exactly
the thing that fails silently -- rows go back, a counter does not, and every scenario after the
first is measured against a world that drifted. So a written store is registered, not accepted:
whether it works is decided by ``prove_store``, which runs the same change twice from the same
starting point and compares. Nothing here has to be right for that gate to be meaningful, which
is the only reason writing it at build time is safe at all.
"""

from __future__ import annotations

from typing import Any, Callable

from . import Snapshot, StoreError, register_store
from .container import ContainerStore

# The functions a written store defines. Fewer would not be enough for an arbitrary engine, and
# more would be us guessing at what engines have in common. The last three are what a scenario's
# own setup lands on: without them the environment can be stood up and read, but nothing can
# change a little of it, so every scenario would run against the same base.
REQUIRED = ("connect", "apply", "state", "freeze", "restore", "add", "amend", "remove")

API = (
    "Your code defines exactly these functions:\n"
    "    def connect(dsn)                      -> a live client, already connected\n"
    "    def apply(db, script)                 -> run statements: migrations, or a seed\n"
    "    def state(db)                         -> {group: [row, ...]} for everything it holds\n"
    "    def freeze(db)                        -> (rows, counters)\n"
    "    def restore(db, rows, counters)       -> put both back exactly\n"
    "    def add(db, group, record)            -> insert one record, return how many landed\n"
    "    def amend(db, group, key, changes, by)-> update records where `by` equals `key`\n"
    "    def remove(db, group, key, by)        -> delete those records; no key means all of them\n"
    "Import whatever driver this engine needs at the top of the file; if it is not installed "
    "you will be told which one is missing. `counters` is anything that keeps counting after "
    "the rows are gone -- a sequence, an auto-increment. Restoring rows without it gives the "
    "next scenario ids continuing from the last one. Engines that hand out nothing of the sort "
    "return {}. Read state in a stable order, or a check comparing the first row is reading a "
    "coin toss. `add`, `amend` and `remove` are what a scenario's setup calls, so they are the "
    "difference between a suite of scenarios and one base world tested many times."
)


def _compile(code: str, engine: str) -> dict[str, Callable[..., Any]]:
    """Turn the written code into its five functions, or say precisely what is missing."""
    namespace: dict[str, Any] = {}
    try:
        exec(compile(code, f"<store:{engine}>", "exec"), namespace)  # nosec B102
    except ImportError as exc:
        raise StoreError(
            f"{engine} ops import something that is not installed: {exc}. Install the driver "
            f"this engine needs, or use an engine whose driver is already present."
        ) from exc
    except SyntaxError as exc:
        raise StoreError(f"{engine} ops do not parse: {exc}") from exc

    missing = [name for name in REQUIRED if not callable(namespace.get(name))]
    if missing:
        raise StoreError(
            f"{engine} ops define {', '.join(sorted(n for n in REQUIRED if n not in missing)) or 'nothing'}"
            f" but not {', '.join(missing)}.\n{API}"
        )
    return {name: namespace[name] for name in REQUIRED}


def register_written(
    *,
    engine: str,
    image: str,
    container_port: int,
    code: str,
    boot_env: dict[str, str] | None = None,
    dsn_template: str = "",
) -> type[ContainerStore]:
    """Teach the harness an engine from code written at build time.

    Registered rather than accepted: this makes the engine available to ``declare_engine``, and
    says nothing at all about whether its reset is correct. That is ``prove_store``'s to decide.
    """
    if not engine.strip():
        raise StoreError("an engine needs a name")
    if not image.strip():
        raise StoreError(f"{engine} needs an image to run")
    if not container_port:
        raise StoreError(f"{engine} needs the port it listens on")
    ops = _compile(code, engine)

    class WrittenStore(ContainerStore):
        pass

    WrittenStore.engine = engine
    WrittenStore.image = image
    WrittenStore.container_port = container_port
    WrittenStore.boot_env = dict(boot_env or {})
    WrittenStore._ops = ops  # type: ignore[attr-defined]
    WrittenStore._dsn_template = (
        dsn_template or "{engine}://{user}:{password}@{host}:{port}/{database}"
    )

    def dsn(self: ContainerStore) -> str:
        host, port = self.address()
        return type(self)._dsn_template.format(  # type: ignore[attr-defined]
            engine=type(self).engine,
            user=self.user,
            password=self.password,
            host=host,
            port=port,
            database=self.database,
        )

    def _client(self: ContainerStore) -> Any:
        """A fresh client per operation.

        Never held open, for the same reason the Postgres store does not hold one: an idle
        transaction of ours blocks the reset, and a reset that hangs on the harness's own
        connection is a very expensive thing to debug.
        """
        return type(self)._ops["connect"](self.dsn())  # type: ignore[attr-defined]

    def _with(self: ContainerStore, name: str, *args: Any) -> Any:
        db = self._client()  # type: ignore[attr-defined]
        try:
            return type(self)._ops[name](db, *args)  # type: ignore[attr-defined]
        finally:
            closer = getattr(db, "close", None)
            if callable(closer):
                closer()

    def probe(self: ContainerStore) -> None:
        db = self._client()  # type: ignore[attr-defined]
        closer = getattr(db, "close", None)
        if callable(closer):
            closer()

    def apply(self: ContainerStore, script: str) -> None:
        if not script.strip():
            return
        self._with("apply", script)  # type: ignore[attr-defined]

    def state(self: ContainerStore) -> dict[str, list[dict[str, Any]]]:
        return self._with("state")  # type: ignore[attr-defined]

    def freeze(self: ContainerStore) -> Snapshot:
        rows, counters = self._with("freeze")  # type: ignore[attr-defined]
        return Snapshot(rows=rows, counters=counters or {})

    def restore(self: ContainerStore, snapshot: Snapshot) -> None:
        self._with("restore", snapshot.rows, snapshot.counters)  # type: ignore[attr-defined]

    def add(self: ContainerStore, collection: str, record: Any) -> int:
        return int(self._with("add", collection, dict(record)) or 0)  # type: ignore[attr-defined]

    def amend(
        self: ContainerStore, collection: str, key: str, changes: Any, *, by: str = ""
    ) -> int:
        return int(self._with("amend", collection, key, dict(changes), by) or 0)  # type: ignore[attr-defined]

    def remove(self: ContainerStore, collection: str, key: str = "", *, by: str = "") -> int:
        return int(self._with("remove", collection, key, by) or 0)  # type: ignore[attr-defined]

    WrittenStore.add = add  # type: ignore[assignment]
    WrittenStore.amend = amend  # type: ignore[assignment]
    WrittenStore.remove = remove  # type: ignore[assignment]
    WrittenStore.dsn = dsn  # type: ignore[assignment]
    WrittenStore._client = _client  # type: ignore[attr-defined]
    WrittenStore._with = _with  # type: ignore[attr-defined]
    WrittenStore.probe = probe  # type: ignore[assignment]
    WrittenStore.apply = apply  # type: ignore[assignment]
    WrittenStore.state = state  # type: ignore[assignment]
    WrittenStore.freeze = freeze  # type: ignore[assignment]
    WrittenStore.restore = restore  # type: ignore[assignment]
    WrittenStore.__name__ = f"{engine.title().replace('_', '')}Store"

    register_store(engine, WrittenStore)
    return WrittenStore
