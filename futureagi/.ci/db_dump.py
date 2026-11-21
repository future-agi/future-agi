#!/usr/bin/env python3
"""A cached dump of a freshly migrated Postgres database, for CI.

Every test job used to spend about six minutes on one serial `migrate`. A job
now restores a dump of that same result when one exists for exactly its
inputs, and one job per run still migrates from zero as the control:

    key      the cache key for this checkout: a hash of every migration file
             and of the other inputs that decide the migrated database. A
             different key means no dump, and the job migrates as before.
    dump     writes a complete plain-SQL dump of a database, gzipped.
    restore  loads a dump into an empty database in one transaction. Any
             error fails the job; nothing falls back to a partial database.
    compare  the control: a cached dump must equal a fresh migrate in schema
             and in rows per table, or the run fails.

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

# What decides the migrated database, per consumer. Tracked files only: the
# key must not move with a virtualenv or a __pycache__ beside the migrations.
MIGRATIONS = ":(glob)futureagi/**/migrations/**"
INPUTS = {
    # pytest's test database (tfc.settings.test, docker-compose.test.yml).
    "pytest": [
        MIGRATIONS,
        "futureagi/uv.lock",
        "futureagi/tfc/settings",
        "futureagi/conftest.py",
        "futureagi/pytest.ini",
        "futureagi/docker-compose.test.yml",
        "futureagi/.ci/db_dump.py",
    ],
    # The distributed E2E stack's database after `manage.py migrate` in the
    # built backend image.
    "e2e-distributed": [
        MIGRATIONS,
        "futureagi/uv.lock",
        "futureagi/tfc/settings",
        "futureagi/Dockerfile.oss",
        "futureagi/entrypoint.sh",
        "docker-compose.distributed.yml",
        "e2e/stack",
        "futureagi/.ci/db_dump.py",
    ],
}
# The private package internal runs check out here. It carries migrations of
# its own, so a checkout with it and one without it are different databases.
OVERLAY = "futureagi/ee/cloud"

COMPLETE = "-- PostgreSQL database dump complete"


class DumpError(Exception):
    pass


def _git(cwd, *args):
    return subprocess.run(
        ["git", "-C", str(cwd), *args], check=True, stdout=subprocess.PIPE, timeout=120
    ).stdout


def cache_key(flavour, root=ROOT):
    """The cache key for `flavour` in the checkout at `root`."""
    if flavour not in INPUTS:
        raise DumpError(f"unknown flavour {flavour!r}; one of {sorted(INPUTS)}")
    digest = hashlib.sha256(f"{VERSION}\n{flavour}\n".encode())
    for spec in INPUTS[flavour]:
        # Mode, blob id and path of every tracked file under the pathspec.
        listed = _git(root, "ls-files", "-s", "-z", "--", spec)
        if not listed:
            raise DumpError(f"key input {spec!r} matches no tracked file")
        digest.update(spec.encode() + b"\n" + listed)
    overlay = Path(root) / OVERLAY
    if flavour == "pytest" and (overlay / ".git").exists():
        digest.update(
            b"overlay\n"
            + _git(overlay, "ls-files", "-s", "-z", "--", ":(glob)**/migrations/**")
        )
    else:
        digest.update(b"no overlay\n")
    return f"migrated-db-{flavour}-v{VERSION}-{digest.hexdigest()[:40]}"


def read_dump(path):
    """The SQL of a gzipped dump. Refuses one that pg_dump did not finish."""
    try:
        with gzip.open(
            path, "rt", encoding="utf-8", errors="surrogateescape"
        ) as handle:
            text = handle.read()
    except (OSError, EOFError) as exc:
        raise DumpError(f"{path}: not a readable gzip dump ({exc})") from exc
    if COMPLETE not in text.splitlines()[-12:]:
        raise DumpError(f"{path}: the dump is incomplete (no {COMPLETE!r} at its end)")
    return text


def normalise(text):
    """Schema statements as written, each table's rows as a count.

    Row values are left out on purpose: a migration may stamp the time or a
    random id, and two correct migrates then differ. pg_dump's comments and
    its per-dump \\restrict token are left out for the same reason.
    """
    out = []
    rows = None
    for line in text.splitlines():
        if rows is not None:
            if line == "\\.":
                out.append(f"<{rows} rows>")
                rows = None
            else:
                rows += 1
            continue
        if not line or line.startswith(("--", "\\restrict", "\\unrestrict")):
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
    """pg_dump `database` through `prefix` (for example `docker compose exec -T db`)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".partial")
    command = [
        *prefix,
        "pg_dump",
        "-U",
        user,
        "--no-owner",
        "--no-privileges",
        database,
    ]
    with (
        gzip.open(partial, "wb") as out,
        subprocess.Popen(command, stdout=subprocess.PIPE) as proc,
    ):
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
        prefix, user, database, "-At", "-c", "SELECT count(*) FROM django_migrations"
    )
    if applied.returncode != 0 or int(applied.stdout.strip() or 0) == 0:
        raise DumpError("the restored database records no applied migration")
    return int(applied.stdout.strip())


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("key").add_argument("flavour", choices=sorted(INPUTS))
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
            print(cache_key(args.flavour))
        elif args.command == "dump":
            dump(args.path, args.user, args.database, args.prefix)
            print(f"dumped {args.database} to {args.path}")
        elif args.command == "restore":
            applied = restore(args.path, args.user, args.database, args.prefix)
            print(
                f"restored {args.database} from {args.path}: {applied} applied migrations"
            )
        else:
            difference = compare(args.cached, args.fresh)
            if difference:
                print("\n".join(difference[:200]))
                print(
                    "::error title=Cached database dump differs from a fresh migrate::"
                    "Something outside the key's inputs changed the migrated database. "
                    "Raise VERSION in futureagi/.ci/db_dump.py in this pull request so no job restores the old dump."
                )
                return 1
            print(
                "the cached dump equals a fresh migrate: same schema, same rows per table"
            )
    except (DumpError, subprocess.CalledProcessError) as exc:
        print(f"::error title=Database dump::{exc}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
