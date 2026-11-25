"""pytest plugin for Backend CI: which backend files a fresh migrate touches.

Observation only. It reports; it never fails, enforces, or changes the dump key.

    CI_MIGRATE_INPUTS=DIR PYTHONPATH=.ci pytest -p migrate_inputs .ci/migrated_db.py --create-db

Loaded with -p, it is imported before pytest-django runs django.setup(), so it
sees everything from there through the database setup: migrate and its
post_migrate handlers, which pytest-django runs while setting up the first
test. The first test's call ends that phase. What the test itself touches is
counted separately.

It records, as paths relative to the backend root (futureagi/):
- every module imported from the tree (its source file, never a .pyc);
- every file opened in the tree (fixtures and data files a migration reads);
- every directory listed in the tree (a new migration file changes a listing).

It never records file contents or environment values. The virtualenv is
skipped. The private EE overlay (ee/cloud) is only counted, because the
report is uploaded as an artifact of a public repository.

It also records its own cost: the number of audit events and the time spent
in the hook, next to the session's wall time.

Standard library only, so the plan job's unit tests can load it.
"""

import json
import os
import sys
import time

ROOT = os.path.realpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
PRIVATE = "ee/cloud/"
SKIPPED = (".venv/", ".git/", ".pytest_cache/")
TITLE = "Migrate inputs"


class Recorder:
    def __init__(self, root=ROOT):
        self.root = root.rstrip(os.sep) + os.sep
        self.phase = "setup"
        self.paths = {
            phase: {"opened": set(), "listed": set()} for phase in ("setup", "test")
        }
        self.events = 0
        self.busy = False
        self.errors = 0
        self.hook_seconds = 0.0
        self.started = time.perf_counter()
        self.setup_seconds = None
        self.imported = {"setup": set(), "test": set()}

    def relative(self, path):
        """The repo-relative path, or None outside the backend tree."""
        if isinstance(path, bytes):
            path = os.fsdecode(path)
        elif not isinstance(path, str):
            path = os.fspath(path)
        full = os.path.realpath(os.path.abspath(path))
        if not full.startswith(self.root):
            return None
        rel = full[len(self.root) :].replace(os.sep, "/")
        head, cache, name = rel.rpartition("__pycache__/")
        # x.cpython-312.pyc, pytest's x.cpython-312-pytest-9.0.2.pyc, and the
        # x.cpython-312.pyc.<n> temp file Python writes and renames over it.
        if cache and ".pyc" in name:
            rel = head + name.split(".", 1)[0] + ".py"
        if rel.startswith(SKIPPED):
            return None
        return rel

    def hook(self, event, args):
        # Timed for every event, not just the ones it records, so hook_seconds
        # is an upper bound on what the recorder costs the migrate.
        began = time.perf_counter()
        self.events += 1
        if self.busy or (
            event != "open" and event != "os.listdir" and event != "os.scandir"
        ):
            self.hook_seconds += time.perf_counter() - began
            return
        self.busy = True
        try:
            path = args[0]
            if path is None or isinstance(path, int):
                return
            rel = self.relative(path)
            if rel is not None:
                kind = "opened" if event == "open" else "listed"
                self.paths[self.phase][kind].add(rel)
        except Exception:  # noqa: BLE001 - a report must never break the migrate it observes
            self.errors += 1  # reported in cost.hook_errors
        finally:
            self.busy = False
            self.hook_seconds += time.perf_counter() - began

    def modules(self):
        found = set()
        for module in list(sys.modules.values()):
            path = getattr(module, "__file__", None)
            if not path:
                continue
            try:
                rel = self.relative(path)
            except Exception:  # noqa: BLE001 - an odd __file__ is skipped, not fatal
                self.errors += 1  # reported in cost.hook_errors
                continue
            if rel is not None:
                found.add(rel)
        return found

    def end_setup(self):
        if self.phase != "setup":
            return
        self.imported["setup"] = self.modules()
        self.setup_seconds = time.perf_counter() - self.started
        self.phase = "test"

    def report(self):
        if self.phase == "setup":
            self.end_setup()
        self.imported["test"] = self.modules() - self.imported["setup"]
        setup = self.paths["setup"]
        files = self.imported["setup"] | setup["opened"]

        def public(paths):
            return sorted(path for path in paths if not path.startswith(PRIVATE))

        def private(paths):
            return sum(1 for path in paths if path.startswith(PRIVATE))

        return {
            "schema": 1,
            "root": "futureagi",
            "setup": {
                "imported": public(self.imported["setup"]),
                "opened": public(setup["opened"] - self.imported["setup"]),
                "listed": public(setup["listed"]),
                "private_overlay_counts": {
                    "files": private(files),
                    "listed": private(setup["listed"]),
                },
            },
            "test_only_counts": {
                "imported": len(self.imported["test"]),
                "opened": len(self.paths["test"]["opened"] - files),
            },
            "cost": {
                "audit_events": self.events,
                "hook_errors": self.errors,
                "hook_seconds": round(self.hook_seconds, 3),
                "setup_seconds": round(self.setup_seconds or 0.0, 3),
                "session_seconds": round(time.perf_counter() - self.started, 3),
            },
        }


def summary(report):
    setup = report["setup"]
    tops = {}
    for path in setup["imported"] + setup["opened"]:
        top = path.split("/", 1)[0] if "/" in path else "(root)"
        tops[top] = tops.get(top, 0) + 1
    cost = report["cost"]
    lines = [
        "### Files a fresh migrate touched (observation only)\n",
        f"- {len(setup['imported'])} modules imported, {len(setup['opened'])} other files opened, "
        f"{len(setup['listed'])} directories listed",
        f"- private overlay: {setup['private_overlay_counts']['files']} files, "
        f"{setup['private_overlay_counts']['listed']} directories (counted, not listed)",
        f"- recorder cost: {cost['audit_events']} events, {cost['hook_seconds']} s in the hook; "
        f"setup {cost['setup_seconds']} s, session {cost['session_seconds']} s",
        "- by top-level directory: "
        + ", ".join(
            f"{top} {count}"
            for top, count in sorted(tops.items(), key=lambda kv: (-kv[1], kv[0]))
        ),
    ]
    return "\n".join(lines) + "\n"


def warn(message):
    sys.stdout.write(f"::warning title={TITLE}::{message}\n")


RECORDER = None
if os.environ.get("CI_MIGRATE_INPUTS"):
    try:
        RECORDER = Recorder()
        sys.addaudithook(RECORDER.hook)
    except Exception as exc:  # noqa: BLE001 - observation only
        RECORDER = None
        warn(f"recorder not installed: {exc}")


def pytest_runtest_call(item):
    if RECORDER is not None:
        try:
            RECORDER.end_setup()
        except Exception as exc:  # noqa: BLE001 - observation only
            warn(f"could not close the setup phase: {exc}")


def pytest_sessionfinish(session, exitstatus):
    if RECORDER is None:
        return
    try:
        report = RECORDER.report()
        target = os.environ["CI_MIGRATE_INPUTS"]
        os.makedirs(target, exist_ok=True)
        with open(
            os.path.join(target, "migrate-inputs.json"), "w", encoding="utf-8"
        ) as out:
            json.dump(report, out, indent=1, sort_keys=True)
            out.write("\n")
        if path := os.environ.get("GITHUB_STEP_SUMMARY"):
            with open(path, "a", encoding="utf-8") as out:
                out.write(summary(report))
    except Exception as exc:  # noqa: BLE001 - observation only
        warn(f"could not write the report: {exc}")
