"""Bind the actual checkout and grade the same four children on a PR or private worker."""

import json
import os
import signal
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import case

WORKFLOW = ".github/workflows/ci-scope-pilot.yml"
LIMIT = 64 * 1024


def git(*args):
    return subprocess.check_output(["git", *args], timeout=10, stderr=subprocess.DEVNULL)


def outer():
    env = os.environ
    event = json.loads(Path(env["GITHUB_EVENT_PATH"]).read_bytes())
    pr = event["pull_request"]
    repo = env["GITHUB_REPOSITORY"]
    case.require(repo == event["repository"]["full_name"] == pr["base"]["repo"]["full_name"], "outer repository")
    case.require(event["action"] in ("opened", "synchronize", "reopened") and event["number"] == pr["number"], "outer event")
    commit, tree, *parents = git("show", "-s", "--format=%H %T %P", "HEAD").decode().split()
    base = pr["base"]["sha"]
    case.require(len(parents) == 2 and case.oid(base), "not an identified PR merge")
    ancestor_rc = subprocess.run(["git", "merge-base", "--is-ancestor", base, parents[0]], timeout=10).returncode
    o = dict(
        event=env["GITHUB_EVENT_NAME"], action=event["action"], ref=env["GITHUB_REF"], pr=event["number"],
        repository=repo, base_ref=pr["base"]["ref"], advertised_base=base, effective_base=parents[0],
        head=pr["head"]["sha"], parents=parents, ancestor_rc=ancestor_rc, event_sha=env["GITHUB_SHA"],
        tested_commit=commit, tested_tree=tree, run_id=env["GITHUB_RUN_ID"], run_attempt=env["GITHUB_RUN_ATTEMPT"],
        job=env["GITHUB_JOB"], workflow_ref=env["GITHUB_WORKFLOW_REF"], workflow_sha=env["GITHUB_WORKFLOW_SHA"],
        run_url=f"{env['GITHUB_SERVER_URL']}/{repo}/actions/runs/{env['GITHUB_RUN_ID']}/attempts/{env['GITHUB_RUN_ATTEMPT']}",
    )
    case.check_outer(o)
    case.require(o["job"] == "calibrate" and o["workflow_ref"] == f"{repo}/{WORKFLOW}@{o['ref']}" and o["workflow_sha"] == commit, "outer workflow identity")
    return o


def private_outer():
    # Never reinterpret a hosted attempt as a private success if its event is malformed.
    case.require(not any(key.startswith("GITHUB_") for key in os.environ), "hosted environment in private mode")
    base, head, tree = (os.environ[k] for k in ("BASE_OID", "HEAD_OID", "HEAD_TREE"))
    case.require(all(case.oid(oid) for oid in (base, head, tree)), "invalid controller OID")
    commit, actual_tree, *parents = git("show", "-s", "--format=%H %T %P", "HEAD").decode().split()
    case.require((commit, actual_tree) == (head, tree), "private checkout mismatch")
    case.require(git("rev-parse", "--is-shallow-repository").strip() == b"false", "shallow private checkout")
    case.require(not git("status", "--porcelain", "--untracked-files=all", "--ignore-submodules=none"), "dirty private checkout")
    case.require(git("merge-base", "--all", base, commit).decode().split() == [base], "private base is not an ancestor")
    return dict(event="local/private-preflight", base=base, tested_commit=commit, tested_tree=actual_tree,
                parents=parents, execution_id=os.urandom(16).hex())


def controller_hashes():
    root = Path(git("rev-parse", "--show-toplevel").decode().strip())
    paths = [root / WORKFLOW, *(HERE / name for name in ("case.py", "observe.py", "run.py", "test_case.py"))]
    hashes = {}
    for path in paths:
        relative = path.relative_to(root).as_posix()
        content = path.read_bytes()
        case.require(content == git("show", "HEAD:" + relative), "controller checkout mismatch: " + relative)
        hashes[relative] = case.digest(content)
    return hashes


