"""Tests for the read/write Postgres router in `tfc.routers`.

Covers:
- `db_for_read` routing under all combinations of replica-configured /
  force_primary / locking-read / model opt-in / ALL_MODELS override.
- `db_for_write` always returns "default".
- `allow_migrate` allows `default` and `default_direct`, denies `replica`.
- `force_primary()` is reentrant and is enforced via ContextVar (works
  correctly across async tasks on the same thread).
- `primary_required` decorator restores state after the call returns.
- Bare model-name strings route the model, `feature:` prefixed strings do not.
- Tests use the `opt_in=` constructor parameter so we don't have to mutate
  `settings.READ_REPLICA_OPT_IN` across tests.
"""

from __future__ import annotations

import asyncio

import pytest
from django.contrib.auth import get_user_model

from tfc.routers import (
    ReadReplicaRouter,
    _is_force_primary,
    force_primary,
    primary_required,
    uses_db,
)


@pytest.fixture
def replica_configured(settings):
    """Inject a `replica` alias into `settings.DATABASES` for the test.

    Saves and restores any prior `replica` config so tests that run in
    environments where a replica is preconfigured don't lose it.
    """
    sentinel = object()
    prior = settings.DATABASES.get("replica", sentinel)
    settings.DATABASES["replica"] = {
        **settings.DATABASES["default"],
        "TEST": {"MIRROR": "default"},
    }
    yield
    if prior is sentinel:
        settings.DATABASES.pop("replica", None)
    else:
        settings.DATABASES["replica"] = prior


User = get_user_model()


# -- db_for_read ---------------------------------------------------------


def test_read_routes_to_default_when_no_replica():
    router = ReadReplicaRouter(opt_in=["User"])
    assert router.db_for_read(User) == "default"


def test_read_routes_to_default_when_model_not_opted_in(replica_configured):
    router = ReadReplicaRouter(opt_in=[])
    assert router.db_for_read(User) == "default"


def test_read_routes_to_replica_when_opted_in(replica_configured):
    router = ReadReplicaRouter(opt_in=["User"])
    assert router.db_for_read(User) == "replica"


def test_all_models_override(replica_configured):
    router = ReadReplicaRouter(opt_in=["ALL_MODELS_USE_READ_REPLICA"])
    assert router.db_for_read(User) == "replica"


def test_force_primary_overrides_opt_in(replica_configured):
    router = ReadReplicaRouter(opt_in=["User"])
    with force_primary():
        assert router.db_for_read(User) == "default"
    assert router.db_for_read(User) == "replica"


def test_force_primary_is_reentrant(replica_configured):
    router = ReadReplicaRouter(opt_in=["User"])
    with force_primary():
        with force_primary():
            assert router.db_for_read(User) == "default"
        # Inner exit does not release the outer pin
        assert router.db_for_read(User) == "default"
    # Both contexts exited
    assert router.db_for_read(User) == "replica"


def test_locking_read_hint_routes_to_default(replica_configured):
    router = ReadReplicaRouter(opt_in=["User"])
    assert router.db_for_read(User, for_update=True) == "default"


def test_instance_hint_does_NOT_route_to_default(replica_configured):
    """Django uses `instance` for related-manager stickiness on ordinary reads.
    Treating it as locking would route every related-object read to primary.
    """
    router = ReadReplicaRouter(opt_in=["User"])
    fake_instance = object()
    assert router.db_for_read(User, instance=fake_instance) == "replica"


def test_feature_key_does_not_route_model(replica_configured):
    """A feature-key string must not match a model — the namespace prefix prevents collision."""
    router = ReadReplicaRouter(opt_in=["feature:User"])
    assert router.db_for_read(User) == "default"


# -- db_for_write --------------------------------------------------------


def test_writes_always_go_to_default(replica_configured):
    router = ReadReplicaRouter(opt_in=["User"])
    assert router.db_for_write(User) == "default"


def test_writes_to_default_under_force_primary(replica_configured):
    router = ReadReplicaRouter(opt_in=["User"])
    with force_primary():
        assert router.db_for_write(User) == "default"


# -- allow_migrate -------------------------------------------------------


def test_migrate_allowed_on_default():
    assert ReadReplicaRouter().allow_migrate("default", "auth") is True


