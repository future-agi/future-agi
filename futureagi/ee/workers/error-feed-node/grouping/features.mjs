import {createHash} from 'node:crypto';
import {validateEmbeddingModel} from './embedding-client.mjs';

// Representation v2: complete contiguous small UTF-8 chunks, pooled by source
// length. This changes representation, not F6's LSH/admission rules. The shared
// serving API exposes neither weights revision nor tokenizer coverage: record
// those as unknown, never reinterpret a deployment cache namespace as proof.
export const FEATURE_VIEWS = Object.freeze(['task', 'semantics']);
export const FEATURE_VERSION = 'f6-minilm-features/v2';
export const FEATURE_LIMITS = Object.freeze({
  rows: 100, textBytesPerView: 256 * 1024, totalTextBytes: 2 * 1024 * 1024,
  chunks: 512, chunkBytes: 128,
});

export function featureDigest(value) {
  const canonical = JSON.stringify(value, (_key, item) => item && typeof item === 'object'
    && !Array.isArray(item) ? Object.fromEntries(Object.keys(item).sort().map(key => [key, item[key]])) : item);
  return createHash('sha256').update(canonical).digest('hex');
}

function validateVector(vector, dimension) {
  if (!Array.isArray(vector) || vector.length !== dimension
      || vector.some(value => !Number.isFinite(value)) || Math.hypot(...vector) === 0) {
    throw new Error('Invalid embedding vector');
  }
}

// Iteration by Unicode code point avoids splitting surrogate pairs. Every
// original character belongs to exactly one chunk; no trim/summary/truncation.
export function splitFeatureText(text) {
  if (typeof text !== 'string' || !text.trim() || !text.isWellFormed()) throw new Error('Invalid feature text');
  const chunks = [];
  let start = 0, end = 0, bytes = 0;
  for (const character of text) {
    const size = Buffer.byteLength(character);
    if (bytes + size > FEATURE_LIMITS.chunkBytes) {
      chunks.push({start, end, text: text.slice(start, end)});
      start = end;
      bytes = 0;
    }
    end += character.length;
    bytes += size;
  }
  chunks.push({start, end, text: text.slice(start, end)});
  return chunks;
}

export async function buildFeatures(rows, {embedBatch, cache = new Map(), model: rawModel,
  batchSize = 16, signal} = {}) {
  if (!Array.isArray(rows) || rows.length > FEATURE_LIMITS.rows
      || typeof embedBatch !== 'function' || !Number.isSafeInteger(batchSize)
      || batchSize < 1 || batchSize > 16 || typeof cache.get !== 'function' || typeof cache.set !== 'function') {
    throw new Error('Invalid feature configuration');
  }
  const model = validateEmbeddingModel(rawModel);
  const modelConfigDigest = featureDigest({model, version: FEATURE_VERSION, chunk_bytes: FEATURE_LIMITS.chunkBytes});
  const output = {}, pending = [], ids = new Set();
  let totalBytes = 0, totalChunks = 0;
  for (const row of rows) {
    if (!row || !['id', 'source_digest', 'evidence_revision'].every(key => typeof row[key] === 'string' && row[key])
        || ids.has(row.id) || !row.views || typeof row.views.semantics !== 'string' || !row.views.semantics.trim()) {
      throw new Error('Invalid or duplicate feature row');
    }
    ids.add(row.id);
    const views = {};
    output[row.id] = {source_digest: row.source_digest, evidence_revision: row.evidence_revision, views};
    for (const view of FEATURE_VIEWS) {
      const text = row.views[view];
      if (text === undefined || text === null || text === '') continue;
      if (typeof text !== 'string') throw new Error('Invalid view text');
      const bytes = Buffer.byteLength(text);
      totalBytes += bytes;
      if (bytes > FEATURE_LIMITS.textBytesPerView || totalBytes > FEATURE_LIMITS.totalTextBytes) {
        throw new Error('Feature text byte limit exceeded');
      }
      const chunks = splitFeatureText(text);
      totalChunks += chunks.length;
      if (totalChunks > FEATURE_LIMITS.chunks) throw new Error('Feature chunk limit exceeded');
      const binding = {source_digest: row.source_digest, evidence_revision: row.evidence_revision,
        view, text_digest: featureDigest(text), model_config_digest: modelConfigDigest};
      const key = featureDigest(binding);
      const cached = await cache.get(key);
      if (cached !== undefined) {
        if (cached.binding_digest !== key || cached.value_digest !== featureDigest(cached.feature)) {
          throw new Error('Invalid feature cache binding');
        }
        validateVector(cached.feature.vector, model.dimension);
        views[view] = structuredClone(cached.feature);
      } else pending.push({binding, key, chunks, views, view, length: text.length});
    }
  }
  const work = pending.flatMap(item => item.chunks);
  // Whitespace-only fragments have no tokens and receive zero semantic weight;
  // keep their positions in coverage rather than sending an empty request.
  const toEmbed = work.filter(chunk => chunk.text.trim());
  for (let offset = 0; offset < toEmbed.length; offset += batchSize) {
    signal?.throwIfAborted();
    const batch = toEmbed.slice(offset, offset + batchSize);
    const result = await embedBatch(batch.map(chunk => chunk.text), {signal});
    if (!Array.isArray(result?.vectors) || result.vectors.length !== batch.length) {
      throw new Error('Embedding result count mismatch');
    }
    batch.forEach((chunk, index) => {
      validateVector(result.vectors[index], model.dimension);
      chunk.vector = result.vectors[index];
    });
  }
  for (const item of pending) {
    const encoded = item.chunks.filter(chunk => chunk.vector);
    const weight = encoded.reduce((sum, chunk) => sum + chunk.end - chunk.start, 0);
    const vector = Array.from({length: model.dimension}, (_, index) => encoded.reduce(
      (sum, chunk) => sum + chunk.vector[index] * (chunk.end - chunk.start), 0) / weight);
    validateVector(vector, model.dimension);
    const feature = {...item.binding, vector, chunks: item.chunks.map(chunk => ({
      start: chunk.start, end: chunk.end, text_digest: featureDigest(chunk.text),
      token_count: null, embedded: Boolean(chunk.vector),
    })), coverage: {unit: 'utf16_code_units', start: 0, end: item.length,
      input_complete: true, model_token_coverage: 'unknown', truncated: null,
      proof: 'contiguous-utf8-chunks/v1'}};
    await cache.set(item.key, {binding_digest: item.key, value_digest: featureDigest(feature), feature: structuredClone(feature)});
    item.views[item.view] = feature;
  }
  return {version: FEATURE_VERSION, model: model.name, model_revision: null,
    serving_release: model.servingRelease, model_config_digest: modelConfigDigest,
    dimension: model.dimension, rows: output};
}
