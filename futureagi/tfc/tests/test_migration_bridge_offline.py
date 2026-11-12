"""Offline bridge contracts; no application imports, settings, database or network.

Graph checks use Django's loader/executor with AST disk metadata. Leaf states
are real ProjectStates projected to model/field/index/constraint NAMES only;
all projected sources are pinned to head-54, including helper bodies. This is
not a field-type, router, DDL, data or makemigrations execution rehearsal.
Run with plugin autoload disabled, --noconftest -c /dev/null -o addopts=.
"""

import ast
from contextlib import contextmanager
import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import django
from django.db import migrations, models
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.loader import MigrationLoader
from django.db.migrations.operations.base import Operation
from django.db.migrations.recorder import MigrationRecorder
from django.db.migrations.state import ProjectState
import pytest

BACKEND = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).with_name("fixtures")
C25 = json.loads((FIXTURES / "c25_applied_migrations_657594d3.json").read_text())
C26 = json.loads((FIXTURES / "c26_prebridge_applied_migrations_head35.json").read_text())
BRIDGES = C25["bridges"]
TEN = {(row["app"], row["newName"]) for row in BRIDGES[:10]}
TWIN = ("prism", "0089_normalize_two_key_org_period")
TWIN_LEGACY = ("prism", "0081_normalize_two_key_org_period")
TARGET_APPS = {"prism", "simulate", "accounts"}


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def attributes(source):
    tree = ast.parse(source)
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Migration")
    return {t.id: n.value for n in cls.body if isinstance(n, ast.Assign)
            for t in n.targets if isinstance(t, ast.Name)}


def graph_keys(node):
    assert isinstance(node, (ast.List, ast.Tuple)), ast.dump(node)
    result = []
    for item in node.elts:
        if isinstance(item, ast.Call):
            assert ast.unparse(item) == "migrations.swappable_dependency(settings.AUTH_USER_MODEL)"
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
    assert isinstance(call, ast.Call) and ast.unparse(call.func) == "SkipDatabaseIfLegacyApplied"
    assert not call.args
    kwargs = {kw.arg: kw.value for kw in call.keywords}
    assert set(kwargs) == {"legacy", "operations"}
    return call, ast.literal_eval(kwargs["legacy"]), kwargs["operations"]


def bare_source(source):
    """Remove only our envelope, preserving the original operations bytes."""
    tree = ast.parse(source)
    attrs = attributes(source)
    ops = attrs["operations"]
    if not (isinstance(ops, ast.List) and len(ops.elts) == 1
            and isinstance(ops.elts[0], ast.Call)
            and ast.unparse(ops.elts[0].func) == "SkipDatabaseIfLegacyApplied"):
        return source
    _, legacy, inner = wrapper_call(source)
    outer_text = ast.get_source_segment(source, ops)
    inner_text = ast.get_source_segment(source, inner)
    assert source.count(outer_text) == 1
    source = source.replace(outer_text, inner_text, 1)
    source = source.replace("from tfc.migration_bridge import SkipDatabaseIfLegacyApplied\n", "", 1)
    source = source.replace(f"    # c2.5 recorded this as {legacy[0]}.{legacy[1]}.\n", "", 1)
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


def normalized_without_dependencies(source):
    tree = ast.parse(source)
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Migration")
    cls.body = [n for n in cls.body if not (isinstance(n, ast.Assign) and any(
        isinstance(t, ast.Name) and t.id == "dependencies" for t in n.targets))]
    return sha(_canonical(tree))


class NoDatabase:
    alias = "migration_bridge_offline"

    def __getattr__(self, name):
        raise AssertionError(f"Database access forbidden: {name}")


@pytest.fixture(autouse=True)
def offline():
    with (patch("socket.socket", side_effect=AssertionError("Network forbidden")),
          patch("socket.create_connection", side_effect=AssertionError("Network forbidden")),
          patch.object(MigrationRecorder, "record_applied", side_effect=AssertionError("History write forbidden")),
          patch.object(MigrationRecorder, "record_unapplied", side_effect=AssertionError("History write forbidden"))):
        yield


@contextmanager
def recorded_history(connection, recorded):
    def applied(recorder):
        assert recorder.connection is connection
        assert recorder.has_table()
        return dict.fromkeys(recorded)  # Fresh: the loader may mutate its copy.

    with (patch.object(MigrationRecorder, "has_table", return_value=True),
          patch.object(MigrationRecorder, "applied_migrations", applied)):
        yield


