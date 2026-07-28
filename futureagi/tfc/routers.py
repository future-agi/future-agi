"""
Read/write Postgres router for futureagi.

Mirrors PostHog's `posthog/dbrouter.py` opt-in-by-model pattern, with three
futureagi-specific additions:

  1. `force_primary()` — a contextvars-based escape hatch that pins reads to
     the primary for the duration of the block. Works across threads and
     coroutines (Temporal activities, async views).
  2. `_is_locking_read(hints)` — defensive check so any future Django change
     that passes a locking hint to `db_for_read` keeps the read on primary.
  3. Stricter `allow_migrate` — only `default` and `default_direct` accept
     migrations. The replica is a physical standby and must not get DDL.

Design notes and the broader rollout plan live in
`internal-docs/read-write-pg-replica/`. Do not enable opt-in for authz models
(`User`, `Organization`, `OrganizationMembership`) without explicit review.
"""

import functools
import inspect
from contextlib import contextmanager
from contextvars import ContextVar

from django.conf import settings


# Why ContextVar, not threading.local: async/ASGI workers (Temporal activities,
# Django async views) share threads across coroutines. A threading.local would
# leak primary-pinning between concurrent tasks running on the same thread.
_force_primary_depth: ContextVar[int] = ContextVar(
    "futureagi_force_primary_depth", default=0
)


def _opt_in_keys() -> set[str]:
    """Read READ_REPLICA_OPT_IN lazily — re-read per call so tests can override settings."""
    return set(getattr(settings, "READ_REPLICA_OPT_IN", []) or [])


def _replica_configured() -> bool:
    return "replica" in settings.DATABASES


@contextmanager
def force_primary():
    """
    Pin ROUTER-MEDIATED reads inside this block to the primary database.

    SCOPE — important caveat:
      This only affects reads that flow through `ReadReplicaRouter.db_for_read()`,
      i.e. ordinary `Model.objects.filter(...)` calls that let the router pick.
      It does NOT redirect code that explicitly bypasses the router via
      `.using("replica")` or `db_manager("replica")` — those continue to
      use the specified alias. Our 9 currently-routed endpoints (project_list,
      dashboard_list, etc.) all use explicit aliases, so `force_primary()`
      cannot redirect them. Read-after-write on those endpoints must either
      (a) construct the query without `.using()` so it goes through the
      router, or (b) flip `READ_REPLICA_OPT_IN=""` to disable routing.

    Safe across threads AND coroutines. Reentrant.

    Use around code that just wrote and immediately reads, where the read
    is a plain ORM query that goes through the router.

    Example:
        with force_primary():
            org.save()
            fresh = Organization.objects.get(pk=org.pk)  # hits primary
            # NOTE: `Organization.objects.using("replica").get(...)` inside
            # this block would still hit replica — explicit beats router.

    Or as a decorator via `primary_required`.
    """
    token = _force_primary_depth.set(_force_primary_depth.get() + 1)
    try:
        yield
    finally:
        _force_primary_depth.reset(token)


def primary_required(fn):
    """Decorator form of `force_primary()` for view-level or task-level wrapping.

    Works for both sync and async functions. For async, the `force_primary()`
    block must wrap the `await`, not just the call that creates the coroutine
    — a naive sync wrapper would exit the context before the coroutine runs,
    leaving ORM reads inside the function unpinned.
    """
    if inspect.iscoroutinefunction(fn):

        @functools.wraps(fn)
        async def async_wrapper(*args, **kwargs):
            with force_primary():
                return await fn(*args, **kwargs)

        return async_wrapper

    @functools.wraps(fn)
    def sync_wrapper(*args, **kwargs):
        with force_primary():
            return fn(*args, **kwargs)

    return sync_wrapper


def _is_force_primary() -> bool:
    return _force_primary_depth.get() > 0


