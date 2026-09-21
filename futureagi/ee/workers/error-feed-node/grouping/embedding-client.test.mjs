import test from 'node:test';
import assert from 'node:assert/strict';
import {
  createEmbeddingClient,
  EmbeddingInputTooLongError,
  GROUPING_EMBEDDING_CAPABILITY,
  GROUPING_EMBEDDING_MODEL,
} from './embedding-client.mjs';

const model = Object.freeze({
  capability: GROUPING_EMBEDDING_CAPABILITY,
  name: GROUPING_EMBEDDING_MODEL,
  revision: 'a'.repeat(40),
  dimension: 384,
  maxSequenceLength: 256,
});

function responseFor(texts, overrides = {}) {
  return new Response(JSON.stringify({
    embeddings: texts.map((_text, index) => Array.from({length: 384}, () => index + 1)),
    model_name: 'text_embedding',
    input_type: 'text',
    ...overrides,
  }), {status: 200, headers: {'Content-Type': 'application/json'}});
}

function client(fetchImpl, overrides = {}) {
  return createEmbeddingClient({
    endpoint: 'http://serving:8080/model/v1/embed',
    model,
    timeoutMs: 1000,
    maxRequestBytes: 4096,
    maxResponseBytes: 2 * 1024 * 1024,
    maxBatchSize: 8,
    fetchImpl,
    ...overrides,
  });
}

test('client uses unchanged serving API without claiming identity or token coverage', async () => {
  let request;
  const embed = client(async (url, options) => {
    request = {url: String(url), options};
    return responseFor(['first input', 'second input']);
  });
  const result = await embed(['first input', 'second input']);
  assert.equal(result.vectors.length, 2);
  assert.equal(result.tokenCounts, null);
  assert.equal(result.model, null);
  assert.equal(result.coverageVerified, false);
  assert.equal(request.url, 'http://serving:8080/model/v1/embed');
  assert.deepEqual(JSON.parse(request.options.body), {
    text: ['first input', 'second input'],
    input_type: 'text',
  });
});

test('strict client fails closed on token overflow without returning content', async () => {
  const embed = client(async () => new Response(JSON.stringify({
    detail: {code: 'embedding_input_too_long', input_index: 0, token_count: 300},
  }), {status: 422}));
  await assert.rejects(() => embed(['over limit']), EmbeddingInputTooLongError);
});

test('client rejects wrong envelope, dimension, count and non-finite vectors', async () => {
  await assert.rejects(() => client(async () => responseFor(['x'], {
    model_name: 'other',
  }))(['x']), /response envelope/);
  await assert.rejects(() => client(async () => responseFor(['x'], {
    embeddings: [[1.0, 2.0]],
  }))(['x']), /Invalid embedding vector/);
  await assert.rejects(() => client(async () => responseFor(['x'], {
    embeddings: [],
  }))(['x']), /count mismatch/);
  const embed = client(async () => {
    const response = await responseFor(['x']).json();
    response.embeddings[0][10] = Number.NaN;
    return new Response(JSON.stringify(response), {status: 200});
  });
  await assert.rejects(() => embed(['x']), /Invalid embedding/);
});

test('batch and byte limits reject locally before transport', async () => {
  let calls = 0;
  const embed = client(async () => { calls++; return responseFor(['x']); }, {
    maxRequestBytes: 256,
    maxBatchSize: 1,
  });
  await assert.rejects(() => embed(['a', 'b']), /batch/);
  await assert.rejects(() => embed(['x'.repeat(512)]), /byte limit/);
  assert.equal(calls, 0);
});

test('configuration requires the approved model and immutable revision', () => {
  assert.throws(() => createEmbeddingClient({
    endpoint: 'http://serving:8080/model/v1/embed',
    model: {...model, revision: 'latest'},
    timeoutMs: 1000,
    maxRequestBytes: 1024,
    maxResponseBytes: 1024,
    maxBatchSize: 1,
  }), /mutable/);
});
