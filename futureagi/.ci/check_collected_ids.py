#!/usr/bin/env python3
"""Fails unless every shard of a Backend CI run collected the same test ids in the same order.

    check_collected_ids.py EXPECTED_SHARDS RECORD [RECORD ...]

    check_collected_ids.py --terminal

In Actions, --terminal also verifies artifact identity, checks the latest
producer of each matrix leg and compares independent per-suite inventories
with completed pytest protocols. The file-only mode checks collection order.
"""

import collections
import concurrent.futures
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import zipfile
from pathlib import Path

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
            records.append([line for line in handle.read().splitlines() if line])
    if not all(records):
        sys.stdout.write(
            "::error title=Collection records::a shard collected no test at all;"
            " a run that collects nothing proves nothing\n"
        )
        return 1
    odd = differing(records)
    if not odd and all(record == records[0] for record in records):
        sys.stdout.write(
            f"{expected} shards collected the same {len(records[0])} tests in the same order\n"
        )
        return 0
    for line, count in odd[:SHOWN]:
        sys.stdout.write(f"in {count} of {expected} shards: {line.partition(' ')[2]}\n")
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
        sys.stdout.write(
            f"position {at + 1}: {records[0][at].partition(' ')[2]}\n"
            f"         against: {other[at].partition(' ')[2]}\n"
        )
        problem = "the shards collected the same tests in a different order."
    sys.stdout.write(
        f"::error title=Shards collected different tests::{problem}"
        " pytest-split can then run a test twice or never."
        " After the fix, re-run all jobs: a re-run of the failed jobs reads the same records.\n"
    )
    return 1


def runtime_skips(rows):
    """Read observed skips independently of whether the whole inventory qualifies."""
    evidence = []
    for key, label, phases in rows:
        skipped = [
            {"phase": phase, "reason": details[0] if details else "not recorded"}
            for phase, status, *details in phases
            if status == "runtime-skip" and len(details) <= 1
        ]
        if skipped:
            evidence.append({"id": key, "test": label, "skips": skipped})
    return evidence


def check_terminals(expected, rows):
    """Compare an independent marker-filtered inventory to completed protocols."""

    def protocols(status):
        return (
            [["setup", status], ["teardown", "passed"]],
            [["setup", "passed"], ["call", status], ["teardown", "passed"]],
        )

    allowed = {
        "pass": ([["setup", "passed"], ["call", "passed"], ["teardown", "passed"]],),
        "skip": protocols("declared-skip"),
        "xfail": protocols("xfail"),
        "xfail-or-xpass": protocols("xfail") + protocols("xpass"),
    }
    eligible = {key: expectation for key, _, expectation in expected}
    if not eligible or len(eligible) != len(expected):
        raise ValueError("empty or duplicated eligible inventory")
    seen = set()
    for key, label, phases in rows:
        protocol = []
        subtest_skipped = False
        for entry in phases:
            phase, status, *details = entry
            if status == "runtime-skip" and len(details) <= 1:
                if phase == "subtest" and protocol == [["setup", "passed"]]:
                    subtest_skipped = True
                    continue
                if phase == "teardown":
                    # A skipped finalizer still needs a valid setup/call sequence.
                    status = "passed"
            elif details:
                raise ValueError(f"malformed terminal phase: {label!r} {entry!r}")
            protocol.append([phase, status])
        if (
            key in seen
            or key not in eligible
            or (subtest_skipped and not any(phase == "call" for phase, _ in protocol))
            or (
                protocol not in allowed[eligible[key]]
                and protocol not in protocols("runtime-skip")
            )
        ):
            raise ValueError(
                f"unexpected, repeated or incomplete terminal: {label!r} {phases!r}"
            )
        seen.add(key)
    if seen != eligible.keys():
        missing = [label for key, label, _ in expected if key not in seen]
        raise ValueError(f"{len(missing)} tests never finished; first: {missing[0]!r}")


