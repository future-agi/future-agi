"""pytest plugin for Backend CI: records the id of every test this process collected.

Every shard collects the whole suite in its own process, and pytest-split
then keeps one group of it. That runs each test exactly once only when all
shards collect the same ids in the same order (pytest-split breaks ties
between equal times by position). An id that holds a per-process random value
(a uuid4() in a parametrize list) breaks it without any failure: one case runs
in two shards and another in none. Each shard records what it collected, in
collection order, and check_collected_ids.py fails the run when two records
differ.

    PYTHONPATH=.ci pytest -p collected_ids --ci-collected-ids=ids.txt ...
"""

import hashlib
import json
import os

import pytest
from _pytest.mark import MarkMatcher
from _pytest.mark.expression import Expression
from _pytest.mark.structures import normalize_mark_list
from _pytest.skipping import evaluate_skip_marks, evaluate_xfail_marks
from pytest import SubtestReport

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


def pytest_addoption(parser):
    # Receipt destinations belong to this invocation, not to child pytest
    # processes started by a test (including this plugin's own self-tests).
    parser.addoption("--ci-collected-ids", metavar="PATH")
    parser.addoption("--ci-eligible-ids", metavar="PATH")
    parser.addoption("--ci-terminal-ids", metavar="PATH")


def pytest_configure(config):
    if path := config.getoption("--ci-eligible-ids"):
        config.pluginmanager.register(EligibleInventory(path, config.option.markexpr))
    if path := config.getoption("--ci-terminal-ids"):
        config.pluginmanager.register(TerminalRecorder(path))


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(config, items):
    # tryfirst: before -m and pytest-split deselect anything.
    path = config.getoption("--ci-collected-ids")
    if path:
        record((item.nodeid for item in items), path)


# An unsharded, marker-filtered collection supplies the expected inventory.
# A second process records terminal outcomes; a collected ID is not a result.
def node_key(nodeid):
    return hashlib.sha256(nodeid.encode("utf-8", "surrogateescape")).hexdigest()


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


LIVE_EXAMPLES = {
    "ee/experiments/src/agent/test_flow.py",
    "ee/experiments/src/agent/test_flow_halueval.py",
    "ee/experiments/src/playground/test_conversation.py",
    "ee/experiments/src/playground/test_rag.py",
}


class EligibleInventory:
    def __init__(self, path, markexpr):
        self.path = path
        self.expression = Expression.compile(markexpr) if markexpr else None

    @pytest.hookimpl(wrapper=True)
    def pytest_make_collect_report(self, collector):
        report = yield
        if report.skipped and report.nodeid in LIVE_EXAMPLES and self.expression:
            # A module-level skip aborts the import before pytest can attach
            # module marks to items. Read the declarations that actually ran,
            # without importing the live example a second time. pytest drops
            # report.call immediately after this hook (collect_one_node).
            for entry in report.call.excinfo.traceback:
                if entry.path == collector.path and entry.name == "<module>":
                    marks = entry.frame.f_globals.get("pytestmark", [])
                    if not isinstance(marks, list):
                        marks = [marks]
                    matcher = MarkMatcher.from_markers(normalize_mark_list(marks))
                    report.ci_example_excluded = not self.expression.evaluate(matcher)
                    break
        return report

    def pytest_collectreport(self, report):
        if not report.skipped:
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
        # These four modules opt out before importing network-backed examples.
        # Accept that exact opt-out only when their declared marks exclude them.
        if (
            report.nodeid in LIVE_EXAMPLES
            and reason
            == "Skipped: Experimental live LLM example; set RUN_EXPERIMENT_EXAMPLES=1 to run."
            and os.environ.get("RUN_EXPERIMENT_EXAMPLES") != "1"
            and getattr(report, "ci_example_excluded", False)
        ):
            return
        pytest.exit(
            f"unexpected collection skip: {report.nodeid}: {reason}", returncode=1
        )

    def pytest_sessionfinish(self, session, exitstatus):
        # collection_finish also runs after an interrupted partial collection.
        if exitstatus != pytest.ExitCode.OK:
            return
        eligible = [
            [
                node_key(item.nodeid),
                item.nodeid[:SHOWN_ID_LENGTH],
                expected_outcome(item),
            ]
            for item in session.items
        ]
        with open(self.path, "w", encoding="utf-8") as out:
            json.dump(eligible, out)


class TerminalRecorder:
    def __init__(self, path):
        self.path = path
        self.phases = {}

    @pytest.hookimpl(hookwrapper=True, tryfirst=True)
    def pytest_runtest_makereport(self, item, call):
        result = yield
        report = result.get_result()
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
                or (
                    call.when == "setup"
                    and exc
                    and getattr(exc.value, "_use_item_location", False)
                )
                else "runtime-skip"
            )
        report.ci_terminal_status = status

    def pytest_runtest_logreport(self, report):
        # Pytest 9 emits a call report for each unittest.subTest as well as
        # the item's own call. Only the latter completes the item's protocol.
        phases = self.phases.setdefault(report.nodeid, [])
        phase = [report.when, report.ci_terminal_status]
        if report.ci_terminal_status == "runtime-skip":
            reason = report.longrepr
            phase.append(str(reason[-1] if isinstance(reason, tuple) else reason))
        if isinstance(report, SubtestReport):
            if report.ci_terminal_status != "passed":
                phase[0] = "subtest"
                phases.append(phase)
            return
        phases.append(phase)
        if report.when == "teardown":
            with open(self.path, "a", encoding="utf-8") as out:
                out.write(
                    json.dumps(
                        [
                            node_key(report.nodeid),
                            report.nodeid[:SHOWN_ID_LENGTH],
                            phases,
                        ]
                    )
                    + "\n"
                )
            del self.phases[report.nodeid]