def child(arm, role, binding):
    # Control selection, deadlines and evidence outside the unittest process. No inherited Actions env.
    argv = [sys.executable, "-I", "-B", str(HERE / "observe.py"), arm, role, binding]
    env = {"PATH": "/usr/bin:/bin", "LANG": "C", "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull}
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        with subprocess.Popen(argv, stdout=stdout, stderr=stderr, env=env, start_new_session=True) as proc:
            timed_out = False
            try:
                proc.wait(timeout=60)
            except subprocess.TimeoutExpired:
                timed_out = True
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait(timeout=10)
            rc = proc.returncode
        stdout.seek(0)
        payload = stdout.read(LIMIT + 1)
        stderr.seek(0)
        diag = stderr.read(LIMIT + 1)
    raw = dict(returncode=rc, timed_out=timed_out, stdout_sha256=case.digest(payload), stderr_sha256=case.digest(diag), receipt=None)
    if payload and len(payload) <= LIMIT:
        try:
            raw["receipt"] = json.loads(payload)
        except (UnicodeError, json.JSONDecodeError):
            raw["parse_error"] = "invalid child JSON"
    return raw


def grade(arm, role, raw, binding):
    try:
        case.require(raw["timed_out"] is False, "child timeout")
        case.require(raw["stderr_sha256"] == case.digest(b""), "unexpected child diagnostics")
        case.require(raw["stdout_sha256"] == case.digest(case.encode(raw["receipt"])), "child receipt digest")
        result = case.check_result(arm, role, raw["receipt"], raw["returncode"], binding)
        return {"outcome": result}
    except case.Invalid as exc:
        return {"outcome": "invalid", "reason": str(exc)}
    except (KeyError, TypeError, ValueError, IndexError) as exc:
        return {"outcome": "invalid", "reason": "malformed evidence: " + type(exc).__name__}


def run(out, *, private=False):
    receipt = dict(task=case.TASK, task_sha256=case.digest(case.encode(case.TASK)), outcome="unverified", results=[], backend_receipt="not available to this job")
    try:
        receipt["outer"] = private_outer() if private else outer()
        receipt["controller_sha256"] = controller_hashes()
        binding = case.digest(case.encode({k: receipt[k] for k in ("task_sha256", "outer", "controller_sha256")}))
        receipt["binding"] = binding
        observed = {}
        for arm in case.SUBJECT:
            for role in case.METHOD:
                raw = child(arm, role, binding)
                data = case.encode(raw)
                path = out / f"{arm}-{role}.json"
                path.write_bytes(data)
                result = grade(arm, role, raw, binding)
                observed.setdefault(arm, {})[role] = result["outcome"]
                receipt["results"].append(dict(arm=arm, role=role, test_id=case.full_id(role), path=path.name, sha256=case.digest(data), **result))
        if observed == case.MATRIX:
            receipt["outcome"] = "calibrated"
    except (KeyError, ValueError, OSError, subprocess.SubprocessError) as exc:
        receipt["error"] = str(exc)[:200] if isinstance(exc, case.Invalid) else type(exc).__name__
    finally:
        (out / "receipt.json").write_bytes(case.encode(receipt))
        print("Historical calibration:", receipt["outcome"])
        for r in receipt["results"]:
            print(r["arm"], r["role"], r["outcome"], r.get("reason", ""))
        if "error" in receipt:
            print(receipt["error"])
    return int(receipt["outcome"] != "calibrated")


if __name__ == "__main__":
    if sys.argv[1:] == ["--private-preflight"]:
        out = Path(os.environ["PILOT_EVIDENCE_DIR"]).resolve()
        root = Path(git("rev-parse", "--show-toplevel").decode().strip())
        case.require(not out.is_relative_to(root), "private evidence must be outside checkout")
        out.mkdir(mode=0o700)  # Refuse an existing directory or a stale receipt.
        sys.exit(run(out, private=True))
    if len(sys.argv) != 1:
        sys.exit("Unknown replay mode.")
    sys.exit(run(Path(os.environ["RUNNER_TEMP"]) / "stale-base-pilot"))
