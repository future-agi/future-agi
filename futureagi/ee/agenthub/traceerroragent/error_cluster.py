import hashlib
import re
from collections.abc import Iterable
from typing import Any

from ee.agenthub.traceerroragent.prompts import ERROR_TAXONOMY
from agentic_eval.core.database.ch_vector import ClickHouseVectorDB
from tracer.queries.error_analysis import TraceErrorAnalysisDB


class ErrorClusterer:
    """Deterministic v1 issue clusterer without external dependencies.

    Approach:
    - Block by taxonomy head and subcategory to keep merges sane
    - Build normalized signatures from description + (1) evidence + root_causes + recommendation
    - Tokenize into char n-grams (3-5), word bigrams, and domain keywords
    - Similarity = 0.6*J(char) + 0.3*J(word-bigrams) + 0.1*J(keywords)
    - Greedy agglomerative per block with threshold; optional cluster-merge refinement

    Trace membership: traces can map to many clusters via member errors.
    """

    def __init__(self, threshold: float = 0.55, min_cluster_size: int = 1, normalize_categories: bool = True):
        self.threshold = threshold
        self.min_cluster_size = min_cluster_size
        self.normalize_categories = normalize_categories
        self._hex_or_long_num = re.compile(r"\b[0-9a-f]{8,}\b|\b\d{2,}\b")
        self._whitespace = re.compile(r"\s+")
        self._alpha = re.compile(r"[a-z]+")
        # Domain keywords to stabilize clustering across paraphrases
        self._domain_keywords: set[str] = {
            'plan','planning','execute','execution','final','final_answer','tool','tools','search_agent',
            'retrieval','rag','timeout','rate','limit','ratelimit','pii','leak','json','format','schema',
            'ocr','audio','image','pdf','token','auth','authentication','authorization','server','crash',
            'misuse','misconfigured','orchestration','step','steps','verify','verification','grounding',
            'hallucination','ungrounded','unsafe','advice','self','correction','self-correction'
        }

    def cluster_errors(self, errors: list[dict[str, Any]], default_trace_id: str | None = None) -> list[dict[str, Any]]:
        if not errors:
            return []

        # Pre-block by category (optionally normalized using ERROR_TAXONOMY)
        blocks: dict[str, list[dict[str, Any]]] = {}
        for err in errors:
            raw = (err.get('category') or 'Uncategorized').strip()
            cat = self._normalize_category_family(raw) if self.normalize_categories else raw
            blocks.setdefault(cat, []).append(err)

        clusters: list[dict[str, Any]] = []
        cluster_counter = 1
        for full_category, group in blocks.items():
            # Prepare signatures and token sets for group
            prepared: list[dict[str, Any]] = []
            for e in group:
                trace_id = e.get('trace_id') or default_trace_id or ''
                sig = self._build_signature_text(e)
                norm = self._normalize(sig)
                char_tokens = self._char_ngrams(norm, {3, 4, 5})
                word_bigrams = self._word_bigrams(norm)
                keywords = self._extract_keywords(norm)
                prepared.append({
                    'err': e,
                    'trace_id': trace_id,
                    'char': char_tokens,
                    'bigrams': word_bigrams,
                    'keys': keywords,
                })

            # Greedy clustering
            local_clusters: list[dict[str, Any]] = []
            for item in prepared:
                best_idx = None
                best_score = 0.0
                for idx, lc in enumerate(local_clusters):
                    score = self._similarity(item, lc['centroid'])
                    if score >= self.threshold and score > best_score:
                        best_idx = idx
                        best_score = score
                if best_idx is None:
                    local_clusters.append(self._new_local_cluster(item))
                else:
                    self._add_to_local_cluster(local_clusters[best_idx], item)

            # Optional refinement: merge clusters that are close
            local_clusters = self._merge_close_clusters(local_clusters, merge_threshold=max(0.5, self.threshold - 0.05))

            # Hard fallback: if still one cluster per error, collapse all into one cluster for this category
            if len(local_clusters) == len(prepared) and len(prepared) > 1:
                merged_all = self._new_local_cluster(prepared[0])
                for item in prepared[1:]:
                    self._add_to_local_cluster(merged_all, item)
                local_clusters = [merged_all]

            # Format output clusters
            for lc in local_clusters:
                if len(lc['members']) < self.min_cluster_size:
                    # Keep singletons as clusters (noise) for analysis; user can filter later
                    pass
                clusters.append(self._format_cluster(
                    lc,
                    cluster_id=f"K{str(cluster_counter).zfill(2)}",
                    category=full_category
                ))
                cluster_counter += 1

        return clusters

    def _extract_head_and_sub(self, category: str) -> tuple[str, str]:
        parts = [p.strip() for p in category.split('>') if p.strip()]
        if not parts:
            return ('General', '')
        if len(parts) == 1:
            return (parts[0], '')
        return (parts[0], parts[1])

    def _build_signature_text(self, err: dict[str, Any]) -> str:
        pieces: list[str] = []
        d = err.get('description')
        if d:
            pieces.append(str(d))
        # include up to two evidence lines (first two non-empty)
        added = 0
        for ev in err.get('evidence_snippets', []) or []:
            if not ev:
                continue
            first = str(ev).splitlines()[0]
            if first:
                pieces.append(first)
                added += 1
                if added >= 2:
                    break
        for rc in err.get('root_causes', []) or []:
            pieces.append(str(rc))
        r = err.get('recommendation')
        if r:
            pieces.append(str(r))
        return ' '.join(pieces)

    def _normalize(self, text: str) -> str:
        if not text:
            return ''
        t = text.lower()
        t = self._hex_or_long_num.sub(' ', t)
        t = self._whitespace.sub(' ', t).strip()
        return t

    def _char_ngrams(self, text: str, ns: set[int]) -> set[str]:
        tokens: set[str] = set()
        cleaned = ''.join(ch for ch in text if 'a' <= ch <= 'z' or ch == ' ')
        compact = cleaned.replace(' ', '')
        for n in ns:
            if n <= 0 or len(compact) < n:
                continue
            for i in range(len(compact) - n + 1):
                tokens.add(compact[i:i+n])
        return tokens

    def _word_bigrams(self, text: str) -> set[str]:
        words = [w for w in self._alpha.findall(text) if len(w) > 2]
        return {f"{words[i]}_{words[i+1]}" for i in range(len(words) - 1)} if len(words) >= 2 else set()

    def _extract_keywords(self, text: str) -> set[str]:
        words = set(self._alpha.findall(text))
        return {w for w in words if w in self._domain_keywords}

    def _normalize_category_family(self, category_path: str) -> str:
        """Snap category tails to ERROR_TAXONOMY where possible.

        If head and subcategory are known, pick closest error name under that subcategory
        using token Jaccard. Otherwise, return original or truncate to known levels.
        """
        parts = [p.strip() for p in category_path.split('>') if p.strip()]
        if not parts:
            return 'Uncategorized'

        head = parts[0]
        sub = parts[1] if len(parts) > 1 else ''
        tail = parts[2] if len(parts) > 2 else ''

        if head not in ERROR_TAXONOMY:
            return category_path

        head_data = ERROR_TAXONOMY[head]
        sub_map = head_data.get('subcategories', {})

        # If sub present and exists
        if sub and sub in sub_map:
            errors = [e['name'] for e in sub_map[sub].get('errors', [])]
            if not errors:
                return f"{head} > {sub}"
            if not tail:
                return f"{head} > {sub}"
            tail_tokens = set(self._alpha.findall(tail.lower()))
            best = None
            best_sim = 0.0
            for name in errors:
                name_tokens = set(self._alpha.findall(name.lower()))
                sim = self._jaccard(tail_tokens, name_tokens)
                if sim > best_sim:
                    best = name
                    best_sim = sim
            if best and best_sim >= 0.3:
                return f"{head} > {sub} > {best}"
            return f"{head} > {sub}"

        # If head-level errors
        if head_data.get('errors') and tail:
            errors = [e['name'] for e in head_data.get('errors', [])]
            tail_tokens = set(self._alpha.findall(tail.lower()))
            best = None
            best_sim = 0.0
            for name in errors:
                name_tokens = set(self._alpha.findall(name.lower()))
                sim = self._jaccard(tail_tokens, name_tokens)
                if sim > best_sim:
                    best = name
                    best_sim = sim
            if best and best_sim >= 0.3:
                return f"{head} > {best}"

        # If only head known
        if head in ERROR_TAXONOMY and not sub:
            return head

        return category_path

    def _jaccard(self, a: set[str], b: set[str]) -> float:
        if not a or not b:
            return 0.0
        inter = len(a & b)
        if inter == 0:
            return 0.0
        return inter / len(a | b)

    def _similarity(self, item: dict[str, Any], centroid: dict[str, set[str]]) -> float:
        char_sim = self._jaccard(item['char'], centroid['char'])
        big_sim = self._jaccard(item['bigrams'], centroid['bigrams'])
        key_sim = self._jaccard(item['keys'], centroid['keys'])
        return 0.6 * char_sim + 0.3 * big_sim + 0.1 * key_sim

    def _new_local_cluster(self, item: dict[str, Any]) -> dict[str, Any]:
        return {
            'centroid': {
                'char': set(item['char']),
                'bigrams': set(item['bigrams']),
                'keys': set(item['keys']),
            },
            'members': [item],
        }

    def _add_to_local_cluster(self, cluster: dict[str, Any], item: dict[str, Any]) -> None:
        # Update centroid as union to keep recall high for paraphrases
        cluster['centroid']['char'].update(item['char'])
        cluster['centroid']['bigrams'].update(item['bigrams'])
        cluster['centroid']['keys'].update(item['keys'])
        cluster['members'].append(item)

    def _merge_close_clusters(self, clusters: list[dict[str, Any]], merge_threshold: float) -> list[dict[str, Any]]:
        if len(clusters) <= 1:
            return clusters
        changed = True
        current = clusters
        while changed:
            changed = False
            used = [False] * len(current)
            merged: list[dict[str, Any]] = []
            for i in range(len(current)):
                if used[i]:
                    continue
                base = current[i]
                used[i] = True
                for j in range(i + 1, len(current)):
                    if used[j]:
                        continue
                    score = self._similarity({'char': current[j]['centroid']['char'],
                                              'bigrams': current[j]['centroid']['bigrams'],
                                              'keys': current[j]['centroid']['keys']},
                                             base['centroid'])
                    if score >= merge_threshold:
                        # merge j into base
                        base['centroid']['char'].update(current[j]['centroid']['char'])
                        base['centroid']['bigrams'].update(current[j]['centroid']['bigrams'])
                        base['centroid']['keys'].update(current[j]['centroid']['keys'])
                        base['members'].extend(current[j]['members'])
                        used[j] = True
                        changed = True
                merged.append(base)
            current = merged
        return current

    def _format_cluster(self, lc: dict[str, Any], cluster_id: str, category: str) -> dict[str, Any]:
        # Pick representative = first member's error
        rep_err = lc['members'][0]['err']
        # Title = first line of representative description
        desc = (rep_err.get('description') or '').strip()
        title = desc.splitlines()[0][:117] + ('...' if len(desc.splitlines()[0]) > 120 else '') if desc else ''

        error_ids: list[str] = []
        trace_ids: list[str] = []
        span_ids: list[str] = []
        descriptions: list[str] = []
        root_causes: list[str] = []
        recommendations: list[str] = []
        combined_impact = 'MEDIUM'
        order = {'HIGH': 3, 'MEDIUM': 2, 'LOW': 1}

        for m in lc['members']:
            e = m['err']
            if e.get('error_id') and e['error_id'] not in error_ids:
                error_ids.append(e['error_id'])
            tid = e.get('trace_id') or m.get('trace_id')
            if tid and tid not in trace_ids:
                trace_ids.append(tid)
            for sid in e.get('location_spans', []) or []:
                if sid not in span_ids:
                    span_ids.append(sid)
            if e.get('description'):
                descriptions.append(e['description'])
            for rc in e.get('root_causes', []) or []:
                if rc not in root_causes:
                    root_causes.append(rc)
            if e.get('recommendation') and e['recommendation'] not in recommendations:
                recommendations.append(e['recommendation'])
            imp = e.get('impact', 'MEDIUM')
            if order.get(imp, 0) > order.get(combined_impact, 0):
                combined_impact = imp

        return {
            'cluster_id': cluster_id,
            'category': category,
            'error_ids': error_ids,
            'trace_ids': trace_ids,
            'span_ids': span_ids,
            'combined_impact': combined_impact,
            'representative': rep_err.get('error_id', ''),
            'title': title,
            'descriptions': descriptions,
            'root_causes': root_causes,
            'recommendations': recommendations,
        }



