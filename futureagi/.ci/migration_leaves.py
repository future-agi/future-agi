"""An early "no" for an app with two latest migrations.

Django refuses to migrate an app whose migrations end in more than one place
(two "leaves"): two branches each added a migration after the same parent.
`makemigrations --check` says so in its own job, but nothing waits for that
job, so every test job starts, sets itself up and fails at its migrate step.
This script gives the same "no" in about a second, before those jobs start.

It reads the migration files as text (ast); it imports nothing from them and
needs no Django, no settings and no database. Django's check stays the
authority. Wherever this script cannot be sure, it gives no verdict for that
app and prints a warning: it must never say "no" where Django says "yes".

Usage: migration_leaves.py [ROOT]   (ROOT defaults to futureagi)
Exit 1 if any app has more than one leaf, else 0.
"""

import ast
import os
import pathlib
import sys

SKIP_DIRS = {"node_modules", "site-packages", "__pycache__"}


class NoVerdict(Exception):
    """This app cannot be judged from its files alone; Django decides."""


def migration_dirs(root):
    """Every `migrations` package under root, sorted."""
    found = []
    for path, dirs, files in os.walk(root):
        dirs[:] = sorted(
            d for d in dirs if not d.startswith(".") and d not in SKIP_DIRS
        )
        if pathlib.Path(path).name == "migrations" and "__init__.py" in files:
            found.append(pathlib.Path(path))
            dirs[:] = []
    return found


def app_label(app_dir):
    """The label Django knows the app by: `label` in apps.py, else the folder's name."""
    apps = app_dir / "apps.py"
    labels = set()
    if apps.is_file():
        try:
            tree = ast.parse(apps.read_text(encoding="utf-8"))
        except (SyntaxError, ValueError, OSError) as error:
            raise NoVerdict(
                f"{apps} cannot be read ({error.__class__.__name__})"
            ) from error
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            for line in node.body:
                if isinstance(line, ast.Assign) and any(
                    isinstance(t, ast.Name) and t.id == "label" for t in line.targets
                ):
                    if not (
                        isinstance(line.value, ast.Constant)
                        and isinstance(line.value.value, str)
                    ):
                        raise NoVerdict(
                            f"{apps} sets a label that is not a plain string"
                        )
                    labels.add(line.value.value)
    if len(labels) > 1:
        raise NoVerdict(f"{apps} sets more than one label: {sorted(labels)}")
    return labels.pop() if labels else app_dir.name


