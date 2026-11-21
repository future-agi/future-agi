#!/usr/bin/env python3
"""A cached dump of pytest's freshly migrated test database, for Backend CI.

Every pytest shard used to spend about six minutes on one serial `migrate`. A
shard now restores a dump of that result when one exists for exactly its
inputs, and one job per run still migrates from zero as the control:

    key      the cache key for this checkout: a hash of every migration file
             and of the other inputs that decide the migrated schema. A
             different key means no dump, and the shard migrates as before.
    dump     writes the schema and the list of applied migrations, gzipped.
    restore  loads a dump into an empty database in one transaction. Any
             error fails the job; nothing falls back to a partial database.
    rebuild  dumps a database, drops it and restores it from that dump. A
             shard with no cached dump runs it after its own migrate, so
             every shard starts from the same state, hit or miss.
    compare  the control: a cached dump must equal a fresh migrate (the same
             schema, the same applied migrations by name), or the run fails.
    tables   writes the tables that hold rows, with their row counts.
    seeded   the control for rows: every table must hold the same number of
             rows after a fresh migrate and after a restore, except the ones
             migration-seeded-tables.json names with a reason.

The dump holds the schema and the rows of django_migrations, and no other
row. That is deliberate:

- pytest (--reuse-db) runs Django's `migrate` on the restored database. It
  finds every migration applied, applies nothing, and then sends post_migrate,
  which seeds content types, permissions and the applications' own reference
  rows from the code under test. Nothing seeded is ever stale.
- Rows a data migration wrote are not carried, so a restored database lacks
  them. Which tables that empties is measured on every run (`seeded`) and
  listed in migration-seeded-tables.json; a new one fails the run until
  someone decides about it. A shard that migrated from zero lost those rows
  anyway at its first transactional test, which truncates every table.
- Seed rows can come from configuration that is not public, and a cache of a
  public repository is readable by any pull request. A schema is not.

Standard library only, so it runs before any dependency is installed.
"""

import argparse
import difflib
import gzip
import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

# Raise this to stop using every existing dump, for example after `compare`
# reports that a cached dump no longer equals a fresh migrate.
VERSION = 1

ROOT = Path(__file__).resolve().parents[2]

# What decides the migrated schema of pytest's database (tfc.settings.test,
# docker-compose.test.yml). Tracked files only: the key must not move with a
# virtualenv or a __pycache__ beside the migrations.
INPUTS = [
    ":(glob)futureagi/**/migrations/**",
    "futureagi/uv.lock",
    "futureagi/tfc/settings",
    "futureagi/conftest.py",
    "futureagi/pytest.ini",
    "futureagi/docker-compose.test.yml",
    "futureagi/.ci/db_dump.py",
    "futureagi/.ci/migrated_db.py",
]
# The private package that internal runs check out. The test settings do not
# install its application, so it should not change the schema; the key still
# keeps the two kinds of checkout apart, so neither can restore the other's dump.
OVERLAY = "futureagi/ee/cloud"

# pg_dump ends each of the two parts of a dump with this line.
COMPLETE = "-- PostgreSQL database dump complete"
PARTS = 2
MIGRATIONS_TABLE = "django_migrations"
COPY = re.compile(r"COPY (\S+) \((.*)\) FROM stdin;")

# Tables a data migration fills, which a restored database therefore holds
# empty. Each entry is a decision with its reason.
SEEDED = Path(__file__).with_name("migration-seeded-tables.json")
# Every table of the public schema with its exact row count, one per line.
ROW_COUNTS = (
    "SELECT c.relname || ' ' || (xpath('/row/n/text()', query_to_xml("
    "format('SELECT count(*) AS n FROM %I.%I', n.nspname, c.relname), false, true, '')))[1]::text "
    "FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace "
    "WHERE c.relkind = 'r' AND n.nspname = 'public' ORDER BY 1"
)


class DumpError(Exception):
    pass


def _say(text):
    sys.stdout.write(text + "\n")


def _git(cwd, *args):
    return subprocess.run(
        ["git", "-C", str(cwd), *args], check=True, stdout=subprocess.PIPE, timeout=120
    ).stdout


def cache_key(root=ROOT):
    """The cache key for the checkout at `root`."""
    digest = hashlib.sha256(f"{VERSION}\n".encode())
    for spec in INPUTS:
        # Mode, blob id and path of every tracked file under the pathspec.
        listed = _git(root, "ls-files", "-s", "-z", "--", spec)
        if not listed:
            raise DumpError(f"key input {spec!r} matches no tracked file")
        digest.update(spec.encode() + b"\n" + listed)
    overlay = (Path(root) / OVERLAY / ".git").exists()
    digest.update(b"overlay\n" if overlay else b"no overlay\n")
    return f"migrated-db-pytest-v{VERSION}-{digest.hexdigest()[:40]}"


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
    if lines.count(COMPLETE) != PARTS or COMPLETE not in lines[-12:]:
        raise DumpError(
            f"{path}: the dump is incomplete (it must hold {PARTS} finished pg_dump parts)"
        )
    return text


