#!/usr/bin/env python3
"""Minimum planning weights for known long tests, applied to the pinned timings.

pytest-split (least_duration) gives the heaviest test group 1 and fills the
other groups around it. It cannot pin or split a test: a test runs alone in
its group only while its weight is at least the sum of the suite's other
weights divided by (splits - 1). The saved timings keep each test's LAST
measured time (merge_test_durations.py), so a test that is slow in one dev run
and fast in the next loses its weight to a single fast sample, and its group
then fills with other tests as well. A floor keeps the weight up.

    duration_floors.py apply FLOORS DURATIONS
    duration_floors.py check FLOORS RECORD [RECORD ...]

`apply` runs once, in the plan job, on the timings every shard of the run then
downloads, so all shards still plan from the same bytes. It only raises: a
test's weight becomes max(time, floor). A test with no time in the file is
left out, because the timings merge would carry an added entry forever. The
file is rewritten only when a weight was raised, in the layout
merge_test_durations.py writes.

`check` runs in the collection job. It reads the shards' collection records
(collected_ids.py) and names, as a warning, each floor whose test no shard
collected, so a rename cannot silently drop a floor. It never fails the job.

FLOORS is a JSON object: test id -> seconds, a positive number. Standard
library only.
"""

import argparse
import hashlib
import json
import math
import os
import sys

TITLE = "Duration floors"


def _no_duplicates(pairs):
    document = {}
    for key, value in pairs:
        if key in document:
            raise ValueError(f"duplicate test id {key!r}")
        document[key] = value
    return document


def _no_constants(token):
    raise ValueError(f"{token} is not a number of seconds")


def load_floors(path):
    """The floors file as {test id: seconds}; ValueError when it is malformed."""
    with open(path, encoding="utf-8") as handle:
        floors = json.load(
            handle,
            object_pairs_hook=_no_duplicates,
            parse_constant=_no_constants,
        )
    if not isinstance(floors, dict):
        raise ValueError("expected an object of test id -> seconds")
    for test, seconds in floors.items():
        if not isinstance(test, str) or not test:
            raise ValueError(f"test id {test!r} is not a non-empty string")
        if (
            isinstance(seconds, bool)
            or not isinstance(seconds, (int, float))
            or not math.isfinite(seconds)
            or seconds <= 0
        ):
            raise ValueError(f"{test}: {seconds!r} is not a positive number of seconds")
    return floors


def raise_to_floors(durations, floors):
    """The durations with each floored test at max(time, floor), and what happened to each floor."""
    result = dict(durations)
    outcomes = []
    for test, floor in sorted(floors.items()):
        if test not in durations:
            outcomes.append((test, "absent", None, floor))
        elif durations[test] < floor:
            result[test] = floor
            outcomes.append((test, "raised", durations[test], floor))
        else:
            outcomes.append((test, "kept", durations[test], floor))
    return result, outcomes


def serialize(durations):
    # merge_test_durations.py's and pytest-split's layout: sorted keys,
    # four-space indent, ASCII escapes, one trailing newline.
    return json.dumps(durations, indent=4, sort_keys=True) + "\n"


def shown(test):
    """A test id as log text: an undecodable byte (a lone surrogate) as an escape."""
    return test.encode("utf-8", "backslashreplace").decode("utf-8")


def escape(message):
    """A workflow-command message, so one id stays on one annotation line."""
    return message.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def warn(message, out):
    out.write(f"::warning title={TITLE}::{escape(message)}\n")


def apply(floors_path, durations_path, out=sys.stdout):
    floors = load_floors(floors_path)
    with open(durations_path, encoding="utf-8") as handle:
        durations = json.load(handle)
    if not isinstance(durations, dict) or not all(
        isinstance(value, (int, float)) and not isinstance(value, bool)
        for value in durations.values()
    ):
        raise ValueError(f"{durations_path}: not a test-to-seconds object")
    result, outcomes = raise_to_floors(durations, floors)
    for test, outcome, seconds, floor in outcomes:
        if outcome == "absent":
            warn(
                f"{shown(test)} has no time in this run's timings; "
                f"its floor of {floor} s is not applied",
                out,
            )
        elif outcome == "raised":
            out.write(f"raised {shown(test)}: {seconds} s -> {floor} s\n")
        else:
            out.write(f"kept {shown(test)}: {seconds} s (floor {floor} s)\n")
    if any(outcome == "raised" for _, outcome, _, _ in outcomes):
        with open(durations_path, "w", encoding="utf-8") as handle:
            handle.write(serialize(result))
    with open(durations_path, "rb") as handle:
        digest = hashlib.sha256(handle.read()).hexdigest()
    out.write(f"planned {os.path.basename(durations_path)} sha256 {digest}\n")


def record_digest(test):
    # collected_ids.py's line key: the first 24 hex digits of the id's sha256.
    return hashlib.sha256(test.encode("utf-8", "surrogateescape")).hexdigest()[:24]


def check(floors_path, record_paths, out=sys.stdout):
    """Warn for each floor no shard collected. Never raises: a warning is all it can give."""
    try:
        floors = load_floors(floors_path)
        collected = set()
        read = 0
        for path in record_paths:
            if not os.path.isfile(path):
                continue
            with open(path, encoding="utf-8", errors="surrogateescape") as handle:
                collected.update(line.split(" ", 1)[0] for line in handle)
            read += 1
        if not read:
            warn("no shard collection record to check the floors against", out)
            return
        for test in sorted(floors):
            if record_digest(test) not in collected:
                warn(
                    f"{shown(test)} is not among the collected tests, "
                    "so its floor no longer applies; "
                    "update futureagi/.ci/test_duration_floors.json",
                    out,
                )
    except Exception as exc:  # noqa: BLE001 - this job gates the merge; a check that cannot run only warns
        warn(f"could not check the floors: {exc}", out)


def main(argv=None):
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    applying = commands.add_parser("apply")
    applying.add_argument("floors")
    applying.add_argument("durations")
    checking = commands.add_parser("check")
    checking.add_argument("floors")
    checking.add_argument("records", nargs="+")
    args = parser.parse_args(argv)
    if args.command == "check":
        check(args.floors, args.records)
        return 0
    try:
        apply(args.floors, args.durations)
    except (OSError, ValueError) as exc:
        sys.stderr.write(f"duration floors: {exc}\n")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
