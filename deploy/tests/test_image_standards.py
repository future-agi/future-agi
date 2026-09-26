"""Image conventions (docs/images.md): OCI labels, base-image pinning, user,
HEALTHCHECK and STOPSIGNAL of every published image, the health probes, and
the build workflows that feed the labels."""

from __future__ import annotations

import functools
import http.server
import importlib.util
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"

APACHE = "Apache-2.0"
WITH_EE = "Apache-2.0 AND LicenseRef-FutureAGI-Enterprise-1.0"
INHERITED = object()  # set by the image this one is built FROM
HAVE_YAML = importlib.util.find_spec("yaml") is not None  # CI installs PyYAML

# Dockerfile -> what docs/images.md promises for its image.
IMAGES = {
    "futureagi/Dockerfile.oss": {
        "licenses": WITH_EE,
        "user": "root",
        "stopsignal": "SIGTERM",
        "healthcheck": True,
        "floating": set(),
    },
    "deploy/platform/Dockerfile": {
        "licenses": WITH_EE,
        "user": "root",
        "stopsignal": "SIGTERM",
        "healthcheck": True,
        # :latest for a quick local build; a release passes digests.
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
    # Root is a documented exception (docs/images.md); Helm runs it as 1000.
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
    "org.opencontainers.image.documentation": "https://github.com/future-agi/future-agi/blob/main/docs/images.md",
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
STANDARD_EXTRAS = "sandbox,billing,ops,gcp,langchain,rabbitmq"


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

        single = (WORKFLOWS / "build-image.yml").read_text(encoding="utf-8")
        self.assertIn("VERSION=${{ steps.version.outputs.version }}", single)
        self.assertIn("REVISION=${{ steps.source.outputs.revision }}", single)
        self.assertIn("CREATED=${{ steps.source.outputs.created }}", single)

        base = (WORKFLOWS / "base-image-publish.yml").read_text(encoding="utf-8")
        self.assertIn("VERSION=${{ inputs.version }}", base)
        self.assertIn("REVISION=${{ steps.source.outputs.revision }}", base)

    def test_platform_ci_builds_every_image_with_the_label_args(self):
        ci = (WORKFLOWS / "platform-ci.yml").read_text(encoding="utf-8")
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
        # futureagi/platform is FROM the -slim digest; nothing else is.
        self.assertEqual(
            jobs["platform-inputs"]["needs"], ["guard", "build", "backend-slim"]
        )
        pin = jobs["platform-inputs"]["steps"][-1]["run"]
        self.assertIn('pin BACKEND_IMAGE futureagi/future-agi "${VERSION}-slim"', pin)
        self.assertNotIn("backend-slim", jobs["simulation-runner"]["needs"])
        self.assertIn("backend-slim", jobs["size-report"]["needs"])

    @unittest.skipUnless(HAVE_YAML, "PyYAML unavailable")
    def test_platform_ci_builds_the_slim_backend(self):
        ci = (WORKFLOWS / "platform-ci.yml").read_text(encoding="utf-8")
        steps = yaml_jobs(WORKFLOWS / "platform-ci.yml")["default-install"]["steps"]
        backend = next(
            s
            for s in steps
            if s.get("with", {}).get("file") == "futureagi/Dockerfile.oss"
        )
        self.assertIn("IMAGE_VARIANT=slim", backend["with"]["build-args"].split())
        self.assertIn("check futureagi/future-agi -slim", ci)


class Docs(unittest.TestCase):
    def test_every_released_image_is_documented(self):
        release = (WORKFLOWS / "release-images.yml").read_text(encoding="utf-8")
        images = set(re.findall(r"\bimage:\s*(futureagi/[a-z0-9-]+)", release))
        self.assertIn("futureagi/future-agi-simulation-runner", images)
        docs = (ROOT / "docs" / "images.md").read_text(encoding="utf-8")
        for image in images | {"futureagi/code-executor-base"}:
            with self.subTest(image=image):
                self.assertIn(f"`{image}`", docs)
        for label in FIXED_LABELS:
            self.assertIn(label, docs)


if __name__ == "__main__":
    unittest.main()
