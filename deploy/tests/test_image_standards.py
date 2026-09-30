"""Image conventions (TESTING.md, "Image conventions"; deploy/images.toml):
OCI labels, base-image pinning, user, HEALTHCHECK and STOPSIGNAL of every
published image, the health probes, and the build workflows that feed the
labels."""

from __future__ import annotations

import contextlib
import fnmatch
import functools
import http.server
import importlib.util
import io
import json
import os
import re
import runpy
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import tomllib
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"

APACHE = "Apache-2.0"
WITH_EE = "Apache-2.0 AND LicenseRef-FutureAGI-Enterprise-1.0"
INHERITED = object()  # set by the image this one is built FROM
HAVE_YAML = importlib.util.find_spec("yaml") is not None  # CI installs PyYAML

# Dockerfile -> what deploy/images.toml and the images page promise for its image.
IMAGES = {
    "futureagi/Dockerfile.oss": {
        "licenses": WITH_EE,
        "user": "root",
        "stopsignal": "SIGTERM",
        "healthcheck": True,
        "floating": set(),
    },
    "deploy/standalone/Dockerfile": {
        "licenses": WITH_EE,
        "user": "root",
        "stopsignal": "SIGTERM",
        "healthcheck": True,
        # :latest for a build from the newest release's tag; a release
        # passes digests.
        "floating": {
            "BACKEND_IMAGE",
            "FRONTEND_IMAGE",
            "FI_COLLECTOR_IMAGE",
            "AGENTCC_GATEWAY_IMAGE",
        },
    },
    "frontend/Dockerfile": {
        "licenses": APACHE,
        "user": "root",
        "stopsignal": "SIGQUIT",
        "healthcheck": True,
        "floating": set(),
    },
    "fi-collector/Dockerfile": {
        "licenses": APACHE,
        "user": "nonroot:nonroot",
        "stopsignal": "SIGTERM",
        "healthcheck": True,
        "floating": {"GO_IMAGE"},
    },
    "agentcc-gateway/Dockerfile": {
        "licenses": APACHE,
        "user": "65532:65532",
        "stopsignal": "SIGTERM",
        "healthcheck": True,
        "floating": {"GO_IMAGE"},
    },
    # Root is a documented exception
    # (https://docs.futureagi.com/docs/self-hosting/images#users); Helm runs it as 1000.
    "futureagi/model_serving/Dockerfile.oss": {
        "licenses": APACHE,
        "user": "root",
        "stopsignal": "SIGTERM",
        "healthcheck": True,
        "floating": set(),
    },
    "futureagi/code-executor/Dockerfile": {
        "licenses": APACHE,
        "user": "root",
        "stopsignal": "SIGTERM",
        "healthcheck": True,
        # An immutable version tag, published before the release builds this.
        "floating": {"CODE_EXECUTOR_BASE"},
    },
    "futureagi/code-executor/Dockerfile.base": {
        "licenses": APACHE,
        "user": None,
        "stopsignal": None,
        "healthcheck": False,
        "floating": set(),
    },
    "Dockerfile.simulation-runner": {
        "licenses": WITH_EE,
        "user": INHERITED,
        "stopsignal": INHERITED,
        "healthcheck": INHERITED,
        "floating": {"BACKEND_IMAGE"},
    },
}

FIXED_LABELS = {
    "org.opencontainers.image.source": "https://github.com/future-agi/future-agi",
    "org.opencontainers.image.url": "https://futureagi.com",
    "org.opencontainers.image.documentation": "https://docs.futureagi.com/docs/self-hosting/images",
    "org.opencontainers.image.vendor": "Future AGI",
    "org.opencontainers.image.version": "${VERSION}",
    "org.opencontainers.image.revision": "${REVISION}",
    "org.opencontainers.image.created": "${CREATED}",
}


def instructions(path: Path) -> list[tuple[str, str]]:
    """(KEYWORD, arguments) per instruction; continuations joined, comments dropped."""
    result, buffer = [], ""
    for raw in path.read_text(encoding="utf-8").splitlines():
        stripped = raw.strip()
        if stripped.startswith("#") or (not buffer and not stripped):
            continue  # Docker drops comment lines, also inside a continuation
        if raw.rstrip().endswith("\\"):
            buffer += raw.rstrip()[:-1] + " "
            continue
        keyword, _, rest = (buffer + raw).strip().partition(" ")
        result.append((keyword.upper(), rest.strip()))
        buffer = ""
    return result


def final_stage(path: Path) -> list[tuple[str, str]]:
    steps = instructions(path)
    last_from = max(i for i, (keyword, _) in enumerate(steps) if keyword == "FROM")
    return steps[last_from + 1 :]


def labels(stage: list[tuple[str, str]]) -> dict[str, str]:
    found = {}
    for keyword, rest in stage:
        if keyword == "LABEL":
            found.update(re.findall(r'([A-Za-z0-9._-]+)="([^"]*)"', rest))
    return found


def exec_form(rest: str) -> list[str]:
    value = json.loads(rest)
    assert isinstance(value, list) and value, rest
    return value


class DockerfileConventions(unittest.TestCase):
    def test_every_published_image_has_the_oci_labels(self):
        for name, spec in IMAGES.items():
            with self.subTest(dockerfile=name):
                found = labels(final_stage(ROOT / name))
                for key, value in FIXED_LABELS.items():
                    self.assertEqual(found.get(key), value, key)
                self.assertEqual(
                    found.get("org.opencontainers.image.licenses"), spec["licenses"]
                )
                for key in ("title", "description"):
                    self.assertTrue(found.get(f"org.opencontainers.image.{key}"), key)

    def test_label_args_come_after_every_layer(self):
        # A changed VERSION/REVISION/CREATED must only change the image
        # config: no RUN (whose cache key includes the args in scope) and no
        # other layer may follow their declaration.
        for name in IMAGES:
            with self.subTest(dockerfile=name):
                stage = final_stage(ROOT / name)
                declared = [
                    i
                    for i, (keyword, rest) in enumerate(stage)
                    if keyword == "ARG"
                    and re.match(r"(VERSION|REVISION|CREATED)\b", rest)
                ]
                self.assertEqual(len(declared), 3)
                layers = [
                    i
                    for i, (keyword, _) in enumerate(stage)
                    if keyword in {"RUN", "COPY", "ADD"}
                ]
                self.assertLess(max(layers, default=-1), min(declared))
                # Not global either: a global ARG reaches every stage's FROM.
                steps = instructions(ROOT / name)
                first_from = next(
                    i for i, (keyword, _) in enumerate(steps) if keyword == "FROM"
                )
                for _, rest in steps[:first_from]:
                    self.assertFalse(
                        re.match(r"(VERSION|REVISION|CREATED)\b", rest), rest
                    )

    def test_base_images_are_digest_pinned_args(self):
        for name, spec in IMAGES.items():
            with self.subTest(dockerfile=name):
                steps = instructions(ROOT / name)
                stages = set()
                for keyword, rest in steps:
                    if keyword == "ARG":
                        arg, _, default = rest.partition("=")
                        if (
                            arg.endswith("_IMAGE") or arg.endswith("_BASE")
                        ) and default:
                            if arg in spec["floating"]:
                                self.assertNotIn(
                                    "@sha256:", default, f"{arg} is listed as floating"
                                )
                            else:
                                self.assertRegex(default, r"@sha256:[0-9a-f]{64}$", arg)
                    if keyword == "FROM":
                        words = [
                            w for w in rest.split() if not w.startswith("--platform=")
                        ]
                        image = words[0]
                        stage_prefix = image.split("${", 1)[0]
                        self.assertTrue(
                            image.startswith("${")
                            or image == "scratch"
                            or image in stages
                            or (
                                stage_prefix
                                and any(s.startswith(stage_prefix) for s in stages)
                            ),
                            f"FROM {image}: name the base in a pinned ARG",
                        )
                        if len(words) == 3 and words[1].upper() == "AS":
                            stages.add(words[2])

    def test_user_stopsignal_and_healthcheck(self):
        for name, spec in IMAGES.items():
            with self.subTest(dockerfile=name):
                stage = final_stage(ROOT / name)
                users = [rest for keyword, rest in stage if keyword == "USER"]
                signals = [rest for keyword, rest in stage if keyword == "STOPSIGNAL"]
                checks = [rest for keyword, rest in stage if keyword == "HEALTHCHECK"]
                if spec["user"] in (None, INHERITED):
                    self.assertEqual(users, [])
                else:
                    self.assertEqual(users[-1:], [spec["user"]])
                if spec["stopsignal"] in (None, INHERITED):
                    self.assertEqual(signals, [])
                else:
                    self.assertEqual(signals, [spec["stopsignal"]])
                if spec["healthcheck"] is True:
                    self.assertEqual(len(checks), 1)
                    options, _, command = checks[0].partition(" CMD ")
                    for option in (
                        "--interval=",
                        "--timeout=",
                        "--start-period=",
                        "--retries=",
                    ):
                        self.assertIn(option, options)
                    exec_form(command)
                else:
                    self.assertEqual(checks, [])

    def test_entrypoint_and_cmd_use_exec_form_with_absolute_paths(self):
        for name in IMAGES:
            with self.subTest(dockerfile=name):
                for keyword, rest in final_stage(ROOT / name):
                    if keyword == "ENTRYPOINT":
                        argv = exec_form(rest)
                        program = argv[1] if argv[0] == "bash" else argv[0]
                        self.assertTrue(program.startswith("/"), argv)
                    elif keyword == "CMD":
                        exec_form(rest)

    def test_the_go_images_ship_the_same_probe(self):
        sources = {
            name: probe_source(ROOT / name)
            for name in ("fi-collector/Dockerfile", "agentcc-gateway/Dockerfile")
        }
        self.assertEqual(len(set(sources.values())), 1)
        self.assertIn("package main", next(iter(sources.values())))

    def test_hadolint_config_ignores_only_package_pins(self):
        text = (ROOT / ".hadolint.yaml").read_text(encoding="utf-8")
        self.assertEqual(
            set(re.findall(r"^\s*-\s*(\S+)", text, re.M)), {"DL3008", "DL3018"}
        )


