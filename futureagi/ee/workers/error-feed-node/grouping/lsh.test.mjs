import test from 'node:test';
import assert from 'node:assert/strict';
import {join, resolve} from 'node:path';
import {pathToFileURL} from 'node:url';
import {featureDigest} from './features.mjs';
import {
  bucketRowsForFeature,
  cannotLinkKey,
  createF6Planes,
  F6ViewIndex,
  probeBucketKeys,
  rankCandidates,
} from './lsh.mjs';

// Fixed mock vectors make this an independent oracle even when the sealed F6
// source is not present in a worker checkout or CI image.
const ORACLE_VECTORS = Object.freeze({
  a: {semantics: [1, 0, 0, 0], task: [0, 1, 0, 0]},
  b: {semantics: [0.9, 0.1, 0, 0], task: [0, 0.9, 0.1, 0]},
  c: {semantics: [0.8, 0.2, 0, 0], task: [0, 0.8, 0.2, 0]},
  d: {semantics: [0, 0, 1, 0], task: [1, 0, 0, 0]},
  blocked: {semantics: [1, 0, 0, 0], task: [0, 1, 0, 0]},
  control: {semantics: [1, 0, 0, 0], task: [0, 1, 0, 0]},
  outside: {semantics: [1, 0, 0, 0], task: [0, 1, 0, 0]},
});

function fixtures() {
  const rows = Object.keys(ORACLE_VECTORS).map(id => ({
    id,
    organization_id: 'org',
    project_id: id === 'outside' ? 'other-project' : 'project',
    control: id === 'control',
    source_digest: `source-${id}`,
    evidence_revision: `revision-${id}`,
    views: {semantics: `statement-${id}`, task: `task-${id}`},
  }));
  const features = {version: 'oracle/v1', model: 'mock', dimension: 4, rows: {}};
  for (const row of rows) {
    features.rows[row.id] = {
      source_digest: row.source_digest,
      evidence_revision: row.evidence_revision,
      views: Object.fromEntries(Object.entries(ORACLE_VECTORS[row.id]).map(([view, vector]) => [view, {
        vector,
        text_digest: featureDigest(row.views[view]),
        chunks: [],
      }])),
    };
  }
  return {rows, features};
}

test('seeded planes retain the sealed F6 bucket oracle and exact plus one-bit probes', () => {
  const {rows} = fixtures();
  const planes = createF6Planes(4);
  const buckets = bucketRowsForFeature(rows[0], 'semantics', ORACLE_VECTORS.a.semantics, planes);
  assert.deepEqual(buckets.map(bucket => bucket.signature), [111, 109, 196, 234, 155, 102, 255, 89]);
  assert.equal(buckets.length, 8);
  const probes = probeBucketKeys(rows[0], 'semantics', ORACLE_VECTORS.a.semantics, planes);
  assert.equal(probes.length, 8 * 9);
  assert.equal(new Set(probes.map(probe => probe.bucket_key)).size, probes.length);
  assert.ok(probes.some(probe => probe.table === 0 && probe.signature === (111 ^ 1)));
});

test('in-memory LSH applies hard scope, control and cannot-link filters before F6 ranking', () => {
  const {rows, features} = fixtures();
  const index = new F6ViewIndex(rows, features);
  assert.deepEqual(index.neighbours('a').map(item => item.id), ['blocked', 'b', 'c']);
  assert.deepEqual(index.neighbours('a', new Set([cannotLinkKey('a', 'blocked')])).map(item => item.id), ['b', 'c']);
  assert.deepEqual(index.neighbours('a', new Set(), {includeControls: true}).map(item => item.id), ['control']);
  assert.ok(!index.neighbours('a').some(item => ['outside', 'control'].includes(item.id)));
  assert.deepEqual(index.neighbours('a')[0], {
    id: 'blocked',
    rank_score: 2 / 61,
    views: {semantics: 1, task: 1},
  });
});

test('pure candidate ranker caps each view at 20 and breaks equal scores by ID', () => {
  const query = {
    id: 'query', organization_id: 'org', project_id: 'project', control: false,
  };
  const candidates = Array.from({length: 22}, (_, index) => ({
    id: `candidate-${String(index).padStart(2, '0')}`,
    organization_id: 'org', project_id: 'project', control: false,
  }));
  const rowsById = new Map([query, ...candidates].map(row => [row.id, row]));
  const features = {dimension: 2, rows: {
    query: {views: {semantics: {vector: [1, 0]}}},
    ...Object.fromEntries(candidates.map(row => [row.id, {views: {semantics: {vector: [1, 0]}}}])),
  }};
  const ranked = rankCandidates({
    queryId: 'query', rowsById, features,
    candidateIdsByView: {semantics: candidates.map(row => row.id).reverse()},
  });
  assert.equal(ranked.length, 20);
  assert.deepEqual(ranked.map(item => item.id), candidates.slice(0, 20).map(row => row.id));
  assert.equal(ranked[0].rank_score, 1 / 61);
  assert.equal(ranked.at(-1).rank_score, 1 / 80);
});

const historicalDirectory = process.env.F6_HISTORICAL_DIR;
test('same mock vectors match the sealed F6 ViewIndex', {skip: !historicalDirectory}, async () => {
  const retrievalUrl = pathToFileURL(join(resolve(historicalDirectory), 'retrieval.mjs')).href;
  const {ViewIndex} = await import(retrievalUrl);
  const {rows, features} = fixtures();
  const policy = {
    embedding_model: 'mock', embedding_dimension: 4,
    index_tables: 8, index_bits: 8, index_mode: 'lsh',
    neighbours_per_view: 20, hybrid_retrieval: false,
  };
  const historical = new ViewIndex(rows, features, policy);
  const production = new F6ViewIndex(rows, features);
  const constraints = new Set([cannotLinkKey('a', 'blocked')]);
  assert.deepEqual(production.neighbours('a', constraints), historical.neighbours('a', constraints));
  assert.deepEqual(production.neighbours('a', new Set(), {includeControls: true}),
    historical.neighbours('a', new Set(), {includeControls: true}));
});
