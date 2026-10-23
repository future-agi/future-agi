"""Verify that request-log headers keep every credential value masked, and with
--apply mask any that are not.

The request log keeps every credential header masked
(``agentcc/services/credential_headers.py``). This command reads every stored
row, soft-deleted rows included, reports by organization, key id and header
name only, never a value, and with --apply masks the same way ingestion does.
Dry run by default.
"""

import hashlib
import uuid
from collections import defaultdict

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Q

from agentcc.models import AgentccAPIKey, AgentccRequestLog
from agentcc.services.credential_headers import (
    MASK,
    is_credential_header,
    mask_credential_headers,
)

# The prefix of every key the gateway mints (internal/auth/keystore.go).
GATEWAY_KEY_PREFIX = "sk-agentcc-"
# The headers the gateway reads its key from: a value there is a gateway key
# even when no stored hash matches it and it has no gateway prefix (a key in
# a self-hosted gateway's own config file).
KEY_HEADERS = frozenset({"authorization", "x-api-key", "x-goog-api-key"})
_HEADER_FIELDS = ("request_headers", "response_headers")


class Command(BaseCommand):
    help = (
        "Verify that request-log headers keep every credential value masked, "
        "reporting by organization, key id and header name, never by value. "
        "Dry run by default; --apply masks them the same way ingestion does."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Mask the credential headers of the rows found.",
        )
        parser.add_argument("--org", help="Only scan this organization's rows.")
        parser.add_argument(
            "--batch-size",
            type=int,
            default=1000,
            help="Rows read (and, with --apply, written) per query.",
        )

    def handle(self, *args, **options):
        batch_size = options["batch_size"]
        if batch_size < 1:
            raise CommandError("--batch-size must be at least 1")
        # The base manager: soft-deleted rows keep their headers too.
        rows = AgentccRequestLog._base_manager.filter(
            Q(request_headers__isnull=False) | Q(response_headers__isnull=False)
        )
        if options["org"]:
            try:
                rows = rows.filter(organization_id=uuid.UUID(options["org"]))
            except ValueError as exc:
                raise CommandError("--org must be an organization ID") from exc

        keys = _KeyIndex()
        report = _Report()
        scanned = 0
        last_pk = None
        # Keyset pages, not a server-side cursor, so the command also runs
        # behind a connection pooler with server-side cursors off.
        while True:
            page = rows if last_pk is None else rows.filter(pk__gt=last_pk)
            batch = list(
                page.order_by("pk").values_list(
                    "pk", "organization_id", "api_key_id", *_HEADER_FIELDS
                )[:batch_size]
            )
            if not batch:
                break
            last_pk = batch[-1][0]
            scanned += len(batch)
            found = []
            for pk, org_id, api_key_id, request_headers, response_headers in batch:
                held = _held(request_headers) + _held(response_headers)
                if held:
                    found.append((pk, org_id, api_key_id, held))
                    if options["apply"]:
                        report.masked.append(
                            AgentccRequestLog(
                                pk=pk,
                                request_headers=mask_credential_headers(
                                    request_headers
                                ),
                                response_headers=mask_credential_headers(
                                    response_headers
                                ),
                            )
                        )
            keys.load(value for *_, held in found for _, value in held)
            for pk, org_id, api_key_id, held in found:
                report.add(pk, org_id, api_key_id, held, keys)
            if report.masked:
                with transaction.atomic():
                    AgentccRequestLog._base_manager.bulk_update(
                        report.masked, list(_HEADER_FIELDS)
                    )
                report.masked_rows += len(report.masked)
                report.masked = []
            if options["verbosity"] > 1:
                self.stderr.write(f"Scanned {scanned} row(s).")

        report.write(self.stdout, scanned=scanned, applied=options["apply"])


def _held(headers):
    """(name, value) for each value a credential header holds in ``headers``.

    A header ingestion already masked holds nothing. Headers stored as
    something other than an object cannot be checked name by name, so the
    whole field counts as held (``mask_credential_headers`` drops it).
    """
    if headers is None:
        return []
    if not isinstance(headers, dict):
        return [("(headers not stored as an object)", None)]
    held = []
    for name, value in headers.items():
        if not is_credential_header(name):
            continue
        for item in value if isinstance(value, list) else [value]:
            if item not in (None, "", MASK):
                held.append((name, item))
    return held


