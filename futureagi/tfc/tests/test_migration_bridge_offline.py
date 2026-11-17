"""Offline bridge contracts; no application imports, settings, database or network.

Graph checks use Django's loader/executor with AST disk metadata. Leaf states
are real ProjectStates projected to model/field/index/constraint NAMES only.
Migrations up to the pinned leaves are pinned on their executable content
(operations, dependencies, imports and helper bodies; not docstrings, comments or
the UPDATE_SAFETY declaration). Migrations after those leaves are listed in
AFTER_BRIDGE and checked as graph extensions only. This is not a field-type,
router, DDL, data or makemigrations execution rehearsal.
Run with plugin autoload disabled, --noconftest -c /dev/null -o addopts=.
"""

import ast
import hashlib
import importlib.util
import json
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import django
import pytest
from django.db import migrations, models
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.loader import MigrationLoader
from django.db.migrations.recorder import MigrationRecorder
from django.db.migrations.state import ProjectState

BACKEND = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).with_name("fixtures")
C25 = json.loads((FIXTURES / "c25_applied_migrations_657594d3.json").read_text())
C26 = json.loads(
    (FIXTURES / "c26_prebridge_applied_migrations_head35.json").read_text()
)
BRIDGES = C25["bridges"]
TEN = {(row["app"], row["newName"]) for row in BRIDGES[:10]}
TWIN = ("prism", "0089_normalize_two_key_org_period")
TWIN_LEGACY = ("prism", "0081_normalize_two_key_org_period")
TARGET_APPS = {"prism", "simulate", "accounts"}


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def attributes(source):
    tree = ast.parse(source)
    cls = next(
        n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Migration"
    )
    return {
        t.id: n.value
        for n in cls.body
        if isinstance(n, ast.Assign)
        for t in n.targets
        if isinstance(t, ast.Name)
    }


def graph_keys(node):
    assert isinstance(node, (ast.List, ast.Tuple)), ast.dump(node)
    result = []
    for item in node.elts:
        if isinstance(item, ast.Call):
            assert (
                ast.unparse(item)
                == "migrations.swappable_dependency(settings.AUTH_USER_MODEL)"
            )
            result.append(("accounts", "__first__"))
        else:
            key = ast.literal_eval(item)
            assert len(key) == 2 and all(isinstance(v, str) for v in key)
            result.append(tuple(key))
    return result