def yaml_jobs(workflow: Path) -> dict:
    import yaml

    return yaml.safe_load(workflow.read_text(encoding="utf-8"))["jobs"]


def probe_source(dockerfile: Path) -> str:
    """The Go source the Dockerfile's `printf ... > main.go` writes."""
    for keyword, rest in instructions(dockerfile):
        if (
            keyword == "RUN"
            and "> main.go" in rest
            and rest.startswith("printf '%s\\n'")
        ):
            command = rest.split("> main.go", 1)[
                0
            ]  # the printf, without the redirect and build
            return subprocess.run(
                ["sh", "-c", command], capture_output=True, text=True, check=True
            ).stdout
    raise AssertionError(f"{dockerfile}: no healthcheck probe source")


# ---------------------------------------------------------------------------
# Backend variants (futureagi/Dockerfile.oss IMAGE_VARIANT)
# ---------------------------------------------------------------------------

BACKEND = ROOT / "futureagi" / "Dockerfile.oss"
VARIANT_SCRIPT = ROOT / "futureagi" / "docker" / "image-variant.sh"
VARIANT_ARGS = (
    "EXTRAS",
    "FFMPEG_FLAVOR",
    "WITH_GIT",
    "WITH_UV",
    "SLIM_SITE_PACKAGES",
    "STRIP_SO",
    "NLTK_DATA_PROFILE",
)
# The groups that were base dependencies before the image-size split, so the
# default variant ships what earlier releases did.
STANDARD_EXTRAS = "sandbox,billing,ops,gcp,langchain,rabbitmq,localizer"


def resolve_variant(**env: str) -> subprocess.CompletedProcess:
    """Source docker/image-variant.sh as a RUN step does; print what it exports."""
    clean = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), **env}
    names = " ".join(f"{n}=${n}" for n in ("IMAGE_VARIANT", *VARIANT_ARGS))
    return subprocess.run(
        ["sh", "-c", f'. "$0" && echo {names}', str(VARIANT_SCRIPT)],
        env=clean,
        capture_output=True,
        text=True,
        check=False,
    )


def resolved(**env: str) -> dict[str, str]:
    proc = resolve_variant(**env)
    assert proc.returncode == 0, proc.stderr
    return dict(pair.split("=", 1) for pair in proc.stdout.split())


