"""Count the stored model configurations that hold a reference value.

    manage.py count_platform_value_configs            integers only
    manage.py count_platform_value_configs --names    and the names
    manage.py count_platform_value_configs --vertex-rows-without-a-key
                                                      and those Vertex rows

A provider key or custom model saved before the save-time check can still
hold a reference (such as os.environ/... or oidc/...) instead of a literal
value.
Such a row is refused before every call; this command says how many there are.

It reads every provider key (``ApiKey``: key and configuration), custom model
(``CustomAIModel``: credentials) and secret (``SecretModel``: value) through
the plain manager, so deleted rows are read too and reported apart, and
decrypts each in memory with the model's own accessors. A string at any depth
holds a reference value when ``platform_value_references`` says so: the check
that refuses it on save and before every call. It writes nothing (no save,
update, delete or signal) and opens no connection but the database's, so the
mutation-free startup guard admits it in any process (``model_hub/apps.py``).

It prints one JSON object of integers:

- ``tables``: per table, ``rows_scanned`` beside ``count_star`` (the table's
  row count; the two are equal), ``rows_with_reference`` (deleted rows
  included), ``rows_with_reference_not_deleted``, ``rows_unreadable``
  (rows with a value this deployment cannot decrypt; that value is not read),
  ``rows_with_identity_fields`` (rows that set one of the reserved
  identity fields at any depth, in a value or in JSON text of one) and
  ``rows_with_external_identity`` (rows no model may use as its Google
  credential: for any provider, another kind of Google credential than a
  service-account key; for a row stored for Vertex AI, anything other than a
  service-account key that signs in at Google's own address; a secret is no
  model configuration and names no provider, so it is never counted there),
  deleted rows included, from the values of a row it can read;
- ``orgs_with_reference``: organizations with such a row in any table,
  deleted rows included.

With ``--names`` it also prints:

- ``names``: each name a row names, only when it matches
  ``^[A-Z_][A-Z0-9_]{0,127}$``, with ``exposed_candidate``, ``rows`` and
  ``orgs``. ``exposed_candidate`` is true when this process's environment
  sets the name; the command never prints the value or its length;
- ``refs_unprintable``: how many strings named something else (never shown:
  it can be control characters, a pasted key or personal data);
- ``issuers``: for the other form, only the issuer, one of google, azure,
  circleci and github or else ``other``, with ``rows`` and ``orgs``; never
  what follows it.

With ``--vertex-rows-without-a-key`` it also prints:

- ``vertex_without_a_key``: for provider keys and custom models, the rows
  stored for Vertex AI that give no complete service-account key of their
  own (a key as text, a key alone, a project alone, a key without its
  private key or client email, an empty configuration, a location alone, or
  nothing; ``stored_vertex_row_gives_no_service_account_key``),
  deleted rows included and rows this deployment cannot decrypt left out:
  ``rows``, ``rows_not_deleted`` and the row ``ids``. Integers and row ids
  only, never a value.

The exit status is 0 whatever the counts; any count above 0 needs attention at
once.
"""

import json
import os
import re

from django.core.management.base import BaseCommand

from model_hub.models.api_key import ApiKey, SecretModel
from model_hub.models.custom_models import CustomAIModel
from model_hub.services.key_at_rest import KeyUnreadable
from model_hub.utils.model_endpoints import (
    is_google_credential_other_than_a_service_account_key,
    is_vertex_provider,
    platform_value_references,
    stored_config_sets_platform_identity_field,
    stored_vertex_row_gives_no_service_account_key,
)

PRINTABLE_NAME = re.compile(r"[A-Z_][A-Z0-9_]{0,127}")
KNOWN_ISSUERS = frozenset({"google", "azure", "circleci", "github"})

# Each table, with the accessors that decrypt its stored values in memory.
TABLES = (
    (ApiKey, ("actual_key", "actual_json")),
    (CustomAIModel, ("actual_json",)),
    (SecretModel, ("actual_key",)),
)


class _Seen:
    """The rows and organizations one printed name or issuer appears in."""

    def __init__(self):
        self.rows = set()
        self.orgs = set()

    def add(self, row):
        self.rows.add((type(row).__name__, row.pk))
        if row.organization_id is not None:
            self.orgs.add(row.organization_id)

    def counts(self):
        return {"rows": len(self.rows), "orgs": len(self.orgs)}