def disk_metadata():
    disk, sources = {}, {}
    roots = []
    for app in C26["apps"]:
        root = BACKEND.parent / app["root"]
        configs = []
        for cls in ast.parse((root / "apps.py").read_text()).body:
            if isinstance(cls, ast.ClassDef):
                conf = {t.id: ast.literal_eval(n.value) for n in cls.body
                        if isinstance(n, ast.Assign) for t in n.targets
                        if isinstance(t, ast.Name) and t.id in {"name", "label"}}
                if "name" in conf:
                    configs.append(conf)
        assert len(configs) == 1
        assert configs[0].get("label", configs[0]["name"].rsplit(".", 1)[-1]) == app["label"]
        roots.append((app["label"], root / "migrations"))
    contrib = Path(django.__file__).resolve().parent / "contrib"
    roots.extend((app, contrib / app / "migrations") for app in ("auth", "contenttypes"))
    for app, root in roots:
        for filename in sorted(root.glob("[0-9]*.py")):
            source = filename.read_text()
            attrs = attributes(source)
            key = (app, filename.stem)
            assert key not in disk
            migration = migrations.Migration(filename.stem, app)
            for attr in ("dependencies", "run_before", "replaces"):
                setattr(migration, attr, graph_keys(attrs[attr]) if attr in attrs else [])
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

    with patch.object(MigrationLoader, "load_disk", load_disk), recorded_history(connection, recorded):
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
        super().__init__(model_name="probe", name=name, field=models.IntegerField(null=True))

    def database_forwards(self, app_label, editor, from_state, to_state):
        self.calls.append(("forwards", self.name, state_names(from_state), state_names(to_state)))

    def database_backwards(self, app_label, editor, from_state, to_state):
        self.calls.append(("backwards", self.name, state_names(from_state), state_names(to_state)))


def state_names(state, app=None):
    return {key: (tuple(sorted(model.fields)),
                  tuple(sorted(i.name for i in model.options.get("indexes", []))),
                  tuple(sorted(c.name for c in model.options.get("constraints", []))))
            for key, model in state.models.items() if app is None or key[0] == app}


@pytest.mark.parametrize("legacy_applied", [True, False])
@pytest.mark.parametrize("direction", ["forwards", "backwards"])
def test_wrapper_preserves_state_and_intermediate_database_states(legacy_applied, direction):
    bridge = helper()
    legacy = ("prism", "0075_dispatch_claim")
    calls = []
    inner = [SpyAddField("first", calls), SpyAddField("second", calls)]
    wrapper = bridge.SkipDatabaseIfLegacyApplied(legacy=legacy, operations=inner)
    before = ProjectState()
    migrations.CreateModel(name="Probe", fields=[("id", models.IntegerField())]).state_forwards("prism", before)
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
    expected = [("forwards", "first", state_names(before), state_names(middle)),
                ("forwards", "second", state_names(middle), state_names(after))]
    if direction == "backwards":
        expected = [("backwards", "second", state_names(after), state_names(middle)),
                    ("backwards", "first", state_names(middle), state_names(before))]
    assert calls == ([] if legacy_applied else expected)
    assert state_names(before)[("prism", "probe")][0] == ("id",)


def test_wrapper_tooling_and_reversibility():
    cls = helper().SkipDatabaseIfLegacyApplied
    legacy = ("accounts", "0037_organizationmembership_removed_at")
    op = migrations.AddField(model_name="probe", name="extra", field=models.IntegerField())
    wrapper = cls(legacy=legacy, operations=[op])
    name, args, kwargs = wrapper.deconstruct()
    assert name == cls.__name__ and args == []
    rebuilt = cls(**kwargs)
    assert rebuilt.legacy == legacy and rebuilt.operations == [op]
    assert wrapper.reversible and wrapper.reduces_to_sql
    assert legacy[1] in wrapper.describe()
    assert wrapper.reduce(migrations.RunPython(migrations.RunPython.noop, elidable=True), "accounts") is False
    irreversible = cls(legacy=legacy, operations=[migrations.RunPython(migrations.RunPython.noop)])
    assert not irreversible.reversible and not irreversible.reduces_to_sql
    assert not wrapper.elidable
    non_atomic = cls(legacy=legacy, operations=[migrations.RunPython(migrations.RunPython.noop, migrations.RunPython.noop, atomic=False)])
    assert non_atomic.atomic is False