def _load_module(path: Path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class BackendVariants(unittest.TestCase):
    def test_standard_is_the_default_and_feature_complete(self):
        expected = {
            "IMAGE_VARIANT": "standard",
            "EXTRAS": STANDARD_EXTRAS,
            "FFMPEG_FLAVOR": "debian",
            "WITH_GIT": "true",
            "WITH_UV": "true",
            "SLIM_SITE_PACKAGES": "0",
            "STRIP_SO": "0",
            "NLTK_DATA_PROFILE": "full",
        }
        self.assertEqual(resolved(), expected)
        self.assertEqual(resolved(IMAGE_VARIANT=""), expected)
        self.assertEqual(resolved(IMAGE_VARIANT="standard"), expected)

    def test_slim_is_the_lean_build(self):
        self.assertEqual(
            resolved(IMAGE_VARIANT="slim"),
            {
                "IMAGE_VARIANT": "slim",
                "EXTRAS": "",
                "FFMPEG_FLAVOR": "minimal",
                "WITH_GIT": "false",
                "WITH_UV": "false",
                "SLIM_SITE_PACKAGES": "1",
                "STRIP_SO": "1",
                "NLTK_DATA_PROFILE": "minimal",
            },
        )

    def test_an_explicit_per_feature_arg_wins(self):
        got = resolved(IMAGE_VARIANT="slim", EXTRAS="sandbox", WITH_GIT="true")
        self.assertEqual((got["EXTRAS"], got["WITH_GIT"]), ("sandbox", "true"))
        self.assertEqual(got["WITH_UV"], "false")  # the rest keep slim's value
        got = resolved(EXTRAS="none", WITH_UV="false", FFMPEG_FLAVOR="none")
        self.assertEqual(
            (got["EXTRAS"], got["WITH_UV"], got["FFMPEG_FLAVOR"]), ("", "false", "none")
        )
        self.assertEqual(got["WITH_GIT"], "true")

    def test_unknown_values_fail_the_build(self):
        for env in (
            {"IMAGE_VARIANT": "lean"},
            {"FFMPEG_FLAVOR": "static"},
            {"WITH_GIT": "yes"},
            {"WITH_UV": "1"},
            {"SLIM_SITE_PACKAGES": "true"},
            {"STRIP_SO": "no"},
            {"NLTK_DATA_PROFILE": "tiny"},
            {"EXTRAS": "sandbox; rm -rf /"},
        ):
            with self.subTest(**env):
                proc = resolve_variant(**env)
                self.assertNotEqual(proc.returncode, 0)
                self.assertTrue(proc.stderr.strip())

    def test_standard_extras_exist_and_the_smoke_test_checks_them(self):
        pyproject = (ROOT / "futureagi" / "pyproject.toml").read_text(encoding="utf-8")
        optional = pyproject.split("[project.optional-dependencies]", 1)[1]
        groups = set(re.findall(r"^([a-z][a-z0-9-]*) = \[", optional, re.M))
        smoke = _load_module(ROOT / "futureagi" / "docker" / "runtime_smoke.py")
        for group in STANDARD_EXTRAS.split(","):
            with self.subTest(group=group):
                self.assertIn(group, groups)
                self.assertTrue(smoke.EXTRA_MODULES[group])

    def test_published_image_checks_import_every_standard_extra(self):
        # The release leg and verify-image-contents.sh re-check the pushed
        # image; each imports a module of every group the smoke test covers.
        smoke = _load_module(ROOT / "futureagi" / "docker" / "runtime_smoke.py")
        for path in (
            WORKFLOWS / "release-images.yml",
            ROOT / "scripts" / "verify-image-contents.sh",
        ):
            text = path.read_text(encoding="utf-8")
            (probe,) = re.findall(r'"import (daytona, [a-z0-9_, ]+)"', text)
            imported = {name.strip() for name in probe.split(",")}
            for group in STANDARD_EXTRAS.split(","):
                with self.subTest(path=path.name, group=group):
                    self.assertTrue(imported & set(smoke.EXTRA_MODULES[group]))

    def test_ffmpeg_stages_match_the_variant_defaults(self):
        # FROM picks the ffmpeg stage before any RUN could source the script.
        aliases = {}
        for keyword, rest in instructions(BACKEND):
            words = rest.split()
            if keyword == "FROM" and len(words) == 3 and words[1].upper() == "AS":
                aliases[words[2]] = words[0]
        self.assertEqual(aliases["ffmpeg"], "ffmpeg-${FFMPEG_FLAVOR:-${IMAGE_VARIANT}}")
        for variant in ("standard", "slim"):
            with self.subTest(variant=variant):
                flavor = resolved(IMAGE_VARIANT=variant)["FFMPEG_FLAVOR"]
                self.assertEqual(aliases[f"ffmpeg-{variant}"], f"ffmpeg-{flavor}")
        for flavor in ("minimal", "debian", "none"):
            self.assertIn(f"ffmpeg-{flavor}", aliases)

    def test_variant_args_default_to_the_variant(self):
        steps = instructions(BACKEND)
        first_from = next(i for i, (k, _) in enumerate(steps) if k == "FROM")
        global_args = dict(
            rest.partition("=")[::2] for k, rest in steps[:first_from] if k == "ARG"
        )
        self.assertEqual(global_args["IMAGE_VARIANT"], "standard")
        for keyword, rest in steps:
            if keyword != "ARG":
                continue
            name, has_default, default = rest.partition("=")
            if name in VARIANT_ARGS and has_default:
                with self.subTest(arg=name):
                    self.assertEqual(default, '""', "empty = the variant's value")
        declared = {rest.partition("=")[0] for k, rest in steps if k == "ARG"}
        self.assertLessEqual(set(VARIANT_ARGS), declared)

    def test_every_step_reading_a_variant_arg_resolves_it_first(self):
        for keyword, rest in instructions(BACKEND):
            read = [n for n in VARIANT_ARGS if f"${n}" in rest or f"${{{n}}}" in rest]
            if keyword == "RUN" and read:
                with self.subTest(run=rest[:60]):
                    self.assertRegex(
                        rest, r"^(set -eux;\s+)?\.\s+\S*/image-variant\.sh", read
                    )

    def test_the_dependency_layer_is_shared_by_both_variants(self):
        # No variant ARG is in scope when requirements.txt installs, so both
        # variants reuse one cached layer (and one registry cache).
        builder = instructions(BACKEND)
        start = next(
            i
            for i, (k, rest) in enumerate(builder)
            if k == "FROM" and rest.endswith("AS builder")
        )
        install = next(
            i
            for i, (k, rest) in enumerate(builder)
            if i > start and k == "RUN" and "-r requirements.txt" in rest
        )
        in_scope = [rest for k, rest in builder[start:install] if k == "ARG"]
        self.assertEqual(in_scope, [])

    def test_service_version_is_image_metadata(self):
        stage = final_stage(BACKEND)
        version_arg = next(
            i
            for i, (k, rest) in enumerate(stage)
            if k == "ARG" and rest.startswith("VERSION")
        )
        env = [
            i
            for i, (k, rest) in enumerate(stage)
            if k == "ENV" and rest == "SERVICE_VERSION=${VERSION}"
        ]
        self.assertEqual(len(env), 1)
        self.assertGreater(env[0], version_arg)

    def test_the_simulation_runner_refuses_a_base_without_sandbox_sdks(self):
        steps = final_stage(ROOT / "Dockerfile.simulation-runner")
        runs = [rest for keyword, rest in steps if keyword == "RUN"]
        self.assertEqual(runs[0], 'python -c "import daytona, e2b" && git --version')
        self.assertIn("/opt/alk-venv", runs[1])


# ---------------------------------------------------------------------------
# Backend images built FROM futureagi/future-agi-base (docker-compose
# .distributed.dev.yml builds the root Dockerfile)
# ---------------------------------------------------------------------------

FUTURE_AGI_BASE_DOCKERFILES = ("Dockerfile", "futureagi/Dockerfile")


def _normalized(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def project_pins() -> dict[str, set[str]]:
    """Package -> the exact versions requirements.txt and the extras pin."""
    pins: dict[str, set[str]] = {}
    requirements = (ROOT / "futureagi" / "requirements.txt").read_text("utf-8")
    for name, version in re.findall(
        r"(?m)^([A-Za-z0-9_.-]+)(?:\[[^\]]*\])?==(\S+)", requirements
    ):
        pins.setdefault(_normalized(name), set()).add(version)
    pyproject = (ROOT / "futureagi" / "pyproject.toml").read_text("utf-8")
    for name, version in re.findall(
        r'"([A-Za-z0-9_.-]+)(?:\[[^\]]*\])?==([^";\s]+)', pyproject
    ):
        pins.setdefault(_normalized(name), set()).add(version)
    return pins


class FutureAgiBaseDockerfiles(unittest.TestCase):
    """The packages these Dockerfiles install over future-agi-base v1.0.4
    until a base rebuilt from requirements.txt replaces it."""

    def pip_install(self, name: str) -> tuple[int, str]:
        steps = instructions(ROOT / name)
        ((index, rest),) = [
            (index, rest)
            for index, (keyword, rest) in enumerate(steps)
            if keyword == "RUN" and rest.startswith("pip install")
        ]
        return index, rest

    def test_the_pins_are_installed_before_the_source_copy(self):
        for name in FUTURE_AGI_BASE_DOCKERFILES:
            with self.subTest(dockerfile=name):
                index, _ = self.pip_install(name)
                steps = instructions(ROOT / name)
                first_copy = next(
                    i for i, (keyword, _) in enumerate(steps) if keyword == "COPY"
                )
                self.assertLess(index, first_copy)

    def test_the_pins_match_the_project(self):
        pins = project_pins()
        for name in FUTURE_AGI_BASE_DOCKERFILES:
            _, rest = self.pip_install(name)
            mirrored = re.findall(r'"([A-Za-z0-9_.-]+)(?:\[[^\]]*\])?==([^"]+)"', rest)
            self.assertTrue(mirrored)
            for package, version in mirrored:
                with self.subTest(dockerfile=name, package=package):
                    self.assertEqual(pins.get(_normalized(package)), {version})


# ---------------------------------------------------------------------------
# Build-time scripts of the backend image (futureagi/docker)
# ---------------------------------------------------------------------------

SMOKE = ROOT / "futureagi" / "docker" / "runtime_smoke.py"
PRUNE = ROOT / "futureagi" / "docker" / "prune_test_dirs.py"
MP3_ENCODERS = 'echo " A..... libmp3lame           libmp3lame MP3 (MPEG audio layer 3)"'


class RuntimeSmoke(unittest.TestCase):
    """runtime_smoke.py against stand-in modules, NLTK data and discovery
    documents, and fake uv/git/ffmpeg/ffprobe on PATH."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.bin = Path(tmp.name) / "bin"
        self.bin.mkdir()
        self.tool_log = Path(tmp.name) / "tools.log"
        self.smoke = _load_module(SMOKE)
        self.missing: set[str] = set()
        self.docs: list[str] = []
        self.resources: list[str] = []

    def tool(self, name: str, script: str = "") -> None:
        path = self.bin / name
        path.write_text(
            f'#!/bin/sh\necho "{name} $*" >> "{self.tool_log}"\n{script}\n',
            encoding="utf-8",
        )
        path.chmod(0o755)

    def ran(self) -> list[str]:
        if not self.tool_log.exists():
            return []
        return self.tool_log.read_text(encoding="utf-8").splitlines()

    def get_static_doc(self, api: str, version: str):
        self.docs.append(f"{api}.{version}")
        return None if f"{api}.{version}" in self.missing else "{}"

    def find(self, resource: str) -> str:
        self.resources.append(resource)
        if resource in self.missing:
            raise LookupError(resource)
        return resource

    def modules(self) -> dict:
        extras = [m for group in self.smoke.EXTRA_MODULES.values() for m in group]
        fakes = {
            name: None if name in self.missing else types.ModuleType(name)
            for name in (*self.smoke.MODULES, *extras)
        }
        discovery_cache = types.ModuleType("googleapiclient.discovery_cache")
        discovery_cache.get_static_doc = self.get_static_doc
        nltk = types.ModuleType("nltk")
        nltk.data = types.SimpleNamespace(find=self.find)
        fakes.update(
            {
                "googleapiclient": types.ModuleType("googleapiclient"),
                "googleapiclient.discovery_cache": discovery_cache,
                "nltk": nltk,
            }
        )
        return fakes

    def run_smoke(self, env: dict, missing=(), as_script: bool = False):
        """(exit code or message, stdout) of one run, as the RUN step sees it."""
        self.missing = set(missing)
        self.docs.clear()
        self.resources.clear()
        out = io.StringIO()
        with (
            mock.patch.dict(os.environ, {"PATH": str(self.bin), **env}, clear=True),
            mock.patch.dict(sys.modules, self.modules()),
            contextlib.redirect_stdout(out),
        ):
            try:
                if as_script:
                    runpy.run_path(str(SMOKE), run_name="__main__")
                else:
                    self.smoke.main()
            except SystemExit as exit_:
                return exit_.code, out.getvalue()
        return 0, out.getvalue()

    def test_a_feature_complete_standard_runtime_passes(self):
        for name in ("uv", "git", "ffprobe"):
            self.tool(name)
        self.tool("ffmpeg", MP3_ENCODERS)

        code, out = self.run_smoke(resolved(), as_script=True)

        self.assertEqual((code, out), (0, "runtime smoke test: OK\n"))
        self.assertEqual(
            self.docs,
            ["servicecontrol.v1", "cloudcommerceprocurement.v1", "drive.v3"],
        )
        self.assertEqual(
            self.resources,
            [*self.smoke.NLTK_RESOURCES, *self.smoke.NLTK_FULL_RESOURCES],
        )
        self.assertEqual(
            self.ran(),
            [
                "ffmpeg -hide_banner -version",
                "ffprobe -hide_banner -version",
                "ffmpeg -hide_banner -encoders",
            ],
        )

    def test_a_slim_runtime_needs_no_extras_uv_git_or_full_data(self):
        self.tool("ffprobe")
        self.tool("ffmpeg", MP3_ENCODERS)
        extras = {m for group in self.smoke.EXTRA_MODULES.values() for m in group}

        code, out = self.run_smoke(
            resolved(IMAGE_VARIANT="slim"),
            missing={*extras, *self.smoke.NLTK_FULL_RESOURCES, "drive.v3"},
        )

        self.assertEqual((code, out), (0, "runtime smoke test: OK\n"))
        self.assertEqual(
            self.docs, ["servicecontrol.v1", "cloudcommerceprocurement.v1"]
        )
        self.assertEqual(self.resources, list(self.smoke.NLTK_RESOURCES))

    def test_every_missing_piece_is_reported_and_fails_the_build(self):
        self.tool("git")
        stderr = "0" * 600 + " libavcodec.so.61: cannot open shared object file"
        self.tool("ffprobe", f'printf "%s\\n" "{stderr}" >&2; exit 127')
        self.tool("ffmpeg", 'echo " A..... libshine MP3 (MPEG audio layer 3)"')
        # The standard variant, but only one extra group and git not wanted.
        env = {**resolved(), "EXTRAS": "billing", "WITH_GIT": "false"}

        code, out = self.run_smoke(
            env,
            missing={"litellm", "stripe", "daytona", "drive.v3", "corpora/omw-1.4/"},
        )

        self.assertEqual(out, "")
        header, *failures = code.split("\n  ")
        self.assertEqual(header, "runtime smoke test FAILED:")
        self.assertTrue(failures[0].startswith("import litellm: ModuleNotFoundError"))
        # daytona is missing too, but the sandbox group was not asked for.
        self.assertTrue(failures[1].startswith("import stripe: ModuleNotFoundError"))
        self.assertEqual(
            failures[2:],
            [
                "googleapiclient discovery document drive.v3 missing",
                "NLTK resource corpora/omw-1.4/ missing",
                "uv on PATH: None, WITH_UV=True",
                f"git on PATH: '{self.bin / 'git'}', WITH_GIT=False",
                "ffprobe -version: " + (stderr + "\n")[-500:],
                "ffmpeg has no libmp3lame encoder",
            ],
        )

    def test_every_core_module_is_imported(self):
        self.tool("ffprobe")
        self.tool("ffmpeg", MP3_ENCODERS)
        # nltk stays: its stand-in also serves the data lookups.
        core = [m for m in self.smoke.MODULES if m != "nltk"]

        code, _ = self.run_smoke(resolved(IMAGE_VARIANT="slim"), missing=core)

        reported = [
            line.split(":", 1)[0].removeprefix("import ")
            for line in code.split("\n  ")[1:]
        ]
        self.assertEqual(reported, core)

    def test_ffmpeg_none_means_no_ffmpeg_on_path(self):
        env = resolved(IMAGE_VARIANT="slim", FFMPEG_FLAVOR="none")
        self.assertEqual(self.run_smoke(env)[0], 0)

        self.tool("ffmpeg", MP3_ENCODERS)
        code, _ = self.run_smoke(env)

        self.assertEqual(
            code,
            "runtime smoke test FAILED:\n  FFMPEG_FLAVOR=none but ffmpeg is on PATH",
        )
        self.assertEqual(self.ran(), [])


class PruneTestDirs(unittest.TestCase):
    """prune_test_dirs.py on a stand-in site-packages."""

    REMOVED = ("pandas/tests", "numpy/linalg/tests", "tests", "selfref/tests")
    KEPT = (
        "elevenlabs/conversational_ai/tests",
        "absref/api/tests",
        "strref/api/tests",
        "lazy/tests",
        "parentref/tests",
    )

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.sp = Path(tmp.name) / "site-packages"
        files = {
            # Test suites nothing imports.
            "pandas/core/frame.py": "import numpy\n",
            "pandas/tests/test_frame.py": "x" * 1_200_000,
            "numpy/__init__.py": "from . import linalg\n",
            "numpy/linalg/tests/test_linalg.py": "x" * 300_000,
            "tests/__init__.py": "",
            # References that do not count: test code and helpers of the same
            # distribution, a relative import above its top level, and
            # another distribution.
            "selfref/conftest.py": "import selfref.tests\n",
            "selfref/testing.py": "from selfref.tests import fixtures\n",
            "selfref/deep.py": "from ...tests import fixtures\n",
            "selfref/tests/test_a.py": "from selfref.tests import fixtures\n",
            "other/uses_pandas.py": "import pandas.tests\n",
            # `tests` subpackages the runtime imports.
            "elevenlabs/conversational_ai/__init__.py": "from .tests import Client\n",
            "elevenlabs/conversational_ai/tests/__init__.py": "class Client: ...\n",
            "absref/client.py": "import absref.api.tests\n",
            "absref/api/tests/__init__.py": "",
            "strref/registry.py": 'PLUGINS = ["strref.api.tests"]\n',
            "strref/api/tests/__init__.py": "",
            "lazy/__init__.py": '_SUBMODULES = {"TestsApi": ".tests"}\n',
            "lazy/tests/__init__.py": "",
            "parentref/sub/mod.py": "from ..tests import helper\n",
            "parentref/tests/helper.py": "",
            # `test` is never pruned: django/test is a runtime package.
            "django/test/client.py": "",
        }
        for rel, text in files.items():
            path = self.sp / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        # Unreadable modules are skipped; symlinks are not counted as freed.
        (self.sp / "pandas" / "broken.py").symlink_to(self.sp / "missing.py")
        self.outside = Path(tmp.name) / "big.bin"
        self.outside.write_bytes(b"\0" * 5_000_000)
        (self.sp / "pandas" / "tests" / "big.bin").symlink_to(self.outside)

    def run_prune(self, *args: str) -> list[str]:
        out = io.StringIO()
        with (
            mock.patch.object(sys, "argv", [str(PRUNE), str(self.sp), *args]),
            contextlib.redirect_stdout(out),
        ):
            runpy.run_path(str(PRUNE), run_name="__main__")
        return out.getvalue().splitlines()

    def assert_keeping(self, lines: list[str]) -> None:
        self.assertEqual(
            sorted(lines),
            sorted(
                f"prune_test_dirs: keeping {os.path.join(*rel.split('/'))} "
                "(imported by its package)"
                for rel in self.KEPT
            ),
        )

    def test_removes_suites_and_keeps_runtime_tests_packages(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            _load_module(PRUNE).main(str(self.sp))
        lines = out.getvalue().splitlines()

        self.assert_keeping(lines[:-1])
        self.assertEqual(
            lines[-1], "prune_test_dirs: removed 4 test directories (1.5 MB), kept 5"
        )
        for rel in self.REMOVED:
            self.assertFalse((self.sp / rel).exists(), rel)
        for rel in (*self.KEPT, "django/test", "pandas/core/frame.py"):
            self.assertTrue((self.sp / rel).exists(), rel)
        self.assertTrue(self.outside.exists())

    def test_dry_run_reports_without_removing(self):
        lines = self.run_prune("--dry-run")

        self.assert_keeping(lines[:-1])
        self.assertEqual(
            lines[-1],
            "prune_test_dirs: would remove 4 test directories (1.5 MB), kept 5",
        )
        for rel in (*self.REMOVED, *self.KEPT):
            self.assertTrue((self.sp / rel).is_dir(), rel)


class NonRootBackend(unittest.TestCase):
    def test_every_path_the_app_writes_belongs_to_appuser(self):
        # The same list the Helm chart mounts an emptyDir on (read-only root).
        env_tpl = (
            ROOT / "deploy" / "helm" / "futureagi" / "templates" / "_env.tpl"
        ).read_text(encoding="utf-8")
        block = env_tpl.split('define "futureagi.python.writablePaths"', 1)[1]
        block = block.split("{{- end -}}", 1)[0]
        paths = re.findall(r":\s*/app/backend/(\S+)", block)
        self.assertIn("tfc/logs", paths)
        final = [rest for keyword, rest in final_stage(BACKEND) if keyword == "RUN"][-1]
        mkdir, chown = final.split("chown appuser:appuser", 1)
        for path in paths:
            with self.subTest(path=path):
                self.assertIn(path, chown.split())
                if path.startswith("tfc/"):
                    self.assertIn(path, mkdir.split())


class VerifyImageContents(unittest.TestCase):
    """scripts/verify-image-contents.sh picks its checks by variant; a fake
    `docker` answers like the image it is asked about."""

    FAKE_DOCKER = r"""#!/bin/sh
printf '%s\n' "$*" >> "$DOCKER_LOG"
case "$*" in
  *-slim*"command -v uv"*|*-slim*"command -v git"*) exit 1 ;;
  *-slim*"git --version"*|*-slim*"import daytona"*) exit 1 ;;
  *-slim*omw-1.4*|*-slim*get_static_doc*) exit 1 ;;
  *COPYING.LGPLv2.1*) case "$*" in *-slim*) exit 0 ;; *) exit 1 ;; esac ;;
  *"test -e /app/backend/ee/cloud"*) exit 1 ;;
