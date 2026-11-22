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
import datetime
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
# The run-time skips that are expected, as rules: a test-file glob AND a reason
# pattern. A skip is allowed only when both match, so a rule cannot swallow a
# new skip with another reason in a covered file, nor the same reason elsewhere.
RUNTIME_SKIP_RULES = Path(__file__).with_name("runtime-skips.json")


def differing(records):
    """The lines that some records hold more often than others, with the number of records that hold each."""
    counts = [collections.Counter(lines) for lines in records]
    odd = (
        (line, sum(1 for count in counts if count[line]))
        for line in set().union(*counts)
        if len({count[line] for count in counts}) > 1
    )
    return sorted(odd, key=lambda found: found[0].partition(" ")[2])


def read(path):
    with open(path, encoding="utf-8", errors="surrogateescape") as handle:
        return [line for line in handle.read().splitlines() if line]


def file_digest(path):
    """A digest of a record, read in pieces."""
    found = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            found.update(block)
    return found.hexdigest()


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
    # Equal digests are equal records. A record is about 10 MB, so the usual,
    # equal case holds one of them in memory and not one per shard; all of
    # them are loaded only to say how they differ.
    if len({file_digest(path) for path in paths}) == 1:
        collected = read(paths[0])
        if collected:
            sys.stdout.write(
                f"{expected} shards collected the same {len(collected)} tests in the same order\n"
            )
            return 0
    records = [read(path) for path in paths]
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


RUNS = ("oss", "internal")
# The same limit as the e2e list (e2e/scripts/shard-report.mjs MAX_RUNTIME_SKIP_DAYS).
MAX_RUNTIME_SKIP_DAYS = 45
DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
GLOB = re.compile(r"[*?\[\]]")


def annotation(text):
    """Workflow-command data: % and line breaks escaped, as Actions requires."""
    return str(text).replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def read_runtime_skip_rules(path=RUNTIME_SKIP_RULES, today=None):
    """The committed rules. Fails closed: a broken rule stops the check.

    Every rule names the exact test files it covers, each with the number of
    tests measured to skip there (its cap), optionally the exact node ids it
    is limited to, an anchored reason, why, the
    ticket that removes it, an owner and an expiry at most 45 days after
    `today` (the run date). An expired rule is still read but allows nothing.
    A rule may name the kind of run it covers: "oss" (a fork pull request or a
    merge group, without the private package) or "internal". Without it a rule
    covers both. A rule whose skips depend on data other tests leave behind
    says why in "varies": each of its files then maps to the number of tests the
    inventory collects there, an upper bound, and matching nothing is no problem.
    """
    today = today or datetime.datetime.now(datetime.UTC).date()
    entries = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(entries, list):
        raise ValueError(f"{path}: expected a list")
    rules = []
    for entry in entries:
        fields = ("reason", "why", "issue", "owner", "expires")
        if not isinstance(entry, dict) or any(
            not isinstance(entry.get(field), str) or not entry[field].strip()
            for field in fields
        ):
            raise ValueError(
                f"{path}: each rule needs reason, why, issue, owner and expires: {entry!r}"
            )
        files = entry.get("files")
        if (
            not isinstance(files, dict)
            or not files
            or any(
                not isinstance(name, str)
                or not name.endswith(".py")
                or name.startswith("/")
                or GLOB.search(name)
                or type(count) is not int
                or count < 1
                for name, count in files.items()
            )
        ):
            raise ValueError(
                f"{path}: files maps each exact test file to its measured count: {entry!r}"
            )
        tests = entry.get("tests")
        if tests is not None and (
            not isinstance(tests, list)
            or not tests
            or any(
                not isinstance(test, str)
                or test.split("::", 1)[0] not in files
                or "::" not in test
                or re.search(r"[*?]", test)
                for test in tests
            )
        ):
            raise ValueError(
                f"{path}: tests lists exact node ids inside the rule's files: {entry!r}"
            )
        if not entry["reason"].startswith("^"):
            raise ValueError(f"{path}: a reason is anchored at its start: {entry!r}")
        if not DATE.fullmatch(entry["expires"]):
            raise ValueError(f"{path}: expires is a YYYY-MM-DD date: {entry!r}")
        expires = datetime.date.fromisoformat(entry["expires"])
        if (expires - today).days > MAX_RUNTIME_SKIP_DAYS:
            raise ValueError(
                f"{path}: a rule expires at most {MAX_RUNTIME_SKIP_DAYS} days ahead: {entry!r}"
            )
        if entry.get("runs", "oss") not in RUNS:
            raise ValueError(f"{path}: runs is one of {RUNS}: {entry!r}")
        if "varies" in entry and (
            not isinstance(entry["varies"], str) or not entry["varies"].strip()
        ):
            raise ValueError(
                f"{path}: varies says why a rule's count depends on other tests: {entry!r}"
            )
        rules.append(
            {
                **entry,
                "pattern": re.compile(entry["reason"]),
                "expired": expires < today,
            }
        )
    return rules