def report_runtime_skips(skips, *, partial, enforce, report_path, summary_path):
    """Publish the full list in either mode before optionally failing qualification."""
    skips.sort(key=lambda row: (row["suite"], row["group"], row["test"], row["id"]))
    Path(report_path).write_text(
        json.dumps({"partial": partial, "enforced": enforce, "tests": skips}, indent=2)
        + "\n",
        encoding="utf-8",
    )
    counts = collections.Counter((row["suite"], row["group"]) for row in skips)
    title = "Unexpected backend runtime skip" + (
        " (partial evidence)" if partial else ""
    )
    for row in skips:
        reasons = "; ".join(
            f"{skip['phase']}: {skip['reason']}" for skip in row["skips"]
        )
        text = f"{row['suite']}/{row['group']} {row['test']}: {reasons}"
        escaped = text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
        sys.stdout.write(f"::warning title={title}::{escaped}\n")
    with open(summary_path, "a", encoding="utf-8") as summary:
        summary.write(f"## Unexpected backend runtime skips: {len(skips)}\n\n")
        if partial:
            summary.write(
                "Partial evidence only: backend qualification failed. Counts are not run totals.\n\n"
            )
        summary.write(
            "Mode: "
            + ("enforced" if enforce else "report-only")
            + ". See `backend-runtime-skips` for tests and reasons.\n\n"
        )
        summary.write(
            "| Suite | Shard | Recorded tests with runtime skips |\n|---|---:|---:|\n"
        )
        for (suite, group), count in sorted(counts.items()):
            summary.write(f"| {suite} | {group} | {count} |\n")
        if not counts and not partial:
            summary.write("| All | All | 0 |\n")
    if skips and enforce and not partial:
        raise ValueError(f"{len(skips)} eligible tests reported runtime skips")


def pages(endpoint, field):
    raw = subprocess.check_output(["gh", "api", "--paginate", "--slurp", endpoint])
    return [row for page in json.loads(raw) for row in page[field]]


def verify_artifact(artifact, repo):
    raw = subprocess.check_output(
        ["gh", "api", f"repos/{repo}/actions/artifacts/{int(artifact['id'])}/zip"]
    )
    if artifact["digest"] != "sha256:" + hashlib.sha256(raw).hexdigest():
        raise ValueError(f"digest mismatch for artifact {artifact['id']}")
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        receipt = json.loads(archive.read("receipt.json"))
        records = [
            json.loads(line) for line in archive.read("terminal.jsonl").splitlines()
        ]
        collection = hashlib.sha256(archive.read("collected-ids.txt")).hexdigest()
        inventory = None
        if receipt["group"] == 1:
            if archive.read("inventory-exit.txt") != b"0\n":
                raise ValueError(f"eligible inventory failed for {receipt['suite']}")
            inventory = json.loads(archive.read("eligible.json"))
    return receipt, records, collection, inventory