def test_every_missing_legacy_name_has_an_exact_bridge_and_wrapper():
    module = helper()
    disk, sources = disk_metadata()
    expected = {(row["app"], row["newName"]): (row["app"], row["legacyName"]) for row in BRIDGES}
    assert module.LEGACY_APPLIED_NAMES == {**expected, TWIN: TWIN_LEGACY}
    recorded = {tuple(k) for k in C25["applied"]}
    assert recorded - set(disk) == set(expected.values()) - {("accounts", "0037_organizationmembership_removed_at")}
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
        assert sha(ast.get_source_segment(source, inner)) == row["operationsSha256"], row["basePath"]
        assert normalized_without_dependencies(bare_source(source)) == row["normalizedSha256"], row["basePath"]


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
    constants = {t.id: n.value for n in tree.body if isinstance(n, ast.Assign)
                 for t in n.targets if isinstance(t, ast.Name)}

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
            for key, value in zip(positional[kind], node.args):
                assert key not in kw
                kw[key] = value
        elif node.args:
            assert kind in {"RunPython", "RunSQL"}, kind
        if kind == "SkipDatabaseIfLegacyApplied":
            assert bridge_class is not None
            return [bridge_class(legacy=literal(kw["legacy"]), operations=decode_list(kw["operations"]))]
        if kind in {"SeparateDatabaseAndState", "RunSQL"}:
            return decode_list(kw["state_operations"]) if "state_operations" in kw else []
        if kind in {"RunPython", "AlterModelManagers", "AlterModelOptions", "AlterUniqueTogether", "AlterModelTable"}:
            return []  # None changes the four named sets under comparison.
        if kind == "CreateModel":
            fields = [(literal(pair.elts[0]), models.IntegerField()) for pair in kw["fields"].elts]
            opts = {}
            if "options" in kw:
                for key, value in zip(kw["options"].keys, kw["options"].values):
                    attr = literal(key)
                    if attr == "indexes":
                        opts[attr] = [models.Index(fields=["id"], name=named(n)) for n in value.elts]
                    elif attr == "constraints":
                        opts[attr] = [models.UniqueConstraint(fields=["id"], name=named(n)) for n in value.elts]
            return [migrations.CreateModel(name=literal(kw["name"]), fields=fields, options=opts)]
        values = {k: literal(v) for k, v in kw.items() if k in {"model_name", "name", "old_name", "new_name", "old_fields"}}
        if kind in {"AddField", "AlterField"}:
            values["field"] = models.IntegerField()
        elif kind in {"AddIndex", "AddIndexConcurrently"}:
            values["index"] = models.Index(fields=["id"], name=named(kw["index"]))
            kind = "AddIndex"
        elif kind == "AddConstraint":
            values["constraint"] = models.UniqueConstraint(fields=["id"], name=named(kw["constraint"]))
        else:
            assert kind in {"RemoveField", "DeleteModel", "RemoveIndex", "RemoveConstraint", "RenameIndex", "RenameField", "RenameModel"}, kind
        return [getattr(migrations, kind)(**values)]

    def decode_list(node):
        assert isinstance(node, ast.List), ast.dump(node)
        return [op for child in node.elts for op in decode(child)]

    expression = attributes(source)["operations"]
    if isinstance(expression, ast.Call):
        # One historical source builds literal renames in loops. Its complete
        # source is pinned below; decode the constant names without running it.
        assert ast.unparse(expression) == "_build_ops()"
        result = [migrations.RenameModel(old_name=old, new_name=new)
                  for old, new, _ in literal(constants["MODEL_RENAMES"])]
        for row in constants["CONSTRAINT_RENAMES"].elts:
            model, old, new = [literal(n) for n in row.elts[:3]]
            result.extend([migrations.RemoveConstraint(model_name=model, name=old),
                           migrations.AddConstraint(model_name=model, constraint=models.UniqueConstraint(fields=["id"], name=new))])
        return result
    return decode_list(expression)


def test_leaf_project_state_names_equal_the_pinned_base():
    bridge = helper()
    disk, sources = disk_metadata()
    expected_sources = C26["baseStateSourceSha256"]
    actual_paths = set()
    for key, source in sources.items():
        if key[0] not in TARGET_APPS:
            continue
        root = next(app["root"] for app in C26["apps"] if app["label"] == key[0])
        filename = f"{root}/migrations/{key[1]}.py"
        actual_paths.add(filename)
        assert sha(bare_source(source)) == expected_sources[filename], filename
    assert actual_paths == set(expected_sources)
    with executor_for(set(), disk) as (executor, _):
        loader = executor.loader
        for app in sorted(TARGET_APPS):
            assert loader.graph.leaf_nodes(app) == [tuple(k) for k in C26["baseLeaves"][app]]
            for leaf in loader.graph.leaf_nodes(app):
                baseline, candidate = ProjectState(), ProjectState()
                for key in loader.graph.forwards_plan(leaf):
                    if key[0] not in TARGET_APPS:
                        continue
                    source = sources[key]
                    for op in name_operations(bare_source(source)):
                        op.state_forwards(key[0], baseline)
                    for op in name_operations(source, bridge.SkipDatabaseIfLegacyApplied):
                        op.state_forwards(key[0], candidate)
                assert state_names(candidate, app) == state_names(baseline, app), leaf
                assert state_names(candidate, app), "Vacuous empty projected state"
