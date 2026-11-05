"""Updater (deploy/update/apply.py): apply, rollback and recover, without Docker.

A fake Installation keeps a small model of one Compose project: the images
the services resolve to (read from the env file on disk, the way Compose
interpolates `${VAR:-default}`), a local image store, a registry a pull
copies from, and the running containers. It records every call in order,
together with the journal writes, env file writes and receipts, so a test
can assert the exact sequence. The env file, the state directory, the
journal and the receipts are real files in a temporary checkout.
"""

from __future__ import annotations

import errno
import hashlib
import io
import json
import os
import re
import shlex
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import unicodedata
from collections import namedtuple
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from deploy.update import apply as apply_module  # noqa: E402
from deploy.update import envfile as envfile_module  # noqa: E402
from deploy.update.apply import (  # noqa: E402
    ROLLBACKABLE, STAGES_BEFORE_ROLLOUT, Blocked, FailedBeforeRollout, FailedDuringRollout, Interrupted,
    InterruptedRun, UpdateError, Updater, run_hook, signals_as_interrupt)
from deploy.update.envfile import EnvFileError, read_env  # noqa: E402
from deploy.update.install import (  # noqa: E402
    COMPONENT_VARIABLES, VERSION_VARIABLES, CommandResult, InstallError, Installation, component_for)
from deploy.update.manifest import parse_image_ref  # noqa: E402
from deploy.update.plan import BLOCKED, UP_TO_DATE, UPDATE_AVAILABLE, jobs_text, plan_text  # noqa: E402
from deploy.update.readiness import NotReady  # noqa: E402
from deploy.update.receipt import RECEIPT_SCHEMA, StateDir, StateError, secret_values  # noqa: E402
from deploy.update.settings import load_settings  # noqa: E402
from deploy.update.plan import build_plan  # noqa: E402
from deploy.update.trust import Verification  # noqa: E402
from deploy.update import receipt as receipt_module  # noqa: E402

PROJECT = "fagi-test"
SECRET = "Zx9-super-secret-key-value-0042"
PG_SECRET = "pg-hunter2-password"
BACKUP = "/opt/backup/run --all"
TARGET = "v1.1.0"
VERSIONS = ("v1.0.0", "v1.1.0", "v1.2.0")
POSTGRES_ID = "sha256:" + "7" * 64
TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

Group = namedtuple("Group", "component variable services repo default")
STANDALONE = (Group("standalone", "FUTURE_AGI_VERSION", ("app",), "futureagi/standalone", "latest"),)
BACKEND = Group("backend", "FUTURE_AGI_VERSION", ("backend", "worker"), "futureagi/future-agi", "latest")
FRONTEND = Group("frontend", "FRONTEND_VERSION", ("frontend",), "futureagi/frontend", "latest")
FI_COLLECTOR = Group("fi_collector", "FI_COLLECTOR_VERSION", ("fi-collector",), "futureagi/fi-collector", "local")
SERVING_GPU = Group("serving_gpu", "SERVING_VERSION", ("serving",), "futureagi/serving", "latest")
DISTRIBUTED = (BACKEND, FRONTEND)


def _hex(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def digest_of(repo, tag):
    return "sha256:" + _hex(f"registry digest {repo}:{tag}")


def id_of(repo, tag):
    return "sha256:" + _hex(f"image id {repo}:{tag}")


def suffix(group):
    return "-gpu" if group.component == "serving_gpu" else ""


# ------------------------------------------------------------------ fakes --

class FakeInstallation(Installation):
    """Installation whose Docker side is an in-memory model (no __init__ probing)."""

    def __init__(self, root, env_file, log, groups, *, mode, production=False, project=PROJECT):  # noqa: D107
        self.root = Path(root).resolve()
        self.production = production
        self.mode = mode
        self.env_file = Path(env_file)
        self.environ = {}
        self.docker = ["docker"]
        self.compose = ["docker", "compose"]
        self.runner = None
        self._config = None
        self._project = project
        self.log = log
        self.specs = [(service, g.repo, g.variable, g.default) for g in groups for service in g.services]
        self.env = read_env(self.env_file)
        self.env_overlay = {}       # Installation.overlay_env (a dry run after a killed run uses it)
        self.image_overrides = {}   # service -> image a compose override forces
        self.local_images = {}      # reference -> image id (the host's image store)
        self.metadata = {}          # image id -> {"repo_digests", "version"}
        self.registry = {}          # reference -> image id a pull fetches
        self.running = {}           # service -> {"image", "image_id"}
        self.extra_containers = []
        self.hidden = set()         # references `docker image inspect` cannot find
        self.failures = {}          # compose verb -> exception, or callable run first
        self.up_skip = set()        # services `up` does not recreate
        self.third_party = {"postgres": "postgres:16"}  # services whose image is not a Future AGI one
        self.up_pull_never = False  # what supports_up_pull_never() answers
        self.profile_services = {}  # service -> raw image of a Compose profile that is not enabled
        self.model_complete = True  # service_model()["complete"]: every profile's services could be resolved

    @property
    def project(self):
        return self._project

    def check_ownership(self):
        # This fake owns its entire in-memory project. Real provenance is tested through Installation/CLI.
        pass

    def resolved_images(self):
        env = read_env(self.env_file)  # Compose reads the file on disk
        images = {}
        for service, repo, variable, default in self.specs:
            value = env.get(variable) or default
            reference = re.fullmatch(r"\$\{(\w+)\}", value)  # Compose interpolates FUTURE_AGI_VERSION=${RELEASE}
            if reference:
                value = env.get(reference.group(1)) or default
            images[service] = self.image_overrides.get(service) or f"{repo}:{value}"
        return images

    def third_party_image(self, service):
        spec = self.third_party[service]
        return spec["image"] if isinstance(spec, dict) else spec

    def config(self, refresh=False):
        self.log.append(("config", refresh))
        services = {name: {"image": image} for name, image in self.resolved_images().items()}
        services.update({name: dict(spec) if isinstance(spec, dict) else {"image": spec}
                         for name, spec in self.third_party.items()})
        return {"name": self._project, "services": services}

    def service_model(self, refresh=False):
        # Installation.service_model: every service of every profile, whether it is active, and the version key
        # its image follows (None when a compose file sets the tag). A service of a disabled profile shows the
        # image of Compose's probe, every version key set to bin-update-probe-<KEY>.
        resolved = self.resolved_images()
        services = {}
        for service, _repo, variable, _default in self.specs:
            services[service] = {"image": resolved[service], "active": True, "buildable": False,
                                 "variable": None if service in self.image_overrides else variable}
        for name, spec in self.third_party.items():
            services[name] = {"image": self.third_party_image(name), "variable": None, "active": True,
                              "buildable": isinstance(spec, dict) and "build" in spec}
        for name, raw in self.profile_services.items():
            keys = [key for key in re.findall(r"\$\{(\w+)", raw) if key in VERSION_VARIABLES]
            image = re.sub(r"\$\{(\w+)[^}]*\}", lambda m: f"bin-update-probe-{m.group(1)}", raw)
            services[name] = {"image": image, "variable": keys[0] if keys else None, "active": False,
                              "buildable": False}
        return {"services": services, "complete": self.model_complete}

    def supports_up_pull_never(self):
        return self.up_pull_never

    def futureagi_services(self, config=None):
        self.log.append(("futureagi_services",))
        return Installation.futureagi_services(self, config)

    def docker_run(self, *args, check=True):
        # `docker tag <image id> <reference>` (a rollback adopting the image that ran under another tag) is the
        # only docker command the updater runs itself; any other still fails the test through run().
        self.log.append(("docker", tuple(args)))
        failure = self.failures.get(f"docker {args[0]}")
        if failure is not None:
            raise failure
        if args[0] == "tag":
            self.local_images[args[2]] = args[1]
            return CommandResult(["docker", *args], 0, "", "")
        return Installation.docker_run(self, *args, check=check)

    def run(self, args, *, check=True, stream=False, timeout=None):
        self.log.append(("run", tuple(args)))
        raise AssertionError(f"the updater ran a raw command: {args}")

    def compose_run(self, *args, check=True, stream=False, timeout=None):
        self.log.append(("compose", tuple(args), stream))
        verb = args[0]
        failure = self.failures.get(verb)
        if failure is not None:
            if isinstance(failure, BaseException):
                raise failure
            failure(*args)
        if verb == "pull":
            resolved = self.resolved_images()
            for service in args[1:]:
                reference = resolved.get(service) or self.third_party_image(service)
                if reference not in self.registry:
                    raise InstallError(f"`docker compose pull {service}` failed with exit code 18: {reference} not found")
                self.local_images[reference] = self.registry[reference]
        elif verb == "up":
            resolved = self.resolved_images()
            for service, _repo, _variable, _default in self.specs:
                if service in self.up_skip:
                    continue
                image_id = self.local_images.get(resolved[service])
                if image_id is None:
                    raise InstallError(f"`docker compose up` failed: no such image {resolved[service]}")
                self.running[service] = {"image": resolved[service], "image_id": image_id}
        return CommandResult(self.compose_args(*args), 0, "", "")

    def containers(self):
        self.log.append(("containers",))
        listing = []
        for service, info in sorted(self.running.items()):
            listing.append({
                "id": f"c-{service}", "service": service, "image_id": info["image_id"], "image": info["image"],
                "status": "running", "exit_code": 0, "restart_count": 0, "health": "healthy",
                "started_at": "2026-10-19T00:00:00Z", "command": [],
            })
        listing.extend(dict(c) for c in self.extra_containers)
        return listing

    def image(self, reference):
        self.log.append(("image", reference))
        if reference in self.hidden:
            return None
        image_id = self.local_images.get(reference)
        present = set(self.local_images.values()) | {c["image_id"] for c in self.running.values()}
        if image_id is None and reference in present:
            image_id = reference
        if image_id is None or image_id not in self.metadata:
            return None
        info = self.metadata[image_id]
        return {"id": image_id, "repo_digests": list(info["repo_digests"]), "version": info["version"],
                "revision": ""}


class FakeReadiness:
    def __init__(self, log):
        self.log = log
        self.failure = None
        self.previous_ids = []  # one list per wait_standalone call

    def _wait(self, name):
        self.log.append((name,))
        if self.failure is None:
            return
        if isinstance(self.failure, BaseException):
            raise self.failure
        self.failure()

    def wait_standalone(self, previous_ids=()):
        self.previous_ids.append(list(previous_ids))
        self._wait("wait_standalone")

    def wait_api(self):
        self._wait("wait_api")


class FakeHook:
    def __init__(self, log, env_path, journal_path):
        self.log = log
        self.env_path = env_path
        self.journal_path = journal_path
        self.calls = []
        self.observed = []
        self.returncode = 0
        self.raises = None

    def __call__(self, argv, **kwargs):
        self.calls.append((list(argv), dict(kwargs)))
        self.log.append(("backup", list(argv)))
        self.observed.append({"env": self.env_path.read_bytes(), "journal": self.journal_path.exists(),
                              "sigterm": signal.getsignal(signal.SIGTERM)})
        if self.raises is not None:
            raise self.raises
        return subprocess.CompletedProcess(list(argv), self.returncode)


class RecordingStateDir(StateDir):
    """The real StateDir, plus a record of journal writes (with the env file at that moment)."""

    def __init__(self, root, secrets, log, env_path):
        super().__init__(root, secrets)
        self.log = log
        self.env_path = env_path
        self.journal_failures = {}
        self.journal_texts = []

    def write_journal(self, record):
        failure = self.journal_failures.get(record.get("stage"))
        if failure is not None:
            raise failure
        self.log.append(("journal", record.get("stage"), self.env_path.read_bytes()))
        super().write_journal(record)
        self.journal_texts.append(self.journal_path.read_text(encoding="utf-8"))

    def write_receipt(self, record):
        path = super().write_receipt(record)
        self.log.append(("receipt", record.get("status")))
        return path

    def clear_journal(self):
        self.log.append(("clear_journal",))
        super().clear_journal()


# ------------------------------------------------------------------- base --

class UpdaterCase(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.log = []
        self.env_write_failures = {}
        self.env_write_count = 0
        self.env_write_kwargs = []
        real_write = envfile_module.write_changes
        self.real_write = real_write

        def recording_write(path, changes, **kwargs):
            index = self.env_write_count
            self.env_write_count += 1
            self.log.append(("env_write", dict(changes)))
            self.env_write_kwargs.append(dict(kwargs))
            failure = self.env_write_failures.get(index)
            if failure is not None:
                raise failure
            return real_write(path, changes, **kwargs)

        patcher = mock.patch.object(envfile_module, "write_changes", recording_write)
        patcher.start()
        self.addCleanup(patcher.stop)
        real_restore = envfile_module.restore_changes

        def recording_restore(path, record):
            index = self.env_write_count
            self.env_write_count += 1
            self.log.append(('env_restore',))
            failure = self.env_write_failures.get(index)
            if failure is not None:
                raise failure
            return real_restore(path, record)

        patcher = mock.patch.object(envfile_module, 'restore_changes', recording_restore)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.build()

    # ---------------------------------------------------------- world --
    def build(self, mode="standalone", groups=None, current="v1.0.0", values=None, env_text=None,
              project=PROJECT):
        self.mode = mode
        self.current = current
        self.groups = tuple(groups or (STANDALONE if mode == "standalone" else DISTRIBUTED))
        production = mode == "production"
        if production:
            (self.root / "deploy").mkdir(exist_ok=True)
            self.env_path = self.root / "deploy" / ".env.production"
        else:
            self.env_path = self.root / ".env"
        if values is None:
            values = {}
            for g in self.groups:
                values.setdefault(g.variable, current + suffix(g))
        self.values = dict(values)
        text = env_text if env_text is not None else self.render_env(self.values)
        if self.env_path.is_symlink():
            self.env_path.unlink()
        self.env_path.write_bytes(text.encode("utf-8"))
        self.original = self.env_path.read_bytes()
        self.log = []
        self.env_write_count = 0
        self.plans_seen = []
        self.histories_seen = []
        self.inst = FakeInstallation(self.root, self.env_path, self.log, self.groups, mode=mode,
                                     production=production, project=project)
        for g in self.groups:
            for version in VERSIONS:
                self.publish(g.repo, version + suffix(g), version)
            tag = read_env(self.env_path).get(g.variable) or g.default
            if tag == "local":
                image_id = self.local_image(g.repo)
            elif tag == "latest":
                image_id = id_of(g.repo, current + suffix(g))
                self.inst.local_images[f"{g.repo}:latest"] = image_id
            else:
                image_id = self.inst.registry.get(f"{g.repo}:{tag}") or self.publish(g.repo, tag, tag)
                self.inst.local_images[f"{g.repo}:{tag}"] = image_id
            for service in g.services:
                self.inst.running[service] = {"image": f"{g.repo}:{tag}", "image_id": image_id}
        self.inst.local_images["postgres:16"] = POSTGRES_ID  # third-party images are on the host already
        self.inst.metadata[POSTGRES_ID] = {"repo_digests": [f"postgres@sha256:{'7' * 64}"], "version": ""}
        self.ready = FakeReadiness(self.log)
        self.state = RecordingStateDir(self.root, secret_values(read_env(self.env_path)), self.log, self.env_path)
        self.hook = FakeHook(self.log, self.env_path, self.state.journal_path)
        self.out = io.StringIO()

    def render_env(self, values):
        lines = ["# Future AGI configuration written by bin/install", f"SECRET_KEY={SECRET}",
                 f"PG_PASSWORD='{PG_SECRET}'"]
        if self.mode == "distributed":
            lines.append("COMPOSE_FILE=docker-compose.distributed.yml")
        lines.append("")
        for variable, value in values.items():
            if value is not None:
                lines.append(f"{variable}={value}")
        lines += ["INSTALL_READY_TIMEOUT_SECONDS=600", "  # an indented comment the updater must keep",
                  'export EXTRA_FLAG="keep me # not a comment"', ""]
        return "\n".join(lines)

    def publish(self, repo, tag, version, *, digest=None, image_id=None, names=None):
        image_id = image_id or id_of(repo, tag)
        digest = digest or digest_of(repo, tag)
        self.inst.metadata[image_id] = {"repo_digests": [f"{n}@{digest}" for n in (names or [repo])],
                                        "version": version}
        self.inst.registry[f"{repo}:{tag}"] = image_id
        return image_id

    def prune(self, version):
        """Drop the host's tags of `version`'s images (`docker image rm`): a run that needs them must pull them.

        A run pulls only the images that are not on the host with the expected
        digest yet, so a test whose pull fails takes them off the host first.
        """

        for g in self.groups:
            self.inst.local_images.pop(f"{g.repo}:{version}{suffix(g)}", None)

    def local_image(self, repo):
        image_id = id_of(repo, "local")
        self.inst.metadata[image_id] = {"repo_digests": [], "version": ""}
        self.inst.local_images[f"{repo}:local"] = image_id
        return image_id

    def snapshot_of(self, version, groups=None):
        images = {}
        for g in groups or self.groups:
            tag = version + suffix(g)
            for service in g.services:
                images[service] = {"image": f"{g.repo}:{tag}", "image_id": id_of(g.repo, tag),
                                   "repo_digests": [f"{g.repo}@{digest_of(g.repo, tag)}"], "version": version}
        return images

    # ----------------------------------------------------------- plan --
    def current_of(self, g):
        tag = self.values.get(g.variable) or g.default
        if tag == "local":
            return None
        if tag == "latest":
            return self.current
        return tag[: -len(suffix(g))] if suffix(g) and tag.endswith(suffix(g)) else tag

    def plan(self, target=TARGET, decision=UPDATE_AVAILABLE, env_changes=None, reasons=(), resume=False,
             current=None, action="update"):
        items = []
        for g in self.groups:
            tag = target + suffix(g)
            items.append({
                "component": g.component, "variable": g.variable, "services": list(g.services),
                "configured": self.values.get(g.variable) or "", "current_version": self.current_of(g),
                "current_source": "test", "current_tags": [self.values.get(g.variable) or g.default],
                "target_tag": tag, "target_image": f"{g.repo}:{tag}", "target_digest": digest_of(g.repo, tag),
                "action": action,
            })
        if env_changes is None:
            env_changes = {g.variable: target + suffix(g) for g in self.groups}
        if current is None:
            current = sorted({v for v in (self.current_of(g) for g in self.groups) if v})
        return {
            "schema_version": 2, "decision": decision, "resume": resume, "target_version": target,
            "channel": "stable", "published_at": "2026-10-19T00:00:00Z", "minimum_version": "v1.0.0",
            "policy": "minor", "manifest_hash": "f" * 64, "manifest_source": "/srv/manifest.json",
            "installation": {"mode": self.inst.mode, "project": self.inst.project,
                             "env_file": self.inst.relative(self.inst.env_file), "root": str(self.inst.root)},
            "current_versions": list(current),
            "signature": {"verified": True, "method": "keyless", "signer": "release-update-manifest.yml",
                          "issuer": "https://token.actions.githubusercontent.com", "tool": "cosign", "error": ""},
            "components": items, "env_changes": dict(env_changes), "deployment_drift": [],
            "missing_env_keys": [], "reasons": list(reasons), "notes": [],
        }

    # -------------------------------------------------------- running --
    def updater(self, **settings):
        loaded = load_settings(self.root, settings or None, environ={})
        return Updater(self.inst, loaded, self.state, out=self.out, readiness=self.ready, hook_runner=self.hook)

    def apply(self, plan=None, *, updater=None, backup_command=BACKUP, allow_no_backup=False, dry_run=False):
        plan = self.plan() if plan is None else plan

        def make_plan(history):
            # The newest receipt of the history apply plans with (None without one), and that history.
            self.plans_seen.append(history[-1] if history else None)
            self.histories_seen.append(list(history))
            return plan

        return (updater or self.updater()).apply(make_plan, {"version": plan["target_version"]},
                                                 allow_no_backup=allow_no_backup,
                                                 backup_command=backup_command, dry_run=dry_run)

    def rollback(self, receipt=None, *, yes=True, updater=None):
        return (updater or self.updater()).rollback(receipt, yes=yes)

    def applied(self, plan=None):
        """A successful apply; the log starts empty afterwards."""

        path = self.apply(plan)
        del self.log[:]
        return path

    def assertRaisesExactly(self, cls, func, *args, **kwargs):
        with self.assertRaises(BaseException) as ctx:
            func(*args, **kwargs)
        self.assertIs(type(ctx.exception), cls, f"{type(ctx.exception).__name__}: {ctx.exception}")
        return ctx.exception

    # -------------------------------------------------------- reading --
    def env_bytes(self):
        return self.env_path.read_bytes()

    def env_value(self, key):
        return read_env(self.env_path).get(key)

    def with_versions(self, mapping, base=None):
        text = (base if base is not None else self.original).decode("utf-8")
        for key, value in mapping.items():
            text, count = re.subn(rf"(?m)^{key}=.*$", f"{key}={value}", text)
            self.assertEqual(count, 1, key)
        return text.encode("utf-8")

    def receipts(self):
        return [json.loads(p.read_text(encoding="utf-8")) for p in self.state.receipts()]

    def last_receipt(self):
        receipts = self.receipts()
        self.assertTrue(receipts, "no receipt was written")
        return receipts[-1]

    def events(self):
        out = []
        for entry in self.log:
            if entry[0] == "journal":
                out.append(("journal", entry[1]))
            elif entry[0] == "backup":
                out.append(("backup",))
            else:
                out.append(entry)
        return out

    def compose_calls(self):
        return [entry[1] for entry in self.log if entry[0] == "compose"]

    def journal(self):
        return self.state.read_journal()

    def state_text(self):
        return "".join(p.read_text(encoding="utf-8", errors="replace")
                       for p in sorted(self.state.path.iterdir()) if p.is_file())

    def craft_apply(self, *, previous="v1.0.0", target=TARGET, status="succeeded", previous_values=None,
                    previous_images=None, groups=None, **fields):
        groups = groups or self.groups
        record = {
            "kind": "apply", "status": status, "stage": "rollout", "mode": self.inst.mode,
            "production": self.inst.production, "env_file": self.inst.relative(self.inst.env_file),
            "project": self.inst.project, "target_version": target, "from_versions": [previous],
            "components": [{"component": g.component, "variable": g.variable, "services": list(g.services),
                            "target_tag": target + suffix(g), "target_digest": digest_of(g.repo, target + suffix(g)),
                            "current_version": previous} for g in groups],
            "env_changes": {g.variable: target + suffix(g) for g in groups},
            "previous_values": (previous_values if previous_values is not None
                                else {g.variable: previous + suffix(g) for g in groups}),
            "previous_images": previous_images if previous_images is not None else self.snapshot_of(previous, groups),
            "started_at": "2026-10-19T00:00:00Z", "finished_at": "2026-10-19T00:01:00Z",
        }
        record.update(fields)
        path = self.state.write_receipt(record)
        del self.log[:]
        return path

    def craft_rollback(self, rolls_back, status="rolled_back", **fields):
        record = {"kind": "rollback", "status": status, "stage": "rollout", "mode": self.inst.mode,
                  "production": self.inst.production, "env_file": self.inst.relative(self.inst.env_file),
                  "project": self.inst.project, "rolls_back": str(Path(rolls_back).resolve()),
                  "from_version": TARGET, "target_version": "v1.0.0", "components": [], "env_changes": {},
                  "previous_values": {}, "previous_images": {}}
        record.update(fields)
        path = self.state.write_receipt(record)
        del self.log[:]
        return path

    def craft_journal(self, stage, **fields):
        record = {"kind": "apply", "status": "running", "stage": stage, "mode": self.inst.mode,
                  "production": self.inst.production, "env_file": self.inst.relative(self.inst.env_file),
                  "project": self.inst.project, "target_version": TARGET,
                  "env_changes": {g.variable: TARGET + suffix(g) for g in self.groups},
                  "previous_values": {g.variable: "v1.0.0" + suffix(g) for g in self.groups},
                  "previous_images": self.snapshot_of("v1.0.0"), "components": [],
                  "started_at": "2026-10-19T00:00:00Z"}
        record.update(fields)
        if stage in STAGES_BEFORE_ROLLOUT and 'env_recovery' not in fields:
            values = record.get('found_values', record['previous_values'])
            try:
                before = envfile_module.render_changes(self.env_bytes().decode('utf-8'), values, verbatim=True)
            except EnvFileError:
                pass  # Explicit malformed/legacy fixture: there is no safe inverse to invent.
            else:
                fixture = self.root / 'journal-before.env'
                fixture.write_bytes(before.encode())
                fixture.chmod(self.env_path.stat().st_mode & 0o7777)
                changes = {key: value for key, value in record['env_changes'].items() if key in values}
                record['env_recovery'] = envfile_module.recovery_record(fixture, changes)
                record['env_recovery']['target'] = os.path.realpath(self.env_path)
                record['env_recovery'].pop('commit')  # Synthetic legacy journal: no physical exchange occurred.
                fixture.unlink()
        StateDir.write_journal(self.state, record)
        return record

    def sentinel_handlers(self):
        calls = []

        def sentinel(signum, frame):  # noqa: ARG001
            calls.append(signum)

        for sig in (signal.SIGTERM, signal.SIGHUP):
            previous = signal.signal(sig, sentinel)
            self.addCleanup(signal.signal, sig, previous)
        return sentinel, calls


def deliver(signum):
    """Send `signum` to this process and give Python the chance to run its handler."""

    os.kill(os.getpid(), signum)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        time.sleep(0.01)
    raise AssertionError(f"signal {signum} was not turned into an exception")


# ================================================================ tests ==

class CrashRecoveryPreservationTests(UpdaterCase):
    """A killed Updater must undo bytes, not re-encode last-definition-wins values."""

    def interrupt_apply(self, plan=None):
        self.inst.failures["config"] = SystemExit(137)  # outside Exception: no in-process cleanup
        with self.assertRaises(SystemExit):
            self.apply(plan)
        del self.inst.failures["config"]
        self.assertEqual(self.journal()["stage"], "env_written")
        self.assertFalse(any(call[0] == "up" for call in self.compose_calls()))

    def test_old_unicode_tables_do_not_rewrite_inside_unrelated_multiline_values(self):
        for char in ("ࡰ", "\U0001e030", "\U00031350"):
            with self.subTest(char=char):
                text = f'FUTURE_AGI_VERSION=v1.0.0\n{char}="first\nFUTURE_AGI_VERSION=v0.0.1\nlast"\n'
                self.build(env_text=text)
                with mock.patch.object(envfile_module, "unicodedata", unicodedata.ucd_3_2_0):
                    self.interrupt_apply()
                    self.assertEqual(self.env_bytes(), text.replace("=v1.0.0", "=v1.1.0").encode())
                    self.updater().recover()
                    self.assertEqual(read_env(self.env_path)["FUTURE_AGI_VERSION"], "v1.0.0")
                self.assertEqual(self.env_bytes(), text.encode())

    def test_killed_apply_restores_duplicate_definitions_and_intervening_interpolation(self):
        self.build(env_text=(f'﻿SECRET_KEY={SECRET}\r\n'
                             'export FUTURE_AGI_VERSION : "v0.9.0"  # first\r\n'
                             'MIDDLE=${FUTURE_AGI_VERSION}\r\n'
                             '  FUTURE_AGI_VERSION= v1.0.0 # last'))
        self.env_path.chmod(0o640)
        self.interrupt_apply()
        recovered = self.updater().recover()
        self.assertEqual(recovered["status"], "interrupted_before_rollout")
        self.assertEqual(self.env_bytes(), self.original)
        self.assertEqual(stat.S_IMODE(self.env_path.stat().st_mode), 0o640)
        self.assertIsNone(self.journal())
        self.assertNotIn(SECRET, self.state_text())

    def test_killed_rollback_restores_escaped_dollars_without_interpolating_them(self):
        runner = "SIMULATION_RUNNER_VERSION"
        for token in ('"\\$X"', '"v\\${X"', "'literal$X'", '"first\\nsecond"'):
            with self.subTest(token=token):
                self.build(values={"FUTURE_AGI_VERSION": "v1.0.0", runner: "v1.0.0", "X": "v9"})
                applied = self.applied(self.plan(env_changes={"FUTURE_AGI_VERSION": TARGET, runner: TARGET}))
                # This inactive pin was edited after the update; rollback can still touch it.
                raw = self.env_bytes().replace(f"{runner}={TARGET}".encode(), f"{runner}={token}".encode())
                self.env_path.write_bytes(raw)
                self.inst.reload_env()
                self.inst.failures["config"] = SystemExit(137)
                with self.assertRaises(SystemExit):
                    self.rollback(applied)
                del self.inst.failures["config"]
                self.assertEqual((self.journal()["kind"], self.journal()["stage"]), ("rollback", "env_written"))
                recovered = self.updater().recover()
                self.assertEqual(recovered["status"], "interrupted_before_rollout")
                self.assertEqual(self.env_bytes(), raw)
                self.assertIsNone(self.journal())


    def test_unicode_tables_never_expose_a_multiline_value_to_apply(self):
        import unicodedata

        for char in ("ࡰ", "\U0001e030", "\U00031350"):
            with self.subTest(key=char), mock.patch.object(envfile_module, "unicodedata", unicodedata.ucd_3_2_0):
                text = f'FUTURE_AGI_VERSION=v1.0.0\n{char}="\nFUTURE_AGI_VERSION=v0.0.1\n"\n'
                self.build(env_text=text)
                self.apply()
                expected = text.replace("FUTURE_AGI_VERSION=v1.0.0", "FUTURE_AGI_VERSION=v1.1.0")
                self.assertEqual(self.env_bytes(), expected.encode())
                self.assertEqual(self.last_receipt()["previous_values"], {"FUTURE_AGI_VERSION": "v1.0.0"})


    def test_killed_rollback_restores_literal_escaped_dollars(self):
        receipt = self.applied()
        before = self.env_bytes().replace(b'FUTURE_AGI_VERSION=v1.1.0',
                                          b'X=v9\nFUTURE_AGI_VERSION="v\\${X"\n'
                                          b'MIDDLE=${FUTURE_AGI_VERSION}\n'
                                          b'FUTURE_AGI_VERSION="\\$X"')
        self.env_path.write_bytes(before)
        self.inst.reload_env()
        self.inst.failures["config"] = SystemExit(137)
        with self.assertRaises(SystemExit):
            self.rollback(receipt)
        del self.inst.failures["config"]
        self.assertEqual((self.journal()["kind"], self.journal()["stage"]), ("rollback", "env_written"))
        recovered = self.updater().recover()
        self.assertEqual(self.env_bytes(), before)
        self.assertEqual(recovered["status"], "interrupted_before_rollout")
        self.assertIsNone(self.journal())

class RecoveryTransactionInvariantTests(UpdaterCase):
    interrupt_apply = CrashRecoveryPreservationTests.interrupt_apply

    def test_mutating_entry_points_prove_ownership_under_lock_before_any_effect(self):
        self.interrupt_apply()
        before = (self.env_bytes(), self.state.journal_path.read_bytes(), self.receipts(), list(self.hook.calls))
        def refuse():
            self.assertIsNotNone(self.state.lock_holder())
            raise InstallError('foreign ownership')
        with mock.patch.object(self.inst, 'check_ownership', side_effect=refuse):
            for run in (lambda: self.apply(), lambda: self.rollback(yes=True), lambda: self.rollback(yes=False),
                        lambda: self.updater().recover()):
                with self.subTest(run=run), self.assertRaisesRegex(InstallError, 'ownership'):
                    run()
                self.assertTrue(self.state.journal_path.exists(), 'a refused recovery must retain its journal')
                self.assertEqual((self.env_bytes(), self.state.journal_path.read_bytes(), self.receipts(),
                                  list(self.hook.calls)), before)

    def test_fresh_apply_refuses_foreign_ownership_before_backup_and_journal(self):
        def refuse():
            self.assertIsNotNone(self.state.lock_holder())
            raise InstallError('foreign ownership')
        with mock.patch.object(self.inst, 'check_ownership', side_effect=refuse):
            with self.assertRaisesRegex(InstallError, 'ownership'):
                self.apply()
        self.assertEqual(self.hook.calls, [])
        self.assertEqual(self.env_bytes(), self.original)
        self.assertIsNone(self.journal())
        self.assertEqual(self.receipts(), [])

    def test_journal_project_identity_must_still_match_the_selected_project(self):
        self.interrupt_apply()
        self.inst._project = 'another-project'
        before = self.env_bytes(), self.state.journal_path.read_bytes()
        with self.assertRaisesRegex(Blocked, 'project'):
            self.updater().recover()
        self.assertEqual((self.env_bytes(), self.state.journal_path.read_bytes()), before)

    def test_no_journal_recover_never_contacts_docker_or_creates_state(self):
        with mock.patch.object(self.inst, 'check_ownership', side_effect=AssertionError('no daemon work')):
            self.assertIsNone(self.updater().recover())
        self.assertFalse(self.state.path.exists())

    def test_legacy_malformed_and_unknown_journals_are_kept_without_guessing(self):
        for journal in ({}, {'kind': 'apply'}, {'kind': 'apply', 'stage': 'future'},
                        {'kind': 'apply', 'stage': 'prepare', 'found_values': {'FUTURE_AGI_VERSION': 'v0.9.0'}},
                        {'kind': 'rollback', 'stage': 'pulled', 'previous_values': {'FUTURE_AGI_VERSION': None}},
                        {'kind': 'apply', 'stage': 'env_written', 'env_recovery': {'format': 2}}):
            with self.subTest(journal=journal):
                self.state.write_journal(journal)
                before = self.state.journal_path.read_bytes()
                with self.assertRaises(StateError):
                    self.updater().recover()
                self.assertEqual(self.env_bytes(), self.original)
                self.assertEqual(self.state.journal_path.read_bytes(), before)
                self.assertEqual(self.receipts(), [])

    def test_killed_recovery_after_env_rename_is_idempotent_and_durable_on_retry(self):
        self.build(env_text='FUTURE_AGI_VERSION=v0.9.0\nMIDDLE=${FUTURE_AGI_VERSION}\n'
                            'FUTURE_AGI_VERSION=v1.0.0')
        self.interrupt_apply()
        journal = self.state.journal_path.read_bytes()
        exchange = envfile_module.envcommit.exchange
        def killed(a, b, c, d):
            exchange(a, b, c, d)
            if b == 'slot':
                raise SystemExit(137)
        with mock.patch.object(envfile_module.envcommit, 'exchange', side_effect=killed):
            with self.assertRaises(SystemExit):
                self.updater().recover()
        self.assertEqual(self.env_bytes(), self.original)
        self.assertEqual(self.state.journal_path.read_bytes(), journal)
        self.assertEqual(self.receipts(), [])
        sync = envfile_module.envcommit.sync_dir
        with mock.patch.object(envfile_module.envcommit, 'sync_dir', wraps=sync) as synced:
            self.updater().recover()
        self.assertGreater(synced.call_count, 0)
        self.assertIsNone(self.journal())
        self.assertEqual(len(self.receipts()), 1)
        self.assertIsNone(self.updater().recover())

    def test_failed_journal_directory_fsync_never_allows_env_mutation(self):
        with mock.patch.object(receipt_module, '_fsync_dir', side_effect=OSError(28, 'No space left on device')):
            with self.assertRaises(StateError):
                self.apply()
        self.assertEqual(self.env_bytes(), self.original)
        self.assertEqual(self.env_write_count, 0)
        self.assertIsNotNone(self.journal())
        self.updater().recover()
        self.assertEqual(self.env_bytes(), self.original)
        self.assertIsNone(self.journal())

    def test_journal_replacement_and_parent_sync_precede_the_env_write(self):
        events = []
        replace, sync = os.replace, os.fsync
        def replaced(src, dst, **kwargs):
            replace(src, dst, **kwargs)
            events.append(('replace', str(dst)))
        def synced(fd):
            sync(fd)
            events.append(('sync-dir' if stat.S_ISDIR(os.fstat(fd).st_mode) else 'sync-file', None))
        exchange = envfile_module.envcommit.exchange
        def exchanged(a, b, c, d):
            events.append(('exchange', d))
            return exchange(a, b, c, d)
        with mock.patch.object(os, 'replace', side_effect=replaced), mock.patch.object(os, 'fsync', side_effect=synced), \
                mock.patch.object(envfile_module.envcommit, 'exchange', side_effect=exchanged):
            self.interrupt_apply()
        first_journal = events.index(('replace', str(self.state.journal_path)))
        first_env = events.index(('exchange', self.env_path.name))
        between = [kind for kind, _ in events[first_journal + 1:first_env]]
        self.assertEqual(between[:2], ['sync-dir', 'sync-dir'])
        self.assertIn('sync-file', between)
        self.assertIn(('replace', 'manifest.json'), events[first_journal + 1:first_env])
        record = self.journal()['env_recovery']
        self.assertEqual(record['before_sha256'], hashlib.sha256(self.original).hexdigest())
        self.assertEqual(record['after_sha256'], hashlib.sha256(self.env_bytes()).hexdigest())
        self.assertNotIn(SECRET, json.dumps(record))

    def test_receipts_never_publish_private_recovery_bytes(self):
        private = 'version-comment-private-value'
        self.build(env_text='SECRET_KEY=' + SECRET + '\nFUTURE_AGI_VERSION=v1.0.0 # ' + private + '\n')
        self.interrupt_apply()
        journal = self.journal()
        # Simulate a removed line's comment in private recovery metadata; StateDir must strip it regardless.
        journal['env_recovery']['private_test'] = private
        path = self.state.write_receipt(journal)
        text = path.read_text()
        self.assertNotIn('env_recovery', text)
        self.assertNotIn(private, text)
        self.assertNotIn(SECRET, self.state.journal_path.read_text())


class ExceptionContractTests(unittest.TestCase):
    def test_exit_codes(self):
        self.assertEqual(UpdateError.exit_code, 2)
        self.assertEqual(Blocked.exit_code, 3)
        self.assertEqual(FailedBeforeRollout.exit_code, 4)
        self.assertEqual(FailedDuringRollout.exit_code, 5)
        self.assertEqual(InterruptedRun.exit_code, 130)

    def test_hierarchy(self):
        for cls in (Blocked, FailedBeforeRollout, FailedDuringRollout, InterruptedRun):
            self.assertTrue(issubclass(cls, UpdateError))
        self.assertTrue(issubclass(UpdateError, RuntimeError))

    def test_interrupted_is_not_an_exception(self):
        # Code that catches Exception must not swallow SIGTERM/SIGHUP.
        self.assertTrue(issubclass(Interrupted, BaseException))
        self.assertFalse(issubclass(Interrupted, Exception))

    def test_stage_constants(self):
        self.assertEqual(STAGES_BEFORE_ROLLOUT, ("prepare", "env_written", "pulled"))
        self.assertEqual(ROLLBACKABLE, ("succeeded", "failed_during_rollout", "interrupted_during_rollout", "staged"))


class SignalsAsInterruptTests(unittest.TestCase):
    def setUp(self):
        self.calls = []

        def sentinel(signum, frame):  # noqa: ARG001
            self.calls.append(signum)

        self.sentinel = sentinel
        for sig in (signal.SIGTERM, signal.SIGHUP):
            previous = signal.signal(sig, sentinel)
            self.addCleanup(signal.signal, sig, previous)

    def test_sigterm_becomes_interrupted_and_handlers_come_back(self):
        with self.assertRaises(Interrupted) as ctx:
            with signals_as_interrupt():
                self.assertIsNot(signal.getsignal(signal.SIGTERM), self.sentinel)
                deliver(signal.SIGTERM)
        self.assertEqual(str(ctx.exception), "SIGTERM")
        self.assertIs(signal.getsignal(signal.SIGTERM), self.sentinel)
        self.assertIs(signal.getsignal(signal.SIGHUP), self.sentinel)
        self.assertEqual(self.calls, [])

    def test_sighup_becomes_interrupted(self):
        with self.assertRaises(Interrupted) as ctx:
            with signals_as_interrupt():
                deliver(signal.SIGHUP)
        self.assertEqual(str(ctx.exception), "SIGHUP")
        self.assertIs(signal.getsignal(signal.SIGHUP), self.sentinel)

    def test_handlers_restored_after_normal_exit(self):
        with signals_as_interrupt():
            self.assertIsNot(signal.getsignal(signal.SIGHUP), self.sentinel)
        self.assertIs(signal.getsignal(signal.SIGTERM), self.sentinel)
        self.assertIs(signal.getsignal(signal.SIGHUP), self.sentinel)

    def test_handlers_restored_after_other_exception(self):
        with self.assertRaises(ValueError):
            with signals_as_interrupt():
                raise ValueError("boom")
        self.assertIs(signal.getsignal(signal.SIGTERM), self.sentinel)

    def test_sigint_is_left_alone(self):
        before = signal.getsignal(signal.SIGINT)
        with signals_as_interrupt():
            self.assertIs(signal.getsignal(signal.SIGINT), before)


# ------------------------------------------------------------ apply: ok --

class ApplySuccessTests(UpdaterCase):
    def test_standalone_runs_every_step_in_order(self):
        self.apply()
        old_id = id_of("futureagi/standalone", "v1.0.0")
        self.assertEqual(self.events(), [
            # Round 5 (G4): the plan it prints says whether the release's images are on this host already.
            ("image", "futureagi/standalone:v1.1.0"),
            ("backup",),
            ("containers",),
            ("image", old_id),
            ("journal", "prepare"),
            ("env_write", {"FUTURE_AGI_VERSION": "v1.1.0"}),
            ("journal", "env_written"),
            ("compose", ("config", "--quiet"), False),
            ("config", True),
            ("futureagi_services",),
            ("image", "futureagi/standalone:v1.1.0"),  # not on the host yet: pulled
            ("compose", ("pull", "app"), True),
            ("image", "postgres:16"),  # present already: third-party images are only pulled when missing
            ("journal", "pulled"),
            ("image", "futureagi/standalone:v1.1.0"),
            ("journal", "rollout"),
            ("compose", ("up", "-d", "--no-build"), True),
            ("wait_standalone",),
            ("containers",),
            ("receipt", "succeeded"),
            ("clear_journal",),
        ])

    def test_journal_is_on_disk_before_the_env_file_changes(self):
        self.apply()
        journals = [e for e in self.log if e[0] == "journal"]
        self.assertEqual(journals[0][1], "prepare")
        self.assertEqual(journals[0][2], self.original)
        first_journal = self.log.index(journals[0])
        first_write = next(i for i, e in enumerate(self.log) if e[0] == "env_write")
        self.assertLess(first_journal, first_write)
        on_disk = json.loads(self.state.journal_texts[0])
        self.assertEqual(on_disk["stage"], "prepare")
        self.assertEqual(on_disk["status"], "running")
        self.assertEqual(on_disk["kind"], "apply")
        self.assertEqual(on_disk["previous_values"], {"FUTURE_AGI_VERSION": "v1.0.0"})
        self.assertEqual(on_disk["env_changes"], {"FUTURE_AGI_VERSION": "v1.1.0"})
        self.assertEqual(on_disk["env_file"], ".env")

    def test_later_journals_see_the_new_env_file(self):
        self.apply()
        expected = self.with_versions({"FUTURE_AGI_VERSION": "v1.1.0"})
        stages = [(e[1], e[2]) for e in self.log if e[0] == "journal"]
        self.assertEqual([s for s, _ in stages], ["prepare", "env_written", "pulled", "rollout"])
        for stage, content in stages[1:]:
            self.assertEqual(content, expected, stage)

    def test_rollout_journal_records_the_verified_image_ids(self):
        self.apply()
        rollout = json.loads(self.state.journal_texts[-1])
        self.assertEqual(rollout["stage"], "rollout")
        self.assertEqual(rollout["target_image_ids"], {"app": id_of("futureagi/standalone", "v1.1.0")})

    def test_only_the_version_key_changes(self):
        self.apply()
        self.assertEqual(self.env_bytes(), self.with_versions({"FUTURE_AGI_VERSION": "v1.1.0"}))
        self.assertEqual(self.inst.env["FUTURE_AGI_VERSION"], "v1.1.0")
        self.assertEqual(self.inst.env["SECRET_KEY"], SECRET)

    def test_env_file_mode_is_kept(self):
        os.chmod(self.env_path, 0o640)
        self.apply()
        self.assertEqual(stat.S_IMODE(self.env_path.stat().st_mode), 0o640)
        leftovers = [p.name for p in self.root.iterdir() if p.name.startswith(".env.futureagi-update-")]
        self.assertEqual(leftovers, [])

    def test_receipt_contents(self):
        path = self.apply()
        self.assertEqual(Path(path).parent, self.state.path)
        receipt = self.last_receipt()
        old, new = "v1.0.0", "v1.1.0"
        repo = "futureagi/standalone"
        self.assertEqual(receipt["schema_version"], RECEIPT_SCHEMA)
        self.assertEqual(receipt["kind"], "apply")
        self.assertEqual(receipt["status"], "succeeded")
        self.assertEqual(receipt["stage"], "rollout")
        self.assertEqual(receipt["mode"], "standalone")
        self.assertIs(receipt["production"], False)
        self.assertEqual(receipt["env_file"], ".env")
        self.assertEqual(receipt["project"], PROJECT)
        self.assertEqual(receipt["target_version"], new)
        self.assertEqual(receipt["from_versions"], [old])
        self.assertEqual(receipt["manifest_hash"], "f" * 64)
        self.assertEqual(receipt["manifest_source"], "/srv/manifest.json")
        self.assertEqual(receipt["signature"]["method"], "keyless")
        self.assertEqual(receipt["components"], [{
            "component": "standalone", "variable": "FUTURE_AGI_VERSION", "services": ["app"],
            "target_tag": new, "target_digest": digest_of(repo, new), "current_version": old,
            "current_tags": [old]}])
        self.assertEqual(receipt["env_changes"], {"FUTURE_AGI_VERSION": new})
        self.assertEqual(receipt["found_values"], {"FUTURE_AGI_VERSION": old})
        self.assertEqual(receipt["previous_values"], {"FUTURE_AGI_VERSION": old})
        self.assertEqual(receipt["previous_images"], {"app": {
            "image": f"{repo}:{old}", "image_id": id_of(repo, old),
            "repo_digests": [f"{repo}@{digest_of(repo, old)}"], "version": old}})
        self.assertEqual(receipt["target_image_ids"], {"app": id_of(repo, new)})
        self.assertEqual(receipt["backup"]["ran"], True)
        self.assertEqual(receipt["backup"]["command"], "/opt/backup/run")
        self.assertIsInstance(receipt["backup"]["seconds"], (int, float))
        self.assertRegex(receipt["started_at"], TIMESTAMP)
        self.assertRegex(receipt["finished_at"], TIMESTAMP)
        for absent in ("error", "resumes", "_path"):
            self.assertNotIn(absent, receipt)

    def test_journal_removed_and_containers_on_new_image(self):
        self.apply()
        self.assertIsNone(self.journal())
        self.assertFalse(self.state.journal_path.exists())
        self.assertEqual(self.inst.running["app"]["image_id"], id_of("futureagi/standalone", "v1.1.0"))

    def test_output_names_the_receipt(self):
        path = self.apply()
        text = self.out.getvalue()
        self.assertIn("Future AGI update check", text)
        self.assertIn("Running the backup hook: /opt/backup/run", text)
        self.assertIn("Pulling futureagi/standalone:v1.1.0", text)
        self.assertIn("Restarting the stack on the new images", text)
        self.assertIn(f"Updated to v1.1.0. Receipt: {path}", text)

    def test_state_directory_and_receipt_are_private(self):
        path = self.apply()
        self.assertEqual(stat.S_IMODE(self.state.path.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(Path(path).stat().st_mode), 0o600)

    def test_signal_handlers_active_during_run_and_restored_after(self):
        sentinel, calls = self.sentinel_handlers()
        seen = {}
        self.inst.failures["up"] = lambda *a: seen.update(term=signal.getsignal(signal.SIGTERM),
                                                           hup=signal.getsignal(signal.SIGHUP))
        self.apply()
        self.assertIsNot(seen["term"], sentinel)
        self.assertIsNot(seen["hup"], sentinel)
        self.assertIs(signal.getsignal(signal.SIGTERM), sentinel)
        self.assertIs(signal.getsignal(signal.SIGHUP), sentinel)
        self.assertEqual(calls, [])

    def test_backup_runs_outside_the_signal_conversion(self):
        sentinel, _ = self.sentinel_handlers()
        self.apply()
        self.assertIs(self.hook.observed[0]["sigterm"], sentinel)

    def test_non_futureagi_services_are_neither_pulled_nor_checked(self):
        self.apply()
        for call in self.compose_calls():
            self.assertNotIn("postgres", call)
        # Only looked up to see that it is on the host (it is): never verified against a digest.
        self.assertEqual(self.log.count(("image", "postgres:16")), 1)

    def test_latest_install_is_pinned_to_the_release(self):
        self.build(values={"FUTURE_AGI_VERSION": ""})
        self.apply()
        self.assertEqual(self.env_bytes(), self.with_versions({"FUTURE_AGI_VERSION": "v1.1.0"}))
        receipt = self.last_receipt()
        self.assertEqual(receipt["previous_values"], {"FUTURE_AGI_VERSION": ""})
        self.assertEqual(receipt["previous_images"]["app"]["image"], "futureagi/standalone:latest")
        self.assertEqual(receipt["previous_images"]["app"]["version"], "v1.0.0")

    def test_absent_key_is_added_and_recorded_as_unset(self):
        self.build(values={"FUTURE_AGI_VERSION": None})
        self.apply()
        self.assertEqual(self.env_bytes(), self.original + b"FUTURE_AGI_VERSION=v1.1.0\n")
        self.assertEqual(self.last_receipt()["previous_values"], {"FUTURE_AGI_VERSION": None})


class ApplyModeTests(UpdaterCase):
    def test_distributed_waits_with_compose_and_the_api(self):
        self.build("distributed")
        self.apply()
        calls = self.compose_calls()
        self.assertEqual(calls, [("config", "--quiet"), ("pull", "backend", "frontend", "worker"),
                                 ("up", "-d", "--no-build", "--wait", "--wait-timeout", "1200")])
        self.assertIn(("wait_api",), self.log)
        self.assertNotIn(("wait_standalone",), self.log)
        self.assertLess(self.log.index(("compose", calls[-1], True)), self.log.index(("wait_api",)))

    def test_standalone_never_uses_compose_wait(self):
        self.apply()
        up = [c for c in self.compose_calls() if c[0] == "up"]
        self.assertEqual(up, [("up", "-d", "--no-build")])
        self.assertNotIn(("wait_api",), self.log)

    def test_wait_timeout_setting_is_passed(self):
        for value in ("60", "300", "7200"):
            with self.subTest(value=value):
                self.build("distributed")
                self.apply(updater=self.updater(FUTURE_AGI_UPDATE_WAIT_TIMEOUT_SECONDS=value))
                self.assertIn(("up", "-d", "--no-build", "--wait", "--wait-timeout", value), self.compose_calls())

    def test_distributed_changes_every_version_key_only(self):
        self.build("distributed")
        self.apply()
        self.assertEqual(self.env_bytes(), self.with_versions({"FUTURE_AGI_VERSION": "v1.1.0",
                                                               "FRONTEND_VERSION": "v1.1.0"}))
        receipt = self.last_receipt()
        self.assertEqual(receipt["mode"], "distributed")
        self.assertEqual(sorted(receipt["previous_images"]), ["backend", "frontend", "worker"])
        self.assertEqual(receipt["target_image_ids"], {
            "backend": id_of("futureagi/future-agi", "v1.1.0"), "worker": id_of("futureagi/future-agi", "v1.1.0"),
            "frontend": id_of("futureagi/frontend", "v1.1.0")})

    def test_distributed_verifies_each_service_image(self):
        self.build("distributed")
        self.apply()
        checked = [e[1] for e in self.log if e[0] == "image" and ":" in e[1] and not e[1].startswith("sha256:")
                   and e[1] != "postgres:16"]  # the presence check of a third-party image
        # Round 5 (G4): the printed plan asks first whether the release's images are all on this host (the first
        # is not). Then each is looked up once to see whether it must be pulled, and once more for its digest.
        self.assertEqual(checked, ["futureagi/future-agi:v1.1.0"] + ["futureagi/future-agi:v1.1.0",
                                                                    "futureagi/frontend:v1.1.0",
                                                                    "futureagi/future-agi:v1.1.0"] * 2)

    def test_production_uses_compose_wait_and_its_env_file(self):
        checkout_env = (self.root / ".env").read_bytes()  # left by setUp's standalone world
        self.build("production")
        self.apply()
        self.assertIn(("up", "-d", "--no-build", "--wait", "--wait-timeout", "1200"), self.compose_calls())
        self.assertIn(("wait_api",), self.log)
        self.assertEqual(self.env_bytes(), self.with_versions({"FUTURE_AGI_VERSION": "v1.1.0",
                                                               "FRONTEND_VERSION": "v1.1.0"}))
        receipt = self.last_receipt()
        self.assertEqual(receipt["mode"], "production")
        self.assertIs(receipt["production"], True)
        self.assertEqual(receipt["env_file"], "deploy/.env.production")
        self.assertEqual((self.root / ".env").read_bytes(), checkout_env, "production never edits .env")

    def test_production_hook_gets_the_production_env_file(self):
        self.build("production")
        self.apply()
        env = self.hook.calls[0][1]["env"]
        self.assertEqual(env["FUTURE_AGI_UPDATE_MODE"], "production")
        self.assertEqual(env["FUTURE_AGI_UPDATE_ENV_FILE"], str(self.env_path))


class EnvFileFidelityTests(UpdaterCase):
    def test_crlf_line_endings_are_kept(self):
        text = "SECRET_KEY=%s\r\nFUTURE_AGI_VERSION=v1.0.0\r\nOTHER=x\r\n" % SECRET
        self.build(env_text=text)
        self.apply()
        self.assertEqual(self.env_bytes(), text.replace("v1.0.0", "v1.1.0").encode())

    def test_crlf_restored_byte_for_byte_after_failure(self):
        text = "SECRET_KEY=%s\r\nFUTURE_AGI_VERSION=v1.0.0\r\nOTHER=x\r\n" % SECRET
        self.build(env_text=text)
        self.inst.failures["pull"] = InstallError("pull failed")
        self.assertRaisesExactly(FailedBeforeRollout, self.apply)
        self.assertEqual(self.env_bytes(), text.encode())

    def test_export_prefix_is_kept(self):
        text = "export FUTURE_AGI_VERSION=v1.0.0\nexport SECRET_KEY=%s\n" % SECRET
        self.build(env_text=text)
        self.apply()
        self.assertEqual(self.env_bytes(), text.replace("v1.0.0", "v1.1.0").encode())

    def test_symlinked_env_file_is_edited_at_its_target(self):
        real = self.root / "config" / "fagi.env"
        real.parent.mkdir()
        real.write_bytes(self.original)
        self.env_path.unlink()
        self.env_path.symlink_to(real)
        self.apply()
        self.assertTrue(self.env_path.is_symlink())
        self.assertEqual(real.read_bytes(), self.with_versions({"FUTURE_AGI_VERSION": "v1.1.0"}))

    def test_absent_key_removed_again_after_failure(self):
        self.build(values={"FUTURE_AGI_VERSION": None})
        self.inst.failures["pull"] = InstallError("pull failed")
        self.assertRaisesExactly(FailedBeforeRollout, self.apply)
        self.assertEqual(self.env_bytes(), self.original)

    def test_empty_value_restored_after_failure(self):
        self.build(values={"FUTURE_AGI_VERSION": ""})
        self.inst.failures["pull"] = InstallError("pull failed")
        self.assertRaisesExactly(FailedBeforeRollout, self.apply)
        self.assertEqual(self.env_bytes(), self.original)

    def test_commented_version_line_restored_byte_for_byte_after_failure(self):
        # "Nothing was changed": a failed update must not eat the operator's note.
        text = ("SECRET_KEY=%s\nFUTURE_AGI_VERSION=v1.0.0  # pinned until QA signs off v1.1\nOTHER=x\n" % SECRET)
        self.build(env_text=text)
        self.inst.failures["pull"] = InstallError("pull failed")
        self.assertRaisesExactly(FailedBeforeRollout, self.apply)
        self.assertEqual(self.env_bytes(), text.encode())


# ------------------------------------------------- apply: before rollout --

class FailureBeforeRolloutTests(UpdaterCase):
    def assertRestored(self, exc, stage):
        self.assertEqual(exc.exit_code, 4)
        self.assertEqual(self.env_bytes(), self.original)
        self.assertEqual(self.inst.env["FUTURE_AGI_VERSION"], "v1.0.0")
        self.assertIsNone(self.journal())
        receipt = self.last_receipt()
        self.assertEqual(receipt["status"], "failed_before_rollout")
        self.assertEqual(receipt["stage"], stage)
        self.assertRegex(receipt["finished_at"], TIMESTAMP)
        self.assertNotIn(("compose", ("up", "-d", "--no-build"), True), self.log)
        self.assertEqual(self.inst.running["app"]["image_id"], id_of("futureagi/standalone", "v1.0.0"))
        self.assertIn("stopped before restarting anything", str(exc))
        self.assertIn("Nothing was changed; the previous versions are configured and running", str(exc))
        self.assertIn(f"Receipt: {self.state.receipts()[-1]}", str(exc))
        return receipt

    def test_config_check_command_fails(self):
        self.inst.failures["config"] = InstallError("`docker compose config` failed with exit code 15: bad yaml")
        exc = self.assertRaisesExactly(FailedBeforeRollout, self.apply)
        receipt = self.assertRestored(exc, "env_written")
        self.assertIn("bad yaml", receipt["error"])
        self.assertNotIn("pull", [c[0] for c in self.compose_calls()])

    def test_config_resolves_another_tag(self):
        self.inst.image_overrides["app"] = "futureagi/standalone:v9.9.9"
        exc = self.assertRaisesExactly(FailedBeforeRollout, self.apply)
        receipt = self.assertRestored(exc, "env_written")
        self.assertIn("service app resolves to futureagi/standalone:v9.9.9, not tag v1.1.0", receipt["error"])
        self.assertIn("compose override, or an exported variable", receipt["error"])

    def test_config_resolves_the_right_tag_pinned_by_digest(self):
        pinned = "futureagi/standalone:v1.1.0@" + digest_of("futureagi/standalone", "v1.1.0")
        self.inst.image_overrides["app"] = pinned
        exc = self.assertRaisesExactly(FailedBeforeRollout, self.apply)
        receipt = self.assertRestored(exc, "env_written")
        self.assertIn(f"resolves to {pinned}", receipt["error"])

    def test_config_resolves_an_untagged_image(self):
        self.inst.image_overrides["app"] = "futureagi/standalone"
        exc = self.assertRaisesExactly(FailedBeforeRollout, self.apply)
        self.assertRestored(exc, "env_written")

    def test_one_of_several_services_resolving_elsewhere_stops_everything(self):
        self.build("distributed")
        self.inst.image_overrides["worker"] = "futureagi/future-agi:v1.0.0"
        self.assertRaisesExactly(FailedBeforeRollout, self.apply)
        self.assertEqual(self.env_bytes(), self.original)
        self.assertIn("service worker resolves to futureagi/future-agi:v1.0.0", self.last_receipt()["error"])

    def test_pull_fails(self):
        self.inst.failures["pull"] = InstallError("`docker compose pull app` failed with exit code 1: unauthorized")
        exc = self.assertRaisesExactly(FailedBeforeRollout, self.apply)
        receipt = self.assertRestored(exc, "env_written")
        self.assertIn("unauthorized", receipt["error"])
        self.assertIn("unauthorized", str(exc))

    def test_pull_of_a_missing_tag_fails(self):
        del self.inst.registry["futureagi/standalone:v1.1.0"]
        exc = self.assertRaisesExactly(FailedBeforeRollout, self.apply)
        self.assertRestored(exc, "env_written")

    def test_image_missing_after_the_pull(self):
        self.inst.hidden.add("futureagi/standalone:v1.1.0")
        exc = self.assertRaisesExactly(FailedBeforeRollout, self.apply)
        receipt = self.assertRestored(exc, "pulled")
        self.assertIn("futureagi/standalone:v1.1.0 is not on this host after the pull", receipt["error"])

    def test_unexpected_exception_is_treated_like_a_failure(self):
        self.inst.failures["pull"] = RuntimeError("something odd")
        exc = self.assertRaisesExactly(FailedBeforeRollout, self.apply)
        self.assertRestored(exc, "env_written")

    def test_exception_without_message_records_its_class(self):
        self.inst.failures["pull"] = RuntimeError()
        self.assertRaisesExactly(FailedBeforeRollout, self.apply)
        self.assertEqual(self.last_receipt()["error"], "RuntimeError")

    def test_failure_restores_installation_env(self):
        self.build("distributed")
        self.inst.failures["pull"] = InstallError("nope")
        self.assertRaisesExactly(FailedBeforeRollout, self.apply)
        self.assertEqual(self.inst.env["FUTURE_AGI_VERSION"], "v1.0.0")
        self.assertEqual(self.inst.env["FRONTEND_VERSION"], "v1.0.0")
        self.assertEqual(self.env_bytes(), self.original)

    def test_restore_is_the_last_env_write(self):
        self.inst.failures["pull"] = InstallError("nope")
        self.assertRaisesExactly(FailedBeforeRollout, self.apply)
        writes = [e[1] for e in self.log if e[0] == "env_write"]
        self.assertEqual(writes, [{"FUTURE_AGI_VERSION": "v1.1.0"}])
        self.assertIn(("env_restore",), self.log)
        self.assertEqual(self.events()[-3:], [("env_restore",),
                                              ("receipt", "failed_before_rollout"), ("clear_journal",)])

    def test_env_write_itself_failing(self):
        self.env_write_failures[0] = EnvFileError("refusing to write unexpected value")
        exc = self.assertRaisesExactly(FailedBeforeRollout, self.apply)
        receipt = self.assertRestored(exc, "prepare")
        self.assertIn("refusing to write unexpected value", receipt["error"])


class DigestVerificationTests(UpdaterCase):
    REPO = "futureagi/standalone"

    def set_new_digests(self, entries):
        self.inst.metadata[id_of(self.REPO, "v1.1.0")]["repo_digests"] = entries

    def signed(self):
        return digest_of(self.REPO, "v1.1.0")

    def assertRefused(self, fragment):
        exc = self.assertRaisesExactly(FailedBeforeRollout, self.apply)
        error = self.last_receipt()["error"]
        self.assertIn("Refusing to start it", error)
        self.assertIn(f"not the signed {self.signed()}", error)
        self.assertIn(fragment, error)
        self.assertEqual(self.last_receipt()["stage"], "pulled")
        self.assertEqual(self.env_bytes(), self.original)
        self.assertNotIn("up", [c[0] for c in self.compose_calls()])
        self.assertIsNone(self.journal())
        return exc

    def test_other_digest_is_refused(self):
        other = "sha256:" + "e" * 64
        self.set_new_digests([f"{self.REPO}@{other}"])
        self.assertRefused(f"resolves to {other} on this host")

    def test_signed_digest_under_another_repository_is_refused(self):
        self.set_new_digests([f"evil.example.com/futureagi/standalone@{self.signed()}",
                              f"futureagi/standalone-fork@{self.signed()}"])
        self.assertRefused("resolves to no registry digest on this host")

    def test_no_repo_digests_is_refused(self):
        self.set_new_digests([])
        self.assertRefused("no registry digest")

    def test_only_malformed_entries_is_refused(self):
        self.set_new_digests(["", "not a reference", f"{self.REPO}@sha256:short"])
        self.assertRefused("no registry digest")

    def test_signed_digest_among_others_is_accepted(self):
        self.set_new_digests([f"{self.REPO}@sha256:{'0' * 64}", "garbage !!", f"{self.REPO}@{self.signed()}"])
        self.apply()
        self.assertEqual(self.last_receipt()["status"], "succeeded")

    def test_docker_hub_name_forms_are_equivalent(self):
        for name in ("docker.io/futureagi/standalone", "index.docker.io/futureagi/standalone",
                     "registry-1.docker.io/futureagi/standalone"):
            with self.subTest(name=name):
                self.build()
                self.set_new_digests([f"{name}@{self.signed()}"])
                self.apply()
                self.assertEqual(self.last_receipt()["status"], "succeeded")

    def test_digest_of_a_previous_release_is_refused(self):
        self.set_new_digests([f"{self.REPO}@{digest_of(self.REPO, 'v1.0.0')}"])
        self.assertRefused(digest_of(self.REPO, "v1.0.0"))

    def test_one_bad_component_stops_every_component(self):
        self.build("distributed")
        frontend_id = id_of("futureagi/frontend", "v1.1.0")
        self.inst.metadata[frontend_id]["repo_digests"] = ["futureagi/frontend@sha256:" + "d" * 64]
        self.assertRaisesExactly(FailedBeforeRollout, self.apply)
        self.assertEqual(self.env_bytes(), self.original)
        self.assertIn("futureagi/frontend:v1.1.0 resolves to", self.last_receipt()["error"])
        self.assertNotIn("up", [c[0] for c in self.compose_calls()])

    def test_component_digests_are_not_interchangeable(self):
        # backend's signed digest must not satisfy frontend.
        self.build("distributed")
        backend_digest = digest_of("futureagi/future-agi", "v1.1.0")
        self.inst.metadata[id_of("futureagi/frontend", "v1.1.0")]["repo_digests"] = [
            f"futureagi/frontend@{backend_digest}"]
        self.assertRaisesExactly(FailedBeforeRollout, self.apply)


# ------------------------------------------------- apply: during rollout --

class FailureDuringRolloutTests(UpdaterCase):
    def assertKept(self, exc, status="failed_during_rollout"):
        self.assertEqual(self.env_bytes(), self.with_versions({"FUTURE_AGI_VERSION": "v1.1.0"}))
        self.assertEqual(self.inst.env["FUTURE_AGI_VERSION"], "v1.1.0")
        self.assertIsNone(self.journal())
        receipt = self.last_receipt()
        self.assertEqual(receipt["status"], status)
        self.assertEqual(receipt["stage"], "rollout")
        path = self.state.receipts()[-1]
        text = str(exc)
        self.assertIn("apply to v1.1.0 stopped while containers were being replaced", text)
        self.assertIn("The env file selects v1.1.0", text)
        self.assertIn("To finish, run `./bin/update apply` again for v1.1.0", text)
        self.assertIn(f"`./bin/update rollback --receipt {path} --yes`", text)
        self.assertIn("database migrations are not reverted", text)
        self.assertIn(f"Receipt: {path}", text)
        writes = [e for e in self.log if e[0] == "env_write"]
        self.assertEqual(len(writes), 1, "the new version keys must stay")
        return receipt

    def test_up_fails(self):
        self.inst.failures["up"] = InstallError("`docker compose up` failed with exit code 1: port is already allocated")
        exc = self.assertRaisesExactly(FailedDuringRollout, self.apply)
        self.assertEqual(exc.exit_code, 5)
        receipt = self.assertKept(exc)
        self.assertIn("port is already allocated", receipt["error"])
        self.assertNotIn(("wait_standalone",), self.log)

    def test_readiness_not_ready(self):
        self.ready.failure = NotReady("the app container keeps restarting (2 restarts during this update)")
        exc = self.assertRaisesExactly(FailedDuringRollout, self.apply)
        receipt = self.assertKept(exc)
        self.assertIn("keeps restarting", receipt["error"])

    def test_readiness_unexpected_error(self):
        self.ready.failure = OSError("connection reset")
        exc = self.assertRaisesExactly(FailedDuringRollout, self.apply)
        self.assertKept(exc)

    def test_distributed_api_not_ready(self):
        self.build("distributed")
        self.ready.failure = NotReady("the API did not answer /health/ on port 8000 within 180s")
        self.assertRaisesExactly(FailedDuringRollout, self.apply)
        self.assertEqual(self.env_value("FRONTEND_VERSION"), "v1.1.0")
        self.assertEqual(self.last_receipt()["status"], "failed_during_rollout")

    def test_container_still_on_previous_image(self):
        self.inst.up_skip.add("app")
        exc = self.assertRaisesExactly(FailedDuringRollout, self.apply)
        receipt = self.assertKept(exc)
        self.assertEqual(receipt["error"], "containers still run the previous images: app")

    def test_stale_service_is_listed_once(self):
        self.build("distributed")
        self.inst.up_skip.add("worker")
        self.inst.extra_containers.append({
            "id": "c-worker-2", "service": "worker", "image_id": id_of("futureagi/future-agi", "v1.0.0"),
            "image": "futureagi/future-agi:v1.0.0", "status": "running", "exit_code": 0, "restart_count": 0,
            "health": "none", "started_at": "", "command": []})
        self.assertRaisesExactly(FailedDuringRollout, self.apply)
        self.assertEqual(self.last_receipt()["error"], "containers still run the previous images: worker")

    def test_containers_of_other_services_are_ignored(self):
        self.inst.extra_containers.append({
            "id": "c-postgres", "service": "postgres", "image_id": "sha256:" + "7" * 64, "image": "postgres:16",
            "status": "running", "exit_code": 0, "restart_count": 0, "health": "healthy", "started_at": "",
            "command": []})
        self.apply()
        self.assertEqual(self.last_receipt()["status"], "succeeded")


# -------------------------------------------------------------- interrupts --

class InterruptTests(UpdaterCase):
    def test_ctrl_c_during_pull_restores(self):
        self.inst.failures["pull"] = KeyboardInterrupt()
        exc = self.assertRaisesExactly(InterruptedRun, self.apply)
        self.assertEqual(exc.exit_code, 130)
        self.assertEqual(self.env_bytes(), self.original)
        receipt = self.last_receipt()
        self.assertEqual(receipt["status"], "interrupted_before_rollout")
        self.assertEqual(receipt["error"], "interrupted")
        self.assertIsNone(self.journal())
        self.assertIn("apply stopped before restarting anything: interrupted", str(exc))

    def test_sigterm_during_config_check_restores(self):
        self.inst.failures["config"] = Interrupted("SIGTERM")
        self.assertRaisesExactly(InterruptedRun, self.apply)
        self.assertEqual(self.env_bytes(), self.original)
        receipt = self.last_receipt()
        self.assertEqual(receipt["status"], "interrupted_before_rollout")
        self.assertEqual(receipt["error"], "SIGTERM")

    def test_interrupt_during_env_write_restores(self):
        self.env_write_failures[0] = KeyboardInterrupt()
        self.assertRaisesExactly(InterruptedRun, self.apply)
        self.assertEqual(self.env_bytes(), self.original)
        receipt = self.last_receipt()
        self.assertEqual(receipt["stage"], "prepare")
        self.assertEqual(receipt["status"], "interrupted_before_rollout")

    def test_interrupt_during_digest_check_restores(self):
        def interrupt_on_new(reference, _real=self.inst.image):
            # The digest check after the pull (the lookup before it only asks whether the image must be pulled).
            if reference == "futureagi/standalone:v1.1.0" and ("journal", "pulled") in self.events():
                raise KeyboardInterrupt()
            return _real(reference)

        self.inst.image = interrupt_on_new
        self.assertRaisesExactly(InterruptedRun, self.apply)
        self.assertEqual(self.env_bytes(), self.original)
        self.assertEqual(self.last_receipt()["stage"], "pulled")

    def test_ctrl_c_during_up_keeps_target(self):
        self.inst.failures["up"] = KeyboardInterrupt()
        exc = self.assertRaisesExactly(InterruptedRun, self.apply)
        self.assertEqual(self.env_value("FUTURE_AGI_VERSION"), "v1.1.0")
        receipt = self.last_receipt()
        self.assertEqual(receipt["status"], "interrupted_during_rollout")
        self.assertEqual(receipt["error"], "interrupted")
        self.assertIn("stopped while containers were being replaced: interrupted", str(exc))

    def test_sighup_during_readiness_keeps_target(self):
        self.ready.failure = Interrupted("SIGHUP")
        self.assertRaisesExactly(InterruptedRun, self.apply)
        self.assertEqual(self.env_value("FUTURE_AGI_VERSION"), "v1.1.0")
        self.assertEqual(self.last_receipt()["status"], "interrupted_during_rollout")
        self.assertEqual(self.last_receipt()["error"], "SIGHUP")

    def test_real_sigterm_during_up_is_handled_and_handlers_restored(self):
        sentinel, calls = self.sentinel_handlers()
        self.inst.failures["up"] = lambda *a: deliver(signal.SIGTERM)
        exc = self.assertRaisesExactly(InterruptedRun, self.apply)
        self.assertEqual(exc.exit_code, 130)
        self.assertEqual(self.env_value("FUTURE_AGI_VERSION"), "v1.1.0")
        receipt = self.last_receipt()
        self.assertEqual(receipt["status"], "interrupted_during_rollout")
        self.assertEqual(receipt["error"], "SIGTERM")
        self.assertIsNone(self.journal())
        self.assertIs(signal.getsignal(signal.SIGTERM), sentinel)
        self.assertIs(signal.getsignal(signal.SIGHUP), sentinel)
        self.assertEqual(calls, [])

    def test_real_sighup_during_pull_restores(self):
        sentinel, calls = self.sentinel_handlers()
        self.inst.failures["pull"] = lambda *a: deliver(signal.SIGHUP)
        self.assertRaisesExactly(InterruptedRun, self.apply)
        self.assertEqual(self.env_bytes(), self.original)
        self.assertEqual(self.last_receipt()["status"], "interrupted_before_rollout")
        self.assertEqual(self.last_receipt()["error"], "SIGHUP")
        self.assertIs(signal.getsignal(signal.SIGHUP), sentinel)
        self.assertEqual(calls, [])

    def test_handlers_restored_after_a_failure(self):
        sentinel, _ = self.sentinel_handlers()
        self.inst.failures["pull"] = InstallError("nope")
        self.assertRaisesExactly(FailedBeforeRollout, self.apply)
        self.assertIs(signal.getsignal(signal.SIGTERM), sentinel)
        self.assertIs(signal.getsignal(signal.SIGHUP), sentinel)

    def test_interrupt_while_recording_the_rollout_stage_restarted_nothing(self):
        # The stage only counts once its journal is on disk: an interrupt while
        # that journal is written (e.g. during fsync) happened before any restart.
        self.state.journal_failures["rollout"] = Interrupted("SIGTERM")
        self.assertRaisesExactly(InterruptedRun, self.apply)
        self.assertNotIn("up", [c[0] for c in self.compose_calls()])
        self.assertEqual(self.env_bytes(), self.original)
        self.assertEqual(self.last_receipt()["status"], "interrupted_before_rollout")


class JournalBookkeepingTests(UpdaterCase):
    def test_first_journal_write_failing_changes_nothing(self):
        self.state.journal_failures["prepare"] = StateError("cannot write journal")
        self.assertRaisesExactly(StateError, self.apply)
        self.assertEqual(self.env_bytes(), self.original)
        self.assertEqual(self.receipts(), [])
        self.assertNotIn("env_write", [e[0] for e in self.log])

    def test_env_written_journal_failing_restores(self):
        self.state.journal_failures["env_written"] = StateError("cannot write journal")
        self.assertRaisesExactly(FailedBeforeRollout, self.apply)
        self.assertEqual(self.env_bytes(), self.original)

    def test_pulled_journal_failing_restores(self):
        self.state.journal_failures["pulled"] = StateError("cannot write journal")
        self.assertRaisesExactly(FailedBeforeRollout, self.apply)
        self.assertEqual(self.env_bytes(), self.original)
        self.assertEqual(self.last_receipt()["status"], "failed_before_rollout")

    def test_rollout_journal_failing_counts_as_before_rollout(self):
        self.state.journal_failures["rollout"] = StateError("cannot write journal")
        self.assertRaisesExactly(FailedBeforeRollout, self.apply)
        self.assertNotIn("up", [c[0] for c in self.compose_calls()])
        self.assertEqual(self.env_bytes(), self.original)
        self.assertEqual(self.last_receipt()["status"], "failed_before_rollout")


class RestoreFailureTests(UpdaterCase):
    def fail_twice(self):
        self.inst.failures["config"] = InstallError("`docker compose config` failed with exit code 15")
        self.env_write_failures[1] = EnvFileError("cannot write .env: No space left on device")
        return self.assertRaisesExactly(UpdateError, self.apply)

    def test_restore_failure_keeps_the_journal(self):
        exc = self.fail_twice()
        self.assertEqual(exc.exit_code, 2)
        self.assertIn("restoring the previous version settings also failed "
                      "(cannot write .env: No space left on device.", str(exc))
        self.assertIn("Run `./bin/update apply` again: it restores them first", str(exc))
        self.assertIn("exit code 15", str(exc))
        journal = self.journal()
        self.assertIsNotNone(journal)
        self.assertEqual(journal["stage"], "env_written")
        self.assertEqual(journal["previous_values"], {"FUTURE_AGI_VERSION": "v1.0.0"})
        self.assertEqual(self.receipts(), [])
        self.assertEqual(self.env_value("FUTURE_AGI_VERSION"), "v1.1.0")

    def test_next_run_restores_from_the_kept_journal(self):
        self.fail_twice()
        self.env_write_failures.clear()
        del self.inst.failures["config"]
        recovered = self.updater().recover()
        self.assertEqual(recovered["status"], "interrupted_before_rollout")
        self.assertEqual(self.env_bytes(), self.original)
        self.assertIsNone(self.journal())

    def test_next_apply_recovers_then_updates(self):
        self.fail_twice()
        self.env_write_failures.clear()
        del self.inst.failures["config"]
        self.apply()
        statuses = [r["status"] for r in self.receipts()]
        self.assertEqual(statuses, ["interrupted_before_rollout", "succeeded"])
        self.assertEqual(self.plans_seen[-1]["status"], "interrupted_before_rollout")
        self.assertEqual(self.env_bytes(), self.with_versions({"FUTURE_AGI_VERSION": "v1.1.0"}))


# ----------------------------------------------------------------- recover --

class RecoverTests(UpdaterCase):
    def setUp(self):
        super().setUp()
        self.build(current="v1.1.0")  # a killed run already wrote the new keys

    def test_no_journal_is_nothing_to_do(self):
        self.assertIsNone(self.updater().recover())
        self.assertEqual(self.receipts(), [])
        self.assertEqual(self.env_bytes(), self.original)

    def test_journal_before_rollout_restores(self):
        for stage in STAGES_BEFORE_ROLLOUT:
            with self.subTest(stage=stage):
                self.build(current="v1.1.0")
                self.craft_journal(stage)
                recovered = self.updater().recover()
                self.assertEqual(recovered["status"], "interrupted_before_rollout")
                self.assertEqual(recovered["stage"], stage)
                self.assertEqual(recovered["error"], "the bin/update process ended before finishing")
                self.assertRegex(recovered["finished_at"], TIMESTAMP)
                self.assertEqual(self.env_bytes(), self.with_versions({"FUTURE_AGI_VERSION": "v1.0.0"}))
                self.assertEqual(self.inst.env["FUTURE_AGI_VERSION"], "v1.0.0")
                self.assertIsNone(self.journal())
                self.assertEqual(self.last_receipt()["status"], "interrupted_before_rollout")
                self.assertEqual(recovered["_path"], str(self.state.receipts()[-1]))
                self.assertIn("it stopped before restarting anything; its version settings were put back",
                              self.out.getvalue())

    def test_journal_during_rollout_keeps_target(self):
        self.craft_journal("rollout")
        recovered = self.updater().recover()
        self.assertEqual(recovered["status"], "interrupted_during_rollout")
        self.assertEqual(self.env_bytes(), self.original)
        self.assertIsNone(self.journal())
        self.assertNotIn("env_write", [e[0] for e in self.log])
        self.assertIn("Recovered an interrupted apply to v1.1.0: it stopped while containers were being replaced",
                      self.out.getvalue())

    def test_unknown_stage_keeps_the_journal_for_manual_recovery(self):
        self.craft_journal('something-new')
        before = self.state.journal_path.read_bytes()
        with self.assertRaises(StateError):
            self.updater().recover()
        self.assertEqual(self.state.journal_path.read_bytes(), before)
        self.assertEqual(self.env_bytes(), self.original)

    def test_existing_error_is_kept(self):
        self.craft_journal("pulled", error="pull was slow")
        self.assertEqual(self.updater().recover()["error"], "pull was slow")

    def test_rollback_journal_is_recovered_as_a_rollback(self):
        self.craft_journal("env_written", kind="rollback", target_version="v1.0.0",
                           previous_values={"FUTURE_AGI_VERSION": "v1.2.0"})
        recovered = self.updater().recover()
        self.assertEqual(recovered["kind"], "rollback")
        self.assertEqual(self.env_value("FUTURE_AGI_VERSION"), "v1.2.0")
        self.assertIn("Recovered an interrupted rollback to v1.0.0", self.out.getvalue())

    def test_journal_of_another_env_file_blocks(self):
        self.craft_journal("pulled", env_file="deploy/.env.production", production=True)
        exc = self.assertRaisesExactly(Blocked, self.updater().recover)
        self.assertIn("an interrupted bin/update apply for deploy/.env.production must be recovered before this "
                      "install can be updated or rolled back", str(exc))
        self.assertIn("run `./bin/update --production rollback` (without --yes it only recovers that run and changes "
                      "nothing else)", str(exc))
        self.assertIsNotNone(self.journal())
        self.assertEqual(self.env_bytes(), self.original)
        self.assertEqual(self.receipts(), [])

    def test_journal_of_the_checkout_env_blocks_a_production_run(self):
        self.build("production", current="v1.1.0")
        self.craft_journal("pulled", env_file=".env", production=False)
        exc = self.assertRaisesExactly(Blocked, self.updater().recover)
        self.assertIn("for .env must be recovered before this install can be updated or rolled back", str(exc))
        self.assertIn("run `./bin/update rollback` (without --yes it only recovers that run", str(exc))

    def test_apply_with_foreign_journal_blocks_before_planning(self):
        self.craft_journal("pulled", env_file="deploy/.env.production", production=True)
        self.build(current="v1.0.0")
        self.assertRaisesExactly(Blocked, self.apply)
        self.assertEqual(self.plans_seen, [])
        self.assertEqual(self.hook.calls, [])
        self.assertEqual(self.env_bytes(), self.original)

    def test_rollback_with_foreign_journal_blocks(self):
        self.craft_journal("pulled", env_file="deploy/.env.production", production=True)
        self.assertRaisesExactly(Blocked, self.rollback)

    def test_corrupt_journal_is_a_state_error(self):
        self.state.ensure()
        self.state.journal_path.write_text("{not json", encoding="utf-8")
        self.assertRaisesExactly(StateError, self.apply)
        self.assertEqual(self.plans_seen, [])
        self.assertEqual(self.env_bytes(), self.original)

    def test_apply_recovers_first_and_plans_with_the_recovered_receipt(self):
        self.craft_journal("rollout")
        self.apply(self.plan(env_changes={}, resume=True, current=["v1.1.0"], action="keep"))
        self.assertEqual(self.plans_seen[0]["status"], "interrupted_during_rollout")
        self.assertEqual([r["status"] for r in self.receipts()], ["interrupted_during_rollout", "succeeded"])


class KilledRunTests(UpdaterCase):
    """SystemExit escapes the updater's handler, like `kill -9` would: the journal stays."""

    def test_killed_before_rollout_is_restored_by_the_next_apply(self):
        self.inst.failures["pull"] = SystemExit(137)
        with self.assertRaises(SystemExit):
            self.apply()
        self.assertEqual(self.journal()["stage"], "env_written")
        self.assertEqual(self.env_value("FUTURE_AGI_VERSION"), "v1.1.0")
        self.assertEqual(self.receipts(), [])
        del self.inst.failures["pull"]
        self.plans_seen = []
        self.apply()
        self.assertEqual(self.plans_seen[0]["status"], "interrupted_before_rollout")
        self.assertEqual([r["status"] for r in self.receipts()], ["interrupted_before_rollout", "succeeded"])
        self.assertIsNone(self.journal())

    def test_killed_during_rollout_is_recorded_and_resumable(self):
        self.inst.failures["up"] = SystemExit(137)
        with self.assertRaises(SystemExit):
            self.apply()
        self.assertEqual(self.journal()["stage"], "rollout")
        del self.inst.failures["up"]
        recovered = self.updater().recover()
        self.assertEqual(recovered["status"], "interrupted_during_rollout")
        self.assertEqual(self.env_value("FUTURE_AGI_VERSION"), "v1.1.0")
        self.assertEqual(recovered["previous_values"], {"FUTURE_AGI_VERSION": "v1.0.0"})

    def test_kill_does_not_hold_the_lock(self):
        self.inst.failures["pull"] = SystemExit(137)
        with self.assertRaises(SystemExit):
            self.apply()
        with self.state.lock():
            pass


# ------------------------------------------------------------------ backup --

class BackupTests(UpdaterCase):
    def assertNothingChanged(self):
        self.assertEqual(self.env_bytes(), self.original)
        self.assertIsNone(self.journal())
        self.assertEqual(self.receipts(), [])
        self.assertEqual(self.compose_calls(), [])
        self.assertNotIn("env_write", [e[0] for e in self.log])

    def test_no_backup_configured_blocks(self):
        exc = self.assertRaisesExactly(Blocked, self.apply, backup_command=None)
        self.assertIn("no backup is configured", str(exc))
        self.assertIn("FUTURE_AGI_UPDATE_BACKUP_COMMAND", str(exc))
        self.assertIn("--allow-no-backup", str(exc))
        self.assertEqual(self.hook.calls, [])
        self.assertNothingChanged()

    def test_empty_cli_command_falls_back_to_the_setting(self):
        self.assertRaisesExactly(Blocked, self.apply, backup_command="")
        self.apply(updater=self.updater(FUTURE_AGI_UPDATE_BACKUP_COMMAND="/usr/local/bin/backup-fagi"),
                   backup_command="")
        self.assertEqual(self.hook.calls[0][0], ["/usr/local/bin/backup-fagi"])

    def test_allow_no_backup_skips_it(self):
        self.apply(backup_command=None, allow_no_backup=True)
        self.assertEqual(self.hook.calls, [])
        self.assertIn("Skipping the backup: --allow-no-backup was given.", self.out.getvalue())
        self.assertEqual(self.last_receipt()["backup"], {"ran": False})

    def test_allow_no_backup_still_runs_a_configured_backup(self):
        self.apply(allow_no_backup=True)
        self.assertEqual(len(self.hook.calls), 1)
        self.assertIs(self.last_receipt()["backup"]["ran"], True)

    def test_setting_is_used_without_a_cli_command(self):
        self.apply(updater=self.updater(FUTURE_AGI_UPDATE_BACKUP_COMMAND="/usr/local/bin/backup-fagi --all"),
                   backup_command=None)
        self.assertEqual(self.hook.calls[0][0], ["/usr/local/bin/backup-fagi", "--all"])

    def test_cli_command_wins_over_the_setting(self):
        self.apply(updater=self.updater(FUTURE_AGI_UPDATE_BACKUP_COMMAND="/from/setting"),
                   backup_command="/from/cli")
        self.assertEqual(self.hook.calls[0][0], ["/from/cli"])

    def test_command_is_split_like_a_command_line_without_a_shell(self):
        self.apply(backup_command="'/opt/my backup/run.sh' --target \"s3://bucket/a b\" --verbose $HOME;rm")
        argv, kwargs = self.hook.calls[0]
        self.assertEqual(argv, ["/opt/my backup/run.sh", "--target", "s3://bucket/a b", "--verbose", "$HOME;rm"])
        self.assertEqual(set(kwargs), {"cwd", "env", "check", "timeout"})
        self.assertEqual(kwargs["cwd"], str(self.root))
        self.assertIs(kwargs["check"], False)
        self.assertEqual(kwargs["timeout"], 3600)

    def test_whitespace_only_command_is_blocked(self):
        exc = self.assertRaisesExactly(Blocked, self.apply, backup_command="   ")
        self.assertIn("FUTURE_AGI_UPDATE_BACKUP_COMMAND is empty", str(exc))
        self.assertEqual(self.hook.calls, [])
        self.assertNothingChanged()

    def test_unparseable_command_is_a_clean_error(self):
        # An unbalanced quote must be reported like any other bad setting, not
        # escape as a bare ValueError (which bin/update turns into a traceback).
        with self.assertRaises(Exception) as ctx:
            self.apply(backup_command="/opt/backup/run --label 'nightly")
        self.assertIsInstance(ctx.exception, UpdateError, repr(ctx.exception))
        self.assertEqual(self.hook.calls, [])
        self.assertNothingChanged()

    def test_hook_environment(self):
        with mock.patch.dict(os.environ, {"FAGI_TEST_INHERITED": "yes"}):
            self.apply()
        env = self.hook.calls[0][1]["env"]
        self.assertEqual(env["FAGI_TEST_INHERITED"], "yes")
        self.assertEqual(env["FUTURE_AGI_UPDATE_ROOT"], str(self.root))
        self.assertEqual(env["FUTURE_AGI_UPDATE_MODE"], "standalone")
        self.assertEqual(env["FUTURE_AGI_UPDATE_PROJECT"], PROJECT)
        self.assertEqual(env["FUTURE_AGI_UPDATE_ENV_FILE"], str(self.env_path))
        self.assertEqual(env["FUTURE_AGI_UPDATE_FROM_VERSION"], "v1.0.0")
        self.assertEqual(env["FUTURE_AGI_UPDATE_TO_VERSION"], "v1.1.0")

    def test_hook_from_version_lists_every_current_version(self):
        self.build("distributed")
        self.apply(self.plan(current=["v1.0.0", "v1.0.1"]))
        self.assertEqual(self.hook.calls[0][1]["env"]["FUTURE_AGI_UPDATE_FROM_VERSION"], "v1.0.0,v1.0.1")
        self.assertEqual(self.hook.calls[0][1]["env"]["FUTURE_AGI_UPDATE_MODE"], "distributed")

    def test_hook_context_overrides_the_inherited_environment(self):
        with mock.patch.dict(os.environ, {"FUTURE_AGI_UPDATE_TO_VERSION": "v0.0.1"}):
            self.apply()
        self.assertEqual(self.hook.calls[0][1]["env"]["FUTURE_AGI_UPDATE_TO_VERSION"], "v1.1.0")

    def test_backup_runs_before_any_change(self):
        self.apply()
        self.assertEqual(self.hook.observed[0]["env"], self.original)
        self.assertIs(self.hook.observed[0]["journal"], False)
        # Round 5 (G4): before it, only the printed plan's look at the release image on this host.
        self.assertEqual(self.events()[:2], [("image", "futureagi/standalone:v1.1.0"), ("backup",)])

    def test_nonzero_exit_stops_before_anything_changes(self):
        self.hook.returncode = 3
        exc = self.assertRaisesExactly(FailedBeforeRollout, self.apply)
        self.assertEqual(exc.exit_code, 4)
        self.assertEqual(str(exc), "the backup hook exited with code 3 (output above); nothing was changed")
        # Round 5 (G4): the printed plan looked at the release image on this host first; nothing else ran.
        self.assertEqual(self.events(), [("image", "futureagi/standalone:v1.1.0"), ("backup",)])
        self.assertNothingChanged()

    def test_timeout(self):
        self.hook.raises = subprocess.TimeoutExpired(["/opt/backup/run"], 42)
        exc = self.assertRaisesExactly(FailedBeforeRollout, self.apply,
                                       updater=self.updater(FUTURE_AGI_UPDATE_BACKUP_TIMEOUT_SECONDS="42"))
        self.assertEqual(self.hook.calls[0][1]["timeout"], 42)
        self.assertEqual(str(exc), "the backup hook did not finish within 42s; nothing was changed")
        self.assertNothingChanged()

    def test_command_that_cannot_run(self):
        self.hook.raises = FileNotFoundError(2, "No such file or directory", "/opt/backup/run")
        exc = self.assertRaisesExactly(FailedBeforeRollout, self.apply)
        self.assertIn("the backup hook could not run (", str(exc))
        self.assertIn("No such file or directory", str(exc))
        self.assertIn("nothing was changed", str(exc))
        self.assertNothingChanged()

    def test_permission_denied(self):
        self.hook.raises = PermissionError(13, "Permission denied")
        self.assertRaisesExactly(FailedBeforeRollout, self.apply)
        self.assertNothingChanged()

    def test_receipt_records_only_the_program(self):
        self.apply(backup_command="/opt/backup/run --password hunter2-long-secret")
        receipt = self.last_receipt()
        self.assertEqual(receipt["backup"]["command"], "/opt/backup/run")
        self.assertNotIn("hunter2-long-secret", self.state_text())

    def test_backup_is_not_run_for_blocked_up_to_date_or_dry_run(self):
        for plan, kwargs in ((self.plan(decision=BLOCKED, reasons=["no"]), {}),
                             (self.plan(decision=UP_TO_DATE, env_changes={}), {}),
                             (self.plan(), {"dry_run": True})):
            with self.subTest(decision=plan["decision"], **kwargs):
                try:
                    self.apply(plan, **kwargs)
                except Blocked:
                    pass
                self.assertEqual(self.hook.calls, [])


# ---------------------------------------------------------- plan decisions --

class PlanDecisionTests(UpdaterCase):
    def assertUntouched(self):
        self.assertEqual(self.env_bytes(), self.original)
        self.assertIsNone(self.journal())
        self.assertEqual(self.receipts(), [])
        self.assertEqual(self.hook.calls, [])
        self.assertEqual(self.log, [])

    def test_blocked_plan(self):
        plan = self.plan(decision=BLOCKED, reasons=["the release signature is not verified: no bundle",
                                                    "v1.0.0 -> v2.0.0 is outside the 'minor' update policy"])
        exc = self.assertRaisesExactly(Blocked, self.apply, plan)
        self.assertEqual(exc.exit_code, 3)
        self.assertEqual(str(exc), "the update is blocked: the release signature is not verified: no bundle; "
                                   "v1.0.0 -> v2.0.0 is outside the 'minor' update policy")
        self.assertIn(plan_text(plan), self.out.getvalue())
        self.assertUntouched()

    def test_up_to_date_plan(self):
        self.assertIsNone(self.apply(self.plan(decision=UP_TO_DATE, env_changes={})))
        self.assertIn("Nothing to do: this install already runs the release.", self.out.getvalue())
        self.assertUntouched()

    def test_dry_run_changes_nothing(self):
        self.assertIsNone(self.apply(dry_run=True))
        text = self.out.getvalue()
        self.assertIn("Dry run: nothing was changed.", text)
        self.assertIn("would run the backup, set FUTURE_AGI_VERSION=v1.1.0, pull and verify the images", text)
        self.assertUntouched()

    def test_dry_run_lists_every_change(self):
        self.build("distributed")
        self.apply(dry_run=True)
        self.assertIn("set FUTURE_AGI_VERSION=v1.1.0, FRONTEND_VERSION=v1.1.0, pull", self.out.getvalue())

    def test_dry_run_without_changes(self):
        self.apply(self.plan(env_changes={}, resume=True), dry_run=True)
        self.assertIn("set no version keys, pull", self.out.getvalue())
        self.assertUntouched()

    def test_dry_run_does_not_recover_a_journal(self):
        self.build(current="v1.1.0")
        self.craft_journal("pulled")
        self.apply(dry_run=True)
        self.assertEqual(self.journal()["stage"], "pulled")
        self.assertEqual(self.env_bytes(), self.original)
        self.assertEqual(self.receipts(), [])
        # It plans as the recovery would leave things (DryRunAfterAKilledRunTests), without recovering.
        self.assertEqual([last["status"] for last in self.plans_seen], ["interrupted_before_rollout"])

    def test_dry_run_of_a_blocked_plan_is_blocked(self):
        self.assertRaisesExactly(Blocked, self.apply, self.plan(decision=BLOCKED, reasons=["drift"]), dry_run=True)
        self.assertUntouched()

    def test_dry_run_of_an_up_to_date_plan(self):
        self.assertIsNone(self.apply(self.plan(decision=UP_TO_DATE, env_changes={}), dry_run=True))
        self.assertIn("Nothing to do", self.out.getvalue())

    def test_plan_gets_the_latest_receipt(self):
        self.build(current="v1.1.0")
        path = self.craft_apply(status="failed_during_rollout")
        self.apply(self.plan(decision=UP_TO_DATE, env_changes={}))
        self.assertEqual(self.plans_seen[0]["_path"], str(path))
        self.assertEqual(self.plans_seen[0]["status"], "failed_during_rollout")

    def test_plan_gets_none_without_receipts(self):
        self.apply()
        self.assertEqual(self.plans_seen, [None])

    def test_plan_is_printed_before_anything_runs(self):
        plan = self.plan()
        self.apply(plan)
        # Worded as the plan this run carries out (its Decision line says what it does, not what to run).
        self.assertTrue(self.out.getvalue().startswith(plan_text(plan, applying=True)))


# ------------------------------------------------------------------ resume --

class ResumeTests(UpdaterCase):
    def failed_rollout(self):
        """An apply whose containers were replaced but never became ready."""

        self.ready.failure = NotReady("the app container keeps restarting")
        self.assertRaisesExactly(FailedDuringRollout, self.apply)
        self.ready.failure = None
        path = self.state.receipts()[-1]
        self.assertEqual(self.inst.running["app"]["image_id"], id_of("futureagi/standalone", "v1.1.0"))
        del self.log[:]
        self.plans_seen = []
        return path

    def resume_plan(self):
        return self.plan(env_changes={}, resume=True, current=["v1.1.0"], action="keep")

    def test_resume_keeps_the_failed_receipts_previous_state(self):
        failed = self.failed_rollout()
        self.apply(self.resume_plan())
        receipt = self.last_receipt()
        self.assertEqual(receipt["status"], "succeeded")
        self.assertEqual(receipt["resumes"], str(failed))
        self.assertEqual(receipt["previous_values"], {"FUTURE_AGI_VERSION": "v1.0.0"})
        self.assertEqual(receipt["previous_images"]["app"]["image_id"], id_of("futureagi/standalone", "v1.0.0"))
        self.assertEqual(self.plans_seen[0]["status"], "failed_during_rollout")
        self.assertEqual(self.plans_seen[0]["_path"], str(failed))

    def test_resume_without_env_changes_leaves_the_env_file(self):
        self.failed_rollout()
        before = self.env_bytes()
        self.apply(self.resume_plan())
        self.assertEqual(self.env_bytes(), before)
        self.assertIn(("env_write", {}), self.log)
        self.assertIn(("up", "-d", "--no-build"), self.compose_calls())

    def test_not_resuming_snapshots_the_current_state(self):
        self.failed_rollout()
        self.apply(self.plan(env_changes={}, resume=False, current=["v1.1.0"], action="keep"))
        receipt = self.last_receipt()
        self.assertNotIn("resumes", receipt)
        self.assertEqual(receipt["previous_images"]["app"]["image_id"], id_of("futureagi/standalone", "v1.1.0"))

    def test_failed_resume_before_rollout_leaves_the_env_file_as_found(self):
        # The resumed run changed nothing in the env file; its failure must not
        # put the versions from before the original update back (unverified,
        # while the containers already run the new release).
        self.failed_rollout()
        before = self.env_bytes()
        self.prune(TARGET)  # pulled only when it is not on the host
        self.inst.failures["pull"] = InstallError("`docker compose pull` failed: network unreachable")
        exc = self.assertRaisesExactly(FailedBeforeRollout, self.apply, self.resume_plan())
        # The env file still selects the stopped update's target: it says so instead of "nothing was changed".
        self.assertIn("is still unfinished: .env selects it (FUTURE_AGI_VERSION=v1.1.0)", str(exc))
        self.assertEqual(self.env_bytes(), before)
        self.assertEqual(self.env_value("FUTURE_AGI_VERSION"), "v1.1.0")

    def test_interrupted_resume_before_rollout_leaves_the_env_file_as_found(self):
        self.failed_rollout()
        before = self.env_bytes()
        self.prune(TARGET)  # pulled only when it is not on the host
        self.inst.failures["pull"] = KeyboardInterrupt()
        self.assertRaisesExactly(InterruptedRun, self.apply, self.resume_plan())
        self.assertEqual(self.env_bytes(), before)

    def test_rollback_after_a_resume_restores_the_original_versions(self):
        self.failed_rollout()
        self.apply(self.resume_plan())
        resumed = self.state.receipts()[-1]
        self.rollback()
        receipt = self.last_receipt()
        self.assertEqual(receipt["kind"], "rollback")
        self.assertEqual(receipt["rolls_back"], str(resumed.resolve()))
        self.assertEqual(self.env_bytes(), self.original)
        self.assertEqual(self.inst.running["app"]["image_id"], id_of("futureagi/standalone", "v1.0.0"))


# ---------------------------------------------------------------- rollback --

class RollbackFlowTests(UpdaterCase):
    def test_rollback_restores_the_previous_release(self):
        applied = self.applied()
        path = self.rollback()
        self.assertEqual(self.env_bytes(), self.original)
        self.assertEqual(self.inst.running["app"]["image_id"], id_of("futureagi/standalone", "v1.0.0"))
        receipt = self.last_receipt()
        self.assertEqual(Path(path), self.state.receipts()[-1])
        self.assertEqual(receipt["kind"], "rollback")
        self.assertEqual(receipt["status"], "rolled_back")
        self.assertEqual(receipt["rolls_back"], str(Path(applied).resolve()))
        self.assertEqual(receipt["from_version"], "v1.1.0")
        self.assertEqual(receipt["target_version"], "v1.0.0")
        self.assertEqual(receipt["env_changes"], {"FUTURE_AGI_VERSION": "v1.0.0"})
        self.assertEqual(receipt["previous_values"], {"FUTURE_AGI_VERSION": "v1.1.0"})
        self.assertEqual(receipt["previous_images"]["app"]["image_id"], id_of("futureagi/standalone", "v1.1.0"))
        self.assertEqual(receipt["components"], self.receipts()[0]["components"])
        self.assertEqual(receipt["mode"], "standalone")
        self.assertEqual(receipt["env_file"], ".env")
        self.assertEqual(receipt["project"], PROJECT)
        self.assertEqual(receipt["target_image_ids"], {"app": id_of("futureagi/standalone", "v1.0.0")})
        self.assertEqual(receipt["schema_version"], RECEIPT_SCHEMA)
        self.assertNotIn("backup", receipt)
        self.assertIsNone(self.journal())

    def test_rollback_steps(self):
        self.applied()
        self.rollback()
        # The image that ran before the update is still on the host: it is verified, never pulled again.
        self.assertEqual(self.compose_calls(), [("config", "--quiet"), ("up", "-d", "--no-build")])
        self.assertIn("Already on this host with the expected digest, not pulled: futureagi/standalone:v1.0.0",
                      self.out.getvalue())
        self.assertIn(("wait_standalone",), self.log)
        self.assertEqual([e[1] for e in self.log if e[0] == "env_write"], [{"FUTURE_AGI_VERSION": "v1.0.0"}])
        self.assertEqual([e[1] for e in self.log if e[0] == "journal"], ["prepare", "env_written", "pulled",
                                                                        "rollout"])
        self.assertEqual(self.hook.calls[1:], [], "rollback runs no backup")

    def test_rollback_output(self):
        self.applied()
        path = self.rollback()
        text = self.out.getvalue()
        self.assertIn("Rolling back v1.1.0 to FUTURE_AGI_VERSION=v1.0.0 (Standalone, project fagi-test).", text)
        self.assertIn("Database migrations the update applied are not reverted", text)
        self.assertIn(f"Rolled back to v1.0.0. Receipt: {path}", text)

    def test_distributed_rollback(self):
        self.build("distributed")
        self.applied()
        self.rollback()
        self.assertEqual(self.env_bytes(), self.original)
        self.assertIn(("up", "-d", "--no-build", "--wait", "--wait-timeout", "1200"), self.compose_calls())
        # The previous images are still on the host: nothing is pulled.
        self.assertEqual([c for c in self.compose_calls() if c[0] == "pull"], [])
        self.assertIn(("wait_api",), self.log)
        self.assertIn("(Distributed, project fagi-test)", self.out.getvalue())

    def test_production_rollback(self):
        self.build("production")
        self.applied()
        self.rollback()
        self.assertEqual(self.env_bytes(), self.original)
        receipt = self.last_receipt()
        self.assertEqual(receipt["env_file"], "deploy/.env.production")
        self.assertIs(receipt["production"], True)

    def test_latest_install_is_pinned_to_the_version_that_ran(self):
        self.build(values={"FUTURE_AGI_VERSION": ""})
        self.applied()
        self.rollback()
        self.assertEqual(self.env_bytes(), self.with_versions({"FUTURE_AGI_VERSION": "v1.0.0"}))
        self.assertEqual(self.inst.running["app"]["image_id"], id_of("futureagi/standalone", "v1.0.0"))
        self.assertEqual(self.last_receipt()["target_version"], "v1.0.0")

    def test_unset_key_is_pinned_to_the_version_that_ran(self):
        self.build(values={"FUTURE_AGI_VERSION": None})
        self.applied()
        self.rollback()
        self.assertEqual(self.env_value("FUTURE_AGI_VERSION"), "v1.0.0")

    def test_needs_yes(self):
        self.applied()
        before = self.env_bytes()
        receipts = self.state.receipts()
        exc = self.assertRaisesExactly(Blocked, self.rollback, yes=False)
        self.assertEqual(str(exc), "rollback not started: rerun with --yes to confirm")
        self.assertEqual(self.env_bytes(), before)
        self.assertEqual(self.state.receipts(), receipts)
        self.assertIsNone(self.journal())
        self.assertEqual(self.log, [])
        self.assertIn("Rolling back v1.1.0 to FUTURE_AGI_VERSION=v1.0.0", self.out.getvalue())

    def test_double_rollback_is_refused(self):
        self.applied()
        first = self.rollback()
        exc = self.assertRaisesExactly(Blocked, self.rollback)
        self.assertIn(f"that update was already rolled back ({Path(first).resolve()})", str(exc))

    def test_rollback_during_rollout_failure(self):
        applied = self.applied()
        self.inst.failures["up"] = InstallError("`docker compose up` failed with exit code 1")
        exc = self.assertRaisesExactly(FailedDuringRollout, self.rollback)
        receipt = self.last_receipt()
        self.assertEqual(receipt["status"], "failed_during_rollout")
        self.assertEqual(receipt["kind"], "rollback")
        self.assertEqual(self.env_bytes(), self.original)  # the rollback keys stay
        self.assertIn("rollback to v1.0.0 stopped while containers were being replaced", str(exc))
        self.assertIn(f"rerun `./bin/update rollback --receipt {Path(applied).resolve()} --yes`", str(exc))

    def test_retry_after_a_failed_rollback(self):
        self.applied()
        self.inst.failures["up"] = InstallError("boom")
        self.assertRaisesExactly(FailedDuringRollout, self.rollback)
        del self.inst.failures["up"]
        self.rollback()
        self.assertEqual([r["status"] for r in self.receipts()], ["succeeded", "failed_during_rollout",
                                                                  "rolled_back"])
        self.assertEqual(self.inst.running["app"]["image_id"], id_of("futureagi/standalone", "v1.0.0"))

    def test_retry_after_a_rollback_failed_before_rollout(self):
        self.applied()
        self.prune("v1.0.0")  # pulled only when it is not on the host
        self.inst.failures["pull"] = InstallError("network down")
        self.assertRaisesExactly(FailedBeforeRollout, self.rollback)
        self.assertEqual(self.env_value("FUTURE_AGI_VERSION"), "v1.1.0")
        del self.inst.failures["pull"]
        self.rollback()
        self.assertEqual(self.env_bytes(), self.original)

    def test_rollback_failure_before_rollout_restores_the_updated_env(self):
        self.applied()
        updated = self.env_bytes()
        self.inst.failures["config"] = InstallError("bad")
        exc = self.assertRaisesExactly(FailedBeforeRollout, self.rollback)
        self.assertIn("rollback stopped before restarting anything", str(exc))
        self.assertEqual(self.env_bytes(), updated)
        self.assertEqual(self.last_receipt()["status"], "failed_before_rollout")

    def test_interrupted_rollback(self):
        self.applied()
        self.inst.failures["up"] = Interrupted("SIGTERM")
        self.assertRaisesExactly(InterruptedRun, self.rollback)
        self.assertEqual(self.last_receipt()["status"], "interrupted_during_rollout")
        self.assertEqual(self.last_receipt()["kind"], "rollback")

    def test_rollback_recovers_a_killed_rollback_first(self):
        applied = self.applied()
        self.prune("v1.0.0")  # pulled only when it is not on the host
        self.inst.failures["pull"] = SystemExit(137)
        with self.assertRaises(SystemExit):
            self.rollback()
        self.assertEqual(self.env_value("FUTURE_AGI_VERSION"), "v1.0.0")
        del self.inst.failures["pull"]
        self.rollback()
        receipts = self.receipts()
        self.assertEqual([(r["kind"], r["status"]) for r in receipts],
                         [("apply", "succeeded"), ("rollback", "interrupted_before_rollout"),
                          ("rollback", "rolled_back")])
        self.assertEqual(receipts[-1]["rolls_back"], str(Path(applied).resolve()))
        self.assertEqual(self.env_bytes(), self.original)

    def test_lock_released_after_rollback(self):
        self.applied()
        self.rollback()
        with self.state.lock():
            pass


class RollbackSelectionTests(UpdaterCase):
    def setUp(self):
        super().setUp()
        self.build(current="v1.1.0")

    def test_nothing_to_roll_back(self):
        exc = self.assertRaisesExactly(Blocked, self.rollback)
        self.assertEqual(str(exc), "there is no update to roll back in this checkout")

    def test_only_unrollbackable_applies(self):
        self.craft_apply(status="failed_before_rollout")
        self.craft_apply(status="interrupted_before_rollout")
        self.assertRaisesExactly(Blocked, self.rollback)

    def test_default_is_the_last_rollbackable_apply(self):
        first = self.craft_apply()
        self.craft_apply(target="v1.2.0", previous="v1.1.0", status="failed_before_rollout")
        self.rollback()
        self.assertEqual(self.last_receipt()["rolls_back"], str(first.resolve()))
        self.assertEqual(self.env_value("FUTURE_AGI_VERSION"), "v1.0.0")

    def test_default_picks_the_newest_of_two_updates(self):
        self.build(current="v1.2.0")
        self.craft_apply()
        second = self.craft_apply(previous="v1.1.0", target="v1.2.0")
        self.rollback()
        self.assertEqual(self.last_receipt()["rolls_back"], str(second.resolve()))
        self.assertEqual(self.env_value("FUTURE_AGI_VERSION"), "v1.1.0")
        self.assertEqual(self.last_receipt()["from_version"], "v1.2.0")
        self.assertEqual(self.last_receipt()["target_version"], "v1.1.0")

    def test_default_ignores_rollback_receipts(self):
        applied = self.craft_apply()
        self.craft_rollback(applied, status="failed_before_rollout")
        self.rollback()
        self.assertEqual(self.last_receipt()["rolls_back"], str(applied.resolve()))

    def test_failed_during_rollout_apply_can_be_rolled_back(self):
        for status in ("failed_during_rollout", "interrupted_during_rollout"):
            with self.subTest(status=status):
                self.build(current="v1.1.0")
                applied = self.craft_apply(status=status)
                self.rollback()
                self.assertEqual(self.last_receipt()["rolls_back"], str(applied.resolve()))
                self.assertEqual(self.last_receipt()["status"], "rolled_back")

    def test_explicit_receipt(self):
        first = self.craft_apply()
        self.rollback(first)
        self.assertEqual(self.last_receipt()["rolls_back"], str(first.resolve()))

    def test_explicit_relative_receipt(self):
        first = self.craft_apply()
        cwd = os.getcwd()
        self.addCleanup(os.chdir, cwd)
        os.chdir(self.root)
        self.rollback(Path(".futureagi-updates") / first.name)
        self.assertEqual(self.last_receipt()["rolls_back"], str(first.resolve()))

    def test_explicit_receipt_outside_the_state_dir(self):
        first = self.craft_apply()
        outside = self.root / first.name
        outside.write_bytes(first.read_bytes())
        exc = self.assertRaisesExactly(StateError, self.rollback, outside)
        self.assertIn("is not a receipt in", str(exc))
        self.assertIn("use the path bin/update printed", str(exc))
        self.assertEqual(self.env_bytes(), self.original)

    def test_explicit_symlink_to_a_receipt_outside(self):
        first = self.craft_apply()
        outside = self.root / "elsewhere" / first.name
        outside.parent.mkdir()
        outside.write_bytes(first.read_bytes())
        link = self.state.path / "receipt-20991231T000000Z-99.json"
        link.symlink_to(outside)
        self.assertRaisesExactly(StateError, self.rollback, link)

    def test_explicit_file_that_is_not_named_like_a_receipt(self):
        first = self.craft_apply()
        copy = self.state.path / "my-receipt.json"
        copy.write_bytes(first.read_bytes())
        self.assertRaisesExactly(StateError, self.rollback, copy)

    def test_explicit_receipt_that_does_not_exist(self):
        self.craft_apply()
        missing = self.state.path / "receipt-20991231T000000Z-1.json"
        self.assertRaisesExactly(StateError, self.rollback, missing)

    def test_explicit_receipt_of_another_schema(self):
        first = self.craft_apply()
        record = json.loads(first.read_text())
        record["schema_version"] = RECEIPT_SCHEMA + 1
        first.write_text(json.dumps(record))
        exc = self.assertRaisesExactly(StateError, self.rollback, first)
        self.assertIn("was written by another version of bin/update", str(exc))
        self.assertEqual(self.env_bytes(), self.original)

    def test_receipt_of_a_rollback(self):
        applied = self.craft_apply()
        rolled = self.craft_rollback(applied, status="failed_during_rollout")
        exc = self.assertRaisesExactly(Blocked, self.rollback, rolled)
        self.assertEqual(str(exc), "that receipt is a rollback; to undo it, apply the release again")

    def test_receipt_that_changed_nothing(self):
        for status in ("failed_before_rollout", "interrupted_before_rollout", "running"):
            with self.subTest(status=status):
                path = self.craft_apply(status=status)
                exc = self.assertRaisesExactly(Blocked, self.rollback, path)
                self.assertIn(f"that update ended {status}: it changed nothing", str(exc))

    def test_receipt_for_another_install(self):
        cases = {
            "mode": {"mode": "distributed"},
            "production": {"production": True},
            "env_file": {"env_file": "other.env"},
            "project": {"project": "another-project"},
        }
        for name, fields in cases.items():
            with self.subTest(field=name):
                path = self.craft_apply(**fields)
                exc = self.assertRaisesExactly(Blocked, self.rollback, path)
                self.assertIn("that receipt is for the", str(exc))
                self.assertIn("rerun with the same flags", str(exc))
                self.assertEqual(self.env_bytes(), self.original)

    def test_receipt_for_a_production_install_names_its_flags(self):
        path = self.craft_apply(mode="production", production=True, env_file="deploy/.env.production")
        exc = self.assertRaisesExactly(Blocked, self.rollback, path)
        self.assertIn("that receipt is for the production install using deploy/.env.production "
                      f"(project {PROJECT}); rerun with the same flags (--production, --env-file "
                      "deploy/.env.production)", str(exc))

    def test_receipt_for_a_non_production_install(self):
        self.build("production", current="v1.1.0")
        path = self.craft_apply(mode="distributed", production=False, env_file=".env")
        exc = self.assertRaisesExactly(Blocked, self.rollback, path)
        self.assertIn("(without --production)", str(exc))

    def test_newer_update_must_be_rolled_back_first(self):
        first = self.craft_apply()
        second = self.craft_apply(previous="v1.1.0", target="v1.2.0")
        exc = self.assertRaisesExactly(Blocked, self.rollback, first)
        self.assertIn(f"a newer update ({second.resolve()}) ran after that one; roll that back first", str(exc))
        self.assertEqual(self.env_bytes(), self.original)

    def test_newer_update_that_stopped_mid_rollout_also_blocks(self):
        first = self.craft_apply()
        self.craft_apply(previous="v1.1.0", target="v1.2.0", status="failed_during_rollout")
        self.assertRaisesExactly(Blocked, self.rollback, first)

    def test_newer_update_that_changed_nothing_does_not_block(self):
        first = self.craft_apply()
        self.craft_apply(previous="v1.1.0", target="v1.2.0", status="failed_before_rollout")
        self.rollback(first)
        self.assertEqual(self.last_receipt()["status"], "rolled_back")

    def test_already_rolled_back(self):
        applied = self.craft_apply()
        rolled = self.craft_rollback(applied)
        for receipt in (None, applied):
            with self.subTest(receipt=receipt):
                exc = self.assertRaisesExactly(Blocked, self.rollback, receipt)
                self.assertEqual(str(exc), f"that update was already rolled back ({rolled.resolve()})")

    def test_rollback_of_another_update_does_not_count(self):
        older = self.craft_apply(target="v1.0.5")
        applied = self.craft_apply()
        self.craft_rollback(older)
        self.rollback()
        self.assertEqual(self.last_receipt()["rolls_back"], str(applied.resolve()))

    def test_failed_rollback_allows_a_retry(self):
        for status in ("failed_during_rollout", "interrupted_during_rollout", "failed_before_rollout",
                       "interrupted_before_rollout"):
            with self.subTest(status=status):
                self.build(current="v1.1.0")
                applied = self.craft_apply()
                self.craft_rollback(applied, status=status)
                self.rollback()
                self.assertEqual(self.last_receipt()["status"], "rolled_back")

    def test_refusals_change_nothing(self):
        first = self.craft_apply()
        self.craft_apply(previous="v1.1.0", target="v1.2.0")
        count = len(self.state.receipts())
        self.assertRaisesExactly(Blocked, self.rollback, first)
        self.assertEqual(len(self.state.receipts()), count)
        self.assertIsNone(self.journal())
        self.assertEqual(self.compose_calls(), [])


class RollbackRestoreValueTests(UpdaterCase):
    def setUp(self):
        super().setUp()
        self.build(current="v1.1.0")

    def test_release_tag_is_kept(self):
        self.craft_apply(previous_values={"FUTURE_AGI_VERSION": "v1.0.0"})
        self.rollback()
        self.assertEqual(self.env_value("FUTURE_AGI_VERSION"), "v1.0.0")

    def test_prerelease_tag_is_kept(self):
        self.publish("futureagi/standalone", "v1.1.0-rc.1", "v1.1.0-rc.1")
        images = {"app": {"image": "futureagi/standalone:v1.1.0-rc.1",
                          "image_id": id_of("futureagi/standalone", "v1.1.0-rc.1"),
                          "repo_digests": [f"futureagi/standalone@{digest_of('futureagi/standalone', 'v1.1.0-rc.1')}"],
                          "version": "v1.1.0-rc.1"}}
        self.craft_apply(previous_values={"FUTURE_AGI_VERSION": "v1.1.0-rc.1"}, previous_images=images)
        self.rollback()
        self.assertEqual(self.env_value("FUTURE_AGI_VERSION"), "v1.1.0-rc.1")

    def test_empty_value_is_pinned_to_the_running_label(self):
        self.craft_apply(previous_values={"FUTURE_AGI_VERSION": ""})
        self.rollback()
        self.assertEqual(self.env_value("FUTURE_AGI_VERSION"), "v1.0.0")

    def test_unset_value_is_pinned_to_the_running_label(self):
        self.craft_apply(previous_values={"FUTURE_AGI_VERSION": None})
        self.rollback()
        self.assertEqual(self.env_value("FUTURE_AGI_VERSION"), "v1.0.0")

    def test_latest_is_pinned_to_the_running_label(self):
        self.craft_apply(previous_values={"FUTURE_AGI_VERSION": "latest"})
        self.rollback()
        self.assertEqual(self.env_value("FUTURE_AGI_VERSION"), "v1.0.0")
        self.assertEqual(self.last_receipt()["env_changes"], {"FUTURE_AGI_VERSION": "v1.0.0"})

    def test_tag_without_the_v_is_pinned_to_the_running_label(self):
        self.craft_apply(previous_values={"FUTURE_AGI_VERSION": "1.0.0"})
        self.rollback()
        self.assertEqual(self.env_value("FUTURE_AGI_VERSION"), "v1.0.0")

    def test_no_label_is_ambiguous(self):
        images = self.snapshot_of("v1.0.0")
        images["app"]["version"] = ""
        self.craft_apply(previous_values={"FUTURE_AGI_VERSION": ""}, previous_images=images)
        exc = self.assertRaisesExactly(Blocked, self.rollback)
        self.assertEqual(str(exc), "cannot tell which release FUTURE_AGI_VERSION ran before the update (it was "
                                   "unset); set it in the env file by hand instead")
        self.assertEqual(self.env_bytes(), self.original)
        self.assertEqual(self.log, [])

    def test_latest_without_label_names_the_value(self):
        images = self.snapshot_of("v1.0.0")
        images["app"]["version"] = ""
        self.craft_apply(previous_values={"FUTURE_AGI_VERSION": "latest"}, previous_images=images)
        exc = self.assertRaisesExactly(Blocked, self.rollback)
        self.assertIn("(it was latest)", str(exc))

    def test_non_release_label_is_ambiguous(self):
        images = self.snapshot_of("v1.0.0")
        images["app"]["version"] = "1.0.0"
        self.craft_apply(previous_values={"FUTURE_AGI_VERSION": ""}, previous_images=images)
        self.assertRaisesExactly(Blocked, self.rollback)

    def test_no_previous_images_is_ambiguous(self):
        self.craft_apply(previous_values={"FUTURE_AGI_VERSION": ""}, previous_images={})
        self.assertRaisesExactly(Blocked, self.rollback)

    def test_different_labels_are_ambiguous(self):
        self.build("distributed", current="v1.1.0")
        images = self.snapshot_of("v1.0.0")
        images["worker"]["version"] = "v1.0.1"
        self.craft_apply(previous_values={"FUTURE_AGI_VERSION": "", "FRONTEND_VERSION": "v1.0.0"},
                         previous_images=images)
        exc = self.assertRaisesExactly(Blocked, self.rollback)
        self.assertIn("cannot tell which release FUTURE_AGI_VERSION ran", str(exc))
        self.assertEqual(self.env_bytes(), self.original)

    def test_one_labelled_container_is_enough(self):
        self.build("distributed", current="v1.1.0")
        images = self.snapshot_of("v1.0.0")
        images["worker"]["version"] = ""
        self.craft_apply(previous_values={"FUTURE_AGI_VERSION": "", "FRONTEND_VERSION": "v1.0.0"},
                         previous_images=images)
        self.rollback()
        self.assertEqual(self.env_value("FUTURE_AGI_VERSION"), "v1.0.0")

    def test_labels_of_other_variables_are_not_used(self):
        self.build("distributed", current="v1.1.0")
        images = self.snapshot_of("v1.0.0")
        for service in ("backend", "worker"):
            images[service]["version"] = ""
        self.craft_apply(previous_values={"FUTURE_AGI_VERSION": "", "FRONTEND_VERSION": "v1.0.0"},
                         previous_images=images)
        self.assertRaisesExactly(Blocked, self.rollback)

    def test_gpu_tag_is_kept(self):
        self.build("distributed", groups=(BACKEND, SERVING_GPU), current="v1.1.0")
        self.craft_apply(previous_values={"FUTURE_AGI_VERSION": "v1.0.0", "SERVING_VERSION": "v1.0.0-gpu"})
        self.rollback()
        self.assertEqual(self.env_value("SERVING_VERSION"), "v1.0.0-gpu")
        self.assertEqual(self.last_receipt()["target_version"], "v1.0.0,v1.0.0-gpu")
        self.assertEqual(self.inst.running["serving"]["image_id"], id_of("futureagi/serving", "v1.0.0-gpu"))

    def test_gpu_latest_is_pinned_with_the_gpu_suffix(self):
        self.build("distributed", groups=(BACKEND, SERVING_GPU), current="v1.1.0")
        self.craft_apply(previous_values={"FUTURE_AGI_VERSION": "v1.0.0", "SERVING_VERSION": ""})
        self.rollback()
        self.assertEqual(self.env_value("SERVING_VERSION"), "v1.0.0-gpu")

    def test_gpu_suffix_on_a_non_release_is_not_kept(self):
        self.build("distributed", groups=(BACKEND, SERVING_GPU), current="v1.1.0")
        self.craft_apply(previous_values={"FUTURE_AGI_VERSION": "v1.0.0", "SERVING_VERSION": "nightly-gpu"})
        self.rollback()
        self.assertEqual(self.env_value("SERVING_VERSION"), "v1.0.0-gpu")

    def test_local_is_kept_and_not_pulled(self):
        local_id = self.local_image("futureagi/standalone")
        images = {"app": {"image": "futureagi/standalone:local", "image_id": local_id, "repo_digests": [],
                          "version": ""}}
        self.craft_apply(previous_values={"FUTURE_AGI_VERSION": "local"}, previous_images=images)
        self.rollback()
        self.assertEqual(self.env_value("FUTURE_AGI_VERSION"), "local")
        self.assertEqual([c[0] for c in self.compose_calls()], ["config", "up"])
        self.assertEqual(self.inst.running["app"]["image_id"], local_id)
        self.assertEqual(self.last_receipt()["target_version"], "local")

    def test_local_image_rebuilt_since_is_refused(self):
        self.local_image("futureagi/standalone")
        images = {"app": {"image": "futureagi/standalone:local", "image_id": "sha256:" + "9" * 64,
                          "repo_digests": [], "version": ""}}
        self.craft_apply(previous_values={"FUTURE_AGI_VERSION": "local"}, previous_images=images)
        exc = self.assertRaisesExactly(FailedBeforeRollout, self.rollback)
        self.assertIn("futureagi/standalone:local is not the image that ran before the update "
                      f"(sha256:{'9' * 64})", str(exc))
        self.assertEqual(self.env_bytes(), self.original)

    def test_only_non_local_services_are_pulled(self):
        self.build("distributed", current="v1.1.0")
        local_id = self.local_image("futureagi/future-agi")
        images = self.snapshot_of("v1.0.0")
        for service in ("backend", "worker"):
            images[service] = {"image": "futureagi/future-agi:local", "image_id": local_id, "repo_digests": [],
                               "version": ""}
        self.craft_apply(previous_values={"FUTURE_AGI_VERSION": "local", "FRONTEND_VERSION": "v1.0.0"},
                         previous_images=images)
        self.rollback()
        self.assertIn(("pull", "frontend"), self.compose_calls())
        self.assertEqual([c for c in self.compose_calls() if c[0] == "pull"], [("pull", "frontend")])

    def test_ambiguity_blocks_before_asking_for_yes(self):
        images = self.snapshot_of("v1.0.0")
        images["app"]["version"] = ""
        self.craft_apply(previous_values={"FUTURE_AGI_VERSION": ""}, previous_images=images)
        exc = self.assertRaisesExactly(Blocked, self.rollback, yes=False)
        self.assertIn("cannot tell which release", str(exc))

    def test_distributed_install_with_fi_collector_built_from_this_checkout(self):
        # The default Distributed install leaves FI_COLLECTOR_VERSION unset, so
        # Compose runs futureagi/fi-collector:local (built from the checkout, no
        # version label). The plan allows updating it; rolling that update back
        # must put the locally built collector back, not refuse the rollback.
        self.build("distributed", groups=(BACKEND, FI_COLLECTOR, FRONTEND), current="v1.0.0",
                   values={"FUTURE_AGI_VERSION": "v1.0.0", "FRONTEND_VERSION": "v1.0.0"})
        local_id = self.inst.running["fi-collector"]["image_id"]
        self.applied()
        self.assertEqual(self.env_value("FI_COLLECTOR_VERSION"), "v1.1.0")
        self.rollback()
        self.assertIn(self.env_value("FI_COLLECTOR_VERSION"), (None, "", "local"))
        self.assertEqual(self.env_value("FUTURE_AGI_VERSION"), "v1.0.0")
        self.assertEqual(self.inst.running["fi-collector"]["image_id"], local_id)
        self.assertEqual(self.last_receipt()["status"], "rolled_back")


class RollbackVerificationTests(UpdaterCase):
    REPO = "futureagi/standalone"

    def setUp(self):
        super().setUp()
        self.build(current="v1.1.0")
        self.old_digest = digest_of(self.REPO, "v1.0.0")

    def images(self, repo_digests, image_id=None):
        return {"app": {"image": f"{self.REPO}:v1.0.0", "image_id": image_id or id_of(self.REPO, "v1.0.0"),
                        "repo_digests": repo_digests, "version": "v1.0.0"}}

    def test_previous_digest_matches(self):
        self.craft_apply(previous_images=self.images([f"{self.REPO}@{self.old_digest}"]))
        self.rollback()
        self.assertEqual(self.last_receipt()["status"], "rolled_back")

    def test_docker_hub_names_are_normalised(self):
        for name in ("docker.io/futureagi/standalone", "index.docker.io/futureagi/standalone"):
            with self.subTest(name=name):
                self.build(current="v1.1.0")
                self.craft_apply(previous_images=self.images([f"{name}@{self.old_digest}"]))
                self.rollback()
                self.assertEqual(self.last_receipt()["status"], "rolled_back")

    def test_previous_digest_differs(self):
        recorded = f"{self.REPO}@sha256:{'c' * 64}"
        self.craft_apply(previous_images=self.images([recorded]))
        exc = self.assertRaisesExactly(FailedBeforeRollout, self.rollback)
        error = self.last_receipt()["error"]
        self.assertIn(f"futureagi/standalone:v1.0.0 does not resolve to the digest that ran before the update "
                      f"({recorded}). Refusing to start it", error)
        self.assertEqual(self.last_receipt()["stage"], "pulled")
        self.assertEqual(self.env_bytes(), self.original)
        self.assertNotIn("up", [c[0] for c in self.compose_calls()])
        self.assertIn("Refusing to start it", str(exc))

    def test_same_digest_in_another_repository_is_refused(self):
        self.craft_apply(previous_images=self.images([f"mirror.example.com/futureagi/standalone@{self.old_digest}"]))
        self.assertRaisesExactly(FailedBeforeRollout, self.rollback)
        self.assertEqual(self.env_bytes(), self.original)

    def test_malformed_previous_entries_are_ignored(self):
        self.craft_apply(previous_images=self.images(["garbage", f"{self.REPO}@{self.old_digest}"]))
        self.rollback()
        self.assertEqual(self.last_receipt()["status"], "rolled_back")

    def test_only_malformed_previous_entries_refuse(self):
        self.craft_apply(previous_images=self.images(["garbage", "also bad@@"]))
        self.assertRaisesExactly(FailedBeforeRollout, self.rollback)

    def test_image_id_fallback_matches(self):
        self.craft_apply(previous_images=self.images([]))
        self.rollback()
        self.assertEqual(self.last_receipt()["status"], "rolled_back")

    def test_image_id_fallback_differs(self):
        other = "sha256:" + "8" * 64
        self.craft_apply(previous_images=self.images([], image_id=other))
        self.assertRaisesExactly(FailedBeforeRollout, self.rollback)
        self.assertIn(f"futureagi/standalone:v1.0.0 is not the image that ran before the update ({other})",
                      self.last_receipt()["error"])
        self.assertEqual(self.env_bytes(), self.original)

    def test_digest_match_wins_over_a_different_image_id(self):
        # Same registry digest, different local id (e.g. re-pulled): accepted.
        self.craft_apply(previous_images=self.images([f"{self.REPO}@{self.old_digest}"],
                                                     image_id="sha256:" + "8" * 64))
        self.rollback()
        self.assertEqual(self.last_receipt()["status"], "rolled_back")

    def test_service_without_previous_image_is_not_verified(self):
        self.craft_apply(previous_images={})
        self.rollback()
        self.assertEqual(self.last_receipt()["status"], "rolled_back")

    def test_image_missing_after_the_pull(self):
        self.craft_apply()
        self.inst.hidden.add(f"{self.REPO}:v1.0.0")
        self.assertRaisesExactly(FailedBeforeRollout, self.rollback)
        self.assertIn("is not on this host after the pull", self.last_receipt()["error"])
        self.assertEqual(self.env_bytes(), self.original)

    def test_config_must_resolve_the_restored_tag(self):
        self.craft_apply()
        self.inst.image_overrides["app"] = f"{self.REPO}:v1.1.0"
        self.assertRaisesExactly(FailedBeforeRollout, self.rollback)
        self.assertIn("not tag v1.0.0", self.last_receipt()["error"])
        self.assertEqual(self.env_bytes(), self.original)

    def test_containers_still_on_the_newer_image(self):
        self.craft_apply()
        self.inst.up_skip.add("app")
        self.assertRaisesExactly(FailedDuringRollout, self.rollback)
        self.assertEqual(self.last_receipt()["error"], "containers still run the previous images: app")


# ----------------------------------------------------------------- secrets --

class SecretTests(UpdaterCase):
    def assertNoSecrets(self, *texts):
        for text in (self.state_text(), *self.state.journal_texts, *texts):
            self.assertNotIn(SECRET, text)
            self.assertNotIn(PG_SECRET, text)

    def test_secrets_scrubbed_from_a_failure_before_rollout(self):
        self.inst.failures["config"] = InstallError(
            f"invalid interpolation: SECRET_KEY={SECRET} PG_PASSWORD={PG_SECRET}")
        exc = self.assertRaisesExactly(FailedBeforeRollout, self.apply)
        self.assertEqual(self.last_receipt()["error"], "invalid interpolation: SECRET_KEY=*** PG_PASSWORD=***")
        self.assertNoSecrets(str(exc))

    def test_secrets_scrubbed_from_a_failure_during_rollout(self):
        self.inst.failures["up"] = InstallError(f"container env PG_PASSWORD={PG_SECRET} rejected")
        exc = self.assertRaisesExactly(FailedDuringRollout, self.apply)
        self.assertIn("PG_PASSWORD=***", self.last_receipt()["error"])
        self.assertNoSecrets(str(exc))

    def test_secrets_scrubbed_from_readiness_errors(self):
        self.ready.failure = NotReady(f"app log: SECRET_KEY {SECRET} too short")
        exc = self.assertRaisesExactly(FailedDuringRollout, self.apply)
        self.assertNoSecrets(str(exc))

    def test_secrets_scrubbed_from_a_rollback_failure(self):
        self.applied()
        self.prune("v1.0.0")  # pulled only when it is not on the host
        self.inst.failures["pull"] = InstallError(f"auth {SECRET}")
        exc = self.assertRaisesExactly(FailedBeforeRollout, self.rollback)
        self.assertNoSecrets(str(exc))

    def test_journals_and_receipts_never_hold_env_contents(self):
        self.apply()
        self.assertTrue(self.state.journal_texts)
        self.assertNoSecrets()
        receipt = self.last_receipt()
        self.assertTrue(set(receipt["env_changes"]) <= set(VERSION_VARIABLES))
        self.assertTrue(set(receipt["previous_values"]) <= set(VERSION_VARIABLES))
        for journal in self.state.journal_texts:
            record = json.loads(journal)
            self.assertTrue(set(record["previous_values"]) <= set(VERSION_VARIABLES))
            self.assertNotIn("EXTRA_FLAG", journal)

    def test_kept_journal_holds_no_secrets(self):
        self.inst.failures["config"] = InstallError(f"bad {SECRET}")
        self.env_write_failures[1] = EnvFileError("disk full")
        self.assertRaisesExactly(UpdateError, self.apply)
        self.assertIsNotNone(self.journal())
        self.assertNoSecrets()

    def test_recovered_error_is_scrubbed(self):
        self.build(current="v1.1.0")
        self.craft_journal("rollout", error=f"left over {SECRET}")
        self.updater().recover()
        self.assertEqual(self.last_receipt()["error"], "left over ***")
        self.assertNoSecrets()


# -------------------------------------------------------------------- lock --

class LockTests(UpdaterCase):
    def test_apply_while_another_run_holds_the_lock(self):
        with self.state.lock():
            exc = self.assertRaisesExactly(StateError, self.apply)
        self.assertIn("another bin/update is running in this checkout", str(exc))
        self.assertIn(f"pid {os.getpid()}", str(exc))
        self.assertEqual(self.plans_seen, [])
        self.assertEqual(self.hook.calls, [])
        self.assertEqual(self.env_bytes(), self.original)
        self.assertEqual(self.receipts(), [])

    def test_rollback_while_another_run_holds_the_lock(self):
        self.applied()
        before = self.env_bytes()
        with self.state.lock():
            self.assertRaisesExactly(StateError, self.rollback)
        self.assertEqual(self.env_bytes(), before)

    def test_dry_run_also_takes_the_lock(self):
        with self.state.lock():
            self.assertRaisesExactly(StateError, self.apply, dry_run=True)

    def test_lock_is_released_after_a_failure(self):
        self.inst.failures["pull"] = InstallError("nope")
        self.assertRaisesExactly(FailedBeforeRollout, self.apply)
        del self.inst.failures["pull"]
        self.apply()
        self.assertEqual(self.last_receipt()["status"], "succeeded")

    def test_lock_is_released_after_blocked(self):
        self.assertRaisesExactly(Blocked, self.apply, backup_command=None)
        with self.state.lock():
            pass


# ------------------------------------------------- found values and stages --

class FoundValuesTests(UpdaterCase):
    """found_values: the keys as THIS run found them; previous_values: before the original update."""

    def failed_rollout(self):
        self.ready.failure = NotReady("the app container keeps restarting")
        self.assertRaisesExactly(FailedDuringRollout, self.apply)
        self.ready.failure = None
        del self.log[:]

    def resume_plan(self, env_changes=None):
        return self.plan(env_changes={} if env_changes is None else env_changes, resume=True, current=["v1.1.0"],
                         action="keep")

    def test_journal_records_the_found_values(self):
        self.apply()
        for text in self.state.journal_texts:
            self.assertEqual(json.loads(text)["found_values"], {"FUTURE_AGI_VERSION": "v1.0.0"})

    def test_found_absent_key_is_recorded_as_unset(self):
        self.build(values={"FUTURE_AGI_VERSION": None})
        self.apply()
        self.assertEqual(self.last_receipt()["found_values"], {"FUTURE_AGI_VERSION": None})

    def test_resume_records_what_it_found_and_keeps_the_original_previous_values(self):
        self.failed_rollout()
        self.apply(self.resume_plan({"FUTURE_AGI_VERSION": "v1.1.0"}))
        receipt = self.last_receipt()
        self.assertEqual(receipt["found_values"], {"FUTURE_AGI_VERSION": "v1.1.0"})
        self.assertEqual(receipt["previous_values"], {"FUTURE_AGI_VERSION": "v1.0.0"})

    def test_failed_resume_with_env_changes_puts_back_what_it_found(self):
        self.failed_rollout()
        before = self.env_bytes()
        self.inst.failures["config"] = InstallError("`docker compose config` failed with exit code 15")
        self.assertRaisesExactly(FailedBeforeRollout, self.apply, self.resume_plan({"FUTURE_AGI_VERSION": "v1.1.0"}))
        self.assertEqual(self.env_bytes(), before)
        self.assertEqual(self.inst.env["FUTURE_AGI_VERSION"], "v1.1.0")
        self.assertEqual([e[1] for e in self.log if e[0] == "env_write"],
                         [{"FUTURE_AGI_VERSION": "v1.1.0"}])
        self.assertIn(("env_restore",), self.log)

    def test_killed_resume_is_recovered_to_what_it_found(self):
        self.failed_rollout()
        before = self.env_bytes()
        self.prune(TARGET)  # pulled only when it is not on the host
        self.inst.failures["pull"] = SystemExit(137)
        with self.assertRaises(SystemExit):
            self.apply(self.resume_plan({"FUTURE_AGI_VERSION": "v1.1.0"}))
        journal = self.journal()
        self.assertEqual(journal["found_values"], {"FUTURE_AGI_VERSION": "v1.1.0"})
        self.assertEqual(journal["previous_values"], {"FUTURE_AGI_VERSION": "v1.0.0"})
        del self.inst.failures["pull"]
        recovered = self.updater().recover()
        self.assertEqual(recovered["status"], "interrupted_before_rollout")
        self.assertEqual(self.env_bytes(), before)
        self.assertEqual(self.inst.env["FUTURE_AGI_VERSION"], "v1.1.0")

    def test_recover_prefers_found_values_over_previous_values(self):
        self.build(current="v1.1.0")
        self.craft_journal("pulled", found_values={"FUTURE_AGI_VERSION": "v1.1.0"})
        recovered = self.updater().recover()
        self.assertEqual(recovered["status"], "interrupted_before_rollout")
        self.assertEqual(self.env_bytes(), self.original)
        self.assertEqual(recovered["previous_values"], {"FUTURE_AGI_VERSION": "v1.0.0"})

    def test_recover_with_empty_found_values_changes_nothing(self):
        self.build(current="v1.1.0")
        self.craft_journal("env_written", found_values={})
        self.updater().recover()
        self.assertEqual(self.env_bytes(), self.original)
        self.assertEqual([e[1] for e in self.log if e[0] == "env_write"], [])
        self.assertIn(("env_restore",), self.log)

    def test_rollback_records_found_values(self):
        self.applied()
        self.rollback()
        receipt = self.last_receipt()
        self.assertEqual(receipt["found_values"], {"FUTURE_AGI_VERSION": "v1.1.0"})
        self.assertEqual(receipt["previous_values"], {"FUTURE_AGI_VERSION": "v1.1.0"})


class StageBookkeepingTests(UpdaterCase):
    def test_failed_stage_journal_leaves_the_previous_stage(self):
        for failing, recorded in (("env_written", "prepare"), ("pulled", "env_written"), ("rollout", "pulled")):
            with self.subTest(failing=failing):
                self.build()
                self.state.journal_failures[failing] = StateError("cannot write journal")
                self.assertRaisesExactly(FailedBeforeRollout, self.apply)
                receipt = self.last_receipt()
                self.assertEqual((receipt["stage"], receipt["status"]), (recorded, "failed_before_rollout"))
                self.assertEqual(self.env_bytes(), self.original)
                self.assertIsNone(self.journal())

    def test_failed_rollout_journal_restarts_nothing_and_says_so(self):
        self.state.journal_failures["rollout"] = StateError("cannot write journal: No space left on device")
        exc = self.assertRaisesExactly(FailedBeforeRollout, self.apply)
        self.assertIn("stopped before restarting anything: cannot write journal: No space left on device", str(exc))
        self.assertNotIn(("wait_standalone",), self.log)
        self.assertNotIn("Restarting the stack", self.out.getvalue())
        self.assertEqual(self.inst.running["app"]["image_id"], id_of("futureagi/standalone", "v1.0.0"))

    def test_interrupt_writing_the_rollout_journal_of_a_rollback(self):
        self.applied()
        updated = self.env_bytes()
        self.state.journal_failures["rollout"] = KeyboardInterrupt()
        self.assertRaisesExactly(InterruptedRun, self.rollback)
        self.assertNotIn("up", [c[0] for c in self.compose_calls()])
        self.assertEqual(self.env_bytes(), updated)
        self.assertEqual(self.last_receipt()["status"], "interrupted_before_rollout")


# --------------------------------------------------------------- hook runner --

def process_gone(pid, timeout=10.0):
    """Whether `pid` has exited (a zombie waiting for its new parent counts as gone)."""

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        state = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True, check=False)
        if not state.stdout.strip() or state.stdout.strip().startswith("Z"):
            return True
        time.sleep(0.05)
    return False


def group_gone(pgid, timeout=10.0):
    """Whether every process of group `pgid` has exited (zombies waiting for their new parent count as gone)."""

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.killpg(pgid, 0)
        except ProcessLookupError:
            return True
        except PermissionError:
            pass  # On sandboxed macOS a dead group can give EPERM; verify through ps, never treat it as success.
        listing = subprocess.run(["ps", "-A", "-o", "pgid=,stat="], capture_output=True, text=True, check=False)
        if listing.returncode != 0:
            raise AssertionError('cannot verify hook process group: ' + listing.stderr)
        states = [line.split()[1] for line in listing.stdout.splitlines()
                  if len(line.split()) == 2 and line.split()[0] == str(pgid)]
        if all(state.startswith("Z") for state in states):
            return True
        time.sleep(0.05)
    return False


class RecordHookGroups:
    """Records the process group of every hook run_hook starts (its own session: the group id is the leader's pid).

    Whether a hook got as far as starting its child before a short timeout depends on how loaded the host is;
    its group is known either way, so the tests check that the whole group is gone.
    """

    def __init__(self):
        self.groups = []
        self.real = subprocess.Popen

    def __call__(self, *args, **kwargs):
        process = self.real(*args, **kwargs)
        if kwargs.get("start_new_session"):
            self.groups.append(process.pid)
        return process


class HookRunnerTests(unittest.TestCase):
    """The default hook runner: run_hook, with real processes."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.dir = Path(os.path.realpath(temporary.name))
        patcher = mock.patch.object(apply_module, "HOOK_STOP_GRACE_SECONDS", 1.0)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.pids = []
        self.addCleanup(self.kill_leftovers)
        self.recorder = RecordHookGroups()
        patcher = mock.patch.object(subprocess, "Popen", self.recorder)
        patcher.start()
        self.addCleanup(patcher.stop)

    def kill_leftovers(self):
        for pgid in self.recorder.groups:
            try:
                os.killpg(pgid, signal.SIGKILL)
            except OSError:
                pass
        for pid in self.pids:
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass

    def hook(self, *, ignore_term=False):
        """A hook that starts a background child and waits for it; it records both pids."""

        script = self.dir / "hook.sh"
        script.write_text(
            "#!/bin/sh\n"
            + ("trap '' TERM\n" if ignore_term else "")
            + f"sleep 300 &\necho $! > '{self.dir}/child.pid'\necho $$ > '{self.dir}/leader.pid'\n"
            f": > '{self.dir}/started'\nwait\n", encoding="utf-8")
        script.chmod(0o755)
        return [str(script)]

    def started(self, timeout=10.0):
        deadline = time.monotonic() + timeout
        while not (self.dir / "started").exists():
            if time.monotonic() > deadline:
                raise AssertionError("the hook did not start")
            time.sleep(0.02)
        pids = [int((self.dir / name).read_text().strip()) for name in ("leader.pid", "child.pid")]
        self.pids.extend(pids)
        return pids

    def signal_when_started(self, signum):
        def send():
            try:
                self.started()
            except AssertionError:
                return
            os.kill(os.getpid(), signum)

        thread = threading.Thread(target=send, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 10)

    def assertGroupGone(self):
        self.assertEqual(len(self.recorder.groups), 1, "run_hook starts one hook")
        self.assertTrue(group_gone(self.recorder.groups[0]), "a process of the hook's group outlived run_hook")
        if (self.dir / "started").exists():  # what it reported, when it got that far before being stopped
            for pid in self.started():
                self.assertTrue(process_gone(pid), f"hook process {pid} outlived run_hook")

    def test_returns_the_exit_code(self):
        result = run_hook(["/bin/sh", "-c", "exit 3"], cwd=str(self.dir), env=dict(os.environ), check=False,
                          timeout=30)
        self.assertEqual(result.returncode, 3)
        self.assertEqual(result.args, ["/bin/sh", "-c", "exit 3"])

    def test_check_raises_on_failure(self):
        with self.assertRaises(subprocess.CalledProcessError):
            run_hook(["/bin/sh", "-c", "exit 4"], cwd=str(self.dir), env=dict(os.environ), check=True, timeout=30)

    def test_cwd_and_environment_are_passed(self):
        env = dict(os.environ, FAGI_HOOK_TEST="from-the-updater")
        run_hook([sys.executable, "-c", "import os; open('out.txt', 'w').write(os.getcwd() + '|' + "
                  "os.environ['FAGI_HOOK_TEST'])"], cwd=str(self.dir), env=env, check=False, timeout=30)
        cwd, value = (self.dir / "out.txt").read_text().split("|")
        self.assertEqual(os.path.realpath(cwd), str(self.dir))
        self.assertEqual(value, "from-the-updater")

    def test_runs_in_its_own_session_and_process_group(self):
        run_hook([sys.executable, "-c", "import os; open('ids.txt', 'w').write("
                  "'%d %d %d' % (os.getpid(), os.getpgid(0), os.getsid(0)))"],
                 cwd=str(self.dir), env=dict(os.environ), check=False, timeout=30)
        pid, pgid, sid = map(int, (self.dir / "ids.txt").read_text().split())
        self.assertEqual((pgid, sid), (pid, pid))
        self.assertNotEqual(sid, os.getsid(0))
        self.assertNotEqual(pgid, os.getpgid(0))

    def test_missing_program_raises_oserror(self):
        with self.assertRaises(OSError):
            run_hook([str(self.dir / "no-such-hook")], cwd=str(self.dir), env=dict(os.environ), check=False,
                     timeout=30)

    def test_timeout_stops_the_whole_process_group(self):
        with self.assertRaises(subprocess.TimeoutExpired):
            run_hook(self.hook(), cwd=str(self.dir), env=dict(os.environ), check=False, timeout=1)
        self.assertGroupGone()

    def test_processes_ignoring_sigterm_are_killed_after_the_grace_period(self):
        # The hook ignores SIGTERM from its first instruction (an ignored signal stays ignored across fork and
        # exec), however long a loaded host takes to start it: only the SIGKILL after the grace stops it.
        previous = signal.signal(signal.SIGTERM, signal.SIG_IGN)
        try:
            started = time.monotonic()
            with self.assertRaises(subprocess.TimeoutExpired):
                run_hook(self.hook(ignore_term=True), cwd=str(self.dir), env=dict(os.environ), check=False,
                         timeout=1)
        finally:
            signal.signal(signal.SIGTERM, previous)
        self.assertGreaterEqual(time.monotonic() - started, 1.9)  # the timeout, then the grace period
        self.assertGroupGone()

    def test_ctrl_c_stops_the_whole_process_group_and_propagates(self):
        previous = signal.signal(signal.SIGINT, signal.default_int_handler)
        self.addCleanup(signal.signal, signal.SIGINT, previous)
        self.signal_when_started(signal.SIGINT)
        with self.assertRaises(KeyboardInterrupt):
            run_hook(self.hook(), cwd=str(self.dir), env=dict(os.environ), check=False, timeout=60)
        self.assertGroupGone()

    def test_sigterm_stops_the_whole_process_group_and_propagates(self):
        self.signal_when_started(signal.SIGTERM)
        with self.assertRaises(Interrupted):
            with signals_as_interrupt():
                run_hook(self.hook(), cwd=str(self.dir), env=dict(os.environ), check=False, timeout=60)
        self.assertGroupGone()

    def test_updater_uses_it_by_default(self):
        updater = Updater(mock.Mock(), mock.Mock(), mock.Mock(), out=io.StringIO(), readiness=mock.Mock())
        self.assertIs(updater.hook_runner, run_hook)


class DefaultHookRunnerTests(UpdaterCase):
    def test_hanging_hook_is_stopped_and_nothing_changes(self):
        marker = self.root / "hook-child.pid"
        script = self.root / "backup.sh"
        script.write_text(f"#!/bin/sh\nsleep 300 &\necho $! > '{marker}'\nwait\n", encoding="utf-8")
        script.chmod(0o755)
        settings = load_settings(self.root, {"FUTURE_AGI_UPDATE_BACKUP_TIMEOUT_SECONDS": "1"}, environ={})
        updater = Updater(self.inst, settings, self.state, out=self.out, readiness=self.ready)
        recorder = RecordHookGroups()
        with mock.patch.object(apply_module, "HOOK_STOP_GRACE_SECONDS", 1.0), \
                mock.patch.object(subprocess, "Popen", recorder):
            exc = self.assertRaisesExactly(FailedBeforeRollout, self.apply, updater=updater,
                                           backup_command=str(script))
        self.assertEqual(str(exc), "the backup hook did not finish within 1s; nothing was changed")
        self.assertEqual(len(recorder.groups), 1)
        self.assertTrue(group_gone(recorder.groups[0]), "a process of the hook's group outlived it")
        child = marker.read_text().strip() if marker.exists() else ""
        if child:  # the hook got as far as starting its child before the timeout
            self.assertTrue(process_gone(int(child)))
        self.assertEqual(self.env_bytes(), self.original)
        self.assertEqual(self.receipts(), [])
        self.assertEqual(self.compose_calls(), [])


class BackupCommandParsingTests(UpdaterCase):
    def assertNothingChanged(self):
        self.assertEqual(self.hook.calls, [])
        self.assertEqual(self.env_bytes(), self.original)
        self.assertIsNone(self.journal())
        self.assertEqual(self.receipts(), [])
        self.assertEqual(self.compose_calls(), [])

    def test_unparseable_cli_command_names_the_flag(self):
        exc = self.assertRaisesExactly(Blocked, self.apply, backup_command="/opt/backup/run --label 'nightly")
        self.assertEqual(exc.exit_code, 3)
        self.assertEqual(str(exc), "FUTURE_AGI_UPDATE_BACKUP_COMMAND (from --backup-command) cannot be parsed "
                                   "(No closing quotation). Fix its quoting; nothing was changed")
        self.assertNothingChanged()

    def test_unparseable_setting_names_where_it_is_set(self):
        (self.root / "update.conf").write_text("FUTURE_AGI_UPDATE_BACKUP_COMMAND=/opt/backup/run --to 'b\n",
                                               encoding="utf-8")
        exc = self.assertRaisesExactly(Blocked, self.apply, backup_command=None)
        self.assertIn("FUTURE_AGI_UPDATE_BACKUP_COMMAND (from update.conf) cannot be parsed (No closing quotation)",
                      str(exc))
        self.assertNothingChanged()

    def test_trailing_escape_is_a_clean_error(self):
        exc = self.assertRaisesExactly(Blocked, self.apply, backup_command="/opt/backup/run \\")
        self.assertIn("(No escaped character)", str(exc))
        self.assertNothingChanged()

    def test_dry_run_does_not_parse_the_command(self):
        self.assertIsNone(self.apply(backup_command="/opt/backup/run 'oops", dry_run=True))


# ------------------------------------------------------ readiness settings --

class ReadinessSettingsTests(UpdaterCase):
    BAD = (("INSTALL_READY_TIMEOUT_SECONDS", "59", "[60,1800]"), ("INSTALL_READY_TIMEOUT_SECONDS", "ten", "[60,1800]"),
           ("INSTALL_STABILITY_SECONDS", "121", "[5,120]"), ("INSTALL_READY_MAX_SECONDS", "299", "[300,7200]"),
           ("INSTALL_READY_MAX_SECONDS", "1.5", "[300,7200]"))

    def set_env_line(self, key, value):
        # The last definition wins, as in Compose.
        self.env_path.write_bytes(self.env_bytes() + f"{key}={value}\n".encode())
        self.original = self.env_bytes()
        self.inst.env = read_env(self.env_path)

    def test_bad_value_blocks_before_the_backup_or_any_change(self):
        for key, value, bounds in self.BAD:
            with self.subTest(key=key, value=value):
                self.build()
                self.set_env_line(key, value)
                exc = self.assertRaisesExactly(Blocked, self.apply)
                self.assertEqual(str(exc), f"{key} must be an integer in {bounds} (got {value!r}) in .env. Fix it and "
                                           "run bin/update again; nothing was changed")
                self.assertEqual(self.hook.calls, [])
                self.assertEqual(self.env_bytes(), self.original)
                self.assertIsNone(self.journal())
                self.assertEqual(self.receipts(), [])
                self.assertEqual(self.compose_calls(), [])

    def test_dry_run_reports_it_too(self):
        self.set_env_line("INSTALL_STABILITY_SECONDS", "0")
        exc = self.assertRaisesExactly(Blocked, self.apply, dry_run=True)
        self.assertIn("INSTALL_STABILITY_SECONDS must be an integer in [5,120] (got '0')", str(exc))
        self.assertNotIn("Dry run", self.out.getvalue())

    def test_bounds_are_accepted(self):
        for key, value in (("INSTALL_READY_TIMEOUT_SECONDS", "60"), ("INSTALL_READY_TIMEOUT_SECONDS", "1800"),
                           ("INSTALL_STABILITY_SECONDS", "5"), ("INSTALL_READY_MAX_SECONDS", "7200")):
            with self.subTest(key=key, value=value):
                self.build()
                self.set_env_line(key, value)
                self.apply()
                self.assertEqual(self.last_receipt()["status"], "succeeded")

    def test_distributed_and_production_do_not_use_them(self):
        for mode in ("distributed", "production"):
            with self.subTest(mode=mode):
                self.build(mode)
                self.set_env_line("INSTALL_READY_TIMEOUT_SECONDS", "1")
                self.apply()
                self.assertEqual(self.last_receipt()["status"], "succeeded")

    def test_rollback_is_blocked_before_anything_runs(self):
        self.applied()
        self.set_env_line("INSTALL_READY_MAX_SECONDS", "99999")
        exc = self.assertRaisesExactly(Blocked, self.rollback)
        self.assertIn("INSTALL_READY_MAX_SECONDS must be an integer in [300,7200] (got '99999') in .env", str(exc))
        self.assertEqual(self.env_bytes(), self.original)
        self.assertEqual(self.compose_calls(), [])
        self.assertIsNone(self.journal())
        self.assertEqual(len(self.receipts()), 1)


# ------------------------------------------------- readiness: app containers --

class ReadinessHandoffTests(UpdaterCase):
    def test_apply_passes_the_app_containers_from_before_up(self):
        self.apply()
        self.assertEqual(self.ready.previous_ids, [["c-app"]])

    def test_every_existing_app_container_is_passed(self):
        self.inst.extra_containers.append({
            "id": "c-app-old", "service": "app", "image_id": id_of("futureagi/standalone", "v1.0.0"),
            "image": "futureagi/standalone:v1.0.0", "status": "exited", "exit_code": 0, "restart_count": 0,
            "health": "none", "started_at": "", "command": []})
        self.inst.extra_containers.append({
            "id": "c-postgres", "service": "postgres", "image_id": "sha256:" + "7" * 64, "image": "postgres:16",
            "status": "running", "exit_code": 0, "restart_count": 0, "health": "healthy", "started_at": "",
            "command": []})
        self.inst.up_skip.add("app")
        self.inst.failures["up"] = lambda *a: self.inst.extra_containers.pop(0)
        self.assertRaisesExactly(FailedDuringRollout, self.apply)  # app stays on the old image here
        self.assertEqual(self.ready.previous_ids, [["c-app", "c-app-old"]])

    def test_rollback_passes_them_too(self):
        self.applied()
        self.rollback()
        self.assertEqual(self.ready.previous_ids, [["c-app"], ["c-app"]])

    def test_resumed_apply_passes_the_current_app_container(self):
        self.ready.failure = NotReady("the app container keeps restarting")
        self.assertRaisesExactly(FailedDuringRollout, self.apply)
        self.ready.failure = None
        self.apply(self.plan(env_changes={}, resume=True, current=["v1.1.0"], action="keep"))
        self.assertEqual(self.ready.previous_ids, [["c-app"], ["c-app"]])

    def test_distributed_waits_on_the_api(self):
        self.build("distributed")
        self.apply()
        self.assertEqual(self.ready.previous_ids, [])
        self.assertIn(("wait_api",), self.log)


# ------------------------------------------------------- production hints --

class ProductionHintTests(UpdaterCase):
    def use_env_file(self, relative):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(self.original)
        self.inst.env_file = path
        self.env_path = self.state.env_path = self.hook.env_path = path
        self.inst.env = read_env(path)

    def test_failed_rollout_hints_carry_the_production_flag(self):
        self.build("production")
        self.inst.failures["up"] = InstallError("`docker compose up` failed with exit code 1")
        exc = self.assertRaisesExactly(FailedDuringRollout, self.apply)
        path = self.state.receipts()[-1]
        self.assertIn("To finish, run `./bin/update --production apply` again for v1.1.0", str(exc))
        self.assertIn(f"To go back, run `./bin/update --production rollback --receipt {path} --yes`", str(exc))

    def test_a_custom_production_env_file_is_named(self):
        self.build("production")
        self.use_env_file("deploy/prod-eu.env")
        self.inst.failures["up"] = InstallError("`docker compose up` failed with exit code 1")
        exc = self.assertRaisesExactly(FailedDuringRollout, self.apply)
        path = self.state.receipts()[-1]
        self.assertIn("`./bin/update --production --env-file deploy/prod-eu.env apply`", str(exc))
        self.assertIn(f"`./bin/update --production --env-file deploy/prod-eu.env rollback --receipt {path} --yes`",
                      str(exc))

    def test_failed_rollback_hint_carries_the_production_flag(self):
        self.build("production")
        applied = self.applied()
        self.inst.failures["up"] = InstallError("`docker compose up` failed with exit code 1")
        exc = self.assertRaisesExactly(FailedDuringRollout, self.rollback)
        self.assertIn(f"rerun `./bin/update --production rollback --receipt {Path(applied).resolve()} --yes`",
                      str(exc))

    def test_non_production_hints_are_unchanged(self):
        self.build("distributed")
        self.inst.failures["up"] = InstallError("`docker compose up` failed with exit code 1")
        exc = self.assertRaisesExactly(FailedDuringRollout, self.apply)
        self.assertIn("To finish, run `./bin/update apply` again", str(exc))
        self.assertIn("`./bin/update rollback --receipt ", str(exc))
        self.assertNotIn("--production", str(exc))


# ---------------------------------------- rollback: local and inactive keys --

RUNNER = "SIMULATION_RUNNER_VERSION"


def inactive_item(target=TARGET):
    """A component pinned for a Compose profile that is not running (no services)."""

    repo = "futureagi/future-agi-simulation-runner"
    return {"component": "simulation_runner", "variable": RUNNER, "services": [], "configured": "",
            "current_version": None, "current_source": "", "current_tags": [], "target_tag": target,
            "target_image": f"{repo}:{target}", "target_digest": digest_of(repo, target), "action": "update",
            "inactive": True}


class RollbackLocalAndInactiveTests(UpdaterCase):
    GROUPS = (BACKEND, FI_COLLECTOR, FRONTEND)

    def setUp(self):
        super().setUp()
        self.build("distributed", groups=self.GROUPS, current="v1.1.0")
        self.local_id = self.local_image(FI_COLLECTOR.repo)

    def components(self, tags=None, groups=None, extra=()):
        tags = tags or {}
        items = [{"component": g.component, "variable": g.variable, "services": list(g.services),
                  "target_tag": TARGET, "target_digest": digest_of(g.repo, TARGET), "current_version": "v1.0.0",
                  "current_tags": list(tags.get(g.component, ["v1.0.0"]))} for g in groups or self.GROUPS]
        return items + [dict(item) for item in extra]

    def previous_images(self, collector=None):
        images = self.snapshot_of("v1.0.0", (BACKEND, FRONTEND))
        images["fi-collector"] = collector or {"image": f"{FI_COLLECTOR.repo}:local", "image_id": self.local_id,
                                               "repo_digests": [], "version": ""}
        return images

    def craft(self, collector_value=None, tags=None, **fields):
        fields.setdefault("components", self.components(tags if tags is not None else {"fi_collector": ["local"]}))
        fields.setdefault("previous_images", self.previous_images())
        return self.craft_apply(previous_values={"FUTURE_AGI_VERSION": "v1.0.0", "FRONTEND_VERSION": "v1.0.0",
                                                 "FI_COLLECTOR_VERSION": collector_value}, **fields)

    def test_unset_collector_that_ran_the_local_build_is_unset_again(self):
        self.craft(None)
        self.rollback()
        self.assertIsNone(self.env_value("FI_COLLECTOR_VERSION"))
        self.assertNotIn(b"FI_COLLECTOR_VERSION", self.env_bytes())
        self.assertEqual(self.env_value("FUTURE_AGI_VERSION"), "v1.0.0")
        self.assertEqual(self.inst.running["fi-collector"]["image_id"], self.local_id)
        self.assertEqual([c for c in self.compose_calls() if c[0] == "pull"], [("pull", "backend", "frontend", "worker")])
        receipt = self.last_receipt()
        self.assertEqual(receipt["status"], "rolled_back")
        self.assertEqual(receipt["env_changes"]["FI_COLLECTOR_VERSION"], None)
        self.assertEqual(receipt["target_version"], "v1.0.0")
        self.assertEqual(receipt["target_image_ids"]["fi-collector"], self.local_id)
        self.assertIn("FI_COLLECTOR_VERSION unset (selects :local)", self.out.getvalue())

    def test_empty_collector_value_is_written_back_empty(self):
        self.craft("")
        self.rollback()
        self.assertIn(b"\nFI_COLLECTOR_VERSION=\n", self.env_bytes())
        self.assertEqual(self.env_value("FI_COLLECTOR_VERSION"), "")
        self.assertEqual(self.inst.running["fi-collector"]["image_id"], self.local_id)
        self.assertIn("FI_COLLECTOR_VERSION= (selects :local)", self.out.getvalue())

    def test_receipt_without_current_tags_falls_back_to_the_image_that_ran(self):
        components = self.components()
        for item in components:
            del item["current_tags"]
        self.craft(None, components=components)
        self.rollback()
        self.assertIsNone(self.env_value("FI_COLLECTOR_VERSION"))
        self.assertEqual(self.inst.running["fi-collector"]["image_id"], self.local_id)

    def test_unset_value_that_followed_latest_is_still_pinned(self):
        self.build(current="v1.1.0")
        components = [{"component": "standalone", "variable": "FUTURE_AGI_VERSION", "services": ["app"],
                       "target_tag": TARGET, "target_digest": digest_of("futureagi/standalone", TARGET),
                       "current_version": "v1.0.0", "current_tags": ["latest"]}]
        self.craft_apply(previous_values={"FUTURE_AGI_VERSION": None}, components=components)
        self.rollback()
        self.assertEqual(self.env_value("FUTURE_AGI_VERSION"), "v1.0.0")

    def test_unset_value_with_a_mixed_history_is_not_guessed(self):
        images = self.previous_images()
        self.craft(None, tags={"fi_collector": ["local", "latest"]}, previous_images=images)
        exc = self.assertRaisesExactly(Blocked, self.rollback)
        self.assertIn("cannot tell which release FI_COLLECTOR_VERSION ran before the update (it was unset)", str(exc))
        self.assertEqual(self.env_bytes(), self.original)

    def test_the_restored_default_must_resolve_to_local(self):
        self.craft(None)
        self.inst.image_overrides["fi-collector"] = f"{FI_COLLECTOR.repo}:v1.1.0"
        self.assertRaisesExactly(FailedBeforeRollout, self.rollback)
        self.assertIn("service fi-collector resolves to futureagi/fi-collector:v1.1.0, not tag local",
                      self.last_receipt()["error"])
        self.assertEqual(self.env_bytes(), self.original)

    def test_local_build_rebuilt_since_is_refused(self):
        self.craft(None)
        rebuilt = "sha256:" + "6" * 64
        self.inst.metadata[rebuilt] = {"repo_digests": [], "version": ""}
        self.inst.local_images[f"{FI_COLLECTOR.repo}:local"] = rebuilt
        self.assertRaisesExactly(FailedBeforeRollout, self.rollback)
        self.assertIn(f"futureagi/fi-collector:local is not the image that ran before the update ({self.local_id})",
                      self.last_receipt()["error"])
        self.assertEqual(self.env_bytes(), self.original)

    def test_failed_rollback_puts_a_removed_key_back_in_place(self):
        # The restore re-adds the removed key; the original bytes (its place
        # in the file included) come back, and the file mode is kept.
        os.chmod(self.env_path, 0o640)
        self.craft(None)
        self.inst.failures["pull"] = InstallError("`docker compose pull` failed: network unreachable")
        exc = self.assertRaisesExactly(FailedBeforeRollout, self.rollback)
        self.assertIn("Nothing was changed", str(exc))
        self.assertEqual(self.env_bytes(), self.original)
        self.assertEqual(stat.S_IMODE(self.env_path.stat().st_mode), 0o640)
        self.assertEqual([p.name for p in self.root.iterdir() if p.name.startswith(".env.futureagi-update-")], [])
        self.assertEqual(self.inst.env["FI_COLLECTOR_VERSION"], "v1.1.0")

    def test_symlinked_env_file_is_put_back_at_its_target(self):
        real = self.root / "config" / "fagi.env"
        real.parent.mkdir()
        real.write_bytes(self.original)
        self.env_path.unlink()
        self.env_path.symlink_to(real)
        self.craft(None)
        self.inst.failures["pull"] = InstallError("pull failed")
        self.assertRaisesExactly(FailedBeforeRollout, self.rollback)
        self.assertTrue(self.env_path.is_symlink())
        self.assertEqual(real.read_bytes(), self.original)

    def test_an_edit_made_meanwhile_is_not_overwritten(self):
        self.craft(None)

        def edit_then_fail(*args):
            self.env_path.write_bytes(self.env_bytes() + b"ADDED_BY_THE_OPERATOR=1\n")
            raise InstallError("pull failed")

        self.inst.failures["pull"] = edit_then_fail
        exc = self.assertRaisesExactly(UpdateError, self.rollback)
        self.assertIn('env commit conflict', str(exc))
        self.assertIn('Kept', str(exc))
        self.assertIn(b'ADDED_BY_THE_OPERATOR=1', self.env_bytes())
        self.assertIsNotNone(self.journal())
        before = self.env_bytes(), self.state.journal_path.read_bytes(), self.receipts()
        with self.assertRaises(StateError):
            self.updater().recover()
        self.assertEqual((self.env_bytes(), self.state.journal_path.read_bytes(), self.receipts()), before)

    def test_containerd_local_build_with_its_own_repo_digest_is_accepted(self):
        own = [f"{FI_COLLECTOR.repo}@{self.local_id}"]
        self.inst.metadata[self.local_id]["repo_digests"] = list(own)
        self.craft(None, previous_images=self.previous_images(
            {"image": f"{FI_COLLECTOR.repo}:local", "image_id": self.local_id, "repo_digests": list(own),
             "version": ""}))
        self.rollback()
        self.assertEqual(self.last_receipt()["status"], "rolled_back")

    def test_local_build_matching_by_image_id_only_is_accepted(self):
        self.inst.metadata[self.local_id]["repo_digests"] = [f"{FI_COLLECTOR.repo}@sha256:{'5' * 64}"]
        self.craft(None, previous_images=self.previous_images(
            {"image": f"{FI_COLLECTOR.repo}:local", "image_id": self.local_id,
             "repo_digests": [f"{FI_COLLECTOR.repo}@sha256:{'4' * 64}"], "version": ""}))
        self.rollback()
        self.assertEqual(self.last_receipt()["status"], "rolled_back")

    def test_containerd_rebuilt_local_image_is_refused(self):
        rebuilt = "sha256:" + "6" * 64
        self.inst.metadata[rebuilt] = {"repo_digests": [f"{FI_COLLECTOR.repo}@{rebuilt}"], "version": ""}
        self.inst.local_images[f"{FI_COLLECTOR.repo}:local"] = rebuilt
        self.craft(None, previous_images=self.previous_images(
            {"image": f"{FI_COLLECTOR.repo}:local", "image_id": self.local_id,
             "repo_digests": [f"{FI_COLLECTOR.repo}@{self.local_id}"], "version": ""}))
        self.assertRaisesExactly(FailedBeforeRollout, self.rollback)

    def test_apply_records_the_tags_that_ran(self):
        self.build("distributed", groups=self.GROUPS, current="v1.0.0",
                   values={"FUTURE_AGI_VERSION": "v1.0.0", "FRONTEND_VERSION": "v1.0.0"})
        self.applied()
        tags = {item["component"]: item["current_tags"] for item in self.last_receipt()["components"]}
        self.assertEqual(tags, {"backend": ["v1.0.0"], "fi_collector": ["local"], "frontend": ["v1.0.0"]})
        self.assertEqual(self.last_receipt()["previous_values"]["FI_COLLECTOR_VERSION"], None)
        self.rollback()
        self.assertEqual(self.env_bytes(), self.original)
        self.assertNotIn("fi-collector", [s for c in self.compose_calls() if c[0] == "pull" for s in c[1:]])

    def test_inactive_pin_is_put_back_as_it_was(self):
        for previous, expected in ((None, None), ("", ""), ("latest", "latest"), ("v1.0.0", "v1.0.0")):
            with self.subTest(previous=previous):
                self.build("distributed", current="v1.1.0",
                           values={"FUTURE_AGI_VERSION": "v1.1.0", "FRONTEND_VERSION": "v1.1.0", RUNNER: "v1.1.0"})
                self.craft_apply(previous_values={"FUTURE_AGI_VERSION": "v1.0.0", "FRONTEND_VERSION": "v1.0.0",
                                                  RUNNER: previous},
                                 env_changes={"FUTURE_AGI_VERSION": TARGET, "FRONTEND_VERSION": TARGET, RUNNER: TARGET},
                                 components=self.components(groups=DISTRIBUTED, extra=[inactive_item()]),
                                 previous_images=self.snapshot_of("v1.0.0"))
                self.rollback()
                self.assertEqual(self.env_value(RUNNER), expected)
                self.assertEqual(self.env_value("FUTURE_AGI_VERSION"), "v1.0.0")
                receipt = self.last_receipt()
                self.assertEqual((receipt["status"], receipt["target_version"]), ("rolled_back", "v1.0.0"))
                self.assertEqual(receipt["env_changes"][RUNNER], previous)
                touched = [e for e in self.log if e[0] in ("image", "compose") and "simulation" in str(e)]
                self.assertEqual(touched, [])

    def test_apply_pins_an_inactive_component_without_pulling_it(self):
        self.build("distributed")
        plan = self.plan()
        plan["components"].append(inactive_item())
        plan["env_changes"][RUNNER] = TARGET
        self.applied(plan)
        self.assertEqual(self.env_value(RUNNER), TARGET)
        receipt = self.last_receipt()
        self.assertEqual(receipt["status"], "succeeded")
        self.assertEqual(receipt["previous_values"][RUNNER], None)
        self.assertEqual([i["services"] for i in receipt["components"] if i["component"] == "simulation_runner"], [[]])
        self.prune("v1.0.0")  # the active services' images must be pulled again
        self.rollback()
        self.assertEqual(self.env_bytes(), self.original)
        self.assertEqual([c for c in self.compose_calls() if c[0] == "pull"], [("pull", "backend", "frontend", "worker")])


# ------------------------------------------- a resume that stopped early --

class StoppedResumeTests(UpdaterCase):
    """A resume that stopped before restarting anything leaves the update it resumed as the one to finish."""

    def stopped_retry(self, failure):
        """A rollout that failed, then a resume of it that `failure` stops at the pull; returns both receipts."""

        self.ready.failure = NotReady("the app container keeps restarting")
        self.assertRaisesExactly(FailedDuringRollout, self.apply)
        self.ready.failure = None
        stopped = self.state.receipts()[-1]
        self.prune(TARGET)  # pulled only when it is not on the host
        self.inst.failures["pull"] = failure
        with self.assertRaises(BaseException):
            self.apply(self.resume_plan())
        del self.inst.failures["pull"]
        if self.journal():
            self.updater().recover()
        retry = self.state.receipts()[-1]
        self.assertNotEqual(retry, stopped)
        del self.log[:]
        self.plans_seen = []
        return stopped, retry

    def resume_plan(self):
        return self.plan(env_changes={}, resume=True, current=["v1.1.0"], action="keep")

    def test_failed_retry_records_the_update_it_resumed(self):
        stopped, _ = self.stopped_retry(InstallError("`docker compose pull` failed: network unreachable"))
        receipt = self.last_receipt()
        self.assertEqual((receipt["status"], receipt["resumes"]), ("failed_before_rollout", str(stopped)))
        self.assertEqual(receipt["previous_values"], {"FUTURE_AGI_VERSION": "v1.0.0"})

    def test_finishing_after_a_failed_retry_resumes_the_stopped_update(self):
        stopped, retry = self.stopped_retry(InstallError("`docker compose pull` failed: network unreachable"))
        self.apply(self.resume_plan())
        self.assertEqual((self.plans_seen[0]["status"], self.plans_seen[0]["_path"]),
                         ("failed_before_rollout", str(retry)))
        receipt = self.last_receipt()
        self.assertEqual(receipt["status"], "succeeded")
        self.assertEqual(receipt["resumes"], str(stopped))
        self.assertEqual(receipt["previous_values"], {"FUTURE_AGI_VERSION": "v1.0.0"})
        self.assertEqual(receipt["previous_images"]["app"]["image_id"], id_of("futureagi/standalone", "v1.0.0"))
        self.rollback()
        self.assertEqual(self.env_bytes(), self.original)
        self.assertEqual(self.inst.running["app"]["image_id"], id_of("futureagi/standalone", "v1.0.0"))

    def test_finishing_after_a_killed_retry_resumes_the_stopped_update(self):
        stopped, retry = self.stopped_retry(SystemExit(137))
        self.assertEqual(self.receipts()[-1]["status"], "interrupted_before_rollout")
        self.apply(self.resume_plan())
        self.assertEqual(self.plans_seen[0]["_path"], str(retry))
        self.assertEqual(self.last_receipt()["resumes"], str(stopped))

    def test_rollback_after_a_failed_retry_undoes_the_stopped_update(self):
        stopped, _ = self.stopped_retry(KeyboardInterrupt())
        self.assertEqual(self.receipts()[-1]["status"], "interrupted_before_rollout")
        self.rollback()
        receipt = self.last_receipt()
        self.assertEqual((receipt["kind"], receipt["rolls_back"]), ("rollback", str(stopped.resolve())))
        self.assertEqual(self.env_bytes(), self.original)
        self.assertEqual(self.inst.running["app"]["image_id"], id_of("futureagi/standalone", "v1.0.0"))


# ------------------------------------------------------------- state files --

class StateDirFileTests(unittest.TestCase):
    """receipt.StateDir on disk: every failure is a StateError bin/update reports, never a traceback."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(os.path.realpath(temporary.name))
        self.state = StateDir(self.root)

    def leftovers(self):
        return sorted(p.name for p in self.state.path.iterdir() if p.name.endswith(".tmp"))

    def test_failed_write_is_a_state_error_and_leaves_no_temp_file(self):
        for step in ("os.fsync", "os.replace"):
            with self.subTest(step=step):
                with mock.patch(step, side_effect=OSError(28, "No space left on device")):
                    with self.assertRaises(StateError) as ctx:
                        self.state.write_journal({"kind": "apply", "stage": "prepare"})
                self.assertEqual(str(ctx.exception),
                                 f"cannot write {self.state.journal_path}: [Errno 28] No space left on device")
                self.assertEqual(self.leftovers(), [])
                self.assertIsNone(self.state.read_journal())

    def test_failed_receipt_write_is_a_state_error(self):
        with mock.patch("os.replace", side_effect=PermissionError(13, "Permission denied")):
            with self.assertRaises(StateError) as ctx:
                self.state.write_receipt({"kind": "apply", "status": "succeeded"})
        self.assertIn("Permission denied", str(ctx.exception))
        self.assertEqual(self.state.receipts(), [])
        self.assertEqual(self.leftovers(), [])

    def test_unopenable_temp_file_is_a_state_error(self):
        self.state.ensure()
        with mock.patch("os.open", side_effect=PermissionError(13, "Permission denied")):
            with self.assertRaises(StateError) as ctx:
                self.state.write_journal({"kind": "apply"})
        self.assertEqual(str(ctx.exception), f"cannot write {self.state.journal_path}: [Errno 13] Permission denied")

    def test_interrupt_while_writing_propagates_and_cleans_up(self):
        with mock.patch("os.replace", side_effect=KeyboardInterrupt()):
            with self.assertRaises(KeyboardInterrupt):
                self.state.write_journal({"kind": "apply"})
        self.assertEqual(self.leftovers(), [])

    def test_temp_file_removed_by_someone_else_is_still_reported(self):
        def vanish(source, target):
            os.unlink(source)
            raise OSError(5, "Input/output error")

        with mock.patch("os.replace", side_effect=vanish):
            with self.assertRaises(StateError):
                self.state.write_journal({"kind": "apply"})
        self.assertEqual(self.leftovers(), [])

    def test_journal_that_is_not_an_object(self):
        self.state.ensure()
        self.state.journal_path.write_text("[1, 2]\n", encoding="utf-8")
        with self.assertRaises(StateError) as ctx:
            self.state.read_journal()
        self.assertEqual(str(ctx.exception), f"{self.state.journal_path} is not a JSON object")

    def test_state_directory_that_cannot_be_created(self):
        (self.root / ".futureagi-updates").write_text("a file, not a directory\n", encoding="utf-8")
        with self.assertRaises(StateError) as ctx:
            self.state.ensure()
        self.assertTrue(str(ctx.exception).startswith(f"cannot create {self.state.path}: "), str(ctx.exception))

    def test_lock_needs_fcntl(self):
        real_import = __import__

        def no_fcntl(name, *args, **kwargs):
            if name == "fcntl":
                raise ImportError("No module named 'fcntl'")
            return real_import(name, *args, **kwargs)

        with mock.patch("builtins.__import__", side_effect=no_fcntl):
            with self.assertRaises(StateError) as ctx:
                with self.state.lock():
                    self.fail("the lock was taken without fcntl")
        self.assertEqual(str(ctx.exception), "bin/update needs a POSIX system (Linux, macOS or WSL)")

    def test_clearing_a_missing_journal_is_fine(self):
        self.state.ensure()
        self.state.clear_journal()
        self.assertIsNone(self.state.read_journal())

    def test_journal_that_cannot_be_removed_is_a_state_error(self):
        self.state.write_journal({"kind": "apply"})
        with mock.patch.object(Path, "unlink", side_effect=PermissionError(13, "Permission denied")):
            with self.assertRaises(StateError) as ctx:
                self.state.clear_journal()
        self.assertEqual(str(ctx.exception), f"cannot remove {self.state.journal_path}: [Errno 13] Permission denied")


# --------------------------------------------------- putting the bytes back --




# ---------------------------------------------------- stopping a hook group --

class StopProcessGroupTests(unittest.TestCase):
    """apply._stop_process_group when the group is already gone, or goes away before SIGKILL."""

    def test_group_already_gone(self):
        process = mock.Mock(pid=4242)
        with mock.patch("os.killpg", side_effect=ProcessLookupError()) as killpg:
            apply_module._stop_process_group(process, 1.0)
        killpg.assert_called_once_with(4242, signal.SIGTERM)
        process.wait.assert_called_once_with(timeout=apply_module.HOOK_REAP_SECONDS)

    def test_group_gone_before_sigkill(self):
        process = mock.Mock(pid=4242)
        process.poll.return_value = None
        sent = []

        def killpg(pid, signum):
            sent.append(signum)
            if signum == signal.SIGKILL:
                raise ProcessLookupError()

        with mock.patch("os.killpg", side_effect=killpg):
            apply_module._stop_process_group(process, 0.0)
        self.assertEqual(sent, [signal.SIGTERM, signal.SIGKILL])
        process.wait.assert_called_once_with(timeout=apply_module.HOOK_REAP_SECONDS)

    def test_reap_falls_back_to_waitpid(self):
        process = mock.Mock(pid=4242)
        process.wait.side_effect = subprocess.TimeoutExpired(["hook"], apply_module.HOOK_REAP_SECONDS)
        with mock.patch("os.waitpid", return_value=(4242, 9)) as waitpid:
            apply_module._reap(process)
        waitpid.assert_called_once_with(4242, 0)
        with mock.patch("os.waitpid", side_effect=ChildProcessError()):
            apply_module._reap(process)  # collected by Popen meanwhile: nothing left to reap

    def test_stranded_popen_lock_does_not_hang(self):
        # An interrupt can land inside Popen.wait(timeout) after it took its internal lock and before
        # it could release it. The hook must still be stopped and reaped, and bin/update must not hang.
        process = subprocess.Popen(["/bin/sh", "-c", "sleep 60"], start_new_session=True)
        self.addCleanup(self.cleanup, process)
        lock = getattr(process, "_waitpid_lock", None)
        if lock is None:
            self.skipTest("this Popen has no internal waitpid lock")
        lock.acquire()
        outcome = {}

        def stop():
            try:
                apply_module._stop_process_group(process, 0.2)
            except BaseException as exc:  # noqa: BLE001 - reported below
                outcome["error"] = exc

        with mock.patch.object(apply_module, "HOOK_REAP_SECONDS", 0.5):
            thread = threading.Thread(target=stop, daemon=True)
            thread.start()
            thread.join(20)
        self.assertFalse(thread.is_alive(), "stopping the hook blocked on Popen's lock")
        self.assertIsNone(outcome.get("error"))
        with self.assertRaises(ChildProcessError):  # reaped: no zombie left behind
            os.waitpid(process.pid, os.WNOHANG)
        lock.release()

    @staticmethod
    def cleanup(process):
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except OSError:
            pass
        try:
            os.waitpid(process.pid, 0)
        except ChildProcessError:
            pass
        process.returncode = process.returncode if process.returncode is not None else -9


# ------------------------------------------------- dry run after a killed run --

class DryRunAfterAKilledRunTests(UpdaterCase):
    """apply --dry-run never recovers, but plans as the recovery the real apply starts with would leave things."""

    def killed(self, verb):
        self.inst.failures[verb] = SystemExit(137)
        with self.assertRaises(SystemExit):
            self.apply()
        del self.inst.failures[verb]
        del self.log[:]
        self.plans_seen = []
        self.out = io.StringIO()
        return self.env_bytes()

    def dry_run(self, plan=None):
        plan = plan or self.plan()
        seen = {}

        def make_plan(history):
            seen.update(last=history[-1] if history else None, history=list(history), env=dict(self.inst.env),
                        overlay=dict(self.inst.env_overlay))
            return plan

        self.updater().apply(make_plan, {"version": plan["target_version"]}, backup_command=BACKUP, dry_run=True)
        return seen, plan

    def test_killed_before_the_restart_plans_with_the_values_recovery_puts_back(self):
        left = self.killed("pull")
        self.assertEqual(self.env_value("FUTURE_AGI_VERSION"), "v1.1.0")
        backups = len(self.hook.calls)
        seen, plan = self.dry_run()
        self.assertEqual(seen["env"]["FUTURE_AGI_VERSION"], "v1.0.0")
        self.assertEqual(seen["overlay"], {"FUTURE_AGI_VERSION": "v1.0.0"})
        self.assertEqual((seen["last"]["status"], seen["last"]["stage"]), ("interrupted_before_rollout", "env_written"))
        self.assertEqual(plan["notes"], [
            apply_module.JOURNAL_NOTE,
            "that run stopped before restarting anything; this plan assumes recovery has put back "
            "FUTURE_AGI_VERSION=v1.0.0"])
        self.assertIn(f"· {apply_module.JOURNAL_NOTE}", self.out.getvalue())
        self.assertIn("Dry run: nothing was changed.", self.out.getvalue())
        # Nothing was recovered or written.
        self.assertEqual(self.env_bytes(), left)
        self.assertEqual(self.journal()["stage"], "env_written")
        self.assertEqual(self.receipts(), [])
        self.assertEqual(len(self.hook.calls), backups)

    def test_it_plans_what_the_real_apply_plans_after_recovering(self):
        self.killed("pull")
        seen, _ = self.dry_run()
        self.apply()
        self.assertEqual(self.plans_seen[0]["status"], seen["last"]["status"])
        self.assertEqual(self.last_receipt()["previous_values"], {"FUTURE_AGI_VERSION": seen["env"]["FUTURE_AGI_VERSION"]})

    def test_killed_during_the_restart_plans_its_resume(self):
        left = self.killed("up")
        seen, plan = self.dry_run(self.plan(env_changes={}, resume=True, current=["v1.1.0"], action="keep"))
        self.assertEqual(seen["last"]["status"], "interrupted_during_rollout")
        self.assertEqual(seen["overlay"], {})
        self.assertEqual(seen["env"]["FUTURE_AGI_VERSION"], "v1.1.0")
        self.assertEqual(plan["notes"], [apply_module.JOURNAL_NOTE])
        self.assertEqual(self.env_bytes(), left)
        self.assertEqual(self.journal()["stage"], "rollout")

    def test_older_journal_without_found_values(self):
        self.build(current="v1.1.0")
        self.craft_journal("pulled")
        seen, _ = self.dry_run()
        self.assertEqual(seen["overlay"], {"FUTURE_AGI_VERSION": "v1.0.0"})

    def test_journal_of_another_install_is_refused_like_the_real_apply(self):
        self.craft_journal("pulled", env_file="deploy/.env.production", production=True)
        exc = self.assertRaisesExactly(Blocked, self.dry_run)
        recover = ("an interrupted bin/update apply for deploy/.env.production must be recovered before this install "
                   "can be updated or rolled back: run `./bin/update --production rollback` (without --yes it only "
                   "recovers that run and changes nothing else), then `./bin/update apply {}--backup-command "
                   "'/opt/backup/run --all'` again; `./bin/update --production apply` would recover it too, but would "
                   "also continue that install's update")
        self.assertEqual(str(exc), recover.format("--dry-run "))
        real = self.assertRaisesExactly(Blocked, self.apply)
        self.assertEqual(str(real), recover.format(""))
        self.assertEqual(self.env_bytes(), self.original)
        self.assertEqual(self.receipts(), [])

    def test_without_a_journal_it_plans_with_the_latest_receipt(self):
        path = self.craft_apply(previous="v0.9.0", target="v1.0.0")
        seen, plan = self.dry_run()
        self.assertEqual(seen["last"]["_path"], str(path))
        self.assertEqual(seen["overlay"], {})
        self.assertEqual(plan["notes"], [])

    def test_preview_recovery_changes_nothing_but_the_overlay(self):
        self.build(values={'FUTURE_AGI_VERSION': None})
        CrashRecoveryPreservationTests.interrupt_apply(self)
        journal = self.journal()
        before = self.env_bytes()
        record, notes = apply_module.preview_recovery(self.inst, journal)
        self.assertEqual(record['status'], 'interrupted_before_rollout')
        self.assertEqual(journal['status'], 'running')
        self.assertIn('FUTURE_AGI_VERSION=unset', notes[-1])
        self.assertNotIn('FUTURE_AGI_VERSION', self.inst.env)
        self.assertEqual(self.env_bytes(), before)
        # Last-value fields are no longer authoritative, even when present but empty.
        record, again = apply_module.preview_recovery(self.inst, dict(journal, found_values={}))
        self.assertEqual(again, notes)


# ------------------------------------------ what a resume records from before --

class ResumeRecordsTheOriginalStateTests(UpdaterCase):
    """A resume records what ran before the ORIGINAL update: values, images and the tags services resolved to."""

    GROUPS = (BACKEND, FI_COLLECTOR, FRONTEND)

    def setUp(self):
        super().setUp()
        # A default Distributed install: fi-collector unset, so it runs the image built from the checkout.
        self.build("distributed", groups=self.GROUPS, values={"FUTURE_AGI_VERSION": "v1.0.0",
                                                               "FRONTEND_VERSION": "v1.0.0"})
        self.local_id = self.inst.running["fi-collector"]["image_id"]

    def failed_rollout(self):
        self.ready.failure = NotReady("the API did not answer /health/ on port 8000 within 180s")
        self.assertRaisesExactly(FailedDuringRollout, self.apply)
        self.ready.failure = None
        stopped = self.state.receipts()[-1]
        del self.log[:]
        self.plans_seen = []
        return stopped

    def resume_plan(self, env_changes=None):
        plan = self.plan(env_changes=env_changes or {}, resume=True, current=["v1.1.0"], action="keep")
        for item in plan["components"]:
            item["current_tags"] = [item["target_tag"]]  # as the real plan sees it: the env file selects the target
        return plan

    def test_resume_keeps_the_tags_from_before_the_original_update(self):
        self.failed_rollout()
        self.apply(self.resume_plan())
        tags = {c["component"]: c["current_tags"] for c in self.last_receipt()["components"]}
        self.assertEqual(tags, {"backend": ["v1.0.0"], "fi_collector": ["local"], "frontend": ["v1.0.0"]})

    def test_rollback_of_a_resumed_update_unsets_the_local_collector_again(self):
        self.failed_rollout()
        self.apply(self.resume_plan())
        self.rollback()
        self.assertEqual(self.last_receipt()["status"], "rolled_back")
        self.assertIsNone(self.env_value("FI_COLLECTOR_VERSION"))
        self.assertEqual(self.env_value("FUTURE_AGI_VERSION"), "v1.0.0")
        self.assertEqual(self.inst.running["fi-collector"]["image_id"], self.local_id)
        self.assertIn("FI_COLLECTOR_VERSION unset (selects :local)", self.out.getvalue())

    def test_rollback_after_a_resume_that_failed_too(self):
        self.failed_rollout()
        self.ready.failure = NotReady("the API did not answer /health/ on port 8000 within 180s")
        self.assertRaisesExactly(FailedDuringRollout, self.apply, self.resume_plan())
        self.ready.failure = None
        self.rollback()
        self.assertIsNone(self.env_value("FI_COLLECTOR_VERSION"))
        self.assertEqual(self.inst.running["fi-collector"]["image_id"], self.local_id)

    def test_stopped_receipt_without_tags_falls_back_to_the_images_that_ran(self):
        stopped = self.failed_rollout()
        record = json.loads(stopped.read_text(encoding="utf-8"))
        for item in record["components"]:
            del item["current_tags"]
        stopped.write_text(json.dumps(record), encoding="utf-8")
        self.apply(self.resume_plan())
        self.assertEqual({c["component"]: c["current_tags"] for c in self.last_receipt()["components"]},
                         {"backend": [], "fi_collector": [], "frontend": []})
        self.rollback()
        self.assertIsNone(self.env_value("FI_COLLECTOR_VERSION"))
        self.assertEqual(self.inst.running["fi-collector"]["image_id"], self.local_id)

    def test_a_key_only_the_resume_changes_is_rolled_back_to_what_it_found(self):
        self.build("distributed")
        # The stopped update changed FUTURE_AGI_VERSION only; the resume also moves FRONTEND_VERSION.
        stopped = self.craft_apply(status="failed_during_rollout", groups=(BACKEND,),
                                   env_changes={"FUTURE_AGI_VERSION": TARGET})
        self.env_path.write_bytes(self.with_versions({"FUTURE_AGI_VERSION": TARGET}))
        self.inst.env = read_env(self.env_path)
        self.apply(self.plan(env_changes={"FRONTEND_VERSION": TARGET}, resume=True, current=["v1.0.0", TARGET]))
        receipt = self.last_receipt()
        self.assertEqual(receipt["resumes"], str(stopped))
        self.assertEqual(receipt["found_values"], {"FRONTEND_VERSION": "v1.0.0"})
        self.assertEqual(receipt["previous_values"], {"FUTURE_AGI_VERSION": "v1.0.0", "FRONTEND_VERSION": "v1.0.0"})
        self.rollback()
        self.assertEqual(self.env_bytes(), self.original)


# ============================================================ round 2 fixes ==

def oneoff_container(service, image, image_id, **fields):
    """A `docker compose run` container: Compose labels it oneoff=True."""

    record = {"id": f"c-run-{service}", "service": service, "image_id": image_id, "image": image,
              "status": "running", "exit_code": 0, "restart_count": 0, "health": "none", "started_at": "",
              "command": [], "oneoff": True}
    record.update(fields)
    return record


class StagedUpdateTests(UpdaterCase):
    """apply --stage: everything up to the digest check, nothing restarted; the next apply finishes it."""

    def stage(self, plan=None, **kwargs):
        plan = plan or self.plan()

        def make_plan(history):
            self.plans_seen.append(history[-1] if history else None)
            self.histories_seen.append(list(history))
            return plan

        return self.updater().apply(make_plan, {"version": plan["target_version"]}, backup_command=BACKUP,
                                    stage=True, **kwargs)

    def finish_plan(self):
        # As the real plan sees a staged update: the env file selects the target, the stack runs the old one.
        plan = self.plan(env_changes={}, resume=True, current=["v1.1.0"], action="keep")
        for item in plan["components"]:
            item["current_tags"] = [item["target_tag"]]
        return plan

    def test_stage_runs_everything_before_the_restart_and_stops(self):
        path = self.stage()
        self.assertEqual(self.events(), [
            # Round 5 (G4): the plan it prints says whether the release's images are on this host already.
            ("image", "futureagi/standalone:v1.1.0"),
            ("backup",),
            ("containers",),
            ("image", id_of("futureagi/standalone", "v1.0.0")),
            ("journal", "prepare"),
            ("env_write", {"FUTURE_AGI_VERSION": "v1.1.0"}),
            ("journal", "env_written"),
            ("compose", ("config", "--quiet"), False),
            ("config", True),
            ("futureagi_services",),
            ("image", "futureagi/standalone:v1.1.0"),  # not on the host yet: pulled
            ("compose", ("pull", "app"), True),
            ("image", "postgres:16"),
            ("journal", "pulled"),
            ("image", "futureagi/standalone:v1.1.0"),
            ("receipt", "staged"),
            ("clear_journal",),
        ])
        self.assertEqual(Path(path), self.state.receipts()[-1])
        receipt = self.last_receipt()
        self.assertEqual((receipt["status"], receipt["stage"]), ("staged", "pulled"))
        self.assertEqual(receipt["target_image_ids"], {"app": id_of("futureagi/standalone", "v1.1.0")})
        self.assertEqual(receipt["previous_values"], {"FUTURE_AGI_VERSION": "v1.0.0"})
        self.assertIs(receipt["backup"]["ran"], True)
        self.assertIsNone(self.journal())
        self.assertEqual(self.env_bytes(), self.with_versions({"FUTURE_AGI_VERSION": "v1.1.0"}))
        self.assertEqual(self.inst.running["app"]["image_id"], id_of("futureagi/standalone", "v1.0.0"))
        self.assertNotIn(("wait_standalone",), self.log)

    def test_stage_message_names_what_comes_next(self):
        path = self.stage()
        text = self.out.getvalue()
        self.assertIn("Staged v1.1.0: .env now selects it (FUTURE_AGI_VERSION=v1.1.0) and its images are pulled and "
                      "verified. Nothing was restarted", text)
        self.assertIn("Then run `./bin/update apply` to restart the stack on v1.1.0", text)
        self.assertIn(f"`./bin/update rollback --receipt {path} --yes`", text)
        self.assertNotIn("initialization jobs", text)
        self.assertNotIn("Restarting the stack", text)

    def test_production_stage_message_names_the_initialization_step(self):
        self.build("production")
        self.stage()
        text = self.out.getvalue()
        # Round 5: the plan's jobs text (T22/T25 reworded its shell rule); apply prints the same.
        self.assertIn("Next, run the release's initialization jobs, which now use these images, with "
                      f"{jobs_text(self.inst)}.", text)
        self.assertIn("`docker compose --env-file deploy/.env.production -f docker-compose.distributed.yml -f "
                      "deploy/docker-compose.production.yml run --rm ...` (deploy/README.md › Upgrading a production "
                      "install", text)
        self.assertIn("Then run `./bin/update --production apply` to restart the stack on v1.1.0", text)
        self.assertLess(text.index("initialization jobs"), text.index("Then run"))

    def test_stage_dry_run_changes_nothing(self):
        self.assertIsNone(self.stage(dry_run=True))
        self.assertIn("would run the backup, set FUTURE_AGI_VERSION=v1.1.0, pull and verify the images, and stop "
                      "before restarting anything", self.out.getvalue())
        self.assertEqual(self.env_bytes(), self.original)
        self.assertEqual(self.receipts(), [])
        self.assertEqual(self.hook.calls, [])
        self.assertEqual(self.compose_calls(), [])

    def test_failure_while_staging_puts_everything_back(self):
        self.inst.failures["pull"] = InstallError("`docker compose pull` failed: network unreachable")
        exc = self.assertRaisesExactly(FailedBeforeRollout, self.stage)
        self.assertIn("Nothing was changed; the previous versions are configured and running", str(exc))
        self.assertEqual(self.env_bytes(), self.original)
        self.assertEqual(self.last_receipt()["status"], "failed_before_rollout")

    def test_apply_finishes_a_staged_update_without_a_second_backup(self):
        staged = self.stage()
        del self.log[:]
        self.hook.calls.clear()
        self.out = io.StringIO()
        self.apply(self.finish_plan())
        self.assertEqual(self.plans_seen[-1]["_path"], str(staged))
        self.assertEqual(self.hook.calls, [], "the backup taken before staging covers the update")
        self.assertIn("Skipping the backup: it ran when the update to v1.1.0 was staged", self.out.getvalue())
        receipt = self.last_receipt()
        self.assertEqual(receipt["status"], "succeeded")
        self.assertEqual(receipt["resumes"], str(staged))
        self.assertEqual(receipt["backup"], {"ran": False, "staged": str(staged)})
        self.assertEqual(receipt["previous_values"], {"FUTURE_AGI_VERSION": "v1.0.0"})
        self.assertEqual(receipt["previous_images"]["app"]["image_id"], id_of("futureagi/standalone", "v1.0.0"))
        self.assertEqual({c["component"]: c["current_tags"] for c in receipt["components"]},
                         {"standalone": ["v1.0.0"]})
        # The staged images are on the host with their signed digests: verified again, not pulled again.
        self.assertEqual([c[0] for c in self.compose_calls()], ["config", "up"])
        self.assertEqual([e[1] for e in self.log if e[0] == "env_write"], [{}])
        self.assertNotIn(("env_restore",), self.log)
        self.assertEqual(self.inst.running["app"]["image_id"], id_of("futureagi/standalone", "v1.1.0"))
        self.assertIn("Updated to v1.1.0", self.out.getvalue())

    def test_finishing_verifies_the_digest_again(self):
        self.stage()
        self.inst.metadata[id_of("futureagi/standalone", "v1.1.0")]["repo_digests"] = [
            f"futureagi/standalone@sha256:{'e' * 64}"]
        self.assertRaisesExactly(FailedBeforeRollout, self.apply, self.finish_plan())
        self.assertNotIn("up", [c[0] for c in self.compose_calls()])

    def test_dry_run_of_the_finishing_apply(self):
        staged = self.stage()
        self.hook.calls.clear()
        self.out = io.StringIO()
        self.apply(self.finish_plan(), dry_run=True)
        text = self.out.getvalue()
        self.assertIn("would set no version keys, verify the images again (pulling only those no longer on this "
                      f"host), and restart the stack, finishing the update staged in {staged} (no new backup)", text)
        self.assertEqual(self.last_receipt()["status"], "staged")

    def test_a_staged_update_can_be_rolled_back(self):
        staged = self.stage()
        self.rollback()
        receipt = self.last_receipt()
        self.assertEqual((receipt["kind"], receipt["status"], receipt["rolls_back"]),
                         ("rollback", "rolled_back", str(Path(staged).resolve())))
        self.assertEqual(self.env_bytes(), self.original)
        self.assertEqual(self.inst.running["app"]["image_id"], id_of("futureagi/standalone", "v1.0.0"))

    def test_finishing_that_fails_before_the_restart_says_the_update_is_still_staged(self):
        staged = self.stage()
        staged_env = self.env_bytes()
        self.prune(TARGET)  # pulled only when it is not on the host
        self.inst.failures["pull"] = InstallError("`docker compose pull` failed: network unreachable")
        exc = self.assertRaisesExactly(FailedBeforeRollout, self.apply, self.finish_plan())
        text = str(exc)
        self.assertNotIn("Nothing was changed", text)
        self.assertIn("The update to v1.1.0 staged earlier is still pending: .env selects it "
                      "(FUTURE_AGI_VERSION=v1.1.0), and the stack still runs the previous versions", text)
        self.assertIn("To finish it, run `./bin/update apply`", text)
        self.assertIn(f"to go back, run `./bin/update rollback --receipt {staged} --yes`", text)
        self.assertEqual(self.env_bytes(), staged_env)
        # The staged receipt is still the one to roll back.
        del self.inst.failures["pull"]
        self.rollback(Path(staged))
        self.assertEqual(self.env_bytes(), self.original)


class ProductionTwoStepTests(UpdaterCase):
    """A production release that needs its initialization jobs updates in two steps."""

    MANIFEST = {"version": TARGET, "migration": {"requires_bootstrap": True}}

    def setUp(self):
        super().setUp()
        self.build("production")

    def run_apply(self, plan=None, manifest=None, **kwargs):
        plan = plan or self.plan()
        kwargs.setdefault("backup_command", BACKUP)

        def make_plan(history):
            self.plans_seen.append(history[-1] if history else None)
            return plan

        return self.updater().apply(make_plan, manifest or self.MANIFEST, **kwargs)

    def test_plain_apply_is_blocked_before_the_backup(self):
        exc = self.assertRaisesExactly(Blocked, self.run_apply)
        # Round 5: the plan's jobs text (T22/T25 reworded its shell rule); apply prints the same.
        self.assertIn("production installs update in two steps: ./bin/update --production apply --stage (backs up, "
                      "then pulls and verifies the release; restarts nothing), run the release's initialization jobs "
                      f"with {jobs_text(self.inst)}, then ./bin/update --production apply. Nothing was changed", str(exc))
        self.assertIn(f"`{PRODUCTION_COMPOSE} run --rm ...` (deploy/README.md › Upgrading a production install", str(exc))
        self.assertIn("Nothing was changed", str(exc))
        self.assertEqual(self.hook.calls, [])
        self.assertEqual(self.env_bytes(), self.original)
        self.assertEqual(self.receipts(), [])
        self.assertEqual(self.compose_calls(), [])

    def test_dry_run_is_blocked_the_same_way(self):
        exc = self.assertRaisesExactly(Blocked, self.run_apply, dry_run=True)
        self.assertIn("production installs update in two steps", str(exc))

    def test_a_custom_env_file_is_named_in_both_commands(self):
        path = self.root / "deploy" / "prod-eu.env"
        path.write_bytes(self.original)
        self.inst.env_file = self.env_path = self.state.env_path = self.hook.env_path = path
        exc = self.assertRaisesExactly(Blocked, self.run_apply)
        self.assertIn("./bin/update --production --env-file deploy/prod-eu.env apply --stage (backs up", str(exc))
        # S11: the jobs read this install's env file, not deploy/README.md's $COMPOSE (deploy/.env.production).
        # Round 5: the plan's jobs text (T22/T25 reworded its shell rule); apply prints the same.
        self.assertIn(f"run the release's initialization jobs with {jobs_text(self.inst)}, then ./bin/update --production "
                      "--env-file deploy/prod-eu.env apply. Nothing was changed", str(exc))
        self.assertIn("`docker compose --env-file deploy/prod-eu.env -f docker-compose.distributed.yml -f "
                      "deploy/docker-compose.production.yml run --rm ...` (deploy/README.md › Upgrading a production "
                      "install", str(exc))

    def test_stage_then_apply(self):
        staged = self.run_apply(stage=True)
        self.assertEqual(self.last_receipt()["status"], "staged")
        plan = self.plan(env_changes={}, resume=True, current=["v1.1.0"], action="keep")
        self.run_apply(plan)
        receipt = self.last_receipt()
        self.assertEqual((receipt["status"], receipt["resumes"]), ("succeeded", str(staged)))

    def test_resuming_a_stopped_update_is_not_blocked(self):
        self.assertRaisesExactly(Blocked, self.run_apply)
        failed = self.craft_apply(status="failed_during_rollout")
        self.env_path.write_bytes(self.with_versions({"FUTURE_AGI_VERSION": TARGET, "FRONTEND_VERSION": TARGET}))
        self.inst.env = read_env(self.env_path)
        self.run_apply(self.plan(env_changes={}, resume=True, current=["v1.1.0"], action="keep"))
        self.assertEqual(self.last_receipt()["resumes"], str(failed))

    def test_only_production_releases_with_migrations_need_two_steps(self):
        self.assertRaisesExactly(Blocked, self.run_apply)
        self.run_apply(manifest={"version": TARGET, "migration": {"requires_bootstrap": False}})
        self.assertEqual(self.last_receipt()["status"], "succeeded")
        self.build("distributed")
        self.run_apply()
        self.assertEqual(self.last_receipt()["status"], "succeeded")


class OneOffContainerTests(UpdaterCase):
    """`docker compose run` containers are not the services' containers."""

    def test_a_one_off_container_on_the_old_image_does_not_fail_the_rollout(self):
        self.inst.extra_containers.append(oneoff_container(
            "app", "futureagi/standalone:v1.0.0", id_of("futureagi/standalone", "v1.0.0")))
        self.apply()
        self.assertEqual(self.last_receipt()["status"], "succeeded")

    def test_one_off_containers_are_not_handed_to_readiness_or_snapshotted(self):
        self.inst.extra_containers.append(oneoff_container(
            "app", "futureagi/standalone:v0.9.0", "sha256:" + "5" * 64))
        self.apply()
        self.assertEqual(self.ready.previous_ids, [["c-app"]])
        self.assertEqual(self.last_receipt()["previous_images"]["app"]["image_id"],
                         id_of("futureagi/standalone", "v1.0.0"))

    def test_distributed_backfill_run_does_not_fail_the_rollout(self):
        self.build("distributed")
        self.inst.extra_containers.append(oneoff_container(
            "backend", "futureagi/future-agi:v1.0.0", id_of("futureagi/future-agi", "v1.0.0")))
        self.apply()
        self.assertEqual(self.last_receipt()["status"], "succeeded")


class InterpolatedVersionValueTests(UpdaterCase):
    """A version key that refers to another variable is put back exactly as it was found."""

    TEXT = ("# Future AGI configuration\nSECRET_KEY=%s\nRELEASE=v1.0.0\nFUTURE_AGI_VERSION=${RELEASE}\n"
            "INSTALL_READY_TIMEOUT_SECONDS=600\n" % SECRET)

    def setUp(self):
        super().setUp()
        self.env_path.write_bytes(self.TEXT.encode())
        self.original = self.env_bytes()
        self.inst.env = read_env(self.env_path)

    def test_failure_before_the_restart_restores_the_reference(self):
        self.inst.failures["pull"] = InstallError("`docker compose pull` failed: network unreachable")
        exc = self.assertRaisesExactly(FailedBeforeRollout, self.apply)
        self.assertIn("Nothing was changed", str(exc))
        self.assertEqual(self.env_bytes(), self.original)
        self.assertEqual(self.inst.env["FUTURE_AGI_VERSION"], "${RELEASE}")
        self.assertIsNone(self.journal())
        self.assertEqual(self.last_receipt()["found_values"], {"FUTURE_AGI_VERSION": "${RELEASE}"})
        self.assertIn("recovery", self.env_write_kwargs[-1])

    def test_a_killed_run_is_recovered_and_the_next_apply_proceeds(self):
        self.inst.failures["pull"] = SystemExit(137)
        with self.assertRaises(SystemExit):
            self.apply()
        del self.inst.failures["pull"]
        recovered = self.updater().recover()
        self.assertEqual(recovered["status"], "interrupted_before_rollout")
        self.assertEqual(self.env_bytes(), self.original)
        self.apply()
        self.assertEqual(self.last_receipt()["status"], "succeeded")

    def test_rollback_can_still_run_after_the_restore(self):
        self.inst.failures["pull"] = KeyboardInterrupt()
        self.assertRaisesExactly(InterruptedRun, self.apply)
        exc = self.assertRaisesExactly(Blocked, self.rollback)
        self.assertEqual(str(exc), "there is no update to roll back in this checkout")


class UnwritableFoundValueTests(UpdaterCase):
    """A found value that cannot be written back on its own never wedges every later run."""

    def setUp(self):
        super().setUp()
        self.build(current="v1.1.0")

    def test_legacy_ambiguous_values_keep_the_journal_without_guessing(self):
        self.craft_journal('pulled', found_values={'FUTURE_AGI_VERSION': 'v1.0.0\nINJECTED=1'})
        journal = self.state.journal_path.read_bytes()
        with self.assertRaises(StateError) as caught:
            self.updater().recover()
        self.assertIn('operator-assisted recovery', str(caught.exception))
        self.assertEqual(self.state.journal_path.read_bytes(), journal)
        self.assertEqual(self.env_bytes(), self.original)
        self.assertEqual(self.receipts(), [])

    def rollback_or_apply_proceeds(self):
        self.apply(self.plan(decision=UP_TO_DATE, env_changes={}))
        self.assertIn("Nothing to do", self.out.getvalue())

    def test_a_legacy_journal_never_partially_restores_other_keys(self):
        self.build('distributed', current=TARGET)
        self.craft_journal('pulled', found_values={'FUTURE_AGI_VERSION': 'v1.0.0', 'FRONTEND_VERSION': 'a\rb'})
        before = self.state.journal_path.read_bytes()
        with self.assertRaises(StateError):
            self.updater().recover()
        self.assertEqual(self.env_bytes(), self.original)
        self.assertEqual(self.state.journal_path.read_bytes(), before)
        self.assertEqual(self.receipts(), [])

    def test_failure_before_the_restart_puts_back_the_original_bytes(self):
        # Compose reads "\n" in a double-quoted value as a line break: no write can put that value back,
        # but the original bytes can.
        text = 'SECRET_KEY=%s\nFUTURE_AGI_VERSION="v1.0.0\\nnote"\nOTHER=x\n' % SECRET
        self.build(env_text=text)
        self.assertEqual(self.inst.env["FUTURE_AGI_VERSION"], "v1.0.0\nnote")
        self.inst.failures["pull"] = InstallError("`docker compose pull` failed: network unreachable")
        exc = self.assertRaisesExactly(FailedBeforeRollout, self.apply)
        self.assertEqual(self.env_bytes(), text.encode())
        self.assertIsNone(self.journal())
        self.assertNotIn("Not put back", str(exc))
        self.assertNotIn("unrestored_values", self.last_receipt())

    def test_when_the_file_was_edited_meanwhile_it_says_what_to_fix_and_keeps_the_journal(self):
        text = 'SECRET_KEY=%s\nFUTURE_AGI_VERSION="v1.0.0\\nnote"\nOTHER=x\n' % SECRET
        self.build(env_text=text)

        def edit_then_fail(*args):
            self.env_path.write_bytes(self.env_bytes() + b"ADDED_BY_THE_OPERATOR=1\n")
            raise InstallError("`docker compose pull` failed: network unreachable")

        self.inst.failures["pull"] = edit_then_fail
        exc = self.assertRaisesExactly(UpdateError, self.apply)
        self.assertIn('env commit conflict', str(exc))
        self.assertIn('Kept', str(exc))
        self.assertIn(b'ADDED_BY_THE_OPERATOR=1', self.env_bytes())
        self.assertIsNotNone(self.journal())
        before = self.env_bytes(), self.state.journal_path.read_bytes(), self.receipts()
        with self.assertRaises(StateError):
            self.updater().recover()
        self.assertEqual((self.env_bytes(), self.state.journal_path.read_bytes(), self.receipts()), before)


class ScrubOnlyFreeTextTests(UpdaterCase):
    """A weak secret equal to the project name must not mangle what rollback compares."""

    def setUp(self):
        super().setUp()
        text = ("SECRET_KEY=%s\nS3_ACCESS_KEY=futureagi\nFUTURE_AGI_VERSION=v1.0.0\n" % SECRET)
        self.build(env_text=text, project="futureagi")

    def test_receipt_keeps_the_project_images_and_paths(self):
        self.assertIn("futureagi", self.state.secrets)
        path = self.apply()
        receipt = self.last_receipt()
        self.assertEqual(receipt["project"], "futureagi")
        self.assertEqual(receipt["previous_images"]["app"]["image"], "futureagi/standalone:v1.0.0")
        self.assertEqual(receipt["previous_images"]["app"]["repo_digests"],
                         [f"futureagi/standalone@{digest_of('futureagi/standalone', 'v1.0.0')}"])
        self.rollback(Path(path))
        self.assertEqual(self.last_receipt()["status"], "rolled_back")
        self.assertEqual(self.env_value("FUTURE_AGI_VERSION"), "v1.0.0")

    def test_command_text_is_still_scrubbed(self):
        self.inst.failures["up"] = InstallError(f"auth failed for futureagi with {SECRET}")
        exc = self.assertRaisesExactly(FailedDuringRollout, self.apply)
        receipt = self.last_receipt()
        self.assertEqual(receipt["error"], "auth failed for *** with ***")
        self.assertNotIn(SECRET, str(exc))
        self.assertEqual(receipt["project"], "futureagi")

    def test_resume_of_a_stopped_rollout_keeps_its_receipt_path(self):
        self.ready.failure = NotReady("the app container keeps restarting")
        self.assertRaisesExactly(FailedDuringRollout, self.apply)
        self.ready.failure = None
        stopped = self.state.receipts()[-1]
        self.apply(self.plan(env_changes={}, resume=True, current=["v1.1.0"], action="keep"))
        self.assertEqual(self.last_receipt()["resumes"], str(stopped))
        self.assertIn(".futureagi-updates", self.last_receipt()["resumes"])

    def test_signature_error_is_scrubbed_and_nothing_else(self):
        plan = self.plan()
        plan["signature"] = dict(plan["signature"], error=f"bundle fetched with token {SECRET}")
        plan["manifest_source"] = "https://example.com/futureagi/manifest.json"
        self.apply(plan)
        receipt = self.last_receipt()
        self.assertEqual(receipt["signature"]["error"], "bundle fetched with token ***")
        self.assertEqual(receipt["manifest_source"], "https://example.com/futureagi/manifest.json")


class InstallScopedHistoryTests(UpdaterCase):
    """Receipts of another install in the same checkout are never this install's."""

    OTHER = {"project": "fagi-staging", "env_file": "deploy/.env.staging", "production": True, "mode": "production"}

    def test_apply_plans_with_this_installs_receipts_only(self):
        mine = self.craft_apply(target="v1.0.0", previous="v0.9.0")
        self.craft_apply(status="failed_during_rollout", **self.OTHER)
        self.apply(self.plan(decision=UP_TO_DATE, env_changes={}))
        self.assertEqual([r["_path"] for r in self.histories_seen[0]], [str(mine)])

    def test_another_installs_stopped_update_is_not_resumed(self):
        self.craft_apply(status="failed_during_rollout", previous_values={"FUTURE_AGI_VERSION": "v0.1.0"},
                         **self.OTHER)
        # Even a plan that (wrongly) asks to resume finds nothing of this install's to carry over.
        self.apply(self.plan(resume=True))
        receipt = self.last_receipt()
        self.assertNotIn("resumes", receipt)
        self.assertEqual(receipt["previous_values"], {"FUTURE_AGI_VERSION": "v1.0.0"})
        self.assertEqual(receipt["previous_images"]["app"]["image_id"], id_of("futureagi/standalone", "v1.0.0"))

    def test_rollback_ignores_another_installs_receipts(self):
        self.build(current="v1.1.0")
        self.craft_apply(**self.OTHER)
        exc = self.assertRaisesExactly(Blocked, self.rollback)
        self.assertEqual(str(exc), "there is no update to roll back in this checkout")

    def test_another_installs_newer_update_does_not_block_this_rollback(self):
        self.build(current="v1.1.0")
        mine = self.craft_apply()
        self.craft_apply(previous="v1.1.0", target="v1.2.0", **self.OTHER)
        self.rollback()
        self.assertEqual(self.last_receipt()["rolls_back"], str(mine.resolve()))
        self.rollback_of_mine_by_path_is_not_blocked_either(mine)

    def rollback_of_mine_by_path_is_not_blocked_either(self, mine):
        exc = self.assertRaisesExactly(Blocked, self.rollback, mine)
        self.assertIn("already rolled back", str(exc))

    def test_dry_run_preview_uses_this_installs_receipts(self):
        self.build(current="v1.1.0")
        self.craft_apply(status="failed_during_rollout", **self.OTHER)
        self.craft_journal("pulled")
        self.apply(dry_run=True)
        self.assertEqual([r["status"] for r in self.histories_seen[0]], ["interrupted_before_rollout"])


class NohupTests(unittest.TestCase):
    """A signal inherited as ignored (nohup) stays ignored."""

    def setUp(self):
        for sig in (signal.SIGTERM, signal.SIGHUP):
            self.addCleanup(signal.signal, sig, signal.getsignal(sig))

    def test_ignored_sighup_stays_ignored_and_sigterm_still_interrupts(self):
        signal.signal(signal.SIGHUP, signal.SIG_IGN)
        with self.assertRaises(Interrupted):
            with signals_as_interrupt():
                self.assertIs(signal.getsignal(signal.SIGHUP), signal.SIG_IGN)
                os.kill(os.getpid(), signal.SIGHUP)  # ignored: nothing happens
                time.sleep(0.05)
                deliver(signal.SIGTERM)
        self.assertIs(signal.getsignal(signal.SIGHUP), signal.SIG_IGN)

    def test_both_ignored_are_left_alone(self):
        for sig in (signal.SIGTERM, signal.SIGHUP):
            signal.signal(sig, signal.SIG_IGN)
        with signals_as_interrupt():
            os.kill(os.getpid(), signal.SIGTERM)
            os.kill(os.getpid(), signal.SIGHUP)
            time.sleep(0.05)
        for sig in (signal.SIGTERM, signal.SIGHUP):
            self.assertIs(signal.getsignal(sig), signal.SIG_IGN)

    def test_compose_inherits_the_ignore(self):
        signal.signal(signal.SIGHUP, signal.SIG_IGN)
        with signals_as_interrupt():
            child = subprocess.run(
                [sys.executable, "-c", "import signal; print(signal.getsignal(signal.SIGHUP) == signal.SIG_IGN)"],
                capture_output=True, text=True, check=True)
        self.assertEqual(child.stdout.strip(), "True")


class InactiveProfileContainerTests(UpdaterCase):
    """Containers of services outside the active profiles must not keep running the previous release."""

    STALE_ID = "ab" * 32

    def setUp(self):
        super().setUp()
        self.build("distributed")
        # Services of Compose profiles that are not enabled (Installation.service_model lists them).
        self.inst.profile_services.update({
            name: "futureagi/future-agi:${FUTURE_AGI_VERSION:-latest}"
            for name in ("worker-default", "worker-stopped", "worker-new", "worker-backfill")})
        self.inst.profile_services.update({
            "old-redis": "redis:7", "code-executor": "futureagi/code-executor:${CODE_EXECUTOR_VERSION:-latest}",
            "simulation-runner": "futureagi/future-agi-simulation-runner:${SIMULATION_RUNNER_VERSION:-latest}"})

    def container(self, service, version="v1.0.0", status="running", repo="futureagi/future-agi", id=None, **fields):
        record = {"id": id or _hex(service), "service": service, "image_id": id_of(repo, version),
                  "image": f"{repo}:{version}", "status": status, "exit_code": 0, "restart_count": 0,
                  "health": "none", "started_at": "", "command": []}
        record.update(fields)
        return record

    def test_a_running_container_of_an_inactive_service_on_the_old_image_fails_the_rollout(self):
        self.inst.extra_containers.extend([
            self.container("worker-default", id=self.STALE_ID),
            # None of these counts: stopped, already on the verified image, not a Future AGI image, a one-off
            # `compose run`, and a component this update does not touch.
            self.container("worker-stopped", status="exited"),
            self.container("worker-new", version=TARGET),
            self.container("old-redis", repo="redis", version="7"),
            self.container("worker-backfill", oneoff=True),
            self.container("code-executor", repo="futureagi/code-executor"),
        ])
        exc = self.assertRaisesExactly(FailedDuringRollout, self.apply)
        self.assertEqual(self.last_receipt()["error"],
                         "containers of services outside the active Compose profiles run another release than the "
                         "stack now runs: worker-default runs v1.0.0, not v1.1.0. Enable their profile "
                         "(COMPOSE_PROFILES in .env) so Compose recreates them on the version .env selects, or remove "
                         f"them with `docker rm -f {self.STALE_ID[:12]}`")
        self.assertIn("To finish, run `./bin/update apply` again", str(exc))
        self.assertEqual(self.env_value("FUTURE_AGI_VERSION"), TARGET)

    def test_an_inactive_pin_this_run_moves_counts(self):
        self.inst.extra_containers.append(self.container("simulation-runner",
                                                         repo="futureagi/future-agi-simulation-runner"))
        plan = self.plan()
        plan["components"].append(inactive_item())
        plan["env_changes"][RUNNER] = TARGET
        self.assertRaisesExactly(FailedDuringRollout, self.apply, plan)
        self.assertIn("now runs: simulation-runner runs futureagi/future-agi-simulation-runner:v1.0.0 (no version "
                      "label), not v1.1.0. Enable", self.last_receipt()["error"])

    def test_a_kept_inactive_pin_on_its_own_tag_does_not_count(self):
        self.inst.extra_containers.append(self.container("simulation-runner", version=TARGET,
                                                         repo="futureagi/future-agi-simulation-runner"))
        self.inst.extra_containers.append(self.container("worker-default"))
        plan = self.plan()
        plan["components"].append(dict(inactive_item(), action="keep", current_version=TARGET,
                                       current_tags=[TARGET]))
        self.assertRaisesExactly(FailedDuringRollout, self.apply, plan)
        self.assertIn("now runs: worker-default runs v1.0.0, not v1.1.0. Enable", self.last_receipt()["error"])

    def test_rollback_checks_them_too(self):
        self.applied()
        # One started during the restart (after the rollback's own check, see Round4RollbackStrayContainerTests).
        stray = self.container("worker-default", version=TARGET)
        self.inst.failures["up"] = lambda *args: self.inst.extra_containers.append(stray)
        self.assertRaisesExactly(FailedDuringRollout, self.rollback)
        # It runs the release the rollback undoes, not the previous one.
        self.assertIn("now runs: worker-default runs v1.1.0, not v1.0.0. Enable", self.last_receipt()["error"])


class UpPullNeverTests(UpdaterCase):
    """Only the images just verified may start: `up --pull never` when Compose has it."""

    def test_standalone_up_never_pulls(self):
        self.inst.up_pull_never = True
        self.apply()
        self.assertIn(("up", "-d", "--no-build", "--pull", "never"), self.compose_calls())

    def test_distributed_and_production_up_never_pull(self):
        for mode in ("distributed", "production"):
            with self.subTest(mode=mode):
                self.build(mode)
                self.inst.up_pull_never = True
                self.apply()
                self.assertIn(("up", "-d", "--no-build", "--pull", "never", "--wait", "--wait-timeout", "1200"),
                              self.compose_calls())

    def test_rollback_up_never_pulls(self):
        self.applied()
        self.inst.up_pull_never = True
        self.rollback()
        self.assertIn(("up", "-d", "--no-build", "--pull", "never"), self.compose_calls())

    def test_the_probe_comes_before_the_rollout_stage(self):
        calls = []
        self.inst.supports_up_pull_never = lambda: calls.append(self.journal()["stage"]) or True
        self.apply()
        self.assertEqual(calls, ["pulled"])


class ThirdPartyImageTests(UpdaterCase):
    """Images of the other services that are missing are pulled before anything restarts."""

    def missing(self, service="clickhouse", image="clickhouse/clickhouse-server:25.3"):
        self.inst.third_party[service] = image
        self.inst.registry[image] = "sha256:" + "3" * 64
        self.inst.metadata["sha256:" + "3" * 64] = {"repo_digests": [], "version": ""}

    def test_a_missing_image_is_pulled_in_the_pull_stage(self):
        self.missing()
        self.apply()
        calls = self.compose_calls()
        self.assertIn(("pull", "clickhouse"), calls)
        self.assertNotIn(("pull", "postgres"), calls)
        events = self.events()
        self.assertLess(events.index(("compose", ("pull", "clickhouse"), True)), events.index(("journal", "pulled")))
        self.assertIn("Pulling clickhouse/clickhouse-server:25.3 (not on this host yet)", self.out.getvalue())

    def test_a_failed_pull_of_it_restarts_nothing_and_restores_the_env_file(self):
        self.missing()
        del self.inst.registry["clickhouse/clickhouse-server:25.3"]  # rate limited, say
        exc = self.assertRaisesExactly(FailedBeforeRollout, self.apply)
        self.assertIn("clickhouse", str(exc))
        self.assertEqual(self.env_bytes(), self.original)
        self.assertNotIn("up", [c[0] for c in self.compose_calls()])

    def test_only_missing_images_of_services_without_a_build_are_pulled(self):
        self.missing()
        self.inst.third_party["sandbox"] = {"image": "fagi-sandbox:dev", "build": {"context": "."}}
        self.apply()
        self.assertEqual([c for c in self.compose_calls() if c[0] == "pull"], [("pull", "app"), ("pull", "clickhouse")])

    def test_rollback_pulls_a_missing_one_too(self):
        self.applied()
        self.missing("temporal", "temporalio/auto-setup:1.25")
        self.rollback()
        self.assertIn(("pull", "temporal"), self.compose_calls())


class ComposeHintTests(UpdaterCase):
    """Failure messages name the Compose command that inspects this install."""

    def test_production_hint_carries_its_env_file_and_compose_files(self):
        self.build("production")
        self.inst.failures["up"] = InstallError("`docker compose up` failed with exit code 1")
        exc = self.assertRaisesExactly(FailedDuringRollout, self.apply)
        hint = ("docker compose --env-file deploy/.env.production -f docker-compose.distributed.yml "
                "-f deploy/docker-compose.production.yml")
        self.assertIn(f"Inspect `{hint} ps -a` and `{hint} logs`", str(exc))

    def test_rollback_hint_too(self):
        self.build("production")
        self.applied()
        self.inst.failures["up"] = InstallError("`docker compose up` failed with exit code 1")
        exc = self.assertRaisesExactly(FailedDuringRollout, self.rollback)
        self.assertIn("Inspect `docker compose --env-file deploy/.env.production -f", str(exc))

    def test_standalone_binary_is_named(self):
        self.inst.compose = ["docker-compose"]
        self.inst.failures["up"] = InstallError("boom")
        exc = self.assertRaisesExactly(FailedDuringRollout, self.apply)
        self.assertIn("Inspect `docker-compose ps -a` and `docker-compose logs`", str(exc))


class QuotedCommandTests(UpdaterCase):
    """Every printed bin/update command works when pasted, whatever the paths."""

    def spaced_checkout(self, mode="standalone"):
        self.root = self.root / "My Projects" / "future agi"
        self.root.mkdir(parents=True)
        self.build(mode)

    def command(self, text, start):
        """The command in backticks that starts with `start`, split like a shell would."""

        begin = text.index(f"`{start}")
        return shlex.split(text[begin + 1:text.index("`", begin + 1)])

    def test_receipt_paths_are_quoted(self):
        self.spaced_checkout()
        self.inst.failures["up"] = InstallError("boom")
        exc = self.assertRaisesExactly(FailedDuringRollout, self.apply)
        argv = self.command(str(exc), "./bin/update rollback")
        self.assertEqual(argv, ["./bin/update", "rollback", "--receipt", str(self.state.receipts()[-1]), "--yes"])

    def test_rollback_retry_path_is_quoted(self):
        self.spaced_checkout()
        applied = self.applied()
        self.inst.failures["up"] = InstallError("boom")
        exc = self.assertRaisesExactly(FailedDuringRollout, self.rollback)
        argv = self.command(str(exc), "./bin/update rollback")
        self.assertEqual(argv[3], str(Path(applied).resolve()))

    def test_staged_and_resumed_messages_quote_paths(self):
        self.spaced_checkout()
        self.ready.failure = NotReady("not ready")
        self.assertRaisesExactly(FailedDuringRollout, self.apply)
        self.ready.failure = None
        stopped = self.state.receipts()[-1]
        self.prune(TARGET)  # pulled only when it is not on the host
        self.inst.failures["pull"] = InstallError("network down")
        exc = self.assertRaisesExactly(FailedBeforeRollout, self.apply,
                                       self.plan(env_changes={}, resume=True, current=["v1.1.0"], action="keep"))
        self.assertEqual(self.command(str(exc), "./bin/update rollback")[3], str(stopped))

    def test_a_custom_env_file_with_a_space_is_quoted(self):
        self.build("production")
        path = self.root / "deploy" / "prod eu.env"
        path.write_bytes(self.original)
        self.inst.env_file = self.env_path = self.state.env_path = self.hook.env_path = path
        self.inst.env = read_env(path)
        self.inst.failures["up"] = InstallError("boom")
        exc = self.assertRaisesExactly(FailedDuringRollout, self.apply)
        argv = self.command(str(exc), "./bin/update --production --env-file")
        self.assertEqual(argv[:4], ["./bin/update", "--production", "--env-file", "deploy/prod eu.env"])

    def test_the_finished_by_hint_is_quoted(self):
        self.spaced_checkout()
        self.build(current="v1.1.0")
        first = self.craft_apply(status="failed_during_rollout")
        second = self.craft_apply(resumes=str(first))
        exc = self.assertRaisesExactly(Blocked, self.rollback, first)
        self.assertEqual(self.command(str(exc), "./bin/update rollback")[3], str(second.resolve()))

    def test_receipt_of_another_install_names_its_flags_quoted(self):
        self.build(current="v1.1.0")
        path = self.craft_apply(mode="production", production=True, env_file="deploy/prod eu.env")
        exc = self.assertRaisesExactly(Blocked, self.rollback, path)
        self.assertIn("rerun with the same flags (--production, --env-file 'deploy/prod eu.env')", str(exc))


class JournalOwnerHintTests(UpdaterCase):
    """The hint for another install's journal names every flag that install needs."""

    def test_custom_production_env_file_is_named(self):
        self.build("production", current="v1.1.0")
        self.craft_journal("pulled", env_file="deploy/prod-eu.env", production=True)
        exc = self.assertRaisesExactly(Blocked, self.updater().recover)
        self.assertEqual(str(exc), "an interrupted bin/update apply for deploy/prod-eu.env must be recovered before "
                                   "this install can be updated or rolled back: run `./bin/update --production "
                                   "--env-file deploy/prod-eu.env rollback` (without --yes it only recovers that run "
                                   "and changes nothing else); `./bin/update --production --env-file deploy/prod-eu.env "
                                   "apply` would recover it too, but would also continue that install's update")

    def test_a_path_with_a_space_is_quoted(self):
        self.craft_journal("pulled", env_file="/etc/future agi/.env", production=True)
        exc = self.assertRaisesExactly(Blocked, self.updater().recover)
        self.assertIn("run `./bin/update --production --env-file '/etc/future agi/.env' rollback`", str(exc))

    def test_a_journal_of_the_same_file_without_production_is_another_installs(self):
        self.build("production", current="v1.1.0")
        self.craft_journal("pulled", production=False)
        exc = self.assertRaisesExactly(Blocked, self.updater().recover)
        self.assertIn("run `./bin/update rollback` (without --yes it only recovers that run", str(exc))


class ResumeFailureMessageTests(UpdaterCase):
    """A resume that stops before restarting anything says the update it resumed is still unfinished."""

    def stopped(self):
        self.ready.failure = NotReady("the app container keeps restarting")
        self.assertRaisesExactly(FailedDuringRollout, self.apply)
        self.ready.failure = None
        return self.state.receipts()[-1]

    def resume(self, failure, cls):
        self.prune(TARGET)  # pulled only when it is not on the host
        self.inst.failures["pull"] = failure
        return self.assertRaisesExactly(cls, self.apply,
                                        self.plan(env_changes={}, resume=True, current=["v1.1.0"], action="keep"))

    def test_failed_resume(self):
        stopped = self.stopped()
        exc = self.resume(InstallError("`docker compose pull` failed: network unreachable"), FailedBeforeRollout)
        text = str(exc)
        self.assertNotIn("the previous versions are configured and running", text)
        self.assertIn("The update to v1.1.0 that stopped while containers were being replaced is still unfinished: "
                      ".env selects it (FUTURE_AGI_VERSION=v1.1.0)", text)
        self.assertIn("To finish it, run `./bin/update apply`", text)
        self.assertIn(f"to go back, run `./bin/update rollback --receipt {stopped} --yes`", text)
        self.assertIn(f"Receipt: {self.state.receipts()[-1]}", text)
        # The command it names works.
        self.inst.failures.clear()
        self.rollback(Path(stopped))
        self.assertEqual(self.env_bytes(), self.original)

    def test_interrupted_resume(self):
        self.stopped()
        exc = self.resume(KeyboardInterrupt(), InterruptedRun)
        self.assertIn("stopped before restarting anything: interrupted. The update to v1.1.0 that stopped", str(exc))



class RollbackChainTests(UpdaterCase):
    """rollback --receipt of an update that later runs continued points at the newest of them."""

    def setUp(self):
        super().setUp()
        self.build(current="v1.1.0")

    def test_the_newest_rollbackable_run_of_the_chain_is_named(self):
        r1 = self.craft_apply(status="failed_during_rollout")
        r2 = self.craft_apply(status="interrupted_during_rollout", resumes=str(r1))
        r3 = self.craft_apply(resumes=str(r2))
        for receipt in (r1, r2):
            with self.subTest(receipt=receipt.name):
                exc = self.assertRaisesExactly(Blocked, self.rollback, receipt)
                self.assertEqual(str(exc), f"that update was finished by a later run ({r3.resolve()}); to undo it, "
                                           f"run `./bin/update rollback --receipt {r3.resolve()} --yes`")
        self.rollback(r3)
        self.assertEqual(self.last_receipt()["rolls_back"], str(r3.resolve()))

    def test_runs_that_changed_nothing_are_not_named(self):
        r1 = self.craft_apply(status="failed_during_rollout")
        r2 = self.craft_apply(status="interrupted_during_rollout", resumes=str(r1))
        r3 = self.craft_apply(resumes=str(r2))
        self.craft_apply(status="failed_before_rollout", resumes=str(r3))
        exc = self.assertRaisesExactly(Blocked, self.rollback, r1)
        self.assertIn(f"finished by a later run ({r3.resolve()})", str(exc))

    def test_a_newer_update_rolled_back_through_its_last_run_no_longer_blocks(self):
        older = self.craft_apply()
        n1 = self.craft_apply(previous="v1.1.0", target="v1.2.0", status="failed_during_rollout")
        n2 = self.craft_apply(previous="v1.1.0", target="v1.2.0", resumes=str(n1))
        rolled = self.craft_rollback(n2)
        exc = self.assertRaisesExactly(Blocked, self.rollback, n1)
        self.assertEqual(str(exc), f"that update was already rolled back ({rolled.resolve()})")
        self.rollback(older)
        self.assertEqual(self.last_receipt()["rolls_back"], str(older.resolve()))
        self.assertEqual(self.env_value("FUTURE_AGI_VERSION"), "v1.0.0")


class ReceiptOrderTests(UpdaterCase):
    """Receipts keep the order they were written in when the wall clock steps back."""

    def test_a_clock_step_back_does_not_reorder_receipts(self):
        first = self.craft_apply(target="v1.0.5")
        now = time.time_ns()
        with mock.patch.object(time, "time_ns", return_value=now - 3600 * 10 ** 9):
            second = self.craft_apply()
        self.assertEqual(self.state.receipts(), [first, second])
        self.assertEqual(self.state.history(self.inst.identity())[-1]["_path"], str(second))

    def test_rollback_takes_the_update_written_last(self):
        self.apply()
        with mock.patch.object(time, "time_ns", return_value=1):
            self.apply(self.plan(target="v1.2.0", current=["v1.1.0"]))
        self.assertEqual(self.env_value("FUTURE_AGI_VERSION"), "v1.2.0")
        exc = self.assertRaisesExactly(Blocked, self.rollback, self.state.receipts()[0])
        self.assertIn(f"a newer update ({self.state.receipts()[1]}) ran after that one", str(exc))
        self.rollback()
        self.assertEqual(self.env_value("FUTURE_AGI_VERSION"), "v1.1.0")


class LockAndStateAccessTests(UpdaterCase):
    """An unusable lock or state directory is a StateError naming the file, never a traceback."""

    def setUp(self):
        super().setUp()
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            self.skipTest("root reads and writes files whatever their mode")

    def unreadable(self, path):
        os.chmod(path, 0)
        self.addCleanup(os.chmod, path, 0o600)

    def test_an_unopenable_lock_file(self):
        self.state.ensure()
        self.state.lock_path.write_text("pid 1\n")
        self.unreadable(self.state.lock_path)
        for run in (self.apply, self.rollback):
            with self.subTest(run=run.__name__):
                exc = self.assertRaisesExactly(StateError, run)
                self.assertIn(f"cannot open {self.state.lock_path}: [Errno 13] Permission denied", str(exc))
        self.assertEqual(self.env_bytes(), self.original)

    def test_a_lock_owned_by_another_user_says_how_to_fix_it(self):
        self.state.ensure()
        self.state.lock_path.write_text("")
        real_stat = os.stat

        def stat_as_root(path, *args, **kwargs):
            result = real_stat(path, *args, **kwargs)
            if str(path) == str(self.state.lock_path):
                return os.stat_result((result.st_mode, result.st_ino, result.st_dev, result.st_nlink, 0, 0,
                                       result.st_size, 0, 0, 0))
            return result

        with mock.patch.object(os, "open", side_effect=PermissionError(13, "Permission denied")), \
                mock.patch.object(os, "stat", side_effect=stat_as_root):
            with self.assertRaises(StateError) as ctx:
                with self.state.lock():
                    pass
        text = str(ctx.exception)
        self.assertIn(f"cannot open {self.state.lock_path}: [Errno 13] Permission denied. It belongs to root", text)
        self.assertIn("run bin/update as root, or give the state back with `sudo chown -R ", text)
        self.assertTrue(text.endswith(f" {self.state.path}`"), text)

    def test_a_file_system_without_locks(self):
        import errno
        import fcntl

        with mock.patch.object(fcntl, "flock", side_effect=OSError(errno.ENOLCK, "No locks available")):
            with self.assertRaises(StateError) as ctx:
                with self.state.lock():
                    pass
        self.assertIn("file system without working file locks", str(ctx.exception))

    def test_an_unreadable_state_directory(self):
        self.craft_apply()
        os.chmod(self.state.path, 0)
        self.addCleanup(os.chmod, self.state.path, 0o700)
        with self.assertRaises(StateError) as ctx:
            self.state.receipts()
        self.assertIn(f"cannot read {self.state.path}", str(ctx.exception))
        with self.assertRaises(StateError):
            self.state.read_journal()


class LockHolderTests(unittest.TestCase):
    """StateDir.lock_holder: who holds the lock, without ever waiting for it or taking it away."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(os.path.realpath(temporary.name))
        self.state = StateDir(self.root)

    def test_no_state_directory(self):
        self.assertIsNone(self.state.lock_holder())
        self.assertFalse(self.state.path.exists(), "lock_holder creates nothing")

    def test_free_lock(self):
        with self.state.lock():
            pass
        self.assertIsNone(self.state.lock_holder())
        with self.state.lock():  # and it did not keep it
            pass

    def test_held_lock(self):
        with self.state.lock():
            started = time.monotonic()
            self.assertEqual(self.state.lock_holder(), f"pid {os.getpid()}")
            self.assertLess(time.monotonic() - started, 1.0)
            self.assertEqual(self.state.lock_holder(), f"pid {os.getpid()}")
        self.assertIsNone(self.state.lock_holder())

    def test_a_peek_does_not_make_a_starting_run_fail(self):
        self.state.ensure()
        self.state.lock_path.write_text("")
        peeking = threading.Event()
        done = threading.Event()

        def peek():
            while not done.is_set():
                self.state.lock_holder()
                peeking.set()
                time.sleep(0.001)

        thread = threading.Thread(target=peek, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 5)
        self.addCleanup(done.set)
        peeking.wait(5)
        for _ in range(20):
            with self.state.lock():
                pass
        done.set()


class StateDirReceiptNumberTests(unittest.TestCase):
    def test_numbers_only_go_up(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        state = StateDir(Path(os.path.realpath(temporary.name)))
        first = state.write_receipt({"kind": "apply", "status": "succeeded"})
        with mock.patch.object(time, "time_ns", return_value=5):
            second = state.write_receipt({"kind": "apply", "status": "succeeded"})
            third = state.write_receipt({"kind": "apply", "status": "succeeded"})
        self.assertEqual(state.receipts(), [first, second, third])
        numbers = [int(p.stem.rsplit("-", 1)[1]) for p in (first, second, third)]
        self.assertEqual(numbers[1:], [numbers[0] + 1, numbers[0] + 2])

    def test_history_is_scoped_to_one_install(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        state = StateDir(Path(os.path.realpath(temporary.name)))
        identity = {"mode": "production", "production": True, "env_file": "deploy/.env.production", "project": "a"}
        mine = state.write_receipt(dict(identity, kind="apply"))
        for field, value in (("mode", "distributed"), ("production", False), ("env_file", "x.env"), ("project", "b")):
            state.write_receipt(dict(identity, kind="apply", **{field: value}))
        later = state.write_receipt(dict(identity, kind="rollback"))
        self.assertEqual([r["_path"] for r in state.history(identity)], [str(mine), str(later)])
        self.assertEqual(state.history(dict(identity, project="c")), [])


class RealPlanEndToEndTests(UpdaterCase):
    """The real plan (build_plan) driving the real Updater, wired the way bin/update wires them.

    The other tests here hand apply a plan written by hand; these check that
    what plan.py decides and what apply.py does with it agree, run after run:
    the two-step production update, a staged update undone or overtaken by a
    newer release, a stopped rollout, and another install's receipts.
    """

    SOURCE = "/srv/manifest.json"

    def setUp(self):
        super().setUp()
        self.build("production")

    def build(self, mode="standalone", **kwargs):
        super().build(mode, **kwargs)
        inst = self.inst
        # FakeInstallation.service_model: every Future AGI image follows its version key, no other profiles.
        inst.up_pull_never = True
        self.settings = load_settings(self.root, None, environ={})
        self.verification = Verification(True, method="keyless", signer="release-update-manifest.yml",
                                         issuer="https://token.actions.githubusercontent.com", tool="cosign")

    def manifest(self, version=TARGET, *, requires_bootstrap=True):
        components = {g.component: {"image": f"{g.repo}:{version + suffix(g)}",
                                    "digest": digest_of(g.repo, version + suffix(g))} for g in self.groups}
        return {"schema_version": 1, "version": version, "channel": "stable",
                "published_at": "2026-10-19T00:00:00Z", "minimum_version": "v1.0.0", "components": components,
                "deployment": {"files": {}}, "migration": {"requires_bootstrap": requires_bootstrap,
                                                           "rollback": "manual"}}

    def check(self, manifest=None):
        """What `bin/update check` plans: this install's receipts, nothing changed."""

        return build_plan(manifest or self.manifest(), self.verification, self.inst, self.settings,
                          manifest_source=self.SOURCE, history=self.state.history(self.inst.identity()))

    def run_apply(self, manifest=None, **kwargs):
        manifest = manifest or self.manifest()
        kwargs.setdefault("backup_command", BACKUP)

        def make_plan(history):
            return build_plan(manifest, self.verification, self.inst, self.settings, manifest_source=self.SOURCE,
                              history=history)

        self.out = io.StringIO()
        return self.updater().apply(make_plan, manifest, **kwargs)

    def up_calls(self):
        return [call for call in self.compose_calls() if call[0] == "up"]

    def test_production_updates_in_two_steps_around_the_initialization_jobs(self):
        old, new = id_of("futureagi/future-agi", "v1.0.0"), id_of("futureagi/future-agi", TARGET)
        plan = self.check()
        self.assertEqual((plan["decision"], plan["next_command"]),
                         (UPDATE_AVAILABLE, "./bin/update --production apply --stage"))
        exc = self.assertRaisesExactly(Blocked, self.run_apply)
        self.assertIn("production installs update in two steps: ./bin/update --production apply --stage", str(exc))
        self.assertEqual((self.hook.calls, self.receipts(), self.env_bytes()), ([], [], self.original))

        # Step 1: back up, pin, pull and verify; nothing restarts.
        staged = self.run_apply(stage=True)
        self.assertEqual(self.last_receipt()["status"], "staged")
        self.assertEqual(len(self.hook.calls), 1)
        self.assertEqual(self.env_value("FUTURE_AGI_VERSION"), TARGET)
        self.assertEqual((self.up_calls(), self.inst.running["backend"]["image_id"]), ([], old))
        plan = self.check()
        self.assertEqual((plan["decision"], plan["resume"], plan["next_command"], plan["reasons"]),
                         (UPDATE_AVAILABLE, True, "./bin/update --production apply", []))
        self.assertIn("run ./bin/update --production apply (finishes the staged update)", plan_text(plan))

        # Step 2: the release's initialization job ran as a one-off container on the staged image.
        self.inst.extra_containers.append(oneoff_container(
            "postgres-schema-bootstrap", f"futureagi/future-agi:{TARGET}", new, status="exited",
            command=["bash", "./entrypoint.sh"]))
        self.assertEqual(self.check()["reasons"], [])

        # Step 3: apply restarts on the verified images, without a second backup.
        del self.log[:]
        finished = self.run_apply()
        receipt = self.last_receipt()
        self.assertEqual((receipt["status"], receipt["resumes"]), ("succeeded", str(staged)))
        self.assertEqual(receipt["backup"], {"ran": False, "staged": str(staged)})
        self.assertEqual(len(self.hook.calls), 1)
        self.assertEqual(receipt["previous_values"], {"FUTURE_AGI_VERSION": "v1.0.0", "FRONTEND_VERSION": "v1.0.0"})
        [up] = self.up_calls()
        self.assertEqual(up[up.index("--pull") + 1], "never")
        self.assertEqual(self.inst.running["backend"]["image_id"], new)
        plan = self.check()
        self.assertEqual((plan["decision"], plan["next_command"]), (UP_TO_DATE, None))

        # One rollback undoes the whole update: the staging run and the run that finished it.
        self.rollback()
        rollback = self.last_receipt()
        self.assertEqual((rollback["kind"], rollback["rolls_back"]), ("rollback", str(Path(finished).resolve())))
        self.assertEqual(self.env_bytes(), self.original)
        self.assertEqual(self.inst.running["backend"]["image_id"], old)

    def test_a_newer_release_while_one_is_staged_names_both_ways_out(self):
        staged = self.run_apply(stage=True)
        newer = self.manifest("v1.2.0")
        plan = self.check(newer)
        self.assertEqual((plan["decision"], plan["next_command"]), (BLOCKED, None))
        reasons = " ".join(plan["reasons"])
        self.assertIn(f"./bin/update --production apply --manifest {self.SOURCE})", reasons)
        self.assertIn(f"./bin/update --production rollback --receipt {shlex.quote(str(staged))} --yes", reasons)
        exc = self.assertRaisesExactly(Blocked, self.run_apply, newer, stage=True)
        self.assertIn("the update to v1.1.0 is staged", str(exc))
        self.assertEqual((self.last_receipt()["status"], len(self.hook.calls)), ("staged", 1))

        # Dropping the staged update makes the newer release an ordinary two-step update again.
        self.rollback(Path(staged))
        self.assertEqual(self.env_bytes(), self.original)
        plan = self.check(newer)
        self.assertEqual((plan["decision"], plan["next_command"]),
                         (UPDATE_AVAILABLE, "./bin/update --production apply --stage"))

    def test_a_stopped_rollout_is_finished_by_apply_and_cannot_be_staged(self):
        self.build("distributed")
        self.inst.failures["up"] = InstallError("`docker compose up` failed with exit code 1: backend is unhealthy")
        self.assertRaisesExactly(FailedDuringRollout, self.run_apply)
        stopped = self.state.receipts()[-1]
        plan = self.check()
        self.assertEqual((plan["resume"], plan["next_command"]), (True, "./bin/update apply"))
        del self.inst.failures["up"]
        exc = self.assertRaisesExactly(Blocked, self.run_apply, stage=True)
        self.assertIn(f"already stopped while containers were being replaced ({stopped}), so there is nothing "
                      "left to stage: run `./bin/update apply` to finish it, or "
                      f"`./bin/update rollback --receipt {shlex.quote(str(stopped))} --yes` to go back", str(exc))
        self.assertEqual(self.last_receipt()["status"], "failed_during_rollout")
        self.run_apply()
        receipt = self.last_receipt()
        self.assertEqual((receipt["status"], receipt["resumes"]), ("succeeded", str(stopped)))

    def test_a_production_stopped_rollout_names_the_initialization_jobs_when_refusing_to_stage(self):
        self.run_apply(stage=True)
        self.inst.failures["up"] = InstallError("`docker compose up` failed with exit code 1: migrate --check")
        self.assertRaisesExactly(FailedDuringRollout, self.run_apply)
        del self.inst.failures["up"]
        exc = self.assertRaisesExactly(Blocked, self.run_apply, stage=True)
        # Round 5: the plan's jobs text (T22/T25 reworded its shell rule); apply prints the same.
        self.assertIn("nothing left to stage: run the release's initialization jobs first if they have not run, with "
                      f"{jobs_text(self.inst)}, then run `./bin/update --production apply` to finish it", str(exc))
        self.assertIn(f"`{PRODUCTION_COMPOSE} run --rm ...` (deploy/README.md › Upgrading a production install", str(exc))

    def test_other_installs_may_stage_too(self):
        self.build("standalone")
        self.assertEqual(self.check()["next_command"], "./bin/update apply")
        staged = self.run_apply(stage=True)
        self.assertNotIn("initialization jobs", self.out.getvalue())
        plan = self.check()
        self.assertEqual((plan["resume"], plan["next_command"]), (True, "./bin/update apply"))
        self.run_apply()
        receipt = self.last_receipt()
        self.assertEqual((receipt["status"], receipt["resumes"], len(self.hook.calls)), ("succeeded", str(staged), 1))

    def test_another_installs_stopped_update_is_not_this_installs(self):
        other = self.craft_apply(status="failed_during_rollout", env_file="deploy/prod-eu.env")
        plan = self.check()
        self.assertEqual((plan["resume"], plan["next_command"]), (False, "./bin/update --production apply --stage"))
        self.run_apply(stage=True)
        self.assertNotIn("resumes", self.last_receipt())
        self.assertNotEqual(self.state.receipts()[-1], other)


@unittest.skipIf(sys.platform == "win32", "bin/update needs a POSIX system")
class StateDirIntegrationTests(unittest.TestCase):
    """The state directory's own error paths, and the newest receipt of one install (what status shows)."""

    IDENTITY = {"mode": "production", "production": True, "env_file": "deploy/.env.production", "project": "a"}

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(os.path.realpath(temporary.name))
        self.state = StateDir(self.root, ["hunter2-secret"])

    def test_latest_receipt_of_one_install(self):
        mine = self.state.write_receipt(dict(self.IDENTITY, kind="apply"))
        theirs = self.state.write_receipt(dict(self.IDENTITY, kind="apply", env_file="deploy/prod-eu.env"))
        self.assertEqual(self.state.latest_receipt()["_path"], str(theirs))
        self.assertEqual(self.state.latest_receipt(self.IDENTITY)["_path"], str(mine))
        # A field given as None (the project, when Docker cannot say) is not compared.
        renamed = self.state.write_receipt(dict(self.IDENTITY, kind="apply", project="b"))
        self.assertEqual(self.state.latest_receipt(dict(self.IDENTITY, project=None))["_path"], str(renamed))
        self.assertEqual(self.state.latest_receipt(self.IDENTITY)["_path"], str(mine))
        self.assertIsNone(self.state.latest_receipt(dict(self.IDENTITY, mode="distributed")))
        self.assertIsNone(StateDir(self.root / "elsewhere").latest_receipt(self.IDENTITY))

    def test_free_text_inside_lists_and_mappings_is_scrubbed(self):
        record = receipt_module.scrub_record(
            {"error": {"stderr": ["x hunter2-secret y"], "code": 1}, "project": "hunter2-secret"}, ["hunter2-secret"])
        self.assertEqual(record, {"error": {"stderr": ["x *** y"], "code": 1}, "project": "hunter2-secret"})

    def test_an_owner_without_a_user_name_is_named_by_uid(self):
        self.state.ensure()
        me = os.geteuid()
        with mock.patch.object(receipt_module.os, "geteuid", return_value=me + 1), \
                mock.patch("pwd.getpwuid", side_effect=KeyError(me)):
            hint = receipt_module.ownership_hint(self.state.path, self.state.path)
        self.assertEqual(hint, f". It belongs to uid {me}, probably from an earlier run as uid {me} (sudo): run "
                               f"bin/update as uid {me}, or give the state back with `sudo chown -R {me + 1} "
                               f"{shlex.quote(str(self.state.path))}`")

    def test_no_ownership_hint_without_user_ids(self):
        with mock.patch.object(receipt_module.os, "geteuid", None):
            self.assertEqual(receipt_module.ownership_hint(self.root, self.root), "")

    def test_a_receipt_that_is_not_a_json_object(self):
        path = self.state.write_receipt({"kind": "apply"})
        for text, fragment in (("{not json", f"cannot read {path}: "), ("[1, 2]", f"{path} is not a JSON object")):
            with self.subTest(text=text):
                path.write_text(text)
                with self.assertRaises(StateError) as caught:
                    self.state.latest_receipt()
                self.assertIn(fragment, str(caught.exception))

    def test_a_state_directory_whose_mode_cannot_be_set(self):
        with mock.patch.object(receipt_module.os, "chmod", side_effect=PermissionError(1, "Operation not permitted")):
            with self.assertRaises(StateError) as caught:
                self.state.ensure()
        self.assertIn(f"cannot use {self.state.path}: [Errno 1] Operation not permitted", str(caught.exception))

    def test_a_lock_error_other_than_contention(self):
        import fcntl
        with mock.patch.object(fcntl, "flock", side_effect=OSError(errno.EIO, "Input/output error")):
            with self.assertRaises(StateError) as caught:
                with self.state.lock():
                    self.fail("the lock was not taken")
        self.assertIn(f"cannot lock {self.state.lock_path}: [Errno 5] Input/output error", str(caught.exception))

    def test_bookkeeping_errors_around_the_lock_are_ignored(self):
        import fcntl
        real_flock, ran = fcntl.flock, []

        def flock(fd, operation):
            if operation == fcntl.LOCK_UN:
                raise OSError(errno.EBADF, "Bad file descriptor")
            return real_flock(fd, operation)

        with mock.patch.object(receipt_module.os, "ftruncate", side_effect=OSError(errno.EIO, "I/O error")), \
                mock.patch.object(fcntl, "flock", side_effect=flock):
            with self.state.lock():
                ran.append(True)
        self.assertEqual(ran, [True])
        self.assertIsNone(self.state.lock_holder(), "closing the descriptor released the lock")
        with mock.patch.object(receipt_module.os, "pread", side_effect=OSError(errno.EIO, "I/O error")):
            self.assertEqual(StateDir._holder_text(0), "")

    def test_lock_holder_refuses_unknown_lock_state_but_close_releases_failed_unlock(self):
        import fcntl
        self.state.ensure()
        self.state.lock_path.write_text("")
        real_flock = fcntl.flock

        def unlock_fails(fd, operation):
            if operation == fcntl.LOCK_UN:
                raise OSError(errno.EBADF, "Bad file descriptor")
            return real_flock(fd, operation)

        with mock.patch.object(fcntl, "flock", side_effect=OSError(errno.ENOLCK, "No locks available")), \
                self.assertRaises(StateError):
            self.state.lock_holder()
        with mock.patch.object(fcntl, "flock", side_effect=unlock_fails):
            self.assertIsNone(self.state.lock_holder())
        with mock.patch.dict(sys.modules, {"fcntl": None}), self.assertRaises(StateError):
            self.state.lock_holder()


class ReapIntegrationTests(unittest.TestCase):
    """apply._reap and _waitpid on their less common paths."""

    def test_a_process_without_popens_lock_is_waited_for(self):
        child = subprocess.Popen([sys.executable, "-c", "pass"])

        class Process:  # a Popen-like object with no _waitpid_lock (another Python's Popen)
            pid, returncode = child.pid, None

            def __init__(self, timeout_first):
                self.timeout_first = timeout_first

            def wait(self, timeout=None):
                if self.timeout_first:
                    raise subprocess.TimeoutExpired("hook", timeout)
                self.returncode = child.wait()
                return self.returncode

        waited = Process(timeout_first=False)
        apply_module._reap(waited)
        self.assertEqual(waited.returncode, 0)
        # A bounded wait that runs out reaps the child directly.
        second = subprocess.Popen([sys.executable, "-c", "raise SystemExit(3)"])
        Process.pid = second.pid
        timed_out = Process(timeout_first=True)
        apply_module._reap(timed_out)
        self.assertEqual(timed_out.returncode, 3)
        second.returncode = 3  # reaped above; Popen must not wait for it again

    def test_an_exit_status_that_cannot_be_decoded(self):
        process = subprocess.Popen([sys.executable, "-c", "pass"])
        with mock.patch.object(apply_module.os, "waitstatus_to_exitcode", side_effect=ValueError("odd status"),
                               create=True):
            apply_module._waitpid(process)
        self.assertEqual(process.returncode, -1)
        apply_module._waitpid(process)  # already reaped: nothing to collect, nothing raised
        self.assertEqual(process.returncode, -1)


class StaleCheckIntegrationTests(UpdaterCase):
    """The post-rollout check of containers outside the active profiles skips what names no image it can read."""

    def test_a_container_whose_image_cannot_be_parsed_is_not_a_future_agi_one(self):
        self.build("distributed")
        self.inst.extra_containers.append({
            "id": _hex("debug"), "service": "debug-shell", "image_id": "sha256:" + "d" * 64, "image": "Not A Ref!",
            "status": "running", "exit_code": 0, "restart_count": 0, "health": "none", "started_at": "",
            "command": []})
        self.apply()
        self.assertEqual(self.last_receipt()["status"], "succeeded")



# ------------------------------------------------------- round 2, part 2 --

class RestoreFailureCommandTests(UpdaterCase):
    """When putting the version settings back fails too, the message names the exact command to run again."""

    def fail_twice(self, run, *args):
        self.inst.failures["config"] = InstallError("`docker compose config` failed with exit code 15")
        self.env_write_failures[self.env_write_count + 1] = EnvFileError(
            f"cannot write {self.inst.relative(self.env_path)}: No space left on device ({SECRET})")
        return self.assertRaisesExactly(UpdateError, run, *args)

    def test_a_custom_production_env_file_is_named_quoted(self):
        self.build("production")
        path = self.root / "deploy" / "prod eu.env"
        path.write_bytes(self.original)
        self.inst.env_file = self.env_path = self.state.env_path = self.hook.env_path = path
        self.inst.env = read_env(path)
        text = str(self.fail_twice(self.apply))
        self.assertIn("Run `./bin/update --production --env-file 'deploy/prod eu.env' apply` again: it restores them "
                      "first", text)
        command = text[text.index("Run `") + 5:text.index("` again")]
        self.assertEqual(shlex.split(command), ["./bin/update", "--production", "--env-file", "deploy/prod eu.env",
                                                "apply"])
        self.assertIsNotNone(self.journal(), "the journal stays: the next run restores")

    def test_a_rollback_names_the_rollback_to_run_again(self):
        applied = self.applied()
        text = str(self.fail_twice(self.rollback))
        self.assertIn(f"Run `./bin/update rollback --receipt {Path(applied).resolve()} --yes` again: it restores "
                      "them first", text)
        self.assertEqual(self.journal()["kind"], "rollback")

    def test_the_restore_error_is_scrubbed(self):
        text = str(self.fail_twice(self.apply))
        self.assertIn("restoring the previous version settings also failed (cannot write .env: No space left on "
                      "device (***).", text)
        self.assertNotIn(SECRET, text)


class RetriedRollbackMessageTests(UpdaterCase):
    """A retried rollback that stops before restarting anything says the stopped rollback is still unfinished."""

    def setUp(self):
        super().setUp()
        self.build(current="v1.1.0")

    def stopped_rollback(self):
        applied = self.craft_apply()
        self.inst.failures["up"] = InstallError("`docker compose up` failed with exit code 1")
        self.assertRaisesExactly(FailedDuringRollout, self.rollback)
        del self.inst.failures["up"]
        self.assertEqual(self.env_value("FUTURE_AGI_VERSION"), "v1.0.0")
        return applied

    def test_the_retry_does_not_claim_the_previous_versions_run(self):
        applied = self.stopped_rollback()
        self.prune("v1.0.0")  # pulled only when it is not on the host
        self.inst.failures["pull"] = InstallError("`docker compose pull` failed: network unreachable")
        exc = self.assertRaisesExactly(FailedBeforeRollout, self.rollback)
        text = str(exc)
        self.assertNotIn("the previous versions are configured and running", text)
        self.assertNotIn("Nothing was changed", text)
        self.assertIn("rollback stopped before restarting anything: `docker compose pull` failed: network "
                      "unreachable. The rollback to v1.0.0 that stopped while containers were being replaced is still "
                      "unfinished: .env selects FUTURE_AGI_VERSION=v1.0.0, and some containers may still run v1.1.0. "
                      f"To finish it, run `./bin/update rollback --receipt {applied.resolve()} --yes`. Receipt: ", text)
        self.assertEqual(self.env_value("FUTURE_AGI_VERSION"), "v1.0.0")
        # The command it names finishes the rollback.
        del self.inst.failures["pull"]
        self.rollback(applied)
        self.assertEqual(self.last_receipt()["status"], "rolled_back")
        self.assertEqual(self.inst.running["app"]["image_id"], id_of("futureagi/standalone", "v1.0.0"))

    def test_an_interrupted_retry_says_so_too(self):
        self.stopped_rollback()
        self.prune("v1.0.0")  # pulled only when it is not on the host
        self.inst.failures["pull"] = KeyboardInterrupt()
        exc = self.assertRaisesExactly(InterruptedRun, self.rollback)
        self.assertIn("stopped before restarting anything: interrupted. The rollback to v1.0.0 that stopped",
                      str(exc))

    def test_only_a_rollback_that_stopped_while_replacing_containers_is_unfinished(self):
        applied = self.craft_apply()
        # A first rollback that stops before restarting anything changed nothing.
        self.inst.failures["pull"] = InstallError("`docker compose pull` failed: network unreachable")
        exc = self.assertRaisesExactly(FailedBeforeRollout, self.rollback)
        self.assertIn("Nothing was changed: .env still selects FUTURE_AGI_VERSION=v1.1.0, which the stack runs", str(exc))
        self.assertEqual(self.env_bytes(), self.original)
        # Once one stopped while replacing containers, a retry that stops early says that one is unfinished.
        self.inst.failures = {"up": InstallError("`docker compose up` failed with exit code 1")}
        self.assertRaisesExactly(FailedDuringRollout, self.rollback, applied)
        self.prune("v1.0.0")  # pulled only when it is not on the host
        self.inst.failures = {"pull": InstallError("`docker compose pull` failed: network unreachable")}
        exc = self.assertRaisesExactly(FailedBeforeRollout, self.rollback, applied)
        self.assertIn("The rollback to v1.0.0 that stopped while containers were being replaced is still unfinished",
                      str(exc))

    def test_a_rollback_retried_after_one_that_changed_nothing_still_counts_the_stopped_one(self):
        applied = self.stopped_rollback()
        self.prune("v1.0.0")  # pulled only when it is not on the host
        self.inst.failures["pull"] = InstallError("`docker compose pull` failed: network unreachable")
        self.assertRaisesExactly(FailedBeforeRollout, self.rollback)
        exc = self.assertRaisesExactly(FailedBeforeRollout, self.rollback, applied)
        self.assertIn("The rollback to v1.0.0 that stopped while containers were being replaced is still unfinished",
                      str(exc))


class JournalOwnerRecordedFieldsTests(UpdaterCase):
    """Only what a journal records decides whose it is, as `bin/update check` decides."""

    def test_a_journal_that_does_not_record_the_production_flag(self):
        self.build("production", current="v1.1.0")
        self.craft_journal("pulled", production=None)
        recovered = self.updater().recover()
        self.assertEqual(recovered["status"], "interrupted_before_rollout")
        self.assertIsNone(self.journal())

    def test_a_journal_that_does_not_record_the_env_file(self):
        self.build(current="v1.1.0")
        self.craft_journal("pulled", env_file=None)
        self.assertEqual(self.updater().recover()["status"], "interrupted_before_rollout")

    def test_a_journal_of_the_other_side_of_production_without_an_env_file_names_this_one(self):
        self.build("production", current="v1.1.0")
        self.craft_journal("pulled", env_file=None, production=False)
        exc = self.assertRaisesExactly(Blocked, self.updater().recover)
        self.assertEqual(str(exc), "an interrupted bin/update apply for deploy/.env.production must be recovered "
                                   "before this install can be updated or rolled back: run `./bin/update rollback` "
                                   "(without --yes it only recovers that run and changes nothing else); "
                                   "`./bin/update apply` would recover it too, but would also continue that install's "
                                   "update")


@unittest.skipIf(sys.platform == "win32", "bin/update needs a POSIX system")
class ClearJournalErrorTests(unittest.TestCase):
    """A journal that cannot be removed is a StateError that says how to fix the ownership."""

    def test_another_users_state_directory(self):
        if os.geteuid() == 0:
            self.skipTest("the state directory would belong to the user running the tests")
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        state = StateDir(Path(os.path.realpath(temporary.name)))
        state.write_journal({"kind": "apply", "stage": "prepare"})
        real_stat = os.stat

        def stat_as_root(path, *args, **kwargs):
            result = real_stat(path, *args, **kwargs)
            return os.stat_result((result.st_mode, result.st_ino, result.st_dev, result.st_nlink, 0, 0,
                                   result.st_size, 0, 0, 0))

        with mock.patch.object(Path, "unlink", side_effect=PermissionError(13, "Permission denied")), \
                mock.patch.object(receipt_module.os, "stat", side_effect=stat_as_root):
            with self.assertRaises(StateError) as caught:
                state.clear_journal()
        text = str(caught.exception)
        self.assertIn(f"cannot remove {state.journal_path}: [Errno 13] Permission denied. It belongs to ", text)
        self.assertIn("give the state back with `sudo chown -R ", text)



@unittest.skipIf(sys.platform == "win32", "bin/update needs a POSIX system")
class LockUnsupportedTests(unittest.TestCase):
    """A file system whose flock() is not supported at all reads like one without a lock daemon."""

    def test_lock_not_supported(self):
        import fcntl
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        state = StateDir(Path(os.path.realpath(temporary.name)))
        for code in sorted({getattr(errno, n) for n in ("EOPNOTSUPP", "ENOTSUP", "ENOSYS") if hasattr(errno, n)}):
            with self.subTest(errno=code), \
                    mock.patch.object(fcntl, "flock", side_effect=OSError(code, os.strerror(code))):
                with self.assertRaises(StateError) as caught:
                    with state.lock():
                        self.fail("the lock was not taken")
                self.assertIn(f"cannot lock {state.lock_path}: ", str(caught.exception))
                self.assertIn("file system without working file locks", str(caught.exception))



# ------------------------------------------------------------ integration --




class StaleCheckScopeTests(UpdaterCase):
    """The post-rollout check compares each service this run verified, and only those."""

    def test_only_the_verified_services_are_compared(self):
        self.build("distributed")
        config = self.inst.config()
        services = self.inst.futureagi_services(config)
        running = {c["service"]: c["image_id"] for c in self.inst.containers()}
        checked, unchecked = services[0]["service"], services[1]["service"]
        image_ids = {name: image_id for name, image_id in running.items() if name != unchecked}
        self.updater()._stale(config, services, image_ids, {})  # nothing was verified for `unchecked`
        image_ids[checked] = "sha256:" + "0" * 64
        with self.assertRaises(NotReady) as raised:
            self.updater()._stale(config, services, image_ids, {})
        self.assertEqual(str(raised.exception), f"containers still run the previous images: {checked}")



# ------------------------------------------------------------------ round 3 --

PRODUCTION_COMPOSE = ("docker compose --env-file deploy/.env.production -f docker-compose.distributed.yml "
                      "-f deploy/docker-compose.production.yml")


def staged_by(case, plan=None, **kwargs):
    """`apply --stage` of `plan` (the case's default plan) by the case's updater; returns the receipt path."""

    plan = plan or case.plan()
    return case.updater().apply(lambda history: plan, {"version": plan["target_version"]}, backup_command=BACKUP,
                                stage=True, **kwargs)


def finishing_plan(case):
    """The plan of the run finishing a staged (or stopped) update: the env file already selects the target."""

    plan = case.plan(env_changes={}, resume=True, current=["v1.0.0"], action="keep")
    for item in plan["components"]:
        item["current_tags"] = [item["target_tag"]]
    return plan


def backticked(text, start):
    """The command in backticks that starts with `start`, split like a shell would."""

    begin = text.index(f"`{start}")
    return shlex.split(text[begin + 1:text.index("`", begin + 1)])


class RollbackStoppedBeforeRestartTests(UpdaterCase):
    """R1: a rollback that stops before its restart says what the env file selects and what is still pending."""

    def test_rollback_of_a_staged_update_says_it_is_still_pending(self):
        staged = staged_by(self)
        staged_env = self.env_bytes()
        self.prune("v1.0.0")  # pulled only when it is not on the host
        self.inst.failures["pull"] = InstallError("`docker compose pull` failed: network unreachable")
        exc = self.assertRaisesExactly(FailedBeforeRollout, self.rollback)
        text = str(exc)
        self.assertNotIn("Nothing was changed", text)
        self.assertNotIn("configured and running", text)
        self.assertIn("rollback stopped before restarting anything: `docker compose pull` failed: network "
                      "unreachable. The update to v1.1.0 staged earlier is still pending: .env selects it "
                      "(FUTURE_AGI_VERSION=v1.1.0), and the stack still runs v1.0.0. To finish it, run "
                      # Round 5 (G6): the finish command names the staged update's own manifest.
                      f"`./bin/update apply --manifest /srv/manifest.json`; to drop it, run `./bin/update rollback "
                      f"--receipt {staged} --yes`. Receipt: {self.state.receipts()[-1]}", text)
        self.assertEqual(self.env_bytes(), staged_env)
        # The command it names drops the staged update.
        del self.inst.failures["pull"]
        self.rollback(Path(backticked(text, "./bin/update rollback")[3]))
        self.assertEqual(self.env_bytes(), self.original)
        self.assertEqual(self.last_receipt()["status"], "rolled_back")

    def test_the_finish_command_it_names_finishes_the_staged_update(self):
        staged = staged_by(self)
        self.prune("v1.0.0")  # pulled only when it is not on the host
        self.inst.failures["pull"] = KeyboardInterrupt()
        exc = self.assertRaisesExactly(InterruptedRun, self.rollback)
        self.assertIn("rollback stopped before restarting anything: interrupted. The update to v1.1.0 staged earlier "
                      "is still pending", str(exc))
        # Round 5 (G6): with the staged update's own manifest.
        self.assertEqual(backticked(str(exc), "./bin/update apply"),
                         ["./bin/update", "apply", "--manifest", "/srv/manifest.json"])
        del self.inst.failures["pull"]
        self.apply(finishing_plan(self))
        receipt = self.last_receipt()
        self.assertEqual((receipt["status"], receipt["resumes"]), ("succeeded", str(staged)))
        self.assertEqual(self.inst.running["app"]["image_id"], id_of("futureagi/standalone", TARGET))

    def test_rollback_of_an_update_that_stopped_mid_rollout_says_it_is_still_unfinished(self):
        self.ready.failure = NotReady("the app container keeps restarting")
        self.assertRaisesExactly(FailedDuringRollout, self.apply)
        self.ready.failure = None
        stopped = self.state.receipts()[-1]
        self.inst.failures["config"] = InstallError("`docker compose config` failed with exit code 15")
        exc = self.assertRaisesExactly(FailedBeforeRollout, self.rollback)
        text = str(exc)
        self.assertNotIn("Nothing was changed", text)
        self.assertIn("The update to v1.1.0 that stopped while containers were being replaced is still unfinished: "
                      ".env selects it (FUTURE_AGI_VERSION=v1.1.0), and some containers may still run v1.0.0. To "
                      # Round 5 (G6): the finish command names the stopped update's own manifest.
                      f"finish it, run `./bin/update apply --manifest /srv/manifest.json`; to go back, run "
                      f"`./bin/update rollback --receipt {stopped} --yes`. Receipt: ", text)
        self.assertEqual(self.env_value("FUTURE_AGI_VERSION"), TARGET)
        del self.inst.failures["config"]
        self.rollback(stopped)
        self.assertEqual(self.env_bytes(), self.original)

    def test_an_interrupted_rollout_is_named_the_same_way(self):
        self.inst.failures["up"] = KeyboardInterrupt()
        self.assertRaisesExactly(InterruptedRun, self.apply)
        del self.inst.failures["up"]
        self.prune("v1.0.0")  # pulled only when it is not on the host
        self.inst.failures["pull"] = InstallError("network down")
        exc = self.assertRaisesExactly(FailedBeforeRollout, self.rollback)
        self.assertIn("that stopped while containers were being replaced is still unfinished", str(exc))

    def test_rollback_of_a_finished_update_reports_the_env_file_and_the_retry(self):
        applied = self.applied()
        self.prune("v1.0.0")  # pulled only when it is not on the host
        self.inst.failures["pull"] = InstallError("network down")
        exc = self.assertRaisesExactly(FailedBeforeRollout, self.rollback)
        text = str(exc)
        self.assertIn("rollback stopped before restarting anything: network down. Nothing was changed: .env still "
                      "selects FUTURE_AGI_VERSION=v1.1.0, which the stack runs. To try the rollback again, run "
                      f"`./bin/update rollback --receipt {Path(applied).resolve()} --yes`.", text)
        self.assertNotIn("still pending", text)
        del self.inst.failures["pull"]
        self.rollback(Path(backticked(text, "./bin/update rollback")[3]))
        self.assertEqual(self.env_bytes(), self.original)

    def test_rollback_of_a_finished_update_names_a_value_it_could_not_put_back(self):
        self.applied()
        # A quoted value with a line break: no write can put it back on its own.
        self.env_path.write_bytes(self.env_bytes().replace(b"FUTURE_AGI_VERSION=v1.1.0",
                                                           b'FUTURE_AGI_VERSION="v1.1.0\\nnote"'))
        self.inst.env = read_env(self.env_path)

        def edit_then_fail(*args):
            self.env_path.write_bytes(self.env_bytes() + b"ADDED_BY_THE_OPERATOR=1\n")
            raise InstallError("network down")

        self.prune("v1.0.0")  # pulled only when it is not on the host
        self.inst.failures["pull"] = edit_then_fail
        exc = self.assertRaisesExactly(UpdateError, self.rollback)
        self.assertIn('env commit conflict', str(exc))
        self.assertIn('Kept', str(exc))
        self.assertIn(b'ADDED_BY_THE_OPERATOR=1', self.env_bytes())
        self.assertIsNotNone(self.journal())
        before = self.env_bytes(), self.state.journal_path.read_bytes(), self.receipts()
        with self.assertRaises(StateError):
            self.updater().recover()
        self.assertEqual((self.env_bytes(), self.state.journal_path.read_bytes(), self.receipts()), before)

    def test_every_key_the_env_file_selects_is_named_as_found(self):
        self.build("distributed")
        staged_by(self)
        self.prune("v1.0.0")  # pulled only when it is not on the host
        self.inst.failures["pull"] = InstallError("network down")
        exc = self.assertRaisesExactly(FailedBeforeRollout, self.rollback)
        self.assertIn(".env selects it (FRONTEND_VERSION=v1.1.0, FUTURE_AGI_VERSION=v1.1.0)", str(exc))

    def test_production_commands_carry_the_flags_and_quote_the_env_file(self):
        self.build("production")
        path = self.root / "deploy" / "prod eu.env"
        path.write_bytes(self.original)
        self.inst.env_file = self.env_path = self.state.env_path = self.hook.env_path = path
        self.inst.env = read_env(path)
        staged = staged_by(self)
        self.prune("v1.0.0")  # pulled only when it is not on the host
        self.inst.failures["pull"] = InstallError("network down")
        exc = self.assertRaisesExactly(FailedBeforeRollout, self.rollback)
        text = str(exc)
        self.assertIn("deploy/prod eu.env selects it (FRONTEND_VERSION=v1.1.0, FUTURE_AGI_VERSION=v1.1.0)", text)
        # Round 5 (G6): with the staged update's own manifest.
        self.assertEqual(backticked(text, "./bin/update --production --env-file 'deploy/prod eu.env' apply"),
                         ["./bin/update", "--production", "--env-file", "deploy/prod eu.env", "apply", "--manifest",
                          "/srv/manifest.json"])
        self.assertEqual(backticked(text, "./bin/update --production --env-file 'deploy/prod eu.env' rollback"),
                         ["./bin/update", "--production", "--env-file", "deploy/prod eu.env", "rollback", "--receipt",
                          str(Path(staged).resolve()), "--yes"])

    def test_a_retried_stopped_rollback_keeps_its_own_wording(self):
        staged = staged_by(self)
        # Round 5 (G2): a staged update the stack never started is rolled back without a restart. This one was
        # started by hand, so the rollback restarts the stack.
        self.inst.running["app"] = {"image": f"futureagi/standalone:{TARGET}",
                                    "image_id": id_of("futureagi/standalone", TARGET)}
        self.inst.failures["up"] = InstallError("`docker compose up` failed with exit code 1")
        self.assertRaisesExactly(FailedDuringRollout, self.rollback)
        self.prune("v1.0.0")  # pulled only when it is not on the host
        self.inst.failures = {"pull": InstallError("network down")}
        exc = self.assertRaisesExactly(FailedBeforeRollout, self.rollback)
        self.assertIn("The rollback to v1.0.0 that stopped while containers were being replaced is still unfinished",
                      str(exc))
        self.assertIn(f"run `./bin/update rollback --receipt {Path(staged).resolve()} --yes`", str(exc))
        self.assertNotIn("staged earlier", str(exc))


class RollbackKeptKeyTests(UpdaterCase):
    """A rollback restores only the keys its update changed."""

    def test_a_rollback_leaves_a_key_the_update_kept_alone(self):
        # FRONTEND_VERSION already selected the release: the update wrote only FUTURE_AGI_VERSION, so the rollback
        # restores only that key and frontend's tag is no expectation of its configuration check.
        self.build("distributed", values={"FUTURE_AGI_VERSION": "v1.0.0", "FRONTEND_VERSION": TARGET})
        plan = self.plan(env_changes={"FUTURE_AGI_VERSION": TARGET})
        plan["components"][1]["action"] = "keep"
        applied = self.applied(plan)
        self.assertEqual(self.last_receipt()["previous_values"], {"FUTURE_AGI_VERSION": "v1.0.0"})
        self.rollback(Path(applied))
        receipt = self.last_receipt()
        self.assertEqual((receipt["status"], receipt["env_changes"]), ("rolled_back", {"FUTURE_AGI_VERSION": "v1.0.0"}))
        self.assertEqual((self.env_value("FUTURE_AGI_VERSION"), self.env_value("FRONTEND_VERSION")), ("v1.0.0", TARGET))
        self.assertEqual(self.inst.running["frontend"]["image"], f"futureagi/frontend:{TARGET}")


class RollbackRedirectWordingTests(UpdaterCase):
    """R4: rollback --receipt of an update a later run continued words that run by how it ended."""

    def setUp(self):
        super().setUp()
        self.build(current="v1.1.0")

    def test_a_later_run_that_also_stopped_continued_it(self):
        for status in ("failed_during_rollout", "interrupted_during_rollout"):
            with self.subTest(status=status):
                self.build(current="v1.1.0")
                for path in self.state.receipts():
                    path.unlink()
                r1 = self.craft_apply(status="failed_during_rollout")
                r2 = self.craft_apply(status=status, resumes=str(r1))
                exc = self.assertRaisesExactly(Blocked, self.rollback, r1)
                self.assertEqual(str(exc), f"that update was continued by a later run ({r2.resolve()}) that also "
                                           f"stopped; to undo it, run `./bin/update rollback --receipt "
                                           f"{r2.resolve()} --yes`")
                self.assertNotIn("finished", str(exc))

    def test_a_later_run_that_staged_it_again(self):
        s1 = self.craft_apply(status="staged")
        s2 = self.craft_apply(status="staged", resumes=str(s1))
        exc = self.assertRaisesExactly(Blocked, self.rollback, s1)
        self.assertEqual(str(exc), f"that update was staged again by a later run ({s2.resolve()}); to undo it, run "
                                   f"`./bin/update rollback --receipt {s2.resolve()} --yes`")

    def test_a_later_run_that_succeeded_finished_it(self):
        r1 = self.craft_apply(status="staged")
        r2 = self.craft_apply(resumes=str(r1))
        exc = self.assertRaisesExactly(Blocked, self.rollback, r1)
        self.assertEqual(str(exc), f"that update was finished by a later run ({r2.resolve()}); to undo it, run "
                                   f"`./bin/update rollback --receipt {r2.resolve()} --yes`")

    def test_the_newest_run_decides_and_its_command_works(self):
        r1 = self.craft_apply(status="failed_during_rollout")
        r2 = self.craft_apply(resumes=str(r1))
        r3 = self.craft_apply(status="interrupted_during_rollout", resumes=str(r2))
        exc = self.assertRaisesExactly(Blocked, self.rollback, r1)
        self.assertIn(f"continued by a later run ({r3.resolve()}) that also stopped", str(exc))
        self.rollback(Path(backticked(str(exc), "./bin/update rollback")[3]))
        self.assertEqual(self.last_receipt()["rolls_back"], str(r3.resolve()))


class ProductionStoppedRolloutInitJobsTests(UpdaterCase):
    """R3: a production update that stopped mid-rollout names the initialization jobs, for this install."""

    MANIFEST = {"version": TARGET, "migration": {"requires_bootstrap": True}}

    def finish(self, plan=None):
        plan = plan or finishing_plan(self)
        return self.updater().apply(lambda history: plan, self.MANIFEST, backup_command=BACKUP)

    def failed_finish(self):
        staged_by(self)
        self.inst.failures["up"] = InstallError("`docker compose up` failed with exit code 1: service "
                                                "\"postgres-schema-bootstrap\" didn't complete successfully: exit 1")
        return str(self.assertRaisesExactly(FailedDuringRollout, self.finish))

    def test_the_exit_5_message_names_the_initialization_jobs(self):
        self.build("production")
        text = self.failed_finish()
        # Round 5: the plan's jobs text (T22/T25 reworded its shell rule); apply prints the same.
        self.assertIn("To finish, run `./bin/update --production apply` again for v1.1.0. If a bootstrap or schema "
                      "check job failed, the release's initialization jobs probably did not run: run them with "
                      f"{jobs_text(self.inst)}, then `./bin/update --production apply`. To go back, run `./bin/update "
                      "--production rollback --receipt ", text)
        self.assertIn(f"`{PRODUCTION_COMPOSE} run --rm ...` (deploy/README.md › Upgrading a production install", text)

    def test_a_custom_env_file_is_carried_into_both_commands(self):
        self.build("production")
        path = self.root / "deploy" / "prod-eu.env"
        path.write_bytes(self.original)
        self.inst.env_file = self.env_path = self.state.env_path = self.hook.env_path = path
        self.inst.env = read_env(path)
        text = self.failed_finish()
        jobs = text[text.index("If a bootstrap"):]
        self.assertEqual(backticked(jobs, "docker compose")[:4], ["docker", "compose", "--env-file",
                                                                   "deploy/prod-eu.env"])
        self.assertEqual(backticked(jobs, "./bin/update"),
                         ["./bin/update", "--production", "--env-file", "deploy/prod-eu.env", "apply"])

    def test_other_installs_and_rollbacks_do_not_name_them(self):
        self.build("distributed")
        self.assertNotIn("initialization jobs", self.failed_finish())
        self.build("production")
        self.applied()
        self.inst.failures["up"] = InstallError("`docker compose up` failed with exit code 1")
        exc = self.assertRaisesExactly(FailedDuringRollout, self.rollback)
        self.assertNotIn("initialization jobs", str(exc))


class StagedInitJobsCommandTests(UpdaterCase):
    """R5: the staged message prints the Compose command that runs the jobs against this install's env file."""

    def use_env_file(self, relative):
        path = self.root / relative
        path.write_bytes(self.original)
        self.inst.env_file = self.env_path = self.state.env_path = self.hook.env_path = path
        self.inst.env = read_env(path)

    def test_a_custom_env_file_is_named_in_the_jobs_command(self):
        self.build("production")
        self.use_env_file("deploy/prod-eu.env")
        staged_by(self)
        text = self.out.getvalue()
        jobs = text[text.index("Next, run the release's initialization jobs"):]
        self.assertEqual(backticked(jobs, "docker compose"),
                         ["docker", "compose", "--env-file", "deploy/prod-eu.env", "-f",
                          "docker-compose.distributed.yml", "-f", "deploy/docker-compose.production.yml", "run",
                          "--rm", "..."])
        self.assertNotIn("deploy/.env.production", text[text.index("Staged v1.1.0"):])

    def test_a_path_with_a_space_is_quoted(self):
        self.build("production")
        self.use_env_file("deploy/prod eu.env")
        staged_by(self)
        text = self.out.getvalue()
        self.assertIn("`docker compose --env-file 'deploy/prod eu.env' -f docker-compose.distributed.yml", text)

    def test_the_standalone_compose_binary_is_named(self):
        self.build("production")
        self.inst.compose = ["docker-compose"]
        staged_by(self)
        self.assertIn("with `docker-compose --env-file deploy/.env.production -f", self.out.getvalue())

    def test_refusing_to_stage_a_stopped_rollout_names_it_too(self):
        self.build("production")
        self.use_env_file("deploy/prod-eu.env")
        self.craft_apply(status="failed_during_rollout")
        exc = self.assertRaisesExactly(Blocked, staged_by, self, finishing_plan(self))
        self.assertIn("run the release's initialization jobs first if they have not run, with `docker compose "
                      "--env-file deploy/prod-eu.env -f docker-compose.distributed.yml", str(exc))


class StrayContainerRolloutTests(UpdaterCase):
    """R9/R10: only running containers of a disabled profile's service whose key the update changes fail it."""

    def setUp(self):
        super().setUp()
        self.build("distributed")
        self.inst.profile_services.update({
            "worker-default": "futureagi/future-agi:${FUTURE_AGI_VERSION:-latest}",
            "simulation-runner": "futureagi/future-agi-simulation-runner:${SIMULATION_RUNNER_VERSION:-latest}"})

    def container(self, service, version="v1.0.0", status="running", repo="futureagi/future-agi", tag=None,
                  image_id=None):
        return {"id": _hex(service + version + status), "service": service,
                "image_id": image_id or id_of(repo, version), "image": f"{repo}:{tag or version}", "status": status,
                "exit_code": 0, "restart_count": 0, "health": "none", "started_at": "", "command": []}

    def test_an_orphan_of_a_service_the_release_no_longer_defines_never_fails_the_rollout(self):
        # property-catalog-supervisor ran futureagi/future-agi in older releases; no compose file defines it now.
        self.inst.extra_containers.append(self.container("property-catalog-supervisor"))
        self.apply()
        self.assertEqual(self.last_receipt()["status"], "succeeded")
        self.assertIn("Updated to v1.1.0", self.out.getvalue())

    def test_only_the_inactive_service_is_named_next_to_an_orphan(self):
        orphan = self.container("property-catalog-supervisor")
        stray = self.container("worker-default")
        self.inst.extra_containers.extend([orphan, stray])
        self.assertRaisesExactly(FailedDuringRollout, self.apply)
        error = self.last_receipt()["error"]
        self.assertIn("now runs: worker-default runs v1.0.0, not v1.1.0. Enable", error)
        self.assertIn(f"`docker rm -f {stray['id'][:12]}`", error)
        self.assertNotIn("property-catalog-supervisor", error)
        self.assertNotIn(orphan["id"][:12], error)

    def test_a_restarting_inactive_container_counts(self):
        self.inst.extra_containers.append(self.container("worker-default", status="restarting"))
        self.assertRaisesExactly(FailedDuringRollout, self.apply)
        self.assertIn("now runs: worker-default runs v1.0.0, not v1.1.0. Enable", self.last_receipt()["error"])

    def test_a_stopped_inactive_container_never_counts(self):
        self.inst.extra_containers.append(self.container("worker-default", status="exited"))
        self.apply()
        self.assertEqual(self.last_receipt()["status"], "succeeded")

    def test_an_inactive_container_whose_key_this_update_keeps_does_not_count(self):
        # SIMULATION_RUNNER_VERSION already selects the release: the update leaves that key alone.
        self.inst.extra_containers.append(self.container("simulation-runner",
                                                         repo="futureagi/future-agi-simulation-runner"))
        plan = self.plan()
        plan["components"].append(dict(inactive_item(), action="keep", current_version=TARGET,
                                       current_tags=[TARGET]))
        self.apply(plan)
        self.assertEqual(self.last_receipt()["status"], "succeeded")

    def test_an_inactive_container_already_on_the_release_does_not_count(self):
        # Created from :latest, which was the release: its image's version label says so.
        repo = "futureagi/future-agi-simulation-runner"
        image_id = self.publish(repo, "latest", TARGET, image_id="sha256:" + "5" * 64)
        self.inst.local_images[f"{repo}:latest"] = image_id
        self.inst.extra_containers.append(self.container("simulation-runner", repo=repo, tag="latest",
                                                         image_id=image_id))
        plan = self.plan()
        plan["components"].append(inactive_item())
        plan["env_changes"][RUNNER] = TARGET
        self.apply(plan)
        self.assertEqual(self.last_receipt()["status"], "succeeded")

    def test_the_same_container_on_the_previous_release_counts(self):
        repo = "futureagi/future-agi-simulation-runner"
        image_id = self.publish(repo, "latest", "v1.0.0", image_id="sha256:" + "5" * 64)
        self.inst.local_images[f"{repo}:latest"] = image_id
        self.inst.extra_containers.append(self.container("simulation-runner", repo=repo, tag="latest",
                                                         image_id=image_id))
        plan = self.plan()
        plan["components"].append(inactive_item())
        plan["env_changes"][RUNNER] = TARGET
        self.assertRaisesExactly(FailedDuringRollout, self.apply, plan)
        self.assertIn("now runs: simulation-runner runs v1.0.0, not v1.1.0. Enable", self.last_receipt()["error"])

    def test_finishing_a_staged_update_still_counts_the_keys_the_staging_run_changed(self):
        staged_by(self)
        self.inst.extra_containers.append(self.container("worker-default"))
        self.assertRaisesExactly(FailedDuringRollout, self.apply, finishing_plan(self))
        self.assertIn("now runs: worker-default runs v1.0.0, not v1.1.0. Enable", self.last_receipt()["error"])

    def test_a_resume_after_a_resume_still_counts_them(self):
        self.ready.failure = NotReady("the API is not answering")
        self.assertRaisesExactly(FailedDuringRollout, self.apply)
        self.assertRaisesExactly(FailedDuringRollout, self.apply, finishing_plan(self))
        self.ready.failure = None
        self.inst.extra_containers.append(self.container("worker-default"))
        self.assertRaisesExactly(FailedDuringRollout, self.apply, finishing_plan(self))
        self.assertIn("now runs: worker-default runs v1.0.0, not v1.1.0. Enable", self.last_receipt()["error"])

    def test_an_unlabelled_container_from_the_release_tag_with_nothing_verified_does_not_count(self):
        # SIMULATION_RUNNER_VERSION pins a profile that is not enabled: nothing of it is pulled or verified, so the
        # tag it was created from is all there is to go by (the plan judges it the same way).
        repo = "futureagi/future-agi-simulation-runner"
        runner = self.container("simulation-runner", repo=repo, tag=TARGET)
        self.assertIsNone(self.inst.image(runner["image_id"]), "the image carries no version label")
        self.inst.extra_containers.append(runner)
        plan = self.plan()
        plan["components"].append(inactive_item())
        plan["env_changes"][RUNNER] = TARGET
        self.apply(plan)
        self.assertEqual(self.last_receipt()["status"], "succeeded")

    def test_left_behind_reads_the_version_label_as_the_plan_does(self):
        from deploy.update.plan import _stray_on_target

        for image, label, want, behind in (
                ("futureagi/future-agi:v1.0.0", TARGET, TARGET, False),
                ("futureagi/future-agi:v1.0.0", "v1.0.0", TARGET, True),
                ("futureagi/future-agi:v1.0.0", "", TARGET, True),
                ("futureagi/future-agi:v1.0.0", f"{TARGET}-gpu", TARGET, True),
                # A CUDA serving image's label may carry the variant, whichever serving image the stack runs.
                ("futureagi/serving:v1.0.0-gpu", f"{TARGET}-gpu", f"{TARGET}-gpu", False),
                ("futureagi/serving:v1.0.0-gpu", TARGET, f"{TARGET}-gpu", False),
                ("futureagi/serving:v1.0.0-gpu", f"{TARGET}-gpu", TARGET, False),
                ("futureagi/serving:v1.0.0-gpu", "v1.0.0-gpu", TARGET, True)):
            with self.subTest(image=image, label=label, want=want):
                ref = parse_image_ref(image)
                component = component_for(ref)
                variable = COMPONENT_VARIABLES[component]
                container = {"image": image, "image_id": "sha256:" + "e" * 64}
                self.assertEqual(Updater._left_behind(container, label, {}, {variable: want}, {variable}), behind)
                release = want[:-4] if want.endswith("-gpu") else want
                self.assertEqual(_stray_on_target({"version": label, "image": image}, component, release), not behind)

    def test_a_container_whose_image_is_no_reference_is_not_left_behind(self):
        self.assertFalse(Updater._left_behind({"image": "not a reference", "image_id": ""}, "", {},
                                              {"FUTURE_AGI_VERSION": TARGET}, {"FUTURE_AGI_VERSION"}))

    def test_the_model_before_interpolation_is_read_only_when_a_container_is_outside_the_active_one(self):
        # Every profile's services come from Installation.service_model (another `compose config`).
        calls = []
        real = self.inst.service_model
        self.inst.service_model = lambda refresh=False: calls.append(refresh) or real(refresh)
        self.apply()
        self.assertEqual(calls, [])
        self.inst.extra_containers.append(self.container("property-catalog-supervisor"))
        self.rollback()
        # Once before the rollback changes anything (_refuse_strays), once after its restart (_stale).
        self.assertEqual(len(calls), 2)


class RealPlanCase(UpdaterCase):
    """The real plan (build_plan) driving the real Updater (no tests of its own)."""

    REQUIRES_BOOTSTRAP = False

    def build(self, mode="standalone", **kwargs):
        super().build(mode, **kwargs)
        self.inst.up_pull_never = True
        self.settings = load_settings(self.root, None, environ={})
        self.verification = Verification(True, method="keyless", signer="release-update-manifest.yml",
                                         issuer="https://token.actions.githubusercontent.com", tool="cosign")

    def manifest(self, version=TARGET):
        components = {g.component: {"image": f"{g.repo}:{version + suffix(g)}",
                                    "digest": digest_of(g.repo, version + suffix(g))} for g in self.groups}
        return {"schema_version": 1, "version": version, "channel": "stable",
                "published_at": "2026-10-19T00:00:00Z", "minimum_version": "v1.0.0", "components": components,
                "deployment": {"files": {}},
                "migration": {"requires_bootstrap": self.REQUIRES_BOOTSTRAP, "rollback": "manual"}}

    def check(self):
        return build_plan(self.manifest(), self.verification, self.inst, self.settings,
                          history=self.state.history(self.inst.identity()))

    def run_apply(self, **kwargs):
        manifest = self.manifest()
        self.out = io.StringIO()
        return self.updater().apply(
            lambda history: build_plan(manifest, self.verification, self.inst, self.settings, history=history),
            manifest, backup_command=BACKUP, **kwargs)


class StrayPlanAndApplyAgreeTests(RealPlanCase):
    """R9/R10 with the real plan: check and apply judge containers outside the active configuration alike."""

    def setUp(self):
        super().setUp()
        self.build("distributed")
        self.inst.profile_services["worker-default"] = "futureagi/future-agi:${FUTURE_AGI_VERSION:-latest}"

    def stray(self, service, status="running"):
        return {"id": _hex(service), "service": service, "image_id": id_of("futureagi/future-agi", "v1.0.0"),
                "image": "futureagi/future-agi:v1.0.0", "status": status, "exit_code": 0, "restart_count": 0,
                "health": "none", "started_at": "", "command": []}

    def test_an_orphan_is_a_note_and_the_update_succeeds(self):
        self.inst.extra_containers.append(self.stray("property-catalog-supervisor"))
        plan = self.check()
        self.assertEqual((plan["decision"], plan["reasons"]), (UPDATE_AVAILABLE, []))
        self.assertTrue(any("property-catalog-supervisor" in note for note in plan["notes"]), plan["notes"])
        self.run_apply()
        self.assertEqual(self.last_receipt()["status"], "succeeded")

    def test_a_stopped_inactive_container_is_a_note_and_the_update_succeeds(self):
        self.inst.extra_containers.append(self.stray("worker-default", status="exited"))
        plan = self.check()
        self.assertEqual((plan["decision"], plan["reasons"]), (UPDATE_AVAILABLE, []))
        self.run_apply()
        self.assertEqual(self.last_receipt()["status"], "succeeded")

    def test_a_running_inactive_container_blocks_both_before_anything_changes(self):
        self.inst.extra_containers.append(self.stray("worker-default"))
        plan = self.check()
        self.assertEqual(plan["decision"], BLOCKED)
        self.assertTrue(any("worker-default" in reason for reason in plan["reasons"]), plan["reasons"])
        self.assertRaisesExactly(Blocked, self.run_apply)
        self.assertEqual((self.receipts(), self.env_bytes()), ([], self.original))


class StagedFinishFromVersionsTests(RealPlanCase):
    """E3 end to end: a staged install is reported, and finished, from the versions it runs."""

    REQUIRES_BOOTSTRAP = True

    def setUp(self):
        super().setUp()
        self.build("production")

    def test_the_finishing_run_records_the_running_versions(self):
        staged = self.run_apply(stage=True)
        plan = self.check()
        self.assertEqual(plan["current_versions"], ["v1.0.0"])
        self.assertEqual({item["component"]: (item["current_version"], item["action"]) for item in plan["components"]},
                         {"backend": ("v1.0.0", "update"), "frontend": ("v1.0.0", "update")})
        self.run_apply()
        receipt = self.last_receipt()
        self.assertEqual((receipt["status"], receipt["resumes"], receipt["from_versions"]),
                         ("succeeded", str(staged), ["v1.0.0"]))
        self.assertEqual(receipt["previous_values"], {"FUTURE_AGI_VERSION": "v1.0.0", "FRONTEND_VERSION": "v1.0.0"})
        self.assertEqual([e[1] for e in self.log if e[0] == "env_write"][-1], {})

    def test_the_staged_message_and_the_plan_name_the_same_jobs_command(self):
        self.run_apply(stage=True)
        staged_text = self.out.getvalue()
        # Round 5: the plan's jobs text (T22/T25 reworded its shell rule); apply prints the same.
        jobs = jobs_text(self.inst)
        self.assertIn(f"`{PRODUCTION_COMPOSE} run --rm ...` (deploy/README.md › Upgrading a production install", jobs)
        self.assertIn(jobs, staged_text)
        self.assertIn(jobs, plan_text(self.check()))



# ------------------------------------------------------------------ round 4 --

REGISTRY_DOWN = InstallError("`docker compose pull backend` failed with exit code 18: dial tcp: lookup "
                             "registry-1.docker.io: no such host")
RELEASE_OPTIONS = ["--manifest", "/srv/fagi/manifest v1.1.0.json", "--bundle", "/srv/fagi/bundle.json",
                   "--policy", "major", "--channel", "stable"]


def updater_with(case, *, options=(), caller_cwd=None):
    """The case's Updater, given the release options __main__ passes (and where bin/update was run)."""

    if caller_cwd is not None:
        case.inst.caller_cwd = Path(caller_cwd)
    return Updater(case.inst, load_settings(case.root, None, environ={}), case.state, out=case.out,
                   readiness=case.ready, hook_runner=case.hook, command_options=list(options))


def every_backticked(text):
    """Every command in backticks in `text`, split like a shell would."""

    return [shlex.split(command) for command in re.findall(r"`([^`]+)`", text)]


def stray_container(service, version, *, repo="futureagi/future-agi", tag=None, image_id=None, status="running"):
    return {"id": _hex(service + version + status), "service": service, "image_id": image_id or id_of(repo, version),
            "image": f"{repo}:{tag or version}", "status": status, "exit_code": 0, "restart_count": 0,
            "health": "none", "started_at": "", "command": []}


class Round4PullOnlyWhatIsNeededTests(UpdaterCase):
    """F3 (S1): an image already on the host with the expected digest is verified, never pulled again."""

    def test_finishing_a_staged_update_needs_no_registry(self):
        staged = staged_by(self)
        self.inst.failures["pull"] = REGISTRY_DOWN
        del self.log[:]
        self.out = io.StringIO()
        self.apply(finishing_plan(self))
        receipt = self.last_receipt()
        self.assertEqual((receipt["status"], receipt["resumes"]), ("succeeded", str(staged)))
        self.assertEqual([call[0] for call in self.compose_calls()], ["config", "up"])
        self.assertEqual(self.inst.running["app"]["image_id"], id_of("futureagi/standalone", TARGET))
        self.assertIn("Already on this host with the expected digest, not pulled: futureagi/standalone:v1.1.0",
                      self.out.getvalue())
        # Still verified against the signed digest after the restart's journal stage.
        self.assertEqual(receipt["target_image_ids"], {"app": id_of("futureagi/standalone", TARGET)})

    def test_dropping_a_staged_update_needs_no_registry(self):
        staged_by(self)
        self.inst.failures["pull"] = REGISTRY_DOWN
        del self.log[:]
        self.rollback()
        self.assertEqual(self.last_receipt()["status"], "rolled_back")
        self.assertEqual(self.env_bytes(), self.original)
        self.assertNotIn("pull", [call[0] for call in self.compose_calls()])

    def test_rolling_back_a_finished_update_needs_no_registry(self):
        self.build("distributed")
        self.applied()
        self.inst.failures["pull"] = REGISTRY_DOWN
        self.rollback()
        self.assertEqual(self.last_receipt()["status"], "rolled_back")
        self.assertEqual(self.env_bytes(), self.original)
        self.assertEqual({service: self.inst.running[service]["image_id"] for service in ("backend", "worker")},
                         {service: id_of("futureagi/future-agi", "v1.0.0") for service in ("backend", "worker")})
        self.assertEqual(self.inst.running["frontend"]["image_id"], id_of("futureagi/frontend", "v1.0.0"))

    def test_only_an_image_that_is_missing_or_carries_another_digest_is_pulled(self):
        self.build("distributed")
        # frontend's release image is on the host already; the backend's tag names a local build.
        self.inst.local_images["futureagi/frontend:v1.1.0"] = id_of("futureagi/frontend", TARGET)
        local_build = "sha256:" + "6" * 64
        self.inst.metadata[local_build] = {"repo_digests": [], "version": TARGET}
        self.inst.local_images["futureagi/future-agi:v1.1.0"] = local_build
        self.apply()
        self.assertEqual([call for call in self.compose_calls() if call[0] == "pull"], [("pull", "backend", "worker")])
        receipt = self.last_receipt()
        self.assertEqual(receipt["status"], "succeeded")
        # The pulled image replaced the local build, and every service was verified.
        self.assertEqual(receipt["target_image_ids"], {
            "backend": id_of("futureagi/future-agi", TARGET), "worker": id_of("futureagi/future-agi", TARGET),
            "frontend": id_of("futureagi/frontend", TARGET)})

    def test_a_rollback_pulls_only_the_images_no_longer_on_the_host(self):
        self.build("distributed")
        applied = self.applied()
        self.inst.local_images.pop("futureagi/future-agi:v1.0.0")  # pruned since the update
        self.rollback(Path(applied))
        self.assertEqual([call for call in self.compose_calls() if call[0] == "pull"], [("pull", "backend", "worker")])
        self.assertEqual(self.last_receipt()["status"], "rolled_back")

    def test_a_rollback_pulls_a_service_whose_previous_image_it_has_no_record_of(self):
        self.build("distributed")
        applied = Path(self.applied())
        receipt = json.loads(applied.read_text(encoding="utf-8"))
        del receipt["previous_images"]["frontend"]
        applied.write_text(json.dumps(receipt), encoding="utf-8")
        self.rollback(applied)
        # Nothing tells which frontend image ran before: only the registry's can be used.
        self.assertEqual([call for call in self.compose_calls() if call[0] == "pull"], [("pull", "frontend")])


class Round4ResumeSkipsTheBackupTests(UpdaterCase):
    """F4 (S2): finishing an update that stopped mid-rollout runs no backup, like finishing a staged one."""

    def stopped(self, **kwargs):
        self.ready.failure = NotReady("the API did not answer /health/ on port 8000 within 180s")
        exc = self.assertRaisesExactly(FailedDuringRollout, self.apply, None, **kwargs)
        self.ready.failure = None
        self.out = io.StringIO()
        return self.state.receipts()[-1], str(exc)

    def test_the_printed_finish_command_works_after_an_update_started_without_a_backup(self):
        self.build("distributed")
        stopped, text = self.stopped(backup_command=None, allow_no_backup=True)
        self.assertEqual(backticked(text, "./bin/update apply"), ["./bin/update", "apply"])
        # Pasted as printed: no backup flag, and none configured.
        self.apply(finishing_plan(self), backup_command=None)
        receipt = self.last_receipt()
        self.assertEqual((receipt["status"], receipt["backup"]), ("succeeded", {"ran": False, "resumes": str(stopped)}))
        self.assertEqual(self.hook.calls, [])
        self.assertIn(f"Skipping the backup: this run finishes the update to v1.1.0 that stopped while containers "
                      f"were being replaced ({stopped}); that update started without a backup.", self.out.getvalue())

    def test_a_backup_hook_that_fails_now_cannot_stop_the_finish(self):
        stopped, _ = self.stopped()
        self.hook.returncode = 1  # a backup of the half-replaced stack would fail
        self.apply(finishing_plan(self))
        receipt = self.last_receipt()
        self.assertEqual((receipt["status"], receipt["backup"]), ("succeeded", {"ran": False, "resumes": str(stopped)}))
        self.assertEqual(len(self.hook.calls), 1, "only the backup before the update started")
        self.assertIn(f"Skipping the backup: this run finishes the update to v1.1.0 that stopped while containers "
                      f"were being replaced ({stopped}), and the backup taken before that update started covers it.",
                      self.out.getvalue())

    def test_a_finish_after_a_finish_that_stopped_early_follows_the_chain(self):
        stopped, _ = self.stopped()
        self.prune(TARGET)  # pulled only when it is not on the host
        self.inst.failures["pull"] = REGISTRY_DOWN
        self.assertRaisesExactly(FailedBeforeRollout, self.apply, finishing_plan(self))
        self.assertEqual(self.last_receipt()["backup"], {"ran": False, "resumes": str(stopped)})
        del self.inst.failures["pull"]
        self.out = io.StringIO()
        self.apply(finishing_plan(self))
        receipt = self.last_receipt()
        self.assertEqual((receipt["status"], receipt["resumes"], receipt["backup"]),
                         ("succeeded", str(stopped), {"ran": False, "resumes": str(stopped)}))
        self.assertEqual(len(self.hook.calls), 1)
        self.assertIn("the backup taken before that update started covers it", self.out.getvalue())

    def test_the_dry_run_of_a_finish_names_no_backup(self):
        stopped, _ = self.stopped()
        self.apply(finishing_plan(self), dry_run=True)
        text = self.out.getvalue()
        self.assertIn("would set no version keys, verify the images again (pulling only those no longer on this "
                      "host), and restart the stack, finishing the update that stopped while containers were being "
                      f"replaced ({stopped}) (no new backup).", text)
        self.assertNotIn("run the backup", text)


class Round4PrintedCommandsTests(UpdaterCase):
    """F2 (S4, S8, S15): every command bin/update prints reproduces the run that printed it, pasted where it ran."""

    MANIFEST = {"version": TARGET, "migration": {"requires_bootstrap": True}}

    def test_the_staged_message_repeats_the_release_options_and_the_rollback_never_does(self):
        updater = updater_with(self, options=RELEASE_OPTIONS)
        plan = self.plan()
        path = updater.apply(lambda history: plan, {"version": TARGET}, backup_command=BACKUP, stage=True)
        text = self.out.getvalue()
        self.assertEqual(backticked(text, "./bin/update apply"), ["./bin/update", "apply", *RELEASE_OPTIONS])
        self.assertEqual(backticked(text, "./bin/update rollback"),
                         ["./bin/update", "rollback", "--receipt", str(path), "--yes"])

    def test_the_exit_5_message_finishes_with_the_same_release(self):
        self.build("production")
        self.inst.failures["up"] = InstallError("`docker compose up` failed with exit code 1")
        updater = updater_with(self, options=RELEASE_OPTIONS)
        plan = self.plan()
        exc = self.assertRaisesExactly(FailedDuringRollout, updater.apply, lambda history: plan, {"version": TARGET},
                                       backup_command=BACKUP)
        finishes = [argv for argv in every_backticked(str(exc)) if "apply" in argv]
        self.assertEqual(finishes, [["./bin/update", "--production", "apply", *RELEASE_OPTIONS]] * 2)
        [back] = [argv for argv in every_backticked(str(exc)) if "rollback" in argv]
        self.assertEqual(back[:3], ["./bin/update", "--production", "rollback"])
        self.assertNotIn("--manifest", back)

    def test_a_finish_that_stops_early_names_apply_with_them(self):
        self.ready.failure = NotReady("the app container keeps restarting")
        self.assertRaisesExactly(FailedDuringRollout, self.apply)
        self.ready.failure = None
        self.prune(TARGET)  # pulled only when it is not on the host
        self.inst.failures["pull"] = REGISTRY_DOWN
        updater = updater_with(self, options=["--manifest", "https://mirror.example/v1.1.0/manifest.json"])
        plan = finishing_plan(self)
        exc = self.assertRaisesExactly(FailedBeforeRollout, updater.apply, lambda history: plan, {"version": TARGET})
        self.assertEqual(backticked(str(exc), "./bin/update apply"),
                         ["./bin/update", "apply", "--manifest", "https://mirror.example/v1.1.0/manifest.json"])

    def test_the_two_step_refusal_and_the_stage_refusal_carry_them(self):
        self.build("production")
        updater = updater_with(self, options=["--manifest", "/srv/m.json"])
        plan = self.plan()
        exc = self.assertRaisesExactly(Blocked, updater.apply, lambda history: plan, self.MANIFEST,
                                       backup_command=BACKUP)
        self.assertIn("production installs update in two steps: ./bin/update --production apply --stage --manifest "
                      "/srv/m.json (backs up, then pulls and verifies the release; restarts nothing), run the "
                      "release's initialization jobs with ", str(exc))
        self.assertIn(", then ./bin/update --production apply --manifest /srv/m.json. Nothing was changed", str(exc))
        stopped = self.craft_apply(status="failed_during_rollout")
        plan = finishing_plan(self)
        exc = self.assertRaisesExactly(Blocked, updater.apply, lambda history: plan, self.MANIFEST,
                                       backup_command=BACKUP, stage=True)
        self.assertIn("then run `./bin/update --production apply --manifest /srv/m.json` to finish it, or "
                      f"`./bin/update --production rollback --receipt {stopped} --yes` to go back", str(exc))

    def test_run_from_another_directory_every_printed_command_starts_in_the_checkout(self):
        self.build("production")
        elsewhere = self.root / "home dir"
        elsewhere.mkdir()
        self.inst.failures["up"] = InstallError("compose up failed with exit code 1")
        updater = updater_with(self, caller_cwd=elsewhere)
        plan = self.plan()
        exc = self.assertRaisesExactly(FailedDuringRollout, updater.apply, lambda history: plan, {"version": TARGET},
                                       backup_command=BACKUP)
        commands = every_backticked(str(exc))
        # Inspect (ps, logs), the jobs, finish (twice) and go back.
        self.assertEqual(len(commands), 6, commands)
        for argv in commands:
            self.assertEqual(argv[:3], ["cd", str(self.root), "&&"], argv)
        self.assertIn(["cd", str(self.root), "&&", "./bin/update", "--production", "apply"], commands)

    def test_run_in_the_checkout_the_commands_need_no_cd(self):
        self.inst.failures["up"] = InstallError("`docker compose up` failed with exit code 1")
        updater = updater_with(self, caller_cwd=self.root)
        plan = self.plan()
        exc = self.assertRaisesExactly(FailedDuringRollout, updater.apply, lambda history: plan, {"version": TARGET},
                                       backup_command=BACKUP)
        self.assertTrue(all(argv[0] != "cd" for argv in every_backticked(str(exc))))
        self.assertEqual(backticked(str(exc), "./bin/update apply"), ["./bin/update", "apply"])


class Round4ApplyingPlanTextTests(RealPlanCase):
    """F6 (S30): apply prints the plan it carries out, its Decision saying what it does, never what to run."""

    REQUIRES_BOOTSTRAP = True

    def decision(self, text):
        [line] = [line for line in text.splitlines() if line.startswith("  Decision")]
        return line

    def test_finishing_a_staged_production_update(self):
        self.build("production")
        self.run_apply(stage=True)
        self.run_apply()
        decision = self.decision(self.out.getvalue())
        self.assertNotIn("run ./bin/update", decision)
        self.assertNotIn("have not run yet", decision)
        self.assertIn(TARGET, decision)

    def test_a_plain_update(self):
        self.build("distributed")
        # A dry run carries out nothing: like check, it names the command to run.
        self.run_apply(dry_run=True)
        self.assertIn("run ./bin/update apply", self.decision(self.out.getvalue()))
        self.run_apply()
        decision = self.decision(self.out.getvalue())
        self.assertNotIn("run ./bin/update", decision)
        self.assertIn(TARGET, decision)


class Round4JournalOfAnotherInstallTests(UpdaterCase):
    """F7 (S45, S47, S48): another install's journal names the command that only recovers it, by its kind."""

    def setUp(self):
        super().setUp()
        self.build("production")

    def test_apply_names_the_recover_only_command_then_itself(self):
        self.craft_journal("rollout", env_file="deploy/prod-eu.env", production=True)
        updater = updater_with(self, options=["--manifest", "/srv/m.json"])
        plan = self.plan()
        exc = self.assertRaisesExactly(Blocked, updater.apply, lambda history: plan, {"version": TARGET},
                                       backup_command=None, allow_no_backup=True, stage=True)
        self.assertEqual(str(exc), (
            "an interrupted bin/update apply for deploy/prod-eu.env must be recovered before this install can be "
            "updated or rolled back: run `./bin/update --production --env-file deploy/prod-eu.env rollback` (without "
            "--yes it only recovers that run and changes nothing else), then `./bin/update --production apply --stage "
            "--allow-no-backup --manifest /srv/m.json` again; `./bin/update --production --env-file "
            "deploy/prod-eu.env apply` would recover it too, but would also continue that install's update"))
        self.assertIsNotNone(self.journal())

    def test_a_rollback_journal_is_worded_as_a_rollback(self):
        self.craft_journal("env_written", kind="rollback", target_version="v1.0.0", env_file="deploy/prod-eu.env",
                           production=True)
        exc = self.assertRaisesExactly(Blocked, self.rollback)
        text = str(exc)
        self.assertIn("an interrupted bin/update rollback for deploy/prod-eu.env must be recovered", text)
        self.assertIn("then `./bin/update --production rollback --yes` again", text)
        self.assertIn("`./bin/update --production --env-file deploy/prod-eu.env apply` would recover it too, but "
                      "would then plan an update of that install, which can undo the rollback", text)
        self.assertNotIn("continue that install's update", text)

    def test_run_from_another_directory_the_commands_start_in_the_checkout(self):
        self.craft_journal("pulled", env_file="deploy/prod-eu.env", production=True)
        elsewhere = self.root / "elsewhere"
        elsewhere.mkdir()
        exc = self.assertRaisesExactly(Blocked, updater_with(self, caller_cwd=elsewhere).rollback, None, yes=True)
        commands = every_backticked(str(exc))
        self.assertEqual(commands[0], ["cd", str(self.root), "&&", "./bin/update", "--production", "--env-file",
                                       "deploy/prod-eu.env", "rollback"])
        for argv in commands:
            self.assertEqual(argv[:3], ["cd", str(self.root), "&&"])

    def test_the_command_it_names_only_recovers_that_run(self):
        # This install (deploy/.env.production) was killed while updating to v1.2.0, after its env file changed.
        applied = self.applied()
        self.publish_all("v1.2.0")
        self.craft_journal("env_written", target_version="v1.2.0",
                           env_changes={"FUTURE_AGI_VERSION": "v1.2.0", "FRONTEND_VERSION": "v1.2.0"},
                           found_values={"FUTURE_AGI_VERSION": TARGET, "FRONTEND_VERSION": TARGET})
        self.env_path.write_bytes(self.with_versions({"FUTURE_AGI_VERSION": "v1.2.0", "FRONTEND_VERSION": "v1.2.0"}))
        # The operator of another install in the same checkout runs apply and is told what to run.
        other_env = self.root / "deploy" / "prod-eu.env"
        other_env.write_bytes(self.original)
        other = FakeInstallation(self.root, other_env, self.log, self.groups, mode="production", production=True)
        refusal = Updater(other, load_settings(self.root, None, environ={}), self.state, out=io.StringIO(),
                          readiness=self.ready, hook_runner=self.hook)
        exc = self.assertRaisesExactly(Blocked, refusal.apply, lambda history: self.plan(), {"version": TARGET},
                                       backup_command=BACKUP)
        argv = backticked(str(exc), "./bin/update --production rollback")
        self.assertEqual(argv, ["./bin/update", "--production", "rollback"])
        # Pasted: rollback without --yes, as this install.
        del self.log[:]
        self.out = io.StringIO()
        self.assertIsNone(self.updater().rollback(None, yes="--yes" in argv))
        text = self.out.getvalue()
        self.assertIn("Recovered an interrupted apply to v1.2.0: it stopped before restarting anything", text)
        self.assertIn("Nothing else was changed. To roll back v1.1.0 as well (to FRONTEND_VERSION=v1.0.0, "
                      "FUTURE_AGI_VERSION=v1.0.0), run `./bin/update --production rollback --receipt "
                      f"{Path(applied).resolve()} --yes`.", text)
        self.assertIsNone(self.journal())
        self.assertEqual(self.env_bytes(), self.with_versions({"FUTURE_AGI_VERSION": TARGET, "FRONTEND_VERSION": TARGET}))
        self.assertEqual([r["status"] for r in self.receipts()], ["succeeded", "interrupted_before_rollout"])
        self.assertEqual([call for call in self.compose_calls() if call[0] in ("pull", "up")], [])
        self.assertEqual(self.hook.calls[1:], [])

    def publish_all(self, version):
        for g in self.groups:
            self.publish(g.repo, version + suffix(g), version)


class Round4RecoverOnlyRollbackTests(UpdaterCase):
    """F7 (S46): rollback without --yes that recovered a run says so and exits 0; --yes only when there is more to do."""

    def test_with_nothing_to_roll_back_it_never_mentions_yes(self):
        self.build(current="v1.1.0")
        self.craft_journal("pulled")  # the first update was killed before it restarted anything
        self.assertIsNone(self.rollback(yes=False))
        text = self.out.getvalue()
        self.assertIn("Recovered an interrupted apply to v1.1.0", text)
        self.assertIn("Nothing else was changed.", text)
        self.assertNotIn("--yes", text)
        self.assertEqual([r["status"] for r in self.receipts()], ["interrupted_before_rollout"])
        self.assertEqual(self.env_bytes(), self.with_versions({"FUTURE_AGI_VERSION": "v1.0.0"}))

    def test_a_recovered_rollout_names_the_rollback_yes_would_do(self):
        self.applied()
        # Killed while replacing containers on the way from v1.1.0 to v1.2.0.
        self.craft_journal("rollout", target_version="v1.2.0", previous_values={"FUTURE_AGI_VERSION": TARGET},
                           previous_images=self.snapshot_of(TARGET))
        self.out = io.StringIO()
        self.assertIsNone(self.rollback(yes=False))
        recovered = self.state.receipts()[-1]
        text = self.out.getvalue()
        self.assertIn("Recovered an interrupted apply to v1.2.0: it stopped while containers were being replaced", text)
        # Round 5 (T40): the preview a rollback shows before it asks for --yes comes before the offer.
        self.assertLess(text.index("Rolling back v1.2.0 to FUTURE_AGI_VERSION=v1.1.0 (Standalone, project fagi-test)."),
                        text.index("Nothing else was changed"))
        # Round 6 (U20): the update it recovered is unfinished; finishing it and rolling it back are both named.
        self.assertIn("Nothing else was changed, but the update to v1.2.0 is not finished: some containers may run "
                      "v1.2.0 while others still run the versions from before it. To finish it, run "
                      "`./bin/update apply`; to roll it back instead (to FUTURE_AGI_VERSION=v1.1.0), run "
                      f"`./bin/update rollback --receipt {recovered} --yes`.", text)
        self.assertEqual(self.env_value("FUTURE_AGI_VERSION"), TARGET)
        self.assertEqual([call for call in self.compose_calls() if call[0] in ("pull", "up")], [])
        # With nothing left to recover, rollback without --yes asks for it as before.
        self.out = io.StringIO()
        exc = self.assertRaisesExactly(Blocked, self.rollback, yes=False)
        self.assertEqual(str(exc), "rollback not started: rerun with --yes to confirm")
        self.assertIn("Rolling back v1.2.0 to FUTURE_AGI_VERSION=v1.1.0", self.out.getvalue())

    def test_a_receipt_that_cannot_be_rolled_back_now_is_not_offered(self):
        applied = self.applied()
        self.craft_journal("rollout", target_version="v1.2.0")  # a later update, killed mid-rollout
        self.out = io.StringIO()
        self.assertIsNone(self.rollback(Path(applied), yes=False))
        text = self.out.getvalue()
        self.assertIn("it stopped while containers were being replaced", text)
        # A newer update ran after that receipt's: rolling it back is not possible now, so --yes is not offered.
        self.assertIn("Nothing else was changed.", text)
        self.assertNotIn("--yes", text)
        self.assertEqual(self.env_value("FUTURE_AGI_VERSION"), TARGET)


class Round4RollbackOfAnUnfinishedUpdateTextTests(UpdaterCase):
    """S24-S27: a rollback that stops before its restart tells the truth about the update it was undoing."""

    def failing_rollback(self):
        self.prune("v1.0.0")  # pulled only when it is not on the host
        self.inst.failures["pull"] = InstallError("network down")
        return str(self.assertRaisesExactly(FailedBeforeRollout, self.rollback))

    def test_production_names_the_initialization_jobs_before_finishing_a_staged_update(self):
        self.build("production")
        staged = staged_by(self)
        text = self.failing_rollback()
        self.assertIn(
            "The update to v1.1.0 staged earlier is still pending: deploy/.env.production selects it "
            "(FRONTEND_VERSION=v1.1.0, FUTURE_AGI_VERSION=v1.1.0), and the stack still runs v1.0.0. To finish it, run "
            # Round 5: the plan's jobs text (T22/T25 reworded its shell rule), and the staged update's own manifest
            # (G6).
            f"the release's initialization jobs first if they have not run yet, with {jobs_text(self.inst)}, then run "
            "`./bin/update --production apply --manifest /srv/manifest.json`; to drop it, run `./bin/update "
            f"--production rollback --receipt {staged} --yes`.", text)
        self.assertIn(f"`{PRODUCTION_COMPOSE} run --rm ...` (deploy/README.md › Upgrading a production install", text)

    def test_production_names_them_before_finishing_a_stopped_rollout(self):
        self.build("production")
        self.inst.failures["up"] = InstallError("`docker compose up` failed with exit code 1")
        self.assertRaisesExactly(FailedDuringRollout, self.apply)
        del self.inst.failures["up"]
        text = self.failing_rollback()
        self.assertIn("is still unfinished: deploy/.env.production selects it (FRONTEND_VERSION=v1.1.0, "
                      "FUTURE_AGI_VERSION=v1.1.0), and some containers may still run v1.0.0. To finish it, run the "
                      "release's initialization jobs first if they have not run yet, with "
                      f"`{PRODUCTION_COMPOSE} run --rm ...`", text)
        # Round 5 (G6): the finish command names the stopped update's own manifest.
        self.assertLess(text.index("initialization jobs"),
                        text.index("`./bin/update --production apply --manifest /srv/manifest.json`"))

    def test_pins_put_back_by_hand_are_not_called_the_update(self):
        staged = staged_by(self)
        self.env_path.write_bytes(self.original)  # the operator set FUTURE_AGI_VERSION back by hand
        self.inst.env = read_env(self.env_path)
        text = self.failing_rollback()
        self.assertNotIn("selects it", text)
        self.assertNotIn("To finish it", text)
        self.assertIn("rollback stopped before restarting anything: network down. .env selects "
                      "FUTURE_AGI_VERSION=v1.0.0 now, not the update to v1.1.0 staged earlier; the stack still runs "
                      f"v1.0.0. To try the rollback again, run `./bin/update rollback --receipt {staged} --yes`.", text)

    def test_a_partly_reverted_update_quotes_what_the_env_file_selects(self):
        self.build("distributed")
        staged = staged_by(self)
        self.env_path.write_bytes(self.with_versions({"FRONTEND_VERSION": TARGET}))  # FUTURE_AGI_VERSION set back
        self.inst.env = read_env(self.env_path)
        text = self.failing_rollback()
        self.assertNotIn("selects it", text)
        self.assertIn(".env selects FRONTEND_VERSION=v1.1.0, FUTURE_AGI_VERSION=v1.0.0 now, not the update to "
                      "v1.1.0 staged earlier", text)
        # The command it names puts every key back.
        del self.inst.failures["pull"]
        self.rollback(Path(backticked(text, "./bin/update rollback")[3]))
        self.assertEqual(self.env_bytes(), self.original)
        self.assertEqual(self.last_receipt()["rolls_back"], str(staged))


class Round4RollbackStrayContainerTests(UpdaterCase):
    """S41: a rollback judges disabled-profile containers against the release it restores, and names theirs."""

    def setUp(self):
        super().setUp()
        self.distributed()

    def distributed(self, **kwargs):
        self.build("distributed", **kwargs)
        self.inst.profile_services.update({
            "worker-default": "futureagi/future-agi:${FUTURE_AGI_VERSION:-latest}",
            "simulation-runner": "futureagi/future-agi-simulation-runner:${SIMULATION_RUNNER_VERSION:-latest}"})

    def test_a_container_on_the_release_being_undone_blocks_before_anything_changes(self):
        applied = self.applied()
        before, receipts = self.env_bytes(), self.state.receipts()
        stray = stray_container("worker-default", TARGET)
        self.inst.extra_containers.append(stray)
        exc = self.assertRaisesExactly(Blocked, self.rollback)
        self.assertEqual(str(exc), (
            "service worker-default has containers on Future AGI images but is not in the active Compose configuration "
            "(a profile that is not enabled now): worker-default (running, v1.1.0). The rollback to v1.0.0 does not "
            "replace its containers, so they would keep running as they are next to it. Enable the profile "
            "(COMPOSE_PROFILES in .env), or remove it with `docker compose rm -sf worker-default`, then run "
            "`./bin/update rollback --yes` again. Nothing was changed"))
        self.assertEqual((self.env_bytes(), self.state.receipts(), self.journal()), (before, receipts, None))
        self.assertEqual(self.compose_calls(), [])
        # Once it is removed, the rollback runs.
        self.inst.extra_containers.remove(stray)
        self.rollback()
        self.assertEqual((self.last_receipt()["status"], self.last_receipt()["rolls_back"]),
                         ("rolled_back", str(applied)))

    def test_one_already_on_the_restored_release_or_stopped_does_not_block(self):
        self.applied()
        self.inst.extra_containers.extend([stray_container("worker-default", "v1.0.0"),
                                           stray_container("worker-backfill", TARGET, status="exited"),
                                           oneoff_container("worker-default", f"futureagi/future-agi:{TARGET}",
                                                            id_of("futureagi/future-agi", TARGET))])
        self.rollback()
        self.assertEqual(self.last_receipt()["status"], "rolled_back")

    def test_a_container_started_during_the_restart_is_named_with_its_release(self):
        self.applied()
        stray = stray_container("worker-default", TARGET)
        self.inst.failures["up"] = lambda *args: self.inst.extra_containers.append(stray)
        self.assertRaisesExactly(FailedDuringRollout, self.rollback)
        self.assertEqual(self.last_receipt()["error"], (
            "containers of services outside the active Compose profiles run another release than the stack now runs: "
            "worker-default runs v1.1.0, not v1.0.0. Enable their profile (COMPOSE_PROFILES in .env) so Compose "
            f"recreates them on the version .env selects, or remove them with `docker rm -f {stray['id'][:12]}`"))

    def test_an_inactive_pin_the_rollback_moves_back_counts_at_the_restored_value(self):
        self.distributed(values={"FUTURE_AGI_VERSION": "v1.0.0", "FRONTEND_VERSION": "v1.0.0", RUNNER: "v1.0.0"})
        plan = self.plan()
        plan["components"].append(dict(inactive_item(), configured="v1.0.0", current_version="v1.0.0",
                                       current_tags=["v1.0.0"]))
        plan["env_changes"][RUNNER] = TARGET
        self.applied(plan)
        repo = "futureagi/future-agi-simulation-runner"
        stray = stray_container("simulation-runner", TARGET, repo=repo)
        self.inst.extra_containers.append(stray)
        # No active service of the runner: the tag it was created from counts, and it is the one being undone.
        exc = self.assertRaisesExactly(Blocked, self.rollback)
        self.assertIn("(a profile that is not enabled now): simulation-runner (running, no version label). The "
                      "rollback to v1.0.0 does not replace its containers", str(exc))
        self.assertEqual(self.env_value(RUNNER), TARGET)
        self.inst.extra_containers.remove(stray)
        self.inst.failures["up"] = lambda *args: self.inst.extra_containers.append(stray)
        self.assertRaisesExactly(FailedDuringRollout, self.rollback)
        self.assertIn(f"now runs: simulation-runner runs {repo}:v1.1.0 (no version label), not v1.0.0. Enable",
                      self.last_receipt()["error"])
        self.assertEqual(self.env_value(RUNNER), "v1.0.0")

    def test_a_key_the_rollback_does_not_move_is_not_judged(self):
        # A :latest install that ran v1.1.0 was pinned to it: rolling that back selects v1.1.0 again.
        self.distributed(current=TARGET, values={"FUTURE_AGI_VERSION": "", "FRONTEND_VERSION": ""})
        self.applied(self.plan(action="pin"))
        self.inst.extra_containers.append(stray_container("worker-default", "v1.0.0"))
        self.rollback()
        receipt = self.last_receipt()
        self.assertEqual((receipt["status"], receipt["env_changes"]),
                         ("rolled_back", {"FUTURE_AGI_VERSION": TARGET, "FRONTEND_VERSION": TARGET}))


class Round4StrayJudgementTests(RealPlanCase):
    """S40 (apply side): apply's check after the restart is the plan's judgement (plan.on_release)."""

    def test_left_behind_is_the_plans_on_release(self):
        from deploy.update.plan import on_release

        backend = {"backend": {"sha256:" + "1" * 64}}
        for image, label, want, verified in (
                ("futureagi/future-agi:v1.1.0", "", TARGET, backend),
                ("futureagi/future-agi:v1.1.0", "", TARGET, {}),
                ("futureagi/future-agi:v1.0.0", TARGET, TARGET, backend),
                ("futureagi/future-agi:v1.0.0", "", TARGET, {}),
                ("futureagi/serving:v1.0.0-gpu", f"{TARGET}-gpu", f"{TARGET}-gpu", {}),
                ("futureagi/serving:v1.1.0-gpu", "", f"{TARGET}-gpu", {"serving_gpu": {"sha256:" + "2" * 64}})):
            with self.subTest(image=image, label=label, verified=bool(verified)):
                variable = COMPONENT_VARIABLES[component_for(parse_image_ref(image))]
                container = {"image": image, "image_id": "sha256:" + "e" * 64}
                behind = Updater._left_behind(container, label, verified, {variable: want}, {variable})
                component = component_for(parse_image_ref(image))
                self.assertEqual(behind, not on_release(image, label, want, by_tag=not verified.get(component)))

    def test_left_behind_asks_the_plans_judgement(self):
        calls = []

        def judged(image, label, tag, *, by_tag):
            calls.append((image, label, tag, by_tag))
            return True

        container = {"image": "futureagi/future-agi:v1.0.0", "image_id": "sha256:" + "e" * 64}
        with mock.patch.object(apply_module, "on_release", judged):
            self.assertFalse(Updater._left_behind(container, "v1.0.0", {"backend": {"sha256:" + "1" * 64}},
                                                  {"FUTURE_AGI_VERSION": TARGET}, {"FUTURE_AGI_VERSION"}))
        self.assertEqual(calls, [("futureagi/future-agi:v1.0.0", "v1.0.0", TARGET, False)])

    def test_an_unlabelled_container_from_the_release_tag_blocks_check_and_apply_alike(self):
        self.build("distributed")
        self.inst.profile_services["worker-default"] = "futureagi/future-agi:${FUTURE_AGI_VERSION:-latest}"
        # Created from the release's tag, but not the image the update verifies, and with no version label.
        self.inst.extra_containers.append(stray_container("worker-default", TARGET, image_id="sha256:" + "4" * 64))
        plan = self.check()
        self.assertEqual(plan["decision"], BLOCKED)
        self.assertRaisesExactly(Blocked, self.run_apply)
        self.assertEqual((self.receipts(), self.env_bytes()), ([], self.original))


class Round4IncompleteServiceModelTests(UpdaterCase):
    """F1: a container of a service the service model may have missed counts as a disabled profile's."""

    def test_an_unknown_running_container_on_the_old_release_fails_the_rollout(self):
        self.build("distributed")
        self.inst.model_complete = False  # a profile's services could not be resolved
        self.inst.extra_containers.append(stray_container("worker-default", "v1.0.0"))
        self.assertRaisesExactly(FailedDuringRollout, self.apply)
        self.assertIn("now runs: worker-default runs v1.0.0, not v1.1.0. Enable", self.last_receipt()["error"])

    def test_with_a_complete_model_it_is_an_orphan(self):
        self.build("distributed")
        self.inst.extra_containers.append(stray_container("worker-default", "v1.0.0"))
        calls = []
        real = self.inst.service_model
        self.inst.service_model = lambda refresh=False: calls.append(refresh) or real(refresh)
        self.apply()
        self.assertEqual(self.last_receipt()["status"], "succeeded")
        self.assertEqual(calls, [False])



class IntegratorRound4DecisionLineTests(RealPlanCase):
    """F6 (S30) end to end: apply's Decision line says what this run does; a run refused before it starts says what
    to run instead, as check does."""

    REQUIRES_BOOTSTRAP = True

    def setUp(self):
        super().setUp()
        self.build("production")

    def decision(self):
        return [line for line in self.out.getvalue().splitlines() if line.startswith("  Decision")][0]

    def test_a_plain_apply_that_must_stage_names_the_stage_and_claims_nothing(self):
        exc = self.assertRaisesExactly(Blocked, self.run_apply)
        self.assertIn("production installs update in two steps", str(exc))
        self.assertEqual(self.decision(), "  Decision      update available: run ./bin/update --production apply "
                                          "--stage (the first of two steps, see below)")
        self.assertEqual((self.receipts(), self.env_bytes()), ([], self.original))

    def test_apply_stage_says_it_stages_and_the_finishing_apply_that_it_finishes(self):
        self.run_apply(stage=True)
        self.assertEqual(self.decision(), "  Decision      update available: staging v1.1.0 (pulls and verifies it, "
                                          "restarts nothing)")
        self.run_apply()
        # Round 5 (G4): the staged images are on this host, so the finishing run says it reuses them.
        self.assertEqual(self.decision(), "  Decision      update available: finishing the staged update to v1.1.0 "
                                          "(reuses its images already on this host, verified again, not pulled)")
        self.assertEqual(self.last_receipt()["status"], "succeeded")

    def test_a_stage_refused_after_a_stopped_rollout_names_the_finishing_apply(self):
        self.run_apply(stage=True)
        self.inst.failures["up"] = InstallError("`docker compose up` failed with exit code 1: migrate --check")
        self.assertRaisesExactly(FailedDuringRollout, self.run_apply)
        del self.inst.failures["up"]
        exc = self.assertRaisesExactly(Blocked, self.run_apply, stage=True)
        self.assertIn("nothing left to stage", str(exc))
        decision = self.decision()
        self.assertTrue(decision.startswith("  Decision      update available: if the release's initialization jobs "
                                            "have not run yet, run them first with `"), decision)
        self.assertTrue(decision.endswith("; then run ./bin/update --production apply (finishes the stopped update)"),
                        decision)

    def test_a_dry_run_names_the_command_as_check_does(self):
        self.run_apply(stage=True, dry_run=True)
        self.assertEqual(self.decision(), "  Decision      update available: run ./bin/update --production apply "
                                          "--stage (the first of two steps, see below)")


class IntegratorRound4InitJobsTests(UpdaterCase):
    """S31 on apply's side: every pointer to the release's initialization jobs carries the shell rule the plan's do."""

    # Round 5: the plan's jobs text (T22/T25 reworded its shell rule); apply prints the same.
    # Round 6 (H3): the one shared sentence, which no longer credits deploy/setup.sh.
    RULE = "Run it from a shell that does not export any key of {env} (Compose prefers your shell over the env file)."

    def test_the_staged_message_and_the_two_step_refusal(self):
        self.build("production")
        rule = self.RULE.format(env="deploy/.env.production")
        staged_by(self)
        # Round 6 (H3): the shared sentence follows the procedure's name.
        self.assertIn(f"which now use these images, with `{PRODUCTION_COMPOSE} run --rm ...` (deploy/README.md › "
                      f"Upgrading a production install", self.out.getvalue())
        self.assertIn(rule, self.out.getvalue())
        updater = self.updater()
        exc = self.assertRaisesExactly(Blocked, updater.apply, lambda history: self.plan(),
                                       {"version": TARGET, "migration": {"requires_bootstrap": True}},
                                       backup_command=BACKUP)
        self.assertIn(rule, str(exc))

    def test_it_names_this_installs_env_file(self):
        self.build("production")
        path = self.root / "deploy" / "prod eu.env"
        path.write_bytes(self.env_bytes())
        self.inst.env_file = self.env_path = self.state.env_path = self.hook.env_path = path
        self.assertEqual(self.updater()._init_jobs(), jobs_text(self.inst))
        self.assertIn(self.RULE.format(env="deploy/prod eu.env"), self.updater()._init_jobs())
        self.assertIn("--env-file 'deploy/prod eu.env'", self.updater()._init_jobs())



class IntegratorRound4ApplyEdgeTests(UpdaterCase):
    """Edges of this round's apply rules (S25/S27, S2, S41)."""

    def test_a_resume_that_finds_the_update_no_longer_selected_says_what_is_selected(self):
        # A plan handed to apply that resumes an update whose key was set back by hand meanwhile (the real plan
        # abandons such an update, F5): the env file then selects neither the update nor what it was resuming.
        self.ready.failure = NotReady("the app container keeps restarting")
        self.assertRaisesExactly(FailedDuringRollout, self.apply)
        self.ready.failure = None
        stopped = self.state.receipts()[-1]
        self.real_write(self.inst.env_file, {"FUTURE_AGI_VERSION": "v1.0.0"})
        exc = self.assertRaisesExactly(FailedBeforeRollout, self.apply,
                                       self.plan(env_changes={}, resume=True, current=["v1.1.0"], action="keep"))
        text = str(exc)
        self.assertIn(".env selects FUTURE_AGI_VERSION=v1.0.0 now, not all of the update to v1.1.0 that stopped while "
                      "containers were being replaced. To go back to the versions from before that update, run "
                      f"`./bin/update rollback --receipt {stopped} --yes`; `./bin/update check` shows what an update "
                      "would do now.", text)
        self.assertNotIn("To finish it", text)

    def test_a_resume_chain_whose_first_receipt_is_gone_ran_no_backup(self):
        history = [{"_path": "/s/r2.json", "backup": {"ran": False}, "resumes": "/s/gone.json"}]
        self.assertFalse(Updater._backup_ran({"backup": {"ran": False}, "resumes": "/s/r2.json"}, history))
        self.assertTrue(Updater._backup_ran({"resumes": "/s/r1.json"},
                                            [{"_path": "/s/r1.json", "backup": {"ran": True}}]))
        # A chain that loops back on itself ends.
        self.assertFalse(Updater._backup_ran({"resumes": "/s/r3.json"}, [{"_path": "/s/r3.json", "resumes": "/s/r3.json"}]))

    def test_the_release_a_stray_should_run_is_the_tag_its_key_selects(self):
        tags = {"FUTURE_AGI_VERSION": "v1.0.0"}
        self.assertEqual(Updater._stray_want({"image": "futureagi/future-agi:v1.1.0"}, tags), "v1.0.0")
        self.assertEqual(Updater._stray_want({"image": "futureagi/frontend:v1.1.0"}, tags), "")
        self.assertEqual(Updater._stray_want({"image": "redis:7"}, tags), "")
        self.assertEqual(Updater._stray_want({"image": "not a reference"}, tags), "")

    def test_a_rollback_with_nothing_outside_the_active_model_reads_no_service_model(self):
        self.build("distributed")
        self.applied()
        calls = []
        self.inst.service_model = lambda refresh=False: calls.append(refresh) or {"services": {}, "complete": True}
        self.inst.extra_containers.append(stray_container("worker-default", TARGET, status="exited"))
        self.rollback()
        self.assertEqual((self.last_receipt()["status"], calls), ("rolled_back", []))



# ------------------------------------------------------------------ round 5 --

# Git for the temporary checkouts below: no system or global configuration (signing, hooks, a default branch).
GIT_ENV = {"GIT_AUTHOR_NAME": "Release", "GIT_AUTHOR_EMAIL": "release@example.com", "GIT_COMMITTER_NAME": "Release",
           "GIT_COMMITTER_EMAIL": "release@example.com", "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull}


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True,
                          env=dict(os.environ, **GIT_ENV)).stdout.decode("utf-8")


def run_pasted(argv, cwd):
    """A printed command (`git checkout ...`, split like a shell would), run where the operator would paste it."""

    if argv[:1] == ["cd"]:
        cwd, argv = argv[1], argv[3:]
    subprocess.run(argv, cwd=str(cwd), check=True, capture_output=True, env=dict(os.environ, **GIT_ENV))


def decision_line(text):
    return [line for line in text.splitlines() if line.startswith("  Decision")][0]


class GitCheckoutCase(UpdaterCase):
    """A checkout that is a git work tree with release tags: v1.0.0, then v1.1.0 changing deployment files."""

    LISTED = ["docker-compose.yml", "docker-compose.distributed.yml", "deploy/clickhouse"]
    V1_0 = {"docker-compose.yml": "services:\n  app:\n    image: futureagi/standalone:${FUTURE_AGI_VERSION:-latest}\n",
            "docker-compose.distributed.yml": "services:\n  backend:\n    image: futureagi/future-agi\n",
            "deploy/clickhouse/users.d/default.xml": "<clickhouse><users/></clickhouse>\n"}
    V1_1 = {"docker-compose.yml": ("services:\n  app:\n    image: futureagi/standalone:${FUTURE_AGI_VERSION:-latest}\n"
                                   "    volumes:\n      - telemetry-data:/data\n"),
            "deploy/clickhouse/users.d/default.xml": "<clickhouse><users><default/></users></clickhouse>\n"}

    def write(self, files):
        for name, text in files.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")

    def release_repo(self, *, listed=None, target_files=None, tags=("v1.0.0", "v1.1.0")):
        """Commit v1.0.0's files, tag them, commit v1.1.0's changes and tag those; the checkout is left at v1.1.0."""

        (self.root / ".gitignore").write_text(".env\n.futureagi-updates/\ndeploy/*.env*\n!deploy/.env.production.example\n",
                                              encoding="utf-8")
        self.write({"deploy/update/deployment-files.txt": "\n".join(self.LISTED) + "\n", **self.V1_0})
        git(self.root, "init", "-q")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", "v1.0.0")
        if "v1.0.0" in tags:
            git(self.root, "tag", "v1.0.0")
        self.write(dict(self.V1_1, **(target_files or {})))
        if listed is not None:
            self.write({"deploy/update/deployment-files.txt": "\n".join(listed) + "\n"})
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", "v1.1.0")
        if "v1.1.0" in tags:
            git(self.root, "tag", "v1.1.0")

    def text_of(self, name):
        return (self.root / name).read_text(encoding="utf-8")


class Round5RollbackDeploymentFilesTests(GitCheckoutCase):
    """G2 (T50, T61): a rollback restarts the previous images only with the previous release's deployment files."""

    REFUSAL = ("The rollback starts v1.0.0's images, which need v1.0.0's deployment files; these differ in this "
               "checkout: docker-compose.yml, deploy/clickhouse. Restore v1.0.0's versions with `git checkout v1.0.0 "
               "-- docker-compose.yml deploy/clickhouse` (bin/update and your env file stay as they are), then run "
               "`{again}` again. Before updating again, run `git checkout v1.1.0 -- docker-compose.yml "
               "deploy/clickhouse`.")

    def test_differing_files_block_before_anything_changes_and_the_printed_commands_work(self):
        self.release_repo()
        applied = self.applied()
        before, receipts = self.env_bytes(), self.state.receipts()
        exc = self.assertRaisesExactly(Blocked, self.rollback)
        self.assertEqual(exc.exit_code, 3)
        self.assertEqual(str(exc), self.REFUSAL.format(again="./bin/update rollback --yes"))
        self.assertEqual((self.env_bytes(), self.state.receipts(), self.journal()), (before, receipts, None))
        self.assertEqual(self.compose_calls(), [])
        # Pasted as printed: the checkout gets v1.0.0's files (the env file and bin/update stay), and the rollback runs.
        run_pasted(backticked(str(exc), "git checkout v1.0.0"), self.root)
        self.assertEqual(self.text_of("docker-compose.yml"), self.V1_0["docker-compose.yml"])
        self.assertEqual(self.env_bytes(), before)
        path = self.rollback()
        self.assertEqual((self.last_receipt()["status"], self.last_receipt()["rolls_back"]),
                         ("rolled_back", str(Path(applied).resolve())))
        self.assertEqual(Path(path), self.state.receipts()[-1])
        self.assertEqual(self.env_bytes(), self.original)
        # Before the next update: the undone release's files come back.
        run_pasted(backticked(str(exc), "git checkout v1.1.0"), self.root)
        self.assertEqual(self.text_of("docker-compose.yml"), self.V1_1["docker-compose.yml"])
        self.assertEqual(git(self.root, "status", "--porcelain"), "")

    def test_without_yes_it_refuses_before_asking_for_it(self):
        self.release_repo()
        self.applied()
        exc = self.assertRaisesExactly(Blocked, self.rollback, yes=False)
        self.assertEqual(str(exc), self.REFUSAL.format(again="./bin/update rollback"))
        self.assertIn("Rolling back v1.1.0 to FUTURE_AGI_VERSION=v1.0.0 (Standalone, project fagi-test).",
                      self.out.getvalue())
        self.assertEqual(self.log, [])

    def test_matching_files_say_nothing_and_roll_back(self):
        self.release_repo()
        self.applied()
        git(self.root, "checkout", "-q", "v1.0.0", "--", "docker-compose.yml", "deploy/clickhouse")
        self.rollback()
        self.assertEqual(self.last_receipt()["status"], "rolled_back")
        self.assertNotIn("deployment files", self.out.getvalue())

    def test_run_from_another_directory_the_git_commands_start_in_the_checkout(self):
        self.release_repo()
        self.applied()
        elsewhere = self.root / "elsewhere"
        elsewhere.mkdir()
        exc = self.assertRaisesExactly(Blocked, updater_with(self, caller_cwd=elsewhere).rollback, None, yes=True)
        commands = [argv for argv in every_backticked(str(exc)) if "git" in argv]
        self.assertEqual(len(commands), 2, commands)
        for argv in commands:
            self.assertEqual(argv[:4], ["cd", str(self.root), "&&", "git"])
        run_pasted(commands[0], elsewhere)
        self.assertEqual(self.text_of("docker-compose.yml"), self.V1_0["docker-compose.yml"])

    def test_a_file_the_previous_release_lacks_counts_only_when_the_compose_files_use_it(self):
        listed = self.LISTED + ["deploy/telemetry.xml", "deploy/unused.txt"]
        self.release_repo(listed=listed, target_files={
            "docker-compose.yml": self.V1_1["docker-compose.yml"] + "      - ./deploy/telemetry.xml:/etc/t.xml\n",
            "deploy/telemetry.xml": "<telemetry/>\n", "deploy/unused.txt": "not referenced\n"})
        self.applied()
        exc = self.assertRaisesExactly(Blocked, self.rollback)
        self.assertEqual(str(exc), (
            "The rollback starts v1.0.0's images, which need v1.0.0's deployment files; these differ in this "
            "checkout: docker-compose.yml, deploy/clickhouse, deploy/telemetry.xml (not in v1.0.0). Restore v1.0.0's "
            "versions with `git checkout v1.0.0 -- docker-compose.yml deploy/clickhouse` and move "
            "deploy/telemetry.xml out of the checkout (bin/update and your env file stay as they are), then run "
            "`./bin/update rollback --yes` again. Before updating again, run `git checkout v1.1.0 -- "
            "docker-compose.yml deploy/clickhouse deploy/telemetry.xml`."))
        # The git command works pasted; once v1.0.0's compose file is back, nothing uses the new file any more.
        run_pasted(backticked(str(exc), "git checkout v1.0.0"), self.root)
        self.rollback()
        self.assertEqual(self.last_receipt()["status"], "rolled_back")

    def test_an_untracked_file_in_a_directory_entry_is_named(self):
        self.release_repo()
        self.applied()
        git(self.root, "checkout", "-q", "v1.0.0", "--", "docker-compose.yml", "deploy/clickhouse")
        self.write({"deploy/clickhouse/users.d/zz-mine.xml": "<mine/>\n"})
        exc = self.assertRaisesExactly(Blocked, self.rollback)
        self.assertIn("these differ in this checkout: deploy/clickhouse. Restore", str(exc))
        self.assertIn(" deploy/clickhouse/users.d/zz-mine.xml is not tracked by git, so `git checkout` leaves it in "
                      "place: move it out of the checkout as well.", str(exc))

    def test_a_checkout_that_is_not_a_git_work_tree_is_noted_and_rolled_back(self):
        self.write({"deploy/update/deployment-files.txt": "\n".join(self.LISTED) + "\n", **self.V1_1})
        self.applied()
        self.rollback()
        self.assertEqual(self.last_receipt()["status"], "rolled_back")
        self.assertIn("Note: this checkout's deployment files could not be checked against v1.0.0's (this checkout is "
                      "not a git work tree). The rollback starts v1.0.0's images with the compose files in this "
                      "checkout as they are.", self.out.getvalue())

    def test_a_missing_tag_is_noted_with_the_fetch_and_rolled_back(self):
        self.release_repo(tags=("v1.1.0",))
        self.applied()
        self.rollback()
        self.assertEqual(self.last_receipt()["status"], "rolled_back")
        self.assertIn("could not be checked against v1.0.0's (this checkout has no tag v1.0.0; `git fetch --tags` "
                      "fetches the release tags)", self.out.getvalue())

    def test_no_git_is_noted_and_rolled_back(self):
        self.release_repo()
        self.applied()
        with mock.patch.object(apply_module.subprocess, "run", side_effect=FileNotFoundError(2, "No such file", "git")):
            self.rollback()
        self.assertEqual(self.last_receipt()["status"], "rolled_back")
        self.assertIn("could not be checked against v1.0.0's (git is not installed)", self.out.getvalue())

    def test_an_undone_release_without_a_tag_is_brought_back_from_head(self):
        self.release_repo(tags=("v1.0.0",))
        self.applied()
        exc = self.assertRaisesExactly(Blocked, self.rollback)
        self.assertIn("Before updating again, run `git checkout HEAD -- docker-compose.yml deploy/clickhouse`.",
                      str(exc))

    def test_a_staged_update_restarted_by_hand_is_checked_too(self):
        self.release_repo()
        staged_by(self)
        self.inst.running["app"] = {"image": f"futureagi/standalone:{TARGET}",
                                    "image_id": id_of("futureagi/standalone", TARGET)}
        exc = self.assertRaisesExactly(Blocked, self.rollback)
        self.assertEqual(str(exc), self.REFUSAL.format(again="./bin/update rollback --yes"))

    def test_the_release_it_goes_back_to(self):
        previous = Updater._previous_release
        self.assertEqual(previous({}, {"FUTURE_AGI_VERSION": "v1.0.0"}), "v1.0.0")
        # A :latest install: the version label of the image that ran.
        images = {"backend": {"version": "v1.0.3"}, "worker": {"version": "v1.0.3"}}
        components = [{"variable": "FUTURE_AGI_VERSION", "services": ["backend", "worker"]}]
        self.assertEqual(previous({"previous_images": images, "components": components}, {}), "v1.0.3")
        self.assertEqual(previous({"previous_values": {"FUTURE_AGI_VERSION": "v1.0.1"}}, {}), "v1.0.1")
        # An update that left FUTURE_AGI_VERSION alone: the one version it came from.
        self.assertEqual(previous({"from_versions": ["v1.0.2"], "components": []}, {"FRONTEND_VERSION": "v1.0.2"}),
                         "v1.0.2")
        self.assertIsNone(previous({"from_versions": ["v1.0.1", "v1.0.2"]}, {"FUTURE_AGI_VERSION": "local"}))


class Round5StagedRollbackRestartsNothingTests(GitCheckoutCase):
    """G2 (T50): rolling back a staged update the stack never started puts the pins back and restarts nothing."""

    def test_it_restores_the_pins_verifies_and_runs_no_up(self):
        staged = staged_by(self)
        del self.log[:]
        self.out = io.StringIO()
        path = self.rollback()
        receipt = self.last_receipt()
        self.assertEqual((receipt["kind"], receipt["status"], receipt["rolls_back"], receipt["restarted"]),
                         ("rollback", "rolled_back", str(Path(staged).resolve()), False))
        self.assertEqual(receipt["target_image_ids"], {"app": id_of("futureagi/standalone", "v1.0.0")})
        self.assertEqual(self.env_bytes(), self.original)
        self.assertEqual([call[0] for call in self.compose_calls()], ["config"])
        self.assertNotIn(("wait_standalone",), self.log)
        self.assertEqual(self.inst.running["app"]["image_id"], id_of("futureagi/standalone", "v1.0.0"))
        text = self.out.getvalue()
        self.assertIn("The update to v1.1.0 was staged and never started: the rollback puts the version settings back "
                      "and verifies the previous images, and restarts nothing.", text)
        # Round 6 (U1): the deployment files could not be compared (this checkout has no list of them): a note.
        self.assertIn("Rolled back to v1.0.0 without restarting anything: the update to v1.1.0 was staged and never "
                      "started, so the stack never changed (it still runs v1.0.0), and .env selects "
                      "FUTURE_AGI_VERSION=v1.0.0 again. Note: this checkout's deployment files could not be checked "
                      "against v1.0.0's (cannot read deploy/update/deployment-files.txt", text)
        self.assertTrue(text.rstrip("\n").endswith(f"make sure they are v1.0.0's. Receipt: {path}"), text)
        self.assertNotIn("Restarting the stack", text)
        self.assertNotIn("deployment files differ", text)
        # The plan then offers the update afresh: the staged one is undone.
        exc = self.assertRaisesExactly(Blocked, self.rollback)
        self.assertIn("that update was already rolled back", str(exc))

    def test_differing_files_are_named_but_do_not_block(self):
        self.release_repo()
        staged_by(self)
        self.out = io.StringIO()
        path = self.rollback()
        self.assertEqual(self.last_receipt()["status"], "rolled_back")
        self.assertNotIn("up", [call[0] for call in self.compose_calls()])
        self.assertIn(
            "This checkout's deployment files differ from v1.0.0's: docker-compose.yml, deploy/clickhouse. Before you "
            "restart the stack (`docker compose up -d`) or run `./bin/install`, restore v1.0.0's versions with `git "
            "checkout v1.0.0 -- docker-compose.yml deploy/clickhouse`; before updating again, run `git checkout "
            f"v1.1.0 -- docker-compose.yml deploy/clickhouse`. Receipt: {path}", self.out.getvalue())

    def test_production_names_its_compose_command_and_no_install(self):
        self.build("production")
        self.release_repo()
        staged_by(self)
        self.out = io.StringIO()
        self.rollback()
        text = self.out.getvalue()
        # Round 6 (U10): the README's restart command, followed by the shell rule.
        self.assertIn("Before you restart the stack, restore v1.0.0's versions with `git checkout v1.0.0 -- "
                      f"docker-compose.yml deploy/clickhouse`, then restart it with `{PRODUCTION_COMPOSE} up -d "
                      "--no-build --wait`. Run it from a shell that does not export any key of deploy/.env.production "
                      "(Compose prefers your shell over the env file). Before updating again, run `git checkout v1.1.0 "
                      "-- docker-compose.yml deploy/clickhouse`.", text)
        self.assertNotIn("bin/install", text)

    def test_a_stage_that_fails_before_finishing_names_the_pending_update(self):
        staged = staged_by(self)
        self.prune("v1.0.0")  # pulled only when it is not on the host
        self.inst.failures["pull"] = REGISTRY_DOWN
        exc = self.assertRaisesExactly(FailedBeforeRollout, self.rollback)
        self.assertIn("The update to v1.1.0 staged earlier is still pending", str(exc))
        self.assertIn(f"to drop it, run `./bin/update rollback --receipt {staged} --yes`", str(exc))
        self.assertNotIn("up", [call[0] for call in self.compose_calls()])

    def test_a_service_restarted_by_hand_restarts_the_stack(self):
        self.build("distributed")
        staged_by(self)
        self.inst.running["frontend"] = {"image": f"futureagi/frontend:{TARGET}",
                                         "image_id": id_of("futureagi/frontend", TARGET)}
        self.rollback()
        receipt = self.last_receipt()
        self.assertEqual(receipt["status"], "rolled_back")
        self.assertNotIn("restarted", receipt)
        self.assertIn("up", [call[0] for call in self.compose_calls()])
        self.assertEqual(self.inst.running["frontend"]["image_id"], id_of("futureagi/frontend", "v1.0.0"))


class Round5StagedUpdateTheStackRunsTests(RealPlanCase):
    """T4, T5, T31, T61: a stopped rollback of a staged update the stack already runs is not 'still pending'."""

    def started_by_hand(self, mode="standalone"):
        self.build(mode)
        staged = self.run_apply(stage=True)
        for g in self.groups:
            for service in g.services:
                self.inst.running[service] = {"image": f"{g.repo}:{TARGET}", "image_id": id_of(g.repo, TARGET)}
        return staged

    def failing_rollback(self):
        self.prune("v1.0.0")  # pulled only when it is not on the host
        self.inst.failures["pull"] = REGISTRY_DOWN
        self.out = io.StringIO()
        return str(self.assertRaisesExactly(FailedBeforeRollout, self.rollback))

    def test_the_message_agrees_with_the_plan(self):
        staged = self.started_by_hand()
        plan = self.check()
        self.assertEqual(plan["decision"], UP_TO_DATE)
        self.assertTrue(any("already runs" in note for note in plan["notes"]), plan["notes"])
        text = self.failing_rollback()
        for claim in ("still pending", "To finish it", "still runs v1.0.0", "to drop it"):
            self.assertNotIn(claim, text)
        self.assertIn(
            "rollback stopped before restarting anything: `docker compose pull backend` failed with exit code 18: dial "
            "tcp: lookup registry-1.docker.io: no such host. The update to v1.1.0 staged earlier already runs: the "
            "stack was restarted on it outside bin/update. Nothing was changed: .env still selects "
            "FUTURE_AGI_VERSION=v1.1.0, which the stack runs. To try the rollback again, which restarts the running "
            f"stack on v1.0.0, run `./bin/update rollback --receipt {staged} --yes`.", text)

    def test_production_offers_no_jobs_and_no_finish(self):
        staged = self.started_by_hand("production")
        text = self.failing_rollback()
        self.assertNotIn("initialization jobs", text)
        self.assertNotIn("apply", text)
        self.assertIn("deploy/.env.production still selects FRONTEND_VERSION=v1.1.0, FUTURE_AGI_VERSION=v1.1.0, which "
                      "the stack runs", text)
        self.assertIn(f"run `./bin/update --production rollback --receipt {staged} --yes`.", text)

    def test_a_service_that_crashed_on_it_keeps_the_update_pending(self):
        self.started_by_hand("distributed")
        # backend was recreated on v1.1.0 but exited (its schema check failed): it runs nothing.
        crashed = self.inst.running.pop("backend")
        self.inst.extra_containers.append(dict(stray_container("backend", TARGET, image_id=crashed["image_id"]),
                                               status="exited", exit_code=1))
        text = self.failing_rollback()
        self.assertIn("The update to v1.1.0 staged earlier is still pending: .env selects it (FRONTEND_VERSION=v1.1.0, "
                      "FUTURE_AGI_VERSION=v1.1.0), and some of its containers were started on it outside bin/update. "
                      "To finish it, run `./bin/update apply", text)
        self.assertNotIn("still runs v1.0.0", text)


class Round5RollbackEnvEditedByHandTests(UpdaterCase):
    """T32: a stopped rollback of a finished update quotes the env file as it is, and what the stack runs."""

    def failing_rollback(self):
        self.prune("v1.0.0")  # pulled only when it is not on the host
        self.inst.failures["pull"] = InstallError("network down")
        return str(self.assertRaisesExactly(FailedBeforeRollout, self.rollback))

    def test_a_key_set_back_by_hand(self):
        applied = self.applied()
        self.real_write(self.env_path, {"FUTURE_AGI_VERSION": "v1.0.0"})
        self.inst.env = read_env(self.env_path)
        text = self.failing_rollback()
        self.assertIn("rollback stopped before restarting anything: network down. Nothing was changed: .env selects "
                      "FUTURE_AGI_VERSION=v1.0.0 now, while the stack runs v1.1.0. To try the rollback again, run "
                      f"`./bin/update rollback --receipt {Path(applied).resolve()} --yes`.", text)
        self.assertNotIn("still selects", text)
        self.assertNotIn("which the stack runs", text)

    def test_one_of_two_keys_set_back_by_hand(self):
        self.build("distributed")
        self.applied()
        self.real_write(self.env_path, {"FUTURE_AGI_VERSION": "v1.0.0"})
        self.inst.env = read_env(self.env_path)
        text = self.failing_rollback()
        self.assertIn("Nothing was changed: .env selects FRONTEND_VERSION=v1.1.0, FUTURE_AGI_VERSION=v1.0.0 now, while "
                      "the stack runs v1.1.0.", text)

    def test_the_env_file_as_the_update_left_it_keeps_the_wording(self):
        self.applied()
        text = self.failing_rollback()
        self.assertIn("Nothing was changed: .env still selects FUTURE_AGI_VERSION=v1.1.0, which the stack runs.", text)


class Round5RollbackStrayOnTheRestoredImageTests(UpdaterCase):
    """T33, T34: the rollback's stray refusal accepts the image it restores, and names what a command can remove."""

    def setUp(self):
        super().setUp()
        self.build("distributed")
        self.inst.profile_services["worker-default"] = "futureagi/future-agi:${FUTURE_AGI_VERSION:-latest}"

    def test_an_unlabelled_stray_on_the_restored_image_does_not_block(self):
        self.inst.metadata[id_of("futureagi/future-agi", "v1.0.0")]["version"] = ""  # an image without the OCI label
        applied = self.applied()
        self.inst.extra_containers.append(stray_container("worker-default", "v1.0.0"))
        self.rollback()
        receipt = self.last_receipt()
        self.assertEqual((receipt["status"], receipt["rolls_back"]), ("rolled_back", str(Path(applied).resolve())))

    def test_an_unlabelled_stray_on_another_image_still_blocks(self):
        self.inst.metadata[id_of("futureagi/future-agi", "v1.0.0")]["version"] = ""
        self.applied()
        other = "sha256:" + "5" * 64
        self.inst.metadata[other] = {"repo_digests": [], "version": ""}
        self.inst.local_images["futureagi/future-agi:custom"] = other
        self.inst.extra_containers.append(stray_container("worker-default", "v1.0.0", image_id=other))
        exc = self.assertRaisesExactly(Blocked, self.rollback)
        self.assertIn("worker-default (running, no version label)", str(exc))

    def test_with_an_incomplete_model_an_unknown_service_is_removed_by_container_id(self):
        self.inst.model_complete = False
        self.applied()
        stray = stray_container("property-catalog-supervisor", TARGET)
        self.inst.extra_containers.append(stray)
        exc = self.assertRaisesExactly(Blocked, self.rollback)
        self.assertEqual(str(exc), (
            "service property-catalog-supervisor has containers on Future AGI images but is not in the active Compose "
            "configuration, and Compose could not resolve every profile, so bin/update cannot tell whether "
            "property-catalog-supervisor belongs to a profile that is not enabled now or is defined by no compose "
            "file any more: property-catalog-supervisor (running, v1.1.0). The rollback to v1.0.0 does not replace "
            f"its containers, so they would keep running as they are next to it. Remove its containers with `docker "
            f"rm -f {stray['id'][:12]}`, then run `./bin/update rollback --yes` again. Nothing was changed"))
        self.assertNotIn("rm -sf", str(exc))
        self.inst.extra_containers.remove(stray)  # what the printed command does
        self.rollback()
        self.assertEqual(self.last_receipt()["status"], "rolled_back")

    def test_known_and_unknown_services_get_each_their_command(self):
        self.inst.model_complete = False
        self.applied()
        unknown = stray_container("property-catalog-supervisor", TARGET)
        self.inst.extra_containers.extend([stray_container("worker-default", TARGET), unknown])
        exc = self.assertRaisesExactly(Blocked, self.rollback)
        text = str(exc)
        self.assertIn("Enable the profile (COMPOSE_PROFILES in .env), or remove it with `docker compose rm -sf "
                      "worker-default`; remove the containers of property-catalog-supervisor with `docker rm -f "
                      f"{unknown['id'][:12]}`, then run", text)
        self.assertNotIn("rm -sf property-catalog-supervisor", text)
        self.assertIn(" (worker-default: a profile that is not enabled now; Compose could not resolve every profile, "
                      "so bin/update cannot tell whether property-catalog-supervisor belongs to one or is defined by "
                      "no compose file any more): ", text)


class Round5RecoveredRollbackTests(UpdaterCase):
    """T38, T40: rollback without --yes after recovering a run previews the rollback, and a stopped one must finish."""

    def test_a_rollback_killed_mid_rollout_must_be_finished(self):
        self.build("production")
        applied = Path(self.applied()).resolve()
        self.inst.failures["up"] = SystemExit(137)  # killed while replacing containers
        with self.assertRaises(SystemExit):
            self.rollback()
        del self.inst.failures["up"]
        self.out = io.StringIO()
        self.assertIsNone(self.rollback(yes=False))
        text = self.out.getvalue()
        self.assertIn("Recovered an interrupted rollback to v1.0.0: it stopped while containers were being replaced",
                      text)
        self.assertNotIn("as well", text)
        self.assertIn("Nothing else was changed, but the rollback of v1.1.0 is not finished: some containers may still "
                      "run v1.1.0 while others run v1.0.0, and bin/update refuses to update this install until it is "
                      f"finished. To finish it, run `./bin/update --production rollback --receipt {applied} --yes`.", text)
        preview = text.index("Rolling back v1.1.0 to FRONTEND_VERSION=v1.0.0, FUTURE_AGI_VERSION=v1.0.0 (Production "
                             "(Distributed), project fagi-test).")
        self.assertLess(preview, text.index("Database migrations the update applied are not reverted"))
        self.assertLess(text.index("Database migrations"), text.index("To finish it"))
        # Pasted, it finishes that rollback.
        self.out = io.StringIO()
        self.rollback(Path(backticked(text, "./bin/update --production rollback")[4]))
        receipt = self.last_receipt()
        self.assertEqual((receipt["status"], receipt["rolls_back"]), ("rolled_back", str(applied)))
        self.assertEqual(self.env_bytes(), self.original)

    def test_the_offer_after_a_recovered_update_shows_the_install_and_the_migration_warning(self):
        self.build("production")
        self.applied()
        self.craft_journal("rollout", target_version="v1.2.0",
                           previous_values={"FUTURE_AGI_VERSION": TARGET, "FRONTEND_VERSION": TARGET},
                           previous_images=self.snapshot_of(TARGET))
        self.out = io.StringIO()
        self.assertIsNone(self.rollback(yes=False))
        text = self.out.getvalue()
        offer = text.index("to roll it back instead")  # Round 6 (U20): after the way to finish it
        self.assertLess(text.index("(Production (Distributed), project fagi-test)."), offer)
        self.assertLess(text.index("Database migrations the update applied are not reverted"), offer)


class Round5RollbackOfALatestInstallTests(UpdaterCase):
    """T52, T60: the image that ran under :latest is tagged with the version restored, not pulled again."""

    def test_standalone_rolls_back_during_a_registry_outage(self):
        self.build(values={"FUTURE_AGI_VERSION": ""})
        self.applied()
        self.inst.failures["pull"] = REGISTRY_DOWN
        self.out = io.StringIO()
        self.rollback()
        receipt = self.last_receipt()
        old = id_of("futureagi/standalone", "v1.0.0")
        self.assertEqual((receipt["status"], receipt["target_image_ids"]), ("rolled_back", {"app": old}))
        self.assertIn(("docker", ("tag", old, "futureagi/standalone:v1.0.0")), self.log)
        self.assertNotIn("pull", [call[0] for call in self.compose_calls()])
        self.assertEqual(self.inst.running["app"]["image_id"], old)
        self.assertEqual(self.env_value("FUTURE_AGI_VERSION"), "v1.0.0")
        self.assertIn(f"Tagging {old[:19]}…, the image that ran before the update (on this host under another tag), "
                      "as futureagi/standalone:v1.0.0 instead of pulling it", self.out.getvalue())

    def test_distributed_tags_each_image_once(self):
        self.build("distributed", values={"FUTURE_AGI_VERSION": None, "FRONTEND_VERSION": ""})
        self.applied()
        self.inst.failures["pull"] = REGISTRY_DOWN
        self.rollback()
        self.assertEqual(self.last_receipt()["status"], "rolled_back")
        tags = [entry[1] for entry in self.log if entry[0] == "docker"]
        self.assertEqual(sorted(tags), [("tag", id_of("futureagi/frontend", "v1.0.0"), "futureagi/frontend:v1.0.0"),
                                        ("tag", id_of("futureagi/future-agi", "v1.0.0"), "futureagi/future-agi:v1.0.0")])

    def test_an_image_whose_digest_changed_is_pulled_and_verified(self):
        self.build(values={"FUTURE_AGI_VERSION": ""})
        self.applied()
        old = id_of("futureagi/standalone", "v1.0.0")
        self.inst.metadata[old]["repo_digests"] = [f"futureagi/standalone@sha256:{'d' * 64}"]
        self.inst.failures["pull"] = REGISTRY_DOWN
        self.assertRaisesExactly(FailedBeforeRollout, self.rollback)
        self.assertNotIn("docker", [entry[0] for entry in self.log])
        self.assertIn("pull", [call[0] for call in self.compose_calls()])

    def test_a_pinned_install_whose_tag_was_removed_is_pulled(self):
        self.applied()
        self.prune("v1.0.0")
        self.rollback()
        self.assertEqual(self.last_receipt()["status"], "rolled_back")
        self.assertNotIn("docker", [entry[0] for entry in self.log])
        self.assertIn(("pull", "app"), self.compose_calls())


class Round5FinishedRunJournalTests(UpdaterCase):
    """T59: a run stopped between its final receipt and removing its journal leaves nothing to recover."""

    def kill_once_at_clear_journal(self):
        real = self.state.clear_journal
        calls = []

        def clear():
            if not calls:
                calls.append(1)
                raise SystemExit(137)
            real()

        self.state.clear_journal = clear

    def test_a_rollback_killed_after_its_receipt_moves_on(self):
        self.applied()
        self.kill_once_at_clear_journal()
        with self.assertRaises(SystemExit):
            self.rollback()
        self.assertEqual([r["status"] for r in self.receipts()], ["succeeded", "rolled_back"])
        self.assertEqual(self.journal()["stage"], "rollout")
        self.out = io.StringIO()
        exc = self.assertRaisesExactly(Blocked, self.rollback)
        self.assertIn("that update was already rolled back", str(exc))
        self.assertIsNone(self.journal())
        self.assertEqual([r["status"] for r in self.receipts()], ["succeeded", "rolled_back"])
        self.assertIn("Removed the journal of a bin/update rollback to v1.0.0 that had finished (receipt ", self.out.getvalue())
        # The next update runs as usual.
        self.apply()
        self.assertEqual([r["status"] for r in self.receipts()], ["succeeded", "rolled_back", "succeeded"])

    def test_a_stage_killed_after_its_receipt_stays_staged(self):
        self.kill_once_at_clear_journal()
        with self.assertRaises(SystemExit):
            staged_by(self)
        staged = self.state.receipts()[-1]
        self.assertEqual(self.journal()["stage"], "pulled")
        self.apply(finishing_plan(self))
        receipts = self.receipts()
        self.assertEqual([r["status"] for r in receipts], ["staged", "succeeded"])
        self.assertEqual(receipts[-1]["resumes"], str(staged))
        self.assertEqual(self.env_value("FUTURE_AGI_VERSION"), TARGET)

    def test_a_journal_of_another_run_is_still_recovered(self):
        applied = Path(self.applied())
        record = json.loads(applied.read_text(encoding="utf-8"))
        # The same start, kind and target as the receipt, but another run's id: a run of its own.
        self.craft_journal(record["stage"], started_at=record["started_at"], run_id="another-run",
                           target_version=record["target_version"])
        recovered = self.updater().recover()
        self.assertEqual(recovered["status"], "interrupted_during_rollout")
        self.assertEqual(len(self.receipts()), 2)

    def test_a_journal_without_a_run_id_matches_by_its_start_kind_target_and_stage(self):
        applied = Path(self.applied())
        record = json.loads(applied.read_text(encoding="utf-8"))
        self.craft_journal(record["stage"], started_at=record["started_at"])
        self.assertIsNone(self.updater().recover())
        self.assertIsNone(self.journal())
        self.assertEqual(len(self.receipts()), 1)

    def test_an_install_wedged_by_a_second_recovered_receipt_can_finish(self):
        applied = self.craft_apply()
        # What an older bin/update left: the rollback's receipt, then the same rollback recovered from its journal.
        self.craft_rollback(applied, started_at="2026-10-19T01:00:00Z")
        self.craft_rollback(applied, status="interrupted_during_rollout", started_at="2026-10-19T01:00:00Z")
        path = self.rollback(Path(applied))
        receipt = self.last_receipt()
        self.assertEqual((receipt["status"], receipt["rolls_back"]), ("rolled_back", str(Path(applied).resolve())))
        self.assertEqual(Path(path), self.state.receipts()[-1])
        exc = self.assertRaisesExactly(Blocked, self.rollback, Path(applied))
        self.assertIn("that update was already rolled back", str(exc))


class Round5ApplyRefusesBeforeItsPlanTests(RealPlanCase):
    """G4 (T6, T11, T14): a run refused before it starts prints the plan as check does, never 'applying'."""

    def apply_with(self, **kwargs):
        manifest = self.manifest()
        self.out = io.StringIO()
        return self.updater().apply(
            lambda history: build_plan(manifest, self.verification, self.inst, self.settings, history=history),
            manifest, **kwargs)

    def test_no_backup_configured(self):
        self.build("distributed")
        exc = self.assertRaisesExactly(Blocked, self.apply_with)
        self.assertIn("no backup is configured", str(exc))
        self.assertEqual(decision_line(self.out.getvalue()), "  Decision      update available: run ./bin/update apply")
        self.assertEqual((self.receipts(), self.env_bytes(), self.hook.calls), ([], self.original, []))

    def test_production_stage_without_a_backup(self):
        self.REQUIRES_BOOTSTRAP = True
        self.build("production")
        exc = self.assertRaisesExactly(Blocked, self.apply_with, stage=True)
        self.assertIn("no backup is configured", str(exc))
        decision = decision_line(self.out.getvalue())
        self.assertNotIn("staging", decision)
        self.assertEqual(decision, "  Decision      update available: run ./bin/update --production apply --stage "
                                   "(the first of two steps, see below)")

    def test_a_backup_command_that_cannot_be_parsed(self):
        exc = self.assertRaisesExactly(Blocked, self.apply_with, backup_command="'/opt/backup")
        self.assertIn("cannot be parsed", str(exc))
        self.assertNotIn("applying", decision_line(self.out.getvalue()))
        self.assertEqual(self.hook.calls, [])

    def test_allow_no_backup_goes_ahead_and_says_so(self):
        self.apply_with(allow_no_backup=True)
        self.assertEqual(decision_line(self.out.getvalue()), "  Decision      update available: applying v1.1.0")
        self.assertEqual(self.last_receipt()["status"], "succeeded")

    def test_a_finishing_run_needs_no_backup(self):
        self.run_apply(stage=True)
        self.apply_with()
        self.assertEqual(decision_line(self.out.getvalue()),
                         "  Decision      update available: finishing the staged update to v1.1.0 (reuses its images already on this host, verified again, not pulled)")
        self.assertEqual(self.last_receipt()["status"], "succeeded")


class Round5ApplyingFlagTests(UpdaterCase):
    """G4 (T6, T9): plan_text gets applying=True only from a run that goes ahead."""

    def applying_flags(self, plan, **kwargs):
        calls = []
        real = apply_module.plan_text

        def recording(plan, **options):
            calls.append(options.get("applying"))
            return real(plan, **options)

        with mock.patch.object(apply_module, "plan_text", recording):
            try:
                self.apply(plan, **kwargs)
            except Blocked:
                pass
        return calls

    def test_each_kind_of_run(self):
        set_bad = "INSTALL_STABILITY_SECONDS=0\n".encode()
        for name, plan, kwargs, expected in (
                ("blocked", self.plan(decision=BLOCKED, reasons=["no"]), {}, [False]),
                ("up to date", self.plan(decision=UP_TO_DATE, env_changes={}), {}, [False]),
                ("dry run", self.plan(), {"dry_run": True}, [False]),
                ("no backup", self.plan(), {"backup_command": None}, [False]),
                ("readiness settings", self.plan(), {"env": set_bad}, [False]),
                ("goes ahead", self.plan(), {}, [True])):
            with self.subTest(name):
                self.build()
                if "env" in kwargs:
                    self.env_path.write_bytes(self.env_bytes() + kwargs.pop("env"))
                    self.inst.env = read_env(self.env_path)
                self.assertEqual(self.applying_flags(plan, **kwargs), expected)


class Round5RollbackFinishCommandTests(UpdaterCase):
    """G6 (T15, T62): the apply a stopped rollback names finishes that update with its own manifest."""

    def failing_rollback(self, updater=None):
        self.prune("v1.0.0")  # pulled only when it is not on the host
        self.inst.failures["pull"] = InstallError("network down")
        return str(self.assertRaisesExactly(FailedBeforeRollout, self.rollback, updater=updater))

    def test_a_local_manifest_is_named(self):
        plan = self.plan()
        plan["manifest_source"] = "/srv/fagi/manifest v1.2.0.json"
        staged_by(self, plan)
        text = self.failing_rollback()
        self.assertEqual(backticked(text, "./bin/update apply"),
                         ["./bin/update", "apply", "--manifest", "/srv/fagi/manifest v1.2.0.json"])

    def test_the_default_manifest_needs_no_option(self):
        plan = self.plan()
        plan["manifest_source"] = load_settings(self.root, None, environ={}).get("FUTURE_AGI_UPDATE_MANIFEST_URL")
        staged_by(self, plan)
        text = self.failing_rollback()
        self.assertEqual(backticked(text, "./bin/update apply"), ["./bin/update", "apply"])

    def test_another_latest_url_names_the_release_itself(self):
        plan = self.plan()
        plan["manifest_source"] = ("https://github.com/acme/future-agi/releases/latest/download/"
                                   "future-agi-update-manifest.json")
        staged_by(self, plan)
        text = self.failing_rollback()
        self.assertEqual(backticked(text, "./bin/update apply"), [
            "./bin/update", "apply", "--manifest",
            "https://github.com/acme/future-agi/releases/download/v1.1.0/future-agi-update-manifest.json"])

    def test_it_never_carries_this_runs_release_options(self):
        staged_by(self)
        updater = updater_with(self, options=["--channel", "beta", "--policy", "major"])
        text = self.failing_rollback(updater)
        self.assertEqual(backticked(text, "./bin/update apply"),
                         ["./bin/update", "apply", "--manifest", "/srv/manifest.json"])

    def test_an_update_stopped_mid_rollout_and_an_interrupted_rollback(self):
        self.ready.failure = NotReady("the app container keeps restarting")
        self.assertRaisesExactly(FailedDuringRollout, self.apply)
        self.ready.failure = None
        self.prune("v1.0.0")
        self.inst.failures["pull"] = KeyboardInterrupt()
        exc = self.assertRaisesExactly(InterruptedRun, self.rollback)
        self.assertEqual(backticked(str(exc), "./bin/update apply"),
                         ["./bin/update", "apply", "--manifest", "/srv/manifest.json"])

    def test_run_from_another_directory_it_starts_in_the_checkout(self):
        staged_by(self)
        elsewhere = self.root / "elsewhere"
        elsewhere.mkdir()
        text = self.failing_rollback(updater_with(self, caller_cwd=elsewhere))
        self.assertIn(f"`cd {self.root} && ./bin/update apply --manifest /srv/manifest.json`", text)

    def test_a_stage_whose_restore_failed_names_apply_stage(self):
        self.inst.failures["config"] = InstallError("`docker compose config` failed with exit code 15")
        self.env_write_failures[1] = EnvFileError("cannot write .env: No space left on device")
        exc = self.assertRaisesExactly(UpdateError, staged_by, self)
        self.assertIn("Run `./bin/update apply --stage` again: it restores them first", str(exc))



class Round5FinishedRunPreviewTests(UpdaterCase):
    """T59 for check and apply --dry-run: a finished run's leftover journal is planned as removed, not recovered."""

    def test_a_dry_run_plans_with_the_receipt_and_changes_nothing(self):
        staged = staged_by(self)
        record = json.loads(Path(staged).read_text(encoding="utf-8"))
        # The staging run's journal as it was when the run was stopped just after writing its receipt.
        StateDir.write_journal(self.state, dict(record, status="running"))
        staged_env = self.env_bytes()
        self.out = io.StringIO()
        self.apply(finishing_plan(self), dry_run=True)
        self.assertEqual([r.get("status") for r in self.histories_seen[-1]], ["staged"])
        self.assertEqual(self.inst.env_overlay, {})
        self.assertIn("a finished bin/update apply left its journal; the next apply or rollback removes it and "
                      "recovers nothing", self.out.getvalue())
        self.assertEqual((self.env_bytes(), len(self.receipts())), (staged_env, 1))
        self.assertIsNotNone(self.journal())

    def test_preview_recovery_with_the_history(self):
        applied = Path(self.applied())
        record = json.loads(applied.read_text(encoding="utf-8"))
        journal = dict(record, status="running")
        history = self.state.history(self.inst.identity())
        preview, notes = apply_module.preview_recovery(self.inst, journal, history)
        self.assertEqual((preview["status"], preview["_path"]), ("succeeded", str(applied)))
        self.assertNotIn(apply_module.JOURNAL_NOTE, notes)
        # Without the history (as before), the journal stands for an interrupted run.
        self.assertEqual(apply_module.preview_recovery(self.inst, journal)[0]["status"], "interrupted_during_rollout")


class Round5StagedUpdateRestartingTests(RealPlanCase):
    """T31 with the plan's rule (T58): a container that restarts or is unhealthy does not run the staged update."""

    def test_a_restarting_or_unhealthy_service_keeps_the_update_pending(self):
        for status, health in (("restarting", "starting"), ("running", "unhealthy")):
            with self.subTest(status=status, health=health):
                self.build("distributed")
                self.run_apply(stage=True)
                for g in self.groups:
                    for service in g.services:
                        self.inst.running[service] = {"image": f"{g.repo}:{TARGET}", "image_id": id_of(g.repo, TARGET)}
                started = self.inst.running.pop("worker")
                self.inst.extra_containers.append(dict(stray_container("worker", TARGET, image_id=started["image_id"]),
                                                       status=status, health=health))
                self.prune("v1.0.0")
                self.inst.failures["pull"] = REGISTRY_DOWN
                text = str(self.assertRaisesExactly(FailedBeforeRollout, self.rollback))
                self.assertIn("staged earlier is still pending", text)
                self.assertNotIn("already runs", text)



class Round5ApplyReusesImagesOnTheHostTests(RealPlanCase):
    """G4 (T14): the Decision line of a run that pulls nothing says it reuses the images already on this host."""

    def test_staging_again_reuses_the_staged_images(self):
        self.build("distributed")
        self.run_apply(stage=True)
        self.assertNotIn("reuses", decision_line(self.out.getvalue()))
        del self.log[:]
        self.run_apply(stage=True)
        decision = decision_line(self.out.getvalue())
        self.assertTrue(decision.startswith("  Decision      update available: staging v1.1.0 (reuses its images "
                                            "already on this host"), decision)
        self.assertNotIn("pulls", decision)
        self.assertNotIn("pull", [call[0] for call in self.compose_calls()])

    def test_an_image_on_the_host_with_another_digest_is_not_reused(self):
        self.build("distributed")
        self.inst.local_images["futureagi/frontend:v1.1.0"] = id_of("futureagi/frontend", TARGET)
        local_build = "sha256:" + "6" * 64
        self.inst.metadata[local_build] = {"repo_digests": [], "version": TARGET}
        self.inst.local_images["futureagi/future-agi:v1.1.0"] = local_build
        self.run_apply()
        self.assertEqual(decision_line(self.out.getvalue()), "  Decision      update available: applying v1.1.0")



# ------------------------------------------------------- integrator, round 5 --

import tarfile  # noqa: E402


def failing_git(real, subcommand, *, stderr=b""):
    """Updater._git that answers `git <subcommand>` with a failure (exit 128) and runs every other command."""

    def run(self, *args):
        if args[0] == subcommand:
            argv = ["git", "-C", str(self.installation.root), "--literal-pathspecs", *args]
            return subprocess.CompletedProcess(argv, 128, b"", stderr)
        return real(self, *args)

    return run


class IntegratorRound5DeploymentCheckEdgeTests(GitCheckoutCase):
    """G2's comparison of the checkout with the previous release's deployment files, at its edges: git failing at
    each step, a release it cannot name, files only one side has, untracked files in several directories."""

    def differences(self, previous="v1.0.0"):
        return self.updater()._deployment_differences(previous)

    def test_the_archive_is_extracted_without_links_or_paths_outside(self):
        data = io.BytesIO()
        with tarfile.open(fileobj=data, mode="w") as archive:
            for name, kind in (("deploy", tarfile.DIRTYPE), ("deploy/ok.yml", tarfile.REGTYPE),
                               ("../outside.yml", tarfile.REGTYPE), ("/absolute.yml", tarfile.REGTYPE),
                               ("deploy/link.yml", tarfile.SYMTYPE)):
                member = tarfile.TarInfo(name)
                member.type = kind
                content = b"x: 1\n" if kind == tarfile.REGTYPE else b""
                member.size = len(content)
                if kind == tarfile.SYMTYPE:
                    member.linkname = "/etc/passwd"
                archive.addfile(member, io.BytesIO(content))
        into = self.root / "extracted"
        into.mkdir()
        apply_module._extract_tar(data.getvalue(), into)
        found = sorted(str(path.relative_to(into)) for path in into.rglob("*"))
        self.assertEqual(found, ["deploy", "deploy/ok.yml"])
        self.assertFalse((self.root / "outside.yml").exists())

    def test_without_a_release_to_compare_with_it_says_so_and_goes_ahead(self):
        self.assertEqual(self.differences(None)["note"], "bin/update cannot tell which release ran before the update")
        self.assertIsNone(self.updater()._check_deployment_files({}, {}, "./bin/update rollback --yes"))
        self.assertIn("Note: this checkout's deployment files could not be checked against the previous release's "
                      "(bin/update cannot tell which release ran before the update). The rollback starts the previous "
                      "images with the compose files in this checkout as they are.", self.out.getvalue())

    def test_a_tag_git_cannot_look_up_is_no_tag(self):
        updater = self.updater()
        self.assertFalse(updater._has_tag(None))
        self.assertFalse(updater._has_tag("latest"))
        with mock.patch.object(apply_module.subprocess, "run", side_effect=subprocess.TimeoutExpired("git", 30)):
            self.assertFalse(updater._has_tag("v1.0.0"))

    def test_git_failing_to_list_or_archive_the_tag_is_noted(self):
        self.release_repo()
        real = Updater._git
        for subcommand, stderr, note in (
                ("ls-tree", b"fatal: not a tree object\n", "`git ls-tree` failed: fatal: not a tree object"),
                ("archive", b"", "`git archive` failed with exit code 128")):
            with self.subTest(subcommand=subcommand):
                with mock.patch.object(Updater, "_git", failing_git(real, subcommand, stderr=stderr)):
                    self.assertEqual(self.differences()["note"], note)

    def test_an_archive_that_cannot_be_read_is_noted(self):
        self.release_repo()
        with mock.patch.object(apply_module, "_extract_tar", side_effect=tarfile.ReadError("truncated header")):
            self.assertEqual(self.differences()["note"], "they could not be compared (truncated header)")

    def test_an_unreadable_compose_file_references_nothing(self):
        listed = self.LISTED + ["docker-compose.extra.yml", "deploy/telemetry.xml"]
        self.release_repo(listed=listed, target_files={
            "docker-compose.extra.yml": "services:\n  app:\n    volumes:\n      - ./deploy/telemetry.xml:/t.xml\n",
            "deploy/telemetry.xml": "<telemetry/>\n"})
        # The checkout's own compose file mounts the file v1.0.0 lacks: it counts.
        self.assertEqual(self.differences()["absent"], ["deploy/telemetry.xml"])
        real = Path.read_text

        def read_text(path, *args, **kwargs):
            if path.name == "docker-compose.extra.yml":
                raise PermissionError(13, "Permission denied", str(path))
            return real(path, *args, **kwargs)

        with mock.patch.object(Path, "read_text", read_text):
            # Only the compose files it can read name what the checkout needs.
            self.assertEqual(self.differences()["absent"], [])

    def test_files_the_previous_release_lacks_alone_are_moved_away(self):
        compose = self.V1_0["docker-compose.yml"] + "    volumes:\n      - ./deploy/telemetry.xml:/t.xml\n"
        self.V1_0 = dict(self.V1_0, **{"docker-compose.yml": compose})
        self.V1_1 = {"docker-compose.yml": compose, "deploy/telemetry.xml": "<telemetry/>\n"}
        self.release_repo(listed=self.LISTED + ["deploy/telemetry.xml"])
        self.applied()
        exc = self.assertRaisesExactly(Blocked, self.rollback)
        self.assertEqual(str(exc), (
            "The rollback starts v1.0.0's images, which need v1.0.0's deployment files; these differ in this "
            "checkout: deploy/telemetry.xml (not in v1.0.0). Move deploy/telemetry.xml out of the checkout, then run "
            "`./bin/update rollback --yes` again. Before updating again, run `git checkout v1.1.0 -- "
            "deploy/telemetry.xml`."))

    def test_untracked_files_are_found_in_every_directory_that_differs(self):
        self.LISTED = self.LISTED + ["deploy/nginx"]
        self.V1_0 = dict(self.V1_0, **{"deploy/nginx/site.conf": "server {}\n"})
        self.V1_1 = dict(self.V1_1, **{"deploy/nginx/site.conf": "server { listen 80; }\n"})
        self.release_repo()
        self.write({"deploy/nginx/extra.conf": "# mine\n", "deploy/clickhouse/.DS_Store": "litter\n",
                    "deploy/nginx/.DS_Store": "litter\n"})
        found = self.differences()
        self.assertEqual(found["differ"], ["docker-compose.yml", "deploy/clickhouse", "deploy/nginx"])
        # Litter the digest skips is never named; a file in the second directory is.
        self.assertEqual(found["untracked"], ["deploy/nginx/extra.conf"])
        with mock.patch.object(Updater, "_git", failing_git(Updater._git, "ls-files")):
            self.assertEqual(self.differences()["untracked"], [])

    def test_a_recovered_run_offers_no_rollback_while_the_files_differ(self):
        self.release_repo()
        applied = self.applied()
        # A later update to v1.2.0, killed before it restarted anything.
        self.craft_journal("pulled", target_version="v1.2.0", env_changes={"FUTURE_AGI_VERSION": "v1.2.0"},
                           previous_values={"FUTURE_AGI_VERSION": TARGET}, found_values={"FUTURE_AGI_VERSION": TARGET},
                           previous_images=self.snapshot_of(TARGET))
        self.out = io.StringIO()
        self.assertIsNone(self.rollback(yes=False))
        text = self.out.getvalue()
        self.assertIn("Recovered an interrupted apply to v1.2.0", text)
        self.assertIn("Nothing else was changed. The rollback starts v1.0.0's images, which need v1.0.0's deployment "
                      "files; these differ in this checkout: docker-compose.yml, deploy/clickhouse. Restore v1.0.0's "
                      "versions with `git checkout v1.0.0 -- docker-compose.yml deploy/clickhouse` (bin/update and "
                      f"your env file stay as they are), then run `./bin/update rollback --receipt {applied} --yes`. "
                      "Before updating again, run `git checkout v1.1.0 -- docker-compose.yml deploy/clickhouse`.", text)
        self.assertNotIn("as well", text)
        self.assertEqual(self.env_value("FUTURE_AGI_VERSION"), TARGET)


class IntegratorRound5ApplyHelperEdgeTests(UpdaterCase):
    """The defensive edges of round 5's apply helpers: records without the expected shape, one-off containers, an
    empty plan, a manifest named relative to the checkout."""

    def test_a_finish_command_names_the_manifest_only_when_it_can(self):
        updater = self.updater(FUTURE_AGI_UPDATE_MANIFEST_URL="releases/manifest.json")
        # A relative FUTURE_AGI_UPDATE_MANIFEST_URL is a file in the checkout: a plain apply reads it.
        self.assertEqual(updater._default_manifest(), str(self.root / "releases" / "manifest.json"))
        self.assertEqual(updater._finish_command({"manifest_source": str(self.root / "releases" / "manifest.json"),
                                                  "target_version": TARGET}), "./bin/update apply")
        # A receipt that names no release: its manifest cannot be named.
        self.assertEqual(updater._finish_command({"manifest_source": "/srv/other.json"}), "./bin/update apply")
        self.assertEqual(updater._finish_command({"manifest_source": "/srv/other.json", "target_version": TARGET}),
                         "./bin/update apply --manifest /srv/other.json")

    def test_a_plan_without_services_reuses_nothing_from_the_host(self):
        self.assertFalse(self.updater()._images_on_host({"components": []}))
        self.assertFalse(self.updater()._images_on_host({"components": [{"inactive": True, "services": ["app"]}]}))

    def test_records_of_another_shape_restore_no_image(self):
        images = {"app": {"image_id": "sha256:old"}}
        self.assertEqual(Updater._restored_images({"components": ["app", {"component": "standalone",
                                                                          "services": ["app", "worker"]}],
                                                   "previous_images": images}),
                         {"standalone": {"sha256:old"}})

    def test_only_a_staged_update_whose_services_never_restarted_never_started(self):
        never = Updater._never_started
        staged = {"status": "staged", "previous_images": {"app": {"image_id": "sha256:old"}},
                  "components": [{"component": "standalone", "services": ["app"]}]}
        oneoff = {"service": "app", "image_id": "sha256:new", "oneoff": True}
        running = {"service": "app", "image_id": "sha256:old"}
        self.assertTrue(never(staged, [running, oneoff]))
        self.assertFalse(never(dict(staged, status="succeeded"), [running]))
        self.assertFalse(never(dict(staged, components=["app"]), [running]))
        self.assertFalse(never(staged, [oneoff]))

    def test_the_stack_runs_a_release_only_when_every_service_runs_or_finished_on_it(self):
        updater = self.updater()
        image_id = "sha256:" + "e" * 64
        self.inst.local_images[f"futureagi/standalone:{TARGET}"] = image_id
        self.inst.metadata[image_id] = {"repo_digests": [], "version": TARGET}
        components = [{"component": "standalone", "services": ["app", "bootstrap"]}]
        app = {"service": "app", "image": f"futureagi/standalone:{TARGET}", "image_id": image_id,
               "status": "running", "health": "healthy"}
        bootstrap = {"service": "bootstrap", "image": f"futureagi/standalone:{TARGET}", "image_id": image_id,
                     "status": "exited", "exit_code": 0}
        oneoff = dict(app, oneoff=True, status="exited", exit_code=1)
        self.assertTrue(updater._runs_release([bootstrap, app, oneoff], components, TARGET))
        self.assertFalse(updater._runs_release([bootstrap, app], [], TARGET))
        self.assertFalse(updater._runs_release([bootstrap, app], components, "latest"))
        self.assertFalse(updater._runs_release([dict(bootstrap, exit_code=1), app], components, TARGET))


# ------------------------------------------------------------------ round 6 --

import shutil  # noqa: E402

# H3: the one sentence that follows a Compose command printed for a production install to run by hand.
SHELL_RULE = "Run it from a shell that does not export any key of {env} (Compose prefers your shell over the env file)."


def kill_once_at_clear_journal(case):
    """The case's next clear_journal is killed (SystemExit 137, as kill -9 ends a run); later ones work."""

    real = case.state.clear_journal
    calls = []

    def clear():
        if not calls:
            calls.append(1)
            raise SystemExit(137)
        real()

    case.state.clear_journal = clear


class Round6ComposePathsTests(unittest.TestCase):
    """H1 (U32): the paths a release's compose files bind-mount or read as env files."""

    def test_short_and_long_syntax_defaults_and_what_is_no_path(self):
        text = ("services:\n"
                "  a:\n"
                "    env_file: deploy/a.env  # one file\n"
                "    volumes: &mounts\n"
                "      - \"./x.conf:/x:ro\"\n"
                "      - data:/d\n"
                "      - ${CONF:-./deploy/conf}:/c\n"
                "      - ${NO_DEFAULT}:/n\n"
                "      - /var/run/docker.sock:/s\n"
                "      - ../outside:/o\n"
                "      - .:/app\n"
                "      - type: bind\n"
                "        source: ./long\n"
                "        target: /l\n"
                "      - type: volume\n"
                "        source: named\n"
                "        target: /n\n"
                "  b:\n"
                "    env_file: [ ./b.env, .env ]\n"
                "    volumes:\n"
                "    - ./same-indent:/s\n"
                "    environment:\n"
                "      - ./not-a-volume\n"
                "  c:\n"
                "    volumes: *mounts\n"
                "    env_file:\n"
                "      - path: ./c.env\n"
                "        required: false\n"
                "      - d.env\n"
                "      - ${ENV_FILE}\n"
                "volumes:\n"
                "  data:\n"
                "    driver: local\n")
        self.assertEqual(apply_module.compose_paths(text),
                         ["deploy/a.env", "x.conf", "deploy/conf", "long", "b.env", ".env", "same-indent", "c.env",
                          "d.env"])

    def test_this_checkouts_compose_files(self):
        found = apply_module.compose_paths((ROOT / "docker-compose.distributed.yml").read_text(encoding="utf-8"))
        # A source with a ${VAR:-default} resolves to its default, as Compose reads it with the variable unset.
        for path in ("agentcc-gateway/config.example.yaml", "deploy/clickhouse/users.d/zz-default-password.xml",
                     "futureagi/scripts/property_catalog_oss", ".env"):
            self.assertIn(path, found)
        self.assertFalse([path for path in found if path.startswith(("/", "..")) or "$" in path], found)


class Round6RollbackInventoryTests(GitCheckoutCase):
    """H1 (U32): the rollback compares the previous release's own deployment files too (its deployment-files.txt,
    and what its compose files bind-mount or read as env files); every command it prints works pasted."""

    APP = "services:\n  app:\n    image: futureagi/standalone:${FUTURE_AGI_VERSION:-latest}\n"
    MOUNTS = APP + "    env_file:\n      - path: .env\n        required: false\n    volumes:\n      - ./deploy/NAME:/etc/app:ro\n"

    def two_releases(self, old, new, removed=(), *, env_tracked=False):
        """Commit `old` (files) as v1.0.0, then remove `removed`, write `new` and commit that as v1.1.0.

        With env_tracked, v1.0.0 also tracks .env (this install's env file), which v1.1.0 no longer does.
        """

        (self.root / ".gitignore").write_text(".env\n.futureagi-updates/\ndeploy/*.env*\n", encoding="utf-8")
        self.write(old)
        git(self.root, "init", "-q")
        git(self.root, "add", "-A")
        if env_tracked:
            git(self.root, "add", "-f", ".env")
        git(self.root, "commit", "-q", "-m", "v1.0.0")
        git(self.root, "tag", "v1.0.0")
        if env_tracked:
            git(self.root, "rm", "-q", "--cached", ".env")
        if removed:
            git(self.root, "rm", "-r", "-q", "--", *removed)
        self.write(new)
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", "v1.1.0")
        git(self.root, "tag", "v1.1.0")

    def renamed(self):
        """v1.0.0 lists and bind-mounts deploy/old; v1.1.0 renames it to deploy/new."""

        self.two_releases(
            {"deploy/update/deployment-files.txt": "docker-compose.yml\ndeploy/old\n",
             "docker-compose.yml": self.MOUNTS.replace("NAME", "old"), "deploy/old/app.conf": "old = 1\n"},
            {"deploy/update/deployment-files.txt": "docker-compose.yml\ndeploy/new\n",
             "docker-compose.yml": self.MOUNTS.replace("NAME", "new"), "deploy/new/app.conf": "new = 1\n"},
            removed=["deploy/old"])

    def move_out(self, name):
        outside = tempfile.TemporaryDirectory()
        self.addCleanup(outside.cleanup)
        shutil.move(str(self.root / name), outside.name)

    def test_a_path_only_the_previous_release_has_is_restored_and_taken_out_again(self):
        self.renamed()
        self.applied()
        exc = self.assertRaisesExactly(Blocked, self.rollback)
        self.assertEqual(str(exc), (
            "The rollback starts v1.0.0's images, which need v1.0.0's deployment files; these differ in this "
            "checkout: docker-compose.yml, deploy/old (missing), deploy/new (not in v1.0.0). Restore v1.0.0's "
            "versions with `git checkout v1.0.0 -- docker-compose.yml deploy/old` and move deploy/new out of the "
            "checkout (bin/update and your env file stay as they are), then run `./bin/update rollback --yes` again. "
            "Before updating again, run `git checkout v1.1.0 -- docker-compose.yml deploy/new` and `git rm -r -f -q "
            "--ignore-unmatch -- deploy/old`."))
        self.assertEqual(self.compose_calls(), [])
        # Pasted as printed, then deploy/new moved away: the rollback runs with every file v1.0.0 needs.
        run_pasted(backticked(str(exc), "git checkout v1.0.0"), self.root)
        self.assertEqual(self.text_of("deploy/old/app.conf"), "old = 1\n")
        self.move_out("deploy/new")
        self.rollback()
        self.assertEqual(self.last_receipt()["status"], "rolled_back")
        # Before the next update, both commands work pasted and leave v1.1.0's checkout exactly.
        for start in ("git checkout v1.1.0", "git rm"):
            run_pasted(backticked(str(exc), start), self.root)
        self.assertEqual(git(self.root, "status", "--porcelain"), "")
        self.assertFalse((self.root / "deploy" / "old").exists())
        self.assertEqual(self.text_of("deploy/new/app.conf"), "new = 1\n")

    def test_restoring_only_the_compose_file_does_not_let_the_rollback_through(self):
        self.renamed()
        self.applied()
        # The old comparison's restore: v1.0.0's compose file, which mounts deploy/old, without deploy/old.
        git(self.root, "checkout", "-q", "v1.0.0", "--", "docker-compose.yml")
        self.assertEqual(self.updater()._deployment_differences("v1.0.0")["differ"], ["deploy/old"])
        exc = self.assertRaisesExactly(Blocked, self.rollback)
        self.assertIn("these differ in this checkout: deploy/old (missing). Restore v1.0.0's versions with `git "
                      "checkout v1.0.0 -- deploy/old`", str(exc))
        self.assertEqual(self.compose_calls(), [])

    def test_a_release_without_a_list_names_its_bind_mounts_and_env_files(self):
        compose = (self.APP + "    env_file:\n      - path: .env\n        required: false\n      - deploy/legacy.conf\n"
                   "    volumes:\n      - ${LEGACY_XML:-./deploy/legacy.xml}:/etc/legacy.xml:ro\n      - data:/data\n"
                   "volumes:\n  data:\n")
        self.two_releases(
            {"docker-compose.yml": compose, "deploy/legacy.xml": "<legacy/>\n", "deploy/legacy.conf": "LEGACY=1\n"},
            {"deploy/update/deployment-files.txt": "docker-compose.yml\n", "docker-compose.yml": self.APP},
            removed=["deploy/legacy.xml", "deploy/legacy.conf"], env_tracked=True)
        self.applied()
        exc = self.assertRaisesExactly(Blocked, self.rollback)
        self.assertEqual(str(exc), (
            "The rollback starts v1.0.0's images, which need v1.0.0's deployment files; these differ in this "
            "checkout: docker-compose.yml, deploy/legacy.conf (missing), deploy/legacy.xml (missing). Restore "
            "v1.0.0's versions with `git checkout v1.0.0 -- docker-compose.yml deploy/legacy.conf deploy/legacy.xml` "
            "(bin/update and your env file stay as they are), then run `./bin/update rollback --yes` again. Before "
            "updating again, run `git checkout v1.1.0 -- docker-compose.yml` and `git rm -r -f -q --ignore-unmatch -- "
            "deploy/legacy.conf deploy/legacy.xml`."))
        # This install's env file, which that compose file reads too (and that release even tracked), is never named.
        self.assertNotIn(".env", str(exc))
        self.assertNotIn(".env", self.updater()._deployment_differences("v1.0.0")["differ"])
        env = self.env_bytes()
        run_pasted(backticked(str(exc), "git checkout v1.0.0"), self.root)
        self.rollback()
        self.assertEqual(self.last_receipt()["status"], "rolled_back")
        self.assertEqual(self.env_bytes(), self.original)
        for start in ("git checkout v1.1.0", "git rm"):
            run_pasted(backticked(str(exc), start), self.root)
        self.assertEqual(git(self.root, "status", "--porcelain"), "")
        self.assertNotEqual(env, self.original)


class Round6GitRemediationTests(GitCheckoutCase):
    """H1 (U33): git commands are printed only for a checkout git works in."""

    def test_a_checkout_git_refuses_gets_a_note_and_no_git_command(self):
        self.release_repo()
        self.applied()
        stderr = (f"fatal: detected dubious ownership in repository at '{self.root}'\nTo add an exception for this "
                  f"directory, call:\n\n\tgit config --global --add safe.directory {self.root}\n").encode()
        with mock.patch.object(Updater, "_git", failing_git(Updater._git, "rev-parse", stderr=stderr)):
            self.rollback()
        text = self.out.getvalue()
        self.assertEqual(self.last_receipt()["status"], "rolled_back")
        self.assertIn("Note: this checkout's deployment files could not be checked against v1.0.0's (git refuses to "
                      "work in this checkout, which another user owns). The rollback starts v1.0.0's images with the "
                      "compose files in this checkout as they are.", text)
        self.assertNotIn("not a git work tree", text)
        self.assertNotIn("git config", text)
        self.assertNotIn("git checkout", text)


class Round6StagedRollbackNoteTests(GitCheckoutCase):
    """H1 (U1) and H3 (U10): a staged rollback that restarts nothing notes the deployment files it could not check, and
    a production install's restart command carries the shell rule."""

    def test_a_missing_tag_is_noted(self):
        self.release_repo(tags=("v1.1.0",))
        staged_by(self)
        self.out = io.StringIO()
        path = self.rollback()
        self.assertEqual(self.last_receipt()["restarted"], False)
        self.assertNotIn("up", [call[0] for call in self.compose_calls()])
        self.assertIn(
            ".env selects FUTURE_AGI_VERSION=v1.0.0 again. Note: this checkout's deployment files could not be checked "
            "against v1.0.0's (this checkout has no tag v1.0.0; `git fetch --tags` fetches the release tags). Before "
            "you restart the stack (`docker compose up -d`) or run `./bin/install`, make sure they are v1.0.0's. "
            f"Receipt: {path}", self.out.getvalue())

    def test_production_notes_them_with_its_restart_command_and_the_shell_rule(self):
        self.build("production")
        self.write({"deploy/update/deployment-files.txt": "\n".join(self.LISTED) + "\n", **self.V1_1})
        staged_by(self)
        self.out = io.StringIO()
        path = self.rollback()
        self.assertIn(
            "Note: this checkout's deployment files could not be checked against v1.0.0's (this checkout is not a git "
            "work tree). Before you restart the stack, make sure they are v1.0.0's, then restart it with "
            f"`{PRODUCTION_COMPOSE} up -d --no-build --wait`. "
            f"{SHELL_RULE.format(env='deploy/.env.production')} Receipt: {path}", self.out.getvalue())
        self.assertNotIn("bin/install", self.out.getvalue())

    def test_production_differing_files_from_another_directory(self):
        self.build("production")
        self.release_repo()
        staged_by(self)
        elsewhere = self.root / "elsewhere"
        elsewhere.mkdir()
        self.out = io.StringIO()
        self.rollback(updater=updater_with(self, caller_cwd=elsewhere))
        text = self.out.getvalue()
        hint = self.inst.compose_hint()
        self.assertTrue(hint.startswith(f"cd {self.root} && docker compose --env-file deploy/.env.production"), hint)
        self.assertIn(f"then restart it with `{hint} up -d --no-build --wait`. "
                      f"{SHELL_RULE.format(env='deploy/.env.production')} Before updating again, run `cd {self.root} "
                      "&& git checkout v1.1.0 -- docker-compose.yml deploy/clickhouse`.", text)
        # Every git command starts in the checkout and works pasted where bin/update was run.
        commands = [argv for argv in every_backticked(text) if "git" in argv]
        self.assertEqual([argv[:4] for argv in commands], [["cd", str(self.root), "&&", "git"]] * 2)
        run_pasted(commands[0], elsewhere)
        self.assertEqual(self.text_of("docker-compose.yml"), self.V1_0["docker-compose.yml"])
        run_pasted(commands[1], elsewhere)
        self.assertEqual(git(self.root, "status", "--porcelain"), "")  # the empty directory is no change to git


class Round6RollbackAfterTheRestoreTests(GitCheckoutCase):
    """H1 (U11): after the rollback's restore was applied, a rollback that stops before its restart says to bring the
    undone release's files back before finishing that update."""

    def failing_rollback(self):
        self.prune("v1.0.0")  # pulled only when it is not on the host
        self.inst.failures["pull"] = InstallError("network down")
        return str(self.assertRaisesExactly(FailedBeforeRollout, self.rollback))

    def test_an_update_stopped_mid_rollout(self):
        self.release_repo()
        self.ready.failure = NotReady("the app container keeps restarting")
        self.assertRaisesExactly(FailedDuringRollout, self.apply)
        self.ready.failure = None
        stopped = self.state.receipts()[-1]
        refusal = self.assertRaisesExactly(Blocked, self.rollback)
        run_pasted(backticked(str(refusal), "git checkout v1.0.0"), self.root)
        text = self.failing_rollback()
        self.assertIn(
            "To finish it, bring back v1.1.0's deployment files with `git checkout v1.1.0 -- docker-compose.yml "
            "deploy/clickhouse`, then run `./bin/update apply --manifest /srv/manifest.json`; to go back, run "
            f"`./bin/update rollback --receipt {stopped} --yes`.", text)
        run_pasted(backticked(text, "git checkout v1.1.0"), self.root)
        self.assertEqual(git(self.root, "status", "--porcelain"), "")
        self.assertEqual(self.updater()._deployment_differences(TARGET)["differ"], [])

    def test_production_brings_the_files_back_before_the_jobs(self):
        self.build("production")
        self.release_repo()
        staged = staged_by(self)
        # frontend was started on the staged release by hand: the rollback restarts the stack and checks the files.
        self.inst.running["frontend"] = {"image": f"futureagi/frontend:{TARGET}",
                                         "image_id": id_of("futureagi/frontend", TARGET)}
        refusal = self.assertRaisesExactly(Blocked, self.rollback)
        run_pasted(backticked(str(refusal), "git checkout v1.0.0"), self.root)
        text = self.failing_rollback()
        self.assertIn(
            "To finish it, bring back v1.1.0's deployment files with `git checkout v1.1.0 -- docker-compose.yml "
            "deploy/clickhouse`, then run the release's initialization jobs if they have not run yet, with "
            f"{jobs_text(self.inst)}, then run `./bin/update --production apply --manifest /srv/manifest.json`; to "
            f"drop it, run `./bin/update --production rollback --receipt {staged} --yes`.", text)
        self.assertLess(text.index("git checkout v1.1.0"), text.index("initialization jobs"))


class Round6ReceiptChannelTests(UpdaterCase):
    """H4 (U2, U3, U37): receipts record the manifest's channel, and the apply that finishes an update names it when
    this install follows another channel."""

    def failing_rollback(self, updater=None):
        self.prune("v1.0.0")  # pulled only when it is not on the host
        self.inst.failures["pull"] = InstallError("network down")
        return str(self.assertRaisesExactly(FailedBeforeRollout, self.rollback, updater=updater))

    def test_every_apply_records_its_channel(self):
        self.applied()
        self.assertEqual(self.last_receipt()["channel"], "stable")
        plan = self.plan(target="v1.2.0")
        plan["channel"] = "beta"
        self.apply(plan)
        self.assertEqual(self.last_receipt()["channel"], "beta")

    def test_a_staged_beta_release_is_finished_on_its_channel(self):
        plan = self.plan()
        plan.update(channel="beta", manifest_source="/srv/beta.json")
        staged_by(self, plan)
        self.assertEqual(self.last_receipt()["channel"], "beta")
        text = self.failing_rollback()
        self.assertEqual(backticked(text, "./bin/update apply"),
                         ["./bin/update", "apply", "--manifest", "/srv/beta.json", "--channel", "beta"])

    def test_an_install_that_follows_beta_finishes_a_stable_update_on_stable(self):
        staged_by(self)
        text = self.failing_rollback(self.updater(FUTURE_AGI_UPDATE_CHANNEL="beta"))
        self.assertEqual(backticked(text, "./bin/update apply"),
                         ["./bin/update", "apply", "--manifest", "/srv/manifest.json", "--channel", "stable"])


class Round6FinishedJournalRecoverOnlyTests(UpdaterCase):
    """U19: the recover-only rollback (no --yes) on a finished run's journal removes it, says so and stops there."""

    def test_a_finished_apply(self):
        kill_once_at_clear_journal(self)
        with self.assertRaises(SystemExit):
            self.apply()
        self.assertEqual(self.journal()["stage"], "rollout")
        self.out = io.StringIO()
        self.assertIsNone(self.rollback(yes=False))
        text = self.out.getvalue()
        self.assertEqual(text, f"Removed the journal of a bin/update apply to v1.1.0 that had finished (receipt "
                               f"{self.state.receipts()[-1]}): it was stopped only before it could remove it, so there "
                               "was nothing to recover.\nNothing else was changed.\n")
        self.assertIsNone(self.journal())
        self.assertEqual([r["status"] for r in self.receipts()], ["succeeded"])
        self.assertEqual(self.env_value("FUTURE_AGI_VERSION"), TARGET)

    def test_a_finished_rollback(self):
        self.applied()
        kill_once_at_clear_journal(self)
        with self.assertRaises(SystemExit):
            self.rollback()
        self.out = io.StringIO()
        self.assertIsNone(self.rollback(yes=False))
        self.assertIn("Removed the journal of a bin/update rollback to v1.0.0 that had finished", self.out.getvalue())
        self.assertTrue(self.out.getvalue().endswith("Nothing else was changed.\n"), self.out.getvalue())
        self.assertEqual([r["status"] for r in self.receipts()], ["succeeded", "rolled_back"])

    def test_the_command_another_installs_refusal_names(self):
        self.build("production")
        kill_once_at_clear_journal(self)
        with self.assertRaises(SystemExit):
            self.apply()
        # The operator of another production install in the same checkout runs apply and is told what to run.
        other_env = self.root / "deploy" / "prod eu.env"
        other_env.write_bytes(self.original)
        other = FakeInstallation(self.root, other_env, self.log, self.groups, mode="production", production=True)
        refusal = Updater(other, load_settings(self.root, None, environ={}), self.state, out=io.StringIO(),
                          readiness=self.ready, hook_runner=self.hook)
        exc = self.assertRaisesExactly(Blocked, refusal.apply, lambda history: self.plan(), {"version": TARGET},
                                       backup_command=BACKUP)
        argv = backticked(str(exc), "./bin/update --production rollback")
        self.assertEqual(argv, ["./bin/update", "--production", "rollback"])
        # Pasted: that install's rollback without --yes removes the journal and changes nothing else.
        del self.log[:]
        self.out = io.StringIO()
        self.assertIsNone(self.updater().rollback(None, yes=False))
        text = self.out.getvalue()
        self.assertIn("that had finished", text)
        for claim in ("Rolling back", "--yes", "Database migrations"):
            self.assertNotIn(claim, text)
        self.assertIsNone(self.journal())
        self.assertEqual([call for call in self.compose_calls() if call[0] in ("pull", "up")], [])


class Round6RecoveryKilledAfterItsReceiptTests(UpdaterCase):
    """U21: a journal left by a recovery that was killed after writing its receipt is reported as a recovered run."""

    def killed_recovery(self):
        applied = Path(self.applied()).resolve()
        self.inst.failures["up"] = SystemExit(137)  # the rollback is killed while replacing containers
        with self.assertRaises(SystemExit):
            self.rollback()
        del self.inst.failures["up"]
        journal = self.journal()
        kill_once_at_clear_journal(self)
        with self.assertRaises(SystemExit):
            self.updater().recover()
        self.assertEqual([r["status"] for r in self.receipts()], ["succeeded", "interrupted_during_rollout"])
        self.assertEqual(self.journal(), journal)
        self.out = io.StringIO()
        return applied

    def test_recovery_says_how_it_was_recorded(self):
        self.killed_recovery()
        self.assertIsNone(self.updater().recover())
        self.assertEqual(self.out.getvalue(), (
            "Removed the journal of an interrupted bin/update rollback to v1.0.0 that was already recorded as "
            f"interrupted_during_rollout (receipt {self.state.receipts()[-1]}): it stopped while containers were being "
            "replaced, so there was nothing more to recover.\n"))
        self.assertIsNone(self.journal())
        self.assertEqual(len(self.receipts()), 2)

    def test_the_recover_only_rollback_says_it_must_be_finished(self):
        applied = self.killed_recovery()
        self.assertIsNone(self.rollback(yes=False))
        text = self.out.getvalue()
        self.assertNotIn("had finished", text)
        self.assertIn("Nothing else was changed, but the rollback of v1.1.0 is not finished: some containers may still "
                      "run v1.1.0 while others run v1.0.0, and bin/update refuses to update this install until it is "
                      f"finished. To finish it, run `./bin/update rollback --receipt {applied} --yes`.", text)
        self.assertNotIn("rerun with --yes", text)
        self.assertEqual(len(self.receipts()), 2)

    def test_a_dry_run_notes_it_the_same_way(self):
        self.killed_recovery()
        journal = self.journal()
        history = self.state.history(self.inst.identity())
        record, notes = apply_module.preview_recovery(self.inst, journal, history)
        self.assertEqual(record["status"], "interrupted_during_rollout")
        self.assertEqual(notes, ["a bin/update rollback already recorded as interrupted_during_rollout left its "
                                 "journal; the next apply or rollback removes it and recovers nothing"])


class Round6RecoverOnlyStoppedRollbackTests(UpdaterCase):
    """U20 (T38): the recover-only rollback words a stopped rollback as one to finish from the receipts, not only from
    the run it recovered, and says so even when the rollback cannot go on now."""

    def test_a_retry_killed_before_its_restart_leaves_the_stopped_rollback_to_finish(self):
        applied = Path(self.applied()).resolve()
        self.inst.failures["up"] = InstallError("`docker compose up` failed with exit code 1")
        self.assertRaisesExactly(FailedDuringRollout, self.rollback)
        # The retry is killed while it pulls, before it restarts anything.
        self.prune("v1.0.0")
        self.inst.failures = {"pull": SystemExit(137)}
        with self.assertRaises(SystemExit):
            self.rollback()
        self.inst.failures = {}
        self.out = io.StringIO()
        self.assertIsNone(self.rollback(yes=False))
        text = self.out.getvalue()
        self.assertIn("Recovered an interrupted rollback to v1.0.0: it stopped before restarting anything", text)
        self.assertNotIn("as well", text)
        self.assertIn("Nothing else was changed, but the rollback of v1.1.0 is not finished: some containers may still "
                      "run v1.1.0 while others run v1.0.0, and bin/update refuses to update this install until it is "
                      f"finished. To finish it, run `./bin/update rollback --receipt {applied} --yes`.", text)

    def test_operator_edit_after_killed_rollout_is_not_silently_acknowledged(self):
        self.applied()
        self.inst.failures["up"] = SystemExit(137)
        with self.assertRaises(SystemExit):
            self.rollback()
        del self.inst.failures["up"]
        edited = self.env_bytes() + b"INSTALL_STABILITY_SECONDS=0\n"
        self.env_path.write_bytes(edited)
        self.inst.env = read_env(self.env_path)
        for _ in range(2):
            with self.assertRaisesRegex(StateError, 'conflict'):
                self.rollback(yes=False)
            self.assertEqual(self.env_bytes(), edited)
            self.assertIsNotNone(self.journal())
        self.assertEqual([r["status"] for r in self.receipts()], ["succeeded"])



class IntegratorRound6RollbackStrayShellRuleTests(UpdaterCase):
    """H3 on both sides: the rollback's refusal for a disabled profile's container names `<compose> rm -sf` for a
    production install with the same shared shell rule the plan's (plan._judge_strays) gives; other installs keep
    their wording."""

    RULE = ("Run it from a shell that does not export any key of deploy/.env.production (Compose prefers your shell "
            "over the env file).")

    def build_with_profiles(self, mode):
        self.build(mode)
        self.inst.profile_services.update({
            "worker-default": "futureagi/future-agi:${FUTURE_AGI_VERSION:-latest}",
            "simulation-runner": "futureagi/future-agi-simulation-runner:${SIMULATION_RUNNER_VERSION:-latest}"})

    def test_production_names_rm_sf_with_the_shell_rule_and_other_installs_keep_their_wording(self):
        self.build_with_profiles("production")
        self.applied()
        before, receipts = self.env_bytes(), self.state.receipts()
        self.inst.extra_containers.append(stray_container("worker-default", TARGET))
        exc = self.assertRaisesExactly(Blocked, self.rollback)
        hint = self.inst.compose_hint()
        self.assertTrue(str(exc).endswith(
            "so they would keep running as they are next to it. Enable the profile (COMPOSE_PROFILES in "
            f"deploy/.env.production), or remove it with `{hint} rm -sf worker-default`. {self.RULE} Then run "
            "`./bin/update --production rollback --yes` again. Nothing was changed"), str(exc))
        self.assertEqual((self.env_bytes(), self.state.receipts(), self.journal()), (before, receipts, None))
        with self.subTest("distributed"):
            self.build_with_profiles("distributed")
            self.applied()
            self.inst.extra_containers.append(stray_container("worker-default", TARGET))
            exc = self.assertRaisesExactly(Blocked, self.rollback)
            self.assertIn("or remove it with `docker compose rm -sf worker-default`, then run `./bin/update rollback "
                          "--yes` again. Nothing was changed", str(exc))
            self.assertNotIn("Run it from a shell", str(exc))

    def test_production_with_an_unlisted_service_too(self):
        self.build_with_profiles("production")
        self.applied()
        self.inst.extra_containers.append(stray_container("worker-default", TARGET))
        ghost = stray_container("old-worker", TARGET)
        self.inst.extra_containers.append(ghost)
        model = self.inst.service_model

        def incomplete(*args, **kwargs):
            # Compose could not resolve every profile: old-worker is listed by no compose file it could read.
            return dict(model(*args, **kwargs), complete=False)

        self.inst.service_model = incomplete
        exc = self.assertRaisesExactly(Blocked, self.rollback)
        hint = self.inst.compose_hint()
        self.assertIn(f"`{hint} rm -sf worker-default`. {self.RULE} Remove the containers of old-worker with "
                      f"`docker rm -f {ghost['id'][:12]}`, then run `./bin/update --production rollback --yes` again. "
                      "Nothing was changed", str(exc))



class IntegratorRound6RecoveredRolloutTests(UpdaterCase):
    """U20's optional part: rollback without --yes that recovered an apply killed while containers were being
    replaced says the stack may run a mix and names both ways out: that update's own apply finishes it (its
    manifest and, when this install follows another channel, its channel), the rollback undoes it."""

    def killed_mid_rollout(self, **fields):
        self.applied()
        self.craft_journal("rollout", target_version="v1.2.0", previous_values={"FUTURE_AGI_VERSION": TARGET},
                           previous_images=self.snapshot_of(TARGET), **fields)
        self.out = io.StringIO()
        self.assertIsNone(self.rollback(yes=False))
        return self.state.receipts()[-1], self.out.getvalue()

    def test_it_names_the_apply_of_that_update_and_its_rollback(self):
        recovered, text = self.killed_mid_rollout(manifest_source="/srv/v1.2.0.json", channel="beta")
        self.assertIn("Nothing else was changed, but the update to v1.2.0 is not finished: some containers may run "
                      "v1.2.0 while others still run the versions from before it. To finish it, run "
                      "`./bin/update apply --manifest /srv/v1.2.0.json --channel beta`; to roll it back instead (to "
                      f"FUTURE_AGI_VERSION=v1.1.0), run `./bin/update rollback --receipt {recovered} --yes`.", text)
        self.assertNotIn("as well", text)
        self.assertEqual(self.env_value("FUTURE_AGI_VERSION"), TARGET)
        self.assertEqual([call for call in self.compose_calls() if call[0] in ("pull", "up")], [])
        with self.subTest("a run stopped before its restart keeps the offer"):
            self.craft_journal("pulled", target_version="v1.3.0", previous_values={"FUTURE_AGI_VERSION": TARGET},
                               found_values={"FUTURE_AGI_VERSION": TARGET})
            self.out = io.StringIO()
            self.assertIsNone(self.rollback(yes=False))
            text = self.out.getvalue()
            self.assertIn("it stopped before restarting anything", text)
            self.assertIn("Nothing else was changed. To roll back", text)
            self.assertNotIn("is not finished", text)



class IntegratorRound6RestoreRemovesWhatThePreviousReleaseLacksTests(GitCheckoutCase):
    """H1's restore, when the undone release added a file inside a listed directory: `git checkout <previous> --
    <directory>` only lays the previous release's files over it, so that file stayed, the directory still differed
    and every retry was refused with the same command. The restore now also removes it (`git rm`), and the undone
    release's checkout brings it back before the next update."""

    ADDED = {"deploy/clickhouse/users.d/added.xml": "<clickhouse><profiles/></clickhouse>\n"}

    def test_the_printed_restore_lets_the_rollback_run_and_the_undo_leaves_a_clean_checkout(self):
        self.release_repo(target_files=self.ADDED)
        applied = self.applied()
        exc = self.assertRaisesExactly(Blocked, self.rollback)
        self.assertEqual(str(exc), (
            "The rollback starts v1.0.0's images, which need v1.0.0's deployment files; these differ in this "
            "checkout: docker-compose.yml, deploy/clickhouse. Restore v1.0.0's versions with `git checkout v1.0.0 -- "
            "docker-compose.yml deploy/clickhouse` and remove what v1.0.0 does not have with `git rm -q -f -- "
            "deploy/clickhouse/users.d/added.xml` (bin/update and your env file stay as they are), then run "
            "`./bin/update rollback --yes` again. Before updating again, run `git checkout v1.1.0 -- "
            "docker-compose.yml deploy/clickhouse`."))
        run_pasted(backticked(str(exc), "git checkout v1.0.0"), self.root)
        run_pasted(backticked(str(exc), "git rm"), self.root)
        self.assertFalse((self.root / "deploy/clickhouse/users.d/added.xml").exists())
        self.rollback()
        self.assertEqual((self.last_receipt()["status"], self.last_receipt()["rolls_back"]),
                         ("rolled_back", str(Path(applied).resolve())))
        run_pasted(backticked(str(exc), "git checkout v1.1.0"), self.root)
        self.assertEqual(git(self.root, "status", "--porcelain"), "")
        self.assertEqual(self.text_of("deploy/clickhouse/users.d/added.xml"), self.ADDED[
            "deploy/clickhouse/users.d/added.xml"])

    def test_a_staged_rollback_notes_the_same_restore(self):
        self.release_repo(target_files=self.ADDED)
        staged_by(self)
        self.out = io.StringIO()
        self.rollback()
        text = self.out.getvalue()
        self.assertIn("This checkout's deployment files differ from v1.0.0's: docker-compose.yml, deploy/clickhouse. "
                      "Before you restart the stack (`docker compose up -d`) or run `./bin/install`, restore v1.0.0's "
                      "versions with `git checkout v1.0.0 -- docker-compose.yml deploy/clickhouse` and remove what "
                      "v1.0.0 does not have with `git rm -q -f -- deploy/clickhouse/users.d/added.xml`", text)

    def test_a_file_git_no_longer_tracks_is_only_named_as_untracked(self):
        self.release_repo(target_files=self.ADDED)
        self.applied()
        git(self.root, "rm", "-q", "--cached", "--", "deploy/clickhouse/users.d/added.xml")
        exc = self.assertRaisesExactly(Blocked, self.rollback)
        self.assertNotIn("git rm", str(exc))
        self.assertIn("deploy/clickhouse/users.d/added.xml is not tracked by git, so `git checkout` leaves it in "
                      "place: move it out of the checkout as well.", str(exc))

    def test_a_file_the_directory_hash_skips_or_one_already_gone_is_not_named(self):
        self.release_repo(target_files=dict(self.ADDED, **{"deploy/clickhouse/users.d/.notes.swp": "x\n",
                                                          "deploy/clickhouse/users.d/gone.xml": "<g/>\n"}))
        self.assertIn("deploy/clickhouse/users.d/.notes.swp", git(self.root, "ls-files"))
        (self.root / "deploy/clickhouse/users.d/gone.xml").unlink()
        self.applied()
        exc = self.assertRaisesExactly(Blocked, self.rollback)
        self.assertIn("with `git rm -q -f -- deploy/clickhouse/users.d/added.xml` (", str(exc))



class IntegratorRound6DeploymentComparisonEdgeTests(GitCheckoutCase):
    """H1's comparison at its edges: what git cannot list names nothing extra, a previous release whose own
    deployment-files.txt cannot be read is still compared by its compose files, and a staged rollback whose
    checkout already holds the previous release's files adds no note."""

    def test_nothing_extra_is_named_when_git_cannot_list_the_release(self):
        self.release_repo(target_files=GitCheckoutCase.V1_1)
        updater = self.updater()
        self.assertEqual(updater._extra_tracked("refs/tags/v9.9.9", ["deploy/clickhouse"]), [])
        self.assertEqual(updater._extra_tracked("refs/tags/v1.0.0", []), [])

    def test_an_unreadable_list_at_the_previous_release_still_compares_its_compose_files(self):
        self.release_repo()
        git(self.root, "tag", "-d", "v1.0.0")
        # v1.0.0 again, with a deployment-files.txt that names nothing (the checkout's own list still does).
        git(self.root, "checkout", "-q", "HEAD~1", "--", "docker-compose.yml")
        (self.root / "deploy/update/deployment-files.txt").write_text("# nothing listed\n", encoding="utf-8")
        git(self.root, "commit", "-q", "-am", "v1.0.0 with an empty list")
        git(self.root, "tag", "v1.0.0")
        git(self.root, "checkout", "-q", "v1.1.0", "--", ".")
        check = self.updater()._deployment_differences("v1.0.0")
        self.assertEqual(check["note"], "")
        self.assertIn("docker-compose.yml", check["differ"])

    def test_a_staged_rollback_whose_files_are_the_previous_releases_says_nothing_more(self):
        self.release_repo()
        git(self.root, "checkout", "-q", "v1.0.0", "--", ".")
        path = staged_by(self)
        self.out = io.StringIO()
        receipt = self.rollback(Path(path))
        text = self.out.getvalue()
        self.assertIn(f"again. Receipt: {receipt}", text)
        self.assertNotIn("deployment files", text)


class IntegratorRound6DeploymentComparisonFailureTests(GitCheckoutCase):
    """A rollback's deployment-file check gives safe, actionable output at each git boundary."""

    def differences(self, previous="v1.0.0"):
        return self.updater()._deployment_differences(previous)

    def _failed_nth_git(self, subcommand, number):
        real = Updater._git
        calls = []

        def run(updater, *args):
            if args[0] == subcommand:
                calls.append(args)
                if len(calls) == number:
                    argv = ["git", "-C", str(updater.installation.root), "--literal-pathspecs", *args]
                    return subprocess.CompletedProcess(argv, 128, b"", b"fatal: test failure\n")
            return real(updater, *args)

        return run, calls

    def test_invalid_previous_release_never_suggests_checkout_commands(self):
        updater = self.updater()
        with mock.patch.object(updater, "_deployment_differences", side_effect=AssertionError("must not compare")):
            for version in (None, "", "latest", "v1.0", 100):
                with self.subTest(version=version):
                    self.assertEqual(updater._files_back_step(version), "")

    def test_empty_path_list_is_a_no_op(self):
        updater = self.updater()
        with mock.patch.object(updater, "_git", side_effect=AssertionError("must not run git")):
            self.assertEqual(updater._paths_at("HEAD", []), ([], None))

    def test_release_without_deployment_metadata_compares_no_paths(self):
        self.write({"ordinary.txt": "previous release\n"})
        git(self.root, "init", "-q")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", "previous release")
        git(self.root, "tag", "v1.0.0")
        self.write({"deploy/update/deployment-files.txt": "docker-compose.yml\n",
                    "docker-compose.yml": "services:\n  app:\n    image: futureagi/standalone:latest\n"})
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", "current release")

        check = self.differences()
        self.assertEqual(check["note"], "")
        self.assertEqual(check["differ"], [])
        self.assertEqual(check["absent"], [])

    def test_checkout_outside_this_worktree_is_not_compared(self):
        self.release_repo()
        real = Updater._git

        def different_top(updater, *args):
            if args[:2] == ("rev-parse", "--show-toplevel"):
                argv = ["git", "-C", str(updater.installation.root), "--literal-pathspecs", *args]
                return subprocess.CompletedProcess(argv, 0, b"/another/checkout\n", b"")
            return real(updater, *args)

        with mock.patch.object(Updater, "_git", different_top):
            check = self.differences()
        self.assertEqual(check["note"], "this checkout is not a git work tree")
        self.assertEqual(check["differ"], [])

    def test_failure_listing_comparison_paths_is_reported(self):
        self.release_repo()
        fail, calls = self._failed_nth_git("ls-tree", 2)
        with mock.patch.object(Updater, "_git", fail):
            check = self.differences()
        self.assertEqual(len(calls), 2)
        self.assertEqual(check["note"], "`git ls-tree` failed: fatal: test failure")
        self.assertEqual(check["differ"], [])

    def test_failure_archiving_comparison_paths_is_reported(self):
        self.release_repo()
        fail, calls = self._failed_nth_git("archive", 2)
        with mock.patch.object(Updater, "_git", fail):
            check = self.differences()
        self.assertEqual(len(calls), 2)
        self.assertEqual(check["note"], "`git archive` failed: fatal: test failure")
        self.assertEqual(check["differ"], [])

    def test_restore_command_keeps_paths_when_git_listing_raises_or_fails(self):
        updater = self.updater()
        failed = subprocess.CompletedProcess(["git", "ls-tree"], 128, b"", b"fatal: unavailable\n")
        failures = (("raises", subprocess.TimeoutExpired("git", 30)), ("returns", ([], failed)))
        for kind, failure in failures:
            with self.subTest(failure=kind):
                patch = (mock.patch.object(updater, "_paths_at", side_effect=failure) if kind == "raises" else
                         mock.patch.object(updater, "_paths_at", return_value=failure))
                with patch:
                    _, _, undo = updater._restore_steps(
                        {"previous": "v1.0.0", "differ": ["docker-compose.yml"], "absent": []}, None)
                self.assertEqual(undo, "`git checkout HEAD -- docker-compose.yml`")

    def test_an_unreadable_earlier_compose_does_not_hide_a_later_bind_mount(self):
        listed = self.LISTED + ["docker-compose.extra.yml", "deploy/telemetry.xml"]
        self.release_repo(listed=listed, target_files={
            "docker-compose.extra.yml": ("services:\n  app:\n    volumes:\n"
                                         "      - ./deploy/telemetry.xml:/etc/telemetry.xml:ro\n"),
            "deploy/telemetry.xml": "<telemetry/>\n"})
        real = Path.read_text

        def read_text(path, *args, **kwargs):
            if path == self.root / "docker-compose.yml":
                raise PermissionError(13, "Permission denied", str(path))
            return real(path, *args, **kwargs)

        with mock.patch.object(Path, "read_text", read_text):
            check = self.differences()
        self.assertEqual(check["absent"], ["deploy/telemetry.xml"])


class ApplyHelperMutationTests(unittest.TestCase):
    def test_ignored_first_signal_does_not_skip_a_later_signal(self):
        sigterm, sighup = signal.SIGTERM, signal.SIGHUP
        previous = {sigterm: signal.SIG_IGN, sighup: signal.SIG_DFL}
        registered = []

        def set_signal(sig, handler):
            registered.append((sig, handler))
            return previous[sig]

        with mock.patch.object(signal, "getsignal", side_effect=lambda sig: previous[sig]), \
                mock.patch.object(signal, "signal", side_effect=set_signal):
            with signals_as_interrupt():
                pass
        installed = [sig for sig, handler in registered if handler not in (signal.SIG_IGN, signal.SIG_DFL)]
        self.assertEqual(installed, [sighup])









    def test_gpu_release_tags_and_release_names_are_normalized(self):
        self.assertTrue(apply_module._is_release_tag("v1.2.3-gpu"))
        self.assertFalse(apply_module._is_release_tag("v1.2.3-gpu-extra"))
        self.assertEqual(apply_module._release_of("v1.2.3-gpu"), "v1.2.3")
        self.assertEqual(apply_module._release_of("v1.2.3"), "v1.2.3")
        # Only a -gpu suffix names the release's CUDA variant: any other four characters stay part of the tag.
        for tag in ("v1.2.3-rc1", "v1.2.3-cpu", "latest-gpu", "local"):
            with self.subTest(tag=tag):
                self.assertEqual(apply_module._release_of(tag), tag)

    def test_digest_set_keeps_a_valid_digest_after_an_invalid_entry(self):
        digest = "sha256:" + "a" * 64
        reference = f"futureagi/standalone@{digest}"
        malformed = "futureagi/standalone@sha256:abc"  # parse_image_ref refuses it
        with self.assertRaises(Exception):
            parse_image_ref(malformed)
        # An entry that does not parse, then one without a digest, then the digest itself.
        self.assertEqual(apply_module._digest_set([malformed, "futureagi/standalone:v1.0.0", reference]),
                         {(parse_image_ref(reference).name, digest)})

    def test_compose_paths_skip_comments_and_read_anchored_flow_lists(self):
        text = ("services:\n  first:\n    volumes:\n\n"
                "      - ./deploy/first:/first:ro\n"
                "  second:\n    volumes: &shared [./deploy/second:/second, ./deploy/third:/third]\n")
        self.assertEqual(apply_module.compose_paths(text),
                         ["deploy/first", "deploy/second", "deploy/third"])

    def test_tar_extraction_skips_unsafe_entries_and_creates_nested_parents(self):
        data = io.BytesIO()
        with tarfile.open(fileobj=data, mode="w") as archive:
            for name, contents in (("../outside.yml", b"unsafe"),
                                   ("deploy/nested/settings.yml", b"safe: true\n")):
                member = tarfile.TarInfo(name)
                member.size = len(contents)
                archive.addfile(member, io.BytesIO(contents))
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        into = root / "into"
        into.mkdir()
        apply_module._extract_tar(data.getvalue(), into)
        self.assertEqual((into / "deploy/nested/settings.yml").read_bytes(), b"safe: true\n")
        self.assertFalse((root / "outside.yml").exists())

    def test_tar_extraction_creates_every_directory_entry(self):
        # A directory entry may come after a file below it, name a directory whose parent has no entry of its own,
        # or name an empty directory: each is created, and none stops the extraction.
        data = io.BytesIO()
        with tarfile.open(fileobj=data, mode="w") as archive:
            member = tarfile.TarInfo("deploy/settings.yml")
            member.size = 3
            archive.addfile(member, io.BytesIO(b"a\n\n"))
            for name in ("deploy", "deploy/conf/empty", "deploy/conf/empty"):
                directory = tarfile.TarInfo(name)
                directory.type = tarfile.DIRTYPE
                archive.addfile(directory)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        into = Path(temporary.name)
        apply_module._extract_tar(data.getvalue(), into)
        self.assertEqual((into / "deploy/settings.yml").read_bytes(), b"a\n\n")
        self.assertTrue((into / "deploy/conf/empty").is_dir())

    def test_compose_paths_drop_trailing_comments_and_mapping_keys(self):
        text = ("services:\n  app:\n    env_file:\n      - ./deploy/app.env # the app's settings\n"
                "      - required: false\n        path: ./deploy/optional.env # optional\n"
                "    volumes:\n      - ./deploy/conf:/conf:ro # read-only\n"
                "      - type: bind\n        source: ./deploy/data # the data directory\n        target: /data\n")
        self.assertEqual(apply_module.compose_paths(text),
                         ["deploy/app.env", "deploy/optional.env", "deploy/conf", "deploy/data"])

    def test_staged_update_with_mixed_old_and_new_containers_was_started(self):
        target = {"status": "staged", "previous_images": {"app": {"image_id": "sha256:old"}},
                  "components": [{"component": "standalone", "services": ["app"]}]}
        containers = [{"service": "app", "image_id": "sha256:old"},
                      {"service": "app", "image_id": "sha256:new"}]
        self.assertFalse(Updater._never_started(target, containers))


class RollbackMutationSafetyTests(UpdaterCase):
    def test_rollback_without_yes_defaults_to_preview_only(self):
        self.applied()
        original, receipts = self.env_bytes(), self.state.receipts()
        calls_before = list(self.compose_calls())
        exc = self.assertRaisesExactly(Blocked, self.updater().rollback, None)
        self.assertEqual(str(exc), "rollback not started: rerun with --yes to confirm")
        self.assertEqual((self.env_bytes(), self.state.receipts()), (original, receipts))
        self.assertNotIn("up", [call[0] for call in self.compose_calls()[len(calls_before):]])

    def test_rollback_reuses_the_previous_image_id_when_no_digest_was_recorded(self):
        applied = Path(self.applied()).resolve()
        record = json.loads(applied.read_text(encoding="utf-8"))
        old_id = record["previous_images"]["app"]["image_id"]
        record["previous_images"]["app"]["repo_digests"] = []
        applied.write_text(json.dumps(record), encoding="utf-8")
        calls_before = list(self.compose_calls())
        self.inst.failures["pull"] = REGISTRY_DOWN

        self.rollback()

        new_calls = self.compose_calls()[len(calls_before):]
        self.assertNotIn("pull", [call[0] for call in new_calls])
        self.assertEqual(self.last_receipt()["status"], "rolled_back")
        self.assertEqual(self.inst.running["app"]["image_id"], old_id)

    def test_rollback_adoption_uses_the_known_id_when_inspection_has_no_id(self):
        self.build(values={"FUTURE_AGI_VERSION": ""})
        applied = Path(self.applied()).resolve()
        old_id = json.loads(applied.read_text(encoding="utf-8"))["previous_images"]["app"]["image_id"]
        real_image = self.inst.image

        def image_without_id(reference):
            info = real_image(reference)
            if reference == old_id and info is not None:
                return dict(info, id=None)
            return info

        with mock.patch.object(self.inst, "image", image_without_id):
            self.rollback()

        self.assertIn(("docker", ("tag", old_id, "futureagi/standalone:v1.0.0")), self.log)
        self.assertNotIn("Already on this host with the expected digest", self.out.getvalue())
        self.assertEqual(self.last_receipt()["status"], "rolled_back")

    # _runs_release: whether a staged update was already started outside bin/update (the stack runs its release).
    COMPONENTS = [{"component": "standalone", "services": ["app", "init"]}]

    def on_host(self, version):
        """The release's standalone image on this host (its version label readable); returns its id."""

        image_id = id_of("futureagi/standalone", version)
        self.inst.local_images[f"futureagi/standalone:{version}"] = image_id
        return image_id

    def box(self, service, version, status="running", exit_code=0, image_id=None):
        return {"service": service, "image": f"futureagi/standalone:{version}", "status": status,
                "exit_code": exit_code, "health": "healthy",
                "image_id": id_of("futureagi/standalone", version) if image_id is None else image_id}

    def runs(self, containers, components=None, version=TARGET):
        return self.updater()._runs_release(containers, components or self.COMPONENTS, version)

    def test_release_already_runs_only_if_all_containers_of_each_service_are_on_it(self):
        self.on_host(TARGET)
        job = self.box("init", TARGET, status="exited")
        self.assertTrue(self.runs([self.box("app", TARGET), self.box("app", TARGET), job]))
        self.assertFalse(self.runs([self.box("app", TARGET), self.box("app", "v1.0.0"), job]))

    def test_a_job_counts_only_if_every_container_of_it_exited_cleanly_on_the_release(self):
        self.on_host(TARGET)
        app = self.box("app", TARGET)
        self.assertTrue(self.runs([app, self.box("init", TARGET, status="exited")]))
        self.assertFalse(self.runs([app, self.box("init", TARGET, status="exited"),
                                    self.box("init", TARGET, status="exited", exit_code=1)]))

    def test_only_finished_jobs_do_not_make_a_running_release(self):
        self.on_host(TARGET)
        self.assertFalse(self.runs([self.box("app", TARGET, status="exited"),
                                    self.box("init", TARGET, status="exited")]))

    def test_an_image_without_a_version_label_is_not_the_release_whatever_its_tag(self):
        unlabelled = "sha256:" + "4" * 64
        self.inst.metadata[unlabelled] = {"repo_digests": [], "version": ""}
        self.inst.local_images[f"futureagi/standalone:{TARGET}"] = unlabelled
        self.assertFalse(self.runs([self.box("app", TARGET, image_id=unlabelled),
                                    self.box("init", TARGET, status="exited", image_id=unlabelled)]))

    def test_a_version_that_is_not_a_release_never_runs(self):
        nightly = "sha256:" + "3" * 64
        self.inst.metadata[nightly] = {"repo_digests": [], "version": "nightly"}
        self.inst.local_images["futureagi/standalone:nightly"] = nightly
        self.assertFalse(self.runs([self.box("app", "nightly", image_id=nightly),
                                    self.box("init", "nightly", status="exited", image_id=nightly)],
                                   version="nightly"))

    def test_each_image_is_inspected_once_and_a_container_without_one_never(self):
        image_id = self.on_host(TARGET)
        del self.log[:]
        self.assertFalse(self.runs([self.box("app", TARGET), self.box("app", TARGET),
                                    self.box("init", TARGET, status="exited", image_id="")]))
        self.assertEqual([entry for entry in self.log if entry[0] == "image"], [("image", image_id)])

    def test_previous_release_selection_ignores_malformed_record_shapes(self):
        self.assertIsNone(Updater._previous_release(
            {"previous_values": {"FUTURE_AGI_VERSION": 123}, "components": []}, {}))
        self.assertIsNone(Updater._previous_release({"components": ["not a component"]}, {}))

    def test_previous_release_of_an_install_that_followed_latest_is_the_version_that_ran(self):
        # FUTURE_AGI_VERSION was empty (or another value that is no release): the image that ran says which.
        target = {"previous_values": {"FUTURE_AGI_VERSION": ""},
                  "components": [{"component": "standalone", "variable": "FUTURE_AGI_VERSION", "services": ["app"]}],
                  "previous_images": {"app": {"image": "futureagi/standalone:latest", "version": "v1.0.0"}}}
        for value in ("", "latest"):
            with self.subTest(value=value):
                target["previous_values"]["FUTURE_AGI_VERSION"] = value
                self.assertEqual(Updater._previous_release(target, {}), "v1.0.0")


class GitComparisonMutationTests(GitCheckoutCase):
    def differences(self, previous="v1.0.0"):
        return self.updater()._deployment_differences(previous)

    def test_untracked_nested_directories_do_not_duplicate_the_same_file(self):
        updater = self.updater()
        output = b"deploy/conf/settings.xml\0"
        result = subprocess.CompletedProcess(["git", "ls-files"], 0, output, b"")
        with mock.patch.object(updater, "_git", return_value=result):
            self.assertEqual(updater._untracked(["deploy", "deploy/conf"]), ["deploy/conf/settings.xml"])

    def test_extra_tracked_scan_continues_after_a_missing_file(self):
        self.write({"deploy/conf/z-extra.xml": "<extra/>\n"})
        updater = self.updater()
        names = b"deploy/conf/a-gone.xml\0deploy/conf/z-extra.xml\0"

        def tree(*args):
            stdout = names if args[4] == "HEAD" else b""
            return subprocess.CompletedProcess(["git", *args], 0, stdout, b"")

        with mock.patch.object(updater, "_git", tree):
            self.assertEqual(updater._extra_tracked("refs/tags/v1.0.0", ["deploy/conf"]),
                             ["deploy/conf/z-extra.xml"])

    def test_git_ignores_environment_that_points_at_another_repository(self):
        other = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, str(other), True)
        (other / "tracked.txt").write_text("external\n", encoding="utf-8")
        git(other, "init", "-q")
        git(other, "add", "-A")
        git(other, "commit", "-q", "-m", "external repository")
        git(other, "tag", "v1.0.0")
        with mock.patch.dict(os.environ, {"GIT_DIR": str(other / ".git")}):
            self.assertFalse(self.updater()._has_tag("v1.0.0"))

    def test_failed_git_status_does_not_trust_partial_stdout(self):
        self.release_repo()
        self.assertNotEqual(self.differences()["differ"], [], "the files differ: a comparison would say so")
        real = Updater._git
        root = str(self.root)
        # Each answer alone, the other git command answering truly: a failed command's output is not trusted, and
        # `--is-inside-work-tree` must answer exactly "true" (it says "false" inside the .git directory).
        answers = {
            "inside fails": (("rev-parse", "--is-inside-work-tree"), 128, b"true\n"),
            "inside says false": (("rev-parse", "--is-inside-work-tree"), 0, b"false\n"),
            "top fails": (("rev-parse", "--show-toplevel"), 128, (root + "\n").encode()),
        }
        for name, (command, code, stdout) in answers.items():
            def partial_output(updater, *args, command=command, code=code, stdout=stdout):
                if args == command:
                    argv = ["git", "-C", root, "--literal-pathspecs", *args]
                    return subprocess.CompletedProcess(argv, code, stdout, b"fatal: failed\n" if code else b"")
                return real(updater, *args)

            with self.subTest(answer=name):
                with mock.patch.object(Updater, "_git", partial_output):
                    check = self.differences()
                self.assertEqual((check["note"], check["differ"]), ("this checkout is not a git work tree", []))

    def test_git_error_uses_the_last_nonempty_diagnostic_line(self):
        result = subprocess.CompletedProcess(["git", "-C", str(self.root), "--literal-pathspecs", "ls-tree"],
                                             128, b"", b"fatal: first\n\nfatal: final\n")
        self.assertEqual(Updater._git_failed(result), "`git ls-tree` failed: fatal: final")

    def test_invalid_release_names_are_rejected_without_asking_git(self):
        updater = self.updater()
        found = subprocess.CompletedProcess(["git"], 0, b"valid\n", b"")
        with mock.patch.object(updater, "_git", return_value=found) as git_call:
            self.assertFalse(updater._has_tag("latest"))
        git_call.assert_not_called()

    def test_previous_release_list_files_are_compared_even_if_current_list_omits_them(self):
        old_compose = "services:\n  app:\n    image: futureagi/standalone:latest\n"
        self.write({"deploy/update/deployment-files.txt": "docker-compose.yml\ndeploy/required.conf\n",
                    "docker-compose.yml": old_compose, "deploy/required.conf": "old config\n"})
        git(self.root, "init", "-q")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", "previous release")
        git(self.root, "tag", "v1.0.0")
        self.write({"deploy/update/deployment-files.txt": "docker-compose.yml\n",
                    "docker-compose.yml": old_compose, "deploy/required.conf": "new config\n"})
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", "current release")

        self.assertEqual(self.differences()["differ"], ["deploy/required.conf"])


class ReceiptMutationEdgeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(os.path.realpath(temporary.name))
        self.state = StateDir(self.root)

    @staticmethod
    def number(path):
        return int(path.stem.rsplit("-", 1)[1])

    def write_raw(self, number, record):
        """A receipt file as an earlier bin/update left it."""

        self.state.ensure()
        path = self.state.path / f"receipt-20261019T000000Z-{number}.json"
        path.write_text(json.dumps(record), encoding="utf-8")
        return path

    IDENTITY = {"mode": "standalone", "production": False, "env_file": ".env", "project": "fagi-test"}

    def test_receipts_written_with_schema_two_stay_readable(self):
        # Receipts outlive the bin/update that wrote them: schema 2 on disk is what rollback reads.
        old = self.write_raw(1000, dict(self.IDENTITY, kind="apply", status="succeeded", schema_version=2))
        self.assertEqual([r["_path"] for r in self.state.history(self.IDENTITY)], [str(old)])
        self.assertEqual(self.state.load_receipt(old)["status"], "succeeded")
        written = json.loads(self.state.write_receipt(dict(self.IDENTITY, kind="apply")).read_text(encoding="utf-8"))
        self.assertEqual(written["schema_version"], 2)

    def test_a_receipt_of_another_schema_does_not_hide_later_ones(self):
        self.write_raw(1000, dict(self.IDENTITY, kind="apply", status="succeeded", schema_version=1))
        later = self.write_raw(2000, dict(self.IDENTITY, kind="apply", status="succeeded", schema_version=2))
        self.assertEqual([r["_path"] for r in self.state.history(self.IDENTITY)], [str(later)])

    def test_receipt_number_stays_after_previous_when_clock_steps_back(self):
        # The newest receipt was written while the clock ran ahead (NTP then stepped it back).
        ahead = 2_000_000_000_000_000_000
        newest = self.write_raw(ahead, dict(self.IDENTITY, kind="apply", status="succeeded", schema_version=2))
        with mock.patch.object(receipt_module.time, "time_ns", return_value=ahead - 10**12):
            second = self.state.write_receipt({"kind": "apply", "status": "succeeded"})
        self.assertEqual(self.number(second), ahead + 1)
        self.assertEqual(self.state.receipts(), [newest, second])

    def test_receipt_number_is_the_clock_when_it_is_ahead_of_the_newest(self):
        newest = self.write_raw(1000, dict(self.IDENTITY, kind="apply", status="succeeded", schema_version=2))
        with mock.patch.object(receipt_module.time, "time_ns", return_value=5_000):
            second = self.state.write_receipt({"kind": "apply", "status": "succeeded"})
        self.assertEqual(self.number(second), 5_000)
        self.assertEqual(self.state.receipts(), [newest, second])

    def test_receipt_never_persists_internal_path(self):
        path = self.state.write_receipt({"kind": "apply", "status": "succeeded", "_path": "/private/source"})
        record = json.loads(path.read_text(encoding="utf-8"))
        self.assertNotIn("_path", record)

    def test_receipt_file_is_indented_json_ending_in_a_newline(self):
        # Receipts are read by people too (`status` names them): two-space indented, sorted, newline-terminated.
        record = {"kind": "apply", "status": "succeeded", "env_changes": {"FUTURE_AGI_VERSION": "v1.1.0"}}
        path = self.state.write_receipt(record)
        expected = dict(record, schema_version=2)
        self.assertEqual(path.read_text(encoding="utf-8"), json.dumps(expected, sort_keys=True, indent=2) + "\n")

    def test_state_file_bytes_are_flushed_before_the_fsync(self):
        real_fsync = os.fsync
        synced = []

        def fsync(fd):
            if stat.S_ISREG(os.fstat(fd).st_mode):
                synced.append(os.fstat(fd).st_size)
            real_fsync(fd)

        with mock.patch.object(os, "fsync", fsync):
            self.state.write_journal({"kind": "apply", "stage": "prepare"})
        self.assertEqual(synced, [self.state.journal_path.stat().st_size] * (2 if sys.platform == 'darwin' else 1))
        self.assertGreater(synced[0], 0)

    def test_secret_values_at_the_six_character_boundary_are_scrubbed(self):
        self.assertEqual(receipt_module.secret_values({"API_TOKEN": "abcdef", "LICENSE": "1234567",
                                                       "DB_PASSWORD": "abcde", "COMPOSE_PROFILES": "workers"}),
                         ["1234567", "abcdef"])

    def test_ownership_hint_checks_the_state_directory_after_a_missing_file(self):
        stat_result = mock.Mock(st_uid=2000)
        with mock.patch.object(os, "geteuid", return_value=1000), \
                mock.patch.object(os, "stat", side_effect=[FileNotFoundError(), stat_result]), \
                mock.patch.object(receipt_module, "_user_name",
                                  side_effect=lambda uid: {1000: "me", 2000: "owner"}[uid]):
            hint = receipt_module.ownership_hint(self.root / "missing", self.state.path)
        self.assertIn("belongs to owner", hint)

    def test_lock_file_is_private(self):
        with self.state.lock():
            pass
        self.assertEqual(stat.S_IMODE(self.state.lock_path.stat().st_mode), 0o600)

    def test_lock_replaces_stale_holder_text_before_writing_pid(self):
        self.state.ensure()
        self.state.lock_path.write_text("pid 123456789\n", encoding="ascii")
        with self.state.lock():
            self.assertEqual(self.state.lock_holder(), f"pid {os.getpid()}")

    def test_a_holder_that_cannot_write_its_pid_leaves_no_stale_one(self):
        # A full disk, say: the lock still works, and no earlier holder's pid (or part of it) is reported as its.
        self.state.ensure()
        self.state.lock_path.write_text("pid 123456789\n", encoding="ascii")
        with mock.patch.object(os, "pwrite", side_effect=OSError(errno.ENOSPC, "No space left on device")):
            with self.state.lock():
                self.assertEqual(self.state.lock_holder(), "lock held")
        self.assertEqual(self.state.lock_path.read_bytes(), b"")

    def test_a_lock_held_for_a_moment_is_waited_for_with_pauses(self):
        # lock_holder() peeks with a shared lock: lock() retries for a moment, sleeping between tries.
        import fcntl

        real_flock = fcntl.flock
        attempts = []

        def flock(fd, operation):
            if operation == fcntl.LOCK_EX | fcntl.LOCK_NB:
                attempts.append(operation)
                if len(attempts) == 1:
                    raise BlockingIOError(errno.EWOULDBLOCK, "Resource temporarily unavailable")
            return real_flock(fd, operation)

        with mock.patch.object(fcntl, "flock", flock), \
                mock.patch.object(receipt_module.time, "sleep") as sleep:
            with self.state.lock():
                self.assertEqual(self.state.lock_holder(), f"pid {os.getpid()}")
        self.assertEqual(len(attempts), 2)
        sleep.assert_called_once_with(0.02)

    def test_a_lock_held_by_another_run_is_refused_after_the_retry_window(self):
        import fcntl

        self.state.ensure()
        other = os.open(str(self.state.lock_path), os.O_RDWR | os.O_CREAT, 0o600)
        self.addCleanup(os.close, other)
        fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
        os.pwrite(other, b"pid 4242\n", 0)
        clock = iter([100.0, 100.1, 100.2, 100.3])
        with mock.patch.object(receipt_module.time, "monotonic", lambda: next(clock)), \
                mock.patch.object(receipt_module.time, "sleep") as sleep:
            with self.assertRaises(StateError) as caught:
                with self.state.lock():
                    pass
        self.assertEqual(str(caught.exception), "another bin/update is running in this checkout (pid 4242)")
        self.assertEqual(sleep.call_count, 2)

    def test_state_directory_is_private_from_the_moment_it_exists(self):
        # Whatever the umask, nobody else can enter the directory, not even before ensure() chmods it.
        previous = os.umask(0)
        self.addCleanup(os.umask, previous)
        real_chmod = os.chmod
        modes = []

        def chmod(path, mode, *args, **kwargs):
            if str(path) == str(self.state.path):
                modes.append(stat.S_IMODE(os.stat(path).st_mode))
            return real_chmod(path, mode, *args, **kwargs)

        with mock.patch.object(os, "chmod", chmod):
            self.state.ensure()
        self.assertEqual(modes, [0o700])
        self.assertEqual(stat.S_IMODE(self.state.path.stat().st_mode), 0o700)

    def test_lock_holder_closes_its_peek_descriptor(self):
        with self.state.lock():
            pass
        real_open = os.open
        descriptors = []

        def recording_open(*args, **kwargs):
            fd = real_open(*args, **kwargs)
            descriptors.append(fd)
            return fd

        with mock.patch.object(os, "open", recording_open):
            self.assertIsNone(self.state.lock_holder())
        self.assertEqual(len(descriptors), 1)
        with self.assertRaises(OSError):
            os.fstat(descriptors[0])

    def test_json_read_error_keeps_the_default_owner_hint_path(self):
        path = self.root / "unreadable.json"
        with mock.patch.object(Path, "read_text", side_effect=PermissionError(13, "Permission denied")), \
                mock.patch.object(receipt_module, "ownership_hint",
                                  side_effect=lambda target, state_dir: f" owned:{state_dir}"):
            with self.assertRaises(StateError) as ctx:
                receipt_module._read_json(path)
        self.assertIn(f"owned:{path.parent}", str(ctx.exception))

    def test_json_write_error_keeps_the_default_owner_hint_path(self):
        path = self.root / "unwritable" / "journal.json"
        with mock.patch.object(os, "open", side_effect=PermissionError(13, "Permission denied")), \
                mock.patch.object(receipt_module, "ownership_hint",
                                  side_effect=lambda target, state_dir: f" owned:{state_dir}"):
            with self.assertRaises(StateError) as ctx:
                receipt_module._write_json(path, {"kind": "apply"})
        self.assertIn(f"owned:{path.parent}", str(ctx.exception))


class BackupAndWaitMutationTests(UpdaterCase):
    def test_backup_elapsed_seconds_are_measured_as_a_duration(self):
        updater = self.updater()
        updater.hook_runner = lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0)
        with mock.patch.object(time, "monotonic", side_effect=[100.0, 101.24]):
            record = updater._backup(BACKUP, False, {})
        self.assertEqual(record["seconds"], 1.2)

    def test_the_longest_backup_timeout_is_accepted(self):
        self.apply(updater=self.updater(FUTURE_AGI_UPDATE_BACKUP_TIMEOUT_SECONDS="86400"))
        self.assertEqual(self.hook.calls[0][1]["timeout"], 86400)
        self.assertEqual(self.last_receipt()["status"], "succeeded")


class StopProcessGroupProbeTests(unittest.TestCase):
    """_stop_process_group after its SIGTERM: the hook's leader can exit while the rest of its group lives on."""

    def stop(self, polls, group_alive, grace):
        process = mock.Mock(pid=4242)
        results = list(polls)
        process.poll.side_effect = lambda: results.pop(0) if len(results) > 1 else results[0]
        sent = []
        alive = list(group_alive)

        def killpg(pid, signum):
            self.assertEqual(pid, 4242)
            sent.append(signum)
            if signum == 0 and not (alive.pop(0) if len(alive) > 1 else alive[0]):
                raise ProcessLookupError()

        ticks = iter(range(10, 10_000))  # a clock that moves one second each time it is read
        with mock.patch("os.killpg", side_effect=killpg), \
                mock.patch.object(apply_module.time, "monotonic", lambda: float(next(ticks))), \
                mock.patch.object(apply_module.time, "sleep") as sleep:
            apply_module._stop_process_group(process, grace)
        return process, sent, sleep

    def test_a_group_that_outlives_its_leader_is_killed_after_the_grace(self):
        # The leader exits on SIGTERM; a child that ignores it keeps the group: it gets SIGKILL once the grace is over.
        process, sent, sleep = self.stop(polls=[0], group_alive=[True], grace=2.5)
        self.assertEqual(sent, [signal.SIGTERM, 0, 0, signal.SIGKILL])
        self.assertEqual(sleep.call_args_list, [mock.call(0.05)] * 2)
        process.wait.assert_called_once_with(timeout=apply_module.HOOK_REAP_SECONDS)

    def test_a_group_gone_with_its_leader_gets_no_sigkill_and_no_more_waiting(self):
        # Its id may belong to another process group by the time a SIGKILL would be sent.
        process, sent, sleep = self.stop(polls=[None, 0], group_alive=[False], grace=30.0)
        self.assertEqual(sent, [signal.SIGTERM, 0])
        self.assertEqual(sleep.call_args_list, [mock.call(0.05)])
        process.wait.assert_not_called()


class RunHookContractTests(unittest.TestCase):
    """run_hook answers like subprocess.run: a failure is returned unless check asks for an exception."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.dir = os.path.realpath(temporary.name)

    def hook(self, code, **kwargs):
        return run_hook(["/bin/sh", "-c", f"exit {code}"], cwd=self.dir, env=dict(os.environ), timeout=30, **kwargs)

    def test_a_failure_is_returned_by_default(self):
        self.assertEqual(self.hook(3).returncode, 3)

    def test_check_accepts_exit_code_zero_only(self):
        self.assertEqual(self.hook(0, check=True).returncode, 0)
        for code in (1, 2):
            with self.subTest(code=code):
                with self.assertRaises(subprocess.CalledProcessError) as caught:
                    self.hook(code, check=True)
                self.assertEqual(caught.exception.returncode, code)


class UpdaterStepMutationTests(UpdaterCase):
    def test_each_message_is_flushed_before_a_command_writes_to_the_same_terminal(self):
        raw = io.BytesIO()
        out = io.TextIOWrapper(raw, encoding="utf-8")  # block-buffered, like stderr on a pipe
        updater = Updater(self.inst, load_settings(self.root, None, environ={}), self.state, out=out,
                          readiness=self.ready, hook_runner=self.hook)
        updater.say("Pulling futureagi/standalone:v1.1.0")
        self.assertEqual(raw.getvalue(), b"Pulling futureagi/standalone:v1.1.0\n")

    def test_restore_refuses_all_keys_if_an_operator_edits_one_line(self):
        self.build('distributed', values={'FUTURE_AGI_VERSION': '', 'FRONTEND_VERSION': 'v1.0.0'})
        changes = {'FUTURE_AGI_VERSION': TARGET, 'FRONTEND_VERSION': TARGET}
        record = envfile_module.recovery_record(self.env_path, changes)
        self.real_write(self.env_path, changes, recovery=record)
        self.env_path.write_bytes(self.env_bytes().replace(b'FUTURE_AGI_VERSION=v1.1.0',
                                                          b'FUTURE_AGI_VERSION=v1.1.0 # operator'))
        before = self.env_bytes()
        with self.assertRaises(StateError):
            self.updater()._restore_env({'env_recovery': record})
        self.assertEqual(self.env_bytes(), before)

    def test_restore_into_a_missing_env_file_retains_recovery_state(self):
        record = envfile_module.recovery_record(self.env_path, {'FUTURE_AGI_VERSION': TARGET})
        self.env_path.unlink()
        with self.assertRaises(StateError):
            self.updater()._restore_env({'env_recovery': record})
        self.assertFalse(self.env_path.exists())

    def test_snapshot_skips_one_off_and_other_containers_and_records_the_rest(self):
        old_id = id_of("futureagi/standalone", "v1.0.0")
        image = "futureagi/standalone:v1.0.0"
        containers = [{"service": "app", "image": image, "image_id": old_id, "oneoff": True},
                      {"service": "postgres", "image": "postgres:16", "image_id": POSTGRES_ID},
                      {"service": "app", "image": image, "image_id": ""},
                      {"service": "app", "image": image, "image_id": old_id}]
        self.assertEqual(self.updater()._snapshot_images(["app"], containers), {"app": {
            "image": image, "image_id": old_id, "version": "v1.0.0",
            "repo_digests": [f"futureagi/standalone@{digest_of('futureagi/standalone', 'v1.0.0')}"]}})

    def test_config_check_goes_on_past_a_service_whose_key_this_run_leaves_alone(self):
        self.build("distributed", groups=(FI_COLLECTOR, BACKEND, FRONTEND))
        with self.assertRaises(UpdateError) as caught:
            self.updater()._check_config({"FUTURE_AGI_VERSION": "v1.0.0", "FRONTEND_VERSION": TARGET})
        self.assertIn("service frontend resolves to futureagi/frontend:v1.0.0, not tag v1.1.0", str(caught.exception))


class RollbackRestoreMutationTests(UpdaterCase):
    def test_a_key_pinned_for_an_inactive_profile_does_not_stop_the_keys_after_it(self):
        receipt = {"components": [
            {"component": "code_executor", "variable": "CODE_EXECUTOR_VERSION", "services": []},
            {"component": "standalone", "variable": "FUTURE_AGI_VERSION", "services": ["app"]}],
            "previous_values": {"CODE_EXECUTOR_VERSION": "v1.0.0", "FUTURE_AGI_VERSION": "v1.0.0"},
            "previous_images": {}}
        values, expected = self.updater()._restore_values(receipt)
        self.assertEqual(values, {"CODE_EXECUTOR_VERSION": "v1.0.0", "FUTURE_AGI_VERSION": "v1.0.0"})
        self.assertEqual(expected, {"FUTURE_AGI_VERSION": "v1.0.0"})

    def test_tags_that_ran_cover_every_item_and_every_readable_image(self):
        items = [{"current_tags": ["local"]}, {"services": ["backend", "worker", "beat"]}]
        previous_images = {"backend": {"image": "UPPER/Case:v1"},  # not an image reference: skipped
                           "worker": {"image": "futureagi/future-agi:local"},
                           "beat": {"image": "futureagi/future-agi"}}
        self.assertEqual(Updater._tags_that_ran(items, previous_images), ["local", "local", "latest"])

    def test_a_stray_is_on_the_restored_image_only_if_all_its_containers_are(self):
        self.assertTrue(Updater._on_restored_image(["a", "b"], {"a", "b"}))
        self.assertFalse(Updater._on_restored_image(["a", "b"], {"a"}))
        self.assertFalse(Updater._on_restored_image([], {"a"}))
        self.assertFalse(Updater._on_restored_image(["a"], None))

    def test_a_rollback_receipt_that_names_no_update_is_ignored(self):
        applied = Path(self.applied()).resolve()
        self.state.write_receipt({"kind": "rollback", "status": "rolled_back", "mode": self.inst.mode,
                                  "production": self.inst.production, "project": self.inst.project,
                                  "env_file": self.inst.relative(self.inst.env_file)})
        self.rollback()
        self.assertEqual((self.last_receipt()["status"], self.last_receipt()["rolls_back"]),
                         ("rolled_back", str(applied)))
        self.assertEqual(self.env_bytes(), self.original)

    def test_rolling_back_an_older_update_names_the_newest_one_to_roll_back_first(self):
        first = self.craft_apply()
        self.craft_apply(previous="v1.1.0", target="v1.2.0")
        newest = shlex.quote(str(Path(self.craft_apply(previous="v1.2.0", target="v1.3.0")).resolve()))
        exc = self.assertRaisesExactly(Blocked, self.rollback, first)
        self.assertEqual(str(exc), f"a newer update ({newest}) ran after that one; roll that back first: "
                                   f"`./bin/update rollback --receipt {newest} --yes`")

    def test_another_installs_journal_is_refused_before_this_installs_receipts_are_read(self):
        self.build("production", current="v1.1.0")
        self.craft_journal("pulled", env_file=None, production=False)
        (self.state.path / "receipt-20261019T000000Z-1.json").write_text("{not json", encoding="utf-8")
        exc = self.assertRaisesExactly(Blocked, self.rollback, yes=False)
        self.assertTrue(str(exc).startswith("an interrupted bin/update apply for "), str(exc))
        self.assertIsNotNone(self.journal())


class RolloutStrayOnAVerifiedImageTests(UpdaterCase):
    """After the restart, a disabled profile's container on an image this run verified runs its release."""

    def setUp(self):
        super().setUp()
        self.build("distributed")
        self.inst.profile_services["worker-default"] = "futureagi/future-agi:${FUTURE_AGI_VERSION:-latest}"

    def test_a_stray_on_the_restored_image_passes_whatever_tag_it_was_created_from(self):
        self.inst.metadata[id_of("futureagi/future-agi", "v1.0.0")]["version"] = ""  # no OCI version label
        applied = Path(self.applied()).resolve()
        # Created from another tag of the very image the rollback restores and verifies (by id).
        self.inst.extra_containers.append(stray_container("worker-default", "v1.0.0", tag="custom"))
        self.rollback()
        self.assertEqual((self.last_receipt()["status"], self.last_receipt()["rolls_back"]),
                         ("rolled_back", str(applied)))


class RecoverOnlyRollbackMutationTests(UpdaterCase):
    """`rollback` without --yes after recovering a rollback that was killed: what it says depends on where it was."""

    def killed_rollback(self, failure_on="pull", receipt=None):
        if failure_on == "pull":
            self.prune("v1.0.0")  # pulled only when it is not on the host
        self.inst.failures[failure_on] = SystemExit(137)
        with self.assertRaises(SystemExit):
            self.rollback(receipt)
        self.inst.failures = {}
        self.out = io.StringIO()

    def test_a_rollback_killed_before_its_restart_leaves_the_update_to_roll_back(self):
        applied = Path(self.applied()).resolve()
        self.killed_rollback()
        self.assertIsNone(self.rollback(yes=False))
        text = self.out.getvalue()
        self.assertIn("Recovered an interrupted rollback to v1.0.0: it stopped before restarting anything", text)
        self.assertNotIn("is not finished", text)
        self.assertIn("Nothing else was changed. To roll back v1.1.0 as well (to FUTURE_AGI_VERSION=v1.0.0), run "
                      f"`./bin/update rollback --receipt {applied} --yes`.", text)

    def test_recovery_refuses_an_operator_edit_without_overwriting_it(self):
        self.applied()
        self.killed_rollback()
        self.env_path.write_bytes(self.env_bytes() + b'INSTALL_STABILITY_SECONDS=0\n')
        self.inst.env = read_env(self.env_path)
        before = self.env_bytes(), self.state.journal_path.read_bytes(), self.receipts()
        with self.assertRaises(StateError):
            self.rollback(yes=False)
        self.assertEqual((self.env_bytes(), self.state.journal_path.read_bytes(), self.receipts()), before)

    def test_a_staged_update_that_never_started_is_previewed_as_restarting_nothing(self):
        staged = staged_by(self)
        self.killed_rollback(failure_on="config")
        self.assertIsNone(self.rollback(yes=False))
        text = self.out.getvalue()
        self.assertIn("The update to v1.1.0 was staged and never started: the rollback puts the version settings back "
                      "and verifies the previous images, and restarts nothing.", text)
        self.assertIn(f"`./bin/update rollback --receipt {staged} --yes`", text)

    def test_the_rollback_killed_mid_rollout_is_the_one_to_finish_whatever_receipt_was_given(self):
        first = Path(self.applied()).resolve()
        second = Path(self.applied(self.plan(target="v1.2.0"))).resolve()
        self.killed_rollback(failure_on="up")
        self.assertIsNone(self.rollback(first, yes=False))
        text = self.out.getvalue()
        self.assertNotIn("cannot be finished now", text)
        self.assertIn(f"finished. To finish it, run `./bin/update rollback --receipt {second} --yes`.", text)


class StoppedRunWordingMutationTests(UpdaterCase):
    def test_a_retried_rollback_quotes_every_key_it_restores(self):
        # SIMULATION_RUNNER_VERSION, pinned for a profile that is not enabled, is put back (unset) like the others.
        self.build("distributed")
        plan = self.plan()
        plan["components"].append(inactive_item())
        plan["env_changes"][RUNNER] = TARGET
        applied = Path(self.apply(plan)).resolve()
        self.inst.failures["up"] = InstallError("`docker compose up` failed with exit code 1")
        self.assertRaisesExactly(FailedDuringRollout, self.rollback)
        self.prune("v1.0.0")  # pulled only when it is not on the host
        self.inst.failures = {"pull": InstallError("`docker compose pull` failed: network unreachable")}
        exc = self.assertRaisesExactly(FailedBeforeRollout, self.rollback)
        self.assertIn("is still unfinished: .env selects FRONTEND_VERSION=v1.0.0, FUTURE_AGI_VERSION=v1.0.0, "
                      "SIMULATION_RUNNER_VERSION=unset, and some containers may still run v1.1.0. To finish it, run "
                      f"`./bin/update rollback --receipt {applied} --yes`.", str(exc))

    def failing_rollback(self):
        self.prune("v1.0.0")  # pulled only when it is not on the host
        self.inst.failures["pull"] = InstallError("network down")
        return str(self.assertRaisesExactly(FailedBeforeRollout, self.rollback))

    def test_a_stack_whose_images_carry_no_version_label_reads_as_running_the_update(self):
        # The rollback found the stack on images without an OCI version label: it says the update, not nothing.
        self.inst.metadata[id_of("futureagi/standalone", TARGET)]["version"] = ""
        self.applied()
        text = self.failing_rollback()
        self.assertIn("rollback stopped before restarting anything: network down. Nothing was changed: .env still "
                      "selects FUTURE_AGI_VERSION=v1.1.0, which the stack runs. To try the rollback again", text)

    def test_a_staged_update_on_unlabelled_images_names_the_release_the_stack_still_runs(self):
        self.inst.metadata[id_of("futureagi/standalone", "v1.0.0")]["version"] = ""
        staged_by(self)
        text = self.failing_rollback()
        self.assertIn("The update to v1.1.0 staged earlier is still pending: .env selects it "
                      "(FUTURE_AGI_VERSION=v1.1.0), and the stack still runs v1.0.0. To finish it", text)

    def test_a_stopped_resume_names_only_the_keys_the_update_changed(self):
        # FRONTEND_VERSION selected the release already: the update changed FUTURE_AGI_VERSION only.
        self.build("distributed", values={"FUTURE_AGI_VERSION": "v1.0.0", "FRONTEND_VERSION": TARGET})
        self.ready.failure = NotReady("the API is not answering")
        self.assertRaisesExactly(FailedDuringRollout, self.apply, self.plan(env_changes={"FUTURE_AGI_VERSION": TARGET}))
        self.ready.failure = None
        self.prune(TARGET)  # pulled only when it is not on the host
        self.inst.failures["pull"] = InstallError("`docker compose pull` failed: network unreachable")
        exc = self.assertRaisesExactly(FailedBeforeRollout, self.apply,
                                       self.plan(env_changes={}, resume=True, current=[TARGET], action="keep"))
        self.assertIn("is still unfinished: .env selects it (FUTURE_AGI_VERSION=v1.1.0).", str(exc))


class DeploymentComparisonMutationTests(GitCheckoutCase):
    IGNORE = ".env\n.futureagi-updates/\n"

    def differences(self, previous="v1.0.0"):
        return self.updater()._deployment_differences(previous)

    def commit(self, files, tag=None):
        self.write(files)
        if not (self.root / ".git").exists():
            git(self.root, "init", "-q")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", tag or "next")
        if tag:
            git(self.root, "tag", tag)

    def test_files_back_step_names_the_checkout_only_when_files_differ(self):
        self.release_repo()
        updater = self.updater()
        self.assertEqual(updater._files_back_step("v1.1.0"), "")  # the checkout is at v1.1.0
        self.assertEqual(updater._files_back_step("v1.0.0"), "bring back v1.0.0's deployment files with `git checkout "
                                                             "v1.0.0 -- docker-compose.yml deploy/clickhouse`")

    def test_no_directories_have_no_untracked_or_extra_files_and_ask_git_nothing(self):
        updater = self.updater()
        with mock.patch.object(updater, "_git", side_effect=AssertionError("must not run git")):
            self.assertEqual(updater._untracked([]), [])
            self.assertEqual(updater._extra_tracked("refs/tags/v1.0.0", []), [])

    def test_extra_tracked_names_a_file_in_nested_directories_once(self):
        self.write({"deploy/conf/extra.xml": "<extra/>\n"})
        updater = self.updater()

        def tree(*args):
            stdout = b"deploy/conf/extra.xml\0" if args[4] == "HEAD" else b""
            return subprocess.CompletedProcess(["git", *args], 0, stdout, b"")

        with mock.patch.object(updater, "_git", tree):
            self.assertEqual(updater._extra_tracked("refs/tags/v1.0.0", ["deploy", "deploy/conf"]),
                             ["deploy/conf/extra.xml"])

    def test_a_release_path_inside_a_listed_directory_is_compared_with_that_directory(self):
        compose = ("services:\n  app:\n    image: futureagi/standalone:${FUTURE_AGI_VERSION:-latest}\n    volumes:\n"
                   "      - ./deploy/clickhouse/users.d/default.xml:/etc/clickhouse-server/users.d/default.xml:ro\n")
        self.commit({".gitignore": self.IGNORE, "docker-compose.yml": compose,
                     "deploy/update/deployment-files.txt": "docker-compose.yml\ndeploy/clickhouse\n",
                     "deploy/clickhouse/users.d/default.xml": "<clickhouse/>\n"}, "v1.0.0")
        self.commit({"deploy/clickhouse/users.d/default.xml": "<clickhouse><users/></clickhouse>\n"})
        self.assertEqual(self.differences()["differ"], ["deploy/clickhouse"])

    def test_a_new_listed_file_named_only_outside_the_compose_files_is_not_absent(self):
        self.commit({".gitignore": self.IGNORE, "deploy/update/deployment-files.txt": "docker-compose.yml\n",
                     "docker-compose.yml": "services:\n  app:\n    image: futureagi/standalone:latest\n"},
                    "v1.0.0")
        self.commit({"deploy/update/deployment-files.txt": "docker-compose.yml\ndeploy/notes.conf\ndeploy/new.conf\n",
                     "deploy/notes.conf": "include deploy/new.conf\n", "deploy/new.conf": "new\n"})
        check = self.differences()
        self.assertEqual((check["note"], check["differ"], check["absent"]), ("", [], []))
        # Named by a compose file, the same file is one the previous release lacks.
        self.commit({"docker-compose.yml": "services:\n  app:\n    image: futureagi/standalone:latest\n    env_file:\n"
                                           "      - deploy/new.conf\n"})
        self.assertEqual(self.differences()["absent"], ["deploy/new.conf"])




if __name__ == "__main__":
    unittest.main()
