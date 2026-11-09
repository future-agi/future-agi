"""HUB-09 (READINESS E02, the Hub erasure test): deleting a member who owns a Hub publisher works,
the entries stay installable under "Deleted account", and the report survives without PII.

The deletion goes through the platform's own member DELETE (`DELETE /accounts/delete-users/`, an
organisation Owner removing a member), not through the ORM, so the test fails if any part of the
platform's delete path still refuses. A second organisation's publisher is the other tenant: it
must not change.
"""

import pytest
from django.core import mail

from accounts.models.organization import Organization
from accounts.models.user import User
from omega_hub import pseudonym
from omega_hub.models import (
    AuditEvent,
    EmailVerification,
    Entry,
    HubToken,
    Like,
    Publisher,
    Report,
)
from omega_hub.models.deletion import DELETED_DISPLAY_NAME
from omega_hub.tests.flows import publish
from omega_hub.tests.helpers import make_person, session_client
from tfc.constants.levels import Level

pytestmark = pytest.mark.django_db
OFFICIAL = "/omega-hub/v1/entries/future-agi/first-agent"
CAROLS = "/omega-hub/v1/entries/c-test/notes-plugin"


def _carol_has_hub_history(world):
    """Carol: a user publisher with a listed entry, a like, a report naming her, audit rows, a token."""
    publish(world)  # future-agi/first-agent, by the maintainers' organisation
    publish(world, "notes-plugin", token=world.token_c, publisher="c-test")
    assert world.c.post(f"{OFFICIAL}/like").status_code == 200
    details = (f"I am {world.carol.user.name} ({world.carol.email}); this entry crashes on start. "
               f"Write to {world.carol.email.upper()} for the stack trace.")
    filed = world.c.post("/omega-hub/v1/reports", {"entry": "future-agi/first-agent", "reason": "broken",
                                                   "details": details}, format="json")
    assert filed.status_code == 201, filed.content
    return Report.objects.get(pk=filed.json()["id"])


def _delete_member(admin, member):
    client = session_client(admin)
    return client.delete("/accounts/delete-users/", {"user_ids": [str(member.user.pk)]}, format="json")


def test_a_member_who_owns_a_publisher_is_deleted_and_the_hub_keeps_their_entries(world):
    report = _carol_has_hub_history(world)
    carol, label = world.carol, pseudonym.label_for(world.carol.user)
    carol_audit = set(AuditEvent.objects.filter(actor=carol.user).values_list("pk", flat=True))
    assert carol_audit and report.reporter_label == label
    # The other tenant: a second organisation with its own publisher and entry, untouched by Carol's deletion.
    other = make_person("erin")
    other_pub = Publisher.objects.create(handle="erin-org", kind="organization", owner_org=other.organization,
                                         display_name="Erin Org", bio="Tools by Erin")
    dana = make_person("dana", org_level=Level.OWNER, organization=carol.organization)

    response = _delete_member(dana, carol)

    assert response.status_code == 200, response.content
    assert response.json() == [{"user_id": str(carol.user.pk), "message": "User deleted successfully."}]
    assert not User.objects.filter(pk=carol.user.pk).exists()

    # The publisher is tombstoned, its entry stays listed and installable.
    pub = Publisher.objects.get(handle="c-test")
    assert pub.erased_at is not None and pub.owner_user_id is None and pub.owner_org_id is None
    assert (pub.display_name, pub.bio, pub.website, pub.github_login, pub.avatar_key) == (DELETED_DISPLAY_NAME, "", "", "", "")
    anon = __import__("rest_framework.test", fromlist=["APIClient"]).APIClient()
    detail = anon.get(CAROLS)
    assert detail.status_code == 200 and detail.json()["publisher"]["displayName"] == DELETED_DISPLAY_NAME
    bundle = anon.get(f"{CAROLS}/versions/1.0.0/bundle")
    assert bundle.status_code == 200 and bundle.content
    listed = {card["ref"] for card in anon.get("/omega-hub/v1/entries").json()["results"]}
    assert {"c-test/notes-plugin", "future-agi/first-agent"} <= listed
    profile = anon.get("/omega-hub/v1/publishers/c-test")
    assert profile.status_code == 200 and carol.email not in profile.content.decode()

    # The report survives without PII; the pseudonym written when it was filed remains.
    report.refresh_from_db()
    assert report.reporter_id is None and report.reporter_label == label
    assert carol.email.lower() not in report.details.lower() and carol.user.name not in report.details
    assert "crashes on start" in report.details and "[deleted account]" in report.details
    rows = world.b.get("/omega-hub/v1/review/reports").json()
    row = next(r for r in rows if r["id"] == str(report.pk))
    assert row["reporter"] is None and row["reporterLabel"] == label

    # Audit rows keep a stable pseudonymous actor; one row records the erasure under it.
    kept = AuditEvent.objects.filter(pk__in=carol_audit)
    assert kept.count() == len(carol_audit)
    assert set(kept.values_list("actor_id", flat=True)) == {None}
    assert set(kept.values_list("actor_label", flat=True)) == {label}
    erased = AuditEvent.objects.get(action="account.erased")
    assert erased.target_id == label and erased.data["publishers"] == ["c-test"] and erased.data["reports"] == 1
    assert carol.email not in str(erased.data)
    audit = world.b.get("/omega-hub/v1/review/audit?action=report.opened").json()["results"]
    assert audit[0]["actor"] is None and audit[0]["actorLabel"] == label

    # Likes go with the account and the count is recounted; tokens and the verification go too.
    assert Entry.objects.get(publisher__handle="future-agi", name="first-agent").like_count == 0
    assert anon.get(OFFICIAL).json()["likes"] == 0
    assert not Like.objects.exists() and not HubToken.objects.filter(publisher=pub).exists()
    assert not EmailVerification.objects.filter(email=carol.email).exists()

    # The other tenant and the maintainers' publisher are unchanged.
    other_pub.refresh_from_db()
    assert (other_pub.erased_at, other_pub.owner_org_id, other_pub.display_name) == (None, other.organization.pk, "Erin Org")
    official = Publisher.objects.get(handle="future-agi")
    assert official.erased_at is None and official.owner_org_id == world.maint_org.pk