def _candidates(value):
    """The strings a stored header value could be a gateway key as."""
    if not isinstance(value, str):
        return []
    value = value.strip()
    if value[:7].lower() == "bearer ":
        value = value[7:].strip()
    return [value] if value else []


def _sha256(value):
    return hashlib.sha256(value.encode()).hexdigest()


class _KeyIndex:
    """The gateway keys whose hash matches a stored value, across every org."""

    def __init__(self):
        self._by_hash = {}

    def load(self, values):
        wanted = {
            _sha256(candidate) for value in values for candidate in _candidates(value)
        } - self._by_hash.keys()
        if not wanted:
            return
        for (
            key_hash,
            org_id,
            key_id,
            key_status,
            deleted,
        ) in AgentccAPIKey._base_manager.filter(key_hash__in=wanted).values_list(
            "key_hash", "organization_id", "gateway_key_id", "status", "deleted"
        ):
            self._by_hash[key_hash] = (
                org_id,
                key_id,
                "deleted" if deleted else key_status,
            )
        for key_hash in wanted - self._by_hash.keys():
            self._by_hash[key_hash] = None

    def find(self, value):
        for candidate in _candidates(value):
            if found := self._by_hash.get(_sha256(candidate)):
                return found
        return None


class _Report:
    def __init__(self):
        self.rows = 0
        self.masked = []
        self.masked_rows = 0
        # org -> (key org, key id, status) -> {"rows": set, "headers": set}
        self.keys = defaultdict(
            lambda: defaultdict(lambda: {"rows": set(), "headers": set()})
        )
        # org -> request key id ("" for none) -> {"rows": set, "headers": set}
        self.unnamed = defaultdict(
            lambda: defaultdict(lambda: {"rows": set(), "headers": set()})
        )
        # org -> header name -> rows
        self.other = defaultdict(lambda: defaultdict(set))

    def add(self, pk, org_id, api_key_id, held, keys):
        self.rows += 1
        for name, value in held:
            found = keys.find(value)
            if found is not None:
                entry = self.keys[org_id][found]
            elif str(name).lower() in KEY_HEADERS or any(
                c.startswith(GATEWAY_KEY_PREFIX) for c in _candidates(value)
            ):
                entry = self.unnamed[org_id][api_key_id or ""]
            else:
                self.other[org_id][name].add(pk)
                continue
            entry["rows"].add(pk)
            entry["headers"].add(name)

    def write(self, out, *, scanned, applied):
        out.write(
            f"Scanned {scanned} request log row(s); {self.rows} with credential "
            "header values to mask."
        )
        for org_id in sorted({*self.keys, *self.unnamed, *self.other}, key=str):
            for (key_org, key_id, key_status), entry in sorted(
                self.keys[org_id].items(), key=lambda item: str(item[0][1])
            ):
                owner = "" if key_org == org_id else f" of org {key_org}"
                advice = (
                    "Rotate it."
                    if key_status == AgentccAPIKey.ACTIVE
                    else "It does not authenticate."
                )
                out.write(
                    f"org {org_id}: gateway key {key_id}{owner} ({key_status}) in "
                    f"{len(entry['rows'])} row(s), sent as "
                    f"{', '.join(sorted(entry['headers']))}. {advice}"
                )
            for api_key_id, entry in sorted(self.unnamed[org_id].items()):
                made_with = (
                    f"the request was made with key {api_key_id}: rotate that key"
                    if api_key_id
                    else "the request names no key"
                )
                out.write(
                    f"org {org_id}: a gateway key no stored key hash matches, in "
                    f"{len(entry['rows'])} row(s), sent as "
                    f"{', '.join(sorted(entry['headers']))}; {made_with}."
                )
            if self.other[org_id]:
                listed = ", ".join(
                    f"{name} in {len(rows)} row(s)"
                    for name, rows in sorted(self.other[org_id].items())
                )
                out.write(f"org {org_id}: other credential headers: {listed}.")
        if applied:
            out.write(f"Masked the credential headers of {self.masked_rows} row(s).")
        elif self.rows:
            out.write(
                "Dry run: nothing was changed. Run again with --apply to mask them."
            )
