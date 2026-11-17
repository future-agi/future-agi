"""Disposable synthetic PostgreSQL and fresh operator-process helpers."""

import os
import secrets
import subprocess
import sys
import time
import uuid

import psycopg
from cryptography.fernet import Fernet
from django.db import connections
from django.db.migrations.executor import MigrationExecutor
from psycopg import sql

from agentcc.hosted_migration_progress import (
    DEFAULT_SCHEMA_DEADLINE_SECONDS,
)
from agentcc.hosted_migration_progress import (
    schema_deadline_seconds as validate_schema_deadline,
)
from agentcc.tests.hosted_rehearsal_process import (
    DEFAULT_OPERATOR_TIMEOUT_SECONDS,
    SCHEMA_PROCESS_GRACE_SECONDS,
    run_operator,
)
from agentcc.tests.hosted_rehearsal_process import (
    operator_timeout_seconds as validate_operator_timeout,
)


class Rehearsal:
    def __init__(
        self,
        schema_deadline_seconds=DEFAULT_SCHEMA_DEADLINE_SECONDS,
        *,
        operator_timeout_seconds=DEFAULT_OPERATOR_TIMEOUT_SECONDS,
    ):
        self.schema_deadline_seconds = validate_schema_deadline(schema_deadline_seconds)
        self.operator_timeout_seconds = validate_operator_timeout(
            operator_timeout_seconds
        )
        if os.environ.get("G18_SYNTHETIC_REHEARSAL") != "1" or not os.environ.get(
            "OMEGA_EXEC_SOURCE_HEAD"
        ):
            raise RuntimeError("Cloud-only synthetic rehearsal requires opt-in")
        if os.environ.get("PG_HOST") not in ("localhost", "127.0.0.1"):
            raise RuntimeError("Only loopback PostgreSQL is allowed")
        self.db = "g18_operator_" + uuid.uuid4().hex
        self.credentials = {
            name: os.environ[env]
            for name, env in (
                ("host", "PG_HOST"),
                ("port", "PG_PORT"),
                ("user", "PG_USER"),
                ("password", "PG_PASSWORD"),
            )
        }
        self.credentials["connect_timeout"] = 2
        self.credentials["options"] = "-c lock_timeout=2000 -c statement_timeout=30000"
        self.source, self.target = (
            Fernet.generate_key().decode(),
            Fernet.generate_key().decode(),
        )
        self.revision = os.environ["OMEGA_EXEC_SOURCE_HEAD"]
        self.timings = []

    def __enter__(self):
        self.admin = psycopg.connect(
            dbname="postgres", autocommit=True, **self.credentials
        )
        self.admin.execute(
            sql.SQL("CREATE DATABASE {}").format(sql.Identifier(self.db))
        )
        self.prior = {}
        for alias in ("default", "default_direct"):
            connection = connections[alias]
            connection.close()
            self.prior[alias] = {
                name: connection.settings_dict.get(name) for name in ("NAME", "OPTIONS")
            }
            connection.settings_dict["NAME"] = self.db
            connection.settings_dict["OPTIONS"] = {
                "connect_timeout": 2,
                "options": "-c lock_timeout=2000 -c statement_timeout=30000",
            }
        self.connection = connections["default_direct"]
        return self

    def __exit__(self, *exc):
        for alias, prior in self.prior.items():
            connections[alias].close()
            connections[alias].settings_dict.update(prior)
        self.admin.execute(
            sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(self.db))
        )
        self.admin.close()

    def sql_connection(self):
        return psycopg.connect(dbname=self.db, **self.credentials)

    def fixture_state(self, boundary=None, *, latest=False):
        # Fixture construction uses Django's default alias, as the historical
        # rehearsal did. It is NOT proof that legacy shared migrations support
        # default_direct. The separate real-command empty-install case pins that.
        def progress(action, migration=None, fake=None):
            if action == "apply_start":
                print(
                    f"fixture-migration {migration.app_label}.{migration.name}",
                    flush=True,
                )

        fixture_connection = connections["default"]
        executor = MigrationExecutor(fixture_connection, progress_callback=progress)
        if boundary or latest:
            started = time.monotonic()
            targets = (
                [("prism", boundary)]
                if boundary
                else executor.loader.graph.leaf_nodes()
            )
            executor.migrate(targets)
            self.timings.append(
                {"fixture_database_seconds": round(time.monotonic() - started, 3)}
            )
            executor = MigrationExecutor(fixture_connection)
        return executor.loader.project_state(
            [("prism", boundary)] if boundary else executor.loader.graph.leaf_nodes()
        ).apps

    def environment(self, **changes):
        env = {
            name: value
            for name, value in os.environ.items()
            if not name.startswith("AGENTCC_")
        }
        env.update(
            DJANGO_SETTINGS_MODULE="agentcc.tests.hosted_process_settings",
            PG_DB=self.db,
            PG_DIRECT_HOST="127.0.0.1",
            AGENTCC_DEPLOYMENT_PROFILE="hosted",
            ENV_TYPE="production",
            SECRET_KEY=secrets.token_urlsafe(48),
            DEBUG="false",
            NO_STARTUP_DB_MUTATIONS="true",
            SERVICE_TYPE="bootstrap",
            STARTUP_DB_MUTATION_MODE="operator",
            AGENTCC_ADMIN_TOKEN=secrets.token_hex(32),
            AGENTCC_INTERNAL_API_KEY=secrets.token_hex(32),
            AGENTCC_WEBHOOK_SECRET=secrets.token_hex(32),
            AGENTCC_GATEWAY_INTERNAL_URL="http://127.0.0.1:9",
            AGENTCC_HOSTED_RELEASE_REVISION=self.revision,
            INTEGRATION_ENCRYPTION_KEY=self.source,
            AGENTCC_GATEWAY_SOURCE_ENCRYPTION_KEY=self.source,
            AGENTCC_GATEWAY_TARGET_ENCRYPTION_KEY=self.target,
        )
        env.update(changes)
        return env

    @staticmethod
    def diagnostic(result, env):
        text = (
            result.stderr.strip().splitlines()[-1]
            if result.stderr.strip()
            else "no stderr"
        )
        metadata = [
            line
            for line in result.stderr.splitlines()
            if line.startswith(("synthetic-migration ", "synthetic-sql-failure "))
        ]
        text = "; ".join([*metadata[-2:], text])
        for name, value in env.items():
            if value and any(
                part in name for part in ("KEY", "TOKEN", "PASSWORD", "SECRET")
            ):
                text = text.replace(value, "[synthetic input redacted]")
        return text[:500]

    def run(self, phase, *args, expected=0, changes=None):
        started = time.monotonic()
        env = self.environment(**(changes or {}))
        schema_args = (
            ["--schema-deadline-seconds", str(self.schema_deadline_seconds)]
            if phase == "schema"
            else []
        )
        result = run_operator(
            [
                sys.executable,
                "-m",
                "agentcc.tests.hosted_process_entry",
                "hosted_gateway_migrate",
                phase,
                *args,
                *schema_args,
            ],
            env=env,
            timeout=(
                self.schema_deadline_seconds + SCHEMA_PROCESS_GRACE_SECONDS
                if phase == "schema"
                else self.operator_timeout_seconds
            ),
        )
        schema_elapsed = [
            float(line.split("=", 1)[1])
            for line in result.stdout.splitlines()
            if line.startswith("schema-phase elapsed_seconds=")
        ]
        self.timings.append(
            {
                "phase": phase,
                "apply": "--apply" in args,
                "command_seconds": round(time.monotonic() - started, 3),
                "exit": result.returncode,
                **(
                    {"schema_phase_seconds": schema_elapsed[-1]}
                    if schema_elapsed
                    else {}
                ),
            }
        )
        print(f"operator-phase {phase} exit={result.returncode}", flush=True)
        for secret in (self.source, self.target, "synthetic-transition-value"):
            assert secret not in result.stdout + result.stderr, (
                "operator disclosed a value"
            )
        assert result.returncode == expected, (
            f"{phase}: exit={result.returncode}, expected={expected}; {self.diagnostic(result, env)}"
        )
        return result.stdout + result.stderr

    def apply(self, phase, *args, **kwargs):
        return self.run(
            phase,
            "--apply",
            "--quiesced",
            "--compatible-runtime-revision",
            self.revision,
            *args,
            **kwargs,
        )

    def key(self, phase="key-transition", *args, **kwargs):
        return self.run(
            phase,
            "--quiesced",
            "--compatible-runtime-revision",
            self.revision,
            *args,
            **kwargs,
        )

    def child_probe(self, action, **changes):
        env = self.environment(**changes)
        result = subprocess.run(
            [sys.executable, "-m", "agentcc.tests.hosted_process_entry", action],
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, (
            f"{action}: exit={result.returncode}; {self.diagnostic(result, env)}"
        )
        return result.stdout