def normalise(text):
    """The dump as written, except what differs between two correct migrates.

    That is three things: the id and the time of each django_migrations row
    (kept as the sorted `app name` pairs, so two dumps with the same number of
    different migrations differ); pg_dump's two version comments; and its
    per-dump \\restrict token. Every other line is compared verbatim, comments
    and blank lines included, because a line of a function body or of a
    multi-line default can look like either.
    """
    out = []
    rows = None
    columns = None
    for line in text.split("\n"):
        if rows is not None:
            if line != "\\.":
                rows.append(line)
                continue
            if columns is None:
                out.append(f"<{len(rows)} rows>")
            else:
                out.extend(sorted(_applied(row, columns) for row in rows))
            rows = None
            continue
        if line.startswith(
            (
                "-- Dumped from database version",
                "-- Dumped by pg_dump version",
                "\\restrict ",
                "\\unrestrict ",
            )
        ):
            continue
        out.append(line)
        copy = COPY.fullmatch(line)
        if copy:
            rows = []
            columns = None
            if copy.group(1).split(".")[-1] == MIGRATIONS_TABLE:
                names = [name.strip() for name in copy.group(2).split(",")]
                if "app" not in names or "name" not in names:
                    raise DumpError(f"{MIGRATIONS_TABLE} has no app and name column")
                columns = (len(names), names.index("app"), names.index("name"))
    if rows is not None:
        raise DumpError("a COPY block has no end")
    return out


def _applied(row, columns):
    """One django_migrations row as `<applied> app name`."""
    width, app, name = columns
    fields = row.split("\t")
    if len(fields) != width:
        raise DumpError(
            f"a {MIGRATIONS_TABLE} row has {len(fields)} fields, not {width}"
        )
    return f"<applied> {fields[app]} {fields[name]}"


def compare(cached, fresh):
    """Lines of difference between two dumps; empty when they are equal."""
    return list(
        difflib.unified_diff(
            normalise(read_dump(cached)),
            normalise(read_dump(fresh)),
            "cached dump",
            "fresh migrate",
            lineterm="",
            n=1,
        )
    )


