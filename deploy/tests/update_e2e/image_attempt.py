"""Failure-only evidence for the two shell-project fixture installs. No image or registry repairs."""
from __future__ import annotations

import http.client
import json
import re
import subprocess
import threading
import time
from collections import deque
from contextlib import closing, contextmanager, suppress

# The faults suite shares a process/daemon across worker threads. Docker events have no caller PID;
# retain our own CLI PIDs separately. An overlapping removal is an observation, not attribution.
_RMIS = deque(maxlen=64)
_LOCK = threading.Lock()
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
_FIXTURE_REF = re.compile(r"localhost:[0-9]{1,5}/futureagi/[a-z0-9_-]{1,48}(?::[a-zA-Z0-9_.-]{1,64}|@sha256:[0-9a-f]{64})")
_DAEMON_ID = re.compile(r"(?:[0-9a-f]{64}|[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}|[A-Z2-7]{4}(?::[A-Z2-7]{4}){11})")
_ACTIONS = ("pull", "tag", "untag", "delete")


def _image(value):
    return value if (_FIXTURE_REF.fullmatch(value) or re.fullmatch(r"(?:sha256:)?[0-9a-f]{64}", value)) else None


def capture_rmi(args, *, cwd, env, timeout, stdin):
    """Same subprocess.run contract as harness.run's rmi calls, with the actual docker CLI PID."""
    start, mono = time.time_ns(), time.monotonic_ns()
    with subprocess.Popen(args, cwd=cwd, env=env or None, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          stdin=subprocess.PIPE if stdin is not None else None) as proc:
        row = {}
        with suppress(Exception):  # bookkeeping must not affect fixture cleanup
            row = dict(start_unix_ns=start, start_mono_ns=mono, pid=proc.pid, exit=None, end_mono_ns=None,
                       args=["docker", "rmi", *[v if v == "-f" else _image(v) or "<other>" for v in args[2:10]]],
                       omitted_args=max(0, len(args) - 10))
            with _LOCK:
                _RMIS.append(row)
        try:
            stdout, stderr = proc.communicate(stdin, timeout=timeout)
        except BaseException:
            proc.kill()
            proc.wait()
            raise  # harness.run still handles TimeoutExpired; preserve every other failure too
        finally:
            with suppress(Exception), _LOCK:
                row.update(exit=proc.returncode, end_mono_ns=time.monotonic_ns(), end_unix_ns=time.time_ns())
        return subprocess.CompletedProcess(args, proc.returncode, stdout, stderr)


def _manifest(port, repo, tag, accept):
    # Local registry only: no proxy, redirects, auth, response body or header dumps.
    try:
        with closing(http.client.HTTPConnection("localhost", port, timeout=2)) as connection:
            connection.request("HEAD", f"/v2/{repo}/manifests/{tag}", headers={"Accept": accept})
            with connection.getresponse() as response:
                digest = response.getheader("Docker-Content-Digest", "")
                return dict(status=response.status, digest=digest if _DIGEST.fullmatch(digest) else None)
    except (OSError, http.client.HTTPException):
        return dict(status=None, digest=None)


def _inspect(result):
    if result.code == 0:
        value = result.stdout.strip()
        if _DIGEST.fullmatch(value):
            return dict(exit=0, category="present", image_id=value)
        category = "invalid_output" if value else "empty_output"
    elif result.code == -999:
        category = "timeout"
    else:
        err = result.stderr.lower()
        if "no such image" in err or "no such object" in err:
            category = "not_found"
        elif "cannot connect" in err or "connection refused" in err:
            category = "daemon_unreachable"
        elif "permission denied" in err or "unauthorized" in err:
            category = "access_denied"
        else:
            category = "other_error"
    return dict(exit=result.code, category=category)


def _unix(ns):
    return f"{ns // 1_000_000_000}.{ns % 1_000_000_000:09d}"


def _events(docker, start, end):
    # Ask for the daemon's finite history *ending at the exception*, not an open subscription. Don't
    # filter by the tag: delete events can name only an image ID. Never print arbitrary event attributes.
    result = docker("events", "--since", _unix(start), "--until", _unix(end), "--filter", "type=image",
                    *(word for action in _ACTIONS for word in ("--filter", f"event={action}")),
                    "--format", '{{.TimeNano}}\t{{.Action}}\t{{.Actor.ID}}\t{{index .Actor.Attributes "name"}}')
    rows = deque(maxlen=24)
    count = 0
    lines = result.stdout.splitlines()
    for line in lines:
        parts = line.split("\t")
        if len(parts) == 4:
            when, action, actor, name = parts
            if re.fullmatch(r"[0-9]{1,20}", when) and action in _ACTIONS and (_image(actor) or _image(name)):
                count += 1
                rows.append(dict(unix_ns=int(when), action=action, actor=_image(actor), name=_image(name)))
    return dict(exit=result.code, source="daemon_recent_history", returned=len(lines),
                filtered=len(lines) - count, omitted=count - len(rows), events=list(rows))


def _report(co, registry, tag, start, mono, end, stop, error):
    from . import harness as e2e

    ref = co.harness.image_ref(tag, registry=registry.host)
    row = dict(project=co.project, tag=ref, start_unix_ns=start, end_unix_ns=end, start_mono_ns=mono,
               end_mono_ns=stop, install_exit=error.result.code if isinstance(error, e2e.CommandFailed) else None)

    def docker(*args):
        try:
            # Same cwd and Docker selection as Checkout.compose, not bin/update's fault-injection wrapper.
            return e2e.run(["docker", *args], cwd=co.root, env=e2e.clean_environ(), check=False, timeout=2)
        except (OSError, ValueError):
            return e2e.Result([], -1, "", "")

    row["image_history"] = _events(docker, start, end)  # sample the finite daemon history before other probes
    row["manifest_head_after_failure"] = _manifest(registry.port, e2e.REPOSITORY, tag, e2e.MANIFEST_ACCEPT)
    daemon = docker("info", "--format", "{{.ID}}")
    identity = daemon.stdout.strip()
    row["daemon_after_failure"] = dict(exit=daemon.code, id=identity if _DAEMON_ID.fullmatch(identity) else None)
    row["inspect_after_failure"] = _inspect(docker("image", "inspect", "--format", "{{.Id}}", ref))
    with _LOCK:
        overlap = [dict(r) for r in _RMIS if r["start_mono_ns"] <= stop and
                   (r["end_mono_ns"] is None or r["end_mono_ns"] >= mono)]
    row["fixture_rmis"] = overlap[-12:]
    row["omitted_fixture_rmis"] = max(0, len(overlap) - 12)
    e2e.log("install image evidence " + json.dumps(row, separators=(",", ":")))


@contextmanager
def install_window(co, registry, tag):
    start, mono = time.time_ns(), time.monotonic_ns()
    try:
        yield
    except Exception as error:
        # unittest has not run checkout.down or registry.remove yet. A collector/log sink failure must
        # never replace the actual install exception (nor cause an otherwise passing install to fail).
        with suppress(Exception):
            _report(co, registry, tag, start, mono, time.time_ns(), time.monotonic_ns(), error)
        raise
