"""Fail unless every required integration test PASSED in a go test -json log.

Required: the fixed list given with --require, plus every
TestClickHouseTrajectory*, TestLiveTrajectory* and TestClickHouseDeadLetter*
function defined in the *_test.go files of the given package directories, so
a new trajectory integration test is required without editing the workflow.
A skipped, failed or absent test fails the check.
Usage: check_integration_proofs.py EVENTS.jsonl --packages DIR... --require NAME...
"""

import argparse
import json
import re
import sys
from pathlib import Path

AUTO = re.compile(
    r"^func (TestClickHouseTrajectory\w*|TestLiveTrajectory\w*|TestClickHouseDeadLetter\w*)\(",
    re.M,
)


def discovered(packages):
    names = set()
    for package in packages:
        for path in sorted(Path(package).glob("*_test.go")):
            names.update(AUTO.findall(path.read_text()))
    return names


def main(argv):
    parser = argparse.ArgumentParser()
    parser.add_argument("events")
    parser.add_argument("--packages", nargs="*", default=[])
    parser.add_argument("--require", nargs="*", default=[])
    args = parser.parse_args(argv)
    required = set(args.require) | discovered(args.packages)
    passed = set()
    for line in Path(args.events).read_text().splitlines():
        if not line.strip():
            continue
        event = json.loads(line)
        if event.get("Action") == "pass" and event.get("Test"):
            passed.add(event["Test"])
    missing = sorted(required - passed)
    if missing:
        print(f"Integration proofs missing or skipped: {missing}", file=sys.stderr)
        return 1
    print(f"Confirmed {len(required)} real integration proofs")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
