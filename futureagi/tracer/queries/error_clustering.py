"""
Error clustering database operations using append-only approach.
Handles cluster creation, matching, and centroid management in ClickHouse.
"""

import hashlib
from typing import Any

import structlog

# Activity-aware stub: used inside Temporal trace-clustering activities.
from tfc.ee_stub import _ee_activity_stub as _ee_stub

try:
    from ee.agenthub.traceerroragent.error_cluster import ErrorEmbeddingClusterer
except ImportError:
    ErrorEmbeddingClusterer = _ee_stub("ErrorEmbeddingClusterer")
from agentic_eval.core.database.ch_vector import ClickHouseVectorDB

logger = structlog.get_logger(__name__)
from tracer.models.project import Project
from tracer.models.trace_error_analysis import (
    ErrorClusterTraces,
    TraceErrorDetail,
    TraceErrorGroup,
)


_DEFAULT_CENTROID_TTL_DAYS = 90


class ErrorClusteringDB:
    """Database operations for append-only error clustering."""

    def __init__(self, euclidean_threshold: float = 0.6, centroid_ttl_days: int = _DEFAULT_CENTROID_TTL_DAYS):
        self.euclidean_threshold = euclidean_threshold
        self.table_name = "error_embeddings"
        self.centroid_ttl_days = centroid_ttl_days

    def ensure_centroid_table(self):
        """Ensure the cluster_centroids table exists in ClickHouse."""

        db = ClickHouseVectorDB()
        try:
            # Array(...) can't sit inside Nullable; override server profiles
            # that set data_type_default_nullable=1 so unmodified types aren't
            # auto-wrapped.
            query = f"""
                CREATE TABLE IF NOT EXISTS cluster_centroids (
                    cluster_id String,
                    project_id UUID,
                    centroid Array(Float32),
                    member_count UInt32,
                    family String,
                    last_updated DateTime DEFAULT now(),
                    PRIMARY KEY (cluster_id)
                ) ENGINE = ReplacingMergeTree(last_updated)
                ORDER BY (cluster_id)
                TTL last_updated + INTERVAL {self.centroid_ttl_days} DAY DELETE
            """
            db.client.execute(query, settings={"data_type_default_nullable": 0})
        finally:
            db.close()

    def expire_stale_centroids(self) -> None:
        """Apply/update the TTL on an existing cluster_centroids table.

        Called once per clustering run so that tables created before TTL was
        introduced are retroactively covered.  ClickHouse ALTER TABLE MODIFY TTL
        is idempotent and fast (metadata-only change).
        """
        db = ClickHouseVectorDB()
        try:
            db.client.execute(
                f"ALTER TABLE cluster_centroids MODIFY TTL "
                f"last_updated + INTERVAL {self.centroid_ttl_days} DAY DELETE",
                settings={"data_type_default_nullable": 0},
            )
        except Exception:
            # Non-fatal: table may not exist yet (first run) or be read-only in tests.
            logger.warning("Could not modify cluster_centroids TTL; will retry next run")
        finally:
            db.close()

    def ensure_error_embeddings_table(self):
        """Ensure the error_embeddings table exists in ClickHouse."""

        db = ClickHouseVectorDB()
        try:
            # Use the standard ClickHouse vector table structure
            query = f"""
                CREATE TABLE IF NOT EXISTS {self.table_name} (
                    id UUID,
                    eval_id UUID,
                    vector Array(Float32),
                    metadata Nested (
                        key String,
                        value Nullable(String)
                    ),
                    deleted UInt8 DEFAULT 0
                ) ENGINE = MergeTree()
                ORDER BY (eval_id, id)
            """
            db.client.execute(query, settings={"data_type_default_nullable": 0})
        finally:
            db.close()

    def cluster_unclustered_errors(
        self,
        project_id: str,
        include_singletons: bool = True,
        min_cluster_size: int = 2,
        min_samples: int = 1,
    ) -> dict[str, Any]:
        """
        Cluster only unclustered errors using append-only approach.

        1. Fetch unclustered embeddings
        2. Try to match each to existing clusters (soft matching)
        3. Run HDBSCAN only on unmatched errors
        4. Create new clusters for HDBSCAN results
        5. Mark embeddings as clustered

        Returns:
            Dict with 'matched_to_existing', 'new_clusters', 'errors' keys
        """

        # Use ErrorEmbeddingClusterer for helper methods
        clusterer = ErrorEmbeddingClusterer(
            euclidean_threshold=self.euclidean_threshold,
            min_cluster_size=min_cluster_size,
            min_samples=min_samples,
        )

        result = {
            "matched_to_existing": 0,
            "new_clusters": 0,
            "errors": [],
        }

        try:
            # Ensure centroid table exists and its TTL is current.
            self.ensure_centroid_table()
            self.expire_stale_centroids()

            self.ensure_error_embeddings_table()

            # 1. Get unclustered embeddings from ClickHouse
            unclustered = self.get_unclustered_embeddings(project_id)
            if not unclustered:
                return result

            # 2. Get existing cluster centroids
            existing_clusters = self.get_existing_cluster_centroids(project_id)

            # 3. Try to match unclustered errors to existing clusters
            unmatched = []
            matched = []

            for emb_data in unclustered:
                error_detail_pk = emb_data["error_detail_pk"]
                vector = emb_data["vector"]
                family = emb_data["family"]

                # Only match within same family
                family_clusters = existing_clusters.get(family, [])

                best_cluster = None
                best_distance = float("inf")

                for cluster in family_clusters:
                    distance = clusterer._euclidean_distance(
                        vector, cluster["centroid"]
                    )
                    if distance < self.euclidean_threshold and distance < best_distance:
                        best_cluster = cluster
                        best_distance = distance

                if best_cluster:
                    # Add to existing cluster
                    self.add_to_existing_cluster(
                        cluster_id=best_cluster["cluster_id"],
                        error_detail_pk=error_detail_pk,
                        vector=vector,
                        clusterer=clusterer,
                    )
                    matched.append(emb_data)
                    result["matched_to_existing"] = (
                        result.get("matched_to_existing", 0) + 1
                    )
                else:
                    unmatched.append(emb_data)

            # 4. Cluster unmatched errors using HDBSCAN
            unmatched_list = unmatched if isinstance(unmatched, list) else []
            if unmatched_list:
                new_clusters = self.cluster_unmatched_errors(
                    unmatched_list,
                    project_id,
                    include_singletons,
                    clusterer,
                )
                result["new_clusters"] = len(new_clusters)

            # 5. Mark all processed embeddings as clustered
            processed_embedding_ids = [
                e["id"] for e in matched + unmatched if "id" in e
            ]
            if processed_embedding_ids:
                self.mark_embeddings_as_clustered(processed_embedding_ids, project_id)

        except Exception as e:
            logger.exception(f"Error in clustering: {str(e)}")
            result["errors"].append(str(e))

        return result

    def get_unclustered_embeddings(self, project_id: str) -> list[dict[str, Any]]:
        """Fetch embeddings marked as unclustered from ClickHouse."""

        clusterer = ErrorEmbeddingClusterer()

        db = ClickHouseVectorDB()
        try:
            query = f"""
                SELECT
                    id,
                    vector,
                    metadata.key as keys,
                    metadata.value as values
                FROM {self.table_name}
                WHERE eval_id = %(project_id)s
                AND deleted = 0
                ORDER BY id DESC
                LIMIT 1000
            """

            rows = db.client.execute(query, {"project_id": project_id})

            embeddings = []
            for row_id, vector, keys, values in rows:
                metadata = (
                    dict(zip(keys, values, strict=False)) if keys and values else {}
                )

                # Only include if clustered='false' or not set
                if metadata.get("clustered", "false") == "false":
                    family = clusterer._normalize_category_family(
                        metadata.get("category", "Uncategorized")
                    )
                    embeddings.append(
                        {
                            "id": row_id,
                            "vector": vector,
                            "error_detail_pk": metadata.get("error_detail_pk"),
                            "error_id": metadata.get("error_id"),
                            "trace_id": metadata.get("trace_id"),
                            "family": family,
                            "category": metadata.get("category", "Uncategorized"),
                        }
                    )

            return embeddings

        finally:
            db.close()

    def get_existing_cluster_centroids(self, project_id: str) -> dict[str, list[dict]]:
        """Get centroids of existing clusters from ClickHouse centroid table."""

        clusters_by_family: dict[Any, Any] = {}

        db = ClickHouseVectorDB()
        try:
            query = """
                SELECT
                    cluster_id,
                    centroid,
                    member_count,
                    family
                FROM cluster_centroids
                WHERE project_id = %(project_id)s
                ORDER BY last_updated DESC
            """

            rows = db.client.execute(query, {"project_id": project_id})

            for cluster_id, centroid, member_count, family in rows:
                if family not in clusters_by_family:
                    clusters_by_family[family] = []

                clusters_by_family[family].append(
                    {
                        "cluster_id": cluster_id,
                        "centroid": centroid,
                        "count": member_count,
                    }
                )
        finally:
            db.close()

        return clusters_by_family

    def add_to_existing_cluster(
        self,
        cluster_id: str,
        error_detail_pk: str,
        vector: list[float],
        clusterer,
    ) -> None:
        """Add an error to an existing cluster and update centroid."""

        try:
            detail = TraceErrorDetail.objects.get(id=error_detail_pk)
            project_id = detail.analysis.project_id
            cluster = TraceErrorGroup.objects.get(
                cluster_id=cluster_id, project_id=project_id
            )

            # Update cluster error_ids
            if detail.error_id and detail.error_id not in cluster.error_ids:
                cluster.error_ids.append(detail.error_id)

            # Update metrics
            cluster.error_count += 1
            cluster.total_events = (cluster.total_events or 0) + len(
                detail.location_spans or []
            )

            # Update affected traces using junction table
            trace = detail.analysis.trace

            # Prepare junction entries for bulk creation
            junction_entries = []
            if detail.location_spans:
                # Create entries for each span
                junction_entries = [
                    ErrorClusterTraces(cluster=cluster, trace=trace, span_id=span_id)
                    for span_id in detail.location_spans
                ]
            else:
                # Create entry for trace without specific span
                junction_entries = [
                    ErrorClusterTraces(cluster=cluster, trace=trace, span=None)
                ]

            # Bulk create with ignore_conflicts to handle duplicates
            if junction_entries:
                ErrorClusterTraces.objects.bulk_create(
                    junction_entries, ignore_conflicts=True
                )

            # Update unique traces count from junction table
            cluster.unique_traces = cluster.clusters.values("trace").distinct().count()

            # Update timestamps (manually since we're not using auto_now)
            if cluster.first_seen is None or detail.created_at < cluster.first_seen:
                cluster.first_seen = detail.created_at
            if cluster.last_seen is None or detail.created_at > cluster.last_seen:
                cluster.last_seen = detail.created_at

            cluster.save()

            # Update centroid in ClickHouse
            db = ClickHouseVectorDB()
            try:
                # Fetch existing centroid
                query = """
                    SELECT centroid, member_count
                    FROM cluster_centroids
                    WHERE cluster_id = %(cluster_id)s
                    LIMIT 1
                """
                rows = db.client.execute(query, {"cluster_id": cluster_id})

                if rows:
                    old_centroid, old_member_count = rows[0]
                    new_centroid = clusterer._update_centroid_incremental(
                        old_centroid, vector, old_member_count
                    )
                else:
                    # First member
                    new_centroid = vector
                    old_member_count = 0

                # Update/Insert centroid
                family = clusterer._normalize_category_family(cluster.error_type)
                upsert_query = """
                    INSERT INTO cluster_centroids (
                        cluster_id, project_id, centroid, member_count, family, last_updated
                    ) VALUES (
                        %(cluster_id)s, %(project_id)s, %(centroid)s, %(member_count)s, %(family)s, now()
                    )
                """
                db.client.execute(
                    upsert_query,
                    {
                        "cluster_id": cluster_id,
                        "project_id": str(cluster.project_id),
                        "centroid": new_centroid,
                        "member_count": old_member_count + 1,
                        "family": family,
                    },
                )
            finally:
                db.close()

        except Exception as e:
            logger.exception(f"Failed to add to cluster {cluster_id}: {str(e)}")

    def cluster_unmatched_errors(
        self,
        unmatched: list[dict],
        project_id: str,
        include_singletons: bool,
        clusterer,
    ) -> list[str]:
        """Run HDBSCAN on unmatched errors to form new clusters."""
        try:
            import hdbscan
        except ImportError:
            logger.error("HDBSCAN not available, skipping clustering")
            return []

        # Group by family for clustering
        by_family: dict[Any, Any] = {}
        for emb in unmatched:
            family = emb["family"]
            if family not in by_family:
                by_family[family] = []
            by_family[family].append(emb)

        new_cluster_ids = []

        for family, family_embeddings in by_family.items():
            if len(family_embeddings) < 2:
                # Create singleton cluster if requested
                if include_singletons:
                    emb = family_embeddings[0]
                    cluster_id = self.create_new_cluster(
                        project_id=project_id,
                        error_detail_pks=[emb["error_detail_pk"]],
                        vectors=[emb["vector"]],
                        family=family,
                        clusterer=clusterer,
                    )
                    if cluster_id:
                        new_cluster_ids.append(cluster_id)
                continue

            # Extract vectors for HDBSCAN
            vectors = [e["vector"] for e in family_embeddings]
            pks = [e["error_detail_pk"] for e in family_embeddings]

            # Run HDBSCAN
            clusterer_hdbscan = hdbscan.HDBSCAN(
                min_cluster_size=clusterer.min_cluster_size,
                min_samples=clusterer.min_samples,
                metric=clusterer.metric,
                cluster_selection_epsilon=0.0,
                cluster_selection_method="eom",
            )

            labels = clusterer_hdbscan.fit_predict(vectors)

            # Group by cluster label
            clusters: dict[Any, Any] = {}
            for i, label in enumerate(labels):
                if label == -1 and not include_singletons:
                    continue  # Skip noise if not including singletons

                if label not in clusters:
                    clusters[label] = {"pks": [], "vectors": []}

                clusters[label]["pks"].append(pks[i])
                clusters[label]["vectors"].append(vectors[i])

            # Create new clusters
            for _label, cluster_data in clusters.items():
                cluster_id = self.create_new_cluster(
                    project_id=project_id,
                    error_detail_pks=cluster_data["pks"],
                    vectors=cluster_data["vectors"],
                    family=family,
                    clusterer=clusterer,
                )
                if cluster_id:
                    new_cluster_ids.append(cluster_id)

        return new_cluster_ids

    def create_new_cluster(
        self,
        project_id: str,
        error_detail_pks: list[str],
        vectors: list[list[float]],
        family: str,
        clusterer,
    ) -> str | None:
        """Create a new TraceErrorGroup cluster."""

        try:
            # Generate stable cluster ID (MD5 used for non-security identifier generation only)
            base = f"{project_id}|{family}|{'|'.join(sorted(error_detail_pks))}"
            h = hashlib.md5(base.encode(), usedforsecurity=False).hexdigest()[:8]
            cluster_id = f"K-{h.upper()}"

            # Get error details
            details = TraceErrorDetail.objects.filter(
                id__in=error_detail_pks
            ).select_related("analysis__trace")

            if not details:
                return None

            # Compute centroid
            centroid = clusterer._compute_centroid(vectors)

            # Prepare cluster data
            error_ids = []
            total_events = 0
            combined_impact = "MEDIUM"
            order = {"HIGH": 3, "MEDIUM": 2, "LOW": 1}

            # Collect trace-span pairs for junction table
            trace_span_pairs = []

            first_detail = None
            for detail in details:
                if not first_detail:
                    first_detail = detail

                if detail.error_id and detail.error_id not in error_ids:
                    error_ids.append(detail.error_id)

                total_events += len(detail.location_spans or [])

                # Collect trace-span pairs for later insertion
                trace = detail.analysis.trace
                if detail.location_spans:
                    for span_id in detail.location_spans:
                        trace_span_pairs.append((trace, span_id))
                else:
                    # Add trace without specific span
                    trace_span_pairs.append((trace, None))

                imp = detail.impact or "MEDIUM"
                if order.get(imp, 0) > order.get(combined_impact, 0):
                    combined_impact = imp

            # Create description
            first_detail_desc = first_detail.description if first_detail else None
            desc = (first_detail_desc or "").strip()
            title = desc.splitlines()[0][:200] if desc else f"Error cluster in {family}"

            # Get project
            project = Project.objects.get(id=project_id)

            # Create the cluster
            cluster, created = TraceErrorGroup.objects.get_or_create(
                project=project,
                cluster_id=cluster_id,
                defaults={
                    "error_type": family,
                    "error_ids": error_ids,
                    "combined_impact": combined_impact,
                    "combined_description": title,
                    "error_count": len(details),
                    "total_events": total_events,
                    "unique_traces": 0,  # Will be updated after junction table entries
                    "unique_users": 0,  # TODO: track users if needed
                    "first_seen": (
                        min(d.created_at for d in details) if details else None
                    ),
                    "last_seen": (
                        max(d.created_at for d in details) if details else None
                    ),
                },
            )

            # Create junction table entries
            junction_entries = []
            seen_pairs = set()  # To avoid duplicates

            for trace, span_id in trace_span_pairs:
                pair = (trace.id, span_id)
                if pair not in seen_pairs:
                    seen_pairs.add(pair)
                    junction_entries.append(
                        ErrorClusterTraces(
                            cluster=cluster, trace=trace, span_id=span_id
                        )
                    )

            # Bulk create junction entries
            if junction_entries:
                ErrorClusterTraces.objects.bulk_create(
                    junction_entries, ignore_conflicts=True
                )

            # Update unique_traces count
            cluster.unique_traces = cluster.clusters.values("trace").distinct().count()
            cluster.save(update_fields=["unique_traces"])

            # Store centroid in ClickHouse
            db = ClickHouseVectorDB()
            try:
                insert_query = """
                    INSERT INTO cluster_centroids (
                        cluster_id, project_id, centroid, member_count, family, last_updated
                    ) VALUES (
                        %(cluster_id)s, %(project_id)s, %(centroid)s, %(member_count)s, %(family)s, now()
                    )
                """
                db.client.execute(
                    insert_query,
                    {
                        "cluster_id": cluster_id,
                        "project_id": project_id,
                        "centroid": centroid,
                        "member_count": len(details),
                        "family": family,
                    },
                )
            finally:
                db.close()

            return cluster_id

        except Exception as e:
            logger.exception(f"Failed to create new cluster: {str(e)}")
            return None

    # Helper methods for junction table operations
    def get_cluster_traces(self, cluster_id: str) -> list[str]:
        """Get all trace IDs associated with a cluster via junction table."""
        return list(
            ErrorClusterTraces.objects.filter(cluster__cluster_id=cluster_id)
            .values_list("trace_id", flat=True)
            .distinct()
        )

    def get_cluster_spans(self, cluster_id: str) -> list[str]:
        """Get all span IDs associated with a cluster via junction table."""
        return list(
            ErrorClusterTraces.objects.filter(cluster__cluster_id=cluster_id)
            .exclude(span=None)
            .values_list("span_id", flat=True)
            .distinct()
        )

    def add_trace_to_cluster(
        self, cluster: TraceErrorGroup, trace, span_ids: list[str] | None = None
    ) -> int:
        """Add a trace and its spans to a cluster using bulk operations.
        Returns number of new entries that would be created (approximate).
        """
        junction_entries = []

        if span_ids:
            # Create entries for each span
            junction_entries = [
                ErrorClusterTraces(cluster=cluster, trace=trace, span_id=span_id)
                for span_id in span_ids
            ]
        else:
            # Create entry for trace without spans
            junction_entries = [
                ErrorClusterTraces(cluster=cluster, trace=trace, span=None)
            ]

        # Bulk create with ignore_conflicts to handle duplicates
        if junction_entries:
            ErrorClusterTraces.objects.bulk_create(
                junction_entries, ignore_conflicts=True
            )

        return len(junction_entries)

    def mark_embeddings_as_clustered(
        self, embedding_ids: list[str], project_id: str
    ) -> None:
        """Mark embeddings as clustered in ClickHouse by updating their metadata.

        Uses the ClickHouse pattern of soft-delete and re-insert for updates.
        """

        if not embedding_ids:
            return

        db = ClickHouseVectorDB()
        try:
            # Fetch the existing embeddings data
            ids_str = ", ".join([f"'{eid}'" for eid in embedding_ids])
            fetch_query = f"""
                SELECT
                    id,
                    eval_id,
                    vector,
                    metadata.key as keys,
                    metadata.value as values
                FROM {self.table_name}
                WHERE
                    id IN ({ids_str})
                    AND eval_id = %(project_id)s
                    AND deleted = 0
            """

            rows = db.client.execute(fetch_query, {"project_id": project_id})

            if not rows:
                logger.warning(
                    f"No embeddings found to mark as clustered for IDs: {embedding_ids[:5]}..."
                )
                return

            # Process fetched embeddings and update metadata
            vectors_to_update = []
            metadata_list_to_update = []
            ids_to_update = []
            eval_ids_to_update = []

            for row_id, eval_id, vector, keys, values in rows:
                metadata = (
                    dict(zip(keys, values, strict=False)) if keys and values else {}
                )

                # Update or add 'clustered' flag
                metadata["clustered"] = "true"

                vectors_to_update.append(vector)
                metadata_list_to_update.append(metadata)
                ids_to_update.append(row_id)
                eval_ids_to_update.append(eval_id)

            # Mark old entries as deleted
            delete_query = f"""
                ALTER TABLE {self.table_name}
                UPDATE deleted = 1
                WHERE
                    id IN ({ids_str})
                    AND eval_id = %(project_id)s
                    AND deleted = 0
            """

            db.client.execute(delete_query, {"project_id": project_id})

            # Re-insert with updated metadata
            if vectors_to_update:
                insert_query = f"""
                    INSERT INTO {self.table_name}
                    (id, eval_id, vector, metadata.key, metadata.value, deleted)
                    VALUES
                """

                insert_data = []
                for row_id, eval_id, vector, metadata in zip(
                    ids_to_update,
                    eval_ids_to_update,
                    vectors_to_update,
                    metadata_list_to_update,
                    strict=False,
                ):
                    metadata_keys = list(metadata.keys())
                    metadata_values = list(metadata.values())
                    insert_data.append(
                        (row_id, eval_id, vector, metadata_keys, metadata_values, 0)
                    )

                db.client.execute(insert_query, insert_data, types_check=True)

                logger.info(
                    f"Successfully marked {len(ids_to_update)} embeddings as clustered in ClickHouse"
                )

        except Exception as e:
            logger.exception(f"Error marking embeddings as clustered: {str(e)}")
            # Don't fail the clustering process if marking fails
        finally:
            db.close()
