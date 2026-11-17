"""Cloud PostgreSQL: no migrate/bootstrap shortcut may seed a hosted install."""

from unittest.mock import patch

from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from django.db.migrations.recorder import MigrationRecorder
from django.test import Client
from django.urls import reverse

from agentcc.tests.hosted_rehearsal_support import Rehearsal


def seed_snapshot():
    from ee.usage.models.usage import SubscriptionTier, SubscriptionTierChoices

    assert ContentType.objects.filter(app_label="accounts", model="user").exists()
    assert Permission.objects.filter(
        content_type__app_label="accounts", codename="add_user"
    ).exists()
    assert set(SubscriptionTier.objects.values_list("name", flat=True)) >= set(
        SubscriptionTierChoices.values
    ), "hosted phases did not emit subscription seeding"
    return {
        "content_types": list(ContentType.objects.order_by("pk").values()),
        "permissions": list(Permission.objects.order_by("pk").values()),
        "tiers": list(
            SubscriptionTier.objects.order_by("pk").values_list("pk", "name")
        ),
        "migrations": list(MigrationRecorder.Migration.objects.order_by("pk").values()),
    }


def test_hosted_empty_install_seeds_signup_and_rerun(django_db_blocker):
    # Deliberately no django_db fixture: only the hosted command installs this
    # disposable database. Rehearsal refuses anything but an opted-in cloud VM.
    with django_db_blocker.unblock(), Rehearsal() as rehearsal:
        rehearsal.run("schema")
        rehearsal.apply("schema")
        rehearsal.run("backfill")
        rehearsal.apply("backfill")
        rehearsal.run("check")
        before = seed_snapshot()

        # External delivery seams only; the actual signup, user/org creation
        # and subscription lookup all execute. No post_migrate receiver mocked.
        with (
            patch(
                "accounts.views.signup.verify_recaptcha", return_value=True
            ) as captcha,
            patch("accounts.utils.track_mixpanel_event") as analytics,
            patch("accounts.utils.process_post_registration") as registration,
        ):
            response = Client().post(
                reverse("user-signup"),
                {
                    "email": "hosted-install@synthetic.example.com",
                    "full_name": "Synthetic Hosted Install",
                    "company_name": "Synthetic",
                    "recaptcha_response": "synthetic",
                },
                content_type="application/json",
            )
            assert response.status_code == 200, response.content
            captcha.assert_called_once()
            analytics.assert_called_once()
            registration.assert_called_once()
        from accounts.models import User
        from ee.usage.models.usage import OrganizationSubscription

        user = User.objects.get(email="hosted-install@synthetic.example.com")
        assert OrganizationSubscription.objects.filter(
            organization=user.organization
        ).exists()
        for phase in ("schema", "backfill"):
            rehearsal.run(phase)
            rehearsal.apply(phase)
        rehearsal.run("check")
        assert seed_snapshot() == before

        # A graph-only success must not hide missing post_migrate rows. Check
        # is read-only; an explicit no-op backfill repairs through the receiver.
        Permission.objects.filter(
            content_type__app_label="accounts", codename="add_user"
        ).delete()
        missing = Permission.objects.count()
        assert "post_migrate" in rehearsal.run("check", expected=1)
        assert Permission.objects.count() == missing
        rehearsal.apply("backfill")
        rehearsal.run("check")
        seed_snapshot()
