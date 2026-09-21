import {createHash} from 'node:crypto';
import {validateEmbeddingModel} from './embedding-client.mjs';

// F6 calls the finding statement view "semantics". Only the approved
// statement/task views are active in this production slice.
export const FEATURE_VIEWS = Object.freeze(['task', 'semantics']);
export const FEATURE_VERSION = 'f6-minilm-features/v1';
export const FEATURE_LIMITS = Object.freeze({
  rows: 100,
  textBytesPerView: 256 * 1024,
  totalTextBytes: 2 * 1024 * 1024,
  chunks: 512,
});

function canonical(value) {
  return JSON.stringify(value, (_key, item) => item && typeof item === 'object'
    && !Array.isArray(item)
    ? Object.fromEntries(Object.keys(item).sort().map(key => [key, item[key]]))
    : item);
}

export function featureDigest(value) {
  return createHash('sha256').update(canonical(value)).digest('hex');
}

function validateVector(vector, dimension) {
  if (!Array.isArray(vector) || vector.length !== dimension
      || vector.some(value => !Number.isFinite(value))
      || Math.hypot(...vector) === 0) {
    throw new Error('Invalid embedding vector');
  }
}

function splitsSurrogatePair(text, offset) {
  if (offset <= 0 || offset >= text.length) return false;
  const before = text.charCodeAt(offset - 1);
  const after = text.charCodeAt(offset);
  return before >= 0xd800 && before <= 0xdbff && after >= 0xdc00 && after <= 0xdfff;
}

function identityChunks(text) {
  return {proof: 'strict-serving', chunks: [{text, start: 0, end: text.length}]};
}

function chunkingPolicy(chunker) {
  if (!chunker) return Object.freeze({capability: 'strict-full-source/v1'});
  const policy = chunker.policy;
  if (typeof chunker !== 'function' || !policy || typeof policy !== 'object'
      || Array.isArray(policy)
      || JSON.stringify(Object.keys(policy).sort()) !== JSON.stringify([
        'capability', 'revision', 'tokenizer_revision',
      ])
      || policy.capability !== 'token-aware-chunker/v1'
      || !/^sha256:[a-f0-9]{64}$/.test(policy.revision ?? '')) {
    throw new Error('Invalid token-aware chunker policy');
  }
  return Object.freeze({...policy});
}

async function prepareChunks(text, model, chunker, policy) {
  if (!chunker) return identityChunks(text);
  if (policy.tokenizer_revision !== model.revision) {
    throw new Error('Token-aware chunker policy mismatch');
  }
  const prepared = await chunker(text, {model});
  const binding = prepared?.binding;
  if (binding?.capability !== model.capability || binding.model !== model.name
      || binding.model_revision !== model.revision
      || binding.max_seq_length !== model.maxSequenceLength
      || binding.chunker_revision !== policy.revision) {
    throw new Error('Token-aware chunker capability mismatch');
  }
  const chunks = prepared.chunks;
  if (!Array.isArray(chunks) || !chunks.length) throw new Error('Token-aware chunker returned no chunks');
  let covered = 0;
  for (const chunk of chunks) {
    if (!Number.isSafeInteger(chunk.start) || !Number.isSafeInteger(chunk.end)
        || chunk.start !== covered || chunk.end <= chunk.start || chunk.end > text.length
        || splitsSurrogatePair(text, chunk.start) || splitsSurrogatePair(text, chunk.end)
        || text.slice(chunk.start, chunk.end) !== chunk.text
        || !Number.isSafeInteger(chunk.token_count) || chunk.token_count < 1
        || chunk.token_count > model.maxSequenceLength) {
      throw new Error('Token-aware chunker did not prove complete source coverage');
    }
    covered = chunk.end;
  }
  if (covered !== text.length) throw new Error('Token-aware chunker did not cover the full source');
  return {proof: 'token-aware-chunker+strict-serving', chunks: chunks.map(chunk => ({...chunk}))};
}

async function cacheGet(cache, key) {
  if (!cache || typeof cache.get !== 'function') throw new Error('Invalid feature cache');
  return cache.get(key);
}