def test_migrate_allowed_on_default_direct():
    """default_direct is a real writer connection (PgBouncer bypass) and must accept migrations."""
    assert ReadReplicaRouter().allow_migrate("default_direct", "auth") is True


def test_migrate_denied_on_replica():
    assert ReadReplicaRouter().allow_migrate("replica", "auth") is False


# -- primary_required decorator -----------------------------------------


def test_primary_required_decorator(replica_configured):
    router = ReadReplicaRouter(opt_in=["User"])

    @primary_required
    def reads_inside():
        return router.db_for_read(User)

    assert reads_inside() == "default"
    # State restored after call
    assert router.db_for_read(User) == "replica"


def test_primary_required_preserves_function_metadata():
    @primary_required
    def my_fn():
        """docstring."""
        return 42

    assert my_fn.__name__ == "my_fn"
    assert my_fn.__doc__ == "docstring."
    assert my_fn() == 42


def test_primary_required_works_on_async_functions(replica_configured):
    """The decorator must `await` the coroutine inside `force_primary()`.
    A naive sync wrapper exits the context before the coroutine runs,
    leaving ORM reads unpinned."""
    router = ReadReplicaRouter(opt_in=["User"])
    seen: dict[str, str] = {}

    @primary_required
    async def reads_inside_async():
        # Force a context switch — if the decorator exited force_primary()
        # before the await, the post-await read would not be pinned.
        await asyncio.sleep(0)
        seen["db"] = router.db_for_read(User)

    asyncio.run(reads_inside_async())
    assert seen["db"] == "default"


# -- ContextVar isolation (async correctness) ---------------------------


def test_force_primary_does_not_leak_across_async_tasks(replica_configured):
    """ContextVar gives each asyncio task its own copy of `force_primary_depth`.

    If we had used `threading.local()`, the pin in `task_with_pin` would
    leak into `task_without_pin` because asyncio tasks share the same OS
    thread.

    The test must force the two tasks to actually overlap inside the pin
    block — otherwise the pin enters/exits before the other task ever
    reads, and a threading.local implementation would also pass. We use
    two asyncio.Events to coordinate: the pinned task enters the pin and
    waits, the unpinned task reads (must NOT see the pin), then signals
    the pinned task to exit.
    """
    router = ReadReplicaRouter(opt_in=["User"])

    async def run():
        unpinned_can_read = asyncio.Event()
        unpinned_done = asyncio.Event()
        results: dict[str, str] = {}

        async def task_with_pin():
            with force_primary():
                # Hand control to the unpinned task, which must read
                # while we're still inside the pin block.
                unpinned_can_read.set()
                await unpinned_done.wait()
                # Verify our own pin still works after the other task ran
                results["pinned_after"] = router.db_for_read(User)

        async def task_without_pin():
            await unpinned_can_read.wait()
            # We're now running concurrently with task_with_pin, which is
            # holding the pin. If pinning leaks across tasks, this read
            # would return "default" instead of "replica".
            results["unpinned"] = router.db_for_read(User)
            unpinned_done.set()

        await asyncio.gather(task_with_pin(), task_without_pin())
        return results

    results = asyncio.run(run())
    # The pin held by task_with_pin must NOT have leaked into task_without_pin
    assert results["unpinned"] == "replica"
    # The pinned task's pin must still be active after the other task ran
    assert results["pinned_after"] == "default"


def test_force_primary_state_is_clean_outside_block():
    assert _is_force_primary() is False
    with force_primary():
        assert _is_force_primary() is True
    assert _is_force_primary() is False


# -- @uses_db declarative decorator -------------------------------------


def test_uses_db_attaches_declared_alias():
    @uses_db("replica")
    def some_view():
        return "ok"

    assert some_view._declared_db_alias == "replica"
    assert some_view._declared_feature_key is None
    assert some_view() == "ok"


def test_uses_db_attaches_feature_key_when_given():
    @uses_db("default", feature_key="feature:foo")
    def some_view():
        return "ok"

    assert some_view._declared_db_alias == "default"
    assert some_view._declared_feature_key == "feature:foo"


