"""Actual settings resolution in isolated subprocesses, with networking denied."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
FIXTURE = {
    "port": 28123,
    "pg_port": 28129,
    "redis_port": 28130,
    "native_port": 28131,
    "password": "fixture-owned-secret",
}

CHILD = r"""
import json, os, socket, sys
sys.path[:0] = json.loads(sys.argv[1])
fixture = json.loads(sys.argv[2])
mutation = sys.argv[3]
attempts = []
def deny_socket(*args, **kwargs):
    attempts.append(True)
    raise AssertionError("settings validation must not open a network connection")
socket.socket.connect = deny_socket
socket.create_connection = deny_socket
# libpq opens C-level sockets, so deny its entrypoints as well.
import psycopg
psycopg.connect = deny_socket
psycopg.Connection.connect = classmethod(deny_socket)
if mutation == "cached":
    os.environ["DJANGO_SETTINGS_MODULE"] = "tfc.settings.settings"
    os.environ["ENV_TYPE"] = "local"
    os.environ["CLOUD_DEPLOYMENT"] = ""
    os.environ["EE_LICENSE_KEY"] = ""
    from django.conf import settings
    assert settings.DATABASES["default"]["HOST"] == "ambient.invalid"
from fixture_connections import ABSENT_LIBPQ_ENV, configure, verify
for index, key in enumerate(sorted(ABSENT_LIBPQ_ENV)):
    os.environ[key] = "" if index % 2 else "ambient_override"
os.environ["PGSERVICEFILE"] = ""
configure(fixture)
from runtime_paths import enter_backend_root
enter_backend_root()
assert not ABSENT_LIBPQ_ENV.intersection(os.environ)
from dotenv import load_dotenv
assert load_dotenv(sys.argv[4], override=True) is False
if mutation == "cached":
    try:
        verify(fixture)
    except RuntimeError as error:
        assert "connection mismatch" in str(error) or "optional libpq overrides must be absent" in str(error)
    else:
        raise AssertionError("cached foreign settings were accepted")
else:
    verify(fixture)
    if not mutation:
        import django
        django.setup()
        verify(fixture)
    from django.conf import settings
    from django.db import connections
    if mutation == "default":
        connections.databases["default"]["HOST"] = "foreign.invalid"
    elif mutation == "direct":
        connections.databases["default_direct"]["PORT"] = "5432"
    elif mutation == "replica":
        connections.databases["replica"]["NAME"] = "foreign_database"
    elif mutation == "ch":
        settings.CLICKHOUSE["CH_HOST"] = "foreign.invalid"
    elif mutation == "ch25":
        settings.CLICKHOUSE_V2["CH25_PASSWORD"] = "wrong-password"
    elif mutation == "catalog":
        settings.PROPERTY_CATALOG_CH_PORT = 9000
    elif mutation == "cache":
        settings.CACHES["default"]["LOCATION"] = "redis://foreign.invalid:6379/1"
    elif mutation == "channels":
        settings.CHANNEL_LAYERS["default"]["CONFIG"]["hosts"] = ["redis://foreign.invalid:6379/3"]
    elif mutation == "cdc":
        os.environ["SRC_PG_HOST"] = "foreign.invalid"
    elif mutation == "empty_service":
        os.environ["PGSERVICEFILE"] = ""
    if mutation:
        try:
            verify(fixture)
        except RuntimeError as error:
            assert "connection mismatch" in str(error) or "optional libpq overrides must be absent" in str(error)
        else:
            raise AssertionError("foreign resolved alias was accepted")
assert not attempts
print("fixture-connections-ok")
"""


class FixtureConnectionTests(unittest.TestCase):
    def check_subprocess(self, mutation=""):
        with tempfile.TemporaryDirectory() as temporary:
            dotenv = Path(temporary) / ".env"
            dotenv.write_text(
                "PGBOUNCER_HOST=dotenv.invalid\nPG_DIRECT_HOST=dotenv.invalid\nCH25_HOST=dotenv.invalid\nREDIS_CACHE_URL=redis://dotenv.invalid:6379/1\n"
            )
            env = {
                key: os.environ[key] for key in ("PATH", "HOME") if key in os.environ
            }
            env.update(
                {
                    "PGBOUNCER_HOST": "ambient.invalid",
                    "PGBOUNCER_PORT": "6432",
                    "PG_DIRECT_HOST": "ambient.invalid",
                    "PG_DIRECT_PORT": "5432",
                    "PGBOUNCER_READ_HOST": "ambient.invalid",
                    "PG_READ_DB": "ambient_db",
                    "PGHOSTADDR": "192.0.2.1",
                    "PGSERVICE": "ambient_service",
                    "PGSSLMODE": "verify-full",
                    "SRC_PG_HOST": "ambient.invalid",
                    "SRC_PG_DB": "ambient_db",
                    "DST_CH_HOST": "ambient.invalid",
                    "CH25_HOST": "ambient.invalid",
                    "CH25_PASSWORD": "ambient_password",
                    "PROPERTY_CATALOG_CH_HOST": "ambient.invalid",
                    "PROPERTY_CATALOG_CH_PORT": "9000",
                    "REDIS_CACHE_URL": "redis://ambient.invalid:6379/1",
                    "REDIS_LOCK_URL": "redis://ambient.invalid:6379/2",
                    "CHANNEL_LAYER_BACKEND": "memory",
                    "CHANNEL_REDIS_URL": "redis://ambient.invalid:6379/3",
                    "HTTP_PROXY": "http://ambient.invalid:3128",
                    "PYTHON_DOTENV_DISABLED": "0",
                    "FUTURE_AGI_TELEMETRY_DISABLED": "true",
                    "OPENBLAS_NUM_THREADS": "1",
                    "OMP_NUM_THREADS": "1",
                }
            )
            result = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    "-B",
                    "-c",
                    CHILD,
                    json.dumps([str(HERE), str(ROOT / "futureagi")]),
                    json.dumps(FIXTURE),
                    mutation,
                    str(dotenv),
                ],
                cwd=temporary,
                env=env,
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertNotIn(FIXTURE["password"], result.stdout + result.stderr)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("fixture-connections-ok", result.stdout)

    def test_optional_libpq_overrides_are_absent_not_empty(self):
        from fixture_connections import ABSENT_LIBPQ_ENV, environment

        configured = environment(FIXTURE)
        self.assertTrue(ABSENT_LIBPQ_ENV.isdisjoint(configured))
        self.assertFalse(
            any(
                key.startswith("PG") and value == ""
                for key, value in configured.items()
            )
        )

    def test_all_actual_settings_ignore_ambient_and_dotenv_aliases(self):
        self.check_subprocess()

    def test_every_foreign_resolved_alias_fails_before_networking(self):
        for mutation in (
            "default",
            "direct",
            "replica",
            "ch",
            "ch25",
            "catalog",
            "cache",
            "channels",
            "cdc",
            "cached",
            "empty_service",
        ):
            with self.subTest(mutation=mutation):
                self.check_subprocess(mutation)


if __name__ == "__main__":
    unittest.main()
