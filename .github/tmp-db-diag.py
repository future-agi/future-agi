"""TEMPORARY diagnostic for the DB reuse PR. Removed before review.

Every dump here is migrated without the private overlay (public only).
"""

import difflib
import subprocess
import sys

sys.path.insert(0, "futureagi/.ci")
import db_dump  # noqa: E402

d = sys.argv[1]


def lines(name):
    return [
        l
        for l in db_dump.read_dump(f"{d}/{name}.sql.gz").split("\n")
        if not l.startswith(("\\restrict ", "\\unrestrict "))
    ]


def show(a, b, limit=120):
    diff = [
        l
        for l in difflib.unified_diff(lines(a), lines(b), a, b, lineterm="", n=0)
    ]
    print(f"== raw diff {a} -> {b}: {len(diff)} lines", flush=True)
    for l in diff[:limit]:
        print("   ", l[:220], flush=True)
    print(f"== masked compare {a} vs {b}:", len(db_dump.compare(f"{d}/{a}.sql.gz", f"{d}/{b}.sql.gz")), flush=True)


show("pub1", "rt0")
show("rt0", "rt")
show("pub1", "rt")
for name in ("pub1", "rt0", "rt"):
    print(name, open(f"{d}/{name}.digest").read().strip())
