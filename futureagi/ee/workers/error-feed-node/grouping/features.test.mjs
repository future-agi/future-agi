import test from 'node:test';
import assert from 'node:assert/strict';
import {EmbeddingInputTooLongError} from './embedding-client.mjs';
import {buildFeatures, featureDigest} from './features.mjs';
import {adaptGroupingSnapshot} from './snapshot.mjs';
import {makeGroupingSnapshotFixture} from './snapshot-fixture.mjs';

const model = Object.freeze({
  capability: 'grouping-features/v1',
  name: 'all-MiniLM-L6-v2',
  revision: 'c'.repeat(40),
  dimension: 384,
  maxSequenceLength: 256,
});

test('unverified serving output cannot be cached as complete features', async () => {
  const cache = new Map();
  await assert.rejects(() => buildFeatures([row()], {
    model, cache,
    embedBatch: async () => ({vectors: [vector(0)], model: null,
      tokenCounts: null, coverageVerified: false}),
  }), /integration is pending/);
  assert.equal(cache.size, 0);
});

function vector(axis) {
  return Array.from({length: model.dimension}, (_, index) => index === axis ? 1 : 0);
}

function row(overrides = {}) {
  return {
    id: 'finding-1',
    source_digest: 'source-a',
    evidence_revision: 'evidence-a',
    views: {semantics: 'statement text', task: 'task requirement'},
    ...overrides,
  };
}

function mockEmbed(calls) {
  return async texts => {
    calls.push(texts);
    return {
      vectors: texts.map((_text, index) => vector(index)),
      tokenCounts: texts.map(text => text.trim().split(/\s+/).length),
      model,
    };
  };
}

test('feature builder embeds statement/task with complete strict-serving coverage', async () => {
  const calls = [];
  const result = await buildFeatures([row()], {embedBatch: mockEmbed(calls), model, cache: new Map()});
  assert.equal(calls.length, 1);
  assert.deepEqual(new Set(calls[0]), new Set(['statement text', 'task requirement']));
  assert.equal(result.model_revision, model.revision);
  assert.equal(result.dimension, 384);
  const statement = result.rows['finding-1'].views.semantics;
  assert.deepEqual(statement.coverage, {
    unit: 'utf16_code_units', start: 0, end: 14, complete: true, proof: 'strict-serving',
  });
  assert.deepEqual(statement.chunks, [{
    start: 0, end: 14, text_digest: featureDigest('statement text'), token_count: 2,
  }]);
});

test('normalized grouping snapshot rows feed the shared feature builder unchanged', async () => {
  const snapshot = makeGroupingSnapshotFixture();
  const rows = adaptGroupingSnapshot(snapshot);
  const calls = [];
  const result = await buildFeatures(rows, {embedBatch: mockEmbed(calls), model, cache: new Map()});
  const occurrenceId = snapshot.occurrences[0].occurrence_id;
  assert.deepEqual(calls, [[
    snapshot.report.requirement_checks[0].requirement,
    snapshot.report.findings[0].statement,
  ]]);
  assert.equal(result.rows[occurrenceId].source_digest, snapshot.snapshot_digest);
  assert.equal(result.rows[occurrenceId].evidence_revision, snapshot.report.evidence_digest);
  assert.equal(result.rows[occurrenceId].views.semantics.text_digest,
    featureDigest(snapshot.report.findings[0].statement));
  assert.equal(result.rows[occurrenceId].views.task.text_digest,
    featureDigest(snapshot.report.requirement_checks[0].requirement));
});

test('cache binding includes source, evidence, view, text and model configuration', async () => {
  const cache = new Map();
  const calls = [];
  const first = await buildFeatures([row()], {embedBatch: mockEmbed(calls), model, cache});
  const second = await buildFeatures([row()], {
    embedBatch: async () => { throw new Error('cache miss'); }, model, cache,
  });
  assert.deepEqual(second, first);
  assert.equal(cache.size, 2);

  const changedCalls = [];
  await buildFeatures([row({source_digest: 'source-b'})], {
    embedBatch: mockEmbed(changedCalls), model, cache,
  });
  assert.equal(changedCalls.length, 1);
  assert.equal(cache.size, 4);
});

