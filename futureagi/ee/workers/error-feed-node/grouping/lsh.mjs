import {featureDigest} from './features.mjs';

export const F6_LSH_POLICY = Object.freeze({tables: 8, bits: 8, neighboursPerView: 20, rrfK: 60});
export const F6_LSH_VERSION = 'f6-random-hyperplane/v1';

function validateDimension(dimension) {
  if (!Number.isSafeInteger(dimension) || dimension < 1 || dimension > 4096) {
    throw new Error('Invalid LSH dimension');
  }
}

function validateVector(vector, dimension) {
  if (!Array.isArray(vector) || vector.length !== dimension
      || vector.some(value => !Number.isFinite(value))
      || Math.hypot(...vector) === 0) {
    throw new Error('Invalid LSH vector');
  }
}

// Ported from the sealed F6 retrieval.mjs random-hyperplane implementation.
// Changing this generator changes every persisted bucket key.
function plane(table, bit, dimension) {
  let seed = Number.parseInt(featureDigest([table, bit]).slice(0, 8), 16) || 1;
  return Array.from({length: dimension}, () => {
    seed ^= seed << 13;
    seed ^= seed >>> 17;
    seed ^= seed << 5;
    return ((seed >>> 0) / 4294967296) * 2 - 1;
  });
}

export function createF6Planes(dimension) {
  validateDimension(dimension);
  return Array.from({length: F6_LSH_POLICY.tables}, (_, table) =>
    Array.from({length: F6_LSH_POLICY.bits}, (_, bit) => plane(table, bit, dimension)));
}

function validatePlanes(planes, dimension) {
  if (!Array.isArray(planes) || planes.length !== F6_LSH_POLICY.tables
      || planes.some(table => !Array.isArray(table) || table.length !== F6_LSH_POLICY.bits
        || table.some(item => !Array.isArray(item) || item.length !== dimension
          || item.some(value => !Number.isFinite(value))))) {
    throw new Error('Invalid F6 LSH planes');
  }
}

export function signatureForVector(vector, planes, table) {
  validateVector(vector, planes?.[0]?.[0]?.length);
  validatePlanes(planes, vector.length);
  if (!Number.isSafeInteger(table) || table < 0 || table >= F6_LSH_POLICY.tables) {
    throw new Error('Invalid LSH table');
  }
  return planes[table].reduce((signature, currentPlane, bit) => {
    const dot = vector.reduce((sum, value, index) => sum + value * currentPlane[index], 0);
    return signature | ((dot >= 0 ? 1 : 0) << bit);
  }, 0);
}

export function scopeKey(row) {
  if (typeof row?.organization_id !== 'string' || !row.organization_id
      || typeof row.project_id !== 'string' || !row.project_id) {
    throw new Error('Invalid LSH row scope');
  }
  return JSON.stringify([row.organization_id, row.project_id]);
}

function bucketKey(row, view, table, signature) {
  return JSON.stringify([scopeKey(row), view, table, signature]);
}

export function bucketRowsForFeature(row, view, vector, planes) {
  if (typeof row?.id !== 'string' || !row.id || typeof row.source_digest !== 'string'
      || !row.source_digest || typeof row.evidence_revision !== 'string' || !row.evidence_revision
      || typeof view !== 'string' || !view) {
    throw new Error('Invalid bucket row identity');
  }
  validateVector(vector, planes?.[0]?.[0]?.length);
  validatePlanes(planes, vector.length);
  const vectorDigest = featureDigest(vector);
  return planes.map((_table, table) => {
    const signature = signatureForVector(vector, planes, table);
    return {
      bucket_key: bucketKey(row, view, table, signature),
      index_version: F6_LSH_VERSION,
      scope_key: scopeKey(row),
      finding_id: row.id,
      source_digest: row.source_digest,
      evidence_revision: row.evidence_revision,
      vector_digest: vectorDigest,
      view,
      table,
      signature,
    };
  });
}

export function probeBucketKeys(row, view, vector, planes) {
  validateVector(vector, planes?.[0]?.[0]?.length);
  validatePlanes(planes, vector.length);
  return planes.flatMap((_table, table) => {
    const exact = signatureForVector(vector, planes, table);
    return [exact, ...Array.from({length: F6_LSH_POLICY.bits}, (_, bit) => exact ^ (1 << bit))]
      .map(signature => ({
        bucket_key: bucketKey(row, view, table, signature),
        scope_key: scopeKey(row),
        view,
        table,
        signature,
      }));
  });
}

export function cannotLinkKey(first, second) {
  return JSON.stringify([first, second].sort());
}

function cosine(first, second) {
  if (first.length !== second.length || !first.length) throw new Error('Vector dimensions differ');
  const dot = first.reduce((sum, value, index) => sum + value * second[index], 0);
  const norm = Math.hypot(...first) * Math.hypot(...second);
  return norm ? Math.max(-1, Math.min(1, dot / norm)) : 0;
}

