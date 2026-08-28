"""P1.12 — Migration boot smoke tests for the three deployment flavors.

Verifies that Django can boot and apply migrations for the app set currently
in INSTALLED_APPS. Catches schema drift between OSS, self-hosted EE and
cloud before a release pipeline builds an image that fails on migrate.
"""

from __future__ import annotations

import pytest
from django.apps import apps
from django.conf import settings
from django.core.management import call_command
from django.db import connection


@pytest.mark.django_db
def test_default_installed_apps_migrate_cleanly():
    call_command("migrate", "--noinput", verbosity=0)
    with connection.cursor() as cursor:
        cursor.execute("SELECT COUNT(*) FROM django_migrations")
        (applied_count,) = cursor.fetchone()
    assert applied_count > 0


# makemigrations --check verifies migration-history consistency against every
# alias the router's allow_migrate accepts — default_direct included.
@pytest.mark.django_db(databases=["default", "default_direct"])
def test_makemigrations_reports_no_pending_changes():
    from io import StringIO

    stdout = StringIO()
    call_command(
        "makemigrations",
        "--check",
        "--dry-run",
        verbosity=0,
        stdout=stdout,
    )


@pytest.mark.django_db
def test_control_plane_tables_exist_when_installed():
    """When cloud control-plane app is registered, its core tables must exist.

    Prevents shipping a cloud image whose migrations were half-applied.
    """
    if not any(
        app == "ee.cloud.control_plane" or app.endswith(".CloudControlPlaneConfig")
        for app in settings.INSTALLED_APPS
    ):
        pytest.skip("Cloud control-plane not installed in this configuration")

    grant_model = apps.get_model("control_plane", "LicenseGrant")
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT COUNT(*) FROM %s" % grant_model._meta.db_table  # noqa: S608
        )


@pytest.mark.django_db
def test_ee_licensing_boots_when_installed():
    """When ee.licensing is registered, importing its state must not raise."""
    if not any(
        app == "ee.licensing" or app.endswith(".LicensingConfig")
        for app in settings.INSTALLED_APPS
    ):
        pytest.skip("ee.licensing not installed in this configuration")

    from ee.licensing.state import get_snapshot

    snapshot = get_snapshot()
    assert snapshot is not None
