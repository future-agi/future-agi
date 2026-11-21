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
    compare  the control: a cached dump must equal a fresh migrate, or the
             run fails.

The dump holds the schema and the rows of django_migrations, and no other
row. That is deliberate:

- pytest (--reuse-db) runs Django's `migrate` on the restored database. It
  finds every migration applied, applies nothing, and then sends post_migrate,
  which seeds content types, permissions and the applications' own reference
  rows from the code under test. Nothing seeded is ever stale.
- Rows a data migration wrote are not carried. A shard already loses them at
  its first transactional test, which truncates every table, so no test can
  depend on them.
- Seed rows can come from configuration that is not public, and a cache of a
  public repository is readable by any pull request. A schema is not.

Standard library only, so it runs before any dependency is installed.
"""

import argparse
import difflib
import gzip
import hashlib
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

    That is three things: the row values of django_migrations (each row stamps
    the time it was applied), kept as a count; pg_dump's two version comments;
    and its per-dump \\restrict token. Every other line is compared verbatim,
    comments and blank lines included, because a line of a function body or of
    a multi-line default can look like either.
    """
    out = []
    rows = None
    for line in text.split("\n"):
        if rows is not None:
            if line == "\\.":
                out.append(f"<{rows} rows>")
                rows = None
            else:
                rows += 1
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
        if line.startswith("COPY ") and line.endswith(" FROM stdin;"):
            rows = 0
    if rows is not None:
        raise DumpError("a COPY block has no end")
    return out


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


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("key")
    for name in ("dump", "restore"):
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
    args = parser.parse_args(argv)
    if args.command in ("dump", "restore") and not args.prefix:
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
