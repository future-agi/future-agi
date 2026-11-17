"""The console-run signer's key registry (fi-security 1d8b0ce0 ruling 4).

Written only by the operator command; at most two active keys per deployment;
list and revoke; every change audit-logged; public keys only, never printed.
Every key here is generated for the test.
"""

import base64
import io

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from django.contrib import admin
from django.core.management import call_command
from structlog.testing import capture_logs

from agentcc.models.console_run_signer_key import AgentccConsoleRunSignerKey
from agentcc.services.console_run_signer_keys import active_key, registered_deployment

pytestmark = pytest.mark.django_db

COMMAND = "agentcc_console_run_signer_keys"


def new_key():
    private = Ed25519PrivateKey.generate()
    raw = private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return private, base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def run(*args):
    out, err = io.StringIO(), io.StringIO()
    call_command(COMMAND, *args, stdout=out, stderr=err)
    return out.getvalue()


def add(kid, public_key, deployment="ee-prod-1", operator="ops@example.test"):
    return run(
        "--add",
        "--deployment",
        deployment,
        "--kid",
        kid,
        # One argument with "=": a base64url key may start with "-".
        f"--public-key={public_key}",
        "--operator",
        operator,
    )


def test_an_operator_adds_a_key_and_the_list_never_prints_it():
    private, public = new_key()
    with capture_logs() as records:
        out = add("ee-k1", public)
    row = AgentccConsoleRunSignerKey.objects.get(kid="ee-k1")
    assert (row.deployment, row.created_by, row.revoked_at) == (
        "ee-prod-1",
        "ops@example.test",
        None,
    )
    assert "ee-k1\tee-prod-1\tactive\tops@example.test" in out
    assert public not in out
    assert any(
        r.get("event") == "console_run_signer_key_added"
        and r.get("kid") == "ee-k1"
        and r.get("deployment") == "ee-prod-1"
        and r.get("operator") == "ops@example.test"
        for r in records
    )
    assert public not in repr(records)
    key, deployment = active_key("ee-k1")
    assert deployment == "ee-prod-1"
    key.verify(private.sign(b"signed bytes"), b"signed bytes")


def test_at_most_two_active_keys_per_deployment():
    add("ee-k1", new_key()[1])
    add("ee-k2", new_key()[1])
    with pytest.raises(SystemExit) as refused:
        add("ee-k3", new_key()[1])
    assert refused.value.code == 1
    assert not AgentccConsoleRunSignerKey.objects.filter(kid="ee-k3").exists()
    # Another deployment has its own two.
    add("other-k1", new_key()[1], deployment="ee-other")
    run("--revoke", "--kid", "ee-k1", "--operator", "ops@example.test")
    add("ee-k3", new_key()[1])


def test_revoking_a_key_refuses_it_at_once():
    add("ee-k1", new_key()[1])
    with capture_logs() as records:
        out = run("--revoke", "--kid", "ee-k1", "--operator", "ops@example.test")
    assert "ee-k1\tee-prod-1\trevoked" in out
    assert active_key("ee-k1") is None
    assert any(r.get("event") == "console_run_signer_key_revoked" for r in records)
    with pytest.raises(SystemExit):
        run("--revoke", "--kid", "ee-k1", "--operator", "ops@example.test")


@pytest.mark.parametrize(
    ("kid", "public_key", "operator"),
    [
        ("ee-k1", "too-short", "ops"),
        ("ee-k1", "A" * 43 + "=", "ops"),
        ("has space", None, "ops"),
        ("ee-k1", None, ""),
    ],
)
def test_refused_requests_change_nothing(kid, public_key, operator):
    with pytest.raises(SystemExit):
        add(kid, public_key or new_key()[1], operator=operator)
    assert not AgentccConsoleRunSignerKey.objects.exists()


def test_a_key_id_is_registered_once():
    add("ee-k1", new_key()[1])
    with pytest.raises(SystemExit):
        add("ee-k1", new_key()[1], deployment="ee-other")


def test_the_registered_deployment_is_named_only_when_there_is_one():
    assert registered_deployment() is None
    add("ee-k1", new_key()[1])
    add("ee-k2", new_key()[1])
    assert registered_deployment() == "ee-prod-1"
    add("other-k1", new_key()[1], deployment="ee-other")
    assert registered_deployment() is None


def test_no_admin_form_writes_the_registry():
    assert AgentccConsoleRunSignerKey not in admin.site._registry
    assert active_key("not a kid") is None
