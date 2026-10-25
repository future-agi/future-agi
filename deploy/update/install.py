"""The installation bin/update acts on, and how to run Docker Compose for it.

Compose runs exactly the way the rest of the repository runs it:
- Standalone and Distributed: `docker compose` in the checkout with no -f and
  no --env-file, like bin/install, so Compose itself reads .env, COMPOSE_FILE,
  COMPOSE_PROFILES and docker-compose.override.yml.
- Production: `docker compose --env-file deploy/.env.production
  -f docker-compose.distributed.yml -f deploy/docker-compose.production.yml`,
  like deploy/setup.sh. bin/update's own commands take every key from the env
  file (subprocess_env drops the shell's exports); a Compose command it
  prints for the operator to run is followed by shell_rule().
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Collection, Dict, List, Mapping, Optional, Sequence, Tuple

from .envfile import read_env
from .manifest import ImageRef, ManifestError, parse_image_ref

STANDALONE_FILE = "docker-compose.yml"
DISTRIBUTED_FILE = "docker-compose.distributed.yml"
PRODUCTION_OVERLAY = "deploy/docker-compose.production.yml"
PRODUCTION_ENV = "deploy/.env.production"

# deploy/setup.sh's SECRET_NAMES: never let a shell export shadow them in
# production (tests keep this list equal to setup.sh's).
SETUP_SECRET_NAMES = (
    "SECRET_KEY", "AGENTCC_INTERNAL_API_KEY", "AGENTCC_ADMIN_TOKEN", "PG_PASSWORD",
    "MINIO_ROOT_PASSWORD", "PROPERTY_CATALOG_API_PASSWORD",
    "PROPERTY_CATALOG_CONSUMER_PASSWORD", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GOOGLE_API_KEY",
    "INTEGRATION_ENCRYPTION_KEY", "EE_LICENSE_KEY", "RECAPTCHA_SECRET_KEY", "MAILGUN_API_KEY",
    "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "FUTURE_AGI_CLOUD_API_KEY",
    "GOOGLE_APPLICATION_CREDENTIALS",
)

# Image repository path -> manifest component. serving's -gpu tags map to
# serving_gpu (see component_for).
REPOSITORY_COMPONENTS = {
    "futureagi/standalone": "standalone",
    "futureagi/future-agi": "backend",
    "futureagi/frontend": "frontend",
    "futureagi/fi-collector": "fi_collector",
    "futureagi/agentcc-gateway": "agentcc_gateway",
    "futureagi/serving": "serving",
    "futureagi/code-executor": "code_executor",
    "futureagi/future-agi-simulation-runner": "simulation_runner",
}
# Manifest component -> the .env key that selects its tag.
COMPONENT_VARIABLES = {
    "standalone": "FUTURE_AGI_VERSION",
    "backend": "FUTURE_AGI_VERSION",
    "frontend": "FRONTEND_VERSION",
    "fi_collector": "FI_COLLECTOR_VERSION",
    "agentcc_gateway": "AGENTCC_GATEWAY_VERSION",
    "serving": "SERVING_VERSION",
    "serving_gpu": "SERVING_VERSION",
    "code_executor": "CODE_EXECUTOR_VERSION",
    "simulation_runner": "SIMULATION_RUNNER_VERSION",
}
VERSION_VARIABLES = tuple(sorted(set(COMPONENT_VARIABLES.values())))
# bin/install's markers of each stack (DISTRIBUTED_ONLY_SERVICES and `app`).
DISTRIBUTED_ONLY_SERVICES = ("backend", "worker", "frontend", "minio", "redis", "rabbitmq", "temporal",
                             "fi-collector", "agentcc-gateway", "peerdb-server")
STANDALONE_ONLY_SERVICES = ("app",)

# Options that take the next argument as their value, so a failure message
# can leave out both. Global options come before the subcommand: there `-f`
# is a Compose file, after a docker subcommand it is --filter or --format.
COMPOSE_GLOBAL_VALUE_OPTIONS = frozenset((
    "-f", "--file", "-p", "--project-name", "--env-file", "--profile", "--project-directory", "--ansi",
    "--progress", "--parallel"))
DOCKER_GLOBAL_VALUE_OPTIONS = frozenset((
    "-c", "--context", "-H", "--host", "--config", "-l", "--log-level", "--tlscacert", "--tlscert", "--tlskey"))
_COMMAND_VALUE_OPTIONS = (
    "--wait-timeout", "--timeout", "--tail", "--since", "--until", "--format", "--filter", "--output", "--hash",
    "--policy", "--pull", "--scale", "--exit-code-from", "--attach", "--no-attach", "--status", "--type",
    "--platform", "--last", "--index", "--name", "--entrypoint", "-e", "--env", "-u", "--user", "-w", "--workdir")
COMPOSE_COMMAND_VALUE_OPTIONS = frozenset(_COMMAND_VALUE_OPTIONS + ("-n",))
DOCKER_COMMAND_VALUE_OPTIONS = frozenset(_COMMAND_VALUE_OPTIONS + ("-f", "-n"))
# What the Docker CLI and Compose print when they cannot reach the engine.
DAEMON_UNREACHABLE = re.compile(
    r"cannot connect to the docker daemon|failed to connect to the docker api|is the docker daemon running"
    r"|context \"[^\"]*\":? (?:context )?(?:not found|does not exist)", re.IGNORECASE)
DAEMON_HINT = "Docker is not running or not reachable (check DOCKER_HOST and docker context ls)"
# `<compose> up --help` lists this option when `up --pull never` works: it
# overrides every service's pull_policy, so `up` only starts images already
# pulled (and verified).
UP_PULL_OPTION = re.compile(r"^\s*(?:-\w,\s+)?--pull(?![\w-])", re.MULTILINE)
# Installation.service_model sets each version key to PROBE_TAG_PREFIX + key:
# an image whose tag then starts with it takes its tag from that key.
PROBE_TAG_PREFIX = "bin-update-probe-"
# A Compose profile name (the compose-spec pattern); `config --profiles` prints one per line.
PROFILE_NAME = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.-]*")
# What `<compose> version --short` prints: 2.24.7, v2.29.1, 2.29.1-desktop.1, 5.1.4.
COMPOSE_VERSION = re.compile(r"v?(\d+)\.(\d+)(?:\.(\d+))?")
# Compose releases bin/update cannot work with, and what each one gets told.
# v2.25.0's `config` has no --format (it prints its usage; -o writes YAML).
COMPOSE_REFUSED = {
    (2, 25, 0): ("Docker Compose v2.25.0 cannot print its configuration as JSON, which bin/update needs (fixed in "
                 "v2.26.0). Upgrade Docker Compose and run bin/update again"),
}
# Added to a failed `config` call on a release that rejects valid compose files
# (v2.24.1: "services.worker-tasks-s.environment array items[0,131] must be
# unique" for the production overlay, which deploy/setup.sh hits too).
COMPOSE_CONFIG_HINTS = {
    (2, 24, 1): "Docker Compose v2.24.1 rejects valid compose files (fixed in v2.24.2): upgrade Docker Compose",
}


class InstallError(RuntimeError):
    """The installation is missing, ambiguous, or not in a state bin/update handles."""


@dataclass
class CommandResult:
    args: List[str]
    returncode: int
    stdout: str
    stderr: str


def component_for(ref: ImageRef) -> Optional[str]:
    component = REPOSITORY_COMPONENTS.get(ref.repository)
    if component == "serving" and ref.tag and ref.tag.endswith("-gpu"):
        return "serving_gpu"
    return component


def probe_variable(image: str) -> Optional[str]:
    """The version key whose probe value (PROBE_TAG_PREFIX + key) starts this image's tag, or None.

    `futureagi/serving:bin-update-probe-SERVING_VERSION-gpu` gives
    SERVING_VERSION. An image with no tag, or a tag that does not start with
    a probe value, gives None: its tag is written in a Compose file.
    """

    last = image.split("@", 1)[0].rsplit("/", 1)[-1]
    if ":" not in last:
        return None
    tag = last.rsplit(":", 1)[1]
    # The longest key first, so a key that starts another key's name never wins.
    for key in sorted(VERSION_VARIABLES, key=len, reverse=True):
        if tag.startswith(PROBE_TAG_PREFIX + key):
            return key
    return None


def _skip_options(args: Sequence[str], start: int, takes_value: Collection[str]) -> int:
    """Index of the first positional argument at or after `start`."""

    index = start
    while index < len(args) and args[index].startswith("-") and args[index] != "-":
        if args[index] == "--":
            return index + 1
        index += 2 if args[index] in takes_value else 1
    return index


def split_command(args: Sequence[str]) -> Tuple[List[str], List[str]]:
    """(the program, as the operator would type it; the arguments after its global options).

    ["docker", "compose", "--env-file", X, "-f", Y, "up", "-d"] gives
    (["docker", "compose"], ["up", "-d"]); a standalone Compose binary or the
    Docker CLI is named by its basename.
    """

    if not args:
        return [], []
    name = os.path.basename(args[0])
    start = _skip_options(args, 1, DOCKER_GLOBAL_VALUE_OPTIONS if name == "docker" else COMPOSE_GLOBAL_VALUE_OPTIONS)
    if name == "docker" and start < len(args) and args[start] == "compose":
        return ["docker", "compose"], list(args[_skip_options(args, start + 1, COMPOSE_GLOBAL_VALUE_OPTIONS):])
    return [name], list(args[start:])


def parse_compose_version(text: str) -> Optional[Tuple[int, int, int]]:
    """(major, minor, patch) of what `<compose> version --short` printed, or None when it names no version."""

    match = COMPOSE_VERSION.match((text or "").strip())
    if not match:
        return None
    return int(match.group(1)), int(match.group(2)), int(match.group(3) or 0)


class ComposeCommand(list):
    """The words that run Docker Compose, and the version it reported (`version`: None when unknown)."""

    def __init__(self, words: Sequence[str], version: Optional[Tuple[int, ...]] = None) -> None:
        super().__init__(words)
        self.version = version


def resolve_compose_command(runner=subprocess.run, which=shutil.which) -> List[str]:
    """`docker compose` (v2 plugin), else a v2+ standalone docker-compose.

    A ComposeCommand: its `version` is what `version --short` printed
    (Installation.compose_version).
    """

    if which("docker"):
        probe = runner(["docker", "compose", "version", "--short"], capture_output=True, text=True, check=False)
        if probe.returncode == 0:
            return ComposeCommand(["docker", "compose"], parse_compose_version(probe.stdout or ""))
    standalone = which("docker-compose")
    if standalone:
        probe = runner([standalone, "version", "--short"], capture_output=True, text=True, check=False)
        version = (probe.stdout or "").strip().lstrip("v")
        if probe.returncode == 0 and re.match(r"^([2-9]|\d{2,})\.", version):
            return ComposeCommand([standalone], parse_compose_version(version))
        # The v2.32.3 release binaries report a commit (`083f676`) as their version; Compose v1 printed 1.x.y.
        if probe.returncode == 0 and re.fullmatch(r"[0-9a-f]{7,40}", version):
            return ComposeCommand([standalone], None)
        raise InstallError(f"docker-compose {version or '(unknown version)'} is too old: bin/update needs Docker Compose v2")
    if not which("docker"):
        raise InstallError("docker is not installed or not on PATH")
    raise InstallError("Docker Compose v2 is not available (neither `docker compose` nor `docker-compose`)")


class Installation:
    """One Future AGI install: its mode, env file and Compose invocation."""

    def __init__(self, root: Path, *, production: bool = False, env_file: Optional[Path] = None,
                 environ: Optional[Mapping[str, str]] = None, runner=subprocess.run,
                 compose: Optional[Sequence[str]] = None, docker: Optional[Sequence[str]] = None,
                 caller_cwd: Optional[Path] = None, compose_version: Optional[Sequence[int]] = None,
                 command_options: Sequence[str] = (), status_overlay: Optional[Path] = None) -> None:
        self.root = root.resolve()
        # The release options this run was given (argv words, as __main__ collects them): the check commands
        # this installation prints repeat them (production_command), so a pasted command plans the same release.
        # The caller may also set the attribute after construction.
        self.command_options: List[str] = [str(word) for word in command_options]
        # Where the operator ran bin/update (its --cwd): printed commands start with
        # `cd <checkout> && ` when that is not the checkout (see cd_prefix).
        self.caller_cwd = Path(caller_cwd) if caller_cwd is not None else self.root
        self.production = production
        # Explicit registered production status mount; never discovered from an
        # ambient export. The registration adapter qualifies its content/hash.
        self.status_overlay = Path(status_overlay).absolute() if status_overlay is not None else None
        if self.status_overlay is not None and (not production or not self.status_overlay.is_file()):
            raise InstallError('registered production status overlay is unavailable')
        self.runner = runner
        self.environ = dict(os.environ if environ is None else environ)
        self.docker = list(docker or ["docker"])
        # Values `check` plans with instead of the env file's (see overlay_env).
        self.env_overlay: Dict[str, Optional[str]] = {}
        if production:
            self.mode = "production"
            self.env_alias = (env_file or self.root / PRODUCTION_ENV).absolute()
            self.env_file = self.env_alias.resolve()
            if not self.env_file.exists():
                raise InstallError(
                    f"{self.env_file} does not exist. Production installs are configured by deploy/setup.sh; "
                    "pass --env-file if yours lives elsewhere")
            for name in (DISTRIBUTED_FILE, PRODUCTION_OVERLAY):
                if not (self.root / name).exists():
                    raise InstallError(f"{name} is missing from {self.root}: is this a complete checkout?")
        else:
            if env_file is not None and env_file.resolve() != (self.root / ".env").resolve():
                raise InstallError(
                    "--env-file is only for production installs (--production). Standalone and Distributed "
                    "installs always use the checkout's .env, as docker compose does")
            self.env_file = self.root / ".env"
            if not self.env_file.exists():
                if (self.root / PRODUCTION_ENV).exists():
                    raise InstallError(
                        f"no .env in {self.root}, but {PRODUCTION_ENV} exists: this looks like a deploy/setup.sh "
                        f"production install. Add --production to the command, as in "
                        f"`{self.production_command('check')}`")
                raise InstallError(f"no .env in {self.root}: run bin/update from the checkout ./bin/install set up")
            self.mode = self._detect_mode()
        # Keep the selected canonical object path separate from a configured
        # alias: a later retarget must not redirect a proven owner's mutation.
        self.env_target = self.env_file.resolve()
        self.env = read_env(self.env_file)
        self._check_shell_overrides()
        command = compose if compose is not None else resolve_compose_command(runner)
        self.compose = list(command)
        # The (major, minor, patch) Compose reported when the command was resolved, else None (unknown).
        version = compose_version if compose_version is not None else getattr(command, "version", None)
        self.compose_version: Optional[Tuple[int, ...]] = tuple(version) if version is not None else None
        # Refused before any other Compose call: every command needs `config --format json`.
        if self.compose_version in COMPOSE_REFUSED:
            raise InstallError(COMPOSE_REFUSED[self.compose_version])
        self._config: Optional[Dict[str, Any]] = None
        # service_model's cache: the probe's services and whether it saw every profile, and the
        # model built from it with the active config of that time.
        self._service_probe: Optional[Tuple[Dict[str, Dict[str, Any]], bool]] = None
        self._service_model: Optional[Tuple[Dict[str, Any], Dict[str, Any]]] = None
        self._up_pull_never: Optional[bool] = None

    # ----------------------------------------------------------- mode --
    def _detect_mode(self) -> str:
        env = read_env(self.env_file)
        recorded = env.get("COMPOSE_FILE", "")
        shell = self.environ.get("COMPOSE_FILE")
        if shell is not None and shell != recorded:
            raise InstallError(
                f"COMPOSE_FILE={shell} is set in your shell, and Docker Compose uses it instead of .env "
                f"({'COMPOSE_FILE=' + recorded if recorded else 'no COMPOSE_FILE'}). Run `unset COMPOSE_FILE` and "
                "try again, as ./bin/install requires")
        # Like Compose: the shell's separator (even an empty one) wins over .env's.
        separator = self.environ.get("COMPOSE_PATH_SEPARATOR", env.get("COMPOSE_PATH_SEPARATOR")) or os.pathsep
        names = [Path(entry).name for entry in recorded.split(separator) if entry]
        has_standalone = STANDALONE_FILE in names or not names
        has_distributed = DISTRIBUTED_FILE in names
        if has_distributed and STANDALONE_FILE in names:
            raise InstallError(f"COMPOSE_FILE in .env lists both {STANDALONE_FILE} and {DISTRIBUTED_FILE}")
        if has_distributed:
            return "distributed"
        if has_standalone:
            return "standalone"
        raise InstallError(f"COMPOSE_FILE={recorded} in .env names neither {STANDALONE_FILE} nor {DISTRIBUTED_FILE}")

    def _check_shell_overrides(self) -> None:
        # Compose prefers an exported project name to the env file's, so its calls could act on another install's
        # containers while this checkout's env file changes. Export-only installs are also valid (bin/install
        # accepts them): only the operator can decide which project owns this checkout, not this preflight.
        # Production drops the export when its env file sets the name, so only a name the file lacks matters there.
        shell_project = self.environ.get("COMPOSE_PROJECT_NAME")
        recorded = self.env.get("COMPOSE_PROJECT_NAME")
        if shell_project is not None and shell_project != (recorded or "") and not (
                self.production and recorded is not None):
            env_name = self.relative(self.env_file)
            verify = ("Unsetting it can select a different project; verify the resulting Compose project belongs to "
                      "this checkout before applying")
            if not shell_project:
                # Empty, it still wins over the env file's: Compose then names the project after the compose
                # file's name: or the directory (checked with Compose v2.24.0 and v5.6.0).
                raise InstallError(
                    f"COMPOSE_PROJECT_NAME is set but empty in your shell, so Docker Compose ignores {env_name}'s "
                    f"COMPOSE_PROJECT_NAME={recorded} and names the project after the compose file or the directory: "
                    "bin/update would restart that project with this checkout's files. If this checkout really "
                    "belongs to that fallback project, persist its resolved, nonempty COMPOSE_PROJECT_NAME in "
                    f"{env_name} and unset the empty shell export. If this is an unrelated shell export, run "
                    f"`unset COMPOSE_PROJECT_NAME` to use the name recorded in {env_name}. {verify}")
            recorded_name = ("no COMPOSE_PROJECT_NAME" if recorded is None else
                             f"COMPOSE_PROJECT_NAME={recorded}" if recorded else "COMPOSE_PROJECT_NAME is empty")
            raise InstallError(
                f"COMPOSE_PROJECT_NAME={shell_project} is set in your shell, and Docker Compose uses it instead of "
                f"{env_name} ({recorded_name}), so bin/update would restart project {shell_project} with this "
                "checkout's files. If this checkout really belongs to the exported project, persist "
                f"COMPOSE_PROJECT_NAME={shell_project} in {env_name}. If this is an unrelated shell export, run "
                f"`unset COMPOSE_PROJECT_NAME`. {verify}")
        if self.production:
            return  # the subprocess environment drops them (see subprocess_env); --env-file wins over COMPOSE_ENV_FILES
        # Compose ignores an empty value, but a whitespace-only file list is still nonempty.
        if self.environ.get("COMPOSE_ENV_FILES"):
            raise InstallError(
                f"COMPOSE_ENV_FILES={self.environ['COMPOSE_ENV_FILES']} is set in your shell, so Docker Compose would "
                "read those files instead of .env, the file bin/update updates. Run `unset COMPOSE_ENV_FILES` and "
                "try again")
        if "COMPOSE_DISABLE_ENV_FILE" in self.environ:  # production passes --env-file, which Compose always reads
            raise InstallError(
                f"COMPOSE_DISABLE_ENV_FILE={self.environ['COMPOSE_DISABLE_ENV_FILE']} is set in your shell, and Docker "
                "Compose skips .env, the file bin/update updates, when it is true. Run `unset "
                "COMPOSE_DISABLE_ENV_FILE` and try again")
        # Refused even when equal to .env today: after the update .env changes
        # and the export would silently select the old image again.
        exported = [key for key in VERSION_VARIABLES if key in self.environ]
        if exported:
            raise InstallError(
                f"{', '.join(exported)} {'is' if len(exported) == 1 else 'are'} exported in your shell and would "
                "override .env for docker compose. Unset them and try again")

    @property
    def label(self) -> str:
        return {"standalone": "Standalone", "distributed": "Distributed", "production": "Production (Distributed)"}[self.mode]

    def relative(self, path: Path) -> str:
        try:
            return str(path.resolve().relative_to(self.root))
        except ValueError:
            return str(path)

    def identity(self) -> Dict[str, Any]:
        """Which install a receipt or journal belongs to: the values every receipt records."""

        return {"mode": self.mode, "production": self.production, "env_file": self.relative(self.env_file),
                "project": self.project}

    def cd_prefix(self) -> str:
        """`cd <checkout> && ` when bin/update was run from another directory (caller_cwd), else "".

        Printed commands start with it, so they work pasted in the shell that
        ran bin/update: they name the checkout's files relative to it.
        """

        caller = getattr(self, "caller_cwd", None)
        if caller is None:
            return ""
        try:
            if Path(caller).resolve() == self.root:
                return ""
        except (OSError, RuntimeError):
            pass
        return f"cd {shlex.quote(str(self.root))} && "

    def production_command(self, args: str) -> str:
        """`./bin/update --production <args>` for this checkout (deploy/.env.production), with cd_prefix().

        For the hints that tell a run without --production that the
        checkout is a deploy/setup.sh install. A check or apply command ends
        with this run's release options (command_options), shell-quoted, so
        pasted it plans the release this run did.
        """

        command = f"{self.cd_prefix()}./bin/update --production {args}"
        options = getattr(self, "command_options", None) or ()
        if args.split(" ", 1)[0] not in ("check", "apply") or not options:
            return command
        return " ".join([command, *(shlex.quote(str(word)) for word in options)])

    def shell_rule(self) -> str:
        """The sentence that follows a Compose command printed for the operator to run on a production install.

        bin/update's own commands take every key from the env file
        (subprocess_env drops the shell's exports); Compose run by hand
        prefers an exported value, so an exported FUTURE_AGI_VERSION would
        start another release than the env file names.
        """

        return (f"Run it from a shell that does not export any key of {self.relative(self.env_file)} "
                "(Compose prefers your shell over the env file).")

    def compose_hint(self) -> str:
        """The Compose command for this install, ready to paste in the shell that ran bin/update.

        `docker compose`, or the standalone binary this run uses: by name when
        the PATH finds that same binary, else by its path. Production adds the
        env file and both Compose files the way deploy/setup.sh and
        deploy/README.md's $COMPOSE do. Every word is shell-quoted, and it
        starts with `cd <checkout> && ` when bin/update was run from another
        directory (cd_prefix).
        """

        words = list(self.compose) or ["docker", "compose"]
        name = os.path.basename(words[0])
        if name != words[0] and self._on_path(name, words[0]):
            words[0] = name
        if self.production:
            words += ["--env-file", self.relative(self.env_file), "-f", DISTRIBUTED_FILE, "-f", PRODUCTION_OVERLAY]
            if getattr(self, 'status_overlay', None) is not None:
                words += ['-f', self.relative(self.status_overlay)]
        return self.cd_prefix() + " ".join(shlex.quote(word) for word in words)

    def _on_path(self, name: str, program: str) -> bool:
        """Whether a shell with this install's PATH runs `program` for `name`."""

        found = shutil.which(name, path=self.environ.get("PATH", os.defpath))
        if not found:
            return False
        try:
            return os.path.samefile(found, program)
        except OSError:
            return False

    def fresh(self) -> "Installation":
        """Independent live model. An in-memory download overlay is never reused."""
        return Installation(self.root, production=self.production,
                            env_file=getattr(self, 'env_alias', self.env_file),
                            environ=self.environ, runner=self.runner, compose=self.compose, docker=self.docker,
                            caller_cwd=self.caller_cwd, compose_version=self.compose_version,
                            command_options=self.command_options, status_overlay=self.status_overlay)

    def overlay_env(self, values: Mapping[str, Optional[str]]) -> None:
        """Plan as if the env file had these values (None: as if the key were unset).

        For `check`, which never writes the env file: self.env takes the
        values, and Compose sees them through the subprocess environment,
        which it prefers over the env file. An unset key is passed as "" so a
        `${VAR:-default}` default applies.
        """

        for key, value in values.items():
            if value is None:
                self.env.pop(key, None)
            else:
                self.env[key] = value
            self.env_overlay[key] = value
        self._config = None
        self._service_probe = None
        self._service_model = None

    def reload_env(self) -> None:
        """Reload the env file after a run changes it and discard Compose models resolved from old values."""

        self.env = read_env(self.env_file)
        self.env_overlay.clear()
        self._config = None
        self._service_probe = None
        self._service_model = None

    # ------------------------------------------------------- commands --
    def subprocess_env(self) -> Dict[str, str]:
        env = dict(self.environ)
        if self.production:
            # The env file is authoritative over ambient exports (deploy/setup.sh itself unsets only SECRET_NAMES).
            for key in set(SETUP_SECRET_NAMES) | set(read_env(self.env_file)) | set(VERSION_VARIABLES):
                env.pop(key, None)
            env.pop("COMPOSE_FILE", None)
            env.pop("COMPOSE_ENV_FILES", None)
        for key, value in self.env_overlay.items():
            env[key] = "" if value is None else value
        return env

    def compose_args(self, *args: str) -> List[str]:
        if self.production:
            overlay = getattr(self, 'status_overlay', None)
            return [*self.compose, "--env-file", str(self.env_file),
                    "-f", str(self.root / DISTRIBUTED_FILE), "-f", str(self.root / PRODUCTION_OVERLAY),
                    *(['-f', str(overlay)] if overlay is not None else []), *args]
        return [*self.compose, *args]

    def run(self, args: Sequence[str], *, check: bool = True, stream: bool = False,
            timeout: Optional[float] = None, extra_env: Optional[Mapping[str, str]] = None) -> CommandResult:
        """Run a command in the checkout with subprocess_env(), plus `extra_env` on top of it."""

        env = self.subprocess_env()
        env.update(extra_env or {})
        kwargs: Dict[str, Any] = {"cwd": str(self.root), "env": env, "check": False, "text": True}
        if timeout is not None:
            kwargs["timeout"] = timeout
        if not stream:
            kwargs["capture_output"] = True
        try:
            result = self.runner(list(args), **kwargs)
        except FileNotFoundError as exc:
            raise InstallError(f"cannot run {args[0]}: {exc}") from exc
        except subprocess.TimeoutExpired as exc:
            program, rest = split_command(args)
            words = program + rest
            shown = " ".join(words[:4]) + (" ..." if len(words) > 4 else "")
            raise InstallError(f"`{shown}` timed out after {timeout:.0f}s") from exc
        outcome = CommandResult(list(args), result.returncode, getattr(result, "stdout", "") or "",
                                getattr(result, "stderr", "") or "")
        if check and outcome.returncode != 0:
            raise InstallError(self.explain_failure(outcome))
        return outcome

    def explain_failure(self, result: CommandResult) -> str:
        """describe_failure(), plus what to do when this Compose release is known to fail that call.

        A failed `config` call on a release that rejects valid compose files
        (COMPOSE_CONFIG_HINTS) says to upgrade Compose.
        """

        problem = self.describe_failure(result)
        hint = COMPOSE_CONFIG_HINTS.get(getattr(self, "compose_version", None) or ())
        compose = list(getattr(self, "compose", None) or [])
        if (hint and compose and list(result.args[:len(compose)]) == compose
                and split_command(result.args)[1][:1] == ["config"]):
            problem += (" " if problem.endswith((".", "?", "!")) else ". ") + hint
        return problem

    def compose_run(self, *args: str, check: bool = True, stream: bool = False,
                    timeout: Optional[float] = None) -> CommandResult:
        # Recheck immediately before a mutating Compose command, not only when the plan was made: a backup
        # hook or another process may have changed project selection since then. Read-only config/status
        # remains usable without an established owner (including when Docker is down).
        if args and args[0] not in ("config", "version", "ps", "ls", "logs", "images", "top", "events") and not (
                "--help" in args or "-h" in args):
            self.check_ownership()
        return self.run(self.compose_args(*args), check=check, stream=stream, timeout=timeout)

    def docker_run(self, *args: str, check: bool = True) -> CommandResult:
        return self.run([*self.docker, *args], check=check)

    @staticmethod
    def describe_failure(result: CommandResult) -> str:
        """`<program> <subcommand> <arguments>` failed with ...: at most four words, no options or their values."""

        program, rest = split_command(result.args)
        value_options = DOCKER_COMMAND_VALUE_OPTIONS if program == ["docker"] else COMPOSE_COMMAND_VALUE_OPTIONS
        words, index = list(program), 0
        while index < len(rest) and len(words) < 4:
            arg = rest[index]
            if arg.startswith("-") and arg != "-":
                index += 2 if arg in value_options else 1
                continue
            # A container id as `docker ps` prints it, not all 64 characters.
            words.append(arg[:12] if re.fullmatch(r"[0-9a-f]{64}", arg) else arg)
            index += 1
        lines = [line.strip() for line in result.stderr.splitlines() if line.strip()]
        detail = f": {lines[-1]}" if lines else (" (output above)" if not result.stderr else "")
        if lines and DAEMON_UNREACHABLE.search(result.stderr):
            detail += (" " if detail.endswith((".", "?", "!")) else ". ") + DAEMON_HINT
        return f"`{' '.join(words)}` failed with exit code {result.returncode}{detail}"

    # --------------------------------------------------------- config --
    def config(self, refresh: bool = False) -> Dict[str, Any]:
        """The fully resolved Compose model, with the env file as it is now (and any overlay_env values).

        Its "services" are the services Compose runs now (the active ones),
        each with its definition from `config --format json`. Which services
        are active is not taken from that JSON's keys: Compose v2.24.7 lists
        every profile's services there. `config --services` names the active
        set on every Compose release, with profiles applied.
        """

        if self._config is None or refresh:
            result = self.compose_run("config", "--format", "json")
            try:
                model = json.loads(result.stdout)
            except json.JSONDecodeError as exc:
                raise InstallError(f"docker compose config returned invalid JSON: {exc}") from exc
            self._config = self._active_only(model)
        return self._config

    def _active_only(self, model: Any) -> Any:
        """`model` with only the services `config --services` names."""

        services = model.get("services") if isinstance(model, dict) else None
        if not isinstance(services, dict):
            return model
        # The service list, not JSON object membership, is authoritative on every Compose release. In particular,
        # v2.24.7's JSON output includes services from disabled profiles.
        listed = self.compose_run("config", "--services").stdout
        names = list(dict.fromkeys(line.strip() for line in listed.splitlines() if line.strip()))
        unknown = [name for name in names if name not in services]
        if unknown:
            raise InstallError(
                f"`{' '.join(split_command(self.compose)[0] or ['docker', 'compose'])} config --services` lists "
                f"{', '.join(unknown[:5])}{' ...' if len(unknown) > 5 else ''}, which its `config --format json` "
                "does not define, so bin/update cannot tell which services Compose runs. Upgrade Docker Compose and "
                "run bin/update again")
        active = set(names)
        return dict(model, services={name: definition for name, definition in services.items() if name in active})

    def service_model(self, refresh: bool = False) -> Dict[str, Any]:
        """Every service the compose files define, under any profile, and the version key its image follows.

        {"services": {name: {"image", "variable", "active", "buildable"}}, "complete": bool}

        Compose is asked, never the files read: `config --profiles` names
        every profile, then `config --format json` runs with all of them
        enabled (COMPOSE_PROFILES) and every version key set to its probe
        value, PROBE_TAG_PREFIX + key (both added to subprocess_env(), as
        overlay_env's values are). `config --no-interpolate` is not used: it
        rejects this repository's compose files on every Compose release
        before v2.40.3.

        - "variable": the version key whose probe value starts the service's
          image tag (`futureagi/serving:bin-update-probe-SERVING_VERSION-gpu`
          gives SERVING_VERSION), else None: the tag is written in a Compose
          file (or the service has no image).
        - "active": the service is in config(), the model Compose runs now
          (the services `config --services` names, see config()).
        - "image": config()'s image for an active service; the probe's, with
          the probe value in it, for any other.
        - "buildable": the service has a build section.

        "complete" is False when Compose could not resolve the model with
        every profile enabled (a service of a disabled profile needs a file
        or value that is missing, say). The services are then only the
        active ones, and a service missing from them may still be defined
        by a profile.

        Cached like config(): overlay_env drops it, refresh=True runs Compose
        again (config() too).
        """

        config = self.config(refresh=refresh)
        probe = getattr(self, "_service_probe", None)
        if probe is None or refresh:
            probe = self._probe_services()
            self._service_probe = probe
        cached = getattr(self, "_service_model", None)
        if cached is not None and cached[0] is config and not refresh:
            return cached[1]
        probed, complete = probe
        active = config.get("services") or {}
        services: Dict[str, Dict[str, Any]] = {}
        for name in sorted(set(probed) | set(active)):
            definition = probed.get(name)
            current = active.get(name)
            if name in active and not isinstance(current, dict):
                current = {}
            if definition is None:
                complete = False  # only the active model lists it: the probe did not see everything
            image = (current if current is not None else definition or {}).get("image")
            services[name] = {
                "image": image if isinstance(image, str) else "",
                "variable": probe_variable(str(definition.get("image") or "")) if definition is not None else None,
                "active": current is not None,
                "buildable": "build" in (definition or {}) or "build" in (current or {}),
            }
        model = {"services": services, "complete": complete}
        self._service_model = (config, model)
        return model

    def _probe_services(self) -> Tuple[Dict[str, Dict[str, Any]], bool]:
        """(the services `config` resolves with every version key at its probe value, whether every profile was on)."""

        probe_env = {key: PROBE_TAG_PREFIX + key for key in VERSION_VARIABLES}
        profiles = self._profiles(probe_env)
        if profiles == []:
            return self._probe(probe_env, strict=True), True
        if profiles:
            services = self._probe({**probe_env, "COMPOSE_PROFILES": ",".join(profiles)}, strict=False)
            if services is not None:
                return services, True
        # Without COMPOSE_PROFILES: only the profiles the install enables, as in config().
        return self._probe(probe_env, strict=True), False

    def _profiles(self, probe_env: Mapping[str, str]) -> Optional[List[str]]:
        """Every profile the compose files name (`config --profiles`), or None when Compose does not tell."""

        result = self.run(self.compose_args("config", "--profiles"), check=False, extra_env=probe_env)
        names = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        if result.returncode != 0 or not all(PROFILE_NAME.fullmatch(name) for name in names):
            return None
        return list(dict.fromkeys(names))

    def _probe(self, probe_env: Mapping[str, str], *, strict: bool) -> Optional[Dict[str, Dict[str, Any]]]:
        """The services of `config --format json` run with `probe_env`; on failure None, or (strict) InstallError."""

        result = self.run(self.compose_args("config", "--format", "json"), check=False, extra_env=probe_env)
        if result.returncode != 0:
            if not strict:
                return None
            problem = self.explain_failure(result)
            if problem != self.describe_failure(result):
                raise InstallError(problem)  # the Compose release, not the placeholders, is the cause
            raise InstallError(
                f"{problem}{' ' if problem.endswith(('.', '?', '!')) else '. '}bin/update ran it with every version "
                f"key set to a placeholder (FUTURE_AGI_VERSION={PROBE_TAG_PREFIX}FUTURE_AGI_VERSION, and so on) to "
                "learn which key selects each image. Make the compose files accept any tag in those keys, then try "
                "again")
        try:
            model = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            if not strict:
                return None
            raise InstallError(f"docker compose config returned invalid JSON: {exc}") from exc
        services = model.get("services") if isinstance(model, dict) else None
        if not isinstance(model, dict) or not isinstance({} if services is None else services, dict):
            if not strict:
                return None
            raise InstallError("docker compose config did not return a Compose model")
        return {str(name): dict(definition) if isinstance(definition, dict) else {}
                for name, definition in (services or {}).items()}

    def supports_up_pull_never(self) -> bool:
        """Whether this Compose has `up --pull never` (asked once, from `up --help`).

        False when the help cannot be read: the caller then treats `up` as
        possibly pulling.
        """

        cached = getattr(self, "_up_pull_never", None)
        if cached is not None:
            return cached
        try:
            result = self.run([*self.compose, "up", "--help"], check=False, timeout=60)
        except InstallError:
            return False
        self._up_pull_never = result.returncode == 0 and bool(UP_PULL_OPTION.search(result.stdout + "\n" + result.stderr))
        return self._up_pull_never

    @property
    def project(self) -> str:
        name = self.config().get("name")
        if not name:
            raise InstallError("docker compose config did not report a project name")
        return name

    def futureagi_services(self, config: Optional[Mapping[str, Any]] = None) -> List[Dict[str, Any]]:
        """Every service whose image is a Future AGI image, with its parsed reference."""

        config = config or self.config()
        services = []
        for name, service in sorted((config.get("services") or {}).items()):
            image = service.get("image")
            if not image:
                continue
            try:
                ref = parse_image_ref(image)
            except ManifestError:
                continue
            component = component_for(ref)
            if component:
                services.append({"service": name, "image": image, "ref": ref, "component": component,
                                 "buildable": "build" in service})
        return services

    # ------------------------------------------------------- ownership --
    def _ownership_error(self, detail: str) -> InstallError:
        production_hint = (f" If this is the checkout's production install, use `{self.production_command('check')}` "
                           "with its original env file." if not self.production and
                           (self.root / PRODUCTION_ENV).exists() else "")
        return InstallError(
            f"cannot establish Compose project {self.project!r} ownership for checkout {str(self.root)!r}: {detail}. "
            "Refusing to use this project's containers. Run bin/update from the original owning checkout with its "
            "original Compose files and env file (including --production/--env-file when needed). If this checkout "
            "was installed with an exported project name, persist that installation's COMPOSE_PROJECT_NAME in "
            f"{self.relative(self.env_file)}; do not simply unset it and use another project's fallback name. "
            "For missing provenance or relocated paths, restore the original paths or have the operator verify "
            "ownership and recover the installation manually before retrying. Do not rename, remove or adopt "
            f"another project's resources to bypass this check.{production_hint}")

    @staticmethod
    def _owner_path(value: Any, *, directory: bool = False, relative_to: Optional[Path] = None) -> Path:
        """An existing, canonical provenance path; absent/relative/ambiguous labels are not evidence.

        Compose records config_files and working_dir as absolute paths. Its
        environment_file label preserves --env-file's spelling, so only that
        label may be relative (to the recorded working_dir).
        """

        if not isinstance(value, str) or not value or any(ord(char) < 32 for char in value):
            raise ValueError("missing or malformed path")
        path = Path(value)
        if not path.is_absolute():
            if relative_to is None:
                raise ValueError("path is not absolute")
            path = relative_to / path
        path = path.resolve(strict=True)
        if not (path.is_dir() if directory else path.is_file()):
            raise ValueError("path is not a directory" if directory else "path is not a file")
        return path

    def _ownership_files(self) -> List[Path]:
        """The local files this invocation selects, in Compose's precedence order.

        No upward directory search or remote/stdin configuration is adopted:
        bin/update must act from the checkout that owns the install. Explicit
        COMPOSE_FILE uses Compose's path separator, not the provenance label's
        comma separator. The latter is unescaped, so comma-bearing paths fail
        closed when the labels are compared.
        """

        if self.production:
            paths = [self.root / DISTRIBUTED_FILE, self.root / PRODUCTION_OVERLAY]
            if getattr(self, 'status_overlay', None) is not None:
                paths.append(self.status_overlay)
        else:
            env = dict(read_env(self.env_file), **self.subprocess_env())
            selected = env.get("COMPOSE_FILE")
            if selected:
                separator = env.get("COMPOSE_PATH_SEPARATOR") or os.pathsep
                paths = [self.root / entry for entry in selected.split(separator)]
            else:
                names = ("compose.yaml", "compose.yml", "docker-compose.yml", "docker-compose.yaml")
                overrides = ("compose.override.yml", "compose.override.yaml", "docker-compose.override.yml",
                             "docker-compose.override.yaml")
                base = next((self.root / name for name in names if (self.root / name).is_file()), None)
                if base is None:
                    raise ValueError("no Compose file exists in the checkout")
                paths = [base]
                override = next((self.root / name for name in overrides if (self.root / name).is_file()), None)
                if override is not None:
                    paths.append(override)
        return [self._owner_path(str(path)) for path in paths]

    def _check_container_owners(self, records: List[Dict[str, Any]]) -> None:
        if not records:
            return
        field = "selected config_files/environment_file"
        try:
            files = self._ownership_files()
            env_file = self._owner_path(str(self.env_file))
            for record in records:
                config = record.get("Config")
                labels = config.get("Labels") if isinstance(config, dict) else None
                if not isinstance(labels, dict):
                    raise ValueError(f"container {str(record.get('Id', '?'))[:12]} has no Compose provenance labels")
                if labels.get("com.docker.compose.project") != self.project:
                    raise ValueError("an inspected container has a different or missing project label")
                field = "working_dir"
                working = labels.get("com.docker.compose.project.working_dir")
                owner = self._owner_path(working, directory=True)
                if owner != self.root:
                    raise ValueError(f"container {str(record.get('Id', '?'))[:12]} records working_dir {working!r}")
                field = "config_files"
                recorded = labels.get("com.docker.compose.project.config_files")
                if not isinstance(recorded, str) or not recorded:
                    raise ValueError("missing or malformed config_files label")
                if [self._owner_path(path) for path in recorded.split(",")] != files:
                    raise ValueError(f"container {str(record.get('Id', '?'))[:12]} records different config_files "
                                     f"{recorded!r}")
                field = "environment_file"
                recorded_env = labels.get("com.docker.compose.project.environment_file")
                # With no --env-file, supported Compose releases omit this label: their implicit .env is
                # identified by working_dir. Production always passes --env-file and must prove which one.
                if recorded_env is None and not self.production:
                    continue
                if not isinstance(recorded_env, str) or not recorded_env:
                    raise ValueError("missing or malformed environment_file label for an explicit env file")
                if [self._owner_path(path, relative_to=owner) for path in recorded_env.split(",")] != [env_file]:
                    raise ValueError(f"container {str(record.get('Id', '?'))[:12]} records a different "
                                     f"environment_file {recorded_env!r}")
        except (OSError, RuntimeError, ValueError) as exc:
            raise self._ownership_error(f"{field}: {exc}") from exc

    def check_ownership(self) -> None:
        """Fresh, fail-closed mutation preflight. Call under the update lock before backup/env/recovery writes.

        Unlike containers() (which still permits a read-only empty status), a
        mutation needs positive provenance. No cached proof, image match,
        project-name match alone, or override can authorize a different owner.
        Stopped and one-off containers participate too: they may share data.
        """

        self.config(refresh=True)
        if not self.containers():
            raise self._ownership_error("no containers remain to prove ownership")

    # ------------------------------------------------------ containers --
    def containers(self) -> List[Dict[str, Any]]:
        """Every container of this Compose project, running or not.

        Every record must prove this checkout owns it, including stopped and
        one-off containers. A foreign or ambiguous record refuses the whole
        project, never silently filters it. A `docker compose run` container
        has "oneoff": True; callers judging versions (not ownership) skip it.
        """

        listing = self.docker_run("ps", "-a", "-q", "--no-trunc",
                                  "--filter", f"label=com.docker.compose.project={self.project}", check=False)
        ids = [line.strip() for line in listing.stdout.splitlines() if line.strip()]
        if listing.returncode != 0:
            raise InstallError(self.describe_failure(listing))
        if not ids:
            return []
        # Never [] on failure: that would read as "no install here" and let the
        # post-rollout check of the running images pass without looking.
        inspected = self.docker_run("inspect", *ids, check=False)
        try:
            records = json.loads(inspected.stdout)
        except json.JSONDecodeError as exc:
            if inspected.returncode != 0:
                raise InstallError(self.describe_failure(inspected)) from exc
            raise InstallError(f"docker inspect returned invalid JSON: {exc}") from exc
        if inspected.returncode != 0 and not self._only_removed(inspected.stderr, ids):
            raise InstallError(self.describe_failure(inspected))
        if not isinstance(records, list) or not all(isinstance(record, dict) for record in records):
            raise InstallError("docker inspect did not return a list of containers")
        seen = [record.get("Id") for record in records]
        if any(not isinstance(cid, str) or cid not in ids for cid in seen) or len(set(seen)) != len(seen):
            raise self._ownership_error("docker inspect returned unexpected or duplicate containers")
        missing = set(ids) - set(seen)
        if missing and (inspected.returncode == 0 or any(
                not self._only_removed(inspected.stderr, [cid]) for cid in missing)):
            raise self._ownership_error("docker inspect omitted containers without confirming their removal")
        self._check_container_owners(records)
        containers = []
        for record in records:
            labels = (record.get("Config") or {}).get("Labels") or {}
            state = record.get("State") or {}
            container = {
                "id": record.get("Id", ""),
                "service": labels.get("com.docker.compose.service", ""),
                "image_id": record.get("Image", ""),
                "image": (record.get("Config") or {}).get("Image", ""),
                "status": state.get("Status", ""),
                "exit_code": state.get("ExitCode"),
                "restart_count": record.get("RestartCount", 0),
                "health": ((state.get("Health") or {}).get("Status")) or "none",
                "started_at": state.get("StartedAt", ""),
                "command": [*((record.get("Config") or {}).get("Entrypoint") or []),
                            *((record.get("Config") or {}).get("Cmd") or [])],
            }
            if labels.get("com.docker.compose.oneoff") == "True":
                container["oneoff"] = True
            containers.append(container)
        return containers

    @staticmethod
    def _only_removed(stderr: str, ids: Sequence[str]) -> bool:
        """Whether `docker inspect` failed only because listed containers were removed after `docker ps`.

        It still prints the others, and the removed ones are no longer part
        of the project.
        """

        lines = [line for line in stderr.splitlines() if line.strip()]
        return bool(lines) and all("no such" in line.lower() and any(cid in line for cid in ids) for line in lines)

    def image(self, reference: str) -> Optional[Dict[str, Any]]:
        result = self.docker_run("image", "inspect", reference, check=False)
        if result.returncode != 0:
            return None
        try:
            records = json.loads(result.stdout)
        except json.JSONDecodeError:
            return None
        if not records:
            return None
        record = records[0]
        labels = (record.get("Config") or {}).get("Labels") or {}
        return {"id": record.get("Id", ""), "repo_digests": record.get("RepoDigests") or [],
                "version": labels.get("org.opencontainers.image.version", ""),
                "revision": labels.get("org.opencontainers.image.revision", "")}

    def check_stack(self, containers: List[Dict[str, Any]]) -> List[str]:
        """Reasons the running project does not match the detected mode.

        One-off `docker compose run` containers say nothing about how the
        stack was started (the production initialization jobs run that way
        without --check), so they are left out. A project with no containers
        may only be stopped: the reason gives the command that starts it. A
        run without --production names `./bin/update --production check`
        (with this run's release options, see production_command) first
        wherever the checkout looks like a deploy/setup.sh install
        (deploy/.env.production, or the overlay's check-only bootstrap jobs).
        """

        containers = [c for c in containers if not c.get("oneoff")]
        services = {c["service"] for c in containers}
        # A run without --production in a checkout deploy/setup.sh set up: every remedy names --production first.
        production_env = not self.production and (self.root / PRODUCTION_ENV).exists()
        maybe_production = (
            f"This checkout has {PRODUCTION_ENV}: if Future AGI runs here as a deploy/setup.sh production install, "
            f"run `{self.production_command('check')}`. Otherwise ") if production_env else ""
        if not containers:
            return [self._no_containers(maybe_production)]
        problems = []
        distributed_signal = sorted(services & set(DISTRIBUTED_ONLY_SERVICES))
        if self.mode == "standalone" and distributed_signal:
            fix = "fix COMPOSE_FILE in .env (./bin/install records it)"
            problems.append(
                f"project {self.project!r} runs Distributed services ({', '.join(distributed_signal)}) but .env selects "
                "the Standalone stack. bin/update never moves an install between stacks"
                + (f". {maybe_production}{fix}" if maybe_production else f"; {fix}"))
        if self.mode != "standalone" and "app" in services and not distributed_signal:
            problems.append(
                f"project {self.project!r} runs the Standalone app container but bin/update was asked for the "
                f"{self.label} stack. bin/update never moves an install between stacks")
        bootstrap = [c for c in containers if c["service"] == "postgres-schema-bootstrap"]
        check_only = bool(bootstrap) and "--check" in bootstrap[-1]["command"]
        # Without --production, check-only bootstrap jobs are the production overlay's. That is the remedy, and the
        # only one: the others (COMPOSE_FILE, the stack) would move a production install off its overlay. A
        # Standalone run judges them only next to Distributed services (Standalone has no bootstrap job).
        if check_only and (self.mode == "distributed" or (self.mode == "standalone" and problems)):
            use = f"`{self.production_command('check')}`, which uses {PRODUCTION_ENV}"
            if not production_env:
                use = (f"`{self.production_command('check')}` with --env-file naming the env file deploy/setup.sh "
                       f"used ({PRODUCTION_ENV} does not exist), which uses it")
            return [f"this install runs the production overlay (check-only bootstrap jobs). Use {use} and the "
                    "overlay as deploy/setup.sh does"]
        if bootstrap and self.mode == "production" and not check_only:
            problems.append(
                "--production was given, but this install was started without the production overlay "
                "(its bootstrap jobs apply migrations). Run bin/update without --production")
        return problems

    def _no_containers(self, maybe_production: str = "") -> str:
        """Why a project with no containers is not updated, and how to start or install it.

        Production's start command is a Compose command the operator runs by
        hand: shell_rule() follows it.
        """

        situation = ("the stack may be stopped (Compose's down removes the containers and keeps the data), or Future "
                     "AGI is not installed here yet")
        situation = maybe_production + situation if maybe_production else situation[0].upper() + situation[1:]
        if self.production:
            return (f"no containers of Compose project {self.project!r} exist. {situation}: install it as "
                    "deploy/README.md describes (deploy/setup.sh), or start it with "
                    f"`{self.compose_hint()} up -d --no-build --wait`. {self.shell_rule()} Then check again")
        start = f"`{self.compose_hint()} up -d`"
        install = f"install it with `{self.cd_prefix()}./bin/install`"
        return (f"no containers of Compose project {self.project!r} exist. {situation}: start it with {start}, or "
                f"{install}, then check again")