esac
exit 0
"""

    def run_script(self, image: str, **env: str) -> subprocess.CompletedProcess:
        with tempfile.TemporaryDirectory() as tmp:
            fake = Path(tmp) / "docker"
            fake.write_text(self.FAKE_DOCKER, encoding="utf-8")
            fake.chmod(0o755)
            environment = {
                "PATH": f"{tmp}:{os.environ.get('PATH', '/usr/bin:/bin')}",
                "DOCKER_LOG": str(Path(tmp) / "log"),
                "OSS_IMAGE": image,
                **env,
            }
            proc = subprocess.run(
                [
                    "bash",
                    str(ROOT / "scripts" / "verify-image-contents.sh"),
                    "v9.9.9",
                    "oss",
                ],
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
            log = Path(tmp) / "log"
            proc.docker_log = log.read_text(encoding="utf-8") if log.exists() else ""
            return proc

    def test_the_default_tag_is_checked_as_feature_complete(self):
        proc = self.run_script("futureagi/future-agi:v9.9.9")
        self.assertEqual(proc.returncode, 0, proc.stdout)
        self.assertIn("(standard variant)", proc.stdout)
        for check in ("Has uv", "Has git", "Has every NLTK package"):
            self.assertIn(f"PASS: {check}", proc.stdout)
        self.assertNotIn("No uv in the runtime image", proc.stdout)
        self.assertIn("--user 1000:1000", proc.docker_log)

    def test_a_slim_tag_is_checked_as_lean(self):
        for image, env in (
            ("futureagi/future-agi:v9.9.9-slim", {}),
            ("futureagi/future-agi:v9.9.9-slim@sha256:" + "0" * 64, {}),
            ("futureagi/future-agi:local-slim", {"OSS_VARIANT": "slim"}),
        ):
            with self.subTest(image=image):
                proc = self.run_script(image, **env)
                self.assertEqual(proc.returncode, 0, proc.stdout)
                self.assertIn("(slim variant)", proc.stdout)
                self.assertIn("PASS: No uv in the runtime image", proc.stdout)
                self.assertNotIn("Has uv", proc.stdout)

    def test_a_slim_image_checked_as_standard_fails(self):
        proc = self.run_script(
            "futureagi/future-agi:v9.9.9-slim", OSS_VARIANT="standard"
        )
        self.assertEqual(proc.returncode, 1)
        self.assertIn("FAIL: Has uv", proc.stdout)

    def test_an_unknown_variant_is_a_usage_error(self):
        proc = self.run_script("futureagi/future-agi:v9.9.9", OSS_VARIANT="lean")
        self.assertEqual(proc.returncode, 2)


VERIFY_BINARIES = ROOT / "deploy" / "standalone" / "bin" / "verify-binaries"
BUNDLED_BINARIES = (
    "fi-collector",
    "agentcc-gateway",
    "temporal",
    "minio",
    "redis-server",
    "nginx",
    "supervisord",
)


STARTS = b"#!/bin/sh\nexit 0\n"  # a binary that starts


def elf_header(machine: int) -> bytes:
    """The first 20 bytes of an ELF file for `machine` (e_machine)."""
    return b"\x7fELF" + bytes(14) + machine.to_bytes(2, "little")


class StandaloneVerifyBinaries(unittest.TestCase):
    """deploy/standalone/bin/verify-binaries, which standalone-ci.yml and
    release-images.yml run inside futureagi/standalone, on an x86_64 host
    with fake binaries."""

    def run_script(self, binaries: dict[str, bytes]) -> tuple[object, str]:
        """binaries: name -> file content. Returns the exit code and stdout."""
        with tempfile.TemporaryDirectory() as tmp:
            for name, content in binaries.items():
                path = Path(tmp) / name
                path.write_bytes(content)
                path.chmod(0o755)
            modules = {
                name: types.ModuleType(name)
                for name in ("channels_redis", "falcon", "gunicorn")
            }
            out = io.StringIO()
            with (
                mock.patch.dict(os.environ, {"PATH": tmp}),
                mock.patch.dict(sys.modules, modules),
                mock.patch("platform.machine", return_value="x86_64"),
                contextlib.redirect_stdout(out),
                self.assertRaises(SystemExit) as exited,
            ):
                runpy.run_path(str(VERIFY_BINARIES), run_name="__main__")
            return exited.exception.code, out.getvalue()

    def test_binaries_that_start_pass(self):
        code, out = self.run_script(dict.fromkeys(BUNDLED_BINARIES, STARTS))
        self.assertIsNone(code)
        self.assertEqual(out.count("ok  "), len(BUNDLED_BINARIES))

    def test_a_missing_foreign_or_broken_binary_fails(self):
        binaries = dict.fromkeys(BUNDLED_BINARIES, STARTS)
        del binaries["nginx"]
        binaries["minio"] = elf_header(0xB7)  # aarch64 on an x86_64 host
        binaries["temporal"] = elf_header(0x3E)  # right machine, cannot exec
        code, out = self.run_script(binaries)
        failures = code.splitlines()
        self.assertEqual(len(failures), 3, code)
        self.assertIn("nginx: not on PATH", failures)
        self.assertIn("minio: built for another architecture", failures)
        self.assertTrue(any(line.startswith("temporal: ") for line in failures))
        self.assertEqual(out.count("ok  "), len(BUNDLED_BINARIES) - 3)

    def test_standalone_ci_and_the_release_run_the_one_script(self):
        self.assertTrue(os.access(VERIFY_BINARIES, os.X_OK))
        ci = (WORKFLOWS / "standalone-ci.yml").read_text(encoding="utf-8")
        release = (WORKFLOWS / "release-images.yml").read_text(encoding="utf-8")
        self.assertIn(
            "--entrypoint /opt/futureagi/bin/verify-binaries"
            ' "$REGISTRY/futureagi/standalone:ci"',
            ci,
        )
        self.assertIn("verify-command: /opt/futureagi/bin/verify-binaries", release)
        for workflow in (ci, release):
            self.assertNotIn("0x3E", workflow)  # no inline copy of the script


BASE_PIN = ROOT / "scripts" / "code-executor-base-pin.sh"


class CodeExecutorBasePin(unittest.TestCase):
    """scripts/code-executor-base-pin.sh, the one parse of the base tag
    futureagi/code-executor/Dockerfile pins (backend-ci.yml, release-images.yml)."""

    def pin(self, arg_line: str | None = None) -> subprocess.CompletedProcess:
        args = ["bash", str(BASE_PIN)]
        with tempfile.TemporaryDirectory() as tmp:
            if arg_line is not None:
                dockerfile = Path(tmp) / "Dockerfile"
                dockerfile.write_text(
                    "# ARG CODE_EXECUTOR_BASE=futureagi/code-executor-base:v0.0.1\n"
                    f"{arg_line}\nFROM ${{CODE_EXECUTOR_BASE}}\n",
                    encoding="utf-8",
                )
                args.append(str(dockerfile))
            return subprocess.run(
                args, cwd=ROOT, capture_output=True, text=True, check=False
            )

    def test_the_committed_pin_is_a_version_tag(self):
        proc = self.pin()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        dockerfile = (ROOT / "futureagi" / "code-executor" / "Dockerfile").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            f"ARG CODE_EXECUTOR_BASE=futureagi/code-executor-base:{proc.stdout.strip()}",
            dockerfile,
        )

    def test_prints_the_version_of_an_immutable_pin(self):
        digest = "@sha256:" + "a" * 64
        for ref, version in (
            ("futureagi/code-executor-base:v1.2.3", "v1.2.3"),
            (f"futureagi/code-executor-base:v1.2.3{digest}", "v1.2.3"),
            ("futureagi/code-executor-base:v1.2.3-rc.1", "v1.2.3-rc.1"),
        ):
            with self.subTest(ref=ref):
                proc = self.pin(f"  ARG CODE_EXECUTOR_BASE={ref}")
                self.assertEqual((proc.returncode, proc.stdout), (0, f"{version}\n"))

    def test_anything_else_fails_with_an_annotation(self):
        for line in (
            "ARG CODE_EXECUTOR_BASE=futureagi/code-executor-base:latest",
            "ARG CODE_EXECUTOR_BASE=futureagi/code-executor-base:v1.2",
            "ARG CODE_EXECUTOR_BASE=someone/code-executor-base:v1.2.3",
            "ARG CODE_EXECUTOR_BASE",
            "ARG OTHER=futureagi/code-executor-base:v1.2.3",
        ):
            with self.subTest(line=line):
                proc = self.pin(line)
                self.assertEqual((proc.returncode, proc.stdout), (1, ""))
                self.assertIn("::error file=", proc.stderr)
                self.assertIn("pin ARG CODE_EXECUTOR_BASE=", proc.stderr)

    def test_backend_ci_and_the_release_run_the_one_script(self):
        for name in ("backend-ci.yml", "release-images.yml"):
            with self.subTest(workflow=name):
                text = (WORKFLOWS / name).read_text(encoding="utf-8")
                self.assertIn("=$(scripts/code-executor-base-pin.sh)", text)
                self.assertNotIn("CODE_EXECUTOR_BASE=([^[:space:]]+)", text)


# ---------------------------------------------------------------------------
# Health probes against a local server
# ---------------------------------------------------------------------------


class _Handler(http.server.BaseHTTPRequestHandler):
    hosts: list[str] = []

    def do_GET(self):  # noqa: N802 (http.server API)
        _Handler.hosts.append(self.headers.get("Host", ""))
        status = 200 if self.path in ("/health/", "/healthz", "/ok") else 503
        self.send_response(status)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *args):
        pass


class _Server:
    def __enter__(self):
        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        _Handler.hosts = []
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()


def _closed_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _load_backend_probe():
    spec = importlib.util.spec_from_file_location(
        "futureagi_healthcheck", ROOT / "futureagi" / "docker" / "healthcheck.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class BackendHealthcheck(unittest.TestCase):
    def setUp(self):
        self.probe = _load_backend_probe()
        clean = {
            k: v
            for k, v in os.environ.items()
            if k
            not in {
                "SERVICE_TYPE",
                "ENABLE_HTTP",
                "ENABLE_GRPC",
                "HEALTHCHECK_URL",
                "ALLOWED_HOSTS",
            }
        }
        # A proxy must never see the probe.
        clean.update(HTTP_PROXY="http://127.0.0.1:9", http_proxy="http://127.0.0.1:9")
        patcher = mock.patch.dict(os.environ, clean, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_role(self, server_port: int, pid1_is_entrypoint: bool = True, **env) -> int:
        os.environ.update(env)
        with (
            mock.patch.object(
                self.probe, "_pid1_runs_entrypoint", return_value=pid1_is_entrypoint
            ),
            mock.patch.object(self.probe, "HTTP_PORT", server_port),
            mock.patch.object(self.probe, "GRPC_PORT", server_port),
            mock.patch.object(self.probe, "FLOWER_PORT", server_port),
        ):
            return self.probe.main([])

    def test_the_api_role_probes_health(self):
        with _Server() as server:
            self.assertEqual(self.run_role(server.port), 0)
            self.assertEqual(
                self.run_role(server.port, SERVICE_TYPE="backend", ENABLE_HTTP="true"),
                0,
            )
        self.assertEqual(self.run_role(_closed_port()), 1)

    def test_grpc_only_and_flower_roles_probe_their_port(self):
        with _Server() as server:
            self.assertEqual(self.run_role(server.port, ENABLE_HTTP="false"), 0)
            self.assertEqual(self.run_role(server.port, SERVICE_TYPE="flower"), 0)
            self.assertEqual(self.run_role(server.port, SERVICE_TYPE="grpc"), 0)
        self.assertEqual(self.run_role(_closed_port(), ENABLE_HTTP="false"), 1)

    def test_roles_without_a_listener_are_healthy(self):
        port = _closed_port()
        for role in ("temporal-worker", "worker", "beat", "bootstrap"):
            with self.subTest(role=role):
                self.assertEqual(self.run_role(port, SERVICE_TYPE=role), 0)
        self.assertEqual(
            self.run_role(
                port, SERVICE_TYPE="backend", ENABLE_HTTP="false", ENABLE_GRPC="false"
            ),
            0,
        )
        # `entrypoint: python -m ...` replaces entrypoint.sh: nothing to probe.
        self.assertEqual(
            self.run_role(
                port, pid1_is_entrypoint=False, ENABLE_HTTP="true", ENABLE_GRPC="true"
            ),
            0,
        )

    def test_urls_must_all_answer_2xx(self):
        with _Server() as server:
            base = f"http://127.0.0.1:{server.port}"
            self.assertEqual(self.probe.main([f"{base}/ok", f"{base}/healthz"]), 0)
            self.assertEqual(self.probe.main([f"{base}/ok", f"{base}/bad"]), 1)
            os.environ["HEALTHCHECK_URL"] = f"{base}/ok, {base}/healthz"
            self.assertEqual(self.probe.main([]), 0)
            os.environ["HEALTHCHECK_URL"] = f"{base}/bad"
            self.assertEqual(self.probe.main([]), 1)

    def test_host_header_is_an_allowed_host(self):
        with _Server() as server:
            os.environ["ALLOWED_HOSTS"] = "*, .example.com,api.example.com"
            self.assertEqual(self.probe.main([f"http://127.0.0.1:{server.port}/ok"]), 0)
            self.assertEqual(_Handler.hosts[-1], "example.com")
            os.environ["ALLOWED_HOSTS"] = "*"
            self.assertEqual(self.probe.main([f"http://127.0.0.1:{server.port}/ok"]), 0)
            self.assertEqual(_Handler.hosts[-1], f"127.0.0.1:{server.port}")

    def test_pid1_detection_reads_proc(self):
        real_open = open

        def fake_open(path, *args, fake, **kwargs):
            return real_open(
                fake if path == "/proc/1/cmdline" else path, *args, **kwargs
            )

        with tempfile.NamedTemporaryFile("wb") as cmdline:
            for argv, expected in (
                (b"bash\0/app/backend/entrypoint.sh\0", True),
                (b"/sbin/docker-init\0--\0bash\0./entrypoint.sh\0", True),
                (b"python\0-m\0tracer.services.clickhouse.oss_cdc_install\0", False),
            ):
                cmdline.seek(0)
                cmdline.truncate()
                cmdline.write(argv)
                cmdline.flush()
                opener = functools.partial(fake_open, fake=cmdline.name)
                with mock.patch("builtins.open", opener):
                    self.assertEqual(self.probe._pid1_runs_entrypoint(), expected, argv)


@unittest.skipUnless(shutil.which("go"), "Go toolchain unavailable")
class GoProbe(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        source = Path(cls.tmp.name) / "main.go"
        source.write_text(
            probe_source(ROOT / "fi-collector" / "Dockerfile"), encoding="utf-8"
        )
        cls.binary = Path(cls.tmp.name) / "healthcheck"
        # As in the Dockerfiles: one file, standard library only, no go.mod.
        env = {
            k: v for k, v in os.environ.items() if k not in {"GOFLAGS", "GO111MODULE"}
        }
        env["CGO_ENABLED"] = "0"
        subprocess.run(
            ["go", "vet", str(source)],
            check=True,
            cwd=cls.tmp.name,
            env=env,
            capture_output=True,
        )
        subprocess.run(
            ["go", "build", "-o", str(cls.binary), str(source)],
            check=True,
            cwd=cls.tmp.name,
            env=env,
            capture_output=True,
        )

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def run_probe(self, *args: str, **env) -> int:
        environment = {k: v for k, v in os.environ.items() if k != "HEALTHCHECK_URL"}
        environment.update(env)
        return subprocess.run(
            [str(self.binary), *args], env=environment, capture_output=True, timeout=30
        ).returncode

    def test_2xx_is_healthy_anything_else_is_not(self):
        with _Server() as server:
            base = f"http://127.0.0.1:{server.port}"
            self.assertEqual(self.run_probe(f"{base}/healthz"), 0)
            self.assertEqual(self.run_probe(f"{base}/bad"), 1)
            self.assertEqual(self.run_probe(HEALTHCHECK_URL=f"{base}/healthz"), 0)
        self.assertEqual(
            self.run_probe(f"http://127.0.0.1:{_closed_port()}/healthz"), 1
        )
        self.assertEqual(self.run_probe(), 1)  # no URL: usage error

    @unittest.skipUnless(sys.platform.startswith("linux"), "needs /proc")
    def test_pid1_mismatch_skips_the_probe(self):
        # PID 1 here is the init of the test machine, never fi-collector.
        self.assertEqual(
            self.run_probe(
                "-pid1", "fi-collector", f"http://127.0.0.1:{_closed_port()}/healthz"
            ),
            0,
        )


# ---------------------------------------------------------------------------
# The UI's nginx security headers and caching (frontend image, Standalone, Helm)
# ---------------------------------------------------------------------------

SECURITY_HEADERS = ROOT / "frontend" / "security-headers.conf"
CHART_UI_CONFIGMAP = "deploy/helm/futureagi/templates/frontend/configmap.yaml"
# Each nginx config of the UI -> where it includes the headers from.
UI_NGINX_CONFIGS = {
    "frontend/nginx.conf": "/etc/nginx/security-headers.conf",
    "deploy/standalone/nginx.conf": "/etc/nginx/security-headers.conf",
    CHART_UI_CONFIGMAP: "/etc/nginx/futureagi/security-headers.conf",
}


def nginx_config(name: str) -> str:
    """The UI nginx config at ROOT / name, comments removed."""
    return "\n".join(
        line.split("#", 1)[0]
        for line in (ROOT / name).read_text(encoding="utf-8").splitlines()
    )


def nginx_blocks(text: str, keyword: str) -> list[str]:
    """The body of every `<keyword> ... { ... }` block, nested braces included."""
    bodies = []
    for match in re.finditer(rf"(?m)^\s*{keyword}\b[^{{;]*\{{", text):
        depth, start = 1, match.end()
        for index in range(start, len(text)):
            depth += {"{": 1, "}": -1}.get(text[index], 0)
            if depth == 0:
                bodies.append(text[start:index])
                break
    return bodies


class NginxSecurityHeaders(unittest.TestCase):
    """nginx drops the server-level add_header directives in a location that
    sets a header of its own, so each such location includes them again."""

    def test_every_location_with_headers_of_its_own_includes_them(self):
        for name, path in UI_NGINX_CONFIGS.items():
            with self.subTest(config=name):
                text = nginx_config(name)
                include = f"include {path};"
                (server,) = nginx_blocks(text, "server")
                self.assertIn(include, server.split("location", 1)[0])
                locations = nginx_blocks(server, "location")
                self.assertGreaterEqual(len(locations), 5)
                for body in locations:
                    if "add_header" in body:
                        self.assertIn(include, body)
                # One file holds them; no config spells a header out again.
                self.assertNotRegex(
                    text, r"add_header (X-Frame-Options|X-Content-Type-Options)"
                )

    def test_the_frontend_image_ships_them_and_standalone_copies_them_from_it(self):
        self.assertIn(
            ("COPY", "security-headers.conf /etc/nginx/security-headers.conf"),
            final_stage(ROOT / "frontend" / "Dockerfile"),
        )
        self.assertIn(
            (
                "COPY",
                "--from=frontend /etc/nginx/security-headers.conf"
                " /etc/nginx/security-headers.conf",
            ),
            final_stage(ROOT / "deploy" / "standalone" / "Dockerfile"),
        )
        self.assertFalse((ROOT / "deploy/standalone/security-headers.conf").exists())
        # The chart cannot read frontend/: it loads a copy that hack/check.sh
        # diffs against this file.
        configmap = (ROOT / CHART_UI_CONFIGMAP).read_text(encoding="utf-8")
        self.assertIn('.Files.Get "files/frontend/security-headers.conf"', configmap)

    def test_the_headers(self):
        directives = [
            line.strip()
            for line in SECURITY_HEADERS.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        names = [
            match.group(1)
            if (match := re.fullmatch(r'add_header ([A-Za-z-]+) "[^"]+" always;', line))
            else line
            for line in directives
        ]
        self.assertEqual(
            names,
            [
                "X-Content-Type-Options",
                "X-Frame-Options",
                "Referrer-Policy",
                "Permissions-Policy",
            ],
        )


class NginxCaching(unittest.TestCase):
    """Standalone and the chart restate frontend/nginx.conf's caching of the SPA
    by hand (deploy/standalone/nginx.conf: "Caching mirrors ...")."""

    maxDiff = None

    @staticmethod
    def caching(name: str) -> dict[str, list[str]]:
        """Each location's matcher -> its expires, Cache-Control and Pragma."""
        (server,) = nginx_blocks(nginx_config(name), "server")
        matchers = re.findall(r"(?m)^\s*location\b([^{;]*)\{", server)
        return {
            " ".join(matcher.split()): [
                " ".join(directive.split())
                for directive in re.findall(
                    r"(?m)^\s*((?:expires|add_header (?:Cache-Control|Pragma))\b[^;]*;)",
                    body,
                )
            ]
            for matcher, body in zip(
                matchers, nginx_blocks(server, "location"), strict=True
            )
        }

    def test_standalone_and_the_chart_cache_as_the_frontend_image_does(self):
        frontend = self.caching("frontend/nginx.conf")
        self.assertEqual(len(frontend), 5)
        self.assertTrue(all(frontend.values()))
        for name in ("deploy/standalone/nginx.conf", CHART_UI_CONFIGMAP):
            with self.subTest(config=name):
                copy = self.caching(name)
                self.assertEqual(
                    {matcher: copy.get(matcher) for matcher in frontend}, frontend
                )