def helper():
    filename = BACKEND / "tfc/migration_bridge.py"
    assert filename.is_file(), "Missing migration bridge implementation"
    spec = importlib.util.spec_from_file_location("tfc.migration_bridge", filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def wrapper_call(source):
    operations = attributes(source)["operations"]
    assert isinstance(operations, ast.List) and len(operations.elts) == 1
    call = operations.elts[0]
    assert (
        isinstance(call, ast.Call)
        and ast.unparse(call.func) == "SkipDatabaseIfLegacyApplied"
    )
    assert not call.args
    kwargs = {kw.arg: kw.value for kw in call.keywords}
    assert set(kwargs) == {"legacy", "operations"}
    return call, ast.literal_eval(kwargs["legacy"]), kwargs["operations"]


def bare_source(source):
    """Remove only our envelope, preserving the original operations bytes."""
    attrs = attributes(source)
    ops = attrs["operations"]
    if not (
        isinstance(ops, ast.List)
        and len(ops.elts) == 1
        and isinstance(ops.elts[0], ast.Call)
        and ast.unparse(ops.elts[0].func) == "SkipDatabaseIfLegacyApplied"
    ):
        return source
    _, legacy, inner = wrapper_call(source)
    outer_text = ast.get_source_segment(source, ops)
    inner_text = ast.get_source_segment(source, inner)
    assert source.count(outer_text) == 1
    source = source.replace(outer_text, inner_text, 1)
    source = source.replace(
        "from tfc.migration_bridge import SkipDatabaseIfLegacyApplied\n", "", 1
    )
    source = source.replace(
        f"    # c2.5 recorded this as {legacy[0]}.{legacy[1]}.\n", "", 1
    )
    return source


def _canonical(node):
    """AST text that is the same on every supported Python.

    ast.dump is not: 3.12 added fields (type_params) and 3.13 stopped printing
    empty ones, so a digest of its output changes with the interpreter. Empty
    and None fields are skipped here, so a field a newer Python adds, which is
    empty for old syntax, does not change the result.
    """
    if isinstance(node, ast.AST):
        parts = []
        for name in node._fields:
            value = getattr(node, name, None)
            if value is None or value == []:
                continue
            parts.append(f"{name}={_canonical(value)}")
        return f"{type(node).__name__}({','.join(parts)})"
    if isinstance(node, list):
        return "[" + ",".join(_canonical(item) for item in node) + "]"
    return repr(node)


def _without_docstring(body):
    first = body[0] if body else None
    if (
        isinstance(first, ast.Expr)
        and isinstance(first.value, ast.Constant)
        and isinstance(first.value.value, str)
    ):
        return body[1:]
    return body


def _is_update_safety(node):
    """The module-level UPDATE_SAFETY dict literal, which only the update gates read."""
    if not (
        isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id == "UPDATE_SAFETY"
    ):
        return False
    try:
        return isinstance(ast.literal_eval(node.value), dict)
    except (ValueError, TypeError, SyntaxError):
        return False


def executable_tree(source, drop_dependencies=False):
    """The parts of a migration that a database's history depends on.

    Operations and their arguments, dependencies, imports and helper bodies stay.
    Docstrings and the UPDATE_SAFETY declaration are left out, and comments are never
    in the tree, so wording and review metadata do not move a pin.
    """
    tree = ast.parse(source)
    tree.body = [
        node for node in _without_docstring(tree.body) if not _is_update_safety(node)
    ]
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            node.body = _without_docstring(node.body)
    if drop_dependencies:
        cls = next(
            n
            for n in tree.body
            if isinstance(n, ast.ClassDef) and n.name == "Migration"
        )
        cls.body = [
            n
            for n in cls.body
            if not (
                isinstance(n, ast.Assign)
                and any(
                    isinstance(t, ast.Name) and t.id == "dependencies"
                    for t in n.targets
                )
            )
        ]
    return tree


def executable_sha(source, drop_dependencies=False):
    return sha(_canonical(executable_tree(source, drop_dependencies)))


# Migrations added after the pinned leaves. They are not pinned by source; the leaf
# test checks only that they extend the pinned history. A new migration in a target
# app fails that test until its name is listed here, in the same commit.
AFTER_BRIDGE = frozenset(
    {
        # Dev's API key expiry (0026) and its merge nodes (0031, 0032), and the
        # dev-merge leaf (0042), came in with the pull 5 dev merge.
        ("accounts", "0026_orgapikey_expires_at"),
        ("accounts", "0031_merge_api_key_expiry"),
        ("accounts", "0032_merge_expiry_and_coding_agent"),
        ("accounts", "0041_default_workspace_for_every_organization"),
        ("accounts", "0042_merge_line_and_dev_1fc7d403"),
        ("prism", "0090_request_log_error_code"),
        ("prism", "0091_request_log_error_source"),
    }
)

# Listed migrations of a branch that forked before the pinned leaf and is merged into
# the app's history after it. Each need not extend the pinned leaf itself; the app's one
# leaf must merge it, and that leaf (listed, not in this set) must extend the pinned leaf.
MERGED_IN = frozenset(
    {
        # Dev's API key expiry (0026) and its merge nodes, joined to the line at 0042.
        ("accounts", "0026_orgapikey_expires_at"),
        ("accounts", "0031_merge_api_key_expiry"),
        ("accounts", "0032_merge_expiry_and_coding_agent"),
    }
)
# Each merged-in migration's reviewed source, by sha256 of its text: any edit fails the
# gate until it is reviewed again and re-pinned here, in the same commit.
MERGED_IN_SHA256 = {
    (
        "accounts",
        "0026_orgapikey_expires_at",
    ): "e22f5f1de4423af42ab3fdc234a19bf8e2bf12ade3e18cd414cb4911c74bfcf8",
    (
        "accounts",
        "0031_merge_api_key_expiry",
    ): "3029effa70e2edd6d5ebdfcc1037e3685c64e1781c111194970ee6047e937cd6",
    (
        "accounts",
        "0032_merge_expiry_and_coding_agent",
    ): "764d9b4dfe47a472ce4f080c5855171f80744673020d13832a731ea4261442f6",
}
# A merged-in migration may only add: an install at the pinned leaf then applies the
# branch and its merge node without changing or dropping anything the leaf has.
ADDITIVE = frozenset({"AddField", "AddIndex", "AddConstraint", "CreateModel"})

# Only these carry the SkipDatabaseIfLegacyApplied envelope; any other pinned migration
# is compared as it is, so adding an envelope to it moves its pin.
BRIDGED = frozenset({(row["app"], row["newName"]) for row in BRIDGES} | {TWIN})


def migration_file(key):
    root = next(app["root"] for app in C26["apps"] if app["label"] == key[0])
    return f"{root}/migrations/{key[1]}.py"


def pin_problems(sources, pinned, after_bridge=AFTER_BRIDGE):
    """Each pinned migration keeps its executable content; every other one is listed."""
    problems, seen = [], set()
    for key, source in sorted(sources.items()):
        if key[0] not in TARGET_APPS:
            continue
        filename = migration_file(key)
        seen.add(filename)
        if key in after_bridge:
            if filename in pinned:
                problems.append(
                    f"{filename}: pinned, so it cannot also be listed in AFTER_BRIDGE"
                )
            elif bare_source(source) != source:
                problems.append(
                    f"{filename}: a migration after the pinned leaf must not carry a "
                    "SkipDatabaseIfLegacyApplied envelope"
                )
        elif filename not in pinned:
            problems.append(
                f"{filename}: added after the pinned leaf; list {key} in AFTER_BRIDGE "
                "in the same commit"
            )
        elif (
            executable_sha(bare_source(source) if key in BRIDGED else source)
            != pinned[filename]
        ):
            problems.append(
                f"{filename}: its operations, dependencies, imports or helper bodies "
                "differ from the pin; a reviewed change is re-pinned in its own commit"
            )
    problems += [f"{name}: pinned but missing" for name in sorted(set(pinned) - seen)]
    problems += [
        f"{key}: listed in AFTER_BRIDGE but not on disk"
        for key in sorted(after_bridge)
        if key not in sources
    ]
    problems += [
        f"{key}: listed in AFTER_BRIDGE but not a migration of {sorted(TARGET_APPS)}"
        for key in sorted(after_bridge)
        if key[0] not in TARGET_APPS
    ]
    return problems


def operation_kinds(source):
    """The operation class names in a migration's source, or None unless its
    operations are a plain list (or tuple) of migrations.<Op>(...) calls."""
    node = attributes(source).get("operations")
    if node is None:
        return []
    if not isinstance(node, (ast.List, ast.Tuple)):
        return None
    kinds = []
    for item in node.elts:
        func = item.func if isinstance(item, ast.Call) else None
        if not (
            isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Name)
            and func.value.id == "migrations"
        ):
            return None
        kinds.append(func.attr)
    return kinds


