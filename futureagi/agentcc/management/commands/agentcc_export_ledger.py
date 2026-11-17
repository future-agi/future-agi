"""Export an org's managed credit ledger with a hash manifest, for audit.

    python manage.py agentcc_export_ledger <org_id> --from 2026-10-01 \\
        --to 2026-11-01 --format csv --out ./ledger-2026-10

Writes ``entries.jsonl`` or ``entries.csv`` (every column of every entry with
``from <= occurred_at < to``, oldest first) and ``manifest.json``:
``{files: {name: sha256}, entry_count, sum_by_kind, org_id, from, to}``.
``--from`` and ``--to`` take an ISO date or datetime, read as UTC without an
offset; either may be left out for an open range.
"""

import csv
import hashlib
import json
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID

from django.core.management.base import BaseCommand, CommandError
from django.utils.dateparse import parse_date, parse_datetime

from agentcc.management.commands.agentcc_managed_credits import (
    load_ledger,
    load_org,
)

MANIFEST = "manifest.json"
# A spreadsheet runs a cell starting with one of these as a formula.
FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _bound(raw, flag):
    if raw is None:
        return None
    try:
        # Each returns None for a malformed string and raises for an impossible one.
        moment = parse_datetime(raw)
        day = None if moment else parse_date(raw)
    except ValueError:
        moment = day = None
    if moment is None and day is None:
        raise CommandError(f"{flag} takes an ISO date or datetime, got {raw!r}")
    if moment is None:
        moment = datetime.combine(day, datetime.min.time())
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def _plain(value):
    """A JSON-ready value: ids and times as strings, everything else as stored."""
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def _csv_cell(value):
    value = _plain(value)
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, separators=(",", ":"), sort_keys=True)
    if isinstance(value, str) and value.startswith(FORMULA_PREFIXES):
        return "'" + value
    return value


def _sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


class Command(BaseCommand):
    help = "Export an org's managed credit ledger entries plus a sha256 manifest."

    def add_arguments(self, parser):
        parser.add_argument("org_id")
        parser.add_argument("--from", dest="start", help="inclusive ISO date/datetime")
        parser.add_argument("--to", dest="end", help="exclusive ISO date/datetime")
        parser.add_argument("--format", choices=["jsonl", "csv"], default="jsonl")
        parser.add_argument("--out", required=True, help="directory to write into")

    def handle(self, *args, **options):
        ledger = load_ledger()
        org = load_org(options["org_id"])
        start = _bound(options["start"], "--from")
        end = _bound(options["end"], "--to")
        if start and end and start >= end:
            raise CommandError("--from must be before --to")

        out = Path(options["out"])
        name = f"entries.{options['format']}"
        for existing in (out / MANIFEST, out / name):
            if existing.exists():
                raise CommandError(f"{existing} already exists; choose another --out")
        out.mkdir(parents=True, exist_ok=True)

        entries = ledger.ledger_entries(org, start=start, end=end)
        columns = [field.attname for field in entries.model._meta.concrete_fields]
        sum_by_kind = dict.fromkeys(entries.model.Kind.values, 0)
        count = 0
        with (out / name).open("w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f) if options["format"] == "csv" else None
            if writer:
                writer.writerow(columns)
            for entry in entries.iterator(chunk_size=2000):
                values = [getattr(entry, column) for column in columns]
                if writer:
                    writer.writerow([_csv_cell(value) for value in values])
                else:
                    row = {c: _plain(v) for c, v in zip(columns, values, strict=True)}
                    f.write(json.dumps(row, sort_keys=True, separators=(",", ":")))
                    f.write("\n")
                # Summed from the rows written, so the manifest attests the file.
                sum_by_kind[entry.kind] += entry.amount_micros
                count += 1

        manifest = {
            "files": {name: _sha256(out / name)},
            "entry_count": count,
            "sum_by_kind": sum_by_kind,
            "org_id": str(org.id),
            "from": _plain(start),
            "to": _plain(end),
        }
        (out / MANIFEST).write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n"
        )
        self.stdout.write(f"wrote {count} entries to {out / name} and {out / MANIFEST}")
