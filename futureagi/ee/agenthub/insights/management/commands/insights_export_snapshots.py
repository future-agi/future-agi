"""Export Insights evidence snapshots for a bounded, explicit list of units.

Read-only: each unit is one bounded ClickHouse read plan
(``ee.agenthub.insights.evidence``) plus a Postgres lookup of its project's
organization; neither database is written. The output is a private corpus in
the layout the Omega evaluation harness reads:

    <out>/corpus.json
    <out>/units/<safe key>.snapshot.json
    <out>/units/<safe key>.media/<media alias>.<ext>     (with --media)

Directories are 0700 and files 0600, and the command refuses a directory
inside a git worktree. It prints counts, typed failure codes and digests,
never trace content or raw unit ids (``<safe key>`` is a hash).

Units are ``KIND:PROJECT_ID:KEY`` (``--unit``, repeatable) or JSON lines of
``{"kind", "project_id", "key"}`` (``--units-file``); kinds are trace, session
and user. ``--media`` fetches media bytes under the evidence media policy:
data URIs decode in process, own-storage objects are read with the storage
client, and external URLs are fetched (through the SSRF guard) only with
``--fetch-external-media``.

``ee.agenthub.insights`` is not an installed app, so ``manage.py`` does not
discover this command; from ``manage.py shell``::

    from django.core.management import call_command
    from ee.agenthub.insights.management.commands.insights_export_snapshots \\
        import Command
    call_command(Command(), out="/private/corpus", unit=["trace:<project>:<id>"])
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandError

from ee.agenthub.insights.evidence import (
    EvidenceLimits,
    EvidenceNotFound,
    EvidenceUnavailable,
    MediaFetchPolicy,
    build_unit_snapshot,
    fetch_snapshot_media,
    normalize_unit_kind,
    resolve_lookup_ids,
    safe_unit_key,
)

CORPUS_SCHEMA = "insights-corpus/v1"
MAX_UNITS = 500
MIB = 1024 * 1024


@dataclass(frozen=True)
class UnitRequest:
    kind: str
    project_id: str
    key: str


class UnitFailed(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _uuid(value: Any) -> str | None:
    try:
        return str(uuid.UUID(str(value)))
    except (TypeError, ValueError, AttributeError):
        return None


def make_unit(kind: Any, project_id: Any, key: Any) -> UnitRequest:
    try:
        kind = normalize_unit_kind(str(kind))
    except EvidenceUnavailable:
        raise CommandError("unit kind must be trace, session or user") from None
    project = _uuid(project_id)
    if project is None:
        raise CommandError("project id must be a UUID")
    key = str(key or "").strip()
    if not key:
        raise CommandError("unit key is empty")
    return UnitRequest(kind, project, key)


def parse_unit(text: str) -> UnitRequest:
    kind, _, rest = text.partition(":")
    project_id, _, key = rest.partition(":")
    if not (kind and project_id and key):
        raise CommandError("--unit must be KIND:PROJECT_ID:KEY")
    return make_unit(kind, project_id, key)


def read_units_file(path: str) -> list[UnitRequest]:
    units = []
    with open(path, encoding="utf-8") as handle:
        for number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
                units.append(make_unit(item["kind"], item["project_id"], item["key"]))
            except (ValueError, KeyError, TypeError, CommandError):
                raise CommandError(
                    f"units file line {number} is not a valid unit"
                ) from None
    return units


def git_worktree_of(path: Path) -> Path | None:
    for candidate in (path, *path.parents):
        if (candidate / ".git").exists():
            return candidate
    return None


def _private_dir(path: Path) -> None:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path, 0o700)


def prepare_corpus_dir(out: str) -> Path:
    root = Path(out).expanduser().resolve()
    if git_worktree_of(root) is not None:
        raise CommandError("refusing to write a private corpus inside a git worktree")
    if root.exists():
        if not root.is_dir():
            raise CommandError("output path is not a directory")
        if any(root.iterdir()) and not (root / "corpus.json").is_file():
            raise CommandError("output directory is not empty and holds no corpus.json")
    _private_dir(root)
    _private_dir(root / "units")
    return root


def write_private(path: Path, data: bytes) -> None:
    """Write ``data`` atomically, readable by the owner only."""
    temporary = path.with_name(f".{path.name}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def load_corpus(root: Path) -> dict[str, Any]:
    path = root / "corpus.json"
    if not path.is_file():
        return {"schema": CORPUS_SCHEMA, "units": [], "failures": []}
    corpus = json.loads(path.read_text(encoding="utf-8"))
    if corpus.get("schema") != CORPUS_SCHEMA:
        raise CommandError("existing corpus.json has a different schema")
    corpus.setdefault("units", [])
    corpus.setdefault("failures", [])
    return corpus


def record(
    corpus: dict[str, Any],
    unit: UnitRequest,
    *,
    entry: dict[str, Any] | None = None,
    code: str | None = None,
) -> None:
    """Replace whatever the corpus held for ``unit`` with its new outcome;
    failed cases are kept so they can be retried and counted."""

    def other(item: dict[str, Any]) -> bool:
        return (item.get("kind"), item.get("project_id"), item.get("key")) != (
            unit.kind,
            unit.project_id,
            unit.key,
        )

    corpus["units"] = [item for item in corpus["units"] if other(item)]
    corpus["failures"] = [item for item in corpus["failures"] if other(item)]
    identity = {"kind": unit.kind, "project_id": unit.project_id, "key": unit.key}
    if entry is not None:
        corpus["units"].append({**identity, **entry})
    if code is not None:
        corpus["failures"].append({**identity, "code": code})


# Seams for tests; the real command reads ClickHouse and Postgres.
def analytics_client():
    from tracer.services.clickhouse.v2.query_service import V2AnalyticsQueryService

    return V2AnalyticsQueryService()


def project_organizations(project_ids: set[str]) -> dict[str, str]:
    from tracer.models.project import Project

    rows = Project.objects.filter(id__in=sorted(project_ids)).values_list(
        "id", "organization_id"
    )
    return {str(pid): str(org) for pid, org in rows if org}


def lookup_ids(kind: str, key: str) -> list[str]:
    return resolve_lookup_ids(kind, key)


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        raise CommandError("--since/--until must be ISO 8601 times") from None
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


class Command(BaseCommand):
    help = (
        "Export Insights evidence snapshots for an explicit, bounded list of "
        "units to a private corpus directory (read-only)."
    )

    def add_arguments(self, parser):
        parser.add_argument("--out", required=True, help="Private corpus directory.")
        parser.add_argument(
            "--unit",
            action="append",
            default=[],
            metavar="KIND:PROJECT_ID:KEY",
            help="A unit to export (repeatable).",
        )
        parser.add_argument("--units-file", help="JSON lines of kind/project_id/key.")
        parser.add_argument(
            "--organization", help="Refuse units whose project is in another org."
        )
        parser.add_argument("--since", help="Earliest trace start, for user units.")
        parser.add_argument("--until", help="Latest trace start, for user units.")
        parser.add_argument("--media", action="store_true", help="Fetch media bytes.")
        parser.add_argument(
            "--fetch-external-media",
            action="store_true",
            help="With --media, also fetch external URLs through the SSRF guard.",
        )
        parser.add_argument("--max-units", type=int, default=50)
        parser.add_argument("--max-spans", type=int, default=EvidenceLimits.max_spans)
        parser.add_argument("--max-media-mib", type=int, default=25)
        parser.add_argument("--max-unit-media-mib", type=int, default=100)
        parser.add_argument("--notes", default="", help="Stored in corpus.json.")

    def handle(self, *args, **options):
        units = [parse_unit(text) for text in options["unit"]]
        if options.get("units_file"):
            units += read_units_file(options["units_file"])
        units = list(dict.fromkeys(units))
        if not units:
            raise CommandError("give at least one --unit or a --units-file")
        allowed = min(int(options["max_units"]), MAX_UNITS)
        if len(units) > allowed:
            raise CommandError(f"{len(units)} units exceeds the limit of {allowed}")
        organization = options.get("organization")
        if organization is not None and _uuid(organization) is None:
            raise CommandError("--organization must be a UUID")
        if options["fetch_external_media"] and not options["media"]:
            raise CommandError("--fetch-external-media needs --media")
        since = _parse_time(options.get("since"))
        until = _parse_time(options.get("until"))
        try:
            limits = EvidenceLimits(max_spans=int(options["max_spans"]))
        except ValueError:
            raise CommandError("--max-spans must be a positive integer") from None
        policy = MediaFetchPolicy(
            max_item_bytes=int(options["max_media_mib"]) * MIB,
            max_unit_bytes=int(options["max_unit_media_mib"]) * MIB,
            fetch_external=bool(options["fetch_external_media"]),
        )

        root = prepare_corpus_dir(options["out"])
        corpus = load_corpus(root)
        organizations = project_organizations({unit.project_id for unit in units})
        analytics = analytics_client()
        exported = 0
        for unit in units:
            safe_key = safe_unit_key(unit.project_id, unit.kind, unit.key)
            try:
                org = organizations.get(unit.project_id)
                if org is None:
                    raise UnitFailed("project_not_found")
                if organization is not None and org != _uuid(organization):
                    raise UnitFailed("organization_mismatch")
                entry = self._export(
                    root,
                    unit,
                    safe_key,
                    org,
                    analytics,
                    limits=limits,
                    policy=policy if options["media"] else None,
                    since=since,
                    until=until,
                )
            except (UnitFailed, EvidenceUnavailable) as failure:
                code = failure.code
            except EvidenceNotFound:
                code = "not_found"
            except Exception as exc:  # report the class only: messages may quote data
                code = f"error:{type(exc).__name__}"
            else:
                record(corpus, unit, entry=entry)
                exported += 1
                self.stdout.write(
                    f"ok   {unit.kind:<7} {safe_key}  spans {entry['spans_included']}"
                    f"/{entry['spans_total']}  chars {entry['chars_total']}  media "
                    f"{entry['media_total']} (files {entry['media_files']})  "
                    f"digest {entry['digest'][:16]}"
                )
                continue
            record(corpus, unit, code=code)
            self.stdout.write(f"fail {unit.kind:<7} {safe_key}  {code}")

        corpus["built_at"] = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        if options["notes"]:
            corpus["notes"] = options["notes"]
        corpus["units"].sort(key=lambda item: item["file"])
        corpus["failures"].sort(key=lambda item: (item["kind"], item["key"]))
        write_private(
            root / "corpus.json",
            json.dumps(corpus, indent=2, sort_keys=True, ensure_ascii=False).encode(),
        )
        self.stdout.write(f"exported {exported} of {len(units)} units to {root}")

    def _export(
        self,
        root: Path,
        unit: UnitRequest,
        safe_key: str,
        organization: str,
        analytics,
        *,
        limits: EvidenceLimits,
        policy: MediaFetchPolicy | None,
        since: datetime | None,
        until: datetime | None,
    ) -> dict[str, Any]:
        try:
            ids = lookup_ids(unit.kind, unit.key)
        except Exception:
            raise UnitFailed("alias_resolution_failed") from None
        snapshot = build_unit_snapshot(
            analytics=analytics,
            project_id=unit.project_id,
            organization_id=organization,
            kind=unit.kind,
            key=unit.key,
            lookup_ids=ids,
            since=since,
            until=until,
            limits=limits,
        )
        fetched = []
        if policy is not None:
            snapshot, fetched = fetch_snapshot_media(snapshot, policy)
        media_dir = root / "units" / f"{safe_key}.media"
        if media_dir.is_dir():
            for stale in media_dir.iterdir():
                if stale.is_file():
                    stale.unlink()
        if fetched:
            _private_dir(media_dir)
            for item in fetched:
                write_private(
                    media_dir / f"{item.alias}.{item.extension}", item.content
                )
        file_name = f"units/{safe_key}.snapshot.json"
        write_private(root / file_name, snapshot.to_bytes())
        manifest = snapshot.manifest
        return {
            "file": file_name,
            "digest": snapshot.digest,
            "chars_total": sum(
                item["chars"]
                for span in manifest["spans"]
                for item in span["fields"].values()
            ),
            "spans_total": manifest["limits"]["spans_total"],
            "spans_included": manifest["limits"]["spans_included"],
            "media_total": len(manifest["media"]),
            "media_files": len(fetched),
            "truncated": manifest["limits"]["truncated"],
        }