def after_bridge_problems(
    loader,
    after_bridge=AFTER_BRIDGE,
    merged_in=MERGED_IN,
    sources=None,
    merged_pins=MERGED_IN_SHA256,
):
    """One leaf per app; each listed migration extends the pinned leaf (or, if merged in,
    is merged by the app's leaf), replaces nothing and takes no legacy or bridged name."""
    reserved = (
        {(r["app"], r["legacyName"]) for r in BRIDGES}
        | {(r["app"], r["newName"]) for r in BRIDGES}
        | {TWIN_LEGACY}
    )
    problems = []
    for app in sorted(TARGET_APPS):
        pinned_leaves = [tuple(k) for k in C26["baseLeaves"][app]]
        listed = sorted(k for k in after_bridge if k[0] == app)
        leaves = loader.graph.leaf_nodes(app)
        if len(leaves) != 1:
            problems.append(
                f"{app}: {len(leaves)} leaf migrations {leaves}; one is expected"
            )
        elif leaves[0] not in (listed or pinned_leaves):
            problems.append(
                f"{app}: leaf {leaves[0]} is neither the pinned leaf nor listed in AFTER_BRIDGE"
            )
        for key in listed:
            if key not in loader.disk_migrations:
                continue
            plan = set(loader.graph.forwards_plan(key))
            missing = [leaf for leaf in pinned_leaves if leaf not in plan]
            if key in merged_in:
                merged_by_leaf = len(leaves) == 1 and key in set(
                    loader.graph.forwards_plan(leaves[0])
                )
                if not merged_by_leaf:
                    problems.append(
                        f"{key}: merged in, but the app's leaf does not merge it"
                    )
                if leaves and leaves[0] in merged_in:
                    problems.append(
                        f"{key}: the app's leaf may not itself be merged in"
                    )
                # Read from the migration's own source: the loader's records carry no
                # operations. Anything but a plain list of migrations.<Op>(...) fails.
                source = (sources or {}).get(key)
                kinds = operation_kinds(source) if source is not None else None
                if kinds is None:
                    problems.append(
                        f"{key}: merged in, but its operations cannot be read as a "
                        "plain list of migrations.<Op>(...) calls"
                    )
                elif set(kinds) - ADDITIVE:
                    problems.append(
                        f"{key}: merged in, but not additive "
                        f"{sorted(set(kinds) - ADDITIVE)}"
                    )
                elif merged_pins.get(key) != sha(source):
                    problems.append(
                        f"{key}: merged in, but its source is not the reviewed pin; "
                        "review it again and re-pin MERGED_IN_SHA256"
                    )
            elif missing:
                problems.append(f"{key}: does not extend the pinned leaf {missing}")
            if loader.disk_migrations[key].replaces:
                problems.append(
                    f"{key}: replaces {loader.disk_migrations[key].replaces}; "
                    "a migration after the pinned leaf replaces nothing"
                )
            if key in reserved:
                problems.append(f"{key}: takes a legacy or bridged name")
    return problems


class NoDatabase:
    alias = "migration_bridge_offline"

    def __getattr__(self, name):
        raise AssertionError(f"Database access forbidden: {name}")


@pytest.fixture(autouse=True)
def offline():
    with (
        patch("socket.socket", side_effect=AssertionError("Network forbidden")),
        patch(
            "socket.create_connection", side_effect=AssertionError("Network forbidden")
        ),
        patch.object(
            MigrationRecorder,
            "record_applied",
            side_effect=AssertionError("History write forbidden"),
        ),
        patch.object(
            MigrationRecorder,
            "record_unapplied",
            side_effect=AssertionError("History write forbidden"),
        ),
    ):
        yield


@contextmanager
def recorded_history(connection, recorded):
    def applied(recorder):
        assert recorder.connection is connection
        assert recorder.has_table()
        return dict.fromkeys(recorded)  # Fresh: the loader may mutate its copy.

    with (
        patch.object(MigrationRecorder, "has_table", return_value=True),
        patch.object(MigrationRecorder, "applied_migrations", applied),
    ):
        yield