def load_recent_errors_from_db(project_id: str, days: int = 30, limit: int = 1000) -> list[dict[str, Any]]:
    """Fetch recent TraceErrorDetail rows and map to clustering input shape.

    Requires Django context. Returns a list of dicts with keys similar to testing output.
    """
    try:
        db = TraceErrorAnalysisDB()
        qs = db.list_recent_error_details(project_id=project_id, days=days, limit=limit)
        errors: list[dict[str, Any]] = []
        for d in qs:
            errors.append({
                'error_id': d.error_id,
                'cluster_id': d.cluster_id,
                'category': d.category,
                'trace_id': str(getattr(d.analysis, 'trace_id', '')),
                'span_ids': d.location_spans or [],
                'location_spans': d.location_spans or [],
                'evidence_snippets': d.evidence_snippets or [],
                'description': d.description or '',
                'impact': d.impact or 'MEDIUM',
                'urgency_to_fix': d.urgency_to_fix or 'HIGH',
                'root_causes': d.root_causes or [],
                'recommendation': d.recommendation or '',
            })
        return errors
    except Exception:
        return []


class ErrorEmbeddingClusterer:
    """Core clustering algorithms and helper methods for error clustering.

    This class provides the underlying algorithms for:
    - Computing centroids and distances
    - Text normalization and embedding generation
    - Category family normalization

    The main clustering orchestration is handled by tracer.queries.error_clustering.ErrorClusteringDB
    which uses this class as a helper for the actual clustering operations.
    """

    TABLE_NAME_DEFAULT = "error_embeddings"

    def __init__(
        self,
        euclidean_threshold: float = 0.5,  # For matching to existing clusters (lower is stricter)
        top_k: int = 50,
        table_name: str = TABLE_NAME_DEFAULT,
        normalize_categories: bool = True,
        min_cluster_size: int = 2,
        min_samples: int = 1,
        metric: str = "euclidean",
        allow_soft_clustering: bool = True,
        soft_membership_threshold: float = 0.4,
    ):
        self.euclidean_threshold = euclidean_threshold  # For matching to existing clusters
        self.top_k = top_k
        self.table_name = table_name
        self.normalize_categories = normalize_categories
        self._alpha = re.compile(r"[a-z]+")
        self.db = TraceErrorAnalysisDB()
        self.min_cluster_size = min_cluster_size
        self.min_samples = min_samples
        self.metric = metric  # Always use euclidean for consistency
        self.allow_soft_clustering = allow_soft_clustering
        self.soft_membership_threshold = soft_membership_threshold

    # ----------------------------- Centroid Helpers -----------------------------
    def _compute_centroid(self, vectors: list[list[float]]) -> list[float]:
        """Compute the centroid (mean) of a list of vectors."""
        if not vectors:
            return []
        if len(vectors) == 1:
            return vectors[0]

        n = len(vectors)
        dim = len(vectors[0])
        centroid = [0.0] * dim

        for vec in vectors:
            for i, val in enumerate(vec):
                centroid[i] += val

        return [c / n for c in centroid]

    def _euclidean_distance(self, vec1: list[float], vec2: list[float]) -> float:
        """Calculate Euclidean distance between two vectors."""
        if len(vec1) != len(vec2):
            return float('inf')

        return float(sum((v1 - v2) ** 2 for v1, v2 in zip(vec1, vec2, strict=False)) ** 0.5)

    def _update_centroid_incremental(
        self,
        current_centroid: list[float],
        new_vector: list[float],
        count: int
    ) -> list[float]:
        """Update centroid incrementally when adding a new vector.

        Formula: new_centroid = (current_centroid * count + new_vector) / (count + 1)
        """
        if not current_centroid:
            return new_vector

        new_centroid = []
        for c, n in zip(current_centroid, new_vector, strict=False):
            new_val = (c * count + n) / (count + 1)
            new_centroid.append(new_val)

        return new_centroid

    def _build_embedding_text_from_detail(self, d) -> str:
        """Build text for embedding from error detail."""
        pieces: list[str] = []
        if d.category:
            pieces.append(f"Category: [{d.category}]")
        for rc in (d.root_causes or []):
            pieces.append(f"Root Cause: {str(rc)}")
        if d.recommendation:
            pieces.append(f"Recommendation: {str(d.recommendation)}")
        return ' ⟂ '.join(pieces)

    # ---------------------------- Clustering ----------------------------
    def cluster_project(
        self,
        project_id: str,
        days: int = 30,
        limit: int = 5000,
        include_singletons: bool = True,
    ) -> list[dict[str, Any]]:
        """Cluster recent errors for a project using stored embeddings (HDBSCAN per family).

        Also persists membership back into TraceErrorGroup per analysis.
        """
        try:
            import hdbscan  # type: ignore
        except Exception:
            # HDBSCAN not available
            return []

        try:
            qs = self.db.list_error_details_since(project_id=project_id, days=days, limit=limit)
        except Exception:
            return []

        # Build index of details and families
        details_by_pk: dict[str, Any] = {}
        blocks: dict[str, list[str]] = {}
        for d in qs:
            pk = str(d.id)
            details_by_pk[pk] = d
            fam = self._normalize_category_family(d.category) if self.normalize_categories else (d.category or 'Uncategorized')
            blocks.setdefault(fam, []).append(pk)

        allowed_pks = set(details_by_pk.keys())
        db = ClickHouseVectorDB()

        def _l2_normalize(vec: list[float]) -> list[float]:
            s = 0.0
            for x in vec:
                s += float(x) * float(x)
            if s <= 0:
                return vec
            inv = s ** -0.5
            return [float(x) * inv for x in vec]

        # Fetch all embeddings once for this project via eval_id filter (avoids metadata sanitization issues)
        def _fetch_project_embeddings() -> list[tuple[str, list[float], dict[str, Any]]]:
            try:
                query = f"SELECT id, vector, metadata.key AS keys, metadata.value AS values FROM {self.table_name} WHERE deleted = 0 AND eval_id = %(project_id)s"
                rows = db.client.execute(query, {"project_id": project_id})
                out: list[tuple[str, list[float], dict[str, Any]]] = []
                for rid, vec, keys, values in rows:
                    meta: dict[str, Any] = {}
                    try:
                        meta = dict(zip(keys, values, strict=False)) if keys and values else {}
                    except Exception:
                        meta = {}
                    out.append((rid, vec, meta))
                return out
            except Exception:
                return []

        all_rows = _fetch_project_embeddings()
        pk_to_vec: dict[str, list[float]] = {}
        for _rid, vec, meta in all_rows:
            meta_dict = meta or {}
            pk = meta_dict.get('error_detail_pk') if isinstance(meta_dict, dict) else None
            if pk:
                pk_to_vec[str(pk)] = vec

        clusters_out: list[dict[str, Any]] = []

        for family, family_pks in blocks.items():

            # Collect vectors for this family's allowed pks (from our time window)
            vectors: list[list[float]] = []
            pks: list[str] = []
            for pk in family_pks:
                if pk not in allowed_pks:
                    continue
                vec = pk_to_vec.get(pk)
                if not vec or vec is None:
                    continue
                normalized_vec = _l2_normalize(vec)
                if normalized_vec:
                    vectors.append(normalized_vec)
                    pks.append(pk)


            if len(vectors) < 2:
                # Not enough to form a cluster; treat singles as noise (skip)
                # If singleton exists, emit as singleton cluster when allowed
                if include_singletons and len(pks) == 1:
                    d = details_by_pk.get(pks[0])
                    if d:
                        cluster_id = self._stable_cluster_id([pks[0]], family)
                        summary = self._format_cluster_summary(cluster_id, family, [d])
                        clusters_out.append(summary)
                continue

            try:
                clusterer = hdbscan.HDBSCAN(
                    min_cluster_size=self.min_cluster_size,
                    min_samples=self.min_samples,
                    metric=self.metric,
                    prediction_data=True,  # Required for soft clustering
                )
                labels = clusterer.fit_predict(vectors)
            except Exception:
                continue

            # Group core members and noise by label
            label_to_core_members: dict[int, list[str]] = {}
            noise_pks: list[str] = []
            for pk, lab in zip(pks, labels, strict=False):
                if lab == -1:
                    noise_pks.append(pk)
                    continue
                label_to_core_members.setdefault(int(lab), []).append(pk)

            # This dict will be populated with core members and then expanded with soft-assigned noise
            final_label_to_members = label_to_core_members.copy()
            unassigned_noise_pks = list(noise_pks)

            # Attempt to assign noise points to existing clusters via soft clustering
            if self.allow_soft_clustering and noise_pks:
                try:
                    membership_vectors = hdbscan.all_points_membership_vectors(clusterer)
                    pk_to_idx = {pk: i for i, pk in enumerate(pks)}

                    for pk in noise_pks:
                        idx = pk_to_idx.get(pk)
                        if idx is None:
                            continue

                        probs = membership_vectors[idx]
                        if not any(probs):
                            continue

                        # Find the best cluster for this noise point
                        best_label = -1
                        max_prob = -1.0
                        for i, p in enumerate(probs):
                            if p > max_prob:
                                max_prob = p
                                best_label = i

                        # If it meets the threshold and the target cluster exists, assign it
                        if max_prob >= self.soft_membership_threshold and best_label in final_label_to_members:
                            final_label_to_members[best_label].append(pk)
                            unassigned_noise_pks.remove(pk)
                except Exception:
                    # Soft clustering can fail, proceed without it if it does
                    pass

            # Emit final clusters (core members + softly assigned noise)
            for member_pks in final_label_to_members.values():
                if not member_pks:
                    continue
                members = [details_by_pk[pk] for pk in member_pks if pk in details_by_pk]
                if not members:
                    continue
                cluster_id = self._stable_cluster_id(member_pks, family)
                summary = self._format_cluster_summary(cluster_id, family, members)
                clusters_out.append(summary)

            # Optionally promote any remaining (unassigned) noise to singleton clusters
            if include_singletons and unassigned_noise_pks:
                for pk in unassigned_noise_pks:
                    d = details_by_pk.get(pk)
                    if not d:
                        continue
                    cluster_id = self._stable_cluster_id([pk], family)
                    summary = self._format_cluster_summary(cluster_id, family, [d])
                    clusters_out.append(summary)

        db.close()

        # Persist to TraceErrorGroup per analysis
        self._persist_trace_error_groups(clusters_out)
        return clusters_out

    # --------------------------- Persistence ----------------------------
    def _persist_trace_error_groups(self, clusters: list[dict[str, Any]]) -> None:
        # Group per analysis id
        for c in clusters:
            cluster_id = c['cluster_id']
            error_type = c['category']
            # members_by_analysis: analysis_id -> list of detail dicts
            by_analysis: dict[str, list[dict[str, Any]]] = {}
            for m in c['members']:
                aid = m['analysis_id']
                by_analysis.setdefault(aid, []).append(m)

            for analysis_id, details in by_analysis.items():
                error_ids = [d['error_id'] for d in details if d.get('error_id')]
                affected_spans = []
                for d in details:
                    for sid in d.get('location_spans', []) or []:
                        if sid not in affected_spans:
                            affected_spans.append(sid)

                combined_impact = 'MEDIUM'
                order = {'HIGH': 3, 'MEDIUM': 2, 'LOW': 1}
                for d in details:
                    imp = d.get('impact', 'MEDIUM')
                    if order.get(imp, 0) > order.get(combined_impact, 0):
                        combined_impact = imp

                combined_description = c.get('title') or ''
                error_count = len(details)
                trace_impact = None

                # Upsert by (analysis_id, cluster_id)
                try:
                    self.db.upsert_trace_error_group(
                        analysis_id=analysis_id,
                        cluster_id=cluster_id,
                        error_type=error_type,
                        error_ids=error_ids,
                        combined_impact=combined_impact,
                        combined_description=combined_description,
                        error_count=error_count,
                        trace_impact=trace_impact,
                    )
                except Exception:
                    continue

    # ----------------------------- Helpers ------------------------------
    def _format_cluster_summary(self, cluster_id: str, family: str, members: list[Any]) -> dict[str, Any]:
        # Representative: first member
        rep = members[0]
        desc = (rep.description or '').strip()
        title = desc.splitlines()[0][:117] + ('...' if len(desc.splitlines()[0]) > 120 else '') if desc else ''

        error_ids: list[str] = []
        trace_ids: list[str] = []
        span_ids: list[str] = []
        root_causes: list[str] = []
        recommendations: list[str] = []
        combined_impact = 'MEDIUM'
        order = {'HIGH': 3, 'MEDIUM': 2, 'LOW': 1}

        members_payload: list[dict[str, Any]] = []

        for d in members:
            if d.error_id and d.error_id not in error_ids:
                error_ids.append(d.error_id)
            if getattr(d.analysis, 'trace', None):
                tid = str(getattr(d.analysis.trace, 'id', ''))
                if tid and tid not in trace_ids:
                    trace_ids.append(tid)
            for sid in (d.location_spans or []):
                if sid not in span_ids:
                    span_ids.append(sid)
            for rc in (d.root_causes or []):
                if rc not in root_causes:
                    root_causes.append(rc)
            if d.recommendation and d.recommendation not in recommendations:
                recommendations.append(d.recommendation)
            imp = d.impact or 'MEDIUM'
            if order.get(imp, 0) > order.get(combined_impact, 0):
                combined_impact = imp

            members_payload.append({
                'detail_pk': str(d.id),
                'error_id': d.error_id,
                'analysis_id': str(getattr(d.analysis, 'id', '')),
                'trace_id': str(getattr(d.analysis.trace, 'id', '')),
                'location_spans': d.location_spans or [],
                'impact': d.impact or 'MEDIUM',
            })

        return {
            'cluster_id': cluster_id,
            'category': family,
            'error_ids': error_ids,
            'trace_ids': trace_ids,
            'span_ids': span_ids,
            'combined_impact': combined_impact,
            'representative': str(getattr(members[0], 'id', '')),
            'title': title,
            'root_causes': root_causes,
            'recommendations': recommendations,
            'members': members_payload,
        }

    def _stable_cluster_id(self, member_pks: Iterable[str], family: str) -> str:
        base = family + '|' + '|'.join(sorted(member_pks))
        h = hashlib.md5(base.encode()).hexdigest()[:8]
        return f"K{h.upper()}"

    def _normalize_category_family(self, category_path: str) -> str:
        parts = [p.strip() for p in (category_path or '').split('>') if p.strip()]
        if not parts:
            return 'Uncategorized'

        head = parts[0]
        sub = parts[1] if len(parts) > 1 else ''
        tail = parts[2] if len(parts) > 2 else ''

        if head not in ERROR_TAXONOMY:
            return category_path or 'Uncategorized'

        head_data = ERROR_TAXONOMY[head]
        sub_map = head_data.get('subcategories', {})

        if sub and sub in sub_map:
            errors = [e['name'] for e in sub_map[sub].get('errors', [])]
            if not errors:
                return f"{head} > {sub}"
            if not tail:
                return f"{head} > {sub}"
            tail_tokens = set(self._alpha.findall(tail.lower()))
            best = None
            best_sim = 0.0
            for name in errors:
                name_tokens = set(self._alpha.findall(name.lower()))
                sim = self._jaccard(tail_tokens, name_tokens)
                if sim > best_sim:
                    best = name
                    best_sim = sim
            if best and best_sim >= 0.3:
                return f"{head} > {sub} > {best}"
            return f"{head} > {sub}"

        if head_data.get('errors') and tail:
            errors = [e['name'] for e in head_data.get('errors', [])]
            tail_tokens = set(self._alpha.findall(tail.lower()))
            best = None
            best_sim = 0.0
            for name in errors:
                name_tokens = set(self._alpha.findall(name.lower()))
                sim = self._jaccard(tail_tokens, name_tokens)
                if sim > best_sim:
                    best = name
                    best_sim = sim
            if best and best_sim >= 0.3:
                return f"{head} > {best}"

        if head in ERROR_TAXONOMY and not sub:
            return head

        return category_path or 'Uncategorized'

    def _jaccard(self, a: set[str], b: set[str]) -> float:
        if not a or not b:
            return 0.0
        inter = len(a & b)
        if inter == 0:
            return 0.0
        return inter / len(a | b)