def dump(path, user, database, prefix):
    """Dump `database` through `prefix` (for example `docker compose exec -T db`).

    Two pg_dump parts in one file: the whole schema, then the rows of
    django_migrations (with the value of its sequence).
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".partial")
    base = [*prefix, "pg_dump", "-U", user, "--no-owner", "--no-privileges"]
    parts = (
        [*base, "--schema-only", database],
        [*base, "--data-only", f"--table={MIGRATIONS_TABLE}", database],
    )
    with gzip.open(partial, "wb") as out:
        for command in parts:
            with subprocess.Popen(command, stdout=subprocess.PIPE) as proc:
                shutil.copyfileobj(proc.stdout, out)
            if proc.returncode != 0:
                raise DumpError(f"pg_dump exited {proc.returncode}")
    read_dump(partial)
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
    read_dump(path)  # refuse an incomplete file before touching the database
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
        "SELECT count(*) FROM django_migrations",
    )
    if applied.returncode != 0 or int(applied.stdout.strip() or 0) == 0:
        raise DumpError("the restored database records no applied migration")
    return int(applied.stdout.strip())


def rebuild(path, user, database, prefix):
    """Replace `database` by a restore of its own dump, written to `path`.

    What a restored database is, made from a database that was migrated here:
    the schema and the applied migrations, and no other row.
    """
    dump(path, user, database, prefix)
    for command in ("dropdb", "createdb"):
        done = subprocess.run([*prefix, command, "-U", user, database], check=False)
        if done.returncode != 0:
            raise DumpError(f"{command} {database} exited {done.returncode}")
    return restore(path, user, database, prefix)


def populated(user, database, prefix):
    """{table: rows} for every table of `database` that holds a row."""
    listed = _psql(prefix, user, database, "-At", "-c", ROW_COUNTS)
    if listed.returncode != 0:
        raise DumpError(f"cannot count the rows of {database}")
    counts = {}
    for line in listed.stdout.decode().splitlines():
        table, rows = line.rsplit(" ", 1)
        if int(rows):
            counts[table] = int(rows)
    if MIGRATIONS_TABLE not in counts:
        raise DumpError(f"{database} records no applied migration")
    return counts


def read_counts(path):
    counts = {}
    for line in Path(path).read_text().splitlines():
        table, rows = line.rsplit(" ", 1)
        counts[table] = int(rows)
    if MIGRATIONS_TABLE not in counts:
        raise DumpError(f"{path}: no {MIGRATIONS_TABLE} row count; not a table list")
    return counts


def read_seeded(path=SEEDED):
    """{table: reason} of the tables a restore is allowed to leave empty."""
    try:
        entries = json.loads(Path(path).read_text())
    except (OSError, ValueError) as exc:
        raise DumpError(f"{path}: not a readable list ({exc})") from exc
    allowed = {}
    for entry in entries if isinstance(entries, list) else [None]:
        table = entry.get("table") if isinstance(entry, dict) else None
        reason = entry.get("reason") if isinstance(entry, dict) else None
        if not (
            isinstance(table, str)
            and table
            and isinstance(reason, str)
            and reason.strip()
        ):
            raise DumpError(f"{path}: each entry needs a table and a reason: {entry!r}")
        if table in allowed:
            raise DumpError(f"{path}: {table} is listed twice")
        allowed[table] = reason
    return allowed


def seeded(fresh, restored, allowed):
    """Compares the row count of every table after a fresh migrate and after a restore.

    Returns (problems, notes). A problem is a table whose two counts differ
    without an entry in `allowed`: it went empty, or it kept only the rows
    that post_migrate reseeds. Equal counts are not equal rows; a count is
    what this can say without the rows leaving the database.
    """
    problems = []
    notes = []
    for table in sorted(set(fresh) | set(restored)):
        before, after = fresh.get(table, 0), restored.get(table, 0)
        if before == after:
            continue
        if table in allowed:
            notes.append(
                f"{table}: {before} rows after a fresh migrate, {after} after a restore (allowed: {allowed[table]})"
            )
        elif before > after:
            problems.append(
                f"{table} holds {before} rows after a fresh migrate and {after} after a restore. "
                "A data migration seeds it and nothing reseeds it. Decide: add it to "
                "futureagi/.ci/migration-seeded-tables.json with the reason no test needs those rows, "
                "or reseed it after a restore (a post_migrate handler)."
            )
        else:
            problems.append(
                f"{table} holds {after} rows after a restore and {before} after a fresh migrate."
            )
    for table in sorted(allowed):
        if fresh.get(table, 0) == restored.get(table, 0):
            notes.append(
                f"{table} is in migration-seeded-tables.json but holds {fresh.get(table, 0)} rows in both states; its entry is out of date"
            )
    return problems, notes


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("key")
    for name in ("dump", "restore", "rebuild", "tables"):
        sub = commands.add_parser(name)
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
    sub = commands.add_parser("seeded")
    sub.add_argument("fresh")
    sub.add_argument("restored")
    args = parser.parse_args(argv)
    if args.command in ("dump", "restore", "rebuild", "tables") and not args.prefix:
        parser.error("a command prefix is required")
    try:
        if args.command == "key":
            _say(cache_key())
        elif args.command == "dump":
            dump(args.path, args.user, args.database, args.prefix)
            _say(f"dumped the schema of {args.database} to {args.path}")
        elif args.command == "restore":
            applied = restore(args.path, args.user, args.database, args.prefix)
            _say(
                f"restored {args.database} from {args.path}: {applied} applied migrations"
            )
        elif args.command == "rebuild":
            applied = rebuild(args.path, args.user, args.database, args.prefix)
            _say(
                f"rebuilt {args.database} from its own dump at {args.path}: "
                f"{applied} applied migrations, and no other row"
            )
        elif args.command == "tables":
            counts = populated(args.user, args.database, args.prefix)
            out = Path(args.path)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(
                "".join(f"{table} {rows}\n" for table, rows in sorted(counts.items()))
            )
            _say(f"{len(counts)} tables of {args.database} hold rows:")
            _say(out.read_text().rstrip("\n"))
        elif args.command == "seeded":
            problems, notes = seeded(
                read_counts(args.fresh), read_counts(args.restored), read_seeded()
            )
            for note in notes:
                _say(note)
            for problem in problems:
                _say(
                    f"::error title=A restored test database lacks seeded rows::{problem}"
                )
            if problems:
                return 1
            _say(
                "every table holds the same number of rows after a restore as after a fresh migrate, "
                "except the tables listed above"
            )
        else:
            difference = compare(args.cached, args.fresh)
            if difference:
                _say("\n".join(difference[:200]))
                _say(
                    "::error title=Cached database dump differs from a fresh migrate::"
                    "Something outside the key's inputs changed the migrated schema. "
                    "Raise VERSION in futureagi/.ci/db_dump.py in this pull request so no job restores the old dump."
                )
                return 1
            _say(
                "the cached dump equals a fresh migrate: same schema, same applied migrations"
            )
    except (DumpError, subprocess.CalledProcessError) as exc:
        _say(f"::error title=Database dump::{exc}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