def disk_metadata():
    disk, sources = {}, {}
    roots = []
    for app in C26["apps"]:
        root = BACKEND.parent / app["root"]
        configs = []
        for cls in ast.parse((root / "apps.py").read_text()).body:
            if isinstance(cls, ast.ClassDef):
                conf = {
                    t.id: ast.literal_eval(n.value)
                    for n in cls.body
                    if isinstance(n, ast.Assign)
                    for t in n.targets
                    if isinstance(t, ast.Name) and t.id in {"name", "label"}
                }
                if "name" in conf:
                    configs.append(conf)
        assert len(configs) == 1
        assert (
            configs[0].get("label", configs[0]["name"].rsplit(".", 1)[-1])
            == app["label"]
        )
        roots.append((app["label"], root / "migrations"))
    contrib = Path(django.__file__).resolve().parent / "contrib"
    roots.extend(
        (app, contrib / app / "migrations") for app in ("auth", "contenttypes")
    )
    for app, root in roots:
        for filename in sorted(root.glob("[0-9]*.py")):
            source = filename.read_text()
            attrs = attributes(source)
            key = (app, filename.stem)
            assert key not in disk
            migration = migrations.Migration(filename.stem, app)
            for attr in ("dependencies", "run_before", "replaces"):
                setattr(
                    migration, attr, graph_keys(attrs[attr]) if attr in attrs else []
                )
            disk[key], sources[key] = migration, source
    return disk, sources


@contextmanager
def executor_for(recorded, disk=None):
    if disk is None:
        disk, _ = disk_metadata()
    connection = NoDatabase()

    def load_disk(loader):
        loader.disk_migrations = dict(disk)
        loader.migrated_apps = {key[0] for key in disk}
        loader.unmigrated_apps = set()

    with (
        patch.object(MigrationLoader, "load_disk", load_disk),
        recorded_history(connection, recorded),
    ):
        executor = MigrationExecutor(connection)
        yield executor, connection


def complete_history(fixture):
    disk, _ = disk_metadata()
    return {tuple(k) for k in fixture["applied"]} | {
        k for k in disk if k[0] in {"auth", "contenttypes"}
    }


def planned(executor, leaves):
    plan = executor.migration_plan(leaves)
    assert all(not backwards for _, backwards in plan)
    return {(m.app_label, m.name) for m, _ in plan}


class SpyAddField(migrations.AddField):
    def __init__(self, name, calls):
        self.calls = calls
        super().__init__(
            model_name="probe", name=name, field=models.IntegerField(null=True)
        )

    def database_forwards(self, app_label, editor, from_state, to_state):
        self.calls.append(
            ("forwards", self.name, state_names(from_state), state_names(to_state))
        )

    def database_backwards(self, app_label, editor, from_state, to_state):
        self.calls.append(
            ("backwards", self.name, state_names(from_state), state_names(to_state))
        )


def state_names(state, app=None):
    return {
        key: (
            tuple(sorted(model.fields)),
            tuple(sorted(i.name for i in model.options.get("indexes", []))),
            tuple(sorted(c.name for c in model.options.get("constraints", []))),
        )
        for key, model in state.models.items()
        if app is None or key[0] == app
    }


@pytest.mark.parametrize("legacy_applied", [True, False])
@pytest.mark.parametrize("direction", ["forwards", "backwards"])
def test_wrapper_preserves_state_and_intermediate_database_states(
    legacy_applied, direction
):
    bridge = helper()
    legacy = ("prism", "0075_dispatch_claim")
    calls = []
    inner = [SpyAddField("first", calls), SpyAddField("second", calls)]
    wrapper = bridge.SkipDatabaseIfLegacyApplied(legacy=legacy, operations=inner)
    before = ProjectState()
    migrations.CreateModel(
        name="Probe", fields=[("id", models.IntegerField())]
    ).state_forwards("prism", before)
    middle = before.clone()
    inner[0].state_forwards("prism", middle)
    after = middle.clone()
    inner[1].state_forwards("prism", after)
    wrapped_state = before.clone()
    wrapper.state_forwards("prism", wrapped_state)
    assert wrapped_state == after
    editor = SimpleNamespace(connection=NoDatabase())
    with recorded_history(editor.connection, {legacy} if legacy_applied else set()):
        if direction == "forwards":
            wrapper.database_forwards("prism", editor, before, after)
        else:
            wrapper.database_backwards("prism", editor, after, before)
    expected = [
        ("forwards", "first", state_names(before), state_names(middle)),
        ("forwards", "second", state_names(middle), state_names(after)),
    ]
    if direction == "backwards":
        expected = [
            ("backwards", "second", state_names(after), state_names(middle)),
            ("backwards", "first", state_names(middle), state_names(before)),
        ]
    assert calls == ([] if legacy_applied else expected)
    assert state_names(before)[("prism", "probe")][0] == ("id",)