async function cacheSet(cache, key, value) {
  if (typeof cache.set !== 'function') throw new Error('Feature cache is read-only');
  await cache.set(key, structuredClone(value));
}

function validateCached(entry, binding) {
  if (entry?.binding_digest !== featureDigest(binding)
      || entry.feature?.text_digest !== binding.text_digest
      || entry.feature?.source_digest !== binding.source_digest
      || entry.feature?.evidence_revision !== binding.evidence_revision
      || entry.feature?.model_config_digest !== binding.model_config_digest) {
    throw new Error('Stale feature cache entry');
  }
  const coverage = entry.feature.coverage;
  const chunks = entry.feature.chunks;
  if (coverage?.unit !== 'utf16_code_units' || coverage.start !== 0
      || coverage.end !== binding.text_length || coverage.complete !== true
      || !['strict-serving', 'token-aware-chunker+strict-serving'].includes(coverage.proof)
      || !Array.isArray(chunks) || !chunks.length || chunks.length > FEATURE_LIMITS.chunks) {
    throw new Error('Invalid cached feature coverage');
  }
  if (coverage.proof === 'strict-serving' && chunks.length !== 1) {
    throw new Error('Invalid cached feature coverage');
  }
  let covered = 0;
  for (const chunk of chunks) {
    if (!Number.isSafeInteger(chunk.start) || !Number.isSafeInteger(chunk.end)
        || chunk.start !== covered || chunk.end <= chunk.start || chunk.end > binding.text_length
        || !/^[a-f0-9]{64}$/.test(chunk.text_digest ?? '')
        || !Number.isSafeInteger(chunk.token_count) || chunk.token_count < 1
        || chunk.token_count > binding.max_sequence_length) {
      throw new Error('Invalid cached feature coverage');
    }
    covered = chunk.end;
  }
  if (covered !== binding.text_length) throw new Error('Invalid cached feature coverage');
  validateVector(entry.feature.vector, binding.dimension);
  return structuredClone(entry.feature);
}

function pool(chunks, dimension, textLength) {
  const vector = Array.from({length: dimension}, (_, index) => chunks.reduce(
    (total, chunk) => total + chunk.vector[index] * (chunk.end - chunk.start), 0,
  ) / textLength);
  validateVector(vector, dimension);
  return vector;
}

function validateRow(row, seen) {
  for (const key of ['id', 'source_digest', 'evidence_revision']) {
    if (typeof row?.[key] !== 'string' || !row[key]) throw new Error(`Invalid feature row ${key}`);
  }
  if (seen.has(row.id)) throw new Error('Duplicate feature row identity');
  seen.add(row.id);
  if (!row.views || typeof row.views !== 'object' || Array.isArray(row.views)) {
    throw new Error('Invalid feature row views');
  }
  if (typeof row.views.semantics !== 'string' || !row.views.semantics.trim()) {
    throw new Error('Finding statement view is required');
  }
}

