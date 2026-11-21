#!/usr/bin/env python3
"""Fails unless every shard of a Backend CI run collected the same test ids.

    check_collected_ids.py EXPECTED_SHARDS RECORD [RECORD ...]

The records come from collected_ids.py, which says why they must be equal.
Standard library only.
"""

import collections
import sys

SHOWN = 40


def differing(records):
    """The lines that some records hold more often than others, with the number of records that hold each."""
    counts = [collections.Counter(lines) for lines in records]
    odd = (
        (line, sum(1 for count in counts if count[line]))
        for line in set().union(*counts)
        if len({count[line] for count in counts}) > 1
    )
    return sorted(odd, key=lambda found: found[0].partition(" ")[2])


def main(argv):
    if len(argv) < 3 or not argv[1].isdigit():
        sys.stderr.write(__doc__ or "")
        return 2
    expected, paths = int(argv[1]), argv[2:]
    if len(paths) != expected or expected < 2:
        sys.stdout.write(
            f"::error title=Collection records::found {len(paths)} records for {expected} shards"
            " (a check of fewer than two proves nothing)\n"
        )
        return 1
    records = []
    for path in paths:
        with open(path, encoding="utf-8", errors="surrogateescape") as handle:
            records.append(handle.read().splitlines())
    odd = differing(records)
    if not odd and records[0]:
        sys.stdout.write(
            f"{expected} shards collected the same {len(records[0])} tests\n"
        )
        return 0
    for line, count in odd[:SHOWN]:
        sys.stdout.write(f"in {count} of {expected} shards: {line.partition(' ')[2]}\n")
    sys.stdout.write(
        f"::error title=Shards collected different tests::{len(odd)} test ids are not the same in every shard"
        " (or no test was collected), so pytest-split can run a test twice or never."
        " The usual cause is a random value in a parametrize list, such as uuid4():"
        " use a fixed value or pass ids=.\n"
    )
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
