"""Bridge a project's PG ObservationSpans into the ClickHouse `spans` table.

Local dev has no PeerDB CDC, so ingested spans live only in Postgres while the
cluster RCA agent (and the rest of the feed read path) read ClickHouse. This
command replicates a project's spans into CH directly: it shreds the
span_attributes JSON into the typed Map columns and populates the denormalized
trace_* context from the PG Trace row (so trace_name / session land without
the dictGet enrichment the dev MV can't do).

    CH_ENABLED=true python manage.py sync_spans_to_ch --project-name rca-live-demo
"""

from __future__ import annotations

import json
import uuid

from django.core.management.base import BaseCommand, CommandError

from tracer.models.observation_span import ObservationSpan
from tracer.models.project import Project
from tracer.services.clickhouse.client import ClickHouseClient

_BATCH = 500


class Command(BaseCommand):
    help = "Replicate a project's PG spans into the ClickHouse spans table (dev bridge)."

    def add_arguments(self, parser):
        parser.add_argument("--project-name", default=None)
        parser.add_argument("--project-id", default=None)

    def handle(self, *args, **opts):
        project = self._resolve_project(opts)
        spans = list(
            ObservationSpan.objects.filter(project=project, deleted=False)
            .select_related("trace")
        )
        if not spans:
            self.stdout.write(self.style.WARNING("No PG spans for project."))
            return

        rows = [self._row(s, project.id) for s in spans]
        client = ClickHouseClient()
        total = 0
        for i in range(0, len(rows), _BATCH):
            chunk = rows[i:i + _BATCH]
            total += client.insert("spans", chunk, columns=list(chunk[0].keys()))
        self.stdout.write(self.style.SUCCESS(
            f"Synced {total} spans -> CH for project {project.name} ({project.id})"
        ))

    def _row(self, s, project_id) -> dict:
        attr_str, attr_num, attr_bool = {}, {}, {}
        for k, v in (s.span_attributes or {}).items():
            if isinstance(v, bool):
                attr_bool[k] = 1 if v else 0
            elif isinstance(v, (int, float)):
                attr_num[k] = float(v)
            elif isinstance(v, str):
                attr_str[k] = v
            else:
                attr_str[k] = json.dumps(v, default=str)

        tr = s.trace
        status = "ERROR" if (s.status or "").upper() == "ERROR" else "OK"
        return {
            "id": str(s.id),
            "trace_id": str(s.trace_id),
            "project_id": uuid.UUID(str(project_id)),
            "parent_span_id": s.parent_span_id,
            "name": s.name or "",
            "observation_type": (s.observation_type or "unknown"),
            "operation_name": s.operation_name,
            "status": status,
            "status_message": s.status_message,
            "start_time": _naive(s.start_time),
            "end_time": _naive(s.end_time),
            "latency_ms": int(s.latency_ms) if s.latency_ms is not None else None,
            "model": s.model,
            "provider": s.provider,
            "prompt_tokens": s.prompt_tokens,
            "completion_tokens": s.completion_tokens,
            "total_tokens": s.total_tokens,
            "cost": s.cost,
            "input": _as_text(s.input),
            "output": _as_text(s.output),
            "span_attr_str": attr_str,
            "span_attr_num": attr_num,
            "span_attr_bool": attr_bool,
            "span_attributes_raw": json.dumps(s.span_attributes or {}, default=str),
            "metadata_map": {},
            "tags": _as_text(s.tags) if s.tags else "",
            "span_events": _as_text(s.span_events) if s.span_events else "[]",
            "schema_version": "1.0",
            "trace_name": (tr.name if tr else None) or s.name,
            "trace_session_id": (tr.session_id if tr and tr.session_id else None),
            "trace_external_id": (tr.external_id if tr else None),
            "trace_tags": _as_text(tr.tags) if (tr and tr.tags) else "",
            "created_at": _naive(s.created_at),
            "updated_at": _naive(s.updated_at),
            "_peerdb_is_deleted": 0,
            "_peerdb_version": 1,
        }

    def _resolve_project(self, opts):
        if opts["project_id"]:
            p = Project.objects.filter(id=opts["project_id"]).first()
        elif opts["project_name"]:
            p = Project.objects.filter(
                name=opts["project_name"]
            ).order_by("-created_at").first()
        else:
            raise CommandError("Pass --project-name or --project-id.")
        if not p:
            raise CommandError("Project not found.")
        return p


def _naive(dt):
    return dt.replace(tzinfo=None) if dt is not None else None


def _as_text(v):
    if v is None:
        return ""
    return v if isinstance(v, str) else json.dumps(v, default=str)
