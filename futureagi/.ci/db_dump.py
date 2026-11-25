#!/usr/bin/env python3
"""A cached dump of pytest's freshly migrated test database, for Backend CI.

Only schema and the migration ledger are transferable. The fresh producer also
checks the data a restore + pytest-django setup actually reconstruct, in a
second disposable database. Non-equivalent recipes migrate locally; test order
or eventual transactional truncation is not evidence of seed parity.
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
# reports that a cached dump no longer equals a fresh migrate.
VERSION = 2
# Reserved for a completed comparison of two valid, different dumps. Ordinary
# tool failures (including an unexpected Python exception) must not mean drift.
COMPARE_DIFFERENT = 3
ROOT = Path(__file__).resolve().parents[2]
# A migration can import any helper or read a fixture outside migrations/.
# Hash the candidate tree (not the commit, branch name or untracked .venv).
INPUTS = ["."]
OVERLAY = "futureagi/ee/cloud"
LEDGER = "COPY public.django_migrations (id, app, name, applied) FROM stdin;"
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


def cache_key(root, runtime):
    """Exact tree, overlay revision (including worktree checkouts), and tools."""
    if not runtime.strip():
        raise DumpError("missing runtime fingerprint")
    digest = hashlib.sha256(f"{VERSION}\0{runtime}\0".encode())
    digest.update(_git(root, "ls-tree", "-z", "HEAD", "--", *INPUTS))
    overlay = Path(root) / OVERLAY
    if overlay.exists():
        if not (overlay / ".git").exists():
            raise DumpError("overlay is present without checkout metadata")
        digest.update(b"overlay\0" + _git(overlay, "rev-parse", "--verify", "HEAD"))
    else:
        digest.update(b"public-only")
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


def normalise(text, *, identities=None):
    """Compare exact migration identities, not their wall-clock timestamps."""
    out = []
    rows = None
    seen = False
    for line in text.split("\n"):
        if rows is not None:
            if line == "\\.":
                if not rows or len(set(rows)) != len(rows):
                    raise DumpError("missing or duplicate migration identity")
                if identities is not None:
                    identities.extend(sorted(rows))
                out.extend(sorted(rows))
                out.append(line)
                rows = None
            else:
                fields = line.split("\t")
                if len(fields) != 4 or not all(fields):
                    raise DumpError("invalid migration ledger row")
                rows.append("\t".join(fields[1:3]))
            continue
        if line.startswith(("\\restrict ", "\\unrestrict ")):
            continue
        out.append(line)
        if line.startswith("COPY ") and line.endswith(" FROM stdin;"):
            if line != LEDGER or seen:
                raise DumpError("unexpected data in schema and migration ledger dump")
            rows = []
            seen = True
    if rows is not None or not seen:
        raise DumpError("missing or unfinished migration ledger")
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
    """Private comparison only. No seed value, per-row hash or data dump is saved.

    Compare every other table (including unknown tables) and all sequence state.
    Physical COPY order and random psql restriction tokens are not data. Even a
    volatile value makes this recipe cold-only unless proven reconstructible.
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
        elif args.command == "seed-digest":
            _say(seed_digest(args.user, args.database, args.prefix))
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
                # SQL defaults or functions may contain non-public configuration.
                _say(
                    "::error title=Cached database dump differs from a fresh migrate::"
                    "Something outside the key's inputs changed the migrated schema. "
                    "Raise VERSION in futureagi/.ci/db_dump.py in this pull request so no job restores the old dump."
                )
                return COMPARE_DIFFERENT
            _say(
                "the cached dump equals a fresh migrate: same schema, same applied migrations"
            )
    except (DumpError, subprocess.CalledProcessError) as exc:
        _say(f"::error title=Database dump::{exc}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
