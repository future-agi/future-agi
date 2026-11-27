#!/usr/bin/env python3
"""The E2E stacks' saved database dumps: their names, records and checks.

Every E2E leg boots a stack and migrates its Postgres from zero. One leg per
stack of a dev push migrates a scratch database with that stack's own image
and saves a dump of it (.ci/e2e-db-control.sh); this file names that dump by
what the migrate reads and checks a saved record before anything trusts it.
The dump itself is made, restored and compared by the backend's tool,
db_dump.py.

Each stack has its own dump, made by its own image under its own settings,
so a difference between the stacks is never a failure.

The dump is the database after `manage.py migrate` alone, post_migrate
included: no cache table, no seed command. It is a public artifact.
"""

import argparse
import hashlib
import importlib.util
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "db_dump", Path(__file__).with_name("db_dump.py")
)
db_dump = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(db_dump)

# Raise this to stop using every saved E2E dump.
VERSION = 1
BACKEND = "futureagi/"
# The backend key's list says what a migrate imports or opens; these entries
# of it are read by pytest only.
PYTEST_ONLY = frozenset({"pytest.ini", "conftest.py", ".ci/migrated_db.py"})
# The image the migrate runs in, this file, and the script that runs the
# migrate.
IMAGE_FILES = frozenset({"Dockerfile.oss", ".ci/e2e_db.py", ".ci/e2e-db-control.sh"})
# Per stack: what names its Postgres image, builds its image on top of the
# backend's and holds the settings its migrate runs under. Whole files and
# folders: any edit misses, which costs one slow run.
STACKS = {
    "distributed": (
        "docker-compose.distributed.yml",
        "e2e/stack/docker-compose.e2e.yml",
        "e2e/stack/e2e.env",
    ),
    "standalone": (
        "docker-compose.yml",
        "e2e/stack/docker-compose.standalone-e2e.yml",
        "e2e/stack/standalone-e2e.env",
        "deploy/standalone/",
    ),
}
RECORD = ("dump.sql.gz", "preseed", "recipe")
SKIP_DIRS = frozenset({"node_modules", "site-packages", "__pycache__"})
# What a plain dump of one database, taken without owners and privileges,
# does not carry.
NOT_CARRIED = (
    ("a grant", r"\bGRANT\s+[\w, ]+\s+ON\s+[\w.\" ]+\s+TO\b"),
    ("a revoke", r"\bREVOKE\s+[\w, ]+\s+ON\s+[\w.\" ]+\s+FROM\b"),
    ("a role", r"\b(?:CREATE|ALTER|DROP)\s+(?:ROLE|USER)\b"),
    ("a database setting", r"\bALTER\s+(?:DATABASE|SYSTEM)\b"),
    ("an owner", r"\bOWNER\s+TO\b"),
)


def _say(text):
    sys.stdout.write(text + "\n")


def key_input(path, stack):
    """Whether a path of the repository is read by that stack's migrate."""
    if any(
        path.startswith(entry) if entry.endswith("/") else path == entry
        for entry in STACKS[stack]
    ):
        return True
    if not path.startswith(BACKEND):
        return False
    inner = path[len(BACKEND) :]
    return (
        "migrations" in inner.split("/")[:-1]
        or inner in IMAGE_FILES
        or inner in db_dump.KEY_FILES - PYTEST_ONLY
        or inner.startswith(db_dump.KEY_DIRS)
    )


def inputs_key(root, stack):
    """The name a stack's saved dump is published under: its committed migrate inputs."""
    if stack not in STACKS:
        raise db_dump.DumpError(f"no E2E stack is called {stack!r}")
    digest = hashlib.sha256(f"{VERSION}\0{stack}\0".encode())
    listing = subprocess.run(
        ["git", "-C", str(root), "ls-tree", "-r", "-z", "--full-tree", "HEAD"],
        check=True,
        stdout=subprocess.PIPE,
        timeout=120,
    ).stdout
    for entry in listing.split(b"\0"):
        path = entry.partition(b"\t")[2].decode("utf-8", "surrogateescape")
        if key_input(path, stack):
            digest.update(entry + b"\0")
    return f"e2e-migrated-db-v{VERSION}-{stack}-{digest.hexdigest()[:40]}"


def recipe(key, server_version_num):
    """The key plus the Postgres that made the dump (`SHOW server_version_num`).

    The key is taken from files before any Postgres runs; the server version
    is what actually ran.
    """
    if not re.fullmatch(r"\d{5,6}", server_version_num):
        raise db_dump.DumpError("the Postgres server version is not a version number")
    return f"{key}-pg{server_version_num}"


