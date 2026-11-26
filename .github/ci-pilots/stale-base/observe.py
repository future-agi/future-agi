"""Child process for one method on one immutable historical subject."""

import importlib.util
import json
import os
import re
import resource
import shutil
import subprocess
import sys
import tempfile
import traceback
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))
import case

PREFIX = """trap 'printf "PILOT_ERR %d %d\\n" "$LINENO" "$?" >&2' ERR; """
MAX_BYTES = 128 * 1024


def git(*args):
    return subprocess.check_output(
        ["git", *args], timeout=10, stderr=subprocess.DEVNULL
    )


def field(*args):
    return git(*args).decode().strip()


def materialize(root, arm, source):
    pin = case.SUBJECT[arm]
    ref = pin["commit"]
    source.update(
        commit=field("rev-parse", ref + "^{commit}"),
        tree=field("rev-parse", ref + "^{tree}"),
    )
    case.require(
        source["commit"] == ref and source["tree"] == pin["tree"], "subject tree pin"
    )
    scripts = root / ".github/scripts"
    workflows = root / ".github/workflows"
    scripts.mkdir(parents=True)
    workflows.mkdir()

    def copy(commit, dest, expected):
        obj = f"{commit}:{dest.relative_to(root)}"
        case.require(0 < int(field("cat-file", "-s", obj)) <= MAX_BYTES, "source size")
        dest.write_bytes(git("show", obj))
        observed = case.digest(dest.read_bytes())
        case.require(observed == expected, "source digest: " + dest.name)
        return observed

    fixed = case.SUBJECT["reference"]["commit"]
    source["oracle"] = copy(fixed, scripts / "test_ci_scope.py", case.ORACLE)
    source["policy"] = copy(fixed, scripts / "ci_scope.py", case.POLICY)
    source["workflows"] = [
        copy(ref, workflows / name, digest)
        for name, digest in zip(case.WORKFLOWS, pin["workflows"], strict=True)
    ]
    spec = importlib.util.spec_from_file_location(
        "test_ci_scope", scripts / "test_ci_scope.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    bodies = [module.bootstrap(name) for name in case.WORKFLOWS]
    case.require(len(set(bodies)) == 1, "bootstrap copies disagree")
    source["bootstrap"] = case.digest(bodies[0].encode())
    source["executed"] = case.digest((PREFIX + bodies[0]).encode())
    case.require(
        source == dict(pin, oracle=case.ORACLE, policy=case.POLICY), "bootstrap pins"
    )
    return module, bodies[0]


def observed_loader(driver, calls, argv, *, env, capture_output, check=False):
    case.require(
        argv == ["bash", "-ec", driver] and capture_output and len(calls) < 4,
        "unknown loader call",
    )
    parents = field("show", "-s", "--format=%P", "HEAD").split()
    body = (
        PREFIX + driver
    )  # Same physical lines; the ERR trap never prints the command or environment.
    c = {
        "event": env["GITHUB_EVENT_NAME"],
        "base": env["BASE_SHA"],
        "head": env["HEAD_SHA"],
        "event_sha": env["GITHUB_SHA"],
        "tested_commit": field("rev-parse", "HEAD"),
        "tested_tree": field("rev-parse", "HEAD^{tree}"),
        "parents": parents,
        "event_tree": field("show", "-s", "--format=%T", env["GITHUB_SHA"]),
        "original_sha256": case.digest(driver.encode()),
        "executed_sha256": case.digest(body.encode()),
    }
    calls.append(c)
    if c["event"] == "pull_request":
        c["merge_base"] = field("merge-base", "--all", *parents)
        c["ancestor_rc"] = subprocess.run(
            ["git", "merge-base", "--is-ancestor", c["base"], parents[0]],
            timeout=10,
            check=False,
        ).returncode
    with tempfile.TemporaryFile() as err:
        done = subprocess.run(
            ["bash", "-ec", body],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=err,
            timeout=15,
            check=False,
        )
        err.seek(0)
        stderr = err.read(MAX_BYTES + 1)
    c.update(returncode=done.returncode, stderr_sha256=case.digest(stderr), plan=None)
    match = re.fullmatch(rb"PILOT_ERR (\d+) (\d+)\n", stderr)
    c["stderr_marker"] = (
        list(map(int, match.groups())) if match else ("other" if stderr else None)
    )
    output = Path(env["GITHUB_OUTPUT"])
    if output.exists():
        case.require(output.stat().st_size <= MAX_BYTES, "scope output too large")
        plans = [
            json.loads(s[6:])
            for s in output.read_text().splitlines()
            if s.startswith("scope=")
        ]
        case.require(len(plans) == 1, "missing or duplicate plan")
        c["plan"] = plans[
            0
        ]  # Only output of digest-verified code over the synthetic fixture.
    if check:
        done.check_returncode()
    return done


class Recorder(unittest.TestResult):
    def __init__(self, record, oracle_file):
        super().__init__()
        self.record = record
        self.oracle_file = oracle_file

    def startTest(self, test):
        super().startTest(test)
        self.record["started"].append(test.id())

    def stopTest(self, test):
        self.record["stopped"].append(test.id())
        super().stopTest(test)

    def terminal(self, test, status, err=None):
        row = {"id": test.id(), "status": status}
        if err:
            row.update(
                exception=err[0].__name__,
                oracle_frames=[
                    [frame.name, frame.lineno]
                    for frame in traceback.extract_tb(err[2])
                    if frame.filename == self.oracle_file
                ],
            )
        self.record["terminal"].append(row)

    def addSuccess(self, test):
        self.terminal(test, "success")

    def addFailure(self, test, err):
        self.terminal(test, "failure", err)
        super().addFailure(test, err)

    def addError(self, test, err):
        self.terminal(test, "error", err)
        super().addError(test, err)

    def addSkip(self, test, reason):
        self.terminal(test, "skip")
        super().addSkip(test, reason)

    def addExpectedFailure(self, test, err):
        self.terminal(test, "expectedFailure", err)
        super().addExpectedFailure(test, err)

    def addUnexpectedSuccess(self, test):
        self.terminal(test, "unexpectedSuccess")
        super().addUnexpectedSuccess(test)


def main(arm, role, binding):
    record = {
        "arm": arm,
        "role": role,
        "binding": binding,
        "source": {},
        "calls": [],
        "selected": [],
        "started": [],
        "terminal": [],
        "stopped": [],
    }
    rc = 2
    try:
        resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_BYTES, MAX_BYTES))
        case.require(
            arm in case.SUBJECT
            and role in case.METHOD
            and re.fullmatch("[0-9a-f]{64}", binding),
            "unknown case",
        )
        for tool in ("bash", "git", "jq", "python3"):
            case.require(shutil.which(tool), "missing prerequisite: " + tool)
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["HOME"] = tmp
            module, driver = materialize(Path(tmp), arm, record["source"])
            record["bodies"] = {"bootstrap": driver, "executed": PREFIX + driver}

            def capture(*args, **kwargs):
                return observed_loader(driver, record["calls"], *args, **kwargs)

            module.subprocess = SimpleNamespace(
                check_output=subprocess.check_output, run=capture
            )
            test = module.CandidateScope(case.METHOD[role])
            record["selected"].append(test.id())
            result = Recorder(record, module.__file__)
            unittest.TestSuite([test]).run(result)
            rc = int(not result.wasSuccessful())
    except (
        OSError,
        ValueError,
        KeyError,
        ImportError,
        AttributeError,
        subprocess.SubprocessError,
    ) as exc:
        record["error"] = (
            str(exc)[:200] if isinstance(exc, case.Invalid) else type(exc).__name__
        )
    sys.stdout.buffer.write(case.encode(record))
    return rc


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:]))
