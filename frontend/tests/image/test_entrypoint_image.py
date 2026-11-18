"""The frontend image's entrypoint, run in the real image.

deploy/tests/test_image_standards.py (FrontendEntrypoint) runs
docker-entrypoint.sh with the host's sh; these tests run the built image, with
its busybox and nginx, and real certificates served to a TLS client.

Needs Docker, openssl and an image built from frontend/Dockerfile:

    FRONTEND_IMAGE=futureagi/frontend:dev \\
        python3 -m unittest discover -s frontend/tests/image -v

Every certificate and key is generated per test and never printed.
"""

from __future__ import annotations

import base64
import os
import shutil
import subprocess
import tempfile
import textwrap
import time
import unittest
import urllib.request
from pathlib import Path

IMAGE = os.environ.get("FRONTEND_IMAGE", "")
READ_ONLY_ROOT = [
    "--read-only",
    "--tmpfs",
    "/tmp",
    "--tmpfs",
    "/var/cache/nginx",
    "--tmpfs",
    "/run",
]


def run(argv, **kwargs):
    return subprocess.run(argv, text=True, capture_output=True, check=False, **kwargs)


def docker(*args):
    return run(["docker", *args])


class Certificates:
    """A test CA that issues leaves with exact validity dates (openssl ca).

    Dates are YYMMDDHHMMSSZ or YYYYMMDDHHMMSSZ; openssl encodes years before
    2050 as UTCTime and later ones as GeneralizedTime (RFC 5280).
    """

    def __init__(self, directory: Path):
        self.dir = directory / "ca"
        (self.dir / "issued").mkdir(parents=True)
        (self.dir / "index.txt").write_text("")
        (self.dir / "config").write_text(
            textwrap.dedent(
                f"""\
                [ca]
                default_ca = test
                [test]
                database = {self.dir}/index.txt
                new_certs_dir = {self.dir}/issued
                rand_serial = yes
                default_md = sha256
                policy = any
                unique_subject = no
                [any]
                commonName = supplied
                [leaf]
                basicConstraints = critical,CA:false
                subjectAltName = DNS:localhost
                [intermediate]
                basicConstraints = critical,CA:true
                """
            )
        )
        self.issuer_key, self.issuer_cert = self._pair("root")
        self._openssl(
            "req", "-x509", "-new", "-key", self.issuer_key, "-out", self.issuer_cert,
            "-days", "3650", "-subj", "/CN=test root",
        )

    def _openssl(self, *args):
        result = run(["openssl", *map(str, args)])
        if result.returncode:
            raise AssertionError(f"openssl {args[0]} failed: {result.stderr}")

    def _pair(self, name):
        key = self.dir / f"{name}.key"
        self._openssl(
            "genpkey", "-algorithm", "EC", "-pkeyopt", "ec_paramgen_curve:P-256",
            "-out", key,
        )
        return key, self.dir / f"{name}.crt"

    def issue(self, name, not_after, *, not_before="20000101000000Z",
              extension="leaf", issuer=None):
        """(PEM certificate, PEM key) of a new certificate with that notAfter."""
        key, cert = self._pair(name)
        csr = self.dir / f"{name}.csr"
        self._openssl("req", "-new", "-key", key, "-out", csr, "-subj", f"/CN={name}")
        issuer_key, issuer_cert = issuer or (self.issuer_key, self.issuer_cert)
        self._openssl(
            "ca", "-batch", "-notext", "-config", self.dir / "config",
            "-extensions", extension, "-cert", issuer_cert, "-keyfile", issuer_key,
            "-startdate", not_before, "-enddate", not_after,
            "-in", csr, "-out", cert,
        )
        return cert.read_text(), key.read_text()


