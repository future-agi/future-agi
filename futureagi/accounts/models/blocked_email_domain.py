import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


def normalize_email_domain(value):
    """`@Spam.Example.com.` and `spam.example.com` are the same domain."""
    return (value or "").strip().lower().lstrip("@").strip(".")


class BlockedEmailDomainSource(models.TextChoices):
    # Added by a person in Django admin. Blocks signups on every deployment.
    MANUAL = "manual", "Added by the team"
    # Copied from accounts/disposable_domains.py by sync_disposable_domains on
    # every deploy. Blocks only where a work email is required (managed cloud),
    # as the in-code list always did.
    DISPOSABLE_LIST = "disposable_list", "Disposable-email list"


class BlockedEmailDomain(models.Model):
    """An email domain that may not create a new account.

    Kept in the database so the team can block a domain from Django admin the
    moment spam signups show up, without a deploy. A blocked domain also
    covers its subdomains: blocking `example.com` stops `mail.example.com` too.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    domain = models.CharField(
        max_length=255,
        unique=True,
        help_text="For example fldubai.com. Subdomains are blocked as well.",
    )
    reason = models.TextField(blank=True)
    source = models.CharField(
        max_length=32,
        choices=BlockedEmailDomainSource.choices,
        default=BlockedEmailDomainSource.MANUAL,
        db_index=True,
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["domain"]

    def __str__(self):
        return self.domain

    def clean(self):
        self.domain = normalize_email_domain(self.domain)
        if "@" in self.domain or "." not in self.domain or " " in self.domain:
            raise ValidationError(
                {
                    "domain": "Enter a domain such as example.com, not an email address or a bare TLD."
                }
            )

    def save(self, *args, **kwargs):
        self.domain = normalize_email_domain(self.domain)
        super().save(*args, **kwargs)