test('token-aware chunker must prove exact contiguous source coverage and binding', async () => {
  const calls = [];
  const chunkerRevision = `sha256:${'e'.repeat(64)}`;
  const chunker = async text => ({
    binding: {
      capability: model.capability,
      model: model.name,
      model_revision: model.revision,
      max_seq_length: model.maxSequenceLength,
      chunker_revision: chunkerRevision,
    },
    chunks: [
      {start: 0, end: 6, text: text.slice(0, 6), token_count: 1},
      {start: 6, end: 10, text: text.slice(6), token_count: 1},
    ],
  });
  chunker.policy = {
    capability: 'token-aware-chunker/v1',
    revision: chunkerRevision,
    tokenizer_revision: model.revision,
  };
  const result = await buildFeatures([row({views: {semantics: 'alpha beta'}})], {
    embedBatch: mockEmbed(calls),
    model,
    cache: new Map(),
    chunker,
  });
  assert.deepEqual(calls[0], ['alpha ', 'beta']);
  assert.equal(result.rows['finding-1'].views.semantics.coverage.proof,
    'token-aware-chunker+strict-serving');
  assert.equal(result.rows['finding-1'].views.semantics.coverage.end, 10);
  assert.deepEqual(result.chunking_policy, chunker.policy);

  let modelCalls = 0;
  const invalidChunker = async () => ({
    binding: {
      capability: model.capability, model: model.name,
      model_revision: model.revision, max_seq_length: model.maxSequenceLength,
      chunker_revision: chunkerRevision,
    },
    chunks: [{start: 1, end: 10, text: 'lpha beta', token_count: 2}],
  });
  invalidChunker.policy = chunker.policy;
  await assert.rejects(() => buildFeatures([row({views: {semantics: 'alpha beta'}})], {
    embedBatch: async () => { modelCalls++; },
    model,
    cache: new Map(),
    chunker: invalidChunker,
  }), /coverage/);
  assert.equal(modelCalls, 0);
});

test('without a tokenizer-aware chunker an over-limit source fails closed', async () => {
  let calls = 0;
  await assert.rejects(() => buildFeatures([row({views: {semantics: 'long source'}})], {
    model,
    cache: new Map(),
    embedBatch: async texts => {
      calls++;
      assert.deepEqual(texts, ['long source']);
      throw new EmbeddingInputTooLongError();
    },
  }), EmbeddingInputTooLongError);
  assert.equal(calls, 1);
});

test('feature source byte bounds reject locally before embedding', async () => {
  let calls = 0;
  await assert.rejects(() => buildFeatures([row({
    views: {semantics: 'x'.repeat(256 * 1024 + 1)},
  })], {
    model,
    cache: new Map(),
    embedBatch: async () => { calls++; },
  }), /byte limit/);
  assert.equal(calls, 0);
});

test('feature builder rejects stale cache entries and unbound vectors', async () => {
  const cache = new Map();
  await buildFeatures([row({views: {semantics: 'text'}})], {
    embedBatch: mockEmbed([]), model, cache,
  });
  const [key, entry] = cache.entries().next().value;
  entry.feature.source_digest = 'wrong';
  cache.set(key, entry);
  await assert.rejects(() => buildFeatures([row({views: {semantics: 'text'}})], {
    embedBatch: mockEmbed([]), model, cache,
  }), /Stale/);

  await assert.rejects(() => buildFeatures([row({views: {semantics: 'other'}})], {
    model,
    cache: new Map(),
    embedBatch: async () => ({vectors: [vector(0)], tokenCounts: [1], model: {...model, revision: 'd'.repeat(40)}}),
  }), /unbound/);
});
