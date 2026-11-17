"""A key sealed under the published pepper verifies once the install has its own.

bin/update (deploy/update/pepper.py) and bin/install give an install still on the
published FI_API_SECRET_PEPPER a new one and keep the published value in
FI_API_SECRET_PEPPERS_RETIRED, which reaches settings as one comma-separated
string. Keys sealed before that change must keep working, and only through the
retired entry. Pure: no database, made-up values only.
"""

from django.test import override_settings

from accounts import api_secrets

PUBLISHED_PEPPER = "local-dev-only-api-secret-pepper-replace-me"
# What bin/update writes: 64 lowercase hex characters (made up here).
NEW_PEPPER = "0123456789abcdef" * 4
OLDER_PEPPER = "an-older-operator-pepper-0123456789abcdef"
SECRET = "made-up-api-secret-0042"


def _sealed_under_the_published_default():
    with override_settings(
        API_SECRET_PEPPER=PUBLISHED_PEPPER,
        API_SECRET_PEPPERS_RETIRED=[],
        ENV_TYPE="local",
    ):
        return api_secrets.hash_secret(SECRET)


class TestPublishedPepperRetiredByTheUpdate:
    def test_a_key_sealed_under_the_published_default_verifies_with_it_retired(self):
        stored = _sealed_under_the_published_default()
        for retired in (
            f"{OLDER_PEPPER},{PUBLISHED_PEPPER}",
            f" {PUBLISHED_PEPPER} ",
            [OLDER_PEPPER, PUBLISHED_PEPPER],
        ):
            with override_settings(
                API_SECRET_PEPPER=NEW_PEPPER,
                API_SECRET_PEPPERS_RETIRED=retired,
                ENV_TYPE="production",
            ):
                verdict = api_secrets.verify_secret(stored, "", SECRET)
                assert verdict.ok and verdict.needs_rehash
                assert not api_secrets.verify_secret(stored, "", SECRET + "x").ok
                # New keys are sealed under the install's own pepper.
                assert api_secrets.hash_secret(SECRET) != stored

    def test_it_does_not_verify_without_the_retired_entry(self):
        stored = _sealed_under_the_published_default()
        for retired in ("", [], OLDER_PEPPER):
            with override_settings(
                API_SECRET_PEPPER=NEW_PEPPER,
                API_SECRET_PEPPERS_RETIRED=retired,
                ENV_TYPE="production",
            ):
                assert not api_secrets.verify_secret(stored, "", SECRET).ok