class Command(BaseCommand):
    help = (
        "Count the stored provider keys, custom models and secrets that hold "
        "a reference (such as os.environ/... or oidc/...) instead of a literal "
        "value: integers only, deleted and undecryptable rows apart. Reads only."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--names",
            action="store_true",
            help=(
                "Also print each plain upper-case name and whether a value of "
                "that name is set where this command runs, and each issuer; "
                "never a value."
            ),
        )
        parser.add_argument(
            "--vertex-rows-without-a-key",
            action="store_true",
            help=(
                "Also print, for provider keys and custom models, the rows "
                "stored for Vertex AI with no complete service-account key of "
                "their own: counts and row ids only."
            ),
        )

    def handle(self, *args, names=False, vertex_rows_without_a_key=False, **options):
        tables = {}
        orgs = set()
        by_name = {}
        by_issuer = {}
        unprintable = 0
        # table -> (id, deleted) of each row stored for Vertex AI with no
        # service-account key of its own
        vertex_without_a_key = {}
        for model, accessors in TABLES:
            if hasattr(model, "provider"):
                vertex_without_a_key[model.__name__] = []
            count_star = model._base_manager.count()
            scanned = with_reference = not_deleted = unreadable = 0
            identity_fields = external_identity = 0
            for row in model._base_manager.all():
                scanned += 1
                stored, cannot_decrypt = [], False
                for accessor in accessors:
                    try:
                        stored.append(getattr(row, accessor))
                    except KeyUnreadable:
                        cannot_decrypt = True
                if cannot_decrypt:
                    unreadable += 1
                if stored_config_sets_platform_identity_field(*stored):
                    identity_fields += 1
                # A secret names no provider and is never counted. A row's
                # provider is never None here, so no row is taken as a Vertex
                # one by its shape alone.
                if hasattr(row, "provider") and (
                    is_google_credential_other_than_a_service_account_key(
                        row.provider or "", *stored
                    )
                ):
                    external_identity += 1
                if (
                    model.__name__ in vertex_without_a_key
                    and not cannot_decrypt
                    and is_vertex_provider(row.provider)
                    and stored_vertex_row_gives_no_service_account_key(*stored)
                ):
                    vertex_without_a_key[model.__name__].append(
                        (str(row.pk), row.deleted)
                    )
                references = list(platform_value_references(stored))
                if not references:
                    continue
                with_reference += 1
                if not row.deleted:
                    not_deleted += 1
                if row.organization_id is not None:
                    orgs.add(row.organization_id)
                for form, rest in references:
                    if form == "name":
                        if not PRINTABLE_NAME.fullmatch(rest):
                            unprintable += 1
                            continue
                        seen = by_name.setdefault(rest, _Seen())
                    else:
                        issuer = rest.split("/", 1)[0].lower()
                        if issuer not in KNOWN_ISSUERS:
                            issuer = "other"
                        seen = by_issuer.setdefault(issuer, _Seen())
                    seen.add(row)
            tables[model.__name__] = {
                "rows_scanned": scanned,
                "count_star": count_star,
                "rows_with_reference": with_reference,
                "rows_with_reference_not_deleted": not_deleted,
                "rows_unreadable": unreadable,
                "rows_with_identity_fields": identity_fields,
                "rows_with_external_identity": external_identity,
            }

        report = {"tables": tables, "orgs_with_reference": len(orgs)}
        if names:
            report["names"] = [
                {
                    "name": name,
                    # Whether it is set here; the value is never read.
                    "exposed_candidate": name in os.environ,
                    **seen.counts(),
                }
                for name, seen in sorted(by_name.items())
            ]
            report["issuers"] = [
                {"issuer": issuer, **seen.counts()}
                for issuer, seen in sorted(by_issuer.items())
            ]
            report["refs_unprintable"] = unprintable
        if vertex_rows_without_a_key:
            report["vertex_without_a_key"] = {
                label: {
                    "rows": len(rows),
                    "rows_not_deleted": sum(not deleted for _pk, deleted in rows),
                    "ids": sorted(pk for pk, _deleted in rows),
                }
                for label, rows in vertex_without_a_key.items()
            }
        self.stdout.write(json.dumps(report))
