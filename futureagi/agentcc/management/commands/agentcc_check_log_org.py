import uuid
from collections import Counter, defaultdict
from datetime import UTC, datetime, time

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Count, Max, Min, Q
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime

from agentcc.models import AgentccAPIKey, AgentccRequestLog, AgentccSession
from agentcc.models.webhook import AgentccWebhookEvent
from agentcc.services.log_ingestion import KEY_ORG_FIELDS, resolve_key_org_ids

CHUNK = 1000


class Command(BaseCommand):
    help = (
        "Verify that every request-log row is stored under its API key's org. "
        "Report only: nothing is changed. On a large table, run it on a read "
        "replica with --since."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--org",
            help="Only rows stored under this org ID, or whose key belongs to it.",
        )
        parser.add_argument(
            "--since",
            help="Only rows created at or after this ISO date or datetime.",
        )
        parser.add_argument(
            "--samples",
            type=int,
            default=5,
            help="How many request IDs to list per group (default 5).",
        )

    def handle(self, *args, **options):
        only_org = _parse_org(options["org"]) if options["org"] else None
        rows = AgentccRequestLog._base_manager.all()
        if options["since"]:
            rows = rows.filter(created_at__gte=_parse_since(options["since"]))

        key_org_ids = {
            key_id: str(org_id) if org_id else None
            for key_id, org_id in resolve_key_org_ids(
                AgentccAPIKey._base_manager.values_list(*KEY_ORG_FIELDS)
            ).items()
        }
        misplaced = defaultdict(
            lambda: {"rows": 0, "cache_hits": 0, "key_ids": set(), "times": []}
        )
        unplaced = Counter()
        for group in (
            rows.values("organization_id", "api_key_id")
            .annotate(
                rows=Count("id"),
                cache_hits=Count("id", filter=Q(cache_hit=True)),
                first=Min("created_at"),
                last=Max("created_at"),
            )
            .order_by()
        ):
            stored_org, key_id = str(group["organization_id"]), group["api_key_id"]
            key_org = key_org_ids.get(key_id) if key_id else None
            if key_org is None:
                if only_org in (None, stored_org):
                    unplaced[_unplaced_reason(key_id, key_org_ids)] += group["rows"]
                continue
            if key_org == stored_org or only_org not in (None, stored_org, key_org):
                continue
            pair = misplaced[(stored_org, key_org)]
            pair["rows"] += group["rows"]
            pair["cache_hits"] += group["cache_hits"]
            pair["key_ids"].add(key_id)
            pair["times"] += [group["first"], group["last"]]

        self.stdout.write("Request-log rows by stored org and API key org:")
        for (stored_org, key_org), pair in sorted(misplaced.items()):
            self.stdout.write(
                self._group_line(rows, stored_org, key_org, pair, options["samples"])
            )
        self.stdout.write(
            f"Rows to review: {sum(p['rows'] for p in misplaced.values())}"
        )
        reasons = ", ".join(f"{k}={v}" for k, v in sorted(unplaced.items()))
        self.stdout.write(
            f"Rows with no API key Django can place: {sum(unplaced.values())}"
            + (f" ({reasons})" if reasons else "")
        )
        self.stdout.write(
            "webhook_events and sessions count matches by request and session "
            "ID, which are unique only within an org."
        )
        self.stdout.write("Report only. Nothing was changed.")

    def _group_line(self, rows, stored_org, key_org, pair, samples):
        group_rows = rows.filter(
            organization_id=stored_org, api_key_id__in=pair["key_ids"]
        )
        request_ids = [
            rid for rid in group_rows.values_list("request_id", flat=True) if rid
        ]
        session_ids = set(
            group_rows.exclude(session_id="").values_list("session_id", flat=True)
        )
        webhook_events = sum(
            AgentccWebhookEvent._base_manager.filter(
                organization_id=stored_org, payload__request_id__in=chunk
            ).count()
            for chunk in _chunks(request_ids)
        )
        sessions = sum(
            AgentccSession._base_manager.filter(
                organization_id=stored_org, session_id__in=chunk
            ).count()
            for chunk in _chunks(sorted(session_ids))
        )
        sample = ", ".join(
            group_rows.order_by("created_at").values_list("request_id", flat=True)[
                :samples
            ]
        )
        return (
            f"  stored_org={stored_org} key_org={key_org} rows={pair['rows']} "
            f"cache_hits={pair['cache_hits']} webhook_events={webhook_events} "
            f"sessions={sessions} first={min(pair['times']).isoformat()} "
            f"last={max(pair['times']).isoformat()} request_ids={sample}"
        )


def _unplaced_reason(key_id, key_org_ids):
    if not key_id:
        return "no_key"
    return "ambiguous_key" if key_id in key_org_ids else "unknown_key"


def _chunks(values):
    values = list(values)
    for start in range(0, len(values), CHUNK):
        yield values[start : start + CHUNK]


def _parse_org(value):
    try:
        return str(uuid.UUID(value))
    except ValueError:
        raise CommandError(f"--org is not an org ID: {value}") from None


def _parse_since(value):
    parsed = parse_datetime(value)
    if parsed is None:
        day = parse_date(value)
        if day is None:
            raise CommandError(f"--since is not an ISO date or datetime: {value}")
        parsed = datetime.combine(day, time.min)
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, UTC)
    return parsed