def test_wrapper_tooling_and_reversibility():
    cls = helper().SkipDatabaseIfLegacyApplied
    legacy = ("accounts", "0037_organizationmembership_removed_at")
    op = migrations.AddField(
        model_name="probe", name="extra", field=models.IntegerField()
    )
    wrapper = cls(legacy=legacy, operations=[op])
    name, args, kwargs = wrapper.deconstruct()
    assert name == cls.__name__ and args == []
    rebuilt = cls(**kwargs)
    assert rebuilt.legacy == legacy and rebuilt.operations == [op]
    assert wrapper.reversible and wrapper.reduces_to_sql
    assert legacy[1] in wrapper.describe()
    assert (
        wrapper.reduce(
            migrations.RunPython(migrations.RunPython.noop, elidable=True), "accounts"
        )
        is False
    )
    irreversible = cls(
        legacy=legacy, operations=[migrations.RunPython(migrations.RunPython.noop)]
    )
    assert not irreversible.reversible and not irreversible.reduces_to_sql
    assert not wrapper.elidable
    non_atomic = cls(
        legacy=legacy,
        operations=[
            migrations.RunPython(
                migrations.RunPython.noop, migrations.RunPython.noop, atomic=False
            )
        ],
    )
    assert non_atomic.atomic is False


def test_every_missing_legacy_name_has_an_exact_bridge_and_wrapper():
    module = helper()
    disk, sources = disk_metadata()
    expected = {
        (row["app"], row["newName"]): (row["app"], row["legacyName"]) for row in BRIDGES
    }
    assert module.LEGACY_APPLIED_NAMES == {**expected, TWIN: TWIN_LEGACY}
    recorded = {tuple(k) for k in C25["applied"]}
    assert recorded - set(disk) == set(expected.values()) - {
        ("accounts", "0037_organizationmembership_removed_at")
    }
    # The c2.5.x add-on twin is bridged too, but its legacy name is not in the
    # c2.5 FINAL history fixture.
    _, actual, inner = wrapper_call(sources[TWIN])
    assert actual == TWIN_LEGACY and isinstance(inner, ast.List) and inner.elts
    assert TWIN_LEGACY not in recorded
    for key, legacy in expected.items():
        assert key in disk and key[0] == legacy[0]
        row = next(r for r in BRIDGES if (r["app"], r["newName"]) == key)
        if row["nonempty"]:
            _, actual, inner = wrapper_call(sources[key])
            assert actual == legacy and isinstance(inner, ast.List) and inner.elts
        else:
            assert isinstance(attributes(sources[key])["operations"], ast.List)
            assert attributes(sources[key])["operations"].elts == []


def test_inner_operations_and_helpers_are_frozen_to_legacy_sources():
    for row in BRIDGES:
        source = (BACKEND.parent / row["basePath"]).read_text()
        if row["nonempty"]:
            _, actual, inner = wrapper_call(source)
            assert actual == (row["app"], row["legacyName"])
        else:
            inner = attributes(source)["operations"]
        assert sha(_canonical(inner)) == row["operationsExecutableSha256"], row[
            "basePath"
        ]
        assert (
            executable_sha(bare_source(source), drop_dependencies=True)
            == row["executableSha256"]
        ), row["basePath"]


def test_c25_history_is_consistent_and_plans_all_ten_new_names():
    with executor_for(complete_history(C25)) as (executor, connection):
        loader = executor.loader
        loader.check_consistent_history(connection)
        union = set()
        for leaf in loader.graph.leaf_nodes():
            pending = planned(executor, [leaf])
            expected = TEN & set(loader.graph.forwards_plan(leaf))
            assert expected <= pending
            union |= pending
        assert TEN <= union


def test_head35_keeps_all_ten_applied_and_never_replays_them():
    history = complete_history(C26)
    assert TEN <= history
    with executor_for(history) as (executor, connection):
        loader = executor.loader
        loader.check_consistent_history(connection)
        assert TEN <= set(loader.applied_migrations)
        assert not TEN & planned(executor, loader.graph.leaf_nodes())
        assert {tuple(k) for k in C26["applied"]} <= set(loader.disk_migrations)


def test_fresh_database_plans_all_ten():
    with executor_for(set()) as (executor, _):
        assert executor.loader.applied_migrations == {}
        assert TEN <= planned(executor, executor.loader.graph.leaf_nodes())


def test_legacy_names_are_never_replacement_targets():
    # Use the frozen mapping here too: this guard must work even at RED.
    legacy = {(r["app"], r["legacyName"]) for r in BRIDGES}
    disk, _ = disk_metadata()
    for key, migration in disk.items():
        assert not set(migration.replaces) & legacy, key
    # The first-appearance twin is a bridge too, never a replacement.
    assert disk[TWIN].replaces == []
    for key, migration in disk.items():
        assert TWIN_LEGACY not in set(migration.replaces), key


