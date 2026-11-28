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

The dump is the database after `manage.py migrate` alone has applied every
migration and before it sends post_migrate (.ci/e2e_db_migrate.py): no
receiver's rows, no cache table, no seed command. So the key needs what the
migrations read and nothing the receivers read. It is a public artifact.
"""

import argparse
import hashlib
import importlib.util
import json
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
# The image the migrate runs in, this file, and the scripts that run the
# migrate.
IMAGE_FILES = frozenset(
    {
        "Dockerfile.oss",
        ".ci/e2e_db.py",
        ".ci/e2e-db-control.sh",
        ".ci/e2e_db_migrate.py",
    }
)
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
# The repository variable that turns restoring on, per stack: a comma list of
# stack names. Unset, empty or naming no stack: every leg migrates from zero.
SWITCH = "E2E_DB_REUSE"
# A run restores only with a leg that still boots from zero (shard 2) and one
# that compares the dump with a fresh migrate (shard 3).
ZERO_SHARD = 2
CONTROL_SHARD = 3
SAFE_NAME = re.compile(r"[A-Za-z0-9_-]{1,40}")
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


CONNECT = re.compile(r"\bpost_migrate\s*\.\s*connect\(\s*([\w.]+)")
DECORATED = re.compile(
    r"@receiver\(\s*(?:[\w.]+\.)?post_migrate\b[^)]*\)\s*\n\s*def\s+(\w+)"
)


def post_migrate_receivers(root):
    """(file, receiver) for each post_migrate receiver this repository connects.

    The dump stops before post_migrate, so a restoring leg gets these rows
    only from its own boot's migrate, which applies nothing. A receiver must
    do its work then too. Test files are not searched.
    """
    root = Path(root)
    found = set()
    for folder, dirs, files in os.walk(root):
        dirs[:] = sorted(
            d
            for d in dirs
            if not d.startswith(".") and d not in SKIP_DIRS and d != "tests"
        )
        for name in files:
            if not name.endswith(".py") or name.startswith("test_"):
                continue
            path = Path(folder) / name
            text = path.read_text(encoding="utf-8", errors="replace")
            for match in (*CONNECT.finditer(text), *DECORATED.finditer(text)):
                found.add((path.relative_to(root).as_posix(), match.group(1)))
    return sorted(found)


def read_switch(value):
    """(the stacks the switch names, the other names in it), each once, in order."""
    named, unknown = [], []
    for name in (value or "").split(","):
        name = name.strip()
        if not name:
            continue
        target = named if name in STACKS else unknown
        if name not in target:
            target.append(name)
    return named, unknown


def _shown(name):
    return repr(name) if SAFE_NAME.fullmatch(name) else "a name with other characters"


def plan(switch, totals, db):
    """What each stack's legs do with the saved dump in this run.

    `totals` gives each stack's leg count, `db` each stack's key and whether
    a saved dump was found. Returns (lines to print, {stack: {key, found,
    restore}}). A stack restores only when the switch names it, a dump was
    found and it has a zero leg and a control leg.
    """
    named, unknown = read_switch(switch)
    if not named and not unknown:
        lines = [f"{SWITCH} is not set or names nothing: every leg migrates from zero."]
    elif SAFE_NAME.fullmatch(switch.replace(",", "").replace(" ", "")):
        lines = [f"{SWITCH} is {switch.strip()!r}."]
    else:
        lines = [f"{SWITCH} is set, with characters a stack name never has."]
    for name in unknown:
        lines.append(
            f"::warning title=E2E database reuse::{SWITCH} names {_shown(name)}, "
            f"which is no E2E stack ({', '.join(STACKS)}). It is ignored."
        )
    result = {}
    for stack, total in totals.items():
        entry = db.get(stack) or {}
        found = entry.get("found") is True
        if stack not in named:
            restore, why = False, f"migrates from zero: {SWITCH} does not name it"
        elif not found:
            restore, why = False, "migrates from zero: no saved dump for this commit"
        elif total < CONTROL_SHARD:
            restore, why = (
                False,
                f"migrates from zero: {total} legs, and restoring needs a zero leg "
                f"and a control leg (shards {ZERO_SHARD} and {CONTROL_SHARD})",
            )
        else:
            restore, why = (
                True,
                f"restores the saved dump on every leg but shard {ZERO_SHARD} "
                f"(boots from zero); shard {CONTROL_SHARD} compares it with a fresh migrate",
            )
        lines.append(f"{stack}: {why}.")
        result[stack] = {
            "key": entry.get("key", ""),
            "found": found,
            "restore": restore,
        }
    return lines, result


RESULT_DIR = re.compile(
    r"db-reuse-(?P<stack>[a-z]+)-(?P<shard>\d+)-attempt-(?P<attempt>\d+)"
)
RESULT_FILE = "e2e-db-result"


def summary(directory, stack, total):
    """A table of what each leg of a restoring run did with the saved dump.

    `directory` holds one folder per leg and attempt, as download-artifact
    writes them (db-reuse-<stack>-<shard>-attempt-<n>/e2e-db-result). The
    last attempt of each shard counts. The file's first line is the restore
    step's result; a later `suite: <job status>` line, the leg's outcome.
    """
    last = {}
    for folder in sorted(Path(directory).iterdir()) if Path(directory).is_dir() else []:
        match = RESULT_DIR.fullmatch(folder.name)
        if not match or match["stack"] != stack:
            continue
        shard, attempt = int(match["shard"]), int(match["attempt"])
        if attempt >= last.get(shard, (0, ""))[0]:
            text = (
                (folder / RESULT_FILE).read_text()
                if (folder / RESULT_FILE).is_file()
                else ""
            )
            lines_ = text.strip().splitlines()
            outcome = next(
                (x.split(":", 1)[1].strip() for x in lines_ if x.startswith("suite:")),
                "",
            )
            last[shard] = (attempt, lines_[0] if lines_ else "", outcome)
    counts = {"restored": [], "fell back": [], "miss": [], "zero": [], "no result": []}
    reasons = []
    failed = []
    for shard in range(1, total + 1):
        _attempt, result, outcome = last.get(shard, (0, "", ""))
        kind = next(
            (
                k
                for k in ("restored", "fell back", "miss", "zero")
                if result.startswith(k)
            ),
            "no result",
        )
        counts[kind].append(shard)
        if kind in ("fell back", "miss"):
            reasons.append(f"- shard {shard}: {result}")
        if outcome == "failure":
            failed.append((shard, kind))
    lines = [
        f"### Saved database, {stack}",
        "",
        "| Legs | Count | Shards |",
        "|---|---|---|",
    ]
    for kind, shards in counts.items():
        if shards or kind != "no result":
            lines.append(
                f"| {kind} | {len(shards)} | {', '.join(map(str, shards)) or '-'} |"
            )
    lines += [""] + reasons if reasons else []
    if failed:
        lines += ["", "Failed legs:"]
        lines += [f"- shard {shard}: {ROLES[kind](shard)}" for shard, kind in failed]
    if any(kind == "restored" for _shard, kind in failed):
        lines += ["", RESTORED_FAILED]
    return "\n".join(lines)


ROLES = {
    "restored": lambda shard: (
        "restored the saved dump"
        + (", and compared it with a fresh migrate" if shard == CONTROL_SHARD else "")
    ),
    "zero": lambda shard: "migrated from zero, by design",
    "fell back": lambda shard: "migrated from zero after its restore fell back",
    "miss": lambda shard: "migrated from zero: the dump was not for its Postgres",
    "no result": lambda shard: (
        "no database result (it stopped before or during the restore)"
    ),
}
# Not "likely": most legs restore, so an ordinary failure looks the same.
RESTORED_FAILED = (
    "A leg that restored the saved dump failed. A restored database that lacks "
    "something the stack needs is possible: the dump holds only Postgres, and "
    "the boot's migrate applies nothing on it. To tell: take this stack out of "
    "the E2E_DB_REUSE repository variable (or clear it), then re-run all jobs "
    "of this run. Every leg then migrates from zero. If the same tests pass, "
    "the saved database is the cause; if they fail the same way, it is not."
)


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
    sub = commands.add_parser("summary")
    sub.add_argument("directory")
    sub.add_argument("stack")
    sub.add_argument("total", type=int)
    sub = commands.add_parser("named")
    sub.add_argument("switch")
    sub = commands.add_parser("plan")
    sub.add_argument("switch")
    sub.add_argument("totals", help="JSON: {stack: leg count}")
    sub.add_argument("db", help="JSON: {stack: {key, found}}")
    sub.add_argument("out", help="file for the JSON result")
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
        elif args.command == "summary":
            _say(summary(args.directory, args.stack, args.total))
        elif args.command == "named":
            _say(" ".join(read_switch(args.switch)[0]))
        elif args.command == "plan":
            lines, result = plan(
                args.switch, json.loads(args.totals), json.loads(args.db)
            )
            for line in lines:
                _say(line)
            Path(args.out).write_text(json.dumps(result, separators=(",", ":")))
        else:
            write_record(args.out, args.dump, args.preseed, args.recipe)
    except (
        db_dump.DumpError,
        OSError,
        subprocess.CalledProcessError,
        ValueError,
        AttributeError,
    ) as exc:
        _say(f"::error title=E2E database dump::{exc}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
