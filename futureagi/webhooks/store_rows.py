"""Row helpers shared by the Postgres store: time conversion, an endpoint's send configuration (decrypted at
claim time) and a delivery's earlier answers for the schedule."""

from __future__ import annotations

from datetime import UTC, datetime

from django.db.models import Q

from webhooks import crypto
from webhooks.core.ports import EndpointConfig
from webhooks.models import Attempt, Endpoint, Policy, Secret


def insert_row(obj):
    """INSERT ``obj`` as ``Model.objects.create`` would, without FA's global pre/post_save receivers
    (``tfc.utils.signals``): they read the ``organization`` foreign key on every save, one more SELECT per row the
    dispatcher writes (WT-30's profile). The webhooks rows set their organization themselves and have no workspace to
    fill (``Event`` and the configuration rows keep the ordinary path)."""
    type(obj).objects.bulk_create([obj])
    return obj


def write_row(obj) -> None:
    """UPDATE every column of ``obj`` by its primary key, as ``obj.save()`` would, without FA's global save receivers
    (see :func:`insert_row`)."""
    model = type(obj)
    model.objects.filter(pk=obj.pk).update(
        **{
            f.attname: getattr(obj, f.attname)
            for f in model._meta.concrete_fields
            if not f.primary_key
        }
    )


def dt_of(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, UTC)


def ms_of(dt: datetime | None) -> int | None:
    return None if dt is None else int(dt.timestamp() * 1000)


def endpoint_config(
    ep: Endpoint, now: datetime, *, decrypt: bool = True
) -> EndpointConfig:
    """The send configuration. ``decrypt=False`` (the reaper, which never sends) leaves the URL, the secrets and the
    headers empty. No local variable holds a decrypted value (K2): a decrypt that fails logs an error, and Sentry
    attaches the stack with each frame's variables to it, so a value decrypted before it would otherwise be sent."""
    if not decrypt:
        return EndpointConfig(
            id=ep.id,
            organization_id=str(ep.organization_id),
            url="",
            secrets_newest_first=(),
            timeout_ms=ep.timeout_ms,
            max_age_s=ep.max_age_s,
            status=ep.status,
        )
    secret_rows = list(
        Secret.objects.filter(endpoint_id=ep.id)
        .filter(Q(state="active") | Q(state="previous", expires_at__gt=now))
        .order_by("-created_at")
    )
    return EndpointConfig(
        id=ep.id,
        organization_id=str(ep.organization_id),
        url=crypto.decrypt(ep.url_encrypted) or "",
        secrets_newest_first=tuple(
            crypto.decrypt(s.secret_encrypted) for s in secret_rows
        ),
        custom_headers=tuple((crypto.decrypt_json(ep.headers_encrypted) or {}).items()),
        timeout_ms=ep.timeout_ms,
        max_age_s=ep.max_age_s,
        status=ep.status,
        allowed_hosts=allowed_hosts(ep),
    )


def allowed_hosts(ep: Endpoint) -> dict:
    out = {}
    org = Policy.objects.filter(
        organization_id=ep.organization_id, workspace__isnull=True
    ).first()
    if org and org.allowed_hosts:
        out["org_allowed_hosts"] = list(org.allowed_hosts)
    if ep.workspace_id:
        ws = Policy.objects.filter(
            organization_id=ep.organization_id, workspace_id=ep.workspace_id
        ).first()
        if ws and ws.allowed_hosts:
            out["workspace_allowed_hosts"] = list(ws.allowed_hosts)
    return out


def history(delivery_id: str) -> tuple[dict, ...]:
    out = []
    for a in (
        Attempt.objects.filter(delivery_id=delivery_id)
        .exclude(outcome="superseded")
        .order_by("n", "id")
    ):
        settled = ms_of(a.settled_at) or ms_of(a.started_at)
        if a.response_status is not None:
            ans = {"status": a.response_status, "settled_at_ms": settled}
            ra = (a.response_headers or {}).get("retry-after")
            if ra is not None:
                ans["retry_after"] = ra
        else:
            ans = {"error": a.outcome, "settled_at_ms": settled}
        out.append(ans)
    return tuple(out)


def iso_ms(dt: datetime) -> str:
    dt = dt.astimezone(UTC)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"
