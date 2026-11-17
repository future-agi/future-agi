"""The Hub's operator commands as an operator runs them (fix round 3).

Round 2's runbook named `omega_hub_storage` and `omega_hub_outbox`, and the mutation-free startup
guard (model_hub/apps.py) refused both in every process: `call_command` in the other tests never
meets the guard. The subprocess tests here run `python manage.py ...` as a new process against
this test database, so the guard sees the real argv. Also here: retention's per-key isolation, the
daily rules notice, the dead-letter listing, and the unrouted page.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

import pytest
from django.core.management import CommandError, call_command
from django.db import connection
from django.utils import timezone

from model_hub.apps import (
    explicit_management_mutation_authorized,
    guarded_management_command,
)
from omega_hub.models import AuditEvent, EmailOutbox, EmailOutboxStatus, Submission
from omega_hub.services import ops, storage

BACKEND_ROOT = Path(__file__).resolve().parents[2]
GUARD_ENV = (
    "SERVICE_TYPE",
    "STARTUP_DB_MUTATION_MODE",
    "NO_STARTUP_DB_MUTATIONS",
    "ENV_TYPE",
    "CLOUD_DEPLOYMENT",
)
# omega_hub_maintainer is not here: a named person grants a maintainer on any production install
# (`$COMPOSE exec backend python manage.py omega_hub_maintainer grant`, deploy/README.md), so it is a
# startup-safe command (HUB-DEPLOY-SHAPES, model_hub/apps.py; tests/test_oss_install_hardening.py).
HUB_OPERATOR_COMMANDS = (
    "omega_hub_storage",
    "omega_hub_outbox",
    "omega_hub_retention",
    "omega_hub_rules_notify",
    "omega_hub_import_official",
    "omega_hub_ensure_official_publisher",
)
OPERATOR = {
    "SERVICE_TYPE": "bootstrap",
    "STARTUP_DB_MUTATION_MODE": "operator",
    "ENV_TYPE": "production",
}
# The subprocesses keep the test settings' ENV_TYPE; the unit test above covers a hosted one.
OPERATOR_JOB = {"SERVICE_TYPE": "bootstrap", "STARTUP_DB_MUTATION_MODE": "operator"}


@pytest.mark.parametrize("command", HUB_OPERATOR_COMMANDS)
@pytest.mark.parametrize(
    "env, authorized",
    [
        (OPERATOR, True),
        ({"NO_STARTUP_DB_MUTATIONS": "false", "ENV_TYPE": "local"}, True),
        ({"NO_STARTUP_DB_MUTATIONS": "false", "ENV_TYPE": "production"}, False),
        ({"SERVICE_TYPE": "backend", "ENV_TYPE": "production"}, False),
        ({"SERVICE_TYPE": "temporal-schedules", "ENV_TYPE": "production"}, False),
        ({"ENV_TYPE": "local"}, False),
    ],
)
def test_hub_operator_commands_run_only_where_mutations_are_authorized(
    monkeypatch, command, env, authorized
):
    for name in GUARD_ENV:
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    argv = ["manage.py", command]
    assert guarded_management_command(argv) == command  # never an any-process command
    assert explicit_management_mutation_authorized(argv) is authorized


def _manage_py(*args, **env):
    environment = {k: v for k, v in os.environ.items() if k not in GUARD_ENV}
    environment.update(
        DJANGO_SETTINGS_MODULE="tfc.settings.test",
        PG_DB=connection.settings_dict["NAME"],
        NO_STARTUP_DB_MUTATIONS="true",
        FUTURE_AGI_LICENSE_URL="http://127.0.0.1:9",
    )
    environment.update(env)
    return subprocess.run(
        [sys.executable, "manage.py", *args],
        cwd=BACKEND_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=300,
        start_new_session=True,
    )


@pytest.fixture
def dead_row(transactional_db):
    row = EmailOutbox.objects.create(
        template="approved",
        recipients=["publisher@example.test"],
        subject="Approved",
        body="frozen",
        status=EmailOutboxStatus.DEAD,
        attempts=8,
        dead_at=timezone.now(),
        last_error="421 try later",
    )
    # transactional_db flushes every table afterwards (TRUNCATE), the append-only audit log included.
    return row


def test_the_runbook_commands_run_as_an_operator_job(dead_row, tmp_path):
    hub_env = {
        "OMEGA_HUB_STORAGE": "filesystem",
        "OMEGA_HUB_STORAGE_DIR": str(tmp_path),
        "TEMPORAL_HOST": "127.0.0.1:9",
    }
    refused = _manage_py("omega_hub_outbox", "--all-dead", **hub_env)
    assert refused.returncode != 0
    assert "omega_hub_outbox is disabled during mutation-free startup" in refused.stderr
    assert EmailOutbox.objects.get(pk=dead_row.pk).status == EmailOutboxStatus.DEAD

    listed = _manage_py("omega_hub_outbox", "--list", **hub_env, **OPERATOR_JOB)
    assert listed.returncode == 0, listed.stderr[-2000:]
    # The test settings print a banner on stdout first; the command's rows are the JSON lines.
    rows = [
        json.loads(line) for line in listed.stdout.splitlines() if line.startswith("{")
    ]
    assert [row["id"] for row in rows] == [str(dead_row.pk)]
    assert rows[0]["lastError"] == "421 try later" and rows[0]["attempts"] == 8
    assert "frozen" not in listed.stdout  # bodies stay out of operator output

    replayed = _manage_py("omega_hub_outbox", "--all-dead", **hub_env, **OPERATOR_JOB)
    assert replayed.returncode == 0, replayed.stderr[-2000:]
    assert "replayed 1 dead-letter row(s)" in replayed.stdout
    row = EmailOutbox.objects.get(pk=dead_row.pk)
    assert (row.status, row.attempts, row.dead_at) == (
        EmailOutboxStatus.PENDING,
        0,
        None,
    )
    assert AuditEvent.objects.get(action="outbox.replayed").data["count"] == 1

    for args in (("omega_hub_storage", "status"), ("omega_hub_retention", "--dry-run")):
        done = _manage_py(*args, **hub_env, **OPERATOR_JOB)
        assert done.returncode == 0, (args, done.stderr[-2000:])
        local = _manage_py(
            *args, **hub_env, NO_STARTUP_DB_MUTATIONS="false", ENV_TYPE="local"
        )
        assert local.returncode == 0, (args, local.stderr[-2000:])


def test_official_bootstrap_runs_through_the_real_operator_startup_guard(
    transactional_db,
):
    from omega_hub.models import Publisher
    from omega_hub.tests.helpers import make_person

    person = make_person("official-bootstrap-operator")
    person.user.is_staff = True
    person.user.save(update_fields=["is_staff"])
    command = ("omega_hub_ensure_official_publisher", "--owner-email", person.email)
    refused = _manage_py(*command, OMEGA_HUB_MAINTAINER_ORG_ID="")
    assert refused.returncode != 0
    assert (
        "omega_hub_ensure_official_publisher is disabled during mutation-free startup"
        in refused.stderr
    )
    assert not Publisher.objects.filter(handle="future-agi").exists()
    allowed = _manage_py(*command, OMEGA_HUB_MAINTAINER_ORG_ID="", **OPERATOR_JOB)
    assert allowed.returncode == 0, allowed.stderr[-2000:]
    official = Publisher.objects.get(handle="future-agi")
    assert (
        official.owner_org_id == person.organization.pk
        and official.tier == Publisher.Tier.OFFICIAL
    )
    assert (
        AuditEvent.objects.get(action="publisher.official_ensured").actor_id
        == person.user.pk
    )


class FlakyStore(storage.FilesystemStore):
    """A store whose purge fails for one key and defers another, like a WORM-locked bundle."""

    def __init__(self, root, *, fail: set, defer: set):
        super().__init__(root)
        self.fail, self.defer, self.purged = fail, defer, []

    def purge(self, key):
        if key in self.fail:
            raise RuntimeError("S3Error: code: InternalError")
        if key in self.defer:
            return 1
        self.purged.append(key)
        return super().purge(key)


def _old_withdrawn(world, count):
    subs = []
    for index in range(count):
        sub = Submission.objects.create(
            publisher=world.official,
            submitted_by=world.maint_a.user,
            channel="web",
            raw_sha256=f"{index:064x}",
            raw_bytes=1,
            status=Submission.Status.WITHDRAWN,
            raw_key=f"raw/r{index}",
        )
        subs.append(sub)
    Submission.objects.filter(pk__in=[s.pk for s in subs]).update(
        updated_at=timezone.now() - timedelta(days=31)
    )
    return subs


@pytest.mark.django_db
def test_one_storage_error_does_not_stop_retention(world, tmp_path, monkeypatch):
    subs = _old_withdrawn(world, 3)
    old = EmailOutbox.objects.create(
        template="t",
        recipients=["x@example.test"],
        subject="s",
        body="b",
        status=EmailOutboxStatus.SENT,
    )
    EmailOutbox.objects.filter(pk=old.pk).update(
        updated_at=timezone.now() - timedelta(days=91)
    )
    store = FlakyStore(str(tmp_path), fail={"raw/r0"}, defer=set())
    monkeypatch.setattr(storage, "get_store", lambda: store)
    out = io.StringIO()
    with pytest.raises(CommandError, match="could not purge or renew 1 key"):
        call_command("omega_hub_retention", stdout=out)
    assert sorted(store.purged) == ["raw/r1", "raw/r2"]
    assert not EmailOutbox.objects.filter(pk=old.pk).exists()
    keys = sorted(
        Submission.objects.filter(pk__in=[s.pk for s in subs]).values_list(
            "raw_key", flat=True
        )
    )
    assert keys == [
        "",
        "",
        "raw/r0",
    ]  # only the failed upload keeps its key, for the next night
    assert "1 keys failed" in out.getvalue()


@pytest.mark.django_db
def test_a_retention_locked_version_is_deferred_not_failed(
    world, tmp_path, monkeypatch
):
    subs = _old_withdrawn(world, 2)
    store = FlakyStore(str(tmp_path), fail=set(), defer={"raw/r1"})
    monkeypatch.setattr(storage, "get_store", lambda: store)
    out = io.StringIO()
    call_command("omega_hub_retention", stdout=out)
    assert (
        "deleted 1 raw uploads" in out.getvalue()
        and "1 locked bundle versions deferred" in out.getvalue()
    )
    assert Submission.objects.get(pk=subs[1].pk).raw_key == "raw/r1"


@pytest.mark.django_db
def test_the_daily_rules_notice_is_a_no_op_once_announced(world, settings):
    from omega_hub.temporal import run_rules_notify

    settings.OMEGA_HUB_RULES_NOTICE_SUMMARY = "Checklist item 3 now names live hosts."
    assert run_rules_notify._original_func() == {"status": "ok"}
    assert run_rules_notify._original_func() == {"status": "ok"}
    assert AuditEvent.objects.filter(action="rules.notified").count() == 1
    with pytest.raises(CommandError, match="already told"):
        call_command(
            "omega_hub_rules_notify", "--summary", "By hand.", stdout=io.StringIO()
        )


@pytest.mark.django_db
def test_a_page_with_no_maintainer_is_logged_every_check(world, monkeypatch):
    from django.contrib.auth.models import Permission

    from omega_hub.services import notify

    Permission.objects.filter(codename="hub_maintainer").delete()
    logged = []
    monkeypatch.setattr(
        notify.logger, "error", lambda event, **kw: logged.append((event, kw))
    )
    alert = {"id": "omega_hub_checker_stalled"}
    assert ops.page([alert]) == 0
    assert ops.page([alert]) == 0
    assert [event for event, _ in logged] == ["omega_hub_ops_alert_unrouted"] * 2
    assert EmailOutbox.objects.count() == 0