def uses_db(alias: str, *, feature_key: str | None = None):
    """Declarative marker: this function reads from the given DB alias.

    PURELY DECLARATIVE — does not auto-route queries. The function body
    MUST still call `.using(alias)` / `db_manager(alias)` on its querysets.
    This decorator's job is to make the routing visible at the call site
    for code review, grep, and a CI lint rule that verifies the body
    actually uses the declared alias (see
    `tfc/tests/test_router.py::test_uses_db_declarations_align_with_using_calls`).

    Why declarative and not magic auto-routing: an "everything inside this
    function routes to <alias>" pattern would unexpectedly route
    auxiliary queries (permission checks, serializer field lookups,
    related-manager fetches) that the developer didn't audit.

    Args:
        alias: the actual alias string. Usually one of our
            `DATABASE_FOR_X` constants (which evaluate to "default" or
            "replica" depending on `READ_REPLICA_OPT_IN`).
        feature_key: the underlying feature key (e.g.
            "feature:dashboard_list"). Optional but recommended for any
            routed view — without it, `_declared_db_alias` is just
            "default" in non-opted deployments, losing the intent.

    Usage:
        from tfc.routers import uses_db
        from tracer.db_routing import DATABASE_FOR_DASHBOARD_LIST

        @uses_db(DATABASE_FOR_DASHBOARD_LIST, feature_key="feature:dashboard_list")
        def list(self, request, *args, **kwargs):
            queryset = self.get_queryset().using(DATABASE_FOR_DASHBOARD_LIST)
            ...

    Introspection:
        fn._declared_db_alias  # → evaluated alias ("default" or "replica")
        fn._declared_feature_key  # → "feature:dashboard_list" or None
    """
    # Misuse guard: `@uses_db` without parens would pass the function
    # itself as `alias` and silently replace the function with the
    # inner `decorator`. Catch that at import time.
    if not isinstance(alias, str):
        raise TypeError(
            "uses_db(alias) requires a string alias as the first argument. "
            "Did you write `@uses_db` without parentheses? "
            "Use `@uses_db(DATABASE_FOR_X)` (or `@uses_db('default')`)."
        )

    def decorator(fn):
        fn._declared_db_alias = alias
        fn._declared_feature_key = feature_key
        return fn

    return decorator


def _is_locking_read(hints: dict) -> bool:
    """
    Defensive: route any read that carries a `for_update` hint to primary.

    SCOPE: this only fires for reads that flow through the ORM router. Any
    code that explicitly bypasses the router (e.g. `Model.objects.using("replica")`
    or `db_manager("replica").select_for_update()`) is not protected here —
    it will still send `SELECT FOR UPDATE` to the standby and fail at the
    PG level. Treat this check as belt-and-braces, not a comprehensive
    guard. Code that explicitly routes to replica must not call
    `select_for_update`.

    Django >=2 routes `select_for_update` via `db_for_write`, so in
    practice this check should rarely fire — but it's two lines and guards
    against Django passing the hint to `db_for_read` in some future release.

    We do NOT check `hints.get("instance")` — Django uses the `instance`
    hint for related-manager stickiness on ordinary reads, and treating it
    as a locking signal would route every related-object read to primary.

    Minor risk: if a third-party library reuses the name `for_update` as
    a router hint for something else, we'll over-route to primary. This
    is safe for correctness (primary always works) but invalidates load-
    shedding assumptions. The hint name is undocumented in Django, so the
    collision risk is low.
    """
    return bool(hints.get("for_update"))


class ReadReplicaRouter:
    """
    Opt-in read/write router.

    Reads go to the replica only when all of these are true:
      - a `replica` alias is configured
      - the model class name is in `settings.READ_REPLICA_OPT_IN`
        (or the special token `ALL_MODELS_USE_READ_REPLICA` is set)
      - we are not inside a `force_primary()` block
      - the read does not carry a `for_update` hint

    Writes always go to `default`. Migrations only on `default` and
    `default_direct` (the PgBouncer-bypass alias used by `manage.py migrate`
    when `lock_timeout` needs to be set at connection time).

    Feature keys with a `feature:` prefix in `READ_REPLICA_OPT_IN` are
    ignored by this router; they are consulted by hot-path code that
    constructs its own `db_manager()` target. The prefix avoids collisions
    with model class names.
    """

    def __init__(self, opt_in=None):
        # Mirrors PostHog's `ReplicaRouter(opt_in=...)` parameter so tests
        # can construct the router with a specific opt-in list instead of
        # monkey-patching settings.
        self._opt_in_override = opt_in

    def _opt_in(self) -> set[str]:
        if self._opt_in_override is not None:
            return set(self._opt_in_override)
        return _opt_in_keys()

    def db_for_read(self, model, **hints):
        if not _replica_configured():
            return "default"
        if _is_force_primary():
            return "default"
        if _is_locking_read(hints):
            return "default"
        opt_in = self._opt_in()
        if "ALL_MODELS_USE_READ_REPLICA" in opt_in:
            return "replica"
        if model.__name__ in opt_in:
            return "replica"
        return "default"

    def db_for_write(self, model, **hints):
        return "default"

    def allow_relation(self, obj1, obj2, **hints):
        return True

    def allow_migrate(self, db, app_label, model_name=None, **hints):
        # `default_direct` is the PgBouncer-bypass alias used for migrations
        # that need `lock_timeout` set at connection time (transaction-pool
        # mode strips session-level SET statements).
        return db in ("default", "default_direct")