def covers(row, rule):
    """Whether a rule's files, run kind and reason cover every skip of a row.

    The test path is the one the independent inventory recorded for this id
    (bind_to_inventory), not the label of the terminal row: a row the
    inventory does not know is never covered.
    """
    test = row.get("inventory_test")
    return bool(test) and (
        rule.get("runs", row.get("runs")) == row.get("runs")
        and test.split("::", 1)[0] in rule["files"]
        and ("tests" not in rule or test in rule["tests"])
        and all(rule["pattern"].search(skip["reason"]) for skip in row["skips"])
    )


def allowing_rule(row, rules):
    """The index of the unexpired rule that covers every skip of a row, or None."""
    for index, rule in enumerate(rules):
        if not rule["expired"] and covers(row, rule):
            return index
    return None


def bind_to_inventory(skips, expected):
    """Give each skip row the test path the independent inventory has for its id."""
    labels = {
        key: label for inventory in expected.values() for key, label, _ in inventory
    }
    for row in skips:
        if row["id"] in labels:
            row["inventory_test"] = labels[row["id"]]
    return skips


def report_runtime_skips(
    skips,
    *,
    partial,
    enforce,
    report_path,
    summary_path,
    rules=None,
    runs=RUNS,
    inventory_files=None,
    collected=None,
    today=None,
):
    """Publish the full list in either mode before optionally failing qualification.

    Skips a rule of runtime-skips.json covers, up to the rule's count in each
    file, are listed under "allowed" with the rule; the others are unexpected.
    Rule problems are errors, judged on the whole run: an expired rule that
    still matches, and on complete evidence only, a file over its count, a
    scheduled rule file that matched nothing (unless the rule varies), and a
    varying rule whose bound is not what the inventory collected for the file
    (`collected`). Unexpected skips and rule problems fail an enforced,
    complete run.
    """
    rules = read_runtime_skip_rules(today=today) if rules is None else rules
    skips.sort(key=lambda row: (row["suite"], row["group"], row["test"], row["id"]))
    recorded = collections.Counter((row["suite"], row["group"]) for row in skips)
    allowed = []
    unexpected = []
    used = collections.Counter()
    over = collections.Counter()
    for row in skips:
        index = allowing_rule(row, rules)
        if index is not None:
            path = row["inventory_test"].split("::", 1)[0]
            if used[index, path] < rules[index]["files"][path]:
                used[index, path] += 1
                allowed.append(
                    {**row, "rule": rules[index]["why"], "issue": rules[index]["issue"]}
                )
                continue
            over[index, path] += 1
        unexpected.append(row)
    # Counts are judged on the whole run, and only on complete evidence.
    problems = (
        []
        if partial
        else [
            f"{path}: {used[key] + count} tests skip under the rule for {rules[key[0]]['issue']},"
            f" above its {'bound' if 'varies' in rules[key[0]] else 'measured'}"
            f" {rules[key[0]]['files'][path]}"
            for key, count in sorted(over.items())
            for path in [key[1]]
        ]
    )
    for index, rule in enumerate(rules):
        if rule["expired"]:
            matching = sum(covers(row, rule) for row in unexpected)
            if matching:
                problems.append(
                    f"the rule for {rule['issue']} expired on {rule['expires']};"
                    f" its {matching} tests count as unexpected ({rule['owner']})"
                )
        elif not partial and rule.get("runs", runs[0]) in runs:
            # Only a file this run's inventory scheduled can be stale: per-change
            # selection does not run every suite, and an internal run says
            # nothing about a fork-only rule.
            for path, count in rule["files"].items():
                if "varies" in rule:
                    if collected is not None and collected.get(path, count) != count:
                        problems.append(
                            f"{path}: the rule for {rule['issue']} bounds it at {count} tests,"
                            f" but the inventory collected {collected[path]}"
                        )
                    continue
                if (inventory_files is None or path in inventory_files) and not used[
                    index, path
                ]:
                    problems.append(
                        f"{path}: the rule for {rule['issue']}"
                        + (f" ({rule['runs']} runs)" if "runs" in rule else "")
                        + " matched nothing;"
                        " remove it from futureagi/.ci/runtime-skips.json"
                    )
    Path(report_path).write_text(
        json.dumps(
            {
                "partial": partial,
                "enforced": enforce,
                "tests": unexpected,
                "allowed": allowed,
                "rule_problems": problems,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    allowed_counts = collections.Counter(
        (row["suite"], row["group"]) for row in allowed
    )
    title = "Unexpected backend runtime skip" + (
        " (partial evidence)" if partial else ""
    )
    for row in unexpected:
        reasons = "; ".join(
            f"{skip['phase']}: {skip['reason']}" for skip in row["skips"]
        )
        text = f"{row['suite']}/{row['group']} {row['test']}: {reasons}"
        sys.stdout.write(f"::warning title={title}::{annotation(text)}\n")
    for problem in problems:
        sys.stdout.write(
            f"::error title=Backend run-time skip rule::{annotation(problem)}\n"
        )
    with open(summary_path, "a", encoding="utf-8") as summary:
        summary.write(
            f"## Backend runtime skips: {len(skips)} "
            f"({len(allowed)} allowed, {len(unexpected)} unexpected)\n\n"
        )
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
            "| Suite | Shard | Recorded tests with runtime skips | Allowed | Unexpected |\n"
            "|---|---:|---:|---:|---:|\n"
        )
        for (suite, group), count in sorted(recorded.items()):
            allowed_here = allowed_counts[suite, group]
            summary.write(
                f"| {suite} | {group} | {count} | {allowed_here} | {count - allowed_here} |\n"
            )
        if not recorded and not partial:
            summary.write("| All | All | 0 | 0 | 0 |\n")
        summary.write(f"\nAllowed by runtime-skips.json: {len(allowed)} tests.\n\n")
        per_rule = collections.Counter()
        for (index, _), count in used.items():
            per_rule[index] += count
        for index, count in sorted(per_rule.items()):
            rule = rules[index]
            total = sum(rule["files"].values())
            summary.write(
                f"- {count} (of {f'up to {total} collected' if 'varies' in rule else f'{total} measured'}), {rule['issue']},"
                f" {rule['owner']}, expires {rule['expires']}: {rule['why']}\n"
            )
        if problems:
            summary.write("\nRule problems:\n\n")
            for problem in problems:
                summary.write(f"- {problem}\n")
    if enforce and not partial and (unexpected or problems):
        raise ValueError(
            f"{len(unexpected)} eligible tests reported unexpected runtime skips;"
            f" {len(problems)} runtime-skip rule problems"
        )


def producer_attempt(matches, name):
    """The latest job of a leg, and the attempt that produced its receipt.

    "Re-run failed jobs" starts a new attempt in which each job it does not
    re-run appears again as a copy: the new run_attempt, but the original
    start and end times, and no new artifact. So a leg's receipt carries the
    earliest attempt of the copies of its latest job, the same rule as the
    e2e report (e2e/scripts/shard-report.mjs shardAttempts). Fails closed:
    the latest job must have finished, and its copies must be consecutive.
    """
    matches = sorted(matches, key=lambda job: job["run_attempt"], reverse=True)
    if (
        not matches
        or not matches[0].get("started_at")
        or not matches[0].get("completed_at")
    ):
        raise ValueError(f"missing producer job {name!r}")
    attempts = [job["run_attempt"] for job in matches]
    if len(set(attempts)) != len(attempts):
        raise ValueError(f"repeated producer job {name!r}")
    latest = matches[0]
    copies = [
        job
        for job in matches
        if (job.get("started_at"), job.get("completed_at"))
        == (latest["started_at"], latest["completed_at"])
    ]
    first = copies[-1]["run_attempt"]
    if len(copies) != latest["run_attempt"] - first + 1:
        raise ValueError(f"inconsistent producer copies {name!r}")
    return latest, first


def malformed_inventory(inventory):
    """Whether an inventory is not a list of [id, test path, expectation] rows."""
    return not isinstance(inventory, list) or any(
        not isinstance(item, list)
        or len(item) != 3
        or not all(isinstance(field, str) for field in item[:2])
        for item in inventory
    )


def run_date(root):
    """The run's creation date (UTC), which every attempt of the run shares."""
    created = json.loads(subprocess.check_output(["gh", "api", root]))["created_at"]
    return datetime.date.fromisoformat(created[:10])


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
    by_job = collections.defaultdict(list)
    for job in pages(root + "/jobs?filter=all&per_page=100", "jobs"):
        by_job[job["name"]].append(job)
    artifacts = [
        a
        for a in pages(root + "/artifacts?per_page=100", "artifacts")
        if a["name"].startswith("collected-ids-")
    ]
    by_name = collections.defaultdict(list)
    for artifact in artifacts:
        by_name[artifact["name"]].append(artifact)
    problems = []
    # Rule expiry is judged on the run's creation date, which every attempt of
    # the run shares: re-running only this job cannot change the verdict.
    try:
        today = run_date(root)
    except (
        OSError,
        subprocess.CalledProcessError,
        KeyError,
        TypeError,
        ValueError,
    ) as exc:
        problems.append(f"the run date is unavailable: {exc}")
        today = None
    if set(by_name) != wanted.keys() or any(len(v) != 1 for v in by_name.values()):
        problems.append("the run does not have exactly one artifact per planned shard")
    plan_digest = hashlib.sha256(os.environ["SHARDS"].encode()).hexdigest()
    identities = set()
    expected = {}
    outcomes = collections.defaultdict(list)
    skips = []
    kinds = set()
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        # Only count observations from authentic receipts of the latest
        # producers. A failed leg may have evidence, but can never qualify.
        futures = {}
        for key, leg in wanted.items():
            suite, group = leg["suite"], leg["group"]
            name = f"{prefix}{leg['name']} shard {group}"
            try:
                latest, producer = producer_attempt(by_job.get(name, []), name)
            except ValueError as exc:
                problems.append(str(exc))
                continue
            if latest["conclusion"] != "success":
                problems.append(f"unsuccessful producer for {suite}/{group}")
            if len(by_name[key]) == 1:
                artifact = by_name[key][0]
                futures[pool.submit(verify_artifact, artifact, repo)] = (
                    artifact,
                    {**latest, "producer_attempt": producer},
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
                    or receipt["attempt"] != job["producer_attempt"]
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
                if inventory is not None and malformed_inventory(inventory):
                    # One validation at ingest: a malformed row marks this suite
                    # partial and never reaches the skip evidence below.
                    raise ValueError("malformed inventory row")
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
            runs = "oss" if receipt["ee"] == "oss" else "internal"
            kinds.add(runs)
            skips.extend(
                {"suite": suite, "group": group, "runs": runs, **row}
                for row in observed
            )
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
                f"{suite}/{group}: attempt {job['producer_attempt']}, artifact {artifact['id']} {artifact['digest']}\n"
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
    bind_to_inventory(skips, expected)
    report_runtime_skips(
        skips,
        partial=bool(problems),
        enforce=os.environ.get("BACKEND_ENFORCE_RUNTIME_SKIPS") == "true",
        report_path=Path(os.environ["RUNNER_TEMP"]) / "backend-runtime-skips.json",
        summary_path=os.environ["GITHUB_STEP_SUMMARY"],
        runs=tuple(sorted(kinds)) or RUNS,
        inventory_files={
            label.split("::", 1)[0]
            for inventory in expected.values()
            for _, label, _ in inventory
        },
        collected=collections.Counter(
            label.split("::", 1)[0]
            for inventory in expected.values()
            for _, label, _ in inventory
        ),
        today=today,
    )
    if problems:
        raise ValueError("; ".join(problems))


if __name__ == "__main__":
    if sys.argv[1:] == ["--terminal"]:
        verify_run()
    else:
        sys.exit(main(sys.argv))
