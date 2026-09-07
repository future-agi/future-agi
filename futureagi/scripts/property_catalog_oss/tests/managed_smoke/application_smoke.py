"""Actual OSS migrations/ORM and continuous lifecycle, on this run only."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from run import HERE, ROOT, Run, load_run, save

APPLICATION_CHAIN_TIMEOUT_SECONDS = 2100
APPLICATION_PARENT_RESERVE_SECONDS = 20


def _application_chain_deadline(parent_deadline: float) -> float:
    return min(
        parent_deadline - APPLICATION_PARENT_RESERVE_SECONDS,
        time.monotonic() + APPLICATION_CHAIN_TIMEOUT_SECONDS,
    )


def snapshot(run: Run) -> None:
    destination = run.directory / "backend"
    destination.mkdir(mode=0o700)
    paths = run.command(
        [
            "git",
            "ls-files",
            "--cached",
            "--others",
            "--exclude-standard",
            "-z",
            "futureagi",
        ],
        cwd=ROOT,
    ).stdout.split("\0")
    digest = hashlib.sha256()
    copied = 0
    for relative in sorted(set(paths)):
        if not relative:
            continue
        path = Path(relative)
        source = ROOT / path
        if path.parts[0] != "futureagi" or ".." in path.parts:
            raise RuntimeError("code snapshot escaped backend scope")
        if any(part.startswith(".env") for part in path.parts) or not source.is_file():
            continue
        if source.is_symlink():
            raise RuntimeError(f"snapshot refuses source symlink: {relative}")
        target = destination.joinpath(*path.parts[1:])
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        digest.update(
            relative.encode() + b"\0" + hashlib.sha256(target.read_bytes()).digest()
        )
        copied += 1
    save(
        run.directory / "application-snapshot.json",
        {"files": copied, "sha256": digest.hexdigest(), "source": str(ROOT)},
    )


def backend_environment(run: Run, *, reader: bool) -> dict:
    m = run.manifest
    runtime = run.directory / "application-runtime"
    spool = run.directory / "application-spool"
    return {
        **run.env,
        "MANAGED_SMOKE_DIRECTORY": str(run.directory),
        "DJANGO_SETTINGS_MODULE": "application_settings",
        "ENV_TYPE": "development",
        "CLOUD_DEPLOYMENT": "",
        "EE_LICENSE_KEY": "",
        "SECRET_KEY": "managed-disposable-" + m["run_id"],
        "SERVICE_TYPE": "bootstrap",
        "STARTUP_DB_MUTATION_MODE": "disabled",
        "NO_STARTUP_DB_MUTATIONS": "true",
        "FAST_STARTUP": "true",
        "FUTURE_AGI_TELEMETRY_DISABLED": "true",
        "OTEL_ENABLED": "false",
        "SENTRY_ENABLED": "false",
        "CH_ENABLED": "false",
        # CH schema is already provisioned by checked-in SQL/OSS bootstrap.
        "FI_SKIP_CH25_MIGRATION": "1",
        "PG_DB": "managed_smoke",
        "PGBOUNCER_HOST": "127.0.0.1",
        "PGBOUNCER_PORT": str(m["ports"]["postgres"]),
        "PG_HOST": "127.0.0.1",
        "PG_PORT": str(m["ports"]["postgres"]),
        "PG_USER": "property_catalog_oss_reader" if reader else "smoke_admin",
        "PG_PASSWORD": "oss-catalog-postgres-reader-local-only"
        if reader
        else m["password"],
        "CH25_HOST": "127.0.0.1",
        "CH25_HTTP_PORT": str(m["ports"]["http"]),
        "CH25_TCP_PORT": str(m["ports"]["native"]),
        "CH25_DATABASE": "default",
        "CH25_USER": "property_catalog_oss_source",
        "CH25_PASSWORD": "oss-catalog-source-local-only",
        "CH25_SERVER_ENFORCED_READONLY": "true",
        "CH25_DROP_LEGACY_CDC_CHAIN": "false",
        "CH25_TRACE_DUAL_WRITE": "false",
        "CH_DUAL_WRITE": "false",
        "SPAN_ATTRIBUTE_CATALOG_READ_MODE": "off",
        "PROPERTY_CATALOG_READ_MODE": "off",
        "PROPERTY_CATALOG_DATABASE": m["database"],
        "PROPERTY_CATALOG_CH_HOST": "127.0.0.1",
        "PROPERTY_CATALOG_CH_PORT": str(m["ports"]["native"]),
        "PROPERTY_CATALOG_CH_USER": "property_catalog_oss_api",
        "PROPERTY_CATALOG_CH_PASSWORD": "oss-catalog-api-local-only",
        "PROPERTY_CATALOG_DEV_RECONCILE_ENABLED": "false",
        "PROPERTY_CATALOG_OSS_SUPERVISOR_ACK": "PROPERTY_CATALOG_OSS_SUPERVISOR_V1",
        "PROPERTY_CATALOG_OSS_SUPERVISOR_POLL_SECONDS": "5",
        "PROPERTY_CATALOG_OSS_SUPERVISOR_WORKSPACE_BATCH_SIZE": "8",
        "PROPERTY_CATALOG_OSS_SUPERVISOR_PROJECT_BATCH_SIZE": "8",
        "PROPERTY_CATALOG_DEV_SOURCE_DATABASE": "default",
        "PROPERTY_CATALOG_DEV_TARGET_DATABASE": m["database"],
        "PROPERTY_CATALOG_DEV_WRITE_CH_DATABASE": m["database"],
        "PROPERTY_CATALOG_DEV_WRITE_CH_HOST": "127.0.0.1",
        "PROPERTY_CATALOG_DEV_WRITE_CH_PORT": str(m["ports"]["native"]),
        "PROPERTY_CATALOG_DEV_WRITE_CH_USER": "property_catalog_oss_control",
        "PROPERTY_CATALOG_DEV_WRITE_CH_PASSWORD": "oss-catalog-control-local-only",
        # Startup proves this owned HTTP mapping against the configured native
        # writer; neither the source route nor the credential is admission proof.
        "FI_PROPERTY_CATALOG_LEDGER_CH_USERNAME": "property_catalog_oss_ledger",
        "FI_PROPERTY_CATALOG_LEDGER_CH_PASSWORD": "oss-catalog-ledger-local-only",
        "PROPERTY_CATALOG_DEV_REVISION_FENCE_FILE": str(
            runtime / "revision-fence-v2.json"
        ),
        "PROPERTY_CATALOG_DEV_DRAIN_PROOF_FILE": str(
            spool / "producer-drain-proof-v2.json"
        ),
        "PROPERTY_CATALOG_DEV_PRODUCER_RETIREMENT_FILE": str(
            spool / "producer-state-retirements-v1.json"
        ),
        "PROPERTY_CATALOG_DEV_MUTATION_LOCK_DIRECTORY": str(runtime),
        "PROPERTY_CATALOG_DEV_SCHEDULED_RECONCILE_WALL_MS": "180000",
        "PROPERTY_CATALOG_CANDIDATE_KAFKA_TOPIC": m["candidate_topic"],
        "PROPERTY_CATALOG_ORDERED_KAFKA_TOPIC": m["ordered_topic"],
    }


def backend(action: str, directory: Path) -> None:
    run = load_run(directory)
    containers = json.loads(run.command(["docker", "inspect", *run.owned()]).stdout)
    pg = next(
        item
        for item in containers
        if item["Config"]["Labels"]["com.docker.compose.service"] == "postgres"
    )
    if pg["NetworkSettings"]["Ports"]["5432/tcp"] != [
        {"HostIp": "127.0.0.1", "HostPort": str(run.manifest["ports"]["postgres"])}
    ]:
        raise RuntimeError("application database is not the owned publication")
    sys.path.insert(0, str(directory / "backend"))
    os.chdir(directory / "backend")
    # Fail closed on accidental Python integration IO outside this test. No
    # credentials or .env are inherited; PostgreSQL DSN is separately checked.
    allowed_ports = set(run.manifest["ports"].values())

    def audit(event, args):
        if event == "socket.connect":
            address = args[1]
            if (
                not isinstance(address, tuple)
                or address[0] not in {"127.0.0.1", "::1"}
                or address[1] not in allowed_ports
            ):
                raise RuntimeError(
                    f"application attempted out-of-scope connection: {address}"
                )

    sys.addaudithook(audit)
    import django

    django.setup()
    from django.conf import settings
    from django.core.management import call_command
    from django.db import connection

    db = settings.DATABASES["default"]
    if (db["NAME"], db["HOST"], int(db["PORT"])) != (
        "managed_smoke",
        "127.0.0.1",
        run.manifest["ports"]["postgres"],
    ):
        raise RuntimeError("Django database escaped disposable DSN")
    if action == "migrate":
        if db["USER"] != "smoke_admin":
            raise RuntimeError("migrations require the new database admin")
        call_command("migrate", interactive=False, skip_checks=True, verbosity=1)
        from django.db.migrations.recorder import MigrationRecorder

        save(
            directory / "application-migrations.json",
            {
                "status": "passed",
                "applied": MigrationRecorder(connection).migration_qs.count(),
                "normal_migrations": True,
            },
        )
    elif action == "fixture":
        if db["USER"] != "smoke_admin":
            raise RuntimeError("fixture requires the new database admin")
        from accounts.models.organization import Organization
        from accounts.models.organization_membership import OrganizationMembership
        from accounts.models.user import OrgApiKey, User
        from accounts.models.workspace import Workspace, WorkspaceMembership
        from tfc.constants.roles import OrganizationRoles
        from tracer.models.project import Project

        fixture = json.loads((directory / "source-fixture.json").read_text())
        organization = Organization.objects.create(
            id=fixture["organization_id"], name="Managed isolated smoke"
        )
        user = User.objects.create_user(
            email=f"{run.manifest['run_id']}@managed-smoke.invalid",
            name="Synthetic smoke owner",
            organization=organization,
        )
        workspace = Workspace.no_workspace_objects.create(
            id=fixture["workspace_id"],
            organization=organization,
            name="smoke",
            is_default=True,
            created_by=user,
        )
        membership, _ = OrganizationMembership.no_workspace_objects.get_or_create(
            organization=organization,
            user=user,
            defaults={"role": OrganizationRoles.OWNER},
        )
        WorkspaceMembership.no_workspace_objects.get_or_create(
            workspace=workspace,
            user=user,
            defaults={
                "role": OrganizationRoles.WORKSPACE_ADMIN,
                "organization_membership": membership,
            },
        )
        OrgApiKey.objects.create(
            name="managed-smoke-api",
            type="user",
            user=user,
            organization=organization,
            workspace=workspace,
        )
        Project.no_workspace_objects.create(
            id=fixture["project_id"],
            organization=organization,
            workspace=workspace,
            name="Managed source fixture",
            model_type="GenerativeLLM",
            trace_type="observe",
            user=user,
        )
        save(directory / "application-fixture.json", {"status": "passed", **fixture})
    elif action == "workspace-fixture":
        if db["USER"] != "smoke_admin":
            raise RuntimeError("workspace fixtures require the owned database admin")
        from workspace_smoke import create

        create(run, json.loads((directory / "source-fixture.json").read_text()))
    elif action in {"relational-seed", "relational-update", "relational-delete"}:
        if db["USER"] != "smoke_admin":
            raise RuntimeError("relational fixture requires the owned database admin")
        from relational_smoke import mutate, seed

        if action == "relational-seed":
            seed(run)
        else:
            mutate(run, phase="updated" if action == "relational-update" else "deleted")
    elif action in {"projectless-seed", "projectless-update", "projectless-delete"}:
        if db["USER"] != "smoke_admin":
            raise RuntimeError("projectless fixture requires the owned database admin")
        from projectless_smoke import delete, seed, update

        {
            "projectless-seed": seed,
            "projectless-update": update,
            "projectless-delete": delete,
        }[action](run)
    elif action in {
        "projectless-seeded",
        "projectless-project",
        "projectless-ingest",
        "projectless-observed",
        "projectless-deleted",
        "projectless-deleted-restart",
    }:
        if (
            db["USER"] != "property_catalog_oss_reader"
            or settings.PROPERTY_CATALOG_READ_MODE != "managed"
        ):
            raise RuntimeError(
                "projectless API checks require managed admission and readonly PG"
            )
        from projectless_smoke import ingest, verify, verify_restart

        if action == "projectless-ingest":
            ingest(run)
        elif action == "projectless-deleted-restart":
            verify_restart(run, phase="deleted")
        else:
            verify(run, phase=action.removeprefix("projectless-"))
    elif action in {
        "read",
        "freshness",
        "ingestion",
        "deletion",
        "source-change",
        "old-version",
        "workspaces",
        "post-audit",
        "relational-seeded",
        "relational-updated",
        "relational-deleted",
    }:
        if (
            db["USER"] != "property_catalog_oss_reader"
            or settings.PROPERTY_CATALOG_READ_MODE != "managed"
        ):
            raise RuntimeError(
                "API verification requires managed admission and readonly PG"
            )
        from rest_framework.test import APIClient

        from accounts.models.user import OrgApiKey

        fixture = json.loads((directory / "source-fixture.json").read_text())
        key = OrgApiKey.objects.get(
            name="managed-smoke-api", workspace_id=fixture["workspace_id"]
        )
        client = APIClient()
        client.credentials(
            HTTP_X_API_KEY=key.api_key,
            HTTP_X_SECRET_KEY=key.secret_key,
            HTTP_X_WORKSPACE_ID=fixture["workspace_id"],
        )

        def get(endpoint, params):
            started = time.monotonic()
            response = client.get(
                "/tracer/dashboard/" + endpoint + "/", params, HTTP_HOST="localhost"
            )
            payload = response.json()
            if response.status_code != 200:
                raise RuntimeError(
                    f"managed {endpoint} API failed: {response.status_code} {payload}"
                )
            result = payload["result"]
            if (
                result.get("query_provenance") != "activated_property_catalog"
                or result.get("query_complete") is not True
            ):
                raise RuntimeError(
                    f"managed {endpoint} did not use selected catalog: {result}"
                )
            return result, time.monotonic() - started

        if action == "ingestion":
            from ingestion_smoke import verify

            verify(run, fixture, key, get)
            return

        if action == "deletion":
            from deletion_smoke import verify

            verify(run, fixture, key, get)
            return

        if action == "source-change":
            from source_change_smoke import verify

            verify(run, fixture, key, get)
            return

        if action == "old-version":
            from old_version_smoke import verify

            verify(run, fixture, key, get)
            return

        if action == "workspaces":
            from workspace_smoke import verify

            verify(run, fixture, key, get)
            return

        if action == "post-audit":
            from post_audit_smoke import verify

            verify(run, fixture, key, get)
            return

        if action in {"relational-seeded", "relational-updated", "relational-deleted"}:
            from relational_smoke import verify

            verify(run, fixture, key, phase=action.removeprefix("relational-"))
            return

        if action == "freshness":
            # A genuine post-activation source commit, followed by the same Go
            # candidate builder used by ingestion. One new event and one late
            # event distinguish processing-time catch-up from event-time scans.
            source = json.loads((directory / "candidate-input.json").read_text())
            now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")
            late = datetime.fromisoformat(fixture["since"]).strftime(
                "%Y-%m-%d %H:%M:%S.%f"
            )
            names = {"live_after_activation": now, "late_after_activation": late}
            rows = []
            for name, event_time in names.items():
                rows.append(
                    {
                        **source["rows"][0],
                        "id": "span-" + name,
                        "start_time": event_time,
                        "created_at": now,
                        "updated_at": now,
                        "attrs_string": {name: "arrived"},
                        "attrs_number": {},
                        "attrs_bool": {},
                        "attributes_extra": "{}",
                        "model": "",
                    }
                )
            committed_at = time.monotonic()
            run.execute(
                "clickhouse",
                [
                    "clickhouse-client",
                    "--database",
                    "default",
                    "--query",
                    "INSERT INTO spans FORMAT JSONEachRow",
                ],
                stdin="\n".join(json.dumps(row) for row in rows),
            )
            save(
                directory / "application-live-input.json",
                {
                    **source,
                    "rows": rows,
                    "topic": os.environ["PROPERTY_CATALOG_CANDIDATE_KAFKA_TOPIC"],
                    "output": str(directory / "application-live-kafka.json"),
                },
            )
            run.command(
                [
                    str(directory / "candidate-smoke"),
                    str(directory / "application-live-input.json"),
                ],
                timeout=45,
            )
            observed, last = {}, {}
            deadline = time.monotonic() + 45
            while time.monotonic() < deadline and len(observed) < len(names):
                for name in names.keys() - observed.keys():
                    props, _ = get(
                        "metrics",
                        {
                            "cursor_mode": "true",
                            "page_size": 50,
                            "search": name,
                            "source": "traces",
                            "project_ids": fixture["project_id"],
                        },
                    )
                    last[name] = {
                        "revision": props["catalog_revision"],
                        "metrics": props["metrics"],
                    }
                    if any(item.get("name") == name for item in props["metrics"]):
                        values, _ = get(
                            "filter_values",
                            {
                                "property_id": "custom_attribute:" + name,
                                "page_size": 50,
                                "source": "traces",
                                "project_ids": fixture["project_id"],
                            },
                        )
                        if [value["value"] for value in values["values"]] != [
                            "arrived"
                        ]:
                            raise RuntimeError(
                                f"live value differs from canonical source: {name}"
                            )
                        observed[name] = {
                            "seconds_after_source_commit": time.monotonic()
                            - committed_at,
                            "catalog_revision": values["catalog_revision"],
                        }
                if len(observed) < len(names):
                    time.sleep(1)
            passed = len(observed) == len(names)
            save(
                directory / "application-freshness.json",
                {
                    "status": "passed" if passed else "failed",
                    "observed": observed,
                    "not_visible": sorted(names.keys() - observed.keys()),
                    "last": last,
                    "seconds_after_source_commit": time.monotonic() - committed_at,
                    "source_commit_then_candidate": True,
                },
            )
            if not passed:
                raise RuntimeError(
                    "post-activation source values did not converge; see application-freshness.json"
                )
            return

        reports = []
        typed = []
        # Same endpoint as Observe's project picker and Dashboard's workspace picker.
        for scope in ({"project_ids": fixture["project_id"]}, {}):
            props, props_seconds = get(
                "metrics",
                {
                    "cursor_mode": "true",
                    "page_size": 50,
                    "search": "plan",
                    "source": "traces",
                    **scope,
                },
            )
            metrics = [item for item in props["metrics"] if item.get("name") == "plan"]
            if len(metrics) != 1:
                raise RuntimeError(f"expected one plan property, got {props}")
            values, values_seconds = get(
                "filter_values",
                {
                    "property_id": metrics[0]["property_id"],
                    "page_size": 50,
                    "source": "traces",
                    **scope,
                },
            )
            if [item["value"] for item in values["values"]] != ["Pro"] or values.get(
                "has_more"
            ) is not False:
                raise RuntimeError(f"plan value parity failed: {values}")
            reports.append(
                {
                    "scope": "project" if scope else "workspace",
                    "properties": props,
                    "values": values,
                    "properties_seconds": props_seconds,
                    "values_seconds": values_seconds,
                }
            )
        # Exercise signed continuation and JSON scalar types through the real
        # serializers/readers. JSON encoding distinguishes true from numeric 1.
        expected = {
            "score": ("number", [1.5]),
            "zero": ("number", [0]),
            "negative": ("number", [-12.5]),
            "enabled": ("boolean", [True]),
            "disabled": ("boolean", [False]),
            "unicode": ("string", ["café"]),
            "quote": ("string", ['a"b\\c']),
            "versioned": ("string", ["new"]),
            "tags": ("array", ["A", "B", 3, True]),
        }
        for name, (kind, expected_values) in expected.items():
            cursor, seen, found, fingerprints = None, set(), [], set()
            for _ in range(8):
                result, _seconds = get(
                    "filter_values",
                    {
                        "property_id": f"custom_attribute:{name}",
                        "page_size": 1,
                        "source": "traces",
                        "project_ids": fixture["project_id"],
                        **({"cursor": cursor} if cursor else {}),
                    },
                )
                if any(item.get("type") != kind for item in result["values"]):
                    raise RuntimeError(f"type changed for {name}: {result}")
                fingerprints.add(result["activation_fingerprint"])
                found.extend(item["value"] for item in result["values"])
                if result.get("has_more") is False:
                    break
                cursor = result.get("next_cursor")
                if not cursor or cursor in seen:
                    raise RuntimeError(f"nonadvancing value cursor for {name}")
                seen.add(cursor)
            else:
                raise RuntimeError(f"value cursor budget exceeded for {name}")

            def encoded(value):
                return json.dumps(value, ensure_ascii=False, sort_keys=True)

            if (
                sorted(map(encoded, found)) != sorted(map(encoded, expected_values))
                or len(fingerprints) != 1
            ):
                raise RuntimeError(
                    f"typed value parity or immutable cursor failed for {name}: {found}"
                )
            typed.append(
                {"name": name, "type": kind, "values": found, "pages": len(seen) + 1}
            )
        for missing_name in ("deleted_value", "foreign_value"):
            result, _seconds = get(
                "metrics",
                {
                    "cursor_mode": "true",
                    "page_size": 50,
                    "search": missing_name,
                    "source": "traces",
                },
            )
            if result["metrics"]:
                raise RuntimeError(f"deleted/foreign property leaked: {missing_name}")
        save(
            directory / "application-api.json",
            {
                "status": "passed",
                "authenticated": True,
                "transport": "Django APIClient, actual auth/middleware and scoped DRF router",
                "full_application_urlconf_verified": False,
                "requests": reports,
                "typed_value_parity": typed,
                "deleted_and_foreign_properties_absent": True,
            },
        )
    elif action in {"supervisor", "operator"}:
        if db["USER"] != "property_catalog_oss_reader":
            raise RuntimeError("supervisor must use actual readonly PostgreSQL role")
        kwargs = {"skip_checks": True}
        if action == "operator":
            kwargs.update(once=True, initial_backfill=True)
        else:
            kwargs["health_file"] = str(directory / "application-health.json")
            from source_change_smoke import install_fault

            install_fault(run)
            from post_audit_smoke import install_fault as install_post_audit_fault

            install_post_audit_fault(run)
            from operation_profile import install as install_profile

            install_profile(run)
        call_command("ch25_property_catalog_oss_supervisor", **kwargs)
    else:
        raise RuntimeError("unknown backend action")


def application_transport_environments(
    run: Run, readonly_env: dict
) -> tuple[dict, dict]:
    """Actual application children share the supervisor's admitted installation."""
    from ordered_chain import environments

    fence = readonly_env["PROPERTY_CATALOG_DEV_REVISION_FENCE_FILE"]
    seq, consumer = environments(run, {"revision_fence_file": fence})
    seq["FI_PROPERTY_CATALOG_SPOOL_DIR"] = str(run.directory / "application-spool")
    seq["FI_PROPERTY_CATALOG_SEQUENCER_STARTUP_TIMEOUT"] = "120s"
    consumer["FI_PROPERTY_CATALOG_REVISION_FENCE_FILE"] = fence
    return seq, consumer


