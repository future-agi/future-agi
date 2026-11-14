"""Owned webhook-event prototype for the append-only reference workload.

This is an ORM fixture, not a delivery/API witness. Use only source-era fields
(the model before 0030/0031); an inactive endpoint and a terminal delivered
record cannot enqueue outbound delivery. No HTTP request is made. The source
witness's organization must already exist; never borrow an arbitrary row/org.
Both fixture identities are deterministic and collisions are verified, not
updated, including soft-deleted collisions. Existing product rows are untouched.
"""

from __future__ import annotations

import uuid

WEBHOOK_EVENT_REFERENCE_MODELS = {
    ("prism", "AgentccWebhookEvent"): "agentcc_webhook_event"
}

WEBHOOK_EVENT_REFERENCE_PREPARE = """
import uuid
from django.apps import apps
from django.db import transaction
Organization=apps.get_model('accounts','Organization')
Webhook=apps.get_model('prism','AgentccWebhook')
Event=apps.get_model('prism','AgentccWebhookEvent')
for model,table in ((Webhook,'agentcc_webhook'),(Event,'agentcc_webhook_event')):
    assert model._meta.managed and model._meta.db_table==table, 'webhook_event_reference: table drift'
    assert model._meta.pk.get_internal_type()=='UUIDField', 'webhook_event_reference: pk drift'
for model,name,target in ((Webhook,'organization',Organization),(Event,'organization',Organization),
                          (Event,'webhook',Webhook)):
    field=model._meta.get_field(name)
    assert field.many_to_one and field.db_constraint and field.remote_field.model==target, 'webhook_event_reference: FK drift'
org_id=uuid.UUID(a['organization_id'])
webhook_id=uuid.UUID(a['webhook_id'])
event_id=uuid.UUID(a['event_id'])
# Reads include soft-deleted rows so a collision cannot be hidden/replaced.
def owned(model,identity,values):
    row=model.all_objects.filter(pk=identity).first()
    if row is None:
        row=model.objects.create(id=identity,**values)
    for key,value in values.items():
        assert getattr(row,key)==value, 'webhook_event_reference: owned fixture differs'
    return row
with transaction.atomic():
    Organization.objects.get(pk=org_id)
    owned(Webhook,webhook_id,dict(organization_id=org_id,
        name='qualification-webhook-'+str(webhook_id),
        url='https://example.invalid/qualification-webhook', secret='',
        events=['request.completed'], is_active=False, headers={},
        description='Owned synthetic update reference; no delivery attempted.',
        deleted=False, deleted_at=None))
    owned(Event,event_id,dict(organization_id=org_id,webhook_id=webhook_id,
        event_type='request.completed',
        payload={'qualification_fixture':True,'namespace':a['namespace']},
        status='delivered', attempts=0, max_attempts=5,
        last_attempt_at=None, last_response_code=None, last_error='', next_retry_at=None,
        deleted=False, deleted_at=None))
print(json.dumps(dict(event_id=str(event_id),webhook_id=str(webhook_id),organization_id=str(org_id))))
"""


def seed_webhook_event_reference(driver, entry, rows, *, prefix, expand, producer):
    """Prepare one owned prototype, then retain the generic cloner's safeguards."""

    def require(ok):
        if not ok:
            raise producer.ManualRelease("high_volume_reference_unavailable")

    require(type(rows) is int and 1 <= rows <= 100000000)
    namespaces = getattr(getattr(driver, "witnesses", None), "namespaces", None)
    require(isinstance(namespaces, dict))
    source = namespaces.get("source")
    require(isinstance(source, dict))
    namespace, headers = source.get("namespace"), source.get("headers")
    require(isinstance(namespace, str) and bool(namespace))
    require(isinstance(headers, dict))
    org_id = headers.get("X-Organization-Id")
    require(isinstance(org_id, str))
    try:
        org_id = str(uuid.UUID(org_id))
    except ValueError:
        require(False)
    identity = uuid.uuid5(
        uuid.NAMESPACE_URL,
        "futureagi-qualification-webhook-event:" + org_id + ":" + namespace,
    )
    sample = {
        "event_id": str(uuid.uuid5(identity, "event")),
        "webhook_id": str(uuid.uuid5(identity, "webhook")),
        "organization_id": org_id,
    }
    # No bearer tokens/API keys or arbitrary witness headers enter the fixture.
    prepared = driver.app_python(
        prefix + WEBHOOK_EVENT_REFERENCE_PREPARE, dict(sample, namespace=namespace)
    )
    require(prepared == sample)
    result = driver.app_python(
        expand, dict(entry, rows=rows, sample_webhook_event=sample)
    )
    require(
        isinstance(result, dict)
        and type(result.get("seeded_rows")) is int
        and result["seeded_rows"] >= rows
    )
    corpus = getattr(driver, "corpus", None)
    if corpus is not None:
        corpus.setdefault("reference_seed_operations", []).append(
            {
                "table": entry["table"],
                "operation": "webhook_event_reference_fixture",
                "version": 1,
                "requested_rows": rows,
                "seeded_rows": result["seeded_rows"],
            }
        )