def test_uses_db_does_NOT_change_routing(replica_configured):
    """The decorator is declarative — it must NOT auto-route reads.

    A function with @uses_db("replica") that does NOT call `.using(...)`
    on its queryset still goes through the normal router. This is the
    point: we want the routing to be explicit at the call site.
    """
    router = ReadReplicaRouter(opt_in=[])  # User is NOT opted in

    @uses_db("replica")
    def reads_without_using():
        return router.db_for_read(User)

    # Decorator says "replica" but the body didn't .using() — router still picks default.
    assert reads_without_using() == "default"


def test_uses_db_preserves_function_metadata():
    @uses_db("replica")
    def my_view():
        """docstring."""
        return 42

    # Decorator should not strip the function's identity.
    assert my_view.__name__ == "my_view"
    assert my_view.__doc__ == "docstring."
    # Attribute must still be attached AND the function must still be callable.
    assert my_view._declared_db_alias == "replica"
    assert my_view() == 42


def test_uses_db_rejects_non_string_alias():
    """`@uses_db` without parens would pass the decorated function as `alias`.
    We catch that misuse at import time."""
    with pytest.raises(TypeError, match="requires a string alias"):

        @uses_db  # forgot the parens — must fail loudly
        def some_view():  # pragma: no cover
            pass


# -- AST lint: @uses_db(X) must call .using(X) / .db_manager(X) ---------


def _find_uses_db_decorated_functions(tree: "ast.AST"):
    """Yield (function_node, alias_name) for every function decorated with @uses_db(NAME, ...)."""
    import ast

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            # Only handle the call form: @uses_db(NAME) — not bare references.
            if not isinstance(dec, ast.Call):
                continue
            func = dec.func
            if not (isinstance(func, ast.Name) and func.id == "uses_db"):
                continue
            if not dec.args:
                continue
            first = dec.args[0]
            if isinstance(first, ast.Name):
                yield node, first.id


def _function_body_routes_to(node, alias_name: str) -> bool:
    """Return True if `node.body` contains a `.using(alias_name)` or
    `.db_manager(alias_name)` call with a matching Name argument."""
    import ast

    for n in ast.walk(node):
        if not isinstance(n, ast.Call):
            continue
        func = n.func
        if not isinstance(func, ast.Attribute):
            continue
        if func.attr not in ("using", "db_manager"):
            continue
        if not n.args:
            continue
        a0 = n.args[0]
        if isinstance(a0, ast.Name) and a0.id == alias_name:
            return True
    return False


def test_uses_db_declarations_align_with_using_calls():
    """CI lint: every `@uses_db(NAME)` function must call `.using(NAME)`
    or `.db_manager(NAME)` somewhere in its body.

    Catches drift between decorator and routing: a function with
    `@uses_db(DATABASE_FOR_DASHBOARD_LIST)` that forgets the `.using()`
    is silently NOT routed. Grep can't catch this; an AST check can.

    Scope: files that import `uses_db` from `tfc.routers`. Add new files
    to `_FILES_TO_CHECK` when you decorate something with `@uses_db`.
    """
    import ast
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[2]
    _FILES_TO_CHECK = [
        repo_root / "tracer" / "views" / "dashboard.py",
        repo_root / "tracer" / "views" / "saved_view.py",
        repo_root / "tracer" / "views" / "replay_session.py",
        repo_root / "tracer" / "views" / "custom_eval_config.py",
        repo_root / "tracer" / "views" / "project_version.py",
        repo_root / "tracer" / "views" / "project.py",
        repo_root / "model_hub" / "views" / "develop_dataset.py",
        repo_root / "model_hub" / "views" / "eval_group.py",
        repo_root / "agentcc" / "views" / "org_config_bulk.py",
    ]

    violations = []
    for path in _FILES_TO_CHECK:
        tree = ast.parse(path.read_text())
        for fn_node, alias_name in _find_uses_db_decorated_functions(tree):
            if not _function_body_routes_to(fn_node, alias_name):
                violations.append(
                    f"{path.name}:{fn_node.lineno} {fn_node.name} is decorated "
                    f"with @uses_db({alias_name}) but its body does not call "
                    f".using({alias_name}) or .db_manager({alias_name})"
                )

    assert not violations, (
        "Drift between @uses_db decorator and actual routing:\n  - "
        + "\n  - ".join(violations)
    )