export async function buildFeatures(rows, {embedBatch, cache = new Map(), model: rawModel,
  batchSize = 16, chunker} = {}) {
  if (!Array.isArray(rows) || typeof embedBatch !== 'function'
      || rows.length > FEATURE_LIMITS.rows
      || !Number.isSafeInteger(batchSize) || batchSize < 1 || batchSize > 16) {
    throw new Error('Invalid feature build configuration');
  }
  const model = validateEmbeddingModel(rawModel);
  const selectedChunkingPolicy = chunkingPolicy(chunker);
  const modelConfigDigest = featureDigest({model, chunking_policy: selectedChunkingPolicy});
  const outputRows = {};
  const missing = [];
  const seen = new Set();
  let totalTextBytes = 0;
  let totalChunks = 0;

  for (const row of rows) {
    validateRow(row, seen);
    const views = {};
    outputRows[row.id] = {
      source_digest: row.source_digest,
      evidence_revision: row.evidence_revision,
      views,
    };
    for (const view of FEATURE_VIEWS) {
      const text = row.views[view];
      if (text === undefined || text === null || text === '') continue;
      if (typeof text !== 'string' || !text.trim()) throw new Error(`Invalid ${view} feature text`);
      const textBytes = Buffer.byteLength(text);
      totalTextBytes += textBytes;
      if (textBytes > FEATURE_LIMITS.textBytesPerView
          || totalTextBytes > FEATURE_LIMITS.totalTextBytes) {
        throw new Error('Feature source text exceeded byte limit');
      }
      const binding = {
        source_digest: row.source_digest,
        evidence_revision: row.evidence_revision,
        view,
        text_digest: featureDigest(text),
        text_length: text.length,
        model_config_digest: modelConfigDigest,
        dimension: model.dimension,
        max_sequence_length: model.maxSequenceLength,
      };
      const key = featureDigest(binding);
      const cached = await cacheGet(cache, key);
      if (cached !== undefined) {
        const feature = validateCached(cached, binding);
        totalChunks += feature.chunks.length;
        if (totalChunks > FEATURE_LIMITS.chunks) throw new Error('Feature chunk limit exceeded');
        views[view] = feature;
        continue;
      }
      const prepared = await prepareChunks(text, model, chunker, selectedChunkingPolicy);
      totalChunks += prepared.chunks.length;
      if (totalChunks > FEATURE_LIMITS.chunks) throw new Error('Feature chunk limit exceeded');
      missing.push({row, view, text, views, binding, key, prepared});
    }
  }

  const chunkWork = missing.flatMap(item => item.prepared.chunks.map((chunk, index) => ({item, chunk, index})));
  for (let offset = 0; offset < chunkWork.length; offset += batchSize) {
    const batch = chunkWork.slice(offset, offset + batchSize);
    const response = await embedBatch(batch.map(work => work.chunk.text));
    if (response?.coverageVerified === false) {
      throw new Error('Serving API does not verify model identity or input coverage; feature preparation integration is pending');
    }
    if (response?.model?.capability !== model.capability || response.model.name !== model.name
        || response.model.revision !== model.revision || response.model.dimension !== model.dimension
        || response.model.maxSequenceLength !== model.maxSequenceLength
        || !Array.isArray(response.vectors) || !Array.isArray(response.tokenCounts)
        || response.vectors.length !== batch.length || response.tokenCounts.length !== batch.length) {
      throw new Error('Embedding client returned an unbound result');
    }
    batch.forEach((work, index) => {
      validateVector(response.vectors[index], model.dimension);
      const tokenCount = response.tokenCounts[index];
      if (!Number.isSafeInteger(tokenCount) || tokenCount < 1 || tokenCount > model.maxSequenceLength
          || (work.chunk.token_count !== undefined && work.chunk.token_count !== tokenCount)) {
        throw new Error('Embedding token coverage mismatch');
      }
      work.chunk.vector = response.vectors[index];
      work.chunk.token_count = tokenCount;
    });
  }

  for (const item of missing) {
    const chunks = item.prepared.chunks;
    const feature = {
      source_digest: item.row.source_digest,
      evidence_revision: item.row.evidence_revision,
      text_digest: item.binding.text_digest,
      model_config_digest: modelConfigDigest,
      vector: pool(chunks, model.dimension, item.text.length),
      chunks: chunks.map(chunk => ({
        start: chunk.start,
        end: chunk.end,
        text_digest: featureDigest(chunk.text),
        token_count: chunk.token_count,
      })),
      coverage: {
        unit: 'utf16_code_units',
        start: 0,
        end: item.text.length,
        complete: true,
        proof: item.prepared.proof,
      },
    };
    item.views[item.view] = feature;
    await cacheSet(cache, item.key, {binding_digest: featureDigest(item.binding), feature});
  }

  return {
    version: FEATURE_VERSION,
    model: model.name,
    model_revision: model.revision,
    model_config_digest: modelConfigDigest,
    chunking_policy: selectedChunkingPolicy,
    dimension: model.dimension,
    rows: outputRows,
  };
}
