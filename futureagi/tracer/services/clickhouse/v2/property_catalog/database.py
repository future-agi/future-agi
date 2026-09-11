"""Shared property-catalog database identities.

Keep the production identity in one lightweight module so readers and writers
cannot silently diverge when the deployment database name changes.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping

PRODUCTION_PROPERTY_CATALOG_DATABASE_ENV = (
    "PROPERTY_CATALOG_PRODUCTION_DATABASE"
)
DEFAULT_PRODUCTION_PROPERTY_CATALOG_DATABASE = "property_catalog"
_DATABASE_IDENTIFIER_RE = re.compile(r"\A[a-z][a-z0-9_]*\Z")
_RESERVED_DATABASES = frozenset(
    {"default", "futureagi", "information_schema", "system"}
)


def configured_production_property_catalog_database(
    source: Mapping[str, str] = os.environ,
) -> str:
    """Return the explicitly configured production catalog database.

    The database name is an infrastructure contract, not an application
    constant.  Keeping the default preserves existing installations while a
    deployment with an already-provisioned isolated catalog can bind every
    reader and writer to that exact database through one environment value.
    """

    raw_database = source.get(
        PRODUCTION_PROPERTY_CATALOG_DATABASE_ENV,
        DEFAULT_PRODUCTION_PROPERTY_CATALOG_DATABASE,
    )
    if not isinstance(raw_database, str):
        raise ValueError(
            f"{PRODUCTION_PROPERTY_CATALOG_DATABASE_ENV} must be a safe, "
            "isolated ClickHouse database identifier"
        )
    database = raw_database.strip()
    if (
        not database
        or len(database.encode("utf-8")) > 128
        or _DATABASE_IDENTIFIER_RE.fullmatch(database) is None
        or database in _RESERVED_DATABASES
    ):
        raise ValueError(
            f"{PRODUCTION_PROPERTY_CATALOG_DATABASE_ENV} must be a safe, "
            "isolated ClickHouse database identifier"
        )
    return database


PRODUCTION_PROPERTY_CATALOG_DATABASE = (
    configured_production_property_catalog_database()
)


def is_production_property_catalog_database(database: str) -> bool:
    """Return whether ``database`` is the canonical production catalog."""

    return database == configured_production_property_catalog_database()


__all__ = [
    "DEFAULT_PRODUCTION_PROPERTY_CATALOG_DATABASE",
    "PRODUCTION_PROPERTY_CATALOG_DATABASE",
    "PRODUCTION_PROPERTY_CATALOG_DATABASE_ENV",
    "configured_production_property_catalog_database",
    "is_production_property_catalog_database",
]