# ---------------------------------------------------------------------------
# Workflows and docs
# ---------------------------------------------------------------------------


class Workflows(unittest.TestCase):
    def test_reusable_builds_pass_the_label_args(self):
        multiarch = (WORKFLOWS / "build-image-multiarch.yml").read_text(
            encoding="utf-8"
        )
        for arg, source in (
            ("VERSION", "version"),
            ("REVISION", "revision"),
            ("CREATED", "created"),
        ):
            self.assertIn(f"{arg}=${{{{ needs.prepare.outputs.{source} }}}}", multiarch)
        # Both architectures build the resolved commit, and the labels are checked.
        self.assertIn("ref: ${{ needs.prepare.outputs.revision }}", multiarch)
        self.assertIn("OCI labels (linux/${{ matrix.arch }})", multiarch)

        base = (WORKFLOWS / "base-image-publish.yml").read_text(encoding="utf-8")
        self.assertIn("VERSION=${{ inputs.version }}", base)
        self.assertIn("REVISION=${{ steps.source.outputs.revision }}", base)

    @unittest.skipUnless(HAVE_YAML, "PyYAML unavailable")
    def test_every_release_image_builds_through_the_multiarch_workflow(self):
        self.assertFalse((WORKFLOWS / "build-image.yml").exists())
        jobs = yaml_jobs(WORKFLOWS / "release-images.yml")
        for name, job in jobs.items():
            if "uses" in job:
                with self.subTest(job=name):
                    self.assertIn(
                        job["uses"],
                        {
                            "./.github/workflows/build-image-multiarch.yml",
                            "./.github/workflows/base-image-publish.yml",
                        },
                    )
        runner = jobs["simulation-runner"]
        self.assertEqual(
            runner["uses"], "./.github/workflows/build-image-multiarch.yml"
        )
        self.assertEqual(json.loads(runner["with"]["arches"]), ["amd64"])
        self.assertEqual(runner["permissions"]["packages"], "write")
        for workflow in WORKFLOWS.glob("*.y*ml"):
            with self.subTest(workflow=workflow.name):
                self.assertNotIn("build-image.yml", workflow.read_text("utf-8"))

    @unittest.skipUnless(HAVE_YAML, "PyYAML unavailable")
    def test_images_ci_runs_these_tests_on_the_files_they_read(self):
        import yaml

        workflow = yaml.safe_load(
            (WORKFLOWS / "images-ci.yml").read_text(encoding="utf-8")
        )
        runs = "\n".join(
            step.get("run", "") for step in workflow["jobs"]["conventions"]["steps"]
        )
        for module in ("test_image_standards.py", "test_image_size_budget.py"):
            self.assertIn(f"-p '{module}'", runs)
        self.assertIn("PyYAML", runs)
        triggers = workflow[True]  # YAML 1.1 reads the `on` key as true
        paths = triggers["pull_request"]["paths"]
        self.assertEqual(triggers["push"]["paths"], paths)
        for path in (
            "frontend/Dockerfile",
            "frontend/security-headers.conf",
            "deploy/standalone/bin/verify-binaries",
            "scripts/code-executor-base-pin.sh",
            "futureagi/requirements.txt",
            "deploy/images.toml",
            "scripts/docs_site.py",
            ".github/workflows/release-images.yml",
        ):
            with self.subTest(path=path):
                self.assertTrue(any(fnmatch.fnmatch(path, glob) for glob in paths))
        collector = (WORKFLOWS / "fi-collector-ci.yml").read_text(encoding="utf-8")
        self.assertNotIn("test_image_", collector)

    def test_standalone_ci_builds_every_image_with_the_label_args(self):
        ci = (WORKFLOWS / "standalone-ci.yml").read_text(encoding="utf-8")
        self.assertEqual(ci.count("${{ steps.labels.outputs.args }}"), 5)
        self.assertIn("OCI labels on every image", ci)

    def test_release_pins_the_simulation_runner_base(self):
        release = (WORKFLOWS / "release-images.yml").read_text(encoding="utf-8")
        # The default (feature-complete) backend, by the digest its job published.
        self.assertIn(
            "BACKEND_IMAGE=docker.io/futureagi/future-agi:"
            "${{ needs.guard.outputs.version }}@${{ needs.backend.outputs.digest }}",
            release,
        )
        self.assertIn("tag-suffix: -gpu", release)

    @unittest.skipUnless(HAVE_YAML, "PyYAML unavailable")
    def test_release_publishes_both_backend_variants(self):
        jobs = yaml_jobs(WORKFLOWS / "release-images.yml")
        default, slim = jobs["backend"]["with"], jobs["backend-slim"]["with"]
        for job in (default, slim):
            self.assertEqual(job["image"], "futureagi/future-agi")
            self.assertEqual(job["dockerfile"], "./futureagi/Dockerfile.oss")
            self.assertTrue(job["push-latest"])
        # The default tags stay the feature-complete variant that the EE and
        # cloud images `uv pip install` on top of.
        self.assertNotIn("IMAGE_VARIANT", default.get("build-args", ""))
        self.assertNotIn("tag-suffix", default)
        for probe in ("uv --version", "git --version", "import daytona, e2b"):
            self.assertIn(probe, default["verify-command"])
        self.assertEqual(slim["tag-suffix"], "-slim")
        self.assertEqual(slim["build-args"].split(), ["IMAGE_VARIANT=slim"])
        # futureagi/standalone is FROM the -slim digest; nothing else is.
        self.assertEqual(
            jobs["standalone-inputs"]["needs"], ["guard", "build", "backend-slim"]
        )
        pin = jobs["standalone-inputs"]["steps"][-1]["run"]
        self.assertIn('pin BACKEND_IMAGE futureagi/future-agi "${VERSION}-slim"', pin)
        self.assertNotIn("backend-slim", jobs["simulation-runner"]["needs"])
        self.assertIn("backend-slim", jobs["size-report"]["needs"])

    @unittest.skipUnless(HAVE_YAML, "PyYAML unavailable")
    def test_standalone_ci_builds_the_slim_backend(self):
        ci = (WORKFLOWS / "standalone-ci.yml").read_text(encoding="utf-8")
        steps = yaml_jobs(WORKFLOWS / "standalone-ci.yml")["standalone-install"][
            "steps"
        ]
        backend = next(
            s
            for s in steps
            if s.get("with", {}).get("file") == "futureagi/Dockerfile.oss"
        )
        self.assertIn("IMAGE_VARIANT=slim", backend["with"]["build-args"].split())
        self.assertIn("check futureagi/future-agi -slim", ci)


