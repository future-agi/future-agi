#!/usr/bin/env python3
"""Fails unless the shards of a Backend CI run divided one and the same collection.

    check_collected_ids.py EXPECTED_SHARDS RECORD [RECORD ...]

Each RECORD is <dir>/collected-ids.txt from collected_ids.py, with the shard's
selection in <dir>/selected-ids.txt beside it. <dir> is the artifact name,
collected-ids-<suite>-<group>. Two checks:

- every shard collected the same test ids in the same order (collected_ids.py
  says why that must hold);
- no test is selected by two shards of one suite, and every selected test is
  one the shard collected.

That shows the plan each shard computed and that no shard's share overlaps
another's. It does not show that a selected test ran: the JUnit report of the
shard does. Standard library only.
"""

import collections
import hashlib
import os
import re
import sys

SHOWN = 40
SELECTED = "selected-ids.txt"
SHARD = re.compile(r"collected-ids-(.+)-(\d+)")


def read(path):
    with open(path, encoding="utf-8", errors="surrogateescape") as handle:
        return [line for line in handle.read().splitlines() if line]


def digest(path):
    """A digest of the record's lines, which reads the file in pieces."""
    found = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            found.update(block)
    return found.hexdigest()


def differing(records):
    """The lines that some records hold more often than others, with the number of records that hold each."""
    counts = [collections.Counter(lines) for lines in records]
    odd = (
        (line, sum(1 for count in counts if count[line]))
        for line in set().union(*counts)
        if len({count[line] for count in counts}) > 1
    )
    return sorted(odd, key=lambda found: found[0].partition(" ")[2])


def explain(paths, expected, out):
    """Says how the records differ; False when they hold the same lines after all.

    Loads every record, so it runs only after a digest mismatch.
    """
    records = [read(path) for path in paths]
    if all(record == records[0] for record in records):
        return False
    odd = differing(records)
    for line, count in odd[:SHOWN]:
        out.write(f"in {count} of {expected} shards: {line.partition(' ')[2]}\n")
    if odd:
        problem = (
            f"{len(odd)} test ids are not the same in every shard."
            " The usual cause is a random value in a parametrize list, such as uuid4():"
            " use a fixed value or pass ids=."
        )
    else:
        other = next(record for record in records if record != records[0])
        at = next(
            i for i, (a, b) in enumerate(zip(records[0], other, strict=True)) if a != b
        )
        out.write(
            f"position {at + 1}: {records[0][at].partition(' ')[2]}\n"
            f"         against: {other[at].partition(' ')[2]}\n"
        )
        problem = "the shards collected the same tests in a different order."
    out.write(
        f"::error title=Shards collected different tests::{problem}"
        " pytest-split can then run a test twice or never."
        " After the fix, re-run all jobs: a re-run of the failed jobs reads the same records.\n"
    )
    return True


def selections(paths, collected, out):
    """Checks the shards' selections. Returns True when they are sound."""
    suites = collections.defaultdict(dict)
    for path in paths:
        directory = os.path.dirname(os.path.abspath(path))
        name = SHARD.fullmatch(os.path.basename(directory))
        selected = os.path.join(directory, SELECTED)
        if not name or not os.path.isfile(selected):
            out.write(
                f"::error title=Selection records::{path}: no {SELECTED} beside it, or its"
                " directory is not collected-ids-<suite>-<group>; a shard that records no"
                " selection proves nothing about it\n"
            )
            return False
        suites[name.group(1)][int(name.group(2))] = read(selected)
    # A collection may hold one id twice; it may then be selected twice.
    known = collections.Counter(collected)
    sound = True
    for suite, shards in sorted(suites.items()):
        times = collections.Counter()
        groups = collections.defaultdict(set)
        stray = []
        for group, lines in sorted(shards.items()):
            for line in lines:
                if line not in known:
                    stray.append((group, line))
                    continue
                times[line] += 1
                groups[line].add(group)
        twice = {
            line: groups[line] for line, count in times.items() if count > known[line]
        }
        total = sum(len(lines) for lines in shards.values())
        if not total:
            out.write(
                f"::error title=Selection records::the {len(shards)} shards of suite {suite}"
                " selected no test at all\n"
            )
            sound = False
        for line, held in sorted(twice.items())[:SHOWN]:
            out.write(
                f"suite {suite}, shards {sorted(held)}: {line.partition(' ')[2]}\n"
            )
        for group, line in stray[:SHOWN]:
            out.write(
                f"suite {suite}, shard {group} selected a test it did not collect: {line.partition(' ')[2]}\n"
            )
        if twice or stray:
            out.write(
                f"::error title=Shards share a test::suite {suite}: {len(twice)} tests are"
                f" selected more often than they were collected and {len(stray)} selected"
                " tests are not in the collection. No test may be in two shards' shares.\n"
            )
            sound = False
        elif total:
            out.write(
                f"suite {suite}: {len(shards)} shards selected {total} tests, each in one shard only\n"
            )
    return sound


def main(argv):
    out = sys.stdout
    if len(argv) < 3 or not argv[1].isdigit():
        sys.stderr.write(__doc__ or "")
        return 2
    expected, paths = int(argv[1]), argv[2:]
    if len(paths) != expected or expected < 2:
        out.write(
            f"::error title=Collection records::found {len(paths)} records for {expected} shards"
            " (a check of fewer than two proves nothing)\n"
        )
        return 1
    if any(os.path.getsize(path) == 0 for path in paths):
        out.write(
            "::error title=Collection records::a shard collected no test at all;"
            " a run that collects nothing proves nothing\n"
        )
        return 1
    # Equal digests are equal records: one record in memory, not all of them.
    if len({digest(path) for path in paths}) != 1 and explain(paths, expected, out):
        return 1
    collected = read(paths[0])
    if not collected:
        out.write(
            "::error title=Collection records::a shard collected no test at all;"
            " a run that collects nothing proves nothing\n"
        )
        return 1
    out.write(
        f"{expected} shards collected the same {len(collected)} tests in the same order\n"
    )
    return 0 if selections(paths, collected, out) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