def _checksums(directory):
    return "".join(
        f"{hashlib.sha256((directory / name).read_bytes()).hexdigest()}  {name}\n"
        for name in RECORD
    )


def write_record(out, dump, preseed, saved_recipe):
    """The four files of a saved dump, in the backend record's layout."""
    out = Path(out)
    shutil.copyfile(dump, out / "dump.sql.gz")
    (out / "preseed").write_text(preseed + "\n")
    (out / "recipe").write_text(saved_recipe + "\n")
    (out / "checksums").write_text(_checksums(out))


def verify(directory, key):
    """The recipe of an intact record saved under `key`, or raise."""
    directory = Path(directory)
    entries = sorted(directory.iterdir()) if directory.is_dir() else []
    if sorted(p.name for p in entries) != sorted((*RECORD, "checksums")) or not all(
        p.is_file() and not p.is_symlink() for p in entries
    ):
        raise db_dump.DumpError("the saved dump is not exactly its four files")
    if (directory / "checksums").read_text() != _checksums(directory):
        raise db_dump.DumpError("the saved dump does not match its checksums")
    if not re.fullmatch(r"[0-9a-f]{64}\n", (directory / "preseed").read_text()):
        raise db_dump.DumpError("the saved dump's row digest is not a digest")
    saved = (directory / "recipe").read_text().removesuffix("\n")
    if not re.fullmatch(re.escape(key) + r"-pg\d{5,6}", saved):
        raise db_dump.DumpError("the saved dump is for other migrate inputs")
    return saved


def _sections(path):
    """The comparable lines of a dump, by table; the rest under "(schema)"."""
    sections = {"(schema)": []}
    table = None
    for line in db_dump.normalise(db_dump.read_dump(path)):
        if table is not None:
            if line == "\\.":
                table = None
            else:
                sections[table].append(line)
        elif line.startswith("COPY ") and line.endswith(" FROM stdin;"):
            table = line.split(" ", 2)[1]
            sections["(schema)"].append(line)
            sections.setdefault(table, [])
        else:
            sections["(schema)"].append(line)
    return sections


def differing(first, second):
    """Where two dumps differ: table names, or "(schema)". Never a value."""
    one, two = _sections(first), _sections(second)
    return sorted(
        name for name in one.keys() | two.keys() if one.get(name) != two.get(name)
    )


def not_carried(root):
    """(migration file, what) for each statement a restored dump would lack."""
    found = []
    for folder, dirs, files in os.walk(root):
        # Installed packages bring migrations of their own; Django's and the
        # other libraries' are not this repository's to judge.
        dirs[:] = sorted(
            d for d in dirs if not d.startswith(".") and d not in SKIP_DIRS
        )
        if Path(folder).name != "migrations":
            continue
        for name in sorted(files):
            if not name.endswith(".py"):
                continue
            path = Path(folder) / name
            text = path.read_text(encoding="utf-8", errors="replace")
            for what, pattern in NOT_CARRIED:
                if re.search(pattern, text, re.IGNORECASE):
                    found.append((path, what))
    return found


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    sub = commands.add_parser("key")
    sub.add_argument("stack")
    sub.add_argument("--root", default=str(db_dump.ROOT))
    sub = commands.add_parser("recipe")
    sub.add_argument("key")
    sub.add_argument("server_version_num")
    sub = commands.add_parser("verify")
    sub.add_argument("directory")
    sub.add_argument("key")
    sub = commands.add_parser("differing")
    sub.add_argument("first")
    sub.add_argument("second")
    sub = commands.add_parser("record")
    for name in ("out", "dump", "preseed", "recipe"):
        sub.add_argument(name)
    args = parser.parse_args(argv)
    try:
        if args.command == "key":
            _say(inputs_key(args.root, args.stack))
        elif args.command == "recipe":
            _say(recipe(args.key, args.server_version_num))
        elif args.command == "verify":
            _say(verify(args.directory, args.key))
        elif args.command == "differing":
            for name in differing(args.first, args.second):
                _say(name)
        else:
            write_record(args.out, args.dump, args.preseed, args.recipe)
    except (db_dump.DumpError, OSError, subprocess.CalledProcessError) as exc:
        _say(f"::error title=E2E database dump::{exc}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
