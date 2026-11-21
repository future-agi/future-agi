#!/usr/bin/env python3
"""Merge the pytest shards' timing files into one .test_durations.

Each shard of a dev push runs with --store-durations: it starts from the run's
pinned plan and rewrites the time of every test it ran. So a shard's file is
the plan plus that shard's measurements. This takes each test's measured time
from the shard that ran it and keeps the plan's time for a test no shard
reported (its shard failed before the upload, or the test was deselected).

    merge_test_durations.py PLAN SHARD_FILE [SHARD_FILE ...] > .test_durations

The result holds only tests of MIN_SECONDS or more. That is about a fifth of
the tests and 99% of the time. It splits nearly as evenly as the full list,
not exactly: pytest-split gives a test without a time the average of its
suite's known times, so the many near-instant tests carry weight they do not
have, and the shard that holds a very long test gets a few more short tests
than it should. Replaying one dev run's times through pytest-split's
algorithm, the longest shard was equal for the main suite and 0.6 minutes
longer for the Metrics suite (25.5 against 24.9). Times are rounded to a tenth
of a millisecond, and a test whose id is longer than MAX_ID_LENGTH is left out
(a few generated parameter ids are hundreds of kilobytes long).

Standard library only.
"""

import json
import sys

MIN_SECONDS = 0.05
MAX_ID_LENGTH = 300


def merge(plan, shards):
    """The plan with every measured time applied, and how many tests were measured."""
    merged = dict(plan)
    measured = set()
    for shard in shards:
        for test, seconds in shard.items():
            if plan.get(test) == seconds:
                continue  # untouched by this shard
            # Two shards never run the same test; if two files disagree with
            # the plan, keep the longer time so the test is not under-weighted.
            merged[test] = max(seconds, merged[test]) if test in measured else seconds
            measured.add(test)
    compact = {
        test: round(seconds, 4)
        for test, seconds in merged.items()
        if seconds >= MIN_SECONDS and len(test) <= MAX_ID_LENGTH
    }
    return compact, len(measured)


def main(argv):
    if len(argv) < 3:
        sys.stderr.write(__doc__)
        return 2
    documents = []
    for path in argv[1:]:
        with open(path, encoding="utf-8") as handle:
            document = json.load(handle)
        if not isinstance(document, dict) or not all(
            isinstance(value, (int, float)) and not isinstance(value, bool)
            for value in document.values()
        ):
            sys.stderr.write(f"{path}: not a test-to-seconds object\n")
            return 1
        documents.append(document)
    merged, measured = merge(documents[0], documents[1:])
    if not measured:
        sys.stderr.write(
            "no shard reported a measured time; refusing to write the plan back as new timings\n"
        )
        return 1
    # pytest-split's own layout: sorted keys, four-space indent.
    json.dump(merged, sys.stdout, indent=4, sort_keys=True)
    sys.stdout.write("\n")
    sys.stderr.write(
        f"{measured} tests measured in {len(documents) - 1} shard files; kept {len(merged)} of "
        f"{MIN_SECONDS}s or more, {sum(merged.values()) / 60:.1f} test-minutes\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
