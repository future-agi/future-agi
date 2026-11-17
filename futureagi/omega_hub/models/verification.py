"""Proof that an account controls its email address (SPEC §6.1 and decision 13; HUB-25).

The platform has no verified-email marker: sign-up and invitations activate an account through an
emailed link, but marketplace, Appsmith and direct API sign-ups create active accounts without one,
and a reactivated member is active again without any email. So the Hub keeps its own record, written
only when the person opens a link the Hub sent to that address. It counts only while the account's
email is still the one that was confirmed.
"""

from django.conf import settings
from django.db import models
from django.utils import timezone


class EmailVerification(models.Model):
    class Method(models.TextChoices):
        LINK = "link", "Link sent by the Hub"

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, primary_key=True, on_delete=models.CASCADE, related_name="omega_hub_email_verification"
    )
    email = models.EmailField()
    method = models.CharField(max_length=16, choices=Method.choices, default=Method.LINK)
    verified_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "omega_hub_email_verification"
