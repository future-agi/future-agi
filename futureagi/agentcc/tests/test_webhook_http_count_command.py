"""Count-only command contract against isolated database fixtures."""

from io import StringIO

import pytest
from django.core.management import call_command
from django.db import connection
from django.test.utils import CaptureQueriesContext

from agentcc.models.webhook import AgentccWebhook
from agentcc.tests.test_webhook_encrypted_fixtures import make_webhook
from agentcc.tests.test_webhook_encrypted_fixtures import (
    webhook_encryption_key as webhook_encryption_key,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def http_rows():
    model = AgentccWebhook._meta.get_field("organization").remote_field.model
    organizations = [
        model._base_manager.create(name="http-count-first"),
        model._base_manager.create(name="http-count-second"),
    ]
    urls = [
        "http://hooks.example.com/first",
        "HTTP://hooks.example.com/second",
        "HtTp://hooks.example.com/third",
        "https://hooks.example.com/http://not-a-scheme",
        "https://hooks.example.com/fourth",
    ]
    for index, url in enumerate(urls):
        make_webhook(
            manager=AgentccWebhook._base_manager,
            organization=organizations[index % 2],
            name=f"receiver-{index}",
            url=url,
            is_active=index != 1,
            deleted=index == 2,
        )
    return AgentccWebhook._base_manager.count()


def count_output(caplog):
    stdout, stderr = StringIO(), StringIO()
    log_start = len(caplog.records)
    with CaptureQueriesContext(connection) as queries:
        call_command("agentcc_webhook_http_count", stdout=stdout, stderr=stderr)
    assert stderr.getvalue() == ""
    assert caplog.records[log_start:] == []
    # One aggregate SELECT; no per-row reads or writes.
    assert len(queries) == 1
    sql = queries[0]["sql"].lstrip().upper()
    assert sql.startswith("SELECT COUNT(")
    return stdout.getvalue()


def test_empty_database_prints_one_zero_line(caplog):
    assert AgentccWebhook._base_manager.count() == 0
    assert count_output(caplog) == "0\n"


@pytest.mark.parametrize("profile", ["hosted", "self-hosted", ""])
def test_count_is_case_insensitive_global_and_value_free(
    http_rows, monkeypatch, profile, caplog
):
    monkeypatch.setenv("AGENTCC_DEPLOYMENT_PROFILE", profile)
    # Inactive and soft-deleted rows still have stored URLs. No workspace or
    # tenant manager may silently hide rows from the pre-cutover inventory.
    assert count_output(caplog) == "3\n"
    assert AgentccWebhook._base_manager.count() == http_rows
    assert AgentccWebhook._base_manager.filter(deleted=True).count() == 1
    assert AgentccWebhook._base_manager.filter(is_active=False).count() == 1
