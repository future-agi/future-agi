"""Third review round of key-scoped guardrail policies."""

import uuid

import pytest
from django.db import connection

from agentcc.models import AgentccAPIKey
from agentcc.models.guardrail_policy import AgentccGuardrailPolicy

URL = "/agentcc/guardrail-policies/"


def _key(org):
    return AgentccAPIKey.no_workspace_objects.create(
        organization=org, gateway_key_id=f"gateway-{uuid.uuid4()}", name="Synthetic"
    )


# Item 2: pods on the previous release insert policy rows without the new
# column during a rolling deploy (and after an image-only rollback), so the
# database itself must supply its default.
@pytest.mark.django_db
def test_runs_on_gateway_keeps_a_database_default():
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT is_nullable, column_default FROM information_schema.columns "
            "WHERE table_name = %s AND column_name = %s",
            ["agentcc_guardrail_policy", "runs_on_gateway"],
        )
        row = cursor.fetchone()
    assert row is not None
    is_nullable, column_default = row
    assert is_nullable == "NO"
    assert column_default == "false"


@pytest.mark.django_db
def test_an_insert_without_runs_on_gateway_succeeds(organization):
    """What an older pod's INSERT looks like: every column but the new one."""
    fields = [
        f
        for f in AgentccGuardrailPolicy._meta.concrete_fields
        if f.name != "runs_on_gateway"
    ]
    template = AgentccGuardrailPolicy(
        organization=organization,
        name="from-old-pod",
        scope="global",
        checks=[],
    )
    for field in fields:
        if getattr(field, "auto_now_add", False) or getattr(field, "auto_now", False):
            field.pre_save(template, add=True)
    columns = ", ".join(connection.ops.quote_name(f.column) for f in fields)
    values = [
        f.get_db_prep_save(getattr(template, f.attname), connection) for f in fields
    ]
    with connection.cursor() as cursor:
        cursor.execute(
            f"INSERT INTO agentcc_guardrail_policy ({columns}) "
            f"VALUES ({', '.join(['%s'] * len(fields))})",
            values,
        )
    row = AgentccGuardrailPolicy.no_workspace_objects.get(name="from-old-pod")
    assert row.runs_on_gateway is False


# Item 4: the Policies tab must be able to tell a dormant row from a live one.
@pytest.mark.django_db
def test_policy_api_exposes_runs_on_gateway_read_only(auth_client, organization):
    api_key = _key(organization)
    dormant = AgentccGuardrailPolicy.no_workspace_objects.create(
        organization=organization,
        name="saved-before-upgrade",
        scope="key",
        applied_keys=[str(api_key.id)],
        checks=[{"name": "secret-detection", "enabled": True, "action": "block"}],
    )
    listed = auth_client.get(URL)
    assert listed.status_code == 200, listed.json()
    body = listed.json()
    rows = body.get("results") or body.get("result") or body
    if isinstance(rows, dict):
        rows = rows.get("results", [])
    row = next(r for r in rows if r["id"] == str(dormant.id))
    assert row["runs_on_gateway"] is False

    # A client cannot switch it on directly; any save through the API does.
    response = auth_client.patch(
        f"{URL}{dormant.id}/",
        {"runs_on_gateway": True, "mode": "monitor"},
        format="json",
    )
    assert response.status_code == 200, response.json()
    assert response.json()["result"]["runs_on_gateway"] is True
    dormant.refresh_from_db()
    assert dormant.runs_on_gateway is True
    assert dormant.mode == "monitor"


@pytest.mark.django_db
def test_runs_on_gateway_in_a_create_body_is_ignored(auth_client, organization):
    api_key = _key(organization)
    created = auth_client.post(
        URL,
        {
            "name": "new-policy",
            "scope": "key",
            "mode": "monitor",
            "applied_keys": [str(api_key.id)],
            "runs_on_gateway": False,
            "checks": [
                {"name": "secret-detection", "enabled": True, "action": "block"}
            ],
        },
        format="json",
    )
    assert created.status_code == 200, created.json()
    assert created.json()["result"]["runs_on_gateway"] is True