def name_operations(source, bridge_class=None):
    """Decode ONLY state names into built-in operations; never eval app code.

    Field types, relationships and other options are intentionally placeholders.
    All unknown operation kinds/dynamic expressions fail closed.
    """
    tree = ast.parse(source)
    constants = {
        t.id: n.value
        for n in tree.body
        if isinstance(n, ast.Assign)
        for t in n.targets
        if isinstance(t, ast.Name)
    }

    def literal(node):
        if isinstance(node, ast.Name):
            node = constants[node.id]
        return ast.literal_eval(node)

    def named(node):
        return literal(next(k.value for k in node.keywords if k.arg == "name"))

    def decode(node):
        assert isinstance(node, ast.Call), ast.dump(node)
        kind = ast.unparse(node.func).rsplit(".", 1)[-1]
        kw = {k.arg: k.value for k in node.keywords}
        positional = {
            "AddField": ("model_name", "name", "field", "preserve_default"),
            "AlterField": ("model_name", "name", "field", "preserve_default"),
            "RemoveField": ("model_name", "name"),
            "AddConstraint": ("model_name", "constraint"),
            "RemoveConstraint": ("model_name", "name"),
            "AddIndex": ("model_name", "index"),
            "AddIndexConcurrently": ("model_name", "index"),
            "RemoveIndex": ("model_name", "name"),
        }
        if node.args and kind in positional:
            assert len(node.args) <= len(positional[kind])
            for key, value in zip(positional[kind], node.args, strict=False):
                assert key not in kw
                kw[key] = value
        elif node.args:
            assert kind in {"RunPython", "RunSQL"}, kind
        if kind == "SkipDatabaseIfLegacyApplied":
            assert bridge_class is not None
            return [
                bridge_class(
                    legacy=literal(kw["legacy"]),
                    operations=decode_list(kw["operations"]),
                )
            ]
        if kind in {"SeparateDatabaseAndState", "RunSQL"}:
            return (
                decode_list(kw["state_operations"]) if "state_operations" in kw else []
            )
        if kind in {
            "RunPython",
            "AlterModelManagers",
            "AlterModelOptions",
            "AlterUniqueTogether",
            "AlterModelTable",
        }:
            return []  # None changes the four named sets under comparison.
        if kind == "CreateModel":
            fields = [
                (literal(pair.elts[0]), models.IntegerField())
                for pair in kw["fields"].elts
            ]
            opts = {}
            if "options" in kw:
                for key, value in zip(
                    kw["options"].keys, kw["options"].values, strict=True
                ):
                    attr = literal(key)
                    if attr == "indexes":
                        opts[attr] = [
                            models.Index(fields=["id"], name=named(n))
                            for n in value.elts
                        ]
                    elif attr == "constraints":
                        opts[attr] = [
                            models.UniqueConstraint(fields=["id"], name=named(n))
                            for n in value.elts
                        ]
            return [
                migrations.CreateModel(
                    name=literal(kw["name"]), fields=fields, options=opts
                )
            ]
        values = {
            k: literal(v)
            for k, v in kw.items()
            if k in {"model_name", "name", "old_name", "new_name", "old_fields"}
        }
        if kind in {"AddField", "AlterField"}:
            values["field"] = models.IntegerField()
        elif kind in {"AddIndex", "AddIndexConcurrently"}:
            values["index"] = models.Index(fields=["id"], name=named(kw["index"]))
            kind = "AddIndex"
        elif kind == "AddConstraint":
            values["constraint"] = models.UniqueConstraint(
                fields=["id"], name=named(kw["constraint"])
            )
        else:
            assert kind in {
                "RemoveField",
                "DeleteModel",
                "RemoveIndex",
                "RemoveConstraint",
                "RenameIndex",
                "RenameField",
                "RenameModel",
            }, kind
        return [getattr(migrations, kind)(**values)]

    def decode_list(node):
        assert isinstance(node, ast.List), ast.dump(node)
        return [op for child in node.elts for op in decode(child)]

    expression = attributes(source)["operations"]
    if isinstance(expression, ast.Call):
        # One historical source builds literal renames in loops. Its complete
        # source is pinned below; decode the constant names without running it.
        assert ast.unparse(expression) == "_build_ops()"
        result = [
            migrations.RenameModel(old_name=old, new_name=new)
            for old, new, _ in literal(constants["MODEL_RENAMES"])
        ]
        for row in constants["CONSTRAINT_RENAMES"].elts:
            model, old, new = [literal(n) for n in row.elts[:3]]
            result.extend(
                [
                    migrations.RemoveConstraint(model_name=model, name=old),
                    migrations.AddConstraint(
                        model_name=model,
                        constraint=models.UniqueConstraint(fields=["id"], name=new),
                    ),
                ]
            )
        return result
    return decode_list(expression)


def test_leaf_project_state_names_equal_the_pinned_base():
    bridge = helper()
    disk, sources = disk_metadata()
    problems = pin_problems(sources, C26["baseStateExecutableSha256"])
    assert not problems, "\n".join(problems)
    with executor_for(set(), disk) as (executor, _):
        loader = executor.loader
        problems = after_bridge_problems(loader, sources=sources)
        assert not problems, "\n".join(problems)
        for app in sorted(TARGET_APPS):
            # The bridge is proven at the pinned leaves; migrations listed after them add none.
            for leaf in [tuple(k) for k in C26["baseLeaves"][app]]:
                baseline, candidate = ProjectState(), ProjectState()
                for key in loader.graph.forwards_plan(leaf):
                    if key[0] not in TARGET_APPS:
                        continue
                    source = sources[key]
                    for op in name_operations(bare_source(source)):
                        op.state_forwards(key[0], baseline)
                    for op in name_operations(
                        source, bridge.SkipDatabaseIfLegacyApplied
                    ):
                        op.state_forwards(key[0], candidate)
                assert state_names(candidate, app) == state_names(baseline, app), leaf
                assert state_names(candidate, app), "Vacuous empty projected state"


