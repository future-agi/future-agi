#!/usr/bin/env python3
"""Merge the pytest shards' timing files into one .test_durations.

Each shard of a dev push runs with --store-durations: it starts from the run's
pinned plan and rewrites the time of every test it ran. So a shard's file is
the plan plus that shard's measurements. This takes each test's measured time
from the shard that ran it and keeps the plan's time for a test no shard
reported (its shard failed before the upload, or the test was deselected).

    merge_test_durations.py PLAN SHARD_FILE [SHARD_FILE ...] > .test_durations
    merge_test_durations.py --compact [--whole PART] PLAN SHARD_FILE [...]

Without options the result holds every measured test but the few with an id
longer than MAX_ID_LENGTH (below). That is what the timings job saves for the
next runs' plan, and it is the closest input: pytest-split gives a
test WITHOUT a time the average of its suite's known times. In a suite with a
few very long tests that average is many seconds, thousands of near-instant
tests then carry weight they do not have, and the shard that holds a very
long test also gets a full share of the short ones. Replaying one dev run
through pytest-split's algorithm, the longest Metrics shard was 23.9 minutes
with only the tests of MIN_SECONDS or more, and 19.9 with every test.

--compact writes the smaller file that is committed as the fallback for a run
that finds no saved timings (about 2.6 MB, not 10): the tests of MIN_SECONDS
or more, and every test measured by a shard file whose path contains a
--whole PART. The committed file uses `--whole timing-data-metrics-`, so the
Metrics suite, the one with the very long tests, is exact there too. For the
main suite both forms gave the same split in that replay.

Times are rounded to a tenth of a millisecond, and a test whose id is longer
than MAX_ID_LENGTH is left out in both forms: a few generated parameter ids
are hundreds of kilobytes long, and one of them would be most of the file.
pytest-split plans each of them with its suite's average time, not its own.

Standard library only.
"""

import argparse
import json
import sys

MIN_SECONDS = 0.05
MAX_ID_LENGTH = 300
# A long test's weight decides how much else pytest-split puts in its group.
# Several metrics tests take one of two paths from run to run (one of them
# 329-517 s or 1093-1099 s), so keeping only the last sample let one fast run
# fill the group of a test that is slow half the time, and the shard ran past
# its timeout. A test that weighed LONG_SECONDS or more now loses at most a
# tenth of its weight per run; a test that got faster for good converges in
# about ten dev runs. A slower sample always applies in full.
LONG_SECONDS = 60.0
DECAY = 0.9


def merge(plan, shards):
    """The plan with every measured time applied, and for each measured test the shard that measured it."""
    merged = dict(plan)
    measured_by = {}
    for index, shard in enumerate(shards):
        for test, seconds in shard.items():
            if plan.get(test) == seconds:
                continue  # untouched by this shard
            previous = plan.get(test)
            if previous is not None and previous >= LONG_SECONDS:
                seconds = max(seconds, DECAY * previous)
            # Two shards never run the same test; if two files disagree with
            # the plan, keep the longer time so the test is not under-weighted.
            if test in measured_by and merged[test] >= seconds:
                continue
            merged[test] = seconds
            measured_by[test] = index
    return merged, measured_by


def tidy(times):
    return {
        test: round(seconds, 4)
        for test, seconds in times.items()
        if len(test) <= MAX_ID_LENGTH
    }


def compact(times, whole=()):
    """The tests of MIN_SECONDS or more, and every test of `whole`."""
    return {
        test: seconds
        for test, seconds in times.items()
        if seconds >= MIN_SECONDS or test in whole
    }


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=(__doc__ or "").splitlines()[0],
        usage="%(prog)s [--compact [--whole PART]] PLAN SHARD_FILE [SHARD_FILE ...]",
    )
    parser.add_argument("--compact", action="store_true")
    parser.add_argument("--whole", action="append", default=[], metavar="PART")
    parser.add_argument("plan")
    parser.add_argument("shards", nargs="+")
    args = parser.parse_args(argv)
    if args.whole and not args.compact:
        parser.error("--whole only has a meaning with --compact")
    documents = []
    for path in (args.plan, *args.shards):
        with open(path, encoding="utf-8") as handle:
            document = json.load(handle)
        if not isinstance(document, dict) or not all(
            isinstance(value, (int, float)) and not isinstance(value, bool)
            for value in document.values()
        ):
            sys.stderr.write(f"{path}: not a test-to-seconds object\n")
            return 1
        documents.append(document)
    merged, measured_by = merge(documents[0], documents[1:])
    if not measured_by:
        sys.stderr.write(
            "no shard reported a measured time; refusing to write the plan back as new timings\n"
        )
        return 1
    result = tidy(merged)
    if args.compact:
        whole = {
            test
            for test, index in measured_by.items()
            if any(part in args.shards[index] for part in args.whole)
        }
        result = compact(result, whole)
    # pytest-split's own layout: sorted keys, four-space indent.
    json.dump(result, sys.stdout, indent=4, sort_keys=True)
    sys.stdout.write("\n")
    sys.stderr.write(
        f"{len(measured_by)} tests measured in {len(args.shards)} shard files; kept {len(result)} of "
        f"{len(merged)}, {sum(result.values()) / 60:.1f} test-minutes\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