def verify_run():
    run_id, repo = os.environ["GITHUB_RUN_ID"], os.environ["GITHUB_REPOSITORY"]
    root = f"repos/{repo}/actions/runs/{run_id}"
    legs = json.loads(os.environ["SHARDS"])["include"]
    wanted = {f"collected-ids-{leg['suite']}-{leg['group']}": leg for leg in legs}
    if not wanted or len(wanted) != len(legs):
        raise ValueError("empty or duplicated matrix")
    # Queue runs call backend-ci through the explicitly named reusable job.
    prefix = "backend / " if os.environ["GITHUB_EVENT_NAME"] == "merge_group" else ""
    jobs = {}
    for job in pages(root + "/jobs?filter=all&per_page=100", "jobs"):
        name = job["name"]
        if name not in jobs or job["run_attempt"] > jobs[name]["run_attempt"]:
            jobs[name] = job
    artifacts = [
        a
        for a in pages(root + "/artifacts?per_page=100", "artifacts")
        if a["name"].startswith("collected-ids-")
    ]
    by_name = collections.defaultdict(list)
    for artifact in artifacts:
        by_name[artifact["name"]].append(artifact)
    problems = []
    if set(by_name) != wanted.keys() or any(len(v) != 1 for v in by_name.values()):
        problems.append("the run does not have exactly one artifact per planned shard")
    plan_digest = hashlib.sha256(os.environ["SHARDS"].encode()).hexdigest()
    identities = set()
    expected = {}
    outcomes = collections.defaultdict(list)
    skips = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        # Only count observations from authentic receipts of the latest
        # producers. A failed leg may have evidence, but can never qualify.
        futures = {}
        for key, leg in wanted.items():
            suite, group = leg["suite"], leg["group"]
            name = f"{prefix}{leg['name']} shard {group}"
            if name not in jobs:
                problems.append(f"missing producer job {name!r}")
                continue
            if jobs[name]["conclusion"] != "success":
                problems.append(f"unsuccessful producer for {suite}/{group}")
            if len(by_name[key]) == 1:
                artifact = by_name[key][0]
                futures[pool.submit(verify_artifact, artifact, repo)] = (
                    artifact,
                    jobs[name],
                    suite,
                    group,
                )
        for future, (artifact, job, suite, group) in futures.items():
            try:
                receipt, rows, collection, inventory = future.result()
                if (
                    not rows
                    or receipt["suite"] != suite
                    or receipt["group"] != group
                    or receipt["run"] != run_id
                    or receipt["sha"] != os.environ["GITHUB_SHA"]
                    or receipt["attempt"] != job["run_attempt"]
                    or receipt["matrix"] != plan_digest
                ):
                    raise ValueError(
                        f"absent or superseded producer for {suite}/{group}"
                    )
                if (
                    not all(
                        re.fullmatch("[0-9a-f]{64}", receipt[k])
                        for k in ("durations", "temporal")
                    )
                    or not receipt["db"]
                    or not re.fullmatch("oss|[0-9a-f]{40}", receipt["ee"])
                ):
                    raise ValueError("incomplete dependency identities")
                observed = runtime_skips(rows)
            except (
                ValueError,
                TypeError,
                KeyError,
                OSError,
                subprocess.CalledProcessError,
                zipfile.BadZipFile,
            ) as exc:
                problems.append(f"{suite}/{group}: {exc}")
                continue
            skips.extend({"suite": suite, "group": group, **row} for row in observed)
            identities.add(
                (
                    collection,
                    receipt["ee"],
                    receipt["db"],
                    receipt["durations"],
                    receipt["temporal"],
                )
            )
            if inventory is not None:
                expected[suite] = inventory
            outcomes[suite].extend(rows)
            sys.stdout.write(
                f"{suite}/{group}: attempt {job['run_attempt']}, artifact {artifact['id']} {artifact['digest']}\n"
            )
    if not problems:
        try:
            if len(identities) != 1:
                raise ValueError(
                    "shards disagree on inputs or collection order; rerun all jobs"
                )
            all_ids = [
                key for inventory in expected.values() for key, _, _ in inventory
            ]
            if len(all_ids) != len(set(all_ids)):
                raise ValueError("suites have overlapping inventories")
            for suite, rows in outcomes.items():
                check_terminals(expected[suite], rows)
        except (KeyError, ValueError, TypeError) as exc:
            problems.append(str(exc))
    report_runtime_skips(
        skips,
        partial=bool(problems),
        enforce=os.environ.get("BACKEND_ENFORCE_RUNTIME_SKIPS") == "true",
        report_path=Path(os.environ["RUNNER_TEMP"]) / "backend-runtime-skips.json",
        summary_path=os.environ["GITHUB_STEP_SUMMARY"],
    )
    if problems:
        raise ValueError("; ".join(problems))


if __name__ == "__main__":
    if sys.argv[1:] == ["--terminal"]:
        verify_run()
    else:
        sys.exit(main(sys.argv))
