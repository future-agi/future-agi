"""Pure validation and identity helpers for CH25 schema topology."""

from __future__ import annotations

import re
from dataclasses import dataclass

_CLUSTER_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_ZK_PATH_PREFIX = re.compile(r"^/[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*$")

HOSTED_PRODUCTION_ENVIRONMENTS = frozenset({"prod", "production"})
HOSTED_PRODUCTION_DEPLOYMENTS = frozenset({"US", "EU"})

PROPERTY_CATALOG_TABLES = frozenset(
    {
        "property_catalog_activations",
        "property_catalog_checkpoints",
        "property_catalog_deliveries",
        "property_catalog_source_streams",
        "property_definition_catalog",
        "span_attribute_value_catalog",
    }
)

# A replicated preflight must also reject stale local tables from the discarded
# pre-release span-only design. They are not part of the new schema allowlist
# and must never be silently accepted by CREATE TABLE IF NOT EXISTS.
RETIRED_PROPERTY_CATALOG_TABLES = frozenset(
    {
        "span_attribute_catalog_activations",
        "span_attribute_catalog_checkpoints",
        "span_attribute_catalog_deliveries",
        "span_attribute_catalog_source_streams",
        "span_attribute_key_catalog",
    }
)

CATALOG_TOPOLOGY_GUARD_TABLES = frozenset(
    {"schema_versions", *PROPERTY_CATALOG_TABLES, *RETIRED_PROPERTY_CATALOG_TABLES}
)


class SchemaTopologyError(ValueError):
    """The requested schema topology is incomplete or unsafe."""


@dataclass(frozen=True)
class SchemaTopology:
    """Validated local or Keeper-replicated schema identity."""

    replicated: bool
    cluster: str | None = None
    zk_table_path_prefix: str | None = None

    @classmethod
    def from_options(
        cls,
        *,
        replicated: bool,
        cluster: object = None,
        zk_table_path_prefix: object = None,
        require_replicated: bool = False,
    ) -> SchemaTopology:
        cluster_supplied = cluster is not None
        zk_prefix_supplied = zk_table_path_prefix is not None
        cluster_name = str(cluster or "").strip()
        zk_prefix = str(zk_table_path_prefix or "").strip()

        if not replicated:
            if cluster_supplied or zk_prefix_supplied:
                raise SchemaTopologyError(
                    "--cluster and --zk-table-path-prefix require --replicated"
                )
            if require_replicated:
                raise SchemaTopologyError(
                    "hosted production schema apply requires explicit "
                    "--replicated, --cluster, and --zk-table-path-prefix"
                )
            return cls(replicated=False)

        missing = []
        if not cluster_name:
            missing.append("--cluster")
        if not zk_prefix:
            missing.append("--zk-table-path-prefix")
        if missing:
            raise SchemaTopologyError(
                "--replicated requires explicit " + " and ".join(missing)
            )
        if not _CLUSTER_NAME.fullmatch(cluster_name):
            raise SchemaTopologyError(
                "--cluster must contain only letters, digits, '.', '_', and '-'"
            )
        if not _ZK_PATH_PREFIX.fullmatch(zk_prefix):
            raise SchemaTopologyError(
                "--zk-table-path-prefix must be an absolute Keeper path with "
                "literal safe path segments and no trailing slash or macros"
            )

        return cls(
            replicated=True,
            cluster=cluster_name,
            zk_table_path_prefix=zk_prefix,
        )

    @property
    def version_identity(self) -> str:
        """Stable identity stored with each schema_versions record."""

        if not self.replicated:
            return "schema-topology/v1/local"
        return (
            "schema-topology/v1/replicated;"
            f"cluster={self.cluster};zk_prefix={self.zk_table_path_prefix}"
        )

    @property
    def includes_legacy_local_versions(self) -> bool:
        """Blank notes were the pre-topology identity for local applies."""

        return not self.replicated


def is_hosted_production(environment: object, cloud_deployment: object) -> bool:
    """Return whether schema operations target a hosted production region."""

    normalized_environment = str(environment or "").strip().lower()
    normalized_deployment = str(cloud_deployment or "").strip().upper()
    return (
        normalized_environment in HOSTED_PRODUCTION_ENVIRONMENTS
        and normalized_deployment in HOSTED_PRODUCTION_DEPLOYMENTS
    )


__all__ = [
    "CATALOG_TOPOLOGY_GUARD_TABLES",
    "PROPERTY_CATALOG_TABLES",
    "RETIRED_PROPERTY_CATALOG_TABLES",
    "SchemaTopology",
    "SchemaTopologyError",
    "is_hosted_production",
]