def _der(tag: int, content: bytes) -> bytes:
    size = len(content)
    if size < 128:
        return bytes([tag, size]) + content
    length = size.to_bytes((size.bit_length() + 7) // 8, "big")
    return bytes([tag, 0x80 | len(length)]) + length + content


def unsigned_certificate(not_after: bytes, time_tag: int) -> str:
    """A PEM certificate nginx would refuse; enough for the entrypoint's check,
    which runs first. openssl cannot issue a GeneralizedTime before 2050."""
    oid = _der(0x30, _der(0x06, bytes.fromhex("2a8648ce3d040302")))
    name = _der(0x30, _der(0x31, _der(0x30, _der(0x06, b"\x55\x04\x03") + _der(0x0C, b"x"))))
    tbs = _der(
        0x30,
        _der(0xA0, _der(0x02, b"\x02")) + _der(0x02, b"\x01") + oid + name
        + _der(0x30, _der(0x17, b"000101000000Z") + _der(time_tag, not_after))
        + name + _der(0x30, b""),
    )
    der = _der(0x30, tbs + oid + _der(0x03, b"\x00" * 65))
    body = "\n".join(textwrap.wrap(base64.b64encode(der).decode(), 64))
    return f"-----BEGIN CERTIFICATE-----\n{body}\n-----END CERTIFICATE-----\n"


def key_body_lines(key_pem: str) -> list[str]:
    return [line for line in key_pem.splitlines() if line and "-----" not in line]


@unittest.skipUnless(
    IMAGE and shutil.which("docker") and shutil.which("openssl"),
    "needs FRONTEND_IMAGE, docker and openssl",
)
class ImageTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="frontend-image-"))
        self.tmp.chmod(0o755)
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.containers = []
        self.addCleanup(self._remove_containers)
        self.ca = Certificates(self.tmp)

    def _remove_containers(self):
        for container in self.containers:
            docker("rm", "-f", container)

    # A Kubernetes Secret volume: tls.crt -> ..data/tls.crt, ..data -> ..<version>.
    def secret_dir(self, cert, key, key_mode=0o400):
        directory = self.tmp / "tls"
        directory.mkdir(mode=0o755, exist_ok=True)
        for name in ("tls.crt", "tls.key"):
            link = directory / name
            if not link.is_symlink():
                link.symlink_to(f"..data/{name}")
        self.rotate(cert, key, key_mode)
        return directory

    def rotate(self, cert, key, key_mode=0o400):
        directory = self.tmp / "tls"
        version = directory / f"..{time.monotonic_ns()}"
        version.mkdir(mode=0o755)
        (version / "tls.crt").write_text(cert)
        (version / "tls.key").write_text(key)
        (version / "tls.crt").chmod(0o644)
        (version / "tls.key").chmod(key_mode)
        staging = directory / "..data_tmp"
        staging.symlink_to(version.name)
        os.replace(staging, directory / "..data")

    def tls_env(self, **extra):
        env = {
            "FRONTEND_TLS_ENABLED": "true",
            "FRONTEND_TLS_PORT": "8443",
            "FRONTEND_TLS_CERT_FILE": "/run/futureagi-tls/tls.crt",
            "FRONTEND_TLS_KEY_FILE": "/run/futureagi-tls/tls.key",
            "FRONTEND_TLS_RELOAD_INTERVAL": "1",
        }
        env.update(extra)
        return env

    def docker_args(self, env, options, tls_dir):
        args = list(options)
        for name, value in env.items():
            args += ["-e", f"{name}={value}"]
        if tls_dir:
            args += ["-v", f"{tls_dir}:/run/futureagi-tls:ro"]
        return args

    def run_to_exit(self, env=None, options=(), tls_dir=None, command=()):
        """(exit code, logs) of a container that should stop by itself; the
        exit code is None when it is still running after 20 seconds."""
        result = docker(
            "run", "-d", *self.docker_args(env or {}, options, tls_dir), IMAGE, *command
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        container = result.stdout.strip()
        self.containers.append(container)
        try:
            code = int(run(["docker", "wait", container], timeout=20).stdout.strip())
        except subprocess.TimeoutExpired:
            code = None
        return code, self.logs(container)

    def start(self, env=None, options=(), tls_dir=None):
        result = docker(
            "run", "-d", "-p", "127.0.0.1::80", "-p", "127.0.0.1::8443",
            *self.docker_args(env or {}, options, tls_dir), IMAGE,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        container = result.stdout.strip()
        self.containers.append(container)
        return container

    def port(self, container, port):
        out = docker("port", container, f"{port}/tcp").stdout.split()
        return int(out[0].rsplit(":", 1)[1])

    def logs(self, container):
        result = docker("logs", container)
        return result.stdout + result.stderr

    def wait_for(self, predicate, seconds=20, what="condition"):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            value = predicate()
            if value:
                return value
            time.sleep(0.25)
        self.fail(f"timed out waiting for {what}")

    def http_get(self, container, path="/"):
        url = f"http://127.0.0.1:{self.port(container, 80)}{path}"
        try:
            with urllib.request.urlopen(url, timeout=3) as response:
                return response.status, response.read().decode()
        except OSError:
            return None

    def served_fingerprint(self, container):
        """SHA-256 fingerprint of the certificate nginx serves, or None."""
        port = self.port(container, 8443)
        try:
            hello = run(
                ["openssl", "s_client", "-connect", f"127.0.0.1:{port}",
                 "-servername", "localhost"],
                input="", timeout=10,
            )
        except subprocess.TimeoutExpired:
            return None
        if "-----BEGIN CERTIFICATE-----" not in hello.stdout:
            return None
        return self.fingerprint(hello.stdout)

    def fingerprint(self, pem):
        result = run(["openssl", "x509", "-noout", "-fingerprint", "-sha256"], input=pem)
        return result.stdout.strip() or None

    def assert_running(self, container):
        state = docker(
            "inspect", "-f", "{{.State.Running}} {{.RestartCount}}", container
        ).stdout.split()
        self.assertEqual(state, ["true", "0"], self.logs(container))


class ReadOnlyRoot(ImageTestCase):
    """deploy/README.md, "Read-only root filesystem": these mounts only."""

    def config_mount(self):
        config = self.tmp / "config.js"
        config.write_text("")
        config.chmod(0o666)
        return ["-v", f"{config}:/usr/share/nginx/html/config.js"]

    def test_http_by_default_with_the_documented_mounts(self):
        container = self.start(
            {"VITE_HOST_API": "https://api.example.com"},
            READ_ONLY_ROOT + self.config_mount(),
        )
        status, _ = self.wait_for(lambda: self.http_get(container), what="HTTP")
        self.assertEqual(status, 200)
        status, config = self.http_get(container, "/config.js")
        self.assertEqual(status, 200)
        self.assertIn('VITE_HOST_API: "https://api.example.com"', config)
        self.assert_running(container)

    def test_https_with_the_documented_mounts(self):
        cert, key = self.ca.issue("localhost", "491231235959Z")
        tls_dir = self.secret_dir(cert, key)
        container = self.start(
            self.tls_env(FRONTEND_HTTP_ENABLED="false"),
            READ_ONLY_ROOT + self.config_mount(),
            tls_dir,
        )
        served = self.wait_for(lambda: self.served_fingerprint(container), what="HTTPS")
        self.assertEqual(served, self.fingerprint(cert))
        self.assert_running(container)

    def test_a_read_only_tmp_fails_clearly(self):
        options = [
            "--read-only", "--tmpfs", "/var/cache/nginx", "--tmpfs", "/run",
        ] + self.config_mount()
        code, logs = self.run_to_exit(options=options)
        self.assertEqual(code, 1, logs)
        self.assertIn(
            "[entrypoint] cannot write /tmp/futureagi-nginx/listen.conf", logs
        )

    def test_a_read_only_config_warns_and_serves_the_placeholder(self):
        # As before: the container still starts, with the image's placeholder.
        container = self.start(
            {"VITE_HOST_API": "https://api.example.com"}, READ_ONLY_ROOT
        )
        status, _ = self.wait_for(lambda: self.http_get(container), what="HTTP")
        self.assertEqual(status, 200)
        status, config = self.http_get(container, "/config.js")
        self.assertEqual(status, 200)
        self.assertIn("window.__FUTURE_AGI_CONFIG__ = {};", config)
        self.assertNotIn("api.example.com", config)
        logs = self.logs(container)
        self.assertIn(
            "[entrypoint] warning: cannot write /usr/share/nginx/html/config.js;"
            " serving the image's placeholder config",
            logs,
        )
        self.assertNotIn("[entrypoint] wrote", logs)
        self.assert_running(container)


class PrivateKeyMode(ImageTestCase):
    """deploy/README.md: owner and fsGroup read are expected; anything broader
    warns and still serves; a missing or unreadable key still fails."""

    def start_with_key_mode(self, mode):
        cert, key = self.ca.issue("localhost", "491231235959Z")
        container = self.start(self.tls_env(), tls_dir=self.secret_dir(cert, key, mode))
        served = self.wait_for(lambda: self.served_fingerprint(container), what="HTTPS")
        self.assertEqual(served, self.fingerprint(cert))
        return container, key

    def test_owner_and_group_read_start_without_a_warning(self):
        for mode in (0o400, 0o440, 0o600, 0o640):
            with self.subTest(mode=oct(mode)):
                container, _ = self.start_with_key_mode(mode)
                self.assertNotIn("warning", self.logs(container))
                docker("rm", "-f", container)

    def test_broader_modes_warn_name_the_file_and_still_serve(self):
        for mode in (0o644, 0o604, 0o660, 0o666, 0o447):
            with self.subTest(mode=oct(mode)):
                container, key = self.start_with_key_mode(mode)
                logs = self.logs(container)
                self.assertIn(
                    "[entrypoint] warning: TLS private key "
                    f"/run/futureagi-tls/tls.key has mode {mode:o}",
                    logs,
                )
                for line in key_body_lines(key):
                    self.assertNotIn(line, logs)
                self.assert_running(container)
                docker("rm", "-f", container)

    def test_a_broad_mode_after_rotation_warns_and_rotates(self):
        container, _ = self.start_with_key_mode(0o440)
        cert, key = self.ca.issue("rotated", "491231235959Z")
        self.rotate(cert, key, 0o644)
        self.wait_for(
            lambda: self.served_fingerprint(container) == self.fingerprint(cert),
            what="the rotated certificate",
        )
        logs = self.logs(container)
        self.assertIn("[tls-reload] warning: TLS private key", logs)
        for line in key_body_lines(key):
            self.assertNotIn(line, logs)
        self.assert_running(container)

    def test_a_missing_or_unreadable_key_still_fails_startup(self):
        cert, key = self.ca.issue("localhost", "491231235959Z")
        tls_dir = self.secret_dir(cert, key, 0o000)
        # The nginx master runs as root; without CAP_DAC_OVERRIDE a mode 000
        # key owned by another user is unreadable to it. busybox `test -r` is
        # true for root whatever the mode, so nginx is what refuses the key.
        code, logs = self.run_to_exit(
            self.tls_env(), ["--cap-drop", "DAC_OVERRIDE"], tls_dir
        )
        self.assertEqual(code, 1, logs)
        self.assertIn('cannot load certificate key "/run/futureagi-tls/tls.key"', logs)
        self.assertIn("Permission denied", logs)
        for line in key_body_lines(key):
            self.assertNotIn(line, logs)
        code, logs = self.run_to_exit(
            self.tls_env(FRONTEND_TLS_KEY_FILE="/run/futureagi-tls/missing.key"),
            tls_dir=tls_dir,
        )
        self.assertEqual(code, 1, logs)
        self.assertIn("[entrypoint] TLS file missing, empty or unreadable", logs)


class CertificateExpiry(ImageTestCase):
    """The entrypoint's notAfter reader, at startup and on rotation."""

    def test_valid_leaves_and_chains_are_served(self):
        intermediate = self.ca.issue(
            "intermediate", "20601231235959Z", extension="intermediate"
        )
        inter_paths = (self.ca.dir / "intermediate.key", self.ca.dir / "intermediate.crt")
        chained_leaf, chained_key = self.ca.issue(
            "chained", "20551231235959Z", issuer=inter_paths
        )
        expired_ca, _ = self.ca.issue("old-ca", "200101000000Z", extension="intermediate")
        leaf, leaf_key = self.ca.issue("leaf", "491231235959Z")
        cases = {
            "UTCTime leaf": self.ca.issue("utc", "491231235959Z"),
            "GeneralizedTime leaf": self.ca.issue("generalized", "20500101000000Z"),
            "leaf then intermediate": (chained_leaf + intermediate[0], chained_key),
            "leaf then an expired CA": (leaf + expired_ca, leaf_key),
        }
        for case, (cert, key) in cases.items():
            with self.subTest(case=case):
                container = self.start(self.tls_env(), tls_dir=self.secret_dir(cert, key))
                served = self.wait_for(
                    lambda: self.served_fingerprint(container), what="HTTPS"
                )
                self.assertEqual(served, self.fingerprint(cert))
                docker("rm", "-f", container)

    def test_expired_and_malformed_leaves_fail_startup(self):
        valid, key = self.ca.issue("valid", "491231235959Z")
        expired, _ = self.ca.issue("expired", "200101000000Z")
        cases = {
            "expired UTCTime leaf": (expired, "expired at 2020-01-01T00:00:00Z"),
            "expired GeneralizedTime leaf": (
                unsigned_certificate(b"20200101000000Z", 0x18),
                "expired at 2020-01-01T00:00:00Z",
            ),
            "expired leaf then a valid CA": (
                expired + self.ca.issuer_cert.read_text(),
                "expired at 2020-01-01T00:00:00Z",
            ),
            "not base64": ("-----BEGIN CERTIFICATE-----\n!!!\n-----END CERTIFICATE-----\n",
                           "cannot read"),
            "truncated": (valid[:80] + "\n-----END CERTIFICATE-----\n", "cannot read"),
            "UTCTime without seconds": (
                unsigned_certificate(b"4912312359Z", 0x17), "cannot read"
            ),
            "fractional seconds": (
                unsigned_certificate(b"20991231235959.5Z", 0x18), "cannot read"
            ),
        }
        for case, (cert, message) in cases.items():
            with self.subTest(case=case):
                code, logs = self.run_to_exit(
                    self.tls_env(), tls_dir=self.secret_dir(cert, key)
                )
                self.assertEqual(code, 1, logs)
                self.assertIn("[entrypoint] ", logs)
                self.assertIn(message, logs)
                self.assertIn("/run/futureagi-tls/tls.crt", logs)

    def test_failed_rotations_keep_the_old_certificate_until_a_valid_one(self):
        first, first_key = self.ca.issue("first", "491231235959Z")
        container = self.start(self.tls_env(), tls_dir=self.secret_dir(first, first_key))
        self.wait_for(
            lambda: self.served_fingerprint(container) == self.fingerprint(first),
            what="the first certificate",
        )
        started = docker("inspect", "-f", "{{.State.StartedAt}}", container).stdout
        expired, expired_key = self.ca.issue("expired", "200101000000Z")
        for cert, key, message in (
            (expired, expired_key, "expired at 2020-01-01T00:00:00Z; keeping current"),
            ("-----BEGIN CERTIFICATE-----\nAAAA\n-----END CERTIFICATE-----\n",
             first_key, "cannot read the certificate in /run/futureagi-tls/tls.crt"),
        ):
            self.rotate(cert, key)
            self.wait_for(lambda: message in self.logs(container), what=message)
            time.sleep(2.5)
            self.assertEqual(self.served_fingerprint(container), self.fingerprint(first))
        second, second_key = self.ca.issue("second", "20500101000000Z")
        self.rotate(second, second_key)
        self.wait_for(
            lambda: self.served_fingerprint(container) == self.fingerprint(second),
            what="the second certificate",
        )
        self.assert_running(container)
        self.assertEqual(
            docker("inspect", "-f", "{{.State.StartedAt}}", container).stdout, started
        )


class ConfigOnly(ImageTestCase):
    """futureagi/standalone runs this entrypoint for config.js only
    (deploy/standalone/bin/start: FRONTEND_CONFIG_ONLY=true)."""

    def generate(self, **env):
        out = self.tmp / f"out-{time.monotonic_ns()}"
        (out / "tmp").mkdir(parents=True)
        out.chmod(0o777)
        (out / "tmp").chmod(0o777)
        env = {"CONFIG_PATH": "/out/config.js", **env}
        code, logs = self.run_to_exit(
            env, ["-v", f"{out}:/out", "-v", f"{out / 'tmp'}:/tmp"], command=["true"]
        )
        config = out / "config.js"
        return code, logs, config.read_bytes() if config.exists() else None, out

    def test_unsupported_tls_settings_are_not_checked(self):
        code, logs, config, out = self.generate(
            FRONTEND_CONFIG_ONLY="true",
            FRONTEND_TLS_ENABLED="true",
            FRONTEND_HTTP_ENABLED="false",
            FRONTEND_TLS_CERT_FILE="/missing/tls.crt",
            FRONTEND_TLS_RELOAD_INTERVAL="0",
            VITE_HOST_API="http://localhost:8000",
        )
        self.assertEqual(code, 0, logs)
        self.assertIn(b'VITE_HOST_API: "http://localhost:8000"', config)
        self.assertFalse((out / "tmp" / "futureagi-nginx").exists())
        self.assertNotIn("TLS", logs)
        self.assertIn("[entrypoint] FRONTEND_CONFIG_ONLY is set: exiting after config.js", logs)

    def test_the_config_is_the_same_as_a_normal_start(self):
        _, _, only, _ = self.generate(
            FRONTEND_CONFIG_ONLY="true", VITE_HOST_API="http://localhost:8000"
        )
        code, logs, normal, out = self.generate(VITE_HOST_API="http://localhost:8000")
        self.assertEqual(code, 0, logs)
        self.assertEqual(only, normal)
        self.assertEqual(
            (out / "tmp" / "futureagi-nginx" / "listen.conf").read_text(),
            "listen 80;\nlisten [::]:80;\n",
        )

    def test_a_normal_start_still_checks_tls(self):
        code, logs, _, _ = self.generate(
            FRONTEND_TLS_ENABLED="true", FRONTEND_TLS_CERT_FILE="/missing/tls.crt"
        )
        self.assertEqual(code, 1, logs)
        self.assertIn("[entrypoint] TLS file missing", logs)
