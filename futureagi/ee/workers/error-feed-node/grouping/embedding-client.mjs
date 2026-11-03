const MODEL = 'all-MiniLM-L6-v2';

export class EmbeddingInputTooLongError extends Error {
  constructor() {
    super('Embedding input exceeds the serving input limit');
    this.name = 'EmbeddingInputTooLongError';
    this.code = 'embedding_input_too_long';
  }
}

function positiveInteger(value, name, ceiling) {
  if (!Number.isSafeInteger(value) || value < 1 || value > ceiling) {
    throw new Error(`Invalid ${name}`);
  }
  return value;
}

export function validateEmbeddingModel(model) {
  const keys = model && typeof model === 'object' && !Array.isArray(model)
    ? Object.keys(model).sort() : [];
  if (JSON.stringify(keys) !== JSON.stringify([
    'dimension', 'name', 'servingRelease',
  ]) || model.name !== MODEL || model.dimension !== 384
      || !/^[a-zA-Z0-9_.:-]{1,128}$/.test(model.servingRelease ?? '')) {
    throw new Error('Unsupported model or missing serving cache release');
  }
  positiveInteger(model.dimension, 'embedding dimension', 4096);
  return Object.freeze({...model});
}

async function readBoundedJson(response, maxBytes) {
  if (!response.body) throw new Error('Embedding response has no body');
  const chunks = [];
  let bytes = 0;
  for await (const chunk of response.body) {
    const data = Buffer.from(chunk);
    bytes += data.length;
    if (bytes > maxBytes) {
      throw new Error('Embedding response exceeded byte limit');
    }
    chunks.push(data);
  }
  try {
    return JSON.parse(Buffer.concat(chunks).toString('utf8'));
  } catch {
    throw new Error('Embedding response is not valid JSON');
  }
}

function validateVectors(vectors, count, dimension) {
  if (!Array.isArray(vectors) || vectors.length !== count) {
    throw new Error('Embedding result count mismatch');
  }
  for (const vector of vectors) {
    if (!Array.isArray(vector) || vector.length !== dimension
        || vector.some(value => !Number.isFinite(value))
        || Math.hypot(...vector) === 0) {
      throw new Error('Invalid embedding vector');
    }
  }
}

export function createEmbeddingClient({endpoint, model: rawModel, timeoutMs,
  maxRequestBytes, maxResponseBytes, maxBatchSize, fetchImpl = fetch}) {
  const target = new URL(endpoint);
  if (!['http:', 'https:'].includes(target.protocol) || target.username || target.password
      || target.search || target.hash || !target.pathname.endsWith('/model/v1/embed')) {
    throw new Error('Invalid embedding endpoint');
  }
  const model = validateEmbeddingModel(rawModel);
  positiveInteger(timeoutMs, 'embedding timeout', 60000);
  positiveInteger(maxRequestBytes, 'embedding request byte limit', 64 * 1024);
  positiveInteger(maxResponseBytes, 'embedding response byte limit', 8 * 1024 * 1024);
  positiveInteger(maxBatchSize, 'embedding batch size', 16);
  if (typeof fetchImpl !== 'function') throw new Error('Invalid embedding transport');

  return async function embedBatch(texts, {signal} = {}) {
    if (!Array.isArray(texts) || texts.length < 1 || texts.length > maxBatchSize
        || texts.some(text => typeof text !== 'string' || !text.trim())) {
      throw new Error('Invalid embedding batch');
    }
    const body = JSON.stringify({
      text: texts,
      input_type: 'text',
    });
    if (Buffer.byteLength(body) > maxRequestBytes) {
      throw new Error('Embedding request exceeded byte limit');
    }
    const deadline = AbortSignal.timeout(timeoutMs);
    const boundedSignal = signal ? AbortSignal.any([signal, deadline]) : deadline;
    const response = await fetchImpl(target, {
      method: 'POST',
      redirect: 'error',
      signal: boundedSignal,
      headers: {'Content-Type': 'application/json'},
      body,
    });
    if (response.status === 422) {
      const errorBody = await readBoundedJson(response, Math.min(maxResponseBytes, 64 * 1024));
      if (errorBody?.detail?.code === 'embedding_input_too_long') {
        throw new EmbeddingInputTooLongError();
      }
      throw new Error('Embedding request was rejected');
    }
    if (!response.ok) {
      await response.body?.cancel();
      const error = new Error('Embedding request failed');
      error.status = response.status;
      throw error;
    }
    const result = await readBoundedJson(response, maxResponseBytes);
    if (result?.model_name !== 'text_embedding' || result.input_type !== 'text') {
      throw new Error('Unexpected embedding response envelope');
    }
    validateVectors(result.embeddings, texts.length, model.dimension);
    return {
      vectors: result.embeddings,
      // The existing API supplies no verified model revision or token coverage.
      // Do not manufacture either from local configuration. The feature builder
      // must not certify these vectors as complete, revision-bound features.
      tokenCounts: null,
      model: null,
      coverageVerified: false,
    };
  };
}

export const GROUPING_EMBEDDING_MODEL = MODEL;