function sameScope(first, second) {
  return scopeKey(first) === scopeKey(second);
}

function idsForView(candidateIdsByView, view) {
  const ids = candidateIdsByView instanceof Map
    ? candidateIdsByView.get(view)
    : candidateIdsByView?.[view];
  if (ids === undefined) return [];
  if (!Array.isArray(ids) && !(ids instanceof Set)) throw new Error('Invalid LSH candidate set');
  return [...ids];
}

// Candidate lookup is deliberately separate from ranking so a later ClickHouse
// reader can supply the same bucket union without changing F6 ranking semantics.
export function rankCandidates({queryId, rowsById, features, candidateIdsByView,
  constraints = new Set(), includeControls = false}) {
  const query = rowsById.get(queryId);
  const queryFeatures = features?.rows?.[queryId];
  if (!query || !queryFeatures || !(constraints instanceof Set)) {
    throw new Error('Invalid LSH ranking input');
  }
  const ranks = new Map();
  for (const [view, entry] of Object.entries(queryFeatures.views)) {
    validateVector(entry.vector, features.dimension);
    const candidates = [...new Set(idsForView(candidateIdsByView, view))]
      .filter(candidateId => {
        const candidate = rowsById.get(candidateId);
        return candidateId !== queryId && candidate && sameScope(query, candidate)
          && !constraints.has(cannotLinkKey(queryId, candidateId))
          && (includeControls ? candidate.control === true : candidate.control !== true)
          && features.rows[candidateId]?.views?.[view];
      })
      .map(candidateId => {
        const vector = features.rows[candidateId].views[view].vector;
        validateVector(vector, features.dimension);
        return {id: candidateId, similarity: cosine(entry.vector, vector)};
      })
      .sort((first, second) => second.similarity - first.similarity
        || first.id.localeCompare(second.id))
      .slice(0, F6_LSH_POLICY.neighboursPerView);
    candidates.forEach((candidate, rank) => {
      const current = ranks.get(candidate.id) ?? {id: candidate.id, rank_score: 0, views: {}};
      current.rank_score += 1 / (F6_LSH_POLICY.rrfK + rank + 1);
      current.views[view] = candidate.similarity;
      ranks.set(candidate.id, current);
    });
  }
  return [...ranks.values()].sort((first, second) => second.rank_score - first.rank_score
    || first.id.localeCompare(second.id));
}

export class F6ViewIndex {
  constructor(rows, features) {
    if (!Array.isArray(rows) || !features?.rows) throw new Error('Invalid F6 index input');
    validateDimension(features.dimension);
    this.rows = new Map();
    this.features = features;
    this.planes = createF6Planes(features.dimension);
    this.buckets = new Map();
    for (const row of rows) {
      if (this.rows.has(row.id)) throw new Error('Duplicate F6 index row');
      this.rows.set(row.id, row);
      const rowFeatures = features.rows[row.id];
      if (!rowFeatures || rowFeatures.source_digest !== row.source_digest
          || rowFeatures.evidence_revision !== row.evidence_revision) {
        throw new Error('Stale feature index');
      }
      for (const [view, entry] of Object.entries(rowFeatures.views)) {
        if (entry.text_digest !== featureDigest(row.views[view])) throw new Error('Stale feature view');
        validateVector(entry.vector, features.dimension);
        for (const bucket of bucketRowsForFeature(row, view, entry.vector, this.planes)) {
          const ids = this.buckets.get(bucket.bucket_key) ?? [];
          ids.push(row.id);
          this.buckets.set(bucket.bucket_key, ids);
        }
      }
    }
  }

  neighbours(id, constraints = new Set(), options = {}) {
    const row = this.rows.get(id);
    const rowFeatures = this.features.rows[id];
    if (!row || !rowFeatures) throw new Error('Unknown F6 index row');
    const candidateIdsByView = new Map();
    for (const [view, entry] of Object.entries(rowFeatures.views)) {
      const found = new Set();
      for (const probe of probeBucketKeys(row, view, entry.vector, this.planes)) {
        for (const candidateId of this.buckets.get(probe.bucket_key) ?? []) found.add(candidateId);
      }
      candidateIdsByView.set(view, found);
    }
    return rankCandidates({
      queryId: id,
      rowsById: this.rows,
      features: this.features,
      candidateIdsByView,
      constraints,
      includeControls: options.includeControls === true,
    });
  }

  similarity(firstId, secondId) {
    const first = this.features.rows[firstId]?.views;
    const second = this.features.rows[secondId]?.views;
    if (!first || !second) throw new Error('Unknown F6 similarity row');
    if (first.semantics && second.semantics) return cosine(first.semantics.vector, second.semantics.vector);
    const similarities = Object.keys(first).flatMap(view => second[view]
      ? [cosine(first[view].vector, second[view].vector)] : []);
    return similarities.length ? Math.max(...similarities) : -1;
  }
}