def own_parents(path, label):
    """The migrations of this app that the migration file at `path` names in `dependencies`."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, ValueError, OSError) as error:
        raise NoVerdict(
            f"{path} cannot be read ({error.__class__.__name__})"
        ) from error
    classes = [
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "Migration"
    ]
    if len(classes) != 1:
        raise NoVerdict(f"{path} does not hold exactly one Migration class")
    # One plain `dependencies = [...]` is all this reads. A second mention
    # (`dependencies += ...`, `Migration.dependencies.append(...)`) could add
    # a parent this would miss.
    mentions = sum(
        (isinstance(node, ast.Name) and node.id == "dependencies")
        or (isinstance(node, ast.Attribute) and node.attr == "dependencies")
        for node in ast.walk(tree)
    )
    if mentions > 1:
        raise NoVerdict(
            f"{path}: dependencies is set or changed in more than one place"
        )
    parents = []
    for line in classes[0].body:
        if isinstance(line, ast.AnnAssign):
            targets, value = [line.target], line.value
        elif isinstance(line, ast.Assign):
            targets, value = line.targets, line.value
        else:
            continue
        names = {t.id for t in targets if isinstance(t, ast.Name)}
        # Squashed migrations and run_before change the graph in ways only
        # Django's loader resolves.
        for special in ("replaces", "run_before"):
            if special in names and not (
                isinstance(value, (ast.List, ast.Tuple)) and not value.elts
            ):
                raise NoVerdict(f"{path} sets {special}")
        if "dependencies" not in names:
            continue
        if not isinstance(value, (ast.List, ast.Tuple)):
            raise NoVerdict(f"{path}: dependencies is not a plain list")
        for item in value.elts:
            plain = (
                isinstance(item, (ast.Tuple, ast.List))
                and len(item.elts) == 2
                and all(
                    isinstance(e, ast.Constant) and isinstance(e.value, str)
                    for e in item.elts
                )
            )
            if plain:
                if item.elts[0].value == label:
                    parents.append(item.elts[1].value)
                continue
            # swappable_dependency(...) names the first migration of an app.
            # With exactly one first migration (checked by the caller), that
            # cannot change which migrations are last. Any other expression
            # could name a migration of this app.
            call = item.func if isinstance(item, ast.Call) else None
            name = (
                call.attr
                if isinstance(call, ast.Attribute)
                else getattr(call, "id", None)
            )
            if name != "swappable_dependency":
                raise NoVerdict(
                    f"{path}: a dependency is neither a plain pair nor swappable_dependency(...)"
                )
    return parents


def leaves(migrations_dir):
    """(label, sorted leaf names) for one app. Raises NoVerdict when unsure."""
    label = app_label(migrations_dir.parent)
    # As Django's loader: every module of the package except names that
    # start with "_" or "~"; packages inside it are not migrations.
    files = sorted(p for p in migrations_dir.glob("*.py") if p.name[0] not in "_~")
    parents = {path.stem: own_parents(path, label) for path in files}
    named = set()
    for name, own in parents.items():
        for parent in own:
            if parent not in parents:
                raise NoVerdict(
                    f"{migrations_dir / name}.py depends on ({label!r}, {parent!r}), which is not a file here"
                )
            named.add(parent)
    if not parents:
        return label, []
    # Exactly one migration that follows nothing. More than one usually means
    # the label above is not the one the files use; then every file would
    # look like a leaf.
    first = [name for name, own in parents.items() if not own]
    if len(first) != 1:
        raise NoVerdict(
            f"{migrations_dir}: {len(first)} migrations depend on no migration of {label!r}; expected exactly 1"
        )
    last = sorted(name for name in parents if name not in named)
    if not last:
        raise NoVerdict(
            f"{migrations_dir}: every migration is followed by another (a cycle)"
        )
    return label, last


def check(root):
    """(clashes, warnings, judged): clashes are (label, dir, leaf names)."""
    clashes, warnings, judged = [], [], 0
    for migrations_dir in migration_dirs(root):
        try:
            label, last = leaves(migrations_dir)
        except NoVerdict as reason:
            warnings.append(str(reason))
            continue
        if not last:
            continue
        judged += 1
        if len(last) > 1:
            clashes.append((label, migrations_dir, last))
    return clashes, warnings, judged


def message(label, migrations_dir, last):
    names = " and ".join(last) if len(last) == 2 else ", ".join(last)
    return (
        f"The app '{label}' ({migrations_dir}) has {len(last)} latest migrations: {names}. "
        "An app must end in one migration; Django refuses to migrate it like this, so no test job was started. "
        "This happens when another pull request added a migration to the same app before yours merged. "
        f"What to do: merge the latest dev into your branch, then make your migration follow the other one: "
        f"in your migration file set dependencies to ('{label}', '<the other migration>'), "
        "give the file the next free number, and push."
    )


def main(argv):
    root = pathlib.Path(argv[1] if len(argv) > 1 else "futureagi")
    if not root.is_dir():
        sys.stdout.write(
            f"::warning::migration leaves: {root} is not a directory; nothing checked\n"
        )
        return 0
    clashes, warnings, judged = check(root)
    for reason in warnings:
        sys.stdout.write(
            f"::warning::migration leaves: no verdict, Django's check decides: {reason}\n"
        )
    for label, migrations_dir, last in clashes:
        sys.stdout.write(
            f"::error title=Two latest migrations in one app::{message(label, migrations_dir, last)}\n"
        )
    sys.stdout.write(
        f"migration leaves: {judged} apps read, {len(clashes)} with more than one latest migration, {len(warnings)} without a verdict\n"
    )
    return 1 if clashes else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
