"""bin/update: the installation model (deploy/update/install.py), the CLI
(deploy/update/__main__.py) and the bin/update wrapper.

By default Docker is never run: Compose and the Docker CLI are small fake
runners that answer with canned `docker compose config` / `docker ps` /
`docker inspect` output. Opt-in Compose tests only run daemon-free config
commands (and version probes in the release matrix). Repository-consistency tests read the real compose files,
deploy/setup.sh, bin/install and deploy/update/deployment-files.txt. The
bin/update tests run the wrapper as a subprocess from a temporary copy of the
checkout (bash, no Docker, no network).
"""

import contextlib
import functools
import importlib
import io
import json
import os
import posixpath
import re
import runpy
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from deploy.update import install  # noqa: E402
from deploy.update.apply import (  # noqa: E402
    Blocked, FailedBeforeRollout, FailedDuringRollout, InterruptedRun, UpdateError)
from deploy.update import envfile as envfile_module  # noqa: E402
from deploy.update.envfile import EnvFileError  # noqa: E402
from deploy.update.install import (  # noqa: E402
    COMPONENT_VARIABLES, DISTRIBUTED_FILE, DISTRIBUTED_ONLY_SERVICES, PRODUCTION_ENV, PRODUCTION_OVERLAY,
    REPOSITORY_COMPONENTS, SETUP_SECRET_NAMES, STANDALONE_FILE, STANDALONE_ONLY_SERVICES, VERSION_VARIABLES,
    CommandResult, InstallError, Installation, component_for, resolve_compose_command)
from deploy.update.manifest import (  # noqa: E402
    COMPONENT_REPOSITORIES, COMPONENT_TAG_SUFFIX, ManifestError, parse_image_ref, read_deployment_file_list)
from deploy.update.readiness import NotReady  # noqa: E402
from deploy.update.receipt import STATE_DIR, StateDir, StateError  # noqa: E402
from deploy.update.settings import DEFAULT_MANIFEST_URL, SettingsError  # noqa: E402
from deploy.update.trust import Verification, VerificationError  # noqa: E402

cli = importlib.import_module("deploy.update.__main__")

COMPOSE = ["docker", "compose"]
COMPOSE_FILES = (STANDALONE_FILE, DISTRIBUTED_FILE, PRODUCTION_OVERLAY)
DIGEST = "sha256:" + "a" * 64


# ----------------------------------------------------------------- fakes --

def completed(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


class Runner:
    """A subprocess.run stand-in: records every call, answers from `handler`."""

    def __init__(self, handler=None):
        self.calls = []
        self.handler = handler
        self.last_config = None

    def __call__(self, args, **kwargs):
        self.calls.append((list(args), dict(kwargs)))
        result = completed() if self.handler is None else self.handler(list(args), kwargs)
        if "config" in args and "--format" in args and "--services" not in args and result.returncode == 0:
            try:
                self.last_config = json.loads(result.stdout)
            except (TypeError, ValueError):
                self.last_config = None
        elif "config" in args and "--services" in args and result.returncode == 0 and self.last_config is not None:
            try:
                json.loads(result.stdout)
            except (TypeError, ValueError):
                if result.stdout.strip():
                    return result
            else:
                pass  # Older fakes answered every Compose call with their JSON fixture.
            services = self.last_config.get("services") if isinstance(self.last_config, dict) else None
            if isinstance(services, dict):
                enabled = set(filter(None, str((kwargs.get("env") or {}).get("COMPOSE_PROFILES", "")).split(",")))
                names = [name for name, service in services.items()
                         if not (isinstance(service, dict) and service.get("profiles"))
                         or "*" in enabled or enabled.intersection(service.get("profiles", []))]
                return completed(0, "".join(f"{name}\n" for name in names))
        return result

    def commands(self):
        return [args for args, _ in self.calls]


def container_record(service, *, cid=None, image="futureagi/standalone:v1.0.0", image_id="sha256:app",
                     status="running", exit_code=0, restarts=0, health=None, started="2026-10-19T00:00:00Z",
                     entrypoint=None, cmd=None, project="futureagi"):
    """One element of `docker inspect` output, as the Docker CLI prints it."""

    state = {"Status": status, "ExitCode": exit_code, "StartedAt": started, "Running": status == "running"}
    if health:
        state["Health"] = {"Status": health, "FailingStreak": 0}
    return {
        "Id": cid or ("c0ffee" + service.replace("-", "")).ljust(64, "0")[:64],
        "Image": image_id,
        "RestartCount": restarts,
        "Name": f"/{project}-{service}-1",
        "State": state,
        "Config": {
            "Image": image,
            "Labels": {"com.docker.compose.project": project, "com.docker.compose.service": service,
                       "com.docker.compose.oneoff": "False"},
            "Entrypoint": entrypoint,
            "Cmd": cmd,
        },
    }


def image_record(image_id, *, version="", revision="", repo_digests=None, labels=True):
    record = {"Id": image_id, "RepoTags": [], "RepoDigests": repo_digests}
    if labels:
        record["Config"] = {"Labels": {"org.opencontainers.image.version": version,
                                       "org.opencontainers.image.revision": revision}}
    return record


def docker_handler(config=None, containers=(), images=None, ps=None, inspect=None, *, add_provenance=True):
    """Answers `docker compose config`, `docker ps`, `docker inspect` and `docker image inspect`."""

    images = images or {}
    config = {"name": "futureagi", "services": {}} if config is None else config
    compose_args = []

    def provenance(records, kwargs):
        # Existing parsing fixtures model containers created by this invocation. Ownership-negative tests use
        # their own raw inspect runner, so absent or malformed labels there are never filled in here.
        if not add_provenance:
            return records
        root = Path(kwargs["cwd"])
        files = [compose_args[i + 1] for i, arg in enumerate(compose_args[:-1]) if arg == "-f"]
        if not files:
            env = read_env(root / ".env")
            selected = env.get("COMPOSE_FILE") or STANDALONE_FILE
            separator = (kwargs.get("env") or {}).get("COMPOSE_PATH_SEPARATOR", env.get("COMPOSE_PATH_SEPARATOR"))
            files = [str(root / part) for part in selected.split(separator or os.pathsep)]
        for record in records:
            if not isinstance(record, dict):
                continue
            labels = (record.get("Config") or {}).get("Labels")
            if isinstance(labels, dict) and "com.docker.compose.project" in labels:
                labels.setdefault("com.docker.compose.project.working_dir", str(root))
                labels.setdefault("com.docker.compose.project.config_files", ",".join(files))
                if "--env-file" in compose_args:
                    labels.setdefault("com.docker.compose.project.environment_file",
                                      compose_args[compose_args.index("--env-file") + 1])
        return records

    def handle(args, kwargs):
        if args[:1] == ["docker"] and "config" in args and "--format" in args:
            compose_args[:] = args
            return completed(0, json.dumps(config))
        if args[1:2] == ["ps"]:
            if ps is not None:
                return ps
            return completed(0, "".join(c["Id"] + "\n" for c in containers))
        if args[1:2] == ["inspect"]:
            if inspect is not None:
                try:
                    records = json.loads(inspect.stdout)
                except ValueError:
                    return inspect
                if isinstance(records, list):
                    return completed(inspect.returncode, json.dumps(provenance(records, kwargs)), inspect.stderr)
                return inspect
            wanted = args[2:]
            return completed(0, json.dumps(provenance([c for c in containers if c["Id"] in wanted], kwargs)))
        if args[1:3] == ["image", "inspect"]:
            record = images.get(args[3])
            if record is None:
                return completed(1, "[]\n", f"Error response from daemon: No such image: {args[3]}\n")
            return completed(0, json.dumps([record]))
        return completed(0)

    def prepare(root):
        if add_provenance:
            # Local-parser fixtures model Compose-created containers, including real local config paths.
            for name in (STANDALONE_FILE, DISTRIBUTED_FILE):
                path = root / name
                if not path.exists():
                    path.write_text('services: {}\n')
    handle.prepare = prepare
    return handle


def valid_manifest(version="v1.2.0"):
    components = {}
    for name, repository in COMPONENT_REPOSITORIES.items():
        components[name] = {"image": f"{repository}:{version}{COMPONENT_TAG_SUFFIX.get(name, '')}", "digest": DIGEST}
    return {
        "schema_version": 1, "product": "future-agi", "channel": "stable", "version": version,
        "source_sha": "0" * 40, "published_at": "2026-10-19T00:00:00Z", "minimum_version": "v1.0.0",
        "signature": {"bundle": "future-agi-update-manifest.sigstore.json"},
        "components": components,
        "helm": {"chart": "oci://ghcr.io/future-agi/charts/futureagi", "version": version[1:], "digest": DIGEST},
        "deployment": {"files": {"docker-compose.yml": "b" * 64}},
        "migration": {"requires_bootstrap": False, "rollback": "manual"},
    }


def clean_environ():
    """The process environment minus anything that would steer bin/update."""

    drop = set(VERSION_VARIABLES) | {"COMPOSE_FILE", "COMPOSE_PATH_SEPARATOR", "COMPOSE_PROJECT_NAME",
                                     "COMPOSE_ENV_FILES", "COMPOSE_DISABLE_ENV_FILE", "PYTHONPATH", "PYTHONHOME",
                                     "PYTHONSTARTUP"}
    return {k: v for k, v in os.environ.items() if k not in drop and not k.startswith("FUTURE_AGI_UPDATE_")}


class TempRootTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()

    def write(self, relative, text=""):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def install(self, env_text="", *, environ=None, runner=None, compose=COMPOSE, **kwargs):
        if env_text is not None:
            self.write(".env", env_text)
        prepare = getattr(getattr(runner, 'handler', None), 'prepare', None)
        if prepare is not None:
            prepare(self.root)
        return Installation(self.root, environ={} if environ is None else environ,
                            runner=runner if runner is not None else Runner(), compose=compose, **kwargs)

    def production_files(self, env_text="FUTURE_AGI_VERSION=v1.0.0\n"):
        self.write(PRODUCTION_ENV, env_text)
        self.write(DISTRIBUTED_FILE, "services: {}\n")
        self.write(PRODUCTION_OVERLAY, "services: {}\n")


# ===================================================== mode detection ====

class ModeDetectionTests(TempRootTest):
    def test_standalone_when_compose_file_is_not_in_env(self):
        inst = self.install("SECRET_KEY=x\n")
        self.assertEqual(inst.mode, "standalone")
        self.assertEqual(inst.label, "Standalone")
        self.assertFalse(inst.production)
        self.assertEqual(inst.env_file, self.root / ".env")

    def test_standalone_when_compose_file_is_empty(self):
        self.assertEqual(self.install("COMPOSE_FILE=\n").mode, "standalone")

    def test_standalone_when_compose_file_names_docker_compose_yml(self):
        self.assertEqual(self.install("COMPOSE_FILE=docker-compose.yml\n").mode, "standalone")

    def test_standalone_with_override_files(self):
        inst = self.install("COMPOSE_FILE=docker-compose.yml:docker-compose.override.yml:extra/more.yml\n")
        self.assertEqual(inst.mode, "standalone")

    def test_distributed_when_compose_file_names_distributed_file(self):
        inst = self.install("COMPOSE_FILE=docker-compose.distributed.yml\n")
        self.assertEqual(inst.mode, "distributed")
        self.assertEqual(inst.label, "Distributed")

    def test_distributed_with_override_files_in_any_position(self):
        for value in ("docker-compose.distributed.yml:docker-compose.override.yml",
                      "docker-compose.override.yml:docker-compose.distributed.yml",
                      "./docker-compose.distributed.yml:/srv/overrides/gpu.yml",
                      "/abs/checkout/docker-compose.distributed.yml",
                      "docker-compose.distributed.yml:"):
            with self.subTest(value=value):
                self.assertEqual(self.install(f"COMPOSE_FILE={value}\n").mode, "distributed")

    def test_quoted_exported_and_commented_values_are_read_like_compose(self):
        for line in ('COMPOSE_FILE="docker-compose.distributed.yml"',
                     "COMPOSE_FILE='docker-compose.distributed.yml'",
                     "export COMPOSE_FILE=docker-compose.distributed.yml",
                     "COMPOSE_FILE=docker-compose.distributed.yml # recorded by ./bin/install"):
            with self.subTest(line=line):
                self.assertEqual(self.install(line + "\n").mode, "distributed")

    def test_last_definition_wins(self):
        inst = self.install("COMPOSE_FILE=docker-compose.yml\nCOMPOSE_FILE=docker-compose.distributed.yml\n")
        self.assertEqual(inst.mode, "distributed")

    def test_commented_out_compose_file_is_ignored(self):
        self.assertEqual(self.install("# COMPOSE_FILE=docker-compose.distributed.yml\n").mode, "standalone")

    def test_custom_separator_from_env_file(self):
        inst = self.install("COMPOSE_PATH_SEPARATOR=;\n"
                            "COMPOSE_FILE=docker-compose.distributed.yml;docker-compose.override.yml\n")
        self.assertEqual(inst.mode, "distributed")

    def test_custom_separator_from_shell_when_env_file_has_none(self):
        inst = self.install("COMPOSE_FILE=docker-compose.override.yml,docker-compose.distributed.yml\n",
                            environ={"COMPOSE_PATH_SEPARATOR": ","})
        self.assertEqual(inst.mode, "distributed")

    def test_shell_separator_wins_over_env_file_like_compose(self):
        # docker-compose (v2/v5) reads COMPOSE_PATH_SEPARATOR from the shell
        # before .env: with these values it loads the distributed stack plus
        # the override. bin/update must split COMPOSE_FILE the same way.
        try:
            inst = self.install("COMPOSE_PATH_SEPARATOR=;\n"
                                "COMPOSE_FILE=docker-compose.distributed.yml,docker-compose.override.yml\n",
                                environ={"COMPOSE_PATH_SEPARATOR": ","})
        except InstallError as exc:
            self.fail(f"split COMPOSE_FILE with .env's separator instead of the shell's: {exc}")
        self.assertEqual(inst.mode, "distributed")

    def test_wrong_separator_does_not_split(self):
        with self.assertRaises(InstallError) as caught:
            self.install("COMPOSE_FILE=docker-compose.distributed.yml;docker-compose.override.yml\n")
        self.assertIn("names neither docker-compose.yml nor docker-compose.distributed.yml", str(caught.exception))

    def test_both_stacks_is_an_error(self):
        for value in ("docker-compose.yml:docker-compose.distributed.yml",
                      "docker-compose.distributed.yml:override.yml:docker-compose.yml"):
            with self.subTest(value=value):
                with self.assertRaises(InstallError) as caught:
                    self.install(f"COMPOSE_FILE={value}\n")
                self.assertEqual(str(caught.exception),
                                 "COMPOSE_FILE in .env lists both docker-compose.yml and docker-compose.distributed.yml")

    def test_neither_stack_is_an_error(self):
        for value in ("other.yml", "docker-compose.override.yml", "my-docker-compose.yml",
                      "docker-compose.yml.bak", "docker-compose.distributed.yaml"):
            with self.subTest(value=value):
                with self.assertRaises(InstallError) as caught:
                    self.install(f"COMPOSE_FILE={value}\n")
                self.assertEqual(
                    str(caught.exception),
                    f"COMPOSE_FILE={value} in .env names neither docker-compose.yml nor docker-compose.distributed.yml")

    def test_no_env_file_is_an_error(self):
        with self.assertRaises(InstallError) as caught:
            Installation(self.root, environ={}, runner=Runner(), compose=COMPOSE)
        self.assertEqual(str(caught.exception),
                         f"no .env in {self.root}: run bin/update from the checkout ./bin/install set up")

    def test_dangling_env_symlink_is_no_env(self):
        (self.root / ".env").symlink_to(self.root / "missing.env")
        with self.assertRaises(InstallError) as caught:
            Installation(self.root, environ={}, runner=Runner(), compose=COMPOSE)
        self.assertIn("no .env in", str(caught.exception))

    def test_symlinked_env_file_is_followed(self):
        target = self.write("elsewhere/real.env", "COMPOSE_FILE=docker-compose.distributed.yml\nFRONTEND_VERSION=v1.0.0\n")
        (self.root / ".env").symlink_to(target)
        inst = Installation(self.root, environ={}, runner=Runner(), compose=COMPOSE)
        self.assertEqual(inst.mode, "distributed")
        self.assertEqual(inst.env["FRONTEND_VERSION"], "v1.0.0")

    def test_env_values_are_read(self):
        inst = self.install("FUTURE_AGI_VERSION=v1.2.3\nSECRET_KEY='abc def'\n")
        self.assertEqual(inst.env, {"FUTURE_AGI_VERSION": "v1.2.3", "SECRET_KEY": "abc def"})

    def test_root_is_resolved(self):
        self.write(".env", "")
        link = self.root.parent / (self.root.name + "-link")
        link.symlink_to(self.root)
        self.addCleanup(link.unlink)
        inst = Installation(link, environ={}, runner=Runner(), compose=COMPOSE)
        self.assertEqual(inst.root, self.root)

    def test_env_file_option_rejected_outside_production(self):
        self.write("other.env", "")
        with self.assertRaises(InstallError) as caught:
            self.install("", env_file=self.root / "other.env")
        self.assertIn("--env-file is only for production installs (--production)", str(caught.exception))
        self.assertIn("always use the checkout's .env", str(caught.exception))

    def test_env_file_option_rejected_even_when_missing(self):
        with self.assertRaises(InstallError) as caught:
            self.install("", env_file=self.root / "nope.env")
        self.assertIn("--env-file is only for production installs", str(caught.exception))

    def test_env_file_option_naming_the_checkout_env_is_accepted(self):
        inst = self.install("", env_file=self.root / ".env")
        self.assertEqual(inst.mode, "standalone")
        self.assertEqual(inst.env_file, self.root / ".env")

    def test_relative_returns_paths_inside_root_relative(self):
        inst = self.install("")
        self.assertEqual(inst.relative(inst.env_file), ".env")
        self.assertEqual(inst.relative(self.root / "a" / "b.txt"), "a/b.txt")
        outside = self.root.parent / "outside.env"
        self.assertEqual(inst.relative(outside), str(outside))


class ShellOverrideTests(TempRootTest):
    def test_shell_compose_file_without_one_in_env(self):
        with self.assertRaises(InstallError) as caught:
            self.install("", environ={"COMPOSE_FILE": "docker-compose.distributed.yml"})
        message = str(caught.exception)
        self.assertIn("COMPOSE_FILE=docker-compose.distributed.yml is set in your shell", message)
        self.assertIn("(no COMPOSE_FILE)", message)
        self.assertIn("unset COMPOSE_FILE", message)

    def test_shell_compose_file_different_from_env(self):
        with self.assertRaises(InstallError) as caught:
            self.install("COMPOSE_FILE=docker-compose.distributed.yml\n",
                         environ={"COMPOSE_FILE": "docker-compose.yml"})
        message = str(caught.exception)
        self.assertIn("COMPOSE_FILE=docker-compose.yml is set in your shell", message)
        self.assertIn("(COMPOSE_FILE=docker-compose.distributed.yml)", message)
        self.assertIn("Run `unset COMPOSE_FILE`", message)

    def test_shell_compose_file_selecting_same_stack_but_different_text(self):
        with self.assertRaises(InstallError) as caught:
            self.install("COMPOSE_FILE=docker-compose.distributed.yml\n",
                         environ={"COMPOSE_FILE": "./docker-compose.distributed.yml"})
        self.assertIn("unset COMPOSE_FILE", str(caught.exception))

    def test_shell_compose_file_equal_to_env_is_accepted(self):
        inst = self.install("COMPOSE_FILE=docker-compose.distributed.yml\n",
                            environ={"COMPOSE_FILE": "docker-compose.distributed.yml"})
        self.assertEqual(inst.mode, "distributed")

    def test_empty_shell_compose_file_with_none_recorded_is_accepted(self):
        self.assertEqual(self.install("", environ={"COMPOSE_FILE": ""}).mode, "standalone")

    def test_shell_compose_file_checked_before_mode_errors(self):
        with self.assertRaises(InstallError) as caught:
            self.install("COMPOSE_FILE=docker-compose.yml:docker-compose.distributed.yml\n",
                         environ={"COMPOSE_FILE": "x.yml"})
        self.assertIn("is set in your shell", str(caught.exception))

    def test_every_exported_version_variable_is_refused(self):
        for key in VERSION_VARIABLES:
            with self.subTest(key=key):
                with self.assertRaises(InstallError) as caught:
                    self.install("", environ={key: "v1.0.0"})
                self.assertEqual(
                    str(caught.exception),
                    f"{key} is exported in your shell and would override .env for docker compose. "
                    "Unset them and try again")

    def test_exported_version_variable_refused_even_when_equal_to_env(self):
        with self.assertRaises(InstallError) as caught:
            self.install("FUTURE_AGI_VERSION=v1.2.3\n", environ={"FUTURE_AGI_VERSION": "v1.2.3"})
        self.assertIn("FUTURE_AGI_VERSION is exported in your shell", str(caught.exception))

    def test_exported_empty_version_variable_is_refused(self):
        with self.assertRaises(InstallError):
            self.install("", environ={"SERVING_VERSION": ""})

    def test_several_exported_version_variables_are_listed(self):
        with self.assertRaises(InstallError) as caught:
            self.install("", environ={"SERVING_VERSION": "v1", "FRONTEND_VERSION": "v1", "PATH": "/bin"})
        self.assertEqual(
            str(caught.exception),
            "FRONTEND_VERSION, SERVING_VERSION are exported in your shell and would override .env for docker "
            "compose. Unset them and try again")

    def test_other_exports_are_fine(self):
        inst = self.install("", environ={"SECRET_KEY": "shell", "FUTURE_AGI_VERSION_X": "1", "DOCKER_HOST": "unix:///x"})
        self.assertEqual(inst.mode, "standalone")

    def test_missing_env_reported_before_exports(self):
        with self.assertRaises(InstallError) as caught:
            Installation(self.root, environ={"FUTURE_AGI_VERSION": "v1"}, runner=Runner(), compose=COMPOSE)
        self.assertIn("no .env in", str(caught.exception))

    def test_environ_defaults_to_process_environment(self):
        self.write(".env", "")
        with mock.patch.dict(os.environ, {"FRONTEND_VERSION": "v9.9.9"}):
            with self.assertRaises(InstallError) as caught:
                Installation(self.root, runner=Runner(), compose=COMPOSE)
        self.assertIn("FRONTEND_VERSION is exported", str(caught.exception))

    def assert_project_remedies(self, message, env_name, project):
        self.assertIn("If this checkout really belongs to the exported project", message)
        self.assertIn(f"persist COMPOSE_PROJECT_NAME={project} in {env_name}", message)
        self.assertIn("If this is an unrelated shell export, run `unset COMPOSE_PROJECT_NAME`", message)
        self.assertIn("Unsetting it can select a different project", message)
        self.assertIn("verify the resulting Compose project belongs to this checkout before applying", message)

    def test_shell_project_name_without_one_in_env_is_refused(self):
        for env_text, recorded in (("", "no COMPOSE_PROJECT_NAME"),
                                   ("COMPOSE_PROJECT_NAME=\n", "COMPOSE_PROJECT_NAME is empty")):
            with self.subTest(env_text=env_text):
                with self.assertRaises(InstallError) as caught:
                    self.install(env_text, environ={"COMPOSE_PROJECT_NAME": "other-install"})
                message = str(caught.exception)
                self.assertIn(f"instead of .env ({recorded})", message)
                self.assertIn("restart project other-install", message)
                self.assert_project_remedies(message, ".env", "other-install")

    def test_shell_project_name_different_from_env_is_refused(self):
        for env_text in ("COMPOSE_PROJECT_NAME=mine\n",
                         "COMPOSE_FILE=docker-compose.distributed.yml\nCOMPOSE_PROJECT_NAME=mine\n"):
            with self.subTest(env_text=env_text):
                with self.assertRaises(InstallError) as caught:
                    self.install(env_text, environ={"COMPOSE_PROJECT_NAME": "other-install"})
                self.assertIn("instead of .env (COMPOSE_PROJECT_NAME=mine)", str(caught.exception))
                self.assertIn("restart project other-install", str(caught.exception))
                self.assert_project_remedies(str(caught.exception), ".env", "other-install")

    def test_empty_shell_project_name_against_a_recorded_one_is_refused(self):
        # Compose then falls back to the compose file's name: or the directory, not a project named '(empty)'.
        with self.assertRaises(InstallError) as caught:
            self.install("COMPOSE_PROJECT_NAME=mine\n", environ={"COMPOSE_PROJECT_NAME": ""})
        message = str(caught.exception)
        self.assertIn("COMPOSE_PROJECT_NAME is set but empty in your shell", message)
        self.assertIn("ignores .env's COMPOSE_PROJECT_NAME=mine", message)
        self.assertIn("compose file or the directory", message)
        self.assertIn("persist its resolved, nonempty COMPOSE_PROJECT_NAME in .env", message)
        self.assertIn("unset the empty shell export", message)
        self.assertIn("If this is an unrelated shell export, run `unset COMPOSE_PROJECT_NAME`", message)
        self.assertIn("verify the resulting Compose project belongs to this checkout before applying", message)
        self.assertNotIn("(empty)", message)

    def test_shell_project_name_equal_to_env_is_accepted(self):
        inst = self.install("COMPOSE_PROJECT_NAME=mine\n", environ={"COMPOSE_PROJECT_NAME": "mine"})
        self.assertEqual(inst.mode, "standalone")

    def test_empty_shell_project_name_with_none_recorded_is_accepted(self):
        for env_text in ("", "COMPOSE_PROJECT_NAME=\n"):
            with self.subTest(env_text=env_text):
                self.assertEqual(self.install(env_text, environ={"COMPOSE_PROJECT_NAME": ""}).mode, "standalone")

    def test_shell_compose_env_files_is_refused(self):
        for value in ("other.env", "other.env,more.env", " ", "\t"):
            with self.subTest(value=value):
                runner = Runner()
                with self.assertRaises(InstallError) as caught:
                    self.install("FUTURE_AGI_VERSION=v1.0.0\n", environ={"COMPOSE_ENV_FILES": value}, runner=runner)
                self.assertEqual(
                    str(caught.exception),
                    f"COMPOSE_ENV_FILES={value} is set in your shell, so Docker Compose would read those files "
                    "instead of .env, the file bin/update updates. Run `unset COMPOSE_ENV_FILES` and try again")
                self.assertEqual(runner.calls, [])
                self.assertEqual((self.root / ".env").read_bytes(), b"FUTURE_AGI_VERSION=v1.0.0\n")

    def test_empty_shell_compose_env_files_is_accepted_like_unset(self):
        for mode, compose_file in (("standalone", STANDALONE_FILE), ("distributed", DISTRIBUTED_FILE)):
            with self.subTest(mode=mode):
                model = {"name": "mine", "services": {"app": {"image": "futureagi/standalone:v1.0.0"}}}
                env_text = f"COMPOSE_FILE={compose_file}\nCOMPOSE_PROJECT_NAME=mine\nFUTURE_AGI_VERSION=v1.0.0\n"
                unset = self.install(env_text, runner=Runner(docker_handler(config=model)))
                empty = self.install(None, environ={"COMPOSE_ENV_FILES": ""}, runner=Runner(docker_handler(config=model)))
                self.assertEqual(empty.mode, mode)
                self.assertEqual(empty.config(), unset.config())
                self.assertEqual(empty.config(), model)
                self.assertEqual(empty.subprocess_env(), {"COMPOSE_ENV_FILES": ""})
                self.assertEqual((self.root / ".env").read_bytes(), env_text.encode())

    def test_empty_compose_env_files_does_not_bypass_other_shell_guards(self):
        cases = [(key, "v1.0.0", f"{key} is exported") for key in VERSION_VARIABLES]
        cases += [("COMPOSE_PROJECT_NAME", "other-install", "restart project other-install")]
        cases += [("COMPOSE_DISABLE_ENV_FILE", value, "unset COMPOSE_DISABLE_ENV_FILE")
                  for value in ("1", "true", "0", "")]
        for key, value, reason in cases:
            with self.subTest(key=key, value=value):
                runner = Runner()
                with self.assertRaises(InstallError) as caught:
                    self.install("FUTURE_AGI_VERSION=v1.0.0\n", runner=runner,
                                 environ={"COMPOSE_ENV_FILES": "", key: value})
                self.assertIn(reason, str(caught.exception))
                self.assertEqual(runner.calls, [])
                self.assertEqual((self.root / ".env").read_bytes(), b"FUTURE_AGI_VERSION=v1.0.0\n")

    def test_empty_compose_env_files_does_not_bypass_refused_compose_version(self):
        runner = Runner()
        with self.assertRaises(InstallError) as caught:
            self.install("", runner=runner, environ={"COMPOSE_ENV_FILES": ""}, compose_version=(2, 25, 0))
        self.assertIn("Docker Compose v2.25.0 cannot print its configuration as JSON", str(caught.exception))
        self.assertEqual(runner.calls, [])

    def test_shell_compose_disable_env_file_is_refused(self):
        # A true value (1, true, ...) makes Compose skip .env: bin/update would write tags nothing reads, and stop
        # only after the backup, at the config check. Any value is refused, before anything runs.
        for value in ("1", "true", "0", ""):
            with self.subTest(value=value):
                runner = Runner()
                with self.assertRaises(InstallError) as caught:
                    self.install("FUTURE_AGI_VERSION=v1.0.0\n", environ={"COMPOSE_DISABLE_ENV_FILE": value},
                                 runner=runner)
                self.assertEqual(
                    str(caught.exception),
                    f"COMPOSE_DISABLE_ENV_FILE={value} is set in your shell, and Docker Compose skips .env, the file "
                    "bin/update updates, when it is true. Run `unset COMPOSE_DISABLE_ENV_FILE` and try again")
                self.assertEqual(runner.calls, [])

    def test_production_lets_its_env_file_win_over_compose_disable_env_file(self):
        # Compose reads an --env-file it is given whatever COMPOSE_DISABLE_ENV_FILE says.
        self.production_files()
        inst = Installation(self.root, production=True, runner=Runner(), compose=COMPOSE,
                            environ={"COMPOSE_DISABLE_ENV_FILE": "1"})
        self.assertEqual(inst.compose_args("config")[:4], ["docker", "compose", "--env-file", str(inst.env_file)])


class CliPreflightRefusalTests(TempRootTest):
    """Real CLI construction must stop before state/recovery, backup, fetch or any subprocess."""

    def snapshot(self):
        return {str(path.relative_to(self.root)): (path.stat().st_mode, path.stat().st_mtime_ns,
                                                  path.stat().st_ino, path.read_bytes() if path.is_file() else None)
                for path in self.root.rglob("*")}

    def refusal(self, env_name, env_text, environ, *, production=False):
        self.write(DISTRIBUTED_FILE, "services: {}\n")
        self.write(PRODUCTION_OVERLAY, "services: {}\n")
        self.write(env_name, '# keep this comment\r\nFUTURE_AGI_VERSION="v1.0.0" # pinned\r\n' + env_text)
        # Even an interrupted update must not be recovered before an ambiguous install is refused.
        self.write(f"{STATE_DIR}/journal.json", '{"stage": "rewritten", "sentinel": "do not recover"}\n')
        before = self.snapshot()
        flags = ["--production", "--env-file", str(self.root / env_name)] if production else []
        messages = []
        for command in (("check",), ("apply", "--allow-no-backup"), ("rollback", "--yes")):
            with self.subTest(command=command):
                runner = Runner()
                stdout, stderr = io.StringIO(), io.StringIO()
                with contextlib.ExitStack() as stack:
                    stack.enter_context(mock.patch.dict(os.environ, environ, clear=True))
                    stack.enter_context(mock.patch.object(cli, "Installation",
                                                          side_effect=functools.partial(Installation, runner=runner)))
                    resolve = stack.enter_context(mock.patch.object(
                        install, "resolve_compose_command", side_effect=AssertionError("preflight reached Compose")))
                    state = stack.enter_context(mock.patch.object(cli, "StateDir"))
                    updater = stack.enter_context(mock.patch.object(cli, "Updater"))
                    fetch = stack.enter_context(mock.patch.object(cli, "fetch_manifest"))
                    stack.enter_context(contextlib.redirect_stdout(stdout))
                    stack.enter_context(contextlib.redirect_stderr(stderr))
                    code = cli.main(["--root", str(self.root), *flags, *command])
                self.assertEqual(code, 2, stderr.getvalue())
                self.assertEqual(stdout.getvalue(), "")
                self.assertEqual(self.snapshot(), before)
                self.assertEqual(runner.calls, [])
                for operation in (resolve, state, updater, fetch):
                    operation.assert_not_called()
                messages.append(stderr.getvalue())
        return messages

    def test_project_refusal_gives_both_remedies_without_mutating_any_install(self):
        for production, env_name, env_text in (
                (False, ".env", ""),
                (False, ".env", "COMPOSE_FILE=docker-compose.distributed.yml\n"),
                (False, ".env", "COMPOSE_PROJECT_NAME=\n"),
                (False, ".env", "COMPOSE_PROJECT_NAME=mine\n"),
                (True, PRODUCTION_ENV, ""),
                (True, "secrets/prod custom.env", "")):
            with self.subTest(production=production, env_name=env_name, env_text=env_text):
                for message in self.refusal(env_name, env_text,
                                            {"COMPOSE_PROJECT_NAME": "fagi-b", "COMPOSE_ENV_FILES": ""},
                                            production=production):
                    ShellOverrideTests.assert_project_remedies(self, message, env_name, "fagi-b")

    def test_empty_project_refusal_never_guesses_or_writes_a_fallback_name(self):
        for message in self.refusal(".env", "COMPOSE_PROJECT_NAME=mine\n", {"COMPOSE_PROJECT_NAME": ""}):
            self.assertIn("compose file or the directory", message)
            self.assertIn("persist its resolved, nonempty COMPOSE_PROJECT_NAME in .env", message)
            self.assertIn("verify the resulting Compose project belongs to this checkout before applying", message)
            self.assertNotIn("(empty)", message)

    def test_nonempty_env_files_refusal_does_not_mutate_or_recover(self):
        for value in ("other.env", " ", "\t"):
            with self.subTest(value=value):
                for message in self.refusal(".env", "", {"COMPOSE_ENV_FILES": value}):
                    self.assertIn("unset COMPOSE_ENV_FILES", message)


@unittest.skipUnless(os.environ.get("FUTURE_AGI_UPDATE_COMPOSE_PREFLIGHT"),
                     "set FUTURE_AGI_UPDATE_COMPOSE_PREFLIGHT to a Compose binary for daemon-free preflight tests")
class ComposePreflightIntegrationTests(TempRootTest):
    """Small opt-in regressions, not the full release matrix: only real `compose config` is executed."""

    def setUp(self):
        super().setUp()
        self.binary = str(Path(os.environ["FUTURE_AGI_UPDATE_COMPOSE_PREFLIGHT"]).expanduser().resolve())
        home = self.root / "home"
        home.mkdir()
        self.environ = {"HOME": str(home), "PATH": os.defpath}
        if "DOCKER_CONTEXT" in os.environ:
            self.environ["DOCKER_CONTEXT"] = os.environ["DOCKER_CONTEXT"]
            # Older Compose initializes the named context even for config. Read its client metadata without
            # changing the global config or contacting the engine; all Compose selection variables stay absent.
            self.environ["DOCKER_CONFIG"] = os.environ.get("DOCKER_CONFIG", str(Path.home() / ".docker"))
        for relative, service, image in ((STANDALONE_FILE, "app", "standalone"),
                                         (DISTRIBUTED_FILE, "backend", "future-agi")):
            self.write(relative, f"name: futureagi\nservices:\n  {service}:\n"
                                 f"    image: futureagi/{image}:${{FUTURE_AGI_VERSION:-latest}}\n")
        self.write(PRODUCTION_OVERLAY, "services: {}\n")

    def fixture(self, mode, project="fromdotenv"):
        self.production = mode.startswith("production")
        self.service = "app" if mode == "standalone" else "backend"
        self.env_file = self.root / {"production": PRODUCTION_ENV,
                                     "production-custom": "secrets/prod custom.env"}.get(mode, ".env")
        text = "FUTURE_AGI_VERSION=v1.0.0\n"
        if mode == "distributed":
            text += f"COMPOSE_FILE={DISTRIBUTED_FILE}\n"
        if project is not None:
            text += f"COMPOSE_PROJECT_NAME={project}\n"
        if self.production:
            self.write(".env", "COMPOSE_PROJECT_NAME=ignored-dotenv\nFUTURE_AGI_VERSION=v0.0.0\n")
        self.write(str(self.env_file.relative_to(self.root)), text)

    def args(self):
        if self.production:
            return [self.binary, "--env-file", str(self.env_file), "-f", str(self.root / DISTRIBUTED_FILE),
                    "-f", str(self.root / PRODUCTION_OVERLAY)]
        return [self.binary]

    def run_compose(self, args, **kwargs):
        self.assertEqual(args[0], self.binary)
        self.assertIn(install.split_command(args)[1], (["config", "--format", "json"], ["config", "--services"]))
        self.assertEqual(kwargs["cwd"], str(self.root))
        return subprocess.run(args, timeout=60, **kwargs)

    def plain(self, exports):
        return self.run_compose([*self.args(), "config", "--format", "json"], cwd=str(self.root),
                                env=dict(self.environ, **exports), text=True, capture_output=True, check=False)

    def plain_config(self, exports):
        result = self.plain(exports)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def installation(self, exports, runner=None):
        return Installation(self.root, production=self.production, env_file=self.env_file if self.production else None,
                            environ=dict(self.environ, **exports), compose=[self.binary],
                            runner=self.run_compose if runner is None else runner)

    def assert_model(self, model, project, tag):
        self.assertEqual(model["name"], project)
        repository = "standalone" if self.service == "app" else "future-agi"
        self.assertEqual(model["services"][self.service]["image"], f"futureagi/{repository}:{tag}")

    def test_empty_and_unset_env_files_read_the_same_dotenv_in_both_stacks(self):
        for mode in ("standalone", "distributed"):
            with self.subTest(mode=mode):
                self.fixture(mode)
                unset = self.plain_config({})
                self.assertEqual(self.plain_config({"COMPOSE_ENV_FILES": ""}), unset)
                self.assert_model(unset, "fromdotenv", "v1.0.0")
                empty = self.installation({"COMPOSE_ENV_FILES": ""})
                self.assertEqual(empty.config(), unset)
                self.assertEqual(self.installation({}).config(), unset)
                # An empty export must keep reading the file after an update changes the image tag.
                self.env_file.write_text(self.env_file.read_text().replace("v1.0.0", "v1.1.0"), encoding="utf-8")
                self.assert_model(empty.config(refresh=True), "fromdotenv", "v1.1.0")
                self.assertEqual(empty.config(), self.installation({}).config())

    def test_nonempty_env_files_replace_dotenv_and_whitespace_is_not_empty(self):
        for mode in ("standalone", "distributed"):
            with self.subTest(mode=mode):
                self.fixture(mode)
                other = self.write("other.env", self.env_file.read_text().replace("fromdotenv", "fromother")
                                   .replace("v1.0.0", "v2.0.0"))
                self.assert_model(self.plain_config({"COMPOSE_ENV_FILES": str(other)}), "fromother", "v2.0.0")
                for value in (str(other), " ", "\t"):
                    with self.subTest(value=value):
                        if value != str(other):
                            result = self.plain({"COMPOSE_ENV_FILES": value})
                            self.assertNotEqual(result.returncode, 0)
                            self.assertIn("env file", result.stderr.lower())
                        before = self.env_file.read_bytes()
                        runner = Runner()
                        with self.assertRaisesRegex(InstallError, "unset COMPOSE_ENV_FILES"):
                            self.installation({"COMPOSE_ENV_FILES": value}, runner=runner)
                        self.assertEqual(runner.calls, [])
                        self.assertEqual(self.env_file.read_bytes(), before)

    def test_production_explicit_env_file_wins_over_env_file_controls(self):
        other = self.write("other.env", "COMPOSE_PROJECT_NAME=fromother\nFUTURE_AGI_VERSION=v2.0.0\n")
        for mode in ("production", "production-custom"):
            for value in (None, "", str(other), " ", "\t"):
                for disable in (None, "true", "", "invalid"):
                    with self.subTest(mode=mode, env_files=value, disable=disable):
                        self.fixture(mode, project="production-owner")
                        exports = {} if value is None else {"COMPOSE_ENV_FILES": value}
                        if disable is not None:
                            exports["COMPOSE_DISABLE_ENV_FILE"] = disable
                        expected = self.plain_config(exports)
                        self.assert_model(expected, "production-owner", "v1.0.0")
                        inst = self.installation(dict(exports, COMPOSE_PROJECT_NAME="unrelated",
                                                      FUTURE_AGI_VERSION="v9.9.9"))
                        self.assertEqual(inst.compose_args(), self.args())
                        self.assertNotIn("COMPOSE_ENV_FILES", inst.subprocess_env())
                        self.assertEqual(inst.config(), expected)

    def test_persisting_the_exported_project_keeps_the_install_in_every_shell(self):
        for mode in ("standalone", "distributed", "production", "production-custom"):
            with self.subTest(mode=mode):
                self.fixture(mode, project=None)
                self.assert_model(self.plain_config({"COMPOSE_PROJECT_NAME": "fagi-b"}), "fagi-b", "v1.0.0")
                # Unsetting alone selects the default project, which could belong to a different checkout.
                self.assert_model(self.plain_config({}), "futureagi", "v1.0.0")
                before = self.env_file.read_bytes()
                runner = Runner()
                with self.assertRaises(InstallError) as caught:
                    self.installation({"COMPOSE_PROJECT_NAME": "fagi-b"}, runner=runner)
                ShellOverrideTests.assert_project_remedies(self, str(caught.exception),
                                                          str(self.env_file.relative_to(self.root)), "fagi-b")
                self.assertEqual(runner.calls, [])
                self.assertEqual(self.env_file.read_bytes(), before)
                self.assertFalse((self.root / STATE_DIR).exists())
                # This is the operator's explicit choice, never an automatic preflight write.
                self.env_file.write_bytes(before + b"COMPOSE_PROJECT_NAME=fagi-b\n")
                for exports in ({}, {"COMPOSE_PROJECT_NAME": "fagi-b"}):
                    self.assert_model(self.installation(exports).config(), "fagi-b", "v1.0.0")

    def test_empty_project_names_follow_compose_fallback_not_a_literal_empty_name(self):
        for mode in ("standalone", "distributed", "production", "production-custom"):
            with self.subTest(mode=mode):
                self.fixture(mode, project="recorded")
                self.assert_model(self.plain_config({"COMPOSE_PROJECT_NAME": ""}), "futureagi", "v1.0.0")
                if self.production:
                    # Production drops the export because its explicit env file records the name.
                    self.assert_model(self.installation({"COMPOSE_PROJECT_NAME": ""}).config(), "recorded", "v1.0.0")
                else:
                    with self.assertRaisesRegex(InstallError, "compose file or the directory"):
                        self.installation({"COMPOSE_PROJECT_NAME": ""}, runner=Runner())
                for project in (None, ""):
                    self.fixture(mode, project=project)
                    for exports in ({}, {"COMPOSE_PROJECT_NAME": ""}):
                        self.assert_model(self.installation(exports).config(), "futureagi", "v1.0.0")
                if self.production:
                    self.assert_model(self.installation({"COMPOSE_PROJECT_NAME": "fagi-b"}).config(),
                                      "futureagi", "v1.0.0")


# ========================================================= production ====

class ProductionTests(TempRootTest):
    def test_production_mode_with_default_env_file(self):
        self.production_files()
        inst = Installation(self.root, production=True, environ={}, runner=Runner(), compose=COMPOSE)
        self.assertEqual(inst.mode, "production")
        self.assertEqual(inst.label, "Production (Distributed)")
        self.assertTrue(inst.production)
        self.assertEqual(inst.env_file, self.root / PRODUCTION_ENV)
        self.assertEqual(inst.relative(inst.env_file), "deploy/.env.production")
        self.assertEqual(inst.env, {"FUTURE_AGI_VERSION": "v1.0.0"})

    def test_production_needs_no_checkout_env(self):
        self.production_files()
        self.assertFalse((self.root / ".env").exists())
        Installation(self.root, production=True, environ={}, runner=Runner(), compose=COMPOSE)

    def test_production_without_env_file(self):
        self.write(DISTRIBUTED_FILE, "")
        self.write(PRODUCTION_OVERLAY, "")
        with self.assertRaises(InstallError) as caught:
            Installation(self.root, production=True, environ={}, runner=Runner(), compose=COMPOSE)
        self.assertEqual(
            str(caught.exception),
            f"{self.root / PRODUCTION_ENV} does not exist. Production installs are configured by deploy/setup.sh; "
            "pass --env-file if yours lives elsewhere")

    def test_production_with_custom_env_file(self):
        self.write(DISTRIBUTED_FILE, "")
        self.write(PRODUCTION_OVERLAY, "")
        custom = self.write("secrets/prod.env", "FRONTEND_VERSION=v2.0.0\n")
        inst = Installation(self.root, production=True, env_file=custom, environ={}, runner=Runner(), compose=COMPOSE)
        self.assertEqual(inst.env_file, custom)
        self.assertEqual(inst.env, {"FRONTEND_VERSION": "v2.0.0"})
        self.assertEqual(inst.relative(inst.env_file), "secrets/prod.env")

    def test_production_custom_env_file_outside_checkout(self):
        self.production_files()
        with tempfile.TemporaryDirectory() as other:
            custom = Path(other).resolve() / "prod.env"
            custom.write_text("SERVING_VERSION=v1.0.0\n")
            inst = Installation(self.root, production=True, env_file=custom, environ={}, runner=Runner(),
                                compose=COMPOSE)
            self.assertEqual(inst.relative(inst.env_file), str(custom))

    def test_production_custom_env_file_missing(self):
        self.production_files()
        with self.assertRaises(InstallError) as caught:
            Installation(self.root, production=True, env_file=self.root / "nope.env", environ={}, runner=Runner(),
                         compose=COMPOSE)
        self.assertIn(f"{self.root / 'nope.env'} does not exist", str(caught.exception))

    def test_production_needs_both_compose_files(self):
        for missing in (DISTRIBUTED_FILE, PRODUCTION_OVERLAY):
            with self.subTest(missing=missing):
                self.production_files()
                (self.root / missing).unlink()
                with self.assertRaises(InstallError) as caught:
                    Installation(self.root, production=True, environ={}, runner=Runner(), compose=COMPOSE)
                self.assertEqual(str(caught.exception),
                                 f"{missing} is missing from {self.root}: is this a complete checkout?")

    def test_production_ignores_shell_compose_file_and_exported_versions(self):
        self.production_files()
        inst = Installation(self.root, production=True, runner=Runner(), compose=COMPOSE,
                            environ={"COMPOSE_FILE": "docker-compose.yml", "FUTURE_AGI_VERSION": "v0.0.1",
                                     "FRONTEND_VERSION": "v0.0.1"})
        self.assertEqual(inst.mode, "production")

    def test_production_shell_project_name_its_env_file_lacks_is_refused(self):
        self.production_files()
        with self.assertRaises(InstallError) as caught:
            Installation(self.root, production=True, runner=Runner(), compose=COMPOSE,
                         environ={"COMPOSE_PROJECT_NAME": "other-install"})
        self.assertIn("COMPOSE_PROJECT_NAME=other-install is set in your shell, and Docker Compose uses it instead "
                      "of deploy/.env.production (no COMPOSE_PROJECT_NAME)", str(caught.exception))

    def test_production_shell_project_name_its_env_file_sets_is_dropped(self):
        self.production_files("FUTURE_AGI_VERSION=v1.0.0\nCOMPOSE_PROJECT_NAME=prod\n")
        inst = Installation(self.root, production=True, runner=Runner(), compose=COMPOSE,
                            environ={"COMPOSE_PROJECT_NAME": "other-install", "COMPOSE_ENV_FILES": "other.env"})
        self.assertNotIn("COMPOSE_PROJECT_NAME", inst.subprocess_env())
        self.assertNotIn("COMPOSE_ENV_FILES", inst.subprocess_env())

    def test_production_ignores_checkout_env_compose_file(self):
        self.production_files()
        self.write(".env", "COMPOSE_FILE=docker-compose.yml:docker-compose.distributed.yml\n")
        inst = Installation(self.root, production=True, environ={}, runner=Runner(), compose=COMPOSE)
        self.assertEqual(inst.mode, "production")


# ===================================================== subprocess env ====

class SubprocessEnvTests(TempRootTest):
    def test_production_drops_secrets_env_file_keys_versions_and_compose_file(self):
        self.production_files("FUTURE_AGI_VERSION=v1.0.0\nONLY_IN_FILE=file\nPG_PASSWORD=from-file\n"
                              "# COMMENTED_OUT=1\nexport EXPORTED_IN_FILE=1\nCOMPOSE_PROJECT_NAME=fileproj\n")
        environ = {name: "shell-" + name for name in SETUP_SECRET_NAMES}
        environ.update({name: "v0.0.1" for name in VERSION_VARIABLES})
        environ.update({"COMPOSE_FILE": "docker-compose.yml", "ONLY_IN_FILE": "shell", "EXPORTED_IN_FILE": "shell",
                        "COMMENTED_OUT": "shell", "PATH": "/usr/bin", "HOME": "/home/u",
                        "DOCKER_HOST": "unix:///run/docker.sock", "COMPOSE_PROJECT_NAME": "shellproj",
                        "COMPOSE_ENV_FILES": "other.env"})
        inst = Installation(self.root, production=True, environ=environ, runner=Runner(), compose=COMPOSE)
        env = inst.subprocess_env()
        for key in SETUP_SECRET_NAMES + VERSION_VARIABLES + ("COMPOSE_FILE", "ONLY_IN_FILE", "EXPORTED_IN_FILE",
                                                              "COMPOSE_PROJECT_NAME", "COMPOSE_ENV_FILES"):
            with self.subTest(dropped=key):
                self.assertNotIn(key, env)
        self.assertEqual(env, {"COMMENTED_OUT": "shell", "PATH": "/usr/bin", "HOME": "/home/u",
                               "DOCKER_HOST": "unix:///run/docker.sock"})
        # The installation's own view of the shell is untouched.
        self.assertEqual(inst.environ, environ)

    def test_production_drops_keys_added_to_env_file_later(self):
        self.production_files("FUTURE_AGI_VERSION=v1.0.0\n")
        inst = Installation(self.root, production=True, environ={"LATE": "shell", "KEEP": "1"}, runner=Runner(),
                            compose=COMPOSE)
        self.assertIn("LATE", inst.subprocess_env())
        with open(inst.env_file, "a") as handle:
            handle.write("LATE=file\n")
        self.assertEqual(inst.subprocess_env(), {"KEEP": "1"})

    def test_non_production_passes_environment_through(self):
        environ = {"PATH": "/usr/bin", "SECRET_KEY": "shell", "COMPOSE_FILE": "docker-compose.yml",
                   "COMPOSE_PROFILES": "gpu", "DOCKER_CONTEXT": "remote"}
        inst = self.install("COMPOSE_FILE=docker-compose.yml\nSECRET_KEY=file\n", environ=environ)
        env = inst.subprocess_env()
        self.assertEqual(env, environ)
        env["MUTATED"] = "1"
        self.assertNotIn("MUTATED", inst.subprocess_env())

    def test_distributed_passes_environment_through(self):
        environ = {"PG_PASSWORD": "shell", "PATH": "/bin"}
        inst = self.install("COMPOSE_FILE=docker-compose.distributed.yml\n", environ=environ)
        self.assertEqual(inst.subprocess_env(), environ)

    def test_environment_is_copied_at_construction(self):
        environ = {"PATH": "/bin"}
        inst = self.install("", environ=environ)
        environ["LATER"] = "1"
        self.assertNotIn("LATER", inst.subprocess_env())

    def test_setup_secret_names_have_no_duplicates(self):
        self.assertEqual(len(SETUP_SECRET_NAMES), len(set(SETUP_SECRET_NAMES)))


# ======================================================= compose args ====

class ComposeArgsTests(TempRootTest):
    def test_production_uses_env_file_and_both_compose_files_in_order(self):
        self.production_files()
        inst = Installation(self.root, production=True, environ={}, runner=Runner(), compose=COMPOSE)
        self.assertEqual(inst.compose_args("up", "-d"), [
            "docker", "compose", "--env-file", str(self.root / PRODUCTION_ENV),
            "-f", str(self.root / DISTRIBUTED_FILE), "-f", str(self.root / PRODUCTION_OVERLAY), "up", "-d"])

    def test_production_with_custom_env_file_and_standalone_binary(self):
        self.production_files()
        custom = self.write("prod/custom.env", "")
        inst = Installation(self.root, production=True, env_file=custom, environ={}, runner=Runner(),
                            compose=["/usr/local/bin/docker-compose"])
        self.assertEqual(inst.compose_args("config", "--quiet"), [
            "/usr/local/bin/docker-compose", "--env-file", str(custom),
            "-f", str(self.root / DISTRIBUTED_FILE), "-f", str(self.root / PRODUCTION_OVERLAY), "config", "--quiet"])

    def test_standalone_and_distributed_use_no_files(self):
        for env_text in ("", "COMPOSE_FILE=docker-compose.distributed.yml:docker-compose.override.yml\n"):
            with self.subTest(env=env_text):
                inst = self.install(env_text)
                args = inst.compose_args("pull", "app")
                self.assertEqual(args, ["docker", "compose", "pull", "app"])
                self.assertNotIn("-f", args)
                self.assertNotIn("--env-file", args)
                self.assertEqual(inst.compose_args(), ["docker", "compose"])

    def test_standalone_binary_without_files(self):
        inst = self.install("", compose=["/opt/bin/docker-compose"])
        self.assertEqual(inst.compose_args("up", "-d"), ["/opt/bin/docker-compose", "up", "-d"])

    def test_compose_run_and_docker_run_use_run(self):
        runner = Runner()
        inst = self.install("", runner=runner, docker=["docker", "--context", "remote"])
        with mock.patch.object(inst, 'check_ownership') as ownership:
            inst.compose_run("pull", "app", stream=True, timeout=12)
        ownership.assert_called_once_with()
        inst.docker_run("ps", "-a", check=False)
        (first, first_kwargs), (second, second_kwargs) = runner.calls
        self.assertEqual(first, ["docker", "compose", "pull", "app"])
        self.assertNotIn("capture_output", first_kwargs)
        self.assertEqual(first_kwargs["timeout"], 12)
        self.assertEqual(second, ["docker", "--context", "remote", "ps", "-a"])
        self.assertTrue(second_kwargs["capture_output"])

    def test_docker_defaults_to_docker(self):
        self.assertEqual(self.install("").docker, ["docker"])


# =================================================== resolve compose ====

class ResolveComposeTests(unittest.TestCase):
    @staticmethod
    def which(**found):
        return lambda name: found.get(name.replace("-", "_"))

    def test_plugin_is_preferred(self):
        runner = Runner(lambda args, kwargs: completed(0, "2.29.1\n"))
        result = resolve_compose_command(runner, self.which(docker="/usr/bin/docker",
                                                            docker_compose="/usr/bin/docker-compose"))
        self.assertEqual(result, ["docker", "compose"])
        self.assertEqual(runner.calls, [(["docker", "compose", "version", "--short"],
                                         {"capture_output": True, "text": True, "check": False})])

    def test_standalone_v2_accepted_when_plugin_missing(self):
        for version in ("2.29.1", "v2.29.1", "2.0.0-rc.1", "5.1.4\n", "v10.0.0", "20.1"):
            with self.subTest(version=version):
                def handler(args, kwargs, version=version):
                    if args[:2] == ["docker", "compose"]:
                        return completed(1, "", "docker: 'compose' is not a docker command.")
                    return completed(0, version)
                runner = Runner(handler)
                result = resolve_compose_command(runner, self.which(docker="/usr/bin/docker",
                                                                    docker_compose="/opt/bin/docker-compose"))
                self.assertEqual(result, ["/opt/bin/docker-compose"])
                self.assertEqual(runner.calls[-1][0], ["/opt/bin/docker-compose", "version", "--short"])

    def test_standalone_without_docker_cli(self):
        runner = Runner(lambda args, kwargs: completed(0, "2.30.0"))
        result = resolve_compose_command(runner, self.which(docker_compose="/opt/bin/docker-compose"))
        self.assertEqual(result, ["/opt/bin/docker-compose"])
        self.assertEqual(runner.commands(), [["/opt/bin/docker-compose", "version", "--short"]])

    def test_standalone_v1_rejected(self):
        for version in ("1.29.2", "v1.29.2", "0.9.0", "2"):
            with self.subTest(version=version):
                runner = Runner(lambda args, kwargs, version=version: completed(
                    1 if args[:2] == ["docker", "compose"] else 0, version))
                with self.assertRaises(InstallError) as caught:
                    resolve_compose_command(runner, self.which(docker="/usr/bin/docker",
                                                               docker_compose="/usr/bin/docker-compose"))
                self.assertEqual(str(caught.exception),
                                 f"docker-compose {version.lstrip('v')} is too old: bin/update needs Docker Compose v2")

    def test_standalone_probe_failing_is_rejected_as_unknown(self):
        runner = Runner(lambda args, kwargs: completed(1, "", "boom"))
        with self.assertRaises(InstallError) as caught:
            resolve_compose_command(runner, self.which(docker_compose="/usr/bin/docker-compose"))
        self.assertEqual(str(caught.exception),
                         "docker-compose (unknown version) is too old: bin/update needs Docker Compose v2")

    def test_standalone_v2_with_failing_probe_is_rejected(self):
        runner = Runner(lambda args, kwargs: completed(1, "2.29.1"))
        with self.assertRaises(InstallError):
            resolve_compose_command(runner, self.which(docker_compose="/usr/bin/docker-compose"))

    def test_nothing_installed(self):
        runner = Runner()
        with self.assertRaises(InstallError) as caught:
            resolve_compose_command(runner, self.which())
        self.assertEqual(str(caught.exception), "docker is not installed or not on PATH")
        self.assertEqual(runner.calls, [])

    def test_docker_without_any_compose(self):
        runner = Runner(lambda args, kwargs: completed(1, "", "unknown command"))
        with self.assertRaises(InstallError) as caught:
            resolve_compose_command(runner, self.which(docker="/usr/bin/docker"))
        self.assertEqual(str(caught.exception),
                         "Docker Compose v2 is not available (neither `docker compose` nor `docker-compose`)")

    def test_installation_resolves_compose_with_its_runner_when_not_given(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".env").write_text("")
            runner = Runner()
            with mock.patch.object(install, "resolve_compose_command", return_value=["docker", "compose"]) as resolve:
                inst = Installation(root, environ={}, runner=runner)
            resolve.assert_called_once_with(runner)
            self.assertEqual(inst.compose, ["docker", "compose"])

    def test_installation_does_not_resolve_compose_before_validating(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(install, "resolve_compose_command") as resolve:
                with self.assertRaises(InstallError):
                    Installation(Path(tmp), environ={}, runner=Runner())
            resolve.assert_not_called()


# ================================================================ run ====

class RunTests(TempRootTest):
    def test_run_captures_output_in_checkout_with_subprocess_env(self):
        runner = Runner(lambda args, kwargs: completed(0, "out\n", "err\n"))
        inst = self.install("", environ={"PATH": "/bin", "X": "1"}, runner=runner)
        result = inst.run(("docker", "version"))
        self.assertEqual(result, CommandResult(["docker", "version"], 0, "out\n", "err\n"))
        args, kwargs = runner.calls[-1]
        self.assertEqual(args, ["docker", "version"])
        self.assertEqual(kwargs, {"cwd": str(self.root), "env": {"PATH": "/bin", "X": "1"}, "check": False,
                                  "text": True, "capture_output": True})

    def test_run_streams_without_capture_and_passes_timeout(self):
        runner = Runner(lambda args, kwargs: completed(0, None, None))
        inst = self.install("", runner=runner)
        result = inst.run(["docker", "compose", "pull"], stream=True, timeout=45)
        self.assertEqual((result.stdout, result.stderr), ("", ""))
        kwargs = runner.calls[-1][1]
        self.assertNotIn("capture_output", kwargs)
        self.assertEqual(kwargs["timeout"], 45)

    def test_run_has_no_timeout_unless_asked(self):
        runner = Runner()
        self.install("", runner=runner).run(["docker", "ps"])
        self.assertNotIn("timeout", runner.calls[-1][1])

    def test_production_run_uses_sanitized_env(self):
        self.production_files("FUTURE_AGI_VERSION=v1\n")
        runner = Runner()
        inst = Installation(self.root, production=True, runner=runner, compose=COMPOSE,
                            environ={"SECRET_KEY": "s", "FUTURE_AGI_VERSION": "v0", "PATH": "/bin"})
        inst.compose_run("config", "--quiet")
        self.assertEqual(runner.calls[-1][1]["env"], {"PATH": "/bin"})

    def test_nonzero_exit_raises_with_description(self):
        runner = Runner(lambda args, kwargs: completed(17, "", "warning\nError: service \"app\" refers to bad\n\n"))
        inst = self.install("", runner=runner)
        with self.assertRaises(InstallError) as caught:
            inst.compose_run("config", "--quiet")
        self.assertEqual(str(caught.exception),
                         '`docker compose config` failed with exit code 17: Error: service "app" refers to bad')

    def test_nonzero_exit_returned_when_not_checked(self):
        runner = Runner(lambda args, kwargs: completed(3, "o", "e"))
        result = self.install("", runner=runner).run(["docker", "ps"], check=False)
        self.assertEqual(result.returncode, 3)
        self.assertEqual((result.stdout, result.stderr), ("o", "e"))

    def test_missing_executable_is_install_error(self):
        def handler(args, kwargs):
            raise FileNotFoundError(2, "No such file or directory", "docker")
        inst = self.install("", runner=Runner(handler))
        with self.assertRaises(InstallError) as caught:
            inst.run(["docker", "ps"])
        self.assertTrue(str(caught.exception).startswith("cannot run docker: "), str(caught.exception))
        self.assertIn("No such file or directory", str(caught.exception))
        self.assertIsInstance(caught.exception.__cause__, FileNotFoundError)

    def test_timeout_is_install_error(self):
        def handler(args, kwargs):
            raise subprocess.TimeoutExpired(args, kwargs["timeout"])
        inst = self.install("", runner=Runner(handler))
        with self.assertRaises(InstallError) as caught:
            inst.run(inst.compose_args("up", "-d", "--wait", "--wait-timeout", "60"), timeout=30.4)
        self.assertEqual(str(caught.exception), "`docker compose up -d ...` timed out after 30s")
        self.assertIsInstance(caught.exception.__cause__, subprocess.TimeoutExpired)


class DescribeFailureTests(unittest.TestCase):
    def describe(self, args, returncode=1, stderr=""):
        return Installation.describe_failure(CommandResult(list(args), returncode, "", stderr))

    def test_last_nonempty_stderr_line(self):
        self.assertEqual(self.describe(["docker", "compose", "pull"], 18, "a\n  b failed  \n\n   \n"),
                         "`docker compose pull` failed with exit code 18: b failed")

    def test_empty_stderr_points_at_output_above(self):
        self.assertEqual(self.describe(["docker", "compose", "up", "-d"], 1, ""),
                         "`docker compose up` failed with exit code 1 (output above)")

    def test_whitespace_only_stderr_has_no_detail(self):
        self.assertEqual(self.describe(["docker", "ps"], 2, " \n\t\n"), "`docker ps` failed with exit code 2")

    def test_options_and_paths_are_left_out_and_four_words_kept(self):
        args = ["docker", "compose", "--env-file", "/srv/fagi/deploy/.env.production", "-f",
                "/srv/fagi/docker-compose.distributed.yml", "-f", "/srv/fagi/deploy/docker-compose.production.yml",
                "pull", "backend", "worker", "frontend"]
        self.assertEqual(self.describe(args, 1, "denied"),
                         "`docker compose pull backend` failed with exit code 1: denied")


# ============================================================= config ====

class ConfigTests(TempRootTest):
    def counting(self, payloads):
        payloads = list(payloads)

        def handler(args, kwargs):
            return payloads.pop(0) if len(payloads) > 1 else payloads[0]
        return Runner(handler)

    def test_config_runs_compose_config_json_once(self):
        runner = self.counting([completed(0, json.dumps({"name": "proj", "services": {}}))])
        inst = self.install("", runner=runner)
        self.assertEqual(inst.config(), {"name": "proj", "services": {}})
        self.assertEqual(inst.config(), {"name": "proj", "services": {}})
        self.assertEqual(runner.commands(), [["docker", "compose", "config", "--format", "json"],
                                             ["docker", "compose", "config", "--services"]])

    def test_refresh_reruns(self):
        runner = self.counting([completed(0, '{"name": "one"}'), completed(0, '{"name": "two"}')])
        inst = self.install("", runner=runner)
        self.assertEqual(inst.config()["name"], "one")
        self.assertEqual(inst.config()["name"], "one")
        self.assertEqual(inst.config(refresh=True)["name"], "two")
        self.assertEqual(inst.config()["name"], "two")
        self.assertEqual(len(runner.calls), 2)

    def test_production_config_uses_files(self):
        self.production_files()
        runner = self.counting([completed(0, '{"name": "futureagi"}')])
        inst = Installation(self.root, production=True, environ={}, runner=runner, compose=COMPOSE)
        inst.config()
        self.assertEqual(runner.commands()[0][-3:], ["config", "--format", "json"])
        self.assertIn("--env-file", runner.commands()[0])

    def test_invalid_json(self):
        inst = self.install("", runner=self.counting([completed(0, "not json")]))
        with self.assertRaises(InstallError) as caught:
            inst.config()
        self.assertTrue(str(caught.exception).startswith("docker compose config returned invalid JSON: "))

    def test_failing_config(self):
        inst = self.install("", runner=self.counting([completed(15, "", "required variable FUTURE_AGI_VERSION is missing")]))
        with self.assertRaises(InstallError) as caught:
            inst.config()
        self.assertEqual(str(caught.exception), "`docker compose config` failed with exit code 15: required "
                                                "variable FUTURE_AGI_VERSION is missing")

    def test_project(self):
        inst = self.install("", runner=self.counting([completed(0, '{"name": "acme", "services": {}}')]))
        self.assertEqual(inst.project, "acme")

    def test_missing_project_name(self):
        for payload in ('{"services": {}}', '{"name": ""}', '{"name": null}'):
            with self.subTest(payload=payload):
                inst = self.install("", runner=self.counting([completed(0, payload)]))
                with self.assertRaises(InstallError) as caught:
                    inst.project
                self.assertEqual(str(caught.exception), "docker compose config did not report a project name")


# ================================================== futureagi_services ====

class ServicesTests(TempRootTest):
    def services(self, services, **kwargs):
        inst = self.install("", runner=Runner(docker_handler({"name": "futureagi", "services": services})), **kwargs)
        return inst.futureagi_services()

    def test_every_repository_maps_to_its_component(self):
        for repository, component in REPOSITORY_COMPONENTS.items():
            with self.subTest(repository=repository):
                found = self.services({"svc": {"image": f"{repository}:v1.2.3"}})
                self.assertEqual(len(found), 1)
                self.assertEqual(found[0]["component"], component)
                self.assertEqual(found[0]["service"], "svc")
                self.assertEqual(found[0]["image"], f"{repository}:v1.2.3")
                self.assertEqual(found[0]["ref"], parse_image_ref(f"{repository}:v1.2.3"))
                self.assertFalse(found[0]["buildable"])

    def test_serving_gpu_tags_map_to_serving_gpu(self):
        found = {s["service"]: s["component"] for s in self.services({
            "serving": {"image": "futureagi/serving:v1.2.3"},
            "serving-gpu": {"image": "futureagi/serving:v1.2.3-gpu"},
            "serving-latest-gpu": {"image": "futureagi/serving:latest-gpu"},
            "serving-untagged": {"image": "futureagi/serving"},
            "serving-gpu-word": {"image": "futureagi/serving:gpu"},
            "serving-digest": {"image": "futureagi/serving@" + DIGEST},
        })}
        self.assertEqual(found, {"serving": "serving", "serving-gpu": "serving_gpu",
                                 "serving-latest-gpu": "serving_gpu", "serving-untagged": "serving",
                                 "serving-gpu-word": "serving", "serving-digest": "serving"})

    def test_gpu_suffix_only_matters_for_serving(self):
        found = self.services({"app": {"image": "futureagi/standalone:v1.2.3-gpu"}})
        self.assertEqual(found[0]["component"], "standalone")

    def test_registry_prefixes_and_mirrors(self):
        found = {s["service"]: s["component"] for s in self.services({
            "a": {"image": "docker.io/futureagi/standalone:v1"},
            "b": {"image": "index.docker.io/futureagi/future-agi:latest"},
            "c": {"image": "registry.example.com:5000/futureagi/frontend:v1"},
            "d": {"image": "localhost:5000/futureagi/code-executor"},
        })}
        self.assertEqual(found, {"a": "standalone", "b": "backend", "c": "frontend", "d": "code_executor"})

    def test_non_future_agi_and_unparsable_images_are_ignored(self):
        found = self.services({
            "postgres": {"image": "postgres:16"},
            "minio": {"image": "ghcr.io/coollabsio/minio:RELEASE.2025@" + DIGEST},
            "lookalike": {"image": "otherorg/standalone:v1"},
            "prefix": {"image": "futureagi/standalone-extra:v1"},
            "nested": {"image": "futureagi/standalone/sub:v1"},
            "bad-case": {"image": "FutureAGI/Standalone:v1"},
            "bad-space": {"image": " futureagi/standalone:v1"},
            "bad-tag": {"image": "futureagi/standalone:bad tag"},
            "bad-digest": {"image": "futureagi/standalone@sha256:short"},
            "empty": {"image": ""},
            "none": {"image": None},
            "build-only": {"build": {"context": "./x"}},
            "app": {"image": "futureagi/standalone:v1"},
        })
        self.assertEqual([s["service"] for s in found], ["app"])

    def test_buildable_services_are_flagged(self):
        found = self.services({
            "fi-collector": {"image": "futureagi/fi-collector:local", "build": {"context": "./fi-collector"}},
            "backend": {"image": "futureagi/future-agi:v1"},
        })
        self.assertEqual({s["service"]: s["buildable"] for s in found}, {"fi-collector": True, "backend": False})

    def test_sorted_by_service_name(self):
        found = self.services({name: {"image": "futureagi/future-agi:v1"} for name in ("worker", "backend", "beat")})
        self.assertEqual([s["service"] for s in found], ["backend", "beat", "worker"])

    def test_explicit_config_does_not_run_compose(self):
        runner = Runner()
        inst = self.install("", runner=runner)
        found = inst.futureagi_services({"services": {"app": {"image": "futureagi/standalone:v1"}}})
        self.assertEqual([s["component"] for s in found], ["standalone"])
        self.assertEqual(runner.calls, [])

    def test_missing_or_null_services(self):
        inst = self.install("", runner=Runner())
        self.assertEqual(inst.futureagi_services({"name": "x"}), [])
        self.assertEqual(inst.futureagi_services({"name": "x", "services": None}), [])

    def test_component_for(self):
        self.assertEqual(component_for(parse_image_ref("futureagi/serving:v1-gpu")), "serving_gpu")
        self.assertEqual(component_for(parse_image_ref("futureagi/serving:v1")), "serving")
        self.assertIsNone(component_for(parse_image_ref("library/serving:v1-gpu")))
        self.assertIsNone(component_for(parse_image_ref("redis:7")))


# ================================================ containers / images ====

class ContainersTests(TempRootTest):
    def inst(self, **handler):
        runner = Runner(docker_handler(**handler))
        return self.install("", runner=runner), runner

    def test_no_containers(self):
        inst, runner = self.inst(containers=[])
        self.assertEqual(inst.containers(), [])
        self.assertEqual(runner.commands()[-1], ["docker", "ps", "-a", "-q", "--no-trunc", "--filter",
                                                 "label=com.docker.compose.project=futureagi"])
        self.assertFalse(any(cmd[1:2] == ["inspect"] for cmd in runner.commands()))

    def test_ps_filters_by_the_resolved_project(self):
        inst, runner = self.inst(config={"name": "my-proj", "services": {}})
        inst.containers()
        self.assertIn("label=com.docker.compose.project=my-proj", runner.commands()[-1])

    def test_parses_inspect_records(self):
        app = container_record("app", image="futureagi/standalone:v1.2.3", image_id="sha256:aaa", restarts=2,
                               health="healthy", entrypoint=["/entrypoint.sh"], cmd=["serve", "--port", "8000"])
        bootstrap = container_record("postgres-schema-bootstrap", image="futureagi/future-agi:v1.2.3",
                                     image_id="sha256:bbb", status="exited", exit_code=0,
                                     entrypoint=["python"], cmd=["manage.py", "migrate", "--check", "--noinput"])
        inst, runner = self.inst(containers=[app, bootstrap])
        found = inst.containers()
        self.assertEqual(runner.commands()[-1], ["docker", "inspect", app["Id"], bootstrap["Id"]])
        self.assertEqual(found, [
            {"id": app["Id"], "service": "app", "image_id": "sha256:aaa", "image": "futureagi/standalone:v1.2.3",
             "status": "running", "exit_code": 0, "restart_count": 2, "health": "healthy",
             "started_at": "2026-10-19T00:00:00Z", "command": ["/entrypoint.sh", "serve", "--port", "8000"]},
            {"id": bootstrap["Id"], "service": "postgres-schema-bootstrap", "image_id": "sha256:bbb",
             "image": "futureagi/future-agi:v1.2.3", "status": "exited", "exit_code": 0, "restart_count": 0,
             "health": "none", "started_at": "2026-10-19T00:00:00Z",
             "command": ["python", "manage.py", "migrate", "--check", "--noinput"]},
        ])

    def test_sparse_records_cannot_establish_ownership(self):
        inst, _ = self.inst(ps=completed(0, 'abc\n'), inspect=completed(0, json.dumps([{'Id': 'abc'}])))
        with self.assertRaisesRegex(InstallError, 'ownership'):
            inst.containers()

    def test_blank_lines_in_ps_output_are_ignored(self):
        app = container_record("app")
        inst, runner = self.inst(containers=[app], ps=completed(0, f"\n  {app['Id']}  \n\n"))
        self.assertEqual([c["service"] for c in inst.containers()], ["app"])
        self.assertEqual(runner.commands()[-1], ["docker", "inspect", app["Id"]])

    def test_failing_ps_is_an_error(self):
        inst, _ = self.inst(ps=completed(1, "", "Cannot connect to the Docker daemon at unix:///var/run/docker.sock"))
        with self.assertRaises(InstallError) as caught:
            inst.containers()
        self.assertEqual(str(caught.exception), "`docker ps` failed with exit code 1: Cannot connect to the Docker "
                                                "daemon at unix:///var/run/docker.sock. Docker is not running or not "
                                                "reachable (check DOCKER_HOST and docker context ls)")

    def test_failed_inspect_is_an_error_not_an_empty_project(self):
        # docker ps just listed this project's containers. If `docker inspect`
        # then fails, reporting [] makes check_stack claim the install does
        # not exist, and lets apply's post-rollout running-image check
        # ("containers still run the previous images") pass without checking.
        app = container_record("app")
        inst, _ = self.inst(containers=[app], inspect=completed(
            1, "[]\n", "Error response from daemon: dial unix /var/run/docker.sock: connect: connection refused"))
        with self.assertRaises(InstallError):
            inst.containers()

    def test_unparsable_inspect_output_is_an_error_not_an_empty_project(self):
        app = container_record("app")
        inst, _ = self.inst(containers=[app], inspect=completed(0, "{truncated"))
        with self.assertRaises(InstallError):
            inst.containers()

    def test_image_inspect(self):
        record = image_record("sha256:img", version="v1.2.3", revision="abc123",
                              repo_digests=["futureagi/standalone@" + DIGEST])
        inst, runner = self.inst(images={"futureagi/standalone:v1.2.3": record})
        self.assertEqual(inst.image("futureagi/standalone:v1.2.3"),
                         {"id": "sha256:img", "repo_digests": ["futureagi/standalone@" + DIGEST],
                          "version": "v1.2.3", "revision": "abc123"})
        self.assertEqual(runner.commands()[-1], ["docker", "image", "inspect", "futureagi/standalone:v1.2.3"])

    def test_image_without_labels_or_digests(self):
        inst, _ = self.inst(images={"local": image_record("sha256:l", labels=False)})
        self.assertEqual(inst.image("local"), {"id": "sha256:l", "repo_digests": [], "version": "", "revision": ""})
        inst, _ = self.inst(images={"x": {"Id": "sha256:x", "Config": {"Labels": None}}})
        self.assertEqual(inst.image("x")["version"], "")

    def test_missing_image(self):
        inst, _ = self.inst(images={})
        self.assertIsNone(inst.image("futureagi/standalone:v9.9.9"))

    def test_image_inspect_bad_output(self):
        for result in (completed(0, "not json"), completed(0, "[]"), completed(0, "")):
            with self.subTest(stdout=result.stdout):
                inst = self.install("", runner=Runner(lambda args, kwargs, result=result: result))
                self.assertIsNone(inst.image("x"))

    def test_image_never_raises_on_failure(self):
        inst = self.install("", runner=Runner(lambda args, kwargs: completed(125, "", "boom")))
        self.assertIsNone(inst.image("sha256:x"))


# ========================================================= check_stack ====

class CheckStackTests(TempRootTest):
    def inst(self, env_text="", production=False):
        runner = Runner(docker_handler({"name": "futureagi", "services": {}}))
        if production:
            self.production_files()
            return Installation(self.root, production=True, environ={}, runner=runner, compose=COMPOSE)
        return self.install(env_text, runner=runner)

    @staticmethod
    def running(*services, bootstrap_command=None):
        containers = [{"service": s, "command": []} for s in services]
        if bootstrap_command is not None:
            containers.append({"service": "postgres-schema-bootstrap", "command": bootstrap_command})
        return containers

    def test_no_containers(self):
        # Round 5 (T53): the stack may only be stopped; production starts with its Compose command, not setup.sh.
        start = {"production": "docker compose --env-file deploy/.env.production -f docker-compose.distributed.yml -f "
                               "deploy/docker-compose.production.yml up -d --no-build --wait"}
        install = {"production": "install it as deploy/README.md describes (deploy/setup.sh)"}
        for kwargs in ({}, {"env_text": "COMPOSE_FILE=docker-compose.distributed.yml\n"}):
            with self.subTest(**kwargs):
                problems = self.inst(**kwargs).check_stack([])
                mode = "production" if kwargs.get("production") else "other"
                self.assertEqual(problems, [
                    "no containers of Compose project 'futureagi' exist. The stack may be stopped (Compose's down "
                    "removes the containers and keeps the data), or Future AGI is not installed here yet: start it "
                    f"with `{start.get(mode, 'docker compose up -d')}`, or "
                    f"{install.get(mode, 'install it with `./bin/install`')}, then check again"])
        # Round 6 (U9): production's start command, run by hand, is followed by the shell rule.
        with self.subTest(production=True):
            self.assertEqual(self.inst(production=True).check_stack([]), [
                "no containers of Compose project 'futureagi' exist. The stack may be stopped (Compose's down removes "
                "the containers and keeps the data), or Future AGI is not installed here yet: "
                f"{install['production']}, or start it with `{start['production']}`. Run it from a shell that does "
                "not export any key of deploy/.env.production (Compose prefers your shell over the env file). Then "
                "check again"])

    def test_standalone_with_only_standalone_services(self):
        self.assertEqual(self.inst().check_stack(self.running("app", "postgres", "clickhouse", "serving")), [])

    def test_standalone_running_distributed_services(self):
        problems = self.inst().check_stack(self.running("app", "worker", "backend", "postgres"))
        self.assertEqual(len(problems), 1)
        self.assertIn("project 'futureagi' runs Distributed services (backend, worker)", problems[0])
        self.assertIn("but .env selects the Standalone stack", problems[0])
        self.assertIn("bin/update never moves an install between stacks", problems[0])

    def test_every_distributed_only_service_is_a_signal(self):
        for service in DISTRIBUTED_ONLY_SERVICES:
            with self.subTest(service=service):
                problems = self.inst().check_stack(self.running("app", service))
                self.assertEqual(len(problems), 1)
                self.assertIn(f"({service})", problems[0])

    def test_distributed_with_only_the_standalone_app(self):
        problems = self.inst("COMPOSE_FILE=docker-compose.distributed.yml\n").check_stack(
            self.running("app", "postgres"))
        self.assertEqual(problems, [
            "project 'futureagi' runs the Standalone app container but bin/update was asked for the Distributed "
            "stack. bin/update never moves an install between stacks"])

    def test_production_with_only_the_standalone_app(self):
        problems = self.inst(production=True).check_stack(self.running("app"))
        self.assertEqual(len(problems), 1)
        self.assertIn("was asked for the Production (Distributed) stack", problems[0])

    def test_distributed_with_app_and_distributed_services_is_not_flagged_as_standalone(self):
        problems = self.inst("COMPOSE_FILE=docker-compose.distributed.yml\n").check_stack(
            self.running("app", "backend"))
        self.assertEqual(problems, [])

    def test_distributed_stack_is_fine(self):
        problems = self.inst("COMPOSE_FILE=docker-compose.distributed.yml\n").check_stack(
            self.running("backend", "worker", "frontend", bootstrap_command=["/entrypoint.sh", "bootstrap"]))
        self.assertEqual(problems, [])

    def test_distributed_install_running_the_production_overlay(self):
        problems = self.inst("COMPOSE_FILE=docker-compose.distributed.yml\n").check_stack(
            self.running("backend", bootstrap_command=["python", "manage.py", "migrate", "--check", "--noinput"]))
        # Round 5 (T17, T55): a command that works pasted, and here no deploy/.env.production says to name the env file.
        self.assertEqual(problems, [
            "this install runs the production overlay (check-only bootstrap jobs). Use `./bin/update --production "
            "check` with --env-file naming the env file deploy/setup.sh used (deploy/.env.production does not exist), "
            "which uses it and the overlay as deploy/setup.sh does"])

    def test_production_install_with_check_only_bootstrap_is_fine(self):
        problems = self.inst(production=True).check_stack(
            self.running("backend", bootstrap_command=["python", "manage.py", "migrate", "--check", "--noinput"]))
        self.assertEqual(problems, [])

    def test_production_flag_on_a_non_production_install(self):
        problems = self.inst(production=True).check_stack(
            self.running("backend", bootstrap_command=["/entrypoint.sh", "bootstrap"]))
        self.assertEqual(problems, [
            "--production was given, but this install was started without the production overlay (its bootstrap "
            "jobs apply migrations). Run bin/update without --production"])

    def test_production_without_bootstrap_container_is_not_judged(self):
        self.assertEqual(self.inst(production=True).check_stack(self.running("backend", "worker")), [])

    def test_check_flag_must_be_an_exact_argument(self):
        problems = self.inst(production=True).check_stack(
            self.running("backend", bootstrap_command=["python", "manage.py", "migrate", "--check-only"]))
        self.assertEqual(len(problems), 1)
        self.assertIn("--production was given", problems[0])

    def test_last_bootstrap_container_decides(self):
        containers = self.running("backend")
        containers.append({"service": "postgres-schema-bootstrap", "command": ["migrate", "--check"]})
        containers.append({"service": "postgres-schema-bootstrap", "command": ["migrate"]})
        self.assertEqual(len(self.inst(production=True).check_stack(containers)), 1)
        self.assertEqual(self.inst("COMPOSE_FILE=docker-compose.distributed.yml\n").check_stack(containers), [])

    def test_standalone_ignores_bootstrap_containers(self):
        self.assertEqual(self.inst().check_stack(self.running("app", bootstrap_command=["--check"])), [])

    def test_standalone_with_distributed_services_and_overlay_reports_only_the_stack(self):
        problems = self.inst().check_stack(self.running("app", "backend", bootstrap_command=["--check"]))
        self.assertEqual(len(problems), 1)


# =================================================== repo consistency ====

def compose_text(name):
    return (ROOT / name).read_text(encoding="utf-8")


def image_lines(text):
    for match in re.finditer(r"^\s*image:\s*(?P<value>.+?)\s*$", text, re.M):
        yield match.group("value").strip("\"'")


def compose_services(text):
    """Top-level service names of a compose file (two-space indented keys under services:)."""

    names, inside = [], False
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if not line.startswith(" "):
            inside = line.rstrip() == "services:"
            continue
        match = re.match(r"^  ([A-Za-z0-9._-]+):", line)
        if inside and match:
            names.append(match.group(1))
    return names


def bind_mount_sources(text):
    """(line, source) for every volume whose source is a path relative to the checkout.

    `${VAR:-default}` and `${VAR-default}` resolve to the default, as Compose
    does with the variable unset.
    """

    sources = []
    block_indent = None
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        if block_indent is not None and indent <= block_indent:
            block_indent = None
        match = re.match(r"^(\s*)volumes:\s*(#.*)?$", line)
        if match:
            block_indent = len(match.group(1))
            continue
        if block_indent is None:
            continue
        item = stripped[2:].strip() if stripped.startswith("- ") else stripped
        if item.startswith("source:"):
            item = item[len("source:"):].strip()
        elif not stripped.startswith("- ") or re.match(r"^[A-Za-z_]+:\s", item):
            continue
        item = item.strip("\"'")
        resolved = re.sub(r"\$\{[A-Za-z_][A-Za-z0-9_]*:?-([^}]*)\}", r"\1", item)
        source = resolved.split(":", 1)[0]
        if source.startswith(("./", "../")) or source in (".", ".."):
            sources.append((stripped, source))
    return sources


class RepositoryConsistencyTests(unittest.TestCase):
    def test_setup_secret_names_equal_setup_sh(self):
        text = (ROOT / "deploy" / "setup.sh").read_text(encoding="utf-8")
        match = re.search(r"^SECRET_NAMES=\(([^)]*)\)", text, re.M)
        self.assertIsNotNone(match, "SECRET_NAMES=(...) not found in deploy/setup.sh")
        names = match.group(1).split()
        self.assertEqual(len(names), len(set(names)))
        self.assertEqual(sorted(SETUP_SECRET_NAMES), sorted(names))

    def test_distributed_only_services_equal_bin_install(self):
        text = (ROOT / "bin" / "install").read_text(encoding="utf-8")
        match = re.search(r"^declare -a DISTRIBUTED_ONLY_SERVICES=\(([^)]*)\)", text, re.M)
        self.assertIsNotNone(match, "DISTRIBUTED_ONLY_SERVICES not found in bin/install")
        self.assertEqual(sorted(DISTRIBUTED_ONLY_SERVICES), sorted(match.group(1).split()))

    def test_stack_marker_services_exist_where_expected(self):
        standalone = set(compose_services(compose_text(STANDALONE_FILE)))
        distributed = set(compose_services(compose_text(DISTRIBUTED_FILE)))
        self.assertIn("postgres", standalone)  # the parser works
        self.assertIn("backend", distributed)
        for service in DISTRIBUTED_ONLY_SERVICES:
            # Markers may name services older Distributed installs ran
            # (rabbitmq), but never one the Standalone stack runs.
            with self.subTest(service=service):
                self.assertNotIn(service, standalone)
        self.assertGreaterEqual(len(set(DISTRIBUTED_ONLY_SERVICES) & distributed), len(DISTRIBUTED_ONLY_SERVICES) - 1)
        for service in STANDALONE_ONLY_SERVICES:
            self.assertIn(service, standalone)
            self.assertNotIn(service, distributed)

    def test_production_overlay_bootstrap_is_check_only_and_distributed_is_not(self):
        overlay = compose_text(PRODUCTION_OVERLAY)
        block = re.search(r"^  postgres-schema-bootstrap:\n((?:    .*\n|\s*\n)+)", overlay, re.M)
        self.assertIsNotNone(block)
        self.assertRegex(block.group(1), r'command:\s*\[[^\]]*"--check"')
        distributed = compose_text(DISTRIBUTED_FILE)
        block = re.search(r"^  postgres-schema-bootstrap:\n((?:    .*\n|\s*\n)+)", distributed, re.M)
        self.assertIsNotNone(block)
        self.assertNotIn("--check", block.group(1))

    def test_every_future_agi_image_maps_to_a_component_and_its_variable(self):
        seen_components = set()
        count = 0
        for name in COMPOSE_FILES:
            for image in image_lines(compose_text(name)):
                if "futureagi/" not in image:
                    continue
                count += 1
                with self.subTest(file=name, image=image):
                    match = re.fullmatch(
                        r"(?P<name>[^:${}\s]+(?::\d+/[^:${}\s]+)?):\$\{(?P<var>[A-Z][A-Z0-9_]*)(?P<rest>[^}]*)\}",
                        image)
                    self.assertIsNotNone(match, "a Future AGI image tag must come from its version variable")
                    ref = parse_image_ref(match.group("name") + ":v0.0.0")
                    self.assertIn(ref.repository, REPOSITORY_COMPONENTS)
                    component = REPOSITORY_COMPONENTS[ref.repository]
                    seen_components.add(component)
                    self.assertEqual(match.group("var"), COMPONENT_VARIABLES[component])
                    self.assertRegex(match.group("rest"), r"^(|:?-[^}]*|:?\?[^}]*)$")
        self.assertGreater(count, 10)
        self.assertEqual(seen_components, set(REPOSITORY_COMPONENTS.values()))

    def test_component_tables_agree_with_the_manifest(self):
        self.assertEqual(set(COMPONENT_VARIABLES), set(COMPONENT_REPOSITORIES))
        for repository, component in REPOSITORY_COMPONENTS.items():
            with self.subTest(repository=repository):
                self.assertEqual(COMPONENT_REPOSITORIES[component], repository)
        self.assertEqual(set(REPOSITORY_COMPONENTS), set(COMPONENT_REPOSITORIES.values()))
        self.assertEqual(COMPONENT_VARIABLES["serving_gpu"], COMPONENT_VARIABLES["serving"])
        self.assertEqual(VERSION_VARIABLES, tuple(sorted(set(COMPONENT_VARIABLES.values()))))

    def test_deployment_files_cover_every_checkout_bind_mount(self):
        listed = read_deployment_file_list(ROOT)
        found = []
        for name in COMPOSE_FILES:
            for line, source in bind_mount_sources(compose_text(name)):
                found.append(source)
                with self.subTest(file=name, mount=line):
                    self.assertNotIn("${", source, "a ./ mount source must resolve without the env file")
                    normalized = posixpath.normpath(source)
                    self.assertFalse(normalized.startswith(".."), "bind mount escapes the checkout")
                    covered = [entry for entry in listed
                               if normalized == entry.rstrip("/") or normalized.startswith(entry.rstrip("/") + "/")]
                    self.assertTrue(covered, f"{normalized} is not covered by deploy/update/deployment-files.txt")
        normalized = {posixpath.normpath(s) for s in found}
        # The parser really saw the mounts this test exists for.
        for expected in ("agentcc-gateway/config.example.yaml", "futureagi/scripts/property_catalog_oss",
                         "deploy/clickhouse/users.d/zz-default-password.xml"):
            self.assertIn(expected, normalized)

    def test_bind_mount_parser(self):
        text = ("services:\n  a:\n    image: x\n    volumes:\n      # comment\n      - data:/d\n"
                "      - ./one.xml:/etc/one.xml:ro\n      - \"./${P:-two/dir}:/two\"\n"
                "      - ${ABS:-/dev/null}:/abs:ro\n      - type: bind\n        source: ./three\n"
                "        target: /three\n    command: [\"./not-a-mount\"]\n    environment:\n"
                "      - ./not-a-volume\nvolumes:\n  data:\n")
        self.assertEqual([s for _, s in bind_mount_sources(text)], ["./one.xml", "./two/dir", "./three"])

    def test_every_listed_deployment_file_exists(self):
        listed = read_deployment_file_list(ROOT)
        for name in COMPOSE_FILES + (".env.example", "deploy/.env.production.example",
                                     "deploy/docker-compose.host-status.standalone.yml",
                                     "deploy/docker-compose.host-status.distributed.yml"):
            self.assertIn(name, listed)
        self.assertEqual(len(listed), len(set(listed)))
        for entry in listed:
            with self.subTest(entry=entry):
                self.assertTrue((ROOT / entry).exists(), f"{entry} is listed but missing from the checkout")


# ============================================================ CLI parser ====

class ParserTests(unittest.TestCase):
    def parse(self, argv):
        stderr = io.StringIO()
        try:
            with contextlib.redirect_stderr(stderr):
                return cli.build_parser().parse_args(argv)
        except SystemExit as exc:
            self.fail(f"{argv} was rejected (exit {exc.code}): {stderr.getvalue().strip().splitlines()[-1:]}")

    def rejects(self, argv):
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as caught:
                cli.build_parser().parse_args(argv)
        self.assertEqual(caught.exception.code, 2)

    def both_positions(self, commands, option_args, attribute, expected):
        for command in commands:
            for argv in ([*option_args, command], [command, *option_args]):
                with self.subTest(argv=argv):
                    namespace = self.parse(argv)
                    self.assertEqual(namespace.command, command)
                    self.assertEqual(getattr(namespace, attribute), expected)

    def test_production_before_or_after_every_subcommand(self):
        self.both_positions(("check", "apply", "rollback", "status"), ["--production"], "production", True)

    def test_env_file_before_or_after_every_subcommand(self):
        self.both_positions(("check", "apply", "rollback", "status"), ["--env-file", "p.env"], "env_file", "p.env")

    def test_manifest_before_or_after_check_and_apply(self):
        self.both_positions(("check", "apply"), ["--manifest", "m.json"], "manifest", "m.json")

    def test_bundle_before_or_after_check_and_apply(self):
        self.both_positions(("check", "apply"), ["--bundle", "b.json"], "bundle", "b.json")

    def test_policy_before_or_after_check_and_apply(self):
        self.both_positions(("check", "apply"), ["--policy", "major"], "policy", "major")

    def test_channel_before_or_after_check_and_apply(self):
        self.both_positions(("check", "apply"), ["--channel", "beta"], "channel", "beta")

    def test_backup_command_before_or_after_apply(self):
        self.both_positions(("apply",), ["--backup-command", "/bin/backup --all"], "backup_command",
                            "/bin/backup --all")

    def test_subcommand_options_after_the_subcommand(self):
        self.assertEqual(self.parse(["check", "--format", "json"]).format, "json")
        self.assertEqual(self.parse(["check"]).format, "text")
        self.assertTrue(self.parse(["apply", "--allow-no-backup"]).allow_no_backup)
        self.assertTrue(self.parse(["apply", "--dry-run"]).dry_run)
        namespace = self.parse(["rollback", "--receipt", "r.json", "--yes"])
        self.assertEqual((namespace.receipt, namespace.yes), ("r.json", True))
        namespace = self.parse(["rollback"])
        self.assertEqual((namespace.receipt, namespace.yes), (None, False))

    def test_hidden_root_and_cwd_before_the_subcommand(self):
        namespace = self.parse(["--root", "/r", "--cwd", "/c", "status"])
        self.assertEqual((namespace.root, namespace.cwd), ("/r", "/c"))

    def test_unset_options_are_absent_so_defaults_apply(self):
        namespace = self.parse(["apply"])
        for name in ("production", "env_file", "manifest", "bundle", "policy", "channel", "backup_command",
                     "root", "cwd"):
            with self.subTest(option=name):
                self.assertFalse(hasattr(namespace, name))

    def test_mixed_positions(self):
        namespace = self.parse(["--production", "check", "--env-file", "x.env", "--format", "json"])
        self.assertEqual((namespace.production, namespace.env_file, namespace.format), (True, "x.env", "json"))

    def test_rejections(self):
        for argv in ([], ["bogus"], ["check", "--policy", "huge"], ["check", "--channel", "nightly"],
                     ["check", "--format", "yaml"], ["status", "--manifest", "m"], ["rollback", "--policy", "major"],
                     ["status", "--yes"], ["check", "--dry-run"], ["apply", "--format", "json"]):
            with self.subTest(argv=argv):
                self.rejects(argv)


# ============================================================ CLI main ====

class FakeInstallation:
    def __init__(self, root, production=False, env_file=None, caller_cwd=None, command_options=()):
        self.root = Path(root)
        self.command_options = list(command_options)
        self.caller_cwd = Path(caller_cwd) if caller_cwd is not None else self.root
        self.production = production
        self.env_file = Path(env_file) if env_file else self.root / ".env"
        self.env = {"SECRET_KEY": "super-secret-value-123", "FUTURE_AGI_VERSION": "v1.1.0"}
        self.docker = ["docker", "--context", "fake"]
        self.mode = "production" if production else "standalone"
        self.label = "Production (Distributed)" if production else "Standalone"
        self.project = "futureagi"
        self.container_list = []
        self.images = {}
        self.services = []

    def relative(self, path):
        try:
            return str(Path(path).relative_to(self.root))
        except ValueError:
            return str(path)

    def containers(self):
        return list(self.container_list)

    def image(self, reference):
        return self.images.get(reference)

    def futureagi_services(self, config=None):
        return list(self.services)

    def identity(self):
        return {"mode": self.mode, "production": self.production, "env_file": self.relative(self.env_file),
                "project": self.project}


def plan_for(decision="update_available", reasons=(), notes=()):
    return {
        "schema_version": 2, "decision": decision, "resume": False, "target_version": "v1.2.0",
        "channel": "stable", "published_at": "2026-10-19T00:00:00Z", "minimum_version": "v1.0.0",
        "policy": "minor", "manifest_hash": "0" * 64, "manifest_source": "src",
        "installation": {"mode": "standalone", "project": "futureagi", "env_file": ".env", "root": "/r"},
        "current_versions": ["v1.1.0"],
        "signature": {"verified": True, "method": "public key", "signer": "/k.pub", "issuer": "", "tool": "cosign",
                      "error": ""},
        "components": [{"component": "standalone", "variable": "FUTURE_AGI_VERSION", "services": ["app"],
                        "configured": "v1.1.0", "current_version": "v1.1.0", "current_source": "env",
                        "current_tags": ["v1.1.0"], "target_tag": "v1.2.0", "target_image": "futureagi/standalone:v1.2.0",
                        "target_digest": DIGEST, "action": "update"}],
        "env_changes": {"FUTURE_AGI_VERSION": "v1.2.0"}, "deployment_drift": [], "missing_env_keys": [],
        "reasons": list(reasons), "notes": list(notes),
        "next_command": "./bin/update apply" if decision == "update_available" else None,
    }


class MainTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        base = Path(temporary.name).resolve()
        self.root = base / "checkout"
        self.root.mkdir()
        self.cwd = base / "caller"
        self.cwd.mkdir()
        self.workdir = self.root / STATE_DIR / f"verify-{os.getpid()}"

        env_patch = mock.patch.dict(os.environ, clean_environ(), clear=True)
        env_patch.start()
        self.addCleanup(env_patch.stop)

        self.installations = []
        self.configure_installation = lambda inst: None

        def make_installation(root, **kwargs):
            inst = FakeInstallation(root, **kwargs)
            self.configure_installation(inst)
            self.installations.append(inst)
            return inst

        self.Installation = self.patch("Installation", side_effect=make_installation)
        self.Updater = self.patch("Updater")
        self.Verifier = self.patch("Verifier")
        self.verification = Verification(True, method="public key", signer="/k.pub", tool="cosign")
        self.Verifier.return_value.verify.return_value = self.verification

        # A trust.FetchedManifest stand-in: `source` is where the manifest was
        # read from (the source asked for, unless a test sets fetched_from to
        # simulate trust's fallback to an older release, with its `note`).
        self.fetched = SimpleNamespace(data={"version": "v1.2.0", "channel": "stable"}, source=None, note=None)
        self.fetched_from = None
        self.fetch_calls = []
        self.fetch_error = None

        def fake_fetch(source, workdir, *, cwd, bundle=None):
            self.assertFalse(workdir.exists(), "a stale verify workdir must be removed before fetching")
            self.fetch_calls.append({"source": source, "workdir": workdir, "cwd": cwd, "bundle": bundle})
            workdir.mkdir(mode=0o700, parents=True)
            (workdir / "manifest.json").write_text("{}")
            if self.fetch_error is not None:
                raise self.fetch_error
            self.fetched.source = self.fetched_from or source
            return self.fetched

        self.fetch = self.patch("fetch_manifest", side_effect=fake_fetch)
        self.plan = plan_for()
        self.build_plan = self.patch("build_plan", side_effect=lambda *a, **k: self.plan)

    def patch(self, name, **kwargs):
        patcher = mock.patch.object(cli, name, **kwargs)
        mocked = patcher.start()
        self.addCleanup(patcher.stop)
        return mocked

    def main(self, *argv, root=True, cwd=True):
        prefix = []
        if root:
            prefix += ["--root", str(self.root)]
        if cwd:
            prefix += ["--cwd", str(self.cwd)]
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = cli.main([*prefix, *argv])
        return code, stdout.getvalue(), stderr.getvalue()

    # ------------------------------------------------------------- check --
    def test_check_update_available_exits_0_with_text_plan(self):
        code, out, err = self.main("check")
        self.assertEqual(code, 0, err)
        self.assertTrue(out.startswith("Future AGI update check\n"))
        self.assertIn("update available: run ./bin/update apply", out)
        self.Updater.return_value.apply.assert_not_called()

    def test_check_up_to_date_exits_0(self):
        self.plan = plan_for("up_to_date")
        code, out, _ = self.main("check")
        self.assertEqual(code, 0)
        self.assertIn("up to date, nothing to do", out)

    def test_check_blocked_exits_3(self):
        self.plan = plan_for("blocked", reasons=["the release signature is not verified: nope"])
        code, out, err = self.main("check")
        self.assertEqual(code, 3)
        self.assertIn("blocked (see below)", out)
        self.assertIn("the release signature is not verified: nope", out)
        self.assertEqual(err, "")

    def test_check_blocked_json_exits_3(self):
        self.plan = plan_for("blocked", reasons=["x"])
        code, out, _ = self.main("check", "--format", "json")
        self.assertEqual(code, 3)
        self.assertEqual(json.loads(out)["decision"], "blocked")

    def test_check_format_json_is_the_plan(self):
        code, out, _ = self.main("check", "--format", "json")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), self.plan)
        self.assertTrue(out.endswith("}\n"))

    def test_check_refuses_a_legacy_pre_rollout_journal_without_guessing(self):
        state = StateDir(self.root)
        state.write_journal({'kind': 'apply', 'target_version': 'v1.2.0', 'stage': 'pulled'})
        before = state.journal_path.read_bytes()
        code, _, err = self.main('check', '--format', 'json')
        self.assertEqual(code, 2)
        self.assertIn('exact env recovery', err)
        self.assertEqual(state.journal_path.read_bytes(), before)

    def test_check_passes_inputs_to_build_plan(self):
        state = StateDir(self.root)
        receipt = state.write_receipt({"kind": "apply", "status": "succeeded", "target_version": "v1.1.0",
                                       "mode": "standalone", "production": False, "env_file": ".env",
                                       "project": "futureagi"})
        manifest = self.cwd / "m.json"
        code, _, _ = self.main("check", "--manifest", str(manifest))
        self.assertEqual(code, 0)
        args, kwargs = self.build_plan.call_args
        self.assertIs(args[0], self.fetched.data)
        self.assertIs(args[1], self.verification)
        self.assertIs(args[2], self.installations[0])
        self.assertEqual(args[3].policy, "minor")
        self.assertEqual(kwargs["manifest_source"], str(manifest))
        self.assertEqual(kwargs["history"][-1]["_path"], str(receipt))
        self.assertEqual(kwargs["history"][-1]["target_version"], "v1.1.0")

    def test_verifier_uses_settings_and_installation_docker(self):
        self.main("check")
        args, kwargs = self.Verifier.call_args
        self.assertEqual(kwargs, {"docker": ["docker", "--context", "fake"]})
        self.assertEqual(args[0].get("FUTURE_AGI_UPDATE_POLICY"), "minor")
        self.Verifier.return_value.verify.assert_called_once_with(self.fetched)

    def test_check_creates_private_state_dir(self):
        self.main("check")
        state = self.root / STATE_DIR
        self.assertTrue(state.is_dir())
        self.assertEqual(stat.S_IMODE(state.stat().st_mode), 0o700)

    # ---------------------------------------------- path resolution --
    def test_relative_manifest_resolved_against_cwd(self):
        self.main("check", "--manifest", "releases/m.json")
        call = self.fetch_calls[0]
        self.assertEqual(call["source"], str(self.cwd / "releases" / "m.json"))
        self.assertEqual(call["cwd"], self.cwd)
        self.assertEqual(call["workdir"], self.workdir)
        self.assertIsNone(call["bundle"])
        self.assertEqual(self.build_plan.call_args[1]["manifest_source"], str(self.cwd / "releases" / "m.json"))

    def test_manifest_before_subcommand_is_resolved_too(self):
        # Options are documented to work on either side of the subcommand.
        stderr = io.StringIO()
        try:
            with contextlib.redirect_stderr(stderr), contextlib.redirect_stdout(io.StringIO()):
                cli.main(["--root", str(self.root), "--cwd", str(self.cwd), "--manifest", "m.json", "check"])
        except SystemExit as exc:
            self.fail(f"--manifest before the subcommand was rejected (exit {exc.code}): {stderr.getvalue()}")
        self.assertEqual(self.fetch_calls[0]["source"], str(self.cwd / "m.json"))

    def test_absolute_manifest_unchanged(self):
        self.main("check", "--manifest", "/srv/releases/m.json")
        self.assertEqual(self.fetch_calls[0]["source"], "/srv/releases/m.json")

    def test_manifest_url_unchanged(self):
        url = "https://example.com/r/future-agi-update-manifest.json"
        self.main("check", "--manifest", url)
        self.assertEqual(self.fetch_calls[0]["source"], url)

    def test_manifest_home_is_expanded(self):
        with mock.patch.dict(os.environ, {"HOME": str(self.cwd / "home")}):
            self.main("check", "--manifest", "~/m.json")
        self.assertEqual(self.fetch_calls[0]["source"], str(self.cwd / "home" / "m.json"))

    def test_relative_bundle_resolved_against_cwd(self):
        self.main("check", "--manifest", "m.json", "--bundle", "sig/b.json")
        self.assertEqual(self.fetch_calls[0]["bundle"], str(self.cwd / "sig" / "b.json"))

    def test_bundle_url_and_absolute_unchanged(self):
        for bundle in ("https://example.com/b.json", "/abs/b.json"):
            with self.subTest(bundle=bundle):
                self.fetch_calls.clear()
                self.main("check", "--bundle", bundle)
                self.assertEqual(self.fetch_calls[0]["bundle"], bundle)

    def test_default_manifest_is_the_latest_release(self):
        self.main("check")
        self.assertEqual(self.fetch_calls[0]["source"], DEFAULT_MANIFEST_URL)

    def test_settings_relative_manifest_resolved_against_root_not_cwd(self):
        (self.root / "update.conf").write_text("FUTURE_AGI_UPDATE_MANIFEST_URL=releases/m.json\n")
        self.main("check")
        self.assertEqual(self.fetch_calls[0]["source"], str(self.root / "releases" / "m.json"))
        self.assertEqual(self.build_plan.call_args[1]["manifest_source"], str(self.root / "releases" / "m.json"))

    def test_environment_relative_manifest_resolved_against_root(self):
        with mock.patch.dict(os.environ, {"FUTURE_AGI_UPDATE_MANIFEST_URL": "m.json"}):
            self.main("check")
        self.assertEqual(self.fetch_calls[0]["source"], str(self.root / "m.json"))

    def test_settings_absolute_manifest_unchanged(self):
        (self.root / "update.conf").write_text("FUTURE_AGI_UPDATE_MANIFEST_URL=/srv/m.json\n")
        self.main("check")
        self.assertEqual(self.fetch_calls[0]["source"], "/srv/m.json")

    def test_cli_manifest_wins_over_settings(self):
        (self.root / "update.conf").write_text("FUTURE_AGI_UPDATE_MANIFEST_URL=/srv/m.json\n")
        self.main("check", "--manifest", "mine.json")
        self.assertEqual(self.fetch_calls[0]["source"], str(self.cwd / "mine.json"))

    def test_relative_env_file_resolved_against_cwd(self):
        self.main("--production", "status", "--env-file", "prod/.env.production")
        self.assertEqual(self.Installation.call_args, mock.call(
            self.root, production=True, env_file=self.cwd / "prod" / ".env.production", caller_cwd=self.cwd.resolve(),
            command_options=[]))

    def test_absolute_env_file_unchanged(self):
        self.main("status", "--env-file", "/etc/fagi.env")
        self.assertEqual(self.Installation.call_args[1]["env_file"], Path("/etc/fagi.env"))

    def test_no_env_file_and_no_production(self):
        self.main("status")
        self.assertEqual(self.Installation.call_args,
                         mock.call(self.root, production=False, env_file=None, caller_cwd=self.cwd.resolve(),
                                   command_options=[]))

    def test_relative_receipt_resolved_against_cwd(self):
        code, _, _ = self.main("rollback", "--receipt", "r/receipt-1.json", "--yes")
        self.assertEqual(code, 0)
        self.Updater.return_value.rollback.assert_called_once_with(self.cwd / "r" / "receipt-1.json", yes=True)
        self.fetch.assert_not_called()
        self.assertFalse(self.workdir.exists())

    def test_rollback_defaults(self):
        self.main("rollback")
        self.Updater.return_value.rollback.assert_called_once_with(None, yes=False)

    def test_updater_gets_installation_settings_state_and_stderr(self):
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr), contextlib.redirect_stdout(io.StringIO()):
            cli.main(["--root", str(self.root), "--cwd", str(self.cwd), "rollback"])
        args, kwargs = self.Updater.call_args
        self.assertIs(args[0], self.installations[0])
        self.assertEqual(args[2].path, self.root / STATE_DIR)
        self.assertEqual(args[2].secrets, ["super-secret-value-123"])
        self.assertIs(kwargs["out"], stderr)

    def test_default_root_is_the_checkout(self):
        self.main("status", root=False)
        self.assertEqual(self.Installation.call_args[0][0], ROOT)

    def test_root_is_resolved(self):
        link = self.cwd / "link"
        link.symlink_to(self.root)
        self.main("--root", str(link), "status", root=False)
        self.assertEqual(self.Installation.call_args[0][0], self.root)

    def test_default_cwd_is_the_process_cwd(self):
        with mock.patch("os.getcwd", return_value=str(self.cwd)):
            self.main("check", "--manifest", "m.json", cwd=False)
        self.assertEqual(self.fetch_calls[0]["source"], str(self.cwd / "m.json"))
        self.assertEqual(self.fetch_calls[0]["cwd"], self.cwd)

    # ------------------------------------------------------------ apply --
    def test_apply_passes_flags(self):
        code, _, _ = self.main("apply", "--allow-no-backup", "--dry-run", "--backup-command", "/bin/bk --all")
        self.assertEqual(code, 0)
        args, kwargs = self.Updater.return_value.apply.call_args
        self.assertIs(args[1], self.fetched.data)
        self.assertEqual(kwargs, {"allow_no_backup": True, "backup_command": "/bin/bk --all", "dry_run": True,
                                  "stage": False, "release_binding": None, "acknowledge_stale_stage": False})
        settings = self.Updater.call_args[0][1]
        self.assertEqual(settings.get("FUTURE_AGI_UPDATE_BACKUP_COMMAND"), "/bin/bk --all")
        self.assertEqual(settings.sources["FUTURE_AGI_UPDATE_BACKUP_COMMAND"], "command line")

    def test_apply_defaults(self):
        self.main("apply")
        _, kwargs = self.Updater.return_value.apply.call_args
        self.assertEqual(kwargs, {"allow_no_backup": False, "backup_command": None, "dry_run": False,
                                  "stage": False, "release_binding": {}, "acknowledge_stale_stage": False})

    def test_apply_make_plan_builds_the_plan_with_the_given_receipt(self):
        self.main("apply", "--manifest", "m.json")
        make_plan = self.Updater.return_value.apply.call_args[0][0]
        self.build_plan.reset_mock()
        last = {"kind": "apply", "_path": "/x"}
        self.assertIs(make_plan([last]), self.plan)
        args, kwargs = self.build_plan.call_args
        self.assertIs(args[0], self.fetched.data)
        self.assertIs(args[1], self.verification)
        self.assertEqual(kwargs, {"manifest_source": str(self.cwd / "m.json"), "history": [last],
                                  "fallback_note": None, "newer_release": None, "journal_reason": None,
                                  "command_options": ["--manifest", str(self.cwd / "m.json")]})

    def test_policy_and_channel_override_settings(self):
        (self.root / "update.conf").write_text("FUTURE_AGI_UPDATE_POLICY=patch\nFUTURE_AGI_UPDATE_CHANNEL=stable\n")
        self.main("check", "--policy", "major", "--channel", "beta")
        settings = self.build_plan.call_args[0][3]
        self.assertEqual((settings.policy, settings.channel), ("major", "beta"))
        self.assertEqual(settings.sources["FUTURE_AGI_UPDATE_POLICY"], "command line")

    def test_settings_from_update_conf_without_flags(self):
        (self.root / "update.conf").write_text("FUTURE_AGI_UPDATE_POLICY=patch\n")
        self.main("check")
        self.assertEqual(self.build_plan.call_args[0][3].policy, "patch")

    # -------------------------------------------------------- exit codes --
    def raise_from_apply(self, exc):
        self.Updater.return_value.apply.side_effect = exc
        return self.main("apply")

    def test_exit_codes_of_update_errors(self):
        for exc, code in ((Blocked("blocked: x"), 3), (FailedBeforeRollout("before"), 4),
                          (FailedDuringRollout("during"), 5), (InterruptedRun("stopped"), 130),
                          (UpdateError("generic"), 2)):
            with self.subTest(exc=type(exc).__name__):
                self.Updater.return_value.apply.reset_mock()
                got, out, err = self.raise_from_apply(exc)
                self.assertEqual(got, code)
                self.assertEqual(err, f"bin/update: {exc}\n")
                self.assertFalse(self.workdir.exists())

    def test_exit_2_for_input_and_tool_errors(self):
        for exc in (ManifestError("bad manifest"), SettingsError("bad setting"), InstallError("no install"),
                    EnvFileError("bad env"), StateError("locked"), VerificationError("no cosign"),
                    NotReady("never ready")):
            with self.subTest(exc=type(exc).__name__):
                got, _, err = self.raise_from_apply(exc)
                self.assertEqual(got, 2)
                self.assertEqual(err, f"bin/update: {exc}\n")

    def test_keyboard_interrupt_exits_130(self):
        got, _, err = self.raise_from_apply(KeyboardInterrupt())
        self.assertEqual(got, 130)
        self.assertEqual(err, "bin/update: interrupted before anything was changed\n")

    def test_rollback_blocked_exits_3(self):
        self.Updater.return_value.rollback.side_effect = Blocked("rollback not started: rerun with --yes to confirm")
        code, _, err = self.main("rollback")
        self.assertEqual(code, 3)
        self.assertEqual(err, "bin/update: rollback not started: rerun with --yes to confirm\n")

    def test_settings_error_from_update_conf(self):
        (self.root / "update.conf").write_text("FUTURE_AGI_UPDATE_POLICY=huge\n")
        code, _, err = self.main("check")
        self.assertEqual(code, 2)
        self.assertEqual(err, "bin/update: FUTURE_AGI_UPDATE_POLICY must be one of patch, minor, major, not 'huge' "
                              "(from update.conf)\n")
        self.Installation.assert_not_called()

    def test_install_error_exits_2_and_creates_no_state(self):
        self.Installation.side_effect = InstallError("no .env in /x")
        for command in ("check", "apply", "rollback", "status"):
            with self.subTest(command=command):
                code, _, err = self.main(command)
                self.assertEqual(code, 2)
                self.assertEqual(err, "bin/update: no .env in /x\n")
                self.assertFalse((self.root / STATE_DIR).exists())

    def test_manifest_error_from_fetch_exits_2(self):
        self.fetch_error = ManifestError("cannot read manifest /x: nope")
        code, _, err = self.main("check")
        self.assertEqual(code, 2)
        self.assertEqual(err, "bin/update: cannot read manifest /x: nope\n")

    def test_check_errors_from_build_plan_exit_2(self):
        self.build_plan.side_effect = InstallError("`docker ps` failed with exit code 1")
        code, out, err = self.main("check")
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("docker ps", err)

    # ----------------------------------------------------------- workdir --
    def test_workdir_removed_after_check(self):
        self.main("check")
        self.assertEqual(self.fetch_calls[0]["workdir"], self.workdir)
        self.assertFalse(self.workdir.exists())

    def test_workdir_removed_after_apply(self):
        self.main("apply")
        self.assertFalse(self.workdir.exists())

    def test_workdir_removed_when_fetch_fails(self):
        self.fetch_error = ManifestError("manifest is not valid JSON")
        self.main("check")
        self.assertFalse(self.workdir.exists())

    def test_workdir_removed_when_verification_raises(self):
        self.Verifier.return_value.verify.side_effect = VerificationError("x")
        self.assertEqual(self.main("check")[0], 2)
        self.assertFalse(self.workdir.exists())

    def test_workdir_removed_on_interrupt(self):
        self.fetch_error = KeyboardInterrupt()
        self.assertEqual(self.main("check")[0], 130)
        self.assertFalse(self.workdir.exists())

    def test_workdir_removed_on_unexpected_exception(self):
        self.fetch_error = RuntimeError("unexpected")
        with self.assertRaises(RuntimeError):
            self.main("check")
        self.assertFalse(self.workdir.exists())

    def test_stale_workdir_removed_before_fetch(self):
        self.workdir.mkdir(parents=True)
        (self.workdir / "manifest.json").write_text("stale")
        (self.workdir / "bundle.json").write_text("stale")
        self.assertEqual(self.main("check")[0], 0)  # fake_fetch asserts the workdir was gone
        self.assertFalse(self.workdir.exists())

    def dead_pid(self):
        """The pid of a process that has exited and been reaped."""

        child = subprocess.Popen([sys.executable, "-c", "pass"])
        child.wait()
        return child.pid

    def test_workdirs_of_killed_runs_are_removed(self):
        # A run killed outright (kill -9) leaves its verify-<pid>; the next verifying run removes it.
        for command in ("check", "apply"):
            with self.subTest(command=command):
                stale = self.root / STATE_DIR / f"verify-{self.dead_pid()}"
                stale.mkdir(parents=True)
                (stale / "manifest.json").write_text("stale")
                self.assertEqual(self.main(command)[0], 0)
                self.assertFalse(stale.exists())

    def test_workdirs_of_live_or_unknown_processes_and_other_entries_are_kept(self):
        state = self.root / STATE_DIR
        state.mkdir()
        dead = self.dead_pid()
        # A check running next to this one: a process of this test's own (waiting for its stdin to close), so a
        # probe that sent a real signal would hit nothing else.
        # It takes SIGHUP's default action even under nohup, and says so before the run starts.
        neighbour = subprocess.Popen([sys.executable, "-c", "import signal, sys; "
                                     "signal.signal(signal.SIGHUP, signal.SIG_DFL); print('ready', flush=True); "
                                     "sys.stdin.read()"], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        self.addCleanup(neighbour.wait)
        self.addCleanup(neighbour.stdout.close)
        self.addCleanup(neighbour.stdin.close)
        self.assertEqual(neighbour.stdout.readline(), b"ready\n")
        live = state / f"verify-{neighbour.pid}"
        foreign = state / "verify-4242"  # alive under another user
        kept_dirs = [live, foreign, state / "verify-0", state / f"verify-{10 ** 30}", state / "verify-abc",
                     state / "verify-12x", state / "verify-", state / "receipts"]
        for directory in kept_dirs:
            directory.mkdir()
            (directory / "keep").write_text("keep")
        not_a_dir = state / f"verify-{dead}"
        not_a_dir.write_text("a file, not a workdir")
        target = self.root / "elsewhere"
        target.mkdir()
        (target / "keep").write_text("keep")
        link = state / f"verify-{self.dead_pid()}"
        link.symlink_to(target)
        real_kill = os.kill

        def kill(pid, sig):
            if pid == 4242:
                raise PermissionError(1, "Operation not permitted")
            return real_kill(pid, sig)

        with mock.patch.object(cli.os, "kill", side_effect=kill):
            self.assertEqual(self.main("check")[0], 0)
        neighbour.stdin.close()
        self.assertEqual(neighbour.wait(), 0)  # probed for existence, never signalled
        for directory in kept_dirs:
            with self.subTest(kept=directory.name):
                self.assertEqual((directory / "keep").read_text(), "keep")
        self.assertEqual(not_a_dir.read_text(), "a file, not a workdir")
        self.assertTrue(link.is_symlink())
        self.assertEqual((target / "keep").read_text(), "keep")

    def test_status_and_rollback_leave_workdirs_alone(self):
        stale = self.root / STATE_DIR / f"verify-{self.dead_pid()}"
        stale.mkdir(parents=True)
        for argv in (("status",), ("rollback", "--yes")):
            with self.subTest(argv=argv):
                self.main(*argv)
                self.assertTrue(stale.is_dir())

    def test_status_and_rollback_never_fetch(self):
        for argv in (("status",), ("rollback", "--yes")):
            with self.subTest(argv=argv):
                self.main(*argv)
                self.fetch.assert_not_called()
                self.Verifier.assert_not_called()
                self.assertFalse(self.workdir.exists())

    # ------------------------------------------------------------ status --
    def test_status_output(self):
        def configure(inst):
            inst.container_list = [
                {"service": "app", "status": "running", "image_id": "sha256:new"},
                {"service": "app", "status": "exited", "image_id": "sha256:old"},
                {"service": "serving", "status": "running", "image_id": ""},
            ]
            inst.images = {"sha256:new": {"version": "v1.1.0"}, "sha256:old": {"version": "v1.0.0"}}
            inst.services = [
                {"service": "app", "image": "futureagi/standalone:v1.1.0", "component": "standalone"},
                {"service": "serving", "image": "futureagi/serving:latest", "component": "serving"},
            ]
        self.configure_installation = configure
        code, out, err = self.main("status")
        self.assertEqual(code, 0, err)
        lines = out.splitlines()
        self.assertEqual(lines[0], "Installation  Standalone (Compose project futureagi, .env)")
        self.assertIn("app", lines[1])
        self.assertIn("futureagi/standalone:v1.1.0", lines[1])
        self.assertIn("running v1.1.0", lines[1])
        self.assertNotIn("v1.0.0", lines[1])
        self.assertIn("(FUTURE_AGI_VERSION=v1.1.0)", lines[1])
        self.assertIn("running -", lines[2])
        self.assertIn("(SERVING_VERSION=unset)", lines[2])
        self.assertEqual(lines[-1], "Last run      none")
        self.assertFalse((self.root / STATE_DIR).exists())

    def test_status_blocks_incomplete_journal_and_shows_last_receipt(self):
        state = StateDir(self.root)
        receipt = state.write_receipt({"kind": "apply", "target_version": "v1.2.0", "status": "succeeded",
                                       "finished_at": "2026-10-19T01:02:03Z"})
        state.write_journal({"kind": "rollback", "target_version": "v1.1.0", "stage": "env_written"})
        code, out, err = self.main("status")
        self.assertEqual(code, 2)
        self.assertIn('missing or invalid exact env recovery data', err)
        self.assertNotIn('the next apply or rollback recovers it', out)
        self.assertIsNotNone(state.read_journal())
        self.assertIn(f"Last run      apply to v1.2.0: succeeded (2026-10-19T01:02:03Z) {receipt}", out)

    def test_status_with_real_installation(self):
        (self.root / STANDALONE_FILE).write_text("services: {}\n")
        app = container_record("app", image="futureagi/standalone:v1.0.0", image_id="sha256:app")
        runner = Runner(docker_handler(
            config={"name": "futureagi", "services": {"app": {"image": "futureagi/standalone:v1.0.0"},
                                                      "postgres": {"image": "postgres:16"}}},
            containers=[app], images={"sha256:app": image_record("sha256:app", version="v1.0.0")}))
        (self.root / ".env").write_text("FUTURE_AGI_VERSION=v1.0.0\n")
        self.Installation.side_effect = functools.partial(Installation, runner=runner, compose=COMPOSE, environ={})
        code, out, err = self.main("status")
        self.assertEqual(code, 0, err)
        self.assertIn("Installation  Standalone (Compose project futureagi, .env)", out)
        self.assertRegex(out, r"app +futureagi/standalone:v1\.0\.0 +running v1\.0\.0 +\(FUTURE_AGI_VERSION=v1\.0\.0\)")
        self.assertNotIn("postgres", out)

    def test_real_installation_without_env_exits_2(self):
        self.Installation.side_effect = functools.partial(Installation, runner=Runner(), compose=COMPOSE, environ={})
        code, out, err = self.main("check")
        self.assertEqual(code, 2)
        self.assertEqual(err, f"bin/update: no .env in {self.root}: run bin/update from the checkout ./bin/install "
                              "set up\n")
        self.assertEqual(out, "")

    def test_real_fetch_reads_manifest_relative_to_cwd_and_cleans_up(self):
        from deploy.update.trust import fetch_manifest as real_fetch
        self.fetch.side_effect = real_fetch
        manifest = valid_manifest()
        (self.cwd / "rel").mkdir()
        (self.cwd / "rel" / "m.json").write_text(json.dumps(manifest))
        (self.cwd / "rel" / "future-agi-update-manifest.sigstore.json").write_text("{}")
        seen = {}

        def plan(data, verification, installation, settings, **kwargs):
            seen["data"] = data
            seen["files"] = sorted(p.name for p in self.workdir.iterdir())
            return self.plan

        self.build_plan.side_effect = plan
        code, _, err = self.main("check", "--manifest", "rel/m.json")
        self.assertEqual(code, 0, err)
        self.assertEqual(seen["data"], manifest)
        self.assertEqual(seen["files"], ["bundle.json", "manifest.json"])
        self.assertFalse(self.workdir.exists())


# ================================================ bin/update (subprocess) ====

PLANTED_MAIN = (
    "import os, sys\n"
    "open(os.environ.get('PLANT_MARKER', '/dev/null'), 'a').write('planted deploy.update ran\\n')\n"
    "print('PLANTED')\n"
    "sys.exit(42)\n")


def plant_deploy_package(directory):
    """A regular `deploy` package whose deploy.update records that it ran."""

    package = directory / "deploy" / "update"
    package.mkdir(parents=True)
    (directory / "deploy" / "__init__.py").write_text(
        "import os\nopen(os.environ.get('PLANT_MARKER', '/dev/null'), 'a').write('planted deploy imported\\n')\n")
    (package / "__init__.py").write_text("")
    (package / "__main__.py").write_text(PLANTED_MAIN)


@unittest.skipIf(shutil.which("bash") is None, "bin/update needs bash")
class BinUpdateScriptTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.checkout = self.base / "check out"
        (self.checkout / "bin").mkdir(parents=True)
        shutil.copy2(ROOT / "bin" / "update", self.checkout / "bin" / "update")
        shutil.copytree(ROOT / "deploy" / "update", self.checkout / "deploy" / "update",
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        self.caller = self.base / "caller dir"
        self.caller.mkdir()
        self.marker = self.base / "marker.log"
        self.env = clean_environ()
        self.env.update({"PYTHON": sys.executable, "PLANT_MARKER": str(self.marker)})

    def run_update(self, *args, env=None, script=None, cwd=None):
        return subprocess.run([str(script or self.checkout / "bin" / "update"), *args], cwd=str(cwd or self.caller),
                              env=self.env if env is None else env, capture_output=True, text=True, timeout=120)

    def assertClean(self, result):
        self.assertNotIn("Traceback", result.stderr)
        self.assertNotIn("Traceback", result.stdout)

    def test_repository_script_is_executable(self):
        script = ROOT / "bin" / "update"
        self.assertTrue(os.access(script, os.X_OK))
        self.assertTrue(script.read_text().startswith("#!/usr/bin/env bash\n"))

    def test_repository_script_help_from_another_directory(self):
        result = self.run_update("--help", script=ROOT / "bin" / "update")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("usage: bin/update", result.stdout)
        self.assertIn("Check for, apply and roll back signed Future AGI releases", result.stdout)

    def test_help_from_another_directory(self):
        result = self.run_update("--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("usage: bin/update", result.stdout)
        for command in ("check", "apply", "rollback", "status"):
            self.assertIn(command, result.stdout)
        self.assertNotIn("--root", result.stdout)
        self.assertNotIn("--cwd", result.stdout)
        self.assertFalse((self.caller / STATE_DIR).exists())
        self.assertFalse((self.checkout / STATE_DIR).exists())

    def test_subcommand_help(self):
        result = self.run_update("check", "--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        for option in ("--manifest", "--bundle", "--policy", "--channel", "--format", "--production", "--env-file"):
            self.assertIn(option, result.stdout)

    def test_usage_errors_exit_2(self):
        for args in ((), ("bogus",), ("check", "--policy", "huge")):
            with self.subTest(args=args):
                result = self.run_update(*args)
                self.assertEqual(result.returncode, 2)
                self.assertIn("usage: bin/update", result.stderr)
                self.assertClean(result)

    def test_status_without_env_is_a_clean_error(self):
        for command in ("status", "check", "apply", "rollback"):
            with self.subTest(command=command):
                result = self.run_update(command)
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertEqual(result.stderr, f"bin/update: no .env in {os.path.realpath(self.checkout)}: run "
                                                "bin/update from the checkout ./bin/install set up\n")
                self.assertEqual(result.stdout, "")
                self.assertFalse((self.checkout / STATE_DIR).exists())
                self.assertFalse((self.caller / STATE_DIR).exists())

    def test_env_in_callers_directory_is_not_used(self):
        (self.caller / ".env").write_text("FUTURE_AGI_VERSION=v1.0.0\n")
        result = self.run_update("status")
        self.assertEqual(result.returncode, 2)
        self.assertIn("no .env in", result.stderr)

    def test_relative_env_file_is_resolved_against_the_callers_directory(self):
        (self.checkout / DISTRIBUTED_FILE).write_text("services: {}\n")
        (self.checkout / "deploy" / "docker-compose.production.yml").write_text("services: {}\n")
        result = self.run_update("--production", "--env-file", "prod.env", "status")
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn(f"bin/update: {os.path.realpath(self.caller)}/prod.env does not exist.", result.stderr)
        self.assertClean(result)

    def test_invalid_update_conf_is_a_clean_error(self):
        (self.checkout / "update.conf").write_text("NOT A SETTING\n")
        result = self.run_update("check")
        self.assertEqual(result.returncode, 2)
        self.assertIn("update.conf:1: expected KEY=value", result.stderr)
        self.assertClean(result)

    def test_exported_version_variable_is_a_clean_error(self):
        (self.checkout / ".env").write_text("")
        result = self.run_update("status", env=dict(self.env, FUTURE_AGI_VERSION="v1.0.0"))
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stderr, "bin/update: FUTURE_AGI_VERSION is exported in your shell and would override "
                                        ".env for docker compose. Unset them and try again\n")

    def test_python_older_than_3_9_is_refused(self):
        fake = self.base / "fake python"
        fake.write_text(
            "#!/bin/sh\n"
            "echo \"$*\" >> \"$PLANT_MARKER\"\n"
            "case \"$1\" in\n"
            "  -V|--version) echo 'Python 3.8.18'; exit 0 ;;\n"
            "  -c) exit 1 ;;\n"
            "esac\n"
            "exit 99\n")
        fake.chmod(0o755)
        result = self.run_update("status", env=dict(self.env, PYTHON=str(fake)))
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(result.stderr, f"bin/update: needs Python 3.9 or newer; {fake} is Python 3.8.18\n")
        calls = self.marker.read_text().splitlines()
        self.assertTrue(calls)
        self.assertFalse(any(" -m " in f" {call} " or call.startswith("-E") for call in calls), calls)

    def test_missing_python_is_refused(self):
        result = self.run_update("status", env=dict(self.env, PYTHON=str(self.base / "no-such-python3")))
        self.assertEqual(result.returncode, 2)
        self.assertIn("bin/update: needs Python 3.9 or newer on the Docker host", result.stderr)

    def test_default_python_is_python3_from_path(self):
        shim = self.base / "shim"
        shim.mkdir()
        (shim / "python3").symlink_to(sys.executable)
        env = {k: v for k, v in self.env.items() if k != "PYTHON"}
        env["PATH"] = str(shim) + os.pathsep + env.get("PATH", os.defpath)
        result = self.run_update("--help", env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("usage: bin/update", result.stdout)

    def test_deploy_package_in_callers_directory_is_not_imported(self):
        plant_deploy_package(self.caller)
        (self.caller / "json.py").write_text(
            "import os\nopen(os.environ['PLANT_MARKER'], 'a').write('planted json imported\\n')\n")
        (self.caller / "argparse.py").write_text(
            "import os\nopen(os.environ['PLANT_MARKER'], 'a').write('planted argparse imported\\n')\n")
        for env in (self.env, dict(self.env, PYTHONPATH=str(self.caller))):
            with self.subTest(pythonpath=env.get("PYTHONPATH")):
                result = self.run_update("--help", env=env)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("usage: bin/update", result.stdout)
                self.assertNotIn("PLANTED", result.stdout)
                self.assertFalse(self.marker.exists(), self.marker.read_text() if self.marker.exists() else "")

    def test_deploy_package_in_the_interpreters_site_packages_does_not_shadow_the_checkout(self):
        # The checkout's deploy/ has no __init__.py, so it is a namespace
        # package. Python's import system lets a regular `deploy` package
        # anywhere later on sys.path (site-packages: -E -s do not remove it)
        # win over a namespace portion, so bin/update would run that
        # package's deploy.update instead of the checkout's.
        venv = self.base / "venv"
        made = subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(venv)], capture_output=True,
                              text=True, timeout=120)
        if made.returncode != 0:
            self.skipTest(f"cannot create a virtual environment: {made.stderr.strip()}")
        python = venv / "bin" / "python"
        purelib = subprocess.run([str(python), "-c", "import sysconfig; print(sysconfig.get_paths()['purelib'])"],
                                 capture_output=True, text=True, timeout=60, check=True).stdout.strip()
        plant_deploy_package(Path(purelib))
        result = self.run_update("--help", env=dict(self.env, PYTHON=str(python)))
        self.assertNotIn("PLANTED", result.stdout)
        self.assertFalse(self.marker.exists(), self.marker.read_text() if self.marker.exists() else "")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("usage: bin/update", result.stdout)


# ==================================== install.py additions (install owner) ====

DAEMON_HINT = ". Docker is not running or not reachable (check DOCKER_HOST and docker context ls)"


class InstallModeAdditions(TempRootTest):
    def test_no_env_but_production_env_points_at_production(self):
        self.write(PRODUCTION_ENV, "FUTURE_AGI_VERSION=v1.0.0\n")
        with self.assertRaises(InstallError) as caught:
            Installation(self.root, environ={}, runner=Runner(), compose=COMPOSE)
        # Round 5 (T17): the command names a bin/update command (one is required), so it works pasted.
        self.assertEqual(str(caught.exception),
                         f"no .env in {self.root}, but deploy/.env.production exists: this looks like a "
                         "deploy/setup.sh production install. Add --production to the command, as in "
                         "`./bin/update --production check`")

    def test_env_next_to_production_env_is_a_normal_install(self):
        self.write(PRODUCTION_ENV, "")
        self.assertEqual(self.install("COMPOSE_FILE=docker-compose.distributed.yml\n").mode, "distributed")

    def test_empty_shell_separator_wins_over_env_file_and_means_the_default(self):
        # Compose takes the shell's COMPOSE_PATH_SEPARATOR even when empty,
        # and an empty separator means the OS default.
        inst = self.install("COMPOSE_PATH_SEPARATOR=;\nCOMPOSE_FILE=docker-compose.distributed.yml"
                            f"{os.pathsep}docker-compose.override.yml\n", environ={"COMPOSE_PATH_SEPARATOR": ""})
        self.assertEqual(inst.mode, "distributed")

    def test_env_file_separator_is_not_used_when_the_shell_sets_one(self):
        with self.assertRaises(InstallError) as caught:
            self.install("COMPOSE_PATH_SEPARATOR=;\n"
                         "COMPOSE_FILE=docker-compose.distributed.yml;docker-compose.override.yml\n",
                         environ={"COMPOSE_PATH_SEPARATOR": ","})
        self.assertIn("names neither docker-compose.yml nor docker-compose.distributed.yml", str(caught.exception))


class InstallContainersAdditions(TempRootTest):
    def inst(self, **handler):
        return self.install("", runner=Runner(docker_handler(**handler)))

    def test_failed_inspect_is_described(self):
        app = container_record("app", cid="ab" * 32)
        inst = self.inst(containers=[app], inspect=completed(1, "[]\n", "Error response from daemon: boom\n"))
        with self.assertRaises(InstallError) as caught:
            inst.containers()
        self.assertEqual(str(caught.exception),
                         "`docker inspect abababababab` failed with exit code 1: Error response from daemon: boom")

    def test_failed_inspect_with_unreachable_daemon_gets_the_hint(self):
        app = container_record("app", cid="ab" * 32)
        inst = self.inst(containers=[app], inspect=completed(
            1, "", "Cannot connect to the Docker daemon at unix:///var/run/docker.sock. Is the docker daemon running?"))
        with self.assertRaises(InstallError) as caught:
            inst.containers()
        self.assertEqual(str(caught.exception),
                         "`docker inspect abababababab` failed with exit code 1: Cannot connect to the Docker daemon "
                         "at unix:///var/run/docker.sock. Is the docker daemon running? Docker is not running or not "
                         "reachable (check DOCKER_HOST and docker context ls)")

    def test_inspect_output_that_is_not_json(self):
        app = container_record("app")
        for stdout in ("", "{truncated", "not json"):
            with self.subTest(stdout=stdout):
                with self.assertRaises(InstallError) as caught:
                    self.inst(containers=[app], inspect=completed(0, stdout)).containers()
                self.assertTrue(str(caught.exception).startswith("docker inspect returned invalid JSON: "),
                                str(caught.exception))

    def test_inspect_output_that_is_not_a_list_of_containers(self):
        app = container_record("app")
        for payload in ({"Id": app["Id"]}, [app, "x"], [None], "text", 3):
            with self.subTest(payload=payload):
                with self.assertRaises(InstallError) as caught:
                    self.inst(containers=[app], inspect=completed(0, json.dumps(payload))).containers()
                self.assertEqual(str(caught.exception), "docker inspect did not return a list of containers")

    def test_container_removed_between_ps_and_inspect_is_left_out(self):
        app, gone = container_record("app"), container_record("worker")
        for message in (f"Error: No such object: {gone['Id']}", f"error: no such object: {gone['Id']}",
                        f"Error response from daemon: No such container: {gone['Id']}"):
            with self.subTest(message=message):
                inst = self.inst(ps=completed(0, f"{app['Id']}\n{gone['Id']}\n"),
                                 inspect=completed(1, json.dumps([app]), message + "\n"))
                self.assertEqual([c["service"] for c in inst.containers()], ["app"])

    def test_every_listed_container_removed_is_an_empty_project(self):
        gone = container_record("app")
        inst = self.inst(ps=completed(0, gone["Id"] + "\n"),
                         inspect=completed(1, "[]\n", f"Error: No such object: {gone['Id']}\n"))
        self.assertEqual(inst.containers(), [])

    def test_other_inspect_errors_alongside_a_removed_container_are_errors(self):
        app, gone = container_record("app"), container_record("worker")
        for stderr in (f"Error: No such object: {gone['Id']}\npermission denied\n",
                       "Error: No such object: some-other-container\n", "Error: No such object\n"):
            with self.subTest(stderr=stderr):
                inst = self.inst(ps=completed(0, f"{app['Id']}\n{gone['Id']}\n"),
                                 inspect=completed(1, json.dumps([app]), stderr))
                with self.assertRaises(InstallError):
                    inst.containers()

    def test_failed_inspect_with_unparsable_output_is_described_not_called_invalid_json(self):
        app = container_record("app", cid="cd" * 32)
        inst = self.inst(containers=[app], inspect=completed(1, "", "Error response from daemon: boom"))
        with self.assertRaises(InstallError) as caught:
            inst.containers()
        self.assertTrue(str(caught.exception).startswith("`docker inspect cdcdcdcdcdcd` failed with exit code 1"))


class InstallDescribeFailureAdditions(unittest.TestCase):
    PRODUCTION = ["--env-file", "/srv/fagi/deploy/.env.production", "-f", "/srv/fagi/docker-compose.distributed.yml",
                  "-f", "/srv/fagi/deploy/docker-compose.production.yml"]

    def describe(self, args, returncode=1, stderr="x"):
        return Installation.describe_failure(CommandResult(list(args), returncode, "", stderr))

    def label(self, args):
        return self.describe(args).split("`")[1]

    def test_options_and_their_values_are_left_out(self):
        cases = [
            (["docker", "compose", *self.PRODUCTION, "up", "-d", "--no-build", "--wait", "--wait-timeout", "300"],
             "docker compose up"),
            (["docker", "compose", "up", "-d", "--no-build"], "docker compose up"),
            (["docker", "compose", "config", "--format", "json"], "docker compose config"),
            (["docker", "compose", "config", "--quiet"], "docker compose config"),
            (["docker", "compose", "logs", "--no-color", "--tail", "2000", "app"], "docker compose logs app"),
            (["docker", "compose", "logs", "-n", "50", "app"], "docker compose logs app"),
            (["docker", "compose", "--project-name", "acme", "pull", "--quiet", "app", "worker"],
             "docker compose pull app"),
            (["docker", "compose", "-p", "acme", "--profile", "gpu", "pull", "serving"], "docker compose pull serving"),
            (["docker", "compose", "--env-file=/x/.env", "up", "--wait-timeout=300", "-d", "app"],
             "docker compose up app"),
            (["docker", "ps", "-a", "-q", "--no-trunc", "--filter", "label=com.docker.compose.project=x"], "docker ps"),
            (["docker", "ps", "-f", "label=x", "--format", "{{.ID}}"], "docker ps"),
            (["docker", "inspect", "--format", "{{json .}}", "abc"], "docker inspect abc"),
            (["docker", "inspect", "-f", "{{.Id}}", "abc"], "docker inspect abc"),
            (["docker", "--context", "remote", "-H", "tcp://h:2376", "ps", "-a"], "docker ps"),
            (["docker", "image", "inspect", "futureagi/standalone:v1.2.3"],
             "docker image inspect futureagi/standalone:v1.2.3"),
        ]
        for args, label in cases:
            with self.subTest(args=args):
                self.assertEqual(self.label(args), label)

    def test_the_program_is_always_named(self):
        cases = [
            (["/usr/local/bin/docker-compose", *self.PRODUCTION, "logs", "--no-color", "--tail", "2000", "app"],
             "docker-compose logs app"),
            (["docker-compose", "pull", "backend", "worker", "frontend"], "docker-compose pull backend worker"),
            (["/usr/bin/docker", "compose", "-f", "a.yml", "up"], "docker compose up"),
            (["docker", "--context", "remote", "compose", "-f", "a.yml", "up", "-d"], "docker compose up"),
            (["docker", "compose", *self.PRODUCTION], "docker compose"),
            (["/usr/bin/docker", "version"], "docker version"),
        ]
        for args, label in cases:
            with self.subTest(args=args):
                self.assertEqual(self.label(args), label)

    def test_at_most_four_words(self):
        self.assertEqual(self.label(["docker", "inspect", "a", "b", "c", "d"]), "docker inspect a b")
        self.assertEqual(self.label(["docker", "compose", "pull", "a", "b"]), "docker compose pull a")

    def test_container_ids_are_shortened(self):
        self.assertEqual(self.label(["docker", "inspect", "ab" * 32, "cd" * 32]),
                         "docker inspect abababababab cdcdcdcdcdcd")
        self.assertEqual(self.label(["docker", "inspect", "short", "x" * 64]), "docker inspect short " + "x" * 64)

    def test_unreachable_daemon_gets_a_hint(self):
        for stderr, detail in (
                ("Cannot connect to the Docker daemon at unix:///var/run/docker.sock",
                 ": Cannot connect to the Docker daemon at unix:///var/run/docker.sock" + DAEMON_HINT),
                ("Cannot connect to the Docker daemon at unix:///var/run/docker.sock. Is the docker daemon running?\n",
                 ": Cannot connect to the Docker daemon at unix:///var/run/docker.sock. Is the docker daemon "
                 "running?" + DAEMON_HINT[1:]),
                ("failed to connect to the docker API at unix:///var/run/docker.sock; check if the path is correct "
                 "and if the daemon is running: dial unix /var/run/docker.sock: connect: no such file or directory",
                 ": failed to connect to the docker API at unix:///var/run/docker.sock; check if the path is correct "
                 "and if the daemon is running: dial unix /var/run/docker.sock: connect: no such file or directory"
                 + DAEMON_HINT),
                ('Failed to initialize: unable to resolve docker endpoint: context "remote": context not found: '
                 "open /root/.docker/contexts/meta/x/meta.json: no such file or directory",
                 ': Failed to initialize: unable to resolve docker endpoint: context "remote": context not found: '
                 "open /root/.docker/contexts/meta/x/meta.json: no such file or directory" + DAEMON_HINT),
                ('context "remote" does not exist', ': context "remote" does not exist' + DAEMON_HINT),
                ("Is the docker daemon running?\nexit status 1", ": exit status 1" + DAEMON_HINT),
        ):
            with self.subTest(stderr=stderr):
                self.assertEqual(self.describe(["docker", "ps"], 1, stderr),
                                 "`docker ps` failed with exit code 1" + detail)

    def test_other_failures_get_no_daemon_hint(self):
        for stderr in ("Error response from daemon: pull access denied for futureagi/x",
                       'unable to prepare context: path "./x" not found',
                       "service \"app\" refers to undefined network", "unauthorized: authentication required"):
            with self.subTest(stderr=stderr):
                self.assertNotIn("Docker is not running", self.describe(["docker", "compose", "pull"], 1, stderr))
        self.assertEqual(self.describe(["docker", "compose", "pull"], 1, ""),
                         "`docker compose pull` failed with exit code 1 (output above)")

    def test_split_command(self):
        from deploy.update.install import split_command
        self.assertEqual(split_command([]), ([], []))
        self.assertEqual(split_command(["docker", "compose", *self.PRODUCTION, "up", "-d"]),
                         (["docker", "compose"], ["up", "-d"]))
        self.assertEqual(split_command(["/opt/docker-compose", "-p", "x", "pull", "-q"]),
                         (["docker-compose"], ["pull", "-q"]))
        self.assertEqual(split_command(["docker", "-c", "remote", "inspect", "-f", "x", "y"]),
                         (["docker"], ["inspect", "-f", "x", "y"]))
        self.assertEqual(split_command(["docker", "--debug", "--", "ps"]), (["docker"], ["ps"]))


class InstallRunAdditions(TempRootTest):
    def timeout_message(self, inst, *args, timeout=30.4):
        with self.assertRaises(InstallError) as caught:
            inst.run(inst.compose_args(*args), timeout=timeout)
        self.assertIsInstance(caught.exception.__cause__, subprocess.TimeoutExpired)
        return str(caught.exception)

    @staticmethod
    def timing_out(args, kwargs):
        raise subprocess.TimeoutExpired(args, kwargs["timeout"])

    def test_production_timeout_names_the_command_not_the_env_file(self):
        self.production_files()
        inst = Installation(self.root, production=True, environ={}, runner=Runner(self.timing_out), compose=COMPOSE)
        self.assertEqual(self.timeout_message(inst, "up", "-d", "--wait", "--wait-timeout", "60"),
                         "`docker compose up -d ...` timed out after 30s")

    def test_short_command_timeout_has_no_ellipsis(self):
        inst = self.install("", runner=Runner(self.timing_out))
        self.assertEqual(self.timeout_message(inst, "pull", timeout=5), "`docker compose pull` timed out after 5s")
        inst = self.install("", runner=Runner(self.timing_out), compose=["/opt/bin/docker-compose"])
        self.assertEqual(self.timeout_message(inst, "pull", "app", "worker", "beat"),
                         "`docker-compose pull app worker ...` timed out after 30s")


class InstallOverlayEnvAdditions(TempRootTest):
    def test_overlay_updates_env_and_subprocess_env(self):
        inst = self.install("FUTURE_AGI_VERSION=v1.0.0\nFRONTEND_VERSION=v1.0.0\nSECRET_KEY=s\n",
                            environ={"PATH": "/bin"})
        inst.overlay_env({"FUTURE_AGI_VERSION": "v1.2.0", "FRONTEND_VERSION": None, "SERVING_VERSION": "v1.2.0"})
        self.assertEqual(inst.env, {"FUTURE_AGI_VERSION": "v1.2.0", "SECRET_KEY": "s", "SERVING_VERSION": "v1.2.0"})
        self.assertEqual(inst.subprocess_env(), {"PATH": "/bin", "FUTURE_AGI_VERSION": "v1.2.0",
                                                 "FRONTEND_VERSION": "", "SERVING_VERSION": "v1.2.0"})
        self.assertEqual(inst.env_overlay, {"FUTURE_AGI_VERSION": "v1.2.0", "FRONTEND_VERSION": None,
                                            "SERVING_VERSION": "v1.2.0"})
        self.assertEqual(inst.environ, {"PATH": "/bin"})

    def test_overlay_is_never_written(self):
        text = "FUTURE_AGI_VERSION=v1.0.0\nFRONTEND_VERSION=v1.0.0\n"
        inst = self.install(text)
        inst.overlay_env({"FUTURE_AGI_VERSION": "v1.2.0", "FRONTEND_VERSION": None})
        self.assertEqual(inst.env_file.read_text(encoding="utf-8"), text)

    def test_later_overlays_add_to_and_replace_earlier_ones(self):
        inst = self.install("FUTURE_AGI_VERSION=v1.0.0\n")
        inst.overlay_env({"FUTURE_AGI_VERSION": None, "FRONTEND_VERSION": "v1.1.0"})
        inst.overlay_env({"FUTURE_AGI_VERSION": "v1.2.0"})
        self.assertEqual(inst.env, {"FUTURE_AGI_VERSION": "v1.2.0", "FRONTEND_VERSION": "v1.1.0"})
        self.assertEqual(inst.subprocess_env(), {"FUTURE_AGI_VERSION": "v1.2.0", "FRONTEND_VERSION": "v1.1.0"})

    def test_empty_overlay_changes_nothing_but_the_cached_config(self):
        inst = self.install("FUTURE_AGI_VERSION=v1.0.0\n", environ={"PATH": "/bin"})
        inst.overlay_env({})
        self.assertEqual(inst.env, {"FUTURE_AGI_VERSION": "v1.0.0"})
        self.assertEqual(inst.subprocess_env(), {"PATH": "/bin"})

    def test_overlay_drops_the_cached_config_and_compose_sees_it(self):
        payloads = [completed(0, '{"name": "before"}'), completed(0, '{"name": "after"}')]
        runner = Runner(lambda args, kwargs: payloads.pop(0))
        inst = self.install("FUTURE_AGI_VERSION=v1.0.0\n", runner=runner, environ={"PATH": "/bin"})
        self.assertEqual(inst.config()["name"], "before")
        inst.overlay_env({"FUTURE_AGI_VERSION": "v1.2.0", "FRONTEND_VERSION": None})
        self.assertEqual(inst.config()["name"], "after")
        self.assertEqual(inst.config()["name"], "after")
        self.assertEqual(len(runner.calls), 2)
        first, second = (kwargs["env"] for _, kwargs in runner.calls)
        self.assertEqual(first, {"PATH": "/bin"})
        self.assertEqual(second, {"PATH": "/bin", "FUTURE_AGI_VERSION": "v1.2.0", "FRONTEND_VERSION": ""})

    def test_production_overlay_is_added_after_the_stripping(self):
        self.production_files("FUTURE_AGI_VERSION=v1.0.0\nFRONTEND_VERSION=v1.0.0\nPG_PASSWORD=file\n")
        environ = {"PATH": "/bin", "FUTURE_AGI_VERSION": "v0.0.1", "PG_PASSWORD": "shell", "SERVING_VERSION": "v0"}
        inst = Installation(self.root, production=True, environ=environ, runner=Runner(), compose=COMPOSE)
        inst.overlay_env({"FUTURE_AGI_VERSION": "v1.2.0", "FRONTEND_VERSION": None})
        self.assertEqual(inst.subprocess_env(), {"PATH": "/bin", "FUTURE_AGI_VERSION": "v1.2.0",
                                                 "FRONTEND_VERSION": ""})
        self.assertEqual(inst.env, {"FUTURE_AGI_VERSION": "v1.2.0", "PG_PASSWORD": "file"})
        self.assertEqual(read_env_file(inst.env_file),
                         {"FUTURE_AGI_VERSION": "v1.0.0", "FRONTEND_VERSION": "v1.0.0", "PG_PASSWORD": "file"})

    def test_overlay_values_are_copied(self):
        inst = self.install("")
        values = {"FUTURE_AGI_VERSION": "v1.2.0"}
        inst.overlay_env(values)
        values["FUTURE_AGI_VERSION"] = "v9"
        self.assertEqual(inst.subprocess_env()["FUTURE_AGI_VERSION"], "v1.2.0")


def read_env_file(path):
    from deploy.update.envfile import read_env
    return read_env(path)


# ======================================= CLI additions (__main__ / bin/update) ====

JOURNAL_NOTE = "an interrupted bin/update run will be recovered by the next apply or rollback"
DAEMON_DOWN = ("`docker ps label=com.docker.compose.project=futureagi` failed with exit code 1: Cannot connect to "
               "the Docker daemon at unix:///var/run/docker.sock")

# Every option, the commands that take it, and argv for it.
CLI_OPTIONS = (
    ("--production", ("check", "apply", "rollback", "status"), ["--production"]),
    ("--env-file", ("check", "apply", "rollback", "status"), ["--env-file", "p.env"]),
    ("--manifest", ("check", "apply"), ["--manifest", "m.json"]),
    ("--bundle", ("check", "apply"), ["--bundle", "b.json"]),
    ("--policy", ("check", "apply"), ["--policy", "major"]),
    ("--channel", ("check", "apply"), ["--channel", "beta"]),
    ("--format", ("check", "status"), ["--format", "json"]),
    ("--backup-command", ("apply",), ["--backup-command", "/bin/backup"]),
    ("--allow-no-backup", ("apply",), ["--allow-no-backup"]),
    ("--dry-run", ("apply",), ["--dry-run"]),
    ("--receipt", ("rollback",), ["--receipt", "r.json"]),
    ("--yes", ("rollback",), ["--yes"]),
)


class CliParserAdditions(unittest.TestCase):
    def parse(self, argv):
        stderr = io.StringIO()
        try:
            with contextlib.redirect_stderr(stderr):
                return cli.build_parser().parse_args(argv)
        except SystemExit as exc:
            self.fail(f"{argv} was rejected (exit {exc.code}): {stderr.getvalue().strip().splitlines()[-1:]}")

    def rejection(self, argv):
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            with self.assertRaises(SystemExit) as caught:
                cli.build_parser().parse_args(argv)
        self.assertEqual(caught.exception.code, 2)
        return stderr.getvalue()

    def test_every_option_before_or_after_every_command_that_takes_it(self):
        for flag, commands, option_args in CLI_OPTIONS:
            dest = flag[2:].replace("-", "_")
            expected = option_args[1] if len(option_args) > 1 else True
            for command in commands:
                for argv in ([*option_args, command], [command, *option_args]):
                    with self.subTest(argv=argv):
                        namespace = self.parse(argv)
                        self.assertEqual(namespace.command, command)
                        self.assertEqual(getattr(namespace, dest), expected)

    def test_option_a_command_does_not_take_is_an_error_naming_it_in_either_position(self):
        for flag, commands, option_args in CLI_OPTIONS:
            for command in set(cli.COMMANDS) - set(commands):
                for argv in ([*option_args, command], [command, *option_args]):
                    with self.subTest(argv=argv):
                        err = self.rejection(argv)
                        self.assertIn(f"usage: bin/update {command}", err)
                        self.assertIn(f"bin/update {command}: error: argument {flag}: not allowed with {command} "
                                      f"(only {' and '.join(commands)} take", err)

    def test_rejection_message_wording(self):
        self.assertIn("argument --manifest: not allowed with status (only check and apply take it)\n",
                      self.rejection(["--manifest", "m", "status"]))
        self.assertIn("argument --backup-command: not allowed with check (only apply takes it)\n",
                      self.rejection(["check", "--backup-command", "x"]))

    def test_command_defaults_when_not_given(self):
        self.assertEqual(self.parse(["check"]).format, "text")
        namespace = self.parse(["apply"])
        self.assertEqual((namespace.allow_no_backup, namespace.dry_run), (False, False))
        namespace = self.parse(["rollback"])
        self.assertEqual((namespace.receipt, namespace.yes), (None, False))
        namespace = self.parse(["status"])
        for name in ("format", "allow_no_backup", "dry_run", "receipt", "yes", "manifest", "backup_command"):
            with self.subTest(option=name):
                self.assertFalse(hasattr(namespace, name))

    def test_value_given_before_the_command_is_not_reset_by_its_default(self):
        self.assertEqual(self.parse(["--format", "json", "check"]).format, "json")
        self.assertTrue(self.parse(["--dry-run", "apply"]).dry_run)
        namespace = self.parse(["--yes", "--receipt", "r.json", "rollback"])
        self.assertEqual((namespace.receipt, namespace.yes), ("r.json", True))

    def test_value_after_the_command_wins_over_one_before(self):
        self.assertEqual(self.parse(["--manifest", "a.json", "check", "--manifest", "b.json"]).manifest, "b.json")

    def test_unknown_option_is_still_unrecognized(self):
        self.assertIn("unrecognized arguments: --bogus", self.rejection(["check", "--bogus"]))

    def test_help_lists_each_command_its_own_options(self):
        parser = cli.build_parser()
        top = parser.format_help()
        for flag in ("--production", "--env-file"):
            self.assertIn(flag, top)
        for flag in ("--manifest", "--format", "--backup-command", "--receipt", "--yes", "--root", "--cwd"):
            self.assertNotIn(flag, top)
        self.assertIn("Options may come before or after COMMAND", top)
        for flag, commands, _ in CLI_OPTIONS:
            for command in cli.COMMANDS:
                with self.subTest(command=command, flag=flag):
                    text = parser.commands[command].format_help()
                    (self.assertIn if command in commands else self.assertNotIn)(flag, text)


class CliMainAdditions(unittest.TestCase):
    setUp = MainTests.setUp
    patch = MainTests.patch
    main = MainTests.main

    NOTE = "the latest GitHub release has no update manifest; using v1.1.0, the newest release that has one"
    FALLBACK = "https://github.com/future-agi/future-agi/releases/download/v1.1.0/future-agi-update-manifest.json"

    def with_overlay(self):
        """FakeInstallations get Installation.overlay_env (contract C1), recording each call."""

        def configure(inst):
            inst.overlays = []

            def overlay_env(values):
                inst.overlays.append(dict(values))
                for key, value in values.items():
                    if value is None:
                        inst.env.pop(key, None)
                    else:
                        inst.env[key] = value
            inst.overlay_env = overlay_env
        self.configure_installation = configure
        seen = {}

        def plan(data, verification, installation, settings, **kwargs):
            seen["env"] = dict(installation.env)
            seen["kwargs"] = kwargs
            return self.plan
        self.build_plan.side_effect = plan
        return seen

    def journal(self, **fields):
        record = {'kind': 'apply', 'target_version': 'v1.2.0', 'stage': 'pulled', 'env_file': '.env',
                  'production': False, 'env_changes': {'FUTURE_AGI_VERSION': 'v1.2.0'}}
        record.update(fields)
        if record['stage'] in ('prepare', 'env_written', 'pulled') and 'env_recovery' not in fields:
            values = record.get('found_values', record.get('previous_values', {}))
            path = self.root / record['env_file']
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(''.join(f'{key}={value}\n' for key, value in values.items() if value is not None))
            record['env_changes'] = {key: record['env_changes'].get(key, 'v1.2.0') for key in values}
            record['env_recovery'] = envfile_module.recovery_record(path, record['env_changes'])
            envfile_module.write_changes(path, record['env_changes'], recovery=record['env_recovery'])
        StateDir(self.root).write_journal(record)
        return record

    # ------------------------------------------------- manifest source --
    def test_manifest_source_is_where_the_manifest_was_read(self):
        self.fetched_from = self.FALLBACK
        code, _, _ = self.main("check")
        self.assertEqual(code, 0)
        self.assertEqual(self.fetch_calls[0]["source"], DEFAULT_MANIFEST_URL)
        self.assertEqual(self.build_plan.call_args[1]["manifest_source"], self.FALLBACK)

    def test_apply_plans_with_the_source_read(self):
        self.fetched_from = self.FALLBACK
        self.main("apply")
        make_plan = self.Updater.return_value.apply.call_args[0][0]
        make_plan(None)
        self.assertEqual(self.build_plan.call_args[1]["manifest_source"], self.FALLBACK)

    def test_fallback_note_on_stderr_and_in_the_text_plan(self):
        self.fetched.note = self.NOTE
        code, out, err = self.main("check")
        self.assertEqual(code, 0)
        self.assertEqual(err, f"Note: {self.NOTE}\n")
        self.assertIn(f"· {self.NOTE}", out)

    def test_fallback_note_is_said_even_when_verification_fails(self):
        self.fetched.note = self.NOTE
        self.Verifier.return_value.verify.side_effect = VerificationError("cosign is not installed")
        code, out, err = self.main("check")
        self.assertEqual((code, out), (2, ""))
        self.assertEqual(err, f"Note: {self.NOTE}\nbin/update: cosign is not installed\n")

    def test_fallback_note_in_the_json_plan_only(self):
        self.fetched.note = self.NOTE
        code, out, err = self.main("check", "--format", "json")
        self.assertEqual(code, 0)
        self.assertEqual(err, "")
        self.assertIn(self.NOTE, json.loads(out)["notes"])

    def test_fallback_note_for_apply(self):
        self.fetched.note = self.NOTE
        _, _, err = self.main("apply")
        self.assertEqual(err, f"Note: {self.NOTE}\n")
        make_plan = self.Updater.return_value.apply.call_args[0][0]
        self.assertIn(self.NOTE, make_plan(None)["notes"])

    def test_no_note_without_fallback(self):
        _, out, err = self.main("check", "--format", "json")
        self.assertEqual(err, "")
        self.assertEqual(json.loads(out)["notes"], [])

    # ---------------------------------------------------------- options --
    def test_release_and_backup_options_before_the_command(self):
        code, _, err = self.main("--backup-command", "/bin/bk", "--policy", "major", "--channel", "beta",
                                 "--bundle", "b.json", "--manifest", "m.json", "apply")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.Updater.return_value.apply.call_args[1]["backup_command"], "/bin/bk")
        settings = self.Updater.call_args[0][1]
        self.assertEqual((settings.policy, settings.channel), ("major", "beta"))
        self.assertEqual(self.fetch_calls[0]["source"], str(self.cwd / "m.json"))
        self.assertEqual(self.fetch_calls[0]["bundle"], str(self.cwd / "b.json"))

    def test_option_the_command_does_not_take_is_refused_before_anything_runs(self):
        for argv in (("--manifest", "m.json", "rollback", "--yes"), ("--backup-command", "x", "check"),
                     ("status", "--policy", "major")):
            with self.subTest(argv=argv):
                with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
                    cli.main(["--root", str(self.root), "--cwd", str(self.cwd), *argv])
                self.assertEqual(caught.exception.code, 2)
        self.Installation.assert_not_called()
        self.fetch.assert_not_called()

    # ---------------------------------------------------------- signals --
    def test_sigterm_during_apply_exits_130_and_restores_the_handler(self):
        import signal
        before = signal.getsignal(signal.SIGTERM)

        def apply(*args, **kwargs):
            handler = signal.getsignal(signal.SIGTERM)
            self.assertTrue(callable(handler), "SIGTERM is not handled while the command runs")
            handler(signal.SIGTERM, None)
        self.Updater.return_value.apply.side_effect = apply
        code, _, err = self.main("apply")
        self.assertEqual((code, err), (130, "bin/update: interrupted before anything was changed\n"))
        self.assertEqual(signal.getsignal(signal.SIGTERM), before)
        self.assertFalse(self.workdir.exists())

    def test_sighup_while_reading_the_installation_exits_130(self):
        import signal
        if not hasattr(signal, "SIGHUP"):
            self.skipTest("no SIGHUP on this platform")
        # Under nohup the tests inherit SIGHUP ignored, and an ignored SIGHUP stays ignored: start from the default.
        self.addCleanup(signal.signal, signal.SIGHUP, signal.signal(signal.SIGHUP, signal.SIG_DFL))
        before = signal.getsignal(signal.SIGHUP)

        def hang_up(inst):
            signal.getsignal(signal.SIGHUP)(signal.SIGHUP, None)
        self.configure_installation = hang_up
        code, out, err = self.main("status")
        self.assertEqual((code, out, err), (130, "", "bin/update: interrupted before anything was changed\n"))
        self.assertEqual(signal.getsignal(signal.SIGHUP), before)

    def test_interrupted_during_fetch_exits_130_and_removes_the_workdir(self):
        self.fetch_error = cli.Interrupted("SIGTERM")
        code, _, err = self.main("check")
        self.assertEqual((code, err), (130, "bin/update: interrupted before anything was changed\n"))
        self.assertFalse(self.workdir.exists())

    def test_interrupted_from_rollback_exits_130(self):
        self.Updater.return_value.rollback.side_effect = cli.Interrupted("SIGHUP")
        code, _, err = self.main("rollback", "--yes")
        self.assertEqual((code, err), (130, "bin/update: interrupted before anything was changed\n"))

    # ----------------------------------------------- check after a killed run --
    def test_check_plans_with_the_values_recovery_puts_back(self):
        seen = self.with_overlay()
        self.journal(found_values={"FUTURE_AGI_VERSION": "v1.0.5", "FRONTEND_VERSION": None},
                     previous_values={"FUTURE_AGI_VERSION": "v1.0.0"})
        code, out, _ = self.main("check", "--format", "json")
        self.assertEqual(code, 0)
        self.assertEqual(self.installations[0].overlays, [{"FUTURE_AGI_VERSION": "v1.0.5", "FRONTEND_VERSION": None}])
        self.assertEqual(seen["env"]["FUTURE_AGI_VERSION"], "v1.0.5")  # overlaid before the plan was built
        notes = json.loads(out)["notes"]
        self.assertIn(JOURNAL_NOTE, notes)
        self.assertIn("that run stopped before restarting anything; this plan assumes recovery has put back "
                      "FRONTEND_VERSION=unset, FUTURE_AGI_VERSION=v1.0.5", notes)
        last = seen["kwargs"]["history"][-1]
        self.assertEqual((last["status"], last["target_version"]), ("interrupted_before_rollout", "v1.2.0"))
        self.assertIsNotNone(StateDir(self.root).read_journal())

    def test_check_refuses_a_legacy_journal_without_exact_recovery_data(self):
        self.with_overlay()
        self.journal(stage='env_written', previous_values={'FUTURE_AGI_VERSION': 'v1.0.0'}, env_recovery=None)
        before = StateDir(self.root).journal_path.read_bytes()
        code, _, err = self.main('check')
        self.assertEqual(code, 2)
        self.assertIn('exact env recovery', err)
        self.assertEqual(self.installations[0].overlays, [])
        self.assertEqual(StateDir(self.root).journal_path.read_bytes(), before)

    def test_check_with_nothing_to_put_back_overlays_nothing(self):
        self.with_overlay()
        self.journal(stage="prepare", found_values={}, previous_values={"FUTURE_AGI_VERSION": "v1.0.0"})
        code, out, _ = self.main("check", "--format", "json")
        self.assertEqual(code, 0)
        self.assertEqual(self.installations[0].overlays, [])
        self.assertEqual(json.loads(out)["notes"], [JOURNAL_NOTE])

    def test_check_after_a_run_killed_during_the_restart_plans_its_resume(self):
        seen = self.with_overlay()
        self.journal(stage="rollout", found_values={"FUTURE_AGI_VERSION": "v1.0.5"})
        code, out, _ = self.main("check", "--format", "json")
        self.assertEqual(code, 0)
        self.assertEqual(self.installations[0].overlays, [])
        self.assertEqual(seen["env"]["FUTURE_AGI_VERSION"], "v1.1.0")  # FakeInstallation's own value
        self.assertEqual(seen["kwargs"]["history"][-1]["status"], "interrupted_during_rollout")
        self.assertEqual(json.loads(out)["notes"], [JOURNAL_NOTE])

    def test_check_with_a_journal_for_another_env_file_plans_normally(self):
        seen = self.with_overlay()
        receipt = StateDir(self.root).write_receipt({"kind": "apply", "status": "succeeded", "target_version": "v1.1.0",
                                                     "mode": "standalone", "production": False, "env_file": ".env",
                                                     "project": "futureagi"})
        self.journal(env_file="deploy/.env.production", production=True,
                     found_values={"FUTURE_AGI_VERSION": "v1.0.0"})
        code, out, _ = self.main("check", "--format", "json")
        self.assertEqual(code, 0)  # build_plan is a stand-in here: it ignores journal_reason
        self.assertEqual(self.installations[0].overlays, [])
        self.assertEqual(seen["kwargs"]["history"][-1]["_path"], str(receipt))
        notes = json.loads(out)["notes"]
        self.assertNotIn(JOURNAL_NOTE, notes)
        self.assertEqual(notes, [])
        cd = f"cd {shlex.quote(str(self.root))} && "  # bin/update ran from another directory (round 4, F2)
        self.assertEqual(seen["kwargs"]["journal_reason"], (
            "an interrupted bin/update run for deploy/.env.production must be recovered before apply or rollback can "
            f"run for this install: run `{cd}./bin/update --production rollback` (without --yes it only recovers that "
            f"run and changes nothing else), then `{cd}./bin/update check` again; `{cd}./bin/update --production apply` "
            "would recover it too, but would also continue that install's update"))

    def test_journal_hint_names_the_flags_of_the_install_it_belongs_to(self):
        seen = self.with_overlay()
        cd = f"cd {shlex.quote(str(self.root))} && "  # bin/update ran from another directory (round 4, F2)
        for fields, prefix in (({"env_file": "secrets/prod.env", "production": True},
                                f"{cd}./bin/update --production --env-file secrets/prod.env"),
                               ({"env_file": "other.env", "production": False}, f"{cd}./bin/update")):
            with self.subTest(**fields):
                self.journal(**fields)
                self.main("check", "--format", "json")
                self.assertIn(f"run `{prefix} rollback` (without --yes it only recovers that run and changes nothing "
                              f"else), then `{cd}./bin/update check` again; `{prefix} apply` would recover it too",
                              seen["kwargs"]["journal_reason"])
                self.plan = plan_for()

    # ------------------------------------------------- status without Docker --
    def test_status_when_docker_is_unreachable(self):
        def configure(inst):
            inst.containers = mock.Mock(side_effect=InstallError(DAEMON_DOWN))
            inst.services = [{"service": "app", "image": "futureagi/standalone:v1.1.0", "component": "standalone"}]
        self.configure_installation = configure
        state = StateDir(self.root)
        receipt = state.write_receipt({"kind": "apply", "target_version": "v1.1.0", "status": "succeeded",
                                       "finished_at": "2026-10-19T01:02:03Z"})
        state.write_journal({"kind": "apply", "target_version": "v1.2.0", "stage": "rollout"})
        code, out, err = self.main("status")
        self.assertEqual(code, 2)
        self.assertEqual(err, f"bin/update: {DAEMON_DOWN}\n")
        lines = out.splitlines()
        self.assertEqual(lines[0], "Installation  Standalone (Compose project futureagi, .env)")
        self.assertRegex(lines[1], r"^  app +futureagi/standalone:v1\.1\.0 +\(FUTURE_AGI_VERSION=v1\.1\.0\)$")
        self.assertEqual(lines[2], f"  running: unknown ({DAEMON_DOWN})")
        self.assertTrue(lines[3].startswith("Interrupted   an apply to v1.2.0 stopped at stage rollout"), lines[3])
        self.assertEqual(lines[4], f"Last run      apply to v1.1.0: succeeded (2026-10-19T01:02:03Z) {receipt}")
        self.assertEqual(len(lines), 5)

    def test_status_when_compose_cannot_resolve_the_project(self):
        failure = "`docker compose config json` failed with exit code 1: cannot connect"

        class NoCompose(FakeInstallation):
            @property
            def project(self):
                raise InstallError(failure)

            @project.setter
            def project(self, value):
                pass

        def make(root, **kwargs):
            inst = NoCompose(root, **kwargs)
            self.installations.append(inst)
            return inst
        self.Installation.side_effect = make
        code, out, err = self.main("status")
        self.assertEqual(code, 2)
        self.assertEqual(err, f"bin/update: {failure}\n")
        lines = out.splitlines()
        self.assertEqual(lines[0], "Installation  Standalone (Compose project unknown, .env)")
        self.assertEqual(lines[1:1 + len(VERSION_VARIABLES)],
                         [f"  {key}={'v1.1.0' if key == 'FUTURE_AGI_VERSION' else 'unset'}" for key in VERSION_VARIABLES])
        self.assertEqual(lines[1 + len(VERSION_VARIABLES):], [f"  running: unknown ({failure})", "Last run      none"])
        self.assertFalse((self.root / STATE_DIR).exists())

    def test_error_after_status_output_flushes_stdout_first(self):
        events = []

        class Recording(io.StringIO):
            def __init__(self, name):
                super().__init__()
                self.name = name

            def write(self, text):
                events.append((self.name, "write", text))
                return super().write(text)

            def flush(self):
                events.append((self.name, "flush", ""))

        def configure(inst):
            inst.containers = mock.Mock(side_effect=InstallError(DAEMON_DOWN))
        self.configure_installation = configure
        stdout, stderr = Recording("stdout"), Recording("stderr")
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = cli.main(["--root", str(self.root), "--cwd", str(self.cwd), "status"])
        self.assertEqual(code, 2)
        error = events.index(("stderr", "write", f"bin/update: {DAEMON_DOWN}\n"))
        self.assertIn(("stdout", "flush", ""), events[:error])
        self.assertFalse([e for e in events[error:] if e[0] == "stdout" and e[1] == "write"])


@unittest.skipIf(shutil.which("bash") is None, "bin/update needs bash")
class CliBinUpdateAdditions(unittest.TestCase):
    setUp = BinUpdateScriptTests.setUp
    run_update = BinUpdateScriptTests.run_update

    def test_package_imports_only_itself_relatively(self):
        # bin/update loads deploy/update under a private name; an absolute
        # `deploy.update` import would load a second (or a foreign) copy.
        import ast
        for path in sorted((ROOT / "deploy" / "update").glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                names = []
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.level == 0:
                    names = [node.module or ""]
                for name in names:
                    with self.subTest(file=path.name, module=name):
                        self.assertNotEqual(name.split(".")[0], "deploy")

    def test_every_python_invocation_is_isolated(self):
        wrapper = self.base / "logging python"
        wrapper.write_text(f"#!/bin/sh\necho \"$1 $2\" >> \"$PLANT_MARKER\"\nexec '{sys.executable}' \"$@\"\n")
        wrapper.chmod(0o755)
        result = self.run_update("--help", env=dict(self.env, PYTHON=str(wrapper)))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("usage: bin/update", result.stdout)
        # The version guard and the run itself.
        self.assertEqual(self.marker.read_text().splitlines(), ["-I -c", "-I -c"])

    def test_python_environment_variables_are_ignored(self):
        userbase = self.base / "userbase"
        site_dir = subprocess.run(
            [sys.executable, "-c", "import site; print(site.getusersitepackages())"], capture_output=True, text=True,
            env=dict(self.env, PYTHONUSERBASE=str(userbase)), check=True, timeout=60).stdout.strip()
        Path(site_dir).mkdir(parents=True)
        for name in ("usercustomize.py", "sitecustomize.py"):
            (Path(site_dir) / name).write_text(
                f"import os\nopen(os.environ['PLANT_MARKER'], 'a').write('{name} imported\\n')\n")
        plant_deploy_package(Path(site_dir))
        startup = self.base / "startup.py"
        startup.write_text("import os\nopen(os.environ['PLANT_MARKER'], 'a').write('PYTHONSTARTUP ran\\n')\n")
        env = dict(self.env, PYTHONUSERBASE=str(userbase), PYTHONPATH=site_dir, PYTHONHOME=str(self.base / "nope"),
                   PYTHONSTARTUP=str(startup))
        result = self.run_update("--help", env=env)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("usage: bin/update", result.stdout)
        self.assertFalse(self.marker.exists(), self.marker.read_text() if self.marker.exists() else "")

    def fake_docker(self):
        """A docker CLI whose Compose works but whose daemon is down."""

        bindir = self.base / "fake bin"
        bindir.mkdir()
        config = json.dumps({"name": "futureagi", "services": {"app": {"image": "futureagi/standalone:v1.0.0"}}})
        (bindir / "docker").write_text(
            "#!/bin/sh\n"
            "case \"$1 $2 $3\" in\n"
            "  'compose version --short') echo 2.29.1; exit 0 ;;\n"
            "  'compose config --services') echo app; exit 0 ;;\n"
            f"  'compose config --format') echo '{config}'; exit 0 ;;\n"
            "esac\n"
            "echo 'Cannot connect to the Docker daemon at unix:///var/run/docker.sock. Is the docker daemon "
            "running?' >&2\n"
            "exit 1\n")
        (bindir / "docker").chmod(0o755)
        return dict(self.env, PATH=str(bindir) + os.pathsep + self.env.get("PATH", os.defpath))

    def test_status_without_the_docker_daemon_prints_what_it_can_then_the_error(self):
        (self.checkout / ".env").write_text("FUTURE_AGI_VERSION=v1.0.0\n")
        env = self.fake_docker()
        result = subprocess.run([str(self.checkout / "bin" / "update"), "status"], cwd=str(self.caller), env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=120)
        self.assertEqual(result.returncode, 2, result.stdout)
        lines = result.stdout.splitlines()
        self.assertEqual(lines[0], "Installation  Standalone (Compose project futureagi, .env)")
        self.assertRegex(lines[1], r"^  app +futureagi/standalone:v1\.0\.0 +\(FUTURE_AGI_VERSION=v1\.0\.0\)$")
        self.assertTrue(lines[2].startswith("  running: unknown (`docker ps"), lines[2])
        self.assertEqual(lines[3], "Last run      none")
        self.assertTrue(lines[4].startswith("bin/update: `docker ps"), lines[4])
        self.assertIn("Cannot connect to the Docker daemon", lines[4])
        self.assertEqual(len(lines), 5, lines)
        self.assertNotIn("Traceback", result.stdout)
        split = self.run_update("status", env=env)
        self.assertEqual(split.stdout, "\n".join(lines[:4]) + "\n")
        self.assertEqual(split.stderr, lines[4] + "\n")


# ============================= round 2: install.py contract D1 and D4 ====

PRODUCTION_HINT_FILES = ["-f", DISTRIBUTED_FILE, "-f", PRODUCTION_OVERLAY]
UP_HELP_WITH_PULL = """
Usage:  docker compose up [OPTIONS] [SERVICE...]

Create and start containers

Options:
      --abort-on-container-exit      Stops all containers if any container was stopped. Incompatible with -d
      --build                        Build images before starting containers
  -d, --detach                       Detached mode: Run containers in the background
      --no-build                     Don't build an image, even if it's policy
      --pull string                  Pull image before running ("always"|"missing"|"never") (default "policy")
      --quiet-pull                   Pull without printing progress information
      --wait                         Wait for services to be running|healthy. Implies detached mode.
      --wait-timeout int             Maximum duration in seconds to wait for the project to be running|healthy
"""
UP_HELP_WITHOUT_PULL = """
Usage:  docker compose up [SERVICE...]

Create and start containers

Options:
      --abort-on-container-exit   Stops all containers if any container was stopped. Incompatible with -d
  -d, --detach                    Detached mode: Run containers in the background
      --no-build                  Don't build an image, even if it's missing
      --quiet-pull                Pull without printing progress information
      --pull-images               (not a real option: only --pull itself counts)
      --wait                      Wait for services to be running|healthy. Implies detached mode.
"""


def executable(path, text="#!/bin/sh\nexit 0\n"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)
    return path


class InstallRound2IdentityTests(TempRootTest):
    """D1: identity() is what every receipt records about the install it belongs to."""

    def runner(self, project="futureagi"):
        return Runner(docker_handler({"name": project, "services": {}}))

    def test_standalone_and_distributed(self):
        for env_text, mode in (("", "standalone"), ("COMPOSE_FILE=docker-compose.distributed.yml\n", "distributed")):
            with self.subTest(mode=mode):
                inst = self.install(env_text, runner=self.runner("acme"))
                self.assertEqual(inst.identity(),
                                 {"mode": mode, "production": False, "env_file": ".env", "project": "acme"})

    def test_production_with_the_default_env_file(self):
        self.production_files()
        inst = Installation(self.root, production=True, environ={}, runner=self.runner(), compose=COMPOSE)
        self.assertEqual(inst.identity(), {"mode": "production", "production": True,
                                           "env_file": "deploy/.env.production", "project": "futureagi"})

    def test_production_with_a_custom_env_file_inside_and_outside_the_checkout(self):
        self.production_files()
        inside = self.write("deploy/prod eu.env", "")
        with tempfile.TemporaryDirectory() as tmp:
            outside = Path(tmp).resolve() / "staging.env"
            outside.write_text("", encoding="utf-8")
            for env_file, shown in ((inside, "deploy/prod eu.env"), (outside, str(outside))):
                with self.subTest(env_file=shown):
                    inst = Installation(self.root, production=True, env_file=env_file, environ={},
                                        runner=self.runner("staging"), compose=COMPOSE)
                    identity = inst.identity()
                    self.assertEqual(identity, {"mode": "production", "production": True, "env_file": shown,
                                                "project": "staging"})
                    # The same values a receipt records for this install.
                    self.assertEqual(identity["env_file"], inst.relative(inst.env_file))
                    self.assertEqual(identity["project"], inst.project)

    def test_two_installs_of_one_checkout_differ(self):
        self.production_files()
        staging = self.write("deploy/.env.staging", "")
        first = Installation(self.root, production=True, environ={}, runner=self.runner(), compose=COMPOSE)
        second = Installation(self.root, production=True, env_file=staging, environ={},
                              runner=self.runner("staging"), compose=COMPOSE)
        self.assertNotEqual(first.identity(), second.identity())

    def test_identity_needs_the_project_name(self):
        inst = self.install("", runner=Runner(lambda args, kwargs: completed(0, '{"services": {}}')))
        with self.assertRaises(InstallError) as caught:
            inst.identity()
        self.assertEqual(str(caught.exception), "docker compose config did not report a project name")


# The contract's probe value of each version key (install.PROBE_TAG_PREFIX + key).
PROBE_ENV = {key: "bin-update-probe-" + key for key in VERSION_VARIABLES}


def is_probe(kwargs):
    """Whether a runner call is service_model's probe (every version key at its probe value)."""

    env = kwargs.get("env") or {}
    return all(env.get(key) == value for key, value in PROBE_ENV.items())


class InstallRound2ConfigRawTests(TempRootTest):
    """D4, moved to round 4's service_model(): which key selects each image, cached like config().

    Round 5 (G1): every valid active JSON model also asks `config --services` for its active set.

    config_raw() (`config --no-interpolate`) is gone: it fails on this
    repository's compose files before Compose v2.40.3 (round 4, F1).
    """

    ACTIVE = {"name": "futureagi", "services": {"app": {"image": "futureagi/standalone:v1.0.0"}}}
    PROBED = {"name": "futureagi", "services": {
        "app": {"image": "futureagi/standalone:bin-update-probe-FUTURE_AGI_VERSION"}}}
    MODEL = {"services": {"app": {"image": "futureagi/standalone:v1.0.0", "variable": "FUTURE_AGI_VERSION",
                                  "active": True, "buildable": False}}, "complete": True}
    ACTIVE_CALL = ["docker", "compose", "config", "--format", "json"]
    PROFILES_CALL = ["docker", "compose", "config", "--profiles"]
    SERVICES_CALL = ["docker", "compose", "config", "--services"]

    def answering(self, *payloads):
        payloads = list(payloads)

        def handler(args, kwargs):
            if "--profiles" in args:
                return completed(0, "")
            if is_probe(kwargs):
                return payloads.pop(0) if len(payloads) > 1 else payloads[0]
            return completed(0, json.dumps(self.ACTIVE))
        return Runner(handler)

    def test_runs_compose_config_without_interpolation_once(self):
        runner = self.answering(completed(0, json.dumps(self.PROBED)))
        inst = self.install("FUTURE_AGI_VERSION=v1.0.0\n", runner=runner)
        self.assertEqual(inst.service_model(), self.MODEL)
        self.assertEqual(inst.service_model(), self.MODEL)
        self.assertEqual(runner.commands(), [self.ACTIVE_CALL, self.SERVICES_CALL, self.PROFILES_CALL, self.ACTIVE_CALL])
        self.assertTrue(is_probe(runner.calls[3][1]))

    def test_refresh_reruns(self):
        two = {"name": "futureagi", "services": dict(self.PROBED["services"], worker={
            "image": "futureagi/future-agi:bin-update-probe-FUTURE_AGI_VERSION"})}
        runner = self.answering(completed(0, json.dumps(self.PROBED)), completed(0, json.dumps(two)))
        inst = self.install("", runner=runner)
        self.assertEqual(sorted(inst.service_model()["services"]), ["app"])
        self.assertEqual(sorted(inst.service_model(refresh=True)["services"]), ["app", "worker"])
        self.assertEqual(sorted(inst.service_model()["services"]), ["app", "worker"])
        self.assertEqual(len(runner.calls), 8)

    def test_cached_apart_from_the_interpolated_model(self):
        runner = self.answering(completed(0, json.dumps(self.PROBED)))
        inst = self.install("FUTURE_AGI_VERSION=v1.0.0\n", runner=runner)
        self.assertEqual(inst.config()["services"]["app"]["image"], "futureagi/standalone:v1.0.0")
        self.assertEqual(inst.service_model(), self.MODEL)
        self.assertEqual(inst.config()["services"]["app"]["image"], "futureagi/standalone:v1.0.0")
        self.assertEqual(inst.service_model(), self.MODEL)
        self.assertEqual(runner.commands(), [self.ACTIVE_CALL, self.SERVICES_CALL, self.PROFILES_CALL, self.ACTIVE_CALL])
        self.assertFalse(is_probe(runner.calls[0][1]))

    def test_production_uses_the_env_file_and_both_compose_files(self):
        self.production_files()
        runner = self.answering(completed(0, json.dumps(self.PROBED)))
        inst = Installation(self.root, production=True, environ={}, runner=runner, compose=COMPOSE)
        inst.service_model()
        files = ["docker", "compose", "--env-file", str(self.root / PRODUCTION_ENV), "-f", str(self.root / DISTRIBUTED_FILE),
                 "-f", str(self.root / PRODUCTION_OVERLAY)]
        self.assertEqual(runner.commands(), [[*files, "config", "--format", "json"], [*files, "config", "--services"],
                                             [*files, "config", "--profiles"], [*files, "config", "--format", "json"]])

    def test_overlay_env_drops_the_cached_model(self):
        before = {"name": "futureagi", "services": {"app": {"image": "futureagi/standalone:bin-update-probe-FUTURE_AGI_VERSION"}}}
        after = {"name": "futureagi", "services": {"app": {"image": "futureagi/standalone:v9"}}}
        runner = self.answering(completed(0, json.dumps(before)), completed(0, json.dumps(after)))
        inst = self.install("FUTURE_AGI_VERSION=v1.0.0\n", runner=runner, environ={"PATH": "/bin"})
        self.assertEqual(inst.service_model()["services"]["app"]["variable"], "FUTURE_AGI_VERSION")
        inst.overlay_env({"FUTURE_AGI_VERSION": "v1.2.0"})
        self.assertIsNone(inst.service_model()["services"]["app"]["variable"])
        # The active model sees the overlay; the probe sets every version key over it.
        self.assertEqual(runner.calls[-4][1]["env"], {"PATH": "/bin", "FUTURE_AGI_VERSION": "v1.2.0"})
        self.assertEqual(runner.calls[-1][1]["env"], {"PATH": "/bin", **PROBE_ENV})

    def test_invalid_json_and_a_model_that_is_not_an_object(self):
        for stdout, message in (("not json", "docker compose config returned invalid JSON: "),
                                ("[]", "docker compose config did not return a Compose model"),
                                ("null", "docker compose config did not return a Compose model"),
                                ('{"services": []}', "docker compose config did not return a Compose model")):
            with self.subTest(stdout=stdout):
                inst = self.install("", runner=self.answering(completed(0, stdout)))
                with self.assertRaises(InstallError) as caught:
                    inst.service_model()
                self.assertTrue(str(caught.exception).startswith(message), str(caught.exception))

    def test_failing_config_is_described(self):
        inst = self.install("", runner=self.answering(completed(15, "", "services.app Additional property x\n")))
        with self.assertRaises(InstallError) as caught:
            inst.service_model()
        self.assertTrue(str(caught.exception).startswith(
            "`docker compose config` failed with exit code 15: services.app Additional property x. bin/update ran it "
            "with every version key set to a placeholder (FUTURE_AGI_VERSION=bin-update-probe-FUTURE_AGI_VERSION, "),
            str(caught.exception))

    def test_subclass_that_skips_init_can_use_it(self):
        # deploy/tests/test_update_apply.py's FakeInstallation sets its own
        # attributes instead of calling Installation.__init__.
        runner = self.answering(completed(0, json.dumps(self.PROBED)))
        bare = bare_installation(self.root, runner)
        self.assertEqual(bare.service_model(), self.MODEL)
        bare.overlay_env({"FUTURE_AGI_VERSION": "v2"})
        self.assertEqual(bare.service_model(), self.MODEL)
        self.assertEqual(len(runner.calls), 8)


def bare_installation(root, runner, compose=COMPOSE):
    class Bare(Installation):
        def __init__(self):  # noqa: D107 - like the apply tests' fake: no probing
            self.root = Path(root).resolve()
            self.production = False
            self.mode = "standalone"
            self.env_file = self.root / ".env"
            self.environ = {}
            self.docker = ["docker"]
            self.compose = list(compose)
            self.runner = runner
            self._config = None
            self.env = {}
            self.env_overlay = {}
    return Bare()


class InstallRound2ComposeHintTests(TempRootTest):
    """D4: compose_hint() is the Compose command an operator pastes in the checkout."""

    def production(self, env_file=None, **kwargs):
        self.production_files()
        kwargs.setdefault("environ", {})
        kwargs.setdefault("compose", COMPOSE)
        return Installation(self.root, production=True, env_file=env_file, runner=Runner(), **kwargs)

    def test_plugin_on_standalone_and_distributed(self):
        for env_text in ("", "COMPOSE_FILE=docker-compose.distributed.yml:docker-compose.override.yml\n"):
            with self.subTest(env=env_text):
                self.assertEqual(self.install(env_text).compose_hint(), "docker compose")

    def test_production_names_the_env_file_and_both_compose_files_relative_to_the_checkout(self):
        self.assertEqual(self.production().compose_hint(),
                         "docker compose --env-file deploy/.env.production -f docker-compose.distributed.yml "
                         "-f deploy/docker-compose.production.yml")

    def test_production_custom_env_file_is_relative_and_quoted(self):
        self.production_files()
        custom = self.write("deploy/prod eu.env", "")
        hint = self.production(custom).compose_hint()
        self.assertEqual(hint, "docker compose --env-file 'deploy/prod eu.env' -f docker-compose.distributed.yml "
                               "-f deploy/docker-compose.production.yml")

    def test_production_env_file_outside_the_checkout_is_absolute(self):
        with tempfile.TemporaryDirectory() as tmp:
            outside = Path(tmp).resolve() / "it's prod.env"
            outside.write_text("", encoding="utf-8")
            hint = self.production(outside).compose_hint()
        import shlex
        self.assertEqual(shlex.split(hint), ["docker", "compose", "--env-file", str(outside), *PRODUCTION_HINT_FILES])

    def test_hint_words_are_the_compose_args_this_run_uses(self):
        self.production_files()
        custom = self.write("deploy/prod $EU.env", "")
        inst = self.production(custom)
        import shlex
        words = shlex.split(inst.compose_hint())
        resolved = [str(self.root / word) if (self.root / word).is_file() else word for word in words]
        self.assertEqual(resolved, inst.compose_args())

    def test_pasted_into_a_shell_compose_gets_exactly_these_arguments(self):
        bindir = self.root / "fake bin"
        executable(bindir / "docker", "#!/bin/sh\nfor a in \"$@\"; do printf '%s\\n' \"$a\"; done\n")
        self.production_files()
        custom = self.write("deploy/prod $EU's.env", "")
        inst = self.production(custom, environ={"PATH": str(bindir) + os.pathsep + os.defpath})
        result = subprocess.run(["/bin/sh", "-c", inst.compose_hint() + " ps -a"], cwd=str(self.root),
                                env={"PATH": str(bindir) + os.pathsep + os.defpath}, capture_output=True, text=True,
                                timeout=60, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), [
            "compose", "--env-file", "deploy/prod $EU's.env", *PRODUCTION_HINT_FILES, "ps", "-a"])

    def test_standalone_binary_found_on_path_is_named(self):
        bindir = self.root / "bin dir"
        binary = executable(bindir / "docker-compose")
        environ = {"PATH": os.pathsep.join(["/nonexistent", str(bindir)])}
        self.assertEqual(self.install("", environ=environ, compose=[str(binary)]).compose_hint(), "docker-compose")
        self.assertEqual(self.production(environ=environ, compose=[str(binary)]).compose_hint(),
                         "docker-compose --env-file deploy/.env.production -f docker-compose.distributed.yml "
                         "-f deploy/docker-compose.production.yml")

    def test_standalone_binary_found_through_a_symlink_on_path_is_named(self):
        binary = executable(self.root / "opt" / "compose-2.29" / "docker-compose")
        link = self.root / "links" / "docker-compose"
        link.parent.mkdir()
        link.symlink_to(binary)
        inst = self.install("", environ={"PATH": str(link.parent)}, compose=[str(binary)])
        self.assertEqual(inst.compose_hint(), "docker-compose")

    def test_standalone_binary_not_on_path_is_given_by_its_path(self):
        binary = executable(self.root / "my tools" / "docker-compose")
        inst = self.install("", environ={"PATH": "/nonexistent"}, compose=[str(binary)])
        import shlex
        self.assertEqual(inst.compose_hint(), shlex.quote(str(binary)))
        self.assertEqual(shlex.split(inst.compose_hint()), [str(binary)])

    def test_another_binary_of_that_name_earlier_on_path_is_not_this_one(self):
        binary = executable(self.root / "wanted" / "docker-compose")
        other = executable(self.root / "first" / "docker-compose")
        environ = {"PATH": os.pathsep.join([str(other.parent), str(binary.parent)])}
        inst = self.install("", environ=environ, compose=[str(binary)])
        self.assertEqual(inst.compose_hint(), str(binary))

    def test_a_bare_program_name_stays_as_given(self):
        self.assertEqual(self.install("", compose=["docker-compose"]).compose_hint(), "docker-compose")
        self.assertEqual(self.install("", compose=["docker", "compose"]).compose_hint(), "docker compose")

    def test_docker_by_path_is_named_only_when_path_finds_it(self):
        docker = executable(self.root / "docker bin" / "docker")
        on_path = self.install("", environ={"PATH": str(docker.parent)}, compose=[str(docker), "compose"])
        self.assertEqual(on_path.compose_hint(), "docker compose")
        off_path = self.install("", environ={"PATH": "/nonexistent"}, compose=[str(docker), "compose"])
        import shlex
        self.assertEqual(shlex.split(off_path.compose_hint()), [str(docker), "compose"])


class InstallRound2OneoffTests(TempRootTest):
    """D4 and finding 3: `docker compose run` containers are marked and never judge the stack."""

    @staticmethod
    def oneoff(service, **kwargs):
        record = container_record(service, **kwargs)
        record["Config"]["Labels"]["com.docker.compose.oneoff"] = "True"
        return record

    def test_containers_marks_one_off_containers(self):
        service = container_record("fi-collector", cid="aa" * 32)
        backfill = self.oneoff("fi-collector", cid="bb" * 32, cmd=["--project", "p1"])
        unlabelled = container_record("app", cid="cc" * 32)
        del unlabelled["Config"]["Labels"]["com.docker.compose.oneoff"]
        lowercase = container_record("worker", cid="dd" * 32)
        lowercase["Config"]["Labels"]["com.docker.compose.oneoff"] = "true"
        inst = self.install("", runner=Runner(docker_handler(containers=[service, backfill, unlabelled, lowercase])))
        found = {c["id"]: c for c in inst.containers()}
        self.assertIs(found["bb" * 32]["oneoff"], True)
        self.assertEqual(found["bb" * 32]["service"], "fi-collector")
        for cid in ("aa" * 32, "cc" * 32, "dd" * 32):
            self.assertNotIn("oneoff", found[cid])

    def inst(self, *, production=False, env_text=""):
        runner = Runner(docker_handler({"name": "futureagi", "services": {}}))
        if production:
            self.production_files()
            return Installation(self.root, production=True, environ={}, runner=runner, compose=COMPOSE)
        return self.install(env_text, runner=runner)

    def test_production_initialization_job_does_not_read_as_a_non_production_stack(self):
        # deploy/README.md: `$COMPOSE run --rm -e SERVICE_TYPE=bootstrap ... --entrypoint bash
        # postgres-schema-bootstrap ./entrypoint.sh` runs the job without --check.
        containers = [
            {"service": "backend", "command": []},
            {"service": "postgres-schema-bootstrap", "command": ["python", "manage.py", "migrate", "--check"]},
            {"service": "postgres-schema-bootstrap", "command": ["bash", "./entrypoint.sh"], "oneoff": True},
        ]
        self.assertEqual(self.inst(production=True).check_stack(containers), [])

    def test_a_one_off_check_job_does_not_read_as_the_production_overlay(self):
        containers = [
            {"service": "backend", "command": []},
            {"service": "postgres-schema-bootstrap", "command": ["/entrypoint.sh", "bootstrap"]},
            {"service": "postgres-schema-bootstrap", "command": ["python", "manage.py", "migrate", "--check"],
             "oneoff": True},
        ]
        self.assertEqual(self.inst(env_text="COMPOSE_FILE=docker-compose.distributed.yml\n").check_stack(containers),
                         [])

    def test_one_off_containers_alone_are_no_install(self):
        problems = self.inst().check_stack([{"service": "app", "command": ["bash"], "oneoff": True}])
        # Round 5 (T53): the stack may be stopped, or not installed.
        self.assertEqual(problems, ["no containers of Compose project 'futureagi' exist. The stack may be stopped "
                                    "(Compose's down removes the containers and keeps the data), or Future AGI is not "
                                    "installed here yet: start it with `docker compose up -d`, or install it with "
                                    "`./bin/install`, then check again"])

    def test_one_off_of_a_distributed_service_is_no_stack_signal(self):
        containers = [{"service": "app", "command": []}, {"service": "worker", "command": [], "oneoff": True}]
        self.assertEqual(self.inst().check_stack(containers), [])

    def test_check_stack_through_containers(self):
        records = [container_record("backend", image="futureagi/future-agi:v1.0.0"),
                   container_record("postgres-schema-bootstrap", image="futureagi/future-agi:v1.0.0",
                                    status="exited", entrypoint=["python"], cmd=["manage.py", "migrate", "--check"]),
                   self.oneoff("postgres-schema-bootstrap", cid="ee" * 32, image="futureagi/future-agi:v1.0.0",
                               entrypoint=["bash"], cmd=["./entrypoint.sh"])]
        self.production_files()
        inst = Installation(self.root, production=True, environ={}, runner=Runner(docker_handler(containers=records)),
                            compose=COMPOSE)
        self.assertEqual(inst.check_stack(inst.containers()), [])


class InstallRound2UpPullNeverTests(TempRootTest):
    """D4: supports_up_pull_never() asks `<compose> up --help` once whether `up --pull never` exists."""

    def answering(self, result):
        def handler(args, kwargs):
            if isinstance(result, BaseException):
                raise result
            return result
        return Runner(handler)

    def test_compose_with_up_pull(self):
        runner = self.answering(completed(0, UP_HELP_WITH_PULL))
        inst = self.install("", runner=runner, environ={"PATH": "/bin"})
        self.assertIs(inst.supports_up_pull_never(), True)
        self.assertIs(inst.supports_up_pull_never(), True)
        self.assertEqual(len(runner.calls), 1)
        args, kwargs = runner.calls[0]
        self.assertEqual(args, ["docker", "compose", "up", "--help"])
        self.assertEqual(kwargs, {"cwd": str(self.root), "env": {"PATH": "/bin"}, "check": False, "text": True,
                                  "capture_output": True, "timeout": 60})

    def test_compose_without_up_pull(self):
        runner = self.answering(completed(0, UP_HELP_WITHOUT_PULL))
        inst = self.install("", runner=runner)
        self.assertIs(inst.supports_up_pull_never(), False)
        self.assertIs(inst.supports_up_pull_never(), False)
        self.assertEqual(len(runner.calls), 1)

    def test_option_forms(self):
        for text, expected in (("  --pull string   Pull image\n", True), ("  -p, --pull string   Pull\n", True),
                               ("\t--pull=never\n", True), ("--pull\n", True),
                               ("      --quiet-pull   Pull quietly\n", False),
                               ("      --pull-always  x\n", False), ("see --pull in the docs\n", False), ("", False)):
            with self.subTest(text=text):
                inst = self.install("", runner=self.answering(completed(0, text)))
                self.assertIs(inst.supports_up_pull_never(), expected)

    def test_help_on_stderr_counts(self):
        inst = self.install("", runner=self.answering(completed(0, "", UP_HELP_WITH_PULL)))
        self.assertIs(inst.supports_up_pull_never(), True)

    def test_failing_help_is_no(self):
        inst = self.install("", runner=self.answering(completed(1, UP_HELP_WITH_PULL, "unknown flag: --help")))
        self.assertIs(inst.supports_up_pull_never(), False)

    def test_probe_that_cannot_run_is_no_and_is_asked_again(self):
        for failure in (FileNotFoundError(2, "No such file or directory", "docker"),
                        subprocess.TimeoutExpired(["docker"], 60)):
            with self.subTest(failure=type(failure).__name__):
                runner = self.answering(failure)
                inst = self.install("", runner=runner)
                self.assertIs(inst.supports_up_pull_never(), False)
                self.assertIs(inst.supports_up_pull_never(), False)
                self.assertEqual(len(runner.calls), 2)

    def test_standalone_binary_and_production_ask_the_binary_alone(self):
        runner = self.answering(completed(0, UP_HELP_WITH_PULL))
        self.install("", runner=runner, compose=["/opt/bin/docker-compose"]).supports_up_pull_never()
        self.production_files()
        Installation(self.root, production=True, environ={}, runner=runner, compose=COMPOSE).supports_up_pull_never()
        self.assertEqual(runner.commands(), [["/opt/bin/docker-compose", "up", "--help"],
                                             ["docker", "compose", "up", "--help"]])

    def test_subclass_that_skips_init_can_use_it(self):
        runner = self.answering(completed(0, UP_HELP_WITH_PULL))
        bare = bare_installation(self.root, runner)
        self.assertIs(bare.supports_up_pull_never(), True)
        self.assertIs(bare.supports_up_pull_never(), True)
        self.assertEqual(len(runner.calls), 1)


# ================================== CLI round 2 (__main__ / bin/update owner) ====

# What FakeInstallation.identity() returns: the receipts and journals of the install MainTests runs.
CLI_IDENTITY = {"mode": "standalone", "production": False, "env_file": ".env", "project": "futureagi"}
CLI_NOTE = "the latest GitHub release has no update manifest; using v1.1.0, the newest release that has one"


def hold_update_lock(test, root):
    """Hold `root`'s update lock from another process, as a running bin/update does.

    Returns the holder text StateDir.lock writes ('pid N') and a function that
    lets the lock go (also called at cleanup).
    """

    code = ("import sys\n"
            "sys.path.insert(0, sys.argv[1])\n"
            "from pathlib import Path\n"
            "from deploy.update.receipt import StateDir\n"
            "with StateDir(Path(sys.argv[2])).lock():\n"
            "    print('locked', flush=True)\n"
            "    sys.stdin.read()\n")
    process = subprocess.Popen([sys.executable, "-c", code, str(ROOT), str(root)], stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, text=True)

    def release():
        if process.stdin and not process.stdin.closed:
            process.stdin.close()
        process.wait(timeout=60)
        if process.stdout and not process.stdout.closed:
            process.stdout.close()

    test.addCleanup(release)
    test.assertEqual(process.stdout.readline().strip(), "locked")
    return f"pid {process.pid}", release


class CliRound2ParserTests(unittest.TestCase):
    parse = CliParserAdditions.parse
    rejection = CliParserAdditions.rejection

    def test_stage_is_an_apply_option_in_either_position(self):
        self.assertTrue(self.parse(["apply", "--stage"]).stage)
        self.assertTrue(self.parse(["--stage", "apply"]).stage)
        self.assertFalse(self.parse(["apply"]).stage)
        namespace = self.parse(["--production", "apply", "--stage", "--dry-run"])
        self.assertEqual((namespace.production, namespace.stage, namespace.dry_run), (True, True, True))

    def test_stage_is_refused_for_every_other_command(self):
        for command in ("check", "rollback", "status"):
            for argv in (["--stage", command], [command, "--stage"]):
                with self.subTest(argv=argv):
                    err = self.rejection(argv)
                    self.assertIn(f"bin/update {command}: error: argument --stage: not allowed with {command} "
                                  "(only apply takes it)", err)
                    self.assertFalse(hasattr(self.parse([command]), "stage"))

    def test_stage_is_in_the_apply_help_only(self):
        parser = cli.build_parser()
        self.assertNotIn("--stage", parser.format_help())
        text = " ".join(parser.commands["apply"].format_help().split())
        self.assertIn("--stage", text)
        self.assertIn("then stop before restarting anything", text)
        self.assertIn("initialization jobs", text)
        for command in ("check", "rollback", "status"):
            with self.subTest(command=command):
                self.assertNotIn("--stage", parser.commands[command].format_help())


class CliRound2MainTests(unittest.TestCase):
    setUp = MainTests.setUp
    patch = MainTests.patch
    main = MainTests.main
    journal = CliMainAdditions.journal

    def planner(self):
        """A build_plan stand-in that records its keywords and does what plan.py does with them.

        FakeInstallations get overlay_env (contract C1). journal_reason is a
        blocking reason; fallback_note becomes a note (as a plan may say it
        itself).
        """

        def configure(inst):
            inst.overlays = []

            def overlay_env(values):
                inst.overlays.append(dict(values))
                inst.env.update({k: v for k, v in values.items() if v is not None})
            inst.overlay_env = overlay_env
        self.configure_installation = configure
        calls = []

        def plan(data, verification, installation, settings, **kwargs):
            calls.append(kwargs)
            reasons = [kwargs["journal_reason"]] if kwargs.get("journal_reason") else []
            notes = [kwargs["fallback_note"]] if kwargs.get("fallback_note") else []
            return plan_for("blocked" if reasons else "update_available", reasons=reasons, notes=notes)
        self.build_plan.side_effect = plan
        return calls

    def receipt(self, **fields):
        record = dict(CLI_IDENTITY, kind="apply", status="succeeded", target_version="v1.1.0")
        record.update(fields)
        return str(StateDir(self.root).write_receipt(record))

    # ------------------------------------------------ apply --stage (D3) --
    def test_apply_stage_is_passed_to_the_updater(self):
        code, _, err = self.main("apply", "--stage")
        self.assertEqual(code, 0, err)
        self.assertIs(self.Updater.return_value.apply.call_args[1]["stage"], True)
        self.Updater.return_value.apply.reset_mock()
        code, _, err = self.main("--stage", "apply", "--dry-run")
        self.assertEqual(code, 0, err)
        kwargs = self.Updater.return_value.apply.call_args[1]
        self.assertEqual((kwargs["stage"], kwargs["dry_run"]), (True, True))

    def test_stage_with_check_is_refused_before_anything_runs(self):
        with contextlib.redirect_stderr(io.StringIO()) as err, self.assertRaises(SystemExit) as caught:
            cli.main(["--root", str(self.root), "--cwd", str(self.cwd), "check", "--stage"])
        self.assertEqual(caught.exception.code, 2)
        self.assertIn("argument --stage: not allowed with check (only apply takes it)", err.getvalue())
        self.Installation.assert_not_called()
        self.fetch.assert_not_called()

    # ------------------------------------- history, fallback note (D1, D8) --
    def test_check_plans_with_this_installs_receipts_only_oldest_first(self):
        calls = self.planner()
        first = self.receipt(target_version="v1.0.0")
        self.receipt(env_file="deploy/.env.production", production=True, mode="distributed", project="fagi",
                     status="failed_during_rollout", target_version="v1.2.0")
        self.receipt(project="fagi-staging", status="staged", target_version="v1.2.0")
        second = self.receipt(target_version="v1.1.0")
        code, _, err = self.main("check")
        self.assertEqual(code, 0, err)
        self.assertEqual([r["_path"] for r in calls[-1]["history"]], [first, second])
        self.assertIsNone(calls[-1]["journal_reason"])
        self.assertNotIn("last_receipt", calls[-1])

    def test_apply_plans_with_the_history_the_updater_gives_and_the_fallback_note(self):
        calls = self.planner()
        self.fetched.note = CLI_NOTE
        self.main("apply")
        make_plan = self.Updater.return_value.apply.call_args[0][0]
        history = [{"kind": "apply", "status": "staged", "_path": "/s"}, {"kind": "apply", "_path": "/x"}]
        plan = make_plan(history)
        self.assertEqual(calls[-1]["history"], history)
        self.assertEqual(calls[-1]["fallback_note"], CLI_NOTE)
        self.assertIsNone(calls[-1]["journal_reason"])
        self.assertEqual(plan["notes"].count(CLI_NOTE), 1)
        make_plan(None)
        self.assertEqual(calls[-1]["history"], [])

    def test_fallback_note_goes_to_the_plan_and_is_said_once(self):
        calls = self.planner()
        self.fetched.note = CLI_NOTE
        code, out, err = self.main("check")
        self.assertEqual(code, 0, err)
        self.assertEqual(calls[-1]["fallback_note"], CLI_NOTE)
        self.assertEqual(out.count(CLI_NOTE), 1)
        self.assertEqual(err, f"Note: {CLI_NOTE}\n")

    def test_check_prints_the_next_command_the_plan_names(self):
        command = "./bin/update --production --env-file 'deploy/my prod.env' apply --stage"
        self.plan = dict(plan_for(), next_command=command)
        code, out, err = self.main("check")
        self.assertEqual(code, 0, err)
        self.assertIn(f"update available: run {command}", out)

    # ------------------------------- a journal another install left (27b) --
    def test_journal_of_another_install_blocks_check(self):
        calls = self.planner()
        self.journal(env_file="deploy/.env.production", production=True, mode="distributed", project="fagi",
                     found_values={"FUTURE_AGI_VERSION": "v1.0.0"})
        code, out, err = self.main("check")
        self.assertEqual(code, 3, err)
        cd = f"cd {shlex.quote(str(self.root))} && "  # bin/update ran from another directory (round 4, F2)
        reason = ("an interrupted bin/update run for deploy/.env.production must be recovered before apply or "
                  f"rollback can run for this install: run `{cd}./bin/update --production rollback` (without --yes it "
                  f"only recovers that run and changes nothing else), then `{cd}./bin/update check` again; "
                  f"`{cd}./bin/update --production apply` would recover it too, but would also continue that install's "
                  "update")
        self.assertEqual(calls[-1]["journal_reason"], reason)
        self.assertIn("blocked (see below)", out)
        self.assertIn(reason, out)
        self.assertNotIn(JOURNAL_NOTE, out)
        self.assertEqual(self.installations[0].overlays, [])
        self.assertEqual(calls[-1]["history"], [])
        self.assertIsNotNone(StateDir(self.root).read_journal())

    def test_journal_of_the_same_env_file_with_other_flags_blocks_check(self):
        # apply refuses it (Updater._check_journal_owner), so check does too.
        calls = self.planner()
        self.journal(production=True)
        code, out, _ = self.main("check", "--format", "json")
        self.assertEqual(code, 3)
        cd = f"cd {shlex.quote(str(self.root))} && "  # bin/update ran from another directory (round 4, F2)
        self.assertEqual(calls[-1]["journal_reason"], (
            "an interrupted bin/update run for .env must be recovered before apply or rollback can run for this "
            f"install: run `{cd}./bin/update --production --env-file .env rollback` (without --yes it only recovers "
            f"that run and changes nothing else), then `{cd}./bin/update check` again; "
            f"`{cd}./bin/update --production --env-file .env apply` would recover it too, but would also continue "
            "that install's update"))
        self.assertEqual(json.loads(out)["decision"], "blocked")
        self.assertEqual(self.installations[0].overlays, [])

    def test_journal_hint_quotes_the_env_file_of_the_install_it_belongs_to(self):
        calls = self.planner()
        self.journal(env_file="deploy/my prod.env", production=True)
        self.main("check")
        cd = f"cd {shlex.quote(str(self.root))} && "  # bin/update ran from another directory (round 4, F2)
        prefix = f"{cd}./bin/update --production --env-file 'deploy/my prod.env'"
        self.assertIn(f"run `{prefix} rollback` (without --yes it only recovers that run and changes nothing else), "
                      f"then `{cd}./bin/update check` again; `{prefix} apply` would recover it too",
                      calls[-1]["journal_reason"])

    def test_journal_of_this_install_is_planned_as_recovered(self):
        calls = self.planner()
        receipt = self.receipt()
        self.journal(**CLI_IDENTITY, found_values={"FUTURE_AGI_VERSION": "v1.0.5"})
        code, out, err = self.main("check", "--format", "json")
        self.assertEqual(code, 0, err)
        self.assertIsNone(calls[-1]["journal_reason"])
        history = calls[-1]["history"]
        self.assertEqual([r.get("_path") for r in history], [receipt, None])
        self.assertEqual((history[-1]["status"], history[-1]["target_version"]), ("interrupted_before_rollout", "v1.2.0"))
        self.assertEqual(self.installations[0].overlays, [{"FUTURE_AGI_VERSION": "v1.0.5"}])
        self.assertIn(JOURNAL_NOTE, json.loads(out)["notes"])

    # ----------------------------------------------- a run in progress (40) --
    def test_status_says_a_run_holding_the_lock_is_in_progress(self):
        holder, release = hold_update_lock(self, self.root)
        code, out, err = self.main("status")
        self.assertEqual(code, 0, err)
        self.assertIn(f"In progress   a bin/update run is in progress ({holder}); wait for it to finish\n", out)
        self.assertNotIn("Interrupted", out)

        StateDir(self.root).write_journal({"kind": "apply", "target_version": "v1.2.0", "stage": "rollout",
                                           **CLI_IDENTITY})
        _, out, _ = self.main("status")
        self.assertIn(f"In progress   a bin/update run is in progress ({holder}); wait for it to finish (now: apply "
                      "to v1.2.0, stage rollout)\n", out)
        self.assertNotIn("Interrupted", out)
        self.assertNotIn("recovers it", out)

        release()  # the run was killed: its journal is now an interrupted run's
        _, out, _ = self.main("status")
        self.assertNotIn("In progress", out)
        self.assertIn("Interrupted   an apply to v1.2.0 stopped at stage rollout; the next apply or rollback recovers it",
                      out)

    def test_check_while_a_run_holds_the_lock_blocks_and_assumes_nothing_about_it(self):
        calls = self.planner()
        self.journal(**CLI_IDENTITY, found_values={"FUTURE_AGI_VERSION": "v1.0.5"})
        holder, _ = hold_update_lock(self, self.root)
        code, out, err = self.main("check")
        self.assertEqual(code, 3, err)
        cd = f"cd {shlex.quote(str(self.root))} && "  # bin/update ran from another directory (round 4, F2)
        reason = (f"a bin/update run is in progress ({holder}); wait for it to finish, then run `{cd}./bin/update "
                  "check` again")
        self.assertEqual(calls[-1]["journal_reason"], reason)
        self.assertIn(reason, out)
        self.assertEqual(self.installations[0].overlays, [])
        self.assertEqual(calls[-1]["history"], [])
        self.assertNotIn(JOURNAL_NOTE, out)
        self.assertNotIn("interrupted", out)

    def test_check_names_the_production_command_to_rerun(self):
        calls = self.planner()
        holder, _ = hold_update_lock(self, self.root)
        self.main("--production", "--env-file", str(self.root / "deploy" / "my prod.env"), "check")
        cd = f"cd {shlex.quote(str(self.root))} && "  # bin/update ran from another directory (round 4, F2)
        self.assertEqual(calls[-1]["journal_reason"], (
            f"a bin/update run is in progress ({holder}); wait for it to finish, then run "
            f"`{cd}./bin/update --production --env-file 'deploy/my prod.env' check` again"))

    # ------------------------------------------------------ interrupts (21) --
    def test_interrupt_before_an_update_run_says_nothing_was_changed(self):
        cases = (
            ("manifest download", ("check",), lambda: setattr(self, "fetch_error", KeyboardInterrupt())),
            ("verification", ("apply",),
             lambda: setattr(self.Verifier.return_value.verify, "side_effect", cli.Interrupted("SIGTERM"))),
            ("planning", ("check",), lambda: setattr(self.build_plan, "side_effect", KeyboardInterrupt())),
        )
        for name, argv, arrange in cases:
            with self.subTest(name):
                self.fetch_error = None
                self.Verifier.return_value.verify.side_effect = None
                self.build_plan.side_effect = lambda *a, **k: self.plan
                arrange()
                code, _, err = self.main(*argv)
                self.assertEqual((code, err), (130, "bin/update: interrupted before anything was changed\n"))
                self.Updater.return_value.apply.assert_not_called()
                self.assertFalse(self.workdir.exists())

    def test_interrupt_inside_a_run_before_it_recorded_anything(self):
        self.receipt()  # an earlier run's receipt is not this run's doing
        for command, method in (("apply", "apply"), ("rollback", "rollback")):
            with self.subTest(command=command):
                getattr(self.Updater.return_value, method).side_effect = KeyboardInterrupt()
                code, _, err = self.main(command)
                self.assertEqual((code, err), (130, "bin/update: interrupted before anything was changed\n"))

    def test_interrupt_that_leaves_a_journal_names_the_recovery(self):
        def stop(*args, **kwargs):
            StateDir(self.root).write_journal({"kind": "apply", "target_version": "v1.2.0", "stage": "pulled",
                                               **CLI_IDENTITY})
            raise cli.Interrupted("SIGHUP")
        self.Updater.return_value.apply.side_effect = stop
        code, _, err = self.main("apply")
        self.assertEqual(code, 130)
        cd = f"cd {shlex.quote(str(self.root))} && "  # bin/update ran from another directory (round 4, F2)
        self.assertEqual(err, f"bin/update: interrupted before the apply finished; the next `{cd}./bin/update apply` "
                              f"or `{cd}./bin/update rollback` recovers it first (`{cd}./bin/update status` shows where "
                              "it stopped)\n")

    def test_interrupt_names_the_flags_of_the_install_whose_journal_was_left(self):
        def stop(*args, **kwargs):
            StateDir(self.root).write_journal({"kind": "rollback", "target_version": "v1.1.0", "stage": "env_written",
                                               "env_file": "deploy/my prod.env", "production": True})
            raise KeyboardInterrupt()
        self.Updater.return_value.rollback.side_effect = stop
        code, _, err = self.main("rollback", "--yes")
        self.assertEqual(code, 130)
        # Another install's journal (round 4, F7): the command that only recovers it comes first.
        cd = f"cd {shlex.quote(str(self.root))} && "  # bin/update ran from another directory (round 4, F2)
        prefix = f"{cd}./bin/update --production --env-file 'deploy/my prod.env'"
        self.assertEqual(err, "bin/update: interrupted; a rollback for deploy/my prod.env stopped earlier and must be "
                              "recovered before apply or rollback can run for this install: "
                              f"run `{prefix} rollback` (without --yes it only recovers that run and changes nothing "
                              f"else); `{prefix} apply` would recover it too, but would then plan an update of that "
                              "install, which can undo the rollback\n")

    def test_interrupt_after_the_run_recorded_a_receipt_points_to_it(self):
        self.receipt()
        made = []

        def stop(*args, **kwargs):
            made.append(self.receipt(target_version="v1.2.0"))
            raise KeyboardInterrupt()
        self.Updater.return_value.apply.side_effect = stop
        code, _, err = self.main("apply")
        self.assertEqual(code, 130)
        cd = f"cd {shlex.quote(str(self.root))} && "  # bin/update ran from another directory (round 4, F2)
        self.assertEqual(err, f"bin/update: interrupted; what the run did is recorded in {made[0]} "
                              f"(`{cd}./bin/update status` shows it)\n")

    # ------------------------------------------- non-UTF-8 locales (32) --
    def latin1_streams(self):
        return (io.TextIOWrapper(io.BytesIO(), encoding="latin-1", errors="strict"),
                io.TextIOWrapper(io.BytesIO(), encoding="latin-1", errors="strict"))

    def run_latin1(self, *argv):
        stdout, stderr = self.latin1_streams()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = cli.main(["--root", str(self.root), "--cwd", str(self.cwd), *argv])
        stdout.flush()
        stderr.flush()
        return code, stdout, stderr

    def test_check_plan_prints_under_a_latin1_locale(self):
        self.plan = plan_for("blocked", reasons=["the release \u201cv1.2.0\u201d is odd \u2026"],
                             notes=["caf\u00e9 \u2713"])
        code, stdout, stderr = self.run_latin1("check")
        self.assertEqual(code, 3)
        self.assertEqual((stdout.errors, stderr.errors), ("backslashreplace", "backslashreplace"))
        data = stdout.buffer.getvalue()
        self.assertIn(b"the release \\u201cv1.2.0\\u201d is odd \\u2026", data)
        self.assertIn(b"caf\xe9 \\u2713", data)  # what latin-1 has is written as it is
        self.assertTrue(data.startswith(b"Future AGI update check\n"))

    def test_errors_print_under_a_latin1_locale(self):
        self.Updater.return_value.apply.side_effect = Blocked("the update is blocked: \u2717 \u201cquoted\u201d")
        code, stdout, stderr = self.run_latin1("apply")
        self.assertEqual(code, 3)
        self.assertEqual(stderr.buffer.getvalue(),
                         b"bin/update: the update is blocked: \\u2717 \\u201cquoted\\u201d\n")


@unittest.skipIf(shutil.which("bash") is None, "bin/update needs bash")
class CliRound2BinUpdateTests(unittest.TestCase):
    setUp = BinUpdateScriptTests.setUp
    run_update = BinUpdateScriptTests.run_update

    def test_stage_through_the_wrapper(self):
        result = self.run_update("apply", "--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--stage", result.stdout)
        result = self.run_update("check", "--stage")
        self.assertEqual(result.returncode, 2)
        self.assertIn("argument --stage: not allowed with check (only apply takes it)", result.stderr)
        self.assertNotIn("Traceback", result.stderr)


class CliIntegrationTests(unittest.TestCase):
    """status reads this install's receipts (D1); OS errors and the cosign fallback note reach the operator."""

    setUp = MainTests.setUp
    patch = MainTests.patch
    main = MainTests.main
    journal = CliMainAdditions.journal
    receipt = CliRound2MainTests.receipt
    planner = CliRound2MainTests.planner

    OTHER = {"mode": "production", "production": True, "env_file": "deploy/prod-eu.env", "project": "fagi-eu"}

    def last_run(self, out):
        return [line for line in out.splitlines() if line.startswith("Last run")]

    def test_status_shows_this_installs_last_run(self):
        mine = self.receipt(target_version="v1.1.0", finished_at="2026-10-19T01:02:03Z")
        self.receipt(**self.OTHER, target_version="v1.2.0", finished_at="2026-10-19T02:00:00Z")
        code, out, err = self.main("status")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.last_run(out), [f"Last run      apply to v1.1.0: succeeded (2026-10-19T01:02:03Z) {mine}"])

    def test_status_says_none_when_only_other_installs_ran(self):
        self.receipt(**self.OTHER)
        code, out, err = self.main("status")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.last_run(out), ["Last run      none"])

    def test_without_docker_status_still_leaves_other_env_files_out(self):
        failure = "`docker compose config json` failed with exit code 1: cannot connect"

        class NoCompose(FakeInstallation):
            @property
            def project(self):
                raise InstallError(failure)

            @project.setter
            def project(self, value):
                pass

        self.Installation.side_effect = lambda root, **kwargs: NoCompose(root, **kwargs)
        # The project cannot be compared without Docker; the env file and the flags still can.
        renamed = self.receipt(project="futureagi-renamed", target_version="v1.1.0", finished_at="T1")
        self.receipt(**self.OTHER)
        code, out, _ = self.main("status")
        self.assertEqual(code, 2)
        self.assertEqual(self.last_run(out), [f"Last run      apply to v1.1.0: succeeded (T1) {renamed}"])

    def test_status_names_the_install_another_installs_journal_belongs_to(self):
        self.journal(env_file="deploy/prod eu.env", production=True)
        code, out, err = self.main("status")
        self.assertEqual(code, 2, err)
        cd = f"cd {shlex.quote(str(self.root))} && "  # bin/update ran from another directory (round 4, F2)
        theirs = f"{cd}./bin/update --production --env-file 'deploy/prod eu.env'"
        self.assertIn(f"Interrupted   an apply to v1.2.0 for deploy/prod eu.env stopped at stage pulled; this install "
                      f"cannot be updated until it is recovered: run `{theirs} rollback` (without --yes it only "
                      f"recovers that run and changes nothing else); `{theirs} apply` would recover it too, but would "
                      "also continue that install's update",
                      out)

    def test_an_os_error_is_an_error_not_a_traceback(self):
        for error, message in (
                (PermissionError(13, "Permission denied", "/srv/checkout/.futureagi-updates/verify-1"),
                 "cannot use /srv/checkout/.futureagi-updates/verify-1: Permission denied"),
                (OSError(28, "No space left on device"), "No space left on device")):
            with self.subTest(error=error):
                self.fetch_error = error
                code, _, err = self.main("check")
                self.assertEqual(code, 2)
                self.assertEqual(err, f"bin/update: {message}\n")

    def test_the_cosign_fallback_note_is_a_plan_note(self):
        self.planner()
        note = ("bin/update used ghcr.io/sigstore/cosign/cosign:v3.0.2 because the cosign on PATH, /usr/bin/cosign, "
                "is v2.2.4, older than v3")
        self.verification.details = {"note": note}
        code, out, err = self.main("check")
        self.assertEqual(code, 0, err)
        self.assertIn(f"  · {note}\n", out)
        self.main("apply")
        plan = self.Updater.return_value.apply.call_args[0][0]([])
        self.assertEqual(plan["notes"].count(note), 1)

    def test_an_unreadable_state_directory_is_the_update_runs_to_report(self):
        unreadable = StateError("cannot read /srv/checkout/.futureagi-updates: [Errno 13] Permission denied")
        with mock.patch.object(StateDir, "receipts", side_effect=unreadable):
            code, _, err = self.main("rollback", "--yes")
        self.assertEqual(code, 0, err)
        self.Updater.return_value.rollback.assert_called_once()

    def test_an_interrupt_when_the_state_cannot_be_read_points_at_status(self):
        self.Updater.return_value.apply.side_effect = KeyboardInterrupt()
        with mock.patch.object(StateDir, "read_journal", side_effect=StateError("cannot read journal.json")):
            code, _, err = self.main("apply")
        self.assertEqual(code, 130)
        cd = f"cd {shlex.quote(str(self.root))} && "  # bin/update ran from another directory (round 4, F2)
        self.assertEqual(err, f"bin/update: interrupted; run `{cd}./bin/update status` to see whether a run was left "
                              "unfinished\n")

    def test_output_streams_that_cannot_be_reconfigured_or_flushed(self):
        class Detached(io.StringIO):
            def reconfigure(self, **kwargs):
                raise ValueError("underlying buffer has been detached")

            def flush(self):
                raise OSError(5, "Input/output error")

        self.Updater.return_value.apply.side_effect = Blocked("the update is blocked: x")
        stdout, stderr = Detached(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = cli.main(["--root", str(self.root), "--cwd", str(self.cwd), "apply"])
        self.assertEqual(code, 3)
        self.assertEqual(stderr.getvalue(), "bin/update: the update is blocked: x\n")


class InstallComposeHintIntegrationTests(TempRootTest):
    """compose_hint() names a standalone Compose binary by its path when the PATH's binary cannot be compared."""

    def test_a_binary_that_cannot_be_compared_is_named_by_its_path(self):
        bindir = self.root / "fake bin"
        binary = executable(bindir / "docker-compose", "#!/bin/sh\nexit 0\n")
        inst = self.install(environ={"PATH": str(bindir)}, compose=[str(binary)])
        self.assertEqual(inst.compose_hint(), "docker-compose")
        with mock.patch.object(install.os.path, "samefile", side_effect=OSError(2, "No such file or directory")):
            self.assertEqual(inst.compose_hint(), shlex.quote(str(binary)))


# ------------------------------------------------------------ full stack --

import hashlib  # noqa: E402

from deploy.update.envfile import read_env  # noqa: E402
from deploy.update.manifest import PRODUCT, hash_path  # noqa: E402
from deploy.update.readiness import Readiness  # noqa: E402

apply_module = importlib.import_module("deploy.update.apply")


def registry_digest(ref, salt=""):
    return "sha256:" + hashlib.sha256(f"{ref}{salt}".encode()).hexdigest()


class FakeEngine:
    """Docker and Compose for one production project, as far as bin/update uses them.

    `config` resolves each service's image through the env file named by
    --env-file, plus the version keys bin/update passes in the environment
    (check's overlay, and service_model's probe values, PROBE_TAG_PREFIX +
    key), as Compose does. A service in `profiles` (service -> the profiles
    that enable it) is active only when COMPOSE_PROFILES (the environment's,
    else the env file's; `*` enables every profile) names one of them:
    `config --services` prints the active services one per line, in
    definition order, `config --format json` gives their model (with each
    one's profiles) and `config --profiles` every profile. `config --no-interpolate` fails the way it
    does for this repository's compose files on every Compose release before
    v2.40.3 (`version`, the release this engine answers `version` with).
    `pull` fetches a tag from `registry` (tag -> (digest, version label)),
    and `up` recreates every active service whose resolved image changed and
    keeps the others, as Compose does.
    """

    PROJECT = "fagi-prod"
    SERVICES = {
        "backend": "futureagi/future-agi:${FUTURE_AGI_VERSION}",
        "frontend": "futureagi/frontend:${FRONTEND_VERSION}",
        "postgres-schema-bootstrap": "futureagi/future-agi:${FUTURE_AGI_VERSION}",
        "postgres": "postgres:16",
    }
    # What `config --no-interpolate` prints for docker-compose.yml on Compose v2.24.0 to v2.40.2.
    NO_INTERPOLATE_ERROR = ("invalid spec: ./${AGENTCC_CONFIG_PATH:-agentcc-gateway/config.example.yaml}:"
                            "/etc/futureagi/secrets/agentcc.yaml:ro: too many colons")

    def __init__(self):
        self.registry = {}
        self.images = {}
        self.tags = {}
        self.records = []
        self.calls = []
        self.created = 0
        self.owner_labels = {}
        # Set: the next `up` replaces only this service, then bin/update is killed outright (no handler runs).
        self.killed_after = None
        # The services the compose files define (name -> image template) and the profiles of those in one.
        self.services = dict(self.SERVICES)
        self.profiles = {}
        self.version = "2.39.4"
        # True: `config --format json` lists every profile's services, as Compose v2.24.7 does (its
        # `config --services` still lists only the active ones).
        self.json_ignores_profiles = False

    def publish(self, ref, version, salt=""):
        self.registry[ref] = (registry_digest(ref, salt), version)

    def pull(self, ref):
        digest, version = self.registry[ref]
        image_id = "sha256:" + hashlib.sha256(f"{ref}@{digest}".encode()).hexdigest()
        self.images[image_id] = {"Id": image_id, "RepoDigests": [f"{ref.rsplit(':', 1)[0]}@{digest}"],
                                 "Config": {"Labels": {"org.opencontainers.image.version": version}}}
        self.tags[ref] = image_id

    def values(self, args, env):
        """The env file's values (--env-file), with the version keys and COMPOSE_PROFILES of `env` over them."""

        values = dict(read_env(Path(args[args.index("--env-file") + 1])))
        values.update({key: value for key, value in env.items()
                       if key in VERSION_VARIABLES or key == "COMPOSE_PROFILES"})
        return values

    def resolve(self, args, env):
        """service -> its image, for every service the compose files define (active or not).

        ${KEY} and ${KEY:-default} are interpolated as Compose does.
        """

        values = self.values(args, env)
        return {name: re.sub(r"\$\{(\w+)(?::-([^}]*))?\}",
                             lambda m: values.get(m.group(1)) or (m.group(2) or ""), template)
                for name, template in self.services.items()}

    def active(self, args, env):
        """The services Compose runs with `env`: those in no profile, and those of an enabled profile."""

        enabled = {name.strip() for name in (self.values(args, env).get("COMPOSE_PROFILES") or "").split(",")}
        return [name for name in self.services
                if not self.profiles.get(name) or "*" in enabled or enabled & set(self.profiles[name])]

    def compose_version(self):
        return tuple(int(part) for part in self.version.split("."))

    @staticmethod
    def service(record):
        """The service a container belongs to, or None for a one-off `docker compose run` container."""

        labels = record["Config"]["Labels"]
        return None if labels.get("com.docker.compose.oneoff") == "True" else labels["com.docker.compose.service"]

    def start(self, service, ref, *, oneoff=False):
        self.created += 1
        bootstrap = service == "postgres-schema-bootstrap" and not oneoff
        if not oneoff:
            self.records = [r for r in self.records if self.service(r) != service]
        self.records.append({
            "Id": f"{self.created:064x}", "Image": self.tags[ref], "RestartCount": 0,
            "Config": {"Image": ref, "Entrypoint": None,
                       "Cmd": ["python", "bootstrap.py", "--check"] if bootstrap else [],
                       "Labels": {**self.owner_labels, "com.docker.compose.project": self.PROJECT,
                                  "com.docker.compose.service": service,
                                  "com.docker.compose.oneoff": "True" if oneoff else "False"}},
            "State": {"Status": "exited" if bootstrap else "running", "ExitCode": 0,
                      "StartedAt": "2026-10-19T00:00:00Z"},
        })

    def running(self):
        """service -> the version label of the image its container runs."""

        return {self.service(r): self.images[r["Image"]]["Config"]["Labels"]["org.opencontainers.image.version"]
                or r["Config"]["Image"] for r in self.records if self.service(r)}

    def container_ids(self):
        return sorted(r["Id"] for r in self.records)

    def up_calls(self):
        return [call for call in self.calls if install.split_command(call)[1][:1] == ["up"]
                and "--help" not in call]

    def config_calls(self, option):
        """The `config` calls that passed `option` (say "--services" or "--no-interpolate")."""

        return [call for call in self.calls if install.split_command(call)[1][:1] == ["config"] and option in call]

    def __call__(self, args, **kwargs):
        args = list(args)
        self.calls.append(args)
        program, rest = install.split_command(args)
        env = kwargs.get("env") or {}
        if program == ["docker", "compose"] and rest[:1] == ["version"]:
            return completed(stdout=f"{self.version}\n" if "--short" in rest
                             else f"Docker Compose version v{self.version}\n")
        if program == ["docker", "compose"] and rest[0] == "config":
            if "--no-interpolate" in rest and self.compose_version() < (2, 40, 3):
                return completed(1, "", self.NO_INTERPOLATE_ERROR + "\n")
            if "--quiet" in rest:
                return completed()
            if "--profiles" in rest:
                names = sorted({name for profiles in self.profiles.values() for name in profiles})
                return completed(stdout="".join(name + "\n" for name in names))
            active = self.active(args, env)
            if "--services" in rest:
                return completed(stdout="".join(name + "\n" for name in active))
            images = self.resolve(args, env)
            listed = list(self.services) if self.json_ignores_profiles else active
            # Compose's model names a service's profiles, as its compose file does.
            return completed(stdout=json.dumps({"name": self.PROJECT, "services": {
                n: dict({"image": images[n]}, **({"profiles": self.profiles[n]} if self.profiles.get(n) else {}))
                for n in listed}}))
        if program == ["docker", "compose"] and rest[:2] == ["up", "--help"]:
            return completed(stdout="Usage:  docker compose up [OPTIONS] [SERVICE...]\n"
                                    "      --pull string   Pull image before running (\"always\"|\"missing\"|\"never\")\n")
        if program == ["docker", "compose"] and rest[0] == "pull":
            images = self.resolve(args, env)
            for name in rest[1:]:
                self.pull(images[name])
            return completed()
        if program == ["docker", "compose"] and rest[0] == "up":
            images = self.resolve(args, env)
            current = {self.service(r): r for r in self.records if self.service(r)}
            for name in self.active(args, env):
                ref = images[name]
                if ref not in self.tags:
                    return completed(1, "", f"Error response from daemon: pull access denied for {ref}")
                record = current.get(name)
                if record is None or record["Config"]["Image"] != ref or record["Image"] != self.tags[ref]:
                    self.start(name, ref)
                    if name == self.killed_after:
                        self.killed_after = None
                        raise SystemExit("killed")
            return completed()
        if args[:2] == ["docker", "ps"]:
            return completed(stdout="".join(r["Id"] + "\n" for r in self.records))
        if args[:2] == ["docker", "inspect"]:
            return completed(stdout=json.dumps([r for r in self.records if r["Id"] in args[2:]]))
        if args[:3] == ["docker", "image", "inspect"]:
            record = self.images.get(args[3]) or self.images.get(self.tags.get(args[3], ""))
            return completed(stdout=json.dumps([record])) if record else completed(1, "", "Error: No such image")
        return completed(99, "", f"unexpected call {args}")


class FullStackProductionTests(unittest.TestCase):
    """Every bin/update command end to end on one production install.

    The real CLI, settings, Installation, plan, Updater, receipts and journal
    run together; only Docker (FakeEngine), the signature check and the health
    probe are faked. The checkout path has a space in it, and every command
    bin/update prints for the operator is run again exactly as printed
    (shlex.split, as a shell would), so a command that does not paste fails here.
    """

    OLD, NEW, NEWER = "v1.0.0", "v1.1.0", "v1.2.0"
    REPOS = ("futureagi/future-agi", "futureagi/frontend")

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        base = Path(temporary.name).resolve()
        self.root = base / "my checkout"
        (self.root / "deploy").mkdir(parents=True)
        (self.root / DISTRIBUTED_FILE).write_text("services: {}\n")
        (self.root / PRODUCTION_OVERLAY).write_text("services: {}\n")
        self.env_file = self.root / PRODUCTION_ENV
        self.env_file.write_text(f"FUTURE_AGI_VERSION={self.OLD}\nFRONTEND_VERSION={self.OLD}\n"
                                 "PG_PASSWORD=prod-password-value\n")
        self.backup_log = base / "backups.log"
        hook = base / "backup hook.py"
        hook.write_text(f"open({str(self.backup_log)!r}, 'a').write('backup\\n')\n")
        self.manifests = {}
        for version in (self.OLD, self.NEW, self.NEWER):
            path = base / "release files" / version / "future-agi-update-manifest.json"
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps(self.manifest(version)))
            self.manifests[version] = path
        self.settings(manifest=self.NEW, backup=f"{shlex.quote(sys.executable)} {shlex.quote(str(hook))}")

        self.engine = FakeEngine()
        self.engine.owner_labels = {
            "com.docker.compose.project.working_dir": str(self.root),
            "com.docker.compose.project.config_files": f"{self.root / DISTRIBUTED_FILE},{self.root / PRODUCTION_OVERLAY}",
            "com.docker.compose.project.environment_file": str(self.env_file)}
        for version in (self.OLD, self.NEW, self.NEWER):
            for repo in self.REPOS:
                self.engine.publish(f"{repo}:{version}", version)
        self.engine.publish("postgres:16", "")
        for name, template in FakeEngine.SERVICES.items():
            ref = template.replace("${FUTURE_AGI_VERSION}", self.OLD).replace("${FRONTEND_VERSION}", self.OLD)
            self.engine.pull(ref)
            self.engine.start(name, ref)

        env_patch = mock.patch.dict(os.environ, clean_environ(), clear=True)
        env_patch.start()
        self.addCleanup(env_patch.stop)
        for target, name, kwargs in (
                (cli, "Installation", {"side_effect": lambda root, **kw: Installation(
                    root, runner=self.engine, compose=["docker", "compose"], **kw)}),
                (cli, "Verifier", {}),
                (apply_module, "Readiness", {"side_effect": lambda inst, out: Readiness(
                    inst, out, probe=lambda url: True, sleep=lambda seconds: None)})):
            patcher = mock.patch.object(target, name, **kwargs)
            mocked = patcher.start()
            self.addCleanup(patcher.stop)
            if name == "Verifier":
                mocked.return_value.trust_fingerprint.return_value = 'e' * 64
                mocked.return_value.verify.return_value = Verification(
                    True, method="keyless", signer="release-update-manifest.yml", tool="cosign",
                    issuer="https://token.actions.githubusercontent.com")

    def manifest(self, version):
        components = {}
        for name, repo in COMPONENT_REPOSITORIES.items():
            ref = f"{repo}:{version}{COMPONENT_TAG_SUFFIX.get(name, '')}"
            components[name] = {"image": ref, "digest": registry_digest(ref)}
        return {
            "schema_version": 1, "product": PRODUCT, "channel": "stable", "version": version,
            "source_sha": "a" * 40, "published_at": "2026-10-19T00:00:00Z", "minimum_version": self.OLD,
            "signature": {"bundle": "future-agi-update-manifest.json.sigstore.json"},
            "components": components,
            "helm": {"chart": "oci://ghcr.io/future-agi/charts/future-agi", "version": version[1:],
                     "digest": "sha256:" + "b" * 64},
            "deployment": {"files": {name: hash_path(self.root, name) for name in (DISTRIBUTED_FILE,
                                                                                   PRODUCTION_OVERLAY)}},
            "migration": {"requires_bootstrap": True, "rollback": "manual"},
        }

    def settings(self, *, manifest, backup=None):
        """update.conf: the manifest to follow (a local file) and the backup hook."""

        self.backup = backup or self.backup
        (self.root / "update.conf").write_text(
            f"FUTURE_AGI_UPDATE_MANIFEST_URL={self.manifests[manifest]}\n"
            f"FUTURE_AGI_UPDATE_BACKUP_COMMAND={self.backup}\n")

    def paste(self, command):
        """Run a command line as an operator would paste it (it must start with ./bin/update)."""

        words = shlex.split(command)
        self.assertEqual(words[0], "./bin/update", command)
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = cli.main(["--root", str(self.root), "--cwd", str(self.root), *words[1:]])
        return code, stdout.getvalue(), stderr.getvalue()

    def printed(self, text, pattern):
        """The one command in `text` that `pattern` (a regex with one group) finds."""

        found = re.findall(pattern, text)
        self.assertEqual(len(found), 1, text)
        return found[0]

    def versions(self):
        env = read_env(self.env_file)
        return env["FUTURE_AGI_VERSION"], env["FRONTEND_VERSION"]

    def receipts(self):
        return [json.loads(path.read_text()) for path in StateDir(self.root).receipts()]

    def receipt_paths(self):
        return [str(path) for path in StateDir(self.root).receipts()]

    def backups(self):
        return len(self.backup_log.read_text().splitlines()) if self.backup_log.exists() else 0

    def running(self, version):
        return {"backend": version, "frontend": version, "postgres-schema-bootstrap": version, "postgres": "postgres:16"}

    def stage(self):
        code, out, err = self.paste("./bin/update --production apply --stage")
        self.assertEqual(code, 0, err)
        return err

    def test_production_updates_in_two_steps_then_rolls_back(self):
        code, out, err = self.paste("./bin/update --production check")
        self.assertEqual(code, 0, err)
        first = self.printed(out, r"update available: run (.+?) \(the first of two steps")
        self.assertEqual(first, "./bin/update --production apply --stage")

        # A plain apply is refused before anything happens, naming the two steps.
        code, out, err = self.paste("./bin/update --production apply")
        self.assertEqual(code, 3, err)
        self.assertIn("production installs update in two steps: ./bin/update --production apply --stage (backs up, "
                      "then pulls and verifies the release; restarts nothing), run the release's initialization jobs "
                      "with `docker compose --env-file deploy/.env.production -f docker-compose.distributed.yml -f "
                      "deploy/docker-compose.production.yml run --rm ...`", err)
        # The plan it printed first names the step to run instead: it does not claim to be applying the release.
        self.assertEqual(self.printed(err, r"update available: run (.+?) \(the first of two steps"), first)
        self.assertNotIn("applying", err)
        self.assertEqual((self.versions(), self.backups(), self.receipts()), ((self.OLD, self.OLD), 0, []))

        # Step one: back up, pin, pull and verify; nothing restarts.
        before = self.engine.container_ids()
        err = self.run_printed_stage(first)
        self.assertEqual(self.versions(), (self.NEW, self.NEW))
        self.assertEqual((self.backups(), self.engine.up_calls(), self.engine.container_ids()), (1, [], before))
        self.assertEqual(self.engine.running(), self.running(self.OLD))
        staged_path = self.receipt_paths()[-1]
        staged = self.receipts()[-1]
        self.assertEqual((staged["status"], staged["target_version"]), ("staged", self.NEW))
        self.assertFalse(StateDir(self.root).journal_path.exists())
        self.assertIn("Nothing was restarted", err)
        self.assertIn("Next, run the release's initialization jobs", err)
        finish = self.printed(err, r"Then run `([^`]+)` to restart the stack")

        code, out, _ = self.paste("./bin/update --production status")
        self.assertEqual(code, 0)
        self.assertIn(f"Last run      apply to {self.NEW}: staged", out)
        code, out, err = self.paste("./bin/update --production check")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.printed(out, r"update available: .*?\brun (\./bin/update .+?) \(finishes"), finish)
        self.assertIn(f"the update to {self.NEW} is staged", out)

        # A legacy hook has no new freshness/proof fields. Explicit acknowledgment is required.
        before = self.env_file.read_bytes()
        code, out, err = self.paste(finish)
        self.assertEqual((code, err), (3, 'bin/update: stale_stage\n'))
        self.assertEqual(self.env_file.read_bytes(), before)
        # Step two after initialization and host acknowledgment; no second backup.
        code, out, err = self.paste(finish + ' --acknowledge-stale-stage')
        self.assertEqual(code, 0, err)
        self.assertEqual(self.engine.running(), self.running(self.NEW))
        self.assertEqual(self.backups(), 1)
        up = self.engine.up_calls()
        self.assertEqual(len(up), 1)
        self.assertIn("--pull never", " ".join(up[0]))
        done = self.receipts()[-1]
        self.assertEqual((done["status"], done["resumes"], done["backup"]),
                         ("succeeded", staged_path, {"ran": False, "staged": staged_path}))
        self.assertEqual(done["previous_values"], {"FUTURE_AGI_VERSION": self.OLD, "FRONTEND_VERSION": self.OLD})
        self.assertEqual(done["project"], FakeEngine.PROJECT)

        code, out, err = self.paste("./bin/update --production check")
        self.assertEqual(code, 0, err)
        self.assertIn("up to date, nothing to do", out)

        # The rollback undoes the whole update (the staged run and the run that finished it).
        code, out, err = self.paste("./bin/update --production rollback --yes")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.versions(), (self.OLD, self.OLD))
        self.assertEqual(self.engine.running(), self.running(self.OLD))
        code, out, err = self.paste("./bin/update --production check")
        self.assertEqual(code, 0, err)
        self.assertIn("update available: run ./bin/update --production apply --stage", out)

    def run_printed_stage(self, command):
        code, out, err = self.paste(command)
        self.assertEqual(code, 0, err)
        return err

    def test_a_staged_update_is_dropped_with_the_printed_rollback(self):
        before = self.engine.container_ids()
        err = self.stage()
        drop = self.printed(err, r"To drop the staged update instead, run `([^`]+)`")
        self.assertIn(shlex.quote(self.receipt_paths()[-1]), drop)  # the checkout path has a space in it
        code, out, err = self.paste(drop)
        self.assertEqual(code, 0, err)
        self.assertEqual(self.versions(), (self.OLD, self.OLD))
        self.assertEqual(self.engine.container_ids(), before)  # nothing had changed, so nothing was recreated
        self.assertEqual(self.receipts()[-1]["status"], "rolled_back")
        code, out, err = self.paste("./bin/update --production check")
        self.assertEqual(code, 0, err)
        self.assertIn("update available: run ./bin/update --production apply --stage", out)

    def test_a_newer_release_waits_for_the_staged_one_and_the_printed_finish_command_works(self):
        self.stage()
        staged_path = self.receipt_paths()[-1]
        self.settings(manifest=self.NEWER)
        code, out, err = self.paste("./bin/update --production check")
        self.assertEqual(code, 3, err)
        self.assertIn(f"the update to {self.NEW} is staged", out)
        self.assertIn(f"./bin/update --production rollback --receipt {shlex.quote(staged_path)} --yes", out)
        finish = self.printed(out, r"(\./bin/update --production apply --manifest '[^']+')")
        code, out, err = self.paste("./bin/update --production apply")
        self.assertEqual(code, 3, err)
        self.assertEqual((self.backups(), self.engine.up_calls()), (1, []))

        # Even acknowledgment cannot waive configuration identity drift.
        code, out, err = self.paste(finish + ' --acknowledge-stale-stage')
        self.assertEqual((code, err), (3, 'bin/update: immutable_release_changed\n'))
        self.settings(manifest=self.NEW)
        code, out, err = self.paste(finish + ' --acknowledge-stale-stage')
        self.assertEqual(code, 0, err)
        self.assertEqual(self.engine.running(), self.running(self.NEW))
        self.settings(manifest=self.NEWER)
        code, out, err = self.paste("./bin/update --production check")
        self.assertEqual(code, 0, err)
        self.assertIn(f"Release       {self.NEWER}", out)
        self.assertIn("update available: run ./bin/update --production apply --stage", out)

    def test_a_finishing_run_that_fails_before_the_restart_leaves_the_staged_update_pending(self):
        self.stage()
        staged_path = self.receipt_paths()[-1]
        # The tag moved after staging: the finishing run's pull fetches other content, which is refused.
        # Round 4 (F3): an image already on the host with the signed digest is not pulled again, so the staged
        # image is gone first (pruned) and the finishing run has to pull.
        self.engine.images.pop(self.engine.tags.pop(f"futureagi/future-agi:{self.NEW}"))
        self.engine.publish(f"futureagi/future-agi:{self.NEW}", self.NEW, salt="moved")
        code, out, err = self.paste("./bin/update --production apply --acknowledge-stale-stage")
        self.assertEqual(code, 4, err)
        self.assertIn(f"The update to {self.NEW} staged earlier is still pending", err)
        self.assertIn(f"rollback --receipt {shlex.quote(staged_path)} --yes", err)
        self.assertEqual(self.versions(), (self.NEW, self.NEW))
        self.assertEqual((self.engine.up_calls(), self.engine.running()), ([], self.running(self.OLD)))

        code, out, err = self.paste("./bin/update --production check")
        self.assertEqual(code, 0, err)
        self.assertIn(f"the update to {self.NEW} is staged", out)
        self.engine.publish(f"futureagi/future-agi:{self.NEW}", self.NEW)
        code, out, err = self.paste("./bin/update --production apply --acknowledge-stale-stage")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.receipts()[-1]["resumes"], staged_path)
        self.assertEqual(self.backups(), 1)

    def test_one_off_containers_are_not_the_stack(self):
        # Between the two steps the release's initialization job runs on the staged image, as a one-off
        # `docker compose run` container that may still be running.
        self.stage()
        self.engine.start("backend", f"futureagi/future-agi:{self.NEW}", oneoff=True)
        code, out, err = self.paste("./bin/update --production status")
        self.assertEqual(code, 0, err)
        backend = [line for line in out.splitlines() if line.split()[:1] == ["backend"]]
        self.assertEqual(len(backend), 1, out)
        self.assertIn(f"running {self.OLD}  (FUTURE_AGI_VERSION={self.NEW})", backend[0])

        code, out, err = self.paste("./bin/update --production apply --acknowledge-stale-stage")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.engine.running(), self.running(self.NEW))
        self.assertEqual(self.receipts()[-1]["status"], "succeeded")

    def killed_mid_rollout(self):
        """Stage, then kill the finishing run once it has replaced the backend: only its journal is left."""

        self.stage()
        self.engine.killed_after = "backend"
        with self.assertRaises(SystemExit):
            self.paste("./bin/update --production apply --acknowledge-stale-stage")
        self.assertEqual(StateDir(self.root).read_journal()["stage"], "rollout")
        self.assertEqual(self.engine.running()["backend"], self.NEW)
        self.assertEqual(self.engine.running()["frontend"], self.OLD)
        code, out, _ = self.paste("./bin/update --production status")
        self.assertIn(f"Interrupted   an apply to {self.NEW} stopped at stage rollout; the next apply or rollback "
                      "recovers it", out)
        # A newer release is out by the time the operator looks.
        self.settings(manifest=self.NEWER)
        code, out, err = self.paste("./bin/update --production check")
        self.assertEqual(code, 3, err)
        self.assertIn(f"the previous update to {self.NEW} stopped while containers were starting "
                      "(interrupted_during_rollout)", out)
        self.assertNotIn("<receipt>", out)
        return out

    def test_a_killed_update_is_rolled_back_with_the_printed_command(self):
        out = self.killed_mid_rollout()
        undo = self.printed(out, r"or roll it back \((\./bin/update [^)]+)\) first")
        self.assertEqual(undo, "./bin/update --production rollback --yes")
        code, out, err = self.paste(undo)
        self.assertEqual(code, 0, err)
        self.assertIn(f"Recovered an interrupted apply to {self.NEW}: it stopped while containers were being replaced",
                      err)
        self.assertEqual(self.versions(), (self.OLD, self.OLD))
        self.assertEqual(self.engine.running(), self.running(self.OLD))
        self.assertIsNone(StateDir(self.root).read_journal())

    def test_a_killed_update_is_finished_with_the_printed_command(self):
        out = self.killed_mid_rollout()
        # Round 6 (H4, U7): the update stopped is of another release than update.conf's manifest, so its finish
        # step checks that release out and runs its jobs first; the apply comes last, before the way back.
        finish = self.printed(out, r"Finish it \(check out v1\.1\.0 with `git checkout v1\.1\.0`, .*?\bthen "
                                   r"(\./bin/update --production apply --manifest '[^']+')\) or roll it back")
        code, out, err = self.paste(finish)
        self.assertEqual(code, 0, err)
        self.assertEqual(self.engine.running(), self.running(self.NEW))
        recovered, done = self.receipts()[-2:]
        self.assertEqual((recovered["status"], done["status"]), ("interrupted_during_rollout", "succeeded"))
        self.assertEqual(done["previous_values"], {"FUTURE_AGI_VERSION": self.OLD, "FRONTEND_VERSION": self.OLD})

    def test_another_installs_staged_update_in_the_same_checkout_is_not_this_ones(self):
        other = {"mode": "production", "production": True, "env_file": "deploy/prod eu.env", "project": "fagi-eu"}
        StateDir(self.root).write_receipt(dict(other, kind="apply", status="staged", target_version=self.NEW,
                                               previous_values={"FUTURE_AGI_VERSION": "v0.9.0"}))
        code, out, err = self.paste("./bin/update --production check")
        self.assertEqual(code, 0, err)
        self.assertIn("update available: run ./bin/update --production apply --stage", out)
        self.stage()
        code, out, err = self.paste("./bin/update --production apply --acknowledge-stale-stage")
        self.assertEqual(code, 0, err)
        done = self.receipts()[-1]
        self.assertEqual(done["previous_values"], {"FUTURE_AGI_VERSION": self.OLD, "FRONTEND_VERSION": self.OLD})
        self.assertNotIn("v0.9.0", json.dumps(done))


class CliRound3MainTests(unittest.TestCase):
    """Round 3 in the CLI: trust's newer release reaches the plan (R6, E2), status and check read the journal after
    probing the lock (R14, E5), and another install's journal is recovered by the command that only recovers it
    (R12, E5)."""

    setUp = MainTests.setUp
    patch = MainTests.patch
    main = MainTests.main
    journal = CliMainAdditions.journal
    receipt = CliRound2MainTests.receipt
    planner = CliRound2MainTests.planner

    RECOVER_ONLY = "(without --yes it only recovers that run and changes nothing else)"

    def lock_probe(self, during):
        """Patch StateDir.lock_holder: `during(state)` is what happens while the lock is probed; it returns the holder."""

        patcher = mock.patch.object(StateDir, "lock_holder", autospec=True, side_effect=during)
        probe = patcher.start()
        self.addCleanup(patcher.stop)
        return probe

    def finishes_while_probed(self):
        """The run whose journal is there finishes while the lock is probed: receipt, journal cleared, lock let go."""

        made = []

        def during(state):
            if state.read_journal() is not None:
                made.append(self.receipt(target_version="v1.2.0"))
                state.clear_journal()
            return None
        self.lock_probe(during)
        return made

    # ------------------------------------------------ the newer release (R6) --
    def test_check_passes_the_newer_release_trust_found_to_the_plan(self):
        calls = self.planner()
        self.fetched.note = CLI_NOTE
        self.fetched.newer_release = "v1.3.0"
        code, _, err = self.main("check")
        self.assertEqual(code, 0, err)
        self.assertEqual((calls[-1]["newer_release"], calls[-1]["fallback_note"]), ("v1.3.0", CLI_NOTE))

    def test_apply_plans_with_the_newer_release_too(self):
        calls = self.planner()
        self.fetched.note = CLI_NOTE
        self.fetched.newer_release = "v1.3.0"
        code, _, err = self.main("apply")
        self.assertEqual(code, 0, err)
        make_plan = self.Updater.return_value.apply.call_args[0][0]
        make_plan([])
        self.assertEqual((calls[-1]["newer_release"], calls[-1]["fallback_note"]), ("v1.3.0", CLI_NOTE))

    def test_a_fallback_with_no_newer_release_passes_none(self):
        # GitHub's latest release is an ad-hoc build or an older backport: the manifest used is the newest
        # release's, so the note is only a note and the plan must not wait for another manifest.
        calls = self.planner()
        self.fetched.note = CLI_NOTE
        self.fetched.newer_release = None
        code, out, err = self.main("check")
        self.assertEqual(code, 0, err)
        self.assertIsNone(calls[-1]["newer_release"])
        self.assertEqual(calls[-1]["fallback_note"], CLI_NOTE)
        self.assertEqual(out.count(CLI_NOTE), 1)
        # A fetched manifest that names no newer release at all is the same.
        del self.fetched.newer_release
        code, _, err = self.main("check")
        self.assertEqual(code, 0, err)
        self.assertIn("newer_release", calls[-1])
        self.assertIsNone(calls[-1]["newer_release"])

    # ------------------------------------ the journal after the lock (R14) --
    def test_status_does_not_call_a_run_that_finished_while_the_lock_was_probed_interrupted(self):
        self.journal(**CLI_IDENTITY, stage="rollout")
        made = self.finishes_while_probed()
        code, out, err = self.main("status")
        self.assertEqual(code, 0, err)
        self.assertEqual(len(made), 1)
        self.assertNotIn("Interrupted", out)
        self.assertNotIn("In progress", out)
        self.assertNotIn("recovers it", out)
        self.assertIn(f"Last run      apply to v1.2.0: succeeded (?) {made[0]}\n", out)

    def test_check_plans_a_run_that_finished_while_the_lock_was_probed_by_its_receipt(self):
        calls = self.planner()
        earlier = self.receipt(target_version="v1.1.0")
        self.journal(**CLI_IDENTITY, stage="rollout", found_values={"FUTURE_AGI_VERSION": "v1.1.0"})
        made = self.finishes_while_probed()
        code, out, err = self.main("check")
        self.assertEqual(code, 0, err)
        history = calls[-1]["history"]
        self.assertEqual([record.get("_path") for record in history], [earlier, made[0]])
        self.assertEqual(history[-1]["status"], "succeeded")
        self.assertIsNone(calls[-1]["journal_reason"])
        self.assertEqual(self.installations[0].overlays, [])
        self.assertNotIn(JOURNAL_NOTE, out)
        self.assertNotIn("interrupted", out)

    def test_a_journal_still_there_once_the_lock_is_free_is_a_stopped_run(self):
        calls = self.planner()
        self.journal(**CLI_IDENTITY, stage="rollout")
        self.lock_probe(lambda state: None)
        code, out, err = self.main("status")
        self.assertEqual(code, 0, err)
        self.assertIn("Interrupted   an apply to v1.2.0 stopped at stage rollout; the next apply or rollback recovers it",
                      out)
        code, out, err = self.main("check", "--format", "json")
        self.assertEqual(code, 0, err)
        self.assertEqual(calls[-1]["history"][-1]["status"], "interrupted_during_rollout")
        self.assertIn(JOURNAL_NOTE, json.loads(out)["notes"])

    def test_status_while_a_run_holds_the_lock_shows_its_newest_record(self):
        self.journal(**CLI_IDENTITY, stage="pulled")

        def moves_on(state):
            state.write_journal(dict(state.read_journal(), stage="rollout"))
            return "pid 4242"
        probe = self.lock_probe(moves_on)
        code, out, err = self.main("status")
        self.assertEqual(code, 0, err)
        self.assertIn("In progress   a bin/update run is in progress (pid 4242); wait for it to finish (now: apply to "
                      "v1.2.0, stage rollout)\n", out)

        # The run that holds the lock recovered a killed run's journal: that journal is not what it is doing.
        def recovers_it(state):
            state.clear_journal()
            return "pid 4242"
        probe.side_effect = recovers_it
        code, out, err = self.main("status")
        self.assertEqual(code, 0, err)
        self.assertIn("In progress   a bin/update run is in progress (pid 4242); wait for it to finish\n", out)
        self.assertNotIn("now:", out)

    # --------------------------------------- another install's journal (R12) --
    def test_check_names_the_command_that_only_recovers_another_installs_journal_first(self):
        calls = self.planner()
        self.journal()  # left by a standalone run (.env, without --production)
        code, out, err = self.main("--production", "--env-file", str(self.root / "deploy" / "my prod.env"), "check")
        self.assertEqual(code, 3, err)
        cd = f"cd {shlex.quote(str(self.root))} && "  # bin/update ran from another directory (round 4, F2)
        reason = ("an interrupted bin/update run for .env must be recovered before apply or rollback can run for this "
                  f"install: run `{cd}./bin/update rollback` {self.RECOVER_ONLY}, then "
                  f"`{cd}./bin/update --production --env-file 'deploy/my prod.env' check` again; `{cd}./bin/update apply` "
                  "would recover it too, but would also continue that install's update")
        self.assertEqual(calls[-1]["journal_reason"], reason)
        self.assertIn(reason, out)
        self.assertLess(reason.index("rollback`"), reason.index("apply`"))
        self.assertNotIn("--yes`", reason)

    def test_status_names_the_command_that_only_recovers_another_installs_journal_first(self):
        self.journal(env_file="deploy/.env.production", production=True, kind="rollback", target_version="v1.0.0",
                     stage="env_written")
        code, out, err = self.main("status")
        self.assertEqual(code, 2, err)
        cd = f"cd {shlex.quote(str(self.root))} && "  # bin/update ran from another directory (round 4, F2)
        # The journal is a rollback's: apply would not continue an update (round 4, F7 / S48).
        self.assertEqual([line for line in out.splitlines() if line.startswith("Interrupted")], [
            "Interrupted   a rollback to v1.0.0 for deploy/.env.production stopped at stage env_written; this install "
            f"cannot be updated until it is recovered: run `{cd}./bin/update --production rollback` "
            f"{self.RECOVER_ONLY}; `{cd}./bin/update --production apply` would recover it too, but would then plan an "
            "update of that install, which can undo the rollback"])


class CliRound3FullStackTests(unittest.TestCase):
    """Two production installs in one checkout (FullStackProductionTests' stack): the command check prints to
    recover the other install's journal, pasted as printed, recovers it and changes nothing else (R12)."""

    OLD, NEW, NEWER = FullStackProductionTests.OLD, FullStackProductionTests.NEW, FullStackProductionTests.NEWER
    REPOS = FullStackProductionTests.REPOS
    setUp = FullStackProductionTests.setUp
    manifest = FullStackProductionTests.manifest
    settings = FullStackProductionTests.settings
    paste = FullStackProductionTests.paste
    printed = FullStackProductionTests.printed
    versions = FullStackProductionTests.versions
    receipts = FullStackProductionTests.receipts
    stage = FullStackProductionTests.stage

    def test_the_printed_recovery_for_another_installs_journal_only_recovers_it(self):
        other_env = self.root / "deploy" / "prod eu.env"
        other_env.write_text(self.env_file.read_text())
        other = "./bin/update --production --env-file 'deploy/prod eu.env'"

        # The default install's finishing run is killed once it has replaced the backend.
        self.stage()
        self.engine.killed_after = "backend"
        with self.assertRaises(SystemExit):
            self.paste("./bin/update --production apply --acknowledge-stale-stage")
        self.assertEqual(StateDir(self.root).read_journal()["stage"], "rollout")
        containers, running = self.engine.container_ids(), self.engine.running()
        env_text, other_text, ups = self.env_file.read_text(), other_env.read_text(), len(self.engine.up_calls())

        code, out, err = self.paste(f"{other} status")
        self.assertEqual(code, 2, err)
        self.assertIn("environment_file", err)
        from_status = self.printed(out, r"run `([^`]+)` \(without --yes it only recovers that run")
        code, out, err = self.paste(f"{other} check")
        self.assertEqual(code, 2, err)
        self.assertIn('environment_file', err)
        # Status still prints the owning install's recovery command even when current provenance is refused.
        recover, again = from_status, f"{other} check"
        self.assertEqual(recover, './bin/update --production rollback')

        # Pasted as printed: the killed run is recovered, and nothing else changes. Round 4 (F7): that is a
        # success, and --yes is mentioned only as the optional way to roll that install back too.
        code, out, err = self.paste(recover)
        self.assertEqual(code, 0, err)
        self.assertIn(f"Recovered an interrupted apply to {self.NEW}", err)
        self.assertNotIn("rollback not started", err)
        self.assertIsNone(StateDir(self.root).read_journal())
        self.assertEqual(self.receipts()[-1]["status"], "interrupted_during_rollout")
        self.assertEqual((self.engine.container_ids(), self.engine.running()), (containers, running))
        self.assertEqual((self.env_file.read_text(), other_env.read_text(), len(self.engine.up_calls())),
                         (env_text, other_text, ups))

        code, out, err = self.paste(again)
        self.assertNotIn("must be recovered before", out + err)
        self.assertNotIn("Interrupted", self.paste(f"{other} status")[1])



# ================================== CLI round 4 (__main__ / bin/update owner) ====

class CliRound4MainTests(unittest.TestCase):
    """Round 4 in the CLI: printed commands reproduce the run that printed them (F2: the caller's directory and
    the release options), another install's journal is named by its kind and recovered by the command that only
    recovers it (F7), and a run that starts between the lock probe and the journal read is in progress (S53)."""

    setUp = MainTests.setUp
    patch = MainTests.patch
    main = MainTests.main
    journal = CliMainAdditions.journal
    receipt = CliRound2MainTests.receipt
    planner = CliRound2MainTests.planner
    lock_probe = CliRound3MainTests.lock_probe

    RECOVER_ONLY = "(without --yes it only recovers that run and changes nothing else)"
    APPLY_CONTINUES = "would recover it too, but would also continue that install's update"
    APPLY_AFTER_ROLLBACK = "would recover it too, but would then plan an update of that install, which can undo the rollback"

    def cd(self, root=None):
        return f"cd {shlex.quote(str(root or self.root))} && "

    def in_progress_reason(self, *argv, root=None, cwd=None):
        """check's reason while a run holds the lock: it names the check command to run again."""

        calls = self.planner()
        if not getattr(self, "probed", False):
            self.lock_probe(lambda state: "pid 4242")
            self.probed = True
        prefix = ["--root", str(root or self.root), "--cwd", str(cwd or self.cwd)]
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = cli.main([*prefix, *argv])
        self.assertEqual(code, 3, stderr.getvalue())
        reason = calls[-1]["journal_reason"]
        found = re.fullmatch(r"a bin/update run is in progress \(pid 4242\); wait for it to finish, then run `([^`]+)` "
                             r"again", reason)
        self.assertIsNotNone(found, reason)
        return found.group(1)

    # ------------------------------------------- the caller's directory (F2) --
    def test_the_installation_knows_the_directory_bin_update_was_run_from(self):
        code, _, err = self.main("status")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.installations[-1].caller_cwd, self.cwd.resolve())
        link = self.cwd.parent / "link to caller"
        link.symlink_to(self.cwd, target_is_directory=True)
        code, _, err = self.main("--cwd", str(link), "status", cwd=False)
        self.assertEqual(code, 0, err)
        self.assertEqual(self.installations[-1].caller_cwd, self.cwd.resolve())
        # Run without bin/update's --cwd (python -m): commands are printed for the checkout.
        code, _, err = self.main("status", cwd=False)
        self.assertEqual(code, 0, err)
        self.assertEqual(self.installations[-1].caller_cwd, self.root)

    def test_commands_start_in_the_checkout_when_bin_update_ran_elsewhere(self):
        self.assertEqual(self.in_progress_reason("check"), f"{self.cd()}./bin/update check")
        deploy = self.root / "deploy"
        deploy.mkdir()
        self.assertEqual(self.in_progress_reason("check", cwd=deploy), f"{self.cd()}./bin/update check")

    def test_commands_are_unchanged_when_bin_update_ran_in_the_checkout_or_a_link_to_it(self):
        self.assertEqual(self.in_progress_reason("check", cwd=self.root), "./bin/update check")
        link = self.root.parent / "checkout link"
        link.symlink_to(self.root, target_is_directory=True)
        self.assertEqual(self.in_progress_reason("check", cwd=link), "./bin/update check")

    def test_a_checkout_path_with_a_space_is_quoted_so_the_command_pastes(self):
        spaced = self.root.parent / "my checkout"
        spaced.mkdir()
        # The operator, in deploy/, names the env file relative to it; the command names it from the checkout.
        command = self.in_progress_reason("--production", "--env-file", "my prod.env", "check", root=spaced,
                                          cwd=spaced / "deploy")
        self.assertEqual(command, f"cd '{spaced}' && ./bin/update --production --env-file 'deploy/my prod.env' check")
        self.assertEqual(shlex.split(command)[:3], ["cd", str(spaced), "&&"])

    # --------------------------------------------- the release options (F2) --
    def test_the_release_options_given_reach_the_plan_and_the_updater_with_local_files_made_absolute(self):
        calls = self.planner()
        home = self.root.parent / "home"
        home.mkdir()
        with mock.patch.dict(os.environ, {"HOME": str(home)}):
            code, _, err = self.main("apply", "--manifest", "my files/m.json", "--bundle", "~/m.json.sigstore.json",
                                     "--policy", "major", "--channel", "beta")
        self.assertEqual(code, 0, err)
        expected = ["--manifest", str(self.cwd / "my files" / "m.json"), "--bundle",
                    str(home / "m.json.sigstore.json"), "--policy", "major", "--channel", "beta"]
        self.assertEqual(self.Updater.call_args[1]["command_options"], expected)
        self.Updater.return_value.apply.call_args[0][0]([])
        self.assertEqual(calls[-1]["command_options"], expected)
        # What was fetched is what the printed commands name.
        self.assertEqual((self.fetch_calls[-1]["source"], self.fetch_calls[-1]["bundle"]), (expected[1], expected[3]))

    def test_urls_are_kept_as_given_and_options_before_the_command_count_too(self):
        calls = self.planner()
        url = "https://example.test/releases/download/v1.2.0/future-agi-update-manifest.json"
        code, _, err = self.main("--channel", "beta", "--manifest", url, "check", "--bundle", url + ".sigstore.json")
        self.assertEqual(code, 0, err)
        self.assertEqual(calls[-1]["command_options"],
                         ["--manifest", url, "--bundle", url + ".sigstore.json", "--channel", "beta"])

    def test_settings_are_not_repeated_and_rollback_takes_no_release_options(self):
        calls = self.planner()
        (self.root / "update.conf").write_text("FUTURE_AGI_UPDATE_POLICY=major\nFUTURE_AGI_UPDATE_CHANNEL=beta\n"
                                               "FUTURE_AGI_UPDATE_MANIFEST_URL=https://mirror.test/m.json\n")
        code, _, err = self.main("check")
        self.assertEqual(code, 0, err)
        self.assertEqual(calls[-1]["command_options"], [])
        code, _, err = self.main("apply")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.Updater.call_args[1]["command_options"], [])
        code, _, err = self.main("rollback", "--yes")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.Updater.call_args[1]["command_options"], [])

    def test_the_check_to_run_again_repeats_the_release_options(self):
        command = self.in_progress_reason("check", "--manifest", "my files/m.json", "--policy", "major")
        self.assertEqual(command, f"{self.cd()}./bin/update check --manifest "
                                  f"{shlex.quote(str(self.cwd / 'my files' / 'm.json'))} --policy major")

    def test_another_installs_recovery_takes_no_release_options_but_the_check_after_it_does(self):
        calls = self.planner()
        self.journal(env_file="deploy/.env.production", production=True)
        code, out, err = self.main("check", "--manifest", "m.json")
        self.assertEqual(code, 3, err)
        theirs = f"{self.cd()}./bin/update --production"
        check = f"{self.cd()}./bin/update check --manifest {shlex.quote(str(self.cwd / 'm.json'))}"
        self.assertEqual(calls[-1]["journal_reason"], (
            "an interrupted bin/update run for deploy/.env.production must be recovered before apply or rollback can "
            f"run for this install: run `{theirs} rollback` {self.RECOVER_ONLY}, then `{check}` again; "
            f"`{theirs} apply` {self.APPLY_CONTINUES}"))

    # ------------------------------------ another install's journal (F7) --
    def test_check_and_status_word_another_installs_journal_by_its_kind(self):
        calls = self.planner()
        theirs = f"{self.cd()}./bin/update --production"
        for kind, apply_does in (("apply", self.APPLY_CONTINUES), ("rollback", self.APPLY_AFTER_ROLLBACK)):
            with self.subTest(kind=kind):
                self.journal(env_file="deploy/.env.production", production=True, kind=kind, stage="env_written")
                code, out, err = self.main("check")
                self.assertEqual(code, 3, err)
                self.assertTrue(calls[-1]["journal_reason"].endswith(f"; `{theirs} apply` {apply_does}"),
                                calls[-1]["journal_reason"])
                code, out, err = self.main("status")
                self.assertEqual(code, 2, err)
                line = [line for line in out.splitlines() if line.startswith("Interrupted")]
                self.assertEqual(len(line), 1, out)
                self.assertTrue(line[0].endswith(f"run `{theirs} rollback` {self.RECOVER_ONLY}; `{theirs} apply` "
                                                 f"{apply_does}"), line[0])
        self.assertNotIn("continue that install's update", line[0])

    def test_an_interrupt_with_another_installs_journal_names_the_command_that_only_recovers_it(self):
        self.lock_probe(lambda state: None)

        def stop(*args, **kwargs):
            self.journal(env_file="deploy/.env.production", production=True, kind="apply", stage="rollout")
            raise KeyboardInterrupt()
        self.Updater.return_value.apply.side_effect = stop
        code, _, err = self.main("apply")
        self.assertEqual(code, 130)
        theirs = f"{self.cd()}./bin/update --production"
        self.assertEqual(err, "bin/update: interrupted; an apply for deploy/.env.production stopped earlier and must be "
                              "recovered before apply or rollback can run for this install: "
                              f"run `{theirs} rollback` {self.RECOVER_ONLY}; `{theirs} apply` {self.APPLY_CONTINUES}\n")
        self.assertNotIn("recovers it first", err)

    def test_an_interrupt_while_another_run_holds_the_lock_does_not_call_its_journal_interrupted(self):
        # Ctrl-C while this apply waited for the lock: the journal there is the live record of the run holding it.
        self.journal(env_file="deploy/.env.production", production=True, stage="pulled")
        self.lock_probe(lambda state: "pid 4242")
        self.Updater.return_value.apply.side_effect = KeyboardInterrupt()
        code, _, err = self.main("apply")
        self.assertEqual(code, 130)
        self.assertEqual(err, "bin/update: interrupted; another bin/update run is in progress (pid 4242): wait for it "
                              f"to finish, then run `{self.cd()}./bin/update status` to see what it did\n")

    def test_an_interrupt_with_this_installs_journal_names_apply_with_its_release_options(self):
        self.lock_probe(lambda state: None)

        def stop(*args, **kwargs):
            self.journal(**CLI_IDENTITY)
            raise KeyboardInterrupt()
        self.Updater.return_value.apply.side_effect = stop
        code, _, err = self.main("apply", "--manifest", "m.json", "--policy", "major")
        self.assertEqual(code, 130)
        apply = f"{self.cd()}./bin/update apply --manifest {shlex.quote(str(self.cwd / 'm.json'))} --policy major"
        self.assertEqual(err, f"bin/update: interrupted before the apply finished; the next `{apply}` or "
                              f"`{self.cd()}./bin/update rollback` recovers it first (`{self.cd()}./bin/update status` "
                              "shows where it stopped)\n")

    def test_rollback_without_yes_that_only_recovered_a_run_exits_0(self):
        # Updater.rollback returns normally once it has only recovered a run (F7, the apply side); that is a success.
        recovered = "Recovered an interrupted apply to v1.2.0: it stopped while containers were being replaced (receipt /r)"

        def rollback(receipt, *, yes=False):
            self.Updater.call_args[1]["out"].write(recovered + "\n")
            return None
        self.Updater.return_value.rollback.side_effect = rollback
        code, out, err = self.main("rollback")
        self.assertEqual((code, out, err), (0, "", recovered + "\n"))
        self.assertIs(self.Updater.return_value.rollback.call_args[1]["yes"], False)

    # -------------------------------------- a run that starts meanwhile (S53) --
    def starts_while_the_journal_is_read(self):
        """No run holds the lock when it is first probed; one takes it and writes its first journal right after."""

        probes = []

        def during(state):
            probes.append(state.read_journal())
            if len(probes) == 1:
                self.journal(**CLI_IDENTITY, stage="prepare", target_version="v1.3.0")
                return None
            return "pid 4242"
        self.lock_probe(during)
        return probes

    def test_status_reports_a_run_that_started_after_the_lock_was_probed_as_in_progress(self):
        probes = self.starts_while_the_journal_is_read()
        code, out, err = self.main("status")
        self.assertEqual(code, 0, err)
        self.assertEqual(len(probes), 2)
        self.assertIn("In progress   a bin/update run is in progress (pid 4242); wait for it to finish (now: apply to "
                      "v1.3.0, stage prepare)\n", out)
        self.assertNotIn("Interrupted", out)
        self.assertNotIn("recovers it", out)

    def test_check_blocks_on_a_run_that_started_after_the_lock_was_probed(self):
        calls = self.planner()
        self.starts_while_the_journal_is_read()
        code, out, err = self.main("check")
        self.assertEqual(code, 3, err)
        self.assertEqual(calls[-1]["journal_reason"], (
            f"a bin/update run is in progress (pid 4242); wait for it to finish, then run `{self.cd()}./bin/update "
            "check` again"))
        self.assertEqual(calls[-1]["history"], [])
        self.assertEqual(self.installations[0].overlays, [])
        self.assertNotIn(JOURNAL_NOTE, out)

    def test_the_lock_is_probed_again_only_when_a_journal_was_read_with_it_free(self):
        probe = self.lock_probe(lambda state: None)
        self.main("status")
        self.assertEqual(probe.call_count, 1)
        self.journal(**CLI_IDENTITY, stage="rollout")
        probe.reset_mock()
        _, out, _ = self.main("status")
        self.assertEqual(probe.call_count, 2)
        self.assertIn("Interrupted   an apply to v1.2.0 stopped at stage rollout; the next apply or rollback recovers it",
                      out)


class CliRound4FullStackTests(unittest.TestCase):
    """FullStackProductionTests' stack, as an operator in another directory uses it (F2), with the recover-only
    rollback (F7) and the Compose model every command plans with (F1)."""

    OLD, NEW, NEWER = FullStackProductionTests.OLD, FullStackProductionTests.NEW, FullStackProductionTests.NEWER
    REPOS = FullStackProductionTests.REPOS
    manifest = FullStackProductionTests.manifest
    settings = FullStackProductionTests.settings
    paste = FullStackProductionTests.paste
    printed = FullStackProductionTests.printed
    versions = FullStackProductionTests.versions
    receipts = FullStackProductionTests.receipts
    receipt_paths = FullStackProductionTests.receipt_paths
    backups = FullStackProductionTests.backups
    running = FullStackProductionTests.running
    stage = FullStackProductionTests.stage

    def setUp(self):
        FullStackProductionTests.setUp(self)  # FakeEngine answers `config --profiles` (no profiles): a complete model
        self.elsewhere = self.root.parent / "operator home"
        self.elsewhere.mkdir()

    def paste_in(self, command):
        """Run `command` in the operator's shell (self.shell, a directory): `cd DIR && ` moves the shell there
        first, as a shell does, and ./bin/update only exists in the checkout (bin/update passes the shell's
        directory as --cwd)."""

        words = shlex.split(command)
        if words[:1] == ["cd"]:
            self.assertEqual(words[2], "&&", command)
            self.shell = self.shell / words[1]
            words = words[3:]
        if words[0] == "./bin/update":
            self.assertEqual(self.shell.resolve(), self.root.resolve(), f"no ./bin/update in {self.shell}: {command}")
        else:
            self.assertEqual(words[0], str(self.root / "bin" / "update"), command)
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = cli.main(["--root", str(self.root), "--cwd", str(self.shell), *words[1:]])
        return code, stdout.getvalue(), stderr.getvalue()

    def commands(self, text):
        """Every command in backquotes in `text` that runs bin/update or Compose."""

        return [command for command in re.findall(r"`([^`]+)`", text)
                if "bin/update" in command or "compose" in command]

    def assert_pastable_here(self, text, option):
        """Every command in `text` works pasted in the operator's shell: `cd <checkout> && ` first unless the
        shell is in the checkout, and the release options on the apply and check commands only."""

        cd = "" if self.shell.resolve() == self.root.resolve() else f"cd {shlex.quote(str(self.root))} && "
        printed = self.commands(text)
        self.assertTrue(printed, text)
        for command in printed:
            with self.subTest(command=command):
                self.assertTrue(command.startswith(cd), command)
                if cd == "":
                    self.assertFalse(command.startswith("cd "), command)
                if "bin/update" in command and " rollback" in command:
                    self.assertNotIn("--manifest", command)
                elif "bin/update" in command:
                    self.assertIn(option, command)

    def newer_manifest(self):
        """update.conf follows v1.1.0; the operator names v1.2.0's manifest relative to their directory."""

        relative = os.path.relpath(self.manifests[self.NEWER], self.elsewhere)
        return relative, f"--manifest {shlex.quote(str(self.elsewhere / relative))}"

    def test_the_next_command_check_prints_in_another_directory_works_pasted_there(self):
        self.shell = self.elsewhere
        relative, option = self.newer_manifest()
        bin_update = shlex.quote(str(self.root / "bin" / "update"))
        code, out, err = self.paste_in(f"{bin_update} --production check --format json --manifest "
                                       f"{shlex.quote(relative)}")
        self.assertEqual(code, 0, err)
        plan = json.loads(out)
        self.assertEqual(plan["target_version"], self.NEWER)
        first = plan["next_command"]
        self.assertTrue(first.startswith(f"cd {shlex.quote(str(self.root))} && ./bin/update --production apply "
                                         "--stage"), first)
        self.assertIn(option, first)

        # Pasted where it was printed, it stages the release that was checked, not update.conf's; the shell is
        # in the checkout now, and what this run prints works there.
        code, out, err = self.paste_in(first)
        self.assertEqual(code, 0, err)
        self.assertEqual(self.shell.resolve(), self.root.resolve())
        self.assertEqual(self.versions(), (self.NEWER, self.NEWER))
        self.assertEqual(self.receipts()[-1]["target_version"], self.NEWER)
        self.assert_pastable_here(err, option)
        code, out, err = self.paste_in(self.printed(err, r"Then run `([^`]+)` to restart the stack") + ' --acknowledge-stale-stage')
        self.assertEqual(code, 0, err)
        self.assertEqual(self.engine.running(), self.running(self.NEWER))

    def test_commands_printed_to_an_operator_in_another_directory_work_pasted_there(self):
        self.shell = self.elsewhere
        relative, option = self.newer_manifest()
        bin_update = shlex.quote(str(self.root / "bin" / "update"))
        code, out, err = self.paste_in(f"{bin_update} --production apply --stage --manifest {shlex.quote(relative)}")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.versions(), (self.NEWER, self.NEWER))
        self.assert_pastable_here(err, option)
        staged = self.receipt_paths()[-1]

        # The finishing apply, pasted in that directory, restarts the stack on the release that was staged.
        code, out, err = self.paste_in(self.printed(err, r"Then run `([^`]+)` to restart the stack") + ' --acknowledge-stale-stage')
        self.assertEqual(code, 0, err)
        self.assertEqual(self.engine.running(), self.running(self.NEWER))
        self.assertEqual(self.receipts()[-1]["resumes"], staged)
        self.shell = self.elsewhere
        code, out, err = self.paste_in(f"{bin_update} --production check --format json --manifest "
                                       f"{shlex.quote(relative)}")
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["decision"], "up_to_date")

    def test_the_recover_only_rollback_exits_0_and_changes_nothing_else(self):
        # The finishing run is killed once it has replaced the backend: there is an update to roll back.
        self.stage()
        self.engine.killed_after = "backend"
        with self.assertRaises(SystemExit):
            self.paste("./bin/update --production apply --acknowledge-stale-stage")
        containers, running = self.engine.container_ids(), self.engine.running()
        env_text, ups = self.env_file.read_text(), len(self.engine.up_calls())

        code, out, err = self.paste("./bin/update --production rollback")
        self.assertEqual(code, 0, err)
        self.assertIn(f"Recovered an interrupted apply to {self.NEW}", err)
        self.assertNotIn("bin/update:", err)
        self.assertNotIn("rollback not started", err)
        self.assertRegex(err, r"(?i)nothing else")
        # --yes only as the optional way to also roll back (there is an update to roll back here). Round 6 (U20):
        # the update it recovered stopped mid-rollout, so finishing it comes first and the rollback is "instead".
        mentions = [line for line in (out + err).splitlines() if "--yes" in line]
        self.assertTrue(mentions, err)
        for line in mentions:
            self.assertRegex(line, r"\b(too|as well|also|instead)\b")
        self.assertIn("To finish it, run `./bin/update --production apply`; to roll it back instead", err)
        self.assertIsNone(StateDir(self.root).read_journal())
        self.assertEqual(self.receipts()[-1]["status"], "interrupted_during_rollout")
        self.assertEqual((self.engine.container_ids(), self.engine.running()), (containers, running))
        self.assertEqual((self.env_file.read_text(), len(self.engine.up_calls())), (env_text, ups))

    def test_the_recover_only_rollback_with_nothing_to_roll_back_never_mentions_yes(self):
        # The first update is killed while pulling: recovering it puts the version keys back; nothing ran.
        def killed(ref):
            raise SystemExit("killed")
        self.engine.pull = killed
        with self.assertRaises(SystemExit):
            self.paste("./bin/update --production apply --stage")
        self.assertEqual(StateDir(self.root).read_journal()["stage"], "env_written")
        self.assertEqual(self.versions(), (self.NEW, self.NEW))
        containers = self.engine.container_ids()

        code, out, err = self.paste("./bin/update --production rollback")
        self.assertEqual(code, 0, err)
        self.assertIn(f"Recovered an interrupted apply to {self.NEW}", err)
        self.assertNotIn("bin/update:", err)
        self.assertRegex(err, r"(?i)nothing else")
        self.assertNotIn("--yes", out + err)
        self.assertIsNone(StateDir(self.root).read_journal())
        self.assertEqual(self.versions(), (self.OLD, self.OLD))
        self.assertEqual((self.engine.container_ids(), self.engine.up_calls()), (containers, []))

    def test_every_command_plans_with_the_service_model_never_config_no_interpolate(self):
        # check and apply plan with Installation.service_model() (`config --profiles`, then the probe); no
        # command runs `config --no-interpolate`, which Compose before v2.40.3 rejects for these compose files.
        steps = (("check", 0), ("apply --stage --dry-run", 0), ("apply --stage", 0), ("check", 0),
                 ("apply --dry-run", 0), ("apply --acknowledge-stale-stage", 0), ("status", 0), ("rollback", 3), ("rollback --yes", 0))
        for step, expected in steps:
            command = f"./bin/update --production {step}"
            with self.subTest(command=command):
                before = len(self.engine.calls)
                code, out, err = self.paste(command)
                self.assertEqual(code, expected, err)
                calls = self.engine.calls[before:]
                self.assertEqual([call for call in calls if "--no-interpolate" in call], [])
                if step.split()[0] in ("check", "apply"):
                    self.assertTrue([call for call in calls if "--profiles" in call], command)
        self.assertEqual(self.engine.running(), self.running(self.OLD))


# ------------------------------------------------------------ round 4 --

class InstallRound4ComposeFiles:
    """`docker compose config` for compose files given as data, answering the way Compose does.

    `services`: name -> {"image": template, "profiles": [...], "build": {...},
    "missing_env_file": True}. Values come from the subprocess environment
    first, then the env file (--env-file, else the checkout's .env), and so
    does COMPOSE_PROFILES. `config --profiles` prints every profile, one per
    line; `config --format json` lists the enabled services and fails when an
    enabled one names an env_file that does not exist. `answers` replaces
    the answer to "profiles", "active" (config without the probe values) or
    "probe" calls.
    """

    def __init__(self, root, services, **answers):
        self.root = Path(root)
        self.services = services
        self.answers = answers
        self.runner = Runner(self.handle)

    @staticmethod
    def interpolate(template, values):
        """(the text, the error) of a Compose template: ${VAR}, ${VAR:-default}, ${VAR:?message}."""

        errors = []

        def substitute(match):
            name, operator, argument = match.group(1), match.group(2), match.group(3)
            value = values.get(name) or ""
            if operator == ":?" and not value:
                errors.append(f"required variable {name} is missing a value: {argument}")
            return value or (argument if operator == ":-" else "")
        text = re.sub(r"\$\{(\w+)(?:(:-|:\?)([^}]*))?\}", substitute, template)
        return text, (errors[0] if errors else None)

    def values(self, args, env):
        path = Path(args[args.index("--env-file") + 1]) if "--env-file" in args else self.root / ".env"
        values = dict(read_env(path)) if path.exists() else {}
        values.update(env)
        return values

    def handle(self, args, kwargs):
        _, rest = install.split_command(args)
        if rest == ["config", "--profiles"]:
            if "profiles" in self.answers:
                return self.answers["profiles"]
            names = sorted({name for spec in self.services.values() for name in spec.get("profiles", ())})
            return completed(0, "".join(name + "\n" for name in names))
        if rest != ["config", "--format", "json"]:
            return completed(0)
        kind = "probe" if is_probe(kwargs) else "active"
        if kind in self.answers:
            answer = self.answers[kind]
            return answer(kwargs) if callable(answer) else answer
        values = self.values(args, kwargs.get("env") or {})
        enabled = {name for name in (values.get("COMPOSE_PROFILES") or "").split(",") if name}
        model = {}
        for name, spec in self.services.items():
            entry = {}
            if "image" in spec:
                entry["image"], error = self.interpolate(spec["image"], values)
                if error:
                    return completed(1, "", f"error while interpolating services.{name}.image: {error}\n")
            if spec.get("profiles") and not enabled & set(spec["profiles"]):
                continue
            if spec.get("missing_env_file"):
                missing = self.root / f"{name}.env"
                return completed(1, "", f"env file {missing} not found: stat {missing}: no such file or directory\n")
            if "build" in spec:
                entry["build"] = spec["build"]
            model[name] = entry
        return completed(0, json.dumps({"name": "futureagi", "services": model}))

    def calls(self):
        """What each Compose call was: "active", "profiles", "probe" or "probe:<its COMPOSE_PROFILES>"."""

        labels = []
        for args, kwargs in self.runner.calls:
            _, rest = install.split_command(args)
            if rest == ["config", "--profiles"]:
                labels.append("profiles")
            elif rest == ["config", "--format", "json"] and is_probe(kwargs):
                profiles = kwargs["env"].get("COMPOSE_PROFILES")
                labels.append("probe" if profiles is None else f"probe:{profiles}")
            elif rest == ["config", "--format", "json"]:
                labels.append("active")
            else:
                labels.append(" ".join(rest))
        return labels


class InstallRound4ServiceModelTests(TempRootTest):
    """F1 (S14, S37, S38, S39): service_model() replaces `config --no-interpolate`.

    Round 5 (G1): config() now also takes its active service set from `config --services` on every release.

    It works on every Compose release the repository supports (v2.24+),
    whose `config --no-interpolate` rejects these compose files before
    v2.40.3, and lists every profile's services on all of them.
    """

    STANDALONE = {
        "app": {"image": "futureagi/standalone:${FUTURE_AGI_VERSION:-latest}"},
        "postgres": {"image": "postgres:16"},
        "serving": {"image": "futureagi/serving:${SERVING_VERSION:-latest}", "profiles": ["ml"]},
        "code-executor": {"image": "futureagi/code-executor:${CODE_EXECUTOR_VERSION:-latest}", "profiles": ["sandbox"]},
    }
    DISTRIBUTED = {
        "backend": {"image": "futureagi/future-agi:${FUTURE_AGI_VERSION:-latest}"},
        "frontend": {"image": "futureagi/frontend:${FRONTEND_VERSION:-latest}"},
        "fi-collector": {"image": "futureagi/fi-collector:${FI_COLLECTOR_VERSION:-local}", "build": {"context": "fi-collector"}},
        "worker-default": {"image": "futureagi/future-agi:${FUTURE_AGI_VERSION:-latest}",
                           "profiles": ["workers", "all", "full"]},
        "temporal-ui": {"image": "temporalio/ui:2.43.3", "profiles": ["observability", "all", "full"]},
        "redis": {"image": "redis:7-alpine"},
    }

    @staticmethod
    def entry(image, variable, active=True, buildable=False):
        return {"image": image, "variable": variable, "active": active, "buildable": buildable}

    def compose(self, services=None, **answers):
        return InstallRound4ComposeFiles(self.root, self.STANDALONE if services is None else services, **answers)

    def test_no_compose_command_uses_no_interpolate(self):
        for mode in ("standalone", "distributed", "production"):
            with self.subTest(mode=mode):
                fake = self.compose(self.DISTRIBUTED if mode != "standalone" else None)
                if mode == "production":
                    self.production_files()
                    inst = Installation(self.root, production=True, environ={}, runner=fake.runner, compose=COMPOSE)
                else:
                    env = "COMPOSE_FILE=docker-compose.distributed.yml\n" if mode == "distributed" else ""
                    inst = self.install(env, runner=fake.runner)
                self.assertEqual(inst.project, "futureagi")
                self.assertTrue(inst.service_model()["complete"])
                inst.futureagi_services()
                self.assertEqual([args for args in fake.runner.commands() if "--no-interpolate" in args], [])

    def test_standalone_lists_every_profile_and_which_key_selects_each_image(self):
        fake = self.compose()
        inst = self.install("FUTURE_AGI_VERSION=v1.0.0\nSERVING_VERSION=v1.0.0\n", runner=fake.runner)
        self.assertEqual(inst.service_model(), {"complete": True, "services": {
            "app": self.entry("futureagi/standalone:v1.0.0", "FUTURE_AGI_VERSION"),
            "code-executor": self.entry("futureagi/code-executor:bin-update-probe-CODE_EXECUTOR_VERSION",
                                        "CODE_EXECUTOR_VERSION", active=False),
            "postgres": self.entry("postgres:16", None),
            "serving": self.entry("futureagi/serving:bin-update-probe-SERVING_VERSION", "SERVING_VERSION",
                                  active=False),
        }})
        self.assertEqual(fake.calls(), ["active", "config --services", "profiles", "probe:ml,sandbox"])

    def test_an_enabled_profile_makes_its_services_active(self):
        fake = self.compose(self.DISTRIBUTED)
        inst = self.install("COMPOSE_FILE=docker-compose.distributed.yml\nCOMPOSE_PROFILES=workers\n"
                            "FUTURE_AGI_VERSION=v1.0.0\n", runner=fake.runner)
        services = inst.service_model()["services"]
        self.assertEqual(services["worker-default"], self.entry("futureagi/future-agi:v1.0.0", "FUTURE_AGI_VERSION"))
        self.assertEqual(services["temporal-ui"], self.entry("temporalio/ui:2.43.3", None, active=False))
        self.assertEqual(services["fi-collector"],
                         self.entry("futureagi/fi-collector:local", "FI_COLLECTOR_VERSION", buildable=True))
        self.assertEqual(fake.calls(), ["active", "config --services", "profiles", "probe:all,full,observability,workers"])

    def test_the_probe_sets_every_version_key_after_the_production_stripping(self):
        self.production_files("FUTURE_AGI_VERSION=v1.0.0\nFRONTEND_VERSION=v1.0.0\nSECRET_KEY=file\n")
        fake = self.compose(self.DISTRIBUTED)
        environ = {"PATH": "/bin", "FUTURE_AGI_VERSION": "v0.9.0", "SECRET_KEY": "shell", "COMPOSE_FILE": "x.yml",
                   "COMPOSE_PROFILES": "observability"}
        inst = Installation(self.root, production=True, environ=environ, runner=fake.runner, compose=COMPOSE)
        inst.overlay_env({"FRONTEND_VERSION": "v1.2.0"})
        model = inst.service_model()
        self.assertEqual(fake.calls(), ["active", "config --services", "profiles", "probe:all,full,observability,workers"])
        active_env, probe_env = fake.runner.calls[0][1]["env"], fake.runner.calls[3][1]["env"]
        self.assertEqual(active_env, {"PATH": "/bin", "COMPOSE_PROFILES": "observability", "FRONTEND_VERSION": "v1.2.0"})
        self.assertEqual(probe_env, {"PATH": "/bin", "COMPOSE_PROFILES": "all,full,observability,workers", **PROBE_ENV})
        self.assertEqual(model["services"]["frontend"], self.entry("futureagi/frontend:v1.2.0", "FRONTEND_VERSION"))
        self.assertEqual(model["services"]["backend"], self.entry("futureagi/future-agi:v1.0.0", "FUTURE_AGI_VERSION"))
        self.assertEqual(model["services"]["temporal-ui"], self.entry("temporalio/ui:2.43.3", None))
        self.assertFalse(model["services"]["worker-default"]["active"])

    def test_tags_written_in_a_compose_file_have_no_variable(self):
        digest = "sha256:" + "b" * 64
        fake = self.compose({
            "app": {"image": "futureagi/standalone:${FUTURE_AGI_VERSION:-latest}"},
            "serving": {"image": "futureagi/serving:${SERVING_VERSION:-latest}-gpu"},
            "frontend": {"image": "futureagi/frontend:v9.9.9"},
            "untagged": {"image": "futureagi/code-executor"},
            "prefixed": {"image": "futureagi/future-agi:build-${FUTURE_AGI_VERSION:-latest}"},
            "pinned": {"image": "futureagi/future-agi:${FUTURE_AGI_VERSION:-latest}@" + digest},
            "mirror": {"image": "registry.example.com:5000/mirror/standalone:${FUTURE_AGI_VERSION:-latest}"},
            "built": {"build": {"context": "."}},
        })
        inst = self.install("FUTURE_AGI_VERSION=v1.0.0\nSERVING_VERSION=v1.0.0\n", runner=fake.runner)
        self.assertEqual(inst.service_model(), {"complete": True, "services": {
            "app": self.entry("futureagi/standalone:v1.0.0", "FUTURE_AGI_VERSION"),
            "built": self.entry("", None, buildable=True),
            "frontend": self.entry("futureagi/frontend:v9.9.9", None),
            "mirror": self.entry("registry.example.com:5000/mirror/standalone:v1.0.0", "FUTURE_AGI_VERSION"),
            "pinned": self.entry(f"futureagi/future-agi:v1.0.0@{digest}", "FUTURE_AGI_VERSION"),
            "prefixed": self.entry("futureagi/future-agi:build-v1.0.0", None),
            "serving": self.entry("futureagi/serving:v1.0.0-gpu", "SERVING_VERSION"),
            "untagged": self.entry("futureagi/code-executor", None),
        }})
        self.assertEqual(fake.calls(), ["active", "config --services", "profiles", "probe"])

    def test_without_profiles_one_probe_without_compose_profiles_is_complete(self):
        fake = self.compose({"app": self.STANDALONE["app"]})
        inst = self.install("", runner=fake.runner)
        self.assertTrue(inst.service_model()["complete"])
        self.assertEqual(fake.calls(), ["active", "config --services", "profiles", "probe"])

    def test_a_profile_that_cannot_be_enabled_leaves_the_active_services_and_says_incomplete(self):
        # Compose resolves a disabled profile's services only when it is enabled: a missing env_file fails then.
        services = dict(self.STANDALONE, serving=dict(self.STANDALONE["serving"], missing_env_file=True))
        for env_text, active in (("FUTURE_AGI_VERSION=v1.0.0\n", ["app", "postgres"]),
                                 ("FUTURE_AGI_VERSION=v1.0.0\nCOMPOSE_PROFILES=sandbox\n",
                                  ["app", "code-executor", "postgres"])):
            with self.subTest(env=env_text):
                fake = self.compose(services)
                inst = self.install(env_text, runner=fake.runner)
                model = inst.service_model()
                self.assertFalse(model["complete"])
                self.assertEqual(sorted(model["services"]), active)
                self.assertTrue(all(entry["active"] for entry in model["services"].values()))
                self.assertEqual(model["services"]["app"],
                                 self.entry("futureagi/standalone:v1.0.0", "FUTURE_AGI_VERSION"))
                # The retry leaves COMPOSE_PROFILES to the install, as config() does.
                self.assertEqual(fake.calls(), ["active", "config --services", "profiles", "probe:ml,sandbox", "probe"])

    def test_profiles_compose_cannot_list_mean_an_incomplete_model(self):
        for answer in (completed(1, "", "unknown flag: --profiles\n"), completed(0, '{"name": "futureagi"}\n'),
                       completed(0, "ml\nnot a profile\n")):
            with self.subTest(answer=answer):
                fake = self.compose(profiles=answer)
                model = self.install("", runner=fake.runner).service_model()
                self.assertFalse(model["complete"])
                self.assertEqual(sorted(model["services"]), ["app", "postgres"])
                self.assertEqual(fake.calls(), ["active", "config --services", "profiles", "probe"])

    def test_profile_names_are_listed_once(self):
        fake = self.compose(profiles=completed(0, "ml\nsandbox\nml\n\n"))
        self.install("", runner=fake.runner).service_model()
        self.assertEqual(fake.calls(), ["active", "config --services", "profiles", "probe:ml,sandbox"])

    def test_both_probes_failing_is_an_error_naming_the_probe(self):
        fake = self.compose(probe=completed(1, "", "services.app.image must be a string\n"))
        inst = self.install("", runner=fake.runner)
        with self.assertRaises(InstallError) as caught:
            inst.service_model()
        self.assertEqual(str(caught.exception), (
            "`docker compose config` failed with exit code 1: services.app.image must be a string. bin/update ran "
            "it with every version key set to a placeholder (FUTURE_AGI_VERSION=bin-update-probe-FUTURE_AGI_VERSION, "
            "and so on) to learn which key selects each image. Make the compose files accept any tag in those keys, "
            "then try again"))
        self.assertEqual(fake.calls(), ["active", "config --services", "profiles", "probe:ml,sandbox", "probe"])

    def test_a_failing_active_config_is_reported_before_any_probe(self):
        fake = self.compose(active=completed(15, "", "yaml: line 3: did not find expected key\n"))
        inst = self.install("", runner=fake.runner)
        with self.assertRaises(InstallError) as caught:
            inst.service_model()
        self.assertEqual(str(caught.exception),
                         "`docker compose config` failed with exit code 15: yaml: line 3: did not find expected key")
        self.assertEqual(fake.calls(), ["active"])

    def test_an_active_service_the_probe_does_not_list_has_no_variable_and_is_incomplete(self):
        probed = {"name": "futureagi", "services": {"app": {"image": "futureagi/standalone:bin-update-probe-FUTURE_AGI_VERSION"}}}
        fake = self.compose({"app": self.STANDALONE["app"], "frontend": {"image": "futureagi/frontend:${FRONTEND_VERSION}"}},
                            probe=completed(0, json.dumps(probed)))
        model = self.install("FRONTEND_VERSION=v1.0.0\n", runner=fake.runner).service_model()
        self.assertFalse(model["complete"])
        self.assertEqual(model["services"]["frontend"], self.entry("futureagi/frontend:v1.0.0", None))
        self.assertEqual(model["services"]["app"]["variable"], "FUTURE_AGI_VERSION")

    def test_cached_and_rebuilt_for_a_refreshed_active_model_without_probing_again(self):
        fake = self.compose()
        inst = self.install("FUTURE_AGI_VERSION=v1.0.0\n", runner=fake.runner)
        first = inst.service_model()
        self.assertIs(inst.service_model(), first)
        self.assertEqual(len(fake.runner.calls), 4)
        # apply rewrites the env file and reads the active model again (config(refresh=True)).
        self.write(".env", "FUTURE_AGI_VERSION=v1.2.0\n")
        inst.config(refresh=True)
        again = inst.service_model()
        self.assertEqual(again["services"]["app"], self.entry("futureagi/standalone:v1.2.0", "FUTURE_AGI_VERSION"))
        self.assertEqual(first["services"]["app"]["image"], "futureagi/standalone:v1.0.0")
        self.assertEqual(fake.calls(), ["active", "config --services", "profiles", "probe:ml,sandbox", "active",
                                       "config --services"] )
        inst.service_model(refresh=True)
        self.assertEqual(fake.calls()[4:], ["active", "config --services", "active", "config --services",
                                           "profiles", "probe:ml,sandbox"])

    def test_probe_variable(self):
        for image, variable in (
                ("futureagi/serving:bin-update-probe-SERVING_VERSION-gpu", "SERVING_VERSION"),
                ("localhost:5000/futureagi/frontend:bin-update-probe-FRONTEND_VERSION", "FRONTEND_VERSION"),
                ("futureagi/frontend:bin-update-probe-FRONTEND_VERSION@sha256:" + "c" * 64, "FRONTEND_VERSION"),
                ("localhost:5000/futureagi/frontend", None), ("futureagi/frontend:v1.0.0", None),
                ("futureagi/frontend:x-bin-update-probe-FRONTEND_VERSION", None), ("", None)):
            with self.subTest(image=image):
                self.assertEqual(install.probe_variable(image), variable)
        with mock.patch.object(install, "VERSION_VARIABLES", ("FOO", "FOO_BAR")):
            self.assertEqual(install.probe_variable("x/y:bin-update-probe-FOO_BAR"), "FOO_BAR")
            self.assertEqual(install.probe_variable("x/y:bin-update-probe-FOO-gpu"), "FOO")


class InstallRound4CallerCwdTests(TempRootTest):
    """F2 (S15): commands printed for an operator who ran bin/update elsewhere start with `cd <checkout> && `."""

    def setUp(self):
        super().setUp()
        self.checkout = self.root / "it's the checkout"
        self.checkout.mkdir()
        self.elsewhere = self.root / "home"
        self.elsewhere.mkdir()
        self.cd = f"cd {shlex.quote(str(self.checkout))} && "

    def make(self, *, production=False, env_text="", **kwargs):
        if production:
            for relative, text in ((PRODUCTION_ENV, "FUTURE_AGI_VERSION=v1.0.0\n"), (DISTRIBUTED_FILE, "services: {}\n"),
                                   (PRODUCTION_OVERLAY, "services: {}\n")):
                (self.checkout / relative).parent.mkdir(parents=True, exist_ok=True)
                (self.checkout / relative).write_text(text, encoding="utf-8")
        else:
            (self.checkout / ".env").write_text(env_text, encoding="utf-8")
        kwargs.setdefault("environ", {})
        kwargs.setdefault("runner", Runner())
        return Installation(self.checkout, production=production, compose=COMPOSE, **kwargs)

    def test_the_default_is_the_checkout_and_adds_nothing(self):
        inst = self.make()
        self.assertEqual(inst.caller_cwd, self.checkout)
        self.assertEqual((inst.cd_prefix(), inst.compose_hint()), ("", "docker compose"))
        self.assertEqual(self.make(caller_cwd=self.checkout).compose_hint(), "docker compose")

    def test_another_directory_gets_the_cd_prefix(self):
        self.assertEqual(self.make(caller_cwd=self.elsewhere).compose_hint(), self.cd + "docker compose")
        self.assertEqual(self.make(production=True, caller_cwd=self.elsewhere).compose_hint(),
                         self.cd + "docker compose --env-file deploy/.env.production -f docker-compose.distributed.yml "
                                   "-f deploy/docker-compose.production.yml")
        # A subdirectory of the checkout is another directory too: the paths are the checkout's.
        self.assertEqual(self.make(caller_cwd=self.checkout / "deploy").cd_prefix(), self.cd)
        self.assertEqual(self.make(caller_cwd=self.root / "gone").cd_prefix(), self.cd)

    def test_a_symlink_to_the_checkout_is_the_checkout(self):
        link = self.elsewhere / "link"
        link.symlink_to(self.checkout)
        self.assertEqual(self.make(caller_cwd=link).cd_prefix(), "")

    def test_set_after_construction_or_never_set(self):
        inst = self.make()
        inst.caller_cwd = self.elsewhere
        self.assertEqual(inst.compose_hint(), self.cd + "docker compose")
        bare = bare_installation(self.checkout, Runner())
        self.assertEqual(bare.compose_hint(), "docker compose")

    def test_pasted_in_the_callers_directory_compose_runs_in_the_checkout_with_these_arguments(self):
        bindir = self.root / "fake bin"
        executable(bindir / "docker", "#!/bin/sh\npwd\nfor a in \"$@\"; do printf '%s\\n' \"$a\"; done\n")
        path = str(bindir) + os.pathsep + os.defpath
        inst = self.make(production=True, caller_cwd=self.elsewhere, environ={"PATH": path})
        result = subprocess.run(["/bin/sh", "-c", inst.compose_hint() + " ps -a"], cwd=str(self.elsewhere),
                                env={"PATH": path}, capture_output=True, text=True, timeout=60, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = result.stdout.splitlines()
        self.assertEqual(Path(lines[0]).resolve(), self.checkout)
        self.assertEqual(lines[1:], ["compose", "--env-file", "deploy/.env.production", *PRODUCTION_HINT_FILES, "ps", "-a"])
        self.assertTrue((self.checkout / lines[3]).is_file())

    def test_printed_bin_update_commands_of_the_installation_get_it_too(self):
        (self.checkout / "deploy").mkdir()
        (self.checkout / PRODUCTION_ENV).write_text("", encoding="utf-8")
        for caller, prefix in ((None, ""), (self.elsewhere, self.cd)):
            with self.subTest(caller=caller):
                with self.assertRaises(InstallError) as caught:
                    Installation(self.checkout, environ={}, runner=Runner(), compose=COMPOSE, caller_cwd=caller)
                # Round 5 (T17): with a bin/update command, so that it works pasted.
                self.assertTrue(str(caught.exception).endswith(
                    "this looks like a deploy/setup.sh production install. Add --production to the command, as in "
                    f"`{prefix}./bin/update --production check`"), str(caught.exception))
        (self.checkout / PRODUCTION_ENV).unlink()
        (self.checkout / DISTRIBUTED_FILE).write_text("services: {}\n")
        bootstrap = container_record("postgres-schema-bootstrap", status="exited", cmd=["python", "bootstrap.py", "--check"])
        for caller, prefix in ((None, ""), (self.elsewhere, self.cd)):
            with self.subTest(caller=caller):
                inst = self.make(env_text="COMPOSE_FILE=docker-compose.distributed.yml\n", caller_cwd=caller,
                                 runner=Runner(docker_handler(containers=[container_record("backend"), bootstrap])))
                # Round 5 (T17, T55): with a bin/update command; deploy/.env.production is gone here.
                self.assertEqual(inst.check_stack(inst.containers()), [
                    f"this install runs the production overlay (check-only bootstrap jobs). Use `{prefix}./bin/update "
                    "--production check` with --env-file naming the env file deploy/setup.sh used "
                    "(deploy/.env.production does not exist), which uses it and the overlay as deploy/setup.sh does"])


@unittest.skipUnless(os.environ.get("FUTURE_AGI_UPDATE_COMPOSE_MATRIX"), "set FUTURE_AGI_UPDATE_COMPOSE_MATRIX to a directory of docker-compose-<version> "
                                     "binaries to check service_model() against real Compose releases")
class InstallRound4ComposeMatrixTests(unittest.TestCase):
    """F1 and round 5's G1 against real Compose releases: config() and service_model() on scratch copies of this
    repository's compose files.

    Opt-in: FUTURE_AGI_UPDATE_COMPOSE_MATRIX names a directory of Docker
    Compose binaries named docker-compose-<version>; every one of them runs
    (round 5: every release from v2.24.0 to v2.40.3 and v5.0.0 to v5.6.0).
    `compose config` never contacts the Docker daemon, so none is needed.
    Each binary is resolved as bin/update resolves Compose
    (resolve_compose_command), which records its version. Each result is
    compared with plain runs of the same binary: `config --services` names the
    active services (on v2.24.7 `config --format json` lists every
    profile's), `config --format json` gives their images. v2.25.0 (no
    `config --format`) must be refused before any other Compose call, and
    v2.24.1, which rejects the production overlay, must say to upgrade
    Compose. tearDownClass prints each release's results.
    """

    REFUSED = (2, 25, 0)
    REJECTS_PRODUCTION = (2, 24, 1)
    REPORTS_A_COMMIT = ((2, 32, 3),)

    @staticmethod
    def version(binary):
        return install.parse_compose_version(binary.name[len("docker-compose-"):])

    @classmethod
    def setUpClass(cls):
        directory = Path(os.environ["FUTURE_AGI_UPDATE_COMPOSE_MATRIX"]).expanduser()
        cls.binaries = sorted((path for path in directory.glob("docker-compose-*")
                               if path.is_file() and os.access(path, os.X_OK) and cls.version(path)),
                              key=cls.version)
        if not cls.binaries:
            raise unittest.SkipTest(f"no executable docker-compose-* in {directory}")
        cls.results = {}

    @classmethod
    def tearDownClass(cls):
        for binary in cls.binaries:
            outcomes = cls.results.get(binary.name) or {}
            sys.stderr.write(f"{binary.name}: " + ", ".join(f"{name} {outcome}" for name, outcome
                                                              in sorted(outcomes.items())) + "\n")

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        (self.base / "home").mkdir()
        self.environ = {"PATH": os.environ.get("PATH", os.defpath), "HOME": str(self.base / "home")}
        if "DOCKER_CONTEXT" in os.environ:
            self.environ["DOCKER_CONTEXT"] = os.environ["DOCKER_CONTEXT"]
            self.environ["DOCKER_CONFIG"] = os.environ.get("DOCKER_CONFIG", str(Path.home() / ".docker"))

    def checkout(self, name, *, production=False, env_lines=(), override=None):
        root = self.base / name
        for relative in (STANDALONE_FILE, DISTRIBUTED_FILE, PRODUCTION_OVERLAY):
            (root / relative).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / relative, root / relative)
        if production:
            lines = []
            for line in (ROOT / "deploy/.env.production.example").read_text(encoding="utf-8").splitlines():
                match = re.fullmatch(r"(\w+)=", line)
                if match and match.group(1) in VERSION_VARIABLES:
                    line += "v1.0.0"
                elif match and re.search(r"(SECRET|PASSWORD|TOKEN|_KEY)$", match.group(1)):
                    line += f"dummy-{match.group(1).lower()}-0123456789abcdef"
                lines.append(line)
            (root / PRODUCTION_ENV).write_text("\n".join([*lines, *env_lines]) + "\n", encoding="utf-8")
        else:
            text = (ROOT / ".env.example").read_text(encoding="utf-8")
            (root / ".env").write_text(text + "\n" + "".join(line + "\n" for line in env_lines), encoding="utf-8")
        if override is not None:
            (root / "docker-compose.override.yml").write_text(override, encoding="utf-8")
        return root

    def installation(self, binary, root, production=False):
        """The Installation bin/update builds with this binary as the only Compose on PATH, and its Compose calls."""

        commands = self.called = []

        def runner(args, **kwargs):
            commands.append(list(args))
            return subprocess.run(args, **kwargs)
        compose = install.resolve_compose_command(runner, lambda name: str(binary) if name == "docker-compose" else None)
        # v2.32.3's binaries report a commit (083f676) as their version: it is unknown, and that is no refusal.
        version = None if self.version(binary) in self.REPORTS_A_COMMIT else self.version(binary)
        self.assertEqual(compose.version, version)
        inst = Installation(root, production=production, environ=self.environ, runner=runner, compose=compose)
        self.assertEqual(inst.compose_version, version)
        return inst, commands

    def each_release(self, name, root, check, production=False):
        """check(installation, its Compose calls, version) for every release that can run, after checking the others' refusals.

        Each release is a subtest; the outcomes go to tearDownClass's report.
        """

        for binary in self.binaries:
            with self.subTest(compose=binary.name):
                version = self.version(binary)
                outcomes = self.results.setdefault(binary.name, {})
                outcomes[name] = "FAILED"
                if version == self.REFUSED:
                    with self.assertRaises(InstallError) as caught:
                        self.installation(binary, root, production)
                    self.assertEqual(str(caught.exception), install.COMPOSE_REFUSED[version])
                    # Refused before any other Compose call: the version was all bin/update asked.
                    self.assertEqual(self.called, [[str(binary), "version", "--short"]])
                    outcomes[name] = "refused (no JSON config)"
                    continue
                inst, commands = self.installation(binary, root, production)
                # The version was asked first, and nothing else yet.
                self.assertEqual(commands, [[str(binary), "version", "--short"]])
                if production and version == self.REJECTS_PRODUCTION:
                    with self.assertRaises(InstallError) as caught:
                        inst.service_model()
                    self.assertTrue(str(caught.exception).endswith(install.COMPOSE_CONFIG_HINTS[version]),
                                    str(caught.exception))
                    self.assertIn("must be unique", str(caught.exception))
                    outcomes[name] = "rejected the overlay (upgrade hint)"
                    continue
                check(inst, commands, version)
                outcomes[name] = "ok"

    def plain(self, inst, *args, profiles=None):
        """`config <args>` as Compose answers it with the install's own values (no probe)."""

        env = inst.subprocess_env()
        if profiles is not None:
            env["COMPOSE_PROFILES"] = profiles
        result = subprocess.run(inst.compose_args("config", *args), cwd=str(inst.root), env=env,
                                capture_output=True, text=True, timeout=120, check=False)
        if result.returncode != 0:
            raise AssertionError(result.stderr)
        return result.stdout

    def plain_config(self, inst, profiles=None):
        """The services Compose runs (`config --services`), each with its `config --format json` definition."""

        listed = json.loads(self.plain(inst, "--format", "json", profiles=profiles))["services"]
        names = self.plain(inst, "--services", profiles=profiles).split()
        self.assertTrue(set(names) <= set(listed), names)
        return {name: listed[name] for name in names}

    @staticmethod
    def futureagi(services):
        """service -> component, for the services on Future AGI images."""

        found = {}
        for name, service in services.items():
            try:
                component = component_for(parse_image_ref(service.get("image") or ""))
            except ManifestError:
                component = None
            if component:
                found[name] = component
        return found

    def check(self, inst, commands, *, complete=True):
        model = inst.service_model()
        self.assertEqual([args for args in commands if "--no-interpolate" in args], [])
        self.assertEqual(model["complete"], complete)
        services = model["services"]
        active = self.plain_config(inst)
        self.assertEqual({name for name, entry in services.items() if entry["active"]}, set(active))
        self.assertEqual(set(inst.config()["services"]), set(active))
        for name, service in active.items():
            self.assertEqual(services[name]["image"], service.get("image", ""), name)
        if complete:
            profiles = self.plain(inst, "--profiles").split()
            everything = self.plain_config(inst, ",".join(profiles))
            self.assertEqual(set(services), set(everything))
        else:
            everything = active
        components = self.futureagi(everything)
        self.assertTrue(components)
        for name, component in components.items():
            self.assertEqual(services[name]["variable"], COMPONENT_VARIABLES[component], name)
            if not services[name]["active"]:
                self.assertIn(PROBE_ENV[COMPONENT_VARIABLES[component]], services[name]["image"])
        for name in set(services) - set(components):
            self.assertIsNone(services[name]["variable"], name)
        return services, components

    def test_standalone(self):
        def check(inst, commands, version):
            services, components = self.check(inst, commands)
            self.assertEqual(sorted(components), ["app", "code-executor", "serving"])
            self.assertEqual([services[name]["active"] for name in ("app", "code-executor", "serving")],
                             [True, False, False])
            self.assertEqual([entry["service"] for entry in inst.futureagi_services()], ["app"])
        self.each_release("standalone", self.checkout("standalone"), check)

    def test_distributed(self):
        def check(inst, commands, version):
            self.assertEqual(inst.mode, "distributed")
            services, components = self.check(inst, commands)
            self.assertTrue(services["backend"]["active"])
            self.assertEqual(services["worker-default"]["variable"], "FUTURE_AGI_VERSION")
            self.assertFalse(services["worker-default"]["active"])
            self.assertNotIn("worker-default", inst.config()["services"])
        self.each_release("distributed", self.checkout(
            "distributed", env_lines=["COMPOSE_FILE=docker-compose.distributed.yml"]), check)

    def test_distributed_with_a_profile_enabled(self):
        def check(inst, commands, version):
            services, _ = self.check(inst, commands)
            self.assertTrue(services["worker-default"]["active"])
            self.assertFalse(services["temporal-ui"]["active"])
        self.each_release("distributed+workers", self.checkout(
            "workers", env_lines=["COMPOSE_FILE=docker-compose.distributed.yml", "COMPOSE_PROFILES=workers"]), check)

    def test_production(self):
        def check(inst, commands, version):
            services, components = self.check(inst, commands)
            self.assertEqual({services[name]["image"].rsplit(":", 1)[1] for name in components
                              if services[name]["active"]}, {"v1.0.0"})
            self.assertFalse(services["worker-default"]["active"])
        self.each_release("production", self.checkout("production", production=True), check, production=True)

    def test_a_profile_that_cannot_be_enabled(self):
        # An override adds a profile-only service whose env_file is missing: only enabling it fails, except on
        # v2.24.7, whose `config --format json` checks no env_file, so its every-profile probe resolves `extra` too.
        override = ("services:\n  extra:\n    image: futureagi/frontend:${FRONTEND_VERSION:-latest}\n"
                    "    profiles: [\"extra\"]\n    env_file: ./missing.env\n")

        def check(inst, commands, version):
            if version == (2, 24, 7):
                services = inst.service_model()["services"]
                self.assertTrue(inst.service_model()["complete"])
                self.assertEqual({name for name, entry in services.items() if entry["active"]},
                                 set(self.plain(inst, "--services").split()))
                self.assertEqual(services["extra"], {"image": "futureagi/frontend:bin-update-probe-FRONTEND_VERSION",
                                                     "variable": "FRONTEND_VERSION", "active": False,
                                                     "buildable": False})
            else:
                services, _ = self.check(inst, commands, complete=False)
                self.assertNotIn("extra", services)
            self.assertNotIn("extra", inst.config()["services"])
            self.assertEqual(services["app"]["variable"], "FUTURE_AGI_VERSION")
        self.each_release("override", self.checkout("override", override=override), check)



class IntegratorRound4CliTests(unittest.TestCase):
    """F2: the refusal to build the installation names a command that works pasted where bin/update was run."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        base = Path(temporary.name).resolve()
        self.root = base / "check out"
        (self.root / "deploy").mkdir(parents=True)
        (self.root / PRODUCTION_ENV).write_text("SECRET_KEY=x\n")
        self.elsewhere = base / "elsewhere"
        self.elsewhere.mkdir()
        env_patch = mock.patch.dict(os.environ, clean_environ(), clear=True)
        env_patch.start()
        self.addCleanup(env_patch.stop)

    def main(self, *argv):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = cli.main(["--root", str(self.root), *argv])
        return code, stderr.getvalue()

    def test_the_production_hint_starts_in_the_checkout_from_another_directory(self):
        code, err = self.main("--cwd", str(self.elsewhere), "status")
        self.assertEqual(code, 2, err)
        # Round 5 (T17): with a bin/update command, so that it works pasted.
        self.assertTrue(err.rstrip().endswith(
            "this looks like a deploy/setup.sh production install. Add --production to the command, as in "
            f"`cd {shlex.quote(str(self.root))} && ./bin/update --production check`"), err)

    def test_it_is_the_short_form_in_the_checkout(self):
        for argv in (("--cwd", str(self.root)), ()):
            with self.subTest(argv=argv):
                code, err = self.main(*argv, "status")
                self.assertEqual(code, 2, err)
                # Round 5 (T17): with a bin/update command, so that it works pasted.
                self.assertTrue(err.rstrip().endswith(
                    "production install. Add --production to the command, as in `./bin/update --production check`"), err)



class IntegratorRound4ServiceModelEdgeTests(TempRootTest):
    """F1 and F2 edges: what Installation does when Compose or the filesystem answers oddly."""

    compose = InstallRound4ServiceModelTests.compose
    STANDALONE = InstallRound4ServiceModelTests.STANDALONE

    def test_an_all_profiles_probe_that_is_not_a_model_falls_back_to_the_installs_profiles(self):
        active = InstallRound4ComposeFiles(self.root, self.STANDALONE)
        for answer in (completed(0, "not json"), completed(0, "[]"), completed(0, '{"services": []}')):
            with self.subTest(answer=answer.stdout):

                def probe(kwargs, answer=answer):
                    # The first probe enables every profile; the retry leaves COMPOSE_PROFILES to the install.
                    if "COMPOSE_PROFILES" in kwargs["env"]:
                        return answer
                    return active.handle(["docker", "compose", "config", "--format", "json"], kwargs)

                fake = self.compose(probe=probe)
                model = self.install("FUTURE_AGI_VERSION=v1.0.0\n", runner=fake.runner).service_model()
                self.assertFalse(model["complete"])
                self.assertEqual(sorted(model["services"]), ["app", "postgres"])
                self.assertEqual(model["services"]["app"]["variable"], "FUTURE_AGI_VERSION")
                self.assertEqual(fake.calls(), ["active", "config --services", "profiles", "probe:ml,sandbox", "probe"])

    def test_an_active_service_compose_gives_no_definition_for_is_still_active(self):
        fake = self.compose(active=completed(0, json.dumps({"name": "futureagi", "services": {"app": None}})))
        model = self.install("", runner=fake.runner).service_model()
        self.assertEqual(model["services"]["app"], {"image": "", "variable": "FUTURE_AGI_VERSION", "active": True,
                                                    "buildable": False})

    def test_a_caller_directory_that_cannot_be_resolved_is_not_the_checkout(self):
        inst = self.install("", caller_cwd=self.root)
        with mock.patch.object(install.Path, "resolve", side_effect=OSError("too many levels of symbolic links")):
            self.assertEqual(inst.cd_prefix(), f"cd {shlex.quote(str(inst.root))} && ")


# ------------------------------------------------------------ round 5 --

class CliRound5SettingsTests(unittest.TestCase):
    """G3 (T28, T29): offline verification without a public key stops only the commands that verify a signature.

    status and rollback verify none, so __main__ asks load_settings to check
    the signature settings (verify=True) for check and apply only: an
    operator with FUTURE_AGI_UPDATE_COSIGN_OFFLINE=true and no public key
    can still see the status and roll back.
    """

    setUp = MainTests.setUp
    patch = MainTests.patch
    main = MainTests.main

    @staticmethod
    def refusal(source):
        return (f"bin/update: FUTURE_AGI_UPDATE_COSIGN_OFFLINE is true (from {source}), but "
                "FUTURE_AGI_UPDATE_COSIGN_PUBLIC_KEY is not set")

    def test_only_check_and_apply_have_load_settings_check_the_signature_settings(self):
        real = cli.load_settings
        load = self.patch("load_settings", side_effect=lambda *args, **kwargs: real(*args, **kwargs))
        for argv, verify in ((["check"], True), (["apply"], True), (["status"], False), (["rollback"], False),
                             (["rollback", "--yes"], False)):
            with self.subTest(argv=argv):
                load.reset_mock()
                code, _, err = self.main(*argv)
                self.assertEqual(code, 0, err)
                self.assertEqual(load.call_count, 1)
                self.assertIs(load.call_args[1].get("verify"), verify)

    def offline(self, where):
        """FUTURE_AGI_UPDATE_COSIGN_OFFLINE=true without a public key, in the environment or in update.conf."""

        if where == "environment":
            patcher = mock.patch.dict(os.environ, {"FUTURE_AGI_UPDATE_COSIGN_OFFLINE": "true"})
            patcher.start()
            self.addCleanup(patcher.stop)
        else:
            (self.root / "update.conf").write_text("FUTURE_AGI_UPDATE_COSIGN_OFFLINE=true\n")

    def test_offline_verification_without_a_public_key_stops_check_and_apply_only(self):
        for where in ("environment", "update.conf"):
            with self.subTest(where=where):
                self.offline(where)
                # check and apply refuse it before fetching anything, as before.
                for command in ("check", "apply"):
                    code, out, err = self.main(command)
                    self.assertEqual((code, out), (2, ""), command)
                    self.assertTrue(err.startswith(self.refusal(where)), err)
                    self.assertEqual(self.fetch_calls, [])
                    self.Updater.return_value.apply.assert_not_called()
                # status and rollback verify no signature: they run.
                code, out, err = self.main("status")
                self.assertEqual((code, err), (0, ""))
                self.assertIn("Last run      none", out)
                self.Updater.return_value.rollback.reset_mock()
                code, _, err = self.main("rollback", "--yes")
                self.assertEqual((code, err), (0, ""))
                self.Updater.return_value.rollback.assert_called_once_with(None, yes=True)
                os.environ.pop("FUTURE_AGI_UPDATE_COSIGN_OFFLINE", None)
                (self.root / "update.conf").unlink(missing_ok=True)

    def test_other_invalid_settings_still_stop_status_and_rollback(self):
        # Only the signature settings are left to the commands that verify one.
        self.offline("update.conf")
        with (self.root / "update.conf").open("a") as conf:
            conf.write("FUTURE_AGI_UPDATE_POLICY=huge\n")
        for command in ("status", "rollback"):
            with self.subTest(command=command, policy="huge"):
                code, _, err = self.main(command)
                self.assertEqual(code, 2, err)
                self.assertIn("FUTURE_AGI_UPDATE_POLICY must be one of patch, minor, major, not 'huge'", err)


class CliRound5GrammarTests(unittest.TestCase):
    """T39, T41: status and the interrupt message name a journal's run with its article (an apply, a rollback)."""

    setUp = MainTests.setUp
    patch = MainTests.patch
    main = MainTests.main
    journal = CliMainAdditions.journal
    lock_probe = CliRound3MainTests.lock_probe
    cd = CliRound4MainTests.cd

    KINDS = (("apply", "an apply"), ("rollback", "a rollback"), (None, "a run"))

    def interrupted(self, out):
        lines = [line for line in out.splitlines() if line.startswith("Interrupted")]
        self.assertEqual(len(lines), 1, out)
        return lines[0]

    def test_status_names_this_installs_interrupted_run_with_its_article(self):
        for kind, named in self.KINDS:
            with self.subTest(kind=kind):
                self.journal(**dict(CLI_IDENTITY, kind=kind, stage="rollout"))
                code, out, err = self.main("status")
                if kind is None:
                    self.assertEqual(code, 2, err)
                    self.assertIn('invalid or unknown', err)
                    self.assertNotIn('Interrupted', out)
                    continue
                self.assertEqual(code, 0, err)
                self.assertEqual(self.interrupted(out), f"Interrupted   {named} to v1.2.0 stopped at stage rollout; the "
                                                        "next apply or rollback recovers it")

    def test_status_names_another_installs_interrupted_run_with_its_article(self):
        for kind, named in self.KINDS:
            with self.subTest(kind=kind):
                self.journal(env_file="deploy/.env.production", production=True, kind=kind, stage="env_written")
                code, out, err = self.main("status")
                self.assertEqual(code, 2, err)
                if kind is None:
                    self.assertIn('invalid or unknown', err)
                    self.assertNotIn('Interrupted', out)
                    continue
                self.assertTrue(self.interrupted(out).startswith(
                    f"Interrupted   {named} to v1.2.0 for deploy/.env.production stopped at stage env_written; this "
                    "install cannot be updated until it is recovered: "), out)

    def test_an_interrupt_names_another_installs_run_with_its_article(self):
        self.lock_probe(lambda state: None)
        for kind, named in self.KINDS:
            with self.subTest(kind=kind):

                def stop(*args, kind=kind, **kwargs):
                    self.journal(env_file="deploy/.env.production", production=True, kind=kind, stage="rollout")
                    raise KeyboardInterrupt()
                self.Updater.return_value.apply.side_effect = stop
                code, _, err = self.main("apply")
                self.assertEqual(code, 130)
                self.assertTrue(err.startswith(f"bin/update: interrupted; {named} for deploy/.env.production stopped "
                                               "earlier and must be recovered before apply or rollback can run for "
                                               f"this install: run `{self.cd()}./bin/update --production rollback` "),
                                err)
                StateDir(self.root).clear_journal()


class CliRound5ParserTests(unittest.TestCase):
    """--channel's help says what releases publish: the stable channel only; beta needs a beta manifest."""

    def help_text(self, *argv):
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout), self.assertRaises(SystemExit) as caught:
            cli.build_parser().parse_args([*argv, "--help"])
        self.assertEqual(caught.exception.code, 0)
        return " ".join(stdout.getvalue().split())

    def test_the_channel_help_says_releases_publish_stable_manifests_only(self):
        for command in ("check", "apply"):
            with self.subTest(command=command):
                self.assertIn("--channel {stable,beta} release channel to follow (default: FUTURE_AGI_UPDATE_CHANNEL "
                              "or stable). Releases publish stable manifests only: beta needs --manifest naming a "
                              "beta manifest", self.help_text(command))
        for command in ("rollback", "status"):
            with self.subTest(command=command):
                self.assertNotIn("--channel", self.help_text(command))


class CliRound5FakeEngineTests(unittest.TestCase):
    """The FakeEngine of the full-stack tests answers Compose as round 5's Installation asks it (G1).

    `config --services` lists the active services, `config --profiles` every
    profile, COMPOSE_PROFILES (the environment's, else the env file's) and
    the probe values decide what `config` resolves, and `config
    --no-interpolate` fails as on every Compose release before v2.40.3.
    """

    WORKER = "futureagi/future-agi:${FUTURE_AGI_VERSION}"

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.env_file = Path(temporary.name) / "prod.env"
        self.env_file.write_text("FUTURE_AGI_VERSION=v1.0.0\nFRONTEND_VERSION=v1.0.0\n")
        self.engine = FakeEngine()
        self.engine.services["worker"] = self.WORKER
        self.engine.profiles["worker"] = ["workers", "full"]
        self.engine.services["temporal-ui"] = "temporalio/ui:2.31.2"
        self.engine.profiles["temporal-ui"] = ["full"]

    def compose(self, *args, env=None):
        command = ["docker", "compose", "--env-file", str(self.env_file), "-f", DISTRIBUTED_FILE, "-f",
                   PRODUCTION_OVERLAY, *args]
        return self.engine(command, cwd="/checkout", env=dict(env or {}), check=False, text=True,
                           capture_output=True)

    def services(self, env=None):
        result = self.compose("config", "--services", env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.splitlines()

    def model(self, env=None):
        result = self.compose("config", "--format", "json", env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        model = json.loads(result.stdout)
        self.assertEqual(model["name"], FakeEngine.PROJECT)
        return model["services"]

    def test_config_services_lists_the_active_services_in_definition_order(self):
        active = ["backend", "frontend", "postgres-schema-bootstrap", "postgres"]
        self.assertEqual(self.services(), active)
        self.assertEqual(list(self.model()), active)
        self.assertEqual(self.model()["backend"], {"image": "futureagi/future-agi:v1.0.0"})

    def test_config_profiles_lists_every_profile(self):
        self.assertEqual(self.compose("config", "--profiles").stdout, "full\nworkers\n")
        self.assertEqual(FakeEngine()(["docker", "compose", "config", "--profiles"], env={}).stdout, "")

    def test_compose_profiles_enables_a_profile_from_the_environment_or_the_env_file(self):
        self.assertEqual(self.services({"COMPOSE_PROFILES": "workers"})[-1], "worker")
        self.assertEqual(self.services({"COMPOSE_PROFILES": "full"})[-2:], ["worker", "temporal-ui"])
        self.assertEqual(self.services({"COMPOSE_PROFILES": "*"})[-2:], ["worker", "temporal-ui"])
        self.assertIn("worker", self.model({"COMPOSE_PROFILES": "nothing,workers"}))
        with self.env_file.open("a") as env_file:
            env_file.write("COMPOSE_PROFILES=workers\n")
        self.assertEqual(self.services()[-1], "worker")
        # The environment's COMPOSE_PROFILES wins over the env file's, as in Compose.
        self.assertEqual(self.services({"COMPOSE_PROFILES": "full"})[-2:], ["worker", "temporal-ui"])
        self.assertNotIn("worker", self.services({"COMPOSE_PROFILES": "other"}))

    def test_the_probe_values_select_every_profiles_images(self):
        probe = dict(PROBE_ENV, COMPOSE_PROFILES="full,workers")
        model = self.model(probe)
        self.assertEqual(set(model), set(self.services(probe)))
        self.assertEqual((model["worker"]["profiles"], model["temporal-ui"]["profiles"]), (["workers", "full"], ["full"]))
        self.assertNotIn("profiles", model["backend"])
        self.assertEqual(model["worker"]["image"], "futureagi/future-agi:bin-update-probe-FUTURE_AGI_VERSION")
        self.assertEqual(model["frontend"]["image"], "futureagi/frontend:bin-update-probe-FRONTEND_VERSION")
        self.assertEqual(model["temporal-ui"]["image"], "temporalio/ui:2.31.2")
        # Without COMPOSE_PROFILES the probe resolves the active services only.
        self.assertNotIn("worker", self.model(PROBE_ENV))
        self.assertEqual(self.model({"FUTURE_AGI_VERSION": "v1.1.0"})["backend"]["image"],
                         "futureagi/future-agi:v1.1.0")

    def test_config_no_interpolate_fails_as_before_compose_v2_40_3(self):
        self.assertEqual(self.compose("version", "--short").stdout, "2.39.4\n")
        result = self.compose("config", "--no-interpolate", "--format", "json")
        self.assertEqual((result.returncode, result.stdout), (1, ""))
        self.assertIn("too many colons", result.stderr)
        self.assertEqual(len(self.engine.config_calls("--no-interpolate")), 1)
        self.engine.version = "2.40.3"
        self.assertEqual(self.compose("config", "--no-interpolate", "--format", "json").returncode, 0)
        self.assertEqual(self.compose("version").stdout, "Docker Compose version v2.40.3\n")

    def test_compose_v2_24_7s_json_lists_every_profile_but_its_services_list_does_not(self):
        self.engine.json_ignores_profiles = True
        self.assertIn("worker", self.model())
        self.assertNotIn("worker", self.services())

    def test_up_starts_the_active_services_only(self):
        for ref in ("futureagi/future-agi:v1.0.0", "futureagi/frontend:v1.0.0", "postgres:16"):
            self.engine.publish(ref, "v1.0.0")
            self.engine.pull(ref)
        self.assertEqual(self.compose("up", "-d", "--no-build").returncode, 0)
        self.assertEqual(sorted(self.engine.running()), ["backend", "frontend", "postgres", "postgres-schema-bootstrap"])
        self.assertEqual(self.compose("up", "-d", env={"COMPOSE_PROFILES": "workers"}).returncode, 0)
        self.assertEqual(self.engine.running()["worker"], "v1.0.0")
        self.assertEqual(len(self.engine.up_calls()), 2)


class CliRound5FullStackTests(unittest.TestCase):
    """FullStackProductionTests' stack with a profile Compose does not run (G1) and the signature settings only
    check and apply use (G3)."""

    OLD, NEW, NEWER = FullStackProductionTests.OLD, FullStackProductionTests.NEW, FullStackProductionTests.NEWER
    REPOS = FullStackProductionTests.REPOS
    manifest = FullStackProductionTests.manifest
    settings = FullStackProductionTests.settings
    paste = FullStackProductionTests.paste
    printed = FullStackProductionTests.printed
    versions = FullStackProductionTests.versions
    receipts = FullStackProductionTests.receipts
    receipt_paths = FullStackProductionTests.receipt_paths
    backups = FullStackProductionTests.backups
    running = FullStackProductionTests.running
    stage = FullStackProductionTests.stage

    def setUp(self):
        FullStackProductionTests.setUp(self)
        # Services of profiles this install does not enable (no container runs them): a worker, and the serving
        # component, whose version key the env file does not set.
        self.engine.services["worker-default"] = "futureagi/future-agi:${FUTURE_AGI_VERSION}"
        self.engine.profiles["worker-default"] = ["workers"]
        self.engine.services["serving"] = "futureagi/serving:${SERVING_VERSION:-latest}"
        self.engine.profiles["serving"] = ["ml"]

    def update_in_two_steps(self):
        """check, then the two steps, each as printed; returns what the first check printed."""

        code, check, err = self.paste("./bin/update --production check")
        self.assertEqual(code, 0, err)
        first = self.printed(check, r"update available: run (.+?) \(the first of two steps")
        code, out, err = self.paste(first)
        self.assertEqual(code, 0, err)
        code, out, err = self.paste(self.printed(err, r"Then run `([^`]+)` to restart the stack") + ' --acknowledge-stale-stage')
        self.assertEqual(code, 0, err)
        code, out, err = self.paste("./bin/update --production check")
        self.assertEqual(code, 0, err)
        self.assertIn("up to date, nothing to do", out)
        return check

    def test_a_disabled_profiles_service_neither_blocks_nor_starts(self):
        check = self.update_in_two_steps()
        self.assertNotIn("worker-default", check)
        self.assertNotIn("serving", check)
        self.assertEqual(self.engine.running(), self.running(self.NEW))
        self.assertEqual(self.engine.config_calls("--no-interpolate"), [])
        code, out, err = self.paste("./bin/update --production rollback --yes")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.engine.running(), self.running(self.OLD))

    def test_an_enabled_profiles_service_is_updated_and_rolled_back_with_the_rest(self):
        with self.env_file.open("a") as env_file:
            env_file.write("COMPOSE_PROFILES=workers\n")
        self.engine.start("worker-default", f"futureagi/future-agi:{self.OLD}")
        self.update_in_two_steps()
        self.assertEqual(self.engine.running(), dict(self.running(self.NEW), **{"worker-default": self.NEW}))
        code, out, err = self.paste("./bin/update --production rollback --yes")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.engine.running(), dict(self.running(self.OLD), **{"worker-default": self.OLD}))

    def test_compose_v2_24_7s_json_does_not_make_a_disabled_profiles_service_active(self):
        # Compose v2.24.7's `config --format json` lists every profile's services; `config --services` lists the
        # ones it runs, and that is the active set (G1, T1, T49).
        self.engine.json_ignores_profiles = True
        self.engine.version = "2.24.7"
        check = self.update_in_two_steps()
        self.assertNotIn("worker-default", check)
        self.assertNotIn("serving", check)
        self.assertEqual(self.engine.running(), self.running(self.NEW))
        self.assertTrue(self.engine.config_calls("--services"))

    def test_status_and_the_printed_rollback_run_with_offline_verification_and_no_public_key(self):
        err = self.stage()
        drop = self.printed(err, r"To drop the staged update instead, run `([^`]+)`")
        with (self.root / "update.conf").open("a") as conf:
            conf.write("FUTURE_AGI_UPDATE_COSIGN_OFFLINE=true\n")
        code, out, err = self.paste("./bin/update --production check")
        self.assertEqual((code, out), (2, ""))
        self.assertTrue(err.startswith("bin/update: FUTURE_AGI_UPDATE_COSIGN_OFFLINE is true (from update.conf), but "
                                       "FUTURE_AGI_UPDATE_COSIGN_PUBLIC_KEY is not set"), err)
        code, out, err = self.paste("./bin/update --production status")
        self.assertEqual((code, err), (0, ""))
        self.assertIn(f"Last run      apply to {self.NEW}: staged", out)
        code, out, err = self.paste(drop)
        self.assertEqual(code, 0, err)
        self.assertEqual(self.versions(), (self.OLD, self.OLD))
        self.assertEqual(self.receipts()[-1]["status"], "rolled_back")
        self.assertEqual(self.engine.running(), self.running(self.OLD))



# ------------------------------------------------------------ round 5 --

class InstallRound5ComposeFiles(InstallRound4ComposeFiles):
    """InstallRound4ComposeFiles that answers `config --services` too, and lists each service's `profiles`.

    Every release's `config --services` names the enabled services. With
    v2_24_7=True, `config --format json` lists every profile's services, as
    Compose v2.24.7 does (it applies no profiles there). `docker` answers the
    calls that are not Compose's (docker_handler); "listed" in `answers`
    replaces the answer to `config --services`.
    """

    def __init__(self, root, services, *, v2_24_7=False, docker=None, **answers):
        super().__init__(root, services, **answers)
        self.v2_24_7 = v2_24_7
        self.docker = docker or docker_handler()

    def handle(self, args, kwargs):
        program, rest = install.split_command(args)
        if program != ["docker", "compose"]:
            return self.docker(args, kwargs)
        values = self.values(args, kwargs.get("env") or {})
        enabled = {name for name in (values.get("COMPOSE_PROFILES") or "").split(",") if name}

        def on(spec):
            return not spec.get("profiles") or bool(enabled & set(spec["profiles"]))
        if rest == ["config", "--services"]:
            if "listed" in self.answers:
                return self.answers["listed"]
            return completed(0, "".join(f"{name}\n" for name, spec in self.services.items() if on(spec)))
        kind = "probe" if is_probe(kwargs) else "active"
        if rest != ["config", "--format", "json"] or kind in self.answers:
            return super().handle(args, kwargs)
        model = {}
        for name, spec in self.services.items():
            entry = {}
            if "image" in spec:
                entry["image"], error = self.interpolate(spec["image"], values)
                if error:
                    return completed(1, "", f"error while interpolating services.{name}.image: {error}\n")
            if not on(spec) and not self.v2_24_7:
                continue
            for key in ("profiles", "build"):
                if key in spec:
                    entry[key] = spec[key]
            model[name] = entry
        return completed(0, json.dumps({"name": "futureagi", "services": model}))


ROUND5_STANDALONE = {
    "app": {"image": "futureagi/standalone:${FUTURE_AGI_VERSION:-latest}"},
    "postgres": {"image": "postgres:16"},
    "clickhouse": {"image": "clickhouse/clickhouse-server:24.8"},
    "serving": {"image": "futureagi/serving:${SERVING_VERSION:-latest}", "profiles": ["ml"]},
    "code-executor": {"image": "futureagi/code-executor:${CODE_EXECUTOR_VERSION:-latest}", "profiles": ["sandbox"]},
}
ROUND5_DISTRIBUTED = {
    "backend": {"image": "futureagi/future-agi:${FUTURE_AGI_VERSION:-latest}"},
    "frontend": {"image": "futureagi/frontend:${FRONTEND_VERSION:-latest}"},
    "worker-default": {"image": "futureagi/future-agi:${FUTURE_AGI_VERSION:-latest}", "profiles": ["workers", "all"]},
    "temporal-ui": {"image": "temporalio/ui:2.43.3", "profiles": ["observability", "all"]},
    "redis": {"image": "redis:7-alpine"},
}
V2_24_1_HINT = "Docker Compose v2.24.1 rejects valid compose files (fixed in v2.24.2): upgrade Docker Compose"
V2_25_0_REFUSAL = ("Docker Compose v2.25.0 cannot print its configuration as JSON, which bin/update needs (fixed in "
                   "v2.26.0). Upgrade Docker Compose and run bin/update again")


class InstallRound5ComposeVersionTests(TempRootTest):
    """G1 (T2, T48, T63): the Compose version is recorded; v2.25.0 is refused, v2.24.1's config failures explained."""

    @staticmethod
    def which(**found):
        return lambda name: found.get(name.replace("-", "_"))

    def test_the_version_compose_prints_is_parsed(self):
        for text, version in (("2.24.7\n", (2, 24, 7)), ("v2.29.1", (2, 29, 1)), ("2.29.1-desktop.1", (2, 29, 1)),
                              ("5.1.4", (5, 1, 4)), ("20.1", (20, 1, 0)), ("2.0.0-rc.1", (2, 0, 0)), ("", None),
                              ("dev", None), ("Docker Compose version v2.24.7", None)):
            with self.subTest(text=text):
                self.assertEqual(install.parse_compose_version(text), version)

    def test_resolving_compose_records_its_version(self):
        plugin = resolve_compose_command(Runner(lambda args, kwargs: completed(0, "2.24.7\n")),
                                         self.which(docker="/usr/bin/docker"))
        self.assertEqual((plugin, plugin.version), (["docker", "compose"], (2, 24, 7)))

        def handler(args, kwargs):
            return completed(1, "", "unknown command") if args[:1] == ["docker"] else completed(0, "v2.40.3\n")
        standalone = resolve_compose_command(Runner(handler), self.which(docker="/usr/bin/docker",
                                                                         docker_compose="/opt/docker-compose"))
        self.assertEqual((standalone, standalone.version), (["/opt/docker-compose"], (2, 40, 3)))
        unknown = resolve_compose_command(Runner(lambda args, kwargs: completed(0, "dev\n")),
                                          self.which(docker="/usr/bin/docker"))
        self.assertIsNone(unknown.version)

    def test_a_standalone_compose_that_reports_a_commit_is_v2_of_unknown_version(self):
        # The v2.32.3 release binaries print `083f676` for `version --short`: that was refused as "too old".
        runner = Runner(lambda args, kwargs: completed(0, "083f676\n"))
        found = resolve_compose_command(runner, self.which(docker_compose="/opt/docker-compose"))
        self.assertEqual((found, found.version), (["/opt/docker-compose"], None))
        for printed in ("1.29.2", "2", "083f67", "not-a-commit"):
            with self.subTest(printed=printed):
                with self.assertRaises(InstallError) as caught:
                    resolve_compose_command(Runner(lambda args, kwargs, printed=printed: completed(0, printed)),
                                            self.which(docker_compose="/opt/docker-compose"))
                self.assertEqual(str(caught.exception),
                                 f"docker-compose {printed} is too old: bin/update needs Docker Compose v2")

    def test_the_installation_keeps_the_version(self):
        real = install.resolve_compose_command
        runner = Runner(lambda args, kwargs: completed(0, "2.24.7\n"))
        with mock.patch.object(install, "resolve_compose_command",
                               lambda run: real(run, self.which(docker="/usr/bin/docker"))):
            self.assertEqual(self.install("", runner=runner, compose=None).compose_version, (2, 24, 7))
        self.assertEqual(runner.commands(), [["docker", "compose", "version", "--short"]])
        self.assertEqual(self.install("", compose_version=(2, 30, 0)).compose_version, (2, 30, 0))
        self.assertEqual(self.install("", compose=install.ComposeCommand(["docker-compose"], (5, 1, 4))).compose_version,
                         (5, 1, 4))
        self.assertIsNone(self.install("").compose_version)

    def test_v2_25_0_is_refused_before_any_other_compose_call(self):
        real = install.resolve_compose_command
        for mode, env_text in (("standalone", ""), ("distributed", "COMPOSE_FILE=docker-compose.distributed.yml\n"),
                               ("production", None)):
            with self.subTest(mode=mode):
                runner = Runner(lambda args, kwargs: completed(0, "2.25.0\n"))
                if env_text is None:
                    self.production_files()
                with mock.patch.object(install, "resolve_compose_command",
                                       lambda run: real(run, self.which(docker="/usr/bin/docker"))):
                    with self.assertRaises(InstallError) as caught:
                        self.install(env_text, runner=runner, compose=None, production=env_text is None)
                self.assertEqual(str(caught.exception), V2_25_0_REFUSAL)
                self.assertEqual(runner.commands(), [["docker", "compose", "version", "--short"]])
        runner = Runner()
        with self.assertRaises(InstallError) as caught:
            self.install("", runner=runner, compose_version=(2, 25, 0))
        self.assertEqual((str(caught.exception), runner.calls), (V2_25_0_REFUSAL, []))
        for version in ((2, 24, 7), (2, 25, 1), (2, 26, 0)):
            self.assertEqual(self.install("", compose_version=version).compose_version, version)

    def failing(self, version, stderr="validating deploy/docker-compose.production.yml: services.worker-tasks-s."
                                      "environment array items[0,131] must be unique\n"):
        runner = Runner(lambda args, kwargs: completed(15, "", stderr))
        return self.install("", runner=runner, compose_version=version)

    def test_a_failing_config_call_on_v2_24_1_says_to_upgrade_compose(self):
        problem = ("`docker compose config` failed with exit code 15: validating deploy/docker-compose.production.yml: "
                   "services.worker-tasks-s.environment array items[0,131] must be unique")
        for call in (lambda inst: inst.config(), lambda inst: inst.compose_run("config", "--quiet"),
                     lambda inst: inst.service_model()):
            inst = self.failing((2, 24, 1))
            with self.assertRaises(InstallError) as caught:
                call(inst)
            self.assertEqual(str(caught.exception), f"{problem}. {V2_24_1_HINT}")
            with self.assertRaises(InstallError) as caught:
                call(self.failing((2, 24, 2)))
            self.assertEqual(str(caught.exception), problem)
        # A sentence that already ends is not given another full stop.
        with self.assertRaises(InstallError) as caught:
            self.failing((2, 24, 1), "invalid compose file.\n").config()
        self.assertEqual(str(caught.exception), "`docker compose config` failed with exit code 15: invalid compose "
                                                f"file. {V2_24_1_HINT}")
        # Only a config call: another command's failure is not Compose rejecting the files.
        with self.assertRaises(InstallError) as caught:
            inst = self.failing((2, 24, 1), "pull access denied\n")
            inst.run(inst.compose_args("pull", "app"))
        self.assertEqual(str(caught.exception), "`docker compose pull app` failed with exit code 15: pull access denied")

    def test_the_probe_failing_on_v2_24_1_names_the_release_not_the_placeholders(self):
        fake = InstallRound5ComposeFiles(self.root, ROUND5_STANDALONE,
                                         probe=completed(15, "", "services.app.environment array items must be unique\n"))
        inst = self.install("", runner=fake.runner, compose_version=(2, 24, 1))
        with self.assertRaises(InstallError) as caught:
            inst.service_model()
        self.assertEqual(str(caught.exception), "`docker compose config` failed with exit code 15: services.app."
                                                f"environment array items must be unique. {V2_24_1_HINT}")


class InstallRound5ActiveServicesTests(TempRootTest):
    """G1 (T1, T49, T63): the active services are the ones `config --services` names, on every Compose release."""

    def compose(self, services=None, **kwargs):
        return InstallRound5ComposeFiles(self.root, ROUND5_STANDALONE if services is None else services, **kwargs)

    def test_v2_24_7_lists_only_the_active_services(self):
        fake = self.compose(v2_24_7=True)
        inst = self.install("FUTURE_AGI_VERSION=v1.0.0\n", runner=fake.runner)
        self.assertEqual(sorted(inst.config()["services"]), ["app", "clickhouse", "postgres"])
        self.assertEqual([entry["service"] for entry in inst.futureagi_services()], ["app"])
        model = inst.service_model()
        self.assertTrue(model["complete"])
        self.assertEqual(model["services"]["serving"], {
            "image": "futureagi/serving:bin-update-probe-SERVING_VERSION", "variable": "SERVING_VERSION",
            "active": False, "buildable": False})
        self.assertEqual({name for name, entry in model["services"].items() if entry["active"]},
                         {"app", "clickhouse", "postgres"})
        self.assertEqual(fake.calls(), ["active", "config --services", "profiles", "probe:ml,sandbox"])
        # Another release lists only the active services, none of a profile: the JSON is the whole answer.
        other = self.compose()
        inst = self.install("FUTURE_AGI_VERSION=v1.0.0\n", runner=other.runner)
        self.assertEqual(sorted(inst.config()["services"]), ["app", "clickhouse", "postgres"])
        self.assertEqual(other.calls(), ["active", "config --services"])

    def test_an_enabled_profiles_services_are_active_on_every_release(self):
        env_text = ("COMPOSE_FILE=docker-compose.distributed.yml\nCOMPOSE_PROFILES=workers\n"
                    "FUTURE_AGI_VERSION=v1.0.0\n")
        for v2_24_7 in (False, True):
            with self.subTest(v2_24_7=v2_24_7):
                fake = self.compose(ROUND5_DISTRIBUTED, v2_24_7=v2_24_7)
                inst = self.install(env_text, runner=fake.runner)
                services = inst.config()["services"]
                self.assertEqual(sorted(services), ["backend", "frontend", "redis", "worker-default"])
                self.assertEqual(services["worker-default"]["image"], "futureagi/future-agi:v1.0.0")
                self.assertEqual(fake.calls(), ["active", "config --services"])

    def test_the_overlay_and_a_refresh_ask_again(self):
        fake = self.compose(v2_24_7=True)
        inst = self.install("FUTURE_AGI_VERSION=v1.0.0\n", runner=fake.runner)
        self.assertNotIn("serving", inst.config()["services"])
        inst.overlay_env({"COMPOSE_PROFILES": "ml"})
        self.assertEqual(sorted(inst.config()["services"]), ["app", "clickhouse", "postgres", "serving"])
        self.write(".env", "FUTURE_AGI_VERSION=v1.0.0\nCOMPOSE_PROFILES=sandbox\n")
        inst.overlay_env({"COMPOSE_PROFILES": None})
        self.assertNotIn("serving", inst.config()["services"])
        self.assertEqual(sorted(inst.config(refresh=True)["services"]), ["app", "clickhouse", "postgres"])
        self.assertEqual(fake.calls(), ["active", "config --services"] * 4)
        # The services call runs with the same environment as the JSON one.
        self.assertEqual(fake.runner.calls[2][1]["env"], fake.runner.calls[3][1]["env"])

    def test_a_service_list_naming_a_service_the_json_does_not_define_is_refused(self):
        fake = self.compose(v2_24_7=True, listed=completed(0, "app\npostgres\nghost\nwraith\n"))
        inst = self.install("", runner=fake.runner)
        with self.assertRaises(InstallError) as caught:
            inst.config()
        self.assertEqual(str(caught.exception), (
            "`docker compose config --services` lists ghost, wraith, which its `config --format json` does not "
            "define, so bin/update cannot tell which services Compose runs. Upgrade Docker Compose and run "
            "bin/update again"))

    def test_a_failing_service_list_is_an_error(self):
        for version, suffix in (((2, 24, 1), ". " + V2_24_1_HINT), ((2, 24, 7), "")):
            with self.subTest(version=version):
                fake = self.compose(v2_24_7=True, listed=completed(1, "", "no such service\n"))
                inst = self.install("", runner=fake.runner, compose_version=version)
                with self.assertRaises(InstallError) as caught:
                    inst.config()
                self.assertEqual(str(caught.exception),
                                 f"`docker compose config` failed with exit code 1: no such service{suffix}")

    def test_production_asks_with_the_env_file_and_both_compose_files(self):
        self.production_files("FUTURE_AGI_VERSION=v1.0.0\n")
        fake = self.compose(ROUND5_DISTRIBUTED, v2_24_7=True)
        inst = Installation(self.root, production=True, environ={}, runner=fake.runner, compose=COMPOSE)
        self.assertEqual(sorted(inst.config()["services"]), ["backend", "frontend", "redis"])
        self.assertEqual(fake.runner.commands()[1], [
            "docker", "compose", "--env-file", str(self.root / PRODUCTION_ENV), "-f", str(self.root / DISTRIBUTED_FILE),
            "-f", str(self.root / PRODUCTION_OVERLAY), "config", "--services"])

    def test_v2_24_7_a_disabled_profiles_running_container_is_a_stray(self):
        # T49 (b): worker-default (workers profile, now off) still runs: on v2.24.7 it was in config(), so no stray.
        from deploy.update.plan import classify_strays

        self.write(DISTRIBUTED_FILE, "services: {}\n")
        containers = [container_record("backend", image="futureagi/future-agi:v1.0.0", image_id="sha256:new"),
                      container_record("worker-default", image="futureagi/future-agi:v0.9.0", image_id="sha256:old")]
        images = {"sha256:new": image_record("sha256:new", version="v1.0.0"),
                  "sha256:old": image_record("sha256:old", version="v0.9.0")}
        fake = self.compose(ROUND5_DISTRIBUTED, v2_24_7=True, docker=docker_handler(containers=containers, images=images))
        inst = self.install("COMPOSE_FILE=docker-compose.distributed.yml\nFUTURE_AGI_VERSION=v1.0.0\n",
                            runner=fake.runner)
        active = inst.config()["services"]
        self.assertNotIn("worker-default", active)
        strays = classify_strays(inst, inst.containers(), active)
        self.assertEqual([(entry["service"], entry["version"]) for entry in strays["inactive"]],
                         [("worker-default", "v0.9.0")])

    def test_v2_24_7_a_default_standalone_install_plans_only_its_active_components(self):
        # T1 (a): serving (ml) and code-executor (sandbox) are off and their keys unset; the plan must not ask for them.
        from deploy.update.manifest import hash_path
        from deploy.update.plan import build_plan
        from deploy.update.settings import load_settings

        self.write(STANDALONE_FILE, "services: {}\n")
        manifest = valid_manifest("v1.2.0")
        manifest["deployment"]["files"] = {STANDALONE_FILE: hash_path(self.root, STANDALONE_FILE)}
        containers = [container_record("app", image="futureagi/standalone:v1.0.0", image_id="sha256:app"),
                      container_record("postgres", image="postgres:16", image_id="sha256:pg")]
        images = {"sha256:app": image_record("sha256:app", version="v1.0.0"), "sha256:pg": image_record("sha256:pg")}
        fake = self.compose(v2_24_7=True, docker=docker_handler(containers=containers, images=images))
        inst = self.install("FUTURE_AGI_VERSION=v1.0.0\n", runner=fake.runner)
        plan = build_plan(manifest, Verification(True, method="public key", signer="cosign.pub", tool="cosign"), inst,
                          load_settings(self.root, {}, environ={}))
        self.assertEqual((plan["decision"], plan["reasons"]), ("update_available", []))
        self.assertEqual([component["component"] for component in plan["components"]], ["standalone"])
        self.assertEqual(plan["env_changes"], {"FUTURE_AGI_VERSION": "v1.2.0"})


class InstallRound5CheckStackTests(TempRootTest):
    """T53, T55 and T17: what check_stack and the installation's refusals tell the operator to run."""

    PRODUCTION_HINT = ("docker compose --env-file deploy/.env.production -f docker-compose.distributed.yml "
                       "-f deploy/docker-compose.production.yml")
    OVERLAY = [{"service": "backend", "command": []}, {"service": "frontend", "command": []},
               {"service": "postgres-schema-bootstrap", "command": ["manage.py", "migrate", "--check", "--noinput"]}]

    def inst(self, env_text="", *, production=False, **kwargs):
        runner = Runner(docker_handler())
        if production:
            self.production_files()
            return Installation(self.root, production=True, environ={}, runner=runner, compose=COMPOSE, **kwargs)
        return self.install(env_text, runner=runner, **kwargs)

    def stopped(self, start, install):
        return (f"no containers of Compose project 'futureagi' exist. The stack may be stopped (Compose's down removes "
                f"the containers and keeps the data), or Future AGI is not installed here yet: start it with `{start}`, "
                f"or {install}, then check again")

    @staticmethod
    def stopped_production(start):
        # Round 6 (U9): the start command, run by hand, ends its sentence and the shell rule follows it.
        return ("no containers of Compose project 'futureagi' exist. The stack may be stopped (Compose's down removes "
                "the containers and keeps the data), or Future AGI is not installed here yet: install it as "
                f"deploy/README.md describes (deploy/setup.sh), or start it with `{start}`. Run it from a shell that "
                "does not export any key of deploy/.env.production (Compose prefers your shell over the env file). "
                "Then check again")

    def test_no_containers_says_the_stack_may_be_stopped_and_how_to_start_it(self):
        for env_text in ("", "COMPOSE_FILE=docker-compose.distributed.yml\n"):
            with self.subTest(env=env_text):
                self.assertEqual(self.inst(env_text).check_stack([]),
                                 [self.stopped("docker compose up -d", "install it with `./bin/install`")])
        # deploy/setup.sh asks for --confirm-initialized again: production starts with its Compose command.
        self.assertEqual(self.inst(production=True).check_stack([]), [self.stopped_production(
            f"{self.PRODUCTION_HINT} up -d --no-build --wait")])

    def test_no_containers_commands_start_in_the_checkout_from_elsewhere(self):
        elsewhere = self.root / "else where"
        elsewhere.mkdir()
        cd = f"cd {shlex.quote(str(self.root))} && "
        self.assertEqual(self.inst(caller_cwd=elsewhere).check_stack([]), [self.stopped(
            f"{cd}docker compose up -d", f"install it with `{cd}./bin/install`")])
        self.assertEqual(self.inst(production=True, caller_cwd=elsewhere).check_stack([]), [self.stopped_production(
            f"{cd}{self.PRODUCTION_HINT} up -d --no-build --wait")])

    def pasted(self, command, cwd):
        """Run a printed command in `cwd` with fake docker and bin/install: (the directory it ran in, its words)."""

        bindir = self.root / "fake bin"
        record = "#!/bin/sh\npwd > \"$RECORD\"\nprintf '%s\\n' \"$0\" \"$@\" >> \"$RECORD\"\n"
        executable(bindir / "docker", record)
        executable(self.root / "bin" / "install", record)
        output = self.root / "record"
        result = subprocess.run(["/bin/sh", "-c", command], cwd=str(cwd), capture_output=True, text=True, timeout=60,
                                env={"PATH": f"{bindir}{os.pathsep}{os.defpath}", "RECORD": str(output)}, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = output.read_text(encoding="utf-8").splitlines()
        return Path(lines[0]).resolve(), [Path(lines[1]).name, *lines[2:]]

    def test_no_containers_commands_work_pasted_where_bin_update_ran(self):
        elsewhere = self.root / "elsewhere"
        elsewhere.mkdir()
        for production in (False, True):
            with self.subTest(production=production):
                inst = self.inst(production=production, caller_cwd=elsewhere)
                commands = re.findall(r"`([^`]+)`", inst.check_stack([])[0])
                ran = [self.pasted(command, elsewhere) for command in commands]
                self.assertEqual({directory for directory, _ in ran}, {self.root})
                words = [found for _, found in ran]
                if production:
                    self.assertEqual(words, [["docker", "compose", "--env-file", PRODUCTION_ENV, "-f", DISTRIBUTED_FILE,
                                              "-f", PRODUCTION_OVERLAY, "up", "-d", "--no-build", "--wait"]])
                else:
                    self.assertEqual(words, [["docker", "compose", "up", "-d"], ["install"]])

    def test_a_production_checkout_run_without_production_names_it_first(self):
        # T55: the production overlay's check-only bootstrap jobs, with a .env next to deploy/.env.production.
        self.write(PRODUCTION_ENV, "FUTURE_AGI_VERSION=v1.0.0\n")
        for env_text in ("", "COMPOSE_FILE=docker-compose.distributed.yml\n"):
            with self.subTest(env=env_text):
                self.assertEqual(self.inst(env_text).check_stack(self.OVERLAY), [
                    "this install runs the production overlay (check-only bootstrap jobs). Use `./bin/update "
                    "--production check`, which uses deploy/.env.production and the overlay as deploy/setup.sh does"])

    def test_the_overlay_without_deploy_env_production_says_to_name_the_env_file(self):
        self.assertEqual(self.inst().check_stack(self.OVERLAY), [
            "this install runs the production overlay (check-only bootstrap jobs). Use `./bin/update --production "
            "check` with --env-file naming the env file deploy/setup.sh used (deploy/.env.production does not exist), "
            "which uses it and the overlay as deploy/setup.sh does"])

    def test_deploy_env_production_puts_production_first_in_every_remedy(self):
        self.write(PRODUCTION_ENV, "FUTURE_AGI_VERSION=v1.0.0\n")
        first = ("This checkout has deploy/.env.production: if Future AGI runs here as a deploy/setup.sh production "
                 "install, run `./bin/update --production check`. Otherwise ")
        self.assertEqual(self.inst().check_stack([{"service": "backend", "command": []}]), [
            "project 'futureagi' runs Distributed services (backend) but .env selects the Standalone stack. bin/update "
            f"never moves an install between stacks. {first}fix COMPOSE_FILE in .env (./bin/install records it)"])
        self.assertEqual(self.inst().check_stack([]), [
            f"no containers of Compose project 'futureagi' exist. {first}the stack may be stopped (Compose's down "
            "removes the containers and keeps the data), or Future AGI is not installed here yet: start it with "
            "`docker compose up -d`, or install it with `./bin/install`, then check again"])
        # A Standalone stack that runs as it should has nothing to remedy.
        self.assertEqual(self.inst().check_stack([{"service": "app", "command": []}]), [])
        # --production itself is not told to add --production.
        self.assertEqual(self.inst(production=True).check_stack([]), [self.stopped_production(
            f"{self.PRODUCTION_HINT} up -d --no-build --wait")])

    def bin_update_words(self, command):
        """The bin/update arguments of a printed command, checked to parse as bin/update parses them."""

        words = shlex.split(command)
        if words[0] == "cd":
            self.assertEqual((Path(words[1]).resolve(), words[2]), (self.root, "&&"))
            words = words[3:]
        self.assertEqual(words[0], "./bin/update")
        stderr = io.StringIO()
        try:
            with contextlib.redirect_stderr(stderr):
                parsed = cli.build_parser().parse_args(words[1:])
        except SystemExit as exc:
            self.fail(f"bin/update rejects `{command}` (exit {exc.code}): {stderr.getvalue().strip()}")
        self.assertTrue(getattr(parsed, "production", False), command)
        return words[1:]

    def test_printed_bin_update_commands_name_a_command_and_start_in_the_checkout(self):
        # T17: `./bin/update --production` alone stops at the usage error (a command is required).
        elsewhere = self.root / "else where"
        elsewhere.mkdir()
        self.write(PRODUCTION_ENV, "FUTURE_AGI_VERSION=v1.0.0\n")
        for caller in (None, elsewhere):
            with self.subTest(caller=caller):
                with self.assertRaises(InstallError) as caught:
                    Installation(self.root, environ={}, runner=Runner(), compose=COMPOSE, caller_cwd=caller)
                message = str(caught.exception)
                self.assertTrue(message.startswith(f"no .env in {self.root}, but deploy/.env.production exists: this "
                                                   "looks like a deploy/setup.sh production install. Add --production "
                                                   "to the command, as in `"), message)
                commands = re.findall(r"`([^`]+)`", message)
                self.assertEqual(len(commands), 1)
                self.assertEqual(commands[0].startswith("cd "), caller is not None)
                self.assertEqual(self.bin_update_words(commands[0]), ["--production", "check"])
                inst = self.inst("COMPOSE_FILE=docker-compose.distributed.yml\n", caller_cwd=caller)
                (self.root / ".env").unlink()  # the next subtest's refusal needs no .env
                for problem in (*inst.check_stack(self.OVERLAY), *inst.check_stack([])):
                    for command in re.findall(r"`([^`]+)`", problem):
                        if "./bin/update" in command:
                            self.assertEqual(self.bin_update_words(command), ["--production", "check"])



# ------------------------------------------------------- integrator, round 5 --

class IntegratorRound5CliTests(unittest.TestCase):
    """T59 on the CLI side: check and status read a journal its run already finished with (its receipt written) as
    the journal the next apply removes, not as an interrupted run to recover."""

    OLD, NEW, NEWER = FullStackProductionTests.OLD, FullStackProductionTests.NEW, FullStackProductionTests.NEWER
    REPOS = FullStackProductionTests.REPOS
    manifest = FullStackProductionTests.manifest
    settings = FullStackProductionTests.settings
    paste = FullStackProductionTests.paste
    printed = FullStackProductionTests.printed
    versions = FullStackProductionTests.versions
    receipts = FullStackProductionTests.receipts
    receipt_paths = FullStackProductionTests.receipt_paths
    backups = FullStackProductionTests.backups
    running = FullStackProductionTests.running
    stage = FullStackProductionTests.stage

    def setUp(self):
        FullStackProductionTests.setUp(self)

    def leave_the_journal_of(self, path):
        """The journal the run that wrote receipt `path` had when it was stopped just before removing it."""

        record = json.loads(Path(path).read_text(encoding="utf-8"))
        StateDir.write_journal(StateDir(self.root), dict(record, status="running"))

    def test_check_plans_the_staged_update_and_status_says_the_journal_is_left_over(self):
        err = self.stage()
        finish = self.printed(err, r"Then run `([^`]+)` to restart the stack")
        self.leave_the_journal_of(self.receipt_paths()[-1])

        code, out, err = self.paste("./bin/update --production check")
        self.assertEqual(code, 0, err)
        # Nothing is put back: the staged update is still the one to finish, with the command its run printed.
        self.assertEqual(self.printed(out, r"update available: .*?\brun (\./bin/update .+?) \(finishes"), finish)
        self.assertIn("a finished bin/update apply left its journal; the next apply or rollback removes it and "
                      "recovers nothing", out)
        self.assertNotIn("this plan assumes recovery has put back", out)

        code, out, err = self.paste("./bin/update --production status")
        self.assertEqual((code, err), (0, ""))
        self.assertIn(f"Journal       left by an apply to {self.NEW} that finished; the next apply or rollback "
                      "removes it", out)
        self.assertNotIn("Interrupted", out)
        self.assertIn(f"Last run      apply to {self.NEW}: staged", out)

        # The finishing apply, explicitly acknowledged, removes the completed journal and finishes the stage.
        code, out, err = self.paste(finish + ' --acknowledge-stale-stage')
        self.assertEqual(code, 0, err)
        self.assertIn("Removed the journal of a bin/update apply", err)
        self.assertFalse(StateDir(self.root).journal_path.exists())
        self.assertEqual(self.engine.running(), self.running(self.NEW))
        self.assertEqual([receipt["status"] for receipt in self.receipts()], ["staged", "succeeded"])

    def test_a_journal_of_an_interrupted_run_is_still_previewed_and_reported_as_interrupted(self):
        self.stage()
        record = json.loads(Path(self.receipt_paths()[-1]).read_text(encoding="utf-8"))
        # Another run (its own id) of the same release: it never wrote its receipt.
        record['env_recovery'] = envfile_module.recovery_record(self.env_file, record['env_changes'])
        StateDir.write_journal(StateDir(self.root), dict(record, status="running", run_id="another-run"))
        code, out, err = self.paste("./bin/update --production check")
        self.assertEqual(code, 0, err)
        self.assertIn("this plan assumes recovery has put back", out)
        code, out, err = self.paste("./bin/update --production status")
        self.assertIn(f"Interrupted   an apply to {self.NEW} stopped at stage {record['stage']}; the next apply or "
                      "rollback recovers it", out)

    def test_every_article_the_status_line_uses(self):
        self.assertEqual([cli._a(kind) for kind in ("apply", "rollback", None, "", "update")],
                         ["an apply", "a rollback", "a run", "a run", "an update"])



# ------------------------------------------------------------ install, round 6 --

class InstallRound6ShellRuleTests(TempRootTest):
    """U9 (H3): a Compose command printed for the operator to run by hand on a production install is followed by
    the one shared shell rule, which credits Compose's precedence, never deploy/setup.sh."""

    PRODUCTION_HINT = InstallRound5CheckStackTests.PRODUCTION_HINT

    def rule(self, env_name="deploy/.env.production"):
        return (f"Run it from a shell that does not export any key of {env_name} (Compose prefers your shell over "
                "the env file).")

    def production(self, *, env_file=None, **kwargs):
        self.production_files()
        if env_file is not None:
            self.write(env_file, "FUTURE_AGI_VERSION=v1.0.0\n")
            kwargs["env_file"] = self.root / env_file
        return Installation(self.root, production=True, environ={}, runner=Runner(docker_handler()),
                            compose=COMPOSE, **kwargs)

    def test_the_shell_rule_is_the_shared_sentence_naming_the_env_file(self):
        self.assertEqual(self.production().shell_rule(), self.rule())
        self.assertEqual(self.production(env_file="deploy/prod env").shell_rule(), self.rule("deploy/prod env"))
        self.assertNotIn("setup.sh", self.production().shell_rule())

    def test_the_start_command_of_a_stopped_production_stack_is_followed_by_the_rule(self):
        # An exported FUTURE_AGI_VERSION would make the pasted `up` start another release than the env file names.
        problem, = self.production().check_stack([])
        start = f"`{self.PRODUCTION_HINT} up -d --no-build --wait`"
        self.assertIn(f"start it with {start}. {self.rule()} Then check again", problem)
        self.assertTrue(problem.endswith(f"{start}. {self.rule()} Then check again"), problem)
        self.assertNotIn("deploy/setup.sh take", problem)
        self.assertEqual(len(re.findall(r"`[^`]+`", problem)), 1, problem)

    def test_the_rule_names_the_env_file_the_start_command_uses(self):
        problem, = self.production(env_file="deploy/prod env").check_stack([])
        start = ("`docker compose --env-file 'deploy/prod env' -f docker-compose.distributed.yml -f "
                 "deploy/docker-compose.production.yml up -d --no-build --wait`")
        self.assertIn(f"start it with {start}. {self.rule('deploy/prod env')} Then check again", problem)

    def test_from_another_directory_the_rule_still_follows_the_command(self):
        elsewhere = self.root / "else where"
        elsewhere.mkdir()
        problem, = self.production(caller_cwd=elsewhere).check_stack([])
        cd = f"cd {shlex.quote(str(self.root))} && "
        self.assertIn(f"start it with `{cd}{self.PRODUCTION_HINT} up -d --no-build --wait`. {self.rule()} Then "
                      "check again", problem)

    def test_only_the_production_start_command_carries_the_rule(self):
        # Standalone and Distributed refuse exported version keys before planning (_check_shell_overrides).
        for env_text in ("", "COMPOSE_FILE=docker-compose.distributed.yml\n"):
            with self.subTest(env=env_text):
                problem, = self.install(env_text, runner=Runner(docker_handler())).check_stack([])
                self.assertNotIn("Run it from a shell", problem)
                self.assertTrue(problem.endswith("start it with `docker compose up -d`, or install it with "
                                                 "`./bin/install`, then check again"), problem)
        problem, = self.production().check_stack([])
        self.assertEqual(problem.count(self.rule()), 1, problem)


class InstallRound6ReleaseOptionsTests(TempRootTest):
    """U39: the `./bin/update --production check` check_stack prints carries the run's release options
    (Installation.command_options), so pasted it checks the release this run was given."""

    OVERLAY = InstallRound5CheckStackTests.OVERLAY
    DISTRIBUTED = "COMPOSE_FILE=docker-compose.distributed.yml\n"

    def setUp(self):
        super().setUp()
        self.manifest = str(self.root / "rel ease" / "future-agi-update-manifest.json")
        self.options = ["--manifest", self.manifest, "--channel", "beta"]
        self.shown = f"--manifest {shlex.quote(self.manifest)} --channel beta"

    def inst(self, env_text=DISTRIBUTED, **kwargs):
        return self.install(env_text, runner=Runner(docker_handler()), **kwargs)

    def parsed(self, command):
        """bin/update's parse of a printed command (after any `cd <checkout> && `)."""

        words = shlex.split(command)
        if words[0] == "cd":
            self.assertEqual((Path(words[1]).resolve(), words[2]), (self.root, "&&"))
            words = words[3:]
        self.assertEqual(words[0], "./bin/update")
        stderr = io.StringIO()
        try:
            with contextlib.redirect_stderr(stderr):
                return cli.build_parser().parse_args(words[1:])
        except SystemExit as exc:
            self.fail(f"bin/update rejects `{command}` (exit {exc.code}): {stderr.getvalue().strip()}")

    def assert_checks_this_release(self, command):
        parsed = self.parsed(command)
        self.assertEqual((parsed.command, parsed.production, parsed.manifest, parsed.channel),
                         ("check", True, self.manifest, "beta"), command)

    def test_the_overlay_hint_carries_the_release_options(self):
        self.write(PRODUCTION_ENV, "FUTURE_AGI_VERSION=v1.0.0\n")
        self.assertEqual(self.inst(command_options=self.options).check_stack(self.OVERLAY), [
            f"this install runs the production overlay (check-only bootstrap jobs). Use `./bin/update --production "
            f"check {self.shown}`, which uses deploy/.env.production and the overlay as deploy/setup.sh does"])

    def test_the_attribute_set_after_construction_is_carried_too(self):
        # How __main__ may hand them over: the installation is built before the release is fetched.
        self.write(PRODUCTION_ENV, "FUTURE_AGI_VERSION=v1.0.0\n")
        inst = self.inst()
        inst.command_options = list(self.options)
        problem, = inst.check_stack(self.OVERLAY)
        command, = re.findall(r"`([^`]+)`", problem)
        self.assert_checks_this_release(command)

    def test_the_hint_without_deploy_env_production_carries_them(self):
        self.assertEqual(self.inst(command_options=self.options).check_stack(self.OVERLAY), [
            f"this install runs the production overlay (check-only bootstrap jobs). Use `./bin/update --production "
            f"check {self.shown}` with --env-file naming the env file deploy/setup.sh used (deploy/.env.production "
            "does not exist), which uses it and the overlay as deploy/setup.sh does"])

    def test_every_production_remedy_of_check_stack_carries_them_and_works_pasted(self):
        self.write(PRODUCTION_ENV, "FUTURE_AGI_VERSION=v1.0.0\n")
        elsewhere = self.root / "else where"
        elsewhere.mkdir()
        for caller in (None, elsewhere):
            with self.subTest(caller=caller):
                standalone = self.inst("", command_options=self.options, caller_cwd=caller)
                problems = [*standalone.check_stack([{"service": "backend", "command": []}]),
                            *standalone.check_stack([]), *self.inst(command_options=self.options,
                                                                    caller_cwd=caller).check_stack(self.OVERLAY)]
                commands = [command for problem in problems for command in re.findall(r"`([^`]+)`", problem)
                            if "./bin/update" in command]
                self.assertEqual(len(commands), 3, problems)
                for command in commands:
                    self.assertEqual(command.startswith("cd "), caller is not None)
                    self.assert_checks_this_release(command)

    def test_the_refusal_of_a_production_checkout_without_production_carries_them(self):
        self.write(PRODUCTION_ENV, "FUTURE_AGI_VERSION=v1.0.0\n")
        with self.assertRaises(InstallError) as caught:
            Installation(self.root, environ={}, runner=Runner(), compose=COMPOSE, command_options=self.options)
        command, = re.findall(r"`([^`]+)`", str(caught.exception))
        self.assertEqual(command, f"./bin/update --production check {self.shown}")
        self.assert_checks_this_release(command)

    def test_only_check_and_apply_commands_take_them(self):
        inst = self.inst(command_options=self.options)
        self.assertEqual(inst.production_command("check"), f"./bin/update --production check {self.shown}")
        self.assertEqual(inst.production_command("apply --stage"),
                         f"./bin/update --production apply --stage {self.shown}")
        self.assertEqual(inst.production_command("status"), "./bin/update --production status")
        self.assertEqual(inst.production_command("rollback --receipt r.json --yes"),
                         "./bin/update --production rollback --receipt r.json --yes")

    def test_a_run_without_release_options_prints_the_command_as_before(self):
        self.assertEqual(self.inst().command_options, [])
        self.assertEqual(self.inst().production_command("check"), "./bin/update --production check")


# --------------------------------------------------------------- CLI, round 6 --

class CliRound6CommandOptionsTests(unittest.TestCase):
    """U39: main gives the installation this run's release options before any plan or stack check, so the
    bin/update commands the installation prints itself (check_stack's `./bin/update --production check`) repeat
    them, as the plan's own commands do."""

    setUp = MainTests.setUp
    patch = MainTests.patch
    main = MainTests.main

    GIVEN = ("--manifest", "release files/m.json", "--bundle", "https://example.com/m.json.sigstore.json",
             "--policy", "major")

    def expected(self):
        """GIVEN as main collects it: the local manifest made absolute (from --cwd), the others as given."""

        return ["--manifest", str(self.cwd / "release files" / "m.json"), "--bundle",
                "https://example.com/m.json.sigstore.json", "--policy", "major"]

    def record_planning(self):
        """[(the installation's command_options, build_plan's command_options)] for every plan built."""

        seen = []

        def plan(manifest, verification, installation, settings, **kwargs):
            seen.append((list(installation.command_options), list(kwargs["command_options"])))
            return self.plan

        self.build_plan.side_effect = plan
        return seen

    def test_check_plans_with_the_release_options_on_the_installation(self):
        for argv in (("check", *self.GIVEN), (*self.GIVEN, "check"), ("check", *self.GIVEN, "--format", "json")):
            with self.subTest(argv=argv):
                seen = self.record_planning()
                code, _, err = self.main(*argv)
                self.assertEqual(code, 0, err)
                self.assertEqual(seen, [(self.expected(), self.expected())])

    def test_apply_sets_them_before_the_updater_and_its_plan(self):
        seen = self.record_planning()
        constructed = []

        def updater(installation, *args, **kwargs):
            constructed.append((list(installation.command_options), list(kwargs["command_options"])))
            return mock.DEFAULT

        self.Updater.side_effect = updater
        self.Updater.return_value.apply.side_effect = lambda make_plan, *args, **kwargs: make_plan([])
        code, _, err = self.main("apply", *self.GIVEN, "--allow-no-backup")
        self.assertEqual(code, 0, err)
        self.assertEqual(constructed, [(self.expected(), self.expected())])
        self.assertEqual(seen, [(self.expected(), self.expected())])

    def test_only_the_options_given_are_repeated(self):
        # A setting (update.conf's manifest URL, the policy) is not an option: a pasted command reads it again.
        (self.root / "update.conf").write_text("FUTURE_AGI_UPDATE_POLICY=patch\n")
        seen = self.record_planning()
        code, _, err = self.main("check")
        self.assertEqual(code, 0, err)
        self.assertEqual(seen, [([], [])])
        for argv in (("status",), ("rollback",), ("rollback", "--yes")):
            with self.subTest(argv=argv):
                self.installations.clear()
                code, _, err = self.main(*argv)
                self.assertEqual(code, 0, err)
                self.assertEqual([inst.command_options for inst in self.installations], [[]])


class CliRound6OverlayHintFullStackTests(unittest.TestCase):
    """U39 end to end: run without --production on a checkout whose stack runs the production overlay (its
    check-only bootstrap jobs), check and apply name `./bin/update --production check` with the release options
    they were given, and that command, pasted, plans the release they did.

    FullStackProductionTests' install, with a stray .env selecting the Distributed stack next to
    deploy/.env.production (Compose reads .env when bin/update passes no --env-file). update.conf follows the
    release the install runs, so only --manifest names a release to update to: the command without it plans
    another one (up to date).
    """

    OLD, NEW, NEWER = FullStackProductionTests.OLD, FullStackProductionTests.NEW, FullStackProductionTests.NEWER
    REPOS = FullStackProductionTests.REPOS
    manifest = FullStackProductionTests.manifest
    settings = FullStackProductionTests.settings
    paste = FullStackProductionTests.paste
    printed = FullStackProductionTests.printed
    versions = FullStackProductionTests.versions
    receipts = FullStackProductionTests.receipts
    backups = FullStackProductionTests.backups
    running = FullStackProductionTests.running

    HINT = r"If this is the checkout's production install, use `([^`]+)`"

    def setUp(self):
        FullStackProductionTests.setUp(self)
        self.settings(manifest=self.OLD)
        (self.root / ".env").write_text(f"COMPOSE_FILE={DISTRIBUTED_FILE}\nFUTURE_AGI_VERSION={self.OLD}\n"
                                        f"FRONTEND_VERSION={self.OLD}\n")
        values = self.engine.values
        dot_env = ["--env-file", str(self.root / ".env")]
        self.engine.values = lambda args, env: values(args if "--env-file" in args else [*args, *dot_env], env)

    def words(self, command):
        """A printed bin/update command's words, checked to parse as bin/update parses them."""

        words = shlex.split(command)
        self.assertEqual(words[0], "./bin/update", command)
        stderr = io.StringIO()
        try:
            with contextlib.redirect_stderr(stderr):
                cli.build_parser().parse_args(words[1:])
        except SystemExit as exc:
            self.fail(f"bin/update rejects `{command}` (exit {exc.code}): {stderr.getvalue().strip()}")
        return words

    def assert_plans_the_new_release(self, command):
        code, out, err = self.paste(command)
        self.assertEqual(code, 0, err)
        self.assertIn(f"  Release       {self.NEW} ", out)
        self.assertIn("update available: run ./bin/update --production apply --stage --manifest ", out)
        # Without the options it plans update.conf's release, which the install already runs.
        code, out, err = self.paste("./bin/update --production check")
        self.assertEqual(code, 0, err)
        self.assertIn("up to date, nothing to do", out)

    def test_check_names_the_production_check_with_its_release_options(self):
        manifest = str(self.manifests[self.NEW])  # its path has a space in it
        code, out, err = self.paste(f"./bin/update check --manifest {shlex.quote(manifest)} --policy minor")
        self.assertEqual(code, 2, err)
        hint = self.printed(err, self.HINT)
        self.assertEqual(self.words(hint), ["./bin/update", "--production", "check", "--manifest", manifest,
                                            "--policy", "minor"])
        self.assert_plans_the_new_release(hint)

    def test_apply_names_it_too_and_changes_nothing(self):
        # The run of the report: `bin/update apply --manifest ... --allow-no-backup`. --allow-no-backup is apply's
        # own option, not a release option: the check command does not take it.
        manifest = str(self.manifests[self.NEW])
        code, out, err = self.paste(f"./bin/update apply --manifest {shlex.quote(manifest)} --allow-no-backup")
        self.assertEqual(code, 2, err)
        # The plan apply printed and the refusal both give it.
        hints = re.findall(self.HINT, err)
        self.assertEqual(len(hints), 1, err)
        self.assertEqual(len(set(hints)), 1, err)
        hint = hints[0]
        self.assertEqual(self.words(hint), ["./bin/update", "--production", "check", "--manifest", manifest])
        self.assertEqual((self.versions(), self.backups(), self.receipts(), self.engine.up_calls()),
                         ((self.OLD, self.OLD), 0, [], []))
        self.assertEqual(read_env(self.root / ".env")["FUTURE_AGI_VERSION"], self.OLD)
        self.assert_plans_the_new_release(hint)

    def test_from_another_directory_the_manifest_is_named_by_its_absolute_path(self):
        elsewhere = self.root.parent / "some where"
        elsewhere.mkdir()
        relative = os.path.relpath(self.manifests[self.NEW], elsewhere)
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = cli.main(["--root", str(self.root), "--cwd", str(elsewhere), "check", "--manifest", relative])
        self.assertEqual(code, 2, stderr.getvalue())
        hint = self.printed(stderr.getvalue(), self.HINT)
        words = shlex.split(hint)
        self.assertEqual(words[:3], ["cd", str(self.root), "&&"])
        command = " ".join(shlex.quote(word) for word in words[3:])
        # The path as given, joined to the directory bin/update ran in: it names the same file pasted anywhere.
        self.assertEqual(self.words(command), ["./bin/update", "--production", "check", "--manifest",
                                               str(elsewhere / relative)])
        self.assert_plans_the_new_release(command)

    def test_a_stopped_stack_names_the_production_check_with_its_release_options(self):
        # No containers: the reason names --production first (deploy/.env.production exists), with the options.
        self.engine.records = []
        manifest = str(self.manifests[self.NEW])
        code, out, err = self.paste(f"./bin/update check --manifest {shlex.quote(manifest)}")
        self.assertEqual(code, 3, err)
        first = self.printed(out, r"This checkout has deploy/\.env\.production: if Future AGI runs here as a "
                                  r"deploy/setup\.sh production install, run `([^`]+)`\. Otherwise ")
        self.assertEqual(self.words(first), ["./bin/update", "--production", "check", "--manifest", manifest])



# ------------------------------------------------------------ integrator, round 6 --

class IntegratorRound6CliTests(unittest.TestCase):
    """U21 on the CLI side: status and an interrupt word a journal whose run already wrote its receipt from that
    receipt's status (a final one finished; another one records how the run stopped). U39's residual: the
    installation's own refusal to start without --production repeats the release options too."""

    OLD, NEW, NEWER = FullStackProductionTests.OLD, FullStackProductionTests.NEW, FullStackProductionTests.NEWER
    REPOS = FullStackProductionTests.REPOS
    manifest = FullStackProductionTests.manifest
    settings = FullStackProductionTests.settings
    paste = FullStackProductionTests.paste
    printed = FullStackProductionTests.printed
    versions = FullStackProductionTests.versions
    receipts = FullStackProductionTests.receipts
    receipt_paths = FullStackProductionTests.receipt_paths
    backups = FullStackProductionTests.backups
    running = FullStackProductionTests.running
    stage = FullStackProductionTests.stage

    def setUp(self):
        FullStackProductionTests.setUp(self)

    def interrupt_the_first_journal_removal(self):
        """Ctrl-C lands just after the run wrote its receipt, before it removed its journal."""

        clear = StateDir.clear_journal
        calls = []

        def interrupted(state):
            calls.append(state)
            if len(calls) == 1:
                raise KeyboardInterrupt()
            return clear(state)

        patcher = mock.patch.object(StateDir, "clear_journal", autospec=True, side_effect=interrupted)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_an_interrupt_after_the_final_receipt_says_the_run_finished(self):
        self.interrupt_the_first_journal_removal()
        code, out, err = self.paste("./bin/update --production apply --stage")
        self.assertEqual(code, 130, err)
        staged = self.receipt_paths()[-1]
        self.assertEqual(self.receipts()[-1]["status"], "staged")
        self.assertTrue(StateDir(self.root).journal_path.exists())
        self.assertEqual(err.splitlines()[-1], (
            f"bin/update: interrupted after the apply finished: receipt {staged} records it "
            "(`./bin/update --production status` shows it); the next `./bin/update --production apply` or "
            "`./bin/update --production rollback` removes the journal it left and recovers nothing"))
        self.assertNotIn("interrupted before the apply finished", err)
        code, out, err = self.paste("./bin/update --production status")
        self.assertEqual((code, err), (0, ""))
        self.assertIn(f"Journal       left by an apply to {self.NEW} that finished; the next apply or rollback "
                      "removes it", out)

    def test_an_interrupt_after_a_receipt_that_records_a_stop_names_its_status(self):
        self.stage()
        record = json.loads(Path(self.receipt_paths()[-1]).read_text(encoding="utf-8"))
        # A rollback killed mid-rollout, whose recovery wrote its receipt; Ctrl-C before the journal was removed.
        stopped = dict(record, kind="rollback", status="interrupted_during_rollout", stage="rollout",
                       started_at="2026-10-20T00:00:00Z", run_id="killed-rollback", target_version=self.OLD,
                       rolls_back=self.receipt_paths()[-1])

        def stop(updater, receipt, yes=False):
            path = StateDir(self.root).write_receipt(stopped)
            StateDir(self.root).write_journal(dict(stopped, status="running"))
            stopped["_path"] = str(path)
            raise KeyboardInterrupt()

        with mock.patch.object(cli.Updater, "rollback", autospec=True, side_effect=stop):
            code, out, err = self.paste("./bin/update --production rollback --yes")
        self.assertEqual(code, 130, err)
        self.assertEqual(err.splitlines()[-1], (
            f"bin/update: interrupted after the rollback was recorded as interrupted_during_rollout: receipt "
            f"{stopped['_path']} records it (`./bin/update --production status` shows it); the next "
            "`./bin/update --production apply` or `./bin/update --production rollback` removes the journal it left "
            "and recovers nothing"))

    def test_status_names_the_status_of_a_receipt_that_records_a_stop(self):
        self.stage()
        record = json.loads(Path(self.receipt_paths()[-1]).read_text(encoding="utf-8"))
        stopped = dict(record, kind="rollback", status="interrupted_during_rollout", stage="rollout",
                       started_at="2026-10-20T00:00:00Z", run_id="killed-recovery", target_version=self.OLD)
        StateDir(self.root).write_receipt(stopped)
        StateDir(self.root).write_journal(dict(stopped, status="running"))
        code, out, err = self.paste("./bin/update --production status")
        self.assertEqual((code, err), (0, ""))
        line = [line for line in out.splitlines() if line.startswith("Journal")]
        self.assertEqual(line, [
            f"Journal       left by a rollback to {self.OLD} that was already recorded as interrupted_during_rollout "
            "(Last run, below); the next apply or rollback removes it, and `./bin/update --production check` says "
            "how to go on"], out)
        self.assertNotIn("that finished", out)
        self.assertIn(f"Last run      rollback to {self.OLD}: interrupted_during_rollout", out)

    def test_the_refusal_to_start_without_production_names_the_release_options(self):
        # update.conf follows the release the install runs: only --manifest names the release to update to.
        self.settings(manifest=self.OLD)
        manifest = str(self.manifests[self.NEW])  # its path has a space in it
        code, out, err = self.paste(f"./bin/update check --manifest {shlex.quote(manifest)} --policy minor")
        self.assertEqual(code, 2, err)
        hint = self.printed(err, r"Add --production to the command, as in `([^`]+)`")
        self.assertEqual(shlex.split(hint), ["./bin/update", "--production", "check", "--manifest", manifest,
                                             "--policy", "minor"])
        code, out, err = self.paste(hint)
        self.assertEqual(code, 0, err)
        self.assertIn(f"  Release       {self.NEW} ", out)
        self.assertIn("update available: run ./bin/update --production apply --stage --manifest ", out)



class IntegratorRound6InterruptTests(unittest.TestCase):
    """U21's interrupt wording when the receipts cannot be read: the journal is still named as the run to recover."""

    setUp = MainTests.setUp
    patch = MainTests.patch
    main = MainTests.main
    journal = CliMainAdditions.journal
    cd = CliRound4MainTests.cd

    def test_unreadable_receipts_leave_the_journal_to_recover(self):
        CliRound3MainTests.lock_probe(self, lambda state: None)

        def stop(*args, **kwargs):
            self.journal(**CLI_IDENTITY)
            raise KeyboardInterrupt()

        self.Updater.return_value.apply.side_effect = stop
        with mock.patch.object(StateDir, "latest_receipt", autospec=True, side_effect=StateError("unreadable")):
            code, _, err = self.main("apply")
        self.assertEqual(code, 130)
        self.assertEqual(err, f"bin/update: interrupted before the apply finished; the next `{self.cd()}./bin/update "
                              f"apply` or `{self.cd()}./bin/update rollback` recovers it first "
                              f"(`{self.cd()}./bin/update status` shows where it stopped)\n")


# ================================================================ round 7 ==

class CliRound7EntryPointTests(unittest.TestCase):
    """`python -m deploy.update` (the module's __main__ guard) exits with main()'s code."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        env_patch = mock.patch.dict(os.environ, clean_environ(), clear=True)
        env_patch.start()
        self.addCleanup(env_patch.stop)

    def run_module(self, *args):
        stdout, stderr = io.StringIO(), io.StringIO()
        with mock.patch.dict(sys.modules), mock.patch.object(sys, "argv", ["bin/update", *args]):
            # Imported already (as `cli`): runpy would warn, and must run a fresh copy.
            sys.modules.pop("deploy.update.__main__", None)
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                with self.assertRaises(SystemExit) as caught:
                    runpy.run_module("deploy.update", run_name="__main__")
        return caught.exception.code, stdout.getvalue(), stderr.getvalue()

    def test_an_error_exits_2_with_its_message(self):
        code, out, err = self.run_module("--root", str(self.root), "--cwd", str(self.root), "status")
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertEqual(err, f"bin/update: no .env in {self.root}: run bin/update from the checkout ./bin/install "
                              "set up\n")
        self.assertFalse((self.root / STATE_DIR).exists())

    def test_help_exits_0(self):
        code, out, err = self.run_module("--help")
        self.assertEqual((code, err), (0, ""))
        self.assertIn("usage: bin/update", out)


class CliRound7StaleWorkdirTests(unittest.TestCase):
    """The verify-<pid> sweep is best effort: a state directory it cannot list never stops a run."""

    setUp = MainTests.setUp
    patch = MainTests.patch
    main = MainTests.main
    dead_pid = MainTests.dead_pid

    def test_a_state_directory_that_cannot_be_listed_does_not_stop_check_or_apply(self):
        state = self.root / STATE_DIR
        stale = state / f"verify-{self.dead_pid()}"
        stale.mkdir(parents=True)
        (stale / "manifest.json").write_text("stale")
        iterdir = Path.iterdir

        def unlistable(path):
            if path == state:
                raise PermissionError(13, "Permission denied", str(path))
            return iterdir(path)

        for command in ("check", "apply"):
            with self.subTest(command=command):
                with mock.patch.object(Path, "iterdir", autospec=True, side_effect=unlistable):
                    code, _, err = self.main(command)
                self.assertEqual((code, err), (0, ""))
                self.assertEqual(self.fetch_calls[-1]["workdir"], self.workdir)
                # Nothing is removed on the strength of a listing that failed; the run's own workdir is.
                self.assertEqual((stale / "manifest.json").read_text(), "stale")
                self.assertFalse(self.workdir.exists())
        self.assertEqual(len(self.fetch_calls), 2)

    def test_every_dead_workdir_goes_whatever_order_the_directory_lists_them_in(self):
        state = self.root / STATE_DIR
        state.mkdir()
        others = [state / "verify-abc", state / "verify-0", state / "notes.txt"]
        others[0].mkdir()
        others[1].mkdir()
        others[2].write_text("keep")
        iterdir = Path.iterdir
        for first in ("others", "a dead one"):
            with self.subTest(first=first):
                dead = [state / f"verify-{self.dead_pid()}" for _ in range(2)]
                for directory in dead:
                    directory.mkdir()
                    (directory / "manifest.json").write_text("stale")
                order = others + dead if first == "others" else dead[:1] + others + dead[1:]

                def listing(path, order=order):
                    return iter(list(order)) if path == state else iterdir(path)

                with mock.patch.object(Path, "iterdir", autospec=True, side_effect=listing):
                    self.assertEqual(self.main("check")[0], 0)
                self.assertEqual([directory.exists() for directory in dead], [False, False])
                self.assertEqual([path.exists() for path in others], [True, True, True])


@unittest.skipIf(shutil.which("bash") is None, "bin/update needs bash")
class CliRound7BinUpdateSymlinkTests(unittest.TestCase):
    """A symlink to bin/update, or to the checkout's bin directory, runs the checkout the script belongs to."""

    setUp = BinUpdateScriptTests.setUp
    run_update = BinUpdateScriptTests.run_update

    def assertRunsTheCheckout(self, script, **options):
        result = self.run_update("status", script=script, **options)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertEqual(result.stderr, f"bin/update: no .env in {os.path.realpath(self.checkout)}: run "
                                        "bin/update from the checkout ./bin/install set up\n")
        self.assertEqual(result.stdout, "")

    def test_an_absolute_symlink_in_another_directory(self):
        links = self.base / "usr local bin"
        links.mkdir()
        (links / "fagi-update").symlink_to(self.checkout / "bin" / "update")
        self.assertRunsTheCheckout(links / "fagi-update")

    def test_a_chain_of_relative_and_absolute_symlinks(self):
        first, second = self.base / "links" / "a", self.base / "links" / "b"
        first.mkdir(parents=True)
        second.mkdir()
        (second / "update").symlink_to(self.checkout / "bin" / "update")
        (first / "fagi-update").symlink_to(Path("..") / "b" / "update")  # relative to the link's own directory
        self.assertRunsTheCheckout(first / "fagi-update")
        # Run by a relative path from the caller's directory, too.
        self.assertRunsTheCheckout(Path("..") / "links" / "a" / "fagi-update")

    def test_a_symlinked_bin_directory(self):
        # `..` of the bin directory is the checkout, not the directory that holds the link.
        (self.base / "fagi-bin").symlink_to(self.checkout / "bin", target_is_directory=True)
        self.assertRunsTheCheckout(self.base / "fagi-bin" / "update")
        result = self.run_update("--help", script=self.base / "fagi-bin" / "update")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("usage: bin/update", result.stdout)

    def test_an_exported_cdpath_neither_breaks_nor_moves_the_checkout(self):
        # `bin/update` typed in the checkout (no ./) makes bin/update `cd bin/..`, a relative path bash looks up
        # in CDPATH first, printing the directory it found: "." finds the checkout, another entry another `bin`.
        elsewhere = self.base / "elsewhere"
        for name in ("bin", "links"):
            (elsewhere / name).mkdir(parents=True)
        (self.base / "links").mkdir()
        (self.base / "links" / "fagi-update").symlink_to(self.checkout / "bin" / "update")
        for cdpath in (".", str(elsewhere), ":" + str(elsewhere)):
            with self.subTest(CDPATH=cdpath):
                env = dict(self.env, CDPATH=cdpath)
                self.assertRunsTheCheckout(Path("bin") / "update", cwd=self.checkout, env=env)
                self.assertRunsTheCheckout(Path("links") / "fagi-update", cwd=self.base, env=env)



@unittest.skipIf(shutil.which("bash") is None, "bin/update needs bash")
class CliRound7BinUpdateWrapperTests(unittest.TestCase):
    """What the bash wrapper itself decides: the Python it accepts, the arguments it passes on, where Python runs."""

    setUp = BinUpdateScriptTests.setUp
    run_update = BinUpdateScriptTests.run_update

    def python_reporting(self, version):
        """A python3 that answers bin/update's version check as Python `version` would (and is the real one else)."""

        wrapper = self.base / ("python " + ".".join(str(number) for number in version))
        wrapper.write_text(
            "#!/bin/sh\n"
            "case \"$3\" in\n"
            f"  *version_info*) exec '{sys.executable}' -I -c \"import sys; sys.version_info = "
            f"{tuple(version) + ('final', 0)!r}\n$3\" ;;\n"
            "esac\n"
            f"exec '{sys.executable}' \"$@\"\n")
        wrapper.chmod(0o755)
        return wrapper

    def test_python_3_9_is_accepted_and_3_8_refused(self):
        result = self.run_update("--help", env=dict(self.env, PYTHON=str(self.python_reporting((3, 9, 0)))))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("usage: bin/update", result.stdout)
        old = self.python_reporting((3, 8, 18))
        result = self.run_update("--help", env=dict(self.env, PYTHON=str(old)))
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertTrue(result.stderr.startswith(f"bin/update: needs Python 3.9 or newer; {old} is Python "),
                        result.stderr)

    def test_a_python_that_is_not_there_is_named(self):
        missing = self.base / "no such dir" / "python3.11"
        result = self.run_update("status", env=dict(self.env, PYTHON=str(missing)))
        self.assertEqual((result.returncode, result.stdout), (2, ""))
        self.assertEqual(result.stderr, f"bin/update: needs Python 3.9 or newer on the Docker host ({missing} was not "
                                        "found)\n")
        # The default is python3 from PATH: here a PATH with the shell tools bin/update uses, and no python3.
        tools = self.base / "no python"
        tools.mkdir()
        for tool in ("bash", "dirname", "readlink"):
            (tools / tool).symlink_to(shutil.which(tool))
        env = {key: value for key, value in self.env.items() if key != "PYTHON"}
        for python in ({}, {"PYTHON": ""}):  # an empty PYTHON is no PYTHON
            with self.subTest(python=python):
                result = self.run_update("status", env=dict(env, PATH=str(tools), **python))
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stderr, "bin/update: needs Python 3.9 or newer on the Docker host (python3 "
                                                "was not found)\n")

    def test_a_relative_python_names_the_one_in_the_callers_directory_even_from_the_checkout(self):
        # Python runs from the checkout: the interpreter bin/update checked must be the one it then starts.
        (self.caller / "py").mkdir()
        (self.caller / "py" / "python3").symlink_to(sys.executable)
        impostor = self.checkout / "py" / "python3"
        executable(impostor, f"#!/bin/sh\n: > '{self.marker}'\nexit 99\n")
        result = self.run_update("status", env=dict(self.env, PYTHON=os.path.join("py", "python3")))
        self.assertEqual((result.returncode, result.stdout), (2, ""), result.stderr)
        self.assertEqual(result.stderr, f"bin/update: no .env in {os.path.realpath(self.checkout)}: run "
                                        "bin/update from the checkout ./bin/install set up\n")
        self.assertFalse(self.marker.exists())
        # One that is only in the checkout is not found where the caller named it.
        (self.checkout / "py" / "python4").symlink_to(sys.executable)
        result = self.run_update("status", env=dict(self.env, PYTHON=os.path.join(".", "py", "python4")))
        self.assertEqual((result.returncode, result.stdout), (2, ""))
        self.assertEqual(result.stderr, "bin/update: needs Python 3.9 or newer on the Docker host "
                                        f"({os.path.realpath(self.caller)}/./py/python4 was not found)\n")

    def test_python_2_is_named_by_its_version_and_its_own_complaints_are_not_shown(self):
        # Python 2 prints its version on stderr and refuses -I with a usage message of its own.
        fake = self.base / "python2.7"
        fake.write_text("#!/bin/sh\n"
                        "case \"$1\" in\n"
                        "  -V) echo 'Python 2.7.18' >&2; exit 0 ;;\n"
                        "  -I) echo 'Unknown option: -I' >&2; echo 'usage: python2.7 [option] ...' >&2; exit 2 ;;\n"
                        "esac\n"
                        "exit 99\n")
        fake.chmod(0o755)
        result = self.run_update("status", env=dict(self.env, PYTHON=str(fake)))
        self.assertEqual((result.returncode, result.stdout), (2, ""))
        self.assertEqual(result.stderr, f"bin/update: needs Python 3.9 or newer; {fake} is Python 2.7.18\n")

    def test_arguments_with_spaces_reach_bin_update_whole(self):
        (self.checkout / DISTRIBUTED_FILE).write_text("services: {}\n")
        (self.checkout / PRODUCTION_OVERLAY).write_text("services: {}\n")
        result = self.run_update("--production", "--env-file", "prod env/fagi prod.env", "status")
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertTrue(result.stderr.startswith(
            f"bin/update: {os.path.realpath(self.caller)}/prod env/fagi prod.env does not exist."), result.stderr)

    @unittest.skipUnless(hasattr(os, "mkfifo"), "needs named pipes")
    def test_sigterm_to_bin_update_reaches_python_which_exits_130(self):
        # bin/update execs Python: a SIGTERM sent to the process that was started is handled by bin/update itself
        # (exit 130, "interrupted ..."), never left to kill a shell while the update runs on unsupervised.
        (self.checkout / ".env").write_text("FUTURE_AGI_VERSION=v1.0.0\n")
        sync = self.base / "sync"
        os.mkfifo(str(sync))
        bindir = self.base / "fake bin"
        bindir.mkdir()
        executable(bindir / "docker", "#!/bin/sh\n"
                   "if [ ! -e \"$SYNC.used\" ]; then : > \"$SYNC.used\"; echo started > \"$SYNC\"; read line; fi\n"
                   "exit 1\n")
        env = dict(self.env, SYNC=str(sync), PATH=str(bindir) + os.pathsep + self.env.get("PATH", os.defpath))
        # Read end opened read-write, so opening it never waits; a second write end says when bin/update ended
        # without reaching docker, so the wait below always ends.
        reader = os.open(str(sync), os.O_RDWR)
        self.addCleanup(os.close, reader)
        writer = os.open(str(sync), os.O_WRONLY | os.O_NONBLOCK)
        process = subprocess.Popen([str(self.checkout / "bin" / "update"), "status"], cwd=str(self.caller), env=env,
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(process.kill)

        def say_when_it_ends():
            try:
                process.wait()
                os.write(writer, b"ended\n")
            finally:
                os.close(writer)

        watcher = threading.Thread(target=say_when_it_ends, daemon=True)
        watcher.start()
        # The first docker call (`docker compose version`) is running.
        self.assertEqual(os.read(reader, 64), b"started\n")
        process.send_signal(signal.SIGTERM)
        out, err = process.communicate(timeout=120)
        watcher.join()
        self.assertEqual((process.returncode, out, err), (130, "", "bin/update: interrupted before anything was "
                                                                   "changed\n"))

    def fake_tools(self):
        """A docker whose Compose resolves a Standalone project with no containers, and a cosign v3 that verifies."""

        bindir = self.base / "fake bin"
        bindir.mkdir()
        config = json.dumps({"name": "futureagi", "services": {"app": {"image": "futureagi/standalone:v1.1.0"}}})
        (bindir / "docker").write_text(
            "#!/bin/sh\n"
            "case \"$*\" in\n"
            "  'compose version --short') echo 2.29.1 ;;\n"
            "  'compose config --services') echo app ;;\n"
            "  'compose config --profiles') ;;\n"
            f"  'compose config --format json') echo '{config}' ;;\n"
            "  ps\\ *) ;;\n"
            "  *) echo \"fake docker: unexpected $*\" >&2; exit 1 ;;\n"
            "esac\n")
        (bindir / "cosign").write_text(
            "#!/bin/sh\n"
            "case \"$1\" in\n"
            "  version) echo '{\"gitVersion\": \"v3.1.3\"}' ;;\n"
            "  verify-blob) exit 0 ;;\n"
            "  *) exit 1 ;;\n"
            "esac\n")
        for tool in ("docker", "cosign"):
            (bindir / tool).chmod(0o755)
        return dict(self.env, PATH=str(bindir) + os.pathsep + self.env.get("PATH", os.defpath))

    def check_with_a_relative_key(self, key_directory):
        (self.checkout / ".env").write_text("FUTURE_AGI_VERSION=v1.1.0\n")
        (self.checkout / "update.conf").write_text("FUTURE_AGI_UPDATE_COSIGN_PUBLIC_KEY=keys/cosign.pub\n")
        (key_directory / "keys").mkdir()
        (key_directory / "keys" / "cosign.pub").write_text("-----BEGIN PUBLIC KEY-----\n")
        (self.caller / "manifest.json").write_text(json.dumps(valid_manifest("v1.2.0")))
        (self.caller / "future-agi-update-manifest.sigstore.json").write_text("{}")
        return self.run_update("check", "--manifest", "manifest.json", env=self.fake_tools())

    def test_a_relative_setting_names_a_file_in_the_checkout(self):
        # update.conf lives in the checkout: Python runs there, whatever directory bin/update was started from.
        result = self.check_with_a_relative_key(self.checkout)
        # Blocked for this fake stack's own reasons (no containers, ...), never for the key.
        self.assertEqual((result.returncode, result.stderr), (3, ""))
        self.assertIn("  Signature     verified (public key: keys/cosign.pub)\n", result.stdout)
        self.assertNotIn("cannot read FUTURE_AGI_UPDATE_COSIGN_PUBLIC_KEY", result.stdout)

    def test_a_relative_setting_never_names_a_file_in_the_callers_directory(self):
        result = self.check_with_a_relative_key(self.caller)
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        self.assertIn("the release signature is not verified: cannot read FUTURE_AGI_UPDATE_COSIGN_PUBLIC_KEY "
                      "keys/cosign.pub: No such file or directory", result.stdout)
        self.assertNotIn("  Signature     verified", result.stdout)



class InstallRound7ComposeProbeProcessTests(TempRootTest):
    """resolve_compose_command with real processes: fake docker / docker-compose executables (never Docker itself)."""

    PLUGIN_MISSING = "echo \"docker: 'compose' is not a docker command.\" >&2\nexit 1\n"

    def resolve(self, **tools):
        bindir = self.root / "fake bin"
        for name, script in tools.items():
            executable(bindir / name.replace("_", "-"), "#!/bin/sh\n" + script)
        bindir.mkdir(exist_ok=True)
        # `docker` is run by name: only the fake one can be found.
        with mock.patch.dict(os.environ, {"PATH": str(bindir)}):
            return resolve_compose_command(which=lambda name: shutil.which(name, path=str(bindir))), bindir

    def test_the_plugins_version_is_read_from_its_output(self):
        command, _ = self.resolve(docker="[ \"$*\" = 'compose version --short' ] && echo 2.29.1 && exit 0\nexit 9\n",
                                  docker_compose="echo v2.40.3\n")
        self.assertEqual((command, command.version), (["docker", "compose"], (2, 29, 1)))

    def test_a_standalone_binarys_version_is_read_from_its_output(self):
        for output, version in (("v2.40.3", (2, 40, 3)), ("2.24.7", (2, 24, 7)), ("083f676", None)):
            with self.subTest(output=output):
                command, bindir = self.resolve(docker=self.PLUGIN_MISSING,
                                               docker_compose=f"[ \"$*\" = 'version --short' ] && echo {output}\n")
                self.assertEqual((command, command.version), ([str(bindir / "docker-compose")], version))

    def test_a_standalone_binary_that_fails_or_is_v1_is_refused(self):
        for script, shown in (("echo 'something broke' >&2\nexit 1\n", "(unknown version)"),
                              ("echo 2.29.1\nexit 3\n", "2.29.1"), ("echo 1.29.2\n", "1.29.2")):
            with self.subTest(shown=shown):
                with self.assertRaises(InstallError) as caught:
                    self.resolve(docker_compose=script)
                self.assertEqual(str(caught.exception),
                                 f"docker-compose {shown} is too old: bin/update needs Docker Compose v2")



class CliRound7HelpTests(unittest.TestCase):
    """Each command's --help lists exactly the options it takes, under the installation and release headings."""

    INSTALLATION = ["--production", "--env-file"]
    RELEASE = ["--manifest", "--bundle", "--policy", "--channel"]

    def sections(self, *argv):
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as caught:
                cli.main([*argv, "--help"])
        self.assertEqual(caught.exception.code, 0)
        sections, title = {}, None
        for line in stdout.getvalue().splitlines():
            if line and not line[0].isspace() and line.endswith(":"):
                title = line[:-1]
                sections[title] = []
            elif title is not None and re.match(r"^  -", line):
                sections[title].append(re.match(r"^  (-[^\s,]+)", line).group(1))
        # Python 3.9 heads the command's own options "optional arguments", later releases "options".
        sections["options"] = sections.pop("optional arguments", None) or sections.pop("options")
        sections.pop("positional arguments", None)
        return sections

    def test_each_command_shows_its_options_under_their_headings(self):
        expected = {
            ("check",): {"options": ["-h", "--format"], "installation": self.INSTALLATION, "release": self.RELEASE},
            ("apply",): {"options": ["-h", "--backup-command", "--allow-no-backup", "--dry-run", "--stage", "--acknowledge-stale-stage"],
                         "installation": self.INSTALLATION, "release": self.RELEASE},
            ("rollback",): {"options": ["-h", "--receipt", "--yes"], "installation": self.INSTALLATION},
            ("status",): {"options": ["-h", "--format", "--export-public"], "installation": self.INSTALLATION},
            (): {"options": ["-h"], "installation": self.INSTALLATION},
        }
        for argv, sections in expected.items():
            with self.subTest(argv=argv):
                self.assertEqual(self.sections(*argv), sections)


class CliRound7JournalWithoutEnvFileTests(unittest.TestCase):
    """A journal that does not record its env file (written by hand, say) is recovered with that kind of install's
    default env file: production's is deploy/.env.production, so its command needs no --env-file."""

    setUp = MainTests.setUp
    patch = MainTests.patch
    main = MainTests.main
    cd = CliRound4MainTests.cd

    def test_another_installs_journal_names_its_default_command(self):
        StateDir(self.root).write_journal({"kind": "apply", "target_version": "v1.2.0", "stage": "pulled",
                                           "production": True})
        theirs = f"{self.cd()}./bin/update --production"
        recover = (f"run `{theirs} rollback` (without --yes it only recovers that run and changes nothing else)")
        code, out, err = self.main("status")
        self.assertEqual(code, 2, err)
        self.assertIn('must be recovered before this install', err)
        self.assertIn(f"this install cannot be updated until it is recovered: {recover}; `{theirs} apply` would "
                      "recover it too, but would also continue that install's update", out)
        self.assertNotIn("--env-file", out)
        self.main("check")  # the plan (a stand-in here) blocks on this reason
        self.assertEqual(self.build_plan.call_args[1]["journal_reason"].split(": ", 1)[1].split(", then ")[0], recover)



class CliRound7InterruptTests(unittest.TestCase):
    """What an interrupt says when the receipts could not be listed as the run started, or before any run started."""

    setUp = MainTests.setUp
    patch = MainTests.patch
    main = MainTests.main
    receipt = CliRound2MainTests.receipt
    cd = CliRound4MainTests.cd

    def test_receipts_unreadable_as_the_run_started_still_point_to_what_it_recorded(self):
        listings = []
        receipts = StateDir.receipts

        def flaky(state):
            listings.append(state)
            if len(listings) == 1:
                raise StateError("cannot read .futureagi-updates: Input/output error")
            return receipts(state)

        made = []

        def stop(*args, **kwargs):
            made.append(self.receipt(target_version="v1.2.0"))
            raise KeyboardInterrupt()

        self.Updater.return_value.apply.side_effect = stop
        with mock.patch.object(StateDir, "receipts", autospec=True, side_effect=flaky):
            code, _, err = self.main("apply")
        self.assertEqual(code, 130)
        self.assertEqual(err, f"bin/update: interrupted; what the run did is recorded in {made[0]} "
                              f"(`{self.cd()}./bin/update status` shows it)\n")

    def test_an_interrupt_before_the_run_is_not_blamed_on_earlier_receipts_or_journals(self):
        self.receipt()
        StateDir(self.root).write_journal({"kind": "apply", "target_version": "v1.2.0", "stage": "pulled",
                                           **CLI_IDENTITY})
        for name, argv, arrange in (
                ("manifest download", ("check",), lambda: setattr(self, "fetch_error", KeyboardInterrupt())),
                ("verification", ("apply",),
                 lambda: setattr(self.Verifier.return_value.verify, "side_effect", cli.Interrupted("SIGTERM")))):
            with self.subTest(name):
                self.fetch_error = None
                self.Verifier.return_value.verify.side_effect = None
                arrange()
                code, _, err = self.main(*argv)
                self.assertEqual((code, err), (130, "bin/update: interrupted before anything was changed\n"))
                self.Updater.return_value.apply.assert_not_called()

    def test_an_earlier_runs_receipt_does_not_make_an_interrupted_run_finished(self):
        earlier = self.receipt(target_version="v1.1.0", run_id="earlier-run")

        def stop(*args, **kwargs):
            StateDir(self.root).write_journal({"kind": "apply", "target_version": "v1.2.0", "stage": "pulled",
                                               "run_id": "this-run", **CLI_IDENTITY})
            raise KeyboardInterrupt()

        self.Updater.return_value.apply.side_effect = stop
        code, _, err = self.main("apply")
        self.assertEqual(code, 130)
        cd = self.cd()
        self.assertEqual(err, f"bin/update: interrupted before the apply finished; the next `{cd}./bin/update apply` "
                              f"or `{cd}./bin/update rollback` recovers it first (`{cd}./bin/update status` shows where "
                              "it stopped)\n")
        self.assertNotIn(earlier, err)


class CliRound7OutputTests(unittest.TestCase):
    """Each output stream is made tolerant on its own: one that cannot be reconfigured does not stop the other."""

    setUp = MainTests.setUp
    patch = MainTests.patch

    def test_a_stdout_without_reconfigure_still_leaves_stderr_tolerant(self):
        self.Updater.return_value.apply.side_effect = Blocked("the release \u201cv1.2.0\u201d is blocked \u2026")
        stdout = io.StringIO()  # no reconfigure()
        stderr = io.TextIOWrapper(io.BytesIO(), encoding="latin-1", errors="strict")
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = cli.main(["--root", str(self.root), "--cwd", str(self.cwd), "apply"])
        stderr.flush()
        self.assertEqual(code, 3)
        self.assertEqual(stderr.errors, "backslashreplace")
        self.assertEqual(stderr.buffer.getvalue(),
                         b"bin/update: the release \\u201cv1.2.0\\u201d is blocked \\u2026\n")

    def test_the_fallback_note_reaches_stderr_before_verification_starts(self):
        # Verification can take minutes (or fail): the note is out first, even on a block-buffered stderr.
        self.fetched.note = CliMainAdditions.NOTE
        stderr = io.TextIOWrapper(io.BytesIO(), encoding="utf-8", line_buffering=False)
        seen = []

        def verify(fetched):
            seen.append(stderr.buffer.getvalue())
            return self.verification

        self.Verifier.return_value.verify.side_effect = verify
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(stderr):
            code = cli.main(["--root", str(self.root), "--cwd", str(self.cwd), "check"])
        self.assertEqual(code, 0)
        self.assertEqual(seen, [f"Note: {CliMainAdditions.NOTE}\n".encode("utf-8")])


class InstallRound7RunTests(TempRootTest):
    """Installation.run's default and messages, and reload_env (after a run writes the env file), which drops what
    check overlaid: Compose then sees the file's values."""

    def test_a_timeout_of_exactly_four_words_shows_them_all(self):
        inst = self.install("", runner=Runner(InstallRunAdditions.timing_out))
        with self.assertRaises(InstallError) as caught:
            inst.run(inst.compose_args("up", "-d"), timeout=60)
        self.assertEqual(str(caught.exception), "`docker compose up -d` timed out after 60s")
        with self.assertRaises(InstallError) as caught:
            inst.run(inst.compose_args("up", "-d", "app"), timeout=60)
        self.assertEqual(str(caught.exception), "`docker compose up -d ...` timed out after 60s")

    def test_run_raises_on_a_failure_unless_told_not_to(self):
        runner = Runner(lambda args, kwargs: completed(1, "", "Error response from daemon: pull access denied"))
        inst = self.install(runner=runner)
        with self.assertRaises(InstallError) as caught:
            inst.run(["docker", "compose", "pull"])
        self.assertEqual(str(caught.exception), "`docker compose pull` failed with exit code 1: Error response from "
                                                "daemon: pull access denied")
        self.assertEqual(inst.run(["docker", "compose", "pull"], check=False).returncode, 1)

    def test_docker_run_raises_on_a_failure_unless_told_not_to(self):
        # apply re-tags a rolled-back image with docker_run's default: a failed `docker tag` must stop it.
        runner = Runner(lambda args, kwargs: completed(1, "", "Error response from daemon: No such image: sha256:ab"))
        inst = self.install(runner=runner, docker=["docker", "--context", "fagi"])
        with self.assertRaises(InstallError) as caught:
            inst.docker_run("tag", "sha256:ab", "futureagi/standalone:v1.1.0")
        self.assertEqual(str(caught.exception), "`docker tag sha256:ab futureagi/standalone:v1.1.0` failed with exit "
                                                "code 1: Error response from daemon: No such image: sha256:ab")
        self.assertEqual(runner.calls[-1][0], ["docker", "--context", "fagi", "tag", "sha256:ab",
                                               "futureagi/standalone:v1.1.0"])
        self.assertEqual(inst.docker_run("tag", "sha256:ab", "x:y", check=False).returncode, 1)

    def test_a_failing_up_help_is_no_and_is_not_asked_again(self):
        runner = Runner(lambda args, kwargs: completed(1, UP_HELP_WITH_PULL, "unknown flag: --help"))
        inst = self.install("", runner=runner)
        self.assertIs(inst.supports_up_pull_never(), False)
        self.assertIs(inst.supports_up_pull_never(), False)
        self.assertEqual(runner.commands(), [["docker", "compose", "up", "--help"]])

    def test_a_docker_command_of_only_global_options_has_no_arguments(self):
        # What describe_failure and a timeout message split: nothing after the options is no IndexError.
        self.assertEqual(install.split_command(["docker", "--context", "colima"]), (["docker"], []))
        self.assertEqual(install.split_command(["docker"]), (["docker"], []))
        self.assertEqual(Installation.describe_failure(CommandResult(["docker", "--context", "colima"], 125, "", "")),
                         "`docker` failed with exit code 125 (output above)")

    def test_the_overlay_is_gone_after_a_reload(self):
        runner = Runner(docker_handler())
        inst = self.install("FUTURE_AGI_VERSION=v1.0.0\nFRONTEND_VERSION=v1.0.0\n", runner=runner)
        inst.overlay_env({"FUTURE_AGI_VERSION": "v0.9.0", "FRONTEND_VERSION": None})
        self.assertEqual({key: inst.subprocess_env()[key] for key in ("FUTURE_AGI_VERSION", "FRONTEND_VERSION")},
                         {"FUTURE_AGI_VERSION": "v0.9.0", "FRONTEND_VERSION": ""})
        self.write(".env", "FUTURE_AGI_VERSION=v1.2.0\nFRONTEND_VERSION=v1.2.0\n")
        inst.reload_env()
        self.assertEqual(inst.env, {"FUTURE_AGI_VERSION": "v1.2.0", "FRONTEND_VERSION": "v1.2.0"})
        inst.config()
        for args, kwargs in runner.calls:
            with self.subTest(args=args):
                self.assertNotIn("FUTURE_AGI_VERSION", kwargs["env"])
                self.assertNotIn("FRONTEND_VERSION", kwargs["env"])



class InstallRound7UnknownServicesTests(TempRootTest):
    """The refusal of a service list the JSON does not define names the Compose this run uses and five services."""

    def refusal(self, unknown, compose=COMPOSE):
        model = {"name": "futureagi", "services": {"app": {"image": "futureagi/standalone:v1.0.0"}}}

        def handle(args, kwargs):
            if "--services" in args:
                return completed(0, "".join(f"{name}\n" for name in ["app", *unknown]))
            return completed(0, json.dumps(model))

        inst = self.install("", runner=Runner(handle), compose=compose)
        with self.assertRaises(InstallError) as caught:
            inst.config()
        return str(caught.exception)

    def test_five_are_named_and_more_are_cut_short(self):
        rest = ", which its `config --format json` does not define, so bin/update cannot tell which services Compose " \
               "runs. Upgrade Docker Compose and run bin/update again"
        five = [f"ghost{n}" for n in range(1, 6)]
        self.assertEqual(self.refusal(five), f"`docker compose config --services` lists {', '.join(five)}{rest}")
        self.assertEqual(self.refusal([*five, "ghost6"]),
                         f"`docker compose config --services` lists {', '.join(five)} ...{rest}")
        self.assertEqual(self.refusal(["ghost1"], compose=["/opt/bin/docker-compose"]),
                         f"`docker-compose config --services` lists ghost1{rest}")



class InstallRound7BuildableTests(TempRootTest):
    """service_model's "buildable": a build section in either model counts, for an inactive service too."""

    def test_a_build_section_seen_by_only_one_model_makes_the_service_buildable(self):
        active = {"name": "futureagi", "services": {
            "app": {"image": "futureagi/standalone:v1.0.0"},
            "worker": {"image": "futureagi/future-agi:v1.0.0", "build": {"context": "."}}}}
        probed = {"name": "futureagi", "services": {
            "app": {"image": "futureagi/standalone:bin-update-probe-FUTURE_AGI_VERSION"},
            "serving": {"image": "futureagi/serving:bin-update-probe-SERVING_VERSION", "build": {"context": "serving"},
                        "profiles": ["ml"]}}}

        def handle(args, kwargs):
            if "--profiles" in args:
                return completed(0, "ml\n")
            if "--services" in args:
                return completed(0, "app\nworker\n")
            probe = kwargs["env"].get("FUTURE_AGI_VERSION", "").startswith("bin-update-probe-")
            return completed(0, json.dumps(probed if probe else active))

        model = self.install("", runner=Runner(handle)).service_model()
        self.assertFalse(model["complete"])  # the probe did not see worker
        self.assertEqual({name: (entry["active"], entry["buildable"]) for name, entry in model["services"].items()},
                         {"app": (True, False), "worker": (True, True), "serving": (False, True)})



class InstallRound7FutureAgiServicesTests(TempRootTest):
    """futureagi_services looks at every service: one without an image, or with an image it cannot parse, is skipped
    and the services after it (in name order) are still found."""

    def test_services_after_one_without_an_image_or_with_an_odd_one_are_found(self):
        config = {"name": "futureagi", "services": {
            "a-builder": {"build": {"context": "."}},
            "app": {"image": "futureagi/standalone:v1.0.0"},
            "b-odd": {"image": "Not A Reference!"},
            "c-third-party": {"image": "redis:7"},
            "frontend": {"image": "futureagi/frontend:v1.0.0", "build": {"context": "frontend"}}}}
        inst = self.install("", runner=Runner(docker_handler(config)))
        self.assertEqual([(entry["service"], entry["component"], entry["buildable"])
                          for entry in inst.futureagi_services()],
                         [("app", "standalone", False), ("frontend", "frontend", True)])


# ================================================================ round 8 ==

class CliRound8StaleWorkdirProbeTests(unittest.TestCase):
    """The verify-<pid> sweep asks only about the processes its directories name: never pid 0 (kill(0, ...)
    addresses this whole process group) and never this run itself."""

    setUp = MainTests.setUp
    patch = MainTests.patch
    main = MainTests.main
    dead_pid = MainTests.dead_pid

    def test_pid_0_and_its_own_pid_are_never_probed(self):
        state = self.root / STATE_DIR
        state.mkdir()
        dead = self.dead_pid()
        group = [state / "verify-0", state / "verify-00"]
        for directory in [*group, state / f"verify-{dead}", self.workdir]:
            directory.mkdir()
            (directory / "manifest.json").write_text("stale")
        probed = []
        real_kill = os.kill

        def kill(pid, sig):
            probed.append((pid, sig))
            return real_kill(pid, sig)

        for command in ("check", "apply"):
            with self.subTest(command=command):
                del probed[:]
                with mock.patch.object(cli.os, "kill", side_effect=kill):
                    self.assertEqual(self.main(command)[0], 0)
                self.assertEqual(probed, [(dead, 0)] if command == "check" else [])
                self.assertEqual([(directory / "manifest.json").read_text() for directory in group], ["stale"] * 2)
                self.assertFalse((state / f"verify-{dead}").exists())
                self.assertFalse(self.workdir.exists())


@unittest.skipIf(shutil.which("bash") is None, "bin/update needs bash")
class CliRound8BinUpdateLoaderTests(unittest.TestCase):
    """The rest of the loader's paths: links met inside linked directories, a linked checkout, a link found on
    PATH, names that start with a dash, each bash on this host, and the interpreters it does not accept."""

    setUp = BinUpdateScriptTests.setUp
    run_update = BinUpdateScriptTests.run_update
    assertRunsTheCheckout = CliRound7BinUpdateSymlinkTests.assertRunsTheCheckout

    def link_farm(self):
        """opt/tools -> "real links", whose "fagi update" -> ../hop/update -> ../check out/bin/update.

        Both links are relative: the kernel reads each from the directory that holds it. The second link is met
        under "real links/..", which is the base directory, while opt/tools/.. is opt, which holds decoys: a hop
        directory and a "check out" with no bin/update run in it.
        """

        real = self.base / "real links"
        real.mkdir()
        (self.base / "opt").mkdir()
        (self.base / "opt" / "tools").symlink_to(real, target_is_directory=True)
        (self.base / "hop").mkdir()
        (self.base / "hop" / "update").symlink_to(Path("..") / "check out" / "bin" / "update")
        (real / "fagi update").symlink_to(Path("..") / "hop" / "update")
        (self.base / "opt" / "hop").mkdir()
        (self.base / "opt" / "check out" / "bin").mkdir(parents=True)
        return self.base / "opt" / "tools" / "fagi update"

    def test_a_relative_link_met_inside_a_linked_directory_is_read_where_the_link_really_is(self):
        self.assertRunsTheCheckout(self.link_farm())
        self.assertRunsTheCheckout(Path("opt") / "tools" / "fagi update", cwd=self.base)

    def test_a_linked_checkout(self):
        (self.base / "fagi").symlink_to(self.checkout, target_is_directory=True)
        (self.base / "links").mkdir()
        (self.base / "links" / "fagi rel").symlink_to(Path("..") / "check out", target_is_directory=True)
        for checkout in (self.base / "fagi", self.base / "links" / "fagi rel"):
            with self.subTest(checkout=checkout.name):
                self.assertRunsTheCheckout(checkout / "bin" / "update")
                self.assertRunsTheCheckout(Path("bin") / "update", cwd=checkout)
                result = self.run_update("--help", script=checkout / "bin" / "update")
                self.assertEqual((result.returncode, result.stderr), (0, ""))
                self.assertIn("usage: bin/update", result.stdout)

    def test_a_link_found_on_path(self):
        # Installed as a command: bash is given the path PATH found, here through a relative link.
        links = self.base / "usr local bin"
        links.mkdir()
        (links / "fagi-update").symlink_to(Path("..") / "check out" / "bin" / "update")
        env = dict(self.env, PATH=str(links) + os.pathsep + self.env.get("PATH", os.defpath))
        self.assertRunsTheCheckout("fagi-update", env=env)
        self.assertRunsTheCheckout("fagi-update", env=env, cwd=self.checkout / "bin")

    def test_names_that_start_with_a_dash(self):
        # `bash -- -co/bin/update`: a path that starts with a dash reaches dirname, readlink and cd as an operand,
        # never as an option.
        (self.base / "-co").symlink_to(self.checkout, target_is_directory=True)
        (self.base / "-links").mkdir()
        (self.base / "-links" / "-fagi").symlink_to(Path("..") / "-co" / "bin" / "update")
        for script in ("-co/bin/update", "-links/-fagi"):
            with self.subTest(script=script):
                result = subprocess.run([shutil.which("bash"), "--", script, "status"], cwd=str(self.base),
                                        env=self.env, capture_output=True, text=True, timeout=120)
                self.assertEqual((result.returncode, result.stdout), (2, ""), result.stderr)
                self.assertEqual(result.stderr, f"bin/update: no .env in {os.path.realpath(self.checkout)}: run "
                                                "bin/update from the checkout ./bin/install set up\n")

    def test_each_bash_on_this_host_finds_the_checkout(self):
        # macOS keeps bash 3.2 at /bin/bash; the bash on PATH may be another.
        shells = sorted({os.path.realpath(path) for path in ("/bin/bash", "/usr/bin/bash", shutil.which("bash"))
                         if path and os.access(path, os.X_OK)})
        (self.base / "fagi-bin").symlink_to(self.checkout / "bin", target_is_directory=True)
        (self.base / "abs").mkdir()
        (self.base / "abs" / "fagi-update").symlink_to(self.checkout / "bin" / "update")
        scripts = [self.checkout / "bin" / "update", self.base / "fagi-bin" / "update",
                   self.base / "abs" / "fagi-update", self.link_farm()]
        for shell in shells:
            for script in scripts:
                with self.subTest(shell=shell, script=str(script.relative_to(self.base))):
                    result = subprocess.run([shell, str(script), "status"], cwd=str(self.caller), env=self.env,
                                            capture_output=True, text=True, timeout=120)
                    self.assertEqual((result.returncode, result.stdout), (2, ""), result.stderr)
                    self.assertEqual(result.stderr, f"bin/update: no .env in {os.path.realpath(self.checkout)}: "
                                                    "run bin/update from the checkout ./bin/install set up\n")

    def test_a_python_that_is_a_directory_or_not_executable_is_not_found(self):
        not_executable = self.base / "python3 copy"
        not_executable.write_text(f"#!/bin/sh\nexec '{sys.executable}' \"$@\"\n")  # mode 0644: not executable
        for python in (not_executable, self.base / "python dir"):
            with self.subTest(python=python.name):
                if python.name == "python dir":
                    python.mkdir()
                result = self.run_update("status", env=dict(self.env, PYTHON=str(python)))
                self.assertEqual((result.returncode, result.stdout), (2, ""))
                self.assertEqual(result.stderr, f"bin/update: needs Python 3.9 or newer on the Docker host ({python} "
                                                "was not found)\n")

    def test_a_python_named_without_a_slash_is_found_on_path_not_in_a_directory(self):
        shim = self.base / "shim dir"
        shim.mkdir()
        (shim / "fagi-python").symlink_to(sys.executable)
        for directory in (self.caller, self.checkout):  # never run from where bin/update was started, or from ROOT
            executable(directory / "fagi-python", f"#!/bin/sh\n: > '{self.marker}'\nexit 99\n")
        env = dict(self.env, PYTHON="fagi-python", PATH=str(shim) + os.pathsep + self.env.get("PATH", os.defpath))
        for cwd in (self.caller, self.checkout):
            with self.subTest(cwd=cwd.name):
                self.assertRunsTheCheckout(self.checkout / "bin" / "update", env=env, cwd=cwd)
                self.assertFalse(self.marker.exists())

    def test_python3_found_through_a_relative_path_entry_is_the_one_that_runs(self):
        # PATH=venv/bin:... (or an empty entry, the current directory) names directories in the caller's
        # directory; Python then runs from the checkout, whose files of the same names must never run.
        impostor = f"#!/bin/sh\n: > '{self.marker}'\nexit 99\n"
        system_path = self.env.get("PATH", os.defpath)
        env = {key: value for key, value in self.env.items() if key != "PYTHON"}
        for entry, directory in (("py bin", Path("py bin")), ("", Path("."))):
            with self.subTest(entry=entry):
                (self.caller / directory).mkdir(exist_ok=True)
                (self.caller / directory / "python3").symlink_to(sys.executable)
                executable(self.checkout / directory / "python3", impostor)
                self.assertRunsTheCheckout(self.checkout / "bin" / "update",
                                           env=dict(env, PATH=entry + os.pathsep + system_path))
                self.assertFalse(self.marker.exists())
                (self.caller / directory / "python3").unlink()
                # Only the checkout has one there: python3 is found further down PATH, and that one runs, not the
                # checkout's file that a second lookup from the checkout would find first.
                self.assertRunsTheCheckout(self.checkout / "bin" / "update",
                                           env=dict(env, PATH=entry + os.pathsep + system_path))
                self.assertFalse(self.marker.exists())
                (self.checkout / directory / "python3").unlink()

    def test_a_python_whose_name_starts_with_a_dash_is_looked_up_as_a_name(self):
        shim = self.base / "shim dir"
        shim.mkdir()
        (shim / "-fagi-python").symlink_to(sys.executable)
        env = dict(self.env, PYTHON="-fagi-python", PATH=str(shim) + os.pathsep + self.env.get("PATH", os.defpath))
        self.assertRunsTheCheckout(self.checkout / "bin" / "update", env=env)
        result = self.run_update("status", env=dict(env, PYTHON="-no-such-python"))
        self.assertEqual((result.returncode, result.stdout, result.stderr),
                         (2, "", "bin/update: needs Python 3.9 or newer on the Docker host (-no-such-python was not "
                                 "found)\n"))

    def test_relative_paths_start_in_the_callers_directory_reached_through_a_link(self):
        # A shell in a linked directory exports PWD as the link; bin/update passes it on as --cwd.
        (self.checkout / DISTRIBUTED_FILE).write_text("services: {}\n")
        (self.checkout / PRODUCTION_OVERLAY).write_text("services: {}\n")
        caller = self.base / "caller link"
        caller.symlink_to(self.caller, target_is_directory=True)
        result = self.run_update("--production", "--env-file", "prod.env", "status", cwd=caller,
                                 env=dict(self.env, PWD=str(caller)))
        self.assertEqual((result.returncode, result.stdout), (2, ""), result.stderr)
        self.assertTrue(result.stderr.startswith(f"bin/update: {os.path.realpath(self.caller)}/prod.env does not "
                                                 "exist."), result.stderr)


# ================================================= checkout ownership ====


def ownership_labels(root, *, files=None, env_file=None):
    """Compose v2 provenance: absolute paths, comma-separated files; implicit .env has no env-file label."""

    labels = {"com.docker.compose.project.working_dir": str(root),
              "com.docker.compose.project.config_files": ",".join(str(p) for p in
                  (files if files is not None else [root / STANDALONE_FILE]))}
    if env_file is not None:
        labels["com.docker.compose.project.environment_file"] = str(env_file)
    return labels


class CheckoutOwnershipTests(TempRootTest):
    def setUp(self):
        super().setUp()
        self.write(".env", "FUTURE_AGI_VERSION=v1.0.0\n")
        self.write(STANDALONE_FILE, "services: {}\n")
        self.other = self.root / "other checkout"
        self.other.mkdir()
        (self.other / STANDALONE_FILE).write_text("services: {}\n")
        (self.other / ".env").write_bytes((self.root / ".env").read_bytes())

    def record(self, service="app", *, owner=None, **kwargs):
        record = container_record(service, **kwargs)
        record["Config"]["Labels"].update(ownership_labels(owner or self.root))
        return record

    def installation(self, records, **kwargs):
        config = {"name": "futureagi", "services": {"app": {"image": "futureagi/standalone:v1.0.0"}}}
        runner = Runner(docker_handler(config=config, containers=records, add_provenance=False))
        inst = Installation(self.root, environ={}, compose=COMPOSE, runner=runner, **kwargs)
        return inst, runner

    def assertRefused(self, records, *needles, **kwargs):
        inst, runner = self.installation(records, **kwargs)
        before = {p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        with self.assertRaises(InstallError) as caught:
            inst.containers()
        message = str(caught.exception)
        self.assertIn("ownership", message)
        for needle in needles:
            self.assertIn(needle, message)
        self.assertEqual({p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()}, before)
        self.assertFalse(any("up" in cmd or "pull" in cmd for cmd in runner.commands()))
        return message

    def test_unset_export_selecting_another_checkout_is_not_an_install_here(self):
        # Real Compose's name fallback is exercised below; this is the actual inspect result it selects.
        record = self.record(owner=self.other)
        message = self.assertRefused([record], "futureagi", str(self.other), str(self.root), "COMPOSE_PROJECT_NAME")
        self.assertIn("persist", message)
        self.assertIn("--env-file", message)

    def test_same_owner_is_accepted_and_all_containers_remain_visible(self):
        records = [self.record(), self.record("postgres", status="exited")]
        inst, _ = self.installation(records)
        self.assertEqual([(c["id"], c["status"]) for c in inst.containers()],
                         [(r["Id"], r["State"]["Status"]) for r in records])
        self.assertEqual(inst.check_stack(inst.containers()), [])
        inst.check_ownership()

    def test_symlink_equivalent_checkout_files_and_env_are_the_same_owner(self):
        alias = self.root / "checkout link"
        alias.symlink_to(self.root, target_is_directory=True)
        record = self.record(owner=alias)
        record["Config"]["Labels"].update(ownership_labels(alias, env_file=alias / ".env"))
        inst, _ = self.installation([record])
        self.assertEqual([c["id"] for c in inst.containers()], [record["Id"]])
        inst.check_ownership()

    def test_conflicting_owners_are_not_filtered_even_for_stopped_or_oneoff_containers(self):
        for state in ("running", "exited", "created", "restarting"):
            for oneoff in ("True", "False"):
                with self.subTest(state=state, oneoff=oneoff):
                    foreign = self.record("postgres-schema-bootstrap", owner=self.other, status=state)
                    foreign["Config"]["Labels"]["com.docker.compose.oneoff"] = oneoff
                    self.assertRefused([self.record(), foreign], foreign["Id"][:12])
                    self.assertRefused([foreign, self.record()], foreign["Id"][:12])

    def test_same_owner_init_jobs_are_checked_but_not_used_as_stack_versions(self):
        job = self.record("app", cid="b" * 64, image="futureagi/standalone:v9.0.0", status="exited")
        job["Config"]["Labels"]["com.docker.compose.oneoff"] = "True"
        inst, _ = self.installation([self.record(), job])
        self.assertTrue(inst.containers()[1]["oneoff"])
        self.assertEqual(inst.check_stack(inst.containers()), [])
        inst.check_ownership()

    def test_missing_or_malformed_labels_never_establish_ownership(self):
        for key in ("com.docker.compose.project", "com.docker.compose.project.working_dir",
                    "com.docker.compose.project.config_files"):
            for value in (None, "", [], {}, 3, "relative/path", "bad\x00path", "\n"):
                with self.subTest(key=key, value=value):
                    record = self.record()
                    if value is None:
                        del record["Config"]["Labels"][key]
                    else:
                        record["Config"]["Labels"][key] = value
                    self.assertRefused([record])
        for labels in (None, [], "malformed", 4):
            with self.subTest(labels=labels):
                record = self.record()
                record["Config"]["Labels"] = labels
                self.assertRefused([record])

    def test_matching_workdir_cannot_hide_foreign_missing_or_conflicting_config_files(self):
        override = self.write("docker-compose.override.yml", "services: {}\n")
        for value in (str(self.other / STANDALONE_FILE), str(self.root / "gone.yml"), str(self.root),
                      str(self.root / STANDALONE_FILE) + ",", "," + str(self.root / STANDALONE_FILE)):
            with self.subTest(files=value):
                record = self.record()
                record["Config"]["Labels"]["com.docker.compose.project.config_files"] = value
                self.assertRefused([record])
        # Two otherwise local owners disagree about the files used to create this project.
        record = self.record("postgres")
        record["Config"]["Labels"].update(ownership_labels(self.root, files=[self.root / STANDALONE_FILE, override]))
        self.assertRefused([self.record(), record], "config_files")

    def test_symlink_to_foreign_config_is_not_local_provenance(self):
        linked = self.root / "foreign.yml"
        linked.symlink_to(self.other / STANDALONE_FILE)
        record = self.record()
        record["Config"]["Labels"].update(ownership_labels(self.root, files=[linked]))
        self.assertRefused([record], "config_files")

    def test_missing_or_relocated_paths_require_manual_recovery_not_adoption(self):
        record = self.record(owner=self.root / "old location")
        message = self.assertRefused([record], "working_dir")
        self.assertIn("restore", message.lower())
        self.assertIn("manual", message.lower())
        # Restoring the original path as an operator-controlled symlink to this same checkout proves equivalence.
        (self.root / "old location").symlink_to(self.root, target_is_directory=True)
        inst, _ = self.installation([record])
        inst.check_ownership()

    def test_production_requires_its_actual_env_file_and_accepts_a_custom_file(self):
        self.production_files()
        custom = self.write("secrets/prod custom.env", "FUTURE_AGI_VERSION=v1.0.0\n")
        for env_file in (self.root / PRODUCTION_ENV, custom):
            with self.subTest(env_file=env_file):
                record = self.record("backend")
                record["Config"]["Labels"].update(ownership_labels(
                    self.root, files=[self.root / DISTRIBUTED_FILE, self.root / PRODUCTION_OVERLAY], env_file=env_file))
                inst, _ = self.installation([record], production=True, env_file=env_file)
                inst.check_ownership()
                for value in (None, "", str(self.root / ".env"), str(self.root / "missing.env"),
                              str(env_file) + "," + str(self.root / ".env")):
                    with self.subTest(label=value):
                        bad = json.loads(json.dumps(record))
                        labels = bad["Config"]["Labels"]
                        if value is None:
                            labels.pop("com.docker.compose.project.environment_file")
                        else:
                            labels["com.docker.compose.project.environment_file"] = value
                        self.assertRefused([bad], "environment_file", production=True, env_file=env_file)

    def test_explicitly_selected_project_is_valid_only_with_matching_owner(self):
        self.write(".env", "FUTURE_AGI_VERSION=v1.0.0\nCOMPOSE_PROJECT_NAME=fagi-b\n")
        record = self.record(project="fagi-b")
        runner = Runner(docker_handler(config={"name": "fagi-b", "services": {}}, containers=[record], add_provenance=False))
        inst = Installation(self.root, environ={"COMPOSE_PROJECT_NAME": "fagi-b"}, compose=COMPOSE, runner=runner)
        inst.check_ownership()
        self.assertTrue(any("label=com.docker.compose.project=fagi-b" in cmd for cmd in runner.commands()))
        record["Config"]["Labels"].update(ownership_labels(self.other))
        with self.assertRaisesRegex(InstallError, "ownership"):
            inst.check_ownership()  # not cached after a successful check

    def test_no_container_proof_refuses_mutation_but_config_and_status_still_work(self):
        inst, runner = self.installation([])
        self.assertEqual(inst.project, "futureagi")
        self.assertEqual(inst.containers(), [])
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertIsNone(cli._status(inst, StateDir(self.root)))
        self.assertIn("Last run      none", out.getvalue())
        with self.assertRaisesRegex(InstallError, "ownership"):
            inst.check_ownership()
        self.assertFalse((self.root / STATE_DIR).exists())

    def test_compose_mutation_rechecks_ownership_and_never_runs_for_a_foreign_project(self):
        record = self.record()
        inst, runner = self.installation([record])
        inst.check_ownership()
        record["Config"]["Labels"].update(ownership_labels(self.other))
        for args in (("up", "-d"), ("pull", "app")):
            with self.subTest(args=args):
                with self.assertRaisesRegex(InstallError, "ownership"):
                    inst.compose_run(*args, check=False)
                self.assertNotIn([*COMPOSE, *args], runner.commands())
        inst.compose_run("logs", "--tail", "10", "app", check=False)
        self.assertIn([*COMPOSE, "logs", "--tail", "10", "app"], runner.commands())


class CliCheckoutOwnershipTests(unittest.TestCase):
    """Real Installation/CLI/Updater/state, with only inspect/config/health/signature I/O faked."""

    OLD, NEW, NEWER = FullStackProductionTests.OLD, FullStackProductionTests.NEW, FullStackProductionTests.NEWER
    REPOS = FullStackProductionTests.REPOS
    manifest = FullStackProductionTests.manifest
    settings = FullStackProductionTests.settings
    paste = FullStackProductionTests.paste
    versions = FullStackProductionTests.versions
    receipts = FullStackProductionTests.receipts
    receipt_paths = FullStackProductionTests.receipt_paths
    backups = FullStackProductionTests.backups
    stage = FullStackProductionTests.stage

    def setUp(self):
        FullStackProductionTests.setUp(self)
        self.owner = self.root / "another checkout"
        (self.owner / "deploy").mkdir(parents=True)
        for path in (DISTRIBUTED_FILE, PRODUCTION_OVERLAY, PRODUCTION_ENV):
            (self.owner / path).write_bytes((self.root / path).read_bytes())
        self.set_owner(self.root)

    def set_owner(self, root):
        for record in self.engine.records:
            record["Config"]["Labels"].update(ownership_labels(
                root, files=[root / DISTRIBUTED_FILE, root / PRODUCTION_OVERLAY], env_file=root / PRODUCTION_ENV))

    def snapshot(self):
        state = StateDir(self.root)
        return {"env": self.env_file.read_bytes(), "foreign_env": (self.owner / PRODUCTION_ENV).read_bytes(),
                "containers": json.loads(json.dumps(self.engine.records)),
                "journal": state.journal_path.read_bytes() if state.journal_path.exists() else None,
                "receipts": [(p.name, p.read_bytes()) for p in state.receipts()],
                "backup": self.backup_log.read_bytes() if self.backup_log.exists() else None,
                "up": list(self.engine.up_calls())}

    def refused(self, command):
        before = self.snapshot()
        calls = len(self.engine.calls)
        code, out, err = self.paste(command)
        self.assertEqual(code, 2, out + err)
        self.assertIn("ownership", err)
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(any(install.split_command(c)[1][:1] in (["pull"], ["up"], ["tag"])
                             for c in self.engine.calls[calls:]))
        return out, err

    def test_fresh_apply_and_stage_cannot_use_another_checkouts_project(self):
        self.set_owner(self.owner)
        for command in ("check", "apply", "apply --stage", "apply --dry-run", "rollback --yes"):
            with self.subTest(command=command):
                self.refused("./bin/update --production " + command)

    def test_staged_finish_and_quiet_rollback_cannot_use_a_foreign_owner(self):
        self.stage()
        self.set_owner(self.owner)
        for command in ("apply", "apply --stage", "rollback --yes", "rollback"):
            with self.subTest(command=command):
                self.refused("./bin/update --production " + command)

    def test_recovery_leaves_env_journal_and_receipts_untouched_for_foreign_owner(self):
        self.stage()
        state = StateDir(self.root)
        state.write_journal(dict(kind="apply", stage="env_written", target_version=self.NEW,
                                 project=self.engine.PROJECT, mode="production", production=True,
                                 env_file=PRODUCTION_ENV, found_values={"FUTURE_AGI_VERSION": self.OLD},
                                 run_id="ownership-interrupted"))
        self.set_owner(self.owner)
        for command in ("apply", "apply --dry-run", "rollback", "rollback --yes"):
            with self.subTest(command=command):
                self.refused("./bin/update --production " + command)

    def test_status_reports_ownership_problem_and_keeps_local_pending_state_visible(self):
        self.stage()
        self.set_owner(self.owner)
        before = self.snapshot()
        code, out, err = self.paste("./bin/update --production status")
        self.assertEqual(code, 2, out + err)
        self.assertIn("ownership", out + err)
        self.assertIn("running: unknown", out)
        self.assertIn("Last run      apply", out)
        self.assertIn("staged", out)
        self.assertIn("FUTURE_AGI_VERSION=" + self.NEW, out)
        self.assertEqual(self.snapshot(), before)

    def test_same_owner_can_stage_finish_and_rollback(self):
        self.stage()
        for command, version in (("apply --acknowledge-stale-stage", self.NEW), ("rollback --yes", self.OLD)):
            code, out, err = self.paste("./bin/update --production " + command)
            self.assertEqual(code, 0, out + err)
            self.assertEqual(self.versions(), (version, version))
            self.assertEqual(self.engine.running()["backend"], version)
        self.assertEqual(self.backups(), 1)
        self.assertEqual([r["status"] for r in self.receipts()], ["staged", "succeeded", "rolled_back"])


if __name__ == "__main__":
    unittest.main()
