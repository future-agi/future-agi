"""omega_hub_retention, omega_hub_rules_notify and omega_hub_maintainer (SPEC §5.1, §6.8, §8)."""

import io
from datetime import timedelta

import pytest
from django.core import mail
from django.core.management import CommandError, call_command
from django.utils import timezone

from omega_hub.models import AuditEvent, Submission
from omega_hub.services import storage
from omega_hub.tests.flows import publish
from omega_hub.tests.helpers import entry_files, make_person, submit, token_client
from tfc.constants.levels import Level

pytestmark = pytest.mark.django_db


def test_retention_deletes_old_raw_uploads_but_keeps_approved_bundles(world):
    entry = publish(world, "first-agent")
    sub_id = submit(token_client(world.token_a), "future-agi", entry_files("notes-plugin")).json()["id"]
    token_client(world.token_a).post(f"/omega-hub/v1/submissions/{sub_id}/withdraw")
    raw_key = Submission.objects.get(pk=sub_id).raw_key
    Submission.objects.filter(pk=sub_id).update(updated_at=timezone.now() - timedelta(days=31))
    dry = io.StringIO()
    call_command("omega_hub_retention", "--dry-run", stdout=dry)
    assert "would delete 1 raw uploads" in dry.getvalue() and storage.get_store().exists(raw_key)
    call_command("omega_hub_retention", stdout=io.StringIO())
    assert not storage.get_store().exists(raw_key)
    assert storage.get_store().exists(entry.latest_version.bundle_key)


def test_rules_notify_emails_publishers_once_per_rules_version(world):
    publish(world, "first-agent")
    mail.outbox.clear()
    call_command("omega_hub_rules_notify", "--summary", "Checklist item 3 now names live hosts.", stdout=io.StringIO())
    assert AuditEvent.objects.filter(action="rules.notified").count() == 1
    with pytest.raises(CommandError):
        call_command("omega_hub_rules_notify", "--summary", "Again.", stdout=io.StringIO())


def test_maintainer_command_grants_and_audits_with_the_actor(world):
    person = make_person("new-maint", org_level=Level.ADMIN, organization=world.maint_org)
    out = io.StringIO()
    call_command("omega_hub_maintainer", "grant", "--email", person.email, "--by", world.maint_a.email, stdout=out)
    assert "is a maintainer now = True" in out.getvalue()
    event = AuditEvent.objects.get(action="maintainer.granted", target_id=str(person.user.pk))
    assert event.data["via"] == "command" and event.actor_id == world.maint_a.user.pk
    call_command("omega_hub_maintainer", "revoke", "--email", person.email, "--by", world.maint_a.email, stdout=io.StringIO())
    assert AuditEvent.objects.filter(action="maintainer.revoked", target_id=str(person.user.pk)).count() == 1
