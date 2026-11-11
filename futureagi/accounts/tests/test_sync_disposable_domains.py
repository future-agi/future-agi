"""sync_disposable_domains: accounts/disposable_domains.py -> BlockedEmailDomain."""

from io import StringIO

import pytest
from django.conf import settings
from django.core.management import call_command

from accounts.management.commands import sync_disposable_domains as command
from accounts.models import BlockedEmailDomain
from accounts.models.blocked_email_domain import BlockedEmailDomainSource

MANUAL = BlockedEmailDomainSource.MANUAL
LISTED = BlockedEmailDomainSource.DISPOSABLE_LIST


@pytest.fixture
def small_list(monkeypatch):
    def use(*domains, extra=()):
        monkeypatch.setattr(command, "DISPOSABLE_EMAIL_DOMAINS", frozenset(domains))
        monkeypatch.setattr(command, "EXTRA_DISPOSABLE_EMAIL_DOMAINS", frozenset(extra))

    return use


def rows():
    return dict(BlockedEmailDomain.objects.values_list("domain", "source"))


@pytest.mark.django_db
class TestSyncDisposableDomains:
    def test_loads_the_list_and_the_local_extras(self, small_list):
        small_list("mailinator.com", "yopmail.com", extra=["uberip.com"])

        assert command.sync_disposable_domains() == (3, 0)

        assert rows() == {
            "mailinator.com": LISTED,
            "yopmail.com": LISTED,
            "uberip.com": LISTED,
        }

    def test_a_second_run_changes_nothing(self, small_list):
        small_list("mailinator.com", "yopmail.com")
        command.sync_disposable_domains()

        assert command.sync_disposable_domains() == (0, 0)

    def test_a_domain_upstream_dropped_is_removed(self, small_list):
        small_list("mailinator.com", "yopmail.com")
        command.sync_disposable_domains()
        small_list("mailinator.com")

        assert command.sync_disposable_domains() == (0, 1)

        assert rows() == {"mailinator.com": LISTED}

    def test_a_domain_the_team_added_is_never_removed(self, small_list):
        BlockedEmailDomain.objects.create(domain="fldubai.com", reason="spam")
        small_list("mailinator.com")

        command.sync_disposable_domains()

        assert rows()["fldubai.com"] == MANUAL

    def test_a_listed_domain_the_team_already_added_stays_manual(self, small_list):
        """A manual block applies on every deployment; turning it into a list
        row would quietly reopen self-hosted installs to it."""
        BlockedEmailDomain.objects.create(domain="mailinator.com")
        small_list("mailinator.com")

        assert command.sync_disposable_domains() == (0, 0)

        assert rows() == {"mailinator.com": MANUAL}

    def test_dry_run_changes_nothing(self, small_list):
        small_list("mailinator.com")

        assert command.sync_disposable_domains(dry_run=True) == (1, 0)

        assert rows() == {}

    def test_the_command_prints_its_counts(self, small_list, monkeypatch):
        monkeypatch.setattr(settings, "CLOUD_DEPLOYMENT", "US")
        small_list("mailinator.com", "yopmail.com")
        out = StringIO()

        call_command("sync_disposable_domains", stdout=out)

        assert "added 2, removed 0" in out.getvalue()

    def test_the_command_does_nothing_on_a_self_hosted_install(
        self, small_list, monkeypatch
    ):
        monkeypatch.setattr(settings, "CLOUD_DEPLOYMENT", "")
        small_list("mailinator.com")
        out = StringIO()

        call_command("sync_disposable_domains", stdout=out)

        assert rows() == {}
        assert "not managed cloud" in out.getvalue()

    def test_the_real_list_loads(self):
        """The packaged list has ~10k entries; every one must fit the column."""
        added, removed = command.sync_disposable_domains()

        assert added > 1000
        assert removed == 0
        assert BlockedEmailDomain.objects.filter(source=MANUAL).count() == 0