def test_resolving_a_report_whose_reporter_was_deleted_still_works(world):
    report = _carol_has_hub_history(world)
    dana = make_person("dana", org_level=Level.OWNER, organization=world.carol.organization)
    assert _delete_member(dana, world.carol).status_code == 200
    mail.outbox.clear()
    resolved = world.b.post(f"/omega-hub/v1/review/reports/{report.pk}/resolve",
                            {"action": "dismiss", "note": "Works on a clean checkout."}, format="json")
    assert resolved.status_code == 200, resolved.content
    assert Report.objects.get(pk=report.pk).status == Report.Status.DISMISSED
    assert mail.outbox == []  # nobody to tell


def test_deleting_an_organisation_tombstones_its_publisher_and_keeps_the_entry(world):
    owner = make_person("olga")
    org = owner.organization
    pub = Publisher.objects.create(handle="olga-org", kind="organization", owner_org=org, display_name="Olga Org")
    other = Publisher.objects.get(handle="c-test")

    from tfc.utils.audit import mute_audit_signals

    # The platform's own audit signal writes an accounts_audit_log row that points at the organisation
    # being deleted (a platform issue for its organisation-deletion path); muted here so the test
    # isolates what the Hub does.
    with mute_audit_signals():
        org.delete()

    pub.refresh_from_db()
    assert pub.erased_at is not None and pub.owner_org_id is None and pub.display_name == DELETED_DISPLAY_NAME
    assert not Organization.objects.filter(pk=org.pk).exists()
    assert AuditEvent.objects.filter(action="organization.erased", data__publishers=["olga-org"]).exists()
    other.refresh_from_db()
    assert other.erased_at is None and other.owner_user_id == world.carol.user.pk


def test_an_erased_publisher_cannot_be_acted_for_and_a_new_person_cannot_claim_it(world):
    _carol_has_hub_history(world)
    dana = make_person("dana", org_level=Level.OWNER, organization=world.carol.organization)
    assert _delete_member(dana, world.carol).status_code == 200
    renamed = session_client(dana).patch("/omega-hub/v1/publishers/c-test", {"bio": "mine now"}, format="json")
    assert renamed.status_code == 403, renamed.content
    taken = session_client(dana).post("/omega-hub/v1/publishers", {"handle": "c-test", "kind": "user",
                                                                   "displayName": "Dana"}, format="json")
    assert taken.status_code == 409, taken.content


def test_the_migration_backfill_labels_rows_written_before_it(world):
    """Rows written before migration 0003 have no label; the backfill gives them the same pseudonym."""
    import importlib

    from django.db import connection
    from django.db.migrations.loader import MigrationLoader

    migration = importlib.import_module("omega_hub.migrations.0003_erasure_and_email_verification")
    # The migration runs on historical models, whose manager is Django's plain one.
    apps = MigrationLoader(connection).project_state(("omega_hub", "0003_erasure_and_email_verification")).apps
    report = _carol_has_hub_history(world)
    Report.objects.filter(pk=report.pk).update(reporter_label="")
    AuditEvent._base_manager.filter(actor=world.carol.user).update(actor_label="")

    class Editor:
        pass

    editor = Editor()
    editor.connection = connection
    migration.backfill_labels(apps, editor)
    migration.backfill_labels(apps, editor)  # idempotent

    label = pseudonym.label_for(world.carol.user)
    assert Report.objects.get(pk=report.pk).reporter_label == label
    assert set(AuditEvent.objects.filter(actor=world.carol.user).values_list("actor_label", flat=True)) == {label}