class Docs(unittest.TestCase):
    """deploy/images.toml, from which the images page of the docs site is
    rendered, covers every released image and every label."""

    def setUp(self):
        with (ROOT / "deploy" / "images.toml").open("rb") as handle:
            self.data = tomllib.load(handle)

    def test_every_released_image_is_documented(self):
        release = (WORKFLOWS / "release-images.yml").read_text(encoding="utf-8")
        images = set(re.findall(r"\bimage:\s*(futureagi/[a-z0-9-]+)", release))
        self.assertIn("futureagi/future-agi-simulation-runner", images)
        described = {image["name"] for image in self.data["image"]}
        for image in images | {"futureagi/code-executor-base"}:
            with self.subTest(image=image):
                self.assertIn(image, described)

    def test_every_label_is_documented(self):
        documented = [label["name"] for label in self.data["label"]]
        self.assertEqual(len(documented), len(set(documented)))
        prefix = "org.opencontainers.image."
        expected = set(FIXED_LABELS) | {
            prefix + name for name in ("title", "description", "licenses")
        }
        self.assertEqual(set(documented), expected)
        values = {label["name"]: label["value"] for label in self.data["label"]}
        # A fixed literal label is stated as the Dockerfiles set it.
        for name, value in FIXED_LABELS.items():
            if "${" not in value and name != prefix + "documentation":
                with self.subTest(label=name):
                    self.assertIn(f"`{value}`", values[name])
        for license_ in (APACHE, WITH_EE):
            self.assertIn(f"`{license_}`", values[prefix + "licenses"])


if __name__ == "__main__":
    unittest.main()
