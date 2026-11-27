"""TEMPORARY diagnostic for the DB reuse PR. Removed before review.

Prints table names, counts and column names only, and only for public dumps.
For the dump made with the private overlay it prints counts, never names.
"""

import difflib
import os
import re
import sys

sys.path.insert(0, "futureagi/.ci")
import db_dump  # noqa: E402

d = sys.argv[1]


def blocks(path):
    """{table: (columns, [rows])} and the non-data lines."""
    text = db_dump.read_dump(path)
    data, other = {}, []
    table = None
    for line in text.split("\n"):
        if table is not None:
            if line == "\\.":
                table = None
            else:
                data[table][1].append(line)
            continue
        if line.startswith(("\\restrict ", "\\unrestrict ")):
            continue
        if line.startswith("COPY ") and line.endswith(" FROM stdin;"):
            name = line.split(" ", 2)[1]
            cols = line.split("(", 1)[1].rsplit(")", 1)[0].split(", ")
            table = name
            data[name] = (cols, [])
            continue
        other.append(line)
    return data, other


def say(*a):
    print(*a, flush=True)


pub1, pub1_other = blocks(f"{d}/pub1.sql.gz")
pub2, pub2_other = blocks(f"{d}/pub2.sql.gz")
internal, int_other = blocks(f"{d}/int.sql.gz")

say("== sizes (bytes gz):", {n: os.path.getsize(f"{d}/{n}.sql.gz") for n in ("pub1", "pub2", "int", "rt")})
say("== public tables with rows:")
for name, (cols, rows) in sorted(pub1.items()):
    if rows:
        say(f"   {len(rows):6d} {name}")
say("== public schema lines:", len(pub1_other), "tables:", len(pub1))


def column_diffs(a, b, mask):
    """Tables and columns whose values differ between two public dumps."""
    out = {}
    for name in sorted(set(a) | set(b)):
        ra = a.get(name, ([], []))[1]
        rb = b.get(name, ([], []))[1]
        fa = sorted(db_dump._masked(r) if mask else r for r in ra)
        fb = sorted(db_dump._masked(r) if mask else r for r in rb)
        if fa == fb:
            continue
        cols = (a.get(name) or b.get(name))[0]
        changed = set()
        if len(ra) == len(rb):
            # Column-wise multiset compare.
            for i, col in enumerate(cols):
                va = sorted(r.split("\t")[i] for r in ra)
                vb = sorted(r.split("\t")[i] for r in rb)
                if mask:
                    va = sorted(db_dump._masked(v) for v in va)
                    vb = sorted(db_dump._masked(v) for v in vb)
                if va != vb:
                    changed.add(col)
        else:
            changed.add(f"rowcount {len(ra)} vs {len(rb)}")
        out[name] = sorted(changed)
    return out


say("== pub1 vs pub2 schema lines equal:", pub1_other == pub2_other)
say("== pub1 vs pub2 unmasked differing tables/columns:")
for k, v in column_diffs(pub1, pub2, False).items():
    say("   ", k, v)
say("== pub1 vs pub2 MASKED differing tables/columns:")
for k, v in column_diffs(pub1, pub2, True).items():
    say("   ", k, v)
say("== compare(pub1, pub2) masked diff lines:", len(db_dump.compare(f"{d}/pub1.sql.gz", f"{d}/pub2.sql.gz")))

# Internal: counts only.
say("== internal tables:", len(internal), "public tables:", len(pub1))
say("== tables only in internal (count):", len(set(internal) - set(pub1)))
say("== tables only in public (count):", len(set(pub1) - set(internal)))
schema_diff = [l for l in difflib.unified_diff(pub1_other, int_other, lineterm="", n=0) if l[:1] in "+-" and l[:3] not in ("+++", "---")]
say("== schema diff lines public vs internal:", len(schema_diff))
masked = column_diffs(pub1, internal, True)
say("== public vs internal MASKED differing tables (count):", len(masked))
say("== public vs internal: differing tables that exist in public (names):")
for k, v in masked.items():
    if k in pub1 and k in internal:
        say("   ", k, v)
say("== compare(pub1, int) masked diff lines:", len(db_dump.compare(f"{d}/pub1.sql.gz", f"{d}/int.sql.gz")))
say("== compare(rt, int) masked diff lines:", len(db_dump.compare(f"{d}/rt.sql.gz", f"{d}/int.sql.gz")))
rt, rt_other = blocks(f"{d}/rt.sql.gz")
say("== rt vs pub1 exact rows equal:", {k: v[1] for k, v in rt.items()} == {k: v[1] for k, v in pub1.items()} or "differ (order?)",
    "sorted equal:", {k: sorted(v[1]) for k, v in rt.items()} == {k: sorted(v[1]) for k, v in pub1.items()},
    "schema equal:", rt_other == pub1_other)

# Secret-shaped and env-derived values in the public dump.
shapes = {
    "openai/anthropic-like": re.compile(r"sk-[A-Za-z0-9_-]{16,}"),
    "github token": re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    "aws key": re.compile(r"AKIA[0-9A-Z]{16}"),
    "private key": re.compile(r"-----BEGIN"),
    "jwt": re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
    "long hex 40+": re.compile(r"\b[0-9a-f]{40,}\b"),
    "long base64 40+": re.compile(r"[A-Za-z0-9+/]{40,}={0,2}"),
    "email": re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[a-z]{2,}"),
}
say("== secret-shaped values in public rows (table, column, shape, count):")
for name, (cols, rows) in sorted(pub1.items()):
    for i, col in enumerate(cols):
        for shape, pattern in shapes.items():
            n = sum(1 for r in rows if pattern.search(r.split("\t")[i]))
            if n:
                say(f"    {name}.{col} {shape} x{n}")
text = "\n".join(r for _, rows in pub1.values() for r in rows)
hits = sorted(
    k for k, v in os.environ.items() if len(v) >= 6 and v not in ("true", "false", "production", "PRODUCTION") and v in text
)
say("== env var NAMES whose value appears in public rows:", hits)
schema_text = "\n".join(pub1_other)
hits = sorted(
    k for k, v in os.environ.items() if len(v) >= 6 and v not in ("true", "false", "production", "PRODUCTION") and v in schema_text
)
say("== env var NAMES whose value appears in public schema:", hits)