# A small migration with every part the pin covers or leaves out.
SAMPLE = """\"\"\"Add a flag to each row.\"\"\"

from django.db import migrations, models

UPDATE_SAFETY = {"schema_version": 1, "tables": ["postgres:sample_row"]}


def fill(apps, schema_editor):
    \"\"\"Set the flag on existing rows.\"\"\"
    # One statement; rows are few.
    if apps is None:
        raise ValueError("no app registry")
    apps.get_model("sample", "Row").objects.update(flag=True)


class Migration(migrations.Migration):
    \"\"\"The flag and its backfill.\"\"\"

    dependencies = [("sample", "0001_initial")]

    operations = [
        migrations.AddField(model_name="row", name="flag", field=models.BooleanField(default=False)),
        migrations.RunPython(fill, migrations.RunPython.noop),
    ]
"""


@pytest.mark.parametrize(
    "old, new",
    [
        pytest.param(
            '"""Add a flag to each row."""',
            '"""Add a boolean flag to each row."""',
            id="module-docstring",
        ),
        pytest.param(
            '"""Set the flag on existing rows."""',
            '"""Backfill the flag."""',
            id="helper-docstring",
        ),
        pytest.param(
            '"""The flag and its backfill."""',
            '"""Adds the flag."""',
            id="class-docstring",
        ),
        pytest.param("    # One statement; rows are few.\n", "", id="comment"),
        pytest.param(
            '"tables": ["postgres:sample_row"]}',
            '"tables": ["postgres:sample_row"], "risk_classes": []}',
            id="update-safety-declaration",
        ),
    ],
)
def test_docstrings_comments_and_the_declaration_do_not_move_a_pin(old, new):
    changed = SAMPLE.replace(old, new, 1)
    assert changed != SAMPLE
    assert executable_sha(changed) == executable_sha(SAMPLE)


@pytest.mark.parametrize(
    "old, new",
    [
        pytest.param("default=False", "default=True", id="operation-argument"),
        pytest.param(
            "        migrations.RunPython(fill, migrations.RunPython.noop),\n",
            "",
            id="operation-removed",
        ),
        pytest.param(
            "objects.update(flag=True)", "objects.update(flag=False)", id="helper-body"
        ),
        pytest.param(
            'ValueError("no app registry")',
            'ValueError("the app registry is missing")',
            id="error-text",
        ),
        pytest.param(
            '("sample", "0001_initial")', '("sample", "0002_other")', id="dependency"
        ),
        pytest.param(
            "from django.db import migrations, models",
            "from django.db import migrations, models, transaction",
            id="import",
        ),
        pytest.param(
            "UPDATE_SAFETY = {",
            "UPDATE_SAFETY = BATCH = {",
            id="declaration-with-a-second-target",
        ),
        pytest.param(
            '{"schema_version": 1,',
            '{"schema_version": (BATCH := 1),',
            id="declaration-that-is-not-a-literal",
        ),
    ],
)
def test_any_executable_change_moves_a_pin(old, new):
    changed = SAMPLE.replace(old, new, 1)
    assert changed != SAMPLE
    assert executable_sha(changed) != executable_sha(SAMPLE)


def test_a_migration_after_the_pinned_leaf_must_be_listed():
    key = ("prism", "0999_example_after_the_pinned_leaf")
    unlisted = pin_problems({key: SAMPLE}, {}, after_bridge=frozenset())
    assert len(unlisted) == 1 and "AFTER_BRIDGE" in unlisted[0]
    assert pin_problems({key: SAMPLE}, {}, after_bridge=frozenset({key})) == []
    # A listed name must exist, and a pinned migration cannot also be listed.
    assert pin_problems({}, {}, after_bridge=frozenset({key}))
    assert pin_problems(
        {key: SAMPLE},
        {migration_file(key): executable_sha(SAMPLE)},
        after_bridge=frozenset({key}),
    )


def test_an_envelope_added_to_a_pinned_migration_moves_its_pin():
    key = ("prism", "0999_example_after_the_pinned_leaf")
    wrapped = (
        SAMPLE.replace(
            "from django.db import migrations, models\n",
            "from django.db import migrations, models\n"
            "from tfc.migration_bridge import SkipDatabaseIfLegacyApplied\n",
            1,
        )
        .replace(
            "    operations = [\n",
            '    operations = [SkipDatabaseIfLegacyApplied(legacy=("prism", "0075_dispatch_claim"), operations=[\n',
            1,
        )
        .replace("\n    ]\n", "\n    ])]\n", 1)
    )
    assert wrapped != SAMPLE
    pinned = {migration_file(key): executable_sha(SAMPLE)}
    assert pin_problems({key: SAMPLE}, pinned, after_bridge=frozenset()) == []
    assert len(pin_problems({key: wrapped}, pinned, after_bridge=frozenset())) == 1


def fake_loader(prism_leaves, plans=(), replaces=(), app_leaves=()):
    """A loader double: the given prism leaves, the leaves app_leaves gives an app,
    else the pinned leaves for accounts and simulate, and each listed migration's
    forward plan and replaces."""
    plans, replaces = dict(plans), dict(replaces)
    leaves = {app: [tuple(k) for k in C26["baseLeaves"][app]] for app in TARGET_APPS}
    for app, keys in dict(app_leaves).items():
        leaves[app] = list(keys)
    leaves["prism"] = list(prism_leaves)
    graph = SimpleNamespace(
        leaf_nodes=lambda app: leaves[app],
        forwards_plan=lambda key: plans.get(key, []),
    )
    disk = {key: SimpleNamespace(replaces=replaces.get(key, [])) for key in plans}
    return SimpleNamespace(graph=graph, disk_migrations=disk)


