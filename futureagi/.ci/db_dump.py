#!/usr/bin/env python3
"""A saved dump of pytest's freshly migrated test database, for Backend CI.

The dump is the whole database (schema and rows) at the moment migrate has
applied every migration and has not yet sent post_migrate (.ci/migrated_db.py
takes it there). A restore followed by pytest-django's migrate applies nothing
and sends post_migrate, so seed rows from signal handlers come from the code
under test. Only a dump made without the private overlay is ever published.
"""

import argparse
import difflib
import gzip
import hashlib
import re
import shutil
import subprocess
import sys
from pathlib import Path

# Raise this to stop using every existing dump, for example after `compare`
# reports that a saved dump no longer equals a fresh migrate.
VERSION = 3
# Reserved for a completed comparison of two valid, different dumps. Ordinary
# tool failures (including an unexpected Python exception) must not mean drift.
COMPARE_DIFFERENT = 3
ROOT = Path(__file__).resolve().parents[2]
BACKEND = "futureagi/"
# What a migrate reads before post_migrate, relative to futureagi/: every
# migrations package, the settings that pick the apps, the locked
# dependencies, this tool, and the helpers and data files that migrations
# import or open. Any other file can change without a new dump. When one
# does change what migrate writes, the control job's compare fails the run:
# add that path here (or raise VERSION) in the same pull request.
KEY_FILES = frozenset(
    {
        "uv.lock",
        "pyproject.toml",
        "pytest.ini",
        "conftest.py",
        "tfc/ee_loader.py",
        ".ci/db_dump.py",
        ".ci/migrated_db.py",
    }
)
KEY_DIRS = ("tfc/settings/",)
LEDGER = "COPY public.django_migrations (id, app, name, applied) FROM stdin;"
# pg_dump ends a dump with this line.
COMPLETE = "-- PostgreSQL database dump complete"
# Values two correct migrates write differently: generated ids and the time
# the row was written. Two dumps are compared with these masked.
GENERATED = (
    (re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}"), "<uuid>"),
    (
        re.compile(r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d(?:\.\d+)?(?:[+-]\d\d(?::\d\d)?)?"),
        "<time>",
    ),
)


class DumpError(Exception):
    pass


def _say(text):
    sys.stdout.write(text + "\n")


def _git(cwd, *args):
    return subprocess.run(
        ["git", "-C", str(cwd), *args], check=True, stdout=subprocess.PIPE, timeout=120
    ).stdout


def key_input(path):
    """Whether a path under futureagi/ is a migrate input (see KEY_FILES)."""
    return (
        "migrations" in path.split("/")[:-1]
        or path in KEY_FILES
        or path.startswith(KEY_DIRS)
    )


def inputs_key(root):
    """The committed migrate inputs: the name a saved dump is published under.

    Never the whole tree (every commit would miss), never the private overlay
    (a published dump holds only what the public tree migrates).
    """
    digest = hashlib.sha256(f"{VERSION}\0".encode())
    listing = _git(root, "ls-tree", "-r", "-z", "--full-tree", "HEAD", "--", BACKEND)
    for entry in listing.split(b"\0"):
        path = entry.partition(b"\t")[2].decode("utf-8", "surrogateescape")
        if path.startswith(BACKEND) and key_input(path[len(BACKEND) :]):
            digest.update(entry + b"\0")
    return f"migrated-db-v{VERSION}-{digest.hexdigest()[:40]}"


def cache_key(root, runtime):
    """The exact recipe: the migrate inputs plus the service images and tools."""
    if not runtime.strip():
        raise DumpError("missing runtime fingerprint")
    return f"{inputs_key(root)}-{hashlib.sha256(runtime.encode()).hexdigest()[:16]}"


def read_dump(path):
    """The SQL of a gzipped dump. Refuses one that is not complete."""
    try:
        with gzip.open(
            path, "rt", encoding="utf-8", errors="surrogateescape", newline="\n"
        ) as handle:
            text = handle.read()
    except (OSError, EOFError) as exc:
        raise DumpError(f"{path}: not a readable gzip dump ({exc})") from exc
    lines = text.split("\n")
    if lines.count(COMPLETE) != 1 or COMPLETE not in lines[-12:]:
        raise DumpError(f"{path}: the dump is incomplete (one finished pg_dump)")
    return text


def _masked(row):
    fields = row.split("\t")
    for index, field in enumerate(fields):
        for pattern, mark in GENERATED:
            if pattern.fullmatch(field):
                fields[index] = mark
    return "\t".join(fields)


def normalise(text, *, identities=None):
    """The dump as comparable lines, and the exact migration identities.

    The ledger keeps (app, name) and drops its id and applied time. Rows of
    other tables are sorted (COPY order is physical) with generated values
    masked. Every other line, schema included, is compared as written.
    """
    out = []
    rows = None
    ledger = False
    seen = False
    for line in text.split("\n"):
        if rows is not None:
            if line != "\\.":
                if ledger:
                    fields = line.split("\t")
                    if len(fields) != 4 or not all(fields):
                        raise DumpError("invalid migration ledger row")
                    line = "\t".join(fields[1:3])
                else:
                    line = _masked(line)
                rows.append(line)
                continue
            if ledger:
                if not rows or len(set(rows)) != len(rows):
                    raise DumpError("missing or duplicate migration identity")
                if identities is not None:
                    identities.extend(sorted(rows))
            out.extend(sorted(rows))
            out.append(line)
            rows = None
            continue
        if line.startswith(("\\restrict ", "\\unrestrict ")):
            continue
        out.append(line)
        if line.startswith("COPY ") and line.endswith(" FROM stdin;"):
            ledger = line == LEDGER
            if ledger and seen:
                raise DumpError("two migration ledgers in one dump")
            seen = seen or ledger
            rows = []
    if rows is not None or not seen:
        raise DumpError("missing or unfinished migration ledger")
    return out


def compare(cached, fresh):
    """Lines of difference between two dumps; empty when they are equal."""
    return list(
        difflib.unified_diff(
            normalise(read_dump(cached)),
            normalise(read_dump(fresh)),
            "saved dump",
            "fresh migrate",
            lineterm="",
            n=1,
        )
    )


def tables(path):
    """(table, rows) for every table that holds a row. Names and counts only."""
    counts = {}
    table = None
    for line in read_dump(path).split("\n"):
        if table is not None:
            if line == "\\.":
                table = None
            else:
                counts[table] = counts.get(table, 0) + 1
        elif line.startswith("COPY ") and line.endswith(" FROM stdin;"):
            table = line.split(" ", 2)[1]
    return sorted(counts.items())


def dump(path, user, database, prefix):
    """Dump `database` through `prefix` (for example `docker compose exec -T db`).

    One plain pg_dump: the schema, every row, and the value of every sequence.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".partial")
    command = [*prefix, "pg_dump", "-U", user, "--no-owner", "--no-privileges"]
    with gzip.open(partial, "wb") as out:
        with subprocess.Popen([*command, database], stdout=subprocess.PIPE) as proc:
            shutil.copyfileobj(proc.stdout, out)
        if proc.returncode != 0:
            raise DumpError(f"pg_dump exited {proc.returncode}")
    normalise(read_dump(partial))
    partial.replace(path)


def _psql(prefix, user, database, *args, stdin=None):
    return subprocess.run(
        [
            *prefix,
            "psql",
            "-X",
            "-q",
            "-v",
            "ON_ERROR_STOP=1",
            "-U",
            user,
            "-d",
            database,
            *args,
        ],
        stdin=stdin,
        stdout=subprocess.PIPE,
        check=False,
    )


def restore(path, user, database, prefix):
    """Load the dump at `path` into the empty `database`, or raise."""
    expected = []
    normalise(read_dump(path), identities=expected)  # before touching the database
    count = "SELECT count(*) FROM pg_catalog.pg_tables WHERE schemaname NOT IN ('pg_catalog', 'information_schema')"
    before = _psql(prefix, user, database, "-At", "-c", count)
    if before.returncode != 0 or before.stdout.strip() != b"0":
        raise DumpError(
            f"{database} is not an empty, reachable database; refusing to restore into it"
        )
    gunzip = subprocess.Popen(["gzip", "-dc", str(path)], stdout=subprocess.PIPE)
    # `-f -` reads the script from stdin; psql accepts --single-transaction
    # only together with -f or -c.
    loaded = _psql(
        prefix, user, database, "--single-transaction", "-f", "-", stdin=gunzip.stdout
    )
    gunzip.stdout.close()
    if gunzip.wait() != 0 or loaded.returncode != 0:
        raise DumpError(
            f"restore failed (gzip {gunzip.returncode}, psql {loaded.returncode})"
        )
    applied = _psql(
        prefix,
        user,
        database,
        "-At",
        "-c",
        "COPY (SELECT app, name FROM public.django_migrations) TO STDOUT",
    )
    actual = applied.stdout.decode("utf-8").removesuffix("\n").split("\n")
    if applied.returncode or sorted(actual) != expected:
        raise DumpError("the restored migration identities do not match the dump")
    return len(expected)


def seed_digest(user, database, prefix):
    """An exact digest of every row except the ledger, and all sequence state.

    It proves a restore reproduces the dumped database byte for byte at the
    post_migrate boundary. Physical COPY order and random psql restriction
    tokens are not data. Only the digest is printed, never a row.
    """
    output = subprocess.run(
        [
            *prefix,
            "pg_dump",
            "-U",
            user,
            "--data-only",
            "--no-owner",
            "--no-privileges",
            "--exclude-table=public.django_migrations",
            database,
        ],
        check=True,
        stdout=subprocess.PIPE,
        timeout=120,
    ).stdout
    return fingerprint_rows(output)


def fingerprint_rows(output):
    digest = hashlib.sha256()
    rows = None
    complete = False
    for line in output.split(b"\n"):
        if rows is not None:
            if line != b"\\.":
                rows.append(hashlib.sha256(line).digest())
                continue
            for row in sorted(rows):
                digest.update(row)
            rows = None
        elif line.startswith(b"COPY ") and line.endswith(b" FROM stdin;"):
            rows = []
        elif re.fullmatch(rb"\\(?:un)?restrict [A-Za-z0-9]+", line):
            continue
        elif line == COMPLETE.encode():
            complete = True
        digest.update(line + b"\n")
    if rows is not None or not complete:
        raise DumpError("incomplete seed comparison")
    return digest.hexdigest()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("key").add_argument("runtime")
    commands.add_parser("inputs-key")
    commands.add_parser("tables").add_argument("path")
    for name in ("dump", "restore", "seed-digest"):
        sub = commands.add_parser(name)
        if name != "seed-digest":
            sub.add_argument("path")
        sub.add_argument("user")
        sub.add_argument("database")
        # REMAINDER, not "+": the prefix carries options of its own
        # (`docker compose -f ... exec -T db`) that argparse must not read.
        sub.add_argument(
            "prefix",
            nargs=argparse.REMAINDER,
            help="command that runs a program beside the database",
        )
    sub = commands.add_parser("compare")
    sub.add_argument("cached")
    sub.add_argument("fresh")
    args = parser.parse_args(argv)
    if args.command in ("dump", "restore", "seed-digest") and not args.prefix:
        parser.error("a command prefix is required")
    try:
        if args.command == "key":
            _say(cache_key(ROOT, args.runtime))
        elif args.command == "inputs-key":
            _say(inputs_key(ROOT))
        elif args.command == "tables":
            for table, rows in tables(args.path):
                _say(f"{rows}\t{table}")
        elif args.command == "seed-digest":
            _say(seed_digest(args.user, args.database, args.prefix))
        elif args.command == "dump":
            dump(args.path, args.user, args.database, args.prefix)
            _say(f"dumped {args.database} to {args.path}")
        elif args.command == "restore":
            applied = restore(args.path, args.user, args.database, args.prefix)
            _say(
                f"restored {args.database} from {args.path}: {applied} applied migrations"
            )
        else:
            difference = compare(args.cached, args.fresh)
            if difference:
                # Never print the lines: the fresh side can hold the private
                # overlay's schema or rows.
                _say(
                    "::error title=Saved database dump differs from a fresh migrate::"
                    f"{len(difference)} diff lines. A file outside the key's inputs changed what migrate writes. "
                    "Add it to KEY_FILES or KEY_DIRS, or raise VERSION, in futureagi/.ci/db_dump.py in this pull request."
                )
                return COMPARE_DIFFERENT
            _say(
                "the saved dump equals a fresh migrate: same schema, same rows up to generated ids and times"
            )
    except (DumpError, subprocess.CalledProcessError) as exc:
        _say(f"::error title=Database dump::{exc}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
