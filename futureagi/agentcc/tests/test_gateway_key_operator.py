"""Operator refusals occur before database access; no key values are printed."""

import os
from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings

from agentcc.tests.test_hosted_startup import hosted_environment, hosted_settings


def operator_environment():
    env = hosted_environment()
    env.update(
        SERVICE_TYPE="bootstrap",
        STARTUP_DB_MUTATION_MODE="operator",
        PG_DIRECT_HOST="127.0.0.1",
        AGENTCC_HOSTED_RELEASE_REVISION="a" * 40,
        AGENTCC_GATEWAY_SOURCE_ENCRYPTION_KEY=env["INTEGRATION_ENCRYPTION_KEY"],
        AGENTCC_GATEWAY_TARGET_ENCRYPTION_KEY=Fernet.generate_key().decode(),
    )
    return env


@pytest.mark.parametrize(
    "phase,changes,args,match",
    [
        ("key-transition", {"SERVICE_TYPE": "backend"}, [], "operator"),
        ("key-transition", {"STARTUP_DB_MUTATION_MODE": "disabled"}, [], "operator"),
        ("key-transition", {}, [], "quiescence"),
        ("key-transition", {}, ["--quiesced"], "revision"),
        (
            "key-transition",
            {"AGENTCC_HOSTED_RELEASE_REVISION": "unreviewed"},
            ["--quiesced"],
            "revision",
        ),
        (
            "key-transition",
            {"AGENTCC_GATEWAY_TARGET_ENCRYPTION_KEY": "malformed"},
            ["--quiesced", "--compatible-runtime-revision", "a" * 40],
            "key",
        ),
        (
            "key-transition",
            {"PG_DIRECT_HOST": ""},
            ["--quiesced", "--compatible-runtime-revision", "a" * 40],
            "PG_DIRECT_HOST",
        ),
        (
            "key-transition",
            {},
            [
                "--quiesced",
                "--compatible-runtime-revision",
                "a" * 40,
                "--key-batch-size",
                "0",
            ],
            "bounds",
        ),
        (
            "key-transition",
            {},
            [
                "--quiesced",
                "--compatible-runtime-revision",
                "a" * 40,
                "--key-deadline-seconds",
                "0",
            ],
            "bounds",
        ),
        ("key-verify", {}, ["--apply"], "read-only"),
    ],
)
def test_key_operator_refuses_before_connecting(phase, changes, args, match):
    env = operator_environment()
    env.update(changes)
    with (
        patch.dict(os.environ, env, clear=True),
        override_settings(**hosted_settings(env)),
    ):
        with patch(
            "agentcc.management.commands.hosted_gateway_migrate.connections"
        ) as connections:
            with pytest.raises(CommandError, match=match) as failure:
                call_command("hosted_gateway_migrate", phase, *args)
            connections.__getitem__.assert_not_called()
    assert env["INTEGRATION_ENCRYPTION_KEY"] not in str(failure.value)
    assert env["AGENTCC_GATEWAY_TARGET_ENCRYPTION_KEY"] not in str(failure.value)