def test_after_bridge_problems_names_each_broken_extension():
    pinned = tuple(C26["baseLeaves"]["prism"][0])
    new = ("prism", "0999_example_after_the_pinned_leaf")
    cases = [
        (fake_loader([pinned, new], {new: [pinned, new]}), {new}, "one is expected"),
        (fake_loader([new]), set(), "neither the pinned leaf nor listed"),
        (fake_loader([new], {new: [new]}), {new}, "does not extend the pinned leaf"),
        (
            fake_loader(
                [new], {new: [pinned, new]}, {new: [("prism", "0001_initial")]}
            ),
            {new},
            "replaces nothing",
        ),
        (
            fake_loader([TWIN_LEGACY], {TWIN_LEGACY: [pinned, TWIN_LEGACY]}),
            {TWIN_LEGACY},
            "takes a legacy or bridged name",
        ),
    ]
    for loader, listed, message in cases:
        problems = after_bridge_problems(loader, after_bridge=frozenset(listed))
        assert len(problems) == 1 and message in problems[0], problems
    # The shipped list, per app: each listed migration extends the app's pinned leaf
    # and the ones listed before it; the leaf is the last listed one, else the pinned.
    plans, leaves = {}, {}
    for app in sorted(TARGET_APPS):
        base = [tuple(k) for k in C26["baseLeaves"][app]]
        listed = sorted(k for k in AFTER_BRIDGE if k[0] == app)
        for i, key in enumerate(listed):
            plans[key] = [*base, *listed[: i + 1]]
        leaves[app] = listed[-1:] or base
    shipped = fake_loader(leaves["prism"], plans, app_leaves=leaves)
    merges = {key: MIGRATION_SOURCE.format("") for key in MERGED_IN}
    pins = {key: sha(text) for key, text in merges.items()}
    assert after_bridge_problems(shipped, sources=merges, merged_pins=pins) == []
    # The shipped pins name exactly the merged-in migrations.
    assert set(MERGED_IN_SHA256) == set(MERGED_IN)


MIGRATION_SOURCE = (
    "from django.db import migrations\n\n\n"
    "class Migration(migrations.Migration):\n"
    "    operations = [{}]\n"
)


def test_a_merged_in_branch_is_merged_by_the_leaf_and_only_adds():
    pinned = tuple(C26["baseLeaves"]["prism"][0])
    fork = ("prism", "0998_example_forked_before_the_pinned_leaf")
    merge = ("prism", "0999_example_merge_after_the_pinned_leaf")
    listed = frozenset({fork, merge})
    plans = {fork: [fork], merge: [pinned, fork, merge]}
    adds = {fork: MIGRATION_SOURCE.format("migrations.AddField('m', 'f', None)")}
    removes = {fork: MIGRATION_SOURCE.format("migrations.RemoveField('m', 'f')")}
    opaque = {fork: MIGRATION_SOURCE.replace("[{}]", "OPERATIONS")}
    runs = {fork: MIGRATION_SOURCE.format("RunPython(forwards)")}
    pins = {fork: sha(adds[fork])}
    edited = {fork: MIGRATION_SOURCE.format("migrations.AddField('m', 'g', None)")}
    assert (
        after_bridge_problems(
            fake_loader([merge], plans),
            after_bridge=listed,
            merged_in=frozenset({fork}),
            sources=adds,
            merged_pins=pins,
        )
        == []
    )
    cases = [
        (fake_loader([merge], plans), frozenset(), adds, "does not extend the pinned"),
        (
            fake_loader([merge], {fork: [fork], merge: [pinned, merge]}),
            frozenset({fork}),
            adds,
            "the app's leaf does not merge it",
        ),
        (fake_loader([merge], plans), frozenset({fork}), removes, "not additive"),
        (fake_loader([merge], plans), frozenset({fork}), opaque, "cannot be read"),
        (fake_loader([merge], plans), frozenset({fork}), runs, "cannot be read"),
        (fake_loader([merge], plans), frozenset({fork}), {}, "cannot be read"),
        (fake_loader([merge], plans), frozenset({fork}), edited, "not the reviewed"),
    ]
    for loader, merged_in, sources, message in cases:
        problems = after_bridge_problems(
            loader,
            after_bridge=listed,
            merged_in=merged_in,
            sources=sources,
            merged_pins=pins,
        )
        assert len(problems) == 1 and message in problems[0], problems
    # The app's leaf is never itself merged in: it alone must extend the pinned leaf.
    leaf_merged_in = {**adds, merge: MIGRATION_SOURCE.format("")}
    problems = after_bridge_problems(
        fake_loader([merge], plans),
        after_bridge=listed,
        merged_in=frozenset({fork, merge}),
        sources=leaf_merged_in,
        merged_pins={key: sha(text) for key, text in leaf_merged_in.items()},
    )
    assert any("may not itself be merged in" in p for p in problems), problems