def execute(run: Run, python: str) -> dict:
    from ordered_chain import Children

    old_database = "property_catalog_dev_smoke_" + run.manifest["run_id"]
    if (
        len(run.manifest["run_id"]) != 16
        or any(char not in "0123456789abcdef" for char in run.manifest["run_id"])
        or run.manifest["database"] != old_database
    ):
        raise RuntimeError(
            "application grant cleanup requires exact owned transport namespace"
        )
    snapshot(run)
    m = {
        **run.manifest,
        "database": "property_catalog_dev_app_" + run.manifest["run_id"],
        "candidate_topic": run.manifest["candidate_topic"] + ".application",
        "ordered_topic": run.manifest["ordered_topic"] + ".application",
    }
    app_run = Run(run.directory, m)
    app_run.deadline = run.deadline
    # The transport stage has stopped its writers. These fixed OSS principals
    # model one installation, not two concurrently writable catalog databases.
    # Rebind only the exact old catalog/capture grants. Retain every table/row
    # and canonical default.spans/system grant; never drop the old namespaces.
    for user, privilege, namespace in (
        ("property_catalog_oss_control", "SELECT, INSERT", old_database),
        ("property_catalog_oss_consumer", "INSERT", old_database),
        ("property_catalog_oss_ledger", "SELECT", old_database),
        ("property_catalog_oss_api", "SELECT", old_database),
        (
            "property_catalog_oss_control",
            "SELECT, INSERT, CREATE TABLE, ALTER DELETE, ALTER TTL, DROP TABLE",
            old_database + "_source_capture",
        ),
        ("property_catalog_oss_source", "SELECT", old_database + "_source_capture"),
    ):
        run.execute(
            "clickhouse",
            [
                "clickhouse-client",
                "--query",
                f"REVOKE {privilege} ON `{namespace}`.* FROM {user}",
            ],
        )
    run.execute(
        "clickhouse",
        [
            "env",
            f"PROPERTY_CATALOG_TARGET_DATABASE={m['database']}",
            "PROPERTY_CATALOG_SOURCE_DATABASE=default",
            "PROPERTY_CATALOG_SCHEMA_DIRECTORY=/schema",
            "CLICKHOUSE_HOST=127.0.0.1",
            "sh",
            "/bootstrap/bootstrap_clickhouse.sh",
        ],
    )
    for topic in (m["candidate_topic"], m["ordered_topic"]):
        run.execute(
            "kafka",
            [
                "/opt/kafka/bin/kafka-topics.sh",
                "--bootstrap-server",
                "kafka:9092",
                "--create",
                "--topic",
                topic,
                "--partitions",
                "1",
                "--replication-factor",
                "1",
            ],
            timeout=20,
        )
    for name in ("application-runtime", "application-spool"):
        (run.directory / name).mkdir(mode=0o700)
    script = str(HERE / "application_smoke.py")
    app_run.env = backend_environment(app_run, reader=False)
    app_run.command([python, script, "migrate", str(run.directory)], timeout=300)
    readonly_env = backend_environment(app_run, reader=True)
    # The expanded all-source lane includes independent application processes
    # and real onboarding transitions, not one query. Keep every API/stage cap
    # unchanged and reserve time inside the parent's aggregate deadline.
    deadline = _application_chain_deadline(run.deadline)
    children = Children(app_run, deadline, prefix="application-")
    seq, consumer = application_transport_environments(app_run, readonly_env)
    health_path = run.directory / "application-health.json"
    supervisor_log_path = run.directory / "application-supervisor.log"
    log = supervisor_log_path.open("xb")
    supervisor = None
    try:
        children.start(seq, consumer)
        supervisor = subprocess.Popen(
            [python, script, "supervisor", str(run.directory)],
            cwd=run.directory,
            env=readonly_env,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )

        def health():
            children.check()
            if supervisor.poll() is not None:
                raise RuntimeError(
                    f"supervisor exited {supervisor.returncode}; see {supervisor_log_path}"
                )
            if supervisor_log_path.stat().st_size > 2_000_000:
                raise RuntimeError("supervisor exceeded bounded log budget")
            return json.loads(health_path.read_text()) if health_path.exists() else {}

        while True:
            h = health()
            if h.get("phase") == "retrying":
                raise RuntimeError(
                    f"empty installation discovery failed safely; see {supervisor_log_path}"
                )
            if h.get("phase") == "idle" and h.get("live") and h.get("ready"):
                break
            time.sleep(0.25)
        identity_path = run.directory / "application-runtime/runtime-identity-v1.json"
        identity_bytes = identity_path.read_bytes()
        save(
            run.directory / "application-empty-install.json",
            {
                "status": "passed",
                "health": h,
                "identity": json.loads(identity_bytes),
                "supervisor_pid": supervisor.pid,
                "no_version_environment": True,
            },
        )
        app_run.command([python, script, "fixture", str(run.directory)], timeout=45)
        from clickhouse_driver import Client

        observer = Client(
            "127.0.0.1",
            port=m["ports"]["native"],
            database=m["database"],
            user="property_catalog_oss_api",
            password="oss-catalog-api-local-only",
            connect_timeout=3,
            send_receive_timeout=5,
            settings={
                "readonly": 2,
                "max_execution_time": 2,
                "max_result_rows": 64,
                "max_result_bytes": 1048576,
            },
        )
        try:
            while True:
                h = health()
                rows = observer.execute(
                    "SELECT toString(workspace_id), catalog_epoch, catalog_revision, toString(build_token), status FROM property_catalog_activations ORDER BY activation_sequence DESC LIMIT 2"
                )
                controls = observer.execute(
                    "SELECT action, control_sequence FROM property_catalog_activation_control_events ORDER BY control_sequence DESC LIMIT 2"
                )
                if (
                    rows
                    and rows[0][-1] == "active"
                    and controls
                    and controls[0][0] == "follow"
                ):
                    break
                if h.get("phase") == "idle" and h.get("detail", {}).get("failed_count"):
                    raise RuntimeError(
                        f"workspace lifecycle failed safely; see {supervisor_log_path}"
                    )
                time.sleep(0.25)
            if identity_path.read_bytes() != identity_bytes:
                raise RuntimeError("workspace bootstrap changed installation identity")
            api_env = {**readonly_env, "PROPERTY_CATALOG_READ_MODE": "managed"}
            previous_env = app_run.env
            app_run.env = api_env
            try:
                app_run.command(
                    [python, script, "read", str(run.directory)], timeout=60
                )
                first_api = json.loads(
                    (run.directory / "application-api.json").read_text()
                )
                save(run.directory / "application-api-before-restart.json", first_api)
                first_pid = supervisor.pid
                supervisor.terminate()
                supervisor.wait(timeout=10)
                children.stop()
                old_health_mtime = health_path.stat().st_mtime_ns
                children.start(seq, consumer)
                supervisor = subprocess.Popen(
                    [python, script, "supervisor", str(run.directory)],
                    cwd=run.directory,
                    env=readonly_env,
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
                while True:
                    h = health()
                    if (
                        health_path.stat().st_mtime_ns > old_health_mtime
                        and h.get("phase") == "idle"
                        and h.get("healthy")
                        and h.get("detail", {}).get("processed_count") == 1
                    ):
                        break
                    if h.get("detail", {}).get("failed_count"):
                        raise RuntimeError("managed application restart failed")
                    time.sleep(0.25)
                if identity_path.read_bytes() != identity_bytes:
                    raise RuntimeError("process restart replaced installation identity")
                if observer.execute(
                    "SELECT count() FROM property_catalog_activation_control_events"
                ) != [(1,)]:
                    raise RuntimeError("restart unnecessarily appended reader controls")
                app_run.command(
                    [python, script, "read", str(run.directory)], timeout=60
                )
                save(
                    run.directory / "application-restart.json",
                    {
                        "status": "passed",
                        "supervisor_before": first_pid,
                        "supervisor_after": supervisor.pid,
                        "transport_pids": children.pids,
                        "same_identity_bytes": True,
                        "single_follow_event": True,
                        "property_and_value_apis_verified_again": True,
                        "health": h,
                    },
                )
                app_run.command(
                    [python, script, "freshness", str(run.directory)], timeout=120
                )
                from ingestion_smoke import (
                    INGESTION_PROCESS_TIMEOUT_SECONDS,
                    start_collector,
                    verify_candidates,
                )

                start_collector(run)
                app_run.command(
                    [python, script, "ingestion", str(run.directory)],
                    timeout=INGESTION_PROCESS_TIMEOUT_SECONDS,
                )
                verify_candidates(run)
                health()
                app_run.command(
                    [python, script, "deletion", str(run.directory)], timeout=120
                )
                health()
                app_run.command(
                    [python, script, "source-change", str(run.directory)], timeout=240
                )
                health()
                # Reuse every typed/scope/pagination assertion after the separate
                # capture-stable and unnotified-repair activations.
                app_run.command(
                    [python, script, "read", str(run.directory)], timeout=60
                )
                save(
                    run.directory / "application-api-after-source-change.json",
                    json.loads((run.directory / "application-api.json").read_text()),
                )
                health()
                app_run.command(
                    [python, script, "old-version", str(run.directory)], timeout=100
                )
                health()
                app_run.command(
                    [python, script, "post-audit", str(run.directory)], timeout=240
                )
                health()
                app_run.env = backend_environment(app_run, reader=False)
                app_run.command(
                    [python, script, "workspace-fixture", str(run.directory)],
                    timeout=45,
                )
                app_run.env = api_env
                app_run.command(
                    [python, script, "workspaces", str(run.directory)], timeout=240
                )
                health()
                for mutation, phase in (
                    ("seed", "seeded"),
                    ("update", "updated"),
                    ("delete", "deleted"),
                ):
                    app_run.env = backend_environment(app_run, reader=False)
                    app_run.command(
                        [python, script, "relational-" + mutation, str(run.directory)],
                        timeout=45,
                    )
                    app_run.env = api_env
                    app_run.command(
                        [python, script, "relational-" + phase, str(run.directory)],
                        timeout=100,
                    )
                    health()
                for mutation, phase in (
                    ("seed", "seeded"),
                    ("update", "project"),
                    (None, "observed"),
                    ("delete", "deleted"),
                ):
                    if mutation is not None:
                        app_run.env = backend_environment(app_run, reader=False)
                        app_run.command(
                            [
                                python,
                                script,
                                "projectless-" + mutation,
                                str(run.directory),
                            ],
                            timeout=45,
                        )
                    app_run.env = api_env
                    if phase == "observed":
                        app_run.command(
                            [python, script, "projectless-ingest", str(run.directory)],
                            timeout=45,
                        )
                    app_run.command(
                        [python, script, "projectless-" + phase, str(run.directory)],
                        timeout=100,
                    )
                    health()
                # Restart the real catalog processes after the last-project
                # deletion. A readback helper alone is not restart evidence.
                previous_supervisor_pid = supervisor.pid
                previous_transport_pids = children.pids[-2:]
                control_count = observer.execute(
                    "SELECT count() FROM property_catalog_activation_control_events"
                )
                supervisor.terminate()
                supervisor.wait(timeout=10)
                children.stop()
                old_health_mtime = health_path.stat().st_mtime_ns
                children.start(seq, consumer)
                supervisor = subprocess.Popen(
                    [python, script, "supervisor", str(run.directory)],
                    cwd=run.directory,
                    env=readonly_env,
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
                if supervisor.pid == previous_supervisor_pid or set(
                    previous_transport_pids
                ) & set(children.pids[-2:]):
                    raise RuntimeError("projectless processes did not actually restart")
                while True:
                    h = health()
                    if h.get("detail", {}).get("failed_count"):
                        raise RuntimeError("projectless restart workspace failed")
                    if (
                        health_path.stat().st_mtime_ns > old_health_mtime
                        and h.get("phase") == "idle"
                        and h.get("healthy")
                        and h.get("detail", {}).get("processed_count") == 4
                    ):
                        break
                    time.sleep(0.25)
                if (
                    identity_path.read_bytes() != identity_bytes
                    or observer.execute(
                        "SELECT count() FROM property_catalog_activation_control_events"
                    )
                    != control_count
                ):
                    raise RuntimeError("projectless restart changed identity/controls")
                app_run.command(
                    [python, script, "projectless-deleted-restart", str(run.directory)],
                    timeout=100,
                )
                save(
                    run.directory / "projectless-restart-processes.json",
                    {
                        "status": "passed",
                        "supervisor_before": previous_supervisor_pid,
                        "supervisor_after": supervisor.pid,
                        "transport_before": previous_transport_pids,
                        "transport_after": children.pids[-2:],
                        "same_identity_bytes": True,
                        "same_control_count": True,
                        "health": h,
                    },
                )
            finally:
                app_run.env = previous_env
            report = {
                "status": "activation_observed",
                "activation": rows,
                "identity_byte_stable": True,
                "normal_orm_fixture": True,
                "reader_api_verified": True,
                "reader_control": controls,
                "full_lifecycle_gate": "incomplete",
            }
            save(run.directory / "application-evidence.json", report)
            return report
        finally:
            observer.disconnect()
    finally:
        if supervisor is not None:
            supervisor.terminate()
            try:
                supervisor.wait(timeout=10)
            except subprocess.TimeoutExpired:
                supervisor.kill()
                supervisor.wait(timeout=3)
        log.close()
        children.stop()


if __name__ == "__main__":
    backend(sys.argv[1], Path(sys.argv[2]).resolve(strict=True))
