"""``manage.py create_user``: the first account of an install, which
./bin/install runs and the Helm install notes tell the operator to run."""

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from accounts.models import User


@pytest.mark.parametrize(
    ("password", "reason"),
    # Sign-up trims the password before it checks it.
    [("password", "too common"), ("1234567 ", "too short")],
)
def test_create_user_refuses_a_password_the_validators_reject(db, password, reason):
    with pytest.raises(CommandError, match=reason):
        call_command(
            "create_user",
            "--email",
            "owner@example.com",
            "--name",
            "Owner",
            "--password",
            password,
        )

    assert not User.objects.filter(email="owner@example.com").exists()


@pytest.mark.parametrize("email", ["owner", "owner@example"])
def test_create_user_refuses_a_malformed_email(db, email):
    with pytest.raises(CommandError, match="Enter a valid email address"):
        call_command(
            "create_user",
            "--email",
            email,
            "--name",
            "Owner",
            "--password",
            "Owner-Passw0rd!",
        )

    assert not User.objects.filter(email__iexact=email).exists()


def test_create_user_says_an_existing_email_already_exists(db):
    User.objects.create_user(
        email="owner@example.com", password="Owner-Passw0rd!", name="Owner"
    )

    # Whatever the password: a re-run of ./bin/install -y counts output with
    # "already exists" as an account it already made.
    with pytest.raises(CommandError, match="already exists"):
        call_command(
            "create_user",
            "--email",
            "Owner@Example.com",
            "--name",
            "Owner",
            "--password",
            "password",
        )

    owner = User.objects.get(email__iexact="owner@example.com")
    assert owner.check_password("Owner-Passw0rd!")
