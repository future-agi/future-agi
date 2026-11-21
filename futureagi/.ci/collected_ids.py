"""pytest plugin for Backend CI: records the id of every test this process collected.

Every shard collects the whole suite in its own process, and pytest-split
then keeps one group of it. That runs each test exactly once only when all
shards collect the same ids in the same order (pytest-split breaks ties
between equal times by position). An id that holds a per-process random value
(a uuid4() in a parametrize list) breaks it without any failure: one case runs
in two shards and another in none. Each shard records what it collected, in
collection order, and check_collected_ids.py fails the run when two records
differ.

    PYTHONPATH=.ci COLLECTED_IDS_FILE=ids.txt pytest -p collected_ids ...
"""

import hashlib
import json
import os

import pytest
from _pytest.skipping import evaluate_skip_marks, evaluate_xfail_marks

# A few generated parameter ids are hundreds of kilobytes long. The digest
# covers the whole id; the text is for the person who reads a difference.
SHOWN_ID_LENGTH = 300


def record(nodeids, path):
    """One line per collected test, in collection order; no line for no test."""
    with open(path, "w", encoding="utf-8", errors="surrogateescape") as out:
        for nodeid in nodeids:
            digest = hashlib.sha256(
                nodeid.encode("utf-8", "surrogateescape")
            ).hexdigest()[:24]
            out.write(f"{digest} {nodeid[:SHOWN_ID_LENGTH]}\n")


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(items):
    # tryfirst: before -m and pytest-split deselect anything.
    path = os.environ.get("COLLECTED_IDS_FILE")
    if path:
        record((item.nodeid for item in items), path)


# An unsharded, marker-filtered collection supplies the expected inventory.
# A second process records terminal outcomes; a collected ID is not a result.
def node_key(nodeid):
    return hashlib.sha256(nodeid.encode("utf-8", "surrogateescape")).hexdigest()


def pytest_collectreport(report):
    if not report.skipped or not os.environ.get("ELIGIBLE_IDS_FILE"):
        return
    reason = (
        str(report.longrepr[-1])
        if isinstance(report.longrepr, tuple)
        else str(report.longrepr)
    )
    # OSS cannot import the private EE package. Collection-time skips for
    # other reasons hide whole modules from both discovery and execution.
    if os.environ.get("IS_INTERNAL_RUN") == "false" and (
        "requires ee/" in reason
        or "could not import 'ee." in reason
        or "Enterprise Edition required" in reason
    ):
        return
    pytest.exit(f"unexpected collection skip: {report.nodeid}: {reason}", returncode=1)


def unittest_skip(item):
    return any(
        getattr(obj, "__unittest_skip__", False)
        for obj in (getattr(item, "obj", None), getattr(item, "cls", None))
    )


def expected_outcome(item):
    # Use the locked pytest's own condition evaluator (including inherited
    # module/class marks); a skip mark with a false condition is not a waiver.
    if unittest_skip(item) or evaluate_skip_marks(item) is not None:
        return "skip"
    xfail = evaluate_xfail_marks(item)
    if xfail is not None:
        return "xfail" if xfail.strict else "xfail-or-xpass"
    return "pass"


def pytest_collection_finish(session):
    path = os.environ.get("ELIGIBLE_IDS_FILE")
    if path:
        eligible = [
            [
                node_key(item.nodeid),
                item.nodeid[:SHOWN_ID_LENGTH],
                expected_outcome(item),
            ]
            for item in session.items
        ]
        with open(path, "w", encoding="utf-8") as out:
            json.dump(eligible, out)


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_runtest_makereport(item, call):
    result = yield
    report = result.get_result()
    if not os.environ.get("TERMINAL_IDS_FILE"):
        return
    status = report.outcome
    if hasattr(report, "wasxfail"):
        status = "xfail" if report.skipped else "xpass"
    elif report.skipped:
        # Pytest marks a skip from @skip/@skipif with use_item_location;
        # imperative pytest.skip() from a test or fixture is not such a mark.
        exc = call.excinfo
        status = (
            "declared-skip"
            if unittest_skip(item)
            or (exc and getattr(exc.value, "_use_item_location", False))
            else "runtime-skip"
        )
    phases = getattr(item, "_ci_terminal_phases", [])
    phases.append((report.when, status))
    item._ci_terminal_phases = phases
    if report.when == "teardown":
        with open(os.environ["TERMINAL_IDS_FILE"], "a", encoding="utf-8") as out:
            out.write(
                json.dumps(
                    [node_key(item.nodeid), item.nodeid[:SHOWN_ID_LENGTH], phases]
                )
                + "\n"
            )
